"""Tests for the unified poll loop + parallel downloads (v2).

Covers the new helpers extracted from the phase-barrier design:
``_advance_song``, ``_poll_one_cycle``, ``_download_worker``,
``adaptive_interval``, ``_handle_lrc_completion``,
``_handle_analysis_completion``, ``_handle_embedding_completion``,
``_submit_analysis_for_song``, ``_submit_embedding_for_song``, and the
unified resume path.
"""

import time
from unittest.mock import MagicMock, patch

import pytest
from rich.console import Console

from stream_of_worship.admin.commands import audio
from stream_of_worship.admin.commands.audio import (
    _advance_song,
    adaptive_interval,
    _handle_analysis_completion,
    _handle_embedding_completion,
    _handle_lrc_completion,
    _poll_one_cycle,
    _submit_analysis_for_song,
    _submit_embedding_for_song,
    _submit_step,
)
from stream_of_worship.admin.db.models import Recording, Song
from stream_of_worship.admin.services.analysis import (
    AnalysisResult,
    AnalysisServiceError,
    EmbeddingResult,
    JobInfo,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _make_song(song_id: str = "s1", title: str = "Test Song") -> Song:
    return Song(
        id=song_id,
        title=title,
        source_url="http://example.com",
        scraped_at="2024-01-01T00:00:00",
        composer="Composer",
        lyrics_raw="line one\nline two",
        lyrics_lines='["line one","line two"]',
    )


def _make_recording(song_id: str = "s1", hash_prefix: str = "abc123def456") -> Recording:
    return Recording(
        content_hash="h" * 64,
        hash_prefix=hash_prefix,
        original_filename="test.mp3",
        file_size_bytes=100,
        imported_at="2024-01-01T00:00:00",
        song_id=song_id,
        r2_audio_url="https://r2/audio.mp3",
        youtube_url="https://youtu.be/abc",
        download_status="completed",
        lrc_status="pending",
        analysis_status="pending",
    )


def _completed_lrc_job(job_id: str = "lrc-job-1") -> JobInfo:
    return JobInfo(
        job_id=job_id,
        status="completed",
        job_type="lrc",
        progress=1.0,
        result=AnalysisResult(lrc_url="https://r2/lrc.lrc", lrc_source="whisper_asr"),
    )


def _completed_analysis_job(job_id: str = "ana-job-1", job_type: str = "fast_analyze") -> JobInfo:
    return JobInfo(
        job_id=job_id,
        status="completed",
        job_type=job_type,
        progress=1.0,
        result=AnalysisResult(
            duration_seconds=180.0,
            tempo_bpm=120.0,
            musical_key="C",
            musical_mode="major",
            key_confidence=0.9,
            loudness_db=-10.0,
        ),
    )


def _completed_embedding_job(job_id: str = "emb-job-1") -> JobInfo:
    return JobInfo(
        job_id=job_id,
        status="completed",
        job_type="embedding",
        progress=1.0,
        result=EmbeddingResult(
            song_id="s1",
            embedding=[0.1] * 384,
            model_version="v1",
            content_hash="abc",
            line_embeddings=[],
        ),
    )


def _noop_manifest_entry(*args, **kwargs):
    pass


# ---------------------------------------------------------------------------
# adaptive_interval
# ---------------------------------------------------------------------------

class TestAdaptiveInterval:
    def test_no_active_jobs_returns_fast(self):
        assert adaptive_interval(time.time(), {}) == 5.0

    def test_recent_completion_returns_fast(self):
        assert adaptive_interval(time.time(), {("s1", "lrc"): "job-1"}) == 5.0

    def test_stale_returns_slow(self):
        old_time = time.time() - 200.0  # > 180s threshold
        assert adaptive_interval(old_time, {("s1", "lrc"): "job-1"}) == 30.0


# ---------------------------------------------------------------------------
# _advance_song
# ---------------------------------------------------------------------------

class TestAdvanceSong:
    """Integration tests for the cascade dispatcher."""

    def test_lrc_completed_advances_to_analysis(self):
        song_id = "s1"
        db_client = MagicMock()
        db_client.get_recording_by_song_id.return_value = _make_recording(song_id)
        db_client.get_song.return_value = _make_song(song_id)

        analysis_client = MagicMock()
        analysis_client.submit_fast_analysis.return_value = JobInfo(
            job_id="ana-job-1", status="queued", job_type="fast_analyze"
        )
        r2_client = MagicMock()
        r2_client.lrc_exists.return_value = None

        results = {song_id: {"lrc": "completed"}}
        active_jobs = {}
        lrc_attempted = {song_id}

        _advance_song(
            song_id, "lrc", ["lrc", "analyze"],
            db_client, analysis_client, r2_client,
            force=False, analysis_tier="fast", stale_after_minutes=120,
            console=Console(quiet=True), results=results,
            active_jobs=active_jobs, lrc_attempted=lrc_attempted,
            _add_manifest_entry=_noop_manifest_entry,
        )

        assert (song_id, "analyze") in active_jobs
        assert active_jobs[(song_id, "analyze")] == "ana-job-1"

    def test_lrc_completed_but_analysis_already_completed_advances_to_embedding(self):
        song_id = "s1"
        db_client = MagicMock()
        db_client.get_recording_by_song_id.return_value = _make_recording(song_id)
        db_client.get_song.return_value = _make_song(song_id)
        db_client.get_embedding_content_hash.return_value = None

        analysis_client = MagicMock()
        analysis_client.submit_embedding.return_value = JobInfo(
            job_id="emb-job-1", status="queued", job_type="embedding"
        )
        r2_client = MagicMock()

        results = {song_id: {"lrc": "completed", "analyze": "completed"}}
        active_jobs = {}
        lrc_attempted = {song_id}

        _advance_song(
            song_id, "lrc", ["lrc", "analyze", "embedding"],
            db_client, analysis_client, r2_client,
            force=False, analysis_tier="fast", stale_after_minutes=120,
            console=Console(quiet=True), results=results,
            active_jobs=active_jobs, lrc_attempted=lrc_attempted,
            _add_manifest_entry=_noop_manifest_entry,
        )

        assert (song_id, "embedding") in active_jobs

    def test_lrc_completed_but_analysis_not_selected_noop(self):
        song_id = "s1"
        results = {song_id: {"lrc": "completed"}}
        active_jobs = {}
        lrc_attempted = {song_id}

        _advance_song(
            song_id, "lrc", ["lrc"],
            MagicMock(), MagicMock(), MagicMock(),
            force=False, analysis_tier="fast", stale_after_minutes=120,
            console=Console(quiet=True), results=results,
            active_jobs=active_jobs, lrc_attempted=lrc_attempted,
            _add_manifest_entry=_noop_manifest_entry,
        )

        assert len(active_jobs) == 0
        assert results[song_id]["_pipeline"] == "completed"

    def test_all_steps_skipped_chain_exhausted(self):
        song_id = "s1"
        results = {song_id: {}}
        active_jobs = {}
        lrc_attempted = set()

        _advance_song(
            song_id, "download", [],
            MagicMock(), MagicMock(), MagicMock(),
            force=False, analysis_tier="fast", stale_after_minutes=120,
            console=Console(quiet=True), results=results,
            active_jobs=active_jobs, lrc_attempted=lrc_attempted,
            _add_manifest_entry=_noop_manifest_entry,
        )

        assert results[song_id]["_pipeline"] == "completed"

    def test_already_active_step_not_resubmitted(self):
        """If (song_id, step) is already in active_jobs, _advance_song returns."""
        song_id = "s1"
        results = {song_id: {}}
        active_jobs = {(song_id, "lrc"): "existing-job"}
        lrc_attempted = set()

        _advance_song(
            song_id, "download", ["download", "lrc"],
            MagicMock(), MagicMock(), MagicMock(),
            force=False, analysis_tier="fast", stale_after_minutes=120,
            console=Console(quiet=True), results=results,
            active_jobs=active_jobs, lrc_attempted=lrc_attempted,
            _add_manifest_entry=_noop_manifest_entry,
        )

        # Should not have submitted a new job
        assert active_jobs[(song_id, "lrc")] == "existing-job"


# ---------------------------------------------------------------------------
# _handle_lrc_completion
# ---------------------------------------------------------------------------

class TestHandleLrcCompletion:
    def test_completed_marks_lrc_done(self):
        song_id = "s1"
        db_client = MagicMock()
        db_client.get_recording_by_song_id.return_value = _make_recording(song_id)
        db_client.get_song.return_value = _make_song(song_id)
        r2_client = MagicMock()

        results = {song_id: {}}

        with patch.object(audio, "_confirm_r2_lrc", return_value="https://r2/lrc.lrc"):
            is_terminal, new_job = _handle_lrc_completion(
                song_id, "lrc-job-1", _completed_lrc_job(),
                db_client, MagicMock(), r2_client,
                force=False, stale_after_minutes=120,
                console=Console(quiet=True), results=results,
                _add_manifest_entry=_noop_manifest_entry,
                resubmit_counts={},
            )

        assert is_terminal is True
        assert new_job is None
        assert results[song_id]["lrc"] == "completed"

    def test_failed_marks_lrc_failed(self):
        song_id = "s1"
        db_client = MagicMock()
        db_client.get_recording_by_song_id.return_value = _make_recording(song_id)
        db_client.get_song.return_value = _make_song(song_id)

        results = {song_id: {}}
        failed_job = JobInfo(job_id="lrc-job-1", status="failed", job_type="lrc",
                             error_message="ASR error")

        is_terminal, _ = _handle_lrc_completion(
            song_id, "lrc-job-1", failed_job,
            db_client, MagicMock(), MagicMock(),
            force=False, stale_after_minutes=120,
            console=Console(quiet=True), results=results,
            _add_manifest_entry=_noop_manifest_entry,
            resubmit_counts={},
        )

        assert is_terminal is True
        assert results[song_id]["lrc"] == "failed"

    def test_processing_returns_not_terminal(self):
        song_id = "s1"
        processing_job = JobInfo(job_id="lrc-job-1", status="processing", job_type="lrc")
        results = {song_id: {}}

        is_terminal, new_job = _handle_lrc_completion(
            song_id, "lrc-job-1", processing_job,
            MagicMock(), MagicMock(), MagicMock(),
            force=False, stale_after_minutes=120,
            console=Console(quiet=True), results=results,
            _add_manifest_entry=_noop_manifest_entry,
            resubmit_counts={},
        )

        assert is_terminal is False
        assert new_job is None


# ---------------------------------------------------------------------------
# _handle_analysis_completion
# ---------------------------------------------------------------------------

class TestHandleAnalysisCompletion:
    def test_completed_marks_analysis_done(self):
        song_id = "s1"
        db_client = MagicMock()
        db_client.get_recording_by_song_id.return_value = _make_recording(song_id)
        db_client.get_song.return_value = _make_song(song_id)

        results = {song_id: {}}

        is_terminal, _ = _handle_analysis_completion(
            song_id, "ana-job-1", _completed_analysis_job(),
            db_client, MagicMock(), "fast",
            Console(quiet=True), results,
            _add_manifest_entry=_noop_manifest_entry,
        )

        assert is_terminal is True
        assert results[song_id]["analyze"] == "completed"
        assert results[song_id]["analysis_tier"] == "fast"

    def test_failed_marks_analysis_failed(self):
        song_id = "s1"
        db_client = MagicMock()
        db_client.get_recording_by_song_id.return_value = _make_recording(song_id)

        results = {song_id: {}}
        failed_job = JobInfo(job_id="ana-job-1", status="failed", job_type="fast_analyze",
                            error_message="model error")

        is_terminal, _ = _handle_analysis_completion(
            song_id, "ana-job-1", failed_job,
            db_client, MagicMock(), "fast",
            Console(quiet=True), results,
            _add_manifest_entry=_noop_manifest_entry,
        )

        assert is_terminal is True
        assert results[song_id]["analyze"] == "failed"


# ---------------------------------------------------------------------------
# _handle_embedding_completion
# ---------------------------------------------------------------------------

class TestHandleEmbeddingCompletion:
    def test_completed_marks_embedding_done(self):
        song_id = "s1"
        db_client = MagicMock()
        db_client.get_recording_by_song_id.return_value = _make_recording(song_id)
        db_client.get_song.return_value = _make_song(song_id)

        results = {song_id: {}}

        with patch.object(audio, "_write_embedding_result", return_value=True):
            is_terminal, _ = _handle_embedding_completion(
                song_id, "emb-job-1", _completed_embedding_job(),
                db_client, MagicMock(),
                Console(quiet=True), results,
                _add_manifest_entry=_noop_manifest_entry,
            )

        assert is_terminal is True
        assert results[song_id]["embedding"] == "completed"

    def test_failed_marks_embedding_failed(self):
        song_id = "s1"
        results = {song_id: {}}
        failed_job = JobInfo(job_id="emb-job-1", status="failed", job_type="embedding",
                             error_message="OOM")

        is_terminal, _ = _handle_embedding_completion(
            song_id, "emb-job-1", failed_job,
            MagicMock(), MagicMock(),
            Console(quiet=True), results,
            _add_manifest_entry=_noop_manifest_entry,
        )

        assert is_terminal is True
        assert results[song_id]["embedding"] == "failed"


# ---------------------------------------------------------------------------
# _submit_analysis_for_song
# ---------------------------------------------------------------------------

class TestSubmitAnalysisForSong:
    def test_submit_success(self):
        song_id = "s1"
        db_client = MagicMock()
        db_client.get_recording_by_song_id.return_value = _make_recording(song_id)
        analysis_client = MagicMock()
        analysis_client.submit_fast_analysis.return_value = JobInfo(
            job_id="ana-1", status="queued", job_type="fast_analyze"
        )

        results = {song_id: {}}
        job_id, status = _submit_analysis_for_song(
            song_id, db_client, analysis_client, MagicMock(),
            force=False, analysis_tier="fast", stale_after_minutes=120,
            console=Console(quiet=True), results=results,
            _add_manifest_entry=_noop_manifest_entry,
        )

        assert status == "submitted"
        assert job_id == "ana-1"

    def test_skip_completed(self):
        song_id = "s1"
        rec = _make_recording(song_id)
        rec.analysis_status = "completed"
        db_client = MagicMock()
        db_client.get_recording_by_song_id.return_value = rec

        results = {song_id: {}}
        job_id, status = _submit_analysis_for_song(
            song_id, db_client, MagicMock(), MagicMock(),
            force=False, analysis_tier="fast", stale_after_minutes=120,
            console=Console(quiet=True), results=results,
            _add_manifest_entry=_noop_manifest_entry,
        )

        assert status == "skipped_completed"
        assert job_id is None
        assert results[song_id]["analyze"] == "completed"

    def test_no_recording_marks_failed(self):
        song_id = "s1"
        db_client = MagicMock()
        db_client.get_recording_by_song_id.return_value = None

        results = {song_id: {}}
        job_id, status = _submit_analysis_for_song(
            song_id, db_client, MagicMock(), MagicMock(),
            force=False, analysis_tier="fast", stale_after_minutes=120,
            console=Console(quiet=True), results=results,
            _add_manifest_entry=_noop_manifest_entry,
        )

        assert status == "skipped_no_recording"
        assert results[song_id]["analyze"] == "failed"


# ---------------------------------------------------------------------------
# _submit_embedding_for_song
# ---------------------------------------------------------------------------

class TestSubmitEmbeddingForSong:
    def test_submit_success(self):
        song_id = "s1"
        db_client = MagicMock()
        db_client.get_song.return_value = _make_song(song_id)
        db_client.get_recording_by_song_id.return_value = _make_recording(song_id)
        db_client.get_embedding_content_hash.return_value = None
        analysis_client = MagicMock()
        analysis_client.submit_embedding.return_value = JobInfo(
            job_id="emb-1", status="queued", job_type="embedding"
        )

        results = {song_id: {}}
        job_id, status = _submit_embedding_for_song(
            song_id, db_client, analysis_client, MagicMock(),
            force=False, analysis_tier="fast", stale_after_minutes=120,
            console=Console(quiet=True), results=results,
            _add_manifest_entry=_noop_manifest_entry,
        )

        assert status == "submitted"
        assert job_id == "emb-1"

    def test_skip_up_to_date(self):
        song_id = "s1"
        song = _make_song(song_id)
        db_client = MagicMock()
        db_client.get_song.return_value = song
        db_client.get_recording_by_song_id.return_value = _make_recording(song_id)
        # Make content hash match so it's "up to date"
        db_client.get_embedding_content_hash.return_value = "match"
        with patch.object(audio, "_compute_content_hash", return_value="match"):
            results = {song_id: {}}
            job_id, status = _submit_embedding_for_song(
                song_id, db_client, MagicMock(), MagicMock(),
                force=False, analysis_tier="fast", stale_after_minutes=120,
                console=Console(quiet=True), results=results,
                _add_manifest_entry=_noop_manifest_entry,
            )

        assert status == "skipped_up_to_date"
        assert results[song_id]["embedding"] == "completed"

    def test_no_lyrics_marks_failed(self):
        song_id = "s1"
        song = _make_song(song_id)
        song.lyrics_raw = None
        db_client = MagicMock()
        db_client.get_song.return_value = song

        results = {song_id: {}}
        job_id, status = _submit_embedding_for_song(
            song_id, db_client, MagicMock(), MagicMock(),
            force=False, analysis_tier="fast", stale_after_minutes=120,
            console=Console(quiet=True), results=results,
            _add_manifest_entry=_noop_manifest_entry,
        )

        assert status == "skipped_no_lyrics"
        assert results[song_id]["embedding"] == "failed"


# ---------------------------------------------------------------------------
# _poll_one_cycle
# ---------------------------------------------------------------------------

class TestPollOneCycle:
    def test_lrc_completion_advances_to_analysis(self):
        """When an LRC job completes, _poll_one_cycle advances to analysis."""
        song_id = "s1"
        db_client = MagicMock()
        db_client.get_recording_by_song_id.return_value = _make_recording(song_id)
        db_client.get_song.return_value = _make_song(song_id)

        analysis_client = MagicMock()
        analysis_client.get_job.return_value = _completed_lrc_job()
        # When advance submits analysis:
        analysis_client.submit_fast_analysis.return_value = JobInfo(
            job_id="ana-1", status="queued", job_type="fast_analyze"
        )
        r2_client = MagicMock()

        results = {song_id: {}}
        active_jobs = {(song_id, "lrc"): "lrc-job-1"}
        lrc_attempted = {song_id}
        resubmit_counts = {}

        with patch.object(audio, "_confirm_r2_lrc", return_value="https://r2/lrc.lrc"):
            _poll_one_cycle(
                pending_futures=set(),
                active_jobs=active_jobs,
                results=results,
                db_client=db_client,
                analysis_client=analysis_client,
                r2_client=r2_client,
                selected_steps=["lrc", "analyze"],
                force=False,
                analysis_tier="fast",
                stale_after_minutes=120,
                console=Console(quiet=True),
                _add_manifest_entry=_noop_manifest_entry,
                results_lock=__import__("threading").Lock(),
                lrc_attempted=lrc_attempted,
                resubmit_counts=resubmit_counts,
                last_completion_time=time.time(),
                batch_start_time=time.time(),
            )

        # LRC job should be removed, analysis job should be added
        assert (song_id, "lrc") not in active_jobs
        assert (song_id, "analyze") in active_jobs
        assert results[song_id]["lrc"] == "completed"

    def test_no_phase_barrier_embedding_before_all_analysis(self):
        """Song A can have embedding submitted while Song B's analysis is still running."""
        song_a, song_b = "sA", "sB"
        db_client = MagicMock()
        db_client.get_recording_by_song_id.side_effect = lambda sid: _make_recording(sid)
        db_client.get_song.side_effect = lambda sid: _make_song(sid)
        db_client.get_embedding_content_hash.return_value = None

        analysis_client = MagicMock()
        # Song A's analysis is completed, Song B's is still processing
        def _get_job(job_id):
            if job_id == "ana-a":
                return _completed_analysis_job(job_id="ana-a")
            return JobInfo(job_id=job_id, status="processing", job_type="fast_analyze")

        analysis_client.get_job.side_effect = _get_job
        analysis_client.submit_embedding.return_value = JobInfo(
            job_id="emb-a", status="queued", job_type="embedding"
        )
        r2_client = MagicMock()

        results = {song_a: {}, song_b: {}}
        active_jobs = {
            (song_a, "analyze"): "ana-a",
            (song_b, "analyze"): "ana-b",
        }
        lrc_attempted = {song_a, song_b}
        resubmit_counts = {}

        _poll_one_cycle(
            pending_futures=set(),
            active_jobs=active_jobs,
            results=results,
            db_client=db_client,
            analysis_client=analysis_client,
            r2_client=r2_client,
            selected_steps=["analyze", "embedding"],
            force=False,
            analysis_tier="fast",
            stale_after_minutes=120,
            console=Console(quiet=True),
            _add_manifest_entry=_noop_manifest_entry,
            results_lock=__import__("threading").Lock(),
            lrc_attempted=lrc_attempted,
            resubmit_counts=resubmit_counts,
            last_completion_time=time.time(),
            batch_start_time=time.time(),
        )

        # Song A: analysis completed → embedding submitted
        assert (song_a, "analyze") not in active_jobs
        assert (song_a, "embedding") in active_jobs
        # Song B: still processing
        assert (song_b, "analyze") in active_jobs
        assert (song_b, "embedding") not in active_jobs


# ---------------------------------------------------------------------------
# Unified resume
# ---------------------------------------------------------------------------

class TestUnifiedResume:
    def test_resume_reconstructs_active_jobs_and_enters_loop(self):
        """_resume_from_manifest reconstructs active_jobs and polls concurrently."""
        from pathlib import Path

        manifest_data = {
            "batch_id": "test-batch",
            "started_at": "2024-01-01T00:00:00Z",
            "selected_steps": ["lrc"],
            "analysis_tier": "fast",
            "stale_after_minutes": 120,
            "songs": [
                {
                    "song_id": "s1",
                    "hash_prefix": "abc123",
                    "step": "lrc",
                    "tier": "lrc",
                    "job_id": "lrc-1",
                    "status": "processing",
                },
                {
                    "song_id": "s2",
                    "hash_prefix": "def456",
                    "step": "lrc",
                    "tier": "lrc",
                    "job_id": "lrc-2",
                    "status": "processing",
                },
                {
                    "song_id": "s3",
                    "hash_prefix": "ghi789",
                    "step": "lrc",
                    "tier": "lrc",
                    "job_id": "lrc-3",
                    "status": "completed",
                },
            ],
        }

        manifest_path = Path("/tmp/test_manifest.json")

        db_client = MagicMock()
        db_client.get_recording_by_song_id.return_value = _make_recording("s1")
        db_client.get_song.return_value = _make_song("s1")

        analysis_client = MagicMock()
        # All jobs return completed so the loop terminates
        analysis_client.get_job.return_value = _completed_lrc_job()
        r2_client = MagicMock()

        with (
            patch.object(audio, "_confirm_r2_lrc", return_value="https://r2/lrc.lrc"),
            patch.object(audio, "_apply_manifest_writeback"),
            patch("pathlib.Path.write_text"),
        ):
            results = audio._resume_from_manifest(
                manifest_data=manifest_data,
                manifest_path=manifest_path,
                db_client=db_client,
                r2_client=r2_client,
                analysis_client=analysis_client,
                stale_after_minutes=120,
                console=Console(quiet=True),
                database_url="postgresql://test",
                download_concurrency=1,
            )

        # Both processing jobs should have been polled
        assert analysis_client.get_job.call_count >= 2
        # Results should have entries for s1 and s2
        assert "s1" in results
        assert "s2" in results
