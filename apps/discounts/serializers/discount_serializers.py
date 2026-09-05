from decimal import Decimal

from rest_framework import serializers

from apps.core.identifiers import OrderReferenceSerializerMixin
from apps.discounts.models import Coupon


# ==============================================================
# INPUT
# ==============================================================

class ApplyCouponInputSerializer(OrderReferenceSerializerMixin):
    """POST /api/v1/discounts/apply/.

    ССЫЛКА НА ЗАКАЗ (F-8, issue #73):
      order_number (``ORD-000001``) — канонический публичный идентификатор;
      order_id — устаревший целочисленный PK (принимается, deprecated).
    """

    code = serializers.CharField(max_length=50)


class RemoveCouponInputSerializer(OrderReferenceSerializerMixin):
    """POST /api/v1/discounts/remove/.

    Тело содержит только ссылку на заказ: order_number (канонический)
    либо order_id (устар.).
    """


class PreviewDiscountInputSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=50)
    order_amount = serializers.DecimalField(
        max_digits=12, decimal_places=2, min_value=Decimal('0.01'),
    )


# ==============================================================
# OUTPUT
# ==============================================================

class CouponListSerializer(serializers.ModelSerializer):
    is_valid_now = serializers.BooleanField(read_only=True)
    is_exhausted = serializers.BooleanField(read_only=True)

    class Meta:
        model = Coupon
        fields = (
            'id', 'code', 'discount_type', 'discount_value',
            'max_discount', 'min_order_amount', 'is_valid_now',
            'is_exhausted', 'started_at', 'ended_at',
        )
        read_only_fields = fields


class CouponSerializer(serializers.ModelSerializer):
    is_valid_now = serializers.BooleanField(read_only=True)
    is_exhausted = serializers.BooleanField(read_only=True)

    class Meta:
        model = Coupon
        fields = (
            'id', 'code', 'description', 'discount_type',
            'discount_value', 'max_discount', 'min_order_amount',
            'max_total_uses', 'max_uses_per_user', 'times_used',
            'started_at', 'ended_at', 'campaign_id',
            'is_active', 'is_valid_now', 'is_exhausted',
            'created_at',
        )
        read_only_fields = fields


class PreviewDiscountOutputSerializer(serializers.Serializer):
    code = serializers.CharField()
    discount_type = serializers.CharField()
    discount_value = serializers.DecimalField(max_digits=10, decimal_places=2)
    max_discount = serializers.DecimalField(max_digits=12, decimal_places=2, allow_null=True)
    calculated_discount = serializers.DecimalField(max_digits=12, decimal_places=2)
    amount_after_discount = serializers.DecimalField(max_digits=12, decimal_places=2)
