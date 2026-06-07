"""Tests for LRC completion visibility behavior in admin audio helpers."""

import io
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from rich.console import Console

from stream_of_worship.admin.commands import audio
from stream_of_worship.admin.db.models import Recording, Song
from stream_of_worship.admin.services.analysis import AnalysisResult, JobInfo


def _console() -> Console:
    return Console(file=io.StringIO(), force_terminal=False)


def _recording(**overrides) -> Recording:
    values = {
        "content_hash": "a" * 64,
        "hash_prefix": "abc123def456",
        "song_id": "song_1",
        "original_filename": "test.mp3",
        "file_size_bytes": 1000,
        "imported_at": "2024-01-01T00:00:00",
        "r2_audio_url": "s3://bucket/abc123def456/audio.mp3",
        "lrc_status": "processing",
        "lrc_job_id": "lrc-job-1",
    }
    values.update(overrides)
    return Recording(**values)


def _song() -> Song:
    return Song(
        id="song_1",
        title="Test Song",
        lyrics_raw="Line one\nLine two",
        source_url="https://example.com/song",
        scraped_at="2024-01-01T00:00:00",
    )


def _completed_lrc_job(lrc_url: str = "s3://bucket/abc123def456/lyrics.lrc") -> JobInfo:
    return JobInfo(
        job_id="lrc-job-1",
        status="completed",
        job_type="lrc",
        progress=1.0,
        result=AnalysisResult(lrc_url=lrc_url, lrc_source="whisper_asr"),
    )


def _empty_pending_cursor() -> MagicMock:
    cursor = MagicMock()
    cursor.fetchall.return_value = []
    return cursor


def test_submit_lrc_wait_completion_forces_review_visibility():
    db_client = MagicMock()
    db_client.get_recording_by_song_id.return_value = _recording(lrc_status="pending")
    db_client.get_song.return_value = _song()

    analysis_client = MagicMock()
    analysis_client.submit_lrc.return_value = JobInfo(
        job_id="lrc-job-1",
        status="processing",
        job_type="lrc",
    )
    analysis_client.wait_for_completion.return_value = _completed_lrc_job()

    audio._submit_lrc_single(
        song_id="song_1",
        db_client=db_client,
        analysis_client=analysis_client,
        force=False,
        whisper_model="large-v3",
        language="zh",
        no_vocals=False,
        no_youtube=False,
        no_whisper_cache=False,
        no_qwen3=False,
        wait=True,
        console=_console(),
    )

    db_client.update_recording_lrc.assert_called_once_with(
        hash_prefix="abc123def456",
        r2_lrc_url="s3://bucket/abc123def456/lyrics.lrc",
        visibility_status="review",
    )


def test_status_sync_lrc_completion_forces_review_visibility():
    db_client = MagicMock()
    db_client.list_recordings.return_value = []
    db_client.get_recording_by_hash.return_value = _recording()

    cursor = MagicMock()
    cursor.fetchall.side_effect = [[("abc123def456",)], []]
    db_client.connection.cursor.return_value = cursor

    analysis_client = MagicMock()
    analysis_client.get_job.return_value = _completed_lrc_job()

    config = SimpleNamespace(
        analysis_url="http://analysis.example",
        r2_bucket="bucket",
        r2_endpoint_url="https://r2.example",
        r2_region="auto",
    )

    with (
        patch.object(audio.AdminConfig, "load", return_value=config),
        patch.object(audio, "get_db_client", return_value=db_client),
        patch.object(audio, "AnalysisClient", return_value=analysis_client),
    ):
        audio.check_status(
            job_id=None,
            sync=True,
            force_status=None,
            force_url=None,
            reconcile=False,
            config_path=None,
        )

    db_client.update_recording_lrc.assert_called_once_with(
        hash_prefix="abc123def456",
        r2_lrc_url="s3://bucket/abc123def456/lyrics.lrc",
        visibility_status="review",
    )


def test_status_reconcile_lrc_on_r2_forces_review_visibility():
    db_client = MagicMock()
    rec = _recording(lrc_status="failed")

    def list_recordings(**kwargs):
        if kwargs.get("lrc_status") == "incomplete":
            return [rec]
        return []

    db_client.list_recordings.side_effect = list_recordings
    db_client.connection.cursor.return_value = _empty_pending_cursor()

    r2_client = MagicMock()
    r2_client.lrc_exists.return_value = "s3://bucket/abc123def456/lyrics.lrc"

    config = SimpleNamespace(
        analysis_url="http://analysis.example",
        r2_bucket="bucket",
        r2_endpoint_url="https://r2.example",
        r2_region="auto",
    )

    with (
        patch.object(audio.AdminConfig, "load", return_value=config),
        patch.object(audio, "get_db_client", return_value=db_client),
        patch.object(audio, "R2Client", return_value=r2_client),
    ):
        audio.check_status(
            job_id=None,
            sync=False,
            force_status=None,
            force_url=None,
            reconcile=True,
            config_path=None,
        )

    db_client.update_recording_lrc.assert_called_once_with(
        hash_prefix="abc123def456",
        r2_lrc_url="s3://bucket/abc123def456/lyrics.lrc",
        visibility_status="review",
    )


def test_poll_all_jobs_completion_forces_review_visibility():
    db_client = MagicMock()
    db_client.get_recording_by_song_id.return_value = _recording()
    db_client.get_song.return_value = _song()

    analysis_client = MagicMock()
    analysis_client.get_job.return_value = _completed_lrc_job()

    r2_client = MagicMock()
    r2_client.lrc_exists.return_value = "s3://bucket/abc123def456/lyrics.lrc"

    results = {"song_1": {}}
    audio._poll_all_jobs(
        active_jobs={"song_1": "lrc-job-1"},
        results=results,
        db_client=db_client,
        analysis_client=analysis_client,
        r2_client=r2_client,
        force_lrc=False,
        stale_after_minutes=60,
        console=_console(),
    )

    db_client.update_recording_lrc.assert_called_once_with(
        "abc123def456",
        "s3://bucket/abc123def456/lyrics.lrc",
        visibility_status="review",
    )


def test_interrupt_reconciliation_forces_review_visibility():
    db_client = MagicMock()
    db_client.get_recording_by_song_id.return_value = _recording()
    db_client.get_song.return_value = _song()

    r2_client = MagicMock()
    r2_client.lrc_exists.return_value = "s3://bucket/abc123def456/lyrics.lrc"

    results = {"song_1": {}}
    audio._reconcile_on_interrupt(
        active_jobs={"song_1": "lrc-job-1"},
        results=results,
        db_client=db_client,
        r2_client=r2_client,
        console=_console(),
    )

    db_client.update_recording_lrc.assert_called_once_with(
        "abc123def456",
        "s3://bucket/abc123def456/lyrics.lrc",
        visibility_status="review",
    )
