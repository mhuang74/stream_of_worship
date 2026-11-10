from __future__ import annotations

import json
import shutil
import struct
import subprocess
from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import pytest

from sow_render_worker.arrangements import ArrangementStep
from sow_render_worker.chapters import generate_chapters_manifest
from sow_render_worker.audio_engine import (
    AudioClipInfo,
    AudioSegmentInfo,
    ExportResult,
    SongsetItem,
    build_ffmpeg_filter_complex,
    calculate_gap_ms,
    calculate_total_duration,
    concatenate_audio_files,
    generate_songset_audio,
    get_audio_info,
    get_crossfade_ms,
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
        arrangement_steps=None,
    )
    defaults.update(overrides)
    return SongsetItem(**defaults)


def _step(
    start: float,
    end: float,
    section_index: int | None = 0,
    label: str = "section",
) -> ArrangementStep:
    return ArrangementStep(
        section_index=section_index,
        label=label,
        start_seconds=start,
        end_seconds=end,
    )


def _clip_entry(path: str, start: float, end: float, target_start: float = 0.0) -> dict:
    return {
        "path": path,
        "source_start_seconds": start,
        "source_end_seconds": end,
        "start_time_seconds": target_start,
    }


def _make_audio_file(
    path: str = "/tmp/song.mp3",
    start_ms: float = 0,
    duration_ms: float = 180000,
    gap_ms: float = 0,
    crossfade_ms: float = 0,
    clips: list[dict] | None = None,
) -> dict:
    return {
        "path": path,
        "item": None,
        "gap_ms": gap_ms,
        "crossfade_ms": crossfade_ms,
        "duration_ms": duration_ms,
        "start_ms": start_ms,
        "clips": clips
        if clips is not None
        else [
            {
                "path": path,
                "source_start_seconds": 0.0,
                "source_end_seconds": duration_ms / 1000.0,
                "start_time_seconds": start_ms / 1000.0,
            }
        ],
    }


class TestSongsetItem:
    def test_default_values(self):
        item = SongsetItem(id="1", songset_id="ss", song_id="s")
        assert item.song_title is None
        assert item.recording_hash_prefix is None
        assert item.position == 0
        assert item.gap_beats is None
        assert item.crossfade_enabled is None
        assert item.crossfade_duration_seconds is None
        assert item.key_shift_semitones is None
        assert item.tempo_ratio is None
        assert item.tempo_bpm is None
        assert item.duration_seconds is None
        assert item.arrangement_steps is None

    def test_frozen(self):
        item = _make_item()
        with pytest.raises(AttributeError):
            item.id = "changed"

    def test_all_fields(self):
        item = SongsetItem(
            id="1",
            songset_id="ss",
            song_id="s",
            song_title="Title",
            recording_hash_prefix="hash",
            position=3,
            gap_beats=4.0,
            crossfade_enabled=1,
            crossfade_duration_seconds=2.5,
            key_shift_semitones=-2.0,
            tempo_ratio=1.1,
            tempo_bpm=120.0,
            duration_seconds=180.0,
            arrangement_steps=(_step(0.0, 4.5), _step(30.0, 45.0, section_index=2)),
        )
        assert item.song_title == "Title"
        assert item.position == 3
        assert item.gap_beats == 4.0
        assert item.crossfade_enabled == 1
        assert item.crossfade_duration_seconds == 2.5
        assert item.key_shift_semitones == -2.0
        assert item.tempo_ratio == 1.1
        assert item.tempo_bpm == 120.0
        assert item.duration_seconds == 180.0
        assert item.arrangement_steps == (
            _step(0.0, 4.5),
            _step(30.0, 45.0, section_index=2),
        )


class TestAudioClipInfo:
    def test_duration_seconds(self):
        clip = AudioClipInfo(
            audio_path="/tmp/a.mp3",
            source_start_seconds=10.0,
            source_end_seconds=20.0,
            start_time_seconds=5.0,
        )
        assert clip.duration_seconds == 10.0

    def test_frozen(self):
        clip = AudioClipInfo("/tmp/a.mp3", 0.0, 1.0, 0.0)
        with pytest.raises(AttributeError):
            clip.source_end_seconds = 2.0


class TestAudioSegmentInfo:
    def test_fields(self):
        item = _make_item()
        clips = (
            AudioClipInfo(
                audio_path="/tmp/audio.mp3",
                source_start_seconds=10.0,
                source_end_seconds=20.0,
                start_time_seconds=0.0,
            ),
        )
        seg = AudioSegmentInfo(
            item=item,
            audio_path="/tmp/audio.mp3",
            start_time_seconds=5.0,
            duration_seconds=180.0,
            gap_before_seconds=2.0,
            clips=clips,
        )
        assert seg.item is item
        assert seg.audio_path == "/tmp/audio.mp3"
        assert seg.start_time_seconds == 5.0
        assert seg.duration_seconds == 180.0
        assert seg.gap_before_seconds == 2.0
        assert seg.clips == clips

    def test_frozen(self):
        item = _make_item()
        seg = AudioSegmentInfo(
            item=item, audio_path="/tmp/a.mp3",
            start_time_seconds=0, duration_seconds=100, gap_before_seconds=0,
            clips=(),
        )
        with pytest.raises(AttributeError):
            seg.audio_path = "changed"


class TestGetCrossfadeMs:
    def test_no_crossfade(self):
        item = _make_item(crossfade_enabled=None, crossfade_duration_seconds=None)
        assert get_crossfade_ms(item) == 0

    def test_crossfade_disabled(self):
        item = _make_item(crossfade_enabled=0, crossfade_duration_seconds=2.0)
        assert get_crossfade_ms(item) == 0

    def test_crossfade_enabled(self):
        item = _make_item(crossfade_enabled=1, crossfade_duration_seconds=2.5)
        assert get_crossfade_ms(item) == 2500

    def test_crossfade_negative_duration(self):
        item = _make_item(crossfade_enabled=1, crossfade_duration_seconds=-1.0)
        assert get_crossfade_ms(item) == 0

    def test_crossfade_zero_duration(self):
        item = _make_item(crossfade_enabled=1, crossfade_duration_seconds=0)
        assert get_crossfade_ms(item) == 0

    def test_crossfade_fractional_seconds(self):
        item = _make_item(crossfade_enabled=1, crossfade_duration_seconds=0.75)
        assert get_crossfade_ms(item) == 750


class TestCalculateGapMs:
    def test_crossfade_override_returns_zero(self):
        item = _make_item(crossfade_enabled=1, crossfade_duration_seconds=2.0)
        assert calculate_gap_ms(item, tempo_bpm=120) == 0

    def test_default_gap_beats_with_tempo(self):
        item = _make_item(gap_beats=None, crossfade_enabled=None)
        result = calculate_gap_ms(item, tempo_bpm=120)
        beat_ms = 60000.0 / 120
        assert result == round(2.0 * beat_ms)

    def test_custom_gap_beats_with_tempo(self):
        item = _make_item(gap_beats=4.0, crossfade_enabled=None)
        result = calculate_gap_ms(item, tempo_bpm=120)
        beat_ms = 60000.0 / 120
        assert result == round(4.0 * beat_ms)

    def test_no_tempo_uses_default_1_second_per_beat(self):
        item = _make_item(gap_beats=2.0, crossfade_enabled=None)
        assert calculate_gap_ms(item, tempo_bpm=None) == 2000

    def test_no_tempo_custom_beats(self):
        item = _make_item(gap_beats=3.0, crossfade_enabled=None)
        assert calculate_gap_ms(item, tempo_bpm=None) == 3000

    def test_zero_tempo_uses_default(self):
        item = _make_item(gap_beats=2.0, crossfade_enabled=None)
        assert calculate_gap_ms(item, tempo_bpm=0) == 2000

    def test_negative_tempo_uses_default(self):
        item = _make_item(gap_beats=2.0, crossfade_enabled=None)
        assert calculate_gap_ms(item, tempo_bpm=-10) == 2000

    def test_120_bpm_2_beats(self):
        item = _make_item(gap_beats=2.0, crossfade_enabled=None)
        result = calculate_gap_ms(item, tempo_bpm=120)
        assert result == 1000

    def test_60_bpm_2_beats(self):
        item = _make_item(gap_beats=2.0, crossfade_enabled=None)
        result = calculate_gap_ms(item, tempo_bpm=60)
        assert result == 2000


class TestGetAudioInfo:
    def test_file_not_found(self):
        assert get_audio_info("/nonexistent/file.mp3") is None

    def test_ffprobe_success(self, tmp_path):
        audio_file = tmp_path / "test.mp3"
        audio_file.write_bytes(b"\x00" * 100)

        probe_output = json.dumps({
            "streams": [
                {
                    "codec_type": "audio",
                    "channels": 2,
                    "sample_rate": "44100",
                }
            ],
            "format": {
                "duration": "180.5",
                "bit_rate": "320000",
            },
        })

        with patch("sow_render_worker.audio_engine.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=probe_output)
            result = get_audio_info(str(audio_file))

        assert result is not None
        assert result["duration_seconds"] == 180.5
        assert result["duration_ms"] == 180500
        assert result["channels"] == 2
        assert result["sample_rate"] == 44100
        assert result["bitrate_kbps"] == 320

    def test_ffprobe_failure(self, tmp_path):
        audio_file = tmp_path / "test.mp3"
        audio_file.write_bytes(b"\x00" * 100)

        with patch("sow_render_worker.audio_engine.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="")
            result = get_audio_info(str(audio_file))

        assert result is None

    def test_ffprobe_no_streams(self, tmp_path):
        audio_file = tmp_path / "test.mp3"
        audio_file.write_bytes(b"\x00" * 100)

        probe_output = json.dumps({"streams": [], "format": {"duration": "0"}})

        with patch("sow_render_worker.audio_engine.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=probe_output)
            result = get_audio_info(str(audio_file))

        assert result is None

    def test_ffprobe_no_audio_stream_falls_back(self, tmp_path):
        audio_file = tmp_path / "test.mp3"
        audio_file.write_bytes(b"\x00" * 100)

        probe_output = json.dumps({
            "streams": [{"codec_type": "video", "channels": 1, "sample_rate": "22050"}],
            "format": {"duration": "60.0", "bit_rate": "128000"},
        })

        with patch("sow_render_worker.audio_engine.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=probe_output)
            result = get_audio_info(str(audio_file))

        assert result is not None
        assert result["channels"] == 1
        assert result["sample_rate"] == 22050

    def test_ffprobe_exception(self, tmp_path):
        audio_file = tmp_path / "test.mp3"
        audio_file.write_bytes(b"\x00" * 100)

        with patch("sow_render_worker.audio_engine.subprocess.run", side_effect=Exception("boom")):
            result = get_audio_info(str(audio_file))

        assert result is None

    def test_ffprobe_missing_fields_defaults(self, tmp_path):
        audio_file = tmp_path / "test.mp3"
        audio_file.write_bytes(b"\x00" * 100)

        probe_output = json.dumps({
            "streams": [{"codec_type": "audio"}],
            "format": {},
        })

        with patch("sow_render_worker.audio_engine.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=probe_output)
            result = get_audio_info(str(audio_file))

        assert result is not None
        assert result["duration_seconds"] == 0
        assert result["channels"] == 2
        assert result["sample_rate"] == 44100
        assert result["bitrate_kbps"] == 0


class TestBuildFfmpegFilterComplex:
    def test_single_file_no_normalize(self):
        audio_files = [_make_audio_file("/tmp/a.mp3")]
        result = build_ffmpeg_filter_complex(audio_files, normalize=False)
        assert "atrim=start=0.000000:end=180.000000,asetpts=PTS-STARTPTS[c0]" in result
        assert "[c0]anull[a0]" in result
        assert "amix=inputs=1" in result
        assert "[outa]" in result
        assert "loudnorm" not in result
        assert "concat=" not in result

    def test_single_file_with_normalize(self):
        audio_files = [_make_audio_file("/tmp/a.mp3")]
        result = build_ffmpeg_filter_complex(audio_files, normalize=True)
        assert "loudnorm=I=-14.0:TP=-1.5:LRA=11[outa]" in result
        assert "[amix_out]" in result

    def test_two_files_with_gap(self):
        audio_files = [
            _make_audio_file("/tmp/a.mp3"),
            _make_audio_file(
                "/tmp/b.mp3", start_ms=182000, duration_ms=200000, gap_ms=2000
            ),
        ]
        result = build_ffmpeg_filter_complex(audio_files, normalize=False)
        assert "adelay=182000|182000" in result
        assert "amix=inputs=2" in result
        assert "[0:a]atrim" in result
        assert "[1:a]atrim" in result
        assert "asplit" not in result

    def test_crossfade_in_and_out(self):
        audio_files = [
            _make_audio_file("/tmp/a.mp3"),
            _make_audio_file(
                "/tmp/b.mp3", start_ms=178000, duration_ms=200000,
                crossfade_ms=2000,
            ),
        ]
        result = build_ffmpeg_filter_complex(audio_files, normalize=False)
        assert "afade=t=in:st=0:d=2.000" in result
        assert "afade=t=out:st=178.000:d=2.000" in result

    def test_custom_target_lufs(self):
        audio_files = [_make_audio_file("/tmp/a.mp3")]
        result = build_ffmpeg_filter_complex(audio_files, normalize=True, target_lufs=-16.0)
        assert "loudnorm=I=-16.0:TP=-1.5:LRA=11[outa]" in result

    def test_three_files(self):
        audio_files = [
            _make_audio_file("/tmp/a.mp3"),
            _make_audio_file(
                "/tmp/b.mp3", start_ms=182000, duration_ms=200000, gap_ms=2000
            ),
            _make_audio_file(
                "/tmp/c.mp3", start_ms=383000, duration_ms=160000, gap_ms=1000
            ),
        ]
        result = build_ffmpeg_filter_complex(audio_files, normalize=False)
        assert "amix=inputs=3" in result

    def test_repeated_steps_use_one_input_trim_and_concat(self):
        audio_files = [
            _make_audio_file(
                "/tmp/a.mp3",
                duration_ms=30000,
                clips=[
                    _clip_entry("/tmp/a.mp3", 10.0, 20.0, 0.0),
                    _clip_entry("/tmp/a.mp3", 0.0, 10.0, 10.0),
                    _clip_entry("/tmp/a.mp3", 10.0, 20.0, 20.0),
                ],
            )
        ]
        result = build_ffmpeg_filter_complex(audio_files, normalize=False)
        assert "[0:a]asplit=3[s0_0][s0_1][s0_2]" in result
        assert (
            "[s0_0]atrim=start=10.000000:end=20.000000,asetpts=PTS-STARTPTS[c0]"
            in result
        )
        assert (
            "[s0_1]atrim=start=0.000000:end=10.000000,asetpts=PTS-STARTPTS[c1]"
            in result
        )
        assert (
            "[s0_2]atrim=start=10.000000:end=20.000000,asetpts=PTS-STARTPTS[c2]"
            in result
        )
        assert "[c0][c1][c2]concat=n=3:v=0:a=1[i0]" in result
        assert result.count("asplit") == 1
        assert "amix=inputs=1" in result

    def test_items_sharing_a_file_use_one_input(self):
        audio_files = [
            _make_audio_file(
                "/tmp/a.mp3",
                clips=[_clip_entry("/tmp/a.mp3", 0.0, 10.0, 0.0)],
                duration_ms=10000,
            ),
            _make_audio_file(
                "/tmp/a.mp3",
                start_ms=12000,
                duration_ms=10000,
                gap_ms=2000,
                clips=[_clip_entry("/tmp/a.mp3", 10.0, 20.0, 12.0)],
            ),
        ]
        result = build_ffmpeg_filter_complex(audio_files, normalize=False)
        assert "[0:a]asplit=2[s0_0][s0_1]" in result
        assert "amix=inputs=2" in result
        assert "[1:a]" not in result


class TestConcatenateAudioFiles:
    def test_calls_ffmpeg_with_correct_args(self):
        audio_files = [_make_audio_file("/tmp/a.mp3")]

        with patch("sow_render_worker.audio_engine.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            concatenate_audio_files(audio_files, "/tmp/out.mp3", job_id="test-job")

        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "ffmpeg"
        assert "-y" in cmd
        assert "-i" in cmd
        assert "/tmp/a.mp3" in cmd
        assert "-filter_complex" in cmd
        assert "-map" in cmd
        assert "[outa]" in cmd
        assert "libmp3lame" in cmd
        assert "320k" in cmd

    def test_one_input_per_distinct_file(self):
        audio_files = [
            _make_audio_file(
                "/tmp/a.mp3",
                duration_ms=10000,
                clips=[_clip_entry("/tmp/a.mp3", 0.0, 10.0, 0.0)],
            ),
            _make_audio_file(
                "/tmp/a.mp3",
                start_ms=12000,
                duration_ms=10000,
                gap_ms=2000,
                clips=[_clip_entry("/tmp/a.mp3", 10.0, 20.0, 12.0)],
            ),
        ]

        with patch("sow_render_worker.audio_engine.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            concatenate_audio_files(audio_files, "/tmp/out.mp3", job_id="test-job")

        cmd = mock_run.call_args[0][0]
        assert cmd.count("-i") == 1
        assert cmd.count("/tmp/a.mp3") == 1

    def test_two_distinct_files_add_two_inputs(self):
        audio_files = [
            _make_audio_file("/tmp/a.mp3"),
            _make_audio_file("/tmp/b.mp3", start_ms=182000, duration_ms=200000, gap_ms=2000),
        ]

        with patch("sow_render_worker.audio_engine.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            concatenate_audio_files(audio_files, "/tmp/out.mp3", job_id="test-job")

        cmd = mock_run.call_args[0][0]
        assert cmd.count("-i") == 2
        assert cmd.index("/tmp/a.mp3") < cmd.index("/tmp/b.mp3")

    def test_custom_options(self):
        audio_files = [_make_audio_file("/tmp/a.mp3")]

        with patch("sow_render_worker.audio_engine.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            concatenate_audio_files(
                audio_files, "/tmp/out.mp3",
                normalize=False,
                output_bitrate="192k",
                sample_rate=22050,
                channels=1,
                job_id="test-job",
            )

        cmd = mock_run.call_args[0][0]
        assert "192k" in cmd
        assert "22050" in cmd
        assert "-ac" in cmd
        idx_ac = cmd.index("-ac")
        assert cmd[idx_ac + 1] == "1"

    def test_ffmpeg_failure_raises(self):
        audio_files = [_make_audio_file("/tmp/a.mp3")]

        with patch("sow_render_worker.audio_engine.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(1, "ffmpeg")
            with pytest.raises(subprocess.CalledProcessError):
                concatenate_audio_files(audio_files, "/tmp/out.mp3", job_id="test-job")


class TestGenerateSongsetAudio:
    def test_empty_items_raises(self):
        fetcher = MagicMock()
        with pytest.raises(ValueError, match="empty songset"):
            generate_songset_audio([], "/tmp/out.mp3", fetcher)

    def test_no_recording_raises(self):
        item = _make_item(recording_hash_prefix=None)
        fetcher = MagicMock()
        with pytest.raises(ValueError, match="no recording"):
            generate_songset_audio([item], "/tmp/out.mp3", fetcher)

    def test_download_failure_raises(self):
        item = _make_item(recording_hash_prefix="abc")
        fetcher = MagicMock()
        fetcher.download_audio.return_value = None
        with pytest.raises(ValueError, match="Could not get audio"):
            generate_songset_audio([item], "/tmp/out.mp3", fetcher)

    def test_single_item_success(self, tmp_path):
        item = _make_item()
        fetcher = MagicMock()
        fetcher.download_audio.return_value = "/tmp/audio.mp3"
        output_path = str(tmp_path / "out.mp3")

        probe_output = json.dumps({
            "streams": [{"codec_type": "audio", "channels": 2, "sample_rate": "44100"}],
            "format": {"duration": "180.0", "bit_rate": "320000"},
        })

        with patch("sow_render_worker.audio_engine.get_audio_info") as mock_info, \
             patch("sow_render_worker.audio_engine.concatenate_audio_files") as mock_concat:
            mock_info.return_value = {
                "duration_seconds": 180.0,
                "duration_ms": 180000,
                "channels": 2,
                "sample_rate": 44100,
                "bitrate_kbps": 320,
                "file_size_bytes": 1000,
            }
            result = generate_songset_audio([item], output_path, fetcher)

        assert result.output_path == output_path
        assert result.total_duration_seconds == 180.0
        assert len(result.segments) == 1
        assert result.segments[0].start_time_seconds == 0
        assert result.segments[0].duration_seconds == 180.0
        assert result.segments[0].gap_before_seconds == 0

    def test_two_items_with_gap(self, tmp_path):
        item1 = _make_item(id="1", position=0, gap_beats=2.0, tempo_bpm=120.0)
        item2 = _make_item(
            id="2", position=1, gap_beats=2.0, tempo_bpm=120.0,
            recording_hash_prefix="def456",
        )
        fetcher = MagicMock()
        fetcher.download_audio.return_value = "/tmp/audio.mp3"
        output_path = str(tmp_path / "out.mp3")

        with patch("sow_render_worker.audio_engine.get_audio_info") as mock_info, \
             patch("sow_render_worker.audio_engine.concatenate_audio_files") as mock_concat:
            mock_info.return_value = {
                "duration_seconds": 180.0,
                "duration_ms": 180000,
                "channels": 2,
                "sample_rate": 44100,
                "bitrate_kbps": 320,
                "file_size_bytes": 1000,
            }
            result = generate_songset_audio([item1, item2], output_path, fetcher)

        assert len(result.segments) == 2
        assert result.segments[0].start_time_seconds == 0
        assert result.segments[1].start_time_seconds == 181.0
        assert result.segments[1].gap_before_seconds == 1.0

    def test_progress_callback(self, tmp_path):
        item = _make_item()
        fetcher = MagicMock()
        fetcher.download_audio.return_value = "/tmp/audio.mp3"
        output_path = str(tmp_path / "out.mp3")
        progress_calls = []

        def progress_cb(current, total):
            progress_calls.append((current, total))

        with patch("sow_render_worker.audio_engine.get_audio_info") as mock_info, \
             patch("sow_render_worker.audio_engine.concatenate_audio_files"):
            mock_info.return_value = {
                "duration_seconds": 180.0,
                "duration_ms": 180000,
                "channels": 2,
                "sample_rate": 44100,
                "bitrate_kbps": 320,
                "file_size_bytes": 1000,
            }
            generate_songset_audio([item], output_path, fetcher, progress_callback=progress_cb)

        assert len(progress_calls) >= 2
        assert progress_calls[-1] == (2, 2)

    def test_crossfade_adjusts_start_time(self, tmp_path):
        item1 = _make_item(id="1", position=0)
        item2 = _make_item(
            id="2", position=1,
            crossfade_enabled=1, crossfade_duration_seconds=2.0,
            recording_hash_prefix="def456",
        )
        fetcher = MagicMock()
        fetcher.download_audio.return_value = "/tmp/audio.mp3"
        output_path = str(tmp_path / "out.mp3")

        with patch("sow_render_worker.audio_engine.get_audio_info") as mock_info, \
             patch("sow_render_worker.audio_engine.concatenate_audio_files"):
            mock_info.return_value = {
                "duration_seconds": 180.0,
                "duration_ms": 180000,
                "channels": 2,
                "sample_rate": 44100,
                "bitrate_kbps": 320,
                "file_size_bytes": 1000,
            }
            result = generate_songset_audio([item1, item2], output_path, fetcher)

        assert result.segments[1].gap_before_seconds == 0.0
        assert result.segments[1].start_time_seconds == 178.0

    def test_null_arrangement_emits_single_full_file_clip(self, tmp_path):
        item = _make_item(duration_seconds=180.0)
        fetcher = MagicMock()
        fetcher.download_audio.return_value = "/tmp/audio.mp3"
        output_path = str(tmp_path / "out.mp3")

        with patch("sow_render_worker.audio_engine.concatenate_audio_files"):
            result = generate_songset_audio([item], output_path, fetcher)

        assert result.segments[0].clips == (
            AudioClipInfo(
                audio_path="/tmp/audio.mp3",
                source_start_seconds=0.0,
                source_end_seconds=180.0,
                start_time_seconds=0.0,
            ),
        )

    def test_arranged_clips_repeat_and_reorder_source_ranges(self, tmp_path):
        item = _make_item(
            recording_hash_prefix="abc",
            duration_seconds=999.0,
            arrangement_steps=(
                _step(10.0, 20.0, section_index=1, label="middle"),
                _step(0.0, 10.0, section_index=0, label="intro"),
                _step(10.0, 20.0, section_index=1, label="middle"),
            ),
        )
        fetcher = MagicMock()
        fetcher.download_audio.return_value = "/tmp/song.mp3"
        output_path = str(tmp_path / "out.mp3")

        with patch("sow_render_worker.audio_engine.concatenate_audio_files"):
            result = generate_songset_audio([item], output_path, fetcher)

        segment = result.segments[0]
        assert [(c.source_start_seconds, c.source_end_seconds, c.start_time_seconds)
                for c in segment.clips] == [
            (10.0, 20.0, 0.0),
            (0.0, 10.0, 10.0),
            (10.0, 20.0, 20.0),
        ]
        assert segment.duration_seconds == 30.0
        assert result.total_duration_seconds == 30.0

    def test_null_index_intro_slices_from_zero(self, tmp_path):
        # An inferred Intro carries no catalog section index; the engine only
        # needs its source range, so 0.0 → 12.4 is one clip.
        item = _make_item(
            recording_hash_prefix="abc",
            duration_seconds=999.0,
            arrangement_steps=(_step(0.0, 12.4, section_index=None, label="intro"),),
        )
        fetcher = MagicMock()
        fetcher.download_audio.return_value = "/tmp/song.mp3"
        output_path = str(tmp_path / "out.mp3")

        with patch("sow_render_worker.audio_engine.concatenate_audio_files"):
            result = generate_songset_audio([item], output_path, fetcher)

        segment = result.segments[0]
        assert [
            (c.source_start_seconds, c.source_end_seconds, c.start_time_seconds)
            for c in segment.clips
        ] == [(0.0, 12.4, 0.0)]
        assert segment.duration_seconds == pytest.approx(12.4)
        assert result.total_duration_seconds == pytest.approx(12.4)

    def test_arranged_clips_join_without_internal_gaps(self, tmp_path):
        item = _make_item(
            arrangement_steps=(
                _step(30.0, 42.5),
                _step(5.25, 12.0),
                _step(30.0, 42.5),
            ),
        )
        fetcher = MagicMock()
        fetcher.download_audio.return_value = "/tmp/song.mp3"
        output_path = str(tmp_path / "out.mp3")

        with patch("sow_render_worker.audio_engine.concatenate_audio_files"):
            result = generate_songset_audio([item], output_path, fetcher)

        clips = result.segments[0].clips
        for previous, following in zip(clips, clips[1:]):
            assert following.start_time_seconds == pytest.approx(
                previous.start_time_seconds + previous.duration_seconds
            )
        assert result.segments[0].duration_seconds == pytest.approx(
            sum(step.duration_seconds for step in item.arrangement_steps)
        )

    def test_inter_item_gap_unchanged_with_arrangements(self, tmp_path):
        item1 = _make_item(
            id="1", position=0, gap_beats=2.0, tempo_bpm=120.0,
            arrangement_steps=(_step(0.0, 10.0), _step(20.0, 30.0)),
        )
        item2 = _make_item(
            id="2", position=1, gap_beats=2.0, tempo_bpm=120.0,
            recording_hash_prefix="def456",
            arrangement_steps=(_step(10.0, 20.0),),
        )
        fetcher = MagicMock()
        fetcher.download_audio.return_value = "/tmp/song.mp3"
        output_path = str(tmp_path / "out.mp3")

        with patch("sow_render_worker.audio_engine.concatenate_audio_files"):
            result = generate_songset_audio([item1, item2], output_path, fetcher)

        assert result.segments[0].duration_seconds == 20.0
        assert result.segments[1].gap_before_seconds == 1.0
        assert result.segments[1].start_time_seconds == 21.0
        assert result.total_duration_seconds == 31.0

    def test_short_arranged_incoming_duration_clamps_crossfade(self, tmp_path):
        item1 = _make_item(id="1", position=0, duration_seconds=180.0)
        item2 = _make_item(
            id="2", position=1,
            crossfade_enabled=1, crossfade_duration_seconds=2.0,
            recording_hash_prefix="def456",
            arrangement_steps=(_step(10.0, 11.5),),
        )
        fetcher = MagicMock()
        fetcher.download_audio.return_value = "/tmp/song.mp3"
        output_path = str(tmp_path / "out.mp3")

        with patch("sow_render_worker.audio_engine.concatenate_audio_files"):
            result = generate_songset_audio([item1, item2], output_path, fetcher)

        assert result.segments[1].gap_before_seconds == 0.0
        assert result.segments[1].start_time_seconds == 178.5
        assert result.segments[1].duration_seconds == 1.5
        assert result.total_duration_seconds == 180.0

    def test_crossfade_exceeding_incoming_total_matches_mix(self, tmp_path):
        item1 = _make_item(id="1", position=0, duration_seconds=180.0)
        item2 = _make_item(
            id="2", position=1,
            crossfade_enabled=1, crossfade_duration_seconds=2.0,
            recording_hash_prefix="def456",
            arrangement_steps=(_step(0.0, 1.5),),
        )
        fetcher = MagicMock()
        fetcher.download_audio.return_value = "/tmp/song.mp3"
        output_path = str(tmp_path / "out.mp3")

        with patch("sow_render_worker.audio_engine.concatenate_audio_files"):
            mix = generate_songset_audio([item1, item2], output_path, fetcher)

        total = calculate_total_duration([item1, item2], fetcher)
        assert mix.total_duration_seconds == 180.0
        assert total == mix.total_duration_seconds

    def test_audio_files_payload_carries_clips(self, tmp_path):
        item = _make_item(
            arrangement_steps=(_step(10.0, 20.0), _step(0.0, 10.0)),
        )
        fetcher = MagicMock()
        fetcher.download_audio.return_value = "/tmp/song.mp3"
        output_path = str(tmp_path / "out.mp3")

        with patch("sow_render_worker.audio_engine.concatenate_audio_files") as mock_concat:
            generate_songset_audio([item], output_path, fetcher)

        audio_files = mock_concat.call_args[0][0]
        assert len(audio_files) == 1
        assert [clip["source_start_seconds"] for clip in audio_files[0]["clips"]] == [10.0, 0.0]
        assert audio_files[0]["path"] == "/tmp/song.mp3"


class TestCalculateTotalDuration:
    def test_single_item_with_duration(self):
        item = _make_item(duration_seconds=180.0)
        fetcher = MagicMock()
        result = calculate_total_duration([item], fetcher)
        assert result == 180.0

    def test_two_items_with_gap(self):
        item1 = _make_item(id="1", duration_seconds=180.0, gap_beats=2.0, tempo_bpm=120.0)
        item2 = _make_item(id="2", duration_seconds=200.0, gap_beats=2.0, tempo_bpm=120.0)
        fetcher = MagicMock()
        result = calculate_total_duration([item1, item2], fetcher)
        expected = 180.0 + 1.0 + 200.0
        assert result == expected

    def test_crossfade_subtracts_from_total(self):
        item1 = _make_item(id="1", duration_seconds=180.0)
        item2 = _make_item(
            id="2", duration_seconds=200.0,
            crossfade_enabled=1, crossfade_duration_seconds=2.0,
        )
        fetcher = MagicMock()
        result = calculate_total_duration([item1, item2], fetcher)
        expected = 180.0 + 0 + 200.0 - 2.0
        assert result == expected

    def test_no_duration_fetches_audio(self):
        item = _make_item(duration_seconds=None, recording_hash_prefix="abc")
        fetcher = MagicMock()
        fetcher.download_audio.return_value = "/tmp/audio.mp3"

        with patch("sow_render_worker.audio_engine.get_audio_info") as mock_info:
            mock_info.return_value = {
                "duration_seconds": 150.0,
                "duration_ms": 150000,
                "channels": 2,
                "sample_rate": 44100,
                "bitrate_kbps": 320,
                "file_size_bytes": 1000,
            }
            result = calculate_total_duration([item], fetcher)

        assert result == 150.0

    def test_no_duration_no_recording(self):
        item = _make_item(duration_seconds=None, recording_hash_prefix=None)
        fetcher = MagicMock()
        result = calculate_total_duration([item], fetcher)
        assert result == 0.0

    def test_arranged_steps_override_db_duration(self):
        item = _make_item(
            duration_seconds=180.0,
            arrangement_steps=(_step(0.0, 5.0), _step(10.0, 15.0)),
        )
        fetcher = MagicMock()
        assert calculate_total_duration([item], fetcher) == 10.0

    def test_two_arranged_items_with_gap(self):
        item1 = _make_item(
            id="1", gap_beats=2.0, tempo_bpm=120.0,
            arrangement_steps=(_step(0.0, 20.0),),
        )
        item2 = _make_item(
            id="2", gap_beats=2.0, tempo_bpm=120.0,
            arrangement_steps=(_step(5.0, 10.0),),
        )
        fetcher = MagicMock()
        assert calculate_total_duration([item1, item2], fetcher) == 26.0

    def test_crossfade_exceeding_incoming_duration_uses_last_end(self):
        item1 = _make_item(id="1", duration_seconds=180.0)
        item2 = _make_item(
            id="2", duration_seconds=200.0,
            crossfade_enabled=1, crossfade_duration_seconds=2.0,
            arrangement_steps=(_step(0.0, 0.5),),
        )
        fetcher = MagicMock()
        # Crossfade clamps to the 0.5s incoming item, so the mix ends at 180.0s
        # rather than the old formula's 180 + 0 + 0.5 - 2.0 = 178.5s.
        result = calculate_total_duration([item1, item2], fetcher)
        assert result == 180.0


class TestExportResult:
    def test_default_values(self):
        r = ExportResult(output_path="/tmp/out.mp3", total_duration_seconds=180.0)
        assert r.segments == ()
        assert r.sample_rate == 44100
        assert r.channels == 2

    def test_frozen(self):
        r = ExportResult(output_path="/tmp/out.mp3", total_duration_seconds=180.0)
        with pytest.raises(AttributeError):
            r.output_path = "changed"


FFMPEG_BIN = shutil.which("ffmpeg")
FFPROBE_BIN = shutil.which("ffprobe")


def _run_checked(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, capture_output=True)


def _estimate_frequency(samples: tuple[int, ...], sample_rate: int = 8000) -> float:
    crossings = 0
    previous = samples[0]
    for sample in samples[1:]:
        if (previous < 0 <= sample) or (previous >= 0 > sample):
            crossings += 1
        previous = sample
    return crossings / (len(samples) / sample_rate) / 2


def _classify_frequency(samples: tuple[int, ...]) -> float:
    estimate = _estimate_frequency(samples)
    return min((220.0, 440.0, 880.0), key=lambda frequency: abs(frequency - estimate))


class _RecordingFetcher:
    def __init__(self, path) -> None:
        self.path = str(path)

    def download_audio(self, hash_prefix: str) -> str:
        return self.path


@pytest.mark.skipif(
    FFMPEG_BIN is None or FFPROBE_BIN is None,
    reason="ffmpeg/ffprobe are required for the real-audio fixture",
)
class TestRealFfmpegArrangementMix:
    """Throwaway local fixture: a 30s tone-segmented MP3, mixed for real."""

    @pytest.fixture()
    def source_audio(self, tmp_path):
        source = tmp_path / "source.mp3"
        _run_checked(
            [
                FFMPEG_BIN, "-y", "-v", "error",
                "-f", "lavfi", "-i", "sine=frequency=220:duration=10:sample_rate=44100",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=10:sample_rate=44100",
                "-f", "lavfi", "-i", "sine=frequency=880:duration=10:sample_rate=44100",
                "-filter_complex", "[0:a][1:a][2:a]concat=n=3:v=0:a=1[out]",
                "-map", "[out]", "-c:a", "libmp3lame", "-b:a", "192k", str(source),
            ]
        )
        return source

    def test_repeated_and_reordered_steps_mix_real_audio(self, tmp_path, source_audio):
        item = _make_item(
            recording_hash_prefix="tone-fixture",
            arrangement_steps=(
                _step(10.0, 20.0, section_index=1, label="middle"),
                _step(0.0, 10.0, section_index=0, label="intro"),
                _step(10.0, 20.0, section_index=1, label="middle"),
            ),
        )
        output = tmp_path / "arranged.mp3"

        result = generate_songset_audio(
            [item], str(output), _RecordingFetcher(source_audio), normalize=False,
        )

        probe = json.loads(
            _run_checked(
                [
                    FFPROBE_BIN, "-v", "quiet", "-print_format", "json",
                    "-show_format", str(output),
                ]
            ).stdout
        )
        assert abs(float(probe["format"]["duration"]) - 30.0) < 0.25
        assert abs(result.total_duration_seconds - 30.0) < 0.001

        pcm = _run_checked(
            [
                FFMPEG_BIN, "-v", "error", "-i", str(output),
                "-f", "s16le", "-ac", "1", "-ar", "8000", "-",
            ]
        ).stdout
        samples = struct.unpack(f"<{len(pcm) // 2}h", pcm)
        third = len(samples) // 3
        margin = 2000  # 0.25s guard against MP3 frame padding at clip joins
        fingerprints = [
            _classify_frequency(samples[i * third + margin:(i + 1) * third - margin])
            for i in range(3)
        ]
        # Source tones are 220Hz in [0,10), 440Hz in [10,20), 880Hz in [20,30).
        # Steps [10,20) [0,10) [10,20) therefore play middle, beginning, middle.
        assert fingerprints == [440.0, 220.0, 440.0]

    def test_remapped_lyrics_match_mixed_clip_timeline(self, tmp_path, source_audio):
        item = _make_item(
            recording_hash_prefix="tone-fixture",
            song_title="Tone Song",
            arrangement_steps=(
                _step(10.0, 20.0, section_index=1, label="middle"),
                _step(0.0, 10.0, section_index=0, label="intro"),
                _step(10.0, 20.0, section_index=1, label="middle"),
            ),
        )
        output = tmp_path / "arranged.mp3"
        result = generate_songset_audio(
            [item], str(output), _RecordingFetcher(source_audio), normalize=False,
        )

        lrc = "[00:01.00]line at 1\n[00:11.00]line at 11\n[00:21.00]line at 21"
        manifest = generate_chapters_manifest(
            list(result.segments), lambda hash_prefix: lrc, result.total_duration_seconds,
        )
        chapter = manifest.chapters[0]
        assert [line.text for line in chapter.lines] == [
            "line at 11", "line at 1", "line at 11",
        ]
        assert [line.start_seconds for line in chapter.lines] == [1.0, 11.0, 21.0]
        assert chapter.start_seconds == 0.0
        assert chapter.end_seconds == 30.0

        # chapters.json carries the same remapped, repeated lyrics.
        payload = manifest.to_camel_case_dict()
        assert [line["text"] for line in payload["chapters"][0]["lines"]] == [
            "line at 11", "line at 1", "line at 11",
        ]
        assert [line["startSeconds"] for line in payload["chapters"][0]["lines"]] == [
            1.0, 11.0, 21.0,
        ]
