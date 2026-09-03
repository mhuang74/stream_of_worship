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
            config=MagicMock(),
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
            config=MagicMock(),
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
            config=MagicMock(),
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
            config=MagicMock(),
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
            config=MagicMock(),
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
                config=MagicMock(),
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
            config=MagicMock(),
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
                config=MagicMock(),
            )

        # Both processing jobs should have been polled
        assert analysis_client.get_job.call_count >= 2
        # Results should have entries for s1 and s2
        assert "s1" in results
        assert "s2" in results


# ---------------------------------------------------------------------------
# v3: components step in the unified loop
# ---------------------------------------------------------------------------

from types import SimpleNamespace

from stream_of_worship.admin.commands.audio import (
    _db_components_have_llm_fields,
    _handle_components_completion,
    _submit_components_for_song,
)
from stream_of_worship.admin.db.models import SongComponent


def _comp(
    component_type: str = "chorus",
    occurrence_index: int = 1,
    role: str = "entry",
    theme: str | None = "讚美",
    posture: str | None = "站立",
) -> SongComponent:
    return SongComponent(
        song_id="s1",
        content_hash="h" * 64,
        component_type=component_type,
        occurrence_index=occurrence_index,
        role=role,
        theme=theme,
        vocal_posture=posture,
    )


class TestDbComponentsHaveLlmFields:
    def test_essential_candidates_complete_true(self):
        comps = [_comp(role="entry"), _comp(role="exit")]
        assert _db_components_have_llm_fields(comps) is True

    def test_missing_one_field_false(self):
        comps = [_comp(role="entry"), _comp(role="exit", theme=None)]
        assert _db_components_have_llm_fields(comps) is False

    def test_no_candidates_false(self):
        comps = [_comp(component_type="verse", role="none")]
        assert _db_components_have_llm_fields(comps) is False

    def test_first_bridge_counts_as_candidate(self):
        comps = [_comp(component_type="bridge", occurrence_index=1, role="none")]
        assert _db_components_have_llm_fields(comps) is True
        comps2 = [_comp(component_type="bridge", occurrence_index=2, role="none")]
        assert _db_components_have_llm_fields(comps2) is False


class TestSubmitComponentsForSong:
    def _run(
        self,
        recording=None,
        db_comps=None,
        force=False,
        newly_backfilled=False,
        config=None,
    ):
        db_client = MagicMock()
        db_client.get_recording_by_song_id.return_value = recording
        db_client.get_song_components.return_value = db_comps or []
        analysis_client = MagicMock()
        analysis_client.submit_component_analysis.return_value = JobInfo(
            job_id="comp-1", status="processing", job_type="component_analysis"
        )
        console = Console(quiet=True)
        results: dict = {"s1": {}}
        manifest: list = []
        job_id, status = _submit_components_for_song(
            "s1",
            db_client,
            analysis_client,
            config or MagicMock(analysis_url="https://analysis"),
            force,
            console,
            results,
            lambda *a, **k: manifest.append(a),
            newly_backfilled=newly_backfilled,
        )
        return job_id, status, results["s1"], manifest, db_client, analysis_client

    def test_no_recording_skipped(self):
        _, status, results, _, _, _ = self._run(recording=None)
        assert status == "skipped_no_recording"
        assert results["components"] == "skipped_no_recording"

    def test_recording_without_audio_url_skipped(self):
        rec = _make_recording("s1")
        rec.r2_audio_url = None
        _, status, results, _, _, _ = self._run(recording=rec)
        assert status == "skipped_no_recording"

    def test_no_sections_or_lrc_skipped(self):
        rec = _make_recording("s1")
        _, status, results, _, _, _ = self._run(recording=rec)
        assert status == "skipped_no_sections"

    def test_db_comps_present_skips_completed(self):
        rec = _rec_with_sections()
        comps = [_comp(role="entry"), _comp(role="exit")]
        _, status, results, _, _, analysis = self._run(recording=rec, db_comps=comps)
        assert status == "skipped_completed"
        analysis.submit_component_analysis.assert_not_called()
        assert results["components"] == "completed"
        assert results["components_source"] == "db_existing"

    def test_db_comps_present_but_theme_stale_re_aggregates(self):
        rec = _rec_with_sections()
        rec.theme = None
        rec.vocal_posture = None
        comps = [_comp(role="entry"), _comp(role="exit")]
        with patch.object(audio, "_persist_recording_theme") as m_persist:
            _, status, _, _, _, _ = self._run(recording=rec, db_comps=comps)
        assert status == "skipped_completed"
        m_persist.assert_called_once()

    def test_newly_backfilled_bypasses_db_skip(self):
        rec = _rec_with_sections()
        comps = [_comp(role="entry"), _comp(role="exit")]
        with patch.object(
            audio, "_prepare_component_job_inputs"
        ) as m_prep:
            m_prep.return_value = {
                "sections": [], "beats": None, "downbeats": None,
                "lrc_content": None, "structured_lyrics": None,
                "cached_result": None,
            }
            _, status, _, _, _, analysis = self._run(
                recording=rec, db_comps=comps, newly_backfilled=True
            )
        assert status == "submitted"
        analysis.submit_component_analysis.assert_called_once()
        assert analysis.submit_component_analysis.call_args.kwargs["force"] is True

    def test_force_bypasses_db_skip(self):
        rec = _rec_with_sections()
        comps = [_comp(role="entry"), _comp(role="exit")]
        with patch.object(audio, "_prepare_component_job_inputs") as m_prep:
            m_prep.return_value = {
                "sections": [], "beats": None, "downbeats": None,
                "lrc_content": None, "structured_lyrics": None,
                "cached_result": None,
            }
            _, status, _, _, _, analysis = self._run(
                recording=rec, db_comps=comps, force=True
            )
        assert status == "submitted"
        analysis.submit_component_analysis.assert_called_once()

    def test_happy_path_submits_job(self):
        rec = _rec_with_sections()
        _, status, results, manifest, _, analysis = self._run(recording=rec)
        assert status == "submitted"
        assert analysis.submit_component_analysis.assert_called_once() or True
        assert manifest[-1][2] == "components" and manifest[-1][5] == "submitted"



class TestHandleComponentsCompletion:
    def test_completed_persists_components(self):
        db = MagicMock()
        db.get_recording_by_song_id.return_value = _make_recording("s1")
        db.get_song.return_value = _make_song("s1")
        job = JobInfo(
            job_id="c1",
            status="completed",
            job_type="component_analysis",
            result=SimpleNamespace(
                components=[
                    {"component_type": "chorus", "occurrence_index": 1, "role": "entry"}
                ]
            ),
        )
        results: dict = {"s1": {}}
        is_terminal, new_job_id = _handle_components_completion(
            "s1", "c1", job, db, Console(quiet=True), results, lambda *a, **k: None
        )
        assert is_terminal is True
        assert new_job_id is None
        db.upsert_song_components.assert_called_once()
        assert results["s1"]["components"] == "completed"
        assert results["s1"]["components_count"] == 1

    def test_failed_job_marks_failed(self):
        db = MagicMock()
        job = JobInfo(
            job_id="c1", status="failed", job_type="component_analysis",
            error_message="boom",
        )
        results: dict = {"s1": {}}
        is_terminal, _ = _handle_components_completion(
            "s1", "c1", job, db, Console(quiet=True), results, lambda *a, **k: None
        )
        assert is_terminal is True
        assert results["s1"]["components"] == "failed"

    def test_empty_components_completed_with_zero(self):
        db = MagicMock()
        db.get_recording_by_song_id.return_value = _make_recording("s1")
        job = JobInfo(
            job_id="c1",
            status="completed",
            job_type="component_analysis",
            result=SimpleNamespace(components=[]),
        )
        results: dict = {"s1": {}}
        is_terminal, _ = _handle_components_completion(
            "s1", "c1", job, db, Console(quiet=True), results, lambda *a, **k: None
        )
        assert is_terminal is True
        assert results["s1"]["components"] == "completed"
        assert results["s1"]["components_count"] == 0


def _rec_with_sections(song_id: str = "s1") -> Recording:
    """Recording with full analysis + LRC complete so components are eligible."""
    return Recording(
        content_hash="h" * 64,
        hash_prefix="abc123def456",
        original_filename="test.mp3",
        file_size_bytes=100,
        imported_at="2024-01-01T00:00:00",
        song_id=song_id,
        r2_audio_url="https://r2/audio.mp3",
        youtube_url="https://youtu.be/abc",
        download_status="completed",
        lrc_status="completed",
        analysis_status="completed",
        sections="[]",
    )


class TestAdvanceSongComponents:
    def test_lrc_completed_advances_to_components(self):
        song_id = "s1"
        db_client = MagicMock()
        rec = _rec_with_sections(song_id)
        db_client.get_recording_by_song_id.return_value = rec
        db_client.get_song.return_value = _make_song(song_id)
        db_client.get_song_components.return_value = []
        db_client.get_embedding_content_hash.return_value = None

        analysis_client = MagicMock()
        analysis_client.submit_component_analysis.return_value = JobInfo(
            job_id="comp-1", status="processing", job_type="component_analysis"
        )
        results = {song_id: {"lrc": "completed", "analyze": "completed"}}
        active_jobs: dict = {}

        _advance_song(
            song_id,
            "lrc",
            ["lrc", "components"],
            db_client,
            analysis_client,
            MagicMock(),
            force=False,
            analysis_tier="fast",
            stale_after_minutes=120,
            console=Console(quiet=True),
            results=results,
            active_jobs=active_jobs,
            lrc_attempted={song_id},
            _add_manifest_entry=_noop_manifest_entry,
            config=MagicMock(analysis_url="https://analysis"),
        )

        assert (song_id, "components") in active_jobs
        assert active_jobs[(song_id, "components")] == "comp-1"


class TestReconcileOnInterruptV3:
    def _reconcile(self, active_jobs, db, r2, config=None):
        results: dict = {"s1": {}}
        audio._reconcile_on_interrupt(
            active_jobs,
            results,
            db,
            r2,
            Console(quiet=True),
            config or MagicMock(analysis_url="https://analysis"),
        )
        return results

    def test_components_job_with_cached_result_upserts(self):
        db = MagicMock()
        db.get_recording_by_song_id.return_value = _rec_with_sections()
        r2 = MagicMock()
        with patch.object(
            audio, "_prepare_component_job_inputs"
        ) as m_prep:
            m_prep.return_value = {
                "sections": [], "beats": None, "downbeats": None,
                "lrc_content": None, "structured_lyrics": None,
                "cached_result": {"components": [
                    {"component_type": "chorus", "occurrence_index": 1, "role": "entry"}
                ]},
            }
            results = self._reconcile({("s1", "components"): "c-1"}, db, r2)
        db.upsert_song_components.assert_called_once()
        assert results["s1"]["components"] == "completed"

    def test_analyze_job_leaves_db_untouched(self):
        db = MagicMock()
        db.get_recording_by_song_id.return_value = _make_recording("s1")
        results = self._reconcile({("s1", "analyze"): "a-1"}, db, MagicMock())
        db.update_recording_lrc.assert_not_called()
        db.update_recording_status.assert_not_called()

    def test_same_song_components_and_lrc_reconciled_without_collision(self):
        db = MagicMock()
        db.get_recording_by_song_id.return_value = _rec_with_sections()
        r2 = MagicMock()
        r2.lrc_exists.return_value = None
        with patch.object(
            audio, "_prepare_component_job_inputs", return_value=None
        ):
            results = self._reconcile(
                {("s1", "components"): "c-1", ("s1", "lrc"): "l-1"}, db, r2
            )
        # Both keys processed: components failed, lrc failed (no R2 file)
        assert results["s1"]["components"] == "failed"
        assert results["s1"]["lrc"] == "failed"


class TestDownloadStructuredLyrics:
    def _invoke(self, tmp_path, fetch_side_effect=None, fetch_return=None):
        db = MagicMock()
        db.get_recording_by_hash.return_value = None
        r2 = MagicMock()
        r2.upload_audio.return_value = "https://r2/audio.mp3"
        audio_path = tmp_path / "audio.mp3"
        audio_path.write_bytes(b"x" * 10)
        song = _make_song("s1")
        fetch_kwargs = (
            {"side_effect": fetch_side_effect}
            if fetch_side_effect is not None
            else {"return_value": fetch_return}
        )
        with (
            patch.object(audio, "YouTubeDownloader") as m_dl,
            patch.object(audio, "compute_file_hash", return_value="h" * 64),
            patch.object(audio, "get_hash_prefix", return_value="abc123def456"),
            patch.object(audio, "probe_duration", return_value=100.0),
            patch.object(audio, "_fetch_structured_lyrics", **fetch_kwargs),
        ):
            m_dl.return_value.download_with_info.return_value = (
                audio_path,
                "https://youtu.be/xyz",
                "Video Title",
            )
            recording, error = audio._download_and_create_recording(
                "s1", song, db, r2, Console(quiet=True), use_llm=True
            )
        return recording, error

    def test_download_creates_recording_with_structured_lyrics(self, tmp_path):
        recording, error = self._invoke(
            tmp_path, fetch_return=("raw", '{"sections":[]}', "youtube")
        )
        assert error is None
        assert recording is not None
        assert recording.structured_lyrics == '{"sections":[]}'
        assert recording.structured_lyrics_raw == "raw"

    def test_structured_lyrics_failure_non_fatal(self, tmp_path):
        import typer

        recording, error = self._invoke(tmp_path, fetch_side_effect=typer.Exit(1))
        assert error is None
        assert recording is not None
        assert recording.structured_lyrics is None
