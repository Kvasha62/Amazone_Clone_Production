# ────────────────────────────────────────────────────────────────────────
# apps/pricing/services/pricing_service.py — бизнес-логика ценообразования.
#
# МЕТОДЫ:
#   set_price()                    — установить/обновить цену варианта
#   get_price()                    — получить объект цены
#   get_effective_price()          — получить эффективную цену (Decimal)
#   remove_price()                 — удалить цену варианта
#   get_price_history()            — история изменений
#   bulk_set_prices()              — массовое обновление
#   recalculate_product_bounds()   — публичный пересчёт min/max товара
#   set_variant_active()           — SERVICE: смена is_active варианта
#                                    + пересчёт границ (ARCH-001 Stage 2)
#   delete_variant()               — SERVICE: удаление варианта
#                                    + пересчёт границ (ARCH-001 Stage 2)
#
# ARCH-001 (Pricing → Catalog ownership):
#   PricingService НЕ мутирует catalog.Product напрямую.
#   `pricing` рассчитывает min_price/max_price из своих цен (Price)
#   и передаёт готовые значения в публичный контракт каталога
#   CatalogService.set_product_prices(product, min_price=..., max_price=...).
#   Проверьте: dependency graph — pricing → CatalogService → catalog.Product,
#   без обратной зависимости catalog → pricing.
#
# 📖 https://docs.djangoproject.com/en/stable/topics/db/transactions/
# 📖 https://docs.djangoproject.com/en/stable/ref/models/expressions/#f-expressions
# 📖 https://docs.djangoproject.com/en/stable/ref/models/querysets/#values-list
# ────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import logging
from decimal import Decimal

from django.db import transaction
from django.db.models import Max, Min
from django.db.models.functions import Coalesce
from rest_framework.exceptions import NotFound, ValidationError

from apps.catalog.models import Product
from apps.catalog.services.catalog_service import CatalogService
from apps.pricing.models import Price, PriceHistory

logger = logging.getLogger(__name__)


class PricingService:
    """
    Сервис для работы с ценами.

    Все mutating-методы обёрнуты в transaction.atomic.
    """

    @staticmethod
    def _locked_product(product_id) -> Product:
        """
        SELECT ... FOR UPDATE по authoritative строке Product.

        ARCH-001 Stage 2 (correction): стратегия конкурентности.
        Блокировка строки товара держится ДО КОНЦА текущей транзакции,
        поэтому весь критический участок
        «мутация price-relevant состояния → расчёт bounds → запись
        Product» сериализуется между конкурентными операциями над
        одним товаром. Вызывается ВНУТРИ transaction.atomic()
        (все authoritative paths).

        Консистентный порядок захвата (сначала Product, потом
        вариант/цена) исключает deadlock между этими путями.
        """
        return Product.objects.select_for_update().get(pk=product_id)

    @staticmethod
    @transaction.atomic
    def set_price(
        variant,
        price: Decimal,
        sale_price: Decimal | None = None,
        changed_by=None,
        reason: str = '',
    ) -> Price:
        """
        Устанавливает или обновляет цену варианта.

        АЛГОРИТМ:
          1. Валидация: price > 0, sale_price ≤ price
          2. LOCK: select_for_update по authoritative Product
          3. get_or_create — найти существующую или создать новую
          4. Если обновление → создать PriceHistory (old → new)
          5. Расчёт bounds (pricing) → CatalogService.set_product_prices

        CONCURRENCY (ARCH-001 Stage 2, correction):
          @transaction.atomic + блокировка строки Product ПОКРЫВАЕТ
          ВЕСЬ критический участок: конкурентные set_price / remove_price /
          set_variant_active / delete_variant над одним товаром
          сериализуются → последний писатель публикует bounds,
          рассчитанные по полному закоммиченному множеству цен
          (lost update невозможен).

        get_or_create — атомарная операция:
          try: get(variant=variant)
          except: create(variant=variant, defaults={...})
        Возвращает (price_obj, created: bool).

        📖 https://docs.djangoproject.com/en/stable/ref/models/querysets/#get-or-create
        """
        # ── Валидация ──
        if price <= 0:
            raise ValidationError({
                'price': 'Цена должна быть больше нуля.',
            })

        if sale_price is not None and sale_price > price:
            raise ValidationError({
                'sale_price': 'Цена со скидкой не может быть больше базовой.',
            })

        # ── LOCK: authoritative Product на весь критический участок ──
        # (мутация Price → расчёт bounds → запись Product — до COMMIT).
        product = PricingService._locked_product(variant.product_id)

        # ── Создание или обновление ──
        # get_or_create: если цена для варианта уже есть → get (created=False)
        # если нет → create с defaults (created=True)
        price_obj, created = Price.objects.get_or_create(
            variant=variant,
            defaults={
                'price': price,
                'sale_price': sale_price,
            },
        )

        if not created:
            # ── Обновление существующей записи ──
            # Сохраняем историю ДО обновления — нам нужны old values.
            PriceHistory.objects.create(
                variant=variant,
                old_price=price_obj.price,          # Текущая (старая) цена
                new_price=price,                     # Новая цена
                old_sale_price=price_obj.sale_price, # Старая скидка
                new_sale_price=sale_price,            # Новая скидка
                changed_by=changed_by,                # Кто изменил
                reason=reason,                        # Почему
            )
            # Обновляем поля цены.
            # update_fields — оптимизация: UPDATE только указанных полей.
            # updated_at — поле BaseModel, включаем обязательно.
            price_obj.price = price
            price_obj.sale_price = sale_price
            price_obj.save(update_fields=['price', 'sale_price', 'updated_at'])

        else:
            # Новая запись — истории нет (старых цен не было).
            logger.info(
                'price_created',
                extra={'variant_id': variant.pk, 'price': str(price)},
            )

        # ── Пересчёт денормализованных цен на товаре ──
        # ARCH-001 (Pricing → Catalog ownership):
        #   `pricing` САМ рассчитывает min_price/max_price из своих цен,
        #   а затем передаёт готовые значения в публичный контракт каталога.
        #   `pricing` НЕ мутирует catalog.Product и не читает его напрямую.
        PricingService.recalculate_product_bounds(product)

        return price_obj

    @staticmethod
    def get_price(variant) -> Price | None:
        """
        Возвращает объект цены варианта или None.

        variant.price — OneToOne related manager.
        DoesNotExist → если цена не задана.
        📖 https://docs.djangoproject.com/en/stable/topics/db/queries/#one-to-one-relationships
        """
        try:
            return variant.price
        except Price.DoesNotExist:
            return None

    @staticmethod
    def get_effective_price(variant) -> Decimal | None:
        """
        Возвращает эффективную цену (sale_price если есть, иначе price).
        None если цена не задана.

        ИСПОЛЬЗУЕТСЯ В:
          CartItem.unit_price → цена за единицу
          Cart total → сумма по корзине
        """
        price_obj = PricingService.get_price(variant)
        if price_obj is None:
            return None
        return price_obj.effective_price

    @staticmethod
    @transaction.atomic
    def remove_price(variant) -> None:
        """
        Удаляет цену варианта и пересчитывает товар.

        .filter(variant=variant).delete() — безопасное удаление:
          если цена есть → delete() → deleted=1 → пересчёт
          если цены нет → deleted=0 → noop

        CONCURRENCY (ARCH-001 Stage 2, correction): блокировка
        authoritative Product ДО удаления цены покрывает весь
        критический участок (удаление → расчёт → запись Product).
        """
        # ── LOCK: authoritative Product ──
        product = PricingService._locked_product(variant.product_id)

        deleted, _ = Price.objects.filter(variant=variant).delete()
        if deleted:
            # ARCH-001: `pricing` рассчитывает границы и передаёт их каталогу.
            PricingService.recalculate_product_bounds(product)

    @staticmethod
    @transaction.atomic
    def set_variant_active(variant, *, is_active: bool) -> None:
        """
        ЯВНАЯ SERVICE-координация (ARCH-001 Stage 2): смена is_active
        варианта + пересчёт price bounds товара.

        Автоматическая реакция pricing на изменение состояния варианта
        невозможна без нарушения архитектуры: она требует либо reverse
        dependency (catalog → pricing), либо cross-context Django
        signal, либо глобальный registry/event bus — все три формы
        запрещены (ARCHITECTURE.md → Cross-Domain Coordination).
        Поэтому изменение price-relevant состояния выполняется этим
        явным сервисным вызовом: видимая точка в коде, явная транзакция.

        ПОТОК (ARCH-001 Stage 2, correction — с локингом):
            LOCK:    select_for_update по authoritative Product
            мутация: CatalogService.set_variant_active (catalog-owned)
            расчёт:  PricingService._compute_price_bounds (pricing-owned)
            запись:  CatalogService.set_product_prices (единственная точка)

        ВАЖНО: прямое изменение variant.is_active в обход этого метода
        (admin / raw ORM) оставляет Product.min_price/max_price
        устаревшими до следующей операции с ценами.
        """
        # ── LOCK: authoritative Product на весь критический участок ──
        product = PricingService._locked_product(variant.product_id)

        CatalogService.set_variant_active(variant, is_active=is_active)
        PricingService.recalculate_product_bounds(product)

    @staticmethod
    @transaction.atomic
    def delete_variant(variant) -> None:
        """
        ЯВНАЯ SERVICE-координация (ARCH-001 Stage 2): удаление варианта
        + пересчёт price bounds товара (аналог set_variant_active).

        Каскадное удаление товара (Product.delete() → CASCADE вариантов)
        пересчёт НЕ запускает: товар удаляется целиком, и price-recompute
        wiring не трогает уже удаляемый Product (регрессионный тест
        test_product_cascade_delete_does_not_recompute_prices).

        CONCURRENCY (ARCH-001 Stage 2, correction): блокировка
        authoritative Product ДО удаления варианта покрывает весь
        критический участок (удаление → расчёт → запись Product).
        """
        # ── LOCK: authoritative Product ──
        product = PricingService._locked_product(variant.product_id)

        CatalogService.delete_variant(variant)
        PricingService.recalculate_product_bounds(product)

    @staticmethod
    def recalculate_product_bounds(product) -> None:
        """
        Публичный контракт: пересчитать min_price / max_price товара.

        ARCH-001 Stage 2 — единственный владелец расчёта price bounds.
        Вызывается из явных service-методов:
          • set_price() / remove_price() — после изменения цены;
          • set_variant_active() / delete_variant() — после изменения
            price-relevant состояния варианта (is_active / удаление);
          • напрямую вызывающим кодом (seed-команды).

        CONCURRENCY (ARCH-001 Stage 2, correction): захватывает
        блокировку authoritative Product (select_for_update) перед
        расчётом — весь участок «расчёт → запись» сериализован.
        Повторный захват внутри транзакции, уже удерживающей блокировку
        (set_price/remove_price/set_variant_active/delete_variant),
        безопасен: строка уже заблокирована этой же транзакцией.
        Благодаря этому даже прямой вызов (seed-команды) не может
        перезаписать bounds устаревшим расчётом поверх конкурентной
        операции — он дождётся её COMMIT.

        Поток (однонаправленный):
          pricing (расчёт из своих Price, только ACTIVE варианты)
            → CatalogService.set_product_prices() (запись)
            → catalog.Product
        """
        locked_product = PricingService._locked_product(product.pk)
        min_price, max_price = PricingService._compute_price_bounds(locked_product)
        CatalogService.set_product_prices(
            locked_product,
            min_price=min_price,
            max_price=max_price,
        )

    @staticmethod
    def _compute_price_bounds(product) -> tuple[Decimal | None, Decimal | None]:
        """
        Рассчитывает min_price / max_price для товара из его цен.

        Данные о ценах принадлежат bounded context `pricing`, поэтому
        расчёт выполняется здесь и передаётся в каталог готовым.

        АЛГОРИТМ:
          1. Берём цены активных вариантов товара (variant.is_active=True).
          2. effective_price = COALESCE(sale_price, price).
          3. min_price = MIN(effective_price), max_price = MAX(effective_price).
          4. Если цены нет → (None, None).

        ВАЖНО: только активные варианты участвуют в расчёте — неактивный
        вариант не виден в каталоге и не должен занижать/завышать цену.
        """
        bounds = (
            Price.objects
            .filter(variant__product=product, variant__is_active=True)
            .aggregate(
                min_price=Min(Coalesce('sale_price', 'price')),
                max_price=Max(Coalesce('sale_price', 'price')),
            )
        )
        min_price = bounds['min_price']
        max_price = bounds['max_price']

        logger.debug(
            'product_price_bounds_computed',
            extra={
                'product_id': product.pk,
                'min_price': str(min_price),
                'max_price': str(max_price),
            },
        )
        return min_price, max_price

    @staticmethod
    def get_price_history(variant, limit: int = 50):
        """
        Возвращает историю изменений цены варианта.
        limit=50 — защита от огромных списков (10 лет истории → тысячи записей).
        """
        return (
            PriceHistory.objects
            .filter(variant=variant)
            .order_by('-created_at')[:limit]
        )

    @staticmethod
    @transaction.atomic
    def bulk_set_prices(prices_data: list[dict], changed_by=None) -> list[Price]:
        """
        Массовое обновление цен.

        prices_data: [{'variant_id': int, 'price': Decimal, 'sale_price': ...}, ...]

        @transaction.atomic — либо ВСЕ цены обновляются, либо НИ ОДНА.
        Без: 5 из 10 обновились, 6-й variant_id не найден → частичное обновление.
        """
        from apps.catalog.models import ProductVariant

        results = []
        for item in prices_data:
            try:
                variant = ProductVariant.objects.get(pk=item['variant_id'])
            except ProductVariant.DoesNotExist:
                raise NotFound(
                    f"Вариант {item['variant_id']} не найден."
                )

            price_obj = PricingService.set_price(
                variant=variant,
                price=item['price'],
                sale_price=item.get('sale_price'),
                changed_by=changed_by,
            )
            results.append(price_obj)

        return results
