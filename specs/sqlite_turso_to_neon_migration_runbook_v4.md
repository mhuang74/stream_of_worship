# SQLite/Turso to Neon Postgres Migration Runbook v4

## Objective

Migrate the existing SQLite/Turso-backed catalog database to Neon Postgres with:

- deterministic source capture from the real current catalog state
- explicit Postgres schema and application compatibility gates
- offline cutover optimized for the current operating model: one operator, one user
- rollback that does not risk losing writes accepted after cutover
- unambiguous Neon branch, endpoint, and DSN switch semantics

This version updates v3 by closing the remaining high-priority gaps from the
risk review, while keeping the default execution path appropriate for a
one-person offline migration:

- source freshness must be proven, not only asserted after a local sync
- the final `cutover-<timestamp>` branch must be explicitly selected by DSN/endpoint
- read-only DSN write tests must fail for permission reasons, not constraint reasons
- heavier audit and accidental-write recovery controls are optional hardening, not
  default blockers

## Operating Assumptions

- The migration operator is currently the sole user of the system.
- The migration will run during a full local downtime window:
  - no `sow-admin` commands running
  - no `sow-app` TUI running
  - no background Turso sync in progress
  - no analysis/LRC job expected to update catalog rows during the migration
- The safest rollback point is the pre-migration SQLite/Turso state.
- No real writes are allowed to Neon until final validation is accepted.
- Neon branches are isolated environments; validating a `cutover-<timestamp>` branch is
  not equivalent to validating `production` unless the configured DSNs point to the
  cutover branch endpoint.

If any of these assumptions stop being true, use stricter multi-user controls:
longer write freeze, formal incident log, and explicit write replay plan.

## Execution Profiles

Use the **one-person offline profile** by default. It is intentionally rigorous
only where mistakes are likely to cause data loss or confusing rollback.

Required gates for the one-person offline profile:

- source freshness proof, or explicit local-authority sign-off
- SQLite `.backup`, integrity checks, and raw sidecar archive
- JSON validity scan for JSON-like payload columns
- atomic/idempotent loader
- staging dry run
- row counts, checksums, status distributions, and app-critical query parity
- local `songsets.db` reference validation
- fresh `cutover-<timestamp>` branch
- branch/endpoint check before config switch and once after app/admin restart
- valid admin/app DSN permission probes
- legacy config backup before DSN switch
- rollback smoke test before accepting real Neon writes

Optional hardening for this one-person migration:

- temporary stabilization audit triggers
- testing post-cutover accidental-write export/replay tooling before cutover
- branch promotion/rename/reset workflows
- detailed DSN identity reports for every rehearsal command
- cold-start latency as a cutover gate

Use the optional hardening items if the migration window becomes multi-user,
if real writes must be accepted immediately after cutover, or if you cannot
re-run the migration cheaply from the verified SQLite snapshot.

## Scope

In scope:

- catalog schema and data migration from SQLite/Turso/libSQL to Neon Postgres
- admin/app database connection cutover from SQLite/Turso to Neon
- validation of local-only songsets against the migrated catalog
- production rollback readiness
- branch/endpoint/DSN verification for the final cutover target

Out of scope:

- unrelated feature refactors
- query tuning beyond required indexes and correctness/performance smoke tests
- R2 object migration; R2 URLs remain metadata in the database
- migrating local-only `songsets.db` into Neon

## Source Systems

Catalog source candidates:

- Turso remote primary database
- admin embedded replica: `~/.config/sow-admin/db/sow.db`
- admin sidecars when present:
  - `~/.config/sow-admin/db/sow.db-wal`
  - `~/.config/sow-admin/db/sow.db-shm`
  - libSQL metadata sidecars such as `sow.db-info`

Local-only user data:

- user app catalog replica: `~/.config/sow/db/sow.db`
- user app songsets database: `~/.config/sow/db/songsets.db`

Important source-of-truth rule:

- Prefer a Turso primary export if available and repeatable.
- Do not assume the admin local replica is current until freshness has been proven
  against the Turso primary or explicitly signed off as authoritative.
- If using the admin local replica, capture it only after quiescing all processes,
  running final sync, and recording a freshness proof.

Acceptable freshness proofs:

- Turso primary export is used as the source snapshot.
- Turso primary row counts/checksums match the local admin replica after sync.
- Turso primary cannot be queried/exported, and the operator records an explicit
  exception stating why the local admin replica is authoritative.

## Required Artifacts

Prepare these files during implementation:

- `specs/migration/sql/01_schema.sql`
- `specs/migration/scripts/02_load_data.py` (must be atomic and retry-safe)
- `specs/migration/sql/03_post_load.sql`
- `specs/migration/sql/04_verify.sql`
- `specs/migration/checklists/cutover_checklist.md`
- `specs/migration/reports/<timestamp>_source_inventory.md`
- `specs/migration/reports/<timestamp>_verification.md`
- `specs/migration/reports/<timestamp>_branch_dsn_verification.md`

Optional hardening artifacts:

- `specs/migration/scripts/05_export_post_cutover.py`
- `specs/migration/sql/06_stabilization_audit.sql`

## Phase 0: Environment, Branch, and Role Preparation

1. Create or identify Neon resources:
   - `production` branch for the last accepted production catalog baseline
   - `staging` branch for rehearsals
   - `cutover-<timestamp>` branch for final load before DSN switch
2. Never delete or recreate the existing `production` branch before cutover acceptance.
3. Use the simple one-person cutover mechanism:
   - production config/env DSNs are switched to the validated `cutover-<timestamp>`
     branch endpoint after all gates pass
   - do not use branch promotion/rename/reset unless you explicitly opt into the
     optional hardening path and document before/after branch IDs and endpoints
4. Create separate Neon roles/DSNs:
   - admin read-write DSN for the final target branch
   - app read-only DSN for the final target branch
   - staging/admin test DSN
   - staging/app read-only test DSN
5. Store secrets outside the repository:
   - `NEON_DATABASE_URL_PROD_ADMIN`
   - `NEON_DATABASE_URL_PROD_APP_READONLY`
   - `NEON_DATABASE_URL_STAGING_ADMIN`
   - `NEON_DATABASE_URL_STAGING_APP_READONLY`
6. Confirm current legacy configuration:
   - `~/.config/sow-admin/config.toml`
   - `~/.config/sow/config.toml`
   - `SOW_TURSO_TOKEN`
   - `SOW_TURSO_READONLY_TOKEN`
7. Confirm R2 settings remain unchanged:
   - `SOW_R2_ACCESS_KEY_ID`
   - `SOW_R2_SECRET_ACCESS_KEY`
   - `[r2]` config section
8. Record branch IDs, endpoint hostnames, database name, and role names for the
   staging and cutover DSNs used by app/admin.
9. Optional: verify Neon auto-suspend settings for the app read-only compute and
   document expected cold-start latency.

Exit criteria:

- Neon project, branches, roles, and DSNs exist.
- DSN switch target is documented before execution.
- Final target branch ID and endpoint hostnames are known.
- Read-only app DSN cannot write catalog tables for permission reasons.
- Legacy config and tokens are available for rollback.
- Optional cold-start latency check is documented if performed.

## Phase 1: Quiesce and Capture a Trusted Source Snapshot

1. Stop all local processes that could touch the catalog:
   - close `sow-app`
   - stop any `sow-admin` command
   - stop analysis/LRC workers if they can write catalog status back through admin commands
2. Confirm no process has the admin DB open:

   ```bash
   # macOS
   lsof ~/.config/sow-admin/db/sow.db
   # Linux
   fuser ~/.config/sow-admin/db/sow.db
   ```

3. If Turso is configured, run final Turso sync from the admin environment:

   ```bash
   uv run --extra admin sow-admin db sync
   ```

4. Record sync result and timestamp.
5. Prove source freshness:
   - preferred: export or query the Turso primary directly and compare counts/checksums
     with the local admin replica
   - acceptable exception: record why Turso primary export/query is unavailable and
     explicitly sign off that the local admin replica is authoritative
6. Capture source metadata from the synced admin DB:

   ```bash
   sqlite3 ~/.config/sow-admin/db/sow.db "PRAGMA integrity_check;"
   sqlite3 ~/.config/sow-admin/db/sow.db "PRAGMA foreign_key_check;"
   sqlite3 ~/.config/sow-admin/db/sow.db ".tables"
   ```

7. Create a consistent SQLite backup using the SQLite backup API:

   ```bash
   mkdir -p specs/migration/snapshots
   sqlite3 ~/.config/sow-admin/db/sow.db ".backup 'specs/migration/snapshots/sow_source_<timestamp>.db'"
   ```

8. Archive the raw database and relevant sidecars for forensics after all processes are closed:

   ```bash
   mkdir -p specs/migration/snapshots/sow_raw_<timestamp>
   cp ~/.config/sow-admin/db/sow.db* specs/migration/snapshots/sow_raw_<timestamp>/
   ```

9. Open the backup copy and verify it independently:

   ```bash
   sqlite3 specs/migration/snapshots/sow_source_<timestamp>.db "PRAGMA integrity_check;"
   sqlite3 specs/migration/snapshots/sow_source_<timestamp>.db "PRAGMA foreign_key_check;"
   ```

10. Pre-flight JSON validity scan: run `02_load_data.py --dry-run --validate-json`
    against the backup to ensure all JSON-like text columns parse cleanly before any
    schema load. Abort if any rows fail validation.

Exit criteria:

- All writers are stopped and `lsof`/`fuser` confirms no open handles.
- Final sync is recorded or intentionally skipped with reason.
- Source freshness proof or explicit local-authority exception is recorded.
- Backup copy passes integrity and FK checks.
- Raw files are archived for forensic fallback.
- JSON validity scan is green.

## Phase 2: Live Source Inventory

Use the verified backup copy as the inventory source. Do not rely only on static Python
schema constants, because the live database may contain migration-added columns.

1. Capture live schema details:

   ```bash
   sqlite3 specs/migration/snapshots/sow_source_<timestamp>.db ".schema"
   sqlite3 specs/migration/snapshots/sow_source_<timestamp>.db "PRAGMA table_info(songs);"
   sqlite3 specs/migration/snapshots/sow_source_<timestamp>.db "PRAGMA table_info(recordings);"
   sqlite3 specs/migration/snapshots/sow_source_<timestamp>.db "PRAGMA table_info(sync_metadata);"
   sqlite3 specs/migration/snapshots/sow_source_<timestamp>.db "PRAGMA index_list(songs);"
   sqlite3 specs/migration/snapshots/sow_source_<timestamp>.db "PRAGMA index_list(recordings);"
   sqlite3 specs/migration/snapshots/sow_source_<timestamp>.db "PRAGMA foreign_key_list(recordings);"
   ```

2. Capture row counts:

   ```sql
   SELECT 'songs', COUNT(*) FROM songs
   UNION ALL
   SELECT 'recordings', COUNT(*) FROM recordings
   UNION ALL
   SELECT 'sync_metadata', COUNT(*) FROM sync_metadata;
   ```

3. Capture source content checksums in the loader/reporting script. At minimum,
   hash a stable ordered JSON/CSV representation of every table row.
4. Explicitly verify known migration-added columns:
   - `songs.deleted_at`
   - `recordings.youtube_url`
   - `recordings.visibility_status`
   - `recordings.deleted_at`
   - `recordings.download_status`
5. Record source status distributions:

   ```sql
   SELECT analysis_status, COUNT(*) FROM recordings GROUP BY analysis_status;
   SELECT lrc_status, COUNT(*) FROM recordings GROUP BY lrc_status;
   SELECT visibility_status, COUNT(*) FROM recordings GROUP BY visibility_status;
   SELECT download_status, COUNT(*) FROM recordings GROUP BY download_status;
   ```

6. Audit app/admin code for reads from `sync_metadata` before deciding to exclude it.
   If any consumer exists, either migrate the table or file a blocking compatibility change.

Exit criteria:

- Inventory report is written.
- Live column list is complete.
- Source checksums and status distributions are recorded.
- `sync_metadata` fate is explicitly decided and documented.

## Phase 3: Postgres Schema Design

1. Produce explicit Postgres DDL from the live inventory, not only from static schema files.
2. Use stable IDs as-is:
   - `songs.id` remains text
   - `recordings.content_hash` remains text primary key
   - `recordings.hash_prefix` remains text unique
3. Map JSON-like text columns intentionally:
   - keep as `text` for minimal app change, or
   - convert to `jsonb` only if the app layer is updated and tested and the Phase 1
     JSON scan confirms all rows are parseable
4. Use a consistent timestamp policy:
   - preferred: `timestamptz` for real timestamps
   - if minimizing app changes, preserve ISO strings as text and defer timestamp refactor
5. Recreate constraints and indexes:
   - PKs
   - unique constraint on `recordings.hash_prefix`
   - FK from `recordings.song_id` to `songs.id`
   - indexes used by current queries and filters
6. Recreate `updated_at` behavior with Postgres triggers if app code continues relying
   on database-side timestamp updates.
7. Optional hardening: for the stabilization window, install audit triggers with
   `06_stabilization_audit.sql` on mutable catalog tables if real writes may be
   accepted before rollback confidence is high. The audit log should capture inserts,
   updates, and deletes with timestamp, table name, operation, primary key, and row
   payload.
8. Do not migrate `sync_metadata` blindly unless a replacement purpose is defined. If
   retained, mark it as legacy migration metadata rather than Turso sync state. If
   excluded, ensure no app code references it.

Exit criteria:

- `01_schema.sql` is reviewed against live inventory.
- Every source column has an explicit target mapping.
- Deprecated Turso-only metadata is either excluded deliberately or retained deliberately.
- Optional stabilization audit decision is documented.

## Phase 4: Application Compatibility Work

This is a blocking phase before production cutover.

1. Implement or configure Postgres-backed database clients for:
   - admin read-write catalog operations
   - app read-only catalog operations
2. Remove or isolate SQLite/libSQL-only operations from the Neon path:
   - `?` placeholders
   - `INSERT OR REPLACE`
   - `datetime('now')`
   - `PRAGMA`
   - `sqlite_master`
   - local file connection assumptions
   - embedded-replica `sync()`
3. Replace upserts with Postgres `INSERT ... ON CONFLICT`.
4. Replace read-only app sync UX with a Neon-specific refresh/no-op behavior.
5. Keep local `songsets.db` on SQLite unless explicitly migrating it later.
6. Ensure local songsets still resolve catalog references through:
   - `song_id`
   - `recording_hash_prefix`
7. Add startup DSN checks in `sow-admin` and `sow-app`:
   - admin DSN must successfully run a harmless valid write inside a transaction
     that is rolled back
   - app DSN must reject a fully valid catalog write with a permission error
   - both checks must log enough target identity to confirm the expected branch/endpoint

Validation command:

```bash
PYTHONPATH=src uv run --python 3.11 --extra app --extra test pytest tests/ \
  --ignore=tests/services/analysis \
  --ignore=services/qwen3/tests \
  --ignore=services/analysis/tests -v
```

Exit criteria:

- Admin smoke tests pass against Neon staging admin DSN.
- App smoke tests pass against Neon staging read-only DSN.
- App read-only DSN cannot mutate catalog tables for permission reasons.
- Legacy Turso sync actions do not corrupt or overwrite Neon-backed config.
- Startup DSN permission and target-identity checks pass for both admin and app.

## Phase 5: Staging Dry Run

1. Reset or recreate Neon `staging`.
2. Apply `01_schema.sql`.
3. Optional: apply `06_stabilization_audit.sql` if audit triggers are part of the
   planned cutover.
4. Load data from the verified SQLite backup with `02_load_data.py`.
5. Confirm load idempotency:
   - if a row already exists with the same primary key, the script must
     `ON CONFLICT DO NOTHING` or `UPDATE` consistently
   - abort on any unexpected mismatch rather than silently overwriting
6. Apply `03_post_load.sql`:
   - indexes
   - triggers
   - constraints
   - analyze/stat refresh
7. Run `04_verify.sql`.
8. Run app/admin smoke tests against staging DSNs.
9. Run DSN identity checks and record at least the branch/endpoint target used by
   the smoke tests:
   - branch ID
   - endpoint hostname
   - database name
   - current role
10. Validate local `songsets.db` references against staging catalog:
    - every non-null `songset_items.song_id` resolves to a song
    - every non-null `songset_items.recording_hash_prefix` resolves to a recording
    - soft-deleted referenced records are reported as intentional or repaired

Exit criteria:

- Staging load is reproducible from scratch.
- Verification report is green.
- Local songsets do not have unexpected orphan references.
- Re-running `02_load_data.py` against already-loaded staging produces a no-op
  rather than duplicate or conflicting data.
- DSN identity check proves tests ran against the intended branch.

## Phase 6: Verification Gates

Run these gates on staging and again on the final cutover target.

Required table parity:

```sql
SELECT COUNT(*) FROM songs;
SELECT COUNT(*) FROM recordings;
SELECT COUNT(*) FROM sync_metadata; -- if retained
```

Required integrity checks:

```sql
SELECT id, COUNT(*) FROM songs GROUP BY id HAVING COUNT(*) > 1;
SELECT content_hash, COUNT(*) FROM recordings GROUP BY content_hash HAVING COUNT(*) > 1;
SELECT hash_prefix, COUNT(*) FROM recordings GROUP BY hash_prefix HAVING COUNT(*) > 1;
SELECT r.song_id
FROM recordings r
LEFT JOIN songs s ON s.id = r.song_id
WHERE r.song_id IS NOT NULL AND s.id IS NULL;
SELECT content_hash, hash_prefix
FROM recordings
WHERE hash_prefix <> substring(content_hash from 1 for 12);
```

Required status checks:

```sql
SELECT analysis_status, COUNT(*) FROM recordings GROUP BY analysis_status;
SELECT lrc_status, COUNT(*) FROM recordings GROUP BY lrc_status;
SELECT visibility_status, COUNT(*) FROM recordings GROUP BY visibility_status;
SELECT download_status, COUNT(*) FROM recordings GROUP BY download_status;
```

Required content checks:

- Compare per-table source and target checksums.
- Parse JSON/text payload columns where expected:
  - `songs.lyrics_lines`
  - `songs.sections`
  - `recordings.beats`
  - `recordings.downbeats`
  - `recordings.sections`
  - `recordings.embeddings_shape`
- Spot-check representative rows:
  - active song with published LRC
  - soft-deleted song
  - recording with completed analysis
  - recording with failed/pending status
  - recording with R2 audio/LRC URLs
- Run R2 `HEAD` spot checks for published recordings with non-null R2 URLs.

Required app-critical checks:

- total active songs
- total active recordings
- analyzed recordings
- LRC-ready songs:

  ```sql
  SELECT COUNT(*)
  FROM songs s
  JOIN recordings r ON s.id = r.song_id
  WHERE r.lrc_status = 'completed'
    AND r.visibility_status = 'published'
    AND r.deleted_at IS NULL
    AND s.deleted_at IS NULL;
  ```

Required Neon-specific checks:

- Verify app DSN write privileges at the privilege layer:

  ```sql
  SELECT
    current_user,
    current_database(),
    inet_server_addr(),
    has_table_privilege(current_user, 'public.songs', 'INSERT') AS can_insert_songs,
    has_table_privilege(current_user, 'public.songs', 'UPDATE') AS can_update_songs,
    has_table_privilege(current_user, 'public.songs', 'DELETE') AS can_delete_songs;
  ```

- Verify app DSN cannot write with a fully valid mutation probe:

  ```sql
  BEGIN;
  INSERT INTO songs (
    id, title, source_url, scraped_at, created_at, updated_at
  )
  VALUES (
    '__test_write_blocked',
    'test',
    'https://example.invalid/test',
    now()::text,
    now()::text,
    now()::text
  );
  ROLLBACK;
  ```

  This must fail with a permission error before `ROLLBACK`. A NOT NULL, FK, type,
  or constraint error is not sufficient proof.

- Verify admin DSN can write with the same fully valid mutation probe inside a
  transaction and then roll it back.
- Record branch/endpoint identity for the DSNs used in final verification.
- Optional UX check: close all connections, wait longer than the configured
  auto-suspend timeout, then run the first catalog query and record response time.

Exit criteria:

- Counts match source.
- Checksums match source or documented intentional transforms.
- No unexpected orphan records.
- App-critical query outputs match source.
- R2 spot checks pass or missing objects are documented as pre-existing.
- App DSN write rejection is confirmed as a permission failure.
- Admin DSN valid write probe succeeds and rolls back.
- Verification report includes target branch ID and endpoint hostnames.
- Optional cold-start result is documented if tested.

## Phase 7: Final Offline Production Cutover

Because this is a sole-user offline migration, the production cutover can be simple.
Keep the system frozen until the final validation decision.

1. Announce local freeze to yourself and stop using the system.
2. Close `sow-app`, `sow-admin`, and any analysis workers.
3. Back up current legacy config before any changes:

   ```bash
   mkdir -p specs/migration/snapshots
   cp ~/.config/sow-admin/config.toml specs/migration/snapshots/sow-admin_config_<timestamp>.bak
   cp ~/.config/sow/config.toml specs/migration/snapshots/sow_app_config_<timestamp>.bak
   ```

4. Repeat Phase 1 to create a fresh final source snapshot and final source freshness proof.
5. Create final target:
   - mandatory: fresh `cutover-<timestamp>` branch
   - never empty, delete, reset, or recreate the existing `production` branch during this phase
6. Record target identity before loading:
   - branch ID
   - endpoint ID
   - endpoint hostname
   - database name
   - admin role
   - app read-only role
7. Apply schema. Apply audit triggers only if optional stabilization audit is enabled.
8. Load data.
9. Run all Phase 6 verification gates against the `cutover-<timestamp>` DSNs.
10. Run admin smoke tests against the cutover admin DSN.
11. Run app smoke tests against the cutover read-only DSN.
12. Validate local `songsets.db` references against the cutover target.
13. Switch app/admin config/env to the DSNs whose endpoint hostname matches the
    validated `cutover-<timestamp>` branch.
14. Immediately after config/env switch, run target identity checks from inside
    `sow-app` and `sow-admin`. Abort if either process connects to any branch other
    than the validated cutover branch.
15. Start `sow-app` and verify:
    - browse catalog
    - search songs
    - list albums/keys
    - open existing songsets
    - preview/download an R2 asset
    - optional: acceptable cold-start UX after idle
16. Start `sow-admin` and verify read/write workflow on a harmless valid test row
    inside a rollback transaction.
17. Accept cutover only after post-switch smoke tests and target identity checks pass.

Mandatory gates:

- Do not switch app/admin config until target verification is green.
- Do not perform real admin writes to Neon until rollback decision is closed.
- If any gate fails, keep or restore legacy config and return to rollback.
- Never destroy or recreate the `production` branch as part of loading.
- Do not accept cutover unless production config/env points to the same branch/endpoint
  that passed final verification.
- Do not use branch promotion/rename/reset during the one-person path.

Exit criteria:

- Neon-backed app/admin smoke tests pass.
- Post-switch app/admin identity checks prove they are connected to the validated target.
- Legacy SQLite/Turso files remain archived and untouched.
- Migration report records final source snapshot, target branch, endpoint hostnames,
  checks, DSN switch action, and acceptance time.
- Legacy config backups exist in `specs/migration/snapshots/`.

## Phase 8: Rollback Plan

Rollback is simplest and safest while no real writes have been accepted on Neon.

Rollback triggers:

- final load fails
- verification fails
- app/admin compatibility failure
- branch/endpoint/DSN identity mismatch
- unexpected missing catalog/songset/R2 references
- Neon connection/auth issue

Rollback steps before accepting Neon writes:

1. Restore legacy config values:
   - admin Turso/SQLite config
   - app Turso/SQLite config
2. Ensure `sow-app` and `sow-admin` point back to the legacy DB paths.
3. Run legacy smoke checks:
   - `sow-admin` can read catalog stats
   - `sow-app` can browse catalog and open songsets
4. Preserve Neon target branch for forensic diff.
5. Do not delete source snapshots.

If real writes were accidentally accepted on Neon before rollback:

1. Stop all writers immediately.
2. Best-effort export changed Neon rows since cutover time:

   ```bash
   uv run --extra admin specs/migration/scripts/05_export_post_cutover.py \
     --dsn "$NEON_DATABASE_URL_PROD_ADMIN" \
     --cutover-time "<ISO_TIMESTAMP>" \
     --output specs/migration/reports/<timestamp>_post_cutover_diff.json
   ```

3. If optional audit triggers were installed, export audit rows from the stabilization
   audit table and treat that as the primary source for inserts, updates, and deletes.
4. If audit triggers were not installed, treat the timestamp-based export as a
   recovery aid with known limitations. Verify that mutable tables have reliable
   `updated_at` coverage and that hard deletes were impossible before trusting it.
5. Review the exported diff and decide whether to replay those changes into legacy
   SQLite/Turso or abandon them.
6. Do not resume legacy writes until that decision is explicit.

Exit criteria:

- Legacy app/admin path is confirmed working, or Neon is accepted as final.
- Any post-cutover writes are either accounted for or explicitly abandoned before
  legacy writes resume.

## Phase 9: Post-Cutover Hardening

1. Keep all source snapshots read-only for the stabilization window.
2. Keep Turso config and tokens available but unused until stabilization ends.
3. Keep the pre-cutover `production` branch and final cutover branch until the
   stabilization window closes if storage cost is acceptable.
4. Configure Neon backup/restore expectations:
   - restore window
   - optional `pg_dump` backup cadence
5. Record final DSNs, roles, branch IDs, and endpoint hostnames in private ops notes.
6. Remove or disable legacy Turso sync UX only after Neon operation is stable.
7. Decommission legacy write path after the stabilization period.
8. Archive migration reports in `specs/migration/reports/`.
9. If cold starts remain a UX issue, consider disabling auto-suspend on the app
   read-only compute or increasing the idle timeout where the Neon plan allows it.
10. If optional audit triggers were installed, decide whether to remove them after
    stabilization.

Exit criteria:

- Neon restore plan is documented.
- Legacy path is intentionally retained or intentionally retired.
- Follow-up tasks for code cleanup are filed.
- Optional auto-suspend and audit-trigger decisions are documented if tested/enabled.

## Delta Summary (v4 vs v3)

| # | Change | Risk Addressed |
|---|--------|----------------|
| 1 | Added mandatory source freshness proof or explicit local-authority exception | Stale source capture |
| 2 | Added simple branch/endpoint/DSN switch semantics before final load | Cutover branch ambiguity |
| 3 | Added branch ID and endpoint hostname recording for staging and cutover DSNs | DSN mix-ups |
| 4 | Changed read-only write probe to require a fully valid row and permission failure | False-pass DSN test |
| 5 | Added privilege-layer checks with `has_table_privilege` | DSN permission validation |
| 6 | Added post-switch target identity checks inside app/admin | Wrong target after cutover |
| 7 | Downgraded stabilization audit triggers to optional hardening | One-person scope control |
| 8 | Downgraded timestamp-based post-cutover export to best-effort recovery aid unless audit is enabled | One-person scope control |
| 9 | Preserving both pre-cutover production and cutover branch is recommended through stabilization | Forensic rollback safety |
| 10 | Added one-person offline execution profile with required vs optional gates | Scope clarity |

---

## Sign-Off Checklist

- [ ] All writers stopped before final snapshot
- [ ] `lsof`/`fuser` confirms no open DB handles
- [ ] Final Turso sync completed or intentionally skipped with reason
- [ ] Source freshness proof completed, or local-authority exception signed off
- [ ] SQLite backup copy created with `.backup`
- [ ] Backup copy passes `integrity_check` and `foreign_key_check`
- [ ] JSON validity scan passes on all JSON-like text columns
- [ ] Live schema inventory includes migration-added columns
- [ ] Postgres DDL reviewed against live inventory
- [ ] Admin RW and app RO Neon roles verified with valid mutation probes
- [ ] App DSN write rejection fails for permission reasons
- [ ] `sync_metadata` fate explicitly decided and documented
- [ ] DSN switch target documented before final load
- [ ] Branch IDs and endpoint hostnames recorded for staging and cutover DSNs
- [ ] Staging dry run completed from scratch
- [ ] Staging re-run of loader is idempotent
- [ ] Source/target counts match
- [ ] Source/target checksums match or intentional transforms documented
- [ ] JSON/text payload checks pass
- [ ] App-critical query parity confirmed
- [ ] R2 spot checks pass or gaps documented as pre-existing
- [ ] Local `songsets.db` references validated
- [ ] App tests/smoke checks pass on Neon
- [ ] Admin tests/smoke checks pass on Neon
- [ ] Post-switch target identity checks confirm the validated branch/endpoint
- [ ] Rollback tested before accepting Neon writes
- [ ] Legacy config backed up before DSN switch
- [ ] Final cutover accepted
- [ ] Post-cutover monitoring and backup plan documented

Optional hardening checklist:

- [ ] Stabilization audit triggers installed
- [ ] Post-cutover accidental-write export script tested
- [ ] Cold-start latency tested and acceptable
- [ ] Pre-cutover production branch retained through stabilization
- [ ] Stabilization audit retention/removal decision documented
