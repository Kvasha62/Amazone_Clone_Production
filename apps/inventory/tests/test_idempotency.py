# ────────────────────────────────────────────────────────────────────────
# apps/inventory/tests/test_idempotency.py
#
# PROD-003 — идемпотентность и конкурентная безопасность операций склада.
#
# Резервирование, освобождение и списание одного заказа:
#   • повторный вызов — no-op (парность движений RESERVE↔RELEASE/OUT);
#   • конкурентные вызовы сериализуются локом строки заказа
#     (lock order: Order → Stock) — двойное списание/освобождение
#     и двойное резервирование невозможны.
#
# Тесты — TransactionTestCase с реальными cross-connection потоками
# (на PostgreSQL блокировки проверяются по-настоящему; приём из
# apps/pricing/tests/test_services.py и apps/reviews/tests/test_concurrency.py:
# каждый поток закрывает свои соединения в finally).
# ────────────────────────────────────────────────────────────────────────

import threading
from decimal import Decimal

from django.db import connections
from django.test import TransactionTestCase, skipUnlessDBFeature

from apps.catalog.constants import ProductStatus
from apps.catalog.models import Brand, Category, Product, ProductVariant
from apps.inventory.models import Stock, StockMovement
from apps.inventory.services.inventory_service import InventoryService
from apps.orders.models import Order, OrderItem
from apps.orders.models.order import OrderStatus
from apps.orders.tests.factories import create_test_order, create_test_user


def _make_order_with_item(quantity: int = 5) -> tuple[Order, ProductVariant]:
    """Заказ с одной позицией quantity шт. и стоком 100 шт."""
    user = create_test_user(email=f'user-{quantity}-{Order.objects.count()}@test.local')
    brand = Brand.objects.create(name=f'IdemBrand{quantity}')
    category = Category.add_root(name=f'IdemCat{quantity}')
    product = Product.objects.create(
        name=f'Idem Product {quantity}',
        brand=brand,
        primary_category=category,
        status=ProductStatus.ACTIVE,
    )
    variant = ProductVariant.objects.create(
        product=product,
        sku=f'IDEM-SKU-{quantity}',
    )
    Stock.objects.create(variant=variant, quantity=100)
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


@skipUnlessDBFeature('has_select_for_update')
class InventoryIdempotencyTests(TransactionTestCase):
    """Повторные операции склада не применяются дважды."""

    def test_reserve_twice_is_idempotent(self):
        order, variant = _make_order_with_item(quantity=5)
        InventoryService.reserve_stock(order)
        stock = Stock.objects.get(variant=variant)
        self.assertEqual(stock.reserved_quantity, 5)

        second = InventoryService.reserve_stock(order)
        self.assertEqual(second, [])
        stock.refresh_from_db()
        self.assertEqual(stock.reserved_quantity, 5)
        self.assertEqual(
            StockMovement.objects.filter(
                order=order,
                kind='reserve',
            ).count(),
            1,
        )

    def test_release_twice_is_idempotent(self):
        order, variant = _make_order_with_item(quantity=5)
        InventoryService.reserve_stock(order)
        InventoryService.release_stock(order)
        stock = Stock.objects.get(variant=variant)
        self.assertEqual(stock.reserved_quantity, 0)

        second = InventoryService.release_stock(order)
        self.assertEqual(second, [])
        stock.refresh_from_db()
        self.assertEqual(stock.reserved_quantity, 0)
        self.assertEqual(
            StockMovement.objects.filter(
                order=order,
                kind='release',
            ).count(),
            1,
        )

    def test_commit_twice_is_idempotent(self):
        order, variant = _make_order_with_item(quantity=5)
        InventoryService.reserve_stock(order)
        InventoryService.commit_stock(order)
        stock = Stock.objects.get(variant=variant)
        self.assertEqual(stock.quantity, 95)
        self.assertEqual(stock.reserved_quantity, 0)

        second = InventoryService.commit_stock(order)
        self.assertEqual(second, [])
        stock.refresh_from_db()
        self.assertEqual(stock.quantity, 95)
        self.assertEqual(
            StockMovement.objects.filter(
                order=order,
                kind='out',
            ).count(),
            1,
        )

    def test_commit_after_release_is_noop(self):
        """Освобождённый резерв не может быть списан повторно."""
        order, variant = _make_order_with_item(quantity=5)
        InventoryService.reserve_stock(order)
        InventoryService.release_stock(order)

        movements = InventoryService.commit_stock(order)
        self.assertEqual(movements, [])
        stock = Stock.objects.get(variant=variant)
        self.assertEqual(stock.quantity, 100)  # ничего не списано
        self.assertEqual(stock.reserved_quantity, 0)
        self.assertFalse(
            StockMovement.objects.filter(
                order=order,
                kind='out',
            ).exists(),
        )

    def test_movements_are_paired(self):
        """RELEASE/OUT ссылаются на своё RESERVE (парность движений)."""
        order, variant = _make_order_with_item(quantity=5)
        InventoryService.reserve_stock(order)
        InventoryService.release_stock(order)

        reserve = StockMovement.objects.get(order=order, kind='reserve')
        release = StockMovement.objects.get(order=order, kind='release')
        self.assertEqual(release.related_movement_id, reserve.pk)


@skipUnlessDBFeature('has_select_for_update')
class InventoryConcurrencyTests(TransactionTestCase):
    """Конкурентные операции одного заказа не дают двойного применения."""

    def _run_concurrently(self, targets, join_timeout=15):
        """Потоки на собственных соединениях, барьер одновременного старта."""
        errors = []
        barrier = threading.Barrier(len(targets))

        def runner(fn):
            try:
                barrier.wait(timeout=10)
                fn()
            except Exception as exc:  # noqa: BLE001 — собираем для assertions
                errors.append(exc)
            finally:
                connections.close_all()

        threads = [
            threading.Thread(target=runner, args=(fn,), daemon=True)
            for fn in targets
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=join_timeout)
        self.assertFalse(any(t.is_alive() for t in threads), 'поток завис')
        self.assertEqual(errors, [], f'Ошибки в конкурентных потоках: {errors!r}')

    def test_concurrent_reserve_single_reservation(self):
        order, variant = _make_order_with_item(quantity=5)

        self._run_concurrently([
            lambda: InventoryService.reserve_stock(order),
            lambda: InventoryService.reserve_stock(order),
        ])

        stock = Stock.objects.get(variant=variant)
        self.assertEqual(stock.reserved_quantity, 5, 'двойное резервирование')
        self.assertEqual(
            StockMovement.objects.filter(order=order, kind='reserve').count(),
            1,
        )

    def test_concurrent_release_no_double_decrement(self):
        order, variant = _make_order_with_item(quantity=5)
        InventoryService.reserve_stock(order)

        self._run_concurrently([
            lambda: InventoryService.release_stock(order),
            lambda: InventoryService.release_stock(order),
        ])

        stock = Stock.objects.get(variant=variant)
        self.assertEqual(stock.reserved_quantity, 0)
        self.assertEqual(
            StockMovement.objects.filter(order=order, kind='release').count(),
            1,
            'двойное освобождение резерва',
        )

    def test_concurrent_commit_no_double_decrement(self):
        order, variant = _make_order_with_item(quantity=5)
        InventoryService.reserve_stock(order)

        self._run_concurrently([
            lambda: InventoryService.commit_stock(order),
            lambda: InventoryService.commit_stock(order),
        ])

        stock = Stock.objects.get(variant=variant)
        self.assertEqual(stock.quantity, 95, 'двойное списание стока')
        self.assertEqual(stock.reserved_quantity, 0)
        self.assertEqual(
            StockMovement.objects.filter(order=order, kind='out').count(),
            1,
        )

    def test_concurrent_release_and_commit(self):
        """Параллельные release и commit: применяется ровно один исход.

        (Либо освобождение, либо списание — но не оба и не дважды.)
        """
        order, variant = _make_order_with_item(quantity=5)
        InventoryService.reserve_stock(order)

        self._run_concurrently([
            lambda: InventoryService.release_stock(order),
            lambda: InventoryService.commit_stock(order),
        ])

        stock = Stock.objects.get(variant=variant)
        released = StockMovement.objects.filter(
            order=order, kind='release',
        ).count()
        committed = StockMovement.objects.filter(
            order=order, kind='out',
        ).count()
        self.assertIn((released, committed), [(1, 0), (0, 1)])
        if released:
            self.assertEqual(stock.quantity, 100)
            self.assertEqual(stock.reserved_quantity, 0)
        else:
            self.assertEqual(stock.quantity, 95)
            self.assertEqual(stock.reserved_quantity, 0)


@skipUnlessDBFeature('has_select_for_update')
class InventoryReconcileOrderTests(TransactionTestCase):
    """reconcile_order() восстанавливает недостающие операции склада."""

    def test_reconcile_delivered_without_commit(self):
        order, variant = _make_order_with_item(quantity=5)
        InventoryService.reserve_stock(order)
        # Имитация сбоя: заказ доставлен, но списание не выполнено
        # (обход FSM через прямой update — сценарий восстановления).
        Order.objects.filter(pk=order.pk).update(
            status=OrderStatus.DELIVERED,
        )

        report = InventoryService.reconcile_order(order)
        self.assertEqual(report['actions'], ['committed'])

        stock = Stock.objects.get(variant=variant)
        self.assertEqual(stock.quantity, 95)
        self.assertEqual(stock.reserved_quantity, 0)

    def test_reconcile_cancelled_without_release(self):
        order, variant = _make_order_with_item(quantity=5)
        InventoryService.reserve_stock(order)
        Order.objects.filter(pk=order.pk).update(
            status=OrderStatus.CANCELLED,
        )

        report = InventoryService.reconcile_order(order)
        self.assertEqual(report['actions'], ['released'])

        stock = Stock.objects.get(variant=variant)
        self.assertEqual(stock.reserved_quantity, 0)
        self.assertEqual(stock.quantity, 100)

    def test_reconcile_confirmed_without_reserve(self):
        order, variant = _make_order_with_item(quantity=5)
        Order.objects.filter(pk=order.pk).update(
            status=OrderStatus.CONFIRMED,
        )

        report = InventoryService.reconcile_order(order)
        self.assertEqual(report['actions'], ['reserved'])

        stock = Stock.objects.get(variant=variant)
        self.assertEqual(stock.reserved_quantity, 5)

    def test_reconcile_consistent_order_is_noop(self):
        order, variant = _make_order_with_item(quantity=5)
        Order.objects.filter(pk=order.pk).update(
            status=OrderStatus.CONFIRMED,
        )
        InventoryService.reserve_stock(order)

        report = InventoryService.reconcile_order(order)
        self.assertEqual(report['actions'], [])
        stock = Stock.objects.get(variant=variant)
        self.assertEqual(stock.reserved_quantity, 5)

    def test_reconcile_repeated_is_safe(self):
        order, variant = _make_order_with_item(quantity=5)
        Order.objects.filter(pk=order.pk).update(
            status=OrderStatus.DELIVERED,
        )
        InventoryService.reconcile_order(order)
        second = InventoryService.reconcile_order(order)
        self.assertEqual(second['actions'], [])
        stock = Stock.objects.get(variant=variant)
        self.assertEqual(stock.quantity, 95)
