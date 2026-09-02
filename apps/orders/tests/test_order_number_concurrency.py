# ────────────────────────────────────────────────────────────────────────
# apps/orders/tests/test_order_number_concurrency.py
#
# F-13 / PROD-010 — конкурентная генерация номера заказа.
#
# Эти тесты — РЕАЛЬНЫЕ cross-connection тесты на PostgreSQL:
#   • TransactionTestCase (данные коммитятся и видны из других сессий);
#   • threading.Barrier — потоки стартуют одновременно, а не по очереди;
#   • каждый поток работает на СОБСТВЕННОМ DB-соединении и закрывает его
#     в finally (приём из apps/reviews/tests/test_concurrency.py и
#     apps/discounts/tests/test_concurrency.py — иначе сессии держат
#     test DB и teardown падает с «database is being accessed by other
#     users»).
#
# ГАРАНТИЯ, КОТОРУЮ ЗАЩИЩАЮТ ТЕСТЫ:
#   Номер заказа выдаёт PostgreSQL SEQUENCE — nextval('orders_order_
#   number_seq'), атомарно на стороне БД, без чтения MAX() в приложении.
#   Поэтому параллельные оформления заказа не могут получить одинаковый
#   order_number, и каждое из них завершается успешно.
#
# РЕГРЕССИЯ, КОТОРУЮ ТЕСТЫ ЛОВЯТ (проверено на прежнем коде):
#   Схема «SELECT MAX(_order_number_seq) → +1 → INSERT» выдавала двум
#   параллельным транзакциям один номер: второй INSERT падал на
#   UNIQUE(order_number) («duplicate key value ... Key (order_number)=
#   (ORD-000004) already exists»), а повтор внутри той же транзакции —
#   на aborted transaction. Симптом гонки: конкурентный checkout не
#   создаёт заказ, а завершается ошибкой.
#
# Конкурентный checkout моделируется разными пользователями: по
# бизнес-правилу unique_active_user_cart у пользователя одна активная
# корзина, поэтому «N параллельных оформлений» — это N покупателей.
# ────────────────────────────────────────────────────────────────────────

import re
import threading
from decimal import Decimal
from threading import Barrier
from unittest import skipUnless

from django.db import connection, connections, transaction
from django.test import TransactionTestCase

from rest_framework.exceptions import ValidationError

from apps.cart.models import Cart, CartItem
from apps.catalog.constants import ProductStatus
from apps.catalog.models import Brand, Category, Product, ProductVariant
from apps.orders.models import Order
from apps.orders.services.order_service import OrderService
from apps.orders.tests.factories import (
    create_test_address,
    create_test_order,
    create_test_user,
)
from apps.pricing.models import Price

# Уникальность номера обеспечивается механизмом PostgreSQL (SEQUENCE).
# На других бэкендах проект работает в dev-режиме без гарантий
# конкурентности, поэтому — как и остальные concurrency-тесты
# репозитория — эти тесты требуют PostgreSQL.
requires_postgresql = skipUnless(
    connection.vendor == 'postgresql',
    'Конкурентная выдача номеров заказов гарантируется PostgreSQL SEQUENCE.',
)

# Формат публичного контракта номера заказа: ORD-000001 (6 zero-padded цифр).
ORDER_NUMBER_RE = re.compile(r'^ORD-\d{6}$')

CONCURRENT_CHECKOUTS = 8


class OrderNumberAllocationTestCase(TransactionTestCase):
    """Общая инфраструктура: товар с ценой, покупатели с корзинами."""

    UNIT_PRICE = Decimal('1000.00')
    QUANTITY = 2
    # subtotal корзины = 1000.00 × 2 = 2000.00 (выше MIN_ORDER_TOTAL).
    SUBTOTAL = Decimal('2000.00')

    def setUp(self):
        brand = Brand.objects.create(name='OrderNumberBrand')
        category = Category.add_root(name='OrderNumberCat')
        product = Product.objects.create(
            name='Order Number Product',
            brand=brand,
            primary_category=category,
            status=ProductStatus.ACTIVE,
        )
        self.variant = ProductVariant.objects.create(
            product=product,
            sku='ORDER-NUMBER-SKU',
        )
        Price.objects.create(variant=self.variant, price=self.UNIT_PRICE)

    # ──────────────────────────────────────────────────────────────
    # Инфраструктура параллельного запуска
    # ──────────────────────────────────────────────────────────────

    def _make_buyer(self):
        """
        Покупатель с адресом доставки и активной корзиной.

        Отдельный пользователь на поток — следствие бизнес-правила
        unique_active_user_cart (одна активная корзина на пользователя).
        """
        user = create_test_user()
        create_test_address(user, city='Москва')
        cart = Cart.objects.create(user=user, is_active=True)
        CartItem.objects.create(
            cart=cart,
            variant=self.variant,
            quantity=self.QUANTITY,
        )
        return user, cart

    def _run_jobs(self, jobs, timeout=60):
        """
        Запускает функции одновременно (барьер старта).

        Каждый поток закрывает свои соединения в finally. Исключения не
        пробрасываются сразу, а собираются: тест сравнивает их со
        списком [], поэтому виден полный масштаб гонки, а не первое
        исключение, и сообщение об ошибке читается как диагностика.
        """
        barrier = Barrier(len(jobs))
        results: list = []
        errors: list = []
        lock = threading.Lock()

        def runner(fn):
            try:
                connections.close_all()
                barrier.wait(timeout=30)
                result = fn()
            except BaseException as exc:  # noqa: BLE001 — собираем для assert
                with lock:
                    errors.append(exc)
            else:
                with lock:
                    results.append(result)
            finally:
                connections.close_all()

        threads = [
            threading.Thread(target=runner, args=(fn,), daemon=True)
            for fn in jobs
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=timeout)

        self.assertEqual(
            [thread.name for thread in threads if thread.is_alive()],
            [],
            'Потоки не завершили конкурентную работу за отведённое время.',
        )
        return results, errors

    def assertValidOrderNumbers(self, order_numbers):
        """Контракт номера: формат, уникальность, соответствие seq."""
        self.assertEqual(
            len(set(order_numbers)),
            len(order_numbers),
            f'Дубликаты номеров заказов: {sorted(order_numbers)}',
        )
        for number in order_numbers:
            self.assertRegex(number, ORDER_NUMBER_RE)


@requires_postgresql
class ConcurrentOrderNumberTests(OrderNumberAllocationTestCase):
    """F-13: параллельное создание заказов не даёт одинаковых номеров."""

    def test_concurrent_checkout_creates_orders_with_unique_numbers(self):
        """
        N покупателей оформляют заказ одновременно (production-путь
        OrderService.create_from_cart): все оформления успешны, номера
        уникальны и соответствуют формату.
        """
        buyers = [self._make_buyer() for _ in range(CONCURRENT_CHECKOUTS)]

        results, errors = self._run_jobs([
            (
                lambda user=user, cart=cart: OrderService.create_from_cart(
                    user=user,
                    cart=cart,
                ).order_number
            )
            for user, cart in buyers
        ])

        self.assertEqual(
            errors,
            [],
            'Конкурентное оформление заказа завершилось ошибкой — гонка за '
            f'номер заказа не устранена: {errors!r}',
        )
        self.assertEqual(len(results), CONCURRENT_CHECKOUTS)
        self.assertValidOrderNumbers(results)

        # DB-инвариант: заказы реально созданы, номера не повторяются,
        # суммы посчитаны (заказ создан полностью, а не частично).
        orders = list(Order.objects.all())
        self.assertEqual(len(orders), CONCURRENT_CHECKOUTS)
        self.assertValidOrderNumbers([order.order_number for order in orders])
        for order in orders:
            self.assertEqual(order.subtotal, self.SUBTOTAL)
            self.assertEqual(order.total, self.SUBTOTAL)
            self.assertEqual(order.items.count(), 1)
            self.assertEqual(
                int(order.order_number.split('-')[1]),
                order._order_number_seq,
            )

        # Каждая корзина закрыта ровно один раз.
        self.assertEqual(
            Cart.objects.filter(is_active=False).count(),
            CONCURRENT_CHECKOUTS,
        )

    def test_concurrent_order_creation_via_model_save_is_unique(self):
        """
        Прямое создание Order (admin add, management commands, фабрики)
        идёт через Order.save() — тот же механизм выдачи номера.
        """
        user = create_test_user()

        results, errors = self._run_jobs([
            lambda: create_test_order(user).order_number
            for _ in range(CONCURRENT_CHECKOUTS)
        ])

        self.assertEqual(
            errors,
            [],
            f'Параллельное создание Order завершилось ошибкой: {errors!r}',
        )
        self.assertEqual(len(results), CONCURRENT_CHECKOUTS)
        self.assertValidOrderNumbers(results)
        self.assertEqual(Order.objects.filter(user=user).count(),
                         CONCURRENT_CHECKOUTS)

    def test_failing_concurrent_checkout_does_not_corrupt_other_orders(self):
        """
        Ошибка одного из параллельных оформлений (пустая корзина) не ломает
        остальные: успешные заказы получают уникальные номера, упавшее
        оформление не оставляет частично созданных строк.
        """
        buyers = [self._make_buyer() for _ in range(CONCURRENT_CHECKOUTS)]
        failing_user, failing_cart = buyers[0]
        CartItem.objects.filter(cart=failing_cart).delete()

        results, errors = self._run_jobs([
            (
                lambda user=user, cart=cart: OrderService.create_from_cart(
                    user=user,
                    cart=cart,
                ).order_number
            )
            for user, cart in buyers
        ])

        self.assertEqual(len(errors), 1, f'Ожидалась одна ошибка: {errors!r}')
        self.assertIsInstance(errors[0], ValidationError)
        self.assertEqual(len(results), CONCURRENT_CHECKOUTS - 1)
        self.assertValidOrderNumbers(results)

        self.assertEqual(Order.objects.count(), CONCURRENT_CHECKOUTS - 1)
        self.assertEqual(
            Order.objects.filter(user=failing_user).count(),
            0,
            'Упавшее оформление не должно оставлять заказ в БД.',
        )
        failing_cart.refresh_from_db()
        self.assertTrue(
            failing_cart.is_active,
            'Корзина упавшего оформления должна остаться активной.',
        )

    def test_concurrent_allocation_yields_each_sequence_value_once(self):
        """
        Сам механизм выдачи номера под нагрузкой: каждый вызов получает
        собственное значение SEQUENCE — без повторов и без пропусков.
        """
        from apps.orders.models.order import allocate_order_number

        per_thread = 25
        total = CONCURRENT_CHECKOUTS * per_thread

        def worker():
            return [allocate_order_number()[1] for _ in range(per_thread)]

        results, errors = self._run_jobs([worker] * CONCURRENT_CHECKOUTS)

        self.assertEqual(
            errors,
            [],
            f'Выдача номера заказа завершилась ошибкой: {errors!r}',
        )
        numbers = [number for chunk in results for number in chunk]
        self.assertEqual(len(numbers), total)
        self.assertValidOrderNumbers(numbers)

        sequences = sorted(int(number.split('-')[1]) for number in numbers)
        self.assertEqual(
            sequences,
            list(range(sequences[0], sequences[0] + total)),
            'nextval() обязан выдавать каждое значение ровно один раз.',
        )


@requires_postgresql
class OrderNumberRollbackSemanticsTests(OrderNumberAllocationTestCase):
    """F-13: откат транзакции и ошибки создания не ломают выдачу номеров."""

    def test_failed_checkout_rolls_back_and_does_not_break_allocation(self):
        """
        Неуспешное оформление (сумма ниже MIN_ORDER_TOTAL) откатывается
        целиком, не оставляет частично созданный заказ, не закрывает
        корзину и не ломает последующие оформления.
        """
        user, cart = self._make_buyer()
        cheap_variant = ProductVariant.objects.create(
            product=self.variant.product,
            sku='ORDER-NUMBER-CHEAP-SKU',
        )
        Price.objects.create(variant=cheap_variant, price=Decimal('0.10'))
        # Единственная позиция корзины — дешёвая: total < MIN_ORDER_TOTAL.
        CartItem.objects.filter(cart=cart).delete()
        CartItem.objects.create(cart=cart, variant=cheap_variant, quantity=1)

        with self.assertRaises(ValidationError):
            OrderService.create_from_cart(user=user, cart=cart)

        self.assertEqual(
            Order.objects.filter(user=user).count(),
            0,
            'Откат должен удалить заказ целиком (без частично созданных строк).',
        )
        cart.refresh_from_db()
        self.assertTrue(
            cart.is_active,
            'Корзина не должна закрываться при неуспешном оформлении.',
        )

        # Следующее оформление (другой покупатель) работает штатно.
        next_user, next_cart = self._make_buyer()
        order = OrderService.create_from_cart(user=next_user, cart=next_cart)
        self.assertRegex(order.order_number, ORDER_NUMBER_RE)
        self.assertEqual(
            int(order.order_number.split('-')[1]),
            order._order_number_seq,
        )
        self.assertEqual(order.total, self.SUBTOTAL)

    def test_rolled_back_order_consumes_sequence_without_duplicates(self):
        """
        Документированная семантика SEQUENCE: откат транзакции расходует
        значение (номер не переиспользуется → возможен gap), уникальность
        при этом сохраняется. Gapless-нумерация архитектурой не требуется.
        """
        first = create_test_order(create_test_user())
        consumed_seq = first._order_number_seq

        doomed = None
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                doomed = create_test_order(create_test_user())
                raise RuntimeError('rollback')

        self.assertIsNotNone(doomed)
        self.assertFalse(
            Order.objects.filter(pk=doomed.pk).exists(),
            'Откатанного заказа не должно быть в БД.',
        )

        second = create_test_order(create_test_user())
        self.assertGreater(
            second._order_number_seq,
            consumed_seq,
            'Значение SEQUENCE не переиспользуется после отката.',
        )
        self.assertValidOrderNumbers([first.order_number, second.order_number])

    def test_order_number_sequence_exists_after_migrations(self):
        """Migration создаёт SEQUENCE — механизм доступен в схеме БД."""
        with connection.cursor() as cursor:
            cursor.execute("SELECT to_regclass('orders_order_number_seq')::text")
            row = cursor.fetchone()
        self.assertIsNotNone(
            row[0],
            'SEQUENCE orders_order_number_seq отсутствует в схеме БД.',
        )

    def test_explicit_order_number_is_preserved(self):
        """Явно заданный номер не перезаписывается механизмом выдачи."""
        order = create_test_order(create_test_user(), order_number='ORD-999999')
        self.assertEqual(order.order_number, 'ORD-999999')

        order.notes = 'обновление без перевыдачи номера'
        order.save()
        order.refresh_from_db()
        self.assertEqual(order.order_number, 'ORD-999999')
