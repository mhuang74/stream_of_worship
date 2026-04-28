# Developer Documentation

This document contains technical details for developers and contributors. For user-facing instructions, see [README.md](README.md).

---

## Table of Contents

1. [Project Status](#project-status)
2. [Architecture Overview](#architecture-overview)
3. [Backend Services](#backend-services)
4. [POC Analysis Setup](#poc-analysis-setup)
5. [Project Structure](#project-structure)
6. [Development Roadmap](#development-roadmap)
7. [Advanced Configuration](#advanced-configuration)
8. [Troubleshooting](#troubleshooting)

---

## Project Status

**Current Phase:** Phase 4 - Analysis Service Implementation  
**Architecture:** Three-component system (POC, CLI, Service)

### Components Status

| Component | Status | Location | Purpose |
|-----------|--------|----------|---------|
| **POC Scripts** | ✅ Complete | `poc/` | Experimental analysis validation |
| **Admin CLI** | 🚧 Phases 1-3 Complete | `src/stream_of_worship/admin/` | Catalog management, audio download |
| **Analysis Service** | 🔄 Phase 4 In Progress | `services/analysis/` | Audio analysis microservice |
| **User App** | ✅ Complete | `src/stream_of_worship/app/` | Transition songset & lyrics video generation |

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
- **Location:** `src/stream_of_worship/app/` (Python package)
- **Purpose:** Interactive tool for generating transition songsets and lyrics videos
- **Users:** Worship leaders, media team members
- **Runtime:** TUI (Textual framework)
- **Technologies:** Textual (TUI), Pydub (audio), Pillow/FFmpeg (video)
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

## Backend Services

The project includes two backend microservices:

### 1. Analysis Service (`services/analysis/`)

FastAPI-based audio analysis service with job queue management.

- **Port:** 8000
- **Purpose:** Audio analysis (tempo, key, beats), stem separation
- **Technologies:** FastAPI, PyTorch, allin1, Demucs
- **Status:** Phase 4 (In Progress)

**Documentation:** [services/analysis/README.md](services/analysis/README.md)

### 2. Qwen3 Alignment Service (`services/qwen3/`)

FastAPI service for forced alignment of lyrics to audio timestamps using Qwen3-ForcedAligner-0.6B.

- **Port:** Configurable (default 8001)
- **Purpose:** LRC generation via forced alignment (not transcription)
- **Technologies:** FastAPI, Qwen3-ForcedAligner, PyTorch
- **Features:**
  - Aligns known lyrics to audio timestamps
  - Line-level timestamps preserved
  - 5-minute audio limit

**Documentation:** [services/qwen3/README.md](services/qwen3/README.md)

---

## POC Analysis Setup

For developers who need to run the experimental analysis scripts.

### Prerequisites

1. **Docker Desktop** installed and running
2. **3-5 worship songs** in MP3 or FLAC format
3. **Terminal/Command Prompt** access

### Step 1: Prepare Audio Files

```bash
# Place test worship songs into poc_audio/
cp /path/to/your/songs/*.mp3 poc_audio/

# Verify files were copied
ls poc_audio/
```

### Step 2: Build Docker Image

```bash
# Build the Docker image (first time only)
docker-compose build
```

### Step 3: Run POC Analysis

**Method A: Command-Line Script (Recommended)**

```bash
# Run POC analysis in one-off container
docker-compose run --rm librosa python poc/poc_analysis.py
```

**Method B: Interactive Jupyter Notebook**

```bash
# Start Jupyter Lab
docker-compose up

# Open browser to http://localhost:8888
# Navigate to notebooks/01_POC_Analysis.ipynb
```

### Alternative: All-In-One Deep Learning Analysis

For ML-based analysis with semantic segment labels:

```bash
# Build All-In-One image (10-20 min first time)
docker compose -f docker/docker-compose.allinone.yml build

# Run analysis
docker compose -f docker/docker-compose.allinone.yml run --rm allinone python poc/poc_analysis_allinone.py
```

**Comparison:**

| Feature | Librosa (Traditional) | All-In-One (Deep Learning) |
|---------|----------------------|---------------------------|
| **Tempo Detection** | Signal processing | Neural network |
| **Segment Labels** | Generic (section_0) | Semantic (verse, chorus) |
| **Embeddings** | MFCCs | Learned 24-dim |
| **Speed** | ~30-60s/song | ~2-3 min/song |
| **Setup** | Lightweight | ~2-3 GB PyTorch |

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
│   ├── screens/                           #    TUI screens (Textual)
│   │   ├── generation.py                #    - Transition generator
│   │   ├── browser.py                   #    - Song catalog browser
│   │   └── songset_manager.py          #    - Songset management
│   ├── services/                        #    Business logic
│   │   ├── audio_engine.py             #    - Audio processing
│   │   ├── video_engine.py             #    - Video generation
│   │   ├── asset_cache.py              #    - R2 asset management
│   │   └── turso_client.py             #    - Database sync
│   ├── config.py                        #    TOML config loader
│   └── main.py                          #    App entry point
│
├── services/analysis/                   # 🚀 Analysis Service (heavy ML)
│   ├── src/sow_analysis/                #    Service package
│   │   ├── main.py                      #    FastAPI app
│   │   ├── routes/                      #    API endpoints
│   │   └── workers/                     #    Background workers
│   └── README.md                        #    Service documentation
│
├── services/qwen3/                      # 🎯 Qwen3 Alignment Service
│   ├── src/sow_qwen3/                   #    Service package
│   │   ├── main.py                      #    FastAPI app
│   │   ├── routes/                      #    API endpoints
│   │   └── workers/                     #    Alignment workers
│   └── README.md                        #    Service documentation
│
├── poc/                                 # 🧪 POC Scripts (archived)
│   ├── docker/                          #    POC Docker environments
│   ├── poc_analysis.py                  #    Librosa analysis script
│   ├── poc_analysis_allinone.py         #    Deep learning analysis
│   └── transition_builder_v2/           #    Legacy TUI
│
├── tests/                               # Test suites
│   ├── admin/                           #    Admin CLI tests
│   ├── app/                             #    User App tests
│   └── services/                        #    Service tests
│
├── specs/                               # Design documents
├── reports/                             # Implementation plans
├── pyproject.toml                       # Root project config
├── README.md                            # User-facing documentation
└── DEVELOPER.md                         # This file
```

### Key Separation Points

| Directory | Package Name | Purpose | Target Users | Deployment |
|-----------|-------------|---------|--------------|------------|
| `src/stream_of_worship/admin/` | `stream-of-worship-admin` | Backend management CLI | Admins / DevOps | `pip install` (admin) |
| `src/stream_of_worship/app/` | `stream-of-worship-app` | End-user transition/video tool | Worship leaders | Desktop app / pip install |
| `services/analysis/` | `sow-analysis` | Audio analysis microservice | Internal service | Docker image |
| `services/qwen3/` | `sow-qwen3` | Lyrics alignment microservice | Internal service | Docker image |
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

### ✅ Phase 8: User App Development (Complete)
- [x] Textual TUI framework setup
- [x] Turso client (read-only connection)
- [x] R2 downloader (audio stems, LRC files)
- [x] Song catalog browser screen
- [x] Transition builder screen
  - [x] Song selection with compatibility scores
  - [x] Parameter adjustment (crossfade, tempo, key)
  - [x] Real-time transition preview
- [x] Lyrics video generator screen
  - [x] LRC file loader
  - [x] Template selection and styling
  - [x] Video rendering with FFmpeg
- [x] Export functionality (audio + video)
- [x] `sow-app` command entry point

### 📋 Phase 9: User App Enhancements (Future)
- [ ] GUI version (PyQt or Electron)
- [ ] Cloud rendering service (offload video generation)
- [ ] Template marketplace (custom video templates)
- [ ] Playlist scheduling (service planning)
- [ ] Multi-output formats (720p, 1080p, 4K)

**Current Focus:** Phase 4 - Analysis Service implementation

---

## Advanced Configuration

### Admin CLI Configuration

Create `~/.config/sow-admin/config.toml`:

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

Environment variables (take precedence):
```bash
export SOW_R2_BUCKET="your-bucket"
export SOW_R2_ENDPOINT_URL="https://xxx.r2.cloudflarestorage.com"
export SOW_R2_REGION="auto"
export SOW_R2_ACCESS_KEY_ID="your-access-key"
export SOW_R2_SECRET_ACCESS_KEY="your-secret-key"
```

### Batch Operations

The CLI uses `song_id` for easy batch operations:

```bash
# Download audio for all songs in an album
sow-admin catalog list --album "敬拜讚美15" --format ids | \
  xargs -I{} sow-admin audio download {}

# Analyze all songs pending analysis
sow-admin audio list --status pending --format ids | \
  xargs -I{} sow-admin audio analyze {}
```

---

## Troubleshooting

### Analysis-Specific Issues

**Problem:** Tempo detection seems wrong

```python
# In poc/poc_analysis.py, adjust start_bpm parameter:
tempo_librosa, beats_frames = librosa.beat.beat_track(
    y=y, sr=sr,
    start_bpm=90,  # Try 70 for slow, 120 for fast
    units='frames'
)
```

**Problem:** Too many/few section boundaries

```python
# Adjust peak picking parameters:
peaks = librosa.util.peak_pick(
    onset_env,
    pre_max=5,     # Increase for fewer boundaries
    post_max=5,
    delta=0.5,     # Increase for fewer boundaries
    wait=15
)
```

### Service Issues

**Problem:** Analysis Service fails to start
- Check R2 credentials in `.env`
- Verify port 8000 is not in use
- Check Docker logs: `docker compose logs -f`

**Problem:** Qwen3 service model not loading
- Verify model is downloaded: `huggingface-cli download Qwen/Qwen3-ForcedAligner-0.6B`
- Check `SOW_QWEN3_MODEL_PATH` environment variable
- Check memory allocation (8GB minimum)

---

## Resources

- **Design Document:** [specs/worship-music-transition-system-design.md](specs/worship-music-transition-system-design.md)
- **Analysis Service:** [services/analysis/README.md](services/analysis/README.md)
- **Qwen3 Service:** [services/qwen3/README.md](services/qwen3/README.md)
- **librosa Documentation:** https://librosa.org/doc/latest/

---

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Run tests: `PYTHONPATH=src uv run pytest tests/`
5. Submit a pull request

---

**Last Updated:** 2025-12-30
