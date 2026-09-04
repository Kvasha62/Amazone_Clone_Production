# ────────────────────────────────────────────────────────────────────────
# apps/analytics/api_views/analytics_views.py — API views для аналитики.
#
# ПОЛНЫЙ СПИСОК ЭНДПОИНТОВ:
#   GET /api/v1/analytics/dashboard/         — комплексный дашборд (staff)
#   GET /api/v1/analytics/sales/             — сводка продаж (staff)
#   GET /api/v1/analytics/sales/timeline/    — временной ряд (staff)
#   GET /api/v1/analytics/top-products/      — топ товаров (staff)
#   GET /api/v1/analytics/top-categories/    — топ категорий (staff)
#   GET /api/v1/analytics/top-customers/     — топ покупателей (staff)
#   GET /api/v1/analytics/conversion/        — конверсия (staff)
#   GET /api/v1/analytics/most-viewed/       — самые просматриваемые (staff)
#
# Все endpoints требуют IsAdminUser — аналитика доступна только staff.
#
# 📖 https://www.django-rest-framework.org/api-guide/views/
# ────────────────────────────────────────────────────────────────────────

import logging

from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from rest_framework.exceptions import ValidationError

from apps.analytics.constants import PERIOD_CHOICES
from apps.analytics.serializers import AnalyticsDateRangeSerializer
from apps.analytics.services.analytics_service import AnalyticsService

_VALID_METRICS = {'revenue', 'quantity'}
_VALID_PERIODS = {choice[0] for choice in PERIOD_CHOICES}


def _parse_limit(request, default: int = 10) -> int:
    """Parse ``limit`` query param — integer >= 1, default 10.

    Raises ``ValidationError`` so the API-04 handler emits the canonical
    ``400`` envelope.  No upper bound is enforced here to preserve
    backward-compatible valid behaviour for large limits; the service
    slices with ``[:limit]`` and large values simply return at most the
    available rows.
    """
    raw = request.query_params.get('limit')
    if raw is None:
        return default
    if isinstance(raw, (list, tuple)):
        raw = raw[-1]
    raw_str = str(raw).strip()
    if not raw_str:
        raise ValidationError({'limit': 'Должно быть целым числом.'})
    try:
        value = int(raw_str)
    except (TypeError, ValueError):
        raise ValidationError({'limit': 'Должно быть целым числом.'})
    if value < 1:
        raise ValidationError({'limit': 'Должно быть не меньше 1.'})
    return value


def _parse_metric(request) -> str:
    """Parse ``metric`` query param — ``revenue`` or ``quantity``."""
    raw = request.query_params.get('metric', 'revenue')
    if isinstance(raw, (list, tuple)):
        raw = raw[-1]
    value = str(raw).strip()
    if value not in _VALID_METRICS:
        raise ValidationError(
            {'metric': 'Недопустимое значение metric. Допустимо: revenue, quantity.'}
        )
    return value


def _parse_period(request) -> str:
    """Parse ``period`` query param — one of ``PERIOD_CHOICES``."""
    raw = request.query_params.get('period', 'daily')
    if isinstance(raw, (list, tuple)):
        raw = raw[-1]
    value = str(raw).strip()
    if value not in _VALID_PERIODS:
        raise ValidationError(
            {
                'period': (
                    f'Недопустимое значение period. Допустимо: '
                    f'{", ".join(sorted(_VALID_PERIODS))}.'
                )
            }
        )
    return value

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


def _parse_days(request) -> int:
    """Парсит параметр days из query string (по умолчанию 30)."""
    input_ser = AnalyticsDateRangeSerializer(data=request.query_params)
    input_ser.is_valid(raise_exception=True)
    return input_ser.validated_data['days']


@extend_schema_view(
    get=extend_schema(summary='Комплексный дашборд (staff)'),
)
class DashboardView(APIView):
    """
    GET /api/v1/analytics/dashboard/

    Возвращает все ключевые метрики в одном запросе.
    Оптимизировано для рендеринга дашборда одним API-вызовом.
    """
    permission_classes = (IsAdminUser,)

    def get(self, request):
        days = _parse_days(request)
        data = AnalyticsService.get_dashboard(days=days)
        return Response(data)


@extend_schema_view(
    get=extend_schema(summary='Сводка продаж (staff)'),
)
class SalesSummaryView(APIView):
    """
    GET /api/v1/analytics/sales/

    Возвращает сводку продаж за период.
    Query params: ?days=30
    """
    permission_classes = (IsAdminUser,)

    def get(self, request):
        days = _parse_days(request)
        data = AnalyticsService.get_sales_summary(days=days)
        return Response(data)


@extend_schema_view(
    get=extend_schema(summary='Временной ряд продаж (staff)'),
)
class SalesTimelineView(APIView):
    """
    GET /api/v1/analytics/sales/timeline/

    Возвращает временной ряд продаж для графиков.
    Query params: ?days=30&period=daily
    """
    permission_classes = (IsAdminUser,)

    def get(self, request):
        days = _parse_days(request)
        period = _parse_period(request)
        data = AnalyticsService.get_sales_timeline(
            days=days, period=period,
        )
        return Response({'timeline': data})


@extend_schema_view(
    get=extend_schema(summary='Топ товаров (staff)'),
)
class TopProductsView(APIView):
    """
    GET /api/v1/analytics/top-products/

    Возвращает топ товаров по выручке или количеству.
    Query params: ?days=30&metric=revenue&limit=10
    """
    permission_classes = (IsAdminUser,)

    def get(self, request):
        days = _parse_days(request)
        metric = _parse_metric(request)
        limit = _parse_limit(request)
        data = AnalyticsService.get_top_products(
            days=days, metric=metric, limit=limit,
        )
        return Response(data)


@extend_schema_view(
    get=extend_schema(summary='Топ категорий (staff)'),
)
class TopCategoriesView(APIView):
    """
    GET /api/v1/analytics/top-categories/

    Возвращает топ категорий по выручке.
    Query params: ?days=30&limit=10
    """
    permission_classes = (IsAdminUser,)

    def get(self, request):
        days = _parse_days(request)
        limit = _parse_limit(request)
        data = AnalyticsService.get_top_categories(days=days, limit=limit)
        return Response(data)


@extend_schema_view(
    get=extend_schema(summary='Топ покупателей (staff)'),
)
class TopCustomersView(APIView):
    """
    GET /api/v1/analytics/top-customers/

    Возвращает топ покупателей по сумме заказов.
    Query params: ?days=30&limit=10
    """
    permission_classes = (IsAdminUser,)

    def get(self, request):
        days = _parse_days(request)
        limit = _parse_limit(request)
        data = AnalyticsService.get_top_customers(days=days, limit=limit)
        return Response(data)


@extend_schema_view(
    get=extend_schema(summary='Конверсия (staff)'),
)
class ConversionView(APIView):
    """
    GET /api/v1/analytics/conversion/

    Рассчитывает конверсию просмотры → заказы.
    Query params: ?days=30
    """
    permission_classes = (IsAdminUser,)

    def get(self, request):
        days = _parse_days(request)
        data = AnalyticsService.get_conversion_rate(days=days)
        return Response(data)


@extend_schema_view(
    get=extend_schema(summary='Самые просматриваемые товары (staff)'),
)
class MostViewedProductsView(APIView):
    """
    GET /api/v1/analytics/most-viewed/

    Возвращает самые просматриваемые товары за период.
    Query params: ?days=30&limit=10
    """
    permission_classes = (IsAdminUser,)

    def get(self, request):
        days = _parse_days(request)
        limit = _parse_limit(request)
        data = AnalyticsService.get_most_viewed_products(
            days=days, limit=limit,
        )
        return Response(data)
