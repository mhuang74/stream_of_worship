# Implementation Plan: Throttle Logs During Quota-Wait Quiescence

> **Status:** Implemented.
> **Component:** Analysis Service (`ops/analysis-service/`)
> **Related:** `free-only-patient-mode-v4.md` (QuotaWaiter origin)

## Problem

When every job in the Analysis Service is blocked waiting for free-tier API
quota to reset (MVSEP or Qwen3 ASR — resets once per UTC day, often many hours
away), two loggers emit redundant lines multiple times per minute with no new
information:

1. **`QuotaWaiter.wait()`** — logs `N jobs waiting for quota reset (sample: [...])`
   every 30s (`quota_waiter.py:103`, threshold `30.0`).
2. **`JobQueue._periodic_logging_loop()`** — logs `Queue state: ...` every 60s
   (`queue.py:2243`, interval `_log_interval_seconds = 60.0` at `queue.py:189`).

Combined this is ~3 log lines/minute of identical content while the system is
in a steady-state wait. Example observed output:

```
11:22:45 QuotaWaiter[mvsep]: 9 jobs waiting for quota reset (sample: [...])
11:23:15 QuotaWaiter[mvsep]: 9 jobs waiting for quota reset (sample: [...])
11:23:24 Queue state: ... STEM_SEPARATION[...processing:9...] | Wait times: ...
11:23:45 QuotaWaiter[mvsep]: 9 jobs waiting for quota reset (sample: [...])
11:24:15 QuotaWaiter[mvsep]: 9 jobs waiting for quota reset (sample: [...])
11:24:24 Queue state: ... STEM_SEPARATION[...processing:9...] | Wait times: ...
...
```

### Desired Behavior

When **all** active (non-finished) jobs are blocked on a quota reset — the
"quiescent" state — back off both loggers to **once every 30 minutes**. As soon
as anything changes (a new job is submitted, quota comes back and a job resumes
real work, a job fails), revert immediately to the normal fast cadence so
operators don't miss state transitions.

## Design

### 1. New config setting

`config.py` — add next to `SOW_QUOTA_POLL_INTERVAL_SECONDS`:

```python
SOW_QUOTA_WAIT_QUIESCENT_LOG_INTERVAL_SECONDS: int = 1800
# When every active job is blocked on a free-tier API quota reset (MVSEP or
# Qwen3 ASR), back off QuotaWaiter and queue-state periodic logging to this
# interval (default 30 min) instead of the normal 30s/60s cadence. The first
# log on entering quiescence still fires immediately; subsequent identical
# "still waiting" lines are suppressed until the interval elapses. Tunable so
# operators can shorten during debugging.
```

### 2. Centralize "quota-wait stage" detection in JobQueue

Add a module-level constant in `queue.py`:

```python
_QUOTA_WAIT_STAGES = frozenset({
    "waiting_for_mvsep_quota_reset",
    "waiting_for_qwen3_asr_quota_reset",
})
```

These stage strings already exist scattered across `stem_separation.py:269,284`
and `queue.py:918,1332,1347`. Centralizing them here is a low-risk cleanup; the
existing call sites may optionally be updated to reference the constant but are
not required to change for correctness.

### 3. `JobQueue._is_quota_wait_quiescent()` method

New method on `JobQueue`:

```python
def _is_quota_wait_quiescent(self) -> bool:
    """True when every active (non-finished) job is blocked on a quota reset.

    Returns True iff:
      - there is >=1 in-memory job with status PROCESSING and stage in
        _QUOTA_WAIT_STAGES, AND
      - all other reportable jobs (QUEUED / WAITING / PROCESSING / recently
        FAILED) are also PROCESSING with a quota-wait stage.

    Empty queue -> False (the early-return in _log_queue_state already
    suppresses logging when there is nothing to report).
    """
```

Implementation iterates `self._jobs.values()`, partitions into
finished (COMPLETED/FAILED/CANCELLED — ignored, except FAILED within
`FINISHED_JOB_MEMORY_RETENTION_SECONDS` which counts as reportable per the
existing `has_reportable_jobs` logic at `queue.py:2195-2213`) and active.
Quiescent requires ≥1 active quota-waiting job AND all active jobs
quota-waiting.

### 4. Adaptive interval in `QuotaWaiter`

`quota_waiter.py` changes:

**Constructor** — add two optional params:

```python
def __init__(
    self,
    name: str,
    probe_fn: Callable[[], bool],
    poll_interval: int,
    log_interval_seconds: float = 30.0,
    quiescent_log_interval_seconds: float = 1800.0,
    is_quiescent_fn: Optional[Callable[[], bool]] = None,
) -> None:
```

Store all three. `is_quiescent_fn` defaults to `None` (treated as "never
quiescent") so existing behavior and all current tests are unchanged.

**`wait()` periodic logging block** (`quota_waiter.py:101-111`) — replace the
fixed `30.0` threshold with a dynamic one:

```python
now = time.monotonic()
quiescent = self._is_quiescent_fn is not None and self._is_quiescent_fn()
effective_interval = (
    self._quiescent_log_interval if quiescent else self._log_interval
)
if now - self._last_log_time >= effective_interval:
    self._last_log_time = now
    sample = list(self._waiting_jobs)[:5]
    logger.info(
        "QuotaWaiter[%s]: %d jobs waiting for quota reset (sample: %s)%s",
        self._name,
        len(self._waiting_jobs),
        sample,
        " [quiescent; next log in %.0fmin]" % (effective_interval / 60.0)
        if quiescent else "",
    )
```

The `[quiescent; next log in 30min]` suffix makes the backoff visible to
operators reading the log so they understand why the next line is far away.

**Transition logging** (active → quiescent): the first time a tick observes
`quiescent=True` after a non-quiescent period, emit one extra INFO line:

```
QuotaWaiter[mvsep]: all jobs blocked on quota reset; backing off periodic log to 30min
```

Track transition with a `self._was_quiescent: bool = False` field. On
`quiescent and not self._was_quiescent` → log the transition line. On
`not quiescent and self._was_quiescent` → log "resuming normal log cadence".
Update `self._was_quiescent = quiescent` each tick.

### 5. Wire `is_quiescent_fn` from JobQueue to QuotaWaiter

`JobQueue.set_quota_waiters()` (`queue.py:230`) currently only stores the
waiter references. Extend it to inject the quiescent callback:

```python
def set_quota_waiters(self, mvsep: Any = None, qwen3: Any = None) -> None:
    self._mvsep_quota_waiter = mvsep
    self._qwen3_quota_waiter = qwen3
    if mvsep is not None:
        mvsep.is_quiescent_fn = self._is_quota_wait_quiescent
    if qwen3 is not None:
        qwen3.is_quiescent_fn = self._is_quota_wait_quiescent
```

(Use attribute assignment rather than a new setter method on QuotaWaiter to
keep the change minimal; the field is settable. Alternatively add a
`set_quiescent_fn()` method on QuotaWaiter for encapsulation — either is
acceptable.)

`main.py:132-144` constructs the `QuotaWaiter` instances **before**
`set_quota_waiters` is called (`main.py:145`), so the callback is wired after
construction. The QuotaWaiter must tolerate `is_quiescent_fn` being `None`
until wired (handled by the `is not None` guard above).

### 6. Throttle `_log_queue_state` during quiescence

Keep the 60s loop tick in `_periodic_logging_loop` unchanged — the tick is
cheap and lets us detect state transitions promptly. Instead, suppress the
**log emission** inside `_log_queue_state()`:

Add field: `self._last_quiescent_log_time: float = 0.0` (init in `__init__`).

At the top of `_log_queue_state()`, after computing `has_reportable_jobs`
but before building the log string:

```python
quiescent = self._is_quota_wait_quiescent()
if quiescent:
    now_mono = time.monotonic()
    if now_mono - self._last_quiescent_log_time < settings.SOW_QUOTA_WAIT_QUIESCENT_LOG_INTERVAL_SECONDS:
        return  # suppress: nothing new to report, all jobs still waiting
    self._last_quiescent_log_time = now_mono
else:
    self._last_quiescent_log_time = 0.0  # reset so next quiescent entry logs immediately
```

This yields:
- **Entering quiescence**: one `Queue state: ...` line is emitted (the first
  tick where `quiescent` becomes True), then suppressed for 30 min.
- **During quiescence**: one line every 30 min.
- **Leaving quiescence**: `_last_quiescent_log_time` resets to 0.0, so the
  next tick logs immediately and frequent 60s logging resumes.

### 7. Behavior matrix

| System state | QuotaWaiter log cadence | Queue-state log cadence |
|---|---|---|
| No jobs | (no waiters) | suppressed (early return) |
| Jobs actively processing | 30s | 60s |
| All jobs quota-waiting (quiescent) | 30 min (+ transition line) | 30 min |
| Quiescent → new job submitted | immediate resume (job stage changes → not quiescent) | immediate resume |
| Quiescent → quota resets | immediate resume (jobs self-check every 1s, resume work, stage changes) | immediate resume on next 60s tick |

Note: throttling affects **logging only**. Job resumption is unaffected —
`QuotaWaiter.wait()` still self-checks `probe_fn()` every 1s and the poller
still runs on its own interval, so jobs resume the moment quota is available
regardless of when the next log line would have fired.

## Files to Change

1. `ops/analysis-service/src/sow_analysis/config.py` — add
   `SOW_QUOTA_WAIT_QUIESCENT_LOG_INTERVAL_SECONDS`.
2. `ops/analysis-service/src/sow_analysis/workers/quota_waiter.py` — add
   `log_interval_seconds`, `quiescent_log_interval_seconds`,
   `is_quiescent_fn` constructor params; dynamic threshold in `wait()`;
   `_was_quiescent` transition logging.
3. `ops/analysis-service/src/sow_analysis/workers/queue.py` — add
   `_QUOTA_WAIT_STAGES` constant, `_is_quota_wait_quiescent()` method,
   `_last_quiescent_log_time` field, suppression logic in
   `_log_queue_state()`; wire `is_quiescent_fn` in `set_quota_waiters()`.
4. `ops/analysis-service/tests/test_quota_waiter.py` — add quiescent
   throttling tests.
5. `ops/analysis-service/tests/test_queue_logging.py` (new) — tests for
   `_is_quota_wait_quiescent()` and `_log_queue_state()` suppression.

## Test Plan

### `test_quota_waiter.py` additions

- `test_quiescent_fn_none_uses_normal_interval`: with `is_quiescent_fn=None`
  (default), logging fires every 30s as before. Use `caplog` + monkeypatched
  `time.monotonic` to advance the clock and assert log count.
- `test_quiescent_fn_true_uses_quiescent_interval`: with
  `is_quiescent_fn=lambda: True`, only one log fires across 40 simulated
  minutes of ticks; second fires at the 30-min mark.
- `test_quiescent_transition_logs_backoff_notice`: on the first quiescent
  tick, the extra "backing off periodic log to 30min" line is emitted.
- `test_quiescent_to_active_resumes_cadence`: after quiescent period, when
  `is_quiescent_fn` flips to False, the "resuming normal log cadence" line
  fires and 30s cadence resumes.

### `test_queue_logging.py` (new)

- `test_is_quota_wait_quiescent_all_processing_quota_wait`: populate
  `job_queue._jobs` with 3 PROCESSING jobs whose `stage` is
  `waiting_for_mvsep_quota_reset` → assert `_is_quota_wait_quiescent()` is
  True.
- `test_is_quota_wait_quiescent_mixed_stages`: one quota-waiting, one
  PROCESSING with `stage="analyzing"` → False.
- `test_is_quota_wait_quiescent_with_queued_job`: quota-waiting jobs plus a
  QUEUED job → False (queued job needs processing).
- `test_is_quota_wait_quiescent_empty_queue`: no jobs → False.
- `test_is_quota_wait_quiescent_only_finished_jobs`: only COMPLETED/FAILED →
  False (nothing active).
- `test_log_queue_state_suppressed_during_quiescence`: mock
  `_is_quota_wait_quiescent` to return True, set
  `_last_quiescent_log_time` to now, assert `logger.info` not called for the
  queue-state line; advance clock past interval, assert it is called once.
- `test_log_queue_state_resumes_after_quiescence`: after suppression, flip
  quiescent to False, assert next call logs immediately and
  `_last_quiescent_log_time` reset to 0.0.

### Verification commands

```bash
cd ops/analysis-service && uv run --extra dev pytest tests/test_quota_waiter.py tests/test_queue_logging.py -v
```

Manual smoke test: with `SOW_FREE_ONLY_MODE=True` and MVSEP quota exhausted,
submit 9 stem-separation jobs. Confirm logs collapse to one line per 30 min
once all jobs enter `waiting_for_mvsep_quota_reset`. Submit a 10th job
mid-quiescence and confirm immediate logging resumes.

## Out of Scope

- Changing the QuotaWaiter poller cadence (`SOW_QUOTA_POLL_INTERVAL_SECONDS`)
  — that controls probe frequency, not logging, and is already 3600s.
- Changing the 1s self-check in `wait()` — that drives job resumption
  latency and must stay fast.
- Throttling logs for non-quota-wait steady states (e.g., long-running
  analysis) — out of scope; only the quota-wait quiescent case is addressed.
