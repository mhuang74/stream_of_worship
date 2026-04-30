# MVSEP Cloud API for Vocal Stem Separation (v2) - Implementation Summary

**Date:** 2026-04-30
**Spec:** `specs/mvsep_api_stem_separation_v2.md`

## Overview

Implemented MVSEP Cloud API integration as the default backend for vocal stem separation in the Analysis Service. This offloads separation to cloud GPUs while keeping the local pipeline as a fallback. The implementation includes per-stage retry with cross-backend handoff, non-retriable error classification, three-layer timeout hierarchy, and daily cost capping.

---

## Files Modified/Created

| File | Action | Description |
|------|--------|-------------|
| `services/analysis/src/sow_analysis/config.py` | Modified | Added MVSEP settings (9 new config values) |
| `services/analysis/src/sow_analysis/services/mvsep_client.py` | **New** | Async MVSEP HTTP client with full lifecycle |
| `services/analysis/src/sow_analysis/services/__init__.py` | Modified | Export MVSEP client + exceptions |
| `services/analysis/src/sow_analysis/workers/separator_wrapper.py` | Modified | Extracted `remove_reverb()` public method |
| `services/analysis/src/sow_analysis/workers/stem_separation.py` | Modified | Added per-stage retry-with-fallback orchestration |
| `services/analysis/src/sow_analysis/workers/queue.py` | Modified | Wired `mvsep_client` through to worker |
| `services/analysis/src/sow_analysis/main.py` | Modified | Initialize MVSEP client at startup, cleanup on shutdown |
| `services/analysis/.env.example` | Modified | Documented new env vars with descriptions |
| `services/analysis/tests/test_mvsep_client.py` | **New** | Unit tests for MvsepClient (14 tests) |
| `services/analysis/tests/test_mvsep_fallback.py` | **New** | Integration tests for fallback logic (10+ tests) |

---

## Implementation Details

### 1. Configuration (`config.py`)

Added 9 new configuration settings after the stem separation block:

```python
# MVSEP Cloud API
SOW_MVSEP_API_KEY: str = ""                    # API token
SOW_MVSEP_ENABLED: bool = True                # Enable/disable
SOW_MVSEP_VOCAL_MODEL: int = 81               # sep_type=40 add_opt1 (BS Roformer 2025.07)
SOW_MVSEP_DEREVERB_MODEL: int = 0             # sep_type=22 add_opt1 (FoxJoy MDX23C)
SOW_MVSEP_HTTP_TIMEOUT: int = 60              # seconds per HTTP request
SOW_MVSEP_STAGE_TIMEOUT: int = 300            # max seconds per stage (submit+poll)
SOW_MVSEP_TOTAL_TIMEOUT: int = 900          # max seconds for entire MVSEP attempt
SOW_MVSEP_DAILY_JOB_LIMIT: int = 50          # max jobs per UTC day (cost cap)
```

**Behavior:** MVSEP is "enabled" only when `SOW_MVSEP_ENABLED=True` AND `SOW_MVSEP_API_KEY` is non-empty AND daily job limit is not exceeded.

### 2. MVSEP Client (`mvsep_client.py`)

**Class:** `MvsepClient`
- Constructor takes all config values with defaults from settings
- `is_available` property: True when enabled, key present, not disabled, and under daily limit
- `_disabled: bool = False` — set True on first non-retriable error

**Daily Cost Tracking:**
```python
_daily_job_count: int = 0
_daily_reset_utc: datetime = <start of UTC day>

_check_daily_limit()  # Resets counter on new UTC day
_increment_daily_count()  # Called per Stage 1 submission
```

**Exception Classes:**
- `MvsepClientError` — base exception
- `MvsepNonRetriableError(MvsepClientError)` — 401/403/invalid key/insufficient credits
- `MvsepTimeoutError(MvsepClientError)` — polling timeout

**Key Methods:**
- `_submit_job()` — POST /create with multipart form, returns job hash
- `_poll_job()` — GET /get with exponential backoff (1.5x, max 30s)
- `_download_files()` — Streaming download with 64KB chunks
- `separate_vocals()` — Stage 1 (BS Roformer, sep_type=40, add_opt1=81)
- `remove_reverb()` — Stage 2 (Reverb Removal, sep_type=22, add_opt2=1)
- `separate_stems()` — Full two-stage pipeline
- `aclose()` — Close httpx.AsyncClient connection pool

**Error Handling:**
- 401/403 → `MvsepNonRetriableError` + `self._disabled = True`
- Other 4xx/5xx → `MvsepClientError` (retriable)
- TimeoutException → `MvsepClientError` (retriable)
- RequestError → `MvsepClientError` (retriable)
- Terminal status (failed/not_found/error) → `MvsepNonRetriableError`

### 3. Separator Wrapper (`separator_wrapper.py`)

**New Method:** `remove_reverb(vocals_path, output_dir)`
- Runs only Stage 2 using UVR-De-Echo
- Used for cross-backend handoff when MVSEP Stage 1 succeeds but Stage 2 fails
- Returns `(dry_vocals_path, reverb_path)`

**Refactoring:**
- `separate_stems()` now calls `remove_reverb()` internally for Stage 2
- Eliminates code duplication

### 4. Stem Separation Worker (`stem_separation.py`)

**New Helper:** `_separate_with_mvsep_fallback()`

**Logic Flow:**
```
if mvsep_client not available:
    return separator_wrapper.separate_stems(input_path, output_dir)

# --- Stage 1: Vocal separation (up to 3 retries) ---
for attempt in 1..3:
    if total_elapsed > TOTAL_TIMEOUT: break
    try:
        vocals, instrumental = mvsep_client.separate_vocals(...)
        break
    except MvsepNonRetriableError:
        break  # 0 retries on non-retriable
    except MvsepClientError:
        continue  # retry

if stage1_result is None:
    job.stage = "fallback_local"
    return separator_wrapper.separate_stems(...)  # Full local fallback

# --- Stage 2: De-reverb (up to 3 retries) ---
for attempt in 1..3:
    if total_elapsed > TOTAL_TIMEOUT: break
    try:
        dry_vocals, reverb = mvsep_client.remove_reverb(vocals, ...)
        break
    except MvsepNonRetriableError:
        break
    except MvsepClientError:
        continue

if stage2_result is None:
    job.stage = "fallback_local_stage2"
    dry_vocals, _ = await separator_wrapper.remove_reverb(vocals, ...)  # Cross-backend handoff

return (dry_vocals, vocals, instrumental)
```

**Job Stage Progressions:**

| Scenario | Stage Progression |
|----------|-------------------|
| MVSEP success (both stages) | `starting → checking_cache → downloading → mvsep_stage1_submitting → mvsep_stage1_polling → mvsep_stage1_downloading → mvsep_stage2_submitting → mvsep_stage2_polling → mvsep_stage2_downloading → renaming_outputs → caching → uploading → complete` |
| MVSEP Stage 1 fail → full local | `... → mvsep_stage1_submitting → [fail x3 or non-retriable] → fallback_local → stage1_bs_roformer → renaming_outputs → ...` |
| MVSEP Stage 1 OK, Stage 2 fail → local Stage 2 | `... → mvsep_stage1_downloading → mvsep_stage2_submitting → [fail x3 or non-retriable] → fallback_local_stage2 → renaming_outputs → ...` |
| No MVSEP configured | `starting → checking_cache → downloading → stage1_bs_roformer → renaming_outputs → ...` |
| Daily limit exceeded | `starting → checking_cache → downloading → stage1_bs_roformer → renaming_outputs → ...` |

### 5. Queue (`queue.py`)

**Changes:**
1. Added `self._mvsep_client: Optional[Any] = None` in `__init__`
2. Added `set_mvsep_client(mvsep_client)` setter method
3. Passes `mvsep_client` to `process_stem_separation()` in `_process_stem_separation_job()`

**Note:** The existing separator-readiness check stays unchanged. Even when MVSEP is primary, local `AudioSeparatorWrapper` must be ready for fallback.

### 6. Main Application (`main.py`)

**Startup (lifespan):**
```python
# After R2 initialization, before set_job_queue:
if settings.SOW_MVSEP_API_KEY and settings.SOW_MVSEP_ENABLED:
    mvsep_client = MvsepClient()
    job_queue.set_mvsep_client(mvsep_client)
    logger.info("MVSEP client initialized (cloud stem separation enabled)")
else:
    logger.info("MVSEP not configured (using local audio-separator only)")
```

**Shutdown:**
```python
if mvsep_client is not None:
    await mvsep_client.aclose()
    logger.info("MVSEP client closed")
```

### 7. Environment Configuration (`.env.example`)

Added comprehensive documentation section:
```bash
# ========================================
# MVSEP Cloud API Configuration (Optional)
# ========================================

SOW_MVSEP_API_KEY=""
# Get a token from: https://mvsep.com/

SOW_MVSEP_ENABLED=true
# Enable/disable MVSEP (default: true)

SOW_MVSEP_VOCAL_MODEL=81
# 81 = BS Roformer 2025.07 (default)
# 29 = BS Roformer 2024.08

SOW_MVSEP_DEREVERB_MODEL=0
# 0 = FoxJoy MDX23C (default)

SOW_MVSEP_HTTP_TIMEOUT=60
SOW_MVSEP_STAGE_TIMEOUT=300
SOW_MVSEP_TOTAL_TIMEOUT=900
SOW_MVSEP_DAILY_JOB_LIMIT=50
```

---

## Testing

### Unit Tests (`test_mvsep_client.py`)

| Test | Description |
|------|-------------|
| `test_submit_success` | Returns job hash on success |
| `test_submit_api_error` | Raises `MvsepClientError` on API error |
| `test_submit_401_raises_non_retriable` | Raises `MvsepNonRetriableError`, sets `_disabled=True` |
| `test_submit_403_raises_non_retriable` | Raises `MvsepNonRetriableError`, sets `_disabled=True` |
| `test_is_available_disabled_after_non_retriable` | `is_available` returns False after disable |
| `test_poll_done` | Returns data on "done" status |
| `test_poll_timeout` | Raises `MvsepTimeoutError` |
| `test_poll_failed_status` | Raises `MvsepNonRetriableError` on terminal failure |
| `test_is_available_with_key` / `test_is_available_without_key` | Availability checks |
| `test_is_available_daily_limit_exceeded` | Limit enforcement |
| `test_daily_limit_resets_on_new_utc_day` | UTC midnight rollover |
| `test_aclose_closes_httpx_client` | Cleanup verification |
| `test_submit_invalid_key_error` / `test_submit_insufficient_credits_error` | Response body error handling |
| `test_submit_timeout_error` / `test_submit_request_error` | Network error handling |

### Integration Tests (`test_mvsep_fallback.py`)

| Test | Description |
|------|-------------|
| `test_mvsep_both_stages_succeed` | Local never called |
| `test_mvsep_stage1_fails_retries_then_succeeds` | 2 Stage 1 MVSEP calls, local never called |
| `test_mvsep_stage1_exhausts_retries_falls_back_full_local` | 3 Stage 1 failures, full local fallback, `job.stage = "fallback_local"` |
| `test_mvsep_stage1_succeeds_stage2_fails_handoff` | Cross-backend handoff: MVSEP Stage 1 + local Stage 2, `job.stage = "fallback_local_stage2"` |
| `test_mvsep_non_retriable_fast_fallback` | 401 on Stage 1 → immediate local fallback, no retries |
| `test_mvsep_non_retriable_disables_future_jobs` | After first 401, `is_available=False` for subsequent jobs |
| `test_mvsep_not_available_uses_local` | `mvsep_client=None` or `is_available=False`, local called immediately |
| `test_total_timeout_exceeded_falls_back` | Cumulative time > `SOW_MVSEP_TOTAL_TIMEOUT`, falls back mid-retry |
| `test_stage_callback_updates` | `job.stage` shows MVSEP-prefixed stages during processing |
| `test_daily_limit_hit_uses_local` | Daily job count at limit, local called immediately |

---

## Design Decisions

1. **Both stages via MVSEP:** Stage 1 (BS Roformer, sep_type=40) + Stage 2 (Reverb Removal, sep_type=22)
2. **Per-stage independent retry:** Each stage retries up to 3 times independently
3. **Cross-backend handoff:** When Stage 1 MVSEP succeeds but Stage 2 fails, MVSEP's Stage 1 FLAC output is passed directly to `separator_wrapper.remove_reverb()`
4. **Retriable errors:** timeout, 5xx, rate-limit, network errors → retry
5. **Non-retriable errors:** 401, 403, invalid key, insufficient credits → fall back immediately, disable MVSEP service-wide
6. **Concurrency stays at 1:** `asyncio.Lock` serialization unchanged
7. **Analysis service only:** Admin CLI (`sow-admin audio vocal`) unchanged

---

## Migration Notes

- **No new pip dependencies:** `httpx` already in `pyproject.toml`
- **No changes to `models.py`:** MVSEP backend is transparent to API consumers
- **No changes to `r2.py`:** Same 3 FLAC outputs regardless of backend
- **Backward compatible:** Existing deployments without `SOW_MVSEP_API_KEY` get zero behavioral change
- **Output format:** MVSEP `output_format=2` (FLAC 16-bit) matches local pipeline
- **File naming:** Uses substring matching (`"vocal"`, `"instrumental"`, `"no reverb"`, etc.) same as POC

---

## Verification Commands

```bash
# Unit tests
PYTHONPATH=src pytest tests/test_mvsep_client.py -v

# Integration tests
PYTHONPATH=src pytest tests/test_mvsep_fallback.py -v

# Integration test (MVSEP path)
# Set SOW_MVSEP_API_KEY in .env, submit a stem separation job via API

# Integration test (partial fallback)
# Use valid API key but invalid SOW_MVSEP_DEREVERB_MODEL

# Integration test (full fallback)
# Set SOW_MVSEP_API_KEY to invalid token

# Integration test (daily cap)
# Set SOW_MVSEP_DAILY_JOB_LIMIT=1, submit 2 jobs
```
