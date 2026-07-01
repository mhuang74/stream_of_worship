"""Read-only diagnostics for failed songset construction."""

from __future__ import annotations

from collections import Counter
from typing import Iterable

from poc.songset_constructor.config import RunConfig
from poc.songset_constructor.models import (
    ScoreBreakdown,
    SongCandidate,
    SongsetProposal,
    TransitionCandidate,
)

from .beam import _sequences
from .fitness import score
from .hard_constraints import validate
from .proposals import draft_from_candidates, proposal_from_draft

MISSING_TEMPO_AND_KEY_METADATA = "missing_tempo_and_key_metadata"


def enrichment_drop_diagnostics(pool: Iterable[SongCandidate], *, sample_limit: int = 5) -> dict:
    counts: Counter[str] = Counter()
    samples = []
    for candidate in pool:
        if candidate.tempo_bpm is None and candidate.musical_key is None:
            counts[MISSING_TEMPO_AND_KEY_METADATA] += 1
            if len(samples) < sample_limit:
                samples.append(
                    {
                        "title": candidate.title,
                        "recording_hash_prefix": candidate.recording_hash_prefix,
                        "reason": MISSING_TEMPO_AND_KEY_METADATA,
                    }
                )
    return {"drop_reasons": dict(counts), "dropped_samples": samples}


def role_eligibility_counts(
    pool: list[SongCandidate],
    config: RunConfig,
    matrix: dict[tuple[str, str], TransitionCandidate],
) -> dict[str, int]:
    closing_limit = 80 if config.intimate else 90
    return {
        "valid_openers_h2": sum(
            1 for candidate in pool if candidate.phase == 1 and (candidate.tempo_bpm or 0) >= 110
        ),
        "valid_closers_h3": sum(
            1
            for candidate in pool
            if candidate.phase in {4, 5}
            and candidate.tempo_bpm is not None
            and candidate.tempo_bpm <= closing_limit
        ),
        "phase_1_candidates_h1": sum(1 for candidate in pool if candidate.phase == 1),
        "phase_3_or_4_candidates_h1": sum(1 for candidate in pool if candidate.phase in {3, 4}),
        "phase_4_or_5_candidates_h1": sum(1 for candidate in pool if candidate.phase in {4, 5}),
        "compatible_transitions_h5": sum(
            1 for transition in matrix.values() if transition.cfd <= 2
        ),
    }


def _proposal_for_diagnostics(
    sequence: list[SongCandidate],
    config: RunConfig,
    matrix: dict[tuple[str, str], TransitionCandidate],
) -> SongsetProposal:
    draft = draft_from_candidates(sequence, rationale="Diagnostics beam sequence.")
    placeholder = ScoreBreakdown(f_theme=0, f_tempo=0, f_harmony=0, f_diversity=0, total=0)
    proposal = proposal_from_draft(draft, sequence, placeholder, llm_origin=False)
    return proposal.model_copy(update={"score": score(proposal, config, matrix)})


def hard_rule_rejection_counts(
    sequences: Iterable[list[SongCandidate]],
    config: RunConfig,
    matrix: dict[tuple[str, str], TransitionCandidate],
) -> dict[str, int]:
    counts: Counter[str] = Counter()
    generated = 0
    rejected = 0
    for sequence in sequences:
        generated += 1
        proposal = _proposal_for_diagnostics(sequence, config, matrix)
        feedback = validate(proposal, config, matrix)
        if feedback.passed:
            continue
        rejected += 1
        counts.update(feedback.violated)
    return {
        "generated_sequences": generated,
        "rejected_sequences": rejected,
        "hard_rule_rejections": dict(sorted(counts.items())),
    }


def beam_diagnostics(
    pool: list[SongCandidate],
    config: RunConfig,
    matrix: dict[tuple[str, str], TransitionCandidate],
    *,
    width: int = 8,
) -> dict:
    sorted_pool = sorted(
        pool,
        key=lambda candidate: (
            candidate.is_dead_end,
            candidate.phase,
            -(candidate.tempo_bpm or 0),
            candidate.recording_hash_prefix,
        ),
    )
    sequences = list(_sequences(sorted_pool, config.songs, width=width))
    return {
        "role_eligibility": role_eligibility_counts(pool, config, matrix),
        **hard_rule_rejection_counts(sequences, config, matrix),
    }


def diagnostic_lines(config: RunConfig, result: dict) -> list[str]:
    lines = []
    load_data = result.get("load_catalog", {})
    enrich_data = result.get("enrich_pool", {})
    beam_data = result.get("beam_seed_candidates", {})
    loaded = load_data.get("pool_size")
    enriched = enrich_data.get("pool_size")
    if isinstance(enriched, int) and enriched < config.songs:
        lines.append(f"only {enriched} enriched candidates remain for a {config.songs}-song set")
    drop_reasons = enrich_data.get("drop_reasons") or {}
    dropped = enrich_data.get("dropped")
    if not dropped and isinstance(drop_reasons, dict):
        dropped = sum(drop_reasons.values())
    if isinstance(drop_reasons, dict) and loaded:
        for reason, count in sorted(drop_reasons.items(), key=lambda item: (-item[1], item[0])):
            if count == loaded:
                lines.append(f"all loaded songs were dropped by {reason} ({count}/{loaded})")
            elif count >= max(1, loaded // 2):
                lines.append(f"most loaded songs were dropped by {reason} ({count}/{loaded})")
            elif count:
                lines.append(f"{count} loaded songs were dropped by {reason}")
    elif dropped:
        lines.append(f"{dropped} loaded songs were dropped during enrichment")

    rejections = beam_data.get("hard_rule_rejections") or {}
    rejected = beam_data.get("rejected_sequences")
    generated = beam_data.get("generated_sequences")
    if isinstance(rejections, dict) and rejections:
        counts = ", ".join(f"{code}={count}" for code, count in sorted(rejections.items()))
        if generated:
            lines.append(
                f"beam validation rejected {rejected or 0}/{generated} generated sequences: "
                f"{counts}"
            )
        else:
            lines.append(f"beam validation rejections by rule: {counts}")
    role = beam_data.get("role_eligibility") or {}
    if isinstance(role, dict) and role:
        zeroes = [key for key, value in sorted(role.items()) if value == 0]
        if zeroes:
            lines.append("role eligibility shortfalls: " + ", ".join(zeroes))
    return lines
