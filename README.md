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

This project consists of three components. Here's how to run each:

| Component | Purpose | Run Command |
|-----------|---------|-------------|
| **Admin CLI** | Catalog management, audio download | `uv run --extra admin sow-admin --help` |
| **User App** | Interactive TUI for transitions | `uv run --extra app sow-app run` |
| **Analysis Service** | Audio analysis & stem separation | `cd services/analysis && docker compose up -d` |

### Prerequisites
- **Admin CLI & User App**: Python 3.11+, `uv` package manager
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

---

## Admin CLI (`sow-admin`)

The Admin CLI is the backend management tool for administrators and DevOps. It manages the song catalog, downloads audio, and coordinates with the Analysis Service.

### Features

**Database Management**
- Initialize SQLite database with Turso sync support
- Database status and health checks
- Reset and migration tools

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

Create `~/.config/sow-admin/config.toml`:

```toml
[database]
path = "/Users/you/.local/share/sow-admin/sow.db"

[r2]
bucket = "your-r2-bucket"
endpoint_url = "https://your-account.r2.cloudflarestorage.com"
region = "auto"

[service]
analysis_url = "http://localhost:8000"

[turso]
database_url = "https://your-db.turso.io"
```

Environment variables (take precedence over config):
```bash
export SOW_R2_BUCKET="your-bucket"
export SOW_R2_ENDPOINT_URL="https://xxx.r2.cloudflarestorage.com"
export SOW_R2_ACCESS_KEY_ID="your-access-key"
export SOW_R2_SECRET_ACCESS_KEY="your-secret-key"
```

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
- Browse master song catalog (synced from Turso cloud database)
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

Create `~/.config/sow/config.toml`:

```toml
[database]
db_path = "/Users/you/.config/sow/db/sow.db"
songsets_db_path = "/Users/you/.config/sow/db/songsets.db"

[turso]
database_url = "libsql://your-db.turso.io"
readonly_token = "your-token"
sync_on_startup = true

[app]
cache_dir = "/Users/you/.cache/sow"
output_dir = "/Users/you/sow/output"
preview_volume = 0.8
default_gap_beats = 2.0
default_video_template = "dark"
default_video_resolution = "1080p"
```

### Commands

```bash
# Run the interactive TUI
sow-app run

# Run with custom config
sow-app run --config /path/to/config.toml

# Database operations
sow-app db sync                    # Sync with Turso cloud database
sow-app db status                  # Check database sync status

# Songset operations
sow-app songset list              # List all songsets
sow-app songset create "Worship Set"  # Create new songset
sow-app songset export <id>       # Export songset to JSON
sow-app songset import <file>     # Import songset from JSON

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

# 3. Sync database (if needed)
uv run --extra app sow-app db sync

# 4. Export songset for sharing
uv run --extra app sow-app songset export <songset-id>
```

---

## POC Transition Builder (Standalone)

The Transition Builder is a **mature, standalone tool** for creating transition audio files between two songs. Unlike `sow-app`, it works directly with analysis JSON files and does not require a SQLite/Turso catalog.

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

**Last Updated:** 2025-12-30
