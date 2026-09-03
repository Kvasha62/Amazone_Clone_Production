# ────────────────────────────────────────────────────────────────────────
# apps/users/services/user_service.py — бизнес-логика пользователей.
#
# Service Layer Pattern:
#   View → сериализатор (валидация) → сервис (бизнес-логика) → ORM (SQL)
#
# МЕТОДЫ:
#   register()        — регистрация (email/username уникальность, хэш пароля)
#   update_profile()  — обновление данных User + UserProfile
#   change_password() — проверка старого пароля, установка нового
#   deactivate()      — мягкое удаление (is_active=False)
#   get_profile()     — получение профиля
#   get_user_by_id()  — получение пользователя
#
# ВАЖНОЕ ЗАМЕЧАНИЕ ПРО AbstractUser:
#   User наследует AbstractUser, НЕ BaseModel → у User НЕТ updated_at.
#   Поэтому user.save(update_fields=['first_name']) — БЕЗ 'updated_at'.
#   UserProfile наследует BaseModel → profile.save(update_fields=[..., 'updated_at']).
#
# 📖 https://docs.djangoproject.com/en/stable/topics/auth/customizing/
# 📖 https://docs.djangoproject.com/en/stable/topics/auth/passwords/
# 📖 https://martinfowler.com/eaaCatalog/serviceLayer.html
# ────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import logging

# get_user_model — возвращает РЕАЛЬНУЮ модель User (не обязательно auth.User).
# 📖 https://docs.djangoproject.com/en/stable/topics/auth/customizing/#django.contrib.auth.get_user_model
from django.contrib.auth import get_user_model

# transaction.atomic — SQL-транзакция (BEGIN / COMMIT / ROLLBACK).
# 📖 https://docs.djangoproject.com/en/stable/topics/db/transactions/#django.db.transaction.atomic
from django.db import transaction

# DRF-исключения → HTTP 400/404.
from rest_framework.exceptions import NotFound, ValidationError

# UserProfile — модель профиля (OneToOne к User).
from apps.users.models import UserProfile

logger = logging.getLogger(__name__)
User = get_user_model()


class UserService:
    """
    Сервис для работы с пользователями.

    Все mutating-методы обёрнуты в transaction.atomic.
    Исключения — DRF NotFound / ValidationError —
    view'хи прокидывают их в Response без лишних try/except.

    NB: User наследует AbstractUser, а НЕ BaseModel —
    у User нет поля updated_at (оно есть только у UserProfile и Address).
    """

    @staticmethod
    @transaction.atomic
    def register(
        *,
        email: str,
        username: str,
        password: str,
        first_name: str = '',
        last_name: str = '',
        phone: str = '',
    ) -> User:
        """
        Регистрация нового пользователя.

        АЛГОРИТМ:
          1. Нормализация email (домен → lowercase)
          2. Проверка уникальности email (case-insensitive)
          3. Проверка уникальности username (case-insensitive)
          4. Создание User с хэшированным паролем
          5. Создание пустого UserProfile

        ПОЧЕМУ ПРОВЕРЯЕМ УНИКАЛЬНОСТЬ В СЕРВИСЕ, А НЕ ПОЛАГАЕМСЯ НА БД:
          unique=True на EmailField → IntegrityError при дубликате.
          Но IntegrityError = 500 Internal Server Error для API.
          Мы проверяем ДО create и выбрасываем ValidationError = 400 Bad Request
          с дружелюбным сообщением на нужном языке.

        📖 https://docs.djangoproject.com/en/stable/topics/auth/passwords/#how-django-stores-passwords
        """
        # normalize_email — приводит домен к lowercase: 'Test@EXAMPLE.com' → 'Test@example.com'
        # Это стандартный метод BaseUserManager.
        # 📖 https://docs.djangoproject.com/en/stable/topics/auth/customizing/#django.contrib.auth.models.BaseUserManager.normalize_email
        email = User.objects.normalize_email(email)

        # Проверяем email уникальность (case-insensitive).
        # __iexact — WHERE email ILIKE 'test@example.com'
        # Без iexact: 'Test@Example.com' и 'test@example.com' — два разных email.
        if User.objects.filter(email__iexact=email).exists():
            raise ValidationError({
                'email': 'Пользователь с таким email уже существует.',
            })

        # Проверяем username уникальность.
        if User.objects.filter(username__iexact=username).exists():
            raise ValidationError({
                'username': 'Пользователь с таким именем уже существует.',
            })

        # Создаём объект User (в памяти, SQL ещё нет).
        user = User(
            email=email,
            username=username,
            first_name=first_name,
            last_name=last_name,
            phone=phone,
        )
        # set_password() — хэширует пароль через PBKDF2 (или argon2 если установлен).
        # НЕ сохраняет в БД — только устанавливает user.password = 'hash...'.
        # 📖 https://docs.djangoproject.com/en/stable/topics/auth/passwords/#using-hashing-libraries
        user.set_password(password)
        # full_clean() — вызывает все field validators + Model.clean().
        # Проверяет: max_length, blank, validators (например MinLengthValidator).
        # 📖 https://docs.djangoproject.com/en/stable/ref/models/instances/#django.db.models.Model.full_clean
        user.full_clean()
        # save() — INSERT INTO users_user (...) VALUES (...)
        user.save()

        # Создаём пустой профиль (дубль сигнала — для надёжности).
        # get_or_create — если профиль уже создан сигналом → возвращает существующий.
        # 📖 apps/users/signals.py — create_user_profile
        UserProfile.objects.get_or_create(user=user)

        # Email is deliberately not included: the event remains useful for
        # auditing while production telemetry avoids unnecessary PII.
        logger.info(
            'user_registered',
            extra={'user_id': user.pk},
        )
        return user

    @staticmethod
    @transaction.atomic
    def update_profile(
        user: User,
        *,
        first_name: str | None = None,
        last_name: str | None = None,
        phone: str | None = None,
        date_of_birth=None,
        gender: str | None = None,
        timezone: str | None = None,
        language: str | None = None,
        email_subscribed: bool | None = None,
    ) -> User:
        """
        Обновляет данные пользователя и профиля.
        None = «не менять» (PATCH semantics).

        РАЗДЕЛЕНИЕ ПОЛЕЙ:
          User (AbstractUser): first_name, last_name, phone — НЕТ updated_at
          UserProfile (BaseModel): date_of_birth, gender, timezone, ... — ЕСТЬ updated_at

        Поэтому сохраняем ДВУМЯ разными save() вызовами:
          user.save(update_fields=['first_name', ...])        — БЕЗ updated_at
          profile.save(update_fields=['timezone', ..., 'updated_at']) — С updated_at
        """
        # ── Обновляем поля User ──
        user_fields = []
        if first_name is not None:
            user.first_name = first_name
            user_fields.append('first_name')
        if last_name is not None:
            user.last_name = last_name
            user_fields.append('last_name')
        if phone is not None:
            user.phone = phone
            user_fields.append('phone')

        if user_fields:
            # AbstractUser НЕ имеет updated_at → НЕ добавляем его в update_fields.
            # 📖 https://docs.djangoproject.com/en/stable/ref/models/instances/#specifying-which-fields-to-save
            user.save(update_fields=user_fields)

        # ── Обновляем поля UserProfile ──
        # get_or_create — если профиль удалён → создаёт новый.
        # Возвращаем tuple (profile, created).
        profile, _ = UserProfile.objects.get_or_create(user=user)
        profile_fields = []
        if date_of_birth is not None:
            profile.date_of_birth = date_of_birth
            profile_fields.append('date_of_birth')
        if gender is not None:
            profile.gender = gender
            profile_fields.append('gender')
        if timezone is not None:
            profile.timezone = timezone
            profile_fields.append('timezone')
        if language is not None:
            profile.language = language
            profile_fields.append('language')
        if email_subscribed is not None:
            profile.email_subscribed = email_subscribed
            profile_fields.append('email_subscribed')

        if profile_fields:
            # BaseModel имеет updated_at → включаем в update_fields.
            profile.save(update_fields=profile_fields + ['updated_at'])

        # user.profile = profile — обновляем кэш в памяти.
        # Без этого: user.profile вернёт СТАРЫЕ данные (из предыдущего запроса).
        # С этим: user.profile → актуальные данные (для сериализации).
        user.profile = profile

        logger.info(
            'user_profile_updated',
            extra={'user_id': user.pk},
        )
        return user

    @staticmethod
    @transaction.atomic
    def change_password(user: User, *, old_password: str, new_password: str) -> None:
        """
        Смена пароля.

        ПОТОК:
          1. Проверить old_password (check_password — сравнивает хэши)
          2. Установить new_password (set_password — хэширует)
          3. Сохранить (update_fields=['password'] — только пароль)

        check_password — использует алгоритм из PASSWORD_HASHERS.
        По умолчанию PBKDF2 → 390 000 итераций → медленно → защита от брутфорса.
        📖 https://docs.djangoproject.com/en/stable/topics/auth/passwords/#how-django-stores-passwords
        """
        if not user.check_password(old_password):
            raise ValidationError({
                'old_password': 'Неверный текущий пароль.',
            })

        user.set_password(new_password)
        # update_fields=['password'] — обновляем ТОЛЬКО пароль.
        # Без: Django обновит ВСЕ поля (неэффективно, risk перезаписи).
        user.save(update_fields=['password'])

        logger.info(
            'user_password_changed',
            extra={'user_id': user.pk},
        )

    @staticmethod
    @transaction.atomic
    def deactivate(user: User) -> None:
        """
        Деактивация аккаунта (мягкое удаление).

        is_active=False → пользователь не может логиниться.
        Данные сохраняются для аналитики и возможного восстановления.
        Жёсткое удаление — только через admin panel.

        📖 https://docs.djangoproject.com/en/stable/ref/contrib/auth/#django.contrib.auth.models.User.is_active
        """
        user.is_active = False
        user.save(update_fields=['is_active'])

        logger.info(
            'user_deactivated',
            extra={'user_id': user.pk},
        )

    @staticmethod
    def get_profile(user: User) -> UserProfile:
        """
        Возвращает профиль пользователя или 404.

        ПОЧЕМУ UserProfile.objects.get(user=user), А НЕ user.profile:
          user.profile — использует related manager → может вернуть
          закэшированный (устаревший) объект.
          UserProfile.objects.get(user=user) — ВСЕГДА свежие данные из БД.
        """
        try:
            return UserProfile.objects.get(user=user)
        except UserProfile.DoesNotExist:
            raise NotFound('Профиль не найден.')

    @staticmethod
    def get_user_by_id(user_id: int) -> User:
        """Возвращает активного пользователя по ID или 404."""
        try:
            # is_active=True — деактивированные пользователи «не существуют» для API.
            return User.objects.get(pk=user_id, is_active=True)
        except User.DoesNotExist:
            raise NotFound('Пользователь не найден.')
