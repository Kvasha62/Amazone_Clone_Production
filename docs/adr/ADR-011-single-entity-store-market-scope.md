# ADR-011 — Single LegalEntity, Store and StoreMarket in First Release

- **Status:** Accepted
- **Date:** 2026-09-06
- **Scope:** merchants, markets, financial ownership

## Decision

1. The first production implementation contains exactly one active `LegalEntity`, one `Store`, and one active `StoreMarket`.
2. The schema introduces these explicit domain concepts so financial ownership is correct from the beginning.
3. Tenant-aware query isolation, tenant switching, tenant-specific permissions and a multi-tenant operating model are out of scope.
4. The model must remain structurally extensible to multiple LegalEntities, Stores and Markets later.
5. A Store belongs to exactly one LegalEntity.
6. A StoreMarket is commercial configuration for a Store; it may define sales geography, enabled payment currencies, provider profiles and shipping-zone associations.
7. StoreMarket never owns Accounting Currency and never changes the monetary semantics of an Order.
8. Historical financial facts remain bound to the LegalEntity/accounting-currency snapshot under which they were created.
9. **First-release Product commercial ownership is explicit:** each `Product` is commercially owned by exactly one `Store`.
10. `ProductVariant` and `Price` inherit commercial ownership through their Product. They do not independently select a Store, LegalEntity, or Accounting Currency.
11. Therefore the authoritative current pricing currency path is:
    `Product → Store → LegalEntity → accounting_currency`.
12. The Product → Store relation is a commercial ownership relation for the first release. It is not tenant isolation, marketplace membership, or a claim that Products may never be reused across Stores in a future model.
13. Any future multi-store catalog reuse requires a separate architecture decision; it must not be simulated by introducing a second currency authority inside Pricing.

## Data migration boundary

The first release has no historical Product → Store ownership field. Introducing the explicit relation requires a fail-closed preflight against the existing data and repository semantics.

The preflight must confirm that the existing production model contains no conflicting multi-store commercial ownership semantics. Seed data alone is not sufficient evidence.

Only after that preflight succeeds may existing Products be associated with the single active Store as the first-release commercial owner.

If conflicting ownership evidence is found, the migration stops and a new architecture/data decision is required.

This Product ownership bootstrap is distinct from historical monetary backfill: it must never be used as evidence to invent a legacy `PriceHistory` currency.

## Non-goals

No marketplace, tenant-isolation, international tax or broad international-shipping implementation is introduced by this ADR.

## Related

- ADR-008 — Money, Currency and Refund Invariants
- ADR-009 — Currency Registry and Monetary Precision
- Issue #92 — Accounting Currency, LegalEntity & StoreMarket boundaries
- Issue #97 — Single-entity first release and no tenant-scoping
- Issue #122 — PROD-042B: Product/Price commercial ownership and Accounting Currency path
