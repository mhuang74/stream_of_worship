"""Unified PostgreSQL schema for Stream of Worship.

Combines catalog (songs, recordings) and application (songsets, songset_items)
schema definitions into a single source file.  Useful when you need to create
or validate the entire database at once (e.g. ``sow-admin db init`` or test
fixtures).

This module re-exports constants from the canonical per-component schema
modules so that there is only one place where DDL lives.
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
    RECORDING_COLUMNS_FOR_JOIN,
    RECORDING_COLUMN_COUNT,
    ROW_COUNT_QUERY,
    SONG_COLUMNS_FOR_JOIN,
    SONG_COLUMN_COUNT,
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

# Ordered list of *all* DDL statements needed to bring up a fresh Postgres DB.
# Run this through a psycopg cursor / connection in order.
ALL_SCHEMA_STATEMENTS = [
    # --- admin / catalog ---
    CREATE_SONGS_TABLE,
    CREATE_RECORDINGS_TABLE,
    *CREATE_INDEXES,
    CREATE_UPDATE_TIMESTAMP_FUNCTION,
    CREATE_SONGS_UPDATE_TRIGGER,
    CREATE_RECORDINGS_UPDATE_TRIGGER,
    # --- app / songsets ---
    CREATE_SONGSETS_TABLE,
    CREATE_SONGSET_ITEMS_TABLE,
    *CREATE_APP_INDEXES,
    CREATE_SONGSETS_UPDATE_TRIGGER,
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
    # Unified list
    "ALL_SCHEMA_STATEMENTS",
]
