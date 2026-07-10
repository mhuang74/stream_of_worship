from poc.songset_constructor.config import RunConfig
from poc.songset_constructor.models import ScoreBreakdown, SongCandidate
from poc.songset_constructor.rules.beam import (
    _candidate_sort_key,
    _proposal_for_sequence,
    _sequences,
    _template,
    compute_fan_out,
    search,
)
from poc.songset_constructor.rules.diagnostics import beam_diagnostics, hard_rule_rejection_counts
from poc.songset_constructor.rules.fitness import f_theme, score
from poc.songset_constructor.rules.hard_constraints import validate
from poc.songset_constructor.rules.phases import fuse_themes, infer_phase
from poc.songset_constructor.rules.proposals import draft_from_candidates, proposal_from_draft
from poc.songset_constructor.rules.themes import classify_lyrics_themes, classify_title_themes
from poc.songset_constructor.rules.transitions import recommend_transition


def _matrix(pool):
    return {
        (left.recording_hash_prefix, right.recording_hash_prefix): recommend_transition(left, right)
        for left in pool
        for right in pool
        if left.recording_hash_prefix != right.recording_hash_prefix
    }


def test_theme_fusion_and_phase_inference():
    title = classify_title_themes("赞美主")
    lyrics = classify_lyrics_themes("我要赞美\n感谢你的恩典")
    fused = fuse_themes(title, lyrics, {}, {})
    assert max(fused, key=fused.get) == "赞美"
    assert infer_phase(fused, 124) == 1


def test_scored_proposal_passes_constraints(synthetic_pool):
    config = RunConfig(no_llm=True)
    matrix = _matrix(synthetic_pool)
    draft = draft_from_candidates(synthetic_pool[:5])
    proposal = proposal_from_draft(
        draft,
        synthetic_pool,
        score=ScoreBreakdown(f_theme=0, f_tempo=0, f_harmony=0, f_diversity=0, total=0),
        llm_origin=False,
    )
    proposal = proposal.model_copy(update={"score": score(proposal, config, matrix)})
    feedback = validate(proposal, config, matrix)
    assert feedback.passed, feedback.errors
    assert proposal.score.total > 0.7


def test_beam_search_is_deterministic(synthetic_pool):
    matrix = _matrix(synthetic_pool)
    config = RunConfig(no_llm=True, top_k=2)
    pool = compute_fan_out(synthetic_pool, matrix, config)
    first = search(pool, config, matrix)
    second = search(pool, config, matrix)
    assert [p.model_dump() for p in first] == [p.model_dump() for p in second]
    assert first


def test_diagnostics_counts_hard_rule_rejections():
    sequence = [
        SongCandidate(
            song_id="s1",
            title="Soft Opener",
            recording_hash_prefix="d001",
            tempo_bpm=100,
            musical_key="C",
            musical_mode="maj",
            phase=1,
        ),
        SongCandidate(
            song_id="s2",
            title="Middle",
            recording_hash_prefix="d002",
            tempo_bpm=95,
            musical_key="F#",
            musical_mode="maj",
            phase=3,
        ),
        SongCandidate(
            song_id="s3",
            title="Response",
            recording_hash_prefix="d003",
            tempo_bpm=80,
            musical_key="C",
            musical_mode="maj",
            phase=4,
        ),
        SongCandidate(
            song_id="s4",
            title="Loud Closer",
            recording_hash_prefix="d004",
            tempo_bpm=100,
            musical_key="F#",
            musical_mode="maj",
            phase=5,
        ),
    ]

    diagnostics = hard_rule_rejection_counts([sequence], RunConfig(songs=4, no_llm=True), {})

    assert diagnostics["generated_sequences"] == 1
    assert diagnostics["rejected_sequences"] == 1
    assert diagnostics["hard_rule_rejections"]["H2"] == 1
    assert diagnostics["hard_rule_rejections"]["H3"] == 1
    assert diagnostics["hard_rule_rejections"]["H5"] == 3


def _candidate(
    song_id,
    title,
    hash_prefix,
    bpm,
    key,
    mode,
    phase,
    composer="Z",
    key_confidence=0.9,
    themes=None,
):
    return SongCandidate(
        song_id=song_id,
        title=title,
        recording_hash_prefix=hash_prefix,
        tempo_bpm=bpm,
        musical_key=key,
        musical_mode=mode,
        key_confidence=key_confidence,
        phase=phase,
        themes=themes or {title: 1},
        composer=composer,
    )


def _loud_closer_pool():
    return [
        _candidate("o1", "Opener", "o1", 124, "G", "maj", 1),
        _candidate("m1", "Mid1", "m1", 112, "D", "maj", 2),
        _candidate("m2", "Mid2", "m2", 100, "A", "maj", 3),
        _candidate("c1", "LoudCloser1", "c1", 95, "E", "min", 4),
        _candidate("c2", "LoudCloser2", "c2", 105, "B", "min", 5),
    ]


def test_relax_h3_raises_ceiling_allows_loud_closer():
    pool = _loud_closer_pool()
    matrix = _matrix(pool)
    strict_config = RunConfig(no_llm=True, auto_relax=False)
    pool = compute_fan_out(pool, matrix, strict_config)

    assert search(pool, strict_config, matrix) == []

    relaxed_config = RunConfig(no_llm=True)
    proposals = search(pool, relaxed_config, matrix)
    assert proposals
    assert any("relaxed_H2_H3" in p.hard_constraint_warnings for p in proposals)


def test_relax_h2_lowers_floor_allows_slow_opener():
    pool = [
        _candidate("o1", "SlowOpener", "o1", 95, "G", "maj", 1),
        _candidate("m1", "Mid1", "m1", 100, "D", "maj", 2),
        _candidate("m2", "Mid2", "m2", 95, "A", "maj", 3),
        _candidate("c1", "Closer1", "c1", 80, "E", "min", 4),
        _candidate("c2", "Closer2", "c2", 78, "B", "min", 5),
    ]
    matrix = _matrix(pool)
    strict_config = RunConfig(no_llm=True, auto_relax=False)
    pool = compute_fan_out(pool, matrix, strict_config)

    assert search(pool, strict_config, matrix) == []

    relaxed_config = RunConfig(no_llm=True)
    proposals = search(pool, relaxed_config, matrix)
    assert proposals
    assert any("relaxed_H2_H3" in p.hard_constraint_warnings for p in proposals)


def test_relax_h1_skips_redundant_phase1_requirement():
    pool = [
        _candidate("o1", "Opener1", "o1", 124, "C", "maj", 1),
        _candidate("o2", "Opener2", "o2", 112, "G", "maj", 1),
        _candidate("m1", "Mid1", "m1", 100, "D", "maj", 4),
        _candidate("c1", "Closer1", "c1", 88, "A", "maj", 5),
    ]
    matrix = _matrix(pool)
    strict_config = RunConfig(songs=4, no_llm=True, auto_relax=False, relax_h1=False)
    pool = compute_fan_out(pool, matrix, strict_config)

    assert search(pool, strict_config, matrix) == []

    relaxed_config = RunConfig(songs=4, no_llm=True, relax_h1=True)
    proposals = search(pool, relaxed_config, matrix)
    assert proposals


def test_no_auto_relax_keeps_strict_only():
    pool = _loud_closer_pool()
    matrix = _matrix(pool)
    config = RunConfig(no_llm=True, auto_relax=False)
    pool = compute_fan_out(pool, matrix, config)

    assert search(pool, config, matrix) == []


# ---------------------------------------------------------------------------
# v1 H2/H3 endcap filtering tests
# ---------------------------------------------------------------------------


def test_beam_filters_closer_by_h3_ceiling():
    pool = [
        _candidate("o1", "Opener", "o1", 124, "G", "maj", 1),
        _candidate("m1", "Mid1", "m1", 112, "D", "maj", 2),
        _candidate("m2", "Mid2", "m2", 100, "A", "maj", 3),
        _candidate("c1", "LowCloser", "c1", 88, "E", "min", 4),
        _candidate("c2", "HighCloser", "c2", 100, "B", "min", 5),
    ]
    matrix = _matrix(pool)

    strict_config = RunConfig(no_llm=True, auto_relax=False)
    strict_pool = compute_fan_out(pool, matrix, strict_config)
    proposals = search(strict_pool, strict_config, matrix)
    assert proposals
    assert all(p.items[-1].bpm <= strict_config.closing_limit for p in proposals)

    relaxed_config = RunConfig(no_llm=True, auto_relax=False, relax_h3_bpm=110)
    relaxed_pool = compute_fan_out(pool, matrix, relaxed_config)
    relaxed_proposals = search(relaxed_pool, relaxed_config, matrix)
    assert relaxed_proposals


def test_beam_filters_opener_by_h2_floor():
    pool = [
        _candidate("o1", "SlowOpener", "o1", 95, "G", "maj", 1),
        _candidate("o2", "FastOpener", "o2", 115, "D", "maj", 1),
        _candidate("m1", "Mid1", "m1", 105, "A", "maj", 2),
        _candidate("m2", "Mid2", "m2", 95, "E", "maj", 3),
        _candidate("c1", "Closer1", "c1", 85, "B", "min", 4),
        _candidate("c2", "Closer2", "c2", 78, "F#", "min", 5),
    ]
    matrix = _matrix(pool)

    strict_config = RunConfig(no_llm=True, auto_relax=False)
    strict_pool = compute_fan_out(pool, matrix, strict_config)
    proposals = search(strict_pool, strict_config, matrix)
    assert proposals
    assert all(p.items[0].bpm >= strict_config.opening_floor for p in proposals)

    relaxed_config = RunConfig(no_llm=True, auto_relax=False, relax_h2_bpm=90)
    relaxed_pool = compute_fan_out(pool, matrix, relaxed_config)
    relaxed_proposals = search(relaxed_pool, relaxed_config, matrix)
    assert relaxed_proposals


def test_relax_h3_unblocks_when_only_high_bpm_closer_matches_preceding():
    pool = [
        _candidate("o1", "Opener", "o1", 124, "G", "maj", 1),
        _candidate("m1", "Mid1", "m1", 120, "D", "maj", 2),
        _candidate("m2", "Mid2", "m2", 115, "A", "maj", 3),
        _candidate("m3", "Mid3", "m3", 110, "E", "maj", 4),
        _candidate("c1", "HighCloser", "c1", 105, "B", "min", 5),
    ]
    matrix = _matrix(pool)

    strict_config = RunConfig(no_llm=True, auto_relax=False)
    strict_pool = compute_fan_out(pool, matrix, strict_config)
    assert search(strict_pool, strict_config, matrix) == []

    relaxed_config = RunConfig(no_llm=True, auto_relax=False, relax_h3_bpm=110)
    relaxed_pool = compute_fan_out(pool, matrix, relaxed_config)
    proposals = search(relaxed_pool, relaxed_config, matrix)
    assert proposals
    assert all(p.items[-1].bpm <= 110 for p in proposals)


def test_diagnostics_beam_sequences_uses_config_ceiling():
    pool = [
        _candidate("o1", "Opener", "o1", 124, "G", "maj", 1),
        _candidate("m1", "Mid1", "m1", 112, "D", "maj", 2),
        _candidate("m2", "Mid2", "m2", 100, "A", "maj", 3),
        _candidate("c1", "LowCloser", "c1", 88, "E", "min", 4),
        _candidate("c2", "HighCloser", "c2", 100, "B", "min", 5),
    ]
    matrix = _matrix(pool)
    config = RunConfig(no_llm=True, auto_relax=False, relax_h3_bpm=110)
    pool = compute_fan_out(pool, matrix, config)
    diagnostics = beam_diagnostics(pool, config, matrix)
    assert diagnostics["generated_sequences"] >= 1
    assert diagnostics["rejected_sequences"] < diagnostics["generated_sequences"]


# ---------------------------------------------------------------------------
# v3 H1/H4/H5 per-pair filtering tests
# ---------------------------------------------------------------------------


def test_relax_h1_opener_accepts_phase_2():
    pool = [
        _candidate("o1", "Phase2Opener", "o1", 115, "G", "maj", 2),
        _candidate("m1", "Mid1", "m1", 105, "D", "maj", 3),
        _candidate("m2", "Mid2", "m2", 95, "A", "maj", 4),
        _candidate("c1", "Closer1", "c1", 85, "B", "min", 5),
        _candidate("c2", "Closer2", "c2", 78, "E", "min", 4),
    ]
    matrix = _matrix(pool)

    strict_config = RunConfig(no_llm=True, auto_relax=False, relax_h1=False)
    strict_pool = compute_fan_out(pool, matrix, strict_config)
    assert search(strict_pool, strict_config, matrix) == []

    relaxed_config = RunConfig(no_llm=True, auto_relax=False, relax_h1=True)
    relaxed_pool = compute_fan_out(pool, matrix, relaxed_config)
    proposals = search(relaxed_pool, relaxed_config, matrix)
    assert proposals


def test_beam_rejects_h4_violating_middle_pair():
    pool = [
        _candidate("o1", "Opener", "o1", 124, "G", "maj", 1),
        _candidate("m1", "Mid1", "m1", 102, "E", "maj", 2),
        _candidate("m2", "Mid2", "m2", 92, "A", "maj", 3),
        _candidate("c1", "Closer1", "c1", 82, "B", "min", 4),
        _candidate("c2", "Closer2", "c2", 78, "E", "min", 5),
    ]
    matrix = _matrix(pool)

    strict_config = RunConfig(no_llm=True, auto_relax=False)
    strict_pool = compute_fan_out(pool, matrix, strict_config)
    strict_sequences = list(_sequences(strict_pool, strict_config, matrix, width=8))
    assert not strict_sequences

    relaxed_config = RunConfig(no_llm=True, auto_relax=False, relax_h4=True, relax_h5=True)
    relaxed_pool = compute_fan_out(pool, matrix, relaxed_config)
    relaxed_sequences = list(_sequences(relaxed_pool, relaxed_config, matrix, width=8))
    assert relaxed_sequences


def test_beam_rejects_h5_violating_pair():
    pool = [
        _candidate("o1", "Opener", "o1", 124, "G", "maj", 1),
        _candidate("m1", "Mid1", "m1", 112, "D", "maj", 2),
        _candidate("m2", "Mid2", "m2", 100, "F", "maj", 3),
        _candidate("c1", "Closer1", "c1", 88, "B", "min", 4),
        _candidate("c2", "Closer2", "c2", 78, "F#", "min", 5),
    ]
    matrix = _matrix(pool)
    for key, transition in matrix.items():
        if transition.cfd > 2:
            matrix[key] = transition.model_copy(update={"suggested_key_shift": 0})

    strict_config = RunConfig(no_llm=True, auto_relax=False)
    strict_pool = compute_fan_out(pool, matrix, strict_config)
    strict_sequences = list(_sequences(strict_pool, strict_config, matrix, width=8))
    assert not strict_sequences

    relaxed_config = RunConfig(no_llm=True, auto_relax=False, relax_h5=True)
    relaxed_pool = compute_fan_out(pool, matrix, relaxed_config)
    relaxed_sequences = list(_sequences(relaxed_pool, relaxed_config, matrix, width=8))
    assert relaxed_sequences


def test_beam_h4_honors_crossfade_branch():
    pool = [
        _candidate("o1", "Opener", "o1", 124, "G", "maj", 1),
        _candidate("m0", "Mid0", "m0", 112, "D", "maj", 2),
        _candidate("m1", "Mid1", "m1", 106, "E", "maj", 2),
        _candidate("m2", "Mid2", "m2", 96, "A", "maj", 3),
        _candidate("c1", "Closer1", "c1", 86, "B", "min", 4),
        _candidate("c2", "Closer2", "c2", 78, "E", "min", 5),
    ]
    matrix = _matrix(pool)
    config = RunConfig(no_llm=True, auto_relax=False)
    pool = compute_fan_out(pool, matrix, config)

    o1 = next(c for c in pool if c.recording_hash_prefix == "o1")
    m1 = next(c for c in pool if c.recording_hash_prefix == "m1")
    key = (o1.recording_hash_prefix, m1.recording_hash_prefix)
    original = matrix[key]
    matrix[key] = original.model_copy(
        update={
            "bpm_delta": 18,
            "crossfade_duration_seconds": 4.0,
            "gap_beats": 2.0,
        }
    )

    sequences = list(_sequences(pool, config, matrix, width=8))
    found = any(
        seq[0].recording_hash_prefix == o1.recording_hash_prefix
        and seq[1].recording_hash_prefix == m1.recording_hash_prefix
        for seq in sequences
    )
    assert found


def test_beam_h5_honors_suggested_key_shift():
    pool = [
        _candidate("o1", "Opener", "o1", 124, "G", "maj", 1),
        _candidate("m1", "Mid1", "m1", 120, "D", "maj", 2),
        _candidate("m2", "Mid2", "m2", 110, "A", "maj", 3),
        _candidate("c1", "Closer1", "c1", 100, "E", "min", 4),
        _candidate("c2", "Closer2", "c2", 90, "B", "min", 5),
    ]
    matrix = _matrix(pool)
    config = RunConfig(no_llm=True, auto_relax=False)
    pool = compute_fan_out(pool, matrix, config)

    o1 = pool[0]
    m1 = pool[1]
    key = (o1.recording_hash_prefix, m1.recording_hash_prefix)
    original = matrix[key]
    matrix[key] = original.model_copy(
        update={
            "cfd": 4,
            "suggested_key_shift": 1,
        }
    )

    sequences = list(_sequences(pool, config, matrix, width=8))
    found = any(
        seq[0].recording_hash_prefix == o1.recording_hash_prefix
        and seq[1].recording_hash_prefix == m1.recording_hash_prefix
        for seq in sequences
    )
    assert found


def test_beam_h5_shifted_ok_after_proposal_applies_shift():
    pool = [
        _candidate("o1", "Opener", "o1", 124, "G", "maj", 1),
        _candidate("m1", "Mid1", "m1", 120, "D", "maj", 2),
        _candidate("m2", "Mid2", "m2", 110, "A", "maj", 3),
        _candidate("c1", "Closer1", "c1", 100, "E", "min", 4),
        _candidate("c2", "Closer2", "c2", 90, "B", "min", 5),
    ]
    matrix = _matrix(pool)
    config = RunConfig(no_llm=True, auto_relax=False)
    pool = compute_fan_out(pool, matrix, config)

    o1 = pool[0]
    m1 = pool[1]
    key = (o1.recording_hash_prefix, m1.recording_hash_prefix)
    original = matrix[key]
    matrix[key] = original.model_copy(
        update={
            "cfd": 4,
            "suggested_key_shift": 1,
        }
    )

    sequences = list(_sequences(pool, config, matrix, width=8))
    target_seq = next(
        seq
        for seq in sequences
        if seq[0].recording_hash_prefix == o1.recording_hash_prefix
        and seq[1].recording_hash_prefix == m1.recording_hash_prefix
    )
    proposal = _proposal_for_sequence(target_seq, config, matrix)
    right_item = proposal.items[1]
    assert right_item.key_shift_semitones == 1
    feedback = validate(proposal, config, matrix)
    assert "H5" not in feedback.violated


def test_diagnostics_relaxed_tier_report_present():
    pool = [
        _candidate("o1", "Opener", "o1", 124, "G", "maj", 1),
        _candidate("m1", "Mid1", "m1", 112, "D", "maj", 2),
        _candidate("m2", "Mid2", "m2", 100, "A", "maj", 3),
        _candidate("c1", "LowCloser", "c1", 88, "E", "min", 4),
        _candidate("c2", "HighCloser", "c2", 100, "B", "min", 5),
    ]
    matrix = _matrix(pool)
    config = RunConfig(no_llm=True, auto_relax=False)
    pool = compute_fan_out(pool, matrix, config)
    diagnostics = beam_diagnostics(pool, config, matrix)

    assert "hard_rule_rejections" in diagnostics
    assert "relaxed_tier_rejections" in diagnostics
    relaxed = diagnostics["relaxed_tier_rejections"]
    assert "hard_rule_rejections" in relaxed
    assert "rejected_sequences" in relaxed
    assert "generated_sequences" in relaxed


def test_diagnostics_uses_beam_sort_key(monkeypatch):
    calls = []
    original = _candidate_sort_key

    def wrapper(candidate):
        calls.append(candidate)
        return original(candidate)

    import poc.songset_constructor.rules.diagnostics as diag_mod

    monkeypatch.setattr(diag_mod, "_candidate_sort_key", wrapper)
    monkeypatch.setattr("poc.songset_constructor.rules.diagnostics._candidate_sort_key", wrapper)

    pool = [
        _candidate("o1", "Opener", "o1", 124, "G", "maj", 1),
        _candidate("m1", "Mid1", "m1", 112, "D", "maj", 2),
        _candidate("c1", "Closer1", "c1", 88, "E", "min", 4),
        _candidate("c2", "Closer2", "c2", 78, "B", "min", 5),
    ]
    matrix = _matrix(pool)
    config = RunConfig(no_llm=True, auto_relax=False)
    beam_diagnostics(pool, config, matrix)
    assert calls


def test_to_dict_preserves_relax_h4_h5():
    config = RunConfig(
        relax_h4=True,
        relax_h5=True,
        relax_h4_bpm=30,
        relax_h5_cfd=4,
    )
    data = config.to_dict()
    assert data["relax_h4"] is True
    assert data["relax_h5"] is True
    assert data["relax_h4_bpm"] == 30
    assert data["relax_h5_cfd"] == 4

    child = RunConfig(**{**data, "songs": 4})
    assert child.relax_h4 is True
    assert child.relax_h5 is True
    assert child.relax_h4_bpm == 30
    assert child.relax_h5_cfd == 4
    assert child.h4_limit == 30
    assert child.h5_limit == 4


def test_compute_fan_out_uses_config_limits():
    pool = [
        _candidate("o1", "Opener", "o1", 124, "G", "maj", 1),
        _candidate("m1", "Mid1", "m1", 100, "D", "maj", 2),
        _candidate("m2", "Mid2", "m2", 76, "A", "maj", 3),
        _candidate("c1", "Closer1", "c1", 52, "E", "min", 4),
        _candidate("c2", "Closer2", "c2", 28, "B", "min", 5),
    ]
    matrix = _matrix(pool)

    strict_config = RunConfig(no_llm=True, auto_relax=False)
    strict_pool = compute_fan_out(pool, matrix, strict_config)
    assert all(c.is_dead_end for c in strict_pool)

    relaxed_config = RunConfig(no_llm=True, auto_relax=False, relax_h4=True, relax_h5=True)
    relaxed_pool = compute_fan_out(pool, matrix, relaxed_config)
    assert any(not c.is_dead_end for c in relaxed_pool)


# ---------------------------------------------------------------------------
# Short-set (songs=2 / songs=3) phase template tests
# ---------------------------------------------------------------------------


def test_template_returns_correct_phase_arc_for_each_song_count():
    assert _template(2) == (1, 4)
    assert _template(3) == (1, 3, 5)
    assert _template(4) == (1, 3, 4, 5)
    assert _template(5) == (1, 2, 3, 4, 5)


def test_beam_search_passes_h0_for_three_songs(synthetic_pool):
    matrix = _matrix(synthetic_pool)
    config = RunConfig(songs=3, no_llm=True)
    pool = compute_fan_out(synthetic_pool, matrix, config)
    proposals = search(pool, config, matrix)
    assert proposals, "expected at least one 3-song proposal passing H0"
    assert len(proposals[0].items) == 3
    feedback = validate(
        proposals[0],
        config,
        matrix,
        relax_h1=config.relax_h1,
        relax_h4=config.relax_h4,
        relax_h5=config.relax_h5,
    )
    assert feedback.passed, feedback.errors


def test_beam_search_passes_h0_for_two_songs():
    # synthetic_pool's BPM spread (124 -> 78) exceeds H4 limits for a 2-song
    # opener->closer arc, so use a compact pool where the opener and closer sit
    # within the default 15-BPM non-crossfade H4 budget.
    pool = [
        _candidate("o1", "Opener", "o1", 110, "G", "maj", 1),
        _candidate("c1", "Closer", "c1", 95, "E", "min", 4),
    ]
    matrix = _matrix(pool)
    config = RunConfig(songs=2, no_llm=True, relax_h3_bpm=100)
    pool = compute_fan_out(pool, matrix, config)
    proposals = search(pool, config, matrix)
    assert proposals, "expected at least one 2-song proposal passing H0"
    assert len(proposals[0].items) == 2
    feedback = validate(
        proposals[0],
        config,
        matrix,
        relax_h1=config.relax_h1,
        relax_h4=config.relax_h4,
        relax_h5=config.relax_h5,
    )
    assert feedback.passed, feedback.errors


def test_f_theme_uses_correct_template_for_short_sets():
    pool = [
        _candidate("o1", "Opener", "o1", 124, "G", "maj", 1),
        _candidate("m1", "Mid", "m1", 98, "A", "maj", 3),
        _candidate("c1", "Closer", "c1", 78, "B", "min", 5),
    ]
    matrix = _matrix(pool)
    draft = draft_from_candidates(pool[:3])
    proposal = proposal_from_draft(
        draft,
        pool,
        score=ScoreBreakdown(f_theme=0, f_tempo=0, f_harmony=0, f_diversity=0, total=0),
        llm_origin=False,
    )
    # Phases exactly match the (1, 3, 5) template -> distance 0 -> f_theme == 1.0
    assert f_theme(proposal, songs=3) == 1.0
    # Sanity: the denominator uses len(template)==3, not the legacy 5-phase value.
    assert score(proposal, RunConfig(songs=3, no_llm=True), matrix).f_theme == 1.0
