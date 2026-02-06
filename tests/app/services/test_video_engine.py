"""Tests for VideoEngine.

Tests LRC parsing and video template operations.
"""

from pathlib import Path
from unittest.mock import MagicMock, Mock

import pytest

from stream_of_worship.app.services.video_engine import (
    VideoEngine,
    VideoTemplate,
    LRCLine,
    TEMPLATES,
)


@pytest.fixture
def sample_lrc_content():
    """String with LRC format content."""
    return """[00:00.00]Line one
[00:05.50]Line two
[00:10.25]Line three
[00:15.00]Line four with longer text
[00:20.123]Line five
"""


@pytest.fixture
def sample_lrc_file(tmp_path, sample_lrc_content):
    """Temporary LRC file with test content."""
    lrc_path = tmp_path / "lyrics.lrc"
    lrc_path.write_text(sample_lrc_content, encoding="utf-8")
    return lrc_path


@pytest.fixture
def mock_asset_cache(sample_lrc_file):
    """Mocked AssetCache returning LRC path."""
    cache = MagicMock()
    cache.download_lrc = Mock(return_value=sample_lrc_file)
    return cache


@pytest.fixture
def video_engine(mock_asset_cache):
    """VideoEngine with mock cache."""
    return VideoEngine(
        asset_cache=mock_asset_cache,
        template=TEMPLATES["dark"],
    )


class TestLRCParsing:
    """Tests for LRC file parsing."""

    def test_parse_lrc_extracts_timestamps(self, video_engine, sample_lrc_content):
        """Verify [mm:ss.xx] format parsing."""
        lines = video_engine._parse_lrc(sample_lrc_content)

        assert len(lines) == 5
        assert isinstance(lines[0], LRCLine)
        assert lines[0].time_seconds == 0.0
        assert lines[0].text == "Line one"

    def test_parse_lrc_handles_milliseconds(self, video_engine):
        """Verify millisecond precision parsing."""
        content = "[00:05.123]Test line"
        lines = video_engine._parse_lrc(content)

        assert len(lines) == 1
        assert lines[0].time_seconds == 5.123

    def test_parse_lrc_handles_empty_lines(self, video_engine):
        """Verify empty line handling."""
        content = """[00:00.00]Line one

[00:05.00]Line two
"""
        lines = video_engine._parse_lrc(content)

        assert len(lines) == 2

    def test_parse_lrc_handles_invalid_lines(self, video_engine):
        """Verify graceful skip of invalid lines."""
        content = """[00:00.00]Valid line
Not a timestamp line
[00:05.00]Another valid line
[invalid]Invalid timestamp
"""
        lines = video_engine._parse_lrc(content)

        assert len(lines) == 2
        assert lines[0].text == "Valid line"
        assert lines[1].text == "Another valid line"

    def test_parse_lrc_skips_empty_text(self, video_engine):
        """Verify lines with no text are skipped."""
        content = "[00:00.00]   "
        lines = video_engine._parse_lrc(content)

        assert len(lines) == 0

    def test_parse_lrc_preserves_order(self, video_engine):
        """Verify timestamps are in order."""
        content = """[00:10.00]Third
[00:05.00]Second
[00:00.00]First
"""
        lines = video_engine._parse_lrc(content)

        # Should preserve order from file, not sorted
        assert lines[0].text == "Third"
        assert lines[1].text == "Second"
        assert lines[2].text == "First"


class TestLoadLRC:
    """Tests for loading LRC files."""

    def test_load_lrc_reads_file(self, video_engine, mock_asset_cache):
        """Verify file reading."""
        lines = video_engine._load_lrc("abc123def456")

        assert lines is not None
        assert len(lines) == 5
        mock_asset_cache.download_lrc.assert_called_once_with("abc123def456")

    def test_load_lrc_returns_none_when_not_cached(self, video_engine, mock_asset_cache):
        """Verify None when LRC not available."""
        mock_asset_cache.download_lrc.return_value = None

        lines = video_engine._load_lrc("missing_hash")

        assert lines is None

    def test_load_lrc_returns_none_on_read_error(self, video_engine, mock_asset_cache, tmp_path):
        """Verify None when file read fails."""
        # Create a path that doesn't exist
        mock_asset_cache.download_lrc.return_value = tmp_path / "nonexistent.lrc"

        lines = video_engine._load_lrc("abc123def456")

        assert lines is None


class TestVideoTemplates:
    """Tests for video templates."""

    def test_video_template_dark_exists(self):
        """Verify TEMPLATES dict has 'dark'."""
        assert "dark" in TEMPLATES
        assert isinstance(TEMPLATES["dark"], VideoTemplate)

    def test_video_template_gradient_warm_exists(self):
        """Verify 'gradient_warm' template."""
        assert "gradient_warm" in TEMPLATES
        template = TEMPLATES["gradient_warm"]
        assert template.name == "gradient_warm"

    def test_video_template_gradient_blue_exists(self):
        """Verify 'gradient_blue' template."""
        assert "gradient_blue" in TEMPLATES
        template = TEMPLATES["gradient_blue"]
        assert template.name == "gradient_blue"

    def test_get_available_templates_returns_list(self):
        """Verify template enumeration."""
        templates = VideoEngine.get_available_templates()

        assert isinstance(templates, list)
        assert "dark" in templates
        assert "gradient_warm" in templates
        assert "gradient_blue" in templates

    def test_get_template_returns_correct_template(self):
        """Verify template lookup."""
        template = VideoEngine.get_template("dark")

        assert template.name == "dark"
        assert template.background_color == (20, 20, 30)
        assert template.resolution == (1920, 1080)

    def test_get_template_defaults_to_dark(self):
        """Verify default template when name not found."""
        template = VideoEngine.get_template("nonexistent")

        assert template.name == "dark"

    def test_template_has_font_size(self):
        """Verify template has font_size attribute."""
        template = TEMPLATES["dark"]

        assert hasattr(template, "font_size")
        assert template.font_size > 0


class TestLRCLine:
    """Tests for LRCLine dataclass."""

    def test_lrcline_dataclass_creation(self):
        """Verify LRCLine works."""
        line = LRCLine(time_seconds=5.5, text="Test lyric")

        assert line.time_seconds == 5.5
        assert line.text == "Test lyric"

    def test_lrcline_zero_timestamp(self):
        """Verify LRCLine with zero timestamp."""
        line = LRCLine(time_seconds=0.0, text="Start")

        assert line.time_seconds == 0.0
