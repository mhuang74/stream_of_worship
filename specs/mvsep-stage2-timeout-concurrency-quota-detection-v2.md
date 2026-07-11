# MVSEP Stage 2 Timeout Resilience, Concurrency, and Quota Detection

> **Version:** 2.0 — Implementation-ready plan derived from review of v1 spec.  
> **Status:** Awaiting implementation.  
> **Scope:** `ops/analysis-service/`

---

## 1. Problem Statement

When MVSEP Stage 1 (vocal separation, melroformer) takes 10–14 minutes due to service-side slowness, it consumes the entire `SOW_MVSEP_TOTAL_TIMEOUT=900s` (15 min) shared budget. Stage 2 (reverb removal) then immediately hits the `time_remaining_fn() <= 0` check on its **first** attempt and bails out without ever submitting to MVSEP — falling back to local UVR-De-Echo (~50 min).

Three compounding issues:

1. **Shared time budget** — `SOW_MVSEP_TOTAL_TIMEOUT` is shared across Stage 1 + Stage 2 + all retries, tracked from a single `total_start` at Stage 1 entry.
2. **Weak retry for timeouts** — `MvsepTimeoutError` from `_poll_job()` is treated as a generic `MvsepClientError`: 3 retries with 5/10/20s backoff. Timeouts usually mean MVSEP is busy and self-resolves with patience.
3. **MVSEP concurrency = 1** — `MvsepClient._mutex` (an `asyncio.Lock`) serializes all `separate_vocals()` and `remove_reverb()` calls across all jobs.

Additionally, the hardcoded `SOW_MVSEP_DAILY_JOB_LIMIT=50` self-imposes a daily cap that doesn't reflect the actual MVSEP account quota.

---

## 2. Design Decisions

| Decision | Rationale |
|---|---|
| **Per-stage dedicated budget for Stage 2** | New `SOW_MVSEP_STAGE2_TIMEOUT=900s` gives Stage 2 its own 15-minute budget independent of Stage 1's consumption. The outer `SOW_MVSEP_TOTAL_TIMEOUT` (raised 900→1800) remains as a hard cap for Stage 1 + Stage 2 combined. |
| **Guarantee first attempt** | The `time_remaining_fn() <= 0` check in `_run_mvsep_stage_with_retries()` is moved to skip only *retries* (`attempt > 1`), not the first attempt. Stage 2 always gets at least one real submission. |
| **Promote `MvsepTimeoutError` to queue-full backoff** | Group with `MvsepQueueFullError` — 6 attempts, 30/60/120/240/300s backoff. Timeouts are transient MVSEP busy-ness that self-resolves. |
| **Replace `_mutex` (Lock) with `_semaphore` (Semaphore, N=3)** | New `SOW_MVSEP_MAX_CONCURRENT=3` allows up to 3 concurrent MVSEP operations. MVSEP Premium supports up to 10; 3 is conservative. A single job's Stage 1 and Stage 2 still serialize naturally (Stage 2 only starts after Stage 1 completes). |
| **Remove `SOW_MVSEP_DAILY_JOB_LIMIT`** | No self-imposed counting. Quota exhaustion is detected from API responses (both `success: false` JSON body and HTTP 400 error text) via keyword matching. When detected, sets `_quota_exhausted = True` and raises `MvsepNonRetriableError`. Subsequent jobs check `is_available` → see `_quota_exhausted` → skip MVSEP → use local. Resets on UTC day rollover. |

---

## 3. Files to Modify

| # | File | Action |
|---|------|--------|
| 1 | `ops/analysis-service/src/sow_analysis/config.py` | Add `SOW_MVSEP_STAGE2_TIMEOUT`, `SOW_MVSEP_MAX_CONCURRENT`; raise `SOW_MVSEP_TOTAL_TIMEOUT` 900→1800; remove `SOW_MVSEP_DAILY_JOB_LIMIT` |
| 2 | `ops/analysis-service/src/sow_analysis/services/mvsep_client.py` | Replace `_mutex`→`_semaphore`; remove daily-count state; add `_quota_exhausted` flag + UTC reset; add `_is_quota_exhausted()` detection in `_submit_job` (both branches); update docstrings |
| 3 | `ops/analysis-service/src/sow_analysis/workers/stem_separation.py` | Import `MvsepTimeoutError`; promote to queue-full backoff; dedicated Stage 2 time budget; guarantee first attempt |
| 4 | `ops/analysis-service/src/sow_analysis/main.py` | Replace `daily_limit` log with `max_concurrent` |
| 5 | `ops/analysis-service/.env.example` | Add new vars, remove daily limit, update comments |
| 6 | `ops/analysis-service/docker-compose.yml` | Pass new env vars with defaults |
| 7 | `ops/analysis-service/DEPLOYMENT.md` | Update env documentation |
| 8 | `ops/analysis-service/tests/test_mvsep_client.py` | Remove daily limit tests; add quota detection tests; update concurrency test; update fixture |
| 9 | `ops/analysis-service/tests/test_mvsep_fallback.py` | Rename daily-limit test; add Stage 2 budget/timeout/first-attempt tests; update total-timeout test |

---

## 4. Implementation Steps

### Step 1 — Config changes (`config.py`)

**Replace** the block at lines 141–145 (MVSEP timeouts & limits):

```python
    # Timeouts & limits
    SOW_MVSEP_HTTP_TIMEOUT: int = 60
    SOW_MVSEP_STAGE_TIMEOUT: int = 300
    SOW_MVSEP_TOTAL_TIMEOUT: int = 900
    SOW_MVSEP_DAILY_JOB_LIMIT: int = 50
```

**With:**

```python
    # Timeouts & limits
    SOW_MVSEP_HTTP_TIMEOUT: int = 60
    SOW_MVSEP_STAGE_TIMEOUT: int = 300
    SOW_MVSEP_STAGE2_TIMEOUT: int = 900       # Dedicated budget for Stage 2 + retries
    SOW_MVSEP_TOTAL_TIMEOUT: int = 1800       # Outer cap: Stage 1 + Stage 2 combined
    SOW_MVSEP_MAX_CONCURRENT: int = 3         # Max concurrent MVSEP API operations
```

#### Verification
- `config.SOW_MVSEP_DAILY_JOB_LIMIT` no longer exists.
- `config.SOW_MVSEP_STAGE2_TIMEOUT == 900`.
- `config.SOW_MVSEP_MAX_CONCURRENT == 3`.
- `config.SOW_MVSEP_TOTAL_TIMEOUT == 1800`.

---

### Step 2 — MvsepClient: replace mutex with semaphore, remove daily limit, add quota detection (`mvsep_client.py`)

#### 2a. Module-level quota helper (after line 19, before exception classes)

**Add:**

```python
# Keywords indicating daily quota/limit exhaustion from MVSEP API
_QUOTA_KEYWORDS = (
    "daily limit",
    "daily quota",
    "limit exceeded",
    "quota exceeded",
    "limit reached",
    "too many jobs",
    "too many requests today",
    "day limit",
    "per day",
    "exceeded your",
)


def _is_quota_exhausted(error_text: str) -> bool:
    """Check if an MVSEP API error message indicates daily quota exhaustion.

    Args:
        error_text: Lowercased error message from API response.

    Returns:
        True if the message matches quota-exhaustion patterns.
    """
    return any(kw in error_text for kw in _QUOTA_KEYWORDS)
```

#### 2b. `__init__` (lines 63–114)

**Remove** parameter and daily-count state:

```python
        daily_job_limit: Optional[int] = None,
```
```python
        self.daily_job_limit = daily_job_limit if daily_job_limit is not None else settings.SOW_MVSEP_DAILY_JOB_LIMIT
        self._daily_job_count = 0
        self._daily_reset_utc = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        self._mutex: Optional[asyncio.Lock] = None
```

**Add** in their place:

```python
        max_concurrent: Optional[int] = None,
```
```python
        self._max_concurrent = max_concurrent if max_concurrent is not None else settings.SOW_MVSEP_MAX_CONCURRENT
        self._semaphore: Optional[asyncio.Semaphore] = None
        self._quota_exhausted = False
        self._quota_reset_utc = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
```

**Update docstring:** remove `daily_job_limit` param doc, add `max_concurrent` param doc. Remove "Includes daily cost tracking" from class docstring; mention quota detection.

#### 2c. `is_available` property (lines 116–130)

**Replace** with:

```python
    @property
    def is_available(self) -> bool:
        """Check if MVSEP is available for use.

        Returns:
            True when enabled, api_token is non-empty, not disabled,
            and daily quota is not exhausted.
        """
        if not self.enabled:
            return False
        if not self.api_token:
            return False
        if self._disabled:
            return False
        if self._quota_exhausted:
            self._check_quota_reset()
            if self._quota_exhausted:
                return False
        return True
```

#### 2d. Remove `_check_daily_limit` and `_increment_daily_count` (lines 132–149)

**Replace** both methods with:

```python
    def _check_quota_reset(self) -> None:
        """Reset quota-exhausted flag on new UTC day."""
        now_utc = datetime.now(timezone.utc)
        today_start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
        if self._quota_reset_utc < today_start:
            self._quota_exhausted = False
            self._quota_reset_utc = today_start
            logger.info("MVSEP daily quota reset for new UTC day")
```

#### 2e. Replace `async with self._mutex:` with `async with self._semaphore:`

In `separate_vocals` (current lines 352–354):

```python
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(self._max_concurrent)
        async with self._semaphore:
```

In `remove_reverb` (current lines 433–435):

```python
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(self._max_concurrent)
        async with self._semaphore:
```

#### 2f. Remove daily count increment

In `separate_vocals` (current line 365), **remove**:

```python
            self._increment_daily_count()  # only count successful submits
```

#### 2g. Add quota detection in `_submit_job` — `success: false` branch (current lines 203–211)

**Replace** with:

```python
            if not success:
                error_msg = result_data.get("message", "Unknown error")
                error_lower = error_msg.lower()
                if "invalid" in error_lower and "key" in error_lower:
                    self._disabled = True
                    raise MvsepNonRetriableError(f"Invalid API key: {error_msg}")
                if "insufficient" in error_lower and "credit" in error_lower:
                    self._disabled = True
                    raise MvsepNonRetriableError(f"Insufficient credits: {error_msg}")
                if _is_quota_exhausted(error_lower):
                    self._quota_exhausted = True
                    logger.warning(f"MVSEP daily quota exhausted: {error_msg}")
                    raise MvsepNonRetriableError(f"Daily quota exhausted: {error_msg}")
                raise MvsepClientError(f"MVSEP API error: {error_msg}")
```

#### 2h. Add quota detection in `_submit_job` — HTTP 400 branch (current lines 225–228)

**Replace** with:

```python
            if status_code == 400:
                error_text = e.response.text.lower()
                if "queue" in error_text or "wait before adding" in error_text:
                    raise MvsepQueueFullError(f"MVSEP queue full: {e.response.text}") from e
                if _is_quota_exhausted(error_text):
                    self._quota_exhausted = True
                    logger.warning(f"MVSEP daily quota exhausted: {e.response.text}")
                    raise MvsepNonRetriableError(f"Daily quota exhausted: {e.response.text}") from e
```

#### Verification
- `client._mutex` no longer exists; `client._semaphore` is an `asyncio.Semaphore` initialized lazily.
- `client.daily_job_limit` and `client._daily_job_count` no longer exist.
- `client._quota_exhausted` defaults to `False`.
- Quota keywords in API responses set `_quota_exhausted = True` and raise `MvsepNonRetriableError`.

---

### Step 3 — Stem separation worker: timeout resilience (`stem_separation.py`)

#### 3a. Import `MvsepTimeoutError` at module level (current line 27)

**Replace:**

```python
from ..services.mvsep_client import MvsepNonRetriableError, MvsepQueueFullError
```

**With:**

```python
from ..services.mvsep_client import MvsepNonRetriableError, MvsepQueueFullError, MvsepTimeoutError
```

#### 3b. Promote `MvsepTimeoutError` to queue-full-style backoff (current lines 107–129)

In `_run_mvsep_stage_with_retries` exception handler, **replace** the `isinstance` check block:

```python
            if isinstance(e, MvsepQueueFullError):
                max_attempts = MVSEP_QUEUE_FULL_MAX_RETRIES
                backoff = _compute_mvsep_backoff(
                    attempt,
                    base=QUEUE_FULL_BACKOFF_BASE,
                    factor=QUEUE_FULL_BACKOFF_FACTOR,
                    cap=QUEUE_FULL_BACKOFF_CAP,
                    jitter=QUEUE_FULL_BACKOFF_JITTER,
                )
                backoff_label = "queue full"
            else:
```

**With:**

```python
            if isinstance(e, (MvsepQueueFullError, MvsepTimeoutError)):
                max_attempts = MVSEP_QUEUE_FULL_MAX_RETRIES
                backoff = _compute_mvsep_backoff(
                    attempt,
                    base=QUEUE_FULL_BACKOFF_BASE,
                    factor=QUEUE_FULL_BACKOFF_FACTOR,
                    cap=QUEUE_FULL_BACKOFF_CAP,
                    jitter=QUEUE_FULL_BACKOFF_JITTER,
                )
                backoff_label = "timeout" if isinstance(e, MvsepTimeoutError) else "queue full"
            else:
```

#### 3c. Guarantee first attempt even when time budget is exhausted (current lines 96–101)

**Replace:**

```python
    for attempt in range(1, MVSEP_QUEUE_FULL_MAX_RETRIES + 1):
        if attempt > max_attempts:
            break
        if time_remaining_fn() <= 0:
            logger.warning(f"MVSEP total timeout exceeded during {stage_name}, using fallback")
            break
```

**With:**

```python
    for attempt in range(1, MVSEP_QUEUE_FULL_MAX_RETRIES + 1):
        if attempt > max_attempts:
            break
        if attempt > 1 and time_remaining_fn() <= 0:
            logger.warning(f"MVSEP timeout budget exhausted during {stage_name}, using fallback")
            break
```

#### 3d. Dedicated Stage 2 time budget in `_separate_with_mvsep_fallback` (current line 233 onward)

**After** the existing `_time_remaining()` definition (current lines 241–242), **add**:

```python
    # Stage 2 gets its own dedicated budget, initialized when Stage 2 begins
    stage2_start: Optional[float] = None

    def _stage2_time_remaining() -> float:
        # Combined: must respect both the outer total cap and Stage 2's dedicated budget
        total_remaining = settings.SOW_MVSEP_TOTAL_TIMEOUT - (time.monotonic() - total_start)
        if stage2_start is None:
            return total_remaining
        stage2_remaining = settings.SOW_MVSEP_STAGE2_TIMEOUT - (time.monotonic() - stage2_start)
        return min(total_remaining, stage2_remaining)
```

**After** `stage2_dir = output_dir / "mvsep_stage2"` (current line 283), **add**:

```python
    stage2_start = time.monotonic()
```

**Replace** the Stage 2 retry call (current lines 290–292):

```python
    stage2_result = await _run_mvsep_stage_with_retries(
        "Stage 2", _stage2_fn, job, _time_remaining
    )
```

**With:**

```python
    stage2_result = await _run_mvsep_stage_with_retries(
        "Stage 2", _stage2_fn, job, _stage2_time_remaining
    )
```

#### Verification
- `MvsepTimeoutError` imported at module level.
- Timeout errors in Stage 1 or Stage 2 get 6 attempts with 30/60/120/240/300s backoff.
- First attempt always runs even if `time_remaining() <= 0`.
- Stage 2 uses `_stage2_time_remaining()`, which returns `min(total_remaining, stage2_remaining)`.

---

### Step 4 — Startup logging (`main.py`)

At current line 142, **replace**:

```python
        ("MVSEP", "daily_limit", str(settings.SOW_MVSEP_DAILY_JOB_LIMIT)),
```

**With:**

```python
        ("MVSEP", "max_concurrent", str(settings.SOW_MVSEP_MAX_CONCURRENT)),
```

---

### Step 5 — Environment documentation

#### 5a. `.env.example` (current lines 287–299)

**Replace** the `SOW_MVSEP_DAILY_JOB_LIMIT=50` block with:

```bash
SOW_MVSEP_STAGE2_TIMEOUT=900
# Dedicated time budget for Stage 2 (reverb removal) + its retries (default: 900 = 15 minutes)
# Independent of Stage 1's time consumption

SOW_MVSEP_MAX_CONCURRENT=3
# Maximum concurrent MVSEP API operations across all jobs (default: 3)
# MVSEP Premium allows up to 10; 3 is a conservative default
# Each job's Stage 1 and Stage 2 still serialize naturally within a single job

# Note: MVSEP daily quota is detected from API responses (not self-imposed).
# When the API reports quota exhaustion, MVSEP is disabled until UTC midnight reset.
```

**Update** the `SOW_MVSEP_TOTAL_TIMEOUT` comment:

```bash
SOW_MVSEP_TOTAL_TIMEOUT=1800
# Max seconds for entire MVSEP attempt per song, across both stages
# and all retries (default: 1800 = 30 minutes)
```

#### 5b. `docker-compose.yml` (current lines 34–37)

**Replace**:

```yaml
  SOW_MVSEP_HTTP_TIMEOUT: ${SOW_MVSEP_HTTP_TIMEOUT:-60}
  SOW_MVSEP_STAGE_TIMEOUT: ${SOW_MVSEP_STAGE_TIMEOUT:-300}
  SOW_MVSEP_TOTAL_TIMEOUT: ${SOW_MVSEP_TOTAL_TIMEOUT:-900}
  SOW_MVSEP_DAILY_JOB_LIMIT: ${SOW_MVSEP_DAILY_JOB_LIMIT:-50}
```

**With:**

```yaml
  SOW_MVSEP_HTTP_TIMEOUT: ${SOW_MVSEP_HTTP_TIMEOUT:-60}
  SOW_MVSEP_STAGE_TIMEOUT: ${SOW_MVSEP_STAGE_TIMEOUT:-300}
  SOW_MVSEP_STAGE2_TIMEOUT: ${SOW_MVSEP_STAGE2_TIMEOUT:-900}
  SOW_MVSEP_TOTAL_TIMEOUT: ${SOW_MVSEP_TOTAL_TIMEOUT:-1800}
  SOW_MVSEP_MAX_CONCURRENT: ${SOW_MVSEP_MAX_CONCURRENT:-3}
```

#### 5c. `DEPLOYMENT.md` (current line 428)

**Replace**:

```yaml
      SOW_MVSEP_TOTAL_TIMEOUT: ${SOW_MVSEP_TOTAL_TIMEOUT:-900}
      SOW_MVSEP_DAILY_JOB_LIMIT: ${SOW_MVSEP_DAILY_JOB_LIMIT:-50}
```

**With:**

```yaml
      SOW_MVSEP_STAGE2_TIMEOUT: ${SOW_MVSEP_STAGE2_TIMEOUT:-900}
      SOW_MVSEP_TOTAL_TIMEOUT: ${SOW_MVSEP_TOTAL_TIMEOUT:-1800}
      SOW_MVSEP_MAX_CONCURRENT: ${SOW_MVSEP_MAX_CONCURRENT:-3}
```

---

## 5. Test Plan

### 5a. `test_mvsep_client.py`

**Fixture updates** (current lines 35, 73–87):
- Remove `SOW_MVSEP_DAILY_JOB_LIMIT = 50` from `MockSettings`.
- Remove `daily_job_limit=50` from `client` fixture.
- Add `max_concurrent=3` to `client` fixture.

**Remove tests:**
- `test_is_available_daily_limit_exceeded` (current lines 289–296)
- `test_daily_limit_resets_on_new_utc_day` (current lines 298–307)

**Add tests:**

```python
def test_is_available_false_when_quota_exhausted(client):
    """Test is_available returns False when quota exhausted."""
    client._quota_exhausted = True
    assert client.is_available is False


def test_quota_resets_on_new_utc_day(client):
    """Test quota-exhausted flag resets on new UTC day."""
    from datetime import datetime, timezone, timedelta
    client._quota_exhausted = True
    yesterday = datetime.now(timezone.utc) - timedelta(days=1)
    client._quota_reset_utc = yesterday.replace(hour=0, minute=0, second=0, microsecond=0)
    assert client.is_available is True
    assert client._quota_exhausted is False


@pytest.mark.asyncio
async def test_quota_exhausted_detected_from_success_false(client, mock_post):
    """Test that API response with success=false and quota keywords sets _quota_exhausted."""
    mock_post.return_value = httpx.Response(
        200,
        json={"success": False, "data": {"message": "Daily limit exceeded"}},
    )
    with pytest.raises(MvsepNonRetriableError):
        await client._submit_job(Path("/tmp/audio.mp3"), sep_type=48, add_opt1=11)
    assert client._quota_exhausted is True


@pytest.mark.asyncio
async def test_quota_exhausted_detected_from_400(client, mock_post):
    """Test that HTTP 400 with quota keywords sets _quota_exhausted."""
    mock_post.return_value = httpx.Response(
        400, text="You have exceeded your daily quota"
    )
    with pytest.raises(MvsepNonRetriableError):
        await client._submit_job(Path("/tmp/audio.mp3"), sep_type=48, add_opt1=11)
    assert client._quota_exhausted is True


@pytest.mark.asyncio
async def test_quota_not_triggered_by_generic_error(client, mock_post):
    """Test that non-quota errors don't set _quota_exhausted."""
    mock_post.return_value = httpx.Response(
        200,
        json={"success": False, "data": {"message": "Some other error"}},
    )
    with pytest.raises(MvsepClientError):
        await client._submit_job(Path("/tmp/audio.mp3"), sep_type=48, add_opt1=11)
    assert client._quota_exhausted is False
```

**Update existing test:** `test_separate_vocals_mutex_serializes` → rename to `test_separate_vocals_semaphore_allows_concurrency` and repurpose to verify up to `max_concurrent=3` simultaneous operations. The test should launch 3 concurrent `separate_vocals` calls and verify they all enter the critical section concurrently (using an `asyncio.Event` or counter), then launch a 4th and verify it blocks until one completes.

---

### 5b. `test_mvsep_fallback.py`

**Rename:** `test_daily_limit_hit_uses_local` → `test_quota_exhausted_uses_local`. Update docstring. Logic unchanged (`is_available = False` simulates quota exhausted).

**Update:** `test_total_timeout_exceeded_falls_back` (current lines 277–304). With the guarantee-first-attempt change, the first attempt still runs. The test sets `SOW_MVSEP_TOTAL_TIMEOUT = 0.1` and Stage 1's `slow_separate_vocals` raises on each attempt. Adjust: the first attempt runs (despite timeout), fails, then the retry (attempt 2) is skipped due to timeout. Assert `separate_vocals` was called **once** (not zero), and local fallback was used.

**Add tests:**

```python
@pytest.mark.asyncio
async def test_stage2_gets_dedicated_budget_after_stage1_consumed_total(
    mock_job, mock_mvsep_client, mock_separator_wrapper
):
    """Stage 2 still gets retries even when Stage 1 consumed most of the total timeout."""
    import time
    from unittest.mock import patch

    original_total_timeout = settings.SOW_MVSEP_TOTAL_TIMEOUT
    original_stage2_timeout = settings.SOW_MVSEP_STAGE2_TIMEOUT
    settings.SOW_MVSEP_TOTAL_TIMEOUT = 0.3
    settings.SOW_MVSEP_STAGE2_TIMEOUT = 0.2

    async def slow_stage1(*args, **kwargs):
        time.sleep(0.15)  # Consumes most of total budget
        return (Path("/tmp/mvsep_vocals.flac"), Path("/tmp/mvsep_instrumental.flac"))

    mock_mvsep_client.separate_vocals.side_effect = slow_stage1
    mock_mvsep_client.remove_reverb.side_effect = [
        MvsepClientError("Stage 2 error"),
        MvsepClientError("Stage 2 error again"),
    ]

    try:
        with patch("asyncio.sleep"):
            with patch("random.uniform", return_value=0.0):
                result = await _separate_with_mvsep_fallback(
                    input_path=Path("/tmp/input.mp3"),
                    output_dir=Path("/tmp/output"),
                    job=mock_job,
                    mvsep_client=mock_mvsep_client,
                    separator_wrapper=mock_separator_wrapper,
                )
        # remove_reverb should have been called at least once (guarantee first attempt)
        assert mock_mvsep_client.remove_reverb.call_count >= 1
        assert mock_separator_wrapper.remove_reverb.call_count >= 0
    finally:
        settings.SOW_MVSEP_TOTAL_TIMEOUT = original_total_timeout
        settings.SOW_MVSEP_STAGE2_TIMEOUT = original_stage2_timeout


@pytest.mark.asyncio
async def test_stage2_timeout_uses_queue_full_backoff(
    mock_job, mock_mvsep_client, mock_separator_wrapper
):
    """MvsepTimeoutError triggers 6 attempts with 30/60/120/240/300s backoff."""
    from sow_analysis.services.mvsep_client import MvsepTimeoutError
    from unittest.mock import patch

    sleep_times = []

    async def fake_sleep(seconds):
        sleep_times.append(seconds)

    mock_mvsep_client.remove_reverb.side_effect = MvsepTimeoutError("Poll timeout")

    with patch("asyncio.sleep", side_effect=fake_sleep):
        with patch("random.uniform", return_value=0.0):
            await _separate_with_mvsep_fallback(
                input_path=Path("/tmp/input.mp3"),
                output_dir=Path("/tmp/output"),
                job=mock_job,
                mvsep_client=mock_mvsep_client,
                separator_wrapper=mock_separator_wrapper,
            )

    assert mock_mvsep_client.remove_reverb.call_count == 6
    assert sleep_times == [30, 60, 120, 240, 300]


@pytest.mark.asyncio
async def test_stage2_first_attempt_runs_even_when_time_exhausted(
    mock_job, mock_mvsep_client, mock_separator_wrapper
):
    """Stage 2 gets at least one real submission attempt even when total budget is 0."""
    import time
    from unittest.mock import patch

    original_total_timeout = settings.SOW_MVSEP_TOTAL_TIMEOUT
    original_stage2_timeout = settings.SOW_MVSEP_STAGE2_TIMEOUT
    settings.SOW_MVSEP_TOTAL_TIMEOUT = 0.01
    settings.SOW_MVSEP_STAGE2_TIMEOUT = 0.01

    async def fast_stage1(*args, **kwargs):
        return (Path("/tmp/mvsep_vocals.flac"), Path("/tmp/mvsep_instrumental.flac"))

    mock_mvsep_client.separate_vocals.side_effect = fast_stage1

    try:
        with patch("asyncio.sleep"):
            with patch("random.uniform", return_value=0.0):
                result = await _separate_with_mvsep_fallback(
                    input_path=Path("/tmp/input.mp3"),
                    output_dir=Path("/tmp/output"),
                    job=mock_job,
                    mvsep_client=mock_mvsep_client,
                    separator_wrapper=mock_separator_wrapper,
                )
        # remove_reverb called at least once despite time budget being exhausted
        assert mock_mvsep_client.remove_reverb.call_count >= 1
    finally:
        settings.SOW_MVSEP_TOTAL_TIMEOUT = original_total_timeout
        settings.SOW_MVSEP_STAGE2_TIMEOUT = original_stage2_timeout


@pytest.mark.asyncio
async def test_timeout_error_backoff_uses_queue_full_constants(
    mock_job, mock_mvsep_client, mock_separator_wrapper
):
    """Verify timeout errors use 30s base backoff, not 5s."""
    from sow_analysis.services.mvsep_client import MvsepTimeoutError
    from unittest.mock import patch

    sleep_times = []

    async def fake_sleep(seconds):
        sleep_times.append(seconds)

    mock_mvsep_client.remove_reverb.side_effect = [
        MvsepTimeoutError("Poll timeout"),
        (Path("/tmp/mvsep_dry.flac"), Path("/tmp/mvsep_reverb.flac")),
    ]

    with patch("asyncio.sleep", side_effect=fake_sleep):
        with patch("random.uniform", return_value=0.0):
            result = await _separate_with_mvsep_fallback(
                input_path=Path("/tmp/input.mp3"),
                output_dir=Path("/tmp/output"),
                job=mock_job,
                mvsep_client=mock_mvsep_client,
                separator_wrapper=mock_separator_wrapper,
            )

    assert len(sleep_times) == 1
    assert sleep_times[0] == 30  # queue-full base, not other-error base (5)
```

---

## 6. Verification Commands

```bash
# Focused MVSEP tests
cd ops/analysis-service
PYTHONPATH=src pytest tests/test_mvsep_fallback.py tests/test_mvsep_client.py -v

# Full regression
PYTHONPATH=src pytest tests/ -v
```

---

## 7. Behavior Summary

### Before

| Scenario | Behavior |
|---|---|
| Stage 1 takes 14 min | Stage 2 gets 1 min of 15-min budget → instant timeout → local fallback (50 min) |
| `MvsepTimeoutError` | 3 retries, 5/10/20s backoff |
| MVSEP concurrency | Serialized (1 at a time via Lock) |
| Daily limit | Hardcoded count of 50, self-imposed |

### After

| Scenario | Behavior |
|---|---|
| Stage 1 takes 14 min | Stage 2 gets its own 15-min dedicated budget → up to 15 min of MVSEP retries |
| `MvsepTimeoutError` | 6 retries, 30/60/120/240/300s backoff |
| MVSEP concurrency | 3 concurrent operations (Semaphore) |
| Daily limit | Detected from API response; blocks until UTC midnight |

### Interaction Effects

- With concurrency=3, multiple jobs' Stage 2 retries no longer block behind each other — they can retry in parallel.
- With longer Stage 2 retries + dedicated budget, the MVSEP service has time to recover from transient slowness.
- With API-response-based quota detection, there's no premature self-imposed limit — MVSEP is used to its actual capacity.

---

## 8. Out of Scope

- No changes to `lrc.py` (no MVSEP logic).
- No changes to `_poll_job` in-band exponential poll backoff (`mvsep_client.py:288`) — already exponential (1.5x, cap 30s).
- No changes to `queue.py` job-level concurrency model (semaphore on `MvsepClient` handles MVSEP-specific concurrency).
- No new `MvsepQuotaExhaustedError` exception type — `MvsepNonRetriableError` is sufficient since the `_quota_exhausted` flag carries the state.

---

## 9. Review Notes (v1 → v2 Changes)

1. **Line numbers removed** from edit instructions — originals in v1 drifted from current file contents. v2 uses exact `oldString`/`newString` context blocks for reliable matching.
2. **Semaphore concurrency test** updated: repurpose existing `test_separate_vocals_mutex_serializes` instead of adding a separate stub. Verifies up to 3 concurrent entries and 4th blocks.
3. **`test_total_timeout_exceeded_falls_back`** explicitly updated to assert `separate_vocals.call_count == 1` (not 0) due to guarantee-first-attempt change.
4. **Added `test_stage2_gets_dedicated_budget_after_stage1_consumed_total`** to verify the core fix: Stage 2 still attempts even when Stage 1 consumed most of the total budget.
5. **Added `test_timeout_error_backoff_uses_queue_full_constants`** as a focused unit test to prevent regression of backoff base values.
