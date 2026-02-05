# sow-admin CLI

Administrative command-line tool for managing Stream of Worship song catalogs, audio recordings, and metadata.

## Overview

`sow-admin` provides a unified interface for:

- Managing the song catalog (scraped from sop.org)
- Tracking audio recordings with hash-based identifiers
- Processing audio through the analysis service
- Generating synchronized lyrics (LRC files)
- Syncing data across devices via Turso

## Installation

The admin CLI is included in the stream-of-worship package. Install with admin dependencies:

```bash
# Using uv (recommended)
uv add --optional admin

# Or install all extras
uv add --optional all
```

## Running the CLI

After installation, the CLI is available as `sow-admin`:

```bash
# Show version
sow-admin --version

# Show help
sow-admin --help

# Run with uv (without installing)
uv run --extra admin sow-admin --version
```

### Development Mode

Run directly from source without installation:

```bash
# Using PYTHONPATH
PYTHONPATH=src uv run --extra admin python -m stream_of_worship.admin.main --version

# Or navigate to the admin directory
cd src/stream_of_worship/admin
python -m main --help
```

## Configuration

Configuration is stored in a TOML file at:

- **macOS/Linux**: `~/.config/sow-admin/config.toml`
- **Windows**: `%APPDATA%\sow-admin\config.toml`

### View Configuration

```bash
sow-admin config show
```

### Set Configuration Values

```bash
# Set analysis service URL
sow-admin config set analysis_url https://analysis.example.com

# Set R2 bucket
sow-admin config set r2_bucket my-audio-bucket

# Set R2 endpoint
sow-admin config set r2_endpoint_url https://xxx.r2.cloudflarestorage.com
```

### Configuration File Location

```bash
sow-admin config path
```

### Example Config File

```toml
[service]
analysis_url = "http://localhost:8000"

[r2]
bucket = "sow-audio"
endpoint_url = "https://xxx.r2.cloudflarestorage.com"
region = "auto"

[turso]
database_url = "libsql://your-db.turso.io"

[database]
path = "/custom/path/sow.db"
```

### Environment Variables

Sensitive values should be set via environment variables:

```bash
export SOW_ANALYSIS_API_KEY="your-api-key"
export SOW_R2_ACCESS_KEY_ID="your-access-key"
export SOW_R2_SECRET_ACCESS_KEY="your-secret-key"
export SOW_TURSO_AUTH_TOKEN="your-turso-token"
```

## Database Commands

The local database uses SQLite for storing song catalog and recording metadata.

### Initialize Database

Create a new database with the schema:

```bash
sow-admin db init
```

Force re-initialization (destructive - deletes all data):

```bash
sow-admin db init --force
```

### Check Database Status

```bash
sow-admin db status
```

Output example:
```
                    Database Information
┏━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Property      ┃ Value                          ┃
┡━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ Database Path │ ~/.config/sow-admin/db/sow.db │
│ Exists        │ Yes                            │
│ File Size     │ 53,248 bytes (0.05 MB)         │
└───────────────┴────────────────────────────────┘

     Database Statistics
┏━━━━━━━━━━━━━━━━━┳━━━━━━━━━┓
┃ Metric          ┃ Value   ┃
┡━━━━━━━━━━━━━━━━━╇━━━━━━━━━┩
│ Songs           │ 150     │
│ Recordings      │ 45      │
│ Integrity Check │ OK      │
│ Foreign Keys    │ Enabled │
│ Last Sync       │ Never   │
└─────────────────┴─────────┘
```

### Show Database Path

```bash
sow-admin db path
```

### Reset Database (DESTRUCTIVE)

```bash
sow-admin db reset --confirm
```

## Database Schema

### Songs Table

Stores the scraped song catalog from sop.org:

| Column | Description |
|--------|-------------|
| `id` | Unique song ID (e.g., "song_0001") |
| `title` | Song title in Chinese |
| `title_pinyin` | Pinyin representation |
| `composer` | Composer name |
| `lyricist` | Lyricist name |
| `album_name` | Album name |
| `album_series` | Album series |
| `musical_key` | Musical key (G, D, etc.) |
| `lyrics_raw` | Raw lyrics text |
| `lyrics_lines` | JSON array of lyric lines |
| `sections` | JSON array of sections |
| `source_url` | Source URL |
| `scraped_at` | When scraped |

### Recordings Table

Stores audio recordings with hash-based identifiers:

| Column | Description |
|--------|-------------|
| `content_hash` | Full SHA-256 hash (64 chars) |
| `hash_prefix` | First 12 chars (R2 directory) |
| `song_id` | Linked song ID |
| `original_filename` | Original filename |
| `file_size_bytes` | File size |
| `imported_at` | Import timestamp |
| `r2_audio_url` | R2 URL for audio |
| `r2_stems_url` | R2 URL for stems |
| `r2_lrc_url` | R2 URL for LRC |
| `duration_seconds` | Audio duration |
| `tempo_bpm` | Detected tempo |
| `musical_key` | Detected key |
| `musical_mode` | major/minor |
| `analysis_status` | pending/processing/completed/failed |
| `lrc_status` | pending/processing/completed/failed |

## Development

### Running Tests

```bash
# Run all admin tests
PYTHONPATH=src uv run --extra admin pytest tests/admin/ -v

# Run specific test file
PYTHONPATH=src uv run --extra admin pytest tests/admin/test_client.py -v

# Run with coverage
PYTHONPATH=src uv run --extra admin pytest tests/admin/ --cov=stream_of_worship.admin
```

### Project Structure

```
src/stream_of_worship/admin/
├── __init__.py          # Module initialization
├── main.py              # CLI entry point (Typer)
├── config.py            # Configuration management
├── commands/
│   ├── __init__.py
│   └── db.py            # Database commands
├── db/
│   ├── __init__.py
│   ├── client.py        # DatabaseClient
│   ├── models.py        # Song, Recording models
│   └── schema.py        # SQL schema definitions
└── services/
    └── __init__.py      # Service clients (future)
```

## Troubleshooting

### Module Not Found Error

If you get `ModuleNotFoundError: No module named 'stream_of_worship'`:

```bash
# Set PYTHONPATH
export PYTHONPATH=/path/to/project/src:$PYTHONPATH

# Or run with uv from project root
uv run --extra admin sow-admin --help
```

### Database Locked

If the database is locked, ensure no other process is using it:

```bash
# Check for running processes
lsof ~/.config/sow-admin/db/sow.db

# Close any open database connections
```

### Config File Not Found

The config and database will be auto-created on first run of `sow-admin db init`.

## Future Commands (Phase 2+)

### Catalog Commands (Phase 2)

```bash
sow-admin catalog scrape [--limit N] [--force]
sow-admin catalog list [--album TEXT] [--key TEXT]
sow-admin catalog search QUERY [--field title|lyrics|composer]
sow-admin catalog show SONG_ID
```

### Audio Commands (Phase 3-5)

```bash
sow-admin audio download SONG_ID
sow-admin audio list [--status pending|completed|failed]
sow-admin audio show HASH_PREFIX
sow-admin audio analyze HASH_PREFIX [--force] [--no-stems]
sow-admin audio lrc HASH_PREFIX [--force]
sow-admin audio status [JOB_ID]
```

### Sync Commands (Phase 7)

```bash
sow-admin db sync [--dry-run]
sow-admin catalog sync [--dry-run]
```

## License

MIT License - See project root for details.
