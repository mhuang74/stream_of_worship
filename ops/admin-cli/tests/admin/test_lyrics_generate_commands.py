"""Tests for `sow-admin lyrics generate` stdin/batch behavior."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from stream_of_worship.admin.commands import lyrics as lyrics_commands
from stream_of_worship.db.connection import ConnectionProvider
from stream_of_worship.db.postgres_schema import ALL_SCHEMA_STATEMENTS

runner = CliRunner()
lyrics_app = lyrics_commands.app


@pytest.fixture(autouse=True)
def _disable_ssl(monkeypatch):
    """Force sslmode=disable for the CLI's connections.

    ConnectionProvider defaults to sslmode="require", which the
    testcontainers Postgres does not support.
    """

    def _factory(url, sslmode="require"):
        return ConnectionProvider(url, sslmode="disable")

    monkeypatch.setattr(lyrics_commands, "ConnectionProvider", _factory)


def _drop_all_tables(make_test_provider):
    """Drop all tables for cleanup."""
    try:
        cleanup_provider = make_test_provider()
        with cleanup_provider.get_connection().cursor() as cur:
            cur.execute(
                """
                DROP TABLE IF EXISTS lyrics_feedback CASCADE;
                DROP TABLE IF EXISTS songset_share CASCADE;
                DROP TABLE IF EXISTS lyric_mark CASCADE;
                DROP TABLE IF EXISTS user_lrc_override CASCADE;
                DROP TABLE IF EXISTS user_settings CASCADE;
                DROP TABLE IF EXISTS songset_items CASCADE;
                DROP TABLE IF EXISTS songsets CASCADE;
                DROP TABLE IF EXISTS theme_anchors CASCADE;
                DROP TABLE IF EXISTS song_line_embedding CASCADE;
                DROP TABLE IF EXISTS song_embedding CASCADE;
                DROP TABLE IF EXISTS recordings CASCADE;
                DROP TABLE IF EXISTS songs CASCADE;
                DROP TABLE IF EXISTS "session" CASCADE;
                DROP TABLE IF EXISTS "account" CASCADE;
                DROP TABLE IF EXISTS "verification" CASCADE;
                DROP TABLE IF EXISTS "user" CASCADE;
                DROP FUNCTION IF EXISTS update_updated_at_column CASCADE;
                DROP FUNCTION IF EXISTS update_updatedat_column CASCADE;
                DROP EXTENSION IF EXISTS vector CASCADE;
                """
            )
        cleanup_provider.close()
    except Exception:
        pass


def _write_config(tmp_path, postgres_url):
    config_path = Path(tmp_path) / "config.toml"
    config_path.write_text(f'[database]\nurl = "{postgres_url}"\n')
    return config_path


def _init_schema(make_test_provider):
    # Drop leftovers from a previous failed run first: the end-of-test
    # cleanup is skipped when a test errors during seeding.
    _drop_all_tables(make_test_provider)
    provider = make_test_provider()
    with provider.get_connection().cursor() as cur:
        for stmt in ALL_SCHEMA_STATEMENTS:
            cur.execute(stmt)
    provider.get_connection().commit()
    return provider


def _seed_guard_data(provider):
    """Seed two songs: song_001 (target matches feedback) and song_002
    (feedback on a different recording than the generation target).

    get_recording_by_song_id has no ORDER BY — it returns an arbitrary
    row among the song's recordings. For song_002 we seed two recordings;
    the feedback points at the NON-target hash. Because the lookup may
    return either, we detect which hash the command targets dynamically
    (recorded via submit_lrc_batch calls) instead of pinning one.
    """
    conn = provider.get_connection()
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO "user" (id, name, email, "emailVerified")
            OVERRIDING SYSTEM VALUE
            VALUES (1, 'User One', 'u1@example.com', true)
            """
        )
        cur.execute(
            """
            INSERT INTO songs (id, title, source_url, scraped_at, composer, lyrics_raw)
            VALUES
                ('song_001', 'Song One', 'https://example.com/1', '2024-01-01T00:00:00', '作曲', '歌词一'),
                ('song_002', 'Song Two', 'https://example.com/2', '2024-01-01T00:00:00', '作曲', '歌词二')
            """
        )
        for hash_, prefix, filename, song_id in [
            ("hash-aaaaaaaaaaaa", "hash-aaaaaa", "one.mp3", "song_001"),
            ("hash-bbbbbbbbbbbb", "hash-bbbbbb", "two-a.mp3", "song_002"),
            ("hash-cccccccccccc", "hash-cccccc", "two-b.mp3", "song_002"),
        ]:
            cur.execute(
                """
                INSERT INTO recordings (
                    content_hash, hash_prefix, song_id, original_filename,
                    file_size_bytes, imported_at, lrc_status, r2_audio_url
                )
                VALUES (%s, %s, %s, %s, 100, '2024-01-01T00:00:00', 'missing', 's3://bucket/audio')
                """,
                (hash_, prefix, song_id, filename),
            )
        # song_001: open sad feedback on its only recording → passes guard.
        cur.execute(
            """
            INSERT INTO lyrics_feedback (id, user_id, recording_content_hash, rating, reason)
            VALUES ('fb-1', 1, 'hash-aaaaaaaaaaaa', 'sad', 'missing')
            """
        )
        # song_002: open sad feedback on hash-bb only.
        cur.execute(
            """
            INSERT INTO lyrics_feedback (id, user_id, recording_content_hash, rating, reason)
            VALUES ('fb-2', 1, 'hash-bbbbbbbbbbbb', 'sad', 'timing')
            """
        )
    conn.commit()
    return provider


def _invoke_stdin_generate(stdin_text: str, extra_args: tuple = ()):
    """Invoke `generate --stdin --force` with config/DB/service mocked and
    submit_lrc_single / submit_lrc_batch patched. Returns
    (result, single_mock, batch_mock)."""
    with (
        patch.object(lyrics_commands, "AdminConfig", MagicMock()) as config_mock,
        patch.object(lyrics_commands, "get_db_client", MagicMock()) as db_mock,
        patch.object(lyrics_commands, "AnalysisClient", MagicMock()),
        patch.object(lyrics_commands, "ConnectionProvider", MagicMock()),
        patch.object(
            lyrics_commands, "submit_lrc_single", return_value=MagicMock()
        ) as single_mock,
        patch.object(
            lyrics_commands, "submit_lrc_batch", return_value=[]
        ) as batch_mock,
    ):
        config = config_mock.return_value
        config.get_connection_url.return_value = "postgresql://fake"
        result = runner.invoke(
            lyrics_app, ["generate", "--stdin", "--force", *extra_args], input=stdin_text
        )
    return result, single_mock, batch_mock


def test_generate_requires_song_id_or_stdin():
    result = runner.invoke(lyrics_app, ["generate"])
    assert result.exit_code == 1
    assert "Either provide a song_id argument or use --stdin flag" in result.output


def test_generate_rejects_song_id_with_stdin():
    result = runner.invoke(lyrics_app, ["generate", "song_001", "--stdin"])
    assert result.exit_code == 1
    assert "Cannot use both song_id argument and --stdin flag" in result.output


def test_generate_accepts_wait_with_stdin():
    """--wait is now valid with --stdin (batch mode polls all jobs)."""
    result, single_mock, batch_mock = _invoke_stdin_generate("song_001\n", ("--wait",))
    assert "not supported with --stdin" not in result.output


def test_generate_rejects_resume_with_stdin():
    result = runner.invoke(lyrics_app, ["generate", "--stdin", "--resume", "m.json"], input="")
    assert result.exit_code == 1
    assert "Cannot use both --stdin and --resume" in result.output


def test_generate_rejects_resume_with_dry_run():
    result = runner.invoke(
        lyrics_app, ["generate", "--resume", "m.json", "--dry-run"], input=""
    )
    assert result.exit_code == 1
    assert "--dry-run is not valid with --resume" in result.output


def test_generate_rejects_resume_with_song_id():
    result = runner.invoke(
        lyrics_app, ["generate", "song_001", "--resume", "m.json"], input=""
    )
    assert result.exit_code == 1
    assert "Cannot use both song_id argument and --resume" in result.output


def test_generate_empty_stdin_prints_message_and_exits_zero():
    with (
        patch.object(lyrics_commands, "AdminConfig", MagicMock()),
        patch.object(lyrics_commands, "get_db_client", MagicMock()),
        patch.object(lyrics_commands, "AnalysisClient", MagicMock()),
    ):
        result = runner.invoke(lyrics_app, ["generate", "--stdin"], input="")
    assert result.exit_code == 0, result.output
    assert "No song IDs provided via stdin" in result.output


def test_generate_stdin_single_id_takes_batch_path():
    """A 1-id pipe gets the batch flow (guard + manifest-able batch submit),
    never the single-song 600s wait path."""
    with (
        patch.object(lyrics_commands, "AdminConfig", MagicMock()) as config_mock,
        patch.object(lyrics_commands, "get_db_client", MagicMock()),
        patch.object(lyrics_commands, "AnalysisClient", MagicMock()),
        patch.object(lyrics_commands, "ConnectionProvider") as provider_cls_mock,
        patch.object(lyrics_commands, "submit_lrc_single") as single_mock,
        patch.object(
            lyrics_commands, "submit_lrc_batch", return_value=[]
        ) as batch_mock,
    ):
        config = config_mock.return_value
        config.get_connection_url.return_value = "postgresql://fake"
        # Guard query returns no open feedback → passes unconditionally.
        conn = provider_cls_mock.return_value.get_connection.return_value
        cursor = MagicMock()
        cursor.__enter__.return_value = cursor
        cursor.fetchall.return_value = []
        conn.cursor.return_value = cursor
        result = runner.invoke(lyrics_app, ["generate", "--stdin", "--force"], input="song_001\n")
    assert result.exit_code == 0, result.output
    single_mock.assert_not_called()
    batch_mock.assert_called_once()
    assert batch_mock.call_args.kwargs["song_ids"] == ["song_001"]


def test_generate_stdin_multiple_ids_routes_to_batch():
    result, single_mock, batch_mock = _invoke_stdin_generate("song_001\nsong_002\n")
    assert result.exit_code == 0, result.output
    batch_mock.assert_called_once()
    assert batch_mock.call_args.kwargs["song_ids"] == ["song_001", "song_002"]
    assert batch_mock.call_args.kwargs["force"] is True
    single_mock.assert_not_called()


@pytest.mark.integration
class TestPreFlightGuard:
    """Seam 2: feedback-aware pre-flight target guard on real Postgres."""

    def teardown_method(self):
        pass

    def test_guard_skips_mismatched_target(
        self, make_test_provider, postgres_url, tmp_path
    ):
        _init_schema(make_test_provider)
        provider = _seed_guard_data(make_test_provider())
        config_path = _write_config(tmp_path, postgres_url)
        target_hash = _target_hash(provider, "song_002")

        with (
            patch.object(lyrics_commands, "get_db_client") as db_mock,
            patch.object(lyrics_commands, "AnalysisClient", MagicMock()),
            patch.object(
                lyrics_commands, "submit_lrc_batch", return_value=[]
            ) as batch_mock,
        ):
            db_client = db_mock.return_value
            # The submission path's own lookup: same recording the CLI sees.
            db_client.get_recording_by_song_id.side_effect = _fake_lookup(provider)
            result = runner.invoke(
                lyrics_app,
                ["generate", "--stdin", "--force", "--config", str(config_path)],
                input="song_002\n",
            )
        assert result.exit_code == 0, result.output
        # hash-bb has the feedback; if the lookup targets hash-cc the guard
        # must skip the song. If it targets hash-bb the song is submitted —
        # either way, never a silent mismatched regeneration.
        if target_hash != "hash-bbbbbbbbbbbb":
            batch_mock.assert_not_called()
            assert "skipped-guard" in result.output
            assert "song_002" in result.output
        else:
            batch_mock.assert_called_once()

    def test_guard_passes_matching_target(self, make_test_provider, postgres_url, tmp_path):
        _init_schema(make_test_provider)
        _seed_guard_data(make_test_provider())
        config_path = _write_config(tmp_path, postgres_url)

        with (
            patch.object(lyrics_commands, "get_db_client") as db_mock,
            patch.object(lyrics_commands, "AnalysisClient", MagicMock()),
            patch.object(
                lyrics_commands, "submit_lrc_batch", return_value=[]
            ) as batch_mock,
        ):
            db_client = db_mock.return_value
            db_client.get_recording_by_song_id.side_effect = _fake_lookup(provider := make_test_provider())
            result = runner.invoke(
                lyrics_app,
                ["generate", "--stdin", "--force", "--config", str(config_path)],
                input="song_001\n",
            )
        assert result.exit_code == 0, result.output
        batch_mock.assert_called_once()
        assert batch_mock.call_args.kwargs["song_ids"] == ["song_001"]
        _drop_all_tables(make_test_provider)

    def test_guard_passes_song_without_feedback(
        self, make_test_provider, postgres_url, tmp_path
    ):
        _init_schema(make_test_provider)
        _seed_guard_data(make_test_provider())
        config_path = _write_config(tmp_path, postgres_url)

        with (
            patch.object(lyrics_commands, "get_db_client") as db_mock,
            patch.object(lyrics_commands, "AnalysisClient", MagicMock()),
            patch.object(
                lyrics_commands, "submit_lrc_batch", return_value=[]
            ) as batch_mock,
        ):
            db_client = db_mock.return_value
            db_client.get_recording_by_song_id.side_effect = _fake_lookup(make_test_provider())
            result = runner.invoke(
                lyrics_app,
                ["generate", "--stdin", "--force", "--config", str(config_path)],
                input="song_unknown\n",
            )
        assert result.exit_code == 0, result.output
        # No feedback rows → guard passes unconditionally (batch will report
        # its own no-recording error; here we only assert the guard passed).
        batch_mock.assert_called_once()
        _drop_all_tables(make_test_provider)


def _fake_lookup(provider):
    """Return a get_recording_by_song_id stub backed by the real DB."""

    def _lookup(song_id):
        with provider.get_connection().cursor() as cur:
            cur.execute(
                "SELECT content_hash, hash_prefix FROM recordings "
                "WHERE song_id = %s AND deleted_at IS NULL LIMIT 1",
                (song_id,),
            )
            row = cur.fetchone()
        if not row:
            return None
        rec = MagicMock()
        rec.content_hash = row[0]
        rec.hash_prefix = row[1]
        return rec

    return _lookup


def _target_hash(provider, song_id):
    with provider.get_connection().cursor() as cur:
        cur.execute(
            "SELECT content_hash FROM recordings "
            "WHERE song_id = %s AND deleted_at IS NULL LIMIT 1",
            (song_id,),
        )
        row = cur.fetchone()
    return row[0] if row else None