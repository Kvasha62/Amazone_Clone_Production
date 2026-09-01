# ────────────────────────────────────────────────────────────────────────
# apps/payments/management/commands/retry_pending_refunds.py
#
# PROD-003 — исполнение незакрытых обязательств возврата.
#
# Находит SUCCEEDED-платежи с refund_required_amount > refund_amount
# (обязательство зафиксировано при провале возврата) и доводит
# refund_amount до обязательства через PaymentService.retry_pending_refunds().
#
# Запуск:
#   python manage.py retry_pending_refunds            # все обязательства
#   python manage.py retry_pending_refunds 1 2 3      # конкретные платежи
#   python manage.py retry_pending_refunds --json     # машинный вывод
#
# Повторный запуск безопасен: операция идемпотентна (обязательство
# закрывается только один раз; строка платежа блокируется в транзакции).
# ────────────────────────────────────────────────────────────────────────

import json as json_module

from django.core.management.base import BaseCommand

from apps.payments.services.payment_service import PaymentService


class Command(BaseCommand):
    help = (
        'Исполнить незакрытые обязательства возвратов '
        '(refund_required_amount > refund_amount).'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            'payment_ids',
            nargs='*',
            type=int,
            help='PK платежей для обработки (по умолчанию — все).',
        )
        parser.add_argument(
            '--json',
            action='store_true',
            default=False,
            help='Вывести результат в JSON формате.',
        )

    def handle(self, *args, **options):
        stats = PaymentService.retry_pending_refunds(
            options['payment_ids'] or None,
        )

        if options['json']:
            self.stdout.write(
                json_module.dumps(stats, indent=2, ensure_ascii=False),
            )
            return

        self.stdout.write(self.style.SUCCESS(
            f'Обязательства возвратов: найдено {stats["found"]}, '
            f'исполнено {stats["settled"]}, ошибок {stats["failed"]}.',
        ))
        if stats['failed']:
            self.stderr.write(
                f'{stats["failed"]} обязательств не исполнено — '
                'повторите запуск или разберите вручную.',
            )
