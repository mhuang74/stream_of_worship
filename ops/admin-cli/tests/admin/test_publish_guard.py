"""Tests for the published-recording reprocess guard.

A recording an admin validated and published must never be regenerated or
replaced by a later CLI run: every writer refuses (exit 1) for a single
target, or skips and reports the same remediation command in batch mode.
"""

import io
from unittest.mock import MagicMock, patch

import pytest
import typer
from rich.console import Console
from typer.testing import CliRunner

from stream_of_worship.admin.commands import audio as audio_commands
from stream_of_worship.admin.commands import lyrics as lyrics_commands
from stream_of_worship.admin.commands.audio import (
    _advance_song,
    _backfill_lyrics_for_song,
    _print_stats,
    _submit_analysis_for_song,
    _submit_components_for_song,
    _submit_lrc_for_song,
    import_youtube_audio_for_song,
)
from stream_of_worship.admin.commands.lyrics import (
    _preflight_guard,
    _submit_forced_alignment_batch,
    _submit_forced_alignment_single,
)
from stream_of_worship.admin.db.models import Recording, Song
from stream_of_worship.admin.services.analysis import JobInfo
from stream_of_worship.admin.main import app
from stream_of_worship.admin.services.lrc_jobs import (
    submit_lrc_batch,
    submit_lrc_job,
    submit_lrc_single,
)
from stream_of_worship.admin.services.visibility import (
    ensure_reprocessable,
    is_published,
    reprocess_blocked_reason,
)

runner = CliRunner()

REMEDIATION = "audio set-visibility song_1 --status review"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _console() -> tuple[Console, io.StringIO]:
    buffer = io.StringIO()
    return Console(file=buffer, force_terminal=False, width=200), buffer


def _make_recording(
    song_id: str = "song_1",
    visibility_status: str | None = "published",
    hash_prefix: str = "abc123def456",
) -> Recording:
    return Recording(
        content_hash=hash_prefix.ljust(64, "0"),
        hash_prefix=hash_prefix,
        original_filename="test.mp3",
        file_size_bytes=100,
        imported_at="2024-01-01T00:00:00",
        song_id=song_id,
        r2_audio_url="s3://bucket/audio.mp3",
        lrc_status="completed",
        analysis_status="completed",
        visibility_status=visibility_status,
        updated_at="2024-01-01T00:00:00+00:00",
    )


def _make_song(song_id: str = "song_1") -> Song:
    return Song(
        id=song_id,
        title="Test Song",
        source_url="http://example.com",
        scraped_at="2024-01-01T00:00:00",
        composer="Composer",
        lyrics_raw="line one\nline two",
        lyrics_lines='["line one","line two"]',
    )


def _noop_manifest_entry(*args, **kwargs):
    return None


def _flat(text: str) -> str:
    """Collapse console line-wrapping so message assertions are width-agnostic."""
    return " ".join(text.split())


# ---------------------------------------------------------------------------
# Guard primitives
# ---------------------------------------------------------------------------


class TestIsPublished:
    @pytest.mark.parametrize("status", ["review", "hold", None])
    def test_non_published_statuses_are_not_protected(self, status):
        assert is_published(_make_recording(visibility_status=status)) is False

    def test_published_status_is_protected(self):
        assert is_published(_make_recording(visibility_status="published")) is True

    def test_missing_recording_is_not_protected(self):
        assert is_published(None) is False


class TestEnsureReprocessable:
    def test_blocks_and_names_remediation(self):
        console, buffer = _console()
        with pytest.raises(typer.Exit) as exc:
            ensure_reprocessable(
                _make_recording(),
                song_id="song_1",
                action="regenerate LRC",
                console=console,
            )
        assert exc.value.exit_code == 1
        output = buffer.getvalue()
        assert REMEDIATION in output
        assert "regenerate LRC" in output

    def test_allows_review_recording(self):
        console, buffer = _console()
        ensure_reprocessable(
            _make_recording(visibility_status="review"),
            song_id="song_1",
            action="regenerate LRC",
            console=console,
        )
        assert buffer.getvalue() == ""

    def test_allows_missing_recording(self):
        console, buffer = _console()
        ensure_reprocessable(None, song_id="song_1", action="regenerate LRC", console=console)
        assert buffer.getvalue() == ""

    def test_reason_names_action_and_remediation(self):
        reason = reprocess_blocked_reason("song_1", "abc123def456", "upload LRC")
        assert "upload LRC" in reason
        assert REMEDIATION in reason


# ---------------------------------------------------------------------------
# LRC job submission
# ---------------------------------------------------------------------------


class TestLrcJobGuards:
    def test_submit_lrc_single_refuses_published(self):
        db_client = MagicMock()
        db_client.get_recording_by_song_id.return_value = _make_recording()
        analysis_client = MagicMock()
        console, buffer = _console()

        with pytest.raises(typer.Exit) as exc:
            submit_lrc_single(
                "song_1",
                db_client,
                analysis_client,
                force=True,
                whisper_model="large-v3",
                language="auto",
                no_vocals=False,
                generator="auto",
                no_cache=False,
                wait=False,
                console=console,
            )

        assert exc.value.exit_code == 1
        assert REMEDIATION in buffer.getvalue()
        analysis_client.submit_lrc.assert_not_called()

    def test_submit_lrc_batch_skips_published(self):
        db_client = MagicMock()
        db_client.get_recording_by_song_id.return_value = _make_recording()
        db_client.get_song.return_value = _make_song()
        analysis_client = MagicMock()
        console, buffer = _console()

        submissions = submit_lrc_batch(
            ["song_1"],
            db_client,
            analysis_client,
            force=True,
            whisper_model="large-v3",
            language="auto",
            no_vocals=False,
            generator="auto",
            no_cache=False,
            console=console,
        )

        assert submissions == []
        analysis_client.submit_lrc.assert_not_called()
        assert REMEDIATION in buffer.getvalue()

    def test_submit_lrc_job_refuses_published(self):
        console, buffer = _console()
        analysis_client_cls = MagicMock()

        with (
            patch(
                "stream_of_worship.admin.services.lrc_jobs.AnalysisClient",
                analysis_client_cls,
            ),
            pytest.raises(typer.Exit) as exc,
        ):
            submit_lrc_job(
                "song_1",
                _make_recording(),
                "http://analysis",
                MagicMock(),
                console,
            )

        assert exc.value.exit_code == 1
        assert REMEDIATION in buffer.getvalue()
        analysis_client_cls.assert_not_called()


# ---------------------------------------------------------------------------
# Forced alignment
# ---------------------------------------------------------------------------


class TestAlignmentGuards:
    def test_alignment_single_refuses_published(self):
        db_client = MagicMock()
        db_client.get_recording_by_song_id.return_value = _make_recording()
        db_client.get_song.return_value = _make_song()
        analysis_client = MagicMock()
        console, buffer = _console()

        with pytest.raises(typer.Exit) as exc:
            _submit_forced_alignment_single(
                "song_1",
                db_client,
                analysis_client,
                language="auto",
                force=True,
                use_vocals_stem=False,
                wait=False,
                console=console,
            )

        assert exc.value.exit_code == 1
        assert REMEDIATION in buffer.getvalue()
        analysis_client.submit_forced_alignment.assert_not_called()

    def test_alignment_batch_skips_published(self):
        db_client = MagicMock()
        db_client.get_recording_by_song_id.return_value = _make_recording()
        db_client.get_song.return_value = _make_song()
        analysis_client = MagicMock()
        console, buffer = _console()

        submissions, skipped, errors = _submit_forced_alignment_batch(
            ["song_1"],
            db_client,
            analysis_client,
            language="auto",
            force=True,
            use_vocals_stem=False,
            console=console,
        )

        assert submissions == []
        assert (skipped, errors) == (1, 0)
        assert REMEDIATION in buffer.getvalue()
        analysis_client.submit_forced_alignment.assert_not_called()


# ---------------------------------------------------------------------------
# Single-target audio commands
# ---------------------------------------------------------------------------


def _invoke_audio(args: list[str], db_client: MagicMock):
    """Invoke an audio subcommand with config/DB mocked."""
    with (
        patch.object(audio_commands, "AdminConfig") as config_cls,
        patch.object(audio_commands, "get_db_client", return_value=db_client),
    ):
        config_cls.load.return_value = config_cls.return_value
        config_cls.return_value.analysis_url = "http://analysis"
        return runner.invoke(app, ["audio", *args])


class TestAudioSingleTargetGuards:
    def test_analyze_command_refuses_published(self):
        db_client = MagicMock()
        db_client.get_recording_by_song_id.return_value = _make_recording()
        analysis_client_cls = MagicMock()

        with patch.object(audio_commands, "AnalysisClient", analysis_client_cls):
            result = _invoke_audio(["analyze", "song_1"], db_client)

        assert result.exit_code == 1, result.output
        assert REMEDIATION in _flat(result.output)
        analysis_client_cls.assert_not_called()

    def test_components_command_refuses_published(self):
        db_client = MagicMock()
        db_client.get_recording_by_song_id.return_value = _make_recording()
        analysis_client_cls = MagicMock()

        with patch.object(audio_commands, "AnalysisClient", analysis_client_cls):
            result = _invoke_audio(["components", "song_1"], db_client)

        assert result.exit_code == 1, result.output
        assert REMEDIATION in _flat(result.output)
        analysis_client_cls.assert_not_called()

    def test_delete_command_refuses_published(self):
        db_client = MagicMock()
        db_client.list_active_recordings_by_song.return_value = [_make_recording()]
        db_client.get_song.return_value = _make_song()

        result = _invoke_audio(["delete", "song_1", "--yes"], db_client)

        assert result.exit_code == 1, result.output
        assert REMEDIATION in _flat(result.output)
        db_client.delete_recording.assert_not_called()

    def test_backfill_lyrics_refuses_published(self):
        db_client = MagicMock()
        db_client.get_recording_by_song_id.return_value = _make_recording()
        console, buffer = _console()

        with pytest.raises(typer.Exit) as exc:
            _backfill_lyrics_for_song(song_id="song_1", db_client=db_client, console=console)

        assert exc.value.exit_code == 1
        assert REMEDIATION in buffer.getvalue()
        db_client.update_recording_structured_lyrics.assert_not_called()

    def test_download_force_refuses_published_replacement(self):
        db_client = MagicMock()
        db_client.get_song.return_value = _make_song()
        db_client.list_active_recordings_by_song.return_value = [_make_recording()]
        console, buffer = _console()
        downloader_cls = MagicMock()

        with (
            patch.object(audio_commands, "R2Client", MagicMock()),
            patch.object(audio_commands, "YouTubeDownloader", downloader_cls),
            pytest.raises(typer.Exit) as exc,
        ):
            import_youtube_audio_for_song(
                song_id="song_1",
                youtube_url=None,
                config=MagicMock(),
                db_client=db_client,
                console=console,
                force=True,
            )

        assert exc.value.exit_code == 1
        assert REMEDIATION in buffer.getvalue()
        downloader_cls.assert_not_called()


# ---------------------------------------------------------------------------
# Manual lyrics writers
# ---------------------------------------------------------------------------


class TestManualLyricsWriterGuards:
    def test_lyrics_upload_refuses_published(self, tmp_path):
        lrc_file = tmp_path / "song.lrc"
        lrc_file.write_text("[00:01.00]line one\n", encoding="utf-8")
        db_client = MagicMock()
        db_client.get_recording_by_song_id.return_value = _make_recording()
        r2_client_cls = MagicMock()

        with (
            patch.object(lyrics_commands, "AdminConfig") as config_cls,
            patch.object(lyrics_commands, "get_db_client", return_value=db_client),
            patch.object(lyrics_commands, "R2Client", r2_client_cls),
        ):
            config_cls.load.return_value = config_cls.return_value
            result = runner.invoke(
                lyrics_commands.app, ["upload", "song_1", str(lrc_file)]
            )

        assert result.exit_code == 1, result.output
        assert REMEDIATION in _flat(result.output)
        r2_client_cls.assert_not_called()
        db_client.update_recording_lrc.assert_not_called()

    def test_lyrics_upload_structured_refuses_published(self, tmp_path):
        """Also blocks the first-time population path for a published song."""
        lyrics_file = tmp_path / "song.txt"
        lyrics_file.write_text("[Verse]\nline one\n", encoding="utf-8")
        db_client = MagicMock()
        db_client.get_recording_by_song_id.return_value = _make_recording(
            visibility_status="published"
        )

        with (
            patch.object(lyrics_commands, "AdminConfig") as config_cls,
            patch.object(lyrics_commands, "get_db_client", return_value=db_client),
        ):
            config_cls.load.return_value = config_cls.return_value
            result = runner.invoke(
                lyrics_commands.app,
                ["upload-structured", "song_1", str(lyrics_file)],
            )

        assert result.exit_code == 1, result.output
        assert REMEDIATION in _flat(result.output)
        db_client.update_recording_structured_lyrics.assert_not_called()

    def test_lyrics_edit_refuses_published(self):
        db_client = MagicMock()
        db_client.get_recording_by_song_id.return_value = _make_recording()
        session = MagicMock()

        with (
            patch.object(lyrics_commands, "AdminConfig") as config_cls,
            patch.object(lyrics_commands, "get_db_client", return_value=db_client),
            patch.object(lyrics_commands, "R2Client", MagicMock()),
            patch.object(lyrics_commands, "_resolve_feedback_user_id", return_value=None),
            patch.object(lyrics_commands, "_run_editor_session", session),
        ):
            config_cls.load.return_value = config_cls.return_value
            result = runner.invoke(lyrics_commands.app, ["edit", "song_1"])

        assert result.exit_code == 1, result.output
        assert REMEDIATION in _flat(result.output)
        session.assert_not_called()


# ---------------------------------------------------------------------------
# Batch submit helpers
# ---------------------------------------------------------------------------


class TestBatchSubmitHelpers:
    def test_submit_lrc_for_song_skips_published(self):
        db_client = MagicMock()
        db_client.get_recording_by_song_id.return_value = _make_recording()
        db_client.get_song.return_value = _make_song()
        analysis_client = MagicMock()
        results = {"song_1": {}}

        status = _submit_lrc_for_song(
            "song_1",
            db_client,
            analysis_client,
            MagicMock(),
            force=True,
            stale_after_minutes=120,
            console=_console()[0],
            results=results,
            active_lrc_jobs={},
            lrc_attempted=set(),
            _add_manifest_entry=_noop_manifest_entry,
        )

        assert status == "skipped_published"
        assert results["song_1"]["generate_lyrics"] == "skipped_published"
        analysis_client.submit_lrc.assert_not_called()

    def test_submit_analysis_for_song_skips_published(self):
        db_client = MagicMock()
        db_client.get_recording_by_song_id.return_value = _make_recording()
        analysis_client = MagicMock()
        results = {"song_1": {}}

        job_id, status = _submit_analysis_for_song(
            "song_1",
            db_client,
            analysis_client,
            MagicMock(),
            force=True,
            analysis_tier="fast",
            stale_after_minutes=120,
            console=_console()[0],
            results=results,
            _add_manifest_entry=_noop_manifest_entry,
        )

        assert (job_id, status) == (None, "skipped_published")
        assert results["song_1"]["analyze"] == "skipped_published"
        analysis_client.submit_fast_analysis.assert_not_called()

    def test_submit_components_for_song_skips_published(self):
        db_client = MagicMock()
        db_client.get_recording_by_song_id.return_value = _make_recording()
        analysis_client = MagicMock()
        results = {"song_1": {}}

        job_id, status = _submit_components_for_song(
            "song_1",
            db_client,
            analysis_client,
            MagicMock(),
            True,
            _console()[0],
            results,
            _noop_manifest_entry,
        )

        assert (job_id, status) == (None, "skipped_published")
        assert results["song_1"]["components"] == "skipped_published"
        analysis_client.submit_component_analysis.assert_not_called()

    def test_batch_download_skips_published(self):
        """`audio batch` must not re-acquire audio for a published recording."""
        recording = _make_recording()
        recording.download_status = "pending"
        recording.r2_audio_url = None
        db_client = MagicMock()
        r2_client = MagicMock()
        console, buffer = _console()

        result = audio_commands._download_if_needed(
            "song_1", recording, db_client, r2_client, console
        )

        assert result["download"] == "skipped_published"
        r2_client.audio_exists.assert_not_called()
        db_client.update_recording_download.assert_not_called()
        db_client.update_recording_r2_url.assert_not_called()
        assert "demote first" in buffer.getvalue()

    def test_advance_song_continues_past_skipped_published(self):
        """A published skip must not fail or stall the step chain."""
        results = {"song_1": {}}

        with patch.object(audio_commands, "_submit_step", return_value=(None, "skipped_published")):
            _advance_song(
                "song_1",
                "download",
                ["download", "generate_lyrics", "analyze", "components"],
                MagicMock(),
                MagicMock(),
                MagicMock(),
                force=True,
                analysis_tier="fast",
                stale_after_minutes=120,
                console=_console()[0],
                results=results,
                active_jobs={},
                lrc_attempted=set(),
                _add_manifest_entry=_noop_manifest_entry,
                config=MagicMock(),
            )

        assert results["song_1"]["_pipeline"] == "completed"
        assert "failed" not in results["song_1"].values()

    def test_print_stats_reports_published_protected(self):
        db_client = MagicMock()
        db_client.get_song.return_value = _make_song()
        console, buffer = _console()

        _print_stats(
            {"song_1": {"generate_lyrics": "skipped_published"}},
            db_client,
            console,
            format="rich",
        )

        output = buffer.getvalue()
        assert "Published (protected from re-processing): 1" in output
        assert REMEDIATION in output
        assert "Requires manual intervention" not in output


# ---------------------------------------------------------------------------
# Integration: pre-flight guard + end-to-end generate
# ---------------------------------------------------------------------------


def _drop_all_tables(make_test_provider):
    """Drop leftover tables so each integration test starts from a clean DB."""
    try:
        cleanup_provider = make_test_provider()
        with cleanup_provider.get_connection().cursor() as cur:
            cur.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
        cleanup_provider.get_connection().commit()
        cleanup_provider.close()
    except Exception:
        pass


def _init_schema(make_test_provider):
    from stream_of_worship.db.postgres_schema import ALL_SCHEMA_STATEMENTS

    _drop_all_tables(make_test_provider)
    provider = make_test_provider()
    with provider.get_connection().cursor() as cur:
        for stmt in ALL_SCHEMA_STATEMENTS:
            cur.execute(stmt)
    provider.get_connection().commit()
    return provider


def _seed_published(provider):
    conn = provider.get_connection()
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO songs (id, title, source_url, scraped_at, composer, lyrics_raw)
            VALUES ('song_1', 'Song One', 'https://example.com/1',
                    '2024-01-01T00:00:00', '作曲', '歌词一')
            """
        )
        cur.execute(
            """
            INSERT INTO recordings (
                content_hash, hash_prefix, song_id, original_filename,
                file_size_bytes, imported_at, lrc_status, r2_audio_url,
                visibility_status
            )
            VALUES ('hashaaaaaaaaaaaa', 'hashaaaaaaaa', 'song_1', 'one.mp3',
                    100, '2024-01-01T00:00:00', 'completed', 's3://bucket/one.mp3',
                    'published')
            """
        )
    conn.commit()
    return provider


@pytest.mark.integration
class TestPublishedGuardIntegration:
    def _set_visibility(self, provider, status: str) -> None:
        conn = provider.get_connection()
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE recordings SET visibility_status = %s WHERE song_id = 'song_1'",
                (status,),
            )
        conn.commit()

    def test_preflight_guard_skips_published_recording(self, make_test_provider):
        from stream_of_worship.admin.db.client import DatabaseClient

        provider = _seed_published(_init_schema(make_test_provider))
        db_client = DatabaseClient(provider)
        console, buffer = _console()

        accepted, skipped = _preflight_guard(["song_1"], db_client, provider, console)

        assert accepted == []
        assert skipped[0]["reason"] == "skipped-published"
        assert REMEDIATION in buffer.getvalue()

        self._set_visibility(provider, "review")
        accepted, skipped = _preflight_guard(["song_1"], db_client, provider, console)

        assert accepted == ["song_1"]
        assert skipped == []
        provider.close()

    def test_generate_refuses_then_allows_after_demotion(self, make_test_provider):
        """End-to-end: `lyrics generate <song> --force` is refused while the
        recording is published and submits exactly once after demotion."""
        from stream_of_worship.admin.db.client import DatabaseClient

        provider = _seed_published(_init_schema(make_test_provider))
        db_client = DatabaseClient(provider)

        def _invoke():
            with (
                patch.object(lyrics_commands, "AdminConfig") as config_cls,
                patch.object(lyrics_commands, "get_db_client", return_value=db_client),
                patch.object(lyrics_commands, "AnalysisClient") as analysis_cls,
            ):
                analysis_cls.return_value.submit_lrc.return_value = JobInfo(
                    job_id="lrc-job-1", status="queued", job_type="lrc"
                )
                config_cls.load.return_value = config_cls.return_value
                config_cls.return_value.analysis_url = "http://analysis"
                result = runner.invoke(
                    lyrics_commands.app, ["generate", "song_1", "--force", "--no-wait"]
                )
            return result, analysis_cls

        refused, analysis_cls = _invoke()
        assert refused.exit_code == 1, refused.output
        assert REMEDIATION in _flat(refused.output)
        analysis_cls.return_value.submit_lrc.assert_not_called()

        self._set_visibility(provider, "review")

        allowed, analysis_cls = _invoke()
        assert allowed.exit_code == 0, allowed.output
        analysis_cls.return_value.submit_lrc.assert_called_once()
        provider.close()
