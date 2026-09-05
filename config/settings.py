"""
Django settings for config project.
"""

import os
import sys
from datetime import timedelta
from pathlib import Path

# ────────────────────────────────────────────────────────────────────────
# .env — переменные окружения (django-cors-headers, python-dotenv)
#
# ПРОДАКШЕН-КОНТРАКТ (PROD-007 / F-11):
#   • DJANGO_DEBUG обязателен и явный. "true" → development, "false" →
#     production. Без значения или с невалидным — запуск падает
#     (ImproperlyConfigured), а не молча выбирает dev-конфигурацию.
#   • В production ОБЯЗАТЕЛЬНЫ: DJANGO_SECRET_KEY (не django-insecure-*),
#     DJANGO_ALLOWED_HOSTS (явный список, без "*"), и CORS не может быть
#     разрешающим (CORS_ALLOW_ALL_ORIGINS принудительно False).
#   • Development-дефолты (localhost CORS, "*" hosts) НИКОГДА не применяются
#     на production-пути.
# ────────────────────────────────────────────────────────────────────────

from dotenv import load_dotenv

# Загружаем .env если файл существует (silent=True — нет ошибки если нет)
load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/6.0/howto/deployment/checklist/

# ────────────────────────────────────────────────────────────────────────
# PROD-007 / F-11 — Production configuration contract (fail closed)
#
# The project boots in exactly one of two explicit modes:
#
#   • PRODUCTION  — DJANGO_DEBUG is explicitly "false"/"0"/"no"/"off"
#   • DEVELOPMENT — DJANGO_DEBUG is explicitly "true"/"1"/"yes"/"on"
#
# Production is the SAFE, EXPLICIT mode. DJANGO_DEBUG must be provided
# explicitly — there is no implicit default and no silent fallback to a
# development/unsafe configuration. A missing or invalid value fails fast
# with ImproperlyConfigured instead of guessing.
#
# On the production path the following security-sensitive settings CANNOT
# fall back to unsafe values:
#   - SECRET_KEY       required, and must not be a Django "django-insecure-*"
#                      placeholder;
#   - ALLOWED_HOSTS    an explicit, non-empty list that must not contain "*";
#   - CORS             CORS_ALLOW_ALL_ORIGINS is forced to False (never
#                      silently permissive).
#
# Development keeps convenient, explicit defaults (localhost CORS, "*" hosts)
# but those defaults are NEVER applied on the production path.
# ────────────────────────────────────────────────────────────────────────

from django.core.exceptions import ImproperlyConfigured

_UNSET = object()

_TRUE_TOKENS = frozenset({"1", "true", "yes", "on", "y"})
_FALSE_TOKENS = frozenset({"0", "false", "no", "off", "n"})


def _parse_bool(name, raw, *, default=_UNSET):
    """Deterministic boolean parsing (AC-5).

    Accepts only the explicit token sets above. Never infers truthiness from a
    value's mere presence (no ``bool(os.getenv(...))``). Raises
    ``ImproperlyConfigured`` on a missing required value or an invalid token.
    """
    if raw is None:
        if default is _UNSET:
            raise ImproperlyConfigured(
                f"{name} is required but was not provided. "
                f"Set it explicitly to one of "
                f"{sorted(_TRUE_TOKENS | _FALSE_TOKENS)}."
            )
        return default
    token = str(raw).strip().lower()
    if token in _TRUE_TOKENS:
        return True
    if token in _FALSE_TOKENS:
        return False
    raise ImproperlyConfigured(
        f"Invalid boolean value for {name}={raw!r}. "
        f"Expected one of {sorted(_TRUE_TOKENS | _FALSE_TOKENS)}."
    )


def _parse_host_list(name, raw, *, default=_UNSET, allow_wildcard=True):
    """Deterministic comma-separated list parsing (AC-5).

    Raises on a missing required value, an empty value, or (in production) a
    wildcard "*" entry.
    """
    if raw is None:
        if default is _UNSET:
            raise ImproperlyConfigured(
                f"{name} is required but was not provided."
            )
        return list(default)
    items = [item.strip() for item in str(raw).split(",")]
    items = [item for item in items if item]
    if not items:
        raise ImproperlyConfigured(
            f"{name} must contain at least one non-empty host."
        )
    if not allow_wildcard and "*" in items:
        raise ImproperlyConfigured(
            f"{name} may not contain the wildcard '*' in production."
        )
    return items


def _build_config(environ):
    """Build security-relevant configuration from ``environ``.

    ``environ`` must support ``.get(name, default)`` like ``os.environ``.
    Raises ``ImproperlyConfigured`` whenever production configuration is
    missing or unsafe. This is the single source of truth for the config
    contract and is exercised directly by the configuration tests.
    """
    debug = _parse_bool("DJANGO_DEBUG", environ.get("DJANGO_DEBUG"))

    # ── SECRET_KEY (AC-1) ──
    if debug:
        # Development convenience: explicit value preferred, safe fallback
        # only when none is given. Never used on the production path.
        secret_key = environ.get("DJANGO_SECRET_KEY") or (
            "django-insecure-DEV-ONLY-not-for-production-"
            "please-change-me-do-not-use-in-any-real-deployment"
        )
    else:
        secret_key = environ.get("DJANGO_SECRET_KEY")
        if not secret_key:
            raise ImproperlyConfigured(
                "DJANGO_SECRET_KEY is required in production. Generate one with: "
                "python -c \"import secrets; print(secrets.token_urlsafe(50))\""
            )
        if secret_key.startswith("django-insecure-"):
            raise ImproperlyConfigured(
                "DJANGO_SECRET_KEY must not be a Django-generated "
                "'django-insecure-*' placeholder in production."
            )

    # ── ALLOWED_HOSTS (AC-3) ──
    if debug:
        allowed_hosts = _parse_host_list(
            "DJANGO_ALLOWED_HOSTS",
            environ.get("DJANGO_ALLOWED_HOSTS"),
            default=["*"],
        )
    else:
        allowed_hosts = _parse_host_list(
            "DJANGO_ALLOWED_HOSTS",
            environ.get("DJANGO_ALLOWED_HOSTS"),
            allow_wildcard=False,
        )

    # ── CORS (AC-4) ──
    if debug:
        cors_allow_all = _parse_bool(
            "CORS_ALLOW_ALL_ORIGINS",
            environ.get("CORS_ALLOW_ALL_ORIGINS"),
            default=True,
        )
        cors_allowed_origins = _parse_host_list(
            "CORS_ALLOWED_ORIGINS",
            environ.get("CORS_ALLOWED_ORIGINS"),
            default=[
                "http://localhost:3000",
                "http://localhost:5173",
                "http://127.0.0.1:3000",
                "http://127.0.0.1:5173",
            ],
        )
    else:
        # Production must never be silently permissive.
        cors_allow_all = _parse_bool(
            "CORS_ALLOW_ALL_ORIGINS",
            environ.get("CORS_ALLOW_ALL_ORIGINS"),
            default=False,
        )
        if cors_allow_all:
            raise ImproperlyConfigured(
                "CORS_ALLOW_ALL_ORIGINS must not be enabled in production."
            )
        cors_allowed_origins = _parse_host_list(
            "CORS_ALLOWED_ORIGINS",
            environ.get("CORS_ALLOWED_ORIGINS"),
            default=[],
        )

    return {
        "DEBUG": debug,
        "SECRET_KEY": secret_key,
        "ALLOWED_HOSTS": allowed_hosts,
        "CORS_ALLOW_ALL_ORIGINS": cors_allow_all,
        "CORS_ALLOWED_ORIGINS": cors_allowed_origins,
    }


# Apply the configuration contract at import time.
_CONFIG = _build_config(os.environ)

# SECURITY WARNING: keep the secret key used for production secret!
SECRET_KEY = _CONFIG["SECRET_KEY"]

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = _CONFIG["DEBUG"]

ALLOWED_HOSTS = _CONFIG["ALLOWED_HOSTS"]

# Application definition

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",

    # Third-party
    "rest_framework",
    "rest_framework_simplejwt",
    "rest_framework_simplejwt.token_blacklist",  # 🔴 JWT blacklist — ROTATE_REFRESH_TOKENS + BLACKLIST_AFTER_ROTATION
    "django_filters",
    "treebeard",
    "drf_spectacular",
    "corsheaders",  # 🔴 CORS — React frontend support

    # Local apps
    "apps.core.apps.CoreConfig",
    "apps.users",
    "apps.catalog",
    "apps.inventory",
    "apps.pricing",
    "apps.cart",
    "apps.orders",
    "apps.payments",
    "apps.reviews",
    "apps.discounts",
    "apps.shipping",
    "apps.wishlist",
    "apps.notifications",
    "apps.analytics",
    "apps.currencies",
    "apps.merchants",
]

# ── PostgreSQL: единственная поддерживаемая БД ──
# django.contrib.postgres всегда нужен для SearchVectorField, GinIndex,
# partial indexes и CheckConstraint с Q-conditions.
# Добавляем безусловно — проект требует PostgreSQL (docker-compose.yml: postgres:18).
INSTALLED_APPS.insert(6, "django.contrib.postgres")

DB_ENGINE = os.getenv("DB_ENGINE", "django.db.backends.postgresql")

MIDDLEWARE = [
    # Outermost application middleware: every response and application log
    # gets a safe request/correlation id before it reaches the edge.
    "apps.core.middleware.RequestCorrelationMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",   # 🔴 CORS — ДО CommonMiddleware
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [os.path.join(BASE_DIR, "templates")],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# Database
# https://docs.djangoproject.com/en/6.0/ref/settings/#databases

# ── DATABASES — читаем из .env, fallback на SQLite ──
# DB_ENGINE уже определён выше (в INSTALLED_APPS-блоке).
# Для PostgreSQL — полный набор (FOR UPDATE, GinIndex, SearchVectorField).
# Для SQLite — ограниченный (нет row-locking, нет full-text search).
#
# PostgreSQL adapter (psycopg3 vs psycopg2):
#   Django 4.2+ автоматически определяет адаптер:
#     если установлен psycopg (v3) → использует его
#     иначе если установлен psycopg2 → использует его
#   ENGINE остаётся "django.db.backends.postgresql" в обоих случаях!
#
# psycopg3 преимущества:
#   • Активная разработка (psycopg2 — только багфиксы)
#   • Встроенный пул соединений (Django 5.1+ OPTIONS.pool)
#   • Поддержка PostgreSQL 18
#   • Быстрее: бинарный протокол, 2x throughput

if DB_ENGINE == "django.db.backends.postgresql":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.getenv("DB_NAME", "amazone_clone"),
            "USER": os.getenv("DB_USER", "postgres"),
            "PASSWORD": os.getenv("DB_PASSWORD", ""),
            "HOST": os.getenv("DB_HOST", "localhost"),
            "PORT": os.getenv("DB_PORT", "5432"),
            # ── psycopg3: пул соединений (Django 5.1+) ──
            # Раскомментируйте для продакшена:
            # "OPTIONS": {
            #     "pool": {
            #         "min_size": 4,
            #         "max_size": 16,
            #         "timeout": 10,
            #     },
            # },
            # ── Важно при pool: CONN_MAX_AGE = 0 ──
            # Пул сам управляет жизненным циклом соединений.
            # "CONN_MAX_AGE": 0,
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": os.path.join(BASE_DIR, "db.sqlite3"),
        }
    }

# Password validation
# https://docs.djangoproject.com/en/6.0/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

# Internationalization
# https://docs.djangoproject.com/en/6.0/topics/i18n/

LANGUAGE_CODE = "en-us"

TIME_ZONE = "UTC"

USE_I18N = True

USE_TZ = True

# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/6.0/howto/static-files/

# PROD-008 (architect review, BLOCKER 2): STATIC_URL — корневой абсолютный
# путь "/static/": в production static генерируется и раздаётся nginx'ом
# именно с этого префикса (location /static/). Относительное "static/"
# порождало бы относительные URL (static/admin/... вместо /static/admin/...)
# при обращении к вложенным путям. Django docs: STATIC_URL должен включать
# ведущий "/" (кроме случая CDN-домена).
STATIC_URL = "/static/"

# PROD-008 / F-12: целевой каталог `collectstatic` (production).
# Совпадает с контейнером `/app/staticfiles` в Dockerfile.backend.prod и
# docker-compose.prod.yml (volume static_data) и исключён из git
# (`.gitignore: staticfiles/`). В DEV не используется.
STATIC_ROOT = os.path.join(BASE_DIR, "staticfiles")

MEDIA_URL = "/media/"
# PROD-008 / F-12: единый путь медиа-файлов для dev и production.
# В контейнере — `/app/media` (volume media_data в docker-compose.prod.yml;
# volume media в docker-compose.yml). Ранее dev-compose монтировал volume
# в /app/uploads, что не совпадало с MEDIA_ROOT — исправлено (Issue #15, §6).
MEDIA_ROOT = os.path.join(BASE_DIR, "media")

# PROD-008 / F-12: доверенные origin для Django admin за https-прокси
# (nginx/LB). Опционально: пусто по умолчанию, явные значения в .env.
CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.getenv("CSRF_TRUSTED_ORIGINS", "").split(",")
    if origin.strip()
]

# ==========================================================
# 🔴 CORS — React Frontend Support
# ==========================================================
# django-cors-headers позволяет React (localhost:3000)
# делать запросы к Django (localhost:8000).
# Без CORS браузер заблокирует все XHR-запросы.
#
# 📖 https://github.com/adamchainz/django-cors-headers

# CORS configuration is derived from the production config contract defined
# above (see _build_config). In production CORS_ALLOW_ALL_ORIGINS is forced to
# False and may never be permissive; in development it defaults to True with
# explicit localhost origins.
CORS_ALLOW_ALL_ORIGINS = _CONFIG["CORS_ALLOW_ALL_ORIGINS"]
CORS_ALLOWED_ORIGINS = _CONFIG["CORS_ALLOWED_ORIGINS"]

# Разрешаем React отправлять JWT в заголовке Authorization
CORS_ALLOW_HEADERS = [
    "accept",
    "accept-encoding",
    "authorization",
    "content-type",
    "dnt",
    "origin",
    "user-agent",
    "x-csrftoken",
    "x-requested-with",
    "x-request-id",
    "x-correlation-id",
]

# Correlation is useful to browser clients too; only these non-secret bounded
# identifiers are exposed, never authorization or application data.
CORS_EXPOSE_HEADERS = ["X-Request-ID", "X-Correlation-ID"]

# Разрешаем куки (если понадобятся)
CORS_ALLOW_CREDENTIALS = True

# ==========================================================
# Django REST Framework
# ==========================================================

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    # 🟡 DEFAULT_RENDERER_CLASSES — только JSON (React не поймёт HTML)
    "DEFAULT_RENDERER_CLASSES": (
        "rest_framework.renderers.JSONRenderer",
    ),
    "DEFAULT_FILTER_BACKENDS": (
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.OrderingFilter",
    ),
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 20,
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "EXCEPTION_HANDLER": "apps.core.api_errors.exception_handler",
    # 🟡 DEFAULT_THROTTLE_CLASSES — защита от спама
    # В TESTING режиме throttle отключается (THROTTLE_RATES = None)
    "DEFAULT_THROTTLE_CLASSES": (
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ),
    "DEFAULT_THROTTLE_RATES": {
        "anon": os.getenv("THROTTLE_ANON", "60/min"),
        "user": os.getenv("THROTTLE_USER", "120/min"),
    },
}

# ==========================================================
# SimpleJWT
# ==========================================================

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=15),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    # 🔴 JWT login по EMAIL (не username)
    # SimpleJWT по умолчанию использует USERNAME_FIELD = "username".
    # Наш User использует email как USERNAME_FIELD —
    # но TokenObtainPairView проверяет authenticate(username=..., password=...).
    # Нужно указать, что поле для входа — email.
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "user_id",
    # API-03: inactive users are rejected on every authenticated request.
    # Access tokens are not individually blacklisted; the account check is the
    # immediate revocation boundary for deactivated accounts.
    "CHECK_USER_IS_ACTIVE": True,
    # API-03: existing JWTs and refresh tokens are NOT invalidated by password
    # change/reset. This is deliberate current behaviour; revocation is through
    # rotation, logout and expiry only (no token-family redesign in API-03).
    "CHECK_REVOKE_TOKEN": False,
}

# ==========================================================
# drf-spectacular (API documentation)
# ==========================================================

SPECTACULAR_SETTINGS = {
    "TITLE": "Amazone Clone API",
    "DESCRIPTION": "Marketplace API",
    # Existing version authority shared by OpenAPI and the health response.
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "ENUM_NAME_OVERRIDES": {
        # Health uses the same singleton enum for status and database.
        "HealthOkEnum": ["ok"],
    },
}

# ==========================================================
# Test Runner
# ==========================================================
# Кастомный runner решает проблему с Python 3.14,
# где unittest discover() некорректно импортирует
# вложенные пакеты tests/ внутри apps/*.
TEST_RUNNER = "config.test_runner.AppDiscoverRunner"

# ==========================================================
# Default primary key field type
# ==========================================================
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ==========================================================
# Custom User model
# ==========================================================
AUTH_USER_MODEL = "users.User"

# ==========================================================
# 🔴 Authentication Backends — login by email
# ==========================================================
# По умолчанию Django ищет по username.
# EmailOrUsernameModelBackend позволяет логин по email.
# ModelBackend — fallback для Django Admin (username).
# 📖 https://docs.djangoproject.com/en/stable/topics/auth/customizing/#writing-an-authentication-backend
AUTHENTICATION_BACKENDS = [
    "apps.users.backends.EmailOrUsernameModelBackend",
    "django.contrib.auth.backends.ModelBackend",
]

# ==========================================================
# TESTING — отключаем throttle
# ==========================================================
# В тестах throttle мешает (слишком много запросов за секунду).
# 📖 https://www.django-rest-framework.org/api-guide/testing/#setting-throttling-policy
# Определяем, запущены ли тесты.
# Способ 1: env var DJANGO_TESTING (устанавливается test_runner.py)
# Способ 2: sys.argv содержит 'test' (manage.py test)
# Оба способа нужны потому что settings.py загружается ДО test_runner.py,
# поэтому DJANGO_TESTING может быть ещё не установлен.
_is_testing = (
    _parse_bool("DJANGO_TESTING", os.getenv("DJANGO_TESTING"), default=False)
    or "test" in sys.argv
)

if _is_testing:
    REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"] = {
        "anon": None,
        "user": None,
    }

# ==========================================================
# 🔴 Payment Webhook Security — HMAC-SHA256
# ==========================================================
# Secret key for verifying payment provider webhooks.
# MUST be set in production via environment variable.
# Without it, all webhook requests are rejected (403).
#
# NEVER:
#   • use Django SECRET_KEY
#   • hardcode a real secret
#   • commit a real secret to the repository
#
# 📖 https://en.wikipedia.org/wiki/HMAC
PAYMENT_WEBHOOK_SECRET = os.getenv("PAYMENT_WEBHOOK_SECRET", "")

# ==========================================================
# Email Configuration
# ==========================================================
# Development: console backend (prints emails to stdout)
# Production: SMTP or django-anymail (set EMAIL_BACKEND in .env)
#
# NEVER commit real SMTP credentials to the repository.
EMAIL_BACKEND = os.getenv(
    "EMAIL_BACKEND",
    "django.core.mail.backends.console.EmailBackend" if DEBUG
    else "django.core.mail.backends.smtp.EmailBackend",
)
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", "noreply@amazone-clone.local")
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5173")

# ==========================================================
# Production observability (PROD-027 / F-19)
# ==========================================================
# Application, Django and Celery records are emitted as JSON lines to the
# existing container stream.  Docker Compose is the only routing/retention
# layer in the canonical deployment; no external logging service is assumed.
# The formatter has an allow-list for context fields and the middleware adds
# request/correlation ids without reading request bodies or sensitive headers.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "observability_context": {
            "()": "apps.core.observability.RequestContextFilter",
        },
    },
    "formatters": {
        "observability_json": {
            "()": "apps.core.observability.JSONFormatter",
        },
    },
    "handlers": {
        "observability_console": {
            "class": "logging.StreamHandler",
            "level": "INFO",
            "formatter": "observability_json",
            "filters": ["observability_context"],
        },
    },
    "loggers": {
        # Existing domain loggers keep their event names and safe operational
        # ids while gaining request/task context through the formatter.
        "apps": {
            "handlers": ["observability_console"],
            "level": "INFO",
            "propagate": False,
        },
        "django": {
            "handlers": ["observability_console"],
            "level": "INFO",
            "propagate": False,
        },
        "django.request": {
            "handlers": ["observability_console"],
            "level": "ERROR",
            "propagate": False,
        },
        "django.server": {
            "handlers": ["observability_console"],
            "level": "INFO",
            "propagate": False,
        },
        "celery": {
            "handlers": ["observability_console"],
            "level": "INFO",
            "propagate": False,
        },
        # Celery's default success trace includes the task return value.  The
        # lifecycle hooks above already record outcome/duration, so suppress
        # that potentially sensitive value while retaining warnings/errors.
        "celery.app.trace": {
            "handlers": ["observability_console"],
            "level": "WARNING",
            "propagate": False,
        },
    },
    "root": {
        "handlers": ["observability_console"],
        "level": "WARNING",
    },
}
