# Plan: MVSEP Quota Keyword Fix + Free-Mode Local Fallback Removal (v1)

> **Status:** Planning (read-only). Do not implement until approved.
> **Related:** `specs/free-only-patient-mode-v4.md`, `specs/mvsep-stage2-timeout-concurrency-quota-detection-v2.md`

## Problem

Production logs (`~/tmp/sow/sow_analysis_5_last_restart.log`) show that after MVSEP returns its daily-limit-exhausted response, the Analysis Service:

1. **Retries the same job 3× with 5/10/20s backoff** before giving up (lines 284–293 of log).
2. **Falls back to local audio-separator** (`"MVSEP Stage 1 failed, falling back to full local pipeline"`, log line 294), which takes ~1 hour per song on CPU.
3. **Subsequent jobs still attempt MVSEP from scratch** (log lines 431–689) — repeat the whole 3-retry-then-local-fallback cycle for every queued stem-separation job.

### Root Cause: Keyword Mismatch

`mvsep_client.py:22-33` defines `_QUOTA_KEYWORDS`:

```python
_QUOTA_KEYWORDS = (
    "daily limit", "daily quota", "limit exceeded", "quota exceeded",
    "limit reached", "too many jobs", "too many requests today",
    "day limit", "per day", "exceeded your",
)
```

The **actual** MVSEP API error message in the 400 response body is:

> `"You have reached the limit of separations for today. Please try again tomorrow or consider signing up for a premium account."`

Verified programmatically: **0 of 10 keywords match** the lowercased real error. Specifically, the list has `"limit reached"` but the real text has `"reached the limit"` (reversed word order) — and no other keyword overlaps.

### Consequence Chain

Because `_is_quota_exhausted()` returns `False` for the real error:

1. In `_submit_job` (`mvsep_client.py:277-285`), the HTTP 400 falls through to the generic `raise MvsepClientError(...)` instead of `MvsepNonRetriableError`.
2. `_quota_exhausted` flag is never set to `True`, so `MvsepClient.is_quota_exhausted` returns `False`.
3. In `_run_mvsep_stage_with_retries` (`stem_separation.py:111-137`), the generic `MvsepClientError` is treated as a retriable "other error" → 3 retries with 5/10/20s backoff.
4. After exhaustion, the outer `while True` loop (`stem_separation.py:335-353`) checks `is_quota_exhausted` — still `False` — so it breaks out instead of calling `_wait_for_mvsep_quota()`.
5. Control reaches the local fallback (`stem_separation.py:355-360`), violating the user's free-mode directive.
6. Because `_quota_exhausted` is never set, every subsequent job also sees `mvsep_client.is_available == True` (line 278) and repeats the entire failure cycle.

The free-mode infrastructure (`QuotaWaiter`, `_wait_for_mvsep_quota()`, the SOW_FREE_ONLY_MODE pre-check and post-stage retry loop) is fully implemented — it just never engages.

---

## Goals

1. **Detect MVSEP's real daily-limit message** as quota exhaustion (set `_quota_exhausted = True`, raise `MvsepNonRetriableError`).
2. **Stop retrying the same job 3× when quota is already exhausted** — break immediately on first quota detection.
3. **Stop subsequent jobs from re-trying MVSEP** — they should see `is_available == False` immediately and enter the quota-wait path.
4. **In SOW_FREE_ONLY_MODE: never fall back to local stem separation** — wait for quota reset with periodic checks instead.
5. **Preserve non-free-mode behavior unchanged** — local fallback remains the safety net when `SOW_FREE_ONLY_MODE=False`.

---

## Changes

### Change 1: Extend `_QUOTA_KEYWORDS` (PRIMARY FIX)

**File:** `ops/analysis-service/src/sow_analysis/services/mvsep_client.py`
**Lines:** 22-33

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

**Rationale:** Substring matching on highly-specific phrases avoids false positives. The three new keywords all appear verbatim in the real MVSEP response; any one of them would suffice, but including all three provides defense against minor phrasing variations.

**Effect:** Once the real message is detected:
- `_quota_exhausted = True` is set on the client singleton
- `MvsepNonRetriableError` is raised instead of generic `MvsepClientError`
- All existing free-mode quota-wait infrastructure engages automatically

### Change 2: Remove Local Fallback in SOW_FREE_ONLY_MODE

**File:** `ops/analysis-service/src/sow_analysis/workers/stem_separation.py`

Three local-fallback code paths currently fire regardless of `SOW_FREE_ONLY_MODE`. Gate them behind `not settings.SOW_FREE_ONLY_MODE`, and in free mode raise `StemSeparationWorkerError` instead.

#### 2a. Stage 1 exhausted retries — non-quota failure (lines 355-360)

**Current:**
```python
if stage1_result is None:
    # Stage 1 MVSEP failed — fall back to full local pipeline
    logger.info("MVSEP Stage 1 failed, falling back to full local pipeline")
    _set_job_stage(job, "fallback_local")
    async with optional_semaphore(local_model_semaphore):
        return await separator_wrapper.separate_stems(input_path, output_dir)
```

**Proposed:**
```python
if stage1_result is None:
    if settings.SOW_FREE_ONLY_MODE:
        # Free mode: no local fallback. Quota waits already handled in
        # the while-loop above; reaching here means non-quota exhaustion
        # (e.g., repeated network errors / timeouts).
        raise StemSeparationWorkerError(
            f"MVSEP Stage 1 failed after retries in free-only mode "
            f"(non-quota error). Refusing local fallback. Job: {job.id}"
        )
    logger.info("MVSEP Stage 1 failed, falling back to full local pipeline")
    _set_job_stage(job, "fallback_local")
    async with optional_semaphore(local_model_semaphore):
        return await separator_wrapper.separate_stems(input_path, output_dir)
```

#### 2b. Stage 1 succeeded but no vocals file (lines 364-368)

**Current:**
```python
if not vocals:
    logger.error("MVSEP Stage 1 succeeded but no vocals file produced")
    _set_job_stage(job, "fallback_local")
    async with optional_semaphore(local_model_semaphore):
        return await separator_wrapper.separate_stems(input_path, output_dir)
```

**Proposed:**
```python
if not vocals:
    logger.error("MVSEP Stage 1 succeeded but no vocals file produced")
    if settings.SOW_FREE_ONLY_MODE:
        raise StemSeparationWorkerError(
            f"MVSEP Stage 1 returned no vocals in free-only mode. "
            f"Refusing local fallback. Job: {job.id}"
        )
    _set_job_stage(job, "fallback_local")
    async with optional_semaphore(local_model_semaphore):
        return await separator_wrapper.separate_stems(input_path, output_dir)
```

#### 2c. Stage 2 exhausted retries — non-quota failure (lines 404-410)

**Current:**
```python
if stage2_result is None:
    # Stage 2 MVSEP failed — local Stage 2 only (cross-backend handoff)
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
            f"MVSEP Stage 2 failed after retries in free-only mode "
            f"(non-quota error). Refusing local fallback. Job: {job.id}"
        )
    logger.info("MVSEP Stage 2 failed, using local Stage 2 fallback")
    _set_job_stage(job, "fallback_local_stage2")
    async with optional_semaphore(local_model_semaphore):
        dry_vocals, _ = await separator_wrapper.remove_reverb(vocals, stage2_dir)
    stage2_result = (dry_vocals, None)
```

**Note on existing already-correct paths (no change needed):**
- Pre-check at lines 278-301: already raises `StemSeparationWorkerError` for permanent unavailability in free mode, and already calls `_wait_for_mvsep_quota()` for quota exhaustion in free mode.
- Stage 1 `while True` loop (lines 335-353): already calls `_wait_for_mvsep_quota()` and retries when `is_quota_exhausted` is True in free mode. Will engage correctly once Change 1 fixes keyword detection.
- Stage 2 `while True` loop (lines 383-402): same — already correct.

### Change 3: Regression Test Using Real MVSEP Error Message

**File:** `ops/analysis-service/tests/test_mvsep_client.py`

The existing test `test_quota_exhausted_detected_from_400` (line 624) uses synthetic text `"You have exceeded your daily quota"`. Add a new test (or augment the existing one) that asserts the **real** error message from production logs is detected:

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

### Change 4: Free-Mode Fallback Tests

**File:** `ops/analysis-service/tests/test_mvsep_fallback.py`

The existing tests assume local fallback (e.g., `test_mvsep_stage1_exhausts_retries_falls_back_full_local`, `test_quota_exhausted_uses_local`, `test_stage1_no_vocals_file_fallback`, `test_mvsep_stage1_succeeds_stage2_fails_handoff`). These run with `SOW_FREE_ONLY_MODE=False` (default) and should continue passing unchanged.

Add new tests for free-mode behavior:

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


@pytest.mark.asyncio
async def test_free_mode_stage1_quota_exhausted_waits_and_retries(
    mock_job, mock_mvsep_client, mock_separator_wrapper, monkeypatch
):
    """In free mode, quota exhaustion triggers QuotaWaiter instead of local fallback."""
    monkeypatch.setattr(settings, "SOW_FREE_ONLY_MODE", True)
    # First call raises non-retriable quota error; second succeeds
    mock_mvsep_client.separate_vocals.side_effect = [
        MvsepNonRetriableError("Daily quota exhausted"),
        (Path("/tmp/v.flac"), Path("/tmp/i.flac")),
    ]
    mock_mvsep_client.is_quota_exhausted = True  # initially exhausted
    # Mock QuotaWaiter that returns available immediately
    qw = AsyncMock()
    qw.mark_exhausted = AsyncMock()
    qw.wait = AsyncMock(return_value=True)

    # Toggle is_quota_exhausted off after first wait
    ...  # (details: use a property mock that flips after wait() is called)

    result = await _separate_with_mvsep_fallback(
        input_path=Path("/tmp/input.mp3"),
        output_dir=Path("/tmp/output"),
        job=mock_job,
        mvsep_client=mock_mvsep_client,
        separator_wrapper=mock_separator_wrapper,
        mvsep_quota_waiter=qw,
    )
    qw.mark_exhausted.assert_called()
    mock_separator_wrapper.separate_stems.assert_not_called()


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

*(Test implementations to be finalized during implementation; the free-mode paths are the new behavior to lock in.)*

---

## Expected Behavior After Changes

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
2. `_run_mvsep_stage_with_retries` retries 3× with 5/10/20s backoff.
3. Exhaustion: `stage1_result is None`, `is_quota_exhausted == False`.
4. New free-mode guard: raises `StemSeparationWorkerError` with "free-only mode" message.
5. Job marked FAILED. User can resubmit later.
6. **No local stem separation.**

### Scenario C: Quota exhausted mid-batch (non-free mode)

- Change 1 improves this case too: keyword detection sets `_quota_exhausted = True`, raises `MvsepNonRetriableError`, breaks retries immediately.
- However, `SOW_FREE_ONLY_MODE=False` means the outer loop's quota-wait check (`if not (settings.SOW_FREE_ONLY_MODE and ...)`) breaks out → local fallback runs as before.
- Subsequent jobs: `is_available` returns `False` for the rest of the UTC day → local fallback runs immediately (no wasted MVSEP retries).
- **Net improvement in non-free mode too: fewer wasted MVSEP API calls.**

---

## Files Touched

| File | Change |
|---|---|
| `ops/analysis-service/src/sow_analysis/services/mvsep_client.py` | Add 3 keywords to `_QUOTA_KEYWORDS` tuple |
| `ops/analysis-service/src/sow_analysis/workers/stem_separation.py` | Gate 3 local-fallback paths behind `not settings.SOW_FREE_ONLY_MODE`; raise `StemSeparationWorkerError` in free mode |
| `ops/analysis-service/tests/test_mvsep_client.py` | Add regression test with real MVSEP error message; add `_is_quota_exhausted()` unit test |
| `ops/analysis-service/tests/test_mvsep_fallback.py` | Add free-mode tests asserting `StemSeparationWorkerError` raised instead of local fallback |

**No config changes.** No new env vars. No changes to `queue.py`, `quota_waiter.py`, or `main.py`.

---

## Verification

```bash
cd ops/analysis-service
uv run --extra dev pytest tests/test_mvsep_client.py -v
uv run --extra dev pytest tests/test_mvsep_fallback.py -v
uv run --extra dev pytest tests/test_quota_waiter.py -v
```

All existing tests must pass (non-free-mode behavior preserved). New free-mode and regression tests must pass.

---

## Risks & Mitigations

| Risk | Mitigation |
|---|---|
| New keywords cause false-positive quota detection on an unrelated MVSEP error | The three new phrases (`"reached the limit"`, `"separations for today"`, `"try again tomorrow"`) are extremely specific to daily-limit messaging. False positive probability is negligible. |
| Free-mode jobs that previously would have used local fallback now FAIL on non-quota errors | This is the intended behavior per user directive. User prefers job failure over 1-hour local processing. Mitigation: clear error message, user can resubmit. |
| Existing tests break if they implicitly relied on free-mode behavior | Verified: `test_mvsep_fallback.py` does not set `SOW_FREE_ONLY_MODE`; default is `False`. Existing tests run in non-free mode and are unaffected. |
| `is_quota_exhausted` stays `True` for rest of UTC day even if quota is actually per-hour | Current `_check_quota_reset()` only resets at UTC midnight. This matches MVSEP's documented daily free-tier limit. No change needed. |
