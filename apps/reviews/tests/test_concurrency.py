# ────────────────────────────────────────────────────────────────────────
# apps/reviews/tests/test_concurrency.py
#
# ARCH-001 (H1) — устранение concurrency lost-update при пересчёте
# Product.rating / Product.reviews_count.
#
# Эти тесты — РЕАЛЬНЫЕ cross-connection тесты на PostgreSQL:
#   • TransactionTestCase (данные коммитятся, видны из других сессий);
#   • bounded-раннер apps.core.tests.concurrency (daemon-потоки +
#     Barrier для одновременного старта + join по общему дедлайну:
#     зависший воркер даёт fail, а не бесконечное ожидание — PROD-015);
#   • каждый поток работает на СОБСТВЕННОМ DB-соединении и закрывает
#     его в finally (иначе сессии держат test DB и teardown падает
#     с «database is being accessed by other users» — приём из
#     apps/discounts/tests/test_concurrency.py и
#     apps/pricing/tests/test_services.py::PriceBoundsConcurrencyTests).
#
# Гарантия H1:
#   ReviewService (create/update/delete/approve/reject) под
#   transaction.atomic ПЕРЕД расчётом агрегатов берёт row lock
#   authoritative Product (SELECT ... FOR UPDATE) и держит его до
#   COMMIT. Конкурентные операции над одним товаром сериализуются,
#   и последний коммитящий писатель публикует AVG/COUNT, посчитанные
#   по полному закоммиченному множеству одобренных Review.
#
# ИНВАРИАНТ (проверяется в КАЖДОМ сценарии, Test E):
#   Product.reviews_count == COUNT(Review WHERE product, is_approved)
#   Product.rating       == ROUND(AVG(Review.rating WHERE approved), 2)
#   (0.00 / 0 — для товара без одобренных отзывов).
# ────────────────────────────────────────────────────────────────────────

import threading
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import connections, transaction
from django.db.models import Avg
from django.test import TransactionTestCase, skipUnlessDBFeature

from apps.catalog.constants import ProductStatus
from apps.catalog.models import Brand, Category, Product
from apps.core.tests.concurrency import ConcurrentJobsMixin
from apps.orders.tests.factories import create_test_user
from apps.reviews.models import Review
from apps.reviews.services.review_service import ReviewService

User = get_user_model()

REVIEW_TEXT = 'Достаточно длинный текст отзыва для прохождения валидации сервиса.'


def _create_user(email):
    """Минимальный пользователь для отзыва (общая фабрика проектов)."""
    return create_test_user(email=email)


def _create_product(name='Concurrency Product'):
    """Минимальный товар без M2M/вариантов — для агрегатов отзывов."""
    brand = Brand.objects.create(name='ConcurrencyBrand')
    category = Category.add_root(name='ConcurrencyCat')
    return Product.objects.create(
        name=name,
        brand=brand,
        primary_category=category,
        status=ProductStatus.ACTIVE,
    )


def expected_aggregate_stats(product):
    """
    Эталонные агрегаты, посчитанные прямым запросом к Review
    (COUNT/AVG одобренных) — та же формула, что владеет reviews,
    но посчитанная независимо от сервисного пути.
    """
    approved = Review.objects.filter(product=product, is_approved=True)
    total = approved.count()
    avg_raw = approved.aggregate(avg=Avg('rating'))['avg']
    avg = round(Decimal(str(avg_raw)), 2) if total else Decimal('0.00')
    return total, avg


@skipUnlessDBFeature('has_select_for_update')
class ReviewAggregateConcurrencyTests(ConcurrentJobsMixin, TransactionTestCase):
    """
    ARCH-001 H1: конкурентная стратегия review-aggregate paths.

    Все production-пути пересчёта (create/update/delete/approve/
    reject) обёрнуты @transaction.atomic и перед расчётом агрегатов
    блокируют authoritative Product. Тесты проверяют это по-настоящему:
    при отсутствии лока сценарии A–D детерминированно оставляют
    Product.rating/reviews_count устаревшими (доказано regression-прогоном
    с временно удалённым select_for_update, см. PR body / отчёт).
    """

    def setUp(self):
        self.product = _create_product()

    # ──────────────────────────────────────────────────────────────
    # Инфраструктура параллельного запуска
    # ──────────────────────────────────────────────────────────────

    def _run_jobs(self, jobs, timeout=30):
        """
        Запускает функции одновременно (барьер старта). Каждый поток
        закрывает свои соединения в finally. Возвращает список
        результатов.

        PROD-015: ожидание ОГРАНИЧЕНО общим дедлайном (daemon-потоки +
        join по дедлайну). Ранее здесь использовался
        `with ThreadPoolExecutor(...)`: `future.result(timeout=...)`
        ограничивал только локальное ожидание, а выход из `with` звал
        shutdown(wait=True) и мог ждать зависший воркер бесконечно.
        Теперь зависание — детерминированный fail со стеком потока.
        """
        run = self.run_concurrent_jobs(jobs, timeout=timeout)
        return run.results

    def _assert_invariant(self, product=None):
        """
        Test E — инвариант агрегатов: денормализованные поля Product
        В ТОЧНОСТИ равны COUNT/AVG одобренных Review.
        """
        product = product or self.product
        product.refresh_from_db()
        total, avg = expected_aggregate_stats(product)
        self.assertEqual(
            product.reviews_count,
            total,
            f'reviews_count={product.reviews_count} != COUNT(approved)={total}',
        )
        self.assertEqual(
            product.rating,
            avg,
            f'rating={product.rating} != AVG(approved rating)={avg}',
        )
        return total, avg

    # ──────────────────────────────────────────────────────────────
    # Test A — concurrent create: два новых approved Review
    # ──────────────────────────────────────────────────────────────

    def test_concurrent_create_two_reviews(self):
        """
        Две транзакции конкурентно создают по ОДОБРЕННОМУ отзыву на
        один товар (разные пользователи). После завершения:
          Review count = 2, reviews_count = 2, rating = среднее двух.
        Без Product row lock один из агрегатов теряется
        (reviews_count=1, rating по одному отзыву).
        """
        user1 = _create_user('conc1@example.com')
        user2 = _create_user('conc2@example.com')
        product_id = self.product.pk

        def create(user_id, rating):
            product = Product.objects.get(pk=product_id)
            user = User.objects.get(pk=user_id)
            ReviewService.create_review(
                user=user,
                product=product,
                rating=rating,
                text=REVIEW_TEXT,
                title='Concurrent',
            )
            return 'ok'

        self._run_jobs([
            lambda: create(user1.pk, 5),
            lambda: create(user2.pk, 3),
        ])

        self.assertEqual(
            Review.objects.filter(product=self.product).count(), 2,
        )
        total, avg = self._assert_invariant()
        self.assertEqual(total, 2)
        self.assertEqual(avg, Decimal('4.00'))

    # ──────────────────────────────────────────────────────────────
    # Test B — concurrent create / delete
    # ──────────────────────────────────────────────────────────────

    def test_concurrent_create_and_delete(self):
        """
        Одна транзакция создаёт новый одобренный Review, вторая
        конкурентно удаляет существующий одобренный Review того же
        товара. После завершения агрегаты соответствуют фактическому
        множеству Review (1 оставшийся одобренный отзыв).
        """
        user_stay = _create_user('stay@example.com')
        user_gone = _create_user('gone@example.com')
        user_new = _create_user('new@example.com')
        review_stay = Review.objects.create(
            user=user_stay, product=self.product, rating=4,
            text=REVIEW_TEXT, is_approved=True,
        )
        review_gone = Review.objects.create(
            user=user_gone, product=self.product, rating=2,
            text=REVIEW_TEXT, is_approved=True,
        )
        product_id = self.product.pk
        gone_id = review_gone.pk
        new_user_id = user_new.pk
        # staff-актор для удаления чужого отзыва (создаём ДО потоков).
        admin = create_test_user(email='admin@example.com', is_staff=True)
        admin_id = admin.pk

        def create_new():
            product = Product.objects.get(pk=product_id)
            user = User.objects.get(pk=new_user_id)
            ReviewService.create_review(
                user=user, product=product, rating=5,
                text=REVIEW_TEXT, title='New',
            )
            return 'created'

        def delete_existing():
            review = Review.objects.select_related('product').get(pk=gone_id)
            actor = User.objects.get(pk=admin_id)
            ReviewService.delete_review(review, user=actor)
            return 'deleted'

        self._run_jobs([create_new, delete_existing])

        remaining = Review.objects.filter(product=self.product).count()
        self.assertEqual(remaining, 2)  # остались review_stay + новый
        self.assertFalse(Review.objects.filter(pk=gone_id).exists())
        self.assertTrue(Review.objects.filter(pk=review_stay.pk).exists())
        total, avg = self._assert_invariant()
        self.assertEqual(total, 2)  # оба оставшихся одобрены
        # Оставшиеся рейтинги: 4 (старый) и 5 (новый) → среднее 4.50.
        self.assertEqual(avg, Decimal('4.50'))

    # ──────────────────────────────────────────────────────────────
    # Test C — concurrent approve (два pending-отзыва)
    # ──────────────────────────────────────────────────────────────

    def test_concurrent_approve_two_pending_reviews(self):
        """
        Две транзакции конкурентно одобряют РАЗНЫЕ pending-отзывы
        одного товара. После завершения оба отзыва входят в агрегаты:
          reviews_count = 2, rating = среднее двух.
        Без Product row lock агрегат одного из approve теряется
        (оба потока видят 0/1 одобренных и пишут устаревшее).
        """
        user1 = _create_user('pend1@example.com')
        user2 = _create_user('pend2@example.com')
        review1 = Review.objects.create(
            user=user1, product=self.product, rating=5,
            text=REVIEW_TEXT, is_approved=False,
        )
        review2 = Review.objects.create(
            user=user2, product=self.product, rating=1,
            text=REVIEW_TEXT, is_approved=False,
        )
        id1, id2 = review1.pk, review2.pk

        def approve(review_id):
            review = Review.objects.select_related('product').get(pk=review_id)
            ReviewService.approve_review(review)
            return 'approved'

        self._run_jobs([
            lambda: approve(id1),
            lambda: approve(id2),
        ])

        self.assertTrue(Review.objects.get(pk=id1).is_approved)
        self.assertTrue(Review.objects.get(pk=id2).is_approved)
        total, avg = self._assert_invariant()
        self.assertEqual(total, 2)
        self.assertEqual(avg, Decimal('3.00'))

    def test_concurrent_approve_and_reject_same_review(self):
        """
        Конкурентные approve и reject ОДНОГО отзыва сериализуются
        локом Review (его собственный UPDATE) И локом Product:
        агрегат в итоге соответствует финальному состоянию отзыва.
        Исход детерминированно один из двух (approved или rejected),
        но в любом случае инвариант агрегатов выполняется.
        """
        user = _create_user('same@example.com')
        review = Review.objects.create(
            user=user, product=self.product, rating=5,
            text=REVIEW_TEXT, is_approved=False,
        )
        review_id = review.pk

        def moderate(approved):
            review_obj = Review.objects.select_related('product').get(pk=review_id)
            if approved:
                ReviewService.approve_review(review_obj)
                return 'approved'
            ReviewService.reject_review(review_obj)
            return 'rejected'

        results = self._run_jobs([
            lambda: moderate(True),
            lambda: moderate(False),
        ])

        self.assertEqual(sorted(results), ['approved', 'rejected'])
        final_approved = Review.objects.get(pk=review_id).is_approved
        total, avg = self._assert_invariant()
        self.assertEqual(total, 1 if final_approved else 0)
        self.assertEqual(avg, Decimal('5.00') if final_approved else Decimal('0.00'))

    # ──────────────────────────────────────────────────────────────
    # Test D — concurrent update rating двух отзывов
    # ──────────────────────────────────────────────────────────────

    def test_concurrent_update_rating_two_reviews(self):
        """
        Две транзакции конкурентно меняют rating ДВУХ существующих
        одобренных отзывов одного товара (update_review пересчитывает
        агрегаты). После завершения rating товара = среднее НОВЫХ
        значений (4 и 2 → 3.00), а не устаревших/смешанных.
        """
        user1 = _create_user('upd1@example.com')
        user2 = _create_user('upd2@example.com')
        review1 = Review.objects.create(
            user=user1, product=self.product, rating=5,
            text=REVIEW_TEXT, is_approved=True,
        )
        review2 = Review.objects.create(
            user=user2, product=self.product, rating=5,
            text=REVIEW_TEXT, is_approved=True,
        )
        id1, id2 = review1.pk, review2.pk

        def update_rating(review_id, user_id, new_rating):
            review_obj = Review.objects.select_related('product').get(pk=review_id)
            user = User.objects.get(pk=user_id)
            ReviewService.update_review(
                review_obj, user=user, rating=new_rating,
            )
            return 'updated'

        self._run_jobs([
            lambda: update_rating(id1, user1.pk, 4),
            lambda: update_rating(id2, user2.pk, 2),
        ])

        self.assertEqual(Review.objects.get(pk=id1).rating, 4)
        self.assertEqual(Review.objects.get(pk=id2).rating, 2)
        total, avg = self._assert_invariant()
        self.assertEqual(total, 2)
        self.assertEqual(avg, Decimal('3.00'))

    def test_mixed_concurrent_paths_end_consistent(self):
        """
        Стресс lock-order/deadlock: ОДНОВРЕМЕННО по одному товару идут
        все пять authoritative-путей — create, update, delete,
        approve, reject (+ helpful vote, который Product не лочит).
        Ни один поток не должен зависнуть (deadlock) или упасть;
        после завершения инвариант агрегатов выполняется строго.

        Если бы существовал обратный порядок захвата
        (Review-lock → Product-lock у одного пути против
        Product-lock → Review-lock у другого), этот тест стабильно
        ловил бы deadlock (lock timeout / зависшие потоки).
        """
        # Базовое состояние: 3 одобренных + 2 pending отзыва.
        users = {f'u{i}': _create_user(f'mix{i}@example.com') for i in range(8)}
        approved = [
            Review.objects.create(
                user=users['u0'], product=self.product, rating=5,
                text=REVIEW_TEXT, is_approved=True,
            ),
            Review.objects.create(
                user=users['u1'], product=self.product, rating=4,
                text=REVIEW_TEXT, is_approved=True,
            ),
            Review.objects.create(
                user=users['u2'], product=self.product, rating=3,
                text=REVIEW_TEXT, is_approved=True,
            ),
        ]
        pending = [
            Review.objects.create(
                user=users['u3'], product=self.product, rating=2,
                text=REVIEW_TEXT, is_approved=False,
            ),
            Review.objects.create(
                user=users['u4'], product=self.product, rating=1,
                text=REVIEW_TEXT, is_approved=False,
            ),
        ]
        voter = users['u5']
        admin = create_test_user(email='mix-admin@example.com', is_staff=True)
        product_id = self.product.pk
        ids = {
            'upd': approved[0].pk,
            'del': approved[1].pk,
            'appr': pending[0].pk,
            'rejt': pending[1].pk,
            'vote': approved[2].pk,
        }

        def job_create():
            product = Product.objects.get(pk=product_id)
            ReviewService.create_review(
                user=User.objects.get(pk=users['u6'].pk), product=product,
                rating=5, text=REVIEW_TEXT, title='Mixed create',
            )
            return 'create'

        def job_update():
            review = Review.objects.select_related('product').get(pk=ids['upd'])
            ReviewService.update_review(
                review, user=User.objects.get(pk=users['u0'].pk), rating=1,
            )
            return 'update'

        def job_delete():
            review = Review.objects.select_related('product').get(pk=ids['del'])
            ReviewService.delete_review(
                review, user=User.objects.get(pk=admin.pk),
            )
            return 'delete'

        def job_approve():
            review = Review.objects.select_related('product').get(pk=ids['appr'])
            ReviewService.approve_review(review)
            return 'approve'

        def job_reject():
            review = Review.objects.select_related('product').get(pk=ids['rejt'])
            ReviewService.reject_review(review)
            return 'reject'

        def job_vote():
            # Helpful vote: лочит Review/vote, агрегаты НЕ трогает.
            review = Review.objects.select_related('product').get(pk=ids['vote'])
            ReviewService.vote_helpful(
                review, user=User.objects.get(pk=voter.pk), vote='yes',
            )
            return 'vote'

        results = self._run_jobs([
            job_create, job_update, job_delete,
            job_approve, job_reject, job_vote,
        ], timeout=45)
        self.assertEqual(sorted(results),
                         ['approve', 'create', 'delete', 'reject', 'update', 'vote'])

        # Ожидаемое множество: одобрены approved[0](=1), approved[2](=3),
        # pending[0] одобрен(=2), новый(=5); удалён approved[1];
        # pending[1] отклонён → не считается.
        self.assertFalse(Review.objects.filter(pk=ids['del']).exists())
        self.assertTrue(Review.objects.get(pk=ids['appr']).is_approved)
        self.assertFalse(Review.objects.get(pk=ids['rejt']).is_approved)
        total, avg = self._assert_invariant()
        self.assertEqual(total, 4)
        # Рейтинги: 1 (updated), 3, 2 (approved pending), 5 (new) → 2.75.
        self.assertEqual(avg, Decimal('2.75'))

    # ──────────────────────────────────────────────────────────────
    # Lock-coverage: критичный участок реально заблокирован
    # ──────────────────────────────────────────────────────────────

    def test_service_blocks_while_product_lock_held(self):
        """
        Пока внешняя транзакция держит SELECT ... FOR UPDATE на
        Product, пересчёт агрегатов конкурента БЛОКИРУЕТСЯ (не пишет
        rating/reviews_count). После COMMIT внешней транзакции воркер
        завершается и публикует корректный агрегат.
        """
        user = _create_user('lock@example.com')
        product_id = self.product.pk
        user_id = user.pk
        done = threading.Event()
        worker_errors = []

        def worker():
            try:
                connections.close_all()
                product = Product.objects.get(pk=product_id)
                ReviewService.create_review(
                    user=User.objects.get(pk=user_id),
                    product=product,
                    rating=5,
                    text=REVIEW_TEXT,
                )
            except Exception as exc:  # noqa: BLE001
                worker_errors.append(exc)
            finally:
                connections.close_all()
                done.set()

        worker_thread = threading.Thread(target=worker, daemon=True)

        with transaction.atomic():
            # Внешняя транзакция захватывает лок authoritative Product.
            Product.objects.select_for_update().get(pk=product_id)
            worker_thread.start()
            # Воркер НЕ должен успеть завершиться, пока лок удерживается.
            finished_while_locked = done.wait(timeout=2)

        self.assertFalse(
            finished_while_locked,
            'create_review НЕ заблокировался на Product row lock — '
            'критический участок пересчёта агрегатов не защищён '
            '(select_for_update не работает / потерян)',
        )
        self.assertEqual(worker_errors, [])

        # Лок отпущен (COMMIT) — воркер завершается.
        worker_thread.join(timeout=15)
        self.assertFalse(worker_thread.is_alive(), 'Воркер не завершился')
        self.assertEqual(worker_errors, [])

        total, avg = self._assert_invariant()
        self.assertEqual(total, 1)
        self.assertEqual(avg, Decimal('5.00'))

    def test_vote_helpful_does_not_touch_product_aggregates(self):
        """
        Lock-order guard (deadlock analysis H1): путь helpful vote
        лочит Review/vote, но НЕ лочит Product и НЕ пересчитывает
        агрегаты — поэтому обратного порядка «Review lock → Product
        lock» в production нет (цикла ожидания с агрегатными путями
        «Product → Review» быть не может). Проверяем поведенчески:
        голос не меняет rating/reviews_count.
        """
        author = _create_user('author@example.com')
        voter = _create_user('voter@example.com')
        # Отзыв создаём через сервис — агрегаты установлены корректно
        # (vote-путь сам их пересчитывать НЕ должен).
        review = ReviewService.create_review(
            user=author, product=self.product, rating=4,
            text=REVIEW_TEXT,
        )
        self._assert_invariant()

        ReviewService.vote_helpful(review, user=voter, vote='yes')

        self.product.refresh_from_db()
        self.assertEqual(self.product.reviews_count, 1)
        self.assertEqual(self.product.rating, Decimal('4.00'))
        self.assertEqual(Review.objects.get(pk=review.pk).helpful_yes, 1)


@skipUnlessDBFeature('has_select_for_update')
class RecalculateRequiresTransactionTests(TransactionTestCase):
    """
    Контракт H1: пересчёт агрегатов требует открытой транзакции
    вызывающего (select_for_update вне atomic не имеет смысла и
    Django бросает TransactionManagementError). Все production-методы
    (create/update/delete/approve/reject) обёрнуты @transaction.atomic,
    поэтому в проде путь всегда транзакционный.
    """

    def test_recalculate_outside_atomic_raises(self):
        from django.db.transaction import TransactionManagementError

        product = _create_product(name='NoTxn Product')
        with self.assertRaises(TransactionManagementError):
            ReviewService.recalculate_product_rating(product)
