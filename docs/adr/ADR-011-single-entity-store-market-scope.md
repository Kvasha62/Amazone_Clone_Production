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

## Non-goals

No marketplace, tenant-isolation, international tax or broad international-shipping implementation is introduced by this ADR.

## Related

- ADR-008 — Money, Currency and Refund Invariants
- Issue #92 — Accounting Currency, LegalEntity & StoreMarket boundaries
- Issue #97 — Single-entity first release and no tenant-scoping
