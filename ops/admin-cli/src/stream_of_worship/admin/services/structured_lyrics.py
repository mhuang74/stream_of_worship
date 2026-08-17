"""Parser for structured (section-tagged) lyrics from YouTube descriptions.

YouTube video descriptions often embed lyrics with explicit section tags
like ``[Verse]``, ``[Pre-Chorus]``, ``[Chorus]``. This module parses those
descriptions into a structured dict and provides a flattening helper that
renders the sections back to a plain-text blob (tags preserved) for use as
the ``lyrics_text`` payload in LRC submission.

When LLM is enabled (default), :func:`parse_structured_lyrics_smart` runs
the heuristic first, then invokes an LLM to clean up the result — dropping
non-lyric junk lines, ensuring section boundaries, and normalizing labels.
The LLM path is fatal on failure (raises ``RuntimeError``); pass
``use_llm=False`` to fall back to the heuristic-only path.
"""

from __future__ import annotations

import os
import re

from pydantic import BaseModel, Field

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


class StructuredLyricsSection(BaseModel):
    """A single section of structured lyrics (e.g. Verse, Chorus)."""

    label: str
    raw_label: str
    lines: list[str] = Field(default_factory=list)

    def to_dict(self) -> dict:
        return {"label": self.label, "raw_label": self.raw_label, "lines": list(self.lines)}


class StructuredLyricsResult(BaseModel):
    """Full structured lyrics result: sections + preamble lines."""

    sections: list[StructuredLyricsSection] = Field(default_factory=list)
    preamble_lines: list[str] = Field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "sections": [s.to_dict() for s in self.sections],
            "preamble_lines": list(self.preamble_lines),
        }


def build_chat_model_for_lyrics():
    """Build an OpenAI-compatible chat model for lyrics cleanup.

    Reads SOW_LLM_API_KEY / SOW_LLM_BASE_URL / SOW_LLM_MODEL from env.
    Raises RuntimeError if LLM env is not configured.
    """
    api_key = os.environ.get("SOW_LLM_API_KEY")
    model = os.environ.get("SOW_LLM_MODEL")
    if not api_key or not model:
        raise RuntimeError(
            "LLM lyrics extraction is enabled but SOW_LLM_API_KEY / "
            "SOW_LLM_MODEL are not set. Either set them or pass --no-llm."
        )
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=model,
        api_key=api_key,
        base_url=os.environ.get("SOW_LLM_BASE_URL"),
        temperature=0.0,
        max_retries=2,
    )


def _build_lyrics_prompt(
    description: str,
    heuristic: dict | None,
    *,
    source_desc: str = "a YouTube video description",
) -> str:
    """Build the LLM prompt for lyrics cleanup.

    ``source_desc`` names the origin of the text (YouTube description by
    default, or a lyrics page) so the prompt reads naturally for either.
    """
    hint = ""
    if heuristic:
        import json

        hint = (
            "\n\nA regex heuristic already produced this candidate parse "
            "(use as a starting hint, correct any errors):\n"
            f"{json.dumps(heuristic, ensure_ascii=False, indent=2)}"
        )
    return (
        f"You are a lyrics parser. Given {source_desc}, extract the structured "
        "song lyrics.\n\n"
        "Identify section boundaries (Verse, Pre-Chorus, Chorus, Bridge, Intro, "
        "Outro, Instrumental, Hook, Refrain, Tag) and assign each non-blank "
        "lyric line to the section it belongs to.\n\n"
        "STRICT RULES:\n"
        "- Keep ALL actual lyric lines verbatim (including Chinese text and "
        "full-width punctuation).\n"
        "- DROP non-lyric junk lines: channel promos, subscribe requests, URLs, "
        "social handles (@...), timestamps, and any other non-lyric noise — "
        "wherever they appear (not just at the end).\n"
        "- Preserve the original section label spelling in raw_label; put the "
        "lowercased normalized form in label.\n"
        "- Lines before the first section tag go into preamble_lines.\n"
        "- If the description contains no section tags and no recognizable "
        "lyrics structure, return an empty sections list.\n"
        "- Do NOT translate, paraphrase, or reorder lyric lines.\n\n"
        "Description to parse:\n"
        "---\n"
        f"{description}\n"
        "---"
        f"{hint}\n"
    )


def extract_structured_lyrics_with_llm(
    description: str | None,
    *,
    source_desc: str = "a YouTube video description",
) -> StructuredLyricsResult | None:
    """Parse a description into structured lyrics using an LLM for cleanup.

    Returns a StructuredLyricsResult or None if the description is empty.
    Raises RuntimeError if LLM env is not configured.
    Raises on LLM call failure (network, malformed JSON) — caller must handle.

    ``source_desc`` names the origin of the text (default: YouTube) so the
    prompt reads naturally for either source.
    """
    if not description:
        return None
    chat = build_chat_model_for_lyrics()
    try:
        structured_chat = chat.with_structured_output(StructuredLyricsResult, method="json_schema")
    except TypeError:
        structured_chat = chat.with_structured_output(
            StructuredLyricsResult, method="function_calling"
        )
    heuristic = parse_structured_lyrics(description)
    prompt = _build_lyrics_prompt(description, heuristic, source_desc=source_desc)
    result = structured_chat.invoke(prompt)
    return result


def parse_structured_lyrics_smart(
    description: str | None,
    *,
    use_llm: bool = True,
    source_desc: str = "a YouTube video description",
) -> dict | None:
    """Parse structured lyrics, preferring LLM cleanup when enabled.

    - use_llm=True (default): runs heuristic, then LLM cleanup. LLM env misconfig /
      call failure is FATAL (raises). Returns the cleaned dict
      (StructuredLyricsResult.to_dict()) or None if the description is empty.
    - use_llm=False: runs the heuristic only. Non-fatal on parse failure
      (returns None).

    ``source_desc`` names the text origin and is passed through to the LLM
    prompt (default: YouTube description).
    """
    if not description:
        return None
    if use_llm:
        result = extract_structured_lyrics_with_llm(description, source_desc=source_desc)
        if result is None:
            return None
        return result.to_dict() or None
    return parse_structured_lyrics(description)


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
