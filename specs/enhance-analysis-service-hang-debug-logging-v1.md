# Implementation Plan: Enhance Analysis Service Hang-Debug Logging (v1)

> **Date:** 2026-08-12
> **Branch:** TBD
> **Spec ID:** `enhance-analysis-service-hang-debug-logging-v1`
> **Component:** Analysis Service (`ops/analysis-service/`)
> **Related:**
> - `specs/llm-rate-limit-retry-v4.md` (unified `call_llm_with_retry` origin)
> - `specs/throttle-logs-during-quota-wait-quiescence.md` (periodic logging loop)
> - `specs/component-feature-extraction-perf-v1.md` (component extraction timing)

---

## Problem

The Component Analysis job's **LLM theme/posture classification** step appears to
hang in production. The logs show the step starts, the OpenAI (Neuralwatt) API
responds with HTTP 200, but then the job goes silent — no "Step completed" line
ever appears, and the job stays stuck in `PROCESSING` with stage `extracting`.

### Evidence (from production logs)

```
2026-08-11 23:12:21,862 - sow_analysis.workers.queue - INFO - [job_3bd0d15f4867] Step completed: Component extraction (147.29s)
2026-08-11 23:12:21,862 - sow_analysis.workers.queue - INFO - [job_3bd0d15f4867] Step started: LLM theme/posture classification
2026-08-11 23:12:22,088 - sow_analysis.workers.queue - INFO - [job_61d901d243e6] Step completed: Component extraction (121.45s)
2026-08-11 23:12:22,088 - sow_analysis.workers.queue - INFO - [job_61d901d243e6] Step started: LLM theme/posture classification
2026-08-11 23:12:28,056 - httpx - INFO - [job_3bd0d15f4867] HTTP Request: POST https://api.neuralwatt.com/v1/chat/completions "HTTP/1.1 200 OK"
2026-08-11 23:12:28,087 - httpx - INFO - [job_3bd0d15f4867] HTTP Request: POST https://api.neuralwatt.com/v1/chat/completions "HTTP/1.1 200 OK"
2026-08-11 23:12:28,604 - httpx - INFO - [job_3bd0d15f4867] HTTP Request: POST https://api.neuralwatt.com/v1/chat/completions "HTTP/1.1 200 OK"
    ... silence — no "Step completed" line ever appears ...
```

### Root Causes

**1. No per-component progress logging inside the LLM classification step.**

`ThemeClassifier.classify_components()` (`classifier.py:169`) fans out to
`asyncio.gather()` across all components (`classifier.py:192`), each making an
independent OpenAI API call via `_classify_component_llm()`
(`classifier.py:221`). The `step_timer("LLM theme/posture classification")`
wrapper in `queue.py:1004` logs only a single "Step started" / "Step completed"
pair. If any single per-component call hangs, retries silently, or deadlocks on
the shared semaphore, there is **zero visibility** into which component or
which API call is stuck.

**2. No timeout on the LLM API call.**

`_classify_component_llm()` (`classifier.py:250-256`) calls the OpenAI SDK via
`asyncio.to_thread(self._client.chat.completions.create, ...)` with **no
`asyncio.wait_for` timeout**. The `OpenAI(...)` client at `classifier.py:160`
is constructed without a `timeout=` argument (compare the embedder at
`embedder.py:42` which sets `timeout=60.0`). If the provider hangs or the
network stalls mid-response, the `to_thread` coroutine blocks forever.

**3. ThemeClassifier does NOT use the unified `call_llm_with_retry()`.**

The LRC and ASR-fallback paths use `call_llm_with_retry()` (`llm_rate_limit.py:474`),
which provides: semaphore management, min-interval pacing, 429/5xx retry with
exponential backoff, budget enforcement (`SOW_LLM_RATE_LIMIT_TIMEOUT_SECONDS=1200`),
and detailed per-attempt warning logs. The theme classifier bypasses all of
this — it manually acquires the semaphore (`classifier.py:244`) and sleeps for
the min interval (`classifier.py:246-247`), but has only a **single manual
retry** on JSON parse failure (`_retry_llm_call`, `classifier.py:346`) and **no
retry on transient HTTP errors** (429, 524, etc.). A 429 or Cloudflare 524
propagates immediately and is swallowed by the broad `except Exception`
at `classifier.py:277`.

**4. httpx logger noise masks the signal.**

The `httpx` and `httpcore` loggers are not suppressed in `logging_config.py`
(only `urllib3`, `botocore`, `audio_separator` are — see `logging_config.py:96-103`).
The OpenAI SDK uses httpx internally, so every API call emits a raw
`HTTP Request: POST ... 200 OK` INFO line. These lines reveal nothing about the
model, tokens, latency, or which component — they just clutter the output and
make it hard to spot the *useful* log lines.

**5. No diagnostic details on LLM calls.**

Even when the API call succeeds, there is no log of: model name, token usage
(prompt/completion/total), response size, retry count, or per-call latency.
When something hangs or fails, operators cannot tell whether the provider is
slow, rate-limiting, returning empty responses, or deadlocking.

**6. Other potentially-hanging steps have no sub-step progress either.**

- **Madmom downbeat detection** (`queue.py:971-975`, `run_in_executor`) — a
  CPU-heavy librosa/madmom call with no timeout and no heartbeat.
- **Component extraction** (`queue.py:983-997`, calls `extract_components()` in
  `components.py:1192`) — internally has stage-level INFO logs (global feature
  precompute, identification, per-component feature computation) but no
  heartbeat for long-running sub-stages.
- **Result conversion** (`queue.py:1016-1044`) — trivial today but wrapped in
  `step_timer` for consistency.

---

## Goal

Make every potentially-hanging step in the component analysis pipeline
**observable**: operators should be able to look at the logs and immediately
tell (a) which step is running, (b) how far along it is, (c) how long it has
been running, and (d) for LLM calls, exactly which request is in flight with
what parameters. A hung step should never look like "started and then silence."

**Out of scope (per user direction):**
- R2 client initialization step (`queue.py:929`) — fast, not a hang risk.
- Audio download from R2 step (`queue.py:944`) — already has R2-level logging.
- Hang-detection watchdog — not adding a separate background watchdog; better
  step logging alone is sufficient for now.

---

## Design

### 1. Enhance `step_timer` with optional heartbeat support

**File:** `ops/analysis-service/src/sow_analysis/step_timer.py`

Add an optional `heartbeat_interval` parameter and a `heartbeat()` method
usable inside the `with` block. The heartbeat logs a "still running" line at
INFO level with elapsed time, readable from inside long-running loops.

**New API:**

```python
@dataclass
class StepHeartbeat:
    """Returned by step_timer() for manual heartbeat calls inside the with-block."""
    step_name: str
    start: float
    log: logging.Logger
    _last_beat: float = field(default=0.0, repr=False)

    def heartbeat(self, detail: str = "") -> None:
        """Log a 'still running' heartbeat line. Throttled to heartbeat_interval."""
        now = time.time()
        if now - self._last_beat < self._heartbeat_interval:
            return
        self._last_beat = now
        elapsed = now - self.start
        msg = f"Step heartbeat: {self.step_name} ({elapsed:.1f}s elapsed"
        if detail:
            msg += f" — {detail}"
        msg += ")"
        self.log.info(msg)


@contextmanager
def step_timer(
    step_name: str,
    log: logging.Logger,
    heartbeat_interval: float = 0.0,
) -> Iterator[StepHeartbeat]:
    """Context manager with start/end/elapsed logging and optional heartbeat.

    Args:
        step_name: Human-readable name.
        log: Logger instance.
        heartbeat_interval: If > 0, enable manual heartbeat() calls from inside
            the block. Does NOT auto-beat; callers must call .heartbeat() in
            their loop. Default 0 (disabled for backward compat).
    """
    start = time.time()
    hb = StepHeartbeat(step_name=step_name, start=start, log=log,
                       _heartbeat_interval=heartbeat_interval)
    log.info(f"Step started: {step_name}")
    try:
        yield hb
    except Exception as exc:
        elapsed = time.time() - start
        log.error(f"Step failed: {step_name} ({elapsed:.2f}s) — {exc}")
        raise
    else:
        elapsed = time.time() - start
        log.info(f"Step completed: {step_name} ({elapsed:.2f}s)")
```

**Backward compatibility:** The return type changes from `Iterator[None]` to
`Iterator[StepHeartbeat]`. Existing callers that do `with step_timer(...):`
without capturing the yield value continue to work unchanged. Callers that want
heartbeats do `with step_timer(..., heartbeat_interval=30) as hb:` and call
`hb.heartbeat("processing component 3/8")` inside their loop.

**New config setting** in `config.py`:

```python
SOW_STEP_HEARTBEAT_INTERVAL_SECONDS: float = 30.0
# Default interval for step_timer heartbeats. A step logs a "still running"
# line every this many seconds when it explicitly calls heartbeat(). Set to 0
# to disable. Individual steps may override via the heartbeat_interval param.
```

---

### 2. Migrate ThemeClassifier to `call_llm_with_retry()` + add timeout

**File:** `ops/analysis-service/src/sow_analysis/workers/classifier.py`

**2a. Add timeout to the OpenAI client constructor.**

At `classifier.py:160`, add `timeout=` and `max_retries=0` (we handle retries
ourselves via `call_llm_with_retry`):

```python
self._client = OpenAI(
    api_key=settings.SOW_LLM_API_KEY,
    base_url=settings.SOW_LLM_BASE_URL,
    timeout=settings.SOW_LLM_CLASSIFICATION_TIMEOUT_SECONDS,
    max_retries=0,  # retries handled by call_llm_with_retry
)
```

**New config setting** in `config.py`:

```python
SOW_LLM_CLASSIFICATION_TIMEOUT_SECONDS: float = 60.0
# Per-request timeout for the OpenAI client used by ThemeClassifier.
# This is the SDK-level HTTP timeout (connect + read). The
# call_llm_with_retry() budget (SOW_LLM_RATE_LIMIT_TIMEOUT_SECONDS=1200s)
# is the overall wall-clock ceiling across all retries.
```

**2b. Replace manual semaphore + asyncio.to_thread with `call_llm_with_retry()`.**

In `_classify_component_llm()` (`classifier.py:249-256`), replace:

```python
async with self._llm_semaphore:
    if self._llm_min_interval > 0:
        await asyncio.sleep(self._llm_min_interval)
    try:
        response = await asyncio.to_thread(
            self._client.chat.completions.create, ...
        )
```

with:

```python
from .llm_rate_limit import call_llm_with_retry

def _do_call() -> str:
    response = self._client.chat.completions.create(
        model=self._model,
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        max_tokens=300,
    )
    # Log diagnostics before returning.
    self._log_llm_diagnostics(response, component)
    return response.choices[0].message.content

result = await call_llm_with_retry(
    _do_call,
    description=f"theme/posture classification (comp {component.occurrence_index})",
)
parsed = self._parse_llm_json(result)
```

Do the same in `_retry_llm_call()` (`classifier.py:346-381`) — or better,
eliminate `_retry_llm_call()` entirely since `call_llm_with_retry` now handles
retries on transient errors. The JSON-parse-failure retry (where the API
returned 200 but unparseable JSON) becomes a single re-call wrapped in
`call_llm_with_retry` with a different `description`.

Remove the manual `self._llm_semaphore` acquire and `self._llm_min_interval`
sleep — `call_llm_with_retry` handles both internally (per the v4 spec).

**2c. Remove `_get_llm_semaphore()` and the `self._llm_semaphore` / `self._llm_min_interval` fields.**

These are no longer needed once all calls go through `call_llm_with_retry()`.
The `_get_llm_semaphore()` function (`classifier.py:438-471`) was only used by
`ThemeClassifier`; `llm_rate_limit.py` has its own lazy-init. Remove it.

---

### 3. Add per-component progress logging to `ThemeClassifier`

**File:** `ops/analysis-service/src/sow_analysis/workers/classifier.py`

In `classify_components()` (`classifier.py:169-193`), replace the bare
`asyncio.gather` with indexed tasks that log per-component start/finish:

```python
async def classify_components(self, components, lrc_content=None):
    total = len(components)
    logger.info(f"LLM classification: {total} components to classify")

    tasks = []
    for i, comp in enumerate(components, 1):
        lyrics_lines = None
        if lrc_content and comp.start_time is not None and comp.end_time is not None:
            lyrics_lines = _extract_lyrics_for_component(
                lrc_content, comp.start_time, comp.end_time
            )
        tasks.append(self._classify_component_with_logging(i, total, comp, lyrics_lines))

    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Log any exceptions that gather captured.
    for i, result in enumerate(results, 1):
        if isinstance(result, Exception):
            logger.warning(
                f"LLM classification: component {i}/{total} failed: {result}"
            )
    return components
```

Add a wrapper method `_classify_component_with_logging`:

```python
async def _classify_component_with_logging(self, idx, total, component, lyrics_lines):
    comp_label = f"component {idx}/{total} (occurrence={component.occurrence_index}, type={component.component_type})"
    logger.info(f"LLM classification: starting {comp_label}")
    start = time.time()
    try:
        await self.classify_component(component, lyrics_lines)
        elapsed = time.time() - start
        logger.info(
            f"LLM classification: completed {comp_label} ({elapsed:.2f}s, "
            f"theme={component.theme}, posture={component.vocal_posture})"
        )
    except Exception as e:
        elapsed = time.time() - start
        logger.warning(f"LLM classification: failed {comp_label} ({elapsed:.2f}s): {e}")
        # classify_component already has its own try/except inside _classify_component_llm,
        # so this is a safety net.
```

This gives operators lines like:

```
LLM classification: 8 components to classify
LLM classification: starting component 3/8 (occurrence=2, type=chorus)
LLM classification: completed component 3/8 (occurrence=2, type=chorus) (5.12s, theme=讚美, posture=To God)
```

---

### 4. Add LLM call diagnostics logging

**File:** `ops/analysis-service/src/sow_analysis/workers/classifier.py`

Add a `_log_llm_diagnostics` method to `ThemeClassifier`:

```python
def _log_llm_diagnostics(self, response, component) -> None:
    """Log diagnostic details for an LLM API call at DEBUG level.

    Logs: model, token usage (prompt/completion/total), response length,
    finish_reason, and component identifier. At INFO level, logs a one-line
    summary with latency and tokens.
    """
    usage = getattr(response, "usage", None)
    model = getattr(response, "model", self._model)
    finish_reason = getattr(response.choices[0], "finish_reason", "unknown")
    content_len = len(response.choices[0].message.content or "")

    prompt_tokens = getattr(usage, "prompt_tokens", "?") if usage else "?"
    completion_tokens = getattr(usage, "completion_tokens", "?") if usage else "?"
    total_tokens = getattr(usage, "total_tokens", "?") if usage else "?"

    logger.debug(
        f"LLM response: model={model}, tokens={prompt_tokens}/{completion_tokens}/{total_tokens} "
        f"(prompt/completion/total), content_len={content_len}, finish={finish_reason}, "
        f"component={component.occurrence_index}"
    )
```

Diagnostics are DEBUG by default (configurable via `SOW_LOG_LEVEL=DEBUG`).
The INFO-level per-component log from section 3 already provides visibility
during normal operation.

---

### 5. Suppress httpx/httpcore logs, add our own

**File:** `ops/analysis-service/src/sow_analysis/logging_config.py`

In `configure_logging()`, within the `suppress_external` block
(`logging_config.py:96-103`), add:

```python
# OpenAI SDK uses httpx internally; its INFO logs ("HTTP Request: POST ...")
# are noisy and lack model/token/latency detail. We log our own diagnostics
# in classifier.py (_log_llm_diagnostics) and llm_rate_limit.py (retry warnings).
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)
```

Our own diagnostics (sections 3 and 4) replace the raw httpx lines with more
informative, job-context-prefixed log entries.

---

### 6. Add heartbeat logging to long-running non-LLM steps

Per user direction, cover these steps (skip R2 init and audio download):

**6a. Madmom downbeat detection** (`queue.py:971-975`)

This is a single `run_in_executor` call — we can't heartbeat *inside* it, but
we can add a heartbeat note. Since it's a single blocking call, the heartbeat
approach doesn't apply. Instead, add sub-step logging inside
`_detect_downbeats_madmom` (if it has identifiable sub-stages) or at minimum
log the audio duration before the call so operators know what to expect.

Minimal change — log the input size before the call:

```python
with step_timer("Madmom downbeat detection", logger) as hb:
    audio_size_mb = audio_path.stat().st_size / (1024 * 1024)
    logger.info(f"Madmom: processing {audio_size_mb:.1f}MB audio file")
    loop = asyncio.get_event_loop()
    madmom_downbeats = await loop.run_in_executor(
        None, _detect_downbeats_madmom, audio_path
    )
```

**6b. Component extraction** (`queue.py:983-997` → `components.py:1192`)

`extract_components()` already has internal stage-level INFO logs (global
feature precompute at `:1256`, identification at `:1302`, per-component features
at `:1329`). Add a `settings.SOW_STEP_HEARTBEAT_INTERVAL_SECONDS` heartbeat
inside the per-component feature computation loop (`components.py:1325-1328`):

```python
features_start = time.time()
last_heartbeat = time.time()
for i, component in enumerate(components, 1):
    compute_component_features(gf, component, beats=beats, downbeats=downbeats)
    now = time.time()
    if now - last_heartbeat >= settings.SOW_STEP_HEARTBEAT_INTERVAL_SECONDS:
        elapsed = now - features_start
        logger.info(
            f"Feature computation heartbeat: {i}/{len(components)} components "
            f"done ({elapsed:.1f}s elapsed)"
        )
        last_heartbeat = now
```

This is the most likely place to hang (librosa hpss + chroma_cqt per
component). The heartbeat tells operators which component is processing and
how far along we are.

**6c. Result conversion** (`queue.py:1016-1044`)

This is a trivial list comprehension today — no heartbeat needed. The existing
`step_timer` wrapper is sufficient. No change.

---

## Files Changed Summary

| File | Change |
|---|---|
| `step_timer.py` | Add `StepHeartbeat` dataclass + `heartbeat_interval` param to `step_timer()` |
| `config.py` | Add `SOW_STEP_HEARTBEAT_INTERVAL_SECONDS` (30.0) and `SOW_LLM_CLASSIFICATION_TIMEOUT_SECONDS` (60.0) |
| `logging_config.py` | Suppress `httpx`, `httpcore`, `openai` loggers to WARNING |
| `classifier.py` | (a) Add `timeout=` + `max_retries=0` to OpenAI client; (b) migrate `_classify_component_llm` and `_retry_llm_call` to `call_llm_with_retry()`; (c) remove `_get_llm_semaphore()` + `self._llm_semaphore` + `self._llm_min_interval`; (d) add per-component progress logging in `classify_components()`; (e) add `_log_llm_diagnostics()` method |
| `queue.py` | Add audio-size log before madmom call (`:971`); no change to LLM step wrapper (step_timer already wraps it) |
| `components.py` | Add heartbeat inside per-component feature computation loop (`:1325`) |

---

## Expected Log Output (After)

```
[job_3bd0d15f4867] Step completed: Component extraction (147.29s)
[job_3bd0d15f4867] Step started: LLM theme/posture classification
[job_3bd0d15f4867] LLM classification: 8 components to classify
[job_3bd0d15f4867] LLM classification: starting component 1/8 (occurrence=0, type=intro)
[job_3bd0d15f4867] LLM classification: starting component 2/8 (occurrence=1, type=verse)
[job_3bd0d15f4867] LLM classification: starting component 3/8 (occurrence=1, type=chorus)
[job_3bd0d15f4867] LLM classification: completed component 2/8 (occurrence=1, type=verse) (4.12s, theme=祈禱, posture=To God)
[job_3bd0d15f4867] LLM classification: completed component 1/8 (occurrence=0, type=intro) (5.85s, theme=讚美, posture=About God)
[job_3bd0d15f4867] LLM classification: completed component 3/8 (occurrence=1, type=chorus) (6.01s, theme=敬拜, posture=To God)
...
[job_3bd0d15f4867] Step completed: LLM theme/posture classification (48.23s)
```

If the provider hangs, you now see exactly which component is stuck and for
how long. If a 429 or 524 occurs, `call_llm_with_retry` logs the retry with
attempt count, backoff, and remaining budget — instead of silently swallowing it.

No more raw `httpx INFO HTTP Request: POST ... 200 OK` lines cluttering the output.

---

## Testing Notes

1. **Unit tests for `step_timer` heartbeat:**
   - Test that heartbeat() respects `heartbeat_interval` throttle.
   - Test that backward-compat (no `heartbeat_interval`, no captured yield) works.
   - Test that exception path still logs "Step failed" with elapsed time.

2. **Unit tests for `ThemeClassifier`:**
   - Mock `call_llm_with_retry` and verify it's called with correct `description`.
   - Verify per-component logging fires for each component.
   - Verify `_log_llm_diagnostics` extracts usage/model from a mock response.
   - Verify that `_get_llm_semaphore()` is removed and no code references it.

3. **Integration test:**
   - Submit a component analysis job with `classify_theme=True` and verify the
     full log sequence appears (step started → N components → per-component
     completed → step completed).
   - Set `SOW_LLM_CLASSIFICATION_TIMEOUT_SECONDS=1` to force a timeout and
     verify it's logged, not silently swallowed.

4. **Lint/typecheck:**
   ```bash
   cd ops/analysis-service && uv run --extra dev pytest tests/ -v
   ```

---

## Migration Notes

- **No DB migration** — purely logging + LLM call behavior changes.
- **Config additions** are backward-compatible (new settings with defaults).
- **`call_llm_with_retry` migration** changes retry behavior for the better:
  previously a single 429 or 524 would fail classification silently; now it
  retries with backoff up to the budget. Classification failures still don't
  fail the job (the `try/except` at `queue.py:1013-1014` remains).
- **httpx suppression** is safe — we add our own richer diagnostics. If
  operators need raw HTTP logs for debugging, they can set
  `SOW_LOG_LEVEL=DEBUG` (httpx logs at DEBUG, our diagnostics at INFO/DEBUG).
  Actually — httpx set to WARNING means even DEBUG won't show them. Document
  the env override: set `httpx` logger to INFO via a Python startup hook if
  raw HTTP logs are needed for provider debugging.
