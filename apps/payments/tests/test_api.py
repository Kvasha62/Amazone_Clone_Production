# ────────────────────────────────────────────────────────────────────────
# apps/payments/tests/test_api.py — тесты API endpoints платежей.
#
# ПРОВЕРЯЕТ:
#   • GET  /api/v1/payments/                      — список
#   • POST /api/v1/payments/                      — создание
#   • GET  /api/v1/payments/{payment_number}/     — детали
#   • POST /api/v1/payments/{payment_number}/refund/  — возврат (staff)
#   • POST /api/v1/payments/{payment_number}/cancel/  — отмена
#   • POST /api/v1/payments/webhook/              — вебхук
#   • Права доступа (IsAuthenticated, IsAdminUser, AllowAny)
#   • Ownership checks
# ────────────────────────────────────────────────────────────────────────

from decimal import Decimal

from django.test import TestCase, override_settings
from django.urls import reverse

from rest_framework import status
from rest_framework.test import APIClient

from apps.core.api_errors import CODE_NOT_FOUND, CODE_VALIDATION
from apps.orders.tests.factories import create_test_order, create_test_user
from apps.payments.models import Payment
from apps.payments.tests.factories import create_test_payment
from apps.payments.tests.webhook_helpers import (
    WEBHOOK_SECRET,
    post_signed_webhook,
)


class PaymentListAPITests(TestCase):
    """Тесты GET / POST /api/v1/payments/."""

    def setUp(self):
        self.user = create_test_user()
        self.order = create_test_order(self.user)
        self.client = APIClient()
        self.url = reverse('payments:payment-list')

    def test_list_requires_auth(self):
        """Список платежей требует аутентификацию."""
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_list_returns_user_payments(self):
        """Список содержит только платежи текущего пользователя."""
        self.client.force_authenticate(self.user)
        payment = create_test_payment(self.order, self.user)
        # Платёж другого пользователя
        other_user = create_test_user()
        other_order = create_test_order(other_user)
        create_test_payment(other_order, other_user)

        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['count'], 1)
        self.assertEqual(len(resp.data['results']), 1)
        self.assertEqual(resp.data['results'][0]['id'], payment.pk)

    @override_settings(DEFAULT_THROTTLE_CLASSES=[])
    def test_create_payment(self):
        """Создание платежа через API (свой заказ → 201 flow сохраняется)."""
        self.client.force_authenticate(self.user)
        data = {
            'order_id': self.order.pk,
            'amount': '1000.00',
            'method': 'card',
        }
        resp = self.client.post(self.url, data, format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertIn('order_number', resp.data)
        self.assertEqual(resp.data['status'], 'pending')
        # Платёж реально создан: заказ/пользователь/сумма/событие корректны.
        payment = Payment.objects.get(pk=resp.data['id'])
        self.assertEqual(payment.order_id, self.order.pk)
        self.assertEqual(payment.user_id, self.user.pk)
        self.assertEqual(payment.amount, Decimal('1000.00'))
        self.assertEqual(payment.status, 'pending')
        self.assertTrue(
            payment.events.filter(event_type='created').exists(),
        )

    @override_settings(DEFAULT_THROTTLE_CLASSES=[])
    def test_create_payment_by_order_number(self):
        """F-8 (#73): канонический order_number принимается."""
        self.client.force_authenticate(self.user)
        data = {
            'order_number': self.order.order_number,
            'amount': '1000.00',
        }
        resp = self.client.post(self.url, data, format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        payment = Payment.objects.get(pk=resp.data['id'])
        self.assertEqual(payment.order_id, self.order.pk)

    @override_settings(DEFAULT_THROTTLE_CLASSES=[])
    def test_create_payment_rejects_both_order_identifiers(self):
        """order_number + order_id одновременно → 400 (F-8, #73)."""
        self.client.force_authenticate(self.user)
        resp = self.client.post(
            self.url,
            {
                'order_number': self.order.order_number,
                'order_id': self.order.pk,
            },
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.data['error']['code'], CODE_VALIDATION)
        self.assertEqual(Payment.objects.count(), 0)

    @override_settings(DEFAULT_THROTTLE_CLASSES=[])
    def test_create_payment_malformed_order_number_returns_400(self):
        """Некорректный формат order_number → 400, а не 404 (F-8, #73)."""
        self.client.force_authenticate(self.user)
        resp = self.client.post(
            self.url, {'order_number': '12345'}, format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.data['error']['code'], CODE_VALIDATION)
        self.assertEqual(Payment.objects.count(), 0)

    @override_settings(DEFAULT_THROTTLE_CLASSES=[])
    def test_create_payment_by_order_number_other_user_returns_404(self):
        """Чужой заказ по order_number → 404 (owner scoping сохранён)."""
        self.client.force_authenticate(self.user)
        other_order = create_test_order(create_test_user())
        resp = self.client.post(
            self.url,
            {'order_number': other_order.order_number},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(resp.data['error']['code'], CODE_NOT_FOUND)
        self.assertEqual(Payment.objects.count(), 0)

    @override_settings(DEFAULT_THROTTLE_CLASSES=[])
    def test_create_payment_without_amount_uses_order_total(self):
        """Если amount не указан — берётся из order.total."""
        self.client.force_authenticate(self.user)
        data = {'order_id': self.order.pk}
        resp = self.client.post(self.url, data, format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(
            Decimal(resp.data['amount']),
            self.order.total,
        )

    @override_settings(DEFAULT_THROTTLE_CLASSES=[])
    def test_create_payment_other_users_order(self):
        """Чужой заказ → canonical API-04 404, existence не раскрывается.

        Issue #68: ownership enforced на view boundary — заказ другого
        пользователя резолвится как несуществующий (404 not_found).
        """
        self.client.force_authenticate(self.user)
        other_user = create_test_user()
        other_order = create_test_order(other_user)
        self.assertFalse(other_user.is_staff)

        data = {'order_id': other_order.pk, 'amount': '500.00'}
        resp = self.client.post(self.url, data, format='json')

        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)
        error = resp.data['error']
        self.assertEqual(set(error), {'code', 'message', 'details'})
        self.assertEqual(error['code'], CODE_NOT_FOUND)
        self.assertEqual(error['message'], 'Заказ не найден.')
        # Canonical API-04 details: единственный non-field detail.
        self.assertEqual(error['details'], [{
            'field': None,
            'code': CODE_NOT_FOUND,
            'message': 'Заказ не найден.',
        }])
        # Платёж не создан.
        self.assertEqual(Payment.objects.count(), 0)
        # Ответ не раскрывает существование/детали чужого заказа.
        body_text = resp.content.decode('utf-8').lower()
        self.assertNotIn(str(other_order.pk), body_text)
        self.assertNotIn(other_order.order_number.lower(), body_text)
        self.assertNotIn(other_user.email.lower(), body_text)

    @override_settings(DEFAULT_THROTTLE_CLASSES=[])
    def test_create_payment_non_existent_order(self):
        """Несуществующий order_id → canonical API-04 404."""
        self.client.force_authenticate(self.user)
        data = {'order_id': 999_999, 'amount': '1000.00'}
        resp = self.client.post(self.url, data, format='json')

        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)
        error = resp.data['error']
        self.assertEqual(set(error), {'code', 'message', 'details'})
        self.assertEqual(error['code'], CODE_NOT_FOUND)
        self.assertEqual(error['message'], 'Заказ не найден.')
        # Canonical API-04 details: единственный non-field detail.
        self.assertEqual(error['details'], [{
            'field': None,
            'code': CODE_NOT_FOUND,
            'message': 'Заказ не найден.',
        }])
        self.assertEqual(Payment.objects.count(), 0)

    @override_settings(DEFAULT_THROTTLE_CLASSES=[])
    def test_create_payment_foreign_and_non_existent_orders_identical(self):
        """Чужой и несуществующий заказ → неотличимые 404-ответы."""
        self.client.force_authenticate(self.user)
        other_user = create_test_user()
        other_order = create_test_order(other_user)

        foreign = self.client.post(
            self.url, {'order_id': other_order.pk}, format='json',
        )
        missing = self.client.post(
            self.url, {'order_id': 999_999}, format='json',
        )

        self.assertEqual(foreign.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(missing.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(foreign.data['error'], missing.data['error'])
        self.assertEqual(Payment.objects.count(), 0)

    # ── Бизнес-правила не ослаблены (Issue #68: regression guard) ──

    @override_settings(DEFAULT_THROTTLE_CLASSES=[])
    def test_create_payment_missing_order_id_returns_400(self):
        """Некорректный вход (нет ссылки на заказ) → 400 validation envelope."""
        self.client.force_authenticate(self.user)
        resp = self.client.post(
            self.url, {'amount': '1000.00'}, format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.data['error']['code'], CODE_VALIDATION)
        self.assertEqual(Payment.objects.count(), 0)

    @override_settings(DEFAULT_THROTTLE_CLASSES=[])
    def test_create_payment_invalid_amount_returns_400(self):
        """Сумма вне [MIN_PAYMENT_AMOUNT, MAX_PAYMENT_AMOUNT] → 400."""
        self.client.force_authenticate(self.user)
        for amount in ('0.50', '100000000.00'):
            with self.subTest(amount=amount):
                resp = self.client.post(
                    self.url,
                    {'order_id': self.order.pk, 'amount': amount},
                    format='json',
                )
                self.assertEqual(
                    resp.status_code,
                    status.HTTP_400_BAD_REQUEST,
                )
                self.assertEqual(
                    resp.data['error']['code'],
                    CODE_VALIDATION,
                )
                self.assertEqual(Payment.objects.count(), 0)

    @override_settings(DEFAULT_THROTTLE_CLASSES=[])
    def test_create_payment_confirmed_order_returns_400(self):
        """Заказ в статусе ≠ PENDING (CONFIRMED) оплатить нельзя → 400."""
        self.client.force_authenticate(self.user)
        self.order.status = 'confirmed'
        self.order.save()
        resp = self.client.post(
            self.url, {'order_id': self.order.pk}, format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.data['error']['code'], CODE_VALIDATION)
        self.assertEqual(Payment.objects.count(), 0)

    @override_settings(DEFAULT_THROTTLE_CLASSES=[])
    def test_create_payment_cancelled_order_returns_400(self):
        """Отменённый заказ оплатить нельзя → 400."""
        self.client.force_authenticate(self.user)
        self.order.status = 'cancelled'
        self.order.save()
        resp = self.client.post(
            self.url, {'order_id': self.order.pk}, format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.data['error']['code'], CODE_VALIDATION)
        self.assertEqual(Payment.objects.count(), 0)

    @override_settings(DEFAULT_THROTTLE_CLASSES=[])
    def test_create_payment_amount_mismatch_order_total_returns_400(self):
        """amount != order.total → 400 с ошибкой по полю amount."""
        self.client.force_authenticate(self.user)
        resp = self.client.post(
            self.url,
            {'order_id': self.order.pk, 'amount': '500.00'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.data['error']['code'], CODE_VALIDATION)
        fields = [
            detail['field']
            for detail in resp.data['error']['details']
            if detail['field'] is not None
        ]
        self.assertIn('amount', fields)
        self.assertEqual(Payment.objects.count(), 0)

    @override_settings(DEFAULT_THROTTLE_CLASSES=[])
    def test_create_payment_already_paid_order_returns_400(self):
        """Успешно оплаченный заказ нельзя оплатить повторно → 400."""
        create_test_payment(self.order, self.user, status='succeeded')
        self.client.force_authenticate(self.user)
        resp = self.client.post(
            self.url, {'order_id': self.order.pk}, format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.data['error']['code'], CODE_VALIDATION)
        self.assertIn('уже оплачен', resp.data['error']['message'])
        # Новых платежей не создано (существующий SUCCEEDED остался один).
        self.assertEqual(Payment.objects.count(), 1)


class PaymentDetailAPITests(TestCase):
    """Тесты GET /api/v1/payments/{payment_number}/."""

    def setUp(self):
        self.user = create_test_user()
        self.order = create_test_order(self.user)
        self.payment = create_test_payment(self.order, self.user)
        self.client = APIClient()

    def test_detail_success(self):
        """Детали платежа доступны владельцу."""
        self.client.force_authenticate(self.user)
        url = reverse(
            'payments:payment-detail',
            kwargs={'payment_number': self.payment.order_number},
        )
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['id'], self.payment.pk)
        self.assertIn('events', resp.data)

    def test_detail_other_user(self):
        """Чужой платёж → 404."""
        other_user = create_test_user()
        self.client.force_authenticate(other_user)
        url = reverse(
            'payments:payment-detail',
            kwargs={'payment_number': self.payment.order_number},
        )
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_detail_unauthenticated(self):
        """Без аутентификации → 401."""
        url = reverse(
            'payments:payment-detail',
            kwargs={'payment_number': self.payment.order_number},
        )
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)


class PaymentRefundAPITests(TestCase):
    """Тесты POST /api/v1/payments/{payment_number}/refund/."""

    def setUp(self):
        self.user = create_test_user()
        self.order = create_test_order(self.user)
        self.payment = create_test_payment(
            self.order, self.user, status='succeeded',
        )
        self.staff_user = create_test_user(is_staff=True)
        self.client = APIClient()

    def test_refund_by_staff(self):
        """Staff может вернуть платёж."""
        self.client.force_authenticate(self.staff_user)
        url = reverse(
            'payments:payment-refund',
            kwargs={'payment_number': self.payment.order_number},
        )
        resp = self.client.post(url, {'reason': 'Брак'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, 'refunded')

    def test_refund_by_regular_user_forbidden(self):
        """Обычный пользователь не может вернуть платёж."""
        self.client.force_authenticate(self.user)
        url = reverse(
            'payments:payment-refund',
            kwargs={'payment_number': self.payment.order_number},
        )
        resp = self.client.post(url, {'reason': 'Test'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_partial_refund_by_staff(self):
        """Staff может сделать частичный возврат."""
        self.client.force_authenticate(self.staff_user)
        url = reverse(
            'payments:payment-refund',
            kwargs={'payment_number': self.payment.order_number},
        )
        resp = self.client.post(
            url, {'amount': '300.00', 'reason': 'Partial'}, format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, 'succeeded')
        self.assertEqual(self.payment.refund_amount, Decimal('300.00'))


class PaymentCancelAPITests(TestCase):
    """Тесты POST /api/v1/payments/{payment_number}/cancel/."""

    def setUp(self):
        self.user = create_test_user()
        self.order = create_test_order(self.user)
        self.payment = create_test_payment(
            self.order, self.user, status='pending',
        )
        self.client = APIClient()

    def test_cancel_by_owner(self):
        """Владелец может отменить PENDING платёж."""
        self.client.force_authenticate(self.user)
        url = reverse(
            'payments:payment-cancel',
            kwargs={'payment_number': self.payment.order_number},
        )
        resp = self.client.post(url, {'reason': 'Передумал'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, 'cancelled')

    def test_cancel_by_other_user(self):
        """Чужой платёж → 404."""
        other_user = create_test_user()
        self.client.force_authenticate(other_user)
        url = reverse(
            'payments:payment-cancel',
            kwargs={'payment_number': self.payment.order_number},
        )
        resp = self.client.post(url, format='json')
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)


class PaymentWebhookAPITests(TestCase):
    """Тесты POST /api/v1/payments/webhook/ — базовые.

    Подпись/заголовки — единый помощник webhook_helpers
    (timestamp + nonce + HMAC по канону, Issue #71).
    """

    def setUp(self):
        self.user = create_test_user()
        self.order = create_test_order(self.user)
        self.payment = create_test_payment(
            self.order, self.user, status='processing',
        )
        self.client = APIClient()
        self.url = reverse('payments:payment-webhook')

    @override_settings(PAYMENT_WEBHOOK_SECRET=WEBHOOK_SECRET)
    def test_webhook_with_valid_signature(self):
        """Webhook с валидной подписью (ts + nonce + HMAC) → 200."""
        data = {
            'external_id': self.payment.external_id,
            'event_type': 'payment.succeeded',
            'status': 'succeeded',
        }
        resp = post_signed_webhook(self.client, self.url, data)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['status'], 'succeeded')

    @override_settings(PAYMENT_WEBHOOK_SECRET=WEBHOOK_SECRET)
    def test_webhook_unknown_payment(self):
        """Webhook с неизвестным external_id → 200 + сообщение."""
        data = {
            'external_id': 'unknown_ext_id_999',
            'event_type': 'payment.succeeded',
            'status': 'succeeded',
        }
        resp = post_signed_webhook(self.client, self.url, data)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn('не найден', resp.data['detail'])

    @override_settings(PAYMENT_WEBHOOK_SECRET=WEBHOOK_SECRET)
    def test_webhook_failed_payment(self):
        """Webhook status=failed → FAILED."""
        data = {
            'external_id': self.payment.external_id,
            'event_type': 'payment.failed',
            'status': 'failed',
            'payload': {'error': 'insufficient_funds'},
        }
        resp = post_signed_webhook(self.client, self.url, data)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['status'], 'failed')

    def test_webhook_invalid_data(self):
        """Webhook с невалидными данными → 400 или 403."""
        data = {
            'external_id': self.payment.external_id,
            'status': 'invalid_status',
        }
        resp = self.client.post(self.url, data, format='json')
        # Without valid signature → 403; with signature but bad data → 400
        self.assertIn(resp.status_code, [
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_403_FORBIDDEN,
        ])
