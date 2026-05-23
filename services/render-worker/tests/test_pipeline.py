from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from sow_render_worker.audio_engine import AudioSegmentInfo, ExportResult, SongsetItem
from sow_render_worker.chapters import ChaptersManifest, Chapter
from sow_render_worker.db import RenderJob, RenderProgress
from sow_render_worker.pipeline import (
    DEFAULT_RENDER_RATIOS,
    MIN_HISTORICAL_JOBS,
    MIN_REASONABLE_RATIO,
    MAX_REASONABLE_RATIO,
    PipelineCancelledError,
    execute_render_pipeline,
    fetch_songset_items,
    get_default_ratio,
    get_render_ratio,
    PHASES,
)
from sow_render_worker.db import update_render_progress
from sow_render_worker.uploader import UploadArtifactsResult


def _make_songset_item(**overrides) -> SongsetItem:
    defaults = {
        "id": "item_1",
        "songset_id": "ss_001",
        "song_id": "song_1",
        "song_title": "Test Song",
        "recording_hash_prefix": "abc123",
        "position": 0,
        "gap_beats": 2.0,
        "crossfade_enabled": 0,
        "crossfade_duration_seconds": None,
        "key_shift_semitones": 0,
        "tempo_ratio": 1.0,
        "tempo_bpm": 120.0,
        "duration_seconds": 180.0,
    }
    defaults.update(overrides)
    return SongsetItem(**defaults)


def _make_render_job(**overrides) -> RenderJob:
    defaults = {
        "id": "job_abc123",
        "songset_id": "ss_001",
        "user_id": 42,
        "status": "queued",
        "template": "dark",
        "resolution": "720p",
        "audio_enabled": True,
        "video_enabled": True,
        "font_size_preset": "M",
        "include_title_card": False,
        "title_card_duration_seconds": None,
    }
    defaults.update(overrides)
    return RenderJob(**defaults)


def _make_mock_conn(fetchone_result=None, fetchall_result=None):
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value.__enter__ = MagicMock(return_value=cursor)
    conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    cursor.fetchone.return_value = fetchone_result
    cursor.fetchall.return_value = fetchall_result or []
    return conn, cursor


def _make_audio_result(items):
    return ExportResult(
        output_path="/tmp/test/output.mp3",
        total_duration_seconds=180.0,
        segments=tuple(
            AudioSegmentInfo(
                item=item,
                audio_path=f"/tmp/test/{item.recording_hash_prefix}.mp3",
                start_time_seconds=0.0,
                duration_seconds=item.duration_seconds or 180.0,
                gap_before_seconds=0.0,
            )
            for item in items
        ),
    )


def _make_chapters_manifest():
    return ChaptersManifest(
        chapters=(
            Chapter(
                position=1,
                song_title="Test Song",
                start_seconds=0.0,
                end_seconds=180.0,
            ),
        ),
        total_duration_seconds=180.0,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )


def _make_upload_result():
    return UploadArtifactsResult(
        mp3_r2_key="renders/job_abc123/output.mp3",
        mp4_r2_key="renders/job_abc123/output.mp4",
        chapters_r2_key="renders/job_abc123/chapters.json",
    )


def _make_mock_fetcher():
    fetcher = MagicMock()
    fetcher.get_job_temp_dir.return_value = Path("/tmp/sow-test")
    fetcher.download_audio.return_value = "/tmp/test/abc123.mp3"
    fetcher.download_lrc.return_value = "[00:00.00]Test line"
    return fetcher


def _make_mock_uploader(upload_result=None):
    uploader = MagicMock()
    uploader.upload_render_artifacts.return_value = upload_result or _make_upload_result()
    return uploader


class TestPhases:
    def test_phase_count(self):
        assert len(PHASES) == 5

    def test_phase_order(self):
        assert PHASES == [
            "preparing",
            "mixing_audio",
            "rendering_frames",
            "encoding_video",
            "uploading",
        ]


class TestDefaultRenderRatios:
    def test_all_keys_present(self):
        assert "720p_video" in DEFAULT_RENDER_RATIOS
        assert "720p_audio" in DEFAULT_RENDER_RATIOS
        assert "1080p_video" in DEFAULT_RENDER_RATIOS
        assert "1080p_audio" in DEFAULT_RENDER_RATIOS

    def test_values(self):
        assert DEFAULT_RENDER_RATIOS["720p_video"] == 0.8
        assert DEFAULT_RENDER_RATIOS["720p_audio"] == 0.4
        assert DEFAULT_RENDER_RATIOS["1080p_video"] == 0.65
        assert DEFAULT_RENDER_RATIOS["1080p_audio"] == 0.4

    def test_thresholds(self):
        assert MIN_HISTORICAL_JOBS == 3
        assert MIN_REASONABLE_RATIO == 0.05
        assert MAX_REASONABLE_RATIO == 5.0


class TestGetDefaultRatio:
    def test_known_keys(self):
        assert get_default_ratio("720p", True) == 0.8
        assert get_default_ratio("720p", False) == 0.4
        assert get_default_ratio("1080p", True) == 0.65
        assert get_default_ratio("1080p", False) == 0.4

    def test_unknown_resolution_returns_max(self):
        assert get_default_ratio("4k", True) == max(DEFAULT_RENDER_RATIOS.values())


class TestGetRenderRatio:
    def test_insufficient_historical_jobs_returns_default(self):
        conn, cursor = _make_mock_conn(
            fetchone_result={"ratio": 0.5, "cnt": 2}
        )
        result = get_render_ratio(conn, "720p", True)
        assert result == get_default_ratio("720p", True)

    def test_sufficient_historical_jobs_returns_avg(self):
        conn, cursor = _make_mock_conn(
            fetchone_result={"ratio": 0.75, "cnt": 5}
        )
        result = get_render_ratio(conn, "720p", True)
        assert result == 0.75

    def test_ratio_below_min_returns_default(self):
        conn, cursor = _make_mock_conn(
            fetchone_result={"ratio": 0.01, "cnt": 5}
        )
        result = get_render_ratio(conn, "720p", True)
        assert result == get_default_ratio("720p", True)

    def test_ratio_above_max_returns_default(self):
        conn, cursor = _make_mock_conn(
            fetchone_result={"ratio": 10.0, "cnt": 5}
        )
        result = get_render_ratio(conn, "720p", True)
        assert result == get_default_ratio("720p", True)

    def test_null_ratio_returns_default(self):
        conn, cursor = _make_mock_conn(
            fetchone_result={"ratio": None, "cnt": 5}
        )
        result = get_render_ratio(conn, "720p", True)
        assert result == get_default_ratio("720p", True)

    def test_no_rows_returns_default(self):
        conn, cursor = _make_mock_conn(fetchone_result=None)
        result = get_render_ratio(conn, "720p", True)
        assert result == get_default_ratio("720p", True)

    def test_query_uses_parameterized(self):
        conn, cursor = _make_mock_conn(
            fetchone_result={"ratio": 0.5, "cnt": 10}
        )
        get_render_ratio(conn, "720p", True)
        params = cursor.execute.call_args[0][1]
        assert params[0] == "completed"
        assert params[1] == "720p"
        assert params[2] is True

    def test_video_enabled_false(self):
        conn, cursor = _make_mock_conn(
            fetchone_result={"ratio": 0.3, "cnt": 10}
        )
        result = get_render_ratio(conn, "1080p", False)
        assert result == 0.3


class TestFetchSongsetItems:
    def test_returns_items(self):
        rows = [
            {
                "id": "item_1",
                "songset_id": "ss_001",
                "song_id": "song_1",
                "recording_hash_prefix": "abc123",
                "position": 0,
                "gap_beats": 2.0,
                "crossfade_enabled": 0,
                "crossfade_duration_seconds": None,
                "key_shift_semitones": 0,
                "tempo_ratio": 1.0,
                "tempo_bpm": 120.0,
                "duration_seconds": 180.0,
                "song_title": "Test Song",
            },
            {
                "id": "item_2",
                "songset_id": "ss_001",
                "song_id": "song_2",
                "recording_hash_prefix": "def456",
                "position": 1,
                "gap_beats": 1.5,
                "crossfade_enabled": 1,
                "crossfade_duration_seconds": 2.0,
                "key_shift_semitones": 2,
                "tempo_ratio": 1.0,
                "tempo_bpm": 100.0,
                "duration_seconds": 200.0,
                "song_title": "Second Song",
            },
        ]
        conn, cursor = _make_mock_conn(fetchall_result=rows)
        items = fetch_songset_items(conn, "ss_001")
        assert len(items) == 2
        assert items[0].id == "item_1"
        assert items[0].song_title == "Test Song"
        assert items[1].id == "item_2"
        assert items[1].crossfade_enabled == 1

    def test_empty_result(self):
        conn, cursor = _make_mock_conn(fetchall_result=[])
        items = fetch_songset_items(conn, "ss_empty")
        assert items == []

    def test_query_joins_tables(self):
        rows = [
            {
                "id": "item_1",
                "songset_id": "ss_001",
                "song_id": "song_1",
                "recording_hash_prefix": "abc",
                "position": 0,
                "gap_beats": 2.0,
                "crossfade_enabled": 0,
                "crossfade_duration_seconds": None,
                "key_shift_semitones": 0,
                "tempo_ratio": 1.0,
                "tempo_bpm": 120.0,
                "duration_seconds": 180.0,
                "song_title": "Song",
            }
        ]
        conn, cursor = _make_mock_conn(fetchall_result=rows)
        fetch_songset_items(conn, "ss_001")
        sql = cursor.execute.call_args[0][0]
        assert "songset_items" in sql
        assert "recordings" in sql
        assert "songs" in sql
        assert "LEFT JOIN" in sql

    def test_query_uses_parameterized(self):
        conn, cursor = _make_mock_conn(fetchall_result=[])
        fetch_songset_items(conn, "ss_001")
        params = cursor.execute.call_args[0][1]
        assert params == ("ss_001",)

    def test_query_orders_by_position(self):
        conn, cursor = _make_mock_conn(fetchall_result=[])
        fetch_songset_items(conn, "ss_001")
        sql = cursor.execute.call_args[0][0]
        assert "ORDER BY si.position" in sql

    def test_null_optional_fields(self):
        rows = [
            {
                "id": "item_1",
                "songset_id": "ss_001",
                "song_id": "song_1",
                "recording_hash_prefix": None,
                "position": 0,
                "gap_beats": None,
                "crossfade_enabled": None,
                "crossfade_duration_seconds": None,
                "key_shift_semitones": None,
                "tempo_ratio": None,
                "tempo_bpm": None,
                "duration_seconds": None,
                "song_title": None,
            }
        ]
        conn, cursor = _make_mock_conn(fetchall_result=rows)
        items = fetch_songset_items(conn, "ss_001")
        assert len(items) == 1
        assert items[0].recording_hash_prefix is None
        assert items[0].song_title is None
        assert items[0].tempo_bpm is None


class TestPipelineCancelledError:
    def test_is_exception(self):
        err = PipelineCancelledError("job cancelled")
        assert isinstance(err, Exception)
        assert "job cancelled" in str(err)


class TestExecuteRenderPipeline:
    def test_full_pipeline_flow(self):
        job = _make_render_job()
        mock_conn = MagicMock()
        items = [_make_songset_item()]
        audio_result = _make_audio_result(items)
        mock_fetcher = _make_mock_fetcher()
        mock_uploader = _make_mock_uploader()

        with patch("sow_render_worker.pipeline.get_render_job", return_value=job), \
             patch("sow_render_worker.pipeline.start_render_job", return_value=job) as mock_start, \
             patch("sow_render_worker.pipeline.update_render_progress") as mock_update, \
             patch("sow_render_worker.pipeline.complete_render_job") as mock_complete, \
             patch("sow_render_worker.pipeline.fail_render_job") as mock_fail, \
             patch("sow_render_worker.pipeline.fetch_songset_items", return_value=items), \
             patch("sow_render_worker.pipeline.get_render_ratio", return_value=0.8), \
             patch("sow_render_worker.pipeline.generate_songset_audio", return_value=audio_result), \
             patch("sow_render_worker.pipeline.generate_chapters_manifest", return_value=_make_chapters_manifest()), \
             patch("sow_render_worker.pipeline.VideoEngine") as mock_ve_class, \
             patch("sow_render_worker.pipeline.Path") as mock_path_cls:

            mock_path_cls.return_value.exists.return_value = True
            mock_ve = MagicMock()
            mock_ve_class.return_value = mock_ve

            execute_render_pipeline(
                "job_abc123", 42, mock_conn,
                asset_fetcher=mock_fetcher,
                uploader=mock_uploader,
            )

            mock_start.assert_called_once_with(mock_conn, "job_abc123", 42)
            assert mock_update.call_count >= 4
            mock_ve_class.assert_called_once()
            mock_ve.generate_video.assert_called_once()
            mock_uploader.upload_render_artifacts.assert_called_once()
            mock_complete.assert_called_once_with(
                mock_conn, "job_abc123", 42,
                mp3_r2_key="renders/job_abc123/output.mp3",
                mp4_r2_key="renders/job_abc123/output.mp4",
                chapters_r2_key="renders/job_abc123/chapters.json",
            )
            mock_fail.assert_not_called()
            mock_fetcher.cleanup_temp.assert_called_once()

    def test_pipeline_no_video(self):
        job = _make_render_job(video_enabled=False)
        mock_conn = MagicMock()
        items = [_make_songset_item()]
        audio_result = _make_audio_result(items)
        mock_fetcher = _make_mock_fetcher()
        mock_uploader = _make_mock_uploader()

        with patch("sow_render_worker.pipeline.get_render_job", return_value=job), \
             patch("sow_render_worker.pipeline.start_render_job", return_value=job), \
             patch("sow_render_worker.pipeline.update_render_progress"), \
             patch("sow_render_worker.pipeline.complete_render_job"), \
             patch("sow_render_worker.pipeline.fail_render_job"), \
             patch("sow_render_worker.pipeline.fetch_songset_items", return_value=items), \
             patch("sow_render_worker.pipeline.get_render_ratio", return_value=0.4), \
             patch("sow_render_worker.pipeline.generate_songset_audio", return_value=audio_result), \
             patch("sow_render_worker.pipeline.generate_chapters_manifest", return_value=_make_chapters_manifest()), \
             patch("sow_render_worker.pipeline.VideoEngine") as mock_ve_class, \
             patch("sow_render_worker.pipeline.Path") as mock_path_cls:

            mock_path_cls.return_value.exists.return_value = True
            execute_render_pipeline(
                "job_abc123", 42, mock_conn,
                asset_fetcher=mock_fetcher,
                uploader=mock_uploader,
            )

            mock_ve_class.assert_not_called()

    def test_pipeline_job_not_found(self):
        mock_conn = MagicMock()

        with patch("sow_render_worker.pipeline.get_render_job", return_value=None):
            with pytest.raises(ValueError, match="Render job .* not found"):
                execute_render_pipeline("nonexistent", 99, mock_conn)

    def test_pipeline_empty_songset(self):
        job = _make_render_job()
        mock_conn = MagicMock()
        mock_fetcher = _make_mock_fetcher()
        mock_uploader = _make_mock_uploader()

        with patch("sow_render_worker.pipeline.get_render_job", return_value=job), \
             patch("sow_render_worker.pipeline.start_render_job", return_value=job), \
             patch("sow_render_worker.pipeline.update_render_progress"), \
             patch("sow_render_worker.pipeline.fail_render_job") as mock_fail, \
             patch("sow_render_worker.pipeline.fetch_songset_items", return_value=[]), \
             patch("sow_render_worker.pipeline.get_render_ratio", return_value=0.8):

            with pytest.raises(ValueError, match="Songset has no items"):
                execute_render_pipeline(
                    "job_abc123", 42, mock_conn,
                    asset_fetcher=mock_fetcher,
                    uploader=mock_uploader,
                )

            mock_fail.assert_called_once()

    def test_pipeline_cancellation_before_audio(self):
        job = _make_render_job()
        cancelled_job = _make_render_job(status="cancelled")
        mock_conn = MagicMock()
        mock_fetcher = _make_mock_fetcher()
        mock_uploader = _make_mock_uploader()

        call_count = [0]
        def get_job_side_effect(conn, job_id, user_id):
            call_count[0] += 1
            if call_count[0] > 1:
                return cancelled_job
            return job

        with patch("sow_render_worker.pipeline.get_render_job", side_effect=get_job_side_effect), \
             patch("sow_render_worker.pipeline.start_render_job", return_value=job), \
             patch("sow_render_worker.pipeline.update_render_progress"), \
             patch("sow_render_worker.pipeline.fail_render_job") as mock_fail, \
             patch("sow_render_worker.pipeline.fetch_songset_items", return_value=[_make_songset_item()]), \
             patch("sow_render_worker.pipeline.get_render_ratio", return_value=0.8):

            execute_render_pipeline(
                "job_abc123", 42, mock_conn,
                asset_fetcher=mock_fetcher,
                uploader=mock_uploader,
            )

            mock_fail.assert_not_called()
            mock_fetcher.cleanup_temp.assert_called_once()

    def test_pipeline_cancellation_during_video(self):
        job = _make_render_job()
        items = [_make_songset_item()]
        audio_result = _make_audio_result(items)
        cancelled_job = _make_render_job(status="cancelled")
        mock_conn = MagicMock()
        mock_fetcher = _make_mock_fetcher()
        mock_uploader = _make_mock_uploader()

        call_count = [0]
        def get_job_side_effect(conn, job_id, user_id):
            call_count[0] += 1
            if call_count[0] > 3:
                return cancelled_job
            return job

        with patch("sow_render_worker.pipeline.get_render_job", side_effect=get_job_side_effect), \
             patch("sow_render_worker.pipeline.start_render_job", return_value=job), \
             patch("sow_render_worker.pipeline.update_render_progress"), \
             patch("sow_render_worker.pipeline.fail_render_job") as mock_fail, \
             patch("sow_render_worker.pipeline.fetch_songset_items", return_value=items), \
             patch("sow_render_worker.pipeline.get_render_ratio", return_value=0.8), \
             patch("sow_render_worker.pipeline.generate_songset_audio", return_value=audio_result), \
             patch("sow_render_worker.pipeline.VideoEngine") as mock_ve_class, \
             patch("sow_render_worker.pipeline.Path") as mock_path_cls:

            mock_path_cls.return_value.exists.return_value = True
            mock_ve = MagicMock()
            mock_ve_class.return_value = mock_ve

            execute_render_pipeline(
                "job_abc123", 42, mock_conn,
                asset_fetcher=mock_fetcher,
                uploader=mock_uploader,
            )

            mock_fail.assert_not_called()
            mock_fetcher.cleanup_temp.assert_called_once()

    def test_pipeline_error_marks_job_failed(self):
        job = _make_render_job()
        mock_conn = MagicMock()
        mock_fetcher = _make_mock_fetcher()
        mock_uploader = _make_mock_uploader()

        with patch("sow_render_worker.pipeline.get_render_job", return_value=job), \
             patch("sow_render_worker.pipeline.start_render_job", return_value=job), \
             patch("sow_render_worker.pipeline.update_render_progress"), \
             patch("sow_render_worker.pipeline.fail_render_job") as mock_fail, \
             patch("sow_render_worker.pipeline.fetch_songset_items", side_effect=RuntimeError("DB error")), \
             patch("sow_render_worker.pipeline.get_render_ratio", return_value=0.8):

            with pytest.raises(RuntimeError, match="DB error"):
                execute_render_pipeline(
                    "job_abc123", 42, mock_conn,
                    asset_fetcher=mock_fetcher,
                    uploader=mock_uploader,
                )

            mock_fail.assert_called_once_with(
                mock_conn, "job_abc123", 42, "DB error"
            )
            mock_fetcher.cleanup_temp.assert_called_once()

    def test_pipeline_error_when_cancelled_does_not_mark_failed(self):
        job = _make_render_job()
        cancelled_job = _make_render_job(status="cancelled")
        mock_conn = MagicMock()
        mock_fetcher = _make_mock_fetcher()
        mock_uploader = _make_mock_uploader()

        call_count = [0]
        def get_job_side_effect(conn, job_id, user_id):
            call_count[0] += 1
            if call_count[0] > 1:
                return cancelled_job
            return job

        with patch("sow_render_worker.pipeline.get_render_job", side_effect=get_job_side_effect), \
             patch("sow_render_worker.pipeline.start_render_job", return_value=job), \
             patch("sow_render_worker.pipeline.update_render_progress"), \
             patch("sow_render_worker.pipeline.fail_render_job") as mock_fail, \
             patch("sow_render_worker.pipeline.fetch_songset_items", side_effect=RuntimeError("some error")), \
             patch("sow_render_worker.pipeline.get_render_ratio", return_value=0.8):

            execute_render_pipeline(
                "job_abc123", 42, mock_conn,
                asset_fetcher=mock_fetcher,
                uploader=mock_uploader,
            )

            mock_fail.assert_not_called()

    def test_pipeline_cleanup_on_success(self):
        job = _make_render_job()
        items = [_make_songset_item()]
        audio_result = _make_audio_result(items)
        mock_conn = MagicMock()
        mock_fetcher = _make_mock_fetcher()
        mock_uploader = _make_mock_uploader()

        with patch("sow_render_worker.pipeline.get_render_job", return_value=job), \
             patch("sow_render_worker.pipeline.start_render_job", return_value=job), \
             patch("sow_render_worker.pipeline.update_render_progress"), \
             patch("sow_render_worker.pipeline.complete_render_job"), \
             patch("sow_render_worker.pipeline.fail_render_job"), \
             patch("sow_render_worker.pipeline.fetch_songset_items", return_value=items), \
             patch("sow_render_worker.pipeline.get_render_ratio", return_value=0.8), \
             patch("sow_render_worker.pipeline.generate_songset_audio", return_value=audio_result), \
             patch("sow_render_worker.pipeline.generate_chapters_manifest", return_value=_make_chapters_manifest()), \
             patch("sow_render_worker.pipeline.VideoEngine") as mock_ve_class, \
             patch("sow_render_worker.pipeline.Path") as mock_path_cls:

            mock_path_cls.return_value.exists.return_value = True
            mock_ve = MagicMock()
            mock_ve_class.return_value = mock_ve

            execute_render_pipeline(
                "job_abc123", 42, mock_conn,
                asset_fetcher=mock_fetcher,
                uploader=mock_uploader,
            )

            mock_fetcher.cleanup_temp.assert_called_once()

    def test_pipeline_cleanup_on_error(self):
        job = _make_render_job()
        mock_conn = MagicMock()
        mock_fetcher = _make_mock_fetcher()
        mock_uploader = _make_mock_uploader()

        with patch("sow_render_worker.pipeline.get_render_job", return_value=job), \
             patch("sow_render_worker.pipeline.start_render_job", return_value=job), \
             patch("sow_render_worker.pipeline.update_render_progress"), \
             patch("sow_render_worker.pipeline.fail_render_job"), \
             patch("sow_render_worker.pipeline.fetch_songset_items", side_effect=RuntimeError("boom")), \
             patch("sow_render_worker.pipeline.get_render_ratio", return_value=0.8):

            with pytest.raises(RuntimeError, match="boom"):
                execute_render_pipeline(
                    "job_abc123", 42, mock_conn,
                    asset_fetcher=mock_fetcher,
                    uploader=mock_uploader,
                )

            mock_fetcher.cleanup_temp.assert_called_once()

    def test_pipeline_cleanup_failure_does_not_suppress_original(self):
        job = _make_render_job()
        mock_conn = MagicMock()
        mock_fetcher = _make_mock_fetcher()
        mock_fetcher.cleanup_temp.side_effect = RuntimeError("cleanup failed")
        mock_uploader = _make_mock_uploader()

        with patch("sow_render_worker.pipeline.get_render_job", return_value=job), \
             patch("sow_render_worker.pipeline.start_render_job", return_value=job), \
             patch("sow_render_worker.pipeline.update_render_progress"), \
             patch("sow_render_worker.pipeline.fail_render_job"), \
             patch("sow_render_worker.pipeline.fetch_songset_items", side_effect=RuntimeError("original error")), \
             patch("sow_render_worker.pipeline.get_render_ratio", return_value=0.8):

            with pytest.raises(RuntimeError, match="original error"):
                execute_render_pipeline(
                    "job_abc123", 42, mock_conn,
                    asset_fetcher=mock_fetcher,
                    uploader=mock_uploader,
                )

    def test_pipeline_progress_updates_through_phases(self):
        job = _make_render_job()
        items = [_make_songset_item()]
        audio_result = _make_audio_result(items)
        mock_conn = MagicMock()
        mock_fetcher = _make_mock_fetcher()
        mock_uploader = _make_mock_uploader()

        with patch("sow_render_worker.pipeline.get_render_job", return_value=job), \
             patch("sow_render_worker.pipeline.start_render_job", return_value=job), \
             patch("sow_render_worker.pipeline.update_render_progress") as mock_update, \
             patch("sow_render_worker.pipeline.complete_render_job"), \
             patch("sow_render_worker.pipeline.fail_render_job"), \
             patch("sow_render_worker.pipeline.fetch_songset_items", return_value=items), \
             patch("sow_render_worker.pipeline.get_render_ratio", return_value=0.8), \
             patch("sow_render_worker.pipeline.generate_songset_audio", return_value=audio_result), \
             patch("sow_render_worker.pipeline.generate_chapters_manifest", return_value=_make_chapters_manifest()), \
             patch("sow_render_worker.pipeline.VideoEngine") as mock_ve_class, \
             patch("sow_render_worker.pipeline.Path") as mock_path_cls:

            mock_path_cls.return_value.exists.return_value = True
            mock_ve = MagicMock()
            mock_ve_class.return_value = mock_ve

            execute_render_pipeline(
                "job_abc123", 42, mock_conn,
                asset_fetcher=mock_fetcher,
                uploader=mock_uploader,
            )

            phases_seen = [
                call[0][3].phase for call in mock_update.call_args_list
            ]
            assert "preparing" in phases_seen
            assert "mixing_audio" in phases_seen
            assert "rendering_frames" in phases_seen
            assert "encoding_video" in phases_seen
            assert "uploading" in phases_seen

            progress_objs = [call[0][3] for call in mock_update.call_args_list]
            for p in progress_objs:
                if p.phase_index is not None:
                    assert p.percent_complete is not None
                    assert p.percent_complete == (p.phase_index / len(PHASES)) * 100

    def test_pipeline_video_progress_callback_updates_percent_complete(self):
        job = _make_render_job()
        items = [_make_songset_item()]
        audio_result = _make_audio_result(items)
        mock_conn = MagicMock()
        mock_fetcher = _make_mock_fetcher()
        mock_uploader = _make_mock_uploader()

        def fake_generate_video(audio_path, segments, output_path, progress_callback=None, timeout_check_callback=None, job_id=None):
            if progress_callback:
                progress_callback(1500, 3000)

        with patch("sow_render_worker.pipeline.get_render_job", return_value=job), \
             patch("sow_render_worker.pipeline.start_render_job", return_value=job), \
             patch("sow_render_worker.pipeline.update_render_progress") as mock_update, \
             patch("sow_render_worker.pipeline.complete_render_job"), \
             patch("sow_render_worker.pipeline.fail_render_job"), \
             patch("sow_render_worker.pipeline.fetch_songset_items", return_value=items), \
             patch("sow_render_worker.pipeline.get_render_ratio", return_value=0.8), \
             patch("sow_render_worker.pipeline.generate_songset_audio", return_value=audio_result), \
             patch("sow_render_worker.pipeline.generate_chapters_manifest", return_value=_make_chapters_manifest()), \
             patch("sow_render_worker.pipeline.VideoEngine") as mock_ve_class, \
             patch("sow_render_worker.pipeline.Path") as mock_path_cls, \
             patch("sow_render_worker.pipeline.time") as mock_time:

            mock_path_cls.return_value.exists.return_value = True
            mock_ve = MagicMock()
            mock_ve.fps = 30
            mock_ve.generate_video = MagicMock(side_effect=fake_generate_video)
            mock_ve_class.return_value = mock_ve

            mock_time.monotonic.side_effect = [0, 0, 0, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5]

            execute_render_pipeline(
                "job_abc123", 42, mock_conn,
                asset_fetcher=mock_fetcher,
                uploader=mock_uploader,
            )

            encoding_video_calls = [
                call for call in mock_update.call_args_list
                if call[0][3].phase == "encoding_video"
            ]
            assert len(encoding_video_calls) >= 1

            callback_progress = encoding_video_calls[-1][0][3]
            phase_base = PHASES.index("encoding_video") / len(PHASES) * 100
            phase_weight = 1 / len(PHASES) * 100
            expected_percent = phase_base + 0.5 * phase_weight
            assert abs(callback_progress.percent_complete - expected_percent) < 0.01

    def test_pipeline_estimated_seconds_left_at_each_phase(self):
        job = _make_render_job()
        items = [_make_songset_item()]
        audio_result = _make_audio_result(items)
        mock_conn = MagicMock()
        mock_fetcher = _make_mock_fetcher()
        mock_uploader = _make_mock_uploader()

        with patch("sow_render_worker.pipeline.get_render_job", return_value=job), \
             patch("sow_render_worker.pipeline.start_render_job", return_value=job), \
             patch("sow_render_worker.pipeline.update_render_progress") as mock_update, \
             patch("sow_render_worker.pipeline.complete_render_job"), \
             patch("sow_render_worker.pipeline.fail_render_job"), \
             patch("sow_render_worker.pipeline.fetch_songset_items", return_value=items), \
             patch("sow_render_worker.pipeline.get_render_ratio", return_value=0.8), \
             patch("sow_render_worker.pipeline.generate_songset_audio", return_value=audio_result), \
             patch("sow_render_worker.pipeline.generate_chapters_manifest", return_value=_make_chapters_manifest()), \
             patch("sow_render_worker.pipeline.VideoEngine") as mock_ve_class, \
             patch("sow_render_worker.pipeline.Path") as mock_path_cls:

            mock_path_cls.return_value.exists.return_value = True
            mock_ve = MagicMock()
            mock_ve_class.return_value = mock_ve

            execute_render_pipeline(
                "job_abc123", 42, mock_conn,
                asset_fetcher=mock_fetcher,
                uploader=mock_uploader,
            )

            for call in mock_update.call_args_list:
                progress = call[0][3]
                if progress.phase_index is not None and progress.phase_index > 0:
                    assert progress.estimated_seconds_left is not None
                    assert progress.estimated_seconds_left >= 0

    def test_pipeline_video_progress_callback_stops_when_job_not_running(self):
        job = _make_render_job()
        items = [_make_songset_item()]
        audio_result = _make_audio_result(items)
        mock_conn = MagicMock()
        mock_fetcher = _make_mock_fetcher()
        mock_uploader = _make_mock_uploader()

        def fake_generate_video(audio_path, segments, output_path, progress_callback=None, timeout_check_callback=None, job_id=None):
            if progress_callback:
                progress_callback(500, 3000)
                progress_callback(1000, 3000)
                progress_callback(1500, 3000)

        encoding_callback_count = [0]

        def mock_update_side_effect(conn, job_id, user_id, progress):
            if progress.phase == "encoding_video":
                encoding_callback_count[0] += 1
                if encoding_callback_count[0] >= 2:
                    return None
            return MagicMock()

        with patch("sow_render_worker.pipeline.get_render_job", return_value=job), \
             patch("sow_render_worker.pipeline.start_render_job", return_value=job), \
             patch("sow_render_worker.pipeline.update_render_progress", side_effect=mock_update_side_effect) as mock_update, \
             patch("sow_render_worker.pipeline.complete_render_job"), \
             patch("sow_render_worker.pipeline.fail_render_job"), \
             patch("sow_render_worker.pipeline.fetch_songset_items", return_value=items), \
             patch("sow_render_worker.pipeline.get_render_ratio", return_value=0.8), \
             patch("sow_render_worker.pipeline.generate_songset_audio", return_value=audio_result), \
             patch("sow_render_worker.pipeline.generate_chapters_manifest", return_value=_make_chapters_manifest()), \
             patch("sow_render_worker.pipeline.VideoEngine") as mock_ve_class, \
             patch("sow_render_worker.pipeline.Path") as mock_path_cls, \
             patch("sow_render_worker.pipeline.time") as mock_time:

            mock_path_cls.return_value.exists.return_value = True
            mock_ve = MagicMock()
            mock_ve.fps = 30
            mock_ve.generate_video = MagicMock(side_effect=fake_generate_video)
            mock_ve_class.return_value = mock_ve

            mock_time.monotonic.side_effect = [0, 0, 0, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5]

            execute_render_pipeline(
                "job_abc123", 42, mock_conn,
                asset_fetcher=mock_fetcher,
                uploader=mock_uploader,
            )

            encoding_video_update_calls = [
                call for call in mock_update.call_args_list
                if call[0][3].phase == "encoding_video"
            ]
            assert len(encoding_video_update_calls) == 2
            assert encoding_callback_count[0] == 2

    def test_pipeline_fail_render_job_error_is_swallowed(self):
        job = _make_render_job()
        mock_conn = MagicMock()
        mock_fetcher = _make_mock_fetcher()
        mock_uploader = _make_mock_uploader()

        with patch("sow_render_worker.pipeline.get_render_job", return_value=job), \
             patch("sow_render_worker.pipeline.start_render_job", return_value=job), \
             patch("sow_render_worker.pipeline.update_render_progress"), \
             patch("sow_render_worker.pipeline.fail_render_job", side_effect=RuntimeError("fail also failed")), \
             patch("sow_render_worker.pipeline.fetch_songset_items", side_effect=RuntimeError("original")), \
             patch("sow_render_worker.pipeline.get_render_ratio", return_value=0.8):

            with pytest.raises(RuntimeError, match="original"):
                execute_render_pipeline(
                    "job_abc123", 42, mock_conn,
                    asset_fetcher=mock_fetcher,
                    uploader=mock_uploader,
                )

    def test_pipeline_creates_default_asset_fetcher(self):
        job = _make_render_job()
        items = [_make_songset_item()]
        audio_result = _make_audio_result(items)
        mock_conn = MagicMock()
        mock_uploader = _make_mock_uploader()

        with patch("sow_render_worker.pipeline.get_render_job", return_value=job), \
             patch("sow_render_worker.pipeline.start_render_job", return_value=job), \
             patch("sow_render_worker.pipeline.update_render_progress"), \
             patch("sow_render_worker.pipeline.complete_render_job"), \
             patch("sow_render_worker.pipeline.fail_render_job"), \
             patch("sow_render_worker.pipeline.fetch_songset_items", return_value=items), \
             patch("sow_render_worker.pipeline.get_render_ratio", return_value=0.8), \
             patch("sow_render_worker.pipeline.generate_songset_audio", return_value=audio_result), \
             patch("sow_render_worker.pipeline.generate_chapters_manifest", return_value=_make_chapters_manifest()), \
             patch("sow_render_worker.pipeline.AssetFetcher") as mock_af_class, \
             patch("sow_render_worker.pipeline.R2Uploader", return_value=mock_uploader), \
             patch("sow_render_worker.pipeline.VideoEngine") as mock_ve_class, \
             patch("sow_render_worker.pipeline.Path") as mock_path_cls:

            mock_path_cls.return_value.exists.return_value = True
            mock_af = MagicMock()
            mock_af.get_job_temp_dir.return_value = Path("/tmp/sow-test")
            mock_af_class.return_value = mock_af
            mock_ve = MagicMock()
            mock_ve_class.return_value = mock_ve

            execute_render_pipeline(
                "job_abc123", 42, mock_conn,
                uploader=mock_uploader,
            )

            mock_af_class.assert_called_once()
            mock_af.initialize.assert_called_once()

    def test_pipeline_audio_disabled_skips_mp3_upload(self):
        job = _make_render_job(audio_enabled=False)
        items = [_make_songset_item()]
        audio_result = _make_audio_result(items)
        mock_conn = MagicMock()
        mock_fetcher = _make_mock_fetcher()
        mock_uploader = _make_mock_uploader()

        with patch("sow_render_worker.pipeline.get_render_job", return_value=job), \
             patch("sow_render_worker.pipeline.start_render_job", return_value=job), \
             patch("sow_render_worker.pipeline.update_render_progress"), \
             patch("sow_render_worker.pipeline.complete_render_job"), \
             patch("sow_render_worker.pipeline.fail_render_job"), \
             patch("sow_render_worker.pipeline.fetch_songset_items", return_value=items), \
             patch("sow_render_worker.pipeline.get_render_ratio", return_value=0.8), \
             patch("sow_render_worker.pipeline.generate_songset_audio", return_value=audio_result), \
             patch("sow_render_worker.pipeline.generate_chapters_manifest", return_value=_make_chapters_manifest()), \
             patch("sow_render_worker.pipeline.VideoEngine") as mock_ve_class, \
             patch("sow_render_worker.pipeline.Path") as mock_path_cls:

            mock_path_cls.return_value.exists.return_value = True
            mock_ve = MagicMock()
            mock_ve_class.return_value = mock_ve

            execute_render_pipeline(
                "job_abc123", 42, mock_conn,
                asset_fetcher=mock_fetcher,
                uploader=mock_uploader,
            )

            call_args = mock_uploader.upload_render_artifacts.call_args
            artifacts = call_args[0][1]
            assert artifacts.mp3_path is None

    def test_pipeline_estimated_total_seconds(self):
        job = _make_render_job()
        items = [_make_songset_item()]
        audio_result = _make_audio_result(items)
        mock_conn = MagicMock()
        mock_fetcher = _make_mock_fetcher()
        mock_uploader = _make_mock_uploader()

        with patch("sow_render_worker.pipeline.get_render_job", return_value=job), \
             patch("sow_render_worker.pipeline.start_render_job", return_value=job), \
             patch("sow_render_worker.pipeline.update_render_progress") as mock_update, \
             patch("sow_render_worker.pipeline.complete_render_job"), \
             patch("sow_render_worker.pipeline.fail_render_job"), \
             patch("sow_render_worker.pipeline.fetch_songset_items", return_value=items), \
             patch("sow_render_worker.pipeline.get_render_ratio", return_value=0.8), \
             patch("sow_render_worker.pipeline.generate_songset_audio", return_value=audio_result), \
             patch("sow_render_worker.pipeline.generate_chapters_manifest", return_value=_make_chapters_manifest()), \
             patch("sow_render_worker.pipeline.VideoEngine") as mock_ve_class, \
             patch("sow_render_worker.pipeline.Path") as mock_path_cls:

            mock_path_cls.return_value.exists.return_value = True
            mock_ve = MagicMock()
            mock_ve_class.return_value = mock_ve

            execute_render_pipeline(
                "job_abc123", 42, mock_conn,
                asset_fetcher=mock_fetcher,
                uploader=mock_uploader,
            )

            mixing_audio_call = None
            for call in mock_update.call_args_list:
                progress = call[0][3]
                if progress.phase == "mixing_audio":
                    mixing_audio_call = progress
                    break

            assert mixing_audio_call is not None
            assert mixing_audio_call.estimated_total_seconds == 180.0 * 0.8
            assert mixing_audio_call.total_duration_seconds == 180.0

    def test_pipeline_skips_when_job_already_claimed(self):
        job = _make_render_job()
        mock_conn = MagicMock()
        mock_fetcher = _make_mock_fetcher()
        mock_uploader = _make_mock_uploader()

        with patch("sow_render_worker.pipeline.get_render_job", return_value=job), \
             patch("sow_render_worker.pipeline.start_render_job", return_value=None), \
             patch("sow_render_worker.pipeline.reclaim_stale_job", return_value=None), \
             patch("sow_render_worker.pipeline.update_render_progress") as mock_update, \
             patch("sow_render_worker.pipeline.fail_render_job") as mock_fail:

            execute_render_pipeline(
                "job_abc123", 42, mock_conn,
                asset_fetcher=mock_fetcher,
                uploader=mock_uploader,
            )

            mock_update.assert_not_called()
            mock_fail.assert_not_called()

    def test_pipeline_uses_fallback_estimate_when_duration_missing(self):
        job = _make_render_job()
        mock_conn = MagicMock()
        mock_fetcher = _make_mock_fetcher()
        mock_uploader = _make_mock_uploader()
        items = [_make_songset_item(duration_seconds=None)]
        audio_result = _make_audio_result(items)

        with patch("sow_render_worker.pipeline.get_render_job", return_value=job), \
             patch("sow_render_worker.pipeline.start_render_job", return_value=job), \
             patch("sow_render_worker.pipeline.update_render_progress"), \
             patch("sow_render_worker.pipeline.complete_render_job"), \
             patch("sow_render_worker.pipeline.fail_render_job") as mock_fail, \
             patch("sow_render_worker.pipeline.fetch_songset_items", return_value=items), \
             patch("sow_render_worker.pipeline.get_render_ratio", return_value=0.8), \
             patch("sow_render_worker.pipeline.generate_songset_audio", return_value=audio_result), \
             patch("sow_render_worker.pipeline.generate_chapters_manifest", return_value=_make_chapters_manifest()), \
             patch("sow_render_worker.pipeline.VideoEngine") as mock_ve_class, \
             patch("sow_render_worker.pipeline.Path") as mock_path_cls:

            mock_path_cls.return_value.exists.return_value = True
            mock_ve = MagicMock()
            mock_ve_class.return_value = mock_ve

            execute_render_pipeline(
                "job_abc123", 42, mock_conn,
                asset_fetcher=mock_fetcher,
                uploader=mock_uploader,
            )

            mock_fail.assert_not_called()

    def test_pipeline_fallback_estimate_multiplies_by_item_count(self):
        job = _make_render_job()
        mock_conn = MagicMock()
        mock_fetcher = _make_mock_fetcher()
        mock_uploader = _make_mock_uploader()
        items = [_make_songset_item(duration_seconds=None) for _ in range(3)]
        audio_result = _make_audio_result(items)

        with patch("sow_render_worker.pipeline.get_render_job", return_value=job), \
             patch("sow_render_worker.pipeline.start_render_job", return_value=job), \
             patch("sow_render_worker.pipeline.update_render_progress"), \
             patch("sow_render_worker.pipeline.complete_render_job"), \
             patch("sow_render_worker.pipeline.fail_render_job") as mock_fail, \
             patch("sow_render_worker.pipeline.fetch_songset_items", return_value=items), \
             patch("sow_render_worker.pipeline.get_render_ratio", return_value=0.8), \
             patch("sow_render_worker.pipeline.generate_songset_audio", return_value=audio_result), \
             patch("sow_render_worker.pipeline.generate_chapters_manifest", return_value=_make_chapters_manifest()), \
             patch("sow_render_worker.pipeline.VideoEngine") as mock_ve_class, \
             patch("sow_render_worker.pipeline.Path") as mock_path_cls:

            mock_path_cls.return_value.exists.return_value = True
            mock_ve = MagicMock()
            mock_ve_class.return_value = mock_ve

            execute_render_pipeline(
                "job_abc123", 42, mock_conn,
                asset_fetcher=mock_fetcher,
                uploader=mock_uploader,
            )

            mock_fail.assert_not_called()
