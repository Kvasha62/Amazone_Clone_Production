# ────────────────────────────────────────────────────────────
# Views для товаров (Product) — API-эндпоинты.
#
# ЧЕТЫРЕ ЭНДПОИНТА:
#   ProductListView    — GET  /api/v1/catalog/products/          (listing)
#   ProductDetailView  — GET  /api/v1/catalog/products/{id}/     (карточка)
#   ProductCreateView  — POST /api/v1/catalog/products/create/   (создание)
#   ProductUpdateView  — PATCH /api/v1/catalog/products/{uuid}/update/ (обновление)
#
# АРХИТЕКТУРНЫЙ ПРИНЦИП:
#   View → сериализатор (валидация) → сервис (бизнес-логика) → ORM (SQL)
#   View НЕ содержит бизнес-логику (только HTTP-обёртку).
#
# drf-spectacular — опциональная зависимость для генерации
# OpenAPI/Swagger документации. try/except — если библиотека
# не установлена, декораторы становятся no-op (пустышками).
# Это позволяет проекту работать без drf-spectacular.
#
# ЧТО БУДЕТ, ЕСЛИ УДАЛИТЬ ФАЙЛ:
#   Все 4 товарных эндпоинта → 404 URL (маршруты не найдут view).
# ────────────────────────────────────────────────────────────

# logging — для структурированного логирования.
import logging

# status — HTTP-статус коды (201 CREATED, 403 FORBIDDEN и т.д.).
# Используем константы вместо магических чисел: status.HTTP_201_CREATED
# вместо 201 — самодокументирующийся код.
from rest_framework import status
from rest_framework.exceptions import PermissionDenied

# Permission classes:
#   AllowAny — доступ без авторизации (публичный каталог).
#   IsAuthenticated — требует JWT-токен (создание/обновление товаров).
from rest_framework.permissions import AllowAny, IsAuthenticated

# Response — DRF-обёртка над HttpResponse с JSON-сериализацией.
# Response(data) → автоматически конвертирует dict/list в JSON.
from rest_framework.response import Response

# APIView — базовый класс DRF для API-эндпоинтов.
# Даёт: request.data (parsed body), request.query_params,
# автоматическую content-negotiation, exception handling.
from rest_framework.views import APIView

from apps.core.pagination import (
    build_paginated_response_data,
    ensure_deterministic_ordering,
    paginate_queryset,
    pagination_parameters,
)
from apps.core.serializers import PaginationResponseSerializer

# Product — модель товара (для повторного чтения после create/update).
from apps.catalog.models import Product

# Все сериализаторы товаров — для валидации и сериализации ответов.
from apps.catalog.serializers import (
    ProductListSerializer,      # output: listing
    ProductDetailSerializer,    # output: карточка
    ProductListQuerySerializer, # input: query-параметры
    CreateProductInputSerializer,  # input: POST body
    UpdateProductInputSerializer,  # input: PATCH body
)

# CatalogService — бизнес-логика (сервисный слой).
from apps.catalog.services.catalog_service import CatalogService

# drf-spectacular — опциональная зависимость.
# try/except: если пакет не установлен (pip install drf-spectacular),
# создаём заглушки-декораторы, которые ничего не делают.
# Без try/except: ImportError при старте Django, если пакет не установлен.
try:
    from drf_spectacular.utils import extend_schema, extend_schema_view
except ImportError:
    # Заглушка для extend_schema — просто возвращает функцию без изменений.
    # **kwargs — принимает любые именованные аргументы и игнорирует их.
    def extend_schema(**kwargs):
        def decorator(func):
            return func
        return decorator

    # Заглушка для extend_schema_view — аналогично.
    def extend_schema_view(**kwargs):
        def decorator(cls):
            return cls
        return decorator

# Логгер модуля для структурированного логирования.
logger = logging.getLogger(__name__)


# ==========================================================
# LISTING
# ==========================================================

# @extend_schema_view — декоратор drf-spectacular для документации.
# добавляет метаданные к OpenAPI-схеме для каждого HTTP-метода.
@extend_schema_view(
    get=extend_schema(
        # summary — краткое описание endpoint (видно в Swagger UI).
        summary='Каталог товаров',
        # description — подробное описание.
        description=(
            'Listing товаров с фильтрацией, поиском и сортировкой. '
            'Поддерживает пагинацию DRF.'
        ),
        # parameters — query-параметры для Swagger UI.
        # ProductListQuerySerializer покажет все параметры:
        # ?category, ?brand, ?min_price, ?max_price, ?search, ?ordering
        parameters=[ProductListQuerySerializer, *pagination_parameters()],
        responses={200: PaginationResponseSerializer},
    ),
)
class ProductListView(APIView):
    """
    GET /api/v1/catalog/products/

    Listing с фильтрами:
        ?category=phones&brand=nike&min_price=100&max_price=5000
        &search=iphone&ordering=-rating
    """

    # permission_classes — список классов проверок доступа.
    # AllowAny — любой пользователь (без JWT-токена).
    # Каталог публичный — незарегистрированные тоже видят товары.
    permission_classes = (AllowAny,)

    def get(self, request):
        """
        Обработка GET-запроса.

        ПОТОК ДАННЫХ:
            1. Валидация query-параметров (serializer)
            2. Получение QuerySet + фильтры (service)
            3. Пагинация (API-05 canonical envelope)
            4. Сериализация страницы (serializer)
            5. Возврат JSON с пагинацией
        """
        # ─── Шаг 1: Валидация query-параметров ───
        # request.query_params — dict-like объект: {'category': 'phones', ...}
        # data=request.query_params — передаём в сериализатор для валидации.
        # is_valid(raise_exception=True) — если валидация провалена,
        # выбрасывает ValidationError → DRF вернёт 400 с деталями ошибок.
        # Без raise_exception=True: is_valid() вернёт False, и нужно
        # вручную обрабатывать errors — больше кода, забудешь обработать.
        query_serializer = ProductListQuerySerializer(data=request.query_params)
        query_serializer.is_valid(raise_exception=True)

        # validated_data — словарь только валидных параметров.
        # Некорректные параметры уже отсечены is_valid().
        params = query_serializer.validated_data

        # ─── Шаг 2: Получение QuerySet + фильтры ───
        # Делегируем бизнес-логику сервису.
        # .get() с default=None — параметр может отсутствовать.
        queryset, applied_filters = CatalogService.get_product_listing(
            category_slug=params.get('category'),
            brand_slug=params.get('brand'),
            tag_slug=params.get('tag'),
            min_price=params.get('min_price'),
            max_price=params.get('max_price'),
            search_query=params.get('search'),
            ordering=params.get('ordering', '-created_at'),
        )

        # ─── Шаг 3: Пагинация ───
        # API-05: deterministic ordering. The service already applies the
        # whitelisted ordering; add ``pk`` as a stable tie-breaker before
        # slicing so page boundaries do not depend on unspecified DB order.
        current_ordering = list(getattr(queryset.query, 'order_by', []) or ['-created_at'])
        queryset = ensure_deterministic_ordering(queryset, current_ordering)
        page_items, meta = paginate_queryset(queryset, request)

        # ─── Шаг 4: Сериализация ───
        # many=True — сериализируем список (не один объект).
        # ProductListSerializer — минимальный набор полей для listing.
        serializer = ProductListSerializer(page_items, many=True)

        # ─── Шаг 5: Возврат с пагинацией ───
        return Response(
            build_paginated_response_data(request, serializer.data, meta),
        )


# ==========================================================
# DETAIL
# ==========================================================

@extend_schema_view(
    get=extend_schema(
        summary='Карточка товара',
        description='Полная информация о товаре: варианты, изображения, теги.',
    ),
)
class ProductDetailView(APIView):
    """
    GET /api/v1/catalog/products/{uuid}/

    Карточка товара с вариантами, изображениями, тегами.
    Ищет по UUID (public) или по slug (SEO-friendly).

    UUID:  550e8400-e29b-41d4-a716-446655440000
    Slug:  iphone-15-pro
    """

    # Публичный доступ — карточка товара видна всем.
    permission_classes = (AllowAny,)

    def get(self, request, identifier: str):
        """
        Обработка GET-запроса.

        identifier — UUID или slug (из URL path).
        Пробуем UUID первым — это основной способ.
        Если не UUID — значит slug.

        ПОЧЕМУ TRY/EXCEPT ДЛЯ UUID:
            uuid.UUID('not-a-uuid') → ValueError.
            Это не ошибка — просто identifier это slug, не UUID.
            ValueError — ОЖИДАЕМОЕ поведение, не исключительная ситуация.
        """
        # Пробуем UUID, затем slug
        # CatalogService — ссылка на класс сервиса (не экземпляр!).
        # Методы @staticmethod — не нужен self.
        service = CatalogService

        try:
            # Стандартный модуль Python uuid.
            # uuid.UUID(identifier) — парсит строку как UUID.
            # Если строка невалидный UUID → ValueError → except.
            # Lazy-импорт: модуль нужен только в этом месте.
            import uuid
            uuid.UUID(identifier)
            # Если дошли сюда — identifier это UUID.
            # get_product_by_uuid — ищет по UUID с for_card() prefetch.
            product = service.get_product_by_uuid(identifier)
        except ValueError:
            # ValueError = identifier НЕ UUID → пробуем как slug.
            # get_product_by_slug — ищет по slug с for_card() prefetch.
            product = service.get_product_by_slug(identifier)

        # Инкремент просмотров.
        # В проде лучше через Celery (асинхронно) — не блокировать ответ.
        # Пока делаем синхронно — для простоты.
        service.increment_product_views(product)

        # Сериализуем товар в JSON с полным набором полей.
        serializer = ProductDetailSerializer(product)
        return Response(serializer.data)


# ==========================================================
# CREATE (staff only)
# ==========================================================

@extend_schema_view(
    post=extend_schema(
        summary='Создать товар',
        description='Создание нового товара. Доступно только staff.',
        # request — тело запроса для Swagger UI (схема CreateProductInputSerializer).
        request=CreateProductInputSerializer,
    ),
)
class ProductCreateView(APIView):
    """
    POST /api/v1/catalog/products/create/

    Создание товара. Доступно только staff.
    Возвращает полную карточку созданного товара.
    """

    # IsAuthenticated — требует JWT-токен в заголовке:
    # Authorization: Bearer <token>
    # Без токена → 401 Unauthorized.
    permission_classes = (IsAuthenticated,)

    def post(self, request):
        """
        Обработка POST-запроса.

        ПОТОК ДАННЫХ:
            1. Проверка прав (is_staff)
            2. Валидация тела запроса (serializer)
            3. Создание товара (service)
            4. Перечитывание с prefetch
            5. Сериализация и возврат
        """
        # ─── Шаг 1: Проверка прав ───
        # is_staff — флаг пользователя Django (администратор).
        # Обычный пользователь (is_staff=False) → 403.
        # НЕ используем permission_classes=(IsAdminUser,) потому что:
        #   IsAdminUser проверяет is_staff, но не даёт кастомный ответ.
        #   Мы хотим вернуть {'detail': 'Недостаточно прав.'} на русском.
        if not request.user.is_staff:
            raise PermissionDenied('Недостаточно прав.')

        # ─── Шаг 2: Валидация тела запроса ───
        # request.data — parsed body (JSON → dict).
        input_serializer = CreateProductInputSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)

        # ─── Шаг 3: Создание через сервис ───
        # **validated_data — распаковка dict в keyword arguments:
        #   name='iPhone', brand_id=1, ...
        product = CatalogService.create_product(**input_serializer.validated_data)

        # ─── Шаг 4: Перечитывание с prefetch ───
        # ПОЧЕМУ НЕ ПРОСТО ВЕРНУТЬ product:
        #   create_product() возвращает объект без prefetch.
        #   ProductDetailSerializer обратится к product.brand → 1 SQL,
        #   product.images.all() → 1 SQL, и т.д.
        #   for_card() делает все prefetch в 1-2 запроса.
        #
        # ПОЧЕМУ for_card() БЕЗ ФИЛЬТРА ПО СТАТУСУ:
        #   Новый товар может быть DRAFT (не ACTIVE).
        #   get_product_by_uuid() фильтрует по ACTIVE → не найдёт DRAFT!
        #   Поэтому .get(pk=product.pk) — без фильтра по статусу.
        product = (
            Product.objects
            .for_card()
            .get(pk=product.pk)
        )

        # ─── Шаг 5: Сериализация и возврат ───
        serializer = ProductDetailSerializer(product)
        # HTTP_201_CREATED — стандартный код для создания ресурса.
        # 200 OK тоже сработает, но 201 — правильнее (RESTful convention).
        return Response(serializer.data, status=status.HTTP_201_CREATED)


# ==========================================================
# UPDATE (staff only)
# ==========================================================

@extend_schema_view(
    patch=extend_schema(
        summary='Обновить товар',
        description='Частичное обновление товара. Доступно только staff.',
        request=UpdateProductInputSerializer,
    ),
)
class ProductUpdateView(APIView):
    """
    PATCH /api/v1/catalog/products/{uuid}/update/

    Частичное обновление товара. Staff only.
    """

    permission_classes = (IsAuthenticated,)

    def patch(self, request, uuid: str):
        """
        Обработка PATCH-запроса.

        uuid — из URL path (Django конвертер <uuid:uuid>).
        Это ПОЛНОЦЕННЫЙ UUID с валидацией на уровне URL-роутинга.

        ПОЧЕМУ PATCH, А НЕ PUT:
            PUT = полное замещение (нужно передать ВСЕ поля).
            PATCH = частичное обновление (только изменённые поля).
            PUT без name → name станет null/blank — опасно!
            PATCH без name → name не меняется — безопасно.
        """
        # Проверка прав — аналогично ProductCreateView.
        if not request.user.is_staff:
            raise PermissionDenied('Недостаточно прав.')

        # Получаем товар по UUID (с for_card() prefetch).
        # get_product_by_uuid() проверяет status=ACTIVE.
        # ПОЧЕМУ: staff может обновлять только активные товары через этот endpoint.
        # DRAFT-товары обновляются через admin panel.
        product = CatalogService.get_product_by_uuid(uuid)

        # Валидация тела запроса.
        # UpdateProductInputSerializer — все поля optional (PATCH).
        input_serializer = UpdateProductInputSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)

        # Обновление через сервис.
        # **validated_data — только переданные поля (не None).
        product = CatalogService.update_product(
            product,
            **input_serializer.validated_data,
        )

        # Перечитываем с prefetch для сериализации.
        # После update prefetch-кэш объекта устарел —
        # нужно заново загрузить связи.
        product = CatalogService.get_product_by_uuid(str(product.uuid))

        serializer = ProductDetailSerializer(product)
        # 200 OK — стандартный код для успешного обновления.
        return Response(serializer.data)
