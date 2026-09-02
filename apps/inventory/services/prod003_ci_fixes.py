"""PROD-003 PostgreSQL consistency fixes.

This module contains the two narrow fixes required by the CI concurrency
suite. They are installed from InventoryConfig.ready() so the existing
InventoryService public API remains unchanged.
"""

from django.db import models, transaction

from apps.inventory.models import Stock, StockMovement
from apps.inventory.models.stock_movement import MovementKind
from apps.inventory.services.inventory_service import InventoryService


def release_stock(order):
    """Release only reserves that have not already been committed."""
    InventoryService._locked_order(order)

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
        Stock.objects.filter(pk=stock.pk).update(
            reserved_quantity=models.F('reserved_quantity') - mv.delta,
        )
        movements.append(StockMovement.objects.create(
            stock=stock,
            kind=MovementKind.RELEASE,
            delta=mv.delta,
            quantity_before=quantity_before,
            quantity_after=quantity_before,
            order=order,
            related_movement=mv,
            note=f'Освобождение резерва (отмена {order.order_number})',
        ))
    return movements


def reconcile_order(order):
    """Reconcile inventory, including DELIVERED orders with no reserve."""
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


# Keep the public API identical while correcting the two CI-proven edge cases.
InventoryService.release_stock = staticmethod(
    lambda order: _atomic_release(order)
)
InventoryService.reconcile_order = staticmethod(
    lambda order: _atomic_reconcile(order)
)


def _atomic_release(order):
    with transaction.atomic():
        return release_stock(order)


def _atomic_reconcile(order):
    with transaction.atomic():
        return reconcile_order(order)
