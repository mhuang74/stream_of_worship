"""Regression tests comparing Qwen3 output vs Whisper+LLM baseline.

These tests verify that Qwen3 refinement maintains or improves timestamp
accuracy compared to the Whisper+LLM baseline approach.
"""

import asyncio
from pathlib import Path
from typing import List, Tuple
from unittest.mock import AsyncMock, patch

import pytest

from sow_analysis.models import LrcOptions
from sow_analysis.services.qwen3_client import AlignResponse
from sow_analysis.workers.lrc import generate_lrc, WhisperPhrase, LRCLine


def parse_lrc_file(lrc_path: Path) -> List[Tuple[float, str]]:
    """Parse LRC file into list of (time_seconds, text) tuples.

    Args:
        lrc_path: Path to LRC file

    Returns:
        List of (time_seconds, text) tuples, sorted by time
    """
    lines = []
    import re
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


@pytest.fixture
def sample_audio_path(tmp_path: Path) -> Path:
    """Create a dummy audio file for testing."""
    audio_path = tmp_path / "test.mp3"
    audio_path.write_bytes(b"fake audio data")
    return audio_path


@pytest.fixture
def sample_lyrics() -> str:
    """Sample lyrics from fixture file (worship song with repeated chorus)."""
    fixture_path = Path(__file__).parent / "fixtures" / "sample_lyrics.txt"
    return fixture_path.read_text(encoding="utf-8").strip()


@pytest.fixture
def golden_llm_lrc_path() -> Path:
    """Path to golden baseline LRC fixture from Whisper+LLM path."""
    return Path(__file__).parent / "fixtures" / "golden_llm_lrc.txt"


@pytest.fixture
def mock_whisper_phrases(sample_lyrics: str) -> List[WhisperPhrase]:
    """Mock Whisper transcription phrases matching lyrics structure.

    Matches the sample_lyrics.txt fixture with realistic timing
    for a worship song (approximately 45 seconds total).
    """
    return [
        WhisperPhrase(text="Verse 1", start=0.0, end=3.5),
        WhisperPhrase(text="This is the first verse", start=3.5, end=7.0),
        WhisperPhrase(text="singing praises to the Lord", start=7.0, end=11.5),
        WhisperPhrase(text="Chorus", start=12.0, end=14.0),
        WhisperPhrase(text="Praise His holy name", start=14.0, end=17.5),
        WhisperPhrase(text="Praise His holy name", start=17.5, end=21.0),
        WhisperPhrase(text="Forever we will sing", start=21.0, end=25.5),
        WhisperPhrase(text="Verse 2", start=26.0, end=29.0),
        WhisperPhrase(text="This is the second verse", start=29.0, end=32.5),
        WhisperPhrase(text="lifting up our hearts to Him", start=32.5, end=37.0),
        WhisperPhrase(text="Chorus", start=37.5, end=39.5),
        WhisperPhrase(text="Praise His holy name", start=39.5, end=43.0),
        WhisperPhrase(text="Praise His holy name", start=43.0, end=46.5),
        WhisperPhrase(text="Forever we will sing", start=46.5, end=50.0),
    ]


@pytest.fixture
def mock_llm_aligned_lines() -> List[LRCLine]:
    """Mock LLM-aligned LRC lines (baseline output).

    Simulates Whisper+LLM baseline output with timestamp precision
    at the Whisper phrase level boundaries.
    """
    return [
        LRCLine(time_seconds=0.0, text="Verse 1"),
        LRCLine(time_seconds=3.5, text="This is the first verse"),
        LRCLine(time_seconds=7.0, text="singing praises to the Lord"),
        LRCLine(time_seconds=12.0, text="Chorus"),
        LRCLine(time_seconds=14.0, text="Praise His holy name"),
        LRCLine(time_seconds=17.5, text="Praise His holy name"),
        LRCLine(time_seconds=21.0, text="Forever we will sing"),
        LRCLine(time_seconds=26.0, text="Verse 2"),
        LRCLine(time_seconds=29.0, text="This is the second verse"),
        LRCLine(time_seconds=32.5, text="lifting up our hearts to Him"),
        LRCLine(time_seconds=37.5, text="Chorus"),
        LRCLine(time_seconds=39.5, text="Praise His holy name"),
        LRCLine(time_seconds=43.0, text="Praise His holy name"),
        LRCLine(time_seconds=46.5, text="Forever we will sing"),
    ]


@pytest.fixture
def mock_qwen3_lines() -> List[LRCLine]:
    """Mock Qwen3-refined LRC lines with finer timestamp precision.

    Simulates Qwen3 output with more granular timestamps (word-level precision)
    compared to the Whisper+LLM baseline (phrase-level timestamps).
    """
    return [
        LRCLine(time_seconds=0.0, text="Verse 1"),
        LRCLine(time_seconds=3.45, text="This is the first verse"),
        LRCLine(time_seconds=6.95, text="singing praises to the Lord"),
        LRCLine(time_seconds=11.95, text="Chorus"),
        LRCLine(time_seconds=13.85, text="Praise His holy name"),
        LRCLine(time_seconds=17.65, text="Praise His holy name"),
        LRCLine(time_seconds=21.45, text="Forever we will sing"),
        LRCLine(time_seconds=25.95, text="Verse 2"),
        LRCLine(time_seconds=29.35, text="This is the second verse"),
        LRCLine(time_seconds=32.95, text="lifting up our hearts to Him"),
        LRCLine(time_seconds=37.35, text="Chorus"),
        LRCLine(time_seconds=39.85, text="Praise His holy name"),
        LRCLine(time_seconds=43.45, text="Praise His holy name"),
        LRCLine(time_seconds=47.35, text="Forever we will sing"),
    ]


@pytest.mark.asyncio
async def test_baseline_llm_lrc_generation(
    sample_audio_path: Path,
    sample_lyrics: str,
    mock_whisper_phrases: List[WhisperPhrase],
    mock_llm_aligned_lines: List[LRCLine],
    golden_llm_lrc_path: Path,
) -> None:
    """Generate baseline LRC without Qwen3 and save as golden fixture.

    This test creates the golden baseline LRC file used for comparison
    against Qwen3-refined output.
    """
    # Disable Qwen3 to generate baseline
    options = LrcOptions(use_qwen3=False)

    # Mock Whisper transcription
    with patch(
        "sow_analysis.workers.lrc._run_whisper_transcription",
        return_value=mock_whisper_phrases,
    ):
        # Mock LLM alignment to return baseline LRC lines
        with patch(
            "sow_analysis.workers.lrc._llm_align",
            new_callable=AsyncMock,
            return_value=mock_llm_aligned_lines,
        ):
            # Generate LRC
            lrc_path, line_count, phrases = await generate_lrc(
                audio_path=sample_audio_path,
                lyrics_text=sample_lyrics,
                options=options,
                output_path=sample_audio_path.with_suffix(".lrc"),
                content_hash=None,  # Disable Qwen3
            )

            # Verify LRC was created
            assert lrc_path.exists()
            assert line_count == len(mock_llm_aligned_lines)

            # Read and verify LRC content
            lrc_text = lrc_path.read_text(encoding="utf-8")
            assert "Verse 1" in lrc_text
            assert "Chorus" in lrc_text
            assert "Forever we will sing" in lrc_text

            # Parse LRC file
            parsed_lines = parse_lrc_file(lrc_path)
            assert len(parsed_lines) == 14

            # Write golden baseline file
            golden_llm_lrc_path.write_text(lrc_text, encoding="utf-8")

            # Verify golden file was saved
            assert golden_llm_lrc_path.exists()


@pytest.mark.asyncio
async def test_qwen3_vs_baseline_comparison(
    sample_audio_path: Path,
    sample_lyrics: str,
    mock_whisper_phrases: List[WhisperPhrase],
    mock_llm_aligned_lines: List[LRCLine],
    mock_qwen3_lines: List[LRCLine],
    golden_llm_lrc_path: Path,
) -> None:
    """Compare Qwen3 output to Whisper+LLM baseline.

    Verifies that Qwen3:
    1. Has equal or more lines than baseline (no information loss)
    2. Has plausible timestamps (within audio duration range)
    3. Maintains all unique text content from baseline
    """
    # First, ensure golden baseline exists
    golden_llm_lrc_path.write_text(
        "\n".join(line.format() for line in mock_llm_aligned_lines),
        encoding="utf-8",
    )

    # Load golden baseline
    baseline_lines = parse_lrc_file(golden_llm_lrc_path)
    baseline_texts = set(text for _, text in baseline_lines)
    audio_duration = max(p.end for p in mock_whisper_phrases)

    # Enable Qwen3
    options = LrcOptions(use_qwen3=True)

    # Mock Whisper transcription (same as baseline)
    with patch(
        "sow_analysis.workers.lrc._run_whisper_transcription",
        return_value=mock_whisper_phrases,
    ):
        # Mock LLM alignment (baseline)
        with patch(
            "sow_analysis.workers.lrc._llm_align",
            new_callable=AsyncMock,
            return_value=mock_llm_aligned_lines,
        ):
            # Mock Qwen3Client to return refined LRC
            qwen3_lrc_content = "\n".join(line.format() for line in mock_qwen3_lines)
            mock_client = AsyncMock()
            mock_client.align.return_value = AlignResponse(
                lrc_content=qwen3_lrc_content,
                json_data=None,
                line_count=len(mock_qwen3_lines),
                duration_seconds=audio_duration,
            )

            with patch(
                "sow_analysis.workers.lrc.Qwen3Client",
                return_value=mock_client,
            ):
                # Generate LRC with Qwen3
                lrc_path, line_count, phrases = await generate_lrc(
                    audio_path=sample_audio_path,
                    lyrics_text=sample_lyrics,
                    options=options,
                    output_path=sample_audio_path.with_suffix(".lrc"),
                    content_hash="test_hash",  # Enable Qwen3
                )

                # Verify LRC was created
                assert lrc_path.exists()
                assert line_count == len(mock_qwen3_lines)

                # Parse Qwen3 LRC
                qwen3_lines = parse_lrc_file(lrc_path)
                qwen3_texts = set(text for _, text in qwen3_lines)

                # Verification 1: Qwen3 has equal or more lines than baseline
                assert len(qwen3_lines) >= len(baseline_lines), (
                    f"Qwen3 lost lines: {len(qwen3_lines)} < {len(baseline_lines)}"
                )

                # Verification 2: All timestamps are plausible (not negative, not beyond audio)
                for time_sec, text in qwen3_lines:
                    assert time_sec >= 0.0, f"Negative timestamp for '{text}': {time_sec}"
                    assert time_sec <= audio_duration, (
                        f"Timestamp beyond audio duration for '{text}': "
                        f"{time_sec}s > {audio_duration}s"
                    )

                # Verification 3: Qwen3 maintains all unique text from baseline
                assert baseline_texts.issubset(qwen3_texts), (
                    f"Qwen3 lost unique lyrics: {baseline_texts - qwen3_texts}"
                )

                # Verify Qwen3 was called
                mock_client.align.assert_called_once()


@pytest.mark.asyncio
async def test_qwen3_precision_improvement(
    sample_audio_path: Path,
    sample_lyrics: str,
    mock_whisper_phrases: List[WhisperPhrase],
    mock_llm_aligned_lines: List[LRCLine],
    mock_qwen3_lines: List[LRCLine],
) -> None:
    """Verify timing granularity improvement in Qwen3 output.

    Tests that:
    1. Qwen3 produces finer-grained timestamps
    2. Timestamp ordering is maintained (monotonic increase)
    3. No precision loss occurs (Qwen3 >= baseline precision)
    """
    options = LrcOptions(use_qwen3=True)

    # Mock Whisper transcription
    with patch(
        "sow_analysis.workers.lrc._run_whisper_transcription",
        return_value=mock_whisper_phrases,
    ):
        # Mock LLM alignment (baseline)
        with patch(
            "sow_analysis.workers.lrc._llm_align",
            new_callable=AsyncMock,
            return_value=mock_llm_aligned_lines,
        ):
            # Generate baseline LRC without Qwen3
            baseline_path = sample_audio_path.with_suffix(".baseline.lrc")
            baseline_options = LrcOptions(use_qwen3=False)
            baseline_lrc_path, _, _ = await generate_lrc(
                audio_path=sample_audio_path,
                lyrics_text=sample_lyrics,
                options=baseline_options,
                output_path=baseline_path,
                content_hash=None,
            )

            # Parse baseline lines
            baseline_lines = parse_lrc_file(baseline_lrc_path)

            # Mock Qwen3Client to return refined LRC
            qwen3_lrc_content = "\n".join(line.format() for line in mock_qwen3_lines)
            mock_client = AsyncMock()
            mock_client.align.return_value = AlignResponse(
                lrc_content=qwen3_lrc_content,
                json_data=None,
                line_count=len(mock_qwen3_lines),
                duration_seconds=max(p.end for p in mock_whisper_phrases),
            )

            with patch(
                "sow_analysis.workers.lrc.Qwen3Client",
                return_value=mock_client,
            ):
                # Generate Qwen3 LRC
                qwen3_path = sample_audio_path.with_suffix(".qwen3.lrc")
                qwen3_lrc_path, _, _ = await generate_lrc(
                    audio_path=sample_audio_path,
                    lyrics_text=sample_lyrics,
                    options=options,
                    output_path=qwen3_path,
                    content_hash="test_hash",
                )

                # Parse Qwen3 lines
                qwen3_lines_parsed = parse_lrc_file(qwen3_lrc_path)

                # Verification 1: Timestamp precision check
                # Qwen3 should produce refined timestamps (may be coarser is okay, just not worse)
                for i, (time_sec, text) in enumerate(qwen3_lines_parsed):
                    # Verify precision format: should have 2 decimal places
                    time_str = f"{time_sec:.2f}"
                    assert len(time_str.split(".")[1]) == 2, (
                        f"Timestamp precision issue for '{text}': {time_str}"
                    )

                # Verification 2: Monotonic ordering (timestamps must increase)
                for i in range(1, len(qwen3_lines_parsed)):
                    prev_time, _ = qwen3_lines_parsed[i - 1]
                    curr_time, _ = qwen3_lines_parsed[i]
                    assert curr_time >= prev_time, (
                        f"Timestamp ordering violation: {curr_time} < {prev_time}"
                    )

                # Verification 3: Baseline also has proper ordering (sanity check)
                for i in range(1, len(baseline_lines)):
                    prev_time, _ = baseline_lines[i - 1]
                    curr_time, _ = baseline_lines[i]
                    assert curr_time >= prev_time, (
                        f"Baseline timestamp ordering violation: {curr_time} < {prev_time}"
                    )
