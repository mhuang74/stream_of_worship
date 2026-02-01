"""Tests for LRC generation pipeline."""

import json
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

try:
    from stream_of_worship.ingestion.lrc_generator import (
        LRCLine,
        WhisperWord,
        LRCGenerator,
        parse_lrc_file,
    )
    HAS_IMPORT = True
except ImportError:
    HAS_IMPORT = False
    LRCLine = None
    WhisperWord = None
    LRCGenerator = None
    parse_lrc_file = None


if not HAS_IMPORT:
    pytest.skip("lrc_generation dependencies not installed", allow_module_level=True)


class TestLRCLine:
    """Tests for LRCLine dataclass."""

    def test_lrc_line_creation(self):
        """Test creating an LRCLine."""
        line = LRCLine(time_seconds=12.5, text="Test lyrics")

        assert line.time_seconds == 12.5
        assert line.text == "Test lyrics"

    def test_format(self):
        """Test LRCLine format method."""
        line1 = LRCLine(time_seconds=12.5, text="Line one")
        assert line1.format() == "[00:12.50] Line one"

        line2 = LRCLine(time_seconds=65.75, text="Line two")
        assert line2.format() == "[01:05.75] Line two"

        line3 = LRCLine(time_seconds=125.125, text="Line three")
        assert line3.format() == "[02:05.12] Line three"


class TestWhisperWord:
    """Tests for WhisperWord dataclass."""

    def test_whisper_word_creation(self):
        """Test creating a WhisperWord."""
        word = WhisperWord(word="test", start=1.0, end=1.5)

        assert word.word == "test"
        assert word.start == 1.0
        assert word.end == 1.5


class TestParseLRCFile:
    """Tests for parse_lrc_file function."""

    @pytest.fixture
    def lrc_file(self, tmp_path):
        """Fixture providing a temporary LRC file."""
        content = """[ti:Test Song]
[ar:Test Artist]
[offset:0]

[00:12.50]First line of lyrics
[00:18.20]Second line of lyrics
[01:05.75]Third line of lyrics
[01:30.00]Fourth line of lyrics
"""
        file_path = tmp_path / "test.lrc"
        file_path.write_text(content, encoding="utf-8")
        return file_path

    def test_parse_standard_lrc(self, lrc_file):
        """Test parsing a standard LRC file."""
        lines = parse_lrc_file(lrc_file)

        assert len(lines) == 4
        assert lines[0].time_seconds == 12.5
        assert lines[0].text == "First line of lyrics"
        assert lines[1].time_seconds == 18.2
        assert lines[1].text == "Second line of lyrics"

    def test_parse_skips_metadata_lines(self, tmp_path):
        """Test that metadata lines are skipped."""
        content = """[ti:Test Song]
[ar:Test Artist]
[al:Cyber Worship]
[by:Test Composer]
"""
        file_path = tmp_path / "metadata_only.lrc"
        file_path.write_text(content, encoding="utf-8")

        lines = parse_lrc_file(file_path)

        assert len(lines) == 0

    def test_parse_raises_file_not_found(self):
        """Test that parse raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            parse_lrc_file(Path("/nonexistent/file.lrc"))


class TestLRCGenerator:
    """Tests for LRCGenerator class."""

    @patch("stream_of_worship.ingestion.lrc_generator.whisper")
    @patch("stream_of_worship.ingestion.lrc_generator.openai")
    @patch("stream_of_worship.ingestion.lrc_generator.get_whisper_cache_path")
    def test_init_with_defaults(self, mock_whisper, mock_openai, mock_cache):
        """Test generator initialization with defaults."""
        mock_cache.return_value = Path("/cache")

        with patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-key"}):
            generator = LRCGenerator()

        assert generator.whisper_model_name == "large-v3"
        assert generator.llm_model == "openai/gpt-4o-mini"
        assert generator.api_base == "https://openrouter.ai/api/v1"

    @patch("stream_of_worship.ingestion.lrc_generator.whisper")
    @patch("stream_of_worship.ingestion.lrc_generator.openai")
    def test_init_with_custom_params(self, mock_openai, mock_whisper):
        """Test generator initialization with custom parameters."""
        with patch.dict("os.environ", {}, clear=True):
            generator = LRCGenerator(
                whisper_model="medium",
                llm_model="custom/model",
                api_key="test-key",
                api_base="https://custom.api/v1",
            )

        assert generator.whisper_model_name == "medium"
        assert generator.llm_model == "custom/model"
        assert generator.api_key == "test-key"
        assert generator.api_base == "https://custom.api/v1"
