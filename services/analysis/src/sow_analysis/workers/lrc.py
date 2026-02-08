"""LRC generation worker — Whisper transcription + LLM alignment.

Generates timestamped LRC files by:
1. Running Whisper transcription with phrase-level timestamps
2. Using LLM to align scraped lyrics with Whisper output
3. Writing standard LRC format file
"""

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from ..config import settings
from ..models import LrcOptions

logger = logging.getLogger(__name__)


class LRCWorkerError(Exception):
    """Base exception for LRC worker errors."""

    pass


class LLMConfigError(LRCWorkerError):
    """Raised when LLM configuration is missing or invalid."""

    pass


class WhisperTranscriptionError(LRCWorkerError):
    """Raised when Whisper transcription fails or returns no phrases."""

    pass


class LLMAlignmentError(LRCWorkerError):
    """Raised when LLM alignment fails after retries."""

    pass


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
    language: str,
    device: str,
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

        # Transcribe with Chinese worship song optimizations
        logger.info(f"Running Whisper transcription: {audio_path}")
        transcribe_start = time.time()

        # VAD parameters to filter out background music/instrumentals
        vad_parameters = {
            "min_silence_duration_ms": 500,
        }

        # Initial prompt in Chinese for better recognition of worship song lyrics
        initial_prompt = "这是一首中文敬拜歌的歌詞"

        segments, info = model.transcribe(
            str(audio_path),
            language=language,
            beam_size=5,
            vad_filter=True,
            vad_parameters=vad_parameters,
            initial_prompt=initial_prompt,
        )

        transcribe_elapsed = time.time() - transcribe_start
        logger.info(f"Whisper transcription completed in {transcribe_elapsed:.2f}s")
        logger.info(f"Detected language: {info.language}, probability: {info.language_probability:.2f}")

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

        return phrases

    try:
        phrases = await loop.run_in_executor(None, _transcribe)
    except Exception as e:
        raise WhisperTranscriptionError(f"Whisper transcription failed: {e}") from e

    if not phrases:
        raise WhisperTranscriptionError("Whisper returned no phrases")

    # Log transcribed phrases with timecodes
    logger.info(f"Transcribed {len(phrases)} phrases")
    logger.info("=" * 80)
    logger.info("WHISPER TRANSCRIBED PHRASES (with timecodes)")
    logger.info("=" * 80)
    for phrase in phrases:
        start_ts = _format_timestamp(phrase.start)
        end_ts = _format_timestamp(phrase.end)
        logger.info(f"{start_ts} - {end_ts}  {phrase.text}")
    logger.info("=" * 80)

    return phrases


def _build_alignment_prompt(lyrics_text: str, whisper_phrases: List[WhisperPhrase]) -> str:
    """Build the LLM prompt for lyrics alignment.

    Args:
        lyrics_text: Original lyrics text (gold standard)
        whisper_phrases: Phrases with timestamps from Whisper

    Returns:
        Prompt string for the LLM
    """
    # Format whisper phrases as JSON for the prompt
    phrases_json = json.dumps(
        [{"text": p.text, "start": round(p.start, 2), "end": round(p.end, 2)}
         for p in whisper_phrases],
        ensure_ascii=False,
        indent=2,
    )

    return f"""You are a lyrics alignment assistant. Your task is to align the original lyrics with Whisper transcription timestamps.

## Original Lyrics (Gold Standard Text)
```
{lyrics_text}
```

## Whisper Transcription (Phrases with Timestamps)
```json
{phrases_json}
```

## Instructions
1. Use the original lyrics as the authoritative text - do NOT use Whisper's transcribed text
2. Map each lyric line to the phrase that best matches its content
3. Use the phrase's start time as the line's timestamp
4. If a line spans multiple phrases, use the first phrase's start time
5. If a lyric line has no matching phrase, interpolate based on surrounding phrases
6. Ensure timestamps are in chronological order
7. Return ONLY a JSON array, no other text

## Output Format
Return a JSON array where each object has:
- "time_seconds": float (start time of the line in seconds)
- "text": string (the original lyric line, exactly as provided)

Example output:
```json
[
  {{"time_seconds": 0.5, "text": "第一行歌詞"}},
  {{"time_seconds": 4.2, "text": "第二行歌詞"}}
]
```

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


async def _llm_align(
    lyrics_text: str,
    whisper_phrases: List[WhisperPhrase],
    llm_model: str,
    max_retries: int = 3,
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
    prompt = _build_alignment_prompt(lyrics_text, whisper_phrases)

    # Log the full LLM prompt
    logger.info("=" * 80)
    logger.info(f"LLM PROMPT (sent to model: {effective_model})")
    logger.info("=" * 80)
    for line in prompt.split("\n"):
        logger.info(line)
    logger.info("=" * 80)

    def _call_llm():
        from openai import OpenAI

        client = OpenAI(
            api_key=settings.SOW_LLM_API_KEY,
            base_url=settings.SOW_LLM_BASE_URL,
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

    last_error: Optional[Exception] = None
    llm_start = time.time()
    for attempt in range(max_retries):
        try:
            logger.info(f"LLM alignment attempt {attempt + 1}/{max_retries}")
            attempt_start = time.time()
            response_text = await loop.run_in_executor(None, _call_llm)
            attempt_elapsed = time.time() - attempt_start
            logger.info(f"LLM call completed in {attempt_elapsed:.2f}s")

            # Log the LLM response
            logger.info("=" * 80)
            logger.info(f"LLM RESPONSE (attempt {attempt + 1}/{max_retries})")
            logger.info("=" * 80)
            for line in response_text.split("\n"):
                logger.info(line)
            logger.info("=" * 80)

            lines = _parse_llm_response(response_text)

            if not lines:
                raise ValueError("LLM returned empty alignment")

            total_llm_elapsed = time.time() - llm_start
            logger.info(f"Successfully aligned {len(lines)} lyric lines (total LLM time: {total_llm_elapsed:.2f}s)")
            return lines

        except json.JSONDecodeError as e:
            last_error = e
            logger.warning(f"LLM response parse error (attempt {attempt + 1}): {e}")
        except ValueError as e:
            last_error = e
            logger.warning(f"LLM response validation error (attempt {attempt + 1}): {e}")
        except Exception as e:
            last_error = e
            logger.warning(f"LLM API error (attempt {attempt + 1}): {e}")

    raise LLMAlignmentError(
        f"LLM alignment failed after {max_retries} attempts: {last_error}"
    )


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


async def generate_lrc(
    audio_path: Path,
    lyrics_text: str,
    options: LrcOptions,
    output_path: Optional[Path] = None,
    cached_phrases: Optional[List[WhisperPhrase]] = None,
) -> tuple[Path, int, List[WhisperPhrase]]:
    """Generate timestamped LRC file from audio and lyrics.

    Args:
        audio_path: Path to audio file (or vocals stem)
        lyrics_text: Original lyrics text
        options: LRC generation options
        output_path: Where to write the LRC file (default: audio_path with .lrc extension)
        cached_phrases: Optional cached Whisper transcription phrases to skip transcription

    Returns:
        Tuple of (path to LRC file, number of lines, transcription phrases)

    Raises:
        LLMConfigError: If LLM API key is not configured
        WhisperTranscriptionError: If Whisper transcription fails
        LLMAlignmentError: If LLM alignment fails after retries
    """
    if output_path is None:
        output_path = audio_path.with_suffix(".lrc")

    # Log scraped lyrics input
    logger.info("=" * 80)
    logger.info("SCRAPED LYRICS (Input)")
    logger.info("=" * 80)
    for line in lyrics_text.split("\n"):
        logger.info(line)
    logger.info("=" * 80)

    # Step 1: Get Whisper transcription (use cache if available)
    logger.info(f"Starting LRC generation for {audio_path}")
    lrc_start = time.time()

    if cached_phrases is not None:
        logger.info(f"Using cached Whisper transcription with {len(cached_phrases)} phrases")
        whisper_phrases = cached_phrases
    else:
        logger.info("No cached transcription found, running Whisper...")
        whisper_phrases = await _run_whisper_transcription(
            audio_path,
            model_name=options.whisper_model,
            language=options.language,
            device=settings.SOW_WHISPER_DEVICE,
        )

    # Step 2: LLM alignment
    lrc_lines = await _llm_align(
        lyrics_text,
        whisper_phrases,
        llm_model=options.llm_model,
    )

    # Step 3: Write LRC file
    line_count = _write_lrc(lrc_lines, output_path)
    total_elapsed = time.time() - lrc_start
    logger.info(f"Wrote {line_count} lines to {output_path} (total LRC time: {total_elapsed:.2f}s)")

    # Log final LRC file contents
    logger.info("=" * 80)
    logger.info("FINAL LRC FILE CONTENTS")
    logger.info("=" * 80)
    with open(output_path, "r", encoding="utf-8") as f:
        for lrc_line in f:
            logger.info(lrc_line.rstrip("\n"))
    logger.info("=" * 80)

    return output_path, line_count, whisper_phrases
