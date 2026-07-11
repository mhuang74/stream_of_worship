# MVSEP: Serialize API Submissions to Prevent "Queue Already Full"

## Context

MVSEP's free-tier API allows **only one unprocessed file in queue per API token** at a
time. The analysis service already has an `asyncio.Semaphore` on the `MvsepClient`
singleton that wraps the entire submit + poll + download sequence for a stage. However,
its default is `SOW_MVSEP_MAX_CONCURRENT = 3`, which permits 3 concurrent MVSEP
operations. When multiple STEM_SEPARATION jobs run concurrently, the second submission
(under ~1 second later) hits:

```
MVSEP queue full: {"success":false,"errors":["You already have unprocessed file in
queue. Please wait before adding new file!"]}
```

The existing exponential backoff retry (`QUEUE_FULL_BACKOFF_BASE=30s`, up to 6 retries)
recovers eventually, but it wastes time (30s+ per collision) and risks burning daily
quota on failed submits.

### Root Cause

The semaphore value (3) exceeds MVSEP's actual concurrency limit (1). The semaphore
already covers the correct scope (submit + poll + download for both Stage 1 and Stage 2),
but it allows too many concurrent slots.

### Existing Prior Work

A prior spec (`specs/mvsep-queue-full-mutex-and-exponential-backoff.md`) proposed an
`asyncio.Lock`. The implementation instead used a configurable semaphore
(`asyncio.Semaphore(self._max_concurrent)`) with default 3. The exponential backoff from
that spec was fully implemented. Only the concurrency value is wrong.

## Design Decision

**Lower `SOW_MVSEP_MAX_CONCURRENT` default from 3 to 1.** This is the simplest fix — the
semaphore already has the correct scope (wrapping submit + poll + download for both
Stage 1 and Stage 2 across all jobs via the singleton client). Setting it to 1 makes it
behave as a mutex, serializing all MVSEP API calls.

- **Both stages covered**: The semaphore is shared across `separate_vocals()` (Stage 1)
  and `remove_reverb()` (Stage 2) on the same singleton client instance, so both stages
  are serialized.
- **Lock covers waiting period**: The `async with self._semaphore` block wraps
  submit + poll + download, so while one song is being processed (in queue), a second
  song cannot be submitted — it blocks on the semaphore until the first completes.
- **Queue-full retry stays as safety net**: The exponential backoff
  (`_compute_mvsep_backoff`, `MVSEP_QUEUE_FULL_MAX_RETRIES=6`) remains for edge cases
  like external traffic on the same API token or the brief window between poll-done and
  lock-release.
- **Still env-configurable**: Operators can override via `SOW_MVSEP_MAX_CONCURRENT` env
  var if MVSEP raises its per-token queue limit in the future.

## Files to Modify

| File | Action |
|------|--------|
| `ops/analysis-service/src/sow_analysis/config.py` | Change `SOW_MVSEP_MAX_CONCURRENT` default from `3` to `1`; update comment |
| `ops/analysis-service/tests/test_mvsep_client.py` | Update `MockSettings.SOW_MVSEP_MAX_CONCURRENT` from `3` to `1`; update `client` fixture `max_concurrent=3` → `max_concurrent=1`; rewrite semaphore concurrency tests to validate serialization instead of parallelism |

## Implementation Steps

### Step 1: Lower default in `config.py`

**File:** `ops/analysis-service/src/sow_analysis/config.py`, line 159

Change:
```python
SOW_MVSEP_MAX_CONCURRENT: int = 3  # Max concurrent MVSEP API operations
```

To:
```python
SOW_MVSEP_MAX_CONCURRENT: int = 1  # Max concurrent MVSEP API operations (MVSEP free-tier allows 1 pending job per token)
```

### Step 2: Update test mock settings

**File:** `ops/analysis-service/tests/test_mvsep_client.py`

**Line 36** — Change `MockSettings`:
```python
SOW_MVSEP_MAX_CONCURRENT = 3
```
To:
```python
SOW_MVSEP_MAX_CONCURRENT = 1
```

**Line 98** — Change `client` fixture:
```python
max_concurrent=3,
```
To:
```python
max_concurrent=1,
```

### Step 3: Rewrite semaphore concurrency tests

**File:** `ops/analysis-service/tests/test_mvsep_client.py`

With `max_concurrent=1`, the existing tests that assert 3-way concurrency no longer hold.
Replace them with serialization tests.

#### Replace `test_separate_vocals_semaphore_allows_concurrency` (line 486)

Old test asserted 3 concurrent `separate_vocals` calls all enter simultaneously
(`max_in_flight == 3`). With `max_concurrent=1`, only 1 can be in flight at a time.

**New test:** `test_separate_vocals_semaphore_serializes_calls`

```python
@pytest.mark.asyncio
async def test_separate_vocals_semaphore_serializes_calls(client, tmp_path, mock_response):
    """With max_concurrent=1, concurrent separate_vocals calls are serialized."""
    import asyncio

    in_flight = 0
    max_in_flight = 0

    async def mock_submit_job(*args, **kwargs):
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        await asyncio.sleep(0.05)
        in_flight -= 1
        return "test_hash"

    async def mock_poll_job(job_hash):
        return {"success": True, "status": "done", "data": {"files": []}}

    async def mock_download(file_entries, output_dir):
        return []

    output_dir = tmp_path / "output"
    output_dir.mkdir()

    with patch.object(client, "_submit_job", side_effect=mock_submit_job):
        with patch.object(client, "_poll_job", side_effect=mock_poll_job):
            with patch.object(client, "_download_files", side_effect=mock_download):
                # Launch 3 concurrent calls — serialized, never more than 1 in flight
                results = await asyncio.gather(
                    client.separate_vocals(client._test_audio, output_dir),
                    client.separate_vocals(client._test_audio, output_dir),
                    client.separate_vocals(client._test_audio, output_dir),
                )

    assert all(r == (None, None) for r in results)
    # With max_concurrent=1, never more than 1 in flight
    assert max_in_flight == 1
```

#### Replace `test_separate_vocals_semaphore_blocks_4th` (line 533)

Old test asserted that with 4 concurrent calls and `max_concurrent=3`, only 3 are in
flight and the 4th blocks. With `max_concurrent=1`, the 2nd call should block.

**New test:** `test_separate_vocals_semaphore_blocks_2nd`

```python
@pytest.mark.asyncio
async def test_separate_vocals_semaphore_blocks_2nd(client, tmp_path, mock_response):
    """With max_concurrent=1, a 2nd concurrent call blocks until the 1st completes."""
    import asyncio

    in_flight = 0
    max_in_flight = 0
    release = asyncio.Event()

    async def mock_submit_job(*args, **kwargs):
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        await release.wait()
        in_flight -= 1
        return "test_hash"

    async def mock_poll_job(job_hash):
        return {"success": True, "status": "done", "data": {"files": []}}

    async def mock_download(file_entries, output_dir):
        return []

    output_dir = tmp_path / "output"
    output_dir.mkdir()

    with patch.object(client, "_submit_job", side_effect=mock_submit_job):
        with patch.object(client, "_poll_job", side_effect=mock_poll_job):
            with patch.object(client, "_download_files", side_effect=mock_download):
                # Launch 2 concurrent calls
                tasks = [
                    asyncio.create_task(client.separate_vocals(client._test_audio, output_dir))
                    for _ in range(2)
                ]
                await asyncio.sleep(0.1)
                # Only 1 should be in flight; 2nd is blocked on semaphore
                assert max_in_flight == 1
                release.set()
                results = await asyncio.gather(*tasks)

    assert all(r == (None, None) for r in results)
    assert max_in_flight == 1
```

### Step 4: Add cross-stage serialization test (NEW)

Add a new test verifying that Stage 1 (`separate_vocals`) and Stage 2 (`remove_reverb`)
from different songs are also serialized, since they share the same semaphore on the
singleton client.

```python
@pytest.mark.asyncio
async def test_mvsep_semaphore_serializes_stage1_and_stage2(client, tmp_path, mock_response):
    """Stage 1 and Stage 2 share the semaphore — they cannot run concurrently."""
    import asyncio

    in_flight = 0
    max_in_flight = 0

    async def mock_submit_job(*args, **kwargs):
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        await asyncio.sleep(0.05)
        in_flight -= 1
        return "test_hash"

    async def mock_poll_job(job_hash):
        return {"success": True, "status": "done", "data": {"files": []}}

    async def mock_download(file_entries, output_dir):
        return []

    output_dir = tmp_path / "output"
    output_dir.mkdir()

    vocals_path = tmp_path / "vocals.flac"
    vocals_path.write_bytes(b"fake vocals")

    with patch.object(client, "_submit_job", side_effect=mock_submit_job):
        with patch.object(client, "_poll_job", side_effect=mock_poll_job):
            with patch.object(client, "_download_files", side_effect=mock_download):
                # Launch Stage 1 and Stage 2 concurrently — should serialize
                await asyncio.gather(
                    client.separate_vocals(client._test_audio, output_dir),
                    client.remove_reverb(vocals_path, output_dir),
                )

    assert max_in_flight == 1
```

## What Does NOT Change

- **Semaphore mechanism**: The `asyncio.Semaphore` on `MvsepClient` and its lazy
  initialization remain unchanged. Only the default value changes.
- **Scope of semaphore**: Already wraps submit + poll + download in both
  `separate_vocals()` and `remove_reverb()`. No structural change needed.
- **Exponential backoff**: `_compute_mvsep_backoff` with `QUEUE_FULL_BACKOFF_*`
  constants stays as a safety net for external contention.
- **Queue dispatch model**: `queue.py`'s `asyncio.create_task` per job stays
  unchanged — jobs still run concurrently, but MVSEP calls serialize on the semaphore.
- **No new env vars**: `SOW_MVSEP_MAX_CONCURRENT` already exists; only the default value
  changes.

## Verification

```bash
cd ops/analysis-service
uv run --extra dev pytest tests/test_mvsep_client.py -v
```

Key tests to verify:
- `test_separate_vocals_semaphore_serializes_calls` — max_in_flight == 1
- `test_separate_vocals_semaphore_blocks_2nd` — 2nd call blocked until 1st completes
- `test_mvsep_semaphore_serializes_stage1_and_stage2` — Stage 1 + Stage 2 serialized
- All existing non-semaphore tests pass unchanged
