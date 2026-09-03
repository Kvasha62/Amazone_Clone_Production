# ────────────────────────────────────────────────────────────────────────
# config/celery.py — конфигурация Celery для Amazon Clone.
#
# Celery — распределённая очередь задач.
# Используется для:
#   - Асинхронной отправки email (подтверждение заказа, промокод)
#   - Периодической очистки старых корзин
#   - Пересчёта денормализованных данных (рейтинги, цены)
#   - Обновления поискового индекса
#
# BROKER: Redis (быстрый, поддерживает pub/sub + очередь)
# BACKEND: redis (результаты задач тоже в Redis)
#
# Запуск:
#   celery -A config worker --loglevel=info
#   celery -A config beat --loglevel=info
#   celery -A config flower   (мониторинг — http://localhost:5555)
# ────────────────────────────────────────────────────────────────────────

import os
from pathlib import Path

from celery import Celery

# Настройки Django — celery требует Django settings для @shared_task
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

# Redis URL: из env или дефолт
REDIS_URL = os.getenv('REDIS_URL', 'redis://localhost:6379/0')

# Создаём приложение Celery
app = Celery(
    'amazonclone',
    broker=REDIS_URL,
    backend=REDIS_URL,
)

# ── Конфигурация ──
app.conf.update(
    # Сериализация: JSON (не pickle — безопасность)
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',

    # Часовой пояс
    timezone='Europe/Moscow',
    enable_utc=True,

    # Результаты задач: хранить 1 час
    result_expires=3600,

    # Ограничение concurrent задач
    worker_concurrency=4,

    # Django owns the structured stdout/stderr handlers.  Do not let a Celery
    # worker replace them with a second root logger configuration.
    worker_hijack_root_logger=False,

    # Retry:Backoff
    task_default_retry_delay=30,  # секунд между retry
    task_max_retries=5,

    # Автообнаружение задач в apps/*/tasks.py
    task_routes={
        'apps.orders.tasks.*': {'queue': 'orders'},
        'apps.cart.tasks.*': {'queue': 'cart'},
        'apps.reviews.tasks.*': {'queue': 'reviews'},
    },
)

# PROD-027 / F-19: register request-context propagation and task lifecycle
# hooks using Celery's built-in signals.  The module logs no task args/results.
from apps.core import celery_observability as _celery_observability  # noqa: E402,F401

# Автообнаружение: Celery ищет tasks.py во всех Django-apps
app.autodiscover_tasks()


# ── Периодические задачи (Celery Beat) ──
from django.conf import settings  # noqa: E402

app.conf.beat_schedule = {
    # Очистка старых корзин — каждый день в 03:00
    'cleanup-old-carts-daily': {
        'task': 'apps.cart.tasks.cleanup_old_carts',
        'schedule': 60 * 60 * 24,  # каждые 24 часа
        'args': (),
    },

    # Напоминания о брошенных корзинах — каждый час
    'abandoned-cart-reminders': {
        'task': 'apps.cart.tasks.send_abandoned_cart_reminders',
        'schedule': 60 * 60,  # каждый час
        'args': (),
    },
}

if __name__ == '__main__':
    app.start()
