"""Tests for SongsetClient (Postgres via testcontainers)."""

import pytest

from stream_of_worship.app.db.models import Songset, SongsetItem
from stream_of_worship.app.db.songset_client import MissingReferenceError, SongsetClient
from stream_of_worship.db.connection import ConnectionProvider
from stream_of_worship.db.postgres_schema import ALL_SCHEMA_STATEMENTS


@pytest.fixture(scope="function")
def songset_client(postgres_url):
    """Create a SongsetClient connected to a fresh Postgres schema."""
    provider = ConnectionProvider(postgres_url)
    conn = provider.get_connection()

    # Create schema
    with conn.cursor() as cur:
        for stmt in ALL_SCHEMA_STATEMENTS:
            cur.execute(stmt)

    client = SongsetClient(provider)
    yield client

    # Cleanup (use fresh connection in case provider was closed by a test)
    try:
        cleanup_provider = ConnectionProvider(postgres_url)
        with cleanup_provider.get_connection().cursor() as cur:
            cur.execute("""
                DROP TABLE IF EXISTS songset_items CASCADE;
                DROP TABLE IF EXISTS songsets CASCADE;
                DROP TABLE IF EXISTS recordings CASCADE;
                DROP TABLE IF EXISTS songs CASCADE;
                DROP FUNCTION IF EXISTS update_updated_at_column CASCADE;
            """)
        cleanup_provider.close()
    except Exception:
        pass


@pytest.mark.integration
class TestSongsetClient:
    """Integration tests for SongsetClient CRUD operations."""

    def test_create_songset(self, songset_client):
        """Test creating a songset."""
        songset = songset_client.create_songset("Worship Set", description="Sunday service")
        assert isinstance(songset, Songset)
        assert songset.name == "Worship Set"
        assert songset.description == "Sunday service"
        assert songset.id.startswith("songset_")

    def test_get_songset(self, songset_client):
        """Test retrieving a songset by ID."""
        created = songset_client.create_songset("Test Set")
        fetched = songset_client.get_songset(created.id)
        assert fetched is not None
        assert fetched.name == "Test Set"

    def test_get_songset_not_found(self, songset_client):
        """Test retrieving a non-existent songset."""
        result = songset_client.get_songset("nonexistent")
        assert result is None

    def test_list_songsets(self, songset_client):
        """Test listing all songsets."""
        songset_client.create_songset("Set A")
        songset_client.create_songset("Set B")
        songsets = songset_client.list_songsets()
        assert len(songsets) == 2
        names = {s.name for s in songsets}
        assert names == {"Set A", "Set B"}

    def test_list_songsets_with_limit(self, songset_client):
        """Test listing songsets with a limit."""
        songset_client.create_songset("Set A")
        songset_client.create_songset("Set B")
        songsets = songset_client.list_songsets(limit=1)
        assert len(songsets) == 1

    def test_update_songset(self, songset_client):
        """Test updating a songset."""
        songset = songset_client.create_songset("Old Name")
        result = songset_client.update_songset(songset.id, name="New Name")
        assert result is True
        updated = songset_client.get_songset(songset.id)
        assert updated.name == "New Name"

    def test_update_songset_description(self, songset_client):
        """Test updating only the description."""
        songset = songset_client.create_songset("Name", description="Old desc")
        songset_client.update_songset(songset.id, description="New desc")
        updated = songset_client.get_songset(songset.id)
        assert updated.description == "New desc"
        assert updated.name == "Name"

    def test_update_songset_not_found(self, songset_client):
        """Test updating a non-existent songset."""
        result = songset_client.update_songset("nonexistent", name="New")
        assert result is False

    def test_delete_songset(self, songset_client):
        """Test deleting a songset."""
        songset = songset_client.create_songset("To Delete")
        result = songset_client.delete_songset(songset.id)
        assert result is True
        assert songset_client.get_songset(songset.id) is None

    def test_delete_songset_not_found(self, songset_client):
        """Test deleting a non-existent songset."""
        result = songset_client.delete_songset("nonexistent")
        assert result is False

    def test_add_item(self, songset_client):
        """Test adding an item to a songset."""
        songset = songset_client.create_songset("My Set")
        item = songset_client.add_item(
            songset_id=songset.id,
            song_id="song_1",
            recording_hash_prefix="abc123",
            position=0,
        )
        assert isinstance(item, SongsetItem)
        assert item.songset_id == songset.id
        assert item.song_id == "song_1"
        assert item.recording_hash_prefix == "abc123"
        assert item.position == 0

    def test_add_item_appends_when_no_position(self, songset_client):
        """Test that items are appended when position is None."""
        songset = songset_client.create_songset("My Set")
        item1 = songset_client.add_item(songset_id=songset.id, song_id="song_1")
        item2 = songset_client.add_item(songset_id=songset.id, song_id="song_2")
        assert item1.position == 0
        assert item2.position == 1

    def test_add_item_with_transition_params(self, songset_client):
        """Test adding an item with transition parameters."""
        songset = songset_client.create_songset("My Set")
        item = songset_client.add_item(
            songset_id=songset.id,
            song_id="song_1",
            gap_beats=4.0,
            crossfade_enabled=True,
            crossfade_duration_seconds=2.0,
            key_shift_semitones=2,
            tempo_ratio=1.1,
        )
        assert item.gap_beats == 4.0
        assert item.crossfade_enabled is True
        assert item.crossfade_duration_seconds == 2.0
        assert item.key_shift_semitones == 2
        assert item.tempo_ratio == 1.1

    def test_get_items(self, songset_client):
        """Test retrieving items from a songset."""
        songset = songset_client.create_songset("My Set")
        songset_client.add_item(songset_id=songset.id, song_id="song_1")
        songset_client.add_item(songset_id=songset.id, song_id="song_2")

        items = songset_client.get_items(songset.id)
        assert len(items) == 2
        assert items[0].song_id == "song_1"
        assert items[1].song_id == "song_2"

    def test_get_item_count(self, songset_client):
        """Test getting item count."""
        songset = songset_client.create_songset("My Set")
        assert songset_client.get_item_count(songset.id) == 0
        songset_client.add_item(songset_id=songset.id, song_id="song_1")
        assert songset_client.get_item_count(songset.id) == 1

    def test_update_item(self, songset_client):
        """Test updating an item."""
        songset = songset_client.create_songset("My Set")
        item = songset_client.add_item(songset_id=songset.id, song_id="song_1", gap_beats=2.0)
        result = songset_client.update_item(item.id, gap_beats=4.0, key_shift_semitones=3)
        assert result is True
        items = songset_client.get_items(songset.id)
        assert items[0].gap_beats == 4.0
        assert items[0].key_shift_semitones == 3

    def test_update_item_crossfade(self, songset_client):
        """Test updating crossfade parameters."""
        songset = songset_client.create_songset("My Set")
        item = songset_client.add_item(songset_id=songset.id, song_id="song_1")
        songset_client.update_item(
            item.id, crossfade_enabled=True, crossfade_duration_seconds=3.0
        )
        items = songset_client.get_items(songset.id)
        assert items[0].crossfade_enabled is True
        assert items[0].crossfade_duration_seconds == 3.0

    def test_update_item_recording_hash(self, songset_client):
        """Test updating recording hash prefix."""
        songset = songset_client.create_songset("My Set")
        item = songset_client.add_item(
            songset_id=songset.id, song_id="song_1", recording_hash_prefix="abc"
        )
        songset_client.update_item(item.id, recording_hash_prefix="def")
        items = songset_client.get_items(songset.id)
        assert items[0].recording_hash_prefix == "def"

    def test_update_item_not_found(self, songset_client):
        """Test updating a non-existent item."""
        result = songset_client.update_item("nonexistent", gap_beats=4.0)
        assert result is False

    def test_remove_item(self, songset_client):
        """Test removing an item."""
        songset = songset_client.create_songset("My Set")
        item = songset_client.add_item(songset_id=songset.id, song_id="song_1")
        result = songset_client.remove_item(item.id)
        assert result is True
        assert songset_client.get_item_count(songset.id) == 0

    def test_remove_item_reorders_positions(self, songset_client):
        """Test that removing an item reorders remaining positions."""
        songset = songset_client.create_songset("My Set")
        songset_client.add_item(songset_id=songset.id, song_id="song_1")
        _item2 = songset_client.add_item(songset_id=songset.id, song_id="song_2")
        songset_client.add_item(songset_id=songset.id, song_id="song_3")

        songset_client.remove_item(_item2.id)
        items = songset_client.get_items(songset.id)
        assert len(items) == 2
        assert items[0].song_id == "song_1"
        assert items[0].position == 0
        assert items[1].song_id == "song_3"
        assert items[1].position == 1

    def test_remove_item_not_found(self, songset_client):
        """Test removing a non-existent item."""
        result = songset_client.remove_item("nonexistent")
        assert result is False

    def test_reorder_item_forward(self, songset_client):
        """Test moving an item forward in the list."""
        songset = songset_client.create_songset("My Set")
        _item1 = songset_client.add_item(songset_id=songset.id, song_id="song_1")
        songset_client.add_item(songset_id=songset.id, song_id="song_2")
        songset_client.add_item(songset_id=songset.id, song_id="song_3")

        songset_client.reorder_item(_item1.id, 2)
        items = songset_client.get_items(songset.id)
        assert items[0].song_id == "song_2"
        assert items[1].song_id == "song_3"
        assert items[2].song_id == "song_1"

    def test_reorder_item_backward(self, songset_client):
        """Test moving an item backward in the list."""
        songset = songset_client.create_songset("My Set")
        songset_client.add_item(songset_id=songset.id, song_id="song_1")
        songset_client.add_item(songset_id=songset.id, song_id="song_2")
        _item3 = songset_client.add_item(songset_id=songset.id, song_id="song_3")

        songset_client.reorder_item(_item3.id, 0)
        items = songset_client.get_items(songset.id)
        assert items[0].song_id == "song_3"
        assert items[1].song_id == "song_1"
        assert items[2].song_id == "song_2"

    def test_reorder_item_same_position(self, songset_client):
        """Test reordering to the same position is a no-op."""
        songset = songset_client.create_songset("My Set")
        item = songset_client.add_item(songset_id=songset.id, song_id="song_1")
        result = songset_client.reorder_item(item.id, 0)
        assert result is True

    def test_reorder_item_not_found(self, songset_client):
        """Test reordering a non-existent item."""
        result = songset_client.reorder_item("nonexistent", 0)
        assert result is False

    def test_delete_songset_cascades_items(self, songset_client):
        """Test that deleting a songset removes all items."""
        songset = songset_client.create_songset("To Delete")
        songset_client.add_item(songset_id=songset.id, song_id="song_1")
        songset_client.add_item(songset_id=songset.id, song_id="song_2")

        songset_client.delete_songset(songset.id)
        assert songset_client.get_item_count(songset.id) == 0

    def test_validate_recording_exists_without_getter(self, songset_client):
        """Test validation with no getter always passes."""
        assert songset_client.validate_recording_exists("abc123") is True

    def test_validate_recording_exists_with_getter(self, songset_client):
        """Test validation with a getter function."""

        def mock_get_recording(hash_prefix):
            if hash_prefix == "exists":
                return {"hash_prefix": "exists"}
            return None

        assert songset_client.validate_recording_exists("exists", mock_get_recording) is True

    def test_validate_recording_exists_raises_on_missing(self, songset_client):
        """Test validation raises MissingReferenceError."""

        def mock_get_recording(hash_prefix):
            return None

        with pytest.raises(MissingReferenceError):
            songset_client.validate_recording_exists("missing", mock_get_recording)

    def test_context_manager(self, songset_client):
        """Test SongsetClient works as a context manager."""
        with songset_client as client:
            assert client is songset_client
            songset = client.create_songset("Context Test")
            assert songset.name == "Context Test"
