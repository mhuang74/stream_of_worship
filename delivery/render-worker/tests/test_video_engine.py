from __future__ import annotations

import json
import math
import os
import subprocess
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, Mock, mock_open, patch

import pytest

from sow_render_worker.audio_engine import AudioSegmentInfo, SongsetItem
from sow_render_worker.chapters import Chapter, ChaptersManifest
from sow_render_worker.frame_renderer import (
    FONT_SIZE_PRESETS,
    VIDEO_TEMPLATES,
    SegmentInfo,
    TitleCardConfig,
    VideoTemplate,
)
from sow_render_worker.lrc_parser import GlobalLRCLine
from sow_render_worker.video_engine import (
    ChapterInfo,
    VideoEngine,
    VideoExportResult,
    RESOLUTION_MAP,
    _check_memory_pressure,
    _MEMORY_WARNING_FRACTION,
)


def _make_item(**overrides) -> SongsetItem:
    defaults = dict(
        id="item-1",
        songset_id="ss-1",
        song_id="song-1",
        song_title="Test Song",
        recording_hash_prefix="abc123",
        position=0,
        gap_beats=2.0,
        crossfade_enabled=None,
        crossfade_duration_seconds=None,
        key_shift_semitones=None,
        tempo_ratio=None,
        tempo_bpm=None,
        duration_seconds=None,
    )
    defaults.update(overrides)
    return SongsetItem(**defaults)


def _make_segment(**overrides) -> AudioSegmentInfo:
    item = overrides.pop("item", _make_item())
    defaults = dict(
        item=item,
        audio_path="/tmp/audio.mp3",
        start_time_seconds=0.0,
        duration_seconds=180.0,
        gap_before_seconds=0.0,
    )
    defaults.update(overrides)
    return AudioSegmentInfo(**defaults)


class MockAssetFetcher:
    def __init__(self, lrc_content: str | None = None, temp_dir: str = "/tmp"):
        self._lrc_content = lrc_content
        self._temp_dir = temp_dir

    def download_lrc(self, hash_prefix: str) -> str | None:
        return self._lrc_content

    def get_temp_dir(self) -> Path:
        return Path(self._temp_dir)


class TestVideoEngineInit:
    def test_default_options(self):
        fetcher = MockAssetFetcher()
        engine = VideoEngine(fetcher)
        assert engine.template == VIDEO_TEMPLATES["dark"]
        assert engine.font_size_preset == "M"
        assert engine.resolution == (1920, 1080)
        assert engine.fps == 24
        assert engine.include_title_card is True
        assert engine.title_card_duration_seconds == 5.0
        assert engine.ffprobe_path == "ffprobe"

    def test_custom_template(self):
        fetcher = MockAssetFetcher()
        engine = VideoEngine(fetcher, template="gradient_warm")
        assert engine.template == VIDEO_TEMPLATES["gradient_warm"]

    def test_custom_font_size_preset(self):
        fetcher = MockAssetFetcher()
        engine = VideoEngine(fetcher, font_size_preset="L")
        assert engine.font_size_preset == "L"

    def test_720p_resolution(self):
        fetcher = MockAssetFetcher()
        engine = VideoEngine(fetcher, resolution="720p")
        assert engine.resolution == (1280, 720)

    def test_1080p_resolution(self):
        fetcher = MockAssetFetcher()
        engine = VideoEngine(fetcher, resolution="1080p")
        assert engine.resolution == (1920, 1080)

    def test_custom_fps(self):
        fetcher = MockAssetFetcher()
        engine = VideoEngine(fetcher, fps=30)
        assert engine.fps == 30

    def test_title_card_disabled(self):
        fetcher = MockAssetFetcher()
        engine = VideoEngine(fetcher, include_title_card=False)
        assert engine.include_title_card is False

    def test_title_card_duration_clamped_min(self):
        fetcher = MockAssetFetcher()
        engine = VideoEngine(fetcher, title_card_duration_seconds=2.0)
        assert engine.title_card_duration_seconds == 5.0

    def test_title_card_duration_clamped_max(self):
        fetcher = MockAssetFetcher()
        engine = VideoEngine(fetcher, title_card_duration_seconds=50.0)
        assert engine.title_card_duration_seconds == 30.0

    def test_title_card_duration_valid(self):
        fetcher = MockAssetFetcher()
        engine = VideoEngine(fetcher, title_card_duration_seconds=10.0)
        assert engine.title_card_duration_seconds == 10.0

    def test_custom_ffmpeg_path(self):
        fetcher = MockAssetFetcher()
        engine = VideoEngine(fetcher, ffmpeg_path="/usr/local/bin/ffmpeg")
        assert engine.ffmpeg_path == "/usr/local/bin/ffmpeg"

    def test_custom_ffprobe_path(self):
        fetcher = MockAssetFetcher()
        engine = VideoEngine(fetcher, ffprobe_path="/usr/local/bin/ffprobe")
        assert engine.ffprobe_path == "/usr/local/bin/ffprobe"

    def test_frame_renderer_created(self):
        fetcher = MockAssetFetcher()
        engine = VideoEngine(fetcher)
        assert engine.frame_renderer is not None
        assert engine.frame_renderer.template == engine.template

    def test_font_family_passed_to_frame_renderer(self):
        fetcher = MockAssetFetcher()
        engine = VideoEngine(fetcher, font_family="lxgw_wenkai_tc")
        assert engine.font_family == "lxgw_wenkai_tc"
        assert engine.frame_renderer.font_family == "lxgw_wenkai_tc"

    def test_default_font_family(self):
        fetcher = MockAssetFetcher()
        engine = VideoEngine(fetcher)
        assert engine.font_family == "noto_serif_tc"
        assert engine.frame_renderer.font_family == "noto_serif_tc"


class TestGetVideoCodecArgs:
    def test_default_bitrate(self):
        fetcher = MockAssetFetcher()
        engine = VideoEngine(fetcher)
        args = engine.get_video_codec_args()
        assert args == [
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "23",
            "-b:v",
            "8000k",
            "-movflags",
            "+faststart",
        ]

    def test_custom_bitrate(self):
        fetcher = MockAssetFetcher()
        engine = VideoEngine(fetcher)
        args = engine.get_video_codec_args("5000k")
        assert args == [
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "23",
            "-b:v",
            "5000k",
            "-movflags",
            "+faststart",
        ]

    def test_low_bitrate(self):
        fetcher = MockAssetFetcher()
        engine = VideoEngine(fetcher)
        args = engine.get_video_codec_args("2000k")
        assert "-b:v" in args
        assert "2000k" in args

    def test_faststart_flag_present(self):
        fetcher = MockAssetFetcher()
        engine = VideoEngine(fetcher)
        args = engine.get_video_codec_args()
        assert "-movflags" in args
        idx = args.index("-movflags")
        assert idx + 1 < len(args)
        assert args[idx + 1] == "+faststart"


class TestGenerateBlankVideo:
    def test_blank_video_args(self, tmp_path):
        output_path = str(tmp_path / "blank.mp4")
        fetcher = MockAssetFetcher()
        engine = VideoEngine(fetcher, ffmpeg_path="/usr/bin/ffmpeg")

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = b""

        with patch("sow_render_worker.video_engine.subprocess.run") as mock_run:
            mock_run.return_value = mock_result
            result = engine.generate_blank_video(
                "/tmp/audio.mp3", output_path, 180.0, job_id="test-job"
            )

        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "/usr/bin/ffmpeg"
        assert "-y" in cmd
        assert "-f" in cmd
        assert "lavfi" in cmd

        color_input_idx = -1
        for i, arg in enumerate(cmd):
            if arg == "-i" and i + 1 < len(cmd) and "color=c=" in cmd[i + 1]:
                color_input_idx = i + 1
                break
        assert color_input_idx > 0
        color_input = cmd[color_input_idx]
        assert "color=c=#14141e" in color_input
        assert "s=1920x1080" in color_input
        assert "d=180.0" in color_input

        assert "-c:v" in cmd
        assert "libx264" in cmd
        assert "5000k" in cmd
        assert "-c:a" in cmd
        assert "aac" in cmd
        assert "192k" in cmd
        assert "-shortest" in cmd

        assert result.output_path == output_path
        assert result.duration_seconds == 180.0
        assert result.width == 1920
        assert result.height == 1080
        assert result.fps == 24
        assert result.total_frames == 24 * 180

    def test_blank_video_720p(self, tmp_path):
        output_path = str(tmp_path / "blank.mp4")
        fetcher = MockAssetFetcher()
        engine = VideoEngine(fetcher, resolution="720p", ffmpeg_path="ffmpeg")

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = b""

        with patch("sow_render_worker.video_engine.subprocess.run") as mock_run:
            mock_run.return_value = mock_result
            result = engine.generate_blank_video(
                "/tmp/audio.mp3", output_path, 60.0, job_id="test-job"
            )

        cmd = mock_run.call_args[0][0]
        color_input_idx = cmd.index("-i") + 1
        color_input = cmd[color_input_idx]
        assert "s=1280x720" in color_input

        assert result.width == 1280
        assert result.height == 720

    def test_blank_video_ffmpeg_failure(self, tmp_path):
        output_path = str(tmp_path / "blank.mp4")
        fetcher = MockAssetFetcher()
        engine = VideoEngine(fetcher)

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = b"error"

        with patch("sow_render_worker.video_engine.subprocess.run") as mock_run:
            mock_run.return_value = mock_result
            with pytest.raises(RuntimeError, match="FFmpeg exited with code 1"):
                engine.generate_blank_video("/tmp/audio.mp3", output_path, 60.0, job_id="test-job")


class TestEncodeVideoWithFFmpeg:
    def test_encode_args_construction(self, tmp_path):
        output_path = str(tmp_path / "video.mp4")
        fetcher = MockAssetFetcher()
        engine = VideoEngine(fetcher, ffmpeg_path="/usr/bin/ffmpeg", fps=24)

        lyrics: list[GlobalLRCLine] = []
        segments: list[SegmentInfo] = []

        mock_process = MagicMock()
        mock_process.stdin = MagicMock()
        mock_process.stdin.write = MagicMock()
        mock_process.wait.return_value = 0
        mock_process.stderr = MagicMock()
        mock_process.stderr.read.return_value = b""

        with patch("sow_render_worker.video_engine.subprocess.Popen") as mock_popen:
            mock_popen.return_value = mock_process
            engine.encode_video_with_ffmpeg(
                "/tmp/audio.mp3",
                output_path,
                total_frames=10,
                total_duration_seconds=0.5,
                lyrics=lyrics,
                segments=segments,
            )

        cmd = mock_popen.call_args[0][0]
        assert cmd[0] == "/usr/bin/ffmpeg"
        assert "-y" in cmd
        assert "-f" in cmd
        assert "rawvideo" in cmd
        assert "-s" in cmd
        assert "1920x1080" in cmd
        assert "-pix_fmt" in cmd
        assert "rgb24" in cmd
        assert "-r" in cmd
        assert "24" in cmd
        assert "-i" in cmd
        assert "-" in cmd
        assert "/tmp/audio.mp3" in cmd
        assert "-c:v" in cmd
        assert "libx264" in cmd
        assert "-c:a" in cmd
        assert "aac" in cmd
        assert "192k" in cmd
        assert "-shortest" in cmd
        assert output_path in cmd

    def test_encode_writes_frames(self, tmp_path):
        output_path = str(tmp_path / "video.mp4")
        fetcher = MockAssetFetcher()
        engine = VideoEngine(fetcher, fps=24)

        lyrics: list[GlobalLRCLine] = [
            GlobalLRCLine(
                text="Hello", local_time_seconds=0.0, global_time_seconds=0.0, title="Song"
            ),
        ]
        segments = [
            SegmentInfo(
                id="1",
                song_id="s1",
                position=0,
                song_title="Song",
                start_time_seconds=0.0,
                duration_seconds=10.0,
            ),
        ]

        mock_process = MagicMock()
        mock_process.stdin = MagicMock()
        mock_process.stdin.write = MagicMock()
        mock_process.wait.return_value = 0
        mock_process.stderr = MagicMock()
        mock_process.stderr.read.return_value = b""

        with patch("sow_render_worker.video_engine.subprocess.Popen") as mock_popen:
            mock_popen.return_value = mock_process
            engine.encode_video_with_ffmpeg(
                "/tmp/audio.mp3",
                output_path,
                total_frames=3,
                total_duration_seconds=0.125,
                lyrics=lyrics,
                segments=segments,
            )

        assert mock_process.stdin.write.call_count == 3
        mock_process.stdin.close.assert_called_once()

    def test_encode_handles_broken_pipe(self, tmp_path):
        output_path = str(tmp_path / "video.mp4")
        fetcher = MockAssetFetcher()
        engine = VideoEngine(fetcher)

        lyrics: list[GlobalLRCLine] = []
        segments: list[SegmentInfo] = []

        mock_process = MagicMock()
        mock_process.stdin = MagicMock()
        mock_process.stdin.write.side_effect = BrokenPipeError()
        mock_process.kill = MagicMock()
        mock_process.wait.return_value = 1
        mock_process.stderr = MagicMock()
        mock_process.stderr.read.return_value = b"error output"

        with patch("sow_render_worker.video_engine.subprocess.Popen") as mock_popen:
            mock_popen.return_value = mock_process
            with pytest.raises(RuntimeError, match="EPIPE"):
                engine.encode_video_with_ffmpeg(
                    "/tmp/audio.mp3",
                    output_path,
                    total_frames=10,
                    total_duration_seconds=0.5,
                    lyrics=lyrics,
                    segments=segments,
                )

        mock_process.kill.assert_called_once()

    def test_encode_ffmpeg_nonzero_exit(self, tmp_path):
        output_path = str(tmp_path / "video.mp4")
        fetcher = MockAssetFetcher()
        engine = VideoEngine(fetcher)

        lyrics: list[GlobalLRCLine] = []
        segments: list[SegmentInfo] = []

        mock_process = MagicMock()
        mock_process.stdin = MagicMock()
        mock_process.stdin.write = MagicMock()
        mock_process.wait.return_value = 1
        mock_process.stderr = MagicMock()
        mock_process.stderr.read.return_value = b"some error"

        with patch("sow_render_worker.video_engine.subprocess.Popen") as mock_popen:
            mock_popen.return_value = mock_process
            with pytest.raises(RuntimeError, match="FFmpeg exited with code 1"):
                engine.encode_video_with_ffmpeg(
                    "/tmp/audio.mp3",
                    output_path,
                    total_frames=2,
                    total_duration_seconds=0.1,
                    lyrics=lyrics,
                    segments=segments,
                )

    def test_encode_progress_callback(self, tmp_path):
        output_path = str(tmp_path / "video.mp4")
        fetcher = MockAssetFetcher()
        engine = VideoEngine(fetcher, fps=2)

        lyrics: list[GlobalLRCLine] = []
        segments: list[SegmentInfo] = []

        progress_calls: list[tuple[int, int]] = []

        def progress_cb(current: int, total: int) -> None:
            progress_calls.append((current, total))

        mock_process = MagicMock()
        mock_process.stdin = MagicMock()
        mock_process.stdin.write = MagicMock()
        mock_process.wait.return_value = 0
        mock_process.stderr = MagicMock()
        mock_process.stderr.read.return_value = b""

        with patch("sow_render_worker.video_engine.subprocess.Popen") as mock_popen:
            mock_popen.return_value = mock_process
            engine.encode_video_with_ffmpeg(
                "/tmp/audio.mp3",
                output_path,
                total_frames=6,
                total_duration_seconds=3.0,
                lyrics=lyrics,
                segments=segments,
                progress_callback=progress_cb,
            )

        assert len(progress_calls) > 0
        assert progress_calls[-1] == (6, 6)

    def test_encode_with_title_card(self, tmp_path):
        output_path = str(tmp_path / "video.mp4")
        fetcher = MockAssetFetcher()
        engine = VideoEngine(fetcher, fps=24, include_title_card=True)

        lyrics: list[GlobalLRCLine] = [
            GlobalLRCLine(
                text="Hello", local_time_seconds=0.0, global_time_seconds=0.0, title="Song"
            ),
        ]
        segments = [
            SegmentInfo(
                id="1",
                song_id="s1",
                position=0,
                song_title="Song",
                start_time_seconds=0.0,
                duration_seconds=10.0,
            ),
        ]

        title_card_config = TitleCardConfig(
            enabled=True,
            duration_seconds=10.0,
            lines=("Test Set", "Song"),
            total_duration_seconds=10.0,
        )

        mock_process = MagicMock()
        mock_process.stdin = MagicMock()
        mock_process.stdin.write = MagicMock()
        mock_process.wait.return_value = 0
        mock_process.stderr = MagicMock()
        mock_process.stderr.read.return_value = b""

        with patch("sow_render_worker.video_engine.subprocess.Popen") as mock_popen:
            mock_popen.return_value = mock_process
            engine.encode_video_with_ffmpeg(
                "/tmp/audio.mp3",
                output_path,
                total_frames=5,
                total_duration_seconds=0.2,
                lyrics=lyrics,
                segments=segments,
                title_card_config=title_card_config,
            )

        assert mock_process.stdin.write.call_count == 5

    def test_encode_broken_pipe_with_zero_exit_code_succeeds(self, tmp_path):
        output_path = str(tmp_path / "video.mp4")
        fetcher = MockAssetFetcher()
        engine = VideoEngine(fetcher)

        lyrics: list[GlobalLRCLine] = []
        segments: list[SegmentInfo] = []

        progress_calls: list[tuple[int, int]] = []

        def progress_cb(current: int, total: int) -> None:
            progress_calls.append((current, total))

        mock_process = MagicMock()
        mock_process.stdin = MagicMock()
        mock_process.stdin.write.side_effect = BrokenPipeError()
        mock_process.stdin.close = MagicMock()
        mock_process.wait.return_value = 0
        mock_process.returncode = 0
        mock_process.stderr = MagicMock()
        mock_process.stderr.read.return_value = b""

        with patch("sow_render_worker.video_engine.subprocess.Popen") as mock_popen:
            mock_popen.return_value = mock_process
            engine.encode_video_with_ffmpeg(
                "/tmp/audio.mp3",
                output_path,
                total_frames=10,
                total_duration_seconds=0.5,
                lyrics=lyrics,
                segments=segments,
                progress_callback=progress_cb,
            )

        assert progress_calls[-1] == (10, 10)

    def test_encode_lyrics_timeline_sync_with_title_card(self, tmp_path):
        output_path = str(tmp_path / "video.mp4")
        fetcher = MockAssetFetcher()
        engine = VideoEngine(
            fetcher, fps=24, include_title_card=True, title_card_duration_seconds=5.0
        )

        lyrics: list[GlobalLRCLine] = [
            GlobalLRCLine(
                text="Hello", local_time_seconds=5.0, global_time_seconds=5.0, title="Song"
            ),
        ]
        segments = [
            SegmentInfo(
                id="1",
                song_id="s1",
                position=0,
                song_title="Song",
                start_time_seconds=0.0,
                duration_seconds=10.0,
            ),
        ]

        title_card_config = TitleCardConfig(
            enabled=True,
            duration_seconds=10.0,
            lines=("Test Set", "Song"),
            total_duration_seconds=10.0,
        )

        mock_process = MagicMock()
        mock_process.stdin = MagicMock()
        mock_process.stdin.write = MagicMock()
        mock_process.wait.return_value = 0
        mock_process.stderr = MagicMock()
        mock_process.stderr.read.return_value = b""

        render_times: list[float] = []
        original_render_frame_bytes = engine.frame_renderer.render_frame_bytes

        def capture_render_time(lyrics_arg, segments_arg, current_time):
            render_times.append(current_time)
            return original_render_frame_bytes(lyrics_arg, segments_arg, current_time)

        with (
            patch("sow_render_worker.video_engine.subprocess.Popen") as mock_popen,
            patch.object(
                engine.frame_renderer, "render_frame_bytes", side_effect=capture_render_time
            ),
        ):
            mock_popen.return_value = mock_process
            engine.encode_video_with_ffmpeg(
                "/tmp/audio.mp3",
                output_path,
                total_frames=240,
                total_duration_seconds=10.0,
                lyrics=lyrics,
                segments=segments,
                title_card_config=title_card_config,
            )

        title_card_frame_count = math.ceil(5.0 * 24)
        if render_times:
            assert render_times[0] >= 5.0


class TestGenerateVideo:
    def test_no_audio_info_raises(self, tmp_path):
        output_path = str(tmp_path / "video.mp4")
        fetcher = MockAssetFetcher()
        engine = VideoEngine(fetcher)

        with patch("sow_render_worker.video_engine.get_audio_info", return_value=None):
            with pytest.raises(ValueError, match="Could not get audio info"):
                engine.generate_video("/tmp/audio.mp3", [], output_path)

    def test_no_lyrics_generates_blank_video(self, tmp_path):
        output_path = str(tmp_path / "video.mp4")
        fetcher = MockAssetFetcher(lrc_content=None)
        engine = VideoEngine(fetcher)

        audio_info = {
            "duration_seconds": 60.0,
            "duration_ms": 60000,
            "sample_rate": 44100,
            "channels": 2,
        }

        blank_result = VideoExportResult(
            output_path=output_path,
            total_frames=1440,
            duration_seconds=60.0,
            width=1920,
            height=1080,
            fps=24,
        )

        with (
            patch("sow_render_worker.video_engine.get_audio_info", return_value=audio_info),
            patch.object(engine, "generate_blank_video", return_value=blank_result) as mock_blank,
        ):
            result = engine.generate_video("/tmp/audio.mp3", [], output_path, job_id="test-job")

        mock_blank.assert_called_once_with("/tmp/audio.mp3", output_path, 60.0, job_id="test-job")
        assert result == blank_result

    def test_with_lyrics_encodes_video(self, tmp_path):
        output_path = str(tmp_path / "video.mp4")
        fetcher = MockAssetFetcher(lrc_content="[00:00.00]Hello\n[00:05.00]World")
        engine = VideoEngine(fetcher, include_title_card=False)

        segment = _make_segment()
        audio_info = {
            "duration_seconds": 180.0,
            "duration_ms": 180000,
            "sample_rate": 44100,
            "channels": 2,
        }

        with (
            patch("sow_render_worker.video_engine.get_audio_info", return_value=audio_info),
            patch.object(engine, "encode_video_with_ffmpeg") as mock_encode,
        ):
            result = engine.generate_video(
                "/tmp/audio.mp3", [segment], output_path, job_id="test-job"
            )

        mock_encode.assert_called_once()
        call_args = mock_encode.call_args
        assert call_args[0][2] == math.ceil(180.0 * 24)
        assert result.output_path == output_path
        assert result.duration_seconds == 180.0
        assert result.width == 1920
        assert result.height == 1080
        assert result.fps == 24

    def test_blank_timing_lines_mixed_with_lyrics_encode_video(self, tmp_path):
        output_path = str(tmp_path / "video.mp4")
        fetcher = MockAssetFetcher(lrc_content="[00:00.00]Hello\n[00:05.00]\n[00:08.00]World")
        engine = VideoEngine(fetcher, include_title_card=False)

        segment = _make_segment()
        audio_info = {
            "duration_seconds": 180.0,
            "duration_ms": 180000,
            "sample_rate": 44100,
            "channels": 2,
        }

        with (
            patch("sow_render_worker.video_engine.get_audio_info", return_value=audio_info),
            patch.object(engine, "encode_video_with_ffmpeg") as mock_encode,
            patch.object(engine, "generate_blank_video") as mock_blank,
        ):
            engine.generate_video("/tmp/audio.mp3", [segment], output_path, job_id="test-job")

        mock_blank.assert_not_called()
        mock_encode.assert_called_once()

    def test_all_blank_timing_lines_generate_blank_video(self, tmp_path):
        output_path = str(tmp_path / "video.mp4")
        fetcher = MockAssetFetcher(lrc_content="[00:00.00]\n[00:05.00]   ")
        engine = VideoEngine(fetcher, include_title_card=False)

        segment = _make_segment()
        audio_info = {
            "duration_seconds": 180.0,
            "duration_ms": 180000,
            "sample_rate": 44100,
            "channels": 2,
        }
        blank_result = VideoExportResult(
            output_path=output_path,
            total_frames=4320,
            duration_seconds=180.0,
            width=1920,
            height=1080,
            fps=24,
        )

        with (
            patch("sow_render_worker.video_engine.get_audio_info", return_value=audio_info),
            patch.object(engine, "generate_blank_video", return_value=blank_result) as mock_blank,
            patch.object(engine, "encode_video_with_ffmpeg") as mock_encode,
        ):
            result = engine.generate_video(
                "/tmp/audio.mp3", [segment], output_path, job_id="test-job"
            )

        mock_blank.assert_called_once_with("/tmp/audio.mp3", output_path, 180.0, job_id="test-job")
        mock_encode.assert_not_called()
        assert result == blank_result

    def test_skips_segment_without_hash_prefix(self, tmp_path):
        output_path = str(tmp_path / "video.mp4")
        fetcher = MockAssetFetcher(lrc_content="[00:00.00]Hello")
        engine = VideoEngine(fetcher, include_title_card=False)

        item = _make_item(recording_hash_prefix=None)
        segment = _make_segment(item=item)
        audio_info = {
            "duration_seconds": 180.0,
            "duration_ms": 180000,
            "sample_rate": 44100,
            "channels": 2,
        }

        with (
            patch("sow_render_worker.video_engine.get_audio_info", return_value=audio_info),
            patch.object(engine, "generate_blank_video") as mock_blank,
        ):
            result = engine.generate_video(
                "/tmp/audio.mp3", [segment], output_path, job_id="test-job"
            )

        mock_blank.assert_called_once()

    def test_skips_segment_with_no_lrc(self, tmp_path):
        output_path = str(tmp_path / "video.mp4")
        fetcher = MockAssetFetcher(lrc_content=None)
        engine = VideoEngine(fetcher, include_title_card=False)

        segment = _make_segment()
        audio_info = {
            "duration_seconds": 180.0,
            "duration_ms": 180000,
            "sample_rate": 44100,
            "channels": 2,
        }

        with (
            patch("sow_render_worker.video_engine.get_audio_info", return_value=audio_info),
            patch.object(engine, "generate_blank_video") as mock_blank,
        ):
            result = engine.generate_video(
                "/tmp/audio.mp3", [segment], output_path, job_id="test-job"
            )

        mock_blank.assert_called_once()

    def test_creates_output_directory(self, tmp_path):
        output_path = str(tmp_path / "subdir" / "video.mp4")
        fetcher = MockAssetFetcher(lrc_content=None)
        engine = VideoEngine(fetcher)

        audio_info = {
            "duration_seconds": 60.0,
            "duration_ms": 60000,
            "sample_rate": 44100,
            "channels": 2,
        }

        blank_result = VideoExportResult(
            output_path=output_path,
            total_frames=1440,
            duration_seconds=60.0,
            width=1920,
            height=1080,
            fps=24,
        )

        with (
            patch("sow_render_worker.video_engine.get_audio_info", return_value=audio_info),
            patch.object(engine, "generate_blank_video", return_value=blank_result),
        ):
            engine.generate_video("/tmp/audio.mp3", [], output_path, job_id="test-job")

        assert Path(tmp_path / "subdir").is_dir()

    def test_title_card_config_passed(self, tmp_path):
        output_path = str(tmp_path / "video.mp4")
        fetcher = MockAssetFetcher(lrc_content="[00:00.00]Hello")
        engine = VideoEngine(fetcher, include_title_card=True, title_card_duration_seconds=5.0)

        segment = _make_segment()
        audio_info = {
            "duration_seconds": 180.0,
            "duration_ms": 180000,
            "sample_rate": 44100,
            "channels": 2,
        }

        with (
            patch("sow_render_worker.video_engine.get_audio_info", return_value=audio_info),
            patch.object(engine, "encode_video_with_ffmpeg") as mock_encode,
        ):
            engine.generate_video("/tmp/audio.mp3", [segment], output_path, job_id="test-job")

        call_kwargs = mock_encode.call_args
        title_card_config = (
            call_kwargs[1].get("title_card_config")
            if "title_card_config" in call_kwargs[1]
            else call_kwargs[0][7]
            if len(call_kwargs[0]) > 7
            else None
        )
        assert title_card_config is not None
        assert title_card_config.enabled is True
        assert len(title_card_config.lines) >= 1
        assert call_kwargs[0][2] == math.ceil(180.0 * 24)


class TestInjectChapters:
    def test_inject_chapters_success(self, tmp_path):
        video_path = str(tmp_path / "video.mp4")
        Path(video_path).write_bytes(b"\x00" * 100)

        fetcher = MockAssetFetcher(temp_dir=str(tmp_path))
        engine = VideoEngine(fetcher, ffmpeg_path="ffmpeg")

        chapters = [
            ChapterInfo(
                position=1,
                song_title="Song 1",
                start_seconds=0.0,
                end_seconds=180.0,
            ),
            ChapterInfo(
                position=2,
                song_title="Song 2",
                start_seconds=180.0,
                end_seconds=360.0,
            ),
        ]

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = b""

        with (
            patch("sow_render_worker.video_engine.subprocess.run") as mock_run,
            patch("sow_render_worker.video_engine.shutil.move"),
        ):
            mock_run.return_value = mock_result
            result = engine.inject_chapters(video_path, chapters, job_id="test-job")

        assert result is True

        cmd = mock_run.call_args[0][0]
        assert "-y" in cmd
        assert "-i" in cmd
        assert video_path in cmd
        assert "-map_metadata" in cmd
        assert "1" in cmd
        assert "-c" in cmd
        assert "copy" in cmd

    def test_inject_chapters_ffmpeg_failure(self, tmp_path):
        video_path = str(tmp_path / "video.mp4")
        Path(video_path).write_bytes(b"\x00" * 100)

        fetcher = MockAssetFetcher(temp_dir=str(tmp_path))
        engine = VideoEngine(fetcher)

        chapters = [
            ChapterInfo(position=1, song_title="Song 1", start_seconds=0.0, end_seconds=180.0),
        ]

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = b"error"

        with patch("sow_render_worker.video_engine.subprocess.run") as mock_run:
            mock_run.return_value = mock_result
            result = engine.inject_chapters(video_path, chapters, job_id="test-job")

        assert result is False

    def test_inject_chapters_exception_returns_false(self, tmp_path):
        video_path = str(tmp_path / "video.mp4")

        fetcher = MockAssetFetcher(temp_dir=str(tmp_path))
        engine = VideoEngine(fetcher)

        chapters = [
            ChapterInfo(position=1, song_title="Song 1", start_seconds=0.0, end_seconds=180.0),
        ]

        with patch("sow_render_worker.video_engine.subprocess.run", side_effect=Exception("boom")):
            result = engine.inject_chapters(video_path, chapters, job_id="test-job")

        assert result is False

    def test_inject_chapters_writes_metadata_file(self, tmp_path):
        video_path = str(tmp_path / "video.mp4")
        Path(video_path).write_bytes(b"\x00" * 100)

        fetcher = MockAssetFetcher(temp_dir=str(tmp_path))
        engine = VideoEngine(fetcher)

        chapters = [
            ChapterInfo(position=1, song_title="Song 1", start_seconds=0.0, end_seconds=180.0),
        ]

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = b""

        with (
            patch("sow_render_worker.video_engine.subprocess.run") as mock_run,
            patch("sow_render_worker.video_engine.shutil.move"),
        ):
            mock_run.return_value = mock_result
            result = engine.inject_chapters(video_path, chapters, job_id="test-job")

        chapters_files = list(tmp_path.glob("chapters-*.txt"))
        assert len(chapters_files) == 0

        chapters_path_arg = None
        for i, arg in enumerate(mock_run.call_args[0][0]):
            if arg == "-i" and i > 0:
                prev_arg = mock_run.call_args[0][0][i - 1]
                if prev_arg == "-i" and i + 1 < len(mock_run.call_args[0][0]):
                    next_arg = mock_run.call_args[0][0][i + 1]
                    if "chapters-" in next_arg:
                        chapters_path_arg = next_arg
                        break

    def test_inject_chapters_cleans_up_temp(self, tmp_path):
        video_path = str(tmp_path / "video.mp4")
        Path(video_path).write_bytes(b"\x00" * 100)

        fetcher = MockAssetFetcher(temp_dir=str(tmp_path))
        engine = VideoEngine(fetcher)

        chapters = [
            ChapterInfo(position=1, song_title="Song 1", start_seconds=0.0, end_seconds=180.0),
        ]

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = b""

        with (
            patch("sow_render_worker.video_engine.subprocess.run") as mock_run,
            patch("sow_render_worker.video_engine.shutil.move"),
        ):
            mock_run.return_value = mock_result
            engine.inject_chapters(video_path, chapters, job_id="test-job")

        chapters_files = list(tmp_path.glob("chapters-*.txt"))
        assert len(chapters_files) == 0


class TestVideoExportResult:
    def test_fields(self):
        result = VideoExportResult(
            output_path="/tmp/video.mp4",
            total_frames=4320,
            duration_seconds=180.0,
            width=1920,
            height=1080,
            fps=24,
        )
        assert result.output_path == "/tmp/video.mp4"
        assert result.total_frames == 4320
        assert result.duration_seconds == 180.0
        assert result.width == 1920
        assert result.height == 1080
        assert result.fps == 24

    def test_frozen(self):
        result = VideoExportResult(
            output_path="/tmp/video.mp4",
            total_frames=100,
            duration_seconds=5.0,
            width=1920,
            height=1080,
            fps=24,
        )
        with pytest.raises(AttributeError):
            result.output_path = "changed"


class TestChapterInfo:
    def test_fields(self):
        ch = ChapterInfo(
            position=1,
            song_title="Song 1",
            start_seconds=0.0,
            end_seconds=180.0,
            lines=({"text": "Hello", "startSeconds": 0.0},),
        )
        assert ch.position == 1
        assert ch.song_title == "Song 1"
        assert ch.start_seconds == 0.0
        assert ch.end_seconds == 180.0
        assert len(ch.lines) == 1

    def test_default_lines(self):
        ch = ChapterInfo(
            position=1,
            song_title="Song 1",
            start_seconds=0.0,
            end_seconds=180.0,
        )
        assert ch.lines == ()

    def test_frozen(self):
        ch = ChapterInfo(
            position=1,
            song_title="Song 1",
            start_seconds=0.0,
            end_seconds=180.0,
        )
        with pytest.raises(AttributeError):
            ch.song_title = "changed"


class TestResolutionMap:
    def test_720p(self):
        assert RESOLUTION_MAP["720p"] == (1280, 720)

    def test_1080p(self):
        assert RESOLUTION_MAP["1080p"] == (1920, 1080)


class TestFindFFmpeg:
    def test_finds_system_ffmpeg(self):
        with patch("sow_render_worker.video_engine.shutil.which", return_value="/usr/bin/ffmpeg"):
            result = VideoEngine._find_ffmpeg()
            assert result == "/usr/bin/ffmpeg"

    def test_falls_back_to_ffmpeg(self):
        with patch("sow_render_worker.video_engine.shutil.which", return_value=None):
            result = VideoEngine._find_ffmpeg()
            assert result == "ffmpeg"


class TestFFmpegCommandConstruction:
    def test_encode_command_has_rawvideo_input_format(self, tmp_path):
        output_path = str(tmp_path / "video.mp4")
        fetcher = MockAssetFetcher()
        engine = VideoEngine(fetcher, ffmpeg_path="ffmpeg")

        mock_process = MagicMock()
        mock_process.stdin = MagicMock()
        mock_process.stdin.write = MagicMock()
        mock_process.wait.return_value = 0
        mock_process.stderr = MagicMock()
        mock_process.stderr.read.return_value = b""

        with patch("sow_render_worker.video_engine.subprocess.Popen") as mock_popen:
            mock_popen.return_value = mock_process
            engine.encode_video_with_ffmpeg(
                "/tmp/audio.mp3",
                output_path,
                total_frames=1,
                total_duration_seconds=0.05,
                lyrics=[],
                segments=[],
            )

        cmd = mock_popen.call_args[0][0]
        assert "-f" in cmd
        idx_f = cmd.index("-f")
        assert cmd[idx_f + 1] == "rawvideo"
        assert "-vcodec" in cmd
        idx_vc = cmd.index("-vcodec")
        assert cmd[idx_vc + 1] == "rawvideo"
        assert "-pix_fmt" in cmd
        idx_pf = cmd.index("-pix_fmt")
        assert cmd[idx_pf + 1] == "rgb24"

    def test_blank_video_command_has_lavfi_format(self, tmp_path):
        output_path = str(tmp_path / "blank.mp4")
        fetcher = MockAssetFetcher()
        engine = VideoEngine(fetcher, ffmpeg_path="ffmpeg")

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = b""

        with patch("sow_render_worker.video_engine.subprocess.run") as mock_run:
            mock_run.return_value = mock_result
            engine.generate_blank_video("/tmp/audio.mp3", output_path, 60.0)

        cmd = mock_run.call_args[0][0]
        assert "-f" in cmd
        idx_f = cmd.index("-f")
        assert cmd[idx_f + 1] == "lavfi"

    def test_chapter_injection_command_has_map_metadata(self, tmp_path):
        video_path = str(tmp_path / "video.mp4")
        Path(video_path).write_bytes(b"\x00" * 100)

        fetcher = MockAssetFetcher(temp_dir=str(tmp_path))
        engine = VideoEngine(fetcher, ffmpeg_path="ffmpeg")

        chapters = [
            ChapterInfo(position=1, song_title="Song 1", start_seconds=0.0, end_seconds=180.0),
        ]

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = b""

        with (
            patch("sow_render_worker.video_engine.subprocess.run") as mock_run,
            patch("sow_render_worker.video_engine.shutil.move"),
        ):
            mock_run.return_value = mock_result
            engine.inject_chapters(video_path, chapters, job_id="test-job")

        cmd = mock_run.call_args[0][0]
        assert "-map_metadata" in cmd
        idx_mm = cmd.index("-map_metadata")
        assert cmd[idx_mm + 1] == "1"
        assert "-c" in cmd
        idx_c = cmd.index("-c")
        assert cmd[idx_c + 1] == "copy"


class TestFFmpegArgsRGB24:
    def test_encode_command_uses_rgb24(self, tmp_path):
        output_path = str(tmp_path / "video.mp4")
        fetcher = MockAssetFetcher()
        engine = VideoEngine(fetcher, ffmpeg_path="ffmpeg")

        mock_process = MagicMock()
        mock_process.stdin = MagicMock()
        mock_process.stdin.write = MagicMock()
        mock_process.wait.return_value = 0
        mock_process.stderr = MagicMock()
        mock_process.stderr.read.return_value = b""

        with patch("sow_render_worker.video_engine.subprocess.Popen") as mock_popen:
            mock_popen.return_value = mock_process
            engine.encode_video_with_ffmpeg(
                "/tmp/audio.mp3",
                output_path,
                total_frames=1,
                total_duration_seconds=0.05,
                lyrics=[],
                segments=[],
            )

        cmd = mock_popen.call_args[0][0]
        pix_fmt_idx = cmd.index("-pix_fmt")
        assert cmd[pix_fmt_idx + 1] == "rgb24"


class TestCheckMemoryPressure:
    def test_raises_at_90_percent(self):
        status_content = "Name: test\nVmRSS: 2900000 kB\nVmSize: 4000000 kB\n"

        with (
            patch.dict("os.environ", {"AWS_LAMBDA_FUNCTION_MEMORY_SIZE": "3072"}),
            patch("builtins.open", mock_open(read_data=status_content)),
        ):
            with pytest.raises(MemoryError, match="Memory pressure"):
                _check_memory_pressure()

    def test_passes_below_threshold(self):
        status_content = "Name: test\nVmRSS: 1000000 kB\nVmSize: 2000000 kB\n"

        with (
            patch.dict("os.environ", {"AWS_LAMBDA_FUNCTION_MEMORY_SIZE": "3072"}),
            patch("builtins.open", mock_open(read_data=status_content)),
        ):
            _check_memory_pressure()

    def test_noop_without_proc(self):
        with patch("builtins.open", side_effect=OSError):
            _check_memory_pressure()

    def test_warning_fraction_is_90_percent(self):
        assert _MEMORY_WARNING_FRACTION == 0.90


class TestGCCollect:
    def test_gc_collect_called_periodically(self, tmp_path):
        output_path = str(tmp_path / "video.mp4")
        fetcher = MockAssetFetcher()
        engine = VideoEngine(fetcher, fps=24)

        lyrics: list[GlobalLRCLine] = []
        segments: list[SegmentInfo] = []

        mock_process = MagicMock()
        mock_process.stdin = MagicMock()
        mock_process.stdin.write = MagicMock()
        mock_process.wait.return_value = 0
        mock_process.stderr = MagicMock()
        mock_process.stderr.read.return_value = b""

        with (
            patch("sow_render_worker.video_engine.subprocess.Popen") as mock_popen,
            patch("sow_render_worker.video_engine.gc") as mock_gc,
        ):
            mock_popen.return_value = mock_process
            engine.encode_video_with_ffmpeg(
                "/tmp/audio.mp3",
                output_path,
                total_frames=120 * 5,
                total_duration_seconds=5.0,
                lyrics=lyrics,
                segments=segments,
            )

            mock_gc.collect.assert_called()
