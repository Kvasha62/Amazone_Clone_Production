# ────────────────────────────────────────────────────────────────────────
# apps/payments/apps.py — конфигурация модуля платежей.
#
# PaymentsConfig — подкласс AppConfig для Django.
# ready() импортирует signals, чтобы обработчики подписались на события.
# 📖 https://docs.djangoproject.com/en/stable/ref/applications/#configuring-applications
# ────────────────────────────────────────────────────────────────────────

from django.apps import AppConfig


class PaymentsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.payments'
    verbose_name = 'Платёжная система'

    def ready(self):
        # Предварительно регистрируем отдельный audit alias ДО запуска
        # Django test database lifecycle. Это сохраняет отдельное
        # соединение для durable refund records, но не мутирует
        # ConnectionHandler во время теста.
        from django.db import connections

        alias = 'payments_audit'
        if alias not in connections.databases:
            connections.databases[alias] = {
                **connections.databases['default'],
                'TEST': {'MIRROR': 'default'},
            }

        # Импортируем signals для регистрации обработчиков.
        # Без этого @receiver декораторы не сработают.
        import apps.payments.signals  # noqa: F401
