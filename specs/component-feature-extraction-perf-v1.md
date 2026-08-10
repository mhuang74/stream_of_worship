# Implementation Plan: Component Feature Extraction Performance Optimization (v1)

> **Date:** 2026-08-10
> **Branch:** TBD
> **Spec ID:** `component-feature-extraction-perf-v1`

---

## Problem

A component analysis job for a single song took **244.57 seconds** (17 components,
`source=lyrics_repetition`) on a CPU-only Docker container. The v3 path (no v5
flags) should complete in 15–40s, but instead took 6× longer.

### Evidence (from production logs)

```
12:12:56.203  Job started
12:12:57.200  Audio download completed in 0.94s
12:12:57.369  Running inline fast_analyze for component beats...
12:12:57.370  Cache hit for fast analysis result
             ... 243 seconds of silence ...
12:17:00.770  Component analysis job completed in 244.57s
              (source=lyrics_repetition, 17 components)
```

### Root Causes

**1. `detect_key_segment_vote` called TWICE per component (34 calls total)**

In `compute_component_features()` (`components.py:821`):

- **Line 905**: `detect_key_segment_vote(y_slice, sr, ...)` — for `component.key`
- **Line 994**: `detect_key_segment_vote(y_slice, sr, ...)` — for
  `component.key_confidence` (sigmoid-mapped from `key_score_margin`)

Each call internally runs:
- `librosa.effects.hpss(y)` — harmonic-percussive source separation (STFT + median filtering)
- `librosa.feature.chroma_cqt(y_harmonic, ...)` — constant-Q chromagram (most expensive)
- `librosa.feature.rms(...)`

For 17 components × 2 calls = **34 calls** to hpss + chroma_cqt. On CPU, each
call takes ~5–8s depending on slice length. 34 × ~7s ≈ **~240s**, matching the
observed 244.57s.

**2. Redundant full audio loads (2× `librosa.load`)**

In `extract_components()` (`components.py:1029`):

- **Line 1112**: `librosa.load(str(audio_path), sr=None, mono=True)` — loads the
  **entire audio** just to get `song_total_duration`, then immediately discards
  it (`del y_tmp`)
- **Line 1140**: `librosa.load(str(audio_path), sr=None, mono=True)` — loads the
  **entire audio again** for feature computation

For a 4-minute song at 44.1kHz, each load allocates ~10.5M samples. The first
load is pure waste.

**3. Per-component `onset_strength` computed redundantly**

In `compute_component_features()`:
- **Line 883**: `librosa.onset.onset_strength(y=y_slice, ...)` — for BPM
- **Line 923**: `librosa.onset.onset_strength(y=y_slice, ...)` — for groove_density

Same slice, same parameters, computed twice. Could be computed once and reused.

**4. Per-component `librosa.feature.rms` computed redundantly**

- **Line 966**: `librosa.feature.rms(y=y_slice, ...)` — for energy_level (full mix)
- **Line 975**: `librosa.feature.rms(y=y_slice, ...)` — for energy_level (fallback path)
- **Line 935**: `librosa.feature.rms(y=source_y, ...)` — for backbeat_strength

These could share a single RMS computation per component.

---

## Goal

Reduce the average v3 component analysis job from **~244s** to **< 30s** for a
typical 17-component song, by:

1. Computing expensive global features (hpss, chroma_cqt, onset_strength, RMS)
   **once** on the full audio, then slicing for each component.
2. Eliminating the duplicate `detect_key_segment_vote` call per component.
3. Eliminating the redundant `librosa.load` for duration.
4. Caching per-component `onset_strength` and `rms` within the function.

---

## Phase 1: Pre-compute global features once, slice per component

**Complexity:** L

**File:** `ops/analysis-service/src/sow_analysis/workers/components.py`

### 1.1 New helper: `_precompute_global_features()`

Add a new function that computes expensive librosa features **once** on the full
audio, returning a dataclass/dict of pre-computed arrays:

```python
@dataclass
class GlobalFeatures:
    """Pre-computed global audio features for per-component slicing."""
    y: np.ndarray
    sr: int
    duration: float
    # Full-track onset strength envelope (for BPM + groove_density).
    onset_env: np.ndarray
    onset_frames: np.ndarray  # frame indices
    onset_times: np.ndarray   # time-aligned to onset_env
    # Full-track RMS (for energy_level + backbeat_strength).
    rms: np.ndarray
    rms_times: np.ndarray
    # Full-track harmonic component (for key detection).
    y_harmonic: np.ndarray
    # Full-track chroma (for key detection).
    chroma: np.ndarray
    # Drums stem (if stems available).
    drums_y: Optional[np.ndarray]
    drums_onset_env: Optional[np.ndarray]
    drums_rms: Optional[np.ndarray]
    drums_rms_times: Optional[np.ndarray]
    # Vocals stem (if stems available).
    vocals_y: Optional[np.ndarray]
```

```python
def _precompute_global_features(
    audio_path: Path,
    sr: int,
    hop_length: int = 512,
    stems_dir: Optional[Path] = None,
) -> GlobalFeatures:
    """Load audio once and compute all expensive global features.

    This replaces the per-component librosa.load + hpss + chroma_cqt + onset
    + rms calls with a single pass over the full audio.
    """
    y, sr = librosa.load(str(audio_path), sr=sr, mono=True)
    duration = float(librosa.get_duration(y=y, sr=sr))

    # Onset strength (full track).
    onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop_length)
    onset_times = librosa.frames_to_time(
        np.arange(len(onset_env)), sr=sr, hop_length=hop_length
    )

    # RMS (full track).
    rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=hop_length)[0]
    rms_times = librosa.frames_to_time(
        np.arange(len(rms)), sr=sr, hop_length=hop_length
    )

    # Harmonic-percussive separation (full track, once).
    y_harmonic, _ = librosa.effects.hpss(y)

    # Chroma (full track, once).
    chroma = librosa.feature.chroma_cqt(
        y=y_harmonic, sr=sr, hop_length=hop_length
    )

    # Stems (optional).
    drums_y = None
    drums_onset_env = None
    drums_rms = None
    drums_rms_times = None
    vocals_y = None
    if stems_dir is not None:
        drums_path = stems_dir / "drums.wav"
        if drums_path.exists():
            try:
                drums_y, _ = librosa.load(str(drums_path), sr=sr, mono=True)
                drums_onset_env = librosa.onset.onset_strength(
                    y=drums_y, sr=sr, hop_length=hop_length
                )
                drums_rms = librosa.feature.rms(
                    y=drums_y, frame_length=2048, hop_length=hop_length
                )[0]
                drums_rms_times = librosa.frames_to_time(
                    np.arange(len(drums_rms)), sr=sr, hop_length=hop_length
                )
            except Exception as e:
                logger.debug(f"Could not load drums stem: {e}")
        vocals_path = stems_dir / "vocals.wav"
        if vocals_path.exists():
            try:
                vocals_y, _ = librosa.load(str(vocals_path), sr=sr, mono=True)
            except Exception as e:
                logger.debug(f"Could not load vocals stem: {e}")

    return GlobalFeatures(
        y=y, sr=sr, duration=duration,
        onset_env=onset_env, onset_frames=np.arange(len(onset_env)),
        onset_times=onset_times,
        rms=rms, rms_times=rms_times,
        y_harmonic=y_harmonic, chroma=chroma,
        drums_y=drums_y, drums_onset_env=drums_onset_env,
        drums_rms=drums_rms, drums_rms_times=drums_rms_times,
        vocals_y=vocals_y,
    )
```

### 1.2 New helper: `_detect_key_from_precomputed_chroma()`

Add a function that performs key detection from pre-computed chroma, avoiding
the per-component hpss + chroma_cqt:

```python
def _detect_key_from_precomputed_chroma(
    chroma: np.ndarray,
    rms: np.ndarray,
    sr: int,
    hop_length: int,
    start_time: float,
    end_time: float,
    rms_times: np.ndarray,
) -> tuple[Optional[str], Optional[float]]:
    """Detect key from pre-computed full-track chroma, sliced to [start, end].

    Returns (key, score_margin) — the two fields needed by
    compute_component_features, avoiding a second detect_key_segment_vote call.
    """
    duration = end_time - start_time
    if duration < 8.0:
        return None, None

    start_frame = librosa.time_to_frames(start_time, sr=sr, hop_length=hop_length)
    end_frame = librosa.time_to_frames(end_time, sr=sr, hop_length=hop_length)
    if end_frame <= start_frame:
        return None, None

    window_chroma = chroma[:, start_frame:end_frame]
    window_rms = rms[start_frame:end_frame]

    if window_rms.size and float(np.mean(window_rms)) < float(np.percentile(rms, 10)):
        return None, None

    chroma_avg = np.mean(window_chroma, axis=1)
    if float(np.max(chroma_avg) - np.min(chroma_avg)) < 0.1:
        return None, None

    from .analyzer import _score_chroma
    scores = sorted(_score_chroma(chroma_avg), key=lambda x: x[2], reverse=True)
    if len(scores) > 1 and scores[0][2] - scores[1][2] < 0.03:
        return None, None

    mode, key, score = scores[0]
    margin = float(scores[0][2] - scores[1][2]) if len(scores) > 1 else None
    return key, margin
```

### 1.3 Refactor `compute_component_features()` to accept `GlobalFeatures`

Change the signature to accept a `GlobalFeatures` object instead of raw `y, sr`:

```python
def compute_component_features(
    gf: GlobalFeatures,
    component: ComponentInstance,
    beats: Optional[list[float]] = None,
    downbeats: Optional[list[float]] = None,
    hop_length: int = 512,
) -> ComponentInstance:
```

Inside, replace all per-component librosa calls with slices from `gf`:

- **BPM**: Slice `gf.onset_env` to component frame range, pass to
  `librosa.beat.tempo()`.
- **Key**: Call `_detect_key_from_precomputed_chroma()` once, get both `key` and
  `score_margin` → compute `key_confidence` from margin. **Eliminates the
  duplicate `detect_key_segment_vote` call.**
- **groove_density**: Reuse the same onset_env slice from BPM (or slice
  `gf.drums_onset_env` if stems available).
- **backbeat_strength**: Slice `gf.rms` (or `gf.drums_rms`) to component range.
- **energy_level**: Reuse the same RMS slice from backbeat (or slice
  `gf.rms` / vocals RMS).

### 1.4 Refactor `extract_components()` to use `_precompute_global_features()`

Replace the two `librosa.load` calls and the per-component loop:

```python
# BEFORE (lines 1108-1160):
# 1. librosa.load for duration (waste)
# 2. identify_from_lyrics_repetition
# 3. librosa.load again for features
# 4. per-component compute_component_features(y, sr, ...)

# AFTER:
# 1. _precompute_global_features() — single load + all expensive features
# 2. identify_from_lyrics_repetition (using gf.duration)
# 3. per-component compute_component_features(gf, ...)
```

Specifically:

- **Remove** lines 1107–1116 (the `librosa.load` just for `song_total_duration`).
  Use `gf.duration` instead.
- **Remove** lines 1139–1143 (the second `librosa.load` for feature computation).
  Use `gf.y` / `gf.sr` instead.
- **Remove** lines 1145–1150 (stems_dir loading inside the loop). Pass `gf`
  which already has stems loaded.
- **Replace** the `for component in components: compute_component_features(y, sr, ...)`
  loop with `for component in components: compute_component_features(gf, ...)`.

---

## Phase 2: Eliminate duplicate key detection call

**Complexity:** S

**File:** `ops/analysis-service/src/sow_analysis/workers/components.py`

### 2.1 Merge key + key_confidence into a single call

Currently in `compute_component_features()`:

```python
# Line 905 — first call (for component.key):
key_result = detect_key_segment_vote(y_slice, sr, segments=[...])
component.key = key_result.key

# Line 994 — second call (for component.key_confidence):
key_result = detect_key_segment_vote(y_slice, sr, segments=[...])
margin = getattr(key_result, "key_score_margin", None)
component.key_confidence = float(1.0 / (1.0 + np.exp(-2.0 * margin)))
```

After Phase 1, both are replaced by a single
`_detect_key_from_precomputed_chroma()` call that returns `(key, margin)`:

```python
key, margin = _detect_key_from_precomputed_chroma(
    gf.chroma, gf.rms, gf.sr, hop_length,
    component.start_time, component.end_time, gf.rms_times,
)
component.key = key
if margin is not None:
    component.key_confidence = float(1.0 / (1.0 + np.exp(-2.0 * margin)))
else:
    component.key_confidence = 0.7
```

This eliminates **17 duplicate calls** to hpss + chroma_cqt.

---

## Phase 3: Cache per-component onset_strength and RMS

**Complexity:** S

**File:** `ops/analysis-service/src/sow_analysis/workers/components.py`

### 3.1 Reuse onset_env within `compute_component_features()`

Currently, `onset_strength` is computed twice per component:

- Line 883: for BPM estimation
- Line 923: for groove_density

After Phase 1, both use the pre-computed `gf.onset_env` sliced to the component
range. The slice is a cheap numpy array view — no recomputation needed. But
within the refactored function, ensure the sliced onset_env is computed once
and reused for both BPM and groove_density.

### 3.2 Reuse RMS within `compute_component_features()`

Currently, `librosa.feature.rms` is computed up to 3 times per component:

- Line 935: for backbeat_strength
- Line 966: for energy_level (full mix)
- Line 975: for energy_level (fallback)

After Phase 1, all use the pre-computed `gf.rms` sliced to the component range.
Ensure the sliced RMS is computed once and reused for backbeat_strength and
energy_level.

---

## Phase 4: Optimize `identify_from_lyrics_repetition` fuzzy matching

**Complexity:** M

**File:** `ops/analysis-service/src/sow_analysis/workers/components.py`

### 4.1 Reduce O(n²) fuzzy matching complexity

The fuzzy matching loop (lines 641–659) compares every pair of signatures:

```python
for i in range(len(sig_list)):
    for j in range(i + 1, len(sig_list)):
        if len(sig_list[i]) != len(sig_list[j]):
            continue
        ...
        if rf_fuzz.ratio(joined_i, joined_j) > 85:
            ...
```

For a song with many LRC lines (e.g., 60 lines, max_window=12), this generates
a large number of signatures. The nested loop is O(n²) where n = number of
unique signatures.

**Optimization:** Group signatures by window size first (already done via the
`len(sig_list[i]) != len(sig_list[j])` check), then use a blocking/bucketing
strategy to reduce comparisons:

```python
# Group signatures by window size.
by_window_size: dict[int, list[int]] = {}
for idx, sig in enumerate(sig_list):
    by_window_size.setdefault(len(sig), []).append(idx)

# Only compare within same window size.
for indices in by_window_size.values():
    if len(indices) < 2:
        continue
    for i_pos, i in enumerate(indices):
        for j in indices[i_pos + 1:]:
            ...
```

This doesn't change the worst case but reduces constant factors by skipping
the `len()` check in the inner loop and improving cache locality.

### 4.2 Add early-exit for exact-duplicate signatures

Before running fuzzy matching, skip signatures that are exact duplicates (already
grouped by `candidates.setdefault(sig, []).append(i)`). The `merged` dict
initialization `{i: {i} for i in range(len(sig_list))}` creates one group per
unique signature — exact duplicates are already in the same `candidates` entry.
The fuzzy loop should skip pairs where one signature is a substring of the other
(fast check) before calling `rf_fuzz.ratio()`.

---

## Phase 5: Add timing instrumentation

**Complexity:** S

**File:** `ops/analysis-service/src/sow_analysis/workers/components.py`

### 5.1 Add per-stage timing logs

Add `time.time()` instrumentation to `extract_components()` and
`compute_component_features()` to make bottlenecks visible in production logs:

```python
# In extract_components():
precompute_start = time.time()
gf = await loop.run_in_executor(None, _precompute_global_features, audio_path, sr, 512, stems_dir)
logger.info(f"Global feature precomputation completed in {time.time() - precompute_start:.2f}s")

identify_start = time.time()
components = identify_from_lyrics_repetition(...)
logger.info(f"Component identification completed in {time.time() - identify_start:.2f}s ({len(components)} components)")

features_start = time.time()
for component in components:
    compute_component_features(gf, component, ...)
logger.info(f"Per-component feature computation completed in {time.time() - features_start:.2f}s ({len(components)} components)")
```

This ensures future regressions are immediately visible.

---

## Phase 6: Update tests

**Complexity:** M

**File:** `ops/analysis-service/tests/test_components.py`

### 6.1 Update `compute_component_features` tests

Existing tests call `compute_component_features(y, sr, component, ...)`. After
Phase 1, the signature changes to `compute_component_features(gf, component, ...)`.
Update all test callsites to construct a `GlobalFeatures` object first.

### 6.2 Add test for `_precompute_global_features()`

Add a test that verifies:
- All fields of `GlobalFeatures` are populated
- `duration` matches `librosa.get_duration()`
- `chroma` shape is `(12, n_frames)`
- `onset_env` and `rms` are 1-D arrays
- Stems fields are `None` when `stems_dir` is `None`

### 6.3 Add test for `_detect_key_from_precomputed_chroma()`

Add a test that verifies:
- Returns `(key, margin)` tuple
- Key is a valid note name (e.g., "G", "C#")
- Margin is a float or None

### 6.4 Add performance regression test

Add a test (marked `@pytest.mark.slow` or guarded by an env var) that:
- Generates a synthetic 4-minute audio signal (sine wave at known frequency)
- Runs `extract_components()` with a mock LRC
- Asserts total time < 10s (generous threshold for CI)

---

## Expected Performance Impact

| Metric | Before | After | Improvement |
|---|---|---|---|
| `librosa.load` calls | 2 | 1 | 2× |
| `hpss` calls | 34 | 1 | 34× |
| `chroma_cqt` calls | 34 | 1 | 34× |
| `onset_strength` calls | 34 | 1 | 34× |
| `rms` calls | ~51 | 1 (+ 1 drums) | ~25× |
| `detect_key_segment_vote` calls | 34 | 0 | eliminated |
| **Total job time (17 components)** | **~244s** | **~15–25s** | **~10–16×** |

The dominant cost (hpss + chroma_cqt × 34) is reduced to a single full-track
computation + 34 cheap numpy array slices.

---

## Files Changed

| File | Change |
|---|---|
| `ops/analysis-service/src/sow_analysis/workers/components.py` | Add `GlobalFeatures` dataclass, `_precompute_global_features()`, `_detect_key_from_precomputed_chroma()`. Refactor `compute_component_features()` and `extract_components()`. Optimize fuzzy matching. Add timing logs. |
| `ops/analysis-service/tests/test_components.py` | Update tests for new `compute_component_features` signature. Add tests for new helpers. Add perf regression test. |

**No changes to:**
- `ops/analysis-service/src/sow_analysis/workers/analyzer.py` (key detection functions unchanged — just called less)
- `ops/analysis-service/src/sow_analysis/workers/queue.py` (job processing flow unchanged)
- `ops/admin-cli/` (CLI unchanged — optimization is server-side only)
- Database schema (no new columns)

---

## Migration Notes

- No schema changes, no cache version bump needed.
- The optimization is transparent to callers — `extract_components()` returns
  the same `list[ComponentInstance]` with the same fields populated.
- Cached `components.json` results remain valid (the optimization only affects
  computation speed, not output schema).
- No breaking changes to API endpoints or CLI commands.

---

## Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Pre-computed full-track chroma may give slightly different key results than per-slice chroma (different windowing) | Acceptable — the full-track chroma sliced to component range is functionally equivalent. Add a comparison test. |
| `_detect_key_from_precomputed_chroma` bypasses the `detect_key_segment_vote` fallback to `detect_key_fulltrack` | Add a fallback: if no windows pass the thresholds, fall back to `detect_key_fulltrack(gf.y_harmonic, gf.sr)` once (not per component). |
| Memory: holding `y`, `y_harmonic`, `chroma`, `onset_env`, `rms` simultaneously | For a 4-minute song at 44.1kHz: `y` ≈ 10.5M samples (~84MB float64), `y_harmonic` ≈ 84MB, `chroma` ≈ (12, ~30k) ≈ 2.9MB. Total ~170MB — acceptable for the analysis service container. |
| Stems loading adds memory if `--use-stems` | Already the case in current code. No change. |

---

## Usage Examples (verification)

After implementation, verify the fix:

```bash
# Run a component analysis job and check timing in logs
uv run --project ops/admin-cli --extra admin sow-admin audio components song_0001

# Expected log output:
# "Global feature precomputation completed in X.XXs"
# "Component identification completed in X.XXs (17 components)"
# "Per-component feature computation completed in X.XXs (17 components)"
# "Component analysis job completed in <30.00s (source=lyrics_repetition, 17 components)"
```

```bash
# Run tests
cd ops/analysis-service && uv run --extra dev pytest tests/test_components.py -v
```
