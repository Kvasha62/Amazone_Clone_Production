# ────────────────────────────────────────────────────────────
# api_views/product_brief_views.py
# Эндпоинт для «Недавно просмотренных» товаров.
#
# GET /api/v1/catalog/products/by-slugs/
#   ?slugs=iphone-15-pro,samsung-galaxy-s24
#
# Возвращает список товаров (ProductListSerializer) по списку slug'ов.
# Используется фронтендом для recentlyViewedStore (localStorage slugs → products).
#
# ПОЧЕМУ ОТДЕЛЬНЫЙ ЭНДПОИНТ, А НЕ ФИЛЬТР В PRODUCT LISTING:
#   - Listing поддерживает ?category=, ?brand=, ?search=
#   - Фильтр по массиву slug'ов — нестандартный (DRF не из коробки)
#   - Отдельный endpoint чище, проще документировать
# ────────────────────────────────────────────────────────────

from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.catalog.models import Product
from apps.catalog.serializers import ProductListSerializer

try:
    from drf_spectacular.utils import extend_schema
except ImportError:
    def extend_schema(**kwargs):
        def decorator(func):
            return func
        return decorator


@extend_schema(
    summary='Товары по slug (Recently Viewed)',
    description=(
        'Возвращает список товаров по массиву slug\'ов. '
        'Используется для «Недавно просмотренных» товаров. '
        'Параметр: ?slugs=slug1,slug2,slug3 (максимум 20).'
    ),
)
class ProductBySlugsView(APIView):
    """
    GET /api/v1/catalog/products/by-slugs/?slugs=slug1,slug2,slug3

    Возвращает список товаров (ProductListSerializer) по slug'ам.
    Публичный доступ (AllowAny) — незарегистрированные тоже видят.
    Максимум 20 slug'ов за запрос (защита от abuse).
    """

    permission_classes = (AllowAny,)

    def get(self, request):
        """
        Обработка GET-запроса.

        ПОТОК:
            1. Парсим ?slugs=slug1,slug2,slug3
            2. Ограничиваем до 20
            3. SELECT с for_list() prefetch
            4. Сериализация и возврат
        """
        slugs_param = request.query_params.get('slugs', '')

        if not slugs_param:
            return Response([])

        # Парсим и валидируем
        slugs = [s.strip() for s in slugs_param.split(',') if s.strip()]
        slugs = slugs[:20]  # максимум 20 товаров

        if not slugs:
            return Response([])

        # Запрос с prefetch. `for_list()` отсутствует в текущем main и
        # раньше маскировался `except Exception` (endpoint всегда отдавал
        # пустой список). Используем существующий публичный queryset-путь
        # `visible().with_related()` и позволяем ошибкам БД/программным
        # ошибкам достичь существующего error boundary API.
        products = list(
            Product.objects.visible().with_related().filter(
                slug__in=slugs,
            )
        )

        # Сериализация
        serializer = ProductListSerializer(products, many=True)
        return Response(serializer.data)
