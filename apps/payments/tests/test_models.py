# ────────────────────────────────────────────────────────────────────────
# apps/payments/tests/test_models.py — тесты моделей Payment и PaymentEvent.
#
# ПРОВЕРЯЕТ:
#   • Создание платежа с валидными данными
#   • Авто-генерация payment_number
#   • Уникальность payment_number
#   • CheckConstraints (amount, refund_amount)
#   • Properties (is_terminal, is_paid, is_refundable)
#   • __str__ representations
#   • Создание PaymentEvent
# ────────────────────────────────────────────────────────────────────────

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.test import TestCase

from apps.orders.tests.factories import create_test_order, create_test_user
from apps.payments.models import Payment, PaymentEvent
from apps.payments.tests.factories import (
    create_test_payment,
    create_test_payment_event,
)


class PaymentModelTests(TestCase):
    """Тесты модели Payment."""

    def setUp(self):
        self.user = create_test_user()
        self.order = create_test_order(self.user)
        self.payment = create_test_payment(self.order, self.user)

    # ── Создание ──

    def test_create_payment_with_defaults(self):
        """Платёж создаётся с авто-генерацией payment_number."""
        self.assertIsNotNone(self.payment.pk)
        self.assertTrue(self.payment.payment_number.startswith('PAY-'))
        self.assertEqual(self.payment.status, 'pending')
        self.assertEqual(self.payment.method, 'card')
        self.assertEqual(self.payment.provider, 'mock')
        self.assertEqual(self.payment.refund_amount, Decimal('0.00'))

    def test_order_number_auto_generation(self):
        """payment_number генерируется автоматически в формате PAY-000001."""
        self.assertRegex(
            self.payment.payment_number,
            r'PAY-\d{6}',
        )

    def test_order_number_sequential(self):
        """Номера платежей последовательны."""
        payment2 = create_test_payment(self.order, self.user)
        seq1 = self.payment._payment_number_seq
        seq2 = payment2._payment_number_seq
        self.assertEqual(seq2, seq1 + 1)

    def test_order_number_unique(self):
        """payment_number уникален — IntegrityError при дубле."""
        with self.assertRaises(IntegrityError):
            payment2 = create_test_payment(self.order, self.user)
            payment2.payment_number = self.payment.payment_number
            payment2.save()

    # ── Properties ──

    def test_is_terminal_pending(self):
        """PENDING — не терминальный."""
        self.assertFalse(self.payment.is_terminal)

    def test_is_terminal_failed(self):
        """FAILED — терминальный."""
        self.payment.status = 'failed'
        self.assertTrue(self.payment.is_terminal)

    def test_is_terminal_cancelled(self):
        """CANCELLED — терминальный."""
        self.payment.status = 'cancelled'
        self.assertTrue(self.payment.is_terminal)

    def test_is_terminal_refunded(self):
        """REFUNDED — терминальный."""
        self.payment.status = 'refunded'
        self.assertTrue(self.payment.is_terminal)

    def test_is_paid(self):
        """is_paid = True только при SUCCEEDED."""
        self.assertFalse(self.payment.is_paid)
        self.payment.status = 'succeeded'
        self.assertTrue(self.payment.is_paid)

    def test_is_refundable(self):
        """is_refundable = True только при SUCCEEDED."""
        self.assertFalse(self.payment.is_refundable)
        self.payment.status = 'succeeded'
        self.assertTrue(self.payment.is_refundable)

    # ── __str__ ──

    def test_str_representation(self):
        """__str__ содержит номер, статус и сумму."""
        result = str(self.payment)
        self.assertIn('PAY-', result)
        self.assertIn('Ожидает оплаты', result)
        self.assertIn(str(self.payment.amount), result)

    # ── Constraints ──

    def test_refund_amount_cannot_exceed_amount(self):
        """refund_amount не может быть > amount."""
        self.payment.status = 'succeeded'
        self.payment.save()
        self.payment.refund_amount = self.payment.amount + Decimal('1.00')
        with self.assertRaises(IntegrityError):
            self.payment.save()

    def test_metadata_default_is_dict(self):
        """metadata по умолчанию — пустой dict."""
        self.assertEqual(self.payment.metadata, {})

    def test_metadata_can_store_json(self):
        """metadata хранит JSON."""
        self.payment.metadata = {'last4': '4242', 'bank': 'test'}
        self.payment.save()
        payment = Payment.objects.get(pk=self.payment.pk)
        self.assertEqual(payment.metadata['last4'], '4242')


class PaymentEventModelTests(TestCase):
    """Тесты модели PaymentEvent."""

    def setUp(self):
        self.user = create_test_user()
        self.order = create_test_order(self.user)
        self.payment = create_test_payment(self.order, self.user)
        self.event = create_test_payment_event(
            self.payment,
            event_type='created',
            new_status='pending',
        )

    def test_create_event(self):
        """Событие создаётся корректно."""
        self.assertIsNotNone(self.event.pk)
        self.assertEqual(self.event.event_type, 'created')
        self.assertEqual(self.event.new_status, 'pending')

    def test_event_has_payment(self):
        """Событие связано с платежом."""
        self.assertEqual(self.event.payment_id, self.payment.pk)

    def test_event_ordering(self):
        """События сортируются по created_at ASC."""
        event2 = create_test_payment_event(
            self.payment,
            event_type='status_changed',
            old_status='pending',
            new_status='processing',
        )
        events = list(self.payment.events.all())
        self.assertEqual(events[0].pk, self.event.pk)
        self.assertEqual(events[1].pk, event2.pk)

    def test_str_representation(self):
        """__str__ содержит тип события."""
        result = str(self.event)
        self.assertIn('PaymentEvent', result)
        self.assertIn('created', result)

    def test_payload_stores_json(self):
        """payload хранит JSON."""
        self.event.payload = {'webhook': 'data', 'code': 200}
        self.event.save()
        event = PaymentEvent.objects.get(pk=self.event.pk)
        self.assertEqual(event.payload['code'], 200)
