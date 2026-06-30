"""Harmony and key-distance helpers."""

from __future__ import annotations

import re

NOTE_TO_PC = {
    "C": 0,
    "B#": 0,
    "C#": 1,
    "DB": 1,
    "D": 2,
    "D#": 3,
    "EB": 3,
    "E": 4,
    "FB": 4,
    "E#": 5,
    "F": 5,
    "F#": 6,
    "GB": 6,
    "G": 7,
    "G#": 8,
    "AB": 8,
    "A": 9,
    "A#": 10,
    "BB": 10,
    "B": 11,
    "CB": 11,
}
PC_TO_NOTE = {0: "C", 1: "C#", 2: "D", 3: "Eb", 4: "E", 5: "F", 6: "F#", 7: "G", 8: "Ab", 9: "A", 10: "Bb", 11: "B"}
FIFTH_ORDER = [0, 7, 2, 9, 4, 11, 6, 1, 8, 3, 10, 5]
FIFTH_INDEX = {pc: idx for idx, pc in enumerate(FIFTH_ORDER)}


def normalize_key(raw: str | None) -> tuple[str, str]:
    if not raw:
        return ("C", "maj")
    text = raw.strip().replace("♭", "b").replace("♯", "#")
    match = re.match(r"^([A-Ga-g])([#bB]?)(?:\s*|-)?(maj(?:or)?|min(?:or)?|m|M)?", text)
    if not match:
        return ("C", "maj")
    note = match.group(1).upper() + match.group(2).replace("b", "b").replace("B", "b")
    mode_token = (match.group(3) or "").lower()
    mode = "min" if mode_token in {"m", "min", "minor"} else "maj"
    if note.upper() not in NOTE_TO_PC:
        note = "C"
    return (PC_TO_NOTE[NOTE_TO_PC[note.upper()]], mode)


def pitch_class(note: str | None) -> int:
    normalized, _ = normalize_key(note)
    return NOTE_TO_PC[normalized.upper()]


def transpose_note(note: str, semitones: int) -> str:
    return PC_TO_NOTE[(pitch_class(note) + semitones) % 12]


def relative_major_pc(key: str | None, mode: str | None = None) -> int:
    note, normalized_mode = normalize_key(f"{key or 'C'} {mode or ''}")
    pc = pitch_class(note)
    return (pc + 3) % 12 if normalized_mode == "min" else pc


def fifth_distance_on_circle(a_pc: int, b_pc: int) -> int:
    ai = FIFTH_INDEX[a_pc % 12]
    bi = FIFTH_INDEX[b_pc % 12]
    distance = abs(ai - bi)
    return min(distance, 12 - distance)


def cfd(from_key: str | None, from_mode: str | None, to_key: str | None, to_mode: str | None) -> int:
    return fifth_distance_on_circle(
        relative_major_pc(from_key, from_mode),
        relative_major_pc(to_key, to_mode),
    )


def key_compatibility_score(distance: int) -> float:
    if distance <= 0:
        return 1.0
    if distance == 1:
        return 0.92
    if distance == 2:
        return 0.78
    if distance == 3:
        return 0.55
    if distance == 4:
        return 0.32
    return 0.15


def suggest_key_shift(
    from_key: str | None,
    from_mode: str | None,
    to_key: str | None,
    to_mode: str | None,
) -> tuple[int, int]:
    current = cfd(from_key, from_mode, to_key, to_mode)
    if current <= 2:
        return (0, current)
    to_note, _ = normalize_key(to_key)
    choices = []
    for shift in (-2, -1, 0, 1, 2):
        shifted = transpose_note(to_note, shift)
        choices.append((cfd(from_key, from_mode, shifted, to_mode), abs(shift), shift))
    best_distance, _, best_shift = min(choices)
    return (best_shift, best_distance)
