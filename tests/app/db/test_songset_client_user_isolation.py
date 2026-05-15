"""User-isolation tests for SongsetClient.

The critical regression: a SongsetClient bound to user A must never read,
mutate, or delete a songset belonging to user B.
"""

import pytest

from stream_of_worship.app.db.songset_client import NotOwnerError, SongsetClient
from stream_of_worship.db.connection import ConnectionProvider
from stream_of_worship.db.postgres_schema import ALL_SCHEMA_STATEMENTS


@pytest.fixture(scope="function")
def two_user_clients(postgres_url, seed_user):
    """Set up schema, two users (alice and bob), and a client for each."""
    provider = ConnectionProvider(postgres_url)
    conn = provider.get_connection()

    with conn.cursor() as cur:
        for stmt in ALL_SCHEMA_STATEMENTS:
            cur.execute(stmt)

    alice_id = seed_user(provider, email="alice-iso@example.com", name="Alice")
    bob_id = seed_user(provider, email="bob-iso@example.com", name="Bob")

    alice = SongsetClient(provider, user_id=alice_id)
    bob = SongsetClient(provider, user_id=bob_id)

    yield alice, bob

    try:
        cleanup_provider = ConnectionProvider(postgres_url)
        with cleanup_provider.get_connection().cursor() as cur:
            cur.execute(
                """
                DROP TABLE IF EXISTS songset_share, lyric_mark,
                    user_lrc_override, user_settings,
                    songset_items, songsets,
                    recordings, songs,
                    "session", "account", "verification", "user" CASCADE;
                DROP FUNCTION IF EXISTS update_updated_at_column CASCADE;
                DROP FUNCTION IF EXISTS update_updatedat_column CASCADE;
                """
            )
        cleanup_provider.close()
    except Exception:
        pass


@pytest.mark.integration
class TestSongsetUserIsolation:
    def test_list_only_returns_own_songsets(self, two_user_clients):
        alice, bob = two_user_clients
        alice.create_songset("Alice Sunday")
        alice.create_songset("Alice Evening")
        bob.create_songset("Bob Sunday")

        alice_sets = alice.list_songsets()
        bob_sets = bob.list_songsets()

        assert {s.name for s in alice_sets} == {"Alice Sunday", "Alice Evening"}
        assert {s.name for s in bob_sets} == {"Bob Sunday"}

    def test_get_other_users_songset_returns_none(self, two_user_clients):
        alice, bob = two_user_clients
        alice_songset = alice.create_songset("Alice's Set")
        # Bob tries to fetch it
        assert bob.get_songset(alice_songset.id) is None
        # Alice can still fetch her own
        assert alice.get_songset(alice_songset.id) is not None

    def test_delete_other_users_songset_returns_false(self, two_user_clients):
        alice, bob = two_user_clients
        alice_songset = alice.create_songset("Alice's Set")
        # Bob tries to delete it
        assert bob.delete_songset(alice_songset.id) is False
        # Songset still exists for Alice
        assert alice.get_songset(alice_songset.id) is not None

    def test_update_other_users_songset_returns_false(self, two_user_clients):
        alice, bob = two_user_clients
        alice_songset = alice.create_songset("Alice's Set")
        assert bob.update_songset(alice_songset.id, name="HACKED") is False
        # Verify Alice's name is intact
        fresh = alice.get_songset(alice_songset.id)
        assert fresh.name == "Alice's Set"

    def test_add_item_to_other_users_songset_raises(self, two_user_clients):
        alice, bob = two_user_clients
        alice_songset = alice.create_songset("Alice's Set")
        with pytest.raises(NotOwnerError):
            bob.add_item(songset_id=alice_songset.id, song_id="song_x")

    def test_get_items_of_other_users_songset_returns_empty(self, two_user_clients):
        alice, bob = two_user_clients
        alice_songset = alice.create_songset("Alice's Set")
        alice.add_item(songset_id=alice_songset.id, song_id="song_1")
        alice.add_item(songset_id=alice_songset.id, song_id="song_2")

        # Bob sees nothing
        assert bob.get_items(alice_songset.id) == []
        assert bob.get_item_count(alice_songset.id) == 0
        # Alice still sees both
        assert len(alice.get_items(alice_songset.id)) == 2

    def test_update_other_users_item_raises(self, two_user_clients):
        alice, bob = two_user_clients
        alice_songset = alice.create_songset("Alice's Set")
        item = alice.add_item(songset_id=alice_songset.id, song_id="song_1")

        with pytest.raises(NotOwnerError):
            bob.update_item(item.id, gap_beats=4.0)

    def test_remove_other_users_item_raises(self, two_user_clients):
        alice, bob = two_user_clients
        alice_songset = alice.create_songset("Alice's Set")
        item = alice.add_item(songset_id=alice_songset.id, song_id="song_1")

        with pytest.raises(NotOwnerError):
            bob.remove_item(item.id)
        # Alice's item still there
        assert len(alice.get_items(alice_songset.id)) == 1

    def test_create_songset_writes_clients_user_id(self, two_user_clients):
        alice, _ = two_user_clients
        s = alice.create_songset("Alice's Set")
        # Round-trip via DB: user_id matches client's user_id
        fresh = alice.get_songset(s.id)
        assert fresh.user_id == alice.user_id
