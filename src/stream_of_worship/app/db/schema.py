"""SQL schema definitions for sow-app database tables.

PostgreSQL DDL for app-specific tables (songsets, songset_items) with
timestamptz columns for timestamp fields.
"""

# ---------------------------------------------------------------------------
# Songsets table (user-created playlists)
# ---------------------------------------------------------------------------
CREATE_SONGSETS_TABLE = """
CREATE TABLE IF NOT EXISTS songsets (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    created_at timestamptz DEFAULT NOW(),
    updated_at timestamptz DEFAULT NOW()
);
"""

# ---------------------------------------------------------------------------
# Songset items (songs within a songset with transition parameters)
# ---------------------------------------------------------------------------
CREATE_SONGSET_ITEMS_TABLE = """
CREATE TABLE IF NOT EXISTS songset_items (
    id TEXT PRIMARY KEY,
    songset_id TEXT NOT NULL REFERENCES songsets(id) ON DELETE CASCADE,
    song_id TEXT NOT NULL,
    recording_hash_prefix TEXT,
    position INTEGER NOT NULL,
    gap_beats REAL DEFAULT 2.0,
    crossfade_enabled INTEGER DEFAULT 0,
    crossfade_duration_seconds REAL,
    key_shift_semitones INTEGER DEFAULT 0,
    tempo_ratio REAL DEFAULT 1.0,
    created_at timestamptz DEFAULT NOW()
);
"""

# ---------------------------------------------------------------------------
# Deprecated: _sync_metadata is a Turso-specific table.
# Preserved here for import compatibility while SongsetClient is migrated.
# ---------------------------------------------------------------------------
CREATE_SYNC_METADATA_TABLE = """
CREATE TABLE IF NOT EXISTS _sync_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at timestamptz DEFAULT NOW()
);
"""

# ---------------------------------------------------------------------------
# Indexes for efficient lookups (PostgreSQL compatible)
# ---------------------------------------------------------------------------
CREATE_APP_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_songset_items_songset_id ON songset_items(songset_id);",
    "CREATE INDEX IF NOT EXISTS idx_songset_items_position ON songset_items(songset_id, position);",
    "CREATE INDEX IF NOT EXISTS idx_songset_items_song_id ON songset_items(song_id);",
]

# ---------------------------------------------------------------------------
# Trigger for updated_at on songsets (reuses the function defined in admin
# schema; kept here so app schema is self-contained)
# ---------------------------------------------------------------------------
CREATE_SONGSETS_UPDATE_TRIGGER = """
DROP TRIGGER IF EXISTS trg_songsets_updated_at ON songsets;
CREATE TRIGGER trg_songsets_updated_at
    BEFORE UPDATE ON songsets
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();
"""

# ---------------------------------------------------------------------------
# All app schema creation statements (in order)
# ---------------------------------------------------------------------------
ALL_APP_SCHEMA_STATEMENTS = [
    CREATE_SONGSETS_TABLE,
    CREATE_SONGSET_ITEMS_TABLE,
    *CREATE_APP_INDEXES,
    CREATE_SONGSETS_UPDATE_TRIGGER,
]

# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------
SONGSET_COUNT_QUERY = """
SELECT COUNT(*) FROM songsets;
"""

SONGSET_ITEMS_QUERY = """
SELECT
    id,
    songset_id,
    song_id,
    recording_hash_prefix,
    position,
    gap_beats,
    crossfade_enabled,
    crossfade_duration_seconds,
    key_shift_semitones,
    tempo_ratio,
    created_at
FROM songset_items
WHERE songset_id = %s
ORDER BY position;
"""

SONGSET_ITEMS_FULL_QUERY = """
SELECT
    id,
    songset_id,
    song_id,
    recording_hash_prefix,
    position,
    gap_beats,
    crossfade_enabled,
    crossfade_duration_seconds,
    key_shift_semitones,
    tempo_ratio,
    created_at
FROM songset_items
WHERE songset_id = %s
ORDER BY position;
"""
