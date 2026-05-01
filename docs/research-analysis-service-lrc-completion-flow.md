# Research: Analysis Service LRC Completion Flow

## Architecture Overview

The system uses a **two-tier architecture** for database management. The Analysis Service has its own local SQLite job store, while the catalog database (with Turso sync) lives in the Admin CLI. The Analysis Service **never writes to Turso directly**. Instead, it writes to its own local SQLite database and to R2, and the Admin CLI is responsible for pulling results and updating its own local SQLite (which syncs to Turso).

## How the Analysis Service Writes LRC Results

The LRC generation pipeline is in `services/analysis/src/sow_analysis/workers/lrc.py`. The `generate_lrc()` function (line 664) returns a tuple of `(Path, int, List[WhisperPhrase])` -- the LRC file path, line count, and Whisper phrases. It writes the LRC file to a local temporary path via `_write_lrc()` (line 490).

The orchestration happens in `_process_lrc_job()` in `services/analysis/src/sow_analysis/workers/queue.py` (line 509). This method:

1. Sets job status to `PROCESSING` in the local SQLite job store (line 529)
2. Calls `generate_lrc()` to produce the LRC file locally
3. Uploads the LRC to R2 (line 852)
4. Saves the result to the local disk cache (line 856)
5. Sets job status to `COMPLETED` in the local SQLite job store with the `lrc_url` and `line_count` in `result_json` (lines 860-878)

## Database Storage: Local SQLite, NOT Turso

The Analysis Service writes to a **local SQLite database** at `{CACHE_DIR}/jobs.db` (default `/cache/jobs.db`). This is defined in:

- `services/analysis/src/sow_analysis/storage/db.py` -- the `JobStore` class uses `aiosqlite`
- `services/analysis/src/sow_analysis/workers/queue.py` line 116 -- `db_path = db_path if db_path is not None else cache_dir / "jobs.db"`

There are **zero references** to Turso, libsql, or any Turso connection strings anywhere in the `services/analysis/` directory. The Analysis Service has no knowledge of Turso whatsoever.

## How LRC Files Are Uploaded to R2

The R2 upload happens in `_process_lrc_job()` at queue.py line 852:

```python
lrc_url = await self.r2_client.upload_lrc(hash_prefix, lrc_path)
```

The `upload_lrc()` method in `services/analysis/src/sow_analysis/storage/r2.py` (line 135) uploads the local LRC file to the key `{hash_prefix}/lyrics.lrc` and returns the S3 URL `s3://{bucket}/{hash_prefix}/lyrics.lrc`.

After R2 upload, two things happen:

1. **Local disk cache** is updated (queue.py line 856): `cache_manager.save_lrc_result(lrc_cache_key, {"lrc_url": lrc_url, "line_count": line_count})`
2. **Local SQLite job store** is updated (queue.py lines 869-878): `job_store.update_job(job.id, status="completed", progress=1.0, stage="complete", result_json=...)`

## Complete Flow: LRC Job Submission to Database Update

### Phase A: Admin CLI submits job and records intent

1. Admin CLI (`_submit_lrc_single()` at `src/stream_of_worship/admin/commands/audio.py` line 341) calls `analysis_client.submit_lrc()` via HTTP POST to `/api/v1/jobs/lrc`
2. Admin CLI immediately updates its own local SQLite catalog: `db_client.update_recording_status(hash_prefix=..., lrc_status="processing", lrc_job_id=job_id)` (line 360-364)

### Phase B: Analysis Service processes the job

3. Analysis Service's `_process_lrc_job()` runs the LRC pipeline:
   - Downloads audio from R2
   - Optionally downloads/generates vocals stem
   - Runs Whisper transcription + LLM alignment (or YouTube transcript path)
   - Optionally runs Qwen3 refinement
   - Writes LRC file locally
4. Uploads LRC to R2 via `r2_client.upload_lrc()` -- stored at `{hash_prefix}/lyrics.lrc`
5. Saves LRC result to local disk cache
6. Updates the Analysis Service's own `jobs.db`: `status=completed`, `result_json` contains `{lrc_url, line_count}`

### Phase C: Admin CLI retrieves results and updates catalog

This happens through **two mechanisms**:

**Mechanism 1: Synchronous wait (single job with `--wait`)**
- `_submit_lrc_single()` calls `analysis_client.wait_for_completion()` (audio.py line 387) which polls `GET /api/v1/jobs/{job_id}` every 30s
- When the job returns `status=completed`, the Admin CLI calls `db_client.update_recording_lrc(hash_prefix, r2_lrc_url=job.result.lrc_url)` (audio.py lines 411-415)
- This writes to the Admin CLI's local SQLite catalog: sets `r2_lrc_url`, `lrc_status='completed'`, and auto-publishes via `visibility_status = COALESCE(visibility_status, 'published')` (client.py lines 893-922)

**Mechanism 2: Async sync via `sow-admin audio status --sync`**
- The `status` command (audio.py line 1920) iterates over recordings with `lrc_status IN ('pending', 'processing')`
- For each, queries the Analysis Service API for the job status
- If completed with an `lrc_url`, calls `db_client.update_recording_lrc()` (audio.py lines 1996-2004)
- If failed, calls `db_client.update_recording_status(lrc_status="failed")` (audio.py lines 2006-2010)

### Phase D: Turso sync (separate manual step)

7. The Admin CLI's `DatabaseClient` can optionally use `libsql` embedded replicas to sync with Turso cloud
8. Triggered by `sow-admin db sync` command
9. Calls `conn.sync()` on the libsql connection, which pushes local writes to Turso cloud
10. This is a **manual operation**

## Data Flow Diagram

```
Analysis Service (local SQLite jobs.db + R2)
       |
       | HTTP API (GET /api/v1/jobs/{id})
       v
Admin CLI (local SQLite catalog.db)
       |
       | libsql embedded replica sync
       v
Turso Cloud Database
```

## Turso Write Operations in the Analysis Service

**There are none.** The Analysis Service never writes to Turso. The path to Turso goes through the Admin CLI.

## Storage Layer Summary (`services/analysis/src/sow_analysis/storage/`)

- **`db.py`** (`JobStore`): Local SQLite via `aiosqlite` for job state persistence. The `jobs` table tracks id, type, status, progress, stage, error_message, request_json, result_json, content_hash, timestamps.

- **`r2.py`** (`R2Client`): S3-compatible storage client for R2. `upload_lrc()` (line 135) stores LRC files at `{hash_prefix}/lyrics.lrc`. Also handles audio downloads, stem uploads, and analysis result uploads.

- **`cache.py`** (`CacheManager`): Local disk cache for deduplication. Stores LRC results as `{hash_prefix}_lrc.json` containing `{lrc_url, line_count}`. Also caches Whisper transcriptions. Cache key for LRC results is a composite hash of `content_hash + lyrics_hash`.
