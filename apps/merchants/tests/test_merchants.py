"""Focused tests for the merchants ownership foundation (issue #110)."""

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.test import TestCase
from rest_framework.exceptions import ValidationError as DRFValidationError

from apps.currencies.models import Currency
from apps.merchants.models import LegalEntity, Store, StoreMarket
from apps.merchants.services import MerchantsService


class SeededFirstReleaseContractTests(TestCase):
    """Issue #110: initial data is exactly one entity/store/market."""

    def test_exactly_one_active_entity_store_and_market_are_seeded(self):
        self.assertEqual(LegalEntity.objects.count(), 1)
        self.assertEqual(Store.objects.count(), 1)
        self.assertEqual(StoreMarket.objects.count(), 1)

        entity = MerchantsService.get_active_legal_entity()
        store = Store.objects.get(slug="amazone-clone")
        market = MerchantsService.get_active_store_market(store)

        self.assertTrue(entity.is_active)
        self.assertTrue(market.is_active)
        self.assertEqual(store.legal_entity_id, entity.pk)
        self.assertEqual(market.store_id, store.pk)

    def test_seeded_accounting_currency_is_rub(self):
        entity = MerchantsService.get_active_legal_entity()
        self.assertEqual(entity.accounting_currency.code, "RUB")
        self.assertEqual(entity.accounting_currency.numeric_code, 643)

    def test_seeded_market_enables_supported_payment_currencies(self):
        market = MerchantsService.get_active_store_market(
            Store.objects.get(slug="amazone-clone"),
        )
        self.assertEqual(market.country_code, "RU")
        self.assertEqual(
            set(market.payment_currencies.values_list("code", flat=True)),
            {"EUR", "RUB", "USD"},
        )

    def test_reverse_migration_data_is_not_duplicated(self):
        """get_active_* helpers resolve the single seeded configuration."""
        entity = MerchantsService.get_active_legal_entity()
        store = MerchantsService.get_store("amazone-clone")
        market = MerchantsService.get_active_store_market(store)

        self.assertEqual(store.legal_entity_id, entity.pk)
        self.assertEqual(market.store_id, store.pk)


class AccountingCurrencyOwnershipTests(TestCase):
    """Issue #110: Accounting Currency belongs to LegalEntity only."""

    def test_accounting_currency_exists_only_on_legal_entity(self):
        self.assertTrue(hasattr(LegalEntity, "accounting_currency"))
        # Store and StoreMarket never own or redefine Accounting Currency
        # (ADR-011 #7).
        self.assertFalse(hasattr(Store, "accounting_currency"))
        self.assertFalse(hasattr(StoreMarket, "accounting_currency"))

    def test_service_rejects_accounting_currency_change(self):
        entity = MerchantsService.get_active_legal_entity()
        other = Currency.objects.get(code="USD")

        with self.assertRaises(DRFValidationError) as ctx:
            MerchantsService.update_legal_entity(
                entity,
                accounting_currency=other,
            )
        self.assertIn("Accounting currency cannot be changed", str(ctx.exception.detail))

        entity.refresh_from_db()
        self.assertEqual(entity.accounting_currency.code, "RUB")

    def test_service_accepts_unchanged_accounting_currency(self):
        entity = MerchantsService.get_active_legal_entity()

        updated = MerchantsService.update_legal_entity(
            entity,
            legal_name="Amazone Clone Ltd.",
            accounting_currency=entity.accounting_currency,
        )
        self.assertEqual(updated.accounting_currency.code, "RUB")

    def test_direct_orm_save_cannot_rewrite_accounting_currency(self):
        """Defense-in-depth: the model guard blocks non-service mutation."""
        entity = MerchantsService.get_active_legal_entity()
        entity.accounting_currency = Currency.objects.get(code="EUR")

        with self.assertRaises(DjangoValidationError):
            entity.save()

        entity.refresh_from_db()
        self.assertEqual(entity.accounting_currency.code, "RUB")

    def test_accounting_currency_requires_active_registry_currency(self):
        inactive = Currency.objects.get(code="JPY")
        inactive.is_active = False
        inactive.save(update_fields=["is_active", "updated_at"])

        with transaction.atomic():
            with self.assertRaises(DRFValidationError):
                MerchantsService.create_legal_entity(
                    legal_name="Second Entity",
                    accounting_currency=inactive,
                )
        self.assertFalse(
            LegalEntity.objects.filter(legal_name="Second Entity").exists(),
        )

    def test_accounting_currency_deletion_is_protected(self):
        """Registry currencies backing financial ownership cannot be deleted."""
        entity = MerchantsService.get_active_legal_entity()

        with self.assertRaises(ProtectedError):
            entity.accounting_currency.delete()

    def test_deactivated_registry_currency_keeps_entity_reference(self):
        """Historical validity: deactivation does not break ownership."""
        entity = MerchantsService.get_active_legal_entity()
        currency = entity.accounting_currency
        currency.is_active = False
        currency.save(update_fields=["is_active", "updated_at"])

        entity.refresh_from_db()
        self.assertEqual(entity.accounting_currency_id, currency.pk)
        self.assertEqual(entity.accounting_currency.code, "RUB")


class SingleActiveConfigurationTests(TestCase):
    """Issue #110 / ADR-011 #1: one active entity and one active market."""

    def test_second_active_legal_entity_is_rejected_by_service(self):
        with transaction.atomic():
            with self.assertRaises(DRFValidationError):
                MerchantsService.create_legal_entity(
                    legal_name="Second Entity",
                    accounting_currency=Currency.objects.get(code="USD"),
                )
        self.assertEqual(LegalEntity.objects.count(), 1)

    def test_second_active_legal_entity_is_rejected_by_database(self):
        with self.assertRaises(IntegrityError):
            LegalEntity.objects.create(
                legal_name="Raw Second Entity",
                accounting_currency=Currency.objects.get(code="USD"),
                is_active=True,
            )

    def test_archived_entity_allows_a_new_active_entity(self):
        """ADR-011 #4: structurally extensible to multiple entities."""
        first = MerchantsService.get_active_legal_entity()
        MerchantsService.update_legal_entity(first, is_active=False)

        second = MerchantsService.create_legal_entity(
            legal_name="Second Entity",
            accounting_currency=Currency.objects.get(code="USD"),
        )
        self.assertTrue(second.is_active)
        first.refresh_from_db()
        self.assertFalse(first.is_active)

    def test_second_active_market_per_store_is_rejected(self):
        store = MerchantsService.get_store("amazone-clone")

        with transaction.atomic():
            with self.assertRaises(DRFValidationError):
                MerchantsService.create_store_market(
                    store=store,
                    country_code="KZ",
                    payment_currencies=Currency.objects.filter(is_active=True),
                )
        self.assertEqual(StoreMarket.objects.count(), 1)

    def test_second_active_market_per_store_is_rejected_by_database(self):
        store = MerchantsService.get_store("amazone-clone")

        with self.assertRaises(IntegrityError):
            StoreMarket.objects.create(store=store, country_code="KZ", is_active=True)

    def test_second_store_may_have_its_own_active_market(self):
        """ADR-011 #4: extensible to multiple stores/markets later."""
        entity = MerchantsService.get_active_legal_entity()
        second_store = MerchantsService.create_store(
            legal_entity=entity,
            name="Outlet",
            slug="outlet",
        )
        market = MerchantsService.create_store_market(
            store=second_store,
            country_code="RU",
            payment_currencies=Currency.objects.filter(code="RUB"),
        )
        self.assertTrue(market.is_active)
        self.assertEqual(StoreMarket.objects.filter(is_active=True).count(), 2)

    def test_duplicate_market_country_per_store_is_rejected(self):
        store = MerchantsService.get_store("amazone-clone")
        market = MerchantsService.get_active_store_market(store)
        MerchantsService.update_store_market(market, is_active=False)

        with self.assertRaises(IntegrityError):
            StoreMarket.objects.create(store=store, country_code="RU", is_active=True)


class StoreMarketConfigurationTests(TestCase):
    """Issue #92 minimal StoreMarket scope, enforced by services."""

    def test_country_code_must_be_iso_alpha2(self):
        store = MerchantsService.get_store("amazone-clone")
        market = MerchantsService.get_active_store_market(store)

        for invalid in ("ru", "RUS", "1U", ""):
            with self.assertRaises(DRFValidationError):
                MerchantsService.create_store_market(
                    store=store,
                    country_code=invalid,
                    payment_currencies=Currency.objects.filter(is_active=True),
                    is_active=False,
                )
            with self.assertRaises(DRFValidationError):
                MerchantsService.update_store_market(market, country_code=invalid)

    def test_lowercase_country_is_rejected_by_database(self):
        store = MerchantsService.get_store("amazone-clone")

        with self.assertRaises(IntegrityError):
            StoreMarket.objects.create(store=store, country_code="ru", is_active=False)

    def test_payment_currencies_must_be_active(self):
        store = MerchantsService.get_store("amazone-clone")
        market = MerchantsService.get_active_store_market(store)

        jpy = Currency.objects.get(code="JPY")
        jpy.is_active = False
        jpy.save(update_fields=["is_active", "updated_at"])

        with transaction.atomic():
            with self.assertRaises(DRFValidationError):
                MerchantsService.update_store_market(
                    market,
                    payment_currencies=[jpy],
                )
        self.assertEqual(
            set(market.payment_currencies.values_list("code", flat=True)),
            {"EUR", "RUB", "USD"},
        )

    def test_payment_currencies_cannot_be_empty(self):
        store = MerchantsService.get_store("amazone-clone")
        market = MerchantsService.get_active_store_market(store)

        with self.assertRaises(DRFValidationError):
            MerchantsService.update_store_market(market, payment_currencies=[])

    def test_payment_currency_deactivation_keeps_market_reference(self):
        market = MerchantsService.get_active_store_market(
            MerchantsService.get_store("amazone-clone"),
        )
        usd = Currency.objects.get(code="USD")
        usd.is_active = False
        usd.save(update_fields=["is_active", "updated_at"])

        market.refresh_from_db()
        self.assertIn(usd, market.payment_currencies.all())

    def test_market_update_replaces_payment_currencies(self):
        market = MerchantsService.get_active_store_market(
            MerchantsService.get_store("amazone-clone"),
        )

        MerchantsService.update_store_market(
            market,
            payment_currencies=Currency.objects.filter(code__in=["RUB", "EUR"]),
        )
        self.assertEqual(
            set(market.payment_currencies.values_list("code", flat=True)),
            {"EUR", "RUB"},
        )


class StoreOwnershipTests(TestCase):
    """Issue #110: Store belongs to exactly one LegalEntity."""

    def test_store_requires_legal_entity(self):
        with self.assertRaises(IntegrityError):
            Store.objects.create(name="Orphan", slug="orphan")

    def test_store_cannot_be_created_under_archived_entity(self):
        entity = MerchantsService.get_active_legal_entity()
        MerchantsService.update_legal_entity(entity, is_active=False)

        with transaction.atomic():
            with self.assertRaises(DRFValidationError):
                MerchantsService.create_store(
                    legal_entity=entity,
                    name="Ghost Store",
                    slug="ghost-store",
                )

    def test_store_requires_name_and_slug(self):
        entity = MerchantsService.get_active_legal_entity()

        with self.assertRaises(DRFValidationError):
            MerchantsService.create_store(legal_entity=entity, name="", slug="x")
        with self.assertRaises(DRFValidationError):
            MerchantsService.create_store(legal_entity=entity, name="X", slug="")

    def test_duplicate_store_name_per_entity_is_rejected(self):
        entity = MerchantsService.get_active_legal_entity()

        with self.assertRaises(IntegrityError):
            Store.objects.create(
                legal_entity=entity,
                name="Amazone Clone",
                slug="another-slug",
            )

    def test_legal_entity_deletion_is_protected_while_store_exists(self):
        entity = MerchantsService.get_active_legal_entity()

        with self.assertRaises(ProtectedError):
            entity.delete()


class LegalEntityServiceTests(TestCase):
    """Service-layer creation/update rules for LegalEntity."""

    def test_legal_name_is_required(self):
        with self.assertRaises(DRFValidationError):
            MerchantsService.create_legal_entity(
                legal_name="   ",
                accounting_currency=Currency.objects.get(code="RUB"),
            )

    def test_update_renames_legal_entity(self):
        entity = MerchantsService.get_active_legal_entity()

        MerchantsService.update_legal_entity(entity, legal_name="Renamed Ltd.")
        entity.refresh_from_db()
        self.assertEqual(entity.legal_name, "Renamed Ltd.")

    def test_update_rejects_empty_legal_name(self):
        entity = MerchantsService.get_active_legal_entity()

        with self.assertRaises(DRFValidationError):
            MerchantsService.update_legal_entity(entity, legal_name=" ")

    def test_reactivation_is_rejected_while_another_entity_is_active(self):
        first = MerchantsService.get_active_legal_entity()
        MerchantsService.update_legal_entity(first, is_active=False)
        second = MerchantsService.create_legal_entity(
            legal_name="Second Entity",
            accounting_currency=Currency.objects.get(code="USD"),
        )

        with transaction.atomic():
            with self.assertRaises(DRFValidationError):
                MerchantsService.update_legal_entity(first, is_active=True)
        second.refresh_from_db()
        self.assertTrue(second.is_active)
