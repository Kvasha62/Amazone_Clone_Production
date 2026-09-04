# ────────────────────────────────────────────────────────────────────────
# apps/payments/api_views/payment_views.py — API views для платежей.
#
# ПЯТЬ ЭНДПОИНТОВ:
#   PaymentListView      — GET    /api/v1/payments/                   (список)
#                          POST   /api/v1/payments/                   (создать)
#   PaymentDetailView    — GET    /api/v1/payments/{payment_number}/  (детали)
#   PaymentRefundView    — POST   /api/v1/payments/{payment_number}/refund/ (возврат)
#   PaymentCancelView    — POST   /api/v1/payments/{payment_number}/cancel/ (отмена)
#   PaymentWebhookView   — POST   /api/v1/payments/webhook/           (вебхук)
#
# АРХИТЕКТУРА:
#   _PaymentViewMixin — общая логика (получить платёж, проверить ownership)
#   Каждый view наследует Mixin + APIView → DRY.
#
# БЕЗОПАСНОСТЬ:
#   • IsAuthenticated — список, создание, детали, отмена
#   • AllowAny + HMAC-SHA256 — вебхук (внешний запрос от провайдера)
#   • IsAdminUser — возврат средств (только для staff)
#   • Ownership check — пользователь видит только свои платежи
#
# 📖 https://www.django-rest-framework.org/api-guide/views/
# ────────────────────────────────────────────────────────────────────────

import hashlib
import hmac
import logging

from django.conf import settings

from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from apps.core.api_errors import BadGateway
from rest_framework.permissions import AllowAny, IsAdminUser, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.pagination import (
    build_paginated_response_data,
    ensure_deterministic_ordering,
    paginate_queryset,
    pagination_parameters,
)
from apps.core.serializers import PaginationResponseSerializer

from apps.orders.models import Order
from apps.orders.models.order import OrderStatus
from apps.payments.constants import PAYMENT_EVENT_ORDER_CONFIRM_FAILED
from apps.payments.exceptions import OrderConfirmationError
from apps.payments.models import Payment, PaymentEvent
from apps.payments.serializers import (
    CancelPaymentInputSerializer,
    CreatePaymentInputSerializer,
    HandleWebhookInputSerializer,
    PaymentListSerializer,
    PaymentSerializer,
    RefundPaymentInputSerializer,
)
from apps.payments.services.payment_service import PaymentService

# drf-spectacular — опциональная зависимость.
try:
    from drf_spectacular.utils import extend_schema, extend_schema_view
except ImportError:
    def extend_schema(**kwargs):
        def decorator(func):
            return func
        return decorator

    def extend_schema_view(**kwargs):
        def decorator(cls):
            return cls
        return decorator

logger = logging.getLogger(__name__)


# ==============================================================
# ОБЩАЯ ЛОГИКА (_PaymentViewMixin)
# ==============================================================

class _PaymentViewMixin:
    """
    Общая логика для всех payment-view.

    Методы:
      _get_payment() — получить платёж по payment_number с ownership check
    """

    permission_classes = (IsAuthenticated,)

    def _get_payment(self, request, payment_number: str) -> Payment:
        """
        Получает платёж по payment_number с проверкой ownership.

        ЗАЩИТА ОТ IDOR:
          Пользователь может запросить чужой платёж.
          Проверяем: payment.user == request.user.
          Если нет → 404 (не 403 — не раскрываем существование).
        """
        try:
            payment = Payment.objects.select_related(
                'order', 'user',
            ).get(order_number=payment_number)
        except Payment.DoesNotExist:
            raise NotFound('Платёж не найден.')

        if not request.user.is_staff and payment.user_id != request.user.pk:
            raise NotFound('Платёж не найден.')

        return payment


# ==============================================================
# /api/v1/payments/ — список и создание платежей
# ==============================================================

@extend_schema_view(
    get=extend_schema(
        summary='Список платежей',
        description='Возвращает список платежей текущего пользователя.',
        parameters=pagination_parameters(),
        responses={200: PaginationResponseSerializer},
    ),
    post=extend_schema(
        summary='Создать платёж',
        description='Создаёт платёж для заказа.',
        request=CreatePaymentInputSerializer,
        responses={201: PaymentSerializer},
    ),
)
class PaymentListView(_PaymentViewMixin, APIView):
    """
    GET  /api/v1/payments/   — список платежей пользователя
    POST /api/v1/payments/   — создать платёж для заказа
    """

    def get(self, request):
        """
        GET /api/v1/payments/

        ВОЗВРАЩАЕТ список платежей текущего пользователя.
        """
        payments = Payment.objects.for_user(request.user).select_related(
            'order', 'user',
        )
        # API-05: deterministic ordering with a stable pk tie-breaker.
        payments = ensure_deterministic_ordering(payments, ['-created_at'])
        page_items, meta = paginate_queryset(payments, request)

        serializer = PaymentListSerializer(page_items, many=True)
        return Response(
            build_paginated_response_data(request, serializer.data, meta),
        )

    def post(self, request):
        """
        POST /api/v1/payments/

        Создаёт платёж для заказа.

        ПОТОК:
          1. Валидация body (CreatePaymentInputSerializer)
          2. Получение заказа
          3. Определение суммы (из body или из заказа)
          4. PaymentService.create_payment() — бизнес-логика
          5. Сериализация и ответ (201 CREATED)
        """
        input_serializer = CreatePaymentInputSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)

        data = input_serializer.validated_data

        # Получаем заказ
        try:
            order = Order.objects.get(pk=data['order_id'])
        except Order.DoesNotExist:
            raise NotFound('Заказ не найден.')

        # Сумма: из body или из total заказа
        amount = data.get('amount') or order.total

        payment = PaymentService.create_payment(
            order=order,
            user=request.user,
            amount=amount,
            method=data.get('method', 'card'),
            provider=data.get('provider', 'mock'),
        )

        # Перечитываем с prefetch
        payment = Payment.objects.with_events().get(pk=payment.pk)

        return Response(
            PaymentSerializer(payment).data,
            status=status.HTTP_201_CREATED,
        )


# ==============================================================
# /api/v1/payments/{payment_number}/ — детали платежа
# ==============================================================

@extend_schema_view(
    get=extend_schema(
        summary='Детали платежа',
        description='Возвращает полную информацию о платеже с историей событий.',
        responses={200: PaymentSerializer},
    ),
)
class PaymentDetailView(_PaymentViewMixin, APIView):
    """
    GET /api/v1/payments/{payment_number}/

    Полная информация о платеже: события, суммы, таймстампы.
    """

    def get(self, request, payment_number: str):
        payment = self._get_payment(request, payment_number)
        # Подтягиваем события
        payment = (
            Payment.objects
            .with_events()
            .select_related('order', 'user')
            .get(pk=payment.pk)
        )
        return Response(PaymentSerializer(payment).data)


# ==============================================================
# /api/v1/payments/{payment_number}/refund/ — возврат средств
# ==============================================================

@extend_schema_view(
    post=extend_schema(
        summary='Возврат средств',
        description='Оформляет возврат средств. Только для staff/admin.',
        request=RefundPaymentInputSerializer,
        responses={200: PaymentSerializer},
    ),
)
class PaymentRefundView(APIView):
    """
    POST /api/v1/payments/{payment_number}/refund/

    Возврат средств. ТОЛЬКО для staff/admin.
    Поддерживает полный и частичный возврат.
    """

    permission_classes = (IsAdminUser,)

    def post(self, request, payment_number: str):
        """
        POST /api/v1/payments/{payment_number}/refund/

        ПОТОК:
          1. Найти платёж по payment_number
          2. Валидация body (RefundPaymentInputSerializer)
          3. PaymentService.refund_payment() — бизнес-логика
          4. Сериализация и ответ
        """
        try:
            payment = Payment.objects.get(order_number=payment_number)
        except Payment.DoesNotExist:
            raise NotFound('Платёж не найден.')

        input_serializer = RefundPaymentInputSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)

        payment = PaymentService.refund_payment(
            payment,
            amount=input_serializer.validated_data.get('amount'),
            reason=input_serializer.validated_data.get('reason', ''),
            user=request.user,
        )

        payment = Payment.objects.with_events().get(pk=payment.pk)
        return Response(PaymentSerializer(payment).data)


# ==============================================================
# /api/v1/payments/{payment_number}/cancel/ — отмена платежа
# ==============================================================

@extend_schema_view(
    post=extend_schema(
        summary='Отменить платёж',
        description='Отменяет платёж. Доступно для владельца и staff.',
        request=CancelPaymentInputSerializer,
        responses={200: PaymentSerializer},
    ),
)
class PaymentCancelView(_PaymentViewMixin, APIView):
    """
    POST /api/v1/payments/{payment_number}/cancel/

    Отмена платежа. Доступна:
      • Владельцу платежа — если платёж в PENDING/PROCESSING
      • Staff/admin — всегда
    """

    def post(self, request, payment_number: str):
        payment = self._get_payment(request, payment_number)

        input_serializer = CancelPaymentInputSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)

        payment = PaymentService.cancel_payment(
            payment,
            user=request.user,
            note=input_serializer.validated_data.get('reason', ''),
        )

        payment = Payment.objects.with_events().get(pk=payment.pk)
        return Response(PaymentSerializer(payment).data)


# ==============================================================
# /api/v1/payments/webhook/ — вебхук от провайдера
# ==============================================================

@extend_schema_view(
    post=extend_schema(
        summary='Вебхук от платёжного провайдера',
        description=(
            'Принимает уведомления от платёжного провайдера. '
            'AllowAny — запрос приходит без JWT. '
            'Аутентификация через HMAC-SHA256 подпись (X-Webhook-Signature).'
        ),
        request=HandleWebhookInputSerializer,
        responses={200: PaymentSerializer},
    ),
)
class PaymentWebhookView(APIView):
    """
    POST /api/v1/payments/webhook/

    Приём вебхуков от платёжного провайдера.

    БЕЗОПАСНОСТЬ:
      • AllowAny — провайдер отправляет запрос без JWT.
      • HMAC-SHA256 подпись обязательна (X-Webhook-Signature header).
      • Secret берётся из settings.PAYMENT_WEBHOOK_SECRET (env var).
      • В DEBUG-режиме без секрета — webhook отклоняется (не молча работает).
      • Timing-safe сравнение (hmac.compare_digest).

    ИДЕМПОТЕНТНОСТЬ:
      Повторный вебхук обрабатывается корректно.
    """

    permission_classes = (AllowAny,)
    authentication_classes = []  # Без аутентификации — внешний запрос

    # Имя HTTP-заголовка с подписью
    SIGNATURE_HEADER = 'X-Webhook-Signature'

    def _verify_signature(self, request) -> bool:
        """
        Проверяет HMAC-SHA256 подпись вебхука.

        АЛГОРИТМ:
          1. Получить PAYMENT_WEBHOOK_SECRET из settings
          2. Получить X-Webhook-Signature из заголовков
          3. Вычислить HMAC-SHA256(secret, request.body)
          4. Сравнить timing-safe (hmac.compare_digest)

        ВОЗВРАЩАЕТ:
          True — подпись валидна
          False — подпись невалидна или отсутствует

        🔴 НИКОГДА не логируем secret или signature.
        """
        secret = getattr(settings, 'PAYMENT_WEBHOOK_SECRET', None)
        if not secret:
            # В production секрет ОБЯЗАН быть задан.
            # В development без секрета — отклоняем (не молча работаем).
            logger.warning('webhook_rejected_no_secret_configured')
            return False

        signature = request.META.get(f'HTTP_{self.SIGNATURE_HEADER.upper().replace("-", "_")}')
        if not signature:
            logger.warning('webhook_rejected_missing_signature')
            return False

        expected = hmac.new(
            secret.encode('utf-8'),
            request.body,
            hashlib.sha256,
        ).hexdigest()

        if not hmac.compare_digest(signature, expected):
            logger.warning('webhook_rejected_invalid_signature')
            return False

        return True

    def post(self, request):
        """
        POST /api/v1/payments/webhook/

        ПОТОК:
          1. Проверка HMAC-SHA256 подписи (X-Webhook-Signature)
          2. Валидация body (HandleWebhookInputSerializer)
          3. PaymentService.handle_webhook() — обработка
          4. Ответ 200 (провайдер ожидает 200 для подтверждения)
        """
        # ── HMAC-SHA256 аутентификация вебхука ──
        if not self._verify_signature(request):
            raise PermissionDenied('Invalid payment webhook signature.')

        input_serializer = HandleWebhookInputSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)

        data = input_serializer.validated_data

        try:
            payment = PaymentService.handle_webhook(
                external_id=data['external_id'],
                event_type=data['event_type'],
                status=data['status'],
                payload=data.get('payload', {}),
            )
        except OrderConfirmationError as exc:
            # PROD-003: подтверждение заказа не удалось — провал обязан
            # быть наблюдаемым и восстанавливаемым. Транзакция вебхука
            # уже откатилась, поэтому аудит-событие пишется в НОВОЙ
            # транзакции и гарантированно сохраняется.
            payment = Payment.objects.filter(
                external_id=data['external_id'],
            ).first()
            if payment is not None:
                PaymentEvent.objects.create(
                    payment=payment,
                    event_type=PAYMENT_EVENT_ORDER_CONFIRM_FAILED,
                    payload={
                        'error': str(exc),
                        'order_status': exc.order_status or '',
                    },
                    note='Webhook получен, но подтверждение заказа не удалось.',
                )
                if exc.order_status in (
                    OrderStatus.DELIVERED,
                    OrderStatus.CANCELLED,
                ):
                    # Заказ уже завершён: деньги за завершённый заказ
                    # принимать нельзя. Закрываем платёж и отвечаем 200,
                    # чтобы провайдер не повторял доставку бесконечно.
                    try:
                        PaymentService.fail_payment(
                            payment,
                            payload={
                                'error': str(exc),
                                'order_status': exc.order_status,
                            },
                            note='Оплата поступила после завершения заказа.',
                        )
                    except (ValidationError, Payment.DoesNotExist) as fail_exc:
                        # Ожидаемые доменные/not-found сбои закрытия платежа
                        # логируются и оставляют контракт «завершённый заказ
                        # не принимает деньги». Неожиданные ошибки (БД,
                        # программные) пробрасываются и НЕ маскируются под 200.
                        logger.error(
                            'webhook_close_payment_failed',
                            extra={
                                'payment_id': payment.pk,
                                'error': str(fail_exc),
                            },
                        )
                    return Response(
                        {'detail': 'Заказ завершён; платёж отклонён.'},
                        status=status.HTTP_200_OK,
                    )
            # Резервирование стока не удалось или сбой БД: платёж
            # откатился вместе с транзакцией. 502 → провайдер повторит
            # доставку, а повторная попытка идемпотентна.
            raise BadGateway(
                'Подтверждение заказа не удалось; повторите запрос позже.',
            )

        if payment is None:
            # Платёж не найден — всё равно 200,
            # чтобы провайдер не повторял отправку.
            return Response(
                {'detail': 'Платёж не найден, webhook logged.'},
                status=status.HTTP_200_OK,
            )

        payment = Payment.objects.with_events().get(pk=payment.pk)
        return Response(PaymentSerializer(payment).data)
