"""Tests for sow-admin database client (PostgreSQL).

Uses testcontainers for integration tests with a real Postgres instance.
"""

from datetime import datetime

import psycopg
import pytest
from testcontainers.postgres import PostgresContainer

from stream_of_worship.admin.db.client import DatabaseClient
from stream_of_worship.admin.db.models import Recording, Song
from stream_of_worship.db.connection import ConnectionProvider


def _pg_url(pg: PostgresContainer) -> str:
    # testcontainers returns postgresql+psycopg2:// but psycopg.connect needs postgresql://
    return pg.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")


@pytest.fixture(scope="module")
def postgres_url():
    """Start a Postgres container for the module."""
    with PostgresContainer("postgres:16-alpine") as pg:
        yield _pg_url(pg)


@pytest.fixture
def provider(postgres_url):
    """Yield a ConnectionProvider and close it after the test."""
    provider = ConnectionProvider(postgres_url)
    yield provider
    provider.close()


@pytest.fixture
def client(provider):
    """Return an initialized DatabaseClient with schema ready.

    Tables are truncated after each test for isolation.
    """
    db = DatabaseClient(provider)
    db.initialize_schema()
    yield db
    # Cleanup: truncate tables that exist (ignore missing for cross-test safety)
    conn = provider.get_connection()
    # Rollback any aborted transaction first
    conn.rollback()
    cursor = conn.cursor()
    for table in ["songs", "recordings", "songsets", "songset_items"]:
        try:
            cursor.execute(f"TRUNCATE TABLE {table} CASCADE")
            conn.commit()
        except psycopg.errors.UndefinedTable:
            conn.rollback()
        except Exception:
            conn.rollback()
    conn.commit()


# ---------------------------------------------------------------------------
# Schema / connection tests
# ---------------------------------------------------------------------------
class TestDatabaseClient:
    """Tests for DatabaseClient class."""

    def test_initialize_schema_creates_tables(self, client):
        """Test that schema initialization creates required tables."""
        cursor = client.connection.cursor()

        # Check for songs table via Postgres catalog
        cursor.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_name = 'songs'"
        )
        assert cursor.fetchone() is not None

        # Check for recordings table
        cursor.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_name = 'recordings'"
        )
        assert cursor.fetchone() is not None

    def test_context_manager(self, provider):
        """Test using client as context manager."""
        with DatabaseClient(provider) as client:
            client.initialize_schema()
            cursor = client.connection.cursor()
            cursor.execute("SELECT 1")
            assert cursor.fetchone()[0] == 1

    def test_get_stats(self, client):
        """Test getting database statistics."""
        stats = client.get_stats()

        assert stats.total_songs == 0
        assert stats.total_recordings == 0
        assert stats.is_healthy is True
        assert stats.sync_version == "3"

    def test_get_stats_with_data(self, client):
        """Test stats after inserting data."""
        song = Song(
            id="song_0001",
            title="Test Song",
            source_url="https://example.com",
            scraped_at=datetime.now().isoformat(),
        )
        client.insert_song(song)

        stats = client.get_stats()
        assert stats.total_songs == 1
        assert stats.total_recordings == 0


# ---------------------------------------------------------------------------
# Song operations
# ---------------------------------------------------------------------------
class TestSongOperations:
    """Tests for song CRUD operations."""

    @pytest.fixture
    def sample_song(self):
        """Return a sample song."""
        return Song(
            id="song_0001",
            title="將天敞開",
            source_url="https://sop.org/song/123",
            scraped_at=datetime.now().isoformat(),
            title_pinyin="jiang_tian_chang_kai",
            album_name="敬拜讚美15",
            musical_key="G",
        )

    def test_insert_and_get_song(self, client, sample_song):
        """Test inserting and retrieving a song."""
        client.insert_song(sample_song)

        retrieved = client.get_song("song_0001")

        assert retrieved is not None
        assert retrieved.id == "song_0001"
        assert retrieved.title == "將天敞開"
        assert retrieved.album_name == "敬拜讚美15"

    def test_get_nonexistent_song(self, client):
        """Test retrieving a song that doesn't exist."""
        result = client.get_song("nonexistent")
        assert result is None

    def test_insert_song_upserts_existing(self, client, sample_song):
        """Test that inserting same ID updates the song."""
        client.insert_song(sample_song)

        # Modify and re-insert
        sample_song.title = "Updated Title"
        client.insert_song(sample_song)

        retrieved = client.get_song("song_0001")
        assert retrieved.title == "Updated Title"

    def test_list_songs(self, client):
        """Test listing songs."""
        for i in range(5):
            song = Song(
                id=f"song_{i:04d}",
                title=f"Song {i}",
                source_url=f"https://example.com/{i}",
                scraped_at=datetime.now().isoformat(),
                album_name="Test Album" if i < 3 else "Other Album",
                musical_key="G" if i < 2 else "D",
            )
            client.insert_song(song)

        all_songs = client.list_songs()
        assert len(all_songs) == 5

        album_songs = client.list_songs(album="Test Album")
        assert len(album_songs) == 3

        key_songs = client.list_songs(key="G")
        assert len(key_songs) == 2

        limited = client.list_songs(limit=2)
        assert len(limited) == 2

    def test_search_songs(self, client):
        """Test searching songs."""
        songs = [
            Song(
                id="song_0001",
                title="將天敞開",
                source_url="https://example.com/1",
                scraped_at=datetime.now().isoformat(),
                composer="Composer A",
            ),
            Song(
                id="song_0002",
                title="感謝",
                source_url="https://example.com/2",
                scraped_at=datetime.now().isoformat(),
                composer="Composer B",
                lyrics_raw="這是歌詞內容",
            ),
            Song(
                id="song_0003",
                title="另一首歌",
                source_url="https://example.com/3",
                scraped_at=datetime.now().isoformat(),
            ),
        ]
        for song in songs:
            client.insert_song(song)

        results = client.search_songs("將天", field="title")
        assert len(results) == 1
        assert results[0].id == "song_0001"

        results = client.search_songs("歌詞", field="lyrics")
        assert len(results) == 1
        assert results[0].id == "song_0002"

        results = client.search_songs("Composer A", field="composer")
        assert len(results) == 1
        assert results[0].id == "song_0001"

        results = client.search_songs("感謝", field="all")
        assert len(results) == 1

        results = client.search_songs("歌", field="all", limit=2)
        assert len(results) == 2


# ---------------------------------------------------------------------------
# Recording operations
# ---------------------------------------------------------------------------
class TestRecordingOperations:
    """Tests for recording CRUD operations."""

    @pytest.fixture
    def sample_recording(self):
        """Return a sample recording."""
        return Recording(
            content_hash="c6de4449928d0c4c5b76e23c9f4e5b8a7c6d5e4f3b2a1908",
            hash_prefix="c6de4449928d",
            song_id=None,
            original_filename="test.mp3",
            file_size_bytes=5242880,
            imported_at=datetime.now().isoformat(),
            r2_audio_url="s3://bucket/c6de4449928d/audio.mp3",
            duration_seconds=245.3,
            tempo_bpm=128.5,
            analysis_status="completed",
        )

    def test_insert_and_get_recording(self, client, sample_recording):
        """Test inserting and retrieving a recording."""
        client.insert_recording(sample_recording)

        retrieved = client.get_recording_by_hash("c6de4449928d")

        assert retrieved is not None
        assert retrieved.hash_prefix == "c6de4449928d"
        assert retrieved.tempo_bpm == 128.5

    def test_get_recording_by_song_id(self, client):
        """Test retrieving a recording by song ID."""
        song = Song(
            id="song_0001",
            title="Test Song",
            source_url="https://example.com",
            scraped_at=datetime.now().isoformat(),
        )
        client.insert_song(song)

        recording = Recording(
            content_hash="c6de4449928d0c4c5b76e23c9f4e5b8a7c6d5e4f3b2a1908",
            hash_prefix="c6de4449928d",
            song_id="song_0001",
            original_filename="test.mp3",
            file_size_bytes=5242880,
            imported_at=datetime.now().isoformat(),
        )
        client.insert_recording(recording)

        retrieved = client.get_recording_by_song_id("song_0001")
        assert retrieved is not None
        assert retrieved.hash_prefix == "c6de4449928d"

    def test_list_recordings(self, client):
        """Test listing recordings."""
        for i in range(5):
            recording = Recording(
                content_hash=f"hash{i}" * 16,
                hash_prefix=f"hash{i}",
                original_filename=f"song{i}.mp3",
                file_size_bytes=1000000,
                imported_at=datetime.now().isoformat(),
                analysis_status="completed" if i < 3 else "pending",
            )
            client.insert_recording(recording)

        all_recordings = client.list_recordings()
        assert len(all_recordings) == 5

        completed = client.list_recordings(status="completed")
        assert len(completed) == 3

        pending = client.list_recordings(status="pending")
        assert len(pending) == 2

    def test_update_recording_status(self, client, sample_recording):
        """Test updating recording status."""
        client.insert_recording(sample_recording)

        client.update_recording_status(
            hash_prefix="c6de4449928d",
            analysis_status="processing",
            analysis_job_id="job_123",
        )

        retrieved = client.get_recording_by_hash("c6de4449928d")
        assert retrieved.analysis_status == "processing"
        assert retrieved.analysis_job_id == "job_123"

    def test_update_recording_analysis(self, client, sample_recording):
        """Test updating recording with analysis results."""
        sample_recording.analysis_status = "pending"
        client.insert_recording(sample_recording)

        client.update_recording_analysis(
            hash_prefix="c6de4449928d",
            duration_seconds=300.0,
            tempo_bpm=120.0,
            musical_key="D",
            musical_mode="minor",
            key_confidence=0.95,
            loudness_db=-10.0,
            beats="[0.0, 0.5, 1.0]",
        )

        retrieved = client.get_recording_by_hash("c6de4449928d")
        assert retrieved.duration_seconds == 300.0
        assert retrieved.tempo_bpm == 120.0
        assert retrieved.musical_key == "D"
        assert retrieved.musical_mode == "minor"
        assert retrieved.key_confidence == 0.95
        assert retrieved.analysis_status == "completed"

    def test_recording_hash_prefix_unique(self, client, sample_recording):
        """Test that hash_prefix upserts on conflict."""
        client.insert_recording(sample_recording)

        duplicate = Recording(
            content_hash="different" * 8,
            hash_prefix="c6de4449928d",
            song_id=None,
            original_filename="other.mp3",
            file_size_bytes=2000000,
            imported_at=datetime.now().isoformat(),
        )

        client.insert_recording(duplicate)

        retrieved = client.get_recording_by_hash("c6de4449928d")
        assert retrieved.original_filename == "other.mp3"

    def test_delete_recording_success(self, client, sample_recording):
        """Deletes recording by hash_prefix."""
        client.insert_recording(sample_recording)

        # Verify recording exists
        assert client.get_recording_by_hash("c6de4449928d") is not None

        # Delete recording (soft delete)
        client.delete_recording("c6de4449928d")

        # Verify recording still exists but has deleted_at set (soft delete)
        # Unlike ReadOnlyClient, admin get_recording_by_hash does NOT filter deleted_at
        retrieved = client.get_recording_by_hash("c6de4449928d")
        assert retrieved is not None
        assert retrieved.deleted_at is not None

    def test_delete_recording_not_found(self, client):
        """Deleting non-existent recording does not raise error."""
        client.delete_recording("nonexistent_hash")
