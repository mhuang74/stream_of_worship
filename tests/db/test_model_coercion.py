"""Unit tests for _to_str() datetime coercion in model from_row() methods.

No Docker required; tests use plain tuples to simulate database rows.
"""

from datetime import datetime, timezone

import pytest

from stream_of_worship.admin.db.models import Song, Recording
from stream_of_worship.admin.db.models import _to_str
from stream_of_worship.app.db.models import Songset, SongsetItem
from stream_of_worship.app.db.models import _to_str as _app_to_str


class TestToStrCoercion:
    """Tests for the _to_str helper function."""

    def test_none_returns_none(self):
        """Verify None is passed through."""
        assert _to_str(None) is None

    def test_string_unchanged(self):
        """Verify strings are passed through unchanged."""
        assert _to_str("hello") == "hello"

    def test_datetime_with_tz(self):
        """Verify datetime with timezone is converted to ISO format."""
        dt = datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
        result = _to_str(dt)
        assert result == "2024-01-15T10:30:00+00:00"

    def test_datetime_without_tz(self):
        """Verify naive datetime is converted to ISO format."""
        dt = datetime(2024, 1, 15, 10, 30, 0)
        result = _to_str(dt)
        assert result == "2024-01-15T10:30:00"

    def test_integer_converted(self):
        """Verify integers are converted to strings."""
        assert _to_str(42) == "42"


class TestSongFromRow:
    """Tests for Song.from_row() with datetime values."""

    @pytest.fixture
    def base_song_row(self):
        """Return a base row tuple for a song."""
        return (
            "song_0001",
            "Test Song",
            "title_pinyin",
            "Composer",
            "Lyricist",
            "Album",
            "Series",
            "G",
            "Lyrics raw",
            None,
            None,
            "http://example.com",
            1,
            "2024-01-01T00:00:00",
        )

    def test_from_row_with_string_timestamps(self, base_song_row):
        """Verify Song.from_row works with string timestamps."""
        row = base_song_row + ("2024-01-15T10:00:00", "2024-01-16T11:00:00", None)
        song = Song.from_row(row)

        assert song.created_at == "2024-01-15T10:00:00"
        assert song.updated_at == "2024-01-16T11:00:00"
        assert song.deleted_at is None

    def test_from_row_with_datetime_timestamps(self, base_song_row):
        """Verify Song.from_row works with datetime objects."""
        created_dt = datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
        updated_dt = datetime(2024, 1, 16, 11, 0, 0, tzinfo=timezone.utc)
        row = base_song_row + (created_dt, updated_dt, None)
        song = Song.from_row(row)

        assert song.created_at == "2024-01-15T10:00:00+00:00"
        assert song.updated_at == "2024-01-16T11:00:00+00:00"
        assert song.deleted_at is None

    def test_from_row_with_deleted_at_datetime(self, base_song_row):
        """Verify Song.from_row works with datetime deleted_at."""
        deleted_dt = datetime(2024, 2, 1, 0, 0, 0, tzinfo=timezone.utc)
        row = base_song_row + ("2024-01-15T10:00:00", "2024-01-16T11:00:00", deleted_dt)
        song = Song.from_row(row)

        assert song.deleted_at == "2024-02-01T00:00:00+00:00"


class TestRecordingFromRow:
    """Tests for Recording.from_row() with datetime values."""

    @pytest.fixture
    def base_recording_row(self):
        """Return a base row tuple for a recording (23 columns, before youtube_url)."""
        return (
            "content_hash_64_chars_long_______",
            "hash_prefix",
            "song_0001",
            "test.mp3",
            1000,
            "2024-01-01T00:00:00",
            None,
            None,
            None,
            180.0,
            120.0,
            "G",
            "major",
            0.95,
            -14.0,
            None,
            None,
            None,
            None,
            "completed",
            None,
            "completed",
            None,
        )

    def test_from_row_29_columns_with_datetime(self, base_recording_row):
        """Verify Recording.from_row with 29 columns and datetime timestamps."""
        created_dt = datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
        updated_dt = datetime(2024, 1, 16, 11, 0, 0, tzinfo=timezone.utc)
        row = base_recording_row + (created_dt, updated_dt, None, None, None, None)
        # Add download_status at position 28
        row = base_recording_row + (created_dt, updated_dt, None, None, None, None)

        recording = Recording.from_row(row)

        assert recording.created_at == "2024-01-15T10:00:00+00:00"
        assert recording.updated_at == "2024-01-16T11:00:00+00:00"


class TestSongsetFromRow:
    """Tests for Songset.from_row() with datetime values."""

    def test_from_row_with_string_timestamps(self):
        """Verify Songset.from_row works with string timestamps."""
        row = (
            "songset_1",
            "My Songset",
            "Description",
            "2024-01-15T10:00:00",
            "2024-01-16T11:00:00",
        )
        songset = Songset.from_row(row)

        assert songset.created_at == "2024-01-15T10:00:00"
        assert songset.updated_at == "2024-01-16T11:00:00"

    def test_from_row_with_datetime_timestamps(self):
        """Verify Songset.from_row works with datetime objects."""
        created_dt = datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
        updated_dt = datetime(2024, 1, 16, 11, 0, 0, tzinfo=timezone.utc)
        row = ("songset_1", "My Songset", "Description", created_dt, updated_dt)
        songset = Songset.from_row(row)

        assert songset.created_at == "2024-01-15T10:00:00+00:00"
        assert songset.updated_at == "2024-01-16T11:00:00+00:00"


class TestSongsetItemFromRow:
    """Tests for SongsetItem.from_row() with datetime values."""

    def test_from_row_with_string_created_at(self):
        """Verify SongsetItem.from_row works with string created_at."""
        row = (
            "item_1",
            "songset_1",
            "song_0001",
            "abc123",
            0,
            2.0,
            0,
            None,
            0,
            1.0,
            "2024-01-15T10:00:00",
        )
        item = SongsetItem.from_row(row)
        assert item.created_at == "2024-01-15T10:00:00"

    def test_from_row_with_datetime_created_at(self):
        """Verify SongsetItem.from_row works with datetime created_at."""
        created_dt = datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
        row = (
            "item_1",
            "songset_1",
            "song_0001",
            "abc123",
            0,
            2.0,
            0,
            None,
            0,
            1.0,
            created_dt,
        )
        item = SongsetItem.from_row(row)
        assert item.created_at == "2024-01-15T10:00:00+00:00"
