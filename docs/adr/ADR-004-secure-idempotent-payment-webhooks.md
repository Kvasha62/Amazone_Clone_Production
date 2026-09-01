# ADR-004 — Authenticate and Idempotently Process Payment Webhooks

## Status

Accepted

## Context

Payment providers call the webhook endpoint without an application JWT, so
`/api/v1/payments/webhook/` must accept external requests. At the same time,
an unauthenticated endpoint must not allow a caller to change payment state,
and providers may redeliver events.

The current provider is a mock provider, but the endpoint already performs
HMAC verification and delegates status handling to the payment service.

## Decision

Keep the webhook endpoint `AllowAny` only because it authenticates each
request with an HMAC-SHA256 signature over the raw request body. It obtains
its secret from `PAYMENT_WEBHOOK_SECRET`, reads `X-Webhook-Signature`, compares
with `hmac.compare_digest()`, and returns HTTP 403 before payload validation
or state processing when the secret or signature is absent or invalid.

After verification, the endpoint passes validated data to
`PaymentService.handle_webhook()`. Payment status transitions lock the
`Payment` row, and confirmation treats an already-succeeded payment as an
idempotent no-op. Unknown payments return HTTP 200 after logging so the
provider does not retry indefinitely.

## Consequences

### Positive

- External providers can call the endpoint without a user session.
- Unsigned, altered, and incorrectly signed bodies cannot transition payment
  state.
- Constant-time comparison avoids a timing-sensitive signature equality check.
- Duplicate successful notifications do not duplicate the payment transition.

### Negative / Trade-offs

- Deployment must configure and protect `PAYMENT_WEBHOOK_SECRET`; no secret
  means all webhooks are rejected.
- This implementation uses the repository's generic HMAC contract; a real
  provider integration may require provider-specific verification in a future
  change.
- Returning 200 for an unknown payment accepts that delivery rather than
  surfacing it as a retryable provider error.

## Alternatives Considered

- Require JWT authentication. Rejected because a payment provider does not
  hold an application user's JWT.
- Accept the endpoint without a signature. Rejected because an arbitrary
  caller could submit a payment state change.
- Compare signatures with normal string equality. Rejected in favor of
  `hmac.compare_digest()`.
- Treat duplicate success events as errors. Rejected because webhook delivery
  is at least once and successful confirmation is idempotent.

## References

- `ARCHITECTURE.md` — “payments” and “Concurrency & Transaction Safety”.
- `apps/payments/api_views/payment_views.py` — `PaymentWebhookView` signature
  verification and dispatch.
- `apps/payments/services/payment_service.py` — webhook handling and locked,
  idempotent payment confirmation.
- `apps/payments/tests/test_webhook_security.py` — signature rejection and
  accepted-request coverage.
- `.env.example` — `PAYMENT_WEBHOOK_SECRET` deployment configuration.
- Issue #24 (PROD-001).
