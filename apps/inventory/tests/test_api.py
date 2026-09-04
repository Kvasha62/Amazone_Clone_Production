# ────────────────────────────────────────────────────────────────────────
# apps/inventory/tests/test_api.py — тесты API endpoints склада.
# ────────────────────────────────────────────────────────────────────────

from django.test import TestCase

from rest_framework.test import APIClient

from apps.catalog.tests.factories import CatalogTestCase
from apps.inventory.models import Stock
from apps.inventory.tests.factories import create_test_stock
from apps.orders.tests.factories import create_test_user


class InventoryAPITests(CatalogTestCase):
    """Тесты API склада (staff only)."""

    def setUp(self):
        self.staff = create_test_user()
        self.staff.is_staff = True
        self.staff.save()

        self.client = APIClient()
        self.client.force_authenticate(user=self.staff)

        self.stock = create_test_stock(self.variant_128, quantity=100)

    # ── Список остатков ──

    def test_list_stocks(self):
        resp = self.client.get('/api/v1/inventory/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['count'], 1)
        self.assertEqual(len(resp.data['results']), 1)

    def test_list_stocks_non_staff(self):
        non_staff = create_test_user()
        self.client.force_authenticate(user=non_staff)
        resp = self.client.get('/api/v1/inventory/')
        self.assertEqual(resp.status_code, 403)

    # ── Детали ──

    def test_stock_detail(self):
        resp = self.client.get(f'/api/v1/inventory/{self.variant_128.pk}/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['quantity'], 100)

    def test_stock_detail_nonexistent_variant(self):
        resp = self.client.get('/api/v1/inventory/99999/')
        self.assertEqual(resp.status_code, 404)

    # ── Пополнение ──

    def test_restock(self):
        resp = self.client.post(
            f'/api/v1/inventory/{self.variant_128.pk}/restock/',
            {'quantity': 50, 'note': 'Приёмка'},
            format='json',
        )
        self.assertEqual(resp.status_code, 201)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, 150)

    def test_restock_invalid_quantity(self):
        resp = self.client.post(
            f'/api/v1/inventory/{self.variant_128.pk}/restock/',
            {'quantity': 0},
            format='json',
        )
        self.assertEqual(resp.status_code, 400)

    # ── Корректировка ──

    def test_adjust(self):
        resp = self.client.post(
            f'/api/v1/inventory/{self.variant_128.pk}/adjust/',
            {'new_quantity': 80, 'note': 'Инвентаризация'},
            format='json',
        )
        self.assertEqual(resp.status_code, 200)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, 80)

    # ── История движений ──

    def test_movements_empty(self):
        resp = self.client.get(
            f'/api/v1/inventory/{self.variant_128.pk}/movements/',
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['count'], 0)
        self.assertEqual(resp.data['results'], [])
        self.assertEqual(resp.data['total_pages'], 0)

    def test_movements_after_restock(self):
        self.client.post(
            f'/api/v1/inventory/{self.variant_128.pk}/restock/',
            {'quantity': 50},
            format='json',
        )
        resp = self.client.get(
            f'/api/v1/inventory/{self.variant_128.pk}/movements/',
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['count'], 1)
        self.assertEqual(len(resp.data['results']), 1)
        self.assertEqual(resp.data['results'][0]['kind'], 'in')
