"""Fitness scoring for candidate songsets."""

from __future__ import annotations

from stream_of_worship.admin.songset_constructor.components import POSTURE_PHASE_FIT
from stream_of_worship.admin.songset_constructor.config import RunConfig
from stream_of_worship.admin.songset_constructor.models import (
    ScoreBreakdown,
    SongsetProposal,
    TransitionCandidate,
)

TEMPLATE_PHASES_5 = (1, 2, 3, 4, 5)
TEMPLATE_PHASES_4 = (1, 3, 4, 5)
TEMPLATE_PHASES_3 = (1, 3, 5)
TEMPLATE_PHASES_2 = (1, 4)

_THEME_TEMPLATES: dict[int, tuple[int, ...]] = {
    2: TEMPLATE_PHASES_2,
    3: TEMPLATE_PHASES_3,
    4: TEMPLATE_PHASES_4,
    5: TEMPLATE_PHASES_5,
}


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def f_theme(proposal: SongsetProposal, songs: int) -> float:
    template = _THEME_TEMPLATES[songs]
    distances = []
    for index, item in enumerate(proposal.items):
        if template[index] == item.phase or template[index] in item.secondary_phases:
            distances.append(0)
        else:
            distances.append(abs((item.phase or 3) - template[index]))
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


def f_energy(proposal: SongsetProposal) -> float | None:
    """Ordinal energy score on pool-percentile ranks; None → neutral redistribution.

    Arc energy value = ``entry_energy_pct`` (the energy the song arrives with);
    the closer also contributes ``exit_energy_pct``. Returns None when fewer than
    2 usable adjacency values and no closer-exit value exist (pool-wide absence).
    """
    items = proposal.items
    adjacency: list[float] = []
    for left, right in zip(items, items[1:]):  # noqa: RUF007 — matches module convention
        if left.exit_energy_pct is not None and right.entry_energy_pct is not None:
            adjacency.append(1.0 - abs(right.entry_energy_pct - left.exit_energy_pct))
    closer_exit = items[-1].exit_energy_pct if items else None
    opener_entry = items[0].entry_energy_pct if items else None
    arc = None
    if opener_entry is not None and closer_exit is not None:
        # Sets should generally land softer than they open.
        arc = 1.0 - max(0.0, opener_entry - closer_exit)
    values = [*adjacency] + ([arc] if arc is not None else [])
    if len(values) < 2:
        return None
    if arc is None:
        return _clamp(sum(adjacency) / len(adjacency))
    adjacency_part = sum(adjacency) / len(adjacency) if adjacency else arc
    return _clamp(0.5 * adjacency_part + 0.5 * arc)


def f_posture(proposal: SongsetProposal) -> float | None:
    """Chorus-preference posture fit against the phase template; None when no item has posture."""
    scored = [
        POSTURE_PHASE_FIT[item.component_posture].get(item.phase, 0.5)
        for item in proposal.items
        if item.component_posture is not None
    ]
    if not scored:
        return None
    return _clamp(sum(scored) / len(scored))


def middle_song_ids(proposal: SongsetProposal) -> set[str]:
    if len(proposal.items) <= 2:
        return set()
    return {item.song_id for item in proposal.items[1:-1]}


W_BASE: dict[str, float] = {"theme": 0.40, "tempo": 0.30, "harmony": 0.20, "diversity": 0.10}


def score(
    proposal: SongsetProposal,
    config: RunConfig,
    matrix: dict[tuple[str, str], TransitionCandidate],
) -> ScoreBreakdown:
    theme = f_theme(proposal, config.count)
    tempo = f_tempo(proposal)
    harmony = f_harmony(proposal, matrix)
    diversity = f_diversity(proposal)
    energy = f_energy(proposal)  # None when the signal is absent pool-wide
    posture = f_posture(proposal)  # None when the signal is absent pool-wide
    absent = (energy is None) + (posture is None)
    scale = 1.0 - 0.05 * (2 - absent)
    weights = {key: round(value * scale, 4) for key, value in W_BASE.items()}
    total = (
        weights["theme"] * theme
        + weights["tempo"] * tempo
        + weights["harmony"] * harmony
        + weights["diversity"] * diversity
    )
    if energy is not None:
        total += 0.05 * energy
    if posture is not None:
        total += 0.05 * posture
    return ScoreBreakdown(
        f_theme=round(theme, 4),
        f_tempo=round(tempo, 4),
        f_harmony=round(harmony, 4),
        f_diversity=round(diversity, 4),
        f_energy=round(energy, 4) if energy is not None else None,
        f_posture=round(posture, 4) if posture is not None else None,
        total=round(total, 4),
    )


def score_with_diversity_penalty(
    proposal: SongsetProposal,
    config: RunConfig,
    matrix: dict[tuple[str, str], TransitionCandidate],
    *,
    used_middle_songs: set[str],
    penalty_weight: float = 0.15,
) -> ScoreBreakdown:
    base = score(proposal, config, matrix)
    middle = middle_song_ids(proposal)
    if not middle:
        return base
    overlap = len(middle & used_middle_songs)
    penalty = penalty_weight * (overlap / len(middle))
    return base.model_copy(update={"total": round(max(0.0, base.total - penalty), 4)})
