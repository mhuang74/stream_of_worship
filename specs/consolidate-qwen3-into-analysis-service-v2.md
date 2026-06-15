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
| Duration validation | Hybrid: `soundfile.info()` first, `librosa.get_duration()` fallback | O(1) for WAV/FLAC via soundfile; librosa fallback for MP3 and other formats |
| Legacy test files | Delete `test_qwen3_fallback.py`, `test_qwen3_regression.py`, `test_lrc_benchmark.py` | They test the old HTTP-client-to-qwen3-container path which no longer exists |
| POC scripts | Leave as-is | Out of scope; experimental code |
| R2 audio download | Reuse existing `R2Client.download_audio()` | Already async, already in analysis service |
| `map_segments_to_lines` | Migrate into `sow_analysis/workers/forced_alignment.py` | Core algorithm, well-tested, must be preserved |
| `Qwen3Client` (HTTP) | Delete | Was the HTTP client to the separate qwen3 container; no longer needed |
| `SOW_QWEN3_BASE_URL` / `SOW_QWEN3_API_KEY` | Remove from analysis config | Was for HTTP calls to qwen3 container; replaced by in-process model |
| New config vars | `SOW_FORCED_ALIGNER_MODEL_PATH`, `SOW_FORCED_ALIGNER_DEVICE`, `SOW_FORCED_ALIGNER_MAX_CONCURRENT` | New names to avoid confusion with DashScope Qwen3 ASR vars |
| Semaphore strategy | External via `optional_semaphore()` | Matches `AudioSeparatorWrapper` pattern; JobQueue controls all concurrency; avoids nested semaphore issues |
| Init failure mode | Raise `RuntimeError` | Matches `AudioSeparatorWrapper._ensure_ready()` pattern; fails loudly on first use instead of silently |
| Model lifecycle | Stay resident until `JobQueue.stop()` | Simpler; no idle TTL complexity; matches current qwen3 service behavior |
| LRC overwrite safety | Copy-before-overwrite | When `--force` is used, copy existing LRC to backup key before uploading new one |
| Dependency version | `qwen-asr>=0.0.6` (open-ended) | Per user preference; accept risk of breaking changes |
| Audio input | Vocals stem preferred (reuse `_resolve_lrc_transcription_audio`) | Clean vocals give better alignment accuracy; same pattern as LRC Whisper/Qwen3 ASR pipeline; auto-triggers stem separation if needed |

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
    use_vocals_stem: bool = True  # Prefer clean vocal stem for better alignment accuracy

class ForcedAlignmentJobRequest(BaseModel):
    audio_url: str
    content_hash: str
    lyrics_text: str
    song_title: str = ""
    options: ForcedAlignmentOptions = Field(default_factory=ForcedAlignmentOptions)
```

Update `Job.request` union to include `ForcedAlignmentJobRequest`.

Update `JobResult.lrc_source` comment to document all valid values:
```python
lrc_source: Optional[str] = None  # youtube_transcript, qwen3_asr, whisper_asr, or forced_alignment
```

### 1c. Create `ForcedAlignerWrapper` (in-process)

**New file**: `services/analysis/src/sow_analysis/workers/forced_aligner.py`

Migrate `Qwen3AlignerWrapper` from `services/qwen3/src/sow_qwen3/workers/aligner.py` with these changes:

- Rename class to `ForcedAlignerWrapper`
- Accept `model_path: str` (HF model ID or local path), `device: str`
- **No internal semaphore** — concurrency controlled externally by `JobQueue` via `optional_semaphore()`
- Lazy initialization with `asyncio.Lock` double-check locking (matches `AudioSeparatorWrapper._ensure_ready()` pattern):
  ```python
  async def _ensure_ready(self) -> None:
      if self._ready:
          return
      async with self._init_lock:
          if self._ready:
              return
          await self.initialize()
          if not self._ready:
              raise RuntimeError("ForcedAligner model failed to load. Check model path and device.")
  ```
- `initialize()`: loads model in thread pool via `run_in_executor`. On failure, sets `_ready=False` but does NOT raise (the `_ensure_ready()` caller raises `RuntimeError` after checking `_ready`).
- `align()` method: calls `_ensure_ready()` first, then runs alignment in thread pool. Same signature, returns `list[tuple[float, float, str]]`. **No semaphore acquisition** inside this method.
- `cleanup()` method: set `_ready=False`, `_model=None`, call `gc.collect()`
- `is_ready` property
- `dtype` hardcoded to `float32` (same as before — the `SOW_QWEN3_DTYPE` config was never actually passed through to the wrapper)

Key differences from old `Qwen3AlignerWrapper`:
1. **No internal semaphore** — removed; concurrency is external
2. **Double-check locking** via `asyncio.Lock` — prevents race condition on first-use lazy init
3. **Raises on init failure** — `_ensure_ready()` raises `RuntimeError` if model fails to load, instead of silently returning

### 1d. Create forced alignment utility functions

**New file**: `services/analysis/src/sow_analysis/workers/forced_alignment.py`

Migrate from `services/qwen3/src/sow_qwen3/routes/align.py`:

- `normalize_text(text: str) -> str` — CJK punctuation/whitespace normalization
- `format_timestamp(seconds: float) -> str` — Format as `[mm:ss.xx]`
- `map_segments_to_lines(segments, original_lines) -> list[tuple[float, float, str]]` — Character-level to line-level mapping

Add new function with hybrid duration validation:

```python
import soundfile
import librosa

def validate_audio_duration(audio_path: Path, max_seconds: float = 300.0) -> float:
    """Validate audio duration using soundfile (O(1) for WAV/FLAC) with librosa fallback."""
    try:
        info = soundfile.info(str(audio_path))
        duration = info.duration
    except Exception:
        duration = librosa.get_duration(path=str(audio_path))
    if duration > max_seconds:
        raise ValueError(f"Audio duration ({duration:.1f}s) exceeds {max_seconds/60:.0f} minute limit")
    return duration
```

### 1e. Refactor `_resolve_lrc_transcription_audio` → `_resolve_transcription_audio`

**File**: `services/analysis/src/sow_analysis/workers/queue.py`

The existing `_resolve_lrc_transcription_audio()` currently takes a `LrcJobRequest` and checks `request.options.use_vocals_stem`. Refactor to accept generic parameters so both LRC and forced alignment jobs can use it:

```python
async def _resolve_transcription_audio(
    self,
    job: Job,
    audio_url: str,
    content_hash: str,
    use_vocals_stem: bool,
    temp_path: Path,
    audio_path: Path,
) -> ResolvedTranscriptionAudio:
```

Keep `_resolve_lrc_transcription_audio()` as a thin wrapper that delegates to `_resolve_transcription_audio()` for backward compatibility:

```python
async def _resolve_lrc_transcription_audio(
    self, job: Job, request: LrcJobRequest, temp_path: Path, audio_path: Path,
) -> ResolvedTranscriptionAudio:
    return await self._resolve_transcription_audio(
        job, request.audio_url, request.content_hash,
        request.options.use_vocals_stem, temp_path, audio_path,
    )
```

### 1f. Add `_process_forced_alignment_job()` to JobQueue

**File**: `services/analysis/src/sow_analysis/workers/queue.py`

Add `ForcedAlignerWrapper` as a class attribute (lazy-initialized, like `_separator_wrapper`):

```python
self._forced_aligner_wrapper: Optional[Any] = None
```

Add setter method (matches `set_separator_wrapper` pattern):
```python
def set_forced_aligner_wrapper(self, wrapper: Any) -> None:
    self._forced_aligner_wrapper = wrapper
```

Add new method:

```python
async def _process_forced_alignment_job(self, job: Job) -> None:
```

Flow:
1. Download audio from R2 to temp dir (reuse existing `self.r2_client.download_audio()`)
2. **Resolve transcription audio** via `_resolve_transcription_audio()` — prefers `vocals_dry` FLAC (clean vocal stem), falls back to `vocals` FLAC, then full mix MP3. Auto-triggers a child stem separation job if `use_vocals_stem=True` and no stems exist yet. This reuses the same resolution logic as the LRC Whisper/Qwen3 ASR pipeline.
3. Validate duration ≤ 300s using `validate_audio_duration()` (hybrid soundfile/librosa) on the resolved audio
4. Lazy-init `ForcedAlignerWrapper` via `_ensure_ready()` (raises RuntimeError on failure)
5. Call `self._forced_aligner_wrapper.align(resolved_audio_path, lyrics_text, language)`
6. Map segments to lines using `map_segments_to_lines()`
7. Format as LRC content using `format_timestamp()`
8. Write LRC file to temp dir
9. Upload LRC to R2 (same pattern as LRC job — `r2_client.upload_lrc()`)
10. Set `job.result = JobResult(lrc_url=..., line_count=..., lrc_source="forced_alignment")`

Wire into `_process_job_with_semaphore()`:
```python
elif job.type == JobType.FORCED_ALIGNMENT:
    # Forced alignment uses local model - acquire semaphore for entire job
    async with self._local_model_semaphore:
        await self._process_forced_alignment_job(job)
```

**Important**: Forced alignment acquires `_local_model_semaphore` for the **entire** job (like ANALYZE), not just the model portion (like LRC/STEM_SEPARATION). This is because the entire job is local-model work — there's no cloud fallback step. The `SOW_FORCED_ALIGNER_MAX_CONCURRENT` config is used to create the `ForcedAlignerWrapper` but the wrapper itself does NOT use an internal semaphore; the external `_local_model_semaphore` provides the concurrency control.

> **Operational note**: With `SOW_MAX_CONCURRENT_LOCAL_MODEL_JOBS=1` (default), a forced alignment job will block all other local model work (analysis, stem separation, Whisper) for its duration. If concurrent operation is needed, increase `SOW_MAX_CONCURRENT_LOCAL_MODEL_JOBS` and ensure sufficient GPU/CPU memory.

Add cleanup in `JobQueue.stop()`:
```python
if self._forced_aligner_wrapper is not None:
    await self._forced_aligner_wrapper.cleanup()
```

### 1g. Wire ForcedAlignerWrapper into service startup

**File**: `services/analysis/src/sow_analysis/main.py`

In the startup sequence (alongside `set_separator_wrapper`), create and set the `ForcedAlignerWrapper`:

```python
from .workers.forced_aligner import ForcedAlignerWrapper

forced_aligner_wrapper = ForcedAlignerWrapper(
    model_path=settings.SOW_FORCED_ALIGNER_MODEL_PATH,
    device=settings.SOW_FORCED_ALIGNER_DEVICE,
)
job_queue.set_forced_aligner_wrapper(forced_aligner_wrapper)
```

The wrapper is NOT initialized at startup — `_ensure_ready()` is called lazily on first forced alignment job.

### 1h. Add API route

**File**: `services/analysis/src/sow_analysis/routes/jobs.py`

```python
@router.post("/jobs/forced-alignment", response_model=JobResponse)
async def submit_forced_alignment_job(
    request: ForcedAlignmentJobRequest,
    api_key: str = Depends(verify_api_key),
) -> JobResponse:
```

### 1i. Update startup config logging

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

### 1j. Add dependency

**File**: `services/analysis/pyproject.toml`

Add `qwen-asr>=0.0.6` to core dependencies. This brings in `Qwen3ForcedAligner` and `transformers` as transitive deps. PyTorch is already installed in the Dockerfile.

Do NOT add `pydub` — we use `soundfile`/`librosa` instead.

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
    use_vocals_stem: bool = True,
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
    use_vocals_stem: bool = typer.Option(True, "--use-vocals-stem/--no-vocals-stem", help="Use clean vocal stem for better accuracy"),
    stdin: bool = typer.Option(False, "--stdin", help="Read song IDs from stdin"),
    wait: bool = typer.Option(False, "--wait", "-w", help="Wait for alignment to complete"),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c", help="Path to config file"),
) -> None:
```

Helper `_submit_forced_alignment_single()`:
1. Look up recording by song_id → `r2_audio_url`, `content_hash`
2. Look up song → `lyrics_raw`
3. Validate recording has audio URL and lyrics
4. **Guard: reject if `lrc_status == "processing"`** — an existing LRC job is in flight; do not overwrite `lrc_job_id`
5. **Copy-before-overwrite**: if `lrc_status == "completed"` and `--force` is set, copy existing LRC from R2 to a backup key (e.g., `lyrics.{lang}.backup.{timestamp}.lrc`) before proceeding
6. Check `duration_seconds` from recording metadata — reject if > 300s (if available; otherwise let server reject)
7. Submit via `analysis_client.submit_forced_alignment()`
8. Update DB: `lrc_status="processing"`, `lrc_job_id=job_id`
9. If `--wait`, poll until complete, update DB with result

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
- Test error handling: model not loaded (RuntimeError from `_ensure_ready()`), alignment failure
- Test `_ensure_ready()` double-check locking (concurrent first-use calls)

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
- `qwen3` service definition
- `qwen3-dev` service definition
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
- **Model path reuse**: The downloaded model cache on the host filesystem can be reused as-is — only the env var names change. The actual snapshot directory path remains the same.

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

Remove the legacy option rejection block:
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
| `services/analysis/src/sow_analysis/workers/forced_aligner.py` | `ForcedAlignerWrapper` class (in-process model wrapper, no internal semaphore, double-check locking, raises on init failure) |
| `services/analysis/src/sow_analysis/workers/forced_alignment.py` | `map_segments_to_lines`, `normalize_text`, `format_timestamp`, `validate_audio_duration` (hybrid soundfile/librosa) |
| `services/analysis/tests/test_map_segments_to_lines.py` | Migrated from qwen3 service (25 tests) |
| `services/analysis/tests/test_forced_alignment.py` | New tests for forced alignment job |

### Modified Files
| File | Change |
|------|--------|
| `services/analysis/src/sow_analysis/config.py` | Remove `SOW_QWEN3_BASE_URL/API_KEY`, add `SOW_FORCED_ALIGNER_*` |
| `services/analysis/src/sow_analysis/models.py` | Add `FORCED_ALIGNMENT` job type, `ForcedAlignmentOptions`, `ForcedAlignmentJobRequest`; remove deprecated `use_qwen3`/`max_qwen3_duration`; update `lrc_source` comment |
| `services/analysis/src/sow_analysis/routes/jobs.py` | Add `POST /jobs/forced-alignment`; remove legacy option rejection |
| `services/analysis/src/sow_analysis/workers/queue.py` | Add `_forced_aligner_wrapper`, `set_forced_aligner_wrapper()`, `_resolve_transcription_audio()` (refactored from `_resolve_lrc_transcription_audio`), `_process_forced_alignment_job()`; wire into dispatcher with `_local_model_semaphore`; cleanup in `stop()` |
| `services/analysis/src/sow_analysis/main.py` | Update startup config logging; create and set `ForcedAlignerWrapper` |
| `services/analysis/src/sow_analysis/services/__init__.py` | Remove `Qwen3Client` exports |
| `services/analysis/pyproject.toml` | Add `qwen-asr>=0.0.6` dependency |
| `services/analysis/docker-compose.yml` | Remove qwen3/qwen3-dev services; add forced aligner env vars |
| `services/analysis/.env.example` | Remove `SOW_QWEN3_*`, add `SOW_FORCED_ALIGNER_*` |
| `services/analysis/scripts/deploy.sh` | Update model paths, remove qwen3 Docker service refs |
| `src/stream_of_worship/admin/services/analysis.py` | Add `submit_forced_alignment()` method |
| `src/stream_of_worship/admin/commands/audio.py` | Add `align-lrc` command with `lrc_status=="processing"` guard and copy-before-overwrite; remove `--no-qwen3` deprecated flag |
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
  ├─ Guard: reject if lrc_status == "processing"
  ├─ Copy-before-overwrite: if --force and existing LRC, backup to R2
  │
  ├─ AnalysisClient.submit_forced_alignment()
  │   └─ POST /api/v1/jobs/forced-alignment
  │       └─ JobQueue.submit(FORCED_ALIGNMENT, request)
  │           └─ Job(QUEUED) → asyncio.Queue
  │
  ├─ Poll: GET /api/v1/jobs/{job_id} (every 30s)
  │
  └─ _process_forced_alignment_job() [IN-PROCESS, no HTTP call]
      ├─ Acquire _local_model_semaphore (external concurrency control)
      ├─ Download audio from R2 (reuse R2Client)
      ├─ _resolve_transcription_audio() — prefers vocals_dry FLAC
      │   ├─ Check R2 for vocals_dry.flac → download if found
      │   ├─ Else: auto-trigger child STEM_SEPARATION job → wait → download vocals
      │   └─ Fallback: use full mix MP3
      ├─ validate_audio_duration() via soundfile/librosa hybrid (≤ 300s)
      ├─ Lazy-init ForcedAlignerWrapper via _ensure_ready() (double-check lock, raises on failure)
      ├─ ForcedAlignerWrapper.align(resolved_audio_path, lyrics_text, language)
      │   └─ Qwen3ForcedAligner.align() [IN-PROCESS, thread pool]
      │   └─ Returns character-level (start, end, text) segments
      ├─ map_segments_to_lines() → line-level timestamps
      ├─ Write LRC file
      ├─ Upload LRC to R2
      └─ JobResult(lrc_url, line_count, lrc_source="forced_alignment")
```

**Key difference from before**: No HTTP call to a separate container. The `Qwen3ForcedAligner` model runs in-process within the analysis service, with concurrency controlled by the existing `_local_model_semaphore`.

---

## Operational Notes

### Semaphore Interaction

With `SOW_MAX_CONCURRENT_LOCAL_MODEL_JOBS=1` (default), a forced alignment job holds the global local-model semaphore for its entire duration, blocking ALL other local model work (analysis, stem separation, Whisper). This is correct because forced alignment is entirely local-model work with no cloud fallback.

If concurrent forced alignment + analysis is needed, increase `SOW_MAX_CONCURRENT_LOCAL_MODEL_JOBS` and ensure sufficient GPU/CPU memory (~1.2GB for ForcedAligner + ~2-4GB for other models).

### Stem Separation Dependency

When `use_vocals_stem=True` (default), forced alignment prefers the clean vocal stem for better accuracy. If no stems exist in R2, a child `STEM_SEPARATION` job is auto-triggered and the forced alignment job waits for it to complete (up to 2-hour timeout, same as LRC pipeline). This adds latency on first run but results are cached in R2 for subsequent runs.

If stem separation is not desired (e.g., for quick testing), use `--no-vocals-stem` to skip stem resolution and use the full mix directly.

### GPU Memory

The ForcedAligner model (~1.2GB) stays resident after first lazy init. Combined with Whisper, Demucs, and AudioSeparator models, GPU VRAM could be exhausted on constrained hardware. Monitor GPU memory usage after first forced alignment job.

### Model Path Migration

The downloaded model cache on the host filesystem can be reused as-is when migrating from qwen3 to the consolidated service. Only the env var names change (`SOW_QWEN3_MODEL_ROOT` → `SOW_FORCED_ALIGNER_MODEL_ROOT`). The actual snapshot directory path and contents remain the same.

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
