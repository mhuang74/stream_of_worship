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
