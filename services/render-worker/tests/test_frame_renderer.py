from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from PIL import Image, ImageDraw

from sow_render_worker.frame_renderer import (
    FONT_SIZE_PRESETS,
    VIDEO_TEMPLATES,
    FontSizePreset,
    FrameRenderer,
    SegmentInfo,
    TitleCardConfig,
    VideoTemplate,
    VideoTemplateName,
    _load_font,
)
from sow_render_worker.lrc_parser import GlobalLRCLine


def _make_lyrics(times_and_texts, title="Song", offset=0.0):
    return [
        GlobalLRCLine(
            text=t,
            local_time_seconds=ts,
            global_time_seconds=ts + offset,
            title=title,
        )
        for ts, t in times_and_texts
    ]


def _make_segment(
    song_title="Test Song",
    start=0.0,
    duration=60.0,
    album=None,
    composer=None,
    lyricist=None,
    tempo=None,
):
    return SegmentInfo(
        id="seg1",
        song_id="song1",
        position=0,
        song_title=song_title,
        song_album_name=album,
        song_composer=composer,
        song_lyricist=lyricist,
        start_time_seconds=start,
        duration_seconds=duration,
        tempo_bpm=tempo,
    )


class TestVideoTemplate:
    def test_frozen_dataclass(self):
        t = VideoTemplate(
            name="dark",
            background_color=(20, 20, 30),
            text_color=(200, 200, 200),
            highlight_color=(255, 255, 255),
            font_size=48,
            resolution=(1920, 1080),
        )
        with pytest.raises(AttributeError):
            t.name = "other"

    def test_fields(self):
        t = VideoTemplate(
            name="dark",
            background_color=(20, 20, 30),
            text_color=(200, 200, 200),
            highlight_color=(255, 255, 255),
            font_size=48,
            resolution=(1920, 1080),
        )
        assert t.name == "dark"
        assert t.background_color == (20, 20, 30)
        assert t.text_color == (200, 200, 200)
        assert t.highlight_color == (255, 255, 255)
        assert t.font_size == 48
        assert t.resolution == (1920, 1080)


class TestFontSizePresets:
    def test_all_presets_defined(self):
        assert set(FONT_SIZE_PRESETS.keys()) == {"S", "M", "L", "XL"}

    def test_preset_values(self):
        assert FONT_SIZE_PRESETS["S"] == 32
        assert FONT_SIZE_PRESETS["M"] == 48
        assert FONT_SIZE_PRESETS["L"] == 64
        assert FONT_SIZE_PRESETS["XL"] == 80


class TestVideoTemplates:
    def test_all_templates_defined(self):
        assert set(VIDEO_TEMPLATES.keys()) == {"dark", "gradient_warm", "gradient_blue"}

    def test_dark_template(self):
        t = VIDEO_TEMPLATES["dark"]
        assert t.name == "dark"
        assert t.background_color == (20, 20, 30)
        assert t.text_color == (200, 200, 200)
        assert t.highlight_color == (255, 255, 255)
        assert t.font_size == 48
        assert t.resolution == (1920, 1080)

    def test_gradient_warm_template(self):
        t = VIDEO_TEMPLATES["gradient_warm"]
        assert t.name == "gradient_warm"
        assert t.background_color == (60, 30, 20)
        assert t.text_color == (255, 240, 220)
        assert t.highlight_color == (255, 200, 150)

    def test_gradient_blue_template(self):
        t = VIDEO_TEMPLATES["gradient_blue"]
        assert t.name == "gradient_blue"
        assert t.background_color == (20, 30, 60)
        assert t.text_color == (220, 240, 255)
        assert t.highlight_color == (150, 200, 255)

    def test_all_templates_have_1080p(self):
        for name, t in VIDEO_TEMPLATES.items():
            assert t.resolution == (1920, 1080), f"{name} resolution mismatch"


class TestSegmentInfo:
    def test_frozen_dataclass(self):
        s = SegmentInfo(
            id="1", song_id="s1", position=0, song_title="Test"
        )
        with pytest.raises(AttributeError):
            s.id = "2"

    def test_optional_fields_default_none(self):
        s = SegmentInfo(id="1", song_id="s1", position=0, song_title="Test")
        assert s.song_album_name is None
        assert s.song_composer is None
        assert s.song_lyricist is None
        assert s.tempo_bpm is None

    def test_all_fields(self):
        s = SegmentInfo(
            id="1",
            song_id="s1",
            position=2,
            song_title="Song",
            song_album_name="Album",
            song_composer="Composer",
            song_lyricist="Lyricist",
            start_time_seconds=10.0,
            duration_seconds=180.0,
            tempo_bpm=120.0,
        )
        assert s.song_album_name == "Album"
        assert s.start_time_seconds == 10.0
        assert s.tempo_bpm == 120.0


class TestTitleCardConfig:
    def test_frozen_dataclass(self):
        c = TitleCardConfig(
            enabled=True,
            duration_seconds=5.0,
            lines=("Test", "Song 1", "Song 2"),
            total_duration_seconds=300.0,
        )
        with pytest.raises(AttributeError):
            c.enabled = False

    def test_fields(self):
        c = TitleCardConfig(
            enabled=True,
            duration_seconds=5.0,
            lines=("My Set", "Song 1", "Song 2", "Song 3"),
            total_duration_seconds=600.0,
        )
        assert c.lines == ("My Set", "Song 1", "Song 2", "Song 3")
        assert c.total_duration_seconds == 600.0


class TestFrameRendererInit:
    def test_default_font_size_preset(self):
        renderer = FrameRenderer(template=VIDEO_TEMPLATES["dark"])
        assert renderer.font_size_preset == "M"
        assert renderer.base_font_size == 48

    def test_custom_font_size_preset(self):
        renderer = FrameRenderer(template=VIDEO_TEMPLATES["dark"], font_size_preset="L")
        assert renderer.base_font_size == 64

    def test_default_resolution_from_template(self):
        renderer = FrameRenderer(template=VIDEO_TEMPLATES["dark"])
        assert renderer.resolution == (1920, 1080)

    def test_custom_resolution(self):
        renderer = FrameRenderer(
            template=VIDEO_TEMPLATES["dark"], resolution=(1280, 720)
        )
        assert renderer.resolution == (1280, 720)

    def test_get_base_font_size(self):
        renderer = FrameRenderer(template=VIDEO_TEMPLATES["dark"], font_size_preset="XL")
        assert renderer.get_base_font_size() == 80


class TestVideoTemplatesAndFontSizes:
    def test_available_templates(self):
        assert "dark" in VIDEO_TEMPLATES
        assert "gradient_warm" in VIDEO_TEMPLATES
        assert "gradient_blue" in VIDEO_TEMPLATES

    def test_template_existing(self):
        t = VIDEO_TEMPLATES["dark"]
        assert t.name == "dark"

    def test_template_fallback_to_dark(self):
        t = VIDEO_TEMPLATES.get("nonexistent", VIDEO_TEMPLATES["dark"])
        assert t.name == "dark"

    def test_font_size_presets(self):
        assert FONT_SIZE_PRESETS["S"] == 32
        assert FONT_SIZE_PRESETS["M"] == 48
        assert FONT_SIZE_PRESETS["L"] == 64
        assert FONT_SIZE_PRESETS["XL"] == 80


class TestFitText:
    def test_text_fits_within_width(self):
        renderer = FrameRenderer(template=VIDEO_TEMPLATES["dark"])
        img = Image.new("RGB", (1920, 1080))
        draw = ImageDraw.Draw(img)
        result = renderer.fit_text(draw, "Hi", 48, 1920)
        assert result == 48

    def test_text_exceeds_width_scales_down(self):
        renderer = FrameRenderer(template=VIDEO_TEMPLATES["dark"])
        img = Image.new("RGB", (1920, 1080))
        draw = ImageDraw.Draw(img)
        long_text = "A" * 200
        result = renderer.fit_text(draw, long_text, 80, 500)
        assert result < 80

    def test_returns_integer(self):
        renderer = FrameRenderer(template=VIDEO_TEMPLATES["dark"])
        img = Image.new("RGB", (1920, 1080))
        draw = ImageDraw.Draw(img)
        result = renderer.fit_text(draw, "Test text", 48, 1000)
        assert isinstance(result, int)


class TestGetMargin:
    def test_returns_positive_value(self):
        renderer = FrameRenderer(template=VIDEO_TEMPLATES["dark"])
        img = Image.new("RGB", (1920, 1080))
        draw = ImageDraw.Draw(img)
        margin = renderer.get_margin(draw, 48)
        assert margin > 0

    def test_larger_font_larger_margin(self):
        renderer = FrameRenderer(template=VIDEO_TEMPLATES["dark"])
        img = Image.new("RGB", (1920, 1080))
        draw = ImageDraw.Draw(img)
        margin_small = renderer.get_margin(draw, 32)
        margin_large = renderer.get_margin(draw, 64)
        assert margin_large > margin_small


class TestRenderFrame:
    def test_returns_pil_image(self):
        renderer = FrameRenderer(template=VIDEO_TEMPLATES["dark"])
        lyrics = _make_lyrics([(5.0, "Hello"), (10.0, "World")])
        segments = [_make_segment(start=0.0, duration=60.0)]
        result = renderer.render_frame(lyrics, segments, 7.0)
        assert isinstance(result, Image.Image)

    def test_image_dimensions(self):
        renderer = FrameRenderer(template=VIDEO_TEMPLATES["dark"])
        lyrics = _make_lyrics([(5.0, "Hello")])
        segments = [_make_segment(start=0.0, duration=60.0)]
        result = renderer.render_frame(lyrics, segments, 7.0)
        assert result.size == (1920, 1080)

    def test_custom_resolution(self):
        renderer = FrameRenderer(
            template=VIDEO_TEMPLATES["dark"], resolution=(1280, 720)
        )
        lyrics = _make_lyrics([(5.0, "Hello")])
        segments = [_make_segment(start=0.0, duration=60.0)]
        result = renderer.render_frame(lyrics, segments, 7.0)
        assert result.size == (1280, 720)

    def test_background_color_applied(self):
        renderer = FrameRenderer(template=VIDEO_TEMPLATES["dark"])
        result = renderer.render_frame([], [], 0.0)
        pixel = result.getpixel((0, 0))
        assert pixel == (20, 20, 30, 255)

    def test_no_matching_segment(self):
        renderer = FrameRenderer(template=VIDEO_TEMPLATES["dark"])
        lyrics = _make_lyrics([(5.0, "Hello")])
        result = renderer.render_frame(lyrics, [], 7.0)
        assert isinstance(result, Image.Image)

    def test_before_first_lyric_shows_intro(self):
        renderer = FrameRenderer(template=VIDEO_TEMPLATES["dark"])
        lyrics = _make_lyrics([(10.0, "Hello")])
        segment = _make_segment(
            start=0.0, duration=60.0, composer="Test Composer"
        )
        result = renderer.render_frame(lyrics, [segment], 2.0)
        assert isinstance(result, Image.Image)

    def test_at_lyric_time(self):
        renderer = FrameRenderer(template=VIDEO_TEMPLATES["dark"])
        lyrics = _make_lyrics([(5.0, "Hello"), (10.0, "World")])
        segments = [_make_segment(start=0.0, duration=60.0)]
        result = renderer.render_frame(lyrics, segments, 5.0)
        assert isinstance(result, Image.Image)

    def test_between_lyrics(self):
        renderer = FrameRenderer(template=VIDEO_TEMPLATES["dark"])
        lyrics = _make_lyrics([(5.0, "Hello"), (10.0, "World")])
        segments = [_make_segment(start=0.0, duration=60.0)]
        result = renderer.render_frame(lyrics, segments, 7.0)
        assert isinstance(result, Image.Image)

    def test_after_all_lyrics(self):
        renderer = FrameRenderer(template=VIDEO_TEMPLATES["dark"])
        lyrics = _make_lyrics([(5.0, "Hello"), (10.0, "World")])
        segments = [_make_segment(start=0.0, duration=60.0)]
        result = renderer.render_frame(lyrics, segments, 15.0)
        assert isinstance(result, Image.Image)

    def test_multiple_segments(self):
        renderer = FrameRenderer(template=VIDEO_TEMPLATES["dark"])
        lyrics_song1 = _make_lyrics([(5.0, "Hello")], title="Song 1", offset=0.0)
        lyrics_song2 = _make_lyrics([(5.0, "World")], title="Song 2", offset=60.0)
        lyrics = lyrics_song1 + lyrics_song2
        seg1 = _make_segment(song_title="Song 1", start=0.0, duration=60.0)
        seg2 = _make_segment(song_title="Song 2", start=60.0, duration=60.0)
        result = renderer.render_frame(lyrics, [seg1, seg2], 65.0)
        assert isinstance(result, Image.Image)

    def test_chinese_lyrics(self):
        renderer = FrameRenderer(template=VIDEO_TEMPLATES["dark"])
        lyrics = _make_lyrics([(5.0, "讚美之泉"), (10.0, "哈利路亞")])
        segments = [_make_segment(start=0.0, duration=60.0)]
        result = renderer.render_frame(lyrics, segments, 7.0)
        assert isinstance(result, Image.Image)


class TestRenderIntroInfo:
    def test_short_gap_skips_intro(self):
        renderer = FrameRenderer(template=VIDEO_TEMPLATES["dark"])
        img = Image.new("RGB", (1920, 1080))
        draw = ImageDraw.Draw(img)
        segment = _make_segment(start=0.0, duration=60.0, composer="Test")
        result = renderer.render_intro_info(segment, 0.5, 2.0, draw, 1920, 1080)
        assert result == 0

    def test_normal_gap_returns_alpha(self):
        renderer = FrameRenderer(template=VIDEO_TEMPLATES["dark"])
        img = Image.new("RGB", (1920, 1080))
        draw = ImageDraw.Draw(img)
        segment = _make_segment(
            start=0.0, duration=60.0, composer="Test Composer"
        )
        result = renderer.render_intro_info(segment, 1.0, 10.0, draw, 1920, 1080)
        assert result == 255

    def test_fade_out_reduces_alpha(self):
        renderer = FrameRenderer(template=VIDEO_TEMPLATES["dark"])
        img = Image.new("RGB", (1920, 1080))
        draw = ImageDraw.Draw(img)
        segment = _make_segment(
            start=0.0, duration=60.0, composer="Test Composer"
        )
        alpha_start = renderer.render_intro_info(segment, 5.0, 10.0, draw, 1920, 1080)
        alpha_later = renderer.render_intro_info(segment, 8.0, 10.0, draw, 1920, 1080)
        assert alpha_later < alpha_start

    def test_after_fade_returns_zero(self):
        renderer = FrameRenderer(template=VIDEO_TEMPLATES["dark"])
        img = Image.new("RGB", (1920, 1080))
        draw = ImageDraw.Draw(img)
        segment = _make_segment(
            start=0.0, duration=60.0, composer="Test Composer"
        )
        result = renderer.render_intro_info(segment, 9.5, 10.0, draw, 1920, 1080)
        assert result == 0

    def test_info_lines_with_chinese_labels(self):
        renderer = FrameRenderer(template=VIDEO_TEMPLATES["dark"])
        img = Image.new("RGB", (1920, 1080))
        draw = ImageDraw.Draw(img)
        segment = _make_segment(
            start=0.0,
            duration=60.0,
            album="Test Album",
            composer="Test Composer",
            lyricist="Test Lyricist",
        )
        result = renderer.render_intro_info(segment, 1.0, 10.0, draw, 1920, 1080)
        assert result == 255

    def test_only_mandatory_line_still_renders(self):
        renderer = FrameRenderer(template=VIDEO_TEMPLATES["dark"])
        img = Image.new("RGB", (1920, 1080))
        draw = ImageDraw.Draw(img)
        segment = SegmentInfo(
            id="1",
            song_id="s1",
            position=0,
            song_title="",
            song_album_name=None,
            song_composer=None,
            song_lyricist=None,
            start_time_seconds=0.0,
            duration_seconds=60.0,
        )
        result = renderer.render_intro_info(segment, 1.0, 10.0, draw, 1920, 1080)
        assert result == 255

    def test_sqrt_based_fade(self):
        renderer = FrameRenderer(template=VIDEO_TEMPLATES["dark"])
        img = Image.new("RGB", (1920, 1080))
        draw = ImageDraw.Draw(img)
        segment = _make_segment(start=0.0, duration=60.0, composer="Test")
        alpha_at_half_fade = renderer.render_intro_info(
            segment, 6.0, 10.0, draw, 1920, 1080
        )
        import math

        info_duration = 10.0 - 4.0 - 3.0
        fade_duration = 4.0
        fade_progress = (6.0 - info_duration) / fade_duration
        expected = math.floor(255 * (1.0 - math.sqrt(fade_progress)))
        assert alpha_at_half_fade == expected


class TestRenderLyrics:
    def test_renders_current_line(self):
        renderer = FrameRenderer(template=VIDEO_TEMPLATES["dark"])
        bg = VIDEO_TEMPLATES["dark"].background_color
        img = Image.new("RGB", (1920, 1080), bg)
        draw = ImageDraw.Draw(img)
        lyrics = _make_lyrics([(5.0, "Hello"), (10.0, "World")])
        renderer.render_lyrics(lyrics, 7.0, "Song", draw, 1920, 1080)
        blank = Image.new("RGB", (1920, 1080), bg)
        assert list(img.getdata()) != list(blank.getdata())

    def test_before_first_lyric_no_render(self):
        renderer = FrameRenderer(template=VIDEO_TEMPLATES["dark"])
        bg = VIDEO_TEMPLATES["dark"].background_color
        img = Image.new("RGB", (1920, 1080), bg)
        draw = ImageDraw.Draw(img)
        lyrics = _make_lyrics([(5.0, "Hello")])
        renderer.render_lyrics(lyrics, 2.0, "Song", draw, 1920, 1080)
        blank = Image.new("RGB", (1920, 1080), bg)
        assert list(img.getdata()) == list(blank.getdata())

    def test_after_last_lyric_shows_last(self):
        renderer = FrameRenderer(template=VIDEO_TEMPLATES["dark"])
        bg = VIDEO_TEMPLATES["dark"].background_color
        img = Image.new("RGB", (1920, 1080), bg)
        draw = ImageDraw.Draw(img)
        lyrics = _make_lyrics([(5.0, "Hello")])
        renderer.render_lyrics(lyrics, 8.0, "Song", draw, 1920, 1080)
        blank = Image.new("RGB", (1920, 1080), bg)
        assert list(img.getdata()) != list(blank.getdata())

    def test_last_lyric_fade_out(self):
        renderer = FrameRenderer(template=VIDEO_TEMPLATES["dark"])
        bg = VIDEO_TEMPLATES["dark"].background_color
        img_before = Image.new("RGB", (1920, 1080), bg)
        draw_before = ImageDraw.Draw(img_before)
        lyrics = _make_lyrics([(5.0, "Hello")])
        renderer.render_lyrics(lyrics, 8.0, "Song", draw_before, 1920, 1080)

        img_after = Image.new("RGB", (1920, 1080), bg)
        draw_after = ImageDraw.Draw(img_after)
        renderer.render_lyrics(lyrics, 50.0, "Song", draw_after, 1920, 1080)

        blank = Image.new("RGB", (1920, 1080), bg)
        assert list(img_before.getdata()) != list(blank.getdata())
        assert list(img_after.getdata()) == list(blank.getdata())

    def test_next_line_rendered(self):
        renderer = FrameRenderer(template=VIDEO_TEMPLATES["dark"])
        bg = VIDEO_TEMPLATES["dark"].background_color
        img = Image.new("RGB", (1920, 1080), bg)
        draw = ImageDraw.Draw(img)
        lyrics = _make_lyrics([(5.0, "Hello"), (10.0, "World")])
        renderer.render_lyrics(lyrics, 5.0, "Song", draw, 1920, 1080)
        blank = Image.new("RGB", (1920, 1080), bg)
        assert list(img.getdata()) != list(blank.getdata())

    def test_chinese_lyrics(self):
        renderer = FrameRenderer(template=VIDEO_TEMPLATES["dark"])
        bg = VIDEO_TEMPLATES["dark"].background_color
        img = Image.new("RGB", (1920, 1080), bg)
        draw = ImageDraw.Draw(img)
        lyrics = _make_lyrics([(5.0, "讚美之泉"), (10.0, "哈利路亞")])
        renderer.render_lyrics(lyrics, 7.0, "Song", draw, 1920, 1080)
        blank = Image.new("RGB", (1920, 1080), bg)
        assert list(img.getdata()) != list(blank.getdata())


class TestRenderTitleCard:
    def test_returns_pil_image(self):
        renderer = FrameRenderer(template=VIDEO_TEMPLATES["dark"])
        config = TitleCardConfig(
            enabled=True,
            duration_seconds=5.0,
            lines=("Test Set", "Song 1", "Song 2", "Song 3"),
            total_duration_seconds=300.0,
        )
        result = renderer.render_title_card(config)
        assert isinstance(result, Image.Image)

    def test_image_dimensions(self):
        renderer = FrameRenderer(template=VIDEO_TEMPLATES["dark"])
        config = TitleCardConfig(
            enabled=True,
            duration_seconds=5.0,
            lines=("Test Set", "Song 1", "Song 2", "Song 3"),
            total_duration_seconds=300.0,
        )
        result = renderer.render_title_card(config)
        assert result.size == (1920, 1080)

    def test_background_color(self):
        renderer = FrameRenderer(template=VIDEO_TEMPLATES["dark"])
        config = TitleCardConfig(
            enabled=True,
            duration_seconds=5.0,
            lines=("Test Set", "Song 1", "Song 2", "Song 3"),
            total_duration_seconds=300.0,
        )
        result = renderer.render_title_card(config)
        pixel = result.getpixel((0, 0))
        assert pixel == (20, 20, 30, 255)

    def test_duration_format(self):
        renderer = FrameRenderer(template=VIDEO_TEMPLATES["dark"])
        config = TitleCardConfig(
            enabled=True,
            duration_seconds=5.0,
            lines=("Test Set", "Song 1", "Song 2", "Song 3"),
            total_duration_seconds=125.0,
        )
        result = renderer.render_title_card(config)
        assert isinstance(result, Image.Image)

    def test_chinese_songset_name(self):
        renderer = FrameRenderer(template=VIDEO_TEMPLATES["dark"])
        config = TitleCardConfig(
            enabled=True,
            duration_seconds=5.0,
            lines=("讚美之泉詩歌集", "歌曲一", "歌曲二", "歌曲三", "歌曲四", "歌曲五"),
            total_duration_seconds=600.0,
        )
        result = renderer.render_title_card(config)
        assert isinstance(result, Image.Image)

    def test_gradient_warm_template(self):
        renderer = FrameRenderer(template=VIDEO_TEMPLATES["gradient_warm"])
        config = TitleCardConfig(
            enabled=True,
            duration_seconds=5.0,
            lines=("Warm Set", "Song 1", "Song 2"),
            total_duration_seconds=180.0,
        )
        result = renderer.render_title_card(config)
        pixel = result.getpixel((0, 0))
        assert pixel == (60, 30, 20, 255)


class TestLoadFont:
    def test_load_font_returns_font(self):
        from PIL import ImageFont

        font = _load_font(48)
        assert font is not None

    def test_load_font_different_sizes(self):
        font_small = _load_font(12)
        font_large = _load_font(100)
        assert font_small is not None
        assert font_large is not None


class TestFrameRendererIntegration:
    def test_full_render_pipeline(self):
        renderer = FrameRenderer(template=VIDEO_TEMPLATES["dark"])
        lyrics = _make_lyrics([
            (5.0, "First line"),
            (10.0, "Second line"),
            (15.0, "Third line"),
        ])
        segments = [_make_segment(start=0.0, duration=60.0)]

        for t in [0.0, 3.0, 7.0, 12.0, 20.0]:
            result = renderer.render_frame(lyrics, segments, t)
            assert isinstance(result, Image.Image)
            assert result.size == (1920, 1080)

    def test_title_card_and_frames(self):
        renderer = FrameRenderer(template=VIDEO_TEMPLATES["gradient_blue"])
        config = TitleCardConfig(
            enabled=True,
            duration_seconds=5.0,
            lines=("Blue Set", "Song 1", "Song 2", "Song 3", "Song 4"),
            total_duration_seconds=240.0,
        )
        title_card = renderer.render_title_card(config)
        assert title_card.size == (1920, 1080)

        lyrics = _make_lyrics([(5.0, "Hello")])
        segments = [_make_segment(start=0.0, duration=60.0)]
        frame = renderer.render_frame(lyrics, segments, 7.0)
        assert frame.size == (1920, 1080)

    def test_all_templates_produce_valid_frames(self):
        lyrics = _make_lyrics([(5.0, "Test")])
        segments = [_make_segment(start=0.0, duration=60.0)]

        for name in VIDEO_TEMPLATES:
            renderer = FrameRenderer(template=VIDEO_TEMPLATES[name])
            result = renderer.render_frame(lyrics, segments, 7.0)
            assert result.size == (1920, 1080)

    def test_all_font_presets_produce_valid_frames(self):
        lyrics = _make_lyrics([(5.0, "Test")])
        segments = [_make_segment(start=0.0, duration=60.0)]

        for preset in ["S", "M", "L", "XL"]:
            renderer = FrameRenderer(
                template=VIDEO_TEMPLATES["dark"], font_size_preset=preset
            )
            result = renderer.render_frame(lyrics, segments, 7.0)
            assert result.size == (1920, 1080)
