# ────────────────────────────────────────────────────────────────────────
# apps/shipping/signals.py — сигналы модуля доставки.
#
# Сигналы:
#   • on_shipment_created — логирование создания отправления
#   • on_shipment_status_changed — логирование изменения статуса
#
# 📖 https://docs.djangoproject.com/en/stable/ref/signals/
# ────────────────────────────────────────────────────────────────────────

import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.shipping.models import Shipment

logger = logging.getLogger(__name__)


@receiver(post_save, sender=Shipment)
def on_shipment_saved(sender, instance, created, **kwargs):
    """
    Сигнал при сохранении отправления.

    При создании — логирует создание с основными данными.
    При обновлении — лог не пишется (изменения логируются в сервисе).
    """
    if created:
        logger.info(
            'shipment_created_signal',
            extra={
                'shipment_id': instance.pk,
                'shipment_number': instance.shipment_number,
                'order_id': instance.order_id,
                'status': instance.status,
                'shipping_cost': str(instance.shipping_cost),
            },
        )
