# MVSEP Cloud API for Vocal Stem Separation

## Context

Vocal stem separation currently runs locally via `AudioSeparatorWrapper` (BS-Roformer + UVR-De-Echo). This is the throughput bottleneck: jobs are serialized (`asyncio.Lock`) because the models require high GPU/CPU memory. By offloading separation to the MVSEP cloud API as the default backend, we free local compute and benefit from MVSEP's beefy cloud GPUs. The local pipeline becomes a fallback after 3 MVSEP failures per job.

A working POC exists at `../stream_of_worship/poc/gen_clean_vocal_stem_mvsep.py` demonstrating the full MVSEP submit/poll/download flow for both stages.

## Design Decisions

- **Both stages via MVSEP**: Stage 1 (BS Roformer, sep_type=40) + Stage 2 (Reverb Removal, sep_type=22)
- **Concurrency stays at 1**: `asyncio.Lock` serialization unchanged
- **Per-job retries**: Each job independently tries MVSEP up to 3 times, then falls back to local
- **Analysis service only**: Admin CLI (`sow-admin audio vocal`) unchanged

## Files to Modify

| File | Action |
|------|--------|
| `services/analysis/src/sow_analysis/config.py` | Add MVSEP settings |
| `services/analysis/src/sow_analysis/services/mvsep_client.py` | **New file** — async MVSEP client |
| `services/analysis/src/sow_analysis/services/__init__.py` | Export new client |
| `services/analysis/src/sow_analysis/workers/stem_separation.py` | Add retry-with-fallback orchestration |
| `services/analysis/src/sow_analysis/workers/queue.py` | Wire `mvsep_client` through to worker |
| `services/analysis/src/sow_analysis/main.py` | Initialize MVSEP client at startup |
| `services/analysis/.env.example` | Document new env vars |
| `services/analysis/tests/test_mvsep_client.py` | **New file** — unit tests |
| `services/analysis/tests/test_mvsep_fallback.py` | **New file** — fallback logic tests |

## Implementation Steps

### Step 1: Add MVSEP settings to `config.py`

Add after the existing stem separation block (line 45):

```python
# MVSEP Cloud API
SOW_MVSEP_API_KEY: str = ""
SOW_MVSEP_ENABLED: bool = True
SOW_MVSEP_VOCAL_MODEL: int = 81       # sep_type=40 add_opt1 (BS Roformer 2025.07)
SOW_MVSEP_DEREVERB_MODEL: int = 0     # sep_type=22 add_opt1 (FoxJoy MDX23C)
SOW_MVSEP_TIMEOUT: int = 900          # Max seconds per stage
```

Effective behavior: MVSEP is "enabled" only when `SOW_MVSEP_ENABLED=True` AND `SOW_MVSEP_API_KEY` is non-empty. Existing deployments without the key get zero behavioral change.

### Step 2: Create `MvsepClient` (new file)

**File**: `services/analysis/src/sow_analysis/services/mvsep_client.py`

Async client using `httpx.AsyncClient`, following the `Qwen3Client` pattern in `services/qwen3_client.py`.

**Class**: `MvsepClient`
- Constructor takes `api_token`, `enabled`, `vocal_model`, `dereverb_model`, `timeout` — all defaulting from `settings` if not provided.
- `is_available` property: `True` when `enabled` and `api_token` is non-empty.

**Exception classes**: `MvsepClientError` (base), `MvsepTimeoutError` (timeout).

**Private methods** (ported from POC, made async):

```python
async def _submit_job(audio_path, sep_type, add_opt1, add_opt2=None, output_format=2) -> str:
    """POST /create with multipart form data. Returns job hash."""
    # httpx.AsyncClient, timeout=120s for upload
    # Raises MvsepClientError on API error or HTTP failure

async def _poll_job(job_hash) -> dict:
    """GET /get?hash=... with exponential backoff. Returns data dict when done."""
    # asyncio.sleep (not time.sleep) — 5s initial, 1.5x factor, 30s max
    # Raises MvsepTimeoutError after self.timeout seconds
    # Raises MvsepClientError on terminal status (failed, not_found, error)

async def _download_files(file_entries, output_dir) -> list[Path]:
    """Download result files via streaming httpx. Returns paths."""
    # 65536 byte chunks, filename from entry["name"] or URL
```

**Public methods**:

```python
async def separate_vocals(input_path, output_dir, stage_callback=None) -> (vocals, instrumental):
    """Stage 1: BS Roformer (sep_type=40, add_opt1=self.vocal_model)."""
    # Invokes stage_callback("mvsep_stage1_submitting"), etc.
    # Identifies vocals/instrumental by filename matching ("vocal" / "instrumental" / "accompaniment")

async def remove_reverb(vocals_path, output_dir, stage_callback=None) -> (dry_vocals, reverb):
    """Stage 2: Reverb Removal (sep_type=22, add_opt1=self.dereverb_model, add_opt2=1)."""
    # Identifies dry vocals by "no reverb" / "noreverb" / "no_echo" / "no echo" / "dry" in filename

async def separate_stems(input_path, output_dir, stage_callback=None) -> (vocals_clean, vocals_reverb, instrumental):
    """Full two-stage pipeline. Same return signature as AudioSeparatorWrapper.separate_stems()."""
    # vocals_reverb = Stage 1 vocals (before de-reverb)
```

**Error handling** (matching `qwen3_client.py` lines 121-135):
- `httpx.HTTPStatusError` -> `MvsepClientError`
- `httpx.TimeoutException` -> `MvsepClientError`
- `httpx.RequestError` -> `MvsepClientError`

### Step 3: Update `services/__init__.py`

Add exports for `MvsepClient`, `MvsepClientError`, `MvsepTimeoutError`.

### Step 4: Add retry-with-fallback to `stem_separation.py`

**New helper** (add before `process_stem_separation`):

```python
MVSEP_MAX_RETRIES = 3

async def _separate_with_mvsep_fallback(
    input_path: Path,
    output_dir: Path,
    job: Job,
    mvsep_client: Optional["MvsepClient"],
    separator_wrapper: AudioSeparatorWrapper,
) -> Tuple[Optional[Path], Optional[Path], Optional[Path]]:
```

Logic:
1. If `mvsep_client` is available (`is_available` returns True):
   - Loop up to `MVSEP_MAX_RETRIES` times
   - Each attempt: call `mvsep_client.separate_stems()` with a `stage_callback` that updates `job.stage`
   - On `MvsepClientError`: log warning, continue to next attempt
   - After 3 failures: log warning, set `job.stage = "fallback_local"`, break
2. Fall through to local: call `separator_wrapper.separate_stems(input_path, output_dir)`

**Modify `process_stem_separation` signature** (line 28):

```python
async def process_stem_separation(
    job: Job,
    separator_wrapper: AudioSeparatorWrapper,
    r2_client: R2Client,
    cache_manager: CacheManager,
    mvsep_client: Optional["MvsepClient"] = None,  # NEW
) -> None:
```

**Replace lines 135-150** (the block calling `separator_wrapper.separate_stems` directly) with a call to `_separate_with_mvsep_fallback`. Everything after (renaming, caching, uploading) stays identical — the output tuple is the same regardless of backend.

### Step 5: Wire `mvsep_client` through `queue.py`

Three changes:

**a.** Add `self._mvsep_client: Optional[Any] = None` in `__init__` (after line 100).

**b.** Add setter method after `set_separator_wrapper` (line 137):

```python
def set_mvsep_client(self, mvsep_client: Any) -> None:
    self._mvsep_client = mvsep_client
```

**c.** Pass it in the `process_stem_separation` call (line 1025):

```python
await process_stem_separation(
    job=job,
    separator_wrapper=self._separator_wrapper,
    r2_client=self.r2_client,
    cache_manager=self.cache_manager,
    mvsep_client=self._mvsep_client,  # NEW
)
```

**Important**: The existing separator-readiness check (lines 967-1017) stays unchanged. Even when MVSEP is the primary backend, the local `AudioSeparatorWrapper` must be ready because it's the fallback. If MVSEP is available but local init hasn't finished, the job still waits — this ensures the fallback path can't fail due to uninitialized models.

### Step 6: Initialize MVSEP client in `main.py`

In the `lifespan` function, after R2 initialization (line 83) and before `set_job_queue` (line 86):

```python
from .services.mvsep_client import MvsepClient

if settings.SOW_MVSEP_API_KEY and settings.SOW_MVSEP_ENABLED:
    mvsep_client = MvsepClient()
    job_queue.set_mvsep_client(mvsep_client)
    logger.info("MVSEP client initialized (cloud stem separation enabled)")
else:
    logger.info("MVSEP not configured (using local audio-separator only)")
```

No background task needed — `MvsepClient` is stateless (no models to load).

### Step 7: Update `.env.example`

Add after the "Stem Separation Model Selection" section (after line 149):

```
# ========================================
# MVSEP Cloud API Configuration (Optional)
# ========================================

SOW_MVSEP_API_KEY=""
# MVSEP API token for cloud-based stem separation
# When set, the service uses MVSEP as the primary backend,
# falling back to local audio-separator after 3 failures per job.
# Get a token from: https://mvsep.com/
# Leave empty to use local audio-separator only.

SOW_MVSEP_ENABLED=true
# Enable/disable MVSEP (default: true). Set to false to force local-only.

SOW_MVSEP_VOCAL_MODEL=81
# MVSEP vocal separation model (sep_type=40, BS Roformer)
# 81 = BS Roformer 2025.07, SDR 11.89 (default)
# 29 = BS Roformer 2024.08, SDR 11.24

SOW_MVSEP_DEREVERB_MODEL=0
# MVSEP reverb removal model (sep_type=22)
# 0 = FoxJoy MDX23C (default)

SOW_MVSEP_TIMEOUT=900
# Max seconds per MVSEP stage (default: 900 = 15 minutes)
```

### Step 8: Tests

**`tests/test_mvsep_client.py`** — Unit tests for `MvsepClient` with mocked httpx:
1. `test_submit_success` — returns job hash
2. `test_submit_api_error` — raises `MvsepClientError`
3. `test_poll_done` — returns data on "done" status
4. `test_poll_timeout` — raises `MvsepTimeoutError`
5. `test_poll_failed_status` — raises `MvsepClientError`
6. `test_is_available_with_key` / `test_is_available_without_key`

**`tests/test_mvsep_fallback.py`** — Integration tests for retry-with-fallback (follow `test_qwen3_fallback.py` pattern):
1. `test_mvsep_succeeds_first_try` — local never called
2. `test_mvsep_fails_then_succeeds` — 2 MVSEP calls, no local
3. `test_mvsep_exhausts_retries_falls_back` — 3 MVSEP failures, local called once, `job.stage` was `"fallback_local"`
4. `test_mvsep_not_available_uses_local` — `mvsep_client=None` or `is_available=False`, local called immediately
5. `test_stage_callback_updates` — `job.stage` set to `mvsep_stage1_*`, `mvsep_stage2_*` during processing

## Job Stage Progression

**MVSEP success**:
```
starting -> checking_cache -> downloading -> mvsep_stage1_submitting -> mvsep_stage1_polling ->
mvsep_stage1_downloading -> mvsep_stage2_submitting -> mvsep_stage2_polling ->
mvsep_stage2_downloading -> renaming_outputs -> caching -> uploading -> complete
```

**MVSEP failure -> local fallback**:
```
starting -> checking_cache -> downloading -> mvsep_stage1_submitting -> [fail x3] ->
fallback_local -> stage1_bs_roformer -> renaming_outputs -> caching -> uploading -> complete
```

**No MVSEP configured** (identical to current behavior):
```
starting -> checking_cache -> downloading -> stage1_bs_roformer -> renaming_outputs ->
caching -> uploading -> complete
```

## Notes

- **No new pip dependencies**: `httpx` already in `pyproject.toml`.
- **No changes to `models.py`**: The MVSEP backend is transparent to API consumers.
- **No changes to `separator_wrapper.py`**: Local wrapper untouched.
- **No changes to `r2.py`**: Same 3 FLAC outputs regardless of backend.
- **Output format**: MVSEP `output_format=2` (FLAC 16-bit) matches local pipeline.
- **MVSEP file naming**: MVSEP returns files like `"vocals (BS Roformer).flac"`. Identify by substring matching (`"vocal"`, `"instrumental"`, `"accompaniment"`, `"no reverb"`, `"noreverb"`), same as POC.

## Verification

1. **Unit tests**: Run `PYTHONPATH=src pytest tests/test_mvsep_client.py tests/test_mvsep_fallback.py -v`
2. **Integration test (MVSEP path)**: Set `SOW_MVSEP_API_KEY` in `.env`, submit a stem separation job via API, verify 3 FLAC files in R2
3. **Integration test (fallback)**: Set `SOW_MVSEP_API_KEY` to an invalid token, submit a job, verify it falls back to local after 3 logged warnings
4. **Integration test (disabled)**: Unset `SOW_MVSEP_API_KEY`, submit a job, verify identical behavior to current (local-only, no MVSEP log messages)
5. **Stage visibility**: Poll job status during processing, verify `job.stage` shows MVSEP-prefixed stages
