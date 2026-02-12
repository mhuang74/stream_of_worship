"""Video engine service for sow-app.

Generates lyrics videos using Pillow for frame rendering and FFmpeg
for video encoding. Supports multiple templates and resolutions.
"""

import math
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from PIL import Image, ImageDraw, ImageFont

from stream_of_worship.app.db.models import SongsetItem
from stream_of_worship.app.logging_config import get_logger
from stream_of_worship.app.services.asset_cache import AssetCache
from stream_of_worship.app.services.audio_engine import AudioSegmentInfo, ExportResult
from stream_of_worship.core.paths import get_bundled_font_path

logger = get_logger(__name__)


@dataclass
class LRCLine:
    """A single line from an LRC file.

    Attributes:
        time_seconds: Timestamp in seconds
        text: Lyric text
    """

    time_seconds: float
    text: str


@dataclass
class GlobalLRCLine:
    """An LRC line with global timing for multi-song exports.

    Attributes:
        global_time_seconds: Time in the final video (seconds)
        local_time_seconds: Original time within the song (seconds)
        text: Lyric text
        title: Song title for this lyric
    """

    global_time_seconds: float
    local_time_seconds: float
    text: str
    title: str


@dataclass
class VideoTemplate:
    """Configuration for a video template.

    Attributes:
        name: Template name
        background_color: Background color as RGB tuple
        text_color: Text color as RGB tuple
        highlight_color: Color for active line
        font_size: Base font size
        resolution: Video resolution (width, height)
    """

    name: str
    background_color: tuple[int, int, int]
    text_color: tuple[int, int, int]
    highlight_color: tuple[int, int, int]
    font_size: int
    resolution: tuple[int, int]


# Predefined templates
TEMPLATES = {
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


class VideoEngine:
    """Video engine for generating lyrics videos.

    Renders lyrics synchronized with audio using LRC timing files.
    Outputs MP4 videos with H.264 encoding.

    Attributes:
        asset_cache: Asset cache for accessing LRC files
        template: Video template to use
        ffmpeg_path: Path to FFmpeg executable
    """

    def __init__(
        self,
        asset_cache: AssetCache,
        template: VideoTemplate,
        ffmpeg_path: str = "ffmpeg",
    ):
        """Initialize the video engine.

        Args:
            asset_cache: Asset cache for accessing LRC files
            template: Video template configuration
            ffmpeg_path: Path to FFmpeg executable
        """
        self.asset_cache = asset_cache
        self.template = template
        self.ffmpeg_path = ffmpeg_path
        self._font: Optional[ImageFont.FreeTypeFont] = None

        # Track song/lyric state for detecting stuck lyrics
        self._last_logged_song = None
        self._last_logged_lyric_time = None
        self._last_logged_lyric_text = None
        self._stuck_frame_counter = 0

    def _get_font(self, size: Optional[int] = None) -> ImageFont.FreeTypeFont:
        """Get a font for rendering text.

        Args:
            size: Font size (defaults to template font_size)

        Returns:
            PIL font object
        """
        font_size = size or self.template.font_size

        # Return cached font if requesting default size
        if font_size == self.template.font_size and self._font is not None:
            return self._font

        # Try to find a suitable font
        font_paths = [
            # Bundled font (highest priority)
            get_bundled_font_path(),
            # System fonts that support Chinese
            "/System/Library/Fonts/PingFang.ttc",  # macOS
            "/System/Library/Fonts/STHeiti Light.ttc",  # macOS
            "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",  # Linux
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",  # Linux
            "C:/Windows/Fonts/simhei.ttf",  # Windows
            # Fallback to default
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ]

        for font_path in font_paths:
            try:
                font = ImageFont.truetype(str(font_path), font_size)
                # Cache font at default size, log only once
                if font_size == self.template.font_size:
                    self._font = font
                    logger.debug(f"Font cached: {font_path}")
                return font
            except Exception:
                continue

        # Fallback to default font
        logger.warning("All font paths failed, using PIL default font")
        return ImageFont.load_default()

    def _get_video_codec_args(self, bitrate: str = "8000k") -> list[str]:
        """Get platform-appropriate video codec arguments.

        Uses hardware acceleration on macOS (h264_videotoolbox) for M-series chips,
        which is 3-5x faster than software encoding. Falls back to software encoding
        with ultrafast preset on other platforms.

        Args:
            bitrate: Video bitrate (e.g., "8000k" for ~8 Mbps).

        Returns:
            List of FFmpeg codec arguments
        """
        if sys.platform == "darwin":
            # Use Apple Silicon hardware encoder
            # Note: h264_videotoolbox doesn't support CRF, must use bitrate
            return ['-c:v', 'h264_videotoolbox', '-b:v', bitrate]
        else:
            # Fastest software encoding preset
            return ['-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '23']

    def _parse_lrc(self, lrc_content: str) -> list[LRCLine]:
        """Parse LRC file content.

        Args:
            lrc_content: Raw LRC file content

        Returns:
            List of LRC lines with timestamps
        """
        lines = []
        # Match [mm:ss.xx] or [mm:ss.xxx] format
        pattern = r'\[(\d{2}):(\d{2})\.(\d{2,3})\](.*)'

        for line in lrc_content.split('\n'):
            match = re.match(pattern, line.strip())
            if match:
                minutes = int(match.group(1))
                seconds = int(match.group(2))
                milliseconds = int(match.group(3).ljust(3, '0')[:3])
                text = match.group(4).strip()

                time_seconds = minutes * 60 + seconds + milliseconds / 1000.0
                if text:  # Only add lines with text
                    lines.append(LRCLine(time_seconds=time_seconds, text=text))

        return lines

    def _load_lrc(self, hash_prefix: str) -> Optional[list[LRCLine]]:
        """Load and parse LRC file for a recording.

        Args:
            hash_prefix: Recording hash prefix

        Returns:
            List of LRC lines or None if not found
        """
        logger.debug(f"LRC LOAD: hash_prefix={hash_prefix} (forced reload)")
        lrc_path = self.asset_cache.download_lrc(hash_prefix, force=True)
        if not lrc_path:
            logger.warning(f"LRC NOT FOUND: hash_prefix={hash_prefix}")
            return None

        try:
            content = lrc_path.read_text(encoding='utf-8')
            lines = self._parse_lrc(content)
            logger.info(f"LRC LOADED: hash_prefix={hash_prefix}, lines={len(lines)}")
            logger.info(f"LRC FULL CONTENT for hash_prefix={hash_prefix}:")
            logger.info("-" * 80)
            for line in content.split('\n'):
                logger.info(f"  {line}")
            logger.info("-" * 80)
            return lines
        except Exception as e:
            logger.error(f"LRC PARSE ERROR: hash_prefix={hash_prefix}, error={e}")
            return None

    def _estimate_last_lyric_duration(
        self, song_lyrics: list[GlobalLRCLine], tempo_bpm: Optional[float]
    ) -> float:
        """Estimate display duration for the last lyric line.

        Uses two-tier approach:
        1. Primary: Match previous occurrence of same text in song
        2. Fallback: Character count + BPM estimation

        Args:
            song_lyrics: All lyrics for the current song
            tempo_bpm: Song tempo in BPM (optional)

        Returns:
            Estimated duration in seconds (minimum 3s, no upper bound)
        """
        if not song_lyrics:
            return 5.0

        last_lyric = song_lyrics[-1]

        # Primary approach: find previous occurrence of same text
        for i in range(len(song_lyrics) - 2, -1, -1):
            if song_lyrics[i].text == last_lyric.text:
                # Use the duration from the previous occurrence
                if i + 1 < len(song_lyrics):
                    duration = song_lyrics[i + 1].global_time_seconds - song_lyrics[i].global_time_seconds
                    # Only log once per unique lookup (cache could be added here if needed)
                    return max(3.0, duration)

        # Fallback approach: character count + BPM estimation
        # Count Chinese characters (any Unicode char > 0x7F roughly works for Chinese)
        # Non-whitespace ASCII counts as ~0.5 chars
        text = last_lyric.text
        char_count = 0
        for char in text:
            if ord(char) > 0x7F:
                char_count += 1.0  # Chinese character
            elif not char.isspace():
                char_count += 0.5  # Non-space ASCII ~ half-width

        bpm = 70.0
        # Use tempo_bpm if it's a valid number, otherwise default to 70 BPM
        if isinstance(tempo_bpm, (int, float)) and tempo_bpm > 0:
            bpm = tempo_bpm
        # Assume 2 beats per character for comfortable reading pace
        beats_per_beat = 60.0 / bpm
        duration = char_count * 2 * beats_per_beat

        return max(3.0, duration)

    def _render_intro_info(
        self,
        segment: AudioSegmentInfo,
        current_time: float,
        first_lyric_time: float,
        img: Image.Image,
    ) -> int:
        """Render song intro info during the gap before first lyric.

        Displays song metadata (title, album, composer, lyricist) with Traditional
        Chinese labels during the intro period, with a fade-out transition before
        lyrics start.

        Timeline:
        - Transition window: info displayed (no header title)
        - Fade-out period (4s): info fades out, header title still hidden
        - Title-only period (3s): only header title shown
        - After first lyric: normal lyrics display with header title

        Short intro fallback (< 7s gap):
        - < 3s gap: skip intro entirely, show header title only
        - 3-7s gap: allocate 60% to info, 40% to fade, no title-only period

        Args:
            segment: Current audio segment with song metadata
            current_time: Current playback time in seconds
            first_lyric_time: Global time when first lyric appears
            img: Image to render onto

        Returns:
            Alpha value (0-255) for the intro info layer, 0 if not in intro period
        """
        segment_start = segment.start_time_seconds
        gap_duration = first_lyric_time - segment_start

        # Not in intro period
        if current_time >= first_lyric_time:
            return 0

        # Short intro: < 3s gap - skip intro entirely
        if gap_duration < 3.0:
            logger.debug(
                f"INTRO_SKIP: song='{segment.item.song_title}', "
                f"gap={gap_duration:.2f}s (< 3s), showing title only"
            )
            return 0

        width, height = self.template.resolution
        draw = ImageDraw.Draw(img)
        font = self._get_font(int(self.template.font_size * 0.9))

        # Calculate phases based on gap duration
        if gap_duration < 7.0:
            # Short intro: 60% info, 40% fade, no title-only period
            info_duration = gap_duration * 0.6
            fade_duration = gap_duration * 0.4
            title_only_duration = 0.0
            intro_mode = "short"
        else:
            # Normal intro: transition window + 4s fade + 3s title-only
            fade_duration = 4.0
            title_only_duration = 3.0
            info_duration = gap_duration - fade_duration - title_only_duration
            intro_mode = "normal"

        time_into_gap = current_time - segment_start

        # Log at the start of intro and at phase transitions
        phase = None
        if time_into_gap < info_duration:
            phase = "info"
        elif time_into_gap < info_duration + fade_duration:
            phase = "fade"
        else:
            phase = "title_only"

        # Log once per second during intro
        if int(current_time * 24) % 24 == 0:
            logger.info(
                f"INTRO_PHASE: song='{segment.item.song_title}', "
                f"mode={intro_mode}, phase={phase}, "
                f"time_into_gap={time_into_gap:.2f}s, "
                f"info_dur={info_duration:.2f}s, fade_dur={fade_duration:.2f}s, "
                f"title_only_dur={title_only_duration:.2f}s"
            )

        # Title-only period: don't render intro info
        if time_into_gap >= info_duration + fade_duration:
            logger.debug(
                f"INTRO_TITLE_ONLY: song='{segment.item.song_title}', "
                f"time_into_gap={time_into_gap:.2f}s, showing header title"
            )
            return 0

        # Build info lines with Traditional Chinese labels
        info_lines = []
        item = segment.item

        if item.song_title:
            info_lines.append(f"歌曲：{item.song_title}")
        if item.song_album_name:
            info_lines.append(f"專輯：{item.song_album_name}")
        if item.song_composer:
            info_lines.append(f"作曲：{item.song_composer}")
        if item.song_lyricist:
            info_lines.append(f"作詞：{item.song_lyricist}")
        info_lines.append("讚美之泉音樂事工")

        if not info_lines:
            return 0

        # Calculate alpha based on phase
        alpha = 255
        if time_into_gap >= info_duration:
            # In fade-out period
            fade_progress = (time_into_gap - info_duration) / fade_duration
            # Use sqrt-based fade for smooth transition (similar to last lyric fade)
            alpha = int(255 * (1.0 - math.sqrt(fade_progress)))
            logger.info(
                f"INTRO_FADE: song='{segment.item.song_title}', "
                f"fade_progress={fade_progress:.2f}, alpha={alpha}, "
                f"time_into_gap={time_into_gap:.2f}s, info_duration={info_duration:.2f}s"
            )
        elif int(current_time * 24) % 24 == 0:
            # Log during info phase once per second
            logger.info(
                f"INTRO_INFO_DISPLAY: song='{segment.item.song_title}', "
                f"alpha={alpha}, time_into_gap={time_into_gap:.2f}s"
            )

        # Calculate total block height
        line_height = int(self.template.font_size * 1.3)
        total_height = len(info_lines) * line_height

        # Render each line left-aligned, block centered horizontally
        base_y = height // 2 - total_height // 2

        for i, line in enumerate(info_lines):
            bbox = draw.textbbox((0, 0), line, font=font)
            text_width = bbox[2] - bbox[0]
            text_height = bbox[3] - bbox[1]

            # Center horizontally
            x = (width - text_width) // 2
            y = base_y + i * line_height

            # Use layer rendering for alpha support
            padding = 10
            text_layer = Image.new(
                'RGBA',
                (text_width + padding * 2, text_height + padding * 2),
                (0, 0, 0, 0)
            )
            text_draw = ImageDraw.Draw(text_layer)
            text_draw.text(
                (padding - bbox[0], padding - bbox[1]),
                line,
                font=font,
                fill=self.template.text_color + (alpha,)
            )
            img.paste(text_layer, (x - padding, y - padding), text_layer)

        # Log completion of intro rendering
        if int(current_time * 24) % 24 == 0:
            logger.debug(
                f"INTRO_RENDER_COMPLETE: song='{segment.item.song_title}', "
                f"lines_rendered={len(info_lines)}, alpha={alpha}"
            )

        return alpha

    def _render_frame(
        self,
        lyrics: list[GlobalLRCLine],
        segments: list[AudioSegmentInfo],
        current_time: float,
    ) -> Image.Image:
        """Render a single video frame.

        Args:
            lyrics: List of LRC lines with global timing
            segments: Audio segments with timing and song info
            current_time: Current playback time in seconds

        Returns:
            PIL Image
        """
        width, height = self.template.resolution
        img = Image.new('RGBA', (width, height), self.template.background_color + (255,))
        draw = ImageDraw.Draw(img)
        font = self._get_font()

        # Find current song based on segment timing
        current_title = ""
        current_segment: Optional[AudioSegmentInfo] = None
        current_segment_start = None
        current_segment_end = None
        for segment in segments:
            segment_start = segment.start_time_seconds
            segment_end = segment_start + segment.duration_seconds
            if segment_start <= current_time < segment_end:
                current_title = segment.item.song_title or "Unknown"
                current_segment = segment
                current_segment_start = segment_start
                current_segment_end = segment_end
                break

        # Log segment detection (less frequently to reduce log spam)
        if int(current_time * 24) % 24 == 0:  # Log once per second (at 24fps)
            if current_title:
                logger.debug(f"FRAME: time={current_time:.3f}s -> segment '{current_title}' "
                            f"[{current_segment_start:.3f}s - {current_segment_end:.3f}s]")
            else:
                logger.debug(f"FRAME: time={current_time:.3f}s -> NO SEGMENT MATCH")

        # Group lyrics by title for easy lookup
        lyrics_by_song: dict[str, list[GlobalLRCLine]] = {}
        for line in lyrics:
            if line.title not in lyrics_by_song:
                lyrics_by_song[line.title] = []
            lyrics_by_song[line.title].append(line)

        # Find active lyrics only for the current song
        current_song_lyrics = lyrics_by_song.get(current_title, [])

        # Track whether we're showing intro info (to suppress header title)
        intro_info_alpha = 0

        # Handle intro period before first lyric
        if current_segment and current_song_lyrics:
            first_lyric_time = current_song_lyrics[0].global_time_seconds

            if current_time < first_lyric_time:
                intro_info_alpha = self._render_intro_info(
                    current_segment, current_time, first_lyric_time, img
                )
                if intro_info_alpha > 0 and int(current_time * 24) % 24 == 0:
                    logger.info(
                        f"FRAME_INTRO_ACTIVE: song='{current_title}', "
                        f"time={current_time:.3f}s, intro_alpha={intro_info_alpha}, "
                        f"header_title_suppressed=True"
                    )
            elif int(current_time * 24) % 24 == 0 and current_time < first_lyric_time + 1:
                # Log when we've just passed the first lyric time (intro ended)
                logger.info(
                    f"INTRO_COMPLETE: song='{current_title}', "
                    f"first_lyric_time={first_lyric_time:.3f}s, transitioning to lyrics"
                )

        # Draw title at top (show when song is playing, unless intro info is displayed)
        # Show title if: no title, intro info has faded (alpha=0), or we're past the intro
        if current_title and intro_info_alpha == 0:
            title_font = self._get_font(int(self.template.font_size * 0.8))
            bbox = draw.textbbox((0, 0), current_title, font=title_font)
            text_width = bbox[2] - bbox[0]
            x = (width - text_width) // 2
            draw.text((x, 50), current_title, font=title_font, fill=self.template.text_color)

        # Only show lyrics if current time is within this song's lyric time range
        if current_song_lyrics:
            first_lyric_time = current_song_lyrics[0].global_time_seconds
            last_lyric_time = current_song_lyrics[-1].global_time_seconds

            # Log lyric timing context (once per second)
            if int(current_time * 24) % 24 == 0:
                logger.debug(f"LYRICS_RANGE: song='{current_title}', "
                            f"first_lyric={first_lyric_time:.3f}s, "
                            f"last_lyric={last_lyric_time:.3f}s, "
                            f"total_lines={len(current_song_lyrics)}")
                # Log if we're before the first lyric (gap at song start)
                if current_time < first_lyric_time:
                    logger.debug(f"LYRICS_BEFORE_FIRST: time={current_time:.3f}s < first_lyric={first_lyric_time:.3f}s "
                                f"(gap={first_lyric_time - current_time:.3f}s)")

            # Render lyrics from first lyric time until song ends
            if current_time >= first_lyric_time:
                # Find current lyric index within this song's lyrics
                current_index = -1
                for i, line in enumerate(current_song_lyrics):
                    if line.global_time_seconds <= current_time:
                        current_index = i
                    else:
                        break

                # If past all lyrics, continue showing the last one
                if current_index == -1 and current_time > last_lyric_time:
                    current_index = len(current_song_lyrics) - 1

                # Log lyric selection (less frequently, once every 5 seconds)
                if int(current_time * 24) % (24 * 5) == 0 and current_index >= 0:
                    current_line = current_song_lyrics[current_index]
                    logger.info(f"LYRIC_SELECTED: time={current_time:.3f}s -> "
                               f"idx={current_index}/{len(current_song_lyrics)-1} "
                               f"[local={current_line.local_time_seconds:.3f}s, "
                               f"global={current_line.global_time_seconds:.3f}s], "
                               f"text='{current_line.text}'")

                # Draw current line: 2x larger font, centered vertically
                if current_index >= 0:
                    current_line = current_song_lyrics[current_index]
                    current_font = self._get_font(int(self.template.font_size * 2))

                    # Check if this is the last lyric and handle fade-out
                    is_last_lyric = current_index == len(current_song_lyrics) - 1
                    fade_alpha = 255
                    is_last_lyric_faded = False

                    if is_last_lyric and current_index >= 0:
                        # Get BPM from current segment for duration estimation
                        tempo_bpm = None
                        for segment in segments:
                            segment_start = segment.start_time_seconds
                            if segment_start <= current_time < segment_start + segment.duration_seconds:
                                tempo_bpm = segment.item.tempo_bpm
                                break

                        # Estimate how long this lyric should display
                        max_display = self._estimate_last_lyric_duration(current_song_lyrics, tempo_bpm)
                        elapsed_since_last_lyric = current_time - current_line.global_time_seconds

                        # Fade duration: 7 seconds, with 30% margin before fade starts
                        FADE_DURATION = 7.0
                        MARGIN = 1.3
                        fade_start_threshold = max_display * MARGIN

                        # Check if we should fade or skip rendering
                        if elapsed_since_last_lyric > fade_start_threshold + FADE_DURATION:
                            # Fully faded - skip rendering this lyric
                            logger.info(
                                f"LAST_LYRIC_FULLY_FADED: song='{current_title}', "
                                f"elapsed={elapsed_since_last_lyric:.2f}s > threshold={fade_start_threshold + FADE_DURATION:.2f}s, "
                                f"skipping render"
                            )
                            current_index = -1
                            is_last_lyric_faded = True
                        elif elapsed_since_last_lyric > fade_start_threshold:
                            # In fade-out period (7 second fade with logarithmic curve)
                            fade_progress = min(1.0, (elapsed_since_last_lyric - fade_start_threshold) / FADE_DURATION)
                            # Logarithmic fade: starts fast, then lingers
                            # At progress=0: alpha=255, at progress=1: alpha=0
                            # Drops quickly at first, then slows down
                            # Using 1 - sqrt(progress): steep initial drop, then lingers
                            log_alpha = 1.0 - math.sqrt(fade_progress)
                            fade_alpha = int(255 * log_alpha)
                            is_last_lyric_faded = True

                            # Log fade start once, then periodically
                            if int(current_time * 24) % 24 == 0 or fade_progress < 0.05:
                                logger.info(
                                    f"LAST_LYRIC_FADE: song='{current_title}', "
                                    f"elapsed={elapsed_since_last_lyric:.2f}s, "
                                    f"fade_start={fade_start_threshold:.2f}s, "
                                    f"progress={fade_progress:.2f}, log_alpha={log_alpha:.2f}, alpha={fade_alpha}"
                                )
                        else:
                            is_last_lyric_faded = False
                            logger.info(
                                f"LAST_LYRIC_NO_FADE: song='{current_title}', "
                                f"elapsed={elapsed_since_last_lyric:.2f}s <= fade_start={fade_start_threshold:.2f}s"
                            )

                    # Only render if not fully faded
                    if current_index >= 0:
                        # Detect potentially stuck lyrics (same lyric for > 20 seconds)
                        is_same_song = (self._last_logged_song == current_title)
                        is_same_lyric_time = (self._last_logged_lyric_time == current_line.global_time_seconds)
                        is_same_text = (self._last_logged_lyric_text == current_line.text)

                        if is_same_song and (is_same_lyric_time or is_same_text):
                            self._stuck_frame_counter += 1
                            stuck_duration = self._stuck_frame_counter / 24.0  # at 24fps

                            # Warn if same lyric stuck for > 20 seconds
                            if self._stuck_frame_counter == 20 * 24:  # 20 seconds at 24fps
                                logger.warning(
                                    f"LYRIC_STUCK_DETECTED: song='{current_title}', "
                                    f"time={current_time:.3f}s, "
                                    f"stuck_for={stuck_duration:.1f}s, "
                                    f"lyric_global_time={current_line.global_time_seconds:.3f}s, "
                                    f"text='{current_line.text}'"
                                )
                            # Log when continuing to show last lyric past its timestamp (debug only)
                            elif current_time > last_lyric_time + 5:  # 5 seconds past last lyric
                                logger.debug(
                                    f"LYRIC_HOLDING_LAST: song='{current_title}', "
                                    f"time={current_time:.3f}s > last_lyric={last_lyric_time:.3f}s, "
                                    f"holding_for={current_time - last_lyric_time:.1f}s, "
                                    f"showing_lyric='{current_line.text}'"
                                )
                        else:
                            # Reset counter when song or lyric changes
                            self._stuck_frame_counter = 0
                            self._last_logged_song = current_title
                            self._last_logged_lyric_time = current_line.global_time_seconds
                            self._last_logged_lyric_text = current_line.text

                        bbox = draw.textbbox((0, 0), current_line.text, font=current_font)
                        text_width = bbox[2] - bbox[0]
                        text_height = bbox[3] - bbox[1]
                        x = (width - text_width) // 2
                        y = height // 2 - text_height // 2

                        # Always use layer rendering for consistent positioning
                        padding = 10
                        text_layer = Image.new('RGBA', (text_width + padding*2, text_height + padding*2), (0, 0, 0, 0))
                        text_draw = ImageDraw.Draw(text_layer)
                        # Draw with bbox offset to match pillow's draw.text() positioning
                        text_draw.text((padding - bbox[0], padding - bbox[1]), current_line.text, font=current_font,
                                       fill=self.template.highlight_color + (fade_alpha,))
                        img.paste(text_layer, (x - padding, y - padding), text_layer)

                    # Next line: 50% transparent, pushed 200px lower
                    # Skip next line if last lyric is faded (would incorrectly show first lyric)
                    if not is_last_lyric_faded:
                        next_index = current_index + 1
                        if next_index < len(current_song_lyrics):
                            next_line = current_song_lyrics[next_index]
                            next_font = font

                            bbox = draw.textbbox((0, 0), next_line.text, font=next_font)
                            text_width = bbox[2] - bbox[0]
                            text_height = bbox[3] - bbox[1]
                            x = (width - text_width) // 2
                            y = height // 2 + 200

                            # If last lyric is fading, also fade next line from 50% to 0%
                            if is_last_lyric and fade_alpha < 255:
                                fade_progress = 1.0 - (fade_alpha / 255.0)
                                next_alpha = int(128 * (1 - fade_progress))  # Start at 50%, fade to 0
                                if int(current_time * 24) % 24 == 0:
                                    logger.info(
                                        f"NEXT_LINE_FADE: song='{current_title}', "
                                        f"next_alpha={next_alpha}, fade_progress={fade_progress:.2f}, "
                                        f"last_lyric_fade_alpha={fade_alpha}"
                                    )
                            else:
                                next_alpha = 128

                            # Always use layer rendering for consistent positioning
                            padding = 10
                            text_layer = Image.new('RGBA', (text_width + padding*2, text_height + padding*2), (0, 0, 0, 0))
                            text_draw = ImageDraw.Draw(text_layer)
                            text_draw.text((padding - bbox[0], padding - bbox[1]), next_line.text, font=next_font,
                                           fill=self.template.text_color + (next_alpha,))
                            img.paste(text_layer, (x - padding, y - padding), text_layer)

        return img

    def generate_lyrics_video(
        self,
        audio_result: ExportResult,
        items: list[SongsetItem],
        output_path: Path,
        progress_callback: Optional[Callable[[int, int], None]] = None,
        fps: int = 24,
    ) -> Path:
        """Generate a lyrics video synchronized with audio.

        Uses platform-appropriate video encoding (h264_videotoolbox on macOS for
        M-series chips, libx264-ultrafast on other platforms) and AAC audio encoding.

        Args:
            audio_result: Result from audio engine export
            items: Songset items with recording info
            output_path: Path for output video
            progress_callback: Called with (current_frame, total_frames)
            fps: Frames per second (default 24 for lyrics videos)

        Returns:
            Path to generated video
        """
        # Ensure output directory exists
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Reset stuck detection counters for new export
        self._last_logged_song = None
        self._last_logged_lyric_time = None
        self._last_logged_lyric_text = None
        self._stuck_frame_counter = 0

        logger.info("=" * 80)
        logger.info(f"VIDEO GENERATION STARTED: duration={audio_result.total_duration_seconds:.3f}s, "
                   f"segments={len(audio_result.segments)}, fps={fps}")

        # Collect all lyrics with global timing
        all_lyrics: list[GlobalLRCLine] = []

        for segment in audio_result.segments:
            lyrics = self._load_lrc(segment.item.recording_hash_prefix or "")
            if lyrics:
                logger.info(f"LYRICS_GLOBAL_CONVERSION: song='{segment.item.song_title}', "
                           f"segment_start={segment.start_time_seconds:.3f}s, "
                           f"lyric_lines={len(lyrics)}")
                # Log first and last lyric global times for verification
                first_global = segment.start_time_seconds + lyrics[0].time_seconds
                last_global = segment.start_time_seconds + lyrics[-1].time_seconds
                logger.info(f"  First lyric: local={lyrics[0].time_seconds:.3f}s -> global={first_global:.3f}s, "
                           f"text='{lyrics[0].text}'")
                logger.info(f"  Last lyric: local={lyrics[-1].time_seconds:.3f}s -> global={last_global:.3f}s, "
                           f"text='{lyrics[-1].text}'")

                for line in lyrics:
                    all_lyrics.append(GlobalLRCLine(
                        global_time_seconds=segment.start_time_seconds + line.time_seconds,
                        local_time_seconds=line.time_seconds,
                        text=line.text,
                        title=segment.item.song_title or "Unknown",
                    ))
            else:
                logger.warning(f"LYRICS_GLOBAL_CONVERSION: song='{segment.item.song_title}' - NO LRC FOUND")

        # Summary of lyric timeline for debugging
        if all_lyrics:
            logger.info("LYRIC_TIMELINE_SUMMARY:")
            current_song = None
            song_lyric_count = 0
            for idx, lyric in enumerate(all_lyrics):
                if lyric.title != current_song:
                    if current_song:
                        logger.info(f"  '{current_song}': {song_lyric_count} lyrics")
                    current_song = lyric.title
                    song_lyric_count = 0
                song_lyric_count += 1
                # Log every 50th lyric to keep output manageable
                if idx % 50 == 0:
                    logger.info(f"    [{idx}] t={lyric.global_time_seconds:.3f}s: '{lyric.text}'")
            if current_song:
                logger.info(f"  '{current_song}': {song_lyric_count} lyrics")

        if not all_lyrics:
            # No lyrics - generate blank video
            logger.warning(f"VIDEO: NO LYRICS FOUND - generating blank video")
            return self._generate_blank_video(
                audio_result.output_path, output_path, fps=fps
            )

        logger.info(f"VIDEO: Total lyrics to render: {len(all_lyrics)}")

        # Generate frames and encode with FFmpeg
        total_frames = int(audio_result.total_duration_seconds * fps)
        width, height = self.template.resolution

        # Build FFmpeg command with platform-specific encoding
        cmd = [
            self.ffmpeg_path,
            '-y',  # Overwrite output
            '-f', 'rawvideo',
            '-vcodec', 'rawvideo',
            '-s', f'{width}x{height}',
            '-pix_fmt', 'rgba',  # RGBA for transparency support
            '-r', str(fps),
            '-i', '-',  # Read from stdin
            '-i', str(audio_result.output_path),
            *self._get_video_codec_args(),  # Platform-specific video codec
            '-c:a', 'aac',  # Encode to AAC (MP4 container standard)
            '-b:a', '192k',  # Audio bitrate
            '-shortest',
            str(output_path),
        ]

        # Start FFmpeg process
        process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        try:
            # Generate and pipe frames
            for frame in range(total_frames):
                current_time = frame / fps

                # Log periodic progress (every 5 seconds of video)
                if frame % (fps * 5) == 0 and frame > 0:
                    logger.info(f"VIDEO_PROGRESS: frame={frame}/{total_frames} "
                               f"({frame/total_frames*100:.1f}%), time={current_time:.1f}s")

                # Update progress
                if progress_callback and frame % fps == 0:  # Update every second
                    progress_callback(frame, total_frames)

                # Render frame using global timing
                img = self._render_frame(all_lyrics, audio_result.segments, current_time)

                # Convert to bytes and write
                frame_bytes = img.tobytes()
                process.stdin.write(frame_bytes)

        finally:
            if process.stdin:
                process.stdin.close()
            process.wait()

        if progress_callback:
            progress_callback(total_frames, total_frames)

        logger.info(f"VIDEO GENERATION COMPLETE: output='{output_path}', "
                   f"total_frames={total_frames}, duration={audio_result.total_duration_seconds:.3f}s")
        logger.info("=" * 80)

        return output_path

    def _generate_blank_video(
        self,
        audio_path: Path,
        output_path: Path,
        fps: int = 24,
    ) -> Path:
        """Generate a blank video with just the background.

        Uses platform-appropriate video encoding (h264_videotoolbox on macOS for
        M-series chips, libx264-ultrafast on other platforms) and AAC audio encoding.

        Args:
            audio_path: Path to audio file
            output_path: Path for output video
            fps: Frames per second (default 24 for lyrics videos)

        Returns:
            Path to generated video
        """
        # Get audio duration using ffprobe
        cmd = [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(audio_path),
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True)
            duration = float(result.stdout.strip())
        except Exception:
            duration = 0

        if duration == 0:
            raise ValueError("Could not determine audio duration")

        # Generate blank video with FFmpeg
        width, height = self.template.resolution
        bg_color = f"color=c={self.template.background_color[0]},{self.template.background_color[1]},{self.template.background_color[2]}"

        cmd = [
            self.ffmpeg_path,
            '-y',
            '-f', 'lavfi',
            '-i', f'color=c=black:s={width}x{height}:d={duration}',
            '-i', str(audio_path),
            *self._get_video_codec_args(bitrate="5000k"),  # Lower bitrate for blank
            '-c:a', 'aac',  # Encode to AAC (MP4 container standard)
            '-b:a', '192k',  # Audio bitrate
            '-shortest',
            str(output_path),
        ]

        subprocess.run(cmd, check=True, capture_output=True)

        return output_path

    @classmethod
    def get_available_templates(cls) -> list[str]:
        """Get list of available template names.

        Returns:
            List of template names
        """
        return list(TEMPLATES.keys())

    @classmethod
    def get_template(cls, name: str) -> VideoTemplate:
        """Get a template by name.

        Args:
            name: Template name

        Returns:
            VideoTemplate instance
        """
        return TEMPLATES.get(name, TEMPLATES["dark"])
