# ────────────────────────────────────────────────────────────────────────
# apps/payments/management/commands/cleanup_webhook_nonces.py —
#   очистка использованных webhook nonce (Issue #71 / API-01 F-6).
#
# ПОЛИТИКА:
#   Nonce с webhook_timestamp=ts мог быть повторно использован только
#   пока (now - ts) <= WEBHOOK_TIMESTAMP_TOLERANCE_SECONDS (окно
#   свежести). После этого он гарантированно отклоняется проверкой
#   свежести, независимо от того, существует ли его строка в БД.
#   Удаление безопасно при ts < now - WEBHOOK_NONCE_RETENTION_SECONDS,
#   где retention = tolerance + 60 c (запас).
#
#   Таблица nonce поэтому ограничена по объёму: хранится только
#   «живое» окно (~6 минут) использованных nonce.
#
# ЗАПУСК:
#   python manage.py cleanup_webhook_nonces
#   python manage.py cleanup_webhook_nonces --dry-run
#   python manage.py cleanup_webhook_nonces --retention-seconds=420
#
# Celery Beat (config/celery.py): каждые 15 минут через
# apps.payments.tasks.cleanup_webhook_nonces.
#
# 📖 https://docs.djangoproject.com/en/stable/howto/custom-management-commands/
# ────────────────────────────────────────────────────────────────────────

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.payments.constants import (
    WEBHOOK_NONCE_RETENTION_SECONDS,
    WEBHOOK_TIMESTAMP_TOLERANCE_SECONDS,
)
from apps.payments.models import PaymentWebhookNonce


class Command(BaseCommand):
    help = (
        'Удаляет использованные webhook nonce, которые гарантированно '
        'больше не могут быть использованы по security policy '
        f'(webhook_timestamp < now - {WEBHOOK_NONCE_RETENTION_SECONDS} c).'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--retention-seconds',
            type=int,
            default=WEBHOOK_NONCE_RETENTION_SECONDS,
            help=(
                'Время жизни nonce в секундах. Должно быть больше окна '
                f'свежести; по умолчанию: {WEBHOOK_NONCE_RETENTION_SECONDS}'
            ),
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            default=False,
            help='Показать что будет удалено, но НЕ удалять.',
        )

    def handle(self, *args, **options):
        # argparse нормализует --retention-seconds → retention_seconds.
        retention = options['retention_seconds']
        dry_run = options['dry_run']

        # Fail-closed: retention обязан покрывать окно свежести, иначе
        # nonce могли бы удалить, пока он ещё replay-абелен (now - ts
        # <= tolerance). Отклоняем unsafe-настройку.
        if retention < WEBHOOK_TIMESTAMP_TOLERANCE_SECONDS:
            raise CommandError(
                f'--retention-seconds должен быть >= '
                f'{WEBHOOK_TIMESTAMP_TOLERANCE_SECONDS} (окно свежести), '
                'иначе nonce удалились бы до истечения replay-окна.'
            )

        # Nonce «мёртв», если даже с учётом retention его timestamp
        # старше окна свежести: ts < now - retention.
        cutoff = int(timezone.now().timestamp()) - retention
        qs = PaymentWebhookNonce.objects.filter(webhook_timestamp__lt=cutoff)
        count = qs.count()

        if dry_run:
            self.stdout.write(
                f'[DRY RUN] Будет удалено {count} webhook nonce '
                f'(webhook_timestamp < {cutoff}).'
            )
            return

        if count:
            deleted, _details = qs.delete()
            self.stdout.write(
                self.style.SUCCESS(
                    f'Удалено {deleted} webhook nonce '
                    f'(webhook_timestamp < {cutoff}).'
                )
            )
        else:
            self.stdout.write('Нет webhook nonce для удаления.')
