"""
Тесты API endpoints ценообразования.
"""
from decimal import Decimal

from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from apps.pricing.models import Price
from apps.pricing.tests.factories import PricingTestCase


class PriceAPITestCase(PricingTestCase):

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.staff)


class PriceGetTests(PriceAPITestCase):

    def test_get_price_not_set(self):
        resp = self.client.get(
            f'/api/v1/pricing/variants/{self.variant_a.pk}/price/',
        )
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_get_price_set(self):
        from apps.pricing.services.pricing_service import PricingService
        PricingService.set_price(self.variant_a, Decimal('100.00'))

        resp = self.client.get(
            f'/api/v1/pricing/variants/{self.variant_a.pk}/price/',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['price'], '100.00')

    def test_get_price_nonexistent_variant(self):
        resp = self.client.get('/api/v1/pricing/variants/99999/price/')
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)


class PriceSetTests(PriceAPITestCase):

    def test_set_price(self):
        resp = self.client.post(
            f'/api/v1/pricing/variants/{self.variant_a.pk}/price/',
            {'price': '150.00'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['price'], '150.00')

    def test_set_price_with_sale(self):
        resp = self.client.post(
            f'/api/v1/pricing/variants/{self.variant_a.pk}/price/',
            {'price': '100.00', 'sale_price': '80.00'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['sale_price'], '80.00')
        self.assertEqual(resp.data['effective_price'], '80.00')
        self.assertEqual(resp.data['discount_percent'], 20)

    def test_set_price_zero_rejected(self):
        resp = self.client.post(
            f'/api/v1/pricing/variants/{self.variant_a.pk}/price/',
            {'price': '0.00'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_set_price_unauthenticated(self):
        self.client.logout()
        resp = self.client.post(
            f'/api/v1/pricing/variants/{self.variant_a.pk}/price/',
            {'price': '100.00'},
            format='json',
        )
        self.assertIn(resp.status_code, [401, 403])

    def test_set_price_updates_product(self):
        """После установки цены Product.min_price/max_price обновляются."""
        self.client.post(
            f'/api/v1/pricing/variants/{self.variant_a.pk}/price/',
            {'price': '100.00'},
            format='json',
        )
        self.product.refresh_from_db()
        self.assertEqual(self.product.min_price, Decimal('100.00'))


class PriceHistoryAPITests(PriceAPITestCase):

    def test_history_empty(self):
        resp = self.client.get(
            f'/api/v1/pricing/variants/{self.variant_a.pk}/history/',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['count'], 0)
        self.assertEqual(resp.data['results'], [])
        self.assertEqual(resp.data['total_pages'], 0)

    def test_history_has_entries(self):
        from apps.pricing.services.pricing_service import PricingService
        PricingService.set_price(self.variant_a, Decimal('100.00'))
        PricingService.set_price(self.variant_a, Decimal('90.00'))

        resp = self.client.get(
            f'/api/v1/pricing/variants/{self.variant_a.pk}/history/',
        )
        self.assertEqual(resp.data['count'], 1)
        self.assertEqual(len(resp.data['results']), 1)
        self.assertEqual(resp.data['results'][0]['old_price'], '100.00')
        self.assertEqual(resp.data['results'][0]['new_price'], '90.00')


class BulkPriceAPITests(PriceAPITestCase):

    def test_bulk_set_prices(self):
        resp = self.client.post('/api/v1/pricing/prices/bulk/', {
            'prices': [
                {'variant_id': self.variant_a.pk, 'price': '100.00'},
                {'variant_id': self.variant_b.pk, 'price': '200.00'},
            ],
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data), 2)

    def test_bulk_invalid_variant(self):
        resp = self.client.post('/api/v1/pricing/prices/bulk/', {
            'prices': [
                {'variant_id': 99999, 'price': '100.00'},
            ],
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)
