# ────────────────────────────────────────────────────────────────────────
# apps/payments/models/payment_event.py — событие платежа (аудит-лог).
#
# БИЗНЕС-ТРЕБОВАНИЯ:
#   • Каждое изменение состояния платежа логируется как событие
#   • События НЕИЗМЕНЯЕМЫ (immutable) — только INSERT, нет UPDATE/DELETE
#   • Хранят: тип события, старый/новый статус, payload от провайдера,
#     кто инициировал, когда произошло
#
# АРХИТЕКТУРНОЕ РЕШЕНИЕ — EVENT LOG (Append-Only):
#   PaymentEvent — это Event Sourcing lite:
#     • Полная история платежа в хронологическом порядке
#     • Можно реконструировать состояние на любой момент времени
#     • Полезно для разбора споров (chargeback)
#     • Требуется для PCI DSS (аудит операций)
#
#   Альтернатива — просто обновлять status в Payment:
#     ✗ Теряем историю (что было ДО succeeded?)
#     ✗ Невозможно понять ПОЧЕМУ платёж перешёл в failed
#     ✗ Нет данных от провайдера (error code, decline reason)
#
# 📖 https://martinfowler.com/eaaDev/EventSourcing.html
# 📖 https://en.wikipedia.org/wiki/Append-only
# ────────────────────────────────────────────────────────────────────────

from django.conf import settings
from django.db import models

from apps.core.models.base_model import BaseModel
from apps.payments.constants import (
    MAX_EXTERNAL_ID_LENGTH,
    MAX_NOTE_LENGTH,
    PAYMENT_EVENT_CHOICES,
    PAYMENT_EVENT_STATUS_CHANGED,
)


class PaymentEvent(BaseModel):
    """
    Событие платежа — запись в аудит-логе.

    IMMUTABLE: создано один раз, никогда не обновляется.
    Все поля — read-only после создания.

    ИСПОЛЬЗУЕТСЯ ДЛЯ:
      • Истории платежа (web-интерфейс, API)
      • Отладки (почему платёж не прошёл?)
      • Реконсиляции (сверка с платёжной системой)
      • Legal / PCI DSS compliance
    """

    # ──────────────────────────────────────────────────────────────
    # Связь с платежом
    # ──────────────────────────────────────────────────────────────
    # on_delete=CASCADE — при удалении платежа удаляем все события.
    #   (Платёж удалён → его история не нужна.)
    # related_name='events' → payment.events.all()
    payment = models.ForeignKey(
        'payments.Payment',
        on_delete=models.CASCADE,
        related_name='events',
        verbose_name='Платёж',
    )

    # ──────────────────────────────────────────────────────────────
    # Тип события
    # ──────────────────────────────────────────────────────────────
    # Из PAYMENT_EVENT_CHOICES:
    #   created, status_changed, webhook_received, refund_initiated,
    #   refund_completed, cancelled, confirmed, callback_received, error
    event_type = models.CharField(
        verbose_name='Тип события',
        max_length=30,
        choices=PAYMENT_EVENT_CHOICES,
        db_index=True,
    )

    # ──────────────────────────────────────────────────────────────
    # Изменение статуса
    # ──────────────────────────────────────────────────────────────
    # old_status / new_status — для события status_changed.
    # Для других событий — пустые строки (blank).
    # ПОЧЕМУ НЕ NULL: status — CharField (TextChoices), '' = «не применимо».
    old_status = models.CharField(
        verbose_name='Предыдущий статус',
        max_length=20,
        blank=True,
        default='',
    )
    new_status = models.CharField(
        verbose_name='Новый статус',
        max_length=20,
        blank=True,
        default='',
    )

    # ──────────────────────────────────────────────────────────────
    # Данные от провайдера
    # ──────────────────────────────────────────────────────────────
    # payload — JSON с данными от платёжного провайдера.
    # Примеры:
    #   • YooKassa webhook: {"object": {"status": "succeeded", ...}}
    #   • Stripe callback: {"payment_intent": "pi_xxx", ...}
    #   • Error: {"code": "card_declined", "message": "Insufficient funds"}
    payload = models.JSONField(
        verbose_name='Данные события',
        blank=True,
        default=dict,
    )

    # external_event_id — ID события у провайдера.
    # Полезно для идемпотентности: если вебхук пришёл дважды —
    # проверяем external_event_id.
    external_event_id = models.CharField(
        verbose_name='Внешний ID события',
        max_length=MAX_EXTERNAL_ID_LENGTH,
        blank=True,
        default='',
    )

    # ──────────────────────────────────────────────────────────────
    # Кто инициировал
    # ──────────────────────────────────────────────────────────────
    # null=True — системные события (cron, webhook) без пользователя.
    # on_delete=SET_NULL — при удалении пользователя событие остаётся.
    performed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='payment_events',
        verbose_name='Инициатор',
    )

    # ──────────────────────────────────────────────────────────────
    # Комментарий
    # ──────────────────────────────────────────────────────────────
    note = models.TextField(
        verbose_name='Комментарий',
        blank=True,
        default='',
        max_length=MAX_NOTE_LENGTH,
    )

    class Meta:
        verbose_name = 'Событие платежа'
        verbose_name_plural = 'События платежей'
        ordering = ('created_at',)  # Хронологический порядок (ASC)
        indexes = [
            # Индекс (payment, event_type) — «все вебхуки для платежа X»
            models.Index(
                fields=['payment', 'event_type'],
                name='pay_event_payment_type_idx',
            ),
            # Индекс по event_type — «сколько вебхуков получено?»
            models.Index(
                fields=['event_type'],
                name='pay_event_type_idx',
            ),
        ]

    def __str__(self):
        return (
            f'PaymentEvent({self.event_type}) '
            f'for {getattr(self.payment, "payment_number", "?")} '
            f'@ {self.created_at:%Y-%m-%d %H:%M}'
        )
