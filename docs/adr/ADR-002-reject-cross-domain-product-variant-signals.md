# ADR-002 — Reject Cross-Domain ProductVariant Signals

## Status

Accepted

## Context

Changing a `ProductVariant`'s active state affects the denormalized price
bounds on its `Product`. The repository previously used cross-domain
`ProductVariant` signal wiring for this boundary. That mechanism made an
important business side effect implicit and required the receiving domain to
react to a catalog lifecycle event.

The current implementation has removed the `on_variant_change` (`post_save`)
and `on_variant_delete` (`post_delete`) cross-domain wiring, together with the
pricing-side price signals. Existing registered signals are restricted to
same-domain logging and local denormalization for this boundary.

## Decision

Do not use Django signals as the primary mechanism for cross-domain business
orchestration. In particular, do not restore cross-domain `ProductVariant`
signals for pricing recalculation.

Price-relevant variant changes are coordinated through explicit calls to
`PricingService.set_variant_active()` and `PricingService.delete_variant()`.
Those methods make the catalog mutation, pricing recalculation, transaction,
and error path visible at the call site.

## Consequences

### Positive

- Cross-domain control flow and its transaction boundary are explicit.
- Failures are observable and can be handled by the orchestrating service.
- The one-way `pricing → catalog` dependency remains intact.
- There is no hidden signal-driven writer of product price bounds.

### Negative / Trade-offs

- Direct ORM/admin changes that bypass the approved service path are not
  automatically repaired by a signal.
- Admin surfaces must guard price-relevant mutations rather than rely on
  signal side effects.
- Same-domain signals remain possible for local housekeeping; this decision
  does not turn signals into a general prohibition.

## Alternatives Considered

- Restore `post_save`/`post_delete` handlers for `ProductVariant`. Rejected
  because they hide a cross-domain business workflow.
- Make catalog listen to pricing state. Rejected because it reverses the
  dependency direction.
- Introduce a global listener registry or event bus for this use case.
  Rejected because the implemented architecture uses explicit service calls,
  not global cross-domain orchestration.

## References

- `ARCHITECTURE.md` — “Cross-Domain Coordination / Price Bounds” and “Role of
  Django Signals”.
- `apps/pricing/services/pricing_service.py` — explicit variant coordination.
- `apps/catalog/signals.py` and `apps/pricing/apps.py` — current boundary
  wiring.
- `apps/catalog/tests/test_signals.py` and
  `apps/catalog/tests/test_admin_variant_guards.py` — regression coverage.
- Issue #24 (PROD-001).
