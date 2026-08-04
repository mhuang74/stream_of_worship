#!/usr/bin/env python3
"""Voice-type → comfortable tonic-PC mapping and free-form range parser.

Comfortable tonic PCs are pitch-class numbers (0-11) per NOTE_TO_PC in
``harmony.py``. The "comfortable" set is the set of tonic PCs where a
song's relative-major tonic falls within the leader's singable range.

Usage:
    from voice_ranges import resolve_leader_range

    result = resolve_leader_range("normal male")
    # {"comfortable_pcs": [0, 1, 2, 3, 4, 5], "label": "normal male"}

    result = resolve_leader_range("A2 to G4")
    # {"comfortable_pcs": [...], "label": "A2-G4"}

    result = resolve_leader_range("alto")
    # {"comfortable_pcs": [9, 10, 11, 0, 2], "label": "low female"}
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
ADMIN_CLI_SRC = PROJECT_ROOT / "ops" / "admin-cli" / "src"
if str(ADMIN_CLI_SRC) not in sys.path:
    sys.path.insert(0, str(ADMIN_CLI_SRC))

from stream_of_worship.admin.songset_constructor.rules.harmony import NOTE_TO_PC, pitch_class

VOICE_TYPE_PCS: dict[str, dict] = {
    "normal male": {
        "comfortable_pcs": [0, 1, 2, 3, 4, 5],
        "label": "normal male",
        "approx_range": "A2-E4",
    },
    "low male": {
        "comfortable_pcs": [8, 9, 10, 11, 0, 1],
        "label": "low male",
        "approx_range": "E2-B3",
    },
    "high male": {
        "comfortable_pcs": [2, 3, 4, 5, 6, 7],
        "label": "high male",
        "approx_range": "C3-G4",
    },
    "normal female": {
        "comfortable_pcs": [0, 2, 3, 4, 5, 7],
        "label": "normal female",
        "approx_range": "G3-E5",
    },
    "low female": {
        "comfortable_pcs": [9, 10, 11, 0, 2],
        "label": "low female",
        "approx_range": "E3-B4",
    },
    "high female": {
        "comfortable_pcs": [2, 4, 5, 7, 9],
        "label": "high female",
        "approx_range": "C4-G5",
    },
}

ALIASES: dict[str, str] = {
    "baritone": "normal male",
    "tenor-baritone": "normal male",
    "bass": "low male",
    "bass-baritone": "low male",
    "tenor": "high male",
    "mezzo": "normal female",
    "mezzo-soprano": "normal female",
    "alto": "low female",
    "soprano": "high female",
}

DEFAULT_VOICE_TYPE = "normal male"

_NOTE_RE = re.compile(r"^([A-Ga-g])([#bB]?)(\d)$")


def _parse_note_to_pc(note_str: str) -> int | None:
    match = _NOTE_RE.match(note_str.strip())
    if not match:
        return None
    letter = match.group(1).upper()
    accidental = match.group(2).replace("B", "b").replace("b", "b")
    note = letter + accidental
    if note.upper() not in NOTE_TO_PC:
        return None
    return pitch_class(note)


def _circular_pc_set_within(low_pc: int, high_pc: int, spread: int = 7) -> list[int]:
    """Compute comfortable tonic PCs within ``spread`` semitones of the tessitura.

    A tonic PC is "comfortable" if it is within a perfect fifth (7 semitones)
    below the leader's high note AND within a perfect fifth above the leader's
    low note (circular PC arithmetic).
    """
    result: list[int] = []
    for pc in range(12):
        dist_below_high = min(abs(pc - high_pc) % 12, 12 - abs(pc - high_pc) % 12)
        dist_above_low = min(abs(pc - low_pc) % 12, 12 - abs(pc - low_pc) % 12)
        if dist_below_high <= spread and dist_above_low <= spread:
            result.append(pc)
    return sorted(result)


def _parse_freeform_range(text: str) -> dict | None:
    """Parse a free-form range string like 'A2 to G4' or 'G2-E4'.

    Returns ``{"comfortable_pcs": [...], "label": "A2-G4"}`` or ``None``.
    """
    cleaned = text.strip().replace("–", "-").replace("—", "-")
    parts = re.split(r"\s*(?:to|–|-|—)\s*", cleaned, maxsplit=1)
    if len(parts) != 2:
        return None
    low_pc = _parse_note_to_pc(parts[0])
    high_pc = _parse_note_to_pc(parts[1])
    if low_pc is None or high_pc is None:
        return None
    comfortable = _circular_pc_set_within(low_pc, high_pc, spread=7)
    label = f"{parts[0].strip()}-{parts[1].strip()}"
    return {"comfortable_pcs": comfortable, "label": label}


def resolve_leader_range(user_input: str) -> dict:
    """Resolve a user-provided voice description to comfortable tonic PCs.

    Accepts:
    - Voice-type labels: "normal male", "low male", "high male",
      "normal female", "low female", "high female"
    - Aliases: "baritone", "tenor", "bass", "alto", "soprano", etc.
    - Free-form ranges: "A2 to G4", "G2-E4", "C3 to G4"

    Falls back to "normal male" if parsing fails.
    """
    cleaned = user_input.strip().lower()

    if cleaned in VOICE_TYPE_PCS:
        entry = VOICE_TYPE_PCS[cleaned]
        return {
            "comfortable_pcs": list(entry["comfortable_pcs"]),
            "label": entry["label"],
        }

    if cleaned in ALIASES:
        key = ALIASES[cleaned]
        entry = VOICE_TYPE_PCS[key]
        return {
            "comfortable_pcs": list(entry["comfortable_pcs"]),
            "label": entry["label"],
        }

    freeform = _parse_freeform_range(user_input)
    if freeform is not None:
        return freeform

    entry = VOICE_TYPE_PCS[DEFAULT_VOICE_TYPE]
    return {
        "comfortable_pcs": list(entry["comfortable_pcs"]),
        "label": f"{DEFAULT_VOICE_TYPE} (fallback — could not parse '{user_input}')",
    }


def circular_pc_distance(a: int, b: int) -> int:
    """Chromatic distance between two pitch classes on the circle."""
    d = abs(a - b) % 12
    return min(d, 12 - d)
