# ────────────────────────────────────────────────────────────────────────
# config/test_runner.py — кастомный test runner для совместимости.
#
# РЕШАЕТ ПРОБЛЕМУ:
#   Python 3.14+ изменил поведение unittest discover(),
#   из-за чего тесты внутри apps/*/tests/ не находятся.
#   Этот runner явно указывает какие модули сканировать.
#
# Совместимость:
#   Python 3.12, 3.13, 3.14, 3.15 — все поддерживаются.
#   Django 6.0, 6.1 — поддерживаются.
#
# 📖 https://docs.djangoproject.com/en/stable/topics/testing/advanced/#defining-a-test-runner
# ────────────────────────────────────────────────────────────────────────

import os
import unittest

# Отключаем throttle в тестах
os.environ["DJANGO_TESTING"] = "True"

from django.test.runner import DiscoverRunner


# Все app-ы, в которых есть тесты.
# Используем ТОЧНЫЙ путь «apps.xxx.tests» — чтобы discover()
# не пытался найти случайные директории-опечатки (tasts и т.п.).
TEST_APP_LABELS = [
    'config.tests',
    'apps.core.tests',
    'apps.users.tests',
    'apps.catalog.tests',
    'apps.pricing.tests',
    'apps.cart.tests',
    'apps.orders.tests',
    'apps.inventory.tests',
    'apps.payments.tests',
    'apps.reviews.tests',
    'apps.discounts.tests',
    'apps.shipping.tests',
    'apps.wishlist.tests',
    'apps.notifications.tests',
    'apps.analytics.tests',
]


class AppDiscoverRunner(DiscoverRunner):
    """
    Кастомный test runner, который явно указывает модули тестов
    вместо автоматического discovery по всему дереву проекта.

    Совместим с Python 3.12–3.15 и Django 6.0–6.1.
    """

    def setup_test_environment(self, **kwargs):
        super().setup_test_environment(**kwargs)

        # PROD-025 / F-18: уведомления планируют Celery-задачи после COMMIT
        # бизнес-транзакции. Брокера (Redis) в тестовом окружении нет, и
        # каждая реальная доставка задачи упиралась бы в retry бэкенда
        # результатов (~20 с на вызов), из-за чего прогон тестов становился
        # непригодным. Стандартный для Django+Celery подход: в тестах задачи
        # выполняются синхронно (eager), брокер не используется вовсе.
        # Продакшен-поведение не меняется: настройка применяется только
        # к процессу test runner'а.
        from config.celery import app as celery_app

        celery_app.conf.task_always_eager = True
        celery_app.conf.task_eager_propagates = True

    def build_suite(self, test_labels=None, **kwargs):
        if not test_labels:
            test_labels = TEST_APP_LABELS
        else:
            # Если переданы конкретные labels — тоже добавляем .tests
            resolved = []
            for label in test_labels:
                if not label.endswith('.tests'):
                    resolved.append(f'{label}.tests')
                else:
                    resolved.append(label)
            test_labels = resolved
        return super().build_suite(test_labels=test_labels, **kwargs)
