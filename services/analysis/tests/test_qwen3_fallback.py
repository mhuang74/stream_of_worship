"""Tests for Qwen3 fallback behavior when service fails or audio is too long."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from sow_analysis.models import LrcOptions, LrcJobRequest
from sow_analysis.services.qwen3_client import Qwen3ClientError
from sow_analysis.workers.lrc import generate_lrc


@pytest.fixture
def sample_audio_path(tmp_path: Path) -> Path:
    """Create a dummy audio file for testing."""
    audio_path = tmp_path / "test.mp3"
    audio_path.write_bytes(b"fake audio data")
    return audio_path


@pytest.fixture
def sample_lyrics() -> str:
    """Sample lyrics for testing."""
    return "Verse 1\nChorus lyrics\nVerse 2\nChorus lyrics again"


@pytest.fixture
def mock_whisper_phrases() -> list:
    """Mock Whisper transcription phrases (short audio, <1 min)."""
    from sow_analysis.workers.lrc import WhisperPhrase
    return [
        WhisperPhrase(text="Verse 1", start=0.0, end=5.0),
        WhisperPhrase(text="Chorus lyrics", start=5.0, end=10.0),
        WhisperPhrase(text="Verse 2", start=10.0, end=15.0),
        WhisperPhrase(text="Chorus lyrics again", start=15.0, end=20.0),
    ]


@pytest.fixture
def long_audio_phrases() -> list:
    """Mock Whisper transcription phrases for long audio (>5 min)."""
    from sow_analysis.workers.lrc import WhisperPhrase
    return [
        WhisperPhrase(text="First line", start=0.0, end=10.0),
        WhisperPhrase(text="...", start=10.0, end=310.0),  # 5+ minute audio
    ]


@pytest.fixture
def mock_llm_align_response() -> list:
    """Mock LLM-aligned LRC lines (fallback result)."""
    from sow_analysis.workers.lrc import LRCLine
    return [
        LRCLine(time_seconds=0.0, text="Verse 1"),
        LRCLine(time_seconds=5.0, text="Chorus lyrics"),
        LRCLine(time_seconds=10.0, text="Verse 2"),
        LRCLine(time_seconds=15.0, text="Chorus lyrics again"),
    ]


@pytest.mark.asyncio
async def test_qwen3_service_unavailable_fallback(
    sample_audio_path: Path,
    sample_lyrics: str,
    mock_whisper_phrases: list,
    mock_llm_align_response: list,
) -> None:
    """Test that Qwen3 service unavailability falls back to LLM-aligned LRC."""
    from sow_analysis.workers.lrc import WhisperPhrase, _llm_align

    options = LrcOptions(use_qwen3=True)

    # Mock Whisper transcription
    with patch(
        "sow_analysis.workers.lrc._run_whisper_transcription",
        return_value=mock_whisper_phrases,
    ):
        # Mock LLM alignment
        with patch(
            "sow_analysis.workers.lrc._llm_align",
            new_callable=AsyncMock,
            return_value=mock_llm_align_response,
        ):
            # Mock Qwen3Client to raise ConnectionError (service unavailable)
            with patch(
                "sow_analysis.workers.lrc.Qwen3Client",
                side_effect=ConnectionError("Cannot connect to Qwen3 service"),
            ):
                lrc_path, line_count, phrases = await generate_lrc(
                    audio_path=sample_audio_path,
                    lyrics_text=sample_lyrics,
                    options=options,
                    output_path=sample_audio_path.with_suffix(".lrc"),
                    content_hash="abc123",  # Enable Qwen3
                )

                # Verify LRC file was created (from LLM alignment, not Qwen3)
                assert lrc_path.exists()
                assert line_count == len(mock_llm_align_response)

                # Verify LLM alignment was called (fallback worked)
                _llm_align.assert_called_once()


@pytest.mark.asyncio
async def test_qwen3_timeout_fallback(
    sample_audio_path: Path,
    sample_lyrics: str,
    mock_whisper_phrases: list,
    mock_llm_align_response: list,
) -> None:
    """Test that Qwen3 timeout falls back to LLM-aligned LRC."""
    from sow_analysis.workers.lrc import _llm_align

    options = LrcOptions(use_qwen3=True)

    with patch(
        "sow_analysis.workers.lrc._run_whisper_transcription",
        return_value=mock_whisper_phrases,
    ):
        with patch(
            "sow_analysis.workers.lrc._llm_align",
            new_callable=AsyncMock,
            return_value=mock_llm_align_response,
        ):
            # Create a mock client that raises TimeoutError
            mock_client = AsyncMock()
            mock_client.align.side_effect = asyncio.TimeoutError("Qwen3 timed out")

            with patch(
                "sow_analysis.workers.lrc.Qwen3Client",
                return_value=mock_client,
            ):
                lrc_path, line_count, phrases = await generate_lrc(
                    audio_path=sample_audio_path,
                    lyrics_text=sample_lyrics,
                    options=options,
                    output_path=sample_audio_path.with_suffix(".lrc"),
                    content_hash="def456",
                )

                # Verify LRC file was created
                assert lrc_path.exists()
                assert line_count == len(mock_llm_align_response)

                # Verify LLM alignment was called (Qwen3 failed but pipeline continued)
                _llm_align.assert_called_once()
