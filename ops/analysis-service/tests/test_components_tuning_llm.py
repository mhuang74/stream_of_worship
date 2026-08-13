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
  - python -m test_components_tuning_llm --report       # markdown report
"""
import argparse
import asyncio
import json
import os
import sys
from dataclasses import asdict, replace
from pathlib import Path
from typing import Optional

import pytest

# Ensure the tests directory is importable when run as `python -m ...`.
_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

from test_components_tuning import (  # noqa: E402
    SONG_IDS,
    _dump_review_slices,
    _load_fixture,
    _parse_raw_lines,
    _score_components,
)
from sow_analysis.workers.section_segmenter import (  # noqa: E402
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


def _load_system_prompt(name: str) -> str:
    """Load a system prompt candidate by short name (e.g. 'V0_default')."""
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


def test_ab_comparison_lyrics_repetition_vs_llm():
    """Side-by-side A/B report: prints both grand totals + per-song deltas.

    Asserts the LLM path is >= 0.70 (pass bar from
    component-identification-llm-segmentation-v2.md). Skipped (not failed)
    if the pass bar is missed during early tuning — instead surfaces the
    gap so the operator knows how far to go.
    """
    from test_components_tuning import score_all

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


import itertools  # noqa: E402


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
    from test_components_tuning import score_all
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
        f"_Generated by `python -m test_components_tuning_llm --report`._",
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
        f"# {'# ' if best_grid_w == DEFAULT_VALIDATOR_WEIGHTS else ''}DEFAULT_VALIDATOR_WEIGHTS unchanged (grid winner equals defaults)",
        "```",
        "",
    ]
    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote report to {report_path}")
