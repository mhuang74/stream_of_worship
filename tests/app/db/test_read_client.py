"""Tests for ReadOnlyClient.

Tests read-only database access to songs and recordings tables.
"""

import sqlite3

import pytest

from stream_of_worship.app.db.read_client import ReadOnlyClient
from stream_of_worship.admin.db.models import Song, Recording


@pytest.fixture
def populated_db(tmp_path):
    """Create a populated SQLite database with sample data."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(db_path)

    # Create songs table
    conn.execute("""
        CREATE TABLE songs (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            title_pinyin TEXT,
            composer TEXT,
            lyricist TEXT,
            album_name TEXT,
            album_series TEXT,
            musical_key TEXT,
            lyrics_raw TEXT,
            lyrics_lines TEXT,
            sections TEXT,
            source_url TEXT NOT NULL,
            table_row_number INTEGER,
            scraped_at TEXT NOT NULL,
            created_at TEXT,
            updated_at TEXT
        )
    """)

    # Create recordings table
    conn.execute("""
        CREATE TABLE recordings (
            content_hash TEXT PRIMARY KEY,
            hash_prefix TEXT UNIQUE NOT NULL,
            song_id TEXT REFERENCES songs(id),
            original_filename TEXT NOT NULL,
            file_size_bytes INTEGER NOT NULL,
            imported_at TEXT NOT NULL,
            r2_audio_url TEXT,
            r2_stems_url TEXT,
            r2_lrc_url TEXT,
            duration_seconds REAL,
            tempo_bpm REAL,
            musical_key TEXT,
            musical_mode TEXT,
            key_confidence REAL,
            loudness_db REAL,
            beats TEXT,
            downbeats TEXT,
            sections TEXT,
            embeddings_shape TEXT,
            analysis_status TEXT DEFAULT 'pending',
            analysis_job_id TEXT,
            lrc_status TEXT DEFAULT 'pending',
            lrc_job_id TEXT,
            created_at TEXT,
            updated_at TEXT
        )
    """)

    # Insert sample songs
    songs_data = [
        ("song_0001", "Amazing Grace", None, "John Newton", None, "Hymns", None, "G", "Lyrics...", None, None, "http://example.com/1", 1, "2024-01-01T00:00:00", None, None),
        ("song_0002", "How Great Thou Art", None, "Stuart Hine", None, "Hymns", None, "D", "Lyrics...", None, None, "http://example.com/2", 2, "2024-01-01T00:00:00", None, None),
        ("song_0003", "Test Song", None, "Test Composer", None, "Modern", None, "C", "Lyrics...", None, None, "http://example.com/3", 3, "2024-01-01T00:00:00", None, None),
    ]
    conn.executemany(
        """INSERT INTO songs (id, title, title_pinyin, composer, lyricist, album_name, album_series,
            musical_key, lyrics_raw, lyrics_lines, sections, source_url, table_row_number,
            scraped_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        songs_data
    )

    # Insert sample recordings
    recordings_data = [
        ("abc123" * 8, "abc123def456", "song_0001", "amazing_grace.mp3", 5000000, "2024-01-01T00:00:00", None, None, None, 180.5, 120.0, "G", "major", 0.95, -14.0, None, None, None, None, "completed", None, "completed", None, None, None),
        ("def456" * 8, "def456ghi789", "song_0002", "how_great.mp3", 6000000, "2024-01-01T00:00:00", None, None, None, 240.0, 100.0, "D", "major", 0.90, -16.0, None, None, None, None, "completed", None, "completed", None, None, None),
        ("xyz789" * 8, "xyz789abc012", None, "orphan.mp3", 3000000, "2024-01-01T00:00:00", None, None, None, 120.0, 90.0, "C", "minor", 0.80, -12.0, None, None, None, None, "pending", None, "pending", None, None, None),
    ]
    conn.executemany(
        """INSERT INTO recordings (content_hash, hash_prefix, song_id, original_filename,
            file_size_bytes, imported_at, r2_audio_url, r2_stems_url, r2_lrc_url,
            duration_seconds, tempo_bpm, musical_key, musical_mode, key_confidence,
            loudness_db, beats, downbeats, sections, embeddings_shape, analysis_status,
            analysis_job_id, lrc_status, lrc_job_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        recordings_data
    )

    conn.commit()
    conn.close()

    return db_path


@pytest.fixture
def read_client(populated_db):
    """ReadOnlyClient instance using populated database."""
    return ReadOnlyClient(populated_db)


class TestReadClientSongOperations:
    """Tests for song read operations."""

    def test_get_song_returns_song(self, read_client):
        """Verify get_song() returns Song dataclass."""
        song = read_client.get_song("song_0001")

        assert song is not None
        assert isinstance(song, Song)
        assert song.id == "song_0001"
        assert song.title == "Amazing Grace"

    def test_get_song_returns_none_for_missing(self, read_client):
        """Verify None returned when song not found."""
        song = read_client.get_song("song_nonexistent")

        assert song is None

    def test_list_songs_returns_list(self, read_client):
        """Verify list_songs() returns list of Songs."""
        songs = read_client.list_songs()

        assert isinstance(songs, list)
        assert len(songs) == 3
        assert all(isinstance(s, Song) for s in songs)

    def test_list_songs_with_album_filter(self, read_client):
        """Verify album filter works."""
        songs = read_client.list_songs(album="Hymns")

        assert len(songs) == 2
        assert all(s.album_name == "Hymns" for s in songs)

    def test_list_songs_with_key_filter(self, read_client):
        """Verify key filter works."""
        songs = read_client.list_songs(key="G")

        assert len(songs) == 1
        assert songs[0].musical_key == "G"

    def test_search_songs_finds_by_title(self, read_client):
        """Verify title search matches."""
        songs = read_client.search_songs("Amazing", field="title")

        assert len(songs) >= 1
        assert any(s.title == "Amazing Grace" for s in songs)

    def test_search_songs_finds_by_artist(self, read_client):
        """Verify artist search matches."""
        songs = read_client.search_songs("Newton", field="composer")

        assert len(songs) >= 1
        assert any(s.composer == "John Newton" for s in songs)


class TestReadClientRecordingOperations:
    """Tests for recording read operations."""

    def test_get_recording_by_hash_returns_recording(self, read_client):
        """Verify hash lookup works."""
        recording = read_client.get_recording_by_hash("abc123def456")

        assert recording is not None
        assert isinstance(recording, Recording)
        assert recording.hash_prefix == "abc123def456"

    def test_get_recording_by_song_id_returns_recording(self, read_client):
        """Verify song_id lookup works."""
        recording = read_client.get_recording_by_song_id("song_0001")

        assert recording is not None
        assert recording.song_id == "song_0001"

    def test_list_recordings_filters_by_status(self, read_client):
        """Verify status filter works."""
        recordings = read_client.list_recordings(status="completed")

        assert len(recordings) == 2
        assert all(r.analysis_status == "completed" for r in recordings)

    def test_list_recordings_has_analysis_filter(self, read_client):
        """Verify has_analysis filter works."""
        recordings = read_client.list_recordings(has_analysis=True)

        assert len(recordings) == 2

    def test_get_recording_count(self, read_client):
        """Verify total recording count."""
        count = read_client.get_recording_count()

        assert count == 3

    def test_get_analyzed_recording_count(self, read_client):
        """Verify analyzed recording count."""
        count = read_client.get_analyzed_recording_count()

        assert count == 2

    def test_get_song_count(self, read_client):
        """Verify total song count."""
        count = read_client.get_song_count()

        assert count == 3


class TestReadClientListOperations:
    """Tests for list/discovery operations."""

    def test_list_albums_returns_unique(self, read_client):
        """Verify list_albums returns distinct albums."""
        albums = read_client.list_albums()

        assert "Hymns" in albums
        assert "Modern" in albums
        assert len(albums) == len(set(albums))  # No duplicates

    def test_list_keys_returns_unique(self, read_client):
        """Verify list_keys returns distinct keys."""
        keys = read_client.list_keys()

        assert "G" in keys
        assert "D" in keys
        assert "C" in keys
        assert len(keys) == len(set(keys))  # No duplicates


class TestReadClientConnectionManagement:
    """Tests for connection lifecycle."""

    def test_connection_lazy_initialization(self, populated_db):
        """Verify connection created on first access."""
        client = ReadOnlyClient(populated_db)

        # Connection should be None initially
        assert client._connection is None

        # Accessing connection property creates it
        conn = client.connection
        assert conn is not None
        assert isinstance(conn, sqlite3.Connection)

    def test_context_manager_closes_connection(self, populated_db):
        """Verify __exit__ closes connection."""
        client = ReadOnlyClient(populated_db)

        # Use as context manager
        with client:
            _ = client.connection  # Force connection creation
            assert client._connection is not None

        # Connection should be closed after exiting context
        assert client._connection is None

    def test_close_idempotent(self, populated_db):
        """Verify close() can be called multiple times safely."""
        client = ReadOnlyClient(populated_db)

        client.close()  # Should not raise
        client.close()  # Should not raise
