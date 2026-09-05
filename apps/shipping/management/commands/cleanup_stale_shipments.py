# ────────────────────────────────────────────────────────────────────────
# apps/shipping/management/commands/cleanup_stale_shipments.py —
#   деактивация зависших отправлений.
#
# Отправления в статусе PREPARING, которые не были переданы
# в службу доставки дольше SHIPMENT_STALE_HOURS часов,
# переводятся в статус RETURNED (возврат).
#
# ЗАПУСК:
#   python manage.py cleanup_stale_shipments
#   python manage.py cleanup_stale_shipments --hours=72
#   python manage.py cleanup_stale_shipments --dry-run
#
# АВТОМАТИЗАЦИЯ:
#   Cron: 0 */6 * * * cd /path && python manage.py cleanup_stale_shipments
#
# 📖 https://docs.djangoproject.com/en/stable/howto/custom-management-commands/
# ────────────────────────────────────────────────────────────────────────

import logging

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.shipping.constants import SHIPMENT_PREPARING
from apps.shipping.models import Shipment

logger = logging.getLogger(__name__)

# По умолчанию — отправления, зависшие более 48 часов в PREPARING.
DEFAULT_STALE_HOURS = 48


class Command(BaseCommand):
    help = (
        'Переводит зависшие отправления (PREPARING > N часов) '
        'в статус RETURNED.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--hours',
            type=int,
            default=DEFAULT_STALE_HOURS,
            help=(
                f'Количество часов без обновления для считания '
                f'отправления «зависшим». По умолчанию: {DEFAULT_STALE_HOURS}.'
            ),
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            default=False,
            help='Показать что будет изменено, но не применять.',
        )

    def handle(self, *args, **options):
        hours = options['hours']
        dry_run = options['dry_run']
        cutoff = timezone.now() - timezone.timedelta(hours=hours)

        stale = Shipment.objects.filter(
            status=SHIPMENT_PREPARING,
            updated_at__lt=cutoff,
        )

        count = stale.count()

        if count == 0:
            self.stdout.write(
                f'Нет зависших отправлений (PREPARING > {hours}ч).'
            )
            return

        if dry_run:
            self.stdout.write(
                f'[DRY RUN] Будет переведено в RETURNED: {count} '
                f'отправлений (PREPARING > {hours}ч).'
            )
            for s in stale[:10]:
                self.stdout.write(f'  • {s.shipment_number}')
            if count > 10:
                self.stdout.write(f'  ... и ещё {count - 10}')
            self.stdout.write('[DRY RUN] Изменения НЕ применены.')
            return

        updated = 0
        for shipment in stale:
            # Сохранение схемы не имеет ожидаемых доменных исключений.
            # Ошибки БД/программные ошибки не должны превращаться в
            # «успешный» отчёт команды — они пробрасываются наружу.
            shipment.status = 'returned'
            shipment.save(update_fields=['status', 'updated_at'])
            updated += 1
            logger.info(
                'stale_shipment_returned',
                extra={
                    'shipment_id': shipment.pk,
                    'shipment_number': shipment.shipment_number,
                },
            )

        self.stdout.write(
            self.style.SUCCESS(
                f'Переведено в RETURNED: {updated} зависших отправлений.'
            )
        )
