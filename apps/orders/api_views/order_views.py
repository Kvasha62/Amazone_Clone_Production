# ────────────────────────────────────────────────────────────────────────
# apps/orders/api_views/order_views.py — API views для заказов.
#
# ЧЕТЫРЕ ЭНДПОИНТА:
#   OrderListView      — GET    /api/v1/orders/                   (список заказов)
#                        POST   /api/v1/orders/                   (создать заказ)
#   OrderDetailView    — GET    /api/v1/orders/{order_number}/    (детали заказа)
#   OrderStatusView    — PATCH  /api/v1/orders/{order_number}/status/  (статус, staff)
#   OrderCancelView    — POST   /api/v1/orders/{order_number}/cancel/  (отмена)
#
# АРХИТЕКТУРА:
#   _OrderViewMixin — общая логика (получить заказ, проверить ownership)
#   Каждый view наследует Mixin + APIView → DRY.
#
# БЕЗОПАСНОСТЬ:
#   • IsAuthenticated — только авторизованные пользователи могут видеть заказы
#   • Ownership check — пользователь видит только свои заказы
#   • IsAdminUser — изменение статуса доступно только staff
#   • Throttling — защита от брутфорса
#
# 📖 https://www.django-rest-framework.org/api-guide/views/
# 📖 https://www.django-rest-framework.org/api-guide/throttling/
# 📖 https://www.django-rest-framework.org/api-guide/permissions/
#
# ЧТО БУДЕТ, ЕСЛИ УДАЛИТЬ ФАЙЛ:
#   Все 4 endpoints заказов → 404 (URL не найдёт view)
# ────────────────────────────────────────────────────────────────────────

import logging

from django.db.models import Count

from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView

from apps.core.pagination import (
    build_paginated_response_data,
    ensure_deterministic_ordering,
    paginate_queryset,
    pagination_parameters,
)
from apps.core.serializers import PaginationResponseSerializer

from apps.cart.models import Cart
from apps.cart.services.cart_service import CartService
from apps.orders.models import Order
from apps.orders.models.order import OrderStatus
from apps.orders.serializers import (
    CancelOrderInputSerializer,
    CreateOrderInputSerializer,
    OrderListSerializer,
    OrderSerializer,
    OrderStatusTransitionSerializer,
)
from apps.orders.services.order_service import OrderService

# drf-spectacular — опциональная зависимость для OpenAPI/Swagger.
# try/except: если пакет не установлен → декораторы-заглушки (no-op).
# 📖 https://drf-spectacular.readthedocs.io/en/latest/
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
# Throttle-класс — ограничение частоты запросов
# ==============================================================

class OrderUserThrottle(UserRateThrottle):
    """
    Throttle для запросов к API заказов.
    30/min — оформить 30 заказов в минуту нереально для нормального пользователя.
    Но бот с украденным JWT-токеном может спамить → ограничиваем.
    📖 https://www.django-rest-framework.org/api-guide/throttling/#userratethrottle
    """
    rate = '30/min'


# ==============================================================
# ОБЩАЯ ЛОГИКА (_OrderViewMixin)
# ==============================================================

class _OrderViewMixin:
    """
    Общая логика для всех order-view.

    Паттерн «Mixin» — класс с методами, который подмешивается
    к APIView через множественное наследование:
        class OrderListView(_OrderViewMixin, APIView)

    Методы:
      _get_order()      — получить заказ по order_number с ownership check
      _serialize_order() — сериализовать заказ в dict

    📖 https://docs.djangoproject.com/en/stable/topics/class-based-views/mixins/
    """

    # IsAuthenticated — только авторизованные.
    # Гость не может оформить/просмотреть заказы.
    permission_classes = (IsAuthenticated,)

    # throttle_classes НЕ задаём на уровне view —
    # чтобы @override_settings(DEFAULT_THROTTLE_CLASSES=[]) работал в тестах.
    # Throttling настраивается глобально в settings.py через REST_FRAMEWORK.
    # 📖 https://www.django-rest-framework.org/api-guide/throttling/

    def _get_order(self, request, order_number: str) -> Order:
        """
        Получает заказ по order_number с проверкой ownership.

        ЗАЩИТА ОТ IDOR:
          Пользователь может запросить чужой заказ по order_number.
          Проверяем: order.user == request.user.
          Если нет → 404 (не 403 — не раскрываем существование заказа).
          📖 https://cheatsheetseries.owasp.org/cheatsheets/Insecure_Direct_Object_Reference_Prevention_Cheat_Sheet.html

        select_related('user') — для __str__() и логирования без N+1.
        prefetch_related('items') — для сериализации позиций.
        """
        try:
            order = (
                Order.objects
                .with_items()
                .with_user()
                .get(order_number=order_number)
            )
        except Order.DoesNotExist:
            raise NotFound('Заказ не найден.')

        # Ownership check: обычный пользователь видит только свои заказы.
        # Admin/staff видит все.
        if not request.user.is_staff and order.user_id != request.user.pk:
            raise NotFound('Заказ не найден.')

        return order


# ==============================================================
# /api/v1/orders/ — список и создание заказов
# ==============================================================

@extend_schema_view(
    get=extend_schema(
        summary='Список заказов',
        description='Возвращает список заказов текущего пользователя.',
        parameters=pagination_parameters(),
        responses={200: PaginationResponseSerializer},
    ),
    post=extend_schema(
        summary='Оформить заказ',
        description='Создаёт заказ из текущей корзины.',
        request=CreateOrderInputSerializer,
        responses={201: OrderSerializer},
    ),
)
class OrderListView(_OrderViewMixin, APIView):
    """
    GET  /api/v1/orders/   — список заказов пользователя
    POST /api/v1/orders/   — оформить заказ из корзины

    IsAuthenticated — только авторизованные.
    POST требует активную корзину с товарами.
    """

    def get(self, request):
        """
        GET /api/v1/orders/

        ВОЗВРАЩАЕТ список заказов (краткий формат, без items).
        Аннотируем items_count для каждого заказа.

        ПОЧЕМУ OrderListSerializer, А НЕ OrderSerializer:
          Список из 50 заказов × 10 позиций = 500 объектов → медленно.
          OrderListSerializer = 1 запрос + items_count (annotation).
          OrderSerializer = 1 запрос + N prefetch (для деталей).
        """
        from apps.orders.serializers.order_serializers import OrderListSerializer

        orders = (
            Order.objects
            .for_user(request.user)
            .annotate(items_count=Count('items'))
            .select_related('user')
        )

        # API-05: deterministic ordering with a stable pk tie-breaker.
        orders = ensure_deterministic_ordering(orders, ['-created_at'])
        page_items, meta = paginate_queryset(orders, request)

        serializer = OrderListSerializer(page_items, many=True)
        return Response(
            build_paginated_response_data(request, serializer.data, meta),
        )

    def post(self, request):
        """
        POST /api/v1/orders/

        Создаёт заказ из текущей активной корзины.

        ПОТОК:
          1. Валидация body (CreateOrderInputSerializer)
          2. Получение/создание корзины
          3. OrderService.create_from_cart() — бизнес-логика
             (цену доставки считает сервер, не клиент)
          4. Сериализация и ответ (201 CREATED)

        ОШИБКИ:
          • Нет корзины → 404
          • Пустая корзина → 400
          • Нет адреса → 400
          • Сумма < MIN_ORDER_TOTAL → 400
          • delivery_cost в теле → 400 (F-08: поле больше не поддерживается,
            цена доставки вычисляется на сервере)
        """
        input_serializer = CreateOrderInputSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)

        # Получаем активную корзину пользователя.
        cart = CartService.get_or_create_cart(request)

        # Создаём заказ через сервис.
        # F-08: доставка сюда НЕ передаётся — цену доставки считает сервер
        # (ShippingService.calculate_order_delivery_cost), поэтому подменить
        # её через тело запроса невозможно.
        order = OrderService.create_from_cart(
            user=request.user,
            cart=cart,
            notes=input_serializer.validated_data.get('notes', ''),
        )

        # Перечитываем с prefetch для полной сериализации.
        order = Order.objects.with_items().get(pk=order.pk)

        return Response(
            OrderSerializer(order).data,
            status=status.HTTP_201_CREATED,
        )


# ==============================================================
# /api/v1/orders/{order_number}/ — детали заказа
# ==============================================================

@extend_schema_view(
    get=extend_schema(
        summary='Детали заказа',
        description='Возвращает полную информацию о заказе.',
        responses={200: OrderSerializer},
    ),
)
class OrderDetailView(_OrderViewMixin, APIView):
    """
    GET /api/v1/orders/{order_number}/

    Полная информация о заказе: позиции, адрес, суммы, статусы.
    Ownership check — пользователь видит только свои заказы.
    """

    def get(self, request, order_number: str):
        """
        GET /api/v1/orders/{order_number}/

        order_number — из URL path (str, не int!).
        Пример: /api/v1/orders/ORD-000001/
        """
        order = self._get_order(request, order_number)
        return Response(OrderSerializer(order).data)


# ==============================================================
# /api/v1/orders/{order_number}/status/ — изменение статуса (staff only)
# ==============================================================

@extend_schema_view(
    patch=extend_schema(
        summary='Изменить статус заказа',
        description=(
            'Переводит заказ в новый статус. '
            'Доступно только для staff/admin. '
            'Переходы валидируются по FSM.'
        ),
        request=OrderStatusTransitionSerializer,
        responses={200: OrderSerializer},
    ),
)
class OrderStatusView(APIView):
    """
    PATCH /api/v1/orders/{order_number}/status/

    Изменение статуса заказа. ТОЛЬКО для staff/admin.
    Переходы валидируются по FSM (Finite State Machine).

    ПОЧЕМУ ОТДЕЛЬНЫЙ ЭНДПОИНТ, А НЕ PATCH /orders/{id}/:
      • Явное действие → легче логировать и аудитить
      • Permission check отличается (IsAdminUser vs IsAuthenticated)
      • Разные сериализаторы (status vs full order)
    """

    # IsAdminUser — только staff/admin могут менять статус.
    # Обычный пользователь НЕ может подтвердить/отменить заказ через API.
    # (Отмена — через отдельный эндпоинт с проверкой ownership.)
    permission_classes = (IsAdminUser,)

    # throttle_classes НЕ задаём на уровне view —
    # чтобы @override_settings(DEFAULT_THROTTLE_CLASSES=[]) работал в тестах.
    # Throttling настраивается глобально в settings.py через REST_FRAMEWORK.

    def patch(self, request, order_number: str):
        """
        PATCH /api/v1/orders/{order_number}/status/

        ПОТОК:
          1. Найти заказ по order_number
          2. Валидация body (OrderStatusTransitionSerializer)
          3. CANCELLED → OrderService.cancel() (единственная точка отмены;
             coupon / inventory / payment coordination)
             иначе → OrderService.transition_status() — FSM
          4. Сериализация и ответ
        """
        try:
            order = Order.objects.get(order_number=order_number)
        except Order.DoesNotExist:
            raise NotFound('Заказ не найден.')

        input_serializer = OrderStatusTransitionSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)

        new_status = input_serializer.validated_data['status']
        # EDU-002: staff status endpoint must not bypass cancel() for
        # CANCELLED — otherwise PENDING coupon slots leak (times_used /
        # CouponUsage left active while order is cancelled).
        if new_status == OrderStatus.CANCELLED:
            order = OrderService.cancel(order, user=request.user)
        else:
            order = OrderService.transition_status(
                order,
                new_status,
                user=request.user,
            )

        # Перечитываем с prefetch.
        order = Order.objects.with_items().get(pk=order.pk)
        return Response(OrderSerializer(order).data)


# ==============================================================
# /api/v1/orders/{order_number}/cancel/ — отмена заказа
# ==============================================================

@extend_schema_view(
    post=extend_schema(
        summary='Отменить заказ',
        description=(
            'Отменяет заказ. Доступно для владельца заказа '
            '(если не в терминальном статусе) и для staff.'
        ),
        request=CancelOrderInputSerializer,
        responses={200: OrderSerializer},
    ),
)
class OrderCancelView(_OrderViewMixin, APIView):
    """
    POST /api/v1/orders/{order_number}/cancel/

    Отмена заказа. Доступна:
      • Владельцу заказа — если заказ не в терминальном статусе
      • Staff/admin — всегда

    ПОЧЕМУ ОТДЕЛЬНЫЙ ЭНДПОИНТ, А НЕ DELETE:
      DELETE — «удалить ресурс». Отмена — не удаление!
      Заказ остаётся в истории (CANCELLED).
      POST + глагол «cancel» — RESTful для не-CRUD операций.
    """

    def post(self, request, order_number: str):
        """
        POST /api/v1/orders/{order_number}/cancel/

        ПОТОК:
          1. Найти заказ с ownership check
          2. Валидация body (CancelOrderInputSerializer)
          3. OrderService.cancel() — бизнес-логика
          4. Сериализация и ответ
        """
        order = self._get_order(request, order_number)

        input_serializer = CancelOrderInputSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)

        order = OrderService.cancel(
            order,
            reason=input_serializer.validated_data.get('reason', ''),
            user=request.user,
        )

        # Перечитываем с prefetch.
        order = Order.objects.with_items().get(pk=order.pk)
        return Response(OrderSerializer(order).data)
