import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, call, patch

import pytest

from sow_render_worker.db import (
    ORPHANED_JOB_THRESHOLD_MINUTES,
    PHASE_ORDER,
    STALE_JOB_THRESHOLD_SECONDS,
    LIKELY_DEAD_THRESHOLD_SECONDS,
    TOTAL_PHASES,
    GLUE_ACTIVE_PHASES,
    GLUE_CLAIM_STALE_SECONDS,
    GLUE_TERMINAL_PHASES,
    GlueExcerpt,
    GlueJob,
    GlueTake,
    RenderJob,
    RenderProgress,
    _normalize_font_family,
    claim_glue_job,
    complete_render_job,
    fail_render_job,
    finish_glue_job,
    finish_glue_job_ready,
    get_connection,
    get_glue_excerpt,
    get_glue_job,
    get_glue_take_by_id,
    get_glue_take_for_descriptor,
    get_phase_index,
    get_render_glue_transitions,
    get_render_job,
    heartbeat_glue_job,
    insert_glue_take,
    insert_glue_take_if_absent,
    list_glue_takes_for_descriptor,
    read_songset_items_for_glue,
    reclaim_likely_dead_job,
    reclaim_stale_job,
    record_glue_raw_response,
    record_provider_contact,
    recover_orphaned_jobs,
    set_glue_job_dispatch_failed,
    select_glue_take_for_junction,
    set_glue_phase,
    start_render_job,
    update_render_progress,
    upsert_glue_excerpt,
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
        sql = cursor.execute.call_args_list[0][0][0]
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
        params = cursor.execute.call_args_list[0][0][1]
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
        params = cursor.execute.call_args_list[0][0][1]
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
        sql = cursor.execute.call_args_list[0][0][0]
        assert "%s" in sql
        assert "WHERE id = %s AND user_id = %s AND status = %s" in sql

    def test_updates_songsets_last_completed_render_job_id(self):
        row = _make_row(status="completed", songset_id="ss_001")
        conn, cursor = _make_mock_conn(fetchone_result=row)

        with patch("sow_render_worker.db.get_render_job", return_value=RenderJob(
            id="job_abc123", songset_id="ss_001", user_id=42, status="running",
            started_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        )):
            complete_render_job(conn, "job_abc123", 42)
        sql = cursor.execute.call_args_list[1][0][0]
        assert "UPDATE songsets SET last_completed_render_job_id" in sql
        params = cursor.execute.call_args_list[1][0][1]
        assert params[0] == "job_abc123"
        assert params[2] == "ss_001"

    def test_uses_transaction(self):
        row = _make_row(status="completed")
        conn, cursor = _make_mock_conn(fetchone_result=row)

        with patch("sow_render_worker.db.get_render_job", return_value=RenderJob(
            id="job_abc123", songset_id="ss_001", user_id=42, status="running",
            started_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        )):
            complete_render_job(conn, "job_abc123", 42)
        conn.commit.assert_called_once()


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

    def test_default_threshold_2_minutes(self):
        assert STALE_JOB_THRESHOLD_SECONDS == 120


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


class TestLikelyDeadThreshold:
    def test_likely_dead_threshold_60_seconds(self):
        assert LIKELY_DEAD_THRESHOLD_SECONDS == 60

    def test_stale_threshold_120_seconds(self):
        assert STALE_JOB_THRESHOLD_SECONDS == 120


class TestReclaimLikelyDeadJob:
    def test_reclaims_likely_dead_job(self):
        dead_time = datetime.now(timezone.utc) - timedelta(seconds=90)
        row = _make_row(status="running", updated_at=dead_time)
        reclaimed_row = _make_row(status="queued", phase="preparing", phase_index=0, percent_complete=0.0, started_at=None)
        conn, cursor = _make_mock_conn(fetchone_result=row)
        cursor.fetchone.return_value = reclaimed_row
        result = reclaim_likely_dead_job(conn, "job_abc123", 42)
        assert result is not None
        assert result.status == "queued"

    def test_skips_recent_job(self):
        recent_time = datetime.now(timezone.utc) - timedelta(seconds=30)
        row = _make_row(status="running", updated_at=recent_time)
        conn, cursor = _make_mock_conn(fetchone_result=row)
        result = reclaim_likely_dead_job(conn, "job_abc123", 42)
        assert result is None

    def test_uses_60_second_threshold(self):
        just_over_60 = datetime.now(timezone.utc) - timedelta(seconds=61)
        row = _make_row(status="running", updated_at=just_over_60)
        reclaimed_row = _make_row(status="queued")
        conn, cursor = _make_mock_conn(fetchone_result=row)
        cursor.fetchone.return_value = reclaimed_row
        result = reclaim_likely_dead_job(conn, "job_abc123", 42)
        assert result is not None


# ---------------------------------------------------------------------------
# Glue (AI transition) helpers
# ---------------------------------------------------------------------------


def _make_glue_job_row(**overrides) -> dict:
    defaults = {
        "id": "11111111-1111-4111-8111-111111111111",
        "user_id": 42,
        "songset_id": "ss_001",
        "from_item_id": "item-a",
        "to_item_id": "item-b",
        "client_request_id": "22222222-2222-4222-8222-222222222222",
        "take_mode": "initial",
        "descriptor_key": "a" * 64,
        "descriptor_json": '{"v":2}',
        "expected_from_updated_at": datetime(2025, 1, 1, tzinfo=timezone.utc),
        "expected_to_updated_at": datetime(2025, 1, 1, tzinfo=timezone.utc),
        "phase": "queued",
        "active": True,
        "dispatch_state": "pending",
        "dispatch_attempt": 0,
        "attempt_id": "33333333-3333-4333-8333-333333333333",
        "pending_take_id": "44444444-4444-4444-8444-444444444444",
        "take_ordinal": 1,
        "seed": 123456789,
        "paid_reservation_id": None,
        "paid_reservation_state": None,
        "provider_contacted": False,
        "provider_phase": None,
        "provider_status": None,
        "retry_kind": None,
        "resulting_take_id": None,
        "active_take_id": None,
        "raw_response_r2_key": None,
        "raw_response_sha256": None,
        "raw_response_bytes": None,
        "error_code": None,
        "error_detail": None,
        "heartbeat_at": None,
        "created_at": datetime(2025, 1, 1, tzinfo=timezone.utc),
        "started_at": None,
        "updated_at": datetime(2025, 1, 1, tzinfo=timezone.utc),
        "finished_at": None,
    }
    defaults.update(overrides)
    return defaults


def _make_glue_take_row(**overrides) -> dict:
    defaults = {
        "id": "55555555-5555-4555-8555-555555555555",
        "user_id": 42,
        "descriptor_key": "a" * 64,
        "descriptor_json": '{"v":2}',
        "ordinal": 1,
        "seed": 123456789,
        "status": "ready",
        "r2_key": "glues/42/take.wav",
        "seconds": 2.786,
        "raw_response_r2_key": None,
        "raw_response_sha256": None,
        "raw_response_bytes": None,
        "metrics_json": "{}",
        "reasons_json": "[]",
        "error_code": None,
        "created_at": datetime(2025, 1, 1, tzinfo=timezone.utc),
        "updated_at": datetime(2025, 1, 1, tzinfo=timezone.utc),
    }
    defaults.update(overrides)
    return defaults


def _make_glue_excerpt_row(**overrides) -> dict:
    defaults = {
        "user_id": 42,
        "logical_key": "b" * 64,
        "version": 2,
        "side": "A",
        "seconds": 12.0,
        "content_hash": "c" * 64,
        "canonical_steps_json": None,
        "audio_sha256": None,
        "elevenlabs_song_id": None,
        "phase": "constructed",
        "rejection_code": None,
        "safe_failure_detail": None,
        "created_at": datetime(2025, 1, 1, tzinfo=timezone.utc),
        "updated_at": datetime(2025, 1, 1, tzinfo=timezone.utc),
    }
    defaults.update(overrides)
    return defaults


class TestGlueConstants:
    def test_active_phases(self):
        assert GLUE_ACTIVE_PHASES == (
            "queued",
            "preparing",
            "uploading",
            "composing",
            "storing-response",
            "staging",
        )

    def test_terminal_phases(self):
        assert GLUE_TERMINAL_PHASES == ("ready", "failed", "cancelled", "dispatch-failed")

    def test_claim_stale_threshold(self):
        assert GLUE_CLAIM_STALE_SECONDS == 210


class TestGlueDataclasses:
    def test_glue_job_defaults(self):
        job = GlueJob(
            id="j1",
            user_id=42,
            songset_id="ss1",
            from_item_id="a",
            to_item_id="b",
            client_request_id="req",
            take_mode="initial",
            descriptor_key="d" * 64,
            descriptor_json="{}",
            phase="queued",
            active=True,
            dispatch_state="pending",
            attempt_id="att",
            pending_take_id="take",
            take_ordinal=1,
            seed=7,
        )
        assert job.dispatch_attempt == 0
        assert job.provider_contacted is False
        assert job.resulting_take_id is None

    def test_glue_take_defaults(self):
        take = GlueTake(
            id="t1",
            user_id=42,
            descriptor_key="d" * 64,
            descriptor_json="{}",
            ordinal=1,
            seed=7,
            status="ready",
        )
        assert take.metrics_json == "{}"
        assert take.reasons_json == "[]"
        assert take.r2_key is None

    def test_glue_excerpt_defaults(self):
        excerpt = GlueExcerpt(
            user_id=42,
            logical_key="k" * 64,
            side="A",
            seconds=12.0,
            content_hash="c" * 64,
            phase="constructed",
        )
        assert excerpt.version == 2
        assert excerpt.audio_sha256 is None

    def test_row_converter_normalizes_uuid_ids(self):
        from uuid import UUID

        row = _make_glue_job_row(
            id=UUID("11111111-1111-4111-8111-111111111111"),
            pending_take_id=UUID("44444444-4444-4444-8444-444444444444"),
        )
        from sow_render_worker.db import _row_to_glue_job

        job = _row_to_glue_job(row)
        assert job.id == "11111111-1111-4111-8111-111111111111"
        assert job.pending_take_id == "44444444-4444-4444-8444-444444444444"


class TestGetGlueJob:
    def test_found(self):
        row = _make_glue_job_row()
        conn, cursor = _make_mock_conn(fetchone_result=row)
        job = get_glue_job(conn, row["id"], 42)
        assert job is not None
        assert job.id == row["id"]
        assert job.phase == "queued"
        sql = cursor.execute.call_args[0][0]
        assert "SELECT * FROM glue_jobs" in sql
        assert "WHERE id = %s AND user_id = %s" in sql

    def test_parameterized(self):
        row = _make_glue_job_row()
        conn, cursor = _make_mock_conn(fetchone_result=row)
        get_glue_job(conn, row["id"], 42)
        assert cursor.execute.call_args[0][1] == (row["id"], 42)

    def test_not_found(self):
        conn, cursor = _make_mock_conn(fetchone_result=None)
        assert get_glue_job(conn, "missing", 42) is None


class TestClaimGlueJob:
    def test_claims_queued_job(self):
        row = _make_glue_job_row(phase="preparing")
        conn, cursor = _make_mock_conn(fetchone_result=row)
        job, claimed_now = claim_glue_job(conn, row["id"], 42)
        assert claimed_now is True
        assert job is not None
        assert job.phase == "preparing"
        sql = cursor.execute.call_args[0][0]
        assert "UPDATE glue_jobs" in sql
        assert "phase = ANY(%s)" in sql
        assert "heartbeat_at IS NULL OR heartbeat_at < %s" in sql
        assert "RETURNING *" in sql

    def test_claim_parameters(self):
        row = _make_glue_job_row(phase="preparing")
        conn, cursor = _make_mock_conn(fetchone_result=row)
        claim_glue_job(conn, row["id"], 42)
        params = cursor.execute.call_args[0][1]
        assert params[0] == "queued"
        assert params[1] == "preparing"
        assert row["id"] in params
        assert 42 in params
        assert list(GLUE_ACTIVE_PHASES) in params
        assert params[-1] is not None  # stale-before timestamp

    def test_duplicate_delivery_does_not_reclaim(self):
        row = _make_glue_job_row(phase="composing")
        conn, cursor = _make_mock_conn()
        cursor.fetchone.side_effect = [None, row]
        job, claimed_now = claim_glue_job(conn, row["id"], 42)
        assert claimed_now is False
        assert job is not None
        assert job.phase == "composing"
        assert cursor.execute.call_count == 2
        assert "SELECT * FROM glue_jobs" in cursor.execute.call_args_list[1][0][0]

    def test_missing_job(self):
        conn, cursor = _make_mock_conn()
        cursor.fetchone.side_effect = [None, None]
        job, claimed_now = claim_glue_job(conn, "missing", 42)
        assert job is None
        assert claimed_now is False


class TestSetGluePhase:
    def test_updates_phase_and_fields(self):
        row = _make_glue_job_row(phase="uploading")
        conn, cursor = _make_mock_conn(fetchone_result=row)
        job = set_glue_phase(
            conn,
            row["id"],
            42,
            "uploading",
            provider_phase="upload-a",
            dispatch_state="dispatched",
        )
        assert job is not None
        sql = cursor.execute.call_args[0][0]
        assert "provider_phase = %s" in sql
        assert "dispatch_state = %s" in sql
        assert "updated_at = %s" in sql
        assert "AND active" in sql
        assert "RETURNING *" in sql
        params = cursor.execute.call_args[0][1]
        assert "uploading" in params
        assert "upload-a" in params
        assert row["id"] in params

    def test_sets_active_explicitly(self):
        row = _make_glue_job_row(phase="staging")
        conn, cursor = _make_mock_conn(fetchone_result=row)
        set_glue_phase(conn, row["id"], 42, "staging", active=True)
        sql = cursor.execute.call_args[0][0]
        assert "active = %s" in sql

    def test_unknown_field_raises(self):
        conn, _ = _make_mock_conn()
        with pytest.raises(ValueError, match="Unknown glue_jobs fields"):
            set_glue_phase(conn, "j1", 42, "preparing", status="ready")
        conn.cursor.assert_not_called()

    def test_not_found(self):
        conn, _ = _make_mock_conn(fetchone_result=None)
        assert set_glue_phase(conn, "j1", 42, "preparing") is None


class TestHeartbeatGlueJob:
    def test_returns_true_when_updated(self):
        conn, cursor = _make_mock_conn(fetchone_result={"id": "j1"})
        assert heartbeat_glue_job(conn, "j1", 42) is True
        sql = cursor.execute.call_args[0][0]
        assert "heartbeat_at = %s" in sql
        assert "AND active" in sql

    def test_returns_false_when_no_active_job(self):
        conn, _ = _make_mock_conn(fetchone_result=None)
        assert heartbeat_glue_job(conn, "j1", 42) is False


class TestRecordProviderContact:
    def test_success_consumes_reservation(self):
        conn, cursor = _make_mock_conn(fetchone_result={"id": "j1"})
        assert record_provider_contact(conn, "j1", 42, "compose") is True

        job_sql, job_params = cursor.execute.call_args_list[0][0]
        assert "provider_contacted = true" in job_sql
        assert "paid_reservation_state = %s" in job_sql
        assert "phase = %s" in job_sql
        assert "provider_phase = %s" in job_sql
        assert "provider_contacted = false" in job_sql
        assert "phase <> %s" in job_sql
        assert "AND active" in job_sql
        assert job_params[0] == "consumed"
        assert job_params[1] == "composing"
        assert job_params[2] == "compose"
        assert job_params[-1] == "cancelled"

        attempt_sql, attempt_params = cursor.execute.call_args_list[1][0]
        assert "UPDATE glue_paid_attempts" in attempt_sql
        assert "state = %s" in attempt_sql
        assert "consumed_at = %s" in attempt_sql
        assert attempt_params[0] == "consumed"
        conn.commit.assert_called_once()
        conn.rollback.assert_not_called()

    def test_upload_phases_map_to_uploading(self):
        for provider_phase in ("upload-a", "upload-b"):
            conn, cursor = _make_mock_conn(fetchone_result={"id": "j1"})
            assert record_provider_contact(conn, "j1", 42, provider_phase) is True
            assert cursor.execute.call_args_list[0][0][1][1] == "uploading"

    def test_cas_loser_consumes_nothing(self):
        conn, cursor = _make_mock_conn(fetchone_result=None)
        assert record_provider_contact(conn, "j1", 42, "compose") is False
        assert cursor.execute.call_count == 1
        conn.rollback.assert_called_once()
        conn.commit.assert_not_called()

    def test_rejects_unknown_provider_phase(self):
        conn, cursor = _make_mock_conn()
        with pytest.raises(ValueError, match="Unknown provider phase"):
            record_provider_contact(conn, "j1", 42, "transcode")
        cursor.execute.assert_not_called()

    def test_restores_autocommit(self):
        conn, _ = _make_mock_conn(fetchone_result={"id": "j1"})
        conn.autocommit = True
        record_provider_contact(conn, "j1", 42, "compose")
        assert conn.autocommit is True


class TestSetGlueJobDispatchFailed:
    def test_sets_terminal_dispatch_failed(self):
        row = _make_glue_job_row(phase="dispatch-failed", active=False)
        conn, cursor = _make_mock_conn(fetchone_result=row)
        job = set_glue_job_dispatch_failed(conn, row["id"], 42)
        assert job is not None
        sql, params = cursor.execute.call_args[0]
        assert "phase = %s" in sql
        assert "active = %s" in sql
        assert "dispatch_state = %s" in sql
        assert "error_code = %s" in sql
        assert "phase = ANY(%s)" in sql
        assert "RETURNING *" in sql
        assert params[0] == "dispatch-failed"
        assert params[1] is False
        assert params[2] == "failed"
        assert params[3] == "GLUE_DISPATCH_FAILED"
        assert params[4] == "free"
        assert params[-1] == ["queued", "dispatch-failed"]

    def test_attempt_guard(self):
        row = _make_glue_job_row(phase="dispatch-failed", active=False)
        conn, cursor = _make_mock_conn(fetchone_result=row)
        set_glue_job_dispatch_failed(conn, row["id"], 42, attempt_id="att-1")
        sql, params = cursor.execute.call_args[0]
        assert "AND attempt_id = %s" in sql
        assert params[-1] == "att-1"
        assert params[-2] == ["queued", "dispatch-failed"]
        assert params[-3] == 42
        assert params[-4] == row["id"]

    def test_not_found(self):
        conn, _ = _make_mock_conn(fetchone_result=None)
        assert set_glue_job_dispatch_failed(conn, "j1", 42) is None


class TestRecordGlueRawResponse:
    def test_records_key_sha_and_bytes_once(self):
        row = _make_glue_job_row(
            phase="storing-response",
            raw_response_r2_key="glues/42/jobs/j1/response.pcm",
            raw_response_sha256="f" * 64,
            raw_response_bytes=1234,
        )
        conn, cursor = _make_mock_conn(fetchone_result=row)
        job = record_glue_raw_response(
            conn,
            row["id"],
            42,
            r2_key=row["raw_response_r2_key"],
            sha256=row["raw_response_sha256"],
            byte_count=1234,
        )
        assert job is not None
        assert job.raw_response_bytes == 1234
        sql, params = cursor.execute.call_args[0]
        assert "raw_response_r2_key = %s" in sql
        assert "raw_response_sha256 = %s" in sql
        assert "raw_response_bytes = %s" in sql
        assert "updated_at = %s" in sql
        assert "raw_response_r2_key IS NULL" in sql
        assert "RETURNING *" in sql
        assert params[0] == row["raw_response_r2_key"]
        assert params[1] == row["raw_response_sha256"]
        assert params[2] == 1234
        assert params[-2:] == (row["id"], 42)

    def test_existing_response_is_never_overwritten(self):
        conn, cursor = _make_mock_conn(fetchone_result=None)
        assert (
            record_glue_raw_response(
                conn,
                "j1",
                42,
                r2_key="glues/42/jobs/j1/response.pcm",
                sha256="a" * 64,
                byte_count=10,
            )
            is None
        )
        sql = cursor.execute.call_args[0][0]
        assert "raw_response_r2_key IS NULL" in sql
        assert "UPDATE glue_jobs" in sql
        # A guarded miss must not issue a second, unguarded write.
        assert cursor.execute.call_count == 1

    @pytest.mark.parametrize("field", ["r2_key", "sha256"])
    def test_rejects_empty_identity(self, field):
        values = {
            "r2_key": "glues/42/jobs/j1/response.pcm",
            "sha256": "a" * 64,
            "byte_count": 10,
        }
        values[field] = ""
        conn, cursor = _make_mock_conn(fetchone_result=None)
        with pytest.raises(ValueError, match=field):
            record_glue_raw_response(conn, "j1", 42, **values)
        cursor.execute.assert_not_called()

    def test_rejects_non_positive_byte_count(self):
        conn, cursor = _make_mock_conn(fetchone_result=None)
        with pytest.raises(ValueError, match="byte_count"):
            record_glue_raw_response(
                conn, "j1", 42, r2_key="glues/42/jobs/j1/response.pcm", sha256="a" * 64, byte_count=0
            )
        cursor.execute.assert_not_called()


class TestFinishGlueJob:
    def test_terminal_clears_active(self):
        row = _make_glue_job_row(phase="failed", active=False)
        conn, cursor = _make_mock_conn(fetchone_result=row)
        job = finish_glue_job(
            conn,
            row["id"],
            42,
            phase="failed",
            error_code="GLUE_PROVIDER_UNKNOWN",
            retry_kind="paid",
        )
        assert job is not None
        sql, params = cursor.execute.call_args[0]
        assert "phase = %s, active = %s" in sql
        assert "finished_at = %s" in sql
        assert "RETURNING *" in sql
        assert params[0] == "failed"
        assert params[1] is False
        assert params[2] == "GLUE_PROVIDER_UNKNOWN"
        assert params[4] == "paid"

    def test_not_found(self):
        conn, _ = _make_mock_conn(fetchone_result=None)
        assert finish_glue_job(conn, "j1", 42, phase="ready") is None


class TestInsertGlueTake:
    def test_inserts_all_columns(self):
        row = _make_glue_take_row()
        conn, cursor = _make_mock_conn(fetchone_result=row)
        take = insert_glue_take(
            conn,
            take_id=row["id"],
            user_id=42,
            descriptor_key=row["descriptor_key"],
            descriptor_json=row["descriptor_json"],
            ordinal=1,
            seed=123456789,
            status="ready",
            r2_key="glues/42/take.wav",
            seconds=2.786,
        )
        assert take is not None
        assert take.ordinal == 1
        sql, params = cursor.execute.call_args[0]
        assert "INSERT INTO glue_takes" in sql
        assert "RETURNING *" in sql
        assert row["id"] in params
        assert 2.786 in params
        assert params[-3] == "{}"  # metrics default
        assert params[-2] == "[]"  # reasons default

    def test_parameterized_descriptor_text(self):
        row = _make_glue_take_row()
        conn, cursor = _make_mock_conn(fetchone_result=row)
        malicious = '{"v":2,"x":"\'; DROP TABLE glue_takes; --"}'
        insert_glue_take(
            conn,
            take_id=row["id"],
            user_id=42,
            descriptor_key=row["descriptor_key"],
            descriptor_json=malicious,
            ordinal=1,
            seed=1,
            status="failed",
        )
        params = cursor.execute.call_args[0][1]
        assert malicious in params


class TestGetGlueTakeForDescriptor:
    def test_defaults_to_earliest_ready_take(self):
        row = _make_glue_take_row()
        conn, cursor = _make_mock_conn(fetchone_result=row)
        take = get_glue_take_for_descriptor(conn, 42, row["descriptor_key"])
        assert take is not None
        sql, params = cursor.execute.call_args[0]
        assert "status = %s" in sql
        assert params[-1] == "ready"
        assert "ORDER BY ordinal ASC" in sql
        assert "LIMIT 1" in sql

    def test_newest_orders_by_created(self):
        row = _make_glue_take_row()
        conn, cursor = _make_mock_conn(fetchone_result=row)
        get_glue_take_for_descriptor(conn, 42, row["descriptor_key"], newest=True)
        sql = cursor.execute.call_args[0][0]
        assert "ORDER BY created_at DESC" in sql

    def test_exact_take_id_guard(self):
        row = _make_glue_take_row()
        conn, cursor = _make_mock_conn(fetchone_result=row)
        get_glue_take_for_descriptor(
            conn, 42, row["descriptor_key"], take_id=row["id"]
        )
        sql, params = cursor.execute.call_args[0]
        assert "AND id = %s" in sql
        assert row["id"] in params

    def test_not_found(self):
        conn, _ = _make_mock_conn(fetchone_result=None)
        assert get_glue_take_for_descriptor(conn, 42, "d" * 64) is None


class TestGlueExcerpts:
    def test_get_found(self):
        row = _make_glue_excerpt_row()
        conn, cursor = _make_mock_conn(fetchone_result=row)
        excerpt = get_glue_excerpt(conn, 42, row["logical_key"])
        assert excerpt is not None
        assert excerpt.side == "A"
        sql, params = cursor.execute.call_args[0]
        assert "SELECT * FROM glue_excerpts" in sql
        assert params == (42, row["logical_key"])

    def test_get_not_found(self):
        conn, _ = _make_mock_conn(fetchone_result=None)
        assert get_glue_excerpt(conn, 42, "missing") is None

    def test_upsert_uses_conflict_target(self):
        row = _make_glue_excerpt_row()
        conn, cursor = _make_mock_conn(fetchone_result=row)
        excerpt = upsert_glue_excerpt(
            conn,
            42,
            row["logical_key"],
            side="A",
            seconds=12.0,
            content_hash="c" * 64,
            phase="uploaded",
            audio_sha256="e" * 64,
            elevenlabs_song_id="song-1",
        )
        assert excerpt is not None
        sql, params = cursor.execute.call_args[0]
        assert "INSERT INTO glue_excerpts" in sql
        assert "ON CONFLICT (user_id, logical_key) DO UPDATE" in sql
        assert "COALESCE(EXCLUDED.audio_sha256, glue_excerpts.audio_sha256)" in sql
        assert "COALESCE(EXCLUDED.elevenlabs_song_id, glue_excerpts.elevenlabs_song_id)" in sql
        assert "RETURNING *" in sql
        assert "uploaded" in params
        assert "song-1" in params

    def test_upsert_rejection_is_a_negative_entry(self):
        row = _make_glue_excerpt_row(phase="rejected", rejection_code="copyright")
        conn, cursor = _make_mock_conn(fetchone_result=row)
        upsert_glue_excerpt(
            conn,
            42,
            row["logical_key"],
            side="B",
            seconds=3.0,
            content_hash="c" * 64,
            phase="rejected",
            rejection_code="copyright",
            safe_failure_detail="provider rejected the excerpt",
        )
        params = cursor.execute.call_args[0][1]
        assert "rejected" in params
        assert "copyright" in params


class TestReadSongsetItemsForGlue:
    def test_selects_render_facts_plus_glue_columns(self):
        row = {
            "id": "item-a",
            "position": 0,
            "glue_enabled": False,
            "glue_beats": 4,
            "glue_active_take_id": None,
            "updated_at": datetime(2025, 1, 1, tzinfo=timezone.utc),
        }
        conn, cursor = _make_mock_conn(fetchall_result=[row])
        items = read_songset_items_for_glue(conn, "ss_001")
        assert items == [row]
        sql, params = cursor.execute.call_args[0]
        for fragment in (
            "si.glue_enabled",
            "si.glue_beats",
            "si.glue_active_take_id",
            "si.updated_at",
            "si.arrangement_steps",
            "r.tempo_bpm",
            "r.duration_seconds",
            "r.sections",
            "r.beats",
            "r.content_hash AS recording_content_hash",
            "s.title AS song_title",
            "LEFT JOIN recordings r ON si.recording_hash_prefix = r.hash_prefix",
            "LEFT JOIN songs s ON si.song_id = s.id",
            "WHERE si.songset_id = %s",
            "ORDER BY si.position",
        ):
            assert fragment in sql
        assert params == ("ss_001",)

    def test_returns_plain_dicts(self):
        conn, cursor = _make_mock_conn(fetchall_result=[])
        assert read_songset_items_for_glue(conn, "ss_001") == []


class TestGetGlueTakeById:
    def test_owner_guarded_select(self):
        row = _make_glue_take_row()
        conn, cursor = _make_mock_conn(fetchone_result=row)
        take = get_glue_take_by_id(conn, row["id"], 42)
        assert take is not None
        assert take.id == row["id"]
        sql, params = cursor.execute.call_args[0]
        assert "FROM glue_takes" in sql
        assert "WHERE id = %s AND user_id = %s" in sql
        assert params == (row["id"], 42)

    def test_missing_returns_none(self):
        conn, _ = _make_mock_conn(fetchone_result=None)
        assert get_glue_take_by_id(conn, "missing", 42) is None


class TestGetRenderGlueTransitions:
    def test_reads_and_parses_snapshot_in_order(self):
        entries = [
            {"from_item_id": "a", "to_item_id": "b", "take_id": "t1", "seconds": 2.5},
            {"from_item_id": "b", "to_item_id": "c", "take_id": "t2", "seconds": 3.0},
        ]
        conn, cursor = _make_mock_conn(
            fetchone_result={"glue_transitions": json.dumps(entries)}
        )
        transitions = get_render_glue_transitions(conn, "job-1")
        assert transitions == entries
        sql, params = cursor.execute.call_args[0]
        assert 'SELECT glue_transitions FROM "render_jobs"' in sql or (
            "SELECT glue_transitions FROM render_jobs" in sql
        )
        assert "WHERE id = %s" in sql
        assert params == ("job-1",)

    def test_null_snapshot_means_no_glue(self):
        conn, _ = _make_mock_conn(fetchone_result={"glue_transitions": None})
        assert get_render_glue_transitions(conn, "job-1") is None

    def test_empty_array_means_no_glue(self):
        conn, _ = _make_mock_conn(fetchone_result={"glue_transitions": "[]"})
        assert get_render_glue_transitions(conn, "job-1") is None

    def test_blank_text_means_no_glue(self):
        conn, _ = _make_mock_conn(fetchone_result={"glue_transitions": "   "})
        assert get_render_glue_transitions(conn, "job-1") is None

    def test_missing_job_returns_none(self):
        conn, _ = _make_mock_conn(fetchone_result=None)
        assert get_render_glue_transitions(conn, "missing") is None

    def test_malformed_json_raises(self):
        conn, _ = _make_mock_conn(
            fetchone_result={"glue_transitions": "{not-json"}
        )
        with pytest.raises(ValueError, match="not valid JSON"):
            get_render_glue_transitions(conn, "job-1")

    def test_non_array_raises(self):
        conn, _ = _make_mock_conn(
            fetchone_result={"glue_transitions": json.dumps({"a": 1})}
        )
        with pytest.raises(ValueError, match="not a JSON array"):
            get_render_glue_transitions(conn, "job-1")

    def test_non_object_entry_raises(self):
        conn, _ = _make_mock_conn(
            fetchone_result={"glue_transitions": json.dumps(["entry"])}
        )
        with pytest.raises(ValueError, match="not a JSON array"):
            get_render_glue_transitions(conn, "job-1")


class TestListGlueTakesForDescriptor:
    def test_lists_ready_auditionable_takes_oldest_first(self):
        rows = [
            _make_glue_take_row(ordinal=1),
            _make_glue_take_row(id="66666666-6666-4666-8666-666666666666", ordinal=2),
        ]
        conn, cursor = _make_mock_conn(fetchall_result=rows)
        takes = list_glue_takes_for_descriptor(conn, 42, "a" * 64)
        assert [take.ordinal for take in takes] == [1, 2]
        sql, params = cursor.execute.call_args[0]
        assert "status = %s" in sql
        assert "r2_key IS NOT NULL" in sql
        assert "seconds > 0" in sql
        assert "ORDER BY ordinal ASC" in sql
        assert params == (42, "a" * 64, "ready")


class TestInsertGlueTakeIfAbsent:
    def _insert(self, conn):
        return insert_glue_take_if_absent(
            conn,
            take_id="55555555-5555-4555-8555-555555555555",
            user_id=42,
            descriptor_key="a" * 64,
            descriptor_json='{"v":2}',
            ordinal=1,
            seed=7,
            status="ready",
            r2_key="glues/42/take.wav",
            seconds=2.786,
            raw_response_r2_key="glues/42/jobs/j/response.pcm",
            raw_response_sha256="f" * 64,
            raw_response_bytes=1234,
            metrics_json='{"audioSha256":"e"}',
            reasons_json="[]",
        )

    def test_conflicts_on_the_preallocated_id(self):
        row = _make_glue_take_row()
        conn, cursor = _make_mock_conn(fetchone_result=row)
        take, created = self._insert(conn)
        assert created is True
        assert take.id == row["id"]
        sql, params = cursor.execute.call_args[0]
        assert "INSERT INTO glue_takes" in sql
        assert "ON CONFLICT (id) DO NOTHING" in sql
        assert "RETURNING *" in sql
        assert params[0] == "55555555-5555-4555-8555-555555555555"

    def test_replay_returns_the_stored_take_untouched(self):
        row = _make_glue_take_row()
        conn, cursor = _make_mock_conn(fetchone_result=None)
        cursor.fetchone.side_effect = [None, row]
        take, created = self._insert(conn)
        assert created is False
        assert take.id == row["id"]
        second_sql, second_params = cursor.execute.call_args_list[1][0]
        assert "SELECT * FROM glue_takes WHERE id = %s AND user_id = %s" in second_sql
        assert second_params == ("55555555-5555-4555-8555-555555555555", 42)


class TestFinishGlueJobReady:
    def test_ready_only_from_staging_phases(self):
        row = _make_glue_job_row(phase="ready", active=False, resulting_take_id="t1")
        conn, cursor = _make_mock_conn(fetchone_result=row)
        job = finish_glue_job_ready(
            conn,
            row["id"],
            42,
            resulting_take_id="t1",
            active_take_id="t1",
        )
        assert job is not None
        sql, params = cursor.execute.call_args[0]
        assert "phase = %s" in sql
        assert "active = %s" in sql
        assert "resulting_take_id = %s" in sql
        assert "active_take_id = %s" in sql
        assert "phase = ANY(%s)" in sql
        assert params[0] == "ready"
        assert params[1] is False
        assert params[2] == "t1"
        assert params[3] == "t1"
        assert params[-1] == ["storing-response", "staging"]

    def test_keeps_a_concurrent_terminal_phase(self):
        conn, _ = _make_mock_conn(fetchone_result=None)
        assert (
            finish_glue_job_ready(
                conn, "j1", 42, resulting_take_id="t1", active_take_id=None
            )
            is None
        )


class TestSelectGlueTakeForJunction:
    def _select(self, conn, **overrides):
        args = dict(
            item_id="item-a",
            songset_id="ss-1",
            user_id=42,
            take_id="55555555-5555-4555-8555-555555555555",
            descriptor_key="a" * 64,
        )
        args.update(overrides)
        return select_glue_take_for_junction(conn, **args)

    def test_guards_owner_descriptor_readiness_and_audio(self):
        conn, cursor = _make_mock_conn(fetchone_result=("item-a",))
        assert self._select(conn) is True
        sql, params = cursor.execute.call_args_list[0][0]
        assert "UPDATE songset_items SET glue_active_take_id = %s" in sql
        assert "SELECT 1 FROM songsets WHERE songsets.id = %s AND songsets.user_id = %s" in sql
        assert "glue_takes.descriptor_key = %s" in sql
        assert "glue_takes.status = %s" in sql
        assert "glue_takes.r2_key IS NOT NULL" in sql
        assert "glue_takes.seconds > 0" in sql
        assert params[0] == "55555555-5555-4555-8555-555555555555"
        assert params[-1] == "ready"
        conn.commit.assert_called_once()

    def test_cross_user_or_wrong_descriptor_select_is_refused(self):
        conn, _ = _make_mock_conn(fetchone_result=None)
        assert self._select(conn, user_id=99) is False
        conn.commit.assert_called_once()

    def test_caller_managed_transaction_does_not_commit_or_toggle(self):
        conn, _ = _make_mock_conn(fetchone_result=("item-a",))
        conn.autocommit = False
        assert self._select(conn, manage_transaction=False) is True
        conn.commit.assert_not_called()
        conn.rollback.assert_not_called()
        assert conn.autocommit is False
