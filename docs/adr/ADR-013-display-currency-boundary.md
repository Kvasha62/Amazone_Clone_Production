# ADR-013 — Display Currency Is Indicative, Not a Financial Fact

- **Status:** Accepted
- **Date:** 2026-09-06
- **Scope:** storefront presentation, pricing, payments

## Decision

1. The current multi-currency implementation supports payment-currency selection, not persistent multi-currency catalog pricing.
2. Catalog, Cart, Order, Shipping and Discounts remain denominated in LegalEntity Accounting Currency.
3. Any future storefront display conversion is an indicative read model/quote and must carry its currency, rate timestamp and validity/TTL.
4. A display quote is never an Order, Payment or Refund financial fact.
5. A display-converted value must not drive authoritative sorting, filtering, discount calculation, accounting or order totals.
6. A buyer's selected Payment Currency becomes a financial fact only through the server-side payment-intent flow governed by ADR-008 and the Payment provider/FX boundaries.

## Non-goals

No storefront display-FX feature is required by this ADR.

## Related

- ADR-008 — Money, Currency and Refund Invariants
- ADR-009 — Currency Registry and Monetary Precision
- Issue #96 — Display currency is indicative
- Issue #108 — No storefront display-FX in current scope
