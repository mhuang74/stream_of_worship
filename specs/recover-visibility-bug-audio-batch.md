# Recover `visibility_status` Reverted by `audio batch` Bug — Dry-Run Report

## Problem Summary

Approximately ~30 recordings in the SOW database had their `visibility_status` reverted from `'published'` to `'review'` due to a bug in the Admin CLI `audio batch` command. The bug fires when the batch poll loop detects a completed LRC job for a recording whose LRC had already been generated (and possibly manually edited and published) — it unconditionally overwrites `visibility_status` to `'review'` instead of preserving the existing value.

This document specifies a **read-only dry-run report** that identifies the suspected bug-reverted recordings by cross-referencing the R2 LRC file's `LastModified` timestamp against the DB `recordings.updated_at` timestamp. No DB writes, no code changes to the Admin CLI.

### User-confirmed scope

- **Deliverable**: Dry-run report only (no DB UPDATE, no code fix to `audio.py:5458`).
- **Run mode**: Inline Python invoked via `uv run --project ops/admin-cli --extra admin python -c "..."`. Nothing added to the Admin CLI command surface.
- **Candidate scope**: Filtered — `visibility_status='review' AND lrc_status='completed' AND r2_lrc_url IS NOT NULL AND deleted_at IS NULL`.
- **Analysis service lookups**: Off by default; opt-in via `--with-analysis` flag.

## Bug Root Cause

### Bug site 1 (primary): `_handle_lrc_completion()` in `audio batch` poll loop

**File**: `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py:5430-5536`

```python
if job.status == "completed":
    recording = db_client.get_recording_by_song_id(song_id)
    lrc_url = _confirm_r2_lrc(r2_client, recording.hash_prefix, console)

    if lrc_url:
        db_client.update_recording_lrc(
            recording.hash_prefix,
            lrc_url,
            visibility_status="review",   # <-- BUG: unconditionally overwrites
        )
```

### Bug site 2 (secondary): `audio status --sync`

**File**: `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py:2811-2822`

```python
# Sync LRC job
if rec.lrc_job_id and rec.lrc_status in ("pending", "processing"):
    try:
        job = client.get_job(rec.lrc_job_id)
        if job.status == "completed":
            if job.result and job.result.lrc_url:
                db_client.update_recording_lrc(
                    hash_prefix=rec.hash_prefix,
                    r2_lrc_url=job.result.lrc_url,
                    visibility_status="review",   # <-- BUG: same pattern
                )
```

### Why the bug overwrites without re-uploading the LRC

`_confirm_r2_lrc()` (`audio.py:6471-6497`) only verifies the existing R2 file via `head_object` — it does **not** re-upload. So when the bug fires on a recording whose LRC was already manually edited and published:

- R2 file at `{hash_prefix}/lyrics.lrc` is **untouched** → `LastModified` stays at the prior manual-edit timestamp `T_manual`.
- DB row is updated: `r2_lrc_url`, `lrc_status='completed'`, `visibility_status='review'`, `updated_at = NOW()` → `updated_at` becomes the bug-fire time `T_bug`.

### Contrast with the safe pattern

All other LRC completion / reconciliation paths correctly use `visibility_status=None`, which routes through `COALESCE(visibility_status, 'published')` in `update_recording_lrc()` (`db/client.py:1085-1138`):

- `audio.py:2516` — `audio status --reconcile`
- `audio.py:4946` — `_submit_lrc_for_song` (batch LRC submit)
- `audio.py:5564` — `_handle_lrc_404`
- `audio.py:6531` — `_reconcile_on_interrupt`
- `editor/upload.py:263` — admin LRC editor save (`upload_revised_lrc`)

## Signal Hypothesis

For each `'review'` recording with `lrc_status='completed'` and `r2_lrc_url IS NOT NULL`:

1. Fetch the R2 file's `LastModified` for `{hash_prefix}/lyrics.lrc` via `R2Client.get_lrc_identity(hash_prefix)` (`services/r2.py:458-484`).
2. Compare to DB `recordings.updated_at`.
3. Compute `delta_hours = (db_updated_at - r2_last_modified)` in hours.

### Verdict matrix

| `delta_hours` range | Verdict | Rationale |
|---|---|---|
| `<= 0.1` (~5 min) | `OK_FRESH_LRC` | LRC was just generated and visibility was set in the same operation. `'review'` is correct. |
| `1 <= delta <= 1440` (1h–60d) | `SUSPECTED_BUG_REVERT` | Visibility was flipped without an LRC re-upload → buggy revert of a manually-published LRC. Recommend restore to `'published'`. |
| otherwise | `INCONCLUSIVE` | Admin should eyeball. |

### Why this works

- **Bug-reverted recording**: `T_manual` (admin edited LRC via editor) < `T_bug` (batch fired). R2 file unchanged at `T_manual`; DB `updated_at` bumped to `T_bug`. Delta = hours to days.
- **Legitimately new LRC**: Analysis service generates LRC, uploads to R2 at `T_gen`, then `_handle_lrc_completion` runs at `T_handle ≈ T_gen`. Both timestamps within seconds/minutes.

### Optional cross-check via analysis service (`--with-analysis`)

For each `SUSPECTED_BUG_REVERT` row, call `AnalysisClient.get_job(lrc_job_id)` (`services/analysis.py`). The returned `JobInfo` has `created_at` and `updated_at` (`analysis.py:98-121`); when `status='completed'`, `updated_at` ≈ job completion time.

Expected ordering for the bug case:
```
job_completed_at  <  r2_last_modified  <  db_updated_at
```
i.e. the LRC job completed before the manual edit, the manual edit produced the current R2 file, and the bug later flipped visibility without re-uploading.

**Caveat**: The analysis service may have purged old jobs. On `AnalysisServiceError` / 404, annotate the row as `"job purged — relying on R2/DB timestamps only"` and rely on the primary signal.

## Schema Reality

The `recordings` table (`ops/admin-cli/src/stream_of_worship/admin/db/schema.py:38-90`) has **no dedicated LRC timestamp columns**. Relevant columns:

| Column | Type | Notes |
|---|---|---|
| `r2_lrc_url` | TEXT | S3 URL of the LRC file in R2 |
| `lrc_status` | TEXT | `pending` / `processing` / `completed` / `failed` |
| `lrc_job_id` | TEXT | External analysis-service job ID |
| `visibility_status` | TEXT | `published` / `review` / `hold` (NULL = unpublished) |
| `updated_at` | timestamptz | Generic row-level trigger on ANY update |
| `imported_at` | TEXT | ISO timestamp at import time |
| `created_at` | timestamptz | Row creation time |
| `deleted_at` | timestamptz | Soft delete; NULL = active |

There is **no** `lrc_updated_at`, `lrc_job_completed_at`, `lrc_synced_at`, or `lrc_published_at` column. There is **no** `lrc_jobs` table — LRC job state lives in the analysis service and is referenced via `lrc_job_id`.

The `updated_at` column is updated via a `BEFORE UPDATE` trigger (`schema.py:188-194`):
```sql
CREATE TRIGGER trg_recordings_updated_at
    BEFORE UPDATE ON recordings
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();
```

## Implementation

### Run command

```bash
uv run --project ops/admin-cli --extra admin python -c "
import sys; sys.path.insert(0, 'src')
# ... script body ...
" \
  [--since 2026-07-10] [--until 2026-07-20] \
  [--album <name>] [--min-delta-hours 1] [--max-delta-hours 1440] \
  [--with-analysis] [--csv]
```

### Script structure

1. **Build clients** using the same configuration flow the Admin CLI uses:
   - `AdminConfig.from_env()` (or equivalent) → instantiate `DatabaseClient`, `R2Client`.
   - Optionally `AnalysisClient` if `--with-analysis`.

2. **Candidate SQL** (filtered, as agreed):
   ```sql
   SELECT r.hash_prefix, r.song_id, r.lrc_job_id, r.r2_lrc_url,
          r.visibility_status, r.lrc_status,
          r.updated_at AS db_updated_at, r.imported_at,
          s.title AS song_title, s.album AS song_album
   FROM recordings r
   LEFT JOIN songs s ON r.song_id = s.id
   WHERE r.visibility_status = 'review'
     AND r.lrc_status = 'completed'
     AND r.r2_lrc_url IS NOT NULL
     AND r.deleted_at IS NULL
     AND (s.deleted_at IS NULL OR s.id IS NULL)
   ORDER BY r.updated_at DESC;
   ```
   Apply `--since` / `--until` / `--album` as additional WHERE clauses if provided.

3. **R2 lookup per candidate**:
   - `r2_client.get_lrc_identity(hash_prefix)` returns `R2ObjectIdentity(exists, etag, last_modified)`.
   - Parse ISO `last_modified`; ensure timezone-aware UTC for the subtraction.
   - Handle missing file (should not happen given `r2_lrc_url IS NOT NULL`, but be defensive): annotate `INCONCLUSIVE` with reason `"R2 file missing"`.

4. **Verdict logic**:
   - `delta_hours = (db_updated_at - r2_last_modified).total_seconds() / 3600`
   - `OK_FRESH_LRC` if `abs(delta_hours) <= 0.1`
   - `SUSPECTED_BUG_REVERT` if `min_delta_hours <= delta_hours <= max_delta_hours` (defaults `1` and `1440`)
   - `INCONCLUSIVE` otherwise

5. **Cross-check (optional, `--with-analysis`)**:
   - For `SUSPECTED_BUG_REVERT` rows, call `analysis_client.get_job(lrc_job_id)`.
   - Capture service-side `updated_at` (job completion time).
   - Tolerate `AnalysisServiceError` / 404 → annotate `"job purged"`.
   - Print `job_completed_at` column alongside the other timestamps.

6. **Output**: Rich table to stdout (or CSV via `--csv`).

### Output columns

| Column | Source | Notes |
|---|---|---|
| `hash_prefix` | DB | Recording identifier |
| `song_id` | DB | Song ID |
| `album` | DB | Album name (for context) |
| `title` | DB | Song title (for context) |
| `db_updated_at` | DB | When visibility was last flipped (bug-fire time) |
| `r2_last_modified` | R2 `head_object` | When the LRC file was last uploaded (manual edit time) |
| `delta_h` | computed | `db_updated_at - r2_last_modified` in hours |
| `lrc_job_id` | DB | Analysis service job ID |
| `job_completed_at` | analysis service (opt) | Service-side `updated_at` when status='completed' |
| `verdict` | computed | `SUSPECTED_BUG_REVERT` / `OK_FRESH_LRC` / `INCONCLUSIVE` |
| `recommendation` | derived | `set-visibility published` for `SUSPECTED_BUG_REVERT`; otherwise `—` |

## Expected Pattern for the ~30 Bug-Reverted Recordings

- `db_updated_at` clusters around one or more bug-run times (admin can spot the cluster).
- `delta_h` falls within hours-to-days range.
- If `--with-analysis`: `job_completed_at < r2_last_modified < db_updated_at` (the smoking gun).
- After clustering, the `SUSPECTED_BUG_REVERT` count ≈ ~30.

## Known False-Positive Risk & Mitigation

The same signature (`db_updated_at > r2_last_modified`) also arises for recordings an admin intentionally reverted to `'review'` via `sow-admin audio set-visibility`. Mitigations baked into the report:

1. **Cluster analysis**: The bug run usually produces a tight batch (e.g. all within a few hours, ~30 rows). Intentional single reverts will look like outliers. The report sorts by `db_updated_at DESC` so clusters are visually obvious.
2. **`--since` / `--until` filters**: If the admin remembers the rough bug-run date, restrict the window to refine the suspected set.
3. **Analysis service cross-check** (`--with-analysis`): The `job_completed_at < r2_last_modified < db_updated_at` ordering is unique to the bug. An intentional `set-visibility` doesn't change `lrc_job_id` and doesn't correspond to a stale-completion event.

## Out of Scope

Per the user's "dry-run only" preference:

- **No code fix** to the bug at `audio.py:5458` and `audio.py:2820` (changing `visibility_status="review"` → `visibility_status=None`).
- **No bulk `UPDATE recordings SET visibility_status='published'`** restore.
- **No new Admin CLI subcommand** added to `commands/audio.py`.

These can be addressed in a follow-up spec if the admin reviews the dry-run report and decides to proceed.

## References

- Bug site 1: `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py:5430-5536` (`_handle_lrc_completion`)
- Bug site 2: `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py:2811-2822` (`audio status --sync`)
- `_confirm_r2_lrc`: `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py:6471-6497`
- `update_recording_lrc`: `ops/admin-cli/src/stream_of_worship/admin/db/client.py:1085-1138`
- `R2Client.get_lrc_identity`: `ops/admin-cli/src/stream_of_worship/admin/services/r2.py:458-484`
- `R2Client.upload_official_lrc`: `ops/admin-cli/src/stream_of_worship/admin/services/r2.py:598-696`
- Admin LRC editor save: `ops/admin-cli/src/stream_of_worship/admin/editor/upload.py:184-279`
- `JobInfo` (analysis service): `ops/admin-cli/src/stream_of_worship/admin/services/analysis.py:98-121`
- Recordings schema: `ops/admin-cli/src/stream_of_worship/admin/db/schema.py:38-90`
- `updated_at` trigger: `ops/admin-cli/src/stream_of_worship/admin/db/schema.py:188-194`
- Existing `--reconcile` pattern (safe): `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py:2507-2527`
