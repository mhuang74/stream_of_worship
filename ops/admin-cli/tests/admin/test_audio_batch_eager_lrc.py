"""Tests for the eager download→LRC handoff in ``_process_batch``.

When both ``--download`` and ``--lrc`` are selected, the LRC job for a song
is submitted as soon as that song's download completes, so the slow LRC step
overlaps with remaining downloads instead of waiting for the whole download
phase to finish. These tests verify the interleaving, the no-double-submit
guard in Phase 2, and that the LRC-only (no-download) path is unchanged.
"""

from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import pytest
from rich.console import Console

from stream_of_worship.admin.commands.audio import _process_batch, _submit_lrc_for_song
from stream_of_worship.admin.db.models import Recording, Song
from stream_of_worship.admin.services.analysis import AnalysisServiceError, JobInfo


def _make_song(song_id: str, title: str = "Test Song") -> Song:
    return Song(
        id=song_id,
        title=title,
        source_url="http://example.com",
        scraped_at="2024-01-01T00:00:00",
        composer="Composer",
        lyrics_raw="line one\nline two",
        lyrics_lines='["line one","line two"]',
    )


def _make_recording(song_id: str, hash_prefix: str = "abc123def456") -> Recording:
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
    )


@pytest.fixture
def stubs(tmp_path):
    """Return a bundle of mocks and patches for exercising _process_batch."""
    db_client = MagicMock()
    analysis_client = MagicMock()
    r2_client = MagicMock()

    # R2 never has a preexisting LRC by default
    r2_client.lrc_exists.return_value = None

    # submit_lrc returns a distinct job id per call
    counter = {"n": 0}

    def _submit_lrc(**kwargs):
        counter["n"] += 1
        return JobInfo(job_id=f"job-{counter['n']}", status="queued", job_type="lrc")

    analysis_client.submit_lrc.side_effect = _submit_lrc

    patches = [
        patch(
            "stream_of_worship.admin.commands.audio._get_manifest_dir",
            return_value=tmp_path,
        ),
        patch(
            "stream_of_worship.admin.commands.audio._write_manifest",
            return_value=tmp_path / "manifest.json",
        ),
        patch("stream_of_worship.admin.commands.audio._poll_all_jobs"),
    ]
    for p in patches:
        p.start()

    yield {
        "db_client": db_client,
        "analysis_client": analysis_client,
        "r2_client": r2_client,
        "counter": counter,
    }

    for p in patches:
        p.stop()


class TestEagerLrcHandoff:
    """Eager submission during the download loop."""

    def test_lrc_submitted_interleaved_with_downloads(self, stubs):
        """submit_lrc is called right after each download, before the next."""
        song_ids = ["s1", "s2", "s3"]
        events: list[str] = []
        created: dict = {}  # song_id -> recording, populated by download

        def _download_and_create_recording(song_id, song, db, r2, console):
            events.append(f"download:{song_id}")
            rec = _make_recording(song_id)
            created[song_id] = rec
            return rec, None

        # First lookup returns None (no recording yet → triggers download);
        # subsequent lookups (by the eager LRC helper) return the created rec.
        stubs["db_client"].get_recording_by_song_id.side_effect = (
            lambda sid: created.get(sid)
        )
        stubs["db_client"].get_song.side_effect = lambda sid: _make_song(sid)

        def _submit_side_effect(**kwargs):
            events.append("submit")
            return JobInfo(job_id="job-x", status="queued", job_type="lrc")

        stubs["analysis_client"].submit_lrc.side_effect = _submit_side_effect

        with patch(
            "stream_of_worship.admin.commands.audio._download_and_create_recording",
            side_effect=_download_and_create_recording,
        ):
            _process_batch(
                db_client=stubs["db_client"],
                r2_client=stubs["r2_client"],
                analysis_client=stubs["analysis_client"],
                song_ids=song_ids,
                selected_steps=["download", "lrc"],
                force=False,
                analysis_tier="fast",
                stale_after_minutes=120,
                console=Console(quiet=True),
            )

        # Interleaving: each submit immediately follows its download
        assert events == [
            "download:s1", "submit",
            "download:s2", "submit",
            "download:s3", "submit",
        ], f"events were not interleaved: {events}"

    def test_phase2_does_not_resubmit_eagerly_submitted(self, stubs):
        """Phase 2 must skip songs already submitted during downloads."""
        song_ids = ["s1", "s2"]
        created: dict = {}

        def _download_and_create_recording(sid, song, db, r2, c):
            rec = _make_recording(sid)
            created[sid] = rec
            return rec, None

        stubs["db_client"].get_recording_by_song_id.side_effect = (
            lambda sid: created.get(sid)
        )
        stubs["db_client"].get_song.side_effect = lambda sid: _make_song(sid)

        with patch(
            "stream_of_worship.admin.commands.audio._download_and_create_recording",
            side_effect=_download_and_create_recording,
        ):
            _process_batch(
                db_client=stubs["db_client"],
                r2_client=stubs["r2_client"],
                analysis_client=stubs["analysis_client"],
                song_ids=song_ids,
                selected_steps=["download", "lrc"],
                force=False,
                analysis_tier="fast",
                stale_after_minutes=120,
                console=Console(quiet=True),
            )

        # Exactly one submit per song (eager), none re-submitted in Phase 2
        assert stubs["analysis_client"].submit_lrc.call_count == len(song_ids)

    def test_skipped_r2_recording_also_eager_submits(self, stubs):
        """A recording already on R2 (skipped_r2) still gets LRC submitted early."""
        song_ids = ["s1"]
        rec = _make_recording("s1")
        stubs["db_client"].get_recording_by_song_id.return_value = rec
        stubs["db_client"].get_song.return_value = _make_song("s1")

        with patch(
            "stream_of_worship.admin.commands.audio._download_if_needed",
            return_value={"download": "skipped_r2", "skip_reason": "audio on R2"},
        ):
            _process_batch(
                db_client=stubs["db_client"],
                r2_client=stubs["r2_client"],
                analysis_client=stubs["analysis_client"],
                song_ids=song_ids,
                selected_steps=["download", "lrc"],
                force=False,
                analysis_tier="fast",
                stale_after_minutes=120,
                console=Console(quiet=True),
            )

        assert stubs["analysis_client"].submit_lrc.call_count == 1

    def test_failed_download_not_lrc_submitted(self, stubs):
        """A song whose download fails must not have LRC submitted."""
        song_ids = ["s1"]
        stubs["db_client"].get_recording_by_song_id.return_value = None
        stubs["db_client"].get_song.return_value = _make_song("s1")

        with patch(
            "stream_of_worship.admin.commands.audio._download_and_create_recording",
            side_effect=lambda sid, song, db, r2, c: (None, "boom"),
        ):
            _process_batch(
                db_client=stubs["db_client"],
                r2_client=stubs["r2_client"],
                analysis_client=stubs["analysis_client"],
                song_ids=song_ids,
                selected_steps=["download", "lrc"],
                force=False,
                analysis_tier="fast",
                stale_after_minutes=120,
                console=Console(quiet=True),
            )

        stubs["analysis_client"].submit_lrc.assert_not_called()


class TestLrcOnlyPath:
    """--lrc without --download must behave as before (no eager calls)."""

    def test_lrc_only_submits_in_phase2_not_eager(self, stubs):
        song_ids = ["s1", "s2"]
        recs = {sid: _make_recording(sid) for sid in song_ids}
        stubs["db_client"].get_recording_by_song_id.side_effect = lambda sid: recs[sid]
        stubs["db_client"].get_song.side_effect = lambda sid: _make_song(sid)

        _process_batch(
            db_client=stubs["db_client"],
            r2_client=stubs["r2_client"],
            analysis_client=stubs["analysis_client"],
            song_ids=song_ids,
            selected_steps=["lrc"],
            force=False,
            analysis_tier="fast",
            stale_after_minutes=120,
            console=Console(quiet=True),
        )

        # One submit per song, all from Phase 2 (no download phase ran)
        assert stubs["analysis_client"].submit_lrc.call_count == len(song_ids)


class TestSubmitLrcForSongHelper:
    """Unit tests for the extracted _submit_lrc_for_song helper."""

    def test_r2_preexisting_skips_submission(self, stubs):
        song_id = "s1"
        rec = _make_recording(song_id)
        stubs["db_client"].get_recording_by_song_id.return_value = rec
        stubs["db_client"].get_song.return_value = _make_song(song_id)
        stubs["r2_client"].lrc_exists.return_value = "https://r2/lrc.lrc"

        active: dict = {}
        attempted: set = set()
        results: dict = {song_id: {}}
        manifest: list = []

        def _add_entry(*args, **kwargs):
            manifest.append(kwargs)

        status = _submit_lrc_for_song(
            song_id,
            stubs["db_client"],
            stubs["analysis_client"],
            stubs["r2_client"],
            force=False,
            stale_after_minutes=120,
            console=Console(quiet=True),
            results=results,
            active_lrc_jobs=active,
            lrc_attempted=attempted,
            _add_manifest_entry=_add_entry,
        )

        assert status == "skipped_r2"
        assert results[song_id]["lrc"] == "completed"
        assert song_id not in active
        stubs["analysis_client"].submit_lrc.assert_not_called()

    def test_submit_success_populates_active_jobs(self, stubs):
        song_id = "s1"
        rec = _make_recording(song_id)
        stubs["db_client"].get_recording_by_song_id.return_value = rec
        stubs["db_client"].get_song.return_value = _make_song(song_id)
        stubs["r2_client"].lrc_exists.return_value = None

        active: dict = {}
        attempted: set = set()
        results: dict = {song_id: {}}
        manifest: list = []

        def _add_entry(*args, **kwargs):
            manifest.append(kwargs)

        status = _submit_lrc_for_song(
            song_id,
            stubs["db_client"],
            stubs["analysis_client"],
            stubs["r2_client"],
            force=False,
            stale_after_minutes=120,
            console=Console(quiet=True),
            results=results,
            active_lrc_jobs=active,
            lrc_attempted=attempted,
            _add_manifest_entry=_add_entry,
        )

        assert status == "submitted"
        assert song_id in active
        assert song_id in attempted
        stubs["analysis_client"].submit_lrc.assert_called_once()

    def test_submit_failure_marks_failed(self, stubs):
        song_id = "s1"
        rec = _make_recording(song_id)
        stubs["db_client"].get_recording_by_song_id.return_value = rec
        stubs["db_client"].get_song.return_value = _make_song(song_id)
        stubs["r2_client"].lrc_exists.return_value = None
        stubs["analysis_client"].submit_lrc.side_effect = AnalysisServiceError(
            "boom", status_code=500
        )

        active: dict = {}
        attempted: set = set()
        results: dict = {song_id: {}}
        manifest: list = []

        def _add_entry(*args, **kwargs):
            manifest.append(kwargs)

        status = _submit_lrc_for_song(
            song_id,
            stubs["db_client"],
            stubs["analysis_client"],
            stubs["r2_client"],
            force=False,
            stale_after_minutes=120,
            console=Console(quiet=True),
            results=results,
            active_lrc_jobs=active,
            lrc_attempted=attempted,
            _add_manifest_entry=_add_entry,
        )

        assert status == "failed"
        assert results[song_id]["lrc"] == "failed"
        assert song_id not in active
        assert song_id in attempted

    def test_no_lyrics_skips(self, stubs):
        song_id = "s1"
        rec = _make_recording(song_id)
        stubs["db_client"].get_recording_by_song_id.return_value = rec
        song = _make_song(song_id)
        song.lyrics_raw = None
        stubs["db_client"].get_song.return_value = song

        active: dict = {}
        attempted: set = set()
        results: dict = {song_id: {}}

        status = _submit_lrc_for_song(
            song_id,
            stubs["db_client"],
            stubs["analysis_client"],
            stubs["r2_client"],
            force=False,
            stale_after_minutes=120,
            console=Console(quiet=True),
            results=results,
            active_lrc_jobs=active,
            lrc_attempted=attempted,
            _add_manifest_entry=lambda *a, **k: None,
        )

        assert status == "skipped_no_lyrics"
        stubs["analysis_client"].submit_lrc.assert_not_called()
