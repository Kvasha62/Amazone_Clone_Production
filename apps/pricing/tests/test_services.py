"""
Тесты PricingService.
"""
import threading
from decimal import Decimal
from unittest import mock

from django.db import connections, transaction
from django.test import TestCase, TransactionTestCase
from rest_framework.exceptions import ValidationError

from apps.catalog.constants import ProductStatus
from apps.catalog.models import Brand, Category, Product, ProductVariant
from apps.catalog.services.catalog_service import CatalogService
from apps.pricing.models import Price, PriceHistory
from apps.pricing.services.pricing_service import PricingService
from apps.pricing.tests.factories import PricingTestCase


class SetPriceTests(PricingTestCase):

    def test_set_price_creates_new(self):
        price = PricingService.set_price(
            self.variant_a, Decimal('100.00'),
        )
        self.assertEqual(price.price, Decimal('100.00'))
        self.assertIsNone(price.sale_price)

    def test_set_price_updates_existing(self):
        PricingService.set_price(self.variant_a, Decimal('100.00'))
        price = PricingService.set_price(self.variant_a, Decimal('90.00'))
        self.assertEqual(price.price, Decimal('90.00'))

    def test_set_price_with_sale(self):
        price = PricingService.set_price(
            self.variant_a, Decimal('100.00'),
            sale_price=Decimal('80.00'),
        )
        self.assertEqual(price.sale_price, Decimal('80.00'))

    def test_set_price_zero_rejected(self):
        with self.assertRaises(ValidationError):
            PricingService.set_price(self.variant_a, Decimal('0.00'))

    def test_set_price_negative_rejected(self):
        with self.assertRaises(ValidationError):
            PricingService.set_price(self.variant_a, Decimal('-10.00'))

    def test_sale_price_gt_price_rejected(self):
        with self.assertRaises(ValidationError):
            PricingService.set_price(
                self.variant_a, Decimal('50.00'),
                sale_price=Decimal('60.00'),
            )

    def test_update_creates_history(self):
        PricingService.set_price(self.variant_a, Decimal('100.00'))
        PricingService.set_price(
            self.variant_a, Decimal('90.00'),
            reason='Скидка',
        )
        history = PriceHistory.objects.filter(variant=self.variant_a)
        self.assertEqual(history.count(), 1)
        self.assertEqual(history.first().old_price, Decimal('100.00'))
        self.assertEqual(history.first().new_price, Decimal('90.00'))

    def test_first_set_no_history(self):
        """Первая установка цены не создаёт историю."""
        PricingService.set_price(self.variant_a, Decimal('100.00'))
        self.assertEqual(
            PriceHistory.objects.filter(variant=self.variant_a).count(), 0,
        )


class RecalculateProductPricesTests(PricingTestCase):

    def test_min_max_set(self):
        """min_price / max_price обновляются на Product."""
        PricingService.set_price(self.variant_a, Decimal('100.00'))
        PricingService.set_price(self.variant_b, Decimal('200.00'))

        self.product.refresh_from_db()
        self.assertEqual(self.product.min_price, Decimal('100.00'))
        self.assertEqual(self.product.max_price, Decimal('200.00'))

    def test_min_max_updated_on_change(self):
        PricingService.set_price(self.variant_a, Decimal('100.00'))
        PricingService.set_price(self.variant_b, Decimal('200.00'))

        PricingService.set_price(self.variant_a, Decimal('300.00'))
        self.product.refresh_from_db()
        self.assertEqual(self.product.min_price, Decimal('200.00'))
        self.assertEqual(self.product.max_price, Decimal('300.00'))

    def test_sale_price_affects_minimum(self):
        PricingService.set_price(
            self.variant_a, Decimal('1000.00'),
            sale_price=Decimal('700.00'),
        )
        PricingService.set_price(self.variant_b, Decimal('1500.00'))

        self.product.refresh_from_db()
        self.assertEqual(self.product.min_price, Decimal('700.00'))
        self.assertEqual(self.product.max_price, Decimal('1500.00'))

    def test_sale_price_affects_maximum(self):
        PricingService.set_price(
            self.variant_a, Decimal('1000.00'),
            sale_price=Decimal('700.00'),
        )
        PricingService.set_price(
            self.variant_b, Decimal('1500.00'),
            sale_price=Decimal('1200.00'),
        )

        self.product.refresh_from_db()
        self.assertEqual(self.product.min_price, Decimal('700.00'))
        self.assertEqual(self.product.max_price, Decimal('1200.00'))

    def test_base_price_used_without_sale_price(self):
        PricingService.set_price(self.variant_a, Decimal('1000.00'))

        self.product.refresh_from_db()
        self.assertEqual(self.product.min_price, Decimal('1000.00'))
        self.assertEqual(self.product.max_price, Decimal('1000.00'))

    def test_sale_price_change_recalculates_bounds(self):
        PricingService.set_price(
            self.variant_a, Decimal('1000.00'),
            sale_price=Decimal('900.00'),
        )
        self.product.refresh_from_db()
        self.assertEqual(self.product.min_price, Decimal('900.00'))

        PricingService.set_price(
            self.variant_a, Decimal('1000.00'),
            sale_price=Decimal('700.00'),
        )
        self.product.refresh_from_db()
        self.assertEqual(self.product.min_price, Decimal('700.00'))

    def test_min_max_none_when_no_prices(self):
        """Нет цен → min_price = max_price = None."""
        self.product.refresh_from_db()
        self.assertIsNone(self.product.min_price)
        self.assertIsNone(self.product.max_price)

    def test_min_max_excludes_inactive_variants(self):
        """Неактивные варианты не учитываются, включая sale_price."""
        PricingService.set_price(self.variant_a, Decimal('100.00'))
        PricingService.set_price(
            self.variant_inactive, Decimal('50.00'),
            sale_price=Decimal('10.00'),
        )

        self.product.refresh_from_db()
        self.assertEqual(self.product.min_price, Decimal('100.00'))
        self.assertEqual(self.product.max_price, Decimal('100.00'))

    def test_single_price(self):
        """Один вариант с ценой — min = max."""
        PricingService.set_price(self.variant_a, Decimal('150.00'))
        self.product.refresh_from_db()
        self.assertEqual(self.product.min_price, Decimal('150.00'))
        self.assertEqual(self.product.max_price, Decimal('150.00'))


class GetPriceTests(PricingTestCase):

    def test_get_price_exists(self):
        PricingService.set_price(self.variant_a, Decimal('100.00'))
        price = PricingService.get_price(self.variant_a)
        self.assertIsNotNone(price)
        self.assertEqual(price.price, Decimal('100.00'))

    def test_get_price_not_exists(self):
        price = PricingService.get_price(self.variant_a)
        self.assertIsNone(price)

    def test_get_effective_price_no_sale(self):
        PricingService.set_price(self.variant_a, Decimal('100.00'))
        self.assertEqual(
            PricingService.get_effective_price(self.variant_a),
            Decimal('100.00'),
        )

    def test_get_effective_price_with_sale(self):
        PricingService.set_price(
            self.variant_a, Decimal('100.00'),
            sale_price=Decimal('75.00'),
        )
        self.assertEqual(
            PricingService.get_effective_price(self.variant_a),
            Decimal('75.00'),
        )

    def test_get_effective_price_none(self):
        self.assertIsNone(PricingService.get_effective_price(self.variant_a))


class RemovePriceTests(PricingTestCase):

    def test_remove_price(self):
        PricingService.set_price(self.variant_a, Decimal('100.00'))
        PricingService.remove_price(self.variant_a)
        self.assertFalse(Price.objects.filter(variant=self.variant_a).exists())

    def test_remove_recalculates_product(self):
        PricingService.set_price(self.variant_a, Decimal('100.00'))
        PricingService.set_price(self.variant_b, Decimal('200.00'))
        PricingService.remove_price(self.variant_a)

        self.product.refresh_from_db()
        self.assertEqual(self.product.min_price, Decimal('200.00'))
        self.assertEqual(self.product.max_price, Decimal('200.00'))

    def test_remove_nonexistent_noop(self):
        """Удаление несуществующей цены — без ошибок."""
        PricingService.remove_price(self.variant_a)


class GetPriceHistoryTests(PricingTestCase):

    def test_history_ordered_by_created_desc(self):
        PricingService.set_price(self.variant_a, Decimal('100.00'))
        PricingService.set_price(self.variant_a, Decimal('200.00'))
        PricingService.set_price(self.variant_a, Decimal('300.00'))

        history = PricingService.get_price_history(self.variant_a)
        self.assertEqual(history.count(), 2)
        self.assertEqual(history[0].new_price, Decimal('300.00'))
        self.assertEqual(history[1].new_price, Decimal('200.00'))

    def test_history_limit(self):
        PricingService.set_price(self.variant_a, Decimal('100.00'))
        PricingService.set_price(self.variant_a, Decimal('200.00'))
        history = PricingService.get_price_history(self.variant_a, limit=1)
        self.assertEqual(history.count(), 1)


class PricingCatalogOwnershipTests(PricingTestCase):
    """
    ARCH-001 (Pricing → Catalog ownership).

    Проверяют архитектуру зависимости:
      pricing → CatalogService.set_product_prices → catalog.Product

    Без обратной зависимости catalog → pricing и без двойного пересчёта.
    """

    def test_set_price_passes_computed_bounds_to_catalog(self):
        """
        set_price САМ рассчитывает min/max и передаёт готовые значения
        в CatalogService.set_product_prices (не мутирует Product.save()).
        """
        with mock.patch.object(
            CatalogService, 'set_product_prices', return_value=self.product,
        ) as set_prices:
            PricingService.set_price(self.variant_a, Decimal('100.00'))
            set_prices.assert_called_once_with(
                self.product,
                min_price=Decimal('100.00'),
                max_price=Decimal('100.00'),
            )

    def test_remove_price_passes_computed_bounds_to_catalog(self):
        """remove_price также передаёт рассчитанные границы в каталог."""
        PricingService.set_price(self.variant_a, Decimal('100.00'))
        PricingService.set_price(self.variant_b, Decimal('200.00'))
        with mock.patch.object(
            CatalogService, 'set_product_prices', return_value=self.product,
        ) as set_prices:
            PricingService.remove_price(self.variant_a)
            set_prices.assert_called_once_with(
                self.product,
                min_price=Decimal('200.00'),
                max_price=Decimal('200.00'),
            )

    def test_set_price_recomputes_exactly_once(self):
        """set_price пересчитывает min/max ровно один раз (без сигнального дубля)."""
        with mock.patch.object(
            CatalogService, 'set_product_prices', return_value=self.product,
        ) as set_prices:
            PricingService.set_price(self.variant_a, Decimal('100.00'))
            # create → ровно 1 вызов каталога, никакого второго от signal.
            self.assertEqual(set_prices.call_count, 1)

    def test_update_price_recomputes_exactly_once(self):
        """Обновление существующей цены — тоже ровно один пересчёт."""
        PricingService.set_price(self.variant_a, Decimal('100.00'))
        with mock.patch.object(
            CatalogService, 'set_product_prices', return_value=self.product,
        ) as set_prices:
            PricingService.set_price(self.variant_a, Decimal('150.00'))
            self.assertEqual(set_prices.call_count, 1)

    def test_remove_price_recomputes_exactly_once(self):
        """remove_price пересчитывает min/max ровно один раз."""
        PricingService.set_price(self.variant_a, Decimal('100.00'))
        with mock.patch.object(
            CatalogService, 'set_product_prices', return_value=self.product,
        ) as set_prices:
            PricingService.remove_price(self.variant_a)
            set_prices.assert_called_once()

    def test_raw_orm_price_creation_does_not_recalculate(self):
        """
        Cross-domain сигналов больше нет: прямое создание Price через ORM
        (в обход PricingService) НЕ пересчитывает каталог.Product.
        Обновление min/max — ответственность PricingService / CatalogService.
        """
        Price.objects.create(variant=self.variant_a, price=Decimal('100.00'))
        self.product.refresh_from_db()
        self.assertIsNone(self.product.min_price)
        self.assertIsNone(self.product.max_price)

    def test_set_price_updates_product_via_catalog_contract(self):
        """Реальный путь: после set_price min/max на товаре корректны."""
        PricingService.set_price(self.variant_a, Decimal('100.00'))
        PricingService.set_price(self.variant_b, Decimal('200.00'))
        self.product.refresh_from_db()
        self.assertEqual(self.product.min_price, Decimal('100.00'))
        self.assertEqual(self.product.max_price, Decimal('200.00'))

    def test_only_active_variants_are_used_in_bounds(self):
        """Неактивные варианты не участвуют в расчёте (только ACTIVE)."""
        PricingService.set_price(self.variant_a, Decimal('100.00'))
        PricingService.set_price(self.variant_inactive, Decimal('10.00'))
        self.product.refresh_from_db()
        self.assertEqual(self.product.min_price, Decimal('100.00'))
        self.assertEqual(self.product.max_price, Decimal('100.00'))

    def test_no_prices_sets_none(self):
        """Отсутствие цен → min_price = max_price = None."""
        self.product.refresh_from_db()
        self.assertIsNone(self.product.min_price)
        self.assertIsNone(self.product.max_price)


class RecalculateProductBoundsTests(PricingTestCase):
    """
    ARCH-001 Stage 2: публичный контракт
    PricingService.recalculate_product_bounds().

    Единственный владелец расчёта price bounds — pricing
    (ARCHITECTURE.md → Cross-Domain Coordination: явные service-вызовы,
    без cross-context сигналов/реестров).
    """

    def _raw_price(self, variant, price):
        """Создаёт Price напрямую через ORM (в обход set_price)."""
        return Price.objects.create(variant=variant, price=price)

    def test_sets_bounds_from_active_variants(self):
        """min/max считаются из цен активных вариантов."""
        self._raw_price(self.variant_a, Decimal('100.00'))
        self._raw_price(self.variant_b, Decimal('300.00'))
        PricingService.recalculate_product_bounds(self.product)
        self.product.refresh_from_db()
        self.assertEqual(self.product.min_price, Decimal('100.00'))
        self.assertEqual(self.product.max_price, Decimal('300.00'))

    def test_excludes_inactive_variants(self):
        """Неактивные варианты не участвуют в расчёте границ."""
        self._raw_price(self.variant_a, Decimal('100.00'))
        self._raw_price(self.variant_inactive, Decimal('10.00'))
        PricingService.recalculate_product_bounds(self.product)
        self.product.refresh_from_db()
        self.assertEqual(self.product.min_price, Decimal('100.00'))
        self.assertEqual(self.product.max_price, Decimal('100.00'))

    def test_no_prices_sets_none(self):
        """Нет цен → min_price = max_price = None."""
        PricingService.recalculate_product_bounds(self.product)
        self.product.refresh_from_db()
        self.assertIsNone(self.product.min_price)
        self.assertIsNone(self.product.max_price)

    def test_writes_through_catalog_contract(self):
        """Запись идёт через CatalogService.set_product_prices (ровно 1)."""
        self._raw_price(self.variant_a, Decimal('150.00'))
        with mock.patch.object(
            CatalogService, 'set_product_prices', return_value=self.product,
        ) as set_prices:
            PricingService.recalculate_product_bounds(self.product)
        set_prices.assert_called_once_with(
            self.product,
            min_price=Decimal('150.00'),
            max_price=Decimal('150.00'),
        )


class VariantStateCoordinationTests(PricingTestCase):
    """
    ARCH-001 Stage 2 (после review): явная SERVICE-координация
    price-relevant состояния варианта.

    Автоматической реакции на ORM-события каталога нет (запрещена
    архитектурой). Изменение is_active / удаление варианта выполняется
    ТОЛЬКО через PricingService.set_variant_active / delete_variant:
    мутация — CatalogService (catalog-owned), расчёт — pricing-owned,
    запись — CatalogService.set_product_prices. Dependency: pricing → catalog.
    """

    def _third_variant(self):
        """Дополнительный активный вариант (для сценариев с 3 ценами)."""
        return ProductVariant.objects.create(
            product=self.product, sku='SKU-P3', is_active=True,
        )

    def test_deactivation_excludes_variant(self):
        """True → False: деактивированный вариант выпадает из границ."""
        PricingService.set_price(self.variant_a, Decimal('100.00'))
        PricingService.set_price(self.variant_b, Decimal('200.00'))
        PricingService.set_variant_active(self.variant_b, is_active=False)
        self.product.refresh_from_db()
        self.assertEqual(self.product.min_price, Decimal('100.00'))
        self.assertEqual(self.product.max_price, Decimal('100.00'))
        self.assertFalse(ProductVariant.objects.get(pk=self.variant_b.pk).is_active)

    def test_reactivation_includes_variant(self):
        """False → True: реактивированный вариант возвращается в границы."""
        PricingService.set_price(self.variant_a, Decimal('100.00'))
        PricingService.set_price(self.variant_b, Decimal('200.00'))
        PricingService.set_variant_active(self.variant_b, is_active=False)
        PricingService.set_variant_active(self.variant_b, is_active=True)
        self.product.refresh_from_db()
        self.assertEqual(self.product.min_price, Decimal('100.00'))
        self.assertEqual(self.product.max_price, Decimal('200.00'))

    def test_delete_variant_updates_bounds(self):
        """Удаление варианта пересчитывает границы (без него)."""
        PricingService.set_price(self.variant_a, Decimal('100.00'))
        PricingService.set_price(self.variant_b, Decimal('200.00'))
        PricingService.delete_variant(self.variant_b)
        self.product.refresh_from_db()
        self.assertEqual(self.product.min_price, Decimal('100.00'))
        self.assertEqual(self.product.max_price, Decimal('100.00'))
        self.assertFalse(ProductVariant.objects.filter(pk=self.variant_b.pk).exists())

    def test_activation_change_without_price_sets_none(self):
        """Отсутствие цены: смена is_active варианта без Price → None."""
        PricingService.set_variant_active(self.variant_a, is_active=False)
        self.product.refresh_from_db()
        self.assertIsNone(self.product.min_price)
        self.assertIsNone(self.product.max_price)

    def test_multiple_active_variants_bounds(self):
        """Несколько активных вариантов: min/max по всем активным."""
        third = self._third_variant()
        PricingService.set_price(self.variant_a, Decimal('100.00'))
        PricingService.set_price(self.variant_b, Decimal('200.00'))
        PricingService.set_price(third, Decimal('300.00'))
        self.product.refresh_from_db()
        self.assertEqual(self.product.min_price, Decimal('100.00'))
        self.assertEqual(self.product.max_price, Decimal('300.00'))
        # Деактивация среднего не ломает границы.
        PricingService.set_variant_active(self.variant_b, is_active=False)
        self.product.refresh_from_db()
        self.assertEqual(self.product.min_price, Decimal('100.00'))
        self.assertEqual(self.product.max_price, Decimal('300.00'))
        # Деактивация минимального сдвигает min.
        PricingService.set_variant_active(self.variant_a, is_active=False)
        self.product.refresh_from_db()
        self.assertEqual(self.product.min_price, Decimal('300.00'))
        self.assertEqual(self.product.max_price, Decimal('300.00'))

    def test_set_variant_active_writes_through_catalog_contract(self):
        """Запись после смены is_active — через контракт каталога (ровно 1)."""
        PricingService.set_price(self.variant_a, Decimal('100.00'))
        PricingService.set_price(self.variant_b, Decimal('200.00'))
        with mock.patch.object(
            CatalogService, 'set_product_prices', return_value=self.product,
        ) as set_prices:
            PricingService.set_variant_active(self.variant_b, is_active=False)
        set_prices.assert_called_once_with(
            self.product,
            min_price=Decimal('100.00'),
            max_price=Decimal('100.00'),
        )

    def test_delete_variant_writes_through_catalog_contract(self):
        """Запись после удаления варианта — через контракт каталога (ровно 1)."""
        PricingService.set_price(self.variant_a, Decimal('100.00'))
        PricingService.set_price(self.variant_b, Decimal('200.00'))
        with mock.patch.object(
            CatalogService, 'set_product_prices', return_value=self.product,
        ) as set_prices:
            PricingService.delete_variant(self.variant_b)
        set_prices.assert_called_once_with(
            self.product,
            min_price=Decimal('100.00'),
            max_price=Decimal('100.00'),
        )

    def test_raw_variant_save_does_not_recompute(self):
        """
        Документирование trade-off: raw ORM-мутация is_active в обход
        сервиса НЕ запускает пересчёт (его нет — см. тесты каталога).
        """
        PricingService.set_price(self.variant_a, Decimal('100.00'))
        with mock.patch.object(
            CatalogService, 'set_product_prices', return_value=self.product,
        ) as set_prices:
            self.variant_a.is_active = False
            self.variant_a.save()
        set_prices.assert_not_called()


class PriceBoundsConcurrencyTests(TransactionTestCase):
    """
    ARCH-001 Stage 2 (correction): конкурентная стратегия
    authoritative price-update paths.

    РЕАЛЬНЫЕ cross-connection тесты (TransactionTestCase на PostgreSQL):
    данные закоммичены, каждый поток работает на СВОЁМ соединении —
    блокировка authoritative Product (select_for_update) проверяется
    по-настоящему, а не последовательными вызовами.

    Гарантия: конкурентные операции над одним Product не оставляют
    Product.min_price/max_price устаревшими (lost update невозможен),
    финальные min/max всегда соответствуют полному закоммиченному
    множеству активных цен.
    """

    def setUp(self):
        self.brand = Brand.objects.create(name='ConcurrencyBrand')
        self.category = Category.add_root(name='ConcurrencyCat')
        self.product = Product.objects.create(
            name='Concurrency Product',
            brand=self.brand,
            primary_category=self.category,
            status=ProductStatus.ACTIVE,
        )
        self.variant_a = ProductVariant.objects.create(
            product=self.product, sku='CONC-A', is_active=True,
        )
        self.variant_b = ProductVariant.objects.create(
            product=self.product, sku='CONC-B', is_active=True,
        )

    def _run_concurrently(self, targets, join_timeout=15):
        """
        Запускает функции в потоках одновременно (барьер старта).
        Каждый поток получает собственное DB-соединение.

        ВАЖНО: каждый поток ЗАКРЫВАЕТ свои DB-соединения в finally.
        Без этого соединения живут до GC: они держат сессии в PostgreSQL,
        и teardown сьюта падает с ObjectInUse на DROP DATABASE
        (наблюдено в CI: «database is being accessed by other users,
        2 other sessions»).
        """
        errors = []
        barrier = threading.Barrier(len(targets))

        def runner(fn):
            try:
                barrier.wait(timeout=10)
                fn()
            except Exception as exc:  # noqa: BLE001 — собираем для assertions
                errors.append(exc)
            finally:
                # Закрываем соединения ЭТОГО потока (thread-local реестр).
                connections.close_all()

        threads = [
            threading.Thread(target=runner, args=(fn,), daemon=True)
            for fn in targets
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=join_timeout)

        self.assertEqual(errors, [], 'Ошибки в конкурентных потоках')
        for thread in threads:
            self.assertFalse(
                thread.is_alive(),
                'Поток не завершился — deadlock или потеря блокировки',
            )

    def test_service_blocks_while_product_lock_held(self):
        """
        Локинг покрывает ВЕСЬ критический участок: пока внешняя
        транзакция держит select_for_update на Product, set_price()
        конкурента БЛОКИРОВАН (не выполняет ни мутацию Price, ни запись
        bounds). После COMMIT внешний поток завершается, финальные
        bounds включают обе цены.
        """
        PricingService.set_price(self.variant_a, Decimal('100.00'))

        done = threading.Event()
        worker_errors = []

        def worker():
            try:
                PricingService.set_price(self.variant_b, Decimal('200.00'))
            except Exception as exc:  # noqa: BLE001
                worker_errors.append(exc)
            finally:
                # Соединение потока должно быть закрыто до его смерти —
                # иначе сессия держит test DB и teardown падает
                # (см. комментарий в _run_concurrently).
                connections.close_all()
                done.set()

        worker_thread = threading.Thread(target=worker, daemon=True)

        with transaction.atomic():
            # Внешняя транзакция захватывает блокировку authoritative Product.
            Product.objects.select_for_update().get(pk=self.product.pk)
            worker_thread.start()
            # Воркер НЕ должен успеть завершиться, пока lock удерживается.
            finished_while_locked = done.wait(timeout=1.5)

        self.assertFalse(
            finished_while_locked,
            'set_price НЕ заблокировался на Product row lock — '
            'критический участок не защищён (локинг не работает)',
        )
        self.assertEqual(worker_errors, [])

        # Lock отпущен (COMMIT) — воркер завершается.
        worker_thread.join(timeout=10)
        self.assertFalse(worker_thread.is_alive())

        self.product.refresh_from_db()
        self.assertEqual(self.product.min_price, Decimal('100.00'))
        self.assertEqual(self.product.max_price, Decimal('200.00'))

    def test_concurrent_set_price_no_stale_bounds(self):
        """
        Два конкурентных set_price на разных вариантах одного товара:
        финальные min/max включают ОБЕ цены (нет lost update).
        """
        self._run_concurrently([
            lambda: PricingService.set_price(self.variant_a, Decimal('100.00')),
            lambda: PricingService.set_price(self.variant_b, Decimal('200.00')),
        ])
        self.product.refresh_from_db()
        self.assertEqual(self.product.min_price, Decimal('100.00'))
        self.assertEqual(self.product.max_price, Decimal('200.00'))

    def test_concurrent_variant_deactivation_and_price_change(self):
        """
        Конкурентные set_variant_active (True→False, вариант B) и
        set_price (вариант A: 100→150). Операции коммутативны:
        при ЛЮБОЙ сериализации финальное множество активных цен —
        {A: 150} → min = max = 150. Stale-состояние невозможно.
        """
        PricingService.set_price(self.variant_a, Decimal('100.00'))
        PricingService.set_price(self.variant_b, Decimal('200.00'))

        self._run_concurrently([
            lambda: PricingService.set_variant_active(
                self.variant_b, is_active=False,
            ),
            lambda: PricingService.set_price(self.variant_a, Decimal('150.00')),
        ])

        self.product.refresh_from_db()
        self.assertEqual(self.product.min_price, Decimal('150.00'))
        self.assertEqual(self.product.max_price, Decimal('150.00'))
        self.assertFalse(
            ProductVariant.objects.get(pk=self.variant_b.pk).is_active,
        )

    def test_concurrent_remove_price_and_set_price(self):
        """
        Конкурентные remove_price (вариант A) и set_price (вариант B,
        200→50). Коммутативны: финальное множество активных цен —
        {B: 50} → min = max = 50 при любом порядке.
        """
        PricingService.set_price(self.variant_a, Decimal('100.00'))
        PricingService.set_price(self.variant_b, Decimal('200.00'))

        self._run_concurrently([
            lambda: PricingService.remove_price(self.variant_a),
            lambda: PricingService.set_price(self.variant_b, Decimal('50.00')),
        ])

        self.product.refresh_from_db()
        self.assertEqual(self.product.min_price, Decimal('50.00'))
        self.assertEqual(self.product.max_price, Decimal('50.00'))
        self.assertFalse(
            Price.objects.filter(variant=self.variant_a).exists(),
        )
