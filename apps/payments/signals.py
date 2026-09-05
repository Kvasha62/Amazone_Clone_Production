# ────────────────────────────────────────────────────────────────────────
# apps/payments/signals.py — обработчики событий платежей.
#
# СИГНАЛЫ:
#   1. on_payment_created — логирование создания платежа
#   2. on_payment_status_changed — логирование смены статуса
#
# ПОЧЕМУ СИГНАЛЫ, А НЕ ВЫЗОВЫ В СЕРВИСЕ:
#   • Сервис содержит бизнес-логику (создание, подтверждение)
#   • Сигналы — побочные эффекты (логирование, уведомления)
#   • Разделение ответственности: сервис не знает про логирование
#
# 📖 https://docs.djangoproject.com/en/stable/ref/signals/
# ────────────────────────────────────────────────────────────────────────

import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.payments.models import Payment, PaymentEvent

logger = logging.getLogger(__name__)


@receiver(post_save, sender=Payment)
def on_payment_saved(sender, instance: Payment, created: bool, **kwargs):
    """
    Срабатывает после каждого сохранения платежа.

    ЧТО ДЕЛАЕТ:
      • Логирует факт создания/обновления платежа
      • НЕ создаёт PaymentEvent — это делает сервис
        (сигнал только для логирования и уведомлений)
    """
    if created:
        logger.info(
            'payment_created_signal',
            extra={
                'payment_id': instance.pk,
                'payment_number': instance.payment_number,
                'order_id': instance.order_id,
                'amount': str(instance.amount),
                'status': instance.status,
            },
        )
    else:
        logger.debug(
            'payment_updated_signal',
            extra={
                'payment_id': instance.pk,
                'payment_number': instance.payment_number,
                'status': instance.status,
            },
        )
