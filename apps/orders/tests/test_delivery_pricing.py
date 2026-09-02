# ────────────────────────────────────────────────────────────────────────
# apps/orders/tests/test_delivery_pricing.py
#
# F-08 / PROD-006 — цена доставки server-authoritative.
#
# Проверяет границу доверия checkout:
#   • OrderService.create_from_cart() больше НЕ принимает delivery_cost —
#     подставить денежную сумму доставки из кода вызывающего нельзя;
#   • авторитетная цена доставки вычисляется на сервере из доменных данных
#     (адрес заказа → ShippingZone → ShippingMethod → calculate_cost());
#   • поддельная сумма доставки в теле POST /api/v1/orders/ (меньше,
#     больше, ноль, отрицательная) отклоняется и не влияет на заказ;
#   • обычный checkout без клиентской цены доставки сохраняет корректный
#     subtotal / delivery_cost / total;
#   • координация заказ ↔ склад (PROD-003) не деградирует.
# ────────────────────────────────────────────────────────────────────────

from decimal import Decimal
from urllib.parse import urlencode

from django.http import QueryDict
from django.test import TestCase
from django.urls import reverse

from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from apps.cart.models import Cart, CartItem
from apps.catalog.constants import ProductStatus
from apps.catalog.models import Brand, Category, Product, ProductVariant
from apps.inventory.models import Stock
from apps.inventory.services.inventory_service import InventoryService
from apps.orders.models import Order
from apps.orders.models.order import OrderStatus
from apps.orders.serializers import CreateOrderInputSerializer
from apps.orders.services.order_service import OrderService
from apps.orders.tests.factories import create_test_address, create_test_user
from apps.pricing.models import Price
from apps.shipping.tests.factories import create_test_method, create_test_zone


class DeliveryPricingTestCase(TestCase):
    """Общая инфраструктура: пользователь, адрес, товар, корзина, тарифы."""

    # subtotal заказа: 1000.00 × 2 + 500.00 × 1 = 2500.00
    UNIT_PRICE_A = Decimal('1000.00')
    UNIT_PRICE_B = Decimal('500.00')
    SUBTOTAL = Decimal('2500.00')

    def setUp(self):
        self.user = create_test_user()
        # Город «Москва» — по нему определяется зона доставки.
        self.address = create_test_address(self.user, city='Москва')

        brand = Brand.objects.create(name='DeliveryBrand')
        category = Category.add_root(name='DeliveryCat')
        product = Product.objects.create(
            name='Delivery Product',
            brand=brand,
            primary_category=category,
            status=ProductStatus.ACTIVE,
        )
        self.variant_a = ProductVariant.objects.create(
            product=product,
            sku='DELIVERY-SKU-A',
        )
        self.variant_b = ProductVariant.objects.create(
            product=product,
            sku='DELIVERY-SKU-B',
        )
        Price.objects.create(variant=self.variant_a, price=self.UNIT_PRICE_A)
        Price.objects.create(variant=self.variant_b, price=self.UNIT_PRICE_B)

    def _make_cart(self) -> Cart:
        """Активная корзина с двумя позициями (subtotal = 2500.00)."""
        cart = Cart.objects.create(user=self.user, is_active=True)
        CartItem.objects.create(cart=cart, variant=self.variant_a, quantity=2)
        CartItem.objects.create(cart=cart, variant=self.variant_b, quantity=1)
        return cart

    def _make_zone_with_method(self, **method_kwargs):
        """Зона «Москва и МО» + активный способ доставки."""
        zone = create_test_zone(
            name='Москва и МО',
            zone_code='msk',
            regions=['Москва', 'Московская область'],
        )
        method = create_test_method(zone=zone, **method_kwargs)
        return zone, method


# ================================================================
# 1. Сервис: авторитетный расчёт цены доставки
# ================================================================

class ServerSideDeliveryCostTests(DeliveryPricingTestCase):
    """AC-2: цена доставки берётся из доменных данных, а не из запроса."""

    def test_delivery_cost_calculated_from_shipping_method(self):
        """Тариф зоны определяет delivery_cost и total заказа."""
        self._make_zone_with_method(
            base_price=Decimal('300.00'),
            price_per_kg=Decimal('0.000'),
            free_shipping_threshold=None,
        )

        order = OrderService.create_from_cart(
            user=self.user,
            cart=self._make_cart(),
        )

        self.assertEqual(order.subtotal, self.SUBTOTAL)
        self.assertEqual(order.delivery_cost, Decimal('300.00'))
        self.assertEqual(order.total, Decimal('2800.00'))
        order.refresh_from_db()
        self.assertEqual(order.delivery_cost, Decimal('300.00'))
        self.assertEqual(order.total, Decimal('2800.00'))

    def test_delivery_cost_zero_when_no_tariff_configured(self):
        """Тарифы не настроены → доставка 0.00 (прежнее поведение checkout)."""
        order = OrderService.create_from_cart(
            user=self.user,
            cart=self._make_cart(),
        )

        self.assertEqual(order.delivery_cost, Decimal('0.00'))
        self.assertEqual(order.total, self.SUBTOTAL)

    def test_free_shipping_threshold_is_applied(self):
        """Порог бесплатной доставки — доменное правило сервера."""
        self._make_zone_with_method(
            base_price=Decimal('300.00'),
            price_per_kg=Decimal('0.000'),
            free_shipping_threshold=Decimal('2000.00'),
        )

        order = OrderService.create_from_cart(
            user=self.user,
            cart=self._make_cart(),
        )

        # subtotal 2500.00 ≥ порога 2000.00 → доставка бесплатная.
        self.assertEqual(order.delivery_cost, Decimal('0.00'))
        self.assertEqual(order.total, self.SUBTOTAL)

    def test_weight_based_tariff_uses_catalog_weight(self):
        """Вес берётся из каталога: cost = base_price + price_per_kg × вес."""
        self.variant_a.weight = Decimal('2.00')
        self.variant_a.save(update_fields=['weight', 'updated_at'])
        self.variant_b.weight = Decimal('1.00')
        self.variant_b.save(update_fields=['weight', 'updated_at'])
        self._make_zone_with_method(
            base_price=Decimal('300.00'),
            price_per_kg=Decimal('50.000'),
            free_shipping_threshold=None,
        )

        order = OrderService.create_from_cart(
            user=self.user,
            cart=self._make_cart(),
        )

        # вес = 2.00 × 2 + 1.00 × 1 = 5.00 кг → 300 + 50 × 5 = 550.00
        self.assertEqual(order.delivery_cost, Decimal('550.00'))
        self.assertEqual(order.total, Decimal('3050.00'))

    def test_delivery_cost_uses_region_of_address(self):
        """Зона ищется по региону адреса, когда город в зону не входит."""
        zone = create_test_zone(
            name='Центр',
            zone_code='central',
            regions=['Тульская область'],
        )
        create_test_method(
            zone=zone,
            base_price=Decimal('250.00'),
            price_per_kg=Decimal('0.000'),
            free_shipping_threshold=None,
        )
        self.address.region = 'Тульская область'
        self.address.city = 'Тула'
        self.address.save(update_fields=['region', 'city', 'updated_at'])

        order = OrderService.create_from_cart(
            user=self.user,
            cart=self._make_cart(),
        )

        self.assertEqual(order.region, 'Тульская область')
        self.assertEqual(order.delivery_cost, Decimal('250.00'))


class DeliveryCostInjectionImpossibleTests(DeliveryPricingTestCase):
    """AC-1: сервис физически не принимает клиентскую сумму доставки."""

    def test_create_from_cart_rejects_delivery_cost_argument(self):
        """delivery_cost больше не параметр create_from_cart() → TypeError."""
        self._make_zone_with_method(
            base_price=Decimal('300.00'),
            price_per_kg=Decimal('0.000'),
            free_shipping_threshold=None,
        )

        with self.assertRaises(TypeError):
            OrderService.create_from_cart(
                user=self.user,
                cart=self._make_cart(),
                delivery_cost=Decimal('1.00'),
            )

        # Заказ не создан — подделка не «проглатывается» молча.
        self.assertFalse(Order.objects.filter(user=self.user).exists())

    def test_forged_lower_amount_cannot_change_server_amount(self):
        """Попытка занизить доставку не меняет серверное значение."""
        self._make_zone_with_method(
            base_price=Decimal('300.00'),
            price_per_kg=Decimal('0.000'),
            free_shipping_threshold=None,
        )
        cart = self._make_cart()

        with self.assertRaises(TypeError):
            OrderService.create_from_cart(
                user=self.user,
                cart=cart,
                delivery_cost=Decimal('0.01'),
            )

        order = OrderService.create_from_cart(
            user=self.user,
            cart=cart,
        )
        self.assertEqual(order.delivery_cost, Decimal('300.00'))
        self.assertEqual(order.total, Decimal('2800.00'))

    def test_forged_higher_amount_cannot_change_server_amount(self):
        """Попытка завысить доставку не меняет серверное значение."""
        self._make_zone_with_method(
            base_price=Decimal('300.00'),
            price_per_kg=Decimal('0.000'),
            free_shipping_threshold=None,
        )
        cart = self._make_cart()

        with self.assertRaises(TypeError):
            OrderService.create_from_cart(
                user=self.user,
                cart=cart,
                delivery_cost=Decimal('99999.00'),
            )

        order = OrderService.create_from_cart(
            user=self.user,
            cart=cart,
        )
        self.assertEqual(order.delivery_cost, Decimal('300.00'))
        self.assertEqual(order.total, Decimal('2800.00'))


# ================================================================
# 2. Сериализатор: явный контракт checkout
# ================================================================

class CreateOrderInputSerializerTests(TestCase):
    """AC-6: delivery_cost — не поддерживаемое поле запроса."""

    def test_notes_only_payload_is_valid(self):
        serializer = CreateOrderInputSerializer(
            data={'notes': 'Позвонить перед доставкой'},
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertNotIn('delivery_cost', serializer.validated_data)

    def test_empty_payload_is_valid(self):
        serializer = CreateOrderInputSerializer(data={})
        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_explicit_delivery_cost_is_rejected(self):
        serializer = CreateOrderInputSerializer(data={'delivery_cost': '1.00'})
        self.assertFalse(serializer.is_valid())
        self.assertIn('delivery_cost', serializer.errors)

    def test_explicit_delivery_cost_in_querydict_is_rejected(self):
        """QueryDict — то, что отдают FormParser и MultiPartParser."""
        serializer = CreateOrderInputSerializer(
            data=QueryDict('delivery_cost=0.00'),
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn('delivery_cost', serializer.errors)

    def test_querydict_with_notes_only_is_valid(self):
        """Контроль: сам QueryDict-вход валиден, отклоняется только поле."""
        serializer = CreateOrderInputSerializer(
            data=QueryDict('notes=Позвонить+перед+доставкой'),
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(
            serializer.validated_data['notes'],
            'Позвонить перед доставкой',
        )

    def test_non_mapping_payload_never_reaches_order_creation(self):
        """Не-mapping тело отклоняется самим DRF ещё до validate()."""
        serializer = CreateOrderInputSerializer(
            data=[{'delivery_cost': '0.00'}],
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn('non_field_errors', serializer.errors)


# ================================================================
# 3. API: подделка цены доставки через запрос
# ================================================================

class CheckoutDeliveryCostAPITests(DeliveryPricingTestCase):
    """AC-1 / AC-3 / AC-4 / AC-6 на уровне HTTP-контракта."""

    def setUp(self):
        super().setUp()
        self._make_zone_with_method(
            base_price=Decimal('300.00'),
            price_per_kg=Decimal('0.000'),
            free_shipping_threshold=None,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.url = reverse('orders:order-list')

    def test_checkout_without_delivery_cost_uses_server_amount(self):
        """Обычный checkout → серверная цена доставки в заказе и в ответе."""
        self._make_cart()

        response = self.client.post(self.url, {'notes': 'К двери'}, format='json')

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['subtotal'], '2500.00')
        self.assertEqual(response.data['delivery_cost'], '300.00')
        self.assertEqual(response.data['total'], '2800.00')

        order = Order.objects.get(pk=response.data['id'])
        self.assertEqual(order.delivery_cost, Decimal('300.00'))
        self.assertEqual(order.total, Decimal('2800.00'))

    def test_forged_lower_delivery_cost_is_rejected(self):
        """Заниженная доставка → 400, заказ не создаётся."""
        self._make_cart()

        response = self.client.post(
            self.url,
            {'delivery_cost': '1.00'},
            format='json',
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('delivery_cost', response.data)
        self.assertFalse(Order.objects.filter(user=self.user).exists())

    def test_forged_higher_delivery_cost_is_rejected(self):
        """Завышенная доставка → 400, заказ не создаётся."""
        self._make_cart()

        response = self.client.post(
            self.url,
            {'delivery_cost': '99999.00'},
            format='json',
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('delivery_cost', response.data)
        self.assertFalse(Order.objects.filter(user=self.user).exists())

    def test_forged_zero_delivery_cost_is_rejected(self):
        """Даже «правдоподобный» ноль отклоняется: поле не поддерживается."""
        self._make_cart()

        response = self.client.post(
            self.url,
            {'delivery_cost': '0.00'},
            format='json',
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(Order.objects.filter(user=self.user).exists())

    def test_negative_delivery_cost_is_rejected(self):
        """Отрицательная доставка → 400."""
        self._make_cart()

        response = self.client.post(
            self.url,
            {'delivery_cost': '-50.00'},
            format='json',
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(Order.objects.filter(user=self.user).exists())

    def test_forged_delivery_cost_in_multipart_is_rejected(self):
        """multipart/form-data (дефолт DRF APIClient) → 400."""
        self._make_cart()

        response = self.client.post(self.url, {'delivery_cost': '1.00'})

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('delivery_cost', response.data)
        self.assertFalse(Order.objects.filter(user=self.user).exists())

    # ── application/x-www-form-urlencoded (FormParser → QueryDict) ──

    def _post_urlencoded(self, payload: dict):
        """Настоящий form-encoded запрос: urlencoded-тело + content-type.

        Именно этот путь разбирает FormParser, отдавая view ``QueryDict``,
        а не ``multipart/form-data``, который DRF подставляет по умолчанию.
        """
        return self.client.post(
            self.url,
            data=urlencode(payload),
            content_type='application/x-www-form-urlencoded',
        )

    def test_forged_zero_delivery_cost_in_urlencoded_form_is_rejected(self):
        """form-encoded delivery_cost=0.00 → 400, заказ не создаётся."""
        self._make_cart()

        response = self._post_urlencoded({'delivery_cost': '0.00'})

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('delivery_cost', response.data)
        self.assertFalse(Order.objects.filter(user=self.user).exists())

    def test_forged_lower_delivery_cost_in_urlencoded_form_is_rejected(self):
        """form-encoded delivery_cost=1.00 → 400, заказ не создаётся."""
        self._make_cart()

        response = self._post_urlencoded({'delivery_cost': '1.00'})

        self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(Order.objects.filter(user=self.user).exists())

    def test_forged_higher_delivery_cost_in_urlencoded_form_is_rejected(self):
        """form-encoded delivery_cost=99999.00 → 400, заказ не создаётся."""
        self._make_cart()

        response = self._post_urlencoded({'delivery_cost': '99999.00'})

        self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(Order.objects.filter(user=self.user).exists())

    def test_urlencoded_form_without_delivery_cost_creates_order(self):
        """Контроль: form-encoded путь рабочий — 400 даёт именно проверка.

        Без этого теста нельзя отличить «поле отклонено» от «запрос
        вообще не дошёл до валидации».
        """
        self._make_cart()

        response = self._post_urlencoded({'notes': 'К двери'})

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['delivery_cost'], '300.00')
        self.assertEqual(response.data['total'], '2800.00')
        self.assertFalse(Order.objects.exclude(user=self.user).exists())


# ================================================================
# 4. PROD-003: координация заказ ↔ склад не деградирует
# ================================================================

class DeliveryPricingCoordinationTests(DeliveryPricingTestCase):
    """AC-5: fail-safe координация PROD-003 сохраняется."""

    def test_confirm_reserves_and_cancel_releases_stock(self):
        InventoryService.restock(self.variant_a, 10)
        InventoryService.restock(self.variant_b, 10)
        self._make_zone_with_method(
            base_price=Decimal('300.00'),
            price_per_kg=Decimal('0.000'),
            free_shipping_threshold=None,
        )

        order = OrderService.create_from_cart(
            user=self.user,
            cart=self._make_cart(),
        )
        self.assertEqual(order.delivery_cost, Decimal('300.00'))

        OrderService.transition_status(order, OrderStatus.CONFIRMED)
        stock_a = Stock.objects.get(variant=self.variant_a)
        stock_b = Stock.objects.get(variant=self.variant_b)
        self.assertEqual(stock_a.reserved_quantity, 2)
        self.assertEqual(stock_b.reserved_quantity, 1)

        OrderService.cancel(order, reason='changed_mind')
        stock_a.refresh_from_db()
        stock_b.refresh_from_db()
        self.assertEqual(stock_a.reserved_quantity, 0)
        self.assertEqual(stock_b.reserved_quantity, 0)

        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.CANCELLED)
        # Снимок цены доставки не пересчитывается задним числом.
        self.assertEqual(order.delivery_cost, Decimal('300.00'))

    def test_insufficient_stock_still_aborts_confirm(self):
        """Провал резервирования по-прежнему откатывает CONFIRMED."""
        # variant_a нужен в количестве 2, на складе только 1 → провал reserve.
        InventoryService.restock(self.variant_a, 1)
        InventoryService.restock(self.variant_b, 1)
        self._make_zone_with_method(
            base_price=Decimal('300.00'),
            price_per_kg=Decimal('0.000'),
            free_shipping_threshold=None,
        )

        order = OrderService.create_from_cart(
            user=self.user,
            cart=self._make_cart(),
        )

        with self.assertRaises(ValidationError):
            OrderService.transition_status(order, OrderStatus.CONFIRMED)

        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.PENDING)
        self.assertEqual(order.delivery_cost, Decimal('300.00'))
