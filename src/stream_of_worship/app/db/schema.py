"""SQL schema definitions for sow-app database tables (PostgreSQL).

Defines the database schema for app-specific tables (songsets, songset_items).
These tables live in the same Neon Postgres database as the admin catalog tables.
"""

from stream_of_worship.admin.db.schema import CREATE_UPDATE_TIMESTAMP_FUNCTION

# SQL to create the songsets table (user-created playlists)
# user_id is BIGINT (matching "user"."id" identity column) and NOT NULL so
# every songset has an owner. ON DELETE CASCADE removes a user's songsets
# (and their items, via the FK on songset_items) when the user is deleted.
CREATE_SONGSETS_TABLE = """
CREATE TABLE IF NOT EXISTS songsets (
    id TEXT PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES "user"("id") ON DELETE CASCADE,
    name TEXT NOT NULL,
    description TEXT,
    created_at timestamptz DEFAULT NOW(),
    updated_at timestamptz DEFAULT NOW()
);
"""

# SQL to create the songset_items table (songs in a songset)
# The FK on songset_id is enforced by Postgres. song_id references the
# songs table in the same database but is intentionally left as plain TEXT
# to keep data-insertion decoupled from catalog validation.
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
    created_at timestamptz DEFAULT NOW()
);
"""

# Indexes for efficient lookups
CREATE_APP_INDEXES = [
    """
    CREATE INDEX IF NOT EXISTS idx_songsets_user_id
    ON songsets(user_id);
    """,
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
DROP TRIGGER IF EXISTS trg_songsets_updated_at ON songsets;
CREATE TRIGGER trg_songsets_updated_at
    BEFORE UPDATE ON songsets
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();
"""

# All app schema creation statements in order
# Note: CREATE_UPDATE_TIMESTAMP_FUNCTION must come before the trigger that uses it
ALL_APP_SCHEMA_STATEMENTS = [
    CREATE_SONGSETS_TABLE,
    CREATE_SONGSET_ITEMS_TABLE,
    *CREATE_APP_INDEXES,
    CREATE_UPDATE_TIMESTAMP_FUNCTION,
    CREATE_SONGSETS_UPDATE_TRIGGER,
]

# SQL to count songsets
SONGSET_COUNT_QUERY = """
SELECT COUNT(*) FROM songsets;
"""

# SQL to get songset items (simple query without JOINs)
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
WHERE songset_id = %s
ORDER BY position;
"""
