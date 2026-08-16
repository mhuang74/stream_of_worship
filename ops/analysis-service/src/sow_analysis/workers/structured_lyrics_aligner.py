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
from .components import (
    ComponentInstance,
    _lines_match,
    _normalize_for_matching,
    parse_lrc,
)
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
        "the structured lyrics. "
        "**Line-count mismatch is expected.** The LRC may merge two short structured "
        "lines into one LRC line, or split one long structured line into two LRC "
        "lines. Do NOT preserve line-count equality between structured sections and "
        "LRC ranges. Instead, match by lyric CONTENT: a section's line_start must "
        "point to the LRC line whose text matches (fuzzy) the section's FIRST "
        "structured line, and line_end must point to the LRC line whose text matches "
        "(fuzzy) the section's LAST structured line. If the structured section has 5 "
        "lines but the LRC only has 4 lines covering that content, line_end must "
        "still equal the 4th LRC line — not the 5th. "
        "Respond with JSON only."
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


def _fuzzy_ratio(a: str, b: str) -> float:
    """Rapidfuzz ratio between two normalized strings (0.0-100.0)."""
    from rapidfuzz import fuzz as rf_fuzz

    return rf_fuzz.ratio(a, b)


def _match_structured_sections(
    aligned: list[Section],
    structured: list[dict],
    lrc_normalized: list[str],
) -> list[tuple[Section, Optional[dict]]]:
    """Pair each aligned Section with its structured-lyrics section.

    When counts are equal, pairs by order (both are in song order). When counts
    differ (LLM merged/split sections), pairs by best fuzzy alignment of the
    structured section's first line against the LRC line at each Section's
    line_start. Returns ``(aligned_section, structured_section_or_None)`` pairs.
    """
    if len(aligned) == len(structured):
        return list(zip(aligned, structured))

    pairs: list[tuple[Section, Optional[dict]]] = []
    used: set[int] = set()
    for sec in aligned:
        lrc_at_start = (
            lrc_normalized[sec.line_start - 1]
            if 0 <= sec.line_start - 1 < len(lrc_normalized)
            else ""
        )
        best_idx: Optional[int] = None
        best_score = -1.0
        for i, ssec in enumerate(structured):
            if i in used:
                continue
            lines = ssec.get("lines") or []
            if not lines:
                continue
            first = _normalize_for_matching(lines[0])
            score = _fuzzy_ratio(first, lrc_at_start)
            if score > best_score:
                best_score = score
                best_idx = i
        if best_idx is not None:
            used.add(best_idx)
            pairs.append((sec, structured[best_idx]))
        else:
            pairs.append((sec, None))
    return pairs


def _validate_section_content_alignment(
    sections: list[Section],
    structured_lyrics_json: str,
    lrc_content: str,
    fuzzy_threshold: int = 85,
) -> tuple[list[Section], list[str]]:
    """Validate that each aligned section's line_start matches its structured first line.

    For each Section, compares the normalized text of the LRC line at
    ``(line_start - 1)`` against the structured section's FIRST line. If it
    doesn't match, attempts a bounded repair: searches ±2 LRC lines around
    ``line_start`` for a match. If found, adjusts ``line_start`` (and
    ``line_end`` by the same delta, clamped to valid range). If no repair is
    possible, the section is flagged with a diagnostic but kept (downstream
    chorus validation may still catch it).

    After repairs, an overlap re-check resolves any newly-introduced overlaps:
    if a repaired section overlaps its predecessor, the predecessor's
    ``line_end`` is trimmed; if it overlaps its successor, the repair is
    reverted and the section flagged.

    Returns ``(repaired_sections, diagnostics)`` where ``diagnostics`` is a
    list of human-readable strings describing each repair or flag.
    """
    diagnostics: list[str] = []
    try:
        structured = json.loads(structured_lyrics_json)
    except (json.JSONDecodeError, TypeError):
        return sections, ["structured lyrics JSON invalid"]
    structured_sections = structured.get("sections", []) if isinstance(structured, dict) else []
    if not structured_sections:
        return sections, ["structured lyrics has no sections"]

    try:
        lrc_lines = parse_lrc(lrc_content).lines
    except ValueError:
        return sections, ["LRC parse failed"]
    n = len(lrc_lines)
    if n == 0:
        return sections, ["LRC has no lines"]
    lrc_normalized = [
        _normalize_for_matching(ln.text) if ln.text else "" for ln in lrc_lines
    ]

    # Work on copies so the caller's list is not mutated on failure paths.
    result = [
        Section(s.label, s.line_start, s.line_end, s.confidence, s.rationale)
        for s in sections
    ]
    pairs = _match_structured_sections(result, structured_sections, lrc_normalized)

    original: dict[int, tuple[int, int]] = {}
    repaired: set[int] = set()

    for i, (sec, ssec) in enumerate(pairs):
        if ssec is None:
            diagnostics.append(f"section '{sec.label}': no matching structured section")
            continue
        lines = ssec.get("lines") or []
        if not lines:
            continue
        first_line = _normalize_for_matching(lines[0])
        lrc_at_start = (
            lrc_normalized[sec.line_start - 1]
            if 0 <= sec.line_start - 1 < n
            else ""
        )
        if _lines_match(first_line, lrc_at_start):
            continue

        found = False
        for delta in range(-2, 3):
            if delta == 0:
                continue
            idx = sec.line_start - 1 + delta
            if 0 <= idx < n and _lines_match(first_line, lrc_normalized[idx]):
                new_start = max(1, min(sec.line_start + delta, n))
                new_end = max(1, min(sec.line_end + delta, n))
                if new_start <= new_end:
                    old_start = sec.line_start
                    original[i] = (sec.line_start, sec.line_end)
                    sec.line_start = new_start
                    sec.line_end = new_end
                    repaired.add(i)
                    diagnostics.append(
                        f"section '{sec.label}': line_start {old_start}->{new_start} "
                        f"(repaired: LRC line '{lrc_at_start}' didn't match structured "
                        f"first line '{first_line}')"
                    )
                    found = True
                    break
        if not found:
            diagnostics.append(
                f"section '{sec.label}': line_start {sec.line_start} may be misaligned "
                f"(LRC line '{lrc_at_start}' doesn't match structured first line "
                f"'{first_line}')"
            )

    _resolve_overlaps(result, repaired, original, diagnostics)

    return result, diagnostics


def _resolve_overlaps(
    sections: list[Section],
    repaired: set[int],
    original: dict[int, tuple[int, int]],
    diagnostics: list[str],
) -> None:
    """Resolve overlaps introduced by validator repairs.

    For each adjacent pair that overlaps:
      - If the LATER section was repaired, trim the EARLIER section's
        ``line_end`` to ``(later.line_start - 1)`` (the earlier section's
        boundary was likely wrong, e.g. it absorbed the later section's first
        line).
      - If the EARLIER section was repaired (overlapping its successor), revert
        that repair and flag it, since trimming a successor's ``line_start`` is
        not safe.
    """
    for i in range(1, len(sections)):
        prev, cur = sections[i - 1], sections[i]
        if cur.line_start <= prev.line_end:
            if i in repaired:
                new_end = cur.line_start - 1
                if new_end >= prev.line_start:
                    prev.line_end = new_end
                    diagnostics.append(
                        f"section '{prev.label}': line_end trimmed to {new_end} "
                        f"to resolve overlap with '{cur.label}'"
                    )
                else:
                    _revert(sections, i, original, repaired, diagnostics)
            elif i - 1 in repaired:
                _revert(sections, i - 1, original, repaired, diagnostics)


def _revert(
    sections: list[Section],
    idx: int,
    original: dict[int, tuple[int, int]],
    repaired: set[int],
    diagnostics: list[str],
) -> None:
    """Revert a repaired section back to its original bounds and flag it."""
    if idx not in repaired:
        return
    old_start, old_end = original[idx]
    sec = sections[idx]
    diagnostics.append(
        f"section '{sec.label}': repair reverted (line_start {sec.line_start}->"
        f"{old_start}) due to overlap with a neighbor"
    )
    sec.line_start = old_start
    sec.line_end = old_end
    repaired.discard(idx)


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


def _build_corrective_message(flagged_diagnostics: list[str]) -> str:
    """Build a corrective user message from flagged alignment diagnostics.

    Appended as a new user message (not replacing the original) so the LLM
    retains the full numbered-LRC + structured-sections context while receiving
    targeted feedback about which line_start values were wrong.
    """
    parts = ["Your previous alignment had issues:"]
    for d in flagged_diagnostics:
        parts.append(f"- {d}")
    parts.append("Re-align with correct line_start values.")
    return "\n".join(parts)


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

    max_attempts = max(1, settings.SOW_LLM_STRUCTURED_LYRICS_MAX_ATTEMPTS)

    sections: Optional[list[Section]] = None
    diagnostics: list[str] = []
    for attempt in range(1, max_attempts + 1):
        text = await call_llm_with_retry(
            _call, description="LLM structured lyrics alignment"
        )
        sections, parse_breakdown = _parse_alignment_json(text, n_lines)
        if sections is None:
            logger.warning(
                "Structured lyrics alignment parse failed (attempt %d): %s",
                attempt,
                parse_breakdown,
            )
            return []
        logger.debug(
            "Structured lyrics alignment parse (attempt %d): %s",
            attempt,
            parse_breakdown,
        )

        sections, diagnostics = _validate_section_content_alignment(
            sections, structured_lyrics_json, lrc_content
        )
        for d in diagnostics:
            if "may be misaligned" in d:
                logger.warning("Section content alignment issue: %s", d)
            else:
                logger.debug("Section content alignment: %s", d)

        flagged = [d for d in diagnostics if "may be misaligned" in d]
        if not flagged:
            break
        if attempt < max_attempts:
            corrective = _build_corrective_message(flagged)
            logger.debug(
                "Structured lyrics alignment retry (attempt %d -> %d) with "
                "corrective feedback",
                attempt,
                attempt + 1,
            )
            messages.append({"role": "user", "content": corrective})

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
