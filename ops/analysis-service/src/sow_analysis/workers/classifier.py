"""LLM-based theme and vocal posture classification for song components."""

import asyncio
import hashlib
import json
import logging
import re
import time
from typing import Optional

from openai import OpenAI

from ..config import settings
from .components import ESSENTIAL_ROLES, ComponentInstance
from .llm_rate_limit import call_llm_with_retry
from .lrc_parser import parse_lrc

logger = logging.getLogger(__name__)

# IMPORT the existing 12-theme system from songset_constructor rules.
# These are the ONLY valid values for theme classification.
# Source: ops/admin-cli/src/stream_of_worship/admin/songset_constructor/rules/themes.py
THEME_CATEGORIES = (
    "讚美", "感恩", "敬拜", "奉獻", "認罪", "差遣",
    "信心", "祈禱", "復興", "聖靈", "十字架", "跟隨",
)

# Vocal posture categories — the ONLY valid values for vocal posture classification.
VOCAL_POSTURE_CATEGORIES = (
    "To God",
    "About God",
    "To Congregation",
)

# CJK character range for Chinese text detection.
_CJK_RANGE_START = 0x4E00
_CJK_RANGE_END = 0x9FFF

# Religious pronouns — used specifically for God in Chinese Christian context.
# 祢 (U+794E): "You" directed to God (second person, reverent).
# 祂 (U+7956): "He/Him" referring to God (third person, reverent).
_RELIGIOUS_SECOND_PERSON = "\u794e"  # 祢 — you-to-God
_RELIGIOUS_THIRD_PERSON = "\u7956"   # 祂 — he-to-God

# First-person pronouns (I, we).
_FIRST_PERSON_PRONOUNS = ("我", "我們", "咱", "俺")

# Second-person pronouns — casual (you).
_SECOND_PERSON_CASUAL = ("你", "您", "你們", "爾")

# Third-person pronouns — casual (he, she, it, they).
_THIRD_PERSON_CASUAL = ("他", "她", "它", "他們", "她們", "它們")

# Imperative / exhortation markers for "To Congregation" detection.
_CONGREGATION_MARKERS = ("讓我們", "當", "應當", "要", "彼此", "眾", "凡")


def _has_religious_pronoun(text: str) -> tuple[bool, bool]:
    """Check for religious pronouns (祢/祂) in text.

    Returns:
        (has_religious_you, has_religious_he)
    """
    has_you = _RELIGIOUS_SECOND_PERSON in text
    has_he = _RELIGIOUS_THIRD_PERSON in text
    return (has_you, has_he)


def _classify_posture_heuristic(lyrics: str) -> Optional[str]:
    """Chinese pronoun pre-pass heuristic for vocal posture.

    Runs BEFORE the LLM call to cross-check and adjust the LLM output.
    Does NOT replace the LLM.

    Heuristic rules (in priority order):
      1. Religious pronoun (祢/祂) present -> "To God" (strong signal)
      2. Imperative plural / congregation markers (讓我們, 當, 彼此) -> "To Congregation"
      3. Casual 你 OR 他 present AND 祢/祂 ABSENT -> "About God" (conservative)
      4. No pronouns / no markers -> None (let LLM decide)

    Returns the heuristic classification, or None if inconclusive.
    """
    if not lyrics:
        return None

    # Rule 1: Religious pronouns -> "To God".
    has_religious_you, has_religious_he = _has_religious_pronoun(lyrics)
    if has_religious_you or has_religious_he:
        return "To God"

    # Rule 2: Imperative / exhortation markers -> "To Congregation".
    if any(m in lyrics for m in _CONGREGATION_MARKERS):
        return "To Congregation"

    # Rule 3: Casual 你 OR 他 present, no 祢/祂 -> "About God".
    has_second = any(p in lyrics for p in _SECOND_PERSON_CASUAL)
    has_third = any(p in lyrics for p in _THIRD_PERSON_CASUAL)
    if has_second or has_third:
        return "About God"

    # Rule 4: No clear pattern.
    return None


def _extract_lyrics_for_component(
    lrc_content: str,
    start_time: float,
    end_time: float,
) -> list[str]:
    """Extract lyric lines within a component's time range from LRC content.

    Parses the LRC content using the existing parse_lrc() from lrc_parser.py,
    then filters lines whose timestamps fall within [start_time, end_time].

    Args:
        lrc_content: Raw LRC file content.
        start_time: Component start time in seconds.
        end_time: Component end time in seconds.

    Returns:
        List of lyric line texts within the time range.
    """
    try:
        lrc_file = parse_lrc(lrc_content)
    except (ValueError, Exception):
        return []

    return [
        ln.text
        for ln in lrc_file.lines
        if ln.text and ln.text.strip()
        and start_time <= ln.time_seconds <= end_time
    ]


def _lyric_hash(lyrics_lines: Optional[list[str]]) -> str:
    """Normalized content hash for lyric deduplication.

    Lowercases, collapses whitespace, and strips each line before hashing.
    Returns a stable hex digest; empty/None input returns a fixed sentinel
    so all empty-lyric components collapse to one representative LLM call.
    """
    if not lyrics_lines:
        return "EMPTY"
    normalized = " ".join(
        " ".join(line.lower().split()) for line in lyrics_lines if line.strip()
    )
    if not normalized:
        return "EMPTY"
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def _is_essential(component: ComponentInstance) -> bool:
    """Return True if the component's role is transition-essential.

    Mirrors components._is_essential; defined locally to keep the
    classifier module decoupled from the components module's internals.
    """
    return component.role in ESSENTIAL_ROLES


def has_cached_llm_fields(
    components: list[ComponentInstance],
    classify_theme: bool,
    classify_vocal_posture: bool,
    all_components: bool = False,
) -> bool:
    """Check whether components already carry LLM classification results.

    Returns True only if every component that would be a classification
    candidate (per all_components / essential-only rules) already has
    the requested LLM fields (theme and/or vocal_posture) populated.
    """
    for comp in components:
        is_candidate = all_components or _is_essential(comp)
        if not is_candidate:
            continue
        if classify_theme and comp.theme is None:
            return False
        if classify_vocal_posture and comp.vocal_posture is None:
            return False
    return True


# Decisiveness indicator regex: word-boundary "or", "either", "possibly", "maybe"
_DECISIVENESS_PATTERN = re.compile(
    r"\b(or|either|possibly|maybe)\b", re.IGNORECASE
)


class ThemeClassifier:
    """Classifies song components using LLM theme and vocal posture detection.

    Reuses the existing LLM rate-limiting infrastructure (SOW_LLM_MAX_CONCURRENT
    semaphore, retry/backoff) shared with LRC and embedding jobs.
    """

    def __init__(self):
        if not settings.SOW_LLM_API_KEY:
            raise ValueError(
                "SOW_LLM_API_KEY environment variable not set."
            )
        if not settings.SOW_LLM_BASE_URL:
            raise ValueError(
                "SOW_LLM_BASE_URL environment variable not set."
            )
        if not settings.SOW_LLM_MODEL:
            raise ValueError(
                "SOW_LLM_MODEL environment variable not set."
            )
        # Reuse the same OpenAI client pattern as workers/lrc.py.
        self._client = OpenAI(
            api_key=settings.SOW_LLM_API_KEY,
            base_url=settings.SOW_LLM_BASE_URL,
            timeout=settings.SOW_LLM_CLASSIFICATION_TIMEOUT_SECONDS,
            max_retries=0,  # retries handled by call_llm_with_retry
        )
        self._model = settings.SOW_LLM_MODEL

    async def classify_components(
        self,
        components: list[ComponentInstance],
        lrc_content: Optional[str] = None,
        all_components: bool = False,
    ) -> list[ComponentInstance]:
        """Classify multiple components in parallel via asyncio.gather.

        v6 selective + dedup strategy:
          1. Select candidates: if ``all_components`` is False (default),
             only essential-role components (entry/exit/loop_target/entry_exit)
             are classified; non-essential rows are skipped (their LLM fields
             stay None).
          2. Pre-extract per-candidate lyrics via _extract_lyrics_for_component.
          3. Group candidates by lyric-content hash (normalized: lowercased +
             whitespace collapsed + stripped). Each group has one
             representative that gets an LLM call; duplicates copy the
             representative's result.
          4. Run asyncio.gather over representative classifications.
          5. Copy fields from each representative to its duplicates.

        Args:
            components: List of ComponentInstance objects to classify.
            lrc_content: Optional LRC text for per-component lyrics extraction.
            all_components: If True, classify ALL components (ignore
                essential-only filtering). Default False.

        Returns:
            The same list with theme/vocal_posture populated.
        """
        total = len(components)

        # 1. Select candidates.
        candidates: list[tuple[int, ComponentInstance]] = []
        skipped: list[tuple[int, ComponentInstance]] = []
        for i, comp in enumerate(components, 1):
            if all_components or _is_essential(comp):
                candidates.append((i, comp))
            else:
                skipped.append((i, comp))

        logger.info(
            f"LLM classification: {len(candidates)} to classify, "
            f"{len(skipped)} skipped (essential-only), {total} total"
        )
        for i, comp in skipped:
            logger.info(
                f"LLM classification: skipped component {i}/{total} "
                f"(occurrence={comp.occurrence_index}, type={comp.component_type}, "
                f"role={comp.role})"
            )

        if not candidates:
            return components

        # 2. Pre-extract lyrics + group by lyric hash.
        groups: dict[
            str, list[tuple[int, ComponentInstance, Optional[list[str]]]]
        ] = {}
        for i, comp in candidates:
            lyrics_lines: Optional[list[str]] = None
            if (
                lrc_content
                and comp.start_time is not None
                and comp.end_time is not None
            ):
                lyrics_lines = _extract_lyrics_for_component(
                    lrc_content, comp.start_time, comp.end_time
                )
            h = _lyric_hash(lyrics_lines)
            groups.setdefault(h, []).append((i, comp, lyrics_lines))

        # 3. Classify one representative per group.
        rep_tasks = []
        rep_index: dict[str, tuple[int, ComponentInstance]] = {}
        for h, members in groups.items():
            rep_i, rep_comp, rep_lyrics = members[0]
            rep_index[h] = (rep_i, rep_comp)
            rep_tasks.append(
                self._classify_component_with_logging(rep_i, total, rep_comp, rep_lyrics)
            )

        logger.info(
            f"LLM classification: {len(rep_tasks)} unique lyric groups "
            f"(deduped from {len(candidates)} candidates)"
        )

        results = await asyncio.gather(*rep_tasks, return_exceptions=True)
        for i, result in enumerate(results, 1):
            if isinstance(result, Exception):
                logger.warning(
                    f"LLM classification: representative {i} failed: {result}"
                )

        # 4. Copy representative result to duplicates.
        for h, members in groups.items():
            if len(members) <= 1:
                continue
            _, rep_comp, _ = members[0]
            rep_i = rep_index[h][0]
            for j, dup_comp, _ in members[1:]:
                dup_comp.theme = rep_comp.theme
                dup_comp.vocal_posture = rep_comp.vocal_posture
                dup_comp.theme_confidence = rep_comp.theme_confidence
                dup_comp.vocal_posture_confidence = rep_comp.vocal_posture_confidence
                dup_comp.theme_reasoning = rep_comp.theme_reasoning
                dup_comp.posture_reasoning = rep_comp.posture_reasoning
                logger.info(
                    f"LLM classification: dedup hit — component {j}/{total} "
                    f"copied from component {rep_i}/{total} "
                    f"(lyric_hash={h})"
                )

        return components

    async def _classify_component_with_logging(
        self,
        idx: int,
        total: int,
        component: ComponentInstance,
        lyrics_lines: Optional[list[str]],
    ) -> None:
        """Classify one component with start/completed/failed progress logging."""
        comp_label = (
            f"component {idx}/{total} (occurrence={component.occurrence_index}, "
            f"type={component.component_type})"
        )
        logger.info(f"LLM classification: starting {comp_label}")
        start = time.time()
        try:
            await self.classify_component(component, lyrics_lines)
            elapsed = time.time() - start
            logger.info(
                f"LLM classification: completed {comp_label} ({elapsed:.2f}s, "
                f"theme={component.theme}, posture={component.vocal_posture})"
            )
        except Exception as e:
            elapsed = time.time() - start
            logger.warning(
                f"LLM classification: failed {comp_label} ({elapsed:.2f}s): {e}"
            )

    async def classify_component(
        self,
        component: ComponentInstance,
        lyrics_lines: Optional[list[str]] = None,
    ) -> ComponentInstance:
        """Classify a single component's theme and vocal posture.

        The heuristic pre-pass runs first but does NOT replace the LLM.
        It provides a confidence cross-check signal.

        Args:
            component: ComponentInstance with lyrics context.
            lyrics_lines: Optional list of lyric lines for the component.

        Returns:
            The same ComponentInstance with theme/vocal_posture populated.
        """
        lyrics_text = " ".join(lyrics_lines or []) if lyrics_lines else ""

        # Step 1: Heuristic pre-pass (posture only).
        heuristic_posture = _classify_posture_heuristic(lyrics_text)

        # Step 2: Always call LLM for primary classification.
        await self._classify_component_llm(component, lyrics_text, heuristic_posture)
        return component

    async def _classify_component_llm(
        self,
        component: ComponentInstance,
        lyrics_text: str,
        heuristic_posture: Optional[str] = None,
    ) -> None:
        """Classify via LLM API call, with heuristic cross-check.

        Uses the shared ``call_llm_with_retry`` utility which handles semaphore
        management, min-interval pacing, 429/5xx retry with backoff, and budget
        enforcement. A single re-call is attempted if the API returns 200 but
        unparseable JSON.

        Posture adjustment scheme:
          - Heuristic agrees with LLM -> +0.05 (capped at 0.95)
          - Heuristic="To God" AND LLM="To Congregation" -> -0.2; flag if < 0.6
          - Heuristic="About God" AND LLM="To God" -> -0.1 (no auto-flag)
          - All other disagreements -> -0.2 (flag if < 0.6)
          - Heuristic is None -> no posture adjustment

        Decisiveness penalty (per-field, NOT cross-applied):
          - theme_reasoning mentions decisiveness words -> -0.1 on theme_confidence only
          - posture_reasoning mentions decisiveness words -> -0.1 on posture_confidence only
        """
        prompt = self._build_prompt(component, lyrics_text)

        try:
            parsed = await call_llm_with_retry(
                lambda: self._do_classify_call(component, prompt),
                description=(
                    f"theme/posture classification (comp {component.occurrence_index})"
                ),
            )
            self._apply_llm_result(component, parsed, heuristic_posture)

            # Retry once on JSON parse failure (API returned 200 but unparseable).
            if component.theme is None or component.vocal_posture is None:
                parsed = await call_llm_with_retry(
                    lambda: self._do_classify_call(component, prompt),
                    description=(
                        f"theme/posture classification retry "
                        f"(comp {component.occurrence_index})"
                    ),
                )
                self._apply_llm_result(component, parsed, heuristic_posture)

        except Exception as e:
            logger.warning(f"LLM classification failed for component: {e}")

    def _do_classify_call(self, component: ComponentInstance, prompt: str) -> dict:
        """Synchronous OpenAI classification call, run in an executor.

        Returns the parsed JSON dict. Logs diagnostics before returning.
        """
        response = self._client.chat.completions.create(
            model=self._model,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            max_tokens=300,
        )
        self._log_llm_diagnostics(response, component)
        result = response.choices[0].message.content
        return self._parse_llm_json(result)

    def _apply_llm_result(
        self,
        component: ComponentInstance,
        parsed: dict,
        heuristic_posture: Optional[str],
    ) -> None:
        """Populate component fields from a parsed LLM response and cross-check."""
        component.theme = parsed.get("theme")
        component.theme_confidence = parsed.get("theme_confidence", 0.7)
        component.theme_reasoning = parsed.get("theme_reasoning", "")
        component.vocal_posture = parsed.get("vocal_posture")
        component.vocal_posture_confidence = parsed.get(
            "vocal_posture_confidence", 0.7
        )
        component.posture_reasoning = parsed.get("posture_reasoning", "")

        # Heuristic cross-check.
        self._apply_heuristic_adjustment(component, heuristic_posture)

    def _log_llm_diagnostics(self, response, component) -> None:
        """Log diagnostic details for an LLM API call at DEBUG level.

        Logs: model, token usage (prompt/completion/total), response length,
        finish_reason, and component identifier. At INFO level, the per-component
        progress log from classify_components provides visibility during normal
        operation.
        """
        usage = getattr(response, "usage", None)
        model = getattr(response, "model", self._model)
        finish_reason = getattr(response.choices[0], "finish_reason", "unknown")
        content_len = len(response.choices[0].message.content or "")

        prompt_tokens = getattr(usage, "prompt_tokens", "?") if usage else "?"
        completion_tokens = getattr(usage, "completion_tokens", "?") if usage else "?"
        total_tokens = getattr(usage, "total_tokens", "?") if usage else "?"

        logger.debug(
            f"LLM response: model={model}, tokens={prompt_tokens}/{completion_tokens}/"
            f"{total_tokens} (prompt/completion/total), content_len={content_len}, "
            f"finish={finish_reason}, component={component.occurrence_index}"
        )

    def _parse_llm_json(self, text: str) -> dict:
        """Parse LLM JSON response with basic error handling."""
        try:
            return json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return {}

    def _apply_heuristic_adjustment(
        self,
        component: ComponentInstance,
        heuristic_posture: Optional[str],
    ) -> None:
        """Adjust LLM confidence based on heuristic agreement.

        Posture adjustments only (theme has no heuristic cross-check).

        Decisiveness penalty is PER-FIELD:
          - theme_reasoning mentions decisiveness words -> theme_confidence only
          - posture_reasoning mentions decisiveness words -> posture_confidence only
        """
        if not heuristic_posture or not component.vocal_posture:
            pass  # No adjustment if heuristic is inconclusive or LLM failed.
        elif heuristic_posture == component.vocal_posture:
            component.vocal_posture_confidence = min(
                0.95,
                (component.vocal_posture_confidence or 0.7) + 0.05,
            )
        else:
            if (heuristic_posture == "To God"
                    and component.vocal_posture == "To Congregation"):
                component.vocal_posture_confidence = max(
                    0.0, (component.vocal_posture_confidence or 0.7) - 0.2
                )
                if component.vocal_posture_confidence < 0.6:
                    logger.warning(
                        f"Review flagged: heuristic='To God' but LLM='To Congregation' "
                        f"for occurrence={component.occurrence_index}"
                    )
            elif (heuristic_posture == "About God"
                    and component.vocal_posture == "To God"):
                component.vocal_posture_confidence = max(
                    0.0, (component.vocal_posture_confidence or 0.7) - 0.1
                )
            else:
                component.vocal_posture_confidence = max(
                    0.0, (component.vocal_posture_confidence or 0.7) - 0.2
                )
                if component.vocal_posture_confidence < 0.6:
                    logger.warning(
                        f"Review flagged: heuristic='{heuristic_posture}' "
                        f"but LLM='{component.vocal_posture}' "
                        f"for occurrence={component.occurrence_index}"
                    )

        # Decisiveness penalty: PER-FIELD (not cross-applied).
        if component.theme_reasoning and _DECISIVENESS_PATTERN.search(component.theme_reasoning):
            if component.theme_confidence:
                component.theme_confidence = max(0.0, component.theme_confidence - 0.1)

        if (component.posture_reasoning
                and _DECISIVENESS_PATTERN.search(component.posture_reasoning)):
            if component.vocal_posture_confidence:
                component.vocal_posture_confidence = max(
                    0.0, component.vocal_posture_confidence - 0.1
                )

    def _build_prompt(
        self,
        component: ComponentInstance,
        lyrics_text: str,
    ) -> str:
        """Build the LLM prompt for theme + vocal posture classification.

        Uses the existing 12-Chinese-theme system and 3 vocal posture categories.
        """
        themes_str = ", ".join(f'"{t}"' for t in THEME_CATEGORIES)
        postures_str = ", ".join(f'"{p}"' for p in VOCAL_POSTURE_CATEGORIES)

        return f"""Classify the following song component's lyrical theme and vocal posture.

Component type: {component.component_type}
Occurrence: {component.occurrence_index}
Role: {component.role}

Lyrics:
{lyrics_text[:2000]}

## Theme Categories (choose exactly ONE — these are Chinese theme names):
{themes_str}

## Vocal Posture Categories (choose exactly ONE):
{postures_str}

## JSON Response Schema:
{{
  "theme": "one of the theme categories above",
  "theme_confidence": 0.0-1.0,
  "theme_reasoning": "brief explanation",
  "vocal_posture": "one of the posture categories above",
  "vocal_posture_confidence": 0.0-1.0,
  "posture_reasoning": "brief explanation"
}}

## Examples:

Example 1 — Direct address to God with religious pronoun:
Lyrics: "祢是聖潔的，祢配得一切讚美" (You are holy, You deserve all praise)
Response: {{"theme": "讚美", "theme_confidence": 0.95, "theme_reasoning": "Religious pronoun 祢 + praise language", "vocal_posture": "To God", "vocal_posture_confidence": 0.98, "posture_reasoning": "Direct address to God using 祢"}}

Example 2 — Third-person description of God:
Lyrics: "神愛世人，賜下獨生子" (God loved the world, gave His only Son)
Response: {{"theme": "信心", "theme_confidence": 0.85, "theme_reasoning": "Describes God's character and works", "vocal_posture": "About God", "vocal_posture_confidence": 0.95, "posture_reasoning": "Third-person reference to God"}}

Example 3 — Congregational exhortation:
Lyrics: "讓我們歡喜快樂，歸榮耀給神" (Let us rejoice and give glory to God)
Response: {{"theme": "讚美", "theme_confidence": 0.80, "theme_reasoning": "Call to praise together", "vocal_posture": "To Congregation", "vocal_posture_confidence": 0.90, "posture_reasoning": "Imperative plural 讓我們 (let us)"}}

Return ONLY valid JSON matching the schema above. No markdown, no explanation.
"""
