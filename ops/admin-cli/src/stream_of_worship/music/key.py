"""Musical key parsing and pitch-class normalization."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal, Optional

KeyStatus = Literal["ok", "range", "missing", "unparseable"]
KeyMode = Literal["major", "minor", "unknown"]

_PITCH_CLASS = {
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

_TOKEN_RE = re.compile(
    r"^\s*(?P<root>[A-Ga-g])(?P<accidental>[#♯b♭]?)(?:\s*(?P<mode>m|minor|major|小調|大調))?\s*$",
    re.IGNORECASE,
)
_RANGE_RE = re.compile(r"\s*(?:-|→|~)\s*")


@dataclass(frozen=True)
class ParsedMusicalKey:
    raw: str
    status: KeyStatus
    display: str
    root: Optional[str]
    mode: KeyMode
    start_root: Optional[str]
    end_root: Optional[str]
    pitch_class: Optional[int]
    start_pitch_class: Optional[int]
    end_pitch_class: Optional[int]


def _normalize_text(value: object) -> str:
    if value is None:
        return ""
    return unicodedata.normalize("NFKC", str(value)).strip()


def _parse_token(token: str) -> tuple[str, KeyMode, int] | None:
    match = _TOKEN_RE.match(token)
    if not match:
        return None
    root = match.group("root").upper()
    accidental = (match.group("accidental") or "").replace("♯", "#").replace("♭", "b")
    display_root = f"{root}{accidental}"
    pitch_class = _PITCH_CLASS.get(display_root.upper())
    if pitch_class is None:
        return None
    raw_mode = (match.group("mode") or "").lower()
    mode: KeyMode = "minor" if raw_mode in {"m", "minor", "小調"} else "major"
    return display_root, mode, pitch_class


def parse_musical_key(value: object) -> ParsedMusicalKey:
    """Parse a catalog or detected musical key into normalized fields."""

    raw = _normalize_text(value)
    if not raw:
        return ParsedMusicalKey(raw="", status="missing", display="", root=None, mode="unknown",
                                start_root=None, end_root=None, pitch_class=None,
                                start_pitch_class=None, end_pitch_class=None)

    tokens = [token for token in _RANGE_RE.split(raw) if token.strip()]
    if not tokens:
        return ParsedMusicalKey(raw=raw, status="missing", display="", root=None, mode="unknown",
                                start_root=None, end_root=None, pitch_class=None,
                                start_pitch_class=None, end_pitch_class=None)

    parsed = [_parse_token(token) for token in tokens]
    if any(item is None for item in parsed):
        return ParsedMusicalKey(raw=raw, status="unparseable", display=raw, root=None,
                                mode="unknown", start_root=None, end_root=None,
                                pitch_class=None, start_pitch_class=None, end_pitch_class=None)

    first = parsed[0]
    last = parsed[-1]
    assert first is not None and last is not None
    status: KeyStatus = "range" if len(parsed) > 1 else "ok"
    start_root, mode, start_pc = first
    end_root, _, end_pc = last
    display = start_root if status == "ok" else f"{start_root} → {end_root}"
    return ParsedMusicalKey(
        raw=raw,
        status=status,
        display=display,
        root=start_root,
        mode=mode,
        start_root=start_root,
        end_root=end_root,
        pitch_class=start_pc,
        start_pitch_class=start_pc,
        end_pitch_class=end_pc,
    )


def pitch_class(value: object) -> Optional[int]:
    """Return the entry pitch class for a key string, if parseable."""

    return parse_musical_key(value).pitch_class

