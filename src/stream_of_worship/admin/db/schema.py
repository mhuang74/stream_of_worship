"""SQL schema definitions for sow-admin database.

Defines the database schema for storing song catalog and recording metadata.
Uses SQLite with libsql compatibility for Turso sync support.
"""

import sqlite3



# SQL to create the songs table (scraped catalog)
CREATE_SONGS_TABLE = """
CREATE TABLE IF NOT EXISTS songs (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    title_pinyin TEXT,
    composer TEXT,
    lyricist TEXT,
    album_name TEXT,
    album_series TEXT,
    musical_key TEXT,
    lyrics_raw TEXT,
    lyrics_lines TEXT,
    sections TEXT,
    source_url TEXT NOT NULL,
    table_row_number INTEGER,
    scraped_at TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    deleted_at TIMESTAMP
);
"""

# SQL to create the recordings table (hash-indexed audio)
CREATE_RECORDINGS_TABLE = """
CREATE TABLE IF NOT EXISTS recordings (
    content_hash TEXT PRIMARY KEY,
    hash_prefix TEXT NOT NULL UNIQUE,
    song_id TEXT REFERENCES songs(id),
    original_filename TEXT NOT NULL,
    file_size_bytes INTEGER NOT NULL,
    imported_at TEXT NOT NULL,

    -- R2 asset URLs
    r2_audio_url TEXT,
    r2_stems_url TEXT,
    r2_lrc_url TEXT,

    -- Analysis metadata (populated by analysis service)
    duration_seconds REAL,
    tempo_bpm REAL,
    musical_key TEXT,
    musical_mode TEXT,
    key_confidence REAL,
    loudness_db REAL,
    beats TEXT,
    downbeats TEXT,
    sections TEXT,
    embeddings_shape TEXT,

    -- Processing status
    analysis_status TEXT DEFAULT 'pending',
    analysis_job_id TEXT,
    lrc_status TEXT DEFAULT 'pending',
    lrc_job_id TEXT,

    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    youtube_url TEXT,
    visibility_status TEXT,
    deleted_at TIMESTAMP,
    download_status TEXT DEFAULT 'pending'
);
"""

# SQL to create sync_metadata table (for Turso sync tracking)
CREATE_SYNC_METADATA_TABLE = """
CREATE TABLE IF NOT EXISTS sync_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT DEFAULT (datetime('now'))
);
"""

# Indexes for efficient lookups
CREATE_INDEXES = [
    """
    CREATE INDEX IF NOT EXISTS idx_recordings_song_id
    ON recordings(song_id);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_recordings_analysis_status
    ON recordings(analysis_status);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_recordings_hash_prefix
    ON recordings(hash_prefix);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_songs_album
    ON songs(album_name);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_songs_title_pinyin
    ON songs(title_pinyin);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_recordings_visibility_status
    ON recordings(visibility_status);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_songs_deleted_at
    ON songs(deleted_at);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_recordings_deleted_at
    ON recordings(deleted_at);
    """,
]

# Trigger to update updated_at on songs
CREATE_SONGS_UPDATE_TRIGGER = """
CREATE TRIGGER IF NOT EXISTS trg_songs_updated_at
AFTER UPDATE ON songs
BEGIN
    UPDATE songs SET updated_at = datetime('now') WHERE id = NEW.id;
END;
"""

# Trigger to update updated_at on recordings
CREATE_RECORDINGS_UPDATE_TRIGGER = """
CREATE TRIGGER IF NOT EXISTS trg_recordings_updated_at
AFTER UPDATE ON recordings
BEGIN
    UPDATE recordings SET updated_at = datetime('now') WHERE content_hash = NEW.content_hash;
END;
"""

# All schema creation statements in order
ALL_SCHEMA_STATEMENTS = [
    CREATE_SONGS_TABLE,
    CREATE_RECORDINGS_TABLE,
    CREATE_SYNC_METADATA_TABLE,
    *CREATE_INDEXES,
    CREATE_SONGS_UPDATE_TRIGGER,
    CREATE_RECORDINGS_UPDATE_TRIGGER,
]

# SQL to get table statistics
TABLE_STATS_QUERY = """
SELECT
    name,
    (SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=name) as exists_flag
FROM sqlite_master
WHERE type='table'
AND name IN ('songs', 'recordings', 'sync_metadata');
"""

# SQL to count rows in each table
ROW_COUNT_QUERY = """
SELECT
    'songs' as table_name,
    COUNT(*) as row_count
FROM songs
UNION ALL
SELECT
    'recordings' as table_name,
    COUNT(*) as row_count
FROM recordings
UNION ALL
SELECT
    'sync_metadata' as table_name,
    COUNT(*) as row_count
FROM sync_metadata;
"""

# SQL to check database integrity
INTEGRITY_CHECK_QUERY = "PRAGMA integrity_check;"

# SQL to get foreign key status
FOREIGN_KEYS_QUERY = "PRAGMA foreign_keys;"

# Column migrations for existing databases (including Turso remote).
# These MUST remain even though the columns are in CREATE TABLE DDL above.
# Reason: Turso remote and any DBs created before these columns were added
# still have the old schema. ALTER TABLE ADD COLUMN is idempotent here
# because apply_column_migrations() catches "duplicate column" errors.
# MUST append only — never reorder (SQLite appends columns physically).
COLUMN_MIGRATIONS: list[tuple[str, str, str]] = [
    ("recordings", "youtube_url", "TEXT"),
    ("recordings", "visibility_status", "TEXT"),
    ("songs", "deleted_at", "TIMESTAMP"),
    ("recordings", "deleted_at", "TIMESTAMP"),
    ("recordings", "download_status", "TEXT DEFAULT 'pending'"),
]

try:
    import libsql as _libsql_module

    _LIBSQL_ERROR: tuple = (_libsql_module.Error,)
except ImportError:
    _LIBSQL_ERROR = ()


def apply_column_migrations(cursor) -> None:
    """Apply column migrations only for columns that don't exist yet.

    Uses PRAGMA table_info to check existence first, avoiding ALTER TABLE
    on columns that already exist. This is critical for libsql connections
    where a caught ALTER TABLE error still gets replicated to Turso via Hrana,
    causing "duplicate column name" errors on sync.

    Args:
        cursor: Database cursor to execute migrations on.
    """
    tables_needed = {table for table, _, _ in COLUMN_MIGRATIONS}
    existing_columns: dict[str, set[str]] = {}
    for table in tables_needed:
        try:
            cursor.execute(f"PRAGMA table_info({table})")
            existing_columns[table] = {row[1] for row in cursor.fetchall()}
        except (sqlite3.OperationalError, *_LIBSQL_ERROR):
            existing_columns[table] = set()

    for table, column, col_type in COLUMN_MIGRATIONS:
        if column not in existing_columns.get(table, set()):
            try:
                cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
            except (sqlite3.OperationalError, *_LIBSQL_ERROR):
                pass


SONG_COLUMNS_FOR_JOIN = """
    s.id, s.title, s.title_pinyin, s.composer, s.lyricist,
    s.album_name, s.album_series, s.musical_key, s.lyrics_raw,
    s.lyrics_lines, s.sections, s.source_url, s.table_row_number,
    s.scraped_at, s.created_at, s.updated_at, s.deleted_at
"""

RECORDING_COLUMNS_FOR_JOIN = """
    r.content_hash, r.hash_prefix, r.song_id, r.original_filename,
    r.file_size_bytes, r.imported_at, r.r2_audio_url, r.r2_stems_url,
    r.r2_lrc_url, r.duration_seconds, r.tempo_bpm, r.musical_key,
    r.musical_mode, r.key_confidence, r.loudness_db, r.beats,
    r.downbeats, r.sections, r.embeddings_shape, r.analysis_status,
    r.analysis_job_id, r.lrc_status, r.lrc_job_id, r.created_at,
    r.updated_at, r.youtube_url, r.visibility_status, r.deleted_at,
    r.download_status
"""

SONG_COLUMN_COUNT = 17
RECORDING_COLUMN_COUNT = 29

# Default sync metadata values
DEFAULT_SYNC_METADATA = {
    "last_sync_at": "",
    "sync_version": "2",
    "local_device_id": "",
}

# SQL for listing active (non-deleted) songs
ACTIVE_SONGS_QUERY = """
SELECT * FROM songs WHERE deleted_at IS NULL
"""

# SQL for listing active (non-deleted) recordings
ACTIVE_RECORDINGS_QUERY = """
SELECT * FROM recordings WHERE deleted_at IS NULL
"""
