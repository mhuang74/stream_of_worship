"""Tests for curated catalog insert/edit/recovery commands."""

from dataclasses import replace

from typer.testing import CliRunner

from stream_of_worship.admin.config import AdminConfig
from stream_of_worship.admin.db.models import Song
from stream_of_worship.admin.main import app
from stream_of_worship.admin.services.catalog_edit import build_song_from_review
from stream_of_worship.admin.services.catalog_edit import normalize_reviewed_data

runner = CliRunner()


def _fake_config() -> AdminConfig:
    return AdminConfig(
        database_url="postgresql://example.invalid/sow",
        r2_bucket="test-bucket",
        r2_endpoint_url="https://test.r2.dev",
        r2_region="auto",
    )


class FakeDbClient:
    def __init__(self):
        self.songs: dict[str, Song] = {}
        self.recordings = {}

    def insert_song(self, song: Song) -> None:
        self.songs[song.id] = song

    def get_song(self, song_id: str, include_deleted: bool = False):
        song = self.songs.get(song_id)
        if not include_deleted and song and song.deleted_at is not None:
            return None
        return song

    def find_song_by_source_url(self, source_url: str, include_deleted: bool = False):
        for song in self.songs.values():
            if song.source_url == source_url and (include_deleted or song.deleted_at is None):
                return song
        return None

    def get_recording_by_song_id(self, song_id: str):
        return self.recordings.get(song_id)

    def update_song(self, song: Song) -> bool:
        if song.id not in self.songs:
            return False
        self.songs[song.id] = song
        return True

    def list_songs(self, **kwargs):
        return [song for song in self.songs.values() if song.deleted_at is None]

    def list_deleted_songs(self):
        return [song for song in self.songs.values() if song.deleted_at is not None]

    def soft_delete_song(self, song_id: str):
        song = self.songs[song_id]
        self.songs[song_id] = replace(song, deleted_at="2026-06-15T00:00:00")
        return True

    def list_recordings_by_song_id(self, song_id: str, include_deleted: bool = False):
        return []

    def count_songset_references(self, song_id: str) -> int:
        return 0

    def hold_recordings_for_song(self, song_id: str) -> int:
        return 0


def test_catalog_insert_manual_inserts_song(monkeypatch):
    db = FakeDbClient()
    monkeypatch.setattr("stream_of_worship.admin.commands.catalog.AdminConfig.load", lambda path=None: _fake_config())
    monkeypatch.setattr("stream_of_worship.admin.commands.catalog.get_db_client", lambda config: db)
    monkeypatch.setattr(
        "stream_of_worship.admin.commands.catalog._review_song_fields",
        lambda initial_fields, comments=None: normalize_reviewed_data(
            {
                "title": "Manual Song",
                "composer": "Composer",
                "lyricist": "",
                "album_name": "Album",
                "album_series": "",
                "musical_key": "",
                "source_url": "https://example.com/manual-song",
                "lyrics_raw": "Line 1\nLine 2",
            }
        ),
    )

    result = runner.invoke(app, ["catalog", "insert"], input="y\n")

    assert result.exit_code == 0
    inserted = db.find_song_by_source_url("https://example.com/manual-song")
    assert inserted is not None
    assert inserted.title == "Manual Song"
    assert db.get_recording_by_song_id(inserted.id) is None


def test_catalog_insert_youtube_dry_run_does_not_write(monkeypatch):
    db = FakeDbClient()
    monkeypatch.setattr("stream_of_worship.admin.commands.catalog.AdminConfig.load", lambda path=None: _fake_config())
    monkeypatch.setattr("stream_of_worship.admin.commands.catalog.get_db_client", lambda config: db)
    monkeypatch.setattr(
        "stream_of_worship.admin.commands.catalog.extract_video_metadata",
        lambda url: type(
            "Meta",
            (),
            {
                "title": "Here I Bow - Brian & Jenn Johnson | After All These Years",
                "webpage_url": "https://youtube.com/watch?v=test123",
                "duration": 245,
            },
        )(),
    )
    monkeypatch.setattr(
        "stream_of_worship.admin.commands.catalog._fetch_transcript_draft",
        lambda url: type("Draft", (), {"source": "YouTube English (auto-generated)", "lines": ["Line 1", "Line 2"]})(),
    )
    monkeypatch.setattr(
        "stream_of_worship.admin.commands.catalog._review_song_fields",
        lambda initial_fields, comments=None: normalize_reviewed_data(initial_fields),
    )

    result = runner.invoke(
        app,
        ["catalog", "insert", "--youtube", "https://youtube.com/watch?v=test123", "--dry-run"],
    )

    assert result.exit_code == 0
    assert "Planned audio download URL" in result.output
    assert db.list_songs() == []


def test_catalog_insert_duplicate_source_url_exits_before_audio(monkeypatch):
    db = FakeDbClient()
    monkeypatch.setattr("stream_of_worship.admin.commands.catalog.AdminConfig.load", lambda path=None: _fake_config())
    monkeypatch.setattr("stream_of_worship.admin.commands.catalog.get_db_client", lambda config: db)
    monkeypatch.setattr(
        "stream_of_worship.admin.commands.catalog._review_song_fields",
        lambda initial_fields, comments=None: normalize_reviewed_data(
            {
                "title": "Existing Song",
                "composer": "",
                "lyricist": "",
                "album_name": "",
                "album_series": "",
                "musical_key": "",
                "source_url": "https://example.com/existing-song",
                "lyrics_raw": "",
            }
        ),
    )

    existing = normalize_reviewed_data(
        {
            "title": "Existing Song",
            "composer": "",
            "lyricist": "",
            "album_name": "",
            "album_series": "",
            "musical_key": "",
            "source_url": "https://example.com/existing-song",
            "lyrics_raw": "",
        }
    )
    db.insert_song(build_song_from_review(existing, existing_song_id="existing_song_deadbeef"))

    result = runner.invoke(app, ["catalog", "insert"])

    assert result.exit_code == 1
    assert "Source URL already exists" in result.output


def test_catalog_edit_preserves_song_id_and_prints_follow_up(monkeypatch):
    db = FakeDbClient()
    original = normalize_reviewed_data(
        {
            "title": "Editable Song",
            "composer": "Composer",
            "lyricist": "",
            "album_name": "",
            "album_series": "",
            "musical_key": "",
            "source_url": "https://example.com/editable-song",
            "lyrics_raw": "Old Line",
        }
    )
    db.insert_song(build_song_from_review(original, existing_song_id="editable_song_deadbeef"))

    monkeypatch.setattr("stream_of_worship.admin.commands.catalog.AdminConfig.load", lambda path=None: _fake_config())
    monkeypatch.setattr("stream_of_worship.admin.commands.catalog.get_db_client", lambda config: db)
    monkeypatch.setattr(
        "stream_of_worship.admin.commands.catalog._review_song_fields",
        lambda initial_fields, comments=None: normalize_reviewed_data(
            {
                **initial_fields,
                "title": "Editable Song Revised",
                "lyrics_raw": "New Line",
            }
        ),
    )

    result = runner.invoke(app, ["catalog", "edit", "editable_song_deadbeef"], input="y\n")

    assert result.exit_code == 0
    updated = db.get_song("editable_song_deadbeef")
    assert updated is not None
    assert updated.title == "Editable Song Revised"
    assert "audio lrc editable_song_deadbeef --force" in result.output


def test_catalog_list_deleted_only_shows_soft_deleted(monkeypatch):
    db = FakeDbClient()
    active = build_song_from_review(
        normalize_reviewed_data(
            {
                "title": "Active Song",
                "composer": "",
                "lyricist": "",
                "album_name": "",
                "album_series": "",
                "musical_key": "",
                "source_url": "https://example.com/active",
                "lyrics_raw": "",
            }
        ),
        existing_song_id="active_song_deadbeef",
    )
    deleted = build_song_from_review(
        normalize_reviewed_data(
            {
                "title": "Deleted Song",
                "composer": "",
                "lyricist": "",
                "album_name": "",
                "album_series": "",
                "musical_key": "",
                "source_url": "https://example.com/deleted",
                "lyrics_raw": "",
            }
        ),
        existing_song_id="deleted_song_deadbeef",
    )
    db.insert_song(active)
    db.insert_song(deleted)
    db.soft_delete_song("deleted_song_deadbeef")

    monkeypatch.setattr("stream_of_worship.admin.commands.catalog.AdminConfig.load", lambda path=None: _fake_config())
    monkeypatch.setattr("stream_of_worship.admin.commands.catalog.get_db_client", lambda config: db)

    result = runner.invoke(app, ["catalog", "list", "--deleted", "--format", "ids"])

    assert result.exit_code == 0
    assert "deleted_song_deadbeef" in result.output
    assert "active_song_deadbeef" not in result.output


def _build_song(song_id: str, title: str = "Test Song") -> Song:
    return build_song_from_review(
        normalize_reviewed_data(
            {
                "title": title,
                "composer": "",
                "lyricist": "",
                "album_name": "",
                "album_series": "",
                "musical_key": "",
                "source_url": f"https://example.com/{song_id}",
                "lyrics_raw": "",
            }
        ),
        existing_song_id=song_id,
    )


def test_catalog_delete_single_soft_deletes_song(monkeypatch):
    db = FakeDbClient()
    song = _build_song("delete_me_deadbeef", "Delete Me")
    db.insert_song(song)

    monkeypatch.setattr("stream_of_worship.admin.commands.catalog.AdminConfig.load", lambda path=None: _fake_config())
    monkeypatch.setattr("stream_of_worship.admin.commands.catalog.get_db_client", lambda config: db)

    result = runner.invoke(app, ["catalog", "delete", "delete_me_deadbeef", "--yes"])

    assert result.exit_code == 0
    assert "soft-deleted successfully" in result.output
    updated = db.get_song("delete_me_deadbeef", include_deleted=True)
    assert updated is not None
    assert updated.deleted_at is not None


def test_catalog_delete_already_deleted_song_errors(monkeypatch):
    db = FakeDbClient()
    song = _build_song("already_deleted_deadbeef", "Already Deleted")
    db.insert_song(song)
    db.soft_delete_song("already_deleted_deadbeef")

    monkeypatch.setattr("stream_of_worship.admin.commands.catalog.AdminConfig.load", lambda path=None: _fake_config())
    monkeypatch.setattr("stream_of_worship.admin.commands.catalog.get_db_client", lambda config: db)

    result = runner.invoke(app, ["catalog", "delete", "already_deleted_deadbeef", "--yes"])

    assert result.exit_code == 1
    assert "already soft-deleted" in result.output


def test_catalog_delete_not_found_errors(monkeypatch):
    db = FakeDbClient()

    monkeypatch.setattr("stream_of_worship.admin.commands.catalog.AdminConfig.load", lambda path=None: _fake_config())
    monkeypatch.setattr("stream_of_worship.admin.commands.catalog.get_db_client", lambda config: db)

    result = runner.invoke(app, ["catalog", "delete", "nonexistent_deadbeef", "--yes"])

    assert result.exit_code == 1
    assert "not found" in result.output


def test_catalog_delete_no_args_no_stdin_errors(monkeypatch):
    db = FakeDbClient()

    monkeypatch.setattr("stream_of_worship.admin.commands.catalog.AdminConfig.load", lambda path=None: _fake_config())
    monkeypatch.setattr("stream_of_worship.admin.commands.catalog.get_db_client", lambda config: db)

    result = runner.invoke(app, ["catalog", "delete"])

    assert result.exit_code == 1
    assert "song_id" in result.output


def test_catalog_delete_batch_from_stdin(monkeypatch):
    db = FakeDbClient()
    song_a = _build_song("batch_a_deadbeef", "Batch A")
    song_b = _build_song("batch_b_deadbeef", "Batch B")
    db.insert_song(song_a)
    db.insert_song(song_b)

    monkeypatch.setattr("stream_of_worship.admin.commands.catalog.AdminConfig.load", lambda path=None: _fake_config())
    monkeypatch.setattr("stream_of_worship.admin.commands.catalog.get_db_client", lambda config: db)

    result = runner.invoke(
        app,
        ["catalog", "delete", "--stdin", "--yes"],
        input="batch_a_deadbeef\nbatch_b_deadbeef\n",
    )

    assert result.exit_code == 0
    assert "2 deleted" in result.output
    assert db.get_song("batch_a_deadbeef", include_deleted=True).deleted_at is not None
    assert db.get_song("batch_b_deadbeef", include_deleted=True).deleted_at is not None


def test_catalog_delete_batch_skips_already_deleted(monkeypatch):
    db = FakeDbClient()
    song_active = _build_song("batch_active_deadbeef", "Active")
    song_deleted = _build_song("batch_deleted_deadbeef", "Deleted")
    db.insert_song(song_active)
    db.insert_song(song_deleted)
    db.soft_delete_song("batch_deleted_deadbeef")

    monkeypatch.setattr("stream_of_worship.admin.commands.catalog.AdminConfig.load", lambda path=None: _fake_config())
    monkeypatch.setattr("stream_of_worship.admin.commands.catalog.get_db_client", lambda config: db)

    result = runner.invoke(
        app,
        ["catalog", "delete", "--stdin", "--yes"],
        input="batch_active_deadbeef\nbatch_deleted_deadbeef\n",
    )

    assert result.exit_code == 0
    assert "1 deleted" in result.output
    assert "Already soft-deleted" in result.output
