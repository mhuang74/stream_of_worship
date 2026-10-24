"""Tests for LRC completion visibility behavior in admin audio helpers."""

import io
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from rich.console import Console

from stream_of_worship.admin.commands import audio
from stream_of_worship.admin.services import lrc_jobs
from stream_of_worship.admin.db.models import Recording, Song
from stream_of_worship.admin.services.analysis import (
    AnalysisResult,
    AnalysisServiceError,
    JobInfo,
)


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

    lrc_jobs.submit_lrc_single(
        song_id="song_1",
        db_client=db_client,
        analysis_client=analysis_client,
        force=False,
        whisper_model="large-v3",
        language="zh",
        no_vocals=False,
        generator="auto",
        no_cache=False,
        wait=True,
        console=_console(),
    )

    db_client.update_recording_lrc.assert_called_once_with(
        hash_prefix="abc123def456",
        r2_lrc_url="s3://bucket/abc123def456/lyrics.lrc",
        visibility_status="review",
        lrc_source="whisper_asr",
    )
    analysis_client.submit_lrc.assert_called_once()
    submit_kwargs = analysis_client.submit_lrc.call_args.kwargs
    assert submit_kwargs["song_title"] == "Test Song"
    assert submit_kwargs["language"] == "zh"


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
        lrc_source="whisper_asr",
    )


def test_status_reconcile_lrc_on_r2_preserves_visibility():
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
        visibility_status=None,
        lrc_source="r2_preexisting",
    )


def test_status_reconcile_backfills_r2_audio_url():
    db_client = MagicMock()
    rec = _recording(
        download_status="failed",
        lrc_status="completed",
        analysis_status="completed",
        r2_audio_url=None,
    )

    def list_recordings(**kwargs):
        if kwargs.get("download_status") == "incomplete":
            return [rec]
        return []

    db_client.list_recordings.side_effect = list_recordings

    r2_client = MagicMock()
    r2_client.lrc_exists.return_value = None
    r2_client.analysis_exists.return_value = None
    r2_client.audio_url.return_value = "s3://bucket/abc123def456/audio.mp3"

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

    db_client.update_recording_download.assert_called_once_with(
        hash_prefix="abc123def456", download_status="completed"
    )
    db_client.update_recording_r2_url.assert_called_once_with(
        hash_prefix="abc123def456",
        r2_audio_url="s3://bucket/abc123def456/audio.mp3",
    )
    db_client.update_recording_status.assert_not_called()


def test_status_reconcile_leaves_download_incomplete_when_url_backfill_fails():
    """A failed URL write must not mark the download completed.

    Reconcile only queues incomplete downloads, so completing the status first would
    strand a recording with no ``r2_audio_url`` that no later reconcile revisits.
    """
    db_client = MagicMock()
    rec = _recording(
        download_status="failed",
        lrc_status="completed",
        analysis_status="completed",
        r2_audio_url=None,
    )

    def list_recordings(**kwargs):
        if kwargs.get("download_status") == "incomplete":
            return [rec]
        return []

    db_client.list_recordings.side_effect = list_recordings
    db_client.update_recording_r2_url.side_effect = RuntimeError("db down")

    r2_client = MagicMock()
    r2_client.lrc_exists.return_value = None
    r2_client.analysis_exists.return_value = None
    r2_client.audio_url.return_value = "s3://bucket/abc123def456/audio.mp3"

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
        pytest.raises(RuntimeError, match="db down"),
    ):
        audio.check_status(
            job_id=None,
            sync=False,
            force_status=None,
            force_url=None,
            reconcile=True,
            config_path=None,
        )

    db_client.update_recording_r2_url.assert_called_once_with(
        hash_prefix="abc123def456",
        r2_audio_url="s3://bucket/abc123def456/audio.mp3",
    )
    db_client.update_recording_download.assert_not_called()


def test_status_reconcile_keeps_existing_audio_url():
    db_client = MagicMock()
    rec = _recording(
        download_status="failed",
        lrc_status="completed",
        analysis_status="completed",
    )

    def list_recordings(**kwargs):
        if kwargs.get("download_status") == "incomplete":
            return [rec]
        return []

    db_client.list_recordings.side_effect = list_recordings

    r2_client = MagicMock()
    r2_client.lrc_exists.return_value = None
    r2_client.analysis_exists.return_value = None
    r2_client.audio_url.return_value = "s3://bucket/abc123def456/audio.mp3"

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

    db_client.update_recording_download.assert_called_once_with(
        hash_prefix="abc123def456", download_status="completed"
    )
    db_client.update_recording_r2_url.assert_not_called()
    db_client.update_recording_status.assert_not_called()


def test_handle_lrc_completion_forces_review_visibility():
    db_client = MagicMock()
    db_client.get_recording_by_song_id.return_value = _recording()
    db_client.get_song.return_value = _song()

    analysis_client = MagicMock()

    r2_client = MagicMock()
    r2_client.lrc_exists.return_value = "s3://bucket/abc123def456/lyrics.lrc"

    results = {"song_1": {}}
    resubmit_counts = {}

    with patch.object(audio, "_confirm_r2_lrc", return_value="s3://bucket/abc123def456/lyrics.lrc"):
        is_terminal, _ = audio._handle_lrc_completion(
            song_id="song_1",
            job_id="lrc-job-1",
            job=_completed_lrc_job(),
            db_client=db_client,
            analysis_client=analysis_client,
            r2_client=r2_client,
            force=False,
            stale_after_minutes=60,
            console=_console(),
            results=results,
            _add_manifest_entry=lambda *a, **k: None,
            resubmit_counts=resubmit_counts,
        )

    assert is_terminal is True
    db_client.update_recording_lrc.assert_called_once_with(
        "abc123def456",
        "s3://bucket/abc123def456/lyrics.lrc",
        visibility_status="review",
        lrc_source="whisper_asr",
    )


def test_interrupt_reconciliation_preserves_visibility():
    db_client = MagicMock()
    db_client.get_recording_by_song_id.return_value = _recording()
    db_client.get_song.return_value = _song()

    r2_client = MagicMock()
    r2_client.lrc_exists.return_value = "s3://bucket/abc123def456/lyrics.lrc"

    results = {"song_1": {}}
    audio._reconcile_on_interrupt(
        active_jobs={("song_1", "generate_lyrics"): "lrc-job-1"},
        results=results,
        db_client=db_client,
        r2_client=r2_client,
        console=_console(),
        config=MagicMock(),
    )

    db_client.update_recording_lrc.assert_called_once_with(
        "abc123def456",
        "s3://bucket/abc123def456/lyrics.lrc",
        visibility_status=None,
        lrc_source="r2_preexisting",
    )


def test_submit_lrc_for_song_skip_r2_preserves_visibility():
    """Pre-existing LRC on R2 (skip path) preserves existing visibility."""
    db_client = MagicMock()
    db_client.get_recording_by_song_id.return_value = _recording()
    db_client.get_song.return_value = _song()

    analysis_client = MagicMock()
    r2_client = MagicMock()
    r2_client.lrc_exists.return_value = "s3://bucket/abc123def456/lyrics.lrc"

    results = {"song_1": {}}
    active_lrc_jobs: dict = {}
    lrc_attempted: set = set()

    status = audio._submit_lrc_for_song(
        song_id="song_1",
        db_client=db_client,
        analysis_client=analysis_client,
        r2_client=r2_client,
        force=False,
        stale_after_minutes=60,
        console=_console(),
        results=results,
        active_lrc_jobs=active_lrc_jobs,
        lrc_attempted=lrc_attempted,
        _add_manifest_entry=lambda *a, **k: None,
    )

    assert status == "skipped_r2"
    assert results["song_1"]["generate_lyrics"] == "completed"
    assert results["song_1"]["generate_lyrics_source"] == "r2_preexisting"
    analysis_client.submit_lrc.assert_not_called()
    db_client.update_recording_lrc.assert_called_once_with(
        "abc123def456",
        "s3://bucket/abc123def456/lyrics.lrc",
        visibility_status=None,
        lrc_source="r2_preexisting",
    )


def test_handle_lrc_404_lost_job_preserves_visibility():
    """Pre-existing LRC on R2 (lost-job 404 path) preserves existing visibility."""
    db_client = MagicMock()
    db_client.get_recording_by_song_id.return_value = _recording()
    db_client.get_song.return_value = _song()

    analysis_client = MagicMock()
    r2_client = MagicMock()
    r2_client.lrc_exists.return_value = "s3://bucket/abc123def456/lyrics.lrc"

    results = {"song_1": {}}
    resubmit_counts = {}

    is_terminal, new_job_id = audio._handle_lrc_404(
        song_id="song_1",
        job_id="lrc-job-1",
        db_client=db_client,
        analysis_client=analysis_client,
        r2_client=r2_client,
        force=False,
        console=_console(),
        results=results,
        _add_manifest_entry=lambda *a, **k: None,
        resubmit_counts=resubmit_counts,
    )

    assert is_terminal is True
    assert new_job_id is None
    assert results["song_1"]["generate_lyrics"] == "completed"
    analysis_client.submit_lrc.assert_not_called()
    db_client.update_recording_lrc.assert_called_once_with(
        "abc123def456",
        "s3://bucket/abc123def456/lyrics.lrc",
        visibility_status=None,
        lrc_source="r2_preexisting",
    )


def test_status_reconcile_skips_pending_table():
    """--reconcile prints its summary and returns without the pending recordings table."""
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

    output = io.StringIO()
    with (
        patch.object(audio.AdminConfig, "load", return_value=config),
        patch.object(audio, "get_db_client", return_value=db_client),
        patch.object(audio, "R2Client", return_value=r2_client),
        patch.object(audio, "console", Console(file=output, force_terminal=False)),
    ):
        audio.check_status(
            job_id=None,
            sync=False,
            force_status=None,
            force_url=None,
            reconcile=True,
            config_path=None,
        )

    text = output.getvalue()
    assert "Reconciled 1 LRC" in text
    assert "Pending Recordings" not in text
    db_client.connection.cursor.return_value.execute.assert_not_called()


def test_status_sync_skips_pending_table():
    """--sync prints its summary and returns without the pending recordings table."""
    db_client = MagicMock()
    db_client.list_recordings.return_value = []
    db_client.get_recording_by_hash.return_value = _recording()

    cursor = MagicMock()
    cursor.fetchall.side_effect = [[("abc123def456",)]]
    db_client.connection.cursor.return_value = cursor

    analysis_client = MagicMock()
    analysis_client.get_job.return_value = _completed_lrc_job()

    config = SimpleNamespace(
        analysis_url="http://analysis.example",
        r2_bucket="bucket",
        r2_endpoint_url="https://r2.example",
        r2_region="auto",
    )

    output = io.StringIO()
    with (
        patch.object(audio.AdminConfig, "load", return_value=config),
        patch.object(audio, "get_db_client", return_value=db_client),
        patch.object(audio, "AnalysisClient", return_value=analysis_client),
        patch.object(audio, "console", Console(file=output, force_terminal=False)),
    ):
        audio.check_status(
            job_id=None,
            sync=True,
            force_status=None,
            force_url=None,
            reconcile=False,
            config_path=None,
        )

    text = output.getvalue()
    assert "Synced 1 job(s)" in text
    assert "Pending Recordings" not in text
    assert cursor.execute.call_count == 1


def test_status_sync_summarizes_repeated_job_not_found():
    """Repeated 404s collapse into one grouped line instead of one per job."""
    db_client = MagicMock()
    db_client.list_recordings.return_value = []
    db_client.get_recording_by_hash.side_effect = [
        _recording(hash_prefix="hash_a", lrc_job_id="job_a"),
        _recording(hash_prefix="hash_b", lrc_job_id="job_b"),
    ]

    cursor = MagicMock()
    cursor.fetchall.return_value = [("hash_a",), ("hash_b",)]
    db_client.connection.cursor.return_value = cursor

    analysis_client = MagicMock()
    analysis_client.get_job.side_effect = [
        AnalysisServiceError("Job not found: job_a", status_code=404),
        AnalysisServiceError("Job not found: job_b", status_code=404),
    ]

    config = SimpleNamespace(
        analysis_url="http://analysis.example",
        r2_bucket="bucket",
        r2_endpoint_url="https://r2.example",
        r2_region="auto",
    )

    output = io.StringIO()
    with (
        patch.object(audio.AdminConfig, "load", return_value=config),
        patch.object(audio, "get_db_client", return_value=db_client),
        patch.object(audio, "AnalysisClient", return_value=analysis_client),
        patch.object(audio, "console", Console(file=output, force_terminal=False)),
    ):
        audio.check_status(
            job_id=None,
            sync=True,
            force_status=None,
            force_url=None,
            reconcile=False,
            config_path=None,
        )

    text = output.getvalue()
    assert "Failed to sync 2 job(s):" in text
    assert "2 × LRC: Job not found: <job>" in text
    assert text.count("Job not found: <job>") == 1
    assert "Could not sync" not in text


def test_status_sync_summary_separates_error_kinds():
    """Analysis and LRC failures with different messages form separate groups."""
    db_client = MagicMock()
    db_client.list_recordings.return_value = []
    db_client.get_recording_by_hash.return_value = _recording(
        hash_prefix="hash_a",
        analysis_job_id="job_an",
        analysis_status="processing",
        lrc_job_id="job_lrc",
    )

    cursor = MagicMock()
    cursor.fetchall.return_value = [("hash_a",)]
    db_client.connection.cursor.return_value = cursor

    analysis_client = MagicMock()
    analysis_client.get_job.side_effect = [
        AnalysisServiceError("Failed to get job status (HTTP 500): boom", status_code=500),
        AnalysisServiceError("Job not found: job_lrc", status_code=404),
    ]

    config = SimpleNamespace(
        analysis_url="http://analysis.example",
        r2_bucket="bucket",
        r2_endpoint_url="https://r2.example",
        r2_region="auto",
    )

    output = io.StringIO()
    with (
        patch.object(audio.AdminConfig, "load", return_value=config),
        patch.object(audio, "get_db_client", return_value=db_client),
        patch.object(audio, "AnalysisClient", return_value=analysis_client),
        patch.object(audio, "console", Console(file=output, force_terminal=False)),
    ):
        audio.check_status(
            job_id=None,
            sync=True,
            force_status=None,
            force_url=None,
            reconcile=False,
            config_path=None,
        )

    text = output.getvalue()
    assert "Failed to sync 2 job(s):" in text
    assert "1 × analysis: Failed to get job status (HTTP 500): boom" in text
    assert "1 × LRC: Job not found: <job>" in text
