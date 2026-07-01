from poc.songset_constructor.config import RunConfig
from poc.songset_constructor.models import ScoreBreakdown, SongCandidate
from poc.songset_constructor.rules.beam import compute_fan_out, search
from poc.songset_constructor.rules.diagnostics import hard_rule_rejection_counts
from poc.songset_constructor.rules.fitness import score
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
    pool = compute_fan_out(synthetic_pool, matrix)
    config = RunConfig(no_llm=True, top_k=2)
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
    pool = compute_fan_out(pool, matrix)

    strict_config = RunConfig(no_llm=True, auto_relax=False)
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
    pool = compute_fan_out(pool, matrix)

    strict_config = RunConfig(no_llm=True, auto_relax=False)
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
    pool = compute_fan_out(pool, matrix)

    strict_config = RunConfig(songs=4, no_llm=True, auto_relax=False)
    assert search(pool, strict_config, matrix) == []

    relaxed_config = RunConfig(songs=4, no_llm=True)
    proposals = search(pool, relaxed_config, matrix)
    assert proposals
    assert any("relaxed_H1" in p.hard_constraint_warnings for p in proposals)


def test_no_auto_relax_keeps_strict_only():
    pool = _loud_closer_pool()
    matrix = _matrix(pool)
    pool = compute_fan_out(pool, matrix)

    config = RunConfig(no_llm=True, auto_relax=False)
    assert search(pool, config, matrix) == []
