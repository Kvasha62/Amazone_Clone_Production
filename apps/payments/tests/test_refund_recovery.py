# ────────────────────────────────────────────────────────────────────────
# apps/payments/tests/test_refund_recovery.py
#
# PROD-003 — провал возврата никогда не теряется:
#
#   • сбой исполнения возврата фиксирует retryable-обязательство
#     (refund_required_amount > refund_amount) + событие refund_failed;
#   • retry_pending_refunds() (и команда retry_pending_refunds)
#     доводит обязательство до конца, повторные запуски идемпотентны;
#   • отмена заказа с провалившимся возвратом оставляет заказ
#     CANCELLED, а обязательство — в БД (никакой молчаливой потери);
#   • record_refund_failure_durable пишет через выделенное соединение —
#     запись переживает откат основной транзакции.
# ────────────────────────────────────────────────────────────────────────

from decimal import Decimal
from unittest.mock import patch

from django.core.management import call_command
from django.db import connection
from django.test import TestCase, TransactionTestCase
from apps.orders.models.order import OrderStatus
from apps.orders.services.order_service import OrderService
from apps.orders.tests.factories import create_test_order, create_test_user
from apps.payments.constants import (
    PAYMENT_EVENT_REFUND_FAILED,
    PAYMENT_STATUS_PROCESSING,
    PAYMENT_STATUS_REFUNDED,
    PAYMENT_STATUS_SUCCEEDED,
)
from apps.payments.services.payment_service import PaymentService
from apps.payments.tests.factories import create_test_payment


class RefundSettleFailureTests(TestCase):
    """Сбой исполнения возврата → явное retryable-обязательство."""

    def setUp(self):
        self.user = create_test_user()
        self.order = create_test_order(self.user)
        self.payment = create_test_payment(
            self.order,
            self.user,
            status=PAYMENT_STATUS_SUCCEEDED,
            amount=Decimal('1000.00'),
        )

    def test_settle_failure_records_retryable_requirement(self):
        with patch.object(
            PaymentService,
            '_settle_refund',
            side_effect=RuntimeError('provider refused'),
        ):
            payment = PaymentService.refund_payment(
                self.payment,
                reason='Отмена заказа',
            )

        self.assertEqual(payment.status, PAYMENT_STATUS_SUCCEEDED)
        self.assertEqual(payment.refund_amount, Decimal('0.00'))
        self.assertEqual(
            payment.refund_required_amount,
            Decimal('1000.00'),
        )
        self.assertEqual(
            payment.refund_pending_amount,
            Decimal('1000.00'),
        )
        self.assertTrue(
            self.payment.events.filter(
                event_type=PAYMENT_EVENT_REFUND_FAILED,
            ).exists(),
        )
        self.assertTrue(
            self.payment.events.filter(
                event_type='refund_initiated',
            ).exists(),
        )

    def test_retry_pending_refunds_settles_obligation(self):
        with patch.object(
            PaymentService,
            '_settle_refund',
            side_effect=RuntimeError('provider refused'),
        ):
            PaymentService.refund_payment(self.payment)

        stats = PaymentService.retry_pending_refunds()
        self.assertEqual(stats['found'], 1)
        self.assertEqual(stats['settled'], 1)
        self.assertEqual(stats['failed'], 0)

        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, PAYMENT_STATUS_REFUNDED)
        self.assertEqual(self.payment.refund_amount, Decimal('1000.00'))
        self.assertEqual(
            self.payment.refund_pending_amount,
            Decimal('0.00'),
        )

    def test_retry_pending_refunds_is_idempotent(self):
        PaymentService.refund_payment(self.payment)
        stats = PaymentService.retry_pending_refunds()
        self.assertEqual(stats['found'], 0)
        self.assertEqual(stats['settled'], 0)

        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, PAYMENT_STATUS_REFUNDED)
        self.assertEqual(self.payment.refund_amount, Decimal('1000.00'))

    def test_retry_pending_refunds_skips_non_succeeded(self):
        self.payment.status = PAYMENT_STATUS_PROCESSING
        self.payment.refund_required_amount = Decimal('1000.00')
        self.payment.save(
            update_fields=['status', 'refund_required_amount'],
        )

        stats = PaymentService.retry_pending_refunds()
        self.assertEqual(stats['found'], 0)

    def test_retry_command_settles_obligation(self):
        with patch.object(
            PaymentService,
            '_settle_refund',
            side_effect=RuntimeError('provider refused'),
        ):
            PaymentService.refund_payment(self.payment)

        call_command('retry_pending_refunds', stdout=None)

        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, PAYMENT_STATUS_REFUNDED)
        self.assertEqual(
            self.payment.refund_pending_amount,
            Decimal('0.00'),
        )


class CancelWithRefundFailureTests(TestCase):
    """Отмена заказа при провале возврата: заказ CANCELLED, долг — в БД."""

    def setUp(self):
        self.user = create_test_user()
        self.order = create_test_order(self.user, status=OrderStatus.CONFIRMED)
        self.payment = create_test_payment(
            self.order,
            self.user,
            status=PAYMENT_STATUS_SUCCEEDED,
            amount=Decimal('1000.00'),
        )

    def test_cancel_records_pending_refund_when_settle_fails(self):
        with patch.object(
            PaymentService,
            '_settle_refund',
            side_effect=RuntimeError('provider refused'),
        ):
            order = OrderService.cancel(self.order, reason='changed_mind')

        self.assertEqual(order.status, OrderStatus.CANCELLED)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, PAYMENT_STATUS_SUCCEEDED)
        self.assertEqual(
            self.payment.refund_required_amount,
            Decimal('1000.00'),
        )
        self.assertTrue(
            self.payment.events.filter(
                event_type=PAYMENT_EVENT_REFUND_FAILED,
            ).exists(),
        )

    def test_cancel_refunds_when_settle_succeeds(self):
        order = OrderService.cancel(self.order, reason='changed_mind')

        self.assertEqual(order.status, OrderStatus.CANCELLED)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, PAYMENT_STATUS_REFUNDED)
        self.assertEqual(
            self.payment.refund_pending_amount,
            Decimal('0.00'),
        )

    def test_cancel_recovery_command_settles_refund(self):
        with patch.object(
            PaymentService,
            '_settle_refund',
            side_effect=RuntimeError('provider refused'),
        ):
            OrderService.cancel(self.order, reason='changed_mind')

        stats = PaymentService.retry_pending_refunds()
        self.assertEqual(stats['settled'], 1)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, PAYMENT_STATUS_REFUNDED)


class RecordRefundFailureDurableTests(TransactionTestCase):
    """Durable-фиксация обязательства через независимое соединение."""

    def setUp(self):
        if connection.vendor != 'postgresql':
            self.skipTest(
                'durable-запись требует PostgreSQL: независимое '
                'psycopg-соединение; SQLite (:memory:) изолирован',
            )
        self.user = create_test_user()
        self.order = create_test_order(self.user)
        self.payment = create_test_payment(
            self.order,
            self.user,
            status=PAYMENT_STATUS_SUCCEEDED,
            amount=Decimal('1000.00'),
        )

    def test_records_requirement_and_event(self):
        recorded = PaymentService.record_refund_failure_durable(
            self.payment.pk,
            reason='Отмена заказа',
            error='db error',
            user_id=self.user.pk,
        )
        self.assertTrue(recorded)

        self.payment.refresh_from_db()
        self.assertEqual(
            self.payment.refund_required_amount,
            Decimal('1000.00'),
        )
        self.assertTrue(
            self.payment.events.filter(
                event_type=PAYMENT_EVENT_REFUND_FAILED,
            ).exists(),
        )

    def test_repeated_call_is_idempotent(self):
        PaymentService.record_refund_failure_durable(
            self.payment.pk,
            reason='Отмена заказа',
            error='db error',
        )
        self.assertTrue(
            PaymentService.record_refund_failure_durable(
                self.payment.pk,
                reason='Отмена заказа',
                error='db error',
            ),
        )
        self.assertEqual(
            self.payment.events.filter(
                event_type=PAYMENT_EVENT_REFUND_FAILED,
            ).count(),
            1,
        )

    def test_non_succeeded_payment_not_recorded(self):
        self.payment.status = PAYMENT_STATUS_PROCESSING
        self.payment.save(update_fields=['status'])

        recorded = PaymentService.record_refund_failure_durable(
            self.payment.pk,
            reason='Отмена заказа',
            error='db error',
        )
        self.assertFalse(recorded)
        self.assertFalse(
            self.payment.events.filter(
                event_type=PAYMENT_EVENT_REFUND_FAILED,
            ).exists(),
        )

    def test_unknown_payment_returns_false(self):
        self.assertFalse(
            PaymentService.record_refund_failure_durable(
                99999999,
                reason='Отмена заказа',
                error='db error',
            ),
        )

    def test_recorded_requirement_settled_by_retry(self):
        PaymentService.record_refund_failure_durable(
            self.payment.pk,
            reason='Отмена заказа',
            error='db error',
        )

        stats = PaymentService.retry_pending_refunds()
        self.assertEqual(stats['settled'], 1)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, PAYMENT_STATUS_REFUNDED)
        self.assertEqual(self.payment.refund_amount, Decimal('1000.00'))
