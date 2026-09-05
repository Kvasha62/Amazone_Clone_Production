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
  the only order-adjacent resource that still did so. Shipment had **no**
  public identifier at all: the only candidates were the integer PK and
  `internal_tracking`, an internal warehouse field.
* Reviews accepted `product_id` **and** `product_uuid` as two parallel
  identifier spaces on one resource, with `product_uuid` silently ignored when
  `product_id` was present and a malformed UUID silently yielding an empty list.
* Payment stored its own `PAY-` number in a model field named `order_number`
  and serialized it under the key `order_number`, so the payload advertised a
  payment number where an order reference was contractually required — and
  carried no order reference at all.

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

* Almost no migrations: most chosen identifiers already exist, are unique and
  indexed (`Order.order_number`, `Product.uuid`). Shipment is the exception —
  it genuinely lacked a public identifier, so one is added (see below).
* Keeps human-readable order/shipment numbers and the frontend's UUID product
  `id`.
* Internal PKs stop being part of the contract without a data model change.

Cons:

* Not a single uniform format across resources — a reader must consult the
  identifier table in the contract.
* Requires a deprecation window in which two identifier forms are accepted.

## Decision

**Option C.** The frozen API v1 identifier contract is:

| Resource | Canonical public identifier | Format |
|---|---|---|
| Product | `id` | UUID |
| ProductVariant | `id` | integer |
| Order | `order_number` | `ORD-000001` |
| Payment | `payment_number` | `PAY-000001` |
| Shipment | `shipment_number` | `SHP-00000001` |
| Review | `id` (integer); product reference `product_id` | integer / UUID |
| Coupon | `id` (integer); `code` is the business identifier | integer |
| User | `id` | integer |

**Shipment has three distinct identifiers — they are not interchangeable:**

| Field | Role | Public? |
|---|---|---|
| `shipment_number` | Canonical public identifier of the resource; immutable, server-generated from a sequence | **Yes** — paths and payloads |
| `tracking_number` | **External** carrier tracking code; supplied by the delivery service, may change or be absent | Yes, as data — never as the resource address |
| `internal_tracking` | Internal warehouse field | **No** — never serialized publicly |

`shipment_number` is a **new column**, not a rename of `internal_tracking`;
reusing the internal field as the public identifier would have merely renamed
the problem. Migration `shipping/0003_shipment_number` adds the column,
backfills existing rows in `pk` order, then applies the `UNIQUE` constraint and
creates the sequence — the constraint is applied only after the data is
populated, so the migration is safe on a non-empty table.

Payment's model field was renamed `order_number` → `payment_number`
(`payments/0006_…`, a `RenameField`, so existing numbers are preserved); the
order reference is serialized from the FK as `order_number`.

Rules:

1. The public identifier is used **in paths and in request bodies alike**.
   `POST /payments/`, `POST /discounts/apply|remove/` and
   `POST /shipping/shipments/create/` accept `order_number`;
   `/shipping/shipments/{shipment_number}/…` accepts `SHP-*` only; reviews
   reference products by `product_id`, **typed as UUID**. There is no
   `product_uuid` field — one reference, one key, one type.
2. Internal integer primary keys are not part of the contract.
3. **One deprecation window, for `order_id` only.** The legacy `order_id`
   request field remains accepted because clients demonstrably used it. It is
   documented as deprecated and will be removed in a future API version.

   No such window exists for shipments or reviews: an integer shipment path
   segment and an integer `product_id` were never public identifiers (the
   catalog has always published product UUIDs), so accepting them would
   *create* the enumerable second addressing scheme this ADR removes. Both
   are rejected — the shipment path with `404`, an integer `product_id` with
   `400`.
4. Sending a canonical identifier **and** its legacy integer counterpart in the
   same request is a `400`. Ambiguity is never resolved silently.
5. A malformed canonical identifier is a `400` (validation), not a `404` and
   not a silent empty result. An unresolvable but well-formed identifier keeps
   the existing `404` / empty-collection semantics, so the 404-not-403
   ownership policy is unaffected.
6. **Identifier parsing is ASCII-strict.** The `ORD-`/`SHP-` patterns and the
   legacy-PK parser accept `[0-9]` only, never Python's `\d` /
   `str.isdigit()`, both of which also match non-ASCII digits. A legacy PK must
   additionally be a positive integer within `bigint` range. Anything else can
   never identify a row and therefore yields the canonical `404` — it must not
   reach `int()` or the database.
7. Responses carry the public identifier.

Shared primitives live in `apps/core/identifiers.py`
(`OrderReferenceSerializerMixin`, `order_reference_filters`, `is_order_number`,
`is_shipment_number`, `parse_legacy_pk`, `parse_uuid`) so no bounded context
re-implements the parsing rules — in particular, no view calls bare `int()` on
a client-supplied path segment.

## Rationale

The identifiers chosen are the ones the system already treats as public:
`order_number` is allocated from a dedicated sequence specifically because it is
a contract value (ADR-005), and `Product.uuid` is what the frontend has always
consumed as `id`. Option C therefore *documents and enforces* the intended
design rather than inventing a new one.

Shipment is the one place where a new field was unavoidable. `internal_tracking`
looks like a public number (`SHP-*`) but is an internal warehouse field, and
F-4 (#69) had already ruled it must not be a public lookup key; promoting it
would have contradicted that decision. `shipment_number` is therefore allocated
from its own sequence using the same ADR-005 mechanism as `order_number`, which
additionally replaces the previous `MAX()+1` read-then-increment generation —
a race that could produce duplicate numbers under concurrent inserts.

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
* ASCII-strict parsing removes a `500` (a superscript digit such as `²` passes
  `str.isdigit()` but crashes `int()`) and an aliasing bug (Arabic-Indic `٤٢`
  resolving to the same row as `42`), in the same spirit as F-1 (#85).
* G-23 is closed; §7 of `docs/api/API_CONTRACT.md` moves from
  "❓ DECISION REQUIRED" to a frozen contract.

### Negative / Trade-offs

* Three identifier formats, not one; readers need the §7 table.
* Two accepted forms per reference during the deprecation window, which must be
  actively removed later rather than left indefinitely.
* Resolving `order_number` / `shipment_number` is a lookup on a unique text
  index rather than a PK lookup — negligible in practice, but no longer the
  cheapest possible query.
* Shipment gains a column, an index and a sequence — the one schema cost of
  Option C, accepted because the alternative was publishing either an
  enumerable PK or an internal field.
* Renaming `Payment.order_number` → `payment_number` changes a payload key
  (`order_number` now carries the *order's* number). This is a corrective
  breaking change: the previous payload was internally inconsistent with the
  contract it is meant to satisfy.

## Related Issues

- #73 (API-01 / F-8 — identifier drift across bounded contexts)
- #65 (API-01 — API v1 contract, gap G-23)
- #69 (API-01 / F-4 — `internal_tracking` is not a public tracking key)

## Related PRs

- API-01/F-8 implementation PR

## Supersedes / Superseded By

N/A
