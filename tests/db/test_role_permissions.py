"""Integration tests for Postgres role permission separation.

Verifies that an "app role" with only ``SELECT`` privileges on catalog
(songs/recordings) tables cannot write to them, while still being able to
perform full CRUD on songset tables.

Requires Docker (testcontainers).  Skipped with ``-m 'not integration'``.
"""

import re

import psycopg
import pytest

from stream_of_worship.admin.db.schema import ALL_SCHEMA_STATEMENTS as ADMIN_SCHEMA
from stream_of_worship.app.db.schema import ALL_APP_SCHEMA_STATEMENTS as APP_SCHEMA
from stream_of_worship.db.connection import ConnectionProvider


@pytest.fixture(scope="module")
def role_postgres_url(postgres_url):
    """Create an ``sow_app_test`` role and yield a DSN for it.

    Uses the *original* ``postgres_url`` (superuser) to set up schemas
    and the role, then yields a DSN that uses ``sow_app_test``.
    """
    # Use superuser to set everything up
    provider = ConnectionProvider(postgres_url)
    conn = provider.get_connection()
    cursor = conn.cursor()

    # Ensure schemas exist
    for stmt in ADMIN_SCHEMA:
        cursor.execute(stmt)
    for stmt in APP_SCHEMA:
        cursor.execute(stmt)

    # Ensure role exists
    cursor.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'sow_app_test') THEN
                DROP ROLE sow_app_test;
            END IF;
        END $$;
    """)
    cursor.execute("CREATE ROLE sow_app_test LOGIN PASSWORD 'testpass';")

    # Grant privileges
    cursor.execute("GRANT SELECT ON songs TO sow_app_test;")
    cursor.execute("GRANT SELECT ON recordings TO sow_app_test;")
    cursor.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO sow_app_test;")
    for table in ("songsets", "songset_items"):
        cursor.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO sow_app_test;")

    conn.commit()

    # Build app-role DSN by replacing credentials in postgres_url
    # Original format: postgresql://postgres:xxx@host/db
    app_url = re.sub(
        r"(postgresql://)[^@/]+(@|/)",
        r"\1sow_app_test:testpass\2",
        postgres_url,
        count=1,
    )

    yield app_url

    # Cleanup: drop role after all tests
    try:
        cursor.execute("DROP ROLE sow_app_test;")
        conn.commit()
    except Exception:
        pass
    provider.close()


@pytest.fixture(autouse=True)
def clean(role_postgres_url, postgres_url):
    """Truncate tables before each test using superuser.
    
    We must use the original postgres_url (superuser) because the app role
    does not have TRUNCATE privileges.
    """
    provider = ConnectionProvider(postgres_url)
    conn = provider.get_connection()
    cursor = conn.cursor()
    cursor.execute("TRUNCATE TABLE songs, recordings, songsets, songset_items CASCADE")
    conn.commit()
    provider.close()


@pytest.mark.integration
class TestAppRoleCRUDOnSongsets:
    """App role can create, read, update, and delete songsets/items."""

    def test_app_role_can_crud_songsets(self, role_postgres_url):
        provider = ConnectionProvider(role_postgres_url)
        conn = provider.get_connection()
        cursor = conn.cursor()

        # Create
        cursor.execute(
            "INSERT INTO songsets (id, name) VALUES (%s, %s)",
            ("ss_app1", "AppRoleSet"),
        )
        conn.commit()

        # Read
        cursor.execute("SELECT name FROM songsets WHERE id = %s", ("ss_app1",))
        assert cursor.fetchone()[0] == "AppRoleSet"

        # Update
        cursor.execute("UPDATE songsets SET name = %s WHERE id = %s", ("Updated", "ss_app1"))
        conn.commit()
        cursor.execute("SELECT name FROM songsets WHERE id = %s", ("ss_app1",))
        assert cursor.fetchone()[0] == "Updated"

        # Delete
        cursor.execute("DELETE FROM songsets WHERE id = %s", ("ss_app1",))
        conn.commit()
        cursor.execute("SELECT COUNT(*) FROM songsets WHERE id = %s", ("ss_app1",))
        assert cursor.fetchone()[0] == 0

    def test_app_role_can_crud_songset_items(self, role_postgres_url):
        provider = ConnectionProvider(role_postgres_url)
        conn = provider.get_connection()
        cursor = conn.cursor()

        # Pre-create a songset
        cursor.execute(
            "INSERT INTO songsets (id, name) VALUES (%s, %s)",
            ("ss_items", "ItemsSet"),
        )
        conn.commit()

        # Insert item
        cursor.execute(
            "INSERT INTO songset_items (id, songset_id, song_id, position) VALUES (%s, %s, %s, %s)",
            ("item_1", "ss_items", "song_x", 0),
        )
        conn.commit()

        cursor.execute("SELECT COUNT(*) FROM songset_items WHERE songset_id = %s", ("ss_items",))
        assert cursor.fetchone()[0] == 1

        cursor.execute("DELETE FROM songset_items WHERE id = %s", ("item_1",))
        conn.commit()

        cursor.execute("SELECT COUNT(*) FROM songset_items WHERE songset_id = %s", ("ss_items",))
        assert cursor.fetchone()[0] == 0


@pytest.mark.integration
class TestAppRoleCannotWriteCatalog:
    """App role must be denied INSERT/UPDATE/DELETE on catalog tables."""

    def test_app_role_cannot_insert_into_songs(self, role_postgres_url):
        provider = ConnectionProvider(role_postgres_url)
        conn = provider.get_connection()
        cursor = conn.cursor()

        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            cursor.execute(
                "INSERT INTO songs (id, title, source_url, scraped_at) VALUES (%s, %s, %s, %s)",
                ("unauthorized", "Bad", "http://x", "2024-01-01"),
            )
            conn.commit()

    def test_app_role_cannot_delete_songs(self, role_postgres_url):
        provider = ConnectionProvider(role_postgres_url)
        conn = provider.get_connection()
        cursor = conn.cursor()

        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            cursor.execute("DELETE FROM songs WHERE id = %s", ("does_not_matter",))
            conn.commit()

    def test_app_role_cannot_update_recordings(self, role_postgres_url):
        provider = ConnectionProvider(role_postgres_url)
        conn = provider.get_connection()
        cursor = conn.cursor()

        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            cursor.execute(
                "UPDATE recordings SET analysis_status = %s WHERE hash_prefix = %s",
                ("completed", "abc"),
            )
            conn.commit()


@pytest.mark.integration
class TestAppRoleCanReadCatalog:
    """App role can SELECT from catalog tables."""

    def test_app_role_can_select_songs(self, role_postgres_url, postgres_url):
        # Seed data via superuser
        su = ConnectionProvider(postgres_url)
        cursor = su.get_connection().cursor()
        cursor.execute(
            "INSERT INTO songs (id, title, source_url, scraped_at) VALUES (%s, %s, %s, %s)",
            ("selectable", "Readable", "http://t", "2024-01-01"),
        )
        su.get_connection().commit()
        su.close()

        # Read via app role
        provider = ConnectionProvider(role_postgres_url)
        conn = provider.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT title FROM songs WHERE id = %s", ("selectable",))
        assert cursor.fetchone()[0] == "Readable"
        provider.close()
