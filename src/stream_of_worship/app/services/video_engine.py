"""Video engine service for sow-app.

Generates lyrics videos using Pillow for frame rendering and FFmpeg
for video encoding. Supports multiple templates and resolutions.
"""

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
from stream_of_worship.app.services.audio_engine import ExportResult
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

    def _get_font(self, size: Optional[int] = None) -> ImageFont.FreeTypeFont:
        """Get a font for rendering text.

        Args:
            size: Font size (defaults to template font_size)

        Returns:
            PIL font object
        """
        font_size = size or self.template.font_size

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
                logger.debug(f"Successfully loaded font: {font_path}")
                return font
            except Exception as e:
                logger.debug(f"Failed to load font {font_path}: {e}")
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
        lrc_path = self.asset_cache.download_lrc(hash_prefix)
        if not lrc_path:
            return None

        try:
            content = lrc_path.read_text(encoding='utf-8')
            return self._parse_lrc(content)
        except Exception:
            return None

    def _render_frame(
        self,
        lyrics: list[GlobalLRCLine],
        current_time: float,
    ) -> Image.Image:
        """Render a single video frame.

        Args:
            lyrics: List of LRC lines with global timing
            current_time: Current playback time in seconds

        Returns:
            PIL Image
        """
        width, height = self.template.resolution
        img = Image.new('RGBA', (width, height), self.template.background_color + (255,))
        draw = ImageDraw.Draw(img)
        font = self._get_font()

        # Find current song title from the active lyric
        title = ""
        for line in lyrics:
            if line.global_time_seconds <= current_time:
                title = line.title
            else:
                break

        # Draw title at top
        if title:
            title_font = self._get_font(int(self.template.font_size * 0.8))
            bbox = draw.textbbox((0, 0), title, font=title_font)
            text_width = bbox[2] - bbox[0]
            x = (width - text_width) // 2
            draw.text((x, 50), title, font=title_font, fill=self.template.text_color)

        # Find current lyric line using global time
        current_index = -1
        for i, line in enumerate(lyrics):
            if line.global_time_seconds <= current_time:
                current_index = i
            else:
                break

        # Draw lyrics (show current line and next line)
        # Current line: 2x larger font, centered vertically
        if current_index >= 0:
            current_line = lyrics[current_index]
            current_font = self._get_font(int(self.template.font_size * 2))

            bbox = draw.textbbox((0, 0), current_line.text, font=current_font)
            text_width = bbox[2] - bbox[0]
            x = (width - text_width) // 2
            y = height // 2 - (bbox[3] - bbox[1]) // 2

            draw.text((x, y), current_line.text, font=current_font, fill=self.template.highlight_color)

            # Next line: 50% transparent, pushed 200px lower
            next_index = current_index + 1
            if next_index < len(lyrics):
                next_line = lyrics[next_index]
                next_font = font

                bbox = draw.textbbox((0, 0), next_line.text, font=next_font)
                text_width = bbox[2] - bbox[0]
                x = (width - text_width) // 2
                y = height // 2 + 200

                # 50% transparent: convert RGB to RGBA with alpha = 128
                next_color = (*self.template.text_color, 128)
                draw.text((x, y), next_line.text, font=next_font, fill=next_color)

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

        # Collect all lyrics with global timing
        all_lyrics: list[GlobalLRCLine] = []

        for segment in audio_result.segments:
            lyrics = self._load_lrc(segment.item.recording_hash_prefix or "")
            if lyrics:
                for line in lyrics:
                    all_lyrics.append(GlobalLRCLine(
                        global_time_seconds=segment.start_time_seconds + line.time_seconds,
                        local_time_seconds=line.time_seconds,
                        text=line.text,
                        title=segment.item.song_title or "Unknown",
                    ))

        if not all_lyrics:
            # No lyrics - generate blank video
            return self._generate_blank_video(
                audio_result.output_path, output_path, fps=fps
            )

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

                # Update progress
                if progress_callback and frame % fps == 0:  # Update every second
                    progress_callback(frame, total_frames)

                # Render frame using global timing
                img = self._render_frame(all_lyrics, current_time)

                # Convert to bytes and write
                frame_bytes = img.tobytes()
                process.stdin.write(frame_bytes)

        finally:
            if process.stdin:
                process.stdin.close()
            process.wait()

        if progress_callback:
            progress_callback(total_frames, total_frames)

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
