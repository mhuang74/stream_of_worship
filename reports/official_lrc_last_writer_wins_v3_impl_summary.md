# Official LRC Last-Writer-Wins (v3) — Implementation Summary

**Date:** 2026-06-16
**Spec:** `specs/official-lrc-last-writer-wins-v3.md`
**Commit:** `24069ee`
**Branch:** `trigger_alignment_via_admin` (PR #105)

---

## Overview

This change makes `{hash_prefix}/lyrics.lrc` the single official LRC object in R2. Both regular LRC generation jobs and ForcedAlignment jobs now overwrite this same object. Before overwriting, the current official object is copied to a timestamped backup. All writers enforce ETag-based stale-object protection to prevent concurrent overwrites. Historical `.forced.lrc` and `.v2.lrc` objects are left untouched in R2.

---

## Key Changes

### 1. Shared `upload_official_lrc()` Helper

Added to both the **analysis service** (async) and **admin CLI** (sync) R2 clients:

```python
async def upload_official_lrc(
    r2_client,
    hash_prefix: str,
    lrc_path: Path,
    expected_etag: Optional[str] = None,
    skip_backup: bool = False,
) -> str:
```

**Behavior:**
1. HEAD `lyrics.lrc` to get current ETag
2. If `expected_etag` is provided and current ETag ≠ expected_etag → raise `StaleObjectError`
3. If `lyrics.lrc` exists and not `skip_backup` → copy to `lyrics.backup.{timestamp_ms}.lrc`
   - If copy fails → raise `BackupFailedError` (fatal)
4. Upload new `lyrics.lrc`
5. Prune old backups: list `lyrics.backup.*.lrc`, delete oldest if count > 5
6. Return `s3://{bucket}/{hash_prefix}/lyrics.lrc`

**Files:**
- `services/analysis/src/sow_analysis/storage/r2.py` — async version + `head_object`, `list_objects`, `delete_object`
- `src/stream_of_worship/admin/services/r2.py` — sync version

### 2. Analysis Service Job Processing

#### `_process_lrc_job()` (`workers/queue.py`)
- **At job start:** Captures ETag of `lyrics.lrc` via `head_object` (or `None` if absent)
- **Cache hit with `lrc_text`:** Writes cached text to temp file, calls `upload_official_lrc()` with captured ETag
- **Cache hit metadata-only (legacy):** Ignored, falls through to regeneration
- **Fresh generation:** Uploads only `lyrics.lrc` via `upload_official_lrc()` — no more `.v2.lrc` or legacy alias
- **Cache storage:** Now stores `lrc_text` in cache entries for rewrite path
- **Error handling:** `StaleObjectError` → job fails with stage `stale_object`; `BackupFailedError` → stage `backup_failed`

#### `_process_forced_alignment_job()` (`workers/queue.py`)
- **At job start:** Captures ETag of `lyrics.lrc` via `head_object`
- **Upload:** Calls `upload_official_lrc()` with captured ETag — no more `.forced.lrc` artifact
- **Result:** `JobResult.lrc_url = s3://{bucket}/{hash_prefix}/lyrics.lrc`, `lrc_source = "forced_alignment"`
- **Removed:** Old inline backup logic (now handled by `upload_official_lrc`)

### 3. Admin CLI

#### `upload-lrc` command (`admin/commands/audio.py`)
- Captures ETag of existing `lyrics.lrc` before upload via `get_lrc_identity()`
- Calls `upload_official_lrc()` with `expected_etag`
- Handles `StaleObjectError` and `BackupFailedError` with user-friendly error messages

#### Editor upload (`admin/editor/upload.py`)
- `upload_revised_lrc()` now calls `upload_official_lrc()` instead of `upload_lrc()`
- Editor's existing stale-session detection (`check_transcribed_changed`) continues to work as a first line of defense
- Backup of previous official LRC is now handled by `upload_official_lrc()`

### 4. Stopped Creating Legacy Artifacts

| Artifact | Before | After |
|----------|--------|-------|
| Regular LRC | `lyrics.{lang}.v2.lrc` + `lyrics.lrc` (legacy alias) | `lyrics.lrc` only |
| ForcedAlignment | `lyrics.{lang}.forced.lrc` | `lyrics.lrc` only |

Historical `.forced.lrc` and `.v2.lrc` objects in R2 are left untouched.

---

## Test Coverage

### New Tests

| Test | File | Description |
|------|------|-------------|
| `test_upload_official_lrc_new_object` | `tests/services/analysis/test_r2.py` | Uploads to `lyrics.lrc` without backup when object doesn't exist |
| `test_upload_official_lrc_creates_backup` | `tests/services/analysis/test_r2.py` | Copies existing object to backup before overwrite |
| `test_upload_official_lrc_stale_etag_raises` | `tests/services/analysis/test_r2.py` | `StaleObjectError` when ETag mismatches |
| `test_upload_official_lrc_backup_failure_raises` | `tests/services/analysis/test_r2.py` | `BackupFailedError` when `copy_object` fails |
| `test_upload_official_lrc_skip_backup` | `tests/services/analysis/test_r2.py` | Upload proceeds when `skip_backup=True` |
| `test_upload_official_lrc_prunes_old_backups` | `tests/services/analysis/test_r2.py` | Deletes oldest backup when count > 5 |
| `test_uploads_new_object_without_backup` | `tests/admin/test_r2.py` | Sync version: upload without backup |
| `test_creates_backup_before_overwrite` | `tests/admin/test_r2.py` | Sync version: backup before overwrite |
| `test_stale_etag_raises` | `tests/admin/test_r2.py` | Sync version: `StaleObjectError` |
| `test_backup_failure_raises` | `tests/admin/test_r2.py` | Sync version: `BackupFailedError` |
| `test_skip_backup_ignores_copy_failure` | `tests/admin/test_r2.py` | Sync version: `skip_backup=True` |
| `test_prunes_old_backups` | `tests/admin/test_r2.py` | Sync version: pruning |
| `test_lrc_job_uploads_official_lrc` | `tests/services/analysis/test_queue.py` | Fresh LRC job uploads only `lyrics.lrc` |
| `test_lrc_job_cache_hit_with_text_rewrites_official` | `tests/services/analysis/test_queue.py` | Cache hit with text rewrites official object |
| `test_lrc_job_cache_hit_metadata_only_ignored` | `tests/services/analysis/test_queue.py` | Metadata-only cache entry ignored |
| `test_lrc_job_stale_object_fails` | `tests/services/analysis/test_queue.py` | Stale ETag fails LRC job |
| `test_stale_object_fails_job` | `services/analysis/tests/test_forced_alignment.py` | Stale ETag fails FA job |
| `test_backup_failure_fails_job` | `services/analysis/tests/test_forced_alignment.py` | Backup failure fails FA job |

### Updated Tests

| Test | File | Change |
|------|------|--------|
| `test_process_forced_alignment_job_success` | `services/analysis/tests/test_forced_alignment.py` | Asserts `lrc_url == lyrics.lrc`, mocks `upload_official_lrc` |
| `test_service_level_backup` | `services/analysis/tests/test_forced_alignment.py` | Tests ETag capture via `head_object` |
| `test_deadlock_prevention` | `services/analysis/tests/test_forced_alignment.py` | Mocks `upload_official_lrc` and `head_object` |
| `test_lrc_job_uses_cache` | `tests/services/analysis/test_lrc_worker.py` | Cache entry now includes `lrc_text` |
| `test_manual_editor_upload_does_not_force_review_visibility` | `tests/admin/services/test_lrc_editor.py` | Mock changed from `upload_lrc` to `upload_official_lrc` |

### Test Results

- **Analysis R2 tests:** 18 passed
- **Analysis queue tests:** 19 passed
- **LRC worker tests:** 48 passed
- **Forced alignment tests:** 26 passed
- **Admin R2 tests:** 36 passed
- **Admin editor tests:** 49 passed

**Total: 196 passed** (analysis-related tests)

---

## Consumer Impact

| Consumer | Change Required |
|----------|-----------------|
| **Admin CLI** | `upload-lrc` and editor upload now use `upload_official_lrc()` with backup + ETag check |
| **Web Lyrics Pullup Screen** | No changes. Already reads `{hash_prefix}/lyrics.lrc` |
| **Lyrics Renderer** | No changes. Already fetches `{hash_prefix}/lyrics.lrc` |
| **Render Worker** | No changes. Constructs LRC key from `hashPrefix` as `{hashPrefix}/lyrics.lrc` |
| **Web user override APIs** | Out of scope. `user_lrc_override` table remains separate |

---

## Assumptions & Known Issues

- **Single instance:** The analysis service runs as a single instance. ETag-based stale-object detection is sufficient; no distributed locking is required.
- **Race window:** Between HEAD ETag check and actual PUT, a narrow race window exists (milliseconds). Accepted given single-instance assumption.
- **App-side AssetCache staleness:** The user app's `AssetCache` has no TTL or ETag checking. After `lyrics.lrc` is updated in R2, the app will continue serving the old cached version indefinitely. Addressing this is out of scope.
- **One-time burst:** Metadata-only legacy cache entries will cause a one-time burst of reprocessing after deployment.

---

## Files Modified

```
services/analysis/src/sow_analysis/storage/r2.py
services/analysis/src/sow_analysis/workers/queue.py
src/stream_of_worship/admin/commands/audio.py
src/stream_of_worship/admin/editor/upload.py
src/stream_of_worship/admin/services/r2.py
services/analysis/tests/test_forced_alignment.py
tests/services/analysis/test_lrc_worker.py
tests/services/analysis/test_queue.py
tests/services/analysis/test_r2.py
tests/admin/services/test_lrc_editor.py
tests/admin/test_r2.py
```

---

## Migration Notes

- No database migration required. `recordings.r2_lrc_url` continues to be stored but is informational only.
- Historical `.forced.lrc` and `.v2.lrc` objects in R2 are left untouched.
- After deployment, songs with metadata-only LRC cache entries will be regenerated once.
