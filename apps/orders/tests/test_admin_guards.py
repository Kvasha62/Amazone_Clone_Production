"""PROD-004 (F-03) — OrderAdmin must not be a second writer of Order.status.

``Order.status`` is a business state machine owned by ``OrderService``
(``transition_status()`` / ``confirm()`` / ``cancel()``): those entrypoints
hold ``select_for_update`` on the order row, validate
``ORDER_STATUS_TRANSITIONS`` and coordinate inventory
(``_handle_inventory_transition``).

The Admin surface must therefore expose status read-only while keeping the
existing authoritative actions (which call ``OrderService``). These tests
cover both layers: generated form/UI metadata and server-side behaviour
(crafted POST, forced ``save_model``, SQL field-set, add path) plus the
retained authoritative action path.
"""

from decimal import Decimal
from unittest import mock

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db import connection
from django.test import RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.exceptions import ValidationError

from apps.orders.admin.order_admin import (
    ORDER_ADMIN_PROTECTED_FIELDS,
    OrderAdmin,
)
from apps.orders.models import Order
from apps.orders.models.order import OrderStatus
from apps.orders.services.order_service import OrderService
from apps.orders.tests.factories import create_test_order, create_test_user

User = get_user_model()


class OrderAdminGuardTestCase(TestCase):
    """Shared fixtures: staff user, PENDING order, Admin instance."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            username='orderadmin',
            email='orderadmin@test.com',
            password='admin123!',
            is_staff=True,
            is_superuser=True,
        )
        cls.buyer = create_test_user()
        cls.order = create_test_order(cls.buyer)

    def setUp(self):
        self.site = AdminSite()
        self.admin = OrderAdmin(Order, self.site)
        self.factory = RequestFactory()
        self.request = self.factory.get('/admin/orders/order/')
        self.request.user = self.staff

    def _change_form_data(self, order, **overrides):
        """Valid OrderAdmin change POST (status is not a form field)."""
        data = {
            # Единственные редактируемые поля OrderAdmin после PROD-004.
            'notes': order.notes,
            'cancellation_reason': order.cancellation_reason,
            # Inline-формсет позиций (read-only, extra=0, max_num=0).
            'items-TOTAL_FORMS': str(order.items.count()),
            'items-INITIAL_FORMS': str(order.items.count()),
            'items-MIN_NUM_FORMS': '0',
            'items-MAX_NUM_FORMS': '0',
        }
        data.update(overrides)
        return data


class OrderAdminStatusReadOnlyTests(OrderAdminGuardTestCase):
    """Layer 1 — status is not an OrderAdmin input."""

    def test_status_is_declared_protected_and_readonly(self):
        self.assertEqual(('status',), ORDER_ADMIN_PROTECTED_FIELDS)
        self.assertIn('status', self.admin.readonly_fields)
        self.assertEqual(
            ('status',), self.admin.protected_fields,
        )

    def test_get_readonly_fields_always_contains_status(self):
        """Protected field survives even if readonly_fields is edited."""
        admin = OrderAdmin(Order, self.site)
        admin.readonly_fields = ()
        self.assertIn(
            'status', admin.get_readonly_fields(self.request, obj=self.order),
        )

    def test_change_form_has_no_status_input(self):
        form_class = self.admin.get_form(
            self.request, obj=self.order, change=True,
        )
        self.assertNotIn('status', form_class(instance=self.order).fields)

    def test_add_form_has_no_status_input(self):
        form_class = self.admin.get_form(self.request, obj=None, change=False)
        self.assertNotIn('status', form_class().fields)

    def test_change_page_renders_status_without_input(self):
        self.client.force_login(self.staff)
        response = self.client.get(
            f'/admin/orders/order/{self.order.pk}/change/',
        )
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        # Статус виден (инспекция сохранена) — readonly-представление ...
        self.assertIn('field-status', content)
        self.assertIn(OrderStatus.PENDING.label, content)
        # ... но поля ввода для него нет.
        self.assertNotIn('name="status"', content)

    def test_status_filtering_is_preserved(self):
        """Admin остаётся полезным: фильтр по статусу не удалён."""
        self.assertIn('status', self.admin.list_filter)
        self.client.force_login(self.staff)
        response = self.client.get(
            f'/admin/orders/order/?status={OrderStatus.PENDING}',
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.order.order_number)


class OrderAdminStatusGuardTests(OrderAdminGuardTestCase):
    """Layer 2 — crafted POST / forced save cannot move the status."""

    def test_crafted_change_post_cannot_mutate_status(self):
        self.client.force_login(self.staff)
        data = self._change_form_data(
            self.order,
            notes='PROD-004 crafted POST',
            # Подделанное бизнес-поле — должно быть проигнорировано.
            status=OrderStatus.CANCELLED,
        )

        response = self.client.post(
            f'/admin/orders/order/{self.order.pk}/change/', data,
        )

        self.assertEqual(response.status_code, 302)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, OrderStatus.PENDING)
        # Обычное редактирование при этом работает.
        self.assertEqual(self.order.notes, 'PROD-004 crafted POST')

    def test_crafted_change_post_cannot_set_terminal_status_timestamps(self):
        self.client.force_login(self.staff)
        data = self._change_form_data(
            self.order, status=OrderStatus.DELIVERED,
        )

        response = self.client.post(
            f'/admin/orders/order/{self.order.pk}/change/', data,
        )

        self.assertEqual(response.status_code, 302)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, OrderStatus.PENDING)
        self.assertIsNone(self.order.delivered_at)

    def test_save_model_rejects_status_change(self):
        self.order.status = OrderStatus.CANCELLED
        with self.assertRaises(PermissionDenied):
            self.admin.save_model(
                self.request, self.order, form=None, change=True,
            )
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, OrderStatus.PENDING)

    def test_save_model_update_sql_excludes_status(self):
        """Защищённая колонка вообще не попадает в UPDATE."""
        self.order.notes = 'PROD-004 SQL field-set check'

        with CaptureQueriesContext(connection) as captured:
            self.admin.save_model(
                self.request, self.order, form=None, change=True,
            )

        order_updates = [
            query['sql']
            for query in captured.captured_queries
            if 'UPDATE "orders_order"' in query['sql']
        ]
        self.assertTrue(order_updates)
        update_sql = '\n'.join(order_updates)
        self.assertIn('"notes"', update_sql)
        self.assertIn('"updated_at"', update_sql)
        for field in ORDER_ADMIN_PROTECTED_FIELDS:
            self.assertNotIn(f'"{field}"', update_sql)

    def test_save_model_allows_safe_order_edit(self):
        self.order.notes = 'Менеджер согласовал доставку'
        self.order.cancellation_reason = ''

        self.admin.save_model(
            self.request, self.order, form=None, change=True,
        )

        self.order.refresh_from_db()
        self.assertEqual(self.order.notes, 'Менеджер согласовал доставку')
        self.assertEqual(self.order.status, OrderStatus.PENDING)

    def test_save_model_change_without_pk_is_rejected(self):
        """Change path не должен вырождаться в full-row insert."""
        unsaved = Order(
            user=self.buyer,
            status=OrderStatus.PENDING,
            notes='PROD-004 unsaved change path',
        )

        with CaptureQueriesContext(connection) as captured:
            with self.assertRaises(PermissionDenied):
                self.admin.save_model(
                    self.request, unsaved, form=None, change=True,
                )

        mutations = [
            query['sql']
            for query in captured.captured_queries
            if 'INSERT INTO "orders_order"' in query['sql']
        ]
        self.assertEqual(mutations, [])
        self.assertFalse(
            Order.objects.filter(
                notes='PROD-004 unsaved change path',
            ).exists(),
        )

    def test_save_model_add_rejects_preset_status(self):
        """Новый заказ нельзя создать сразу «подтверждённым»."""
        preset = Order(
            user=self.buyer,
            status=OrderStatus.CONFIRMED,
            subtotal=Decimal('0.00'),
            total=Decimal('0.00'),
            notes='PROD-004 preset status',
        )
        with self.assertRaises(PermissionDenied):
            self.admin.save_model(
                self.request, preset, form=None, change=False,
            )
        self.assertFalse(
            Order.objects.filter(notes='PROD-004 preset status').exists(),
        )

    def test_save_model_add_allows_default_status(self):
        """Add path остаётся рабочим для нейтрального состояния."""
        order = Order(
            user=self.buyer,
            subtotal=Decimal('0.00'),
            total=Decimal('0.00'),
            notes='PROD-004 default status',
        )
        self.admin.save_model(self.request, order, form=None, change=False)

        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.PENDING)
        self.assertTrue(order.order_number)


class OrderAdminAuthoritativeActionTests(OrderAdminGuardTestCase):
    """The retained Admin actions go through OrderService, not the field."""

    def test_confirm_selected_uses_order_service(self):
        self.client.force_login(self.staff)

        response = self.client.post(
            '/admin/orders/order/',
            {
                'action': 'confirm_selected',
                'select_across': '0',
                'index': '0',
                '_selected_action': [str(self.order.pk)],
            },
        )

        self.assertEqual(response.status_code, 302)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, OrderStatus.CONFIRMED)
        self.assertIsNotNone(self.order.confirmed_at)

    def test_cancel_selected_uses_order_service(self):
        self.client.force_login(self.staff)

        response = self.client.post(
            '/admin/orders/order/',
            {
                'action': 'cancel_selected',
                'select_across': '0',
                'index': '0',
                '_selected_action': [str(self.order.pk)],
            },
        )

        self.assertEqual(response.status_code, 302)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, OrderStatus.CANCELLED)
        # Валидная доменная причина (CANCELLATION_REASONS), а не произвольная
        # строка: раньше действие передавало 'cancelled_by_admin' и молча
        # отменяло 0 заказов.
        self.assertEqual(self.order.cancellation_reason, 'other')
        self.assertIsNotNone(self.order.cancelled_at)

    def test_service_path_still_moves_status_outside_admin(self):
        """Read-only Admin не «замораживает» авторитетный путь."""
        confirmed = OrderService.confirm(self.order, user=self.staff)
        self.assertEqual(confirmed.status, OrderStatus.CONFIRMED)

        transitioned = OrderService.transition_status(
            self.order, OrderStatus.PROCESSING, user=self.staff,
        )
        self.assertEqual(transitioned.status, OrderStatus.PROCESSING)

    def test_confirm_selected_handles_domain_validation_error(self):
        """Expected domain errors keep the admin batch action alive."""
        with mock.patch(
            'apps.orders.services.order_service.OrderService.confirm',
            side_effect=ValidationError({'detail': 'cannot confirm'}),
        ):
            self.admin.confirm_selected(
                self.request,
                Order.objects.filter(pk=self.order.pk),
            )
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, OrderStatus.PENDING)

    def test_confirm_selected_propagates_unexpected_service_error(self):
        """Unexpected service failures are not silently converted to success."""
        with mock.patch(
            'apps.orders.services.order_service.OrderService.confirm',
            side_effect=RuntimeError('boom'),
        ):
            with self.assertRaises(RuntimeError):
                self.admin.confirm_selected(
                    self.request,
                    Order.objects.filter(pk=self.order.pk),
                )

    def test_cancel_selected_handles_domain_validation_error(self):
        """Expected domain errors keep the admin batch action alive."""
        with mock.patch(
            'apps.orders.services.order_service.OrderService.cancel',
            side_effect=ValidationError({'detail': 'cannot cancel'}),
        ):
            self.admin.cancel_selected(
                self.request,
                Order.objects.filter(pk=self.order.pk),
            )
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, OrderStatus.PENDING)

    def test_cancel_selected_propagates_unexpected_service_error(self):
        """Unexpected service failures are not silently converted to success."""
        with mock.patch(
            'apps.orders.services.order_service.OrderService.cancel',
            side_effect=RuntimeError('boom'),
        ):
            with self.assertRaises(RuntimeError):
                self.admin.cancel_selected(
                    self.request,
                    Order.objects.filter(pk=self.order.pk),
                )
