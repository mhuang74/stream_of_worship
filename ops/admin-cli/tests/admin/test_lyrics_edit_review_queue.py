"""Tests for `sow-admin lyrics edit --review-next-song` (issue #260).

Covers:
- The pure post-upload offer planner (`plan_post_upload_offers`) — no DB.
- The ranked review queue and its helper SQL (`get_review_queue`,
  `get_recording_feedback_summary`, `resolve_recording_sad_feedback`)
  against real Postgres via the CLI seam.
- The external CLI behavior of `lyrics edit --review-next-song`: the
  ranked pick reason line, the pause, the pre-launch poor-feedback
  banner, and the post-upload offer sequence — with the editor session
  patched out (the editor TUI itself is a separate seam).
"""

import builtins
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

    The command module builds its provider via
    ``stream_of_worship.admin.commands.catalog.get_db_client``, so both
    the catalog module's ``ConnectionProvider`` reference (used by
    ``get_db_client``) and the lyrics module's own reference (used for
    feedback SQL) must be patched.
    """

    def _factory(url, sslmode="require"):
        return ConnectionProvider(url, sslmode="disable")

    monkeypatch.setattr(lyrics_commands, "ConnectionProvider", _factory)
    monkeypatch.setattr(catalog_commands, "ConnectionProvider", _factory)


@pytest.fixture(autouse=True)
def _stub_r2(monkeypatch):
    """Replace R2Client with an inert stub.

    The edit command constructs R2Client from config; the review-queue
    behavior under test never touches object storage because the editor
    session is patched out too. (Same seam as test_audio_lrc_visibility.)
    """

    class _StubR2:
        audio_exists = staticmethod(lambda hash_prefix: False)
        download_audio = staticmethod(lambda hash_prefix, path: None)
        get_lrc_identity = staticmethod(lambda hash_prefix: None)
        download_lrc_content = staticmethod(lambda hash_prefix: None)

    monkeypatch.setattr(lyrics_commands, "R2Client", lambda **kwargs: _StubR2())


@pytest.fixture
def patched_editor(monkeypatch):
    """Patch _run_editor_session to return scripted outcomes.

    Used by tests that exercise the pick/offer flow without the real TUI.
    Returns the list the test appends scripted outcomes to; each session
    pops one outcome (None = no audio). Upload outcomes carry the edited
    recording's hash prefix so DB assertions can target it.
    """
    calls: list[str] = []
    outcomes: list[object] = []

    def _fake_session(db_client, cache_dir, r2_client, song, recording, feedback_user_id=None):
        calls.append(recording.hash_prefix)
        scripted = outcomes.pop(0) if outcomes else None
        if scripted is None:
            return lyrics_commands.EditorSessionOutcome(
                no_audio=True, upload_attempted=False, upload_result=None
            )
        if isinstance(scripted, lyrics_commands.EditorSessionOutcome):
            # Prebuilt outcome (quit / draft helpers): pass through.
            return scripted
        scripted.hash_prefix = recording.hash_prefix
        return lyrics_commands.EditorSessionOutcome(
            no_audio=False, upload_attempted=True, upload_result=scripted
        )

    monkeypatch.setattr(lyrics_commands, "_run_editor_session", _fake_session)
    return outcomes


def _upload_outcome(success=True):
    from stream_of_worship.admin.editor.upload import UploadResult

    result = UploadResult(success=success, r2_url="https://r2/x.lrc" if success else None)
    return result


def _draft_outcome():
    """Scripted session that ended in a local-draft save (story 17):
    upload attempted, no upload result."""
    return lyrics_commands.EditorSessionOutcome(
        no_audio=False, upload_attempted=True, upload_result=None
    )


def _quit_outcome():
    """Scripted session that ended via quit-without-save: no dialog opened."""
    return lyrics_commands.EditorSessionOutcome(
        no_audio=False, upload_attempted=False, upload_result=None
    )


def _clean_quit_outcome():
    """Scripted session that ended via quit dialog with no unsaved changes."""
    return lyrics_commands.EditorSessionOutcome(
        no_audio=False, upload_attempted=False, upload_result=None, quit_clean=True
    )


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


def _write_config(tmp_path, postgres_url, feedback_email=None):
    config_path = Path(tmp_path) / "config.toml"
    admin_section = f'\n[admin]\nfeedback_email = "{feedback_email}"\n' if feedback_email else ""
    config_path.write_text(f'[database]\nurl = "{postgres_url}"\n' + admin_section)
    return config_path


def _init_schema(make_test_provider):
    _drop_all_tables(make_test_provider)
    provider = make_test_provider()
    with provider.get_connection().cursor() as cur:
        for stmt in ALL_SCHEMA_STATEMENTS:
            cur.execute(stmt)
    provider.get_connection().commit()
    return provider


def _seed_review_pool(provider):
    """Seed four songs with distinct ranking evidence.

    song_c: open sad on hash-dd (2024-03-02, newest) -> rank 1.
    song_a: open sad on hash-aa (2024-01-01, older) -> rank 2.
    song_b: no open sad; 0 songsets, 1 favorite, newest album -> rank 3.
    Recordings hash-cc (song_c) and hash-pp (song_a, published) prove
    the per-song dedup: hash-dd wins for song_c (newer feedback) and the
    published hash-pp never enters the pool. song_d (published, hash-hz)
    carries the open fb5 sad row outside the pool for summary coverage.
    """
    HA, HB, HC, HD, HP, HZ = (
        "ha" * 16,
        "hb" * 16,
        "hc" * 16,
        "hd" * 16,
        "hp" * 16,
        "hz" * 16,
    )
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
              'Album Z','S17','2024-04-01T00:00:00Z')
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
             (%s,'hash-cc','song_c','c.mp3',1,'2024','review','2024-01-01T00:00:00Z'),
             (%s,'hash-dd','song_c','d.mp3',1,'2024','review','2024-03-01T00:00:00Z'),
             (%s,'hash-pp','song_a','p.mp3',1,'2024','published','2024-05-01T00:00:00Z'),
             (%s,'hash-hz','song_d','z.mp3',1,'2024','published','2024-05-02T00:00:00Z')
            """,
            (HA, HB, HC, HD, HP, HZ),
        )
        cur.execute(
            """
            INSERT INTO lyrics_feedback (id, user_id, recording_content_hash,
                rating, reason, created_at, updated_at)
            VALUES
             ('fb1',1,%s,'sad','timing','2024-01-01T00:00:00Z','2024-01-01T00:00:00Z'),
             ('fb2',2,%s,'sad','missing','2024-02-01T00:00:00Z','2024-02-01T00:00:00Z'),
             ('fb3',3,%s,'sad','timing','2024-03-01T00:00:00Z','2024-03-02T00:00:00Z'),
             ('fb4',1,%s,'happy',NULL,'2024-03-01T00:00:00Z','2024-03-01T00:00:00Z'),
             ('fb5',2,%s,'sad','other','2024-01-05T00:00:00Z','2024-01-05T00:00:00Z'),
             ('fb6',3,%s,'sad','missing','2024-06-01T00:00:00Z','2024-06-01T00:00:00Z')
            """,
            (HA, HC, HD, HD, HZ, HP),
        )
        cur.execute("INSERT INTO songsets (id, user_id, name) VALUES ('ss1',1,'S1'),('ss2',1,'S2')")
        cur.execute(
            """
            INSERT INTO songset_items (id, songset_id, song_id, position) VALUES
             ('it1','ss1','song_a',1),('it2','ss1','song_a',2),('it3','ss2','song_a',3),
             ('it4','ss1','song_c',4)
            """
        )
        cur.execute(
            "INSERT INTO user_favorite_songs (user_id, song_id) "
            "VALUES (2,'song_c'),(3,'song_c'),(1,'song_b')"
        )
    conn.commit()


class TestPlanPostUploadOffers:
    """Pure decision-function tests (no DB)."""

    def test_full_plan_loop_mode_with_open_sad(self):
        plan = lyrics_commands.plan_post_upload_offers(
            upload_ok=True, open_sad_count=2, loop_mode=True
        )
        assert plan == ["clear", "visibility", "skip-if-visibility-declined", "continue"]

    def test_no_open_sad_omits_clear(self):
        plan = lyrics_commands.plan_post_upload_offers(
            upload_ok=True, open_sad_count=0, loop_mode=True
        )
        assert plan == ["visibility", "skip-if-visibility-declined", "continue"]

    def test_failed_upload_produces_no_offers(self):
        plan = lyrics_commands.plan_post_upload_offers(
            upload_ok=False, open_sad_count=5, loop_mode=True
        )
        assert plan == []

    def test_single_mode_no_loop_only_steps(self):
        plan = lyrics_commands.plan_post_upload_offers(
            upload_ok=True, open_sad_count=0, loop_mode=False
        )
        assert plan == ["visibility"]

    def test_single_mode_with_open_sad(self):
        plan = lyrics_commands.plan_post_upload_offers(
            upload_ok=True, open_sad_count=3, loop_mode=False
        )
        assert plan == ["clear", "visibility"]


@pytest.mark.integration
class TestGetReviewQueue:
    """Ranking + dedup SQL against real Postgres."""

    def test_ranks_by_newest_open_sad_then_dedups_per_song(self, make_test_provider, postgres_url):
        provider = _init_schema(make_test_provider)
        _seed_review_pool(provider)
        client = DatabaseClient(provider)

        queue = client.get_review_queue()
        assert [c.song_id for c in queue] == ["song_c", "song_a", "song_b"]
        # Per-song dedup: song_c's newest-feedback recording (hash-dd) wins.
        assert queue[0].hash_prefix == "hash-dd"
        assert queue[0].open_sad_count == 1
        assert queue[0].reason_counts == {"timing": 1}
        assert queue[0].sample_reason == "timing"
        assert queue[1].hash_prefix == "hash-aa"
        # Published recording (hash-pp) never enters the pool.
        assert all(c.hash_prefix != "hash-pp" for c in queue)

    def test_no_feedback_song_ranked_last_with_cascade_tiebreaks(
        self, make_test_provider, postgres_url
    ):
        provider = _init_schema(make_test_provider)
        _seed_review_pool(provider)
        client = DatabaseClient(provider)

        queue = client.get_review_queue()
        song_b = queue[2]
        assert song_b.open_sad_count == 0
        assert song_b.latest_open_sad_at is None
        assert song_b.songset_appearances == 0
        assert song_b.favorite_count == 1

    def test_resolved_sad_drops_song_out_of_tier_one(self, make_test_provider, postgres_url):
        provider = _init_schema(make_test_provider)
        _seed_review_pool(provider)
        client = DatabaseClient(provider)

        resolved = client.resolve_recording_sad_feedback("ha" * 16)
        assert resolved == 1

        queue = client.get_review_queue()
        # song_a falls out of the feedback tier; its 3 songset appearances
        # keep it above song_b (0 appearances, 1 favorite).
        assert [c.song_id for c in queue] == ["song_c", "song_a", "song_b"]
        assert queue[1].hash_prefix == "hash-aa"
        assert queue[1].open_sad_count == 0

    def test_empty_pool_when_nothing_in_review(self, make_test_provider, postgres_url):
        _init_schema(make_test_provider)
        client = DatabaseClient(make_test_provider())
        assert client.get_review_queue() == []


@pytest.mark.integration
class TestFeedbackSummaryAndResolve:
    def test_summary_counts_open_sad_only(self, make_test_provider, postgres_url):
        provider = _init_schema(make_test_provider)
        _seed_review_pool(provider)
        client = DatabaseClient(provider)

        summary = client.get_recording_feedback_summary("ha" * 16)
        assert summary.open_sad_count == 1
        assert summary.reason_counts == {"timing": 1}
        assert summary.sample_reason == "timing"

        # fb5 on hash-hz (song_d, published) is an open sad-with-reason
        # row outside the review pool; fb4 (happy) never counts.
        summary_z = client.get_recording_feedback_summary("hz" * 16)
        assert summary_z.open_sad_count == 1
        assert summary_z.reason_counts == {"other": 1}
        assert summary_z.sample_reason == "other"

        # hash-bb (song_b) has no feedback at all.
        summary_b = client.get_recording_feedback_summary("hb" * 16)
        assert summary_b.open_sad_count == 0
        assert summary_b.reason_counts == {}

        summary_p = client.get_recording_feedback_summary("hp" * 16)
        assert summary_p.open_sad_count == 1  # open sad on a published rec still counts

        empty = client.get_recording_feedback_summary("hy" * 16)
        assert empty.open_sad_count == 0
        assert empty.reason_counts == {}

    def test_resolve_reversible_via_unresolve(self, make_test_provider, postgres_url):
        provider = _init_schema(make_test_provider)
        _seed_review_pool(provider)
        client = DatabaseClient(provider)

        assert client.resolve_recording_sad_feedback("ha" * 16) == 1
        assert client.get_recording_feedback_summary("ha" * 16).open_sad_count == 0
        # Unresolve via the existing feedback path (raw SQL mirror of the
        # CLI command's write) restores the open count.
        with client.connection.cursor() as cur:
            cur.execute(
                "UPDATE lyrics_feedback SET resolved_at = NULL "
                "WHERE recording_content_hash = %s",
                ("ha" * 16,),
            )
        client.connection.commit()
        assert client.get_recording_feedback_summary("ha" * 16).open_sad_count == 1


@pytest.mark.integration
class TestReviewNextSongCommand:
    """CLI behavior with the editor session patched out."""

    def _invoke(self, config_path, input_text):
        return runner.invoke(
            lyrics_app,
            ["edit", "--review-next-song", "--config", str(config_path)],
            input=input_text,
            env={"COLUMNS": "200"},
        )

    def test_pick_reason_line_and_pause(
        self, make_test_provider, postgres_url, tmp_path, patched_editor
    ):
        provider = _init_schema(make_test_provider)
        _seed_review_pool(provider)
        config_path = _write_config(tmp_path, postgres_url)
        patched_editor.append(_upload_outcome())

        result = self._invoke(config_path, "\n")  # pause, then EOF declines offers
        assert result.exit_code == 0, result.output
        assert "Next review song: Song C" in result.output
        assert "Why picked:" in result.output
        assert "1 open poor rating(s) since" in result.output
        assert "timing" in result.output
        assert "Press Enter to open the editor, or type anything + Enter to abort" in result.output
        # Pre-launch banner from candidate summary
        assert "Open poor feedback: 1 rating(s)" in result.output

    def test_eof_declines_all_and_stops_loop(
        self, make_test_provider, postgres_url, tmp_path, patched_editor
    ):
        provider = _init_schema(make_test_provider)
        _seed_review_pool(provider)
        config_path = _write_config(tmp_path, postgres_url)
        patched_editor.append(_upload_outcome())

        result = self._invoke(config_path, "\n")
        assert result.exit_code == 0, result.output
        # The single "\n" feeds the pause gate; EOF then declines every
        # subsequent prompt (offers, continue) and the loop stops after
        # one song.
        assert result.output.count("Next review song:") == 1

    def test_skip_declined_song_re_picked(
        self, make_test_provider, postgres_url, tmp_path, patched_editor
    ):
        provider = _init_schema(make_test_provider)
        _seed_review_pool(provider)
        config_path = _write_config(tmp_path, postgres_url)
        patched_editor.append(_upload_outcome())  # session 1 only

        # Declining the skip offer leaves the song eligible (story 20):
        # with nothing changed (no clear, no visibility change), the loop
        # re-picks song_c. Session 2 has no scripted audio, so the no-audio
        # skip path then walks the remaining queue to exhaustion. The
        # trailing Enters feed the pauses of the no-audio walk.
        result = self._invoke(config_path, "\nn\nn\nn\n\n\n\n\n")
        assert result.exit_code == 0, result.output
        assert result.output.count("Next review song: Song C") == 2
        assert "will be skipped" not in result.output  # skip declined
        assert "No review-visibility songs available (3 skipped this session)" in result.output

    def test_skip_accepted_excludes_song_from_next_pick(
        self, make_test_provider, postgres_url, tmp_path, patched_editor
    ):
        provider = _init_schema(make_test_provider)
        _seed_review_pool(provider)
        config_path = _write_config(tmp_path, postgres_url)
        patched_editor.append(_upload_outcome())

        # decline clear, leave visibility unchanged ("n" is not a menu
        # number), accept skip, accept continue
        result = self._invoke(config_path, "\nn\nn\nyes\n\n")
        assert result.exit_code == 0, result.output
        assert "will be skipped for the rest of this session" in result.output
        assert "Next review song: Song A" in result.output

    def test_clear_resolves_and_publish_promotes(
        self, make_test_provider, postgres_url, tmp_path, patched_editor
    ):
        provider = _init_schema(make_test_provider)
        _seed_review_pool(provider)
        config_path = _write_config(tmp_path, postgres_url)
        client = DatabaseClient(make_test_provider())
        patched_editor.append(_upload_outcome())

        # accept clear, choose "published" (menu 1), accept continue,
        # Enter at next pause
        result = self._invoke(config_path, "\nyes\n1\n\n\n")
        assert result.exit_code == 0, result.output
        assert "Resolved 1 poor rating(s)" in result.output
        assert "Set visibility for hash-dd (current: ◐ review):" in result.output
        assert "set to published" in result.output

        # DB state: song_c's edited recording promoted; feedback resolved.
        rec = client.get_recording_by_hash("hash-dd")
        assert rec.visibility_status == "published"
        summary = client.get_recording_feedback_summary("hd" * 16)
        assert summary.open_sad_count == 0

        # Next pick excludes promoted song_c -> song_a
        assert "Next review song: Song A" in result.output

    def test_publish_accepted_skips_offer_and_continues(
        self, make_test_provider, postgres_url, tmp_path, patched_editor
    ):
        provider = _init_schema(make_test_provider)
        _seed_review_pool(provider)
        config_path = _write_config(tmp_path, postgres_url)
        patched_editor.append(_upload_outcome())

        # decline clear, choose "published" (menu 1), accept continue,
        # Enter at next pause
        result = self._invoke(config_path, "\nn\n1\n\n\n")
        assert result.exit_code == 0, result.output
        assert "Visibility left unchanged" not in result.output
        assert result.output.count("Next review song:") >= 2
        assert "Next review song: Song A" in result.output

    def test_no_audio_candidate_skipped_and_counted(
        self, make_test_provider, postgres_url, tmp_path, patched_editor
    ):
        provider = _init_schema(make_test_provider)
        _seed_review_pool(provider)
        config_path = _write_config(tmp_path, postgres_url)

        result = self._invoke(config_path, "\n")
        assert result.exit_code == 0, result.output
        assert "Skipping song_c (Song C)" in result.output
        assert "no audio available" in result.output
        assert "Next review song: Song A" in result.output

    def test_dry_session_exhausts_queue_message(
        self, make_test_provider, postgres_url, tmp_path, patched_editor
    ):
        provider = _init_schema(make_test_provider)
        _seed_review_pool(provider)
        config_path = _write_config(tmp_path, postgres_url)

        # skip all three songs via no-audio path, then queue exhausts
        result = self._invoke(config_path, "\n\n\n")
        assert result.exit_code == 0, result.output
        assert result.output.count("Skipping") == 3
        assert "No review-visibility songs available (3 skipped this session)" in result.output

    def test_empty_queue_message(self, make_test_provider, postgres_url, tmp_path):
        _init_schema(make_test_provider)
        config_path = _write_config(tmp_path, postgres_url)

        result = self._invoke(config_path, "")
        assert result.exit_code == 0, result.output
        assert "No review-visibility songs available (0 skipped this session)" in result.output

    def test_mutual_exclusion_with_song_id(self, make_test_provider, postgres_url, tmp_path):
        _init_schema(make_test_provider)
        config_path = _write_config(tmp_path, postgres_url)

        result = runner.invoke(
            lyrics_app,
            ["edit", "song_001", "--review-next-song", "--config", str(config_path)],
        )
        assert result.exit_code == 1
        assert "cannot combine" in result.output

    def test_missing_both_errors(self, make_test_provider, postgres_url, tmp_path):
        _init_schema(make_test_provider)
        config_path = _write_config(tmp_path, postgres_url)

        result = runner.invoke(
            lyrics_app,
            ["edit", "--config", str(config_path)],
        )
        assert result.exit_code == 1
        assert "provide a song_id argument or use --review-next-song" in result.output

    def test_abort_at_pause_skips_editor(
        self, make_test_provider, postgres_url, tmp_path, patched_editor
    ):
        provider = _init_schema(make_test_provider)
        _seed_review_pool(provider)
        config_path = _write_config(tmp_path, postgres_url)
        patched_editor.append(_upload_outcome())

        # Non-empty reply at the pause aborts: no editor, song stays queued.
        result = self._invoke(config_path, "q\n")
        assert result.exit_code == 0, result.output
        assert "Aborted; editor not opened." in result.output
        assert result.output.count("Next review song: Song C") == 1
        assert patched_editor == [_upload_outcome()]

    def test_ctrl_c_at_pause_aborts(self, monkeypatch):
        candidate = SimpleNamespace(
            latest_open_sad_at=None,
            reason_counts={},
            songset_appearances=0,
            favorite_count=0,
            song_title="Song X",
            song_id="song_x",
            hash_prefix="hx",
            open_sad_count=0,
            sample_reason=None,
        )

        def _raise_keyboard_interrupt(prompt):
            raise KeyboardInterrupt

        monkeypatch.setattr(builtins, "input", _raise_keyboard_interrupt)
        proceed = lyrics_commands._print_review_pick_reason(candidate, pause=True)
        assert proceed is False


@pytest.mark.integration
class TestPlainEditStillWorks:
    """Story 24: plain `lyrics edit SONG_ID` keeps working (banner without
    pause, same post-upload offers); story 12: clear no-audio error."""

    def _seed_single_song(self, provider):
        """One song with one review recording carrying open sad feedback."""
        HA = "ha" * 16
        conn = provider.get_connection()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO "user" (id, name, email, "emailVerified")
                OVERRIDING SYSTEM VALUE VALUES (1,'U1','u1@x.com',true)
                """
            )
            cur.execute(
                """
                INSERT INTO songs (id, title, source_url, scraped_at)
                VALUES ('song_001','測試歌曲','https://x/1','2024-01-01T00:00:00')
                """
            )
            cur.execute(
                """
                INSERT INTO recordings (content_hash, hash_prefix, song_id,
                    original_filename, file_size_bytes, imported_at,
                    visibility_status)
                VALUES (%s,'hash-aa','song_001','a.mp3',1,'2024','review')
                """,
                (HA,),
            )
            cur.execute(
                """
                INSERT INTO lyrics_feedback (id, user_id, recording_content_hash,
                    rating, reason) VALUES ('fb1',1,%s,'sad','timing')
                """,
                (HA,),
            )
        conn.commit()

    def test_banner_no_pause_and_offers(
        self, make_test_provider, postgres_url, tmp_path, monkeypatch
    ):
        provider = _init_schema(make_test_provider)
        self._seed_single_song(provider)
        config_path = _write_config(tmp_path, postgres_url)

        def _fake_session(db_client, cache_dir, r2_client, song, recording, feedback_user_id=None):
            assert recording.hash_prefix == "hash-aa"
            return lyrics_commands.EditorSessionOutcome(
                no_audio=False,
                upload_attempted=True,
                upload_result=_upload_outcome(),
            )

        monkeypatch.setattr(lyrics_commands, "_run_editor_session", _fake_session)

        result = runner.invoke(
            lyrics_app,
            ["edit", "song_001", "--config", str(config_path)],
            input="yes\n1\n",  # accept clear, choose "published" (menu 1)
            env={"COLUMNS": "200"},
        )
        assert result.exit_code == 0, result.output
        # Banner shown without a pause prompt (story 24).
        assert "Open poor feedback: 1 rating(s)" in result.output
        assert "Press Enter" not in result.output
        # Same post-upload offers as loop mode minus loop-only steps.
        assert "Clear poor ratings?" in result.output
        assert "Set visibility for hash-aa (current: ◐ review):" in result.output
        assert "Skip this song" not in result.output
        assert "Continue to next review song" not in result.output
        assert "Resolved 1 poor rating(s)" in result.output
        assert "set to published" in result.output

    def test_no_audio_clear_error(self, make_test_provider, postgres_url, tmp_path, monkeypatch):
        provider = _init_schema(make_test_provider)
        self._seed_single_song(provider)
        config_path = _write_config(tmp_path, postgres_url)

        def _fake_session(db_client, cache_dir, r2_client, song, recording, feedback_user_id=None):
            return lyrics_commands.EditorSessionOutcome(
                no_audio=True, upload_attempted=False, upload_result=None
            )

        monkeypatch.setattr(lyrics_commands, "_run_editor_session", _fake_session)

        result = runner.invoke(
            lyrics_app,
            ["edit", "song_001", "--config", str(config_path)],
            input="",
            env={"COLUMNS": "200"},
        )
        assert result.exit_code == 1
        assert "No audio found for song_001" in result.output
        assert "Audio is required for timestamp alignment" in result.output

    def test_clean_quit_offers_publish_single_song(
        self, make_test_provider, postgres_url, tmp_path, monkeypatch
    ):
        """Clean quit in single-song mode offers a visibility change."""
        provider = _init_schema(make_test_provider)
        self._seed_single_song(provider)
        conn = provider.get_connection()
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE recordings SET lrc_status = 'completed' WHERE hash_prefix = 'hash-aa'"
            )
        conn.commit()
        config_path = _write_config(tmp_path, postgres_url)

        def _fake_session(db_client, cache_dir, r2_client, song, recording, feedback_user_id=None):
            assert recording.hash_prefix == "hash-aa"
            return lyrics_commands.EditorSessionOutcome(
                no_audio=False, upload_attempted=False, upload_result=None, quit_clean=True
            )

        monkeypatch.setattr(lyrics_commands, "_run_editor_session", _fake_session)

        result = runner.invoke(
            lyrics_app,
            ["edit", "song_001", "--config", str(config_path)],
            input="1\n",  # menu 1 = "● published"
            env={"COLUMNS": "200"},
        )
        assert result.exit_code == 0, result.output
        assert "Set visibility for hash-aa (current: ◐ review):" in result.output
        assert "set to published" in result.output
        client = DatabaseClient(make_test_provider())
        assert client.get_recording_by_hash("hash-aa").visibility_status == "published"

    def test_clean_quit_offers_demote_for_published(
        self, make_test_provider, postgres_url, tmp_path, monkeypatch
    ):
        """A published recording is offered review/hold — never its own status."""
        provider = _init_schema(make_test_provider)
        self._seed_single_song(provider)
        conn = provider.get_connection()
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE recordings SET lrc_status = 'completed', "
                "visibility_status = 'published' WHERE hash_prefix = 'hash-aa'"
            )
        conn.commit()
        config_path = _write_config(tmp_path, postgres_url)

        def _fake_session(db_client, cache_dir, r2_client, song, recording, feedback_user_id=None):
            return lyrics_commands.EditorSessionOutcome(
                no_audio=False, upload_attempted=False, upload_result=None, quit_clean=True
            )

        monkeypatch.setattr(lyrics_commands, "_run_editor_session", _fake_session)

        result = runner.invoke(
            lyrics_app,
            ["edit", "song_001", "--config", str(config_path)],
            input="1\n",  # menu 1 = "◐ review" (published is not offered)
            env={"COLUMNS": "200"},
        )
        assert result.exit_code == 0, result.output
        assert "Set visibility for hash-aa (current: ● published):" in result.output
        assert "visibility set to review" in result.output
        client = DatabaseClient(make_test_provider())
        assert client.get_recording_by_hash("hash-aa").visibility_status == "review"

    def test_feedback_user_id_passed_in_single_song_mode(
        self, make_test_provider, postgres_url, tmp_path, monkeypatch
    ):
        provider = _init_schema(make_test_provider)
        self._seed_single_song(provider)  # seeds user id 1, email u1@x.com
        config_path = _write_config(tmp_path, postgres_url, feedback_email="u1@x.com")
        seen: list = []

        def _fake_session(db_client, cache_dir, r2_client, song, recording, feedback_user_id):
            seen.append(feedback_user_id)
            return lyrics_commands.EditorSessionOutcome(
                no_audio=False, upload_attempted=False, upload_result=None
            )

        monkeypatch.setattr(lyrics_commands, "_run_editor_session", _fake_session)

        result = runner.invoke(
            lyrics_app,
            ["edit", "song_001", "--config", str(config_path)],
            input="",
            env={"COLUMNS": "200"},
        )
        assert result.exit_code == 0, result.output
        assert seen == [1]
        assert "Feedback disabled" not in result.output


@pytest.mark.integration
class TestLoopExitAndDraftBehavior:
    """Quit-without-upload must offer a way out of the loop, and a local
    draft save must not clear ratings or trigger offers (stories 17, 21)."""

    def test_quit_without_upload_prompts_continue_and_exits(
        self, make_test_provider, postgres_url, tmp_path, patched_editor
    ):
        provider = _init_schema(make_test_provider)
        _seed_review_pool(provider)
        config_path = _write_config(tmp_path, postgres_url)
        patched_editor.append(_quit_outcome())  # session 1: quit, no dialog

        # Session 2+ unscripted -> no_audio skips; the point here is the
        # continue prompt after the quit, answered "n" -> loop exits.
        result = self._invoke_quit(config_path, "\nn\n")
        assert result.exit_code == 0, result.output
        assert "No upload for song_c (Song C); it stays in the queue." in result.output
        assert "Continue to next review song?" in result.output
        # Answered "n" -> loop exits instead of relaunching the editor.
        assert result.output.count("Next review song: Song C") == 1
        # Song was not skip-marked: it stays eligible (no skip message).
        assert "will be skipped" not in result.output

    def test_draft_save_no_offers_and_no_clear(
        self, make_test_provider, postgres_url, tmp_path, patched_editor
    ):
        provider = _init_schema(make_test_provider)
        _seed_review_pool(provider)
        config_path = _write_config(tmp_path, postgres_url)
        patched_editor.append(_draft_outcome())

        result = self._invoke_quit(config_path, "\n")
        assert result.exit_code == 0, result.output
        # No offers after a draft save (story 17).
        assert "Clear poor ratings?" not in result.output
        assert "Set visibility for" not in result.output
        # Feedback rows stay open.
        client = DatabaseClient(make_test_provider())
        assert client.get_recording_feedback_summary("hd" * 16).open_sad_count == 1
        assert client.get_recording_by_hash("hash-dd").visibility_status == "review"

    def test_visibility_reread_after_upload_in_loop(
        self, make_test_provider, postgres_url, tmp_path, patched_editor
    ):
        """Upload COALESCEs NULL visibility to 'review'; the publish offer
        must appear even though the pre-session Recording object had NULL."""
        provider = _init_schema(make_test_provider)
        _seed_review_pool(provider)
        # Set the picked recording's visibility to NULL (fresh-recording case).
        conn = provider.get_connection()
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE recordings SET visibility_status = NULL WHERE hash_prefix = 'hash-dd'"
            )
        conn.commit()

        config_path = _write_config(tmp_path, postgres_url)
        patched_editor.append(_upload_outcome())

        result = self._invoke_quit(config_path, "\nn\n1\nn\n")
        assert result.exit_code == 0, result.output
        # The menu reports the status read straight from the DB ("review"
        # for hash-cc) and offers only the alternatives.
        assert "Set visibility for hash-cc (current: ◐ review):" in result.output
        assert "visibility set to published" in result.output

    def test_clean_quit_offers_publish_and_continues(
        self, make_test_provider, postgres_url, tmp_path, patched_editor, monkeypatch
    ):
        """Clean quit in loop mode offers publish when eligible, then the
        loop still continues to the next song."""
        provider = _init_schema(make_test_provider)
        _seed_review_pool(provider)
        # Eligibility: completed LRC on the picked recording (hash-dd).
        conn = provider.get_connection()
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE recordings SET lrc_status = 'completed' WHERE hash_prefix = 'hash-dd'"
            )
        conn.commit()

        config_path = _write_config(tmp_path, postgres_url)
        patched_editor.append(_clean_quit_outcome())

        result = self._invoke_quit(config_path, "\n1\n\n")
        assert result.exit_code == 0, result.output
        assert "Set visibility for hash-dd (current: ◐ review):" in result.output
        client = DatabaseClient(make_test_provider())
        assert client.get_recording_by_hash("hash-dd").visibility_status == "published"
        # The loop continues after the publish offer.
        assert "Continue to next review song?" in result.output

    def test_clean_quit_decline_leaves_visibility(
        self, make_test_provider, postgres_url, tmp_path, patched_editor
    ):
        provider = _init_schema(make_test_provider)
        _seed_review_pool(provider)
        conn = provider.get_connection()
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE recordings SET lrc_status = 'completed' WHERE hash_prefix = 'hash-dd'"
            )
        conn.commit()

        config_path = _write_config(tmp_path, postgres_url)
        patched_editor.append(_clean_quit_outcome())

        result = self._invoke_quit(config_path, "\nn\nn\n")
        assert result.exit_code == 0, result.output
        assert "Visibility left unchanged." in result.output
        client = DatabaseClient(make_test_provider())
        assert client.get_recording_by_hash("hash-dd").visibility_status == "review"

    def test_clean_quit_bare_enter_does_not_publish(
        self, make_test_provider, postgres_url, tmp_path, patched_editor
    ):
        """A bare Enter at the menu must leave visibility unchanged."""
        provider = _init_schema(make_test_provider)
        _seed_review_pool(provider)
        conn = provider.get_connection()
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE recordings SET lrc_status = 'completed' WHERE hash_prefix = 'hash-dd'"
            )
        conn.commit()

        config_path = _write_config(tmp_path, postgres_url)
        patched_editor.append(_clean_quit_outcome())

        result = self._invoke_quit(config_path, "\n\n")
        assert result.exit_code == 0, result.output
        assert "Set visibility for hash-dd (current: ◐ review):" in result.output
        assert "Visibility left unchanged." in result.output
        client = DatabaseClient(make_test_provider())
        assert client.get_recording_by_hash("hash-dd").visibility_status == "review"

    def test_clean_quit_invalid_choice_leaves_visibility(
        self, make_test_provider, postgres_url, tmp_path, patched_editor
    ):
        """A non-numeric reply at the menu leaves visibility unchanged."""
        provider = _init_schema(make_test_provider)
        _seed_review_pool(provider)
        conn = provider.get_connection()
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE recordings SET lrc_status = 'completed' WHERE hash_prefix = 'hash-dd'"
            )
        conn.commit()

        config_path = _write_config(tmp_path, postgres_url)
        patched_editor.append(_clean_quit_outcome())

        result = self._invoke_quit(config_path, "\ny\n")
        assert result.exit_code == 0, result.output
        assert "Set visibility for hash-dd (current: ◐ review):" in result.output
        assert "Visibility left unchanged." in result.output
        client = DatabaseClient(make_test_provider())
        assert client.get_recording_by_hash("hash-dd").visibility_status == "review"

    def test_clean_quit_not_offered_when_lrc_pending(
        self, make_test_provider, postgres_url, tmp_path, patched_editor
    ):
        provider = _init_schema(make_test_provider)
        _seed_review_pool(provider)
        config_path = _write_config(tmp_path, postgres_url)
        patched_editor.append(_clean_quit_outcome())

        result = self._invoke_quit(config_path, "\n")
        assert result.exit_code == 0, result.output
        assert "Set visibility for" not in result.output

    @staticmethod
    def _invoke_quit(config_path, input_text):
        return runner.invoke(
            lyrics_app,
            ["edit", "--review-next-song", "--config", str(config_path)],
            input=input_text,
            env={"COLUMNS": "200"},
        )
