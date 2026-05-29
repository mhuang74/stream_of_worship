"""SQL schema definitions for sow-admin database (PostgreSQL).

Defines the database schema for storing song catalog and recording metadata.
Uses PostgreSQL with native timestamptz columns.
"""

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
    created_at timestamptz DEFAULT NOW(),
    updated_at timestamptz DEFAULT NOW(),
    deleted_at timestamptz
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

    created_at timestamptz DEFAULT NOW(),
    updated_at timestamptz DEFAULT NOW(),

    -- YouTube URL (for transcript-based LRC generation)
    youtube_url TEXT,

    -- Visibility status for User App (published, review, hold)
    visibility_status TEXT DEFAULT NULL,

    -- Download status (pending|processing|completed|failed)
    download_status TEXT DEFAULT 'pending',

    -- Soft delete timestamp (NULL = active)
    deleted_at timestamptz
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

# Song embedding table (pgvector for semantic search)
CREATE_SONG_EMBEDDING_TABLE = """
CREATE TABLE IF NOT EXISTS song_embedding (
    song_id       TEXT PRIMARY KEY REFERENCES songs(id) ON DELETE CASCADE,
    embedding     vector(1536) NOT NULL,
    model_version TEXT NOT NULL DEFAULT 'openai-text-embedding-3-small',
    content_hash  TEXT NOT NULL,
    created_at    TIMESTAMPTZ DEFAULT NOW()
);
"""

# Song line embedding table (pgvector for snippet matching)
CREATE_SONG_LINE_EMBEDDING_TABLE = """
CREATE TABLE IF NOT EXISTS song_line_embedding (
    id           SERIAL PRIMARY KEY,
    song_id      TEXT NOT NULL REFERENCES songs(id) ON DELETE CASCADE,
    line_index   INTEGER NOT NULL,
    line_text    TEXT NOT NULL,
    embedding    vector(1536) NOT NULL,
    model_version TEXT NOT NULL DEFAULT 'openai-text-embedding-3-small'
);
"""

# Indexes for embedding tables
CREATE_EMBEDDING_INDEXES = [
    """
    CREATE INDEX IF NOT EXISTS idx_song_embedding_cosine
    ON song_embedding USING hnsw (embedding vector_cosine_ops);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_song_line_embedding_song
    ON song_line_embedding(song_id);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_song_line_embedding_cosine
    ON song_line_embedding USING hnsw (embedding vector_cosine_ops);
    """,
]

# Postgres function to auto-update updated_at columns
CREATE_UPDATE_TIMESTAMP_FUNCTION = """
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';
"""

# Trigger to update updated_at on songs
CREATE_SONGS_UPDATE_TRIGGER = """
DROP TRIGGER IF EXISTS trg_songs_updated_at ON songs;
CREATE TRIGGER trg_songs_updated_at
    BEFORE UPDATE ON songs
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();
"""

# Trigger to update updated_at on recordings
CREATE_RECORDINGS_UPDATE_TRIGGER = """
DROP TRIGGER IF EXISTS trg_recordings_updated_at ON recordings;
CREATE TRIGGER trg_recordings_updated_at
    BEFORE UPDATE ON recordings
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();
"""

# All schema creation statements in order
ALL_SCHEMA_STATEMENTS = [
    CREATE_SONGS_TABLE,
    CREATE_RECORDINGS_TABLE,
    *CREATE_INDEXES,
    CREATE_SONG_EMBEDDING_TABLE,
    CREATE_SONG_LINE_EMBEDDING_TABLE,
    *CREATE_EMBEDDING_INDEXES,
    CREATE_UPDATE_TIMESTAMP_FUNCTION,
    CREATE_SONGS_UPDATE_TRIGGER,
    CREATE_RECORDINGS_UPDATE_TRIGGER,
]

# SQL to get table statistics (Postgres-compatible)
TABLE_STATS_QUERY = """
SELECT table_name, 1 as exists_flag
FROM information_schema.tables
WHERE table_schema = 'public'
AND table_name IN ('songs', 'recordings', 'sync_metadata');
"""

# SQL to count rows in each table (Postgres-compatible)
ROW_COUNT_QUERY = """
SELECT 'songs' as table_name, COUNT(*) as row_count FROM songs
UNION ALL
SELECT 'recordings' as table_name, COUNT(*) as row_count FROM recordings;
"""

# SQL to count active (non-deleted) rows in each table
ACTIVE_ROW_COUNT_QUERY = """
SELECT 'songs' as table_name, COUNT(*) as row_count FROM songs WHERE deleted_at IS NULL
UNION ALL
SELECT 'recordings' as table_name, COUNT(*) as row_count FROM recordings WHERE deleted_at IS NULL;
"""

# Column lists for JOIN queries (used by catalog service and other query builders)
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
    r.updated_at, r.youtube_url, r.visibility_status, r.download_status, r.deleted_at
"""

SONG_COLUMN_COUNT = 17
RECORDING_COLUMN_COUNT = 29

# SQL for listing active (non-deleted) songs
ACTIVE_SONGS_QUERY = """
SELECT * FROM songs WHERE deleted_at IS NULL
"""

# SQL for listing active (non-deleted) recordings
ACTIVE_RECORDINGS_QUERY = """
SELECT * FROM recordings WHERE deleted_at IS NULL
"""
