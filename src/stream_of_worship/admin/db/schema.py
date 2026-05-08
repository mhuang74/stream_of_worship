"""SQL schema definitions for sow-admin database.

PostgreSQL DDL for the catalog tables (songs, recordings) with timestamptz
columns for timestamp fields. psycopg3 auto-converts timestamptz values to
Python datetime objects with timezone.utc.
"""

# ---------------------------------------------------------------------------
# Songs table
# ---------------------------------------------------------------------------
# scraped_at is kept as TEXT because it is set by Python (datetime.now().isoformat())
# and never updated by SQL. created_at/updated_at/deleted_at use timestamptz for
# consistent server-side behaviour.

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
    created_at timestamptz DEFAULT NOW(),
    updated_at timestamptz DEFAULT NOW(),
    deleted_at timestamptz
);
"""

# ---------------------------------------------------------------------------
# Recordings table
# ---------------------------------------------------------------------------
# imported_at is kept as TEXT for the same reason as scraped_at.

CREATE_RECORDINGS_TABLE = """
CREATE TABLE IF NOT EXISTS recordings (
    content_hash TEXT PRIMARY KEY,
    hash_prefix TEXT NOT NULL UNIQUE,
    song_id TEXT REFERENCES songs(id),
    original_filename TEXT NOT NULL,
    file_size_bytes INTEGER NOT NULL,
    imported_at TEXT NOT NULL,
    r2_audio_url TEXT,
    r2_stems_url TEXT,
    r2_lrc_url TEXT,
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
    analysis_status TEXT DEFAULT 'pending',
    analysis_job_id TEXT,
    lrc_status TEXT DEFAULT 'pending',
    lrc_job_id TEXT,
    created_at timestamptz DEFAULT NOW(),
    updated_at timestamptz DEFAULT NOW(),
    youtube_url TEXT,
    visibility_status TEXT DEFAULT NULL,
    deleted_at timestamptz,
    download_status TEXT DEFAULT 'pending'
);
"""

# ---------------------------------------------------------------------------
# Deprecated: sync_metadata is a Turso-specific table.
# It is preserved here for import compatibility while the client is migrated
# and is NOT included in ALL_SCHEMA_STATEMENTS.
# ---------------------------------------------------------------------------
CREATE_SYNC_METADATA_TABLE = """
CREATE TABLE IF NOT EXISTS sync_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at timestamptz DEFAULT NOW()
);
"""

# ---------------------------------------------------------------------------
# Indexes (same 8 indexes as before, PostgreSQL compatible)
# ---------------------------------------------------------------------------
CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_recordings_song_id ON recordings(song_id);",
    "CREATE INDEX IF NOT EXISTS idx_recordings_analysis_status ON recordings(analysis_status);",
    "CREATE INDEX IF NOT EXISTS idx_recordings_hash_prefix ON recordings(hash_prefix);",
    "CREATE INDEX IF NOT EXISTS idx_songs_album ON songs(album_name);",
    "CREATE INDEX IF NOT EXISTS idx_songs_title_pinyin ON songs(title_pinyin);",
    "CREATE INDEX IF NOT EXISTS idx_recordings_visibility_status ON recordings(visibility_status);",
    "CREATE INDEX IF NOT EXISTS idx_songs_deleted_at ON songs(deleted_at);",
    "CREATE INDEX IF NOT EXISTS idx_recordings_deleted_at ON recordings(deleted_at);",
]

# ---------------------------------------------------------------------------
# Trigger function for updated_at (PostgreSQL plpgsql)
# ---------------------------------------------------------------------------
CREATE_UPDATED_AT_FUNCTION = """
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';
"""

# ---------------------------------------------------------------------------
# Triggers that invoke the function above
# ---------------------------------------------------------------------------
CREATE_SONGS_UPDATE_TRIGGER = """
DROP TRIGGER IF EXISTS trg_songs_updated_at ON songs;
CREATE TRIGGER trg_songs_updated_at
    BEFORE UPDATE ON songs
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();
"""

CREATE_RECORDINGS_UPDATE_TRIGGER = """
DROP TRIGGER IF EXISTS trg_recordings_updated_at ON recordings;
CREATE TRIGGER trg_recordings_updated_at
    BEFORE UPDATE ON recordings
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();
"""

# ---------------------------------------------------------------------------
# All schema creation statements (in order)
# ---------------------------------------------------------------------------
ALL_SCHEMA_STATEMENTS = [
    CREATE_SONGS_TABLE,
    CREATE_RECORDINGS_TABLE,
    *CREATE_INDEXES,
    CREATE_UPDATED_AT_FUNCTION,
    CREATE_SONGS_UPDATE_TRIGGER,
    CREATE_RECORDINGS_UPDATE_TRIGGER,
]

# ---------------------------------------------------------------------------
# Helper constants used by CatalogService / ReadOnlyClient for JOIN queries.
# ---------------------------------------------------------------------------
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

# ---------------------------------------------------------------------------
# Stats / diagnostic queries (kept for import compatibility; will be
# updated/removed when DatabaseClient is migrated in task 12.3).
# ---------------------------------------------------------------------------
ROW_COUNT_QUERY = """
SELECT 'songs' as table_name, COUNT(*) as row_count FROM songs
UNION ALL
SELECT 'recordings', COUNT(*) FROM recordings;
"""

INTEGRITY_CHECK_QUERY = "SELECT pg_is_in_recovery();"

FOREIGN_KEYS_QUERY = """
SELECT COUNT(*) > 0 FROM information_schema.table_constraints
WHERE constraint_type = 'FOREIGN KEY';
"""

TABLE_STATS_QUERY = """
SELECT table_name, COUNT(*) as row_count
FROM information_schema.tables
WHERE table_name IN ('songs', 'recordings')
GROUP BY table_name;
"""

ACTIVE_SONGS_QUERY = """
SELECT * FROM songs WHERE deleted_at IS NULL;
"""

ACTIVE_RECORDINGS_QUERY = """
SELECT * FROM recordings WHERE deleted_at IS NULL;
"""

# ---------------------------------------------------------------------------
# Deprecated: default sync metadata (Turso-specific).
# Kept for import compatibility during migration.
# ---------------------------------------------------------------------------
DEFAULT_SYNC_METADATA = {
    "last_sync_at": "",
    "sync_version": "3",
    "local_device_id": "",
}
