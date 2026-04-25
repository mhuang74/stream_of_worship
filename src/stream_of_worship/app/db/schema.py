"""SQL schema definitions for sow-app database tables.

Defines the database schema for app-specific tables (songsets, songset_items).
These tables are separate from the admin-managed songs/recordings tables.
"""

# SQL to create the songsets table (user-created playlists)
CREATE_SONGSETS_TABLE = """
CREATE TABLE IF NOT EXISTS songsets (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
"""

# SQL to create the songset_items table (songs in a songset)
# Note: Foreign keys to songs/recordings are intentionally removed for cross-DB compatibility.
# Integrity is enforced in application code. recording_hash_prefix is the canonical anchor.
CREATE_SONGSET_ITEMS_TABLE = """
CREATE TABLE IF NOT EXISTS songset_items (
    id TEXT PRIMARY KEY,
    songset_id TEXT NOT NULL REFERENCES songsets(id) ON DELETE CASCADE,
    song_id TEXT NOT NULL,
    recording_hash_prefix TEXT,
    position INTEGER NOT NULL,
    -- Transition parameters from previous song (null for first song)
    gap_beats REAL DEFAULT 2.0,
    crossfade_enabled INTEGER DEFAULT 0,
    crossfade_duration_seconds REAL,
    -- For future: key adjustment, tempo adjustment
    key_shift_semitones INTEGER DEFAULT 0,
    tempo_ratio REAL DEFAULT 1.0,
    created_at TEXT DEFAULT (datetime('now'))
);
"""

# Indexes for efficient lookups
CREATE_APP_INDEXES = [
    """
    CREATE INDEX IF NOT EXISTS idx_songset_items_songset_id
    ON songset_items(songset_id);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_songset_items_position
    ON songset_items(songset_id, position);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_songset_items_song_id
    ON songset_items(song_id);
    """,
]

# Trigger to update updated_at on songsets
CREATE_SONGSETS_UPDATE_TRIGGER = """
CREATE TRIGGER IF NOT EXISTS trg_songsets_updated_at
AFTER UPDATE ON songsets
BEGIN
    UPDATE songsets SET updated_at = datetime('now') WHERE id = NEW.id;
END;
"""

# All app schema creation statements in order
ALL_APP_SCHEMA_STATEMENTS = [
    CREATE_SONGSETS_TABLE,
    CREATE_SONGSET_ITEMS_TABLE,
    *CREATE_APP_INDEXES,
    CREATE_SONGSETS_UPDATE_TRIGGER,
]

# SQL to count songsets
SONGSET_COUNT_QUERY = """
SELECT COUNT(*) FROM songsets;
"""

# SQL to get songset items (simple query without cross-DB JOINs)
# Cross-DB lookups are done in Python via CatalogService.get_songset_with_items()
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
WHERE songset_id = ?
ORDER BY position;
"""

# SQL to get songset items with orphaned status info (for export/backup)
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
WHERE songset_id = ?
ORDER BY position;
"""
