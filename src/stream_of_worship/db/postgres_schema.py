"""Unified PostgreSQL DDL for the Stream of Worship database.

Contains all DDL statements for catalog tables (songs, recordings) and
app tables (songsets, songset_items) in a single file.

Key design decisions:
- Timestamps for created_at / updated_at / deleted_at use ``timestamptz``.
- ``scraped_at`` and ``imported_at`` remain ``TEXT`` (set by Python, never
  modified by SQL; avoids reformatting historical data).
- The trigger function ``update_updated_at_column()`` is defined once and
  re-used by all tables that need auto-updating ``updated_at``.
- ``sync_metadata`` and ``_sync_metadata`` are **not** created (Turso-specific).

A single ``ALL_SCHEMA_STATEMENTS`` list executes everything in the correct
order.
"""

# ---------------------------------------------------------------------------
# Shared trigger function (must exist before triggers that reference it)
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
# Songs table
# ---------------------------------------------------------------------------
# scraped_at is kept as TEXT because it is set by Python (datetime.now().isoformat())
# and never updated by SQL.  created_at/updated_at/deleted_at use timestamptz for
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
    deleted_at timestamptz
);
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
# Indexes for catalog tables
# ---------------------------------------------------------------------------
CREATE_ADMIN_INDEXES = [
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
# Indexes for app tables
# ---------------------------------------------------------------------------
CREATE_APP_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_songset_items_songset_id ON songset_items(songset_id);",
    "CREATE INDEX IF NOT EXISTS idx_songset_items_position ON songset_items(songset_id, position);",
    "CREATE INDEX IF NOT EXISTS idx_songset_items_song_id ON songset_items(song_id);",
]

# ---------------------------------------------------------------------------
# Triggers (re-use the shared function above)
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

CREATE_SONGSETS_UPDATE_TRIGGER = """
DROP TRIGGER IF EXISTS trg_songsets_updated_at ON songsets;
CREATE TRIGGER trg_songsets_updated_at
    BEFORE UPDATE ON songsets
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();
"""

# ---------------------------------------------------------------------------
# Complete ordered list of statements for fresh schema initialization.
#
# Order matters:
#   1. tables (songs before recordings because of FK,
#             songsets before songset_items because of FK)
#   2. indexes
#   3. trigger function
#   4. triggers
# ---------------------------------------------------------------------------
ALL_SCHEMA_STATEMENTS = [
    CREATE_SONGS_TABLE,
    CREATE_RECORDINGS_TABLE,
    CREATE_SONGSETS_TABLE,
    CREATE_SONGSET_ITEMS_TABLE,
    *CREATE_ADMIN_INDEXES,
    *CREATE_APP_INDEXES,
    CREATE_UPDATED_AT_FUNCTION,
    CREATE_SONGS_UPDATE_TRIGGER,
    CREATE_RECORDINGS_UPDATE_TRIGGER,
    CREATE_SONGSETS_UPDATE_TRIGGER,
]

# ---------------------------------------------------------------------------
# Query helpers (used by client code and catalog service)
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
    r.analysis_job_id, r.lrc_status, r.lrc_job_id, r.youtube_url,
    r.visibility_status, r.created_at, r.updated_at, r.deleted_at
"""

SONG_COLUMN_COUNT = 17

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

# ---------------------------------------------------------------------------
# Stats / diagnostic queries (kept for import compatibility)
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

ACTIVE_SONGS_QUERY = """
SELECT * FROM songs WHERE deleted_at IS NULL;
"""

ACTIVE_RECORDINGS_QUERY = """
SELECT * FROM recordings WHERE deleted_at IS NULL;
"""
