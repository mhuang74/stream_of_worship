"""Tests for submit_lrc_batch (direct call, mocked DB + analysis client).

Regression: the batch path referenced an undefined ``youtube_url`` local
(NameError), so piping multiple song IDs into
``sow_admin lyrics generate --stdin`` failed every song with
"Unexpected error: name 'youtube_url' is not defined".
"""

from unittest.mock import MagicMock, patch

from rich.console import Console

from stream_of_worship.admin.services import lrc_jobs
from stream_of_worship.admin.services.lrc_jobs import submit_lrc_batch


def _make_recording():
    recording = MagicMock()
    recording.lrc_status = "pending"
    recording.lrc_job_id = None
    recording.r2_audio_url = "https://r2.example.com/audio/song.mp3"
    recording.content_hash = "abc123"
    recording.hash_prefix = "abc"
    recording.youtube_url = "https://youtu.be/x"
    recording.tempo_bpm = 72.0
    return recording


def _make_db(recording):
    db = MagicMock()
    db.get_recording_by_song_id.return_value = recording
    db.get_song.return_value = MagicMock(title="Test Song")
    return db


def _make_analysis_client():
    client = MagicMock()
    client.submit_lrc.return_value = MagicMock(job_id="job_1")
    return client


def _run_batch(no_youtube=False, force=False):
    recording = _make_recording()
    db = _make_db(recording)
    analysis = _make_analysis_client()
    console = Console()
    with patch.object(lrc_jobs, "resolve_lyrics_text", return_value="lyrics"):
        submit_lrc_batch(
            song_ids=["song_001"],
            db_client=db,
            analysis_client=analysis,
            force=force,
            whisper_model="base",
            language="zh",
            no_vocals=False,
            no_youtube=no_youtube,
            no_whisper_cache=False,
            no_qwen3_asr=False,
            force_qwen3_asr=False,
            console=console,
        )
    return db, analysis


def test_submit_lrc_batch_passes_recording_youtube_url():
    db, analysis = _run_batch()

    analysis.submit_lrc.assert_called_once()
    assert (
        analysis.submit_lrc.call_args.kwargs["youtube_url"]
        == "https://youtu.be/x"
    )
    db.update_recording_status.assert_called_once_with(
        hash_prefix="abc", lrc_status="processing", lrc_job_id="job_1"
    )


def test_submit_lrc_batch_no_youtube_passes_empty_string():
    _db, analysis = _run_batch(no_youtube=True)

    analysis.submit_lrc.assert_called_once()
    assert analysis.submit_lrc.call_args.kwargs["youtube_url"] == ""


def test_submit_lrc_batch_skips_processing_without_force():
    recording = _make_recording()
    recording.lrc_status = "processing"
    recording.lrc_job_id = "job_existing"
    db = _make_db(recording)
    analysis = _make_analysis_client()
    console = Console()
    with patch.object(lrc_jobs, "resolve_lyrics_text", return_value="lyrics"):
        submit_lrc_batch(
            song_ids=["song_001"],
            db_client=db,
            analysis_client=analysis,
            force=False,
            whisper_model="base",
            language="zh",
            no_vocals=False,
            no_youtube=False,
            no_whisper_cache=False,
            no_qwen3_asr=False,
            force_qwen3_asr=False,
            console=console,
        )

    analysis.submit_lrc.assert_not_called()
    db.update_recording_status.assert_not_called()
