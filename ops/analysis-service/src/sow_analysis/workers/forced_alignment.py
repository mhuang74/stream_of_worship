"""Forced alignment utility functions.

Migrated from services/qwen3/src/sow_qwen3/routes/align.py.
Provides text normalization, timestamp formatting, segment-to-line mapping,
audio duration validation, and long-recording chunking.

Chunking exists because Qwen3ForcedAligner-0.6B cannot align audio longer than
~5 minutes: recordings above ``SOW_FORCED_ALIGN_MAX_CHUNK_SECONDS`` are cut at
quiet points, aligned chunk-by-chunk, and the per-chunk results concatenated
after shifting each timestamp by its chunk offset.

This module is stdlib-only; ``soundfile``/``librosa``/``numpy`` are imported
lazily inside the function bodies that need them (``queue.py`` guards the import
with a try/except ImportError).
"""

import logging
import math
import re
from bisect import bisect_right
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


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
    aligned_normalized = ""
    segment_positions = []

    for seg_start, seg_end, seg_text in segments:
        normalized_seg = normalize_text(seg_text)
        start_char = len(aligned_normalized)
        aligned_normalized += normalized_seg
        end_char = len(aligned_normalized)
        segment_positions.append((start_char, end_char, seg_start, seg_end, seg_text))

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

        duration = librosa.get_duration(path=str(audio_path))
    if duration > max_seconds:
        raise ValueError(
            f"Audio duration ({duration:.1f}s) exceeds {max_seconds:.0f}s "
            f"(chunking limit; recordings up to {max_seconds / 60:.0f} minutes are supported)"
        )
    return duration


def pick_quiet_cuts(
    times: list[float],
    rms: list[float],
    ideal_cuts: list[float],
    window: float,
    min_gap: float = 60.0,
) -> list[float]:
    """Snap each ideal cut to the quietest RMS frame within ±``window``.

    Args:
        times: Frame centre times in seconds, monotonically increasing.
        rms: Per-frame RMS energy, same length as ``times``.
        ideal_cuts: Desired cut times (evenly spaced chunk boundaries).
        window: Search radius in seconds around each ideal cut.
        min_gap: Minimum distance from the previous cut and from either audio
            edge, so cuts cannot cluster at a boundary.

    Returns:
        One cut time per entry in ``ideal_cuts``. A cut falls back to its ideal
        time when no frame inside the window qualifies (including degenerate
        input with no frames), so the result is always well-defined.
    """
    if not ideal_cuts:
        return []
    if not times or not rms or len(times) != len(rms):
        return [max(0.0, float(cut)) for cut in ideal_cuts]

    audio_start = times[0]
    audio_end = times[-1]
    cuts: list[float] = []
    prev_cut = audio_start

    for ideal in ideal_cuts:
        low = max(ideal - window, audio_start + min_gap, prev_cut + min_gap)
        high = min(ideal + window, audio_end - min_gap)

        best_time: Optional[float] = None
        best_rms = math.inf
        if high >= low:
            for frame_time, frame_rms in zip(times, rms):
                if frame_time < low:
                    continue
                if frame_time > high:
                    break
                if frame_rms < best_rms:
                    best_rms = frame_rms
                    best_time = frame_time

        if best_time is None:
            best_time = max(ideal, prev_cut + min_gap)
        cuts.append(best_time)
        prev_cut = best_time

    return cuts


def find_chunk_boundaries(
    audio_path: Path,
    duration: float,
    chunk_seconds: float,
    cut_window: float,
) -> list[float]:
    """Return ``[0.0, c1, ..., c_{n-1}, duration]`` of silence-aware cuts.

    ``n_chunks`` is derived from ``chunk_seconds - cut_window`` so a ±window
    snap still leaves every chunk under ``chunk_seconds``; ideal cuts are evenly
    spaced and then snapped to the quietest nearby frame. Any librosa failure
    logs a warning and returns the evenly spaced boundaries — a job must not fail
    just because the silence scan could not decode the file.

    Args:
        audio_path: Source recording (WAV/FLAC/MP3).
        duration: Recording duration in seconds.
        chunk_seconds: Maximum chunk length (the model limit).
        cut_window: ± search radius around each ideal cut.

    Returns:
        Strictly increasing boundaries starting at 0.0 and ending at
        ``duration``; a single chunk returns ``[0.0, duration]``.
    """
    if duration <= 0:
        return [0.0, 0.0]
    # Snap slack must come out of the chunk budget: with n = ceil(duration/chunk)
    # a ±window snap could push a chunk past the model limit (e.g. 890 s -> 3
    # chunks snapped to 326 s). Shrinking the target leaves >= cut_window of
    # headroom per cut.
    target_seconds = max(chunk_seconds - cut_window, 1.0)
    n_chunks = max(1, math.ceil(duration / target_seconds))
    if n_chunks == 1:
        return [0.0, duration]

    ideal_cuts = [duration * index / n_chunks for index in range(1, n_chunks)]

    # Low sample rate: only RMS is needed, so decode as cheaply as possible.
    scan_sr = 8000
    try:
        import librosa
        import numpy as np

        samples, sr = librosa.load(str(audio_path), sr=scan_sr, mono=True)
        rms_frames = librosa.feature.rms(y=samples)[0]
        frame_times = librosa.frames_to_time(np.arange(len(rms_frames)), sr=sr, hop_length=512)
        cuts = pick_quiet_cuts(
            [float(value) for value in frame_times],
            [float(value) for value in rms_frames],
            ideal_cuts,
            cut_window,
            min_gap=min(60.0, chunk_seconds / 2.0),
        )
    except Exception as exc:  # noqa: BLE001 - the fallback must never raise
        logger.warning(
            "Silence scan failed for %s (%s); using evenly spaced chunk boundaries",
            audio_path,
            exc,
        )
        cuts = ideal_cuts

    boundaries = [0.0]
    for index, cut in enumerate(cuts, start=1):
        # Defense in depth: even a bad snap must leave <= chunk_seconds per chunk
        # and enough room for the chunks that follow.
        lower = max(boundaries[-1], duration - (n_chunks - index) * chunk_seconds)
        upper = min(boundaries[-1] + chunk_seconds, duration)
        clamped = min(max(float(cut), lower), upper)
        if boundaries[-1] < clamped < duration:
            boundaries.append(clamped)
    boundaries.append(duration)
    return boundaries


def extract_chunk(audio_path: Path, start: float, end: float, out_path: Path) -> Path:
    """Write ``[start, end)`` of ``audio_path`` to ``out_path`` as 16 kHz mono PCM-16 WAV.

    16 kHz mono is exactly what the Modal client transcodes to, so a chunk needs
    no further conversion. Decoding from the source (rather than byte-slicing)
    keeps VBR MP3 handling correct.

    Args:
        audio_path: Source recording.
        start: Chunk start in seconds.
        end: Chunk end in seconds.
        out_path: Destination WAV path.

    Returns:
        ``out_path``.
    """
    import librosa
    import soundfile

    samples, sr = librosa.load(
        str(audio_path),
        sr=16_000,
        mono=True,
        offset=start,
        duration=max(0.0, end - start),
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    soundfile.write(str(out_path), samples, sr, subtype="PCM_16")
    return out_path


def assign_lines_to_chunks(
    lyrics_lines: list[str],
    boundaries: list[float],
    duration: float,
    line_times: Optional[list[float]] = None,
) -> list[list[str]]:
    """Partition ``lyrics_lines`` into per-chunk contiguous slices.

    Each line is placed by its centroid time — ``line_times`` when the caller has
    real per-line timestamps (a parseable LRC), otherwise a char-proportional
    estimate over ``duration``. Empty lines carry no timing, so they attach to
    the following timed line (or the previous one when trailing).

    Chunk indexes are forced non-decreasing so every slice is contiguous: the
    per-chunk ``map_segments_to_lines`` outputs concatenate back to the full
    original line list in order, and every non-empty line lands in exactly one
    chunk.

    Args:
        lyrics_lines: Full lyric lines, in order.
        boundaries: Strictly increasing boundaries from ``find_chunk_boundaries``.
        duration: Recording duration in seconds (proportional fallback only).
        line_times: Optional per-line timestamps matching ``lyrics_lines``.

    Returns:
        ``len(boundaries) - 1`` lists of lines; some may be empty.
    """
    n_chunks = max(1, len(boundaries) - 1)
    if not lyrics_lines:
        return [[] for _ in range(n_chunks)]

    if line_times is not None and len(line_times) == len(lyrics_lines):
        centroids = [float(value) for value in line_times]
    else:
        lengths = [len(normalize_text(line)) for line in lyrics_lines]
        total = sum(lengths)
        centroids = []
        consumed = 0
        for length in lengths:
            if total:
                centroids.append((consumed + length / 2.0) / total * duration)
            else:
                centroids.append(0.0)
            consumed += length

    def _chunk_for(centroid: float) -> int:
        index = bisect_right(boundaries, centroid) - 1
        return min(max(index, 0), n_chunks - 1)

    raw = [_chunk_for(centroids[position]) for position in range(len(lyrics_lines))]

    def _neighbour_chunk(position: int) -> Optional[int]:
        for step in range(position + 1, len(lyrics_lines)):
            if normalize_text(lyrics_lines[step]):
                return raw[step]
        for step in range(position - 1, -1, -1):
            if normalize_text(lyrics_lines[step]):
                return raw[step]
        return None

    resolved: list[int] = []
    for position, line in enumerate(lyrics_lines):
        if normalize_text(line):
            resolved.append(raw[position])
            continue
        neighbour = _neighbour_chunk(position)
        resolved.append(0 if neighbour is None else neighbour)

    # Contiguity: a slice is a run of consecutive lines, so indexes must not
    # decrease. Monotonic clamping keeps the concatenation invariant.
    slices: list[list[str]] = [[] for _ in range(n_chunks)]
    highest = 0
    for line, chunk_index in zip(lyrics_lines, resolved):
        clamped = min(max(highest, chunk_index, 0), n_chunks - 1)
        highest = clamped
        slices[clamped].append(line)
    return slices
