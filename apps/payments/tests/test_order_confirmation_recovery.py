# ────────────────────────────────────────────────────────────────────────
# apps/payments/tests/test_order_confirmation_recovery.py
#
# PROD-003 — подтверждение заказа после успешного платежа fail-safe:
#
#   • провал резервирования откатывает платёж (OrderConfirmationError,
#     платёж остаётся PROCESSING — денежное состояние не уходит вперёд);
#   • провал из-за уже продвинутого заказа (staff подтвердил заранее)
#     НЕ откатывает деньги: SUCCEEDED консистентен + событие
#     order_confirm_failed делает расхождение наблюдаемым;
#   • вебхук на завершённый заказ: платёж закрывается, ответ 200;
#   • вебхук на PENDING-заказ с нехваткой стока: 502 (провайдер
#     повторит) + durable-событие order_confirm_failed;
#   • идемпотентная повторная доставка «залечивает» SUCCEEDED+PENDING;
#   • reconcile_succeeded_payment — точка восстановления для команды.
# ────────────────────────────────────────────────────────────────────────

import hashlib
import hmac
import json
from decimal import Decimal

from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from apps.catalog.constants import ProductStatus
from apps.catalog.models import Brand, Category, Product, ProductVariant
from apps.inventory.models import Stock
from apps.orders.models import Order, OrderItem
from apps.orders.models.order import OrderStatus
from apps.orders.tests.factories import create_test_order, create_test_user
from apps.payments.constants import (
    PAYMENT_EVENT_ORDER_CONFIRM_FAILED,
    PAYMENT_STATUS_FAILED,
    PAYMENT_STATUS_PENDING,
    PAYMENT_STATUS_PROCESSING,
    PAYMENT_STATUS_SUCCEEDED,
)
from apps.payments.exceptions import OrderConfirmationError
from apps.payments.models import PaymentEvent
from apps.payments.services.payment_service import PaymentService
from apps.payments.tests.factories import create_test_payment

WEBHOOK_SECRET = 'test-webhook-secret-key-32bytes!!'


def _sign_body(body: bytes, secret: str = WEBHOOK_SECRET) -> str:
    return hmac.new(
        secret.encode('utf-8'),
        body,
        hashlib.sha256,
    ).hexdigest()


def _make_order_with_item(quantity: int = 5, stock_quantity: int = 100):
    """Заказ с одной позицией и стоком stock_quantity."""
    user = create_test_user()
    brand = Brand.objects.create(name='PayRecoveryBrand')
    category = Category.add_root(name='PayRecoveryCat')
    product = Product.objects.create(
        name='PayRecovery Product',
        brand=brand,
        primary_category=category,
        status=ProductStatus.ACTIVE,
    )
    variant = ProductVariant.objects.create(
        product=product,
        sku='PAYREC-SKU-001',
    )
    Stock.objects.create(variant=variant, quantity=stock_quantity)
    order = create_test_order(user)
    OrderItem.objects.create(
        order=order,
        variant=variant,
        product_name=product.name,
        sku=variant.sku,
        unit_price=Decimal('100.00'),
        quantity=quantity,
    )
    return order, variant


class ConfirmPaymentFailureTests(TestCase):
    """Классификация сбоев подтверждения заказа."""

    def setUp(self):
        self.user = create_test_user()
        self.order = create_test_order(self.user)
        self.payment = create_test_payment(
            self.order,
            self.user,
            status=PAYMENT_STATUS_PROCESSING,
        )

    def test_reserve_failure_rolls_back_payment(self):
        order, variant = _make_order_with_item(quantity=5, stock_quantity=2)
        payment = create_test_payment(
            order,
            order.user,
            status=PAYMENT_STATUS_PROCESSING,
        )

        with self.assertRaises(OrderConfirmationError):
            PaymentService.confirm_payment(payment)

        payment.refresh_from_db()
        self.assertEqual(payment.status, PAYMENT_STATUS_PROCESSING)
        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.PENDING)
        self.assertFalse(
            payment.events.filter(event_type='confirmed').exists(),
            'подтверждение должно откатиться вместе с транзакцией',
        )

    def test_order_already_confirmed_commits_payment_with_event(self):
        # Staff подтвердил заказ заранее (без позиций — резерв не нужен).
        Order.objects.filter(pk=self.order.pk).update(
            status=OrderStatus.CONFIRMED,
        )

        payment = PaymentService.confirm_payment(self.payment)

        self.assertEqual(payment.status, PAYMENT_STATUS_SUCCEEDED)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, OrderStatus.CONFIRMED)
        self.assertTrue(
            self.payment.events.filter(
                event_type=PAYMENT_EVENT_ORDER_CONFIRM_FAILED,
            ).exists(),
        )

    def test_order_cancelled_raises_and_keeps_payment_pending(self):
        Order.objects.filter(pk=self.order.pk).update(
            status=OrderStatus.CANCELLED,
        )

        with self.assertRaises(OrderConfirmationError):
            PaymentService.confirm_payment(self.payment)

        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, PAYMENT_STATUS_PROCESSING)

    def test_idempotent_reentry_reconciles_pending_order(self):
        """SUCCEEDED платёж + PENDING заказ: повторная доставка лечит."""
        payment = create_test_payment(
            self.order,
            self.user,
            status=PAYMENT_STATUS_SUCCEEDED,
        )

        result = PaymentService.confirm_payment(payment)

        self.assertEqual(result.status, PAYMENT_STATUS_SUCCEEDED)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, OrderStatus.CONFIRMED)


class ReconcileSucceededPaymentTests(TestCase):
    """reconcile_succeeded_payment — точки восстановления."""

    def setUp(self):
        self.user = create_test_user()
        self.order = create_test_order(self.user)

    def test_pending_order_confirmed(self):
        payment = create_test_payment(
            self.order,
            self.user,
            status=PAYMENT_STATUS_SUCCEEDED,
        )
        self.assertEqual(
            PaymentService.reconcile_succeeded_payment(payment),
            'confirmed',
        )
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, OrderStatus.CONFIRMED)

    def test_cancelled_order_requires_refund(self):
        payment = create_test_payment(
            self.order,
            self.user,
            status=PAYMENT_STATUS_SUCCEEDED,
        )
        Order.objects.filter(pk=self.order.pk).update(
            status=OrderStatus.CANCELLED,
        )

        self.assertEqual(
            PaymentService.reconcile_succeeded_payment(payment),
            'refund_required',
        )
        payment.refresh_from_db()
        self.assertEqual(
            payment.refund_required_amount,
            payment.amount,
        )
        self.assertTrue(
            payment.events.filter(event_type='refund_failed').exists(),
        )

    def test_confirmed_order_ok(self):
        payment = create_test_payment(
            self.order,
            self.user,
            status=PAYMENT_STATUS_SUCCEEDED,
        )
        Order.objects.filter(pk=self.order.pk).update(
            status=OrderStatus.CONFIRMED,
        )
        self.assertEqual(
            PaymentService.reconcile_succeeded_payment(payment),
            'ok',
        )

    def test_non_succeeded_skipped(self):
        payment = create_test_payment(
            self.order,
            self.user,
            status=PAYMENT_STATUS_PENDING,
        )
        self.assertEqual(
            PaymentService.reconcile_succeeded_payment(payment),
            'skipped',
        )


class WebhookOrderConfirmationRecoveryTests(TestCase):
    """HTTP-контракт вебхука при сбое подтверждения заказа."""

    def setUp(self):
        self.client = APIClient()
        self.url = reverse('payments:payment-webhook')

    def _post_signed(self, payment, status_value='succeeded'):
        data = {
            'external_id': payment.external_id,
            'event_type': 'payment.succeeded',
            'status': status_value,
        }
        body = json.dumps(data).encode('utf-8')
        return self.client.post(
            self.url,
            data=body,
            content_type='application/json',
            HTTP_X_WEBHOOK_SIGNATURE=_sign_body(body),
        )

    @override_settings(PAYMENT_WEBHOOK_SECRET=WEBHOOK_SECRET)
    def test_insufficient_stock_returns_502_with_durable_event(self):
        order, variant = _make_order_with_item(quantity=5, stock_quantity=2)
        payment = create_test_payment(
            order,
            order.user,
            status=PAYMENT_STATUS_PROCESSING,
        )

        resp = self._post_signed(payment)

        self.assertEqual(resp.status_code, status.HTTP_502_BAD_GATEWAY)
        payment.refresh_from_db()
        self.assertEqual(payment.status, PAYMENT_STATUS_PROCESSING)
        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.PENDING)
        # Событие записано ПОСЛЕ отката транзакции вебхука — durable.
        self.assertTrue(
            PaymentEvent.objects.filter(
                payment=payment,
                event_type=PAYMENT_EVENT_ORDER_CONFIRM_FAILED,
            ).exists(),
        )

    @override_settings(PAYMENT_WEBHOOK_SECRET=WEBHOOK_SECRET)
    def test_closed_order_returns_200_and_closes_payment(self):
        order, variant = _make_order_with_item(quantity=5, stock_quantity=2)
        Order.objects.filter(pk=order.pk).update(
            status=OrderStatus.CANCELLED,
        )
        payment = create_test_payment(
            order,
            order.user,
            status=PAYMENT_STATUS_PROCESSING,
        )

        resp = self._post_signed(payment)

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        payment.refresh_from_db()
        self.assertEqual(payment.status, PAYMENT_STATUS_FAILED)
        self.assertTrue(
            PaymentEvent.objects.filter(
                payment=payment,
                event_type=PAYMENT_EVENT_ORDER_CONFIRM_FAILED,
            ).exists(),
        )

    @override_settings(PAYMENT_WEBHOOK_SECRET=WEBHOOK_SECRET)
    def test_retry_after_restock_succeeds(self):
        """502 → восстановление стока → повтор вебхука → 200."""
        from apps.inventory.services.inventory_service import InventoryService

        order, variant = _make_order_with_item(quantity=5, stock_quantity=2)
        payment = create_test_payment(
            order,
            order.user,
            status=PAYMENT_STATUS_PROCESSING,
        )

        first = self._post_signed(payment)
        self.assertEqual(first.status_code, status.HTTP_502_BAD_GATEWAY)

        InventoryService.restock(variant, 100)
        second = self._post_signed(payment)
        self.assertEqual(second.status_code, status.HTTP_200_OK)

        payment.refresh_from_db()
        self.assertEqual(payment.status, PAYMENT_STATUS_SUCCEEDED)
        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.CONFIRMED)
