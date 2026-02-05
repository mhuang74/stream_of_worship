"""LRC generation worker — Whisper transcription + LLM alignment.

Generates timestamped LRC files by:
1. Running Whisper transcription with word-level timestamps
2. Using LLM to align scraped lyrics with Whisper output
3. Writing standard LRC format file
"""

import asyncio
import json
import logging
import re
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
    """Raised when Whisper transcription fails or returns no words."""

    pass


class LLMAlignmentError(LRCWorkerError):
    """Raised when LLM alignment fails after retries."""

    pass


@dataclass
class WhisperWord:
    """A word with timing from Whisper transcription."""

    word: str
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


async def _run_whisper_transcription(
    audio_path: Path,
    model_name: str,
    language: str,
    device: str,
) -> List[WhisperWord]:
    """Run Whisper transcription with word-level timestamps.

    Args:
        audio_path: Path to audio file
        model_name: Whisper model name (e.g., "large-v3")
        language: Language hint (e.g., "zh")
        device: Device to run on ("cpu" or "cuda")

    Returns:
        List of WhisperWord with timing information

    Raises:
        WhisperTranscriptionError: If transcription fails or returns no words
    """
    loop = asyncio.get_event_loop()

    def _transcribe():
        import whisper

        # Ensure cache directory exists
        cache_dir = settings.WHISPER_CACHE_DIR
        cache_dir.mkdir(parents=True, exist_ok=True)

        # Load model
        logger.info(f"Loading Whisper model: {model_name} on {device}")
        model = whisper.load_model(model_name, device=device, download_root=str(cache_dir))

        # Transcribe with word timestamps
        logger.info(f"Transcribing: {audio_path}")
        result = model.transcribe(
            str(audio_path),
            language=language,
            word_timestamps=True,
        )

        # Extract words from segments
        words = []
        for segment in result.get("segments", []):
            for word_info in segment.get("words", []):
                words.append(
                    WhisperWord(
                        word=word_info["word"].strip(),
                        start=word_info["start"],
                        end=word_info["end"],
                    )
                )

        return words

    try:
        words = await loop.run_in_executor(None, _transcribe)
    except Exception as e:
        raise WhisperTranscriptionError(f"Whisper transcription failed: {e}") from e

    if not words:
        raise WhisperTranscriptionError("Whisper returned no words")

    logger.info(f"Transcribed {len(words)} words")
    return words


def _build_alignment_prompt(lyrics_text: str, whisper_words: List[WhisperWord]) -> str:
    """Build the LLM prompt for lyrics alignment.

    Args:
        lyrics_text: Original lyrics text (gold standard)
        whisper_words: Words with timestamps from Whisper

    Returns:
        Prompt string for the LLM
    """
    # Format whisper words as JSON for the prompt
    words_json = json.dumps(
        [{"word": w.word, "start": round(w.start, 2), "end": round(w.end, 2)}
         for w in whisper_words],
        ensure_ascii=False,
        indent=2,
    )

    return f"""You are a lyrics alignment assistant. Your task is to align the original lyrics with Whisper transcription timestamps.

## Original Lyrics (Gold Standard Text)
```
{lyrics_text}
```

## Whisper Transcription (Words with Timestamps)
```json
{words_json}
```

## Instructions
1. Use the original lyrics as the authoritative text - do NOT use Whisper's transcribed text
2. Find the best matching timestamp for the START of each lyric line
3. If Whisper missed some words, interpolate timestamps based on surrounding context
4. Ensure timestamps are in chronological order
5. Return ONLY a JSON array, no other text

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
    whisper_words: List[WhisperWord],
    llm_model: str,
    max_retries: int = 3,
) -> List[LRCLine]:
    """Use LLM to align lyrics with Whisper timestamps.

    Args:
        lyrics_text: Original lyrics text
        whisper_words: Words with timestamps from Whisper
        llm_model: LLM model identifier (e.g., "openai/gpt-4o-mini")
        max_retries: Maximum retry attempts on parse failure

    Returns:
        List of LRCLine with aligned timestamps

    Raises:
        LLMConfigError: If LLM API key is not configured
        LLMAlignmentError: If alignment fails after retries
    """
    if not settings.SOW_LLM_API_KEY:
        raise LLMConfigError(
            "SOW_LLM_API_KEY environment variable not set. "
            "Set this to your OpenRouter/OpenAI API key."
        )

    loop = asyncio.get_event_loop()
    prompt = _build_alignment_prompt(lyrics_text, whisper_words)

    def _call_llm():
        from openai import OpenAI

        client = OpenAI(
            api_key=settings.SOW_LLM_API_KEY,
            base_url=settings.SOW_LLM_BASE_URL,
        )

        response = client.chat.completions.create(
            model=llm_model,
            messages=[
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,  # Low temperature for consistent output
        )

        return response.choices[0].message.content

    last_error: Optional[Exception] = None
    for attempt in range(max_retries):
        try:
            logger.info(f"LLM alignment attempt {attempt + 1}/{max_retries}")
            response_text = await loop.run_in_executor(None, _call_llm)
            lines = _parse_llm_response(response_text)

            if not lines:
                raise ValueError("LLM returned empty alignment")

            logger.info(f"Successfully aligned {len(lines)} lyric lines")
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
) -> tuple[Path, int]:
    """Generate timestamped LRC file from audio and lyrics.

    Args:
        audio_path: Path to audio file (or vocals stem)
        lyrics_text: Original lyrics text
        options: LRC generation options
        output_path: Where to write the LRC file (default: audio_path with .lrc extension)

    Returns:
        Tuple of (path to LRC file, number of lines)

    Raises:
        LLMConfigError: If LLM API key is not configured
        WhisperTranscriptionError: If Whisper transcription fails
        LLMAlignmentError: If LLM alignment fails after retries
    """
    if output_path is None:
        output_path = audio_path.with_suffix(".lrc")

    # Step 1: Run Whisper transcription
    logger.info(f"Starting LRC generation for {audio_path}")
    whisper_words = await _run_whisper_transcription(
        audio_path,
        model_name=options.whisper_model,
        language=options.language,
        device=settings.WHISPER_DEVICE,
    )

    # Step 2: LLM alignment
    lrc_lines = await _llm_align(
        lyrics_text,
        whisper_words,
        llm_model=options.llm_model,
    )

    # Step 3: Write LRC file
    line_count = _write_lrc(lrc_lines, output_path)
    logger.info(f"Wrote {line_count} lines to {output_path}")

    return output_path, line_count
