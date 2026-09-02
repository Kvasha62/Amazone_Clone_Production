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
        # Импортируем signals для регистрации обработчиков.
        # Без этого @receiver декораторы не сработают.
        import apps.payments.signals  # noqa: F401
