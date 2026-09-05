# ────────────────────────────────────────────────────────────────────────
# apps/shipping/api_views/shipping_views.py — API views для доставки.
#
# ПОЛНЫЙ СПИСОК ЭНДПОИНТОВ:
#   GET  /api/v1/shipping/methods/               — способы доставки
#   POST /api/v1/shipping/calculate/              — расчёт стоимости
#   GET  /api/v1/shipping/shipments/              — список отправлений
#   POST /api/v1/shipping/shipments/              — создать отправление (staff)
#   GET  /api/v1/shipping/shipments/{shipment}/         — детали отправления
#   PATCH /api/v1/shipping/shipments/{shipment}/status/ — переход статуса (staff)
#   POST /api/v1/shipping/shipments/{shipment}/tracking/— трек-номер (staff)
#
# ИДЕНТИФИКАТОР ОТПРАВЛЕНИЯ (F-8, issue #73):
#   {shipment} — публичный internal_tracking (SHP-00000001);
#   числовой PK принимается как deprecated-вариант.
#   GET  /api/v1/shipping/track/{tracking}/       — отслеживание (публичный)
#
# 📖 https://www.django-rest-framework.org/api-guide/views/
# ────────────────────────────────────────────────────────────────────────

import logging

from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.pagination import (
    build_paginated_response_data,
    ensure_deterministic_ordering,
    paginate_queryset,
    pagination_parameters,
)
from apps.core.serializers import PaginationResponseSerializer

from apps.core.identifiers import (
    is_shipment_number,
    order_reference_filters,
    parse_legacy_pk,
)
from apps.orders.models import Order
from apps.shipping.models import Shipment, ShippingMethod
from apps.shipping.serializers import (
    ShipmentCreateSerializer,
    ShipmentDetailSerializer,
    ShipmentListSerializer,
    ShipmentTrackingSerializer,
    ShippingCostRequestSerializer,
    ShippingCostResponseSerializer,
    ShippingMethodListSerializer,
    TrackingUpdateSerializer,
    TransitionStatusSerializer,
)
from apps.shipping.services.shipping_service import ShippingService

try:
    from drf_spectacular.utils import extend_schema, extend_schema_view
except ImportError:
    def extend_schema(**kwargs):
        def decorator(func): return func
        return decorator
    def extend_schema_view(**kwargs):
        def decorator(cls): return cls
        return decorator

logger = logging.getLogger(__name__)


def _shipment_lookup(shipment: str) -> dict | None:
    """ORM-фильтр по публичному идентификатору отправления (F-8, #73).

    ``SHP-00000001`` → ``{'internal_tracking': 'SHP-00000001'}``
    ``"42"``         → ``{'pk': 42}`` (DEPRECATED: сырой целочисленный PK)

    ``None`` — значение не является ни публичным номером, ни допустимым PK и
    поэтому не может соответствовать ни одному отправлению. Возвращаем
    именно ``None``, а не «заведомо пустой фильтр»: фильтр вида
    ``{'pk': None}`` пришлось бы отличать от валидного по значению внутри
    словаря, и такая проверка ломается при первом же добавлении нового
    вида идентификатора. Вызывающий код обязан превратить ``None`` в
    канонический ``404 not_found``.
    """
    value = str(shipment)
    if is_shipment_number(value):
        return {'internal_tracking': value}

    legacy_pk = parse_legacy_pk(value)
    if legacy_pk is not None:
        return {'pk': legacy_pk}

    return None


def _get_shipment(queryset, shipment: str) -> Shipment:
    """Возвращает отправление по публичному идентификатору либо 404."""
    lookup = _shipment_lookup(shipment)
    if lookup is None:
        raise NotFound('Отправление не найдено.')
    try:
        return queryset.get(**lookup)
    except Shipment.DoesNotExist:
        raise NotFound('Отправление не найдено.')


# ================================================================
# Способы доставки
# ================================================================

@extend_schema_view(
    get=extend_schema(
        summary='Список способов доставки',
        responses={200: ShippingMethodListSerializer(many=True)},
    ),
)
class ShippingMethodListView(APIView):
    """
    GET /api/v1/shipping/methods/

    Возвращает список активных способов доставки.
    Поддерживает фильтрацию по зоне и типу через query-параметры.
    """
    permission_classes = (IsAuthenticated,)

    def get(self, request):
        zone_code = request.query_params.get('zone_code')
        region = request.query_params.get('region')
        shipping_type = request.query_params.get('shipping_type')

        methods = ShippingService.get_available_methods(
            zone_code=zone_code,
            region=region,
            shipping_type=shipping_type,
        )
        serializer = ShippingMethodListSerializer(methods, many=True)
        return Response(serializer.data)


# ================================================================
# Расчёт стоимости доставки
# ================================================================

@extend_schema_view(
    post=extend_schema(
        summary='Расчёт стоимости доставки',
        request=ShippingCostRequestSerializer,
        responses={200: ShippingCostResponseSerializer},
    ),
)
class ShippingCostView(APIView):
    """
    POST /api/v1/shipping/calculate/

    Рассчитывает стоимость доставки для всех доступных способов
    в указанной зоне.
    """
    permission_classes = (IsAuthenticated,)

    def post(self, request):
        input_ser = ShippingCostRequestSerializer(data=request.data)
        input_ser.is_valid(raise_exception=True)
        data = input_ser.validated_data

        result = ShippingService.calculate_shipping_cost(
            order_total=data['order_total'],
            zone_code=data.get('zone_code'),
            region=data.get('region'),
            shipping_type=data.get('shipping_type'),
            weight_kg=data.get('weight_kg'),
        )

        # Формируем ответ
        methods_data = []
        for item in result['methods']:
            method = item['method']
            methods_data.append({
                'method_id': method.pk,
                'method_name': method.name,
                'shipping_type': method.shipping_type,
                'cost': item['cost'],
                'estimated_days_display': method.estimated_days_display,
                'is_free': item['cost'] == 0,
            })

        zone_data = None
        if result['zone']:
            from apps.shipping.serializers import ShippingZoneSerializer
            zone_data = ShippingZoneSerializer(result['zone']).data

        response_data = {
            'zone': zone_data,
            'methods': methods_data,
        }
        return Response(response_data)


# ================================================================
# Отправления — CRUD
# ================================================================

@extend_schema_view(
    get=extend_schema(
        summary='Список отправлений пользователя',
        parameters=pagination_parameters(),
        responses={200: PaginationResponseSerializer},
    ),
)
class ShipmentListView(APIView):
    """
    GET /api/v1/shipping/shipments/

    Возвращает список отправлений текущего пользователя.
    Для staff — все отправления.
    """
    permission_classes = (IsAuthenticated,)

    def get(self, request):
        if request.user.is_staff:
            shipments = Shipment.objects.select_related(
                'order', 'method',
            )
        else:
            shipments = (
                request.user.shipments
                .select_related('order', 'method')
            )
        # API-05: deterministic ordering with a stable pk tie-breaker.
        shipments = ensure_deterministic_ordering(shipments, ['-created_at'])
        page_items, meta = paginate_queryset(shipments, request)
        serializer = ShipmentListSerializer(page_items, many=True)
        return Response(
            build_paginated_response_data(request, serializer.data, meta),
        )


@extend_schema_view(
    post=extend_schema(
        summary='Создать отправление (staff)',
        request=ShipmentCreateSerializer,
        responses={201: ShipmentDetailSerializer},
    ),
)
class ShipmentCreateView(APIView):
    """
    POST /api/v1/shipping/shipments/

    Создаёт отправление для заказа.
    Только для staff (администраторов / менеджеров склада).
    """
    permission_classes = (IsAdminUser,)

    def post(self, request):
        input_ser = ShipmentCreateSerializer(data=request.data)
        input_ser.is_valid(raise_exception=True)
        data = input_ser.validated_data

        # Получаем заказ по публичному order_number (F-8, #73)
        # либо по устаревшему целочисленному order_id.
        try:
            order = Order.objects.get(**order_reference_filters(data))
        except Order.DoesNotExist:
            raise NotFound('Заказ не найден.')

        # Получаем способ доставки
        try:
            method = ShippingMethod.objects.get(pk=data['method_id'])
        except ShippingMethod.DoesNotExist:
            raise NotFound('Способ доставки не найден.')

        shipment = ShippingService.create_shipment(
            order=order,
            method=method,
            weight_kg=data.get('weight_kg'),
            notes=data.get('notes', ''),
        )

        serializer = ShipmentDetailSerializer(shipment)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


@extend_schema_view(
    get=extend_schema(
        summary='Детали отправления',
        responses={200: ShipmentDetailSerializer},
    ),
)
class ShipmentDetailView(APIView):
    """
    GET /api/v1/shipping/shipments/{shipment}/

    Детали отправления. Пользователь видит только свои,
    staff — все.

    {shipment} — публичный internal_tracking (SHP-00000001);
    числовой PK принимается как deprecated-вариант (F-8, #73).
    """
    permission_classes = (IsAuthenticated,)

    def get(self, request, shipment):
        if request.user.is_staff:
            queryset = Shipment.objects.select_related(
                'order', 'method', 'method__zone', 'user',
            )
        else:
            queryset = request.user.shipments.select_related(
                'order', 'method', 'method__zone', 'user',
            )

        shipment_obj = _get_shipment(queryset, shipment)
        serializer = ShipmentDetailSerializer(shipment_obj)
        return Response(serializer.data)


# ================================================================
# Управление статусом и трекингом
# ================================================================

@extend_schema_view(
    patch=extend_schema(
        summary='Изменить статус отправления (staff)',
        request=TransitionStatusSerializer,
        responses={200: ShipmentDetailSerializer},
    ),
)
class ShipmentStatusView(APIView):
    """
    PATCH /api/v1/shipping/shipments/{shipment}/status/

    Переводит отправление в новый статус по правилам FSM.
    Только для staff.

    {shipment} — публичный internal_tracking (SHP-00000001);
    числовой PK принимается как deprecated-вариант (F-8, #73).
    """
    permission_classes = (IsAdminUser,)

    def patch(self, request, shipment):
        shipment = _get_shipment(Shipment.objects.all(), shipment)

        input_ser = TransitionStatusSerializer(data=request.data)
        input_ser.is_valid(raise_exception=True)
        data = input_ser.validated_data

        shipment = ShippingService.transition_status(
            shipment,
            data['status'],
            tracking_number=data.get('tracking_number', ''),
        )

        serializer = ShipmentDetailSerializer(shipment)
        return Response(serializer.data)


@extend_schema_view(
    post=extend_schema(
        summary='Обновить трек-номер (staff)',
        request=TrackingUpdateSerializer,
        responses={200: ShipmentDetailSerializer},
    ),
)
class ShipmentTrackingView(APIView):
    """
    POST /api/v1/shipping/shipments/{shipment}/tracking/

    Обновляет трек-номер отправления.
    Только для staff.

    {shipment} — публичный internal_tracking (SHP-00000001);
    числовой PK принимается как deprecated-вариант (F-8, #73).
    """
    permission_classes = (IsAdminUser,)

    def post(self, request, shipment):
        shipment = _get_shipment(Shipment.objects.all(), shipment)

        input_ser = TrackingUpdateSerializer(data=request.data)
        input_ser.is_valid(raise_exception=True)

        shipment = ShippingService.update_tracking(
            shipment,
            input_ser.validated_data['tracking_number'],
        )

        serializer = ShipmentDetailSerializer(shipment)
        return Response(serializer.data)


# ================================================================
# Публичное отслеживание по трек-номеру
# ================================================================

@extend_schema_view(
    get=extend_schema(
        summary='Отслеживание отправления по трек-номеру',
        responses={200: ShipmentTrackingSerializer},
    ),
)
class ShipmentTrackingByCodeView(APIView):
    """
    GET /api/v1/shipping/track/{tracking}/

    Публичный endpoint — без авторизации.
    Позволяет отслеживать посылку по ВНЕШНЕМУ трек-номеру
    (``Shipment.tracking_number``). Внутренний ``internal_tracking``
    (``SHP-*``) не является публичным ключом поиска и вместе с полностью
    неизвестным номером возвращает канонический ``404 not_found``.
    Не раскрывает чувствительных данных и факт существования shipment.
    """
    permission_classes = ()  # публичный

    def get(self, request, tracking):
        shipment = ShippingService.get_shipment_by_tracking(tracking)
        serializer = ShipmentTrackingSerializer(shipment)
        return Response(serializer.data)
