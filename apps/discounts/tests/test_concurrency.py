from decimal import Decimal
from functools import partial

from django.contrib.auth import get_user_model
from django.db import IntegrityError, connections, transaction
from django.test import TestCase, TransactionTestCase, skipUnlessDBFeature
from rest_framework.exceptions import ValidationError

from apps.core.tests.concurrency import ConcurrentJobsMixin
from apps.discounts.models import CouponUsage
from apps.discounts.tests.factories import create_test_coupon
from apps.orders.models import Order
from apps.orders.models.order import OrderStatus
from apps.orders.services.order_service import OrderService
from apps.orders.tests.factories import create_test_order, create_test_user

User = get_user_model()


@skipUnlessDBFeature('has_select_for_update')
class CouponConcurrencyTests(ConcurrentJobsMixin, TransactionTestCase):
    """Cross-connection тесты купонов.

    PROD-015: запуск идёт через bounded-раннер
    apps.core.tests.concurrency (daemon-потоки + join по ОБЩЕМУ
    дедлайну). Прежний `with ThreadPoolExecutor(...)` ограничивал
    только `future.result(timeout=30)`, а выход из контекст-менеджера
    вызывал shutdown(wait=True) и мог ждать зависший воркер
    бесконечно. Теперь зависание = детерминированный fail со стеком.
    """

    reset_sequences = True

    def _apply(self, order_id, user_id, code, barrier):
        connections.close_all()
        try:
            barrier.wait(timeout=10)
            user = User.objects.get(pk=user_id)
            order = Order.objects.get(pk=order_id)
            try:
                OrderService.apply_coupon(order, code, user=user)
                return 'ok'
            except ValidationError:
                return 'error'
        finally:
            connections.close_all()

    def _run_two(self, orders, users, code):
        jobs = [
            partial(self._apply, order.pk, user.pk, code)
            for order, user in zip(orders, users)
        ]
        return self._run_jobs(jobs)

    def test_last_global_slot_allows_exactly_one_apply(self):
        coupon = create_test_coupon(
            code='GLOBAL1',
            max_total_uses=1,
            max_uses_per_user=10,
        )
        user1 = create_test_user()
        user2 = create_test_user()
        order1 = create_test_order(user1, subtotal=Decimal('1000.00'), total=Decimal('1000.00'))
        order2 = create_test_order(user2, subtotal=Decimal('1000.00'), total=Decimal('1000.00'))

        results = self._run_two([order1, order2], [user1, user2], coupon.code)

        self.assertEqual(sorted(results), ['error', 'ok'])
        coupon.refresh_from_db()
        self.assertEqual(coupon.times_used, 1)
        self.assertEqual(CouponUsage.objects.filter(coupon=coupon).count(), 1)

    def test_per_user_limit_allows_only_one_concurrent_order(self):
        coupon = create_test_coupon(
            code='USER1',
            max_total_uses=10,
            max_uses_per_user=1,
        )
        user = create_test_user()
        order1 = create_test_order(user, subtotal=Decimal('1000.00'), total=Decimal('1000.00'))
        order2 = create_test_order(user, subtotal=Decimal('1000.00'), total=Decimal('1000.00'))

        results = self._run_two([order1, order2], [user, user], coupon.code)

        self.assertEqual(sorted(results), ['error', 'ok'])
        coupon.refresh_from_db()
        self.assertEqual(coupon.times_used, 1)
        self.assertEqual(CouponUsage.objects.filter(coupon=coupon, user=user).count(), 1)

    def test_concurrent_apply_to_same_order_is_serialized_by_order_lock(self):
        coupon = create_test_coupon(
            code='ORDER1',
            max_total_uses=10,
            max_uses_per_user=10,
        )
        user = create_test_user()
        order = create_test_order(user, subtotal=Decimal('1000.00'), total=Decimal('1000.00'))

        results = self._run_two([order, order], [user, user], coupon.code)

        self.assertEqual(sorted(results), ['error', 'ok'])
        coupon.refresh_from_db()
        self.assertEqual(coupon.times_used, 1)
        self.assertEqual(CouponUsage.objects.filter(coupon=coupon, order=order).count(), 1)

    def test_counter_matches_usage_rows_after_concurrent_apply(self):
        coupon = create_test_coupon(
            code='COUNT1',
            max_total_uses=4,
            max_uses_per_user=2,
        )
        users = [create_test_user() for _ in range(4)]
        orders = [
            create_test_order(user, subtotal=Decimal('1000.00'), total=Decimal('1000.00'))
            for user in users
        ]

        results = self._run_two(orders[:2], users[:2], coupon.code)
        self.assertEqual(sorted(results), ['ok', 'ok'])
        results = self._run_two(orders[2:], users[2:], coupon.code)
        self.assertEqual(sorted(results), ['ok', 'ok'])

        coupon.refresh_from_db()
        self.assertEqual(
            coupon.times_used,
            CouponUsage.objects.filter(coupon=coupon).count(),
        )

    # ── ARCH-002 (п.5): apply vs remove / apply vs cancel ──────────────

    def _remove(self, order_id, user_id, barrier):
        connections.close_all()
        try:
            barrier.wait(timeout=10)
            user = User.objects.get(pk=user_id)
            order = Order.objects.get(pk=order_id)
            try:
                OrderService.remove_coupon(order, user=user)
                return 'ok'
            except ValidationError:
                return 'error'
        finally:
            connections.close_all()

    def _cancel(self, order_id, user_id, barrier):
        connections.close_all()
        try:
            barrier.wait(timeout=10)
            user = User.objects.get(pk=user_id)
            order = Order.objects.get(pk=order_id)
            try:
                OrderService.cancel(order, user=user)
                return 'ok'
            except ValidationError:
                return 'error'
        finally:
            connections.close_all()

    def _run_jobs(self, jobs, timeout=30):
        """Ограниченный по времени запуск job'ов, принимающих barrier."""
        run = self.run_concurrent_jobs(jobs, timeout=timeout, pass_barrier=True)
        return run.results

    def test_apply_vs_remove_same_order_ends_consistent(self):
        """Гонка apply/remove на одном заказе сериализуется lock'ом Order.

        Допустимы оба исхода (remove выиграл → error+ok; apply выиграл →
        ok+ok), но инварианты сходятся: times_used == числу usage-строк,
        usage существует ⇔ discount > 0, total согласован с discount.
        """
        coupon = create_test_coupon(
            code='RACE1',
            max_total_uses=10,
            max_uses_per_user=10,
        )
        user = create_test_user()
        order = create_test_order(user, subtotal=Decimal('1000.00'), total=Decimal('1000.00'))

        results = self._run_jobs([
            lambda barrier: self._apply(order.pk, user.pk, coupon.code, barrier),
            lambda barrier: self._remove(order.pk, user.pk, barrier),
        ])

        self.assertIn(sorted(results), [['error', 'ok'], ['ok', 'ok']])
        order.refresh_from_db()
        coupon.refresh_from_db()
        usages = CouponUsage.objects.filter(order=order).count()
        self.assertEqual(usages, 1 if order.discount > 0 else 0)
        self.assertEqual(coupon.times_used, usages)
        expected_total = order.subtotal + order.delivery_cost - order.discount
        self.assertEqual(order.total, max(expected_total, Decimal('0.00')))

    def test_apply_vs_cancel_same_order_ends_released(self):
        """Гонка apply/cancel на одном заказе: order → CANCELLED, слот
        купона не теряется и не остаётся «висеть» — либо apply не прошёл
        (заказ уже не PENDING), либо cancel освободил usage при
        PENDING → CANCELLED."""
        coupon = create_test_coupon(
            code='RACE2',
            max_total_uses=10,
            max_uses_per_user=10,
        )
        user = create_test_user()
        order = create_test_order(user, subtotal=Decimal('1000.00'), total=Decimal('1000.00'))

        results = self._run_jobs([
            lambda barrier: self._apply(order.pk, user.pk, coupon.code, barrier),
            lambda barrier: self._cancel(order.pk, user.pk, barrier),
        ])

        self.assertIn(sorted(results), [['error', 'ok'], ['ok', 'ok']])
        order.refresh_from_db()
        coupon.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.CANCELLED)
        self.assertEqual(coupon.times_used, 0)
        self.assertEqual(CouponUsage.objects.filter(order=order).count(), 0)
        self.assertEqual(order.discount, Decimal('0.00'))
        self.assertEqual(order.total, Decimal('1000.00'))


class CouponUsageOrderUniquenessTests(TestCase):
    """ARCH-002 (п.3/п.5): БД-гарантия UNIQUE(order) на CouponUsage.

    На одном заказе максимум одна активная usage — ЛЮБОГО купона
    (прежний вариант UNIQUE(coupon, order) этого не обеспечивал).
    """

    def test_second_usage_same_order_other_coupon_raises_integrity_error(self):
        user = create_test_user()
        order = create_test_order(user, subtotal=Decimal('1000.00'), total=Decimal('1000.00'))
        coupon1 = create_test_coupon(code='UNIQ1')
        coupon2 = create_test_coupon(code='UNIQ2')
        CouponUsage.objects.create(coupon=coupon1, order=order, user=user)

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                CouponUsage.objects.create(coupon=coupon2, order=order, user=user)

        self.assertEqual(CouponUsage.objects.filter(order=order).count(), 1)

    def test_second_usage_same_order_same_coupon_raises_integrity_error(self):
        user = create_test_user()
        order = create_test_order(user, subtotal=Decimal('1000.00'), total=Decimal('1000.00'))
        coupon = create_test_coupon(code='UNIQ3')
        CouponUsage.objects.create(coupon=coupon, order=order, user=user)

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                CouponUsage.objects.create(coupon=coupon, order=order, user=user)

        self.assertEqual(CouponUsage.objects.filter(order=order).count(), 1)
