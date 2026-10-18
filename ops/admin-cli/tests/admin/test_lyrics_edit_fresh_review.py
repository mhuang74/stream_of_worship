"""Tests for `sow-admin lyrics edit --review-fresh` (never-reviewed sweep).

Covers:
- ``get_fresh_review_queue``: pool = review-visibility recordings with no
  ``lyrics_feedback`` row of any rating (open or resolved); per-song dedup
  picks the newest un-reviewed recording; ranking cascade = songset
  appearances → favorites → newest album → song id (no feedback tier).
- ``upsert_recording_feedback`` / ``delete_recording_feedback`` /
  ``resolve_recording_feedback`` against real Postgres.
- The external CLI behavior of `lyrics edit --review-fresh`: mutual
  exclusion, pick reason line, pause, exhaustion message, skipping songs
  with feedback, and post-upload offers — with the editor session patched
  out (the editor TUI itself is a separate seam).
"""

from pathlib import Path

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
    """Force sslmode=disable for the CLI's connections (testcontainers)."""

    def _factory(url, sslmode="require"):
        return ConnectionProvider(url, sslmode="disable")

    monkeypatch.setattr(lyrics_commands, "ConnectionProvider", _factory)
    monkeypatch.setattr(catalog_commands, "ConnectionProvider", _factory)


@pytest.fixture(autouse=True)
def _stub_r2(monkeypatch):
    """Replace R2Client with an inert stub (editor session is patched out)."""

    class _StubR2:
        audio_exists = staticmethod(lambda hash_prefix: False)
        download_audio = staticmethod(lambda hash_prefix, path: None)
        get_lrc_identity = staticmethod(lambda hash_prefix: None)
        download_lrc_content = staticmethod(lambda hash_prefix: None)

    monkeypatch.setattr(lyrics_commands, "R2Client", lambda **kwargs: _StubR2())


@pytest.fixture
def patched_editor(monkeypatch):
    """Patch _run_editor_session to return scripted outcomes.

    Same seam as test_lyrics_edit_review_queue.py; the fake signature
    includes the new trailing feedback_user_id parameter.
    """
    calls: list[tuple] = []
    outcomes: list[object] = []

    def _fake_session(db_client, cache_dir, r2_client, song, recording, feedback_user_id=None):
        calls.append((recording.hash_prefix, feedback_user_id))
        scripted = outcomes.pop(0) if outcomes else None
        if scripted is None:
            return lyrics_commands.EditorSessionOutcome(
                no_audio=True, upload_attempted=False, upload_result=None
            )
        if isinstance(scripted, lyrics_commands.EditorSessionOutcome):
            return scripted
        scripted.hash_prefix = recording.hash_prefix
        return lyrics_commands.EditorSessionOutcome(
            no_audio=False, upload_attempted=True, upload_result=scripted
        )

    monkeypatch.setattr(lyrics_commands, "_run_editor_session", _fake_session)
    return {"calls": calls, "outcomes": outcomes}


def _upload_outcome(success=True):
    from stream_of_worship.admin.editor.upload import UploadResult

    return UploadResult(success=success, r2_url="https://r2/x.lrc" if success else None)


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


def _write_config(tmp_path, postgres_url):
    config_path = Path(tmp_path) / "config.toml"
    config_path.write_text(f'[database]\nurl = "{postgres_url}"\n')
    return config_path


def _init_schema(make_test_provider):
    _drop_all_tables(make_test_provider)
    provider = make_test_provider()
    with provider.get_connection().cursor() as cur:
        for stmt in ALL_SCHEMA_STATEMENTS:
            cur.execute(stmt)
    provider.get_connection().commit()
    return provider


def _seed_users(provider, count: int = 3) -> None:
    """Insert ``count`` Better Auth users (ids 1..count)."""
    conn = provider.get_connection()
    with conn.cursor() as cur:
        rows = ",".join(f"({i},'U{i}','u{i}@x.com',true)" for i in range(1, count + 1))
        cur.execute(
            f"""
            INSERT INTO "user" (id, name, email, "emailVerified")
            OVERRIDING SYSTEM VALUE VALUES {rows}
            """
        )
    conn.commit()


def _seed_one_song(provider, song_id: str = "song_x", *hashes: str) -> None:
    """Insert one song plus one review recording per hash (2-uple prefix)."""
    conn = provider.get_connection()
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO songs (id, title, source_url, scraped_at)
            VALUES (%s,'Song X','https://x/0','2024-01-01T00:00:00')
            """,
            (song_id,),
        )
        for i, (full, prefix) in enumerate(hashes):
            cur.execute(
                """
                INSERT INTO recordings (content_hash, hash_prefix, song_id,
                    original_filename, file_size_bytes, imported_at,
                    visibility_status, created_at)
                VALUES (%s,%s,%s,'f.mp3',1,'2024','review',%s)
                """,
                (full, prefix, song_id, f"2024-01-0{i + 1}T00:00:00Z"),
            )
    conn.commit()


def _seed_fresh_pool(provider):
    """Seed the never-reviewed sweep scenario.

    Review recordings:
    - song_a: hash-aa open sad (u1) + hash-ar resolved happy (u2) → all
      song_a recordings reviewed → song_a drops out entirely.
    - song_b: hash-bb open happy (u3) → reviewed → drops out.
    - song_c: two review recordings — NEWER hash-cc has feedback (fb5,
      resolved happy), OLDER hash-cz has none → the older un-reviewed
      recording must be picked (newer-but-reviewed bypassed).
    - song_d: hash-dd review, no feedback at all → fresh.
    Non-pool: song_e hash-ee published with open sad → never in pool.
    Ranking evidence: song_a 3 appearances; song_c 2; song_b 1 favorite;
    song_d newest album (Album Z), no songsets/favorites.
    Expected queue: song_c (2 app), song_d (0 app, newest album) —
    song_a/song_b reviewed; ties broken deterministically.
    """
    HA = "ha" * 16   # hash-aa (song_a, open sad)
    HAR = "har" * 11  # hash-ar (song_a, resolved happy); 33 chars
    HB = "hb" * 16   # hash-bb (song_b, open happy)
    HC = "hc" * 16   # hash-cc (song_c, resolved sad fb5)
    HCZ = "hcz" * 11  # hash-cz (song_c, un-reviewed); 33 chars
    HD = "hd" * 16   # hash-dd (song_d, fresh)
    HE = "he" * 16   # hash-ee (song_e, published, open sad)
    conn = provider.get_connection()
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO "user" (id, name, email, "emailVerified")
            OVERRIDING SYSTEM VALUE
            VALUES (1,'U1','u1@x.com',true),(2,'U2','u2@x.com',true),
                   (3,'U3','u3@x.com',true)
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
              'Album Y','S16','2025-06-01T00:00:00Z'),
             ('song_c','Song C','https://x/3','2024-01-01T00:00:00',
              'Album X','S15','2024-02-01T00:00:00Z'),
             ('song_d','Song D','https://x/4','2024-01-01T00:00:00',
              'Album Z','S17','2026-01-01T00:00:00Z'),
             ('song_e','Song E','https://x/5','2024-01-01T00:00:00',
              'Album E','S18','2024-05-01T00:00:00Z')
            """
        )
        cur.execute(
            """
            INSERT INTO recordings (content_hash, hash_prefix, song_id,
                original_filename, file_size_bytes, imported_at,
                visibility_status, created_at)
            VALUES
             (%s,'hash-aa','song_a','a.mp3',1,'2024','review','2024-01-01T00:00:00Z'),
             (%s,'hash-ar','song_a','r.mp3',1,'2024','review','2024-06-01T00:00:00Z'),
             (%s,'hash-bb','song_b','b.mp3',1,'2024','review','2024-01-01T00:00:00Z'),
             (%s,'hash-cc','song_c','c.mp3',1,'2024','review','2024-04-01T00:00:00Z'),
             (%s,'hash-cz','song_c','z.mp3',1,'2024','review','2024-02-01T00:00:00Z'),
             (%s,'hash-dd','song_d','d.mp3',1,'2024','review','2024-01-01T00:00:00Z'),
             (%s,'hash-ee','song_e','e.mp3',1,'2024','published','2024-01-01T00:00:00Z')
            """,
            (HA, HAR, HB, HC, HCZ, HD, HE),
        )
        cur.execute(
            """
            INSERT INTO lyrics_feedback (id, user_id, recording_content_hash,
                rating, reason, created_at, updated_at, resolved_at)
            VALUES
             ('fb1',1,%s,'sad','timing','2024-03-01T00:00:00Z','2024-03-01T00:00:00Z',NULL),
             ('fb2',2,%s,'happy',NULL,'2024-07-01T00:00:00Z','2024-07-01T00:00:00Z',
              '2024-08-01T00:00:00Z'),
             ('fb3',3,%s,'happy',NULL,'2024-03-01T00:00:00Z','2024-03-01T00:00:00Z',NULL),
             ('fb4',1,%s,'happy',NULL,'2024-05-01T00:00:00Z','2024-05-01T00:00:00Z',
              '2024-05-02T00:00:00Z'),
             ('fb5',2,%s,'sad','other','2024-05-01T00:00:00Z','2024-05-01T00:00:00Z',NULL)
            """,
            (HA, HAR, HB, HC, HE),
        )
        cur.execute("INSERT INTO songsets (id, user_id, name) VALUES ('ss1',1,'S1'),('ss2',1,'S2')")
        cur.execute(
            """
            INSERT INTO songset_items (id, songset_id, song_id, position) VALUES
             ('it1','ss1','song_a',1),('it2','ss1','song_a',2),('it3','ss2','song_a',3),
             ('it4','ss1','song_c',4),('it5','ss2','song_c',5)
            """
        )
        cur.execute("INSERT INTO user_favorite_songs (user_id, song_id) VALUES (1,'song_b')")
    conn.commit()


@pytest.mark.integration
class TestGetFreshReviewQueue:
    """Pool/dedup/ranking SQL against real Postgres."""

    def test_excludes_any_feedback_and_dedups_to_newest_unreviewed(
        self, make_test_provider, postgres_url
    ):
        provider = _init_schema(make_test_provider)
        _seed_fresh_pool(provider)
        client = DatabaseClient(provider)

        queue = client.get_fresh_review_queue()
        by_song = {c.song_id: c for c in queue}
        # Reviewed songs drop out entirely: open sad (hash-aa), resolved
        # happy (hash-ar), open happy (hash-bb) all disqualify.
        assert "song_a" not in by_song
        assert "song_b" not in by_song
        # song_c: newest recording (hash-cc) has RESOLVED feedback; the
        # older un-reviewed recording (hash-cz) is the edit target.
        assert by_song["song_c"].hash_prefix == "hash-cz"
        # song_d is fresh.
        assert by_song["song_d"].hash_prefix == "hash-dd"
        # Published recording with feedback never enters the pool.
        assert all(c.hash_prefix != "hash-ee" for c in queue)

    def test_ranking_cascade_appearances_favorites_album_songid(
        self, make_test_provider, postgres_url
    ):
        provider = _init_schema(make_test_provider)
        _seed_fresh_pool(provider)
        client = DatabaseClient(provider)

        queue = client.get_fresh_review_queue()
        # song_c: 2 appearances → rank 1; song_d: 0 appearances but newest
        # album (2026) → rank 2. No feedback tier exists in this cascade.
        assert [c.song_id for c in queue] == ["song_c", "song_d"]
        assert queue[0].songset_appearances == 2
        assert queue[0].favorite_count == 0
        assert queue[1].songset_appearances == 0
        assert queue[1].favorite_count == 0
        # Fresh candidates carry no feedback data by construction.
        assert queue[0].open_sad_count == 0
        assert queue[0].latest_open_sad_at is None

    def test_favorite_count_outranks_album_recency(self, make_test_provider, postgres_url):
        provider = _init_schema(make_test_provider)
        _seed_fresh_pool(provider)
        conn = provider.get_connection()
        with conn.cursor() as cur:
            # song_d gets a favorite: favorites beat album recency.
            cur.execute("INSERT INTO user_favorite_songs (user_id, song_id) VALUES (2,'song_d')")
        conn.commit()
        client = DatabaseClient(provider)

        queue = client.get_fresh_review_queue()
        assert [c.song_id for c in queue] == ["song_c", "song_d"]

    def test_empty_pool_when_everything_reviewed(self, make_test_provider, postgres_url):
        provider = _init_schema(make_test_provider)
        _seed_fresh_pool(provider)
        client = DatabaseClient(provider)
        # Any rating marks a recording reviewed: the two fresh recordings
        # (hash-cz, hash-dd) gain a happy row → pool empties.
        client.upsert_recording_feedback("hcz" * 11, 1, "happy", None, None)
        client.upsert_recording_feedback("hd" * 16, 1, "happy", None, None)
        assert client.get_fresh_review_queue() == []


@pytest.mark.integration
class TestUpsertRecordingFeedback:
    def test_insert_then_update_same_row(self, make_test_provider, postgres_url):
        provider = _init_schema(make_test_provider)
        _seed_users(provider, 1)
        _seed_one_song(provider, "song_x", ("ha" * 16, "hash-aa"))
        client = DatabaseClient(provider)

        client.upsert_recording_feedback("ha" * 16, 1, "sad", "timing", "bridge off")
        client.upsert_recording_feedback("ha" * 16, 1, "happy", None, None)

        rows = client.get_recording_feedback_rows("ha" * 16)
        assert len(rows) == 1  # UNIQUE (user, recording): updated, not duplicated
        assert rows[0].rating == "happy"
        assert rows[0].reason is None  # happy clears reason
        assert rows[0].note is None
        assert rows[0].email == "u1@x.com"
        assert rows[0].resolved_at is None  # upsert never touches resolved_at

    def test_second_user_has_own_row(self, make_test_provider, postgres_url):
        provider = _init_schema(make_test_provider)
        _seed_users(provider, 2)
        _seed_one_song(provider, "song_x", ("ha" * 16, "hash-aa"))
        client = DatabaseClient(provider)

        client.upsert_recording_feedback("ha" * 16, 1, "sad", "timing", None)
        client.upsert_recording_feedback("ha" * 16, 2, "sad", "missing", "no lyrics")

        rows = client.get_recording_feedback_rows("ha" * 16)
        assert len(rows) == 2
        emails = {r.email: r.reason for r in rows}
        assert emails == {"u1@x.com": "timing", "u2@x.com": "missing"}


@pytest.mark.integration
class TestDeleteRecordingFeedback:
    def test_deletes_only_own_row_and_reports_absence(self, make_test_provider, postgres_url):
        provider = _init_schema(make_test_provider)
        _seed_users(provider, 2)
        _seed_one_song(provider, "song_x", ("ha" * 16, "hash-aa"))
        client = DatabaseClient(provider)

        client.upsert_recording_feedback("ha" * 16, 1, "sad", "timing", None)
        client.upsert_recording_feedback("ha" * 16, 2, "happy", None, None)

        assert client.delete_recording_feedback("ha" * 16, 1) is True
        rows = client.get_recording_feedback_rows("ha" * 16)
        assert [r.email for r in rows] == ["u2@x.com"]
        # Idempotent: nothing left for user 1.
        assert client.delete_recording_feedback("ha" * 16, 1) is False

    def test_deleted_recording_returns_to_fresh_queue(self, make_test_provider, postgres_url):
        provider = _init_schema(make_test_provider)
        _seed_fresh_pool(provider)
        client = DatabaseClient(provider)

        # Mark song_d's recording reviewed → drops out.
        client.upsert_recording_feedback("hd" * 16, 1, "happy", None, None)
        assert [c.song_id for c in client.get_fresh_review_queue()] == ["song_c"]

        # Delete the row → recording returns to the sweep.
        assert client.delete_recording_feedback("hd" * 16, 1) is True
        assert [c.song_id for c in client.get_fresh_review_queue()] == ["song_c", "song_d"]


@pytest.mark.integration
class TestResolveRecordingFeedback:
    def test_resolves_open_rows_of_any_rating(self, make_test_provider, postgres_url):
        provider = _init_schema(make_test_provider)
        _seed_users(provider, 2)
        _seed_one_song(provider, "song_x", ("ha" * 16, "hash-aa"))
        client = DatabaseClient(provider)

        client.upsert_recording_feedback("ha" * 16, 1, "happy", None, None)
        client.upsert_recording_feedback("ha" * 16, 2, "sad", "timing", None)

        resolved = client.resolve_recording_feedback("ha" * 16)
        assert resolved == 2
        rows = client.get_recording_feedback_rows("ha" * 16)
        assert all(r.resolved_at is not None for r in rows)

        # Idempotent: no open rows left.
        assert client.resolve_recording_feedback("ha" * 16) == 0

    def test_leaves_other_recordings_rows_open(self, make_test_provider, postgres_url):
        provider = _init_schema(make_test_provider)
        _seed_users(provider, 1)
        _seed_one_song(provider, "song_x", ("ha" * 16, "hash-aa"), ("hb" * 16, "hash-bb"))
        client = DatabaseClient(provider)

        client.upsert_recording_feedback("ha" * 16, 1, "sad", "timing", None)
        client.upsert_recording_feedback("hb" * 16, 1, "sad", "timing", None)

        assert client.resolve_recording_feedback("ha" * 16) == 1
        assert client.get_recording_feedback_rows("hb" * 16)[0].resolved_at is None


@pytest.mark.integration
class TestReviewFreshCommand:
    """CLI behavior with the editor session patched out."""

    def _invoke(self, config_path, input_text):
        return runner.invoke(
            lyrics_app,
            ["edit", "--review-fresh", "--config", str(config_path)],
            input=input_text,
            env={"COLUMNS": "200"},
        )

    def test_pick_reason_line_and_pause(self, make_test_provider, postgres_url, tmp_path, patched_editor):
        provider = _init_schema(make_test_provider)
        _seed_fresh_pool(provider)
        config_path = _write_config(tmp_path, postgres_url)
        patched_editor["outcomes"].append(_upload_outcome())

        result = self._invoke(config_path, "\n")  # pause, then EOF declines offers
        assert result.exit_code == 0, result.output
        assert "Next review song: Song C" in result.output
        assert "Why picked: never reviewed (no feedback on this recording)" in result.output
        assert "2 songset appearance(s)" in result.output
        assert "Press Enter to open the editor, or type anything + Enter to abort" in result.output
        # Edited recording is the older un-reviewed one.
        assert patched_editor["calls"][0][0] == "hash-cz"
        # No poor-feedback banner in fresh mode (no feedback by construction).
        assert "Open poor feedback" not in result.output

    def test_mutual_exclusion_with_song_id(self, make_test_provider, postgres_url, tmp_path):
        _init_schema(make_test_provider)
        config_path = _write_config(tmp_path, postgres_url)

        result = runner.invoke(
            lyrics_app,
            ["edit", "song_001", "--review-fresh", "--config", str(config_path)],
        )
        assert result.exit_code == 1
        assert "cannot combine song_id argument with --review-fresh" in result.output

    def test_mutual_exclusion_with_review_next_song(self, make_test_provider, postgres_url, tmp_path):
        _init_schema(make_test_provider)
        config_path = _write_config(tmp_path, postgres_url)

        result = runner.invoke(
            lyrics_app,
            ["edit", "--review-next-song", "--review-fresh", "--config", str(config_path)],
        )
        assert result.exit_code == 1
        assert "cannot combine --review-next-song with --review-fresh" in result.output

    def test_bare_invocation_error_mentions_fresh(self, make_test_provider, postgres_url, tmp_path):
        _init_schema(make_test_provider)
        config_path = _write_config(tmp_path, postgres_url)

        result = runner.invoke(
            lyrics_app,
            ["edit", "--config", str(config_path)],
        )
        assert result.exit_code == 1
        assert "provide a song_id argument or use --review-next-song/--review-fresh" in result.output

    def test_song_with_feedback_skipped_next_fresh_picked(
        self, make_test_provider, postgres_url, tmp_path, patched_editor, monkeypatch
    ):
        provider = _init_schema(make_test_provider)
        _seed_fresh_pool(provider)
        config_path = _write_config(tmp_path, postgres_url)
        patched_editor["outcomes"].append(_upload_outcome())  # session 1 only

        # A successful upload marks the recording reviewed: the fake editor
        # session inserts the feedback row the real admin feedback hotkey
        # would create. The queue then recomputes → next pick is song_d.
        db_for_session = DatabaseClient(ConnectionProvider(postgres_url, sslmode="disable"))

        def _fake_with_feedback(db_client, cache_dir, r2_client, song, recording, feedback_user_id=None):
            # Behave like patched_editor's fake, but record feedback first.
            patched_editor["calls"].append((recording.hash_prefix, feedback_user_id))
            scripted = patched_editor["outcomes"].pop(0) if patched_editor["outcomes"] else None
            if scripted is None:
                outcome = lyrics_commands.EditorSessionOutcome(
                    no_audio=True, upload_attempted=False, upload_result=None
                )
            elif isinstance(scripted, lyrics_commands.EditorSessionOutcome):
                outcome = scripted
            else:
                scripted.hash_prefix = recording.hash_prefix
                outcome = lyrics_commands.EditorSessionOutcome(
                    no_audio=False, upload_attempted=True, upload_result=scripted
                )
            if outcome.upload_attempted and outcome.upload_result is not None:
                db_for_session.upsert_recording_feedback(recording.content_hash, 2, "happy", None, None)
            return outcome

        monkeypatch.setattr(lyrics_commands, "_run_editor_session", _fake_with_feedback)

        # Pause, decline skip (publish accepted so no skip offer fires… the
        # plan is publish → continue): accept publish, accept continue →
        # loop re-picks with the recomputed queue.
        result = self._invoke(config_path, "\nyes\n\n")
        assert result.exit_code == 0, result.output
        assert "Next review song: Song C" in result.output
        assert "Next review song: Song D" in result.output

    def test_exhausted_message(self, make_test_provider, postgres_url, tmp_path, patched_editor):
        provider = _init_schema(make_test_provider)
        _seed_fresh_pool(provider)
        config_path = _write_config(tmp_path, postgres_url)

        # No scripted outcomes → no-audio skip path walks the whole queue.
        result = self._invoke(config_path, "\n\n")
        assert result.exit_code == 0, result.output
        assert result.output.count("Skipping") == 2
        assert "No never-reviewed songs available (2 skipped this session)" in result.output

    def test_empty_pool_message(self, make_test_provider, postgres_url, tmp_path):
        _init_schema(make_test_provider)
        config_path = _write_config(tmp_path, postgres_url)

        result = self._invoke(config_path, "")
        assert result.exit_code == 0, result.output
        assert "No never-reviewed songs available (0 skipped this session)" in result.output

    def test_post_upload_publish_offer_appears(
        self, make_test_provider, postgres_url, tmp_path, patched_editor
    ):
        provider = _init_schema(make_test_provider)
        _seed_fresh_pool(provider)
        config_path = _write_config(tmp_path, postgres_url)
        patched_editor["outcomes"].append(_upload_outcome())

        # pause, decline publish, decline continue
        result = self._invoke(config_path, "\nn\nn\n")
        assert result.exit_code == 0, result.output
        assert "Set visibility to published?" in result.output
        assert "Visibility left unchanged." in result.output
        # No clear-ratings offer in fresh mode (open_sad_count is 0 here —
        # feedback only exists on other recordings).
        assert "Clear poor ratings?" not in result.output

    def test_feedback_user_id_passed_to_editor_session(
        self, make_test_provider, postgres_url, tmp_path, patched_editor
    ):
        provider = _init_schema(make_test_provider)
        _seed_fresh_pool(provider)
        config_path = _write_config(tmp_path, postgres_url)
        patched_editor["outcomes"].append(_upload_outcome())

        result = self._invoke(config_path, "\n")
        assert result.exit_code == 0, result.output
        # admin.feedback_email unset → feedback disabled (None) but the
        # session still runs.
        assert patched_editor["calls"][0][1] is None
        assert "Feedback disabled: set" in result.output
