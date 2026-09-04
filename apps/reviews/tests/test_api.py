# ────────────────────────────────────────────────────────────────────────
# apps/reviews/tests/test_api.py
#
# Тесты API отзывов:
#   - Список (публичный + фильтрация + сортировка + пагинация)
#   - Создание (требует авторизацию)
#   - Детали (публичный)
#   - Обновление / удаление (требует авторизацию)
#   - Голосование за полезность (toggle-логика)
# ────────────────────────────────────────────────────────────────────────

from django.test import TestCase
from django.urls import reverse

from rest_framework import status
from rest_framework.test import APIClient

from apps.core.api_errors import CODE_NOT_FOUND
from apps.catalog.tests.factories import CatalogTestCase
from apps.orders.tests.factories import create_test_user
from apps.reviews.models import ReviewHelpfulVote
from apps.reviews.tests.factories import create_test_review


class ReviewListPublicTests(CatalogTestCase):
    """GET /reviews/ — публичный эндпоинт (AllowAny)."""

    def setUp(self):
        self.user = create_test_user()
        self.client = APIClient()
        self.url = reverse('reviews:review-list')
        self.r5 = create_test_review(
            self.user, self.product, rating=5,
            text='Отличный товар, очень доволен!',
        )
        self.other_user = create_test_user()
        self.r3 = create_test_review(
            self.other_user, self.product, rating=3,
            text='Нормальный товар за свои деньги.',
        )

    def test_list_without_auth(self):
        resp = self.client.get(self.url, {'product_id': self.product.pk})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn('results', resp.data)
        self.assertEqual(resp.data['count'], 2)

    def test_list_by_product_uuid(self):
        resp = self.client.get(self.url, {'product_uuid': str(self.product.uuid)})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['count'], 2)

    def test_list_filter_by_rating(self):
        resp = self.client.get(self.url, {
            'product_id': self.product.pk, 'rating': 5,
        })
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['count'], 1)
        self.assertEqual(resp.data['results'][0]['rating'], 5)

    def test_list_filter_by_rating_gte(self):
        resp = self.client.get(self.url, {
            'product_id': self.product.pk, 'rating_gte': 4,
        })
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['count'], 1)

    def test_list_filter_by_rating_lte(self):
        resp = self.client.get(self.url, {
            'product_id': self.product.pk, 'rating_lte': 3,
        })
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['count'], 1)

    def test_list_filter_verified(self):
        resp = self.client.get(self.url, {
            'product_id': self.product.pk, 'verified': 'true',
        })
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['count'], 0)

    def test_list_sort_by_rating_desc(self):
        resp = self.client.get(self.url, {
            'product_id': self.product.pk, 'ordering': '-rating',
        })
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        ratings = [r['rating'] for r in resp.data['results']]
        self.assertEqual(ratings, sorted(ratings, reverse=True))

    def test_list_sort_by_rating_asc(self):
        resp = self.client.get(self.url, {
            'product_id': self.product.pk, 'ordering': 'rating',
        })
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        ratings = [r['rating'] for r in resp.data['results']]
        self.assertEqual(ratings, sorted(ratings))

    def test_list_sort_by_helpful(self):
        resp = self.client.get(self.url, {
            'product_id': self.product.pk, 'ordering': 'helpful',
        })
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['count'], 2)

    def test_list_pagination(self):
        resp = self.client.get(self.url, {
            'product_id': self.product.pk, 'page': 1, 'page_size': 1,
        })
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data['results']), 1)
        self.assertEqual(resp.data['count'], 2)
        self.assertEqual(resp.data['page'], 1)
        self.assertEqual(resp.data['page_size'], 1)
        self.assertEqual(resp.data['total_pages'], 2)
        self.assertIsNone(resp.data['previous'])
        self.assertIsNotNone(resp.data['next'])

    def test_list_by_own_user_id(self):
        self.client.force_authenticate(self.user)

        resp = self.client.get(self.url, {'user_id': self.user.pk})

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['count'], 1)
        self.assertEqual(
            [item['id'] for item in resp.data['results']],
            [self.r5.pk],
        )
        self.assertTrue(
            all(item['user_id'] == self.user.pk for item in resp.data['results'])
        )

    def test_list_by_other_user_id_returns_canonical_404(self):
        self.client.force_authenticate(self.user)

        resp = self.client.get(self.url, {'user_id': self.other_user.pk})

        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)
        error = resp.data['error']
        self.assertEqual(set(error), {'code', 'message', 'details'})
        self.assertEqual(error['code'], CODE_NOT_FOUND)
        self.assertIsInstance(error['message'], str)
        self.assertTrue(error['message'])
        self.assertIsInstance(error['details'], list)
        for detail in error['details']:
            self.assertEqual(set(detail), {'field', 'code', 'message'})

        # The request must not be rewritten to the caller's id, and the error
        # must not disclose identifiers or implementation details. Ignore the
        # optional outer request_id, which is an allowed API-04 correlation id.
        error_text = str(error).lower()
        self.assertNotIn(str(self.other_user.pk), error_text)
        for internal_detail in ('traceback', 'valueerror', 'exception'):
            self.assertNotIn(internal_detail, error_text)

    def test_list_without_user_id_keeps_own_review_scope(self):
        self.client.force_authenticate(self.user)

        resp = self.client.get(self.url)

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['count'], 1)
        self.assertTrue(
            all(item['user_id'] == self.user.pk for item in resp.data['results'])
        )

    def test_staff_can_filter_by_other_user_id(self):
        staff = create_test_user(is_staff=True)
        self.client.force_authenticate(staff)

        resp = self.client.get(self.url, {'user_id': self.other_user.pk})

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['count'], 1)
        self.assertEqual(
            [item['id'] for item in resp.data['results']],
            [self.r3.pk],
        )
        self.assertTrue(
            all(
                item['user_id'] == self.other_user.pk
                for item in resp.data['results']
            )
        )

    def test_anonymous_without_product_filter(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['count'], 0)
        self.assertEqual(resp.data['results'], [])
        self.assertEqual(resp.data['total_pages'], 0)

    def test_list_my_vote_for_authenticated(self):
        """Авторизованный пользователь видит свой голос (my_vote)."""
        voter = create_test_user()
        # Голосуем за первый отзыв
        ReviewHelpfulVote.objects.create(
            user=voter, review=self.r5, vote='yes',
        )
        self.r5.helpful_yes = 1
        self.r5.save(update_fields=['helpful_yes'])

        self.client.force_authenticate(voter)
        resp = self.client.get(self.url, {'product_id': self.product.pk})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        results = resp.data['results']
        # Находим r5 в результатах
        r5_data = next(r for r in results if r['id'] == self.r5.pk)
        self.assertEqual(r5_data['my_vote'], 'yes')
        # r3 — не голосовали
        r3_data = next(r for r in results if r['id'] == self.r3.pk)
        self.assertIsNone(r3_data['my_vote'])


class ReviewCreateAPITests(CatalogTestCase):
    """POST /reviews/ — создание (требует авторизацию)."""

    def setUp(self):
        self.user = create_test_user()
        self.client = APIClient()
        self.url = reverse('reviews:review-list')

    def test_create_requires_auth(self):
        data = {'product_id': self.product.pk, 'rating': 4, 'text': 'Отличный!'}
        resp = self.client.post(self.url, data, format='json')
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_create_review(self):
        self.client.force_authenticate(self.user)
        data = {'product_id': self.product.pk, 'rating': 4, 'text': 'Отличный телефон!'}
        resp = self.client.post(self.url, data, format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data['rating'], 4)

    def test_create_review_with_uuid(self):
        self.client.force_authenticate(self.user)
        data = {
            'product_uuid': str(self.product.uuid),
            'rating': 5, 'text': 'Замечательный товар!',
        }
        resp = self.client.post(self.url, data, format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)

    def test_create_duplicate_fails(self):
        self.client.force_authenticate(self.user)
        data = {'product_id': self.product.pk, 'rating': 4, 'text': 'Отличный!'}
        self.client.post(self.url, data, format='json')
        resp = self.client.post(self.url, data, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_create_review_invalid_product(self):
        self.client.force_authenticate(self.user)
        data = {'product_id': 99999, 'rating': 5, 'text': 'Отличный телефон!'}
        resp = self.client.post(self.url, data, format='json')
        # product_id=99999 не найден → 404, или валидация → 400
        self.assertIn(resp.status_code, [status.HTTP_404_NOT_FOUND, status.HTTP_400_BAD_REQUEST])


class ReviewDetailAPITests(CatalogTestCase):
    """GET/PATCH/DELETE /reviews/{id}/."""

    def setUp(self):
        self.user = create_test_user()
        self.client = APIClient()
        self.review = create_test_review(
            self.user, self.product, rating=4, text='Отличный!',
        )
        self.url = reverse('reviews:review-detail', kwargs={'review_id': self.review.pk})

    def test_get_detail_public(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['rating'], 4)

    def test_update_review(self):
        self.client.force_authenticate(self.user)
        resp = self.client.patch(self.url, {'rating': 3}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['rating'], 3)

    def test_update_wrong_user(self):
        other = create_test_user()
        self.client.force_authenticate(other)
        resp = self.client.patch(self.url, {'rating': 1}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_delete_review(self):
        self.client.force_authenticate(self.user)
        resp = self.client.delete(self.url)
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)


class ReviewHelpfulAPITests(CatalogTestCase):
    """POST /reviews/{id}/helpful/ — toggle-логика."""

    def setUp(self):
        self.author = create_test_user()
        self.review = create_test_review(
            self.author, self.product, rating=4, text='Отличный!',
        )
        self.voter = create_test_user()
        self.client = APIClient()
        self.url = reverse('reviews:review-helpful', kwargs={'review_id': self.review.pk})

    def test_first_vote_yes(self):
        """Первый голос 'yes' → helpful_yes=1."""
        self.client.force_authenticate(self.voter)
        resp = self.client.post(self.url, {'vote': 'yes'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['helpful_yes'], 1)
        self.assertEqual(resp.data['helpful_no'], 0)
        self.assertEqual(resp.data['my_vote'], 'yes')

    def test_first_vote_no(self):
        """Первый голос 'no' → helpful_no=1."""
        self.client.force_authenticate(self.voter)
        resp = self.client.post(self.url, {'vote': 'no'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['helpful_yes'], 0)
        self.assertEqual(resp.data['helpful_no'], 1)

    def test_toggle_off(self):
        """Повторный тот же голос → toggle off (отмена)."""
        self.client.force_authenticate(self.voter)
        self.client.post(self.url, {'vote': 'yes'}, format='json')
        resp = self.client.post(self.url, {'vote': 'yes'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['helpful_yes'], 0)
        self.assertEqual(resp.data['helpful_no'], 0)
        self.assertIsNone(resp.data['my_vote'])

    def test_switch_yes_to_no(self):
        """Переключение yes→no."""
        self.client.force_authenticate(self.voter)
        self.client.post(self.url, {'vote': 'yes'}, format='json')
        resp = self.client.post(self.url, {'vote': 'no'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['helpful_yes'], 0)
        self.assertEqual(resp.data['helpful_no'], 1)
        self.assertEqual(resp.data['my_vote'], 'no')

    def test_switch_no_to_yes(self):
        """Переключение no→yes."""
        self.client.force_authenticate(self.voter)
        self.client.post(self.url, {'vote': 'no'}, format='json')
        resp = self.client.post(self.url, {'vote': 'yes'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['helpful_yes'], 1)
        self.assertEqual(resp.data['helpful_no'], 0)

    def test_author_cannot_vote_own_review(self):
        """Автор не может голосовать за свой отзыв."""
        self.client.force_authenticate(self.author)
        resp = self.client.post(self.url, {'vote': 'yes'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_vote_requires_auth(self):
        resp = self.client.post(self.url, {'vote': 'yes'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_invalid_vote_value(self):
        self.client.force_authenticate(self.voter)
        resp = self.client.post(self.url, {'vote': 'maybe'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_two_different_users(self):
        """Два разных пользователя — два независимых голоса."""
        voter2 = create_test_user()
        self.client.force_authenticate(self.voter)
        self.client.post(self.url, {'vote': 'yes'}, format='json')
        self.client.force_authenticate(voter2)
        resp = self.client.post(self.url, {'vote': 'no'}, format='json')
        self.assertEqual(resp.data['helpful_yes'], 1)
        self.assertEqual(resp.data['helpful_no'], 1)

    def test_cannot_vote_infinite(self):
        """Нельзя накрутить голоса: повторный тот же голос = toggle off."""
        self.client.force_authenticate(self.voter)
        self.client.post(self.url, {'vote': 'yes'}, format='json')
        # Повторный yes → toggle off
        resp = self.client.post(self.url, {'vote': 'yes'}, format='json')
        self.assertEqual(resp.data['helpful_yes'], 0)
        # Снова yes → добавляется
        resp = self.client.post(self.url, {'vote': 'yes'}, format='json')
        self.assertEqual(resp.data['helpful_yes'], 1)
        # И снова yes → toggle off
        resp = self.client.post(self.url, {'vote': 'yes'}, format='json')
        self.assertEqual(resp.data['helpful_yes'], 0)
