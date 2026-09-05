# ADR-008: Money, Currency and Refund Invariants

- **Status:** Accepted
- **Date:** 2026-09-06
- **Scope:** pricing, cart, orders, shipping, payments, refunds, currencies, merchants

## Context

The current system historically behaves as a single-currency application, while `Price.currency` is an isolated per-variant field that is not propagated through cart, order, payment, shipping, or refund facts. Monetary fields in those contexts currently lack an explicit currency. This makes mixed-currency calculations possible and makes a refund contract impossible to prove from persisted facts.

The production business requirement is strict: when a buyer is entitled to a full refund, the buyer must receive the full amount originally charged, in exactly the currency in which the buyer paid. A later exchange-rate change, current catalog price, current store currency, or another mutable configuration must never reduce or convert that refund.

## Decision

### 1. Accounting Currency

Accounting Currency belongs to the selling **LegalEntity**, not to a product variant and not to an individual payment.

A `Store` is a commercial storefront/channel owned by a `LegalEntity`. A `StoreMarket` describes where and how that storefront sells. The initial deployment remains single-entity/single-store, but the legal-entity boundary is explicit.

`LegalEntity.accounting_currency` is the source of truth for internal commercial calculations. Orders and payments copy the relevant currency into immutable historical facts.

A change of accounting currency for an existing LegalEntity with financial history is not an in-place rewrite of historical facts. The preferred policy is to create a new LegalEntity for a new accounting currency; if temporal configuration is ever required, it must still never mutate historical facts.

### 2. Order Currency

All Order and OrderItem monetary values are calculated and stored exclusively in the Order's snapshot `accounting_currency`.

Order domain logic does not perform FX conversion and does not depend on payment providers.

The following are historical facts and must not be recalculated from current configuration:

- Order subtotal;
- delivery cost;
- discount;
- total;
- OrderItem unit prices;
- Order accounting currency.

### 3. Payment Dual-Currency Model

A Payment records two distinct monetary facts:

- `base_amount` + `base_currency`: the amount owed/recorded by the Order in Accounting Currency;
- `charged_amount` + `charged_currency`: the amount actually authorized/captured from the buyer in Payment Currency.

For a successful payment, `base_amount` must equal the immutable Order total in `base_currency`.

If conversion is required, the Payment stores an immutable FX snapshot sufficient to explain the charged amount, including at minimum rate, timestamp, source and currency pair.

The current exchange rate is never used to rewrite an already completed Payment.

### 4. FX Boundary

FX conversion belongs exclusively at the Payment boundary (and, if later introduced, a separate indicative display-quote/read model).

The following contexts do **not** perform FX conversion:

- pricing;
- cart;
- orders;
- shipping calculation;
- discounts.

Catalog and Order prices are not silently converted into a buyer currency and persisted as facts.

### 5. Currency Registry

Currencies are represented by an ISO-4217 reference registry rather than a hard-coded three-value enum attached to `Price`.

The registry provides at least:

- ISO code;
- numeric code;
- minor-unit precision;
- active/inactive status.

Currency-dependent rounding and minimum/maximum amount validation must use the currency's minor-unit rules rather than RUB-specific assumptions.

`Price.currency` must not remain the source of truth for commercial currency. Pricing is denominated in the LegalEntity's Accounting Currency.

### 6. Payment Providers

Payment providers are behind a Port & Adapter boundary inside `payments`.

`PaymentService` may depend on a provider port/router, but Order and other upstream domains must not import concrete provider adapters.

Provider capabilities determine whether a provider/profile supports a requested country, currency, method, refund type, and webhook capability. The provider selected for a payment is a routing/configuration fact, not a client-controlled arbitrary string.

Provider transaction identifiers are provider-originated identifiers. Correlation and webhook routing must preserve provider identity without weakening the existing replay/idempotency guarantees.

### 7. Refund Invariant

A refund is a monetary operation associated with a Payment.

For a **full refund**:

`refund.charged_amount == Payment.charged_amount`

and

`refund.charged_currency == Payment.charged_currency`.

Therefore:

- `100 EUR` paid → `100 EUR` refunded;
- `100 USD` paid → `100 USD` refunded;
- `10,000 RUB` paid → `10,000 RUB` refunded.

A full refund MUST NOT be recalculated from:

- current Product/Price data;
- current Order pricing;
- current Store/LegalEntity currency configuration;
- the exchange rate at refund time;
- any other mutable later configuration.

The authoritative source for the buyer-facing refund amount and currency is the original captured Payment.

For partial refunds, every refund operation remains in the original Payment Currency and the sum of charged-currency refunds must never exceed the captured charged amount. Each refund operation is separately persisted so that multiple refunds and their individual provider operations can be audited.

### 8. Accounting Representation of Refunds

The system may additionally record the business obligation represented by a refund in Accounting Currency. If such a base representation is used, it is an accounting/reporting fact and does not replace the buyer-facing charged-currency refund fact.

A refund may therefore contain:

- `base_amount` + `base_currency` for the accounting obligation;
- `charged_amount` + `charged_currency` for the actual refund instruction/result;
- its own FX snapshot when conversion is required for the refund execution;
- provider refund identifier and lifecycle state.

FX gain/loss caused by a difference between the accounting obligation and the later execution rate is an accounting/reporting concern. It must never alter the buyer's entitled original-currency refund amount.

### 9. Historical Immutability

Completed monetary facts are immutable with respect to amount and currency.

Changing prices, shipping tariffs, discounts, provider configuration, currency configuration, or FX rates must not alter historical Order, Payment, Refund, or Shipment monetary facts.

Retries of a failed refund may create a new provider execution attempt and a new execution FX snapshot where required, but they must preserve the underlying refund obligation and must not silently reduce the buyer's entitled amount.

## Consequences

### Positive

- Full refunds are deterministic and buyer-safe.
- Historical orders/payments remain auditable after configuration or FX changes.
- Accounting currency is separated cleanly from payment currency.
- FX and provider-specific behavior are kept outside Order/Price/Cart domains.
- Multiple partial refunds can be represented as separate auditable operations.

### Negative / Cost

- Currency must be propagated into historical monetary models and API contracts.
- A Currency Registry and FX boundary are required.
- Payment provider abstraction and routing become explicit architectural components.
- Existing RUB-specific validation/messages and factories require migration.
- Refund recovery logic must understand base and charged currency separately.

## Non-goals

This ADR does not select a concrete FX vendor, payment provider, tax regime, markup policy, international shipping policy, or customer-facing display-currency UX. Those require separate decisions before implementation.

## Required implementation constraints

1. No implementation may infer refund currency from current catalog/store configuration.
2. No implementation may convert a full refund into a different buyer-facing currency.
3. No implementation may use the current FX rate to reduce or rewrite the original captured refund entitlement.
4. No implementation may reintroduce mixed-currency arithmetic in Cart or Order.
5. No implementation may bypass the Payment Service/provider port with direct provider calls from Order.
6. All new monetary API representations must expose currency explicitly.
7. All completed monetary facts must have tests proving historical immutability.

## Related

- `ARCHITECTURE.md`
- `docs/api/API_CONTRACT.md`
- ADR-003: PostgreSQL Production Consistency Authority
- ADR-004: Secure and Idempotent Payment Webhooks
- ADR-005: Public Resource Identity
- ADR-006: API v1 Contract and Freeze Governance
- Issue #75: Multi-currency money and original-currency refund contract
