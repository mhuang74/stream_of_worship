# Consolidate Qwen3 ForcedAligner into Analysis Service - Implementation Summary

**Date:** 2026-06-16  
**Status:** Complete  
**Spec:** `specs/consolidate-qwen3-into-analysis-service-v3.md`  
**Branch:** `trigger_alignment_via_admin`  
**Commit:** `7f67d64`

---

## Overview

Merged the Qwen3 ForcedAligner from a separate `services/qwen3/` Docker microservice into the Analysis Service (`services/analysis/`), then deleted `services/qwen3/` entirely. This reduces operational complexity to a single Docker image while preserving forced alignment capability via a new `FORCED_ALIGNMENT` job type and `audio align-lrc` CLI command.

**Key architectural change:** The `Qwen3ForcedAligner` model now runs in-process within the analysis service (lazy-loaded on first job), replacing the previous HTTP-call-to-separate-container pattern. Concurrency is controlled by acquiring `_local_model_semaphore` only around the `align()` call, not the entire job, preventing deadlock with auto-triggered stem separation child jobs.

---

## Phase 1: Analysis Service — Forced Alignment Worker

### 1a. Config Variables

**File:** `services/analysis/src/sow_analysis/config.py`

- Removed: `SOW_QWEN3_BASE_URL`, `SOW_QWEN3_API_KEY`
- Added: `SOW_FORCED_ALIGNER_MODEL_PATH` (default: `"Qwen/Qwen3-ForcedAligner-0.6B"`), `SOW_FORCED_ALIGNER_DEVICE` (default: `"auto"`)

### 1b. Job Type and Models

**File:** `services/analysis/src/sow_analysis/models.py`

- Added `FORCED_ALIGNMENT` to `JobType` enum
- Added `ForcedAlignmentOptions` model: `language` (Literal["zh", "en"]), `force`, `use_vocals_stem`
- Added `ForcedAlignmentJobRequest` model: `audio_url`, `content_hash`, `lyrics_text`, `song_title`, `options`
- Updated `Job.request` union to include `ForcedAlignmentJobRequest`
- Removed deprecated `use_qwen3` and `max_qwen3_duration` from `LrcOptions`
- Updated `lrc_source` comment to include `"forced_alignment"` as valid value

### 1c. ForcedAlignerWrapper (In-Process)

**New file:** `services/analysis/src/sow_analysis/workers/forced_aligner.py`

Key design:
- **No internal semaphore** — concurrency controlled externally by `JobQueue`
- **Double-check locking** via `asyncio.Lock` for lazy init (prevents race condition on first-use)
- **Raises `RuntimeError`** on init failure (matches `AudioSeparatorWrapper._ensure_ready()` pattern)
- **GPU memory cleanup** — `torch.cuda.empty_cache()` in `cleanup()`
- Lazy initialization: model loaded in thread pool via `run_in_executor` on first `align()` call
- `dtype` hardcoded to `float32`

### 1d. Forced Alignment Utility Functions

**New file:** `services/analysis/src/sow_analysis/workers/forced_alignment.py`

- `normalize_text(text)` — CJK punctuation/whitespace normalization
- `format_timestamp(seconds)` — Format as `[mm:ss.xx]`
- `map_segments_to_lines(segments, original_lines)` — Character-level to line-level mapping
- `validate_audio_duration(audio_path, max_seconds=300.0)` — Hybrid `soundfile.info()` (O(1) for WAV/FLAC) with `librosa.get_duration()` fallback

### 1e. Refactored Transcription Audio Resolution

**File:** `services/analysis/src/sow_analysis/workers/queue.py`

- Refactored `_resolve_lrc_transcription_audio()` → `_resolve_transcription_audio()` (generic, shared by LRC and forced alignment)
- Kept `_resolve_lrc_transcription_audio()` as thin wrapper for backward compatibility

### 1f. Forced Alignment Job Processing

**File:** `services/analysis/src/sow_analysis/workers/queue.py`

- Added `_forced_aligner_wrapper` class attribute with `set_forced_aligner_wrapper()` setter
- Added `_process_forced_alignment_job()` method with flow:
  1. Download audio from R2
  2. Resolve transcription audio (prefers `vocals_dry` FLAC, auto-triggers stem separation if needed)
  3. Validate duration ≤ 300s via hybrid soundfile/librosa
  4. Lazy-init `ForcedAlignerWrapper` via `_ensure_ready()`
  5. Acquire `_local_model_semaphore` ONLY around `align()` call (prevents deadlock)
  6. Map segments to lines, format as LRC
  7. Service-level copy-before-overwrite for existing LRC files
  8. Upload LRC to R2
  9. Set `job.result = JobResult(lrc_url=..., line_count=..., lrc_source="forced_alignment")`
- Wired into dispatcher WITHOUT full-job semaphore
- Added cleanup in `stop()`
- Updated `_log_queue_state()` with `FORCED_ALIGNMENT` stats
- Updated `submit()` type signature

### 1g. Service Startup Wiring

**File:** `services/analysis/src/sow_analysis/main.py`

- Create `ForcedAlignerWrapper` at startup (NOT initialized — lazy on first job)
- Set on `JobQueue` via `set_forced_aligner_wrapper()`
- Cleanup on shutdown
- Updated startup config logging (replaced Qwen3 URL with model path/device)

### 1h. API Route

**File:** `services/analysis/src/sow_analysis/routes/jobs.py`

- Added `POST /api/v1/jobs/forced-alignment` endpoint
- Removed legacy option rejection block for `use_qwen3`/`max_qwen3_duration`

### 1i. Dependencies

**File:** `services/analysis/pyproject.toml`

- Added `qwen-asr>=0.0.6,<0.1.0`
- Added `soundfile>=0.12.0`

### 1j. R2 Client

**File:** `services/analysis/src/sow_analysis/storage/r2.py`

- Added `copy_object()` method for service-level LRC backup before overwrite

---

## Phase 2: Database Schema Migration

**File:** `services/analysis/src/sow_analysis/storage/db.py`

- Added `_migrate_forced_alignment_type()` migration (same pattern as `_migrate_embedding_type()`)
- Updated `_row_to_job()` to handle `JobType.FORCED_ALIGNMENT`
- Updated CREATE TABLE CHECK constraint to include `'forced_alignment'`

---

## Phase 3: Admin CLI — `audio align-lrc` Command

### 3a. AnalysisClient Method

**File:** `src/stream_of_worship/admin/services/analysis.py`

- Added `submit_forced_alignment()` method — POST to `/api/v1/jobs/forced-alignment`

### 3b. CLI Command

**File:** `src/stream_of_worship/admin/commands/audio.py`

- Added `align-lrc` command with options: `--lang`, `--force`, `--use-vocals-stem/--no-vocals-stem`, `--stdin`, `--wait`
- Helper `_submit_forced_alignment_single()`: DB lookup → validate → guard (reject if `lrc_status=="processing"`) → submit → update DB
- Helper `_submit_forced_alignment_batch()` for `--stdin` mode
- Removed deprecated `--no-qwen3` flag from `lrc` command

---

## Phase 4: Tests

### 4a. Migrated Tests

**New file:** `services/analysis/tests/test_map_segments_to_lines.py` — 34 tests, all passing

Migrated from `services/qwen3/tests/test_map_segments_to_lines.py` with updated imports.

### 4b. New Tests

**New file:** `services/analysis/tests/test_forced_alignment.py` — 19 tests, all passing

Coverage:
- `ForcedAlignmentJobRequest` model validation (defaults, custom options, invalid language)
- `format_timestamp` (zero, minutes+seconds, large minutes)
- `validate_audio_duration` (under limit, over limit, librosa fallback)
- `_process_forced_alignment_job` (success, language mapping, invalid request, no wrapper, alignment failure, service-level backup, deadlock prevention)
- `ForcedAlignerWrapper` (init failure raises RuntimeError, skip if ready, cleanup)

### 4c. Deleted Legacy Tests

- `services/analysis/tests/test_qwen3_fallback.py`
- `services/analysis/tests/test_qwen3_regression.py`
- `services/analysis/tests/test_lrc_benchmark.py`
- `services/analysis/tests/test_lrc_integration_qwen3.py`

---

## Phase 5: Cleanup

### Deleted Files

| File | Reason |
|------|--------|
| `services/qwen3/` (entire directory) | Merged into analysis service |
| `services/analysis/src/sow_analysis/services/qwen3_client.py` | HTTP client to separate container; no longer needed |
| `docker/docker-compose.prod.yml` | Standalone qwen3 prod config |

### Updated Files

| File | Changes |
|------|---------|
| `services/analysis/docker-compose.yml` | Removed qwen3/qwen3-dev services, qwen3-cache volume, SOW_QWEN3_* env vars; added SOW_FORCED_ALIGNER_* env vars and model volume mount |
| `services/analysis/.env.example` | Replaced SOW_QWEN3_* with SOW_FORCED_ALIGNER_* |
| `services/analysis/scripts/deploy.sh` | Updated model paths, removed qwen3 Docker service refs |
| `services/analysis/src/sow_analysis/services/__init__.py` | Removed Qwen3Client exports |
| `services/analysis/src/sow_analysis/services/mvsep_client.py` | Removed qwen3_client.py reference in comment |
| `DEVELOPER.md` | Removed qwen3 service references |
| `docs/lrc-job-flow.md` | Updated to reflect in-process forced alignment |
| `services/analysis/README.md` | Updated architecture docs |
| `services/analysis/DEVELOPER.md` | Updated development docs |
| `services/analysis/DEPLOYMENT.md` | Updated deployment docs |

---

## Test Results

```
services/analysis/tests/test_map_segments_to_lines.py: 34 passed
services/analysis/tests/test_forced_alignment.py: 19 passed
Total analysis service tests: 164 passed, 4 failed (pre-existing, unrelated)
```

The 4 pre-existing failures are in `test_youtube_transcript.py` (missing `SOW_YOUTUBE_PROXY` attribute on `MockSettings`) — unrelated to this change.

---

## Key Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Model loading | Lazy (on first forced-alignment job) | Matches `AudioSeparatorWrapper` pattern; avoids ~1.2GB RAM overhead at startup |
| Duration validation | Hybrid: `soundfile.info()` first, `librosa.get_duration()` fallback | O(1) for WAV/FLAC via soundfile; librosa fallback for MP3 and other formats |
| Semaphore strategy | Acquire ONLY around `align()` call | Prevents deadlock with nested stem-separation jobs |
| Init failure mode | Raise `RuntimeError` | Matches `AudioSeparatorWrapper._ensure_ready()` pattern; fails loudly |
| Language values | `zh`/`en` at API level | Mapped to `Chinese`/`English` only at `Qwen3ForcedAligner.align()` call site |
| LRC overwrite safety | Service-level copy-before-overwrite | Protects all callers (API + CLI); defense in depth |
| Dependency pin | `qwen-asr>=0.0.6,<0.1.0` | Pin upper bound to avoid breaking changes |

---

## Remaining Verification (Requires Running Services)

- [ ] `docker compose up analysis` works without qwen3 service
- [ ] `sow-admin audio align-lrc <song_id> --wait` works end-to-end
- [ ] `sow-admin audio lrc <song_id>` still works unchanged
