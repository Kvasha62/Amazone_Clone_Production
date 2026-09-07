# ARCH-013 — Legacy Price Currency Migration Contract

- **Status:** Accepted
- **Date:** 2026-09-07
- **Scope:** pricing, catalog, merchants, currencies, database migration
- **Parent:** PROD-042B / #127

## Decision

The legacy independent `Price.currency` authority may be removed only after a deterministic, read-only, fail-closed preflight proves every existing `Price` is compatible with the authoritative commercial currency.

The authoritative path is:

`Price → ProductVariant → Product → Store → LegalEntity → accounting_currency`

Currency identity and precision are owned by the Currency Registry.

## Preflight invariants

For every existing `Price`:

1. Product exists and has exactly one valid commercial Store owner.
2. Store exists and resolves to exactly one LegalEntity.
3. LegalEntity has a non-null Accounting Currency present in the Currency Registry.
4. Legacy `Price.currency` is present, known, and valid.
5. Legacy `Price.currency` equals the LegalEntity Accounting Currency.
6. No amount is rewritten, rounded, converted, or otherwise transformed.

Any missing, ambiguous, conflicting, invalid, or unprovable condition blocks migration.

## Blocker classifications

Use deterministic classifications:

- `UNPROVABLE` — required historical/current relationship cannot be established.
- `AMBIGUOUS` — multiple valid candidates or ownership paths exist.
- `CONFLICT` — authoritative values disagree.
- `UNKNOWN` — required fact is unavailable or cannot be classified safely.

Every blocked `Price` must be reported individually with a stable record identifier and reason. Aggregate PASS is allowed only when zero blockers exist.

## PriceHistory

`PriceHistory.currency` is an explicit nullable historical snapshot.

- Existing rows remain `NULL` when historical currency is not provable.
- Targeted backfill is permitted only with record-specific authoritative evidence.
- Current `Price.currency`, current LegalEntity configuration, defaults, seeds, or assumptions are not historical evidence.
- Newly-created history must persist the authoritative Accounting Currency at creation time.
- Established historical currency is immutable.

## Schema transition

### Price

After a successful preflight and after all required application code is deployed:

1. Stop creation/update paths from treating `Price.currency` as business authority.
2. Ensure effective pricing currency is resolved exclusively through commercial ownership → LegalEntity Accounting Currency.
3. Remove the legacy `Price.currency` column in a dedicated schema migration.
4. Do not alter existing monetary amounts as part of this transition.

The migration must not execute if preflight reports any blocker.

### PriceHistory

1. Add `currency` as a nullable Currency Registry reference if not already present.
2. Preserve existing NULLs.
3. Populate only newly-created rows automatically from authoritative Accounting Currency.
4. Do not blanket-backfill legacy rows.
5. Enforce immutability at the application/domain boundary and with database constraints where technically appropriate.

## Transaction and locking strategy

The destructive schema transition must run as an atomic migration where supported by PostgreSQL and must be preceded by the read-only preflight against the same target schema state.

Application writes that could create incompatible `Price` records must be deployed/disabled before the destructive column removal. Migration-time locks must be kept as short as practical. The migration must never rely on concurrent application writes remaining compatible by assumption.

The migration must not perform data conversion or financial recalculation.

## Rollback

Rollback is schema rollback only where technically safe. No rollback procedure may rewrite established historical monetary facts.

Before destructive removal, a recoverable database backup/snapshot is required according to the production backup policy. If the migration cannot be safely reversed at the schema level, restoration from the pre-migration backup is the recovery mechanism.

## Tests and CI gates

Required before authorization:

- model and migration tests;
- every legacy Price compatibility case;
- missing/ambiguous/conflict/unprovable ownership cases;
- currency mismatch case;
- legacy PriceHistory NULL preservation;
- targeted evidence-based backfill case;
- rejection of blanket/default/current-state backfill;
- new PriceHistory currency snapshot;
- immutability tests;
- no amount rewrite / no FX tests;
- `python manage.py check --fail-level WARNING`;
- `python manage.py makemigrations --check --dry-run`;
- `python manage.py spectacular --file schema.yml --validate`;
- full test suite;
- `git diff --check`;
- green GitHub CI.

## Runtime evidence required

Before production migration authorization, retain:

- target database/environment identifier without secrets;
- application commit SHA;
- migration/preflight version;
- timestamp;
- successful database connectivity evidence;
- complete human-readable preflight result;
- machine-readable JSON result;
- exit code;
- counts of Products, Variants, Prices, PriceHistory and blockers;
- proof that the preflight performed no writes;
- backup/snapshot evidence required by production policy.

CI fixtures do not substitute for target-database evidence.

## Prohibitions

This contract explicitly forbids:

- defaulting legacy currency to RUB;
- copying current currency into historical records;
- FX conversion during migration;
- silent amount rewriting;
- blanket historical backfill;
- deleting or discarding unknown historical facts;
- inferring historical Store activity from unsupported current state;
- executing the destructive migration while any blocker remains.

## Release sequence

`Target DB read-only preflight (#126) → ARCH-013 contract → implementation (#111) → CI → Architect review → target preflight re-run → Owner migration authorization → production migration → post-migration verification`
