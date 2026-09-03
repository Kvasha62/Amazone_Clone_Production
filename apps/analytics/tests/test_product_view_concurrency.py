# ────────────────────────────────────────────────────────────────────────
# apps/analytics/tests/test_product_view_concurrency.py
#
# PROD-021 / F-22 — конкурентная дедупликация просмотров товара.
#
# Это РЕАЛЬНЫЕ cross-connection тесты на PostgreSQL:
#   • TransactionTestCase (данные коммитятся и видны другим сессиям);
#   • потоки стартуют одновременно (барьер) на СВОИХ соединениях —
#     запуск через apps/core/tests/concurrency.py (ограниченный по
#     времени раннер с гарантированным освобождением тестовой БД).
#
# ГАРАНТИЯ, КОТОРУЮ ЗАЩИЩАЮТ ТЕСТЫ:
#   Инвариант «один пользователь/сессия → один просмотр товара в час»
#   выполняется и при параллельных запросах: ключ дедупликации
#   сериализуется транзакционным advisory-локом PostgreSQL
#   (apps/analytics/locks.py), поэтому check-then-insert выполняется
#   строго по одному конкуренту за раз.
#
# РЕГРЕССИЯ, КОТОРУЮ ТЕСТЫ ЛОВЯТ (проверено на прежнем коде):
#   без лока обе транзакции видели «просмотра нет» → создавались ДВА
#   ProductView и Product.views_count увеличивался на 2.
# ────────────────────────────────────────────────────────────────────────

from datetime import timedelta
from unittest import skipUnless

from django.db import connection, connections, transaction
from django.test import TransactionTestCase
from django.utils import timezone

from apps.analytics.models import ProductView
from apps.analytics.services.analytics_service import AnalyticsService
from apps.catalog.constants import ProductStatus
from apps.catalog.models import Brand, Category, Product
from apps.core.tests.concurrency import ConcurrentJobsMixin
from apps.orders.tests.factories import create_test_user

requires_postgresql = skipUnless(
    connection.vendor == 'postgresql',
    'Конкурентная дедупликация просмотров гарантируется advisory-локами PostgreSQL.',
)

CONCURRENT_REQUESTS = 8


@requires_postgresql
class ProductViewDeduplicationConcurrencyTests(ConcurrentJobsMixin, TransactionTestCase):
    """Инвариант дедупликации под параллельными запросами."""

    def setUp(self):
        brand = Brand.objects.create(name='ViewRaceBrand')
        category = Category.add_root(name='ViewRaceCat')
        self.product = Product.objects.create(
            name='View Race Product',
            brand=brand,
            primary_category=category,
            status=ProductStatus.ACTIVE,
        )
        self.initial_views_count = self.product.views_count

    # ──────────────────────────────────────────────────────────────
    # Инфраструктура
    # ──────────────────────────────────────────────────────────────

    def _record(self, **kwargs):
        """Один «запрос» на собственном соединении, как во view."""
        def job():
            try:
                # Товар перечитываем в своём соединении: поток работает
                # с собственной сессией БД, как отдельный воркер gunicorn.
                product = Product.objects.get(pk=self.product.pk)
                with transaction.atomic():
                    return AnalyticsService.record_view(product, **kwargs)
            finally:
                connections.close_all()
        return job

    def _assert_single_view(self, run):
        recorded = [value for value in run.results if value is not None]
        self.assertEqual(
            len(recorded), 1,
            f'Ожидался ровно один записанный просмотр, получено {len(recorded)}.',
        )
        self.assertEqual(
            ProductView.objects.filter(product=self.product).count(), 1,
            'В БД должна остаться ровно одна строка ProductView.',
        )
        self.product.refresh_from_db()
        self.assertEqual(
            self.product.views_count, self.initial_views_count + 1,
            'views_count должен вырасти ровно на 1, а не на число гонщиков.',
        )

    # ──────────────────────────────────────────────────────────────
    # AC-2 — анонимная сессия
    # ──────────────────────────────────────────────────────────────

    def test_concurrent_same_session_records_single_view(self):
        """Параллельные запросы одной сессии → один просмотр."""
        run = self.run_concurrent_jobs(
            [self._record(session_key='race-session') for _ in range(CONCURRENT_REQUESTS)],
        )
        self._assert_single_view(run)

    # ──────────────────────────────────────────────────────────────
    # AC-1 — авторизованный пользователь
    # ──────────────────────────────────────────────────────────────

    def test_concurrent_same_user_records_single_view(self):
        """Параллельные запросы одного пользователя → один просмотр."""
        user = create_test_user()
        run = self.run_concurrent_jobs(
            [self._record(user=user) for _ in range(CONCURRENT_REQUESTS)],
        )
        self._assert_single_view(run)

    def test_concurrent_same_user_different_sessions_records_single_view(self):
        """Личность авторизованного — user, а не session_key."""
        user = create_test_user()
        run = self.run_concurrent_jobs([
            self._record(user=user, session_key=f'sess-{index}')
            for index in range(CONCURRENT_REQUESTS)
        ])
        self._assert_single_view(run)

    # ──────────────────────────────────────────────────────────────
    # AC-3 — независимость разных личностей
    # ──────────────────────────────────────────────────────────────

    def test_concurrent_different_sessions_are_independent(self):
        """Разные сессии параллельно → по просмотру на каждую."""
        run = self.run_concurrent_jobs([
            self._record(session_key=f'independent-{index}')
            for index in range(CONCURRENT_REQUESTS)
        ])
        recorded = [value for value in run.results if value is not None]
        self.assertEqual(len(recorded), CONCURRENT_REQUESTS)
        self.assertEqual(
            ProductView.objects.filter(product=self.product).count(),
            CONCURRENT_REQUESTS,
        )
        self.product.refresh_from_db()
        self.assertEqual(
            self.product.views_count,
            self.initial_views_count + CONCURRENT_REQUESTS,
        )

    def test_concurrent_different_users_are_independent(self):
        """Разные пользователи параллельно → по просмотру на каждого."""
        users = [create_test_user() for _ in range(CONCURRENT_REQUESTS)]
        run = self.run_concurrent_jobs([self._record(user=user) for user in users])
        recorded = [value for value in run.results if value is not None]
        self.assertEqual(len(recorded), CONCURRENT_REQUESTS)
        self.assertEqual(
            ProductView.objects.filter(product=self.product).count(),
            CONCURRENT_REQUESTS,
        )

    # ──────────────────────────────────────────────────────────────
    # AC-4 — скользящее окно в один час сохранено
    # ──────────────────────────────────────────────────────────────

    def test_view_older_than_one_hour_is_not_deduplicated(self):
        """Просмотр старше часа не блокирует новый (окно сохранено)."""
        old = ProductView.objects.create(
            product=self.product, session_key='window-session',
        )
        ProductView.objects.filter(pk=old.pk).update(
            created_at=timezone.now() - timedelta(hours=2),
        )
        view = AnalyticsService.record_view(self.product, session_key='window-session')
        self.assertIsNotNone(view)
        self.assertEqual(
            ProductView.objects.filter(product=self.product).count(), 2,
        )

    def test_view_inside_one_hour_window_is_deduplicated(self):
        """Просмотр внутри часа по-прежнему дедуплицируется."""
        old = ProductView.objects.create(
            product=self.product, session_key='window-session',
        )
        ProductView.objects.filter(pk=old.pk).update(
            created_at=timezone.now() - timedelta(minutes=30),
        )
        self.assertIsNone(
            AnalyticsService.record_view(self.product, session_key='window-session'),
        )

    # ──────────────────────────────────────────────────────────────
    # AC-5 / AC-9 — счётчик и отсутствие частичного состояния
    # ──────────────────────────────────────────────────────────────

    def test_views_count_consistent_across_repeated_races(self):
        """Две волны гонок по разным сессиям → счётчик = числу сессий."""
        for wave in range(2):
            self.run_concurrent_jobs([
                self._record(session_key=f'wave-{wave}')
                for _ in range(CONCURRENT_REQUESTS)
            ])
        self.product.refresh_from_db()
        self.assertEqual(ProductView.objects.filter(product=self.product).count(), 2)
        self.assertEqual(self.product.views_count, self.initial_views_count + 2)

    def test_anonymous_without_session_is_not_deduplicated(self):
        """Без user и session_key дедупликация неприменима (контракт сохранён)."""
        first = AnalyticsService.record_view(self.product)
        second = AnalyticsService.record_view(self.product)
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
