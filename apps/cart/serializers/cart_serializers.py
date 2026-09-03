# ────────────────────────────────────────────────────────────────────────
# apps/cart/serializers/cart_serializers.py — сериализаторы корзины.
#
# ЧЕТЫРЕ СЕРИАЛИЗАТОРА:
#   1. AddToCartInputSerializer     — валидация POST body (добавление)
#   2. UpdateCartItemInputSerializer — валидация PATCH body (обновление)
#   3. CartItemSerializer            — сериализация позиции (output)
#   4. CartSerializer                — сериализация корзины целиком (output)
#
# ПАТТЕРН «Input / Output разделение»:
#   Input — что API принимает (запрос)
#   Output — что API отдаёт (ответ)
#   Разделение позволяет менять форматы независимо:
#   например, добавить в ответ total_quantity, не трогая input.
#
# 📖 https://www.django-rest-framework.org/api-guide/serializers/
# 📖 https://www.django-rest-framework.org/api-guide/fields/
#
# ЧТО БУДЕТ, ЕСЛИ УДАЛИТЬ ФАЙЛ:
#   • Все API endpoints корзины → ImportError (500)
# ────────────────────────────────────────────────────────────────────────

# Decimal — для сумм (избегаем float-неточностей).
# 📖 https://docs.python.org/3/library/decimal.html
from decimal import Decimal

# serializers — модуль DRF с базовыми классами.
# 📖 https://www.django-rest-framework.org/api-guide/serializers/#serializers
from rest_framework import serializers

# MAX_ITEM_QUANTITY — лимит количества (999) для max_value.
from apps.cart.constants import MAX_ITEM_QUANTITY

# Cart, CartItem — модели корзины.
from apps.cart.models import Cart, CartItem


# ==========================================================
# INPUT-СЕРИАЛИЗАТОРЫ (валидация запросов)
# ==========================================================

class AddToCartInputSerializer(serializers.Serializer):
    """
    Валидация тела POST /api/v1/cart/items/.

    ФОРМАТ ЗАПРОСА:
        {"variant_id": 42, "quantity": 2}

    ПОЧЕМУ Serializer, А НЕ ModelSerializer:
        Входные данные не мапятся 1:1 на модель CartItem:
        • variant_id — это PK варианта, а не FK-объект
        • quantity — опционально (default=1)
        ModelSerializer пытался бы создать CartItem напрямую —
        а это делает CartService.add_item().
    """

    # variant_id — обязательное поле, integer ≥ 1.
    # min_value=1 — PK не может быть 0 или отрицательным.
    variant_id = serializers.IntegerField(min_value=1)

    # quantity — опциональное, integer [1, 999].
    # default=1 — если не передан → добавить 1 штуку.
    # max_value=MAX_ITEM_QUANTITY — защита от quantity=999999.
    quantity = serializers.IntegerField(
        min_value=1,
        max_value=MAX_ITEM_QUANTITY,
        default=1,
    )


class UpdateCartItemInputSerializer(serializers.Serializer):
    """
    Валидация тела PATCH /api/v1/cart/items/{id}/.

    ФОРМАТ ЗАПРОСА:
        {"quantity": 5}

    Отличие от Add: только quantity (variant_id не меняется).
    """

    # quantity — обязательное (required=True по умолчанию для PATCH body).
    # PATCH без quantity → 400 Bad Request.
    quantity = serializers.IntegerField(
        min_value=1,
        max_value=MAX_ITEM_QUANTITY,
    )


# ==========================================================
# OUTPUT-СЕРИАЛИЗАТОРЫ (ответы API)
# ==========================================================

class CartItemSerializer(serializers.ModelSerializer):
    """
    Позиция корзины — только чтение.

    ВЫВОДИТ:
        {
            "id": 42,
            "product_name": "iPhone 15 Pro",
            "sku": "IP15P-128-BLK",
            "price": 89990.00,        // unit_price (из property модели)
            "quantity": 2,
            "total_price": 179980.00   // price × quantity
        }

    source=... — навигация по связям:
      'variant.product.name' → item.variant.product.name → SQL JOIN.
    """

    # product_name — имя товара (из variant → product → name).
    # source='variant.product.name' — тройная навигация:
    #   item.variant (FK) → variant.product (FK) → product.name (CharField)
    # 📖 https://www.django-rest-framework.org/api-guide/fields/#source
    product_name = serializers.CharField(
        source='variant.product.name',
        read_only=True,
    )
    # sku — артикул варианта (уникальный идентификатор SKU).
    sku = serializers.CharField(
        source='variant.sku',
        read_only=True,
    )

    # price — цена за единицу (из property CartItem.unit_price).
    # source='unit_price' — обращается к item.unit_price (property).
    # allow_null=True — вариант может не иметь цены (null в JSON).
    # 📖 https://www.django-rest-framework.org/api-guide/fields/#decimalfield
    price = serializers.DecimalField(
        max_digits=10,         # до 9 999 999 999.99 — хватит
        decimal_places=2,      # копейки
        source='unit_price',   # property модели CartItem.unit_price
        allow_null=True,
        read_only=True,
    )

    # total_price — стоимость позиции (price × quantity).
    # ИМЯ ПОЛЯ СОВПАДАЕТ С ИМЕНЕМ PROPERTY → source не нужен!
    # DRF автоматически обратится к obj.total_price (property).
    # 📖 https://www.django-rest-framework.org/api-guide/serializers/#field-level-validation
    total_price = serializers.DecimalField(
        max_digits=10,
        decimal_places=2,
        allow_null=True,
        read_only=True,
    )

    class Meta:
        model = CartItem
        # Строгий набор полей для API-ответа.
        fields = (
            'id',              # PK позиции
            'product_name',    # «iPhone 15 Pro» (из source)
            'sku',             # «IP15P-128-BLK» (из source)
            'price',           # цена за 1 шт. (из unit_price property)
            'quantity',        # количество
            'total_price',     # цена × количество (из property)
        )
        # Все поля read-only — API не принимает CartItem напрямую.
        # Создание/обновление — через CartService.
        read_only_fields = fields


class CartSerializer(serializers.ModelSerializer):
    """
    Корзина целиком — для API-ответа.

    ВЫВОДИТ:
        {
            "id": 1,
            "items": [...],
            "total": 179980.00,
            "total_quantity": 5
        }

    items — вложенный список CartItemSerializer (many=True).
    total / total_quantity — вычисляемые поля (SerializerMethodField).
    """

    # items — вложенный сериализатор для позиций.
    # many=True — список позиций (не одна).
    # read_only=True — позиции не создаются через этот сериализатор.
    items = CartItemSerializer(many=True, read_only=True)

    # SerializerMethodField — поле, значение которого вычисляется
    # в методе get_total(). DRF вызывает этот метод при сериализации.
    # 📖 https://www.django-rest-framework.org/api-guide/fields/#serializermethodfield
    total = serializers.SerializerMethodField()
    total_quantity = serializers.SerializerMethodField()

    class Meta:
        model = Cart
        fields = (
            'id',              # PK корзины
            'items',           # вложенный список CartItemSerializer
            'total',           # общая сумма (SerializerMethodField)
            'total_quantity',  # общее количество единиц
        )
        read_only_fields = fields

    def get_total(self, obj: Cart) -> Decimal:
        """
        Общая стоимость корзины.

        АЛГОРИТМ:
          sum(item.total_price for item in items)

        ОПТИМИЗАЦИЯ:
          Используем prefetched items (из with_items()), чтобы
          избежать лишнего SQL-запроса.
          Если prefetch не был сделан → fallback на items.all() (1 SQL).

        Decimal('0.00') — начальное значение для sum().
          (не 0! — нужен Decimal для точного сложения денег)
        item.total_price or Decimal('0.00') — если total_price = None
          (вариант без цены) → считаем как 0.
        """
        items = self._get_items(obj)
        return sum(
            (item.total_price or Decimal('0.00') for item in items),
            Decimal('0.00'),
        )

    def get_total_quantity(self, obj: Cart) -> int:
        """
        Общее количество единиц в корзине.

        ПРИМЕР: 2 позиции (×3 и ×5) → total_quantity = 8.
        """
        items = self._get_items(obj)
        return sum(item.quantity for item in items)

    @staticmethod
    def _get_items(obj: Cart):
        """
        Извлечение items для сериализации.

        КАК РАБОТАЕТ PREFETCH CACHE:
          Cart.objects.with_items().get(pk=1) →
          Django выполняет prefetch_related('items') →
          сохраняет результат в obj._prefetched_objects_cache['items'].
          obj.items.all() → берёт из кэша (БЕЗ SQL!).

          Cart.objects.get(pk=1) → НЕТ prefetch →
          obj.items.all() → делает SQL-запрос (1 запрос).

        Этот метод работает в обоих случаях:
          prefetch есть → из кэша (быстро)
          prefetch нет → SQL (медленно, но не падает)

        SQL/программные ошибки НЕ глотаются: они должны достичь
        существующего error boundary API.

        📖 https://docs.djangoproject.com/en/stable/ref/models/querysets/#prefetch-related
        """
        return obj.items.all()
