"""Tests for SongsetClient admin-level methods."""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from stream_of_worship.db.app.models import Songset, SongsetItem
from stream_of_worship.db.app.songset_client import SongsetClient


@pytest.fixture
def mock_connection_provider():
    """Create a mock ConnectionProvider."""
    provider = MagicMock()
    conn = MagicMock()
    provider.get_connection.return_value = conn
    return provider, conn


@pytest.fixture
def client(mock_connection_provider):
    """Create a SongsetClient with mocked connection."""
    provider, conn = mock_connection_provider
    return SongsetClient(provider, user_id=0), conn


class TestListAllSongsets:
    """Tests for SongsetClient.list_all_songsets."""

    def test_list_all_returns_songsets(self, client, mock_connection_provider):
        """Test list_all_songsets returns all songsets."""
        songset_client, conn = client
        cursor = conn.cursor.return_value
        cursor.fetchall.return_value = [
            ("ss1", 1, "My Songset", "Desc", "2024-01-01T00:00:00", "2024-01-01T00:00:00"),
            ("ss2", 2, "Another Songset", None, "2024-01-02T00:00:00", "2024-01-02T00:00:00"),
        ]

        result = songset_client.list_all_songsets()

        assert len(result) == 2
        assert result[0].id == "ss1"
        assert result[0].name == "My Songset"
        assert result[1].id == "ss2"
        assert result[1].name == "Another Songset"

    def test_list_all_with_limit(self, client, mock_connection_provider):
        """Test list_all_songsets respects limit."""
        songset_client, conn = client
        cursor = conn.cursor.return_value
        cursor.fetchall.return_value = [
            ("ss1", 1, "Songset 1", None, "2024-01-01T00:00:00", "2024-01-01T00:00:00"),
        ]

        result = songset_client.list_all_songsets(limit=5)

        assert len(result) == 1
        cursor.execute.assert_called_once()
        call_args = cursor.execute.call_args
        assert "LIMIT" in call_args[0][0]

    def test_list_all_empty(self, client, mock_connection_provider):
        """Test list_all_songsets returns empty list when no songsets."""
        songset_client, conn = client
        conn.cursor.return_value.fetchall.return_value = []

        result = songset_client.list_all_songsets()

        assert result == []


class TestListSongsetsForUserId:
    """Tests for SongsetClient.list_songsets_for_user_id."""

    def test_list_for_user_id_filters_correctly(self, client, mock_connection_provider):
        """Test list_songsets_for_user_id filters by user_id."""
        songset_client, conn = client
        cursor = conn.cursor.return_value
        cursor.fetchall.return_value = [
            ("ss1", 42, "Alice Songset", None, "2024-01-01T00:00:00", "2024-01-01T00:00:00"),
        ]

        result = songset_client.list_songsets_for_user_id(42)

        assert len(result) == 1
        assert result[0].user_id == 42
        assert result[0].name == "Alice Songset"
        cursor.execute.assert_called_once()
        call_args = cursor.execute.call_args
        assert call_args[0][1] == [42]

    def test_list_for_user_id_with_limit(self, client, mock_connection_provider):
        """Test list_songsets_for_user_id respects limit."""
        songset_client, conn = client
        cursor = conn.cursor.return_value
        cursor.fetchall.return_value = [
            ("ss1", 42, "Songset", None, "2024-01-01T00:00:00", "2024-01-01T00:00:00"),
        ]

        result = songset_client.list_songsets_for_user_id(42, limit=10)

        assert len(result) == 1
        call_args = cursor.execute.call_args
        assert "LIMIT" in call_args[0][0]


class TestListSongsetItemsWithSongRecording:
    """Tests for SongsetClient.list_songset_items_with_song_recording."""

    def test_batch_fetch_returns_items_grouped(self, client, mock_connection_provider):
        """Test batch fetch returns items grouped by songset_id."""
        songset_client, conn = client
        cursor = conn.cursor.return_value
        cursor.fetchall.return_value = [
            # Row format: (item_id, songset_id, song_id, recording_hash_prefix, position,
            #              gap_beats, crossfade_enabled, crossfade_duration_seconds,
            #              key_shift_semitones, tempo_ratio, created_at,
            #              song_title, song_key, duration_seconds, tempo_bpm,
            #              recording_key, loudness_db)
            ("item1", "ss1", "song1", None, 0, 2.0, 0, None, 0, 1.0, "2024-01-01T00:00:00",
             "Test Song", "G", 240.5, 120.0, "G", None),
            ("item2", "ss1", "song2", None, 1, 2.0, 1, 5.0, 0, 1.0, "2024-01-01T00:00:00",
             "Another Song", "D", 180.0, 90.0, "D", -15.5),
        ]

        result = songset_client.list_songset_items_with_song_recording(["ss1", "ss2"])

        assert "ss1" in result
        assert "ss2" in result
        assert len(result["ss1"]) == 2
        assert len(result["ss2"]) == 0  # Empty list for missing songset

        # Verify first item
        item = result["ss1"][0]
        assert item.song_title == "Test Song"
        assert item.position == 0
        assert item.tempo_bpm == 120.0

    def test_batch_fetch_orphaned_items(self, client, mock_connection_provider):
        """Test batch fetch handles orphaned items (no matching songs row)."""
        songset_client, conn = client
        cursor = conn.cursor.return_value
        cursor.fetchall.return_value = [
            # Orphaned item: song columns are NULL
            ("item1", "ss1", "song999", None, 0, 2.0, 0, None, 0, 1.0, "2024-01-01T00:00:00",
             None, None, None, None, None, None),
        ]

        result = songset_client.list_songset_items_with_song_recording(["ss1"])

        assert len(result["ss1"]) == 1
        item = result["ss1"][0]
        assert item.song_title is None
        assert item.display_key == "?"

    def test_batch_fetch_empty_ids(self, client, mock_connection_provider):
        """Test batch fetch with empty list returns empty dict."""
        songset_client, conn = client

        result = songset_client.list_songset_items_with_song_recording([])

        assert result == {}

    def test_batch_fetch_preserves_order(self, client, mock_connection_provider):
        """Test batch fetch preserves position order."""
        songset_client, conn = client
        cursor = conn.cursor.return_value
        cursor.fetchall.return_value = [
            ("item2", "ss1", "song2", None, 1, 2.0, 0, None, 0, 1.0, "2024-01-01T00:00:00",
             "Song B", "G", 200.0, 100.0, "G", None),
            ("item1", "ss1", "song1", None, 0, 2.0, 0, None, 0, 1.0, "2024-01-01T00:00:00",
             "Song A", "D", 180.0, 90.0, "D", None),
        ]

        result = songset_client.list_songset_items_with_song_recording(["ss1"])

        # Items should be ordered by position (SQL ORDER BY)
        assert result["ss1"][0].position == 1
        assert result["ss1"][1].position == 0
