"""PROD-004 (F-06) — ShipmentAdmin must not be a second writer of lifecycle.

``Shipment.status`` is an FSM owned by ``ShippingService.transition_status()``:
it locks the row, validates ``SHIPMENT_STATUS_TRANSITIONS``, sets the
transition timestamps (``shipped_at`` / ``delivered_at``) and synchronizes the
order status through ``OrderService``. A direct Admin write of the status (or
of the timestamps the same transition owns) would leave ``Order`` and
``Shipment`` out of sync.

Tracking number / notes / weight stay administrative data and remain
editable. The tests cover the UI/form layer, the server-side ``save_model``
layer, the add path, and the authoritative service path.
"""

from decimal import Decimal

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db import connection
from django.test import RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.orders.models.order import OrderStatus
from apps.orders.services.order_service import OrderService
from apps.orders.tests.factories import create_test_order, create_test_user
from apps.shipping.admin.shipping_admin import (
    SHIPMENT_ADMIN_PROTECTED_FIELDS,
    ShipmentAdmin,
)
from apps.shipping.constants import SHIPMENT_IN_TRANSIT, SHIPMENT_PREPARING
from apps.shipping.models import Shipment
from apps.shipping.services.shipping_service import ShippingService
from apps.shipping.tests.factories import create_test_method, create_test_shipment

User = get_user_model()


class ShipmentAdminGuardTestCase(TestCase):
    """Shared fixtures: staff user, order, shipment."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            username='shipadmin',
            email='shipadmin@test.com',
            password='admin123!',
            is_staff=True,
            is_superuser=True,
        )
        cls.buyer = create_test_user()
        cls.order = create_test_order(cls.buyer)
        cls.method = create_test_method()
        cls.shipment = create_test_shipment(
            cls.order, cls.method, user=cls.buyer,
        )

    def setUp(self):
        self.site = AdminSite()
        self.admin = ShipmentAdmin(Shipment, self.site)
        self.factory = RequestFactory()
        self.request = self.factory.get('/admin/shipping/shipment/')
        self.request.user = self.staff

    def _change_form_data(self, shipment, **overrides):
        """Valid ShipmentAdmin change POST (lifecycle fields are not inputs)."""
        data = {
            'order': str(shipment.order_id),
            'user': str(shipment.user_id),
            'method': str(shipment.method_id),
            'tracking_number': shipment.tracking_number,
            'shipping_cost': str(shipment.shipping_cost),
            'weight_kg': (
                str(shipment.weight_kg) if shipment.weight_kg is not None else ''
            ),
            'notes': shipment.notes,
        }
        data.update(overrides)
        return data


class ShipmentAdminReadOnlyTests(ShipmentAdminGuardTestCase):
    """Layer 1 — lifecycle fields are not ShipmentAdmin inputs."""

    def test_protected_fields_are_declared_readonly(self):
        self.assertEqual(
            ('status', 'shipped_at', 'delivered_at'),
            SHIPMENT_ADMIN_PROTECTED_FIELDS,
        )
        for field in SHIPMENT_ADMIN_PROTECTED_FIELDS:
            self.assertIn(field, self.admin.readonly_fields)

    def test_get_readonly_fields_always_contains_protected_fields(self):
        admin = ShipmentAdmin(Shipment, self.site)
        admin.readonly_fields = ()
        readonly = admin.get_readonly_fields(self.request, obj=self.shipment)
        for field in SHIPMENT_ADMIN_PROTECTED_FIELDS:
            self.assertIn(field, readonly)

    def test_change_form_has_no_lifecycle_inputs(self):
        form_class = self.admin.get_form(
            self.request, obj=self.shipment, change=True,
        )
        form_fields = form_class(instance=self.shipment).fields
        for field in SHIPMENT_ADMIN_PROTECTED_FIELDS:
            self.assertNotIn(field, form_fields)
        # Административные поля остаются редактируемыми.
        self.assertIn('tracking_number', form_fields)
        self.assertIn('notes', form_fields)

    def test_change_page_renders_status_without_input(self):
        self.client.force_login(self.staff)
        response = self.client.get(
            f'/admin/shipping/shipment/{self.shipment.pk}/change/',
        )
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn('field-status', content)
        for field in SHIPMENT_ADMIN_PROTECTED_FIELDS:
            self.assertNotIn(f'name="{field}"', content)

    def test_status_filtering_is_preserved(self):
        self.assertIn('status', self.admin.list_filter)
        self.client.force_login(self.staff)
        response = self.client.get(
            f'/admin/shipping/shipment/?status={SHIPMENT_PREPARING}',
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.shipment.internal_tracking)


class ShipmentAdminGuardTests(ShipmentAdminGuardTestCase):
    """Layer 2 — crafted POST / forced save cannot move the lifecycle."""

    def test_crafted_change_post_cannot_mutate_status(self):
        self.client.force_login(self.staff)
        data = self._change_form_data(
            self.shipment,
            # Подделанные business-поля.
            status='delivered',
            shipped_at='2020-01-01 00:00:00',
            delivered_at='2020-01-02 00:00:00',
            # Легитимное административное изменение.
            tracking_number='PROD-004-TRACK-1',
        )

        response = self.client.post(
            f'/admin/shipping/shipment/{self.shipment.pk}/change/', data,
        )

        self.assertEqual(response.status_code, 302)
        self.shipment.refresh_from_db()
        self.assertEqual(self.shipment.status, SHIPMENT_PREPARING)
        self.assertIsNone(self.shipment.shipped_at)
        self.assertIsNone(self.shipment.delivered_at)
        self.assertEqual(self.shipment.tracking_number, 'PROD-004-TRACK-1')

    def test_save_model_rejects_status_change(self):
        self.shipment.status = 'delivered'
        with self.assertRaises(PermissionDenied):
            self.admin.save_model(
                self.request, self.shipment, form=None, change=True,
            )
        self.shipment.refresh_from_db()
        self.assertEqual(self.shipment.status, SHIPMENT_PREPARING)

    def test_save_model_rejects_timestamp_forgery(self):
        self.shipment.delivered_at = timezone.now()
        with self.assertRaises(PermissionDenied):
            self.admin.save_model(
                self.request, self.shipment, form=None, change=True,
            )
        self.shipment.refresh_from_db()
        self.assertIsNone(self.shipment.delivered_at)

    def test_save_model_update_sql_excludes_protected_fields(self):
        self.shipment.notes = 'PROD-004 SQL field-set check'

        with CaptureQueriesContext(connection) as captured:
            self.admin.save_model(
                self.request, self.shipment, form=None, change=True,
            )

        updates = [
            query['sql']
            for query in captured.captured_queries
            if 'UPDATE "shipping_shipment"' in query['sql']
        ]
        self.assertTrue(updates)
        update_sql = '\n'.join(updates)
        self.assertIn('"notes"', update_sql)
        for field in SHIPMENT_ADMIN_PROTECTED_FIELDS:
            self.assertNotIn(f'"{field}"', update_sql)

    def test_save_model_allows_administrative_edit(self):
        self.shipment.tracking_number = 'PROD-004-TRACK-2'
        self.shipment.notes = 'Передали в СДЭК'
        self.shipment.weight_kg = Decimal('2.500')

        self.admin.save_model(
            self.request, self.shipment, form=None, change=True,
        )

        self.shipment.refresh_from_db()
        self.assertEqual(self.shipment.tracking_number, 'PROD-004-TRACK-2')
        self.assertEqual(self.shipment.notes, 'Передали в СДЭК')
        self.assertEqual(self.shipment.weight_kg, Decimal('2.500'))
        self.assertEqual(self.shipment.status, SHIPMENT_PREPARING)

    def test_save_model_add_rejects_preset_status(self):
        forged = Shipment(
            order=self.order,
            user=self.buyer,
            method=self.method,
            status='delivered',
            shipping_cost=Decimal('300.00'),
        )
        shipments_before = Shipment.objects.count()
        with self.assertRaises(PermissionDenied):
            self.admin.save_model(
                self.request, forged, form=None, change=False,
            )
        self.assertIsNone(forged.pk)
        self.assertEqual(Shipment.objects.count(), shipments_before)
        self.shipment.refresh_from_db()
        self.assertEqual(self.shipment.status, SHIPMENT_PREPARING)

    def test_save_model_add_allows_default_status(self):
        shipment = Shipment(
            order=self.order,
            user=self.buyer,
            method=self.method,
            shipping_cost=Decimal('300.00'),
        )
        # Существующее отправление занимает OneToOne(order) — удаляем его,
        # чтобы проверить именно add-path guard, а не constraint.
        Shipment.objects.filter(pk=self.shipment.pk).delete()

        self.admin.save_model(self.request, shipment, form=None, change=False)

        shipment.refresh_from_db()
        self.assertEqual(shipment.status, SHIPMENT_PREPARING)
        self.assertTrue(shipment.internal_tracking)


class ShipmentAuthoritativePathTests(ShipmentAdminGuardTestCase):
    """Read-only Admin must not freeze ShippingService."""

    def test_transition_status_still_moves_shipment_and_order(self):
        confirmed = OrderService.confirm(self.order, user=self.staff)
        self.assertEqual(confirmed.status, OrderStatus.CONFIRMED)

        shipment = ShippingService.transition_status(
            self.shipment, SHIPMENT_IN_TRANSIT, tracking_number='TRK-123',
        )

        self.assertEqual(shipment.status, SHIPMENT_IN_TRANSIT)
        self.assertIsNotNone(shipment.shipped_at)
        self.assertEqual(shipment.tracking_number, 'TRK-123')
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, OrderStatus.PROCESSING)

    def test_update_tracking_still_works(self):
        ShippingService.update_tracking(self.shipment, 'TRK-456')
        self.shipment.refresh_from_db()
        self.assertEqual(self.shipment.tracking_number, 'TRK-456')
