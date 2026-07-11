# Implementation Plan: Free-Only Patient Mode Flag

## Overview

Add a single environment variable flag (`SOW_FREE_ONLY_MODE`) that enables "patient free-API-only mode" for the Analysis Service. When enabled, LRC generation and stem separation jobs wait for free-tier API quota to reset (UTC daily) instead of falling back to local models (Whisper, audio-separator) or failing.

### Current Pipeline (Before Change)

**LRC Generation fallback chain:**
```
1. YouTube transcript → LLM correction → LRC          (free)
2. Qwen3 ASR (DashScope) → LLM alignment → LRC        (paid, cheap)
3. Whisper (local) → LLM alignment → LRC               (local fallback)
```

**Stem Separation fallback chain:**
```
1. MVSEP Cloud API (two-stage: vocal sep + optional reverb removal)
2. Local audio-separator (BS-Roformer + UVR-De-Echo)
```

### Desired Behavior When Flag Enabled

- **Stem Separation**: Use MVSEP Cloud API only. If quota exhausted, do NOT fall back to local audio-separator. Poll MVSEP quota availability, update job progress, stay in processing state. Resume MVSEP when quota available.
- **LRC Pipeline**: When Qwen3 ASR quota exhausted, do NOT fall back to Whisper. Poll DashScope quota availability, update job progress, stay in processing state. Resume Qwen3 ASR when quota available.
- **YouTube transcript path**: Already free, unchanged.
- **Other job types** (LLM chat, embedding, forced alignment): Unaffected.

### Key Design Decisions (Resolved)

| Decision | Choice | Rationale |
|---|---|---|
| Qwen3 quota detection | 429 after retries = quota | Simple, self-healing. No response body parsing. |
| Poller probe strategy | Passive check | Calls existing `is_available` property. Free, leverages UTC-rollover logic. |
| MVSEP time budget | Pause during wait | Don't count quota-waiting against the 1800s timeout. Fair to caller who opted into patience. |
| Poller failure safety | 1s self-check | Each job independently self-checks `is_available` every 1s. Poller is optimization, not hard dependency. |

---

## 1. New Environment Variables

### `SOW_FREE_ONLY_MODE`

| Attribute | Value |
|---|---|
| **Type** | `bool` |
| **Default** | `false` |
| **Description** | When enabled, LRC and stem-separation jobs wait for free-tier API quota to reset (UTC daily) instead of falling back to local models (Whisper, audio-separator) or failing. Scoped to LRC generation (Qwen3 ASR) and stem separation (MVSEP) only. Other job types unaffected. |

### `SOW_QUOTA_POLL_INTERVAL_SECONDS`

| Attribute | Value |
|---|---|
| **Type** | `int` |
| **Default** | `3600` (~1 hour) |
| **Description** | How often the shared QuotaWaiter's poller checks whether free-tier API quota has reset. Both MVSEP and DashScope. Lower values = faster detection after UTC midnight at cost of more checks. Since `wait()` also self-checks every 1s, this primarily controls how aggressively the poller re-evaluates; it is an optimization, not a hard dependency. |

Both variables go in `config.py` `Settings` class alongside existing `SOW_MVSEP_ENABLED`, `SOW_DASHSCOPE_*`, etc. Document in `.env.example`.

---

## 2. Where Changes Belong

| Area | File(s) | Nature of Change | Complexity |
|---|---|---|---|
| **Configuration** | `config.py` | Add 2 fields | Trivial |
| **New QuotaWaiter module** | New file: `workers/quota_waiter.py` | New class — core abstraction | Medium (~100 lines) |
| **MVSEP client** | `services/mvsep_client.py` | Expose `_quota_exhausted` vs `_disabled` distinction (minor property) | Trivial |
| **Qwen3 ASR client** | `services/qwen3_asr_client.py` | Add `_quota_exhausted` flag + UTC reset + new `Qwen3AsrQuotaExhaustedError` exception | Medium |
| **Stem separation worker** | `workers/stem_separation.py` | Add conditional wait at 3 decision points in `_separate_with_mvsep_fallback()` + time-budget pause + `stage_updater` callback parameter on `process_stem_separation()` / `_separate_with_mvsep_fallback()` | Medium |
| **Job queue / LRC pipeline** | `workers/queue.py` | Conditional wait-or-fallback in `except Qwen3AsrError` handler; QuotaWaiter injection via setter | Medium |
| **Application startup** | `main.py` | Create QuotaWaiter instances in lifespan, inject into queue | Low |
| `.env.example` | Document 2 vars | Trivial |

---

## 3. Polling Architecture Design

### Core Abstraction: `QuotaWaiter` (new module: `workers/quota_waiter.py`)

One instance per API type (MVSEP, DashScope). Service-wide singleton shared by all jobs.

### Wait Semantics: 1s Self-Check + Shared Poller Optimization

The `wait()` method does a 1-second-granularity loop. Each tick:
1. Checks the cancellation callback → returns `False` if cancelled
2. **Directly calls `is_available` (the `probe_fn`)** in-process → returns `True` if available
3. Falls through to `asyncio.wait_for(_event.wait(), timeout=1.0)` for the next tick

This means:
- **Every job independently self-checks every 1s.** The poller background task is a shared **optimization** that proactively sets the event on detection — but even if it crashes, each job's own 1s tick will eventually discover availability (after the next UTC midnight, when `is_available` returns True).
- The poller (`SOW_QUOTA_POLL_INTERVAL_SECONDS` = 3600s) primarily benefits the case where `is_available` is expensive (it isn't for MVSEP — it's a flag check). So for MVSEP, the poller mostly just sets the event slightly early. The per-job 1s self-check is the actual reliability mechanism.

### API

```python
class QuotaWaiter:
    def __init__(self, name: str, probe_fn: Callable[[], bool], poll_interval: int): ...

    async def mark_exhausted(self) -> None:
        """Called when a job detects quota exhaustion. Clears event, starts poller."""

    async def wait(self, job, cancel_fn: Callable[[], bool]) -> bool:
        """Block until quota available or job cancelled.
        Self-checks is_available every 1s. Returns True if available, False if cancelled."""

    def _start_poller(self) -> None:
        """Lazily start background poller task."""

    async def _poll_loop(self) -> None:
        """Background task: every poll_interval, call probe_fn. Set event when True."""

    async def stop(self) -> None:
        """Called during shutdown. Cancels poller task."""
```

### Architecture Diagram

```
                    ┌──────────────────────┐
                    │    QuotaWaiter       │
                    │    (MVSEP)           │
                    │                      │
  Stem Job A ──────┤  wait(jobA, cancel)  │
  Stem Job B ──────┤  wait(jobB, cancel)  │
                    │                      │
                    │  _poll_loop ─────────┼──► mvsep_client.is_available
                    │    (every 3600s)     │    (checks _quota_exhausted +
                    │                      │     _check_quota_reset())
                    │                      │
                    │  1s self-check ──────┼──► mvsep_client.is_available
                    │  (per job, in wait())│    (same probe, independent)
                    └──────────────────────┘

                    ┌──────────────────────┐
                    │    QuotaWaiter       │
                    │    (DashScope)       │
                    │                      │
  LRC Job C  ──────┤  wait(jobC, cancel)  │
                    │                      │
                    │  _poll_loop ─────────┼──► qwen3_client.is_available
                    │    (every 3600s)     │    (new: checks _quota_exhausted +
                    │                      │     UTC rollover)
                    │                      │
                    │  1s self-check ──────┼──► qwen3_client.is_available
                    │  (per job, in wait())│    (same probe, independent)
                    └──────────────────────┘
```

### Lifecycle in `main.py` lifespan

In startup (alongside `MvsepClient`, `AudioSeparatorWrapper`):
- Create `mvsep_quota_waiter` with `probe_fn=mvsep_client.is_available` (if MVSEP is configured)
- Create `qwen3_quota_waiter` with `probe_fn=qwen3_client.is_available` (built lazily or always-created)
- Inject both into `job_queue` via new setter method (following existing `set_mvsep_client()` / `set_separator_wrapper()` pattern)

In shutdown:
- `await mvsep_quota_waiter.stop()` / `await qwen3_quota_waiter.stop()`

### Wiring in `queue.py`

New instance attributes on `JobQueue`:
- `self._mvsep_quota_waiter: Optional[QuotaWaiter] = None`
- `self._qwen3_quota_waiter: Optional[QuotaWaiter] = None`

New setter:
```python
def set_quota_waiters(self, mvsep=None, qwen3=None):
    self._mvsep_quota_waiter = mvsep
    self._qwen3_quota_waiter = qwen3
```

---

## 4. State Management for Waiting Jobs

| Aspect | Value |
|---|---|
| **Job status** | `PROCESSING` (no new status) |
| **Stage (MVSEP)** | `"waiting_for_mvsep_quota_reset"` — persisted to DB via `_update_stage()` |
| **Stage (DashScope)** | `"waiting_for_qwen3_asr_quota_reset"` — persisted to DB via `_update_stage()` |
| **Progress** | Kept at current value (don't reset) — MVSEP ~0.3, Qwen3 ASR ~0.4 |
| **Heartbeat** | Refresh `updated_at` on each 1s self-check tick so monitoring doesn't flag as stale |

On resume:
- Set stage back to active stage: `"qwen3_asr_transcribing"` (LRC) or `"mvsep_stage1"` / `"mvsep_stage2"` (stems)

### Stage Transitions

```
LRC (free-only mode, quota hit):
  starting → downloading → resolving_transcription_audio →
  qwen3_asr_transcribing → waiting_for_qwen3_asr_quota_reset →
  [1s self-check passes] → qwen3_asr_transcribing → qwen3_asr_done → uploading → complete

Stem Separation (free-only mode, quota hit at Stage 1):
  starting → mvsep_stage1 → waiting_for_mvsep_quota_reset →
  [1s self-check passes] → mvsep_stage1 → mvsep_stage1_done → mvsep_stage2 → ... → complete
```

### DB Persistence

The `_update_stage()` helper at `queue.py:766` already persists `stage` + `progress` to DB. Use it for the waiting stage transitions. This means the waiting state is **visible via the API** (`GET /api/v1/jobs/{id}`) — callers will see `"waiting_for_mvsep_quota_reset"` in the `stage` field with `"status": "processing"`.

#### Problem: Stem Separation's `_set_job_stage()` Is In-Memory Only

The existing stem-separation code uses `_set_job_stage()` (`stem_separation.py:201-206`), which only mutates the in-memory `Job` dataclass — it does NOT write to the database:

```python
def _set_job_stage(job: Job, stage: str) -> None:
    """Update job stage in memory (does not persist to store)."""
    job.stage = stage
    job.updated_at = datetime.now(timezone.utc)
```

This is acceptable for transient inner-stage transitions like `"fallback_local"` (which are purely informational and last seconds). But the quota-wait stage can persist for **hours**, so it MUST be persisted to DB. Otherwise:
- The API (`GET /api/v1/jobs/{id}`) would show a stale stage from the last DB write
- External monitoring cannot detect the waiting state
- On service restart, recovery resets to `QUEUED` / `stage="requeued"` — but while the service is running, operators need visibility

#### Solution: Pass an Async Stage-Updater Callback Through the Call Chain

The `JobQueue._update_stage()` method (`queue.py:766-782`) is an instance method that writes to `self.job_store`. The stem-separation functions (`process_stem_separation`, `_separate_with_mvsep_fallback`) are module-level functions that don't have access to the queue instance. 

**Approach**: Add an optional `stage_updater` callback parameter to the stem-separation functions, following the existing pattern of passing dependencies through function parameters (as `local_model_semaphore`, `mvsep_client`, etc. are already passed through).

**1. Define the callback type** (in `stem_separation.py` or a shared types module):

```python
from typing import Callable, Awaitable

# Async callback: (stage: str, progress: Optional[float]) -> None
StageUpdater = Callable[[str, "Optional[float]"], "Awaitable[None]"]
```

**2. Add `stage_updater` parameter to `_separate_with_mvsep_fallback()`** (`stem_separation.py:209`):

```python
async def _separate_with_mvsep_fallback(
    input_path: Path,
    output_dir: Path,
    job: Job,
    mvsep_client: Optional["MvsepClient"],
    separator_wrapper: AudioSeparatorWrapper,
    local_model_semaphore: Optional[asyncio.Semaphore] = None,
    stage_updater: Optional[StageUpdater] = None,  # NEW
) -> Tuple[Optional[Path], Optional[Path], Optional[Path]]:
```

**3. Add `stage_updater` parameter to `process_stem_separation()`** (`stem_separation.py:318`) and pass it through to `_separate_with_mvsep_fallback()`:

```python
async def process_stem_separation(
    job: Job,
    separator_wrapper: AudioSeparatorWrapper,
    r2_client: R2Client,
    cache_manager: CacheManager,
    mvsep_client: Optional["MvsepClient"] = None,
    local_model_semaphore: Optional[asyncio.Semaphore] = None,
    stage_updater: Optional[StageUpdater] = None,  # NEW
) -> None:
    ...
    # Pass stage_updater to _separate_with_mvsep_fallback
    result = await _separate_with_mvsep_fallback(
        ..., stage_updater=stage_updater
    )
```

**4. Wire it from `JobQueue._process_stem_separation_job()`** (`queue.py:1675`):

```python
await process_stem_separation(
    job=job,
    separator_wrapper=self._separator_wrapper,
    r2_client=self.r2_client,
    cache_manager=self.cache_manager,
    mvsep_client=self._mvsep_client,
    local_model_semaphore=self._local_model_semaphore,
    stage_updater=self._update_stage,  # NEW: pass the bound method
)
```

`self._update_stage` is already an async method with signature `(job: Job, stage: str, progress: Optional[float] = None) -> None`. However, the callback type is `(stage: str, progress: Optional[float]) -> None` (without `job`), so either:
- **Option A**: Use a lambda/partial to bind `job`: `stage_updater=lambda s, p=None: self._update_stage(job, s, p)`
- **Option B**: Include `job` in the callback signature: `StageUpdater = Callable[[Job, str, Optional[float]], Awaitable[None]]`

**Option A is recommended** — it keeps the stem-separation functions decoupled from the `Job` object's lifecycle and matches how `job` is already passed separately as a parameter. The binding happens at the call site in `_process_stem_separation_job`.

**5. Use `stage_updater` for waiting-stage transitions** in `_separate_with_mvsep_fallback()`:

```python
# When entering quota wait:
if stage_updater:
    await stage_updater("waiting_for_mvsep_quota_reset", None)
else:
    _set_job_stage(job, "waiting_for_mvsep_quota_reset")  # fallback to in-memory

# When resuming after wait:
if stage_updater:
    await stage_updater("mvsep_stage1", None)  # or "mvsep_stage2"
else:
    _set_job_stage(job, "mvsep_stage1")
```

**6. Backward compatibility**: When `stage_updater` is `None` (not passed), fall back to the existing `_set_job_stage()` in-memory behavior. This preserves backward compatibility for any callers that don't pass the callback.

#### What About Non-Waiting Inner Stages?

The existing inner-stage transitions (`"fallback_local"`, `"fallback_local_stage2"`, individual Stage 1/Stage 2 events) can remain using `_set_job_stage()` (in-memory only). Only the **waiting-stage transitions** need the `stage_updater` callback, since those are the ones that persist for hours and need DB visibility.

However, for consistency, the implementation could route ALL inner-stage transitions through `stage_updater` when available. This is a minor improvement that gives full DB visibility into stem-separation progress. **Trade-off**: slightly more DB writes (one per stage transition, ~5-10 per job) vs. better observability. The existing LRC pipeline already does this (every `_update_stage` call writes to DB).

---

## 5. Integration with Existing Quota Detection

### 5a. MVSEP (minimal changes to existing detection)

The MVSEP client already has the full quota lifecycle:
- `_quota_exhausted` flag (`mvsep_client.py:134`) set on keyword match (lines 222-235, 249-257)
- `_check_quota_reset()` auto-clears on UTC midnight (lines 161-168)
- `is_available` property checks both flags (lines 141-159)
- `_disabled` is permanent (auth/credit errors, never auto-resets) (lines 225-230, 244-248)

**Change**: Add an `is_quota_exhausted` property that returns `self._quota_exhausted` (True if daily quota hit, False if disabled or available). This lets the caller distinguish:
- `not is_available AND is_quota_exhausted` → **wait** (daily, will reset)
- `not is_available AND not is_quota_exhausted` → **fail** (permanent `_disabled` or missing key)

**Integration in `stem_separation.py:_separate_with_mvsep_fallback()`**:

At three decision points:

**Point 1** (line 235-239): Initial availability check
```python
if not mvsep_client or not mvsep_client.is_available:
    if settings.SOW_FREE_ONLY_MODE and mvsep_client and mvsep_client.is_quota_exhausted:
        # Wait for quota reset, then retry from top
        await mvsep_quota_waiter.mark_exhausted()
        stage_updater("waiting_for_mvsep_quota_reset", ...)
        available = await mvsep_quota_waiter.wait(job, lambda: job.status == JobStatus.CANCELLED)
        if not available or job.status == JobStatus.CANCELLED:
            return  # cancelled
        # After resume: continue to Stage 1 (don't fall back to local)
    else:
        # Existing: local fallback (unchanged)
        ...
```

**Point 2** (line 272-277): Stage 1 failed (returned None from retries)
```python
if stage1_result is None:
    if settings.SOW_FREE_ONLY_MODE and mvsep_client.is_quota_exhausted:
        # Wait and retry Stage 1
        ...
    else:
        # Existing: full local pipeline fallback (unchanged)
        ...
```

**Point 3** (line 306-312): Stage 2 failed
```python
if stage2_result is None:
    if settings.SOW_FREE_ONLY_MODE and mvsep_client.is_quota_exhausted:
        # Wait and retry Stage 2
        ...
    else:
        # Existing: local Stage 2 fallback (unchanged)
        ...
```

**Implementation pattern**: Wrap each stage's MVSEP attempt in a `while True` loop that breaks on success or non-quota failure, and calls `mark_exhausted()` + `wait()` on quota failure:
```python
while True:
    stage1_result = await _run_mvsep_stage_with_retries(...)
    if stage1_result is not None:
        break
    if not (settings.SOW_FREE_ONLY_MODE and mvsep_client.is_quota_exhausted):
        break  # Non-quota failure → existing fallback
    # Quota exhausted in free-only mode → wait and retry
    await mvsep_quota_waiter.mark_exhausted()
    stage_updater("waiting_for_mvsep_quota_reset", ...)
    available = await mvsep_quota_waiter.wait(job, lambda: job.status == JobStatus.CANCELLED)
    if not available:  # cancelled
        return
    # After resume: back to top of while loop
```

### 5b. DashScope Qwen3 ASR (new quota detection)

**Current gap**: The `Qwen3AsrClient` has:
- `_circuit_open` (class-level, permanent, opens on 401/403 — auth, not quota)
- 429 → `Qwen3AsrError` (retriable) → 3 retries via `_with_retries()` → propagates to LRC handler → falls back to Whisper

**Missing**: A `_quota_exhausted` flag that marks "daily quota spent, will reset at UTC midnight."

**Changes to `qwen3_asr_client.py`**:

1. **New exception** (alongside the existing three at lines 27-36):
   ```python
   class Qwen3AsrQuotaExhaustedError(Qwen3AsrError):
       """DashScope free-tier daily quota exhausted. Will reset at UTC midnight."""
   ```

2. **New instance state** (in `__init__`, parallel to MVSEP's pattern):
   ```python
   self._quota_exhausted: bool = False
   self._quota_reset_utc: datetime = datetime.now(timezone.utc).replace(
       hour=0, minute=0, second=0, microsecond=0
   )
   ```

3. **New `_check_quota_reset()` method** (copy of MVSEP's pattern):
   ```python
   def _check_quota_reset(self) -> None:
       now_utc = datetime.now(timezone.utc)
       today_start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
       if self._quota_reset_utc < today_start:
           self._quota_exhausted = False
           self._quota_reset_utc = today_start
   ```

4. **New `is_available` property**:
   ```python
   @property
   def is_available(self) -> bool:
       if not self.api_key:
           return False
       if self.__class__._circuit_open:
           return False
       if self._quota_exhausted:
           self._check_quota_reset()
           if self._quota_exhausted:
               return False
       return True
   ```

5. **New `is_quota_exhausted` property**:
   ```python
   @property
   def is_quota_exhausted(self) -> bool:
       return self._quota_exhausted
   ```

6. **Modify `_with_retries()`** (line 211-225): When all 3 attempts fail on 429, set quota flag and raise new exception:
   ```python
   async def _with_retries(self, call):
       last_error = None
       was_rate_limited = False
       for attempt in range(3):
           try:
               return await call()
           except Qwen3AsrNonRetriableError:
               raise
           except Qwen3AsrTimeoutError:
               raise
           except Exception as exc:
               last_error = exc
               # Detect 429 / rate-limit in the exc or its cause
               if _is_dashscope_rate_limited(exc):
                   was_rate_limited = True
               if attempt == 2:
                   break
               await asyncio.sleep(2**attempt)
       # Retries exhausted
       if was_rate_limited:
           self._quota_exhausted = True
           raise Qwen3AsrQuotaExhaustedError(
               f"DashScope quota exhausted after retries: {last_error}"
           ) from last_error
       raise Qwen3AsrError(f"Qwen3 ASR failed after retries: {last_error}") from last_error
   ```

   The `_is_dashscope_rate_limited(exc)` helper checks for status_code 429 or "429" string in the exception (similar to `llm_rate_limit.py:52-104` and `youtube_transcript.py:60-95`).

7. **Add `is_available` check at top of `transcribe()`** (line 104-108): Check `_quota_exhausted` + `_check_quota_reset()` before attempting, similar to the existing `_circuit_open` check.

**Integration in `queue.py:_process_lrc_job` (lines 1112-1168)**:

Wrap the Qwen3 ASR block in a retry loop:
```python
while True:
    try:
        lrc_path, line_count, _qwen_phrases = await generate_lrc_from_qwen3_asr(...)
        lrc_source = "qwen3_asr"
        await self._update_stage(job, "qwen3_asr_done", 0.7)
        break  # success
    except Qwen3AsrQuotaExhaustedError:
        if settings.SOW_FREE_ONLY_MODE:
            await self._qwen3_quota_waiter.mark_exhausted()
            await self._update_stage(job, "waiting_for_qwen3_asr_quota_reset", 0.4)
            available = await self._qwen3_quota_waiter.wait(
                job, lambda: job.status == JobStatus.CANCELLED
            )
            if not available or job.status == JobStatus.CANCELLED:
                return  # cancelled
            await self._update_stage(job, "qwen3_asr_transcribing", 0.4)
            continue  # retry Qwen3 ASR
        else:
            # Existing: fall back to Whisper
            await self._update_stage(job, "falling_back_to_whisper", 0.45)
            break
    except Qwen3AsrError as e:
        # Existing: fall back to Whisper (unchanged)
        await self._update_stage(job, "falling_back_to_whisper", 0.45)
        break
    except Exception as e:
        # Existing: fall back to Whisper (unchanged)
        await self._update_stage(job, "falling_back_to_whisper", 0.45)
        break
```

The `if lrc_source != "qwen3_asr"` block (line 1171) naturally handles the Whisper fallback — if the loop exits without `lrc_source = "qwen3_asr"`, Whisper runs.

### 5c. Pausing the MVSEP Time Budget

In `_separate_with_mvsep_fallback()`, the `_time_remaining()` function (`stem_separation.py:241-242`) is based on `time.monotonic()`. When waiting for quota, we need to **not** count the wait time.

**Approach**: Track wait time as a deduction:
```python
total_wait_seconds = 0.0

def _time_remaining() -> float:
    elapsed = time.monotonic() - total_start - total_wait_seconds
    return settings.SOW_MVSEP_TOTAL_TIMEOUT - elapsed

# During quota wait:
wait_start = time.monotonic()
available = await mvsep_quota_waiter.wait(job, cancel_fn)
total_wait_seconds += time.monotonic() - wait_start
```

This way, the 1800s budget only counts actual API processing time, not quota-wait time. The existing timeout/budget machinery in `_run_mvsep_stage_with_retries()` works unchanged.

### 5d. What Does NOT Change

- **MVSEP retry/backoff** (`_run_mvsep_stage_with_retries`, `stem_separation.py:77-143`): Runs first, as-is. Patient wait only happens after the retry budget is exhausted and `_quota_exhausted` is the cause.
- **DashScope 3-attempt retry** (`_with_retries`, `qwen3_asr_client.py:211-225`): Runs first, as-is. Only when all 3 fail with 429 does the quota flag set.
- **LLM rate-limit retry** (`llm_rate_limit.py`): Unchanged. The LLM alignment calls (both YouTube correction and ASR alignment) are separate from ASR/stem quota.
- **Semaphores**: `_local_model_semaphore`, `_dashscope_asr_semaphore`, MVSEP `_semaphore` — not held during waiting. The `await wait()` suspends the coroutine, releasing the event loop. The DashScope semaphore is acquired inside `generate_lrc_from_qwen3_asr()` around the `transcribe()` call — if that raises before the semaphore context exits, the semaphore is released. The MVSEP semaphore is acquired inside `separate_vocals()` / `remove_reverb()` — same pattern.
- **YouTube transcript path**: Completely unaffected — it's already free.

---

## 6. Backward Compatibility

When `SOW_FREE_ONLY_MODE = False` (default):

| Component | Behavior |
|---|---|
| **QuotaWaiter** | Created but never called. `wait()` is never invoked because integration code checks `settings.SOW_FREE_ONLY_MODE` first. |
| **MVSEP client** | Unchanged. `_quota_exhausted` still works as before (lazy UTC check, fallback). New `is_quota_exhausted` property is never read. |
| **Qwen3 ASR client** | `_with_retries()` now raises `Qwen3AsrQuotaExhaustedError` instead of `Qwen3AsrError` when 429 retries are exhausted. This is a **subclass** of `Qwen3AsrError`, so the existing `except Qwen3AsrError` handler at `queue.py:1157` catches it — falls back to Whisper as before. No behavior change. |
| **Stem separation** | All three fallback points check `settings.SOW_FREE_ONLY_MODE` first — if False, skip straight to existing local fallback. |
| **LRC pipeline** | The `except Qwen3AsrQuotaExhaustedError` handler checks `settings.SOW_FREE_ONLY_MODE` — if False, falls through to existing Whisper fallback. |
| **DB schema** | No changes. Uses existing `stage` (free-form string) and `progress` (float) fields with new string values. No migration needed. |

**Key design principle**: `Qwen3AsrQuotaExhaustedError` extends `Qwen3AsrError` so it's caught by existing handlers when free-only mode is off.

---

## 7. Edge Cases

### Service restart during polling
Existing recovery at `queue.py:217-264` handles this: `PROCESSING` jobs are reset to `QUEUED` with `progress=0.0`, `stage="requeued"`. On restart:
- Client flags (`_quota_exhausted`, `_circuit_open`) are fresh (new process)
- If UTC day has rolled: `is_available` returns True, job proceeds normally
- If same UTC day: first API call fails with quota error again → enters wait
- QuotaWaiter instances are recreated (fresh state) — safe
- **No additional recovery logic needed.**

### Job cancellation during polling
The `wait()` method's 1s self-check loop checks `cancel_fn()` (which is `lambda: job.status == JobStatus.CANCELLED`) every tick. When `cancel_job()` sets the status, the next tick (within 1s) returns `False`, and the job handler exits cleanly. Follows the existing cooperative cancellation pattern (`queue.py:839`, `1108`).

### Multiple jobs waiting simultaneously
- All share one `QuotaWaiter` instance per API type
- When `is_available` returns True (either via poller setting the event or via 1s self-check), all waiting jobs resume
- Semaphore contention handled normally: MVSEP `_semaphore` (max 3) and `_dashscope_asr_semaphore` (max 2) gate actual API calls
- `mark_exhausted()` called by multiple jobs — `asyncio.Event.clear()` is idempotent. Poller start is guarded against double-start.

### Missing API keys
- `SOW_MVSEP_API_KEY` empty: `is_available` returns False, but `is_quota_exhausted` returns False → not a quota issue → job fails with clear error (not infinite wait)
- `SOW_DASHSCOPE_API_KEY` empty: Existing check at `queue.py:1113-1114` (`settings.SOW_DASHSCOPE_API_KEY`) skips Qwen3 ASR entirely → YouTube/Whisper path. Free-only mode requires the key to be configured.

### `_disabled` (permanent MVSEP errors) in free-only mode
- `_disabled = True` (invalid API key, insufficient credits, 401/403) → `is_quota_exhausted` returns False → does NOT trigger wait → job fails with error. Correct: patient waiting cannot fix a bad API key.

### Poller failure / hang
With the 1s self-check approach, the poller is a **non-critical optimization**:
- If poller crashes: each job's 1s self-check calls `is_available` directly → still detects UTC rollover → resumes
- If poller hangs: same — 1s self-check doesn't depend on poller
- Poller `_poll_loop` wrapped in `try/except Exception: log + continue` for resilience
- **No maximum wait timeout needed** — the 1s self-check ensures eventual progress or detection

### Time budget and quota wait interaction
Per design decision, quota-wait time is **excluded** from the `SOW_MVSEP_TOTAL_TIMEOUT` (1800s) budget. The `_time_remaining()` function subtracts accumulated wait time. This prevents the existing timeout from prematurely triggering fallback during a multi-hour quota wait.

### Qwen3 `_circuit_open` vs `_quota_exhausted` interaction
- `_circuit_open` opens on 401/403 (auth, permanent) — opens circuit breaker, raises `Qwen3AsrNonRetriableError`
- `_quota_exhausted` sets on 429 retry exhaustion (daily quota, temporary) — raises `Qwen3AsrQuotaExhaustedError`
- These are **independent flags**: an auth error doesn't set `_quota_exhausted`, and a quota error doesn't open `_circuit_open`
- `is_available` checks both: `if _circuit_open: return False; if _quota_exhausted: _check_quota_reset(); ...`
- In free-only mode, quota exhaustion → wait; circuit open → fail (can't fix auth error by waiting)

---

## 8. Testing Considerations

### Unit Tests

**New file: `tests/test_quota_waiter.py`**

| Test | What it verifies |
|---|---|
| `wait_returns_true_when_available` | Event is set → returns True immediately |
| `wait_returns_false_on_cancel` | cancel_fn returns True → returns False within ~1s |
| `wait_self_checks_is_available_every_second` | Even without poller, detects availability via 1s self-check |
| `mark_exhausted_clears_event_starts_poller` | After mark_exhausted, waiters block; poller task is created |
| `poller_sets_event_when_probe_returns_true` | Poller calls probe_fn, sets event, unblocks all waiters |
| `multiple_waiters_all_unblock` | 3 concurrent waiters all resume on single event.set() |
| `poller_survives_probe_exception` | probe_fn raises → poller logs and continues, doesn't crash |
| `poller_stops_when_no_waiters` | After all waiters unblock, poller task cancels itself |
| `mark_exhausted_idempotent` | Multiple jobs call mark_exhausted concurrently → single poller start |

**Extend: `tests/test_mvsep_client.py`**

| Test | What it verifies |
|---|---|
| `is_quota_exhausted_property` | Returns True after quota keyword match, False after _disabled or normally |
| `is_available_after_utc_rollover` (likely existing) | `_quota_exhausted` clears after UTC midnight |

**New: Qwen3 ASR client tests** (`tests/test_qwen3_asr_client.py` or extend integration)

| Test | What it verifies |
|---|---|
| `with_retries_raises_quota_exhausted_on_429` | Mock 3x 429 → raises `Qwen3AsrQuotaExhaustedError`, sets `_quota_exhausted = True` |
| `quota_exhausted_resets_after_utc` | Set `_quota_exhausted = True`, advance time → `is_available` returns True |
| `quota_does_not_open_circuit` | `_quota_exhausted = True` does NOT set `_circuit_open` |
| `auth_error_opens_circuit_not_quota` | 401 → `_circuit_open = True`, `_quota_exhausted = False` |
| `quota_error_is_subclass_of_asr_error` | `isinstance(Qwen3AsrQuotaExhaustedError(...), Qwen3AsrError) == True` |

**Extend: `tests/test_mvsep_fallback.py`**

| Test | What it verifies |
|---|---|
| `free_only_mode_waits_on_quota` | `SOW_FREE_ONLY_MODE=True`, `_quota_exhausted=True` → calls QuotaWaiter.wait, does NOT call local separator |
| `free_only_mode_fails_on_disabled` | `SOW_FREE_ONLY_MODE=True`, `_disabled=True` → does NOT wait, fails or falls back |
| `free_only_disabled_falls_back_normally` | `SOW_FREE_ONLY_MODE=False` → existing local fallback, QuotaWaiter never called |
| `time_budget_paused_during_wait` | Track `total_wait_seconds`, verify `_time_remaining()` excludes wait time |

**New: LRC pipeline tests**

| Test | What it verifies |
|---|---|
| `lrc_free_only_waits_on_quota` | `Qwen3AsrQuotaExhaustedError` in free-only mode → enters wait stage, does NOT fall back to Whisper |
| `lrc_free_only_disabled_falls_back` | `SOW_FREE_ONLY_MODE=False` → `Qwen3AsrQuotaExhaustedError` caught by `except Qwen3AsrError` → Whisper fallback |
| `lrc_cancelled_during_wait` | Job cancelled during QuotaWaiter.wait → handler exits, status is CANCELLED |
| `lrc_resumes_after_wait` | After QuotaWaiter returns True → retries Qwen3 ASR → succeeds |

### Integration Tests

| Test | What it verifies |
|---|---|
| `full_wait_and_resume_mvsep` | Mock MVSEP: quota error 1st call, success 2nd → stage transitions: `starting` → `waiting_for_mvsep_quota_reset` → `mvsep_stage1` → `complete` |
| `full_wait_and_resume_qwen3` | Mock Qwen3: 429 1st call, success 2nd → stage transitions: `waiting_for_qwen3_asr_quota_reset` → `qwen3_asr_transcribing` → `complete` |
| `multiple_jobs_share_poller` | 3 stem jobs hit quota → 1 poller task → all 3 resume on event.set() |
| `cancelled_job_during_wait_clean_exit` | Start job → enters wait → cancel_job → status CANCELLED within 1s |
| `jobs_independent_waiters` | Stem job waiting on MVSEP quota, LRC job waiting on Qwen3 quota → each uses its own QuotaWaiter |
| `stage_updater_persists_waiting_stage` | Stem job enters quota wait → `stage_updater` callback called with `"waiting_for_mvsep_quota_reset"` → DB row has matching `stage` value |
| `stage_updater_none_falls_back_to_in_memory` | `stage_updater=None` → uses `_set_job_stage()` (in-memory), no DB write for inner stages — backward compatible |

### Test Infrastructure

- **Poll interval**: Tests should inject `poll_interval=0.1` for fast iteration (the 1s self-check is the real mechanism, not the poller)
- **probe_fn mockability**: Pass a simple lambda in tests, don't create real API clients
- **`SOW_FREE_ONLY_MODE`**: Use `monkeypatch` on `settings.SOW_FREE_ONLY_MODE` or pass via a fixture (since `settings` is a module-level singleton)
- **Time mocking**: For UTC rollover tests, use `freezegun` or manual monkeypatch on `_check_quota_reset`'s `datetime.now(timezone.utc)` call
- **Existing test patterns**: Follow the mocking approach used in `test_mvsep_fallback.py` (mock `MvsepClient`, `AudioSeparatorWrapper`) and `test_mvsep_client.py` (mock `httpx.AsyncClient`)

---

## 9. Summary: Files to Create/Modify

| File | Action | Complexity |
|---|---|---|
| `workers/quota_waiter.py` | **New** | Medium — QuotaWaiter class (~100 lines) |
| `config.py` | Add 2 fields | Trivial |
| `.env.example` | Document 2 vars | Trivial |
| `services/qwen3_asr_client.py` | Add `_quota_exhausted` flag, `is_available`, `is_quota_exhausted`, `Qwen3AsrQuotaExhaustedError`, modify `_with_retries` | Medium |
| `services/mvsep_client.py` | Add `is_quota_exhausted` property | Trivial |
| `workers/stem_separation.py` | Add conditional wait at 3 decision points + time-budget pause + `stage_updater` callback parameter on `process_stem_separation()` / `_separate_with_mvsep_fallback()` | Medium |
| `workers/queue.py` | Add QuotaWaiter injection, LRC wait-retry loop, `_wait_for_quota` helper | Medium |
| `main.py` | Create QuotaWaiter instances in lifespan, inject into queue | Low |
| `tests/test_quota_waiter.py` | **New** | Medium |
| `tests/test_mvsep_client.py` | Extend | Low |
| `tests/test_mvsep_fallback.py` | Extend | Medium |
| `tests/test_qwen3_asr_client.py` (new or extend integration) | **New/Extend** | Medium |

All paths relative to `ops/analysis-service/src/sow_analysis/` (source) and `ops/analysis-service/tests/` (tests).

---

## Appendix: Key Code Locations (for implementer reference)

| Symbol | Location |
|---|---|
| `Settings` class | `config.py` |
| `MvsepClient._quota_exhausted` | `services/mvsep_client.py:134` |
| `MvsepClient._disabled` | `services/mvsep_client.py:132` |
| `MvsepClient.is_available` | `services/mvsep_client.py:141-159` |
| `MvsepClient._check_quota_reset()` | `services/mvsep_client.py:161-168` |
| `MvsepClient._submit_job()` (quota detection) | `services/mvsep_client.py:170-261` |
| `_is_quota_exhausted()` (keyword matcher) | `services/mvsep_client.py:36-45` |
| `Qwen3AsrClient._circuit_open` | `services/qwen3_asr_client.py:90` |
| `Qwen3AsrClient.transcribe()` | `services/qwen3_asr_client.py:104-145` |
| `Qwen3AsrClient._with_retries()` | `services/qwen3_asr_client.py:211-225` |
| `Qwen3AsrClient._raise_for_response()` | `services/qwen3_asr_client.py:308-317` |
| `Qwen3AsrError` / `NonRetriableError` / `TimeoutError` | `services/qwen3_asr_client.py:27-36` |
| `_separate_with_mvsep_fallback()` | `workers/stem_separation.py:209-315` |
| `_run_mvsep_stage_with_retries()` | `workers/stem_separation.py:77-143` |
| `_set_job_stage()` (in-memory only) | `workers/stem_separation.py:201-207` |
| `process_stem_separation()` | `workers/stem_separation.py:318-536` |
| `_process_lrc_job()` | `workers/queue.py:870` |
| Qwen3 ASR try/except block | `workers/queue.py:1112-1168` |
| Whisper fallback block | `workers/queue.py:1170-1213` |
| `_update_stage()` (persists to DB) | `workers/queue.py:766-782` |
| `JobQueue.initialize()` (recovery) | `workers/queue.py:217-264` |
| `JobQueue.cancel_job()` | `workers/queue.py:1823-1873` |
| `JobQueue._local_model_semaphore` | `workers/queue.py:164-166` |
| `JobQueue._dashscope_asr_semaphore` | `workers/queue.py:167` |
| `optional_semaphore()` | `workers/queue.py:30-40` |
| `process_jobs()` (main loop) | `workers/queue.py:350-379` |
| `lifespan` (startup/shutdown) | `main.py` |
| `JobStatus` enum | `models.py:11-18` |
| `Job` dataclass | `models.py:245-265` |
| `JobStore` schema | `storage/db.py:63-89` |
| `JobStore.get_interrupted_jobs()` | `storage/db.py:550` |
| `JobStore.update_job()` | `storage/db.py:420` |
