# ────────────────────────────────────────────────────────────────────────
# api_views/password_reset_views.py
# API-эндпоинты для восстановления пароля (token-based).
#
# ЭНДПОИНТЫ:
#   POST /api/v1/auth/password-reset/
#     Body: {"email": "user@example.com"}
#     → Отправляет email с токеном для сброса пароля.
#     → 200 OK (всегда — чтобы не утекала информация о существовании email)
#
#   POST /api/v1/auth/password-reset/confirm/
#     Body: {"uid": "...", "token": "...", "new_password": "..."}
#     → Устанавливает новый пароль.
#     → 200 OK или 400 Bad Request
#
# БЕЗОПАСНОСТЬ:
#   - Token генерируется Django (PasswordResetTokenGenerator)
#   - Token действует 3 дня (PASSWORD_RESET_TIMEOUT = 259200)
#   - Email не раскрывает существование аккаунта (всегда 200)
#   - UID — base64-кодировка PK пользователя
#   - 🔴 Token и UID+token НИКОГДА не логируются
#   - 🔴 Пароль НИКОГДА не логируется
#
# 📖 https://docs.djangoproject.com/en/stable/topics/auth/default/#resetting-passwords
# ────────────────────────────────────────────────────────────────────────

import logging

try:
    from kombu.exceptions import OperationalError as KombuOperationalError
except ImportError:  # pragma: no cover - optional Celery dependency
    KombuOperationalError = None

try:
    from redis.exceptions import ConnectionError as RedisConnectionError
    from redis.exceptions import TimeoutError as RedisTimeoutError
except ImportError:  # pragma: no cover - optional Redis dependency
    RedisConnectionError = None
    RedisTimeoutError = None

from django.contrib.auth.tokens import default_token_generator
from django.utils.encoding import force_str
from django.utils.http import urlsafe_base64_decode
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.users.models import User

try:
    from drf_spectacular.utils import extend_schema
except ImportError:
    def extend_schema(**kwargs):
        def decorator(func):
            return func
        return decorator

logger = logging.getLogger(__name__)

# Expected infrastructure failures that trigger the synchronous email
# fallback. These are the concrete exceptions raised by the async enqueue /
# broker layer when Celery or its Redis transport is unavailable: missing
# optional dependencies (ImportError), OS-level connection failures
# (built-in ConnectionError), kombu transport failures
# (kombu.exceptions.OperationalError), and Redis client connection/timeout
# failures (redis.exceptions.*). RuntimeError is deliberately NOT included:
# Celery may wrap underlying broker failures in a broad RuntimeError, which
# is a generic programming-prone exception and must propagate to the API
# error boundary instead of being silently turned into sync email.
_CELERY_FALLBACK_ERRORS = (ImportError, ConnectionError)
if KombuOperationalError is not None:
    _CELERY_FALLBACK_ERRORS += (KombuOperationalError,)
if RedisConnectionError is not None:
    _CELERY_FALLBACK_ERRORS += (RedisConnectionError,)
if RedisTimeoutError is not None:
    _CELERY_FALLBACK_ERRORS += (RedisTimeoutError,)


# ── Serializers ─────────────────────────────────────────────

class PasswordResetRequestSerializer(serializers.Serializer):
    """Валидация запроса на сброс пароля."""
    email = serializers.EmailField()


class PasswordResetConfirmSerializer(serializers.Serializer):
    """Валидация подтверждения сброса пароля."""
    uid = serializers.CharField()
    token = serializers.CharField()
    new_password = serializers.CharField(min_length=8, max_length=128)
    new_password_confirm = serializers.CharField(min_length=8, max_length=128)

    def validate(self, attrs):
        if attrs['new_password'] != attrs['new_password_confirm']:
            raise serializers.ValidationError(
                {'new_password_confirm': 'Пароли не совпадают.'}
            )
        return attrs


# ── Views ───────────────────────────────────────────────────

@extend_schema(
    summary='Запросить сброс пароля',
    description='Отправляет email с токеном для сброса пароля. Всегда возвращает 200.',
    request=PasswordResetRequestSerializer,
)
class PasswordResetRequestView(APIView):
    """
    POST /api/v1/auth/password-reset/

    Отправляет email с токеном для сброса пароля.
    Всегда возвращает 200 OK — не раскрывает существование аккаунта.
    """

    permission_classes = (AllowAny,)

    def post(self, request):
        serializer = PasswordResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        email = serializer.validated_data['email']

        try:
            user = User.objects.get(email__iexact=email, is_active=True)
        except User.DoesNotExist:
            # Тихо — не раскрываем существование email
            return Response({'detail': 'Если email существует, письмо отправлено.'})

        if user.has_usable_password():
            # Генерируем uid и token
            from django.utils.http import urlsafe_base64_encode
            from django.utils.encoding import force_bytes

            uid = urlsafe_base64_encode(force_bytes(user.pk))
            token = default_token_generator.make_token(user)

            # Отправляем email через Celery task (если Celery доступен)
            # Fallback: отправляем синхронно через Django email backend
            try:
                from apps.notifications.tasks import send_password_reset_email
                send_password_reset_email.delay(user.pk, uid, token)
            except _CELERY_FALLBACK_ERRORS:
                # Ожидаемые сбои Celery/брокера (модуль недоступен или
                # брокер не отвечает) → синхронный fallback. Программные
                # ошибки (например, неверная сигнатура задачи) намеренно
                # пробрасываются, чтобы не маскировать проблему под 200.
                self._send_reset_email_sync(user, uid, token)

            # 🔴 НЕ логируем token, uid, или ссылку с токеном
            logger.info('Password reset requested for user %s', user.pk)

        # Всегда 200 — не утекает информация
        return Response({'detail': 'Если email существует, письмо отправлено.'})

    @staticmethod
    def _send_reset_email_sync(user, uid: str, token: str):
        """
        Синхронная отправка password reset email.

        Использует Django email backend (console в dev, SMTP в prod).
        Token передаётся в контекст шаблона, но НЕ логируется.
        """
        from django.core.mail import send_mail
        from django.conf import settings

        # Формируем ссылку сброса (frontend route)
        reset_url = (
            f"{getattr(settings, 'FRONTEND_URL', 'http://localhost:5173')}"
            f"/forgot-password?uid={uid}&token={token}"
        )

        send_mail(
            subject='Сброс пароля — Amazone Clone',
            message=(
                f'Здравствуйте, {user.get_full_name() or user.username}!\n\n'
                f'Вы запросили сброс пароля.\n'
                f'Перейдите по ссылке для установки нового пароля:\n'
                f'{reset_url}\n\n'
                f'Если вы не запрашивали сброс пароля, проигнорируйте это письмо.\n'
                f'Ссылка действительна 3 дня.'
            ),
            from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@amazone-clone.local'),
            recipient_list=[user.email],
            fail_silently=True,
        )


@extend_schema(
    summary='Подтвердить сброс пароля',
    description='Устанавливает новый пароль по токену из email.',
    request=PasswordResetConfirmSerializer,
)
class PasswordResetConfirmView(APIView):
    """
    POST /api/v1/auth/password-reset/confirm/

    Устанавливает новый пароль по uid + token из email.
    """

    permission_classes = (AllowAny,)

    def post(self, request):
        serializer = PasswordResetConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        uid = serializer.validated_data['uid']
        token = serializer.validated_data['token']
        new_password = serializer.validated_data['new_password']

        # Декодируем uid → user PK
        try:
            pk = force_str(urlsafe_base64_decode(uid))
            user = User.objects.get(pk=pk, is_active=True)
        except (TypeError, ValueError, OverflowError, User.DoesNotExist):
            raise ValidationError('Недействительная ссылка для сброса пароля.')

        # Проверяем токен
        if not default_token_generator.check_token(user, token):
            raise ValidationError('Недействительный или просроченный токен.')

        # Устанавливаем новый пароль
        # 🔴 User наследует AbstractUser (НЕ BaseModel) — НЕТ updated_at
        user.set_password(new_password)
        user.save(update_fields=['password'])

        # 🔴 НЕ логируем token или пароль
        logger.info('Password reset confirmed for user %s', user.pk)

        return Response({'detail': 'Пароль успешно изменён.'})
