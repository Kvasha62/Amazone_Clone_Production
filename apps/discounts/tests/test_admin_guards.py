"""PROD-004 (F-07) — CouponAdmin must not be a second writer of times_used.

``Coupon.times_used`` is a denormalized usage counter owned by the discounts
domain: ``DiscountService.register_usage()`` increments it atomically
(``UPDATE ... WHERE times_used < max_total_uses``) and
``DiscountService.release_usage()`` decrements it while deleting the matching
``CouponUsage`` row. A direct Admin edit desynchronizes the counter from the
usage rows and from ``max_total_uses``.

Coupon configuration (code, discount type/value, limits, period, is_active)
stays editable. The tests cover the UI/form layer, the server-side
``save_model`` layer, the add path, and the authoritative service path.
"""

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db import connection
from django.test import RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.discounts.admin.coupon_admin import (
    COUPON_ADMIN_PROTECTED_FIELDS,
    CouponAdmin,
)
from apps.discounts.models import Coupon, CouponUsage
from apps.discounts.services.discount_service import DiscountService
from apps.discounts.tests.factories import create_test_coupon
from apps.orders.tests.factories import create_test_order, create_test_user

User = get_user_model()


class CouponAdminGuardTestCase(TestCase):
    """Shared fixtures: staff user, coupon, order."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            username='couponadmin',
            email='couponadmin@test.com',
            password='admin123!',
            is_staff=True,
            is_superuser=True,
        )
        cls.buyer = create_test_user()
        cls.coupon = create_test_coupon(code='PROD004', max_total_uses=2)
        cls.order = create_test_order(cls.buyer)

    def setUp(self):
        self.site = AdminSite()
        self.admin = CouponAdmin(Coupon, self.site)
        self.factory = RequestFactory()
        self.request = self.factory.get('/admin/discounts/coupon/')
        self.request.user = self.staff

    def _change_form_data(self, coupon, **overrides):
        """Valid CouponAdmin change POST (times_used is not an input)."""
        data = {
            'code': coupon.code,
            'description': coupon.description,
            'discount_type': coupon.discount_type,
            'discount_value': str(coupon.discount_value),
            'max_discount': (
                str(coupon.max_discount)
                if coupon.max_discount is not None else ''
            ),
            'min_order_amount': str(coupon.min_order_amount),
            'max_total_uses': str(coupon.max_total_uses),
            'max_uses_per_user': str(coupon.max_uses_per_user),
            'started_at_0': timezone.localtime(coupon.started_at)
            .strftime('%Y-%m-%d'),
            'started_at_1': timezone.localtime(coupon.started_at)
            .strftime('%H:%M:%S'),
            'ended_at_0': timezone.localtime(coupon.ended_at)
            .strftime('%Y-%m-%d'),
            'ended_at_1': timezone.localtime(coupon.ended_at)
            .strftime('%H:%M:%S'),
            'campaign': coupon.campaign_id or '',
            'is_active': 'on' if coupon.is_active else '',
        }
        data.update(overrides)
        return data


class CouponAdminReadOnlyTests(CouponAdminGuardTestCase):
    """Layer 1 — times_used is not a CouponAdmin input."""

    def test_protected_field_is_declared_readonly(self):
        self.assertEqual(('times_used',), COUPON_ADMIN_PROTECTED_FIELDS)
        self.assertIn('times_used', self.admin.readonly_fields)

    def test_get_readonly_fields_always_contains_protected_field(self):
        admin = CouponAdmin(Coupon, self.site)
        admin.readonly_fields = ()
        self.assertIn(
            'times_used',
            admin.get_readonly_fields(self.request, obj=self.coupon),
        )

    def test_change_form_has_no_times_used_input(self):
        form_class = self.admin.get_form(
            self.request, obj=self.coupon, change=True,
        )
        form_fields = form_class(instance=self.coupon).fields
        self.assertNotIn('times_used', form_fields)
        # Конфигурация купона остаётся редактируемой.
        self.assertIn('max_total_uses', form_fields)
        self.assertIn('is_active', form_fields)

    def test_add_form_has_no_times_used_input(self):
        form_class = self.admin.get_form(self.request, obj=None, change=False)
        self.assertNotIn('times_used', form_class().fields)

    def test_change_page_renders_counter_without_input(self):
        self.client.force_login(self.staff)
        response = self.client.get(
            f'/admin/discounts/coupon/{self.coupon.pk}/change/',
        )
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn('field-times_used', content)
        self.assertNotIn('name="times_used"', content)


class CouponAdminGuardTests(CouponAdminGuardTestCase):
    """Layer 2 — crafted POST / forced save cannot move the counter."""

    def test_crafted_change_post_cannot_mutate_times_used(self):
        self.client.force_login(self.staff)
        data = self._change_form_data(
            self.coupon,
            # Подделанный бизнес-счётчик + легитимная конфигурация.
            times_used='999',
            max_total_uses='5',
        )

        response = self.client.post(
            f'/admin/discounts/coupon/{self.coupon.pk}/change/', data,
        )

        self.assertEqual(response.status_code, 302)
        self.coupon.refresh_from_db()
        self.assertEqual(self.coupon.times_used, 0)
        self.assertEqual(self.coupon.max_total_uses, 5)

    def test_crafted_add_post_cannot_preset_times_used(self):
        self.client.force_login(self.staff)
        data = self._change_form_data(
            self.coupon,
            code='PROD004FORGED',
            times_used='500',
        )

        response = self.client.post('/admin/discounts/coupon/add/', data)

        self.assertEqual(response.status_code, 302)
        forged = Coupon.objects.get(code='PROD004FORGED')
        self.assertEqual(forged.times_used, 0)

    def test_save_model_rejects_times_used_change(self):
        self.coupon.times_used = 999
        with self.assertRaises(PermissionDenied):
            self.admin.save_model(
                self.request, self.coupon, form=None, change=True,
            )
        self.coupon.refresh_from_db()
        self.assertEqual(self.coupon.times_used, 0)

    def test_save_model_update_sql_excludes_protected_field(self):
        self.coupon.description = 'PROD-004 SQL field-set check'

        with CaptureQueriesContext(connection) as captured:
            self.admin.save_model(
                self.request, self.coupon, form=None, change=True,
            )

        updates = [
            query['sql']
            for query in captured.captured_queries
            if 'UPDATE "discounts_coupon"' in query['sql']
        ]
        self.assertTrue(updates)
        update_sql = '\n'.join(updates)
        self.assertIn('"description"', update_sql)
        for field in COUPON_ADMIN_PROTECTED_FIELDS:
            self.assertNotIn(f'"{field}"', update_sql)

    def test_save_model_allows_configuration_edit(self):
        self.coupon.max_total_uses = 50
        self.coupon.is_active = False

        self.admin.save_model(
            self.request, self.coupon, form=None, change=True,
        )

        self.coupon.refresh_from_db()
        self.assertEqual(self.coupon.max_total_uses, 50)
        self.assertFalse(self.coupon.is_active)
        self.assertEqual(self.coupon.times_used, 0)

    def test_save_model_add_rejects_preset_counter(self):
        forged = Coupon(
            code='PROD004PRESET',
            discount_type=self.coupon.discount_type,
            discount_value=self.coupon.discount_value,
            min_order_amount=self.coupon.min_order_amount,
            started_at=self.coupon.started_at,
            ended_at=self.coupon.ended_at,
            times_used=10,
        )
        with self.assertRaises(PermissionDenied):
            self.admin.save_model(
                self.request, forged, form=None, change=False,
            )
        self.assertFalse(
            Coupon.objects.filter(code='PROD004PRESET').exists(),
        )


class CouponAuthoritativePathTests(CouponAdminGuardTestCase):
    """Read-only Admin must not freeze the discount domain logic."""

    def test_register_usage_increments_counter_with_usage_row(self):
        DiscountService.register_usage(
            self.coupon, user=self.buyer, order=self.order,
        )

        self.coupon.refresh_from_db()
        self.assertEqual(self.coupon.times_used, 1)
        self.assertEqual(
            CouponUsage.objects.filter(coupon=self.coupon).count(), 1,
        )

    def test_release_usage_decrements_counter_with_usage_row(self):
        usage = DiscountService.register_usage(
            self.coupon, user=self.buyer, order=self.order,
        )
        DiscountService.release_usage(usage)

        self.coupon.refresh_from_db()
        self.assertEqual(self.coupon.times_used, 0)
        self.assertEqual(
            CouponUsage.objects.filter(coupon=self.coupon).count(), 0,
        )

    def test_register_usage_respects_max_total_uses(self):
        """Счётчик и лимит остаются согласованными."""
        DiscountService.register_usage(
            self.coupon, user=self.buyer, order=self.order,
        )
        second_order = create_test_order(self.buyer)
        DiscountService.register_usage(
            self.coupon, user=self.buyer, order=second_order,
        )
        third_order = create_test_order(self.buyer)

        self.coupon.refresh_from_db()
        self.assertEqual(self.coupon.times_used, 2)

        with self.assertRaises(ValidationError):
            DiscountService.register_usage(
                self.coupon, user=self.buyer, order=third_order,
            )
        self.coupon.refresh_from_db()
        self.assertEqual(self.coupon.times_used, 2)
