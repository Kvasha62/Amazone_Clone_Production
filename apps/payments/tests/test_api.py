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

from apps.orders.tests.factories import create_test_order, create_test_user
from apps.payments.models import Payment
from apps.payments.tests.factories import create_test_payment


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
        """Создание платежа через API."""
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

    def test_create_payment_other_users_order(self):
        """Нельзя создать платёж для чужого заказа."""
        self.client.force_authenticate(self.user)
        other_user = create_test_user()
        other_order = create_test_order(other_user)
        data = {'order_id': other_order.pk, 'amount': '500.00'}
        resp = self.client.post(self.url, data, format='json')
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)


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
    """Тесты POST /api/v1/payments/webhook/ — базовые (без HMAC)."""

    def setUp(self):
        self.user = create_test_user()
        self.order = create_test_order(self.user)
        self.payment = create_test_payment(
            self.order, self.user, status='processing',
        )
        self.client = APIClient()
        self.url = reverse('payments:payment-webhook')

    @override_settings(PAYMENT_WEBHOOK_SECRET='test-secret-key-32bytes!!!!')
    def test_webhook_with_valid_signature(self):
        """Webhook с валидной HMAC подписей → 200."""
        import hashlib, hmac, json
        data = {
            'external_id': self.payment.external_id,
            'event_type': 'payment.succeeded',
            'status': 'succeeded',
        }
        body = json.dumps(data).encode('utf-8')
        sig = hmac.new(b'test-secret-key-32bytes!!!!', body, hashlib.sha256).hexdigest()
        resp = self.client.post(
            self.url, data=body, content_type='application/json',
            HTTP_X_WEBHOOK_SIGNATURE=sig,
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['status'], 'succeeded')

    @override_settings(PAYMENT_WEBHOOK_SECRET='test-secret-key-32bytes!!!!')
    def test_webhook_unknown_payment(self):
        """Webhook с неизвестным external_id → 200 + сообщение."""
        import hashlib, hmac, json
        data = {
            'external_id': 'unknown_ext_id_999',
            'event_type': 'payment.succeeded',
            'status': 'succeeded',
        }
        body = json.dumps(data).encode('utf-8')
        sig = hmac.new(b'test-secret-key-32bytes!!!!', body, hashlib.sha256).hexdigest()
        resp = self.client.post(
            self.url, data=body, content_type='application/json',
            HTTP_X_WEBHOOK_SIGNATURE=sig,
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn('не найден', resp.data['detail'])

    @override_settings(PAYMENT_WEBHOOK_SECRET='test-secret-key-32bytes!!!!')
    def test_webhook_failed_payment(self):
        """Webhook status=failed → FAILED."""
        import hashlib, hmac, json
        data = {
            'external_id': self.payment.external_id,
            'event_type': 'payment.failed',
            'status': 'failed',
            'payload': {'error': 'insufficient_funds'},
        }
        body = json.dumps(data).encode('utf-8')
        sig = hmac.new(b'test-secret-key-32bytes!!!!', body, hashlib.sha256).hexdigest()
        resp = self.client.post(
            self.url, data=body, content_type='application/json',
            HTTP_X_WEBHOOK_SIGNATURE=sig,
        )
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
