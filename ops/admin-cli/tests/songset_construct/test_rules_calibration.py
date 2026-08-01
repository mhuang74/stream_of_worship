"""Tests for calibrated H1/H4/H5 constraint defaults and cache-first behavior."""

from __future__ import annotations

from stream_of_worship.admin.songset_constructor.config import RunConfig
from stream_of_worship.admin.songset_constructor.models import (
    ProposalItem,
    ScoreBreakdown,
    SongsetProposal,
    TransitionCandidate,
)
from stream_of_worship.admin.songset_constructor.rules.hard_constraints import validate


def _make_item(
    hash_prefix: str,
    *,
    song_id: str = "s1",
    title: str = "Song",
    phase: int = 1,
    secondary_phases: list[int] | None = None,
    bpm: float = 100.0,
    key_shift: int = 0,
    gap_beats: float = 2.0,
    crossfade: float = 0.0,
    key_confidence: float | None = 0.9,
) -> ProposalItem:
    return ProposalItem(
        position=0,
        recording_hash_prefix=hash_prefix,
        song_id=song_id,
        title=title,
        phase=phase,
        secondary_phases=secondary_phases or [],
        bpm=bpm,
        key_shift_semitones=key_shift,
        gap_beats=gap_beats,
        crossfade_duration_seconds=crossfade,
        key_confidence=key_confidence,
    )


def _make_proposal(items: list[ProposalItem]) -> SongsetProposal:
    return SongsetProposal(
        items=items,
        score=ScoreBreakdown(f_theme=0, f_tempo=0, f_harmony=0, f_diversity=0, total=0),
    )


def _make_transition(
    left: str,
    right: str,
    *,
    cfd: int = 1,
    bpm_delta: float = 10.0,
    gap_beats: float = 2.0,
    crossfade: float = 0.0,
    key_shift: int = 0,
) -> tuple[tuple[str, str], TransitionCandidate]:
    return (left, right), TransitionCandidate(
        from_hash_prefix=left,
        to_hash_prefix=right,
        cfd=cfd,
        bpm_delta=bpm_delta,
        key_compat=0.8,
        suggested_key_shift=key_shift,
        transition_technique="direct",
        crossfade_enabled=crossfade > 0,
        crossfade_duration_seconds=crossfade,
        gap_beats=gap_beats,
    )


# ---------------------------------------------------------------------------
# Config defaults
# ---------------------------------------------------------------------------

def test_config_h4_h5_defaults_match_catalog_reality():
    assert RunConfig(count=3, proposals=3, pool=200).h4_limit == 45
    assert RunConfig(count=3, proposals=3, pool=200, relax_h4=True).h4_limit == 55
    assert RunConfig(count=3, proposals=3, pool=200).h5_limit == 3
    assert RunConfig(count=3, proposals=3, pool=200, relax_h5=True).h5_limit == 4


def test_config_h4_no_crossfade_limit():
    config = RunConfig(count=3, proposals=3, pool=200)
    assert config.h4_no_crossfade_limit == 40
    relaxed = RunConfig(count=3, proposals=3, pool=200, relax_h4=True)
    assert relaxed.h4_no_crossfade_limit == 40  # min(40, 55) = 40


def test_config_to_dict_includes_new_fields():
    d = RunConfig(count=3, proposals=3, pool=200).to_dict()
    assert d["h4_strict"] == 45
    assert d["h4_no_crossfade"] == 40
    assert d["h5_strict"] == 3


def test_config_child_inherits_calibrated_defaults():
    parent = RunConfig(count=3, proposals=3, pool=200)
    child = RunConfig(**parent.to_dict())
    assert child.h4_strict == 45
    assert child.h4_no_crossfade == 40
    assert child.h5_strict == 3
    assert child.h4_limit == 45
    assert child.h5_limit == 3


# ---------------------------------------------------------------------------
# H1 — primary-only opener count
# ---------------------------------------------------------------------------

def test_h1_strict_counts_primary_phase_only_not_secondary():
    """A closer with secondary_phases=[1,3] should NOT be counted as a phase-1 opener."""
    config = RunConfig(count=3, proposals=3, pool=200)
    items = [
        _make_item("h1", song_id="s1", phase=1, bpm=100),
        _make_item("h2", song_id="s2", phase=3, bpm=85),
        _make_item("h3", song_id="s3", phase=5, secondary_phases=[1, 3], bpm=70),
    ]
    proposal = _make_proposal(items)
    matrix: dict[tuple[str, str], TransitionCandidate] = {}
    feedback = validate(proposal, config, matrix)
    assert "H1" not in feedback.violated, f"H1 should pass but got: {feedback.errors}"


def test_h1_strict_fails_when_no_primary_phase1_opener():
    """Proposal with no primary phase-1 song should fail strict H1."""
    config = RunConfig(count=3, proposals=3, pool=200)
    items = [
        _make_item("h1", song_id="s1", phase=2, bpm=100),
        _make_item("h2", song_id="s2", phase=3, bpm=85),
        _make_item("h3", song_id="s3", phase=5, bpm=70),
    ]
    proposal = _make_proposal(items)
    matrix: dict[tuple[str, str], TransitionCandidate] = {}
    feedback = validate(proposal, config, matrix)
    assert "H1" in feedback.violated


def test_h1_relaxed_drops_phase1_requirement():
    """With relax_h1, only the phase 4/5 closer requirement remains."""
    config = RunConfig(count=3, proposals=3, pool=200)
    items = [
        _make_item("h1", song_id="s1", phase=2, bpm=100),
        _make_item("h2", song_id="s2", phase=3, bpm=85),
        _make_item("h3", song_id="s3", phase=5, bpm=70),
    ]
    proposal = _make_proposal(items)
    matrix: dict[tuple[str, str], TransitionCandidate] = {}
    feedback = validate(proposal, config, matrix, relax_h1=True)
    assert "H1" not in feedback.violated


# ---------------------------------------------------------------------------
# H4 — calibrated BPM caps
# ---------------------------------------------------------------------------

def test_h4_default_45_strict_applied():
    """Non-crossfade cap is 40; crossfade-tier cap is 45."""
    config = RunConfig(count=3, proposals=3, pool=200)

    # bpm_delta=40, gap_beats=0 (no crossfade) → passes (≤ 40)
    items = [
        _make_item("h1", song_id="s1", phase=1, bpm=100, gap_beats=0.0),
        _make_item("h2", song_id="s2", phase=3, bpm=60, gap_beats=0.0),
        _make_item("h3", song_id="s3", phase=5, bpm=60, gap_beats=0.0),
    ]
    matrix = dict([
        _make_transition("h1", "h2", bpm_delta=40.0, gap_beats=0.0),
        _make_transition("h2", "h3", bpm_delta=0.0, gap_beats=0.0),
    ])
    proposal = _make_proposal(items)
    feedback = validate(proposal, config, matrix)
    assert "H4" not in feedback.violated

    # bpm_delta=44, gap_beats=0 (no crossfade) → fails (> 40 non-crossfade cap)
    items2 = [
        _make_item("h1", song_id="s1", phase=1, bpm=100, gap_beats=0.0),
        _make_item("h2", song_id="s2", phase=3, bpm=56, gap_beats=0.0),
        _make_item("h3", song_id="s3", phase=5, bpm=56, gap_beats=0.0),
    ]
    matrix2 = dict([
        _make_transition("h1", "h2", bpm_delta=44.0, gap_beats=0.0),
        _make_transition("h2", "h3", bpm_delta=0.0, gap_beats=0.0),
    ])
    proposal2 = _make_proposal(items2)
    feedback2 = validate(proposal2, config, matrix2)
    assert "H4" in feedback2.violated

    # bpm_delta=50, gap_beats=2.0 (any gap triggers crossfade-tier cap=45) → fails (50 > 45)
    items3 = [
        _make_item("h1", song_id="s1", phase=1, bpm=100, gap_beats=2.0),
        _make_item("h2", song_id="s2", phase=3, bpm=50, gap_beats=2.0),
        _make_item("h3", song_id="s3", phase=5, bpm=50, gap_beats=2.0),
    ]
    matrix3 = dict([
        _make_transition("h1", "h2", bpm_delta=50.0, gap_beats=2.0),
        _make_transition("h2", "h3", bpm_delta=0.0, gap_beats=2.0),
    ])
    proposal3 = _make_proposal(items3)
    feedback3 = validate(proposal3, config, matrix3)
    assert "H4" in feedback3.violated


def test_h4_beam_threshold_matches_hard_constraints():
    """Both beam.py and hard_constraints.py use gap_beats > 0 (not > 4) for the higher cap."""
    from stream_of_worship.admin.songset_constructor.rules.beam import _sequences
    from stream_of_worship.admin.songset_constructor.models import SongCandidate

    config = RunConfig(count=2, proposals=1, pool=200)
    pool = [
        SongCandidate(song_id="s1", title="A", recording_hash_prefix="h1", phase=1, tempo_bpm=100, fan_out=1, is_dead_end=False),
        SongCandidate(song_id="s2", title="B", recording_hash_prefix="h2", phase=4, tempo_bpm=60, fan_out=1, is_dead_end=False),
    ]
    # gap_beats=2.0, bpm_delta=42 — with gap_beats > 0, the crossfade-tier cap (45) applies, so 42 ≤ 45 passes
    matrix = dict([
        _make_transition("h1", "h2", bpm_delta=42.0, gap_beats=2.0),
    ])
    sequences = list(_sequences(pool, config, matrix, width=4))
    # The sequence h1→h2 should survive because 42 ≤ 45 (crossfade-tier via gap_beats > 0)
    assert len(sequences) > 0
    seq_hashes = [c.recording_hash_prefix for c in sequences[0]]
    assert seq_hashes == ["h1", "h2"]


# ---------------------------------------------------------------------------
# H5 — calibrated CFD defaults
# ---------------------------------------------------------------------------

def test_h5_strict_3_default_allows_cfd_3():
    """Adjacent CFD=3 with no key shift should pass strict H5 (default=3)."""
    config = RunConfig(count=3, proposals=1, pool=200)
    items = [
        _make_item("h1", song_id="s1", phase=1, bpm=100),
        _make_item("h2", song_id="s2", phase=3, bpm=85),
        _make_item("h3", song_id="s3", phase=5, bpm=70),
    ]
    matrix = dict([
        _make_transition("h1", "h2", cfd=3, bpm_delta=15.0, gap_beats=2.0),
        _make_transition("h2", "h3", cfd=3, bpm_delta=15.0, gap_beats=2.0),
    ])
    proposal = _make_proposal(items)
    feedback = validate(proposal, config, matrix)
    assert "H5" not in feedback.violated


def test_h5_strict_fails_cfd_4():
    """Adjacent CFD=4 with no key shift should fail strict H5 (default=3)."""
    config = RunConfig(count=3, proposals=1, pool=200)
    items = [
        _make_item("h1", song_id="s1", phase=1, bpm=100),
        _make_item("h2", song_id="s2", phase=3, bpm=85),
        _make_item("h3", song_id="s3", phase=5, bpm=70),
    ]
    matrix = dict([
        _make_transition("h1", "h2", cfd=4, bpm_delta=15.0, gap_beats=2.0),
        _make_transition("h2", "h3", cfd=3, bpm_delta=15.0, gap_beats=2.0),
    ])
    proposal = _make_proposal(items)
    feedback = validate(proposal, config, matrix)
    assert "H5" in feedback.violated


def test_h5_relaxed_4_allows_cfd_4():
    """With relax_h5 on config, CFD=4 should pass (relaxed default=4)."""
    config = RunConfig(count=3, proposals=1, pool=200, relax_h5=True)
    items = [
        _make_item("h1", song_id="s1", phase=1, bpm=100),
        _make_item("h2", song_id="s2", phase=3, bpm=85),
        _make_item("h3", song_id="s3", phase=5, bpm=70),
    ]
    matrix = dict([
        _make_transition("h1", "h2", cfd=4, bpm_delta=15.0, gap_beats=2.0),
        _make_transition("h2", "h3", cfd=4, bpm_delta=15.0, gap_beats=2.0),
    ])
    proposal = _make_proposal(items)
    feedback = validate(proposal, config, matrix)
    assert "H5" not in feedback.violated


def test_h5_key_shift_bypasses_cfd_limit():
    """CFD > limit but with matching suggested_key_shift should pass H5."""
    config = RunConfig(count=3, proposals=1, pool=200)
    items = [
        _make_item("h1", song_id="s1", phase=1, bpm=100),
        _make_item("h2", song_id="s2", phase=3, bpm=85),
        _make_item("h3", song_id="s3", phase=5, bpm=70, key_shift=2),
    ]
    matrix = dict([
        _make_transition("h1", "h2", cfd=3, bpm_delta=15.0, gap_beats=2.0),
        _make_transition("h2", "h3", cfd=5, bpm_delta=15.0, gap_beats=2.0, key_shift=2),
    ])
    proposal = _make_proposal(items)
    feedback = validate(proposal, config, matrix)
    assert "H5" not in feedback.violated


# ---------------------------------------------------------------------------
# Diagnostics — h5_limit used instead of hardcoded 2
# ---------------------------------------------------------------------------

def test_diagnostics_compatible_transitions_uses_config_h5_limit():
    from stream_of_worship.admin.songset_constructor.rules.diagnostics import role_eligibility_counts

    config = RunConfig(count=2, proposals=1, pool=200)
    pool = []
    matrix = dict([
        _make_transition("h1", "h2", cfd=3),
        _make_transition("h2", "h3", cfd=2),
        _make_transition("h3", "h4", cfd=4),
    ])
    counts = role_eligibility_counts(pool, config, matrix)
    # strict h5_limit=3 → cfd 3 and 2 pass, cfd 4 fails → 2 compatible
    assert counts["compatible_transitions_h5"] == 2

    relaxed_config = RunConfig(count=2, proposals=1, pool=200, relax_h5=True)
    counts_relaxed = role_eligibility_counts(pool, relaxed_config, matrix)
    # relaxed h5_limit=4 → all 3 pass
    assert counts_relaxed["compatible_transitions_h5"] == 3
