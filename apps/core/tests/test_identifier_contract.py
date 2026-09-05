"""API-01 / F-8 (issue #73) — frozen cross-context identifier contract.

Контракт, зафиксированный в ``docs/api/API_CONTRACT.md`` §7:

* **Order** — публичный идентификатор ``order_number`` (``ORD-000001``)
  и в путях, и в телах запросов; целочисленный PK — внутренний.
* **Shipment** — публичный идентификатор ``shipment_number``
  (``SHP-00000001``), отдельное иммутабельное поле. ``internal_tracking``
  — внутреннее поле и наружу не отдаётся; ``tracking_number`` — ВНЕШНИЙ
  трек службы доставки. Целочисленный PK публичным адресом не является.
* **Payment** — идентичность ``payment_number`` (``PAY-000001``); ссылка на
  заказ — ``order_number``.
* **Product** — публичный идентификатор UUID; каталог отдаёт его как ``id``
  (нормативно) и дублирует явным полем ``uuid``. Отзывы ссылаются полем
  ``product_id`` типа UUID (конкурирующего ``product_uuid`` нет).
* Устаревшая целочисленная ссылка ``order_id`` принимается на окно
  совместимости; одновременная передача с ``order_number`` → 400.

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
    MAX_BIGINT_PK,
    is_order_number,
    is_shipment_number,
    order_reference_filters,
    parse_legacy_pk,
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

    def test_public_number_patterns_are_ascii_only(self):
        """``\\d`` в Python-регексе матчит не-ASCII цифры — паттерны на [0-9].

        Без этого ``ORD-٠٠٠٠٠١`` считался бы валидным номером заказа и уходил
        в БД как «канонический» идентификатор, которого там быть не может.
        """
        self.assertFalse(is_order_number('ORD-٠٠٠٠٠١'))
        self.assertFalse(is_shipment_number('SHP-٠٠٠٠٠٠٠١'))
        self.assertTrue(is_order_number('ORD-000001'))
        self.assertTrue(is_shipment_number('SHP-00000001'))

    def test_parse_legacy_pk_accepts_ascii_digits(self):
        self.assertEqual(parse_legacy_pk('42'), 42)
        self.assertEqual(parse_legacy_pk(42), 42)
        self.assertEqual(parse_legacy_pk(str(MAX_BIGINT_PK)), MAX_BIGINT_PK)

    def test_parse_legacy_pk_rejects_non_ascii_digits(self):
        """``str.isdigit()`` пропускает не-ASCII цифры — ``parse_legacy_pk`` нет.

        ``'٤٢'`` (арабо-индийские цифры) не должно быть вторым способом
        адресовать PK 42, а ``'²'`` роняло ``int()`` с ``ValueError``.
        """
        self.assertTrue('٤٢'.isdigit())
        self.assertTrue('²'.isdigit())
        self.assertIsNone(parse_legacy_pk('٤٢'))
        self.assertIsNone(parse_legacy_pk('²'))

    def test_parse_legacy_pk_rejects_out_of_range_and_garbage(self):
        self.assertIsNone(parse_legacy_pk(MAX_BIGINT_PK + 1))
        self.assertIsNone(parse_legacy_pk(str(MAX_BIGINT_PK + 1)))
        self.assertIsNone(parse_legacy_pk('0'))
        self.assertIsNone(parse_legacy_pk(0))
        self.assertIsNone(parse_legacy_pk('-1'))
        self.assertIsNone(parse_legacy_pk(-1))
        self.assertIsNone(parse_legacy_pk('4.2'))
        self.assertIsNone(parse_legacy_pk(''))
        self.assertIsNone(parse_legacy_pk(None))
        self.assertIsNone(parse_legacy_pk('not-a-pk'))

    def test_parse_legacy_pk_rejects_bool(self):
        """``True`` — это ``int`` в Python; PK из него получаться не должен."""
        self.assertIsNone(parse_legacy_pk(True))
        self.assertIsNone(parse_legacy_pk(False))

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


class ShipmentIdentifierContractTests(TestCase):
    """Shipment: shipment_number — единственный публичный идентификатор."""

    def setUp(self):
        from apps.shipping.tests.factories import create_test_shipment

        self.client = APIClient()
        self.user = create_test_user()
        self.order = create_test_order(self.user, status='confirmed')
        self.shipment = create_test_shipment(self.order)
        self.client.force_authenticate(self.user)

    def test_shipment_number_format_and_immutability(self):
        """Номер соответствует SHP-формату и не меняется при save()."""
        self.assertTrue(is_shipment_number(self.shipment.shipment_number))
        original = self.shipment.shipment_number

        self.shipment.tracking_number = 'EXT-999'
        self.shipment.save()
        self.shipment.refresh_from_db()

        # Смена внешнего трека не влияет на публичный идентификатор.
        self.assertEqual(self.shipment.shipment_number, original)

    def test_shipment_number_differs_from_other_identifiers(self):
        """Три идентификатора — три разных роли, не синонимы."""
        self.assertNotEqual(
            self.shipment.shipment_number, self.shipment.tracking_number,
        )
        self.assertIsNotNone(self.shipment.shipment_number)

    def test_list_exposes_shipment_number_and_hides_internal_tracking(self):
        response = self.client.get(reverse('shipping:shipment-list'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        item = response.data['results'][0]
        self.assertEqual(item['shipment_number'], self.shipment.shipment_number)
        self.assertNotIn('internal_tracking', item)
        # Целочисленный PK наружу не утекает.
        self.assertNotIn('id', item)

    def test_detail_addressed_by_shipment_number(self):
        url = reverse(
            'shipping:shipment-detail',
            kwargs={'shipment_number': self.shipment.shipment_number},
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertNotIn('internal_tracking', response.data)

    def test_shipment_numbers_are_unique_across_shipments(self):
        from apps.shipping.tests.factories import create_test_shipment

        second_order = create_test_order(self.user, status='confirmed')
        # Переиспользуем метод первого отправления: фабрика зоны создала бы
        # дубликат zone_code и тест упал бы не по причине контракта.
        second = create_test_shipment(second_order, method=self.shipment.method)
        self.assertNotEqual(
            self.shipment.shipment_number, second.shipment_number,
        )


class PaymentIdentifierContractTests(TestCase):
    """Payment: payment_number — идентичность, order_number — ссылка."""

    def setUp(self):
        from apps.payments.tests.factories import create_test_payment

        self.client = APIClient()
        self.user = create_test_user()
        self.order = create_test_order(self.user)
        self.payment = create_test_payment(self.order, self.user)
        self.client.force_authenticate(self.user)

    def test_payment_number_and_order_number_are_distinct_roles(self):
        """PAY-номер — идентичность платежа, ORD-номер — ссылка на заказ."""
        self.assertTrue(self.payment.payment_number.startswith('PAY-'))
        self.assertTrue(self.order.order_number.startswith('ORD-'))
        self.assertNotEqual(
            self.payment.payment_number, self.order.order_number,
        )

    def test_detail_payload_separates_both_identifiers(self):
        url = reverse(
            'payments:payment-detail',
            kwargs={'payment_number': self.payment.payment_number},
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Регрессия: раньше под ключом order_number ехал PAY-номер.
        self.assertEqual(
            response.data['payment_number'], self.payment.payment_number,
        )
        self.assertEqual(
            response.data['order_number'], self.order.order_number,
        )
