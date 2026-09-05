# ADR-010 — Payment Provider Port, Adapter and Router

- **Status:** Accepted
- **Date:** 2026-09-06
- **Scope:** payments, provider integrations, webhooks, refunds

## Context

The current payment implementation is tightly coupled to a mock provider and does not establish a durable abstraction for provider capabilities, routing, provider-scoped transaction identity, or refund execution.

## Decision

1. `payments` owns the Payment Provider Port, provider registry and deterministic `PaymentRouter`.
2. Concrete providers are infrastructure adapters behind the port. Order, Pricing, Cart and other upstream domains never import concrete adapters.
3. The client may request a payment currency and method, but cannot select an arbitrary provider. Server-side routing selects an enabled `PaymentProviderProfile` based on market, country, currency, method, capabilities, amount limits and explicit priority rules.
4. Provider capabilities are explicit and include supported countries, currencies, payment methods, refunds, partial refunds and webhook support where applicable.
5. Provider transaction identifiers originate from the provider adapter. They are unique within the provider/profile namespace. Webhook correlation establishes provider/profile context before mutating a Payment.
6. Existing ADR-004 timestamp/nonce/HMAC replay protection and durable nonce claim remain mandatory. Provider-specific signature verification is additional and cannot replace business idempotency.
7. The selected provider for a refund is the provider owning the captured transaction unless a separately approved migration/recovery mechanism exists.
8. The refund adapter receives the exact buyer-facing charged amount and charged currency established by the original Payment. The adapter cannot silently convert, reduce or substitute the obligation.
9. Provider credentials are represented by a secret/configuration reference and are never persisted as plaintext business data or exposed through API/Admin.
10. `PaymentRouter` is side-effect-free and deterministic; provider calls happen only after routing and validation.

## Webhook correlation

A provider/profile mismatch is rejected and cannot mutate another provider's Payment. Any migration from the current global external-ID uniqueness must preserve historical lookup and ADR-004 idempotency in one coordinated change.

## Non-goals

This ADR does not select the first real provider, FX vendor, tax regime, or secret-store technology.

## Related

- ADR-004 — Secure and Idempotent Payment Webhooks
- ADR-008 — Money, Currency and Refund Invariants
- ADR-009 — Currency Registry and Monetary Precision
- Issue #94 — Payment Provider Port/Adapter/Router
- Issue #99 — First real payment provider selection
- Issue #100 — Provider credentials
