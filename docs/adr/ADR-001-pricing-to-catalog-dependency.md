# ADR-001 — Pricing-to-Catalog Dependency Direction

## Status

Accepted

## Context

`pricing` owns `Price`, `PriceHistory`, and the calculation of a product's
price bounds from prices on active variants. `catalog` owns `Product`,
including the denormalized `min_price` and `max_price` fields. Updating those
fields directly from `pricing` would bypass catalog ownership, while making
the catalog service query pricing data would reverse the business-path
dependency and couple it to pricing storage.

The one-way rule applies to the authoritative application service path:
`CatalogService` does not import `pricing` or query pricing tables. Some
catalog management commands are bootstrap tooling and do import pricing
models/services to populate data; they are an existing exception outside the
runtime price-bound contract and do not calculate or write bounds through
`CatalogService`.

## Decision

Keep the dependency one-way: `pricing → catalog`.

`PricingService.recalculate_product_bounds()` calculates the bounds from
pricing-owned `Price` rows and calls the catalog-owned
`CatalogService.set_product_prices()` contract to write the values to
`Product`. The `CatalogService` business path does not import `pricing` or
query pricing tables.

The same boundary applies to price-relevant variant changes: the explicit
`PricingService.set_variant_active()` and `PricingService.delete_variant()`
entry points coordinate the catalog-owned variant mutation and the pricing
recalculation.

## Consequences

### Positive

- The calculation stays with the domain that owns price data.
- `catalog` remains the sole service-level writer of its product fields.
- The runtime price-bound dependency is directional and visible in service
  calls.
- Price-bound updates have one authoritative path.

### Negative / Trade-offs

- Callers must use the pricing service for price-relevant variant changes.
- Raw ORM or shell changes to a variant's active state can leave bounds stale
  until a pricing operation recalculates them.
- `pricing` has an intentional dependency on catalog's public service
  contract.
- Catalog data-population management commands are an existing reverse tooling
  dependency; this ADR documents it rather than treating the one-way runtime
  contract as a repository-wide import ban.

## Alternatives Considered

- Have `CatalogService` import pricing or query `Price` rows. Rejected for
  the runtime business path because it reverses the established dependency
  direction.
- Let `pricing` update `Product.min_price` and `Product.max_price` through the
  ORM. Rejected because it bypasses catalog model ownership.
- Use an automatic cross-domain signal or global event listener. Rejected in
  favor of an explicit, transactional service call.

## References

- `ARCHITECTURE.md` — “Domain Ownership” and “Cross-Domain Coordination / Price Bounds”.
- `apps/pricing/services/pricing_service.py` — `recalculate_product_bounds()`,
  `set_variant_active()`, and `delete_variant()`.
- `apps/catalog/services/catalog_service.py` — `set_product_prices()` and
  variant mutation contracts.
- `apps/catalog/management/commands/populate_catalog.py` and
  `apps/catalog/management/commands/populate_full.py` — existing bootstrap
  tooling exception.
- Issue #24 (PROD-001).
