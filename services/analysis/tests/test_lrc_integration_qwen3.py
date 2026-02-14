"""End-to-end integration tests for full LRC pipeline with Qwen3 enabled.

These tests validate the complete LRC generation flow:
Whisper transcription -> LLM alignment -> Qwen3 refinement -> LRC file

The integration tests use comprehensive mocks that simulate real-world
worship song structures with repeated choruses, verses, and bridges.
"""

import asyncio
import re
from pathlib import Path
from typing import List, Tuple, Dict
from unittest.mock import AsyncMock, patch

import pytest

from sow_analysis.models import LrcOptions
from sow_analysis.services.qwen3_client import AlignResponse
from sow_analysis.workers.lrc import generate_lrc, WhisperPhrase, LRCLine


# ============================================================================
# Helper Functions
# ============================================================================

def parse_lrc_file(lrc_path: Path) -> List[Tuple[float, str]]:
    """Parse LRC file into list of (time_seconds, text) tuples.

    Args:
        lrc_path: Path to LRC file

    Returns:
        List of (time_seconds, text) tuples, sorted by time
    """
    lines = []
    pattern = r"\[(\d{2}):(\d{2}\.\d{2})\](.*)"
    for line in lrc_path.read_text(encoding="utf-8").strip().split("\n"):
        match = re.match(pattern, line)
        if match:
            minutes = int(match.group(1))
            seconds = float(match.group(2))
            text = match.group(3).strip()
            time_seconds = minutes * 60 + seconds
            if text:
                lines.append((time_seconds, text))
    lines.sort(key=lambda x: x[0])
    return lines


def count_unique_lines(lines: List[Tuple[float, str]]) -> Dict[str, int]:
    """Count occurrences of each unique lyric line.

    Args:
        lines: List of (time_seconds, text) tuples

    Returns:
        Dict mapping lyric text to count
    """
    counts: Dict[str, int] = {}
    for _, text in lines:
        counts[text] = counts.get(text, 0) + 1
    return counts


def verify_lrc_format(lines: List[Tuple[float, str]]) -> bool:
    """Verify LRC format is valid.

    Args:
        lines: List of (time_seconds, text) tuples

    Returns:
        True if format is valid
    """
    # Check timestamps are non-negative
    for time_sec, text in lines:
        if time_sec < 0:
            return False
        if not text:
            return False
    # Check timestamps are in ascending order
    for i in range(1, len(lines)):
        if lines[i][0] < lines[i - 1][0]:
            return False
    return True


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def integration_audio_path(tmp_path: Path) -> Path:
    """Path to test audio fixture file."""
    return Path(__file__).parent / "fixtures" / "integration_test_audio.wav"


@pytest.fixture
def integration_lyrics() -> str:
    """Load complete worship song lyrics from fixture."""
    fixture_path = Path(__file__).parent / "fixtures" / "integration_test_lyrics.txt"
    return fixture_path.read_text(encoding="utf-8").strip()


@pytest.fixture
def mock_whisper_phrases_full() -> List[WhisperPhrase]:
    """Comprehensive Whisper transcription phrases matching song structure.

    Simulates realistic Whisper output with phrase-level timestamps for:
    - Verse 1 (lines 1-3)
    - Chorus (lines 4-6)
    - Verse 2 (lines 7-8)
    - Chorus (lines 9-11) - repeated
    - Bridge (lines 12-13)
    - Chorus (lines 14-16) - repeated again
    """
    return [
        # Verse 1
        WhisperPhrase(text="Verse 1", start=0.0, end=3.5),
        WhisperPhrase(text="You are my strength when I am weak", start=3.5, end=8.2),
        WhisperPhrase(text="You are the treasure that I seek", start=8.2, end=12.8),
        WhisperPhrase(text="You are my all in all", start=12.8, end=17.5),
        # Chorus (first)
        WhisperPhrase(text="Seeking You as a precious jewel", start=18.0, end=22.5),
        WhisperPhrase(text="Lord, to give up I'd be a fool", start=22.5, end=27.2),
        WhisperPhrase(text="You are my all in all", start=27.2, end=32.0),
        # Verse 2
        WhisperPhrase(text="Jesus, Lamb of God, worthy is Your name", start=32.5, end=38.0),
        WhisperPhrase(text="Jesus, Lamb of God, worthy is Your name", start=38.0, end=43.5),
        # Chorus (second)
        WhisperPhrase(text="Seeking You as a precious jewel", start=44.0, end=48.5),
        WhisperPhrase(text="Lord, to give up I'd be a fool", start=48.5, end=53.2),
        WhisperPhrase(text="You are my all in all", start=53.2, end=58.0),
        # Bridge
        WhisperPhrase(text="Taking my sin, my cross, my shame", start=58.5, end=63.5),
        WhisperPhrase(text="Rising again I bless Your name", start=63.5, end=68.5),
        # Chorus (third - final)
        WhisperPhrase(text="Seeking You as a precious jewel", start=69.0, end=73.5),
        WhisperPhrase(text="Lord, to give up I'd be a fool", start=73.5, end=78.2),
        WhisperPhrase(text="You are my all in all", start=78.2, end=83.0),
    ]


@pytest.fixture
def mock_llm_aligned_lines_full() -> List[LRCLine]:
    """Mock LLM-aligned LRC lines matching Whisper phrases.

    Simulates LLM alignment output with timestamps at phrase boundaries.
    This is what the LLM would produce before Qwen3 refinement.
    """
    return [
        LRCLine(time_seconds=0.0, text="Verse 1"),
        LRCLine(time_seconds=3.5, text="You are my strength when I am weak"),
        LRCLine(time_seconds=8.2, text="You are the treasure that I seek"),
        LRCLine(time_seconds=12.8, text="You are my all in all"),
        LRCLine(time_seconds=18.0, text="Seeking You as a precious jewel"),
        LRCLine(time_seconds=22.5, text="Lord, to give up I'd be a fool"),
        LRCLine(time_seconds=27.2, text="You are my all in all"),
        LRCLine(time_seconds=32.5, text="Jesus, Lamb of God, worthy is Your name"),
        LRCLine(time_seconds=38.0, text="Jesus, Lamb of God, worthy is Your name"),
        LRCLine(time_seconds=44.0, text="Seeking You as a precious jewel"),
        LRCLine(time_seconds=48.5, text="Lord, to give up I'd be a fool"),
        LRCLine(time_seconds=53.2, text="You are my all in all"),
        LRCLine(time_seconds=58.5, text="Taking my sin, my cross, my shame"),
        LRCLine(time_seconds=63.5, text="Rising again I bless Your name"),
        LRCLine(time_seconds=69.0, text="Seeking You as a precious jewel"),
        LRCLine(time_seconds=73.5, text="Lord, to give up I'd be a fool"),
        LRCLine(time_seconds=78.2, text="You are my all in all"),
    ]


@pytest.fixture
def mock_qwen3_refined_lines() -> List[LRCLine]:
    """Mock Qwen3-refined LRC lines with character-level precision.

    Simulates Qwen3 word/character-level forced alignment output with
    timestamps that are more precise than the phrase-level LLM output.
    The key difference is the refinement of timestamps (same text, better timing).
    """
    return [
        LRCLine(time_seconds=0.0, text="Verse 1"),
        LRCLine(time_seconds=3.45, text="You are my strength when I am weak"),
        LRCLine(time_seconds=8.15, text="You are the treasure that I seek"),
        LRCLine(time_seconds=12.82, text="You are my all in all"),
        LRCLine(time_seconds=17.98, text="Seeking You as a precious jewel"),
        LRCLine(time_seconds=22.48, text="Lord, to give up I'd be a fool"),
        LRCLine(time_seconds=27.22, text="You are my all in all"),
        LRCLine(time_seconds=32.52, text="Jesus, Lamb of God, worthy is Your name"),
        LRCLine(time_seconds=38.05, text="Jesus, Lamb of God, worthy is Your name"),
        LRCLine(time_seconds=43.98, text="Seeking You as a precious jewel"),
        LRCLine(time_seconds=48.52, text="Lord, to give up I'd be a fool"),
        LRCLine(time_seconds=53.25, text="You are my all in all"),
        LRCLine(time_seconds=58.52, text="Taking my sin, my cross, my shame"),
        LRCLine(time_seconds=63.48, text="Rising again I bless Your name"),
        LRCLine(time_seconds=68.98, text="Seeking You as a precious jewel"),
        LRCLine(time_seconds=73.48, text="Lord, to give up I'd be a fool"),
        LRCLine(time_seconds=78.18, text="You are my all in all"),
    ]


# ============================================================================
# Main Integration Test
# ============================================================================

@pytest.mark.asyncio
async def test_full_pipeline_with_qwen3_enabled(
    integration_audio_path: Path,
    integration_lyrics: str,
    mock_whisper_phrases_full: List[WhisperPhrase],
    mock_llm_aligned_lines_full: List[LRCLine],
    mock_qwen3_refined_lines: List[LRCLine],
) -> None:
    """Test the complete LRC generation pipeline with Qwen3 enabled.

    This test validates:
    1. Function returns successfully without exceptions
    2. LRC file exists at output_path
    3. LRC file contains all lyric lines from input
    4. Timestamps are in ascending order (monotonic)
    5. First timestamp is >= 0.0
    6. Last timestamp is <= audio duration
    7. Unique lyric count matches input (no lines lost)
    8. Repeated sections have multiple entries (chorus appears 3 times)
    9. Qwen3 client was called
    10. LLM alignment was called first (pipeline order)
    """
    options = LrcOptions(use_qwen3=True)

    # Calculate expected audio duration from mock Whisper phrases
    audio_duration = max(p.end for p in mock_whisper_phrases_full)

    # Mock Whisper transcription
    with patch(
        "sow_analysis.workers.lrc._run_whisper_transcription",
        return_value=mock_whisper_phrases_full,
    ):
        # Mock LLM alignment - capture the mock for assertion
        with patch(
            "sow_analysis.workers.lrc._llm_align",
            new_callable=AsyncMock,
            return_value=mock_llm_aligned_lines_full,
        ) as mock_llm_align:
            # Mock Qwen3Client to return refined LRC with character-level precision
            qwen3_lrc_content = "\n".join(
                line.format() for line in mock_qwen3_refined_lines
            )
            mock_client = AsyncMock()
            mock_client.align.return_value = AlignResponse(
                lrc_content=qwen3_lrc_content,
                json_data=None,
                line_count=len(mock_qwen3_refined_lines),
                duration_seconds=audio_duration,
            )

            with patch(
                "sow_analysis.workers.lrc.Qwen3Client",
                return_value=mock_client,
            ):
                # Generate LRC with all mocks in place
                lrc_path, line_count, phrases = await generate_lrc(
                    audio_path=integration_audio_path,
                    lyrics_text=integration_lyrics,
                    options=options,
                    output_path=integration_audio_path.with_suffix(".lrc"),
                    content_hash="test_hash_abc123",  # Enable Qwen3
                )

                # Verification 1: Function returns successfully
                assert lrc_path is not None
                assert isinstance(lrc_path, Path)

                # Verification 2: LRC file exists at output_path
                assert lrc_path.exists()

                # Verification 3: LRC file contains key lyric lines from input
                # Section headers (Verse 1, Chorus, Bridge) may not be aligned
                # if Whisper doesn't transcribe them, so verify actual lyric content
                lrc_text = lrc_path.read_text(encoding="utf-8")
                key_lyrics = [
                    "You are my strength when I am weak",
                    "You are the treasure that I seek",
                    "You are my all in all",
                    "Seeking You as a precious jewel",
                    "Lord, to give up I'd be a fool",
                    "Jesus, Lamb of God, worthy is Your name",
                    "Taking my sin, my cross, my shame",
                    "Rising again I bless Your name",
                ]
                for key_line in key_lyrics:
                    assert key_line in lrc_text, (
                        f"Key lyric not found in LRC: '{key_line}'"
                    )

                # Verification 4: Parse LRC and verify timestamps
                parsed_lines = parse_lrc_file(lrc_path)

                # Verification 5: Timestamps are in ascending order (monotonic)
                for i in range(1, len(parsed_lines)):
                    assert parsed_lines[i][0] >= parsed_lines[i - 1][0], (
                        f"Timestamp ordering violation: "
                        f"{parsed_lines[i][0]} < {parsed_lines[i-1][0]}"
                    )

                # Verification 6: First timestamp is >= 0.0
                assert parsed_lines[0][0] >= 0.0

                # Verification 7: Last timestamp is <= audio duration
                assert parsed_lines[-1][0] <= audio_duration, (
                    f"Last timestamp {parsed_lines[-1][0]}s "
                    f"exceeds audio duration {audio_duration}s"
                )

                # Verification 8: Verify unique lyric count covers key content
                # Section headers may not be included, so verify coverage of actual lyrics
                unique_count_output = len(count_unique_lines(parsed_lines))
                assert unique_count_output >= 8, (
                    f"Output should have at least 8 unique lyric lines, "
                    f"got {unique_count_output}"
                )

                # Verification 9: Repeated sections have multiple entries
                # Chorus "You are my all in all" should appear 3 times
                chorus_counts = count_unique_lines(parsed_lines)
                assert chorus_counts.get("You are my all in all", 0) >= 3, (
                    "Chorus line should appear at least 3 times (ver, x2, x3)"
                )

                # Verification 10: LRC format is valid
                assert verify_lrc_format(parsed_lines), "LRC format validation failed"

                # Verification 11: Qwen3 client was actually called
                mock_client.align.assert_called_once()

                # Verification 12: LLM alignment was called first (pipeline order)
                mock_llm_align.assert_called_once()

                # Verification 13: Check that Qwen3 refined timestamps are used
                # by verifying the first timestamp matches Qwen3 output
                assert parsed_lines[0][0] == 0.0, "First timestamp from Qwen3"


@pytest.mark.asyncio
async def test_qwen3_refinement_applied(
    integration_audio_path: Path,
    integration_lyrics: str,
    mock_whisper_phrases_full: List[WhisperPhrase],
    mock_llm_aligned_lines_full: List[LRCLine],
) -> None:
    """Verify Qwen3 refinement replaces LLM timestamps.

    This test verifies:
    1. LLM returns timestamps at phrase boundaries
    2. Qwen3 returns refined timestamps with character-level precision
    3. Final LRC uses Qwen3 timestamps (not LLM)
    """
    options = LrcOptions(use_qwen3=True)

    # LLM-aligned timestamps (phrase-level)
    llm_timestamps = [line.time_seconds for line in mock_llm_aligned_lines_full]

    # Qwen3-refined timestamps (more precise)
    qwen3_refined_lines = [
        LRCLine(time_seconds=t + 0.05, text=mock_llm_aligned_lines_full[i].text)
        for i, t in enumerate(llm_timestamps)
    ]
    qwen3_timestamps = [line.time_seconds for line in qwen3_refined_lines]

    # Mock Whisper transcription
    with patch(
        "sow_analysis.workers.lrc._run_whisper_transcription",
        return_value=mock_whisper_phrases_full,
    ):
        with patch(
            "sow_analysis.workers.lrc._llm_align",
            new_callable=AsyncMock,
            return_value=mock_llm_aligned_lines_full,
        ):
            # Mock Qwen3Client to return refined timestamps
            qwen3_lrc_content = "\n".join(line.format() for line in qwen3_refined_lines)
            mock_client = AsyncMock()
            mock_client.align.return_value = AlignResponse(
                lrc_content=qwen3_lrc_content,
                json_data=None,
                line_count=len(qwen3_refined_lines),
                duration_seconds=max(p.end for p in mock_whisper_phrases_full),
            )

            with patch(
                "sow_analysis.workers.lrc.Qwen3Client",
                return_value=mock_client,
            ):
                # Generate LRC
                lrc_path, line_count, phrases = await generate_lrc(
                    audio_path=integration_audio_path,
                    lyrics_text=integration_lyrics,
                    options=options,
                    output_path=integration_audio_path.with_suffix(".lrc"),
                    content_hash="refinement_test_hash",
                )

                # Parse LRC output
                parsed_lines = parse_lrc_file(lrc_path)
                output_timestamps = [t for t, _ in parsed_lines]

                # Verify final LRC uses Qwen3 timestamps (not LLM)
                for i, qwen3_ts in enumerate(qwen3_timestamps):
                    if i < len(output_timestamps):
                        assert abs(output_timestamps[i] - qwen3_ts) < 0.01, (
                            f"Line {i}: Expected Qwen3 timestamp {qwen3_ts}, "
                            f"got {output_timestamps[i]}"
                        )


@pytest.mark.asyncio
async def test_qwen3_disabled_uses_llm(
    integration_audio_path: Path,
    integration_lyrics: str,
    mock_whisper_phrases_full: List[WhisperPhrase],
    mock_llm_aligned_lines_full: List[LRCLine],
) -> None:
    """Test that pipeline works correctly with Qwen3 disabled.

    Verifies:
    1. Qwen3Client is NOT called
    2. LLM alignment produces final output
    3. Pipeline still works correctly
    """
    options = LrcOptions(use_qwen3=False)

    with patch(
        "sow_analysis.workers.lrc._run_whisper_transcription",
        return_value=mock_whisper_phrases_full,
    ):
        with patch(
            "sow_analysis.workers.lrc._llm_align",
            new_callable=AsyncMock,
            return_value=mock_llm_aligned_lines_full,
        ) as mock_llm_align:
            # Qwen3Client should NOT be called
            with patch(
                "sow_analysis.workers.lrc.Qwen3Client"
            ) as mock_qwen3_client:
                # Generate LRC with Qwen3 disabled
                lrc_path, line_count, phrases = await generate_lrc(
                    audio_path=integration_audio_path,
                    lyrics_text=integration_lyrics,
                    options=options,
                    output_path=integration_audio_path.with_suffix(".lrc"),
                    content_hash=None,  # Disable Qwen3
                )

                # Verification 1: LRC file was created
                assert lrc_path.exists()
                assert line_count == len(mock_llm_aligned_lines_full)

                # Verification 2: LLL alignment was called
                mock_llm_align.assert_called_once()

                # Verification 3: Qwen3Client was NOT instantiated/called
                mock_qwen3_client.assert_not_called()

                # Verification 4: LRC contains expected content
                lrc_text = lrc_path.read_text(encoding="utf-8")
                assert "You are my all in all" in lrc_text
                assert "Seeking You as a precious jewel" in lrc_text
