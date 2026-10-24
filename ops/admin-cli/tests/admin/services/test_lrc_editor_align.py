"""Tests for the in-editor LRC forced-alignment pipeline."""

import threading
from unittest.mock import MagicMock

import pytest

from stream_of_worship.admin.db.models import Recording, Song
from stream_of_worship.admin.editor.align import align_lrc_for_editor
from stream_of_worship.admin.services.analysis import (
    AnalysisResult,
    AnalysisServiceError,
    JobInfo,
)
from stream_of_worship.admin.services.r2 import R2ObjectIdentity

HASH_PREFIX = "abc123def456"
LRC_URL = f"s3://bucket/{HASH_PREFIX}/lyrics.lrc"


def _recording(**overrides) -> Recording:
    base = dict(
        content_hash=f"{HASH_PREFIX}{'0' * 52}",
        hash_prefix=HASH_PREFIX,
        original_filename="song.mp3",
        file_size_bytes=1024,
        imported_at="2026-01-01T00:00:00+00:00",
        song_id="song_0001",
        r2_audio_url=f"s3://bucket/{HASH_PREFIX}/audio.mp3",
        r2_lrc_url=LRC_URL,
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
        lyrics_raw="catalog one\ncatalog two",
    )
    base.update(overrides)
    return Song(**base)


def _clients(*, recording=None, song=None):
    db_client = MagicMock()
    db_client.get_recording_by_hash.return_value = _recording() if recording is None else recording
    db_client.get_song.return_value = _song() if song is None else song
    r2_client = MagicMock()
    analysis_client = MagicMock()
    return db_client, r2_client, analysis_client


def _queued_job(job_id="job-1"):
    return JobInfo(job_id=job_id, status="queued", job_type="forced_alignment")


def _completed_job(job_id="job-1", lrc_url: str | None = LRC_URL):
    return JobInfo(
        job_id=job_id,
        status="completed",
        job_type="forced_alignment",
        progress=1.0,
        stage="done",
        result=AnalysisResult(lrc_url=lrc_url),
    )


def _run(db_client, r2_client, analysis_client, **kwargs):
    return align_lrc_for_editor(
        analysis_client=analysis_client,
        db_client=db_client,
        r2_client=r2_client,
        db_lock=threading.RLock(),
        hash_prefix=HASH_PREFIX,
        r2_retry_interval=0.0,
        **kwargs,
    )


def test_align_submits_and_returns_redownloaded_content():
    db_client, r2_client, analysis_client = _clients()
    analysis_client.submit_forced_alignment.return_value = _queued_job()
    analysis_client.wait_for_completion.return_value = _completed_job()
    r2_client.download_lrc_content.side_effect = [
        "[00:01.00]catalog one\n[00:02.00]catalog two",  # lyrics payload read
        "[00:01.10]catalog one\n[00:02.20]catalog two",  # post-align refresh
    ]
    r2_client.get_lrc_identity.return_value = R2ObjectIdentity(exists=True, etag="aligned")

    result = _run(db_client, r2_client, analysis_client)

    assert result.ok is True
    assert result.lrc_content == "[00:01.10]catalog one\n[00:02.20]catalog two"
    assert result.identity.etag == "aligned"
    assert result.job_id == "job-1"

    submit_kwargs = analysis_client.submit_forced_alignment.call_args.kwargs
    assert submit_kwargs["audio_url"] == f"s3://bucket/{HASH_PREFIX}/audio.mp3"
    assert submit_kwargs["content_hash"] == f"{HASH_PREFIX}{'0' * 52}"
    assert submit_kwargs["song_title"] == "Amazing Grace"
    assert submit_kwargs["force"] is True
    assert submit_kwargs["language"] == "auto"
    assert submit_kwargs["use_vocals_stem"] is True

    db_client.update_recording_status.assert_called_once_with(
        hash_prefix=HASH_PREFIX, lrc_status="processing", lrc_job_id="job-1"
    )
    db_client.update_recording_lrc.assert_called_once_with(
        hash_prefix=HASH_PREFIX,
        r2_lrc_url=LRC_URL,
        visibility_status="review",
        lrc_source="forced_alignment",
    )


def test_align_passes_language_through():
    db_client, r2_client, analysis_client = _clients()
    analysis_client.submit_forced_alignment.return_value = _queued_job()
    analysis_client.wait_for_completion.return_value = _completed_job()
    r2_client.download_lrc_content.return_value = "[00:01.00]catalog one"
    r2_client.get_lrc_identity.return_value = R2ObjectIdentity(exists=True, etag="e")

    result = _run(db_client, r2_client, analysis_client, language="zh")

    assert result.ok is True
    assert analysis_client.submit_forced_alignment.call_args.kwargs["language"] == "zh"


def test_align_prefers_existing_lrc_text_over_catalog():
    """Alignment retimes the published words; it must not re-send catalog text."""
    db_client, r2_client, analysis_client = _clients()
    analysis_client.submit_forced_alignment.return_value = _queued_job()
    analysis_client.wait_for_completion.return_value = _completed_job()
    r2_client.download_lrc_content.side_effect = [
        "[00:05.00]existing one\n[00:06.00]existing two",
        "[00:05.10]existing one\n[00:06.10]existing two",
    ]
    r2_client.get_lrc_identity.return_value = R2ObjectIdentity(exists=True, etag="e")

    result = _run(db_client, r2_client, analysis_client)

    assert result.ok is True
    assert (
        analysis_client.submit_forced_alignment.call_args.kwargs["lyrics_text"]
        == "existing one\nexisting two"
    )


def test_align_falls_back_to_catalog_lyrics_when_lrc_is_missing():
    db_client, r2_client, analysis_client = _clients(recording=_recording(r2_lrc_url=None))
    analysis_client.submit_forced_alignment.return_value = _queued_job()
    analysis_client.wait_for_completion.return_value = _completed_job()
    r2_client.download_lrc_content.return_value = "[00:01.00]aligned"
    r2_client.get_lrc_identity.return_value = R2ObjectIdentity(exists=True, etag="e")

    result = _run(db_client, r2_client, analysis_client)

    assert result.ok is True
    assert (
        analysis_client.submit_forced_alignment.call_args.kwargs["lyrics_text"]
        == "catalog one\ncatalog two"
    )


def test_align_marks_failed_when_job_fails():
    db_client, r2_client, analysis_client = _clients()
    analysis_client.submit_forced_alignment.return_value = _queued_job()
    analysis_client.wait_for_completion.return_value = JobInfo(
        job_id="job-1",
        status="failed",
        job_type="forced_alignment",
        error_message="aligner crashed",
    )

    result = _run(db_client, r2_client, analysis_client)

    assert result.ok is False
    assert result.error == "aligner crashed"
    db_client.update_recording_status.assert_called_with(
        hash_prefix=HASH_PREFIX, lrc_status="failed"
    )
    db_client.update_recording_lrc.assert_not_called()


def test_align_marks_failed_when_polling_raises():
    db_client, r2_client, analysis_client = _clients()
    analysis_client.submit_forced_alignment.return_value = _queued_job()
    analysis_client.wait_for_completion.side_effect = AnalysisServiceError(
        "Timed out waiting for job job-1 after 1200.0s"
    )

    result = _run(db_client, r2_client, analysis_client)

    assert result.ok is False
    assert "Forced alignment failed" in result.error
    db_client.update_recording_status.assert_called_with(
        hash_prefix=HASH_PREFIX, lrc_status="failed"
    )


def test_align_blocks_published_recording():
    db_client, r2_client, analysis_client = _clients(
        recording=_recording(visibility_status="published")
    )

    result = _run(db_client, r2_client, analysis_client)

    assert result.ok is False
    assert "Refusing to re-align LRC" in result.error
    analysis_client.submit_forced_alignment.assert_not_called()
    db_client.update_recording_status.assert_not_called()


def test_align_reports_submit_failure_without_touching_status():
    db_client, r2_client, analysis_client = _clients()
    analysis_client.submit_forced_alignment.side_effect = AnalysisServiceError("HTTP 503")

    result = _run(db_client, r2_client, analysis_client)

    assert result.ok is False
    assert "Failed to submit forced alignment job" in result.error
    db_client.update_recording_status.assert_not_called()


def test_align_returns_error_when_r2_content_missing():
    db_client, r2_client, analysis_client = _clients()
    analysis_client.submit_forced_alignment.return_value = _queued_job()
    analysis_client.wait_for_completion.return_value = _completed_job()
    r2_client.download_lrc_content.return_value = None

    result = _run(db_client, r2_client, analysis_client, r2_retry_attempts=2)

    assert result.ok is False
    assert "not readable from R2 yet" in result.error
    assert result.job_id == "job-1"
    # The job did complete, so the DB result write still happened.
    db_client.update_recording_lrc.assert_called_once()


def test_align_falls_back_to_existing_r2_object_when_url_missing():
    db_client, r2_client, analysis_client = _clients()
    analysis_client.submit_forced_alignment.return_value = _queued_job()
    analysis_client.wait_for_completion.return_value = _completed_job(lrc_url=None)
    r2_client.lrc_exists.return_value = LRC_URL
    r2_client.download_lrc_content.return_value = "[00:01.00]aligned"
    r2_client.get_lrc_identity.return_value = R2ObjectIdentity(exists=True, etag="e")

    result = _run(db_client, r2_client, analysis_client)

    assert result.ok is True
    assert db_client.update_recording_lrc.call_args.kwargs["r2_lrc_url"] == LRC_URL


def test_align_reports_progress_to_callback():
    db_client, r2_client, analysis_client = _clients()
    analysis_client.submit_forced_alignment.return_value = _queued_job()

    def _wait(job_id, poll_interval, timeout, callback):
        callback(
            JobInfo(
                job_id=job_id,
                status="processing",
                job_type="forced_alignment",
                progress=0.5,
                stage="qwen3_align",
            )
        )
        return _completed_job()

    analysis_client.wait_for_completion.side_effect = _wait
    r2_client.download_lrc_content.return_value = "[00:01.00]aligned"
    r2_client.get_lrc_identity.return_value = R2ObjectIdentity(exists=True, etag="e")

    seen: list[str] = []
    result = _run(db_client, r2_client, analysis_client, on_progress=seen.append)

    assert result.ok is True
    assert seen == ["Aligning LRC… 50% [qwen3_align]"]


def test_align_missing_recording_is_an_error():
    db_client, r2_client, analysis_client = _clients(recording=False)

    result = _run(db_client, r2_client, analysis_client)

    assert result.ok is False
    assert "No recording found" in result.error


@pytest.mark.parametrize(
    ("duration_seconds", "expected_timeout"),
    [
        (None, 1200.0),  # unknown duration: base budget
        (240.0, 1200.0),  # single chunk
        (300.0, 1200.0),  # still a single chunk
        (301.0, 1320.0),  # 2 chunks: +120s
        (890.0, 1440.0),  # 3 chunks: +240s
    ],
)
def test_align_wait_budget_scales_with_chunk_count(duration_seconds, expected_timeout):
    db_client, r2_client, analysis_client = _clients(
        recording=_recording(duration_seconds=duration_seconds)
    )
    analysis_client.submit_forced_alignment.return_value = _queued_job()
    analysis_client.wait_for_completion.return_value = _completed_job()
    r2_client.download_lrc_content.return_value = "[00:01.00]aligned"
    r2_client.get_lrc_identity.return_value = R2ObjectIdentity(exists=True, etag="e")

    result = _run(db_client, r2_client, analysis_client)

    assert result.ok is True
    assert analysis_client.wait_for_completion.call_args.kwargs["timeout"] == expected_timeout
