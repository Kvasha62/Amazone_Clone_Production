# Production Baseline — PROD-000

## Approved baseline commit

| Field | Value |
|---|---|
| Repository | `Kvasha62/Amazone_Clone_Production` |
| Branch | `main` |
| Approved baseline SHA | `38648bddb82c8cd4848e03e930a46e499e4e017e` |
| Baseline commit title | `Merge pull request #23 from Kvasha62/arena/01a0538f-amazone-clone` |
| CI workflow | `.github/workflows/ci.yml` (workflow name: `CI`) |
| CI status on baseline SHA | ✅ green (check run `ci` — `completed` / `success`) |
| Approved on | 2026-09-01 |
| Ticket | PROD-000 |

Production `main` points exactly at this SHA. This commit is the frozen
reference point for every subsequent production change: releases, hotfixes and
audits are described relative to it.

## Verification performed (read-only)

1. `git rev-parse origin/main` → `38648bddb82c8cd4848e03e930a46e499e4e017e` — matches the approved SHA.
2. `.github/workflows/ci.yml` exists and is unmodified.
3. GitHub check runs for the baseline SHA report conclusion `success`.
4. Source repository `Kvasha62/Amazone_Clone` was not touched in any way.

## Baseline scope

The baseline covers the repository tree at the SHA above, including:

- Django backend (`config/`, `apps/`, `manage.py`, `requirements.txt`)
- Container definitions (`Dockerfile.backend`, `Dockerfile.frontend`, `docker-compose.yml`)
- CI definition (`.github/workflows/ci.yml`)
- Documentation (`ARCHITECTURE.md`, `docs/`)

## Change of baseline

The baseline SHA may only be advanced by a dedicated `PROD-xxx` ticket that:

- names the new SHA explicitly,
- proves CI is green on it,
- updates this document in the same pull request.

No other change may modify the baseline record.

## Baseline advance — PROD-003 (in review, PR #5)

| Field | Value |
|---|---|
| Ticket | PROD-003 — Make order↔inventory↔payment coordination fail-safe |
| Pull request | #5 (`Kvasha62/Amazone_Clone_Production`) |
| Branch | `arena/01a05e31-amazone-clone-production` |
| New baseline SHA (final code head of PR #5) | `df74d6a945fe813ad02a7818409173ef2df6b742` |
| Commit title | `PROD-003: move inventory concurrency fixes into InventoryService (remove monkey-patch)` |
| CI status on this SHA | ✅ green — check run `ci` `completed` / `success`, GitHub Actions run `33594177345` |
| Test suite | `Ran 1159 tests in 120.694s — OK` (PostgreSQL 18.6, Python 3.13.15, Django 6.1); migrations check OK |
| Approved on | 2026-09-02 |

Scope of PROD-003 (all in this PR, CI green on the head SHA):

- Fail-safe coordination `order ↔ inventory ↔ payment`:
  inventory transitions lock the `Order` row first and pair
  RESERVE/RELEASE/OUT movements (idempotent reserve/release/commit,
  no double-decrement under concurrency — covered by
  `apps/inventory/tests/test_idempotency.py`);
- all inventory concurrency fixes (release/commit race exclusion,
  DELIVERED reconciliation with reserve recovery) live canonically in
  `apps/inventory/services/inventory_service.py` — the runtime
  monkey-patching module `apps/inventory/services/prod003_ci_fixes.py`
  is removed and its reintroduction is guarded by tests
  (`InventoryServiceCanonicalImplementationTests`);
- refund failures are never lost: `refund_required_amount` +
  `refund_failed` events, `retry_pending_refunds` settles them
  idempotently; durable recording writes the obligation through an
  independent connection and survives rollback of the carrier
  transaction (`apps/payments/tests/test_refund_recovery.py`);
- `OrderConfirmationError` + webhook 502/200 contract for
  payment↔order confirmation recovery;
- management commands `retry_pending_refunds`,
  `reconcile_order_coordination`.

`main` is not modified or merged by this ticket: it currently points at
`3fff49f158cf2aa6f93fd5bf98053c60de57c4b2` and the PROD-000 record above
remains the frozen reference for `main`; the advance recorded here takes
effect when PR #5 is merged. This record is certified by a docs-only
commit on top of the SHA named above; CI is green on both commits.

