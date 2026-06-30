from poc.songset_constructor.config import RunConfig
from poc.songset_constructor.models import ScoreBreakdown
from poc.songset_constructor.rules.beam import compute_fan_out, search
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
