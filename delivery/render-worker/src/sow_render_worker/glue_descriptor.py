"""Canonical Glue descriptor identity.

Mirror of ``delivery/webapp/src/lib/glue/canonical.ts`` and
``delivery/webapp/src/lib/glue/descriptor.ts`` (spec:
specs/inpaint-transition-elevenlabs-v2.md §2.2). Both languages must emit
byte-identical canonical JSON and sha256 keys; the shared fixture
``delivery/webapp/src/test/fixtures/glue-canonical.cross_language.json`` is
asserted by both test suites.

Numbers are formatted from integer micro-units (never ``str(float)``): an
integer-valued number serializes as ``12`` (not ``12.0``), ``-0`` becomes
``0``, and an exponent is never emitted.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from typing import Any

DESCRIPTOR_VERSION = 2
DESCRIPTOR_MODEL = "music_v2_5"
DESCRIPTOR_PROMPT_VERSION = 2
DESCRIPTOR_EXCERPT_SECONDS = 12
GLUE_BEATS_CHOICES = (2, 4, 8, 16)

_MICRO_UNITS = 1_000_000
_MAX_EXACT_MICRO = 2**53 - 1


def canonical_six_dp(value: float) -> float:
    """Round to six decimals, half up, and return the canonical float.

    Raises ``ValueError`` for non-finite values (or values whose micro-units
    exceed the exactly representable integer range).
    """
    return _exact_micro_units(value) / _MICRO_UNITS


def canonical_number_text(value: float) -> str:
    """Canonical text: sign, integer part, and a zero-stripped fraction."""
    micro = _exact_micro_units(value)
    sign = "-" if micro < 0 else ""
    whole, fraction = divmod(abs(micro), _MICRO_UNITS)
    if fraction == 0:
        return f"{sign}{whole}"
    digits = f"{fraction:06d}".rstrip("0")
    return f"{sign}{whole}.{digits}"


def canonical_json(value: Any) -> str:
    """Compact JSON in insertion order, with raw non-ASCII text."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return canonical_number_text(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return "[" + ",".join(canonical_json(entry) for entry in value) + "]"
    if isinstance(value, Mapping):
        parts: list[str] = []
        for key, entry in value.items():
            if not isinstance(key, str):
                raise TypeError("canonical_json keys must be strings")
            parts.append(f"{json.dumps(key, ensure_ascii=False)}:{canonical_json(entry)}")
        return "{" + ",".join(parts) + "}"
    raise TypeError(f"canonical_json: unsupported value type {type(value).__name__}")


def build_descriptor(
    *,
    beats: int,
    to_tempo_bpm: float,
    from_side: Mapping[str, Any],
    to_side: Mapping[str, Any],
) -> tuple[dict[str, Any], str, str]:
    """Build the canonical descriptor, its canonical JSON, and the sha256 key.

    ``*_side`` mappings use the canonical payload keys ``contentHash`` and
    ``steps``; ``steps=None`` stays ``None`` — never a full-range step.
    """
    descriptor: dict[str, Any] = {
        "v": DESCRIPTOR_VERSION,
        "model": DESCRIPTOR_MODEL,
        "promptVersion": DESCRIPTOR_PROMPT_VERSION,
        "excerptSeconds": DESCRIPTOR_EXCERPT_SECONDS,
        "beats": _resolve_beats(beats),
        "toTempoBpm": canonical_six_dp(to_tempo_bpm),
        "from": _resolve_side(from_side),
        "to": _resolve_side(to_side),
    }
    canonical = canonical_json(descriptor)
    key = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return descriptor, canonical, key


def _exact_micro_units(value: float) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"canonical number must be numeric, received {type(value).__name__}")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"canonical number must be finite, received {value!r}")
    # floor(v + 0.5) matches JavaScript Math.round (both round .5 toward
    # +Infinity) in canonical.ts.
    micro = math.floor(number * _MICRO_UNITS + 0.5)
    if abs(micro) > _MAX_EXACT_MICRO:
        raise ValueError(f"canonical number exceeds six-decimal precision: {value!r}")
    return micro


def _resolve_beats(beats: int) -> int:
    if beats not in GLUE_BEATS_CHOICES:
        raise ValueError(f"Glue descriptor beats must be one of 2, 4, 8, 16; received {beats!r}")
    return beats


def _resolve_side(side: Mapping[str, Any]) -> dict[str, Any]:
    content_hash = side.get("contentHash")
    if not isinstance(content_hash, str) or not content_hash:
        raise ValueError("Glue descriptor side requires a non-empty contentHash")
    raw_steps = side.get("steps")
    steps: list[list[float]] | None
    if raw_steps is None:
        steps = None
    else:
        steps = [[canonical_six_dp(step[0]), canonical_six_dp(step[1])] for step in raw_steps]
    return {"contentHash": content_hash, "steps": steps}


def milliseconds_to_frames(milliseconds: int) -> int:
    """Map the requested duration to 44.1 kHz PCM frames as in staging."""
    return int(math.floor((milliseconds / 1000.0) * 44100 + 0.5))


def generation_duration_ms(beats: int, to_tempo_bpm: float) -> int:
    """Glue length in beats of the incoming tempo, floored at the API minimum."""
    if isinstance(beats, bool) or not isinstance(beats, int) or beats <= 0:
        raise ValueError(f"Glue beats must be a positive integer; received {beats!r}")
    if isinstance(to_tempo_bpm, bool) or not isinstance(to_tempo_bpm, (int, float)):
        raise TypeError("Glue incoming tempo must be numeric")
    tempo = float(to_tempo_bpm)
    if tempo <= 0:
        raise ValueError(f"Glue incoming tempo must be positive; received {to_tempo_bpm!r}")
    # Half-up milliseconds, matching the repository's canonical rounding (never
    # Python's half-even round) so the request is deterministic.
    return int(math.floor(max(3.0, beats * 60.0 / tempo) * 1000 + 0.5))
