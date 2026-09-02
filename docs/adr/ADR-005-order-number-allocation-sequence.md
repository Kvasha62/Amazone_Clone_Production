# ADR-005 — Allocate Order Numbers from a PostgreSQL Sequence

## Status

Accepted

## Context

`Order.order_number` is the public identifier of an order (`ORD-000123`). It is
used in the API (`GET /api/v1/orders/{order_number}/`), in staff workflows, in
payments and shipping coordination, and on customer-facing documents, so its
format is a contract, and its uniqueness is enforced by the database
(`order_number` is `unique=True`).

Order numbers were allocated in application code by reading the maximum
`Order._order_number_seq` and adding one, both in `Order.save()` and in
`OrderService.create_from_cart()`. That is a read-then-increment: two
transactions running in parallel could read the same maximum and both attempt
to insert the same number. The `UNIQUE` index on `order_number` prevented a
duplicate row, but it converted the race into a failed checkout: the losing
`INSERT` raised `IntegrityError`, the transaction became aborted, and the retry
inside the same `atomic` block could not execute another query
(`TransactionManagementError`).

`ARCHITECTURE.md` requires `select_for_update()` for concurrency-sensitive
state, but there is no existing row to lock for a value that does not exist
yet, so row locking does not apply to number allocation.

## Decision

Allocate order numbers from a PostgreSQL sequence, `orders_order_number_seq`,
created by migration `orders.0003_order_number_sequence` and seeded from
`MAX(_order_number_seq)` so existing orders keep their numbers.

`Order.save()` is the single allocation point: when `order_number` is empty it
calls `allocate_order_number()`, which executes
`SELECT nextval('orders_order_number_seq')`, stores the numeric value in
`_order_number_seq`, and formats the public number from it. The service layer
no longer reads a maximum and no longer retries on `IntegrityError`.

Uniqueness remains enforced by the existing `UNIQUE` index on `order_number`.
The number format, field type, serialization, and API semantics are unchanged.

## Consequences

### Positive

- Concurrent order creation cannot allocate the same number: `nextval()` is
  atomic in the database and returns a distinct value to every caller, without
  application-level locking or read-then-increment.
- Allocation costs one indexed database call and takes no row locks, so it does
  not serialize checkouts behind a shared row.
- Every order-creation path shares one allocation rule (`Order.save()`):
  checkout, admin add, management commands, and fixtures.
- The public contract is preserved: `ORD-` plus a zero-padded six-digit
  numeric part, still backed by `_order_number_seq`.

### Negative / Trade-offs

- `nextval()` is not transactional. A rolled-back order creation consumes its
  value, so order numbers are unique and increasing but not gapless. Gapless
  numbering is not a business requirement, and closing gaps would require
  serializing all checkouts behind one row.
- Allocation now depends on a database object created by a migration. A
  database that has not applied `orders.0003` cannot allocate numbers.
- Backends without sequences (the SQLite development fallback in
  `config/settings.py`) keep a documented `MAX()+1` fallback and rely on the
  `UNIQUE` index. That path is development-only; PostgreSQL is the production
  database in CI and `docker-compose.prod.yml`.

## Alternatives Considered

- Keep `MAX()+1` and retry on `IntegrityError`. Rejected: the retry executes
  inside the aborted transaction, so it cannot recover, and the attempt itself
  is the race.
- `select_for_update()` around a maximum read. Rejected: there is no existing
  row representing the next number to lock, and locking an arbitrary order row
  would serialize unrelated checkouts.
- A dedicated counter table updated with `UPDATE ... RETURNING`. Workable, but
  it adds a model, a migration, and a hot row that serializes all order
  creation, for no gain over a native sequence.
- Derive the number from the primary key or a UUID. Rejected: it changes the
  public number format, which is an existing contract.
- Database-level formatting via a default expression. Rejected: the numeric
  value is needed in the model instance to keep `order_number` and
  `_order_number_seq` consistent.

## References

- `ARCHITECTURE.md` — “Concurrency-Safe State Transitions” and `orders`.
- `apps/orders/models/order.py` — `allocate_order_number()` and `Order.save()`.
- `apps/orders/migrations/0003_order_number_sequence.py` — sequence creation
  and seeding.
- `apps/orders/services/order_service.py` — `OrderService.create_from_cart()`.
- `apps/orders/tests/test_order_number_concurrency.py` — cross-connection
  concurrency and rollback coverage.
- Issue #19 (PROD-010).
