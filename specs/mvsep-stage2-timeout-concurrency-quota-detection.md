# MVSEP Stage 2 Timeout Resilience, Concurrency, and Quota Detection

## Context

When MVSEP Stage 1 (vocal separation, melroformer) takes 10-14 minutes due to
service-side slowness, it consumes the entire `SOW_MVSEP_TOTAL_TIMEOUT=900s`
(15 min) shared budget. Stage 2 (reverb removal) then immediately hits the
`time_remaining_fn() <= 0` check on its **first** attempt and bails out without
ever submitting to MVSEP — falling back to local UVR-De-Echo (~50 min).

```
08:41:59,115 - MVSEP Stage 1 succeeded on attempt 1
08:41:59,115 - MVSEP total timeout exceeded during Stage 2, using fallback   ← same ms!
08:41:59,116 - MVSEP Stage 2 failed, using local Stage 2 fallback
```

Once MVSEP service-wide slowness begins, **every subsequent job** exhibits the
same pattern because Stage 1 alone consumes the shared budget each time. There
is no per-stage replenishment.

Three compounding issues:

1. **Shared time budget** — `SOW_MVSEP_TOTAL_TIMEOUT` is shared across Stage 1 +
   Stage 2 + all retries, tracked from a single `total_start` at Stage 1 entry
   (`stem_separation.py:233,241-242`).
2. **Weak retry for timeouts** — `MvsepTimeoutError` from `_poll_job()`
   (`mvsep_client.py:255`) is treated as a generic `MvsepClientError`: 3 retries
   with 5/10/20s backoff. Timeouts usually mean MVSEP is busy and self-resolves
   with patience — they deserve the same treatment as `MvsepQueueFullError`
   (6 retries, 30/60/120/240/300s backoff).
3. **MVSEP concurrency = 1** — `MvsepClient._mutex` (an `asyncio.Lock`) serializes
   all `separate_vocals()` and `remove_reverb()` calls across all jobs. Multiple
   jobs queue behind each other instead of running in parallel.

Additionally, the hardcoded `SOW_MVSEP_DAILY_JOB_LIMIT=50` self-imposes a daily
cap that doesn't reflect the actual MVSEP account quota. The real quota should
be detected from API responses, not counted locally.

## Design Decisions

- **Per-stage dedicated budget for Stage 2**: New `SOW_MVSEP_STAGE2_TIMEOUT=900s`
  gives Stage 2 its own 15-minute budget independent of Stage 1's consumption.
  The outer `SOW_MVSEP_TOTAL_TIMEOUT` (raised 900→1800) remains as a hard cap
  for Stage 1 + Stage 2 combined.
- **Guarantee first attempt**: The `time_remaining_fn() <= 0` check in
  `_run_mvsep_stage_with_retries()` is moved to skip only *retries* (attempt > 1),
  not the first attempt. Stage 2 always gets at least one real submission.
- **Promote `MvsepTimeoutError` to queue-full backoff**: Group with
  `MvsepQueueFullError` — 6 attempts, 30/60/120/240/300s backoff. Timeouts are
  transient MVSEP busy-ness that self-resolves.
- **Replace `_mutex` (Lock) with `_semaphore` (Semaphore, N=3)**: New
  `SOW_MVSEP_MAX_CONCURRENT=3` allows up to 3 concurrent MVSEP operations.
  MVSEP Premium supports up to 10; 3 is conservative. A single job's Stage 1
  and Stage 2 still serialize naturally (Stage 2 only starts after Stage 1
  completes).
- **Remove `SOW_MVSEP_DAILY_JOB_LIMIT`**: No self-imposed counting. Quota
  exhaustion is detected from API responses (both `success: false` JSON body
  and HTTP 400 error text) via keyword matching. When detected, sets
  `_quota_exhausted = True` and raises `MvsepNonRetriableError`. Subsequent
  jobs check `is_available` → see `_quota_exhausted` → skip MVSEP → use local.
  Resets on UTC day rollover.

## Files to Modify

| File | Action |
|------|--------|
| `ops/analysis-service/src/sow_analysis/config.py` | Add `SOW_MVSEP_STAGE2_TIMEOUT`, `SOW_MVSEP_MAX_CONCURRENT`; raise `SOW_MVSEP_TOTAL_TIMEOUT` 900→1800; remove `SOW_MVSEP_DAILY_JOB_LIMIT` |
| `ops/analysis-service/src/sow_analysis/services/mvsep_client.py` | Replace `_mutex`→`_semaphore`; remove daily count logic; add `_quota_exhausted` flag + UTC reset; add `_is_quota_exhausted()` detection in `_submit_job`; update docstring |
| `ops/analysis-service/src/sow_analysis/workers/stem_separation.py` | Import `MvsepTimeoutError`; promote to queue-full backoff; dedicated Stage 2 time budget; guarantee first attempt |
| `ops/analysis-service/src/sow_analysis/main.py` | Replace `daily_limit` log with `max_concurrent` |
| `ops/analysis-service/.env.example` | Add new vars, remove daily limit, update comments |
| `ops/analysis-service/docker-compose.yml` | Pass new env vars with defaults |
| `ops/analysis-service/DEPLOYMENT.md` | Update env documentation |
| `ops/analysis-service/tests/test_mvsep_client.py` | Remove daily limit tests; add quota detection tests; add concurrency test |
| `ops/analysis-service/tests/test_mvsep_fallback.py` | Rename daily limit test; add Stage 2 budget/timeout/first-attempt tests; update total-timeout test |

## Implementation Steps

### Step 1: Config changes (`config.py`)

In the MVSEP timeouts section (lines 141-145), replace:

```python
# Timeouts & limits
SOW_MVSEP_HTTP_TIMEOUT: int = 60
SOW_MVSEP_STAGE_TIMEOUT: int = 300
SOW_MVSEP_TOTAL_TIMEOUT: int = 900
SOW_MVSEP_DAILY_JOB_LIMIT: int = 50
```

With:

```python
# Timeouts & limits
SOW_MVSEP_HTTP_TIMEOUT: int = 60
SOW_MVSEP_STAGE_TIMEOUT: int = 300
SOW_MVSEP_STAGE2_TIMEOUT: int = 900       # Dedicated budget for Stage 2 + retries
SOW_MVSEP_TOTAL_TIMEOUT: int = 1800       # Outer cap: Stage 1 + Stage 2 combined
SOW_MVSEP_MAX_CONCURRENT: int = 3         # Max concurrent MVSEP API operations
```

### Step 2: MvsepClient — replace mutex with semaphore (`mvsep_client.py`)

#### 2a. `__init__` (lines 63-114)

Remove `daily_job_limit` parameter and all daily-count state. Add `max_concurrent`
parameter and semaphore.

Remove:
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

Add:
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

Update docstring: remove `daily_job_limit` param doc, add `max_concurrent` param doc.
Remove "Includes daily cost tracking" from class docstring; mention quota detection.

#### 2b. `is_available` (lines 116-130)

Replace `_check_daily_limit()` with quota-exhausted check:

```python
@property
def is_available(self) -> bool:
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

#### 2c. Remove `_check_daily_limit` and `_increment_daily_count` (lines 132-149)

Replace with:

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

#### 2d. Replace `async with self._mutex:` with `async with self._semaphore:`

In `separate_vocals` (lines 352-354):

```python
if self._semaphore is None:
    self._semaphore = asyncio.Semaphore(self._max_concurrent)
async with self._semaphore:
```

In `remove_reverb` (lines 433-435):

```python
if self._semaphore is None:
    self._semaphore = asyncio.Semaphore(self._max_concurrent)
async with self._semaphore:
```

#### 2e. Remove daily count increment

In `separate_vocals` line 365, remove:
```python
self._increment_daily_count()  # only count successful submits
```

### Step 3: MvsepClient — quota exhaustion detection (`mvsep_client.py`)

#### 3a. Add module-level helper function (after line 19, before exception classes)

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

#### 3b. Add quota detection in `_submit_job` — `success: false` branch (lines 203-211)

Current:
```python
if not success:
    error_msg = result_data.get("message", "Unknown error")
    if "invalid" in error_msg.lower() and "key" in error_msg.lower():
        self._disabled = True
        raise MvsepNonRetriableError(f"Invalid API key: {error_msg}")
    if "insufficient" in error_msg.lower() and "credit" in error_msg.lower():
        self._disabled = True
        raise MvsepNonRetriableError(f"Insufficient credits: {error_msg}")
    raise MvsepClientError(f"MVSEP API error: {error_msg}")
```

New:
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

#### 3c. Add quota detection in `_submit_job` — HTTP 400 branch (lines 225-228)

Current:
```python
if status_code == 400:
    error_text = e.response.text.lower()
    if "queue" in error_text or "wait before adding" in error_text:
        raise MvsepQueueFullError(f"MVSEP queue full: {e.response.text}") from e
```

New:
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

### Step 4: Stem separation worker — timeout resilience (`stem_separation.py`)

#### 4a. Import `MvsepTimeoutError` at module level (line 27)

Current:
```python
from ..services.mvsep_client import MvsepNonRetriableError, MvsepQueueFullError
```

New:
```python
from ..services.mvsep_client import MvsepNonRetriableError, MvsepQueueFullError, MvsepTimeoutError
```

#### 4b. Promote `MvsepTimeoutError` to queue-full-style backoff

In `_run_mvsep_stage_with_retries` exception handler (lines 107-141), change the
`isinstance` check to group `MvsepTimeoutError` with `MvsepQueueFullError`:

Current:
```python
        except Exception as e:
            if isinstance(e, MvsepNonRetriableError):
                logger.error(f"MVSEP {stage_name} non-retriable error: {e}")
                break
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
                ...
```

New:
```python
        except Exception as e:
            if isinstance(e, MvsepNonRetriableError):
                logger.error(f"MVSEP {stage_name} non-retriable error: {e}")
                break
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
                ...
```

#### 4c. Guarantee first attempt even when time budget is exhausted

In `_run_mvsep_stage_with_retries` (lines 96-101), change the time-remaining
check to skip only on retry (attempt > 1):

Current:
```python
    for attempt in range(1, MVSEP_QUEUE_FULL_MAX_RETRIES + 1):
        if attempt > max_attempts:
            break
        if time_remaining_fn() <= 0:
            logger.warning(f"MVSEP total timeout exceeded during {stage_name}, using fallback")
            break
```

New:
```python
    for attempt in range(1, MVSEP_QUEUE_FULL_MAX_RETRIES + 1):
        if attempt > max_attempts:
            break
        if attempt > 1 and time_remaining_fn() <= 0:
            logger.warning(f"MVSEP timeout budget exhausted during {stage_name}, using fallback")
            break
```

#### 4d. Dedicated Stage 2 time budget in `_separate_with_mvsep_fallback`

In `_separate_with_mvsep_fallback` (lines 233-242), add Stage 2 budget tracking:

Current:
```python
    total_start = time.monotonic()

    def _time_remaining() -> float:
        return settings.SOW_MVSEP_TOTAL_TIMEOUT - (time.monotonic() - total_start)
```

New:
```python
    total_start = time.monotonic()

    def _time_remaining() -> float:
        return settings.SOW_MVSEP_TOTAL_TIMEOUT - (time.monotonic() - total_start)

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

Before Stage 2 retries begin (after line 283 `stage2_dir = ...`), set the clock:

```python
    stage2_dir = output_dir / "mvsep_stage2"
    stage2_start = time.monotonic()
```

Pass `_stage2_time_remaining` to Stage 2's retry loop (lines 290-292):

Current:
```python
    stage2_result = await _run_mvsep_stage_with_retries(
        "Stage 2", _stage2_fn, job, _time_remaining
    )
```

New:
```python
    stage2_result = await _run_mvsep_stage_with_retries(
        "Stage 2", _stage2_fn, job, _stage2_time_remaining
    )
```

### Step 5: Startup logging (`main.py`)

Line 142, replace:

```python
("MVSEP", "daily_limit", str(settings.SOW_MVSEP_DAILY_JOB_LIMIT)),
```

With:

```python
("MVSEP", "max_concurrent", str(settings.SOW_MVSEP_MAX_CONCURRENT)),
```

### Step 6: Environment documentation

#### `.env.example` (lines 287-299)

Replace `SOW_MVSEP_DAILY_JOB_LIMIT=50` block with:

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

Update `SOW_MVSEP_TOTAL_TIMEOUT` comment:

```bash
SOW_MVSEP_TOTAL_TIMEOUT=1800
# Max seconds for entire MVSEP attempt per song, across both stages
# and all retries (default: 1800 = 30 minutes)
```

#### `docker-compose.yml` (lines 34-37)

Replace:
```yaml
  SOW_MVSEP_HTTP_TIMEOUT: ${SOW_MVSEP_HTTP_TIMEOUT:-60}
  SOW_MVSEP_STAGE_TIMEOUT: ${SOW_MVSEP_STAGE_TIMEOUT:-300}
  SOW_MVSEP_TOTAL_TIMEOUT: ${SOW_MVSEP_TOTAL_TIMEOUT:-900}
  SOW_MVSEP_DAILY_JOB_LIMIT: ${SOW_MVSEP_DAILY_JOB_LIMIT:-50}
```

With:
```yaml
  SOW_MVSEP_HTTP_TIMEOUT: ${SOW_MVSEP_HTTP_TIMEOUT:-60}
  SOW_MVSEP_STAGE_TIMEOUT: ${SOW_MVSEP_STAGE_TIMEOUT:-300}
  SOW_MVSEP_STAGE2_TIMEOUT: ${SOW_MVSEP_STAGE2_TIMEOUT:-900}
  SOW_MVSEP_TOTAL_TIMEOUT: ${SOW_MVSEP_TOTAL_TIMEOUT:-1800}
  SOW_MVSEP_MAX_CONCURRENT: ${SOW_MVSEP_MAX_CONCURRENT:-3}
```

#### `DEPLOYMENT.md` (line 428)

Replace `SOW_MVSEP_DAILY_JOB_LIMIT` line with:
```yaml
      SOW_MVSEP_STAGE2_TIMEOUT: ${SOW_MVSEP_STAGE2_TIMEOUT:-900}
      SOW_MVSEP_TOTAL_TIMEOUT: ${SOW_MVSEP_TOTAL_TIMEOUT:-1800}
      SOW_MVSEP_MAX_CONCURRENT: ${SOW_MVSEP_MAX_CONCURRENT:-3}
```

### Step 7: Tests

#### `test_mvsep_client.py`

Remove `SOW_MVSEP_DAILY_JOB_LIMIT = 50` from test settings (line 35).
Remove `daily_job_limit=50` from client fixture (line 84).
Add `max_concurrent=3` to client fixture.

Remove tests:
- `test_is_available_daily_limit_exceeded` (lines 289-294)
- `test_daily_limit_resets_on_new_utc_day` (lines 298-307)

Add tests:

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

@pytest.mark.asyncio
async def test_semaphore_allows_concurrent_operations():
    """Test that up to max_concurrent operations can run simultaneously."""
    # Create client with max_concurrent=3
    # Start 3 concurrent _submit_job calls
    # Verify all 3 proceed without blocking
    # Start a 4th — verify it blocks until one completes
```

#### `test_mvsep_fallback.py`

Rename `test_daily_limit_hit_uses_local` → `test_quota_exhausted_uses_local`
(lines 364-379). Update docstring. Logic unchanged (`is_available = False`
simulates quota exhausted).

Add tests:

```python
@pytest.mark.asyncio
async def test_stage2_gets_dedicated_budget_after_stage1_consumed_total(
    mock_job, mock_mvsep_client, mock_separator_wrapper
):
    """Stage 2 still gets retries even when Stage 1 consumed most of the total timeout."""
    # Set SOW_MVSEP_TOTAL_TIMEOUT to a small value
    # Stage 1 succeeds quickly but with enough elapsed time to exhaust total budget
    # Stage 2 should still get at least one attempt (guarantee first attempt)
    # Assert remove_reverb was called at least once

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
    # Set SOW_MVSEP_TOTAL_TIMEOUT = 0.1
    # Stage 1 succeeds very fast
    # Stage 2 should still attempt once despite time_remaining <= 0
    # Assert remove_reverb was called at least once

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
            await _separate_with_mvsep_fallback(
                input_path=Path("/tmp/input.mp3"),
                output_dir=Path("/tmp/output"),
                job=mock_job,
                mvsep_client=mock_mvsep_client,
                separator_wrapper=mock_separator_wrapper,
            )

    assert len(sleep_times) == 1
    assert sleep_times[0] == 30  # queue-full base, not other-error base (5)
```

Update `test_total_timeout_exceeded_falls_back` (lines 277-304): With the
guarantee-first-attempt change, the first attempt still runs. The test sets
`SOW_MVSEP_TOTAL_TIMEOUT = 0.1` and Stage 1's `slow_separate_vocals` raises on
each attempt. Adjust: the first attempt runs (despite timeout), fails, then the
retry (attempt 2) is skipped due to timeout. Assert `separate_vocals` was called
once (not zero), and local fallback was used.

## Verification

```bash
cd ops/analysis-service
PYTHONPATH=src pytest tests/test_mvsep_fallback.py tests/test_mvsep_client.py -v
```

Full regression:
```bash
cd ops/analysis-service
PYTHONPATH=src pytest tests/ -v
```

## Behavior Summary

### Before

| Scenario | Behavior |
|---|---|
| Stage 1 takes 14 min | Stage 2 gets 1 min of 15-min budget → instant timeout → local fallback (50 min) |
| MvsepTimeoutError | 3 retries, 5/10/20s backoff |
| MVSEP concurrency | Serialized (1 at a time via Lock) |
| Daily limit | Hardcoded count of 50, self-imposed |

### After

| Scenario | Behavior |
|---|---|
| Stage 1 takes 14 min | Stage 2 gets its own 15-min dedicated budget → up to 15 min of MVSEP retries |
| MvsepTimeoutError | 6 retries, 30/60/120/240/300s backoff |
| MVSEP concurrency | 3 concurrent operations (Semaphore) |
| Daily limit | Detected from API response; blocks until UTC midnight |

### Interaction effects

- With concurrency=3, multiple jobs' Stage 2 retries no longer block behind each
  other — they can retry in parallel.
- With longer Stage 2 retries + dedicated budget, the MVSEP service has time to
  recover from transient slowness.
- With API-response-based quota detection, there's no premature self-imposed
  limit — MVSEP is used to its actual capacity.

## Out of Scope

- No changes to `lrc.py` (no MVSEP logic)
- No changes to `_poll_job` in-band exponential poll backoff
  (`mvsep_client.py:286`) — already exponential (1.5x, cap 30s)
- No changes to `queue.py` job-level concurrency model (semaphore on MvsepClient
  handles MVSEP-specific concurrency)
- No new `MvsepQuotaExhaustedError` exception type — `MvsepNonRetriableError` is
  sufficient since the `_quota_exhausted` flag carries the state
