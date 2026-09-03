# ────────────────────────────────────────────────────────────────────────
# apps/orders/tests/test_coordination_failures.py
#
# PROD-003 — сбои координации заказ ↔ склад больше не молчаливые:
#
#   • провал резервирования откатывает подтверждение заказа
#     (заказ остаётся PENDING, сток не тронут, движений нет);
#   • провал списания откатывает DELIVERED (заказ остаётся SHIPPED);
#   • провал освобождения откатывает отмену ЦЕЛИКОМ (включая coupon
#     usage и статус) — повтор отмены безопасен (операции идемпотентны);
#   • после восстановления причин сбоя повторный переход успешен.
# ────────────────────────────────────────────────────────────────────────

import json
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TransactionTestCase
from rest_framework.exceptions import ValidationError

from apps.catalog.constants import ProductStatus
from apps.catalog.models import Brand, Category, Product, ProductVariant
from apps.discounts.models import Coupon, CouponUsage
from apps.discounts.tests.factories import create_test_coupon
from apps.inventory.models import Stock, StockMovement
from apps.inventory.services.inventory_service import InventoryService
from apps.orders.models import Order, OrderItem
from apps.orders.models.order import OrderStatus
from apps.orders.services.order_service import OrderService
from apps.orders.tests.factories import create_test_order, create_test_user


def _make_order_with_item(quantity: int = 5, stock_quantity: int = 100):
    """Заказ с одной позицией и стоком stock_quantity."""
    user = create_test_user()
    brand = Brand.objects.create(name='CoordBrand')
    category = Category.add_root(name='CoordCat')
    product = Product.objects.create(
        name='Coord Product',
        brand=brand,
        primary_category=category,
        status=ProductStatus.ACTIVE,
    )
    variant = ProductVariant.objects.create(
        product=product,
        sku='COORD-SKU-001',
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


class ReserveFailureTests(TransactionTestCase):
    """Провал резервирования откатывает CONFIRMED."""

    def test_insufficient_stock_aborts_confirm(self):
        order, variant = _make_order_with_item(quantity=5, stock_quantity=2)

        with self.assertRaises(ValidationError):
            OrderService.transition_status(order, OrderStatus.CONFIRMED)

        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.PENDING)
        stock = Stock.objects.get(variant=variant)
        self.assertEqual(stock.reserved_quantity, 0)
        self.assertEqual(stock.quantity, 2)
        self.assertFalse(
            StockMovement.objects.filter(
                order=order,
                kind='reserve',
            ).exists(),
        )

    def test_confirm_succeeds_after_recovery(self):
        """Повторный переход после устранения причины — успешен."""
        order, variant = _make_order_with_item(quantity=5, stock_quantity=2)

        with self.assertRaises(ValidationError):
            OrderService.transition_status(order, OrderStatus.CONFIRMED)

        InventoryService.restock(variant, 100)
        OrderService.transition_status(order, OrderStatus.CONFIRMED)

        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.CONFIRMED)
        stock = Stock.objects.get(variant=variant)
        self.assertEqual(stock.reserved_quantity, 5)
        self.assertEqual(
            StockMovement.objects.filter(
                order=order,
                kind='reserve',
            ).count(),
            1,
        )

    def test_confirm_twice_rejected_by_fsm(self):
        """Идемпотентность перехода обеспечивается FSM, а не складу."""
        order, variant = _make_order_with_item(quantity=5)
        OrderService.transition_status(order, OrderStatus.CONFIRMED)

        with self.assertRaises(ValidationError):
            OrderService.transition_status(order, OrderStatus.CONFIRMED)

        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.CONFIRMED)
        stock = Stock.objects.get(variant=variant)
        self.assertEqual(stock.reserved_quantity, 5)
        self.assertEqual(
            StockMovement.objects.filter(
                order=order,
                kind='reserve',
            ).count(),
            1,
        )


class CommitFailureTests(TransactionTestCase):
    """Провал списания откатывает DELIVERED."""

    def test_commit_failure_aborts_deliver(self):
        order, variant = _make_order_with_item(quantity=5)
        OrderService.transition_status(order, OrderStatus.CONFIRMED)
        OrderService.transition_status(order, OrderStatus.PROCESSING)
        OrderService.transition_status(order, OrderStatus.SHIPPED)

        with patch.object(
            InventoryService,
            'commit_stock',
            side_effect=RuntimeError('db failure'),
        ):
            with self.assertRaises(RuntimeError):
                OrderService.transition_status(
                    order,
                    OrderStatus.DELIVERED,
                )

        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.SHIPPED)
        stock = Stock.objects.get(variant=variant)
        self.assertEqual(stock.quantity, 100, 'сток не должен списаться')
        self.assertEqual(stock.reserved_quantity, 5, 'резерв сохраняется')
        self.assertFalse(
            StockMovement.objects.filter(
                order=order,
                kind='out',
            ).exists(),
        )

    def test_deliver_succeeds_after_recovery(self):
        """Повторный DELIVERED после устранения сбоя — успешен."""
        order, variant = _make_order_with_item(quantity=5)
        OrderService.transition_status(order, OrderStatus.CONFIRMED)
        OrderService.transition_status(order, OrderStatus.PROCESSING)
        OrderService.transition_status(order, OrderStatus.SHIPPED)

        with patch.object(
            InventoryService,
            'commit_stock',
            side_effect=RuntimeError('db failure'),
        ):
            with self.assertRaises(RuntimeError):
                OrderService.transition_status(
                    order,
                    OrderStatus.DELIVERED,
                )

        OrderService.transition_status(order, OrderStatus.DELIVERED)
        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.DELIVERED)
        stock = Stock.objects.get(variant=variant)
        self.assertEqual(stock.quantity, 95)
        self.assertEqual(stock.reserved_quantity, 0)
        self.assertEqual(
            StockMovement.objects.filter(
                order=order,
                kind='out',
            ).count(),
            1,
        )


class ReleaseFailureTests(TransactionTestCase):
    """Провал освобождения откатывает отмену целиком."""

    def test_release_failure_aborts_cancel(self):
        order, variant = _make_order_with_item(quantity=5)
        OrderService.transition_status(order, OrderStatus.CONFIRMED)

        with patch.object(
            InventoryService,
            'release_stock',
            side_effect=RuntimeError('db failure'),
        ):
            with self.assertRaises(RuntimeError):
                OrderService.cancel(order, reason='changed_mind')

        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.CONFIRMED)
        stock = Stock.objects.get(variant=variant)
        self.assertEqual(stock.reserved_quantity, 5, 'резерв сохраняется')

    def test_release_failure_keeps_coupon_usage(self):
        """Откат отмены не теряет coupon usage (консистентность)."""
        order, variant = _make_order_with_item(quantity=5)
        coupon = create_test_coupon(code='FAILSAFE10')
        OrderService.apply_coupon(order, coupon.code, user=order.user)
        OrderService.transition_status(order, OrderStatus.CONFIRMED)

        with patch.object(
            InventoryService,
            'release_stock',
            side_effect=RuntimeError('db failure'),
        ):
            with self.assertRaises(RuntimeError):
                OrderService.cancel(order, reason='changed_mind')

        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.CONFIRMED)
        coupon.refresh_from_db()
        self.assertEqual(coupon.times_used, 1)
        self.assertTrue(
            CouponUsage.objects.filter(order=order).exists(),
        )

    def test_cancel_succeeds_after_recovery(self):
        """Повторная отмена после устранения сбоя — успешна."""
        order, variant = _make_order_with_item(quantity=5)
        OrderService.transition_status(order, OrderStatus.CONFIRMED)

        with patch.object(
            InventoryService,
            'release_stock',
            side_effect=RuntimeError('db failure'),
        ):
            with self.assertRaises(RuntimeError):
                OrderService.cancel(order, reason='changed_mind')

        OrderService.cancel(order, reason='changed_mind')
        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.CANCELLED)
        stock = Stock.objects.get(variant=variant)
        self.assertEqual(stock.reserved_quantity, 0)
        self.assertEqual(
            StockMovement.objects.filter(
                order=order,
                kind='release',
            ).count(),
            1,
        )

    def test_transition_status_still_rejects_cancelled(self):
        """EDU-002/AC7: cancel() остаётся единственной точкой отмены."""
        order, variant = _make_order_with_item(quantity=5)
        with self.assertRaises(ValidationError) as ctx:
            OrderService.transition_status(order, OrderStatus.CANCELLED)
        self.assertIn('cancel()', str(ctx.exception.detail))
        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.PENDING)


class ReconcileOrderCoordinationCommandTests(TransactionTestCase):
    """Команда reconcile_order_coordination восстанавливает обе стороны."""

    def test_reconciles_inventory_for_delivered_order(self):
        order, variant = _make_order_with_item(quantity=5)
        InventoryService.reserve_stock(order)
        Order.objects.filter(pk=order.pk).update(
            status=OrderStatus.DELIVERED,
        )

        call_command('reconcile_order_coordination', stdout=None)

        stock = Stock.objects.get(variant=variant)
        self.assertEqual(stock.quantity, 95, 'списание восстановлено')
        self.assertEqual(stock.reserved_quantity, 0)

    def test_reconciles_succeeded_payment_on_cancelled_order(self):
        from apps.payments.tests.factories import create_test_payment

        order, variant = _make_order_with_item(quantity=5)
        Order.objects.filter(pk=order.pk).update(
            status=OrderStatus.CANCELLED,
        )
        payment = create_test_payment(
            order,
            order.user,
            status='succeeded',
            amount=Decimal('500.00'),
        )

        call_command('reconcile_order_coordination', stdout=None)

        payment.refresh_from_db()
        self.assertEqual(
            payment.refund_required_amount,
            Decimal('500.00'),
            'обязательство возврата зафиксировано',
        )

    def test_json_report_output(self):
        import io

        order, variant = _make_order_with_item(quantity=5)
        Order.objects.filter(pk=order.pk).update(
            status=OrderStatus.DELIVERED,
        )

        out = io.StringIO()
        call_command(
            'reconcile_order_coordination',
            '--json',
            stdout=out,
        )
        report = json.loads(out.getvalue())
        self.assertIn('orders_checked', report)
        self.assertIn('inventory_actions', report)
        self.assertIn('payment_reconciliations', report)
        self.assertIn('errors', report)

    def test_expected_domain_error_is_reported(self):
        """Domain/not-found/DB failures remain visible in the report."""
        order, variant = _make_order_with_item(quantity=5)
        Order.objects.filter(pk=order.pk).update(
            status=OrderStatus.DELIVERED,
        )

        out = StringIO()
        with patch(
            'apps.orders.management.commands.'
            'reconcile_order_coordination.InventoryService.reconcile_order',
            side_effect=ValidationError({'detail': 'cannot reconcile'}),
        ):
            call_command(
                'reconcile_order_coordination',
                order.order_number,
                '--json',
                stdout=out,
                stderr=StringIO(),
            )

        report = json.loads(out.getvalue())
        self.assertEqual(report['errors'][0]['phase'], 'inventory')

    def test_unexpected_error_propagates(self):
        """Unexpected errors stop the command instead of producing success."""
        order, variant = _make_order_with_item(quantity=5)
        Order.objects.filter(pk=order.pk).update(
            status=OrderStatus.DELIVERED,
        )

        with patch(
            'apps.orders.management.commands.'
            'reconcile_order_coordination.InventoryService.reconcile_order',
            side_effect=RuntimeError('boom'),
        ):
            with self.assertRaises(RuntimeError):
                call_command(
                    'reconcile_order_coordination',
                    order.order_number,
                    stdout=StringIO(),
                    stderr=StringIO(),
                )
