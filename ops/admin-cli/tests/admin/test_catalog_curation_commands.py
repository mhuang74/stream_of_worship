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


def test_catalog_list_deleted_only_shows_quarantined(monkeypatch):
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
