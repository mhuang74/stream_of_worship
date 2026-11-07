"""Tests for `sow-admin lyrics review --songids-file`.

Covers the read-only review walk (editor session patched out):
file order, the `skipped-feedback` / `skipped-songless` skips, published
recordings being reviewable, the pause gate aborting the whole run, the
implicit auto-advance, and the empty-file / required-option edges.

The TUI itself (blocked edit keys, no autosave) is covered by
``tests/admin/services/test_lrc_editor_screen.py``.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from stream_of_worship.admin.commands import catalog as catalog_commands
from stream_of_worship.admin.commands import lyrics as lyrics_commands
from stream_of_worship.admin.db.client import DatabaseClient
from stream_of_worship.db.connection import ConnectionProvider
from stream_of_worship.db.postgres_schema import ALL_SCHEMA_STATEMENTS

runner = CliRunner()
lyrics_app = lyrics_commands.app


@pytest.fixture(autouse=True)
def _disable_ssl(monkeypatch):
    """Force sslmode=disable for the CLI's connections (testcontainers).

    Both the catalog module's ``ConnectionProvider`` reference (used by
    ``get_db_client``) and the lyrics module's own reference (feedback SQL
    and the review walk's provider) must be patched.
    """

    def _factory(url, sslmode="require"):
        return ConnectionProvider(url, sslmode="disable")

    monkeypatch.setattr(lyrics_commands, "ConnectionProvider", _factory)
    monkeypatch.setattr(catalog_commands, "ConnectionProvider", _factory)


@pytest.fixture(autouse=True)
def _stub_r2(monkeypatch):
    """Replace R2Client with an inert stub (review never touches storage)."""

    class _StubR2:
        audio_exists = staticmethod(lambda hash_prefix: False)
        download_audio = staticmethod(lambda hash_prefix, path: None)
        get_lrc_identity = staticmethod(lambda hash_prefix: None)
        download_lrc_content = staticmethod(lambda hash_prefix: None)

    monkeypatch.setattr(lyrics_commands, "R2Client", lambda **kwargs: _StubR2())


@pytest.fixture
def patched_editor(monkeypatch):
    """Patch _run_editor_session, recording (hash_prefix, read_only) per call.

    A test can append ``"no_audio"`` to ``outcomes`` to script the
    no-audio skip; with nothing scripted the session opens normally.
    """
    record = SimpleNamespace(calls=[], outcomes=[])

    def _fake_session(
        db_client,
        cache_dir,
        r2_client,
        song,
        recording,
        feedback_user_id=None,
        analysis_client=None,
        read_only=False,
    ):
        record.calls.append((recording.hash_prefix, read_only))
        scripted = record.outcomes.pop(0) if record.outcomes else None
        if scripted == "no_audio":
            return lyrics_commands.EditorSessionOutcome(
                no_audio=True, upload_attempted=False, upload_result=None
            )
        return lyrics_commands.EditorSessionOutcome(
            no_audio=False, upload_attempted=False, upload_result=None
        )

    monkeypatch.setattr(lyrics_commands, "_run_editor_session", _fake_session)
    return record


def _drop_all_tables(make_test_provider):
    """Drop all tables for cleanup."""
    try:
        cleanup_provider = make_test_provider()
        with cleanup_provider.get_connection().cursor() as cur:
            cur.execute(
                """
                DROP TABLE IF EXISTS lyrics_feedback CASCADE;
                DROP TABLE IF EXISTS user_favorite_songs CASCADE;
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


def _init_schema(make_test_provider):
    _drop_all_tables(make_test_provider)
    provider = make_test_provider()
    with provider.get_connection().cursor() as cur:
        for stmt in ALL_SCHEMA_STATEMENTS:
            cur.execute(stmt)
    provider.get_connection().commit()
    return provider


def _write_config(tmp_path, postgres_url):
    config_path = Path(tmp_path) / "config.toml"
    config_path.write_text(f'[database]\nurl = "{postgres_url}"\n')
    return config_path


def _seed_review_pool(provider):
    """Three reviewable songs plus one open sad row.

    song_a: review, hash-aa, open sad (fb1) -> skipped-feedback.
    song_b: review, hash-bb, no feedback     -> reviewed.
    song_c: PUBLISHED, hash-cc, no feedback  -> reviewed (no published guard).
    """
    HA, HB, HC = "ha" * 16, "hb" * 16, "hc" * 16
    conn = provider.get_connection()
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO "user" (id, name, email, "emailVerified")
            OVERRIDING SYSTEM VALUE
            VALUES (1,'U1','u1@x.com',true),(2,'U2','u2@x.com',true)
            """
        )
        cur.execute(
            """
            INSERT INTO songs (id, title, source_url, scraped_at,
                album_name, album_series, created_at)
            VALUES
             ('song_a','Song A','https://x/1','2024-01-01T00:00:00',
              'Album X','S15','2024-01-01T00:00:00Z'),
             ('song_b','Song B','https://x/2','2024-01-01T00:00:00',
              'Album Y','S16','2024-01-01T00:00:00Z'),
             ('song_c','Song C','https://x/3','2024-01-01T00:00:00',
              'Album X','S15','2024-02-01T00:00:00Z')
            """
        )
        cur.execute(
            """
            INSERT INTO recordings (content_hash, hash_prefix, song_id,
                original_filename, file_size_bytes, imported_at,
                visibility_status, created_at)
            VALUES
             (%s,'hash-aa','song_a','a.mp3',1,'2024','review','2024-01-01T00:00:00Z'),
             (%s,'hash-bb','song_b','b.mp3',1,'2024','review','2024-01-01T00:00:00Z'),
             (%s,'hash-cc','song_c','c.mp3',1,'2024','published','2024-02-01T00:00:00Z')
            """,
            (HA, HB, HC),
        )
        cur.execute(
            """
            INSERT INTO lyrics_feedback (id, user_id, recording_content_hash,
                rating, reason, created_at, updated_at)
            VALUES
             ('fb1',1,%s,'sad','timing','2024-01-01T00:00:00Z','2024-01-01T00:00:00Z')
            """,
            (HA,),
        )
    conn.commit()


def _write_ids(tmp_path, lines) -> Path:
    path = Path(tmp_path) / "ids.txt"
    path.write_text("".join(f"{line}\n" for line in lines))
    return path


@pytest.mark.integration
class TestLyricsReviewCommand:
    def _invoke(self, config_path, ids_path, input_text):
        return runner.invoke(
            lyrics_app,
            ["review", "--songids-file", str(ids_path), "--config", str(config_path)],
            input=input_text,
            env={"COLUMNS": "200"},
        )

    def test_walks_file_order_skips_feedback_and_songless(
        self, make_test_provider, postgres_url, tmp_path, patched_editor
    ):
        provider = _init_schema(make_test_provider)
        _seed_review_pool(provider)
        config_path = _write_config(tmp_path, postgres_url)
        ids_path = _write_ids(tmp_path, ["song_a", "bogus_song", "song_c", "song_b"])

        result = self._invoke(config_path, ids_path, "\n\n")

        assert result.exit_code == 0, result.output
        assert patched_editor.calls == [("hash-cc", True), ("hash-bb", True)]
        assert "skipped-feedback song_a: 1 unresolved feedback row(s) (👍0 👎1)" in result.output
        assert "resolve with 'sow-admin lyrics feedback resolve song_a'" in result.output
        assert "skipped-songless bogus_song" in result.output
        # Published recording is reviewed: no visibility guard in review mode.
        assert "Review song 3/4:" in result.output
        assert "Review song 4/4:" in result.output
        assert "skipped-published" not in result.output
        assert "Launching LRC review" not in result.output  # session patched out

    def test_auto_advance_needs_only_pause_gates(
        self, make_test_provider, postgres_url, tmp_path, patched_editor
    ):
        provider = _init_schema(make_test_provider)
        _seed_review_pool(provider)
        config_path = _write_config(tmp_path, postgres_url)
        ids_path = _write_ids(tmp_path, ["song_c", "song_b"])

        result = self._invoke(config_path, ids_path, "\n\n")

        assert result.exit_code == 0, result.output
        assert patched_editor.calls == [("hash-cc", True), ("hash-bb", True)]
        assert "Continue to next song" not in result.output
        assert "Continue to next review song" not in result.output

    def test_pause_abort_stops_run(
        self, make_test_provider, postgres_url, tmp_path, patched_editor
    ):
        provider = _init_schema(make_test_provider)
        _seed_review_pool(provider)
        config_path = _write_config(tmp_path, postgres_url)
        ids_path = _write_ids(tmp_path, ["song_c", "song_b"])

        result = self._invoke(config_path, ids_path, "x\n")

        assert result.exit_code == 0, result.output
        assert "Aborted; remaining songs in the file were not processed." in result.output
        assert patched_editor.calls == []
        assert "Review song 2/2:" not in result.output

    def test_resolving_feedback_unblocks_song(
        self, make_test_provider, postgres_url, tmp_path, patched_editor
    ):
        provider = _init_schema(make_test_provider)
        _seed_review_pool(provider)
        config_path = _write_config(tmp_path, postgres_url)
        ids_path = _write_ids(tmp_path, ["song_a"])

        first = self._invoke(config_path, ids_path, "\n")
        assert first.exit_code == 0, first.output
        assert patched_editor.calls == []
        assert "skipped-feedback song_a" in first.output

        DatabaseClient(make_test_provider()).resolve_recording_sad_feedback("ha" * 16)

        second = self._invoke(config_path, ids_path, "\n")
        assert second.exit_code == 0, second.output
        assert patched_editor.calls == [("hash-aa", True)]
        assert "skipped-feedback song_a" not in second.output

    def test_no_audio_skip_message(
        self, make_test_provider, postgres_url, tmp_path, patched_editor
    ):
        provider = _init_schema(make_test_provider)
        _seed_review_pool(provider)
        config_path = _write_config(tmp_path, postgres_url)
        ids_path = _write_ids(tmp_path, ["song_c"])
        patched_editor.outcomes.append("no_audio")

        result = self._invoke(config_path, ids_path, "\n")

        assert result.exit_code == 0, result.output
        assert patched_editor.calls == [("hash-cc", True)]
        assert "Skipping song_c (Song C): no audio available in object storage." in result.output

    def test_empty_file_exits_zero(self, tmp_path):
        config_path = _write_config(tmp_path, "postgresql://unused/db")
        ids_path = _write_ids(tmp_path, [""])

        result = self._invoke(config_path, ids_path, "")

        assert result.exit_code == 0, result.output
        assert "No song IDs found in" in result.output

    def test_songids_file_option_is_required(self, tmp_path):
        config_path = _write_config(tmp_path, "postgresql://unused/db")

        result = runner.invoke(
            lyrics_app, ["review", "--config", str(config_path)], env={"COLUMNS": "200"}
        )

        assert result.exit_code == 2
