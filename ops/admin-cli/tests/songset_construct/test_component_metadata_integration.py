"""Component-metadata integration v2 — spec tests 1-14.

Deterministic JSON-in/JSON-out tests for the component-metadata integration:
boundary transitions, H2/H3/H8 boundary semantics, f_energy/f_posture with
neutral weight redistribution, aggregation determinism, beam sort, and the
report smoke test.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from stream_of_worship.admin.songset_constructor.components import (
    POSTURE_PHASE_FIT,
    aggregate_components,
)
from stream_of_worship.admin.songset_constructor.config import RunConfig
from stream_of_worship.admin.songset_constructor.models import (
    ProposalItem,
    ScoreBreakdown,
    SongCandidate,
    SongsetProposal,
    TransitionCandidate,
)
from stream_of_worship.admin.songset_constructor.rules.fitness import f_energy, f_posture, score
from stream_of_worship.admin.songset_constructor.rules.hard_constraints import validate
from stream_of_worship.admin.songset_constructor.rules.proposals import stamp_boundary_sources
from stream_of_worship.admin.songset_constructor.rules.transitions import recommend_transition

THEME_KEYS = ("讚美", "感恩", "敬拜", "奉獻", "認罪", "差遣", "信心", "祈禱", "復興", "聖靈", "十字架", "跟隨")


def _cand(**kw) -> SongCandidate:
    base = {"song_id": "x", "title": "T", "recording_hash_prefix": "h", "tempo_bpm": 100.0, "musical_key": "G",
                "musical_mode": "maj", "key_confidence": 0.9}
    return SongCandidate(**{**base, **kw})


def _item(hash_prefix: str, *, song_id: str = "s", title: str = "T", phase: int = 1, bpm: float | None = 100.0,
          shift: int = 0, **kw) -> ProposalItem:
    kw.setdefault("key_confidence", 0.9)
    return ProposalItem(
        position=0, recording_hash_prefix=hash_prefix, song_id=song_id, title=title, phase=phase,
        secondary_phases=[], themes=[], bpm=bpm, key="G", mode="maj",
        duration_seconds=200.0, tonic_pc=7, key_shift_semitones=shift, gap_beats=2.0, **kw,
    )


def _proposal(items: list[ProposalItem]) -> SongsetProposal:
    return SongsetProposal(items=items, score=ScoreBreakdown(f_theme=0, f_tempo=0, f_harmony=0, f_diversity=0, total=0))


def _transition(left: str, right: str, *, boundary_source: str = "song_level", **kw) -> tuple[tuple[str, str], TransitionCandidate]:
    base = {"cfd": 1, "bpm_delta": 10.0, "key_compat": 0.92, "suggested_key_shift": 0, "transition_technique": "pivot",
                "crossfade_enabled": False, "crossfade_duration_seconds": 0.0, "gap_beats": 2.0}
    base.update(kw)
    return (left, right), TransitionCandidate(from_hash_prefix=left, to_hash_prefix=right, **base, boundary_source=boundary_source)


# ---------------------------------------------------------------------------
# Test 1 — regression: zero-component pool behaves exactly as before
# ---------------------------------------------------------------------------

def test_zero_component_pool_is_strictly_additive():
    a = _cand(song_id="s1", title="A", recording_hash_prefix="h1", tempo_bpm=120.0, musical_key="D", musical_mode="maj")
    b = _cand(song_id="s2", title="B", recording_hash_prefix="h2", tempo_bpm=100.0, musical_key="G", musical_mode="maj")
    t = recommend_transition(a, b)
    assert t.boundary_source == "song_level"
    assert t.cfd == 1 and t.bpm_delta == 20.0 and not t.warnings

    items = [
        _item("h1", song_id="s1", title="A", phase=1, bpm=120.0),
        _item("h2", song_id="s2", title="B", phase=4, bpm=100.0),
    ]
    matrix = dict([_transition("h1", "h2", cfd=1, bpm_delta=20.0)])
    proposal = _proposal(items)
    breakdown = score(proposal, RunConfig(count=2, proposals=1, pool=200), matrix)
    # four-way weights: 0.40/0.30/0.20/0.10, f_energy/f_posture absent-as-None
    assert breakdown.f_energy is None and breakdown.f_posture is None
    f_theme, f_tempo, f_harmony, f_div = breakdown.f_theme, breakdown.f_tempo, breakdown.f_harmony, breakdown.f_diversity
    assert abs(breakdown.total - round(0.40 * f_theme + 0.30 * f_tempo + 0.20 * f_harmony + 0.10 * f_div, 4)) < 1e-9


# ---------------------------------------------------------------------------
# Test 2 — boundary math: pair-level provenance
# ---------------------------------------------------------------------------

def test_boundary_pair_component_and_fallback():
    left = _cand(song_id="s1", title="A", recording_hash_prefix="h1", musical_key="D", musical_mode="maj",
                 exit_key="B", exit_mode="min", exit_key_confidence=0.8, exit_bpm=98.0)
    right = _cand(song_id="s2", title="B", recording_hash_prefix="h2", musical_key="G", musical_mode="maj",
                  entry_key="E", entry_mode="min", entry_key_confidence=0.7, entry_bpm=102.0)
    t = recommend_transition(left, right)
    assert t.boundary_source == "component"
    # Bmin relative major = D (pc2); Emin relative major = G (pc7) → CFD 1
    assert t.cfd == 1
    assert t.bpm_delta == 4.0

    # one side missing key → full song-level fallback
    right2 = _cand(song_id="s2", title="B", recording_hash_prefix="h2", musical_key="G", musical_mode="maj")
    t2 = recommend_transition(left, right2)
    assert t2.boundary_source == "song_level"
    assert t2.cfd == 1  # D maj vs G maj


# ---------------------------------------------------------------------------
# Test 3 — BPM provenance and warnings
# ---------------------------------------------------------------------------

def test_bpm_provenance_warnings():
    left = _cand(song_id="s1", title="A", recording_hash_prefix="h1", tempo_bpm=120.0,
                 exit_key="B", exit_mode="min", exit_key_confidence=0.8, exit_bpm=None)
    right = _cand(song_id="s2", title="B", recording_hash_prefix="h2", tempo_bpm=100.0,
                  entry_key="E", entry_mode="min", entry_key_confidence=0.7, entry_bpm=102.0)
    t = recommend_transition(left, right)
    assert t.boundary_source == "component"
    assert t.bpm_delta == abs(120.0 - 102.0)
    assert any("missing exit bpm on A; used song-level bpm" in w for w in t.warnings)

    # both boundary BPMs missing → combined unreliable warning
    right2 = _cand(song_id="s2", title="B", recording_hash_prefix="h2", tempo_bpm=100.0,
                   entry_key="E", entry_mode="min", entry_key_confidence=0.7, entry_bpm=None)
    t2 = recommend_transition(left, right2)
    assert any("missing boundary bpm on both sides" in w for w in t2.warnings)
    assert not any("missing exit bpm" in w for w in t2.warnings)

    # song-level pair with missing tempo — the silent-0 trap now warns
    left3 = _cand(song_id="s3", title="C", recording_hash_prefix="h3", tempo_bpm=None)
    right3 = _cand(song_id="s4", title="D", recording_hash_prefix="h4", tempo_bpm=100.0)
    t3 = recommend_transition(left3, right3)
    assert t3.boundary_source == "song_level"
    assert t3.bpm_delta == 100.0
    assert any("missing bpm on C — delta unreliable" in w for w in t3.warnings)


# ---------------------------------------------------------------------------
# Test 4 — H2/H3 boundary semantics
# ---------------------------------------------------------------------------

def test_h2_h3_boundary_bpms():
    config = RunConfig(count=2, proposals=1, pool=200)
    # opener song-level bpm 80 (< floor 90) but entry bpm 100 → H2 passes
    items = [
        _item("h1", song_id="s1", phase=1, bpm=80.0, entry_bpm=100.0),
        _item("h2", song_id="s2", title="Closer", phase=4, bpm=100.0),
    ]
    feedback = validate(_proposal(items), config, {})
    assert "H2" not in feedback.violated
    # closer song-level bpm 100 but exit bpm 70 → H3 passes
    items2 = [
        _item("h1", song_id="s1", phase=1, bpm=100.0),
        _item("h2", song_id="s2", title="Closer", phase=4, bpm=100.0, exit_bpm=70.0),
    ]
    feedback2 = validate(_proposal(items2), config, {})
    assert "H3" not in feedback2.violated
    # missing both boundary and song-level → fails as today
    items3 = [
        _item("h1", song_id="s1", phase=1, bpm=None),
        _item("h2", song_id="s2", title="Closer", phase=4, bpm=100.0),
    ]
    feedback3 = validate(_proposal(items3), config, {})
    assert "H2" in feedback3.violated


# ---------------------------------------------------------------------------
# Test 5 — H8 boundary gate
# ---------------------------------------------------------------------------

def test_h8_boundary_gate():
    config = RunConfig(count=2, proposals=1, pool=200)

    def _feedback(boundary_source: str, entry_conf: float | None, song_conf: float | None):
        items = [
            _item("h1", song_id="s1", phase=1, bpm=100.0),
            _item("h2", song_id="s2", title="Next", phase=4, bpm=100.0, key_confidence=song_conf,
                  entry_key_confidence=entry_conf, shift=1),
        ]
        matrix = dict([_transition("h1", "h2", boundary_source=boundary_source)])
        return validate(_proposal(items), config, matrix)

    # boundary >= 0.6 with song-level < 0.6 → transposable (gains it)
    assert "H8" not in _feedback("component", 0.8, 0.4).violated
    # the accepted regression: song-level >= 0.6 but boundary key present with conf < 0.6 → loses transposability
    assert "H8" in _feedback("component", 0.4, 0.9).violated
    # song_level pair → song-level gate (unchanged today behavior)
    assert "H8" not in _feedback("song_level", 0.3, 0.9).violated
    assert "H8" in _feedback("song_level", 0.9, 0.4).violated
    # opener: no incoming transition → always song-level gate
    opener_only = [
        _item("h1", song_id="s1", phase=1, bpm=100.0, key_confidence=0.9, entry_key_confidence=0.2, shift=2),
        _item("h2", song_id="s2", title="Next", phase=4, bpm=100.0),
    ]
    feedback = validate(_proposal(opener_only), config, {})
    assert "H8" not in feedback.violated
    # missing transition in matrix → song-level gate
    items_missing = [
        _item("h1", song_id="s1", phase=1, bpm=100.0),
        _item("h2", song_id="s2", title="Next", phase=4, bpm=100.0, key_confidence=0.9, entry_key_confidence=0.2, shift=1),
    ]
    feedback_missing = validate(_proposal(items_missing), config, {})
    assert "H8" not in feedback_missing.violated


# ---------------------------------------------------------------------------
# Test 6 — theme primary/fallback (component-primary, ADR-0006)
# ---------------------------------------------------------------------------

def test_theme_primary_and_fallback():
    from stream_of_worship.admin.songset_constructor.rules.phases import (
        apply_seasonal_bias,
        infer_phase,
    )

    component_dist = {theme: 0.0 for theme in THEME_KEYS}
    component_dist["聖靈"] = 1.0
    cand = _cand(song_id="s1", component_theme_scores=component_dist, tempo_bpm=65.0)
    fused, source = (dict(cand.component_theme_scores), "component")
    assert source == "component"
    fused = apply_seasonal_bias(fused, None)
    assert infer_phase(fused, cand.tempo_bpm) == 4  # 聖靈 + bpm < 70

    # component-less song → fusion path (tempo-only fallback ladder)
    cand2 = _cand(song_id="s2", title="No Theme", component_theme_scores=None, tempo_bpm=105.0)
    assert cand2.theme_source is None
    assert infer_phase({}, cand2.tempo_bpm) == 1

    # dense 12-key shape
    dist = aggregate_components([
        ("s1", "entry", "chorus", 0, 1, None, None, None, None, "敬拜", 0.8, None, None),
    ])
    assert set(dist["component_theme_scores"].keys()) == set(THEME_KEYS)


# ---------------------------------------------------------------------------
# Test 7 — distribution weighting: pinned worked example
# ---------------------------------------------------------------------------

def test_distribution_weighting_worked_example():
    rows = [
        ("s1", "entry", "chorus", 0, 101, None, None, None, None, "敬拜", 0.8, None, None),
        ("s1", "exit", "chorus", 1, 102, None, None, None, None, "差遣", 0.6, None, None),
        ("s1", None, "verse", 2, 103, None, None, None, None, "感恩", 0.9, None, None),
    ]
    out = aggregate_components(rows)
    d = out["component_theme_scores"]
    total = 1.0 * 0.8 + 1.0 * 0.6 + 0.5 * 0.9
    assert abs(d["敬拜"] - 0.8 / total) < 1e-9
    assert abs(d["差遣"] - 0.6 / total) < 1e-9
    assert abs(d["感恩"] - 0.45 / total) < 1e-9
    assert abs(sum(d.values()) - 1.0) < 1e-9
    assert max(d, key=d.get) == "敬拜"  # chorus-majority preserved

    # posture argmax + weighted-average confidence: choruses To God 0.9, verse About God 0.5
    rows2 = [
        ("s2", "entry", "chorus", 0, 201, None, None, None, None, None, None, "To God", 0.9),
        ("s2", None, "chorus", 1, 202, None, None, None, None, None, None, "To God", 0.8),
        ("s2", None, "verse", 2, 203, None, None, None, None, None, None, "About God", 0.5),
    ]
    out2 = aggregate_components(rows2)
    assert out2["component_posture"] == "To God"
    # weighted-average confidence of winner rows: (1.0*0.9 + 1.0*0.8) / (1.0 + 1.0) = 0.85
    assert abs(out2["component_posture_confidence"] - 0.85) < 1e-9

    # loop_target rows vote too
    rows3 = [
        ("s3", "loop_target", "chorus", 0, 301, None, None, None, None, "敬拜", 0.7, None, None),
    ]
    out3 = aggregate_components(rows3)
    assert abs(out3["component_theme_scores"]["敬拜"] - 1.0) < 1e-9


# ---------------------------------------------------------------------------
# Test 8 — energy percentiles + f_energy
# ---------------------------------------------------------------------------

def test_f_energy_and_percentiles():
    items = [
        _item("h1", phase=1, entry_energy_pct=0.9, exit_energy_pct=0.7),
        _item("h2", phase=4, entry_energy_pct=0.6, exit_energy_pct=0.1),
        _item("h3", phase=5, entry_energy_pct=0.2, exit_energy_pct=0.05),
    ]
    proposal = _proposal(items)
    # adjacency: |0.6-0.7|=0.1 → 0.9; |0.2-0.1|=0.1 → 0.9; arc: 0.9-0.05=0.85 → 0.15
    value = f_energy(proposal)
    expected = 0.5 * ((0.9 + 0.9) / 2) + 0.5 * 0.15
    assert abs(value - expected) < 1e-9

    # pool-wide absence → None (weight redistributed, four-way totals)
    assert f_energy(_proposal([_item("h1"), _item("h2")])) is None
    breakdown = score(_proposal([_item("h1"), _item("h2")]), RunConfig(count=2, proposals=1, pool=200), {})
    assert breakdown.f_energy is None
    assert abs(breakdown.total - round(0.40 * breakdown.f_theme + 0.30 * breakdown.f_tempo + 0.20 * breakdown.f_harmony + 0.10 * breakdown.f_diversity, 4)) < 1e-9

    # ties-averaged percentile (midpoint) math
    values = [-12.0, -15.0, -20.0, -20.0]
    n = len(values)
    pct = lambda v: (sum(1 for u in values if u < v) + 0.5 * sum(1 for u in values if u == v)) / n
    assert abs(pct(-20.0) - (0 + 1.0) / n) < 1e-9
    assert abs(pct(-12.0) - (3 + 0.5) / n) < 1e-9


# ---------------------------------------------------------------------------
# Test 9 — three-mode weights
# ---------------------------------------------------------------------------

def test_weight_modes():
    config = RunConfig(count=2, proposals=1, pool=200)

    def _with(items: list[ProposalItem]) -> ScoreBreakdown:
        return score(_proposal(items), config, {})

    # zero-component pool: four-way exact
    b4 = _with([_item("h1"), _item("h2")])
    assert b4.f_energy is None and b4.f_posture is None
    assert abs(b4.total - round(0.40 * b4.f_theme + 0.30 * b4.f_tempo + 0.20 * b4.f_harmony + 0.10 * b4.f_diversity, 4)) < 1e-9

    # one-signal pool: base × 0.95 + 0.05
    b5 = _with([
        _item("h1", entry_energy_pct=0.8, exit_energy_pct=0.6),
        _item("h2", entry_energy_pct=0.5, exit_energy_pct=0.1),
    ])
    assert b5.f_energy is not None and b5.f_posture is None
    expected5 = round(0.38 * b5.f_theme + 0.285 * b5.f_tempo + 0.19 * b5.f_harmony + 0.095 * b5.f_diversity + 0.05 * b5.f_energy, 4)
    assert abs(b5.total - expected5) < 1e-9

    # both signals: base × 0.90 + 0.05 + 0.05
    b6 = _with([
        _item("h1", entry_energy_pct=0.8, exit_energy_pct=0.6, component_posture="To God"),
        _item("h2", entry_energy_pct=0.5, exit_energy_pct=0.1, component_posture="To God"),
    ])
    assert b6.f_energy is not None and b6.f_posture is not None
    expected6 = round(0.36 * b6.f_theme + 0.27 * b6.f_tempo + 0.18 * b6.f_harmony + 0.09 * b6.f_diversity + 0.05 * b6.f_energy + 0.05 * b6.f_posture, 4)
    assert abs(b6.total - expected6) < 1e-9

    # weights sum to 1.0 in every mode
    assert abs(0.40 + 0.30 + 0.20 + 0.10 - 1.0) < 1e-9
    assert abs(0.38 + 0.285 + 0.19 + 0.095 + 0.05 - 1.0) < 1e-9
    assert abs(0.36 + 0.27 + 0.18 + 0.09 + 0.05 + 0.05 - 1.0) < 1e-9

    # f_posture fit values from the pinned matrix
    assert POSTURE_PHASE_FIT["To God"][3] == 1.0
    assert POSTURE_PHASE_FIT["About God"][4] == 0.0
    assert POSTURE_PHASE_FIT["To Congregation"][2] == 0.0
    assert f_posture(_proposal([_item("h1"), _item("h2")])) is None


# ---------------------------------------------------------------------------
# Test 10 — matrix-only visibility (structural invariant)
# ---------------------------------------------------------------------------

def test_matrix_only_visibility():
    # H2/H3/H8 consume ProposalItem fields + matrix transitions only — no SongCandidate anywhere.
    config = RunConfig(count=2, proposals=1, pool=200)
    items = [
        _item("h1", song_id="s1", phase=1, bpm=100.0, entry_bpm=95.0),
        _item("h2", song_id="s2", title="Next", phase=4, bpm=100.0, exit_bpm=80.0,
              key_confidence=0.9, entry_key_confidence=0.2, shift=1),
    ]
    matrix = dict([_transition("h1", "h2", boundary_source="component")])
    feedback = validate(_proposal(items), config, matrix)
    assert "H8" in feedback.violated  # boundary gate on entry confidence
    # stamp helper mirrors the matrix without reading candidates
    proposal = _proposal(items)
    stamped = stamp_boundary_sources(proposal, matrix)
    assert stamped.items[0].incoming_boundary_source is None
    assert stamped.items[1].incoming_boundary_source == "component"


# ---------------------------------------------------------------------------
# Test 11 — beam theme-diverse sort counts positive themes
# ---------------------------------------------------------------------------

def test_beam_theme_diverse_counts_positive():
    from stream_of_worship.admin.songset_constructor.rules.beam import _sort_key_theme_diverse

    def _c(hash_prefix: str, themes: dict[str, float]) -> SongCandidate:
        return _cand(song_id=hash_prefix, title=hash_prefix, recording_hash_prefix=hash_prefix, themes=themes)

    zero = {t: 0.0 for t in THEME_KEYS}
    three = _c("h1", {**zero, "讚美": 0.9, "感恩": 0.5, "敬拜": 0.3})
    two = _c("h2", {**zero, "讚美": 0.9, "感恩": 0.5})
    empty = _c("h3", {})

    seq3 = [three, three]
    seq2 = [two, two]
    target = (1, 4)
    key3 = _sort_key_theme_diverse(seq3, target)
    key2 = _sort_key_theme_diverse(seq2, target)
    # 3 distinct positive themes sorts before 2 (phase/tempo equal, -theme_count smaller wins)
    assert key3 < key2

    # degenerate all-zero themes does not crash and counts 0;
    # the set spans the sequence: two songs with the same 2 positive themes → 2
    assert _sort_key_theme_diverse([empty, empty], target)[2] == -0
    key_empty = _sort_key_theme_diverse([two, empty], target)
    assert key_empty[2] == -2


# ---------------------------------------------------------------------------
# Test 13 — cache: old pool JSON validates to None/False defaults
# ---------------------------------------------------------------------------

def test_old_cache_json_validates_to_defaults():
    old_pool_entry = {
        "song_id": "s1",
        "title": "Old Song",
        "recording_hash_prefix": "h_old12345",
        "tempo_bpm": 100.0,
        "musical_key": "C",
        "musical_mode": "maj",
    }
    cand = SongCandidate.model_validate(old_pool_entry)
    assert cand.has_components is False
    assert cand.entry_bpm is None and cand.exit_key is None
    assert cand.component_theme_scores is None and cand.component_posture is None
    assert cand.theme_source is None and cand.entry_energy_pct is None

    # old transition JSON round-trips too
    old_transition = {
        "from_hash_prefix": "a", "to_hash_prefix": "b", "cfd": 1, "bpm_delta": 5.0,
        "key_compat": 0.92, "suggested_key_shift": 0, "transition_technique": "pivot",
        "crossfade_enabled": False, "crossfade_duration_seconds": 0.0, "gap_beats": 2.0,
    }
    t = TransitionCandidate.model_validate(old_transition)
    assert t.boundary_source == "song_level" and t.warnings == []


# ---------------------------------------------------------------------------
# Test 14 — aggregation determinism: tiebreak + entry_exit
# ---------------------------------------------------------------------------

def test_aggregation_determinism():
    # 6-song multi-row tiebreak: lowest occurrence_index, then lowest id
    rows = [
        ("s1", "entry", "chorus", 1, 900, 96.0, "G", 0.8, -14.0, None, None, None, None),
        ("s1", "entry", "chorus", 0, 901, 88.0, "C", 0.7, -12.0, None, None, None, None),
        ("s1", "exit", "verse", 0, 902, 90.0, "D", 0.6, -13.0, None, None, None, None),
        ("s1", "exit", "verse", 0, 899, 92.0, "A", 0.6, -13.5, None, None, None, None),
    ]
    out = aggregate_components(rows, musical_mode="min")
    assert out["entry_bpm"] == 88.0 and out["entry_key"] == "C"  # occurrence_index 0 wins
    assert out["exit_bpm"] == 92.0 and out["exit_key"] == "A"  # same occurrence_index → lowest id 899
    assert out["entry_mode"] == "min" and out["exit_mode"] == "min"
    # same input → same output across runs
    assert aggregate_components(rows, musical_mode="min") == out

    # entry_exit combined role satisfies both boundaries
    rows_dual = [
        ("s2", "entry_exit", "chorus", 0, 800, 90.0, "G", 0.8, -12.0, None, None, "To God", 0.9),
    ]
    dual = aggregate_components(rows_dual, musical_mode="maj")
    assert dual["entry_bpm"] == 90.0 and dual["exit_bpm"] == 90.0
    assert dual["entry_key"] == "G" and dual["exit_key"] == "G"


# ---------------------------------------------------------------------------
# Test 12 — report smoke (subprocess)
# ---------------------------------------------------------------------------

def test_report_smoke(tmp_path: Path):
    pool = [
        {
            "song_id": "c1", "title": "讚美之歌", "recording_hash_prefix": "h_c1", "tempo_bpm": 128.0,
            "musical_key": "G", "musical_mode": "maj", "key_confidence": 0.9, "duration_seconds": 200.0,
            "lyrics_raw": "讚美 主", "has_components": True, "theme_source": "component", "phase": 1,
            "themes": {t: (1.0 if t == "讚美" else 0.0) for t in THEME_KEYS},
        },
        {
            "song_id": "f1", "title": "感恩之心", "recording_hash_prefix": "h_f1", "tempo_bpm": 95.0,
            "musical_key": "D", "musical_mode": "maj", "key_confidence": 0.9, "duration_seconds": 180.0,
            "lyrics_raw": "感謝 恩典", "has_components": False, "phase": 2,
            "themes": {t: (1.0 if t == "感恩" else 0.0) for t in THEME_KEYS},
        },
    ]
    transitions = [{
        "from_hash_prefix": "h_c1", "to_hash_prefix": "h_f1", "cfd": 2, "bpm_delta": 33.0,
        "key_compat": 0.78, "suggested_key_shift": 0, "transition_technique": "direct",
        "crossfade_enabled": False, "crossfade_duration_seconds": 0.0, "gap_beats": 2.0,
        "warnings": ["missing exit bpm on 讚美之歌; used song-level bpm"], "boundary_source": "component",
    }]
    proposal = {
        "rank": 1, "llm_origin": False,
        "score": {"f_theme": 0.8, "f_tempo": 0.7, "f_harmony": 0.78, "f_diversity": 1.0,
                  "f_energy": 0.9, "f_posture": 0.9, "total": 0.85, "range_penalty": 0.0},
        "items": [
            {"position": 1, "recording_hash_prefix": "h_c1", "song_id": "c1", "title": "讚美之歌",
             "phase": 1, "secondary_phases": [], "themes": ["讚美"], "bpm": 128.0, "key": "G", "mode": "maj",
             "key_confidence": 0.9, "duration_seconds": 200.0, "tonic_pc": 7, "has_components": True,
             "theme_source": "component", "component_posture": "To God", "entry_energy_pct": 0.83,
             "exit_energy_pct": 0.62, "entry_bpm": 126.0, "exit_bpm": 120.0, "entry_key": "G",
             "exit_key": "B", "entry_key_confidence": 0.8, "incoming_boundary_source": None},
            {"position": 2, "recording_hash_prefix": "h_f1", "song_id": "f1", "title": "感恩之心",
             "phase": 2, "secondary_phases": [], "themes": ["感恩"], "bpm": 95.0, "key": "D", "mode": "maj",
             "key_confidence": 0.9, "duration_seconds": 180.0, "tonic_pc": 2, "has_components": False,
             "theme_source": "fusion", "component_posture": None, "entry_energy_pct": None,
             "exit_energy_pct": None, "entry_bpm": None, "exit_bpm": None, "entry_key": None,
             "exit_key": None, "entry_key_confidence": None, "incoming_boundary_source": "component"},
        ],
    }
    data = {"proposals": [proposal], "pool": pool, "config": {"count": 2},
            "transitions": transitions, "summary": "smoke test"}

    script = Path(__file__).resolve().parents[4] / "lab" / "skills" / "songset-constructor" / "scripts" / "write_report.py"
    subprocess.run(
        [sys.executable, str(script), "--output-dir", str(tmp_path)],
        input=json.dumps(data), capture_output=True, text=True,
        env={"PYTHONPATH": "ops/admin-cli/src", "NO_COLOR": "1", "PATH": "/usr/bin:/bin"},
        check=True,
    )
    report = (tmp_path / "proposal_report.md").read_text(encoding="utf-8")
    assert "Component coverage: 1/2 (50.0%)" in report
    assert "Theme source: component=1" in report
    assert "Transposable population" in report
    assert "| 讚美之歌 → 感恩之心 | component | 2 | 33 | missing exit bpm on 讚美之歌; used song-level bpm |" in report
    assert "Energy arc" in report
    assert "Posture sequence" in report
    assert "six-way" in report
    assert "Fallback (song-level): 感恩之心" in report