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
CREATE_SONGSET_ITEMS_TABLE = """
CREATE TABLE IF NOT EXISTS songset_items (
    id TEXT PRIMARY KEY,
    songset_id TEXT NOT NULL REFERENCES songsets(id) ON DELETE CASCADE,
    song_id TEXT NOT NULL REFERENCES songs(id),
    recording_hash_prefix TEXT REFERENCES recordings(hash_prefix),
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

# SQL to get songset items with song/recording details
SONGSET_ITEMS_DETAIL_QUERY = """
SELECT
    si.id,
    si.songset_id,
    si.song_id,
    si.recording_hash_prefix,
    si.position,
    si.gap_beats,
    si.crossfade_enabled,
    si.crossfade_duration_seconds,
    si.key_shift_semitones,
    si.tempo_ratio,
    si.created_at,
    s.title as song_title,
    s.musical_key as song_key,
    r.duration_seconds,
    r.tempo_bpm,
    r.musical_key as recording_key,
    r.loudness_db,
    s.composer as song_composer,
    s.lyricist as song_lyricist,
    s.album_name as song_album_name
FROM songset_items si
JOIN songs s ON si.song_id = s.id
LEFT JOIN recordings r ON si.recording_hash_prefix = r.hash_prefix
WHERE si.songset_id = ?
ORDER BY si.position;
"""
