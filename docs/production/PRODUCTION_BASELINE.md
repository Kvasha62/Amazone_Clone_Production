# Production Baseline — PROD-013

## Approved baseline commit

| Field | Value |
|---|---|
| Repository | `Kvasha62/Amazone_Clone_Production` |
| Branch | `main` |
| Approved baseline SHA | `1b4f069c159b198d30ee82a71b65198ce11bd2b7` |
| Baseline commit title | `Merge pull request #24 from Kvasha62/arena/01a06248-amazone-clone-production` |
| CI workflow | `.github/workflows/ci.yml` (workflow name: `CI`) |
| CI status on baseline SHA | ✅ green — GitHub Actions run #42 / ID `33636809167`, job/check `ci` (ID `100269608813`) — `completed` / `success` |
| Verified CI evidence on baseline SHA | Workflow `CI`; Django system checks, migration check, migration application, and the full test suite all succeeded; run completed 2026-09-02T13:40:15Z |
| Approved on | 2026-09-02 |
| Ticket | PROD-013 |

Production `main` points exactly at this SHA. This commit is the current
production source-of-truth commit and the frozen reference point for every
subsequent production change. `main` at
`1b4f069c159b198d30ee82a71b65198ce11bd2b7` is the authoritative production
state. It is the merge commit for completed PROD-012 (PR #24). Earlier baseline
records are preserved below as history.

## Verification performed (read-only)

1. GitHub `main`, local `main`, and `origin/main` all resolve to `1b4f069c159b198d30ee82a71b65198ce11bd2b7` — an exact match with the current production baseline SHA (verified 2026-09-02).
2. GitHub Actions workflow `CI`, run #42 / ID `33636809167`: `head_sha` = `1b4f069c159b198d30ee82a71b65198ce11bd2b7` (exact match), event `push`, status `completed`, conclusion `success`, completed 2026-09-02T13:40:15Z; job `ci` (ID `100269608813`) is `completed` / `success`.
3. Every CI job step succeeded on the baseline SHA: `Checkout repository`, `Set up Python 3.13`, `Install dependencies`, `Django system checks`, `Migration check`, `Apply migrations`, and `Run tests` all concluded `success`.
4. PROD-012 / PR #24 is `MERGED` / completed (merged 2026-09-02T13:36:56Z); its merge commit is exactly `1b4f069c159b198d30ee82a71b65198ce11bd2b7`. CI on its final PR HEAD `051f6ab31fdede888446c65868c00a92ded234af` was also green (run #41 / ID `33636174754`, check `ci` ID `100267488189` — `completed` / `success`).
5. `.github/workflows/ci.yml` exists and is unmodified by this ticket.
6. Source repository `Kvasha62/Amazone_Clone` was not touched in any way.

## Baseline advance — PROD-013 (this document)

| Field | Value |
|---|---|
| Ticket | PROD-013 — Advance production baseline after PROD-012 (Issue #25) |
| Status | Documentation-only governance change; pending Architect review and Owner merge |
| Change class | This pull request changes only `docs/production/PRODUCTION_BASELINE.md` |
| Advance | Previous baseline `406a96c37b0d0249ea7f44b459a54dca8566561b` → `1b4f069c159b198d30ee82a71b65198ce11bd2b7` (`main`) |
| Completed production change | PROD-012 / PR #24 — merged 2026-09-02 |
| Verified on | 2026-09-02 |

PROD-012 is recorded as completed/merged via PR #24
(`PROD-012: Harden ShippingService synchronous exception handling (F-14)`).
Its ShippingService synchronous exception-handling hardening (F-14) is part of
the verified `main` state. No functional, model, schema, migration, dependency,
CI workflow, deployment, or educational-repository changes are introduced by
PROD-013. All other remaining audit findings are outside this ticket and remain
unchanged. Architect review is required before the Owner performs the final
merge; the Architect/Assistant must not merge this governance pull request.

## Historical baseline record — PROD-011 (superseded)

This record was current immediately before PROD-013. It is preserved for audit
and is now superseded by the approved baseline at the top of this document.

| Field | Value |
|---|---|
| Repository | `Kvasha62/Amazone_Clone_Production` |
| Branch | `main` |
| Approved baseline SHA | `406a96c37b0d0249ea7f44b459a54dca8566561b` |
| Baseline commit title | `Merge pull request #20 from Kvasha62/arena/01a0620b-amazone-clone-production` |
| CI workflow | `.github/workflows/ci.yml` (workflow name: `CI`) |
| CI status on baseline SHA | ✅ green — GitHub Actions run #38 / ID `33633045879`, job/check `ci` (ID `100256993637`) — `completed` / `success` |
| Verified CI evidence on baseline SHA | Workflow `CI`; Django system checks, migration check, migrations, and tests all succeeded; run completed 2026-09-02 |
| Approved on | 2026-09-02 |
| Ticket | PROD-011 |

## Baseline advance — PROD-011 (merged; record preserved)

| Field | Value |
|---|---|
| Ticket | PROD-011 — Advance production baseline after PROD-010 (Issue #21) |
| Status | Documentation-only governance change; merged and completed |
| Change class | That pull request changed only `docs/production/PRODUCTION_BASELINE.md` |
| Advance | Previous baseline `27fc2c3b2aca17db8656e4381464ff52022fab70` → `406a96c37b0d0249ea7f44b459a54dca8566561b` (`main`) |
| Completed production change | PROD-010 / PR #20 — merged 2026-09-02 |
| Verified on | 2026-09-02 |

PROD-010 is recorded as completed/merged via PR #20. Its PostgreSQL-sequence
order-number allocation and concurrency fix (F-13) are part of the verified
`main` state. No functional, model, schema, migration, dependency, CI workflow,
deployment, or educational-repository changes were introduced by PROD-011.

## Historical baseline record — PROD-009 (superseded)

This record was current immediately before PROD-011. It is preserved for audit
and is now superseded by the approved baseline at the top of this document.

| Field | Value |
|---|---|
| Repository | `Kvasha62/Amazone_Clone_Production` |
| Branch | `main` |
| Approved baseline SHA | `27fc2c3b2aca17db8656e4381464ff52022fab70` |
| Baseline commit title | `Merge pull request #16 from Kvasha62/arena/01a06191-amazone-clone-production` |
| CI workflow | `.github/workflows/ci.yml` (workflow name: `CI`) |
| CI status on baseline SHA | ✅ green — GitHub Actions run ID `33621123578`, job/check `ci` (ID `100218124214`) — `completed` / `success` |
| Verified CI evidence on baseline SHA | Workflow `CI`; all setup, checks, migration, and test steps successful; run completed 2026-09-02 |
| Approved on | 2026-09-02 |
| Ticket | PROD-009 |

## Baseline advance — PROD-009 (this document)

| Field | Value |
|---|---|
| Ticket | PROD-009 — Advance production baseline after PROD-007 and PROD-008 (Issue #17) |
| Status | Documentation-only governance change; pending review and owner merge |
| Change class | This pull request changes only `docs/production/PRODUCTION_BASELINE.md` |
| Advance | Previous baseline `78981863e20e4be705480157402156b455e77211` → `27fc2c3b2aca17db8656e4381464ff52022fab70` (`main`) |
| Verified on | 2026-09-02 |

PROD-007 is recorded as completed/merged via PR #13. Its production settings
hardening is part of the verified `main` state. PROD-008 is recorded as
completed/merged via PR #16. Its production deployment configuration is part
of the verified `main` state. No functional, schema, dependency, CI workflow,
or deployment implementation changes are introduced by PROD-009.

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

## Historical baseline record — PROD-000 (superseded)

Verbatim record established by PROD-000 on 2026-09-01, preserved unchanged for
audit. It is superseded by the approved baseline above and no longer describes
the current production state.

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

Statement as approved on 2026-09-01:

> Production `main` points exactly at this SHA. This commit is the frozen
> reference point for every subsequent production change: releases, hotfixes
> and audits are described relative to it.

Verification performed (read-only) at that time:

1. `git rev-parse origin/main` → `38648bddb82c8cd4848e03e930a46e499e4e017e` — matches the approved SHA.
2. `.github/workflows/ci.yml` exists and is unmodified.
3. GitHub check runs for the baseline SHA report conclusion `success`.
4. Source repository `Kvasha62/Amazone_Clone` was not touched in any way.

## Baseline advance — PROD-003 (merged via PR #5)

| Field | Value |
|---|---|
| Ticket | PROD-003 — Make order↔inventory↔payment coordination fail-safe |
| Status | ✅ MERGED / completed on 2026-09-02 — not in review, not open |
| Pull request | #5 (`Kvasha62/Amazone_Clone_Production`) — MERGED |
| Branch | `arena/01a05e31-amazone-clone-production` |
| Merge commit on `main` | `e27b1e1cc5ab319f7f684a5bc13ceafe7bc9916b` — `Merge pull request #5 from Kvasha62/arena/01a05e31-amazone-clone-production` |
| CI on merge commit | ✅ green — GitHub Actions run #17 / ID `33595830211`, check run `ci` `completed` / `success` |
| Final PR HEAD SHA | `7a7abca5b9e896d17f9f5149ae406c6e187eb661` |
| Final PR HEAD commit title | `PROD-003: align baseline record with final PR HEAD (governance)` |
| CI on final PR HEAD | ✅ green — check run `ci` `completed` / `success`, GitHub Actions run `33595261333` |
| New baseline SHA (last code-bearing commit of PR #5) | `df74d6a945fe813ad02a7818409173ef2df6b742` |
| Baseline commit title | `PROD-003: move inventory concurrency fixes into InventoryService (remove monkey-patch)` |
| CI on baseline SHA | ✅ green — check run `ci` `completed` / `success`, GitHub Actions run `33594177345` |
| Test suite on baseline SHA | `Ran 1159 tests in 120.694s — OK` (PostgreSQL 18.6, Python 3.13.15, Django 6.1); migrations check OK |
| Approved on | 2026-09-02 |

**Baseline semantics.** Per the rules above, the baseline fixed the last
**code-bearing** commit of the PR: `df74d6a945fe813ad02a7818409173ef2df6b742`.
The final PR HEAD `7a7abca5b9e896d17f9f5149ae406c6e187eb661` is a docs-only
certification commit on top of it (this record itself) and therefore was
**not** the baseline SHA — each is recorded explicitly with its own commit
title and its own green CI run, so «baseline SHA», «final PR HEAD» and CI
never contradict each other. The advance took effect when PR #5 was merged on
2026-09-02: `main` moved from `3fff49f158cf2aa6f93fd5bf98053c60de57c4b2` to
the merge commit `e27b1e1cc5ab319f7f684a5bc13ceafe7bc9916b`, whose merge CI
(GitHub Actions run #17 / ID `33595830211`) concluded `success`.

Scope of PROD-003 (all in this PR, CI green on the baseline SHA):

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

PROD-003 is merged and complete — nothing remains open or in review. During
review CI was green on the code baseline SHA (run `33594177345`), on the
intermediate record HEAD `3925846335683a2f20c66873d65cbafe7d57aca1` (run
`33594581351`), and on the final PR HEAD (run `33595261333`). Production has
since advanced through PROD-004 / PR #7 to the baseline recorded at the top of
this document.

## Baseline advance — PROD-004 (merged via PR #7)

| Field | Value |
|---|---|
| Ticket | PROD-004 — Close Admin/domain bypasses for business state mutations (Issue #6) |
| Status | ✅ MERGED / completed on 2026-09-02 — not in review, not open |
| Pull request | #7 (`Kvasha62/Amazone_Clone_Production`) — MERGED |
| Branch | `arena/01a060a9-amazone-clone-production` |
| Final PR HEAD SHA | `e6ce5333a34f831b8843bb396e432d1ee3cb467e` |
| Final PR HEAD commit title | `PROD-004: close Admin/domain bypasses for business state mutations` |
| CI on final PR HEAD | ✅ green — check run `ci` `completed` / `success`, GitHub Actions run `33598859271` |
| Merge commit on `main` | `78981863e20e4be705480157402156b455e77211` — `Merge pull request #7 from Kvasha62/arena/01a060a9-amazone-clone-production` |
| CI on merge commit | ✅ green — GitHub Actions run #19 / ID `33600313709`, check run `ci` `completed` / `success` |
| Verified test evidence on merge CI | 1262 tests passed; `python manage.py makemigrations --check` clean (PostgreSQL 18.6, Python 3.13.15, Django 6.1) |
| Approved on | 2026-09-02 |

Resulting production state: `main` at
`78981863e20e4be705480157402156b455e77211`, verified green on 2026-09-02 — this
is the current production baseline recorded at the top of this document.

Scope of PROD-004 (single commit `e6ce5333a34f831b8843bb396e432d1ee3cb467e`,
CI green on both the PR head and the merge commit):

- Closed audit findings F-03, F-04, F-05, F-06, F-07 and N-05: Django Admin is
  no longer a second mutation path for business-owned state — `Order.status`,
  `Stock.quantity`/`reserved_quantity`/`variant`, `Price.price`/`sale_price`/
  `variant`/`currency`, `Shipment.status`/`shipped_at`/`delivered_at`,
  `Coupon.times_used`, `CartItem.quantity`/`variant` (standalone admin and
  inline inside `CartAdmin`);
- one shared guard in `apps/core/admin_guards.py` (new, dependency-free):
  `ProtectedFieldsAdminMixin` / `ProtectedFieldsInlineMixin` — Layer 1
  makes protected fields read-only in generated Admin forms, Layer 2
  raises `PermissionDenied` in `save_model()` and writes only
  `update_fields` excluding protected columns; `guard_inline_formsets()`
  covers inlines via `ModelAdmin.save_formset()`;
- business mutations remain owned exclusively by the authoritative services:
  `OrderService`, `InventoryService`, `PricingService`, `ShippingService`,
  `DiscountService`, `CartService`;
- no database migrations, no dependency changes, no CI workflow changes.

## Baseline advance — PROD-005 (this document)

| Field | Value |
|---|---|
| Ticket | PROD-005 — Advance production baseline after PROD-003 and PROD-004 (Issue #8) |
| Pull request | #9 (`Kvasha62/Amazone_Clone_Production`) |
| Change class | Documentation-only (governance): this pull request changes only `docs/production/PRODUCTION_BASELINE.md` |
| Advance | `38648bddb82c8cd4848e03e930a46e499e4e017e` (PROD-000 record) → `78981863e20e4be705480157402156b455e77211` (`main`) |
| Verified on | 2026-09-02 |

This ticket performs the formal baseline advance required by the
_change of baseline_ rule above after the successful merges of PROD-003
(PR #5) and PROD-004 (PR #7): it names the new SHA explicitly, proves CI is
green on it (GitHub Actions run #19 / ID `33600313709`, conclusion `success`),
and updates this document in the same pull request. No source-code, migration,
dependency, CI-workflow, or deployment changes are introduced; the educational
repository `Kvasha62/Amazone_Clone` is untouched. Remaining audit findings
(F-08, F-10–F-14, F-15–F-23, CI-01, N-02–N-04, N-06, N-08) are unchanged by
this ticket and remain tracked separately.
