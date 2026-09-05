# ────────────────────────────────────────────────────────────────────────
# apps/payments/serializers/payment_serializers.py — сериализаторы платежей.
#
# СЕРИАЛИЗАТОРЫ:
#   INPUT (валидация запросов):
#     CreatePaymentInputSerializer   — тело POST /payments/
#     HandleWebhookInputSerializer   — тело POST /payments/webhook/
#     RefundPaymentInputSerializer   — тело POST /payments/{id}/refund/
#     CancelPaymentInputSerializer   — тело POST /payments/{id}/cancel/
#
#   OUTPUT (ответы API):
#     PaymentEventSerializer         — событие платежа
#     PaymentListSerializer          — краткий платёж для списка
#     PaymentSerializer              — полный платёж
#
# ПАТТЕРН «Input / Output разделение»:
#   Input — что API принимает (запрос)
#   Output — что API отдаёт (ответ)
#   Разные форматы, разные поля, разная валидация.
#
# 📖 https://www.django-rest-framework.org/api-guide/serializers/
# ────────────────────────────────────────────────────────────────────────

from decimal import Decimal

from rest_framework import serializers

from apps.payments.constants import (
    MAX_PAYMENT_AMOUNT,
    MIN_PAYMENT_AMOUNT,
    PAYMENT_EVENT_CHOICES,
    PAYMENT_METHOD_CHOICES,
    PAYMENT_METHOD_CARD,
    PAYMENT_STATUS_SUCCEEDED,
)
from apps.core.identifiers import OrderReferenceSerializerMixin
from apps.payments.models import Payment, PaymentEvent
from apps.payments.models.payment import PaymentStatus


# ==============================================================
# INPUT-СЕРИАЛИЗАТОРЫ
# ==============================================================

class CreatePaymentInputSerializer(OrderReferenceSerializerMixin):
    """
    Валидация тела POST /api/v1/payments/.

    ФОРМАТ ЗАПРОСА (F-8, issue #73):
        {
            "order_number": "ORD-000001", // канонический идентификатор заказа
            "amount": "1500.00",        // опционально, default = order.total
            "method": "card",            // опционально, default = "card"
            "provider": "mock"           // опционально, default = "mock"
        }

    ССЫЛКА НА ЗАКАЗ (F-8):
      order_number — канонический публичный идентификатор;
      order_id — устаревший целочисленный PK, всё ещё принимается.
      Ровно одно из полей; оба сразу → 400.
    """
    amount = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=MIN_PAYMENT_AMOUNT,
        max_value=MAX_PAYMENT_AMOUNT,
        required=False,
        help_text='Сумма платежа. Если не указана — берётся из заказа.',
    )
    method = serializers.ChoiceField(
        choices=PAYMENT_METHOD_CHOICES,
        default=PAYMENT_METHOD_CARD,
        required=False,
        help_text='Метод оплаты.',
    )
    provider = serializers.CharField(
        max_length=50,
        default='mock',
        required=False,
        help_text='Платёжный провайдер.',
    )


class HandleWebhookInputSerializer(serializers.Serializer):
    """
    Валидация тела POST /api/v1/payments/webhook/.

    ФОРМАТ ЗАПРОСА (от платёжного провайдера):
        {
            "external_id": "mock_abc123",
            "event_type": "payment.succeeded",
            "status": "succeeded",
            "payload": {...}
        }
    """
    external_id = serializers.CharField(
        max_length=200,
        help_text='ID платежа во внешней системе.',
    )
    event_type = serializers.CharField(
        max_length=100,
        help_text='Тип события от провайдера.',
    )
    status = serializers.ChoiceField(
        choices=PaymentStatus.choices,
        help_text='Новый статус платежа.',
    )
    payload = serializers.JSONField(
        required=False,
        default=dict,
        help_text='Дополнительные данные от провайдера.',
    )


class RefundPaymentInputSerializer(serializers.Serializer):
    """
    Валидация тела POST /api/v1/payments/{id}/refund/.

    ФОРМАТ ЗАПРОСА:
        {
            "amount": "500.00",     // опционально, default = полный возврат
            "reason": "Брак"        // опционально
        }
    """
    amount = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=Decimal('0.01'),
        required=False,
        help_text='Сумма возврата. Если не указана — полный возврат.',
    )
    reason = serializers.CharField(
        max_length=500,
        required=False,
        default='',
        allow_blank=True,
        help_text='Причина возврата.',
    )


class CancelPaymentInputSerializer(serializers.Serializer):
    """
    Валидация тела POST /api/v1/payments/{id}/cancel/.

    ФОРМАТ ЗАПРОСА:
        {
            "reason": "Передумал"  // опционально
        }
    """
    reason = serializers.CharField(
        max_length=500,
        required=False,
        default='',
        allow_blank=True,
        help_text='Причина отмены.',
    )


# ==============================================================
# OUTPUT-СЕРИАЛИЗАТОРЫ
# ==============================================================

class PaymentEventSerializer(serializers.ModelSerializer):
    """
    Событие платежа — только чтение (output).

    Показывает историю платежа: кто, когда, что сделал.
    """

    class Meta:
        model = PaymentEvent
        fields = (
            'id',
            'event_type',
            'old_status',
            'new_status',
            'payload',
            'performed_by',
            'note',
            'created_at',
        )
        read_only_fields = fields


class PaymentListSerializer(serializers.ModelSerializer):
    """
    Краткая информация о платеже — для списка.

    БЕЗ events, metadata, payload — для быстрой загрузки.
    """
    status_display = serializers.CharField(
        source='get_status_display',
        read_only=True,
    )
    method_display = serializers.CharField(
        source='get_method_display',
        read_only=True,
    )
    order_number = serializers.CharField(read_only=True)

    class Meta:
        model = Payment
        fields = (
            'id',
            'order_number',
            'status',
            'status_display',
            'amount',
            'method',
            'method_display',
            'provider',
            'created_at',
            'paid_at',
        )
        read_only_fields = fields


class PaymentSerializer(serializers.ModelSerializer):
    """
    Полная информация о платеже — для детальной страницы.

    Включает events (история), все суммы, таймстампы, metadata.
    """
    status_display = serializers.CharField(
        source='get_status_display',
        read_only=True,
    )
    method_display = serializers.CharField(
        source='get_method_display',
        read_only=True,
    )
    is_terminal = serializers.BooleanField(read_only=True)
    is_paid = serializers.BooleanField(read_only=True)
    is_refundable = serializers.BooleanField(read_only=True)
    events = PaymentEventSerializer(many=True, read_only=True)
    order_number = serializers.CharField(read_only=True)

    class Meta:
        model = Payment
        fields = (
            'id',
            'order_number',
            'status',
            'status_display',
            'is_terminal',
            'is_paid',
            'is_refundable',
            'amount',
            'refund_amount',
            'method',
            'method_display',
            'provider',
            'external_id',
            'order',
            'user',
            'paid_at',
            'cancelled_at',
            'refunded_at',
            'note',
            'refund_reason',
            'metadata',
            'events',
            'created_at',
            'updated_at',
        )
        read_only_fields = fields
