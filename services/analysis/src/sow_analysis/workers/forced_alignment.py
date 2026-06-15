"""Forced alignment utility functions.

Migrated from services/qwen3/src/sow_qwen3/routes/align.py.
Provides text normalization, timestamp formatting, segment-to-line mapping,
and audio duration validation.
"""

import re
from pathlib import Path


def normalize_text(text: str) -> str:
    """Normalize text by removing whitespace and common CJK punctuation."""
    return re.sub(r"[\s。，！？、；：\"''""''""''（）【】「」『』 ]+", "", text)


def format_timestamp(seconds: float) -> str:
    """Format seconds as [mm:ss.xx] timestamp."""
    minutes = int(seconds // 60)
    secs = seconds % 60
    return f"[{minutes:02d}:{secs:05.2f}]"


def map_segments_to_lines(
    segments: list[tuple[float, float, str]],
    original_lines: list[str],
) -> list[tuple[float, float, str]]:
    """Map character-level alignment segments to original lyric lines.

    The Qwen3ForcedAligner returns character/word-level timestamps. This function
    maps those fine-grained segments back to the original lyric lines by tracking
    text position and computing min/max timestamps for each line.
    """
    aligned_text = ""
    segment_positions = []

    for seg_start, seg_end, seg_text in segments:
        start_char = len(aligned_text)
        aligned_text += seg_text
        end_char = len(aligned_text)
        segment_positions.append((start_char, end_char, seg_start, seg_end, seg_text))

    aligned_normalized = normalize_text(aligned_text)

    line_alignments = []
    current_pos = 0

    for line in original_lines:
        normalized_line = normalize_text(line)
        if not normalized_line:
            prev_end = line_alignments[-1][1] if line_alignments else 0.0
            line_alignments.append((prev_end, prev_end, line))
            continue

        line_start = aligned_normalized.find(normalized_line, current_pos)

        if line_start == -1:
            if current_pos >= len(aligned_normalized):
                prev_end = line_alignments[-1][1] if line_alignments else 0.0
                line_alignments.append((prev_end, prev_end, line))
            else:
                ratio = current_pos / len(aligned_normalized)
                est_start = segments[0][0] if segments else 0.0
                est_end = segments[-1][1] if segments else 0.0
                duration = est_end - est_start
                line_alignments.append(
                    (est_start + ratio * duration, est_start + ratio * duration, line)
                )
            continue

        line_end = line_start + len(normalized_line)
        current_pos = line_end

        overlapping_segments = []
        for (
            seg_start_char,
            seg_end_char,
            seg_start_time,
            seg_end_time,
            _seg_text,
        ) in segment_positions:
            if seg_end_char > line_start and seg_start_char < line_end:
                overlapping_segments.append((seg_start_time, seg_end_time))

        if overlapping_segments:
            start_time = min(s[0] for s in overlapping_segments)
            end_time = max(s[1] for s in overlapping_segments)
            line_alignments.append((start_time, end_time, line))
        else:
            ratio = line_start / len(aligned_normalized) if aligned_normalized else 0
            est_start = segments[0][0] if segments else 0.0
            est_end = segments[-1][1] if segments else 0.0
            duration = est_end - est_start
            line_alignments.append(
                (
                    est_start + ratio * duration,
                    est_start + ratio * duration + (duration / len(original_lines)),
                    line,
                )
            )

    return line_alignments


def validate_audio_duration(audio_path: Path, max_seconds: float = 300.0) -> float:
    """Validate audio duration using soundfile (O(1) for WAV/FLAC) with librosa fallback."""
    try:
        import soundfile

        info = soundfile.info(str(audio_path))
        duration = info.duration
    except Exception:
        import librosa

        duration = librosa.get_duration(filename=str(audio_path))
    if duration > max_seconds:
        raise ValueError(
            f"Audio duration ({duration:.1f}s) exceeds {max_seconds / 60:.0f} minute limit"
        )
    return duration
