#!/usr/bin/env python3
"""LRC lyrics file accuracy evaluation tool.

Compares LRC lyrics with Whisper transcription to identify:
- Missing/extra words
- Timing errors

Outputs a detailed diff report and configurable 0-100 accuracy score.
"""

import math
import re
import sys
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


# --------------------------------------------------------------------------
# Data Classes
# --------------------------------------------------------------------------


@dataclass
class PinyinWord:
    """A word with its pinyin representation and timestamp.

    Attributes:
        text: Original Chinese text
        pinyin: Pinyin representation (no tones)
        time_seconds: Timestamp in seconds
    """

    text: str
    pinyin: str
    time_seconds: float


@dataclass
class DiffEntry:
    """A single entry in the diff result.

    Attributes:
        op: Operation type ('equal', 'delete', 'insert', 'replace')
        lrc_text: LRC text (for equal/delete/replace)
        audio_text: Audio text (for equal/insert/replace)
        lrc_pinyin: LRC pinyin
        audio_pinyin: Audio pinyin
        lrc_time: LRC timestamp
        audio_time: Audio timestamp
        time_diff: Timing difference in seconds (for matched words)
    """

    op: str
    lrc_text: Optional[str] = None
    audio_text: Optional[str] = None
    lrc_pinyin: Optional[str] = None
    audio_pinyin: Optional[str] = None
    lrc_time: Optional[float] = None
    audio_time: Optional[float] = None
    time_diff: Optional[float] = None


@dataclass
class EvaluationStats:
    """Statistics from the evaluation.

    Attributes:
        lrc_word_count: Total words in LRC
        audio_word_count: Total words in audio
        matched_count: Number of matched words
        missing_count: Words in LRC not found in audio
        extra_count: Words in audio not found in LRC
        rms_error_ms: RMS timing error in milliseconds
        max_error_ms: Maximum timing error in milliseconds
    """

    lrc_word_count: int
    audio_word_count: int
    matched_count: int
    missing_count: int
    extra_count: int
    rms_error_ms: float
    max_error_ms: float


@dataclass
class EvaluationScores:
    """Scores from the evaluation.

    Attributes:
        text_accuracy: Text accuracy score (0-100)
        timing_accuracy: Timing accuracy score (0-100)
        final_score: Weighted final score (0-100)
        text_weight: Weight used for text accuracy
        timing_weight: Weight used for timing accuracy
    """

    text_accuracy: float
    timing_accuracy: float
    final_score: float
    text_weight: float
    timing_weight: float


@dataclass
class EvaluationResult:
    """Full evaluation result.

    Attributes:
        success: Whether evaluation completed successfully
        stats: Evaluation statistics
        scores: Evaluation scores
        diff_entries: List of diff entries for detailed report
        error_message: Error message if success is False
    """

    success: bool
    stats: Optional[EvaluationStats] = None
    scores: Optional[EvaluationScores] = None
    diff_entries: list[DiffEntry] = field(default_factory=list)
    error_message: Optional[str] = None


# --------------------------------------------------------------------------
# Pinyin Conversion
# --------------------------------------------------------------------------


def chinese_to_pinyin(text: str) -> list[str]:
    """Convert Chinese text to pinyin without tones.

    Args:
        text: Chinese text to convert

    Returns:
        List of pinyin syllables (no tones, lowercase)
    """
    from pypinyin import lazy_pinyin

    # Filter to only Chinese characters
    chinese_chars = re.findall(r"[\u4e00-\u9fff]", text)
    if not chinese_chars:
        return []

    # Convert to pinyin (lazy_pinyin returns no tones by default)
    result = lazy_pinyin("".join(chinese_chars))
    return [p.lower() for p in result if p]


def normalize_pinyin(pinyin: str) -> str:
    """Normalize pinyin for comparison.

    Args:
        pinyin: Pinyin string

    Returns:
        Normalized pinyin (lowercase, stripped)
    """
    return pinyin.lower().strip()


# --------------------------------------------------------------------------
# LRC Parsing with Word-Level Support
# --------------------------------------------------------------------------


@dataclass
class LRCWord:
    """A single word from LRC with timestamp.

    Attributes:
        text: Word text
        time_seconds: Timestamp in seconds
    """

    text: str
    time_seconds: float


def parse_enhanced_lrc_line(line: str) -> Optional[tuple[float, str, list[LRCWord]]]:
    """Parse an enhanced LRC line with optional word-level timestamps.

    Supports formats:
    - Standard: [00:12.50] 我爱你
    - Word-level: [00:12.50]我<00:12.80>爱<00:13.10>你

    Args:
        line: LRC line to parse

    Returns:
        Tuple of (line_start_time, raw_text, words) or None if invalid
    """
    # Match standard LRC timestamp
    line_match = re.match(r"\[(\d{2}):(\d{2})\.(\d{2,3})\](.*)", line.strip())
    if not line_match:
        return None

    minutes = int(line_match.group(1))
    seconds = int(line_match.group(2))
    ms_str = line_match.group(3)
    milliseconds = int(ms_str.ljust(3, "0")[:3])
    line_start = minutes * 60 + seconds + milliseconds / 1000.0
    content = line_match.group(4)

    # Check for word-level timestamps: text<mm:ss.xx>text<mm:ss.xx>...
    # Format: [line_time]word1<time2>word2<time3>word3
    # The timestamp <time2> marks when word2 STARTS
    timestamp_pattern = r"<(\d{2}):(\d{2})\.(\d{2,3})>"

    if re.search(timestamp_pattern, content):
        # Word-level format - split by timestamps
        words = []

        # Split content into segments by timestamps
        parts = re.split(timestamp_pattern, content)
        # parts will be: [text1, min, sec, ms, text2, min, sec, ms, text3, ...]

        # First segment uses line start time
        if parts[0].strip():
            words.append(LRCWord(text=parts[0].strip(), time_seconds=line_start))

        # Process remaining segments (each group of 4: text, min, sec, ms)
        i = 1
        while i + 3 <= len(parts):
            word_min = int(parts[i])
            word_sec = int(parts[i + 1])
            word_ms_str = parts[i + 2]
            word_ms = int(word_ms_str.ljust(3, "0")[:3])
            word_time = word_min * 60 + word_sec + word_ms / 1000.0

            # Text comes AFTER this timestamp
            text_idx = i + 3
            if text_idx < len(parts) and parts[text_idx].strip():
                words.append(LRCWord(text=parts[text_idx].strip(), time_seconds=word_time))

            i += 4

        raw_text = re.sub(r"<\d{2}:\d{2}\.\d{2,3}>", "", content).strip()
        return (line_start, raw_text, words)
    else:
        # Standard format - return whole line as single "word"
        text = content.strip()
        if text:
            return (line_start, text, [LRCWord(text=text, time_seconds=line_start)])
        return (line_start, "", [])


def interpolate_word_times(
    words: list[str], line_start: float, line_end: float
) -> list[LRCWord]:
    """Interpolate timestamps for words in a line.

    Distributes time evenly across words based on character count.

    Args:
        words: List of word strings
        line_start: Line start time in seconds
        line_end: Line end time in seconds

    Returns:
        List of LRCWord with interpolated timestamps
    """
    if not words:
        return []

    if len(words) == 1:
        return [LRCWord(text=words[0], time_seconds=line_start)]

    # Calculate total characters
    total_chars = sum(len(w) for w in words)
    if total_chars == 0:
        total_chars = len(words)

    duration = line_end - line_start
    result = []
    current_time = line_start

    for word in words:
        result.append(LRCWord(text=word, time_seconds=current_time))
        # Advance time proportional to word length
        char_ratio = len(word) / total_chars if total_chars > 0 else 1 / len(words)
        current_time += duration * char_ratio

    return result


def parse_lrc_file(content: str) -> list[PinyinWord]:
    """Parse LRC file content into PinyinWord list.

    Args:
        content: LRC file content

    Returns:
        List of PinyinWord with pinyin and timestamps
    """
    result = []
    lines_data = []

    # First pass: collect all lines with timestamps
    for line in content.split("\n"):
        parsed = parse_enhanced_lrc_line(line)
        if parsed:
            lines_data.append(parsed)

    # Second pass: interpolate word times for lines without word-level timestamps
    for i, (line_start, raw_text, words) in enumerate(lines_data):
        # Determine line end (next line's start or line_start + 5s)
        if i + 1 < len(lines_data):
            line_end = lines_data[i + 1][0]
        else:
            line_end = line_start + 5.0

        if len(words) == 1 and len(words[0].text) > 1:
            # Single word representing whole line - split into characters and interpolate
            chars = list(re.findall(r"[\u4e00-\u9fff]", words[0].text))
            if chars:
                interpolated = interpolate_word_times(chars, line_start, line_end)
                for w in interpolated:
                    pinyin_list = chinese_to_pinyin(w.text)
                    for py in pinyin_list:
                        result.append(PinyinWord(text=w.text, pinyin=py, time_seconds=w.time_seconds))
        else:
            # Already have word-level or character-level
            for w in words:
                pinyin_list = chinese_to_pinyin(w.text)
                for py in pinyin_list:
                    result.append(PinyinWord(text=w.text, pinyin=py, time_seconds=w.time_seconds))

    return result


def extract_lrc_lines(content: str) -> list[tuple[float, str]]:
    """Extract original LRC lines with timestamps.

    Args:
        content: LRC file content

    Returns:
        List of (timestamp, text) tuples for each line
    """
    result = []
    for line in content.split("\n"):
        parsed = parse_enhanced_lrc_line(line)
        if parsed:
            line_start, raw_text, _ = parsed
            result.append((line_start, raw_text))
    return result


# --------------------------------------------------------------------------
# Audio Transcription Engines
# --------------------------------------------------------------------------

# Supported transcription engines
ENGINES = ["whisper", "sensevoice", "paraformer"]


def transcribe_with_whisper(
    audio_path: Path,
    model_name: str = "large-v3",
    device: str = "cpu",
    compute_type: str = "int8",
    language: str = "zh",
) -> list[PinyinWord]:
    """Transcribe audio using faster-whisper with word-level timestamps.

    Args:
        audio_path: Path to audio file
        model_name: Whisper model name (e.g., large-v3, medium, small)
        device: Device to run on (cpu/cuda/mps)
        compute_type: Compute type (int8/float16/int8_float16)
        language: Language hint

    Returns:
        List of PinyinWord with pinyin and timestamps
    """
    from faster_whisper import WhisperModel

    console = Console(stderr=True)
    console.print(f"[whisper] Loading model: {model_name} on {device}", style="dim")

    model = WhisperModel(model_name, device=device, compute_type=compute_type)

    console.print(f"[whisper] Transcribing: {audio_path}", style="dim")

    segments, info = model.transcribe(
        str(audio_path),
        language=language,
        beam_size=5,
        word_timestamps=True,
        initial_prompt="这是一首中文敬拜诗歌",
        vad_filter=True,
    )

    result = []
    for segment in segments:
        if segment.words:
            for word in segment.words:
                text = word.word.strip()
                if text:
                    pinyin_list = chinese_to_pinyin(text)
                    for py in pinyin_list:
                        result.append(PinyinWord(text=text, pinyin=py, time_seconds=word.start))

    console.print(f"[whisper] Transcribed {len(result)} pinyin syllables", style="dim")
    return result


def transcribe_with_sensevoice(
    audio_path: Path,
    model_name: str = "iic/SenseVoiceSmall",
    device: str = "cpu",
    **kwargs,
) -> list[PinyinWord]:
    """Transcribe audio using FunASR SenseVoice model.

    SenseVoice is optimized for Chinese and supports emotion/event detection.

    Args:
        audio_path: Path to audio file
        model_name: SenseVoice model ID (default: iic/SenseVoiceSmall)
        device: Device to run on (cpu/cuda)
        **kwargs: Additional arguments (ignored for compatibility)

    Returns:
        List of PinyinWord with pinyin and timestamps
    """
    from funasr import AutoModel

    console = Console(stderr=True)
    console.print(f"[sensevoice] Loading model: {model_name} on {device}", style="dim")

    # SenseVoice model with VAD for better segmentation
    model = AutoModel(
        model=model_name,
        trust_remote_code=True,
        device=device,
    )

    console.print(f"[sensevoice] Transcribing: {audio_path}", style="dim")

    # SenseVoice inference
    res = model.generate(
        input=str(audio_path),
        cache={},
        language="zh",
        use_itn=True,
        batch_size_s=60,
    )

    result = []
    raw_transcription = ""  # For debug output

    for item in res:
        # SenseVoice returns sentence-level results
        # Extract text and try to get timestamps if available
        text = item.get("text", "")
        timestamp = item.get("timestamp", [])

        if text:
            # Clean up SenseVoice output (may have emotion tags like <|HAPPY|>)
            clean_text = re.sub(r"<\|[^|]+\|>", "", text).strip()
            raw_transcription += clean_text + " "

            if timestamp and len(timestamp) > 0:
                # Has word-level timestamps: [[start_ms, end_ms], ...]
                chars = list(re.findall(r"[\u4e00-\u9fff]", clean_text))
                for i, char in enumerate(chars):
                    if i < len(timestamp):
                        start_ms = timestamp[i][0]
                        time_sec = start_ms / 1000.0
                    else:
                        # Fallback: estimate from previous
                        time_sec = result[-1].time_seconds + 0.2 if result else 0.0

                    pinyin_list = chinese_to_pinyin(char)
                    for py in pinyin_list:
                        result.append(PinyinWord(text=char, pinyin=py, time_seconds=time_sec))
            else:
                # No timestamps - use segment start time and interpolate
                segment_start = item.get("start", 0) / 1000.0 if "start" in item else 0.0
                segment_end = item.get("end", 0) / 1000.0 if "end" in item else segment_start + 5.0

                chars = list(re.findall(r"[\u4e00-\u9fff]", clean_text))
                if chars:
                    duration = segment_end - segment_start
                    time_per_char = duration / len(chars) if len(chars) > 0 else 0.2

                    for i, char in enumerate(chars):
                        time_sec = segment_start + i * time_per_char
                        pinyin_list = chinese_to_pinyin(char)
                        for py in pinyin_list:
                            result.append(PinyinWord(text=char, pinyin=py, time_seconds=time_sec))

    console.print(f"[sensevoice] Transcribed {len(result)} pinyin syllables", style="dim")

    # Warn if no Chinese content was detected
    if len(result) == 0 and raw_transcription.strip():
        console.print(
            f"[sensevoice] Warning: No Chinese characters detected. Raw transcription:\n"
            f"  {raw_transcription[:200]}{'...' if len(raw_transcription) > 200 else ''}",
            style="yellow"
        )

    return result


def transcribe_with_paraformer(
    audio_path: Path,
    model_name: str = "paraformer-zh",
    device: str = "cpu",
    **kwargs,
) -> list[PinyinWord]:
    """Transcribe audio using FunASR Paraformer model with timestamps.

    Paraformer is a fast non-autoregressive model optimized for Chinese ASR.
    Uses paraformer-zh with VAD for better speech detection and character-level
    timestamps.

    Args:
        audio_path: Path to audio file
        model_name: Paraformer model ID (default: paraformer-zh)
        device: Device to run on (cpu/cuda)
        **kwargs: Additional arguments (ignored for compatibility)

    Returns:
        List of PinyinWord with pinyin and timestamps
    """
    from funasr import AutoModel

    console = Console(stderr=True)
    console.print(f"[paraformer] Loading model: {model_name} on {device}", style="dim")

    # Use Paraformer-zh with VAD for better speech detection
    # The VAD model segments the audio and helps detect speech regions
    model = AutoModel(
        model=model_name,
        model_revision="v2.0.4",
        vad_model="fsmn-vad",
        vad_model_revision="v2.0.4",
        device=device,
    )

    console.print(f"[paraformer] Transcribing: {audio_path}", style="dim")

    # Paraformer inference with timestamps
    res = model.generate(
        input=str(audio_path),
        batch_size_s=300,
    )

    result = []
    for item in res:
        text = item.get("text", "")
        timestamp = item.get("timestamp", [])

        if text:
            chars = list(re.findall(r"[\u4e00-\u9fff]", text))

            if timestamp and len(timestamp) > 0:
                # Has character-level timestamps
                for i, char in enumerate(chars):
                    if i < len(timestamp):
                        start_ms = timestamp[i][0]
                        time_sec = start_ms / 1000.0
                    else:
                        time_sec = result[-1].time_seconds + 0.2 if result else 0.0

                    pinyin_list = chinese_to_pinyin(char)
                    for py in pinyin_list:
                        result.append(PinyinWord(text=char, pinyin=py, time_seconds=time_sec))
            else:
                # Fallback: interpolate timestamps based on segment boundaries
                segment_start = item.get("start", 0) / 1000.0 if "start" in item else 0.0
                segment_end = item.get("end", 0) / 1000.0 if "end" in item else segment_start + 5.0

                if chars:
                    duration = segment_end - segment_start
                    time_per_char = duration / len(chars) if len(chars) > 0 else 0.2

                    for i, char in enumerate(chars):
                        time_sec = segment_start + i * time_per_char
                        pinyin_list = chinese_to_pinyin(char)
                        for py in pinyin_list:
                            result.append(PinyinWord(text=char, pinyin=py, time_seconds=time_sec))

    console.print(f"[paraformer] Transcribed {len(result)} pinyin syllables", style="dim")
    return result


def transcribe_audio(
    audio_path: Path,
    engine: str = "whisper",
    model_name: Optional[str] = None,
    device: str = "cpu",
    compute_type: str = "int8",
) -> list[PinyinWord]:
    """Transcribe audio using the specified engine.

    Args:
        audio_path: Path to audio file
        engine: Transcription engine (whisper, sensevoice, paraformer)
        model_name: Model name/ID (engine-specific, None for default)
        device: Device to run on
        compute_type: Compute type (whisper only)

    Returns:
        List of PinyinWord with pinyin and timestamps
    """
    if engine == "whisper":
        return transcribe_with_whisper(
            audio_path,
            model_name=model_name or "large-v3",
            device=device,
            compute_type=compute_type,
        )
    elif engine == "sensevoice":
        return transcribe_with_sensevoice(
            audio_path,
            model_name=model_name or "iic/SenseVoiceSmall",
            device=device,
        )
    elif engine == "paraformer":
        return transcribe_with_paraformer(
            audio_path,
            model_name=model_name or "paraformer-zh",
            device=device,
        )
    else:
        raise ValueError(f"Unknown engine: {engine}. Supported: {ENGINES}")


# --------------------------------------------------------------------------
# Sequence Alignment
# --------------------------------------------------------------------------


def align_sequences(
    lrc_words: list[PinyinWord], audio_words: list[PinyinWord]
) -> list[DiffEntry]:
    """Align LRC and audio word sequences using SequenceMatcher.

    Args:
        lrc_words: LRC pinyin words
        audio_words: Audio pinyin words

    Returns:
        List of DiffEntry showing alignment
    """
    # Extract pinyin lists for matching
    lrc_pinyins = [w.pinyin for w in lrc_words]
    audio_pinyins = [w.pinyin for w in audio_words]

    matcher = SequenceMatcher(None, lrc_pinyins, audio_pinyins)
    opcodes = matcher.get_opcodes()

    result = []
    for op, lrc_start, lrc_end, audio_start, audio_end in opcodes:
        if op == "equal":
            # Matched words
            for i, j in zip(range(lrc_start, lrc_end), range(audio_start, audio_end)):
                lrc_w = lrc_words[i]
                audio_w = audio_words[j]
                time_diff = audio_w.time_seconds - lrc_w.time_seconds
                result.append(
                    DiffEntry(
                        op="equal",
                        lrc_text=lrc_w.text,
                        audio_text=audio_w.text,
                        lrc_pinyin=lrc_w.pinyin,
                        audio_pinyin=audio_w.pinyin,
                        lrc_time=lrc_w.time_seconds,
                        audio_time=audio_w.time_seconds,
                        time_diff=time_diff,
                    )
                )
        elif op == "delete":
            # Words in LRC not in audio (missing from transcription)
            for i in range(lrc_start, lrc_end):
                lrc_w = lrc_words[i]
                result.append(
                    DiffEntry(
                        op="delete",
                        lrc_text=lrc_w.text,
                        lrc_pinyin=lrc_w.pinyin,
                        lrc_time=lrc_w.time_seconds,
                    )
                )
        elif op == "insert":
            # Words in audio not in LRC (extra in transcription)
            for j in range(audio_start, audio_end):
                audio_w = audio_words[j]
                result.append(
                    DiffEntry(
                        op="insert",
                        audio_text=audio_w.text,
                        audio_pinyin=audio_w.pinyin,
                        audio_time=audio_w.time_seconds,
                    )
                )
        elif op == "replace":
            # Handle replacement as delete + insert
            for i in range(lrc_start, lrc_end):
                lrc_w = lrc_words[i]
                result.append(
                    DiffEntry(
                        op="delete",
                        lrc_text=lrc_w.text,
                        lrc_pinyin=lrc_w.pinyin,
                        lrc_time=lrc_w.time_seconds,
                    )
                )
            for j in range(audio_start, audio_end):
                audio_w = audio_words[j]
                result.append(
                    DiffEntry(
                        op="insert",
                        audio_text=audio_w.text,
                        audio_pinyin=audio_w.pinyin,
                        audio_time=audio_w.time_seconds,
                    )
                )

    return result


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------


def calculate_text_score(diff: list[DiffEntry]) -> float:
    """Calculate text accuracy score (F1-like).

    Args:
        diff: List of diff entries

    Returns:
        Score from 0-100
    """
    matched = sum(1 for d in diff if d.op == "equal")
    deleted = sum(1 for d in diff if d.op == "delete")
    inserted = sum(1 for d in diff if d.op == "insert")

    # Precision: matched / (matched + inserted)
    # Recall: matched / (matched + deleted)
    # F1 = 2 * P * R / (P + R)

    if matched == 0:
        return 0.0

    precision = matched / (matched + inserted) if (matched + inserted) > 0 else 0
    recall = matched / (matched + deleted) if (matched + deleted) > 0 else 0

    if precision + recall == 0:
        return 0.0

    f1 = 2 * precision * recall / (precision + recall)
    return f1 * 100


def calculate_timing_score(diff: list[DiffEntry], threshold_ms: float = 500.0) -> tuple[float, float, float]:
    """Calculate timing accuracy score.

    Args:
        diff: List of diff entries
        threshold_ms: RMS threshold for 0 timing score (milliseconds)

    Returns:
        Tuple of (score, rms_error_ms, max_error_ms)
    """
    matched = [d for d in diff if d.op == "equal" and d.time_diff is not None]

    if not matched:
        return 100.0, 0.0, 0.0  # No timing to compare

    # Calculate RMS error
    squared_errors = [(d.time_diff * 1000) ** 2 for d in matched]  # Convert to ms
    rms_error_ms = math.sqrt(sum(squared_errors) / len(squared_errors))

    # Max error
    max_error_ms = max(abs(d.time_diff * 1000) for d in matched)

    # Score: 100 - (rms / threshold) * 100, clamped to [0, 100]
    score = max(0.0, 100.0 - (rms_error_ms / threshold_ms) * 100)

    return score, rms_error_ms, max_error_ms


def calculate_final_score(
    text_score: float, timing_score: float, text_weight: float = 0.6, timing_weight: float = 0.4
) -> float:
    """Calculate weighted final score.

    Args:
        text_score: Text accuracy score (0-100)
        timing_score: Timing accuracy score (0-100)
        text_weight: Weight for text score
        timing_weight: Weight for timing score

    Returns:
        Final weighted score (0-100)
    """
    return text_score * text_weight + timing_score * timing_weight


# --------------------------------------------------------------------------
# Report Formatting
# --------------------------------------------------------------------------


def format_timestamp(seconds: float) -> str:
    """Format seconds as mm:ss.xx timestamp."""
    minutes = int(seconds // 60)
    secs = seconds % 60
    return f"{minutes:02d}:{secs:05.2f}"


def format_diff_report(
    result: EvaluationResult, song_title: Optional[str] = None, song_id: Optional[str] = None, verbose: bool = False
) -> str:
    """Format evaluation result as a text report.

    Args:
        result: Evaluation result
        song_title: Optional song title for header
        song_id: Optional song ID for header
        verbose: Include word-by-word diff

    Returns:
        Formatted report string
    """
    lines = []
    lines.append("=== LRC Evaluation Report ===")
    lines.append("")

    if song_title or song_id:
        title_str = song_title or ""
        if song_id:
            title_str += f" ({song_id})" if title_str else song_id
        lines.append(f"Song: {title_str}")
        lines.append("")

    if not result.success:
        lines.append(f"Error: {result.error_message}")
        return "\n".join(lines)

    stats = result.stats
    scores = result.scores

    lines.append("--- Statistics ---")
    lines.append(f"LRC words:      {stats.lrc_word_count:5}    |  Audio words:    {stats.audio_word_count:5}")
    lines.append(f"Matched:        {stats.matched_count:5}    |  Missing: {stats.missing_count}  |  Extra: {stats.extra_count}")
    lines.append("")

    lines.append("--- Timing ---")
    lines.append(f"RMS error:      {stats.rms_error_ms:5.1f} ms  |  Max: {stats.max_error_ms:.1f} ms")
    lines.append("")

    lines.append("--- Scores ---")
    lines.append(f"Text accuracy:   {scores.text_accuracy:5.1f} / 100  (weight: {scores.text_weight})")
    lines.append(f"Timing accuracy: {scores.timing_accuracy:5.1f} / 100  (weight: {scores.timing_weight})")
    lines.append("─" * 35)
    lines.append(f"Final score:     {scores.final_score:5.1f} / 100")

    if verbose and result.diff_entries:
        lines.append("")
        lines.append("--- Word-by-Word Diff ---")
        lines.append("")

        for entry in result.diff_entries:
            if entry.op == "equal":
                time_diff_str = f"{entry.time_diff * 1000:+.0f}ms" if entry.time_diff else ""
                lrc_time_str = format_timestamp(entry.lrc_time) if entry.lrc_time else ""
                lines.append(f"  = {entry.lrc_pinyin:8} [{lrc_time_str}] {time_diff_str}")
            elif entry.op == "delete":
                lrc_time_str = format_timestamp(entry.lrc_time) if entry.lrc_time else ""
                lines.append(f"  - {entry.lrc_pinyin:8} [{lrc_time_str}] (missing in audio)")
            elif entry.op == "insert":
                audio_time_str = format_timestamp(entry.audio_time) if entry.audio_time else ""
                lines.append(f"  + {entry.audio_pinyin:8} [{audio_time_str}] (extra in audio)")

    return "\n".join(lines)


def format_json_report(result: EvaluationResult, song_title: Optional[str] = None, song_id: Optional[str] = None) -> str:
    """Format evaluation result as JSON.

    Args:
        result: Evaluation result
        song_title: Optional song title
        song_id: Optional song ID

    Returns:
        JSON string
    """
    import json

    data = {
        "success": result.success,
        "song_title": song_title,
        "song_id": song_id,
    }

    if not result.success:
        data["error"] = result.error_message
    else:
        data["stats"] = {
            "lrc_word_count": result.stats.lrc_word_count,
            "audio_word_count": result.stats.audio_word_count,
            "matched_count": result.stats.matched_count,
            "missing_count": result.stats.missing_count,
            "extra_count": result.stats.extra_count,
            "rms_error_ms": round(result.stats.rms_error_ms, 2),
            "max_error_ms": round(result.stats.max_error_ms, 2),
        }
        data["scores"] = {
            "text_accuracy": round(result.scores.text_accuracy, 2),
            "timing_accuracy": round(result.scores.timing_accuracy, 2),
            "final_score": round(result.scores.final_score, 2),
            "text_weight": result.scores.text_weight,
            "timing_weight": result.scores.timing_weight,
        }

    return json.dumps(data, ensure_ascii=False, indent=2)


def format_line_diff_report(
    result: EvaluationResult,
    lrc_lines: list[tuple[float, str]],
    song_title: Optional[str] = None,
    song_id: Optional[str] = None,
) -> str:
    """Format evaluation result as line-by-line side-by-side diff.

    Shows original lyrics lines with Chinese characters and pinyin,
    comparing LRC content vs audio transcription in a code-diff style.

    Args:
        result: Evaluation result
        lrc_lines: Original LRC lines as (timestamp, text) tuples
        song_title: Optional song title for header
        song_id: Optional song ID for header

    Returns:
        Formatted report string with side-by-side diff
    """
    lines = []
    lines.append("=== LRC Evaluation Report ===")
    lines.append("")

    if song_title or song_id:
        title_str = song_title or ""
        if song_id:
            title_str += f" ({song_id})" if title_str else song_id
        lines.append(f"Song: {title_str}")
        lines.append("")

    if not result.success:
        lines.append(f"Error: {result.error_message}")
        return "\n".join(lines)

    stats = result.stats
    scores = result.scores

    lines.append("--- Statistics ---")
    lines.append(f"LRC words:      {stats.lrc_word_count:5}    |  Audio words:    {stats.audio_word_count:5}")
    lines.append(f"Matched:        {stats.matched_count:5}    |  Missing: {stats.missing_count}  |  Extra: {stats.extra_count}")
    lines.append("")

    lines.append("--- Timing ---")
    lines.append(f"RMS error:      {stats.rms_error_ms:5.1f} ms  |  Max: {stats.max_error_ms:.1f} ms")
    lines.append("")

    lines.append("--- Scores ---")
    lines.append(f"Text accuracy:   {scores.text_accuracy:5.1f} / 100  (weight: {scores.text_weight})")
    lines.append(f"Timing accuracy: {scores.timing_accuracy:5.1f} / 100  (weight: {scores.timing_weight})")
    lines.append("─" * 35)
    lines.append(f"Final score:     {scores.final_score:5.1f} / 100")
    lines.append("")

    # Line-by-line diff section
    lines.append("--- Line-by-Line Diff ---")
    lines.append("")
    lines.append("Legend: = matched | - missing from audio | + extra in audio")
    lines.append("        [LRC time] → [Audio time] (diff)")
    lines.append("")

    # Group diff entries by approximate LRC line boundaries
    if not result.diff_entries or not lrc_lines:
        lines.append("(No diff data available)")
        return "\n".join(lines)

    # Build line boundaries from LRC
    line_boundaries = []
    for i, (ts, text) in enumerate(lrc_lines):
        next_ts = lrc_lines[i + 1][0] if i + 1 < len(lrc_lines) else ts + 10.0
        line_boundaries.append((ts, next_ts, text))

    # Assign diff entries to lines
    diff_by_line: dict[int, list[DiffEntry]] = {i: [] for i in range(len(line_boundaries))}
    unassigned: list[DiffEntry] = []

    for entry in result.diff_entries:
        # Use LRC time for equal/delete, audio time for insert
        ref_time = entry.lrc_time if entry.lrc_time is not None else entry.audio_time
        if ref_time is None:
            unassigned.append(entry)
            continue

        assigned = False
        for i, (start, end, _) in enumerate(line_boundaries):
            if start <= ref_time < end:
                diff_by_line[i].append(entry)
                assigned = True
                break

        if not assigned:
            # Assign to closest line
            closest_idx = 0
            min_dist = float('inf')
            for i, (start, end, _) in enumerate(line_boundaries):
                dist = min(abs(ref_time - start), abs(ref_time - end))
                if dist < min_dist:
                    min_dist = dist
                    closest_idx = i
            diff_by_line[closest_idx].append(entry)

    # Format each line
    col_width = 45

    for line_idx, (start_ts, _, original_text) in enumerate(line_boundaries):
        entries = diff_by_line.get(line_idx, [])
        if not entries and not original_text.strip():
            continue  # Skip empty lines with no diff

        # Header: timestamp and original text
        ts_str = format_timestamp(start_ts)
        lines.append(f"[{ts_str}] {original_text}")

        # Build LRC side and Audio side
        lrc_chars = []
        lrc_pinyins = []
        audio_chars = []
        audio_pinyins = []
        markers = []  # '=' '-' '+'

        for entry in entries:
            if entry.op == "equal":
                lrc_chars.append(entry.lrc_text or "")
                lrc_pinyins.append(entry.lrc_pinyin or "")
                audio_chars.append(entry.audio_text or "")
                audio_pinyins.append(entry.audio_pinyin or "")
                markers.append("=")
            elif entry.op == "delete":
                lrc_chars.append(entry.lrc_text or "")
                lrc_pinyins.append(entry.lrc_pinyin or "")
                audio_chars.append("∅")
                audio_pinyins.append("—")
                markers.append("-")
            elif entry.op == "insert":
                lrc_chars.append("∅")
                lrc_pinyins.append("—")
                audio_chars.append(entry.audio_text or "")
                audio_pinyins.append(entry.audio_pinyin or "")
                markers.append("+")

        if not markers:
            lines.append("  (no words)")
            lines.append("")
            continue

        # Format as table-like rows
        # Row 1: Chinese characters with markers
        char_row_lrc = "  LRC:   "
        char_row_audio = "  Audio: "
        pinyin_row_lrc = "         "
        pinyin_row_audio = "         "

        for i, marker in enumerate(markers):
            lc = lrc_chars[i] if i < len(lrc_chars) else ""
            ac = audio_chars[i] if i < len(audio_chars) else ""
            lp = lrc_pinyins[i] if i < len(lrc_pinyins) else ""
            ap = audio_pinyins[i] if i < len(audio_pinyins) else ""

            # Color coding via marker prefix
            if marker == "=":
                prefix = " "
            elif marker == "-":
                prefix = "-"
            elif marker == "+":
                prefix = "+"
            else:
                prefix = " "

            # Fixed width cells
            cell_width = max(len(lc), len(ac), len(lp), len(ap), 2) + 1

            char_row_lrc += f"{prefix}{lc:<{cell_width}}"
            char_row_audio += f"{prefix}{ac:<{cell_width}}"
            pinyin_row_lrc += f" {lp:<{cell_width}}"
            pinyin_row_audio += f" {ap:<{cell_width}}"

        lines.append(char_row_lrc.rstrip())
        lines.append(pinyin_row_lrc.rstrip())
        lines.append(char_row_audio.rstrip())
        lines.append(pinyin_row_audio.rstrip())

        # Timing summary for this line
        matched_entries = [e for e in entries if e.op == "equal" and e.time_diff is not None]
        if matched_entries:
            avg_diff = sum(e.time_diff for e in matched_entries) / len(matched_entries)
            max_diff = max(abs(e.time_diff) for e in matched_entries)
            lines.append(f"  Timing: avg {avg_diff*1000:+.0f}ms, max {max_diff*1000:.0f}ms")

        lines.append("")

    # Show any unassigned entries
    if unassigned:
        lines.append("--- Unassigned Entries ---")
        for entry in unassigned:
            if entry.op == "insert":
                lines.append(f"  + {entry.audio_text}({entry.audio_pinyin})")

    return "\n".join(lines)


# --------------------------------------------------------------------------
# Main Evaluation Function
# --------------------------------------------------------------------------


def evaluate_lrc(
    lrc_words: list[PinyinWord],
    audio_words: list[PinyinWord],
    text_weight: float = 0.6,
    timing_weight: float = 0.4,
    timing_threshold_ms: float = 500.0,
) -> EvaluationResult:
    """Run full LRC evaluation.

    Args:
        lrc_words: Parsed LRC pinyin words
        audio_words: Transcribed audio pinyin words
        text_weight: Weight for text accuracy
        timing_weight: Weight for timing accuracy
        timing_threshold_ms: Threshold for timing score calculation

    Returns:
        EvaluationResult with stats, scores, and diff
    """
    # Align sequences
    diff = align_sequences(lrc_words, audio_words)

    # Calculate statistics
    matched_count = sum(1 for d in diff if d.op == "equal")
    missing_count = sum(1 for d in diff if d.op == "delete")
    extra_count = sum(1 for d in diff if d.op == "insert")

    # Calculate scores
    text_score = calculate_text_score(diff)
    timing_score, rms_error_ms, max_error_ms = calculate_timing_score(diff, timing_threshold_ms)
    final_score = calculate_final_score(text_score, timing_score, text_weight, timing_weight)

    stats = EvaluationStats(
        lrc_word_count=len(lrc_words),
        audio_word_count=len(audio_words),
        matched_count=matched_count,
        missing_count=missing_count,
        extra_count=extra_count,
        rms_error_ms=rms_error_ms,
        max_error_ms=max_error_ms,
    )

    scores = EvaluationScores(
        text_accuracy=text_score,
        timing_accuracy=timing_score,
        final_score=final_score,
        text_weight=text_weight,
        timing_weight=timing_weight,
    )

    return EvaluationResult(
        success=True,
        stats=stats,
        scores=scores,
        diff_entries=diff,
    )


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


app = typer.Typer(help="Evaluate LRC lyrics file accuracy against audio transcription")


@app.command()
def main(
    song_id: Optional[str] = typer.Argument(None, help="Song ID (uses cached LRC and vocals)"),
    lrc: Optional[Path] = typer.Option(None, "--lrc", "-l", help="Local LRC file path"),
    audio: Optional[Path] = typer.Option(None, "--audio", "-a", help="Local audio file path"),
    engine: str = typer.Option(
        "whisper",
        "--engine",
        "-e",
        help="Transcription engine: whisper, sensevoice, paraformer",
    ),
    model: Optional[str] = typer.Option(
        None,
        "--model",
        "-m",
        help="Model name (default: large-v3 for whisper, SenseVoiceSmall for sensevoice)",
    ),
    device: str = typer.Option("cpu", "--device", "-d", help="Device to run on (cpu/cuda/mps)"),
    compute_type: str = typer.Option("int8", "--compute-type", "-c", help="Compute type (int8/float16, whisper only)"),
    text_weight: float = typer.Option(0.6, "--text-weight", help="Text accuracy weight"),
    timing_weight: float = typer.Option(0.4, "--timing-weight", help="Timing accuracy weight"),
    timing_threshold: float = typer.Option(500.0, "--timing-threshold", help="RMS threshold for 0 timing score (ms)"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Output file (default: stdout)"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show word-by-word diff"),
):
    """Evaluate LRC lyrics file accuracy against audio transcription.

    Supported transcription engines:
    - whisper: OpenAI Whisper via faster-whisper (default)
    - sensevoice: FunASR SenseVoice (Chinese-optimized)
    - paraformer: FunASR Paraformer (fast, Chinese-optimized)

    Usage examples:

    1. Song ID mode with default (whisper):
       uv run --extra lrc_eval poc/eval_lrc.py wo_yao_quan_xin_zan_mei_244

    2. Compare engines:
       uv run --extra lrc_eval poc/eval_lrc.py <song_id> --engine whisper
       uv run --extra lrc_eval poc/eval_lrc.py <song_id> --engine sensevoice
       uv run --extra lrc_eval poc/eval_lrc.py <song_id> --engine paraformer

    3. Local file mode:
       uv run --extra lrc_eval poc/eval_lrc.py --lrc lyrics.lrc --audio vocals.wav -e sensevoice
    """
    console = Console(stderr=True)
    song_title = None

    # Validate input mode
    if song_id and (lrc or audio):
        console.print("Error: Cannot specify both song_id and --lrc/--audio", style="red")
        raise typer.Exit(1)

    if not song_id and not (lrc and audio):
        console.print("Error: Must specify either song_id or both --lrc and --audio", style="red")
        raise typer.Exit(1)

    # Resolve file paths
    lrc_path: Optional[Path] = None
    audio_path: Optional[Path] = None

    if song_id:
        # Song ID mode - use AssetCache
        try:
            from stream_of_worship.app.config import AppConfig
            from stream_of_worship.app.db.read_client import ReadOnlyClient
            from stream_of_worship.app.services.catalog import CatalogService
            from stream_of_worship.app.services.asset_cache import AssetCache
            from stream_of_worship.admin.services.r2 import R2Client

            config = AppConfig.load()
            db_client = ReadOnlyClient(config.db_path)
            catalog = CatalogService(db_client)

            # Look up song
            song_with_recording = catalog.get_song_with_recording(song_id)
            if not song_with_recording:
                console.print(f"Error: Song not found: {song_id}", style="red")
                raise typer.Exit(1)

            if not song_with_recording.recording:
                console.print(f"Error: No recording found for song: {song_id}", style="red")
                raise typer.Exit(1)

            song = song_with_recording.song
            recording = song_with_recording.recording
            hash_prefix = recording.hash_prefix
            song_title = song.title

            console.print(f"Song: {song.title}", style="bold")
            console.print(f"Recording: {hash_prefix}", style="dim")

            # Initialize R2 and cache
            r2_client = R2Client(
                bucket=config.r2_bucket,
                endpoint_url=config.r2_endpoint_url,
                region=config.r2_region,
            )
            cache = AssetCache(cache_dir=config.cache_dir, r2_client=r2_client)

            # Get LRC file
            lrc_path = cache.get_lrc_path(hash_prefix)
            if not lrc_path.exists():
                console.print("Downloading LRC file...", style="dim")
                lrc_path = cache.download_lrc(hash_prefix)
                if not lrc_path:
                    console.print("Error: Could not download LRC file", style="red")
                    raise typer.Exit(1)

            # Get vocals stem
            audio_path = cache.get_stem_path(hash_prefix, "vocals")
            if not audio_path.exists():
                console.print("Downloading vocals stem...", style="dim")
                audio_path = cache.download_stem(hash_prefix, "vocals")
                if not audio_path:
                    console.print("Error: Could not download vocals stem", style="red")
                    raise typer.Exit(1)

        except FileNotFoundError:
            console.print("Error: Config file not found. Run 'sow-app' first.", style="red")
            raise typer.Exit(1)
        except ValueError as e:
            console.print(f"Error: R2 credentials not configured: {e}", style="red")
            raise typer.Exit(1)
    else:
        # Local file mode
        lrc_path = lrc
        audio_path = audio

    # Verify files exist
    if not lrc_path.exists():
        console.print(f"Error: LRC file not found: {lrc_path}", style="red")
        raise typer.Exit(1)

    if not audio_path.exists():
        console.print(f"Error: Audio file not found: {audio_path}", style="red")
        raise typer.Exit(1)

    console.print(f"LRC: {lrc_path}", style="dim")
    console.print(f"Audio: {audio_path}", style="dim")

    # Parse LRC
    console.print("Parsing LRC file...", style="dim")
    lrc_content = lrc_path.read_text(encoding="utf-8")
    lrc_words = parse_lrc_file(lrc_content)
    lrc_lines = extract_lrc_lines(lrc_content)  # For line-by-line diff
    console.print(f"Parsed {len(lrc_words)} pinyin syllables from LRC ({len(lrc_lines)} lines)", style="dim")

    # Validate engine
    if engine not in ENGINES:
        console.print(f"Error: Unknown engine '{engine}'. Supported: {ENGINES}", style="red")
        raise typer.Exit(1)

    console.print(f"Engine: {engine}", style="dim")

    # Transcribe audio
    audio_words = transcribe_audio(
        audio_path=audio_path,
        engine=engine,
        model_name=model,
        device=device,
        compute_type=compute_type,
    )

    # Run evaluation
    console.print("Running evaluation...", style="dim")
    result = evaluate_lrc(
        lrc_words=lrc_words,
        audio_words=audio_words,
        text_weight=text_weight,
        timing_weight=timing_weight,
        timing_threshold_ms=timing_threshold,
    )

    # Format output
    if json_output:
        report = format_json_report(result, song_title=song_title, song_id=song_id)
    elif verbose:
        # Use line-by-line diff format for verbose mode
        report = format_line_diff_report(result, lrc_lines, song_title=song_title, song_id=song_id)
    else:
        report = format_diff_report(result, song_title=song_title, song_id=song_id, verbose=False)

    # Output
    if output:
        output.write_text(report, encoding="utf-8")
        console.print(f"Report written to: {output}", style="green")
    else:
        print(report)


if __name__ == "__main__":
    app()
