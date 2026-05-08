# SQLite/Turso to Neon Postgres Migration Runbook

## Objective

Migrate the existing SQLite/Turso-backed catalog database to Neon Postgres with:

- reproducible dry runs
- clear cutover and rollback procedure
- verification gates before production traffic switch

Source database files:

- `~/.config/sow-admin/db/sow.db*`

This runbook is planning-only and does not require immediate code changes.

## Scope

In scope:

- schema + data migration from SQLite to Neon Postgres
- app/admin connection cutover from SQLite/Turso to Neon
- production validation and rollback readiness

Out of scope:

- feature refactors unrelated to migration
- query-level optimization beyond minimum required indexes
- non-database infra changes

## Assumptions

- Python runtime is `3.11`.
- `uv` is the package manager for project tooling.
- Neon project/organization access is available.
- Migration can be done with a short write freeze for safer cutover.

## Roles

- Migration owner: executes runbook and signs off each gate.
- Reviewer: validates schema mapping and cutover checklist.
- Operator: performs production cutover commands.

## Phase 0: Prepare Environment

1. Create Neon project and branches:
   - `production` (default root)
   - `staging` (for full dry runs)
2. Collect and store secrets:
   - `NEON_DATABASE_URL_PROD`
   - `NEON_DATABASE_URL_STAGING`
3. Confirm local source database files exist:
   - `~/.config/sow-admin/db/sow.db`
   - optional `~/.config/sow-admin/db/sow.db-wal`
   - optional `~/.config/sow-admin/db/sow.db-shm`
4. Define migration window and change-freeze notification plan.

Exit criteria:

- Neon project/branches ready
- access credentials verified
- source DB files confirmed

## Phase 1: Source Inventory and Snapshot

1. Take immutable backup of source files before any migration rehearsal:
   - copy `sow.db`, and include `-wal`/`-shm` if present
2. Record source metadata:
   - table list
   - row counts per table
   - index list
   - foreign key declarations
3. Extract schema DDL from SQLite for mapping review.
4. Identify SQLite-specific patterns needing conversion:
   - `INTEGER PRIMARY KEY` semantics
   - booleans stored as integer
   - datetime text/epoch formats
   - JSON stored as text

Suggested inventory commands:

```bash
sqlite3 ~/.config/sow-admin/db/sow.db ".tables"
sqlite3 ~/.config/sow-admin/db/sow.db ".schema"
sqlite3 ~/.config/sow-admin/db/sow.db "PRAGMA foreign_key_list('<table_name>');"
```

Exit criteria:

- schema snapshot stored
- row count baseline stored
- mapping risks documented

## Phase 2: Target Schema Design (Postgres)

1. Produce explicit Postgres DDL from SQLite schema.
2. Decide type mappings table-by-table, including:
   - integer/text/real/blob equivalents
   - boolean conversion
   - timestamp with/without timezone policy
3. Recreate constraints and indexes in Postgres intentionally.
4. Define sequence/identity handling for PK continuity.
5. Review with maintainer before first load.

Output artifacts to prepare:

- `specs/migration/sql/01_schema.sql` (planned)
- `specs/migration/sql/03_post_load.sql` (planned)

Exit criteria:

- reviewed Postgres DDL approved

## Phase 3: Data Load Dry Run (Neon Staging)

1. Reset/refresh Neon `staging` branch from `production`.
2. Apply target schema DDL on `staging`.
3. Load data table-by-table from SQLite snapshot.
4. Apply post-load steps:
   - indexes
   - constraints
   - sequence alignment
5. Run database analyze/stat refresh as needed.

Planned loader artifact:

- `specs/migration/scripts/02_load_data.py` (planned)

Exit criteria:

- full load completes with no fatal errors
- all constraints/indexes applied successfully

## Phase 4: Verification Gates (Staging)

Run and record all checks:

1. Row count parity (source vs staging target) for every table.
2. PK uniqueness checks.
3. FK integrity checks.
4. Nullability and unique constraint checks.
5. Spot-check sampled records on critical tables.
6. Application smoke tests against Neon staging DSN.

Verification SQL template examples:

```sql
-- row count
SELECT COUNT(*) FROM <table_name>;

-- duplicate PK check
SELECT <pk_col>, COUNT(*) 
FROM <table_name>
GROUP BY <pk_col>
HAVING COUNT(*) > 1;
```

Exit criteria:

- all gate checks pass
- app smoke tests pass against staging

## Phase 5: App Compatibility Review

Before production cutover, ensure SQL/dialect compatibility is addressed:

- placeholder style differences
- upsert syntax differences
- `last_insert_rowid()` replacement
- datetime function behavior differences
- transaction behavior assumptions

Validation command set (no implementation in this runbook):

```bash
PYTHONPATH=src uv run --python 3.11 --extra app --extra test pytest tests/ \
  --ignore=tests/services/analysis \
  --ignore=services/qwen3/tests \
  --ignore=services/analysis/tests -v
```

Exit criteria:

- tests pass with Neon-backed configuration
- no unresolved SQL compatibility blockers

## Phase 6: Production Cutover

1. Announce write freeze start.
2. Stop/disable writers to SQLite/Turso path.
3. Take final source snapshot.
4. Run final incremental/full load to Neon `production`.
5. Execute verification gates on production target:
   - row counts
   - integrity checks
   - critical query checks
6. Switch application/admin DSNs to Neon production.
7. Run post-switch smoke tests.
8. Lift freeze after validation pass.

Mandatory gate before traffic switch:

- Do not switch DSN until verification is green.

## Phase 7: Rollback Plan

Rollback triggers:

- verification failure after load
- critical app workflow failure after DSN switch
- sustained error rate increase post-cutover

Rollback steps:

1. Re-enable previous SQLite/Turso DSN/config.
2. Restart services using old config.
3. Confirm write path restored to old system.
4. Preserve Neon migrated state for forensic diff (do not destroy immediately).
5. Open incident log with failure checkpoint.

## Phase 8: Post-Cutover Hardening

1. Keep SQLite snapshot read-only during stabilization window.
2. Define Neon backup/PITR and recovery rehearsal cadence.
3. Standardize branch workflow (`production`/`staging`/feature branches).
4. Add migration artifacts and outcomes to repo docs/reports.
5. Decommission legacy write path after agreed stabilization period.

## Recommended Repository Artifacts (Next Step)

Planned files to create in implementation phase:

- `specs/migration/sql/01_schema.sql`
- `specs/migration/scripts/02_load_data.py`
- `specs/migration/sql/03_post_load.sql`
- `specs/migration/sql/04_verify.sql`
- `specs/migration/checklists/cutover_checklist.md`

## Sign-off Checklist

- [ ] Source DB snapshot captured
- [ ] Postgres schema mapping reviewed
- [ ] Staging dry run successful
- [ ] Verification gates green
- [ ] App tests/smoke checks green on Neon
- [ ] Production cutover window approved
- [ ] Rollback steps rehearsed
- [ ] Post-cutover monitoring ready

