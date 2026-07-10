# MVSEP Queue-Full: Mutex Serialization + Exponential Backoff

## Context

Concurrent STEM_SEPARATION jobs submit to MVSEP simultaneously, but MVSEP's API only
allows **one pending file per API token**. The error:

```
MVSEP queue full: {"success":false,"errors":["You already have unprocessed file in queue. Please wait before adding new file!"]}
```

is produced by N-1 of N concurrent jobs. Two compounding problems make this worse:

1. **No MVSEP concurrency control** — `queue.py:397-400` launches STEM_SEPARATION jobs with
   no semaphore. All jobs call `mvsep_client.separate_vocals()` concurrently. Only one can
   succeed; the rest get queue-full.

2. **Daily quota burned by failed submits** — `_increment_daily_count()` is called at the top
   of `separate_vocals()` (`mvsep_client.py:350`), **before** `_submit_job()`. Every queue-full
   failure consumes a daily quota unit. After 50 failures (`SOW_MVSEP_DAILY_JOB_LIMIT=50`),
   `is_available()` returns `False`, and all remaining jobs silently fall back to local
   audio-separator — **without ever trying MVSEP**.

The per-job retry loop with fixed backoff (`[60, 120, 300]`) is correct in isolation but
cannot help when concurrent jobs burn through daily quota on queue-full failures before
the backoff fires.

## Design Decisions

- **MVSEP mutex (`asyncio.Lock`)**: Serialize all MVSEP API calls (submit + poll + download).
  MVSEP only accepts one pending job per token, so the lock eliminates queue-full from our
  own concurrency. Jobs queue on the lock (unbounded wait).
- **Daily count only on successful submit**: Move `_increment_daily_count()` to after
  `_submit_job()` returns a valid job hash. Failed submits no longer consume quota.
- **Exponential backoff with jitter (safety net)**: Even with the mutex, queue-full can
  still occur from external traffic on the same API token or the brief window between
  poll-done and lock-release. Replace fixed backoff lists with exponential formula.
- **Wait indefinitely on mutex**: Jobs queue for MVSEP rather than falling back to local.
  Bounded per-job by `SOW_MVSEP_TOTAL_TIMEOUT=900s` for actual MVSEP processing time.
- **Mutex wraps entire `separate_vocals` / `remove_reverb`**: submit + poll + download
  are all inside the lock. Simplest, matches MVSEP's 1-pending constraint.

## Files to Modify

| File | Action |
|------|--------|
| `ops/analysis-service/src/sow_analysis/services/mvsep_client.py` | Add `asyncio.Lock`; wrap `separate_vocals()` and `remove_reverb()` in mutex; move `_increment_daily_count()` after successful submit |
| `ops/analysis-service/src/sow_analysis/workers/stem_separation.py` | Replace fixed backoff lists with exponential + jitter; bump queue-full max retries 4→6; extract shared backoff helper to deduplicate Stage 1/Stage 2 retry blocks |
| `ops/analysis-service/tests/test_mvsep_client.py` | Add mutex serialization test; existing tests unaffected (lock is transparent for single calls) |
| `ops/analysis-service/tests/test_mvsep_fallback.py` | Update backoff assertions for exponential values; patch `random.uniform` for determinism; rename 4-attempt test to 6-attempt; add jitter test |

## Implementation Steps

### Step 1: Add MVSEP mutex to `mvsep_client.py`

In `MvsepClient.__init__()`, add:

```python
self._mutex = asyncio.Lock()
```

### Step 2: Wrap `separate_vocals()` in mutex; fix daily count

Wrap the submit + poll + download sequence in `async with self._mutex:`. Move
`_increment_daily_count()` from before `_submit_job()` to after it returns a valid
job hash:

```python
async def separate_vocals(self, input_path, output_dir, stage_callback=None):
    async with self._mutex:
        if stage_callback:
            stage_callback("mvsep_stage1_submitting")

        job_hash = await self._submit_job(
            audio_path=input_path,
            sep_type=self.stage1_sep_type,
            add_opt1=self.stage1_add_opt1,
            add_opt2=self.stage1_add_opt2,
            output_format=2,
        )
        self._increment_daily_count()  # only count successful submits

        if stage_callback:
            stage_callback("mvsep_stage1_polling")

        result = await self._poll_job(job_hash)

        if stage_callback:
            stage_callback("mvsep_stage1_downloading")

        file_entries = result.get("data", {}).get("files", [])
        downloaded = await self._download_files(file_entries, output_dir)

        # ... existing file identification logic ...

    return vocals_file, instrumental_file
```

### Step 3: Wrap `remove_reverb()` in mutex

Same pattern — wrap submit + poll + download in `async with self._mutex:`. Stage 2
already does not increment daily count (Stage 1 counts the whole pipeline).

### Step 4: Replace fixed backoff with exponential + jitter in `stem_separation.py`

Replace module-level constants:

```python
import random

MVSEP_MAX_RETRIES = 3                      # unchanged (non-queue-full errors)
MVSEP_QUEUE_FULL_MAX_RETRIES = 6           # was 4

# Queue-full (retriable, long): 30, 60, 120, 240, 300... + ±20% jitter, cap 300s
QUEUE_FULL_BACKOFF_BASE = 30.0
QUEUE_FULL_BACKOFF_FACTOR = 2.0
QUEUE_FULL_BACKOFF_CAP = 300.0
QUEUE_FULL_BACKOFF_JITTER = 0.20

# Other retriable errors: 5, 10, 20... (matches current behavior, now via formula)
OTHER_ERROR_BACKOFF_BASE = 5.0
OTHER_ERROR_BACKOFF_FACTOR = 2.0
OTHER_ERROR_BACKOFF_CAP = 20.0
OTHER_ERROR_BACKOFF_JITTER = 0.20
```

Add shared backoff helper:

```python
def _compute_mvsep_backoff(attempt: int, *, base: float, factor: float,
                           cap: float, jitter: float) -> float:
    """Exponential backoff with ±jitter. attempt is 1-indexed."""
    raw = base * (factor ** (attempt - 1))
    capped = min(raw, cap)
    amp = capped * jitter
    return max(0.0, capped + random.uniform(-amp, amp))
```

### Step 5: Deduplicate Stage 1 / Stage 2 retry loops

Extract the duplicated ~15-line retry blocks (`stem_separation.py:160-193` and
`221-254`) into a shared helper that both stages call. Each loop uses
`_compute_mvsep_backoff()` with the appropriate preset (queue-full vs other-error).

### Step 6: Update tests

#### `test_mvsep_fallback.py`

| Test | Change |
|------|--------|
| `test_queue_full_backoff_timing` (`:451`) | Patch `random.uniform` → `0.0`; assert `[30, 60]` instead of `[60, 120]` |
| `test_other_error_backoff_timing` (`:483`) | Patch `random.uniform` → `0.0`; assert `[5, 10]` (unchanged values via formula) |
| `test_queue_full_4_attempts_before_fallback` (`:514`) | Rename to `_6_attempts`; assert `call_count == 6`, 5 sleeps, values `[30, 60, 120, 240, 300]` |
| **NEW** `test_queue_full_backoff_jitter_applied` | Patch `random.uniform` to return `+amp`; verify sleep > base value |

#### `test_mvsep_client.py`

| Test | Change |
|------|--------|
| `test_separate_vocals_handles_other_type` (`:402`) | No change — mutex transparent for single calls |
| `test_submit_*` tests (`:91-374`) | No change — test `_submit_job` directly, not `separate_vocals` |
| `test_is_available_daily_limit_exceeded` (`:281`) | No change — still valid |
| **NEW** `test_separate_vocals_mutex_serializes` | Two concurrent `separate_vocals` calls; assert second starts after first completes |

## Verification

```bash
cd ops/analysis-service
PYTHONPATH=src pytest tests/test_mvsep_client.py tests/test_mvsep_fallback.py -v
```

## Out of Scope

- No changes to `lrc.py` (no MVSEP logic)
- No changes to `_poll_job` in-band exponential poll backoff (`mvsep_client.py:286`) — already exponential (1.5x, cap 30s)
- No new env vars; backoff constants stay module-level
- No changes to `queue.py` concurrency model (mutex on MvsepClient handles serialization)
