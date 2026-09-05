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

import hmac
import logging

from django.conf import settings

from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from apps.core.api_errors import BadGateway
from apps.core.identifiers import order_reference_filters
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
from apps.payments.constants import (
    PAYMENT_EVENT_ORDER_CONFIRM_FAILED,
    WEBHOOK_NONCE_HEADER,
    WEBHOOK_SIGNATURE_HEADER,
    WEBHOOK_TIMESTAMP_HEADER,
)
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
from apps.payments.services.webhook_security import (
    claim_webhook_nonce,
    compute_webhook_signature,
    is_fresh_webhook_timestamp,
    is_valid_webhook_nonce,
    is_valid_webhook_signature_format,
    is_valid_webhook_timestamp,
)

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
            ).get(payment_number=payment_number)
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
          2. Получение заказа с owner scoping (Issue #68 / API-01 F-3)
             по публичному order_number заказа (F-8 / #73) либо по
             устаревшему order_id
          3. Определение суммы (из body или из заказа)
          4. PaymentService.create_payment() — бизнес-логика
          5. Сериализация и ответ (201 CREATED)

        OWNERSHIP (API-04 §10, 404-not-403 policy):
          Заказ резолвится с фильтром user=request.user на boundary view:
          чужой или несуществующий заказ → canonical 404
          «Заказ не найден.» — существование ресурса не раскрывается.
          Service-level ownership check в PaymentService.create_payment()
          остаётся как defense-in-depth.
        """
        input_serializer = CreatePaymentInputSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)

        data = input_serializer.validated_data

        # Получаем заказ с owner scoping (IDOR-защита на boundary view):
        # заказ другого пользователя неотличим от несуществующего → 404.
        order = Order.objects.filter(
            user=request.user,
            **order_reference_filters(data),
        ).first()
        if order is None:
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
            payment = Payment.objects.get(payment_number=payment_number)
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
            'Аутентификация через HMAC-SHA256 (X-Webhook-Signature) '
            'с защитой от replay: X-Webhook-Timestamp + X-Webhook-Nonce. '
            'См. docs/api/API_CONTRACT.md §11.3 и ADR-004.'
        ),
        request=HandleWebhookInputSerializer,
        responses={200: PaymentSerializer},
    ),
)
class PaymentWebhookView(APIView):
    """
    POST /api/v1/payments/webhook/

    Приём вебхуков от платёжного провайдера.

    БЕЗОПАСНОСТЬ (транспортная защита + replay protection, Issue #71):
      • AllowAny — провайдер отправляет запрос без JWT.
      • HMAC-SHA256 подпись обязательна (X-Webhook-Signature).
        Подписывается не только body, а каноническая сборка:
            timestamp || nonce || raw_body
        (raw_body — исходные байты request.body).
      • X-Webhook-Timestamp — Unix epoch (секунды), окно свежести ±300 с.
      • X-Webhook-Nonce — одноразовый; фиксируется в БД в отдельной
        (durable) транзакции; повтор nonce → отклонение (replay).
      • Secret — settings.PAYMENT_WEBHOOK_SECRET (env var).
      • Без секрета / без заголовков / невалидный формат / просроченный
        timestamp / неверная подпись / повторный nonce → 403 (fail-closed).
      • Timing-safe сравнение (hmac.compare_digest).
      • Security failure НЕ раскрывает причину: для клиента все отказы
        идентичны (тот же canonical authentication error).

    ИДЕМПОТЕНТНОСТЬ:
      • Transport-level: timestamp + nonce — повтор той же подписи
        невозможен (freshness + одноразовый nonce).
      • Business-level: Payment.external_id — повторное бизнес-событие
        того же платежа идемпотентно (см. handle_webhook).
    """

    permission_classes = (AllowAny,)
    authentication_classes = []  # Без аутентификации — внешний запрос

    # Имена HTTP-заголовков (контракт — см. constants).
    TIMESTAMP_HEADER = WEBHOOK_TIMESTAMP_HEADER
    NONCE_HEADER = WEBHOOK_NONCE_HEADER
    SIGNATURE_HEADER = WEBHOOK_SIGNATURE_HEADER

    @staticmethod
    def _header(request, name: str):
        """Доступ к HTTP-заголовку по имени (Django WSGI META)."""
        meta_key = f'HTTP_{name.upper().replace("-", "_")}'
        return request.META.get(meta_key)

    def _verify_webhook_security(self, request) -> bool:
        """
        Полный security pipeline webhook (Issue #71).

        ПОРЯДОК (бизнес-логика НЕ выполняется до его успешного конца):
          1. PAYMENT_WEBHOOK_SECRET задан              (fail-closed)
          2. Прочитать timestamp, nonce, signature
          3. Валидация формата timestamp (ASCII decimal int)
          4. Валидация формата/длины nonce
          5. Валидация формата signature (lowercase hex)
          6. Свежесть timestamp: abs(now - ts) <= 300 с
          7. HMAC-SHA256(secret, ts || nonce || raw_body)
          8. Timing-safe сравнение (hmac.compare_digest)
          9. Атомарная (race-safe) фиксация nonce в durable-транзакции

        ВОЗВРАЩАЕТ:
          True  — все security checks пройдены, nonce зафиксирован
          False — любой отказ (secret/format/freshness/signature/replay)

        🔴 НИКОГДА не логируем secret, signature, nonce, timestamp.
        🔴 Для клиента причина отказа не различается (см. post()).
        """
        # 1. Fail-closed: secret ОБЯЗАН быть задан (в production — env var).
        secret = getattr(settings, 'PAYMENT_WEBHOOK_SECRET', None)
        if not secret:
            logger.warning('webhook_rejected_no_secret_configured')
            return False

        # 2. Прочитать заголовки.
        timestamp = self._header(request, self.TIMESTAMP_HEADER)
        nonce = self._header(request, self.NONCE_HEADER)
        signature = self._header(request, self.SIGNATURE_HEADER)

        # 3–5. Формат заголовков (отклоняем до сравнения подписи).
        if not (
            is_valid_webhook_timestamp(timestamp)
            and is_valid_webhook_nonce(nonce)
            and is_valid_webhook_signature_format(signature)
        ):
            # Не различаем, какой именно заголовок невалиден:
            # для атакующего это не несёт полезной информации, а для
            # клиента — единый canonical error.
            logger.warning('webhook_rejected_invalid_header_format')
            return False

        # 6. Свежесть timestamp (±300 с).
        if not is_fresh_webhook_timestamp(int(timestamp)):
            logger.warning('webhook_rejected_stale_timestamp')
            return False

        # 7. Подпись по канонической сборке ts || nonce || raw_body.
        expected = compute_webhook_signature(
            secret, timestamp, nonce, request.body,
        )

        # 8. Timing-safe сравнение.
        if not hmac.compare_digest(signature, expected):
            logger.warning('webhook_rejected_invalid_signature')
            return False

        # 9. Атомарная (race-safe) фиксация nonce в DURABLE-транзакции.
        #    Она НЕ откатывается вместе с бизнес-транзакцией ниже.
        if not claim_webhook_nonce(nonce, int(timestamp)):
            logger.warning('webhook_rejected_replay')
            return False

        return True

    def post(self, request):
        """
        POST /api/v1/payments/webhook/

        ПОТОК:
          1. Security pipeline (secret, timestamp, nonce, signature,
             freshness, HMAC, claim nonce) — до ВСЕЙ бизнес-логики.
          2. Валидация body (HandleWebhookInputSerializer)
          3. PaymentService.handle_webhook() — обработка
          4. Ответ 200 (провайдер ожидает 200 для подтверждения)

        SECURITY FAILURE:
          Любой отказ в шаге 1 → PermissionDenied с единым сообщением.
          Для клиента все отказы (нет secret / нет заголовков /
          невалидный формат / просроченный timestamp / неверная подпись /
          повторный nonce) выглядят ИДЕНТИЧНО — canonical
          authentication error, ничего не раскрывается.
        """
        # ── Security pipeline (HMAC + timestamp + nonce + replay) ──
        if not self._verify_webhook_security(request):
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
