# ────────────────────────────────────────────────────────────────────────
# apps/cart/api_views/cart_views.py — API views для корзины.
#
# ПЯТЬ ЭНДПОИНТОВ:
#   CartView            — GET    /api/v1/cart/                    (получить корзину)
#                         DELETE /api/v1/cart/                    (очистить корзину)
#   CartItemView        — POST   /api/v1/cart/items/              (добавить товар)
#   CartItemDetailView  — PATCH  /api/v1/cart/items/{id}/         (изменить кол-во)
#                         DELETE /api/v1/cart/items/{id}/         (удалить позицию)
#   CartMergeView       — POST   /api/v1/cart/merge/              (слить гостевую)
#
# АРХИТЕКТУРА:
#   _CartViewMixin — общая логика (получить корзину, перечитать, сериализовать)
#   Каждый view наследует Mixin + APIView → DRY.
#
# THROTTLING:
#   CartAnonThrottle  — 30/min для анонимов
#   CartUserThrottle  — 120/min для авторизованных
#   Защита от брутфорса и DoS.
#
# 📖 https://www.django-rest-framework.org/api-guide/views/
# 📖 https://www.django-rest-framework.org/api-guide/throttling/
# 📖 https://www.django-rest-framework.org/api-guide/permissions/
#
# ЧТО БУДЕТ, ЕСЛИ УДАЛИТЬ ФАЙЛ:
#   Все 5 endpoints корзины → 404 (URL не найдёт view)
# ────────────────────────────────────────────────────────────────────────

# logging — структурированное логирование.
import logging

# status — HTTP-коды (201 CREATED, 200 OK, 400 BAD REQUEST, etc.).
# 📖 https://www.django-rest-framework.org/api-guide/status-codes/
from rest_framework import status
from rest_framework.exceptions import NotFound, ValidationError

# Permissions:
#   AllowAny — публичный доступ (без JWT-токена)
#   IsAuthenticated — требует авторизацию (для merge)
# 📖 https://www.django-rest-framework.org/api-guide/permissions/
from rest_framework.permissions import AllowAny, IsAuthenticated

# Response — JSON-обёртка DRF.
from rest_framework.response import Response

# APIView — базовый класс DRF для API-endpoints.
from rest_framework.views import APIView

# Throttle-классы — ограничение частоты запросов.
# 📖 https://www.django-rest-framework.org/api-guide/throttling/
from rest_framework.throttling import AnonRateThrottle, UserRateThrottle

# Cart — модель корзины (для перечитывания с prefetch).
from apps.cart.models import Cart

# Сериализаторы — валидация и сериализация.
from apps.cart.serializers import (
    AddToCartInputSerializer,
    CartSerializer,
    UpdateCartItemInputSerializer,
)

# CartService — бизнес-логика.
from apps.cart.services.cart_service import CartService

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


# ==========================================================
# Throttle-классы — ограничение частоты запросов
# ==========================================================

# AnonRateThrottle — throttle для анонимных пользователей.
# Определяется по IP-адресу.
# rate = '30/min' → максимум 30 запросов в минуту.
# 📖 https://www.django-rest-framework.org/api-guide/throttling/#anoneratethrottle
class CartAnonThrottle(AnonRateThrottle):
    """Throttle для анонимных запросов к корзине."""
    rate = '30/min'          # переопределяется в settings, если задано


# UserRateThrottle — throttle для авторизованных.
# Определяется по user.pk.
# 📖 https://www.django-rest-framework.org/api-guide/throttling/#userratethrottle
class CartUserThrottle(UserRateThrottle):
    """Throttle для авторизованных запросов к корзине."""
    rate = '120/min'


# ==========================================================
# ОБЩАЯ ЛОГИКА (_CartViewMixin)
# ==========================================================

class _CartViewMixin:
    """
    Общая логика для всех cart-view.

    Паттерн «Mixin» — класс с методами, который подмешивается
    к APIView через множественное наследование:
        class CartView(_CartViewMixin, APIView)

    Методы:
      _get_cart()       — получить/создать корзину из request
      _reload_cart()    — перечитать с prefetch для сериализации
      _serialize_cart() — сериализовать корзину в dict
      _respond_cart()   — вернуть Response с JSON корзины

    📖 https://docs.djangoproject.com/en/stable/topics/class-based-views/mixins/
    """

    # permission_classes = (AllowAny,) — публичный доступ.
    # Гость тоже может пользоваться корзиной!
    permission_classes = (AllowAny,)

    # throttle_classes — список throttle-классов.
    # DRF применяет ВСЕ throttle'ы: если ЛЮБОЙ превысил → 429 Too Many Requests.
    throttle_classes = (CartAnonThrottle, CartUserThrottle)

    def _get_cart(self, request) -> Cart:
        """
        Получить или создать корзину для текущего запроса.
        Делегирует CartService.get_or_create_cart().
        """
        return CartService.get_or_create_cart(request)

    def _reload_cart(self, cart: Cart) -> Cart:
        """
        Перечитывает корзину с prefetch для сериализации.

        ПОЧЕМУ НЕ ПРОСТО СЕРИАЛИЗОВАТЬ cart:
          После CartService.add_item() у cart устарел prefetch-кэш.
          Если сериализовать — items не будут содержать variant/product/price.
          with_items() делает полный prefetch → актуальные данные.

        ПОЧЕМУ with_items() БЕЗ active():
          Мы уже знаем PK корзины — фильтр по is_active не нужен.
          Более того: если корзина была деактивирована при merge →
          active() её не найдёт → ошибка!
        """
        # with_items() — метод QuerySet: prefetch items + variant + product + brand + price + stock
        return Cart.objects.with_items().get(pk=cart.pk)

    def _serialize_cart(self, cart: Cart) -> dict:
        """Сериализует корзину в dict (для Response)."""
        return CartSerializer(cart).data

    def _respond_cart(
        self,
        cart: Cart,
        *,
        reload: bool = True,
        status_code: int = status.HTTP_200_OK,
    ) -> Response:
        """
        Утилита: перезагружает (опционально) и возвращает Response.

        Параметры:
          cart — объект корзины (возможно устаревший)
          reload — перечитать с prefetch? (default True)
          status_code — HTTP-код ответа

        reload=False используется когда данные уже свежие
        (например, после merge view сама перезагружает).
        """
        if reload:
            cart = self._reload_cart(cart)
        return Response(self._serialize_cart(cart), status=status_code)


# ==========================================================
# /api/v1/cart/ — получение и очистка корзины
# ==========================================================

@extend_schema_view(
    get=extend_schema(
        summary='Получить корзину',
        description='Возвращает текущую корзину пользователя или гостя.',
        responses={200: CartSerializer},
    ),
    delete=extend_schema(
        summary='Очистить корзину',
        description='Удаляет все позиции из корзины.',
        responses={200: CartSerializer},
    ),
)
class CartView(_CartViewMixin, APIView):
    """
    GET    /api/v1/cart/   — получить корзину
    DELETE /api/v1/cart/   — очистить корзину

    AllowAny — гостевая корзина создаётся по session_key.
    """

    def get(self, request):
        """
        GET /api/v1/cart/

        ВОЗВРАЩАЕТ:
          {
            "id": 1,
            "items": [...],
            "total": "1500.00",
            "total_quantity": 3
          }
        """
        cart = self._get_cart(request)
        return self._respond_cart(cart)

    def delete(self, request):
        """
        DELETE /api/v1/cart/

        Очищает ВСЕ позиции. Корзина остаётся (is_active=True).
        Возвращает пустую корзину.
        """
        cart = self._get_cart(request)
        CartService.clear(cart)
        # После clear() — перечитываем для актуального ответа.
        return self._respond_cart(cart)


# ==========================================================
# /api/v1/cart/items/ — добавление товара
# ==========================================================

@extend_schema_view(
    post=extend_schema(
        summary='Добавить товар в корзину',
        description='Добавляет вариант товара или увеличивает количество.',
        request=AddToCartInputSerializer,
        responses={201: CartSerializer},
    ),
)
class CartItemView(_CartViewMixin, APIView):
    """
    POST /api/v1/cart/items/   — добавить вариант в корзину
        body: {"variant_id": 1, "quantity": 2}
    """

    def post(self, request):
        """
        Добавление товара в корзину.

        ПОТОК:
          1. Валидация body (AddToCartInputSerializer)
          2. Получение/создание корзины
          3. CartService.add_item() — бизнес-логика
          4. Перечитывание с prefetch
          5. Сериализация и ответ (201 CREATED)
        """
        # Валидация входных данных.
        # raise_exception=True → 400 если невалидно.
        input_serializer = AddToCartInputSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)

        cart = self._get_cart(request)
        # Добавляем товар через сервис (с проверками стока, лимитов и т.д.)
        CartService.add_item(
            cart=cart,
            variant_id=input_serializer.validated_data['variant_id'],
            quantity=input_serializer.validated_data['quantity'],
        )
        # 201 CREATED — стандартный HTTP-код для создания ресурса.
        return self._respond_cart(
            cart,
            status_code=status.HTTP_201_CREATED,
        )


# ==========================================================
# /api/v1/cart/items/<id>/ — обновление и удаление позиции
# ==========================================================

@extend_schema_view(
    patch=extend_schema(
        summary='Изменить количество',
        description='Обновляет количество единиц позиции.',
        request=UpdateCartItemInputSerializer,
        responses={200: CartSerializer},
    ),
    delete=extend_schema(
        summary='Удалить позицию',
        description='Удаляет позицию из корзины.',
        responses={200: CartSerializer},
    ),
)
class CartItemDetailView(_CartViewMixin, APIView):
    """
    PATCH  /api/v1/cart/items/<id>/  — изменить количество
        body: {"quantity": 5}
    DELETE /api/v1/cart/items/<id>/  — удалить позицию
    """

    def patch(self, request, item_id: int):
        """
        Обновление количества позиции.

        item_id — PK CartItem (из URL path, <int:item_id>).
        Сервис проверяет что item принадлежит корзине пользователя
        (защита от IDOR).
        """
        input_serializer = UpdateCartItemInputSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)

        cart = self._get_cart(request)
        CartService.update_item_quantity(
            cart=cart,
            item_id=item_id,
            quantity=input_serializer.validated_data['quantity'],
        )
        return self._respond_cart(cart)

    def delete(self, request, item_id: int):
        """
        Удаление позиции из корзины.

        Сервис проверяет ownership (item принадлежит корзине).
        """
        cart = self._get_cart(request)
        CartService.remove_item(cart=cart, item_id=item_id)
        return self._respond_cart(cart)


# ==========================================================
# /api/v1/cart/merge/ — слияние гостевой корзины (JWT auth)
# ==========================================================

@extend_schema_view(
    post=extend_schema(
        summary='Слить гостевую корзину',
        description=(
            'Переносит позиции из гостевой корзины в корзину '
            'текущего пользователя. Вызывать после получения JWT-токена. '
            'Session-key берётся из текущей сессии.'
        ),
        responses={200: CartSerializer},
    ),
)
class CartMergeView(APIView):
    """
    POST /api/v1/cart/merge/

    Явное слияние гостевой корзины в пользовательскую.

    ПОЧЕМУ НУЖЕН ОТДЕЛЬНЫЙ ЭНДПОИНТ:
      При JWT-авторизации сигнал user_logged_in НЕ срабатывает
      (JWT = stateless, нет session-based login).
      Поэтому frontend должен ЯВНО вызвать POST /cart/merge/
      после получения JWT-токена.

    ПОСЛЕДОВАТЕЛЬНОСТЬ ДЕЙСТВИЙ (frontend):
      1. Гость наполняет корзину → POST /cart/items/ (без токена)
      2. Гость логинится → POST /api/v1/users/login/ → JWT
      3. Frontend вызывает POST /cart/merge/ (с JWT в заголовке)
      4. Гостевая корзина сливается в юзерскую

    📖 https://django-rest-framework-simplejwt.readthedocs.io/en/latest/
    """

    # IsAuthenticated — только для авторизованных.
    # Гость не может «слить сам с собой» — нужен target user.
    permission_classes = (IsAuthenticated,)

    # Только UserRateThrottle — пользователь авторизован.
    throttle_classes = (CartUserThrottle,)

    def post(self, request):
        """
        Слияние гостевой корзины.

        ПОТОК:
          1. Проверить наличие session_key (у гостя была сессия)
          2. Вызвать CartService.merge_guest_into_user_cart()
          3. Вернуть обновлённую корзину пользователя
        """
        # session_key берётся из текущей сессии Django.
        # Если frontend не использует session middleware → session_key = None.
        session_key = request.session.session_key
        if not session_key:
            raise ValidationError('Сессия гостя не найдена.')

        # Вызываем сервис слияния.
        user_cart = CartService.merge_guest_into_user_cart(
            session_key, request.user,
        )
        if user_cart is None:
            # Нет гостевой корзины — нечего сливать.
            raise NotFound('Гостевая корзина не найдена.')

        # Сериализуем итоговую корзину с prefetch.
        cart = Cart.objects.with_items().get(pk=user_cart.pk)
        return Response(CartSerializer(cart).data)
