"""LLM-based theme and vocal posture classification for song components."""

import asyncio
import json
import logging
import re
from typing import Optional

from openai import OpenAI

from ..config import settings
from .components import ComponentInstance
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
        )
        self._model = settings.SOW_LLM_MODEL
        # Reuse the module-level LLM semaphore if available.
        self._llm_semaphore = _get_llm_semaphore()
        self._llm_min_interval = settings.SOW_LLM_MIN_INTERVAL_SECONDS

    async def classify_components(
        self,
        components: list[ComponentInstance],
        lrc_content: Optional[str] = None,
    ) -> list[ComponentInstance]:
        """Classify multiple components in parallel via asyncio.gather.

        Args:
            components: List of ComponentInstance objects to classify.
            lrc_content: Optional LRC text for per-component lyrics extraction.

        Returns:
            The same list with theme/vocal_posture populated.
        """
        tasks = []
        for comp in components:
            lyrics_lines = None
            if lrc_content and comp.start_time is not None and comp.end_time is not None:
                lyrics_lines = _extract_lyrics_for_component(
                    lrc_content, comp.start_time, comp.end_time
                )
            tasks.append(self.classify_component(comp, lyrics_lines))

        await asyncio.gather(*tasks, return_exceptions=True)
        return components

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

        Uses the shared LLM semaphore and min_interval throttle.

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

        async with self._llm_semaphore:
            # Min interval throttle (same pattern as lrc.py).
            if self._llm_min_interval > 0:
                await asyncio.sleep(self._llm_min_interval)

            try:
                response = await asyncio.to_thread(
                    self._client.chat.completions.create,
                    model=self._model,
                    messages=[{"role": "user", "content": prompt}],
                    response_format={"type": "json_object"},
                    max_tokens=300,
                )
                result = response.choices[0].message.content
                parsed = self._parse_llm_json(result)

                # Populate from LLM.
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

                # Retry on parse failure.
                if component.theme is None or component.vocal_posture is None:
                    await self._retry_llm_call(component, lyrics_text, heuristic_posture)

            except Exception as e:
                logger.warning(f"LLM classification failed for component: {e}")

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

    async def _retry_llm_call(
        self,
        component: ComponentInstance,
        lyrics_text: str,
        heuristic_posture: Optional[str] = None,
    ) -> None:
        """Retry LLM call on JSON parse failure. Re-runs heuristic cross-check."""
        async with self._llm_semaphore:
            if self._llm_min_interval > 0:
                await asyncio.sleep(self._llm_min_interval)
            try:
                prompt = self._build_prompt(component, lyrics_text)
                response = await asyncio.to_thread(
                    self._client.chat.completions.create,
                    model=self._model,
                    messages=[{"role": "user", "content": prompt}],
                    response_format={"type": "json_object"},
                    max_tokens=300,
                )
                result = response.choices[0].message.content
                parsed = self._parse_llm_json(result)

                # Parse ALL fields (not just theme/posture).
                component.theme = parsed.get("theme")
                component.theme_confidence = parsed.get("theme_confidence", 0.7)
                component.theme_reasoning = parsed.get("theme_reasoning", "")
                component.vocal_posture = parsed.get("vocal_posture")
                component.vocal_posture_confidence = parsed.get(
                    "vocal_posture_confidence", 0.7
                )
                component.posture_reasoning = parsed.get("posture_reasoning", "")

                # Re-run heuristic cross-check on retry.
                self._apply_heuristic_adjustment(component, heuristic_posture)
            except Exception as e:
                logger.warning(f"LLM retry failed: {e}")

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


def _get_llm_semaphore():
    """Get the shared LLM semaphore (same instance as LRC/embedding jobs).

    Reuses the module-level semaphore from llm_rate_limit.py, ensuring
    consistent throttling across all LLM consumers.

    The semaphore in llm_rate_limit.py is lazily initialized on first
    _acquire_llm_slot() call. We trigger initialization by calling
    _acquire_llm_slot() then _release_llm_slot() once, then return
    the now-initialized _llm_semaphore.
    """
    try:
        from . import llm_rate_limit

        # Trigger lazy initialization if not yet done.
        if llm_rate_limit._llm_semaphore is None:
            # Force initialization by calling _acquire_llm_slot synchronously.
            # This creates the semaphore if SOW_LLM_MAX_CONCURRENT > 0.
            # We can't await here (not in async context at init time),
            # so we replicate the initialization logic.
            max_concurrent = settings.SOW_LLM_MAX_CONCURRENT
            if max_concurrent > 0:
                llm_rate_limit._llm_semaphore = asyncio.Semaphore(max_concurrent)
            else:
                llm_rate_limit._llm_semaphore = None  # disabled

        if llm_rate_limit._llm_semaphore is not None:
            return llm_rate_limit._llm_semaphore
    except ImportError:
        pass

    # Fallback: create a local semaphore (less ideal — no shared throttling).
    max_concurrent = max(1, settings.SOW_LLM_MAX_CONCURRENT)
    return asyncio.Semaphore(max_concurrent)
