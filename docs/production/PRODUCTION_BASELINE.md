# Production Baseline — PROD-027

## Approved baseline commit

| Field | Value |
|---|---|
| Repository | `Kvasha62/Amazone_Clone_Production` |
| Branch | `main` |
| Approved baseline SHA | `0554c16d6af2575ca1aeb1022d28d5f1ab387101` |
| Baseline commit title | `Merge pull request #54 from Kvasha62/arena/01a06741-amazone-clone-production` |
| Baseline commit parents | `c0d155127d8144ec5577387c0dbe80ad12b2b5e6` (`main` after the PROD-026 governance advance / PR #52) and `e695b274086109d73dde20669cceb09cc9db9522` (final HEAD of PR #54) |
| Previous baseline | `ac03048a86f852fa73431dd2411d26ef1f91405b` |
| CI workflow | `.github/workflows/ci.yml` (workflow name: `CI`) |
| CI status on baseline SHA | ✅ green — GitHub Actions run #76 / ID `33759519107` (event `push`, branch `main`, attempt 1), job/check `ci` (ID `100662061385`) — `completed` / `success` |
| Verified CI evidence on baseline SHA | Workflow `CI`; every job step succeeded — `Set up job`, `Initialize containers`, `Checkout repository`, `Set up Python 3.13`, `Install dependencies`, `Django system checks` (`python manage.py check --fail-level WARNING`), `Migration check` (`makemigrations --check --dry-run` → no changes), `Apply migrations` (`migrate --noinput`), `Run tests` (full PostgreSQL 18 test suite, Django 6.1.1), and all post/cleanup steps; run started 2026-09-03T13:10:11Z, job completed 2026-09-03T13:14:19Z, run completed 2026-09-03T13:14:21Z |
| Approved on | 2026-09-03 |
| Ticket | PROD-028 |

Production `main` points exactly at this SHA. This commit is the current
production source-of-truth commit and the frozen reference point for every
subsequent production change. `main` at
`0554c16d6af2575ca1aeb1022d28d5f1ab387101` is the authoritative production
state: it is the merge commit of completed PROD-027 / F-19 (PR #54), and its
first-parent history contains the merge commit of the PROD-026 governance
advance (PR #52, `c0d155127d8144ec5577387c0dbe80ad12b2b5e6`) on top of the
previous approved baseline `ac03048a86f852fa73431dd2411d26ef1f91405b`.
Earlier baseline records are preserved below as history.

## Verification performed (read-only)

1. PR #54 is `MERGED` (merged 2026-09-03T13:10:08Z by the Owner); the GitHub Pull Requests API and `gh pr view` both report merge commit `0554c16d6af2575ca1aeb1022d28d5f1ab387101` and final HEAD `e695b274086109d73dde20669cceb09cc9db9522`.
2. GitHub `main` (REST API `branches/main`), `git ls-remote origin refs/heads/main`, local `main`, and `origin/main` all resolve to `0554c16d6af2575ca1aeb1022d28d5f1ab387101` — an exact match with the new production baseline SHA (verified 2026-09-03). The GitHub commit API reports parents `c0d155127d8144ec5577387c0dbe80ad12b2b5e6` and `e695b274086109d73dde20669cceb09cc9db9522`, and the commit is reachable from `main` as its current tip.
3. `main` is exactly 4 commits ahead of and 0 commits behind the previous baseline `ac03048a86f852fa73431dd2411d26ef1f91405b` (`compare/ac03048...0554c16`): `80bf207cbe0764bc6d734e419a88414e36cd480f` + merge `c0d155127d8144ec5577387c0dbe80ad12b2b5e6` (PROD-026 / PR #52, docs-only governance advance), `e695b274086109d73dde20669cceb09cc9db9522` + merge `0554c16d6af2575ca1aeb1022d28d5f1ab387101` (PROD-027 / PR #54). Nothing else landed on `main` in this baseline range.
4. GitHub Actions workflow `CI`, run #76 / ID `33759519107`: `head_sha` = `0554c16d6af2575ca1aeb1022d28d5f1ab387101` (exact match), event `push`, branch `main`, `run_attempt` 1, status `completed`, conclusion `success`, started 2026-09-03T13:10:11Z, completed 2026-09-03T13:14:21Z; job/check `ci` (ID `100662061385`) is `completed` / `success`.
5. CI on PR #54's final HEAD `e695b274086109d73dde20669cceb09cc9db9522` is green: workflow `CI`, run #75 / ID `33758414141`, event `pull_request`, attempt 1, `completed` / `success` (2026-09-03T12:59:22Z→13:02:21Z); job/check `ci` (ID `100658389918`) is `completed` / `success`, and every step from environment setup through Django checks, migration checks, migrations and the full test suite succeeded.
6. PROD-026 / PR #52 (the governance advance that recorded the previous baseline) is `MERGED` / completed (merged 2026-09-03T12:12:47Z); its merge commit `c0d155127d8144ec5577387c0dbe80ad12b2b5e6` is the first parent of the new baseline SHA.
7. `.github/workflows/ci.yml` exists and is unmodified by this ticket.
8. Source repository `Kvasha62/Amazone_Clone` was not touched in any way.

## Baseline advance — PROD-028 (this document)

| Field | Value |
|---|---|
| Ticket | PROD-028 — Advance production baseline after PROD-027 (F-19) (Issue #55) |
| Status | Documentation-only governance change; pending Architect review and Owner merge |
| Change class | This pull request changes only `docs/production/PRODUCTION_BASELINE.md` |
| Advance | Previous baseline `ac03048a86f852fa73431dd2411d26ef1f91405b` → `0554c16d6af2575ca1aeb1022d28d5f1ab387101` (`main`) |
| Completed production change | PROD-027 / F-19 / Issue #53 / PR #54 — merged 2026-09-03T13:10:08Z |
| Intermediate governance merge in range | PROD-026 / PR #52 — merged 2026-09-03T12:12:47Z (docs-only; merge commit `c0d155127d8144ec5577387c0dbe80ad12b2b5e6`) |
| Verified on | 2026-09-03 |

### Completion record — PROD-027 / PR #54 (merged)

| Field | Value |
|---|---|
| Ticket | PROD-027 — Production observability maturity (F-19) (Issue #53) |
| Status | ✅ MERGED / completed on 2026-09-03T13:10:08Z — not in review, not open |
| Pull request | #54 (`Kvasha62/Amazone_Clone_Production`) — `PROD-027: Add production observability maturity` — MERGED |
| Branch | `arena/01a06741-amazone-clone-production` |
| Final PR HEAD | `e695b274086109d73dde20669cceb09cc9db9522` — `PROD-027: add production observability` |
| CI on final PR HEAD | ✅ green — GitHub Actions run #75 / ID `33758414141`, check run `ci` ID `100658389918` — `completed` / `success` (2026-09-03T12:59:22Z→13:02:21Z) |
| Merge commit on `main` | `0554c16d6af2575ca1aeb1022d28d5f1ab387101` — `Merge pull request #54 from Kvasha62/arena/01a06741-amazone-clone-production` — **the new baseline SHA** |
| CI on merge commit | ✅ green — GitHub Actions run #76 / ID `33759519107`, check run `ci` ID `100662061385` — `completed` / `success` |
| Files changed | `Dockerfile.backend.prod`; `apps/core/celery_observability.py` (new); `apps/core/middleware.py` (new); `apps/core/observability.py` (new); `apps/core/tests/test_observability.py` (new); `apps/notifications/tasks.py`; `apps/users/services/user_service.py`; `config/celery.py`; `config/settings.py`; `docker-compose.prod.yml`; `docker/production/nginx.conf` |

Scope of PROD-027 (closed audit finding F-19): production observability
maturity. The functional PR added production-safe request/correlation IDs;
query-free HTTP lifecycle logging with status and duration; privacy-aware
structured application logs with redaction; unexpected-exception traceback
visibility without changing exception behavior; Celery lifecycle/context
visibility; query-free Gunicorn/Nginx edge logging; and existing Docker
`json-file` log rotation — while preserving the existing health contracts.
No external telemetry platform, event bus, broker, schema, API redesign, CI
redesign, or new architecture was introduced, and the functional PR did not
modify this baseline document. `Kvasha62/Amazone_Clone` was untouched.

PROD-027 / F-19 is recorded as completed/merged via PR #54 and is part of the
verified `main` state at the new baseline SHA. This pull request (PROD-028) is
a docs-only governance change that modifies only
`docs/production/PRODUCTION_BASELINE.md`; it does not change application or
test source, API, models, migrations, dependencies, CI, deployment
configuration, or the educational repository `Kvasha62/Amazone_Clone`, and it
does not remediate any functional or audit finding. F-20, CI-01 and all
N-findings remain outside this ticket and unchanged. Architect review is
required before the Owner performs the final merge; the Architect/Assistant
must not merge this governance pull request.

## Historical baseline record — PROD-025 (superseded)

This record was current immediately before PROD-028. It is preserved for audit
and is now superseded by the approved baseline at the top of this document.

## Approved baseline commit

| Field | Value |
|---|---|
| Repository | `Kvasha62/Amazone_Clone_Production` |
| Branch | `main` |
| Approved baseline SHA | `ac03048a86f852fa73431dd2411d26ef1f91405b` |
| Baseline commit title | `Merge pull request #50 from Kvasha62/arena/01a066e9-amazone-clone-production` |
| Baseline commit parents | `5924352babd90e84929e256f44772cebf81e5089` (`main` after the PROD-024 governance advance / PR #48) and `3fbd7a3c8d207db47b747ac516f8d66b593337ef` (final HEAD of PR #50) |
| CI workflow | `.github/workflows/ci.yml` (workflow name: `CI`) |
| CI status on baseline SHA | ✅ green — GitHub Actions run #72 / ID `33752285990` (event `push`, branch `main`, attempt 1), job/check `ci` (ID `100638369138`) — `completed` / `success` |
| Verified CI evidence on baseline SHA | Workflow `CI`; every job step succeeded — `Set up job`, `Initialize containers`, `Checkout repository`, `Set up Python 3.13`, `Install dependencies`, `Django system checks` (`python manage.py check --fail-level WARNING`), `Migration check` (`makemigrations --check --dry-run` → no changes), `Apply migrations` (`migrate --noinput`), `Run tests` (full PostgreSQL 18 test suite, Django 6.1.1), and all post/cleanup steps; run started 2026-09-03T11:54:55Z, job completed 2026-09-03T11:58:15Z, run completed 2026-09-03T11:58:16Z |
| Approved on | 2026-09-03 |
| Ticket | PROD-026 |

Production `main` points exactly at this SHA. This commit is the current
production source-of-truth commit and the frozen reference point for every
subsequent production change. `main` at
`ac03048a86f852fa73431dd2411d26ef1f91405b` is the authoritative production
state: it is the merge commit of completed PROD-025 / F-18 (PR #50), and its
first-parent history contains the merge commit of the PROD-024 governance
advance (PR #48, `5924352babd90e84929e256f44772cebf81e5089`) on top of the
previous approved baseline `cb79892fc29b4c4f86b5881e6273924d96831d9a`.
Earlier baseline records are preserved below as history.

## Verification performed (read-only)

1. PR #50 is `MERGED` (merged 2026-09-03T11:54:52Z by the Owner); the GitHub Pull Requests API and `gh pr view` both report merge commit `ac03048a86f852fa73431dd2411d26ef1f91405b` and final HEAD `3fbd7a3c8d207db47b747ac516f8d66b593337ef`.
2. GitHub `main` (REST API `branches/main`), `git ls-remote origin refs/heads/main`, local `main`, and `origin/main` all resolve to `ac03048a86f852fa73431dd2411d26ef1f91405b` — an exact match with the new production baseline SHA (verified 2026-09-03). The GitHub commit API reports parents `5924352babd90e84929e256f44772cebf81e5089` and `3fbd7a3c8d207db47b747ac516f8d66b593337ef`, and the commit is reachable from `main` as its current tip.
3. `main` is exactly 4 commits ahead of and 0 commits behind the previous baseline `cb79892fc29b4c4f86b5881e6273924d96831d9a` (`compare/cb79892...ac03048`): `c34227034c3940c313d2fa2139d0d9e98a015138` + merge `5924352babd90e84929e256f44772cebf81e5089` (PROD-024 / PR #48, docs-only governance advance), `3fbd7a3c8d207db47b747ac516f8d66b593337ef` + merge `ac03048a86f852fa73431dd2411d26ef1f91405b` (PROD-025 / PR #50). Nothing else landed on `main` in this baseline range.
4. GitHub Actions workflow `CI`, run #72 / ID `33752285990`: `head_sha` = `ac03048a86f852fa73431dd2411d26ef1f91405b` (exact match), event `push`, branch `main`, `run_attempt` 1, status `completed`, conclusion `success`, started 2026-09-03T11:54:55Z, completed 2026-09-03T11:58:16Z; job/check `ci` (ID `100638369138`) is `completed` / `success`. It is the only check run registered on the baseline SHA.
5. Every CI job step succeeded on the baseline SHA: `Set up job`, `Initialize containers`, `Checkout repository`, `Set up Python 3.13`, `Install dependencies`, `Django system checks` (`python manage.py check --fail-level WARNING`), `Migration check` (`python manage.py makemigrations --check --dry-run`), `Apply migrations` (`migrate --noinput`), `Run tests` (`python manage.py test --verbosity 2`, full PostgreSQL 18 test suite, Django 6.1.1), and all post/cleanup steps concluded `success`.
6. CI on PR #50's final HEAD `3fbd7a3c8d207db47b747ac516f8d66b593337ef` is green: workflow `CI`, run #71 / ID `33751152570`, event `pull_request`, attempt 1, `completed` / `success` (2026-09-03T11:42:26Z→11:45:58Z); job/check `ci` (ID `100634761199`) is `completed` / `success`, and every step from environment setup through Django checks, migration checks, migrations and the full test suite succeeded.
7. PROD-024 / PR #48 is `MERGED` / completed (merged 2026-09-03T09:35:26Z); its merge commit `5924352babd90e84929e256f44772cebf81e5089` is the first parent of the new baseline SHA. CI on that merge commit is green (run #70 / ID `33739690725`, check `ci` ID `100598438095` — `completed` / `success`, 2026-09-03T09:35:28Z→09:38:41Z). CI on PR #48's final HEAD `c34227034c3940c313d2fa2139d0d9e98a015138` was also green (run #69 / ID `33738042486`, check `ci` ID `100593173657` — `completed` / `success`).
8. `.github/workflows/ci.yml` exists and is unmodified by this ticket.
9. Source repository `Kvasha62/Amazone_Clone` was not touched in any way.

## Baseline advance — PROD-026 (merged via PR #52; record preserved)

| Field | Value |
|---|---|
| Ticket | PROD-026 — Advance production baseline after PROD-025 (F-18) (Issue #51) |
| Status | Documentation-only governance change; MERGED / completed via PR #52 (merge commit `c0d155127d8144ec5577387c0dbe80ad12b2b5e6`, merged 2026-09-03T12:12:47Z) |
| Change class | That pull request changed only `docs/production/PRODUCTION_BASELINE.md` |
| Advance | Previous baseline `cb79892fc29b4c4f86b5881e6273924d96831d9a` → `ac03048a86f852fa73431dd2411d26ef1f91405b` (`main`) |
| Completed production change | PROD-025 / F-18 / Issue #49 / PR #50 — merged 2026-09-03T11:54:52Z |
| Intermediate governance merge in range | PROD-024 / PR #48 — merged 2026-09-03T09:35:26Z (docs-only; merge commit `5924352babd90e84929e256f44772cebf81e5089`; CI run #70 / ID `33739690725`, check `ci` ID `100598438095` — `completed` / `success`) |
| Verified on | 2026-09-03 |

### Completion record — PROD-025 / PR #50 (merged)

| Field | Value |
|---|---|
| Ticket | PROD-025 — Wire domain events to notifications (F-18) (Issue #49) |
| Status | ✅ MERGED / completed on 2026-09-03T11:54:52Z — not in review, not open |
| Pull request | #50 (`Kvasha62/Amazone_Clone_Production`) — `PROD-025: Wire domain events to notifications (F-18)` — MERGED |
| Branch | `arena/01a066e9-amazone-clone-production` |
| Final PR HEAD | `3fbd7a3c8d207db47b747ac516f8d66b593337ef` — `PROD-025: Wire domain events to notifications (F-18)` |
| CI on final PR HEAD | ✅ green — GitHub Actions run #71 / ID `33751152570`, check run `ci` ID `100634761199` — `completed` / `success` (2026-09-03T11:42:26Z→11:45:58Z) |
| Merge commit on `main` | `ac03048a86f852fa73431dd2411d26ef1f91405b` — `Merge pull request #50 from Kvasha62/arena/01a066e9-amazone-clone-production` — **the new baseline SHA** |
| CI on merge commit | ✅ green — GitHub Actions run #72 / ID `33752285990`, check run `ci` ID `100638369138` — `completed` / `success` |
| Files changed | `apps/notifications/constants.py`; `apps/notifications/services/notification_events.py` (new); `apps/notifications/services/notification_service.py`; `apps/notifications/tests/test_event_wiring.py` (new); `apps/notifications/tests/test_services.py`; `apps/orders/services/order_service.py`; `apps/payments/services/payment_service.py`; `config/test_runner.py` |

Scope of PROD-025 (closed audit finding F-18): the pre-existing notification
service methods and Celery tasks had no callers from authoritative business
paths. PROD-025 added one explicit event-to-notification boundary,
`NotificationEvents`, and invoked it only from the existing authoritative
`OrderService` order-creation/status/cancellation transitions and
`PaymentService.confirm_payment()` success transition. It maps only existing
notification types, so an order status without a notification contract (for
example `processing`) emits nothing. Existing confirmation and shipped-email
Celery tasks are dispatched for their matching committed order events.

Notification emission uses `transaction.on_commit(..., robust=True)` within
the existing service transactions: rolled-back business operations emit
nothing, workers see only committed rows, and callback failures are logged
without converting an already-committed business result into a false failure.
Repeated payment confirmation remains idempotent and does not duplicate the
payment-success notification. The implementation introduced no event bus,
outbox, new task, queue, transaction boundary, model or migration. Regression
coverage records event mapping, authoritative-path wiring, rollback behavior,
payment retry/idempotency, and existing task execution. `config/test_runner.py`
only enables Celery eager execution for the test session; production Celery
behavior is unchanged. Public API contracts, models, migrations, dependencies,
CI workflow and deployment configuration are unchanged. The functional PR did
not modify this baseline document, and `Kvasha62/Amazone_Clone` was untouched.

PROD-025 / F-18 is recorded as completed/merged via PR #50 and is part of the
verified `main` state at this historical baseline SHA. PROD-026 was a
docs-only governance change that modified only
`docs/production/PRODUCTION_BASELINE.md`; it did not change application or
test source, API, models, migrations, dependencies, CI, deployment
configuration, or the educational repository `Kvasha62/Amazone_Clone`, and it
did not remediate any functional or audit finding. Architect review and the
Owner merge were completed via PR #52 on 2026-09-03; this record is preserved
for audit.

## Historical baseline record — PROD-023 (superseded)

This record was current immediately before PROD-026. It is preserved for audit
and is now superseded by the approved baseline at the top of this document.

## Approved baseline commit

| Field | Value |
|---|---|
| Repository | `Kvasha62/Amazone_Clone_Production` |
| Branch | `main` |
| Approved baseline SHA | `cb79892fc29b4c4f86b5881e6273924d96831d9a` |
| Baseline commit title | `Merge pull request #45 from Kvasha62/arena/01a0665b-amazone-clone-production` |
| Baseline commit parents | `f99cebef3c054bd60b561f9a14b1ba23f673cb80` (`main` after the PROD-022 governance advance / PR #44) and `1177c6614b1207a9b93ec649ccd73d06ead0182e` (final HEAD of PR #45) |
| CI workflow | `.github/workflows/ci.yml` (workflow name: `CI`) |
| CI status on baseline SHA | ✅ green — GitHub Actions run #68 / ID `33735008917` (event `push`, branch `main`, attempt 1), job/check `ci` (ID `100583400043`) — `completed` / `success` |
| Verified CI evidence on baseline SHA | Workflow `CI`; every job step succeeded — `Set up job`, `Initialize containers`, `Checkout repository`, `Set up Python 3.13`, `Install dependencies`, `Django system checks` (`python manage.py check --fail-level WARNING`), `Migration check` (`makemigrations --check --dry-run` → no changes), `Apply migrations` (`migrate --noinput`), `Run tests` (full PostgreSQL 18 test suite, Django 6.1.1), and all post/cleanup steps; run started 2026-09-03T08:44:40Z, completed 2026-09-03T08:48:06Z |
| Approved on | 2026-09-03 |
| Ticket | PROD-024 |

Production `main` points exactly at this SHA. This commit is the current
production source-of-truth commit and the frozen reference point for every
subsequent production change. `main` at
`cb79892fc29b4c4f86b5881e6273924d96831d9a` is the authoritative production
state: it is the merge commit of completed PROD-023 (PR #45), and its
first-parent history contains the merge commit of the PROD-022 governance
advance (PR #44, `f99cebef3c054bd60b561f9a14b1ba23f673cb80`) on top of the
previous baseline `0fb5079f2d3b330ffdc6772ee0a0399fe0d6a98f`.
Earlier baseline records are preserved below as history.

## Verification performed (read-only)

1. GitHub `main` (REST API `branches/main`), `git ls-remote origin refs/heads/main`, local `main`, and `origin/main` all resolve to `cb79892fc29b4c4f86b5881e6273924d96831d9a` — an exact match with the new production baseline SHA (verified 2026-09-03).
2. `main` is exactly 4 commits ahead of and 0 commits behind the previous baseline `0fb5079f2d3b330ffdc6772ee0a0399fe0d6a98f` (`compare/0fb5079...cb79892`): `1e1ef97f0c18c03bb6d6387faed51ef3edc655f0` + merge `f99cebef3c054bd60b561f9a14b1ba23f673cb80` (PROD-022 / PR #44, docs-only governance advance), `1177c6614b1207a9b93ec649ccd73d06ead0182e` + merge `cb79892fc29b4c4f86b5881e6273924d96831d9a` (PROD-023 / PR #45). Nothing else has landed on `main` since the previous baseline.
3. GitHub Actions workflow `CI`, run #68 / ID `33735008917`: `head_sha` = `cb79892fc29b4c4f86b5881e6273924d96831d9a` (exact match), event `push`, branch `main`, `run_attempt` 1, status `completed`, conclusion `success`, started 2026-09-03T08:44:40Z, completed 2026-09-03T08:48:06Z; job `ci` (ID `100583400043`) is `completed` / `success`. It is the only check run registered on the baseline SHA.
4. Every CI job step succeeded on the baseline SHA: `Set up job`, `Initialize containers`, `Checkout repository`, `Set up Python 3.13`, `Install dependencies`, `Django system checks` (`python manage.py check --fail-level WARNING`), `Migration check` (`python manage.py makemigrations --check --dry-run`), `Apply migrations` (`migrate --noinput`), `Run tests` (`python manage.py test --verbosity 2`, full PostgreSQL 18 test suite, Django 6.1.1), and all post/cleanup steps concluded `success`.
5. PROD-023 / PR #45 is `MERGED` / completed (merged 2026-09-03T08:44:38Z); its merge commit is exactly the baseline SHA `cb79892fc29b4c4f86b5881e6273924d96831d9a`. CI on its final PR HEAD `1177c6614b1207a9b93ec649ccd73d06ead0182e` was green (run #67 / ID `33733615364`, check `ci` ID `100578973186` — `completed` / `success`, 2026-09-03T08:29:22Z→08:32:53Z).
6. PROD-022 / PR #44 (the governance advance that recorded the previous baseline) is `MERGED` / completed (merged 2026-09-03T08:17:08Z); its merge commit is `f99cebef3c054bd60b561f9a14b1ba23f673cb80` (first parent of the baseline SHA). CI on that merge commit is green (run #66 / ID `33732530492`, check `ci` ID `100575525440` — `completed` / `success`, 2026-09-03T08:17:10Z→08:21:18Z).
7. `.github/workflows/ci.yml` exists and is unmodified by this ticket.
8. Source repository `Kvasha62/Amazone_Clone` was not touched in any way.

## Baseline advance — PROD-024 (merged via PR #48; record preserved)

| Field | Value |
|---|---|
| Ticket | PROD-024 — Advance production baseline after PROD-023 (F-23) (Issue #47) |
| Status | Documentation-only governance change; MERGED / completed via PR #48 (merge commit `5924352babd90e84929e256f44772cebf81e5089`, merged 2026-09-03T09:35:26Z) |
| Change class | That pull request changed only `docs/production/PRODUCTION_BASELINE.md` |
| Advance | Previous baseline `0fb5079f2d3b330ffdc6772ee0a0399fe0d6a98f` → `cb79892fc29b4c4f86b5881e6273924d96831d9a` (`main`) |
| Completed production change | PROD-023 / PR #45 — merged 2026-09-03T08:44:38Z |
| Intermediate governance merge in range | PROD-022 / PR #44 — merged 2026-09-03T08:17:08Z (docs-only; merge commit `f99cebef3c054bd60b561f9a14b1ba23f673cb80`; CI run #66 / ID `33732530492`, check `ci` ID `100575525440` — `completed` / `success`) |
| Verified on | 2026-09-03 |

### Completion record — PROD-023 / PR #45 (merged)

| Field | Value |
|---|---|
| Ticket | PROD-023 — Protect `Wishlist.items_count` from Django Admin mutation (F-23) (Issue #46) |
| Status | ✅ MERGED / completed on 2026-09-03T08:44:38Z — not in review, not open |
| Pull request | #45 (`Kvasha62/Amazone_Clone_Production`) — `PROD-023: Protect Wishlist.items_count from Admin mutation (F-23)` — MERGED |
| Branch | `arena/01a0665b-amazone-clone-production` |
| Commits | `1177c6614b1207a9b93ec649ccd73d06ead0182e` — `PROD-023: protect Wishlist.items_count from Admin mutation (F-23)` (final PR HEAD) |
| CI on final PR HEAD | ✅ green — GitHub Actions run #67 / ID `33733615364`, check run `ci` ID `100578973186` — `completed` / `success` (2026-09-03T08:32:52Z) |
| Merge commit on `main` | `cb79892fc29b4c4f86b5881e6273924d96831d9a` — `Merge pull request #45 from Kvasha62/arena/01a0665b-amazone-clone-production` — **the new baseline SHA** |
| CI on merge commit | ✅ green — GitHub Actions run #68 / ID `33735008917`, check run `ci` ID `100583400043` — `completed` / `success` |
| Files changed | `apps/wishlist/admin/wishlist_admin.py`, `apps/wishlist/tests/test_admin_guards.py` (new) |

Scope of PROD-023 (closed audit finding F-23):
`Wishlist.items_count` is a denormalized business-state counter whose only
legitimate writers are the `WishlistService` methods — `add_item()`
(`F('items_count') + 1`), `remove_item()` / `move_to_cart()`
(`Greatest(F('items_count') - n, 0)`) and `clear()` (`items_count = 0`).
`WishlistAdmin` treated `items_count` as an ordinary ModelForm input, so a
normal Admin save or a crafted POST could write an arbitrary value and
desynchronize `Wishlist.items_count` from the actual number of `WishlistItem`
rows — the same class of Admin ↔ domain bypass closed for `Coupon.times_used`
by PROD-004 (F-07). The fix reuses the existing PROD-004 guard with no new
architecture: `WishlistAdmin` now subclasses `ProtectedFieldsAdminMixin`
(`apps/core/admin_guards.py`) with `protected_fields = ('items_count',)` —
Layer 1 (form) keeps `items_count` in `readonly_fields` /
`get_readonly_fields()`, so the generated ModelForm has no input and an
ordinary Admin POST cannot bind the field; Layer 2 (server-side)
`save_model()` raises `PermissionDenied` when the in-memory value differs
from the stored row (change) or the model default (add), and change-saves
write an explicit `update_fields` set that excludes `items_count`. `user`
remains editable. `WishlistService` is unchanged; the API, models,
migrations and other bounded contexts are unchanged. New regression tests:
`apps/wishlist/tests/test_admin_guards.py` — `items_count` is never a
change/add form input, crafted Admin change/add POSTs cannot mutate the
counter while allowed fields still save, direct `save_model()` rejects an
in-memory counter change (with the UPDATE excluding the protected column)
while still allowing `user` edits, and service-level `add_item()` /
`remove_item()` / `clear()` keep the counter aligned with actual
`WishlistItem` rows. `makemigrations --check --dry-run` → no changes.
CI/deployment configuration is unchanged. The educational repository
`Kvasha62/Amazone_Clone` was not touched.

PROD-023 is recorded as completed/merged via PR #45 and is part of the verified
`main` state at this historical baseline SHA. PROD-024 was a docs-only
governance change that modified only
`docs/production/PRODUCTION_BASELINE.md`; it did not change source code, API,
models, migrations, dependencies, CI, deployment configuration, or the
educational repository `Kvasha62/Amazone_Clone`, and it did not remediate any
audit finding. Architect review and the Owner merge were completed via PR #48
on 2026-09-03; this record is preserved for audit.

## Historical baseline record — PROD-021 (superseded)

This record was current immediately before PROD-024. It is preserved for audit
and is now superseded by the approved baseline at the top of this document.

## Approved baseline commit

| Field | Value |
|---|---|
| Repository | `Kvasha62/Amazone_Clone_Production` |
| Branch | `main` |
| Approved baseline SHA | `0fb5079f2d3b330ffdc6772ee0a0399fe0d6a98f` |
| Baseline commit title | `Merge pull request #42 from Kvasha62/arena/01a065cb-amazone-clone-production` |
| Baseline commit parents | `411177cf772f680568f014c02fde4bf3ed0bc863` (`main` after the PROD-020 governance advance / PR #40) and `053d84fcb3b697a240cc4128f70afa33a7d123b6` (final HEAD of PR #42) |
| CI workflow | `.github/workflows/ci.yml` (workflow name: `CI`) |
| CI status on baseline SHA | ✅ green — GitHub Actions run #64 / ID `33730756889` (event `push`, branch `main`, attempt 1), job/check `ci` (ID `100569893578`) — `completed` / `success` |
| Verified CI evidence on baseline SHA | Workflow `CI`; every job step succeeded — `Set up job`, `Initialize containers`, `Checkout repository`, `Set up Python 3.13`, `Install dependencies`, `Django system checks` (`python manage.py check --fail-level WARNING`), `Migration check` (`makemigrations --check --dry-run` → no changes), `Apply migrations` (`migrate --noinput`), `Run tests` (full PostgreSQL 18 test suite, Django 6.1.1), and all post/cleanup steps; run started 2026-09-03T07:57:13Z, completed 2026-09-03T08:00:35Z |
| Approved on | 2026-09-03 |
| Ticket | PROD-022 |

Production `main` points exactly at this SHA. This commit is the current
production source-of-truth commit and the frozen reference point for every
subsequent production change. `main` at
`0fb5079f2d3b330ffdc6772ee0a0399fe0d6a98f` is the authoritative production
state: it is the merge commit of completed PROD-021 (PR #42), and its
first-parent history contains the merge commit of the PROD-020 governance
advance (PR #40, `411177cf772f680568f014c02fde4bf3ed0bc863`) on top of the
previous baseline `fb29effb889e2589244b50751f1bcd4ee38ae116`.
Earlier baseline records are preserved below as history.

## Verification performed (read-only)

1. GitHub `main` (REST API `branches/main`), `git ls-remote origin refs/heads/main`, local `main`, and `origin/main` all resolve to `0fb5079f2d3b330ffdc6772ee0a0399fe0d6a98f` — an exact match with the new production baseline SHA (verified 2026-09-03).
2. `main` is exactly 4 commits ahead of and 0 commits behind the previous baseline `fb29effb889e2589244b50751f1bcd4ee38ae116` (`compare/fb29effb...0fb5079`): `fa312f72ce34c5996d70596ad240df586f3d4c12` + merge `411177cf772f680568f014c02fde4bf3ed0bc863` (PROD-020 / PR #40, docs-only governance advance), `053d84fcb3b697a240cc4128f70afa33a7d123b6` + merge `0fb5079f2d3b330ffdc6772ee0a0399fe0d6a98f` (PROD-021 / PR #42). Nothing else has landed on `main` since the previous baseline.
3. GitHub Actions workflow `CI`, run #64 / ID `33730756889`: `head_sha` = `0fb5079f2d3b330ffdc6772ee0a0399fe0d6a98f` (exact match), event `push`, branch `main`, `run_attempt` 1, status `completed`, conclusion `success`, started 2026-09-03T07:57:13Z, completed 2026-09-03T08:00:35Z; job `ci` (ID `100569893578`) is `completed` / `success`. It is the only check run registered on the baseline SHA.
4. Every CI job step succeeded on the baseline SHA: `Set up job`, `Initialize containers`, `Checkout repository`, `Set up Python 3.13`, `Install dependencies`, `Django system checks` (`python manage.py check --fail-level WARNING`), `Migration check` (`python manage.py makemigrations --check --dry-run`), `Apply migrations` (`migrate --noinput`), `Run tests` (`python manage.py test --verbosity 2`, full PostgreSQL 18 test suite, Django 6.1.1), and all post/cleanup steps concluded `success`.
5. PROD-021 / PR #42 is `MERGED` / completed (merged 2026-09-03T07:57:10Z); its merge commit is exactly the baseline SHA `0fb5079f2d3b330ffdc6772ee0a0399fe0d6a98f`. CI on its final PR HEAD `053d84fcb3b697a240cc4128f70afa33a7d123b6` was green (run #63 / ID `33720995715`, check `ci` ID `100539928695` — `completed` / `success`, 2026-09-03T05:56:29Z→05:59:52Z).
6. PROD-020 / PR #40 (the governance advance that recorded the previous baseline) is `MERGED` / completed (merged 2026-09-03T05:27:49Z); its merge commit is `411177cf772f680568f014c02fde4bf3ed0bc863` (first parent of the baseline SHA). CI on that merge commit is green (run #62 / ID `33718949417`, check `ci` ID `100533940584` — `completed` / `success`, 2026-09-03T05:27:51Z→05:31:25Z).
7. `.github/workflows/ci.yml` exists and is unmodified by this ticket.
8. Source repository `Kvasha62/Amazone_Clone` was not touched in any way.

## Baseline advance — PROD-022 (merged via PR #44; record preserved)

| Field | Value |
|---|---|
| Ticket | PROD-022 — Advance production baseline after PROD-021 (Issue #43) |
| Status | Documentation-only governance change; MERGED / completed via PR #44 (merge commit `f99cebef3c054bd60b561f9a14b1ba23f673cb80`, merged 2026-09-03T08:17:08Z) |
| Change class | That pull request changed only `docs/production/PRODUCTION_BASELINE.md` |
| Advance | Previous baseline `fb29effb889e2589244b50751f1bcd4ee38ae116` → `0fb5079f2d3b330ffdc6772ee0a0399fe0d6a98f` (`main`) |
| Completed production change | PROD-021 / PR #42 — merged 2026-09-03T07:57:10Z |
| Intermediate governance merge in range | PROD-020 / PR #40 — merged 2026-09-03T05:27:49Z (docs-only; merge commit `411177cf772f680568f014c02fde4bf3ed0bc863`; CI run #62 / ID `33718949417`, check `ci` ID `100533940584` — `completed` / `success`) |
| Verified on | 2026-09-03 |

### Completion record — PROD-021 / PR #42 (merged)

| Field | Value |
|---|---|
| Ticket | PROD-021 — Make analytics ProductView deduplication concurrency-safe (F-22) (Issue #41) |
| Status | ✅ MERGED / completed on 2026-09-03T07:57:10Z — not in review, not open |
| Pull request | #42 (`Kvasha62/Amazone_Clone_Production`) — `PROD-021: Make analytics ProductView deduplication concurrency-safe (F-22)` — MERGED |
| Branch | `arena/01a065cb-amazone-clone-production` |
| Commits | `053d84fcb3b697a240cc4128f70afa33a7d123b6` — `PROD-021: make ProductView deduplication concurrency-safe (F-22)` (final PR HEAD) |
| CI on final PR HEAD | ✅ green — GitHub Actions run #63 / ID `33720995715`, check run `ci` ID `100539928695` — `completed` / `success` (2026-09-03T05:59:52Z) |
| Merge commit on `main` | `0fb5079f2d3b330ffdc6772ee0a0399fe0d6a98f` — `Merge pull request #42 from Kvasha62/arena/01a065cb-amazone-clone-production` — **the new baseline SHA** |
| CI on merge commit | ✅ green — GitHub Actions run #64 / ID `33730756889`, check run `ci` ID `100569893578` — `completed` / `success` |
| Files changed | `apps/analytics/locks.py` (new), `apps/analytics/services/analytics_service.py`, `apps/analytics/tests/test_locks.py` (new), `apps/analytics/tests/test_product_view_concurrency.py` (new), `ARCHITECTURE.md` |

Scope of PROD-021 (closed audit finding F-22):
`AnalyticsService.record_view()` is `SELECT EXISTS(...)` → `INSERT` →
`UPDATE views_count` inside `transaction.atomic()`, which on PostgreSQL
`READ COMMITTED` gives atomicity but not isolation against this
check-then-insert pattern: two concurrent transactions for the same
deduplication identity both observed `exists() == False`, both inserted, and
`Product.views_count` was incremented twice. The fix serializes the
check-then-insert per identity: before the existence check, inside the
existing `transaction.atomic()`, it takes
`pg_advisory_xact_lock(bigint)` on a deterministic key derived from the
identity (new `apps/analytics/locks.py`: `dedup_identity()`,
`blake2b`-derived lock key — not `hash()`, because `PYTHONHASHSEED`
randomization would give different gunicorn workers different keys — and
`acquire_dedup_lock()`). Deduplication identity semantics are unchanged:
authenticated `(product, user)`, anonymous `(product, session_key)`, neither
user nor session → deduplication not applicable, sliding one-hour window.
The lock is released automatically on COMMIT or ROLLBACK, serializes only
competitors for the same key (different products/users/sessions never block
each other), is independent of application timing, and requires no schema
change (a `UNIQUE` / partial `UniqueConstraint` was rejected because the
invariant is a sliding window, not a deterministic function of a row). On
non-PostgreSQL backends (SQLite dev only) the lock is a no-op with a warning,
so production guarantees are not weakened. `Product.views_count` is
incremented only on the path that actually inserts a `ProductView`
(deduplicated racers return before the insert), so the counter grows by
exactly 1 per recorded view. New tests: 8 cross-connection PostgreSQL
concurrency regression tests (`apps/analytics/tests/test_product_view_concurrency.py`
— `TransactionTestCase`, barrier start, one DB connection per thread; with
the fix disabled the suite fails exactly on the single-view invariant, with
it in place all 71 analytics tests pass) and 8 identity/lock-key semantics
tests (`apps/analytics/tests/test_locks.py`). `ARCHITECTURE.md` adds one row
to the existing concurrency-protection table documenting the implemented
decision. API is unchanged. Models are unchanged. Migrations are unchanged
(`makemigrations --check --dry-run` → `No changes detected`). CI/deployment
configuration is unchanged. The educational repository
`Kvasha62/Amazone_Clone` was not touched.

PROD-021 is recorded as completed/merged via PR #42 and is part of the verified
`main` state at the new baseline SHA. This pull request (PROD-022) is a
docs-only governance change that modifies only
`docs/production/PRODUCTION_BASELINE.md`; it does not change source code, API,
models, migrations, dependencies, CI, deployment configuration, or the
educational repository `Kvasha62/Amazone_Clone`, and it does not remediate any
audit finding — all remaining audit findings are outside this ticket and
remain unchanged. Architect review is required before the Owner performs the
final merge; the Architect/Assistant must not merge this governance pull
request.

## Historical baseline record — PROD-019 (superseded)

This record was current immediately before PROD-022. It is preserved for audit
and is now superseded by the approved baseline at the top of this document.

## Approved baseline commit

| Field | Value |
|---|---|
| Repository | `Kvasha62/Amazone_Clone_Production` |
| Branch | `main` |
| Approved baseline SHA | `fb29effb889e2589244b50751f1bcd4ee38ae116` |
| Baseline commit title | `Merge pull request #38 from Kvasha62/arena/01a06596-amazone-clone-production` |
| Baseline commit parents | `ef47ceccd6eaf7d4054396ed4f22a38b4f705c2b` (`main` after PROD-018 / PR #36) and `8b72c6f94fa266332e15df2f92632f7253f77e05` (final HEAD of PR #38) |
| CI workflow | `.github/workflows/ci.yml` (workflow name: `CI`) |
| CI status on baseline SHA | ✅ green — GitHub Actions run #60 / ID `33717985162` (event `push`, branch `main`, attempt 1), job/check `ci` (ID `100531087938`) — `completed` / `success` |
| Verified CI evidence on baseline SHA | Workflow `CI`; every job step succeeded — `Set up job`, `Initialize containers`, `Checkout repository`, `Set up Python 3.13`, `Install dependencies`, `Django system checks` (`python manage.py check --fail-level WARNING`), `Migration check` (`makemigrations --check --dry-run` → no changes), `Apply migrations` (`migrate --noinput`), `Run tests` (full PostgreSQL 18 test suite, Django 6.1.1), and all post/cleanup steps; run started 2026-09-03T05:13:46Z, completed 2026-09-03T05:16:20Z |
| Approved on | 2026-09-03 |
| Ticket | PROD-020 |

Production `main` points exactly at this SHA. This commit is the current
production source-of-truth commit and the frozen reference point for every
subsequent production change. `main` at
`fb29effb889e2589244b50751f1bcd4ee38ae116` is the authoritative production
state: it is the merge commit of completed PROD-019 (PR #38), and its
first-parent history contains the merge commit of the PROD-018 governance
advance (PR #36, `ef47ceccd6eaf7d4054396ed4f22a38b4f705c2b`) on top of the
previous baseline `161639cd0ff929923c50b54d78c125a1e95ed931`.
Earlier baseline records are preserved below as history.

## Verification performed (read-only)

1. GitHub `main` (REST API `branches/main`), `git ls-remote origin refs/heads/main`, local `main`, and `origin/main` all resolve to `fb29effb889e2589244b50751f1bcd4ee38ae116` — an exact match with the new production baseline SHA (verified 2026-09-03).
2. `main` is exactly 4 commits ahead of and 0 commits behind the previous baseline `161639cd0ff929923c50b54d78c125a1e95ed931` (`compare/161639cd...fb29effb`): `b9f49f14` + merge `ef47cecc` (PROD-018 / PR #36, docs-only governance advance), `8b72c6f9` + merge `fb29effb` (PROD-019 / PR #38). Nothing else has landed on `main` since the previous baseline.
3. GitHub Actions workflow `CI`, run #60 / ID `33717985162`: `head_sha` = `fb29effb889e2589244b50751f1bcd4ee38ae116` (exact match), event `push`, branch `main`, `run_attempt` 1, status `completed`, conclusion `success`, started 2026-09-03T05:13:46Z, completed 2026-09-03T05:16:20Z; job `ci` (ID `100531087938`) is `completed` / `success`. It is the only check run registered on the baseline SHA.
4. Every CI job step succeeded on the baseline SHA: `Set up job`, `Initialize containers`, `Checkout repository`, `Set up Python 3.13`, `Install dependencies`, `Django system checks`, `Migration check`, `Apply migrations`, `Run tests`, and all post/cleanup steps concluded `success`. The `Run tests` step executed the full PostgreSQL 18 / Python 3.13 / Django 6.1.1 suite and concluded `success`.
5. PROD-019 / PR #38 is `MERGED` / completed (merged 2026-09-03T05:13:38Z by the Owner); its merge commit is exactly the baseline SHA `fb29effb889e2589244b50751f1bcd4ee38ae116`. CI on its final PR HEAD `8b72c6f94fa266332e15df2f92632f7253f77e05` was green (run #59 / ID `33716358381`, check `ci` ID `100526265842` — `completed` / `success`, 2026-09-03T04:47:58Z→04:51:22Z).
6. PROD-018 / PR #36 (the governance advance recorded at the top of the previous baseline) is `MERGED` / completed (merged 2026-09-03T04:37:01Z by the Owner); its merge commit is `ef47ceccd6eaf7d4054396ed4f22a38b4f705c2b` (first parent of the baseline SHA). CI on that merge commit is green (run #58 / ID `33715701364`, check `ci` ID `100524314514` — `completed` / `success`, 2026-09-03T04:40:22Z).
7. `.github/workflows/ci.yml` exists and is unmodified by this ticket.
8. Source repository `Kvasha62/Amazone_Clone` was not touched in any way.

## Baseline advance — PROD-020 (merged via PR #40; record preserved)

| Field | Value |
|---|---|
| Ticket | PROD-020 — Advance production baseline after PROD-019 (Issue #39) |
| Status | Documentation-only governance change; MERGED / completed via PR #40 (merge commit `411177cf772f680568f014c02fde4bf3ed0bc863`, merged 2026-09-03T05:27:49Z) |
| Change class | That pull request changed only `docs/production/PRODUCTION_BASELINE.md` |
| Advance | Previous baseline `161639cd0ff929923c50b54d78c125a1e95ed931` → `fb29effb889e2589244b50751f1bcd4ee38ae116` (`main`) |
| Completed production change | PROD-019 / PR #38 — merged 2026-09-03T05:13:38Z |
| Intermediate governance merge in range | PROD-018 / PR #36 — merged 2026-09-03T04:37:01Z (docs-only; merge commit `ef47ceccd6eaf7d4054396ed4f22a38b4f705c2b`; CI run #58 / ID `33715701364`, check `ci` ID `100524314514` — `completed` / `success`) |
| Verified on | 2026-09-03 |

### Completion record — PROD-019 / PR #38 (merged)

| Field | Value |
|---|---|
| Ticket | PROD-019 — Use effective prices for product bounds (F-10) (Issue #37) |
| Status | ✅ MERGED / completed on 2026-09-03T05:13:38Z — not in review, not open |
| Pull request | #38 (`Kvasha62/Amazone_Clone_Production`) — `PROD-019: Use effective prices for product bounds (F-10)` — MERGED |
| Branch | `arena/01a06596-amazone-clone-production` |
| Commits | `8b72c6f94fa266332e15df2f92632f7253f77e05` — `Fix effective price product bounds (F-10)` (final PR HEAD) |
| CI on final PR HEAD | ✅ green — GitHub Actions run #59 / ID `33716358381`, check run `ci` ID `100526265842` — `completed` / `success` (2026-09-03T04:51:22Z) |
| Merge commit on `main` | `fb29effb889e2589244b50751f1bcd4ee38ae116` — `Merge pull request #38 from Kvasha62/arena/01a06596-amazone-clone-production` — **the new baseline SHA** |
| CI on merge commit | ✅ green — GitHub Actions run #60 / ID `33717985162`, check run `ci` ID `100531087938` — `completed` / `success` |
| Files changed | `apps/pricing/services/pricing_service.py`, `apps/pricing/tests/test_services.py` |

Scope of PROD-019 (closed audit finding F-10):
`PricingService._compute_price_bounds()` was computing `Product.min_price` and
`Product.max_price` from the raw `Price.price` column, ignoring `sale_price`.
Since `Price.effective_price` implements the contract
`sale_price if sale_price is not None else price`, the denormalized product-level
price bounds were incorrect whenever any variant carried a sale price. The fix
represents effective prices in SQL as `COALESCE(sale_price, price)` and
aggregates with `MIN` and `MAX`, preserving the existing active-variant filter.
The authoritative flow
`PricingService → CatalogService.set_product_prices() → Product.min_price/max_price`
is unchanged. Pricing service regression tests cover sale-price effects on both
minimum and maximum, base-price fallback without a sale price, inactive-variant
exclusion (including an inactive sale price), and sale-price update
recalculation. API is unchanged. Models are unchanged. Migrations are unchanged.
`ARCHITECTURE.md` is unchanged. Concurrency strategy is preserved
(`transaction.atomic` + `select_for_update` + `PricingService` +
`CatalogService.set_product_prices()`). The `pricing → catalog` dependency
direction is preserved. No `ProductVariant` cross-domain signals were
introduced. CI/deployment configuration is unchanged. The educational
repository `Kvasha62/Amazone_Clone` was not touched.

PROD-019 is recorded as completed/merged via PR #38 and is part of the verified
`main` state at the new baseline SHA. This pull request (PROD-020) is a
docs-only governance change that modifies only
`docs/production/PRODUCTION_BASELINE.md`; it does not change source code, API,
models, migrations, dependencies, CI, deployment configuration, or the
educational repository `Kvasha62/Amazone_Clone`, and it does not remediate any
audit finding — all remaining audit findings (F-18–F-23, CI-01, N-findings, and
others) are outside this ticket and remain unchanged. Architect review is
required before the Owner performs the final merge; the Architect/Assistant must
not merge this governance pull request.

## Historical baseline record — PROD-018 (superseded)

This record was current immediately before PROD-020. It is preserved for audit
and is now superseded by the approved baseline at the top of this document.

### Approved baseline commit

| Field | Value |
|---|---|
| Repository | `Kvasha62/Amazone_Clone_Production` |
| Branch | `main` |
| Approved baseline SHA | `161639cd0ff929923c50b54d78c125a1e95ed931` |
| Baseline commit title | `Merge pull request #34 from Kvasha62/arena/01a06547-amazone-clone-production` |
| Baseline commit parents | `70bf433a7c467555bc6d5c6a359fd6c553d4e5a0` (`main` after PROD-016 / PR #32) and `e9eb4daddf6c236d3e5dc2f73abebb88c928d63b` (final HEAD of PR #34) |
| CI workflow | `.github/workflows/ci.yml` (workflow name: `CI`) |
| CI status on baseline SHA | ✅ green — GitHub Actions run #56 / ID `33714478195` (event `push`, branch `main`, attempt 1), job/check `ci` (ID `100520637452`) — `completed` / `success` |
| Verified CI evidence on baseline SHA | Workflow `CI`; every job step succeeded — `Set up job`, `Initialize containers`, `Checkout repository`, `Set up Python 3.13`, `Install dependencies`, `Django system checks` (`python manage.py check --fail-level WARNING`), `Migration check` (`makemigrations --check --dry-run` → no changes), `Apply migrations` (`migrate --noinput`), `Run tests` (full PostgreSQL 18 test suite, Django 6.1.1), and all post/cleanup steps; run started 2026-09-03T04:17:04Z, completed 2026-09-03T04:20:22Z |
| Approved on | 2026-09-03 |
| Ticket | PROD-018 |

### Baseline advance — PROD-018 (merged via PR #36; record preserved)

| Field | Value |
|---|---|
| Ticket | PROD-018 — Advance production baseline after PROD-017 (Issue #35) |
| Status | Documentation-only governance change; MERGED / completed via PR #36 (merge commit `ef47ceccd6eaf7d4054396ed4f22a38b4f705c2b`, merged 2026-09-03T04:37:01Z) |
| Change class | That pull request changed only `docs/production/PRODUCTION_BASELINE.md` |
| Advance | Previous baseline `2a616450a107d7b7d53f39f5c3776baff1921515` → `161639cd0ff929923c50b54d78c125a1e95ed931` (`main`) |
| Completed production change | PROD-017 / PR #34 — merged 2026-09-03T04:17:02Z |
| Intermediate governance merge in range | PROD-016 / PR #32 — merged 2026-09-03T03:16:11Z (docs-only; merge commit `70bf433a7c467555bc6d5c6a359fd6c553d4e5a0`; CI run #51 / ID `33710752240`, check `ci` ID `100509491926` — `completed` / `success`) |
| Verified on | 2026-09-03 |

#### Completion record — PROD-017 / PR #34 (merged)

| Field | Value |
|---|---|
| Ticket | PROD-017 — Eliminate unsafe broad exception handling (F-17) (Issue #33) |
| Status | ✅ MERGED / completed on 2026-09-03T04:17:02Z — not in review, not open |
| Pull request | #34 (`Kvasha62/Amazone_Clone_Production`) — `PROD-017: Eliminate unsafe broad exception handling (F-17)` — MERGED |
| Branch | `arena/01a06547-amazone-clone-production` |
| Commits | `a0882bb9e077a4c5f4921cc2106d984ba836ae23` — `PROD-017: eliminate unsafe broad exception handling (F-17)`; `3fa788534b8272f6fb2098b946a88e67f8b54dd8` — `PROD-017: preserve Celery offline fallback; harden regression tests`; `910dab6e547d1aefa38ca2b07eea60d16f0853e7` — `PROD-017: make failing admin/wishlist regression tests backend-portable`; `e9eb4daddf6c236d3e5dc2f73abebb88c928d63b` — `PROD-017: address Architect review on Celery fallback and catalog scope` (final PR HEAD) |
| CI on final PR HEAD | ✅ green — GitHub Actions run #55 / ID `33714031388`, check run `ci` ID `100519334042` — `completed` / `success` (2026-09-03T04:13:40Z) |
| Merge commit on `main` | `161639cd0ff929923c50b54d78c125a1e95ed931` — `Merge pull request #34 from Kvasha62/arena/01a06547-amazone-clone-production` — **the PROD-018 baseline SHA** |
| CI on merge commit | ✅ green — GitHub Actions run #56 / ID `33714478195`, check run `ci` ID `100520637452` — `completed` / `success` |
| Files changed | Production: `apps/cart/serializers/cart_serializers.py`, `apps/catalog/api_views/product_brief_views.py`, `apps/core/health_urls.py`, `apps/orders/admin/order_admin.py`, `apps/orders/management/commands/cleanup_stale_orders.py`, `apps/orders/management/commands/reconcile_order_coordination.py`, `apps/payments/api_views/payment_views.py`, `apps/payments/management/commands/cleanup_stale_payments.py`, `apps/payments/services/payment_service.py`, `apps/shipping/management/commands/cleanup_stale_shipments.py`, `apps/users/api_views/password_reset_views.py`, `apps/wishlist/services/wishlist_service.py`. Tests: `apps/cart/tests/test_serializers.py` (new), `apps/catalog/tests/test_api.py`, `apps/core/tests/test_health.py` (new), `apps/orders/tests/test_admin_guards.py`, `apps/orders/tests/test_coordination_failures.py`, `apps/orders/tests/test_cleanup_stale_orders_command.py` (new), `apps/payments/tests/test_cleanup_stale_payments_command.py` (new), `apps/payments/tests/test_order_confirmation_recovery.py`, `apps/payments/tests/test_services.py`, `apps/shipping/tests/test_cleanup_stale_shipments.py` (new), `apps/users/tests/test_password_reset.py`, `apps/wishlist/tests/test_services.py` |

PROD-017 is recorded as completed/merged via PR #34 and is part of the verified
`main` state at the PROD-018 baseline SHA. No functional, model, schema,
migration, dependency, CI workflow, deployment, or educational-repository changes
were introduced by PROD-018, and no remaining audit finding was remediated by it
— all remaining audit findings are outside that ticket and remain unchanged.

## Historical baseline record — PROD-016 (superseded)

This record was current immediately before PROD-018. It is preserved for audit
and is now superseded by the approved baseline at the top of this document.

## Approved baseline commit

| Field | Value |
|---|---|
| Repository | `Kvasha62/Amazone_Clone_Production` |
| Branch | `main` |
| Approved baseline SHA | `2a616450a107d7b7d53f39f5c3776baff1921515` |
| Baseline commit title | `Merge pull request #30 from Kvasha62/arena/01a06362-amazone-clone-production` |
| Baseline commit parents | `d7e383b3a5ba1a4550d0e059234a570918b5b44a` (`main` after PROD-014 / PR #28) and `76d9fc7b13ac2ccd2c54317ebd5319efe257bfa5` (final HEAD of PR #30) |
| CI workflow | `.github/workflows/ci.yml` (workflow name: `CI`) |
| CI status on baseline SHA | ✅ green — GitHub Actions run #49 / ID `33671965689` (event `push`, branch `main`, attempt 1), job/check `ci` (ID `100387493188`) — `completed` / `success` |
| Verified CI evidence on baseline SHA | Workflow `CI`; `python manage.py check --fail-level WARNING` → `System check identified no issues (0 silenced).`; `python manage.py makemigrations --check --dry-run` → `No changes detected`; `python manage.py migrate --noinput` applied every migration `OK` (including `payments.0004_payment_external_id_unique`); `python manage.py test --verbosity 2` → `Found 1420 test(s).` / `Ran 1420 tests in 185.524s` / `OK` (PostgreSQL 18.6, Python 3.13.15, Django 6.1.1, psycopg 3.3.5); run started 2026-09-02T19:13:04Z, completed 2026-09-02T19:17:01Z |
| Approved on | 2026-09-02 |
| Ticket | PROD-016 |

Production `main` points exactly at this SHA. This commit is the current
production source-of-truth commit and the frozen reference point for every
subsequent production change. `main` at
`2a616450a107d7b7d53f39f5c3776baff1921515` is the authoritative production
state: it is the merge commit of completed PROD-015 (PR #30), and its
first-parent history contains the merge commit of completed PROD-014 (PR #28,
`d7e383b3a5ba1a4550d0e059234a570918b5b44a`) and the merge commit of the
PROD-013 governance advance (PR #26, `b80568fc9d675c25cca4d53f89fa79902d6dc916`)
on top of the previous baseline `1b4f069c159b198d30ee82a71b65198ce11bd2b7`.
Earlier baseline records are preserved below as history.

## Verification performed (read-only)

1. GitHub `main` (REST API `branches/main`), `git ls-remote origin refs/heads/main`, local `main`, and `origin/main` all resolve to `2a616450a107d7b7d53f39f5c3776baff1921515` — an exact match with the new production baseline SHA (verified 2026-09-02).
2. `main` is exactly 7 commits ahead of and 0 commits behind the previous baseline `1b4f069c159b198d30ee82a71b65198ce11bd2b7` (`compare/1b4f069c...2a616450`): `2cbabd81` + merge `b80568fc` (PROD-013 / PR #26, docs-only), `d8bb2cfc` + merge `d7e383b3` (PROD-014 / PR #28), `b8fa9533` + `76d9fc7b` + merge `2a616450` (PROD-015 / PR #30). Nothing else has landed on `main` since the previous baseline.
3. GitHub Actions workflow `CI`, run #49 / ID `33671965689`: `head_sha` = `2a616450a107d7b7d53f39f5c3776baff1921515` (exact match), event `push`, branch `main`, `run_attempt` 1, status `completed`, conclusion `success`, started 2026-09-02T19:13:04Z, completed 2026-09-02T19:17:01Z; job `ci` (ID `100387493188`) is `completed` / `success`. It is the only check run registered on the baseline SHA.
4. Every CI job step succeeded on the baseline SHA: `Set up job`, `Initialize containers`, `Checkout repository`, `Set up Python 3.13`, `Install dependencies`, `Django system checks`, `Migration check`, `Apply migrations`, `Run tests`, and all post/cleanup steps concluded `success`. Job-log evidence: `System check identified no issues (0 silenced).`, `No changes detected`, every migration `... OK` (including `payments.0004_payment_external_id_unique`), `Found 1420 test(s).`, `Ran 1420 tests in 185.524s` / `OK`, `Destroying test database for alias 'default'`.
5. PROD-014 / PR #28 is `MERGED` / completed (merged 2026-09-02T14:45:01Z by the Owner); its merge commit is exactly `d7e383b3a5ba1a4550d0e059234a570918b5b44a` (first parent of the baseline SHA). CI on its final PR HEAD `d8bb2cfcb0b1e5cf7ddf41587ea7085299f9f5a0` was green (run #45 / ID `33643178944`, check `ci` ID `100291140896` — `completed` / `success`, 2026-09-02T14:40:09Z). CI on the merge commit is green: run #46 / ID `33644092110`, attempt 2, check `ci` ID `100362024710` — `completed` / `success` (2026-09-02T18:00:38Z); attempt 1 of that same run (job ID `100294231087`) was `cancelled` at 2026-09-02T15:31:14Z while its `Run tests` step was still in progress — this is CI incident `33644092110`, recorded (and explicitly not attributed) below.
6. PROD-015 / PR #30 is `MERGED` / completed (merged 2026-09-02T19:13:01Z by the Owner); its merge commit is exactly the baseline SHA `2a616450a107d7b7d53f39f5c3776baff1921515`. CI on its final PR HEAD `76d9fc7b13ac2ccd2c54317ebd5319efe257bfa5` was green (run #48 / ID `33671082942`, check `ci` ID `100384573576` — `completed` / `success`, 2026-09-02T19:08:26Z); CI on its first commit `b8fa953364a700740af49661458c4b7fb3d4a99b` was also green (run #47 / ID `33668185398`, check `ci` ID `100374971862` — `completed` / `success`, 2026-09-02T18:39:09Z).
7. `.github/workflows/ci.yml` exists and is unmodified by this ticket.
8. Source repository `Kvasha62/Amazone_Clone` was not touched in any way.

## Baseline advance — PROD-016 (merged via PR #32; record preserved)

| Field | Value |
|---|---|
| Ticket | PROD-016 — Advance production baseline after PROD-014 and PROD-015 (Issue #31) |
| Status | Documentation-only governance change; MERGED / completed via PR #32 (merge commit `70bf433a7c467555bc6d5c6a359fd6c553d4e5a0`, merged 2026-09-03T03:16:11Z) |
| Change class | This pull request changes only `docs/production/PRODUCTION_BASELINE.md` |
| Advance | Previous baseline `1b4f069c159b198d30ee82a71b65198ce11bd2b7` → `2a616450a107d7b7d53f39f5c3776baff1921515` (`main`) |
| Completed production changes | PROD-014 / PR #28 — merged 2026-09-02T14:45:01Z; PROD-015 / PR #30 — merged 2026-09-02T19:13:01Z |
| Intermediate governance merge in range | PROD-013 / PR #26 — merged 2026-09-02T13:52:54Z (docs-only; merge commit `b80568fc9d675c25cca4d53f89fa79902d6dc916`; CI run #44 / ID `33638464256`, check `ci` ID `100275198811` — `completed` / `success`) |
| Verified on | 2026-09-02 |

### Completion record — PROD-014 / PR #28 (merged)

| Field | Value |
|---|---|
| Ticket | PROD-014 — Enforce uniqueness of `Payment.external_id` (F-15) (Issue #27) |
| Status | ✅ MERGED / completed on 2026-09-02T14:45:01Z — not in review, not open |
| Pull request | #28 (`Kvasha62/Amazone_Clone_Production`) — `PROD-014: Enforce uniqueness of Payment.external_id (F-15)` — MERGED |
| Branch | `arena/01a0626a-amazone-clone-production` |
| Final PR HEAD SHA | `d8bb2cfcb0b1e5cf7ddf41587ea7085299f9f5a0` — `PROD-014 (F-15): enforce DB-level uniqueness of Payment.external_id` |
| CI on final PR HEAD | ✅ green — GitHub Actions run #45 / ID `33643178944`, check run `ci` ID `100291140896` — `completed` / `success` |
| Merge commit on `main` | `d7e383b3a5ba1a4550d0e059234a570918b5b44a` — `Merge pull request #28 from Kvasha62/arena/01a0626a-amazone-clone-production` |
| CI on merge commit | ✅ green — GitHub Actions run #46 / ID `33644092110`, attempt 2, check run `ci` ID `100362024710` — `completed` / `success` (attempt 1 was `cancelled`; see the CI-incident note below) |
| Files changed | `apps/payments/models/payment.py`, `apps/payments/migrations/0004_payment_external_id_unique.py` (new), `apps/payments/querysets/payment_queryset.py`, `apps/payments/tests/test_external_id_uniqueness.py` (new), `ARCHITECTURE.md` |

Scope of PROD-014 (closed audit finding F-15): non-blank `Payment.external_id`
is now globally unique at the database boundary — partial
`UniqueConstraint(payment_external_id_unique)` (`WHERE external_id <> ''`)
declared in `Payment.Meta.constraints`, the plain `db_index` on the column
replaced by that partial unique index, migration `payments.0004` with a
fail-loud duplicate guard that aborts before any DDL, and 13 regression tests.
Webhook correlation (`with_external_id(...).first()`) is unchanged and now
deterministic. No API contract, dependency, CI workflow, or deployment changes
were part of PROD-014.

### Completion record — PROD-015 / PR #30 (merged)

| Field | Value |
|---|---|
| Ticket | PROD-015 — Make reviews/discounts concurrency tests fail boundedly (Issue #29) |
| Status | ✅ MERGED / completed on 2026-09-02T19:13:01Z — not in review, not open |
| Pull request | #30 (`Kvasha62/Amazone_Clone_Production`) — `PROD-015: Make reviews/discounts concurrency tests fail boundedly` — MERGED |
| Branch | `arena/01a06362-amazone-clone-production` |
| Commits | `b8fa953364a700740af49661458c4b7fb3d4a99b` — `PROD-015: bounded failure for reviews/discounts concurrency tests`; `76d9fc7b13ac2ccd2c54317ebd5319efe257bfa5` — `PROD-015: guarantee stuck workers cannot hold the test database` (final PR HEAD) |
| CI on final PR HEAD | ✅ green — GitHub Actions run #48 / ID `33671082942`, check run `ci` ID `100384573576` — `completed` / `success` (intermediate HEAD `b8fa9533…`: run #47 / ID `33668185398`, check `ci` ID `100374971862` — `completed` / `success`) |
| Merge commit on `main` | `2a616450a107d7b7d53f39f5c3776baff1921515` — `Merge pull request #30 from Kvasha62/arena/01a06362-amazone-clone-production` — **the new baseline SHA** |
| CI on merge commit | ✅ green — GitHub Actions run #49 / ID `33671965689`, check run `ci` ID `100387493188` — `completed` / `success` |
| Files changed | `apps/core/tests/__init__.py` (new), `apps/core/tests/concurrency.py` (new), `apps/core/tests/test_concurrency_db.py` (new), `apps/core/tests/test_concurrency_runner.py` (new), `apps/reviews/tests/test_concurrency.py`, `apps/discounts/tests/test_concurrency.py`, `config/test_runner.py` |

Scope of PROD-015 (test infrastructure only): the executor-based concurrency
tests in `reviews` and `discounts` relied on `with ThreadPoolExecutor(...)`,
whose exit calls `shutdown(wait=True)` and could therefore wait indefinitely
for a stuck worker after `future.result(timeout=...)`. They now run through the
test-only bounded runner in `apps/core/tests/concurrency.py`
(`run_concurrent_jobs`, `WorkerSessionRegistry`, `ConcurrentJobsMixin`):
daemon worker threads joined against one shared deadline, server-side
`statement_timeout` / `lock_timeout` / `idle_in_transaction_session_timeout`
on worker sessions, `pg_terminate_backend()` of stuck worker sessions from the
main thread before Django teardown, bounded verification that the test
database has been released, and deterministic failure reporting with
diagnostics. All pre-existing concurrency and consistency assertions were
preserved; 14 runner-semantics tests and 7 PostgreSQL guarantee tests were
added; `apps.core.tests` was added to `TEST_APP_LABELS` in
`config/test_runner.py`. No production business logic, public API, model,
migration, dependency, CI workflow, or deployment changes were part of
PROD-015.

**CI incident `33644092110` — explicitly not attributed to PROD-015.**
GitHub Actions run #46 / ID `33644092110` (push of the PROD-014 merge commit
`d7e383b3a5ba1a4550d0e059234a570918b5b44a`) was `cancelled` on its first
attempt at 2026-09-02T15:31:14Z while the `Run tests` step was still in
progress; the same SHA passed unchanged on re-run (attempt 2, `success`,
2026-09-02T18:00:38Z). PROD-015 addressed the independently established
bounded-concurrency test lifecycle defect described above. It is **not**
recorded here as the proven root cause of that incident; the incident remains
a separate, unattributed item and is not resolved by this baseline advance.

PROD-014 and PROD-015 are recorded as completed/merged via PR #28 and PR #30
respectively, and both are part of the verified `main` state at the new
baseline SHA. No functional, model, schema, migration, dependency, CI workflow,
deployment, or educational-repository changes are introduced by PROD-016, and
no remaining audit finding is remediated by it — all remaining audit findings
are outside this ticket and remain unchanged. Architect review is required
before the Owner performs the final merge; the Architect/Assistant must not
merge this governance pull request.

## Historical baseline record — PROD-013 (superseded)

This record was current immediately before PROD-016. It is preserved for audit
and is now superseded by the approved baseline at the top of this document.

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

## Baseline advance — PROD-013 (merged; record preserved)

| Field | Value |
|---|---|
| Ticket | PROD-013 — Advance production baseline after PROD-012 (Issue #25) |
| Status | Documentation-only governance change; merged and completed (PR #26, merge commit `b80568fc9d675c25cca4d53f89fa79902d6dc916`, merged 2026-09-02T13:52:54Z) |
| Change class | That pull request changed only `docs/production/PRODUCTION_BASELINE.md` |
| Advance | Previous baseline `406a96c37b0d0249ea7f44b459a54dca8566561b` → `1b4f069c159b198d30ee82a71b65198ce11bd2b7` (`main`) |
| Completed production change | PROD-012 / PR #24 — merged 2026-09-02 |
| Verified on | 2026-09-02 |

PROD-012 is recorded as completed/merged via PR #24
(`PROD-012: Harden ShippingService synchronous exception handling (F-14)`);
it was merged 2026-09-02T13:36:56Z with merge commit
`1b4f069c159b198d30ee82a71b65198ce11bd2b7`, and CI on its final PR HEAD
`051f6ab31fdede888446c65868c00a92ded234af` was green (run #41 / ID
`33636174754`, check `ci` ID `100267488189` — `completed` / `success`).
Its ShippingService synchronous exception-handling hardening (F-14) is part of
the verified `main` state. No functional, model, schema, migration, dependency,
CI workflow, deployment, or educational-repository changes were introduced by
PROD-013.

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

