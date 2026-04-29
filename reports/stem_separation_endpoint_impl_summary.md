# Implementation Summary: Stem Separation Endpoint v2

## Overview

Implemented the `/jobs/stem-separation` endpoint in the analysis service that runs BS-Roformer + UVR-De-Echo to generate clean vocals and instrumental stems. This endpoint auto-triggers from the LRC worker when a clean vocal stem is missing in R2.

---

## Files Created

### 1. `services/analysis/src/sow_analysis/workers/separator_wrapper.py`

**Purpose:** Async wrapper for audio-separator library with lifecycle management.

**Key Components:**
- `AudioSeparatorWrapper` class mirroring `Qwen3AlignerWrapper` pattern
- Pre-loads both BS-Roformer and UVR-De-Echo models at startup via `initialize()`
- `separate_stems()` method runs two-stage separation in thread pool
- Graceful failure handling (service can start without models)
- Cleanup method for resource release

**Configuration:**
- `model_dir`: Host-bind-mounted model directory
- `bs_roformer_model`: Configurable BS-Roformer model filename
- `dereverb_model`: Configurable UVR-De-Echo model filename
- `output_format`: "FLAC" (canonical format)

---

### 2. `services/analysis/src/sow_analysis/workers/stem_separation.py`

**Purpose:** Stem separation worker implementing the two-stage pipeline.

**Key Functions:**

#### `process_stem_separation(job, separator_wrapper, r2_client, cache_manager)`
- Downloads audio from R2
- Runs two-stage separation via `separator_wrapper.separate_stems()`
- Caches results locally at `/cache/stems_clean/{hash[:32]}/`
- Uploads `vocals_clean.flac` and `instrumental_clean.flac` to R2
- Sets `JobResult` with URLs to both stems
- Short-circuit if stems already exist (R2 check with force override)

#### `get_clean_vocals_url(content_hash, r2_client)`
- Checks for existing vocals in priority order:
  1. `vocals_clean.flac` (canonical)
  2. `vocals_clean.wav` (legacy admin-produced)
  3. `vocals.wav` (Demucs legacy)
- Returns S3 URL if found, None otherwise

---

## Files Modified

### 3. `services/analysis/src/sow_analysis/models.py`

**Changes:**
- Added `STEM_SEPARATION = "stem_separation"` to `JobType` enum
- Added `StemSeparationOptions` Pydantic model:
  - `force: bool = False`
  - `dereverb_model: str = "UVR-De-Echo-Normal.pth"`
- Added `StemSeparationJobRequest` Pydantic model:
  - `audio_url: str`
  - `content_hash: str`
  - `options: StemSeparationOptions`
- Extended `JobResult` with:
  - `vocals_clean_url: Optional[str]`
  - `instrumental_clean_url: Optional[str]`
- Updated `Job.request` Union type to include `StemSeparationJobRequest`

---

### 4. `services/analysis/src/sow_analysis/config.py`

**Changes:**
- Added `SOW_MAX_CONCURRENT_STEM_SEPARATION_JOBS: int = 1`
- Added `SOW_AUDIO_SEPARATOR_MODEL_DIR: Path = Path("/models/audio-separator")`
- Added `SOW_BS_ROFORMER_MODEL: str = "model_bs_roformer_ep_317_sdr_12.9755.ckpt"`
- Added `SOW_DEREVERB_MODEL: str = "UVR-De-Echo-Normal.pth"`

---

### 5. `services/analysis/src/sow_analysis/storage/db.py`

**Changes:**
- Updated `CHECK (type IN (...))` constraint to include `'stem_separation'`
- Updated `_row_to_job()` to use explicit per-type branching:
  ```python
  if job_type == JobType.ANALYZE:
      request = AnalyzeJobRequest.model_validate_json(request_json)
  elif job_type == JobType.LRC:
      request = LrcJobRequest.model_validate_json(request_json)
  elif job_type == JobType.STEM_SEPARATION:
      request = StemSeparationJobRequest.model_validate_json(request_json)
  else:
      raise ValueError(f"Unknown job type: {job_type}")
  ```
- Note: `jobs.db` will be wiped on first run with new image due to schema change

---

### 6. `services/analysis/src/sow_analysis/storage/r2.py`

**Changes:**
- Added `upload_clean_stems(hash_prefix, vocals_clean, instrumental_clean)` method:
  - Uploads to `{hash_prefix}/stems/vocals_clean.flac`
  - Uploads to `{hash_prefix}/stems/instrumental_clean.flac` (if provided)
  - Returns tuple of (vocals_url, instrumental_url)

---

### 7. `services/analysis/src/sow_analysis/workers/queue.py`

**Major Changes:**

#### Constructor Updates
- Added `max_concurrent_stem_separation: int = 1` parameter
- Added `_stem_separation_lock = asyncio.Lock()` for serialization
- Added `_separator_wrapper` attribute

#### New Methods

**`_process_stem_separation_job(job)`**
- Validates request type and dependencies
- Calls `process_stem_separation()` worker function
- Persists results to database
- Error handling with `StemSeparationWorkerError`

**`set_separator_wrapper(wrapper)`**
- Dependency injection for AudioSeparatorWrapper

#### LRC Job Auto-Trigger Integration

Updated `_process_lrc_job()` stem lookup block (lines ~559-600):

**Before:**
```python
for stem_name in ["vocals_clean", "vocals"]:
    stem_url = f"s3://.../stems/{stem_name}.wav"
    if await self.r2_client.check_exists(stem_url):
        # download and use
```

**After:**
```python
# Check for clean vocals using helper
vocals_stem_url = await get_clean_vocals_url(content_hash, self.r2_client)

if vocals_stem_url:
    # Download and use for transcription
    transcription_path = stem_path
    job.stage = "using_vocals_stem"
else:
    # Auto-trigger stem separation
    job.stage = "submitting_stem_separation_child"
    child_job = await self.submit(JobType.STEM_SEPARATION, child_request)
    
    # Release LRC semaphore while waiting
    # Poll child job status
    # Re-acquire LRC semaphore when complete
    
    if child_job.success:
        vocals_stem_url = child_job.result.vocals_clean_url
        transcription_path = vocals_path
```

#### Concurrency Model

```python
# JobQueue.__init__
self._analysis_lock = asyncio.Lock()           # 1 ANALYZE
self._lrc_semaphore = asyncio.Semaphore(2)     # 2 LRC concurrent
self._stem_separation_lock = asyncio.Lock()    # 1 STEM_SEPARATION
```

---

### 8. `services/analysis/src/sow_analysis/routes/jobs.py`

**Changes:**
- Added import for `StemSeparationJobRequest`
- Extended `job_to_response()` to copy new URL fields:
  - `vocals_clean_url`
  - `instrumental_clean_url`
- Added new endpoint:
  ```python
  @router.post("/jobs/stem-separation", response_model=JobResponse)
  async def submit_stem_separation_job(request: StemSeparationJobRequest, ...)
  ```

---

### 9. `services/analysis/src/sow_analysis/main.py`

**Changes:**
- Added conditional import for `AudioSeparatorWrapper`
- Added global `separator_wrapper` variable
- Extended `lifespan()`:
  - Passes `max_concurrent_stem_separation` to `JobQueue`
  - Initializes `AudioSeparatorWrapper` with config settings
  - Injects wrapper into `JobQueue` via `set_separator_wrapper()`
  - Cleanup on shutdown via `separator_wrapper.cleanup()`

---

### 10. `services/analysis/Dockerfile`

**Changes:**
- Added `mkdir -p /models/audio-separator` to create model directory

---

### 11. `services/analysis/docker-compose.yml`

**Changes:**

#### Common Environment Variables
- Added `SOW_MAX_CONCURRENT_STEM_SEPARATION_JOBS: ${SOW_MAX_CONCURRENT_STEM_SEPARATION_JOBS:-1}`
- Added `SOW_AUDIO_SEPARATOR_MODEL_DIR: ${SOW_AUDIO_SEPARATOR_MODEL_DIR:-/models/audio-separator}`
- Added `SOW_BS_ROFORMER_MODEL: ${SOW_BS_ROFORMER_MODEL:-model_bs_roformer_ep_317_sdr_12.9755.ckpt}`
- Added `SOW_DEREVERB_MODEL: ${SOW_DEREVERB_MODEL:-UVR-De-Echo-Normal.pth}`

#### Volumes (analysis and analysis-dev services)
- Added bind mount: `${SOW_AUDIO_SEPARATOR_MODEL_ROOT}:/models/audio-separator:ro`

---

## Concurrency Model (Final)

```python
# JobQueue.__init__
self._analysis_lock = asyncio.Lock()                        # 1 ANALYZE
self._lrc_semaphore = asyncio.Semaphore(max_concurrent_lrc)  # default 2
self._stem_separation_lock = asyncio.Lock()                 # 1 STEM_SEPARATION
```

**LRC Auto-Trigger Flow:**
1. LRC worker holds `_lrc_semaphore` slot, downloads audio
2. Stem lookup: checks R2 for `vocals_clean.flac`
3. If none found and `use_vocals_stem=True`:
   - Submit child `STEM_SEPARATION` job via `JobQueue.submit()`
   - **Release `_lrc_semaphore` slot**
   - Poll child job status via `JobStore.get_job(child_id)` until terminal state
   - **Re-acquire `_lrc_semaphore` slot**
4. Download `vocals_clean.flac`
5. Continue with Whisper + Qwen3 (using clean vocals URL)

---

## Data-Flow Contract

### R2 Layout (Canonical)
- `s3://{bucket}/{hash_prefix}/stems/vocals_clean.flac`
- `s3://{bucket}/{hash_prefix}/stems/instrumental_clean.flac`

### R2 Fallbacks (Read-Only Legacy)
- `s3://{bucket}/{hash_prefix}/stems/vocals_clean.wav` (admin-produced before deprecation)
- `s3://{bucket}/{hash_prefix}/stems/vocals.wav` (Demucs)

### Local Cache
- `/cache/stems_clean/{content_hash[:32]}/vocals_clean.flac`
- `/cache/stems_clean/{content_hash[:32]}/instrumental_clean.flac`

### Models
- Pre-downloaded on host at `${SOW_AUDIO_SEPARATOR_MODEL_ROOT}`
- Bind-mounted `:ro` at `/models/audio-separator/` in container
- Loaded once at FastAPI lifespan startup

---

## Admin CLI Changes (Pending)

### `src/stream_of_worship/admin/services/analysis.py`
To be updated with:
- `submit_stem_separation(audio_url, content_hash, force=False, dereverb_model=None)` method
- Update `AnalysisResult` and `_parse_job_response` for new URL fields

### `src/stream_of_worship/admin/commands/audio.py`
- Remove `vocal_clean()` function (lines ~1358-1573)
- Remove `vocal` Typer command
- Per user decision: "remove completely, no need for message"

### `src/stream_of_worship/admin/services/r2.py`
- Keep `upload_stem()` method for future manual stem uploads
- No changes needed (per user decision)

---

## Model Download Instructions (To Add to README)

Models must be pre-downloaded on the host before running the service:

```bash
# One-time host setup
python -c "
from audio_separator.separator import Separator

# Download BS-Roformer model
sep = Separator(output_dir='/tmp', output_format='FLAC')
sep.load_model(model_filename='model_bs_roformer_ep_317_sdr_12.9755.ckpt')

# Download UVR-De-Echo model
sep2 = Separator(output_dir='/tmp', output_format='FLAC')
sep2.load_model(model_filename='UVR-De-Echo-Normal.pth')

print('Models downloaded successfully')
"
```

Set environment variable:
```bash
export SOW_AUDIO_SEPARATOR_MODEL_ROOT="$HOME/.cache/audio-separator"
```

---

## Testing

### Unit Tests (No Docker)
```bash
PYTHONPATH=src uv run --python 3.11 --extra app --extra test pytest tests/services/analysis/test_stem_separation_worker.py -v
```

### Integration Test (Docker)
```bash
# Pre-download host models
# Rebuild and start
cd services/analysis && docker compose build && docker compose up -d

# Verify lifespan logs "audio-separator models loaded"

# Submit job
curl -X POST http://localhost:8000/api/v1/jobs/stem-separation \
  -H "Authorization: Bearer $SOW_ANALYSIS_API_KEY" \
  -d '{"audio_url":"s3://...","content_hash":"<hash>"}'

# Poll through stages: downloading → stage1_bs_roformer → stage2_dereverb → uploading → complete
```

### E2E LRC Integration
```bash
# Pick song without vocals_clean.flac
sow_admin audio lrc <song_id>

# Confirm stages: submitting_stem_separation_child → awaiting_stem_separation:<child_id> → using_vocals_clean_stem → transcribing
```

---

## Out of Scope (Future Work)

- Rename `services/qwen3` → `services/forced_aligner`
- Pre-bake host model directory provisioning automation
- `sow_admin audio regenerate-clean-stems <song_id>` CLI subcommand
- Backfill script for existing catalog
- Verify no remaining callers of `admin/services/r2.py:upload_stem()` before removal

---

## Migration Notes

1. **Database:** `jobs.db` will be automatically wiped/recreated on first startup with new image (acceptable: only holds ≤7-day job history)

2. **R2 Storage:** Existing `vocals_clean.wav` files remain accessible via fallback chain

3. **Admin CLI:** `sow-admin audio vocal` command removed entirely; use LRC auto-trigger or future `regenerate-clean-stems` command

4. **Models:** Host must have models pre-downloaded before container starts; service will log warning and skip stem separation if models unavailable

---

## Verification Checklist

- [ ] Unit tests pass (mocked `Separator.separate()`)
- [ ] Integration test: submit stem-separation job, poll through stages
- [ ] Confirm both FLAC files exist in R2 after completion
- [ ] E2E LRC test: confirm auto-trigger and child job creation
- [ ] Verify Qwen3 receives clean vocals URL (check logs)
- [ ] Concurrency test: two stem-separation jobs queue serially
- [ ] Concurrency test: LRC releases semaphore during stem wait
- [ ] DB migration: confirm clean wipe/recreation
- [ ] Idempotency: re-submit same job returns cached result quickly
