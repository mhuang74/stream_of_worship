# Plan: MVSEP Quota Keyword Fix + Free-Mode Local Fallback Removal (v2)

> **Status:** Planning (read-only). Do not implement until approved.  
> **Supersedes:** `specs/mvsep-quota-keyword-fix-and-free-mode-fallback-removal-v1.md`  
> **Related:** `specs/free-only-patient-mode-v4.md`, `specs/mvsep-stage2-timeout-concurrency-quota-detection-v2.md`

## 1. Problem (Unchanged from v1)

Production logs show MVSEP's real daily-limit message (`"You have reached the limit of separations for today"`) causes 3× wasted retries + 1-hour local CPU fallback per job, because no keyword in `_QUOTA_KEYWORDS` matches the reversed word order (`"reached the limit"` instead of `"limit reached"`).

Additionally, in `SOW_FREE_ONLY_MODE`, the local-fallback code paths violate the user's directive to wait for quota reset rather than burn CPU for an hour.

---

## 2. v1 Plan: Review Findings — 7 Serious Issues

During review of v1 against the current codebase, the following issues were identified that could cause production failures, confusing debugging, or dead code.

### Issue 1: RACE — `is_quota_exhausted` Property Has Side Effects (CORRECTED)

**Location:** `mvsep_client.py` lines 189–198.

The `is_quota_exhausted` **property** calls `_check_quota_reset()`, which mutates `_quota_exhausted` and `_quota_reset_utc`. When v1's new free-mode checks in `stem_separation.py` access this property multiple times per second inside `while True` loops, repeated UTC-day comparisons are harmless but noisy. More importantly, the v1 test plan's `test_free_mode_stage1_quota_exhausted_waits_and_retries` uses `mock_mvsep_client.is_quota_exhausted = True` as an **assignment** on a `MagicMock(spec=MvsepClient)`, which works because `MagicMock` allows arbitrary attribute writes. However, if the mock ever becomes `AsyncMock(spec=MvsepClient)` or stricter, the property-descriptor may block assignment.

**Fix:** Clarify in tests that `is_quota_exhausted` must be set by setting `_quota_exhausted` directly (mimicking real client behavior). Document that callers should treat `is_quota_exhausted` as read-only.

### Issue 2: RACE — `_quota_exhausted` Set But Not Propagated Across Service Restarts (HIGH)

**Location:** `mvsep_client.py`.

`MvsepClient` is constructed fresh on every service restart in `main.py` lifespan:
```python
mvsep_client = MvsepClient()  # _quota_exhausted defaults to False
```

If the service restarts while MVSEP quota is exhausted, the new client starts with `_quota_exhausted = False`. The next stem-separation job will:
1. See `is_available == True` (initially).
2. Submit to MVSEP.
3. Get HTTP 400 quota error → set `_quota_exhausted = True`.
4. Waste **one** API call before re-detecting.

This is technically an unavoidable consequence of purely in-memory state. **v1 should at least acknowledge it** and suggest mitigation (e.g., if `SOW_FREE_ONLY_MODE=True`, skip the initial submit and probe first after restart, or log a loud warning).

**Fix for v2:** In the free-mode pre-check path of `stem_separation.py`, add a **proactive probe** (a lightweight call, not a full stem submission) when `is_available` is `True` but `_quota_exhausted` was `False` at startup. This is complex. Simpler fix: in `main.py`, when `SOW_FREE_ONLY_MODE=True` and MVSEP client is initialized, immediately call a lightweight `check_quota()` endpoint if one exists. **MVSEP has no such endpoint.** The pragmatic fix: accept one wasted call per restart, but document it as a known limitation.

**Better fix for v2:** In `main.py` lifespan, after creating `MvsepClient`, if `SOW_FREE_ONLY_MODE=True`, immediately check `is_available` and log a clear line:
```python
if settings.SOW_FREE_ONLY_MODE and mvsep_client and not mvsep_client.is_available:
    logger.warning("MVSEP unavailable at startup in free-only mode; jobs will wait for quota reset")
```
This is already implied but worth making explicit to avoid operator confusion.

### Issue 3: DEAD CODE — `test_free_mode_stage1_quota_exhausted_waits_and_retries` Cannot Work As Written (HIGH)

**Location:** v1 Change 4, test snippet.

The test in v1 sets:
```python
mock_mvsep_client.separate_vocals.side_effect = [
    MvsepNonRetriableError("Daily quota exhausted"),
    (Path("/tmp/v.flac"), Path("/tmp/i.flac")),
]
mock_mvsep_client.is_quota_exhausted = True
qw = AsyncMock()
qw.mark_exhausted = AsyncMock()
qw.wait = AsyncMock(return_value=True)
```

But the real `QuotaWaiter` is not passed into `_separate_with_mvsep_fallback` in this test — `qw` is. After `wait()` returns True, the code loops back to `_run_mvsep_stage_with_retries` → `mvsep_client.separate_vocals()` — but the mock's `is_quota_exhausted` is still `True` (it's a `MagicMock` attribute, not a property). So `is_available` is still `False` (because `is_quota_exhausted` is never reset by `QuotaWaiter`).

In reality, `QuotaWaiter.wait()` does not reset `mvsep_client._quota_exhausted` — it just **probes** `is_available` (which internally checks `_check_quota_reset()` for UTC midnight). The only way `is_available` becomes `True` again is when the UTC day rolls over. So the test's `side_effect` second success is never reached because `is_available` remains `False`.

**Fix for v2:** The test should:
1. After `qw.wait()` returns True, simulate a UTC day reset by setting `mock_mvsep_client._quota_exhausted = False` (or patching `datetime.now`).
2. OR, test the integration with a real `QuotaWaiter` + mock probe function that toggles, rather than mocking `QuotaWaiter` itself.
3. Simplify: use a mock probe function that returns `False` then `True`, driving a real `QuotaWaiter` instance.

### Issue 4: THREE DISTINCT ROOT CAUSES FOR NO-VOCALS — MUST DISTINGUISH (MEDIUM–HIGH)

**Location:** `mvsep_client.py` `separate_vocals()` (lines 430–469) and `stem_separation.py` (lines 362–368).

After `separate_vocals()` successfully submits, polls, and downloads files, it classifies them using two strategies:

```python
# 1. Match by API "type" field ("vocals", "other", etc.)
# 2. Fallback: match by filename substring ("vocal", "instrumental", etc.)
return vocals_file, instrumental_file
```

Back in the worker:
```python
vocals, instrumental = stage1_result
if not vocals:
    logger.error("MVSEP Stage 1 succeeded but no vocals file produced")
    _set_job_stage(job, "fallback_local")
    async with optional_semaphore(local_model_semaphore):
        return await separator_wrapper.separate_stems(input_path, output_dir)
```

**Three distinct root causes** can lead to `vocals is None`:

| # | Root Cause | Description | Retry Helpful? |
|---|---|---|---|
| A | **API returned no files** | `file_entries` is empty (MVSEP bug) | Yes — may recover |
| B | **Download failure** | `file_entries` non-empty but `_download_files` returns empty (transient network fail) | Yes — likely recoverable |
| C | **Classification failure** | Files downloaded successfully, but neither API-type nor filename-fallback matched any file as "vocals" (our parsing bug) | **No** — retrying will always fail |

The **current code treats all three as one indistinguishable case** and falls back to local audio-separator. This causes:

1. **Quality divergence:** Local audio-separator (BS-Roformer) and MVSEP (MelBand Roformer) produce different-sounding stems. A user who expects MVSEP quality silently gets lower-quality local output.
2. **Hidden bugs:** If MVSEP introduces a new response format (new `type` values, renamed `download` fields, new filename patterns), the classification code fails silently and we never find out.
3. **Wasted resources:** Local fallback on CPU takes ~1 hour. If the cause was a transient download failure, we burned 1 hour of CPU unnecessarily.
4. **Cache/R2 inconsistency:** `process_stem_separation()` uploads the local-fallback stems to R2. Future cache hits return the lower-quality local stems, permanently polluting the cache for that song.

**Fix for v2:** Distinguish the three cases with a new exception type and targeted handling:

1. **Cases A & B** (API empty / download failure): Raise `MvsepClientError` (retriable) from `separate_vocals()`. `_run_mvsep_stage_with_retries` will retry. After exhaustion, existing free-mode raise / non-free fallback behavior applies.
2. **Case C** (classification failure): Raise new `MvsepParsingError(MvsepNonRetriableError)` — non-retriable, bypasses retries, propagates up to fail the job in **both** modes.

See **Change 2b** below for the full implementation.

### Issue 5: MISSING TIMEBUDGET FIX — `_time_remaining()` Still Counts Wait Time Against Total Timeout (MEDIUM)

**Location:** `stem_separation.py` lines 303–305.

The v2 timeout-concurrency spec (`mvsep-stage2-timeout-concurrency-quota-detection-v2.md`) already addresses this by subtracting `total_wait_seconds[0]` from elapsed time. Looking at **current** code:
```python
def _time_remaining() -> float:
    elapsed = time.monotonic() - total_start - total_wait_seconds[0]
    return settings.SOW_MVSEP_TOTAL_TIMEOUT - elapsed
```

This is already fixed in the deployed code (the `- total_wait_seconds[0]` is present). **v1 did not mention this** but it is already correct. No action needed, but worth noting that if v1 were applied against an older branch without this fix, quota-waiting jobs would time out while waiting.

### Issue 6: TEST DUPLICATION — `test_stage1_no_vocals_file_fallback` Appears Twice in `test_mvsep_fallback.py`

**Location:** `tests/test_mvsep_fallback.py`.

The file already contains two identical copies of `test_stage1_no_vocals_file_fallback` (confirmed by `grep`). Adding more tests that modify this path may silently shadow or break existing tests if the duplicate is not resolved first.

**Fix for v2:** Remove the duplicate `test_stage1_no_vocals_file_fallback` before adding new tests.

### Issue 7: MISLEADING LOGGING — Free-Mode "Non-Quota Error" Message Is Incorrect for Some Paths (LOW)

**Location:** v1 Change 2a, 2c.

The error messages say "non-quota error (e.g., repeated network errors / timeouts)". But `MvsepTimeoutError` and `MvsepQueueFullError` have **their own retry paths** with up to 6 attempts at longer backoff — they are unlikely to be the cause of reaching the fallback code. When the fallback code IS reached after 6 queue-full retries, the error IS effectively permanent (MVSEP queue is permanently full). The message should say "MVSEP appears permanently unavailable" rather than "non-quota error".

**Fix for v2:** Rephrase error message to be more accurate.

---

## 3. Goals (Refined from v1)

1. **Detect MVSEP's real daily-limit message** as quota exhaustion (set `_quota_exhausted = True`, raise `MvsepNonRetriableError`).
2. **Stop retrying the same job 3× when quota is already exhausted** — break immediately on first quota detection.
3. **Stop subsequent jobs from re-trying MVSEP** — they should see `is_available == False` immediately and enter the quota-wait path.
4. **In `SOW_FREE_ONLY_MODE`: never fall back to local stem separation** — wait for quota reset with periodic checks instead.
5. **Preserve non-free-mode behavior unchanged** — local fallback remains the safety net when `SOW_FREE_ONLY_MODE=False`.
6. **[NEW]** Distinguish three root causes for no-vocals-file scenario: API empty (retriable), download failure (retriable), classification failure (non-retriable). Raise appropriate exceptions from `separate_vocals()`.
7. **[NEW]** Remove duplicate test before adding new tests.
8. **[NEW]** Correct test for free-mode quota-wait-and-retry to handle the fact that `is_available` stays `False` until UTC midnight.

---

## 4. Changes

### Change 1: Extend `_QUOTA_KEYWORDS` (PRIMARY FIX)

**File:** `ops/analysis-service/src/sow_analysis/services/mvsep_client.py`  
**Lines:** 22–33

Add the missing phrases that appear in MVSEP's actual daily-limit response. These are additive — no existing keywords removed:

```python
_QUOTA_KEYWORDS = (
    "daily limit",
    "daily quota",
    "limit exceeded",
    "quota exceeded",
    "limit reached",
    "reached the limit",        # NEW: matches "You have reached the limit of separations for today"
    "separations for today",    # NEW: matches same message, very specific
    "try again tomorrow",      # NEW: matches "Please try again tomorrow"
    "too many jobs",
    "too many requests today",
    "day limit",
    "per day",
    "exceeded your",
)
```

**Effect:** Once the real message is detected:
- `_quota_exhausted = True` is set on the client singleton
- `MvsepNonRetriableError` is raised instead of generic `MvsepClientError`
- All existing free-mode quota-wait infrastructure engages automatically

---

### Change 2: Remove Local Fallback in SOW_FREE_ONLY_MODE

**File:** `ops/analysis-service/src/sow_analysis/workers/stem_separation.py`

Gate the three local-fallback code paths behind `not settings.SOW_FREE_ONLY_MODE`, and in free mode raise `StemSeparationWorkerError` instead.

#### 2a. Stage 1 exhausted retries — non-quota failure (lines 355–360)

**Current:**
```python
if stage1_result is None:
    logger.info("MVSEP Stage 1 failed, falling back to full local pipeline")
    _set_job_stage(job, "fallback_local")
    async with optional_semaphore(local_model_semaphore):
        return await separator_wrapper.separate_stems(input_path, output_dir)
```

**Proposed:**
```python
if stage1_result is None:
    if settings.SOW_FREE_ONLY_MODE:
        raise StemSeparationWorkerError(
            f"MVSEP Stage 1 permanently unavailable in free-only mode "
            f"(quota not exhausted but retries exhausted). "
            f"Refusing local fallback. Job: {job.id}"
        )
    logger.info("MVSEP Stage 1 failed, falling back to full local pipeline")
    _set_job_stage(job, "fallback_local")
    async with optional_semaphore(local_model_semaphore):
        return await separator_wrapper.separate_stems(input_path, output_dir)
```

#### 2b. Stage 1 succeeded but no vocals file — DISTINGUISH THREE ROOT CAUSES

**Current:**
```python
if not vocals:
    logger.error("MVSEP Stage 1 succeeded but no vocals file produced")
    _set_job_stage(job, "fallback_local")
    async with optional_semaphore(local_model_semaphore):
        return await separator_wrapper.separate_stems(input_path, output_dir)
```

**New exception type (add to `mvsep_client.py`):**
```python
class MvsepParsingError(MvsepNonRetriableError):
    """MVSEP returned files but we could not classify them (parsing bug)."""
    pass
```

**New `separate_vocals()` logic (lines 430–469):**

After `_download_files()` returns `file_entries`, classify them. If classification fails (no vocals file found by either API-type or filename-fallback), raise `MvsepParsingError`:

```python
# In separate_vocals(), after _download_files():
if not file_entries:
    # Case A: API returned no files — transient, retriable
    raise MvsepClientError(
        f"MVSEP Stage 1 returned no file entries for job {job_id}. "
        f"This may be a transient API issue."
    )

# ... existing classification logic ...

# After classification:
if vocals_file is None:
    # Case C: files downloaded but none classified as vocals — our bug
    raise MvsepParsingError(
        f"MVSEP Stage 1 returned {len(file_entries)} file(s) for job {job_id} "
        f"but none could be classified as vocals. "
        f"API types: {[e.get('type') for e in file_entries]}. "
        f"Download URLs: {[e.get('download') for e in file_entries if e.get('download')]}. "
        f"This indicates a parsing/classification bug — please check MVSEP response format."
    )
```

**For Case B (download failure):** `_download_files()` already returns empty list on network errors. If `file_entries` is non-empty but `_download_files` returns empty, this is a transient network failure. Raise `MvsepClientError` (retriable):

```python
# In _download_files() or after it:
if file_entries and not downloaded_files:
    raise MvsepClientError(
        f"MVSEP Stage 1: {len(file_entries)} file(s) returned but download failed. "
        f"Transient network error — will retry."
    )
```

**Worker-side handling (in `stem_separation.py`):**

The existing `if not vocals:` block is **replaced** by the exceptions raised from `separate_vocals()`. No additional worker-side handling needed — `MvsepClientError` is retriable (caught by `_run_mvsep_stage_with_retries`), and `MvsepParsingError` is non-retriable (bypasses retries, propagates up to fail the job).

**Rationale (v2 change):** The three root causes have fundamentally different retry semantics. Cases A & B are transient and may recover on retry. Case C is a persistent bug that will never resolve. Distinguishing them prevents wasted retries on bugs (Case C) and enables recovery from transient failures (Cases A & B).

#### 2c. Stage 2 exhausted retries — non-quota failure (lines 404–410)

**Current:**
```python
if stage2_result is None:
    logger.info("MVSEP Stage 2 failed, using local Stage 2 fallback")
    _set_job_stage(job, "fallback_local_stage2")
    async with optional_semaphore(local_model_semaphore):
        dry_vocals, _ = await separator_wrapper.remove_reverb(vocals, stage2_dir)
    stage2_result = (dry_vocals, None)
```

**Proposed:**
```python
if stage2_result is None:
    if settings.SOW_FREE_ONLY_MODE:
        raise StemSeparationWorkerError(
            f"MVSEP Stage 2 permanently unavailable in free-only mode "
            f"(quota not exhausted but retries exhausted). "
            f"Refusing local Stage 2 fallback. Job: {job.id}"
        )
    logger.info("MVSEP Stage 2 failed, using local Stage 2 fallback")
    _set_job_stage(job, "fallback_local_stage2")
    async with optional_semaphore(local_model_semaphore):
        dry_vocals, _ = await separator_wrapper.remove_reverb(vocals, stage2_dir)
    stage2_result = (dry_vocals, None)
```

---

### Change 3: Regression Test Using Real MVSEP Error Message

**File:** `ops/analysis-service/tests/test_mvsep_client.py`

The existing test `test_quota_exhausted_detected_from_400` (line 624) uses synthetic text `"You have exceeded your daily quota"`. Add a new test that asserts the **real** error message from production logs is detected:

```python
@pytest.mark.asyncio
async def test_quota_exhausted_detected_from_real_mvsep_message(client, mock_post):
    """Regression: real MVSEP daily-limit message must trigger quota detection.

    Production log showed this exact 400 response body was NOT detected as
    quota exhaustion, causing 3 wasted retries + unwanted local fallback.
    """
    req = httpx.Request("POST", "https://api.mvsep.com/api/create")
    mock_post.return_value = httpx.Response(
        400,
        json={
            "success": False,
            "errors": [
                "You have reached the limit of separations for today. "
                "Please try again tomorrow or consider signing up for a "
                "premium account."
            ],
        },
        request=req,
    )
    with pytest.raises(MvsepNonRetriableError):
        await client._submit_job(client._test_audio, sep_type=48, add_opt1=11)
    assert client._quota_exhausted is True
```

Also add a unit test for `_is_quota_exhausted()` directly, asserting the real message matches:

```python
def test_is_quota_exhausted_real_mvsep_message():
    from sow_analysis.services.mvsep_client import _is_quota_exhausted
    real = (
        "you have reached the limit of separations for today. "
        "please try again tomorrow or consider signing up for a premium account."
    )
    assert _is_quota_exhausted(real) is True
```

---

### Change 4: Free-Mode Fallback Tests

**File:** `ops/analysis-service/tests/test_mvsep_fallback.py`

**Pre-requisite:** Remove the duplicate `test_stage1_no_vocals_file_fallback` that appears twice in the file.

The existing tests assume local fallback (e.g., `test_mvsep_stage1_exhausts_retries_falls_back_full_local`, `test_quota_exhausted_uses_local`, `test_stage1_no_vocals_file_fallback`, `test_mvsep_stage1_succeeds_stage2_fails_handoff`). These run with `SOW_FREE_ONLY_MODE=False` (default) and should continue passing unchanged.

#### 4a. New test: free mode Stage 1 non-quota failure raises

```python
@pytest.mark.asyncio
async def test_free_mode_stage1_non_quota_failure_raises_no_local(
    mock_job, mock_mvsep_client, mock_separator_wrapper, monkeypatch
):
    """In free mode, non-quota Stage 1 exhaustion raises instead of local fallback."""
    monkeypatch.setattr(settings, "SOW_FREE_ONLY_MODE", True)
    mock_mvsep_client.is_quota_exhausted = False
    mock_mvsep_client.separate_vocals.side_effect = MvsepClientError("Persistent error")

    with pytest.raises(StemSeparationWorkerError, match="free-only mode"):
        await _separate_with_mvsep_fallback(
            input_path=Path("/tmp/input.mp3"),
            output_dir=Path("/tmp/output"),
            job=mock_job,
            mvsep_client=mock_mvsep_client,
            separator_wrapper=mock_separator_wrapper,
        )
    mock_separator_wrapper.separate_stems.assert_not_called()
```

#### 4b. New test: free mode quota exhaustion → wait → resume after UTC reset

```python
@pytest.mark.asyncio
async def test_free_mode_stage1_quota_exhausted_waits_and_retries(
    mock_job, mock_mvsep_client, mock_separator_wrapper, monkeypatch
):
    """In free mode, quota exhaustion triggers QuotaWaiter. Job resumes after reset."""
    monkeypatch.setattr(settings, "SOW_FREE_ONLY_MODE", True)

    call_count = 0
    async def side_effect_fn(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise MvsepNonRetriableError("Daily quota exhausted")
        return (Path("/tmp/v.flac"), Path("/tmp/i.flac"))

    mock_mvsep_client.separate_vocals.side_effect = side_effect_fn
    mock_mvsep_client.remove_reverb.return_value = (
        Path("/tmp/mvsep_dry.flac"), Path("/tmp/mvsep_reverb.flac")
    )

    # Simulate quota exhausted initially; will be reset during wait
    mock_mvsep_client._quota_exhausted = True
    mock_mvsep_client.is_quota_exhausted = True  # first read

    # Create a real QuotaWaiter with a probe that flips after first wait
    probe_calls = [False, True]  # first call exhausted, second available
    def probe_fn():
        return probe_calls.pop(0)

    qw = QuotaWaiter("mvsep_test", probe_fn, poll_interval=3600)

    result = await _separate_with_mvsep_fallback(
        input_path=Path("/tmp/input.mp3"),
        output_dir=Path("/tmp/output"),
        job=mock_job,
        mvsep_client=mock_mvsep_client,
        separator_wrapper=mock_separator_wrapper,
        mvsep_quota_waiter=qw,
    )

    assert result[1] == Path("/tmp/v.flac")  # vocals from 2nd call
    mock_separator_wrapper.separate_stems.assert_not_called()
    await qw.stop()
```

**Rationale for v2 change:** The v1 test used `AsyncMock` for `QuotaWaiter`, which didn't test the real `wait()` logic or the fact that `is_quota_exhausted` must actually become `False` (via probe) before the job retries. Using a real `QuotaWaiter` with a toggling probe function tests the actual integration.

#### 4c. New test: free mode Stage 2 non-quota failure raises

```python
@pytest.mark.asyncio
async def test_free_mode_stage2_non_quota_failure_raises_no_local(
    mock_job, mock_mvsep_client, mock_separator_wrapper, monkeypatch
):
    """In free mode, non-quota Stage 2 exhaustion raises instead of local Stage 2 fallback."""
    monkeypatch.setattr(settings, "SOW_FREE_ONLY_MODE", True)
    mock_mvsep_client.is_quota_exhausted = False
    mock_mvsep_client.remove_reverb.side_effect = MvsepClientError("Stage 2 persistent error")

    with pytest.raises(StemSeparationWorkerError, match="free-only mode"):
        await _separate_with_mvsep_fallback(
            input_path=Path("/tmp/input.mp3"),
            output_dir=Path("/tmp/output"),
            job=mock_job,
            mvsep_client=mock_mvsep_client,
            separator_wrapper=mock_separator_wrapper,
        )
    mock_separator_wrapper.remove_reverb.assert_not_called()
```

#### 4d. New tests: no-vocals-file — three distinct root causes

```python
@pytest.mark.asyncio
async def test_mvsep_stage1_api_empty_raises_retriable(
    mock_job, mock_mvsep_client, mock_separator_wrapper, monkeypatch
):
    """Case A: API returned no file entries — retriable MvsepClientError."""
    mock_mvsep_client.separate_vocals.side_effect = MvsepClientError(
        "MVSEP Stage 1 returned no file entries"
    )
    mock_mvsep_client.is_quota_exhausted = False

    with pytest.raises(MvsepClientError, match="no file entries"):
        await _separate_with_mvsep_fallback(
            input_path=Path("/tmp/input.mp3"),
            output_dir=Path("/tmp/output"),
            job=mock_job,
            mvsep_client=mock_mvsep_client,
            separator_wrapper=mock_separator_wrapper,
        )
    mock_separator_wrapper.separate_stems.assert_not_called()


@pytest.mark.asyncio
async def test_mvsep_stage1_download_failure_raises_retriable(
    mock_job, mock_mvsep_client, mock_separator_wrapper, monkeypatch
):
    """Case B: Files returned but download failed — retriable MvsepClientError."""
    mock_mvsep_client.separate_vocals.side_effect = MvsepClientError(
        "MVSEP Stage 1: file(s) returned but download failed"
    )
    mock_mvsep_client.is_quota_exhausted = False

    with pytest.raises(MvsepClientError, match="download failed"):
        await _separate_with_mvsep_fallback(
            input_path=Path("/tmp/input.mp3"),
            output_dir=Path("/tmp/output"),
            job=mock_job,
            mvsep_client=mock_mvsep_client,
            separator_wrapper=mock_separator_wrapper,
        )
    mock_separator_wrapper.separate_stems.assert_not_called()


@pytest.mark.asyncio
async def test_mvsep_stage1_classification_failure_raises_non_retriable(
    mock_job, mock_mvsep_client, mock_separator_wrapper, monkeypatch
):
    """Case C: Files downloaded but none classified as vocals — non-retriable MvsepParsingError."""
    mock_mvsep_client.separate_vocals.side_effect = MvsepParsingError(
        "MVSEP Stage 1 returned 2 file(s) but none could be classified as vocals"
    )
    mock_mvsep_client.is_quota_exhausted = False

    with pytest.raises(MvsepParsingError, match="none could be classified as vocals"):
        await _separate_with_mvsep_fallback(
            input_path=Path("/tmp/input.mp3"),
            output_dir=Path("/tmp/output"),
            job=mock_job,
            mvsep_client=mock_mvsep_client,
            separator_wrapper=mock_separator_wrapper,
        )
    mock_separator_wrapper.separate_stems.assert_not_called()


@pytest.mark.asyncio
async def test_mvsep_stage1_classification_failure_fails_in_both_modes(
    mock_job, mock_mvsep_client, mock_separator_wrapper, monkeypatch
):
    """Case C must fail in both free and non-free modes — no local fallback."""
    for free_mode in (True, False):
        monkeypatch.setattr(settings, "SOW_FREE_ONLY_MODE", free_mode)
        mock_mvsep_client.separate_vocals.side_effect = MvsepParsingError(
            "classification failed"
        )
        mock_mvsep_client.is_quota_exhausted = False

        with pytest.raises(MvsepParsingError, match="classification failed"):
            await _separate_with_mvsep_fallback(
                input_path=Path("/tmp/input.mp3"),
                output_dir=Path("/tmp/output"),
                job=mock_job,
                mvsep_client=mock_mvsep_client,
                separator_wrapper=mock_separator_wrapper,
            )
        mock_separator_wrapper.separate_stems.assert_not_called()
```

---

## 5. Startup Warning Enhancement

**File:** `ops/analysis-service/src/sow_analysis/main.py`  
**Location:** After MVSEP client initialization (around line 99).

Add a loud warning at startup when the service begins in free-only mode with MVSEP unavailable:

```python
if settings.SOW_FREE_ONLY_MODE and mvsep_client and not mvsep_client.is_available:
    logger.warning(
        "SOW_FREE_ONLY_MODE=True but MVSEP is unavailable at startup "
        "(missing API key, disabled, or quota already exhausted). "
        "Stem-separation jobs will wait for quota or fail permanently."
    )
```

**Rationale:** Helps operators understand why all stem-separation jobs are immediately waiting/failing after a restart, especially if quota was exhausted before the restart.

---

## 6. Expected Behavior After Changes

### Scenario A: MVSEP quota exhausted mid-batch (free mode)

1. Job N submits to MVSEP → HTTP 400 with real "reached the limit" message.
2. `_is_quota_exhausted()` returns `True` → `_quota_exhausted = True`, raises `MvsepNonRetriableError`.
3. `_run_mvsep_stage_with_retries` sees `MvsepNonRetriableError` → breaks immediately (no 3× retries).
4. `while True` outer loop sees `is_quota_exhausted == True` → calls `_wait_for_mvsep_quota()`.
5. `QuotaWaiter` blocks the job, periodically polls MVSEP availability.
6. **Job N+1** starts → pre-check sees `mvsep_client.is_available == False` and `is_quota_exhausted == True` → enters quota-wait path directly (no MVSEP API call, no retries).
7. After UTC midnight (or whenever MVSEP resets), `is_available` returns `True`.
8. All waiting jobs resume MVSEP Stage 1 from scratch and succeed.
9. **No local stem separation ever runs in free mode.**

### Scenario B: Non-quota MVSEP failure (free mode)

1. Job submits to MVSEP → network error or timeout.
2. `_run_mvsep_stage_with_retries` retries 3× (or 6× for queue-full/timeout) with backoff.
3. Exhaustion: `stage1_result is None`, `is_quota_exhausted == False`.
4. New free-mode guard: raises `StemSeparationWorkerError`.
5. Job marked FAILED. User can resubmit later.
6. **No local stem separation.**

### Scenario C: Quota exhausted mid-batch (non-free mode)

- Change 1 improves this case too: keyword detection sets `_quota_exhausted = True`, raises `MvsepNonRetriableError`, breaks retries immediately.
- However, `SOW_FREE_ONLY_MODE=False` means the outer loop's quota-wait check breaks out → local fallback runs as before.
- Subsequent jobs: `is_available` returns `False` for the rest of the UTC day → local fallback runs immediately (no wasted MVSEP retries).
- **Net improvement in non-free mode too: fewer wasted MVSEP API calls.**

### Scenario D: MVSEP Stage 1 succeeds but no vocals file

| Case | Root Cause | Behavior After v2 |
|---|---|---|
| A | API returned no files | `MvsepClientError` raised → retriable → `_run_mvsep_stage_with_retries` retries → may recover |
| B | Download failure | `MvsepClientError` raised → retriable → `_run_mvsep_stage_with_retries` retries → may recover |
| C | Classification failure | `MvsepParsingError` raised → non-retriable → bypasses retries → job FAILS in **both** modes |

- **Cases A & B:** Retry may recover from transient issues. After exhaustion, existing free-mode raise / non-free fallback behavior applies.
- **Case C:** Fails immediately in both modes. No local fallback. Operators see detailed error with API types and download URLs for debugging.

---

## 7. Files Touched

| File | Change |
|---|---|
| `ops/analysis-service/src/sow_analysis/services/mvsep_client.py` | Add 3 keywords to `_QUOTA_KEYWORDS` tuple; add `MvsepParsingError` exception; update `separate_vocals()` to distinguish 3 root causes and raise appropriate exceptions |
| `ops/analysis-service/src/sow_analysis/workers/stem_separation.py` | Gate 2 local-fallback paths behind `not settings.SOW_FREE_ONLY_MODE` + raise; remove no-vocals local fallback (now handled by exceptions from `separate_vocals()`) |
| `ops/analysis-service/src/sow_analysis/main.py` | Add startup warning when free-only mode + MVSEP unavailable |
| `ops/analysis-service/tests/test_mvsep_client.py` | Add regression test with real MVSEP error message; add `_is_quota_exhausted()` unit test; add tests for `MvsepParsingError` and three root-cause scenarios |
| `ops/analysis-service/tests/test_mvsep_fallback.py` | Remove duplicate test; add free-mode tests; add three-case no-vocals tests |

**No config changes.** No new env vars. No changes to `queue.py`, `quota_waiter.py`.

---

## 8. Verification

```bash
cd ops/analysis-service
uv run --extra dev pytest tests/test_mvsep_client.py -v
uv run --extra dev pytest tests/test_mvsep_fallback.py -v
uv run --extra dev pytest tests/test_quota_waiter.py -v
```

All existing tests must pass (non-free-mode behavior preserved). New free-mode and regression tests must pass.

---

## 9. Risks & Mitigations (Updated from v1)

| Risk | Severity | Mitigation |
|---|---|---|
| New keywords cause false-positive quota detection on an unrelated MVSEP error | Low | The three new phrases (`"reached the limit"`, `"separations for today"`, `"try again tomorrow"`) are extremely specific to daily-limit messaging. False positive probability is negligible. |
| Free-mode jobs that previously would have used local fallback now FAIL on non-quota errors | Medium (expected) | This is the intended behavior per user directive. User prefers job failure over 1-hour local processing. Mitigation: clear error message, user can resubmit. |
| Existing tests break if they implicitly relied on free-mode behavior | Low | Verified: `test_mvsep_fallback.py` does not set `SOW_FREE_ONLY_MODE`; default is `False`. Existing tests run in non-free mode and are unaffected. |
| `is_quota_exhausted` stays `True` for rest of UTC day even if quota is per-hour | Low | Current `_check_quota_reset()` only resets at UTC midnight. This matches MVSEP's documented daily free-tier limit. No change needed. |
| **Service restart loses `_quota_exhausted` state → one wasted API call** | **Medium** | **Documented limitation: in-memory state is lost on restart. One job will waste a call before re-detecting. Mitigation: startup warning added to alert operators.** |
| **Case C (classification failure) now fails in non-free mode → previously-working jobs may fail** | **Medium** | **This path was already a parsing bug being masked. If production sees this, it means MVSEP changed response format. Better to fail loudly so we can fix classification, than produce silently-wrong stems. Detailed error message includes API types and download URLs for debugging.** |
| **New `MvsepParsingError` not caught by existing retry logic** | **Low** | `MvsepParsingError` subclasses `MvsepNonRetriableError`, which is already caught by `_run_mvsep_stage_with_retries` and breaks immediately. No code changes needed in worker retry logic. |

---

## 10. v1 → v2 Changelog

| # | Change | Rationale |
|---|---|---|
| 1 | Issue 4: no-vocals-file path now distinguishes 3 root causes (API empty, download failure, classification failure) | Different causes have different retry semantics — transient failures should retry, parsing bugs should not |
| 2 | New `MvsepParsingError(MvsepNonRetriableError)` exception type | Distinguishes parsing bugs from transient failures |
| 3 | `separate_vocals()` raises `MvsepClientError` for Cases A/B (retriable) and `MvsepParsingError` for Case C (non-retriable) | Enables targeted retry behavior without worker-side changes |
| 4 | Worker-side no-vocals local fallback removed — exceptions propagate from `separate_vocals()` | Cleaner separation of concerns; no need for worker to distinguish cases |
| 5 | Issue 3: free-mode quota-wait test uses real `QuotaWaiter` + toggling probe | v1's mocked `AsyncMock` waiter could not properly test the `is_available` reset logic |
| 6 | Issue 6: remove duplicate `test_stage1_no_vocals_file_fallback` before adding new tests | Prevents test shadowing |
| 7 | Issue 7: rephrase error messages from "non-quota error" to "permanently unavailable" | More accurate description of the failure mode |
| 8 | Issue 2: add startup warning for free-mode + unavailable MVSEP | Alert operators to the post-restart re-detection waste |
| 9 | Issue 1: document that `is_quota_exhausted` should not be set directly on real client | Prevents confusion in tests |
| 10 | New tests: 4 tests for three root-cause scenarios (API empty, download failure, classification failure, both-modes) | Ensures all three cases are covered |

---

(End of file)
