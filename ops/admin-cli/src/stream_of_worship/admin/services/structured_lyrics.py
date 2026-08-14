"""Parser for structured (section-tagged) lyrics from YouTube descriptions.

YouTube video descriptions often embed lyrics with explicit section tags
like ``[Verse]``, ``[Pre-Chorus]``, ``[Chorus]``. This module parses those
descriptions into a structured dict and provides a flattening helper that
renders the sections back to a plain-text blob (tags preserved) for use as
the ``lyrics_text`` payload in LRC submission.
"""

from __future__ import annotations

import re

_SECTION_TAG_RE = re.compile(r"^\[(?P<label>[^\]]+)\]\s*$")

_RECOGNISED_LABELS = frozenset(
    {
        "verse",
        "verse 1",
        "verse 2",
        "verse 3",
        "verse 4",
        "verse 5",
        "pre-chorus",
        "prechorus",
        "chorus",
        "chorus 1",
        "chorus 2",
        "chorus 3",
        "bridge",
        "intro",
        "outro",
        "instrumental",
        "hook",
        "refrain",
        "tag",
    }
)

_TRAILING_NON_LYRIC_HINTS = ("http", "www.", "@", "粉絲", "訂閱", "subscribe", "▶")


def _is_trailing_non_lyric(line: str) -> bool:
    """Heuristic: line looks like a promo/link line, not lyrics."""
    lower = line.lower()
    return any(hint in lower for hint in _TRAILING_NON_LYRIC_HINTS)


def parse_structured_lyrics(description: str | None) -> dict | None:
    """Parse a YouTube description into structured lyric sections.

    Returns ``{"sections": [{"label": str, "raw_label": str, "lines": [str]}], "preamble_lines": [str]}``
    or ``None`` if the description has no section tags.

    Parsing rules:
    1. Split on newlines; strip trailing ``\\r``.
    2. Section-tag lines match ``^\\[<label>\\]$`` (case-insensitive).
       Unrecognised bracket-only lines are still treated as section headers
       so lyric content is never dropped.
    3. Lines before the first section tag go into ``preamble_lines``.
    4. Each subsequent non-empty, non-tag line appends to the current
       section's ``lines``. Blank lines between sections are dropped.
    5. Trailing non-lyric lines (URLs, promo) after a blank-line gap from
       the last section's lyric block are excluded.
    6. Returns ``None`` when zero section tags are present.
    """
    if not description:
        return None

    lines = description.split("\n")
    lines = [l.rstrip("\r") for l in lines]

    preamble_lines: list[str] = []
    sections: list[dict] = []
    current: dict | None = None
    in_trailing_gap = False

    for raw_line in lines:
        stripped = raw_line.strip()
        tag_match = _SECTION_TAG_RE.match(stripped)

        if tag_match:
            raw_label = tag_match.group("label")
            label = raw_label.strip().lower()
            current = {
                "label": label,
                "raw_label": raw_label.strip(),
                "lines": [],
            }
            sections.append(current)
            in_trailing_gap = False
            continue

        if current is None:
            if stripped:
                preamble_lines.append(stripped)
            continue

        if not stripped:
            if current["lines"]:
                in_trailing_gap = True
            continue

        if in_trailing_gap and _is_trailing_non_lyric(stripped):
            continue

        in_trailing_gap = False
        current["lines"].append(stripped)

    if not sections:
        return None

    return {
        "sections": sections,
        "preamble_lines": preamble_lines,
    }


def flatten_structured_lyrics(structured: dict) -> str:
    """Render structured sections to a single lyrics_text blob, tags preserved.

    Each section emits its ``[Label]`` header line followed by its lyric
    lines, separated by single newlines. Blank lines between sections are
    omitted. Preamble lines are NOT included.
    """
    sections = structured.get("sections", [])
    if not sections:
        return ""

    out: list[str] = []
    for section in sections:
        raw_label = section.get("raw_label") or section.get("label", "")
        out.append(f"[{raw_label}]")
        out.extend(section.get("lines", []))
    return "\n".join(out)
