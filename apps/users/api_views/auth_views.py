# ────────────────────────────────────────────────────────────────────────
# apps/users/api_views/auth_views.py — регистрация, смена пароля и выход.
#
# ЭНДПОИНТЫ:
#   POST /api/v1/auth/register/          — RegisterView
#   POST /api/v1/auth/change-password/   — ChangePasswordView
#   POST /api/v1/auth/logout/            — LogoutView
#
# 📖 https://www.django-rest-framework.org/api-guide/views/
# ────────────────────────────────────────────────────────────────────────

from rest_framework import serializers, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.settings import api_settings
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken
from rest_framework_simplejwt.tokens import RefreshToken, UntypedToken

from apps.users.models import User

try:
    from drf_spectacular.utils import extend_schema, extend_schema_view
except ImportError:
    def extend_schema(**kwargs):
        def decorator(func): return func
        return decorator
    def extend_schema_view(**kwargs):
        def decorator(cls): return cls
        return decorator


# ================================================================
# Сериализаторы (inline — используются только здесь)
# ================================================================

class RegisterInputSerializer(serializers.Serializer):
    email = serializers.EmailField()
    username = serializers.CharField(max_length=150)
    password = serializers.CharField(write_only=True, min_length=8)
    password_confirm = serializers.CharField(write_only=True, min_length=8)
    first_name = serializers.CharField(max_length=150, required=False, default='')
    last_name = serializers.CharField(max_length=150, required=False, default='')

    def validate(self, data):
        if data.get('password') != data.get('password_confirm'):
            raise serializers.ValidationError(
                {'password_confirm': 'Пароли не совпадают.'},
            )
        return data


class RegisterOutputSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    email = serializers.EmailField()
    username = serializers.CharField()
    first_name = serializers.CharField()
    last_name = serializers.CharField()


class ChangePasswordInputSerializer(serializers.Serializer):
    old_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True, min_length=8)
    new_password_confirm = serializers.CharField(write_only=True, min_length=8)

    def validate(self, data):
        if data.get('new_password') != data.get('new_password_confirm'):
            raise serializers.ValidationError(
                {'new_password_confirm': 'Пароли не совпадают.'},
            )
        return data


class LogoutInputSerializer(serializers.Serializer):
    """
    Валидация POST /auth/logout/.

    Body: {"refresh": "<refresh_token>"}
    """
    refresh = serializers.CharField(write_only=True)


# ================================================================
# RegisterView
# ================================================================

@extend_schema_view(
    post=extend_schema(
        summary='Регистрация',
        request=RegisterInputSerializer,
        responses={201: RegisterOutputSerializer},
    ),
)
class RegisterView(APIView):
    """POST /api/v1/auth/register/ — регистрация нового пользователя."""
    permission_classes = (AllowAny,)

    def post(self, request):
        input_ser = RegisterInputSerializer(data=request.data)
        if not input_ser.is_valid():
            # 🔴 Логируем только ошибки валидации БЕЗ password/password_confirm
            import logging
            logger = logging.getLogger(__name__)
            safe_errors = {
                k: v for k, v in input_ser.errors.items()
                if k not in ('password', 'password_confirm')
            }
            logger.warning("Register validation error: %s", safe_errors)
        input_ser.is_valid(raise_exception=True)
        data = input_ser.validated_data

        # Проверка уникальности email
        if User.objects.filter(email__iexact=data['email']).exists():
            return Response(
                {'email': 'Пользователь с таким email уже существует.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Проверка уникальности username
        if User.objects.filter(username=data['username']).exists():
            return Response(
                {'username': 'Пользователь с таким именем уже существует.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user = User.objects.create_user(
            username=data['username'],
            email=data['email'],
            password=data['password'],
            first_name=data.get('first_name', ''),
            last_name=data.get('last_name', ''),
        )

        output = RegisterOutputSerializer(user)
        return Response(output.data, status=status.HTTP_201_CREATED)


# ================================================================
# ChangePasswordView
# ================================================================

@extend_schema_view(
    post=extend_schema(
        summary='Смена пароля',
        request=ChangePasswordInputSerializer,
        responses={200: 'Password changed'},
    ),
)
class ChangePasswordView(APIView):
    """POST /api/v1/auth/change-password/"""
    permission_classes = (IsAuthenticated,)

    def post(self, request):
        input_ser = ChangePasswordInputSerializer(data=request.data)
        input_ser.is_valid(raise_exception=True)
        data = input_ser.validated_data

        user = request.user

        if not user.check_password(data['old_password']):
            return Response(
                {'old_password': 'Неверный текущий пароль.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user.set_password(data['new_password'])
        user.save()

        return Response({'detail': 'Пароль успешно изменён.'})


# ================================================================
# LogoutView
# ================================================================

@extend_schema_view(
    post=extend_schema(
        summary='Выход',
        description='Черный список переданного refresh-токена и отзыв права обновления токенов.',
        request=LogoutInputSerializer,
        responses={200: 'Logged out'},
    ),
)
class LogoutView(APIView):
    """
    POST /api/v1/auth/logout/

    API-03: logout blacklists the supplied refresh token. The access token is
    intentionally NOT blacklisted — it remains valid until its 15-minute
    expiration. The client is responsible for discarding local tokens.

    The endpoint is idempotent from the client's perspective: a refresh token
    that has already been blacklisted returns 200 without creating a new
    token family entry.
    """

    permission_classes = (IsAuthenticated,)

    def post(self, request):
        input_ser = LogoutInputSerializer(data=request.data)
        input_ser.is_valid(raise_exception=True)
        refresh_token = input_ser.validated_data['refresh']

        refresh = self._resolve_refresh_token(refresh_token)

        if refresh is None:
            # Already blacklisted -> client already cannot refresh from it.
            return Response({'detail': 'Выполнен выход.'})

        # Only the caller may revoke their own refresh capability.
        token_user_id = refresh.payload.get(api_settings.USER_ID_CLAIM)
        if str(token_user_id) != str(request.user.pk):
            raise InvalidToken(
                {'detail': 'Токен обновления не принадлежит текущему пользователю.'},
            )

        refresh.blacklist()
        return Response({'detail': 'Выполнен выход.'})

    @staticmethod
    def _resolve_refresh_token(refresh_token):
        """
        Resolve a valid (not-yet-blacklisted) RefreshToken instance.

        Returns None when the token is already blacklisted (idempotent logout).
        Raises InvalidToken (401) for malformed, expired, wrong-type or
        otherwise unusable refresh tokens.
        """
        try:
            return RefreshToken(refresh_token)
        except TokenError:
            # A blacklisted SimpleJWT refresh token is still cryptographically
            # valid, but RefreshToken rejects it during blacklist verification.
            # UntypedToken skips token-type verification and lets us detect the
            # already-revoked case without treating malformed/expired tokens as
            # a successful logout.
            try:
                untyped = UntypedToken(refresh_token)
            except TokenError:
                raise InvalidToken(
                    {'detail': 'Токен обновления недействителен или истёк.'},
                ) from None

            jti = untyped.get(api_settings.JTI_CLAIM)
            if jti and BlacklistedToken.objects.filter(token__jti=jti).exists():
                return None

            raise InvalidToken(
                {'detail': 'Токен обновления недействителен или истёк.'},
            ) from None
