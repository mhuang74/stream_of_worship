# Production Review Fixes: Free-Only Patient Mode (v2)

Fixes for critical and high-severity issues found in `specs/free-only-patient-mode-v2.md`.

---

## Critical Fix 1: Missing `mvsep_quota_waiter` Parameter in Stem Separation Functions

**Problem:** The spec's code snippets in section 5a use `mvsep_quota_waiter` inside `_separate_with_mvsep_fallback()`, but the function signature (section 5d, line 253) only adds `stage_updater`. Since `_separate_with_mvsep_fallback` and `process_stem_separation` are module-level functions (not `JobQueue` methods), they cannot access `self._mvsep_quota_waiter`.

**Fix:** Add `mvsep_quota_waiter` as an optional parameter to both functions, mirroring the `stage_updater` pattern.

**Updated signatures:**

```python
# In stem_separation.py

async def _separate_with_mvsep_fallback(
    input_path: Path,
    output_dir: Path,
    job: Job,
    mvsep_client: Optional["MvsepClient"],
    separator_wrapper: AudioSeparatorWrapper,
    local_model_semaphore: Optional[asyncio.Semaphore] = None,
    stage_updater: Optional[StageUpdater] = None,
    mvsep_quota_waiter: Optional["QuotaWaiter"] = None,   # NEW
) -> Tuple[Optional[Path], Optional[Path], Optional[Path]]:
    ...
```

```python
async def process_stem_separation(
    job: Job,
    separator_wrapper: AudioSeparatorWrapper,
    r2_client: R2Client,
    cache_manager: CacheManager,
    mvsep_client: Optional["MvsepClient"] = None,
    local_model_semaphore: Optional[asyncio.Semaphore] = None,
    stage_updater: Optional[StageUpdater] = None,
    mvsep_quota_waiter: Optional["QuotaWaiter"] = None,   # NEW
) -> None:
    ...
    result = await _separate_with_mvsep_fallback(
        ...,
        stage_updater=stage_updater,
        mvsep_quota_waiter=mvsep_quota_waiter,
    )
```

**Updated wiring in `queue.py:_process_stem_separation_job`:**

```python
await process_stem_separation(
    job=job,
    separator_wrapper=self._separator_wrapper,
    r2_client=self.r2_client,
    cache_manager=self.cache_manager,
    mvsep_client=self._mvsep_client,
    local_model_semaphore=self._local_model_semaphore,
    stage_updater=lambda s, p=None: self._update_stage(job, s, p),
    mvsep_quota_waiter=self._mvsep_quota_waiter,  # NEW
)
```

---

## Critical Fix 2: Point 1 `_disabled=True` in Free-Only Mode Falls Back to Local

**Problem:** Section 5a (line 326-327) states the design rule:
```
not is_available AND not is_quota_exhausted → fail (permanent _disabled or missing key)
```
But the code for Point 1 (line 336-347) falls through to the `else` branch which does local fallback. In free-only mode, permanent errors like `_disabled=True` (invalid API key, insufficient credits) should **fail** the job, not fall back to local models.

**Fix:** Restructure the Point 1 availability check to handle three distinct cases.

**Updated Point 1 code (replaces lines 333-348 in the spec):**

```python
# Point 1: Initial availability check
if not mvsep_client or not mvsep_client.is_available:
    if settings.SOW_FREE_ONLY_MODE and mvsep_client and mvsep_client.is_quota_exhausted:
        # Quota exhausted in free-only mode → wait for reset
        await mvsep_quota_waiter.mark_exhausted()
        if stage_updater:
            await stage_updater("waiting_for_mvsep_quota_reset", None)
        available = await mvsep_quota_waiter.wait(job, lambda: job.status == JobStatus.CANCELLED)
        if not available or job.status == JobStatus.CANCELLED:
            return (None, None, None)  # cancelled
        # After resume: continue to Stage 1 (don't fall back to local)
    elif settings.SOW_FREE_ONLY_MODE:
        # Permanent unavailability in free-only mode → fail
        # (covers: _disabled=True, missing API key, not enabled)
        raise StemSeparationWorkerError(
            "MVSEP permanently unavailable in free-only mode "
            "(disabled, missing API key, or not enabled). "
            "Cannot fall back to local models."
        )
    else:
        # Not in free-only mode → existing local fallback (unchanged)
        logger.info("MVSEP not available, using local audio-separator")
        async with optional_semaphore(local_model_semaphore):
            return await separator_wrapper.separate_stems(input_path, output_dir)
```

**Design note:** The `StemSeparationWorkerError` is caught by `_process_stem_separation_job` (line 1699-1711) which sets `status=FAILED`, `stage="stem_separation_error"`, and persists to DB. No new error handling needed.

---

## Critical Fix 3: `lrc_source` Variable Not Initialized Before `while True` Loop

**Problem:** The spec's LRC handler code (section 5b, line 508-554) wraps the Qwen3 ASR call in a `while True` loop. `lrc_source = "qwen3_asr"` is set inside the `try` block. If the first iteration hits `except Qwen3AsrError` (non-quota) and breaks, `lrc_source` would raise `UnboundLocalError` at the `if lrc_source != "qwen3_asr"` check (current line 1171).

**Fix:** Initialize `lrc_source = None` immediately before the `while True` loop.

**Updated code (insert before `while True:` at line 508 in the spec):**

```python
lrc_source = None  # Initialize before retry loop
while True:
    try:
        lrc_path, line_count, _qwen_phrases = await generate_lrc_from_qwen3_asr(
            ...
        )
        lrc_source = "qwen3_asr"
        await self._update_stage(job, "qwen3_asr_done", 0.7)
        break  # success
    except Qwen3AsrQuotaExhaustedError:
        ...
```

---

## High Fix 4: `_stage2_time_remaining()` Doesn't Subtract Wait Time

**Problem:** Section 5d (line 594-595) says "The same pattern is used for `_stage2_time_remaining()`" but provides no code. The current `_stage2_time_remaining()` computes `time.monotonic() - total_start` without subtracting `total_wait_seconds`. If Stage 1 waits for hours, Stage 2 will immediately timeout.

**Fix:** Update `_stage2_time_remaining()` to also subtract accumulated wait time.

**Updated `_stage2_time_remaining()` definition (replaces lines 247-253 in current code):**

```python
def _stage2_time_remaining() -> float:
    # Must subtract wait time from elapsed, same as _time_remaining()
    total_remaining = (
        settings.SOW_MVSEP_TOTAL_TIMEOUT
        - (time.monotonic() - total_start - total_wait_seconds[0])
    )
    if stage2_start is None:
        return total_remaining
    stage2_remaining = settings.SOW_MVSEP_STAGE2_TIMEOUT - (time.monotonic() - stage2_start)
    return min(total_remaining, stage2_remaining)
```

---

## High Fix 5: Service Restart Resets Progress to 0.0

**Problem:** Section 4 says "Progress: Kept at current value (don't reset)". But `initialize()` in `queue.py:238` resets `job.progress = 0.0` for all interrupted jobs. This means after a restart during a quota wait, the API shows `progress: 0.0` instead of the preserved value.

**Fix:** Update the recovery logic in `initialize()` to preserve the existing progress from the DB row when requeuing interrupted jobs. The `_update_stage()` method already persists progress to DB, so the value is available.

**Updated recovery code in `queue.py:initialize()` (around line 238):**

```python
# Before (current):
job.progress = 0.0
job.stage = "requeued"

# After (fix):
# Preserve progress from DB — _update_stage() persists it during quota waits
job.stage = "requeued"
# job.progress is already set from the DB row deserialization; do NOT reset to 0.0
```

**Note:** The `get_interrupted_jobs()` method at `storage/db.py:550` reads the `progress` column from the `jobs` table. The `_row_to_job()` deserializer at `storage/db.py:454` maps it to `Job.progress`. So the progress value from the last `_update_stage()` call is already available — the `initialize()` code just overwrites it to 0.0. Removing that overwrite is the fix.

**Edge case:** Jobs that were interrupted before reaching the quota-wait stage (e.g., during download) will still have `progress=0.0` from their initial state. This is correct — the fix only preserves progress for jobs that had already progressed.

---

## High Fix 6: No Heartbeat Mechanism for 60-Second DB Write During Wait

**Problem:** Section 4 says "Call `_update_stage` with identical values every 60 seconds to refresh `updated_at`". But `wait()` blocks the coroutine with a 1s self-check loop — it has no mechanism to call `_update_stage` every 60s. Without this, waiting jobs will have stale `updated_at` timestamps, and monitoring will incorrectly flag them as stuck.

**Fix:** Add a `heartbeat_fn` callback parameter to `QuotaWaiter.wait()`. The 1s self-check loop calls it every 60 iterations. The caller provides a lambda that calls `_update_stage` with the current stage and progress.

**Updated `QuotaWaiter.wait()` signature:**

```python
async def wait(
    self,
    job,
    cancel_fn: Callable[[], bool],
    heartbeat_fn: Optional[Callable[[], None]] = None,
) -> bool:
    """Block until quota available or job cancelled.

    Self-checks is_available every 1s. Calls heartbeat_fn every 60s
    to refresh job updated_at for monitoring. Returns True if
    available, False if cancelled.
    """
```

**Updated call sites:**

```python
# LRC handler (queue.py):
available = await self._qwen3_quota_waiter.wait(
    job,
    lambda: job.status == JobStatus.CANCELLED,
    heartbeat_fn=lambda: self._update_stage(job, "waiting_for_qwen3_asr_quota_reset", 0.4),
)

# Stem separation (stem_separation.py):
available = await mvsep_quota_waiter.wait(
    job,
    lambda: job.status == JobStatus.CANCELLED,
    heartbeat_fn=lambda: (
        stage_updater("waiting_for_mvsep_quota_reset", None)
        if stage_updater else None
    ),
)
```

**Implementation note for `wait()`:** The heartbeat counter resets on each `wait()` call. The counter is local to the method, not shared across callers. The `heartbeat_fn` is called synchronously (not awaited) to keep the loop simple — the caller's lambda can schedule an async task if needed. Since `_update_stage` is async, the lambda should fire-and-forget via `asyncio.create_task()`:

```python
# In the 1s self-check loop inside wait():
tick = 0
while True:
    if cancel_fn():
        return False
    if self.probe_fn():
        self._event.set()
        return True
    tick += 1
    if tick % 60 == 0 and heartbeat_fn:
        heartbeat_fn()
    try:
        await asyncio.wait_for(self._event.wait(), timeout=1.0)
    except asyncio.TimeoutError:
        pass
```

**Alternative (simpler, preferred):** Instead of a `heartbeat_fn` callback, make the `wait()` caller responsible for the heartbeat. The caller wraps `wait()` in an `asyncio.wait()` with a 60s timeout, calling `_update_stage` on each timeout:

```python
# LRC handler (queue.py):
while True:
    available = await self._qwen3_quota_waiter.wait(
        job, lambda: job.status == JobStatus.CANCELLED
    )
    if available:
        break
    # Timed out after 60s — refresh heartbeat
    await self._update_stage(job, "waiting_for_qwen3_asr_quota_reset", 0.4)
```

But this requires `wait()` to support a timeout parameter. The simpler approach: add a `max_wait_seconds` parameter to `wait()`. When the timeout expires, `wait()` returns `False` (same as cancellation). The caller checks `job.status != JobStatus.CANCELLED` to distinguish timeout from cancellation, then calls `_update_stage` and re-enters `wait()`:

```python
# Updated QuotaWaiter.wait() signature:
async def wait(
    self,
    job,
    cancel_fn: Callable[[], bool],
    max_wait_seconds: int = 60,
) -> bool:
    """Block until quota available, job cancelled, or max_wait_seconds elapsed.
    Returns True if available, False if cancelled or timed out.
    """
```

**Decision: Use the `max_wait_seconds=60` approach.** It is simpler, requires no callback plumbing, and the caller already has access to `self._update_stage`. The call site becomes:

```python
# LRC handler (queue.py):
while True:
    available = await self._qwen3_quota_waiter.wait(
        job, lambda: job.status == JobStatus.CANCELLED, max_wait_seconds=60
    )
    if available:
        break
    if job.status == JobStatus.CANCELLED:
        return  # cancelled
    # Heartbeat: refresh updated_at so monitoring doesn't flag as stale
    await self._update_stage(job, "waiting_for_qwen3_asr_quota_reset", 0.4)
    # Loop back to wait() for another 60s

# Stem separation (stem_separation.py):
while True:
    available = await mvsep_quota_waiter.wait(
        job, lambda: job.status == JobStatus.CANCELLED, max_wait_seconds=60
    )
    if available:
        break
    if job.status == JobStatus.CANCELLED:
        return (None, None, None)
    # Heartbeat
    if stage_updater:
        await stage_updater("waiting_for_mvsep_quota_reset", None)
    # Loop back to wait() for another 60s
```

**Note:** This also means the 1s self-check loop in `wait()` needs a tick counter to break after `max_wait_seconds` iterations. The `wait()` implementation uses `asyncio.wait_for(self._event.wait(), timeout=1.0)` in a loop; a `for _ in range(max_wait_seconds)` loop is sufficient.

---

## High Fix 7: `SOW_FREE_ONLY_MODE=True` with Missing DashScope Key Silently Falls Back to Whisper

**Problem:** Section 7 (line 644) acknowledges that a missing `SOW_DASHSCOPE_API_KEY` skips Qwen3 ASR entirely, falling through to Whisper. But in free-only mode, this silently violates the feature's intent. A misconfiguration would go undetected.

**Fix:** Add a startup validation warning in `main.py` lifespan, and add a guard in the LRC handler.

**Fix A: Startup validation in `main.py` lifespan (alongside existing config table logging):**

```python
# In main.py lifespan, after creating settings/config table:
if settings.SOW_FREE_ONLY_MODE:
    if not settings.SOW_DASHSCOPE_API_KEY:
        logger.warning(
            "SOW_FREE_ONLY_MODE is enabled but SOW_DASHSCOPE_API_KEY is not set. "
            "LRC generation will fall back to Whisper (local model) instead of "
            "waiting for Qwen3 ASR quota. Set SOW_DASHSCOPE_API_KEY to use free-only mode."
        )
    if not settings.SOW_MVSEP_API_KEY:
        logger.warning(
            "SOW_FREE_ONLY_MODE is enabled but SOW_MVSEP_API_KEY is not set. "
            "Stem separation will fail with MVSEP permanently unavailable. "
            "Set SOW_MVSEP_API_KEY to use free-only mode."
        )
```

**Fix B: Guard in LRC handler (queue.py, around line 1112-1117):**

```python
# Before the existing Qwen3 ASR check:
if (
    request.options.use_qwen3_asr
    and settings.SOW_DASHSCOPE_API_KEY
    ...
):
    ...

# Add: when free-only mode is on but DashScope is not configured,
# fail the job instead of silently falling back to Whisper.
# This is in addition to the existing check — the new block goes
# AFTER the existing `if` block, inside the `else` branch:

if (
    request.options.use_qwen3_asr
    and settings.SOW_DASHSCOPE_API_KEY
    and generate_lrc_from_qwen3_asr is not None
    and build_qwen3_asr_cache_key is not None
):
    # ... existing Qwen3 ASR path (with the new while True retry loop) ...
else:
    if settings.SOW_FREE_ONLY_MODE and request.options.use_qwen3_asr:
        # Free-only mode requires DashScope to be configured
        raise LrcWorkerError(
            "SOW_FREE_ONLY_MODE is enabled but DashScope Qwen3 ASR is not configured. "
            "Set SOW_DASHSCOPE_API_KEY to use free-only mode, "
            "or disable use_qwen3_asr to skip ASR-based LRC generation."
        )
    logger.info("Qwen3 ASR disabled or DashScope not configured; using Whisper")
    await self._update_stage(job, "falling_back_to_whisper", 0.35)
```

**Note on `LrcWorkerError`:** The existing error handler at `queue.py:1305-1335` catches `LrcWorkerError` and sets `status=FAILED`. This is the correct behavior for a misconfiguration.

---

## Summary of Changes to the Original Spec

| Issue | Severity | Section(s) Affected | Nature of Fix |
|---|---|---|---|
| Missing `mvsep_quota_waiter` param | Critical | 5a, 5d, 2 | Add parameter to `_separate_with_mvsep_fallback` and `process_stem_separation`; wire from `JobQueue` |
| `_disabled` falls back to local | Critical | 5a (Point 1) | Add `elif SOW_FREE_ONLY_MODE: raise` between quota-wait and local-fallback branches |
| `lrc_source` not initialized | Critical | 5b | Add `lrc_source = None` before `while True` |
| `_stage2_time_remaining()` missing wait deduction | High | 5d | Subtract `total_wait_seconds[0]` in `_stage2_time_remaining()` |
| Progress reset to 0.0 on restart | High | 4, 7 | Remove `job.progress = 0.0` in `initialize()` recovery |
| No heartbeat mechanism | High | 4, 3 | Add `max_wait_seconds=60` to `wait()`; caller loops with heartbeat |
| Missing DashScope key silent fallback | High | 7, 5b | Add startup warning + runtime guard in LRC handler |