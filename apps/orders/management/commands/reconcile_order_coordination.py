# ────────────────────────────────────────────────────────────────────────
# apps/orders/management/commands/reconcile_order_coordination.py
#
# PROD-003 — реконсиляция координации заказ ↔ склад ↔ платежи.
#
# Восстанавливает согласованность после сбоев:
#
#   1. Склад (InventoryService.reconcile_order — идемпотентно):
#        • CONFIRMED/PROCESSING/SHIPPED без RESERVE → резервирование;
#        • CANCELLED с непарными RESERVE → освобождение резерва;
#        • DELIVERED с непарными RESERVE → списание стока.
#
#   2. Платежи (PaymentService.reconcile_succeeded_payment):
#        • SUCCEEDED + PENDING → повторное подтверждение заказа;
#        • SUCCEEDED + CANCELLED → фиксация обязательства возврата
#          (подхватит `retry_pending_refunds`).
#
# Запуск:
#   python manage.py reconcile_order_coordination            # все заказы
#   python manage.py reconcile_order_coordination ORD-000123 # конкретные
#   python manage.py reconcile_order_coordination --json     # машинный вывод
#
# Повторный запуск безопасен: все операции идемпотентны.
# ────────────────────────────────────────────────────────────────────────

import json as json_module

from django.core.exceptions import ObjectDoesNotExist
from django.core.management.base import BaseCommand
from django.db import DatabaseError
from rest_framework.exceptions import NotFound, ValidationError

from apps.inventory.services.inventory_service import InventoryService
from apps.orders.models import Order
from apps.orders.models.order import OrderStatus
from apps.payments.constants import PAYMENT_STATUS_SUCCEEDED
from apps.payments.models import Payment
from apps.payments.services.payment_service import PaymentService

# Статусы заказов, для которых применимо восстановление склада.
_INVENTORY_RECONCILE_STATUSES = (
    OrderStatus.CONFIRMED,
    OrderStatus.PROCESSING,
    OrderStatus.SHIPPED,
    OrderStatus.DELIVERED,
    OrderStatus.CANCELLED,
)


class Command(BaseCommand):
    help = (
        'Реконсиляция координации заказ ↔ склад ↔ платежи (PROD-003).'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            'order_numbers',
            nargs='*',
            type=str,
            help='Номера заказов для обработки (по умолчанию — все).',
        )
        parser.add_argument(
            '--json',
            action='store_true',
            default=False,
            help='Вывести результат в JSON формате.',
        )

    def handle(self, *args, **options):
        order_numbers = options['order_numbers']
        if order_numbers:
            orders = Order.objects.filter(order_number__in=order_numbers)
        else:
            orders = (
                Order.objects
                .filter(status__in=_INVENTORY_RECONCILE_STATUSES)
                .order_by('pk')
            )

        report = {
            'orders_checked': 0,
            'inventory_actions': [],
            'payment_reconciliations': [],
            'errors': [],
        }

        for order in orders:
            report['orders_checked'] += 1
            try:
                inventory_report = InventoryService.reconcile_order(order)
                if inventory_report['actions']:
                    report['inventory_actions'].append(inventory_report)
            except (
                ValidationError,
                NotFound,
                ObjectDoesNotExist,
                DatabaseError,
            ) as exc:
                # Ожидаемые доменные/not-found/DB сбои одной стороны
                # фиксируются в отчёте; неожиданные программные ошибки
                # намеренно пробрасываются и останавливают команду.
                report['errors'].append({
                    'order_number': order.order_number,
                    'phase': 'inventory',
                    'error': str(exc),
                })
                self.stderr.write(
                    f'Склад: {order.order_number}: {exc}',
                )

            try:
                for payment in Payment.objects.filter(
                    order=order,
                    status=PAYMENT_STATUS_SUCCEEDED,
                ):
                    outcome = PaymentService.reconcile_succeeded_payment(
                        payment,
                    )
                    report['payment_reconciliations'].append({
                        'payment_number': payment.order_number,
                        'order_number': order.order_number,
                        'outcome': outcome,
                    })
            except (
                ValidationError,
                NotFound,
                ObjectDoesNotExist,
                DatabaseError,
            ) as exc:
                # Ожидаемые доменные/not-found/DB сбои одной стороны
                # фиксируются в отчёте; неожиданные программные ошибки
                # намеренно пробрасываются и останавливают команду.
                report['errors'].append({
                    'order_number': order.order_number,
                    'phase': 'payment',
                    'error': str(exc),
                })
                self.stderr.write(
                    f'Платежи: {order.order_number}: {exc}',
                )

        if options['json']:
            self.stdout.write(
                json_module.dumps(report, indent=2, ensure_ascii=False),
            )
            return

        self.stdout.write(self.style.SUCCESS(
            f'Проверено заказов: {report["orders_checked"]}, '
            f'действий по складу: {len(report["inventory_actions"])}, '
            f'реконсиляций платежей: {len(report["payment_reconciliations"])}, '
            f'ошибок: {len(report["errors"])}.',
        ))
        if report['errors']:
            self.stderr.write(
                f'{len(report["errors"])} ошибок — повторите запуск '
                'или разберите вручную (см. отчёт --json).',
            )
