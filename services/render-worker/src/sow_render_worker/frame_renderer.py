from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from PIL import Image, ImageDraw, ImageFont

from sow_render_worker.lrc_parser import (
    GlobalLRCLine,
    estimate_last_lyric_duration,
    group_lyrics_by_song,
)

VideoTemplateName = Literal["dark", "gradient_warm", "gradient_blue"]
FontSizePreset = Literal["S", "M", "L", "XL"]


@dataclass(frozen=True)
class VideoTemplate:
    name: VideoTemplateName
    background_color: tuple[int, int, int]
    text_color: tuple[int, int, int]
    highlight_color: tuple[int, int, int]
    font_size: int
    resolution: tuple[int, int]


@dataclass(frozen=True)
class SegmentInfo:
    id: str
    song_id: str
    position: int
    song_title: str
    song_album_name: str | None = None
    song_composer: str | None = None
    song_lyricist: str | None = None
    start_time_seconds: float = 0.0
    duration_seconds: float = 0.0
    tempo_bpm: float | None = None


@dataclass(frozen=True)
class TitleCardConfig:
    enabled: bool
    duration_seconds: float
    songset_name: str
    song_count: int
    total_duration_seconds: float


FONT_SIZE_PRESETS: dict[FontSizePreset, int] = {
    "S": 32,
    "M": 48,
    "L": 64,
    "XL": 80,
}

VIDEO_TEMPLATES: dict[VideoTemplateName, VideoTemplate] = {
    "dark": VideoTemplate(
        name="dark",
        background_color=(20, 20, 30),
        text_color=(200, 200, 200),
        highlight_color=(255, 255, 255),
        font_size=48,
        resolution=(1920, 1080),
    ),
    "gradient_warm": VideoTemplate(
        name="gradient_warm",
        background_color=(60, 30, 20),
        text_color=(255, 240, 220),
        highlight_color=(255, 200, 150),
        font_size=48,
        resolution=(1920, 1080),
    ),
    "gradient_blue": VideoTemplate(
        name="gradient_blue",
        background_color=(20, 30, 60),
        text_color=(220, 240, 255),
        highlight_color=(150, 200, 255),
        font_size=48,
        resolution=(1920, 1080),
    ),
}

_SANS_SERIF_FONT_PATHS = [
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
]


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in _SANS_SERIF_FONT_PATHS:
        try:
            return ImageFont.truetype(path, size)
        except (OSError, IOError):
            continue
    try:
        return ImageFont.truetype("sans-serif", size)
    except (OSError, IOError):
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


class FrameRenderer:
    def __init__(
        self,
        template: VideoTemplate,
        font_size_preset: FontSizePreset = "M",
        resolution: tuple[int, int] | None = None,
    ):
        self.template = template
        self.font_size_preset = font_size_preset
        self.resolution = resolution or template.resolution
        self.base_font_size = FONT_SIZE_PRESETS[font_size_preset]

    def get_base_font_size(self) -> int:
        return self.base_font_size

    def _get_font(self, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        return _load_font(size)

    def fit_text(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        target_font_size: int,
        max_width: int,
    ) -> int:
        font = self._get_font(target_font_size)
        bbox = draw.textbbox((0, 0), text, font=font)
        text_width = bbox[2] - bbox[0]
        if text_width <= max_width:
            return target_font_size
        scale = max_width / text_width
        return math.floor(target_font_size * scale)

    def get_margin(
        self,
        draw: ImageDraw.ImageDraw,
        font_size: int,
    ) -> float:
        font = self._get_font(font_size)
        bbox = draw.textbbox((0, 0), "中", font=font)
        return bbox[2] - bbox[0]

    def render_frame(
        self,
        lyrics: list[GlobalLRCLine],
        segments: list[SegmentInfo],
        current_time: float,
    ) -> Image.Image:
        width, height = self.resolution
        img = Image.new("RGBA", (width, height), (*self.template.background_color, 255))
        draw = ImageDraw.Draw(img)

        current_title = ""
        current_segment: SegmentInfo | None = None

        for segment in segments:
            segment_start = segment.start_time_seconds
            segment_end = segment_start + segment.duration_seconds
            if segment_start <= current_time < segment_end:
                current_title = segment.song_title or "Unknown"
                current_segment = segment
                break

        lyrics_by_song = group_lyrics_by_song(lyrics)
        current_song_lyrics = lyrics_by_song.get(current_title, [])

        intro_info_alpha = 0

        if current_segment and current_song_lyrics:
            first_lyric_time = current_song_lyrics[0].global_time_seconds
            if current_time < first_lyric_time:
                intro_info_alpha = self.render_intro_info(
                    current_segment,
                    current_time,
                    first_lyric_time,
                    draw,
                    width,
                    height,
                )

        if current_title and intro_info_alpha == 0:
            text_r, text_g, text_b = self.template.text_color
            title_font_size_target = math.floor(self.base_font_size * 0.8)
            margin = self.get_margin(draw, title_font_size_target)
            title_font_size = self.fit_text(
                draw, current_title, title_font_size_target, width - margin * 2
            )
            font = self._get_font(title_font_size)
            draw.text(
                (width // 2, 50),
                current_title,
                fill=(text_r, text_g, text_b),
                font=font,
                anchor="mt",
            )

        if current_song_lyrics:
            first_lyric_time = current_song_lyrics[0].global_time_seconds
            if current_time >= first_lyric_time:
                self.render_lyrics(
                    current_song_lyrics,
                    current_time,
                    current_title,
                    draw,
                    width,
                    height,
                )

        return img

    def render_intro_info(
        self,
        segment: SegmentInfo,
        current_time: float,
        first_lyric_time: float,
        draw: ImageDraw.ImageDraw,
        width: int,
        height: int,
    ) -> int:
        segment_start = segment.start_time_seconds
        gap_duration = first_lyric_time - segment_start

        if current_time >= first_lyric_time:
            return 0

        if gap_duration < 3.0:
            return 0

        if gap_duration < 7.0:
            info_duration = gap_duration * 0.6
            fade_duration = gap_duration * 0.4
            title_only_duration = 0.0
        else:
            fade_duration = 4.0
            title_only_duration = 3.0
            info_duration = gap_duration - fade_duration - title_only_duration

        time_into_gap = current_time - segment_start

        if time_into_gap >= info_duration + fade_duration:
            return 0

        info_lines: list[str] = []

        if segment.song_title:
            info_lines.append(f"歌曲：{segment.song_title}")
        if segment.song_album_name:
            info_lines.append(f"專輯：{segment.song_album_name}")
        if segment.song_composer:
            info_lines.append(f"作曲：{segment.song_composer}")
        if segment.song_lyricist:
            info_lines.append(f"作詞：{segment.song_lyricist}")
        info_lines.append("讚美之泉音樂事工")

        if not info_lines:
            return 0

        alpha = 255
        if time_into_gap >= info_duration:
            fade_progress = (time_into_gap - info_duration) / fade_duration
            alpha = math.floor(255 * (1.0 - math.sqrt(fade_progress)))

        line_height = self.base_font_size * 1.3
        total_height = len(info_lines) * line_height
        base_y = height / 2 - total_height / 2

        text_r, text_g, text_b = self.template.text_color
        intro_font_size = math.floor(self.base_font_size * 0.9)
        margin = self.get_margin(draw, intro_font_size)
        max_width = width - margin * 2

        for i, line in enumerate(info_lines):
            fitted_size = self.fit_text(draw, line, intro_font_size, max_width)
            font = self._get_font(fitted_size)
            fill_color = (
                int(text_r * alpha / 255),
                int(text_g * alpha / 255),
                int(text_b * alpha / 255),
            )
            y_pos = base_y + i * line_height + line_height / 2
            draw.text(
                (width // 2, int(y_pos)),
                line,
                fill=fill_color,
                font=font,
                anchor="mm",
            )

        return alpha

    def render_lyrics(
        self,
        song_lyrics: list[GlobalLRCLine],
        current_time: float,
        current_title: str,
        draw: ImageDraw.ImageDraw,
        width: int,
        height: int,
    ) -> None:
        current_index = -1
        for i, line in enumerate(song_lyrics):
            if line.global_time_seconds <= current_time:
                current_index = i
            else:
                break

        last_lyric_time = song_lyrics[-1].global_time_seconds
        if current_index == -1 and current_time > last_lyric_time:
            current_index = len(song_lyrics) - 1

        if current_index < 0:
            return

        current_line = song_lyrics[current_index]

        is_last_lyric = current_index == len(song_lyrics) - 1
        fade_alpha = 255
        is_last_lyric_faded = False

        if is_last_lyric:
            max_display = estimate_last_lyric_duration(song_lyrics)
            elapsed_since_last_lyric = current_time - current_line.global_time_seconds

            fade_duration = 7.0
            margin = 1.3
            fade_start_threshold = max_display * margin

            if elapsed_since_last_lyric > fade_start_threshold + fade_duration:
                return
            elif elapsed_since_last_lyric > fade_start_threshold:
                fade_progress = min(
                    1.0,
                    (elapsed_since_last_lyric - fade_start_threshold) / fade_duration,
                )
                log_alpha = 1.0 - math.sqrt(fade_progress)
                fade_alpha = math.floor(255 * log_alpha)
                is_last_lyric_faded = True

        highlight_r, highlight_g, highlight_b = self.template.highlight_color
        current_font_size_target = self.base_font_size * 2
        margin = self.get_margin(draw, current_font_size_target)
        current_font_size = self.fit_text(
            draw, current_line.text, current_font_size_target, width - margin * 2
        )
        font = self._get_font(current_font_size)
        fill_color = (
            int(highlight_r * fade_alpha / 255),
            int(highlight_g * fade_alpha / 255),
            int(highlight_b * fade_alpha / 255),
        )
        y = int(height * 0.33)
        draw.text(
            (width // 2, y),
            current_line.text,
            fill=fill_color,
            font=font,
            anchor="mt",
        )

        if not is_last_lyric_faded:
            next_index = current_index + 1
            if next_index < len(song_lyrics):
                next_line = song_lyrics[next_index]

                next_alpha = 128
                if is_last_lyric and fade_alpha < 255:
                    fade_progress = 1.0 - fade_alpha / 255.0
                    next_alpha = math.floor(128 * (1 - fade_progress))

                text_r, text_g, text_b = self.template.text_color
                next_font_size_target = self.base_font_size
                next_margin = self.get_margin(draw, next_font_size_target)
                next_font_size = self.fit_text(
                    draw,
                    next_line.text,
                    next_font_size_target,
                    width - next_margin * 2,
                )
                next_font = self._get_font(next_font_size)
                next_fill_color = (
                    int(text_r * next_alpha / 255),
                    int(text_g * next_alpha / 255),
                    int(text_b * next_alpha / 255),
                )
                next_y = int(height * 0.33 + 200)
                draw.text(
                    (width // 2, next_y),
                    next_line.text,
                    fill=next_fill_color,
                    font=next_font,
                    anchor="mt",
                )

    def render_title_card(self, config: TitleCardConfig) -> Image.Image:
        width, height = self.resolution
        img = Image.new("RGBA", (width, height), (*self.template.background_color, 255))
        draw = ImageDraw.Draw(img)

        text_r, text_g, text_b = self.template.text_color

        title_card_font_size_target = self.base_font_size * 2
        margin = self.get_margin(draw, title_card_font_size_target)
        title_card_font_size = self.fit_text(
            draw, config.songset_name, title_card_font_size_target, width - margin * 2
        )
        font = self._get_font(title_card_font_size)
        draw.text(
            (width // 2, int(height * 0.4)),
            config.songset_name,
            fill=(text_r, text_g, text_b),
            font=font,
            anchor="mm",
        )

        base_font = self._get_font(self.base_font_size)
        duration_minutes = math.floor(config.total_duration_seconds / 60)
        duration_seconds = math.floor(config.total_duration_seconds % 60)
        duration_text = f"{duration_minutes}:{duration_seconds:02d}"
        subtitle = f"{config.song_count} 首歌曲 · {duration_text}"
        draw.text(
            (width // 2, int(height * 0.55)),
            subtitle,
            fill=(text_r, text_g, text_b),
            font=base_font,
            anchor="mm",
        )

        return img
