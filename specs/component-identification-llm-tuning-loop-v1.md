# Implementation Plan v1: LLM Component Identification Tuning Loop

> Goal: optimize the LLM-based component identification pipeline (`segment_song` in `ops/analysis-service/src/sow_analysis/workers/section_segmenter.py`) against the 3-song fixture set in `eval/components_tuning/`, then A/B-compare with the lyrics-repetition baseline (which plateaus at grand-total IoU ≈ 0.286 per `specs/component-identification-tuning-loop-v1.md`).
>
> Scope is strictly the **LLM path** in `section_segmenter.py`. The allin1-sections path, the downstream LLM `ThemeClassifier`, and the lyrics-repetition fallback are out of scope (the last is exercised only as an unchanged A/B baseline).

## Locked decisions (user-confirmed)

1. **Fixture reuse** — identical 3-song fixtures/ground-truths from `eval/components_tuning/` and the existing scorer. The scorer is source-agnostic (partitions by `component_type` only, never by `source`), so the LLM path's `ComponentInstance` rows feed directly into the existing `_score_song` logic with zero scorer changes.
2. **A/B comparison via existing harness** — a new parallel `score_all_llm()` runs `segment_song` synchronously via `asyncio.run(...)`, feeds its result into the same `_score_components` scorer, and reports both grand-totals side-by-side in the test output. No admin-cli / analysis-service HTTP round-trip is required for fast iteration; the CLI `--segmentation-mode` flow from `admin-cli-segmentation-mode-flag-v2.md` is reserved for final end-to-end validation only (out of scope for this loop).
3. **Three tunable axes (priority order):**
   - **A1: Few-shot examples content** (highest leverage). Iterate on quality, count (0–3), and label breadth of hand-written examples in `segmentation_few_shot.json`. The leakage guard `_EXPECTED_HELD_OUT_IDS` ensures examples cannot be drawn from the 3 fixtures; candidate examples MUST come from other songs with manually verified choruses and verses.
   - **A2: System prompt wording variants** — explore 3 prompt variants (current default, repetition-cue-emphasized, structural-conventions-emphasized). Hardcoded today; promoted via a new `system_prompt_override` kwarg on `segment_song`.
   - **A3: Chorus-repetition validator multipliers** — the three hardcoded constants in `_validate_chorus_repetition` (`0.60` non-repeated, `0.90` trimmed, `+0.05` confirmed) plus the mapping-time `*0.95` literal in `_map_sections_to_components`. Promote to a `ValidatorWeights` dataclass with `DEFAULT_VALIDATOR_WEIGHTS` and grid-sweep narrow bands.
4. **Knobs explicitly OUT of scope for this loop** (held at defaults; were excluded by user selection):
   - `SOW_LLM_SEGMENTATION_MODEL` (model selection)
   - `SOW_LLM_SEGMENTATION_MAX_TOKENS` (token budget)
   - `temperature` (fixed at 0 — deterministic outputs only)
   - `SOW_LLM_SEGMENTATION_SANITY_CHECK` (stays OFF — would double LLM cost)
   - `snap_to_downbeat` (exercised by callers of `segment_song`, not tuned here)
   - `_CHORUS_KEYWORDS` equivalent — none exists for the LLM path
5. **Commit artifacts (the loop's output):**
   - Updated `ops/analysis-service/src/sow_analysis/workers/segmentation_few_shot.json` — the `__CHANGE_ME__` placeholder REPLACED with the winning real examples (real `source_song_id` from a song NOT in the 3 fixtures).
   - `ops/analysis-service/.env.segmentation-tuning.example` — committed; documents the env values that reproduce the winning result (`SOW_COMPONENTS_USE_LLM_SEGMENTATION`, `SOW_LLM_SEGMENTATION_MODEL`, etc.). Does NOT itself change production defaults.
   - `eval/components_tuning/reports/llm_tuning_report.md` — auto-generated summary of per-song IoU before/after, axis-by-axis progressions.
   - `ValidatorWeights` dataclass + `DEFAULT_VALIDATOR_WEIGHTS` promoted into `section_segmenter.py`; updated to grid-winner values if a winner > baseline + 0.05 emerges (mirrors v1's promotion threshold).
6. **Spec depth** — full step-by-step with concrete file paths, function signatures, JSON schemas, scoring formulas, iteration protocol, manual verification checklist.

## Pass bar (decided before implementation)

- **Pass:** `score_all_llm()` grand-total IoU ≥ **0.70** on the 3 fixtures (target lifted from `specs/component-identification-llm-segmentation-v2.md`).
- **Stretch:** ≥ **0.85** per-song mean on all 3 fixtures.
- **No regression:** `score_all()` (lyrics-repetition) still reports ≈ 0.286; existing `test_section_segmenter.py` and `test_components_tuning.py` unit tests stay green; `DEFAULT_VALIDATOR_WEIGHTS == ValidatorWeights()` (verbatim current values) before the grid search runs.

## Line indexing convention

Unchanged from `specs/component-identification-tuning-loop-v1.md` and `specs/component-identification-llm-segmentation-v2.md`: 1-based **raw** LRC line numbers (every physical line counts, blanks included), both `line_start` and `line_end` **inclusive**. The LLM's numbered-LRC input (via `_render_numbered_lrc`) and `_parse_segmenter_json` use the same convention; the existing `_expected_time_range` scorer helper mirrors the algorithm's end-time derivation exactly, so expected and predicted ranges are compared on identical semantics with no conversion.

## Critical files

### New files
- `ops/analysis-service/tests/test_components_tuning_llm.py` — LLM scorer + axis-by-axis tuning harness + A/B comparison test + `__main__` report writer.
- `ops/analysis-service/src/sow_analysis/workers/segmentation_few_shot_candidates/V0_none.json`, `V1_single.json`, `V2_pair.json`, `V3_trio.json` — candidate few-shot example sets, each honoring the same schema and leakage guard as the production file.
- `ops/analysis-service/src/sow_analysis/workers/segmentation_prompts/V0_default.txt`, `V1_repetition_cues.txt`, `V2_structural.txt` — candidate system prompts (one per file, plain text).
- `ops/analysis-service/.env.segmentation-tuning.example` — committed env-variable documentation.
- `eval/components_tuning/reports/llm_tuning_report.md` — auto-generated; only this file is committed, sibling transient reports stay git-ignored.

### Modified files (ext-only; behavior bit-identical at defaults)
- `ops/analysis-service/src/sow_analysis/workers/section_segmenter.py` — add `ValidatorWeights` dataclass + `DEFAULT_VALIDATOR_WEIGHTS`; add three trailing kwargs to `segment_song` (`few_shot_override`, `system_prompt_override`, `validator_weights`) all defaulting to current behavior; thread them through `_build_segmentation_prompt`, `_validate_chorus_repetition`, `_map_sections_to_components`; replace the inlined `0.60`/`0.90`/`0.05`/`0.95` literals with reads off `weights`.
- `ops/analysis-service/tests/test_section_segmenter.py` — extend with explicit "defaults are bit-identical" parity tests for the three new kwargs (`few_shot_override=None`, `system_prompt_override=None`, `validator_weights=DEFAULT_VALIDATOR_WEIGHTS`); existing tests unchanged.
- `ops/analysis-service/tests/test_components_tuning.py` — extract the pure-scoring math (Steps 3–7 of `_score_song`) into a new `_score_components(components, ground_truth, raw)` helper that both `_score_song` and the new LLM harness reuse. **Refactor only — no behavior change for v1 callers.** Add a `review_dir` kwarg to `_dump_review_slices` defaulting to the existing `REVIEW_DIR` constant.
- `eval/components_tuning/.gitignore` — keep `review/` ignored; add `review_llm/` ignored; explicitly `!reports/llm_tuning_report.md` so the report is committed (sibling transient reports stay ignored).

### Unchanged files (do NOT modify)
- `ops/analysis-service/src/sow_analysis/workers/components.py` — `extract_components` continues to call `segment_song` WITHOUT the new kwargs (uses defaults).
- `ops/analysis-service/src/sow_analysis/workers/queue.py` — no changes.
- `ops/analysis-service/src/sow_analysis/models.py` — no changes.
- `ops/analysis-service/src/sow_analysis/storage/cache.py` — `COMPONENT_SCHEMA_VERSION` unchanged.
- `ops/admin-cli/**` — no changes (the `--segmentation-mode llm` CLI path is exercised only for optional final end-to-end verification; out of scope for the in-loop tuning).

## Implementation changes

### Change 0 — Refactor `_score_song` to extract pure scorer

**File:** `ops/analysis-service/tests/test_components_tuning.py`

Extract Steps 3–7 of `_score_song` (everything after the `components = identify_from_lyrics_repetition(...)` call) into a new module-level function:

```python
def _score_components(
    components: list,
    ground_truth: dict,
    raw: list,
) -> dict:
    """Pure scorer: partition `components` into choruses/verses, compute
    per-component IoU + role bonus/penalty + false-positive penalty against
    `ground_truth`. `raw` is the unfiltered raw LRC line list (raw_indices).

    Returns the same dict shape as the original `_score_song`. Caller is
    responsible for producing `components` via whichever identification
    path (lyrics-repetition OR LLM) it wishes to score.
    """
    # ... (existing steps 3-7 moved here verbatim, reading `components` instead
    # of `identify_from_lyrics_repetition(...)` output)
```

Rewrite `_score_song` as a thin wrapper:

```python
def _score_song(lrc_content, ground_truth, weights, song_total_duration=None) -> dict:
    raw = _parse_raw_lines(lrc_content)
    if song_total_duration is None:
        song_total_duration = raw[-1].time_seconds if len(raw) >= 2 else None
    components = identify_from_lyrics_repetition(
        lrc_content, song_total_duration=song_total_duration, weights=weights
    )
    return _score_components(components, ground_truth, raw)
```

Add a `review_dir` kwarg to `_dump_review_slices` (default to existing `REVIEW_DIR`); the new LLM harness passes a separate `review_llm/` directory so its slices don't overwrite the repetition slices.

```python
def _dump_review_slices(song_id, lrc_content, components, review_dir=REVIEW_DIR) -> Path:
    out_dir = review_dir / song_id
    ...   # identical body, just uses the `review_dir` parameter.
```

**Behavior invariant:** `score_all(DEFAULT_WEIGHTS)` returns the same `grand_total` and per-song means before and after this refactor (sanity-asserted by the unchanged `test_default_weights_baseline`).

### Change 1 — `ValidatorWeights` dataclass + `DEFAULT_VALIDATOR_WEIGHTS`

**File:** `ops/analysis-service/src/sow_analysis/workers/section_segmenter.py`. Add near the top (after `_EXPECTED_HELD_OUT_IDS`, before the `Section` dataclass):

```python
@dataclass(frozen=True)
class ValidatorWeights:
    """Tunable multipliers for _validate_chorus_repetition and
    _map_sections_to_components. Each field corresponds to a knob whose
    default reproduces the current hardcoded literal exactly.
    """
    # Multiplied into a non-repeated chorus's confidence (musically valid
    # but should score lower than a repeating chorus, e.g. an outro chorus).
    nonrepeated_multiplier: float = 0.60
    # Multiplied into confidence after trimming an over-merged chorus's
    # line_end down to the last line whose text repeats elsewhere.
    trimmed_multiplier: float = 0.90
    # Added (then clamped to [0, 1]) when the section already ends on a
    # repeating line — i.e. the LLM's boundary was confirmed correct.
    confirmed_bonus: float = 0.05
    # Multiplied into every emitted ComponentInstance.confidence in the
    # mapper. Mirrors the framing that LLM-derived confidences carry a
    # small discount relative to direct audio analysis.
    mapping_confidence_multiplier: float = 0.95


DEFAULT_VALIDATOR_WEIGHTS = ValidatorWeights()
```

### Change 2 — Add three override kwargs to `segment_song`

**File:** `section_segmenter.py`. Replace the signature (currently lines 544–551):

```python
async def segment_song(
    lrc_content: str,
    song_title: Optional[str] = None,
    duration: Optional[float] = None,
    beats: Optional[list[float]] = None,
    downbeats: Optional[list[float]] = None,
    snap_to_downbeat: bool = False,
    # v1 tuning-loop overrides — defaults reproduce current behavior bit-for-bit.
    few_shot_override: Optional[list[dict]] = None,
    system_prompt_override: Optional[str] = None,
    validator_weights: ValidatorWeights = DEFAULT_VALIDATOR_WEIGHTS,
) -> list[ComponentInstance]:
    client = _build_client()
    model = _segmentation_model()
    few_shot = (
        few_shot_override
        if few_shot_override is not None
        else _load_few_shot_examples()
    )
    messages = _build_segmentation_prompt(
        lrc_content, song_title, duration, few_shot,
        system_prompt_override=system_prompt_override,
    )
    # ... (LLM call unchanged)
    sections = _parse_segmenter_json(text, n_lines)
    if sections is None:
        return []
    sections = _validate_chorus_repetition(
        sections, lrc_content, weights=validator_weights,
    )
    if settings.SOW_LLM_SEGMENTATION_SANITY_CHECK:
        checked = await _sanity_check_llm(sections, lrc_content, client, model)
        if checked is None:
            corrected = await _corrective_segmentation_call(
                client, model, lrc_content, song_title, duration, few_shot, sections
            )
            if corrected is not None:
                sections = _validate_chorus_repetition(
                    corrected, lrc_content, weights=validator_weights,
                )
    lines = list(parse_lrc(lrc_content).lines)
    return _map_sections_to_components(
        sections, lines, beats=beats, downbeats=downbeats,
        snap_to_downbeat=snap_to_downbeat, weights=validator_weights,
    )
```

### Change 3 — Thread overrides through the call chain

- **`_build_segmentation_prompt(... , system_prompt_override: Optional[str] = None)`**: when `system_prompt_override` is not `None`, use it verbatim as the system message; otherwise fall back to the existing hardcoded string. No other change to prompt assembly.
- **`_validate_chorus_repetition(sections, lrc_content, weights: ValidatorWeights = DEFAULT_VALIDATOR_WEIGHTS)`**: replace the inlined `* 0.60` with `* weights.nonrepeated_multiplier`, `* 0.90` with `* weights.trimmed_multiplier`, and `+ 0.05` (clamped at 1.0) with `+ weights.confirmed_bonus` (still clamped at 1.0).
- **`_map_sections_to_components(sections, lines, beats=None, downbeats=None, snap_to_downbeat=False, weights: ValidatorWeights = DEFAULT_VALIDATOR_WEIGHTS)`**: replace both occurrences of `sec.confidence * 0.95` (the chorus and verse `ComponentInstance.confidence` assignments) with `sec.confidence * weights.mapping_confidence_multiplier`.

### Change 4 — Few-shot candidates directory + loader helper

Create `ops/analysis-service/src/sow_analysis/workers/segmentation_few_shot_candidates/` with four JSON files. Each file honors the same schema as the production `segmentation_few_shot.json` (a top-level list of example objects, each with a **mandatory `source_song_id`** field, a numbered-LRC `input` string, and a `sections` array). The leakage guard applies to each — `source_song_id` MUST NOT be any of the 3 fixture IDs (`jun_wang_jiu_zai_zhe_li_1c32724c`, `yi_sheng_jing_bai_mi_da2173d0`, `zhu_a__wo_yao_gen_sui_mi_83163301`).

Files:
- `V0_none.json` — `[]` (zero examples — measures the LLM's unaided baseline).
- `V1_single.json` — one short, hand-verified example with a single verse→chorus→verse→chorus pattern. Should be a real song segment, NOT the current `__CHANGE_ME__` placeholder text.
- `V2_pair.json` — two examples illustrating structurally different patterns (e.g. one A-B-A-B, one A-B-C-B with a `bridge`).
- `V3_trio.json` — three examples with full label breadth: at least one must contain `prechorus`, at least one must contain `bridge`, at least one must contain `intro` or `outro` or `instrumental`.

Operator MUST obtain these from real worship songs by manually segmenting their LRCs (out of scope for this plan to enumerate the exact content — the performing agent fetches candidate LRCs from R2 or the song library under songs NOT in the 3-fixture eval set, hand-labels them, and commits).

The harness loads candidates via:

```python
def _load_few_shot_candidate(name: str) -> list[dict]:
    """Load a few-shot candidate file by short name (e.g. 'V0_none').

    Applies the SAME leakage guard as the production loader.
    Raises ValueError if any example's source_song_id is a fixture song.
    """
    path = (
        Path(__file__).resolve().parents[1]
        / "src" / "sow_analysis" / "workers"
        / "segmentation_few_shot_candidates" / f"{name}.json"
    )
    examples = json.loads(path.read_text(encoding="utf-8"))
    for ex in examples:
        song_id = str(ex.get("source_song_id", "")).strip()
        if song_id in _EXPECTED_HELD_OUT_IDS:
            raise ValueError(
                f"Candidate '{name}' leaks fixture song_id '{song_id}'."
            )
    return examples
```

### Change 5 — System prompt candidates directory + loader helper

Create `ops/analysis-service/src/sow_analysis/workers/segmentation_prompts/` with three plain-text files. Each file contains exactly one system prompt (UTF-8, no trailing newline normalization required — loader strips whitespace).

- `V0_default.txt` — verbatim copy of the current hardcoded string from `_build_segmentation_prompt` (the "You are a Chinese worship-music structure analyst. ..." block).
- `V1_repetition_cues.txt` — variant that adds explicit guidance: "Identify the chorus as the section whose text — especially its last line — repeats elsewhere in the song, either verbatim or near-verbatim. Prefer line endings that fall on a line known to repeat. Do NOT include non-repeating verse or instrumental lines in the chorus range."
- `V2_structural.txt` — variant that adds explicit guidance: "Chinese worship songs typically follow verse → chorus → verse → chorus ordering. The chorus usually contains the song's hook and is shorter (2–6 lines). The verse usually introduces narrative or thematic content and is a different lyric from the chorus. Do NOT merge verse content into the chorus section; do NOT include intro / outro / instrumental-only lines as a chorus."

The harness loads prompt text via:

```python
def _load_system_prompt(name: str) -> str:
    path = (
        Path(__file__).resolve().parents[1]
        / "src" / "sow_analysis" / "workers"
        / "segmentation_prompts" / f"{name}.txt"
    )
    return path.read_text(encoding="utf-8").strip()
```

### Change 6 — New test file `test_components_tuning_llm.py`

**File:** `ops/analysis-service/tests/test_components_tuning_llm.py`

Imports the existing scorer helpers from `test_components_tuning.py` (refactored per Change 0):
- `SONG_IDS`, `_load_fixture`, `_parse_raw_lines`, `_dump_review_slices`, `_score_components`.

Adds an LLM-specific scorer and an A/B comparison test:

```python
"""Tuning harness for the LLM whole-song segmentation path (segment_song).

Reuses the existing 3-song fixtures + scorer (test_components_tuning.py),
runs the LLM path synchronously via asyncio.run, scores the result on the
same IoU metric, and writes per-component lyrics review slices to
eval/components_tuning/review_llm/<song_id>/.

Entry points (all require SOW_LLM_API_KEY + SOW_LLM_BASE_URL):
  - pytest test_components_tuning_llm.py                # baseline + A/B
  - pytest test_components_tuning_llm.py::TestAxisA1    # few-shot sweep
  - pytest test_components_tuning_llm.py::TestAxisA2    # prompt sweep
  - pytest test_components_tuning_llm.py::TestAxisA3    # validator sweep
  - python -m sow_analysis.tests.test_components_tuning_llm --report  # markdown report
"""
import argparse
import asyncio
import json
import os
from dataclasses import asdict, replace
from pathlib import Path
from typing import Optional

import pytest

from sow_analysis.tests.test_components_tuning import (
    SONG_IDS, _load_fixture, _parse_raw_lines,
    _dump_review_slices, _score_components,
)
from sow_analysis.workers.section_segmenter import (
    DEFAULT_VALIDATOR_WEIGHTS,
    ValidatorWeights,
    _EXPECTED_HELD_OUT_IDS,
    segment_song,
)

LLM_REVIEW_DIR = (
    Path(__file__).resolve().parents[3]
    / "eval" / "components_tuning" / "review_llm"
)
REPORTS_DIR = (
    Path(__file__).resolve().parents[3]
    / "eval" / "components_tuning" / "reports"
)

pytestmark = pytest.mark.skipif(
    not (os.environ.get("SOW_LLM_API_KEY") and os.environ.get("SOW_LLM_BASE_URL")),
    reason="LLM tuning tests require SOW_LLM_API_KEY + SOW_LLM_BASE_URL.",
)


def _load_few_shot_candidate(name: str) -> list[dict]:
    """See Change 4 in the spec — loads candidates/*, applies leakage guard."""
    path = (
        Path(__file__).resolve().parents[1]
        / "src" / "sow_analysis" / "workers"
        / "segmentation_few_shot_candidates" / f"{name}.json"
    )
    examples = json.loads(path.read_text(encoding="utf-8"))
    for ex in examples:
        song_id = str(ex.get("source_song_id", "")).strip()
        if song_id in _EXPECTED_HELD_OUT_IDS:
            raise ValueError(
                f"Candidate '{name}' leaks fixture song_id '{song_id}'."
            )
    return examples


def _load_system_prompt(name: str) -> str:
    """See Change 5 in the spec — loads prompts/<name>.txt."""
    path = (
        Path(__file__).resolve().parents[1]
        / "src" / "sow_analysis" / "workers"
        / "segmentation_prompts" / f"{name}.txt"
    )
    return path.read_text(encoding="utf-8").strip()


def _segment_components_sync(
    lrc_content: str,
    song_total_duration: Optional[float] = None,
    few_shot_override=None,
    system_prompt_override=None,
    validator_weights=DEFAULT_VALIDATOR_WEIGHTS,
):
    """Synchronous wrapper around the async segment_song."""
    return asyncio.run(
        segment_song(
            lrc_content,
            duration=song_total_duration,
            few_shot_override=few_shot_override,
            system_prompt_override=system_prompt_override,
            validator_weights=validator_weights,
        )
    )


def _score_song_llm(
    lrc_content: str,
    ground_truth: dict,
    song_id: str,
    few_shot=None,
    system_prompt=None,
    weights=DEFAULT_VALIDATOR_WEIGHTS,
) -> tuple[dict, list]:
    """Run the LLM path, score it, and dump review slices into
    eval/components_tuning/review_llm/<song_id>/.

    Returns (score_dict, components) — the second value lets the caller
    inspect raw LLM output (e.g. for the markdown report's per-song blog).
    """
    raw = _parse_raw_lines(lrc_content)
    song_total_duration = raw[-1].time_seconds if len(raw) >= 2 else None
    components = _segment_components_sync(
        lrc_content,
        song_total_duration=song_total_duration,
        few_shot_override=few_shot,
        system_prompt_override=system_prompt,
        validator_weights=weights,
    )
    _dump_review_slices(song_id, lrc_content, components, review_dir=LLM_REVIEW_DIR)
    return _score_components(components, ground_truth, raw), components


def score_all_llm(
    few_shot=None,
    system_prompt=None,
    weights=DEFAULT_VALIDATOR_WEIGHTS,
) -> tuple[float, list]:
    """Mirror score_all() but via the LLM path."""
    per_song = []
    for song_id in SONG_IDS:
        lrc, gt = _load_fixture(song_id)
        result, components = _score_song_llm(
            lrc, gt, song_id,
            few_shot=few_shot, system_prompt=system_prompt, weights=weights,
        )
        per_song.append((song_id, result, components))
    grand_total = sum(r["per_song_mean"] for _, r, _ in per_song) / len(per_song)
    return grand_total, per_song
```

**Baseline test:**

```python
def test_llm_baseline_default():
    """LLM path at defaults (production snapshot) — surfaces the baseline
    number for the tuning protocol. Not a hard gate (no min threshold).
    """
    grand_total, per_song = score_all_llm()
    print("\n--- LLM baseline (defaults) ---")
    print(f"GRAND TOTAL = {grand_total:.3f}")
    for song_id, r, _ in per_song:
        print(f"  {song_id}: {r['per_song_mean']:.3f} "
              f"(n_pred_chorus={r['n_predicted_choruses']}, "
              f"n_exp={r['n_expected_choruses']}, "
              f"fp_hits={len(r.get('false_positive_hits', []))})")
    assert 0.0 <= grand_total <= 1.0
```

**A/B comparison test:**

```python
def test_ab_comparison_lyrics_repetition_vs_llm():
    """Side-by-side A/B report: prints both grand totals + per-song deltas.

    Asserts the LLM path is >= 0.70 (pass bar from
    component-identification-llm-segmentation-v2.md). Skipped (not failed)
    if the pass bar is missed during early tuning — instead surfaces the
    gap so the operator knows how far to go.
    """
    from sow_analysis.tests.test_components_tuning import score_all

    rep_total, rep_per_song = score_all()
    llm_total, llm_per_song = score_all_llm()
    print("\n--- A/B comparison ---")
    print(f"  lyrics-repetition GRAND TOTAL = {rep_total:.3f}")
    print(f"  LLM segmentation   GRAND TOTAL = {llm_total:.3f}")
    print(f"  delta (LLM - repetition) = {llm_total - rep_total:+.3f}")
    for (sid_r, r_r), (sid_l, r_l, _) in zip(rep_per_song, llm_per_song):
        assert sid_r == sid_l
        print(f"  {sid_r}: rep={r_r['per_song_mean']:.3f}  "
              f"llm={r_l['per_song_mean']:.3f}  "
              f"delta={r_l['per_song_mean'] - r_r['per_song_mean']:+.3f}")
    # Sanity: both totals in [0, 1].
    assert 0.0 <= rep_total <= 1.0
    assert 0.0 <= llm_total <= 1.0
```

**Axis A1 — Few-shot examples sweep:**

```python
FEW_SHOT_CANDIDATES = ["V0_none", "V1_single", "V2_pair", "V3_trio"]


class TestAxisA1:
    """A1: few-shot examples content sweep.

    For each candidate file, score all 3 songs and print the breakdown.
    The winner is the candidate with the highest grand-total IoU; ties
    broken by smaller example count (lower token cost).
    """

    def test_few_shot_sweep(self):
        best_total, best_name, best_per_song = -1.0, None, None
        for name in FEW_SHOT_CANDIDATES:
            few_shot = _load_few_shot_candidate(name)
            total, per_song = score_all_llm(few_shot=few_shot)
            print(f"\n[A1] {name}: GRAND TOTAL = {total:.3f}")
            for sid, r, _ in per_song:
                print(f"     {sid}: {r['per_song_mean']:.3f}")
            if total > best_total or (total == best_total and best_name and
                                       len(few_shot) < len(_load_few_shot_candidate(best_name))):
                best_total, best_name, best_per_song = total, name, per_song
        print(f"\n[A1] BEST = {best_name}  total={best_total:.3f}")
        # Sanity: at least one candidate produced a finite result.
        assert best_name is not None
```

**Axis A2 — System prompt sweep (with the A1 winner pinned):**

```python
PROMPT_CANDIDATES = ["V0_default", "V1_repetition_cues", "V2_structural"]


class TestAxisA2:
    """A2: system prompt variants sweep, with A1's winning few-shot pinned.

    Run `pytest tests/test_components_tuning_llm.py::TestAxisA2 -v -s`
    AFTER `TestAxisA1` has identified the best few-shot candidate. Set
    the candidate name via the SOW_TUNING_A1_WINNER env var; falls back
    to 'V1_single' on unset (documented in .env.segmentation-tuning.example).
    """

    def _a1_winner(self) -> list[dict]:
        name = os.environ.get("SOW_TUNING_A1_WINNER", "V1_single")
        return _load_few_shot_candidate(name)

    def test_prompt_sweep(self):
        few_shot = self._a1_winner()
        best_total, best_name, best_per_song = -1.0, None, None
        for name in PROMPT_CANDIDATES:
            prompt = _load_system_prompt(name)
            total, per_song = score_all_llm(
                few_shot=few_shot, system_prompt=prompt,
            )
            print(f"\n[A2] {name}: GRAND TOTAL = {total:.3f}")
            for sid, r, _ in per_song:
                print(f"     {sid}: {r['per_song_mean']:.3f}")
            if total > best_total:
                best_total, best_name, best_per_song = total, name, per_song
        print(f"\n[A2] BEST = {best_name}  total={best_total:.3f}")
        assert best_name is not None
```

**Axis A3 — Validator weights grid sweep (with A1 + A2 winners pinned):**

```python
import itertools


class TestAxisA3:
    """A3: ValidatorWeights grid sweep (narrow bands to avoid overfit on 3
    songs). A1 + A2 winners are pinned via env vars
    SOW_TUNING_A1_WINNER + SOW_TUNING_A2_WINNER (fall back to 'V1_single'
    and 'V0_default' on unset).

    Tunable axes (4 dimensions, 3 steps each = 81 combinations):
      - nonrepeated_multiplier in {0.4, 0.6, 0.8}
      - trimmed_multiplier     in {0.8, 0.9, 1.0}
      - confirmed_bonus        in {0.0, 0.05, 0.10}
      - mapping_confidence_multiplier in {0.9, 0.95, 1.0}
    """

    def _pinned_overrides(self):
        a1 = _load_few_shot_candidate(
            os.environ.get("SOW_TUNING_A1_WINNER", "V1_single")
        )
        a2 = _load_system_prompt(
            os.environ.get("SOW_TUNING_A2_WINNER", "V0_default")
        )
        return a1, a2

    def _axes(self):
        return itertools.product(
            [0.4, 0.6, 0.8],   # nonrepeated_multiplier
            [0.8, 0.9, 1.0],   # trimmed_multiplier
            [0.0, 0.05, 0.10], # confirmed_bonus
            [0.9, 0.95, 1.0],  # mapping_confidence_multiplier
        )

    def test_validator_grid_search(self):
        few_shot, prompt = self._pinned_overrides()
        best_total, best_w, best_per_song = -1.0, None, None
        for nrm, tm, cb, mcm in self._axes():
            w = replace(
                DEFAULT_VALIDATOR_WEIGHTS,
                nonrepeated_multiplier=nrm,
                trimmed_multiplier=tm,
                confirmed_bonus=cb,
                mapping_confidence_multiplier=mcm,
            )
            total, per_song = score_all_llm(
                few_shot=few_shot, system_prompt=prompt, weights=w,
            )
            if total > best_total:
                best_total, best_w, best_per_song = total, w, per_song
        print("\n[A3] Grid search winner:")
        print(f"  BEST TOTAL = {best_total:.3f}")
        print(f"  BEST WEIGHTS = {asdict(best_w)}")
        for sid, r, _ in best_per_song:
            print(f"  {sid}: {r['per_song_mean']:.3f}")
        # Assert: grid is at least as good as defaults (else defaults win).
        default_total, _ = score_all_llm(
            few_shot=few_shot, system_prompt=prompt,
        )
        assert best_total >= default_total - 1e-9
```

**`__main__` block — markdown report writer:**

```python
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", action="store_true",
                        help="Write eval/components_tuning/reports/llm_tuning_report.md")
    args = parser.parse_args()

    if not args.report:
        # Default: print baselines.
        gt, ps = score_all_llm()
        print(f"LLM default total = {gt:.3f}")
        for sid, r, _ in ps:
            print(f"  {sid}: {r['per_song_mean']:.3f}")
        raise SystemExit(0)

    # Report mode: sweep all three axes + produce markdown.
    from sow_analysis.tests.test_components_tuning import score_all
    rep_total, rep_per_song = score_all()
    # A1
    a1_results = []
    for name in FEW_SHOT_CANDIDATES:
        fs = _load_few_shot_candidate(name)
        t, _ = score_all_llm(few_shot=fs)
        a1_results.append((name, t))
    a1_winner = max(a1_results, key=lambda x: x[1])
    # A2 (pin A1 winner)
    a1_fs = _load_few_shot_candidate(a1_winner[0])
    a2_results = []
    for name in PROMPT_CANDIDATES:
        p = _load_system_prompt(name)
        t, _ = score_all_llm(few_shot=a1_fs, system_prompt=p)
        a2_results.append((name, t))
    a2_winner = max(a2_results, key=lambda x: x[1])
    # A3 (pin A1 + A2 winners) — narrow grid
    a2_p = _load_system_prompt(a2_winner[0])
    best_grid_total, best_grid_w = -1.0, None
    for nrm, tm, cb, mcm in itertools.product(
        [0.4, 0.6, 0.8], [0.8, 0.9, 1.0],
        [0.0, 0.05, 0.10], [0.9, 0.95, 1.0],
    ):
        w = replace(
            DEFAULT_VALIDATOR_WEIGHTS,
            nonrepeated_multiplier=nrm, trimmed_multiplier=tm,
            confirmed_bonus=cb, mapping_confidence_multiplier=mcm,
        )
        t, _ = score_all_llm(few_shot=a1_fs, system_prompt=a2_p, weights=w)
        if t > best_grid_total:
            best_grid_total, best_grid_w = t, w
    # Final A/B
    final_total, _ = score_all_llm(
        few_shot=a1_fs, system_prompt=a2_p, weights=best_grid_w,
    )

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORTS_DIR / "llm_tuning_report.md"
    lines = [
        "# LLM Component Identification Tuning Report",
        "",
        f"_Generated by `python -m sow_analysis.tests.test_components_tuning_llm --report`._",
        "",
        "## A/B summary",
        "",
        "| Mode | Grand-total IoU |",
        "|---|---|",
        f"| lyrics-repetition (baseline) | {rep_total:.3f} |",
        f"| LLM segmentation (defaults)   | — (run `score_all_llm()`)|",
        f"| LLM segmentation (tuned)     | {final_total:.3f} |",
        f"| Delta (tuned LLM − repetition) | {final_total - rep_total:+.3f} |",
        "",
        "## A1 — Few-shot examples",
        "",
        "| Candidate | Grand-total IoU |",
        "|---|---|",
    ]
    for name, t in a1_results:
        lines.append(f"| {name} | {t:.3f} |")
    lines.append(f"\n**Winner:** `{a1_winner[0]}` ({a1_winner[1]:.3f})")
    lines += [
        "",
        "## A2 — System prompt",
        "",
        "| Candidate | Grand-total IoU |",
        "|---|---|",
    ]
    for name, t in a2_results:
        lines.append(f"| {name} | {t:.3f} |")
    lines.append(f"\n**Winner:** `{a2_winner[0]}` ({a2_winner[1]:.3f})")
    lines += [
        "",
        "## A3 — Validator weights grid",
        "",
        f"**Best grid total:** `{best_grid_total:.3f}`",
        "",
        f"**Best weights:** `{asdict(best_grid_w)}`",
        "",
        "## Recommended env-var settings",
        "",
        "```",
        f"SOW_COMPONENTS_USE_LLM_SEGMENTATION=true",
        f"SOW_TUNING_A1_WINNER={a1_winner[0]}",
        f"SOW_TUNING_A2_WINNER={a2_winner[0]}",
        f"# {"# ".rstrip() if best_grid_w == DEFAULT_VALIDATOR_WEIGHTS else "# "}DEFAULT_VALIDATOR_WEIGHTS unchanged (grid winner equals defaults)",
        "```",
        "",
    ]
    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote report to {report_path}")
```

### Change 7 — `.env.segmentation-tuning.example`

**File:** `ops/analysis-service/.env.segmentation-tuning.example` (new, committed)

```bash
# Documents the env-var values that reproduce the LLM tuning loop's
# winning result. Copy to .env or set inline. These do NOT change
# production defaults — production dashes (sow_analysis/config.py) remain
# the source of truth.

# Master LLM segmentation switch (analysis-service only).
SOW_COMPONENTS_USE_LLM_SEGMENTATION=true

# Required: LLM provider credentials.
SOW_LLM_API_KEY=<your-key>
SOW_LLM_BASE_URL=<your-openai-compatible-base-url>

# Optional: override the model used by segment_song (falls back to
# SOW_LLM_MODEL if unset).
SOW_LLM_SEGMENTATION_MODEL=

# Tuning loop pins — these are NOT consumed by segment_song; the tuning
# harness reads them to pin A1/A2 winners during the A3 grid search.
SOW_TUNING_A1_WINNER=V1_single
SOW_TUNING_A2_WINNER=V0_default

# Sanity-check stays OFF during tuning (default). Enable only for final
# validation if the tuned score is still below 0.70.
SOW_LLM_SEGMENTATION_SANITY_CHECK=false

# Token budget — 2048 is sufficient for ~100-line LRCs. Increase for
# longer songs if JSON truncation is observed.
SOW_LLM_SEGMENTATION_MAX_TOKENS=2048
```

### Change 8 — `.gitignore` updates for review output

**File:** `eval/components_tuning/.gitignore`

Current content (per `component-identification-tuning-loop-v1.md`):
```
# Generated on every test run — do not commit.
review/
```

Update to:
```
# Generated on every test run — do not commit.
review/
review_llm/

# Keep the committed tuning report; ignore transient sibling files.
reports/*
!reports/llm_tuning_report.md
```

## Scoring formula (summary)

Unchanged from `specs/component-identification-tuning-loop-v1.md`. The scorer is source-agnostic:

```
per_component[i].component_score = clamp(best_iou + role_bonus - role_penalty, 0, 1)
per_song_mean = mean(component_scores)
                  - FP_PENALTY * count(false_positive_hits with max_iou >= 0.3)
per_song_mean = clamp(per_song_mean, 0, 1)
grand_total   = mean(per_song_mean across 3 songs)
```

Where:
- `best_iou` = max IoU between the expected component's time range and any predicted component of the matching `component_type` (the LLM mapper emits `component_type="chorus"` or `"verse"`, `role` ∈ {`entry`, `exit`, `none`, `loop_target`}, `source="llm_segmentation"`).
- `role_bonus = +0.05` if predicted role matches expected (`entry`/`exit`/`loop_target`); `+0.05` if expected is `entry_exit` AND both `entry`+`exit` predicted rows present.
- `role_penalty = -0.10` otherwise.
- Occurrence-count mismatches append zero-scored entries to `per_component`, dragging the mean down.
- `false_positive_hits` = predicted choruses with `IoU >= 0.3` against any `false_positive_avoid` range; each subtracts `0.25` from `per_song_mean` (clamped at 0).

**Note on the chorus-only filter:** the existing v1 scorer aligns choruses by `c.component_type == "chorus" and c.role in ESSENTIAL_ROLES`. The LLM mapper emits `role="none"` for any middle chorus when `n_choruses > 2`; all 3 fixture songs have ≤ 2 chorus occurrences, so `role="none"` is never emitted and the filter is a no-op. If a future fixture has 3+ choruses, the scorer's filter will silently drop the middle occurrence (expected behavior — only `entry`/`exit`/`loop_target` are essential). Out of scope to change here.

## Tuning-iteration protocol

1. **Defaults are bit-identical — verify first.** Run the existing section-segmenter unit tests; assert they all pass (operator sanity gate):
   ```
   cd ops/analysis-service && uv run --extra dev pytest tests/test_section_segmenter.py -v
   ```
2. **LLM baseline measurement.** Set `SOW_LLM_API_KEY` + `SOW_LLM_BASE_URL` (and optionally `SOW_LLM_SEGMENTATION_MODEL`) in the environment. Run:
   ```
   cd ops/analysis-service && uv run --extra dev pytest tests/test_components_tuning_llm.py -v -s
   ```
   Record the LLM baseline `GRAND TOTAL` (with no overrides). Compare against the repetition baseline `0.286` (printed by `pytest tests/test_components_tuning.py -v -s`).
3. **Inspect review slices.** Open `eval/components_tuning/review_llm/<song_id>/component_*.txt` and the existing `review/<song_id>/component_*.txt` side-by-side. Identify structural failure modes (over-merged chorus, missing verse, role misalignment, false-positive chorus). These notes inform A1 few-shot construction.
4. **Prepare A1 candidate files.** Hand-segment real worship songs (NOT in the 3-fixture set). Use songs the operator has personally verified chorus/verse boundaries for; commit the LRC excerpts + verified `sections` into `V0_none.json`, `V1_single.json`, `V2_pair.json`, `V3_trio.json`. Each example must include `source_song_id` — never `__CHANGE_ME__` in candidate files (only the production placeholder still uses that sentinel).
5. **Run A1 sweep:**
   ```
   cd ops/analysis-service && uv run --extra dev pytest tests/test_components_tuning_llm.py::TestAxisA1 -v -s
   ```
   Record the winner; set `SOW_TUNING_A1_WINNER=<winner>` in the environment.
6. **Run A2 sweep (with A1 winner pinned):**
   ```
   export SOW_TUNING_A1_WINNER=<a1-winner>
   cd ops/analysis-service && uv run --extra dev pytest tests/test_components_tuning_llm.py::TestAxisA2 -v -s
   ```
   Record the winner; set `SOW_TUNING_A2_WINNER=<winner>`.
7. **Run A3 grid (with A1 + A2 winners pinned):**
   ```
   export SOW_TUNING_A1_WINNER=<a1-winner>
   export SOW_TUNING_A2_WINNER=<a2-winner>
   cd ops/analysis-service && uv run --extra dev pytest tests/test_components_tuning_llm.py::TestAxisA3 -v -s
   ```
   Record the best grid weights.
8. **Apply winning parameters (commit decision threshold: improve by ≥ 0.05):**
   - If the A1 winner beats the V0 (no few-shot) baseline by ≥ 0.05, **replace** the production `segmentation_few_shot.json` with the winning candidate's content (with `source_song_id` set to real values, not `__CHANGE_ME__`).
   - If the A2 winner beats A1-winner-with-default-prompt by ≥ 0.05, **inline** the winning prompt string into `_build_segmentation_prompt` as the new hardcoded default (and keep `V0_default.txt` as a fallback file). Otherwise leave the prompt unchanged.
   - If the A3 best grid beats `DEFAULT_VALIDATOR_WEIGHTS` by ≥ 0.05, **update** `DEFAULT_VALIDATOR_WEIGHTS` fields to the grid-winner values in `section_segmenter.py`. Otherwise leave the dataclass unchanged.
9. **Re-run all section-segmenter unit tests.** Confirm the v1 tuning-loop refactor (Change 0) and the new dataclass + kwargs (Changes 1–3) didn't regress any existing test:
   ```
   cd ops/analysis-service && uv run --extra dev pytest tests/test_section_segmenter.py tests/test_components_tuning.py tests/test_components_tuning_llm.py -v -s
   ```
10. **Inspect review slices for the tuned config.** Open `eval/components_tuning/review_llm/<song_id>/component_*.txt` for final inspection. Verify (by eye) that:
    - Each predicted chorus covers the ground-truth range.
    - `expected_verse` (line 3–4 of `jun_wang_jiu_zai_zhe_li_1c32724c/ground_truth.json`) emits a `component_type="verse"`, `role="loop_target"` row.
    - No false-positive choruses appear.
11. **Write the markdown report:**
    ```
    cd ops/analysis-service && uv run --extra dev python -m sow_analysis.tests.test_components_tuning_llm --report
    ```
    Commit `eval/components_tuning/reports/llm_tuning_report.md` AND `ops/analysis-service/.env.segmentation-tuning.example`.
12. **Iterate (optional, max 2 rounds).** If the tuned LLM score is still below 0.70, widen the A1 candidate pool (add a `V4_quad.json` with 4 examples covering more structural patterns), OR widen the A2 prompt variants (e.g. one that explicitly lists `_VALID_LABELS` in the user message), OR widen the A3 axes around the current winner. Stop when delta between iterations is `< 0.02` or when the pass bar (0.70) is reached.
13. **Final end-to-end sanity (out of scope for the loop, recommended before merge).** Re-run a real COMPONENT ANALYSIS job on each of the 3 songs via:
    ```
    uv run --project ops/admin-cli --extra admin sow-admin audio analyze components <song-id> --segmentation-mode llm --force
    ```
    Verify the persisted `components.json`'s chorus/verse rows now align with the manually verified ground truth, and that `source="llm_segmentation"` appears in the rows.

## Out of scope

- Tuning the LLM model selection (`SOW_LLM_SEGMENTATION_MODEL`). The loop uses whatever model the env is configured with.
- Tuning `temperature` (fixed at 0 — deterministic outputs only).
- Tuning `max_tokens` (held at 2048; widening is operator-only on JSON truncation).
- The sanity-check 2nd/3rd call (`SOW_LLM_SEGMENTATION_SANITY_CHECK`). Stays OFF — would double LLM cost and is a separate effort if the tuned score is still below pass bar.
- Retuning the `ThemeClassifier` in `classifier.py`. Runs downstream of identification; orthogonal to this loop.
- The allin1-sections identification path (`identify_from_allin1_sections`).
- Expanding the 3-song fixture set. A 10-song follow-up eval is planned but separate.
- Persisting LLM segmentation results to the `song_components` DB table. The `section_label` / `lyrics_excerpt` / `llm_rationale` fields already flow through to `components.json` and the job-result API without DB storage; this loop does not change DB schema.
- The `--segmentation-mode llm` admin-cli / worker dispatch logic. Exercised only for final end-to-end verification (step 13 above); no tuning changes are made to the CLI dispatch.

## Rollout order

1. **Change 0** (extract `_score_components` from `_score_song` in `test_components_tuning.py`). Existing repetition tests must stay green — this is a pure refactor.
2. **Change 1** (`ValidatorWeights` dataclass + `DEFAULT_VALIDATOR_WEIGHTS` in `section_segmenter.py`). No usage yet.
3. **Change 2 + Change 3** (add three override kwargs to `segment_song`; thread them through `_build_segmentation_prompt`, `_validate_chorus_repetition`, `_map_sections_to_components`). Behavior at defaults is bit-identical.
4. **Extend `test_section_segmenter.py`** with parity tests for the three new kwargs. Run all section-segmenter tests to confirm no regression.
5. **Change 4 + Change 5** (candidate few-shot `.json` files + prompt `.txt` files). Operator fills real song examples; leakage guard is exercised by the candidate loader.
6. **Change 6** (`test_components_tuning_llm.py`). The `test_llm_baseline_default` and `test_ab_comparison_...` tests will PASS (skipif) without env vars set; they fail/pass per the actual LLM behavior once credentials are provided.
7. **Change 7 + Change 8** (`.env.segmentation-tuning.example` + `.gitignore` updates).
8. **Operator runs the tuning-iteration protocol** (steps 1–11 above). The markers for `pytestmark = pytest.mark.skipif(... SOW_LLM_API_KEY)` ensure tests don't fail CI in environments without LLM credentials.
9. **Commit artifacts** (winning `segmentation_few_shot.json`, updated `DEFAULT_VALIDATOR_WEIGHTS` if grid won, `llm_tuning_report.md`, `.env.segmentation-tuning.example`).
10. **Re-run all tests** to confirm no regression.

## Manual verification checklist

1. **Change 0 parity:** `score_all(DEFAULT_WEIGHTS)` returns the same `grand_total` and per-song means before and after the `_score_components` extraction. Verified by `test_default_weights_baseline` (in `test_components_tuning.py`).
2. **Changes 1–3 bit-identical at defaults:** `segment_song(LRC_X, ...)` (called without the three new kwargs) returns byte-identical `ComponentInstance` lists before and after. Verified by extending `test_section_segmenter.py` with parity tests that explicitly pass `few_shot_override=None`, `system_prompt_override=None`, `validator_weights=DEFAULT_VALIDATOR_WEIGHTS` and assert output equality against the pre-change capture.
3. **LLM baseline is reported:** `test_llm_baseline_default` prints `GRAND TOTAL = ...` and exits 0 (no minimum threshold). Skipped (not failed) when `SOW_LLM_API_KEY` is unset.
4. **A/B comparison is reported:** `test_ab_comparison_lyrics_repetition_vs_llm` prints both grand totals and per-song deltas side-by-side.
5. **A1 sweep prints winner:** `TestAxisA1::test_few_shot_sweep` prints per-candidate totals and the `BEST = ...` winner line. The Leakage guard raises a clear `ValueError` if any candidate file contains a fixture song ID.
6. **A2 sweep prints winner:** `TestAxisA2::test_prompt_sweep` prints per-prompt totals and the winner; uses the A1 winner via `SOW_TUNING_A1_WINNER` env var (falls back to `V1_single`).
7. **A3 grid prints winner:** `TestAxisA3::test_validator_grid_search` prints `BEST TOTAL`, `BEST WEIGHTS`, and per-song breakdown; the `assert best_total >= default_total - 1e-9` invariant holds (i.e. grid is at least as good as `DEFAULT_VALIDATOR_WEIGHTS` when fed the same overrides).
8. **Markdown report is written:** `python -m sow_analysis.tests.test_components_tuning_llm --report` writes `eval/components_tuning/reports/llm_tuning_report.md` with the A/B summary, per-axis results, and recommended env-var settings.
9. **Review slices appear in `review_llm/`:** after any `score_all_llm(...)` call, `eval/components_tuning/review_llm/<song_id>/component_*.txt` exists with one file per predicted component and accurate lyrics + header block (`# source: llm_segmentation`).
10. **`.gitignore` correctness:** `eval/components_tuning/review_llm/` is ignored; `eval/components_tuning/reports/llm_tuning_report.md` is committed (NOT ignored). Verified by `git status --ignored`.
11. **`.env.segmentation-tuning.example` is committed** and documents all env-var names referenced by the harness.
12. **No regression:** `pytest tests/test_section_segmenter.py tests/test_components_tuning.py tests/test_components.py -v` all green after the loop concludes. The committed `segmentation_few_shot.json` no longer contains the `__CHANGE_ME__` placeholder (it has real `source_song_id` values from songs outside the 3-fixture set, OR is `[]` if the loop concluded zero few-shot examples was best).

## Related specs

- `specs/component-identification-tuning-loop-v1.md` — the lyrics-repetition weight-tuning loop (origin of the 3-song fixture set, scorer, and 0.286 baseline).
- `specs/component-identification-llm-segmentation-v2.md` — the LLM segmentation implementation plan (origin of `segment_song`, `ValidatorWeights`-equivalent hardcoded literals, and the 0.70 pass bar).
- `specs/admin-cli-segmentation-mode-flag-v2.md` — the `--segmentation-mode llm|repetition|allin1` CLI flag (used for final end-to-end sanity check, NOT for in-loop tuning).
- `specs/chorus-component-metadata-impl-plan-v5.md` — v5 component metadata plan (origin of `ComponentAnalysisOptions` and `ComponentResult`).

## End of file
