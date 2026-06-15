# Plan: Consolidate Qwen3 ForcedAligner into Analysis Service & Remove `services/qwen3/`

## Overview

Merge the Qwen3 ForcedAligner functionality from the separate `services/qwen3/` Docker microservice into the Analysis Service (`services/analysis/`), then delete `services/qwen3/` entirely. This reduces operational complexity to a single Docker image while preserving the forced alignment capability via a new `FORCED_ALIGNMENT` job type and `audio align-lrc` CLI command.

## Current State

- **`services/qwen3/`**: Standalone FastAPI service in its own Docker container. Exposes `POST /api/v1/align`. Uses `Qwen3ForcedAligner-0.6B` model (~1.2GB). Runs on port 8001.
- **`services/analysis/`**: Main job processing service. Has a vestigial `Qwen3Client` (HTTP client to qwen3 service) that is **never called** in production. The active Qwen3 integration is DashScope Qwen3 ASR (cloud API), which is unrelated.
- **Admin CLI**: Has `audio lrc` command with `--no-qwen3-asr` flags (for DashScope ASR, not ForcedAligner). No forced alignment command exists yet.

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Model loading | Lazy (on first forced-alignment job) | Matches `AudioSeparatorWrapper` pattern; avoids ~1.2GB RAM overhead at startup |
| Duration validation | Use `librosa.get_duration()` | Already a dependency; no need to add `pydub` |
| Legacy test files | Delete `test_qwen3_fallback.py`, `test_qwen3_regression.py`, `test_lrc_benchmark.py` | They test the old HTTP-client-to-qwen3-container path which no longer exists |
| POC scripts | Leave as-is | Out of scope; experimental code |
| R2 audio download | Reuse existing `R2Client.download_audio()` | Already async, already in analysis service |
| `map_segments_to_lines` | Migrate into `sow_analysis/workers/forced_alignment.py` | Core algorithm, well-tested, must be preserved |
| `Qwen3Client` (HTTP) | Delete | Was the HTTP client to the separate qwen3 container; no longer needed |
| `SOW_QWEN3_BASE_URL` / `SOW_QWEN3_API_KEY` | Remove from analysis config | Was for HTTP calls to qwen3 container; replaced by in-process model |
| New config vars | `SOW_FORCED_ALIGNER_MODEL_PATH`, `SOW_FORCED_ALIGNER_DEVICE`, `SOW_FORCED_ALIGNER_MAX_CONCURRENT` | New names to avoid confusion with DashScope Qwen3 ASR vars |

---

## Phase 1: Analysis Service — Add Forced Alignment Worker

### 1a. Add new config variables

**File**: `services/analysis/src/sow_analysis/config.py`

Add after the existing `SOW_QWEN3_*` vars:

```python
# Forced Aligner Configuration (Qwen3ForcedAligner-0.6B, runs in-process)
SOW_FORCED_ALIGNER_MODEL_PATH: str = "Qwen/Qwen3-ForcedAligner-0.6B"  # HF model ID or local path
SOW_FORCED_ALIGNER_DEVICE: str = "auto"  # auto/mps/cuda/cpu
SOW_FORCED_ALIGNER_MAX_CONCURRENT: int = 1  # Semaphore limit for concurrent alignments
```

Remove:
```python
SOW_QWEN3_BASE_URL: str = "http://qwen3:8000"
SOW_QWEN3_API_KEY: str = ""
```

### 1b. Add `FORCED_ALIGNMENT` job type and models

**File**: `services/analysis/src/sow_analysis/models.py`

```python
class JobType(str, Enum):
    ANALYZE = "analyze"
    LRC = "lrc"
    STEM_SEPARATION = "stem_separation"
    EMBEDDING = "embedding"
    FORCED_ALIGNMENT = "forced_alignment"  # NEW

class ForcedAlignmentOptions(BaseModel):
    model_config = ConfigDict(extra="allow")
    language: str = "Chinese"
    force: bool = False

class ForcedAlignmentJobRequest(BaseModel):
    audio_url: str
    content_hash: str
    lyrics_text: str
    song_title: str = ""
    options: ForcedAlignmentOptions = Field(default_factory=ForcedAlignmentOptions)
```

Update `Job.request` union to include `ForcedAlignmentJobRequest`.

Add `lrc_source` value `"forced_alignment"` to `JobResult.lrc_source` documentation.

### 1c. Create `ForcedAlignerWrapper` (in-process)

**New file**: `services/analysis/src/sow_analysis/workers/forced_aligner.py`

Migrate `Qwen3AlignerWrapper` from `services/qwen3/src/sow_qwen3/workers/aligner.py` with these changes:

- Rename class to `ForcedAlignerWrapper`
- Accept `model_path: str` (HF model ID or local path), `device: str`, `max_concurrent: int`
- Lazy initialization: `initialize()` called on first use, not at startup
- `align()` method: same signature, returns `list[tuple[float, float, str]]`
- `cleanup()` method: unload model
- `is_ready` property
- Internal: uses `asyncio.Semaphore` for concurrency, `run_in_executor` for model loading and inference

Key difference from old code: `dtype` is hardcoded to `float32` (same as before — the `SOW_QWEN3_DTYPE` config was never actually passed through to the wrapper).

### 1d. Create forced alignment utility functions

**New file**: `services/analysis/src/sow_analysis/workers/forced_alignment.py`

Migrate from `services/qwen3/src/sow_qwen3/routes/align.py`:

- `normalize_text(text: str) -> str` — CJK punctuation/whitespace normalization
- `format_timestamp(seconds: float) -> str` — Format as `[mm:ss.xx]`
- `map_segments_to_lines(segments, original_lines) -> list[tuple[float, float, str]]` — Character-level to line-level mapping

Add new function:
- `validate_audio_duration(audio_path: Path, max_seconds: float = 300.0) -> float` — Uses `librosa.get_duration()` instead of `pydub`

```python
import librosa

def validate_audio_duration(audio_path: Path, max_seconds: float = 300.0) -> float:
    duration = librosa.get_duration(path=str(audio_path))
    if duration > max_seconds:
        raise ValueError(f"Audio duration ({duration:.1f}s) exceeds {max_seconds/60:.0f} minute limit")
    return duration
```

### 1e. Add `_process_forced_alignment_job()` to JobQueue

**File**: `services/analysis/src/sow_analysis/workers/queue.py`

Add new method:

```python
async def _process_forced_alignment_job(self, job: Job) -> None:
```

Flow:
1. Download audio from R2 to temp dir (reuse existing `self.r2_client.download_audio()`)
2. Validate duration ≤ 300s using `validate_audio_duration()` (librosa-based)
3. Lazy-init `ForcedAlignerWrapper` if not already loaded (store as `self._forced_aligner_wrapper`)
4. Call `self._forced_aligner_wrapper.align(audio_path, lyrics_text, language)`
5. Map segments to lines using `map_segments_to_lines()`
6. Format as LRC content using `format_timestamp()`
7. Write LRC file to temp dir
8. Upload LRC to R2 (same pattern as LRC job)
9. Set `job.result = JobResult(lrc_url=..., line_count=..., lrc_source="forced_alignment")`

Wire into `_process_job_with_semaphore()`:
```python
if job.type == JobType.FORCED_ALIGNMENT:
    await self._process_forced_alignment_job(job)
```

Add cleanup in `JobQueue.stop()`:
```python
if self._forced_aligner_wrapper is not None:
    await self._forced_aligner_wrapper.cleanup()
```

### 1f. Add API route

**File**: `services/analysis/src/sow_analysis/routes/jobs.py`

```python
@router.post("/jobs/forced-alignment", response_model=JobResponse)
async def submit_forced_alignment_job(
    request: ForcedAlignmentJobRequest,
    api_key: str = Depends(verify_api_key),
) -> JobResponse:
```

### 1g. Update startup config logging

**File**: `services/analysis/src/sow_analysis/main.py`

Replace:
```python
("Qwen3 ForcedAligner", "base_url", settings.SOW_QWEN3_BASE_URL),
```
With:
```python
("Forced Aligner", "model_path", settings.SOW_FORCED_ALIGNER_MODEL_PATH),
("Forced Aligner", "device", settings.SOW_FORCED_ALIGNER_DEVICE),
("Forced Aligner", "max_concurrent", str(settings.SOW_FORCED_ALIGNER_MAX_CONCURRENT)),
```

### 1h. Add dependency

**File**: `services/analysis/pyproject.toml`

Add `qwen-asr>=0.0.6` to core dependencies. This brings in `Qwen3ForcedAligner` and `transformers` as transitive deps. PyTorch is already installed in the Dockerfile.

Do NOT add `pydub` — we use `librosa` instead.

---

## Phase 2: Admin CLI — Add `audio align-lrc` Command

### 2a. Add `AnalysisClient.submit_forced_alignment()` method

**File**: `src/stream_of_worship/admin/services/analysis.py`

```python
def submit_forced_alignment(
    self,
    audio_url: str,
    content_hash: str,
    lyrics_text: str,
    song_title: str = "",
    language: str = "Chinese",
    force: bool = False,
) -> JobInfo:
```

POST to `{base_url}/api/v1/jobs/forced-alignment`.

### 2b. Add `audio align-lrc` CLI command

**File**: `src/stream_of_worship/admin/commands/audio.py`

```python
@app.command("align-lrc")
def align_lrc_recording(
    song_id: Optional[str] = typer.Argument(None, help="Song ID to force-align LRC for"),
    language: str = typer.Option("Chinese", "--lang", help="Language hint: Chinese, English"),
    force: bool = typer.Option(False, "--force", "-f", help="Force re-alignment"),
    stdin: bool = typer.Option(False, "--stdin", help="Read song IDs from stdin"),
    wait: bool = typer.Option(False, "--wait", "-w", help="Wait for alignment to complete"),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c", help="Path to config file"),
) -> None:
```

Helper `_submit_forced_alignment_single()`:
1. Look up recording by song_id → `r2_audio_url`, `content_hash`
2. Look up song → `lyrics_raw`
3. Validate recording has audio URL and lyrics
4. Check `duration_seconds` from recording metadata — reject if > 300s (if available; otherwise let server reject)
5. Submit via `analysis_client.submit_forced_alignment()`
6. Update DB: `lrc_status="processing"`, `lrc_job_id=job_id`
7. If `--wait`, poll until complete, update DB with result

Helper `_submit_forced_alignment_batch()` for `--stdin` mode.

---

## Phase 3: Migrate Tests

### 3a. Migrate `map_segments_to_lines` tests

**New file**: `services/analysis/tests/test_map_segments_to_lines.py`

Copy from `services/qwen3/tests/test_map_segments_to_lines.py` (25 test methods). Update imports to point to `sow_analysis.workers.forced_alignment`.

### 3b. Add forced alignment job tests

**New file**: `services/analysis/tests/test_forced_alignment.py`

- Test `ForcedAlignmentJobRequest` model validation
- Test `POST /api/v1/jobs/forced-alignment` endpoint (auth, validation)
- Test `_process_forced_alignment_job()` with mocked `ForcedAlignerWrapper`
- Test duration validation (> 5 min rejection)
- Test error handling: model not loaded, alignment failure

### 3c. Delete legacy test files

**Delete**:
- `services/analysis/tests/test_qwen3_fallback.py`
- `services/analysis/tests/test_qwen3_regression.py`
- `services/analysis/tests/test_lrc_benchmark.py`

These tested the old HTTP-client-to-qwen3-container path which no longer exists.

---

## Phase 4: Remove Qwen3 Service & Clean Up References

### 4a. Delete `services/qwen3/` directory

Entire directory tree removed.

### 4b. Delete `Qwen3Client` (HTTP client)

**Delete file**: `services/analysis/src/sow_analysis/services/qwen3_client.py`

**Update**: `services/analysis/src/sow_analysis/services/__init__.py` — remove exports of `Qwen3Client`, `Qwen3ClientError`, `AlignRequest`, `AlignResponse`, `OutputFormat`

### 4c. Remove qwen3 services from docker-compose

**File**: `services/analysis/docker-compose.yml`

Remove:
- `qwen3` service definition (lines 106-136)
- `qwen3-dev` service definition (lines 139-175)
- `qwen3-cache` volume
- `SOW_QWEN3_BASE_URL` and `SOW_QWEN3_API_KEY` from common env

Add to common env:
```yaml
SOW_FORCED_ALIGNER_MODEL_PATH: ${SOW_FORCED_ALIGNER_MODEL_PATH:-Qwen/Qwen3-ForcedAligner-0.6B}
SOW_FORCED_ALIGNER_DEVICE: ${SOW_FORCED_ALIGNER_DEVICE:-auto}
SOW_FORCED_ALIGNER_MAX_CONCURRENT: ${SOW_FORCED_ALIGNER_MAX_CONCURRENT:-1}
```

Add model volume mount to analysis service:
```yaml
volumes:
  - analysis-cache:/cache
  - ${SOW_AUDIO_SEPARATOR_MODEL_ROOT}:/models/audio-separator:ro
  - ${SOW_FORCED_ALIGNER_MODEL_ROOT}:/models/hf-model:ro  # NEW (optional, for offline model)
```

### 4d. Delete production docker-compose

**Delete**: `docker/docker-compose.prod.yml` (was standalone qwen3 prod config)

### 4e. Update deploy script

**File**: `services/analysis/scripts/deploy.sh`

- Keep `download_qwen3_model()` function (still needed to download the model)
- Remove references to `sow-qwen3` Docker image
- Remove qwen3 service from docker-compose commands
- Update `get_model_paths()` to set `SOW_FORCED_ALIGNER_MODEL_ROOT` and `SOW_FORCED_ALIGNER_MODEL_SNAPSHOT` instead of `SOW_QWEN3_MODEL_ROOT`/`SOW_QWEN3_MODEL_SNAPSHOT`

### 4f. Clean up analysis service config

**File**: `services/analysis/src/sow_analysis/config.py`

Remove:
```python
SOW_QWEN3_BASE_URL: str = "http://qwen3:8000"
SOW_QWEN3_API_KEY: str = ""
```

### 4g. Clean up analysis service .env.example

**File**: `services/analysis/.env.example`

Remove `SOW_QWEN3_BASE_URL`, `SOW_QWEN3_API_KEY`, `SOW_QWEN3_MODEL_ROOT`, `SOW_QWEN3_MODEL_SNAPSHOT`.

Add `SOW_FORCED_ALIGNER_MODEL_PATH`, `SOW_FORCED_ALIGNER_DEVICE`, `SOW_FORCED_ALIGNER_MAX_CONCURRENT`.

### 4h. Clean up deprecated LrcOptions fields

**File**: `services/analysis/src/sow_analysis/models.py`

Remove deprecated fields from `LrcOptions`:
```python
use_qwen3: Optional[bool] = None
max_qwen3_duration: Optional[int] = None
```

**File**: `services/analysis/src/sow_analysis/routes/jobs.py`

Remove the legacy option rejection block (lines 186-192):
```python
legacy = {"use_qwen3", "max_qwen3_duration"} & set(options)
if legacy:
    raise HTTPException(422, ...)
```

### 4i. Clean up Admin CLI deprecated flag

**File**: `src/stream_of_worship/admin/commands/audio.py`

Remove the `--no-qwen3` hidden/deprecated option and its validation block from the `lrc` command.

### 4j. Update documentation

**Files to update** (remove qwen3 service references, add forced aligner config):
- `services/analysis/README.md`
- `services/analysis/DEVELOPER.md`
- `services/analysis/DEPLOYMENT.md`
- `docs/lrc-job-flow.md`
- `DEVELOPER.md` (root)

### 4k. Update root pyproject.toml

**File**: `pyproject.toml`

The `poc_qwen3_align` extra can remain (POC scripts still use it). No changes needed for POC extras.

---

## File Change Summary

### New Files
| File | Purpose |
|------|---------|
| `services/analysis/src/sow_analysis/workers/forced_aligner.py` | `ForcedAlignerWrapper` class (in-process model wrapper) |
| `services/analysis/src/sow_analysis/workers/forced_alignment.py` | `map_segments_to_lines`, `normalize_text`, `format_timestamp`, `validate_audio_duration` |
| `services/analysis/tests/test_map_segments_to_lines.py` | Migrated from qwen3 service (25 tests) |
| `services/analysis/tests/test_forced_alignment.py` | New tests for forced alignment job |

### Modified Files
| File | Change |
|------|--------|
| `services/analysis/src/sow_analysis/config.py` | Remove `SOW_QWEN3_BASE_URL/API_KEY`, add `SOW_FORCED_ALIGNER_*` |
| `services/analysis/src/sow_analysis/models.py` | Add `FORCED_ALIGNMENT` job type, `ForcedAlignmentOptions`, `ForcedAlignmentJobRequest`; remove deprecated `use_qwen3`/`max_qwen3_duration` |
| `services/analysis/src/sow_analysis/routes/jobs.py` | Add `POST /jobs/forced-alignment`; remove legacy option rejection |
| `services/analysis/src/sow_analysis/workers/queue.py` | Add `_process_forced_alignment_job()`; wire into dispatcher |
| `services/analysis/src/sow_analysis/main.py` | Update startup config logging |
| `services/analysis/src/sow_analysis/services/__init__.py` | Remove `Qwen3Client` exports |
| `services/analysis/pyproject.toml` | Add `qwen-asr>=0.0.6` dependency |
| `services/analysis/docker-compose.yml` | Remove qwen3/qwen3-dev services; add forced aligner env vars |
| `services/analysis/.env.example` | Remove `SOW_QWEN3_*`, add `SOW_FORCED_ALIGNER_*` |
| `services/analysis/scripts/deploy.sh` | Update model paths, remove qwen3 Docker service refs |
| `src/stream_of_worship/admin/services/analysis.py` | Add `submit_forced_alignment()` method |
| `src/stream_of_worship/admin/commands/audio.py` | Add `align-lrc` command; remove `--no-qwen3` deprecated flag |
| Various docs | Remove qwen3 service references |

### Deleted Files
| File | Reason |
|------|--------|
| `services/qwen3/` (entire directory) | Merged into analysis service |
| `services/analysis/src/sow_analysis/services/qwen3_client.py` | HTTP client to separate container; no longer needed |
| `services/analysis/tests/test_qwen3_fallback.py` | Tests old HTTP-client path |
| `services/analysis/tests/test_qwen3_regression.py` | Tests old HTTP-client path |
| `services/analysis/tests/test_lrc_benchmark.py` | Tests old HTTP-client path |
| `docker/docker-compose.prod.yml` | Standalone qwen3 prod config |

---

## Request Flow (After Consolidation)

```
sow-admin audio align-lrc <song_id> --wait
  │
  ├─ DB lookup: recording (r2_audio_url, content_hash, duration_seconds)
  ├─ DB lookup: song (lyrics_raw)
  ├─ Validate: duration <= 5 min (if available in DB)
  │
  ├─ AnalysisClient.submit_forced_alignment()
  │   └─ POST /api/v1/jobs/forced-alignment
  │       └─ JobQueue.submit(FORCED_ALIGNMENT, request)
  │           └─ Job(QUEUED) → asyncio.Queue
  │
  ├─ Poll: GET /api/v1/jobs/{job_id} (every 30s)
  │
  └─ _process_forced_alignment_job() [IN-PROCESS, no HTTP call]
      ├─ Download audio from R2 (reuse R2Client)
      ├─ validate_audio_duration() via librosa (≤ 300s)
      ├─ Lazy-init ForcedAlignerWrapper (first use only)
      ├─ ForcedAlignerWrapper.align(audio_path, lyrics_text, language)
      │   └─ Qwen3ForcedAligner.align() [IN-PROCESS, thread pool]
      │   └─ Returns character-level (start, end, text) segments
      ├─ map_segments_to_lines() → line-level timestamps
      ├─ Write LRC file
      ├─ Upload LRC to R2
      └─ JobResult(lrc_url, line_count, lrc_source="forced_alignment")
```

**Key difference from before**: No HTTP call to a separate container. The `Qwen3ForcedAligner` model runs in-process within the analysis service, protected by a semaphore for concurrency control.

---

## Migration Checklist

- [ ] Phase 1: Add forced alignment worker to analysis service
- [ ] Phase 2: Add `audio align-lrc` CLI command
- [ ] Phase 3: Migrate and update tests
- [ ] Phase 4: Remove qwen3 service and clean up references
- [ ] Verify: `docker compose up analysis` works without qwen3 service
- [ ] Verify: `sow-admin audio align-lrc <song_id> --wait` works end-to-end
- [ ] Verify: `sow-admin audio lrc <song_id>` still works (unchanged)
- [ ] Verify: All tests pass
