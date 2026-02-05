# sow-admin CLI Design

## Problem Statement

The current system has disconnected data sources:
- **Lyrics catalog** uses pinyin-based IDs (`wo_men_huan_qing_sheng_dan_1`)
- **Audio files** use manual English names (`give_thanks.mp3`)
- **No link** between scraped lyrics and audio recordings

## Solution: Hash-Based Universal Identifier System

All audio assets use **SHA-256 hash of audio file bytes** as the universal identifier:
- Hash computed after YouTube download, before R2 upload
- All derived assets (stems, LRC) stored under same hash prefix on R2

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│                              User's Laptop                               │
│  ┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐   │
│  │   sow-admin     │────▶│   Local SQLite  │────▶│  Turso Cloud    │   │
│  │   CLI (Typer)   │     │   (sow.db)      │     │  (explicit sync)│   │
│  └────────┬────────┘     └─────────────────┘     └─────────────────┘   │
│           │                                                              │
│           │ 1. Download from YouTube (yt-dlp)                           │
│           │ 2. Upload audio to R2                                        │
│           │ 3. Call Analysis Service                                     │
│           │ 4. Poll for completion                                       │
│           │ 5. Store metadata + R2 paths in DB                          │
│           ▼                                                              │
└───────────┼─────────────────────────────────────────────────────────────┘
            │
            │ HTTPS (API Key auth)
            ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                        Analysis Service (Docker)                         │
│                       (runs on powerful machine)                         │
│  ┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐   │
│  │  REST API       │     │  Job Queue      │     │  Workers        │   │
│  │  (FastAPI)      │────▶│  (in-memory)    │────▶│  - allin1       │   │
│  └─────────────────┘     └─────────────────┘     │  - Demucs       │   │
│                                                   │  - Whisper      │   │
│                                                   └────────┬────────┘   │
│                                                            │            │
└────────────────────────────────────────────────────────────┼────────────┘
                                                             │
                                                             ▼
                                              ┌─────────────────────────┐
                                              │   Cloudflare R2         │
                                              │   (Primary Storage)     │
                                              │                         │
                                              │   /{hash_prefix}/       │
                                              │     ├── audio.mp3       │
                                              │     ├── stems/          │
                                              │     │   ├── vocals.wav  │
                                              │     │   ├── drums.wav   │
                                              │     │   ├── bass.wav    │
                                              │     │   └── other.wav   │
                                              │     └── lyrics.lrc      │
                                              └─────────────────────────┘
```

---

## Database Schema (libsql/Turso)

### Tables

```sql
-- Song Catalog (scraped from sop.org)
CREATE TABLE songs (
    id TEXT PRIMARY KEY,           -- song_0001, song_0002, etc.
    title TEXT NOT NULL,
    title_pinyin TEXT,
    composer TEXT,
    lyricist TEXT,
    album_name TEXT,
    album_series TEXT,
    musical_key TEXT,
    lyrics_raw TEXT,
    lyrics_lines TEXT,             -- JSON array
    sections TEXT,                 -- JSON array
    source_url TEXT NOT NULL,
    table_row_number INTEGER,
    scraped_at TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

-- Audio Recordings (hash-indexed, one-to-one with songs)
CREATE TABLE recordings (
    content_hash TEXT PRIMARY KEY, -- Full SHA-256 (64 chars)
    hash_prefix TEXT NOT NULL UNIQUE, -- First 12 chars (R2 directory ID)
    song_id TEXT REFERENCES songs(id), -- One-to-one link to song
    original_filename TEXT NOT NULL,
    file_size_bytes INTEGER NOT NULL,
    imported_at TEXT NOT NULL,

    -- R2 asset URLs
    r2_audio_url TEXT,             -- s3://bucket/{hash}/audio.mp3
    r2_stems_url TEXT,             -- s3://bucket/{hash}/stems/ (prefix)
    r2_lrc_url TEXT,               -- s3://bucket/{hash}/lyrics.lrc

    -- Analysis metadata (populated by analysis service)
    duration_seconds REAL,
    tempo_bpm REAL,
    musical_key TEXT,
    musical_mode TEXT,
    key_confidence REAL,
    loudness_db REAL,
    beats TEXT,                    -- JSON array of beat timestamps
    downbeats TEXT,                -- JSON array of downbeat timestamps
    sections TEXT,                 -- JSON array of {start, end, label}
    embeddings_shape TEXT,         -- JSON array [4, timesteps, 24]

    -- Processing status
    analysis_status TEXT DEFAULT 'pending', -- pending, processing, completed, failed
    analysis_job_id TEXT,
    lrc_status TEXT DEFAULT 'pending',      -- pending, processing, completed, failed
    lrc_job_id TEXT,

    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

-- Sync metadata
CREATE TABLE sync_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT DEFAULT (datetime('now'))
);

-- Index for efficient lookups
CREATE INDEX idx_recordings_song_id ON recordings(song_id);
CREATE INDEX idx_recordings_analysis_status ON recordings(analysis_status);
CREATE INDEX idx_songs_album ON songs(album_name);
```

---

## Storage Architecture

### Cloudflare R2 (Primary Storage)

All audio assets stored on R2, organized by content hash:

```
sow-audio-bucket/
└── {hash_prefix}/              # e.g., c6de4449928d/
    ├── audio.mp3               # Original audio (uploaded by CLI)
    ├── stems/                  # Separated stems (uploaded by service)
    │   ├── vocals.wav
    │   ├── drums.wav
    │   ├── bass.wav
    │   └── other.wav
    └── lyrics.lrc              # Timestamped lyrics (uploaded by service)
```

### Local Database

```
~/.config/sow-admin/
├── config.toml                 # CLI configuration
└── db/
    └── sow.db                  # Local libsql database
```

### What's Synced via Turso

| Data | Storage | Notes |
|------|---------|-------|
| Song catalog | `songs` table | Scraped from sop.org |
| Recording metadata | `recordings` table | Analysis results, R2 URLs |
| Processing status | `recordings` table | Job status for analysis/LRC |

### What's Local Only

| Data | Storage | Notes |
|------|---------|-------|
| CLI config | `~/.config/sow-admin/config.toml` | Service URLs, non-sensitive settings |
| API keys | Environment variables | R2 credentials, service API key |
| Audio files | R2 (downloaded on-demand) | For transition rendering |

---

## Analysis Service API

### Endpoints

#### `POST /jobs/analyze`

Submit audio for analysis (tempo, key, beats, sections, stems).

**Request:**
```json
{
  "audio_url": "s3://sow-audio/c6de4449928d/audio.mp3",
  "content_hash": "c6de4449928d...",
  "options": {
    "generate_stems": true,
    "stem_model": "htdemucs"
  }
}
```

**Response:**
```json
{
  "job_id": "job_abc123",
  "status": "queued",
  "created_at": "2024-01-15T10:30:00Z"
}
```

#### `GET /jobs/{job_id}`

Poll job status.

**Response (in progress):**
```json
{
  "job_id": "job_abc123",
  "status": "processing",
  "progress": 0.45,
  "stage": "stem_separation"
}
```

**Response (completed):**
```json
{
  "job_id": "job_abc123",
  "status": "completed",
  "result": {
    "duration_seconds": 245.3,
    "tempo_bpm": 128.5,
    "musical_key": "G",
    "musical_mode": "major",
    "key_confidence": 0.87,
    "loudness_db": -8.2,
    "beats": [0.23, 0.70, 1.17, ...],
    "downbeats": [0.23, 2.10, 4.00, ...],
    "sections": [
      {"label": "intro", "start": 0.0, "end": 15.2},
      {"label": "verse", "start": 15.2, "end": 45.8},
      ...
    ],
    "embeddings_shape": [4, 512, 24],
    "stems_url": "s3://sow-audio/c6de4449928d/stems/"
  }
}
```

#### `POST /jobs/lrc`

Generate timestamped LRC from audio + lyrics.

**Request:**
```json
{
  "audio_url": "s3://sow-audio/c6de4449928d/audio.mp3",
  "content_hash": "c6de4449928d...",
  "lyrics_text": "第一行歌詞\n第二行歌詞\n...",
  "options": {
    "whisper_model": "large-v3",
    "llm_model": "openai/gpt-4o-mini"
  }
}
```

**Response:**
```json
{
  "job_id": "job_lrc456",
  "status": "queued"
}
```

**Completed Response:**
```json
{
  "job_id": "job_lrc456",
  "status": "completed",
  "result": {
    "lrc_url": "s3://sow-audio/c6de4449928d/lyrics.lrc",
    "line_count": 42
  }
}
```

### Authentication

API key via header:
```
Authorization: Bearer <SOW_ANALYSIS_API_KEY>
```

### Caching

Service maintains local cache by content_hash to avoid re-processing:
- If `{hash}/analysis.json` exists in cache, skip allin1 analysis
- If `{hash}/stems/` exists in cache, skip Demucs separation
- Cache can be invalidated via `force: true` option

---

## CLI Commands (Typer)

### `sow-admin catalog`
```
scrape [--limit N] [--force] [--dry-run]
    Scrape song catalog from sop.org
    --force: Re-scrape all songs (default: incremental)

list [--album TEXT] [--key TEXT] [--composer TEXT] [--has-recording] [--format table|ids]
    List songs from catalog
    --format ids: Output one song ID per line (for piping)

search QUERY [--field title|lyrics|composer|all] [--limit N]
    Search songs in catalog

show SONG_ID
    Show detailed info for a song

sync [--dry-run]
    Sync local database with Turso cloud (explicit sync only)
```

### `sow-admin audio`
```
download SONG_ID [--dry-run]
    Download audio from YouTube using song metadata (title + album + artist)
    Uploads to R2 after download

list [--status pending|completed|failed] [--format table|ids]
    List recordings
    --format ids: Output one hash prefix per line

show HASH_PREFIX
    Show detailed info for a recording

analyze HASH_PREFIX [--force] [--no-stems]
    Submit recording for analysis (calls analysis service)
    --force: Re-analyze even if already completed

lrc HASH_PREFIX [--force]
    Generate LRC for recording (calls LRC service)
    Requires song to be linked (uses scraped lyrics)

status [JOB_ID]
    Check status of analysis/LRC jobs
```

### `sow-admin db`
```
init [--force]
    Initialize local database

status
    Show database stats and sync status

sync [--dry-run]
    Sync with Turso cloud

reset [--confirm]
    Reset local database (destructive)
```

### `sow-admin config`
```
show
    Show current configuration

set KEY VALUE
    Set configuration value

path
    Show config file path
```

---

## Module Structure

```
src/stream_of_worship/admin/
├── __init__.py
├── main.py                    # Typer app entry point
├── commands/
│   ├── __init__.py
│   ├── catalog.py             # Catalog commands (scrape, list, search, show, sync)
│   ├── audio.py               # Audio commands (download, list, show, analyze, lrc)
│   ├── db.py                  # Database commands (init, status, reset)
│   └── config.py              # Config commands (show, set, path)
├── db/
│   ├── __init__.py
│   ├── client.py              # DatabaseClient (local libsql + Turso sync)
│   ├── models.py              # Song, Recording dataclasses
│   └── schema.py              # SQL schema definitions
├── services/
│   ├── __init__.py
│   ├── scraper.py             # Catalog scraper (from sop.org)
│   ├── hasher.py              # SHA-256 hashing
│   ├── youtube.py             # YouTube download (yt-dlp wrapper)
│   ├── r2.py                  # Cloudflare R2 client
│   ├── analysis.py            # Analysis service client
│   └── sync.py                # Turso Cloud sync
└── config.py                  # Configuration management
```

### Analysis Service (separate package)

```
services/analysis/
├── Dockerfile
├── pyproject.toml
├── src/
│   └── sow_analysis/
│       ├── __init__.py
│       ├── main.py            # FastAPI app
│       ├── routes/
│       │   ├── __init__.py
│       │   ├── jobs.py        # Job submission endpoints
│       │   └── health.py      # Health check
│       ├── workers/
│       │   ├── __init__.py
│       │   ├── analyzer.py    # allin1 + librosa analysis
│       │   ├── separator.py   # Demucs stem separation
│       │   └── lrc.py         # Whisper + LLM alignment
│       ├── storage/
│       │   ├── __init__.py
│       │   ├── r2.py          # R2 upload/download
│       │   └── cache.py       # Local result cache
│       └── models.py          # Pydantic models
└── docker-compose.yml
```

---

## Dependencies

### CLI (`sow-admin`)

```toml
[project.optional-dependencies]
admin = [
    "typer[all]>=0.9.0",
    "libsql-client>=0.3.0",
    "rich>=13.0.0",
    "beautifulsoup4>=4.14.0",
    "lxml>=6.0.0",
    "requests>=2.32.0",
    "pypinyin>=0.52.0",
    "yt-dlp>=2024.0.0",
    "boto3>=1.34.0",           # For R2 (S3-compatible)
]

[project.scripts]
sow-admin = "stream_of_worship.admin.main:app"
```

### Analysis Service

```toml
[project.dependencies]
fastapi = ">=0.109.0"
uvicorn = ">=0.27.0"
allin1 = ">=1.1.0"
demucs = ">=4.0.0"
librosa = ">=0.10.0"
openai-whisper = ">=20231117"
boto3 = ">=1.34.0"
httpx = ">=0.26.0"
```

---

## Configuration

### CLI Config File (`~/.config/sow-admin/config.toml`)

```toml
[service]
analysis_url = "https://analysis.example.com"

[r2]
bucket = "sow-audio"
endpoint_url = "https://xxx.r2.cloudflarestorage.com"
region = "auto"

[turso]
database_url = "libsql://your-db.turso.io"
```

### Environment Variables (secrets)

```bash
# Analysis service
SOW_ANALYSIS_API_KEY=your-api-key

# Cloudflare R2
SOW_R2_ACCESS_KEY_ID=your-access-key
SOW_R2_SECRET_ACCESS_KEY=your-secret-key

# Turso (for sync)
SOW_TURSO_AUTH_TOKEN=your-turso-token

# LRC generation (used by service)
OPENROUTER_API_KEY=your-openrouter-key
```

---

## Implementation Order

### Phase 1: Foundation
1. Module structure, config management
2. `db/schema.py`, `db/models.py`
3. `db/client.py` (local SQLite only, no Turso yet)
4. `commands/db.py` (init, status)

### Phase 2: Catalog
1. `services/scraper.py` (refactor from `poc/lyrics_scraper.py`)
2. `commands/catalog.py` (scrape, list, show, search)

### Phase 3: Audio Download
1. `services/hasher.py` (SHA-256)
2. `services/youtube.py` (yt-dlp wrapper)
3. `services/r2.py` (R2 upload/download)
4. `commands/audio.py` (download, list, show)

### Phase 4: Analysis Service
1. FastAPI app structure
2. Job queue (in-memory for v1)
3. `workers/analyzer.py` (allin1 integration)
4. `workers/separator.py` (Demucs integration)
5. R2 upload for stems
6. Docker packaging

### Phase 5: CLI-Service Integration
1. `services/analysis.py` (service client)
2. `commands/audio.py` (analyze command)
3. Job polling and status updates

### Phase 6: LRC Generation
1. `workers/lrc.py` (Whisper + LLM alignment)
2. `commands/audio.py` (lrc command)

### Phase 7: Turso Sync
1. `services/sync.py`
2. `commands/catalog.py` (sync command)
3. `commands/db.py` (sync command)

---

## CLI Output Format

### Default (Interactive)

Rich tables for human readability:

```
$ sow-admin catalog list --limit 3

┏━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━┳━━━━━━━━━━━┓
┃ ID          ┃ Title         ┃ Album     ┃ Key   ┃ Recording ┃
┡━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━╇━━━━━━━━━━━┩
│ song_0001   │ 將天敞開      │ 敬拜讚美15 │ G     │ ✓         │
│ song_0002   │ 我們歡慶聖誕  │ 聖誕特輯   │ D     │ -         │
│ song_0003   │ 感謝          │ 敬拜讚美12 │ C     │ ✓         │
└─────────────┴───────────────┴───────────┴───────┴───────────┘
```

### Pipeable (`--format ids`)

One ID per line for shell pipelines:

```
$ sow-admin catalog list --has-recording --format ids
song_0001
song_0003
song_0007

$ sow-admin catalog list --album "敬拜讚美15" --format ids | xargs -I{} sow-admin audio download {}
```

---

## Workflows

### 1. Initial Setup

```bash
# Initialize database
sow-admin db init

# Scrape song catalog
sow-admin catalog scrape

# View catalog
sow-admin catalog list --limit 20
```

### 2. Download and Analyze a Song

```bash
# Find a song
sow-admin catalog search "將天敞開"
# Output: song_0001

# Download from YouTube
sow-admin audio download song_0001
# Downloads, hashes, uploads to R2, creates recording entry

# Submit for analysis
sow-admin audio analyze c6de4449928d
# Submits job to analysis service

# Check status
sow-admin audio status job_abc123

# Generate LRC
sow-admin audio lrc c6de4449928d
```

### 3. Batch Processing

```bash
# Download all songs from an album
sow-admin catalog list --album "敬拜讚美15" --format ids | \
  xargs -I{} sow-admin audio download {}

# Analyze all pending recordings
sow-admin audio list --status pending --format ids | \
  xargs -I{} sow-admin audio analyze {}
```

### 4. Sync Across Devices

```bash
# On device A: after making changes
sow-admin db sync

# On device B: pull latest
sow-admin db sync
```

---

## Verification

After implementation:

```bash
# 1. Initialize and scrape
sow-admin db init
sow-admin catalog scrape --limit 10
sow-admin catalog list

# 2. Download a song
sow-admin catalog search "感謝"
sow-admin audio download song_0003

# 3. Check recording
sow-admin audio list
sow-admin audio show c6de4449928d

# 4. Submit analysis (requires service running)
sow-admin audio analyze c6de4449928d
sow-admin audio status

# 5. Generate LRC
sow-admin audio lrc c6de4449928d

# 6. Verify R2 assets
# Check R2 bucket for: c6de4449928d/audio.mp3, stems/, lyrics.lrc
```
