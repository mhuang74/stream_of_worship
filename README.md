# Stream of Worship

A seamless Chinese worship music transition system designed to analyze songs (tempo, key, structure) and generate smooth transitions between them.

**End Goals:**
- Generate audio files containing multiple songs with smooth transitions between songs
- Generate video files containing lyrics videos of multiple songs with smooth transitions
- Provide an interactive tool to select songs from the library, experiment with transition parameters, and generate output audio/video files
- Provide an admin tool to manage the song library (via scraping sop.org) and perform song analysis and lyrics LRC generation

**Note:** This repository contains both the lightweight CLI tool (`sow-admin`) and the heavy Analysis Service. They are architecturally separate but co-located in a monorepo.

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

## Project Status: Production Development

**Current Phase:** Phase 4 - Analysis Service Implementation
**Architecture:** Three-component system (POC, CLI, Service)

### Components Status

| Component | Status | Location | Purpose |
|-----------|--------|----------|---------|
| **POC Scripts** | ✅ Complete | `poc/` | Experimental analysis validation |
| **Admin CLI** | 🚧 Phases 1-3 Complete | `src/stream_of_worship/admin/` | Catalog management, audio download |
| **Analysis Service** | 🔄 Phase 4 In Progress | `services/analysis/` | Audio analysis microservice |
| **User App** | 📋 Planned (Phase 8+) | `src/stream_of_worship/app/` | Transition songset & lyrics video generation |

---

## Architecture Overview

The project consists of **four architecturally separate components**:

### 1. 🧪 POC Scripts (Experimental)
- **Location:** `poc/` directory
- **Purpose:** Validate analysis algorithms during development
- **Runtime:** One-off script execution in Docker
- **Technologies:** Librosa (signal processing) or All-In-One (deep learning)
- **Status:** Archived experimental code (note: the `poc/transition_builder_v2/` TUI lives on as the `stream-of-worship tui` command)

### 2. 🖥️ Admin CLI (Backend Management)
- **Location:** `src/stream_of_worship/admin/` (Python package)
- **Purpose:** Backend tool for catalog management and audio operations
- **Users:** Administrators, DevOps
- **Runtime:** One-shot CLI commands (`sow-admin catalog scrape`, `sow-admin audio download`)
- **Dependencies:** **Lightweight** (~50MB) - typer, requests, yt-dlp, boto3
- **Database:** Local SQLite with Turso cloud sync support
- **Installation:** `uv run --extra admin sow-admin`

### 3. 🚀 Analysis Service (Microservice)
- **Location:** `services/analysis/` (separate package: `sow_analysis`)
- **Purpose:** CPU/GPU-intensive audio analysis and stem separation
- **Users:** Called by Admin CLI or User App
- **Runtime:** Long-lived FastAPI HTTP server (port 8000)
- **Technologies:** FastAPI, PyTorch, allin1, Demucs, Cloudflare R2
- **Dependencies:** **Heavy** (~2GB) - PyTorch, ML models, NATTEN
- **Deployment:** Docker container with platform-specific builds (x86_64 vs ARM64)
- **API:** REST endpoints at `http://localhost:8000/api/v1/`

### 4. 🎵 User App (End-User Application)
- **Location:** `src/stream_of_worship/app/` (planned)
- **Purpose:** Interactive tool for generating transition songsets and lyrics videos
- **Users:** Worship leaders, media team members
- **Runtime:** TUI (Textual framework) or GUI application
- **Technologies:** Textual (TUI), Pydub (audio), MoviePy (video), FFmpeg
- **Data Source:**
  - **Metadata:** Turso cloud database (synced from Admin CLI)
  - **Audio Assets:** Cloudflare R2 (pre-analyzed stems, LRC files)
- **Key Features:**
  - Browse master song catalog
  - Select songs for transitions (with compatibility scoring)
  - Adjust transition parameters (crossfade, tempo stretch, key shift)
  - Generate multi-song audio files with smooth transitions
  - Generate lyrics videos with synchronized LRC timing
  - Export final audio/video outputs
- **Evolution:** Production upgrade from `poc/transition_builder_v2/` TUI prototype

### Why Architecturally Separate?

| Concern | POC Scripts | Admin CLI | Analysis Service | User App |
|---------|-------------|-----------|------------------|----------|
| **Runtime Model** | Ad-hoc experimentation | One-shot commands | Long-lived daemon | Interactive session |
| **Target Users** | Developers | Admins / DevOps | Internal service | Worship leaders / media teams |
| **Dependencies** | Varies (experimental) | Minimal | Very heavy (PyTorch) | Moderate (FFmpeg, video libs) |
| **Distribution** | Development only | pip install (admin) | Docker image | Desktop app / pip install |
| **Deployment** | Local developer machine | Admin's machine | Cloud server / GPU | End-user's machine |
| **Data Access** | Local files | SQLite + R2 (read/write) | R2 + temp cache | Turso + R2 (read-only) |
| **Versioning** | Unversioned (experimental) | Semantic versioning | Independent API versions | Semantic versioning |
| **Communication** | N/A | HTTP client (to Service) | HTTP server | HTTP client (to Service) + Turso sync |

### Component Interaction

```
Backend Flow (Admin):
┌──────────────────┐
│  Admin CLI       │  ← Lightweight, runs on admin's machine
│  (sow-admin)     │
└────────┬─────────┘
         │
         ├─── catalog scrape ──→ sop.org → SQLite (local)
         │
         ├─── audio download ──→ YouTube → R2 upload → SQLite
         │
         └─── audio analyze ──→ HTTP POST /api/v1/jobs/analyze
                                          ↓
                         ┌────────────────────────────┐
                         │  Analysis Service          │  ← Heavy ML, GPU server
                         │  (FastAPI + Job Queue)     │
                         └────────────┬───────────────┘
                                      │
                         ┌────────────┴────────────┐
                         ↓                         ↓
                  ┌─────────────┐         ┌─────────────┐
                  │ allin1      │         │ Demucs      │
                  │ worker      │         │ worker      │
                  └──────┬──────┘         └──────┬──────┘
                         │                       │
                         └───────────┬───────────┘
                                     ↓
                            ┌─────────────────┐
                            │ Cloudflare R2   │  → Stems, JSON, LRC
                            └────────┬────────┘
                                     │
                         ┌───────────┴───────────┐
                         ↓                       ↓
                   ┌──────────┐          ┌─────────────┐
                   │ SQLite   │ ──sync→  │ Turso Cloud │
                   │ (local)  │          │ (replicated)│
                   └──────────┘          └──────┬──────┘
                                                 │
                                                 ↓
Frontend Flow (End-User):                       │
┌──────────────────┐                            │
│  User App        │  ← Interactive TUI/GUI     │
│  (sow-app)       │                            │
└────────┬─────────┘                            │
         │                                      │
         ├─── read catalog metadata ───────────┘
         │
         ├─── download audio/stems ───→ R2 (read-only)
         │
         ├─── generate transitions ──→ Local processing (Pydub)
         │
         └─── render lyrics video ───→ Local processing (MoviePy)
                     ↓
              Final outputs:
              - transition_songset.mp3
              - lyrics_video.mp4
```

**Key Design Decisions:**
1. **Admin CLI** never imports PyTorch/ML libraries. It submits jobs to Analysis Service via HTTP.
2. **User App** reads from Turso (metadata) and R2 (audio assets) but never writes. It's a read-only consumer.
3. **Analysis Service** is the only component with heavy ML dependencies and GPU access.
4. **Turso Sync** enables User App to work with up-to-date catalog without direct database access to admin's machine.

---

## Quick Start

Choose the component you want to work with:

- **[POC Scripts](#quick-start-poc)** - Experimental analysis validation (archived)
- **[Admin CLI](#cli-installation)** - Backend catalog and audio management (admins)
- **[Analysis Service](#analysis-service-setup)** - Audio analysis microservice (Phase 4)
- **[User App](#user-app-usage)** - Transition songset generation (end-users, Phase 8+)

---

## Quick Start (POC) {#quick-start-poc}

### Prerequisites

1. **Docker Desktop** installed and running ([Download](https://www.docker.com/products/docker-desktop/))
2. **3-5 worship songs** in MP3 or FLAC format (test files)
3. **Terminal/Command Prompt** access

### Step-by-Step Setup and Execution

#### Step 1: Prepare Your Audio Files

```bash
# Create the poc_audio directory if it doesn't exist (already exists in repo)
# Place your test worship songs (MP3 or FLAC format) into poc_audio/
cp /path/to/your/songs/*.mp3 poc_audio/

# Verify files were copied
ls poc_audio/
```

**Expected output:** List of your audio files (e.g., `song1.mp3`, `song2.mp3`, etc.)

#### Step 2: Build the Docker Image

```bash
# Build the Docker image (first time only, or after dependencies change)
docker-compose build

# This will take 3-5 minutes as it:
# - Downloads Python 3.11 base image
# - Installs system dependencies (ffmpeg, audio libraries)
# - Installs Python packages (librosa, madmom, etc.)
```

**Expected output:**
- Build progress messages
- Final line: `Successfully tagged stream_of_worship_librosa:latest` or similar

#### Step 3: Run the POC Analysis

Choose **ONE** of the following two methods:

---

**Method A: Command-Line Script (Recommended)**

Best for: Quick analysis, automation, debugging, CI/CD

```bash
# Run the POC analysis script in a one-off container
docker-compose run --rm librosa python poc/poc_analysis.py
```

**What happens:**
1. Docker starts a new container from the built image
2. Mounts your local `poc_audio/` and `poc_output/` directories
3. Runs the analysis script
4. Saves results to `poc_output/`
5. Container automatically removed after completion (`--rm` flag)

**Expected output:**
```
============================================================
POC ANALYSIS - Worship Music Transition System
============================================================

Stage 1/7: Setup and Discovery
--------------------------------------------------------------
Found 3 audio files in poc_audio/
Output directory: poc_output/

Stage 2/7: Feature Extraction
--------------------------------------------------------------
[1/3] Analyzing: song1.mp3
  ✓ Tempo: 120.0 BPM
  ✓ Key: C major
  ✓ Structure: 5 sections detected
...
```

**Runtime:** ~30-60 seconds per song (e.g., 3 songs = ~2-3 minutes total)

**Alternative (if container is already running):**
```bash
# If you have a running container from Method B, use exec instead:
docker-compose exec librosa python poc/poc_analysis.py
```

---

### POC Transition Builder (Mature - Standalone)

The Transition Builder is a **mature, standalone tool** for creating transition audio files between two songs. Unlike `sow-app` (which generates multi-song lyrics videos with database integration), the Transition Builder works directly with analysis JSON files and does not require a SQLite/Turso catalog.

**When to Use:**
- Quick experimentation with transition parameters between 2 songs
- Generate standalone transition audio clips
- Fine-tune crossfade, tempo matching, and key shifting
- When you don't need the full database infrastructure

**Key Differences from `sow-app`:**

| Feature | Transition Builder | Lyrics Video Builder (`sow-app`) |
|---------|-------------------|----------------------------------|
| Primary output | 2-song transition audio | Multi-song lyrics video |
| Catalog required | ❌ No (JSON files directly) | ✅ Yes (SQLite/Turso) |
| Use case | Quick experimentation | Production songsets |
| Video generation | ❌ No | ✅ Yes |
| Database | Standalone | Requires admin-managed catalog |

#### Step 1: Create Configuration File

Create `~/.local/share/sow/config.json` (Linux) or `~/Library/Application Support/sow/config.json` (macOS):

```json
{
  "audio_folder": "~/.local/share/sow/song_library",
  "output_folder": "~/.local/share/sow/output/transitions",
  "output_songs_folder": "~/.local/share/sow/output/songs",
  "analysis_json": "poc/output_allinone/poc_full_results.json",
  "stems_folder": "poc/output_allinone/stems",
  "lyrics_folder": "data/lyrics/songs",
  "audio_format": "ogg",
  "audio_bitrate": "192k",
  "audio_sample_rate": 48000,
  "video_resolution": "1080p",
  "error_logging": true,
  "session_logging": false,
  "llm_model": "openai/gpt-4o-mini"
}
```

**Key paths:**
- `analysis_json`: Path to POC analysis results (from Step 3 above)
- `stems_folder`: Path to separated audio stems
- `output_folder`: Where transition audio files will be saved
- `output_songs_folder`: Where full song exports will be saved

#### Step 2: Launch the Transition Builder TUI

```bash
# Install with TUI dependencies
uv sync --extra tui

# Launch Transition Builder
uv run stream-of-worship tui

# Or with custom config path
uv run stream-of-worship tui --config /path/to/config.json
```

#### Step 3: Using the TUI

1. **Select Outgoing Song** - Choose the first song (where transition starts)
2. **Select Incoming Song** - Choose the second song (where transition ends)
3. **Adjust Parameters:**
   - Crossfade duration
   - Tempo matching (BPM alignment)
   - Key shifting (musical key alignment)
   - Transition point selection
4. **Preview** - Listen to the transition
5. **Generate** - Export the transition audio file to `output_folder`

#### Additional CLI Commands

The `stream-of-worship` CLI also provides:

```bash
# Ingestion commands
stream-of-worship ingest analyze --song /path/to/audio.mp3
stream-of-worship ingest scrape-lyrics --limit 10
stream-of-worship ingest generate-lrc --song-id "song-id"
stream-of-worship ingest generate-metadata --song-id "song-id"

# Playlist commands
stream-of-worship playlist build --from-json playlist.json
stream-of-worship playlist export-video --from-json playlist.json
stream-of-worship playlist validate --from-json playlist.json

# Configuration
stream-of-worship config show
stream-of-worship config set key value
```

---

**Method B: Interactive Jupyter Notebook**

Best for: Exploration, visualization, experimentation, learning

```bash
# Step 1: Start Jupyter Lab server
docker-compose up

# Keep this terminal window open - it shows server logs
```

**Expected output:**
```
[I 2024-01-01 12:00:00.000 ServerApp] Jupyter Server is running at:
[I 2024-01-01 12:00:00.000 ServerApp] http://0.0.0.0:8888/lab
```

```bash
# Step 2: Open your web browser and navigate to:
http://localhost:8888

# Step 3: In the Jupyter Lab file browser (left sidebar):
# - Click on "notebooks" folder
# - Click on "01_POC_Analysis.ipynb"

# Step 4: Run the analysis
# - Menu → Run → Run All Cells
# - Or press Shift+Enter repeatedly to run each cell

# Step 5: Wait for analysis to complete
# - Watch for completion indicators in each cell
# - Final cell will print "POC Analysis Complete!"
```

**Runtime:** ~2-5 minutes for 3-5 songs (same as Method A, but with interactive visualization)

```bash
# Step 6: Stop Jupyter Lab when done
# Press Ctrl+C in the terminal where docker-compose up is running
# Then run:
docker-compose down
```

---

#### Step 4: Review Results

```bash
# Check the generated outputs
ls -lh poc_output/

# Expected files:
# - poc_summary.csv                     (summary table)
# - poc_full_results.json               (detailed data)
# - poc_analysis_visualizations.png     (song charts)
# - poc_compatibility_scores.csv        (compatibility matrix)
# - poc_compatibility_heatmap.png       (heatmap)
# - transition_<songA>_to_<songB>.flac (sample transition)
# - transition_waveform.png             (transition visualization)
```

**View results:**
```bash
# Open summary CSV in spreadsheet app
open poc_output/poc_summary.csv        # macOS
xdg-open poc_output/poc_summary.csv    # Linux
start poc_output/poc_summary.csv       # Windows

# View visualizations
open poc_output/poc_analysis_visualizations.png
```

---

### Alternative: All-In-One Deep Learning Analysis

The project includes an **experimental deep learning approach** using the `allin1` library for more advanced music analysis. This alternative method provides:

- **ML-based beat/downbeat/tempo detection** (instead of librosa's signal processing)
- **Automatic segment labeling** (intro, verse, chorus, bridge, outro)
- **Audio embeddings** (24-dimensional feature vectors per stem)
- **Comparison baseline** for evaluating traditional vs. deep learning approaches

#### Prerequisites for All-In-One

1. Same as above (Docker Desktop, audio files)
2. **More disk space**: ~2-3 GB for PyTorch and deep learning models
3. **Longer build time**: 10-20 minutes for first build (downloads models)

#### Step 1: Build the All-In-One Docker Image

```bash
# Build the allinone Docker image using the separate docker-compose file
docker compose -f docker/docker-compose.allinone.yml build

# This will take 10-20 minutes as it:
# - Installs PyTorch (CPU-only for x86_64, standard for ARM64/M-series)
# - Installs NATTEN library (neighborhood attention)
# - Installs allin1 music analysis library
# - Downloads pre-trained models on first run
```

**Expected output:**
- Build progress messages for allinone image
- Final line: `Successfully tagged allinone:latest` or similar

#### Step 2: Run All-In-One POC Analysis

```bash
# Run the POC analysis using all-in-one deep learning models
docker compose -f docker/docker-compose.allinone.yml run --rm allinone python poc/poc_analysis_allinone.py
```

**What happens:**
1. Docker starts container from allinone image
2. Mounts `poc/audio/` (input) and `poc/output_allinone/` (output)
3. Runs deep learning analysis with all-in-one models
4. Saves results to `poc/output_allinone/`
5. Container automatically removed after completion

**Expected output:**
```
✓ All-in-one library loaded successfully
============================================================
POC ANALYSIS (All-In-One) - Worship Music Transition System
============================================================

Stage 1/7: Setup and Discovery
--------------------------------------------------------------
Found 3 audio files in poc_audio/
Output directory: poc_output_allinone/

Stage 2/7: Feature Extraction (Deep Learning)
--------------------------------------------------------------
[1/3] Analyzing: song1.mp3
  Loading all-in-one models...
  ✓ Beat tracking (ML): 120.5 BPM (confidence: 0.95)
  ✓ Segment labels: intro → verse → chorus → verse → outro
  ✓ Embeddings extracted (24-dim per stem)
...
```

**Runtime:** ~2-3 minutes per song (longer than librosa due to model inference)
- First run: Additional 1-2 minutes to download pre-trained models

**Note:** Model weights are cached in `~/.cache/` and persisted between runs.

#### Step 3: Review All-In-One Results

```bash
# Check the generated outputs
ls -lh poc_output_allinone/

# Expected files (similar to librosa output, plus embeddings):
# - poc_allinone_summary.csv                (summary with ML predictions)
# - poc_allinone_full_results.json          (detailed data + embeddings)
# - poc_allinone_visualizations.png         (visualizations with ML labels)
# - poc_allinone_compatibility_scores.csv   (compatibility matrix)
# - poc_allinone_compatibility_heatmap.png  (heatmap)
# - transition_allinone_<songA>_to_<songB>.flac
# - transition_allinone_waveform.png
```

#### Comparison: Librosa vs. All-In-One

| Feature | Librosa (Traditional) | All-In-One (Deep Learning) |
|---------|----------------------|---------------------------|
| **Tempo Detection** | Signal processing (onset envelopes) | Neural network (trained on labeled data) |
| **Beat Tracking** | Dynamic programming | Transformer-based model |
| **Segment Labels** | Generic (section_0, section_1) | Semantic (intro, verse, chorus) |
| **Embeddings** | Hand-crafted features (MFCCs) | Learned 24-dim embeddings |
| **Speed** | Fast (~30-60s per song) | Slower (~2-3 min per song) |
| **Accuracy** | Good for most songs | Better on complex songs |
| **Setup** | Lightweight | Requires PyTorch + models |

**When to use each:**
- **Librosa**: Quick POC, simpler setup, good enough for most worship music
- **All-In-One**: Production system, complex song structures, need semantic labels

---

## CLI Installation {#cli-installation}

The `sow-admin` CLI is the production tool for managing the song catalog and audio library.

### Prerequisites
1. **Python 3.11+** installed
2. **uv** package manager ([Installation](https://docs.astral.sh/uv/getting-started/installation/))
3. **git** for cloning the repository

### Installation Steps

```bash
# Clone the repository
git clone https://github.com/yourusername/sow_cli_admin.git
cd sow_cli_admin

# Install the CLI with admin extras
uv sync --extra admin

# Verify installation
uv run sow-admin --version
```

### Basic Usage

```bash
# Initialize database
uv run sow-admin db init

# Scrape song catalog from sop.org
uv run sow-admin catalog scrape

# Search for songs
uv run sow-admin catalog search "主祢是愛"

# Download audio for a song (requires YouTube, R2 credentials)
uv run sow-admin audio download --song-id "zhu-ni-shi-ai-1"

# List downloaded recordings
uv run sow-admin audio list

# Submit analysis job (requires Analysis Service running)
uv run sow-admin audio analyze --recording-id "abc123def456"
```

### Configuration

Create a config file at `~/.config/sow-admin/config.toml`:

```toml
[database]
path = "/Users/you/.local/share/sow-admin/sow.db"

[r2]
bucket = "your-r2-bucket"
endpoint_url = "https://your-account.r2.cloudflarestorage.com"
region = "auto"

[analysis_service]
base_url = "http://localhost:8000"
```

Set environment variables for R2 (takes precedence over config file):
```bash
# Non-sensitive R2 config
export SOW_R2_BUCKET="your-bucket"
export SOW_R2_ENDPOINT_URL="https://xxx.r2.cloudflarestorage.com"
export SOW_R2_REGION="auto"

# Sensitive credentials (never commit these)
export SOW_R2_ACCESS_KEY_ID="your-access-key"
export SOW_R2_SECRET_ACCESS_KEY="your-secret-key"
```

**See [CLI Documentation](docs/cli-usage.md) for complete command reference.**

### Batch Operations

The CLI uses `song_id` as the user-facing identifier, making batch operations easy with standard Unix tools:

```bash
# Download audio for all songs in an album
sow-admin catalog list --album "敬拜讚美15" --format ids | \
  xargs -I{} sow-admin audio download {}

# Analyze all songs that have audio but no analysis yet
sow-admin audio list --status pending --format ids | \
  xargs -I{} sow-admin audio analyze {}

# Download and analyze a specific list of songs
cat songs_to_process.txt | xargs -I{} sow-admin audio download {}
cat songs_to_process.txt | xargs -I{} sow-admin audio analyze {}

# Check status of all pending analyses
sow-admin audio status
```

**Tip:** The `--format ids` flag outputs one ID per line, making it perfect for piping to `xargs`.

---

## Analysis Service Setup {#analysis-service-setup}

The Analysis Service is a FastAPI microservice that performs audio analysis and stem separation.

### Prerequisites
1. **Docker Desktop** installed and running
2. **Cloudflare R2** account and credentials
3. **8GB+ RAM** (16GB recommended for GPU)
4. **GPU** (optional, but recommended for faster processing)

### Quick Start

```bash
# Navigate to service directory
cd services/analysis

# Set environment variables
cp .env.example .env
# Edit .env with your R2 credentials:
#   SOW_R2_ACCESS_KEY_ID=your-key
#   SOW_R2_SECRET_ACCESS_KEY=your-secret

# Build the Docker image (takes 10-20 minutes first time)
docker compose build

# Start the service
docker compose up -d

# Check service health
curl http://localhost:8000/api/v1/health
# Expected: {"status": "healthy", "version": "0.1.0"}

# View logs
docker compose logs -f
```

### Submit Analysis Job

```bash
# Via CLI (recommended)
uv run sow-admin audio analyze --recording-id "abc123def456"

# Or via direct HTTP request
curl -X POST http://localhost:8000/api/v1/jobs/analyze \
  -H "Content-Type: application/json" \
  -d '{
    "recording_id": "abc123def456",
    "audio_url": "s3://bucket/audio.mp3",
    "options": {
      "extract_stems": true,
      "compute_embeddings": true
    }
  }'
```

### Check Job Status

```bash
# Get job status
curl http://localhost:8000/api/v1/jobs/{job_id}

# Response:
# {
#   "job_id": "550e8400-e29b-41d4-a716-446655440000",
#   "status": "completed",
#   "progress": 1.0,
#   "result": {
#     "tempo_bpm": 120.5,
#     "musical_key": "C",
#     "musical_mode": "major",
#     "duration_seconds": 245.3,
#     ...
#   }
# }
```

**See [Analysis Service Documentation](services/analysis/README.md) for API reference.**

---

## User App Usage {#user-app-usage}

**Status:** Planned for Phase 8+ (after Admin CLI + Analysis Service are complete)

The User App is the end-user facing tool for creating transition songsets and lyrics videos. It will be a production-ready evolution of the `poc/transition_builder_v2/` TUI prototype.

### Planned Features

**Song Selection**
- Browse master song catalog (synced from Turso)
- Search by title, artist, album, key, tempo
- View song metadata and analysis results
- Preview audio stems (vocals, drums, bass, other)

**Transition Generation**
- Select multiple songs for a songset
- View compatibility scores between songs
- Adjust transition parameters:
  - Crossfade duration
  - Tempo stretching (match BPM)
  - Key shifting (match musical key)
  - Transition point selection (verse → chorus, etc.)
- Real-time preview of transitions
- Export multi-song audio file

**Lyrics Video Generation**
- Load LRC files from R2
- Select video template (backgrounds, fonts, animations)
- Customize styling (colors, positioning, effects)
- Sync lyrics with audio timeline
- Export MP4 video file

### Planned Installation (Phase 8+)

```bash
# Install User App with app extras
uv sync --extra app

# Run the User App
uv run sow-app

# Or with GUI version (future)
uv run sow-app --gui
```

### Data Dependencies

The User App requires:
1. **Turso database access** (read-only) - Song catalog metadata
2. **R2 storage access** (read-only) - Audio stems, LRC files
3. **FFmpeg** installed locally - Audio/video processing

No direct Admin CLI access needed - User App is fully decoupled.

**See [User App Roadmap](#phase-8-user-app-development-planned) for development timeline.**

---

## POC Validation Checklist

### 1. Tempo Accuracy

- [ ] Tap along to each song manually
- [ ] Compare to detected BPM (should be ±5 BPM)
- [ ] Verify in `poc_summary.csv`

**Success Criteria:** ≥80% of songs within ±5 BPM

### 2. Key Detection

- [ ] Compare to sheet music (if available)
- [ ] Or use external tool (Mixed In Key, Tunebat)
- [ ] Check `full_key` column in summary

**Success Criteria:** ≥70% match sheet music

### 3. Transition Quality

- [ ] Listen to `transition_*.flac` file
- [ ] Does crossfade sound natural?
- [ ] Any jarring discontinuities?

**Success Criteria:** Smooth, natural-sounding transition

### 4. Section Boundaries

- [ ] Review `poc_analysis_visualizations.png`
- [ ] Do colored sections align with actual structure?
- [ ] Are intro/outro/verse/chorus labels reasonable?

**Success Criteria:** ≥50% of boundaries align with real changes

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

### Analysis Issues

**Problem:** Tempo detection seems wrong

```python
# In poc/poc_analysis.py or Notebook Cell 2, adjust start_bpm parameter:
tempo_librosa, beats_frames = librosa.beat.beat_track(
    y=y, sr=sr,
    start_bpm=90,  # Change this (try 70 for slow, 120 for fast)
    units='frames'
)
```

**Problem:** Too many/few section boundaries

```python
# In poc/poc_analysis.py or Notebook Cell 2, adjust peak picking parameters:
peaks = librosa.util.peak_pick(
    onset_env,
    pre_max=5,     # Increase for fewer boundaries
    post_max=5,    # Increase for fewer boundaries
    delta=0.5,     # Increase for fewer boundaries
    wait=15
)
```

---

## Project Structure

```
sow_cli_admin/                           # Repository root
│
├── src/stream_of_worship/admin/         # 🖥️ Admin CLI Package (backend)
│   ├── commands/                        #    CLI command groups
│   │   ├── db.py                        #    - db init/status/reset
│   │   ├── catalog.py                   #    - catalog scrape/list/search
│   │   └── audio.py                     #    - audio download/list/analyze
│   ├── services/                        #    Business logic
│   │   ├── scraper.py                   #    - HTML scraping (sop.org)
│   │   ├── youtube.py                   #    - yt-dlp wrapper
│   │   ├── hasher.py                    #    - SHA-256 hashing
│   │   └── r2.py                        #    - R2 storage client
│   ├── db/                              #    Database layer
│   │   ├── client.py                    #    - SQLite client
│   │   ├── schema.py                    #    - Table definitions
│   │   └── models.py                    #    - Pydantic models
│   ├── config.py                        #    TOML config loader
│   └── main.py                          #    Typer app entry point
│
├── src/stream_of_worship/app/           # 🎵 User App Package (frontend)
│   │                                    #    [Planned - Phase 8+]
│   ├── screens/                         #    TUI screens (Textual)
│   │   ├── catalog_browser.py           #    - Song catalog browser
│   │   ├── transition_builder.py        #    - Transition builder UI
│   │   └── video_generator.py           #    - Lyrics video generator
│   ├── services/                        #    Business logic
│   │   ├── turso_client.py              #    - Turso database reader
│   │   ├── r2_downloader.py             #    - R2 asset downloader
│   │   ├── transition_engine.py         #    - Audio transition generator
│   │   └── video_renderer.py            #    - Video rendering engine
│   ├── models.py                        #    Data models
│   └── main.py                          #    App entry point
│
├── services/analysis/                   # 🚀 Analysis Service (heavy ML)
│   ├── src/sow_analysis/                #    Service package (separate)
│   │   ├── main.py                      #    FastAPI app
│   │   ├── config.py                    #    Service configuration
│   │   ├── models.py                    #    Request/response schemas
│   │   ├── routes/                      #    API endpoints
│   │   │   ├── health.py                #    - GET /health
│   │   │   └── jobs.py                  #    - POST/GET /jobs/*
│   │   ├── workers/                     #    Background workers
│   │   │   ├── analyzer.py              #    - allin1 analysis
│   │   │   ├── separator.py             #    - Demucs stem separation
│   │   │   ├── lrc.py                   #    - LRC generation (Phase 6)
│   │   │   └── queue.py                 #    - In-memory job queue
│   │   └── storage/                     #    Storage layer
│   │       ├── r2.py                    #    - R2 client (async)
│   │       └── cache.py                 #    - Content-hash cache
│   ├── Dockerfile                       #    Multi-stage Docker build
│   ├── docker-compose.yml               #    Service orchestration
│   ├── pyproject.toml                   #    Service dependencies
│   └── README.md                        #    API documentation
│
├── poc/                                 # 🧪 POC Scripts (archived)
│   ├── docker/                          #    POC Docker environments
│   │   ├── docker-compose.yml           #    - Librosa environment
│   │   ├── docker-compose.allinone.yml  #    - Deep learning environment
│   │   ├── Dockerfile                   #    - Librosa image
│   │   └── Dockerfile.allinone          #    - All-In-One image
│   ├── poc_analysis.py                  #    Librosa analysis script
│   ├── poc_analysis_allinone.py         #    Deep learning analysis
│   ├── lyrics_scraper.py                #    Lyrics scraper prototype
│   ├── audio/                           #    Test audio files
│   ├── output/                          #    Librosa results
│   ├── output_allinone/                 #    All-In-One results
│   └── transition_builder_v2/           #    Legacy TUI (archived)
│
├── tests/admin/                         # CLI unit tests
│   ├── commands/                        #    Command tests
│   ├── services/                        #    Service tests
│   └── db/                              #    Database tests
│
├── specs/                               # Design documents
│   ├── sow_admin_design.md              #    CLI + Service architecture
│   └── worship-music-transition-system-design.md  # Original POC spec
│
├── reports/                             # Implementation plans
│   └── phase4_detailed_impl_plan.md     #    Analysis Service plan
│
├── pyproject.toml                       # Root project config
├── README.md                            # This file
└── .gitignore                           # Git exclusions
```

### Key Separation Points

| Directory | Package Name | Purpose | Target Users | Deployment |
|-----------|-------------|---------|--------------|------------|
| `src/stream_of_worship/admin/` | `stream-of-worship-admin` | Backend management CLI | Admins / DevOps | `pip install` (admin) |
| `src/stream_of_worship/app/` | `stream-of-worship-app` | End-user transition/video tool | Worship leaders / media teams | Desktop app or `pip install` |
| `services/analysis/` | `sow-analysis` | Audio analysis microservice | Internal service | Docker image |
| `poc/` | N/A (scripts) | Experimental validation | Developers | Local scripts only |

---

## Development Roadmap

### ✅ Phase 1: Foundation (Complete)
- [x] CLI scaffold (Typer)
- [x] Database schema (SQLite + Turso sync support)
- [x] Configuration (TOML)
- [x] `db` command group (init, status, reset)

### ✅ Phase 2: Catalog Management (Complete)
- [x] Web scraper for sop.org
- [x] Song ID normalization (Chinese → pinyin)
- [x] `catalog` command group (scrape, list, search, show)
- [x] Incremental scraping

### ✅ Phase 3: Audio Download (Complete)
- [x] YouTube search and download (yt-dlp)
- [x] Content-hash based deduplication (SHA-256)
- [x] Cloudflare R2 upload
- [x] `audio` command group (download, list, show)
- [x] Recording metadata tracking

### 🔄 Phase 4: Analysis Service (In Progress)
- [ ] FastAPI service architecture
- [ ] Job queue (in-memory for MVP, Redis later)
- [ ] allin1 worker (tempo, key, beats, sections, embeddings)
- [ ] Demucs worker (stem separation)
- [ ] R2 stems upload
- [ ] Docker deployment (x86_64 + ARM64 support)
- [ ] CLI integration (`audio analyze`, `audio status`)

### 📋 Phase 5: CLI ↔ Service Integration (Planned)
- [ ] `audio analyze` command (submit jobs via HTTP)
- [ ] `audio status` command (poll job status)
- [ ] `audio results` command (fetch analysis results)
- [ ] Retry logic and error handling
- [ ] Progress indicators

### 📋 Phase 6: LRC Generation (Planned)
- [ ] Whisper transcription worker
- [ ] LLM line alignment (GPT-4 / Claude)
- [ ] LRC file generation
- [ ] R2 LRC upload
- [ ] `lyrics generate` command
- [ ] `lyrics show` command

### 📋 Phase 7: Turso Sync (Planned)
- [ ] Turso cloud database setup
- [ ] Bidirectional sync logic (Admin CLI ↔ Turso)
- [ ] Conflict resolution
- [ ] `db sync` command
- [ ] Multi-device admin support

### 📋 Phase 8: User App Development (Planned)
- [ ] Textual TUI framework setup
- [ ] Turso client (read-only connection)
- [ ] R2 downloader (audio stems, LRC files)
- [ ] Song catalog browser screen
- [ ] Transition builder screen
  - [ ] Song selection with compatibility scores
  - [ ] Parameter adjustment (crossfade, tempo, key)
  - [ ] Real-time transition preview
- [ ] Lyrics video generator screen
  - [ ] LRC file loader
  - [ ] Template selection and styling
  - [ ] Video rendering with MoviePy
- [ ] Export functionality (audio + video)
- [ ] `sow-app` command entry point

### 📋 Phase 9: User App Enhancements (Future)
- [ ] GUI version (PyQt or Electron)
- [ ] Cloud rendering service (offload video generation)
- [ ] Template marketplace (custom video templates)
- [ ] Playlist scheduling (service planning)
- [ ] Multi-output formats (720p, 1080p, 4K)

**Current Focus:** Phase 4 - Analysis Service implementation

---

## Resources

- **POC Script Guide:** [poc/README.md](poc/README.md)
- **Design Document:** [specs/worship-music-transition-system-design.md](specs/worship-music-transition-system-design.md)
- **librosa Documentation:** https://librosa.org/doc/latest/
- **madmom Documentation:** https://madmom.readthedocs.io/
- **Stream of Praise:** https://www.sop.org/

---

## Contributing

POC phase is exploratory. Feedback on:

- Analysis accuracy
- Additional test songs
- Edge cases or failure modes

---

## License

MIT License - See [LICENSE](LICENSE) file

---

**Last Updated:** 2025-12-30
**POC Status:** Ready for validation (Standalone script + Jupyter notebook)
