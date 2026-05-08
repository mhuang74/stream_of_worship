# SQLite/Turso to Neon Postgres Migration Runbook v3

## Objective

Migrate the existing SQLite/Turso-backed catalog database to Neon Postgres with:

- deterministic source capture from the real current catalog state
- explicit Postgres schema and application compatibility gates
- offline cutover optimized for the current operating model: one operator, one user
- rollback that does not risk losing writes accepted after cutover

This version updates the original runbook for a sole-user migration performed while the
system is not being used, and incorporates risk-mitigation hardening from the v2 review (see
`docs/sqlite_turso_to_neon_migration_risk_assessment.md`).

## Operating Assumptions

- The migration operator is currently the sole user of the system.
- The migration will run during a full local downtime window:
  - no `sow-admin` commands running
  - no `sow-app` TUI running
  - no background Turso sync in progress
  - no analysis/LRC job expected to update catalog rows during the migration
- The safest rollback point is the pre-migration SQLite/Turso state.
- No writes are allowed to Neon production until final validation is accepted.

If any of these assumptions stop being true, use the stricter multi-user controls from the
original runbook: longer write freeze, formal incident log, and explicit write replay plan.

## Scope

In scope:

- catalog schema and data migration from SQLite/Turso/libSQL to Neon Postgres
- admin/app database connection cutover from SQLite/Turso to Neon
- validation of local-only songsets against the migrated catalog
- production rollback readiness

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

- Do not assume the admin local replica is current until a final sync and validation have
  completed.
- Prefer a Turso primary export if available and repeatable.
- If using the admin local replica, capture it only after quiescing all processes and
  verifying sync freshness.

## Required Artifacts

Prepare these files during implementation:

- `specs/migration/sql/01_schema.sql`
- `specs/migration/scripts/02_load_data.py` (must be atomic / idempotent)
- `specs/migration/sql/03_post_load.sql`
- `specs/migration/sql/04_verify.sql`
- `specs/migration/scripts/05_export_post_cutover.py`
- `specs/migration/checklists/cutover_checklist.md`
- `specs/migration/reports/<timestamp>_source_inventory.md`
- `specs/migration/reports/<timestamp>_verification.md`

## Phase 0: Environment and Role Preparation

1. Create or identify Neon resources:
   - `production` branch for final accepted catalog (never delete or recreate)
   - `staging` branch for rehearsals
   - `cutover-<timestamp>` branch for final load before DSN switch (mandatory)
2. Create separate Neon roles/DSNs:
   - admin read-write DSN
   - app read-only DSN
   - staging/admin test DSN
   - staging/app read-only test DSN
3. Store secrets outside the repository:
   - `NEON_DATABASE_URL_PROD_ADMIN`
   - `NEON_DATABASE_URL_PROD_APP_READONLY`
   - `NEON_DATABASE_URL_STAGING_ADMIN`
   - `NEON_DATABASE_URL_STAGING_APP_READONLY`
4. Confirm current legacy configuration:
   - `~/.config/sow-admin/config.toml`
   - `~/.config/sow/config.toml`
   - `SOW_TURSO_TOKEN`
   - `SOW_TURSO_READONLY_TOKEN`
5. Confirm R2 settings remain unchanged:
   - `SOW_R2_ACCESS_KEY_ID`
   - `SOW_R2_SECRET_ACCESS_KEY`
   - `[r2]` config section
6. **(New in v3)** Verify Neon project auto-suspend settings for the app read-only compute;
   document expected cold-start latency and test at least once before cutover.

Exit criteria:

- Neon project, branches, roles, and DSNs exist.
- Read-only app DSN cannot write catalog tables.
- Legacy config and tokens are available for rollback.
- Neon cold-start latency is documented and acceptable.

## Phase 1: Quiesce and Capture a Trusted Source Snapshot

1. Stop all local processes that could touch the catalog:
   - close `sow-app`
   - stop any `sow-admin` command
   - stop analysis/LRC workers if they can write catalog status back through admin commands
2. **(New in v3)** Confirm no process has the admin DB open:

   ```bash
   # macOS
   lsof ~/.config/sow-admin/db/sow.db
   # Linux
   fuser ~/.config/sow-admin/db/sow.db
   ```

3. Run final Turso sync from the admin environment if Turso is configured:

   ```bash
   uv run --extra admin sow-admin db sync
   ```

4. Record sync result and timestamp.
5. Capture source metadata from the synced admin DB:

   ```bash
   sqlite3 ~/.config/sow-admin/db/sow.db "PRAGMA integrity_check;"
   sqlite3 ~/.config/sow-admin/db/sow.db "PRAGMA foreign_key_check;"
   sqlite3 ~/.config/sow-admin/db/sow.db ".tables"
   ```

6. Create a consistent SQLite backup using the SQLite backup API through the CLI:

   ```bash
   mkdir -p specs/migration/snapshots
   sqlite3 ~/.config/sow-admin/db/sow.db ".backup 'specs/migration/snapshots/sow_source_<timestamp>.db'"
   ```

7. Also archive the raw database and relevant sidecars for forensics after all processes are closed:

   ```bash
   mkdir -p specs/migration/snapshots/sow_raw_<timestamp>
   cp ~/.config/sow-admin/db/sow.db* specs/migration/snapshots/sow_raw_<timestamp>/
   ```

8. Open the backup copy and verify it independently:

   ```bash
   sqlite3 specs/migration/snapshots/sow_source_<timestamp>.db "PRAGMA integrity_check;"
   sqlite3 specs/migration/snapshots/sow_source_<timestamp>.db "PRAGMA foreign_key_check;"
   ```

9. **(New in v3)** Pre-flight JSON validity scan: run `02_load_data.py --dry-run --validate-json`
   against the backup to ensure all JSON-like text columns parse cleanly before any schema load.
   Abort if any rows fail validation.

Exit criteria:

- All writers are stopped and `lsof`/`fuser` confirms no open handles.
- Final sync is recorded or intentionally skipped with reason.
- Backup copy passes integrity and FK checks.
- Raw files are archived for forensic fallback.
- **(New in v3)** JSON validity scan is green.

## Phase 2: Live Source Inventory

Use the verified backup copy as the inventory source. Do not rely only on static Python schema
constants, because the live database may contain migration-added columns.

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

3. Capture source content checksums in the loader/reporting script. At minimum, hash a stable
   ordered JSON/CSV representation of every table row.
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

6. **(New in v3)** Audit app/admin code for any reads from `sync_metadata` before deciding to
   exclude it. If any consumer exists, either migrate the table or file a follow-up code change.

Exit criteria:

- Inventory report is written.
- Live column list is complete.
- Source checksums and status distributions are recorded.
- **(New in v3)** `sync_metadata` fate is explicitly decided and documented.

## Phase 3: Postgres Schema Design

1. Produce explicit Postgres DDL from the live inventory, not only from static schema files.
2. Use stable IDs as-is:
   - `songs.id` remains text
   - `recordings.content_hash` remains text primary key
   - `recordings.hash_prefix` remains text unique
3. Map JSON-like text columns intentionally:
   - keep as `text` for minimal app change, or
   - convert to `jsonb` only if the app layer is updated and tested and the Phase 1 JSON scan
     confirms all rows are parseable
4. Use a consistent timestamp policy:
   - preferred: `timestamptz` for real timestamps
   - if minimizing app changes, preserve ISO strings as text and defer timestamp refactor
5. Recreate constraints and indexes:
   - PKs
   - unique constraint on `recordings.hash_prefix`
   - FK from `recordings.song_id` to `songs.id`
   - indexes used by current queries and filters
6. Recreate `updated_at` behavior with Postgres triggers if app code will continue relying on
   database-side timestamp updates.
7. Do not migrate `sync_metadata` blindly unless a replacement purpose is defined. If retained,
   mark it as legacy migration metadata rather than Turso sync state. If excluded, ensure no
   app code references it.

Exit criteria:

- `01_schema.sql` is reviewed against live inventory.
- Every source column has an explicit target mapping.
- Deprecated Turso-only metadata is either excluded deliberately or retained deliberately.

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
7. **(New in v3)** DSN sanity check: add a startup smoke test in `sow-admin` and `sow-app` that
   validates write permissions on the admin DSN (must succeed on a harmless test row) and
   confirms the app DSN rejects writes to catalog tables.

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
- The app read-only DSN cannot mutate catalog tables.
- Legacy Turso sync actions do not corrupt or overwrite Neon-backed config.
- **(New in v3)** Startup DSN permission checks pass for both admin and app.

## Phase 5: Staging Dry Run

1. Reset or recreate Neon `staging`.
2. Apply `01_schema.sql`.
3. Load data from the verified SQLite backup with `02_load_data.py`.
4. **(New in v3)** Confirm load idempotency: if a row already exists with the same primary key,
   the script must `ON CONFLICT DO NOTHING` or `UPDATE` consistently. Abort on any unexpected
   mismatch rather than silently overwriting.
5. Apply `03_post_load.sql`:
   - indexes
   - triggers
   - constraints
   - analyze/stat refresh
6. Run `04_verify.sql`.
7. Run app/admin smoke tests against staging DSNs.
8. Validate local `songsets.db` references against staging catalog:
   - every non-null `songset_items.song_id` resolves to a song
   - every non-null `songset_items.recording_hash_prefix` resolves to a recording
   - soft-deleted referenced records are reported as intentional or repaired

Exit criteria:

- Staging load is reproducible from scratch.
- Verification report is green.
- Local songsets do not have unexpected orphan references.
- **(New in v3)** Re-running `02_load_data.py` against the already-loaded staging produces a
  no-op (zero rows changed) rather than duplicate or conflicting data.

## Phase 6: Verification Gates

Run these gates on staging and again on final production/cutover target.

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

**Additional Neon-specific checks (New in v3):**

- Verify app DSN cannot write:

  ```sql
  -- This must fail with a permission error
  INSERT INTO songs (id, title) VALUES ('__test_write_blocked', 'test');
  ```

- Run at least one cold-start latency check: close all connections, wait 6 minutes (or the
  configured auto-suspend timeout), then run the first catalog query and record response time.

Exit criteria:

- Counts match source.
- Checksums match source or documented intentional transforms.
- No unexpected orphan records.
- App-critical query outputs match source.
- R2 spot checks pass or missing objects are documented as pre-existing.
- **(New in v3)** App DSN write rejection is confirmed.
- **(New in v3)** Cold-start latency is acceptable for TUI interaction.

## Phase 7: Final Offline Production Cutover

Because this is a sole-user offline migration, the production cutover can be simple. Keep the
system frozen until the final validation decision.

1. Announce local freeze to yourself and stop using the system.
2. Close `sow-app`, `sow-admin`, and any analysis workers.
3. **(New in v3)** Back up current legacy config before any changes:

   ```bash
   mkdir -p specs/migration/snapshots
   cp ~/.config/sow-admin/config.toml specs/migration/snapshots/sow-admin_config_<timestamp>.bak
   cp ~/.config/sow/config.toml specs/migration/snapshots/sow_app_config_<timestamp>.bak
   ```

4. Repeat Phase 1 to create a fresh final source snapshot.
5. Create a final target:
   - **mandatory: fresh `cutover-<timestamp>` branch** (never empty/recreated `production`)
6. Apply schema and load data.
7. Run all Phase 6 verification gates.
8. Run admin smoke tests against admin DSN.
9. Run app smoke tests against app read-only DSN.
10. Validate local `songsets.db` references against final target.
11. Switch config/env to Neon DSNs only after all checks pass.
12. Start `sow-app` and verify:
    - browse catalog
    - search songs
    - list albums/keys
    - open existing songsets
    - preview/download an R2 asset
    - **(New in v3)** acceptable cold-start UX after idle
13. Start `sow-admin` and verify read/write workflow on a harmless test row or controlled
    metadata update, then revert the test change if needed.
14. Accept cutover only after post-switch smoke tests pass.

Mandatory gates:

- Do not switch app/admin config until target verification is green.
- Do not perform real admin writes to Neon until rollback decision is closed.
- If any gate fails, keep legacy config and return to rollback.
- **(New in v3)** Never destroy or recreate the `production` branch; always load into a
  `cutover-<timestamp>` branch.

Exit criteria:

- Neon-backed app/admin smoke tests pass.
- Legacy SQLite/Turso files remain archived and untouched.
- Migration report records final source snapshot, target branch, checks, and acceptance time.
- **(New in v3)** Legacy config backups exist in `specs/migration/snapshots/`.

## Phase 8: Rollback Plan

Rollback is safe only while no real writes have been accepted on Neon.

Rollback triggers:

- final load fails
- verification fails
- app/admin compatibility failure
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
2. **(New in v3)** Export changed Neon rows since cutover time:

   ```bash
   uv run --extra admin specs/migration/scripts/05_export_post_cutover.py \
     --dsn "$NEON_DATABASE_URL_PROD_ADMIN" \
     --cutover-time "<ISO_TIMESTAMP>" \
     --output specs/migration/reports/<timestamp>_post_cutover_diff.json
   ```

3. Review the exported diff and decide whether to replay those changes into legacy SQLite/Turso
   or abandon them.
4. Do not resume legacy writes until that decision is explicit.

Exit criteria:

- Legacy app/admin path is confirmed working, or Neon is accepted as final.
- Any post-cutover writes are accounted for.

## Phase 9: Post-Cutover Hardening

1. Keep all source snapshots read-only for the stabilization window.
2. Keep Turso config and tokens available but unused until stabilization ends.
3. Configure Neon backup/restore expectations:
   - restore window
   - optional `pg_dump` backup cadence
4. Record final DSNs and roles in private ops notes.
5. Remove or disable legacy Turso sync UX only after Neon operation is stable.
6. Decommission legacy write path after the stabilization period.
7. Archive migration reports in `specs/migration/reports/`.
8. **(New in v3)** If cold starts remain an UX issue, consider disabling auto-suspend on the
   app read-only compute or increasing the idle timeout.

Exit criteria:

- Neon restore plan is documented.
- Legacy path is intentionally retained or intentionally retired.
- Follow-up tasks for code cleanup are filed.
- **(New in v3)** A decision on Neon auto-suspend is documented.

## Delta Summary (v3 vs v2)

| # | Change | Risk Addressed |
|---|--------|----------------|
| 1 | Mandatory `cutover-<timestamp>` branch; never recreate `production` | Operational #1 |
| 2 | `lsof`/`fuser` quiescence verification before backup | Operational #2 |
| 3 | Pre-flight JSON validity scan in Phase 1 | Data Loss #3 |
| 4 | Atomic/idempotent `02_load_data.py` with `ON CONFLICT` / no-op retry | Data Loss #2 |
| 5 | Legacy config backup before DSN switch | Operational #3 |
| 6 | `05_export_post_cutover.py` script for accidental-write recovery | Data Loss #4 |
| 7 | Neon cold-start latency documentation and testing | Operational #4 |
| 8 | Startup DSN write-permission smoke tests | Operational #5 |
| 9 | Audited `sync_metadata` exclusion with code-consumer check | Data Loss #5 |
| 10 | App DSN write rejection check in Phase 6 gates | Operational #5 |
| 11 | Staging dry-run idempotency confirmation | Data Loss #2 |
| 12 | Auto-suspend decision documented in Phase 9 | Operational #4 |

---

## Sign-Off Checklist

- [ ] All writers stopped before final snapshot
- [ ] `lsof`/`fuser` confirms no open DB handles
- [ ] Final Turso sync completed or intentionally skipped with reason
- [ ] SQLite backup copy created with `.backup`
- [ ] Backup copy passes `integrity_check` and `foreign_key_check`
- [ ] JSON validity scan passes on all JSON-like text columns
- [ ] Live schema inventory includes migration-added columns
- [ ] Postgres DDL reviewed against live inventory
- [ ] Admin RW and app RO Neon roles verified (including startup write checks)
- [ ] `sync_metadata` fate explicitly decided and documented
- [ ] Staging dry run completed from scratch
- [ ] Staging re-run of loader is idempotent (zero rows changed)
- [ ] Source/target counts match
- [ ] Source/target checksums match or intentional transforms documented
- [ ] JSON/text payload checks pass
- [ ] App-critical query parity confirmed
- [ ] R2 spot checks pass or gaps documented as pre-existing
- [ ] Local `songsets.db` references validated
- [ ] App tests/smoke checks pass on Neon
- [ ] Admin tests/smoke checks pass on Neon
- [ ] Rollback tested before accepting Neon writes
- [ ] Cold-start latency tested and acceptable
- [ ] Legacy config backed up before DSN switch
- [ ] Final cutover accepted
- [ ] Post-cutover monitoring and backup plan documented
