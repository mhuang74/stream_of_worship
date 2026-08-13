# Implementation Plan v1: Component Identification Tuning Loop

> Goal: improve the accuracy of `identify_from_lyrics_repetition` (chorus/verse identification in the analysis-service COMPONENT ANALYSIS job) by establishing a manually verified ground-truth fixture set for 3 songs, scoring the algorithm's current output via IoU, dumping per-component lyrics slices for review, then iterating on the multi-cue scoring weights to maximize the score.
>
> Scope is strictly the **lyrics-repetition path** in `ops/analysis-service/src/sow_analysis/workers/components.py`. The allin1-sections path is out of scope (it consumes already-labeled sections).

## Locked decisions (user-confirmed)

1. **LRC source for fixtures** — LRC text is pasted into committed fixture files under `eval/components_tuning/<song_id>/lrc.lrc`. The user fetches the LRC from R2 once (`{hash12}/lyrics.lrc`) and pastes it; tests then read the committed file. No runtime R2 dependency. The fetch step itself lives outside this plan (operator runs `sow-admin audio ...` or `aws s3 cp` to obtain the LRC).
2. **Ground-truth format** — Each song's `ground_truth.json` uses 1-based LRC line indices (indexing into the parsed non-empty lines that `identify_from_lyrics_repetition` itself operates on — see "Line indexing convention" below). Roles (`entry` / `exit` / `loop_target`) are specified per expected occurrence.
3. **Verification scope** — All chorus occurrences (not just occurrence 1), entry/exit role assignment, and a "no false-positive verses-as-chorus" check (penalize the algorithm if it returns a chorus whose time range overlaps a deliberately-listed non-chorus section by more than a threshold).
4. **Scoring metric** — Time-range IoU between expected and predicted. Total per song is the mean IoU across all expected components; grand total across 3 songs is the mean of per-song means. Continuous [0.0, 1.0].
5. **Lyrics-slice dump** — The test harness writes one `.txt` per predicted component into `eval/components_tuning/review/<song_id>/` on every run. No new CLI command. The directory is git-ignored (it is review output, not committed source).
6. **Tuning surface** — Only the four multi-cue weight knobs. These are currently inlined as literals in `identify_from_lyrics_repetition`; this plan promotes them to a `LyricsRepetitionWeights` dataclass with module-level `DEFAULT_WEIGHTS` instance, and introduces a `weights=` kwarg on `identify_from_lyrics_repetition` (default `DEFAULT_WEIGHTS`, so existing call sites and tests are unaffected). Out of scope: `_CHORUS_KEYWORDS`, window-size range (2..min(12, N//2)), rapidfuzz ratio > 85 threshold.
7. **Plan depth** — Full step-by-step with concrete file paths, function signatures, fixture/JSON schemas, scoring formulas, and a defined iteration protocol.

## Line indexing convention

`identify_from_lyrics_repetition` parses the LRC and immediately filters to non-empty lines (`components.py:857`: `lines = [ln for ln in lrc_file.lines if ln.text and ln.text.strip()]`). The ground-truth `line_start` / `line_end` indices are **1-BASED** positions into this filtered list — the first non-empty LRC line has index 1. The scorer converts 1-based indices to 0-based internally before deriving time ranges.

Indices are half-open: `line_start` is inclusive, `line_end` is exclusive. E.g. `[5, 9)` means lines 5,6,7,8 (1-based) = 0-based indices 4,5,6,7.

The scorer re-parses the LRC the same way and uses these indices to derive expected time ranges:

```
# Convert 1-based -> 0-based for internal use.
zero_start = line_start - 1
zero_end   = line_end - 1   # still exclusive

expected_start_time = filtered_lines[zero_start].time_seconds
# end_time mirrors identify_from_lyrics_repetition (lines 971-982):
if zero_end < len(filtered_lines):
    expected_end_time = filtered_lines[zero_end].time_seconds
else:
    # estimate via average line duration in the block
    ...
```

For consistency with how the algorithm itself computes `end_time`, the scorer helper `_expected_time_range(parsed_lines, line_start, line_end)` converts 1-based -> 0-based internally, then mirrors `identify_from_lyrics_repetition`'s end-time derivation (lines 971–982). Expected and predicted ranges are thus comparable on identical semantics.

## Critical files

### New files
- `ops/analysis-service/src/sow_analysis/workers/components.py` — ext-ing only: add `LyricsRepetitionWeights` dataclass + `DEFAULT_WEIGHTS` + `weights=` kwarg on `identify_from_lyrics_repetition`; replace the inlined literals.
- `eval/components_tuning/<song_id>/lrc.lrc` (×3) — committed LRC fixture (user pastes content).
- `eval/components_tuning/<song_id>/ground_truth.json` (×3) — committed stub (user fills `line_start`/`line_end`/`role`).
- `ops/analysis-service/tests/test_components_tuning.py` — the scorer + lyrics-slice dumper + grid-search tuning driver.
- `eval/components_tuning/.gitignore` — ignore `review/` output.

### Unchanged files (do NOT modify)
- `ops/analysis-service/src/sow_analysis/workers/queue.py` — job handler continues to call `identify_from_lyrics_repetition` without `weights=` (uses the default).
- `ops/analysis-service/src/sow_analysis/workers/classifier.py` — no changes (LLM step is downstream of identification).
- `ops/analysis-service/src/sow_analysis/storage/cache.py` — `COMPONENT_SCHEMA_VERSION` unchanged.
- `ops/analysis-service/tests/test_components.py` — existing tests untouched; they continue to call `identify_from_lyrics_repetition(lrc, ...)` without `weights=`, exercising the default.

## Implementation changes

### Change 0 — Promote scoring weights to a `LyricsRepetitionWeights` dataclass

**File:** `ops/analysis-service/src/sow_analysis/workers/components.py`
**Location:** Add the dataclass near the top of the module (after the `ESSENTIAL_ROLES` constant at line 169, before `_is_essential`).

```python
@dataclass(frozen=True)
class LyricsRepetitionWeights:
    """Multi-cue scoring weights for identify_from_lyrics_repetition.

    Each field corresponds to a knob in the multi-cue scoring formula
    (components.py:924-937). See individual field docs for semantics.
    """
    # Multiplier on (min(repeat_count, cap) * window_size). Together with
    # repeat_count_cap, tunes how aggressively repetition is rewarded.
    repetition_multiplier: float = 1.0
    # Caps repeat_count before multiplication (tunes diminishing returns).
    repeat_count_cap: int = 4
    # position_weight assigned when the first occurrence is past the
    # early-intro threshold (i.e., likely a real chorus, not an intro tag).
    position_weight_late: float = 1.0
    # position_weight assigned when the first occurrence is within the
    # early-intro threshold (penalty for verses-as-chorus false positives).
    position_weight_early: float = 0.4
    # Threshold (fraction of song_total_duration) below which position is
    # considered "early". 0.1 means "first 10% of the song".
    position_early_fraction: float = 0.1
    # Fallback for position_weight_early when song_total_duration is None:
    # occurrences starting before this many seconds are "early".
    position_early_seconds: float = 10.0
    # length_weight when the window size falls in the "preferred" range.
    length_weight_preferred: float = 1.0
    # length_weight when the window size falls outside the preferred range.
    length_weight_other: float = 0.6
    # Preferred window-size range (inclusive both ends).
    length_preferred_min: int = 4
    length_preferred_max: int = 8
    # content_weight boost when any _CHORUS_KEYWORDS appears in the joined text.
    content_weight_keyword_present: float = 1.4
    # content_weight baseline when no keyword appears.
    content_weight_keyword_absent: float = 1.0


DEFAULT_WEIGHTS = LyricsRepetitionWeights()
```

### Change 1 — Add `weights=` kwarg to `identify_from_lyrics_repetition`

**File:** `ops/analysis-service/src/sow_analysis/workers/components.py`
**Location:** Replace the signature and the inlined scoring block (currently lines 813–819 signature, 924–937 scoring).

New signature (add `weights` as the LAST kwarg, default to `DEFAULT_WEIGHTS`):

```python
def identify_from_lyrics_repetition(
    lrc_content: str,
    beats: Optional[list[float]] = None,
    downbeats: Optional[list[float]] = None,
    song_total_duration: Optional[float] = None,
    snap_to_downbeat: bool = False,
    weights: LyricsRepetitionWeights = DEFAULT_WEIGHTS,
) -> list[ComponentInstance]:
```

Replace the inlined multi-cue scoring block (currently lines 924–937) with:

```python
        # v3 multi-cue scoring, parametrized via `weights`.
        repetition_score = (
            weights.repetition_multiplier
            * min(repeat_count, weights.repeat_count_cap)
            * w
        )
        if song_total_duration and song_total_duration > 0:
            early_threshold = weights.position_early_fraction * song_total_duration
            position_weight = (
                weights.position_weight_late
                if occurrence_times[0] > early_threshold
                else weights.position_weight_early
            )
        else:
            position_weight = (
                weights.position_weight_late
                if occurrence_times[0] > weights.position_early_seconds
                else weights.position_weight_early
            )
        length_weight = (
            weights.length_weight_preferred
            if weights.length_preferred_min <= w <= weights.length_preferred_max
            else weights.length_weight_other
        )
        content_weight = (
            weights.content_weight_keyword_present
            if any(kw in joined_text.lower() for kw in _CHORUS_KEYWORDS)
            else weights.content_weight_keyword_absent
        )
        final_score = (
            repetition_score * position_weight * length_weight * content_weight
        )
```

Behavior at default weights is bit-identical to the existing literals (`1.0`, `4`, `1.0`/`0.4`, `0.1`, `10.0`, `1.0`/`0.6`, `4`/`8`, `1.4`/`1.0`). Existing tests in `test_components.py` continue to pass unchanged.

### Change 2 — Fixture schema: LRC + ground truth JSON

**Layout:**
```
eval/components_tuning/
├── .gitignore                         # ignores "review/"
├── jun_wang_jiu_zai_zhe_li_1c32724c/
│   ├── lrc.lrc                        # COMMITTED; user pastes content
│   └── ground_truth.json              # COMMITTED stub; user fills indices
├── yi_sheng_jing_bai_mi_da2173d0/
│   ├── lrc.lrc
│   └── ground_truth.json
└── zhu_a__wo_yao_gen_sui_mi_83163301/
    ├── lrc.lrc
    └── ground_truth.json
```

**`lrc.lrc`** — Plain LRC text. The user obtains the canonical LRC from R2 (`{hash12}/lyrics.lrc`, where hash12 is the first 12 chars of the song's recording content_hash), then pastes it verbatim into the fixture file. The fetch command is out of scope (operator uses `aws s3 cp s3://<bucket>/<hash12>/lyrics.lrc -` or any existing admin CLI dump command).

**`ground_truth.json`** — Schema:

```json
{
  "song_id": "jun_wang_jiu_zai_zhe_li_1c32724c",
  "hash12": "8a4663283a5c",
  "lrc_file": "lrc.lrc",
  "note": "Line indices are 1-BASED: the first non-empty LRC line is index 1. line_end is EXCLUSIVE (half-open [line_start, line_end)).",
  "expected_chorus_occurrences": [
    {
      "occurrence_index": 1,
      "line_start": 5,
      "line_end": 10,
      "role": "entry"
    },
    {
      "occurrence_index": 2,
      "line_start": 13,
      "line_end": 18,
      "role": "exit"
    }
  ],
  "expected_verse": {
    "occurrence_index": 1,
    "line_start": 1,
    "line_end": 5,
    "role": "loop_target"
  },
  "false_positive_avoid": [
    {
      "label": "verse_2_solo",
      "line_start": 19,
      "line_end": 24
    }
  ]
}
```

Schema semantics:
- `line_start` / `line_end` are 1-BASED indices into the **parsed, filtered (non-empty) line list** that `identify_from_lyrics_repetition` produces from `parse_lrc(lrc_content)`. The first non-empty LRC line has index 1. `line_end` is **exclusive** (half-open range `[line_start, line_end)`).
- `expected_chorus_occurrences` — every chorus occurrence the operator has manually verified in the song. The scorer maps each to a predicted chorus component by best-IoU and averages.
- `expected_verse` — the verse section that should be returned as `component_type="verse"`, `role="loop_target"`. Set to `null` if the song has no verse-before-chorus (in which case the scorer skips verse scoring for that song).
- `false_positive_avoid` — sections that should NOT be detected as chorus. For each, the scorer searches predicted chorus components for an IoU above `FP_PENALTY_IOU_THRESHOLD = 0.3` with any expected-false-positive range, and subtracts a penalty from the song's score (see "Scoring formula"). Set to `[]` if no false-positive check is needed.
- For a single-chorus song, list ONE entry in `expected_chorus_occurrences` and set its `role` to `"entry_exit"` (the algorithm emits two rows — `entry` + `exit` — for a single chorus; the scorer matches the single expected range against BOTH rows and verifies both row roles are present).

### Change 3 — Test file `test_components_tuning.py`

**File:** `ops/analysis-service/tests/test_components_tuning.py` (new)
**Imports & helpers:**

```python
"""Tuning harness for identify_from_lyrics_repetition.

Loads committed LRC + ground-truth fixtures under
tests/fixtures/components_tuning/<song_id>/, runs the algorithm, scores
the result via time-range IoU, and writes per-component lyrics slices to
tests/fixtures/components_tuning/review/<song_id>/ for manual review.

Three entry points:
  - pytest test_components_tuning.py         # runs scorer with DEFAULT_WEIGHTS
  - pytest test_components_tuning.py::TestGridSearch   # sweeps the weight grid
  - python -m sow_analysis.tests.test_components_tuning   # CLI: best-weights report
"""
import json
from dataclasses import asdict, replace
from pathlib import Path
from typing import Optional

import pytest

from sow_analysis.workers.components import (
    ComponentInstance,
    DEFAULT_WEIGHTS,
    LyricsRepetitionWeights,
    identify_from_lyrics_repetition,
)
from sow_analysis.workers.lrc_parser import parse_lrc
# Reuse the classifier's lyrics-extraction helper for the review dump:
# matches the same time-range filtering used in prod LLM classification.
from sow_analysis.workers.classifier import _extract_lyrics_for_component

FIXTURES = Path(__file__).resolve().parents[3] / "eval" / "components_tuning"
REVIEW_DIR = FIXTURES / "review"
SONG_IDS = [
    "jun_wang_jiu_zai_zhe_li_1c32724c",
    "yi_sheng_jing_bai_mi_da2173d0",
    "zhu_a__wo_yao_gen_sui_mi_83163301",
]

FP_PENALTY_IOU_THRESHOLD = 0.3     # any predicted chorus with IoU above this
                                    # against a false_positive_avoid range
                                    # triggers the false-positive penalty.
FP_PENALTY = 0.25                   # subtracted from the song's per-song mean
                                    # for EACH false-positive hit (clamped at 0).
ROLE_BONUS = 0.05                   # added to per-component IoU when role
                                    # matches expected (clamped at 1.0).
ROLE_MISMATCH_PENALTY = 0.10        # subtracted when role is wrong.
```

**Helper: `_load_fixture(song_id)` → `(lrc_content, ground_truth)`**

```python
def _load_fixture(song_id: str) -> tuple[str, dict]:
    song_dir = FIXTURES / song_id
    lrc_path = song_dir / "lrc.lrc"
    gt_path = song_dir / "ground_truth.json"
    assert lrc_path.exists(), (
        f"Missing LRC fixture: {lrc_path}. Fetch the LRC from R2 "
        f"({song_id}'s hash12}/lyrics.lrc) and commit it here."
    )
    assert gt_path.exists(), f"Missing ground truth: {gt_path}"
    lrc_content = lrc_path.read_text(encoding="utf-8")
    gt = json.loads(gt_path.read_text(encoding="utf-8"))
    return lrc_content, gt
```

**Helper: `_parse_filtered_lines(lrc_content)` → `list[LRCLine]`** — same non-empty filter the algorithm uses; assert `line_start < line_end <= len(lines)` for every expected range.

```python
def _parse_filtered_lines(lrc_content: str) -> list:
    lrc_file = parse_lrc(lrc_content)
    return [ln for ln in lrc_file.lines if ln.text and ln.text.strip()]
```

**Helper: `_expected_time_range(lines, line_start, line_end)` → `(start_t, end_t)`** — mirrors the algorithm's `end_time` derivation (see "Line indexing convention").

```python
def _expected_time_range(lines, line_start: int, line_end: int) -> tuple[float, float]:
    # Convert 1-based ground-truth indices to 0-based internal indices.
    zero_start = line_start - 1
    zero_end = line_end - 1   # still exclusive
    n = len(lines)
    assert 0 <= zero_start < zero_end <= n, (
        f"Bad 1-based line range [{line_start}, {line_end}) for {n}-line LRC"
    )
    start_t = lines[zero_start].time_seconds
    # Mirror identify_from_lyrics_repetition's end-time derivation:
    if zero_end < n:
        end_t = lines[zero_end].time_seconds
    else:
        # Estimate via average line duration in the block.
        durations = [
            lines[k + 1].time_seconds - lines[k].time_seconds
            for k in range(zero_start, min(zero_end, n - 1))
        ]
        avg = (sum(durations) / len(durations)) if durations else 4.0
        end_t = lines[min(zero_end - 1, n - 1)].time_seconds + avg
    return start_t, end_t
```

**Helper: `_iou(a_start, a_end, b_start, b_end)` → `float`**

```python
def _iou(a_start, a_end, b_start, b_end) -> float:
    inter = max(0.0, min(a_end, b_end) - max(a_start, b_start))
    union = max(a_end, b_end) - min(a_start, b_start)
    return inter / union if union > 0 else 0.0
```

**Helper: `_dump_review_slices(song_id, lrc_content, components)` → writes one `.txt` per predicted component**

```python
def _dump_review_slices(song_id, lrc_content, components) -> Path:
    out_dir = REVIEW_DIR / song_id
    out_dir.mkdir(parents=True, exist_ok=True)
    # Wipe previous slices for this song.
    for old in out_dir.glob("component_*.txt"):
        old.unlink()
    n = len(components)
    for i, c in enumerate(components, 1):
        lyrics_lines = _extract_lyrics_for_component(
            lrc_content, c.start_time, c.end_time
        ) if c.start_time is not None else []
        body = "\n".join(lyrics_lines) or "<no lyrics in range>"
        header = (
            f"# Component {i}/{n}\n"
            f"# song_id: {song_id}\n"
            f"# type: {c.component_type}\n"
            f"# occurrence_index: {c.occurrence_index}\n"
            f"# role: {c.role}\n"
            f"# source: {c.source}\n"
            f"# time: [{c.start_time:.2f}, {c.end_time:.2f}]  "
            f"duration={c.end_time - c.start_time:.2f}s\n"
            f"# lyrics_line_count: {len(lyrics_lines)}\n"
            f"---\n"
        )
        (out_dir / f"component_{i:02d}_{c.component_type}_{c.role}.txt").write_text(
            header + body + "\n", encoding="utf-8"
        )
    return out_dir
```

**Scoring function: `_score_song(lrc_content, ground_truth, weights)` → `dict`**

Returns a dict with shape:
```
{
  "per_component": [ {"expected_role": "entry", "occurrence_index": 1,
                      "best_iou": 0.82, "matched_pred_role": "entry",
                      "role_bonus": 0.05, "role_penalty": 0.0,
                      "component_score": 0.87}, ... ],
  "verse_iou": 0.91,
  "false_positive_hits": [{"label": "verse_2_solo", "max_iou": 0.55}],
  "per_song_mean": 0.78,
  "n_predicted_choruses": 2,
  "n_expected_choruses": 2,
  "n_predicted_verses": 1,
}
```

Algorithm:
1. Parse LRC, run `identify_from_lyrics_repetition(lrc_content, song_total_duration=..., weights=weights)`. Use the LRC file's last-line timestamp as `song_total_duration` (or `None` if the LRC has < 2 lines).
2. Partition predicted components into `choruses` (by `component_type == "chorus"`) and `verses` (by `component_type == "verse"`).
3. For each `expected_chorus_occurrences[i]`:
   - Compute expected time range `(e_start, e_end)` via `_expected_time_range`.
   - Compute IoU against EVERY predicted chorus component's time range; take `best_iou`, recording the matched component's `role` as `matched_pred_role`.
   - If expected `role == "entry_exit"` (single-chorus case), the algorithm emits two rows; require at least one matched pred has `role == "entry"` AND another has `role == "exit"`. If both present → `role_bonus = ROLE_BONUS`; else `role_penalty = ROLE_MISMATCH_PENALTY`.
   - Otherwise: if `matched_pred_role == expected_role` → `role_bonus = ROLE_BONUS`; else `role_penalty = ROLE_MISMATCH_PENALTY`.
   - `component_score = clamp(best_iou + role_bonus - role_penalty, 0.0, 1.0)`.
4. If `expected_verse` is non-null, compute expected verse range and best-IoU against predicted verses; add to `per_component` list with `expected_role = "loop_target"`.
5. **Occurrence-count check**: if `n_predicted_choruses != n_expected_choruses`, the missing/extra occurrences are appended to `per_component` with `component_score = 0.0` and `best_iou = 0.0` so that occurrence-count mismatches directly drag the mean down.
6. **False-positive check**: for each `fp` in `false_positive_avoid`, compute `max_iou` against all predicted choruses. If `>= FP_PENALTY_IOU_THRESHOLD`, record it and subtract `FP_PENALTY` from `per_song_mean` (clamped at 0).
7. `per_song_mean = mean(per_component.component_score) - sum(false_positive_hits) * FP_PENALTY` (clamped at 0).

**Top-level scorer: `score_all(weights=DEFAULT_WEIGHTS)` → `(grand_total, per_song_results)`**

```python
def score_all(weights: LyricsRepetitionWeights = DEFAULT_WEIGHTS):
    per_song = []
    for song_id in SONG_IDS:
        lrc, gt = _load_fixture(song_id)
        result = _score_song(lrc, gt, weights)
        # Always dump review slices (even on failure paths) for manual inspection.
        components = identify_from_lyrics_repetition(
            lrc, song_total_duration=gt.get("song_total_duration"), weights=weights
        )
        _dump_review_slices(song_id, lrc, components)
        per_song.append((song_id, result))
    grand_total = sum(r["per_song_mean"] for _, r in per_song) / len(per_song)
    return grand_total, per_song
```

**Test classes (pytest entry points):**

```python
@pytest.mark.parametrize("song_id", SONG_IDS)
def test_fixture_completeness(song_id):
    """FAIL fast if any fixture is still a stub or missing LRC."""
    lrc, gt = _load_fixture(song_id)
    filtered = _parse_filtered_lines(lrc)
    assert len(filtered) >= 4, f"{song_id}: LRC has too few non-empty lines"
    # Validate every line range is in-bounds.
    for occ in gt["expected_chorus_occurrences"]:
        assert 1 <= occ["line_start"] < occ["line_end"] <= len(filtered) + 1, (
            f"{song_id}: chorus occ {occ['occurrence_index']} "
            f"range [{occ['line_start']},{occ['line_end']}) OOB"
        )
    if gt.get("expected_verse"):
        v = gt["expected_verse"]
        assert 1 <= v["line_start"] < v["line_end"] <= len(filtered) + 1, (
            f"{song_id}: verse range [{v['line_start']},{v['line_end']}) OOB"
        )
    for fp in gt.get("false_positive_avoid", []):
        assert 1 <= fp["line_start"] < fp["line_end"] <= len(filtered) + 1, (
            f"{song_id}: fp range [{fp['line_start']},{fp['line_end']}) OOB"
        )


def test_default_weights_baseline():
    """Baseline: score_all() with DEFAULT_WEIGHTS, print breakdown.
    Not a hard-gate assertion (no minimum score threshold) — it exists to
    surface the baseline number for the tuning protocol below.
    """
    grand_total, per_song = score_all(DEFAULT_WEIGHTS)
    print("\n--- DEFAULT_WEIGHTS baseline ---")
    print(f"GRAND TOTAL = {grand_total:.3f}")
    for song_id, r in per_song:
        print(f"  {song_id}: {r['per_song_mean']:.3f} "
              f"(n_pred_chorus={r['n_predicted_choruses']}, "
              f"n_exp={r['n_expected_choruses']}, "
              f"fp_hits={len(r.get('false_positive_hits', []))})")
    # Sanity: total is finite and in [0, 1].
    assert 0.0 <= grand_total <= 1.0


class TestGridSearch:
    """Grid-search over the 4 multi-cue weight knobs (recommended subset).
    Tunable axes (kept narrow to avoid overfitting on 3 songs):
      - repeat_count_cap in {3, 4, 5}
      - position_weight_early in {0.2, 0.4, 0.6}
      - length_weight_other in {0.4, 0.6, 0.8}
      - content_weight_keyword_present in {1.2, 1.4, 1.6}
    All other fields stay at DEFAULT_WEIGHTS values.
    Total combinations: 3*3*3*3 = 81.
    """
    import itertools

    def _axes(self):
        return self.itertools.product(
            [3, 4, 5],                  # repeat_count_cap
            [0.2, 0.4, 0.6],            # position_weight_early
            [0.4, 0.6, 0.8],            # length_weight_other
            [1.2, 1.4, 1.6],            # content_weight_keyword_present
        )

    def test_grid_search_reports_best_weights(self):
        best_total, best_weights, best_per_song = -1.0, None, None
        results = []
        for cap, pos_early, len_other, content_kp in self._axes():
            w = replace(
                DEFAULT_WEIGHTS,
                repeat_count_cap=cap,
                position_weight_early=pos_early,
                length_weight_other=len_other,
                content_weight_keyword_present=content_kp,
            )
            total, per_song = score_all(w)
            results.append((total, w))
            if total > best_total:
                best_total, best_weights, best_per_song = total, w, per_song
        print("\n--- Grid search winners ---")
        print(f"BEST TOTAL = {best_total:.3f}")
        print(f"BEST WEIGHTS = {asdict(best_weights)}")
        for song_id, r in best_per_song:
            print(f"  {song_id}: {r['per_song_mean']:.3f}")
        # Assert grid produced a strictly better-than-default result OR
        # default already optimal. Either way, the baseline should be in
        # the reported set:
        default_total, _ = score_all(DEFAULT_WEIGHTS)
        assert best_total >= default_total - 1e-9
```

**`__main__` block** (so the file can be run as `python -m sow_analysis.tests.test_components_tuning` for a one-off best-weights report):

```python
if __name__ == "__main__":
    grand_total, per_song = score_all(DEFAULT_WEIGHTS)
    print(f"DEFAULT total = {grand_total:.3f}")
    for song_id, r in per_song:
        print(f"  {song_id}: {r['per_song_mean']:.3f}")
```

### Change 4 — `.gitignore` for review output

**File:** `eval/components_tuning/.gitignore`
**Content:**
```
# Generated on every test run — do not commit.
review/
```

## Scoring formula (summary)

Per song:
```
per_component[i].component_score = clamp(best_iou + role_bonus - role_penalty, 0, 1)
per_song_mean = mean(component_scores)
                  - FP_PENALTY * count(false_positive_hits with max_iou >= 0.3)
per_song_mean = clamp(per_song_mean, 0, 1)         # lower clamp only via FP
grand_total   = mean(per_song_mean across 3 songs)
```

Where:
- `best_iou` = max IoU between the expected component's time range and any predicted component of the matching `component_type`.
- `role_bonus = +0.05` if predicted role matches expected (entry/exit/loop_target); `+0.05` if expected is `entry_exit` and BOTH entry and exit predicted rows are present.
- `role_penalty = -0.10` otherwise.
- Occurrence-count mismatches append zero-scored entries to `per_component`, dragging the mean down.
- `false_positive_hits` = predicted choruses with `IoU >= 0.3` against any `false_positive_avoid` range; each subtracts `0.25` from `per_song_mean` (clamped at 0).

## Tuning-iteration protocol

1. **Fetch LRC** for each of the 3 songs from R2 (`{hash12}/lyrics.lrc`), paste verbatim into the three `lrc.lrc` fixture files.
2. **Run `identify_from_lyrics_repetition` once per song** (via the test harness with DEFAULT_WEIGHTS) and inspect `tests/fixtures/components_tuning/review/<song_id>/component_*.txt` slices. These are the lyrics the algorithm believes each component covers — reviewed side-by-side with the LRC timestamps.
3. **Manually verify** chorus and verse 1 (and ideally every chorus occurrence) for each song. Fill the `ground_truth.json` files:
   - For each chorus occurrence, set `occurrence_index`, `line_start`, `line_end` (half-open, 1-based into filtered lines), and `role` (`entry` / `exit` / `entry_exit` for single-chorus songs).
   - Set `expected_verse` similarly, or `null` if the song starts directly with a chorus.
   - Populate `false_positive_avoid` with any section that looks like a verse repeating but might tempt the algorithm into calling it a chorus.
4. **Baseline run:** `cd ops/analysis-service && uv run --extra dev pytest tests/test_components_tuning.py -v -s`. Record the `GRAND TOTAL = ...` number.
5. **Grid search:** `cd ops/analysis-service && uv run --extra dev pytest tests/test_components_tuning.py::TestGridSearch -v -s`. Record the best-weights report.
6. **Inspect the winning weights.** If `best_total > baseline + 0.05`, update `DEFAULT_WEIGHTS` in `components.py` to the grid-winner values. (A 0.05 absolute improvement on a 3-song set is a meaningful signal — anything smaller is within noise.)
7. **Re-run the existing component tests** to ensure no regression:
   ```
   cd ops/analysis-service && uv run --extra dev pytest tests/test_components.py -v
   ```
8. **Iterate** (optional): widen the grid axes around the winner (e.g. if winner is `repeat_count_cap=5`, re-sweep `{4,5,6}`) and re-run step 5. Stop when the delta between iterations is `< 0.02` or when grid axes can no longer widen without overfit risk (limit: 2 iterations of axis refinement).
9. **Commit** the updated `DEFAULT_WEIGHTS` and the now-populated fixture/ground-truth files. Review-output dir stays git-ignored.
10. **Sanity-check on real songs** (out of scope for the loop, but recommended before merge): re-run a COMPONENT ANALYSIS job on each of the 3 songs via `sow-admin audio components <song> --compute-all-fields --force` and verify the persisted `components.json`'s chorus/verse rows now align with the manually verified ground truth.

## Out of scope

- Tuning `_CHORUS_KEYWORDS`, the window-size range, or the rapidfuzz ratio threshold. These are intentionally left fixed to limit overfit risk on a 3-song sample.
- Persisting the review slices to R2 or the DB. They are local-only artifacts.
- Adding a CLI command for the lyrics-slice dump. The test harness is the sole producer/consumer.
- Bumping `COMPONENT_SCHEMA_VERSION`. No persistence-format change.
- Re-tuning the LLM classifier in `classifier.py`. Identification precedes classification; classifier tuning is a separate effort.
- The allin1-sections identification path (`identify_from_allin1_sections`).

## Rollout order

1. **Change 0 + Change 1** (`components.py`: dataclass + `weights=` kwarg). Existing tests must still pass — this is a pure refactor at `DEFAULT_WEIGHTS` values.
2. **Change 2 + Change 4** (fixture stubs + `.gitignore`). Files exist with stub `lrc.lrc` containing a placeholder comment and `ground_truth.json` containing empty lists / placeholder line indices.
3. **Change 3** (`test_components_tuning.py`). All fixture-completeness assertions will FAIL at this point — that is the intended forcing function for the user to populate the fixtures.
4. **User fills fixtures** (LRC + ground truth). Re-runs `test_fixture_completeness` until green.
5. **User runs baseline + grid search** (steps 4–6 of the tuning-iteration protocol). Updates `DEFAULT_WEIGHTS` if a winner emerges.
6. **Re-run `test_components.py`** to confirm no regression on existing algorithm tests.

## Manual verification checklist

1. **Refactor is bit-identical:** with `DEFAULT_WEIGHTS` and no `weights=` kwarg supplied by callers, `identify_from_lyrics_repetition(LRC_X)` returns byte-identical `ComponentInstance` lists before/after Change 0+1. Verified by running `tests/test_components.py` (should stay green).
2. **Fixture-completeness test fails loud:** before the user populates fixtures, `pytest tests/test_components_tuning.py::test_fixture_completeness` fails with an index-OOB or stub-marker assertion — not a silent pass.
3. **Review slices appear:** after any `score_all(...)` call, `tests/fixtures/components_tuning/review/<song_id>/component_*.txt` exists, with one file per predicted component and accurate lyrics + header block.
4. **Baseline number is reported:** `test_default_weights_baseline` prints `GRAND TOTAL = ...` and exits 0 (no minimum threshold).
5. **Grid search prints winner:** `TestGridSearch::test_grid_search_reports_best_weights` prints `BEST TOTAL`, `BEST WEIGHTS`, and per-song breakdown; the `assert best_total >= default_total - 1e-9` invariant holds.
6. **`DEFAULT_WEIGHTS` update preserves existing tests:** after updating the dataclass to grid-winner values, `tests/test_components.py` stays green.

## Related specs

- `specs/fix-component-analysis-llm-persistence-admin-cli-v3-implementation.md` — admin-cli LLM persistence fix (orthogonal to identification tuning).
- `specs/chorus-component-metadata-impl-plan-v5.md` — v5 component metadata plan (origin of the multi-cue scoring being tuned here).
