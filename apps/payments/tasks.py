# ────────────────────────────────────────────────────────────────────────
# apps/payments/tasks.py — Celery-задачи для платежей.
#
# Фоновые задачи:
#   - cleanup_webhook_nonces — очистка использованных webhook nonce
#     старше retention (Issue #71 / API-01 F-6).
#
# PATTERNS:
#   Аналогично apps/cart/tasks.py — задача вызывает management-команду
#   через Django API (call_command). Beat-расписание — в config/celery.py
#   (каждые 15 минут; retention nonce ≈ 6 минут, поэтому накопление
#   между запусками невелико).
# ────────────────────────────────────────────────────────────────────────

from celery import shared_task


@shared_task(name='apps.payments.tasks.cleanup_webhook_nonces')
def cleanup_webhook_nonces():
    """
    Очистка использованных webhook nonce.

    Вызывает management-команду cleanup_webhook_nonces через Django API.
    Удаление безопасно: nonce гарантированно не может быть replay-нут
    после истечения retention (см. команду).
    """
    from django.core.management import call_command

    call_command('cleanup_webhook_nonces')
