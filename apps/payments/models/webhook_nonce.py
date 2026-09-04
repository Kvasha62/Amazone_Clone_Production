# ────────────────────────────────────────────────────────────────────────
# apps/payments/models/webhook_nonce.py — использованный nonce вебхука.
#
# БИЗНЕС-ТРЕБОВАНИЕ (Issue #71 / API-01 F-6):
#   Payment webhook защищён HMAC-SHA256 + X-Webhook-Timestamp
#   + X-Webhook-Nonce. Nonce обязан использоваться ровно один раз,
#   поэтому сервер хранит каждый принятый nonce и отклоняет повтор.
#
# АРХИТЕКТУРНОЕ РЕШЕНИЕ:
#   • nonce — уникален на уровне БД (unique=True → UNIQUE-индекс).
#     Фиксация nonce = INSERT; повторный INSERT одного и того же nonce
#     завершается IntegrityError — это race-safe примитив: при
#     параллельных запросах выигрывает ровно один INSERT, проигравшие
#     получают IntegrityError (см.
#     apps.payments.services.webhook_security.claim_webhook_nonce).
#   • webhook_timestamp — timestamp из заголовка (epoch seconds).
#     Хранится, чтобы cleanup удалял только те nonce, которые
#     гарантированно больше не могут быть свежими (см.
#     WEBHOOK_NONCE_RETENTION_SECONDS).
#   • created_at — server-side creation timestamp (BaseModel):
#     время, когда сервер зафиксировал nonce.
#   • Очистка: management-команда cleanup_webhook_nonces + Celery task
#     (каждые 15 минут) удаляет nonce, для которых (now - webhook_timestamp)
#     превысило retention = freshness window + 60 c.
#
# 📖 https://docs.djangoproject.com/en/stable/ref/models/fields/
# ────────────────────────────────────────────────────────────────────────

from django.db import models

from apps.core.models.base_model import BaseModel
from apps.payments.constants import WEBHOOK_NONCE_MAX_LENGTH


class PaymentWebhookNonce(BaseModel):
    """
    Зафиксированный (использованный) nonce payment webhook.

    Строка появляется ровно один раз (INSERT при приёме webhook) и
    никогда не обновляется. Наличие строки = «этот nonce уже
    использован» → повторный webhook с ним отклоняется.

    Поле:
      nonce — значение X-Webhook-Nonce (уникальное, см. выше).
      webhook_timestamp — значение X-Webhook-Timestamp (epoch seconds),
        с которым nonce был принят.
    """

    # nonce — уникальный. unique=True создаёт UNIQUE-индекс
    # (payments_paymentwebhooknonce_nonce_key) — это и «DB index/
    # constraint для nonce», и атомарная race-safe фиксация:
    # два параллельных INSERT того же nonce → ровно один коммитится,
    # второй получает IntegrityError.
    nonce = models.CharField(
        verbose_name='Nonce',
        max_length=WEBHOOK_NONCE_MAX_LENGTH,
        unique=True,
    )

    # webhook_timestamp — timestamp самого webhook (epoch seconds,
    # значение из X-Webhook-Timestamp). Именно по нему cleanup считает,
    # гарантированно ли nonce «просрочен» по security policy:
    # nonce мог быть replay'нут только пока (now - webhook_timestamp)
    # было в пределах окна свежести.
    webhook_timestamp = models.BigIntegerField(
        verbose_name='Webhook timestamp',
    )

    class Meta:
        verbose_name = 'Webhook nonce'
        verbose_name_plural = 'Webhook nonces'
        ordering = ('created_at',)

        indexes = [
            # Индекс по webhook_timestamp — для cleanup-запроса:
            # «удали nonce с webhook_timestamp < cutoff».
            models.Index(
                fields=['webhook_timestamp'],
                name='webhook_nonce_ts_idx',
            ),
        ]

    def __str__(self):
        return f'PaymentWebhookNonce(ts={self.webhook_timestamp})'
