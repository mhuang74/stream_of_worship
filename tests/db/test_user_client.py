"""Integration tests for UserClient against the Better Auth ``"user"`` table."""

import pytest

from stream_of_worship.db.connection import ConnectionProvider
from stream_of_worship.db.postgres_schema import ALL_SCHEMA_STATEMENTS
from stream_of_worship.db.user_client import DuplicateEmailError, UserClient


@pytest.fixture(scope="function")
def user_client(postgres_url):
    """Create a UserClient with a fresh schema."""
    provider = ConnectionProvider(postgres_url, sslmode="disable")
    conn = provider.get_connection()

    with conn.cursor() as cur:
        for stmt in ALL_SCHEMA_STATEMENTS:
            cur.execute(stmt)

    client = UserClient(provider)
    yield client

    try:
        cleanup_provider = ConnectionProvider(postgres_url, sslmode="disable")
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
class TestUserClient:
    def test_create_user_returns_sequential_id(self, user_client):
        u1 = user_client.create_user("alice@example.com", name="Alice")
        u2 = user_client.create_user("bob@example.com", name="Bob")
        assert isinstance(u1.id, int)
        assert isinstance(u2.id, int)
        assert u2.id == u1.id + 1
        assert u1.name == "Alice"
        assert u1.email == "alice@example.com"

    def test_create_user_default_name_from_email(self, user_client):
        u = user_client.create_user("carol@example.com")
        assert u.name == "carol"

    def test_duplicate_email_raises(self, user_client):
        user_client.create_user("dupe@example.com", name="First")
        with pytest.raises(DuplicateEmailError):
            user_client.create_user("dupe@example.com", name="Second")

    def test_get_user_returns_user(self, user_client):
        created = user_client.create_user("alice@example.com", name="Alice")
        fetched = user_client.get_user(created.id)
        assert fetched is not None
        assert fetched.id == created.id
        assert fetched.email == "alice@example.com"

    def test_get_user_unknown_returns_none(self, user_client):
        assert user_client.get_user(999999) is None

    def test_get_user_by_email(self, user_client):
        user_client.create_user("alice@example.com", name="Alice")
        fetched = user_client.get_user_by_email("alice@example.com")
        assert fetched is not None
        assert fetched.name == "Alice"
        assert user_client.get_user_by_email("nobody@example.com") is None

    def test_list_users_creation_order(self, user_client):
        user_client.create_user("a@example.com", name="A")
        user_client.create_user("b@example.com", name="B")
        user_client.create_user("c@example.com", name="C")
        users = user_client.list_users()
        assert [u.name for u in users] == ["A", "B", "C"]

    def test_delete_user_returns_false_for_unknown(self, user_client):
        assert user_client.delete_user(999999) is False

    def test_delete_user_cascades_to_songsets(self, user_client, postgres_url):
        """Deleting a user must remove their songsets via CASCADE."""
        from stream_of_worship.app.db.songset_client import SongsetClient

        user = user_client.create_user("alice@example.com", name="Alice")
        songset_client = SongsetClient(
            ConnectionProvider(postgres_url, sslmode="disable"), user_id=user.id
        )
        songset_client.create_songset("Sunday")
        songset_client.create_songset("Evening")

        assert len(songset_client.list_songsets()) == 2

        assert user_client.delete_user(user.id) is True
        assert user_client.get_user(user.id) is None
        # Songsets cascade-deleted: list returns empty for this (now-defunct) user_id
        assert songset_client.list_songsets() == []
