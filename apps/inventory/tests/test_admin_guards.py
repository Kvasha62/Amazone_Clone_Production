"""PROD-004 (F-04) — StockAdmin must not be a second writer of stock state.

``Stock.quantity`` / ``Stock.reserved_quantity`` are owned by
``InventoryService``: every mutating entrypoint (``restock``,
``adjust_stock``, ``reserve_stock``, ``release_stock``, ``commit_stock``)
holds ``select_for_update`` and writes a ``StockMovement`` audit row.
``Stock.variant`` identifies whose stock the row is, so re-pointing it from
Admin would move inventory between SKUs without a movement row and without
a lock.

The tests cover the UI/form layer, the server-side ``save_model`` layer, the
inline audit surface on the same Admin page, and the authoritative service
path that must keep working.
"""

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db import connection
from django.test import RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext

from apps.catalog.constants import ProductStatus
from apps.catalog.models import Brand, Category, Product, ProductVariant
from apps.inventory.admin.stock_admin import (
    STOCK_ADMIN_PROTECTED_FIELDS,
    StockAdmin,
)
from apps.inventory.models import Stock, StockMovement
from apps.inventory.services.inventory_service import InventoryService

User = get_user_model()


class StockAdminGuardTestCase(TestCase):
    """Shared fixtures: staff user, product variants, stock row."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            username='stockadmin',
            email='stockadmin@test.com',
            password='admin123!',
            is_staff=True,
            is_superuser=True,
        )
        cls.brand = Brand.objects.create(name='StockGuardBrand')
        cls.category = Category.add_root(name='StockGuardCat')
        cls.product = Product.objects.create(
            name='Stock Guard Product',
            brand=cls.brand,
            primary_category=cls.category,
            status=ProductStatus.ACTIVE,
        )
        cls.variant = ProductVariant.objects.create(
            product=cls.product, sku='STOCK-GUARD-A', is_active=True,
        )
        cls.other_variant = ProductVariant.objects.create(
            product=cls.product, sku='STOCK-GUARD-B', is_active=True,
        )
        cls.stock = Stock.objects.create(
            variant=cls.variant,
            quantity=100,
            reserved_quantity=20,
            low_stock_threshold=5,
        )

    def setUp(self):
        self.site = AdminSite()
        self.admin = StockAdmin(Stock, self.site)
        self.factory = RequestFactory()
        self.request = self.factory.get('/admin/inventory/stock/')
        self.request.user = self.staff

    def _change_form_data(self, stock, **overrides):
        """Valid StockAdmin change POST (business fields are not inputs)."""
        data = {
            'low_stock_threshold': str(stock.low_stock_threshold),
            # Inline-формсет движений (read-only, extra=0, max_num=0).
            'movements-TOTAL_FORMS': '0',
            'movements-INITIAL_FORMS': '0',
            'movements-MIN_NUM_FORMS': '0',
            'movements-MAX_NUM_FORMS': '0',
        }
        data.update(overrides)
        return data


class StockAdminReadOnlyTests(StockAdminGuardTestCase):
    """Layer 1 — business fields are not StockAdmin inputs."""

    def test_protected_fields_are_declared_readonly(self):
        self.assertEqual(
            ('variant', 'quantity', 'reserved_quantity'),
            STOCK_ADMIN_PROTECTED_FIELDS,
        )
        for field in STOCK_ADMIN_PROTECTED_FIELDS:
            self.assertIn(field, self.admin.readonly_fields)

    def test_get_readonly_fields_always_contains_protected_fields(self):
        admin = StockAdmin(Stock, self.site)
        admin.readonly_fields = ()
        readonly = admin.get_readonly_fields(self.request, obj=self.stock)
        for field in STOCK_ADMIN_PROTECTED_FIELDS:
            self.assertIn(field, readonly)

    def test_change_form_has_no_business_inputs(self):
        form_class = self.admin.get_form(
            self.request, obj=self.stock, change=True,
        )
        form_fields = form_class(instance=self.stock).fields
        for field in STOCK_ADMIN_PROTECTED_FIELDS:
            self.assertNotIn(field, form_fields)
        # Операционная настройка остаётся редактируемой.
        self.assertIn('low_stock_threshold', form_fields)

    def test_change_page_renders_quantity_without_input(self):
        self.client.force_login(self.staff)
        response = self.client.get(
            f'/admin/inventory/stock/{self.stock.pk}/change/',
        )
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn('field-quantity', content)
        for field in STOCK_ADMIN_PROTECTED_FIELDS:
            self.assertNotIn(f'name="{field}"', content)

    def test_admin_add_is_not_offered(self):
        """Строки Stock создаёт InventoryService.get_or_create_stock()."""
        self.assertFalse(self.admin.has_add_permission(self.request))
        self.client.force_login(self.staff)
        self.assertEqual(
            self.client.get('/admin/inventory/stock/add/').status_code, 403,
        )


class StockAdminGuardTests(StockAdminGuardTestCase):
    """Layer 2 — crafted POST / forced save cannot move stock counters."""

    def test_crafted_change_post_cannot_mutate_quantity(self):
        self.client.force_login(self.staff)
        data = self._change_form_data(
            self.stock,
            low_stock_threshold='7',
            # Подделанные бизнес-поля.
            quantity='999999',
            reserved_quantity='0',
            variant=str(self.other_variant.pk),
        )

        response = self.client.post(
            f'/admin/inventory/stock/{self.stock.pk}/change/', data,
        )

        self.assertEqual(response.status_code, 302)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, 100)
        self.assertEqual(self.stock.reserved_quantity, 20)
        self.assertEqual(self.stock.variant_id, self.variant.pk)
        # Операционная настройка при этом сохранилась.
        self.assertEqual(self.stock.low_stock_threshold, 7)

    def test_crafted_change_post_writes_no_stock_movement(self):
        """Инлайн на той же странице не даёт добавить аудит-движение."""
        self.client.force_login(self.staff)
        data = self._change_form_data(self.stock)
        data.update({
            'movements-TOTAL_FORMS': '1',
            'movements-INITIAL_FORMS': '0',
            'movements-MAX_NUM_FORMS': '1000',
            'movements-0-kind': 'in',
            'movements-0-delta': '1000',
            'movements-0-quantity_before': '100',
            'movements-0-quantity_after': '1100',
            'movements-0-note': 'PROD-004 forged movement',
        })

        self.client.post(
            f'/admin/inventory/stock/{self.stock.pk}/change/', data,
        )

        self.assertFalse(
            StockMovement.objects.filter(
                note='PROD-004 forged movement',
            ).exists(),
        )
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, 100)

    def test_save_model_rejects_quantity_change(self):
        self.stock.quantity = 42
        with self.assertRaises(PermissionDenied):
            self.admin.save_model(
                self.request, self.stock, form=None, change=True,
            )
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, 100)

    def test_save_model_rejects_reserved_quantity_change(self):
        self.stock.reserved_quantity = 0
        with self.assertRaises(PermissionDenied):
            self.admin.save_model(
                self.request, self.stock, form=None, change=True,
            )
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.reserved_quantity, 20)

    def test_save_model_rejects_variant_repoint(self):
        self.stock.variant = self.other_variant
        with self.assertRaises(PermissionDenied):
            self.admin.save_model(
                self.request, self.stock, form=None, change=True,
            )
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.variant_id, self.variant.pk)

    def test_save_model_update_sql_excludes_protected_fields(self):
        self.stock.low_stock_threshold = 12

        with CaptureQueriesContext(connection) as captured:
            self.admin.save_model(
                self.request, self.stock, form=None, change=True,
            )

        updates = [
            query['sql']
            for query in captured.captured_queries
            if 'UPDATE "inventory_stock"' in query['sql']
        ]
        self.assertTrue(updates)
        update_sql = '\n'.join(updates)
        self.assertIn('"low_stock_threshold"', update_sql)
        self.assertIn('"updated_at"', update_sql)
        for field in STOCK_ADMIN_PROTECTED_FIELDS:
            self.assertNotIn(f'"{field}"', update_sql)

    def test_save_model_allows_operational_edit(self):
        """Admin остаётся полезным: порог «мало товара» редактируется."""
        self.stock.low_stock_threshold = 25
        self.admin.save_model(self.request, self.stock, form=None, change=True)

        self.stock.refresh_from_db()
        self.assertEqual(self.stock.low_stock_threshold, 25)
        self.assertEqual(self.stock.quantity, 100)
        self.assertEqual(self.stock.reserved_quantity, 20)

    def test_save_model_add_rejects_preset_quantity(self):
        """Новый остаток нельзя создать сразу заполненным."""
        forged = Stock(variant=self.other_variant, quantity=500)
        with self.assertRaises(PermissionDenied):
            self.admin.save_model(
                self.request, forged, form=None, change=False,
            )
        self.assertFalse(
            Stock.objects.filter(variant=self.other_variant).exists(),
        )


class StockAuthoritativePathTests(StockAdminGuardTestCase):
    """Read-only Admin must not freeze InventoryService."""

    def test_restock_still_moves_quantity_with_audit(self):
        movement = InventoryService.restock(
            self.variant, 15, performed_by=self.staff, note='PROD-004 restock',
        )

        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, 115)
        self.assertEqual(movement.quantity_before, 100)
        self.assertEqual(movement.quantity_after, 115)
        self.assertTrue(
            StockMovement.objects.filter(
                stock=self.stock, note='PROD-004 restock',
            ).exists(),
        )

    def test_adjust_stock_still_moves_quantity_with_audit(self):
        InventoryService.adjust_stock(
            self.variant, 80, performed_by=self.staff, note='PROD-004 adjust',
        )

        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, 80)
        self.assertTrue(
            StockMovement.objects.filter(
                stock=self.stock, note='PROD-004 adjust',
            ).exists(),
        )

    def test_get_or_create_stock_creates_row_outside_admin(self):
        stock = InventoryService.get_or_create_stock(self.other_variant)
        self.assertEqual(stock.quantity, 0)
        self.assertEqual(stock.reserved_quantity, 0)


# ────────────────────────────────────────────────────────────────────
# PROD-032 / F-25 — Admin deletion guards for Stock and StockMovement.
#
# StockMovement.stock uses on_delete=CASCADE, so deleting a Stock row
# through Admin would cascade-delete its audit history.  Deleting a
# StockMovement directly removes an individual audit record.  Both
# paths are now blocked at the Admin layer.
# ────────────────────────────────────────────────────────────────────

from apps.inventory.admin.stock_admin import StockMovementAdmin


class StockAdminDeleteGuardTests(StockAdminGuardTestCase):
    """PROD-032 / F-25 — Stock deletion is prohibited through Admin."""

    def setUp(self):
        super().setUp()
        self.movement_admin = StockMovementAdmin(StockMovement, self.site)
        # Create a StockMovement to verify cascade is blocked.
        self.movement = StockMovement.objects.create(
            stock=self.stock,
            kind='in',
            delta=50,
            quantity_before=100,
            quantity_after=150,
            note='PROD-032 test movement',
        )

    # ── AC-1: object-level deletion is prohibited ──

    def test_has_delete_permission_is_false(self):
        self.assertFalse(self.admin.has_delete_permission(self.request))
        self.assertFalse(
            self.admin.has_delete_permission(self.request, obj=self.stock),
        )

    def test_delete_model_raises_and_keeps_stock_and_movements(self):
        with self.assertRaises(PermissionDenied):
            self.admin.delete_model(self.request, self.stock)

        self.assertTrue(Stock.objects.filter(pk=self.stock.pk).exists())
        # AC-5: audit history is preserved.
        self.assertTrue(
            StockMovement.objects.filter(pk=self.movement.pk).exists(),
        )

    # ── AC-2: bulk/queryset deletion is prohibited ──

    def test_delete_queryset_raises_and_keeps_stock_and_movements(self):
        qs = Stock.objects.filter(pk=self.stock.pk)
        with self.assertRaises(PermissionDenied):
            self.admin.delete_queryset(self.request, qs)

        self.assertTrue(Stock.objects.filter(pk=self.stock.pk).exists())
        self.assertTrue(
            StockMovement.objects.filter(pk=self.movement.pk).exists(),
        )

    def test_delete_selected_action_is_unavailable(self):
        actions = self.admin.get_actions(self.request)
        self.assertNotIn('delete_selected', actions)

    # ── AC-1/AC-2/AC-5 via HTTP Admin path ──

    def test_admin_delete_view_is_forbidden_and_audit_preserved(self):
        self.client.force_login(self.staff)
        url = f'/admin/inventory/stock/{self.stock.pk}/delete/'

        response = self.client.post(url, {'post': 'yes'})

        self.assertEqual(response.status_code, 403)
        self.assertTrue(Stock.objects.filter(pk=self.stock.pk).exists())
        self.assertTrue(
            StockMovement.objects.filter(pk=self.movement.pk).exists(),
        )


class StockMovementAdminDeleteGuardTests(StockAdminGuardTestCase):
    """PROD-032 / F-25 — StockMovement deletion is prohibited through Admin."""

    def setUp(self):
        super().setUp()
        self.movement_admin = StockMovementAdmin(StockMovement, self.site)
        self.movement = StockMovement.objects.create(
            stock=self.stock,
            kind='in',
            delta=50,
            quantity_before=100,
            quantity_after=150,
            note='PROD-032 movement guard test',
        )

    # ── AC-3: object-level deletion is prohibited ──

    def test_has_delete_permission_is_false(self):
        self.assertFalse(
            self.movement_admin.has_delete_permission(self.request),
        )
        self.assertFalse(
            self.movement_admin.has_delete_permission(
                self.request, obj=self.movement,
            ),
        )

    def test_delete_model_raises_and_keeps_movement(self):
        with self.assertRaises(PermissionDenied):
            self.movement_admin.delete_model(self.request, self.movement)

        # AC-5: the movement itself is preserved.
        self.assertTrue(
            StockMovement.objects.filter(pk=self.movement.pk).exists(),
        )

    # ── AC-4: bulk/queryset deletion is prohibited ──

    def test_delete_queryset_raises_and_keeps_movements(self):
        qs = StockMovement.objects.filter(pk=self.movement.pk)
        with self.assertRaises(PermissionDenied):
            self.movement_admin.delete_queryset(self.request, qs)

        self.assertTrue(
            StockMovement.objects.filter(pk=self.movement.pk).exists(),
        )

    def test_delete_selected_action_is_unavailable(self):
        actions = self.movement_admin.get_actions(self.request)
        self.assertNotIn('delete_selected', actions)

    # ── AC-3/AC-4/AC-5 via HTTP Admin path ──

    def test_admin_delete_view_is_forbidden_and_movement_preserved(self):
        self.client.force_login(self.staff)
        url = (
            f'/admin/inventory/stockmovement/{self.movement.pk}/delete/'
        )

        response = self.client.post(url, {'post': 'yes'})

        self.assertEqual(response.status_code, 403)
        self.assertTrue(
            StockMovement.objects.filter(pk=self.movement.pk).exists(),
        )


class InventoryServiceUnchangedByDeleteGuardTests(StockAdminGuardTestCase):
    """AC-6 — InventoryService mutation and audit paths still work."""

    def test_restock_creates_movement_after_delete_guard(self):
        movement = InventoryService.restock(
            self.variant, 25,
            performed_by=self.staff,
            note='PROD-032 restock verify',
        )
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, 125)
        self.assertEqual(movement.quantity_before, 100)
        self.assertEqual(movement.quantity_after, 125)
        self.assertTrue(
            StockMovement.objects.filter(
                stock=self.stock, note='PROD-032 restock verify',
            ).exists(),
        )

    def test_adjust_stock_still_works_after_delete_guard(self):
        InventoryService.adjust_stock(
            self.variant, 80,
            performed_by=self.staff,
            note='PROD-032 adjust verify',
        )
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, 80)
        self.assertTrue(
            StockMovement.objects.filter(
                stock=self.stock, note='PROD-032 adjust verify',
            ).exists(),
        )
