# ────────────────────────────────────────────────────────────────────────
# apps/analytics/services/analytics_service.py — бизнес-логика аналитики.
#
# АРХИТЕКТУРНЫЙ ПРИНЦИП «Service Layer»:
#   View → сериализатор → сервис (бизнес-логика) → ORM (SQL)
#
# ОПЕРАЦИИ:
#   record_view()            — записать просмотр товара
#   get_sales_summary()      — сводка продаж за период
#   get_sales_timeline()     — временной ряд продаж
#   get_top_products()       — топ товаров по выручке/продажам
#   get_top_categories()     — топ категорий
#   get_top_customers()      — топ покупателей
#   get_conversion_rate()    — конверсия просмотры → заказы
#   get_dashboard()          — комплексный dashboard
#
# 📖 Про Service Layer: https://martinfowler.com/eaaCatalog/serviceLayer.html
#
# ЧТО БУДЕТ, ЕСЛИ УДАЛИТЬ ФАЙЛ:
#   • Все API views аналитики → ImportError
#   • GET /api/v1/analytics/ → 500
#   • Dashboard пустой
# ────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Count, F, Q, Sum
from django.utils import timezone

from apps.analytics.constants import (
    DEFAULT_DAILY_POINTS,
    DEFAULT_TOP_CATEGORIES,
    DEFAULT_TOP_CUSTOMERS,
    DEFAULT_TOP_PRODUCTS,
    PERIOD_DAILY,
    PERIOD_MONTHLY,
    PERIOD_WEEKLY,
    SOURCE_DIRECT,
)
from apps.analytics.locks import acquire_dedup_lock
from apps.analytics.models import ProductView
from apps.orders.models import Order, OrderItem
from apps.orders.models.order import OrderStatus

logger = logging.getLogger(__name__)


class AnalyticsService:
    """
    Бизнес-логика аналитики.

    Все методы — read-only (агрегации, GROUP BY, COUNT).
    Только record_view() пишет данные.
    """

    # ==============================================================
    # ЗАПИСЬ ПРОСМОТРА ТОВАРА
    # ==============================================================

    @staticmethod
    @transaction.atomic
    def record_view(
        product,
        *,
        user=None,
        session_key: str = '',
        source: str = SOURCE_DIRECT,
        ip_address: str | None = None,
        user_agent: str = '',
    ) -> ProductView | None:
        """
        Записывает просмотр товара с дедупликацией.

        ДЕДУПЛИКАЦИЯ:
          Один пользователь/сессия → один просмотр в час.
          Защищает от накруток (F5, боты).

        КОНКУРЕНТНОСТЬ (PROD-021 / F-22):
          Проверка «просмотр уже есть» и вставка — это check-then-insert;
          сам по себе transaction.atomic() его НЕ сериализует (READ
          COMMITTED: обе конкурентные транзакции видят «нет строки»).
          Поэтому ключ дедупликации (товар + пользователь ИЛИ товар +
          сессия) захватывается транзакционным advisory-локом
          PostgreSQL — см. apps/analytics/locks.py. Лок берётся ДО
          exists(), снимается автоматически на commit/rollback и
          сериализует только конкурентов за тот же ключ.

        ЛИЧНОСТЬ ДЕДУПЛИКАЦИИ:
          • авторизованный → (product, user), скользящее окно 1 час;
          • аноним → (product, session_key), скользящее окно 1 час;
          • нет ни того, ни другого → дедупликация не применяется.

        ARGS:
            product: экземпляр Product
            user: авторизованный пользователь (опционально)
            session_key: ключ сессии (для анонимов)
            source: источник трафика
            ip_address: IP-адрес
            user_agent: User-Agent

        RETURNS:
            ProductView если записан, None если дубликат (в течение часа)
        """
        now = timezone.now()
        one_hour_ago = now - timedelta(hours=1)

        # ── Сериализация ключа дедупликации ──
        # Берём advisory-лок ДО проверки: пока текущая транзакция не
        # завершится, конкурент с тем же (товар, пользователь/сессия)
        # не выполнит ни свой exists(), ни свой INSERT.
        acquire_dedup_lock(
            product.pk,
            user_id=user.pk if user else None,
            session_key=session_key,
        )

        # ── Дедупликация ──
        # Ищем просмотр этого товара этим пользователем/сессией за последний час.
        if user:
            exists = ProductView.objects.filter(
                product=product,
                user=user,
                created_at__gte=one_hour_ago,
            ).exists()
        elif session_key:
            exists = ProductView.objects.filter(
                product=product,
                session_key=session_key,
                created_at__gte=one_hour_ago,
            ).exists()
        else:
            exists = False

        if exists:
            logger.debug(
                'product_view_deduplicated',
                extra={'product_id': product.pk},
            )
            return None

        # ── Создаём запись ──
        view = ProductView.objects.create(
            product=product,
            user=user,
            session_key=session_key,
            source=source,
            ip_address=ip_address,
            user_agent=user_agent[:500] if user_agent else '',
        )

        # ── Обновляем денормализованный счётчик ──
        # Product.views_count инкрементируем через F() — атомарно.
        from apps.catalog.models import Product
        Product.objects.filter(pk=product.pk).update(
            views_count=F('views_count') + 1,
        )

        logger.info(
            'product_view_recorded',
            extra={
                'view_id': view.pk,
                'product_id': product.pk,
                'source': source,
            },
        )

        return view

    # ==============================================================
    # СВОДКА ПРОДАЖ ЗА ПЕРИОД
    # ==============================================================

    @staticmethod
    def get_sales_summary(
        *,
        days: int = 30,
        start_date=None,
        end_date=None,
    ) -> dict:
        """
        Возвращает сводку продаж за период.

        ARGS:
            days: количество дней (по умолчанию 30)
            start_date: начало периода (переопределяет days)
            end_date: конец периода (по умолчанию — сейчас)

        RETURNS:
            {
                'total_revenue': Decimal,      — выручка (только доставленные)
                'total_orders': int,           — всего заказов
                'confirmed_orders': int,       — подтверждённых
                'delivered_orders': int,       — доставленных
                'cancelled_orders': int,       — отменённых
                'pending_orders': int,         — ожидающих
                'avg_order_value': Decimal,    — средний чек
                'total_items_sold': int,       — продано единиц товара
                'period_start': datetime,
                'period_end': datetime,
            }
        """
        if start_date is None:
            start_date = timezone.now() - timedelta(days=days)
        if end_date is None:
            end_date = timezone.now()

        # Все заказы за период
        orders_qs = Order.objects.filter(
            created_at__gte=start_date,
            created_at__lte=end_date,
        )

        # Агрегации по статусам
        status_counts = orders_qs.aggregate(
            total=Count('id'),
            confirmed=Count('id', filter=Q(status=OrderStatus.CONFIRMED)),
            delivered=Count('id', filter=Q(status=OrderStatus.DELIVERED)),
            cancelled=Count('id', filter=Q(status=OrderStatus.CANCELLED)),
            pending=Count('id', filter=Q(status=OrderStatus.PENDING)),
            processing=Count('id', filter=Q(status=OrderStatus.PROCESSING)),
            shipped=Count('id', filter=Q(status=OrderStatus.SHIPPED)),
        )

        # Выручка: только доставленные заказы
        # (деньги реально получены, товар передан клиенту)
        revenue_agg = orders_qs.filter(
            status=OrderStatus.DELIVERED,
        ).aggregate(
            total_revenue=Sum('total'),
        )

        # Продано единиц товара (из доставленных заказов)
        items_agg = OrderItem.objects.filter(
            order__in=orders_qs.filter(status=OrderStatus.DELIVERED),
        ).aggregate(
            total_items=Sum('quantity'),
        )

        total_orders = status_counts['total'] or 0
        total_revenue = revenue_agg['total_revenue'] or Decimal('0')
        total_items = items_agg['total_items'] or 0

        # Средний чек: выручка / количество доставленных
        delivered_count = status_counts['delivered'] or 0
        avg_order_value = (
            total_revenue / delivered_count
            if delivered_count > 0
            else Decimal('0')
        )

        return {
            'total_revenue': total_revenue,
            'total_orders': total_orders,
            'confirmed_orders': status_counts['confirmed'] or 0,
            'delivered_orders': delivered_count,
            'cancelled_orders': status_counts['cancelled'] or 0,
            'pending_orders': status_counts['pending'] or 0,
            'processing_orders': status_counts['processing'] or 0,
            'shipped_orders': status_counts['shipped'] or 0,
            'avg_order_value': avg_order_value,
            'total_items_sold': total_items,
            'period_start': start_date,
            'period_end': end_date,
        }

    # ==============================================================
    # ВРЕМЕННОЙ РЯД ПРОДАЖ (ДЛЯ ГРАФИКОВ)
    # ==============================================================

    @staticmethod
    def get_sales_timeline(
        *,
        days: int = DEFAULT_DAILY_POINTS,
        period: str = PERIOD_DAILY,
        start_date=None,
    ) -> list[dict]:
        """
        Возвращает временной ряд продаж (для графиков).

        ARGS:
            days: количество дней периода
            period: hourly / daily / weekly / monthly
            start_date: начало (по умолчанию — days назад)

        RETURNS:
            [
                {
                    'date': '2026-06-01',
                    'orders_count': 15,
                    'revenue': '45000.00',
                    'items_sold': 42,
                },
                ...
            ]

        АЛГОРИТМ:
          1. Определяем шаг (timedelta) по period
          2. Разбиваем период на точки
          3. Для каждой точки — агрегация
        """
        now = timezone.now()
        if start_date is None:
            start_date = now - timedelta(days=days)

        # ── Шаг агрегации ──
        if period == PERIOD_DAILY:
            step = timedelta(days=1)
        elif period == PERIOD_WEEKLY:
            step = timedelta(weeks=1)
        elif period == PERIOD_MONTHLY:
            # Для месячного периода — группируем по 30 дней
            step = timedelta(days=30)
        else:
            step = timedelta(days=1)

        timeline = []

        current = start_date
        while current <= now:
            next_point = current + step

            # Заказы за этот шаг
            orders_in_step = Order.objects.filter(
                created_at__gte=current,
                created_at__lt=next_point,
            )

            agg = orders_in_step.aggregate(
                orders_count=Count('id'),
                revenue=Sum('total', filter=Q(status=OrderStatus.DELIVERED)),
            )

            items_agg = OrderItem.objects.filter(
                order__in=orders_in_step.filter(status=OrderStatus.DELIVERED),
            ).aggregate(
                items_sold=Sum('quantity'),
            )

            timeline.append({
                'date': current.strftime('%Y-%m-%d'),
                'orders_count': agg['orders_count'] or 0,
                'revenue': str(agg['revenue'] or Decimal('0')),
                'items_sold': items_agg['items_sold'] or 0,
            })

            current = next_point

        return timeline

    # ==============================================================
    # ТОП ТОВАРОВ
    # ==============================================================

    @staticmethod
    def get_top_products(
        *,
        days: int = 30,
        limit: int = DEFAULT_TOP_PRODUCTS,
        metric: str = 'revenue',
    ) -> list[dict]:
        """
        Возвращает топ товаров по выручке или количеству продаж.

        ARGS:
            days: период анализа (по умолчанию 30 дней)
            limit: сколько топ-товаров вернуть
            metric: 'revenue' (по выручке) или 'quantity' (по количеству)

        RETURNS:
            [
                {
                    'product_id': 1,
                    'product_name': 'Galaxy S24',
                    'sku': 'SM-S24-128',
                    'quantity_sold': 42,
                    'revenue': '210000.00',
                },
                ...
            ]
        """
        start_date = timezone.now() - timedelta(days=days)

        # Товары из доставленных заказов (реальные продажи)
        qs = OrderItem.objects.filter(
            order__status=OrderStatus.DELIVERED,
            order__created_at__gte=start_date,
        )

        # Группировка по товару (используем variant_id и product_name)
        if metric == 'quantity':
            qs = qs.values(
                'variant_id', 'product_name', 'sku',
            ).annotate(
                quantity_sold=Sum('quantity'),
                revenue=Sum(F('unit_price') * F('quantity')),
            ).order_by('-quantity_sold')[:limit]
        else:
            qs = qs.values(
                'variant_id', 'product_name', 'sku',
            ).annotate(
                quantity_sold=Sum('quantity'),
                revenue=Sum(F('unit_price') * F('quantity')),
            ).order_by('-revenue')[:limit]

        return [
            {
                'variant_id': item['variant_id'],
                'product_name': item['product_name'],
                'sku': item['sku'],
                'quantity_sold': item['quantity_sold'] or 0,
                'revenue': str(item['revenue'] or Decimal('0')),
            }
            for item in qs
        ]

    # ==============================================================
    # ТОП КАТЕГОРИЙ
    # ==============================================================

    @staticmethod
    def get_top_categories(
        *,
        days: int = 30,
        limit: int = DEFAULT_TOP_CATEGORIES,
    ) -> list[dict]:
        """
        Возвращает топ категорий по выручке.

        RETURNS:
            [
                {
                    'category_id': 1,
                    'category_name': 'Смартфоны',
                    'orders_count': 50,
                    'revenue': '500000.00',
                },
                ...
            ]
        """
        start_date = timezone.now() - timedelta(days=days)

        # JOIN OrderItem → ProductVariant → Product → primary_category
        qs = (
            OrderItem.objects
            .filter(
                order__status=OrderStatus.DELIVERED,
                order__created_at__gte=start_date,
                variant__isnull=False,
            )
            .values(
                'variant__product__primary_category_id',
                'variant__product__primary_category__name',
            )
            .annotate(
                orders_count=Count('order', distinct=True),
                revenue=Sum(F('unit_price') * F('quantity')),
            )
            .order_by('-revenue')[:limit]
        )

        return [
            {
                'category_id': item['variant__product__primary_category_id'],
                'category_name': item['variant__product__primary_category__name'] or 'Без категории',
                'orders_count': item['orders_count'],
                'revenue': str(item['revenue'] or Decimal('0')),
            }
            for item in qs
        ]

    # ==============================================================
    # ТОП ПОКУПАТЕЛЕЙ
    # ==============================================================

    @staticmethod
    def get_top_customers(
        *,
        days: int = 30,
        limit: int = DEFAULT_TOP_CUSTOMERS,
    ) -> list[dict]:
        """
        Возвращает топ покупателей по сумме заказов.

        RETURNS:
            [
                {
                    'user_id': 1,
                    'email': 'ivan@example.com',
                    'orders_count': 15,
                    'total_spent': '250000.00',
                },
                ...
            ]
        """
        start_date = timezone.now() - timedelta(days=days)

        qs = (
            Order.objects
            .filter(
                status=OrderStatus.DELIVERED,
                created_at__gte=start_date,
            )
            .values('user_id', 'user__email')
            .annotate(
                orders_count=Count('id'),
                total_spent=Sum('total'),
            )
            .order_by('-total_spent')[:limit]
        )

        return [
            {
                'user_id': item['user_id'],
                'email': item['user__email'],
                'orders_count': item['orders_count'],
                'total_spent': str(item['total_spent'] or Decimal('0')),
            }
            for item in qs
        ]

    # ==============================================================
    # КОНВЕРСИЯ: ПРОСМОТРЫ → ЗАКАЗЫ
    # ==============================================================

    @staticmethod
    def get_conversion_rate(
        *,
        days: int = 30,
    ) -> dict:
        """
        Рассчитывает конверсию: просмотры → заказы.

        ФОРМУЛА:
          conversion_rate = (unique_buyers / total_views) × 100

        RETURNS:
            {
                'total_views': int,         — всего просмотров за период
                'total_orders': int,        — всего заказов за период
                'conversion_rate': Decimal, — процент конверсии
            }
        """
        start_date = timezone.now() - timedelta(days=days)

        total_views = ProductView.objects.filter(
            created_at__gte=start_date,
        ).count()

        total_orders = Order.objects.filter(
            created_at__gte=start_date,
        ).exclude(
            status=OrderStatus.CANCELLED,
        ).count()

        if total_views > 0:
            conversion = (Decimal(total_orders) / Decimal(total_views)) * Decimal('100')
            conversion = conversion.quantize(Decimal('0.01'))
        else:
            conversion = Decimal('0')

        return {
            'total_views': total_views,
            'total_orders': total_orders,
            'conversion_rate': conversion,
        }

    # ==============================================================
    # ПРОСМОТРЫ ТОВАРОВ
    # ==============================================================

    @staticmethod
    def get_product_views(
        product,
        *,
        days: int = 30,
    ) -> dict:
        """
        Возвращает статистику просмотров конкретного товара.

        RETURNS:
            {
                'total_views': int,
                'unique_viewers': int,
                'recent_views': int,
                'by_source': dict,
            }
        """
        start_date = timezone.now() - timedelta(days=days)

        qs = ProductView.objects.filter(
            product=product,
            created_at__gte=start_date,
        )

        total_views = qs.count()

        # Уникальные зрители (по user или session_key)
        unique_users = qs.exclude(user__isnull=True).values('user_id').distinct().count()
        unique_sessions = qs.exclude(session_key='').values('session_key').distinct().count()
        unique_viewers = unique_users + unique_sessions

        # Просмотры за последние 7 дней
        recent = qs.filter(
            created_at__gte=timezone.now() - timedelta(days=7),
        ).count()

        # По источникам
        source_breakdown = dict(
            qs.values_list('source').annotate(
                count=Count('id'),
            ).values_list('source', 'count'),
        )

        return {
            'total_views': total_views,
            'unique_viewers': unique_viewers,
            'recent_views': recent,
            'by_source': source_breakdown,
        }

    @staticmethod
    def get_most_viewed_products(
        *,
        days: int = 30,
        limit: int = DEFAULT_TOP_PRODUCTS,
    ) -> list[dict]:
        """
        Возвращает самые просматриваемые товары за период.

        RETURNS:
            [
                {
                    'product_id': 1,
                    'product_name': 'Galaxy S24',
                    'views_count': 1500,
                },
                ...
            ]
        """
        start_date = timezone.now() - timedelta(days=days)

        qs = (
            ProductView.objects
            .filter(created_at__gte=start_date)
            .values(
                'product_id',
                'product__name',
            )
            .annotate(
                views_count=Count('id'),
            )
            .order_by('-views_count')[:limit]
        )

        return [
            {
                'product_id': item['product_id'],
                'product_name': item['product__name'],
                'views_count': item['views_count'],
            }
            for item in qs
        ]

    # ==============================================================
    # КОМПЛЕКСНЫЙ DASHBOARD
    # ==============================================================

    @staticmethod
    def get_dashboard(*, days: int = 30) -> dict:
        """
        Возвращает комплексный дашборд для админ-панели.

        Объединяет все ключевые метрики в один ответ:
          • Сводка продаж
          • Топ-товары
          • Топ-категории
          • Топ-покупатели
          • Конверсия
          • Временной ряд

        RETURNS:
            {
                'summary': {...},
                'top_products': [...],
                'top_categories': [...],
                'top_customers': [...],
                'conversion': {...},
                'timeline': [...],
            }
        """
        return {
            'summary': AnalyticsService.get_sales_summary(days=days),
            'top_products': AnalyticsService.get_top_products(days=days),
            'top_categories': AnalyticsService.get_top_categories(days=days),
            'top_customers': AnalyticsService.get_top_customers(days=days),
            'conversion': AnalyticsService.get_conversion_rate(days=days),
            'timeline': AnalyticsService.get_sales_timeline(days=min(days, 30)),
        }
