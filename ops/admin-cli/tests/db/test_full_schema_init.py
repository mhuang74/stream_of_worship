"""Test that ALL_SCHEMA_STATEMENTS produces the full expected schema."""

import pytest

from stream_of_worship.db.connection import ConnectionProvider
from stream_of_worship.db.postgres_schema import ALL_SCHEMA_STATEMENTS


EXPECTED_TABLES = {
    # catalog
    "songs",
    "recordings",
    # auth (Better Auth core)
    "user",
    "account",
    "session",
    "verification",
    # app
    "songsets",
    "songset_items",
    # per-user app
    "user_settings",
    "user_lrc_override",
    "lyric_mark",
    "songset_share",
}


@pytest.mark.integration
class TestFullSchemaInit:
    def test_all_tables_created(self, postgres_url):
        """Running ALL_SCHEMA_STATEMENTS creates every expected table."""
        provider = ConnectionProvider(postgres_url, sslmode="disable")
        conn = provider.get_connection()

        with conn.cursor() as cur:
            for stmt in ALL_SCHEMA_STATEMENTS:
                cur.execute(stmt)

            cur.execute(
                """
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = 'public'
                """
            )
            actual = {row[0] for row in cur.fetchall()}

        missing = EXPECTED_TABLES - actual
        assert not missing, f"Missing tables: {sorted(missing)}"

        # Cleanup
        with conn.cursor() as cur:
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

    def test_critical_foreign_keys(self, postgres_url):
        """Verify the FKs that enforce multi-user isolation are in place."""
        provider = ConnectionProvider(postgres_url, sslmode="disable")
        conn = provider.get_connection()

        with conn.cursor() as cur:
            for stmt in ALL_SCHEMA_STATEMENTS:
                cur.execute(stmt)

            cur.execute(
                """
                SELECT
                    tc.table_name AS from_table,
                    kcu.column_name AS from_column,
                    ccu.table_name AS to_table,
                    ccu.column_name AS to_column
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu
                  ON tc.constraint_name = kcu.constraint_name
                 AND tc.table_schema = kcu.table_schema
                JOIN information_schema.constraint_column_usage ccu
                  ON ccu.constraint_name = tc.constraint_name
                 AND ccu.table_schema = tc.table_schema
                WHERE tc.constraint_type = 'FOREIGN KEY'
                  AND tc.table_schema = 'public'
                """
            )
            fks = {
                (r[0], r[1], r[2], r[3]) for r in cur.fetchall()
            }

        # FKs that MUST exist for multi-user isolation to work.
        required = [
            ("songsets", "user_id", "user", "id"),
            ("user_lrc_override", "user_id", "user", "id"),
            ("user_lrc_override", "recording_content_hash",
             "recordings", "content_hash"),
            ("lyric_mark", "user_id", "user", "id"),
            ("songset_share", "songset_id", "songsets", "id"),
            ("songset_share", "created_by_user_id", "user", "id"),
        ]

        for fk in required:
            assert fk in fks, f"Missing FK: {fk}"

        # Cleanup
        with conn.cursor() as cur:
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
