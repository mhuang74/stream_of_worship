from __future__ import annotations

import logging
import math
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

from sow_render_worker.audio_engine import AudioSegmentInfo, get_audio_info
from sow_render_worker.chapters import Chapter, ChaptersManifest, chapters_to_ffmpeg_metadata
from sow_render_worker.frame_renderer import (
    VIDEO_TEMPLATES,
    FontSizePreset,
    FrameRenderer,
    SegmentInfo,
    TitleCardConfig,
    VideoTemplateName,
)
from sow_render_worker.lrc_parser import GlobalLRCLine, convert_to_global_timeline, parse_lrc

logger = logging.getLogger(__name__)


RESOLUTION_MAP: dict[str, tuple[int, int]] = {
    "720p": (1280, 720),
    "1080p": (1920, 1080),
}


@dataclass(frozen=True)
class VideoExportResult:
    output_path: str
    total_frames: int
    duration_seconds: float
    width: int
    height: int
    fps: int


@dataclass(frozen=True)
class ChapterInfo:
    position: int
    song_title: str
    start_seconds: float
    end_seconds: float
    lines: tuple[dict[str, Any], ...] = field(default_factory=tuple)


ProgressCallback = Callable[[int, int], None]
TimeoutCheckCallback = Callable[[], None]


class AssetFetcherProtocol(Protocol):
    def download_lrc(self, hash_prefix: str) -> str | None: ...

    def get_temp_dir(self) -> Path: ...


class VideoEngine:
    def __init__(
        self,
        asset_fetcher: AssetFetcherProtocol,
        template: VideoTemplateName = "dark",
        font_size_preset: FontSizePreset = "M",
        resolution: str = "1080p",
        fps: int = 24,
        include_title_card: bool = True,
        title_card_duration_seconds: float = 5.0,
        ffmpeg_path: str | None = None,
        ffprobe_path: str | None = None,
    ):
        self.asset_fetcher = asset_fetcher
        self.template = VIDEO_TEMPLATES.get(template, VIDEO_TEMPLATES["dark"])
        self.font_size_preset = font_size_preset
        self.resolution = RESOLUTION_MAP.get(resolution, (1920, 1080))
        self.fps = fps
        self.include_title_card = include_title_card
        self.title_card_duration_seconds = max(5.0, min(title_card_duration_seconds, 30.0))
        self.ffmpeg_path = ffmpeg_path or self._find_ffmpeg()
        self.ffprobe_path = ffprobe_path or "ffprobe"

        self.frame_renderer = FrameRenderer(
            template=self.template,
            font_size_preset=self.font_size_preset,
            resolution=self.resolution,
        )

    @staticmethod
    def _find_ffmpeg() -> str:
        found = shutil.which("ffmpeg")
        return found or "ffmpeg"

    def get_video_codec_args(self, bitrate: str = "8000k") -> list[str]:
        return [
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "23",
            "-b:v",
            bitrate,
        ]

    def generate_video(
        self,
        audio_path: str,
        segments: list[AudioSegmentInfo],
        output_path: str,
        progress_callback: ProgressCallback | None = None,
        timeout_check_callback: TimeoutCheckCallback | None = None,
        job_id: str | None = None,
    ) -> VideoExportResult:
        output_dir = Path(output_path).parent
        output_dir.mkdir(parents=True, exist_ok=True)

        audio_info = get_audio_info(audio_path)
        if not audio_info:
            raise ValueError("Could not get audio info")

        total_duration_seconds = audio_info["duration_seconds"]
        title_card_frames = (
            math.ceil(self.title_card_duration_seconds * self.fps)
            if self.include_title_card
            else 0
        )
        total_frames = math.ceil(total_duration_seconds * self.fps) + title_card_frames

        logger.info(
            "[%s] generate_video: duration=%.1fs, total_frames=%d, resolution=%s, fps=%d",
            job_id or "unknown", total_duration_seconds, total_frames,
            f"{self.resolution[0]}x{self.resolution[1]}", self.fps,
        )

        all_lyrics: list[GlobalLRCLine] = []
        chapters: list[ChapterInfo] = []

        for i, segment in enumerate(segments):
            hash_prefix = segment.item.recording_hash_prefix
            if not hash_prefix:
                continue

            try:
                lrc_content = self.asset_fetcher.download_lrc(hash_prefix)
            except Exception:
                lrc_content = None
            if not lrc_content:
                continue

            local_lyrics = parse_lrc(lrc_content)
            title = (
                segment.item.song_title
                or (str(segment.item.song_id) if segment.item.song_id else f"song-{i}")
            )
            global_lyrics = convert_to_global_timeline(
                local_lyrics, segment.start_time_seconds, title
            )
            all_lyrics.extend(global_lyrics)

            segment_end = segment.start_time_seconds + segment.duration_seconds
            chapters.append(
                ChapterInfo(
                    position=i + 1,
                    song_title=(
                        segment.item.song_title
                        or (str(segment.item.song_id) if segment.item.song_id else f"Song {i + 1}")
                    ),
                    start_seconds=segment.start_time_seconds,
                    end_seconds=segment_end,
                    lines=tuple(
                        {
                            "text": line.text,
                            "startSeconds": segment.start_time_seconds + line.time_seconds,
                        }
                        for line in local_lyrics
                    ),
                )
            )

        if not all_lyrics:
            return self.generate_blank_video(
                audio_path, output_path, total_duration_seconds,
                job_id=job_id,
            )

        segment_infos: list[SegmentInfo] = []
        for i, seg in enumerate(segments):
            segment_infos.append(
                SegmentInfo(
                    id=seg.item.id,
                    song_id=seg.item.song_id,
                    position=seg.item.position,
                    song_title=(
                        seg.item.song_title
                        or (str(seg.item.song_id) if seg.item.song_id else f"Song {i + 1}")
                    ),
                    start_time_seconds=seg.start_time_seconds,
                    duration_seconds=seg.duration_seconds,
                    tempo_bpm=seg.item.tempo_bpm,
                )
            )

        title_card_config: TitleCardConfig | None = None
        if self.include_title_card:
            title_card_config = TitleCardConfig(
                enabled=True,
                duration_seconds=total_duration_seconds,
                songset_name=(
                    (segments[0].item.song_title or "Worship Set") if segments else "Worship Set"
                ),
                song_count=len(segments),
                total_duration_seconds=total_duration_seconds,
            )

        self.encode_video_with_ffmpeg(
            audio_path,
            output_path,
            total_frames,
            total_duration_seconds,
            all_lyrics,
            segment_infos,
            progress_callback,
            title_card_config,
            timeout_check_callback,
            job_id=job_id,
        )

        logger.info(
            "[%s] generate_video: complete, %d frames encoded",
            job_id or "unknown", total_frames,
        )

        return VideoExportResult(
            output_path=output_path,
            total_frames=total_frames,
            duration_seconds=total_duration_seconds,
            width=self.resolution[0],
            height=self.resolution[1],
            fps=self.fps,
        )

    def encode_video_with_ffmpeg(
        self,
        audio_path: str,
        output_path: str,
        total_frames: int,
        total_duration_seconds: float,
        lyrics: list[GlobalLRCLine],
        segments: list[SegmentInfo],
        progress_callback: ProgressCallback | None = None,
        title_card_config: TitleCardConfig | None = None,
        timeout_check_callback: TimeoutCheckCallback | None = None,
        job_id: str | None = None,
    ) -> None:
        width, height = self.resolution

        ffmpeg_start = time.monotonic()
        logger.info(
            "[%s] encode_video_with_ffmpeg: starting FFmpeg pipe, %d frames (%.1fs at %dfps)",
            job_id or "unknown", total_frames, total_duration_seconds, self.fps,
        )

        args = [
            self.ffmpeg_path,
            "-y",
            "-f",
            "rawvideo",
            "-vcodec",
            "rawvideo",
            "-s",
            f"{width}x{height}",
            "-pix_fmt",
            "rgba",
            "-r",
            str(self.fps),
            "-i",
            "-",
            "-i",
            audio_path,
            *self.get_video_codec_args(),
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            output_path,
        ]

        process = subprocess.Popen(
            args,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )

        stderr_chunks: list[bytes] = []
        stderr_thread: threading.Thread | None = None
        if process.stderr:
            def _drain_stderr(pipe, chunks):
                try:
                    while True:
                        chunk = pipe.read(4096)
                        if not chunk:
                            break
                        chunks.append(chunk)
                except Exception:
                    pass

            stderr_thread = threading.Thread(target=_drain_stderr, args=(process.stderr, stderr_chunks), daemon=True)
            stderr_thread.start()

        title_card_frame_count = (
            math.ceil(self.title_card_duration_seconds * self.fps)
            if title_card_config and title_card_config.enabled
            else 0
        )

        title_card_bytes: bytes | None = None
        if title_card_config and title_card_frame_count > 0:
            title_card_img = self.frame_renderer.render_title_card(title_card_config)
            title_card_bytes = title_card_img.tobytes()

        frame_count = 0

        try:
            while frame_count < total_frames:
                if timeout_check_callback:
                    timeout_check_callback()

                if title_card_config and frame_count < title_card_frame_count:
                    frame_bytes = title_card_bytes
                else:
                    lyrics_frame_index = (
                        frame_count - title_card_frame_count if title_card_config else frame_count
                    )
                    current_time = lyrics_frame_index / self.fps
                    img = self.frame_renderer.render_frame(lyrics, segments, current_time)
                    frame_bytes = img.tobytes()

                try:
                    process.stdin.write(frame_bytes)
                except BrokenPipeError:
                    logger.error(
                        "[%s] FFmpeg pipe broken at frame %d/%d",
                        job_id or "unknown", frame_count, total_frames,
                    )
                    process.stdin.close()
                    if stderr_thread:
                        stderr_thread.join(timeout=5)
                    process.wait()
                    stderr_output = b"".join(stderr_chunks).decode("utf-8", errors="replace")
                    stderr_info = (
                        f"\nFFmpeg stderr (last 2000 chars): {stderr_output[-2000:]}"
                        if stderr_output
                        else ""
                    )
                    raise RuntimeError(
                        f"FFmpeg process closed prematurely (EPIPE on stdin write).{stderr_info}"
                    )

                frame_count += 1

                if progress_callback and frame_count % self.fps == 0:
                    progress_callback(frame_count, total_frames)

                if frame_count % (self.fps * 30) == 0 and frame_count > 0:
                    video_seconds = frame_count / self.fps
                    logger.info(
                        "[%s] Video encoding progress: %.0fs/%.0fs (%d/%d frames, %.1f%%)",
                        job_id or "unknown", video_seconds, total_duration_seconds,
                        frame_count, total_frames,
                        frame_count / total_frames * 100 if total_frames > 0 else 0,
                    )

            try:
                process.stdin.close()
            except BrokenPipeError:
                pass
        except Exception:
            process.kill()
            if stderr_thread:
                stderr_thread.join(timeout=5)
            process.wait()
            raise

        if stderr_thread:
            stderr_thread.join(timeout=5)
        return_code = process.wait()
        ffmpeg_elapsed = time.monotonic() - ffmpeg_start
        logger.info(
            "[%s] FFmpeg process exited with code %d in %.1fs",
            job_id or "unknown", return_code, ffmpeg_elapsed,
        )
        stderr_output = b"".join(stderr_chunks).decode("utf-8", errors="replace")
        if return_code != 0:
            stderr_info = (
                f"\nFFmpeg stderr (last 2000 chars): {stderr_output[-2000:]}"
                if stderr_output
                else ""
            )
            raise RuntimeError(f"FFmpeg exited with code {return_code}.{stderr_info}")

        if progress_callback:
            progress_callback(total_frames, total_frames)

    def generate_blank_video(
        self,
        audio_path: str,
        output_path: str,
        duration_seconds: float,
        job_id: str | None = None,
    ) -> VideoExportResult:
        width, height = self.resolution
        bg_r, bg_g, bg_b = self.template.background_color
        hex_color = f"#{bg_r:02x}{bg_g:02x}{bg_b:02x}"

        logger.info(
            "[%s] generate_blank_video: %.1fs, %s",
            job_id or "unknown", duration_seconds, output_path,
        )

        args = [
            self.ffmpeg_path,
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c={hex_color}:s={width}x{height}:d={duration_seconds}",
            "-i",
            audio_path,
            *self.get_video_codec_args("5000k"),
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            output_path,
        ]

        result = subprocess.run(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        if result.returncode != 0:
            stderr_output = result.stderr.decode("utf-8", errors="replace")
            stderr_info = (
                f"\nFFmpeg stderr (last 2000 chars): {stderr_output[-2000:]}"
                if stderr_output
                else ""
            )
            raise RuntimeError(f"FFmpeg exited with code {result.returncode}.{stderr_info}")

        return VideoExportResult(
            output_path=output_path,
            total_frames=math.ceil(duration_seconds * self.fps),
            duration_seconds=duration_seconds,
            width=width,
            height=height,
            fps=self.fps,
        )

    def inject_chapters(
        self,
        video_path: str,
        chapters: list[ChapterInfo],
        job_id: str | None = None,
    ) -> bool:
        logger.info(
            "[%s] inject_chapters: %d chapters into %s",
            job_id or "unknown", len(chapters), video_path,
        )
        try:
            temp_dir = self.asset_fetcher.get_temp_dir()
            chapters_path = str(Path(temp_dir) / f"chapters-{int(time.time() * 1000)}.txt")

            manifest = ChaptersManifest(
                chapters=tuple(
                    Chapter(
                        position=ch.position,
                        song_title=ch.song_title,
                        start_seconds=ch.start_seconds,
                        end_seconds=ch.end_seconds,
                    )
                    for ch in chapters
                ),
                total_duration_seconds=0,
                generated_at="",
            )
            chapters_content = chapters_to_ffmpeg_metadata(manifest)
            Path(chapters_path).write_text(chapters_content, encoding="utf-8")

            output_path = f"{video_path}.chapters.mp4"

            args = [
                self.ffmpeg_path,
                "-y",
                "-i",
                video_path,
                "-i",
                chapters_path,
                "-map_metadata",
                "1",
                "-c",
                "copy",
                output_path,
            ]

            result = subprocess.run(
                args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )

            if result.returncode != 0:
                logger.warning("[%s] inject_chapters: failed", job_id or "unknown")
                return False

            shutil.move(output_path, video_path)

            try:
                Path(chapters_path).unlink()
            except OSError:
                pass

            logger.info("[%s] inject_chapters: complete", job_id or "unknown")
            return True
        except Exception:
            logger.warning("[%s] inject_chapters: failed", job_id or "unknown")
            return False
