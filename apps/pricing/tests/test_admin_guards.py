"""PROD-004 (F-05) — PriceAdmin must not be a second writer of prices.

``Price.price`` / ``Price.sale_price`` (and the ``variant`` / ``currency``
that define whose price it is and in which currency) are owned by
``PricingService.set_price()``: it locks the authoritative ``Product`` row,
validates bounds, appends ``PriceHistory`` and republishes
``Product.min_price`` / ``Product.max_price`` through
``CatalogService.set_product_prices()``.

The tests cover the UI/form layer, the server-side ``save_model`` layer, the
add path, and the authoritative service path (including the derived
effective price and product bounds invariants).
"""

from decimal import Decimal

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db import connection
from django.test import RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext

from apps.catalog.constants import ProductStatus
from apps.catalog.models import Brand, Category, Product, ProductVariant
from apps.pricing.admin.price_admin import (
    PRICE_ADMIN_PROTECTED_FIELDS,
    PriceAdmin,
)
from apps.pricing.models import Price, PriceHistory
from apps.pricing.services.pricing_service import PricingService

User = get_user_model()


class PriceAdminGuardTestCase(TestCase):
    """Shared fixtures: staff user, variants, price row."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            username='priceadmin',
            email='priceadmin@test.com',
            password='admin123!',
            is_staff=True,
            is_superuser=True,
        )
        cls.brand = Brand.objects.create(name='PriceGuardBrand')
        cls.category = Category.add_root(name='PriceGuardCat')
        cls.product = Product.objects.create(
            name='Price Guard Product',
            brand=cls.brand,
            primary_category=cls.category,
            status=ProductStatus.ACTIVE,
        )
        cls.variant = ProductVariant.objects.create(
            product=cls.product, sku='PRICE-GUARD-A', is_active=True,
        )
        cls.other_variant = ProductVariant.objects.create(
            product=cls.product, sku='PRICE-GUARD-B', is_active=True,
        )
        PricingService.set_price(
            cls.variant, Decimal('1000.00'), changed_by=cls.staff,
        )
        PricingService.set_price(
            cls.other_variant, Decimal('2000.00'), changed_by=cls.staff,
        )
        cls.product.refresh_from_db()
        cls.price = Price.objects.get(variant=cls.variant)

    def setUp(self):
        self.site = AdminSite()
        self.admin = PriceAdmin(Price, self.site)
        self.factory = RequestFactory()
        self.request = self.factory.get('/admin/pricing/price/')
        self.request.user = self.staff


class PriceAdminReadOnlyTests(PriceAdminGuardTestCase):
    """Layer 1 — price fields are not PriceAdmin inputs."""

    def test_protected_fields_are_declared_readonly(self):
        self.assertEqual(
            ('variant', 'price', 'sale_price', 'currency'),
            PRICE_ADMIN_PROTECTED_FIELDS,
        )
        for field in PRICE_ADMIN_PROTECTED_FIELDS:
            self.assertIn(field, self.admin.readonly_fields)

    def test_get_readonly_fields_always_contains_protected_fields(self):
        admin = PriceAdmin(Price, self.site)
        admin.readonly_fields = ()
        readonly = admin.get_readonly_fields(self.request, obj=self.price)
        for field in PRICE_ADMIN_PROTECTED_FIELDS:
            self.assertIn(field, readonly)

    def test_change_form_has_no_business_inputs(self):
        form_class = self.admin.get_form(
            self.request, obj=self.price, change=True,
        )
        form_fields = form_class(instance=self.price).fields
        for field in PRICE_ADMIN_PROTECTED_FIELDS:
            self.assertNotIn(field, form_fields)

    def test_change_page_shows_price_without_inputs(self):
        self.client.force_login(self.staff)
        response = self.client.get(
            f'/admin/pricing/price/{self.price.pk}/change/',
        )
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        # Инспекция сохранена ...
        self.assertIn('field-price', content)
        self.assertIn('1000.00', content)
        # ... но полей ввода нет.
        for field in PRICE_ADMIN_PROTECTED_FIELDS:
            self.assertNotIn(f'name="{field}"', content)

    def test_admin_add_is_not_offered(self):
        """Цену создаёт PricingService.set_price()."""
        self.assertFalse(self.admin.has_add_permission(self.request))
        self.client.force_login(self.staff)
        self.assertEqual(
            self.client.get('/admin/pricing/price/add/').status_code, 403,
        )

    def test_list_page_still_inspectable(self):
        self.client.force_login(self.staff)
        response = self.client.get('/admin/pricing/price/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'PRICE-GUARD-A')


class PriceAdminGuardTests(PriceAdminGuardTestCase):
    """Layer 2 — crafted POST / forced save cannot write prices."""

    def test_crafted_change_post_cannot_mutate_price(self):
        self.client.force_login(self.staff)
        before_bounds = (self.product.min_price, self.product.max_price)

        response = self.client.post(
            f'/admin/pricing/price/{self.price.pk}/change/',
            {
                # Подделанные бизнес-поля.
                'variant': str(self.other_variant.pk),
                'price': '1.00',
                'sale_price': '0.50',
                'currency': 'USD',
            },
        )

        self.assertEqual(response.status_code, 302)
        self.price.refresh_from_db()
        self.assertEqual(self.price.price, Decimal('1000.00'))
        self.assertIsNone(self.price.sale_price)
        self.assertEqual(self.price.currency, Price.CurrencyChoices.RUB)
        self.assertEqual(self.price.variant_id, self.variant.pk)
        self.assertEqual(self.price.effective_price, Decimal('1000.00'))
        self.product.refresh_from_db()
        self.assertEqual(
            (self.product.min_price, self.product.max_price), before_bounds,
        )

    def test_crafted_add_post_is_rejected(self):
        self.client.force_login(self.staff)
        response = self.client.post(
            '/admin/pricing/price/add/',
            {
                'variant': str(self.other_variant.pk),
                'price': '1.00',
                'currency': 'RUB',
            },
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(Price.objects.count(), 2)
        self.product.refresh_from_db()
        self.assertEqual(self.product.min_price, Decimal('1000.00'))

    def test_save_model_rejects_price_change(self):
        self.price.price = Decimal('1.00')
        with self.assertRaises(PermissionDenied):
            self.admin.save_model(
                self.request, self.price, form=None, change=True,
            )
        self.price.refresh_from_db()
        self.assertEqual(self.price.price, Decimal('1000.00'))

    def test_save_model_rejects_sale_price_change(self):
        self.price.sale_price = Decimal('0.50')
        with self.assertRaises(PermissionDenied):
            self.admin.save_model(
                self.request, self.price, form=None, change=True,
            )
        self.price.refresh_from_db()
        self.assertIsNone(self.price.sale_price)

    def test_save_model_rejects_variant_repoint(self):
        """Перепривязка цены меняет границы ДВУХ товаров — запрещена."""
        self.price.variant = self.other_variant
        with self.assertRaises(PermissionDenied):
            self.admin.save_model(
                self.request, self.price, form=None, change=True,
            )
        self.price.refresh_from_db()
        self.assertEqual(self.price.variant_id, self.variant.pk)

    def test_save_model_update_sql_excludes_protected_fields(self):
        with CaptureQueriesContext(connection) as captured:
            self.admin.save_model(
                self.request, self.price, form=None, change=True,
            )

        updates = [
            query['sql']
            for query in captured.captured_queries
            if 'UPDATE "pricing_price"' in query['sql']
        ]
        self.assertTrue(updates)
        update_sql = '\n'.join(updates)
        for field in PRICE_ADMIN_PROTECTED_FIELDS:
            self.assertNotIn(f'"{field}"', update_sql)

    def test_save_model_add_rejects_any_price_value(self):
        forged = Price(
            variant=self.variant, price=Decimal('1.00'), currency='RUB',
        )
        with self.assertRaises(PermissionDenied):
            self.admin.save_model(
                self.request, forged, form=None, change=False,
            )


class PriceAuthoritativePathTests(PriceAdminGuardTestCase):
    """Read-only Admin must not freeze PricingService."""

    def test_set_price_still_updates_price_history_and_bounds(self):
        history_before = PriceHistory.objects.filter(
            variant=self.variant,
        ).count()

        PricingService.set_price(
            self.variant, Decimal('750.00'), changed_by=self.staff,
        )

        self.price.refresh_from_db()
        self.assertEqual(self.price.price, Decimal('750.00'))
        self.assertEqual(self.price.effective_price, Decimal('750.00'))
        self.assertEqual(
            PriceHistory.objects.filter(variant=self.variant).count(),
            history_before + 1,
        )
        self.product.refresh_from_db()
        self.assertEqual(self.product.min_price, Decimal('750.00'))
        self.assertEqual(self.product.max_price, Decimal('2000.00'))

    def test_remove_price_still_recalculates_bounds(self):
        PricingService.remove_price(self.other_variant)

        self.product.refresh_from_db()
        self.assertEqual(self.product.min_price, Decimal('1000.00'))
        self.assertEqual(self.product.max_price, Decimal('1000.00'))
