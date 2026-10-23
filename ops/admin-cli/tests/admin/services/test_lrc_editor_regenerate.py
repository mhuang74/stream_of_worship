"""Tests for the in-editor LRC force-regeneration pipeline."""

import threading
from unittest.mock import MagicMock

from stream_of_worship.admin.db.models import Recording, Song
from stream_of_worship.admin.editor.regenerate import regenerate_lrc_for_editor
from stream_of_worship.admin.services.analysis import (
    AnalysisResult,
    AnalysisServiceError,
    JobInfo,
)
from stream_of_worship.admin.services.r2 import R2ObjectIdentity

HASH_PREFIX = "abc123def456"


def _recording(**overrides) -> Recording:
    base = dict(
        content_hash=f"{HASH_PREFIX}{'0' * 52}",
        hash_prefix=HASH_PREFIX,
        original_filename="song.mp3",
        file_size_bytes=1024,
        imported_at="2026-01-01T00:00:00+00:00",
        song_id="song_0001",
        r2_audio_url=f"s3://bucket/{HASH_PREFIX}/audio.mp3",
        youtube_url="https://www.youtube.com/watch?v=xyz",
        tempo_bpm=72.0,
        duration_seconds=240.0,
        lrc_status="completed",
        visibility_status="review",
    )
    base.update(overrides)
    return Recording(**base)


def _song(**overrides) -> Song:
    base = dict(
        id="song_0001",
        title="Amazing Grace",
        source_url="https://sop.org/song/1",
        scraped_at="2026-01-01T00:00:00+00:00",
        lyrics_raw="line one\nline two",
    )
    base.update(overrides)
    return Song(**base)


def _clients(*, recording=None, song=None):
    db_client = MagicMock()
    db_client.get_recording_by_hash.return_value = (
        _recording() if recording is None else recording
    )
    db_client.get_song.return_value = _song() if song is None else song
    r2_client = MagicMock()
    analysis_client = MagicMock()
    return db_client, r2_client, analysis_client


def _completed_job(job_id="job-1", lrc_url=f"s3://bucket/{HASH_PREFIX}/lyrics.lrc"):
    return JobInfo(
        job_id=job_id,
        status="completed",
        job_type="lrc",
        progress=1.0,
        stage="done",
        result=AnalysisResult(lrc_url=lrc_url, lrc_source="qwen3_asr"),
    )


def _run(db_client, r2_client, analysis_client, **kwargs):
    return regenerate_lrc_for_editor(
        analysis_client=analysis_client,
        db_client=db_client,
        r2_client=r2_client,
        db_lock=threading.RLock(),
        song_id="song_0001",
        hash_prefix=HASH_PREFIX,
        r2_retry_interval=0.0,
        **kwargs,
    )


def test_regenerate_submits_force_and_returns_redownloaded_content():
    db_client, r2_client, analysis_client = _clients()
    analysis_client.submit_lrc.return_value = JobInfo(
        job_id="job-1", status="queued", job_type="lrc"
    )
    analysis_client.wait_for_completion.return_value = _completed_job()
    r2_client.download_lrc_content.return_value = "[00:01.00]New A\n[00:02.00]New B"
    r2_client.get_lrc_identity.return_value = R2ObjectIdentity(exists=True, etag="new-etag")

    result = _run(db_client, r2_client, analysis_client)

    assert result.ok is True
    assert result.lrc_content == "[00:01.00]New A\n[00:02.00]New B"
    assert result.identity.etag == "new-etag"
    assert result.job_id == "job-1"

    # Force submission with the recording's own metadata and untouched defaults.
    submit_kwargs = analysis_client.submit_lrc.call_args.kwargs
    assert submit_kwargs["force"] is True
    assert submit_kwargs["youtube_url"] == "https://www.youtube.com/watch?v=xyz"
    assert submit_kwargs["audio_url"] == f"s3://bucket/{HASH_PREFIX}/audio.mp3"
    assert submit_kwargs["lyrics_text"] == "line one\nline two"
    assert submit_kwargs["song_title"] == "Amazing Grace"
    assert submit_kwargs["song_id"] == "song_0001"
    assert "whisper_model" not in submit_kwargs
    assert "language" not in submit_kwargs
    assert "use_qwen3_asr" not in submit_kwargs

    # Status went processing -> completed (via update_recording_lrc, review).
    db_client.update_recording_status.assert_called_once_with(
        hash_prefix=HASH_PREFIX, lrc_status="processing", lrc_job_id="job-1"
    )
    db_client.update_recording_lrc.assert_called_once_with(
        hash_prefix=HASH_PREFIX,
        r2_lrc_url=f"s3://bucket/{HASH_PREFIX}/lyrics.lrc",
        visibility_status="review",
        lrc_source="qwen3_asr",
    )


def test_regenerate_marks_failed_when_job_fails():
    db_client, r2_client, analysis_client = _clients()
    analysis_client.submit_lrc.return_value = JobInfo(
        job_id="job-1", status="queued", job_type="lrc"
    )
    analysis_client.wait_for_completion.return_value = JobInfo(
        job_id="job-1", status="failed", job_type="lrc", error_message="whisper crashed"
    )

    result = _run(db_client, r2_client, analysis_client)

    assert result.ok is False
    assert result.error == "whisper crashed"
    db_client.update_recording_status.assert_called_with(
        hash_prefix=HASH_PREFIX, lrc_status="failed"
    )
    db_client.update_recording_lrc.assert_not_called()
    r2_client.download_lrc_content.assert_not_called()


def test_regenerate_marks_failed_when_polling_raises():
    db_client, r2_client, analysis_client = _clients()
    analysis_client.submit_lrc.return_value = JobInfo(
        job_id="job-1", status="queued", job_type="lrc"
    )
    analysis_client.wait_for_completion.side_effect = AnalysisServiceError(
        "Timed out waiting for job job-1 after 600.0s"
    )

    result = _run(db_client, r2_client, analysis_client)

    assert result.ok is False
    assert "LRC generation failed" in result.error
    db_client.update_recording_status.assert_called_with(
        hash_prefix=HASH_PREFIX, lrc_status="failed"
    )


def test_regenerate_blocks_published_recording():
    db_client, r2_client, analysis_client = _clients(
        recording=_recording(visibility_status="published")
    )

    result = _run(db_client, r2_client, analysis_client)

    assert result.ok is False
    assert "Refusing to regenerate LRC" in result.error
    analysis_client.submit_lrc.assert_not_called()
    db_client.update_recording_status.assert_not_called()


def test_regenerate_returns_error_when_r2_content_missing():
    db_client, r2_client, analysis_client = _clients()
    analysis_client.submit_lrc.return_value = JobInfo(
        job_id="job-1", status="queued", job_type="lrc"
    )
    analysis_client.wait_for_completion.return_value = _completed_job()
    r2_client.download_lrc_content.return_value = None

    result = _run(db_client, r2_client, analysis_client, r2_retry_attempts=2)

    assert result.ok is False
    assert "not readable from R2 yet" in result.error
    assert r2_client.download_lrc_content.call_count == 2
    # The job did complete, so the DB result write still happened.
    db_client.update_recording_lrc.assert_called_once()
    assert result.job_id == "job-1"


def test_regenerate_reports_progress_to_callback():
    db_client, r2_client, analysis_client = _clients()
    analysis_client.submit_lrc.return_value = JobInfo(
        job_id="job-1", status="queued", job_type="lrc"
    )

    def _wait(job_id, poll_interval, timeout, callback):
        callback(JobInfo(job_id=job_id, status="processing", job_type="lrc",
                         progress=0.5, stage="whisper"))
        return _completed_job()

    analysis_client.wait_for_completion.side_effect = _wait
    r2_client.download_lrc_content.return_value = "[00:01.00]A"
    r2_client.get_lrc_identity.return_value = R2ObjectIdentity(exists=True, etag="e")

    seen = []
    result = _run(db_client, r2_client, analysis_client, on_progress=seen.append)

    assert result.ok is True
    assert seen == ["Regenerating LRC… 50% [whisper]"]
