# Research: Analysis Service LRC Completion Flow

> Refreshed 2026-09-22. Supersedes the earlier SQLite/Turso narrative — the
> admin catalog is now **PostgreSQL** (psycopg), and the LRC pipeline has a
> third transcription source (DashScope Qwen3 ASR). For the pipeline decision
> tree, see also `docs/lrc-job-flow.md`.

## Architecture Overview

Two-tier architecture. The **Analysis Service** owns a local SQLite job store
(`{CACHE_DIR}/jobs.db` via aiosqlite) plus R2 for artifacts. The **Admin CLI**
owns the catalog: **PostgreSQL accessed via `psycopg`**
(`ops/admin-cli/src/stream_of_worship/admin/db/client.py`). The Analysis
Service never writes to the catalog; it exposes job status over HTTP and the
Admin CLI pulls results and updates the catalog.

There is no Turso/libsql sync anymore; `sow-admin db init` runs the unified
Postgres DDL from `ops/admin-cli/src/stream_of_worship/db/postgres_schema.py`
(`ALL_SCHEMA_STATEMENTS`, all idempotent `CREATE ... IF NOT EXISTS` / `ADD
COLUMN IF NOT EXISTS`).

## LRC Pipeline Sources (`ops/analysis-service/src/sow_analysis/workers/queue.py`, `_process_lrc_job`)

`_process_lrc_job()` picks exactly one generation path, in priority order,
and records it in `lrc_source`:

| Priority | Source | `lrc_source` value | Trigger |
|---|---|---|---|
| 1 | YouTube transcript + LLM correction | `youtube_transcript` | `recordings.youtube_url` present; free-only mode waits out rate-limit circuit breakers, non-free mode falls through |
| 2 | DashScope Qwen3 ASR → canonical snap → LLM alignment | `qwen3_asr` | `options.use_qwen3_asr` (default true); quota exhaustion waits in free mode, errors fall back to Whisper |
| 3 | faster-Whisper transcription + LLM alignment | `whisper_asr` | Fallback; phrases cached under a language/prompt-aware key |
| — | Qwen3ForcedAligner (separate job type) | `forced_alignment` | `sow-admin lyrics align` |
| — | Admin manual paths (catalog-side, see below) | `manual_upload`, `r2_preexisting` | n/a |

The worker sets `lrc_source` inside `result_json` of the job store row and
returns it to the admin via `GET /api/v1/jobs/{id}` → `result.lrc_source`
(`JobResult.lrc_source`, `ops/analysis-service/src/sow_analysis/models.py`).

## How the Analysis Service Writes LRC Results

`_process_lrc_job()` in `ops/analysis-service/src/sow_analysis/workers/queue.py`:

1. Sets job status to `PROCESSING` in the local SQLite job store
2. Checks the LRC result cache (unless `force=True`); a cache hit with cached
   text re-uploads `lyrics.lrc` to R2 and returns `lrc_source` from the cache
3. Runs the pipeline (above) → writes LRC to a temp path via `_write_lrc()`
4. Uploads to R2 via `r2_client.upload_official_lrc(hash_prefix, lrc_path,
   expected_etag=official_lrc_etag)` — ETag captured at job start for
   stale-object protection (fails the job if a human edited the official LRC
   mid-flight); a `.bak` backup of the previous file is kept
5. Saves `{lrc_url, line_count, lrc_source, lrc_text}` to the local disk cache
   (`cache_manager.save_lrc_result`, composite key `content_hash + lyrics_hash`)
6. Sets job status `COMPLETED` with `result_json` containing
   `{lrc_url, line_count, lrc_source}`

**Job retention:** `JobStore.purge_old_jobs(max_age_days=7)` runs at every
service startup (`queue.py` `initialize()`), deleting completed/failed/
cancelled jobs older than 7 days. **The job store is therefore NOT a durable
record of LRC provenance** — which is why `lrc_source` is now persisted in
the catalog (below).

## Complete Flow: LRC Job Submission to Catalog Update

### Phase A: Admin CLI submits job and records intent

1. `sow-admin lyrics generate` (`ops/admin-cli/.../commands/lyrics.py`) →
   `submit_lrc_single()` / `submit_lrc_batch()` in
   `ops/admin-cli/.../services/lrc_jobs.py` → HTTP POST `/api/v1/jobs/lrc`
2. Admin CLI immediately updates the catalog:
   `db_client.update_recording_status(hash_prefix=..., lrc_status="processing",
   lrc_job_id=job_id)`

### Phase B: Analysis Service processes the job

See "LRC Pipeline Sources" and "How the Analysis Service Writes LRC Results"
above.

### Phase C: Admin CLI retrieves results and updates catalog

**Mechanism 1: Synchronous wait (`lyrics generate --wait`)**
- `submit_lrc_single()` calls `analysis_client.wait_for_completion()` which
  polls `GET /api/v1/jobs/{job_id}` every 30s
- On `status=completed`, calls
  `db_client.update_recording_lrc(hash_prefix, r2_lrc_url=...,
  visibility_status="review", lrc_source=job.result.lrc_source)`

**Mechanism 2: Async sync (`sow-admin audio status --sync`)**
- Iterates recordings with `lrc_status IN ('pending', 'processing')`
- For each, queries the Analysis Service API for the job status
- If completed with an `lrc_url`, calls `db_client.update_recording_lrc()`
  (now passing `lrc_source=job.result.lrc_source`)
- If failed, calls `db_client.update_recording_status(lrc_status="failed")`

### Phase D: No external sync needed

Postgres is the single source of truth; there is no separate sync step.

## LRC Provenance in the Catalog (`recordings.lrc_source`)

Column: `recordings.lrc_source TEXT` (39th column of
`RECORDING_COLUMNS_SELECT`; `RECORDING_COLUMN_COUNT = 39`). NULL = legacy row
predating the column, or source unknown.

| Value | Written by |
|---|---|
| `youtube_transcript` | LRC job completed via YouTube transcript path (`audio status --sync`, `lyrics generate --wait`, batch completion) |
| `qwen3_asr` | LRC job completed via DashScope Qwen3 ASR path (same mechanisms) |
| `whisper_asr` | LRC job completed via Whisper fallback path (same mechanisms) |
| `forced_alignment` | `sow-admin lyrics align` completion |
| `manual_upload` | `sow-admin lyrics upload`, LRC editor save (`editor/upload.py`), `audio status --force-status --force-url` |
| `r2_preexisting` | Reconciliation paths that found `lyrics.lrc` already on R2 (`audio status --reconcile`, batch skip-on-R2, lost-job recovery) |
| NULL | Recorded before this column existed, or source unknown |

`update_recording_lrc(lrc_source=...)` uses
`lrc_source = COALESCE(%s, lrc_source)`: passing `None` preserves any existing
value; passing a value overwrites.

### Querying by source

```bash
# All LRCs generated from YouTube transcripts (pipeable IDs)
sow-admin audio list --lrc-source youtube_transcript --format ids

# Re-run LRC for that source after a pipeline enhancement
sow-admin audio list --lrc-source youtube_transcript --format ids \
  | sow-admin lyrics generate --force --stdin

# Source quality breakdown (psql)
SELECT lrc_source, COUNT(*) FROM recordings
WHERE r2_lrc_url IS NOT NULL AND deleted_at IS NULL
GROUP BY lrc_source ORDER BY 2 DESC;

# Cross-reference with user feedback (lyrics_feedback, ADR 0007)
SELECT r.lrc_source, f.rating, COUNT(*) FROM lyrics_feedback f
JOIN recordings r ON r.content_hash = f.recording_content_hash
GROUP BY r.lrc_source, f.rating;
```

### Historical recordings (backfill)

The analysis service purges job rows after 7 days, so the job store holds at
most ~1 week of `lrc_source` values — a bulk backfill migration is not worth
building. Legacy rows keep `lrc_source = NULL` (filterable as `--lrc-source
none`). Sources are recorded going forward from the deployment of this
change; to (re)populate known rows, re-run the LRC pipeline with `--force`.

## Storage Layer Summary (`ops/analysis-service/src/sow_analysis/storage/`)

- **`db.py`** (`JobStore`): Local SQLite via `aiosqlite` for job state
  persistence; 7-day purge of terminal jobs on startup.
- **`r2.py`** (`R2Client`): `upload_official_lrc()` stores LRC at
  `{hash_prefix}/lyrics.lrc` with ETag stale-object protection + backup; also
  handles audio downloads, stem uploads, analysis result uploads.
- **`cache.py`** (`CacheManager`): LRC results cached as
  `{content_hash+lyrics_hash}` → `{lrc_url, line_count, lrc_source, lrc_text}`;
  also caches Whisper and Qwen3 ASR transcriptions.
