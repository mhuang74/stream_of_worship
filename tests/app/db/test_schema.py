"""Tests for app database schema.

Tests that SQL DDL statements execute correctly and constraints work as expected.
"""

import sqlite3

import pytest

from stream_of_worship.app.db.schema import (
    ALL_APP_SCHEMA_STATEMENTS,
    CREATE_SONGSETS_TABLE,
    CREATE_SONGSET_ITEMS_TABLE,
    CREATE_APP_INDEXES,
    CREATE_SONGSETS_UPDATE_TRIGGER,
)


@pytest.fixture
def db_connection(tmp_path):
    """Create an in-memory SQLite database with foreign keys enabled."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    yield conn
    conn.close()


class TestSchemaCreation:
    """Tests for schema creation statements."""

    def test_songsets_table_created(self, db_connection):
        """Verify CREATE_SONGSETS_TABLE executes without error."""
        cursor = db_connection.cursor()
        cursor.execute(CREATE_SONGSETS_TABLE)
        db_connection.commit()

        # Verify table exists
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='songsets'")
        assert cursor.fetchone() is not None

    def test_songset_items_table_created(self, db_connection):
        """Verify CREATE_SONGSET_ITEMS_TABLE executes without error."""
        cursor = db_connection.cursor()
        # Need to create songsets first for FK constraint
        cursor.execute(CREATE_SONGSETS_TABLE)
        cursor.execute(CREATE_SONGSET_ITEMS_TABLE)
        db_connection.commit()

        # Verify table exists
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='songset_items'")
        assert cursor.fetchone() is not None

    def test_all_schema_statements_execute(self, db_connection):
        """Verify all schema statements execute without error."""
        cursor = db_connection.cursor()

        # Need to create admin tables first for FK constraints
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS songs (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                source_url TEXT NOT NULL,
                scraped_at TEXT NOT NULL
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS recordings (
                content_hash TEXT PRIMARY KEY,
                hash_prefix TEXT UNIQUE NOT NULL,
                song_id TEXT REFERENCES songs(id),
                original_filename TEXT NOT NULL,
                file_size_bytes INTEGER NOT NULL,
                imported_at TEXT NOT NULL
            )
        """)

        for statement in ALL_APP_SCHEMA_STATEMENTS:
            cursor.execute(statement)
        db_connection.commit()

        # Verify all expected tables exist
        cursor.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table' AND name IN ('songsets', 'songset_items')
        """)
        tables = [row[0] for row in cursor.fetchall()]
        assert 'songsets' in tables
        assert 'songset_items' in tables


class TestForeignKeyConstraints:
    """Tests for foreign key constraint enforcement."""

    @pytest.fixture
    def schema_db(self, db_connection):
        """Database with full schema including admin tables."""
        cursor = db_connection.cursor()

        # Create admin tables
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS songs (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                source_url TEXT NOT NULL,
                scraped_at TEXT NOT NULL
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS recordings (
                content_hash TEXT PRIMARY KEY,
                hash_prefix TEXT UNIQUE NOT NULL,
                song_id TEXT REFERENCES songs(id),
                original_filename TEXT NOT NULL,
                file_size_bytes INTEGER NOT NULL,
                imported_at TEXT NOT NULL
            )
        """)

        # Create app tables
        for statement in ALL_APP_SCHEMA_STATEMENTS:
            cursor.execute(statement)

        db_connection.commit()
        return db_connection

    def test_foreign_key_references_work(self, schema_db):
        """Verify FK constraints on song_id, recording_hash_prefix are enforced."""
        cursor = schema_db.cursor()

        # Insert valid parent records
        cursor.execute(
            "INSERT INTO songs (id, title, source_url, scraped_at) VALUES (?, ?, ?, ?)",
            ("song_0001", "Test Song", "http://example.com", "2024-01-01T00:00:00")
        )
        cursor.execute(
            "INSERT INTO recordings (content_hash, hash_prefix, song_id, original_filename, file_size_bytes, imported_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("abc123" * 8, "abc123def456", "song_0001", "test.mp3", 1000, "2024-01-01T00:00:00")
        )
        cursor.execute(
            "INSERT INTO songsets (id, name, created_at, updated_at) VALUES (?, ?, ?, ?)",
            ("songset_0001", "Test Songset", "2024-01-01T00:00:00", "2024-01-01T00:00:00")
        )

        # Insert valid child record - should succeed
        cursor.execute(
            """INSERT INTO songset_items
                (id, songset_id, song_id, recording_hash_prefix, position)
                VALUES (?, ?, ?, ?, ?)""",
            ("item_0001", "songset_0001", "song_0001", "abc123def456", 0)
        )
        schema_db.commit()

        # Verify insert worked
        cursor.execute("SELECT COUNT(*) FROM songset_items")
        assert cursor.fetchone()[0] == 1

    def test_foreign_key_constraint_song_id(self, schema_db):
        """Verify FK error on invalid song_id."""
        cursor = schema_db.cursor()

        cursor.execute(
            "INSERT INTO songsets (id, name, created_at, updated_at) VALUES (?, ?, ?, ?)",
            ("songset_0001", "Test Songset", "2024-01-01T00:00:00", "2024-01-01T00:00:00")
        )

        # Try to insert with invalid song_id - should fail
        with pytest.raises(sqlite3.IntegrityError):
            cursor.execute(
                """INSERT INTO songset_items
                    (id, songset_id, song_id, position)
                    VALUES (?, ?, ?, ?)""",
                ("item_0001", "songset_0001", "invalid_song", 0)
            )

    def test_foreign_key_constraint_recording_hash(self, schema_db):
        """Verify FK error on invalid recording_hash_prefix."""
        cursor = schema_db.cursor()

        cursor.execute(
            "INSERT INTO songs (id, title, source_url, scraped_at) VALUES (?, ?, ?, ?)",
            ("song_0001", "Test Song", "http://example.com", "2024-01-01T00:00:00")
        )
        cursor.execute(
            "INSERT INTO songsets (id, name, created_at, updated_at) VALUES (?, ?, ?, ?)",
            ("songset_0001", "Test Songset", "2024-01-01T00:00:00", "2024-01-01T00:00:00")
        )

        # Try to insert with invalid recording_hash_prefix - should fail
        with pytest.raises(sqlite3.IntegrityError):
            cursor.execute(
                """INSERT INTO songset_items
                    (id, songset_id, song_id, recording_hash_prefix, position)
                    VALUES (?, ?, ?, ?, ?)""",
                ("item_0001", "songset_0001", "song_0001", "invalid_hash", 0)
            )


class TestConstraints:
    """Tests for table constraints."""

    @pytest.fixture
    def schema_db(self, db_connection):
        """Database with full schema."""
        cursor = db_connection.cursor()

        # Create admin tables
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS songs (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                source_url TEXT NOT NULL,
                scraped_at TEXT NOT NULL
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS recordings (
                content_hash TEXT PRIMARY KEY,
                hash_prefix TEXT UNIQUE NOT NULL,
                song_id TEXT REFERENCES songs(id),
                original_filename TEXT NOT NULL,
                file_size_bytes INTEGER NOT NULL,
                imported_at TEXT NOT NULL
            )
        """)

        for statement in ALL_APP_SCHEMA_STATEMENTS:
            cursor.execute(statement)

        db_connection.commit()
        return db_connection

    def test_index_on_position_exists(self, schema_db):
        """Verify index on (songset_id, position) exists for efficient queries."""
        cursor = schema_db.cursor()

        cursor.execute(
            """SELECT name FROM sqlite_master
               WHERE type='index' AND name='idx_songset_items_position'"""
        )
        result = cursor.fetchone()

        assert result is not None

    def test_cascade_delete_removes_items(self, schema_db):
        """Verify deleting songset cascades to items."""
        cursor = schema_db.cursor()

        cursor.execute(
            "INSERT INTO songs (id, title, source_url, scraped_at) VALUES (?, ?, ?, ?)",
            ("song_0001", "Test Song", "http://example.com", "2024-01-01T00:00:00")
        )
        cursor.execute(
            "INSERT INTO songsets (id, name, created_at, updated_at) VALUES (?, ?, ?, ?)",
            ("songset_0001", "Test Songset", "2024-01-01T00:00:00", "2024-01-01T00:00:00")
        )
        cursor.execute(
            """INSERT INTO songset_items
                (id, songset_id, song_id, position)
                VALUES (?, ?, ?, ?)""",
            ("item_0001", "songset_0001", "song_0001", 0)
        )
        schema_db.commit()

        # Verify item exists
        cursor.execute("SELECT COUNT(*) FROM songset_items WHERE songset_id = ?", ("songset_0001",))
        assert cursor.fetchone()[0] == 1

        # Delete songset
        cursor.execute("DELETE FROM songsets WHERE id = ?", ("songset_0001",))
        schema_db.commit()

        # Verify item was cascade deleted
        cursor.execute("SELECT COUNT(*) FROM songset_items WHERE songset_id = ?", ("songset_0001",))
        assert cursor.fetchone()[0] == 0

    def test_updated_at_trigger_fires(self, schema_db):
        """Verify trigger updates updated_at on modification."""
        cursor = schema_db.cursor()

        cursor.execute(
            "INSERT INTO songsets (id, name, created_at, updated_at) VALUES (?, ?, ?, ?)",
            ("songset_0001", "Test Songset", "2024-01-01T12:00:00", "2024-01-01T12:00:00")
        )
        schema_db.commit()

        # Get initial updated_at
        cursor.execute("SELECT updated_at FROM songsets WHERE id = ?", ("songset_0001",))
        initial_updated_at = cursor.fetchone()[0]

        # Update the songset
        cursor.execute(
            "UPDATE songsets SET name = ? WHERE id = ?",
            ("Updated Name", "songset_0001")
        )
        schema_db.commit()

        # Verify updated_at changed
        cursor.execute("SELECT updated_at FROM songsets WHERE id = ?", ("songset_0001",))
        new_updated_at = cursor.fetchone()[0]

        assert new_updated_at != initial_updated_at
