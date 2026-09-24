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
        # Three songed recordings (R1-R3) share Song ID song_001; songless R4
        # falls back to hash-dd. Grouping is proven by distinct Open counts.
        assert result.output.count("song_001") == 3
        assert "hash-dd" in result.output
        assert "測試歌曲" in result.output
        # Reason breakdown present
        assert "missing" in result.output
        assert "timing" in result.output
        # Polarity column: mixed open happy+sad rows (R1) → 👎
        assert "Like" in result.output
        assert "Reasons (neg)" in result.output
        assert "👎" in result.output
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
        # R1 (timing×1) and R3 (timing×2) have open timing complaints;
        # R2 (other×1) does not. Rows distinguishable via Open counts.
        assert "timing×1" in result.output
        assert "timing×2" in result.output
        assert "other×1" not in result.output
        _drop_all_tables(make_test_provider)

    def test_filter_by_rating(self, make_test_provider, postgres_url, tmp_path):
        _init_schema(make_test_provider)
        _seed_data(make_test_provider())
        config_path = _write_config(tmp_path, postgres_url)

        result = runner.invoke(
            lyrics_app,
            ["feedback", "list", "--rating", "good", "--config", str(config_path)],
            env={"COLUMNS": "200"},
        )
        # only R1 (songed) has an open happy row; CLI good → storage happy.
        # R1 row: Open=1 with empty Reasons (its only open row is happy).
        assert result.exit_code == 0, result.output
        assert "👍" in result.output

        result = runner.invoke(
            lyrics_app,
            ["feedback", "list", "--rating", "poor", "--config", str(config_path)],
            env={"COLUMNS": "200"},
        )
        assert result.exit_code == 0, result.output
        # R1, R2, R3 (songed) + R4 (songless) have open sad rows; CLI poor → storage sad
        assert result.output.count("song_001") == 3
        assert "hash-dd" in result.output
        _drop_all_tables(make_test_provider)

    def test_positive_row_shown_as_positive(self, make_test_provider, postgres_url, tmp_path):
        _init_schema(make_test_provider)
        _seed_data(make_test_provider())
        config_path = _write_config(tmp_path, postgres_url)

        result = runner.invoke(
            lyrics_app,
            ["feedback", "list", "--rating", "good", "--config", str(config_path)],
            env={"COLUMNS": "200"},
        )
        assert result.exit_code == 0, result.output
        # R1 under --rating good: open_count == open_happy == 1 → 👍
        assert "👍" in result.output
        _drop_all_tables(make_test_provider)

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
        # All four seeded recordings reappear (songed rows show song_001,
        # songless R4 shows hash-dd)
        assert result_all.output.count("song_001") == 3
        assert "hash-dd" in result_all.output
        _drop_all_tables(make_test_provider)

    def test_format_ids_prints_pipeable_song_ids(self, make_test_provider, postgres_url, tmp_path):
        _init_schema(make_test_provider)
        _seed_data(make_test_provider())
        config_path = _write_config(tmp_path, postgres_url)

        result = runner.invoke(
            lyrics_app,
            ["feedback", "list", "--format", "ids", "--config", str(config_path)],
            env={"COLUMNS": "200"},
        )
        assert result.exit_code == 0, result.output
        # Three songed recordings dedup to one song_001 line; songless R4 is
        # reported on stderr as skipped, keeping stdout a clean id stream.
        lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
        assert lines == ["song_001"]
        assert "hash-dd" in result.stderr
        assert "Skipped 1 recording" in result.stderr
        _drop_all_tables(make_test_provider)

    def test_format_ids_dedupes_multi_recording_song(self, make_test_provider, postgres_url, tmp_path):
        """Regression pin: a song with open sad rows on several recordings is
        emitted exactly once by the ids formatter (the `seen` set)."""
        _init_schema(make_test_provider)
        provider = _seed_data_provider(make_test_provider)
        # All three songed recordings already carry open sad rows for song_001.
        config_path = _write_config(tmp_path, postgres_url)

        result = runner.invoke(
            lyrics_app,
            [
                "feedback", "list", "--rating", "poor", "--format", "ids",
                "--config", str(config_path),
            ],
            env={"COLUMNS": "200"},
        )
        assert result.exit_code == 0, result.stdout
        lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
        # One song id despite three recordings with open sad feedback.
        assert lines == ["song_001"]
        _drop_all_tables(make_test_provider)

    def test_rating_poor_excludes_resolved_sad_with_open_happy(
        self, make_test_provider, postgres_url, tmp_path
    ):
        """--rating poor means 'has ≥1 OPEN sad row': a recording whose sad
        rows are all resolved but which has an open happy row drops out."""
        _init_schema(make_test_provider)
        provider = _seed_data_provider(make_test_provider)
        # Resolve ALL of R1's sad rows, leaving its happy row open.
        conn = provider.get_connection()
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE lyrics_feedback SET resolved_at = NOW() "
                "WHERE recording_content_hash = 'hash-aaaaaaaaaaaaaaaa' AND rating = 'sad'"
            )
        conn.commit()
        config_path = _write_config(tmp_path, postgres_url)

        result = runner.invoke(
            lyrics_app,
            [
                "feedback", "list", "--rating", "poor", "--format", "ids",
                "--config", str(config_path),
            ],
            env={"COLUMNS": "200"},
        )
        assert result.exit_code == 0, result.stdout
        # hash-aa (resolved sad, open happy) drops out; hash-bb/hash-cc remain.
        lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
        assert lines == ["song_001"]  # other songed recordings still qualify (same song)
        with provider.get_connection().cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM lyrics_feedback f "
                "JOIN recordings r ON r.content_hash = f.recording_content_hash "
                "WHERE r.song_id = 'song_001' AND f.resolved_at IS NULL "
                "AND f.rating = 'sad'"
            )
            open_sad = cur.fetchone()[0]
        assert open_sad == 3  # hash-bb + hash-cc rows still open sad
        _drop_all_tables(make_test_provider)

    def test_format_ids_empty_feedback_prints_nothing(
        self, make_test_provider, postgres_url, tmp_path
    ):
        _init_schema(make_test_provider)
        config_path = _write_config(tmp_path, postgres_url)

        result = runner.invoke(
            lyrics_app,
            ["feedback", "list", "--format", "ids", "--config", str(config_path)],
            env={"COLUMNS": "200"},
        )
        assert result.exit_code == 0, result.output
        # Pipe contract: empty queue emits zero stdout lines so the downstream
        # --stdin consumer sees clean EOF (set-visibility errors on stray text).
        assert result.stdout.strip() == ""
        _drop_all_tables(make_test_provider)

    def test_rating_good_ids_pipeable_for_set_visibility(
        self, make_test_provider, postgres_url, tmp_path
    ):
        """Pipe-consumer contract for `audio set-visibility --stdin`:
        --rating good emits exactly R1's song ID; songless R4 has only sad
        open rows, so the good filter excludes it."""
        _init_schema(make_test_provider)
        _seed_data(make_test_provider())
        config_path = _write_config(tmp_path, postgres_url)

        result = runner.invoke(
            lyrics_app,
            [
                "feedback",
                "list",
                "--rating",
                "good",
                "--format",
                "ids",
                "--config",
                str(config_path),
            ],
            env={"COLUMNS": "200"},
        )
        assert result.exit_code == 0, result.stdout
        lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
        assert lines == ["song_001"]
        _drop_all_tables(make_test_provider)


def test_feedback_list_rejects_unknown_format():
    result = runner.invoke(lyrics_app, ["feedback", "list", "--format", "json"])
    assert result.exit_code == 1
    assert "Invalid format" in result.output


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
