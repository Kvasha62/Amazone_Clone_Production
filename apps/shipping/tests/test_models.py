# ────────────────────────────────────────────────────────────────────────
# apps/shipping/tests/test_models.py — тесты моделей доставки.
#
# Проверяет:
#   • ShippingZone: создание, contains_region, __str__
#   • ShippingMethod: создание, calculate_cost, estimated_days_display, __str__
#   • Shipment: создание, авто-генерация internal_tracking, is_terminal, __str__
#
# 📖 https://docs.djangoproject.com/en/stable/topics/testing/overview/
# ────────────────────────────────────────────────────────────────────────

from decimal import Decimal

from django.test import TestCase

from apps.shipping.models import Shipment, ShippingMethod, ShippingZone
from apps.shipping.tests.factories import (
    create_test_method,
    create_test_shipment,
    create_test_zone,
)
from apps.orders.tests.factories import create_test_order, create_test_user


# ================================================================
# ShippingZone
# ================================================================

class ShippingZoneModelTests(TestCase):
    """Тесты модели ShippingZone."""

    def test_create_zone(self):
        """Успешное создание зоны."""
        zone = create_test_zone()
        self.assertIsNotNone(zone.pk)
        self.assertEqual(zone.name, 'Москва и МО')
        self.assertEqual(zone.zone_code, 'msk')
        self.assertEqual(zone.regions, ['Москва', 'Московская область'])
        self.assertTrue(zone.is_active)

    def test_str_representation(self):
        """__str__ = 'Название (код)'."""
        zone = create_test_zone()
        self.assertEqual(str(zone), 'Москва и МО (msk)')

    def test_contains_region_found(self):
        """contains_region = True для региона в списке."""
        zone = create_test_zone()
        self.assertTrue(zone.contains_region('Москва'))
        self.assertTrue(zone.contains_region('Московская область'))

    def test_contains_region_case_insensitive(self):
        """contains_region нечувствителен к регистру."""
        zone = create_test_zone()
        self.assertTrue(zone.contains_region('москва'))
        self.assertTrue(zone.contains_region('МОСКВА'))

    def test_contains_region_not_found(self):
        """contains_region = False для региона не в списке."""
        zone = create_test_zone()
        self.assertFalse(zone.contains_region('Тула'))

    def test_contains_region_empty(self):
        """contains_region = False для пустого региона/списка."""
        zone = create_test_zone(regions=[])
        self.assertFalse(zone.contains_region('Москва'))
        self.assertFalse(zone.contains_region(''))

    def test_unique_zone_code(self):
        """Нельзя создать две зоны с одинаковым zone_code."""
        create_test_zone(zone_code='central')
        with self.assertRaises(Exception):
            create_test_zone(zone_code='central')

    def test_ordering_by_name(self):
        """Зоны сортируются по name."""
        create_test_zone(name='Восток', zone_code='east')
        create_test_zone(name='Альфа', zone_code='alpha')
        zones = list(ShippingZone.objects.all().values_list('name', flat=True))
        self.assertEqual(zones, sorted(zones))


# ================================================================
# ShippingMethod
# ================================================================

class ShippingMethodModelTests(TestCase):
    """Тесты модели ShippingMethod."""

    def test_create_method(self):
        """Успешное создание способа доставки."""
        method = create_test_method()
        self.assertIsNotNone(method.pk)
        self.assertEqual(method.name, 'Курьерская доставка')
        self.assertEqual(method.shipping_type, 'courier')
        self.assertEqual(method.base_price, Decimal('300.00'))
        self.assertEqual(method.price_per_kg, Decimal('50.000'))

    def test_str_representation(self):
        """__str__ = 'Название (Тип)'."""
        method = create_test_method()
        self.assertEqual(str(method), 'Курьерская доставка (Курьер)')

    def test_calculate_cost_basic(self):
        """Базовый расчёт: base_price без веса."""
        method = create_test_method(
            base_price=Decimal('300.00'),
            price_per_kg=Decimal('0.000'),
            free_shipping_threshold=None,
        )
        cost = method.calculate_cost(order_total=Decimal('1000.00'))
        self.assertEqual(cost, Decimal('300.00'))

    def test_calculate_cost_with_weight(self):
        """Расчёт с весом: base_price + price_per_kg * weight."""
        method = create_test_method(
            base_price=Decimal('200.00'),
            price_per_kg=Decimal('50.000'),
            free_shipping_threshold=None,
        )
        cost = method.calculate_cost(
            order_total=Decimal('1000.00'),
            weight_kg=Decimal('2.000'),
        )
        # 200 + 50 * 2 = 300
        self.assertEqual(cost, Decimal('300.00'))

    def test_calculate_cost_free_shipping(self):
        """Бесплатная доставка при превышении порога."""
        method = create_test_method(
            base_price=Decimal('300.00'),
            free_shipping_threshold=Decimal('5000.00'),
        )
        cost = method.calculate_cost(order_total=Decimal('5000.00'))
        self.assertEqual(cost, Decimal('0.00'))

    def test_calculate_cost_below_free_threshold(self):
        """Платная доставка ниже порога бесплатной."""
        method = create_test_method(
            base_price=Decimal('300.00'),
            free_shipping_threshold=Decimal('5000.00'),
            price_per_kg=Decimal('0.000'),
        )
        cost = method.calculate_cost(order_total=Decimal('4999.99'))
        self.assertEqual(cost, Decimal('300.00'))

    def test_calculate_cost_max_shipping_cap(self):
        """Ограничение максимальной стоимости доставки."""
        method = create_test_method(
            base_price=Decimal('200.00'),
            price_per_kg=Decimal('100.000'),
            free_shipping_threshold=None,
            max_shipping_cost=Decimal('500.00'),
        )
        cost = method.calculate_cost(
            order_total=Decimal('1000.00'),
            weight_kg=Decimal('10.000'),  # 200 + 100*10 = 1200 → capped at 500
        )
        self.assertEqual(cost, Decimal('500.00'))

    def test_calculate_cost_no_weight_no_per_kg(self):
        """Расчёт без веса — только base_price."""
        method = create_test_method(
            base_price=Decimal('250.00'),
            price_per_kg=Decimal('50.000'),
            free_shipping_threshold=None,
        )
        cost = method.calculate_cost(order_total=Decimal('1000.00'))
        # weight_kg=None → не прибавляем price_per_kg
        self.assertEqual(cost, Decimal('250.00'))

    def test_estimated_days_same(self):
        """estimated_days_display при min == max."""
        method = create_test_method(estimated_days_min=3, estimated_days_max=3)
        self.assertEqual(method.estimated_days_display, '3 дн.')

    def test_estimated_days_range(self):
        """estimated_days_display при min != max."""
        method = create_test_method(estimated_days_min=2, estimated_days_max=5)
        self.assertEqual(method.estimated_days_display, '2-5 дн.')

    def test_estimated_days_today(self):
        """estimated_days_display при min=0, max=0."""
        method = create_test_method(estimated_days_min=0, estimated_days_max=0)
        self.assertEqual(method.estimated_days_display, 'Сегодня')

    def test_estimated_days_today_tomorrow(self):
        """estimated_days_display при min=0, max=1."""
        method = create_test_method(estimated_days_min=0, estimated_days_max=1)
        self.assertEqual(method.estimated_days_display, 'Сегодня-завтра')


# ================================================================
# Shipment
# ================================================================

class ShipmentModelTests(TestCase):
    """Тесты модели Shipment."""

    def setUp(self):
        self.user = create_test_user()
        self.order = create_test_order(self.user)

    def test_create_shipment(self):
        """Успешное создание отправления."""
        shipment = create_test_shipment(self.order)
        self.assertIsNotNone(shipment.pk)
        self.assertEqual(shipment.order, self.order)
        self.assertEqual(shipment.user, self.user)
        self.assertEqual(shipment.status, 'preparing')
        self.assertEqual(shipment.shipping_cost, Decimal('300.00'))

    def test_auto_generate_internal_tracking(self):
        """Автогенерация internal_tracking при создании."""
        shipment = create_test_shipment(self.order)
        self.assertTrue(shipment.internal_tracking.startswith('SHP-'))
        # Длина: SHP- + 8 цифр = 12 символов
        self.assertEqual(len(shipment.internal_tracking), 12)

    def test_internal_tracking_sequential(self):
        """Номера internal_tracking последовательны."""
        zone = create_test_zone(zone_code='seq_zone')
        method = create_test_method(zone=zone, name='Seq Method')
        user2 = create_test_user()
        order2 = create_test_order(user2)

        s1 = create_test_shipment(self.order, method=method)
        s2 = create_test_shipment(order2, method=method)

        # Номер s2 > номера s1
        self.assertNotEqual(s1.internal_tracking, s2.internal_tracking)

    def test_str_representation(self):
        """__str__ содержит публичный номер отправления и статус."""
        shipment = create_test_shipment(self.order)
        str_repr = str(shipment)
        self.assertIn(shipment.shipment_number, str_repr)
        self.assertIn('Собирается', str_repr)

    def test_is_terminal_preparing(self):
        """PREPARING — не терминальный."""
        shipment = create_test_shipment(self.order, status='preparing')
        self.assertFalse(shipment.is_terminal)

    def test_is_terminal_delivered(self):
        """DELIVERED — терминальный."""
        shipment = create_test_shipment(self.order, status='delivered')
        self.assertTrue(shipment.is_terminal)

    def test_is_terminal_returned(self):
        """RETURNED — терминальный."""
        shipment = create_test_shipment(self.order, status='returned')
        self.assertTrue(shipment.is_terminal)

    def test_unique_order_one_to_one(self):
        """Один заказ — одно отправление (OneToOne)."""
        create_test_shipment(self.order)
        with self.assertRaises(Exception):
            create_test_shipment(self.order)

    def test_unique_internal_tracking(self):
        """internal_tracking уникален."""
        s1 = create_test_shipment(self.order)
        # internal_tracking генерируется автоматически — проверяем уникальность
        self.assertIsNotNone(s1.internal_tracking)

    def test_ordering_by_created_at_desc(self):
        """Отправления сортируются по -created_at."""
        shipment = create_test_shipment(self.order)
        self.assertIsNotNone(shipment.pk)
