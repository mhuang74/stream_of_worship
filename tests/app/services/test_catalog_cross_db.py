"""Tests for CatalogService with unified Postgres database.

Previously these tests created two separate SQLite databases (catalog + songsets).
Now all tables live in a single Postgres database, so the tests initialize
one shared schema and use a single ConnectionProvider.
"""

import pytest
from unittest.mock import MagicMock

from stream_of_worship.admin.db.client import DatabaseClient
from stream_of_worship.admin.db.models import Recording, Song
from stream_of_worship.app.db.models import SongsetItem
from stream_of_worship.app.db.read_client import ReadOnlyClient
from stream_of_worship.app.db.songset_client import SongsetClient
from stream_of_worship.app.services.catalog import CatalogService, SongsetItemWithDetails
from stream_of_worship.db.postgres_schema import ALL_SCHEMA_STATEMENTS
from tests.conftest import make_test_provider


@pytest.fixture(scope="function")
def unified_db(postgres_url):
    """Set up a unified Postgres database with all tables.

    Returns:
        Tuple of (DatabaseClient, ReadOnlyClient, SongsetClient).
    """
    provider = make_test_provider(postgres_url)
    conn = provider.get_connection()

    # Create schema
    with conn.cursor() as cur:
        for stmt in ALL_SCHEMA_STATEMENTS:
            cur.execute(stmt)

    admin_client = DatabaseClient(provider)
    read_client = ReadOnlyClient(provider)

    # Create a test user for SongsetClient
    from stream_of_worship.db.user_client import UserClient
    with UserClient(provider) as user_client:
        user = user_client.create_user(email="test-catalog@example.com", name="Test User")

    songset_client = SongsetClient(provider, user_id=user.id)

    yield admin_client, read_client, songset_client

    # Cleanup (use fresh connection in case provider was closed by a test)
    try:
        cleanup_provider = make_test_provider(postgres_url)
        with cleanup_provider.get_connection().cursor() as cur:
            cur.execute("""
                DROP TABLE IF EXISTS songset_share, lyric_mark,
                    user_lrc_override, user_settings,
                    songset_items, songsets,
                    recordings, songs,
                    "session", "account", "verification", "user" CASCADE;
                DROP FUNCTION IF EXISTS update_updated_at_column CASCADE;
                DROP FUNCTION IF EXISTS update_updatedat_column CASCADE;
            """)
        cleanup_provider.close()
    except Exception:
        pass


class TestCrossDBLookup:
    """Test suite for songset item lookups in unified Postgres DB."""

    def test_get_songset_with_items_resolves_references(self, unified_db):
        """Test that songset items resolve song/recording references."""
        admin_client, read_client, songset_client = unified_db

        # Insert catalog data
        admin_client.insert_song(
            Song(
                id="song_1",
                title="Test Song",
                source_url="http://test",
                scraped_at="2024-01-01T00:00:00",
            )
        )
        admin_client.insert_recording(
            Recording(
                content_hash="f" * 64,
                hash_prefix="abc123",
                song_id="song_1",
                original_filename="test.mp3",
                file_size_bytes=1000,
                imported_at="2024-01-01T00:00:00",
            )
        )

        # Insert songset data
        songset = songset_client.create_songset("Test Set")
        songset_client.add_item(
            songset_id=songset.id,
            song_id="song_1",
            recording_hash_prefix="abc123",
            position=0,
        )

        catalog = CatalogService(read_client)
        items, orphan_count = catalog.get_songset_with_items(songset.id, songset_client)

        assert len(items) == 1
        assert orphan_count == 0

        item = items[0]
        assert isinstance(item, SongsetItemWithDetails)
        assert item.song is not None
        assert item.song.title == "Test Song"
        assert item.recording is not None
        assert item.recording.hash_prefix == "abc123"
        assert item.is_orphan is False

    def test_get_songset_with_items_detects_orphans(self, unified_db):
        """Test that missing references are marked as orphans."""
        _, read_client, songset_client = unified_db

        # No catalog data - empty songs/recordings tables

        songset = songset_client.create_songset("Test Set")
        songset_client.add_item(
            songset_id=songset.id,
            song_id="song_1",
            recording_hash_prefix="missing_hash",
            position=0,
        )

        catalog = CatalogService(read_client)
        items, orphan_count = catalog.get_songset_with_items(songset.id, songset_client)

        assert len(items) == 1
        assert orphan_count == 1

        item = items[0]
        assert item.is_orphan is True
        assert item.song is None
        assert item.recording is None
        assert item.display_title == "Unknown"

    def test_get_songset_with_items_detects_soft_deleted(self, unified_db):
        """Test that soft-deleted songs are marked as orphans."""
        admin_client, read_client, songset_client = unified_db

        # Insert soft-deleted song and recording
        admin_client.insert_song(
            Song(
                id="song_1",
                title="Deleted Song",
                source_url="http://test",
                scraped_at="2024-01-01T00:00:00",
            )
        )
        admin_client.insert_recording(
            Recording(
                content_hash="f" * 64,
                hash_prefix="abc123",
                song_id="song_1",
                original_filename="test.mp3",
                file_size_bytes=1000,
                imported_at="2024-01-01T00:00:00",
            )
        )
        # Soft delete both
        admin_client.soft_delete_song("song_1")
        admin_client.delete_recording("abc123")

        songset = songset_client.create_songset("Test Set")
        songset_client.add_item(
            songset_id=songset.id,
            song_id="song_1",
            recording_hash_prefix="abc123",
            position=0,
        )

        catalog = CatalogService(read_client)
        items, orphan_count = catalog.get_songset_with_items(songset.id, songset_client)

        assert len(items) == 1
        # Both song and recording exist but are soft-deleted
        # is_orphan should be True because recording is deleted
        item = items[0]
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
            scraped_at="2024-01-01T00:00:00",
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
    """Test JOIN query splits at correct offset."""

    def test_join_query_splits_at_correct_offset(self, unified_db):
        """Verify _list_analyzed_songs splits rows correctly.
        Using SONG_COLUMN_COUNT from schema to split joined rows.
        """
        admin_client, read_client, _ = unified_db

        # Insert full song
        admin_client.insert_song(
            Song(
                id="song_1",
                title="Test Song",
                title_pinyin="test_pinyin",
                composer="Composer",
                lyricist="Lyricist",
                album_name="Album",
                album_series=None,
                musical_key="G",
                lyrics_raw="raw",
                lyrics_lines="lines",
                sections=None,
                source_url="http://test",
                table_row_number=1,
                scraped_at="2024-01-01T00:00:00",
            )
        )

        # Insert full recording
        admin_client.insert_recording(
            Recording(
                content_hash="h" * 64,
                hash_prefix="abc123",
                song_id="song_1",
                original_filename="test.mp3",
                file_size_bytes=1000,
                imported_at="2024-01-01T00:00:00",
                duration_seconds=180.0,
                tempo_bpm=120.0,
                musical_key="G",
                musical_mode="major",
                key_confidence=0.9,
                loudness_db=-8.0,
                analysis_status="completed",
                lrc_status="completed",
                youtube_url="https://yt.com",
                visibility_status="published",
            )
        )

        catalog = CatalogService(read_client)

        # Test _list_analyzed_songs
        songs = catalog._list_analyzed_songs()
        assert len(songs) == 1

        song = songs[0]
        assert song.song.id == "song_1"
        assert song.song.title == "Test Song"
        assert song.recording.content_hash == "h" * 64
        assert song.recording.visibility_status == "published"

    def test_join_query_with_deleted_at_populated(self, unified_db):
        """Test soft-deleted songs filtered out."""
        admin_client, read_client, _ = unified_db

        admin_client.insert_song(
            Song(
                id="song_1",
                title="Active Song",
                source_url="http://test",
                scraped_at="2024-01-01T00:00:00",
            )
        )
        admin_client.insert_song(
            Song(
                id="song_2",
                title="Deleted Song",
                source_url="http://test",
                scraped_at="2024-01-01T00:00:00",
            )
        )
        # Soft delete song_2 via raw SQL
        conn = admin_client.connection
        with conn.cursor() as cur:
            cur.execute("UPDATE songs SET deleted_at = NOW() WHERE id = %s", ("song_2",))

        admin_client.insert_recording(
            Recording(
                content_hash="h1" + "a" * 62,
                hash_prefix="hash1",
                song_id="song_1",
                original_filename="test.mp3",
                file_size_bytes=1000,
                imported_at="2024-01-01T00:00:00",
                analysis_status="completed",
            )
        )
        # Insert recording for deleted song too
        admin_client.insert_recording(
            Recording(
                content_hash="h2" + "b" * 62,
                hash_prefix="hash2",
                song_id="song_2",
                original_filename="test.mp3",
                file_size_bytes=1000,
                imported_at="2024-01-01T00:00:00",
                analysis_status="completed",
            )
        )

        catalog = CatalogService(read_client)
        songs = catalog._list_analyzed_songs()

        # Only active song should be returned
        assert len(songs) == 1
        assert songs[0].song.title == "Active Song"
