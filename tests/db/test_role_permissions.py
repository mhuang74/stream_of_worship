"""Integration tests for Postgres role-based access control.

Verifies that the app role can read catalog tables and CRUD songset tables,
but cannot write to catalog tables (songs, recordings).
"""

import pytest


@pytest.fixture(scope="function")
def restricted_connection(postgres_url):
    """Create a restricted app role and yield a connection using it.

    Returns:
        Tuple of (admin_connection, restricted_connection_url).
    """
    import urllib.parse

    import psycopg

    conn = psycopg.connect(postgres_url)

    # Create schema first
    from stream_of_worship.db.postgres_schema import ALL_SCHEMA_STATEMENTS

    with conn.cursor() as cur:
        for stmt in ALL_SCHEMA_STATEMENTS:
            cur.execute(stmt)
    conn.commit()

    # Create restricted role (re-create each time for isolation)
    # Must use autocommit for CREATE ROLE (cannot be in transaction)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("DROP ROLE IF EXISTS sow_app_test;")
        cur.execute("CREATE ROLE sow_app_test WITH LOGIN PASSWORD 'testpass';")
        # Extract DB name from the URL path
        parsed = urllib.parse.urlparse(postgres_url)
        db_name = parsed.path.lstrip("/") or "test"
        cur.execute(f'GRANT CONNECT ON DATABASE "{db_name}" TO sow_app_test;')
        cur.execute("GRANT USAGE ON SCHEMA public TO sow_app_test;")
        # Read-only on catalog
        cur.execute("GRANT SELECT ON songs TO sow_app_test;")
        cur.execute("GRANT SELECT ON recordings TO sow_app_test;")
        # Read-only on auth user table (for TUI pick-a-user); no writes on
        # account/session/verification (webapp Better Auth role owns those).
        cur.execute('GRANT SELECT ON "user" TO sow_app_test;')
        # Full CRUD on songsets
        cur.execute("GRANT ALL ON songsets TO sow_app_test;")
        cur.execute("GRANT ALL ON songset_items TO sow_app_test;")
        # Full CRUD on per-user app tables
        cur.execute("GRANT ALL ON user_settings TO sow_app_test;")
        cur.execute("GRANT ALL ON user_lrc_override TO sow_app_test;")
        cur.execute("GRANT ALL ON lyric_mark TO sow_app_test;")
        cur.execute("GRANT ALL ON songset_share TO sow_app_test;")
        # Allow sequence usage for songset inserts
        cur.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO sow_app_test;")
    conn.autocommit = False

    # Build restricted URL using the *same* database
    restricted_url = (
        f"postgresql://sow_app_test:testpass@{parsed.hostname}:{parsed.port}/{db_name}"
    )

    yield conn, restricted_url

    # Cleanup: drop role's owned objects first, then the role itself
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("DROP OWNED BY sow_app_test CASCADE;")
        cur.execute("DROP ROLE IF EXISTS sow_app_test;")
    conn.close()


@pytest.mark.integration
class TestRolePermissions:
    """Test that Postgres role restrictions are enforced."""

    def test_app_role_can_select_songs(self, restricted_connection):
        """App role should be able to read from songs table."""
        admin_conn, restricted_url = restricted_connection
        import psycopg

        restricted_conn = psycopg.connect(restricted_url)

        # Insert via admin
        with admin_conn.cursor() as cur:
            cur.execute(
                "INSERT INTO songs (id, title, source_url, scraped_at) VALUES (%s, %s, %s, %s)",
                ("song_1", "Test Song", "http://test", "2024-01-01"),
            )
            admin_conn.commit()

        # Read via restricted
        with restricted_conn.cursor() as cur:
            cur.execute("SELECT id, title FROM songs WHERE id = %s", ("song_1",))
            row = cur.fetchone()
            assert row is not None
            assert row[1] == "Test Song"

        restricted_conn.close()

    def test_app_role_cannot_insert_into_songs(self, restricted_connection):
        """App role should NOT be able to write to songs table."""
        _, restricted_url = restricted_connection
        import psycopg

        restricted_conn = psycopg.connect(restricted_url)

        with pytest.raises(Exception):
            with restricted_conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO songs (id, title, source_url, scraped_at) VALUES (%s, %s, %s, %s)",
                    ("song_2", "Test", "http://test", "2024-01-01"),
                )
                restricted_conn.commit()

        restricted_conn.close()

    def _seed_user(self, admin_conn) -> int:
        """Insert a user via the admin connection and return its id (idempotent)."""
        with admin_conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO "user" ("name", "email") VALUES (%s, %s)
                ON CONFLICT ("email") DO UPDATE SET "name" = EXCLUDED."name"
                RETURNING "id"
                """,
                ("Role Test User", "role-test@example.com"),
            )
            user_id = cur.fetchone()[0]
            admin_conn.commit()
        return user_id

    def test_app_role_can_crud_songsets(self, restricted_connection):
        """App role should be able to create, read, update, delete songsets."""
        admin_conn, restricted_url = restricted_connection
        import psycopg

        user_id = self._seed_user(admin_conn)
        restricted_conn = psycopg.connect(restricted_url)

        # Create songset
        with restricted_conn.cursor() as cur:
            cur.execute(
                "INSERT INTO songsets (id, user_id, name) VALUES (%s, %s, %s)",
                ("set_1", user_id, "Test Set"),
            )
            restricted_conn.commit()

        # Read
        with restricted_conn.cursor() as cur:
            cur.execute("SELECT name FROM songsets WHERE id = %s", ("set_1",))
            row = cur.fetchone()
            assert row[0] == "Test Set"

        # Update
        with restricted_conn.cursor() as cur:
            cur.execute("UPDATE songsets SET name = %s WHERE id = %s", ("Updated", "set_1"))
            restricted_conn.commit()

        # Delete
        with restricted_conn.cursor() as cur:
            cur.execute("DELETE FROM songsets WHERE id = %s", ("set_1",))
            restricted_conn.commit()

        # Verify deleted
        with restricted_conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM songsets WHERE id = %s", ("set_1",))
            assert cur.fetchone()[0] == 0

        restricted_conn.close()

    def test_app_role_can_crud_songset_items(self, restricted_connection):
        """App role should be able to manage songset_items."""
        admin_conn, restricted_url = restricted_connection
        import psycopg

        user_id = self._seed_user(admin_conn)
        restricted_conn = psycopg.connect(restricted_url)

        # Create songset first
        with restricted_conn.cursor() as cur:
            cur.execute(
                "INSERT INTO songsets (id, user_id, name) VALUES (%s, %s, %s)",
                ("set_1", user_id, "Test Set"),
            )
            restricted_conn.commit()

        # Create item
        with restricted_conn.cursor() as cur:
            cur.execute(
                """INSERT INTO songset_items
                   (id, songset_id, song_id, position)
                   VALUES (%s, %s, %s, %s)""",
                ("item_1", "set_1", "song_1", 0),
            )
            restricted_conn.commit()

        # Read
        with restricted_conn.cursor() as cur:
            cur.execute("SELECT song_id FROM songset_items WHERE id = %s", ("item_1",))
            assert cur.fetchone()[0] == "song_1"

        # Update
        with restricted_conn.cursor() as cur:
            cur.execute(
                "UPDATE songset_items SET position = %s WHERE id = %s",
                (1, "item_1"),
            )
            restricted_conn.commit()

        # Delete
        with restricted_conn.cursor() as cur:
            cur.execute("DELETE FROM songset_items WHERE id = %s", ("item_1",))
            restricted_conn.commit()

        restricted_conn.close()

    def test_app_role_can_select_user(self, restricted_connection):
        """App role should be able to read from the user table (for TUI picker)."""
        admin_conn, restricted_url = restricted_connection
        import psycopg

        self._seed_user(admin_conn)
        restricted_conn = psycopg.connect(restricted_url)

        with restricted_conn.cursor() as cur:
            cur.execute('SELECT "name" FROM "user" WHERE "email" = %s',
                        ("role-test@example.com",))
            row = cur.fetchone()
            assert row is not None
            assert row[0] == "Role Test User"

        restricted_conn.close()

    def test_app_role_cannot_insert_into_user(self, restricted_connection):
        """App role must NOT be able to create users; that's admin/webapp only."""
        _, restricted_url = restricted_connection
        import psycopg

        restricted_conn = psycopg.connect(restricted_url)

        with pytest.raises(Exception):
            with restricted_conn.cursor() as cur:
                cur.execute(
                    'INSERT INTO "user" ("name", "email") VALUES (%s, %s)',
                    ("Sneaky", "sneak@example.com"),
                )
                restricted_conn.commit()

        restricted_conn.close()

    def test_app_role_can_crud_user_settings(self, restricted_connection):
        """App role should be able to CRUD per-user app tables."""
        admin_conn, restricted_url = restricted_connection
        import psycopg

        user_id = self._seed_user(admin_conn)
        restricted_conn = psycopg.connect(restricted_url)

        with restricted_conn.cursor() as cur:
            cur.execute(
                "INSERT INTO user_settings (user_id, offline_auto_cache) VALUES (%s, %s)",
                (user_id, False),
            )
            cur.execute(
                "SELECT offline_auto_cache FROM user_settings WHERE user_id = %s",
                (user_id,),
            )
            assert cur.fetchone()[0] is False
            cur.execute("DELETE FROM user_settings WHERE user_id = %s", (user_id,))
            restricted_conn.commit()

        restricted_conn.close()
