from __future__ import annotations

import logging
import math
import os
from collections import OrderedDict
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

from PIL import Image, ImageDraw, ImageFont

from sow_render_worker.lrc_parser import (
    GlobalLRCLine,
    estimate_last_lyric_duration,
    group_lyrics_by_song,
)

logger = logging.getLogger(__name__)

VideoTemplateName = Literal["dark", "gradient_warm", "gradient_blue"]
FontSizePreset = Literal["S", "M", "L", "XL"]

_DEFAULT_FADE_ALPHA_STEPS = 16
_DEFAULT_MAX_CACHE_ENTRIES = 300
_DEFAULT_CACHE_ENABLED = True


def _get_bool_env(name: str, default: bool) -> bool:
    val = os.environ.get(name, "").strip().lower()
    if val in ("1", "true", "yes", "on"):
        return True
    if val in ("0", "false", "no", "off"):
        return False
    return default


def _get_int_env(name: str, default: int) -> int:
    val = os.environ.get(name, "")
    if val.strip():
        try:
            return int(val)
        except ValueError:
            pass
    return default


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
    lines: tuple[str, ...]
    total_duration_seconds: float


@dataclass(frozen=True)
class VisualState:
    segment_id: str
    current_title: str
    current_segment: SegmentInfo | None
    current_song_lyrics: list[GlobalLRCLine]
    current_lyric_index: int
    intro_alpha: int
    fade_alpha: int
    is_last_lyric_faded: bool
    current_time: float


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


@lru_cache(maxsize=32)
def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in _SANS_SERIF_FONT_PATHS:
        try:
            font = ImageFont.truetype(path, size)
            logger.debug("Loaded font: %s (size=%d)", path, size)
            return font
        except (OSError, IOError):
            continue
    try:
        font = ImageFont.truetype("sans-serif", size)
        logger.debug("Loaded font: sans-serif (size=%d)", size)
        return font
    except (OSError, IOError):
        font = ImageFont.load_default(size=size)
        logger.warning("No TrueType font found, using default font (size=%d)", size)
        return font
    except TypeError:
        font = ImageFont.load_default()
        logger.warning("No TrueType font found, using default font (size=%d)", size)
        return font


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

        self._cache_enabled = _get_bool_env("SOW_FRAME_CACHE_ENABLED", _DEFAULT_CACHE_ENABLED)
        self._fade_alpha_steps = min(256, max(2, _get_int_env("SOW_FADE_ALPHA_STEPS", _DEFAULT_FADE_ALPHA_STEPS)))
        self._max_cache_entries = max(1, _get_int_env("SOW_MAX_CACHE_ENTRIES", _DEFAULT_MAX_CACHE_ENTRIES))
        self._frame_cache: OrderedDict[tuple, bytes] = OrderedDict()
        self._cache_hits = 0
        self._cache_misses = 0
        self._alpha_step_size = 256 // self._fade_alpha_steps

        logger.info(
            "FrameRenderer init: template=%s, font_size=%s, resolution=%dx%d, "
            "cache_enabled=%s, fade_alpha_steps=%d, max_entries=%d",
            self.template.name, self.font_size_preset, self.resolution[0], self.resolution[1],
            self._cache_enabled, self._fade_alpha_steps, self._max_cache_entries,
        )

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

    def clear_cache(self) -> None:
        self._frame_cache.clear()
        self._cache_hits = 0
        self._cache_misses = 0

    def get_cache_stats(self) -> dict[str, int]:
        return {
            "entries": len(self._frame_cache),
            "hits": self._cache_hits,
            "misses": self._cache_misses,
            "max_entries": self._max_cache_entries,
        }

    def _quantize_alpha(self, alpha: int) -> int:
        if alpha >= 255:
            return 255
        return (alpha // self._alpha_step_size) * self._alpha_step_size

    def _compute_intro_alpha(
        self,
        segment: SegmentInfo,
        current_time: float,
        first_lyric_time: float,
    ) -> int:
        segment_start = segment.start_time_seconds
        gap_duration = first_lyric_time - segment_start

        if current_time >= first_lyric_time or gap_duration < 3.0:
            return 0

        if gap_duration < 7.0:
            info_duration = gap_duration * 0.6
            fade_duration = gap_duration * 0.4
        else:
            fade_duration = 4.0
            title_only_duration = 3.0
            info_duration = gap_duration - fade_duration - title_only_duration

        time_into_gap = current_time - segment_start

        if time_into_gap >= info_duration + fade_duration:
            return 0

        if time_into_gap < info_duration:
            return 255

        fade_progress = (time_into_gap - info_duration) / fade_duration
        return math.floor(255 * (1.0 - math.sqrt(fade_progress)))

    def _compute_last_lyric_fade_alpha(
        self,
        song_lyrics: list[GlobalLRCLine],
        current_time: float,
        current_index: int,
    ) -> int:
        if current_index != len(song_lyrics) - 1:
            return 255

        max_display = estimate_last_lyric_duration(song_lyrics)
        elapsed_since_last_lyric = current_time - song_lyrics[current_index].global_time_seconds

        fade_duration = 7.0
        margin = 1.3
        fade_start_threshold = max_display * margin

        if elapsed_since_last_lyric > fade_start_threshold + fade_duration:
            return 0
        elif elapsed_since_last_lyric > fade_start_threshold:
            fade_progress = min(
                1.0,
                (elapsed_since_last_lyric - fade_start_threshold) / fade_duration,
            )
            return math.floor(255 * (1.0 - math.sqrt(fade_progress)))

        return 255

    def _resolve_visual_state(
        self,
        lyrics: list[GlobalLRCLine],
        segments: list[SegmentInfo],
        current_time: float,
    ) -> VisualState:
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

        intro_alpha = 0
        if current_segment and current_song_lyrics:
            first_lyric_time = current_song_lyrics[0].global_time_seconds
            if current_time < first_lyric_time:
                intro_alpha = self._compute_intro_alpha(
                    current_segment, current_time, first_lyric_time
                )

        current_lyric_index = -1
        fade_alpha = 255
        is_last_lyric_faded = False

        if current_song_lyrics and current_time >= current_song_lyrics[0].global_time_seconds:
            for i, line in enumerate(current_song_lyrics):
                if line.global_time_seconds <= current_time:
                    current_lyric_index = i
                else:
                    break

            if current_lyric_index >= 0:
                is_last = current_lyric_index == len(current_song_lyrics) - 1
                if is_last:
                    fade_alpha = self._compute_last_lyric_fade_alpha(
                        current_song_lyrics, current_time, current_lyric_index
                    )
                    if fade_alpha <= 0:
                        is_last_lyric_faded = True
                        current_lyric_index = -1

        return VisualState(
            segment_id=current_segment.id if current_segment else "",
            current_title=current_title,
            current_segment=current_segment,
            current_song_lyrics=current_song_lyrics,
            current_lyric_index=current_lyric_index,
            intro_alpha=intro_alpha,
            fade_alpha=fade_alpha,
            is_last_lyric_faded=is_last_lyric_faded,
            current_time=current_time,
        )

    def _compute_cache_key(self, state: VisualState) -> tuple:
        quantized_intro = self._quantize_alpha(state.intro_alpha) if state.intro_alpha > 0 else 0
        quantized_fade = self._quantize_alpha(state.fade_alpha) if state.fade_alpha < 255 else 255

        return (
            state.segment_id,
            state.current_title,
            state.current_lyric_index,
            quantized_intro,
            quantized_fade,
            state.is_last_lyric_faded,
        )

    def render_frame(
        self,
        lyrics: list[GlobalLRCLine],
        segments: list[SegmentInfo],
        current_time: float,
        _state: VisualState | None = None,
    ) -> Image.Image:
        state = _state or self._resolve_visual_state(lyrics, segments, current_time)
        return self._render_frame_impl(state)

    def render_frame_bytes(
        self,
        lyrics: list[GlobalLRCLine],
        segments: list[SegmentInfo],
        current_time: float,
    ) -> bytes:
        state = self._resolve_visual_state(lyrics, segments, current_time)

        if self._cache_enabled:
            cache_key = self._compute_cache_key(state)
            if cache_key in self._frame_cache:
                self._cache_hits += 1
                self._frame_cache.move_to_end(cache_key)
                return self._frame_cache[cache_key]

            self._cache_misses += 1
            img = self._render_frame_impl(state)
            frame_bytes = img.tobytes()

            self._frame_cache[cache_key] = frame_bytes
            if len(self._frame_cache) > self._max_cache_entries:
                self._frame_cache.popitem(last=False)

            return frame_bytes

        img = self._render_frame_impl(state)
        return img.tobytes()

    def _render_frame_impl(self, state: VisualState) -> Image.Image:
        width, height = self.resolution
        img = Image.new("RGBA", (width, height), (*self.template.background_color, 255))
        draw = ImageDraw.Draw(img)

        current_title = state.current_title
        current_segment = state.current_segment
        current_song_lyrics = state.current_song_lyrics
        current_time = state.current_time

        intro_info_alpha = 0

        if current_segment and current_song_lyrics and state.intro_alpha > 0:
            intro_info_alpha = self.render_intro_info(
                current_segment,
                current_time,
                current_song_lyrics[0].global_time_seconds,
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
        alpha = self._compute_intro_alpha(segment, current_time, first_lyric_time)
        if alpha <= 0:
            return 0

        segment_start = segment.start_time_seconds
        gap_duration = first_lyric_time - segment_start

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

        fade_alpha = self._compute_last_lyric_fade_alpha(song_lyrics, current_time, current_index)
        is_last_lyric_faded = is_last_lyric and fade_alpha <= 0

        if is_last_lyric_faded:
            return

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

        if not config.lines:
            return img

        margin = 40
        min_body_font_size = 16
        line_spacing_factor = 1.2
        heading_gap_factor = 1.5

        heading_font_size_target = self.base_font_size * 2
        body_font_size_target = heading_font_size_target - 20

        heading_font_size = heading_font_size_target
        body_font_size = body_font_size_target

        while True:
            heading_font = self._get_font(heading_font_size)
            body_font = self._get_font(body_font_size)

            total_height = 0
            for i, line in enumerate(config.lines):
                font = heading_font if i == 0 else body_font
                bbox = draw.textbbox((0, 0), line, font=font)
                line_height = bbox[3] - bbox[1]
                total_height += line_height
                if i == 0 and len(config.lines) > 1:
                    total_height += int(body_font_size * heading_gap_factor)
                elif i > 0:
                    total_height += int(body_font_size * line_spacing_factor)

            if total_height <= height - margin * 2 or body_font_size <= min_body_font_size:
                break

            heading_font_size -= 2
            body_font_size = max(min_body_font_size, heading_font_size - 20)

        heading_font = self._get_font(heading_font_size)
        body_font = self._get_font(body_font_size)

        y_start = (height - total_height) // 2
        current_y = y_start

        for i, line in enumerate(config.lines):
            font = heading_font if i == 0 else body_font
            target_size = heading_font_size if i == 0 else body_font_size
            fitted_size = self.fit_text(draw, line, target_size, width - margin * 2)
            font = self._get_font(fitted_size)
            draw.text(
                (width // 2, current_y),
                line,
                fill=(text_r, text_g, text_b),
                font=font,
                anchor="mt",
            )
            bbox = draw.textbbox((0, 0), line, font=font)
            line_height = bbox[3] - bbox[1]
            current_y += line_height
            if i == 0 and len(config.lines) > 1:
                current_y += int(body_font_size * heading_gap_factor)
            elif i > 0:
                current_y += int(body_font_size * line_spacing_factor)

        return img
