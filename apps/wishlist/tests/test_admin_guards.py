"""PROD-023 (F-23) — WishlistAdmin must not be a second writer of items_count.

``Wishlist.items_count`` is a denormalized counter owned by the wishlist
domain: ``WishlistService.add_item()`` increments it atomically
(``F('items_count') + 1``), ``remove_item()`` / ``move_to_cart()`` decrement
it with ``Greatest(..., 0)``, and ``clear()`` resets it to 0. A direct Admin
edit desynchronizes the counter from the actual ``WishlistItem`` rows.

The ``user`` FK stays editable. The tests cover the UI/form layer, the
server-side ``save_model`` layer, the add path, remaining allowed Admin
fields, and the authoritative service path.
"""

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db import connection
from django.test import RequestFactory
from django.test.utils import CaptureQueriesContext

from apps.catalog.tests.factories import CatalogTestCase
from apps.orders.tests.factories import create_test_user
from apps.wishlist.admin.wishlist_admin import (
    WISHLIST_ADMIN_PROTECTED_FIELDS,
    WishlistAdmin,
)
from apps.wishlist.models import Wishlist, WishlistItem
from apps.wishlist.services.wishlist_service import WishlistService
from apps.wishlist.tests.factories import create_test_wishlist

User = get_user_model()


class WishlistAdminGuardTestCase(CatalogTestCase):
    """Shared fixtures: staff user, wishlist owner, wishlist with a known count."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.staff = User.objects.create_user(
            username='wishlistadmin',
            email='wishlistadmin@test.com',
            password='admin123!',
            is_staff=True,
            is_superuser=True,
        )
        cls.owner = create_test_user()
        cls.wishlist = create_test_wishlist(cls.owner)
        cls.wishlist.items_count = 3
        cls.wishlist.save(update_fields=['items_count'])

    def setUp(self):
        self.site = AdminSite()
        self.admin = WishlistAdmin(Wishlist, self.site)
        self.factory = RequestFactory()
        self.request = self.factory.get('/admin/wishlist/wishlist/')
        self.request.user = self.staff
        self.wishlist.refresh_from_db()

    def _change_form_data(self, wishlist, **overrides):
        """Valid WishlistAdmin change POST (items_count is not an input)."""
        data = {
            'user': str(wishlist.user_id),
        }
        data.update(overrides)
        return data


class WishlistAdminReadOnlyTests(WishlistAdminGuardTestCase):
    """Layer 1 — items_count is not a WishlistAdmin input."""

    def test_protected_field_is_declared_readonly(self):
        self.assertEqual(('items_count',), WISHLIST_ADMIN_PROTECTED_FIELDS)
        self.assertIn('items_count', self.admin.readonly_fields)

    def test_get_readonly_fields_always_contains_protected_field(self):
        admin = WishlistAdmin(Wishlist, self.site)
        admin.readonly_fields = ()
        self.assertIn(
            'items_count',
            admin.get_readonly_fields(self.request, obj=self.wishlist),
        )

    def test_change_form_has_no_items_count_input(self):
        form_class = self.admin.get_form(
            self.request, obj=self.wishlist, change=True,
        )
        form_fields = form_class(instance=self.wishlist).fields
        self.assertNotIn('items_count', form_fields)
        # Привязка владельца остаётся редактируемой.
        self.assertIn('user', form_fields)

    def test_add_form_has_no_items_count_input(self):
        form_class = self.admin.get_form(self.request, obj=None, change=False)
        self.assertNotIn('items_count', form_class().fields)

    def test_change_page_renders_counter_without_input(self):
        self.client.force_login(self.staff)
        response = self.client.get(
            f'/admin/wishlist/wishlist/{self.wishlist.pk}/change/',
        )
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn('field-items_count', content)
        self.assertNotIn('name="items_count"', content)


class WishlistAdminGuardTests(WishlistAdminGuardTestCase):
    """Layer 2 — crafted POST / forced save cannot move the counter."""

    def test_crafted_change_post_cannot_mutate_items_count(self):
        new_owner = create_test_user()
        self.client.force_login(self.staff)
        data = self._change_form_data(
            self.wishlist,
            # Подделанный бизнес-счётчик + легитимная смена владельца.
            items_count='999',
            user=str(new_owner.pk),
        )

        response = self.client.post(
            f'/admin/wishlist/wishlist/{self.wishlist.pk}/change/', data,
        )

        self.assertEqual(response.status_code, 302)
        self.wishlist.refresh_from_db()
        self.assertEqual(self.wishlist.items_count, 3)
        self.assertEqual(self.wishlist.user_id, new_owner.pk)

    def test_crafted_add_post_cannot_preset_items_count(self):
        self.client.force_login(self.staff)
        owner = create_test_user()
        data = {
            'user': str(owner.pk),
            'items_count': '500',
        }

        response = self.client.post('/admin/wishlist/wishlist/add/', data)

        self.assertEqual(response.status_code, 302)
        forged = Wishlist.objects.get(user=owner)
        self.assertEqual(forged.items_count, 0)

    def test_save_model_rejects_items_count_change(self):
        self.wishlist.items_count = 999
        with self.assertRaises(PermissionDenied):
            self.admin.save_model(
                self.request, self.wishlist, form=None, change=True,
            )
        self.wishlist.refresh_from_db()
        self.assertEqual(self.wishlist.items_count, 3)

    def test_save_model_update_sql_excludes_protected_field(self):
        new_owner = create_test_user()
        self.wishlist.user = new_owner

        with CaptureQueriesContext(connection) as captured:
            self.admin.save_model(
                self.request, self.wishlist, form=None, change=True,
            )

        updates = [
            query['sql']
            for query in captured.captured_queries
            if 'UPDATE "wishlist_wishlist"' in query['sql']
        ]
        self.assertTrue(updates)
        update_sql = '\n'.join(updates)
        self.assertIn('"user_id"', update_sql)
        for field in WISHLIST_ADMIN_PROTECTED_FIELDS:
            self.assertNotIn(f'"{field}"', update_sql)

    def test_save_model_allows_user_edit(self):
        new_owner = create_test_user()
        self.wishlist.user = new_owner

        self.admin.save_model(
            self.request, self.wishlist, form=None, change=True,
        )

        self.wishlist.refresh_from_db()
        self.assertEqual(self.wishlist.user_id, new_owner.pk)
        self.assertEqual(self.wishlist.items_count, 3)

    def test_save_model_add_rejects_preset_counter(self):
        owner = create_test_user()
        forged = Wishlist(user=owner, items_count=10)
        with self.assertRaises(PermissionDenied):
            self.admin.save_model(
                self.request, forged, form=None, change=False,
            )
        self.assertFalse(Wishlist.objects.filter(user=owner).exists())


class WishlistAuthoritativePathTests(WishlistAdminGuardTestCase):
    """Read-only Admin must not freeze the wishlist domain logic."""

    def setUp(self):
        super().setUp()
        # Start from a consistent empty counter so service updates
        # can be asserted against actual WishlistItem rows.
        self.wishlist.items_count = 0
        self.wishlist.save(update_fields=['items_count'])

    def test_add_item_increments_counter(self):
        WishlistService.add_item(self.owner, self.variant_128)

        self.wishlist.refresh_from_db()
        self.assertEqual(self.wishlist.items_count, 1)
        self.assertEqual(
            WishlistItem.objects.filter(wishlist=self.wishlist).count(), 1,
        )

    def test_remove_item_decrements_counter(self):
        item = WishlistService.add_item(self.owner, self.variant_128)

        WishlistService.remove_item(self.owner, item.pk)

        self.wishlist.refresh_from_db()
        self.assertEqual(self.wishlist.items_count, 0)
        self.assertEqual(
            WishlistItem.objects.filter(wishlist=self.wishlist).count(), 0,
        )

    def test_clear_resets_counter(self):
        WishlistService.add_item(self.owner, self.variant_128)
        WishlistService.add_item(self.owner, self.variant_256)

        removed = WishlistService.clear(self.owner)

        self.wishlist.refresh_from_db()
        self.assertEqual(removed, 2)
        self.assertEqual(self.wishlist.items_count, 0)
        self.assertEqual(
            WishlistItem.objects.filter(wishlist=self.wishlist).count(), 0,
        )
