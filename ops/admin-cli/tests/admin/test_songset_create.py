"""Tests for ``sow-admin songset create`` and helpers.

Follows the mocking conventions in ``test_audio_soft_delete_maintenance.py``:
in-memory stubs for ``ReadOnlyClient`` / ``SongsetClient`` / ``UserClient``.
"""

import os
from unittest.mock import MagicMock, patch

import pytest
import typer
from typer.testing import CliRunner

from stream_of_worship.admin.commands._songset_create_helpers import (
    _dedupe_songset_name,
    _format_duration,
    _sanitize_title_for_name,
    resolve_song_token,
)
from stream_of_worship.admin.config import AdminConfig
from stream_of_worship.admin.constants import (
    SONGSET_MAX_DURATION_SECONDS,
    SONGSET_MAX_SONGS,
)
from stream_of_worship.admin.db.models import Recording, Song
from stream_of_worship.admin.main import app
from stream_of_worship.db.app.models import Songset
from stream_of_worship.db.app.songset_client import MissingReferenceError

runner = CliRunner()


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


def _song(
    song_id: str = "test_song_a1b2c3d4",
    title: str = "Test Song",
    album: str = "敬拜讚美15",
    key: str = "G",
) -> Song:
    return Song(
        id=song_id,
        title=title,
        source_url="https://example.com/song",
        scraped_at="2024-01-01T00:00:00",
        album_name=album,
        musical_key=key,
    )


def _recording(
    hash_prefix: str = "abc123def456",
    song_id: str = "test_song_a1b2c3d4",
    duration: float = 240.0,
    bpm: float = 72.0,
    imported_at: str = "2024-01-01T00:00:00",
) -> Recording:
    return Recording(
        content_hash=f"{hash_prefix}{'0' * 52}"[:64],
        hash_prefix=hash_prefix,
        song_id=song_id,
        original_filename="song.mp3",
        file_size_bytes=100,
        imported_at=imported_at,
        r2_audio_url=f"s3://bucket/{hash_prefix}/audio.mp3",
        duration_seconds=duration,
        tempo_bpm=bpm,
    )


class FakeReadClient:
    """In-memory ReadOnlyClient stub."""

    def __init__(
        self,
        songs: dict[str, Song] | None = None,
        search_results: dict[str, list[Song]] | None = None,
        recordings: dict[str, list[Recording]] | None = None,
    ):
        self._songs = songs or {}
        self._search = search_results or {}
        self._recordings = recordings or {}

    def get_song(self, song_id: str, include_deleted: bool = False):
        return self._songs.get(song_id)

    def search_songs(self, query: str, field: str = "all", limit: int = 20, include_deleted: bool = False):
        return self._search.get(query, [])[:limit]

    def list_active_recordings_by_song_id(self, song_id: str, include_deleted: bool = False):
        return list(self._recordings.get(song_id, []))


class FakeSongsetClient:
    """In-memory SongsetClient stub."""

    def __init__(self, existing_songsets: list[Songset] | None = None):
        self._existing = existing_songsets or []
        self.created: list[dict] = []
        self.fail_with: Exception | None = None

    def list_songsets_for_user_id(self, user_id: int, limit=None):
        return list(self._existing)

    def create_songset_with_items(self, name: str, description: str, items: list[dict]) -> Songset:
        if self.fail_with is not None:
            raise self.fail_with
        songset = Songset(
            id="ss_0a1b2c3d",
            user_id=1,
            name=name,
            description=description,
            created_at="2024-01-01T00:00:00",
            updated_at="2024-01-01T00:00:00",
        )
        self.created.append({"name": name, "description": description, "items": items})
        return songset


def _make_config() -> AdminConfig:
    return AdminConfig(database_url="postgresql://example")


# ---------------------------------------------------------------------------
# Constants-parity test
# ---------------------------------------------------------------------------


def test_constants_parity_with_webapp():
    """Keep admin CLI constants in sync with delivery/webapp/src/lib/constants.ts:1-2."""
    assert SONGSET_MAX_SONGS == 5
    assert SONGSET_MAX_DURATION_SECONDS == 1500


# ---------------------------------------------------------------------------
# _sanitize_title_for_name
# ---------------------------------------------------------------------------


def test_sanitize_title_strips_spaces():
    assert _sanitize_title_for_name("信 實 偉 大") == "信實偉大"


def test_sanitize_title_strips_whitespace():
    assert _sanitize_title_for_name("  Hello  ") == "Hello"


def test_sanitize_title_drops_non_printable():
    assert _sanitize_title_for_name("abc\x00def") == "abcdef"


def test_sanitize_title_empty_returns_empty():
    assert _sanitize_title_for_name("") == ""


# ---------------------------------------------------------------------------
# _dedupe_songset_name
# ---------------------------------------------------------------------------


def test_dedupe_name_no_collision():
    assert _dedupe_songset_name("Foo", set()) == "Foo"


def test_dedupe_name_first_collision():
    assert _dedupe_songset_name("Foo", {"Foo"}) == "Foo_2"


def test_dedupe_name_second_collision():
    assert _dedupe_songset_name("Foo", {"Foo", "Foo_2"}) == "Foo_3"


def test_dedupe_name_foo_disappeared():
    assert _dedupe_songset_name("Foo", {"Foo_2"}) == "Foo"


def test_dedupe_name_third_collision():
    assert _dedupe_songset_name("Foo", {"Foo", "Foo_2", "Foo_3"}) == "Foo_4"


# ---------------------------------------------------------------------------
# _format_duration
# ---------------------------------------------------------------------------


def test_format_duration_none():
    assert _format_duration(None) == "--:--"


def test_format_duration_zero():
    assert _format_duration(0) == "0:00"


def test_format_duration_normal():
    assert _format_duration(252) == "4:12"


def test_format_duration_over_hour():
    assert _format_duration(3725) == "62:05"


# ---------------------------------------------------------------------------
# resolve_song_token
# ---------------------------------------------------------------------------


def test_resolve_token_exact_song_id():
    song = _song()
    recording = _recording()
    read_client = FakeReadClient(
        songs={song.id: song},
        recordings={song.id: [recording]},
    )
    console = MagicMock()

    result_song, result_recording = resolve_song_token(
        "test_song_a1b2c3d4", read_client, console, non_interactive=False
    )
    assert result_song.id == song.id
    assert result_recording.hash_prefix == recording.hash_prefix


def test_resolve_token_slug_id_multiple_underscores():
    song = _song(song_id="wei_da_de_shen_a1b2c3d4")
    recording = _recording(song_id="wei_da_de_shen_a1b2c3d4")
    read_client = FakeReadClient(
        songs={song.id: song},
        recordings={song.id: [recording]},
    )
    console = MagicMock()

    result_song, _result_recording = resolve_song_token(
        "wei_da_de_shen_a1b2c3d4", read_client, console, non_interactive=False
    )
    assert result_song.id == "wei_da_de_shen_a1b2c3d4"


def test_resolve_token_slug_id_uppercase_hex_not_matched():
    song = _song(song_id="test_song_0045_a1b2c3d4", title="wo_de_ye_su_4C27D159")
    recording = _recording(song_id="test_song_0045_a1b2c3d4")
    read_client = FakeReadClient(
        search_results={"wo_de_ye_su_4C27D159": [song]},
        recordings={song.id: [recording]},
    )
    console = MagicMock()

    result_song, _result_recording = resolve_song_token(
        "wo_de_ye_su_4C27D159", read_client, console, non_interactive=False
    )
    assert result_song.id == "test_song_0045_a1b2c3d4"


def test_resolve_token_slug_id_not_found_exits():
    read_client = FakeReadClient(
        songs={},
        search_results={"test_song_a1b2c3d4": [_song(song_id="test_song_0099_a1b2c3d4")]},
    )
    console = MagicMock()

    with pytest.raises(typer.Exit):
        resolve_song_token(
            "test_song_a1b2c3d4", read_client, console, non_interactive=False
        )
    console.print.assert_any_call("[red]No song found with ID 'test_song_a1b2c3d4'.[/red]")


def test_resolve_token_title_single_match():
    song = _song(song_id="test_song_0045_a1b2c3d4", title="信實偉大")
    recording = _recording(song_id="test_song_0045_a1b2c3d4")
    read_client = FakeReadClient(
        search_results={"信實偉大": [song]},
        recordings={song.id: [recording]},
    )
    console = MagicMock()

    result_song, _result_recording = resolve_song_token(
        "信實偉大", read_client, console, non_interactive=False
    )
    assert result_song.id == "test_song_0045_a1b2c3d4"


def test_resolve_token_title_zero_matches_exits():
    read_client = FakeReadClient(search_results={})
    console = MagicMock()

    with pytest.raises(typer.Exit):
        resolve_song_token("Nonexistent", read_client, console, non_interactive=False)


def test_resolve_token_title_multiple_matches_non_interactive_exits():
    song1 = _song(song_id="test_song_0044_a1b2c3d4", title="恩典之路")
    song2 = _song(song_id="test_song_0072_a1b2c3d4", title="恩典之路 (Live)")
    read_client = FakeReadClient(
        search_results={"恩典之路": [song1, song2]},
    )
    console = MagicMock()

    with pytest.raises(typer.Exit):
        resolve_song_token("恩典之路", read_client, console, non_interactive=True)


def test_resolve_token_title_multiple_matches_interactive_picks(monkeypatch):
    song1 = _song(song_id="test_song_0044_a1b2c3d4", title="恩典之路")
    song2 = _song(song_id="test_song_0072_a1b2c3d4", title="恩典之路 (Live)")
    rec1 = _recording(song_id="test_song_0044_a1b2c3d4", bpm=72.0)
    rec2 = _recording(song_id="test_song_0072_a1b2c3d4", bpm=80.0)
    read_client = FakeReadClient(
        search_results={"恩典之路": [song1, song2]},
        recordings={song1.id: [rec1], song2.id: [rec2]},
    )
    console = MagicMock()

    monkeypatch.setattr("stream_of_worship.admin.commands._songset_create_helpers.typer.prompt", lambda *a, **k: "1")

    result_song, _result_recording = resolve_song_token(
        "恩典之路", read_client, console, non_interactive=False
    )
    assert result_song.id == "test_song_0044_a1b2c3d4"


def test_resolve_token_no_active_recordings_exits():
    song = _song()
    read_client = FakeReadClient(
        songs={song.id: song},
        recordings={},
    )
    console = MagicMock()

    with pytest.raises(typer.Exit):
        resolve_song_token(song.id, read_client, console, non_interactive=False)


def test_resolve_token_multiple_recordings_picks_latest():
    song = _song()
    old_rec = _recording(hash_prefix="oldoldold000", imported_at="2024-01-01T00:00:00")
    new_rec = _recording(hash_prefix="newnewnew000", imported_at="2024-06-01T00:00:00")
    read_client = FakeReadClient(
        songs={song.id: song},
        recordings={song.id: [new_rec, old_rec]},  # already DESC
    )
    console = MagicMock()

    _, result_recording = resolve_song_token(
        song.id, read_client, console, non_interactive=False
    )
    assert result_recording.hash_prefix == "newnewnew000"


def test_resolve_token_missing_duration_exits():
    song = _song()
    recording = _recording(duration=None)
    read_client = FakeReadClient(
        songs={song.id: song},
        recordings={song.id: [recording]},
    )
    console = MagicMock()

    with pytest.raises(typer.Exit):
        resolve_song_token(song.id, read_client, console, non_interactive=False)


def test_resolve_token_title_pinyin_match():
    song = _song(song_id="test_song_0050_a1b2c3d4", title="恩典之路")
    recording = _recording(song_id="test_song_0050_a1b2c3d4")
    read_client = FakeReadClient(
        search_results={"endian": [song]},
        recordings={song.id: [recording]},
    )
    console = MagicMock()

    result_song, _ = resolve_song_token(
        "endian", read_client, console, non_interactive=False
    )
    assert result_song.id == "test_song_0050_a1b2c3d4"


# ---------------------------------------------------------------------------
# create_songset (CLI-level tests via CliRunner)
# ---------------------------------------------------------------------------


def test_create_songset_no_user_no_env_exits():
    config = _make_config()
    with (
        patch("stream_of_worship.admin.commands.songset.AdminConfig.load", return_value=config),
        patch.dict(os.environ, {}, clear=False),
    ):
        os.environ.pop("SOW_DEFAULT_USER", None)
        result = runner.invoke(app, ["songset", "create", "test_song_a1b2c3d4"])

    assert result.exit_code == 1
    assert "No user specified" in result.output


def test_create_songset_env_var_user(monkeypatch):
    song = _song()
    recording = _recording()
    read_client = FakeReadClient(
        songs={song.id: song},
        recordings={song.id: [recording]},
    )
    songset_client = FakeSongsetClient()

    monkeypatch.setenv("SOW_DEFAULT_USER", "alice@example.com")

    with (
        patch("stream_of_worship.admin.commands.songset.AdminConfig.load", return_value=_make_config()),
        patch("stream_of_worship.admin.commands.songset._get_connection_provider", return_value=MagicMock()),
        patch("stream_of_worship.admin.commands.songset.UserClient") as uc_cls,
        patch("stream_of_worship.admin.commands.songset.ReadOnlyClient", return_value=read_client),
        patch("stream_of_worship.admin.commands.songset.SongsetClient", return_value=songset_client),
    ):
        from stream_of_worship.db.auth_models import User

        uc_cls.return_value.get_user_by_email.return_value = User(
            id=1, name="Alice", email="alice@example.com"
        )
        result = runner.invoke(
            app, ["songset", "create", "test_song_a1b2c3d4", "-y"]
        )

    assert result.exit_code == 0
    assert "✓ Created songset" in result.output
    assert len(songset_client.created) == 1


def test_create_songset_flag_user_takes_precedence(monkeypatch):
    song = _song()
    recording = _recording()
    read_client = FakeReadClient(
        songs={song.id: song},
        recordings={song.id: [recording]},
    )
    songset_client = FakeSongsetClient()

    monkeypatch.setenv("SOW_DEFAULT_USER", "bob@example.com")

    with (
        patch("stream_of_worship.admin.commands.songset.AdminConfig.load", return_value=_make_config()),
        patch("stream_of_worship.admin.commands.songset._get_connection_provider", return_value=MagicMock()),
        patch("stream_of_worship.admin.commands.songset.UserClient") as uc_cls,
        patch("stream_of_worship.admin.commands.songset.ReadOnlyClient", return_value=read_client),
        patch("stream_of_worship.admin.commands.songset.SongsetClient", return_value=songset_client),
    ):
        from stream_of_worship.db.auth_models import User

        uc_cls.return_value.get_user_by_email.return_value = User(
            id=1, name="Alice", email="alice@example.com"
        )
        result = runner.invoke(
            app, ["songset", "create", "-u", "alice@example.com", "test_song_a1b2c3d4", "-y"]
        )

    assert result.exit_code == 0
    uc_cls.return_value.get_user_by_email.assert_called_with("alice@example.com")


def test_create_songset_auto_name_from_titles(monkeypatch):
    song1 = _song(song_id="test_song_a1b2c3d4", title="信實偉大")
    song2 = _song(song_id="test_song_0002_a1b2c3d4", title="恩典之路")
    rec1 = _recording(song_id="test_song_a1b2c3d4", hash_prefix="hash000000001")
    rec2 = _recording(song_id="test_song_0002_a1b2c3d4", hash_prefix="hash000000002")
    read_client = FakeReadClient(
        songs={song1.id: song1, song2.id: song2},
        recordings={song1.id: [rec1], song2.id: [rec2]},
    )
    songset_client = FakeSongsetClient()

    with (
        patch("stream_of_worship.admin.commands.songset.AdminConfig.load", return_value=_make_config()),
        patch("stream_of_worship.admin.commands.songset._get_connection_provider", return_value=MagicMock()),
        patch("stream_of_worship.admin.commands.songset.UserClient") as uc_cls,
        patch("stream_of_worship.admin.commands.songset.ReadOnlyClient", return_value=read_client),
        patch("stream_of_worship.admin.commands.songset.SongsetClient", return_value=songset_client),
    ):
        from stream_of_worship.db.auth_models import User

        uc_cls.return_value.get_user_by_email.return_value = User(
            id=1, name="Alice", email="alice@example.com"
        )
        result = runner.invoke(
            app,
            ["songset", "create", "-u", "alice@example.com", "test_song_a1b2c3d4", "test_song_0002_a1b2c3d4", "-y"],
        )

    assert result.exit_code == 0
    assert songset_client.created[0]["name"] == "信實偉大_恩典之路"


def test_create_songset_explicit_name(monkeypatch):
    song = _song()
    recording = _recording()
    read_client = FakeReadClient(
        songs={song.id: song},
        recordings={song.id: [recording]},
    )
    songset_client = FakeSongsetClient()

    with (
        patch("stream_of_worship.admin.commands.songset.AdminConfig.load", return_value=_make_config()),
        patch("stream_of_worship.admin.commands.songset._get_connection_provider", return_value=MagicMock()),
        patch("stream_of_worship.admin.commands.songset.UserClient") as uc_cls,
        patch("stream_of_worship.admin.commands.songset.ReadOnlyClient", return_value=read_client),
        patch("stream_of_worship.admin.commands.songset.SongsetClient", return_value=songset_client),
    ):
        from stream_of_worship.db.auth_models import User

        uc_cls.return_value.get_user_by_email.return_value = User(
            id=1, name="Alice", email="alice@example.com"
        )
        result = runner.invoke(
            app,
            ["songset", "create", "-u", "alice@example.com", "-n", "Custom", "test_song_a1b2c3d4", "-y"],
        )

    assert result.exit_code == 0
    assert songset_client.created[0]["name"] == "Custom"


def test_create_songset_oversize_count_exits_early(monkeypatch):
    monkeypatch.setenv("SOW_DEFAULT_USER", "alice@example.com")

    with (
        patch("stream_of_worship.admin.commands.songset.AdminConfig.load", return_value=_make_config()),
        patch("stream_of_worship.admin.commands.songset._get_connection_provider", return_value=MagicMock()),
        patch("stream_of_worship.admin.commands.songset.UserClient") as uc_cls,
    ):
        from stream_of_worship.db.auth_models import User

        uc_cls.return_value.get_user_by_email.return_value = User(
            id=1, name="Alice", email="alice@example.com"
        )
        result = runner.invoke(
            app,
            [
                "songset", "create", "-u", "alice@example.com",
                "test_song_a1b2c3d4", "test_song_0002_a1b2c3d4", "test_song_0003_a1b2c3d4",
                "test_song_0004_a1b2c3d4", "test_song_0005_a1b2c3d4", "test_song_0006_a1b2c3d4",
            ],
        )

    assert result.exit_code == 1
    assert "exceeds maximum of 5 songs" in result.output


def test_create_songset_oversize_duration_exits(monkeypatch):
    song_ids = [
        "test_song_a1b2c3d4",
        "test_song_0002_a1b2c3d4",
        "test_song_0003_a1b2c3d4",
        "test_song_0004_a1b2c3d4",
        "test_song_0005_a1b2c3d4",
    ]
    songs = {sid: _song(song_id=sid, title=f"Song{i}") for i, sid in enumerate(song_ids, 1)}
    recordings = {
        sid: [_recording(song_id=sid, hash_prefix=f"hash00000000{i}", duration=320.0)]
        for i, sid in enumerate(song_ids, 1)
    }
    read_client = FakeReadClient(songs=songs, recordings=recordings)

    with (
        patch("stream_of_worship.admin.commands.songset.AdminConfig.load", return_value=_make_config()),
        patch("stream_of_worship.admin.commands.songset._get_connection_provider", return_value=MagicMock()),
        patch("stream_of_worship.admin.commands.songset.UserClient") as uc_cls,
        patch("stream_of_worship.admin.commands.songset.ReadOnlyClient", return_value=read_client),
        patch("stream_of_worship.admin.commands.songset.SongsetClient", return_value=FakeSongsetClient()),
    ):
        from stream_of_worship.db.auth_models import User

        uc_cls.return_value.get_user_by_email.return_value = User(
            id=1, name="Alice", email="alice@example.com"
        )
        result = runner.invoke(
            app,
            [
                "songset", "create", "-u", "alice@example.com", "-y",
                "test_song_a1b2c3d4", "test_song_0002_a1b2c3d4", "test_song_0003_a1b2c3d4", "test_song_0004_a1b2c3d4", "test_song_0005_a1b2c3d4",
            ],
        )

    assert result.exit_code == 1
    assert "exceeds maximum duration" in result.output


def test_create_songset_dry_run_no_persist(monkeypatch):
    song = _song()
    recording = _recording()
    read_client = FakeReadClient(
        songs={song.id: song},
        recordings={song.id: [recording]},
    )
    songset_client = FakeSongsetClient()

    with (
        patch("stream_of_worship.admin.commands.songset.AdminConfig.load", return_value=_make_config()),
        patch("stream_of_worship.admin.commands.songset._get_connection_provider", return_value=MagicMock()),
        patch("stream_of_worship.admin.commands.songset.UserClient") as uc_cls,
        patch("stream_of_worship.admin.commands.songset.ReadOnlyClient", return_value=read_client),
        patch("stream_of_worship.admin.commands.songset.SongsetClient", return_value=songset_client),
    ):
        from stream_of_worship.db.auth_models import User

        uc_cls.return_value.get_user_by_email.return_value = User(
            id=1, name="Alice", email="alice@example.com"
        )
        result = runner.invoke(
            app,
            ["songset", "create", "-u", "alice@example.com", "--dry-run", "test_song_a1b2c3d4", "-y"],
        )

    assert result.exit_code == 0
    assert "Dry run" in result.output
    assert len(songset_client.created) == 0


def test_create_songset_missing_reference_error(monkeypatch):
    song = _song()
    recording = _recording()
    read_client = FakeReadClient(
        songs={song.id: song},
        recordings={song.id: [recording]},
    )
    songset_client = FakeSongsetClient()
    songset_client.fail_with = MissingReferenceError(
        "Recordings not found: ['abc123def456']", "recording", "abc123def456"
    )

    with (
        patch("stream_of_worship.admin.commands.songset.AdminConfig.load", return_value=_make_config()),
        patch("stream_of_worship.admin.commands.songset._get_connection_provider", return_value=MagicMock()),
        patch("stream_of_worship.admin.commands.songset.UserClient") as uc_cls,
        patch("stream_of_worship.admin.commands.songset.ReadOnlyClient", return_value=read_client),
        patch("stream_of_worship.admin.commands.songset.SongsetClient", return_value=songset_client),
    ):
        from stream_of_worship.db.auth_models import User

        uc_cls.return_value.get_user_by_email.return_value = User(
            id=1, name="Alice", email="alice@example.com"
        )
        result = runner.invoke(
            app,
            ["songset", "create", "-u", "alice@example.com", "test_song_a1b2c3d4", "-y"],
        )

    assert result.exit_code == 1
    assert "Persistence failed" in result.output


def test_create_songset_generic_exception(monkeypatch):
    song = _song()
    recording = _recording()
    read_client = FakeReadClient(
        songs={song.id: song},
        recordings={song.id: [recording]},
    )
    songset_client = FakeSongsetClient()
    songset_client.fail_with = RuntimeError("FK violation")

    with (
        patch("stream_of_worship.admin.commands.songset.AdminConfig.load", return_value=_make_config()),
        patch("stream_of_worship.admin.commands.songset._get_connection_provider", return_value=MagicMock()),
        patch("stream_of_worship.admin.commands.songset.UserClient") as uc_cls,
        patch("stream_of_worship.admin.commands.songset.ReadOnlyClient", return_value=read_client),
        patch("stream_of_worship.admin.commands.songset.SongsetClient", return_value=songset_client),
    ):
        from stream_of_worship.db.auth_models import User

        uc_cls.return_value.get_user_by_email.return_value = User(
            id=1, name="Alice", email="alice@example.com"
        )
        result = runner.invoke(
            app,
            ["songset", "create", "-u", "alice@example.com", "test_song_a1b2c3d4", "-y"],
        )

    assert result.exit_code == 1
    assert "Persistence failed" in result.output


def test_create_songset_duplicate_song_warns(monkeypatch):
    song = _song()
    recording = _recording()
    read_client = FakeReadClient(
        songs={song.id: song},
        recordings={song.id: [recording]},
    )
    songset_client = FakeSongsetClient()

    with (
        patch("stream_of_worship.admin.commands.songset.AdminConfig.load", return_value=_make_config()),
        patch("stream_of_worship.admin.commands.songset._get_connection_provider", return_value=MagicMock()),
        patch("stream_of_worship.admin.commands.songset.UserClient") as uc_cls,
        patch("stream_of_worship.admin.commands.songset.ReadOnlyClient", return_value=read_client),
        patch("stream_of_worship.admin.commands.songset.SongsetClient", return_value=songset_client),
    ):
        from stream_of_worship.db.auth_models import User

        uc_cls.return_value.get_user_by_email.return_value = User(
            id=1, name="Alice", email="alice@example.com"
        )
        result = runner.invoke(
            app,
            ["songset", "create", "-u", "alice@example.com", "test_song_a1b2c3d4", "test_song_a1b2c3d4", "-y"],
        )

    assert result.exit_code == 0
    assert "appears multiple times" in result.output
    assert "(dup)" in result.output
    assert len(songset_client.created[0]["items"]) == 2


def test_create_songset_yes_ambiguous_title_exits(monkeypatch):
    song1 = _song(song_id="test_song_0044_a1b2c3d4", title="恩典之路")
    song2 = _song(song_id="test_song_0072_a1b2c3d4", title="恩典之路 (Live)")
    read_client = FakeReadClient(
        search_results={"恩典之路": [song1, song2]},
    )

    with (
        patch("stream_of_worship.admin.commands.songset.AdminConfig.load", return_value=_make_config()),
        patch("stream_of_worship.admin.commands.songset._get_connection_provider", return_value=MagicMock()),
        patch("stream_of_worship.admin.commands.songset.UserClient") as uc_cls,
        patch("stream_of_worship.admin.commands.songset.ReadOnlyClient", return_value=read_client),
        patch("stream_of_worship.admin.commands.songset.SongsetClient", return_value=FakeSongsetClient()),
    ):
        from stream_of_worship.db.auth_models import User

        uc_cls.return_value.get_user_by_email.return_value = User(
            id=1, name="Alice", email="alice@example.com"
        )
        result = runner.invoke(
            app,
            ["songset", "create", "-u", "alice@example.com", "恩典之路", "-y"],
        )

    assert result.exit_code == 1
    assert "Multiple matches" in result.output


def test_create_songset_name_collision_auto_suffix(monkeypatch):
    song = _song()
    recording = _recording()
    read_client = FakeReadClient(
        songs={song.id: song},
        recordings={song.id: [recording]},
    )
    existing = Songset(id="ss_old", user_id=1, name="TestSong", created_at="", updated_at="")
    songset_client = FakeSongsetClient(existing_songsets=[existing])

    with (
        patch("stream_of_worship.admin.commands.songset.AdminConfig.load", return_value=_make_config()),
        patch("stream_of_worship.admin.commands.songset._get_connection_provider", return_value=MagicMock()),
        patch("stream_of_worship.admin.commands.songset.UserClient") as uc_cls,
        patch("stream_of_worship.admin.commands.songset.ReadOnlyClient", return_value=read_client),
        patch("stream_of_worship.admin.commands.songset.SongsetClient", return_value=songset_client),
    ):
        from stream_of_worship.db.auth_models import User

        uc_cls.return_value.get_user_by_email.return_value = User(
            id=1, name="Alice", email="alice@example.com"
        )
        result = runner.invoke(
            app,
            ["songset", "create", "-u", "alice@example.com", "test_song_a1b2c3d4", "-y"],
        )

    assert result.exit_code == 0
    assert songset_client.created[0]["name"] == "TestSong_2"


def test_create_songset_user_not_found_exits(monkeypatch):
    with (
        patch("stream_of_worship.admin.commands.songset.AdminConfig.load", return_value=_make_config()),
        patch("stream_of_worship.admin.commands.songset._get_connection_provider", return_value=MagicMock()),
        patch("stream_of_worship.admin.commands.songset.UserClient") as uc_cls,
    ):
        uc_cls.return_value.get_user_by_email.return_value = None
        result = runner.invoke(
            app,
            ["songset", "create", "-u", "nobody@example.com", "test_song_a1b2c3d4", "-y"],
        )

    assert result.exit_code == 1
    assert "User not found" in result.output
