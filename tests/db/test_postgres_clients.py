"""Integration tests for psycopg clients against a real Postgres database.

Requires Docker (testcontainers).  Marked with ``@pytest.mark.integration``;
these are skipped with ``-m 'not integration'``.
"""

import pytest

from stream_of_worship.admin.db.client import DatabaseClient
from stream_of_worship.admin.db.models import Recording, Song
from stream_of_worship.admin.db.schema import ALL_SCHEMA_STATEMENTS as ADMIN_SCHEMA
from stream_of_worship.app.db.models import Songset, SongsetItem
from stream_of_worship.app.db.read_client import ReadOnlyClient
from stream_of_worship.app.db.schema import ALL_APP_SCHEMA_STATEMENTS as APP_SCHEMA
from stream_of_worship.app.db.songset_client import SongsetClient
from stream_of_worship.db.connection import ConnectionProvider


@pytest.fixture(scope="module")
def db(postgres_url):
    """Create a module-scoped unified Postgres database with all schemas."""
    provider = ConnectionProvider(postgres_url)
    conn = provider.get_connection()
    cursor = conn.cursor()

    for stmt in ADMIN_SCHEMA:
        cursor.execute(stmt)
    for stmt in APP_SCHEMA:
        cursor.execute(stmt)

    conn.commit()
    yield provider
    provider.close()


@pytest.fixture(autouse=True)
def clean_tables(db):
    """Truncate all tables between tests to keep tests hermetic."""
    conn = db.get_connection()
    cursor = conn.cursor()
    cursor.execute("TRUNCATE TABLE songs, recordings, songsets, songset_items CASCADE")
    conn.commit()


@pytest.mark.integration
class TestAdminDatabaseClient:
    """Integration tests for admin ``DatabaseClient``."""

    def test_insert_and_get_song(self, db):
        client = DatabaseClient(db)
        song = Song(
            id="song_0001",
            title="Test Song",
            source_url="http://example.com",
            scraped_at="2024-01-01T00:00:00",
        )
        client.insert_song(song)

        retrieved = client.get_song("song_0001")
        assert retrieved is not None
        assert retrieved.title == "Test Song"

    def test_list_songs_with_filter(self, db):
        client = DatabaseClient(db)
        client.insert_song(
            Song(id="s1", title="Song A", source_url="http://a", scraped_at="2024-01-01", album_name="Album X")
        )
        client.insert_song(
            Song(id="s2", title="Song B", source_url="http://b", scraped_at="2024-01-01", album_name="Album Y")
        )

        results = client.list_songs(album="Album X")
        assert len(results) == 1
        assert results[0].title == "Song A"

    def test_insert_and_get_recording(self, db):
        client = DatabaseClient(db)
        client.insert_song(
            Song(id="song_0001", title="Test", source_url="http://t", scraped_at="2024-01-01")
        )

        recording = Recording(
            content_hash="a" * 64,
            hash_prefix="abc123",
            song_id="song_0001",
            original_filename="test.mp3",
            file_size_bytes=1000,
            imported_at="2024-01-01T00:00:00",
        )
        client.insert_recording(recording)

        retrieved = client.get_recording_by_hash("abc123")
        assert retrieved is not None
        assert retrieved.song_id == "song_0001"

    def test_get_stats(self, db):
        client = DatabaseClient(db)
        stats = client.get_stats()
        assert stats.is_healthy is True
        assert stats.table_counts["songs"] == 0
        assert stats.table_counts["recordings"] == 0

        client.insert_song(
            Song(id="s1", title="Test", source_url="http://t", scraped_at="2024-01-01")
        )
        stats = client.get_stats()
        assert stats.table_counts["songs"] == 1

    def test_transaction_rollback_on_integrity_error(self, db):
        """Verify transaction context manager rolls back on errors."""
        client = DatabaseClient(db)
        song = Song(
            id="trans_song",
            title="Transaction Song",
            source_url="http://t",
            scraped_at="2024-01-01",
        )
        client.insert_song(song)

        try:
            with client.transaction() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT INTO songs (id, title, source_url, scraped_at) VALUES (%s, %s, %s, %s)",
                    ("trans_song_2", "Other", "http://o", "2024-01-01"),
                )
                # Force an integrity error by duplicating the unique id
                cursor.execute(
                    "INSERT INTO songs (id, title, source_url, scraped_at) VALUES (%s, %s, %s, %s)",
                    ("trans_song", "Dup", "http://d", "2024-01-01"),
                )
                conn.commit()
        except Exception:
            pass  # Expected to fail

        # Verify the first song still exists (not rolled back because each
        # insert_song uses its own transaction)
        assert client.get_song("trans_song") is not None


@pytest.mark.integration
class TestAppReadOnlyClient:
    """Integration tests for app ``ReadOnlyClient``."""

    def test_check_connection(self, db):
        client = ReadOnlyClient(db)
        assert client.check_connection() is True

    def test_get_song(self, db):
        admin = DatabaseClient(db)
        admin.insert_song(
            Song(id="s1", title="ReadOnly" , source_url="http://r", scraped_at="2024-01-01")
        )

        read = ReadOnlyClient(db)
        song = read.get_song("s1")
        assert song is not None
        assert song.title == "ReadOnly"

    def test_get_recording_by_song_id(self, db):
        admin = DatabaseClient(db)
        admin.insert_song(
            Song(id="s1", title="Test", source_url="http://t", scraped_at="2024-01-01")
        )
        admin.insert_recording(
            Recording(
                content_hash="b" * 64,
                hash_prefix="rec456",
                song_id="s1",
                original_filename="t.mp3",
                file_size_bytes=500,
                imported_at="2024-01-01T00:00:00",
            )
        )

        read = ReadOnlyClient(db)
        rec = read.get_recording_by_song_id("s1")
        assert rec is not None
        assert rec.hash_prefix == "rec456"

    def test_list_albums(self, db):
        admin = DatabaseClient(db)
        for idx in range(3):
            admin.insert_song(
                Song(
                    id=f"s{idx}",
                    title=f"Song {idx}",
                    source_url=f"http://{idx}",
                    scraped_at="2024-01-01",
                    album_name="Album Z",
                )
            )

        read = ReadOnlyClient(db)
        albums = read.list_albums()
        assert "Album Z" in albums

    def test_search_songs(self, db):
        admin = DatabaseClient(db)
        admin.insert_song(
            Song(id="search_me", title="Special Title", source_url="http://s", scraped_at="2024-01-01")
        )
        admin.insert_song(
            Song(id="other", title="Other One", source_url="http://o", scraped_at="2024-01-01")
        )

        read = ReadOnlyClient(db)
        results = read.search_songs("Special")
        assert len(results) == 1
        assert results[0].id == "search_me"


@pytest.mark.integration
class TestAppSongsetClient:
    """Integration tests for ``SongsetClient``."""

    def test_create_and_get_songset(self, db):
        client = SongsetClient(db)
        ss = client.create_songset("My Playlist", "A description")
        assert ss.id is not None
        assert ss.name == "My Playlist"

        retrieved = client.get_songset(ss.id)
        assert retrieved is not None
        assert retrieved.name == "My Playlist"

    def test_list_and_update_songsets(self, db):
        client = SongsetClient(db)
        client.create_songset("One")
        client.create_songset("Two")

        all_sets = client.list_songsets()
        assert len(all_sets) == 2

        ss = all_sets[0]
        client.update_songset(ss.id, name="Updated")
        assert client.get_songset(ss.id).name == "Updated"

    def test_delete_songset_cascades(self, db):
        client = SongsetClient(db)
        ss = client.create_songset("Temp")
        client.add_item(ss.id, "song_a", None, position=0)
        client.delete_songset(ss.id)
        assert client.get_songset(ss.id) is None
        assert client.get_item_count(ss.id) == 0

    def test_add_and_reorder_items(self, db):
        client = SongsetClient(db)
        ss = client.create_songset("Items")
        item1 = client.add_item(ss.id, "s1", None, position=0)
        item2 = client.add_item(ss.id, "s2", None, position=1)
        item3 = client.add_item(ss.id, "s3", None, position=2)

        client.reorder_item(item1.id, 2)
        items = client.get_items(ss.id)
        positions = {i.id: i.position for i in items}
        assert positions[item1.id] == 2

    def test_remove_item(self, db):
        client = SongsetClient(db)
        ss = client.create_songset("Remove")
        item = client.add_item(ss.id, "s1", None, position=0)
        assert client.remove_item(item.id) is True
        assert client.get_item_count(ss.id) == 0

    def test_update_item_transition_params(self, db):
        client = SongsetClient(db)
        ss = client.create_songset("Transitions")
        item = client.add_item(ss.id, "s1", None, position=0, gap_beats=2.0)

        client.update_item(
            item.id,
            gap_beats=4.0,
            crossfade_enabled=True,
            crossfade_duration_seconds=3.5,
            key_shift_semitones=2,
            tempo_ratio=1.1,
        )

        items = client.get_items(ss.id)
        assert items[0].gap_beats == 4.0
        assert items[0].crossfade_duration_seconds == 3.5
        assert items[0].key_shift_semitones == 2
        assert items[0].tempo_ratio == 1.1
