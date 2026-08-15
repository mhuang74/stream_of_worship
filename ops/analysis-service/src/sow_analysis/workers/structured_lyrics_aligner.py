"""LLM-based structured lyrics-to-LRC alignment.

Unlike ``section_segmenter.py`` (which segments LRC from scratch, where the
LLM has to guess labels), this module receives **authoritative section labels
and content** from the YouTube description and only asks the LLM to **align**
them to LRC line indices. This handles line-segmentation mismatches
(merged/split lines, empty lines, whitespace differences) that the
deterministic ``identify_from_structured_lyrics()`` cannot.

The output is the same ``Section`` format as ``section_segmenter.py``, so
``_map_sections_to_components`` works (with modifications per C1/H1/H2).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from openai import OpenAI

from ..config import settings
from .components import ComponentInstance, parse_lrc
from .llm_rate_limit import call_llm_with_retry
from .section_segmenter import (
    _EXPECTED_HELD_OUT_IDS,
    _VALID_LABELS,
    DEFAULT_VALIDATOR_WEIGHTS,
    Section,
    ValidatorWeights,
    _map_sections_to_components,
    _render_numbered_lrc,
    _segmentation_model,
    _validate_chorus_repetition,
)

logger = logging.getLogger(__name__)

# [H5] Label normalization map — applied as a fallback before rejecting
# unknown labels in _parse_alignment_json.
_LABEL_NORMALIZATION_MAP: dict[str, str] = {
    "verse 1": "verse",
    "verse 2": "verse",
    "verse 3": "verse",
    "verse 4": "verse",
    "verse 5": "verse",
    "pre-chorus": "prechorus",
    "prechorus": "prechorus",
    "chorus 1": "chorus",
    "chorus 2": "chorus",
    "chorus 3": "chorus",
    "hook": "chorus",
    "refrain": "chorus",
    "tag": "chorus",
    "bridge": "bridge",
    "intro": "intro",
    "outro": "outro",
    "instrumental": "instrumental",
}


def _build_client() -> OpenAI:
    """OpenAI client with the structured lyrics alignment timeout."""
    if not settings.SOW_LLM_API_KEY:
        raise ValueError("SOW_LLM_API_KEY environment variable not set.")
    if not settings.SOW_LLM_BASE_URL:
        raise ValueError("SOW_LLM_BASE_URL environment variable not set.")
    return OpenAI(
        api_key=settings.SOW_LLM_API_KEY,
        base_url=settings.SOW_LLM_BASE_URL,
        timeout=settings.SOW_LLM_STRUCTURED_LYRICS_TIMEOUT_SECONDS,
        max_retries=0,
    )


def _render_structured_sections(structured_lyrics_json: str) -> str:
    """Render structured lyrics JSON into a readable text block for the prompt.

    Output format:
        [Verse]
        祢的話在我心
        使我腳步不偏離
        ...

        [Chorus]
        主啊 我要跟隨祢
        將我一生獻給祢
        ...
    """
    try:
        structured = json.loads(structured_lyrics_json)
    except (json.JSONDecodeError, TypeError):
        return "(invalid structured lyrics JSON)"
    sections = structured.get("sections", [])
    if not sections:
        return "(no sections in structured lyrics)"
    blocks: list[str] = []
    for sec in sections:
        label = sec.get("label", sec.get("raw_label", "unknown"))
        lines = sec.get("lines", [])
        block_lines = [f"[{label}]"]
        block_lines.extend(lines)
        blocks.append("\n".join(block_lines))
    return "\n\n".join(blocks)


def _build_alignment_prompt(
    lrc_content: str,
    structured_lyrics_json: str,
    few_shot_examples: list[dict],
) -> list[dict]:
    """Construct the system + user messages for the alignment LLM call."""
    numbered, _n_lines = _render_numbered_lrc(lrc_content)
    structured_text = _render_structured_sections(structured_lyrics_json)

    system = (
        "You are a Chinese worship-music structure analyst. Given a numbered LRC "
        "lyric file AND structured lyrics sections (from the YouTube video "
        "description), map each section to a range of LRC line numbers. Return a "
        "JSON object with a single key 'sections'. Each section has: label (one of "
        "intro, verse, prechorus, chorus, bridge, outro, instrumental), line_start "
        "(1-based, inclusive), line_end (1-based, inclusive), confidence (0.0-1.0), "
        "and a short rationale. "
        "**Normalize all labels to exactly one of: intro, verse, prechorus, chorus, "
        "bridge, outro, instrumental.** Drop numbers (Verse 1 -> verse), convert "
        "hyphens (pre-chorus -> prechorus), map synonyms (hook/refrain/tag -> "
        "chorus). Sections may repeat (e.g. a Chorus appearing 3 times -> 3 "
        "sections with different line ranges). Sections must be non-overlapping. "
        "It is OK to skip LRC lines that belong to interludes or sections not in "
        "the structured lyrics. Respond with JSON only."
    )

    user_parts: list[str] = []
    user_parts.append("Here are a few reference examples of correct output:")
    for ex in few_shot_examples:
        user_parts.append(ex["input"])
        user_parts.append("Expected output:")
        user_parts.append(json.dumps({"sections": ex["sections"]}, ensure_ascii=False))
    user_parts.append("Now align the structured lyrics sections to this numbered LRC:")
    user_parts.append(numbered)
    user_parts.append("Structured lyrics sections to align:")
    user_parts.append(structured_text)
    user_parts.append("Output JSON only:")

    user = "\n".join(user_parts)
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _parse_alignment_json(
    response_text: str, n_lines: int
) -> tuple[Optional[list[Section]], str]:
    """Relaxed parser for the alignment LLM response.

    Unlike ``_parse_segmenter_json``, this parser:
    - Sorts sections by ``line_start`` before overlap checking. [H4]
    - Checks overlap against ALL accepted sections (not just previous). [H4]
    - Allows gaps between sections (relaxed contiguity). [H4]
    - Applies ``_LABEL_NORMALIZATION_MAP`` as a fallback before rejecting
      unknown labels. [H5]

    Returns:
        ``(sections, breakdown)`` where ``sections`` is ``None`` if parsing
        failed, and ``breakdown`` is a human-readable string explaining the
        failure or success summary.
    """
    try:
        data = json.loads(response_text)
    except (json.JSONDecodeError, TypeError) as e:
        return None, f"JSON decode failed: {e}"

    if not isinstance(data, dict):
        return None, f"Response is not a JSON object (type={type(data).__name__})"

    sections_list = data.get("sections")
    if not isinstance(sections_list, list):
        return None, "'sections' key missing or not a list"
    if not sections_list:
        return None, "'sections' array is empty"

    # Track rejection reasons
    rejected_reasons: list[str] = []
    raw_sections: list[Section] = []

    for i, raw in enumerate(sections_list):
        if not isinstance(raw, dict):
            rejected_reasons.append(f"section[{i}]: not a dict")
            continue
        label = str(raw.get("label", "")).lower().strip()
        original_label = label
        # [H5] Apply normalization fallback before rejecting.
        if label not in _VALID_LABELS:
            label = _LABEL_NORMALIZATION_MAP.get(label, label)
        if label not in _VALID_LABELS:
            rejected_reasons.append(
                f"section[{i}]: invalid label '{original_label}'"
            )
            continue
        try:
            line_start = int(raw["line_start"])
            line_end = int(raw["line_end"])
            confidence = float(raw.get("confidence", 0.5))
        except (KeyError, TypeError, ValueError) as e:
            rejected_reasons.append(
                f"section[{i}] label='{label}': missing/invalid "
                f"line_start/line_end/confidence ({e})"
            )
            continue
        if not (1 <= line_start <= line_end <= n_lines):
            rejected_reasons.append(
                f"section[{i}] label='{label}': out of range "
                f"(line_start={line_start}, line_end={line_end}, n_lines={n_lines})"
            )
            continue
        raw_sections.append(
            Section(
                label=label,
                line_start=line_start,
                line_end=line_end,
                confidence=max(0.0, min(1.0, confidence)),
                rationale=raw.get("rationale"),
            )
        )

    if not raw_sections:
        return None, (
            f"All {len(sections_list)} sections rejected: "
            f"{'; '.join(rejected_reasons)}"
        )

    # [H4] Sort by line_start before overlap checking.
    raw_sections.sort(key=lambda s: s.line_start)

    # [H4] Overlap detection against ALL accepted sections (gaps allowed).
    accepted: list[Section] = []
    seen_ranges: set[tuple[int, int]] = set()
    overlap_rejects = 0
    dup_rejects = 0
    for sec in raw_sections:
        # Check overlap against all accepted sections.
        overlaps = False
        for acc in accepted:
            if sec.line_start <= acc.line_end and sec.line_end >= acc.line_start:
                overlaps = True
                break
        if overlaps:
            overlap_rejects += 1
            rejected_reasons.append(
                f"section label='{sec.label}' lines={sec.line_start}-{sec.line_end}: "
                f"overlaps existing"
            )
            continue
        if (sec.line_start, sec.line_end) in seen_ranges:
            dup_rejects += 1
            rejected_reasons.append(
                f"section label='{sec.label}' lines={sec.line_start}-{sec.line_end}: "
                f"duplicate range"
            )
            continue
        accepted.append(sec)
        seen_ranges.add((sec.line_start, sec.line_end))

    if not accepted:
        return None, (
            f"All {len(raw_sections)} post-sort sections rejected "
            f"(overlaps={overlap_rejects}, duplicates={dup_rejects}): "
            f"{'; '.join(rejected_reasons)}"
        )

    breakdown = (
        f"Parsed {len(accepted)} sections from {len(sections_list)} raw "
        f"({len(rejected_reasons)} rejected"
        + (f": {'; '.join(rejected_reasons)}" if rejected_reasons else "")
        + ")"
    )
    return accepted, breakdown


def _load_alignment_few_shot_examples() -> list[dict]:
    """Load alignment few-shot examples from the committed JSON file.

    Each example must include ``source_song_id`` so the loader can assert
    it does not come from any of the 3 fixture evaluation songs (held-out).
    [H3] Asserts ``source_song_id`` is not in ``_EXPECTED_HELD_OUT_IDS``.
    """
    few_shot_path = Path(__file__).parent / "structured_lyrics_alignment_few_shot.json"
    if not few_shot_path.exists():
        logger.warning(
            "Alignment few-shot examples file not found at %s; "
            "running with zero examples.",
            few_shot_path,
        )
        return []
    try:
        examples = json.loads(few_shot_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to load alignment few-shot examples: %s", e)
        return []
    if not isinstance(examples, list):
        logger.warning("Alignment few-shot examples file is not a list; ignoring.")
        return []
    for ex in examples:
        song_id = str(ex.get("source_song_id", "")).strip()
        if song_id in _EXPECTED_HELD_OUT_IDS:
            raise ValueError(
                f"Alignment few-shot example source_song_id '{song_id}' is a "
                f"held-out fixture evaluation song. Remove this example from "
                f"structured_lyrics_alignment_few_shot.json to prevent test-set "
                f"leakage."
            )
    return examples


async def align_structured_lyrics(
    structured_lyrics_json: str,
    lrc_content: str,
    beats: Optional[list[float]] = None,
    downbeats: Optional[list[float]] = None,
    snap_to_downbeat: bool = False,
    song_total_duration: Optional[float] = None,  # [H2]
    weights: ValidatorWeights = DEFAULT_VALIDATOR_WEIGHTS,
) -> list[ComponentInstance]:
    """Identify components by LLM-aligning structured lyrics sections to LRC lines.

    Unlike ``section_segmenter.py`` (which segments LRC from scratch), this
    function receives authoritative section labels from the YouTube description
    and only asks the LLM to map each section to a range of LRC line indices.
    This handles line-segmentation mismatches (merged/split lines, empty lines,
    whitespace differences) that the deterministic
    ``identify_from_structured_lyrics()`` cannot.

    Returns ComponentInstance list with source='structured_lyrics_llm'.
    """
    client = _build_client()
    model = _segmentation_model()
    few_shot = _load_alignment_few_shot_examples()
    messages = _build_alignment_prompt(lrc_content, structured_lyrics_json, few_shot)

    system_prompt = messages[0]["content"]
    user_message = messages[1]["content"]
    _numbered, n_lines = _render_numbered_lrc(lrc_content)
    structured_text = _render_structured_sections(structured_lyrics_json)
    few_shot_chars = sum(
        len(json.dumps(ex, ensure_ascii=False)) for ex in few_shot
    )
    logger.debug(
        "LLM request [LLM structured lyrics alignment]: model=%s, "
        "system_prompt=%d chars, user_message=%d chars "
        "(few_shot: %d examples, ~%d chars; numbered_lrc: %d lines, %d chars; "
        "structured_sections: %d chars)",
        model,
        len(system_prompt),
        len(user_message),
        len(few_shot),
        few_shot_chars,
        n_lines,
        len(_numbered),
        len(structured_text),
    )
    logger.debug(
        "LLM request [LLM structured lyrics alignment] user message:\n%s",
        user_message,
    )

    def _call() -> str:
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0,
            response_format={"type": "json_object"},
            max_tokens=settings.SOW_LLM_SEGMENTATION_MAX_TOKENS,
        )
        usage = resp.usage
        logger.debug(
            "LLM response [LLM structured lyrics alignment]: model=%s, "
            "finish_reason=%s, prompt_tokens=%s, completion_tokens=%s, "
            "total_tokens=%s, response_id=%s, content_length=%d chars",
            resp.model,
            resp.choices[0].finish_reason,
            usage.prompt_tokens if usage else "N/A",
            usage.completion_tokens if usage else "N/A",
            usage.total_tokens if usage else "N/A",
            resp.id,
            len(resp.choices[0].message.content or ""),
        )
        logger.debug(
            "LLM response [LLM structured lyrics alignment] content:\n%s",
            resp.choices[0].message.content or "",
        )
        return resp.choices[0].message.content or ""

    text = await call_llm_with_retry(
        _call, description="LLM structured lyrics alignment"
    )
    sections, parse_breakdown = _parse_alignment_json(text, n_lines)
    if sections is None:
        logger.warning(
            "Structured lyrics alignment parse failed: %s", parse_breakdown
        )
        return []
    logger.debug(
        "Structured lyrics alignment parse: %s", parse_breakdown
    )
    # Defensive post-processing: chorus repetition cross-check.
    sections = _validate_chorus_repetition(sections, lrc_content, weights=weights)
    lines = list(parse_lrc(lrc_content).lines)
    return _map_sections_to_components(
        sections,
        lines,
        beats=beats,
        downbeats=downbeats,
        snap_to_downbeat=snap_to_downbeat,
        weights=weights,
        source="structured_lyrics_llm",  # [C1]
        song_total_duration=song_total_duration,  # [H2]
    )
