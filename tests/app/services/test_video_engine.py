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
    GlobalLRCLine,
    TEMPLATES,
)
from stream_of_worship.app.services.audio_engine import AudioSegmentInfo


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


def create_mock_segment(
    song_title: str,
    start_time: float,
    duration: float,
    composer: str | None = None,
    lyricist: str | None = None,
    album: str | None = None,
):
    """Create a mock AudioSegmentInfo for testing."""
    mock_item = Mock()
    mock_item.song_title = song_title
    mock_item.song_composer = composer
    mock_item.song_lyricist = lyricist
    mock_item.song_album_name = album
    mock_item.tempo_bpm = 120.0
    return AudioSegmentInfo(
        item=mock_item,
        audio_path=Path("/tmp/test.mp3"),
        start_time_seconds=start_time,
        duration_seconds=duration,
        gap_before_seconds=0.0,
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
        mock_asset_cache.download_lrc.assert_called_once_with("abc123def456", force=True)

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


class TestGlobalLRCLine:
    """Tests for GlobalLRCLine dataclass."""

    def test_global_lrcline_creation(self):
        """Verify GlobalLRCLine works."""
        line = GlobalLRCLine(
            global_time_seconds=65.0,
            local_time_seconds=5.0,
            text="Test lyric",
            title="Test Song"
        )

        assert line.global_time_seconds == 65.0
        assert line.local_time_seconds == 5.0
        assert line.text == "Test lyric"
        assert line.title == "Test Song"


class TestRenderFrameGlobalTiming:
    """Tests for _render_frame with global timing - multi-song exports."""

    def test_render_frame_finds_lyric_by_global_time(self, video_engine):
        """Verify lyrics are found using global_time_seconds, not local."""
        # Simulate two songs: Song 1 at 0-60s, Song 2 at 60-120s
        lyrics = [
            GlobalLRCLine(global_time_seconds=0.0, local_time_seconds=0.0, text="Song 1 Line 1", title="Song 1"),
            GlobalLRCLine(global_time_seconds=5.0, local_time_seconds=5.0, text="Song 1 Line 2", title="Song 1"),
            # Song 2 starts at global time 60s, but local time 0s
            GlobalLRCLine(global_time_seconds=60.0, local_time_seconds=0.0, text="Song 2 Line 1", title="Song 2"),
            GlobalLRCLine(global_time_seconds=65.0, local_time_seconds=5.0, text="Song 2 Line 2", title="Song 2"),
        ]
        segments = [
            create_mock_segment("Song 1", 0.0, 60.0),
            create_mock_segment("Song 2", 60.0, 60.0),
        ]

        # At global time 60s, we should see Song 2 Line 1 (not Song 1 Line 1 with local_time=0)
        img = video_engine._render_frame(lyrics, segments, current_time=60.0)

        # Verify image was created
        assert img is not None
        assert img.size == (1920, 1080)

    def test_render_frame_at_boundary_between_songs(self, video_engine):
        """Verify correct lyric shown at song boundary."""
        lyrics = [
            GlobalLRCLine(global_time_seconds=0.0, local_time_seconds=0.0, text="First Song Start", title="Song 1"),
            GlobalLRCLine(global_time_seconds=30.0, local_time_seconds=30.0, text="First Song End", title="Song 1"),
            GlobalLRCLine(global_time_seconds=30.0, local_time_seconds=0.0, text="Second Song Start", title="Song 2"),
        ]
        segments = [
            create_mock_segment("Song 1", 0.0, 30.0),
            create_mock_segment("Song 2", 30.0, 30.0),
        ]

        # At exactly 30s, should show Song 2 (new segment starts at 30s)
        img = video_engine._render_frame(lyrics, segments, current_time=30.0)
        assert img is not None

    def test_render_frame_title_derived_from_segments(self, video_engine):
        """Verify title is derived from segments, not just active lyrics."""
        lyrics = [
            GlobalLRCLine(global_time_seconds=0.0, local_time_seconds=0.0, text="Line 1", title="First Song"),
            GlobalLRCLine(global_time_seconds=60.0, local_time_seconds=0.0, text="Line 2", title="Second Song"),
        ]
        segments = [
            create_mock_segment("First Song", 0.0, 60.0),
            create_mock_segment("Second Song", 60.0, 60.0),
        ]

        # At time 60s, title should be "Second Song" based on segment
        img = video_engine._render_frame(lyrics, segments, current_time=60.0)
        assert img is not None

    def test_render_frame_shows_title_before_first_lyric(self, video_engine):
        """Verify title appears during intro before first lyric starts."""
        lyrics = [
            GlobalLRCLine(global_time_seconds=10.0, local_time_seconds=10.0, text="First Lyric", title="Song 1"),
        ]
        segments = [
            create_mock_segment("Song 1", 0.0, 60.0),
        ]

        # At time 0, title should show but no lyrics (intro)
        img = video_engine._render_frame(lyrics, segments, current_time=0.0)
        assert img is not None

    def test_render_frame_second_song_lyrics_not_shown_early(self, video_engine):
        """Critical test: 2nd song lyrics should NOT show during 1st song."""
        # Song 2 starts at 60s global time
        lyrics = [
            GlobalLRCLine(global_time_seconds=0.0, local_time_seconds=0.0, text="Song 1 First Line", title="Song 1"),
            GlobalLRCLine(global_time_seconds=60.0, local_time_seconds=0.0, text="Song 2 First Line", title="Song 2"),
        ]
        segments = [
            create_mock_segment("Song 1", 0.0, 60.0),
            create_mock_segment("Song 2", 60.0, 60.0),
        ]

        # At video time 0, should NOT show "Song 2 First Line"
        img = video_engine._render_frame(lyrics, segments, current_time=0.0)
        assert img is not None

        # At video time 60, SHOULD show "Song 2 First Line"
        img_at_60 = video_engine._render_frame(lyrics, segments, current_time=60.0)
        assert img_at_60 is not None


class TestIntroTransitions:
    """Tests for song intro transition rendering."""

    def test_intro_info_rendered_during_transition_window(self, video_engine):
        """Verify intro info is shown during the transition window."""
        # Song starts at 0s, first lyric at 15s (10s transition window + 4s fade + 1s buffer)
        lyrics = [
            GlobalLRCLine(global_time_seconds=15.0, local_time_seconds=15.0, text="First Lyric", title="Test Song"),
        ]
        segments = [
            create_mock_segment(
                "Test Song", 0.0, 60.0,
                composer="John Composer",
                lyricist="Jane Lyricist",
                album="Test Album"
            ),
        ]

        # At time 2s (within transition window), intro info should be rendered
        img = video_engine._render_frame(lyrics, segments, current_time=2.0)
        assert img is not None
        assert img.size == (1920, 1080)

    def test_header_title_suppressed_during_intro_info(self, video_engine):
        """Verify header title is hidden while intro info is displayed."""
        lyrics = [
            GlobalLRCLine(global_time_seconds=15.0, local_time_seconds=15.0, text="First Lyric", title="Test Song"),
        ]
        segments = [
            create_mock_segment("Test Song", 0.0, 60.0, composer="Composer"),
        ]

        # At time 2s (intro period), title should be suppressed
        img = video_engine._render_frame(lyrics, segments, current_time=2.0)
        assert img is not None

    def test_header_title_shown_during_title_only_period(self, video_engine):
        """Verify header title appears during title-only period (3s before lyrics)."""
        lyrics = [
            GlobalLRCLine(global_time_seconds=15.0, local_time_seconds=15.0, text="First Lyric", title="Test Song"),
        ]
        segments = [
            create_mock_segment("Test Song", 0.0, 60.0, composer="Composer"),
        ]

        # At time 13s (2s before lyrics, within title-only period), title should show
        img = video_engine._render_frame(lyrics, segments, current_time=13.0)
        assert img is not None

    def test_intro_fade_out_transition(self, video_engine):
        """Verify intro info fades out smoothly before lyrics start."""
        lyrics = [
            GlobalLRCLine(global_time_seconds=15.0, local_time_seconds=15.0, text="First Lyric", title="Test Song"),
        ]
        segments = [
            create_mock_segment("Test Song", 0.0, 60.0, composer="Composer"),
        ]

        # At time 10s (within 4s fade period before lyrics), fade should be in progress
        img = video_engine._render_frame(lyrics, segments, current_time=10.0)
        assert img is not None

    def test_short_intro_skipped_when_less_than_3s(self, video_engine):
        """Verify intro is skipped when gap is less than 3 seconds."""
        lyrics = [
            GlobalLRCLine(global_time_seconds=2.0, local_time_seconds=2.0, text="First Lyric", title="Test Song"),
        ]
        segments = [
            create_mock_segment("Test Song", 0.0, 60.0, composer="Composer"),
        ]

        # At time 1s (gap is only 2s), title should show (intro skipped)
        img = video_engine._render_frame(lyrics, segments, current_time=1.0)
        assert img is not None

    def test_short_intro_compressed_when_3_to_7s(self, video_engine):
        """Verify compressed intro when gap is between 3-7 seconds."""
        lyrics = [
            GlobalLRCLine(global_time_seconds=5.0, local_time_seconds=5.0, text="First Lyric", title="Test Song"),
        ]
        segments = [
            create_mock_segment("Test Song", 0.0, 60.0, composer="Composer"),
        ]

        # At time 2s (within compressed intro period), info should show
        img = video_engine._render_frame(lyrics, segments, current_time=2.0)
        assert img is not None

    def test_intro_info_with_missing_metadata(self, video_engine):
        """Verify intro renders gracefully when metadata is missing."""
        lyrics = [
            GlobalLRCLine(global_time_seconds=15.0, local_time_seconds=15.0, text="First Lyric", title="Test Song"),
        ]
        # No composer, lyricist, or album provided
        segments = [
            create_mock_segment("Test Song", 0.0, 60.0),
        ]

        # Should still render without errors
        img = video_engine._render_frame(lyrics, segments, current_time=2.0)
        assert img is not None

    def test_intro_info_alpha_returned(self, video_engine):
        """Verify _render_intro_info returns appropriate alpha values."""
        segment = create_mock_segment(
            "Test Song", 0.0, 60.0,
            composer="Composer",
            album="Album"
        )

        # Create a test image
        from PIL import Image
        img = Image.new('RGBA', (1920, 1080), (0, 0, 0, 255))

        # During transition window: full alpha
        alpha = video_engine._render_intro_info(
            segment, current_time=2.0, first_lyric_time=15.0, img=img
        )
        assert alpha == 255

        # During fade period: partial alpha
        alpha_fade = video_engine._render_intro_info(
            segment, current_time=10.0, first_lyric_time=15.0, img=img
        )
        assert 0 < alpha_fade < 255

        # During title-only period: no intro info (alpha = 0)
        alpha_none = video_engine._render_intro_info(
            segment, current_time=13.0, first_lyric_time=15.0, img=img
        )
        assert alpha_none == 0

        # After lyrics start: no intro info (alpha = 0)
        alpha_after = video_engine._render_intro_info(
            segment, current_time=16.0, first_lyric_time=15.0, img=img
        )
        assert alpha_after == 0
