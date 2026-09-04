# ────────────────────────────────────────────────────────────────────────
# apps/shipping/tests/test_api.py — тесты API endpoints доставки.
#
# Проверяет:
#   • GET /api/v1/shipping/methods/
#   • POST /api/v1/shipping/calculate/
#   • GET /api/v1/shipping/shipments/
#   • POST /api/v1/shipping/shipments/create/
#   • GET /api/v1/shipping/shipments/{id}/
#   • PATCH /api/v1/shipping/shipments/{id}/status/
#   • POST /api/v1/shipping/shipments/{id}/tracking/
#   • GET /api/v1/shipping/track/{tracking}/
#
# 📖 https://www.django-rest-framework.org/api-guide/testing/
# ────────────────────────────────────────────────────────────────────────

from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from apps.orders.tests.factories import create_test_order, create_test_user
from apps.shipping.tests.factories import (
    create_test_method,
    create_test_shipment,
    create_test_zone,
)


class ShippingMethodListAPITests(TestCase):
    """Тесты GET /api/v1/shipping/methods/."""

    def setUp(self):
        self.user = create_test_user()
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.zone = create_test_zone()
        self.method = create_test_method(zone=self.zone)

    def test_list_methods(self):
        """Получение списка способов доставки."""
        url = reverse('shipping:method-list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)

    def test_list_requires_auth(self):
        """Требуется авторизация."""
        self.client.logout()
        url = reverse('shipping:method-list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class ShippingCostCalculateAPITests(TestCase):
    """Тесты POST /api/v1/shipping/calculate/."""

    def setUp(self):
        self.user = create_test_user()
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.zone = create_test_zone()
        self.method = create_test_method(zone=self.zone)

    def test_calculate_success(self):
        """Успешный расчёт стоимости."""
        url = reverse('shipping:calculate')
        data = {
            'zone_code': 'msk',
            'order_total': '1000.00',
        }
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['methods']), 1)
        self.assertEqual(response.data['methods'][0]['cost'], Decimal('300.00'))

    def test_calculate_no_zone_or_region(self):
        """Ошибка если не указана зона или регион."""
        url = reverse('shipping:calculate')
        data = {'order_total': '1000.00'}
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_calculate_with_region(self):
        """Расчёт по названию региона."""
        url = reverse('shipping:calculate')
        data = {
            'region': 'Москва',
            'order_total': '1000.00',
        }
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)


class ShipmentListAPITests(TestCase):
    """Тесты GET /api/v1/shipping/shipments/."""

    def setUp(self):
        self.user = create_test_user()
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.order = create_test_order(self.user, status='confirmed')

    def test_list_user_shipments(self):
        """Пользователь видит свои отправления."""
        create_test_shipment(self.order)
        url = reverse('shipping:shipment-list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(len(response.data['results']), 1)

    def test_list_empty(self):
        """Пустой список если нет отправлений."""
        url = reverse('shipping:shipment-list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 0)
        self.assertEqual(response.data['results'], [])
        self.assertEqual(response.data['total_pages'], 0)


class ShipmentCreateAPITests(TestCase):
    """Тесты POST /api/v1/shipping/shipments/create/."""

    def setUp(self):
        self.admin = create_test_user(is_staff=True)
        self.client = APIClient()
        self.client.force_authenticate(user=self.admin)
        self.zone = create_test_zone()
        self.method = create_test_method(zone=self.zone)
        self.user = create_test_user()
        self.order = create_test_order(self.user, status='confirmed')

    def test_create_success(self):
        """Успешное создание отправления (staff)."""
        url = reverse('shipping:shipment-create')
        data = {
            'order_id': self.order.pk,
            'method_id': self.method.pk,
        }
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['order_number'], self.order.order_number)

    def test_create_non_staff_forbidden(self):
        """Обычный пользователь не может создать."""
        regular_user = create_test_user()
        self.client.force_authenticate(user=regular_user)
        url = reverse('shipping:shipment-create')
        data = {
            'order_id': self.order.pk,
            'method_id': self.method.pk,
        }
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_create_order_not_found(self):
        """Ошибка при несуществующем заказе."""
        url = reverse('shipping:shipment-create')
        data = {
            'order_id': 99999,
            'method_id': self.method.pk,
        }
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class ShipmentDetailAPITests(TestCase):
    """Тесты GET /api/v1/shipping/shipments/{id}/."""

    def setUp(self):
        self.user = create_test_user()
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.order = create_test_order(self.user, status='confirmed')
        self.shipment = create_test_shipment(self.order)

    def test_detail_success(self):
        """Успешное получение деталей."""
        url = reverse('shipping:shipment-detail', kwargs={'pk': self.shipment.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['internal_tracking'], self.shipment.internal_tracking)

    def test_detail_not_found(self):
        """NotFound для несуществующего отправления."""
        url = reverse('shipping:shipment-detail', kwargs={'pk': 99999})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class ShipmentStatusAPITests(TestCase):
    """Тесты PATCH /api/v1/shipping/shipments/{id}/status/."""

    def setUp(self):
        self.admin = create_test_user(is_staff=True)
        self.client = APIClient()
        self.client.force_authenticate(user=self.admin)
        self.user = create_test_user()
        self.order = create_test_order(self.user, status='confirmed')
        self.shipment = create_test_shipment(self.order, status='preparing')

    def test_transition_success(self):
        """Успешный переход статуса."""
        url = reverse('shipping:shipment-status', kwargs={'pk': self.shipment.pk})
        data = {'status': 'in_transit'}
        response = self.client.patch(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], 'in_transit')

    def test_transition_invalid(self):
        """Недопустимый переход → 400."""
        url = reverse('shipping:shipment-status', kwargs={'pk': self.shipment.pk})
        data = {'status': 'delivered'}  # preparing → delivered: invalid
        response = self.client.patch(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class ShipmentTrackingAPITests(TestCase):
    """Тесты POST /api/v1/shipping/shipments/{id}/tracking/."""

    def setUp(self):
        self.admin = create_test_user(is_staff=True)
        self.client = APIClient()
        self.client.force_authenticate(user=self.admin)
        self.user = create_test_user()
        self.order = create_test_order(self.user, status='confirmed')
        self.shipment = create_test_shipment(self.order)

    def test_update_tracking_success(self):
        """Успешное обновление трек-номера."""
        url = reverse('shipping:shipment-tracking', kwargs={'pk': self.shipment.pk})
        data = {'tracking_number': 'TRACK-99999'}
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['tracking_number'], 'TRACK-99999')


class ShipmentTrackingByCodeAPITests(TestCase):
    """Тесты GET /api/v1/shipping/track/{tracking}/."""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user()
        self.order = create_test_order(self.user, status='confirmed')
        self.shipment = create_test_shipment(self.order, tracking_number='EXT-12345')

    def test_track_by_external(self):
        """Отслеживание по внешнему треку (без авторизации)."""
        url = reverse('shipping:track-by-code', kwargs={'tracking': 'EXT-12345'})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['tracking_number'], 'EXT-12345')

    def test_track_by_internal(self):
        """Отслеживание по внутреннему треку."""
        url = reverse(
            'shipping:track-by-code',
            kwargs={'tracking': self.shipment.internal_tracking},
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_track_not_found(self):
        """NotFound для несуществующего трека."""
        url = reverse('shipping:track-by-code', kwargs={'tracking': 'NOPE'})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_track_no_auth_required(self):
        """Публичный endpoint — авторизация не нужна."""
        url = reverse('shipping:track-by-code', kwargs={'tracking': 'EXT-12345'})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
