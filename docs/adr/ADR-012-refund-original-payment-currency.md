# ADR-012 — Refund Buyer-Facing Amount Is Immutable in Original Payment Currency

- **Status:** Accepted
- **Date:** 2026-09-06
- **Scope:** payments, refunds, accounting/reconciliation

## Context

A refund must remain correct even when exchange rates, catalog prices, store configuration or accounting currency change after payment capture.

## Decision

1. The original captured Payment is the sole authoritative source for a buyer-facing refund amount and currency.
2. A full refund returns exactly the original captured `charged_amount` in exactly the original `charged_currency`.
3. A partial refund, when supported, remains in the original Payment Currency and the cumulative charged-currency refunds cannot exceed the captured charged amount.
4. A refund retry is another execution attempt of the same obligation. It cannot reduce or convert the buyer-facing amount.
5. Refund-time FX may be recorded only for internal accounting/reconciliation when required by the execution path. It must never alter the buyer-facing refund obligation.
6. Any accounting gain/loss caused by FX movement is separate from the buyer refund and must not be hidden by changing the refund amount.
7. Refund amount/currency must never be inferred from current Product, Price, Order, Store, LegalEntity, Currency Registry activation state or current FX rates.

## Consequences

The refund model must persist enough immutable information to reconstruct the exact original charged-currency obligation and to audit each provider execution attempt independently.

## Related

- ADR-008 — Money, Currency and Refund Invariants
- ADR-010 — Payment Provider Port, Adapter and Router
- Issue #98 — Refund buyer-facing amount is immutable in original Payment Currency
