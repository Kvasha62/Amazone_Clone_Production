# ────────────────────────────────────────────────────────────────────────
# apps/shipping/api_views/shipping_views.py — API views для доставки.
#
# ПОЛНЫЙ СПИСОК ЭНДПОИНТОВ:
#   GET  /api/v1/shipping/methods/               — способы доставки
#   POST /api/v1/shipping/calculate/              — расчёт стоимости
#   GET  /api/v1/shipping/shipments/              — список отправлений
#   POST /api/v1/shipping/shipments/              — создать отправление (staff)
#   GET  /api/v1/shipping/shipments/{id}/         — детали отправления
#   PATCH /api/v1/shipping/shipments/{id}/status/ — переход статуса (staff)
#   POST /api/v1/shipping/shipments/{id}/tracking/— обновить трек-номер (staff)
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

        # Получаем заказ
        try:
            order = Order.objects.get(pk=data['order_id'])
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
    GET /api/v1/shipping/shipments/{id}/

    Детали отправления. Пользователь видит только свои,
    staff — все.
    """
    permission_classes = (IsAuthenticated,)

    def get(self, request, pk):
        try:
            if request.user.is_staff:
                shipment = Shipment.objects.select_related(
                    'order', 'method', 'method__zone', 'user',
                ).get(pk=pk)
            else:
                shipment = (
                    request.user.shipments
                    .select_related('order', 'method', 'method__zone', 'user')
                    .get(pk=pk)
                )
        except Shipment.DoesNotExist:
            raise NotFound('Отправление не найдено.')

        serializer = ShipmentDetailSerializer(shipment)
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
    PATCH /api/v1/shipping/shipments/{id}/status/

    Переводит отправление в новый статус по правилам FSM.
    Только для staff.
    """
    permission_classes = (IsAdminUser,)

    def patch(self, request, pk):
        try:
            shipment = Shipment.objects.get(pk=pk)
        except Shipment.DoesNotExist:
            raise NotFound('Отправление не найдено.')

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
    POST /api/v1/shipping/shipments/{id}/tracking/

    Обновляет трек-номер отправления.
    Только для staff.
    """
    permission_classes = (IsAdminUser,)

    def post(self, request, pk):
        try:
            shipment = Shipment.objects.get(pk=pk)
        except Shipment.DoesNotExist:
            raise NotFound('Отправление не найдено.')

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
