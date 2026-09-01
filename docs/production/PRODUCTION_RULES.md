# Production Repository Rules

These rules govern `Kvasha62/Amazone_Clone_Production`. They take effect with
the baseline recorded in [`PRODUCTION_BASELINE.md`](./PRODUCTION_BASELINE.md).

## 1. Role of this repository

- This repository is the **production** repository. It holds only code that is
  approved for production deployment.
- The upstream development repository `Kvasha62/Amazone_Clone` is the **source**.
  It is **read-only** from here: nothing in a production ticket may push to,
  modify, or otherwise alter the source repository.

## 2. Branch protection expectations for `main`

- `main` always points at an approved, CI-green commit.
- No direct pushes to `main`. All changes arrive through pull requests.
- Force-push and branch deletion on `main` are forbidden.
- Linear history is preferred; merges are performed by maintainers only.

## 3. Branch naming

| Prefix | Purpose |
|---|---|
| `prod/PROD-xxx-<slug>` | Production ticket work (docs, release prep, config) |
| `hotfix/PROD-xxx-<slug>` | Urgent production fix |
| `release/<version>` | Release preparation |

One ticket → one branch → one pull request.

## 4. Pull request requirements

Every production pull request must state:

1. the ticket ID,
2. the baseline SHA it is built on,
3. the CI status,
4. the exact list of files changed and why,
5. explicit confirmation that the source repository was not modified.

A pull request is merged only after CI is green and a maintainer approves.
Agent-created pull requests are never self-merged.

## 5. CI

- `.github/workflows/ci.yml` is the single authoritative pipeline
  (Django checks → migration check → migrate → tests, on PostgreSQL 18,
  Python 3.13).
- CI configuration is changed only by a ticket whose declared purpose is to
  change CI. Documentation tickets must never edit it.
- A red CI blocks merge. There is no override.

## 6. Change classes

| Class | Allowed content |
|---|---|
| Documentation | files under `docs/production/` and top-level docs |
| Configuration | deployment/runtime config, explicitly reviewed |
| Code promotion | code imported from the approved source revision |

A single pull request must not mix documentation with source-code changes.

## 7. Safety rules for automated agents

- Perform read-only verification before any write.
- If any mandatory precondition fails (wrong `main` SHA, missing CI workflow,
  red CI), stop and change nothing.
- Never modify the baseline commit, its history, or `.github/workflows/ci.yml`.
- Never merge a pull request unless the ticket explicitly requests it.
