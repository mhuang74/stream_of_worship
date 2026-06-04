from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, call, patch

import pytest

from sow_render_worker.db import (
    ORPHANED_JOB_THRESHOLD_MINUTES,
    PHASE_ORDER,
    STALE_JOB_THRESHOLD_SECONDS,
    TOTAL_PHASES,
    RenderJob,
    RenderProgress,
    _normalize_font_family,
    complete_render_job,
    fail_render_job,
    get_connection,
    get_phase_index,
    get_render_job,
    reclaim_stale_job,
    recover_orphaned_jobs,
    start_render_job,
    update_render_progress,
)


def _make_row(**overrides) -> dict:
    defaults = {
        "id": "job_abc123",
        "songset_id": "ss_001",
        "user_id": 42,
        "status": "queued",
        "phase": "preparing",
        "phase_index": 0,
        "total_phases": 5,
        "percent_complete": 0.0,
        "estimated_seconds_left": None,
        "elapsed_seconds": 0.0,
        "error_message": None,
        "estimated_total_seconds": None,
        "total_duration_seconds": None,
        "started_at": None,
        "template": "dark",
        "resolution": "720p",
        "audio_enabled": True,
        "video_enabled": True,
        "font_size_preset": "M",
        "font_family": "noto_serif_tc",
        "include_title_card": False,
        "title_card_duration_seconds": None,
        "mp3_r2_key": None,
        "mp4_r2_key": None,
        "chapters_r2_key": None,
        "created_at": datetime(2025, 1, 1, tzinfo=timezone.utc),
        "updated_at": datetime(2025, 1, 1, tzinfo=timezone.utc),
        "completed_at": None,
    }
    defaults.update(overrides)
    return defaults


def _make_mock_conn(fetchone_result=None, fetchall_result=None, rowcount=None):
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value.__enter__ = MagicMock(return_value=cursor)
    conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    cursor.fetchone.return_value = fetchone_result
    cursor.fetchall.return_value = fetchall_result or []
    cursor.rowcount = rowcount if rowcount is not None else 0
    return conn, cursor


class TestPhaseConstants:
    def test_total_phases(self):
        assert TOTAL_PHASES == 5

    def test_phase_order(self):
        assert PHASE_ORDER == [
            "preparing",
            "mixing_audio",
            "rendering_frames",
            "encoding_video",
            "uploading",
        ]

    def test_orphaned_threshold(self):
        assert ORPHANED_JOB_THRESHOLD_MINUTES == 15


class TestGetPhaseIndex:
    @pytest.mark.parametrize(
        "phase,expected",
        [
            ("preparing", 0),
            ("mixing_audio", 1),
            ("rendering_frames", 2),
            ("encoding_video", 3),
            ("uploading", 4),
            ("completed", 5),
        ],
    )
    def test_valid_phases(self, phase, expected):
        assert get_phase_index(phase) == expected

    def test_unknown_phase(self):
        assert get_phase_index("unknown") == -1


class TestGetConnection:
    def test_with_explicit_url(self):
        with patch("sow_render_worker.db.psycopg2.connect") as mock_connect:
            mock_connect.return_value = MagicMock()
            conn = get_connection("postgresql://user:pass@localhost/db")
            mock_connect.assert_called_once_with(
                "postgresql://user:pass@localhost/db",
                keepalives=1,
                keepalives_idle=60,
                keepalives_interval=10,
                keepalives_count=5,
            )

    def test_from_env_var(self):
        with patch("sow_render_worker.db.psycopg2.connect") as mock_connect:
            mock_connect.return_value = MagicMock()
            with patch.dict("os.environ", {"SOW_DATABASE_URL": "postgresql://env:pass@host/db"}):
                conn = get_connection()
                mock_connect.assert_called_once_with(
                    "postgresql://env:pass@host/db",
                    keepalives=1,
                    keepalives_idle=60,
                    keepalives_interval=10,
                    keepalives_count=5,
                )

    def test_missing_url_raises(self):
        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(ValueError, match="SOW_DATABASE_URL is required"):
                get_connection()


class TestGetRenderJob:
    def test_found(self):
        row = _make_row()
        conn, cursor = _make_mock_conn(fetchone_result=row)
        result = get_render_job(conn, "job_abc123", 42)
        assert result is not None
        assert result.id == "job_abc123"
        assert result.user_id == 42
        assert result.status == "queued"
        cursor.execute.assert_called_once()
        sql = cursor.execute.call_args[0][0]
        assert "SELECT" in sql
        assert "FROM render_jobs" in sql
        assert "WHERE id = %s AND user_id = %s" in sql

    def test_not_found(self):
        conn, cursor = _make_mock_conn(fetchone_result=None)
        result = get_render_job(conn, "nonexistent", 99)
        assert result is None

    def test_parameterized_query(self):
        row = _make_row()
        conn, cursor = _make_mock_conn(fetchone_result=row)
        get_render_job(conn, "job_abc123", 42)
        args = cursor.execute.call_args[0]
        assert args[1] == ("job_abc123", 42)


class TestStartRenderJob:
    def test_success(self):
        row = _make_row(status="running")
        conn, cursor = _make_mock_conn(fetchone_result=row)
        result = start_render_job(conn, "job_abc123", 42)
        assert result is not None
        assert result.status == "running"
        sql = cursor.execute.call_args[0][0]
        assert "UPDATE render_jobs" in sql
        assert "SET status = %s" in sql
        assert "RETURNING *" in sql
        assert "AND status = %s" in sql

    def test_not_found(self):
        conn, cursor = _make_mock_conn(fetchone_result=None)
        result = start_render_job(conn, "nonexistent", 99)
        assert result is None

    def test_parameterized_query(self):
        row = _make_row(status="running")
        conn, cursor = _make_mock_conn(fetchone_result=row)
        start_render_job(conn, "job_abc123", 42)
        params = cursor.execute.call_args[0][1]
        assert params[0] == "running"
        assert params[3] == "job_abc123"
        assert params[4] == 42
        assert params[5] == "queued"

    def test_uses_coalesce_for_started_at(self):
        row = _make_row(status="running")
        conn, cursor = _make_mock_conn(fetchone_result=row)
        start_render_job(conn, "job_abc123", 42)
        sql = cursor.execute.call_args[0][0]
        assert "COALESCE(started_at" in sql


class TestUpdateRenderProgress:
    def test_update_phase(self):
        row = _make_row(phase="mixing_audio", phase_index=1)
        conn, cursor = _make_mock_conn(fetchone_result=row)
        progress = RenderProgress(phase="mixing_audio")
        result = update_render_progress(conn, "job_abc123", 42, progress)
        assert result is not None
        assert result.phase == "mixing_audio"
        sql = cursor.execute.call_args[0][0]
        assert "UPDATE render_jobs" in sql
        assert "phase = %s" in sql
        assert "phase_index = %s" in sql

    def test_update_elapsed_seconds(self):
        row = _make_row(elapsed_seconds=30.0)
        conn, cursor = _make_mock_conn(fetchone_result=row)
        progress = RenderProgress(elapsed_seconds=30.0)
        result = update_render_progress(conn, "job_abc123", 42, progress)
        assert result is not None
        params = cursor.execute.call_args[0][1]
        assert 30.0 in params

    def test_update_estimated_total_seconds(self):
        row = _make_row(estimated_total_seconds=120.0)
        conn, cursor = _make_mock_conn(fetchone_result=row)
        progress = RenderProgress(estimated_total_seconds=120.0)
        result = update_render_progress(conn, "job_abc123", 42, progress)
        assert result is not None

    def test_update_total_duration_seconds(self):
        row = _make_row(total_duration_seconds=180.5)
        conn, cursor = _make_mock_conn(fetchone_result=row)
        progress = RenderProgress(total_duration_seconds=180.5)
        result = update_render_progress(conn, "job_abc123", 42, progress)
        assert result is not None

    def test_update_started_at(self):
        started = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        row = _make_row(started_at=started)
        conn, cursor = _make_mock_conn(fetchone_result=row)
        progress = RenderProgress(started_at=started)
        result = update_render_progress(conn, "job_abc123", 42, progress)
        assert result is not None

    def test_no_updates_returns_current_job(self):
        row = _make_row()
        conn, cursor = _make_mock_conn(fetchone_result=row)
        progress = RenderProgress()
        result = update_render_progress(conn, "job_abc123", 42, progress)
        assert result is not None
        assert cursor.execute.call_count == 1
        sql = cursor.execute.call_args[0][0]
        assert "SELECT" in sql

    def test_not_found(self):
        conn, cursor = _make_mock_conn(fetchone_result=None)
        progress = RenderProgress(phase="mixing_audio")
        result = update_render_progress(conn, "nonexistent", 99, progress)
        assert result is None

    def test_multiple_fields_in_single_update(self):
        row = _make_row(phase="encoding_video", phase_index=3, elapsed_seconds=60.0)
        conn, cursor = _make_mock_conn(fetchone_result=row)
        progress = RenderProgress(phase="encoding_video", elapsed_seconds=60.0)
        result = update_render_progress(conn, "job_abc123", 42, progress)
        assert result is not None
        sql = cursor.execute.call_args[0][0]
        assert "phase = %s" in sql
        assert "phase_index = %s" in sql
        assert "elapsed_seconds = %s" in sql

    def test_update_percent_complete(self):
        row = _make_row(percent_complete=60.0)
        conn, cursor = _make_mock_conn(fetchone_result=row)
        progress = RenderProgress(percent_complete=60.0)
        result = update_render_progress(conn, "job_abc123", 42, progress)
        assert result is not None
        params = cursor.execute.call_args[0][1]
        assert 60.0 in params

    def test_update_estimated_seconds_left(self):
        row = _make_row(estimated_seconds_left=120.0)
        conn, cursor = _make_mock_conn(fetchone_result=row)
        progress = RenderProgress(estimated_seconds_left=120.0)
        result = update_render_progress(conn, "job_abc123", 42, progress)
        assert result is not None
        params = cursor.execute.call_args[0][1]
        assert 120.0 in params

    def test_status_guard_running(self):
        row = _make_row(phase="encoding_video", phase_index=3)
        conn, cursor = _make_mock_conn(fetchone_result=row)
        progress = RenderProgress(phase="encoding_video")
        result = update_render_progress(conn, "job_abc123", 42, progress)
        assert result is not None
        sql = cursor.execute.call_args[0][0]
        assert "AND status = 'running'" in sql

    def test_status_guard_returns_none_when_not_running(self):
        conn, cursor = _make_mock_conn(fetchone_result=None)
        progress = RenderProgress(phase="encoding_video")
        result = update_render_progress(conn, "job_abc123", 42, progress)
        assert result is None

    def test_parameterized_no_string_interpolation(self):
        row = _make_row(phase="mixing_audio", phase_index=1)
        conn, cursor = _make_mock_conn(fetchone_result=row)
        progress = RenderProgress(phase="mixing_audio")
        update_render_progress(conn, "job_abc123", 42, progress)
        params = cursor.execute.call_args[0][1]
        assert "mixing_audio" in params
        assert 1 in params
        assert "job_abc123" in params
        assert 42 in params


class TestCompleteRenderJob:
    def test_success(self):
        started = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        row = _make_row(
            status="completed",
            phase="completed",
            phase_index=5,
            percent_complete=100.0,
            mp3_r2_key="renders/job_abc123/output.mp3",
            mp4_r2_key="renders/job_abc123/output.mp4",
            chapters_r2_key="renders/job_abc123/chapters.json",
            completed_at=datetime(2025, 6, 1, 12, 5, 0, tzinfo=timezone.utc),
        )
        conn, cursor = _make_mock_conn(fetchone_result=row)

        get_row = _make_row(started_at=started)
        get_conn, get_cursor = _make_mock_conn(fetchone_result=get_row)
        conn.cursor.side_effect = get_conn.cursor.side_effect

        with patch("sow_render_worker.db.get_render_job", return_value=RenderJob(
            id="job_abc123", songset_id="ss_001", user_id=42, status="running",
            started_at=started,
        )):
            result = complete_render_job(
                conn, "job_abc123", 42,
                mp3_r2_key="renders/job_abc123/output.mp3",
                mp4_r2_key="renders/job_abc123/output.mp4",
                chapters_r2_key="renders/job_abc123/chapters.json",
            )
        assert result is not None
        assert result.status == "completed"
        assert result.phase == "completed"
        assert result.phase_index == 5

    def test_elapsed_seconds_calculated(self):
        started = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        row = _make_row(status="completed", elapsed_seconds=300.0)
        conn, cursor = _make_mock_conn(fetchone_result=row)

        with patch("sow_render_worker.db.get_render_job", return_value=RenderJob(
            id="job_abc123", songset_id="ss_001", user_id=42, status="running",
            started_at=started,
        )):
            result = complete_render_job(conn, "job_abc123", 42)
        assert result is not None
        sql = cursor.execute.call_args[0][0]
        assert "elapsed_seconds = %s" in sql

    def test_no_started_at_sets_elapsed_null(self):
        row = _make_row(status="completed", elapsed_seconds=None)
        conn, cursor = _make_mock_conn(fetchone_result=row)

        with patch("sow_render_worker.db.get_render_job", return_value=RenderJob(
            id="job_abc123", songset_id="ss_001", user_id=42, status="running",
            started_at=None,
        )):
            result = complete_render_job(conn, "job_abc123", 42)
        assert result is not None
        params = cursor.execute.call_args[0][1]
        assert params[4] is None

    def test_job_not_found(self):
        conn, cursor = _make_mock_conn(fetchone_result=None)
        with patch("sow_render_worker.db.get_render_job", return_value=None):
            result = complete_render_job(conn, "nonexistent", 99)
        assert result is None

    def test_r2_keys_set(self):
        row = _make_row(
            status="completed",
            mp3_r2_key="renders/job/output.mp3",
            mp4_r2_key="renders/job/output.mp4",
            chapters_r2_key="renders/job/chapters.json",
        )
        conn, cursor = _make_mock_conn(fetchone_result=row)

        with patch("sow_render_worker.db.get_render_job", return_value=RenderJob(
            id="job_abc123", songset_id="ss_001", user_id=42, status="running",
            started_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        )):
            result = complete_render_job(
                conn, "job_abc123", 42,
                mp3_r2_key="renders/job/output.mp3",
                mp4_r2_key="renders/job/output.mp4",
                chapters_r2_key="renders/job/chapters.json",
            )
        params = cursor.execute.call_args[0][1]
        assert "renders/job/output.mp3" in params
        assert "renders/job/output.mp4" in params
        assert "renders/job/chapters.json" in params

    def test_parameterized_query(self):
        row = _make_row(status="completed")
        conn, cursor = _make_mock_conn(fetchone_result=row)

        with patch("sow_render_worker.db.get_render_job", return_value=RenderJob(
            id="job_abc123", songset_id="ss_001", user_id=42, status="running",
            started_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        )):
            complete_render_job(conn, "job_abc123", 42)
        sql = cursor.execute.call_args[0][0]
        assert "%s" in sql
        assert "WHERE id = %s AND user_id = %s AND status = %s" in sql


class TestFailRenderJob:
    def test_success(self):
        row = _make_row(status="failed", error_message="Something went wrong", songset_id="ss_001")
        conn, cursor = _make_mock_conn(fetchone_result=row)
        result = fail_render_job(conn, "job_abc123", 42, "Something went wrong")
        assert result is not None
        assert result.status == "failed"
        assert result.error_message == "Something went wrong"

    def test_not_found(self):
        conn, cursor = _make_mock_conn(fetchone_result=None)
        result = fail_render_job(conn, "nonexistent", 99, "error")
        assert result is None

    def test_parameterized_query(self):
        row = _make_row(status="failed", error_message="error msg", songset_id="ss_001")
        conn, cursor = _make_mock_conn(fetchone_result=row)
        fail_render_job(conn, "job_abc123", 42, "error msg")
        first_call_params = cursor.execute.call_args_list[0][0][1]
        assert first_call_params[0] == "failed"
        assert first_call_params[1] == "error msg"
        assert first_call_params[3] == "job_abc123"
        assert first_call_params[4] == 42

    def test_sets_updated_at(self):
        row = _make_row(status="failed", songset_id="ss_001")
        conn, cursor = _make_mock_conn(fetchone_result=row)
        fail_render_job(conn, "job_abc123", 42, "error")
        sql = cursor.execute.call_args_list[0][0][0]
        assert "updated_at = %s" in sql

    def test_status_guard_prevents_overwriting_completed(self):
        row = _make_row(status="failed", error_message="error", songset_id="ss_001")
        conn, cursor = _make_mock_conn(fetchone_result=row)
        fail_render_job(conn, "job_abc123", 42, "error")
        sql = cursor.execute.call_args_list[0][0][0]
        assert "AND status IN %s" in sql

    def test_uses_transaction(self):
        row = _make_row(status="failed", error_message="error", songset_id="ss_001")
        conn, cursor = _make_mock_conn(fetchone_result=row)
        fail_render_job(conn, "job_abc123", 42, "error")
        conn.commit.assert_called_once()


class TestRecoverOrphanedJobs:
    def test_no_orphans(self):
        conn, cursor = _make_mock_conn(fetchall_result=[])
        result = recover_orphaned_jobs(conn)
        assert result == 0

    def test_recovers_orphans(self):
        orphan1 = {"id": "job1", "songset_id": "ss1"}
        orphan2 = {"id": "job2", "songset_id": "ss2"}
        conn, cursor = _make_mock_conn(fetchall_result=[orphan1, orphan2])
        result = recover_orphaned_jobs(conn)
        assert result == 2

    def test_uses_threshold(self):
        conn, cursor = _make_mock_conn(fetchall_result=[])
        recover_orphaned_jobs(conn, threshold_minutes=15)
        params = cursor.execute.call_args_list[0][0][1]
        assert params[3] == "running"
        assert params[4] is not None

    def test_sets_failed_status(self):
        conn, cursor = _make_mock_conn(fetchall_result=[{"id": "j1", "songset_id": "ss1"}])
        recover_orphaned_jobs(conn)
        params = cursor.execute.call_args_list[0][0][1]
        assert params[0] == "failed"
        assert "timed out" in params[1]

    def test_default_threshold_15_minutes(self):
        conn, cursor = _make_mock_conn(fetchall_result=[])
        recover_orphaned_jobs(conn)
        params = cursor.execute.call_args_list[0][0][1]
        threshold = params[4]
        expected_threshold = datetime.now(timezone.utc) - timedelta(minutes=15)
        assert abs((threshold - expected_threshold).total_seconds()) < 5

    def test_custom_threshold(self):
        conn, cursor = _make_mock_conn(fetchall_result=[])
        recover_orphaned_jobs(conn, threshold_minutes=60)
        params = cursor.execute.call_args_list[0][0][1]
        threshold = params[4]
        expected_threshold = datetime.now(timezone.utc) - timedelta(minutes=60)
        assert abs((threshold - expected_threshold).total_seconds()) < 5

    def test_parameterized_query(self):
        conn, cursor = _make_mock_conn(rowcount=0)
        recover_orphaned_jobs(conn)
        sql = cursor.execute.call_args[0][0]
        assert "WHERE status = %s AND updated_at < %s" in sql

    def test_uses_transaction(self):
        conn, cursor = _make_mock_conn(fetchall_result=[{"id": "j1", "songset_id": "ss1"}])
        recover_orphaned_jobs(conn)
        conn.commit.assert_called_once()


class TestReclaimStaleJob:
    def test_reclaims_stale_job(self):
        stale_time = datetime.now(timezone.utc) - timedelta(minutes=10)
        row = _make_row(status="running", updated_at=stale_time)
        reclaimed_row = _make_row(status="queued", phase="preparing", phase_index=0, percent_complete=0.0, started_at=None)
        conn, cursor = _make_mock_conn(fetchone_result=row)
        cursor.fetchone.return_value = reclaimed_row
        result = reclaim_stale_job(conn, "job_abc123", 42)
        assert result is not None
        assert result.status == "queued"

    def test_resets_progress_fields(self):
        stale_time = datetime.now(timezone.utc) - timedelta(minutes=10)
        row = _make_row(status="running", updated_at=stale_time)
        reclaimed_row = _make_row(status="queued", phase="preparing", phase_index=0, percent_complete=0.0, started_at=None)
        conn, cursor = _make_mock_conn(fetchone_result=row)
        cursor.fetchone.return_value = reclaimed_row
        reclaim_stale_job(conn, "job_abc123", 42)
        update_call = cursor.execute.call_args_list[1]
        sql = update_call[0][0]
        params = update_call[0][1]
        assert "started_at = NULL" in sql
        assert "phase = %s" in sql
        assert params[3] == "preparing"

    def test_skips_recent_job(self):
        recent_time = datetime.now(timezone.utc) - timedelta(seconds=60)
        row = _make_row(status="running", updated_at=recent_time)
        conn, cursor = _make_mock_conn(fetchone_result=row)
        result = reclaim_stale_job(conn, "job_abc123", 42)
        assert result is None

    def test_skips_non_running_job(self):
        row = _make_row(status="completed")
        conn, cursor = _make_mock_conn(fetchone_result=None)
        result = reclaim_stale_job(conn, "job_abc123", 42)
        assert result is None

    def test_skips_job_without_updated_at(self):
        row = _make_row(status="running", updated_at=None)
        conn, cursor = _make_mock_conn(fetchone_result=row)
        result = reclaim_stale_job(conn, "job_abc123", 42)
        assert result is None

    def test_custom_threshold(self):
        stale_time = datetime.now(timezone.utc) - timedelta(seconds=400)
        row = _make_row(status="running", updated_at=stale_time)
        reclaimed_row = _make_row(status="queued")
        conn, cursor = _make_mock_conn(fetchone_result=row)
        cursor.fetchone.return_value = reclaimed_row
        result = reclaim_stale_job(conn, "job_abc123", 42, stale_threshold_seconds=300)
        assert result is not None

    def test_default_threshold_5_minutes(self):
        assert STALE_JOB_THRESHOLD_SECONDS == 300


class TestRenderJobDataclass:
    def test_defaults(self):
        job = RenderJob(id="j1", songset_id="ss1", user_id=1, status="queued")
        assert job.phase is None
        assert job.template == "dark"
        assert job.resolution == "720p"
        assert job.audio_enabled is True
        assert job.video_enabled is True
        assert job.font_size_preset == "M"
        assert job.include_title_card is False
        assert job.mp3_r2_key is None
        assert job.mp4_r2_key is None
        assert job.chapters_r2_key is None

    def test_all_fields(self):
        now = datetime.now(timezone.utc)
        job = RenderJob(
            id="j1",
            songset_id="ss1",
            user_id=1,
            status="completed",
            phase="completed",
            phase_index=5,
            total_phases=5,
            percent_complete=100.0,
            estimated_seconds_left=0.0,
            elapsed_seconds=300.0,
            error_message=None,
            estimated_total_seconds=300.0,
            total_duration_seconds=280.0,
            started_at=now,
            template="gradient_warm",
            resolution="1080p",
            audio_enabled=False,
            video_enabled=True,
            font_size_preset="L",
            include_title_card=True,
            title_card_duration_seconds=5.0,
            mp3_r2_key="renders/j1/output.mp3",
            mp4_r2_key="renders/j1/output.mp4",
            chapters_r2_key="renders/j1/chapters.json",
            created_at=now,
            updated_at=now,
            completed_at=now,
        )
        assert job.status == "completed"
        assert job.template == "gradient_warm"
        assert job.include_title_card is True
        assert job.title_card_duration_seconds == 5.0


class TestRenderProgressDataclass:
    def test_defaults(self):
        p = RenderProgress()
        assert p.phase is None
        assert p.phase_index is None
        assert p.estimated_total_seconds is None
        assert p.total_duration_seconds is None
        assert p.started_at is None
        assert p.elapsed_seconds is None
        assert p.percent_complete is None
        assert p.estimated_seconds_left is None

    def test_with_values(self):
        now = datetime.now(timezone.utc)
        p = RenderProgress(
            phase="mixing_audio",
            estimated_total_seconds=120.0,
            elapsed_seconds=30.0,
            percent_complete=20.0,
            estimated_seconds_left=90.0,
        )
        assert p.phase == "mixing_audio"
        assert p.estimated_total_seconds == 120.0
        assert p.elapsed_seconds == 30.0
        assert p.percent_complete == 20.0
        assert p.estimated_seconds_left == 90.0


class TestRowToRenderJob:
    def test_null_optional_fields(self):
        from sow_render_worker.db import _row_to_render_job

        row = _make_row(
            phase=None,
            phase_index=None,
            total_phases=None,
            percent_complete=None,
            estimated_seconds_left=None,
            elapsed_seconds=None,
            error_message=None,
            estimated_total_seconds=None,
            total_duration_seconds=None,
            started_at=None,
            mp3_r2_key=None,
            mp4_r2_key=None,
            chapters_r2_key=None,
            completed_at=None,
        )
        job = _row_to_render_job(row)
        assert job.phase is None
        assert job.phase_index is None
        assert job.percent_complete == 0.0
        assert job.elapsed_seconds is None
        assert job.mp3_r2_key is None

    def test_all_fields_populated(self):
        from sow_render_worker.db import _row_to_render_job

        now = datetime.now(timezone.utc)
        row = _make_row(
            status="running",
            phase="encoding_video",
            phase_index=3,
            total_phases=5,
            percent_complete=60.0,
            elapsed_seconds=180.0,
            estimated_total_seconds=300.0,
            total_duration_seconds=280.0,
            started_at=now,
            mp3_r2_key="renders/j1/output.mp3",
        )
        job = _row_to_render_job(row)
        assert job.status == "running"
        assert job.phase == "encoding_video"
        assert job.phase_index == 3
        assert job.percent_complete == 60.0
        assert job.elapsed_seconds == 180.0
        assert job.estimated_total_seconds == 300.0
        assert job.total_duration_seconds == 280.0
        assert job.started_at == now
        assert job.mp3_r2_key == "renders/j1/output.mp3"


class TestJobStatusTransitions:
    def test_queued_to_running(self):
        row = _make_row(status="running")
        conn, cursor = _make_mock_conn(fetchone_result=row)
        result = start_render_job(conn, "job_abc123", 42)
        assert result.status == "running"

    def test_running_to_completed(self):
        row = _make_row(status="completed", phase="completed", phase_index=5)
        conn, cursor = _make_mock_conn(fetchone_result=row)
        with patch("sow_render_worker.db.get_render_job", return_value=RenderJob(
            id="job_abc123", songset_id="ss_001", user_id=42, status="running",
            started_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        )):
            result = complete_render_job(conn, "job_abc123", 42)
        assert result.status == "completed"

    def test_running_to_failed(self):
        row = _make_row(status="failed", error_message="crash")
        conn, cursor = _make_mock_conn(fetchone_result=row)
        result = fail_render_job(conn, "job_abc123", 42, "crash")
        assert result.status == "failed"
        assert result.error_message == "crash"

    def test_progress_through_phases(self):
        for i, phase in enumerate(PHASE_ORDER):
            row = _make_row(phase=phase, phase_index=i)
            conn, cursor = _make_mock_conn(fetchone_result=row)
            progress = RenderProgress(phase=phase)
            result = update_render_progress(conn, "job_abc123", 42, progress)
            assert result is not None
            assert result.phase == phase


class TestSQLInjectionSafety:
    def test_get_render_job_parameterized(self):
        row = _make_row()
        conn, cursor = _make_mock_conn(fetchone_result=row)
        malicious_id = "'; DROP TABLE render_jobs; --"
        get_render_job(conn, malicious_id, 42)
        params = cursor.execute.call_args[0][1]
        assert params[0] == malicious_id
        assert isinstance(params[0], str)

    def test_fail_render_job_parameterized(self):
        row = _make_row(status="failed")
        conn, cursor = _make_mock_conn(fetchone_result=row)
        malicious_msg = "'); DROP TABLE render_jobs; --"
        fail_render_job(conn, "job_abc123", 42, malicious_msg)
        first_call_params = cursor.execute.call_args_list[0][0][1]
        assert first_call_params[1] == malicious_msg

    def test_update_progress_parameterized(self):
        row = _make_row()
        conn, cursor = _make_mock_conn(fetchone_result=row)
        progress = RenderProgress(phase="mixing_audio")
        update_render_progress(conn, "job_abc123", 42, progress)
        sql = cursor.execute.call_args[0][0]
        assert "%s" in sql
        assert f'"' not in sql.split("SET")[1].split("WHERE")[0] if "SET" in sql else True


class TestNormalizeFontFamily:
    def test_valid_font_families_return_themselves(self):
        assert _normalize_font_family("lxgw_wenkai_tc") == "lxgw_wenkai_tc"
        assert _normalize_font_family("chocolate_classical_sans") == "chocolate_classical_sans"
        assert _normalize_font_family("chiron_goround_tc") == "chiron_goround_tc"
        assert _normalize_font_family("noto_serif_tc") == "noto_serif_tc"

    def test_missing_font_family_returns_default(self):
        assert _normalize_font_family(None) == "noto_serif_tc"

    def test_unknown_font_family_returns_default_with_warning(self):
        with patch("sow_render_worker.db.logger") as mock_logger:
            assert _normalize_font_family("bad_value") == "noto_serif_tc"
            mock_logger.warning.assert_called_once()
            call_args = mock_logger.warning.call_args
            log_msg = call_args[0][0] if call_args[0] else ""
            log_args = call_args[0][1:] if len(call_args[0]) > 1 else ()
            assert "bad_value" in log_msg or "bad_value" in str(log_args)

    def test_non_string_font_family_returns_default(self):
        assert _normalize_font_family(123) == "noto_serif_tc"

    def test_empty_string_returns_default(self):
        assert _normalize_font_family("") == "noto_serif_tc"

    def test_none_does_not_log_warning(self):
        with patch("sow_render_worker.db.logger") as mock_logger:
            _normalize_font_family(None)
            mock_logger.warning.assert_not_called()


class TestRowToRenderJobFontNormalization:
    def test_missing_font_family_in_row(self):
        from sow_render_worker.db import _row_to_render_job

        row = _make_row()
        del row["font_family"]
        job = _row_to_render_job(row)
        assert job.font_family == "noto_serif_tc"

    def test_valid_font_family_in_row(self):
        from sow_render_worker.db import _row_to_render_job

        row = _make_row(font_family="lxgw_wenkai_tc")
        job = _row_to_render_job(row)
        assert job.font_family == "lxgw_wenkai_tc"

    def test_invalid_font_family_in_row(self):
        from sow_render_worker.db import _row_to_render_job

        row = _make_row(font_family="bad_value")
        job = _row_to_render_job(row)
        assert job.font_family == "noto_serif_tc"
