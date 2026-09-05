# ────────────────────────────────────────────────────────────────────────
# apps/shipping/serializers/shipping_serializers.py — сериализаторы доставки.
#
# Сериализаторы для:
#   • ShippingZone — зоны доставки
#   • ShippingMethod — способы доставки
#   • Shipment — отправления
#   • Запросы расчёта стоимости
#   • Обновление трек-номера / статуса
#
# 📖 https://www.django-rest-framework.org/api-guide/serializers/
# ────────────────────────────────────────────────────────────────────────

from decimal import Decimal

from rest_framework import serializers

from apps.core.identifiers import OrderReferenceSerializerMixin
from apps.shipping.models import Shipment, ShippingMethod, ShippingZone


# ================================================================
# ShippingZone
# ================================================================

class ShippingZoneSerializer(serializers.ModelSerializer):
    """Полный сериализатор зоны доставки."""

    class Meta:
        model = ShippingZone
        fields = (
            'id', 'name', 'zone_code', 'regions',
            'is_active', 'created_at', 'updated_at',
        )
        read_only_fields = ('id', 'created_at', 'updated_at')


# ================================================================
# ShippingMethod
# ================================================================

class ShippingMethodSerializer(serializers.ModelSerializer):
    """Полный сериализатор способа доставки."""

    zone_name = serializers.CharField(
        source='zone.name',
        read_only=True,
    )
    estimated_days_display = serializers.CharField(read_only=True)

    class Meta:
        model = ShippingMethod
        fields = (
            'id', 'name', 'shipping_type', 'zone', 'zone_name',
            'base_price', 'price_per_kg', 'free_shipping_threshold',
            'max_shipping_cost', 'estimated_days_min', 'estimated_days_max',
            'estimated_days_display', 'max_weight_kg', 'pickup_address',
            'is_active', 'sort_order', 'created_at', 'updated_at',
        )
        read_only_fields = ('id', 'created_at', 'updated_at')


class ShippingMethodListSerializer(serializers.ModelSerializer):
    """
    Лёгкий сериализатор для списка способов доставки.
    Включает зону и сроки, но без весовых ограничений.
    """

    zone_name = serializers.CharField(
        source='zone.name',
        read_only=True,
    )
    estimated_days_display = serializers.CharField(read_only=True)

    class Meta:
        model = ShippingMethod
        fields = (
            'id', 'name', 'shipping_type', 'zone_name',
            'base_price', 'price_per_kg', 'free_shipping_threshold',
            'estimated_days_display', 'is_active',
        )


# ================================================================
# Запрос расчёта стоимости
# ================================================================

class ShippingCostRequestSerializer(serializers.Serializer):
    """
    Запрос расчёта стоимости доставки.

    Поля zone_code / region — альтернативные способы указания зоны.
    Передаём хотя бы одно из них.
    """

    zone_code = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text='Код зоны доставки (msk, central).',
    )
    region = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text='Название региона для автоопределения зоны.',
    )
    order_total = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        required=True,
        help_text='Сумма заказа для проверки бесплатной доставки.',
    )
    shipping_type = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text='Фильтр по типу доставки (courier, pickup, post, express).',
    )
    weight_kg = serializers.DecimalField(
        max_digits=8,
        decimal_places=3,
        required=False,
        allow_null=True,
        help_text='Вес заказа в кг.',
    )

    def validate(self, data):
        if not data.get('zone_code') and not data.get('region'):
            raise serializers.ValidationError(
                'Укажите zone_code или region для определения зоны.'
            )
        return data


class ShippingCostMethodSerializer(serializers.Serializer):
    """Способ доставки с рассчитанной стоимостью."""

    method_id = serializers.IntegerField()
    method_name = serializers.CharField()
    shipping_type = serializers.CharField()
    cost = serializers.DecimalField(max_digits=10, decimal_places=2)
    estimated_days_display = serializers.CharField()
    is_free = serializers.BooleanField()


class ShippingCostResponseSerializer(serializers.Serializer):
    """Ответ с расчётом стоимости доставки для всех способов."""

    zone = ShippingZoneSerializer(allow_null=True)
    methods = ShippingCostMethodSerializer(many=True)


# ================================================================
# Shipment
# ================================================================

class ShipmentCreateSerializer(OrderReferenceSerializerMixin):
    """Запрос создания отправления.

    ССЫЛКА НА ЗАКАЗ (F-8, issue #73):
      order_number (``ORD-000001``) — канонический публичный идентификатор;
      order_id — устаревший целочисленный PK (принимается, deprecated).
    """
    method_id = serializers.IntegerField(
        help_text='ID способа доставки.',
    )
    weight_kg = serializers.DecimalField(
        max_digits=8,
        decimal_places=3,
        required=False,
        allow_null=True,
    )
    notes = serializers.CharField(
        required=False,
        allow_blank=True,
    )


class ShipmentListSerializer(serializers.ModelSerializer):
    """Сериализатор списка отправлений."""

    order_number = serializers.CharField(
        source='order.order_number',
        read_only=True,
    )
    method_name = serializers.CharField(
        source='method.name',
        read_only=True,
    )
    status_display = serializers.CharField(
        source='get_status_display',
        read_only=True,
    )

    class Meta:
        model = Shipment
        fields = (
            # F-8 (#73): 'internal_tracking' (SHP-00000001) — публичный
            # идентификатор отправления. 'id' (целочисленный PK) остаётся в
            # payload на окно совместимости и помечен как deprecated.
            'id', 'internal_tracking', 'tracking_number',
            'order_number', 'method_name', 'status',
            'status_display', 'shipping_cost',
            'shipped_at', 'delivered_at', 'created_at',
        )


class ShipmentDetailSerializer(ShipmentListSerializer):
    """Детальный сериализатор отправления."""

    user_email = serializers.CharField(
        source='user.email',
        read_only=True,
    )
    shipping_type = serializers.CharField(
        source='method.shipping_type',
        read_only=True,
    )
    zone_name = serializers.CharField(
        source='method.zone.name',
        read_only=True,
    )
    weight_kg = serializers.DecimalField(
        max_digits=8, decimal_places=3,
        read_only=True, allow_null=True,
    )
    notes = serializers.CharField(read_only=True)

    class Meta(ShipmentListSerializer.Meta):
        fields = ShipmentListSerializer.Meta.fields + (
            'user_email', 'shipping_type', 'zone_name',
            'weight_kg', 'notes', 'updated_at',
        )


class TrackingUpdateSerializer(serializers.Serializer):
    """Запрос обновления трек-номера."""

    tracking_number = serializers.CharField(
        max_length=50,
        help_text='Трек-номер от службы доставки.',
    )


class TransitionStatusSerializer(serializers.Serializer):
    """Запрос перехода статуса отправления."""

    status = serializers.CharField(
        help_text='Новый статус отправления.',
    )
    tracking_number = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text='Обновить трек-номер (опционально).',
    )


class ShipmentTrackingSerializer(serializers.Serializer):
    """
    Публичный сериализатор для отслеживания по трек-номеру.
    Не содержит чувствительных данных (user_email, order_id).
    """

    internal_tracking = serializers.CharField()
    tracking_number = serializers.CharField()
    status = serializers.CharField()
    status_display = serializers.CharField(
        source='get_status_display',
    )
    method_name = serializers.CharField(
        source='method.name',
    )
    estimated_days_display = serializers.CharField(
        source='method.estimated_days_display',
    )
    shipped_at = serializers.DateTimeField(allow_null=True)
    created_at = serializers.DateTimeField()
