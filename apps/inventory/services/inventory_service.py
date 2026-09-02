# ────────────────────────────────────────────────────────────────────────
# apps/inventory/services/inventory_service.py — бизнес-логика склада.
#
# АРХИТЕКТУРНЫЙ ПРИНЦИП «Service Layer»:
#   View → сериализатор → сервис (бизнес-логика) → ORM (SQL)
#
# ОПЕРАЦИИ:
#   get_or_create_stock()  — получить/создать остатки для варианта
#   reserve_stock()        — зарезервировать под заказ
#   release_stock()        — освободить резерв (отмена)
#   commit_stock()         — списать при отгрузке/доставке
#   restock()              — пополнение от поставщика
#   adjust_stock()         — ручная корректировка
#   check_availability()   — проверить доступность
#
# БЕЗОПАСНОСТЬ КОНКУРЕНТНОГО ДОСТУПА:
#   Все mutating-методы используют select_for_update() →
#   PostgreSQL блокирует строку Stock до COMMIT → нет race conditions.
#
# 📖 Про Service Layer: https://martinfowler.com/eaaCatalog/serviceLayer.html
# 📖 Про select_for_update: https://docs.djangoproject.com/en/stable/ref/models/querysets/#select-for-update
# 📖 Про F-expressions: https://docs.djangoproject.com/en/stable/ref/models/expressions/#f-expressions
#
# ЧТО БУДЕТ, ЕСЛИ УДАЛИТЬ ФАЙЛ:
#   • Все API views склада → ImportError
#   • Невозможно резервировать/списывать/пополнять товар
# ────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import logging

from django.db import models, transaction

from rest_framework.exceptions import NotFound, ValidationError

from apps.inventory.constants import MAX_RESERVE_ITEMS
from apps.inventory.models import Stock, StockMovement
from apps.inventory.models.stock_movement import MovementKind

logger = logging.getLogger(__name__)


class InventoryService:
    """
    Бизнес-логика склада.

    Все mutating-методы обёрнуты в transaction.atomic и используют
    select_for_update() для исключения race conditions.
    """

    # ==============================================================
    # Получение / создание остатков
    # ==============================================================

    @staticmethod
    def get_or_create_stock(variant) -> Stock:
        """
        Возвращает Stock для варианта. Создаёт если не существует.

        OneToOneField с null=True → get() может упасть.
        Используем get_or_create() — атомарная операция.

        📖 https://docs.djangoproject.com/en/stable/ref/models/querysets/#get-or-create
        """
        stock, _ = Stock.objects.get_or_create(
            variant=variant,
            defaults={'quantity': 0, 'reserved_quantity': 0},
        )
        return stock

    @staticmethod
    def _locked_order(order):
        """SELECT ... FOR UPDATE по строке заказа (PROD-003).

        Сериализует резервирование/освобождение/списание ОДНОГО заказа
        между конкурентными вызовами: все операции начинаются с лока
        заказа и только потом лочат строки Stock (единый lock order
        Order → Stock исключает deadlock между этими путями). Блокировка
        держится до COMMIT; повторный захват в транзакции, уже
        удерживающей лок (OrderService.transition_status), безопасен.
        """
        from apps.orders.models import Order

        return Order.objects.select_for_update().get(pk=order.pk)

    @staticmethod
    @transaction.atomic
    def reconcile_order(order) -> dict:
        """Восстановить согласованность стока для заказа (PROD-003).

        Идемпотентно применяет недостающие операции по статусу заказа:
          • CONFIRMED / PROCESSING / SHIPPED без RESERVE → reserve_stock();
          • CANCELLED с непарными RESERVE → release_stock();
          • DELIVERED — если RESERVE потерян (сбой до резервирования),
            сначала reserve_stock(), затем commit_stock() (списание).
        Каждая операция сама по себе идемпотентна (movement-pairing),
        поэтому повторный запуск безопасен. Возвращает отчёт
        {'order_id', 'order_status', 'actions'}.
        """
        from apps.orders.models.order import OrderStatus

        locked = InventoryService._locked_order(order)
        status = locked.status
        actions = []

        if status in (
            OrderStatus.CONFIRMED,
            OrderStatus.PROCESSING,
            OrderStatus.SHIPPED,
        ):
            if InventoryService.reserve_stock(locked):
                actions.append('reserved')

        elif status == OrderStatus.CANCELLED:
            if InventoryService.release_stock(locked):
                actions.append('released')

        elif status == OrderStatus.DELIVERED:
            # PROD-003: у DELIVERED-заказа мог не сохраниться RESERVE
            # (сбой между переходом статуса и резервированием). Сначала
            # восстанавливаем резерв, затем списываем — иначе списание
            # было бы no-op и сток остался бы несниженным.
            has_reserve = StockMovement.objects.filter(
                order=locked,
                kind=MovementKind.RESERVE,
            ).exists()
            if not has_reserve:
                InventoryService.reserve_stock(locked)
            if InventoryService.commit_stock(locked):
                actions.append('committed')

        return {
            'order_id': locked.pk,
            'order_status': status,
            'actions': actions,
        }

    @staticmethod
    def get_available_quantity(variant) -> int:
        """
        Возвращает доступное количество для варианта.
        0 если Stock не существует.
        """
        try:
            stock = Stock.objects.get(variant=variant)
            return stock.available_quantity
        except Stock.DoesNotExist:
            return 0

    # ==============================================================
    # Резервирование под заказ (CONFIRMED)
    # ==============================================================

    @staticmethod
    @transaction.atomic
    def reserve_stock(order) -> list[StockMovement]:
        """
        Резервирует сток для всех позиций заказа.

        ВЫЗЫВАЕТСЯ ПРИ:
          Order.transition_status(CONFIRMED)

        АЛГОРИТМ:
          1. Для каждой позиции заказа (OrderItem):
             a. Получить/создать Stock для variant
             b. select_for_update — заблокировать строку
             c. Проверить available_quantity ≥ quantity
             d. Увеличить reserved_quantity на quantity
             e. Создать StockMovement(RESERVE)

        ВОЗВРАЩАЕТ:
          Список StockMovement (по одному на позицию)

        ВЫБРАСЫВАЕТ:
          ValidationError — если не хватает стока

        📖 https://docs.djangoproject.com/en/stable/ref/models/querysets/#select-for-update
        """
        from apps.orders.models import OrderItem

        # PROD-003: лок заказа сериализует все операции стока этого
        # заказа (Order → Stock); повторный/конкурентный reserve
        # одного заказа невозможен.
        InventoryService._locked_order(order)

        items = order.items.select_related('variant').all()
        if not items:
            return []

        # PROD-003: идемпотентность — если заказ уже зарезервирован,
        # повторный вызов ничего не делает (повторный increment
        # reserved_quantity невозможен).
        if StockMovement.objects.filter(
            order=order,
            kind=MovementKind.RESERVE,
        ).exists():
            return []

        movements = []
        for item in items:
            if not item.variant:
                # Вариант удалён — пропускаем (заказ уже immutable)
                logger.warning(
                    'reserve_skip_variant_deleted',
                    extra={'order_id': order.pk, 'item_id': item.pk},
                )
                continue

            # ── ВАЖНО: select_for_update() + get_or_create() — НЕСОВМЕСТИМЫ! ──
            # select_for_update() блокирует СУЩЕСТВУЮЩИЕ строки (SELECT … FOR UPDATE).
            # Если Stock не существует → get_or_create() делает INSERT,
            # но SELECT FOR UPDATE ничего не нашёл → IntegrityError или
            # «ни одна строка не заблокирована».
            #
            # ПРАВИЛЬНЫЙ АЛГОРИТМ:
            #   1. get_or_create() БЕЗ select_for_update (создаёт если нет)
            #   2. select_for_update().get() — блокируем СУЩЕСТВУЮЩУЮ строку
            #
            # 📖 https://docs.djangoproject.com/en/stable/ref/models/querysets/#select-for-update
            # 📖 https://code.djangoproject.com/ticket/33876
            stock, created = Stock.objects.get_or_create(
                variant=item.variant,
                defaults={'quantity': 0, 'reserved_quantity': 0},
            )
            # Теперь блокируем существующую строку до COMMIT.
            stock = Stock.objects.select_for_update().get(pk=stock.pk)

            available = stock.available_quantity
            if available < item.quantity:
                raise ValidationError({
                    'detail': (
                        f'Недостаточно товара {item.sku} на складе. '
                        f'Доступно: {available}, запрошено: {item.quantity}.'
                    ),
                })

            quantity_before = stock.quantity
            reserved_before = stock.reserved_quantity

            # F() expression — атомарное обновление на уровне SQL:
            # UPDATE inventory_stock SET reserved_quantity = reserved_quantity + N
            # Не загружаем текущее значение в Python → нет race condition.
            # 📖 https://docs.djangoproject.com/en/stable/ref/models/expressions/#f-expressions
            Stock.objects.filter(pk=stock.pk).update(
                reserved_quantity=models.F('reserved_quantity') + item.quantity,
            )

            # Создаём запись аудита.
            movement = StockMovement.objects.create(
                stock=stock,
                kind=MovementKind.RESERVE,
                delta=item.quantity,
                quantity_before=quantity_before,
                quantity_after=quantity_before,  # quantity не изменился
                order=order,
                note=f'Резерв под заказ {order.order_number}',
            )
            movements.append(movement)

            logger.info(
                'stock_reserved',
                extra={
                    'stock_id': stock.pk,
                    'variant_sku': item.sku,
                    'delta': item.quantity,
                    'order_id': order.pk,
                },
            )

        return movements

    # ==============================================================
    # Освобождение резерва (CANCELLED)
    # ==============================================================

    @staticmethod
    @transaction.atomic
    def release_stock(order) -> list[StockMovement]:
        """
        Освобождает зарезервированный сток при отмене заказа.

        ВЫЗЫВАЕТСЯ ПРИ:
          Order.transition_status(CANCELLED)

        АЛГОРИТМ:
          1. Найти все StockMovement(RESERVE) для данного заказа
          2. Для каждого — уменьшить reserved_quantity
          3. Создать StockMovement(RELEASE)

        ПОЧЕМУ ИЩЕМ ЧЕРЕЗ MOVEMENTS, А НЕ ЧЕРЕЗ OrderItem:
          Movements — source of truth для того, СКОЛЬКО было зарезервировано.
          OrderItem мог быть изменён после резервирования —
          movement.delta содержит точное значение.
        """
        # PROD-003: лок заказа (Order → Stock) сериализует release
        # против конкурентных release/commit/reserve этого заказа.
        InventoryService._locked_order(order)

        # PROD-003: обрабатываем только RESERVE-движения, у которых ещё
        # НЕТ парного RELEASE и НЕТ парного OUT. Повторный/конкурентный
        # вызов — no-op: повторный decrement reserved_quantity невозможен.
        # OUT-исключение закрывает гонку release↔commit: если commit
        # победил, release уже ничего не должен освобождать (иначе
        # reserved ушёл бы в минус и нарушился CHECK-инвариант).
        released_reserve_ids = StockMovement.objects.filter(
            order=order,
            kind=MovementKind.RELEASE,
            related_movement__isnull=False,
        ).values('related_movement_id')
        committed_reserve_ids = StockMovement.objects.filter(
            order=order,
            kind=MovementKind.OUT,
            related_movement__isnull=False,
        ).values('related_movement_id')
        reserve_movements = (
            StockMovement.objects
            .filter(order=order, kind=MovementKind.RESERVE)
            .exclude(pk__in=released_reserve_ids)
            .exclude(pk__in=committed_reserve_ids)
            .select_related('stock')
        )

        movements = []
        for mv in reserve_movements:
            stock = Stock.objects.select_for_update().get(pk=mv.stock_id)

            quantity_before = stock.quantity

            # Уменьшаем reserved на величину резерва.
            Stock.objects.filter(pk=stock.pk).update(
                reserved_quantity=models.F('reserved_quantity') - mv.delta,
            )

            movement = StockMovement.objects.create(
                stock=stock,
                kind=MovementKind.RELEASE,
                delta=mv.delta,
                quantity_before=quantity_before,
                quantity_after=quantity_before,
                order=order,
                related_movement=mv,
                note=f'Освобождение резерва (отмена {order.order_number})',
            )
            movements.append(movement)

            logger.info(
                'stock_released',
                extra={
                    'stock_id': stock.pk,
                    'delta': mv.delta,
                    'order_id': order.pk,
                },
            )

        return movements

    # ==============================================================
    # Списание при доставке (DELIVERED)
    # ==============================================================

    @staticmethod
    @transaction.atomic
    def commit_stock(order) -> list[StockMovement]:
        """
        Списывает физический сток и снимает резерв при доставке.

        ВЫЗЫВАЕТСЯ ПРИ:
          Order.transition_status(DELIVERED)

        АЛГОРИТМ:
          1. Найти все RESERVE-движения заказа
          2. Для каждого:
             a. Уменьшить quantity на delta (физическое списание)
             b. Уменьшить reserved_quantity на delta (снятие резерва)
             c. Создать StockMovement(OUT)

        ПОЧЕМУ СПИСЫВАЕМ И quantity, И reserved:
          При резервировании мы увеличили reserved, но не тронули quantity.
          Теперь товар ФИЗИЧЕСКИ покинул склад → quantity -= delta.
          Резерв больше не нужен → reserved -= delta.
          Итог: available = (quantity - delta) - (reserved - delta) = available — не изменился.
        """
        # PROD-003: лок заказа (Order → Stock) сериализует commit
        # против конкурентных release/commit/reserve этого заказа.
        InventoryService._locked_order(order)

        # PROD-003: списываем только RESERVE-движения, у которых ещё нет
        # парного OUT и которые не были освобождены (RELEASE). Повторный/
        # конкурентный вызов — no-op: повторное списание невозможно.
        committed_reserve_ids = StockMovement.objects.filter(
            order=order,
            kind=MovementKind.OUT,
            related_movement__isnull=False,
        ).values('related_movement_id')
        released_reserve_ids = StockMovement.objects.filter(
            order=order,
            kind=MovementKind.RELEASE,
            related_movement__isnull=False,
        ).values('related_movement_id')
        reserve_movements = (
            StockMovement.objects
            .filter(order=order, kind=MovementKind.RESERVE)
            .exclude(pk__in=committed_reserve_ids)
            .exclude(pk__in=released_reserve_ids)
            .select_related('stock')
        )

        movements = []
        for mv in reserve_movements:
            stock = Stock.objects.select_for_update().get(pk=mv.stock_id)

            quantity_before = stock.quantity

            # Атомарное обновление: списываем И quantity, И reserved.
            Stock.objects.filter(pk=stock.pk).update(
                quantity=models.F('quantity') - mv.delta,
                reserved_quantity=models.F('reserved_quantity') - mv.delta,
            )

            movement = StockMovement.objects.create(
                stock=stock,
                kind=MovementKind.OUT,
                delta=mv.delta,
                quantity_before=quantity_before,
                quantity_after=quantity_before - mv.delta,
                order=order,
                related_movement=mv,
                note=f'Списание (доставка {order.order_number})',
            )
            movements.append(movement)

            logger.info(
                'stock_committed',
                extra={
                    'stock_id': stock.pk,
                    'delta': mv.delta,
                    'order_id': order.pk,
                    'qty_before': quantity_before,
                    'qty_after': quantity_before - mv.delta,
                },
            )

        return movements

    # ==============================================================
    # Пополнение от поставщика
    # ==============================================================

    @staticmethod
    @transaction.atomic
    def restock(variant, quantity: int, *, performed_by=None, note: str = '') -> StockMovement:
        """
        Пополняет склад от поставщика.

        АЛГОРИТМ:
          1. Получить/создать Stock для variant
          2. select_for_update — блокировка
          3. Увеличить quantity на quantity
          4. Создать StockMovement(IN)

        📖 https://docs.djangoproject.com/en/stable/ref/models/expressions/#f-expressions
        """
        if quantity < 1:
            raise ValidationError({'quantity': 'Количество должно быть ≥ 1.'})

        stock = InventoryService.get_or_create_stock(variant)
        # select_for_update — блокируем для безопасности.
        stock = Stock.objects.select_for_update().get(pk=stock.pk)

        quantity_before = stock.quantity

        Stock.objects.filter(pk=stock.pk).update(
            quantity=models.F('quantity') + quantity,
        )

        movement = StockMovement.objects.create(
            stock=stock,
            kind=MovementKind.IN,
            delta=quantity,
            quantity_before=quantity_before,
            quantity_after=quantity_before + quantity,
            performed_by=performed_by,
            note=note or 'Поступление от поставщика',
        )

        logger.info(
            'stock_restocked',
            extra={
                'stock_id': stock.pk,
                'delta': quantity,
                'qty_before': quantity_before,
                'qty_after': quantity_before + quantity,
            },
        )

        return movement

    # ==============================================================
    # Ручная корректировка (инвентаризация)
    # ==============================================================

    @staticmethod
    @transaction.atomic
    def adjust_stock(
        variant,
        new_quantity: int,
        *,
        performed_by=None,
        note: str = '',
    ) -> StockMovement:
        """
        Устанавливает quantity в конкретное значение (инвентаризация).

        Отличие от restock: restock добавляет N штук,
        adjust_stock устанавливает конкретное число.

        АЛГОРИТМ:
          1. Получить Stock
          2. Вычислить delta = new_quantity - quantity
          3. Если delta > 0 → quantity += delta
          4. Если delta < 0 → quantity -= |delta| (проверяем reserved)
          5. Создать StockMovement(ADJUSTMENT)

        ПОЧЕМУ ПРОВЕРЯЕМ RESERVED ПРИ УМЕНЬШЕНИИ:
          Нельзя уменьшить quantity ниже reserved_quantity:
          если reserved=30, а мы хотим quantity=20 →
          available = 20 - 30 = -10 → продали -10 штук — баг.
        """
        if new_quantity < 0:
            raise ValidationError({'new_quantity': 'Количество не может быть < 0.'})

        stock = InventoryService.get_or_create_stock(variant)
        stock = Stock.objects.select_for_update().get(pk=stock.pk)

        delta = new_quantity - stock.quantity
        if delta == 0:
            raise ValidationError({'detail': 'Количество не изменилось.'})

        # Проверяем что при уменьшении не режем reserved.
        if delta < 0 and new_quantity < stock.reserved_quantity:
            raise ValidationError({
                'detail': (
                    f'Нельзя уменьшить до {new_quantity}: '
                    f'зарезервировано {stock.reserved_quantity} шт. '
                    f'Сначала снимите резерв.'
                ),
            })

        quantity_before = stock.quantity
        Stock.objects.filter(pk=stock.pk).update(quantity=new_quantity)

        movement = StockMovement.objects.create(
            stock=stock,
            kind=MovementKind.ADJUSTMENT,
            delta=abs(delta),
            quantity_before=quantity_before,
            quantity_after=new_quantity,
            performed_by=performed_by,
            note=note or 'Ручная корректировка',
        )

        logger.info(
            'stock_adjusted',
            extra={
                'stock_id': stock.pk,
                'qty_before': quantity_before,
                'qty_after': new_quantity,
                'delta': delta,
            },
        )

        return movement

    # ==============================================================
    # Проверка доступности
    # ==============================================================

    @staticmethod
    def check_availability(variant, quantity: int) -> bool:
        """
        Проверяет: можно ли заказать quantity штук данного варианта?
        True = можно, False = нельзя.
        """
        available = InventoryService.get_available_quantity(variant)
        return available >= quantity
