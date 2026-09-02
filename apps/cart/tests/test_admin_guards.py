"""PROD-004 (N-05) — Cart Admin must not be a second writer of cart items.

``CartItem.quantity`` and ``CartItem.variant`` are owned by ``CartService``:
``add_item()`` / ``update_item_quantity()`` validate the per-cart item limit,
variant/product activity and stock availability under ``select_for_update``,
and ``remove_item()`` owns deletion. A direct Admin write bypasses all of it
(e.g. ``quantity=999`` while ``stock=1``).

Both Admin surfaces must be protected: the standalone ``CartItemAdmin`` page
**and** ``CartItemInline`` on the Cart page (a second POST path into the same
business state). The tests cover form/UI metadata, crafted POSTs on both
surfaces, the server-side guards, and the authoritative service path.
"""

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db import connection
from django.test import RequestFactory
from rest_framework.exceptions import ValidationError
from django.test.utils import CaptureQueriesContext

from apps.cart.admin.cart_admin import (
    CART_ITEM_ADMIN_PROTECTED_FIELDS,
    CartAdmin,
    CartItemAdmin,
    CartItemInline,
)
from apps.cart.models import Cart, CartItem
from apps.cart.services.cart_service import CartService
from apps.cart.tests.factories import CartTestCase
from apps.inventory.models import Stock

User = get_user_model()


class UnguardedCartItemInline(CartItemInline):
    """CartItemInline with BOTH readonly layers removed on purpose.

    Имитирует будущую правку, которая снова выставит бизнес-поля в форму
    (``readonly_fields`` / ``get_readonly_fields``). Protected-контракт при
    этом остаётся, поэтому server-side слой обязан остановить запись —
    именно это и проверяют тесты ниже.
    """

    readonly_fields = ()
    fields = ('variant', 'quantity')

    def get_readonly_fields(self, request, obj=None):
        return ()


class CartItemAdminGuardTests(CartTestCase):
    """Standalone /admin/cart/cartitem/ surface."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.staff = User.objects.create_user(
            username='cartadmin',
            email='cartadmin@test.com',
            password='admin123!',
            is_staff=True,
            is_superuser=True,
        )

    def setUp(self):
        super().setUp()
        self._create_stock(self.variant_a, quantity=100)
        self._create_stock(self.variant_b, quantity=100)
        self._create_price(self.variant_a)
        self._create_price(self.variant_b)
        self.cart = Cart.objects.create(user=self.user, is_active=True)
        self.item = CartItem.objects.create(
            cart=self.cart, variant=self.variant_a, quantity=3,
        )
        self.site = AdminSite()
        self.admin = CartItemAdmin(CartItem, self.site)
        self.factory = RequestFactory()
        self.request = self.factory.get('/admin/cart/cartitem/')
        self.request.user = self.staff

    # ── Слой 1: UI / форма ──────────────────────────────────────────

    def test_protected_fields_are_declared_readonly(self):
        self.assertEqual(
            ('variant', 'quantity'), CART_ITEM_ADMIN_PROTECTED_FIELDS,
        )
        for field in CART_ITEM_ADMIN_PROTECTED_FIELDS:
            self.assertIn(field, self.admin.readonly_fields)

    def test_get_readonly_fields_always_contains_protected_fields(self):
        admin = CartItemAdmin(CartItem, self.site)
        admin.readonly_fields = ()
        readonly = admin.get_readonly_fields(self.request, obj=self.item)
        for field in CART_ITEM_ADMIN_PROTECTED_FIELDS:
            self.assertIn(field, readonly)

    def test_change_form_has_no_business_inputs(self):
        form_class = self.admin.get_form(
            self.request, obj=self.item, change=True,
        )
        form_fields = form_class(instance=self.item).fields
        for field in CART_ITEM_ADMIN_PROTECTED_FIELDS:
            self.assertNotIn(field, form_fields)

    def test_add_form_has_no_business_inputs(self):
        form_class = self.admin.get_form(self.request, obj=None, change=False)
        form_fields = form_class().fields
        for field in CART_ITEM_ADMIN_PROTECTED_FIELDS:
            self.assertNotIn(field, form_fields)

    def test_change_page_renders_item_without_business_inputs(self):
        self.client.force_login(self.staff)
        response = self.client.get(
            f'/admin/cart/cartitem/{self.item.pk}/change/',
        )
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn('field-quantity', content)
        for field in CART_ITEM_ADMIN_PROTECTED_FIELDS:
            self.assertNotIn(f'name="{field}"', content)

    def test_admin_add_is_not_offered(self):
        self.assertFalse(self.admin.has_add_permission(self.request))
        self.client.force_login(self.staff)
        self.assertEqual(
            self.client.get('/admin/cart/cartitem/add/').status_code, 403,
        )

    def test_change_list_still_inspectable(self):
        self.client.force_login(self.staff)
        response = self.client.get('/admin/cart/cartitem/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'SKU-A')

    # ── Слой 2: server-side ─────────────────────────────────────────

    def test_crafted_change_post_cannot_mutate_business_fields(self):
        self.client.force_login(self.staff)

        response = self.client.post(
            f'/admin/cart/cartitem/{self.item.pk}/change/',
            {
                'cart': str(self.cart.pk),
                # Подделанные бизнес-поля.
                'variant': str(self.variant_b.pk),
                'quantity': '999',
            },
        )

        self.assertEqual(response.status_code, 302)
        self.item.refresh_from_db()
        self.assertEqual(self.item.quantity, 3)
        self.assertEqual(self.item.variant_id, self.variant_a.pk)

    def test_save_model_rejects_quantity_change(self):
        self.item.quantity = 999
        with self.assertRaises(PermissionDenied):
            self.admin.save_model(
                self.request, self.item, form=None, change=True,
            )
        self.item.refresh_from_db()
        self.assertEqual(self.item.quantity, 3)

    def test_save_model_rejects_variant_swap(self):
        self.item.variant = self.variant_b
        with self.assertRaises(PermissionDenied):
            self.admin.save_model(
                self.request, self.item, form=None, change=True,
            )
        self.item.refresh_from_db()
        self.assertEqual(self.item.variant_id, self.variant_a.pk)

    def test_save_model_update_sql_excludes_protected_fields(self):
        self.item.cart = self.cart  # без изменений, проверяем набор колонок

        with CaptureQueriesContext(connection) as captured:
            self.admin.save_model(
                self.request, self.item, form=None, change=True,
            )

        updates = [
            query['sql']
            for query in captured.captured_queries
            if 'UPDATE "cart_cartitem"' in query['sql']
        ]
        self.assertTrue(updates)
        update_sql = '\n'.join(updates)
        self.assertIn('"updated_at"', update_sql)
        for field in CART_ITEM_ADMIN_PROTECTED_FIELDS:
            self.assertNotIn(f'"{field}"', update_sql)

    def test_save_model_add_rejects_preset_quantity(self):
        forged = CartItem(
            cart=self.cart, variant=self.variant_b, quantity=500,
        )
        with self.assertRaises(PermissionDenied):
            self.admin.save_model(
                self.request, forged, form=None, change=False,
            )
        self.assertFalse(
            CartItem.objects.filter(variant=self.variant_b).exists(),
        )


class CartItemInlineGuardTests(CartTestCase):
    """/admin/cart/cart/ surface — the inline POST path."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.staff = User.objects.create_user(
            username='cartinlineadmin',
            email='cartinlineadmin@test.com',
            password='admin123!',
            is_staff=True,
            is_superuser=True,
        )

    def setUp(self):
        super().setUp()
        self._create_stock(self.variant_a, quantity=100)
        self._create_stock(self.variant_b, quantity=100)
        self._create_price(self.variant_a)
        self._create_price(self.variant_b)
        self.cart = Cart.objects.create(user=self.user, is_active=True)
        self.item = CartItem.objects.create(
            cart=self.cart, variant=self.variant_a, quantity=3,
        )
        self.site = AdminSite()
        self.cart_admin = CartAdmin(Cart, self.site)
        self.inline = CartItemInline(Cart, self.site)
        self.factory = RequestFactory()
        self.request = self.factory.get('/admin/cart/cart/')
        self.request.user = self.staff

    def _cart_form_data(self, **overrides):
        """Valid CartAdmin change POST + inline management form."""
        data = {
            'user': str(self.cart.user_id),
            'is_active': 'on' if self.cart.is_active else '',
            'items-TOTAL_FORMS': '1',
            'items-INITIAL_FORMS': '1',
            'items-MIN_NUM_FORMS': '0',
            'items-MAX_NUM_FORMS': '1000',
            'items-0-id': str(self.item.pk),
            'items-0-cart': str(self.cart.pk),
        }
        data.update(overrides)
        return data

    # ── Слой 1: UI / форма ──────────────────────────────────────────

    def test_inline_protected_fields_are_declared_readonly(self):
        self.assertEqual(
            ('variant', 'quantity'), self.inline.protected_fields,
        )
        for field in CART_ITEM_ADMIN_PROTECTED_FIELDS:
            self.assertIn(field, self.inline.readonly_fields)

    def test_inline_get_readonly_fields_always_contains_protected_fields(self):
        inline = CartItemInline(Cart, self.site)
        inline.readonly_fields = ()
        readonly = inline.get_readonly_fields(self.request, obj=self.cart)
        for field in CART_ITEM_ADMIN_PROTECTED_FIELDS:
            self.assertIn(field, readonly)

    def test_inline_change_page_has_no_business_inputs(self):
        self.client.force_login(self.staff)
        response = self.client.get(
            f'/admin/cart/cart/{self.cart.pk}/change/',
        )
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        for field in CART_ITEM_ADMIN_PROTECTED_FIELDS:
            self.assertNotIn(f'name="items-0-{field}"', content)
            self.assertNotIn(f'name="items-__prefix__-{field}"', content)

    def test_inline_does_not_offer_new_rows(self):
        self.assertFalse(
            self.inline.has_add_permission(self.request, obj=self.cart),
        )

    # ── Слой 2: server-side ─────────────────────────────────────────

    def test_crafted_inline_post_cannot_mutate_business_fields(self):
        self.client.force_login(self.staff)

        response = self.client.post(
            f'/admin/cart/cart/{self.cart.pk}/change/',
            self._cart_form_data(**{
                'items-0-variant': str(self.variant_b.pk),
                'items-0-quantity': '999',
            }),
        )

        self.assertEqual(response.status_code, 302)
        self.item.refresh_from_db()
        self.assertEqual(self.item.quantity, 3)
        self.assertEqual(self.item.variant_id, self.variant_a.pk)

    def test_crafted_inline_post_cannot_add_new_item(self):
        self.client.force_login(self.staff)
        items_before = CartItem.objects.count()

        self.client.post(
            f'/admin/cart/cart/{self.cart.pk}/change/',
            self._cart_form_data(**{
                'items-TOTAL_FORMS': '2',
                'items-1-id': '',
                'items-1-cart': str(self.cart.pk),
                'items-1-variant': str(self.variant_b.pk),
                'items-1-quantity': '5',
            }),
        )

        self.assertEqual(CartItem.objects.count(), items_before)
        self.assertFalse(
            CartItem.objects.filter(variant=self.variant_b).exists(),
        )

    def test_cart_save_still_works_after_inline_guard(self):
        """Легитимное редактирование корзины не сломано защитой."""
        self.client.force_login(self.staff)

        response = self.client.post(
            f'/admin/cart/cart/{self.cart.pk}/change/',
            self._cart_form_data(is_active=''),
        )

        self.assertEqual(response.status_code, 302)
        self.cart.refresh_from_db()
        self.assertFalse(self.cart.is_active)
        self.item.refresh_from_db()
        self.assertEqual(self.item.quantity, 3)

    def test_inline_guard_rejects_protected_change_even_without_readonly(self):
        """Server-side слой работает и без UI-слоя (defense-in-depth)."""
        inline = UnguardedCartItemInline(Cart, self.site)
        formset_class = inline.get_formset(self.request, obj=self.cart)
        formset = formset_class(
            data={
                'items-TOTAL_FORMS': '1',
                'items-INITIAL_FORMS': '1',
                'items-MIN_NUM_FORMS': '0',
                'items-MAX_NUM_FORMS': '1000',
                'items-0-id': str(self.item.pk),
                'items-0-cart': str(self.cart.pk),
                'items-0-variant': str(self.variant_b.pk),
                'items-0-quantity': '999',
            },
            instance=self.cart,
        )
        self.assertTrue(formset.is_valid(), formset.errors)

        with self.assertRaises(PermissionDenied):
            inline.assert_formset_protected(formset)

        self.item.refresh_from_db()
        self.assertEqual(self.item.quantity, 3)
        self.assertEqual(self.item.variant_id, self.variant_a.pk)

    def test_cart_admin_save_formset_rejects_protected_inline_change(self):
        """Хук CartAdmin.save_formset() вызывает guard до записи."""
        inline = UnguardedCartItemInline(Cart, self.site)
        formset_class = inline.get_formset(self.request, obj=self.cart)
        formset = formset_class(
            data={
                'items-TOTAL_FORMS': '1',
                'items-INITIAL_FORMS': '1',
                'items-MIN_NUM_FORMS': '0',
                'items-MAX_NUM_FORMS': '1000',
                'items-0-id': str(self.item.pk),
                'items-0-cart': str(self.cart.pk),
                'items-0-variant': str(self.variant_a.pk),
                'items-0-quantity': '42',
            },
            instance=self.cart,
        )
        self.assertTrue(formset.is_valid(), formset.errors)

        with CaptureQueriesContext(connection) as captured:
            with self.assertRaises(PermissionDenied):
                self.cart_admin.save_formset(
                    self.request, None, formset, change=True,
                )

        cartitem_updates = [
            query['sql']
            for query in captured.captured_queries
            if 'UPDATE "cart_cartitem"' in query['sql']
            or 'INSERT INTO "cart_cartitem"' in query['sql']
        ]
        self.assertEqual(cartitem_updates, [])
        self.item.refresh_from_db()
        self.assertEqual(self.item.quantity, 3)

    def test_inline_guard_allows_untouched_formset(self):
        inline = CartItemInline(Cart, self.site)
        formset_class = inline.get_formset(self.request, obj=self.cart)
        formset = formset_class(
            data=self._cart_form_data(), instance=self.cart,
        )
        self.assertTrue(formset.is_valid(), formset.errors)
        # Не бросает: защищённые поля не изменились.
        inline.assert_formset_protected(formset)


class CartAuthoritativePathTests(CartTestCase):
    """Read-only Admin must not freeze CartService."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.staff = User.objects.create_user(
            username='cartpathadmin',
            email='cartpathadmin@test.com',
            password='admin123!',
            is_staff=True,
            is_superuser=True,
        )

    def setUp(self):
        super().setUp()
        self._create_stock(self.variant_a, quantity=10)
        self._create_stock(self.variant_b, quantity=10)
        self._create_price(self.variant_a)
        self._create_price(self.variant_b)
        self.cart = Cart.objects.create(user=self.user, is_active=True)
        self.site = AdminSite()
        self.cart_admin = CartAdmin(Cart, self.site)
        self.factory = RequestFactory()
        self.request = self.factory.get('/admin/cart/cart/')
        self.request.user = self.staff

    def test_add_item_still_creates_position(self):
        item = CartService.add_item(self.cart, self.variant_a.pk, 2)
        self.assertEqual(item.quantity, 2)

    def test_update_item_quantity_still_moves_quantity(self):
        item = CartService.add_item(self.cart, self.variant_a.pk, 2)
        updated = CartService.update_item_quantity(self.cart, item.pk, 7)
        self.assertEqual(updated.quantity, 7)

    def test_update_item_quantity_still_validates_stock(self):
        item = CartService.add_item(self.cart, self.variant_a.pk, 2)
        with self.assertRaises(ValidationError):
            CartService.update_item_quantity(self.cart, item.pk, 9999)
        item.refresh_from_db()
        self.assertEqual(item.quantity, 2)

    def test_remove_item_still_deletes_position(self):
        item = CartService.add_item(self.cart, self.variant_a.pk, 2)
        CartService.remove_item(self.cart, item.pk)
        self.assertEqual(CartItem.objects.filter(pk=item.pk).count(), 0)

    def test_existing_cart_admin_action_still_works(self):
        """Легитимная Admin-операция (деактивация корзины) сохранена."""
        self.client.force_login(self.staff)

        response = self.client.post(
            '/admin/cart/cart/',
            {
                'action': 'deactivate_selected',
                'select_across': '0',
                'index': '0',
                '_selected_action': [str(self.cart.pk)],
            },
        )

        self.assertEqual(response.status_code, 302)
        self.cart.refresh_from_db()
        self.assertFalse(self.cart.is_active)
        self.assertEqual(
            Stock.objects.get(variant=self.variant_a).quantity, 10,
        )
