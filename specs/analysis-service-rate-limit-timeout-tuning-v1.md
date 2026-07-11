# Analysis Service: Rate Limiting & Timeout Tuning

## Context

Analysis of `sow_analysis_4_filtered_by_script.log` (865 lines, one batch run on
2026-07-11) reveals three categories of unnecessary warnings, failures, and wasted
time caused by misconfigured concurrency limits, missing retry coverage for transient
proxy errors, and suboptimal backoff scheduling.

### Quantitative Summary from the Log

| Metric | Count | Impact |
|--------|-------|--------|
| MVSEP "queue full" warnings | 36 | 18 retry waits totaling ~2000s+ of wasted wall-clock time |
| YouTube SSL error warnings (urllib3) | 44 | 7 direct-fetch failures, 3 list-fallback failures |
| YouTube transcript path FAILED → ASR fallback | 3 jobs | Each took 894s–1312s (vs 50–640s for succeeded YouTube path); each also triggered an unnecessary stem separation job |
| LLM 429 errors | 0 | LLM rate limiting is healthy; no tuning needed |

---

## Issue 1: MVSEP `max_concurrent` Deployment Override Still Set to 3

**Severity: HIGH** — Eliminates ~36 warnings and ~2000s of wasted retry waits per batch.

### Problem

`config.py` already defaults `SOW_MVSEP_MAX_CONCURRENT = 1` (line 159), and the
prior spec `specs/mvsep-serialize-stage1-submissions-v1.md` already recommended
this change for the Python default. However, the **deployment configurations**
still override it to 3:

- `ops/analysis-service/docker-compose.yml` line 38:
  `SOW_MVSEP_MAX_CONCURRENT: ${SOW_MVSEP_MAX_CONCURRENT:-3}`
- `ops/analysis-service/DEPLOYMENT.md` line 429:
  `SOW_MVSEP_MAX_CONCURRENT: ${SOW_MVSEP_MAX_CONCURRENT:-3}`

The startup configuration table in the log confirms the deployed value is 3:
```
| MVSEP | max_concurrent | 3 |
```

MVSEP's free-tier API allows only **1 pending job per token**. With
`max_concurrent=3`, three concurrent stem-separation jobs all submit
simultaneously. Only the first succeeds; the other two receive HTTP 400:

```
MVSEP queue full: {"success":false,"errors":["You already have unprocessed file
in queue. Please wait before adding new file!"]}
```

The exponential backoff (30s base, 2x factor, 300s cap, ±20% jitter) eventually
recovers, but wastes 27–282 seconds per collision. In this log: 18 retry waits
ranging from 27s to 282s.

Also affects **Stage 2** (de-reverb): when two jobs reach Stage 2 simultaneously,
they also collide. The log shows Stage 2 reaching attempt 4 and 5 in multiple
jobs (lines 270, 304, 730, 745, 753, 845).

### Evidence (representative log lines)

- Line 77–79: First queue-full collision (attempt 1, 32s wait)
- Line 89–91: Second collision on same job (attempt 2, 62s wait)
- Line 270–271: Stage 2 attempt 4, 206s wait
- Line 753–754: Stage 2 attempt 4, 282s wait
- Stem separation durations: 342s, 476s, 714s (much of which is retry waiting)

### Fix

Change the deployment default from 3 to 1 in:

| File | Line | Current | New |
|------|------|---------|-----|
| `ops/analysis-service/docker-compose.yml` | 38 | `SOW_MVSEP_MAX_CONCURRENT: ${SOW_MVSEP_MAX_CONCURRENT:-3}` | `SOW_MVSEP_MAX_CONCURRENT: ${SOW_MVSEP_MAX_CONCURRENT:-1}` |
| `ops/analysis-service/DEPLOYMENT.md` | 429 | `SOW_MVSEP_MAX_CONCURRENT: ${SOW_MVSEP_MAX_CONCURRENT:-3}` | `SOW_MVSEP_MAX_CONCURRENT: ${SOW_MVSEP_MAX_CONCURRENT:-1}` |

With `max_concurrent=1`, the `MvsepClient` singleton's `asyncio.Semaphore(1)`
serializes all MVSEP submit+poll+download calls across both stages and all jobs.
The existing exponential backoff remains as a safety net for edge cases (e.g.,
external traffic on the same API token).

### What Does NOT Change

- `config.py` default (already 1)
- Semaphore mechanism or scope
- Exponential backoff constants (`QUEUE_FULL_BACKOFF_*`)
- Queue dispatch model

---

## Issue 2: YouTube Transcript SSL Errors Not Retried at Application Level

**Severity: HIGH** — Prevents 3 unnecessary ASR fallbacks per batch (~4000s+ wasted
compute) and 44 urllib3 warnings.

### Problem

The YouTube transcript path fetches captions via the `youtube-transcript-api`
library through a Decodo rotating residential proxy
(`gate.decodo.com:10003`). The proxy intermittently returns SSL-level errors:

```
SSLError(1, '[SSL: BAD_EXTENSION] bad extension (_ssl.c:1016)')
SSLError(1, '[SSL: WRONG_VERSION_NUMBER] wrong version number (_ssl.c:1016)')
SSLError(1, '[SSL] record layer failure (_ssl.c:2590)')
```

These are **transient** — they occur when the proxy's current exit IP has a TLS
handshake issue. The `RotatingProxyConfig` sets
`prevent_keeping_connections_alive=True` so the next request gets a new IP,
but `urllib3`'s built-in retries (3 attempts) happen too rapidly and often
hit the same bad IP.

The application-level rate limiter (`_YouTubeRateLimiter.call()`) only retries
on HTTP 429 (`_is_rate_limited_error()`). SSL errors are classified as
non-429, so they propagate immediately — the caller then falls through to
the list fallback (consuming another rate-limiter slot), and if that also
fails, the entire YouTube transcript path is abandoned and the job falls
back to expensive LLM-based ASR.

### Impact Chain (from the log)

3 jobs hit this failure chain:

1. **job_d4921796b0e9** (video: `-KdJMCUafxA`): Direct fetch SSL fail → list
   fallback also failed (SSL) → YouTube path FAILED → ASR fallback → stem
   separation (797s) + DashScope ASR + LLM alignment → LRC job total: **1312s**
2. **job_655de386d76c** (video: `9vkWTRP-x_c`): Same chain → LRC job total:
   **894s**
3. **job_01e72c201cf4** (video: `nOG3gLLoVMw`): Same chain → LRC job total:
   **1171s**

If the SSL errors had been retried at the application level with a short delay
(allowing IP rotation), these jobs would likely have succeeded via the YouTube
path (~50–300s each) instead of requiring ASR fallback (~900–1300s each).

### Fix

Add SSL error detection and retry to `_YouTubeRateLimiter` in
`ops/analysis-service/src/sow_analysis/workers/youtube_transcript.py`.

#### 2a. Add SSL error detection function

Add a `_is_transient_connection_error()` function alongside the existing
`_is_rate_limited_error()`:

```python
def _is_transient_connection_error(e: Exception) -> bool:
    """Check if an exception is a transient connection-level error (SSL, network).

    These errors are common with rotating residential proxies where individual
    exit IPs may have TLS handshake issues. They are transient — the next
    request through a different IP typically succeeds.

    Detects:
    - SSLError from urllib3/requests (via type name and message)
    - ConnectionError variants
    - "Max retries exceeded" caused by SSL/connection failures
    """
    visited: set[int] = set()
    exc: Optional[Exception] = e
    ssl_markers = ("ssl", "bad_extension", "wrong_version_number",
                   "record layer failure", "tls")
    conn_markers = ("connection reset", "connection aborted",
                    "broken pipe", "read timed out")
    while exc is not None and id(exc) not in visited:
        visited.add(id(exc))
        type_name = type(exc).__name__.lower()
        if "ssl" in type_name or "connectionerror" in type_name:
            return True
        error_str = str(exc).lower()
        if any(m in error_str for m in ssl_markers):
            return True
        if any(m in error_str for m in conn_markers):
            return True
        exc = getattr(exc, "__cause__", None) or getattr(exc, "__context__", None)
    return False
```

#### 2b. Extend the retry loop in `_YouTubeRateLimiter.call()`

In the `except Exception as e:` block of `call()`, after the existing
`_is_rate_limited_error(e)` check, add a branch for transient connection
errors:

```python
except Exception as e:
    if _is_rate_limited_error(e):
        # ... existing 429 retry logic ...
    elif _is_transient_connection_error(e) and attempt < max_retries:
        # SSL/connection errors from rotating proxy — retry with backoff
        delay = min(base_delay * (2 ** attempt), 30.0)
        delay += random.uniform(0, delay * 0.25)
        logger.info(
            "YouTube API transient connection error for %s, "
            "attempt %d/%d, retrying in %.1fs: %s",
            description, attempt + 1, max_retries + 1, delay, e,
        )
        await asyncio.sleep(delay)
        continue
    else:
        # Non-retriable error — propagate immediately
        raise
```

**Key design choices:**
- Reuse the same `max_retries` and `base_delay` as 429 retries (already
  configurable via `SOW_YOUTUBE_TRANSCRIPT_MAX_RETRIES` /
  `SOW_YOUTUBE_TRANSCRIPT_RETRY_BASE_DELAY`).
- Cap single-retry delay at 30s (not 60s like 429) since SSL errors
  resolve faster with IP rotation.
- Do **not** increment `_consecutive_429_count` or open the circuit breaker
  for SSL errors — they are proxy-level, not YouTube-level rate limiting.
- The `prevent_keeping_connections_alive=True` setting on the proxy config
  ensures each retry gets a new IP.

#### 2c. Add new config setting (optional)

Add `SOW_YOUTUBE_TRANSCRIPT_SSL_RETRY_BASE_DELAY: float = 5.0` to `config.py`
if separate tuning is desired, or reuse the existing
`SOW_YOUTUBE_TRANSCRIPT_RETRY_BASE_DELAY`.

**Recommendation:** Reuse the existing setting to avoid config bloat. The
existing default (5s base, 10s in free-mode) is appropriate for SSL retries.

### Files to Modify

| File | Action |
|------|--------|
| `ops/analysis-service/src/sow_analysis/workers/youtube_transcript.py` | Add `_is_transient_connection_error()`; extend retry loop in `_YouTubeRateLimiter.call()` |
| `ops/analysis-service/tests/test_youtube_transcript.py` | Add tests for SSL error retry behavior |

### What Does NOT Change

- Circuit breaker logic (SSL errors don't trip it)
- Min-interval throttle
- Concurrency semaphore
- Proxy configuration or `RotatingProxyConfig`

---

## Issue 3: YouTube Direct-Fetch → List-Fallback Wastes Rate-Limiter Slots

**Severity: MEDIUM** — Reduces redundant proxy requests and rate-limiter contention.

### Problem

When the direct fetch fails (e.g., SSL error), `fetch_youtube_transcript()`
immediately calls `_rate_limiter.call()` again for the list fallback (line 647).
This consumes a second min-interval throttle slot (30s in free mode) and makes
another proxy request that may hit the same transient SSL issue.

In the log, 7 direct-fetch failures led to 7 list-fallback attempts, of which
4 also hit SSL errors. The list fallback typically succeeds when the SSL error
was truly transient (4 out of 7 succeeded), but the 3 that also failed wasted
additional time.

### Fix

Add a short delay (2–3 seconds) between the direct-fetch failure and the
list-fallback attempt to allow proxy IP rotation. This is simpler than a
structural refactor and avoids consuming a second full min-interval slot.

In `fetch_youtube_transcript()` (around line 643):

```python
except Exception as e:
    logger.error(f"Direct fetch failed for {video_id}, trying list fallback: {e}")
    # Brief delay to allow proxy IP rotation before list fallback
    await asyncio.sleep(2.0)
```

**Note:** This is a minor optimization. The primary fix for Issue 2 (SSL retry
at the rate-limiter level) will prevent most direct-fetch failures from
reaching this fallback path.

### Files to Modify

| File | Action |
|------|--------|
| `ops/analysis-service/src/sow_analysis/workers/youtube_transcript.py` | Add `await asyncio.sleep(2.0)` before list fallback |

---

## Issue 4: MVSEP Polling Initial Interval Could Be Longer (Low Priority)

**Severity: LOW** — Minor optimization to reduce HTTP request count.

### Problem

MVSEP `_poll_job()` starts polling at 5s intervals, growing by 1.5x to a max
of 30s. Looking at the log, MVSEP typically takes 30–90 seconds for Stage 1
and 30–60 seconds for Stage 2. The initial 5s polls return "still processing"
and are wasted requests.

### Fix (Optional)

Change `poll_interval` initial value from 5.0 to 8.0 in
`ops/analysis-service/src/sow_analysis/services/mvsep_client.py` (line 306).

This reduces ~2–3 unnecessary poll requests per stage per job with no impact
on latency (the first poll at 8s still catches fast completions).

**Priority:** Low. Not worth implementing unless we want to reduce MVSEP API
request count. No failures or warnings are caused by the current 5s interval.

---

## No Changes Needed

### LLM Rate Limiting (`SOW_LLM_MAX_CONCURRENT = 3`)

The log shows **zero LLM 429 errors**. All `POST
https://api.neuralwatt.com/v1/chat/completions` requests returned HTTP 200.
The 3-slot semaphore successfully prevented self-inflicted rate limits. No
tuning needed.

### MVSEP Timeouts

No timeouts triggered. Stage timeout (300s) and Stage 2 timeout (900s) were
never hit. Total timeout (1800s) was never reached. Current values are adequate.

### MVSEP Retry Counts

`MVSEP_QUEUE_FULL_MAX_RETRIES = 6` — No job exhausted all 6 retries. The
longest retry chain was 5 attempts (line 304, 845). Once Issue 1 is fixed
(max_concurrent=1), retry counts will rarely exceed 1–2.

### Queue Start Delay

`queue_start_delay = 30s` — working as designed (cancellation window).

### DashScope ASR Timeouts

`SOW_DASHSCOPE_ASR_TIMEOUT_SECONDS = 300` and
`SOW_DASHSCOPE_ASR_FILETRANS_TIMEOUT_SECONDS = 1800` — No DashScope timeouts
in the log. ASR filetrans calls completed in ~10–16s.

---

## Implementation Order

1. **Issue 1** (MVSEP deployment default) — 2-file change, immediate impact
2. **Issue 2** (YouTube SSL retry) — Core fix, ~50 lines of new code
3. **Issue 3** (List fallback delay) — 1-line change, complements Issue 2
4. **Issue 4** (MVSEP poll interval) — Optional, low priority

## Verification

```bash
cd ops/analysis-service

# Run YouTube transcript tests (Issue 2/3)
uv run --extra dev pytest tests/test_youtube_transcript.py -v

# Run MVSEP client tests (Issue 1 — unchanged, but verify no regression)
uv run --extra dev pytest tests/test_mvsep_client.py -v

# Run full test suite
uv run --extra dev pytest tests/ -v
```

After deploying Issue 1, verify the startup configuration table shows
`MVSEP max_concurrent = 1` and that queue-full warnings are eliminated (except
rare edge cases from external token contention).

After deploying Issue 2, verify that SSL errors from the proxy no longer
cause immediate fallback to ASR — they should be retried with backoff and
typically succeed on the 2nd or 3rd attempt.
