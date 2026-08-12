# Plan: Reuse cached LLM theme/posture classification in COMPONENT_ANALYSIS jobs

## Context

When rerunning a COMPONENT_ANALYSIS job for the same song (same `content_hash`),
the beat-grid and component-extraction steps hit their caches (local + R2) and
complete in ~0ms. However, the LLM theme/posture classification step (30-50s
of OpenAI API calls) always re-executes from scratch, even though prior LLM
results exist.

Job log evidence:

```
[job_9dc6fa08ba80] Component cache hit (local): dbd506660aa3ee05...
[job_9dc6fa08ba80] Step completed: Component extraction (0.00s)
[job_9dc6fa08ba80] Step started: LLM theme/posture classification
[job_9dc6fa08ba80] LLM classification: 17 to classify, 0 skipped (essential-only), 17 total
...
[job_9dc6fa08ba80] Step completed: LLM theme/posture classification (31.58s)
```

The `ThemeClassifier` has no persistent cache — a fresh instance is created per
job (queue.py:1023), and `classify_components()` calls the LLM unconditionally.

## Root Cause

Two bugs compound:

### Bug 1: components.json is saved BEFORE LLM runs, so it never carries LLM results

`extract_components()` (components.py:1280) serializes and saves
`components.json` to local cache + R2 at lines 1450-1455 — **before** the LLM
classification step in queue.py runs. The serialized payload includes
`theme`, `vocal_posture`, etc. (components.py:1494-1500), but at save time
these fields are all `None` because LLM hasn't run yet.

After LLM classification modifies the `ComponentInstance` objects in-memory
(queue.py:1024-1028), **nothing re-persists components.json**. The LLM results
exist only in the job result stored in the SQLite job DB. So even on a cache
hit, the deserialized components have `theme=None`.

### Bug 2: LLM step runs unconditionally — no short-circuit check

The queue worker (queue.py:1014-1030) always calls
`ThemeClassifier.classify_components()` whenever `classify_theme` or
`classify_vocal_posture` is set, regardless of whether the components already
have populated LLM fields from a prior cached run.

## Changes

### 1. Re-persist components.json AFTER LLM classification

**File:** `ops/analysis-service/src/sow_analysis/workers/queue.py`
**Location:** After the LLM classification try/except block (after line 1030),
before the Result conversion step (line 1032).

After LLM classification completes (whether it succeeded or partially
succeeded), re-serialize the components and overwrite the cached
`components.json` in both local cache and R2. This ensures future reruns can
detect and reuse the LLM results.

```python
# Re-persist components.json with LLM results now populated.
if request.options.classify_theme or request.options.classify_vocal_posture:
    try:
        from .components import _serialize_components

        hash_prefix = request.content_hash[:12]
        payload = _serialize_components(
            components, request.content_hash, hash_prefix, source
        )
        self.cache_manager.save_component_result(request.content_hash, payload)
        if self.r2_client is not None:
            await self.r2_client.upload_component_result(hash_prefix, payload)
    except Exception as e:
        logger.warning(f"Failed to re-persist components.json after LLM: {e}")
```

### 2. Add short-circuit check before LLM step

**File:** `ops/analysis-service/src/sow_analysis/workers/classifier.py`
**Location:** New helper function near `_is_essential` (after line 159).

Add a function that checks whether all components that need LLM classification
already have the required LLM fields populated:

```python
def has_cached_llm_fields(
    components: list[ComponentInstance],
    classify_theme: bool,
    classify_vocal_posture: bool,
    all_components: bool = False,
) -> bool:
    """Check whether components already carry LLM classification results.

    Returns True only if every component that would be a classification
    candidate (per all_components / essential-only rules) already has
    the requested LLM fields (theme and/or vocal_posture) populated.
    """
    for comp in components:
        is_candidate = all_components or _is_essential(comp)
        if not is_candidate:
            continue
        if classify_theme and comp.theme is None:
            return False
        if classify_vocal_posture and comp.vocal_posture is None:
            return False
    return True
```

### 3. Use short-circuit in the queue worker

**File:** `ops/analysis-service/src/sow_analysis/workers/queue.py`
**Location:** At the start of the LLM classification block (line 1014-1018).

Before instantiating `ThemeClassifier` and calling `classify_components()`,
check whether the components already have the needed LLM fields. If so AND
`force` is False, skip the LLM step entirely.

```python
if request.options.classify_theme or request.options.classify_vocal_posture:
    with step_timer("LLM theme/posture classification", logger):
        from .classifier import has_cached_llm_fields

        if (
            not request.options.force
            and has_cached_llm_fields(
                components,
                classify_theme=request.options.classify_theme,
                classify_vocal_posture=request.options.classify_vocal_posture,
                all_components=request.options.all_components,
            )
        ):
            logger.info(
                "LLM classification skipped — cached results found in components.json"
            )
        else:
            try:
                from .classifier import ThemeClassifier

                classifier = ThemeClassifier()
                components = await classifier.classify_components(
                    components,
                    lrc_content=request.lrc_content,
                    all_components=request.options.all_components,
                )
            except Exception as e:
                logger.warning(f"LLM classification failed: {e}")
```

### 4. Force flag behavior

When `force=True`:
- `extract_components()` already bypasses its cache (components.py:1323:
  `if not force:`), so components are freshly extracted with `theme=None`.
- The `has_cached_llm_fields()` check will return False (fields are None).
- LLM classification runs as expected.
- After LLM, the re-persist step (Change 1) saves the updated components.json.

When `force=False`:
- `extract_components()` returns cached components (which now include LLM
  fields, thanks to Change 1's re-persist).
- `has_cached_llm_fields()` returns True.
- LLM step is skipped. Log: `LLM classification skipped — cached results found
  in components.json`.

## Edge cases handled

1. **First run (no cache):** Components freshly extracted with `theme=None`.
   `has_cached_llm_fields()` returns False → LLM runs → re-persist saves
   results.

2. **Second run (cache hit):** Components from cache with LLM fields populated
   (thanks to re-persist). `has_cached_llm_fields()` returns True → LLM
   skipped.

3. **`all_components=True` on rerun when first run was `all_components=False`:**
   Non-essential components have `theme=None` → `has_cached_llm_fields()`
   returns False → LLM runs for all. (No partial-reuse optimization; future
   enhancement.)

4. **`classify_theme=True` on rerun when first run had `classify_theme=False`:**
   Theme fields are None → `has_cached_llm_fields()` returns False → LLM
   runs.

5. **LLM partially failed on first run:** Some components have `theme=None`
   → `has_cached_llm_fields()` returns False → LLM re-runs for all
   (within-run dedup still applies via lyric-hash grouping).

## Verification

### Unit tests

**File:** `ops/analysis-service/tests/test_classifier.py`

Add tests for `has_cached_llm_fields()`:

```python
def test_has_cached_llm_fields_all_populated():
    """Returns True when all essential components have theme + posture set."""
    components = [
        ComponentInstance(component_type="chorus", occurrence_index=1,
                          role="entry", start_time=0, end_time=10,
                          theme="讚美", vocal_posture="To God",
                          theme_confidence=0.9, vocal_posture_confidence=0.9),
    ]
    assert has_cached_llm_fields(components, classify_theme=True,
                                 classify_vocal_posture=True)

def test_has_cached_llm_fields_missing_theme():
    """Returns False when a candidate component has theme=None."""
    components = [
        ComponentInstance(component_type="chorus", occurrence_index=1,
                          role="entry", start_time=0, end_time=10,
                          theme=None, vocal_posture="To God"),
    ]
    assert not has_cached_llm_fields(components, classify_theme=True,
                                      classify_vocal_posture=True)

def test_has_cached_llm_fields_skips_non_essential():
    """Non-essential components are ignored (essential-only mode)."""
    components = [
        ComponentInstance(component_type="chorus", occurrence_index=1,
                          role="entry", start_time=0, end_time=10,
                          theme="讚美", vocal_posture="To God"),
        ComponentInstance(component_type="verse", occurrence_index=1,
                          role="none", start_time=10, end_time=20,
                          theme=None, vocal_posture=None),
    ]
    assert has_cached_llm_fields(components, classify_theme=True,
                                 classify_vocal_posture=True,
                                 all_components=False)

def test_has_cached_llm_fields_all_components_mode():
    """all_components=True requires ALL components to have fields."""
    components = [
        ComponentInstance(component_type="chorus", occurrence_index=1,
                          role="entry", start_time=0, end_time=10,
                          theme="讚美", vocal_posture="To God"),
        ComponentInstance(component_type="verse", occurrence_index=1,
                          role="none", start_time=10, end_time=20,
                          theme=None, vocal_posture=None),
    ]
    assert not has_cached_llm_fields(components, classify_theme=True,
                                      classify_vocal_posture=True,
                                      all_components=True)
```

**File:** `ops/analysis-service/tests/test_components.py` (or a new
`test_queue_component_analysis.py`)

Add integration test verifying the end-to-end cache flow: run component
analysis with `classify_theme=True` twice for the same `content_hash` and
assert the second run skips LLM (mock the ThemeClassifier to assert it is
never called on the second run).

### Manual verification

1. Submit a component analysis job with `classify_theme=True` for a song.
2. Observe LLM classification runs (~30s).
3. Re-submit the same job (same `content_hash`, `force=False`).
4. Observe: `Component cache hit (local)` → `LLM classification skipped —
   cached results found in components.json` → LLM step completes in ~0s.
5. Re-submit with `force=True`.
6. Observe: components re-extracted, LLM classification runs again.

Run tests:
```bash
cd ops/analysis-service && uv run --extra dev pytest tests/test_classifier.py tests/test_components.py -v
```

## Critical files

- `ops/analysis-service/src/sow_analysis/workers/queue.py`
  - Lines 1014-1030: Add short-circuit check + re-persist after LLM
- `ops/analysis-service/src/sow_analysis/workers/classifier.py`
  - After line 159: Add `has_cached_llm_fields()` helper
- `ops/analysis-service/src/sow_analysis/workers/components.py`
  - Lines 1462-1504: `_serialize_components()` already includes LLM fields
    (no change needed; referenced for context)
  - Lines 1507-1541: `_deserialize_components()` already restores LLM fields
    (no change needed; referenced for context)
- `ops/analysis-service/tests/test_classifier.py`
  - Add `has_cached_llm_fields()` unit tests
