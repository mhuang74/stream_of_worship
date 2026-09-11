"""Component-row aggregation for the songset constructor.

Pure-Python aggregation of ``song_components`` rows into SongCandidate field
updates.  No admin command imports (package boundary: the constructor reads
the DB only via its own ``db.py``); mirrors the chorus-preference weighting of
``_aggregate_recording_theme`` conceptually, not by import.
"""

from __future__ import annotations

from stream_of_worship.admin.songset_constructor.rules.themes import THEMES

# Component-row tuple layout (mirrors COMPONENT_ROWS_QUERY in db.py):
# 0: song_id         5: bpm               10: theme_confidence
# 1: role            6: key               11: vocal_posture
# 2: component_type  7: key_confidence    12: vocal_posture_confidence
# 3: occurrence_index 8: energy_level
# 4: id              9: theme
THEME_CHORUS_WEIGHT = 1.0
THEME_NON_CHORUS_WEIGHT = 0.5

# Liturgical posture→phase fit (rows: posture, cols: phase 1..5).
# Direct address (To God) peaks in Worship/Response; testimony (About God) fits
# Call and Commission; congregational exhortation (To Congregation) fits
# Call/Response/Commission but is not Worship.
POSTURE_PHASE_FIT: dict[str, dict[int, float]] = {
    "To God": {1: 0.5, 2: 1.0, 3: 1.0, 4: 1.0, 5: 0.5},
    "About God": {1: 1.0, 2: 0.5, 3: 0.5, 4: 0.0, 5: 1.0},
    "To Congregation": {1: 1.0, 2: 0.0, 3: 0.5, 4: 1.0, 5: 1.0},
}

POSTURES = ("To God", "About God", "To Congregation")


def _boundary_row(rows: list[tuple], roles: set[str]) -> tuple | None:
    """Lowest occurrence_index, then lowest id — the deterministic tiebreak."""
    candidates = [row for row in rows if row[1] in roles]
    if not candidates:
        return None
    return min(candidates, key=lambda row: (row[3], row[4]))



def _weighted_distribution(
    rows: list[tuple], value_index: int, confidence_index: int, vocab: tuple[str, ...]
) -> dict[str, float] | None:
    votes: dict[str, float] = {value: 0.0 for value in vocab}
    for row in rows:
        value = row[value_index]
        if value is None:
            continue
        weight = (
            THEME_CHORUS_WEIGHT if row[2] == "chorus" else THEME_NON_CHORUS_WEIGHT
        ) * (row[confidence_index] or 0.0)
        votes[str(value)] += weight
    total = sum(votes.values())
    if total <= 0:
        return None
    return {value: weight / total for value, weight in votes.items()}


def aggregate_components(rows: list[tuple], *, musical_mode: str | None = None) -> dict:
    """Aggregate one song's component rows into SongCandidate field updates.

    Returns {} when rows is empty (song has no components).
    Output keys: has_components, entry_bpm, entry_key, entry_mode, entry_key_confidence,
    entry_energy_level_db, exit_bpm, exit_key, exit_mode, exit_key_confidence,
    exit_energy_level_db, component_theme_scores, component_posture, component_posture_confidence.
    """
    if not rows:
        return {}

    entry_row = _boundary_row(rows, {"entry", "entry_exit"})
    exit_row = _boundary_row(rows, {"exit", "entry_exit"})

    theme_scores = _weighted_distribution(rows, 9, 10, vocab=THEMES)
    posture_scores = _weighted_distribution(rows, 11, 12, vocab=POSTURES)
    posture: str | None = None
    posture_confidence: float | None = None
    if posture_scores:
        posture = max(posture_scores.items(), key=lambda item: (item[1], item[0]))[0]
        # Weighted-average confidence of the rows that voted for the argmax posture:
        # Σ(base_weight · confidence) / Σ(base_weight) over the winner's rows.
        base_weights = [
            THEME_CHORUS_WEIGHT if row[2] == "chorus" else THEME_NON_CHORUS_WEIGHT
            for row in rows
            if row[11] == posture
        ]
        confidences = [row[12] or 0.0 for row in rows if row[11] == posture]
        base_total = sum(base_weights)
        posture_confidence = (
            sum(w * c for w, c in zip(base_weights, confidences)) / base_total if base_total > 0 else None
        )

    def _boundary_fields(row: tuple | None, prefix: str) -> dict:
        if row is None:
            return {}
        return {
            f"{prefix}_bpm": row[5],
            f"{prefix}_key": row[6],
            # Component rows store bare note names only — copy the recording-level
            # mode; a None mode makes normalize_key treat the key as major.
            f"{prefix}_mode": musical_mode,
            f"{prefix}_key_confidence": row[7],
            f"{prefix}_energy_level_db": row[8],
        }

    update: dict = {"has_components": True}
    update.update(_boundary_fields(entry_row, "entry"))
    update.update(_boundary_fields(exit_row, "exit"))
    if theme_scores is not None:
        update["component_theme_scores"] = theme_scores
    if posture is not None:
        update["component_posture"] = posture
        update["component_posture_confidence"] = posture_confidence
    return update