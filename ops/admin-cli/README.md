# sow-admin CLI

Administrative command-line tool for managing Stream of Worship song catalogs, audio recordings, and metadata.

## Overview

`sow-admin` provides a unified interface for:

`- Managing the song catalog (scraped from sop.org)
- Tracking audio recordings with hash-based identifiers
- Processing audio through the analysis service
- Generating synchronized lyrics (LRC files)

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
uv run --project ops/admin-cli --extra admin python -m stream_of_worship.admin.main --version

# Or navigate to the admin directory
cd ops/admin-cli/src/stream_of_worship/admin
python -m main --help
```

## Configuration

Configuration is stored in a TOML file at:

- **macOS/Linux**: `~/.config/stream-of-worship-admin/config.toml`
- **Windows**: `%APPDATA%\stream-of-worship-admin\config.toml`

Cache directory is always at `~/.cache/stream-of-worship-admin/` (not configurable).

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
bucket = "stream-of-worship"
endpoint_url = "https://xxx.r2.cloudflarestorage.com"
region = "auto"

[database]
url = "postgresql://sow_admin_rw@ep-xxx-pooler.us-east-1.aws.neon.tech/sow"
```

Note: Cache directory is always at `~/.cache/stream-of-worship-admin/` and is not configurable.

Note: The database URL for Neon should include `sslmode=require` in the query string for production use. The application uses `sslmode=prefer` by default, which attempts SSL first but allows fallback for testing environments.

### Environment Variables

Sensitive credentials should be set via environment variables:

```bash
# Database password (required for Neon connection)
export SOW_DATABASE_PASSWORD="your-password"

# R2 Credentials (sensitive - never commit these)
export SOW_R2_ACCESS_KEY_ID="your-access-key"
export SOW_R2_SECRET_ACCESS_KEY="your-secret-key"

# Analysis service API key
export SOW_ANALYSIS_API_KEY="your-api-key"
```

**Note:** Non-sensitive settings like `database.url`, `r2.bucket`, and `r2.endpoint_url` should be configured in the config file. Only sensitive credentials use environment variables for security.

## Database Commands

The database uses PostgreSQL (hosted on Neon) for storing song catalog and recording metadata.

### Initialize Database

Create the database schema on a new PostgreSQL database:

```bash
sow-admin db init
```

Force re-initialization (re-runs schema creation on an existing database):

```bash
sow-admin db init --force
```

### Check Database Status

```bash
sow-admin db status
```

Output example:
```
             Database Connection             
┏━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Property      ┃ Value                    ┃
┡━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ Database URL  │ postgresql://sow_admin_… │
│ Password      │ Set via env var          │
│ Health        │ Connected                │
└───────────────┴──────────────────────────┘

           Database Statistics           
┏━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━┓
┃ Metric         ┃ Value                ┃
┡━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━┩
│ Songs          │ 150                  │
│ Recordings     │ 45                   │
│ Health Check   │ OK                   │
│ Schema Version │ 3                    │
└────────────────┴──────────────────────┘
```

### Show Database URL

```bash
sow-admin db url
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
PYTHONPATH=ops/admin-cli/src uv run --project ops/admin-cli --extra admin --extra test pytest ops/admin-cli/tests/admin/ -v

# Run specific test file
PYTHONPATH=ops/admin-cli/src uv run --project ops/admin-cli --extra admin --extra test pytest ops/admin-cli/tests/admin/test_client.py -v

# Run with coverage
PYTHONPATH=ops/admin-cli/src uv run --project ops/admin-cli --extra admin --extra test pytest ops/admin-cli/tests/admin/ --cov=stream_of_worship.admin
```

### Project Structure

```
ops/admin-cli/src/stream_of_worship/admin/
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

### Database Connection Issues

If you cannot connect to the database, verify your PostgreSQL connection:

```bash
# Check the configured database URL
sow-admin db status

# Verify the database is reachable
psql "$SOW_DATABASE_URL" -c "SELECT 1"
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
sow-admin audio components SONG_ID [v5 options]
sow-admin audio status [JOB_ID]
```

#### Audio Components

Extracts and displays chorus/verse component metadata for a song: component type,
occurrence, role, start/end time, BPM, key, groove, backbeat, energy, and
confidence. Submits a component analysis job to the analysis service (using
cached allin1 sections or LRC lyrics repetition with multi-cue disambiguation),
then displays the results in a Rich table.

If a cached `components.json` already exists in R2 with the current schema
version, it is returned directly (unless `--force` is specified).

```bash
# Single song (v3 path — fast, no extra deps)
sow-admin audio components song_0001

# Single song with all v5 options enabled
sow-admin audio components song_0001 \
    --classify-theme --classify-posture --snap-to-downbeat --energy-roles --use-stems

# Batch backfill via stdin
sow-admin audio list --analysis completed --format ids \
  | sow-admin audio components --stdin \
    --use-stems --snap-to-downbeat --energy-roles --classify-theme --classify-posture

# View persisted results
sow-admin audio show song_0001
```

Common flags:

| Flag | Default | Description |
|------|---------|-------------|
| `--force` / `-f` | off | Force re-extraction (bypass cache) |
| `--no-wait` | off | Submit without waiting for completion |
| `--stdin` | off | Read song IDs from stdin (one per line) for batch backfill |

##### v5 Options (opt-in, all default off)

All v5 flags are **opt-in** (default `False`). When none are specified, the
stable v3 path runs unchanged — fast and dependency-free. Enabling v5 options
adds rich per-component metadata (theme, vocal posture, per-field confidence,
energy-aware roles, downbeat snapping) at the cost of significantly more compute,
latency, and (for LLM flags) recurring API billing.

| Flag | Default | Effect |
|------|---------|--------|
| `--snap-to-downbeat` | off | Snap component boundaries to downbeats (runs madmom NN if downbeats not provided) |
| `--energy-roles` | off | Use energy-based entry/exit role assignment (drums stem signal) |
| `--use-stems` | off | Use Demucs stems for feature extraction |
| `--classify-theme` | off | LLM theme classification (12 Chinese themes) |
| `--classify-posture` | off | LLM vocal posture classification (3 categories) |

###### Why these flags are expensive

These flags were deliberately designed as opt-in to avoid changing the stable
v3 path. Enabling them turns a fast, dependency-free run into a slow, costly,
ML/LLM-heavy pipeline. Per-flag cost breakdown:

- **`--use-stems`** — Most expensive locally. Triggers Demucs stem separation
  (heavy ML model inference per song; CPU/GPU + storage). The recording must
  have stems available or they will be generated on demand. This contradicts
  the admin-CLI's lightweight design — the actual work happens in the
  analysis-service container, not in the CLI itself.

- **`--classify-theme` / `--classify-posture`** — Recurring **financial** cost.
  Makes **one LLM API call per component** (parallelized via a shared
  semaphore with `asyncio.gather`). Cost and latency scale with
  `songs × components`. Adds nondeterminism and possible retries on JSON parse
  failure. Requires `SOW_LLM_API_KEY`, `SOW_LLM_BASE_URL`, and `SOW_LLM_MODEL`
  to be configured on the analysis service (see
  [analysis-service README](../../ops/analysis-service/README.md)).

- **`--snap-to-downbeat`** — Runs madmom's two-stage RNN downbeat detector
  (`RNNDownBeatProcessor` → `DBNDownBeatTrackingProcessor`) when downbeats are
  not already cached. Adds NN inference time per job. Can fail (falls back to
  beat-only snapping if madmom errors).

- **`--energy-roles`** — Computes energy-based entry/exit role assignment using
  a composite score (`0.4×rms + 0.3×drums_onset + 0.3×backbeat`). Best used
  **together with `--use-stems`** — without the drums stem, the energy signal
  is weaker and the benefit is marginal.

###### Cost guidance

- Enable expensive flags **per-song or on targeted subsets**, not on full-library
  backfills, unless you genuinely need v5 metadata everywhere.
- `--classify-theme` / `--classify-posture` are the recurring-cost (LLM-billed)
  items — use sparingly and prefer them for songs where semantic metadata
  matters most (e.g., songset construction input).
- `--energy-roles` without `--use-stems` gives a weaker signal; consider
  combining them.
- Changing v5 option usage triggers `COMPONENT_SCHEMA_VERSION = 2` cache
  invalidation, so re-runs will re-analyze even if a stale v1 cache exists.

See `reports/chorus_component_metadata_v5_impl_summary.md` for the full
implementation details.

### Theme Anchors Commands

```bash
# Populate the theme_anchors table from the bundled JSON (required before songset construct)
sow-admin theme-anchors sync
sow-admin theme-anchors sync --force   # Re-insert even if 12 rows exist
```

### Songset Construct Command

```bash
# Prerequisite: theme_anchors table must be populated
sow-admin theme-anchors sync

# Dry run (no DB writes)
uv run --project ops/admin-cli --extra admin --extra constructor sow-admin songset construct \
  --user me@example.com --count 3 --proposals 3 --dry-run --no-cache

# Full run with auto-save
uv run --project ops/admin-cli --extra admin --extra constructor sow-admin songset construct \
  --user me@example.com --count 3 --proposals 3 --yes

# List constructed songsets
sow-admin songset list --user me@example.com
```

The `construct` command requires the `constructor` extra (`pydantic`, `langgraph`).
Without it, a clear `RuntimeError` with install instructions is raised.

#### LLM Configuration (optional)

`construct` runs beam search deterministically by default. LLM-based planning is
enabled with `--llm` (and `--llm-judge` for the LLM judge). When LLM mode is
enabled, the following environment variables are used:

| Variable | Purpose |
|----------|---------|
| `SOW_LLM_API_KEY` | API key for the OpenAI-compatible chat provider (required when `--llm`) |
| `SOW_LLM_MODEL` | Chat model name (required when `--llm`; no hardcoded default — set via env or `--llm-model`) |
| `SOW_LLM_BASE_URL` | Optional base URL for an OpenAI-compatible gateway |

The CLI auto-loads `/opt/sow/.env` (already-exported shell variables take
precedence). The chat model is built with `temperature=0.2` and `max_retries=2`.
If `--llm` is set and `SOW_LLM_API_KEY` or `SOW_LLM_MODEL` is missing, the
command fails fast with a clear configuration error.

```bash
# Example: LLM-based planning
export SOW_LLM_API_KEY="sk-..."
export SOW_LLM_MODEL="your-model"
uv run --project ops/admin-cli --extra admin --extra constructor sow-admin songset construct \
  --user me@example.com --count 3 --proposals 3 --llm --yes
```

### Sync Commands (Phase 7)

```bash
sow-admin db sync [--dry-run]
sow-admin catalog sync [--dry-run]
```

## License

MIT License - See project root for details.
