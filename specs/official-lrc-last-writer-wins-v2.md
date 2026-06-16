# Official LRC Last-Writer-Wins (Revised)

## Summary

- Make `{hash_prefix}/lyrics.lrc` the single official LRC object.
- Both regular LRC jobs and ForcedAlignment jobs overwrite this same official object.
- Before overwriting, copy the current official object to `{hash_prefix}/lyrics.backup.{unix_timestamp_ms}.lrc`.
- Backup failure is **fatal** (fails the job). A `skip_backup` force flag allows override for emergencies.
- Automated jobs must **not** silently overwrite a `lyrics.lrc` that was modified after the job started (e.g., by a manual admin edit). Jobs compare the ETag of `lyrics.lrc` at job-start vs. pre-upload; mismatch fails the job.
- Stop creating new `lyrics.{lang}.forced.lrc` and `lyrics.{lang}.v2.lrc` artifacts.
- Leave historical `.forced.lrc` and `.v2.lrc` objects in R2 untouched.
- The analysis service runs as a **single instance**; no distributed locking is required.

## Key Changes

### 1. Shared Official-LRC Upload Helper

Add a shared helper in the analysis service for all official LRC writes:

```python
async def upload_official_lrc(
    r2_client,
    hash_prefix: str,
    lrc_path: Path,
    expected_etag: Optional[str] = None,  # ETag captured at job start
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
    5. Return s3://{bucket}/{hash_prefix}/lyrics.lrc
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

### 3. Backup Behavior

- **Backup key format**: `{hash_prefix}/lyrics.backup.{unix_timestamp_ms}.lrc` (millisecond precision to avoid collision).
- **Backup scope**: Always the current official `lyrics.lrc` at the moment of upload.
- **Failure handling**: If `copy_object` raises any exception and `skip_backup=False`, fail the job. Do not proceed with upload.
- **Retention**: Out of scope for this change. Backups accumulate until a future cleanup policy is defined.

### 4. Job Result URLs

- **ForcedAlignment result**:
  - `JobResult.lrc_url = s3://{bucket}/{hash_prefix}/lyrics.lrc`
  - `JobResult.lrc_source = "forced_alignment"`
- **Regular LRC result**:
  - `JobResult.lrc_url = s3://{bucket}/{hash_prefix}/lyrics.lrc`
  - `JobResult.lrc_source` preserved as before (`youtube_transcript`, `qwen3_asr`, or `whisper_asr`)

### 5. Cache Behavior

- Store generated LRC **text** in the LRC cache going forward (in addition to metadata).
- On cache hit with cached text:
  1. Write cached text to a temp file.
  2. Call `upload_official_lrc()` with the temp file (no ETag check needed for cache hits, or pass `expected_etag=None`).
  3. Complete the job.
- On cache hit with **metadata-only** legacy entry (no cached text): ignore and regenerate.
- Cache-hit uploads use the same backup logic as fresh uploads.

## Consumers

| Consumer | Change Required |
|----------|-----------------|
| **Admin CLI** | No code changes. Continues storing `final_job.result.lrc_url` in `recordings.r2_lrc_url`; now always `.../lyrics.lrc`. |
| **Web Lyrics Pullup Screen** | No changes. Already reads `{hash_prefix}/lyrics.lrc`. |
| **Lyrics Renderer** | No changes. Already fetches `{hash_prefix}/lyrics.lrc`. |
| **Web user override APIs** | Out of scope. `user_lrc_override` table remains separate. |

## Test Plan

### New Tests

1. **ForcedAlignment uploads only `lyrics.lrc`** — does not create `.forced.lrc`.
2. **ForcedAlignment backs up existing `lyrics.lrc`** before overwrite.
3. **Backup failure fails the job** — `job.status == FAILED`, `lyrics.lrc` unchanged.
4. **Skip-backup force flag** — when `skip_backup=True`, upload proceeds even if backup fails.
5. **Stale-object detection** — job started when ETag=X, `lyrics.lrc` modified to ETag=Y before upload → job fails.
6. **Regular LRC uploads only `lyrics.lrc`** — does not create `.v2.lrc`.
7. **Regular LRC cache hit with cached text** rewrites official `lyrics.lrc`.
8. **Metadata-only legacy LRC cache entry** is ignored and regenerated.
9. **Millisecond timestamp** in backup key prevents collision.

### Existing Tests to Update

- `tests/services/analysis/test_forced_alignment.py`:
  - Update `upload_lrc` mock assertions to expect `lyrics.lrc` instead of `lyrics.zh.forced.lrc`.
  - Update `copy_object` assertions to expect backup of `lyrics.lrc`.
  - Add test for stale-object failure.
- `tests/services/analysis/test_queue.py`:
  - Update LRC job assertions to expect `lyrics.lrc` instead of `lyrics.{lang}.v2.lrc`.
  - Add test for cache-hit rewrite path.
- `tests/services/analysis/test_r2.py`:
  - Add tests for `upload_official_lrc` helper (if extracted to R2 client) or queue-level tests.

### Regression Tests

- Existing admin, web signed-url, and render-worker tests continue asserting `{hash_prefix}/lyrics.lrc`.

## Assumptions

- The analysis service runs as a **single instance**. ETag-based stale-object detection is sufficient; no distributed locking is required.
- Official LRC means the shared R2 object `{hash_prefix}/lyrics.lrc`.
- Last completed LRC-producing job wins, **unless** the official object was modified after the job started.
- Backups are always copies of the previous official `lyrics.lrc`.
- Web official-LRC editing authorization is out of scope.
- Historical `.forced.lrc` and `.v2.lrc` objects are left in R2 indefinitely.

## Out of Scope

- Distributed locking (not needed for single instance).
- Backup retention / cleanup policy.
- Migration to delete historical `.forced.lrc` and `.v2.lrc` objects.
- Web user override API changes.
- Quality gating (e.g., preventing a worse ForcedAlignment result from overwriting a better Whisper result).
