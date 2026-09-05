"""API-01 / F-8 (issue #73) — frozen cross-context identifier contract.

Контракт, зафиксированный в ``docs/api/API_CONTRACT.md`` §7:

* **Order** — публичный идентификатор ``order_number`` (``ORD-000001``)
  и в путях, и в телах запросов; целочисленный PK — внутренний.
* **Shipment** — публичный идентификатор ``internal_tracking``
  (``SHP-00000001``); сырой PK больше не является каноническим путём.
* **Product** — публичный идентификатор UUID; каталог отдаёт его как ``id``
  (нормативно) и дублирует явным полем ``uuid``; отзывы ссылаются по UUID.
* Устаревшие целочисленные ссылки (``order_id``, ``product_id``, числовой
  сегмент пути отправления) принимаются на окно совместимости.

Тесты здесь проверяют кросс-контекстные инварианты; поэндпоинтные проверки
живут в тестах соответствующих приложений.
"""

from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from rest_framework import status
from rest_framework.test import APIClient

from apps.catalog.tests.factories import CatalogTestCase
from apps.core.identifiers import (
    is_order_number,
    is_shipment_number,
    order_reference_filters,
    parse_uuid,
)
from apps.orders.tests.factories import create_test_order, create_test_user


class IdentifierHelpersTests(TestCase):
    """Примитивы apps.core.identifiers."""

    def test_is_order_number(self):
        self.assertTrue(is_order_number('ORD-000001'))
        self.assertTrue(is_order_number('ORD-1234567'))
        self.assertFalse(is_order_number('ord-000001'))
        self.assertFalse(is_order_number('SHP-00000001'))
        self.assertFalse(is_order_number('1'))
        self.assertFalse(is_order_number(''))
        self.assertFalse(is_order_number(None))

    def test_is_shipment_number(self):
        self.assertTrue(is_shipment_number('SHP-00000001'))
        self.assertFalse(is_shipment_number('ORD-000001'))
        self.assertFalse(is_shipment_number('42'))
        self.assertFalse(is_shipment_number(None))

    def test_parse_uuid(self):
        value = '00000000-0000-4000-8000-000000000000'
        self.assertEqual(str(parse_uuid(value)), value)
        self.assertIsNone(parse_uuid('not-a-uuid'))
        self.assertIsNone(parse_uuid(None))
        self.assertIsNone(parse_uuid(7))

    def test_order_reference_filters(self):
        self.assertEqual(
            order_reference_filters({'order_number': 'ORD-000001'}),
            {'order_number': 'ORD-000001'},
        )
        self.assertEqual(order_reference_filters({'order_id': 7}), {'pk': 7})


class OrderIdentifierContractTests(TestCase):
    """Order: публичный идентификатор — order_number, и в пути, и в теле."""

    def setUp(self):
        self.user = create_test_user()
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.order = create_test_order(
            self.user,
            subtotal=Decimal('1000.00'),
            total=Decimal('1000.00'),
        )

    def test_order_number_format(self):
        self.assertTrue(is_order_number(self.order.order_number))

    def test_order_detail_path_uses_order_number(self):
        url = reverse(
            'orders:order-detail',
            kwargs={'order_number': self.order.order_number},
        )
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['order_number'], self.order.order_number)

    def test_cross_context_endpoints_accept_order_number(self):
        """Все кросс-контекстные ссылки на заказ принимают order_number."""
        payments_url = reverse('payments:payment-list')
        resp = self.client.post(
            payments_url,
            {'order_number': self.order.order_number, 'amount': '1000.00'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)

    def test_cross_context_endpoints_reject_malformed_order_number(self):
        """Мусорный order_number → 400 (валидация), а не 404/500."""
        payments_url = reverse('payments:payment-list')
        resp = self.client.post(
            payments_url, {'order_number': 'ORD_1'}, format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)


class ProductIdentifierContractTests(CatalogTestCase):
    """Product: публичный идентификатор — UUID."""

    def setUp(self):
        self.client = APIClient()

    def _listing_item(self):
        resp = self.client.get(reverse('catalog:product-list'))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        items = [
            item for item in resp.data['results']
            if item['id'] == str(self.product.uuid)
        ]
        self.assertEqual(len(items), 1)
        return items[0]

    def test_listing_id_is_uuid_and_uuid_field_matches(self):
        item = self._listing_item()
        self.assertEqual(item['id'], str(self.product.uuid))
        self.assertEqual(item['uuid'], str(self.product.uuid))
        self.assertIsNotNone(parse_uuid(item['id']))

    def test_detail_id_is_uuid(self):
        url = reverse(
            'catalog:product-detail',
            kwargs={'identifier': str(self.product.uuid)},
        )
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['id'], str(self.product.uuid))
        self.assertEqual(resp.data['uuid'], str(self.product.uuid))

    def test_integer_pk_never_leaks_as_product_id(self):
        item = self._listing_item()
        self.assertNotEqual(item['id'], self.product.pk)
        self.assertNotEqual(item['id'], str(self.product.pk))
