from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import pytest

from sow_render_worker.audio_engine import (
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
    )
    defaults.update(overrides)
    return SongsetItem(**defaults)


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


class TestAudioSegmentInfo:
    def test_fields(self):
        item = _make_item()
        seg = AudioSegmentInfo(
            item=item,
            audio_path="/tmp/audio.mp3",
            start_time_seconds=5.0,
            duration_seconds=180.0,
            gap_before_seconds=2.0,
        )
        assert seg.item is item
        assert seg.audio_path == "/tmp/audio.mp3"
        assert seg.start_time_seconds == 5.0
        assert seg.duration_seconds == 180.0
        assert seg.gap_before_seconds == 2.0

    def test_frozen(self):
        item = _make_item()
        seg = AudioSegmentInfo(
            item=item, audio_path="/tmp/a.mp3",
            start_time_seconds=0, duration_seconds=100, gap_before_seconds=0,
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
        audio_files = [
            {"path": "/tmp/a.mp3", "item": None, "gap_ms": 0,
             "crossfade_ms": 0, "duration_ms": 180000, "start_ms": 0},
        ]
        result = build_ffmpeg_filter_complex(audio_files, normalize=False)
        assert "[0:a]asetpts=PTS-STARTPTS[a0]" in result
        assert "amix=inputs=1" in result
        assert "[outa]" in result
        assert "loudnorm" not in result

    def test_single_file_with_normalize(self):
        audio_files = [
            {"path": "/tmp/a.mp3", "item": None, "gap_ms": 0,
             "crossfade_ms": 0, "duration_ms": 180000, "start_ms": 0},
        ]
        result = build_ffmpeg_filter_complex(audio_files, normalize=True)
        assert "loudnorm=I=-14.0:TP=-1.5:LRA=11[outa]" in result
        assert "[amix_out]" in result

    def test_two_files_with_gap(self):
        audio_files = [
            {"path": "/tmp/a.mp3", "item": None, "gap_ms": 0,
             "crossfade_ms": 0, "duration_ms": 180000, "start_ms": 0},
            {"path": "/tmp/b.mp3", "item": None, "gap_ms": 2000,
             "crossfade_ms": 0, "duration_ms": 200000, "start_ms": 182000},
        ]
        result = build_ffmpeg_filter_complex(audio_files, normalize=False)
        assert "adelay=182000|182000" in result
        assert "amix=inputs=2" in result

    def test_crossfade_in_and_out(self):
        audio_files = [
            {"path": "/tmp/a.mp3", "item": None, "gap_ms": 0,
             "crossfade_ms": 0, "duration_ms": 180000, "start_ms": 0},
            {"path": "/tmp/b.mp3", "item": None, "gap_ms": 0,
             "crossfade_ms": 2000, "duration_ms": 200000, "start_ms": 178000},
        ]
        result = build_ffmpeg_filter_complex(audio_files, normalize=False)
        assert "afade=t=in:st=0:d=2.000" in result
        assert "afade=t=out:st=178.000:d=2.000" in result

    def test_custom_target_lufs(self):
        audio_files = [
            {"path": "/tmp/a.mp3", "item": None, "gap_ms": 0,
             "crossfade_ms": 0, "duration_ms": 180000, "start_ms": 0},
        ]
        result = build_ffmpeg_filter_complex(audio_files, normalize=True, target_lufs=-16.0)
        assert "loudnorm=I=-16.0:TP=-1.5:LRA=11[outa]" in result

    def test_three_files(self):
        audio_files = [
            {"path": "/tmp/a.mp3", "item": None, "gap_ms": 0,
             "crossfade_ms": 0, "duration_ms": 180000, "start_ms": 0},
            {"path": "/tmp/b.mp3", "item": None, "gap_ms": 2000,
             "crossfade_ms": 0, "duration_ms": 200000, "start_ms": 182000},
            {"path": "/tmp/c.mp3", "item": None, "gap_ms": 1000,
             "crossfade_ms": 0, "duration_ms": 160000, "start_ms": 383000},
        ]
        result = build_ffmpeg_filter_complex(audio_files, normalize=False)
        assert "amix=inputs=3" in result


class TestConcatenateAudioFiles:
    def test_calls_ffmpeg_with_correct_args(self):
        audio_files = [
            {"path": "/tmp/a.mp3", "item": None, "gap_ms": 0,
             "crossfade_ms": 0, "duration_ms": 180000, "start_ms": 0},
        ]

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

    def test_custom_options(self):
        audio_files = [
            {"path": "/tmp/a.mp3", "item": None, "gap_ms": 0,
             "crossfade_ms": 0, "duration_ms": 180000, "start_ms": 0},
        ]

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
        audio_files = [
            {"path": "/tmp/a.mp3", "item": None, "gap_ms": 0,
             "crossfade_ms": 0, "duration_ms": 180000, "start_ms": 0},
        ]

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
