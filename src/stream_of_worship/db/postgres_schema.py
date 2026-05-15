"""Unified PostgreSQL schema for Stream of Worship.

Combines catalog (songs, recordings), auth (user, account, session,
verification), app (songsets, songset_items), and per-user app tables
(user_settings, user_lrc_override, lyric_mark, songset_share) into a
single ordered DDL list. Used by ``sow-admin db init``.

This module re-exports constants from the per-component schema modules so
DDL has exactly one source of truth.
"""

from stream_of_worship.admin.db.schema import (
    ACTIVE_RECORDINGS_QUERY,
    ACTIVE_SONGS_QUERY,
    CREATE_INDEXES,
    CREATE_RECORDINGS_TABLE,
    CREATE_RECORDINGS_UPDATE_TRIGGER,
    CREATE_SONGS_TABLE,
    CREATE_SONGS_UPDATE_TRIGGER,
    CREATE_UPDATE_TIMESTAMP_FUNCTION,
    RECORDING_COLUMN_COUNT,
    RECORDING_COLUMNS_FOR_JOIN,
    ROW_COUNT_QUERY,
    SONG_COLUMN_COUNT,
    SONG_COLUMNS_FOR_JOIN,
    TABLE_STATS_QUERY,
)
from stream_of_worship.app.db.schema import (
    CREATE_APP_INDEXES,
    CREATE_SONGSET_ITEMS_TABLE,
    CREATE_SONGSETS_TABLE,
    CREATE_SONGSETS_UPDATE_TRIGGER,
    SONGSET_COUNT_QUERY,
    SONGSET_ITEMS_FULL_QUERY,
    SONGSET_ITEMS_QUERY,
)
from stream_of_worship.app.db.user_data_schema import (
    ALL_USER_DATA_SCHEMA_STATEMENTS,
)
from stream_of_worship.db.auth_schema import (
    ALL_AUTH_SCHEMA_STATEMENTS,
)

# Ordered DDL to bring up a fresh Postgres DB.
#
# Order matters:
#   1. Catalog (songs, recordings) — no FKs out to other groups.
#   2. Auth (user, account, ...)   — songsets.user_id FKs into "user".
#   3. App (songsets, items)       — songset_share FKs into songsets.
#   4. Per-user app tables         — FK into both "user" and songsets/recordings.
ALL_SCHEMA_STATEMENTS = [
    # --- 1. admin / catalog ---
    CREATE_SONGS_TABLE,
    CREATE_RECORDINGS_TABLE,
    *CREATE_INDEXES,
    CREATE_UPDATE_TIMESTAMP_FUNCTION,
    CREATE_SONGS_UPDATE_TRIGGER,
    CREATE_RECORDINGS_UPDATE_TRIGGER,
    # --- 2. auth (Better Auth core) ---
    *ALL_AUTH_SCHEMA_STATEMENTS,
    # --- 3. app / songsets ---
    CREATE_SONGSETS_TABLE,
    CREATE_SONGSET_ITEMS_TABLE,
    *CREATE_APP_INDEXES,
    CREATE_SONGSETS_UPDATE_TRIGGER,
    # --- 4. per-user app tables ---
    *ALL_USER_DATA_SCHEMA_STATEMENTS,
]

__all__ = [
    # Re-exports from admin schema
    "ACTIVE_RECORDINGS_QUERY",
    "ACTIVE_SONGS_QUERY",
    "CREATE_INDEXES",
    "CREATE_RECORDINGS_TABLE",
    "CREATE_RECORDINGS_UPDATE_TRIGGER",
    "CREATE_SONGS_TABLE",
    "CREATE_SONGS_UPDATE_TRIGGER",
    "CREATE_UPDATE_TIMESTAMP_FUNCTION",
    "RECORDING_COLUMNS_FOR_JOIN",
    "RECORDING_COLUMN_COUNT",
    "ROW_COUNT_QUERY",
    "SONG_COLUMNS_FOR_JOIN",
    "SONGSET_ITEMS_FULL_QUERY",
    "SONGSET_ITEMS_QUERY",
    "SONGSET_COUNT_QUERY",
    "SONG_COLUMN_COUNT",
    "TABLE_STATS_QUERY",
    # Re-exports from app schema
    "CREATE_APP_INDEXES",
    "CREATE_SONGSET_ITEMS_TABLE",
    "CREATE_SONGSETS_TABLE",
    "CREATE_SONGSETS_UPDATE_TRIGGER",
    # Re-exports from auth + per-user data schema
    "ALL_AUTH_SCHEMA_STATEMENTS",
    "ALL_USER_DATA_SCHEMA_STATEMENTS",
    # Unified list
    "ALL_SCHEMA_STATEMENTS",
]
