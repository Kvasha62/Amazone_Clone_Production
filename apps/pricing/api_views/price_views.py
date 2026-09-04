# ────────────────────────────────────────────────────────────────────────
# apps/pricing/api_views/price_views.py — API views для ценообразования.
#
# Все endpoints — ТОЛЬКО для staff (IsAdminUser).
# Цены меняют менеджеры, не покупатели.
#
# ТРИ ЭНДПОИНТА:
#   PriceDetailView  — GET/POST /pricing/variants/<id>/price/
#   PriceHistoryView — GET     /pricing/variants/<id>/history/
#   BulkPriceView    — POST    /pricing/prices/bulk/
#
# 📖 https://www.django-rest-framework.org/api-guide/permissions/#isadminuser
# 📖 https://www.django-rest-framework.org/api-guide/views/
# ────────────────────────────────────────────────────────────────────────

import logging

from rest_framework import status
from rest_framework.exceptions import NotFound
# IsAdminUser — разрешает только request.user.is_staff=True.
# 📖 https://www.django-rest-framework.org/api-guide/permissions/#isadminuser
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.catalog.models import ProductVariant
from apps.pricing.models import Price, PriceHistory
from apps.pricing.serializers import (
    SetPriceInputSerializer,
    BulkSetPricesInputSerializer,
    PriceSerializer,
    PriceHistorySerializer,
)
from apps.pricing.services.pricing_service import PricingService

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


@extend_schema_view(
    get=extend_schema(summary='Цены варианта', description='Возвращает цену для варианта товара.'),
    post=extend_schema(summary='Установить цену', description='Создаёт или обновляет цену варианта. Только для staff.',
        request=SetPriceInputSerializer, responses={200: PriceSerializer}),
)
class PriceDetailView(APIView):
    """
    GET  /api/v1/pricing/variants/<variant_id>/price/  — цена варианта
    POST /api/v1/pricing/variants/<variant_id>/price/  — установить цену

    IsAdminUser — только staff может менять цены.
    """
    permission_classes = (IsAdminUser,)

    def get(self, request, variant_id: int):
        """
        Получить цену варианта по ID.
        variant_id — из URL path (<int:variant_id>).
        """
        try:
            variant = ProductVariant.objects.get(pk=variant_id)
        except ProductVariant.DoesNotExist:
            raise NotFound('Вариант не найден.')

        price_obj = PricingService.get_price(variant)
        if price_obj is None:
            # Цена не задана — это не ошибка, а отсутствие данных → 404.
            raise NotFound('Цена не задана.')
        return Response(PriceSerializer(price_obj).data)

    def post(self, request, variant_id: int):
        """
        Установить цену варианта.
        variant_id — из URL (не из body!).
        changed_by=request.user — для аудита в PriceHistory.
        """
        try:
            variant = ProductVariant.objects.get(pk=variant_id)
        except ProductVariant.DoesNotExist:
            raise NotFound('Вариант не найден.')

        serializer = SetPriceInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        price_obj = PricingService.set_price(
            variant=variant,
            price=serializer.validated_data['price'],
            sale_price=serializer.validated_data.get('sale_price'),
            changed_by=request.user,       # Для PriceHistory.changed_by
            reason=serializer.validated_data.get('reason', ''),
        )
        return Response(PriceSerializer(price_obj).data)


@extend_schema_view(
    get=extend_schema(summary='История цен', description='История изменения цены варианта.'),
)
class PriceHistoryView(APIView):
    """
    GET /api/v1/pricing/variants/<variant_id>/history/  — история цен
    """
    permission_classes = (IsAdminUser,)

    def get(self, request, variant_id: int):
        """История изменений цены варианта (по убыванию даты)."""
        try:
            variant = ProductVariant.objects.get(pk=variant_id)
        except ProductVariant.DoesNotExist:
            raise NotFound('Вариант не найден.')

        history = PricingService.get_price_history(variant)
        return Response(PriceHistorySerializer(history, many=True).data)


@extend_schema_view(
    post=extend_schema(
        summary='Массовое обновление цен',
        description='Обновляет цены для нескольких вариантов. Только для staff.',
        request=BulkSetPricesInputSerializer,
    ),
)
class BulkPriceView(APIView):
    """
    POST /api/v1/pricing/prices/bulk/  — массовое обновление цен.

    ФОРМАТ ЗАПРОСА:
        { "prices": [
            {"variant_id": 1, "price": "100.00"},
            {"variant_id": 2, "price": "200.00", "sale_price": "150.00"}
        ]}

    @transaction.atomic в PricingService — все или ничего.
    """
    permission_classes = (IsAdminUser,)

    def post(self, request):
        serializer = BulkSetPricesInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        results = PricingService.bulk_set_prices(
            prices_data=serializer.validated_data['prices'],
            changed_by=request.user,
        )
        return Response(PriceSerializer(results, many=True).data)
