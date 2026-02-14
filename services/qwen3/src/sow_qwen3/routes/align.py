"""Alignment endpoint for lyrics to audio timestamps."""

import logging
import re
from typing import TYPE_CHECKING

from fastapi import APIRouter, Header, HTTPException

from ..config import settings
from ..models import AlignRequest, AlignResponse, LyricLine, OutputFormat
from ..storage.audio import download_audio, validate_audio_duration
from .health import get_aligner  # Share aligner getter with health route

logger = logging.getLogger(__name__)
router = APIRouter()

if TYPE_CHECKING:
    from ..workers.aligner import Qwen3AlignerWrapper


def normalize_text(text: str) -> str:
    """Normalize text by removing whitespace and common punctuation.

    Args:
        text: Text to normalize

    Returns:
        Normalized text
    """
    return re.sub(r"[\s。，！？、；：\"''""''""''（）【】「」『』 ]+", "", text)


def format_timestamp(seconds: float) -> str:
    """Format seconds as [mm:ss.xx] timestamp.

    Args:
        seconds: Time in seconds

    Returns:
        Formatted timestamp string
    """
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

    Args:
        segments: List of (start_time, end_time, text) from aligner
        original_lines: Original lyric lines (preserving structure)

    Returns:
        List of (start_time, end_time, text) with one entry per original line
    """
    # Build the full aligned text and track character positions
    aligned_text = ""
    segment_positions = []  # (start_char, end_char, start_time, end_time)

    for seg_start, seg_end, seg_text in segments:
        start_char = len(aligned_text)
        aligned_text += seg_text
        end_char = len(aligned_text)
        segment_positions.append((start_char, end_char, seg_start, seg_end, seg_text))

    # Build normalized aligned text
    aligned_normalized = normalize_text(aligned_text)

    # Map each original line to its time range
    line_alignments = []
    current_pos = 0

    for line in original_lines:
        normalized_line = normalize_text(line)
        if not normalized_line:
            # Empty line - use previous end time or 0
            prev_end = line_alignments[-1][1] if line_alignments else 0.0
            line_alignments.append((prev_end, prev_end, line))
            continue

        # Find this line in the normalized aligned text
        line_start = aligned_normalized.find(normalized_line, current_pos)

        if line_start == -1:
            # Line not found - might be due to alignment differences
            # Use interpolation based on position in original text
            if current_pos >= len(aligned_normalized):
                prev_end = line_alignments[-1][1] if line_alignments else 0.0
                line_alignments.append((prev_end, prev_end, line))
            else:
                # Estimate position proportionally
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

        # Find all segments that overlap with this line
        overlapping_segments = []
        for (
            seg_start_char,
            seg_end_char,
            seg_start_time,
            seg_end_time,
            _seg_text,
        ) in segment_positions:
            # Check overlap
            if seg_end_char > line_start and seg_start_char < line_end:
                overlapping_segments.append((seg_start_time, seg_end_time))

        if overlapping_segments:
            # Use earliest start and latest end for this line
            start_time = min(s[0] for s in overlapping_segments)
            end_time = max(s[1] for s in overlapping_segments)
            line_alignments.append((start_time, end_time, line))
        else:
            # No segments overlap - use interpolated time
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


@router.post("/api/v1/align", response_model=AlignResponse)
async def align_lyrics(
    request: AlignRequest,
    authorization: str | None = Header(None),
) -> AlignResponse:
    """Align lyrics to audio timestamps.

    Args:
        request: Alignment request with audio URL and lyrics text
        authorization: Bearer token for API authentication (optional if API_KEY not set)

    Returns:
        Alignment response with LRC/JSON formatted results

    Raises:
        HTTPException: 400 for invalid requests, 401 for auth failure, 500 for errors
    """
    # Verify API key if configured
    if settings.API_KEY:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(
                status_code=401, detail="Missing or invalid Authorization header"
            )
        token = authorization[7:]
        if token != settings.API_KEY:
            raise HTTPException(status_code=401, detail="Invalid API key")

    # Get aligner
    aligner: Qwen3AlignerWrapper | None = get_aligner()
    if aligner is None or not aligner.is_ready:
        raise HTTPException(status_code=503, detail="Model not loaded")

    # Split lyrics into lines
    lyrics_lines = [line.rstrip() for line in request.lyrics_text.splitlines()]
    # Remove trailing empty lines
    while lyrics_lines and not lyrics_lines[-1]:
        lyrics_lines.pop()

    if not lyrics_lines:
        raise HTTPException(
            status_code=400, detail="Lyrics are required for forced alignment"
        )

    # Download audio
    try:
        audio_path = download_audio(request.audio_url, settings.CACHE_DIR)
    except Exception as e:
        logger.error(f"Failed to download audio: {e}")
        raise HTTPException(status_code=400, detail=f"Failed to download audio: {str(e)}")

    # Validate duration (5 minute limit)
    try:
        duration_seconds = validate_audio_duration(audio_path)
    except ValueError as e:
        logger.error(f"Audio validation failed: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to validate audio: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to validate audio: {str(e)}")

    # Run alignment
    try:
        raw_segments = await aligner.align(
            audio_path=audio_path,
            lyrics_text=request.lyrics_text,
            language=request.language,
        )
    except Exception as e:
        logger.error(f"Alignment failed: {e}")
        raise HTTPException(status_code=500, detail=f"Alignment failed: {str(e)}")

    # Map segments to lines
    line_alignments = map_segments_to_lines(raw_segments, lyrics_lines)

    # Build response based on format
    lrc_content: str | None = None
    json_data: list[LyricLine] | None = None

    if request.format == OutputFormat.LRC:
        lrc_lines = []
        for start, _end, text in line_alignments:
            timestamp = format_timestamp(start)
            lrc_lines.append(f"{timestamp} {text}")
        lrc_content = "\n".join(lrc_lines)

    if request.format == OutputFormat.JSON:
        json_data = [
            LyricLine(start_time=start, end_time=end, text=text)
            for start, end, text in line_alignments
        ]

    return AlignResponse(
        lrc_content=lrc_content,
        json_data=json_data,
        line_count=len(line_alignments),
        duration_seconds=duration_seconds,
    )
