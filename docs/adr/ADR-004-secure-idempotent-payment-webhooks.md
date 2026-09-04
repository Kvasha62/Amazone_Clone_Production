# ADR-004 — Authenticate and Idempotently Process Payment Webhooks

## Status

Accepted

**Amended by Issue #71 (API-01 / F-6):** the original decision authenticated
webhooks with an HMAC-SHA256 signature over the raw request body only. A
correctly signed, captured request could be replayed indefinitely. The
Decision below records the amended protocol: a signed **timestamp** and a
one-time **nonce** are now part of the signed payload, with a ±300 s
freshness window and a durable, race-safe nonce store.

## Context

Payment providers call the webhook endpoint without an application JWT, so
`/api/v1/payments/webhook/` must accept external requests. At the same time,
an unauthenticated endpoint must not allow a caller to change payment state,
providers may redeliver events, and — the gap this amendment closes — an
attacker who captured one validly signed webhook can resend it at any later
time, because the signature over the raw body alone does not change.

The current provider is a mock provider, but the endpoint already performs
HMAC verification and delegates status handling to the payment service.

## Decision

### Endpoint posture

The webhook endpoint stays `AllowAny` **only because** it authenticates each
request in its own transport layer. It obtains its secret from
`PAYMENT_WEBHOOK_SECRET`, compares digests with `hmac.compare_digest()`
(timing-safe), and returns HTTP 403 before payload validation or state
processing when any transport check fails.

### Transport headers (all required)

* `X-Webhook-Timestamp` — Unix epoch **seconds**, UTC. Format: ASCII decimal
  integer, no leading zeros (except bare `0`), at most 20 digits.
* `X-Webhook-Nonce` — unpredictable one-time identifier. Format:
  `[A-Za-z0-9_-]`, length 1–128 (arbitrarily large values are rejected).
* `X-Webhook-Signature` — lowercase hex `HMAC-SHA256` digest, exactly 64
  characters.

### Freshness window

`abs(server_now − webhook_timestamp) <= 300` seconds (±5 minutes). Requests
older than the window **and** requests more than the window in the future are
rejected.

### Exact HMAC input

The signature is computed over the canonical concatenation of the timestamp
bytes, the nonce bytes, and the **raw request body bytes**:

```python
signed_payload = (
    timestamp.encode("ascii")
    + nonce.encode("ascii")
    + request.body
)
expected = hmac.new(
    secret.encode("utf-8"), signed_payload, hashlib.sha256
).hexdigest()
```

`timestamp`/`nonce` are the exact header strings; `request.body` is the raw
body (no JSON re-serialization, normalization, or whitespace changes —
`request.data` / serialized JSON is never signed). The construction lives in
`apps/payments/services/webhook_security.compute_webhook_signature` and the
test helper imports that same function, so tests sign byte-identically to
production.

### Timing-safe comparison

The received signature is compared with `hmac.compare_digest()`; a non-hex
or non-64-char value is rejected at the format stage before comparison.

### Fail-closed secret behavior

If `PAYMENT_WEBHOOK_SECRET` is unset/empty, **all** webhook requests are
rejected (403). Missing headers, malformed headers, stale timestamps,
signature mismatch, and reused nonces are all rejected as well.

### Nonce uniqueness, persistence, and race-safe claim

Each accepted nonce is persisted in `PaymentWebhookNonce`
(migration `payments/0005_paymentwebhooknonce`):

* `nonce` — UNIQUE (database-level unique index);
* `webhook_timestamp` — the accepted `X-Webhook-Timestamp` (epoch seconds);
* `created_at` — server-side creation timestamp (from `BaseModel`).

The claim is an atomic INSERT; a duplicate INSERT fails the UNIQUE constraint
with `IntegrityError`. The check-then-insert pattern (`exists()` → `create()`)
is deliberately **not** used: under parallel requests it is racy. The
database is the single arbiter — exactly one concurrent INSERT of a given
nonce commits, all others observe `IntegrityError` and are rejected.

### Durable claim (independent transaction boundary)

The nonce claim is committed in its **own** transaction **before** business
processing starts:

```text
transaction A: validate/claim nonce  → COMMIT
transaction B: PaymentService.handle_webhook()   (own atomic)
```

If transaction B rolls back (e.g. order confirmation failure → 502), the
nonce claim in transaction A **remains committed**. A retry of the same
webhook (same nonce) is therefore rejected; a legitimate provider
redelivery must use a new nonce and is handled by business idempotency.

### Neutral security errors

All transport failures return the **same** `403` canonical
`permission_denied` envelope (same `error.code`/`message`/`details`). The
rejection does not distinguish a stale timestamp, a reused nonce, or a bad
signature, and never reveals the secret, the expected or received signature,
the nonce value, DB errors, or tracebacks. The secret and signature are not
logged.

### Two independent protection levels

* **Transport-level replay protection** — timestamp + nonce + HMAC over the
  signed payload. Defeats redelivery of the same signed message.
* **Business-level idempotency** — `Payment.external_id` (unique constraint,
  migration `payments/0004_payment_external_id_unique`) plus
  `PaymentService.handle_webhook` treating an already-succeeded payment as an
  idempotent no-op. Absorbs legitimate at-least-once delivery of the same
  payment event (each redelivery carrying a fresh nonce).

The two mechanisms are complementary and neither replaces the other: the
transport layer cannot deduplicate a *new* delivery of the same payment
event, and business idempotency cannot stop a *replayed* signed request.

### Nonce retention / cleanup policy

A nonce with `webhook_timestamp = ts` can only ever be replayed while
`server_now − ts <= 300`. Once `ts < server_now − WEBHOOK_NONCE_RETENTION_SECONDS`
(retention = 300 s tolerance + 60 s safety margin), the nonce is guaranteed
useless. `cleanup_webhook_nonces` (management command; Celery task
`apps.payments.tasks.cleanup_webhook_nonces` scheduled by Celery Beat every
15 minutes) deletes exactly those rows. The command refuses
`--retention-seconds` smaller than the 300 s freshness window, so retention
can never be misconfigured into replaying a still-fresh nonce. The nonce
table therefore holds only a short-lived window (~6 minutes) of used nonces.

## Consequences

### Positive

* External providers can call the endpoint without a user session.
* Unsigned, altered, and incorrectly signed bodies cannot transition payment
  state.
* A captured request stops being valid after the ±300 s freshness window,
  and a valid one becomes unusable after a single delivery (one-time nonce).
* Concurrent duplicate nonces are resolved by the database: exactly one wins.
* A failed business transaction cannot "un-use" a nonce — retrying the same
  webhook after a 502/rollback is rejected.
* Constant-time comparison avoids a timing-sensitive signature equality check.
* Duplicate successful notifications (fresh nonce) do not duplicate the
  payment transition (business idempotency on `external_id`).
* The nonce store is bounded by the cleanup policy.

### Negative / Trade-offs

* Deployment must configure and protect `PAYMENT_WEBHOOK_SECRET`; no secret
  means all webhooks are rejected.
* Providers must be updated to send `X-Webhook-Timestamp` and
  `X-Webhook-Nonce` and to re-sign over `timestamp || nonce || body`; a
  provider redelivery must use a new nonce.
* Each webhook adds one small row (nonce) that is cleaned up minutes later —
  negligible storage/IO, but it is a new table and a new periodic task.
* This implementation uses the repository's generic HMAC contract; a real
  provider integration may require provider-specific verification in a future
  change.
* Returning 200 for an unknown payment accepts that delivery rather than
  surfacing it as a retryable provider error.

## Alternatives Considered

* Require JWT authentication. Rejected because a payment provider does not
  hold an application user's JWT.
* Accept the endpoint without a signature. Rejected because an arbitrary
  caller could submit a payment state change.
* Compare signatures with normal string equality. Rejected in favor of
  `hmac.compare_digest()`.
* Treat duplicate success events as errors. Rejected because webhook delivery
  is at least once and successful confirmation is idempotent.
* Replay protection via `exists()`-then-`create()`. Rejected: racy under
  parallel requests; the UNIQUE-constraint INSERT is the race-safe
  primitive.
* Storing claimed nonces in Redis/cache. Rejected: the durable guarantee
  (claim survives business rollback; replay rejected after process restart)
  requires the database, which is already the source of truth for payments.
* Signing a `timestamp + body` scheme without a nonce (e.g. GitHub-style).
  Rejected: without a one-time nonce, any request signed within the window
  remains replayable until the window closes; the nonce makes each delivery
  single-use.

## References

* `ARCHITECTURE.md` — "payments" and "Concurrency & Transaction Safety".
* `docs/api/API_CONTRACT.md` §11.3 — normative webhook header/signature/
  replay/error contract.
* `apps/payments/api_views/payment_views.py` — `PaymentWebhookView` security
  pipeline (secret → headers → formats → freshness → HMAC → claim).
* `apps/payments/services/webhook_security.py` — timestamp/nonce/signature
  validation, canonical HMAC, durable race-safe claim.
* `apps/payments/models/webhook_nonce.py` +
  `apps/payments/migrations/0005_paymentwebhooknonce.py` — nonce store.
* `apps/payments/management/commands/cleanup_webhook_nonces.py` +
  `apps/payments/tasks.py` + `config/celery.py` — retention/cleanup.
* `apps/payments/tests/test_webhook_security.py` — signature, timestamp,
  nonce, idempotency, durability, concurrency, and leakage coverage.
* `apps/payments/tests/webhook_helpers.py` — single shared test helper that
  signs with the production function.
* `.env.example` — `PAYMENT_WEBHOOK_SECRET` deployment configuration.
* Issue #24 (PROD-001) — original webhook security.
* Issue #71 (API-01 / F-6) — replay protection amendment.
