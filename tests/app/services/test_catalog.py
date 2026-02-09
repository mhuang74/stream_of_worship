"""Tests for CatalogService.

Tests high-level catalog browsing operations.
"""

from unittest.mock import MagicMock, Mock

import pytest

from stream_of_worship.app.services.catalog import CatalogService, SongWithRecording
from stream_of_worship.admin.db.models import Song, Recording


@pytest.fixture
def mock_read_client():
    """Mocked ReadOnlyClient with predefined return values."""
    client = MagicMock()

    # Sample songs
    song1 = Song(
        id="song_0001",
        title="Amazing Grace",
        source_url="http://example.com/1",
        scraped_at="2024-01-01T00:00:00",
        album_name="Hymns",
        musical_key="G",
    )
    song2 = Song(
        id="song_0002",
        title="How Great Thou Art",
        source_url="http://example.com/2",
        scraped_at="2024-01-01T00:00:00",
        album_name="Hymns",
        musical_key="D",
    )
    song3 = Song(
        id="song_0003",
        title="Modern Song",
        source_url="http://example.com/3",
        scraped_at="2024-01-01T00:00:00",
        album_name="Modern",
        musical_key="C",
    )

    # Sample recordings
    recording1 = Recording(
        content_hash="abc123" * 8,
        hash_prefix="abc123def456",
        song_id="song_0001",
        original_filename="amazing_grace.mp3",
        file_size_bytes=5000000,
        imported_at="2024-01-01T00:00:00",
        duration_seconds=180.5,
        tempo_bpm=120.0,
        musical_key="G",
        musical_mode="major",
        analysis_status="completed",
        lrc_status="completed",
    )
    recording2 = Recording(
        content_hash="def456" * 8,
        hash_prefix="def456ghi789",
        song_id="song_0002",
        original_filename="how_great.mp3",
        file_size_bytes=6000000,
        imported_at="2024-01-01T00:00:00",
        duration_seconds=240.0,
        tempo_bpm=100.0,
        musical_key="D",
        musical_mode="major",
        analysis_status="completed",
        lrc_status="completed",
    )

    # Configure mock methods
    client.get_song.side_effect = lambda sid: {
        "song_0001": song1,
        "song_0002": song2,
        "song_0003": song3,
    }.get(sid)

    client.list_songs.return_value = [song1, song2, song3]

    client.search_songs.return_value = [song1, song2]

    client.get_recording_by_song_id.side_effect = lambda sid: {
        "song_0001": recording1,
        "song_0002": recording2,
    }.get(sid)

    client.list_albums.return_value = ["Hymns", "Modern"]

    client.list_keys.return_value = ["C", "D", "G"]

    client.get_song_count.return_value = 3
    client.get_recording_count.return_value = 2
    client.get_analyzed_recording_count.return_value = 2

    # Mock connection for raw SQL queries
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    # Initially return empty for SQL queries (list_available_keys will override this)
    mock_cursor.fetchall.return_value = []
    mock_conn.cursor.return_value = mock_cursor
    client.connection = mock_conn

    # Override fetchall for list_available_keys (which uses SQL)
    def mock_execute_and_fetchall(query, params=None):
        """Mock execute that checks the query and returns appropriate results."""
        # For list_available_keys query
        if "SELECT DISTINCT s.musical_key" in query:
            return [("C",), ("D",), ("G",)]
        return []

    # Store the original execute and fetchall
    original_execute = mock_cursor.execute
    original_fetchall = mock_cursor.fetchall

    # Track the last query for debugging
    last_query = []
    last_params = []

    def mock_execute(query, params=None):
        last_query.append(query)
        last_params.append(params)
        # Don't actually do anything

    def mock_fetchall_with_context():
        """Mock fetchall that returns results based on the last query."""
        if last_query and "SELECT DISTINCT s.musical_key" in last_query[-1]:
            return [("C",), ("D",), ("G",)]
        return []

    mock_cursor.execute = mock_execute
    mock_cursor.fetchall = mock_fetchall_with_context

    return client


@pytest.fixture
def catalog_service(mock_read_client):
    """CatalogService using mock_read_client."""
    return CatalogService(mock_read_client)


class TestGetSongWithRecording:
    """Tests for get_song_with_recording."""

    def test_get_song_with_recording_returns_combined(self, catalog_service):
        """Verify SongWithRecording creation."""
        result = catalog_service.get_song_with_recording("song_0001")

        assert result is not None
        assert isinstance(result, SongWithRecording)
        assert result.song.id == "song_0001"
        assert result.recording is not None
        assert result.recording.hash_prefix == "abc123def456"

    def test_get_song_with_recording_returns_none_for_missing(self, catalog_service):
        """Verify None when no song found."""
        result = catalog_service.get_song_with_recording("song_nonexistent")

        assert result is None

    def test_get_song_without_recording(self, catalog_service, mock_read_client):
        """Verify SongWithRecording when song has no recording."""
        mock_read_client.get_recording_by_song_id.return_value = None

        result = catalog_service.get_song_with_recording("song_0003")

        assert result is not None
        assert result.recording is None


class TestListSongsWithRecordings:
    """Tests for list_songs_with_recordings."""

    def test_list_songs_with_only_recordings_filter(self, catalog_service, mock_read_client):
        """Verify only_with_recordings filter."""
        # song_0003 has no recording
        result = catalog_service.list_songs_with_recordings(only_with_recordings=True)

        assert len(result) == 2
        assert all(r.recording is not None for r in result)

    def test_list_songs_with_recordings_returns_empty_when_none(self, catalog_service, mock_read_client):
        """Verify empty list handling."""
        mock_read_client.list_songs.return_value = []

        result = catalog_service.list_songs_with_recordings()

        assert result == []


class TestSearchSongs:
    """Tests for search operations."""

    def test_search_songs_finds_by_title(self, catalog_service, mock_read_client):
        """Verify title search."""
        result = catalog_service.search_songs_with_recordings("Amazing", field="title")

        mock_read_client.search_songs.assert_called_with("Amazing", field="title", limit=20)
        assert len(result) > 0

    def test_search_songs_finds_by_artist(self, catalog_service, mock_read_client):
        """Verify artist search."""
        result = catalog_service.search_songs_with_recordings("Grace", field="all")

        mock_read_client.search_songs.assert_called_with("Grace", field="all", limit=20)

    def test_search_songs_returns_empty_when_no_match(self, catalog_service, mock_read_client):
        """Verify no results handling."""
        mock_read_client.search_songs.return_value = []

        result = catalog_service.search_songs_with_recordings("xyz123")

        assert result == []


class TestListAvailable:
    """Tests for listing available albums and keys."""

    def test_list_available_albums_returns_unique(self, catalog_service):
        """Verify distinct albums."""
        result = catalog_service.list_available_albums()

        # Should filter to albums that have recordings
        assert isinstance(result, list)

    def test_list_available_keys_returns_unique(self, catalog_service):
        """Verify distinct keys."""
        result = catalog_service.list_available_keys()

        assert isinstance(result, list)
        assert "C" in result
        assert "D" in result
        assert "G" in result


class TestGetStats:
    """Tests for statistics."""

    def test_get_stats_returns_correct_counts(self, catalog_service):
        """Verify stats calculation."""
        stats = catalog_service.get_stats()

        assert stats["total_songs"] == 3
        assert stats["total_recordings"] == 2
        assert stats["analyzed_recordings"] == 2
        assert stats["analysis_coverage"] == "100.0%"

    def test_get_stats_handles_zero_recordings(self, catalog_service, mock_read_client):
        """Verify stats with no recordings."""
        mock_read_client.get_recording_count.return_value = 0

        stats = catalog_service.get_stats()

        assert stats["analysis_coverage"] == "N/A"


class TestSongWithRecordingProperties:
    """Tests for SongWithRecording helper properties."""

    def test_has_analysis_with_analysis(self, catalog_service):
        """Verify has_analysis property when analysis complete."""
        swr = catalog_service.get_song_with_recording("song_0001")

        assert swr.has_analysis is True
        assert swr.has_lrc is True

    def test_has_analysis_without_recording(self, catalog_service, mock_read_client):
        """Verify has_analysis when no recording."""
        mock_read_client.get_recording_by_song_id.return_value = None

        swr = catalog_service.get_song_with_recording("song_0003")

        assert swr.has_analysis is False
        assert swr.has_lrc is False

    def test_duration_seconds_property(self, catalog_service):
        """Verify duration_seconds property."""
        swr = catalog_service.get_song_with_recording("song_0001")

        assert swr.duration_seconds == 180.5

    def test_tempo_bpm_property(self, catalog_service):
        """Verify tempo_bpm property."""
        swr = catalog_service.get_song_with_recording("song_0001")

        assert swr.tempo_bpm == 120.0

    def test_display_key_from_recording(self, catalog_service):
        """Verify display_key uses recording key."""
        swr = catalog_service.get_song_with_recording("song_0001")

        assert swr.display_key == "G major"

    def test_display_key_fallback_to_song(self, catalog_service, mock_read_client):
        """Verify display_key falls back to song key."""
        recording = mock_read_client.get_recording_by_song_id("song_0001")
        recording.musical_key = None
        recording.musical_mode = None

        swr = catalog_service.get_song_with_recording("song_0001")

        assert swr.display_key == "G"

    def test_formatted_duration(self, catalog_service):
        """Verify formatted_duration property."""
        swr = catalog_service.get_song_with_recording("song_0001")

        assert swr.formatted_duration == "3:00"

    def test_formatted_duration_without_recording(self, catalog_service, mock_read_client):
        """Verify formatted_duration without recording."""
        mock_read_client.get_recording_by_song_id.return_value = None

        swr = catalog_service.get_song_with_recording("song_0003")

        assert swr.formatted_duration == "--:--"
