# SQLite/Turso to Neon Postgres Migration - Sign-Off Checklist

**Project:** Stream of Worship catalog database migration  
**Runbook:** v4 (`specs/sqlite_turso_to_neon_migration_runbook_v4.md`)  
**Date created:** 2026-05-08  
**Operator:** mhuang  

---

## Phase 1: Quiesce and Source Capture

### 1.1 All writers stopped before final snapshot

- [ ] No `sow-app` process running
- [ ] No `sow-admin` command running
- [ ] No analysis/LRC workers running
- [ ] No background Turso sync in progress

**Verification:**
```bash
ps aux | grep -E 'sow-app|sow-admin|stream_of_worship' | grep -v grep
```

### 1.2 `lsof`/`fuser` confirms no open DB handles

- [ ] No process holds the admin DB file open

**Verification:**
```bash
# Linux
fuser ~/.config/sow-admin/db/sow.db
# macOS
lsof ~/.config/sow-admin/db/sow.db
```

**Pre-flight status (2026-05-08):** PASS - `fuser` reports no open handles on the admin DB.

### 1.3 Final Turso sync completed or intentionally skipped with reason

- [ ] Turso sync executed and timestamp recorded, OR
- [ ] Intentionally skipped with documented reason

**If skipping, document reason here:**
> _[Enter reason]_

**Verification (if syncing):**
```bash
uv run --extra admin sow-admin db sync
```

### 1.4 Source freshness proof completed, or local-authority exception signed off

- [ ] Turso primary export/counts match local replica, OR
- [ ] Turso primary cannot be queried/exported - local-authority exception signed off

**If local-authority exception, document reason here:**
> _[Enter reason]_

### 1.5 SQLite backup copy created with `.backup`

- [ ] Backup created at `specs/migration/snapshots/sow_source_<timestamp>.db`

**Command:**
```bash
mkdir -p specs/migration/snapshots
sqlite3 ~/.config/sow-admin/db/sow.db ".backup 'specs/migration/snapshots/sow_source_$(date +%Y%m%dT%H%M%S).db'"
```

### 1.6 Backup copy passes `integrity_check` and `foreign_key_check`

- [ ] `PRAGMA integrity_check` returns `ok`
- [ ] `PRAGMA foreign_key_check` returns empty (no violations)

**Pre-flight status (2026-05-08):** Source DB passes both checks.

**Verification:**
```bash
sqlite3 specs/migration/snapshots/sow_source_<timestamp>.db "PRAGMA integrity_check;"
sqlite3 specs/migration/snapshots/sow_source_<timestamp>.db "PRAGMA foreign_key_check;"
```

### 1.7 JSON validity scan passes on all JSON-like text columns

- [ ] All `songs.lyrics_lines` values parse as valid JSON (or are NULL)
- [ ] All `songs.sections` values parse as valid JSON (or are NULL)
- [ ] All `recordings.beats` values parse as valid JSON (or are NULL)
- [ ] All `recordings.downbeats` values parse as valid JSON (or are NULL)
- [ ] All `recordings.sections` values parse as valid JSON (or are NULL)
- [ ] All `recordings.embeddings_shape` values parse as valid JSON (or are NULL)

**Pre-flight status (2026-05-08):** PASS - All JSON columns valid across 685 songs, 73 recordings.

**Verification:**
```bash
uv run --extra admin python specs/migration/scripts/02_load_data.py --dry-run --validate-json
# OR manual check:
python3 -c "
import sqlite3, json, sys
db = sqlite3.connect('specs/migration/snapshots/sow_source_<timestamp>.db')
errors = []
for sid, ll, sec in db.execute('SELECT id, lyrics_lines, sections FROM songs'):
    for col, val in [('lyrics_lines', ll), ('sections', sec)]:
        if val:
            try: json.loads(val)
            except: errors.append(f'songs:{sid}:{col}')
for ch, beats, db2, sec, es in db.execute('SELECT content_hash, beats, downbeats, sections, embeddings_shape FROM recordings'):
    for col, val in [('beats', beats), ('downbeats', db2), ('sections', sec), ('embeddings_shape', es)]:
        if val:
            try: json.loads(val)
            except: errors.append(f'recordings:{ch}:{col}')
print(f'{len(errors)} errors') if errors else print('PASS')
"
```

---

## Phase 2: Live Source Inventory

### 2.1 Live schema inventory includes migration-added columns

- [ ] `songs.deleted_at` present
- [ ] `recordings.youtube_url` present
- [ ] `recordings.visibility_status` present
- [ ] `recordings.deleted_at` present
- [ ] `recordings.download_status` present (added by `initialize_schema()` migration)

**Pre-flight status (2026-05-08):** Source DB has all columns EXCEPT `download_status` (ALTER TABLE migration hasn't been run yet).

**Current live schema:**
- `songs`: 17 columns (id through deleted_at)
- `recordings`: 28 columns (content_hash through deleted_at; download_status NOT yet present)
- `sync_metadata`: 3 columns (key, value, updated_at)

### 2.2 Postgres DDL reviewed against live inventory

- [ ] Every source column has an explicit target mapping in `01_schema.sql`
- [ ] Deprecated Turso-only metadata excluded or retained deliberately
- [ ] `sync_metadata` fate decided (see item 2.3)

---

## Phase 3: Role and Permission Verification

### 3.1 Admin RW and app RO Neon roles verified with valid mutation probes

- [ ] Admin DSN can INSERT a valid test row inside a transaction, then ROLLBACK
- [ ] App read-only DSN cannot INSERT a valid row (permission failure, not constraint)

**Verification:**
```sql
-- Admin DSN test:
BEGIN;
INSERT INTO songs (id, title, source_url, scraped_at, created_at, updated_at)
VALUES ('__test_write_probe', 'test', 'https://example.invalid/test', now()::text, now()::text, now()::text);
ROLLBACK;

-- App RO DSN test (must fail with permission error):
BEGIN;
INSERT INTO songs (id, title, source_url, scraped_at, created_at, updated_at)
VALUES ('__test_write_blocked', 'test', 'https://example.invalid/test', now()::text, now()::text, now()::text');
ROLLBACK;
```

### 3.2 App DSN write rejection fails for permission reasons

- [ ] Error is `ERROR: permission denied for table songs` (or equivalent)
- [ ] Error is NOT a NOT NULL, FK, type, or constraint violation

**Verification (via psql with app RO DSN):**
```sql
SELECT
  current_user,
  has_table_privilege(current_user, 'public.songs', 'INSERT') AS can_insert_songs,
  has_table_privilege(current_user, 'public.songs', 'UPDATE') AS can_update_songs,
  has_table_privilege(current_user, 'public.songs', 'DELETE') AS can_delete_songs;
```

---

## Phase 2/3: sync_metadata Decision

### 2.3 `sync_metadata` fate explicitly decided and documented

- [ ] Decision recorded: MIGRATE / EXCLUDE / RETAIN_AS_LEGACY

**Current consumers of `sync_metadata`:**
- `admin/db/client.py`: `update_sync_metadata()` - writes `last_sync_at`, `local_device_id`, `turso_generation_token`
- `admin/db/client.py`: reads sync metadata for Turso URL masking and auto-recovery
- `admin/db/schema.py`: checksum query includes `sync_metadata`
- `admin/commands/db.py`: Turso bootstrap seeds `sync_metadata`

**Recommended decision:** EXCLUDE from Neon migration. The `sync_metadata` table tracks Turso-specific state (last sync timestamp, device ID, generation token) that has no meaning in a Postgres context. All consumers are in the Turso sync path which will be replaced/disabled. Mark as legacy and skip.

---

## Phase 5/6: Pre-Cutover Documentation

### 3.3 DSN switch target documented before final load

- [ ] Target branch name recorded: `cutover-<timestamp>`
- [ ] Target endpoint hostname recorded
- [ ] Target database name recorded

**Record here:**
> Branch: _[enter]_
> Endpoint: _[enter]_
> Database: _[enter]_

### 3.4 Branch IDs and endpoint hostnames recorded for staging and cutover DSNs

- [ ] Staging branch ID:
- [ ] Staging admin endpoint hostname:
- [ ] Staging app RO endpoint hostname:
- [ ] Cutover branch ID:
- [ ] Cutover admin endpoint hostname:
- [ ] Cutover app RO endpoint hostname:

**Record here:**
> Staging branch ID: _[enter]_
> Staging admin endpoint: _[enter]_
> Staging app RO endpoint: _[enter]_
> Cutover branch ID: _[enter]_
> Cutover admin endpoint: _[enter]_
> Cutover app RO endpoint: _[enter]_

---

## Phase 5: Staging Dry Run

### 4.1 Staging dry run completed from scratch

- [ ] Neon staging branch reset/recreated
- [ ] `01_schema.sql` applied successfully
- [ ] `02_load_data.py` completed without errors
- [ ] `03_post_load.sql` applied successfully
- [ ] `04_verify.sql` passed

### 4.2 Staging re-run of loader is idempotent

- [ ] Re-running `02_load_data.py` produces no duplicate or conflicting data
- [ ] All rows use `ON CONFLICT DO NOTHING` or consistent UPDATE behavior

---

## Phase 6: Verification Gates

### 5.1 Source/target counts match

- [ ] `songs` count matches (source: 685)
- [ ] `recordings` count matches (source: 73)
- [ ] `sync_metadata` count matches if retained (source: 3)

**Pre-flight source counts (2026-05-08):**
| Table | Count |
|-------|-------|
| songs | 685 |
| recordings | 73 |
| sync_metadata | 3 |

### 5.2 Source/target checksums match or intentional transforms documented

- [ ] Per-table source and target checksums match
- [ ] Or any intentional transforms are documented

### 5.3 JSON/text payload checks pass

- [ ] All JSON payload columns in target parse correctly
- [ ] No truncation or encoding issues

### 5.4 App-critical query parity confirmed

- [ ] Total active songs matches
- [ ] Total active recordings matches
- [ ] Analyzed recordings count matches
- [ ] LRC-ready songs count matches

**Verification query (run on both source and target):**
```sql
SELECT COUNT(*)
FROM songs s
JOIN recordings r ON s.id = r.song_id
WHERE r.lrc_status = 'completed'
  AND r.visibility_status = 'published'
  AND r.deleted_at IS NULL
  AND s.deleted_at IS NULL;
```

### 5.5 R2 spot checks pass or gaps documented as pre-existing

- [ ] Sample of recordings with R2 URLs: HEAD requests return 200 or 404 documented as pre-existing

### 5.6 Local `songsets.db` references validated

- [ ] Every non-null `songset_items.song_id` resolves to a song in target
- [ ] Every non-null `songset_items.recording_hash_prefix` resolves to a recording in target

**Pre-flight status (2026-05-08):** PASS - All songset references resolve to catalog entries.

### 5.7 App tests/smoke checks pass on Neon

- [ ] App TUI can browse catalog
- [ ] App TUI can search songs
- [ ] App TUI can open existing songsets

### 5.8 Admin tests/smoke checks pass on Neon

- [ ] Admin can read catalog stats
- [ ] Admin can search catalog

---

## Phase 7: Final Cutover

### 6.1 Post-switch target identity checks confirm the validated branch/endpoint

- [ ] `sow-app` startup logs confirm correct branch/endpoint
- [ ] `sow-admin` startup logs confirm correct branch/endpoint
- [ ] Neither process connects to any branch other than the validated cutover branch

### 6.2 Rollback tested before accepting Neon writes

- [ ] Legacy config backed up (see 6.4)
- [ ] Legacy SQLite/Turso path confirmed working
- [ ] Admin can read catalog stats on legacy path
- [ ] App can browse catalog on legacy path

### 6.3 Legacy config backed up before DSN switch

- [ ] `~/.config/sow-admin/config.toml` backed up
- [ ] `~/.config/sow/config.toml` backed up

**Command:**
```bash
mkdir -p specs/migration/snapshots
cp ~/.config/sow-admin/config.toml specs/migration/snapshots/sow-admin_config_$(date +%Y%m%dT%H%M%S).bak
cp ~/.config/sow/config.toml specs/migration/snapshots/sow_app_config_$(date +%Y%m%dT%H%M%S).bak
```

### 6.4 Final cutover accepted

- [ ] All above items checked
- [ ] Post-switch smoke tests passed
- [ ] Cutover time recorded

**Cutover acceptance time:** _[enter]_

### 6.5 Post-cutover monitoring and backup plan documented

- [ ] Neon restore window documented
- [ ] pg_dump backup cadence decided
- [ ] Legacy Turso config retained or retired decision made

---

## Optional Hardening Checklist

### H.1 Stabilization audit triggers installed

- [ ] `06_stabilization_audit.sql` applied to cutover target (if using)

### H.2 Post-cutover accidental-write export script tested

- [ ] `05_export_post_cutover.py --dsn ... --cutover-time ...` produces valid output (if using)

### H.3 Cold-start latency tested and acceptable

- [ ] First query after idle period responds within acceptable time
- [ ] Recorded latency: _[enter]_ ms

### H.4 Pre-cutover production branch retained through stabilization

- [ ] Pre-cutover `production` branch exists and is not deleted
- [ ] Branch ID: _[enter]_

### H.5 Stabilization audit retention/removal decision documented

- [ ] Audit triggers: RETAIN / REMOVE after stabilization
- [ ] Decision date: _[enter]_

---

## Source DB State Snapshot (Pre-Migration)

Captured 2026-05-08 from `~/.config/sow-admin/db/sow.db`:

| Metric | Value |
|--------|-------|
| integrity_check | ok |
| foreign_key_check | (no violations) |
| songs count | 685 |
| recordings count | 73 |
| sync_metadata count | 3 |
| JSON validity | all columns PASS |

**Source status distributions:**

| Table | Column | Status | Count |
|-------|--------|--------|-------|
| recordings | analysis_status | completed | 13 |
| recordings | analysis_status | pending | 60 |
| recordings | lrc_status | completed | 20 |
| recordings | lrc_status | processing | 53 |
| recordings | visibility_status | (NULL) | 53 |
| recordings | visibility_status | published | 20 |

**Missing column note:** `recordings.download_status` is defined in code schema (`schema.py`, `models.py`, `client.py`) but not yet present in the live DB. The ALTER TABLE migration in `client.py:initialize_schema()` adds it when next invoked.

**Songsets reference check:** PASS - all `songset_items.song_id` and `songset_items.recording_hash_prefix` references resolve.
