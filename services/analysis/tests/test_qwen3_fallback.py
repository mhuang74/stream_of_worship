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
