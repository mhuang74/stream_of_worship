import pytest
from stream_of_worship.app.db.schema import ALL_APP_SCHEMA_STATEMENTS
from stream_of_worship.admin.db.schema import ALL_SCHEMA_STATEMENTS as ADMIN_SCHEMA
from stream_of_worship.admin.db.models import Recording, Song
from stream_of_worship.app.db.models import SongsetItem
from stream_of_worship.app.db.read_client import ReadOnlyClient
from stream_of_worship.app.db.songset_client import SongsetClient
from stream_of_worship.app.services.catalog import CatalogService, SongsetItemWithDetails
from stream_of_worship.db.connection import ConnectionProvider

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def schema_provider(postgres_url):
    """Create a single Postgres database with all tables (unified DB)."""
    provider = ConnectionProvider(postgres_url)
    conn = provider.get_connection()
    cursor = conn.cursor()

    for stmt in ADMIN_SCHEMA:
        cursor.execute(stmt)
    for stmt in ALL_APP_SCHEMA_STATEMENTS:
        cursor.execute(stmt)

    # Clean start
    cursor.execute("TRUNCATE TABLE songs, recordings, songsets, songset_items CASCADE")
    conn.commit()
    yield provider
    provider.close()


@pytest.fixture(autouse=True)
def clean_tables(schema_provider):
    """Truncate tables between tests for isolation."""
    conn = schema_provider.get_connection()
    cursor = conn.cursor()
    cursor.execute("TRUNCATE TABLE songs, recordings, songsets, songset_items CASCADE")
    conn.commit()


def _insert_song(cursor, song_id: str, title: str, deleted_at=None):
    """Helper to insert a song with the minimum required columns."""
    cursor.execute(
        """
        INSERT INTO songs (id, title, source_url, scraped_at, deleted_at)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (song_id, title, "http://test", "2024-01-01T00:00:00", deleted_at),
    )


def _insert_recording(cursor, content_hash: str, hash_prefix: str, song_id: str, deleted_at=None):
    """Helper to insert a recording with the minimum required columns."""
    cursor.execute(
        """
        INSERT INTO recordings (
            content_hash, hash_prefix, song_id, original_filename,
            file_size_bytes, imported_at, deleted_at, analysis_status
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (content_hash, hash_prefix, song_id, "test.mp3", 1000, "2024-01-01T00:00:00", deleted_at, "completed"),
    )


def _insert_songset(cursor, songset_id: str, name: str):
    """Helper to insert a songset."""
    cursor.execute(
        "INSERT INTO songsets (id, name) VALUES (%s, %s)",
        (songset_id, name),
    )


def _insert_songset_item(cursor, item_id: str, songset_id: str, song_id: str, hash_prefix, position: int = 0):
    """Helper to insert a songset item."""
    cursor.execute(
        """
        INSERT INTO songset_items (id, songset_id, song_id, recording_hash_prefix, position, gap_beats)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (item_id, songset_id, song_id, hash_prefix, position, 2.0),
    )


class TestCrossDBLookup:
    """Test suite for cross-DB songset item lookups (unified Postgres DB)."""

    def test_get_songset_with_items_resolves_references(self, schema_provider):
        """Test that songset items resolve song/recording references."""
        conn = schema_provider.get_connection()
        cursor = conn.cursor()

        _insert_song(cursor, "song_1", "Test Song")
        _insert_recording(cursor, "full_hash_64_chars_long__________", "abc123", "song_1")
        _insert_songset(cursor, "set_1", "Test Set")
        _insert_songset_item(cursor, "item_1", "set_1", "song_1", "abc123", position=0)

        conn.commit()

        read_client = ReadOnlyClient(schema_provider)
        songset_client = SongsetClient(schema_provider)

        catalog = CatalogService(read_client)
        items, orphan_count = catalog.get_songset_with_items("set_1", songset_client)

        assert len(items) == 1
        assert orphan_count == 0
        item = items[0]
        assert isinstance(item, SongsetItemWithDetails)
        assert item.song is not None
        assert item.song.title == "Test Song"
        assert item.recording is not None
        assert item.recording.hash_prefix == "abc123"
        assert item.is_orphan is False

    def test_get_songset_with_items_detects_orphans(self, schema_provider):
        """Test that missing references are marked as orphans."""
        conn = schema_provider.get_connection()
        cursor = conn.cursor()

        # Empty catalog--no songs or recordings inserted
        _insert_songset(cursor, "set_1", "Test Set")
        _insert_songset_item(cursor, "item_1", "set_1", "song_1", "missing_hash", position=0)

        conn.commit()

        read_client = ReadOnlyClient(schema_provider)
        songset_client = SongsetClient(schema_provider)

        catalog = CatalogService(read_client)
        items, orphan_count = catalog.get_songset_with_items("set_1", songset_client)

        assert len(items) == 1
        assert orphan_count == 1

        item = items[0]
        assert item.is_orphan is True
        assert item.song is None
        assert item.recording is None
        assert item.display_title == "Unknown"

    def test_get_songset_with_items_detects_soft_deleted(self, schema_provider):
        """Test that soft-deleted songs are marked as orphans."""
        import datetime

        conn = schema_provider.get_connection()
        cursor = conn.cursor()

        # Soft-deleted song and recording
        _insert_song(cursor, "song_1", "Deleted Song", deleted_at=datetime.datetime(2024, 1, 2))
        _insert_recording(cursor, "full_hash_64_chars_long__________", "abc123", "song_1",
                         deleted_at=datetime.datetime(2024, 1, 2))
        _insert_songset(cursor, "set_1", "Test Set")
        _insert_songset_item(cursor, "item_1", "set_1", "song_1", "abc123", position=0)

        conn.commit()

        read_client = ReadOnlyClient(schema_provider)
        songset_client = SongsetClient(schema_provider)

        catalog = CatalogService(read_client)
        items, orphan_count = catalog.get_songset_with_items("set_1", songset_client)

        assert len(items) == 1
        item = items[0]
        # Soft-deleted items are found via include_deleted=True, treated as orphans
        assert item.is_orphan is True


class TestSongsetItemWithDetails:
    """Test SongsetItemWithDetails helper class."""

    def test_is_orphan_when_song_missing(self):
        """Test is_orphan when song is None."""
        item = SongsetItem(
            id="item_1",
            songset_id="set_1",
            song_id="song_1",
            position=0,
        )
        details = SongsetItemWithDetails(item=item, song=None, recording=None)
        assert details.is_orphan is True

    def test_is_orphan_when_recording_missing(self):
        """Test is_orphan when recording is None."""
        from unittest.mock import MagicMock

        song = MagicMock(spec=Song)
        item = SongsetItem(
            id="item_1",
            songset_id="set_1",
            song_id="song_1",
            position=0,
        )
        details = SongsetItemWithDetails(item=item, song=song, recording=None)
        assert details.is_orphan is True

    def test_not_orphan_when_both_present(self):
        """Test not orphan when both present."""
        from unittest.mock import MagicMock

        song = MagicMock(spec=Song)
        song.deleted_at = None
        recording = MagicMock(spec=Recording)
        recording.deleted_at = None
        item = SongsetItem(
            id="item_1",
            songset_id="set_1",
            song_id="song_1",
            position=0,
        )
        details = SongsetItemWithDetails(item=item, song=song, recording=recording)
        assert details.is_orphan is False

    def test_display_title_from_song(self):
        """Test display_title uses song title."""
        song = Song(
            id="song_1",
            title="Test Song",
            source_url="http://test",
            scraped_at="2024-01-01",
        )
        item = SongsetItem(
            id="item_1",
            songset_id="set_1",
            song_id="song_1",
            position=0,
        )
        details = SongsetItemWithDetails(item=item, song=song, recording=None)
        assert details.display_title == "Test Song"

    def test_display_title_unknown_when_no_song(self):
        """Test display_title is 'Unknown' when no song."""
        item = SongsetItem(
            id="item_1",
            songset_id="set_1",
            song_id="song_1",
            position=0,
        )
        details = SongsetItemWithDetails(item=item, song=None, recording=None)
        assert details.display_title == "Unknown"


class TestJoinColumnOffset:
    """Test JOIN query splits at correct offset (R6.1)."""

    def test_join_query_splits_at_correct_offset(self, schema_provider):
        """Verify _list_analyzed_songs splits rows correctly at SONG_COLUMN_COUNT."""
        conn = schema_provider.get_connection()
        cursor = conn.cursor()

        # Insert a full 17-column song
        cursor.execute(
            """
            INSERT INTO songs (
                id, title, title_pinyin, composer, lyricist,
                album_name, album_series, musical_key, lyrics_raw,
                lyrics_lines, sections, source_url, table_row_number,
                scraped_at, created_at, updated_at, deleted_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                "song_1", "Test Song", "test_pinyin", "Composer",
                "Lyricist", "Album", None, "G", "raw", "lines",
                None, "http://test", 1, "2024-01-01",
                None, None, None,
            ),
        )

        # Insert a recording
        # Columns in recordings table order (matching Recording.from_row expectations):
        # content_hash, hash_prefix, song_id, original_filename, file_size_bytes,
        # imported_at, r2_audio_url, r2_stems_url, r2_lrc_url, duration_seconds,
        # tempo_bpm, musical_key, musical_mode, key_confidence, loudness_db,
        # beats, downbeats, sections, embeddings_shape, analysis_status,
        # analysis_job_id, lrc_status, lrc_job_id, created_at, updated_at,
        # youtube_url, visibility_status, deleted_at, download_status
        cursor.execute(
            """
            INSERT INTO recordings (
                content_hash, hash_prefix, song_id, original_filename,
                file_size_bytes, imported_at, r2_audio_url, r2_stems_url,
                r2_lrc_url, duration_seconds, tempo_bpm, musical_key,
                musical_mode, key_confidence, loudness_db, beats,
                downbeats, sections, embeddings_shape, analysis_status,
                analysis_job_id, lrc_status, lrc_job_id, created_at,
                updated_at, youtube_url, visibility_status, deleted_at,
                download_status
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                "h" * 64, "abc123", "song_1", "test.mp3", 1000,
                "2024-01-01", None, None, None, 180.0, 120.0,
                "G", "major", 0.9, -8.0, None, None, None, None,
                "completed", None, "completed", None,
                None, None, "https://yt.com", "published", None,
                "completed",
            ),
        )
        conn.commit()

        read_client = ReadOnlyClient(schema_provider)
        catalog = CatalogService(read_client)

        songs = catalog._list_analyzed_songs()
        assert len(songs) == 1

        song = songs[0]
        assert song.song.id == "song_1"
        assert song.song.title == "Test Song"
        assert song.recording.content_hash == "h" * 64
        assert song.recording.visibility_status == "published"

    def test_join_query_with_deleted_at_populated(self, schema_provider):
        """Test soft-deleted songs filtered out."""
        import datetime

        conn = schema_provider.get_connection()
        cursor = conn.cursor()

        cursor.execute(
            """
            INSERT INTO songs (id, title, source_url, scraped_at, deleted_at)
            VALUES (%s, %s, %s, %s, %s)
            """,
            ("song_1", "Active Song", "http://test", "2024-01-01", None),
        )
        cursor.execute(
            """
            INSERT INTO songs (id, title, source_url, scraped_at, deleted_at)
            VALUES (%s, %s, %s, %s, %s)
            """,
            ("song_2", "Deleted Song", "http://test", "2024-01-01", datetime.datetime(2024, 1, 2)),
        )
        cursor.execute(
            """
            INSERT INTO recordings (content_hash, hash_prefix, song_id, original_filename,
                file_size_bytes, imported_at, analysis_status)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            ("h" * 64, "abc123", "song_1", "test.mp3", 1000, "2024-01-01", "completed"),
        )
        conn.commit()

        read_client = ReadOnlyClient(schema_provider)
        catalog = CatalogService(read_client)

        songs = catalog._list_analyzed_songs()

        # Only active song should be returned
        assert len(songs) == 1
        assert songs[0].song.title == "Active Song"


class TestConnectionProviderSharing:
    """Test that ConnectionProvider is shared between ReadOnlyClient and SongsetClient."""

    def test_shared_connection_provider(self, schema_provider):
        """Verify ReadOnlyClient and SongsetClient share the same ConnectionProvider."""
        read_client = ReadOnlyClient(schema_provider)
        songset_client = SongsetClient(schema_provider)

        # Both clients should have the same connection provider
        assert read_client.connection_provider is songset_client.connection_provider
        assert read_client.connection_provider is schema_provider

        # Both should return the same connection object
        assert read_client.connection is songset_client.connection

        # Closing one should close the shared connection
        read_client.close()
        assert schema_provider._connection is None

    def test_catalog_service_with_shared_provider(self, schema_provider):
        """Verify CatalogService works with shared ConnectionProvider."""
        conn = schema_provider.get_connection()
        cursor = conn.cursor()

        _insert_song(cursor, "song_1", "Test Song")
        _insert_recording(cursor, "full_hash_64_chars_long__________", "abc123", "song_1")
        conn.commit()

        read_client = ReadOnlyClient(schema_provider)
        songset_client = SongsetClient(schema_provider)

        catalog = CatalogService(read_client)

        # Catalog should be able to query via the shared connection
        songs = catalog._list_analyzed_songs()
        assert len(songs) == 1
        assert songs[0].song.title == "Test Song"

        # SongsetClient should also work with the same connection
        songset = songset_client.create_songset("Test Set")
        assert songset is not None
        assert songset.name == "Test Set"

