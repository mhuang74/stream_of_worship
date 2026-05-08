"""Tests for app database schema (PostgreSQL).

These are integration tests that require a real Postgres instance.
"""

import pytest
from testcontainers.postgres import PostgresContainer

from stream_of_worship.app.db.schema import (
    ALL_APP_SCHEMA_STATEMENTS,
    CREATE_SONGSETS_TABLE,
    CREATE_SONGSET_ITEMS_TABLE,
    CREATE_APP_INDEXES,
)


def _pg_url(pg: PostgresContainer) -> str:
    return pg.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")


@pytest.fixture(scope="module")
def postgres_url():
    """Start a Postgres container for the module."""
    with PostgresContainer("postgres:16-alpine") as pg:
        yield _pg_url(pg)


@pytest.fixture
def conn(postgres_url):
    """Yield a psycopg connection and close it after the test."""
    import psycopg

    c = psycopg.connect(postgres_url)
    yield c
    c.close()


@pytest.mark.integration
class TestSchemaCreation:
    """Tests for schema creation statements."""

    def test_songsets_table_created(self, conn):
        """Verify CREATE_SONGSETS_TABLE executes without error."""
        cursor = conn.cursor()
        cursor.execute(CREATE_SONGSETS_TABLE)
        conn.commit()

        cursor.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_name = 'songsets'"
        )
        assert cursor.fetchone() is not None

    def test_songset_items_table_created(self, conn):
        """Verify CREATE_SONGSET_ITEMS_TABLE executes without error."""
        cursor = conn.cursor()
        cursor.execute(CREATE_SONGSETS_TABLE)
        cursor.execute(CREATE_SONGSET_ITEMS_TABLE)
        conn.commit()

        cursor.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_name = 'songset_items'"
        )
        assert cursor.fetchone() is not None

    def test_all_schema_statements_execute(self, conn):
        """Verify all schema statements execute without error."""
        cursor = conn.cursor()

        # Need the trigger function first (defined in admin schema)
        cursor.execute("""
            CREATE OR REPLACE FUNCTION update_updated_at_column()
            RETURNS TRIGGER AS $$
            BEGIN
                NEW.updated_at = NOW();
                RETURN NEW;
            END;
            $$ language 'plpgsql';
        """)

        for statement in ALL_APP_SCHEMA_STATEMENTS:
            cursor.execute(statement)
        conn.commit()

        cursor.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_name IN ('songsets', 'songset_items')"
        )
        tables = [row[0] for row in cursor.fetchall()]
        assert "songsets" in tables
        assert "songset_items" in tables


@pytest.mark.integration
class TestForeignKeyConstraints:
    """Tests for foreign key constraint enforcement."""

    @pytest.fixture
    def schema_db(self, conn):
        """Database with full schema."""
        cursor = conn.cursor()

        # Ensure a clean slate for this test class
        for tbl in ['songset_items', 'songsets', 'songs']:
            cursor.execute(f"DROP TABLE IF EXISTS {tbl} CASCADE")

        # Create admin tables (simplified, just enough for FK to work)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS songs (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                source_url TEXT NOT NULL,
                scraped_at TEXT NOT NULL
            )
        """)

        # Create trigger function
        cursor.execute("""
            CREATE OR REPLACE FUNCTION update_updated_at_column()
            RETURNS TRIGGER AS $$
            BEGIN
                NEW.updated_at = NOW();
                RETURN NEW;
            END;
            $$ language 'plpgsql';
        """)

        # Create app tables
        for statement in ALL_APP_SCHEMA_STATEMENTS:
            cursor.execute(statement)

        conn.commit()
        return conn

    def test_foreign_key_references_work(self, schema_db):
        """Verify FK constraint on songset_id is enforced."""
        cursor = schema_db.cursor()

        cursor.execute(
            "INSERT INTO songsets (id, name) VALUES (%s, %s)", ("songset_0001", "Test Songset")
        )
        cursor.execute(
            "INSERT INTO songset_items (id, songset_id, song_id, position) "
            "VALUES (%s, %s, %s, %s)",
            ("item_0001", "songset_0001", "song_0001", 0),
        )
        schema_db.commit()

        cursor.execute("SELECT COUNT(*) FROM songset_items")
        assert cursor.fetchone()[0] == 1

    def test_foreign_key_constraint_songset_id(self, schema_db):
        """Verify FK error on invalid songset_id."""
        import psycopg

        cursor = schema_db.cursor()

        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            cursor.execute(
                "INSERT INTO songset_items (id, songset_id, song_id, position) "
                "VALUES (%s, %s, %s, %s)",
                ("item_0001", "invalid_set", "song_0001", 0),
            )


@pytest.mark.integration
class TestConstraints:
    """Tests for table constraints."""

    @pytest.fixture
    def schema_db(self, conn):
        """Database with full schema."""
        cursor = conn.cursor()

        # Ensure a clean slate for this test class
        for tbl in ['songset_items', 'songsets', 'songs']:
            cursor.execute(f"DROP TABLE IF EXISTS {tbl} CASCADE")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS songs (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                source_url TEXT NOT NULL,
                scraped_at TEXT NOT NULL
            )
        """)
        cursor.execute("""
            CREATE OR REPLACE FUNCTION update_updated_at_column()
            RETURNS TRIGGER AS $$
            BEGIN
                NEW.updated_at = NOW();
                RETURN NEW;
            END;
            $$ language 'plpgsql';
        """)

        for statement in ALL_APP_SCHEMA_STATEMENTS:
            cursor.execute(statement)

        conn.commit()
        return conn

    def test_index_on_position_exists(self, schema_db):
        """Verify index on (songset_id, position) exists."""
        cursor = schema_db.cursor()

        cursor.execute(
            "SELECT indexname FROM pg_indexes "
            "WHERE tablename = 'songset_items' AND indexname = 'idx_songset_items_position'"
        )
        assert cursor.fetchone() is not None

    def test_cascade_delete_removes_items(self, schema_db):
        """Verify deleting songset cascades to items."""
        import psycopg

        cursor = schema_db.cursor()

        cursor.execute(
            "INSERT INTO songsets (id, name) VALUES (%s, %s)", ("songset_0001", "Test Songset")
        )
        cursor.execute(
            "INSERT INTO songset_items (id, songset_id, song_id, position) "
            "VALUES (%s, %s, %s, %s)",
            ("item_0001", "songset_0001", "song_0001", 0),
        )
        schema_db.commit()

        cursor.execute(
            "SELECT COUNT(*) FROM songset_items WHERE songset_id = %s", ("songset_0001",)
        )
        assert cursor.fetchone()[0] == 1

        cursor.execute("DELETE FROM songsets WHERE id = %s", ("songset_0001",))
        schema_db.commit()

        cursor.execute(
            "SELECT COUNT(*) FROM songset_items WHERE songset_id = %s", ("songset_0001",)
        )
        assert cursor.fetchone()[0] == 0

    def test_updated_at_trigger_fires(self, schema_db):
        """Verify trigger updates updated_at on modification."""
        import time

        cursor = schema_db.cursor()

        cursor.execute(
            "INSERT INTO songsets (id, name, created_at, updated_at) VALUES (%s, %s, NOW(), NOW())",
            ("songset_0001", "Test Songset"),
        )
        schema_db.commit()

        cursor.execute("SELECT updated_at FROM songsets WHERE id = %s", ("songset_0001",))
        initial = cursor.fetchone()[0]

        time.sleep(0.05)

        cursor.execute(
            "UPDATE songsets SET name = %s WHERE id = %s", ("Updated Name", "songset_0001")
        )
        schema_db.commit()

        cursor.execute("SELECT updated_at FROM songsets WHERE id = %s", ("songset_0001",))
        new = cursor.fetchone()[0]

        assert new >= initial
