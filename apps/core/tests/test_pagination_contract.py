"""API-05 contract tests for the canonical collection/pagination envelope.

These tests assert public HTTP behaviour of every paginated collection
endpoint, not internal implementation details.
"""

from decimal import Decimal

from django.urls import reverse
from rest_framework import status

from apps.catalog.constants import ProductStatus
from apps.core.pagination import DEFAULT_PAGE_SIZE
from apps.catalog.models import Product
from apps.catalog.tests.factories import CatalogTestCase
from apps.discounts.tests.factories import create_test_coupon
from apps.inventory.tests.factories import create_test_movement, create_test_stock
from apps.notifications.tests.factories import create_test_notification
from apps.orders.tests.factories import create_test_order, create_test_user
from apps.payments.tests.factories import create_test_payment
from apps.pricing.models import PriceHistory
from apps.reviews.tests.factories import create_test_review
from apps.shipping.tests.factories import create_test_method, create_test_shipment

CANONICAL_FIELDS = {
    'count',
    'page',
    'page_size',
    'total_pages',
    'next',
    'previous',
    'results',
}


def assert_canonical_shape(self, data):
    """Assert the API-05 canonical collection envelope."""
    self.assertTrue(CANONICAL_FIELDS.issubset(data.keys()), data)
    self.assertIsInstance(data['count'], int)
    self.assertIsInstance(data['page'], int)
    self.assertIsInstance(data['page_size'], int)
    self.assertIsInstance(data['total_pages'], int)
    self.assertIsInstance(data['results'], list)
    self.assertTrue(data['next'] is None or isinstance(data['next'], str))
    self.assertTrue(data['previous'] is None or isinstance(data['previous'], str))


class PaginationContractTests(CatalogTestCase):
    """Shared setup and endpoint-level contract checks."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()

        cls.user = create_test_user()
        cls.staff = create_test_user(is_staff=True)
        cls.other_user = create_test_user()
        cls.third_user = create_test_user()

        # Products (the base CatalogTestCase already adds one active product).
        cls.products = [cls.product]
        for index, name in enumerate(('Zeta', 'Alpha', 'Beta'), start=1):
            product = Product.objects.create(
                name=name,
                brand=cls.brand,
                primary_category=cls.leaf_category,
                status=ProductStatus.ACTIVE,
            )
            cls.products.append(product)

        # Orders / payments.
        cls.orders = [create_test_order(cls.user) for _ in range(3)]
        cls.payments = [
            create_test_payment(order, cls.user)
            for order in cls.orders
        ]

        # Inventory stock + movement history.
        cls.stock = create_test_stock(cls.variant_128, quantity=100)
        cls.stock_2 = create_test_stock(cls.variant_256, quantity=10)
        for _ in range(3):
            create_test_movement(cls.stock)

        # Price history (direct rows; the pricing service is under test
        # elsewhere and uses row locks that are unnecessary in a contract fixture).
        for old_price, new_price in (
            (Decimal('100.00'), Decimal('90.00')),
            (Decimal('90.00'), Decimal('80.00')),
            (Decimal('80.00'), Decimal('70.00')),
        ):
            PriceHistory.objects.create(
                variant=cls.variant_128,
                old_price=old_price,
                new_price=new_price,
                old_sale_price=None,
                new_sale_price=None,
                changed_by=cls.staff,
                reason='contract fixture',
            )

        # Coupons.
        for code in ('API05A', 'API05B', 'API05C'):
            create_test_coupon(code=code)

        # Shipments (reuse one method so the helper does not create a
        # duplicate default zone with the same unique zone_code).
        cls.shipping_method = create_test_method()
        for order in cls.orders:
            create_test_shipment(order, method=cls.shipping_method)

        # Notifications.
        for index in range(3):
            create_test_notification(cls.user, title=f'N{index}')
            create_test_notification(cls.other_user, title=f'Other{index}')

        # Reviews for the base product.
        for user, rating in ((cls.user, 5), (cls.other_user, 4), (cls.third_user, 3)):
            create_test_review(user, cls.product, rating=rating)

    def setUp(self):
        from rest_framework.test import APIClient

        self.client = APIClient()

    # ------------------------------------------------------------------
    # Endpoint inventory
    # ------------------------------------------------------------------

    def _paginated_endpoints(self):
        """Return every public collection endpoint classified as paginated.

        Each item is ``(label, url, user, base_params)``. ``base_params`` are the
        query parameters required to observe an existing collection for that
        endpoint (for example ``product_id`` for the public reviews list).

        The per-resource identity key used by the stability assertions lives
        in ``IDENTITY_KEYS`` — after F-8 (#73) not every resource is
        addressed by ``id`` (shipments use ``shipment_number``).
        """
        price_history_url = reverse(
            'pricing:variant-price-history',
            kwargs={'variant_id': self.variant_128.pk},
        )
        movements_url = reverse(
            'inventory:stock-movements',
            kwargs={'variant_id': self.variant_128.pk},
        )
        return [
            ('products', reverse('catalog:product-list'), None, {}),
            ('orders', reverse('orders:order-list'), self.user, {}),
            ('payments', reverse('payments:payment-list'), self.user, {}),
            ('inventory', reverse('inventory:stock-list'), self.staff, {}),
            ('movements', movements_url, self.staff, {}),
            ('price-history', price_history_url, self.staff, {}),
            ('coupons', reverse('discounts:coupon-list'), self.staff, {}),
            ('shipments', reverse('shipping:shipment-list'), self.user, {}),
            ('notifications', reverse('notifications:notification-list'), self.user, {}),
            ('notifications-unread', reverse('notifications:notification-unread'), self.user, {}),
            ('reviews', reverse('reviews:review-list'), None, {
                'product_id': str(self.product.uuid),
            }),
        ]

    # F-8 (#73): канонический идентификатор элемента коллекции по ресурсу.
    # Значение по умолчанию — 'id'; переопределяется там, где контракт
    # заморозил другой публичный ключ.
    IDENTITY_KEYS = {
        'shipments': 'shipment_number',
    }

    def _identity_key(self, label):
        return self.IDENTITY_KEYS.get(label, 'id')

    def _request_paginated(self, url, user, base_params, params):
        """Issue a request to a paginated endpoint with merged query params."""
        self.client.force_authenticate(user or None)
        query = dict(base_params)
        query.update(params)
        return self.client.get(url, query)

    # ------------------------------------------------------------------
    # Canonical shape + pagination bounds
    # ------------------------------------------------------------------

    def test_every_paginated_endpoint_returns_canonical_shape(self):
        for label, url, user, base_params in self._paginated_endpoints():
            with self.subTest(endpoint=label):
                response = self._request_paginated(url, user, base_params, {})
                self.assertEqual(response.status_code, status.HTTP_200_OK)
                assert_canonical_shape(self, response.data)
                self.assertGreaterEqual(response.data['count'], 1)

    def test_every_paginated_endpoint_uses_default_page_and_page_size(self):
        for label, url, user, base_params in self._paginated_endpoints():
            with self.subTest(endpoint=label):
                response = self._request_paginated(url, user, base_params, {})
                self.assertEqual(response.status_code, status.HTTP_200_OK)
                self.assertEqual(response.data['page'], 1)
                self.assertEqual(response.data['page_size'], DEFAULT_PAGE_SIZE)
                self.assertEqual(
                    response.data['total_pages'],
                    (response.data['count'] + DEFAULT_PAGE_SIZE - 1)
                    // DEFAULT_PAGE_SIZE,
                )

    def test_every_paginated_endpoint_honours_custom_page_size_and_bounds(self):
        for label, url, user, base_params in self._paginated_endpoints():
            with self.subTest(endpoint=label):
                # Minimum (1) and an explicit custom page_size.
                minimum = self._request_paginated(
                    url, user, base_params, {'page_size': 1},
                )
                self.assertEqual(minimum.status_code, status.HTTP_200_OK)
                self.assertEqual(minimum.data['page_size'], 1)
                self.assertEqual(len(minimum.data['results']), 1)

                # Maximum allowed page_size (100) is accepted.
                maximum = self._request_paginated(
                    url, user, base_params, {'page_size': 100},
                )
                self.assertEqual(maximum.status_code, status.HTTP_200_OK)
                self.assertEqual(maximum.data['page_size'], 100)
                self.assertEqual(maximum.data['count'], minimum.data['count'])

                # Above maximum, zero and negative are API-04 validation errors.
                for params in (
                    {'page_size': 101},
                    {'page_size': 0},
                    {'page_size': -1},
                ):
                    with self.subTest(endpoint=label, params=params):
                        response = self._request_paginated(
                            url, user, base_params, params,
                        )
                        self.assertEqual(
                            response.status_code, status.HTTP_400_BAD_REQUEST,
                        )
                        self.assertEqual(
                            response.data['error']['code'], 'validation_error',
                        )

    def test_every_paginated_endpoint_rejects_non_integer_page_params(self):
        for label, url, user, base_params in self._paginated_endpoints():
            for params in ({'page': 'abc'}, {'page_size': 'abc'}):
                with self.subTest(endpoint=label, params=params):
                    response = self._request_paginated(
                        url, user, base_params, params,
                    )
                    self.assertEqual(
                        response.status_code, status.HTTP_400_BAD_REQUEST,
                    )
                    self.assertEqual(
                        response.data['error']['code'], 'validation_error',
                    )

    def test_every_paginated_endpoint_rejects_zero_and_negative_page(self):
        for label, url, user, base_params in self._paginated_endpoints():
            for params in ({'page': 0}, {'page': -1}):
                with self.subTest(endpoint=label, params=params):
                    response = self._request_paginated(
                        url, user, base_params, params,
                    )
                    self.assertEqual(
                        response.status_code, status.HTTP_400_BAD_REQUEST,
                    )
                    self.assertEqual(
                        response.data['error']['code'], 'validation_error',
                    )

    # ------------------------------------------------------------------
    # First / middle / last / beyond-last pages
    # ------------------------------------------------------------------

    def test_first_middle_last_and_beyond_last_pages(self):
        self.client.force_authenticate(self.user)
        url = reverse('orders:order-list')
        total = len(self.orders)

        first = self.client.get(url, {'page': 1, 'page_size': 1})
        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(first.data['page'], 1)
        self.assertEqual(first.data['count'], total)
        self.assertEqual(len(first.data['results']), 1)
        self.assertIsNotNone(first.data['next'])
        self.assertIsNone(first.data['previous'])

        middle = self.client.get(url, {'page': 2, 'page_size': 1})
        self.assertEqual(middle.status_code, status.HTTP_200_OK)
        self.assertEqual(middle.data['page'], 2)
        self.assertEqual(len(middle.data['results']), 1)

        last = self.client.get(url, {'page': total, 'page_size': 1})
        self.assertEqual(last.status_code, status.HTTP_200_OK)
        self.assertEqual(last.data['page'], total)
        self.assertIsNone(last.data['next'])
        self.assertIsNotNone(last.data['previous'])

        beyond = self.client.get(url, {'page': total + 10, 'page_size': 1})
        self.assertEqual(beyond.status_code, status.HTTP_200_OK)
        self.assertEqual(beyond.data['page'], total + 10)
        self.assertEqual(beyond.data['results'], [])
        self.assertEqual(beyond.data['count'], total)
        self.assertIsNone(beyond.data['next'])

    def test_empty_collection_has_one_representation(self):
        # A brand-new user has an empty notification stream and still receives
        # the canonical empty envelope (not a bare array, not a 404).
        empty_user = create_test_user()
        self.client.force_authenticate(empty_user)
        url = reverse('notifications:notification-list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 0)
        self.assertEqual(response.data['results'], [])
        self.assertEqual(response.data['total_pages'], 0)
        self.assertIsNone(response.data['next'])
        self.assertIsNone(response.data['previous'])

    def test_reviews_empty_collection_uses_canonical_envelope(self):
        # The public reviews list for a product with no reviews still returns
        # the canonical empty envelope (not a bare array, not a 404).
        reviewless_product = self.products[1]
        response = self.client.get(
            reverse('reviews:review-list'),
            {'product_id': str(reviewless_product.uuid)},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 0)
        self.assertEqual(response.data['results'], [])
        self.assertEqual(response.data['total_pages'], 0)
        self.assertEqual(response.data['page'], 1)
        self.assertEqual(response.data['page_size'], DEFAULT_PAGE_SIZE)
        self.assertIsNone(response.data['next'])
        self.assertIsNone(response.data['previous'])

    # ------------------------------------------------------------------
    # Deterministic page boundaries
    # ------------------------------------------------------------------

    def test_every_paginated_endpoint_first_middle_last_and_beyond_last_pages(self):
        for label, url, user, base_params in self._paginated_endpoints():
            with self.subTest(endpoint=label):
                total = self._request_paginated(
                    url, user, base_params, {'page_size': 1},
                ).data['count']

                first = self._request_paginated(
                    url, user, base_params, {'page': 1, 'page_size': 1},
                )
                self.assertEqual(first.status_code, status.HTTP_200_OK)
                self.assertEqual(first.data['page'], 1)
                self.assertEqual(len(first.data['results']), 1)
                self.assertIsNone(first.data['previous'])
                if total > 1:
                    self.assertIsNotNone(first.data['next'])

                if total >= 2:
                    # With page_size=1, page 2 is the last page when total == 2.
                    middle_page = 2
                    middle = self._request_paginated(
                        url, user, base_params,
                        {'page': middle_page, 'page_size': 1},
                    )
                    self.assertEqual(middle.status_code, status.HTTP_200_OK)
                    self.assertEqual(middle.data['page'], middle_page)
                    self.assertEqual(len(middle.data['results']), 1)
                    self.assertIsNotNone(middle.data['previous'])

                last = self._request_paginated(
                    url, user, base_params, {'page': total, 'page_size': 1},
                )
                self.assertEqual(last.status_code, status.HTTP_200_OK)
                self.assertEqual(last.data['page'], total)
                self.assertEqual(len(last.data['results']), 1)
                self.assertIsNone(last.data['next'])
                if total > 1:
                    self.assertIsNotNone(last.data['previous'])

                beyond = self._request_paginated(
                    url, user, base_params,
                    {'page': total + 10, 'page_size': 1},
                )
                self.assertEqual(beyond.status_code, status.HTTP_200_OK)
                self.assertEqual(beyond.data['page'], total + 10)
                self.assertEqual(beyond.data['results'], [])
                self.assertEqual(beyond.data['count'], total)
                self.assertIsNone(beyond.data['next'])
                # API-05: for page > total_pages, previous points to the last
                # available page (documented previous page semantics).
                self.assertIsNotNone(beyond.data['previous'])
                self.assertIn(f'page={total}', beyond.data['previous'])

    def test_every_paginated_endpoint_has_stable_non_overlapping_pages(self):
        for label, url, user, base_params in self._paginated_endpoints():
            with self.subTest(endpoint=label):
                self.client.force_authenticate(user or None)
                page_one = self.client.get(url, {**base_params, 'page': 1, 'page_size': 1})
                page_two = self.client.get(url, {**base_params, 'page': 2, 'page_size': 1})
                self.assertEqual(page_one.status_code, status.HTTP_200_OK)
                self.assertEqual(page_two.status_code, status.HTTP_200_OK)
                if page_one.data['count'] < 2:
                    self.assertEqual(page_two.data['results'], [])
                    continue
                self.assertEqual(len(page_one.data['results']), 1)
                self.assertEqual(len(page_two.data['results']), 1)
                key = self._identity_key(label)
                first_ids = {item[key] for item in page_one.data['results']}
                second_ids = {item[key] for item in page_two.data['results']}
                self.assertFalse(first_ids & second_ids)
                # Re-requesting the first page yields the identical item.
                page_one_repeat = self.client.get(url, {**base_params, 'page': 1, 'page_size': 1})
                self.assertEqual(
                    [item[key] for item in page_one_repeat.data['results']],
                    list(first_ids),
                )

    def test_deterministic_ordering_keeps_page_boundaries_stable(self):
        self.client.force_authenticate(self.user)
        url = reverse('orders:order-list')
        page_one = self.client.get(url, {'page': 1, 'page_size': 2})
        page_two = self.client.get(url, {'page': 2, 'page_size': 2})
        first_ids = [item['id'] for item in page_one.data['results']]
        second_ids = [item['id'] for item in page_two.data['results']]
        self.assertFalse(set(first_ids) & set(second_ids))
        # Re-requesting the same page yields the same exact identifiers.
        page_one_repeat = self.client.get(url, {'page': 1, 'page_size': 2})
        self.assertEqual(
            [item['id'] for item in page_one_repeat.data['results']],
            first_ids,
        )

    # ------------------------------------------------------------------
    # Notifications representation
    # ------------------------------------------------------------------

    def test_notifications_and_unread_use_same_representation(self):
        self.client.force_authenticate(self.user)
        all_url = reverse('notifications:notification-list')
        unread_url = reverse('notifications:notification-unread')

        all_resp = self.client.get(all_url, {'page_size': 10})
        unread_resp = self.client.get(unread_url, {'page_size': 10})

        self.assertEqual(all_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(unread_resp.status_code, status.HTTP_200_OK)
        assert_canonical_shape(self, all_resp.data)
        assert_canonical_shape(self, unread_resp.data)

        all_item_fields = set(all_resp.data['results'][0].keys())
        unread_item_fields = set(unread_resp.data['results'][0].keys())
        self.assertEqual(all_item_fields, unread_item_fields)
        # Both use the full NotificationSerializer representation.
        self.assertIn('body', all_item_fields)
        self.assertIn('read_at', all_item_fields)

    # ------------------------------------------------------------------
    # Reviews shape consistency
    # ------------------------------------------------------------------

    def test_reviews_helpful_and_default_share_canonical_shape(self):
        product_url = reverse('reviews:review-list')
        default = self.client.get(
            product_url,
            {'product_id': str(self.product.uuid), 'page': 1, 'page_size': 1},
        )
        helpful = self.client.get(
            product_url,
            {'product_id': str(self.product.uuid), 'ordering': 'helpful', 'page': 1, 'page_size': 1},
        )
        self.assertEqual(default.status_code, status.HTTP_200_OK)
        self.assertEqual(helpful.status_code, status.HTTP_200_OK)
        assert_canonical_shape(self, default.data)
        assert_canonical_shape(self, helpful.data)
        self.assertTrue(default.data['count'] >= 3)
        self.assertTrue(helpful.data['count'] >= 3)
