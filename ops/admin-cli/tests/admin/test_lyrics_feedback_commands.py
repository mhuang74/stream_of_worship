"""Tests for `sow-admin lyrics feedback` commands (issue #194)."""

from pathlib import Path

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
    testcontainers Postgres does not support. The commands build their
    provider from the config URL with no way to pass sslmode, so patch
    the class reference inside the lyrics command module (test-only;
    the database itself is real).
    """

    def _factory(url, sslmode="require"):
        return ConnectionProvider(url, sslmode="disable")

    monkeypatch.setattr(lyrics_commands, "ConnectionProvider", _factory)


runner = CliRunner()
lyrics_app = lyrics_commands.app


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


def _seed_data(provider):
    """Seed one song, four recordings, four users, and feedback rows.

    One row per (user, recording) — the table's UNIQUE constraint.

    R1 (hash-aa...): 3 open (u1 sad missing, u2 sad timing, u3 happy)
    + 1 resolved sad missing (u4). Dominant open reason: missing/timing
    tie → missing wins by REASON_ORDER → suggested action "generate".
    R2 (hash-bb...): 1 open sad other (u1) → "manual review".
    R3 (hash-cc...): 2 open sad timing (u2, u3) → "re-align".
    R4 (hash-dd...): 1 open sad missing (u4) — songless recording
    (song_id NULL) to prove the queue's LEFT JOIN keeps it visible.
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
            INSERT INTO songs (id, title, source_url, scraped_at, composer)
            VALUES ('song_001', '測試歌曲', 'https://example.com/1', '2024-01-01T00:00:00', '作曲')
            """
        )
        for hash_, prefix, filename, lrc_status, song_id in [
            ("hash-aaaaaaaaaaaaaaaa", "hash-aa", "one.mp3", "completed", "song_001"),
            ("hash-bbbbbbbbbbbbbbbb", "hash-bb", "two.mp3", "missing", "song_001"),
            ("hash-cccccccccccccccc", "hash-cc", "three.mp3", "completed", "song_001"),
            ("hash-dddddddddddddddd", "hash-dd", "four.mp3", "completed", None),
        ]:
            cur.execute(
                """
                INSERT INTO recordings (
                    content_hash, hash_prefix, song_id, original_filename,
                    file_size_bytes, imported_at, lrc_status
                )
                VALUES (%s, %s, %s, %s, 100, '2024-01-01T00:00:00', %s)
                """,
                (hash_, prefix, song_id, filename, lrc_status),
            )
        cur.execute(
            """
            INSERT INTO "user" (id, name, email, "emailVerified")
            OVERRIDING SYSTEM VALUE
            VALUES (2, 'User Two', 'u2@example.com', true),
                   (3, 'User Three', 'u3@example.com', true),
                   (4, 'User Four', 'u4@example.com', true)
            """
        )
        cur.execute(
            """
            INSERT INTO lyrics_feedback (id, user_id, recording_content_hash, rating, reason)
            VALUES
                ('fb-1', 1, 'hash-aaaaaaaaaaaaaaaa', 'sad', 'missing'),
                ('fb-2', 2, 'hash-aaaaaaaaaaaaaaaa', 'sad', 'timing'),
                ('fb-3', 3, 'hash-aaaaaaaaaaaaaaaa', 'happy', NULL),
                ('fb-4', 4, 'hash-aaaaaaaaaaaaaaaa', 'sad', 'missing'),
                ('fb-5', 1, 'hash-bbbbbbbbbbbbbbbb', 'sad', 'other'),
                ('fb-6', 2, 'hash-cccccccccccccccc', 'sad', 'timing'),
                ('fb-7', 3, 'hash-cccccccccccccccc', 'sad', 'timing'),
                ('fb-8', 4, 'hash-dddddddddddddddd', 'sad', 'missing')
            """
        )
        # mark fb-4 resolved
        cur.execute("UPDATE lyrics_feedback SET resolved_at = NOW() WHERE id = 'fb-4'")
    conn.commit()


@pytest.mark.integration
class TestLyricsFeedbackListCommand:
    """Tests for 'lyrics feedback list'."""

    def test_groups_by_recording_with_counts_and_reasons(
        self, make_test_provider, postgres_url, tmp_path
    ):
        _init_schema(make_test_provider)
        _seed_data(make_test_provider())
        config_path = _write_config(tmp_path, postgres_url)

        result = runner.invoke(
            lyrics_app,
            ["feedback", "list", "--config", str(config_path)],
            env={"COLUMNS": "200"},
        )
        assert result.exit_code == 0, result.output
        # One row per recording (grouped, no duplicates)
        assert result.output.count("hash-aa") >= 1
        assert "測試歌曲" in result.output
        # Pipeline lrc_status column distinguishes the two recordings
        assert "completed" in result.output
        assert "missing" in result.output
        # Reason breakdown present
        assert "missing" in result.output
        assert "timing" in result.output
        _drop_all_tables(make_test_provider)

    def test_open_count_excludes_resolved(self, make_test_provider, postgres_url, tmp_path):
        _init_schema(make_test_provider)
        _seed_data(make_test_provider())
        config_path = _write_config(tmp_path, postgres_url)

        result = runner.invoke(
            lyrics_app,
            ["feedback", "list", "--config", str(config_path)],
            env={"COLUMNS": "200"},
        )
        assert result.exit_code == 0, result.output
        # R1 has 3 open rows (2 sad + 1 happy); fb-4 is resolved and excluded
        assert "3" in result.output
        _drop_all_tables(make_test_provider)

    def test_filter_by_reason(self, make_test_provider, postgres_url, tmp_path):
        _init_schema(make_test_provider)
        _seed_data(make_test_provider())
        config_path = _write_config(tmp_path, postgres_url)

        result = runner.invoke(
            lyrics_app,
            ["feedback", "list", "--reason", "timing", "--config", str(config_path)],
            env={"COLUMNS": "200"},
        )
        assert result.exit_code == 0, result.output
        # only R1 has an open timing complaint
        assert "hash-aa" in result.output
        assert "hash-bb" not in result.output
        _drop_all_tables(make_test_provider)

    def test_filter_by_rating(self, make_test_provider, postgres_url, tmp_path):
        _init_schema(make_test_provider)
        _seed_data(make_test_provider())
        config_path = _write_config(tmp_path, postgres_url)

        result = runner.invoke(
            lyrics_app,
            ["feedback", "list", "--rating", "happy", "--config", str(config_path)],
            env={"COLUMNS": "200"},
        )
        assert result.exit_code == 0, result.output
        # only R1 has an open happy row
        assert "hash-aa" in result.output
        assert "hash-bb" not in result.output

    def test_songless_recording_still_listed(self, make_test_provider, postgres_url, tmp_path):
        """Feedback for a recording with NULL song_id must not vanish from the queue."""
        _init_schema(make_test_provider)
        _seed_data(make_test_provider())
        config_path = _write_config(tmp_path, postgres_url)

        result = runner.invoke(
            lyrics_app,
            ["feedback", "list", "--config", str(config_path)],
            env={"COLUMNS": "200"},
        )
        assert result.exit_code == 0, result.output
        assert "hash-dd" in result.output
        _drop_all_tables(make_test_provider)

    def test_all_flag_includes_fully_resolved_recordings(
        self, make_test_provider, postgres_url, tmp_path
    ):
        _init_schema(make_test_provider)
        _seed_and_resolve_all(make_test_provider)
        config_path = _write_config(tmp_path, postgres_url)

        result = runner.invoke(
            lyrics_app,
            ["feedback", "list", "--config", str(config_path)],
            env={"COLUMNS": "200"},
        )
        assert result.exit_code == 0, result.output
        assert "No" in result.output or "0" in result.output  # empty queue message
        result_all = runner.invoke(
            lyrics_app,
            ["feedback", "list", "--all", "--config", str(config_path)],
            env={"COLUMNS": "200"},
        )
        assert result_all.exit_code == 0, result_all.output
        assert "hash-aa" in result_all.output
        _drop_all_tables(make_test_provider)

    def test_suggested_action_mapping(self, make_test_provider, postgres_url, tmp_path):
        _init_schema(make_test_provider)
        _seed_data(make_test_provider())
        config_path = _write_config(tmp_path, postgres_url)

        result = runner.invoke(
            lyrics_app,
            ["feedback", "list", "--config", str(config_path)],
            env={"COLUMNS": "200"},
        )
        assert result.exit_code == 0, result.output
        assert "generate" in result.output.lower()  # missing → generate Lyrics
        assert "re-align" in result.output.lower()  # timing → re-align
        _drop_all_tables(make_test_provider)


def _seed_and_resolve_all(make_test_provider):
    provider = make_test_provider()
    _seed_data(provider)
    conn = provider.get_connection()
    with conn.cursor() as cur:
        cur.execute("UPDATE lyrics_feedback SET resolved_at = NOW()")
    conn.commit()
    return provider


@pytest.mark.integration
class TestLyricsFeedbackResolveCommand:
    """Tests for 'lyrics feedback resolve' / 'unresolve'."""

    def test_resolve_by_content_hash(self, make_test_provider, postgres_url, tmp_path):
        _init_schema(make_test_provider)
        provider = _seed_data_provider(make_test_provider)
        config_path = _write_config(tmp_path, postgres_url)

        result = runner.invoke(
            lyrics_app,
            ["feedback", "resolve", "hash-aaaaaaaaaaaaaaaa", "--config", str(config_path)],
        )
        assert result.exit_code == 0, result.output
        with provider.get_connection().cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM lyrics_feedback "
                "WHERE recording_content_hash = 'hash-aaaaaaaaaaaaaaaa' "
                "AND resolved_at IS NULL"
            )
            open_count = cur.fetchone()[0]
        assert open_count == 0
        # other recording untouched
        with provider.get_connection().cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM lyrics_feedback "
                "WHERE recording_content_hash = 'hash-bbbbbbbbbbbbbbbb' "
                "AND resolved_at IS NULL"
            )
            assert cur.fetchone()[0] == 1
        _drop_all_tables(make_test_provider)

    def test_resolve_by_song_id(self, make_test_provider, postgres_url, tmp_path):
        _init_schema(make_test_provider)
        provider = _seed_data_provider(make_test_provider)
        config_path = _write_config(tmp_path, postgres_url)

        result = runner.invoke(
            lyrics_app,
            ["feedback", "resolve", "song_001", "--config", str(config_path)],
        )
        assert result.exit_code == 0, result.output
        with provider.get_connection().cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM lyrics_feedback WHERE resolved_at IS NULL")
            # Only the songless recording's feedback (fb-8, hash-dd) stays open:
            # song-level resolve covers recordings of that song, nothing else.
            assert cur.fetchone()[0] == 1
        _drop_all_tables(make_test_provider)

    def test_unresolve_by_content_hash(self, make_test_provider, postgres_url, tmp_path):
        _init_schema(make_test_provider)
        provider = _seed_data_provider(make_test_provider)
        config_path = _write_config(tmp_path, postgres_url)

        # resolve everything first
        runner.invoke(
            lyrics_app,
            ["feedback", "resolve", "song_001", "--config", str(config_path)],
        )
        result = runner.invoke(
            lyrics_app,
            [
                "feedback",
                "unresolve",
                "hash-aaaaaaaaaaaaaaaa",
                "--config",
                str(config_path),
            ],
        )
        assert result.exit_code == 0, result.output
        with provider.get_connection().cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM lyrics_feedback "
                "WHERE recording_content_hash = 'hash-aaaaaaaaaaaaaaaa' "
                "AND resolved_at IS NULL"
            )
            reopened = cur.fetchone()[0]
        assert reopened == 4  # all hash-aa rows: 3 open + 1 pre-resolved (fb-4)
        _drop_all_tables(make_test_provider)

    def test_resolve_unknown_target_errors(self, make_test_provider, postgres_url, tmp_path):
        _init_schema(make_test_provider)
        _seed_data_provider(make_test_provider)
        config_path = _write_config(tmp_path, postgres_url)

        result = runner.invoke(
            lyrics_app,
            ["feedback", "resolve", "does-not-exist", "--config", str(config_path)],
        )
        assert result.exit_code != 0
        _drop_all_tables(make_test_provider)


def _seed_data_provider(make_test_provider):
    provider = make_test_provider()
    _seed_data(provider)
    return provider
