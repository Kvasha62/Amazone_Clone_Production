# ────────────────────────────────────────────────────────────────────────
# apps/inventory/api_views/inventory_views.py — API views для склада.
#
# ПЯТЬ ЭНДПОИНТОВ:
#   StockListView          — GET /api/v1/inventory/                    (список остатков)
#   StockDetailView        — GET /api/v1/inventory/{variant_id}/       (остатки варианта)
#   StockRestockView       — POST /api/v1/inventory/{variant_id}/restock/  (пополнение)
#   StockAdjustView        — POST /api/v1/inventory/{variant_id}/adjust/   (корректировка)
#   StockMovementListView  — GET /api/v1/inventory/{variant_id}/movements/ (история)
#
# БЕЗОПАСНОСТЬ:
#   • IsAdminUser — только staff/admin могут видеть и менять склад
#   • (Просмотр остатков можно открыть для всех — решите сами)
#
# 📖 https://www.django-rest-framework.org/api-guide/views/
# 📖 https://www.django-rest-framework.org/api-guide/permissions/
#
# ЧТО БУДЕТ, ЕСЛИ УДАЛИТЬ ФАЙЛ:
#   Все 5 endpoints склада → 404
# ────────────────────────────────────────────────────────────────────────

import logging

from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.pagination import (
    build_paginated_response_data,
    ensure_deterministic_ordering,
    paginate_queryset,
    pagination_parameters,
)
from apps.core.serializers import PaginationResponseSerializer

from apps.catalog.models import ProductVariant
from apps.inventory.models import Stock, StockMovement
from apps.inventory.serializers import (
    AdjustStockInputSerializer,
    RestockInputSerializer,
    StockMovementSerializer,
    StockSerializer,
)
from apps.inventory.services.inventory_service import InventoryService

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


def _get_variant(variant_id: int) -> ProductVariant:
    """Получает вариант по PK или выбрасывает NotFound."""
    try:
        return ProductVariant.objects.get(pk=variant_id)
    except ProductVariant.DoesNotExist:
        raise NotFound('Вариант товара не найден.')


# ==============================================================
# GET /api/v1/inventory/ — список остатков
# ==============================================================

@extend_schema_view(
    get=extend_schema(
        summary='Остатки на складе',
        description='Возвращает список остатков всех вариантов (staff only).',
        parameters=pagination_parameters(),
        responses={200: PaginationResponseSerializer},
    ),
)
class StockListView(APIView):
    """GET /api/v1/inventory/ — список остатков (staff only)."""

    permission_classes = (IsAdminUser,)

    def get(self, request):
        stocks = Stock.objects.with_variant().all()
        # API-05: deterministic ordering with a stable pk tie-breaker.
        stocks = ensure_deterministic_ordering(stocks, ['-created_at'])
        page_items, meta = paginate_queryset(stocks, request)

        serializer = StockSerializer(page_items, many=True)
        return Response(
            build_paginated_response_data(request, serializer.data, meta),
        )


# ==============================================================
# GET /api/v1/inventory/{variant_id}/ — остатки конкретного варианта
# ==============================================================

@extend_schema_view(
    get=extend_schema(
        summary='Остатки варианта',
        description='Возвращает остатки конкретного варианта товара.',
        responses={200: StockSerializer},
    ),
)
class StockDetailView(APIView):
    """GET /api/v1/inventory/{variant_id}/ — остатки варианта (staff only)."""

    permission_classes = (IsAdminUser,)

    def get(self, request, variant_id: int):
        variant = _get_variant(variant_id)
        stock = InventoryService.get_or_create_stock(variant)
        # Перечитываем для свежих данных.
        stock = Stock.objects.get(pk=stock.pk)
        return Response(StockSerializer(stock).data)


# ==============================================================
# POST /api/v1/inventory/{variant_id}/restock/ — пополнение
# ==============================================================

@extend_schema_view(
    post=extend_schema(
        summary='Пополнить склад',
        description='Добавляет количество на склад (приёмка от поставщика).',
        request=RestockInputSerializer,
        responses={201: StockMovementSerializer},
    ),
)
class StockRestockView(APIView):
    """POST /api/v1/inventory/{variant_id}/restock/ — пополнение (staff only)."""

    permission_classes = (IsAdminUser,)

    def post(self, request, variant_id: int):
        variant = _get_variant(variant_id)

        input_serializer = RestockInputSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)

        movement = InventoryService.restock(
            variant,
            input_serializer.validated_data['quantity'],
            performed_by=request.user,
            note=input_serializer.validated_data.get('note', ''),
        )

        return Response(
            StockMovementSerializer(movement).data,
            status=status.HTTP_201_CREATED,
        )


# ==============================================================
# POST /api/v1/inventory/{variant_id}/adjust/ — корректировка
# ==============================================================

@extend_schema_view(
    post=extend_schema(
        summary='Скорректировать остатки',
        description='Устанавливает точное количество (инвентаризация).',
        request=AdjustStockInputSerializer,
        responses={200: StockMovementSerializer},
    ),
)
class StockAdjustView(APIView):
    """POST /api/v1/inventory/{variant_id}/adjust/ — корректировка (staff only)."""

    permission_classes = (IsAdminUser,)

    def post(self, request, variant_id: int):
        variant = _get_variant(variant_id)

        input_serializer = AdjustStockInputSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)

        movement = InventoryService.adjust_stock(
            variant,
            input_serializer.validated_data['new_quantity'],
            performed_by=request.user,
            note=input_serializer.validated_data.get('note', ''),
        )

        return Response(StockMovementSerializer(movement).data)


# ==============================================================
# GET /api/v1/inventory/{variant_id}/movements/ — история движений
# ==============================================================

@extend_schema_view(
    get=extend_schema(
        summary='История движений',
        description='Возвращает историю движений для варианта.',
        parameters=pagination_parameters(),
        responses={200: PaginationResponseSerializer},
    ),
)
class StockMovementListView(APIView):
    """
    GET /api/v1/inventory/{variant_id}/movements/ — история (staff only).
    """

    permission_classes = (IsAdminUser,)

    def get(self, request, variant_id: int):
        variant = _get_variant(variant_id)

        try:
            stock = Stock.objects.get(variant=variant)
        except Stock.DoesNotExist:
            stock = None

        if stock is None:
            # Empty collection: canonical envelope instead of a bare array.
            page_items, meta = paginate_queryset(StockMovement.objects.none(), request)
            return Response(
                build_paginated_response_data(request, [], meta),
            )

        movements = stock.movements.all()
        # API-05: deterministic ordering with a stable pk tie-breaker.
        movements = ensure_deterministic_ordering(movements, ['-created_at'])
        page_items, meta = paginate_queryset(movements, request)
        serializer = StockMovementSerializer(page_items, many=True)
        return Response(
            build_paginated_response_data(request, serializer.data, meta),
        )
