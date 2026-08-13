"""LLM whole-song segmentation (Design C) + repetition cross-check validator.

Segments an LRC into labelled sections (intro/verse/prechorus/chorus/bridge/
outro/instrumental) via one LLM call, maps sections to ComponentInstance via
the pure-Python mapper, then runs a deterministic repetition validator over
each chorus section to confirm repetition and tighten boundaries. An opt-in
2nd/3rd LLM sanity check runs only when SOW_LLM_SEGMENTATION_SANITY_CHECK is
enabled. Any failure falls back to the existing lyrics-repetition path.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from openai import OpenAI

from ..config import settings
from .components import ComponentInstance, _normalize_line, _snap_to_beat, _snap_to_downbeat, parse_lrc
from .llm_rate_limit import call_llm_with_retry

logger = logging.getLogger(__name__)

# Held-out fixture song IDs — few-shot examples must NOT come from these songs.
# This list is duplicated from test_components_tuning.py:SONG_IDS to provide
# a runtime assertion against test-set leakage in the few-shot loader.
_EXPECTED_HELD_OUT_IDS = {
    "jun_wang_jiu_zai_zhe_li_1c32724c",
    "yi_sheng_jing_bai_mi_da2173d0",
    "zhu_a__wo_yao_gen_sui_mi_83163301",
}

_VALID_LABELS = {
    "intro",
    "verse",
    "prechorus",
    "chorus",
    "bridge",
    "outro",
    "instrumental",
}


@dataclass(frozen=True)
class ValidatorWeights:
    """Tunable multipliers for _validate_chorus_repetition and
    _map_sections_to_components. Each field corresponds to a knob whose
    default reproduces the current hardcoded literal exactly.
    """
    # Multiplied into a non-repeated chorus's confidence (musically valid
    # but should score lower than a repeating chorus, e.g. an outro chorus).
    nonrepeated_multiplier: float = 0.60
    # Multiplied into confidence after trimming an over-merged chorus's
    # line_end down to the last line whose text repeats elsewhere.
    trimmed_multiplier: float = 0.90
    # Added (then clamped to [0, 1]) when the section already ends on a
    # repeating line — i.e. the LLM's boundary was confirmed correct.
    confirmed_bonus: float = 0.05
    # Multiplied into every emitted ComponentInstance.confidence in the
    # mapper. Mirrors the framing that LLM-derived confidences carry a
    # small discount relative to direct audio analysis.
    mapping_confidence_multiplier: float = 0.95


DEFAULT_VALIDATOR_WEIGHTS = ValidatorWeights()


@dataclass
class Section:
    label: str
    line_start: int
    line_end: int
    confidence: float
    rationale: Optional[str] = None


def _build_client() -> OpenAI:
    if not settings.SOW_LLM_API_KEY:
        raise ValueError("SOW_LLM_API_KEY environment variable not set.")
    if not settings.SOW_LLM_BASE_URL:
        raise ValueError("SOW_LLM_BASE_URL environment variable not set.")
    return OpenAI(
        api_key=settings.SOW_LLM_API_KEY,
        base_url=settings.SOW_LLM_BASE_URL,
        timeout=settings.SOW_LLM_SEGMENTATION_TIMEOUT_SECONDS,
        max_retries=0,
    )


def _segmentation_model() -> str:
    model = settings.SOW_LLM_SEGMENTATION_MODEL or settings.SOW_LLM_MODEL
    if not model:
        raise ValueError("No segmentation model configured (SOW_LLM_MODEL unset).")
    return model


def _render_numbered_lrc(lrc_content: str) -> tuple[str, int]:
    try:
        lines = parse_lrc(lrc_content).lines
    except ValueError:
        logger.warning("parse_lrc returned no lines; treating as empty LRC")
        return "(empty LRC)", 0
    out: list[str] = []
    for i, ln in enumerate(lines, start=1):
        text = ln.text if ln.text is not None else ""
        stamp = f"{ln.time_seconds:.2f}"
        out.append(f"{i}  [{stamp}] {text}")
    return "\n".join(out), len(lines)


def _build_segmentation_prompt(
    lrc_content: str,
    song_title: Optional[str],
    duration: Optional[float],
    few_shot_examples: list[dict],
    system_prompt_override: Optional[str] = None,
) -> list[dict]:
    numbered, _n_lines = _render_numbered_lrc(lrc_content)
    system = (
        system_prompt_override
        if system_prompt_override is not None
        else (
            "You are a Chinese worship-music structure analyst. Given a numbered LRC "
            "lyric file, segment the song into labeled sections and return a JSON object "
            "with a single key 'sections'. Each section has: label (one of intro, verse, "
            "prechorus, chorus, bridge, outro, instrumental), line_start (1-based, "
            "inclusive), line_end (1-based, inclusive), confidence (0.0-1.0), and a short "
            "rationale. Sections must be non-overlapping, cover every non-blank line in "
            "order, and be sorted by line_start. Respond with JSON only."
        )
    )
    user_parts: list[str] = []
    if song_title:
        user_parts.append(f"Song title: {song_title}")
    if duration is not None:
        user_parts.append(f"Approximate duration: {duration:.1f}s")
    user_parts.append("Here are a few reference examples of correct output:")
    for ex in few_shot_examples:
        user_parts.append(ex["input"])
        user_parts.append("Expected output:")
        user_parts.append(json.dumps({"sections": ex["sections"]}, ensure_ascii=False))
    user_parts.append("Now segment this numbered LRC:")
    user_parts.append(numbered)
    user_parts.append("Output JSON only:")
    user = "\n".join(user_parts)
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _parse_segmenter_json(response_text: str, n_lines: int) -> Optional[list[Section]]:
    try:
        data = json.loads(response_text)
    except (json.JSONDecodeError, TypeError):
        return None
    sections_list = data.get("sections") if isinstance(data, dict) else None
    if not isinstance(sections_list, list) or not sections_list:
        return None
    sections: list[Section] = []
    prev_end = 0
    seen_ranges: set[tuple[int, int]] = set()
    for raw in sections_list:
        if not isinstance(raw, dict):
            return None
        label = str(raw.get("label", "")).lower()
        if label not in _VALID_LABELS:
            return None
        try:
            line_start = int(raw["line_start"])
            line_end = int(raw["line_end"])
            confidence = float(raw.get("confidence", 0.5))
        except (KeyError, TypeError, ValueError):
            return None
        if not (1 <= line_start <= line_end <= n_lines):
            return None
        # Strict contiguity: no overlaps (line_start <= prev_end) and no gaps
        # (line_start > prev_end + 1). Design C treats any gap as invalid.
        if line_start <= prev_end or line_start > prev_end + 1 or (line_start, line_end) in seen_ranges:
            return None
        prev_end = line_end
        seen_ranges.add((line_start, line_end))
        sections.append(
            Section(
                label=label,
                line_start=line_start,
                line_end=line_end,
                confidence=max(0.0, min(1.0, confidence)),
                rationale=raw.get("rationale"),
            )
        )
    if not sections:
        return None
    return sections


def _map_sections_to_components(
    sections: list[Section],
    lines: list,
    beats: Optional[list[float]] = None,
    downbeats: Optional[list[float]] = None,
    snap_to_downbeat: bool = False,
    weights: ValidatorWeights = DEFAULT_VALIDATOR_WEIGHTS,
) -> list[ComponentInstance]:
    if not sections:
        return []
    chorus_sections = [s for s in sections if s.label == "chorus"]
    if not chorus_sections:
        return []

    n = len(lines)

    def _sec_time(s: Section) -> tuple[float, float]:
        start = lines[s.line_start - 1].time_seconds
        if s.line_end < n:
            end = lines[s.line_end].time_seconds
        else:
            block_durations = [
                lines[k + 1].time_seconds - lines[k].time_seconds
                for k in range(s.line_start - 1, min(s.line_end, n - 1))
            ]
            avg = sum(block_durations) / len(block_durations) if block_durations else 4.0
            end = lines[min(s.line_end - 1, n - 1)].time_seconds + avg

        if snap_to_downbeat and downbeats:
            start = _snap_to_downbeat(start, downbeats)
            end = _snap_to_downbeat(end, downbeats)
        elif beats:
            start = _snap_to_beat(start, beats)
            end = _snap_to_beat(end, beats)
        return start, end

    def _lyrics_excerpt(s: Section) -> Optional[str]:
        lines_in = [
            ln.text
            for ln in lines[s.line_start - 1 : s.line_end]
            if ln.text and ln.text.strip()
        ]
        return "\n".join(lines_in) if lines_in else None

    components: list[ComponentInstance] = []
    n_choruses = len(chorus_sections)
    for i, sec in enumerate(chorus_sections):
        start, end = _sec_time(sec)
        conf = sec.confidence * weights.mapping_confidence_multiplier
        excerpt = _lyrics_excerpt(sec)
        if n_choruses == 1:
            components.append(
                ComponentInstance(
                    component_type="chorus",
                    occurrence_index=1,
                    role="entry",
                    start_time=start,
                    end_time=end,
                    confidence=conf,
                    source="llm_segmentation",
                    section_label="chorus",
                    lyrics_excerpt=excerpt,
                    llm_rationale=sec.rationale,
                )
            )
            components.append(
                ComponentInstance(
                    component_type="chorus",
                    occurrence_index=1,
                    role="exit",
                    start_time=start,
                    end_time=end,
                    confidence=conf,
                    source="llm_segmentation",
                    section_label="chorus",
                    lyrics_excerpt=excerpt,
                    llm_rationale=sec.rationale,
                )
            )
        else:
            role = "entry" if i == 0 else ("exit" if i == n_choruses - 1 else "none")
            components.append(
                ComponentInstance(
                    component_type="chorus",
                    occurrence_index=i + 1,
                    role=role,
                    start_time=start,
                    end_time=end,
                    confidence=conf,
                    source="llm_segmentation",
                    section_label="chorus",
                    lyrics_excerpt=excerpt,
                    llm_rationale=sec.rationale,
                )
            )

    first_chorus_start = lines[chorus_sections[0].line_start - 1].time_seconds
    verse_before: Optional[Section] = None
    for sec in sections:
        sec_start = lines[sec.line_start - 1].time_seconds
        if sec_start >= first_chorus_start:
            break
        if sec.label == "verse":
            verse_before = sec
    if verse_before is not None:
        start, end = _sec_time(verse_before)
        components.append(
            ComponentInstance(
                component_type="verse",
                occurrence_index=1,
                role="loop_target",
                start_time=start,
                end_time=end,
                confidence=verse_before.confidence * weights.mapping_confidence_multiplier,
                source="llm_segmentation",
                section_label="verse",
                lyrics_excerpt=_lyrics_excerpt(verse_before),
                llm_rationale=verse_before.rationale,
            )
        )
    return components


def _load_few_shot_examples() -> list[dict]:
    """Load few-shot examples from the committed JSON file.

    Each example must include ``source_song_id`` so the loader can assert
    it does not come from any of the 3 fixture evaluation songs (held-out).
    The file must contain 2-3 examples. If absent/empty, logs a warning and
    proceeds with zero examples (still valid).
    """
    few_shot_path = Path(__file__).parent / "segmentation_few_shot.json"
    if not few_shot_path.exists():
        logger.warning(
            "Few-shot examples file not found at %s; running with zero examples.",
            few_shot_path,
        )
        return []
    try:
        examples = json.loads(few_shot_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to load few-shot examples: %s", e)
        return []
    if not isinstance(examples, list):
        logger.warning("Few-shot examples file is not a list; ignoring.")
        return []
    for ex in examples:
        song_id = str(ex.get("source_song_id", "")).strip()
        if song_id in _EXPECTED_HELD_OUT_IDS:
            raise ValueError(
                f"Few-shot example source_song_id '{song_id}' is a held-out "
                f"fixture evaluation song. Remove this example from "
                f"segmentation_few_shot.json to prevent test-set leakage."
            )
    return examples


def _validate_chorus_repetition(
    sections: list[Section],
    lrc_content: str,
    weights: ValidatorWeights = DEFAULT_VALIDATOR_WEIGHTS,
) -> list[Section]:
    """Deterministic repetition cross-check for chorus sections.

    For each chorus section:
      1. Extract normalized text of each line in the section.
      2. Search the rest of the song (outside this section's line range) for
         occurrences of the section's last non-blank line.
      3. If the last line repeats elsewhere, trim ``line_end`` to the last
         line whose normalized text appears elsewhere in the song.
         Multiply confidence by 0.90 for a trim.
      4. If no line in the section repeats anywhere, multiply confidence by
         0.60 (non-repeated chorus is musically valid, e.g. outro chorus).
      5. If the section is confirmed unchanged (already ends on a repeating
         line), add 0.05 bonus (capped at 1.0).

    Never expands boundaries, never merges sections, never extends a
    previously-trimmed boundary. Only shortens ``line_end``.
    """
    try:
        lrc_lines = parse_lrc(lrc_content).lines
    except ValueError:
        return sections

    n = len(lrc_lines)
    if n == 0:
        return sections

    normalized = [_normalize_line(ln.text) if ln.text else "" for ln in lrc_lines]

    # Build a map: normalized_text -> set of 0-based line indices.
    text_to_indices: dict[str, set[int]] = {}
    for idx, norm in enumerate(normalized):
        if norm:
            text_to_indices.setdefault(norm, set()).add(idx)

    result: list[Section] = []
    for sec in sections:
        if sec.label != "chorus":
            result.append(sec)
            continue

        sec_start_0 = sec.line_start - 1
        sec_end_0 = sec.line_end - 1
        sec_line_indices = set(range(sec_start_0, sec_end_0 + 1))

        # Find the last non-blank line in the section.
        last_repeating_line_0 = None
        any_repeats = False
        for k in range(sec_end_0, sec_start_0 - 1, -1):
            if k >= n:
                continue
            norm = normalized[k]
            if not norm:
                continue
            # Check if this line's text appears outside this section.
            other_indices = text_to_indices.get(norm, set()) - sec_line_indices
            if other_indices:
                any_repeats = True
                last_repeating_line_0 = k
                break

        if not any_repeats:
            # Non-repeated chorus — keep but lower confidence.
            new_conf = sec.confidence * weights.nonrepeated_multiplier
            result.append(
                Section(
                    label=sec.label,
                    line_start=sec.line_start,
                    line_end=sec.line_end,
                    confidence=new_conf,
                    rationale=sec.rationale,
                )
            )
        elif last_repeating_line_0 is not None and last_repeating_line_0 < sec_end_0:
            # Trim: the last repeating line is before the current end.
            new_line_end = last_repeating_line_0 + 1  # back to 1-based
            new_conf = sec.confidence * weights.trimmed_multiplier
            result.append(
                Section(
                    label=sec.label,
                    line_start=sec.line_start,
                    line_end=new_line_end,
                    confidence=new_conf,
                    rationale=sec.rationale,
                )
            )
        else:
            # Confirmed unchanged — the section already ends on a repeating line.
            new_conf = min(1.0, sec.confidence + weights.confirmed_bonus)
            result.append(
                Section(
                    label=sec.label,
                    line_start=sec.line_start,
                    line_end=sec.line_end,
                    confidence=new_conf,
                    rationale=sec.rationale,
                )
            )

    return result


async def _sanity_check_llm(
    sections: list[Section],
    lrc_content: str,
    client: OpenAI,
    model: str,
) -> Optional[list[Section]]:
    """Opt-in 2nd LLM call: ask the model to verify the proposed segmentation.

    Returns the sections unchanged if the LLM says correct=True.
    Returns None if the LLM says correct=False (caller may issue a corrective
    3rd call or fall back). Returns None on parse failure.
    """
    numbered, _n_lines = _render_numbered_lrc(lrc_content)
    proposed = json.dumps(
        [
            {"label": s.label, "line_start": s.line_start, "line_end": s.line_end}
            for s in sections
        ],
        ensure_ascii=False,
    )
    prompt = (
        "Here is a proposed segmentation of a worship song. The numbered LRC and the "
        "proposed sections (label, line_start, line_end) follow. Return a JSON object "
        "with key 'correct' (true/false) and, if false, key 'rationale' describing any "
        "mislabeled or mis-bounded section.\n\n"
        f"{numbered}\n\nProposed sections:\n{proposed}"
    )

    def _call() -> str:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": "You verify song structure segmentations. Return JSON only.",
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0,
            response_format={"type": "json_object"},
            max_tokens=settings.SOW_LLM_SEGMENTATION_MAX_TOKENS,
        )
        return resp.choices[0].message.content or ""

    text = await call_llm_with_retry(_call, description="LLM segmentation sanity check")
    try:
        verdict = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    if verdict.get("correct") is True:
        return sections
    return None


async def _corrective_segmentation_call(
    client: OpenAI,
    model: str,
    lrc_content: str,
    song_title: Optional[str],
    duration: Optional[float],
    few_shot: list[dict],
    rejected_sections: list[Section],
) -> Optional[list[Section]]:
    """3rd LLM call: re-segment after the sanity check rejected the first attempt.

    Includes the rejected segmentation and the sanity-check rationale in the
    prompt so the model can correct its mistakes. Returns parsed sections or
    None on failure.
    """
    numbered, n_lines = _render_numbered_lrc(lrc_content)
    rejected_json = json.dumps(
        [
            {"label": s.label, "line_start": s.line_start, "line_end": s.line_end}
            for s in rejected_sections
        ],
        ensure_ascii=False,
    )
    system = (
        "You are a Chinese worship-music structure analyst. A previous segmentation "
        "attempt was rejected by a verifier. Re-segment the song correctly. Return a "
        "JSON object with a single key 'sections'. Each section has: label (one of "
        "intro, verse, prechorus, chorus, bridge, outro, instrumental), line_start "
        "(1-based, inclusive), line_end (1-based, inclusive), confidence (0.0-1.0), "
        "and a short rationale. Sections must be non-overlapping, cover every non-blank "
        "line in order, and be sorted by line_start. Respond with JSON only."
    )
    user_parts: list[str] = []
    if song_title:
        user_parts.append(f"Song title: {song_title}")
    if duration is not None:
        user_parts.append(f"Approximate duration: {duration:.1f}s")
    user_parts.append(f"Rejected segmentation (incorrect):\n{rejected_json}")
    user_parts.append("Now re-segment this numbered LRC correctly:")
    user_parts.append(numbered)
    user_parts.append("Output JSON only:")
    user = "\n".join(user_parts)

    def _call() -> str:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0,
            response_format={"type": "json_object"},
            max_tokens=settings.SOW_LLM_SEGMENTATION_MAX_TOKENS,
        )
        return resp.choices[0].message.content or ""

    text = await call_llm_with_retry(
        _call, description="LLM segmentation corrective call"
    )
    return _parse_segmenter_json(text, n_lines)


async def segment_song(
    lrc_content: str,
    song_title: Optional[str] = None,
    duration: Optional[float] = None,
    beats: Optional[list[float]] = None,
    downbeats: Optional[list[float]] = None,
    snap_to_downbeat: bool = False,
    # v1 tuning-loop overrides — defaults reproduce current behavior bit-for-bit.
    few_shot_override: Optional[list[dict]] = None,
    system_prompt_override: Optional[str] = None,
    validator_weights: ValidatorWeights = DEFAULT_VALIDATOR_WEIGHTS,
) -> list[ComponentInstance]:
    client = _build_client()
    model = _segmentation_model()
    few_shot = (
        few_shot_override
        if few_shot_override is not None
        else _load_few_shot_examples()
    )
    messages = _build_segmentation_prompt(
        lrc_content, song_title, duration, few_shot,
        system_prompt_override=system_prompt_override,
    )

    def _call() -> str:
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0,
            response_format={"type": "json_object"},
            max_tokens=settings.SOW_LLM_SEGMENTATION_MAX_TOKENS,
        )
        return resp.choices[0].message.content or ""

    text = await call_llm_with_retry(_call, description="LLM whole-song segmentation")
    _numbered, n_lines = _render_numbered_lrc(lrc_content)
    sections = _parse_segmenter_json(text, n_lines)
    if sections is None:
        return []
    sections = _validate_chorus_repetition(
        sections, lrc_content, weights=validator_weights,
    )
    if settings.SOW_LLM_SEGMENTATION_SANITY_CHECK:
        checked = await _sanity_check_llm(sections, lrc_content, client, model)
        if checked is None:
            corrected = await _corrective_segmentation_call(
                client, model, lrc_content, song_title, duration, few_shot, sections
            )
            if corrected is not None:
                sections = _validate_chorus_repetition(
                    corrected, lrc_content, weights=validator_weights,
                )
    lines = list(parse_lrc(lrc_content).lines)
    return _map_sections_to_components(
        sections,
        lines,
        beats=beats,
        downbeats=downbeats,
        snap_to_downbeat=snap_to_downbeat,
        weights=validator_weights,
    )
