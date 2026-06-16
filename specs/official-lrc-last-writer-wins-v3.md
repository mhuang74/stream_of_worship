# Official LRC Last-Writer-Wins (v3)

## Summary

- Make `{hash_prefix}/lyrics.lrc` the single official LRC object.
- Both regular LRC jobs and ForcedAlignment jobs overwrite this same official object.
- Before overwriting, copy the current official object to `{hash_prefix}/lyrics.backup.{unix_timestamp_ms}.lrc`.
- Backup failure is **fatal** (fails the job). A `skip_backup` force flag allows override for emergencies.
- All writers (analysis service jobs, cache hits, and admin CLI) enforce ETag-based stale-object protection: if `lyrics.lrc` was modified after the operation started, the write fails.
- Stop creating new `lyrics.{lang}.forced.lrc` and `lyrics.{lang}.v2.lrc` artifacts.
- Leave historical `.forced.lrc` and `.v2.lrc` objects in R2 untouched.
- The analysis service runs as a **single instance**; no distributed locking is required.
- Prune old backups to keep at most 5 per `hash_prefix`.

## Key Changes

### 1. Shared Official-LRC Upload Helper

Add a shared helper in the analysis service for all official LRC writes:

```python
MAX_BACKUPS_PER_PREFIX = 5

async def upload_official_lrc(
    r2_client,
    hash_prefix: str,
    lrc_path: Path,
    expected_etag: Optional[str] = None,
    skip_backup: bool = False,
) -> str:
    """
    1. HEAD lyrics.lrc to get current ETag.
    2. If expected_etag is provided and current ETag != expected_etag:
         raise StaleObjectError("lyrics.lrc was modified after job started")
    3. If lyrics.lrc exists and not skip_backup:
         copy to lyrics.backup.{timestamp_ms}.lrc
         If copy fails: raise BackupFailedError
    4. Upload new lyrics.lrc
    5. Prune old backups: list lyrics.backup.*.lrc, delete oldest if count > MAX_BACKUPS_PER_PREFIX
    6. Return s3://{bucket}/{hash_prefix}/lyrics.lrc
    """
```

Use this helper from:
- `_process_lrc_job()` (regular LRC generation)
- `_process_forced_alignment_job()`
- Cache-hit rewrite path

### 2. ETag-Based Stale-Object Protection

In `_process_lrc_job()` and `_process_forced_alignment_job()`:

- **At job start** (after downloading audio, before alignment/transcription): HEAD `lyrics.lrc` and record its ETag (or `None` if absent).
- **At upload time**: Pass the recorded ETag to `upload_official_lrc()`.
- If the ETag has changed since job start, fail the job with `error_message="lyrics.lrc was modified by another process after this job started"`.

This prevents:
- A ForcedAlignment job from overwriting a manual admin edit made while the job was running.
- A regular LRC job from overwriting a concurrent ForcedAlignment result (or vice versa) within the single instance.

### 3. Cache-Hit ETag Protection

Cache hits also enforce ETag protection:

- **At job start** (before returning the cached result): HEAD `lyrics.lrc` and record its ETag (or `None` if absent).
- **At upload time**: Pass the recorded ETag to `upload_official_lrc()`.
- If the ETag has changed since job start, fail the job with `error_message="lyrics.lrc was modified by another process after this job started"`.

This prevents a cache hit from silently clobbering a manual admin edit.

### 4. Backup Behavior

- **Backup key format**: `{hash_prefix}/lyrics.backup.{unix_timestamp_ms}.lrc` (millisecond precision to avoid collision).
- **Backup scope**: Always the current official `lyrics.lrc` at the moment of upload.
- **Failure handling**: If `copy_object` raises any exception and `skip_backup=False`, fail the job. Do not proceed with upload.
- **Pruning**: After a successful backup, list all `lyrics.backup.*.lrc` objects for the `hash_prefix`. If count exceeds `MAX_BACKUPS_PER_PREFIX` (5), delete the oldest backups (sorted by timestamp in key name) until only 5 remain.
- **Retention**: Beyond the soft limit of 5, no additional cleanup policy. A future lifecycle policy may further reduce retention.

### 5. Job Result URLs

- **ForcedAlignment result**:
  - `JobResult.lrc_url = s3://{bucket}/{hash_prefix}/lyrics.lrc`
  - `JobResult.lrc_source = "forced_alignment"`
- **Regular LRC result**:
  - `JobResult.lrc_url = s3://{bucket}/{hash_prefix}/lyrics.lrc`
  - `JobResult.lrc_source` preserved as before (`youtube_transcript`, `qwen3_asr`, or `whisper_asr`)

### 6. Cache Behavior

- Store generated LRC **text** in the LRC cache going forward (in addition to metadata).
- On cache hit with cached text:
  1. Write cached text to a temp file.
  2. Call `upload_official_lrc()` with the temp file and the ETag captured at job start.
  3. Complete the job.
- On cache hit with **metadata-only** legacy entry (no cached text): ignore and regenerate. This will cause a one-time burst of reprocessing for existing cached songs after deployment.
- Cache-hit uploads use the same backup and ETag logic as fresh uploads.

### 7. Admin CLI Backup

The admin CLI's `upload-lrc` command and editor upload must also create backups before overwriting `lyrics.lrc`:

- Add `upload_official_lrc()` (or equivalent synchronous helper) to the admin R2 client.
- The admin editor already has ETag-based stale-session protection (`check_transcribed_changed`). This continues to work.
- The `upload-lrc` command currently has no ETag check. Add one: capture ETag before upload, pass to the helper. This prevents `upload-lrc` from silently overwriting a concurrent edit.

## Consumers

| Consumer | Change Required |
|----------|-----------------|
| **Admin CLI** | `upload-lrc` and editor upload now use `upload_official_lrc()` with backup + ETag check. `recordings.r2_lrc_url` continues to be stored; now always `.../lyrics.lrc`. |
| **Web Lyrics Pullup Screen** | No changes. Already reads `{hash_prefix}/lyrics.lrc`. |
| **Lyrics Renderer** | No changes. Already fetches `{hash_prefix}/lyrics.lrc`. |
| **Render Worker** | No changes. Constructs LRC key from `hashPrefix` as `{hashPrefix}/lyrics.lrc`. |
| **Web user override APIs** | Out of scope. `user_lrc_override` table remains separate. |

## Test Plan

### New Tests

1. **ForcedAlignment uploads only `lyrics.lrc`** — does not create `.forced.lrc`.
2. **ForcedAlignment backs up existing `lyrics.lrc`** before overwrite.
3. **Backup failure fails the job** — `job.status == FAILED`, `lyrics.lrc` unchanged.
4. **Skip-backup force flag** — when `skip_backup=True`, upload proceeds even if backup fails.
5. **Stale-object detection (fresh job)** — job started when ETag=X, `lyrics.lrc` modified to ETag=Y before upload → job fails.
6. **Stale-object detection (cache hit)** — cache hit started when ETag=X, `lyrics.lrc` modified to ETag=Y before upload → job fails.
7. **Regular LRC uploads only `lyrics.lrc`** — does not create `.v2.lrc`.
8. **Regular LRC cache hit with cached text** rewrites official `lyrics.lrc` with ETag check.
9. **Metadata-only legacy LRC cache entry** is ignored and regenerated.
10. **Millisecond timestamp** in backup key prevents collision.
11. **Backup pruning** — after 6th backup, oldest is deleted; only 5 remain.
12. **Admin `upload-lrc` creates backup** before overwriting `lyrics.lrc`.
13. **Admin `upload-lrc` ETag check** — fails if `lyrics.lrc` was modified since ETag capture.

### Existing Tests to Update

- `tests/services/analysis/test_forced_alignment.py`:
  - Update `upload_lrc` mock assertions to expect `lyrics.lrc` instead of `lyrics.zh.forced.lrc`.
  - Update `copy_object` assertions to expect backup of `lyrics.lrc`.
  - Add test for stale-object failure.
- `tests/services/analysis/test_queue.py`:
  - Update LRC job assertions to expect `lyrics.lrc` instead of `lyrics.{lang}.v2.lrc`.
  - Add test for cache-hit rewrite path with ETag check.
- `tests/services/analysis/test_r2.py`:
  - Add tests for `upload_official_lrc` helper (backup, ETag check, pruning).

### Regression Tests

- Existing admin, web signed-url, and render-worker tests continue asserting `{hash_prefix}/lyrics.lrc`.

## Assumptions

- The analysis service runs as a **single instance**. ETag-based stale-object detection is sufficient; no distributed locking is required.
- Official LRC means the shared R2 object `{hash_prefix}/lyrics.lrc`.
- Last completed LRC-producing job wins, **unless** the official object was modified after the job started.
- Backups are always copies of the previous official `lyrics.lrc`.
- `recordings.r2_lrc_url` in the database is not consumed by any runtime code path. All runtime components construct the LRC key from `hashPrefix`. The field is informational only (used by admin CLI for display/deletion). No DB coordination is needed when the analysis service writes `lyrics.lrc`.
- Web official-LRC editing authorization is out of scope.
- Historical `.forced.lrc` and `.v2.lrc` objects are left in R2 indefinitely.

## Known Issues

- **App-side AssetCache staleness**: The user app's `AssetCache` (`src/stream_of_worship/app/services/asset_cache.py`) has no TTL or ETag checking. After `lyrics.lrc` is updated in R2, the app will continue serving the old cached version indefinitely. Addressing this is out of scope for this change.
- **HEAD → COPY → PUT race window**: Between the ETag check and the actual upload, an admin CLI upload could complete, making the ETag check stale. The window is small (milliseconds between HEAD and PUT), and the single-instance assumption makes this unlikely for analysis-service jobs. Admin CLI is a separate process with no coordination, but the risk is accepted given the narrow window.

## Out of Scope

- Distributed locking (not needed for single instance).
- Backup retention / cleanup policy beyond the soft limit of 5.
- Migration to delete historical `.forced.lrc` and `.v2.lrc` objects.
- Web user override API changes.
- Quality gating (e.g., preventing a worse ForcedAlignment result from overwriting a better Whisper result).
- App-side AssetCache TTL/ETag invalidation.
