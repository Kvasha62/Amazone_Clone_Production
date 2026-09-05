# ADR-006 — Public Resource Identifiers for API v1

## Status

Accepted

## Context

API-01 inventoried every `/api/v1/` endpoint and recorded gap **G-23**,
"identifier drift across bounded contexts" (issue #73). Four different
identifier conventions coexisted, and they disagreed with each other:

* Catalog product payloads serialize `id` as the product **UUID**, while `id`
  means an integer primary key on every other resource.
* Order URLs use the public `order_number` (`ORD-000001`), but the
  cross-context write endpoints — `POST /payments/`,
  `POST /discounts/apply|remove/` and `POST /shipping/shipments/create/` —
  took the internal integer `order_id`. A client that only ever saw
  `order_number` could not call them without a second lookup.
* Shipment routes (`/shipping/shipments/{pk}/…`) exposed the raw integer PK,
  the only order-adjacent resource that still did so.
* Reviews accepted `product_id` **and** `product_uuid` as two parallel
  identifier spaces on one resource, with `product_uuid` silently ignored when
  `product_id` was present and a malformed UUID silently yielding an empty list.

API v1 cannot be frozen while the same logical entity is addressed by different
identifiers depending on which bounded context is being called.

## Problem

Choose one public identifier per resource, make every bounded context use it,
and do so without breaking clients that already send integer identifiers.

## Options Considered

### Option A — Standardize on integer primary keys everywhere

Pros:

* Simplest possible mapping; no lookup indirection.
* Uniform `id` semantics across all payloads.

Cons:

* Reverses the existing, deliberate design: `order_number` and
  `internal_tracking` exist precisely so the API does not expose sequential
  internal PKs.
* Sequential integers are enumerable, which is the class of problem F-4 (#69)
  had just closed for shipment tracking.
* Would break every existing order URL and the frontend's product `id`.

### Option B — Standardize on UUIDs everywhere

Pros:

* Uniform, non-enumerable identifiers.

Cons:

* Orders, shipments and payments have no UUID column; adding one means
  migrations, backfill and a new unique index on hot tables.
* Discards `ORD-000001` / `SHP-00000001`, which are human-readable and used in
  staff workflows and customer-facing documents (ADR-005).

### Option C — Freeze the *existing* public identifier per resource and make every context use it

Pros:

* No migrations: every chosen identifier already exists, is unique and is
  indexed (`Order.order_number`, `Shipment.internal_tracking`, `Product.uuid`).
* Keeps human-readable order/shipment numbers and the frontend's UUID product
  `id`.
* Internal PKs stop being part of the contract without a data model change.

Cons:

* Not a single uniform format across resources — a reader must consult the
  identifier table in the contract.
* Requires a deprecation window in which two identifier forms are accepted.

## Decision

**Option C.** The frozen API v1 identifier contract is:

| Resource | Public identifier | Format |
|---|---|---|
| Order | `order_number` | `ORD-000001` |
| Shipment | `internal_tracking` | `SHP-00000001` |
| Product | UUID (serialized as `id`, also exposed as `uuid`) | UUID |

Rules:

1. The public identifier is used **in paths and in request bodies alike**.
   `POST /payments/`, `POST /discounts/apply|remove/` and
   `POST /shipping/shipments/create/` accept `order_number`;
   `/shipping/shipments/{shipment}/…` accepts `SHP-*`; reviews accept
   `product_uuid`.
2. Internal integer primary keys are not part of the contract.
3. **One deprecation window.** The legacy integer references — the `order_id`
   and `product_id` request fields and an all-digit shipment path segment —
   remain accepted so existing clients keep working. They are documented as
   deprecated and will be removed in a future API version.
4. Sending a canonical identifier **and** its legacy integer counterpart in the
   same request is a `400`. Ambiguity is never resolved silently.
5. A malformed canonical identifier is a `400` (validation), not a `404` and
   not a silent empty result. An unresolvable but well-formed identifier keeps
   the existing `404` / empty-collection semantics, so the 404-not-403
   ownership policy is unaffected.
6. Responses carry the public identifier.

Shared primitives live in `apps/core/identifiers.py`
(`OrderReferenceSerializerMixin`, `order_reference_filters`, `is_order_number`,
`is_shipment_number`, `parse_uuid`) so no bounded context re-implements the
parsing rules.

## Rationale

The identifiers chosen are the ones the system already treats as public:
`order_number` is allocated from a dedicated sequence specifically because it is
a contract value (ADR-005); `internal_tracking` is generated for exactly the
same reason; `Product.uuid` is what the frontend has always consumed as `id`.
Option C therefore *documents and enforces* the intended design rather than
inventing a new one, and it needs no schema migration.

Rejecting integer PKs as public identifiers also removes an enumeration surface:
`GET /shipping/shipments/1/` was previously a guessable probe, and while
ownership scoping already returned `404`, the contract no longer invites the
attempt.

Accepting both forms during a deprecation window — rather than switching
atomically — keeps the change non-breaking, which matters because the contract
is not yet frozen and clients are in flight. Making "both at once" an error
prevents the window from creating a second, silent precedence rule of its own.

## Consequences

### Positive

* One identifier per resource across every bounded context; a client that
  received `order_number` from `POST /orders/` can drive payment, discounts and
  shipment creation with it.
* Internal PK values are no longer required knowledge for any API workflow.
* Malformed identifiers now fail loudly (`400`) instead of degrading into an
  empty list, closing the reviews half of G-14's silent-failure behaviour.
* G-23 is closed; §7 of `docs/api/API_CONTRACT.md` moves from
  "❓ DECISION REQUIRED" to a frozen contract.

### Negative / Trade-offs

* Three identifier formats, not one; readers need the §7 table.
* Two accepted forms per reference during the deprecation window, which must be
  actively removed later rather than left indefinitely.
* Resolving `order_number` / `internal_tracking` is a lookup on a unique text
  index rather than a PK lookup — negligible in practice, but no longer the
  cheapest possible query.
* `ShipmentDetailSerializer` still emits `id` (integer PK) and reviews still
  emit `product_id` for the same window; those payload fields must be dropped
  when the window closes.

## Related Issues

- #73 (API-01 / F-8 — identifier drift across bounded contexts)
- #65 (API-01 — API v1 contract, gap G-23)
- #69 (API-01 / F-4 — `internal_tracking` is not a public tracking key)

## Related PRs

- API-01/F-8 implementation PR

## Supersedes / Superseded By

N/A
