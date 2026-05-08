-- Stream of Worship - PostgreSQL Schema (v4 runbook)
-- Run with: psql <neon_dsn> -f 01_schema.sql

-- =============================================================================
-- Extension (if needed)
-- =============================================================================
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- =============================================================================
-- Trigger function for auto-updating updated_at
-- =============================================================================
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

-- =============================================================================
-- Songs table
-- =============================================================================
-- scraped_at is kept as TEXT because it is set by Python (datetime.now().isoformat())
-- and never updated by SQL. created_at/updated_at/deleted_at use timestamptz for
-- consistent server-side behaviour.
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

-- =============================================================================
-- Recordings table
-- =============================================================================
-- imported_at is kept as TEXT for the same reason as scraped_at.
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

-- =============================================================================
-- Songsets table (user-created playlists)
-- =============================================================================
CREATE TABLE IF NOT EXISTS songsets (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    created_at timestamptz DEFAULT NOW(),
    updated_at timestamptz DEFAULT NOW()
);

-- =============================================================================
-- Songset items (songs within a songset with transition parameters)
-- =============================================================================
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

-- =============================================================================
-- Indexes for catalog tables
-- =============================================================================
CREATE INDEX IF NOT EXISTS idx_recordings_song_id ON recordings(song_id);
CREATE INDEX IF NOT EXISTS idx_recordings_analysis_status ON recordings(analysis_status);
CREATE INDEX IF NOT EXISTS idx_recordings_hash_prefix ON recordings(hash_prefix);
CREATE INDEX IF NOT EXISTS idx_songs_album ON songs(album_name);
CREATE INDEX IF NOT EXISTS idx_songs_title_pinyin ON songs(title_pinyin);
CREATE INDEX IF NOT EXISTS idx_recordings_visibility_status ON recordings(visibility_status);
CREATE INDEX IF NOT EXISTS idx_songs_deleted_at ON songs(deleted_at);
CREATE INDEX IF NOT EXISTS idx_recordings_deleted_at ON recordings(deleted_at);

-- =============================================================================
-- Indexes for app tables
-- =============================================================================
CREATE INDEX IF NOT EXISTS idx_songset_items_songset_id ON songset_items(songset_id);
CREATE INDEX IF NOT EXISTS idx_songset_items_position ON songset_items(songset_id, position);
CREATE INDEX IF NOT EXISTS idx_songset_items_song_id ON songset_items(song_id);

-- =============================================================================
-- Triggers
-- =============================================================================
DROP TRIGGER IF EXISTS trg_songs_updated_at ON songs;
CREATE TRIGGER trg_songs_updated_at
    BEFORE UPDATE ON songs
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS trg_recordings_updated_at ON recordings;
CREATE TRIGGER trg_recordings_updated_at
    BEFORE UPDATE ON recordings
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS trg_songsets_updated_at ON songsets;
CREATE TRIGGER trg_songsets_updated_at
    BEFORE UPDATE ON songsets
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();
