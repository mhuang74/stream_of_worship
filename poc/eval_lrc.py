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
        rms_error_ms: RMS timing error in milliseconds (after offset normalization)
        max_error_ms: Maximum timing error in milliseconds (after offset normalization)
        time_offset_ms: Detected global time offset in milliseconds (audio - LRC)
        exact_matches: Number of exact character matches
        homophone_matches: Number of homophone (same pinyin, different char) matches
        pinyin_accuracy: Accuracy based on pinyin matching (includes homophones)
    """

    lrc_word_count: int
    audio_word_count: int
    matched_count: int
    missing_count: int
    extra_count: int
    rms_error_ms: float
    max_error_ms: float
    time_offset_ms: float = 0.0
    exact_matches: int = 0
    homophone_matches: int = 0
    pinyin_accuracy: float = 0.0


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


@dataclass
class VADSegment:
    """A voice activity detection segment.

    Attributes:
        start_ms: Start time in milliseconds
        end_ms: End time in milliseconds
    """

    start_ms: int
    end_ms: int

    @property
    def start_seconds(self) -> float:
        """Start time in seconds."""
        return self.start_ms / 1000.0

    @property
    def end_seconds(self) -> float:
        """End time in seconds."""
        return self.end_ms / 1000.0

    @property
    def duration_seconds(self) -> float:
        """Duration in seconds."""
        return (self.end_ms - self.start_ms) / 1000.0


# --------------------------------------------------------------------------
# Pinyin Conversion
# --------------------------------------------------------------------------

# Custom pinyin overrides for characters with context-dependent pronunciations.
# pypinyin defaults may be incorrect for worship song contexts.
PINYIN_OVERRIDES = {
    "祢": "ni",  # Respectful "You" (God) - not surname "mi"
    "禰": "ni",  # Variant of 祢
}


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

    # Apply custom overrides for worship song context
    final_result = []
    for i, char in enumerate(chinese_chars):
        if char in PINYIN_OVERRIDES:
            final_result.append(PINYIN_OVERRIDES[char])
        elif i < len(result):
            final_result.append(result[i].lower())

    return [p for p in final_result if p]


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
    model_name: str = "large-v2",
    device: str = "cpu",
    compute_type: str = "int8",
    language: str = "zh",
    lyrics_text: Optional[str] = None,
) -> list[PinyinWord]:
    """Transcribe audio using faster-whisper with word-level timestamps.

    Args:
        audio_path: Path to audio file
        model_name: Whisper model name (e.g., large-v3, medium, small)
        device: Device to run on (cpu/cuda/mps)
        compute_type: Compute type (int8/float16/int8_float16)
        language: Language hint
        lyrics_text: Optional published lyrics to improve transcription accuracy

    Returns:
        List of PinyinWord with pinyin and timestamps
    """
    from faster_whisper import WhisperModel

    console = Console(stderr=True)
    console.print(f"[whisper] Loading model: {model_name} on {device}", style="dim")

    model = WhisperModel(model_name, device=device, compute_type=compute_type)

    console.print(f"[whisper] Transcribing: {audio_path}", style="dim")

    # Build dynamic initial prompt with lyrics if available
    if lyrics_text:
        # Take first 50 lines and truncate to 2000 characters max
        lyrics_truncated = "\n".join(lyrics_text.split("\n")[:50])
        if len(lyrics_truncated) > 2000:
            lyrics_truncated = lyrics_truncated[:2000]
        initial_prompt = f"这是一首中文敬拜诗歌。歌词如下：\n{lyrics_truncated}"
        console.print(f"[whisper] Using lyrics-enhanced prompt ({len(lyrics_truncated)} chars)", style="dim")
    else:
        initial_prompt = "这是一首中文敬拜诗歌"

    segments, info = model.transcribe(
        str(audio_path),
        language=language,
        beam_size=5,
        word_timestamps=True,
        initial_prompt=initial_prompt,
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
    batch_size_s: int = 60,
    use_itn: bool = False,
    disable_vad: bool = False,
    vad_max_silence: int = 1000,
    vad_threshold: float = 0.5,
    debug: bool = False,
    **kwargs,
) -> list[PinyinWord]:
    """Transcribe audio using FunASR SenseVoice model.

    SenseVoice is optimized for Chinese and supports emotion/event detection.

    Args:
        audio_path: Path to audio file
        model_name: SenseVoice model ID (default: iic/SenseVoiceSmall)
        device: Device to run on (cpu/cuda)
        batch_size_s: Batch size in seconds for processing (default: 60)
        use_itn: Use inverse text normalization (default: False for better alignment)
        disable_vad: Disable internal VAD (default: False)
        vad_max_silence: VAD max end silence in ms (default: 1000)
        vad_threshold: VAD speech/noise threshold 0-1 (default: 0.5)
        debug: Enable debug output (default: False)
        **kwargs: Additional arguments (ignored for compatibility)

    Returns:
        List of PinyinWord with pinyin and timestamps
    """
    from funasr import AutoModel

    console = Console(stderr=True)
    console.print(f"[sensevoice] Loading model: {model_name} on {device}", style="dim")

    # SenseVoice model with configurable VAD
    if disable_vad:
        model = AutoModel(
            model=model_name,
            trust_remote_code=True,
            device=device,
        )
    else:
        vad_kwargs = {
            "max_end_silence": vad_max_silence,
            "speech_noise_thres": vad_threshold,
        }
        model = AutoModel(
            model=model_name,
            trust_remote_code=True,
            device=device,
            vad_model="fsmn-vad",
            vad_kwargs=vad_kwargs,
        )

    console.print(f"[sensevoice] Transcribing: {audio_path}", style="dim")
    if debug:
        console.print(
            f"[sensevoice] Parameters: use_itn={use_itn}, disable_vad={disable_vad}, "
            f"vad_max_silence={vad_max_silence}, vad_threshold={vad_threshold}",
            style="dim"
        )

    # SenseVoice inference
    gen_kwargs = {
        "input": str(audio_path),
        "cache": {},
        "language": "zh",
        "batch_size_s": batch_size_s,
    }
    if use_itn:
        gen_kwargs["use_itn"] = True

    res = model.generate(**gen_kwargs)

    result = []
    raw_transcription = []  # For debug output

    # Patterns to remove from SenseVoice output
    event_patterns = [
        r"<\|[^|]+\|>",      # <|HAPPY|>, <|EMOTION|>, etc.
        r"\|[^|]+\|",         # |zh|, |en|, language tags
        r"<[^>]+>",           # Any other tags
        r"\[.*?\]",           # Bracketed content
    ]

    for i, item in enumerate(res):
        # SenseVoice returns sentence-level results
        text = item.get("text", "")
        timestamp = item.get("timestamp", [])

        if debug and i < 5:
            console.print(f"[sensevoice DEBUG] Segment {i}: text='{text[:100]}'", style="dim")
            if timestamp:
                console.print(f"[sensevoice DEBUG] Segment {i}: first 3 timestamps={timestamp[:3]}", style="dim")

        if text:
            # Clean up SenseVoice output comprehensively
            clean_text = text
            for pattern in event_patterns:
                clean_text = re.sub(pattern, "", clean_text)
            clean_text = clean_text.strip()
            raw_transcription.append(clean_text)

            # Extract Chinese characters
            chars = [c for c in clean_text if '\u4e00' <= c <= '\u9fff']

            if timestamp and len(timestamp) > 0 and len(chars) > 0:
                # Has word-level timestamps: [[start_ms, end_ms], ...]
                if len(chars) <= len(timestamp):
                    for j, char in enumerate(chars):
                        start_ms = timestamp[j][0]
                        time_sec = start_ms / 1000.0
                        pinyin_list = chinese_to_pinyin(char)
                        for py in pinyin_list:
                            result.append(PinyinWord(text=char, pinyin=py, time_seconds=time_sec))
                else:
                    # Fewer timestamps than characters - interpolate
                    num_chars = len(chars)
                    num_ts = len(timestamp)
                    first_ts = timestamp[0][0] / 1000.0
                    last_ts = timestamp[-1][0] / 1000.0
                    time_span = last_ts - first_ts

                    for j, char in enumerate(chars):
                        if num_ts > 1:
                            ratio = j / (num_chars - 1) if num_chars > 1 else 0
                            time_sec = first_ts + (time_span * ratio)
                        else:
                            time_sec = first_ts
                        pinyin_list = chinese_to_pinyin(char)
                        for py in pinyin_list:
                            result.append(PinyinWord(text=char, pinyin=py, time_seconds=time_sec))
            elif chars:
                # No timestamps - use segment start time and interpolate
                segment_start = item.get("start", 0) / 1000.0 if "start" in item else 0.0
                segment_end = item.get("end", 0) / 1000.0 if "end" in item else segment_start + 5.0
                duration = segment_end - segment_start
                time_per_char = duration / len(chars) if len(chars) > 0 else 0.2

                for j, char in enumerate(chars):
                    time_sec = segment_start + j * time_per_char
                    pinyin_list = chinese_to_pinyin(char)
                    for py in pinyin_list:
                        result.append(PinyinWord(text=char, pinyin=py, time_seconds=time_sec))

    console.print(f"[sensevoice] Transcribed {len(result)} pinyin syllables from {len(raw_transcription)} segments", style="dim")

    # Debug output
    full_raw = " ".join(raw_transcription)
    if debug:
        console.print(f"[sensevoice DEBUG] Raw transcription length: {len(full_raw)} chars", style="dim")
        console.print(f"[sensevoice DEBUG] First 500 chars: {full_raw[:500]}", style="dim")

    # Warn if no Chinese content was detected
    if len(result) == 0 and full_raw.strip():
        console.print(
            f"[sensevoice] Warning: No Chinese characters detected. Raw transcription:\n"
            f"  {full_raw[:200]}{'...' if len(full_raw) > 200 else ''}",
            style="yellow"
        )

    return result


def transcribe_with_paraformer(
    audio_path: Path,
    model_name: str = "paraformer-zh",
    device: str = "cpu",
    batch_size_s: int = 60,
    disable_vad: bool = False,
    vad_max_silence: int = 1000,
    vad_threshold: float = 0.5,
    debug: bool = False,
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
        batch_size_s: Batch size in seconds for processing (default: 60)
        disable_vad: Disable internal VAD (default: False)
        vad_max_silence: VAD max end silence in ms (default: 1000)
        vad_threshold: VAD speech/noise threshold 0-1 (default: 0.5)
        debug: Enable debug output (default: False)
        **kwargs: Additional arguments (ignored for compatibility)

    Returns:
        List of PinyinWord with pinyin and timestamps
    """
    from funasr import AutoModel

    console = Console(stderr=True)
    console.print(f"[paraformer] Loading model: {model_name} on {device}", style="dim")

    # Use Paraformer-zh with configurable VAD
    if disable_vad:
        model = AutoModel(
            model=model_name,
            model_revision="v2.0.4",
            device=device,
        )
    else:
        vad_kwargs = {
            "max_end_silence": vad_max_silence,
            "speech_noise_thres": vad_threshold,
        }
        model = AutoModel(
            model=model_name,
            model_revision="v2.0.4",
            vad_model="fsmn-vad",
            vad_model_revision="v2.0.4",
            vad_kwargs=vad_kwargs,
            device=device,
        )

    console.print(f"[paraformer] Transcribing: {audio_path}", style="dim")
    if debug:
        console.print(
            f"[paraformer] Parameters: disable_vad={disable_vad}, "
            f"vad_max_silence={vad_max_silence}, vad_threshold={vad_threshold}",
            style="dim"
        )

    # Paraformer inference with timestamps
    res = model.generate(
        input=str(audio_path),
        batch_size_s=batch_size_s,
    )

    result = []
    for i, item in enumerate(res):
        text = item.get("text", "")
        timestamp = item.get("timestamp", [])

        if debug and i < 5:
            console.print(f"[paraformer DEBUG] Segment {i}: text='{text[:100] if text else ''}'", style="dim")

        if text:
            chars = list(re.findall(r"[\u4e00-\u9fff]", text))

            if timestamp and len(timestamp) > 0:
                # Has character-level timestamps
                for j, char in enumerate(chars):
                    if j < len(timestamp):
                        start_ms = timestamp[j][0]
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

                    for j, char in enumerate(chars):
                        time_sec = segment_start + j * time_per_char
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
    batch_size_s: int = 60,
    # SenseVoice-specific
    use_itn: bool = False,
    sensevoice_disable_vad: bool = False,
    sensevoice_vad_max_silence: int = 1000,
    sensevoice_vad_threshold: float = 0.5,
    # Paraformer-specific
    paraformer_disable_vad: bool = False,
    paraformer_vad_max_silence: int = 1000,
    paraformer_vad_threshold: float = 0.5,
    # Whisper-specific
    lyrics_text: Optional[str] = None,
    # Debug
    debug: bool = False,
) -> list[PinyinWord]:
    """Transcribe audio using the specified engine.

    Args:
        audio_path: Path to audio file
        engine: Transcription engine (whisper, sensevoice, paraformer)
        model_name: Model name/ID (engine-specific, None for default)
        device: Device to run on
        compute_type: Compute type (whisper only)
        batch_size_s: Batch size in seconds for processing (default: 60)
        use_itn: Use ITN for SenseVoice (default: False)
        sensevoice_disable_vad: Disable internal VAD for SenseVoice
        sensevoice_vad_max_silence: VAD max silence for SenseVoice
        sensevoice_vad_threshold: VAD threshold for SenseVoice
        paraformer_disable_vad: Disable internal VAD for Paraformer
        paraformer_vad_max_silence: VAD max silence for Paraformer
        paraformer_vad_threshold: VAD threshold for Paraformer
        lyrics_text: Optional lyrics text to improve Whisper transcription
        debug: Enable debug output

    Returns:
        List of PinyinWord with pinyin and timestamps
    """
    if engine == "whisper":
        return transcribe_with_whisper(
            audio_path,
            model_name=model_name or "large-v2",
            device=device,
            compute_type=compute_type,
            lyrics_text=lyrics_text,
        )
    elif engine == "sensevoice":
        return transcribe_with_sensevoice(
            audio_path,
            model_name=model_name or "iic/SenseVoiceSmall",
            device=device,
            batch_size_s=batch_size_s,
            use_itn=use_itn,
            disable_vad=sensevoice_disable_vad,
            vad_max_silence=sensevoice_vad_max_silence,
            vad_threshold=sensevoice_vad_threshold,
            debug=debug,
        )
    elif engine == "paraformer":
        return transcribe_with_paraformer(
            audio_path,
            model_name=model_name or "paraformer-zh",
            device=device,
            batch_size_s=batch_size_s,
            disable_vad=paraformer_disable_vad,
            vad_max_silence=paraformer_vad_max_silence,
            vad_threshold=paraformer_vad_threshold,
            debug=debug,
        )
    else:
        raise ValueError(f"Unknown engine: {engine}. Supported: {ENGINES}")


# --------------------------------------------------------------------------
# VAD Pre-processing
# --------------------------------------------------------------------------


def run_vad_segmentation(
    audio_path: Path,
    device: str = "cpu",
) -> list[VADSegment]:
    """Run Voice Activity Detection to segment audio.

    Uses FunASR fsmn-vad model to detect speech regions.

    Args:
        audio_path: Path to audio file
        device: Device to run on (cpu/cuda)

    Returns:
        List of VADSegment with speech regions
    """
    from funasr import AutoModel

    console = Console(stderr=True)
    console.print("[vad] Running VAD segmentation...", style="dim")

    vad_model = AutoModel(
        model="fsmn-vad",
        trust_remote_code=True,
        remote_code="./model.py",
        device=device,
    )

    vad_res = vad_model.generate(input=str(audio_path))
    segments_ms = vad_res[0].get("value", []) if vad_res else []

    segments = [VADSegment(start_ms=s[0], end_ms=s[1]) for s in segments_ms]
    console.print(f"[vad] Found {len(segments)} speech segments", style="dim")

    return segments


def merge_vad_segments(
    segments: list[VADSegment],
    max_len_s: int = 15,
    gap_ms: int = 200,
) -> list[VADSegment]:
    """Merge nearby VAD segments into longer spans.

    Merges adjacent segments with small gaps to reduce fragmentation.

    Args:
        segments: List of VAD segments
        max_len_s: Maximum merged segment length in seconds
        gap_ms: Maximum gap between segments to merge in milliseconds

    Returns:
        List of merged VADSegment
    """
    if not segments:
        return []
    if max_len_s <= 0:
        return segments

    merged: list[VADSegment] = []
    cur_start, cur_end = segments[0].start_ms, segments[0].end_ms
    max_len_ms = max_len_s * 1000

    for seg in segments[1:]:
        gap = seg.start_ms - cur_end
        new_len = seg.end_ms - cur_start
        if gap <= gap_ms and new_len <= max_len_ms:
            cur_end = seg.end_ms
        else:
            merged.append(VADSegment(start_ms=cur_start, end_ms=cur_end))
            cur_start, cur_end = seg.start_ms, seg.end_ms

    merged.append(VADSegment(start_ms=cur_start, end_ms=cur_end))
    return merged


def refine_vad_segments_with_silence(
    audio_path: Path,
    segments: list[VADSegment],
    silence_gap_ms: int = 500,
    silence_thresh_db: float = -40.0,
) -> list[VADSegment]:
    """Split VAD segments based on detected silence gaps.

    Uses pydub to detect silence within segments and splits them.

    Args:
        audio_path: Path to audio file
        segments: List of VAD segments to refine
        silence_gap_ms: Minimum silence gap length to split (ms)
        silence_thresh_db: Silence threshold in dBFS

    Returns:
        List of refined VADSegment
    """
    if silence_gap_ms <= 0 or not segments:
        return segments

    from pydub import AudioSegment
    from pydub.silence import detect_silence

    console = Console(stderr=True)
    console.print(f"[vad] Refining segments with silence detection (gap={silence_gap_ms}ms)", style="dim")

    audio = AudioSegment.from_file(str(audio_path))
    out: list[VADSegment] = []

    for seg in segments:
        segment_audio = audio[seg.start_ms:seg.end_ms]
        silences = detect_silence(
            segment_audio,
            min_silence_len=silence_gap_ms,
            silence_thresh=silence_thresh_db,
        )
        cur = seg.start_ms
        for s_start, s_end in silences:
            seg_end = seg.start_ms + s_start
            if seg_end > cur:
                out.append(VADSegment(start_ms=cur, end_ms=seg_end))
            cur = seg.start_ms + s_end
        if cur < seg.end_ms:
            out.append(VADSegment(start_ms=cur, end_ms=seg.end_ms))

    console.print(f"[vad] Refined to {len(out)} segments", style="dim")
    return out


def transcribe_segment(
    audio_path: Path,
    segment: VADSegment,
    engine: str,
    model_name: Optional[str] = None,
    device: str = "cpu",
    compute_type: str = "int8",
    batch_size_s: int = 60,
    # SenseVoice-specific
    use_itn: bool = False,
    sensevoice_disable_vad: bool = False,
    sensevoice_vad_max_silence: int = 1000,
    sensevoice_vad_threshold: float = 0.5,
    # Paraformer-specific
    paraformer_disable_vad: bool = False,
    paraformer_vad_max_silence: int = 1000,
    paraformer_vad_threshold: float = 0.5,
    # Whisper-specific
    lyrics_text: Optional[str] = None,
    # Debug
    debug: bool = False,
) -> list[PinyinWord]:
    """Transcribe a single audio segment and adjust timestamps.

    Args:
        audio_path: Path to audio file
        segment: VAD segment to transcribe
        engine: Transcription engine
        model_name: Model name/ID
        device: Device to run on
        compute_type: Compute type (whisper only)
        batch_size_s: Batch size in seconds
        use_itn: Use ITN for SenseVoice
        sensevoice_disable_vad: Disable internal VAD for SenseVoice
        sensevoice_vad_max_silence: VAD max silence for SenseVoice
        sensevoice_vad_threshold: VAD threshold for SenseVoice
        paraformer_disable_vad: Disable internal VAD for Paraformer
        paraformer_vad_max_silence: VAD max silence for Paraformer
        paraformer_vad_threshold: VAD threshold for Paraformer
        lyrics_text: Optional lyrics text to improve Whisper transcription
        debug: Enable debug output

    Returns:
        List of PinyinWord with timestamps adjusted by segment offset
    """
    from poc.utils import extract_audio_segment

    # Extract segment to temp file
    segment_path = extract_audio_segment(
        audio_path, segment.start_seconds, segment.end_seconds
    )

    try:
        # Transcribe segment
        words = transcribe_audio(
            audio_path=segment_path,
            engine=engine,
            model_name=model_name,
            device=device,
            compute_type=compute_type,
            batch_size_s=batch_size_s,
            use_itn=use_itn,
            sensevoice_disable_vad=sensevoice_disable_vad,
            sensevoice_vad_max_silence=sensevoice_vad_max_silence,
            sensevoice_vad_threshold=sensevoice_vad_threshold,
            paraformer_disable_vad=paraformer_disable_vad,
            paraformer_vad_max_silence=paraformer_vad_max_silence,
            paraformer_vad_threshold=paraformer_vad_threshold,
            lyrics_text=lyrics_text,
            debug=debug,
        )

        # Adjust timestamps by segment offset
        adjusted_words = [
            PinyinWord(
                text=w.text,
                pinyin=w.pinyin,
                time_seconds=w.time_seconds + segment.start_seconds,
            )
            for w in words
        ]
        return adjusted_words
    finally:
        # Clean up temp file
        if segment_path.exists():
            segment_path.unlink()


def build_lrc_segments(lrc_lines: list[tuple[float, str]]) -> list[VADSegment]:
    """Build segments from LRC line timestamps.

    Args:
        lrc_lines: List of (timestamp, text) tuples from LRC

    Returns:
        List of VADSegment based on LRC line boundaries
    """
    if not lrc_lines:
        return []

    segments = []
    for i, (timestamp, text) in enumerate(lrc_lines):
        # Skip empty lines
        if not text.strip():
            continue

        # Determine segment end (next line's start or timestamp + default duration)
        if i + 1 < len(lrc_lines):
            segment_end = lrc_lines[i + 1][0]
        else:
            segment_end = timestamp + 5.0  # Default 5s for last line

        # Skip very short segments
        if segment_end - timestamp < 0.1:
            continue

        segments.append(VADSegment(
            start_ms=int(timestamp * 1000),
            end_ms=int(segment_end * 1000),
        ))

    return segments


def transcribe_with_segmentation(
    audio_path: Path,
    engine: str = "whisper",
    model_name: Optional[str] = None,
    device: str = "cpu",
    compute_type: str = "int8",
    batch_size_s: int = 60,
    segment_mode: str = "vad",
    lrc_lines: Optional[list[tuple[float, str]]] = None,
    merge_segments: bool = True,
    merge_length_s: int = 15,
    split_on_silence: bool = False,
    silence_gap_ms: int = 500,
    silence_thresh_db: float = -40.0,
    # SenseVoice-specific
    use_itn: bool = False,
    sensevoice_disable_vad: bool = False,
    sensevoice_vad_max_silence: int = 1000,
    sensevoice_vad_threshold: float = 0.5,
    # Paraformer-specific
    paraformer_disable_vad: bool = False,
    paraformer_vad_max_silence: int = 1000,
    paraformer_vad_threshold: float = 0.5,
    # Whisper-specific
    lyrics_text: Optional[str] = None,
    # Debug
    debug: bool = False,
) -> list[PinyinWord]:
    """Transcribe audio with optional segmentation pre-processing.

    Orchestrates segmentation (VAD or LRC-based), optional merging/splitting,
    and per-segment transcription with timestamp adjustment.

    Args:
        audio_path: Path to audio file
        engine: Transcription engine (whisper, sensevoice, paraformer)
        model_name: Model name/ID (engine-specific, None for default)
        device: Device to run on
        compute_type: Compute type (whisper only)
        batch_size_s: Batch size in seconds for processing
        segment_mode: Segmentation mode: "vad", "lrc", or "none"
        lrc_lines: LRC lines for "lrc" segment mode
        merge_segments: Merge adjacent VAD segments (default: True)
        merge_length_s: Maximum merged segment length in seconds (default: 15)
        split_on_silence: Split segments on silence gaps (default: False)
        silence_gap_ms: Minimum silence gap to split (ms) (default: 500)
        silence_thresh_db: Silence threshold in dBFS (default: -40.0)
        use_itn: Use ITN for SenseVoice
        sensevoice_disable_vad: Disable internal VAD for SenseVoice
        sensevoice_vad_max_silence: VAD max silence for SenseVoice
        sensevoice_vad_threshold: VAD threshold for SenseVoice
        paraformer_disable_vad: Disable internal VAD for Paraformer
        paraformer_vad_max_silence: VAD max silence for Paraformer
        paraformer_vad_threshold: VAD threshold for Paraformer
        lyrics_text: Optional lyrics text to improve Whisper transcription
        debug: Enable debug output

    Returns:
        List of PinyinWord with pinyin and timestamps
    """
    console = Console(stderr=True)

    # If segment mode is "none", use direct transcription
    if segment_mode == "none":
        return transcribe_audio(
            audio_path=audio_path,
            engine=engine,
            model_name=model_name,
            device=device,
            compute_type=compute_type,
            batch_size_s=batch_size_s,
            use_itn=use_itn,
            sensevoice_disable_vad=sensevoice_disable_vad,
            sensevoice_vad_max_silence=sensevoice_vad_max_silence,
            sensevoice_vad_threshold=sensevoice_vad_threshold,
            paraformer_disable_vad=paraformer_disable_vad,
            paraformer_vad_max_silence=paraformer_vad_max_silence,
            paraformer_vad_threshold=paraformer_vad_threshold,
            lyrics_text=lyrics_text,
            debug=debug,
        )

    # Get segments based on mode
    segments: list[VADSegment] = []

    if segment_mode == "lrc":
        if not lrc_lines:
            console.print("[lrc] No LRC lines provided, falling back to direct transcription", style="yellow")
            return transcribe_audio(
                audio_path=audio_path,
                engine=engine,
                model_name=model_name,
                device=device,
                compute_type=compute_type,
                batch_size_s=batch_size_s,
                use_itn=use_itn,
                sensevoice_disable_vad=sensevoice_disable_vad,
                sensevoice_vad_max_silence=sensevoice_vad_max_silence,
                sensevoice_vad_threshold=sensevoice_vad_threshold,
                paraformer_disable_vad=paraformer_disable_vad,
                paraformer_vad_max_silence=paraformer_vad_max_silence,
                paraformer_vad_threshold=paraformer_vad_threshold,
                lyrics_text=lyrics_text,
                debug=debug,
            )
        segments = build_lrc_segments(lrc_lines)
        console.print(f"[lrc] Built {len(segments)} segments from LRC lines", style="dim")
    elif segment_mode == "vad":
        # Run VAD segmentation
        segments = run_vad_segmentation(audio_path, device=device)
        if not segments:
            console.print("[vad] No speech segments found, falling back to direct transcription", style="yellow")
            return transcribe_audio(
                audio_path=audio_path,
                engine=engine,
                model_name=model_name,
                device=device,
                compute_type=compute_type,
                batch_size_s=batch_size_s,
                use_itn=use_itn,
                sensevoice_disable_vad=sensevoice_disable_vad,
                sensevoice_vad_max_silence=sensevoice_vad_max_silence,
                sensevoice_vad_threshold=sensevoice_vad_threshold,
                paraformer_disable_vad=paraformer_disable_vad,
                paraformer_vad_max_silence=paraformer_vad_max_silence,
                paraformer_vad_threshold=paraformer_vad_threshold,
                lyrics_text=lyrics_text,
                debug=debug,
            )
    else:
        raise ValueError(f"Unknown segment_mode: {segment_mode}. Supported: vad, lrc, none")

    # Merge segments if enabled (VAD mode only)
    if merge_segments and segment_mode == "vad":
        segments = merge_vad_segments(segments, max_len_s=merge_length_s)
        console.print(f"[vad] Merged to {len(segments)} segments", style="dim")

    # Split on silence if enabled (VAD mode only)
    if split_on_silence and silence_gap_ms > 0 and segment_mode == "vad":
        segments = refine_vad_segments_with_silence(
            audio_path, segments, silence_gap_ms=silence_gap_ms, silence_thresh_db=silence_thresh_db
        )

    # Transcribe each segment
    mode_label = segment_mode
    console.print(f"[{mode_label}] Transcribing {len(segments)} segments...", style="dim")
    all_words: list[PinyinWord] = []

    # Build segment-specific lyrics for LRC mode (1:1 mapping with non-empty lines)
    segment_lyrics: list[Optional[str]] = []
    if segment_mode == "lrc" and lrc_lines:
        segment_lyrics = [text for _, text in lrc_lines if text.strip()]
        if engine == "whisper":
            console.print(f"[{mode_label}] Using segment-specific lyrics for Whisper prompting", style="dim")
    elif segment_mode == "vad":
        # VAD mode: no lyrics (we don't know which lyrics correspond to which segment)
        segment_lyrics = [None] * len(segments)
        if engine == "whisper":
            console.print(f"[{mode_label}] VAD mode: no lyrics prompting (segment-lyrics mapping unknown)", style="dim")

    for i, segment in enumerate(segments):
        # Determine lyrics for this segment
        if segment_mode == "lrc" and i < len(segment_lyrics):
            # LRC mode: use segment-specific lyrics line
            seg_lyrics = segment_lyrics[i]
        elif segment_mode == "vad":
            # VAD mode: no lyrics guidance
            seg_lyrics = None
        else:
            # Fallback: no lyrics
            seg_lyrics = None

        console.print(
            f"[{mode_label}] Segment {i+1}/{len(segments)}: {segment.start_seconds:.2f}s - {segment.end_seconds:.2f}s",
            style="dim",
        )
        words = transcribe_segment(
            audio_path=audio_path,
            segment=segment,
            engine=engine,
            model_name=model_name,
            device=device,
            compute_type=compute_type,
            batch_size_s=batch_size_s,
            use_itn=use_itn,
            sensevoice_disable_vad=sensevoice_disable_vad,
            sensevoice_vad_max_silence=sensevoice_vad_max_silence,
            sensevoice_vad_threshold=sensevoice_vad_threshold,
            paraformer_disable_vad=paraformer_disable_vad,
            paraformer_vad_max_silence=paraformer_vad_max_silence,
            paraformer_vad_threshold=paraformer_vad_threshold,
            lyrics_text=seg_lyrics,
            debug=debug,
        )
        all_words.extend(words)

    console.print(f"[{mode_label}] Total transcribed: {len(all_words)} pinyin syllables", style="dim")
    return all_words


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


def align_sequences_per_line(
    lrc_words: list[PinyinWord],
    audio_words: list[PinyinWord],
    lrc_lines: list[tuple[float, str]],
    time_tolerance_s: float = 2.0,
) -> list[DiffEntry]:
    """Align LRC and audio word sequences per line.

    Instead of global alignment, this function aligns each LRC line to
    overlapping audio words, reducing cascading errors.

    Args:
        lrc_words: LRC pinyin words
        audio_words: Audio pinyin words
        lrc_lines: Original LRC lines as (timestamp, text) tuples
        time_tolerance_s: Time tolerance for finding overlapping audio words

    Returns:
        List of DiffEntry showing alignment
    """
    if not lrc_lines or not lrc_words:
        return align_sequences(lrc_words, audio_words)

    result: list[DiffEntry] = []

    # Build line boundaries
    line_boundaries: list[tuple[float, float, int, int]] = []  # (start, end, lrc_start_idx, lrc_end_idx)

    # First, map each LRC word to its line based on timestamp
    lrc_word_idx = 0
    for i, (line_ts, line_text) in enumerate(lrc_lines):
        # Determine line end (next line's start or some time after)
        if i + 1 < len(lrc_lines):
            line_end = lrc_lines[i + 1][0]
        else:
            line_end = line_ts + 10.0  # Last line: assume 10s duration

        # Find LRC words that belong to this line
        line_start_idx = lrc_word_idx
        while lrc_word_idx < len(lrc_words) and lrc_words[lrc_word_idx].time_seconds < line_end:
            lrc_word_idx += 1
        line_end_idx = lrc_word_idx

        line_boundaries.append((line_ts, line_end, line_start_idx, line_end_idx))

    # Track used audio words to handle orphans
    used_audio_indices: set[int] = set()

    # Process each line
    for line_ts, line_end, lrc_start_idx, lrc_end_idx in line_boundaries:
        # Get LRC words for this line
        line_lrc_words = lrc_words[lrc_start_idx:lrc_end_idx]
        if not line_lrc_words:
            continue

        # Find overlapping audio words (with tolerance)
        search_start = line_ts - time_tolerance_s
        search_end = line_end + time_tolerance_s

        line_audio_words = []
        line_audio_indices = []
        for j, audio_w in enumerate(audio_words):
            if search_start <= audio_w.time_seconds < search_end:
                line_audio_words.append(audio_w)
                line_audio_indices.append(j)

        # Align this line's words
        if not line_audio_words:
            # No audio words found - mark all LRC words as missing
            for lrc_w in line_lrc_words:
                result.append(
                    DiffEntry(
                        op="delete",
                        lrc_text=lrc_w.text,
                        lrc_pinyin=lrc_w.pinyin,
                        lrc_time=lrc_w.time_seconds,
                    )
                )
            continue

        # Run alignment on this subset
        lrc_pinyins = [w.pinyin for w in line_lrc_words]
        audio_pinyins = [w.pinyin for w in line_audio_words]

        matcher = SequenceMatcher(None, lrc_pinyins, audio_pinyins)
        opcodes = matcher.get_opcodes()

        for op, lrc_s, lrc_e, audio_s, audio_e in opcodes:
            if op == "equal":
                for ii, jj in zip(range(lrc_s, lrc_e), range(audio_s, audio_e)):
                    lrc_w = line_lrc_words[ii]
                    audio_w = line_audio_words[jj]
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
                    used_audio_indices.add(line_audio_indices[jj])
            elif op == "delete":
                for ii in range(lrc_s, lrc_e):
                    lrc_w = line_lrc_words[ii]
                    result.append(
                        DiffEntry(
                            op="delete",
                            lrc_text=lrc_w.text,
                            lrc_pinyin=lrc_w.pinyin,
                            lrc_time=lrc_w.time_seconds,
                        )
                    )
            elif op == "insert":
                for jj in range(audio_s, audio_e):
                    audio_w = line_audio_words[jj]
                    result.append(
                        DiffEntry(
                            op="insert",
                            audio_text=audio_w.text,
                            audio_pinyin=audio_w.pinyin,
                            audio_time=audio_w.time_seconds,
                        )
                    )
                    used_audio_indices.add(line_audio_indices[jj])
            elif op == "replace":
                for ii in range(lrc_s, lrc_e):
                    lrc_w = line_lrc_words[ii]
                    result.append(
                        DiffEntry(
                            op="delete",
                            lrc_text=lrc_w.text,
                            lrc_pinyin=lrc_w.pinyin,
                            lrc_time=lrc_w.time_seconds,
                        )
                    )
                for jj in range(audio_s, audio_e):
                    audio_w = line_audio_words[jj]
                    result.append(
                        DiffEntry(
                            op="insert",
                            audio_text=audio_w.text,
                            audio_pinyin=audio_w.pinyin,
                            audio_time=audio_w.time_seconds,
                        )
                    )
                    used_audio_indices.add(line_audio_indices[jj])

    # Handle orphan audio words (not matched to any line)
    for j, audio_w in enumerate(audio_words):
        if j not in used_audio_indices:
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
# Time Offset Normalization
# --------------------------------------------------------------------------


def calculate_time_offset(diff: list[DiffEntry]) -> float:
    """Calculate the global time offset between audio and LRC timestamps.

    Uses median of time differences from matched words to be robust against outliers.
    This offset is typically caused by VAD detecting speech start at different times
    than the LRC timestamps (e.g., isolated vocals vs original mix).

    Args:
        diff: List of diff entries from alignment

    Returns:
        Median time offset in seconds (audio_time - lrc_time)
    """
    matched = [d for d in diff if d.op == "equal" and d.time_diff is not None]

    if not matched:
        return 0.0

    # Use median for robustness against outliers
    time_diffs = sorted([d.time_diff for d in matched])
    n = len(time_diffs)
    if n % 2 == 0:
        median = (time_diffs[n // 2 - 1] + time_diffs[n // 2]) / 2
    else:
        median = time_diffs[n // 2]

    return median


def normalize_time_offset(diff: list[DiffEntry], offset: float) -> list[DiffEntry]:
    """Normalize diff entries by subtracting the global time offset.

    This removes the penalty for VAD-induced timing shifts, allowing
    evaluation of relative timing accuracy.

    Args:
        diff: List of diff entries
        offset: Time offset in seconds to subtract from time_diff

    Returns:
        New list of DiffEntry with normalized time_diff values
    """
    normalized = []
    for entry in diff:
        if entry.op == "equal" and entry.time_diff is not None:
            # Create new entry with normalized time_diff
            normalized.append(
                DiffEntry(
                    op=entry.op,
                    lrc_text=entry.lrc_text,
                    audio_text=entry.audio_text,
                    lrc_pinyin=entry.lrc_pinyin,
                    audio_pinyin=entry.audio_pinyin,
                    lrc_time=entry.lrc_time,
                    audio_time=entry.audio_time,
                    time_diff=entry.time_diff - offset,
                )
            )
        else:
            normalized.append(entry)

    return normalized


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


def calculate_pinyin_accuracy(
    lrc_words: list[PinyinWord], audio_words: list[PinyinWord]
) -> dict:
    """Calculate pinyin-level accuracy with homophone analysis.

    This compares pinyin representations and categorizes matches as:
    - exact: Same character and pinyin
    - homophone: Same pinyin but different character (Chinese homophone)
    - mismatch: Different pinyin

    Args:
        lrc_words: LRC pinyin words
        audio_words: Audio pinyin words

    Returns:
        Dictionary with accuracy statistics
    """
    # Build pinyin-to-words mapping for both lists
    lrc_pinyin_list = [w.pinyin for w in lrc_words]
    audio_pinyin_list = [w.pinyin for w in audio_words]

    # Use sequence matcher on pinyin
    matcher = SequenceMatcher(None, lrc_pinyin_list, audio_pinyin_list)
    opcodes = matcher.get_opcodes()

    exact_matches = 0
    homophone_matches = 0
    total_lrc = len(lrc_words)
    total_audio = len(audio_words)

    for op, lrc_start, lrc_end, audio_start, audio_end in opcodes:
        if op == "equal":
            # Pinyin matches - check if characters are the same
            for i, j in zip(range(lrc_start, lrc_end), range(audio_start, audio_end)):
                if lrc_words[i].text == audio_words[j].text:
                    exact_matches += 1
                else:
                    homophone_matches += 1

    # Calculate metrics
    total_matched_pinyin = exact_matches + homophone_matches

    metrics = {
        "exact_matches": exact_matches,
        "homophone_matches": homophone_matches,
        "total_pinyin_matches": total_matched_pinyin,
        "lrc_count": total_lrc,
        "audio_count": total_audio,
        "exact_accuracy": (exact_matches / total_lrc * 100) if total_lrc > 0 else 0,
        "pinyin_accuracy": (total_matched_pinyin / total_lrc * 100) if total_lrc > 0 else 0,
        "coverage": (total_matched_pinyin / total_audio * 100) if total_audio > 0 else 0,
    }

    return metrics


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

    # Show pinyin accuracy breakdown if available
    if stats.pinyin_accuracy > 0:
        lines.append("--- Pinyin Analysis ---")
        lines.append(f"Exact matches:   {stats.exact_matches:5}    |  Homophones: {stats.homophone_matches}")
        lines.append(f"Pinyin accuracy: {stats.pinyin_accuracy:5.1f}% (includes homophones)")
        lines.append("")

    lines.append("--- Timing ---")
    lines.append(f"Global offset:  {stats.time_offset_ms:+.0f} ms (auto-corrected)")
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
            "time_offset_ms": round(result.stats.time_offset_ms, 2),
            "rms_error_ms": round(result.stats.rms_error_ms, 2),
            "max_error_ms": round(result.stats.max_error_ms, 2),
            "exact_matches": result.stats.exact_matches,
            "homophone_matches": result.stats.homophone_matches,
            "pinyin_accuracy": round(result.stats.pinyin_accuracy, 2),
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
    audio_words: list[PinyinWord],
    song_title: Optional[str] = None,
    song_id: Optional[str] = None,
) -> str:
    """Format evaluation result as line-by-line side-by-side diff.

    Shows original lyrics lines with Chinese characters and pinyin,
    comparing LRC content vs audio transcription in a code-diff style.

    Args:
        result: Evaluation result
        lrc_lines: Original LRC lines as (timestamp, text) tuples
        audio_words: Raw transcribed words from ASR engine
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
    lines.append(f"Global offset:  {stats.time_offset_ms:+.0f} ms (auto-corrected)")
    lines.append(f"RMS error:      {stats.rms_error_ms:5.1f} ms  |  Max: {stats.max_error_ms:.1f} ms")
    lines.append("")

    lines.append("--- Scores ---")
    lines.append(f"Text accuracy:   {scores.text_accuracy:5.1f} / 100  (weight: {scores.text_weight})")
    lines.append(f"Timing accuracy: {scores.timing_accuracy:5.1f} / 100  (weight: {scores.timing_weight})")
    lines.append("─" * 35)
    lines.append(f"Final score:     {scores.final_score:5.1f} / 100")
    lines.append("")

    # Check for data availability early
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
        # Use LRC time for equal/delete, audio time (offset-adjusted) for insert
        if entry.lrc_time is not None:
            ref_time = entry.lrc_time
        elif entry.audio_time is not None:
            # Adjust audio time by offset to align with LRC timeline
            ref_time = entry.audio_time - (stats.time_offset_ms / 1000.0)
        else:
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

    # --- Side-by-side comparison: LRC vs Raw Transcription ---
    lines.append("--- LRC vs Raw Transcription (side-by-side) ---")
    lines.append("")

    # First, build raw transcription lines from audio_words
    raw_transcription_lines = []
    if audio_words:
        # Group consecutive characters into lines based on time gaps
        # A gap > 1 second suggests a new phrase/line
        current_line_chars = []
        current_line_start = None
        GAP_THRESHOLD = 1.0  # seconds

        for i, word in enumerate(audio_words):
            if current_line_start is None:
                current_line_start = word.time_seconds
                current_line_chars.append(word.text)
            else:
                # Check time gap from previous word
                prev_time = audio_words[i - 1].time_seconds
                gap = word.time_seconds - prev_time

                if gap > GAP_THRESHOLD:
                    # Save current line and start new one
                    line_text = "".join(current_line_chars)
                    raw_transcription_lines.append((current_line_start, line_text))
                    current_line_chars = [word.text]
                    current_line_start = word.time_seconds
                else:
                    current_line_chars.append(word.text)

        # Save final line
        if current_line_chars:
            line_text = "".join(current_line_chars)
            raw_transcription_lines.append((current_line_start, line_text))

    # Format side-by-side with fixed column width
    col_width = 35
    lines.append(f"{'LRC Lyrics':<{col_width}} | {'Raw Transcription (ASR)'}")
    lines.append(f"{'-' * col_width}-+-{'-' * col_width}")

    # Get LRC lines (filter empty)
    lrc_display = [(ts, text) for ts, text in lrc_lines if text.strip()]

    # Print side by side
    max_lines = max(len(lrc_display), len(raw_transcription_lines))
    for i in range(max_lines):
        # LRC column
        if i < len(lrc_display):
            ts, text = lrc_display[i]
            lrc_col = f"[{format_timestamp(ts)}] {text}"
        else:
            lrc_col = ""

        # Transcription column
        if i < len(raw_transcription_lines):
            ts, text = raw_transcription_lines[i]
            asr_col = f"[{format_timestamp(ts)}] {text}"
        else:
            asr_col = ""

        # Truncate if too long
        if len(lrc_col) > col_width:
            lrc_col = lrc_col[: col_width - 1] + "…"
        if len(asr_col) > col_width:
            asr_col = asr_col[: col_width - 1] + "…"

        lines.append(f"{lrc_col:<{col_width}} | {asr_col}")

    lines.append("")

    # Line-by-line diff section
    lines.append("--- Line-by-Line Diff ---")
    lines.append("")
    lines.append("Legend: = matched | - missing from audio | + extra in audio")
    lines.append("        [LRC time] → [Audio time] (diff)")
    lines.append("")

    # Format each line

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
    lrc_lines: Optional[list[tuple[float, str]]] = None,
    use_per_line_alignment: bool = True,
    pinyin_mode: bool = True,
) -> EvaluationResult:
    """Run full LRC evaluation.

    Args:
        lrc_words: Parsed LRC pinyin words
        audio_words: Transcribed audio pinyin words
        text_weight: Weight for text accuracy
        timing_weight: Weight for timing accuracy
        timing_threshold_ms: Threshold for timing score calculation
        lrc_lines: Original LRC lines for per-line alignment
        use_per_line_alignment: Use per-line alignment instead of global
        pinyin_mode: Use pinyin accuracy (includes homophones) instead of character accuracy

    Returns:
        EvaluationResult with stats, scores, and diff
    """
    # Align sequences - use per-line if enabled and lrc_lines available
    if use_per_line_alignment and lrc_lines:
        diff = align_sequences_per_line(lrc_words, audio_words, lrc_lines)
    else:
        diff = align_sequences(lrc_words, audio_words)

    # Calculate statistics
    matched_count = sum(1 for d in diff if d.op == "equal")
    missing_count = sum(1 for d in diff if d.op == "delete")
    extra_count = sum(1 for d in diff if d.op == "insert")

    # Calculate pinyin-level accuracy (includes homophones)
    pinyin_metrics = calculate_pinyin_accuracy(lrc_words, audio_words)

    # Calculate and apply time offset normalization
    # This removes penalty for VAD-induced global timing shifts
    time_offset = calculate_time_offset(diff)
    normalized_diff = normalize_time_offset(diff, time_offset)

    # Calculate scores using normalized diff for timing
    text_score = calculate_text_score(diff)  # Text score doesn't depend on timing

    # In pinyin mode, use pinyin accuracy (includes homophones) as the text score
    if pinyin_mode:
        text_score = pinyin_metrics["pinyin_accuracy"]

    timing_score, rms_error_ms, max_error_ms = calculate_timing_score(
        normalized_diff, timing_threshold_ms
    )
    final_score = calculate_final_score(text_score, timing_score, text_weight, timing_weight)

    stats = EvaluationStats(
        lrc_word_count=len(lrc_words),
        audio_word_count=len(audio_words),
        matched_count=matched_count,
        missing_count=missing_count,
        extra_count=extra_count,
        rms_error_ms=rms_error_ms,
        max_error_ms=max_error_ms,
        time_offset_ms=time_offset * 1000,  # Convert to ms
        exact_matches=pinyin_metrics["exact_matches"],
        homophone_matches=pinyin_metrics["homophone_matches"],
        pinyin_accuracy=pinyin_metrics["pinyin_accuracy"],
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
        diff_entries=normalized_diff,  # Return normalized diff for reporting
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
        help="Model name (default: large-v2 for whisper, SenseVoiceSmall for sensevoice)",
    ),
    device: str = typer.Option("cpu", "--device", "-d", help="Device to run on (cpu/cuda/mps)"),
    compute_type: str = typer.Option("int8", "--compute-type", "-c", help="Compute type (int8/float16, whisper only)"),
    batch_size_s: int = typer.Option(60, "--batch-size-s", help="Batch size in seconds for transcription"),
    # Segmentation options
    segment_mode: str = typer.Option("lrc", "--segment-mode", help="Segmentation mode: lrc, vad, or none"),
    merge_vad: bool = typer.Option(True, "--merge-vad/--no-merge-vad", help="Merge adjacent VAD segments (vad mode)"),
    split_on_silence: bool = typer.Option(False, "--split-on-silence/--no-split-on-silence", help="Split segments on silence (vad mode)"),
    # Alignment and scoring options
    per_line_align: bool = typer.Option(True, "--per-line-align/--no-per-line-align", help="Use per-line alignment"),
    pinyin_mode: bool = typer.Option(True, "--pinyin-mode/--no-pinyin-mode", help="Use pinyin accuracy (includes homophones)"),
    text_weight: float = typer.Option(0.6, "--text-weight", help="Text accuracy weight"),
    timing_weight: float = typer.Option(0.4, "--timing-weight", help="Timing accuracy weight"),
    timing_threshold: float = typer.Option(500.0, "--timing-threshold", help="RMS threshold for 0 timing score (ms)"),
    # SenseVoice-specific options
    use_itn: bool = typer.Option(False, "--use-itn/--no-use-itn", help="Use inverse text normalization (SenseVoice)"),
    sensevoice_disable_vad: bool = typer.Option(False, "--sensevoice-disable-vad", help="Disable internal VAD (SenseVoice)"),
    sensevoice_vad_max_silence: int = typer.Option(1000, "--sensevoice-vad-max-silence", help="VAD max silence in ms (SenseVoice)"),
    sensevoice_vad_threshold: float = typer.Option(0.5, "--sensevoice-vad-threshold", help="VAD threshold 0-1 (SenseVoice)"),
    # Paraformer-specific options
    paraformer_disable_vad: bool = typer.Option(False, "--paraformer-disable-vad", help="Disable internal VAD (Paraformer)"),
    paraformer_vad_max_silence: int = typer.Option(1000, "--paraformer-vad-max-silence", help="VAD max silence in ms (Paraformer)"),
    paraformer_vad_threshold: float = typer.Option(0.5, "--paraformer-vad-threshold", help="VAD threshold 0-1 (Paraformer)"),
    # Output options
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Output file (default: stdout)"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show word-by-word diff"),
    debug: bool = typer.Option(False, "--debug", help="Enable debug output"),
):
    """Evaluate LRC lyrics file accuracy against audio transcription.

    Supported transcription engines:
    - whisper: OpenAI Whisper via faster-whisper (default)
    - sensevoice: FunASR SenseVoice (Chinese-optimized)
    - paraformer: FunASR Paraformer (fast, Chinese-optimized)

    Segmentation modes:
    - lrc: Use LRC line timestamps to split audio (default, enables segment-specific lyrics prompting for Whisper)
    - vad: Use Voice Activity Detection to split audio
    - none: Transcribe full audio without segmentation (uses full lyrics for Whisper prompting)

    Usage examples:

    1. Song ID mode with default (whisper):
       uv run --extra lrc_eval poc/eval_lrc.py wo_yao_quan_xin_zan_mei_244

    2. Compare engines:
       uv run --extra lrc_eval poc/eval_lrc.py <song_id> --engine whisper
       uv run --extra lrc_eval poc/eval_lrc.py <song_id> --engine sensevoice
       uv run --extra lrc_eval poc/eval_lrc.py <song_id> --engine paraformer

    3. VAD-based segmentation (instead of default LRC):
       uv run --extra lrc_eval poc/eval_lrc.py <song_id> -e paraformer --segment-mode vad

    4. Disable pinyin mode (character-exact matching):
       uv run --extra lrc_eval poc/eval_lrc.py <song_id> --no-pinyin-mode

    5. Local file mode:
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
    # Extract raw lyrics text (without timestamps) for whisper prompting
    lyrics_text = "\n".join(text for _, text in lrc_lines if text.strip())
    console.print(f"Parsed {len(lrc_words)} pinyin syllables from LRC ({len(lrc_lines)} lines)", style="dim")

    # Validate engine
    if engine not in ENGINES:
        console.print(f"Error: Unknown engine '{engine}'. Supported: {ENGINES}", style="red")
        raise typer.Exit(1)

    # Validate segment mode
    if segment_mode not in ["vad", "lrc", "none"]:
        console.print(f"Error: Unknown segment mode '{segment_mode}'. Supported: vad, lrc, none", style="red")
        raise typer.Exit(1)

    console.print(f"Engine: {engine}", style="dim")
    console.print(f"Segment mode: {segment_mode}, merge: {merge_vad}, split_on_silence: {split_on_silence}", style="dim")
    console.print(f"Per-line alignment: {per_line_align}, pinyin_mode: {pinyin_mode}, batch_size_s: {batch_size_s}", style="dim")

    # Transcribe audio (with optional segmentation pre-processing)
    audio_words = transcribe_with_segmentation(
        audio_path=audio_path,
        engine=engine,
        model_name=model,
        device=device,
        compute_type=compute_type,
        batch_size_s=batch_size_s,
        segment_mode=segment_mode,
        lrc_lines=lrc_lines if segment_mode == "lrc" else None,
        merge_segments=merge_vad,
        split_on_silence=split_on_silence,
        use_itn=use_itn,
        sensevoice_disable_vad=sensevoice_disable_vad,
        sensevoice_vad_max_silence=sensevoice_vad_max_silence,
        sensevoice_vad_threshold=sensevoice_vad_threshold,
        paraformer_disable_vad=paraformer_disable_vad,
        paraformer_vad_max_silence=paraformer_vad_max_silence,
        paraformer_vad_threshold=paraformer_vad_threshold,
        lyrics_text=lyrics_text if engine == "whisper" else None,
        debug=debug,
    )

    # Run evaluation
    console.print("Running evaluation...", style="dim")
    if pinyin_mode:
        console.print("Pinyin mode: Scoring based on pronunciation accuracy (includes homophones)", style="dim")
    result = evaluate_lrc(
        lrc_words=lrc_words,
        audio_words=audio_words,
        text_weight=text_weight,
        timing_weight=timing_weight,
        timing_threshold_ms=timing_threshold,
        lrc_lines=lrc_lines,
        use_per_line_alignment=per_line_align,
        pinyin_mode=pinyin_mode,
    )

    # Format output
    if json_output:
        report = format_json_report(result, song_title=song_title, song_id=song_id)
    elif verbose:
        # Use line-by-line diff format for verbose mode
        report = format_line_diff_report(
            result, lrc_lines, audio_words, song_title=song_title, song_id=song_id
        )
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
