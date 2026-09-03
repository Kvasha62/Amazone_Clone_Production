# ────────────────────────────────────────────────────────────────────────
# apps/payments/tests/test_services.py — тесты PaymentService.
#
# ПРОВЕРЯЕТ:
#   • create_payment() — создание платежа
#   • process_payment() — переход в PROCESSING
#   • confirm_payment() — подтверждение (SUCCEEDED) + подтверждение заказа
#   • fail_payment() — ошибка оплаты
#   • cancel_payment() — отмена
#   • refund_payment() — возврат (полный и частичный)
#   • handle_webhook() — обработка вебхука
#   • Валидации: ownership, статус заказа, лимиты, повторная оплата
# ────────────────────────────────────────────────────────────────────────

from decimal import Decimal
from unittest import mock

from django.db import OperationalError
from django.test import TestCase

from rest_framework.exceptions import NotFound, ValidationError

from apps.orders.models.order import OrderStatus
from apps.orders.tests.factories import (
    create_test_order,
    create_test_order_item,
    create_test_user,
)
from apps.payments.constants import (
    MAX_PAYMENT_AMOUNT,
    MIN_PAYMENT_AMOUNT,
    PAYMENT_STATUS_CANCELLED,
    PAYMENT_STATUS_FAILED,
    PAYMENT_STATUS_PENDING,
    PAYMENT_STATUS_PROCESSING,
    PAYMENT_STATUS_REFUNDED,
    PAYMENT_STATUS_SUCCEEDED,
)
from apps.payments.models import Payment, PaymentEvent
from apps.payments.services.payment_service import PaymentService
from apps.payments.tests.factories import create_test_payment


class CreatePaymentServiceTests(TestCase):
    """Тесты PaymentService.create_payment()."""

    def setUp(self):
        self.user = create_test_user()
        self.order = create_test_order(self.user)

    def test_create_payment_success(self):
        """Успешное создание платежа."""
        payment = PaymentService.create_payment(
            order=self.order,
            user=self.user,
            amount=Decimal('1000.00'),
            method='card',
        )
        self.assertEqual(payment.status, PAYMENT_STATUS_PENDING)
        self.assertEqual(payment.amount, Decimal('1000.00'))
        self.assertEqual(payment.order_id, self.order.pk)
        self.assertEqual(payment.user_id, self.user.pk)
        # Должен быть создан PaymentEvent(CREATED)
        self.assertTrue(
            payment.events.filter(event_type='created').exists()
        )

    def test_create_payment_generates_external_id(self):
        """При создании генерируется mock external_id."""
        payment = PaymentService.create_payment(
            order=self.order,
            user=self.user,
            amount=self.order.total,  # 🔴 Сумма должна совпадать с order.total
        )
        self.assertTrue(payment.external_id.startswith('mock_'))

    def test_create_payment_wrong_user(self):
        """Нельзя создать платёж для чужого заказа."""
        other_user = create_test_user()
        with self.assertRaises(NotFound):
            PaymentService.create_payment(
                order=self.order,
                user=other_user,
                amount=Decimal('1000.00'),
            )

    def test_create_payment_order_not_pending(self):
        """Нельзя оплатить заказ не в статусе PENDING."""
        self.order.status = OrderStatus.CONFIRMED
        self.order.save()
        with self.assertRaises(ValidationError):
            PaymentService.create_payment(
                order=self.order,
                user=self.user,
                amount=Decimal('1000.00'),
            )

    def test_create_payment_order_cancelled(self):
        """Нельзя оплатить отменённый заказ."""
        self.order.status = OrderStatus.CANCELLED
        self.order.save()
        with self.assertRaises(ValidationError):
            PaymentService.create_payment(
                order=self.order,
                user=self.user,
                amount=Decimal('1000.00'),
            )

    def test_create_payment_amount_too_small(self):
        """Сумма < MIN_PAYMENT_AMOUNT → ValidationError."""
        with self.assertRaises(ValidationError):
            PaymentService.create_payment(
                order=self.order,
                user=self.user,
                amount=Decimal('0.50'),
            )

    def test_create_payment_amount_too_large(self):
        """Сумма > MAX_PAYMENT_AMOUNT → ValidationError."""
        with self.assertRaises(ValidationError):
            PaymentService.create_payment(
                order=self.order,
                user=self.user,
                amount=MAX_PAYMENT_AMOUNT + Decimal('1.00'),
            )

    def test_create_payment_already_paid(self):
        """Нельзя создать второй платёж, если заказ уже оплачен."""
        payment = create_test_payment(
            self.order, self.user, status=PAYMENT_STATUS_SUCCEEDED,
        )
        with self.assertRaises(ValidationError) as ctx:
            PaymentService.create_payment(
                order=self.order,
                user=self.user,
                amount=Decimal('1000.00'),
            )
        self.assertIn('уже оплачен', str(ctx.exception.detail))

    def test_create_payment_after_failed_is_allowed(self):
        """После FAILED платежа можно создать новый."""
        create_test_payment(
            self.order, self.user, status=PAYMENT_STATUS_FAILED,
        )
        payment = PaymentService.create_payment(
            order=self.order,
            user=self.user,
            amount=Decimal('1000.00'),
        )
        self.assertEqual(payment.status, PAYMENT_STATUS_PENDING)


class ProcessPaymentServiceTests(TestCase):
    """Тесты PaymentService.process_payment()."""

    def setUp(self):
        self.user = create_test_user()
        self.order = create_test_order(self.user)
        self.payment = create_test_payment(
            self.order, self.user, status=PAYMENT_STATUS_PENDING,
        )

    def test_process_success(self):
        """PENDING → PROCESSING."""
        payment = PaymentService.process_payment(self.payment)
        self.assertEqual(payment.status, PAYMENT_STATUS_PROCESSING)

    def test_process_creates_event(self):
        """При обработке создаётся PaymentEvent(status_changed)."""
        PaymentService.process_payment(self.payment)
        self.assertTrue(
            self.payment.events.filter(
                event_type='status_changed',
                new_status=PAYMENT_STATUS_PROCESSING,
            ).exists()
        )

    def test_process_not_pending_fails(self):
        """Нельзя обработать не-PENDING платёж."""
        self.payment.status = PAYMENT_STATUS_SUCCEEDED
        self.payment.save()
        with self.assertRaises(ValidationError):
            PaymentService.process_payment(self.payment)


class ConfirmPaymentServiceTests(TestCase):
    """Тесты PaymentService.confirm_payment()."""

    def setUp(self):
        self.user = create_test_user()
        self.order = create_test_order(self.user)
        self.payment = create_test_payment(
            self.order, self.user, status=PAYMENT_STATUS_PROCESSING,
        )

    def test_confirm_success(self):
        """PROCESSING → SUCCEEDED."""
        payment = PaymentService.confirm_payment(
            self.payment,
            external_id='ext_123',
        )
        self.assertEqual(payment.status, PAYMENT_STATUS_SUCCEEDED)
        self.assertIsNotNone(payment.paid_at)
        self.assertEqual(payment.external_id, 'ext_123')

    def test_confirm_updates_order_status(self):
        """Подтверждение платежа → Order.status = CONFIRMED."""
        PaymentService.confirm_payment(self.payment)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, OrderStatus.CONFIRMED)

    def test_confirm_creates_event(self):
        """При подтверждении создаётся PaymentEvent(confirmed)."""
        PaymentService.confirm_payment(self.payment)
        self.assertTrue(
            self.payment.events.filter(
                event_type='confirmed',
                new_status=PAYMENT_STATUS_SUCCEEDED,
            ).exists()
        )

    def test_confirm_idempotent(self):
        """Повторное подтверждение уже SUCCEEDED — без ошибки."""
        PaymentService.confirm_payment(self.payment)
        # Второй вызов — идемпотентный
        payment = PaymentService.confirm_payment(self.payment)
        self.assertEqual(payment.status, PAYMENT_STATUS_SUCCEEDED)

    def test_confirm_from_pending(self):
        """PENDING → SUCCEEDED (некоторые провайдеры мгновенно отвечают)."""
        payment = create_test_payment(
            self.order, self.user, status=PAYMENT_STATUS_PENDING,
        )
        result = PaymentService.confirm_payment(payment)
        self.assertEqual(result.status, PAYMENT_STATUS_SUCCEEDED)

    def test_confirm_failed_payment(self):
        """Нельзя подтвердить FAILED платёж."""
        self.payment.status = PAYMENT_STATUS_FAILED
        self.payment.save()
        with self.assertRaises(ValidationError):
            PaymentService.confirm_payment(self.payment)


class FailPaymentServiceTests(TestCase):
    """Тесты PaymentService.fail_payment()."""

    def setUp(self):
        self.user = create_test_user()
        self.order = create_test_order(self.user)
        self.payment = create_test_payment(
            self.order, self.user, status=PAYMENT_STATUS_PROCESSING,
        )

    def test_fail_success(self):
        """PROCESSING → FAILED."""
        payment = PaymentService.fail_payment(
            self.payment,
            note='Card declined',
        )
        self.assertEqual(payment.status, PAYMENT_STATUS_FAILED)
        self.assertEqual(payment.note, 'Card declined')

    def test_fail_creates_event(self):
        PaymentService.fail_payment(self.payment)
        self.assertTrue(
            self.payment.events.filter(
                event_type='error',
                new_status=PAYMENT_STATUS_FAILED,
            ).exists()
        )

    def test_fail_from_pending(self):
        """PENDING → FAILED (возможно)."""
        payment = create_test_payment(
            self.order, self.user, status=PAYMENT_STATUS_PENDING,
        )
        result = PaymentService.fail_payment(payment)
        self.assertEqual(result.status, PAYMENT_STATUS_FAILED)

    def test_fail_succeeded_fails(self):
        """Нельзя FAIL-нуть успешный платёж."""
        self.payment.status = PAYMENT_STATUS_SUCCEEDED
        self.payment.save()
        with self.assertRaises(ValidationError):
            PaymentService.fail_payment(self.payment)


class CancelPaymentServiceTests(TestCase):
    """Тесты PaymentService.cancel_payment()."""

    def setUp(self):
        self.user = create_test_user()
        self.order = create_test_order(self.user)
        self.payment = create_test_payment(
            self.order, self.user, status=PAYMENT_STATUS_PENDING,
        )

    def test_cancel_pending(self):
        """PENDING → CANCELLED."""
        payment = PaymentService.cancel_payment(
            self.payment, note='Передумал',
        )
        self.assertEqual(payment.status, PAYMENT_STATUS_CANCELLED)
        self.assertIsNotNone(payment.cancelled_at)

    def test_cancel_processing(self):
        """PROCESSING → CANCELLED."""
        self.payment.status = PAYMENT_STATUS_PROCESSING
        self.payment.save()
        payment = PaymentService.cancel_payment(self.payment)
        self.assertEqual(payment.status, PAYMENT_STATUS_CANCELLED)

    def test_cancel_succeeded_fails(self):
        """Нельзя отменить SUCCEEDED платёж (нужен refund)."""
        self.payment.status = PAYMENT_STATUS_SUCCEEDED
        self.payment.save()
        with self.assertRaises(ValidationError):
            PaymentService.cancel_payment(self.payment)

    def test_cancel_creates_event(self):
        PaymentService.cancel_payment(self.payment)
        self.assertTrue(
            self.payment.events.filter(
                event_type='cancelled',
                new_status=PAYMENT_STATUS_CANCELLED,
            ).exists()
        )


class RefundPaymentServiceTests(TestCase):
    """Тесты PaymentService.refund_payment()."""

    def setUp(self):
        self.user = create_test_user()
        self.order = create_test_order(self.user)
        self.payment = create_test_payment(
            self.order, self.user,
            status=PAYMENT_STATUS_SUCCEEDED,
            amount=Decimal('1000.00'),
        )

    def test_full_refund(self):
        """Полный возврат → REFUNDED."""
        payment = PaymentService.refund_payment(
            self.payment, reason='Брак',
        )
        self.assertEqual(payment.status, PAYMENT_STATUS_REFUNDED)
        self.assertEqual(payment.refund_amount, Decimal('1000.00'))
        self.assertIsNotNone(payment.refunded_at)

    def test_partial_refund(self):
        """Частичный возврат — остаётся SUCCEEDED."""
        payment = PaymentService.refund_payment(
            self.payment, amount=Decimal('300.00'),
        )
        self.assertEqual(payment.status, PAYMENT_STATUS_SUCCEEDED)
        self.assertEqual(payment.refund_amount, Decimal('300.00'))

    def test_partial_then_full_refund(self):
        """Частичный + остаток → REFUNDED."""
        PaymentService.refund_payment(
            self.payment, amount=Decimal('300.00'),
        )
        payment = PaymentService.refund_payment(
            self.payment, amount=Decimal('700.00'),
        )
        self.assertEqual(payment.status, PAYMENT_STATUS_REFUNDED)
        self.assertEqual(payment.refund_amount, Decimal('1000.00'))

    def test_refund_exceeds_amount(self):
        """Возврат > суммы платежа → ValidationError."""
        with self.assertRaises(ValidationError):
            PaymentService.refund_payment(
                self.payment, amount=Decimal('1500.00'),
            )

    def test_refund_zero_fails(self):
        """Возврат 0 → ValidationError."""
        with self.assertRaises(ValidationError):
            PaymentService.refund_payment(
                self.payment, amount=Decimal('0.00'),
            )

    def test_refund_not_succeeded_fails(self):
        """Возврат для не-SUCCEEDED платежа → ValidationError."""
        self.payment.status = PAYMENT_STATUS_PENDING
        self.payment.save()
        with self.assertRaises(ValidationError):
            PaymentService.refund_payment(self.payment)

    def test_refund_creates_event(self):
        PaymentService.refund_payment(self.payment, reason='Test')
        self.assertTrue(
            self.payment.events.filter(
                event_type='refund_completed',
            ).exists()
        )

    def test_partial_refund_creates_initiated_event(self):
        PaymentService.refund_payment(
            self.payment, amount=Decimal('100.00'),
        )
        self.assertTrue(
            self.payment.events.filter(
                event_type='refund_initiated',
            ).exists()
        )


class HandleWebhookServiceTests(TestCase):
    """Тесты PaymentService.handle_webhook()."""

    def setUp(self):
        self.user = create_test_user()
        self.order = create_test_order(self.user)
        self.payment = create_test_payment(
            self.order, self.user,
            status=PAYMENT_STATUS_PROCESSING,
        )

    def test_webhook_succeeded(self):
        """Webhook status=succeeded → подтверждение."""
        payment = PaymentService.handle_webhook(
            external_id=self.payment.external_id,
            event_type='payment.succeeded',
            status=PAYMENT_STATUS_SUCCEEDED,
            payload={'test': True},
        )
        self.assertIsNotNone(payment)
        self.assertEqual(payment.status, PAYMENT_STATUS_SUCCEEDED)

    def test_webhook_failed(self):
        """Webhook status=failed → FAILED."""
        payment = PaymentService.handle_webhook(
            external_id=self.payment.external_id,
            event_type='payment.failed',
            status=PAYMENT_STATUS_FAILED,
        )
        self.assertEqual(payment.status, PAYMENT_STATUS_FAILED)

    def test_webhook_unknown_payment(self):
        """Webhook с неизвестным external_id → None."""
        result = PaymentService.handle_webhook(
            external_id='unknown_id_12345',
            event_type='payment.succeeded',
            status=PAYMENT_STATUS_SUCCEEDED,
        )
        self.assertIsNone(result)

    def test_webhook_creates_audit_event(self):
        """Webhook создаёт PaymentEvent(webhook_received)."""
        PaymentService.handle_webhook(
            external_id=self.payment.external_id,
            event_type='payment.succeeded',
            status=PAYMENT_STATUS_SUCCEEDED,
        )
        self.assertTrue(
            self.payment.events.filter(
                event_type='webhook_received',
            ).exists()
        )

    def test_webhook_database_lookup_error_propagates(self):
        """F-17: DB failure must not be converted into 'payment not found'."""
        with mock.patch(
            'apps.payments.querysets.payment_queryset.'
            'PaymentQuerySet.with_external_id',
            side_effect=OperationalError('database is down'),
        ):
            with self.assertRaises(OperationalError):
                PaymentService.handle_webhook(
                    external_id=self.payment.external_id,
                    event_type='payment.succeeded',
                    status=PAYMENT_STATUS_SUCCEEDED,
                )

    def test_fresh_order_status_propagates_programming_error(self):
        """F-17: `_fresh_order_status` must not hide programming errors."""
        from apps.orders.models import Order

        with mock.patch('apps.orders.models.Order.objects') as mock_objects:
            mock_objects.only.return_value.get.side_effect = RuntimeError(
                'boom',
            )
            with self.assertRaises(RuntimeError):
                PaymentService._fresh_order_status(999)


class GetPaymentByNumberTests(TestCase):
    """Тесты PaymentService.get_payment_by_number()."""

    def test_existing_payment(self):
        user = create_test_user()
        order = create_test_order(user)
        payment = create_test_payment(order, user)
        result = PaymentService.get_payment_by_number(payment.order_number)
        self.assertEqual(result.pk, payment.pk)

    def test_non_existing_payment(self):
        with self.assertRaises(NotFound):
            PaymentService.get_payment_by_number('PAY-999999')
