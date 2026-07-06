"""Unit tests for _to_str() datetime-to-string coercion.

Verifies that ``_to_str()`` correctly handles strings, datetime objects,
and ``None`` values from both ``admin.db.models`` and ``app.db.models``.
"""

from datetime import datetime, timezone

from stream_of_worship.admin.db.models import Recording, Song, _to_str
from stream_of_worship.db.app.models import Songset, SongsetItem


class TestToStr:
    """Unit tests for _to_str helper."""

    def test_to_str_with_none(self):
        """_to_str(None) should return None."""
        assert _to_str(None) is None

    def test_to_str_with_string(self):
        """_to_str should pass strings through unchanged."""
        assert _to_str("2024-01-01T00:00:00") == "2024-01-01T00:00:00"

    def test_to_str_with_datetime(self):
        """_to_str should convert datetime to ISO 8601 string."""
        dt = datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
        result = _to_str(dt)
        assert result == "2024-01-15T10:30:00+00:00"

    def test_to_str_with_naive_datetime(self):
        """_to_str should handle naive datetime objects."""
        dt = datetime(2024, 6, 1, 12, 0, 0)
        result = _to_str(dt)
        assert result == "2024-06-01T12:00:00"

    def test_to_str_with_int(self):
        """_to_str should convert non-datetime objects via str()."""
        assert _to_str(42) == "42"


class TestSongFromRowCoercion:
    """Test that Song.from_row() handles timestamptz datetime objects."""

    def test_from_row_with_string_timestamps(self):
        """Test with string timestamps (legacy string timestamp format)."""
        row = (
            "song_1",
            "Test Song",
            None,  # title_pinyin
            None,  # composer
            None,  # lyricist
            None,  # album_name
            None,  # album_series
            None,  # musical_key
            None,  # lyrics_raw
            None,  # lyrics_lines
            None,  # sections
            "http://test",
            None,  # table_row_number
            "2024-01-01T00:00:00",
            "2024-01-01T00:00:00",
            "2024-01-01T00:00:00",
            None,
        )
        song = Song.from_row(row)
        assert song.created_at == "2024-01-01T00:00:00"
        assert song.updated_at == "2024-01-01T00:00:00"
        assert song.deleted_at is None

    def test_from_row_with_datetime_timestamps(self):
        """Test with datetime objects (Postgres timestamptz)."""
        created = datetime(2024, 3, 15, 10, 30, 0, tzinfo=timezone.utc)
        updated = datetime(2024, 3, 15, 11, 0, 0, tzinfo=timezone.utc)
        row = (
            "song_1",
            "Test Song",
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            "http://test",
            None,
            "2024-01-01T00:00:00",
            created,
            updated,
            None,
        )
        song = Song.from_row(row)
        assert song.created_at == "2024-03-15T10:30:00+00:00"
        assert song.updated_at == "2024-03-15T11:00:00+00:00"
        assert song.deleted_at is None


class TestRecordingFromRowCoercion:
    """Test that Recording.from_row() handles timestamptz datetime objects."""

    def test_from_row_with_datetime_timestamps_29_cols(self):
        """Test Recording.from_row with datetime created_at/updated_at."""
        created = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        updated = datetime(2024, 6, 1, 0, 0, 0, tzinfo=timezone.utc)
        row = (
            "a" * 64,  # content_hash          [0]
            "abc123",  # hash_prefix           [1]
            "song_1",  # song_id               [2]
            "test.mp3",  # original_filename   [3]
            1000,  # file_size_bytes          [4]
            "2024-01-01T00:00:00",  # imported_at [5]
            None,  # r2_audio_url              [6]
            None,  # r2_stems_url              [7]
            None,  # r2_lrc_url                [8]
            180.0,  # duration_seconds         [9]
            120.0,  # tempo_bpm                [10]
            "G",  # musical_key                [11]
            "major",  # musical_mode           [12]
            0.9,  # key_confidence             [13]
            -8.0,  # loudness_db              [14]
            None,  # beats                     [15]
            None,  # downbeats                 [16]
            None,  # sections                  [17]
            None,  # embeddings_shape          [18]
            "completed",  # analysis_status    [19]
            None,  # analysis_job_id           [20]
            "completed",  # lrc_status         [21]
            None,  # lrc_job_id                [22]
            created,  # created_at             [23]
            updated,  # updated_at             [24]
            None,  # youtube_url               [25]
            "published",  # visibility_status  [26]
            "completed",  # download_status    [27]
            None,  # deleted_at                [28]
        )
        recording = Recording.from_row(row)
        assert recording.created_at == "2024-01-01T00:00:00+00:00"
        assert recording.updated_at == "2024-06-01T00:00:00+00:00"
        assert recording.youtube_url is None
        assert recording.visibility_status == "published"
        assert recording.deleted_at is None
        assert recording.download_status == "completed"


class TestSongsetFromRowCoercion:
    """Test that Songset.from_row() handles timestamptz datetime objects."""

    def test_from_row_with_datetime(self):
        """Test Songset.from_row with datetime objects."""
        created = datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
        updated = datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        row = ("set_1", 7, "My Set", "desc", created, updated)
        songset = Songset.from_row(row)
        assert songset.user_id == 7
        assert songset.created_at == "2024-01-15T10:00:00+00:00"
        assert songset.updated_at == "2024-01-15T12:00:00+00:00"


class TestSongsetItemFromRowCoercion:
    """Test that SongsetItem.from_row() handles timestamptz datetime objects."""

    def test_from_row_with_datetime(self):
        """Test SongsetItem.from_row with datetime created_at."""
        created = datetime(2024, 2, 1, 8, 0, 0, tzinfo=timezone.utc)
        row = (
            "item_1", "set_1", "song_1", "abc123", 0, 2.0, 0, None, 0, 1.0, created
        )
        item = SongsetItem.from_row(row)
        assert item.created_at == "2024-02-01T08:00:00+00:00"

    def test_from_row_detailed_with_datetime(self):
        """Test detailed SongsetItem.from_row with datetime."""
        created = datetime(2024, 2, 1, 8, 0, 0, tzinfo=timezone.utc)
        row = (
            "item_1", "set_1", "song_1", "abc123", 0, 2.0, 0, None, 0, 1.0,
            created, "Test Song", "G", 180.0, 120.0, "G", -8.0, "Composer",
            "Lyricist", "Album",
        )
        item = SongsetItem.from_row(row, detailed=True)
        assert item.created_at == "2024-02-01T08:00:00+00:00"
        assert item.song_title == "Test Song"
