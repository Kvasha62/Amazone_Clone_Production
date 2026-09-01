# ────────────────────────────────────────────────────────────────────────
# apps/inventory/models/stock_movement.py — запись о движении товара на складе.
#
# БИЗНЕС-СМЫСЛ:
#   StockMovement = одна запись в аудиторском журнале склада:
#     «12.06.2026 14:30 | SKU-A | +100 шт | Приёмка от поставщика»
#     «12.06.2026 15:00 | SKU-A | -30 шт  | Резерв под заказ ORD-000123»
#     «12.06.2026 18:00 | SKU-A | +30 шт  | Освобождение (отмена ORD-000123)»
#
# АРХИТЕКТУРНЫЙ ПРИНЦИП «Event Sourcing lite»:
#   Каждое изменение остатков (Stock.quantity / reserved_quantity)
#   сопровождается записью в StockMovement → полный аудит.
#   Без этого: кто изменил остатки? Когда? Почему? → неизвестно.
#
# ТИПЫ ДВИЖЕНИЙ (kind):
#   IN        — поступление от поставщика (приёмка)
#   OUT       — списание при доставке (физическое списание)
#   RESERVE   — резерв под подтверждённый заказ
#   RELEASE   — освобождение резерва (отмена заказа)
#   ADJUSTMENT— ручная корректировка (инвентаризация, брак)
#
# 📖 https://martinfowler.com/eaaDev/EventSourcing.html
# 📖 https://docs.djangoproject.com/en/stable/ref/models/fields/#choices
#
# ЧТО БУДЕТ, ЕСЛИ УДАЛИТЬ ФАЙЛ:
#   • Нет аудита движений → невозможно расследовать расхождения
#   • Потеря истории для аналитики (скорость продаж, оборачиваемость)
# ────────────────────────────────────────────────────────────────────────

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models

from apps.core.models.base_model import BaseModel
from apps.inventory.constants import MAX_MOVEMENT_NOTE_LENGTH


class MovementKind(models.TextChoices):
    """
    Типы движений склада.

    Каждый тип имеет чёткую семантику:
      • IN        — товар принят от поставщика (quantity += delta)
      • OUT       — товар отгружен клиенту (quantity -= delta, reserved -= delta)
      • RESERVE   — товар забронирован под заказ (reserved += delta)
      • RELEASE   — бронь снята (reserved -= delta)
      • ADJUSTMENT— ручная корректировка (quantity ± delta)

    📖 https://docs.djangoproject.com/en/stable/ref/models/fields/#enumeration-types
    """
    IN = 'in', 'Поступление'
    OUT = 'out', 'Списание (отгрузка)'
    RESERVE = 'reserve', 'Резервирование'
    RELEASE = 'release', 'Освобождение резерва'
    ADJUSTMENT = 'adjustment', 'Корректировка'


class StockMovement(BaseModel):
    """
    Запись о движении товара на складе.

    ПОЛЯ:
      stock        — FK к Stock (какой вариант)
      kind         — тип движения (IN / OUT / RESERVE / RELEASE / ADJUSTMENT)
      delta        — изменение (положительное или отрицательное число)
      quantity_before — остаток ДО движения (snapshot)
      quantity_after  — остаток ПОСЛЕ движения (snapshot)
      order        — опциональная ссылка на заказ (для RESERVE/RELEASE/OUT)
      performed_by — кто выполнил (пользователь или система)
      note         — комментарий к движению

    ПОЧЕМУ ХРАНИМ quantity_before / quantity_after:
      Snapshot на момент движения → можно восстановить историю:
        «Было 100 → стало 70 (резерв 30 под заказ ORD-000123)»
      Без snapshot: если quantity изменится позже →
      потеряем информацию о том, сколько БЫЛО на момент движения.

    📖 https://martinfowler.com/eaaDev/EventSourcing.html
    """

    # stock — FK к записи остатков.
    # on_delete=CASCADE — при удалении Stock удаляем все его движения.
    # related_name='movements' → stock.movements.all()
    stock = models.ForeignKey(
        'inventory.Stock',
        on_delete=models.CASCADE,
        related_name='movements',
        verbose_name='Остаток',
    )

    # kind — тип движения (TextChoices).
    kind = models.CharField(
        verbose_name='Тип движения',
        max_length=20,
        choices=MovementKind.choices,
        db_index=True,
    )

    # delta — величина изменения.
    # PositiveIntegerField — мы храним ВСЕГДА положительное число.
    # Направление определяется типом (kind):
    #   IN, RELEASE → увеличение
    #   OUT, RESERVE → уменьшение
    # ADJUSTMENT может быть любым → delta всегда ≥ 1,
    # направление задаётся через kind + context.
    delta = models.PositiveIntegerField(
        verbose_name='Величина изменения',
        validators=[MinValueValidator(1)],
    )

    # quantity_before — snapshot остатка ДО движения.
    # Позволяет отслеживать: «было 100 → стало 70».
    quantity_before = models.PositiveIntegerField(
        verbose_name='Остаток до',
    )

    # quantity_after — snapshot остатка ПОСЛЕ движения.
    quantity_after = models.PositiveIntegerField(
        verbose_name='Остаток после',
    )

    # order — опциональная ссылка на заказ.
    # null=True — не все движения связаны с заказом (приёмка, корректировка).
    # on_delete=SET_NULL — при удалении заказа движение остаётся в аудите.
    order = models.ForeignKey(
        'orders.Order',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='stock_movements',
        verbose_name='Заказ',
    )

    # performed_by — кто выполнил движение.
    # null=True — системные операции (автоматический reserve/release).
    # on_delete=SET_NULL — при удалении пользователя движение остаётся.
    performed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='stock_movements',
        verbose_name='Выполнил',
    )

    # related_movement — связь парных движений (PROD-003).
    # RELEASE ссылается на своё RESERVE, OUT ссылается на своё RESERVE.
    # Парность — основа идемпотентности release_stock()/commit_stock():
    # повторный или конкурентный вызов обрабатывает только «непарные»
    # RESERVE-движения и не может списать/освободить сток дважды.
    # null=True — одиночные движения (IN, ADJUSTMENT) и движения,
    # созданные до PROD-003.
    # on_delete=SET_NULL — при удалении исходного движения парное
    # остаётся в аудите.
    related_movement = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='counter_movements',
        verbose_name='Связанное движение',
    )

    # note — комментарий к движению.
    # Примеры: «Приёмка по накладной №12345», «Инвентаризация: найдено 5 бракованных»
    note = models.CharField(
        verbose_name='Примечание',
        max_length=MAX_MOVEMENT_NOTE_LENGTH,
        blank=True,
        default='',
    )

    class Meta:
        db_table = 'inventory_stockmovement'
        verbose_name = 'Движение склада'
        verbose_name_plural = 'Движения склада'
        ordering = ('-created_at',)

        indexes = [
            # Индекс по stock — для запросов «все движения данного варианта».
            models.Index(
                fields=['stock'],
                name='inventory_mvmt_stock_idx',
            ),
            # Индекс по kind — для аналитики: «сколько поступлений за месяц?»
            models.Index(
                fields=['kind'],
                name='inventory_mvmt_kind_idx',
            ),
            # Составной индекс (stock, created_at) — для хронологии:
            # «Последние 10 движений варианта X»
            models.Index(
                fields=['stock', '-created_at'],
                name='inv_mvmt_stock_cr_idx',
            ),
            # Индекс по order — для запроса «все движения заказа ORD-000123».
            models.Index(
                fields=['order'],
                name='inventory_mvmt_order_idx',
            ),
        ]

    def __str__(self):
        """
        «IN | SKU-A | +100 | 0 → 100 | 12.06.2026 14:30»
        """
        direction = '+' if self.kind in (
            MovementKind.IN, MovementKind.RELEASE, MovementKind.ADJUSTMENT,
        ) else '-'
        return (
            f'{self.get_kind_display()} | '
            f'{self.stock} | '
            f'{direction}{self.delta} | '
            f'{self.quantity_before} → {self.quantity_after}'
        )
