"""Unit tests for Postgres DDL schema statements.

Uses testcontainers to validate that every schema module's DDL is valid
PostgreSQL.

Run with:
    PYTHONPATH=src uv run --python 3.11 --extra app --extra test pytest tests/db -v
"""

import time

import psycopg
import pytest
from testcontainers.postgres import PostgresContainer


def _pg_url(pg: PostgresContainer) -> str:
    # testcontainers returns postgresql+psycopg2:// but psycopg.connect needs postgresql://
    return pg.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")


@pytest.fixture(scope="session")
def postgres_container():
    with PostgresContainer("postgres:16-alpine") as pg:
        yield pg


@pytest.fixture
def postgres_conn(postgres_container):
    url = _pg_url(postgres_container)
    with psycopg.connect(url) as conn:
        yield conn


# ---------------------------------------------------------------------------
# Tests for admin/db/schema.py
# ---------------------------------------------------------------------------
class TestAdminSchema:
    def test_admin_all_schema_statements(self, postgres_conn):
        from stream_of_worship.admin.db.schema import ALL_SCHEMA_STATEMENTS

        for statement in ALL_SCHEMA_STATEMENTS:
            postgres_conn.execute(statement)
        postgres_conn.commit()

    def test_songs_table_exists(self, postgres_conn):
        from stream_of_worship.admin.db.schema import CREATE_SONGS_TABLE

        postgres_conn.execute(CREATE_SONGS_TABLE)
        postgres_conn.commit()
        cur = postgres_conn.cursor()
        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = 'songs'"
        )
        assert cur.fetchone() is not None

    def test_recordings_table_exists(self, postgres_conn):
        from stream_of_worship.admin.db.schema import CREATE_SONGS_TABLE, CREATE_RECORDINGS_TABLE

        postgres_conn.execute(CREATE_SONGS_TABLE)
        postgres_conn.execute(CREATE_RECORDINGS_TABLE)
        postgres_conn.commit()
        cur = postgres_conn.cursor()
        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = 'recordings'"
        )
        assert cur.fetchone() is not None

    def test_admin_indexes_created(self, postgres_conn):
        from stream_of_worship.admin.db.schema import ALL_SCHEMA_STATEMENTS

        for statement in ALL_SCHEMA_STATEMENTS:
            postgres_conn.execute(statement)
        postgres_conn.commit()
        cur = postgres_conn.cursor()
        cur.execute(
            "SELECT indexname FROM pg_indexes "
            "WHERE schemaname = 'public' AND tablename IN ('songs', 'recordings')"
        )
        indexes = {row[0] for row in cur.fetchall()}
        assert "idx_songs_album" in indexes
        assert "idx_recordings_song_id" in indexes

    def test_updated_at_trigger_fires(self, postgres_conn):
        from stream_of_worship.admin.db.schema import ALL_SCHEMA_STATEMENTS

        for statement in ALL_SCHEMA_STATEMENTS:
            postgres_conn.execute(statement)
        postgres_conn.commit()
        cur = postgres_conn.cursor()
        cur.execute(
            "INSERT INTO songs (id, title, source_url, scraped_at) VALUES (%s, %s, %s, %s)",
            ("song_test", "Test Song", "http://example.com", "2024-01-01T00:00:00"),
        )
        postgres_conn.commit()
        time.sleep(0.05)
        cur.execute("SELECT updated_at FROM songs WHERE id = %s", ("song_test",))
        before = cur.fetchone()[0]
        cur.execute("UPDATE songs SET title = %s WHERE id = %s", ("Updated", "song_test"))
        postgres_conn.commit()
        cur.execute("SELECT updated_at FROM songs WHERE id = %s", ("song_test",))
        after = cur.fetchone()[0]
        assert after >= before, "updated_at trigger did not fire"


# ---------------------------------------------------------------------------
# Tests for app/db/schema.py
# ---------------------------------------------------------------------------
class TestAppSchema:
    def test_app_all_schema_statements(self, postgres_conn):
        from stream_of_worship.app.db.schema import ALL_APP_SCHEMA_STATEMENTS

        # app triggers require the function to exist first
        postgres_conn.execute(
            """
            CREATE OR REPLACE FUNCTION update_updated_at_column()
            RETURNS TRIGGER AS $$
            BEGIN
                NEW.updated_at = NOW();
                RETURN NEW;
            END;
            $$ language 'plpgsql';
            """
        )
        for statement in ALL_APP_SCHEMA_STATEMENTS:
            postgres_conn.execute(statement)
        postgres_conn.commit()

    def test_songsets_table_exists(self, postgres_conn):
        from stream_of_worship.app.db.schema import CREATE_SONGSETS_TABLE

        postgres_conn.execute(CREATE_SONGSETS_TABLE)
        postgres_conn.commit()
        cur = postgres_conn.cursor()
        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = 'songsets'"
        )
        assert cur.fetchone() is not None

    def test_songset_items_table_exists(self, postgres_conn):
        from stream_of_worship.app.db.schema import (
            CREATE_SONGSET_ITEMS_TABLE,
            CREATE_SONGSETS_TABLE,
        )

        postgres_conn.execute(CREATE_SONGSETS_TABLE)
        postgres_conn.execute(CREATE_SONGSET_ITEMS_TABLE)
        postgres_conn.commit()
        cur = postgres_conn.cursor()
        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = 'songset_items'"
        )
        assert cur.fetchone() is not None

    def test_app_indexes_created(self, postgres_conn):
        from stream_of_worship.app.db.schema import ALL_APP_SCHEMA_STATEMENTS

        postgres_conn.execute(
            """
            CREATE OR REPLACE FUNCTION update_updated_at_column()
            RETURNS TRIGGER AS $$
            BEGIN
                NEW.updated_at = NOW();
                RETURN NEW;
            END;
            $$ language 'plpgsql';
            """
        )
        for statement in ALL_APP_SCHEMA_STATEMENTS:
            postgres_conn.execute(statement)
        postgres_conn.commit()
        cur = postgres_conn.cursor()
        cur.execute(
            "SELECT indexname FROM pg_indexes "
            "WHERE schemaname = 'public' AND tablename IN ('songsets', 'songset_items')"
        )
        indexes = {row[0] for row in cur.fetchall()}
        assert "idx_songset_items_songset_id" in indexes
        assert "idx_songset_items_position" in indexes

    def test_songsets_trigger_fires(self, postgres_conn):
        from stream_of_worship.app.db.schema import ALL_APP_SCHEMA_STATEMENTS

        postgres_conn.execute(
            """
            CREATE OR REPLACE FUNCTION update_updated_at_column()
            RETURNS TRIGGER AS $$
            BEGIN
                NEW.updated_at = NOW();
                RETURN NEW;
            END;
            $$ language 'plpgsql';
            """
        )
        for statement in ALL_APP_SCHEMA_STATEMENTS:
            postgres_conn.execute(statement)
        postgres_conn.commit()
        cur = postgres_conn.cursor()
        cur.execute(
            "INSERT INTO songsets (id, name) VALUES (%s, %s)",
            ("ss_1", "Test Songset"),
        )
        postgres_conn.commit()
        time.sleep(0.05)
        cur.execute("SELECT updated_at FROM songsets WHERE id = %s", ("ss_1",))
        before = cur.fetchone()[0]
        cur.execute("UPDATE songsets SET name = %s WHERE id = %s", ("Updated", "ss_1"))
        postgres_conn.commit()
        cur.execute("SELECT updated_at FROM songsets WHERE id = %s", ("ss_1",))
        after = cur.fetchone()[0]
        assert after >= before, "updated_at trigger did not fire"


# ---------------------------------------------------------------------------
# Tests for db/postgres_schema.py (unified)
# ---------------------------------------------------------------------------
class TestUnifiedSchema:
    def test_all_tables_created(self, postgres_conn):
        from stream_of_worship.db.postgres_schema import ALL_SCHEMA_STATEMENTS

        for statement in ALL_SCHEMA_STATEMENTS:
            postgres_conn.execute(statement)
        postgres_conn.commit()
        cur = postgres_conn.cursor()
        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' "
            "AND table_name IN ('songs', 'recordings', 'songsets', 'songset_items')"
        )
        tables = {row[0] for row in cur.fetchall()}
        assert {"songs", "recordings", "songsets", "songset_items"} <= tables

    def test_triggers_exist(self, postgres_conn):
        from stream_of_worship.db.postgres_schema import ALL_SCHEMA_STATEMENTS

        for statement in ALL_SCHEMA_STATEMENTS:
            postgres_conn.execute(statement)
        postgres_conn.commit()
        cur = postgres_conn.cursor()
        cur.execute(
            "SELECT trigger_name FROM information_schema.triggers "
            "WHERE event_object_table IN ('songs', 'recordings', 'songsets')"
        )
        triggers = {row[0] for row in cur.fetchall()}
        assert "trg_songs_updated_at" in triggers
        assert "trg_recordings_updated_at" in triggers
        assert "trg_songsets_updated_at" in triggers
