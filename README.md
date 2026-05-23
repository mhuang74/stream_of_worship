# Stream of Worship

A seamless Chinese worship music transition system designed to analyze songs (tempo, key, structure) and generate smooth transitions between them.

**End Goals:**
- Generate audio files containing multiple songs with smooth transitions between songs
- Generate video files containing lyrics videos of multiple songs with smooth transitions
- Provide an interactive tool to select songs from the library, experiment with transition parameters, and generate output audio/video files
- Provide an admin tool to manage the song library (via scraping sop.org) and perform song analysis and lyrics LRC generation

**Note:** This repository contains both the lightweight CLI tool (`sow-admin`) and the heavy Analysis Service. They are architecturally separate but co-located in a monorepo.

---

## Quick Start

This project consists of six components. Here's how to run each:

| Component | Purpose | Run Command |
|-----------|---------|-------------|
| **Admin CLI** | Catalog management, audio download | `uv run --extra admin sow-admin --help` |
| **User App** | Interactive TUI for transitions | `uv run --extra app sow-app run` |
| **Web App** | Browser-based worship set editor | `pnpm --filter sow-webapp dev` |
| **Analysis Service** | Audio analysis & stem separation | `cd services/analysis && docker compose up -d` |
| **Render Worker** | Serverless render processing | `cd services/render-worker && docker compose up --build` |

### Prerequisites
- **Admin CLI & User App**: Python 3.11+, `uv` package manager
- **Web App**: Node.js 18+, `pnpm` package manager
- **Web App Database**: PostgreSQL with the `pgvector` extension available
- **Analysis Service**: Docker Desktop, Cloudflare R2 credentials

### Component Details

#### Admin CLI (Backend Management)
```bash
# Initialize database
uv run --extra admin sow-admin db init

# Scrape song catalog
uv run --extra admin sow-admin catalog scrape

# Download audio
uv run --extra admin sow-admin audio download --song-id "song-id"
```

#### User App (End-User TUI)
```bash
# Run the interactive TUI
uv run --extra app sow-app run

# Or with custom config
uv run --extra app sow-app run --config /path/to/config.toml
```

#### Analysis Service (Microservice)
```bash
cd services/analysis

# Copy and configure environment
cp .env.example .env
# Edit .env with R2 credentials

# Build and start
docker compose build  # First time only (10-20 min)
docker compose up -d

# Check health
curl http://localhost:8000/api/v1/health
```

#### Web App (Browser-Based Editor)
```bash
# Install dependencies (from project root)
pnpm install

# Start dev server
pnpm --filter sow-webapp dev

# Or from the webapp directory
cd webapp && pnpm dev
```

---

## Web App (`sow-webapp`)

The Web App is the primary end-user interface for worship leaders and media teams. It is a Next.js browser application offering phone-first worship set preparation and playback, with desktop power-mode for advanced editing.

### Features

**Songset Management**
- Browse and search the master song catalog (title, artist, key, tempo)
- Create multi-song worship sets with per-song transition configuration
- Adjust gap duration and crossfade for each song pair
- Desktop: key shift and tempo nudge per song

**Render Pipeline**
- Generate MP3 audio: blended multi-song mix with smooth transitions (processed asynchronously via AWS Lambda)
- Generate MP4 video: synchronized lyrics video with chapter markers (processed asynchronously via AWS Lambda)
- Real-time progress via SSE (Server-Sent Events)
- Configurable video resolution (720p, 1080p, 1440p) and font size

**Playback**
- Built-in controller player (audio + synchronized lyrics)
- Second-screen projection via W3C Presentation API or Google Cast
- Offline caching of rendered files via Service Worker
- Full keyboard shortcuts and Media Session API integration

**Content Review**
- LRC lyrics review and editing (fix timing, correct characters)
- Transition detail sheet (waveform alignment preview)
- Semantic song search: full-text tsvector across Chinese text and pinyin, plus similar-song discovery via pre-computed embeddings

**Sharing & Settings**
- Generate shareable public player links with configurable expiry
- User settings (display name, default transition parameters)

### Prerequisites

- Node.js 18+
- pnpm

External services (required for full functionality):
- Neon PostgreSQL database
- pgvector enabled in that PostgreSQL database
- Cloudflare R2 object storage (for rendered audio/video files)
- AWS SQS queue + Lambda (for render job processing)

### Setup

```bash
# Install dependencies
cd webapp && pnpm install

# Configure environment
cp .env.example .env.local
# Edit .env.local with your credentials (see .env.production.example for full docs)

# Push database schema
psql "$DATABASE_URL" -c 'CREATE EXTENSION IF NOT EXISTS vector;'
npx drizzle-kit push

# Start dev server
pnpm dev   # → http://localhost:8080
```

See [webapp/.env.production.example](webapp/.env.production.example) for documentation of all environment variables.

### Development Commands

```bash
pnpm dev          # Dev server on :8080
pnpm test         # Run test suite (Vitest)
pnpm lint         # ESLint check
pnpm build        # Production build

# Database migrations
npx drizzle-kit push       # Push schema to DB (dev)
npx drizzle-kit generate   # Generate migration files
npx drizzle-kit migrate    # Run pending migrations
```

### Routes

| Path | Description |
|------|-------------|
| `/login` | User login |
| `/register` | User registration |
| `/songsets` | Songset list |
| `/songsets/[id]` | Songset editor |
| `/songsets/[id]/render` | Render configuration and progress |
| `/songsets/[id]/play` | Playback controller |
| `/songsets/[id]/play/controller` | Controller player (Presentation API) |
| `/songsets/[id]/play/projection` | Second-screen lyrics projection |
| `/share/[token]` | Public shared player |
| `/share/[token]/play/audio` | Shared audio playback |
| `/share/[token]/play/projection` | Shared projection playback |
| `/settings` | User settings |

### API Summary

- `GET /api/songs`, `GET /api/songs/[id]`, `GET /api/songs/search`, `GET /api/songs/albums`, `POST /api/songs/search/semantic`: authenticated catalog APIs. Semantic search requires a `recordingId` and uses pre-computed embeddings from the database. App users only see songs with at least one published recording.
- `GET|POST|PATCH|DELETE /api/songsets...`: authenticated songset CRUD, item editing, and reorder operations scoped to the owner.
- `POST /api/render-jobs`, `GET /api/render-jobs/[id]`, `DELETE /api/render-jobs/[id]`, `GET /api/render-jobs/[id]/events`, `GET /api/render-jobs/[id]/artifact-sizes`: authenticated render creation, polling, cancellation, SSE progress, and artifact size queries.
- `GET|POST /api/signed-url`: authenticated signed URL minting for published recordings by `hashPrefix` or the caller's own render job artifacts by `renderJobId`.
- `POST /api/transitions/preview`: authenticated transition audio preview signed URL generation.
- `GET|DELETE /api/offline/cache`: authenticated offline artifact URL generation and invalidation.
- `GET|PUT /api/settings`: authenticated per-user settings.
- `GET|POST|DELETE /api/lyrics/marks`, `GET|PUT|DELETE /api/lyrics/overrides`: authenticated lyric review and override storage.
- `POST /api/share`, `GET /api/share`, `DELETE /api/share/[token]`: authenticated share management with a 20-active-share cap per user.
- `GET /api/share/[token]`: public share-token lookup used by shared playback pages.

### Deployment

The web app deploys to **Vercel**. Render jobs are enqueued to AWS SQS and processed by a Lambda worker (Docker container deployed to private ECR), so the Vercel function only needs a short timeout for job creation and SSE progress streaming.

See [webapp/README.md](webapp/README.md) for full deployment instructions including:
- Vercel project setup
- Environment variable configuration
- Google Cast SDK receiver registration (dev / staging / production)
- Cast production approval process

---

## Admin CLI (`sow-admin`)

The Admin CLI is the backend management tool for administrators and DevOps. It manages the song catalog, downloads audio, and coordinates with the Analysis Service.

### Features

**Database Management**
- Initialize PostgreSQL database with schema creation
- Database status and health checks

**Catalog Management**
- Scrape song catalog from sop.org (Stream of Praise)
- Search songs by title, artist, album, or lyrics
- List albums and songs with filtering
- Incremental updates (only new songs)

**Audio Management**
- Download audio from YouTube via yt-dlp
- Content-hash based deduplication (SHA-256)
- Upload to Cloudflare R2 storage
- List recordings with status filtering

**Analysis Coordination**
- Submit audio analysis jobs to Analysis Service
- Check analysis job status
- Monitor stem separation progress

**Songset Management**
- Create and manage songsets
- Export songsets to JSON
- Backup and restore songset databases

### Common Commands

```bash
# Database operations
sow-admin db init                                    # Initialize database
sow-admin db status                                  # Check database status

# Catalog operations
sow-admin catalog scrape                             # Scrape all songs from sop.org
sow-admin catalog scrape --incremental              # Only new songs
sow-admin catalog search "主祢是愛"                   # Search by title/lyrics
sow-admin catalog list --album "敬拜讚美15"          # List songs in album
sow-admin catalog show <song-id>                     # Show song details

# Audio operations
sow-admin audio download --song-id "zhu-ni-shi-ai-1"  # Download specific song
sow-admin audio list                                # List all recordings
sow-admin audio list --status pending               # List pending analyses
sow-admin audio analyze --recording-id <id>        # Submit for analysis
sow-admin audio status                              # Check analysis status

# Songset operations
sow-admin songset create --name "Sunday Worship"     # Create new songset
sow-admin songset list                              # List all songsets
sow-admin songset export --songset-id <id>         # Export to JSON
```

### Configuration

Create `~/.config/stream-of-worship-admin/config.toml`:

```toml
[service]
analysis_url = "http://localhost:8000"

[database]
url = "postgresql://sow_admin_rw@ep-xxx-pooler.neon.tech/sow"

[r2]
bucket = "stream-of-worship"
endpoint_url = "https://<account-id>.r2.cloudflarestorage.com"
region = "auto"
```

**Note:** Cache directory is always at `~/.cache/stream-of-worship-admin/` and is not configurable.

**Required Environment Variables** (for sensitive credentials):
```bash
# PostgreSQL password (Admin CLI - never store in config)
export SOW_DATABASE_PASSWORD="your-database-password"

# R2 credentials
export SOW_R2_ACCESS_KEY_ID="your-access-key"
export SOW_R2_SECRET_ACCESS_KEY="your-secret-key"

# Analysis service API key
export SOW_ANALYSIS_API_KEY="your-api-key"
```

**Note:** Non-sensitive settings like `database.url`, `r2.bucket`, and `r2.endpoint_url` should be configured in the config file. Only sensitive credentials use environment variables for security.

### Workflow Example

```bash
# 1. Initialize the database
uv run --extra admin sow-admin db init

# 2. Scrape the song catalog from sop.org
uv run --extra admin sow-admin catalog scrape

# 3. Search for a specific song
uv run --extra admin sow-admin catalog search "主祢是愛"

# 4. Download audio for a song
uv run --extra admin sow-admin audio download --song-id "zhu-ni-shi-ai-1"

# 5. Submit for analysis (requires Analysis Service running)
uv run --extra admin sow-admin audio analyze --recording-id "abc123def456"

# 6. Check analysis status
uv run --extra admin sow-admin audio status
```

---

## User App (`sow-app`)

The User App is the primary tool for worship leaders and media teams to create multi-song sets with synchronized lyrics videos.

### Features

**Song Selection**
- Browse master song catalog (from PostgreSQL database)
- Search by title, artist, album, key, tempo
- View song metadata and analysis results
- Preview audio stems (vocals, drums, bass, other)

**Songset Creation**
- Create multi-song sets with smooth transitions
- Adjust transition parameters:
  - Crossfade duration
  - Tempo matching (BPM alignment)
  - Key shifting (musical key alignment)
  - Gap duration between songs
- Real-time preview

**Lyrics Video Generation**
- Load LRC files from cloud storage
- Select video templates (dark, gradient_warm, gradient_blue)
- Configure resolution (720p, 1080p, 1440p, 4K)
- Export MP4 video with synchronized lyrics

### Configuration

Create `~/.config/stream-of-worship/config.toml`:

```toml
[database]
url = "postgresql://sow_app@ep-xxx-pooler.neon.tech/sow"

[r2]
bucket = "stream-of-worship"
endpoint_url = "https://<account-id>.r2.cloudflarestorage.com"
region = "auto"

[app]
working_dir = "~/stream-of-worship"
preview_volume = 0.8
default_gap_beats = 2.0
default_video_template = "dark"
default_video_resolution = "1080p"
```

**Derived paths (User App only):**
- Logs: `<working_dir>/logs/`
- Output: `<working_dir>/output/`
- Backup: `<working_dir>/backup/`

**Cache locations (not configurable):**
- Admin CLI: `~/.cache/stream-of-worship-admin/`
- User App: `~/.cache/stream-of-worship/`

**Required Environment Variables** (for sensitive credentials):
```bash
# PostgreSQL password (required - env var only for security)
export SOW_DATABASE_PASSWORD="your-database-password"

# R2 credentials (required for downloading audio assets)
export SOW_R2_ACCESS_KEY_ID="your-access-key"
export SOW_R2_SECRET_ACCESS_KEY="your-secret-key"
```

**Note:** The PostgreSQL password is read from the `SOW_DATABASE_PASSWORD` environment variable only and is never stored in the config file for security.

### Commands

```bash
# Run the interactive TUI
sow-app run

# Run with custom config
sow-app run --config /path/to/config.toml

# Database operations
sow-app db check                   # Check database connectivity

# Songset operations
sow-app songset list              # List all songsets
sow-app songset create "Worship Set"  # Create new songset
sow-app songsets backup <id>      # Backup songset to JSON
sow-app songsets restore <file>   # Restore songset from JSON

# Configuration
sow-app config show               # Display current configuration
```

### Common Usage Workflow

```bash
# 1. Run the TUI
uv run --extra app sow-app run

# 2. In the TUI:
#    - Browse catalog (press 'b')
#    - Search for songs
#    - Select songs to add to songset
#    - Adjust transition parameters
#    - Preview transitions
#    - Export audio or video

# 3. Backup songset for sharing
uv run --extra app sow-app songsets backup <songset-id>
```

---

## POC Transition Builder (Standalone)

The Transition Builder is a **mature, standalone tool** for creating transition audio files between two songs. Unlike `sow-app`, it works directly with analysis JSON files and does not require a database.

**Best for:** Quick experimentation, 2-song transitions without database setup.

**Prerequisites:** Requires POC analysis output. See [DEVELOPER.md](DEVELOPER.md) for POC analysis setup instructions.

### Configuration

Create `~/.local/share/sow/config.json` (Linux) or `~/Library/Application Support/sow/config.json` (macOS):

```json
{
  "audio_folder": "~/.local/share/sow/song_library",
  "output_folder": "~/.local/share/sow/output/transitions",
  "output_songs_folder": "~/.local/share/sow/output/songs",
  "analysis_json": "poc/output_allinone/poc_full_results.json",
  "stems_folder": "poc/output_allinone/stems",
  "audio_format": "ogg",
  "audio_bitrate": "192k",
  "video_resolution": "1080p"
}
```

### Launch

```bash
# Install with TUI dependencies
uv sync --extra tui

# Launch Transition Builder
uv run stream-of-worship tui

# Or with custom config path
uv run stream-of-worship tui --config /path/to/config.json
```

### Using the TUI

1. **Select Outgoing Song** - Choose the first song (where transition starts)
2. **Select Incoming Song** - Choose the second song (where transition ends)
3. **Adjust Parameters:**
   - Crossfade duration
   - Tempo matching (BPM alignment)
   - Key shifting (musical key alignment)
   - Transition point selection
4. **Preview** - Listen to the transition
5. **Generate** - Export the transition audio file

---

## Troubleshooting

### Docker Issues

**Problem:** "Cannot connect to Docker daemon"

```bash
# Solution: Start Docker Desktop application
# Wait for it to fully start (whale icon in system tray)
```

**Problem:** "Port 8888 already in use"

```bash
# Solution: Stop existing Jupyter instance or change port
docker compose -f docker/docker-compose.yml down
# Edit docker/docker-compose.yml: Change "8888:8888" to "8889:8888"
docker compose -f docker/docker-compose.yml up
```

### Audio Issues

**Problem:** "librosa.load() fails with codec error"

```bash
# Solution: Convert audio to supported format
ffmpeg -i input.m4a output.mp3
# Or use FLAC: ffmpeg -i input.m4a output.flac
```

**Problem:** "No audio files found"

```bash
# Solution: Check file location
ls poc_audio/
# Should show *.mp3 or *.flac files
# If empty, copy files: cp /path/to/songs/*.mp3 poc_audio/
```

---

## Resources

- **Developer Documentation:** [DEVELOPER.md](DEVELOPER.md) - Architecture, roadmap, advanced configuration
- **POC Script Guide:** [poc/README.md](poc/README.md)
- **Design Document:** [specs/worship-music-transition-system-design.md](specs/worship-music-transition-system-design.md)
- **librosa Documentation:** https://librosa.org/doc/latest/
- **Stream of Praise:** https://www.sop.org/

---

## Contributing

Feedback welcome on:
- Analysis accuracy
- Additional test songs
- Edge cases or failure modes

See [DEVELOPER.md](DEVELOPER.md) for development setup and contribution guidelines.

---

## License

MIT License - See [LICENSE](LICENSE) file

---

**Last Updated:** 2026-05-17
