"""Tests for submit_lrc_batch (direct call, mocked DB + analysis client).

Regression: the batch path referenced an undefined ``youtube_url`` local
(NameError), so piping multiple song IDs into
``sow_admin lyrics generate --stdin`` failed every song with
"Unexpected error: name 'youtube_url' is not defined".
"""

from unittest.mock import MagicMock, patch

from rich.console import Console

from stream_of_worship.admin.services import lrc_jobs
from stream_of_worship.admin.services.lrc_jobs import submit_lrc_batch, submit_lrc_single


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


def _run_batch(generator="auto", force=False, no_cache=False):
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
            generator=generator,
            no_cache=no_cache,
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


def test_submit_lrc_batch_whisper_generator_clears_youtube():
    _db, analysis = _run_batch(generator="whisper")

    analysis.submit_lrc.assert_called_once()
    kwargs = analysis.submit_lrc.call_args.kwargs
    assert kwargs["youtube_url"] == ""
    assert kwargs["use_qwen3_asr"] is False
    assert kwargs["lrc_generator"] == "whisper_asr"


def test_submit_lrc_batch_qwen3_generator_skips_youtube():
    _db, analysis = _run_batch(generator="qwen3")

    analysis.submit_lrc.assert_called_once()
    kwargs = analysis.submit_lrc.call_args.kwargs
    assert kwargs["youtube_url"] == ""
    assert kwargs["use_qwen3_asr"] is True
    assert kwargs["lrc_generator"] == "qwen3_asr"


def test_submit_lrc_batch_youtube_generator_without_url_skips():
    recording = _make_recording()
    recording.youtube_url = None
    db = _make_db(recording)
    analysis = _make_analysis_client()
    with patch.object(lrc_jobs, "resolve_lyrics_text", return_value="lyrics"):
        submissions = submit_lrc_batch(
            song_ids=["song_001"],
            db_client=db,
            analysis_client=analysis,
            force=False,
            whisper_model="base",
            language="zh",
            no_vocals=False,
            generator="youtube",
            no_cache=False,
            console=Console(),
        )

    analysis.submit_lrc.assert_not_called()
    assert submissions == []


def test_submit_lrc_batch_no_cache_forwards_bypasses():
    _db, analysis = _run_batch(no_cache=True)

    analysis.submit_lrc.assert_called_once()
    kwargs = analysis.submit_lrc.call_args.kwargs
    assert kwargs["force_whisper"] is True
    assert kwargs["force_qwen3_asr"] is True


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
            generator="auto",
            no_cache=False,
            console=console,
        )

    analysis.submit_lrc.assert_not_called()
    db.update_recording_status.assert_not_called()


def _run_batch_with_trace(dump_llm_trace):
    recording = _make_recording()
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
            generator="auto",
            no_cache=False,
            console=console,
            dump_llm_trace=dump_llm_trace,
        )
    return analysis


def test_submit_lrc_batch_forwards_song_id_and_dump_llm_trace():
    analysis = _run_batch_with_trace(dump_llm_trace=True)

    analysis.submit_lrc.assert_called_once()
    kwargs = analysis.submit_lrc.call_args.kwargs
    assert kwargs["song_id"] == "song_001"
    assert kwargs["dump_llm_trace"] is True


def test_submit_lrc_batch_default_clears_dump_llm_trace():
    analysis = _run_batch_with_trace(dump_llm_trace=False)

    analysis.submit_lrc.assert_called_once()
    kwargs = analysis.submit_lrc.call_args.kwargs
    assert kwargs["dump_llm_trace"] is False


def _run_single_with_trace(dump_llm_trace):
    recording = _make_recording()
    db = _make_db(recording)
    analysis = _make_analysis_client()
    console = Console()
    with patch.object(lrc_jobs, "resolve_lyrics_text", return_value="lyrics"):
        submit_lrc_single(
            song_id="song_001",
            db_client=db,
            analysis_client=analysis,
            force=False,
            whisper_model="base",
            language="zh",
            no_vocals=False,
            generator="auto",
            no_cache=False,
            wait=False,
            console=console,
            dump_llm_trace=dump_llm_trace,
        )
    return analysis


def test_submit_lrc_single_forwards_song_id_and_dump_llm_trace():
    analysis = _run_single_with_trace(dump_llm_trace=True)

    analysis.submit_lrc.assert_called_once()
    kwargs = analysis.submit_lrc.call_args.kwargs
    assert kwargs["song_id"] == "song_001"
    assert kwargs["dump_llm_trace"] is True


def _run_batch_with_llm_model(llm_model):
    recording = _make_recording()
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
            generator="auto",
            no_cache=False,
            console=console,
            llm_model=llm_model,
        )
    return analysis


def test_submit_lrc_batch_forwards_llm_model():
    analysis = _run_batch_with_llm_model("openai/gpt-4o-mini")

    analysis.submit_lrc.assert_called_once()
    assert analysis.submit_lrc.call_args.kwargs["llm_model"] == "openai/gpt-4o-mini"


def test_submit_lrc_batch_default_llm_model_empty():
    analysis = _run_batch_with_llm_model("")

    analysis.submit_lrc.assert_called_once()
    assert analysis.submit_lrc.call_args.kwargs["llm_model"] == ""


def _run_single_with_llm_model(llm_model):
    recording = _make_recording()
    db = _make_db(recording)
    analysis = _make_analysis_client()
    console = Console()
    with patch.object(lrc_jobs, "resolve_lyrics_text", return_value="lyrics"):
        submit_lrc_single(
            song_id="song_001",
            db_client=db,
            analysis_client=analysis,
            force=False,
            whisper_model="base",
            language="zh",
            no_vocals=False,
            generator="auto",
            no_cache=False,
            wait=False,
            console=console,
            llm_model=llm_model,
        )
    return analysis


def test_submit_lrc_single_forwards_llm_model():
    analysis = _run_single_with_llm_model("openai/gpt-4o-mini")

    analysis.submit_lrc.assert_called_once()
    assert analysis.submit_lrc.call_args.kwargs["llm_model"] == "openai/gpt-4o-mini"


def test_submit_lrc_single_default_llm_model_empty():
    analysis = _run_single_with_llm_model("")

    analysis.submit_lrc.assert_called_once()
    assert analysis.submit_lrc.call_args.kwargs["llm_model"] == ""


def test_submit_lrc_single_prints_trace_url():
    """Wait mode prints the LLM trace report URL returned on the job result."""
    from io import StringIO

    from stream_of_worship.admin.services.analysis import AnalysisResult, JobInfo

    recording = _make_recording()
    db = _make_db(recording)
    analysis = _make_analysis_client()
    analysis.wait_for_completion.return_value = JobInfo(
        job_id="job_1",
        status="completed",
        job_type="lrc",
        progress=1.0,
        result=AnalysisResult(
            lrc_url="s3://bucket/l.lrc",
            trace_url="http://h/debug-dumps/x.html",
        ),
    )
    buffer = StringIO()
    console = Console(file=buffer, width=200)
    with patch.object(lrc_jobs, "resolve_lyrics_text", return_value="lyrics"):
        submit_lrc_single(
            song_id="song_001",
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
            wait=True,
            console=console,
            dump_llm_trace=True,
        )

    assert "LLM trace report: http://h/debug-dumps/x.html" in buffer.getvalue()
