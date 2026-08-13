"""Tuning harness for identify_from_lyrics_repetition.

Loads committed LRC + ground-truth fixtures under
eval/components_tuning/<song_id>/, runs the algorithm, scores the result
via time-range IoU, and writes per-component lyrics slices to
eval/components_tuning/review/<song_id>/ for manual review.

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
    ESSENTIAL_ROLES,
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


def _load_fixture(song_id: str) -> tuple[str, dict]:
    song_dir = FIXTURES / song_id
    lrc_path = song_dir / "lrc.lrc"
    gt_path = song_dir / "ground_truth.json"
    assert lrc_path.exists(), (
        f"Missing LRC fixture: {lrc_path}. Fetch the LRC from R2 "
        f"({song_id} hash12/lyrics.lrc) and commit it here."
    )
    assert gt_path.exists(), f"Missing ground truth: {gt_path}"
    lrc_content = lrc_path.read_text(encoding="utf-8")
    gt = json.loads(gt_path.read_text(encoding="utf-8"))
    return lrc_content, gt


def _parse_raw_lines(lrc_content: str) -> list:
    lrc_file = parse_lrc(lrc_content)
    return list(lrc_file.lines)   # NO non-empty filtering — raw lines, blanks included


def _expected_time_range(lines, line_start: int, line_end: int) -> tuple[float, float]:
    # 1-based INCLUSIVE [line_start, line_end] -> 0-based internal indices.
    if line_start > line_end:
        line_start, line_end = line_end, line_start   # normalize ordering
    n = len(lines)
    assert 1 <= line_start <= line_end <= n, (
        f"Bad 1-based inclusive line range [{line_start}, {line_end}] for {n}-line LRC"
    )
    start_t = lines[line_start - 1].time_seconds
    # Mirror identify_from_lyrics_repetition's end-time derivation: the end
    # is the start of the line AFTER the block (0-based index `line_end`).
    if line_end < n:
        end_t = lines[line_end].time_seconds
    else:
        # Estimate via average line duration in the block.
        durations = [
            lines[k + 1].time_seconds - lines[k].time_seconds
            for k in range(line_start - 1, min(line_end, n - 1))
        ]
        avg = (sum(durations) / len(durations)) if durations else 4.0
        end_t = lines[min(line_end - 1, n - 1)].time_seconds + avg
    return start_t, end_t


def _iou(a_start, a_end, b_start, b_end) -> float:
    inter = max(0.0, min(a_end, b_end) - max(a_start, b_start))
    union = max(a_end, b_end) - min(a_start, b_start)
    return inter / union if union > 0 else 0.0


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


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def _score_song(lrc_content, ground_truth, weights, song_total_duration=None) -> dict:
    # Use the LRC file's last-line timestamp as song_total_duration (or None
    # if the LRC has < 2 lines).
    raw = _parse_raw_lines(lrc_content)
    if song_total_duration is None:
        song_total_duration = raw[-1].time_seconds if len(raw) >= 2 else None

    components = identify_from_lyrics_repetition(
        lrc_content, song_total_duration=song_total_duration, weights=weights
    )
    # Ground truth only specifies ESSENTIAL components (entry/exit chorus,
    # loop_target verse). Filter predicted components to essential roles
    # before comparison so non-essential rows (role="none") don't count as
    # spurious extra occurrences.
    choruses = [
        c for c in components
        if c.component_type == "chorus" and c.role in ESSENTIAL_ROLES
    ]
    verses = [c for c in components if c.component_type == "verse"]

    per_component = []

    # Step 3: expected chorus occurrences.
    for occ in ground_truth["expected_chorus_occurrences"]:
        e_start, e_end = _expected_time_range(raw, occ["line_start"], occ["line_end"])
        expected_role = occ["role"]
        best_iou = 0.0
        matched_pred_role = None
        for c in choruses:
            iou = _iou(e_start, e_end, c.start_time, c.end_time)
            if iou > best_iou:
                best_iou = iou
                matched_pred_role = c.role
        role_bonus = 0.0
        role_penalty = 0.0
        if expected_role == "entry_exit":
            # Single-chorus case: algorithm emits two rows (entry + exit).
            pred_roles = {c.role for c in choruses}
            if "entry" in pred_roles and "exit" in pred_roles:
                role_bonus = ROLE_BONUS
            else:
                role_penalty = ROLE_MISMATCH_PENALTY
        else:
            if matched_pred_role == expected_role:
                role_bonus = ROLE_BONUS
            else:
                role_penalty = ROLE_MISMATCH_PENALTY
        component_score = _clamp(best_iou + role_bonus - role_penalty)
        per_component.append(
            {
                "expected_role": expected_role,
                "occurrence_index": occ["occurrence_index"],
                "best_iou": best_iou,
                "matched_pred_role": matched_pred_role,
                "role_bonus": role_bonus,
                "role_penalty": role_penalty,
                "component_score": component_score,
            }
        )

    # Step 4: expected verse.
    if ground_truth.get("expected_verse"):
        v = ground_truth["expected_verse"]
        e_start, e_end = _expected_time_range(raw, v["line_start"], v["line_end"])
        best_iou = 0.0
        for c in verses:
            best_iou = max(best_iou, _iou(e_start, e_end, c.start_time, c.end_time))
        component_score = _clamp(best_iou + ROLE_BONUS)
        per_component.append(
            {
                "expected_role": "loop_target",
                "occurrence_index": v["occurrence_index"],
                "best_iou": best_iou,
                "matched_pred_role": "loop_target" if verses else None,
                "role_bonus": ROLE_BONUS if verses else 0.0,
                "role_penalty": 0.0,
                "component_score": component_score,
            }
        )

    # Step 5: occurrence-count check.
    n_pred_choruses = len(choruses)
    n_expected_choruses = len(ground_truth["expected_chorus_occurrences"])
    if n_pred_choruses != n_expected_choruses:
        diff = abs(n_pred_choruses - n_expected_choruses)
        for _ in range(diff):
            per_component.append(
                {
                    "expected_role": "chorus",
                    "occurrence_index": None,
                    "best_iou": 0.0,
                    "matched_pred_role": None,
                    "role_bonus": 0.0,
                    "role_penalty": 0.0,
                    "component_score": 0.0,
                }
            )

    # Step 6: false-positive check.
    false_positive_hits = []
    for fp in ground_truth.get("false_positive_avoid", []):
        e_start, e_end = _expected_time_range(raw, fp["line_start"], fp["line_end"])
        max_iou = 0.0
        for c in choruses:
            max_iou = max(max_iou, _iou(e_start, e_end, c.start_time, c.end_time))
        if max_iou >= FP_PENALTY_IOU_THRESHOLD:
            false_positive_hits.append({"label": fp.get("label", "?"), "max_iou": max_iou})

    # Step 7: per-song mean.
    if per_component:
        per_song_mean = sum(pc["component_score"] for pc in per_component) / len(per_component)
    else:
        per_song_mean = 0.0
    per_song_mean -= FP_PENALTY * len(false_positive_hits)
    per_song_mean = _clamp(per_song_mean)

    return {
        "per_component": per_component,
        "verse_iou": next(
            (pc["best_iou"] for pc in per_component if pc["expected_role"] == "loop_target"),
            None,
        ),
        "false_positive_hits": false_positive_hits,
        "per_song_mean": per_song_mean,
        "n_predicted_choruses": n_pred_choruses,
        "n_expected_choruses": n_expected_choruses,
        "n_predicted_verses": len(verses),
    }


def score_all(weights: LyricsRepetitionWeights = DEFAULT_WEIGHTS):
    per_song = []
    for song_id in SONG_IDS:
        lrc, gt = _load_fixture(song_id)
        raw = _parse_raw_lines(lrc)
        song_total_duration = raw[-1].time_seconds if len(raw) >= 2 else None
        result = _score_song(lrc, gt, weights, song_total_duration=song_total_duration)
        # Always dump review slices (even on failure paths) for manual inspection.
        # Use the SAME song_total_duration as the scorer so the dumped slices
        # reflect exactly what was scored.
        components = identify_from_lyrics_repetition(
            lrc, song_total_duration=song_total_duration, weights=weights
        )
        _dump_review_slices(song_id, lrc, components)
        per_song.append((song_id, result))
    grand_total = sum(r["per_song_mean"] for _, r in per_song) / len(per_song)
    return grand_total, per_song


@pytest.mark.parametrize("song_id", SONG_IDS)
def test_fixture_completeness(song_id):
    """FAIL fast if any fixture is still a stub or missing LRC."""
    lrc, gt = _load_fixture(song_id)
    raw = _parse_raw_lines(lrc)
    assert len(raw) >= 4, f"{song_id}: LRC has too few lines"
    # Validate every line range is in-bounds (1-based INCLUSIVE).
    for occ in gt["expected_chorus_occurrences"]:
        assert 1 <= occ["line_start"] <= occ["line_end"] <= len(raw), (
            f"{song_id}: chorus occ {occ['occurrence_index']} "
            f"range [{occ['line_start']},{occ['line_end']}] OOB"
        )
    if gt.get("expected_verse"):
        v = gt["expected_verse"]
        assert 1 <= v["line_start"] <= v["line_end"] <= len(raw), (
            f"{song_id}: verse range [{v['line_start']},{v['line_end']}] OOB"
        )
    for fp in gt.get("false_positive_avoid", []):
        assert 1 <= fp["line_start"] <= fp["line_end"] <= len(raw), (
            f"{song_id}: fp range [{fp['line_start']},{fp['line_end']}] OOB"
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
    """Grid-search over the 4 multi-cue weight knobs (refined around winner).
    Tunable axes (kept narrow to avoid overfitting on 3 songs):
      - repeat_count_cap in {3, 4, 5}
      - position_weight_early in {0.6, 0.8, 1.0}
      - length_weight_other in {0.8, 1.0, 1.2}
      - content_weight_keyword_present in {1.0, 1.2}
    All other fields stay at DEFAULT_WEIGHTS values.
    Total combinations: 3*3*3*2 = 54.
    """
    import itertools

    def _axes(self):
        return self.itertools.product(
            [3, 4, 5],                  # repeat_count_cap
            [0.6, 0.8, 1.0],            # position_weight_early
            [0.8, 1.0, 1.2],            # length_weight_other
            [1.0, 1.2],                 # content_weight_keyword_present
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


if __name__ == "__main__":
    grand_total, per_song = score_all(DEFAULT_WEIGHTS)
    print(f"DEFAULT total = {grand_total:.3f}")
    for song_id, r in per_song:
        print(f"  {song_id}: {r['per_song_mean']:.3f}")
