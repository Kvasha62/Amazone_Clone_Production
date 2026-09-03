# ────────────────────────────────────────────────────────────────────────
# apps/orders/management/commands/cleanup_stale_orders.py —
# Management-команда для автоматической отмены старых PENDING-заказов.
#
# ЗАЧЕМ:
#   Заказы в статусе PENDING, не оплаченные в течение
#   ORDER_PENDING_TTL_HOURS (24 часа), занимают:
#     1. «Замороженный» сток — если сток зарезервирован при создании
#     2. Номер заказа — ORD-000001 числится как PENDING → путаница
#     3. Ресурсы БД — каждая строка Order + OrderItem занимает место
#
# ВЫЗОВ:
#   python manage.py cleanup_stale_orders
#   python manage.py cleanup_stale_orders --dry-run   # только показать
#   python manage.py cleanup_stale_orders --hours 48   # кастомный TTL
#
# CRontab (рекомендация):
#   0 */6 * * * cd /app && python manage.py cleanup_stale_orders >> /var/log/cleanup.log 2>&1
#   → каждые 6 часов
#
# 📖 https://docs.djangoproject.com/en/stable/howto/custom-management-commands/
# 📖 https://docs.djangoproject.com/en/stable/topics/db/transactions/
#
# ЧТО БУДЕТ, ЕСЛИ УДАЛИТЬ ФАЙЛ:
#   • Старые PENDING-заказы никогда не отменятся
#   • Сток может быть «заморожен» навсегда
#   • Номера заказов расходуются на забытые заказы
# ────────────────────────────────────────────────────────────────────────

from datetime import timedelta

from django.core.exceptions import ObjectDoesNotExist
from django.core.management.base import BaseCommand
from django.utils import timezone
from rest_framework.exceptions import NotFound, ValidationError

from apps.orders.constants import ORDER_PENDING_TTL_HOURS
from apps.orders.models import Order
from apps.orders.models.order import OrderStatus


class Command(BaseCommand):
    """
    Автоматическая отмена старых PENDING-заказов.

    АЛГОРИТМ:
      1. Найти заказы со статусом PENDING
      2. Фильтр: created_at < now() - ORDER_PENDING_TTL_HOURS
      3. Отменить каждый (OrderService.cancel)

    БЕЗОПАСНОСТЬ:
      • --dry-run — показать что будет отменено, но не отменять
      • Логирование каждого отменённого заказа
      • Транзакции для атомарности

    📖 https://docs.djangoproject.com/en/stable/howto/custom-management-commands/
    """

    # help — текст, показываемый при --help
    help = (
        'Отменяет заказы в статусе PENDING, не обновлявшиеся '
        f'долее {ORDER_PENDING_TTL_HOURS} часов.'
    )

    def add_arguments(self, parser):
        """
        Добавляет аргументы командной строки.

        --dry-run — показать что будет отменено, но не отменять.
        Полезно для отладки и ручного запуска.

        --hours — кастомный TTL (перекрывает ORDER_PENDING_TTL_HOURS).
        Полезно для разовой очистки с другим периодом.

        📖 https://docs.djangoproject.com/en/stable/howto/custom-management-commands/#accepting-optional-arguments
        """
        parser.add_argument(
            '--dry-run',
            action='store_true',
            default=False,
            help='Показать что будет отменено, но не отменять.',
        )
        parser.add_argument(
            '--hours',
            type=int,
            default=ORDER_PENDING_TTL_HOURS,
            help=(
                f'Количество часов для TTL (по умолчанию: '
                f'{ORDER_PENDING_TTL_HOURS}).'
            ),
        )

    def handle(self, *args, **options):
        """
        Основная логика команды.

        ПОТОК:
          1. Вычислить cutoff-время (now - hours)
          2. Найти PENDING-заказы старше cutoff
          3. Отменить каждый (или показать в dry-run)
          4. Вывести итоговую статистику
        """
        dry_run = options['dry_run']
        hours = options['hours']

        # cutoff — «заказы, созданные ДО этого времени, считаются старыми».
        # timezone.now() — timezone-aware datetime (USE_TZ = True).
        cutoff = timezone.now() - timedelta(hours=hours)

        # Фильтруем: PENDING + созданные до cutoff.
        # select_related('user') — для логирования без N+1.
        stale_orders = (
            Order.objects
            .select_related('user')
            .filter(
                status=OrderStatus.PENDING,
                created_at__lt=cutoff,
            )
        )

        count = stale_orders.count()

        if count == 0:
            self.stdout.write(
                self.style.SUCCESS('Нет старых PENDING-заказов для отмены.')
            )
            return

        self.stdout.write(
            f'Найдено {count} PENDING-заказов старше {hours} часов.'
        )

        if dry_run:
            self.stdout.write(
                self.style.WARNING('[DRY-RUN] Отмена НЕ выполнена.')
            )
            for order in stale_orders:
                self.stdout.write(
                    f'  {order.order_number} — '
                    f'{order.user.email} — '
                    f'{order.total}₽ — '
                    f'создан {order.created_at}'
                )
            return

        # ── Реальная отмена ──
        from apps.orders.services.order_service import OrderService

        cancelled = 0
        for order in stale_orders:
            try:
                OrderService.cancel(
                    order,
                    reason='payment_failed',  # автоматическая причина
                )
                self.stdout.write(
                    f'  ✓ {order.order_number} — отменён'
                )
                cancelled += 1
            except (ValidationError, NotFound, ObjectDoesNotExist) as exc:
                # Ожидаемые доменные/not-found сбои одного заказа не должны
                # останавливать обработку остальных. Прочие ошибки (БД,
                # программные) НЕ глотаются: команда падает с видимым
                # traceback, а не завершается "успехом" с неполным результатом.
                self.stderr.write(
                    f'  ✗ {order.order_number} — ошибка: {exc}'
                )

        self.stdout.write(
            self.style.SUCCESS(
                f'Отменено {cancelled}/{count} заказов.'
            )
        )
