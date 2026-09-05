# ADR-009 — Currency Registry and Monetary Precision

- **Status:** Accepted
- **Date:** 2026-09-06
- **Scope:** currencies, pricing, orders, payments, refunds

## Context

The legacy system has a hard-coded RUB/USD/EUR currency choice on `Price`, while other monetary models have no explicit currency. A production multi-currency contract therefore requires a single authoritative currency registry and currency-specific precision rules.

## Decision

1. Currency identity is governed by an ISO-4217 registry owned by the `currencies` bounded context.
2. A currency record contains at least ISO alphabetic code, ISO numeric code, minor-unit precision, and active status.
3. The registry is extensible beyond RUB/USD/EUR. Zero-decimal and three-decimal currencies are first-class.
4. Historical monetary facts retain their currency even if that currency is later deactivated. Deactivation does not rewrite or invalidate history.
5. Persisted financial amounts use `Decimal`; floating-point arithmetic is forbidden for monetary calculations.
6. Provider-facing amounts are represented at the target currency's allowed minor-unit precision.
7. Internal conversion may use higher Decimal precision, but conversion to a financial/provider-facing amount is deterministic and uses the currency's minor-unit rule.
8. The implementation uses `ROUND_HALF_UP` as the default deterministic rounding mode unless a provider contract explicitly requires a stricter compatible rule.
9. No universal two-decimal assumption is permitted.
10. Currency validation and precision are centralized; bounded contexts must not maintain independent currency enums or RUB-specific precision rules.

## Pricing Currency Authority

`LegalEntity.accounting_currency` is the authoritative Accounting Currency for current commercial pricing.

The accepted first-release commercial ownership path is:

`Product → Store → LegalEntity → accounting_currency`

`ProductVariant` and `Price` inherit that ownership through `Product`. `Price` therefore does not own an independent currency choice and must not contain a competing pricing-owned currency enum or second business source of truth.

The effective current pricing currency is resolved as:

`Price → ProductVariant → Product → Store → LegalEntity → accounting_currency`

API/client input cannot select or override this currency. Pricing and Cart do not perform FX conversion.

## Ownership

- `currencies` owns currency metadata and FX-rate records.
- `pricing`, `cart`, `orders`, `shipping`, and `discounts` consume currency identity but do not own competing registries.
- `payments` owns payment-currency selection and applies the registry rules at the payment boundary.
- `refunds` preserve the original Payment Currency for buyer-facing obligations.
- `merchants` owns LegalEntity/Store/StoreMarket commercial ownership concepts; Product's first-release commercial owner is an explicit Store reference in the catalog model.

## Historical Currency Snapshots

Historical monetary facts are snapshots, not live references to mutable commercial configuration.

`PriceHistory.currency` is nullable solely to represent pre-existing legacy rows for which the historical currency was never persisted. Such rows remain `NULL` unless authoritative, record-specific evidence permits a targeted backfill.

The following are not valid evidence for historical backfill:

- current `Price.currency`;
- current LegalEntity or Store configuration;
- model defaults;
- seed data or seed assumptions.

All newly-created `PriceHistory` records must persist the authoritative Accounting Currency at creation time. Once persisted, the historical currency is immutable.

## Legacy Price Migration

Removal of the legacy `Price.currency` authority requires a fail-closed migration preflight. Every existing Price amount must be shown to be compatible with the Accounting Currency of its commercial owner before the legacy field is removed.

If any existing Price is incompatible, ambiguous, or cannot be established as compatible, the migration stops. Existing amounts are not silently rewritten and FX conversion is not permitted as a migration mechanism.

## Historical invariants

A completed Order, Payment, Refund or Shipment must remain valid if the current Currency Registry changes. Current `active` status is not consulted to reinterpret a historical monetary fact.

## Non-goals

This ADR does not select an FX vendor, payment provider, tax regime, or storefront display-currency policy.

## Related

- ADR-008 — Money, Currency and Refund Invariants
- ADR-011 — Single LegalEntity, Store and StoreMarket in First Release
- ADR-004 — Secure and Idempotent Payment Webhooks
- ADR-005 — Allocate Order Numbers from a PostgreSQL Sequence
- ADR-006 — Public Resource Identifiers for API v1
- Issue #95 — Currency Registry and ISO-4217 monetary precision
