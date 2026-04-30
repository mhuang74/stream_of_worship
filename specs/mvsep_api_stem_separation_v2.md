# MVSEP Cloud API for Vocal Stem Separation (v2)

## Context

Vocal stem separation currently runs locally via `AudioSeparatorWrapper` (BS-Roformer + UVR-De-Echo). This is the throughput bottleneck: jobs are serialized (`asyncio.Lock`) because the models require high GPU/CPU memory. By offloading separation to the MVSEP cloud API as the default backend, we free local compute and benefit from MVSEP's beefy cloud GPUs. The local pipeline becomes a fallback after MVSEP failures per job.

A working POC exists at `../stream_of_worship/poc/gen_clean_vocal_stem_mvsep.py` demonstrating the full MVSEP submit/poll/download flow for both stages.

## v2 Changes from v1

1. **Retriable vs non-retriable error classification** — permanent failures (401/403/invalid key) fast-fall to local without burning 3 retries; service-wide MVSEP disable after first non-retriable error
2. **Per-stage retry with cross-backend handoff** — Stage 1 MVSEP success + Stage 2 MVSEP failure → local Stage 2 only (not full re-run)
3. **Three-layer timeout hierarchy** — HTTP (60s), per-stage polling (300s), per-song total (900s)
4. **httpx client lifecycle** — `aclose()` on shutdown
5. **Daily MVSEP cost cap** — configurable daily job limit with UTC-day rollover
6. **`separator_wrapper.py` modification** — extract `remove_reverb()` public method for cross-backend handoff

## Design Decisions

- **Both stages via MVSEP**: Stage 1 (BS Roformer, sep_type=40) + Stage 2 (Reverb Removal, sep_type=22)
- **Per-stage independent retry**: Each stage retries up to 3 times independently; cross-backend handoff on partial failure
- **Retriable errors**: timeout, 5xx, rate-limit, network errors → retry
- **Non-retriable errors**: 401, 403, invalid key, insufficient credits → fall back immediately, disable MVSEP service-wide
- **Concurrency stays at 1**: `asyncio.Lock` serialization unchanged
- **Analysis service only**: Admin CLI (`sow-admin audio vocal`) unchanged

## Files to Modify

| File | Action |
|------|--------|
| `services/analysis/src/sow_analysis/config.py` | Add MVSEP settings (timeout hierarchy + daily cap) |
| `services/analysis/src/sow_analysis/services/mvsep_client.py` | **New file** — async MVSEP client |
| `services/analysis/src/sow_analysis/services/__init__.py` | Export new client + exceptions |
| `services/analysis/src/sow_analysis/workers/stem_separation.py` | Add per-stage retry-with-fallback orchestration |
| `services/analysis/src/sow_analysis/workers/separator_wrapper.py` | Extract `remove_reverb()` public method |
| `services/analysis/src/sow_analysis/workers/queue.py` | Wire `mvsep_client` through to worker |
| `services/analysis/src/sow_analysis/main.py` | Initialize MVSEP client at startup, cleanup on shutdown |
| `services/analysis/.env.example` | Document new env vars |
| `services/analysis/tests/test_mvsep_client.py` | **New file** — unit tests |
| `services/analysis/tests/test_mvsep_fallback.py` | **New file** — fallback logic tests |

## Timeout Hierarchy

| Timeout | Setting | Default | Scope |
|---|---|---|---|
| **HTTP request** | `SOW_MVSEP_HTTP_TIMEOUT` | 60s | Single `httpx` call (POST submit, GET poll, streaming download) |
| **Per-stage polling** | `SOW_MVSEP_STAGE_TIMEOUT` | 300s | Submit + poll loop for one stage (Stage 1 or Stage 2) |
| **Per-song total** | `SOW_MVSEP_TOTAL_TIMEOUT` | 900s | Ceiling across both stages + all retries in `_separate_with_mvsep_fallback` |

All `httpx.AsyncClient` calls use `timeout=SOW_MVSEP_HTTP_TIMEOUT`. The `_poll_job` loop is bounded by `SOW_MVSEP_STAGE_TIMEOUT`. The top-level retry orchestrator in `_separate_with_mvsep_fallback` tracks cumulative time and falls back if `SOW_MVSEP_TOTAL_TIMEOUT` is exceeded.

## Implementation Steps

### Step 1: Add MVSEP settings to `config.py`

Add after the existing stem separation block (line 45):

```python
# MVSEP Cloud API
SOW_MVSEP_API_KEY: str = ""
SOW_MVSEP_ENABLED: bool = True
SOW_MVSEP_VOCAL_MODEL: int = 81       # sep_type=40 add_opt1 (BS Roformer 2025.07)
SOW_MVSEP_DEREVERB_MODEL: int = 0     # sep_type=22 add_opt1 (FoxJoy MDX23C)
SOW_MVSEP_HTTP_TIMEOUT: int = 60      # seconds per HTTP request
SOW_MVSEP_STAGE_TIMEOUT: int = 300    # max seconds per stage (submit+poll)
SOW_MVSEP_TOTAL_TIMEOUT: int = 900    # max seconds for entire MVSEP attempt per song
SOW_MVSEP_DAILY_JOB_LIMIT: int = 50   # max MVSEP jobs per UTC day (cost cap)
```

Effective behavior: MVSEP is "enabled" only when `SOW_MVSEP_ENABLED=True` AND `SOW_MVSEP_API_KEY` is non-empty AND daily job limit is not exceeded. Existing deployments without the key get zero behavioral change.

### Step 2: Create `MvsepClient` (new file)

**File**: `services/analysis/src/sow_analysis/services/mvsep_client.py`

Async client using `httpx.AsyncClient`, following the `Qwen3Client` pattern in `services/qwen3_client.py`.

**Class**: `MvsepClient`
- Constructor takes `api_token`, `enabled`, `vocal_model`, `dereverb_model`, `http_timeout`, `stage_timeout`, `daily_job_limit` — all defaulting from `settings` if not provided.
- `is_available` property: `True` when `enabled`, `api_token` is non-empty, `_disabled` is `False`, and daily job limit is not exceeded.
- `_disabled: bool = False` — set to `True` after first non-retriable error; disables MVSEP for the rest of the service lifetime.

**Exception classes**:
- `MvsepClientError` (base)
- `MvsepNonRetriableError(MvsepClientError)` — 401/403/invalid key/insufficient credits
- `MvsepTimeoutError(MvsepClientError)` — polling timeout

**Daily cost tracking**:
```python
_daily_job_count: int = 0
_daily_reset_utc: datetime = <start of current UTC day>

def _check_daily_limit(self) -> bool:
    """Return True if under daily job limit. Reset counter on new UTC day."""
    now_utc = datetime.now(timezone.utc)
    today_start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
    if self._daily_reset_utc < today_start:
        self._daily_job_count = 0
        self._daily_reset_utc = today_start
    return self._daily_job_count < self.daily_job_limit

def _increment_daily_count(self) -> None:
    self._daily_job_count += 1
```

**Private methods** (ported from POC, made async):

```python
async def _submit_job(audio_path, sep_type, add_opt1, add_opt2=None, output_format=2) -> str:
    """POST /create with multipart form data. Returns job hash."""
    # httpx.AsyncClient, timeout=self.http_timeout
    # On HTTP 401/403: raise MvsepNonRetriableError (with self._disabled = True)
    # On httpx.HTTPStatusError (other): raise MvsepClientError
    # On httpx.TimeoutException: raise MvsepClientError (retriable)
    # On httpx.RequestError: raise MvsepClientError (retriable)
    # On API error response (error in JSON body): raise MvsepClientError

async def _poll_job(job_hash) -> dict:
    """GET /get?hash=... with exponential backoff. Returns data dict when done."""
    # asyncio.sleep (not time.sleep) — 5s initial, 1.5x factor, 30s max
    # Raises MvsepTimeoutError after self.stage_timeout seconds
    # On terminal status (failed, not_found, error): raise MvsepNonRetriableError
    #   (MVSEP declared the job failed — retrying won't help)

async def _download_files(file_entries, output_dir) -> list[Path]:
    """Download result files via streaming httpx. Returns paths."""
    # 65536 byte chunks, timeout=self.http_timeout per request
    # filename from entry["name"] or URL
```

**Public methods**:

```python
async def separate_vocals(input_path, output_dir, stage_callback=None) -> (vocals, instrumental):
    """Stage 1: BS Roformer (sep_type=40, add_opt1=self.vocal_model)."""
    # Increments daily job count
    # Invokes stage_callback("mvsep_stage1_submitting"), etc.
    # Identifies vocals/instrumental by filename matching ("vocal" / "instrumental" / "accompaniment")

async def remove_reverb(vocals_path, output_dir, stage_callback=None) -> (dry_vocals, reverb):
    """Stage 2: Reverb Removal (sep_type=22, add_opt1=self.dereverb_model, add_opt2=1)."""
    # Does NOT increment daily job count (counted in separate_stems)
    # Identifies dry vocals by "no reverb" / "noreverb" / "no_echo" / "no echo" / "dry" in filename

async def separate_stems(input_path, output_dir, stage_callback=None) -> (vocals_clean, vocals_reverb, instrumental):
    """Full two-stage pipeline. Same return signature as AudioSeparatorWrapper.separate_stems()."""
    # vocals_reverb = Stage 1 vocals (before de-reverb)
```

**Error handling** (refined from v1):
- `httpx.HTTPStatusError` with status 401/403 → `MvsepNonRetriableError` + set `self._disabled = True`
- `httpx.HTTPStatusError` (other 4xx/5xx) → `MvsepClientError` (retriable)
- `httpx.TimeoutException` → `MvsepClientError` (retriable)
- `httpx.RequestError` → `MvsepClientError` (retriable)

**Lifecycle**:
```python
async def aclose(self) -> None:
    """Close httpx.AsyncClient connection pool."""
    await self._client.aclose()
```

### Step 3: Update `services/__init__.py`

Add exports for `MvsepClient`, `MvsepClientError`, `MvsepNonRetriableError`, `MvsepTimeoutError`.

### Step 4: Extract `remove_reverb()` from `separator_wrapper.py`

Add a public method to `AudioSeparatorWrapper` that runs only Stage 2 (de-reverb):

```python
async def remove_reverb(
    self,
    vocals_path: Path,
    output_dir: Path,
) -> Tuple[Optional[Path], Optional[Path]]:
    """Run Stage 2 only: remove echo/reverb from vocals using UVR-De-Echo.

    Used as a local fallback when MVSEP Stage 1 succeeds but Stage 2 fails,
    avoiding re-running Stage 1 locally.

    Args:
        vocals_path: Path to vocals file (Stage 1 output)
        output_dir: Directory for output files

    Returns:
        Tuple of (dry_vocals_path, reverb_path).
        Either element may be None if the stage failed to produce output.

    Raises:
        RuntimeError: If models are not ready
    """
    if not self._ready:
        raise RuntimeError("Models not ready. Call initialize() first.")

    loop = asyncio.get_running_loop()
    output_dir.mkdir(parents=True, exist_ok=True)

    def _run_stage2():
        from audio_separator.separator import Separator

        sep = Separator(
            output_dir=str(output_dir),
            model_file_dir=str(self.model_dir),
            output_format=self.output_format,
        )
        sep.load_model(model_filename=self.dereverb_model)
        return sep.separate(str(vocals_path))

    stage2_outputs = await loop.run_in_executor(None, _run_stage2)

    dry_vocals_file: Optional[Path] = None
    reverb_file: Optional[Path] = None
    for output_file in stage2_outputs:
        output_path = Path(output_file)
        if not output_path.is_absolute():
            output_path = output_dir / output_path

        name_lower = output_path.name.lower()
        if "no echo" in name_lower or "dry" in name_lower or "no_echo" in name_lower:
            dry_vocals_file = output_path
        elif "reverb" in name_lower or "echo" in name_lower:
            reverb_file = output_path

    if not dry_vocals_file and stage2_outputs:
        dry_vocals_file = Path(stage2_outputs[0])
        if not dry_vocals_file.is_absolute():
            dry_vocals_file = output_dir / dry_vocals_file

    return dry_vocals_file, reverb_file
```

Also refactor `separate_stems()` to call `remove_reverb()` internally for Stage 2, reducing duplication:

```python
# In separate_stems(), replace Stage 2 inline code (lines 157-193) with:
stage2_dir = output_dir / "stage2"
dry_vocals_file, _ = await self.remove_reverb(vocals_file, stage2_dir)
```

### Step 5: Add per-stage retry-with-fallback to `stem_separation.py`

**New helper** (add before `process_stem_separation`):

```python
import time

MVSEP_MAX_RETRIES = 3

async def _separate_with_mvsep_fallback(
    input_path: Path,
    output_dir: Path,
    job: Job,
    mvsep_client: Optional["MvsepClient"],
    separator_wrapper: AudioSeparatorWrapper,
) -> Tuple[Optional[Path], Optional[Path], Optional[Path]]:
    """Try MVSEP per-stage with cross-backend handoff; fall back to local on failure."""
```

**Logic** (per-stage independent retry with cross-backend handoff):

```
total_start = time.monotonic()

if mvsep_client not available:
    return separator_wrapper.separate_stems(input_path, output_dir)

# --- Stage 1: Vocal separation ---
stage1_result = None
for attempt in 1..MVSEP_MAX_RETRIES:
    if total_elapsed > SOW_MVSEP_TOTAL_TIMEOUT: break
    try:
        vocals, instrumental = mvsep_client.separate_vocals(input_path, stage1_dir, callback)
        stage1_result = (vocals, instrumental)
        break
    except MvsepNonRetriableError:
        log permanent failure, break (0 retries on non-retriable)
    except MvsepClientError:
        log warning, continue

if stage1_result is None:
    # Stage 1 MVSEP failed — fall back to full local pipeline
    job.stage = "fallback_local"
    return separator_wrapper.separate_stems(input_path, output_dir)

vocals, instrumental = stage1_result

# --- Stage 2: De-reverb ---
stage2_result = None
for attempt in 1..MVSEP_MAX_RETRIES:
    if total_elapsed > SOW_MVSEP_TOTAL_TIMEOUT: break
    try:
        dry_vocals, reverb = mvsep_client.remove_reverb(vocals, stage2_dir, callback)
        stage2_result = (dry_vocals, reverb)
        break
    except MvsepNonRetriableError:
        log permanent failure, break
    except MvsepClientError:
        log warning, continue

if stage2_result is None:
    # Stage 2 MVSEP failed — local Stage 2 only (cross-backend handoff)
    job.stage = "fallback_local_stage2"
    dry_vocals, _ = await separator_wrapper.remove_reverb(vocals, stage2_dir)
    stage2_result = (dry_vocals, None)

dry_vocals, _ = stage2_result
return (dry_vocals, vocals, instrumental)
```

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

### Step 6: Wire `mvsep_client` through `queue.py`

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

### Step 7: Initialize MVSEP client in `main.py`

In the `lifespan` function, after R2 initialization (line 83) and before `set_job_queue` (line 86):

```python
from .services.mvsep_client import MvsepClient

mvsep_client = None
if settings.SOW_MVSEP_API_KEY and settings.SOW_MVSEP_ENABLED:
    mvsep_client = MvsepClient()
    job_queue.set_mvsep_client(mvsep_client)
    logger.info("MVSEP client initialized (cloud stem separation enabled)")
else:
    logger.info("MVSEP not configured (using local audio-separator only)")
```

No background task needed — `MvsepClient` is stateless (no models to load).

**Shutdown cleanup** — add before `job_queue.stop()` in the shutdown block:

```python
if mvsep_client is not None:
    await mvsep_client.aclose()
    logger.info("MVSEP client closed")
```

Store `mvsep_client` as a local variable in the `lifespan` function (not global) so it's accessible in both startup and shutdown blocks.

### Step 8: Update `.env.example`

Add after the "Stem Separation Model Selection" section (after line 149):

```
# ========================================
# MVSEP Cloud API Configuration (Optional)
# ========================================

SOW_MVSEP_API_KEY=""
# MVSEP API token for cloud-based stem separation
# When set, the service uses MVSEP as the primary backend,
# falling back to local audio-separator after failures per job.
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

SOW_MVSEP_HTTP_TIMEOUT=60
# Seconds per HTTP request to MVSEP API (default: 60)

SOW_MVSEP_STAGE_TIMEOUT=300
# Max seconds per MVSEP stage — submit + polling (default: 300 = 5 minutes)

SOW_MVSEP_TOTAL_TIMEOUT=900
# Max seconds for entire MVSEP attempt per song, across both stages
# and all retries (default: 900 = 15 minutes)

SOW_MVSEP_DAILY_JOB_LIMIT=50
# Max MVSEP jobs per UTC day — cost cap (default: 50)
# When exceeded, MVSEP is skipped and local audio-separator is used
# until the next UTC day.
```

### Step 9: Tests

**`tests/test_mvsep_client.py`** — Unit tests for `MvsepClient` with mocked httpx:
1. `test_submit_success` — returns job hash
2. `test_submit_api_error` — raises `MvsepClientError`
3. `test_submit_401_raises_non_retriable` — raises `MvsepNonRetriableError`, sets `_disabled=True`
4. `test_submit_403_raises_non_retriable` — raises `MvsepNonRetriableError`, sets `_disabled=True`
5. `test_is_available_disabled_after_non_retriable` — `is_available` returns `False` after `_disabled=True`
6. `test_poll_done` — returns data on "done" status
7. `test_poll_timeout` — raises `MvsepTimeoutError`
8. `test_poll_failed_status` — raises `MvsepNonRetriableError` (MVSEP declared job failed)
9. `test_is_available_with_key` / `test_is_available_without_key`
10. `test_is_available_daily_limit_exceeded` — `is_available` returns `False` when daily count >= limit
11. `test_daily_limit_resets_on_new_utc_day` — count resets at UTC midnight
12. `test_aclose_closes_httpx_client` — `aclose()` calls `self._client.aclose()`

**`tests/test_mvsep_fallback.py`** — Integration tests for per-stage retry-with-fallback:
1. `test_mvsep_both_stages_succeed` — local never called
2. `test_mvsep_stage1_fails_retries_then_succeeds` — 2 Stage 1 MVSEP calls, local never called
3. `test_mvsep_stage1_exhausts_retries_falls_back_full_local` — 3 Stage 1 MVSEP failures, `separator_wrapper.separate_stems()` called, `job.stage = "fallback_local"`
4. `test_mvsep_stage1_succeeds_stage2_fails_handoff` — Stage 1 MVSEP succeeds, Stage 2 MVSEP fails 3x, `separator_wrapper.remove_reverb()` called with MVSEP Stage 1 vocals, `job.stage = "fallback_local_stage2"`
5. `test_mvsep_non_retriable_fast_fallback` — 401 on Stage 1 → immediate local fallback, no retries
6. `test_mvsep_non_retriable_disables_future_jobs` — after first 401, `is_available` returns `False` for subsequent jobs
7. `test_mvsep_not_available_uses_local` — `mvsep_client=None` or `is_available=False`, local called immediately
8. `test_total_timeout_exceeded_falls_back` — cumulative MVSEP time > `SOW_MVSEP_TOTAL_TIMEOUT`, falls back mid-retry
9. `test_stage_callback_updates` — `job.stage` set to `mvsep_stage1_*`, `mvsep_stage2_*` during processing
10. `test_daily_limit_hit_uses_local` — daily job count at limit, local called immediately

## Job Stage Progression

**MVSEP success (both stages)**:
```
starting -> checking_cache -> downloading -> mvsep_stage1_submitting -> mvsep_stage1_polling ->
mvsep_stage1_downloading -> mvsep_stage2_submitting -> mvsep_stage2_polling ->
mvsep_stage2_downloading -> renaming_outputs -> caching -> uploading -> complete
```

**MVSEP Stage 1 failure -> full local fallback**:
```
starting -> checking_cache -> downloading -> mvsep_stage1_submitting -> [fail x3 or non-retriable] ->
fallback_local -> stage1_bs_roformer -> [stage2 via remove_reverb] -> renaming_outputs ->
caching -> uploading -> complete
```

**MVSEP Stage 1 success, Stage 2 failure -> local Stage 2 only**:
```
starting -> checking_cache -> downloading -> mvsep_stage1_submitting -> mvsep_stage1_polling ->
mvsep_stage1_downloading -> mvsep_stage2_submitting -> [fail x3 or non-retriable] ->
fallback_local_stage2 -> [stage2 via separator_wrapper.remove_reverb] -> renaming_outputs ->
caching -> uploading -> complete
```

**No MVSEP configured** (identical to current behavior):
```
starting -> checking_cache -> downloading -> stage1_bs_roformer -> renaming_outputs ->
caching -> uploading -> complete
```

**MVSEP daily limit exceeded** (identical to no MVSEP configured):
```
starting -> checking_cache -> downloading -> stage1_bs_roformer -> renaming_outputs ->
caching -> uploading -> complete
```

## Notes

- **No new pip dependencies**: `httpx` already in `pyproject.toml`.
- **No changes to `models.py`**: The MVSEP backend is transparent to API consumers.
- **No changes to `r2.py`**: Same 3 FLAC outputs regardless of backend.
- **Output format**: MVSEP `output_format=2` (FLAC 16-bit) matches local pipeline.
- **MVSEP file naming**: MVSEP returns files like `"vocals (BS Roformer).flac"`. Identify by substring matching (`"vocal"`, `"instrumental"`, `"accompaniment"`, `"no reverb"`, `"noreverb"`), same as POC.
- **Cross-backend handoff**: When Stage 1 MVSEP succeeds but Stage 2 fails, MVSEP's Stage 1 FLAC output is passed directly to `separator_wrapper.remove_reverb()`. The FLAC format is compatible between backends.

## Verification

1. **Unit tests**: Run `PYTHONPATH=src pytest tests/test_mvsep_client.py tests/test_mvsep_fallback.py -v`
2. **Integration test (MVSEP path)**: Set `SOW_MVSEP_API_KEY` in `.env`, submit a stem separation job via API, verify 3 FLAC files in R2
3. **Integration test (partial fallback)**: Use a valid API key but configure an invalid `SOW_MVSEP_DEREVERB_MODEL`, submit a job, verify Stage 1 MVSEP succeeds and Stage 2 falls back to local
4. **Integration test (full fallback)**: Set `SOW_MVSEP_API_KEY` to an invalid token, submit a job, verify it falls back to full local after 1 logged non-retriable error (not 3 retries)
5. **Integration test (disabled)**: Unset `SOW_MVSEP_API_KEY`, submit a job, verify identical behavior to current (local-only, no MVSEP log messages)
6. **Integration test (daily cap)**: Set `SOW_MVSEP_DAILY_JOB_LIMIT=1`, submit 2 jobs, verify second job uses local
7. **Stage visibility**: Poll job status during processing, verify `job.stage` shows MVSEP-prefixed stages
