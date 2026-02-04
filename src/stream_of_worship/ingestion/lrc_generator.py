"""LRC generation pipeline using Whisper + LLM.

This module handles the creation of high-quality time-coded lyrics files
by combining OpenAI Whisper ASR with LLM-based alignment to scraped lyrics.
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, List, Tuple, Any

try:
    import openai
except ImportError:
    openai = None

try:
    import whisper
    from whisper.audio import load_audio
except ImportError:
    whisper = None

from stream_of_worship.core.paths import get_whisper_cache_path


@dataclass
class LRCLine:
    """A single line in an LRC file."""

    time_seconds: float
    text: str

    def format(self) -> str:
        """Format as LRC line: [mm:ss.xx] text"""
        minutes = int(self.time_seconds // 60)
        seconds = self.time_seconds % 60
        return f"[{minutes:02d}:{seconds:05.2f}] {self.text}"


@dataclass
class WhisperWord:
    """A word with timestamp from Whisper output."""

    word: str
    start: float
    end: float


class LRCGenerator:
    """Generate LRC files using Whisper + LLM alignment."""

    def __init__(
        self,
        whisper_model: str = "large-v3",
        llm_model: str = "openai/gpt-4o-mini",
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
    ):
        """Initialize LRC generator.

        Args:
            whisper_model: Whisper model name (default: large-v3)
            llm_model: LLM model identifier (default: openai/gpt-4o-mini)
            api_key: OpenRouter API key (if None, reads from OPENROUTER_API_KEY env var)
            api_base: Custom API base URL (defaults to https://openrouter.ai/api/v1)
        """
        self.whisper_model_name = whisper_model
        self.llm_model = llm_model
        self.api_key = api_key
        self.api_base = api_base or "https://openrouter.ai/api/v1"

        # Lazy-load models
        self._whisper_model = None
        self._llm_client = None

        # Ensure cache directory exists
        get_whisper_cache_path().mkdir(parents=True, exist_ok=True)

    @property
    def whisper_model(self):
        """Get or load Whisper model."""
        if whisper is None:
            raise ImportError(
                "whisper is required for LRC generation. "
                "Install with: uv add --extra lrc_generation openai-whisper"
            )
        if self._whisper_model is None:
            print(f"Loading Whisper model: {self.whisper_model_name}")
            self._whisper_model = whisper.load_model(
                self.whisper_model_name,
                download_root=str(get_whisper_cache_path()),
            )
        return self._whisper_model

    @property
    def llm_client(self):
        """Get or create LLM client."""
        if openai is None:
            raise ImportError(
                "openai is required for LLM alignment. "
                "Install with: uv add --extra lrc_generation openai"
            )
        if self._llm_client is None:
            # Try to get API key from environment if not provided
            key = self.api_key
            if key is None:
                import os

                key = os.environ.get("OPENROUTER_API_KEY")
            if not key:
                raise ValueError(
                    "OpenRouter API key required. Set OPENROUTER_API_KEY environment variable "
                    "or pass api_key parameter."
                )
            self._llm_client = openai.OpenAI(api_key=key, base_url=self.api_base)
        return self._llm_client

    def generate(
        self,
        audio_path: Path,
        lyrics_text: str,
        beats: List[float],
        output_path: Path,
        progress_callback=None,
    ) -> bool:
        """Generate LRC file for a song.

        Args:
            audio_path: Path to audio file
            lyrics_text: Scraped lyrics text (gold standard)
            beats: List of beat timestamps from analysis.json
            output_path: Path where .lrc file should be saved
            progress_callback: Optional callback function for progress updates

        Returns:
            True if generation succeeded, False otherwise
        """
        try:
            if progress_callback:
                progress_callback("Running Whisper transcription...", 0.1)

            # Step 1: Run Whisper to get word-level timestamps
            whisper_words = self._run_whisper(audio_path)

            if progress_callback:
                progress_callback("Running LLM alignment...", 0.5)

            # Step 2: Use LLM to align scraped lyrics with Whisper timestamps
            aligned_lines = self._llm_align(lyrics_text, whisper_words)

            if progress_callback:
                progress_callback("Applying beat grid alignment...", 0.7)

            # Step 3: Snap timestamps to beat grid
            snapped_lines = self._beat_snap(aligned_lines, beats)

            if progress_callback:
                progress_callback("Writing LRC file...", 0.9)

            # Step 4: Write LRC file
            self._write_lrc(snapped_lines, output_path)

            if progress_callback:
                progress_callback("Done!", 1.0)

            return True

        except Exception as e:
            print(f"Error generating LRC for {audio_path.name}: {e}")
            return False

    def _run_whisper(self, audio_path: Path) -> List[WhisperWord]:
        """Run Whisper ASR on audio file.

        Args:
            audio_path: Path to audio file

        Returns:
            List of words with timestamps
        """
        result = self.whisper_model.transcribe(
            str(audio_path),
            word_timestamps=True,
            language="zh",  # Chinese for worship songs
            fp16=False,  # Use float32 for better compatibility
        )

        words: List[WhisperWord] = []
        for segment in result["segments"]:
            for word_info in segment.get("words", []):
                if word_info:
                    words.append(
                        WhisperWord(
                            word=word_info["word"].strip(),
                            start=word_info["start"],
                            end=word_info["end"],
                        )
                    )

        return words

    def _llm_align(
        self, lyrics_text: str, whisper_words: List[WhisperWord], max_retries: int = 3
    ) -> List[LRCLine]:
        """Use LLM to align scraped lyrics with Whisper timestamps.

        Args:
            lyrics_text: Scraped lyrics (gold standard)
            whisper_words: Whisper word-level output with timestamps
            max_retries: Maximum number of retries on failure

        Returns:
            List of LRC lines with aligned timestamps

        Raises:
            RuntimeError: If LLM alignment fails after all retries
        """
        # Prepare Whisper context for LLM
        whisper_context = self._format_whisper_for_llm(whisper_words)

        # Get the first and last timestamp to understand the time range
        first_time = whisper_words[0].start if whisper_words else 0
        last_time = whisper_words[-1].end if whisper_words else 0

        prompt = f"""You are tasked with aligning scraped song lyrics to Whisper ASR timestamps.

## Task
Align the scraped lyrics to the Whisper timestamps. The Whisper output gives word-level timing.
You need to determine which groups of words form phrases and assign timestamps to those phrases.

## Scraped Lyrics (Gold Standard - Use this exact text)
```
{lyrics_text}
```

## Whisper ASR Output (Timing Reference)
Audio duration: {first_time:.1f}s to {last_time:.1f}s

{whisper_context}

## Instructions
1. Parse the scraped lyrics into natural phrases (typically half a line or one clause).
2. For each phrase, find the matching words in the Whisper output by semantic similarity.
3. Use the start time of the first word in the phrase as the phrase timestamp.
4. Return results as JSON array with "time_seconds" and "text" fields.
5. Handle repeated phrases, ad-libs, and variations by matching context.
6. If scraped lyrics differ from Whisper transcription, use scraped text (it's the gold standard).
7. Ensure timestamps are within audio range ({first_time:.1f}s to {last_time:.1f}s).

## Output Format
Return ONLY a JSON array. Example:
[{{"time_seconds": 12.5, "text": "phrase one"}}, {{"time_seconds": 18.2, "text": "phrase two"}}]

No markdown, no explanation, just the JSON array."""

        last_error = None
        for attempt in range(max_retries):
            try:
                response = self.llm_client.chat.completions.create(
                    model=self.llm_model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.3,  # Lower temperature for more deterministic alignment
                    max_tokens=4000,
                )

                content = response.choices[0].message.content.strip()

                # Clean up any markdown code blocks
                if content.startswith("```json"):
                    content = content[7:]
                if content.startswith("```"):
                    content = content[3:]
                if content.endswith("```"):
                    content = content[:-3]
                content = content.strip()

                data = json.loads(content)

                lines: List[LRCLine] = []
                for item in data:
                    lines.append(
                        LRCLine(time_seconds=float(item["time_seconds"]), text=item["text"])
                    )

                return lines

            except json.JSONDecodeError as e:
                last_error = f"LLM returned invalid JSON: {e}"
                if attempt < max_retries - 1:
                    print(f"  Retry {attempt + 1}/{max_retries}: JSON parse error, retrying...")
                    continue
            except Exception as e:
                last_error = f"LLM alignment failed: {e}"
                if attempt < max_retries - 1:
                    print(f"  Retry {attempt + 1}/{max_retries}: {e}, retrying...")
                    continue

        raise RuntimeError(last_error)

    def _format_whisper_for_llm(self, words: List[WhisperWord]) -> str:
        """Format Whisper output for LLM prompt.

        Args:
            words: List of Whisper words

        Returns:
            Formatted string for prompt
        """
        # Process all words without truncation - chunk into groups of 10
        # with empty lines between groups for readability
        lines = []
        for i, word in enumerate(words):
            # Group into chunks to save tokens
            if i % 10 == 0:
                if i > 0:
                    lines.append("")
            lines.append(f"[{word.start:.2f}-{word.end:.2f}s] {word.word}")

        return "\n".join(lines)

    def _beat_snap(
        self, lines: List[LRCLine], beats: List[float]
    ) -> List[LRCLine]:
        """Snap timestamps to nearest beat grid.

        Args:
            lines: LRC lines to snap
            beats: List of beat timestamps from analysis.json

        Returns:
            LRC lines with snapped timestamps
        """
        if not beats:
            return lines

        snapped: List[LRCLine] = []
        beats_array = sorted(beats)

        for line in lines:
            # Find nearest beat
            nearest_beat = min(beats_array, key=lambda b: abs(b - line.time_seconds))
            snapped.append(LRCLine(time_seconds=nearest_beat, text=line.text))

        return snapped

    def _write_lrc(self, lines: List[LRCLine], output_path: Path) -> None:
        """Write LRC file.

        Args:
            lines: LRC lines to write
            output_path: Path to output file
        """
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with output_path.open("w", encoding="utf-8") as f:
            for line in lines:
                f.write(line.format())
                f.write("\n")

    def batch_generate(
        self,
        songs: List[Tuple[Path, str, List[float], Path]],
        max_failures: int = 5,
        progress_callback=None,
    ) -> Tuple[int, int, List[Path]]:
        """Generate LRC files for multiple songs.

        Args:
            songs: List of (audio_path, lyrics_text, beats, output_path) tuples
            max_failures: Maximum number of failures before stopping
            progress_callback: Optional callback for progress updates

        Returns:
            Tuple of (success_count, failure_count, list of successful output paths)
        """
        success = 0
        failures = 0
        total = len(songs)
        successful_paths: List[Path] = []

        for i, (audio_path, lyrics, beats, output_path) in enumerate(songs):
            if progress_callback:
                progress_callback(f"Processing {i + 1}/{total}: {audio_path.name}", i / total)

            try:
                if self.generate(audio_path, lyrics, beats, output_path):
                    success += 1
                    successful_paths.append(output_path)
                else:
                    failures += 1
                    if failures >= max_failures:
                        print(f"Stopping after {max_failures} failures")
                        break
            except Exception as e:
                print(f"  Error processing {audio_path.name}: {e}")
                failures += 1
                if failures >= max_failures:
                    print(f"Stopping after {max_failures} failures")
                    break

        return success, failures, successful_paths


def parse_lrc_file(path: Path) -> List[LRCLine]:
    """Parse an LRC file.

    Args:
        path: Path to .lrc file

    Returns:
        List of LRC lines

    Raises:
        ValueError: If file format is invalid
    """
    lines: List[LRCLine] = []

    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue

            # Parse [mm:ss.xx] format
            match = re.match(r"\[(\d+):(\d+\.\d+)\]\s*(.+)", line)
            if not match:
                continue

            minutes = int(match.group(1))
            seconds = float(match.group(2))
            text = match.group(3).strip()

            # Skip metadata lines like [ti:...], [ar:...]
            if ":" in text and len(text) < 20:
                continue

            time_seconds = minutes * 60 + seconds
            lines.append(LRCLine(time_seconds=time_seconds, text=text))

    return lines
