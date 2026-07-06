"""Tests for database commands (Postgres via testcontainers)."""

import pytest

from stream_of_worship.admin.commands.db import _get_db_client, _mask_url
from stream_of_worship.admin.config import AdminConfig
from stream_of_worship.admin.db.client import DatabaseClient
from stream_of_worship.db.postgres_schema import ALL_SCHEMA_STATEMENTS


@pytest.fixture(scope="function")
def admin_config(postgres_url):
    """Create a temporary AdminConfig pointing at the test Postgres DB."""
    config = AdminConfig()
    config.database_url = postgres_url
    return config


@pytest.fixture(scope="function")
def db_client(make_test_provider):
    """Create a DatabaseClient connected to test Postgres, with schema initialized."""
    provider = make_test_provider()
    client = DatabaseClient(provider)

    # Initialize schema
    conn = provider.get_connection()
    with conn.cursor() as cur:
        for stmt in ALL_SCHEMA_STATEMENTS:
            cur.execute(stmt)

    yield client

    # Cleanup (use fresh connection in case provider was closed by a test)
    try:
        cleanup_provider = make_test_provider()
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


class TestMaskUrl:
    """Unit tests for URL masking helper."""

    def test_mask_url_with_password(self):
        """Test that password is masked in URL."""
        url = "postgresql://user:secret@localhost/db"
        masked = _mask_url(url)
        assert "secret" not in masked
        assert "****" in masked
        assert masked == "postgresql://user:****@localhost/db"

    def test_mask_url_without_password(self):
        """Test URL without password passes through."""
        url = "postgresql://user@localhost/db"
        assert _mask_url(url) == url

    def test_mask_url_empty(self):
        """Test empty URL handling."""
        assert _mask_url("") == "(not configured)"


class TestGetDbClient:
    """Unit tests for _get_db_client helper."""

    def test_returns_database_client(self, admin_config):
        """Test that _get_db_client returns a DatabaseClient."""
        client = _get_db_client(admin_config)
        assert isinstance(client, DatabaseClient)
        client.close()


@pytest.mark.integration
class TestDbInit:
    """Integration tests for db init command behavior."""

    def test_initialize_schema_idempotent(self, db_client):
        """Schema initialization should be idempotent."""
        # Should not raise even though schema already exists
        db_client.initialize_schema()

    def test_get_stats_on_empty_db(self, db_client):
        """Stats on empty DB should show zero rows."""
        stats = db_client.get_stats()
        assert stats.is_healthy is True
        assert stats.total_songs == 0
        assert stats.total_recordings == 0

    def test_get_stats_after_insert(self, db_client):
        """Stats should reflect inserted rows."""
        from stream_of_worship.admin.db.models import Song

        db_client.insert_song(
            Song(
                id="song_1",
                title="Test",
                source_url="http://test",
                scraped_at="2024-01-01T00:00:00",
            )
        )
        stats = db_client.get_stats()
        assert stats.total_songs == 1
        assert stats.total_recordings == 0
