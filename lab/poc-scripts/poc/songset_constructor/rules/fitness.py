"""Fitness scoring for candidate songsets."""

from __future__ import annotations

from poc.songset_constructor.config import RunConfig
from poc.songset_constructor.models import ScoreBreakdown, SongsetProposal, TransitionCandidate

TEMPLATE_PHASES_5 = (1, 2, 3, 4, 5)
TEMPLATE_PHASES_4 = (1, 3, 4, 5)


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def f_theme(proposal: SongsetProposal, songs: int) -> float:
    template = TEMPLATE_PHASES_4 if songs == 4 else TEMPLATE_PHASES_5
    distances = [abs((item.phase or 3) - template[index]) for index, item in enumerate(proposal.items)]
    return _clamp(1.0 - sum(distances) / (4.0 * len(template)))


def f_tempo(proposal: SongsetProposal) -> float:
    bpms = [item.bpm for item in proposal.items if item.bpm is not None]
    if len(bpms) < 2:
        return 0.5
    deltas = [abs(bpms[index + 1] - bpms[index]) for index in range(len(bpms) - 1)]
    smoothness = 1.0 - min(1.0, sum(deltas) / (25.0 * len(deltas)))
    arc_bonus = 1.0 if bpms[0] >= bpms[-1] else 0.75
    return _clamp(0.75 * smoothness + 0.25 * arc_bonus)


def f_harmony(
    proposal: SongsetProposal,
    matrix: dict[tuple[str, str], TransitionCandidate],
) -> float:
    if len(proposal.items) < 2:
        return 1.0
    scores = []
    for left, right in zip(proposal.items, proposal.items[1:]):
        transition = matrix.get((left.recording_hash_prefix, right.recording_hash_prefix))
        scores.append(transition.key_compat if transition else 0.2)
    return _clamp(sum(scores) / len(scores))


def f_diversity(proposal: SongsetProposal) -> float:
    song_ids = {item.song_id for item in proposal.items}
    themes = {theme for item in proposal.items for theme in item.themes}
    song_part = len(song_ids) / max(1, len(proposal.items))
    theme_part = min(1.0, len(themes) / max(2, len(proposal.items)))
    return _clamp(0.7 * song_part + 0.3 * theme_part)


def score(
    proposal: SongsetProposal,
    config: RunConfig,
    matrix: dict[tuple[str, str], TransitionCandidate],
) -> ScoreBreakdown:
    theme = f_theme(proposal, config.songs)
    tempo = f_tempo(proposal)
    harmony = f_harmony(proposal, matrix)
    diversity = f_diversity(proposal)
    total = 0.40 * theme + 0.30 * tempo + 0.20 * harmony + 0.10 * diversity
    return ScoreBreakdown(
        f_theme=round(theme, 4),
        f_tempo=round(tempo, 4),
        f_harmony=round(harmony, 4),
        f_diversity=round(diversity, 4),
        total=round(total, 4),
    )
