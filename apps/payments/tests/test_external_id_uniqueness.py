# ────────────────────────────────────────────────────────────────
# apps/payments/tests/test_external_id_uniqueness.py
#
# F-15 / PROD-014 — регрессионные тесты инварианта уникальности
# Payment.external_id.
#
# ПРОВЕРЯЕТ:
#   • БД-границу: частичный уникальный индекс payment_external_id_unique
#     (condition external_id <> '') отклоняет дубликаты даже на путях,
#     обходящих валидацию приложения (queryset.update());
#   • Глобальное пространство уникальности: дубль external_id
#     отвергается и в рамках одного провайдера, и МЕЖДУ разными
#     провайдерами (контракт вебхук-корреляции ADR-004: поиск
#     только по external_id, provider в выборке не участвует);
#   • Blank-семантику: платежи без назначенного провайдером ID
#     (external_id='') легитимно сосуществуют;
#   • Сохранность модели платёжных попыток: несколько платежей
#     одного заказа допускаются (уникальные/пустые external_id);
#   • Совместимость вебхуков: корреляция по external_id находит
#     ровно один платёж, повторная доставка идемпотентна.
# ────────────────────────────────────────────────────────────────

from django.db import IntegrityError, transaction
from django.db.models import UniqueConstraint
from django.test import TestCase

from apps.orders.tests.factories import create_test_order, create_test_user
from apps.payments.constants import (
    PAYMENT_STATUS_PROCESSING,
    PAYMENT_STATUS_SUCCEEDED,
)
from apps.payments.models import Payment
from apps.payments.services.payment_service import PaymentService
from apps.payments.tests.factories import create_test_payment

UNIQUE_CONSTRAINT_NAME = 'payment_external_id_unique'


class ExternalIdConstraintConfigTests(TestCase):
    """Инвариант объявлен на уровне модели (и миграции), а не в коде."""

    def test_unique_constraint_registered(self):
        """Meta.constraints содержит payment_external_id_unique."""
        names = [c.name for c in Payment._meta.constraints]
        self.assertIn(UNIQUE_CONSTRAINT_NAME, names)

    def test_constraint_is_partial_unique_on_external_id(self):
        """Constraint — UniqueConstraint по external_id с условием.

        Условие исключает blank (''): платежи до назначения ID
        провайдером не конкурируют между собой (AC-7).
        """
        constraint = next(
            c for c in Payment._meta.constraints
            if c.name == UNIQUE_CONSTRAINT_NAME
        )
        self.assertIsInstance(constraint, UniqueConstraint)
        self.assertEqual(list(constraint.fields), ['external_id'])
        self.assertTrue(constraint.condition)
        self.assertFalse(constraint.deferrable)


class ExternalIdUniquenessTests(TestCase):
    """Дубликаты external_id отклоняются на границе БД."""

    def setUp(self):
        self.user = create_test_user()
        self.order = create_test_order(self.user)
        self.payment = create_test_payment(
            self.order, self.user, external_id='provider-pay-001',
        )

    def test_duplicate_external_id_rejected(self):
        """Повторный payment с тем же external_id → IntegrityError."""
        other_order = create_test_order(self.user)
        with self.assertRaises(IntegrityError):
            create_test_payment(
                other_order, self.user, external_id='provider-pay-001',
            )

    def test_duplicate_external_id_rejected_across_providers(self):
        """Один external_id у двух провайдеров → IntegrityError.

        Пространство уникальности — ГЛОБАЛЬНОЕ (AC-1): вебхук-корреляция
        ищет платёж только по external_id без provider, поэтому один и
        тот же идентификатор у 'mock' и 'yookassa' сделал бы выборку
        неоднозначной. (AC-6: провайдер-скоупированная уникальность —
        НЕ существующий контракт, поэтому и не вводится.)
        """
        other_order = create_test_order(self.user)
        with self.assertRaises(IntegrityError):
            create_test_payment(
                other_order, self.user,
                external_id='provider-pay-001',
                provider='yookassa',
            )

    def test_duplicate_rejected_at_db_boundary_via_update(self):
        """Дубль отвергается БД даже при update() мимо валидации приложения.

        queryset.update() не вызывает save()/валидаторы — единственная
        защита здесь сам частичный UNIQUE-индекс (AC-2).
        """
        other_order = create_test_order(self.user)
        other_payment = create_test_payment(
            other_order, self.user, external_id='provider-pay-002',
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Payment.objects.filter(pk=other_payment.pk).update(
                    external_id='provider-pay-001',
                )
        # Значение не изменилось — транзакция откатилась целиком.
        other_payment.refresh_from_db()
        self.assertEqual(other_payment.external_id, 'provider-pay-002')

    def test_distinct_external_ids_allowed(self):
        """Разные external_id — обе записи сохраняются."""
        other_order = create_test_order(self.user)
        second = create_test_payment(
            other_order, self.user, external_id='provider-pay-002',
        )
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.external_id, 'provider-pay-001')
        self.assertEqual(second.external_id, 'provider-pay-002')
        self.assertEqual(
            Payment.objects.filter(external_id__in=[
                'provider-pay-001', 'provider-pay-002',
            ]).count(),
            2,
        )


class ExternalIdBlankSemanticsTests(TestCase):
    """Blank external_id ('') намеренно не уникален (AC-7)."""

    def setUp(self):
        self.user = create_test_user()
        self.order = create_test_order(self.user)

    def test_multiple_blank_external_ids_allowed(self):
        """Несколько платежей с external_id='' сосуществуют."""
        for _ in range(3):
            create_test_payment(self.order, self.user, external_id='')
        self.assertEqual(
            Payment.objects.filter(external_id='').count(),
            3,
        )

    def test_blank_and_non_blank_coexist(self):
        """Пустые и заполненные external_id не конфликтуют."""
        create_test_payment(self.order, self.user, external_id='')
        create_test_payment(self.order, self.user, external_id='ext-a')
        create_test_payment(self.order, self.user, external_id='')
        create_test_payment(self.order, self.user, external_id='ext-b')
        self.assertEqual(
            Payment.objects.filter(external_id='').count(),
            2,
        )
        self.assertEqual(
            Payment.objects.exclude(external_id='').count(),
            2,
        )

    def test_payment_attempts_model_preserved(self):
        """Несколько попыток оплаты заказа сохранены (AC-4).

        Одному заказу можно иметь сколько угодно платежей: проваленная
        попытка + повтор (разные/пустые external_id).
        """
        failed_attempt = create_test_payment(
            self.order, self.user,
            external_id='ext-attempt-1', status='failed',
        )
        retry_blank = create_test_payment(
            self.order, self.user, external_id='',
        )
        retry_assigned = create_test_payment(
            self.order, self.user, external_id='ext-attempt-2',
        )
        self.assertEqual(self.order.payments.count(), 3)
        self.assertIn(failed_attempt, self.order.payments.all())
        self.assertIn(retry_blank, self.order.payments.all())
        self.assertIn(retry_assigned, self.order.payments.all())


class ExternalIdWebhookCorrelationTests(TestCase):
    """Вебхук-корреляция ADR-004 остаётся однозначной и идемпотентной."""

    def setUp(self):
        self.user = create_test_user()
        self.order = create_test_order(self.user)
        self.payment = create_test_payment(
            self.order, self.user,
            external_id='webhook-target-id',
            status=PAYMENT_STATUS_PROCESSING,
        )
        # Шум вокруг: blank-платежи и чужие ID не мешают корреляции.
        create_test_payment(self.order, self.user, external_id='')
        create_test_payment(self.order, self.user, external_id='')

    def test_with_external_id_matches_exactly_one_row(self):
        """with_external_id() возвращает ровно одну строку (AC-5)."""
        self.assertEqual(
            Payment.objects.with_external_id('webhook-target-id').count(),
            1,
        )
        self.assertEqual(
            Payment.objects.with_external_id('webhook-target-id').get().pk,
            self.payment.pk,
        )

    def test_webhook_confirms_target_payment(self):
        """Webhook находит целевой платёж среди blank-шума."""
        result = PaymentService.handle_webhook(
            external_id='webhook-target-id',
            event_type='payment.succeeded',
            status=PAYMENT_STATUS_SUCCEEDED,
            payload={'source': 'provider'},
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.pk, self.payment.pk)
        self.assertEqual(result.status, PAYMENT_STATUS_SUCCEEDED)

    def test_webhook_redelivery_is_idempotent(self):
        """Повторная доставка вебхука не падает и не меняет статус.

        Инвариант уникальности гарантирует единственного кандидата —
        идемпотентность ADR-004 становится детерминированной.
        """
        for _ in range(2):
            result = PaymentService.handle_webhook(
                external_id='webhook-target-id',
                event_type='payment.succeeded',
                status=PAYMENT_STATUS_SUCCEEDED,
            )
            self.assertIsNotNone(result)
            self.assertEqual(result.status, PAYMENT_STATUS_SUCCEEDED)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, PAYMENT_STATUS_SUCCEEDED)
        # Два вебхука → два аудит-события, один платёж.
        self.assertEqual(
            self.payment.events.filter(event_type='webhook_received').count(),
            2,
        )

    def test_webhook_unknown_external_id(self):
        """Неизвестный external_id → None (поведение не изменено)."""
        result = PaymentService.handle_webhook(
            external_id='totally-unknown-id',
            event_type='payment.succeeded',
            status=PAYMENT_STATUS_SUCCEEDED,
        )
        self.assertIsNone(result)
