# ────────────────────────────────────────────────────────────────────────
# apps/payments/management/commands/cleanup_stale_payments.py —
#   отмена «зависших» платежей (PENDING слишком долго).
#
# АЛГОРИТМ:
#   1. Найти все платежи в статусе PENDING
#   2. У которых created_at + PAYMENT_PENDING_TTL_HOURS < now()
#   3. Отменить каждый через PaymentService.cancel_payment()
#
# ВЫЗОВ:
#   python manage.py cleanup_stale_payments
#   python manage.py cleanup_stale_payments --hours=48
#   python manage.py cleanup_stale_payments --dry-run
#   python manage.py cleanup_stale_payments --json
#
# CRON (production):
#   0 */6 * * * cd /app && python manage.py cleanup_stale_payments --json >> /var/log/payments_cleanup.log 2>&1
#
# 📖 https://docs.djangoproject.com/en/stable/howto/custom-management-commands/
# ────────────────────────────────────────────────────────────────────────

import json as json_module

from django.core.exceptions import ObjectDoesNotExist
from django.core.management.base import BaseCommand
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.payments.constants import PAYMENT_PENDING_TTL_HOURS, PAYMENT_STATUS_PENDING
from apps.payments.models import Payment
from apps.payments.services.payment_service import PaymentService


class Command(BaseCommand):
    help = (
        f'Отменяет платежи в статусе PENDING старше '
        f'{PAYMENT_PENDING_TTL_HOURS} часов.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--hours',
            type=int,
            default=PAYMENT_PENDING_TTL_HOURS,
            help=(
                f'Возраст платежа в часах для отмены. '
                f'По умолчанию: {PAYMENT_PENDING_TTL_HOURS}'
            ),
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            default=False,
            help='Показать что будет отменено, но НЕ отменять.',
        )
        parser.add_argument(
            '--json',
            action='store_true',
            default=False,
            help='Вывести результат в JSON формате.',
        )

    def handle(self, *args, **options):
        hours = options['hours']
        dry_run = options['dry_run']
        json_output = options['json']
        cutoff = timezone.now() - __import__('datetime').timedelta(hours=hours)

        # Ищем «зависшие» платежи
        stale_payments = Payment.objects.filter(
            status=PAYMENT_STATUS_PENDING,
            created_at__lt=cutoff,
        )

        count = stale_payments.count()
        cancelled_ids = []

        if dry_run:
            for p in stale_payments:
                cancelled_ids.append({
                    'id': p.pk,
                    'payment_number': p.payment_number,
                    'amount': str(p.amount),
                    'created_at': p.created_at.isoformat(),
                })
            msg = f'[DRY RUN] Найдено {count} зависших платежей для отмены.'
        else:
            for payment in stale_payments:
                try:
                    PaymentService.cancel_payment(
                        payment,
                        note=f'Авто-отмена: PENDING > {hours}ч.',
                    )
                    cancelled_ids.append({
                        'id': payment.pk,
                        'payment_number': payment.payment_number,
                    })
                except (ValidationError, ObjectDoesNotExist) as exc:
                    # Ожидаемые доменные/not-found сбои одного платежа
                    # логируются и не останавливают обработку остальных.
                    # Неожиданные ошибки (БД, программные) пробрасываются.
                    self.stderr.write(
                        f'Ошибка отмены платежа {payment.payment_number}: {exc}'
                    )

            msg = f'Отменено {len(cancelled_ids)} зависших платежей.'

        if json_output:
            self.stdout.write(json_module.dumps({
                'cancelled_count': len(cancelled_ids),
                'payments': cancelled_ids,
                'dry_run': dry_run,
            }, indent=2, ensure_ascii=False))
        else:
            self.stdout.write(self.style.SUCCESS(msg))
