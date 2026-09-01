# ADR-003 — Pricing and Review Concurrency Consistency

## Status

Accepted

## Context

`Product.min_price` and `Product.max_price` are denormalized from prices on
active variants. Concurrent price updates, removals, variant activation
changes, or variant deletions for the same product could otherwise calculate
from incomplete committed state and publish stale bounds.

`Product.rating` and `Product.reviews_count` are likewise denormalized values.
`reviews` calculates the average rating and approved-review count, while
`catalog` owns the product fields. Creating, editing, deleting, approving, or
rejecting reviews can otherwise cause concurrent aggregate recomputations to
overwrite a result calculated before another transaction commits.

A review's unique user/product constraint protects duplicate review creation,
but it does not protect the aggregate read-modify-write sequence. In both
cases, the shared authoritative row is `catalog.Product`, rather than an
individual `Price`, `ProductVariant`, or `Review` row.

## Decision

Use `transaction.atomic()` and `SELECT ... FOR UPDATE` on the authoritative
`Product` row to serialize each product's consistency-sensitive recomputation.
The lock is held through the calculation and the catalog service write, until
commit.

For price bounds, `PricingService.set_price()`, `remove_price()`,
`set_variant_active()`, `delete_variant()`, and the public
`recalculate_product_bounds()` path acquire the product lock before mutating
price-relevant state or calculating bounds. They follow the lock order:
product first, then variant/price state.

For review aggregates, aggregate-affecting `ReviewService` methods run in
`transaction.atomic()`. `recalculate_product_rating()` locks the product
before calculating approved-review `AVG`/`COUNT`, then writes through
`CatalogService.set_review_stats()`. The review service owns the calculation;
the catalog service remains the single service-level writer of
`Product.rating` and `Product.reviews_count`.

## Consequences

### Positive

- Concurrent price operations for one product serialize, so committed bounds
  are calculated from a complete committed view of the relevant prices.
- Concurrent review aggregate operations for one product do not lose approved
  reviews in the published count or average.
- The locking protocol protects each recomputation's complete
  read-modify-write invariant, rather than only one row update.
- Price paths use a consistent product-first lock order to avoid deadlocks
  among those paths.
- Calculation ownership and catalog field-write ownership remain explicit;
  review aggregates have no second review-side ORM writer.
- Cross-connection tests cover both consistency protocols and their resulting
  denormalized invariants.

### Negative / Trade-offs

- Concurrent price, review, or moderation operations for the same product can
  wait on the product lock.
- The locks are deliberately broader than a single query because they protect
  a multi-row recomputation.
- Raw ORM writes outside the service/admin paths are not covered by these
  application-level protocols.
- `reviews` intentionally depends on catalog's public service contract for
  the aggregate write.

## Alternatives Considered

- Lock only an individual `Price`, variant, or `Review` row. Rejected because
  each result is derived from a set of rows associated with one product.
- Rely only on the review uniqueness constraint. Rejected because it does not
  serialize aggregate recomputation.
- Use `F()` expressions alone. Rejected because price bounds and review
  averages/counts are recomputations over sets of rows, not single-field
  increments.
- Recompute after commit, in a signal, or in an asynchronous task. Rejected
  because the current invariant requires an explicit, synchronous,
  transactional write path.
- Update product aggregates directly from `reviews`. Rejected because the
  fields belong to catalog and are written through its service contract.

## References

- `ARCHITECTURE.md` — “Concurrency & Transaction Safety” and
  “Cross-Domain Coordination”.
- `apps/pricing/services/pricing_service.py` — `_locked_product()` and all
  authoritative price-bound paths.
- `apps/reviews/services/review_service.py` — `_locked_product()` and
  `recalculate_product_rating()`.
- `apps/catalog/services/catalog_service.py` — `set_product_prices()` and
  `set_review_stats()`.
- `apps/pricing/tests/test_services.py` — price-bound and concurrency
  coverage.
- `apps/reviews/tests/test_concurrency.py` and
  `apps/reviews/tests/test_architecture.py` — review locking and ownership
  coverage.
- Issue #24 (PROD-001).
