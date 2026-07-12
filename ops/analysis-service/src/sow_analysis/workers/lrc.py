"""LRC generation worker — Whisper transcription + LLM alignment.

Generates timestamped LRC files by:
1. Running Whisper transcription with phrase-level timestamps
2. Using LLM to align scraped lyrics with Whisper output
3. Writing standard LRC format file
"""

import asyncio
import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Literal, Optional

from ..config import settings
from ..models import LrcOptions
from ..services.canonical_snap import snap_qwen3_asr_to_canonical
from ..services.qwen3_asr_client import Qwen3AsrClient, Qwen3AsrError, Qwen3AsrResult
from ..storage.cache import CacheManager
from .exceptions import LLMConfigError, WorkerError

logger = logging.getLogger(__name__)

ResolvedLrcLanguage = Literal["zh", "en"]
LRC_PROMPT_CACHE_VERSION = "lang-v2"
WHISPER_INITIAL_PROMPT_VERSION = "lang-v2"


class LRCWorkerError(WorkerError):
    """Base exception for LRC worker errors."""

    pass


class WhisperTranscriptionError(LRCWorkerError):
    """Raised when Whisper transcription fails or returns no phrases."""

    pass


class LLMAlignmentError(LRCWorkerError):
    """Raised when LLM alignment fails after retries."""

    pass


class Qwen3RefinementError(LRCWorkerError):
    """Raised when Qwen3 refinement fails (non-blocking, falls back to LLM)."""

    pass


@dataclass(frozen=True)
class LrcLanguageResolution:
    requested: str
    resolved: ResolvedLrcLanguage
    reason: str


_CJK_RE = re.compile(
    "["
    "\u3400-\u4dbf"
    "\u4e00-\u9fff"
    "\uf900-\ufaff"
    "\U00020000-\U0002a6df"
    "\U0002a700-\U0002b73f"
    "\U0002b740-\U0002b81f"
    "\U0002b820-\U0002ceaf"
    "\U0002ceb0-\U0002ebef"
    "]"
)
_LATIN_RE = re.compile(r"[A-Za-z]")


def _contains_cjk(text: str) -> bool:
    return bool(_CJK_RE.search(text or ""))


def _contains_latin(text: str) -> bool:
    return bool(_LATIN_RE.search(text or ""))


def resolve_lrc_language(
    language: str,
    song_title: str,
    lyrics_text: str,
) -> LrcLanguageResolution:
    """Resolve raw LRC language option to a concrete downstream language."""
    if language in {"zh", "en"}:
        return LrcLanguageResolution(language, language, "explicit")
    if language != "auto":
        raise ValueError("language must be one of: auto, zh, en")

    if _contains_cjk(song_title):
        return LrcLanguageResolution(language, "zh", "title_cjk")
    if _contains_latin(song_title):
        return LrcLanguageResolution(language, "en", "title_latin")
    if _contains_cjk(lyrics_text):
        return LrcLanguageResolution(language, "zh", "lyrics_cjk")
    if _contains_latin(lyrics_text):
        return LrcLanguageResolution(language, "en", "lyrics_latin")
    return LrcLanguageResolution(language, "zh", "default_zh")


def warn_if_lrc_language_script_mismatch(
    language: ResolvedLrcLanguage, lyrics_text: object
) -> None:
    stripped = lyrics_text.strip() if isinstance(lyrics_text, str) else ""
    if not stripped:
        return
    cjk_count = len(_CJK_RE.findall(stripped))
    latin_count = len(_LATIN_RE.findall(stripped))
    if language == "en" and cjk_count > latin_count:
        logger.warning(
            "Resolved LRC language is en but lyrics contain more CJK than Latin characters "
            "(cjk=%s latin=%s)",
            cjk_count,
            latin_count,
        )
    elif language == "zh" and latin_count > cjk_count:
        logger.warning(
            "Resolved LRC language is zh but lyrics contain more Latin than CJK characters "
            "(latin=%s cjk=%s)",
            latin_count,
            cjk_count,
        )


@dataclass
class WhisperPhrase:
    """A phrase/segment with timing from Whisper transcription."""

    text: str  # Full phrase text
    start: float  # seconds
    end: float  # seconds


@dataclass
class LRCLine:
    """A timestamped lyric line."""

    time_seconds: float
    text: str

    def format(self) -> str:
        """Format as LRC timestamp line: [mm:ss.xx] text"""
        minutes = int(self.time_seconds // 60)
        seconds = self.time_seconds % 60
        return f"[{minutes:02d}:{seconds:05.2f}] {self.text}"


def _format_timestamp(seconds: float) -> str:
    """Format seconds as [mm:ss.xx] timestamp."""
    minutes = int(seconds // 60)
    secs = seconds % 60
    return f"[{minutes:02d}:{secs:05.2f}]"


async def _run_whisper_transcription(
    audio_path: Path,
    model_name: str,
    language: ResolvedLrcLanguage,
    device: str,
    lyrics_text: Optional[str] = None,
) -> List[WhisperPhrase]:
    """Run Whisper transcription with phrase-level timestamps.

    Args:
        audio_path: Path to audio file
        model_name: Whisper model name (e.g., "large-v3")
        language: Language hint (e.g., "zh")
        device: Device to run on ("cpu" or "cuda")

    Returns:
        List of WhisperPhrase with timing information

    Raises:
        WhisperTranscriptionError: If transcription fails or returns no phrases
    """
    loop = asyncio.get_event_loop()

    def _transcribe():
        from faster_whisper import WhisperModel

        # Ensure cache directory exists
        cache_dir = settings.SOW_WHISPER_CACHE_DIR
        cache_dir.mkdir(parents=True, exist_ok=True)

        # Determine device
        device_type = device if device else "cuda"
        compute_type = "int8"  # Use int8 quantization for speed

        # Load model
        logger.info(f"Loading Whisper model: {model_name} on {device_type} with {compute_type}")
        model_load_start = time.time()
        model = WhisperModel(
            model_name,
            device=device_type,
            compute_type=compute_type,
            download_root=str(cache_dir),
        )
        model_load_elapsed = time.time() - model_load_start
        logger.info(f"Whisper model loaded in {model_load_elapsed:.2f}s")

        # Transcribe with language-specific worship song context.
        logger.info(f"Running Whisper transcription: {audio_path}")
        transcribe_start = time.time()

        # Build dynamic initial prompt with lyrics if available
        if lyrics_text:
            # Take first 50 lines and truncate to 2000 characters max
            lyrics_truncated = "\n".join(lyrics_text.split("\n")[:50])
            if len(lyrics_truncated) > 2000:
                lyrics_truncated = lyrics_truncated[:2000]
            if language == "en":
                initial_prompt = (
                    "This is an English worship song. Preserve the English words, "
                    "phrasing, contractions, casing, and punctuation from these official lyrics:\n"
                    f"{lyrics_truncated}"
                )
            else:
                initial_prompt = f"这是一首中文敬拜诗歌。歌词如下：\n{lyrics_truncated}"
            logger.info(f"Using lyrics-enhanced initial prompt ({len(lyrics_truncated)} chars)")
        else:
            if language == "en":
                initial_prompt = (
                    "This is an English worship song. Preserve English worship lyrics, "
                    "phrasing, contractions, casing, and punctuation."
                )
            else:
                initial_prompt = "这是一首中文敬拜歌的歌詞"
            logger.info("Using default initial prompt (no lyrics provided)")

        segments, info = model.transcribe(
            str(audio_path),
            language=language,
            beam_size=5,
            vad_filter=True,
            condition_on_previous_text=True,
            initial_prompt=initial_prompt,
        )

        # Note: segments is a generator - transcription happens during iteration
        # Extract phrases from segments (convert generator to list)
        phrases = []
        for segment in segments:
            text = segment.text.strip()
            if text:
                phrases.append(
                    WhisperPhrase(
                        text=text,
                        start=segment.start,
                        end=segment.end,
                    )
                )

        transcribe_elapsed = time.time() - transcribe_start
        logger.info(f"Whisper transcription completed in {transcribe_elapsed:.2f}s")
        logger.info(
            f"Detected language: {info.language}, probability: {info.language_probability:.2f}"
        )

        return phrases

    try:
        phrases = await loop.run_in_executor(None, _transcribe)
    except Exception as e:
        raise WhisperTranscriptionError(f"Whisper transcription failed: {e}") from e

    if not phrases:
        raise WhisperTranscriptionError("Whisper returned no phrases")

    # Log transcribed phrases with timecodes
    logger.info(f"Transcribed {len(phrases)} phrases")
    logger.debug("=" * 80)
    logger.debug("WHISPER TRANSCRIBED PHRASES (with timecodes)")
    logger.debug("=" * 80)
    for phrase in phrases:
        start_ts = _format_timestamp(phrase.start)
        end_ts = _format_timestamp(phrase.end)
        logger.debug(f"{start_ts} - {end_ts}  {phrase.text}")
    logger.debug("=" * 80)

    return phrases


def _build_alignment_prompt(
    lyrics_text: str,
    whisper_phrases: List[WhisperPhrase],
    language: ResolvedLrcLanguage = "zh",
) -> str:
    """Build the LLM prompt for lyrics alignment.

    Args:
        lyrics_text: Original lyrics text (gold standard)
        whisper_phrases: Phrases with timestamps from Whisper

    Returns:
        Prompt string for the LLM
    """
    # Format whisper phrases as JSON for the prompt
    phrases_json = json.dumps(
        [
            {"text": p.text, "start": round(p.start, 2), "end": round(p.end, 2)}
            for p in whisper_phrases
        ],
        ensure_ascii=False,
        indent=2,
    )

    # Calculate total duration from whisper phrases
    last_timestamp = max(p.end for p in whisper_phrases) if whisper_phrases else 0.0

    if language == "en":
        return f"""You are a lyrics alignment assistant for English worship songs. Your task is to assign accurate timestamps to every sung lyric line.

## Official Lyrics (Gold Standard Text - Use exactly as written)
```
{lyrics_text}
```

## ASR Transcription (Phrases with Timestamps)
```json
{phrases_json}
```

## Song Structure Information
- Total audio duration: {last_timestamp:.2f} seconds
- The ASR transcription contains {len(whisper_phrases)} phrases

## Critical Instructions

1. Worship songs often repeat verses, choruses, bridges, and tags. Preserve repeated sung sections as separate output entries with their own timestamps.
2. Process each ASR phrase in order. For each phrase, find the best matching line from the official lyrics.
3. The same official lyric line can appear multiple times with different timestamps.
4. Output approximately {len(whisper_phrases)} entries. Do not collapse repeated sections.
5. Use the exact text from "Official Lyrics". Preserve English casing, punctuation, contractions, and line text exactly.
6. Use the start time of each ASR phrase as the timestamp for the matched lyric line.
7. Keep timestamps in ascending order.

## Output Format
Return a JSON array where each object has:
- "time_seconds": float (start time from the corresponding ASR phrase)
- "text": string (matched official lyric line, exactly as provided)

Return ONLY the JSON array, no explanation or markdown code blocks."""

    return f"""You are a lyrics alignment assistant. Your task is to assign accurate timestamps to every line sung in the song.

## Original Lyrics (Gold Standard Text - Use exactly as written)
```
{lyrics_text}
```

## Whisper Transcription (Phrases with Timestamps)
```json
{phrases_json}
```

## Song Structure Information
- Total audio duration: {last_timestamp:.2f} seconds
- The Whisper transcription contains {len(whisper_phrases)} phrases

## Critical Instructions

1. **IMPORTANT**: Worship songs often repeat verses and choruses multiple times. The same lyric line may appear 3-5+ times throughout the song.

2. **Process each Whisper phrase**: Go through the Whisper transcription in order. For EACH phrase, find the best matching lyric line from the original lyrics.

3. **Allow repetitions**: The same lyric line CAN and SHOULD appear multiple times in your output with different timestamps if it was sung multiple times.

4. **Output length**: Your output MUST have approximately {len(whisper_phrases)} entries (one per Whisper phrase). Do NOT consolidate repeated sections.

5. **Text authority**: Use the exact text from "Original Lyrics", not the Whisper transcription (which may have errors).

6. **Timestamp handling**: Use the start time of each Whisper phrase as the timestamp for the matched lyric line.

7. **Chronological order**: Ensure all timestamps are in ascending order.

## Output Format
Return a JSON array where each object has:
- "time_seconds": float (start time from the corresponding Whisper phrase)
- "text": string (the matched lyric line from Original Lyrics, exactly as provided)

Example showing repeated chorus:
```json
[
  {{"time_seconds": 15.0, "text": "我要看見"}},
  {{"time_seconds": 18.5, "text": "我要看見"}},
  {{"time_seconds": 22.0, "text": "如同摩西看見祢的榮耀"}},
  {{"time_seconds": 42.0, "text": "我要看見"}},
  {{"time_seconds": 45.5, "text": "我要看見"}},
  {{"time_seconds": 49.0, "text": "這世代要看見祢榮耀"}},
  {{"time_seconds": 56.0, "text": "我要看見"}},
  {{"time_seconds": 58.0, "text": "我要看見"}}
]
```

Return ONLY the JSON array, no explanation or markdown code blocks."""


def _build_qwen3_asr_alignment_prompt(
    lyrics_text: str,
    whisper_phrases: List[WhisperPhrase],
    language: ResolvedLrcLanguage = "zh",
) -> str:
    phrases_json = json.dumps(
        [
            {"text": p.text, "start": round(p.start, 2), "end": round(p.end, 2)}
            for p in whisper_phrases
        ],
        ensure_ascii=False,
        indent=2,
    )
    if language == "en":
        return f"""You are a lyrics alignment assistant for English worship songs.

## Canonical English Lyrics
```
{lyrics_text}
```

## Qwen3 ASR Phrases
These phrases may already be snapped to canonical lyric lines. Preserve each timestamp.
Only fix text, assign or reorder canonical English lyric lines, preserve repeated sung
sections, and preserve official casing, punctuation, and contractions.
```json
{phrases_json}
```

Return the same JSON shape:
[
  {{"time_seconds": 12.34, "text": "canonical lyric line"}}
]

Return ONLY the JSON array, no explanation or markdown code blocks."""

    return f"""You are a lyrics alignment assistant for Chinese worship songs.

## Canonical Lyrics
```
{lyrics_text}
```

## Qwen3 ASR Phrases
These phrases may already be snapped to canonical lyric lines. Preserve each timestamp. Only fix
text, assign or reorder canonical lyric lines, and preserve repeated sung sections.
```json
{phrases_json}
```

Return the same JSON shape:
[
  {{"time_seconds": 12.34, "text": "canonical lyric line"}}
]

Return ONLY the JSON array, no explanation or markdown code blocks."""


def _parse_llm_response(response_text: str) -> List[LRCLine]:
    """Parse LLM response into LRCLine objects.

    Args:
        response_text: Raw LLM response text

    Returns:
        List of LRCLine objects

    Raises:
        ValueError: If response cannot be parsed as valid JSON
    """
    # Strip markdown code blocks if present
    text = response_text.strip()
    if text.startswith("```"):
        # Remove opening code block (with optional language tag)
        text = re.sub(r"^```(?:json)?\s*\n?", "", text)
        # Remove closing code block
        text = re.sub(r"\n?```\s*$", "", text)

    # Parse JSON
    data = json.loads(text)

    if not isinstance(data, list):
        raise ValueError("Expected JSON array")

    lines = []
    for item in data:
        if not isinstance(item, dict):
            raise ValueError("Expected array of objects")
        if "time_seconds" not in item or "text" not in item:
            raise ValueError("Each object must have 'time_seconds' and 'text'")

        lines.append(
            LRCLine(
                time_seconds=float(item["time_seconds"]),
                text=str(item["text"]),
            )
        )

    return lines


def _validate_alignment_coverage(
    lrc_lines: List[LRCLine],
    whisper_phrases: List[WhisperPhrase],
    duration_threshold_seconds: float = 10.0,
) -> None:
    """Validate that LLM alignment covers the full duration of the audio.

    Args:
        lrc_lines: Aligned LRC lines from LLM
        whisper_phrases: Original Whisper transcription phrases
        duration_threshold_seconds: Threshold for duration coverage warning

    Logs warnings if:
        - LLM output has significantly fewer lines than Whisper phrases
        - Last timestamp in LLM output is much earlier than last Whisper phrase
    """
    if not lrc_lines or not whisper_phrases:
        logger.warning("Cannot validate alignment: empty LRC lines or Whisper phrases")
        return

    # Get last timestamps
    last_lrc_time = max(line.time_seconds for line in lrc_lines)
    last_whisper_time = max(p.end for p in whisper_phrases)

    # Check duration coverage
    duration_gap = last_whisper_time - last_lrc_time
    if duration_gap > duration_threshold_seconds:
        logger.warning(
            f"LRC alignment may be incomplete: last LRC timestamp is {last_lrc_time:.2f}s, "
            f"but audio continues until {last_whisper_time:.2f}s (gap: {duration_gap:.2f}s). "
            f"The song may have repeated sections that were not properly aligned."
        )

    # Check line count ratio
    expected_lines = len(whisper_phrases)
    actual_lines = len(lrc_lines)
    if actual_lines < expected_lines * 0.7:  # Less than 70% of expected lines
        logger.warning(
            f"LRC alignment may be missing repetitions: got {actual_lines} lines "
            f"but Whisper detected {expected_lines} phrases. "
            f"Repeated sections may have been consolidated incorrectly."
        )

    # Log coverage stats at info level
    coverage_pct = (last_lrc_time / last_whisper_time * 100) if last_whisper_time > 0 else 0
    logger.info(
        f"Alignment coverage: {actual_lines}/{expected_lines} lines, "
        f"{last_lrc_time:.2f}s/{last_whisper_time:.2f}s ({coverage_pct:.1f}%)"
    )


async def _llm_align(
    lyrics_text: str,
    whisper_phrases: List[WhisperPhrase],
    llm_model: str,
    max_retries: int = 3,
    prompt_builder: Callable[
        [str, List[WhisperPhrase], ResolvedLrcLanguage], str
    ] = _build_alignment_prompt,
    language: ResolvedLrcLanguage = "zh",
) -> List[LRCLine]:
    """Use LLM to align lyrics with Whisper timestamps.

    Args:
        lyrics_text: Original lyrics text
        whisper_phrases: Phrases with timestamps from Whisper
        llm_model: LLM model identifier (e.g., "openai/gpt-4o-mini"), falls back to SOW_LLM_MODEL
        max_retries: Maximum retry attempts on parse failure

    Returns:
        List of LRCLine with aligned timestamps

    Raises:
        LLMConfigError: If LLM API key or model is not configured
        LLMAlignmentError: If alignment fails after retries
    """
    if not settings.SOW_LLM_API_KEY:
        raise LLMConfigError(
            "SOW_LLM_API_KEY environment variable not set. "
            "Set this to your OpenRouter/OpenAI API key."
        )

    if not settings.SOW_LLM_BASE_URL:
        raise LLMConfigError(
            "SOW_LLM_BASE_URL environment variable not set. "
            "Set this to your OpenAI-compatible API base URL "
            "(e.g., https://openrouter.ai/api/v1)."
        )

    # Use provided model or fall back to env var
    effective_model = llm_model or settings.SOW_LLM_MODEL
    if not effective_model:
        raise LLMConfigError(
            "LLM model not specified. Either set llm_model in the request "
            "or set SOW_LLM_MODEL environment variable."
        )

    loop = asyncio.get_event_loop()
    prompt = prompt_builder(lyrics_text, whisper_phrases, language)

    # Log the full LLM prompt
    logger.debug("=" * 80)
    logger.debug(f"LLM PROMPT (sent to model: {effective_model})")
    logger.debug("=" * 80)
    for line in prompt.split("\n"):
        logger.debug(line)
    logger.debug("=" * 80)

    def _call_llm():
        from openai import OpenAI

        client = OpenAI(
            api_key=settings.SOW_LLM_API_KEY,
            base_url=settings.SOW_LLM_BASE_URL,
            max_retries=0,
        )

        response = client.chat.completions.create(
            model=effective_model,
            messages=[
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,  # Low temperature for consistent output
        )

        return response.choices[0].message.content

    logger.info(f"Using LLM model: {effective_model}")

    from .llm_rate_limit import (
        _acquire_llm_slot,
        _call_llm_with_rate_limit_retry,
        _is_llm_rate_limited_error,
        _release_llm_slot,
    )

    last_error: Optional[Exception] = None
    llm_start = time.time()
    for attempt in range(max_retries):
        try:
            logger.info(f"LLM alignment attempt {attempt + 1}/{max_retries}")
            attempt_start = time.time()

            # Use rate-limit retry wrapper for the LLM call
            # (handles 429 with backoff; non-429 propagates immediately)
            await _acquire_llm_slot()
            try:
                response_text = await _call_llm_with_rate_limit_retry(
                    _call_llm,
                    description=f"LLM alignment ({effective_model})",
                    loop=loop,
                )
            finally:
                _release_llm_slot()

            attempt_elapsed = time.time() - attempt_start
            logger.info(f"LLM call completed in {attempt_elapsed:.2f}s")

            # Log the LLM response
            logger.debug("=" * 80)
            logger.debug(f"LLM RESPONSE (attempt {attempt + 1}/{max_retries})")
            logger.debug("=" * 80)
            for line in response_text.split("\n"):
                logger.debug(line)
            logger.debug("=" * 80)

            lines = _parse_llm_response(response_text)

            if not lines:
                raise ValueError("LLM returned empty alignment")

            # Phase 2: Post-alignment validation
            _validate_alignment_coverage(lines, whisper_phrases)

            total_llm_elapsed = time.time() - llm_start
            logger.info(
                f"Successfully aligned {len(lines)} lyric lines (total LLM time: {total_llm_elapsed:.2f}s)"
            )
            return lines

        except json.JSONDecodeError as e:
            last_error = e
            logger.warning(f"LLM response parse error (attempt {attempt + 1}): {e}")
        except ValueError as e:
            last_error = e
            logger.warning(f"LLM response validation error (attempt {attempt + 1}): {e}")
        except Exception as e:
            if _is_llm_rate_limited_error(e):
                # Rate-limit errors should have been retried inside
                # _call_llm_with_rate_limit_retry. If we get here, all rate-limit
                # retries were exhausted — treat as a failure.
                last_error = e
                logger.warning(f"LLM rate-limit retries exhausted (attempt {attempt + 1}): {e}")
            else:
                last_error = e
                logger.warning(f"LLM API error (attempt {attempt + 1}): {e}")

    raise LLMAlignmentError(f"LLM alignment failed after {max_retries} attempts: {last_error}")


def _write_lrc(lines: List[LRCLine], output_path: Path) -> int:
    """Write LRC lines to file.

    Args:
        lines: List of timestamped lyric lines
        output_path: Path to write the LRC file

    Returns:
        Number of lines written
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        for line in sorted(lines, key=lambda x: x.time_seconds):
            f.write(line.format() + "\n")

    return len(lines)


def build_qwen3_asr_cache_key(
    content_hash: str,
    lyrics_text: str,
    stem_kind: str,
    model: str,
    region: str,
    language: str,
    context_max_chars: int,
    context: str,
) -> str:
    """Build rich Qwen3 ASR cache key."""
    parts = {
        "content_hash": content_hash,
        "lyrics_hash": hashlib.sha256(lyrics_text.encode("utf-8")).hexdigest(),
        "stem_kind": stem_kind,
        "model": model,
        "region": region,
        "language": language,
        "context_max_chars": context_max_chars,
        "context_hash": hashlib.sha256(context.encode("utf-8")).hexdigest(),
        "cache_version": settings.SOW_DASHSCOPE_ASR_CACHE_VERSION,
    }
    return hashlib.sha256(json.dumps(parts, sort_keys=True).encode("utf-8")).hexdigest()


def build_whisper_transcription_cache_key(
    content_hash: str,
    lyrics_text: str,
    stem_kind: str,
    model: str,
    language: ResolvedLrcLanguage,
) -> str:
    """Build language-aware Whisper transcription cache key."""
    parts = {
        "content_hash": content_hash,
        "lyrics_hash": hashlib.sha256(lyrics_text.encode("utf-8")).hexdigest(),
        "stem_kind": stem_kind,
        "model": model,
        "language": language,
        "prompt_version": WHISPER_INITIAL_PROMPT_VERSION,
    }
    return hashlib.sha256(json.dumps(parts, sort_keys=True).encode("utf-8")).hexdigest()


def _build_qwen3_context(
    lyrics_text: str, max_chars: int, language: ResolvedLrcLanguage = "zh"
) -> str:
    if language == "en":
        header = (
            "This is an English worship song. Prioritize these official lyric words and "
            "phrases, preserve repeated sung sections, and preserve English casing and punctuation."
        )
    else:
        header = "這是一首中文敬拜詩歌。請優先辨識下列正式歌詞中的詞句，保留演唱重複。"
    lines = []
    total = len(header) + 2
    for line in lyrics_text.splitlines():
        candidate = line.strip()
        if not candidate:
            continue
        if total + len(candidate) + 1 > max_chars:
            break
        lines.append(candidate)
        total += len(candidate) + 1
    return header + "\n" + "\n".join(lines)


async def generate_lrc_from_qwen3_asr(
    audio_path: Path,
    lyrics_text: str,
    options: LrcOptions,
    output_path: Path,
    cache_key: str,
    cache_manager: CacheManager,
    dashscope_semaphore: asyncio.Semaphore,
    resolved_language: ResolvedLrcLanguage = "zh",
    qwen3_client: Optional["Qwen3AsrClient"] = None,
) -> tuple[Path, int, list[WhisperPhrase]]:
    """Generate LRC from DashScope Qwen3 ASR, snap, then LLM alignment."""
    context_limit = min(
        options.qwen3_asr_context_max_chars,
        settings.SOW_DASHSCOPE_ASR_CONTEXT_MAX_CHARS,
    )
    context = _build_qwen3_context(lyrics_text, context_limit, resolved_language)

    cached_payload = (
        None if options.force_qwen3_asr else cache_manager.get_qwen3_asr_transcription(cache_key)
    )
    if cached_payload:
        logger.info("Qwen3 ASR cache hit")
        result = Qwen3AsrResult.from_cache_payload(cached_payload)
    else:
        logger.info("Qwen3 ASR cache miss; calling DashScope")
        client = (
            qwen3_client
            if qwen3_client is not None
            else Qwen3AsrClient(
                api_key=settings.SOW_DASHSCOPE_API_KEY,
                region=settings.SOW_DASHSCOPE_ASR_REGION,
                flash_model=settings.SOW_DASHSCOPE_ASR_FLASH_MODEL,
                filetrans_model=settings.SOW_DASHSCOPE_ASR_FILETRANS_MODEL,
            )
        )
        async with dashscope_semaphore:
            result = await client.transcribe(audio_path, context=context)
        cache_manager.save_qwen3_asr_transcription(cache_key, result.to_cache_payload())

    if len(result.segments) < options.qwen3_asr_min_usable_segments:
        raise Qwen3AsrError(f"Qwen3 ASR returned too few usable segments: {len(result.segments)}")

    snapped = snap_qwen3_asr_to_canonical(
        result,
        lyrics_text,
        threshold=options.qwen3_asr_snap_threshold,
    )
    qwen_phrases = [WhisperPhrase(p.text, p.start, p.end) for p in snapped if p.text.strip()]
    if len(qwen_phrases) < options.qwen3_asr_min_usable_segments:
        raise Qwen3AsrError(
            f"Qwen3 ASR snapping returned too few usable phrases: {len(qwen_phrases)}"
        )

    lrc_lines = await _llm_align(
        lyrics_text,
        qwen_phrases,
        llm_model=options.llm_model,
        prompt_builder=_build_qwen3_asr_alignment_prompt,
        language=resolved_language,
    )
    line_count = _write_lrc(lrc_lines, output_path)
    logger.info("Qwen3 ASR LRC generation wrote %s lines", line_count)
    return output_path, line_count, qwen_phrases


async def try_youtube_transcript_lrc(
    youtube_url: str,
    lyrics_text: str,
    options: LrcOptions,
    output_path: Path,
    resolved_language: ResolvedLrcLanguage = "zh",
) -> Optional[tuple[Path, int, List[WhisperPhrase]]]:
    """Attempt LRC generation via YouTube transcript (primary path).

    Returns (path, line_count, []) on success, None on failure.
    Logs scraped lyrics and result regardless of outcome.
    """
    from .youtube_transcript import (
        YouTubeRateLimitedError,
        youtube_transcript_to_lrc,
    )

    logger.debug("=" * 80)
    logger.debug("SCRAPED LYRICS (Input)")
    logger.debug("=" * 80)
    for line in lyrics_text.split("\n"):
        logger.debug(line)
    logger.debug("=" * 80)

    lrc_start = time.time()

    logger.info("=" * 80)
    logger.info("LRC GENERATION: Attempting YouTube transcript path (primary)")
    logger.info(f"YouTube URL: {youtube_url}")
    logger.info("=" * 80)
    try:
        lrc_lines = await youtube_transcript_to_lrc(
            youtube_url=youtube_url,
            lyrics_text=lyrics_text,
            llm_model=options.llm_model,
            language=resolved_language,
        )
        line_count = _write_lrc(lrc_lines, output_path)
        total_elapsed = time.time() - lrc_start
        logger.info("=" * 80)
        logger.info("LRC GENERATION: YouTube transcript path SUCCEEDED")
        logger.info(f"Wrote {line_count} lines to {output_path} (total time: {total_elapsed:.2f}s)")
        logger.info("=" * 80)
        logger.debug("=" * 80)
        logger.debug("FINAL LRC FILE CONTENTS (via YouTube transcript)")
        logger.debug("=" * 80)
        with open(output_path, "r", encoding="utf-8") as f:
            for lrc_line in f:
                logger.debug(lrc_line.rstrip("\n"))
        logger.debug("=" * 80)
        return output_path, line_count, []
    except YouTubeRateLimitedError:
        raise  # let queue.py decide: wait-and-retry (free) or fall back (non-free)
    except Exception as e:  # incl. YouTubeTranscriptError("no transcript found")
        logger.warning("=" * 80)
        logger.warning("LRC GENERATION: YouTube transcript path FAILED")
        logger.warning(f"Reason: {e}")
        logger.warning("Falling back to LLM-based ASR...")
        logger.warning("=" * 80)
        return None


async def generate_lrc(
    audio_path: Path,
    lyrics_text: str,
    options: LrcOptions,
    output_path: Optional[Path] = None,
    cached_phrases: Optional[List[WhisperPhrase]] = None,
    youtube_url: Optional[str] = None,
    content_hash: Optional[str] = None,
    vocals_stem_url: Optional[str] = None,
    local_model_semaphore: Optional[asyncio.Semaphore] = None,
    resolved_language: ResolvedLrcLanguage = "zh",
) -> tuple[Path, int, List[WhisperPhrase]]:
    """Generate timestamped LRC file from audio and lyrics.

    If a youtube_url is provided, tries YouTube transcript + LLM correction first.
    Falls back to Whisper + LLM alignment on failure or when no URL is given.

    Args:
        audio_path: Path to audio file (or vocals stem)
        lyrics_text: Original lyrics text
        options: LRC generation options
        output_path: Where to write the LRC file (default: audio_path with .lrc extension)
        cached_phrases: Optional cached Whisper transcription phrases to skip transcription
        youtube_url: Optional YouTube URL for transcript-based LRC (primary path)
        content_hash: Deprecated, retained for caller compatibility.
        vocals_stem_url: Deprecated, retained for caller compatibility.
        local_model_semaphore: Optional semaphore to limit concurrent local model execution.
            Acquired around Whisper transcription. LLM alignment does not acquire this semaphore.

    Returns:
        Tuple of (path to LRC file, number of lines, transcription phrases)

    Raises:
        LLMConfigError: If LLM API key is not configured
        WhisperTranscriptionError: If Whisper transcription fails
        LLMAlignmentError: If LLM alignment fails after retries
    """
    from .queue import optional_semaphore

    if output_path is None:
        output_path = audio_path.with_suffix(".lrc")

    lrc_start = time.time()

    # Primary path: YouTube transcript + LLM correction
    if youtube_url:
        result = await try_youtube_transcript_lrc(
            youtube_url, lyrics_text, options, output_path, resolved_language
        )
        if result is not None:
            return result

    # Log scraped lyrics and announce Whisper path when YouTube is not being attempted
    if not youtube_url:
        logger.debug("=" * 80)
        logger.debug("SCRAPED LYRICS (Input)")
        logger.debug("=" * 80)
        for line in lyrics_text.split("\n"):
            logger.debug(line)
        logger.debug("=" * 80)
        logger.debug("=" * 80)
        logger.debug("LRC GENERATION: Using Whisper transcription directly")
        logger.debug("=" * 80)

    # Fallback path: Whisper transcription + LLM alignment
    logger.info(f"Starting Whisper LRC generation for {audio_path}")

    if cached_phrases is not None:
        logger.info(f"Using cached Whisper transcription with {len(cached_phrases)} phrases")
        whisper_phrases = cached_phrases
    else:
        logger.info("No cached transcription found, running Whisper...")
        async with optional_semaphore(local_model_semaphore):
            whisper_phrases = await _run_whisper_transcription(
                audio_path,
                model_name=options.whisper_model,
                language=resolved_language,
                device=settings.SOW_WHISPER_DEVICE,
                lyrics_text=lyrics_text,
            )

    # Step 2: LLM alignment
    lrc_lines = await _llm_align(
        lyrics_text,
        whisper_phrases,
        llm_model=options.llm_model,
        language=resolved_language,
    )

    # Step 3: Write LRC file
    line_count = _write_lrc(lrc_lines, output_path)
    total_elapsed = time.time() - lrc_start
    logger.info(f"Wrote {line_count} lines to {output_path} (total LRC time: {total_elapsed:.2f}s)")

    # Log final LRC file contents
    logger.debug("=" * 80)
    logger.debug("FINAL LRC FILE CONTENTS")
    logger.debug("=" * 80)
    with open(output_path, "r", encoding="utf-8") as f:
        for lrc_line in f:
            logger.debug(lrc_line.rstrip("\n"))
    logger.debug("=" * 80)

    return output_path, line_count, whisper_phrases
