# ────────────────────────────────────────────────────────────────────────
# apps/shipping/models/shipment.py — отправление (посылка).
#
# БИЗНЕС-ТРЕБОВАНИЯ:
#   • Привязка отправления к заказу (Order → Shipment)
#   • Способ доставки (ShippingMethod)
#   • Трек-номер для отслеживания
#   • Статусная машина: PREPARING → IN_TRANSIT → OUT_FOR_DELIVERY → DELIVERED
#   • Вес и габариты (опционально)
#   • Стоимость доставки (snapshot из ShippingMethod)
#
# АРХИТЕКТУРНЫЕ РЕШЕНИЯ:
#   • Один заказ = одно отправление (1:1)
#     (в реальном проекте может быть 1:N для многотомных заказов)
#   • shipping_cost — snapshot стоимости (не пересчитывается)
#   • ТРИ РАЗНЫХ ИДЕНТИФИКАТОРА (F-8, #73) — не путать:
#       – shipment_number   — КАНОНИЧЕСКИЙ ПУБЛИЧНЫЙ id (SHP-00000001),
#                             immutable, выдаётся SEQUENCE, адресует
#                             ресурс в URL и отдаётся в API;
#       – tracking_number   — ВНЕШНИЙ трек службы доставки (carrier),
#                             приходит извне, может меняться/отсутствовать;
#       – internal_tracking — ВНУТРЕННЕЕ складское поле, в публичные
#                             serializers НЕ попадает.
#
# 📖 https://docs.djangoproject.com/en/stable/ref/models/fields/#decimalfield
#
# ЧТО БУДЕТ, ЕСЛИ УДАЛИТЬ ФАЙЛ:
#   • Невозможно отслеживать отправления
#   • API трекинга → ImportError
# ────────────────────────────────────────────────────────────────────────

import logging
from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import connections, models, router

from apps.core.models.base_model import BaseModel
from apps.shipping.constants import (
    MAX_TRACKING_NUMBER_LENGTH,
    SHIPMENT_NUMBER_DIGITS,
    SHIPMENT_NUMBER_PREFIX,
    SHIPMENT_PREPARING,
    SHIPMENT_STATUS_CHOICES,
    SHIPMENT_STATUS_TRANSITIONS,
    SHIPMENT_TERMINAL_STATUSES,
    SHIPMENT_TRACKING_DIGITS,
    SHIPMENT_TRACKING_PREFIX,
)


logger = logging.getLogger(__name__)

# Имя PostgreSQL SEQUENCE для публичных номеров отправлений.
# Создаётся миграцией apps/shipping/migrations/0003_shipment_number.py
# и выставляется из MAX(_shipment_number_seq) — существующие отправления
# сохраняют свои номера (см. ADR-005: тот же механизм для order_number).
SHIPMENT_NUMBER_SEQUENCE = 'shipping_shipment_number_seq'


def format_shipment_number(sequence_value: int) -> str:
    """Форматирует числовое значение в публичный номер: ``SHP-00000001``."""
    return f'{SHIPMENT_NUMBER_PREFIX}-{sequence_value:0{SHIPMENT_NUMBER_DIGITS}d}'


def allocate_shipment_number(*, using: str | None = None) -> tuple[int, str]:
    """Выдаёт следующий номер отправления: ``(число, 'SHP-00000001')``.

    Механизм полностью повторяет ADR-005 (order_number): значение выдаёт
    PostgreSQL SEQUENCE через ``nextval()``, который атомарен на стороне
    СУБД. Прежняя схема ``SELECT MAX(...) + 1`` — read-then-increment:
    две параллельные вставки читали один MAX и получали один номер,
    превращая гонку в ``IntegrityError`` на UNIQUE.

    Откат транзакции расходует значение (nextval не транзакционен), поэтому
    в нумерации возможны gaps; гарантируются уникальность и монотонность.
    """
    connection = (
        connections[using] if using
        else connections[router.db_for_write(Shipment)]
    )
    sequence_value = _next_shipment_number_sequence(connection)
    return sequence_value, format_shipment_number(sequence_value)


def _next_shipment_number_sequence(connection) -> int:
    """Возвращает следующее числовое значение номера отправления."""
    if connection.vendor == 'postgresql':
        with connection.cursor() as cursor:
            # Имя SEQUENCE идёт параметром (nextval принимает regclass) —
            # конкатенации SQL нет.
            cursor.execute('SELECT nextval(%s)', [SHIPMENT_NUMBER_SEQUENCE])
            return int(cursor.fetchone()[0])

    # Fallback для бэкендов без SEQUENCE (SQLite — dev-режим, не production).
    logger.warning(
        'shipment_number_sequence_unsupported_backend',
        extra={'vendor': connection.vendor},
    )
    max_seq = Shipment.objects.using(connection.alias).aggregate(
        max_seq=models.Max('_shipment_number_seq'),
    )['max_seq'] or 0
    return max_seq + 1


def generate_internal_tracking() -> str:
    """
    Генерирует внутренний трек-номер: SHP-00000001.

    ФОРМАТ: {SHIPMENT_TRACKING_PREFIX}-{8 цифр, zero-padded}
    ПРИМЕР: SHP-00000001, SHP-00000123

    АЛГОРИТМ:
      1. Получить максимальный числовой суффикс из БД
      2. Прибавить 1
      3. Отформатировать с leading zeros

    THREAD-SAFETY:
      UniqueConstraint на internal_tracking → IntegrityError → retry.
      Для production предпочтительнее PostgreSQL SEQUENCE.
    """
    max_number = Shipment.objects.aggregate(
        max_num=models.Max('_tracking_seq'),
    )['max_num'] or 0

    next_num = max_number + 1
    return f'{SHIPMENT_TRACKING_PREFIX}-{next_num:0{SHIPMENT_TRACKING_DIGITS}d}'


class Shipment(BaseModel):
    """
    Отправление (посылка) — связывает заказ со способом доставки.

    Хранит информацию о трекинге, стоимости и статусе доставки.

    СВЯЗИ:
      • Order (OneToOne) — заказ, к которому привязано отправление
      • ShippingMethod (FK) — выбранный способ доставки
      • User (FK) — владелец (denormalized для быстрого доступа)
    """

    # ── Заказ (OneToOne) ──
    # Один заказ = одно отправление.
    # on_delete=CASCADE — если заказ удалён, отправление тоже.
    # related_name='shipment' → order.shipment — единственное отправление
    order = models.OneToOneField(
        'orders.Order',
        on_delete=models.CASCADE,
        related_name='shipment',
        verbose_name='Заказ',
    )

    # ── Пользователь (denormalized) ──
    # Копируем user из Order для быстрого доступа:
    #   Shipment.objects.filter(user=...)
    # без JOIN к orders_order.
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='shipments',
        verbose_name='Пользователь',
    )

    # ── Способ доставки ──
    method = models.ForeignKey(
        'shipping.ShippingMethod',
        on_delete=models.PROTECT,
        related_name='shipments',
        verbose_name='Способ доставки',
    )

    # ── Статус отправления (FSM) ──
    status = models.CharField(
        verbose_name='Статус',
        max_length=25,
        choices=SHIPMENT_STATUS_CHOICES,
        default=SHIPMENT_PREPARING,
        db_index=True,
    )

    # ── Внешний трек-номер (от службы доставки) ──
    # Заполняется после передачи посылки в службу доставки.
    # Пример: 1234567890 (СДЭК), RA123456789RU (Почта России)
    tracking_number = models.CharField(
        verbose_name='Трек-номер',
        max_length=MAX_TRACKING_NUMBER_LENGTH,
        blank=True,
        default='',
        db_index=True,
        help_text='Трек-номер от службы доставки.',
    )

    # ── Публичный номер отправления (F-8, #73) ──
    # КАНОНИЧЕСКИЙ публичный идентификатор ресурса Shipment: SHP-00000001.
    # Адресует отправление в URL и возвращается в payload.
    # Immutable (editable=False) и server-generated: значение выдаёт
    # SEQUENCE в save(), клиент его не задаёт и не меняет.
    shipment_number = models.CharField(
        verbose_name='Номер отправления',
        max_length=20,
        unique=True,
        editable=False,
        blank=True,
        help_text='Публичный номер отправления (SHP-00000001).',
    )

    # Числовой счётчик публичного номера (значение из SEQUENCE).
    # Храним отдельно, чтобы не парсить строку 'SHP-00000001'.
    _shipment_number_seq = models.PositiveBigIntegerField(
        editable=False,
        db_index=True,
        null=True,
        blank=True,
    )

    # ── Внутренний трек-номер ──
    # ВНУТРЕННЕЕ поле: не является публичным идентификатором и НЕ попадает
    # в публичные serializers (F-8, #73). Оставлено для внутренних
    # процессов склада и совместимости с исторической выдачей.
    # Автогенерируемый: SHP-00000001
    internal_tracking = models.CharField(
        verbose_name='Внутренний трек',
        max_length=20,
        unique=True,
        editable=False,
        blank=True,
    )

    # Внутренний числовой счётчик для генерации internal_tracking
    _tracking_seq = models.PositiveIntegerField(
        editable=False,
        default=0,
        db_index=True,
    )

    # ── Стоимость доставки (snapshot) ──
    # Копируется из ShippingMethod.calculate_cost() при создании.
    # Не пересчитывается — стоимость «заморожена» на момент оформления.
    shipping_cost = models.DecimalField(
        verbose_name='Стоимость доставки (₽)',
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0'))],
    )

    # ── Вес отправления (кг) ──
    # null = вес неизвестен (определяется при сборке).
    weight_kg = models.DecimalField(
        verbose_name='Вес (кг)',
        max_digits=8,
        decimal_places=3,
        null=True,
        blank=True,
        help_text='Фактический вес отправления в кг.',
    )

    # ── Комментарий / примечания ──
    notes = models.TextField(
        verbose_name='Примечания',
        blank=True,
        default='',
        help_text='Заметки о доставке (для склада / службы доставки).',
    )

    # ── Таймстампы переходов ──
    shipped_at = models.DateTimeField(
        verbose_name='Дата отправки',
        null=True,
        blank=True,
        db_index=True,
    )
    delivered_at = models.DateTimeField(
        verbose_name='Дата доставки',
        null=True,
        blank=True,
        db_index=True,
    )

    class Meta:
        verbose_name = 'Отправление'
        verbose_name_plural = 'Отправления'
        ordering = ('-created_at',)
        indexes = [
            models.Index(
                fields=['user', 'status'],
                name='shipment_user_status_idx',
            ),
            models.Index(
                fields=['status', 'created_at'],
                name='shipment_status_created_idx',
            ),
        ]

    def __str__(self):
        return (
            f'{self.shipment_number} '
            f'({self.get_status_display()}) '
            f'— Order #{self.order_id}'
        )

    @property
    def is_terminal(self) -> bool:
        """True если отправление в терминальном статусе."""
        return self.status in SHIPMENT_TERMINAL_STATUSES

    def save(self, *args, **kwargs):
        """
        Переопределённый save() — авто-генерация публичного номера и
        внутреннего трека.

        shipment_number выдаётся SEQUENCE (ADR-005-подобный механизм) и
        неизменяем: однажды присвоенный номер никогда не перезаписывается,
        потому что он является публичным идентификатором ресурса.
        """
        if not self.shipment_number:
            using = kwargs.get('using')
            seq_value, number = allocate_shipment_number(using=using)
            self._shipment_number_seq = seq_value
            self.shipment_number = number

        if not self.internal_tracking:
            max_seq = Shipment.objects.aggregate(
                max_seq=models.Max('_tracking_seq'),
            )['max_seq'] or 0
            self._tracking_seq = max_seq + 1
            self.internal_tracking = (
                f'{SHIPMENT_TRACKING_PREFIX}-'
                f'{self._tracking_seq:0{SHIPMENT_TRACKING_DIGITS}d}'
            )
        super().save(*args, **kwargs)
