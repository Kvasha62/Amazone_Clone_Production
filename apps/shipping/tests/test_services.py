# ────────────────────────────────────────────────────────────────────────
# apps/shipping/tests/test_services.py — тесты сервиса доставки.
#
# Проверяет:
#   • ShippingService.calculate_shipping_cost()
#   • ShippingService.get_available_methods()
#   • ShippingService.create_shipment()
#   • ShippingService.update_tracking()
#   • ShippingService.transition_status()
#   • ShippingService._resolve_zone()
#   • ShippingService._sync_order_status()
#
# 📖 https://docs.djangoproject.com/en/stable/topics/testing/overview/
# ────────────────────────────────────────────────────────────────────────

from decimal import Decimal
from unittest import mock

from django.db import DatabaseError
from django.test import TestCase
from rest_framework.exceptions import NotFound, ValidationError

from apps.orders.models import Order
from apps.orders.services.order_service import OrderService
from apps.orders.tests.factories import create_test_order, create_test_user
from apps.shipping.constants import MAX_SHIPPING_COST
from apps.shipping.models import Shipment, ShippingMethod, ShippingZone
from apps.shipping.services.shipping_service import ShippingService
from apps.shipping.tests.factories import (
    create_test_method,
    create_test_shipment,
    create_test_zone,
)


# ================================================================
# Расчёт стоимости доставки
# ================================================================

class CalculateShippingCostTests(TestCase):
    """Тесты ShippingService.calculate_shipping_cost()."""

    def setUp(self):
        self.zone = create_test_zone()
        self.method_courier = create_test_method(
            zone=self.zone,
            name='Курьер',
            shipping_type='courier',
            base_price=Decimal('300.00'),
            price_per_kg=Decimal('50.000'),
            free_shipping_threshold=Decimal('5000.00'),
        )
        self.method_pickup = create_test_method(
            zone=self.zone,
            name='Самовывоз',
            shipping_type='pickup',
            base_price=Decimal('0.00'),
            price_per_kg=Decimal('0.000'),
            free_shipping_threshold=None,
            sort_order=20,
        )

    def test_calculate_with_zone_code(self):
        """Расчёт по коду зоны."""
        result = ShippingService.calculate_shipping_cost(
            order_total=Decimal('1000.00'),
            zone_code='msk',
        )
        self.assertEqual(result['zone'], self.zone)
        self.assertEqual(len(result['methods']), 2)

    def test_calculate_with_region(self):
        """Расчёт по названию региона."""
        result = ShippingService.calculate_shipping_cost(
            order_total=Decimal('1000.00'),
            region='Москва',
        )
        self.assertEqual(result['zone'], self.zone)
        self.assertEqual(len(result['methods']), 2)

    def test_calculate_with_zone_object(self):
        """Расчёт с передачей объекта зоны."""
        result = ShippingService.calculate_shipping_cost(
            order_total=Decimal('1000.00'),
            zone=self.zone,
        )
        self.assertEqual(result['zone'], self.zone)

    def test_calculate_filter_by_type(self):
        """Фильтрация по типу доставки."""
        result = ShippingService.calculate_shipping_cost(
            order_total=Decimal('1000.00'),
            zone=self.zone,
            shipping_type='courier',
        )
        self.assertEqual(len(result['methods']), 1)
        self.assertEqual(result['methods'][0]['method'], self.method_courier)

    def test_calculate_free_shipping(self):
        """Бесплатная доставка при превышении порога."""
        result = ShippingService.calculate_shipping_cost(
            order_total=Decimal('5000.00'),
            zone=self.zone,
            shipping_type='courier',
        )
        # Курьер: free_shipping_threshold=5000 → cost=0
        self.assertEqual(result['methods'][0]['cost'], Decimal('0.00'))

    def test_calculate_no_zone_found(self):
        """Зона не найдена — пустой список методов."""
        result = ShippingService.calculate_shipping_cost(
            order_total=Decimal('1000.00'),
            zone_code='nonexistent',
        )
        self.assertIsNone(result['zone'])
        self.assertEqual(len(result['methods']), 0)

    def test_calculate_with_weight(self):
        """Расчёт с учётом веса."""
        result = ShippingService.calculate_shipping_cost(
            order_total=Decimal('1000.00'),
            zone=self.zone,
            shipping_type='courier',
            weight_kg=Decimal('2.000'),
        )
        # 300 + 50*2 = 400
        self.assertEqual(result['methods'][0]['cost'], Decimal('400.00'))

    def test_calculate_no_zone_no_region(self):
        """Без зоны и региона — все методы (нет фильтра по зоне)."""
        result = ShippingService.calculate_shipping_cost(
            order_total=Decimal('1000.00'),
        )
        self.assertIsNone(result['zone'])
        # Нет зоны → нет фильтра → все активные методы
        self.assertEqual(len(result['methods']), 2)


# ================================================================
# Авторитетная цена доставки для заказа (F-08 / PROD-006)
# ================================================================

class CalculateOrderDeliveryCostTests(TestCase):
    """Тесты ShippingService.calculate_order_delivery_cost().

    Единственный серверный путь, которым checkout получает
    Order.delivery_cost: только доменные данные, ничего из запроса.
    """

    def setUp(self):
        self.zone = create_test_zone(
            name='Москва и МО',
            zone_code='msk',
            regions=['Москва', 'Московская область'],
        )
        self.method = create_test_method(
            zone=self.zone,
            name='Курьер',
            base_price=Decimal('300.00'),
            price_per_kg=Decimal('50.000'),
            free_shipping_threshold=Decimal('5000.00'),
            sort_order=10,
        )

    def test_cost_from_method_tariff(self):
        """База + вес, порог бесплатной доставки не достигнут."""
        cost = ShippingService.calculate_order_delivery_cost(
            order_total=Decimal('1000.00'),
            city='Москва',
            weight_kg=Decimal('2.00'),
        )
        # 300 + 50 × 2 = 400
        self.assertEqual(cost, Decimal('400.00'))

    def test_zone_resolved_by_region(self):
        """Зона определяется по региону адреса."""
        cost = ShippingService.calculate_order_delivery_cost(
            order_total=Decimal('1000.00'),
            region='Московская область',
        )
        self.assertEqual(cost, Decimal('300.00'))

    def test_free_shipping_threshold(self):
        """Сумма ≥ порога → бесплатная доставка."""
        cost = ShippingService.calculate_order_delivery_cost(
            order_total=Decimal('5000.00'),
            city='Москва',
        )
        self.assertEqual(cost, Decimal('0.00'))

    def test_no_zone_returns_zero(self):
        """Зона не определена → NO_DELIVERY_CHARGE (серверная константа)."""
        cost = ShippingService.calculate_order_delivery_cost(
            order_total=Decimal('1000.00'),
            city='Неизвестный город',
        )
        self.assertEqual(cost, Decimal('0.00'))

    def test_no_address_data_returns_zero(self):
        """Без региона и города тариф не подбирается."""
        cost = ShippingService.calculate_order_delivery_cost(
            order_total=Decimal('1000.00'),
        )
        self.assertEqual(cost, Decimal('0.00'))

    def test_inactive_method_ignored(self):
        """Неактивный способ доставки не участвует в расчёте."""
        self.method.is_active = False
        self.method.save(update_fields=['is_active', 'updated_at'])

        cost = ShippingService.calculate_order_delivery_cost(
            order_total=Decimal('1000.00'),
            city='Москва',
        )
        self.assertEqual(cost, Decimal('0.00'))

    def test_primary_method_selected_by_sort_order(self):
        """Берётся основной способ зоны (Meta.ordering: sort_order, base_price)."""
        create_test_method(
            zone=self.zone,
            name='Самовывоз',
            base_price=Decimal('99.00'),
            price_per_kg=Decimal('0.000'),
            free_shipping_threshold=None,
            sort_order=5,
        )

        cost = ShippingService.calculate_order_delivery_cost(
            order_total=Decimal('1000.00'),
            city='Москва',
        )
        self.assertEqual(cost, Decimal('99.00'))

    def test_cost_capped_by_max_shipping_cost(self):
        """Опечатка в тарифе не даёт цене превысить MAX_SHIPPING_COST."""
        self.method.base_price = MAX_SHIPPING_COST + Decimal('5000.00')
        self.method.free_shipping_threshold = None
        self.method.max_shipping_cost = None
        self.method.save(update_fields=[
            'base_price',
            'free_shipping_threshold',
            'max_shipping_cost',
            'updated_at',
        ])

        cost = ShippingService.calculate_order_delivery_cost(
            order_total=Decimal('1000.00'),
            city='Москва',
        )
        self.assertEqual(cost, MAX_SHIPPING_COST)

    def test_shipping_type_filter(self):
        """Фильтр по типу доставки ограничивает выбор способа."""
        create_test_method(
            zone=self.zone,
            name='Самовывоз',
            shipping_type='pickup',
            base_price=Decimal('99.00'),
            price_per_kg=Decimal('0.000'),
            free_shipping_threshold=None,
            sort_order=5,
        )

        cost = ShippingService.calculate_order_delivery_cost(
            order_total=Decimal('1000.00'),
            city='Москва',
            shipping_type='pickup',
        )
        self.assertEqual(cost, Decimal('99.00'))

        courier_cost = ShippingService.calculate_order_delivery_cost(
            order_total=Decimal('1000.00'),
            city='Москва',
            shipping_type='courier',
        )
        self.assertEqual(courier_cost, Decimal('300.00'))


# ================================================================
# Список доступных способов
# ================================================================

class GetAvailableMethodsTests(TestCase):
    """Тесты ShippingService.get_available_methods()."""

    def setUp(self):
        self.zone = create_test_zone()
        self.method1 = create_test_method(
            zone=self.zone, name='Курьер', shipping_type='courier',
        )
        self.method2 = create_test_method(
            zone=self.zone, name='Самовывоз', shipping_type='pickup',
            sort_order=20,
        )

    def test_get_all_for_zone(self):
        """Все методы для зоны."""
        methods = ShippingService.get_available_methods(zone_code='msk')
        self.assertEqual(len(methods), 2)

    def test_filter_by_type(self):
        """Фильтрация по типу."""
        methods = ShippingService.get_available_methods(
            zone_code='msk', shipping_type='courier',
        )
        self.assertEqual(len(methods), 1)
        self.assertEqual(methods[0].shipping_type, 'courier')

    def test_inactive_methods_excluded(self):
        """Неактивные методы исключены."""
        self.method2.is_active = False
        self.method2.save()
        methods = ShippingService.get_available_methods(zone_code='msk')
        self.assertEqual(len(methods), 1)

    def test_by_region(self):
        """Определение зоны по региону."""
        methods = ShippingService.get_available_methods(region='Москва')
        self.assertEqual(len(methods), 2)

    def test_unknown_region_empty(self):
        """Неизвестный регион — пустой список."""
        methods = ShippingService.get_available_methods(region='Тула')
        self.assertEqual(len(methods), 0)


# ================================================================
# Создание отправления
# ================================================================

class CreateShipmentTests(TestCase):
    """Тесты ShippingService.create_shipment()."""

    def setUp(self):
        self.user = create_test_user()
        self.zone = create_test_zone()
        self.method = create_test_method(zone=self.zone)
        # Создаём подтверждённый заказ
        self.order = create_test_order(
            self.user,
            status='confirmed',
        )

    def test_create_success(self):
        """Успешное создание отправления."""
        shipment = ShippingService.create_shipment(
            order=self.order,
            method=self.method,
        )
        self.assertIsNotNone(shipment.pk)
        self.assertEqual(shipment.order, self.order)
        self.assertEqual(shipment.user, self.user)
        self.assertEqual(shipment.method, self.method)
        self.assertEqual(shipment.status, 'preparing')

    def test_create_calculates_cost(self):
        """Стоимость рассчитывается автоматически."""
        shipment = ShippingService.create_shipment(
            order=self.order,
            method=self.method,
        )
        # method.base_price=300, order.total=1000 (< 5000 threshold)
        self.assertEqual(shipment.shipping_cost, Decimal('300.00'))

    def test_create_with_custom_cost(self):
        """Явно указанная стоимость."""
        shipment = ShippingService.create_shipment(
            order=self.order,
            method=self.method,
            shipping_cost=Decimal('450.00'),
        )
        self.assertEqual(shipment.shipping_cost, Decimal('450.00'))

    def test_create_with_weight(self):
        """Создание с указанием веса."""
        shipment = ShippingService.create_shipment(
            order=self.order,
            method=self.method,
            weight_kg=Decimal('2.500'),
        )
        self.assertEqual(shipment.weight_kg, Decimal('2.500'))

    def test_create_duplicate_raises(self):
        """Нельзя создать два отправления для одного заказа."""
        ShippingService.create_shipment(order=self.order, method=self.method)
        with self.assertRaises(ValidationError) as ctx:
            ShippingService.create_shipment(order=self.order, method=self.method)
        self.assertIn('уже имеет отправление', str(ctx.exception.detail))

    def test_create_pending_order_raises(self):
        """Нельзя создать для заказа в PENDING."""
        pending_order = create_test_order(self.user, status='pending')
        with self.assertRaises(ValidationError) as ctx:
            ShippingService.create_shipment(
                order=pending_order, method=self.method,
            )
        self.assertIn('PENDING', str(ctx.exception.detail))

    def test_create_auto_generates_tracking(self):
        """internal_tracking генерируется автоматически."""
        shipment = ShippingService.create_shipment(
            order=self.order, method=self.method,
        )
        self.assertTrue(shipment.internal_tracking.startswith('SHP-'))


# ================================================================
# Обновление трек-номера
# ================================================================

class UpdateTrackingTests(TestCase):
    """Тесты ShippingService.update_tracking()."""

    def setUp(self):
        self.user = create_test_user()
        self.order = create_test_order(self.user, status='confirmed')
        self.shipment = create_test_shipment(self.order)

    def test_update_tracking_success(self):
        """Успешное обновление трек-номера."""
        shipment = ShippingService.update_tracking(
            self.shipment, '1234567890',
        )
        self.assertEqual(shipment.tracking_number, '1234567890')

    def test_update_tracking_empty(self):
        """Обновление пустым трек-номером (допустимо)."""
        shipment = ShippingService.update_tracking(
            self.shipment, '',
        )
        self.assertEqual(shipment.tracking_number, '')


# ================================================================
# Переход статуса
# ================================================================

class TransitionStatusTests(TestCase):
    """Тесты ShippingService.transition_status()."""

    def setUp(self):
        self.user = create_test_user()
        self.order = create_test_order(self.user, status='confirmed')
        self.shipment = create_test_shipment(self.order, status='preparing')

    def test_transition_preparing_to_in_transit(self):
        """PREPARING → IN_TRANSIT."""
        shipment = ShippingService.transition_status(
            self.shipment, 'in_transit',
        )
        self.assertEqual(shipment.status, 'in_transit')
        self.assertIsNotNone(shipment.shipped_at)

    def test_transition_in_transit_to_out_for_delivery(self):
        """IN_TRANSIT → OUT_FOR_DELIVERY."""
        self.shipment.status = 'in_transit'
        self.shipment.save()
        shipment = ShippingService.transition_status(
            self.shipment, 'out_for_delivery',
        )
        self.assertEqual(shipment.status, 'out_for_delivery')

    def test_transition_out_for_delivery_to_delivered(self):
        """OUT_FOR_DELIVERY → DELIVERED."""
        self.shipment.status = 'out_for_delivery'
        self.shipment.save()
        shipment = ShippingService.transition_status(
            self.shipment, 'delivered',
        )
        self.assertEqual(shipment.status, 'delivered')
        self.assertIsNotNone(shipment.delivered_at)

    def test_transition_preparing_to_returned(self):
        """PREPARING → RETURNED."""
        shipment = ShippingService.transition_status(
            self.shipment, 'returned',
        )
        self.assertEqual(shipment.status, 'returned')

    def test_transition_invalid_raises(self):
        """Недопустимый переход → ValidationError."""
        with self.assertRaises(ValidationError) as ctx:
            ShippingService.transition_status(
                self.shipment, 'delivered',  # preparing → delivered: недопустимо
            )
        self.assertIn('недопустим', str(ctx.exception.detail))

    def test_transition_terminal_raises(self):
        """Переход из терминального статуса → ValidationError."""
        self.shipment.status = 'delivered'
        self.shipment.save()
        with self.assertRaises(ValidationError) as ctx:
            ShippingService.transition_status(
                self.shipment, 'in_transit',
            )
        self.assertIn('терминальном', str(ctx.exception.detail))

    def test_transition_failed_to_in_transit(self):
        """FAILED → IN_TRANSIT (повторная попытка)."""
        self.shipment.status = 'failed'
        self.shipment.save()
        shipment = ShippingService.transition_status(
            self.shipment, 'in_transit',
        )
        self.assertEqual(shipment.status, 'in_transit')

    def test_transition_failed_to_returned(self):
        """FAILED → RETURNED."""
        self.shipment.status = 'failed'
        self.shipment.save()
        shipment = ShippingService.transition_status(
            self.shipment, 'returned',
        )
        self.assertEqual(shipment.status, 'returned')

    def test_transition_with_tracking_number(self):
        """Переход с обновлением трек-номера."""
        shipment = ShippingService.transition_status(
            self.shipment, 'in_transit',
            tracking_number='TRACK-12345',
        )
        self.assertEqual(shipment.tracking_number, 'TRACK-12345')

    def test_in_transit_to_failed(self):
        """IN_TRANSIT → FAILED."""
        self.shipment.status = 'in_transit'
        self.shipment.save()
        shipment = ShippingService.transition_status(
            self.shipment, 'failed',
        )
        self.assertEqual(shipment.status, 'failed')

    def test_in_transit_to_returned(self):
        """IN_TRANSIT → RETURNED."""
        self.shipment.status = 'in_transit'
        self.shipment.save()
        shipment = ShippingService.transition_status(
            self.shipment, 'returned',
        )
        self.assertEqual(shipment.status, 'returned')


# ================================================================
# Определение зоны
# ================================================================

class ResolveZoneTests(TestCase):
    """Тесты ShippingService._resolve_zone()."""

    def setUp(self):
        self.zone_msk = create_test_zone(
            name='Москва', zone_code='msk',
            regions=['Москва', 'Московская область'],
        )
        self.zone_spb = create_test_zone(
            name='Санкт-Петербург', zone_code='spb',
            regions=['Санкт-Петербург', 'Ленинградская область'],
        )

    def test_resolve_by_zone_code(self):
        """Определение зоны по коду."""
        zone = ShippingService._resolve_zone(zone_code='msk')
        self.assertEqual(zone, self.zone_msk)

    def test_resolve_by_region(self):
        """Определение зоны по названию региона."""
        zone = ShippingService._resolve_zone(region='Санкт-Петербург')
        self.assertEqual(zone, self.zone_spb)

    def test_resolve_by_region_case_insensitive(self):
        """Определение зоны без учёта регистра."""
        zone = ShippingService._resolve_zone(region='москва')
        self.assertEqual(zone, self.zone_msk)

    def test_resolve_unknown_zone_code(self):
        """Неизвестный код зоны → None."""
        zone = ShippingService._resolve_zone(zone_code='unknown')
        self.assertIsNone(zone)

    def test_resolve_unknown_region(self):
        """Неизвестный регион → None."""
        zone = ShippingService._resolve_zone(region='Новосибирск')
        self.assertIsNone(zone)

    def test_resolve_nothing_returns_none(self):
        """Без аргументов → None."""
        zone = ShippingService._resolve_zone()
        self.assertIsNone(zone)


# ================================================================
# Синхронизация статуса заказа
# ================================================================

class SyncOrderStatusTests(TestCase):
    """Тесты ShippingService._sync_order_status()."""

    def setUp(self):
        self.user = create_test_user()
        self.order = create_test_order(self.user, status='confirmed')
        self.zone = create_test_zone()
        self.method = create_test_method(zone=self.zone)

    def test_sync_in_transit_to_processing(self):
        """IN_TRANSIT → Order.status=PROCESSING."""
        shipment = create_test_shipment(
            self.order, method=self.method, status='preparing',
        )
        ShippingService._sync_order_status(shipment, 'in_transit')
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, 'processing')

    def test_sync_delivered(self):
        """DELIVERED → Order.status=DELIVERED."""
        # Order must be in 'shipped' status for 'delivered' transition
        self.order.status = 'shipped'
        self.order.save()
        shipment = create_test_shipment(
            self.order, method=self.method, status='out_for_delivery',
        )
        ShippingService._sync_order_status(shipment, 'delivered')
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, 'delivered')

    def test_sync_returned_to_cancelled(self):
        """RETURNED → Order.status=CANCELLED."""
        shipment = create_test_shipment(
            self.order, method=self.method, status='preparing',
        )
        ShippingService._sync_order_status(shipment, 'returned')
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, 'cancelled')

    def test_sync_noop_for_unmapped_status(self):
        """Статус без маппинга → заказ не меняется."""
        shipment = create_test_shipment(
            self.order, method=self.method, status='preparing',
        )
        original_status = self.order.status
        ShippingService._sync_order_status(shipment, 'preparing')
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, original_status)


# ================================================================
# F-14 / PROD-012: обработка исключений синхронного пути доставки
# ================================================================

class SyncExceptionHandlingTests(TestCase):
    """Регрессия F-14: ошибки синхронного пути не проглатываются.

    ОЖИДАЕМЫЙ доменный исход (ValidationError доменной FSM заказа) —
    операция доставки завершается успешно, поведение API прежнее.
    НЕОЖИДАННЫЙ сбой — пробрасывается наружу, транзакция откатывается,
    частичное состояние Shipment/Order не сохраняется.
    """

    def setUp(self):
        self.user = create_test_user()
        self.order = create_test_order(self.user, status='confirmed')
        self.zone = create_test_zone()
        self.method = create_test_method(zone=self.zone)
        self.shipment = create_test_shipment(
            self.order, method=self.method, status='preparing',
        )

    # ── Успешный путь остаётся прежним ────────────────────────────

    def test_successful_transition_still_syncs_order(self):
        """AC-6: успешный переход по-прежнему синхронизирует заказ."""
        shipment = ShippingService.transition_status(
            self.shipment, 'in_transit',
        )
        self.order.refresh_from_db()
        self.assertEqual(shipment.status, 'in_transit')
        self.assertEqual(self.order.status, 'processing')

    # ── ОЖИДАЕМЫЙ доменный исход ──────────────────────────────────

    def test_expected_order_domain_error_does_not_break_shipment(self):
        """AC-2: ValidationError доменной FSM заказа не ломает доставку."""
        # Заказ в терминальном статусе → OrderService отклонит переход.
        self.order.status = 'cancelled'
        self.order.save(update_fields=['status'])

        with self.assertLogs(
            'apps.shipping.services.shipping_service', level='WARNING',
        ) as logs:
            shipment = ShippingService.transition_status(
                self.shipment, 'in_transit',
            )

        self.assertEqual(shipment.status, 'in_transit')
        self.shipment.refresh_from_db()
        self.assertEqual(self.shipment.status, 'in_transit')
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, 'cancelled')
        self.assertIn('order_status_sync_rejected', ''.join(logs.output))

    def test_expected_domain_error_from_shipment_fsm_still_raises(self):
        """AC-2: недопустимый переход доставки по-прежнему ValidationError."""
        with self.assertRaises(ValidationError):
            ShippingService.transition_status(self.shipment, 'delivered')
        self.shipment.refresh_from_db()
        self.assertEqual(self.shipment.status, 'preparing')

    # ── НЕОЖИДАННЫЙ сбой ──────────────────────────────────────────

    def test_unexpected_exception_is_not_swallowed(self):
        """AC-1 / AC-3: неожиданное исключение доходит до вызывающего."""
        with mock.patch.object(
            OrderService,
            'transition_status',
            side_effect=RuntimeError('boom'),
        ):
            with self.assertRaises(RuntimeError):
                ShippingService.transition_status(self.shipment, 'in_transit')

    def test_unexpected_database_error_is_not_swallowed(self):
        """AC-3: инфраструктурный сбой (DatabaseError) пробрасывается."""
        with mock.patch.object(
            OrderService,
            'cancel',
            side_effect=DatabaseError('connection lost'),
        ):
            with self.assertRaises(DatabaseError):
                ShippingService.transition_status(self.shipment, 'returned')

    def test_missing_order_is_not_swallowed(self):
        """AC-3: отсутствующий Order — не «успех», а ошибка."""
        with mock.patch(
            'apps.orders.models.Order.objects.get',
            side_effect=Order.DoesNotExist,
        ):
            with self.assertRaises(Order.DoesNotExist):
                ShippingService.transition_status(self.shipment, 'in_transit')

    # ── Откат транзакции ──────────────────────────────────────────

    def test_failed_transition_rolls_back_shipment_state(self):
        """AC-4: при неожиданном сбое Shipment не сохраняется частично."""
        with mock.patch.object(
            OrderService,
            'transition_status',
            side_effect=RuntimeError('boom'),
        ):
            with self.assertRaises(RuntimeError):
                ShippingService.transition_status(self.shipment, 'in_transit')

        self.shipment.refresh_from_db()
        self.assertEqual(self.shipment.status, 'preparing')
        self.assertIsNone(self.shipment.shipped_at)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, 'confirmed')

    def test_failed_transition_rolls_back_tracking_number(self):
        """AC-4: трек-номер из неудавшегося перехода не сохраняется."""
        with mock.patch.object(
            OrderService,
            'transition_status',
            side_effect=RuntimeError('boom'),
        ):
            with self.assertRaises(RuntimeError):
                ShippingService.transition_status(
                    self.shipment, 'in_transit', tracking_number='TRK-999',
                )

        self.shipment.refresh_from_db()
        self.assertNotEqual(self.shipment.tracking_number, 'TRK-999')

    # ── Блокировки сохранены ──────────────────────────────────────

    def test_transition_still_locks_shipment_row(self):
        """AC-5: select_for_update() сохранён в пути перехода статуса."""
        with mock.patch(
            'apps.shipping.models.Shipment.objects.select_for_update',
            wraps=Shipment.objects.select_for_update,
        ) as locked:
            ShippingService.transition_status(self.shipment, 'in_transit')
        self.assertTrue(locked.called)


# ================================================================
# Получение отправления
# ================================================================

class GetShipmentTests(TestCase):
    """Тесты ShippingService.get_shipment_by_order/order/get_by_tracking."""

    def setUp(self):
        self.user = create_test_user()
        self.order = create_test_order(self.user, status='confirmed')
        self.shipment = create_test_shipment(self.order)

    def test_get_by_order(self):
        """Получение отправления по заказу."""
        result = ShippingService.get_shipment_by_order(self.order)
        self.assertEqual(result.pk, self.shipment.pk)

    def test_get_by_order_not_found(self):
        """NotFound если отправление не существует."""
        from apps.orders.tests.factories import create_test_user
        other_user = create_test_user()
        other_order = create_test_order(other_user, status='confirmed')
        with self.assertRaises(NotFound):
            ShippingService.get_shipment_by_order(other_order)

    def test_get_by_internal_tracking_not_found(self):
        """Внутренний трек НЕ является публичным ключом поиска (Issue #69).

        ``get_shipment_by_tracking`` резолвит только по внешнему
        ``tracking_number``; передача существующего ``internal_tracking``
        должна приводить к тому же ``NotFound``, что и неизвестный номер.
        """
        with self.assertRaises(NotFound):
            ShippingService.get_shipment_by_tracking(
                self.shipment.internal_tracking,
            )

    def test_get_by_external_tracking(self):
        """Получение по внешнему трек-номеру."""
        self.shipment.tracking_number = 'EXT-12345'
        self.shipment.save()
        result = ShippingService.get_shipment_by_tracking('EXT-12345')
        self.assertEqual(result.pk, self.shipment.pk)

    def test_get_by_tracking_not_found(self):
        """NotFound для несуществующего трека."""
        with self.assertRaises(NotFound):
            ShippingService.get_shipment_by_tracking('NO-SUCH-TRACK')
