# ────────────────────────────────────────────────────────────────────────
# apps/orders/serializers/order_serializers.py — сериализаторы заказов.
#
# ШЕСТЬ СЕРИАЛИЗАТОРОВ:
#   1. CreateOrderInputSerializer    — валидация POST body (создание)
#   2. OrderStatusTransitionSerializer — валидация PATCH body (статус)
#   3. CancelOrderInputSerializer    — валидация POST body (отмена)
#   4. OrderItemSerializer           — сериализация позиции (output)
#   5. OrderListSerializer           — краткий заказ для списка (output)
#   6. OrderSerializer               — полный заказ (output)
#
# ПАТТЕРН «Input / Output разделение»:
#   Input — что API принимает (запрос)
#   Output — что API отдаёт (ответ)
#   Разделение позволяет менять форматы независимо:
#   например, добавить в ответ total_quantity, не трогая input.
#
# ПАТТЕРН «List vs Detail»:
#   OrderListSerializer — краткий (без items) для списка заказов
#   OrderSerializer — полный (с items) для детальной страницы
#   Зачем: список из 100 заказов × 10 позиций = 1000 объектов → медленно.
#
# 📖 https://www.django-rest-framework.org/api-guide/serializers/
# 📖 https://www.django-rest-framework.org/api-guide/fields/
#
# ЧТО БУДЕТ, ЕСЛИ УДАЛИТЬ ФАЙЛ:
#   • Все API endpoints заказов → ImportError (500)
# ────────────────────────────────────────────────────────────────────────

from collections.abc import Mapping

from rest_framework import serializers

from apps.orders.models import Order, OrderItem
from apps.orders.models.order import OrderStatus


# ==============================================================
# INPUT-СЕРИАЛИЗАТОРЫ (валидация запросов)
# ==============================================================

def _mapping_payloads(initial_data):
    """Возвращает mapping-payload'ы из ``Serializer.initial_data``.

    ФОРМА ``request.data`` ЗАВИСИТ ОТ ПАРСЕРА DRF:
      • JSONRenderer/JSONParser                → обычный ``dict``
      • FormParser (application/x-www-form-urlencoded) → ``QueryDict``
      • MultiPartParser (multipart/form-data)  → ``QueryDict``

    Поэтому проверка идёт по ``collections.abc.Mapping``, а не по
    конкретному ``dict``: так она одинаково работает для JSON dict,
    Django ``QueryDict`` и любого другого mapping-like объекта, который
    может отдать парсер.

    Немappping-вход (строка, число, список) возвращает пустой кортеж —
    ложных срабатываний нет; такой вход DRF отклоняет ещё до ``validate()``
    в ``to_internal_value()`` («Invalid data. Expected a dictionary…»),
    поэтому до расчёта заказа он не доходит в любом случае.
    """
    if isinstance(initial_data, Mapping):
        return (initial_data,)
    return ()


class CreateOrderInputSerializer(serializers.Serializer):
    """
    Валидация тела POST /api/v1/orders/.

    ФОРМАТ ЗАПРОСА:
        {
            "notes": "Позвонить перед доставкой"  // опционально
        }

    ЦЕНА ДОСТАВКИ — СЕРВЕРНАЯ (F-08 / PROD-006):
        ``delivery_cost`` НЕ является полем запроса и не участвует в расчёте
        заказа. Авторитетную стоимость доставки считает сервер
        (``ShippingService.calculate_order_delivery_cost`` →
        ``OrderService.create_from_cart``) из адреса доставки, суммы заказа
        и тарифов ``ShippingMethod``. Явно переданное значение отклоняется
        с 400 — см. ``validate()``.

    ПОЧЕМУ Serializer, А НЕ ModelSerializer:
        Входные данные не мапятся 1:1 на модель Order:
        • notes — опционально
        ModelSerializer попытался бы создать Order напрямую —
        а это делает OrderService.create_from_cart().
    """

    # notes — комментарий к заказу. Опционально.
    # max_length=1000 — защита от огромных текстов.
    notes = serializers.CharField(
        max_length=1000,
        required=False,
        default='',
        allow_blank=True,
    )

    def validate(self, attrs):
        """F-08: явный ``delivery_cost`` в теле запроса отклоняется.

        Стоимость доставки — денежное бизнес-правило, поэтому она не
        принимается от клиента. По умолчанию DRF молча игнорирует
        неизвестные поля; здесь неизвестное денежное поле отклоняется явно,
        чтобы подделка цены доставки была видна клиенту как ошибка,
        а не как «принятый» запрос.

        Контракт должен работать для ЛЮБОГО поддерживаемого формата тела:
        JSON (``dict``), form-encoded и multipart (``QueryDict``) —
        см. ``_mapping_payloads()``.
        """
        for payload in _mapping_payloads(self.initial_data):
            if 'delivery_cost' in payload:
                raise serializers.ValidationError({
                    'delivery_cost': (
                        'Стоимость доставки рассчитывается на сервере и не '
                        'принимается от клиента. Удалите поле delivery_cost.'
                    ),
                })
        return attrs


class OrderStatusTransitionSerializer(serializers.Serializer):
    """
    Валидация тела PATCH /api/v1/orders/{id}/status/.

    ФОРМАТ ЗАПРОСА:
        {"status": "confirmed"}

    Только для staff/admin — обычные пользователи не могут менять статус.
    Валидация переходов выполняется в OrderService (FSM).
    """

    # status — новый статус заказа.
    #choice_field — валидирует что значение в OrderStatus.values.
    status = serializers.ChoiceField(
        choices=OrderStatus.choices,
    )


class CancelOrderInputSerializer(serializers.Serializer):
    """
    Валидация тела POST /api/v1/orders/{id}/cancel/.

    ФОРМАТ ЗАПРОСА:
        {"reason": "changed_mind"}

    Причина отмены — опциональна, но рекомендуется для аналитики.
    """

    # reason — причина отмены (из CANCELLATION_REASONS).
    # required=False — пользователь может отменить без указания причины.
    reason = serializers.CharField(
        required=False,
        default='',
        allow_blank=True,
        max_length=30,
    )


# ==============================================================
# OUTPUT-СЕРИАЛИЗАТОРЫ (ответы API)
# ==============================================================

class OrderItemSerializer(serializers.ModelSerializer):
    """
    Позиция заказа — только чтение (output).

    ВЫВОДИТ:
        {
            "id": 42,
            "product_name": "iPhone 15 Pro",
            "sku": "IP15P-128-BLK",
            "unit_price": "89990.00",
            "quantity": 2,
            "total_price": "179980.00"
        }

    total_price — computed property модели (unit_price × quantity).
    variant_id — для ссылки на товар (может быть null если удалён).
    """

    # total_price — из property OrderItem.total_price.
    # source не нужен — имя совпадает с property.
    total_price = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        read_only=True,
    )

    # variant_id — PK варианта товара (может быть null при удалении).
    # source НЕ нужен — имя поля совпадает с именем атрибута модели.
    # DRF 3.17+ выбрасывает AssertionError при source=field_name.
    variant_id = serializers.IntegerField(
        read_only=True,
        allow_null=True,
    )

    class Meta:
        model = OrderItem
        fields = (
            'id',
            'variant_id',
            'product_name',
            'sku',
            'unit_price',
            'quantity',
            'total_price',
        )
        read_only_fields = fields  # все поля только для чтения


class OrderListSerializer(serializers.ModelSerializer):
    """
    Краткая информация о заказе — для списка заказов.

    БЕЗ позиций (items) — для быстрой загрузки списка.
    Содержит: номер, статус, сумму, дату, количество позиций.

    ВЫВОДИТ:
        {
            "id": 1,
            "order_number": "ORD-000001",
            "status": "pending",
            "status_display": "Ожидает оплаты",
            "total": "179980.00",
            "items_count": 3,
            "created_at": "2025-01-15T10:30:00Z"
        }

    ПОЧЕМУ items_count А НЕ items:
        items_count = Annotation (COUNT) → 1 число.
        items = Prefetch → N объектов.
        Для списка: 1 число быстрее чем N объектов.
    """

    # status_display — человекочитаемый статус.
    # get_FOO_display() — метод Django для TextChoices.
    # 📖 https://docs.djangoproject.com/en/stable/ref/models/instances/#django.db.models.Model.get_FOO_display
    status_display = serializers.CharField(
        source='get_status_display',
        read_only=True,
    )

    # items_count — количество позиций в заказе.
    # Аннотируется во View через .annotate(items_count=Count('items')).
    # Если не аннотировано → None → показываем 0.
    items_count = serializers.IntegerField(
        read_only=True,
        default=0,
    )

    class Meta:
        model = Order
        fields = (
            'id',
            'order_number',
            'status',
            'status_display',
            'total',
            'items_count',
            'created_at',
        )
        read_only_fields = fields


class OrderSerializer(serializers.ModelSerializer):
    """
    Полная информация о заказе — для детальной страницы.

    Включает позиции (items), полный адрес, все суммы и таймстампы.

    ВЫВОДИТ:
        {
            "id": 1,
            "order_number": "ORD-000001",
            "status": "pending",
            "status_display": "Ожидает оплаты",
            "items": [...],
            "subtotal": "179980.00",
            "delivery_cost": "300.00",
            "discount": "0.00",
            "total": "180280.00",
            "recipient_name": "Иван Иванов",
            "full_address": "Россия, Москва, ул. Тестовая, д. 1",
            "notes": "",
            "cancellation_reason": "",
            "cancelled_at": null,
            "confirmed_at": null,
            "delivered_at": null,
            "created_at": "2025-01-15T10:30:00Z"
        }

    📖 https://www.django-rest-framework.org/api-guide/serializers/#modelserializer
    """

    items = OrderItemSerializer(many=True, read_only=True)
    status_display = serializers.CharField(
        source='get_status_display',
        read_only=True,
    )
    full_address = serializers.CharField(read_only=True)
    is_terminal = serializers.BooleanField(read_only=True)

    class Meta:
        model = Order
        fields = (
            'id',
            'order_number',
            'status',
            'status_display',
            'is_terminal',
            'items',
            'subtotal',
            'delivery_cost',
            'discount',
            'total',
            'recipient_name',
            'full_address',
            'notes',
            'cancellation_reason',
            'cancelled_at',
            'confirmed_at',
            'delivered_at',
            'created_at',
        )
        read_only_fields = fields
