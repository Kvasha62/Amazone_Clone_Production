"""Merchant ownership service layer (issue #110 / PROD-041).

``MerchantsService`` is the authoritative mutation path for LegalEntity,
Store and StoreMarket. All creation/update rules live here so that no other
context has to mutate merchant configuration through the ORM (issue #92
ownership rules, issue #110 acceptance criteria).

Monetary ownership invariants enforced by this module:

- Accounting Currency belongs to the LegalEntity only and is immutable after
  creation (ADR-008 §1). A new accounting currency requires a new
  LegalEntity.
- Historical financial facts must never depend on current merchant
  configuration (ADR-008 §2, ADR-011 #8): downstream contexts snapshot what
  they need at fact-creation time and treat it as immutable. This module
  provides read helpers for that snapshot step and keeps mutation explicit.
- StoreMarket restricts payment currencies but never redefines Accounting
  Currency or Order monetary semantics (ADR-011 #7).
"""

from __future__ import annotations

import re

from django.db import transaction
from rest_framework.exceptions import ValidationError

from apps.currencies.models import Currency
from apps.merchants.models import LegalEntity, Store, StoreMarket

COUNTRY_CODE_PATTERN = re.compile(r"^[A-Z]{2}$")


class MerchantsService:
    """Creation/update rules and read helpers for merchant configuration."""

    # ------------------------------------------------------------------
    # Internal validation helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_accounting_currency(currency) -> Currency:
        """Validate an accounting-currency argument."""
        if not isinstance(currency, Currency) or currency.pk is None:
            raise ValidationError(
                {"accounting_currency": "A persisted registry Currency is required."},
            )
        if not currency.is_active:
            raise ValidationError(
                {"accounting_currency": "Accounting currency must be an active registry currency."},
            )
        return currency

    @staticmethod
    def _validate_payment_currencies(currencies) -> list[Currency]:
        """Validate enabled payment currencies for a market.

        The registry is authoritative: every entry must be a persisted,
        active Currency. Order-preserving de-duplication keeps ``set()``
        calls idempotent. Deactivating a currency later does not break
        historical market configuration: the many-to-many reference stays
        intact (PROD-040 historical-validity semantics).
        """
        if currencies is None:
            raise ValidationError(
                {"payment_currencies": "At least one payment currency is required."},
            )
        if isinstance(currencies, (str, bytes)) or not hasattr(
            currencies,
            "__iter__",
        ):
            raise ValidationError(
                {"payment_currencies": "payment_currencies must be an iterable of Currency instances."},
            )
        validated: list[Currency] = []
        seen: set[int] = set()
        for currency in currencies:
            if not isinstance(currency, Currency) or currency.pk is None:
                raise ValidationError(
                    {"payment_currencies": "Persisted registry Currency instances are required."},
                )
            if not currency.is_active:
                raise ValidationError(
                    {"payment_currencies": f"Currency {currency.code} is not active."},
                )
            if currency.pk not in seen:
                seen.add(currency.pk)
                validated.append(currency)
        if not validated:
            raise ValidationError(
                {"payment_currencies": "At least one payment currency is required."},
            )
        return validated

    @staticmethod
    def _validate_country_code(country_code) -> str:
        if not isinstance(country_code, str) or not COUNTRY_CODE_PATTERN.fullmatch(
            country_code,
        ):
            raise ValidationError(
                {"country_code": "country_code must be an ISO 3166-1 alpha-2 code (e.g. 'RU')."},
            )
        return country_code

    # ------------------------------------------------------------------
    # LegalEntity
    # ------------------------------------------------------------------

    @staticmethod
    @transaction.atomic
    def create_legal_entity(
        *,
        legal_name: str,
        accounting_currency: Currency,
        is_active: bool = True,
    ) -> LegalEntity:
        """Create a LegalEntity — the only owner of Accounting Currency.

        First-release contract: at most one active LegalEntity exists
        (ADR-011 #1). The partial unique constraint is the hard guarantee;
        the service check gives a domain-level error instead of an
        IntegrityError.
        """
        if not legal_name or not str(legal_name).strip():
            raise ValidationError({"legal_name": "legal_name is required."})

        MerchantsService._validate_accounting_currency(accounting_currency)

        if is_active and LegalEntity.objects.filter(is_active=True).exists():
            raise ValidationError(
                {"is_active": "Only one active LegalEntity is allowed (ADR-011)."},
            )

        return LegalEntity.objects.create(
            legal_name=str(legal_name).strip(),
            accounting_currency=accounting_currency,
            is_active=is_active,
        )

    @staticmethod
    @transaction.atomic
    def update_legal_entity(
        entity: LegalEntity,
        *,
        legal_name: str | None = None,
        is_active: bool | None = None,
        accounting_currency: Currency | None = None,
    ) -> LegalEntity:
        """Update mutable LegalEntity fields.

        Accounting Currency is rejected as a mutable field: changing it for
        an existing LegalEntity is prohibited (ADR-008 §1, issue #92). A new
        accounting currency requires a new LegalEntity; the previous entity
        is archived (``is_active=False``) instead.
        """
        if (
            accounting_currency is not None
            and accounting_currency.pk != entity.accounting_currency_id
        ):
            raise ValidationError(
                {
                    "accounting_currency": (
                        "Accounting currency cannot be changed once a LegalEntity "
                        "exists. Create a new LegalEntity for a new accounting "
                        "currency (ADR-008)."
                    ),
                },
            )

        update_fields: list[str] = []
        if legal_name is not None:
            if not str(legal_name).strip():
                raise ValidationError({"legal_name": "legal_name cannot be empty."})
            entity.legal_name = str(legal_name).strip()
            update_fields.append("legal_name")
        if is_active is not None:
            if is_active and not entity.is_active:
                if (
                    LegalEntity.objects.exclude(pk=entity.pk)
                    .filter(is_active=True)
                    .exists()
                ):
                    raise ValidationError(
                        {"is_active": "Only one active LegalEntity is allowed (ADR-011)."},
                    )
            entity.is_active = is_active
            update_fields.append("is_active")

        if update_fields:
            entity.save(update_fields=update_fields + ["updated_at"])
        return entity

    @staticmethod
    def get_active_legal_entity() -> LegalEntity:
        """Return the single active LegalEntity (first-release contract).

        Downstream contexts call this only when creating a financial fact,
        and copy (snapshot) what they need — never recalculating historical
        facts from it later (ADR-008 §2, ADR-011 #8).
        """
        return LegalEntity.objects.select_related("accounting_currency").get(
            is_active=True,
        )

    # ------------------------------------------------------------------
    # Store
    # ------------------------------------------------------------------

    @staticmethod
    @transaction.atomic
    def create_store(
        *,
        legal_entity: LegalEntity,
        name: str,
        slug: str,
    ) -> Store:
        """Create a Store owned by exactly one LegalEntity (ADR-011 #5)."""
        if not isinstance(legal_entity, LegalEntity) or legal_entity.pk is None:
            raise ValidationError(
                {"legal_entity": "A persisted LegalEntity is required."},
            )
        if not legal_entity.is_active:
            raise ValidationError(
                {"legal_entity": "Stores can only be created for an active LegalEntity."},
            )
        if not name or not str(name).strip():
            raise ValidationError({"name": "name is required."})
        if not slug or not str(slug).strip():
            raise ValidationError({"slug": "slug is required."})

        return Store.objects.create(
            legal_entity=legal_entity,
            name=str(name).strip(),
            slug=str(slug).strip(),
        )

    @staticmethod
    @transaction.atomic
    def update_store(
        store: Store,
        *,
        name: str | None = None,
        slug: str | None = None,
    ) -> Store:
        """Update mutable Store fields; ownership (legal_entity) is fixed."""
        update_fields: list[str] = []
        if name is not None:
            if not str(name).strip():
                raise ValidationError({"name": "name cannot be empty."})
            store.name = str(name).strip()
            update_fields.append("name")
        if slug is not None:
            if not str(slug).strip():
                raise ValidationError({"slug": "slug cannot be empty."})
            store.slug = str(slug).strip()
            update_fields.append("slug")

        if update_fields:
            store.save(update_fields=update_fields + ["updated_at"])
        return store

    @staticmethod
    def get_store(slug: str) -> Store:
        """Return the Store for a channel slug."""
        return Store.objects.select_related(
            "legal_entity",
            "legal_entity__accounting_currency",
        ).get(slug=slug)

    # ------------------------------------------------------------------
    # StoreMarket
    # ------------------------------------------------------------------

    @staticmethod
    @transaction.atomic
    def create_store_market(
        *,
        store: Store,
        country_code: str,
        payment_currencies,
        is_active: bool = True,
    ) -> StoreMarket:
        """Create the commercial configuration of a Store for one market."""
        if not isinstance(store, Store) or store.pk is None:
            raise ValidationError({"store": "A persisted Store is required."})

        MerchantsService._validate_country_code(country_code)
        currencies = MerchantsService._validate_payment_currencies(payment_currencies)

        if (
            is_active
            and StoreMarket.objects.filter(store=store, is_active=True).exists()
        ):
            raise ValidationError(
                {"is_active": "Only one active StoreMarket per Store is allowed (ADR-011)."},
            )

        market = StoreMarket.objects.create(
            store=store,
            country_code=country_code,
            is_active=is_active,
        )
        market.payment_currencies.set(currencies)
        return market

    @staticmethod
    @transaction.atomic
    def update_store_market(
        market: StoreMarket,
        *,
        country_code: str | None = None,
        payment_currencies=None,
        is_active: bool | None = None,
    ) -> StoreMarket:
        """Update mutable StoreMarket configuration."""
        update_fields: list[str] = []

        if country_code is not None:
            MerchantsService._validate_country_code(country_code)
            market.country_code = country_code
            update_fields.append("country_code")

        if is_active is not None:
            if is_active and not market.is_active:
                if (
                    StoreMarket.objects.exclude(pk=market.pk)
                    .filter(store=market.store, is_active=True)
                    .exists()
                ):
                    raise ValidationError(
                        {
                            "is_active": (
                                "Only one active StoreMarket per Store is allowed "
                                "(ADR-011)."
                            ),
                        },
                    )
            market.is_active = is_active
            update_fields.append("is_active")

        if update_fields:
            market.save(update_fields=update_fields + ["updated_at"])

        if payment_currencies is not None:
            currencies = MerchantsService._validate_payment_currencies(payment_currencies)
            market.payment_currencies.set(currencies)

        return market

    @staticmethod
    def get_active_store_market(store: Store) -> StoreMarket:
        """Return the single active StoreMarket of a Store."""
        return (
            StoreMarket.objects.filter(store=store, is_active=True)
            .select_related(
                "store",
                "store__legal_entity",
                "store__legal_entity__accounting_currency",
            )
            .prefetch_related("payment_currencies")
            .get()
        )
