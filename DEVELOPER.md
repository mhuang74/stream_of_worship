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

**Current Phase:** Web App production, Admin CLI operational, Analysis Service running  
**Architecture:** Six-component system with shared PostgreSQL (Neon) database

### Components Status

| Component | Status | Location | Purpose |
|-----------|--------|----------|---------|
| **POC Scripts** | ✅ Archived | `poc/` | Experimental analysis validation (legacy) |
| **Admin CLI** | ✅ Operational | `src/stream_of_worship/admin/` | Catalog management, audio download, schema init |
| **Analysis Service** | ✅ Operational | `services/analysis/` | Audio analysis, stem separation, LRC generation |
| **User App** | ⚠️ Deprecated | `src/stream_of_worship/app/` | TUI (deprecated in favor of Web App) |
| **Web App** | ✅ Production | `webapp/` | Primary end-user interface (Next.js) |
| **Render Worker** | ✅ Production | `services/render-worker/` | AWS Lambda render processing |

---

## Architecture Overview

The project consists of **six architecturally separate components**:

### 1. 🧪 POC Scripts (Archived Experimental)
- **Location:** `poc/` directory
- **Purpose:** Validate analysis algorithms during development
- **Runtime:** One-off script execution in Docker
- **Technologies:** Librosa (signal processing) or All-In-One (deep learning)
- **Status:** Archived. The `poc/transition_builder_v2/` TUI lives on as the `stream-of-worship tui` command but is also deprecated.

### 2. 🖥️ Admin CLI (Backend Management)
- **Location:** `src/stream_of_worship/admin/` (Python package)
- **Purpose:** Backend tool for catalog management and audio operations
- **Users:** Administrators, DevOps
- **Runtime:** One-shot CLI commands (`sow-admin catalog scrape`, `sow-admin audio download`)
- **Dependencies:** **Lightweight** (~50MB) - typer, rich, psycopg3, boto3, yt-dlp
- **Database:** **PostgreSQL (Neon)** via `psycopg` (psycopg3, synchronous) with `ConnectionProvider` for auto-reconnect and cold-start retry
- **Installation:** `uv run --extra admin sow-admin`

### 3. 🚀 Analysis Service (Microservice)
- **Location:** `services/analysis/` (separate package: `sow_analysis`)
- **Purpose:** CPU/GPU-intensive audio analysis and stem separation
- **Users:** Called by Admin CLI or Web App
- **Runtime:** Long-lived FastAPI HTTP server (port 8000)
- **Technologies:** FastAPI, PyTorch, allin1, Demucs, audio-separator, Cloudflare R2
- **Dependencies:** **Heavy** (~2GB) - PyTorch, ML models, NATTEN
- **Database:** **SQLite** (via `aiosqlite`) for job queue persistence only — **not** connected to the shared PostgreSQL
- **Deployment:** Docker container with platform-specific builds (x86_64 vs ARM64)
- **API:** REST endpoints at `http://localhost:8000/api/v1/`

### 4. 🎵 User App (Deprecated)
- **Location:** `src/stream_of_worship/app/` (Python package)
- **Purpose:** Interactive TUI for transitions and lyrics video generation (**deprecated**)
- **Users:** Worship leaders, media team members (migrating to Web App)
- **Runtime:** TUI (Textual framework)
- **Technologies:** Textual (TUI), psycopg3, Pydub, Pillow, FFmpeg
- **Database:** **PostgreSQL (Neon)** via shared `ConnectionProvider` — migration status uncertain
- **Note:** The Web App (`sow-webapp`) is now the recommended interface for all end-user operations.

### 5. 🌐 Web App (Primary End-User Interface)
- **Location:** `webapp/` (Node.js/TypeScript, Next.js 16 App Router)
- **Purpose:** Browser-based worship set editor and playback
- **Users:** Worship leaders, media teams, end users
- **Runtime:** Next.js server (Vercel deployment) + browser client
- **Technologies:** Next.js 16, Drizzle ORM, Better Auth, pgvector, Cloudflare R2
- **Database:** **PostgreSQL (Neon)** via `@neondatabase/serverless` + Drizzle ORM
- **Auth:** Better Auth with `drizzleAdapter`
- **Key Features:**
  - Browse and search song catalog (full-text tsvector + semantic pgvector search)
  - Create and manage multi-song worship sets with transition configuration
  - Submit render jobs (processed asynchronously via AWS Lambda)
  - Real-time progress via SSE (Server-Sent Events)
  - Built-in playback controller with synchronized lyrics
  - Second-screen projection via W3C Presentation API or Google Cast
  - LRC lyrics review and editing
  - Shareable public player links

### 6. ⚡ Render Worker (AWS Lambda)
- **Location:** `services/render-worker/` (Python, deployed as Lambda container via private ECR)
- **Purpose:** Serverless render processing (audio mixing + video encoding)
- **Users:** Called by Web App via SQS
- **Runtime:** AWS Lambda container (triggered by SQS events)
- **Technologies:** psycopg2, boto3, Pillow, FFmpeg, Cloudflare R2
- **Dependencies:** **Moderate** — psycopg2-binary, boto3, Pillow, ffmpeg-python
- **Database:** **PostgreSQL (Neon)** via `psycopg2` (synchronous, connection string)
- **Queue:** AWS SQS (render jobs enqueued by Web App, processed by Lambda)
- **Deployment:** Docker container → private AWS ECR → Lambda function

### Why Architecturally Separate?

| Concern | Admin CLI | Analysis Service | User App (Dep.) | Web App | Render Worker |
|---------|-----------|------------------|-----------------|---------|---------------|
| **Runtime Model** | One-shot commands | Long-lived daemon | Interactive TUI | Serverless + browser | Event-driven Lambda |
| **Target Users** | Admins / DevOps | Internal service | End users (legacy) | End users | Internal service |
| **Dependencies** | Minimal | Very heavy (PyTorch) | Moderate | Node.js stack | Moderate (psycopg2, FFmpeg) |
| **Distribution** | `uv run --extra admin` | Docker image | `uv run --extra app` | Vercel | Lambda container |
| **Data Access** | PostgreSQL (Neon) + R2 | R2 + SQLite (jobs) | PostgreSQL (Neon) + R2 | PostgreSQL (Neon) + R2 | PostgreSQL (Neon) + R2 |
| **Database Driver** | psycopg3 | aiosqlite | psycopg3 | Drizzle ORM + Neon | psycopg2 |

### Shared Database Architecture

All components except the Analysis Service share a **single PostgreSQL database hosted on Neon**:

```
                    ┌─────────────────────────────┐
                    │   PostgreSQL (Neon)         │
                    │                             │
                    │  Catalog Tables:            │
                    │    songs, recordings        │
                    │    song_embedding,          │
                    │    song_line_embedding      │
                    │  Auth Tables (Better Auth): │
                    │    user, account, session,  │
                    │    verification             │
                    │  App Tables:                │
                    │    songsets, songset_items  │
                    │    render_jobs              │
                    │    user_settings,           │
                    │    user_lrc_override,       │
                    │    lyric_mark, songset_share│
                    └────────┬────────────────────┘
                             │
            ┌────────────────┼────────────────┐
            │                │                │
     ┌──────┴──────┐  ┌─────┴──────┐  ┌─────┴──────┐
     │ Admin CLI   │  │  Web App   │  │Render Worker│
     │ (psycopg3)  │  │(Drizzle)   │  │ (psycopg2)  │
     └─────────────┘  └────────────┘  └────────────┘
            │                │                │
            └────────────────┼────────────────┘
                             │
                    ┌────────┴────────┐
                    │  Cloudflare R2  │
                    │  (audio, stems, │
                    │   LRC, videos)  │
                    └─────────────────┘
```

**Key Design Decisions:**
1. **Admin CLI** never imports PyTorch/ML libraries. It manages catalog and submits jobs to Analysis Service via HTTP.
2. **Analysis Service** is the only component with heavy ML dependencies and uses SQLite only for its internal job queue (not connected to shared PostgreSQL).
3. **Web App** is the primary end-user interface, using Drizzle ORM with Neon's serverless driver.
4. **Render Worker** shares the same PostgreSQL database as the Web App for render job status tracking.
5. **User App** is deprecated; all new development should target the Web App.

### Component Interaction

```
Backend Flow (Admin):
┌──────────────────┐
│  Admin CLI       │  ← Lightweight, runs on admin's machine
│  (sow-admin)     │
└────────┬─────────┘
         │
         ├─── catalog scrape ──→ sop.org → PostgreSQL (Neon)
         │
         ├─── audio download ──→ YouTube → R2 upload → PostgreSQL
         │
         └─── audio analyze ──→ HTTP POST /api/v1/jobs/analyze
                                          ↓
                         ┌────────────────────────────┐
                         │  Analysis Service          │  ← Heavy ML, Docker
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
                            └─────────────────┘

Frontend Flow (End-User):
┌──────────────────┐
│  Web App         │  ← Next.js on Vercel
│  (sow-webapp)    │
└────────┬─────────┘
         │
         ├─── read/write catalog metadata ──→ PostgreSQL (Neon)
         │                                      (via Drizzle ORM)
         ├─── read/write songsets ──────────→ PostgreSQL (Neon)
         │
         ├─── submit render job ────────────→ PostgreSQL (Neon)
         │            │
         │            └─── SQS enqueue ──→ AWS Lambda (Render Worker)
         │                                  │
         │                                  ├─── fetch songset from DB
         │                                  ├─── download audio/stems from R2
         │                                  ├─── mix audio + render video
         │                                  └─── upload to R2 + update DB
         │
         ├─── download audio/stems ───→ R2 (read-only)
         │
         └─── poll render progress ───→ PostgreSQL (Neon) via SSE
```

---

## Backend Services

The project includes two backend microservices:

### 1. Analysis Service (`services/analysis/`)

FastAPI-based audio analysis service with job queue management.

- **Port:** 8000
- **Purpose:** Audio analysis (tempo, key, beats, sections, embeddings), stem separation, LRC generation
- **Technologies:** FastAPI, PyTorch, allin1, Demucs, audio-separator, Whisper
- **Database:** SQLite (job persistence only, not connected to shared PostgreSQL)
- **Status:** Operational

**Documentation:** [services/analysis/README.md](services/analysis/README.md)

### 2. Render Worker (`services/render-worker/`)

AWS Lambda container that processes render jobs from an SQS queue.

- **Purpose:** Audio mixing (FFmpeg) + lyrics video encoding (Pillow + FFmpeg)
- **Technologies:** psycopg2, boto3, Pillow, FFmpeg
- **Database:** PostgreSQL (Neon) via psycopg2
- **Queue:** AWS SQS
- **Status:** Operational

**Documentation:** [services/render-worker/README.md](services/render-worker/README.md)

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
│   │   ├── db.py                        #    - db init/status/url
│   │   ├── catalog.py                   #    - catalog scrape/list/search/show
│   │   ├── audio.py                     #    - audio download/list/analyze/lrc/align
│   │   └── config.py                    #    - config show/set/path
│   ├── services/                        #    Business logic
│   │   ├── scraper.py                   #    - HTML scraping (sop.org)
│   │   ├── youtube.py                   #    - yt-dlp wrapper
│   │   ├── hasher.py                    #    - SHA-256 hashing
│   │   └── r2.py                        #    - R2 storage client
│   ├── db/                              #    Database layer
│   │   ├── client.py                    #    - DatabaseClient (psycopg3)
│   │   ├── schema.py                    #    - SQL schema DDL
│   │   └── models.py                    #    - Song, Recording dataclasses
│   ├── config.py                        #    TOML config loader
│   └── main.py                          #    Typer app entry point
│
├── src/stream_of_worship/app/           # 🎵 User App Package (DEPRECATED)
│   ├── screens/                           #    TUI screens (Textual)
│   │   ├── generation.py                #    - Transition generator
│   │   ├── browser.py                   #    - Song catalog browser
│   │   └── songset_manager.py           #    - Songset management
│   ├── services/                        #    Business logic
│   │   ├── audio_engine.py             #    - Audio processing
│   │   ├── video_engine.py             #    - Video generation
│   │   ├── asset_cache.py              #    - R2 asset management
│   │   └── turso_client.py             #    - Legacy (unused, kept for compat)
│   ├── db/                              #    Database layer (psycopg3)
│   │   ├── read_client.py              #    - ReadOnlyClient (catalog)
│   │   ├── songset_client.py           #    - SongsetClient (CRUD)
│   │   ├── schema.py                   #    - App-specific schema
│   │   └── user_data_schema.py         #    - Per-user schema
│   ├── config.py                        #    TOML config loader
│   └── main.py                          #    App entry point
│
├── src/stream_of_worship/db/            # Shared database infrastructure
│   ├── connection.py                    #    - ConnectionProvider (psycopg3)
│   └── postgres_schema.py               #    - Unified schema DDL (all components)
│
├── webapp/                              # 🌐 Web App (Next.js)
│   ├── src/
│   │   ├── app/                         #    Next.js App Router pages
│   │   │   ├── api/                     #    API routes
│   │   │   │   ├── songs/               #    Catalog APIs
│   │   │   │   ├── songsets/            #    Songset CRUD
│   │   │   │   ├── render-jobs/         #    Render job management + SSE
│   │   │   │   ├── auth/                #    Better Auth endpoints
│   │   │   │   └── ...
│   │   │   └── ...
│   │   ├── db/                          #    Database client + schema
│   │   │   ├── index.ts                 #    Drizzle + Neon client
│   │   │   └── schema.ts                #    Full schema (15 tables)
│   │   ├── lib/
│   │   │   ├── auth.ts                  #    Better Auth config
│   │   │   └── db/                      #    DB query functions
│   │   └── test/                        #    Test utilities
│   ├── drizzle/                         #    Drizzle migration files
│   ├── drizzle.config.ts                #    Drizzle Kit config
│   └── package.json                     #    Node.js dependencies
│
├── services/analysis/                   # 🚀 Analysis Service (heavy ML)
│   ├── src/sow_analysis/                #    Service package
│   │   ├── main.py                      #    FastAPI app
│   │   ├── config.py                    #    Pydantic settings
│   │   ├── models.py                    #    Pydantic models
│   │   ├── routes/                      #    API endpoints
│   │   │   ├── health.py
│   │   │   └── jobs.py
│   │   ├── storage/                     #    R2 and cache clients
│   │   │   ├── cache.py
│   │   │   ├── db.py                    #    SQLite job persistence
│   │   │   └── r2.py
│   │   └── workers/                     #    Background job processors
│   │       ├── analyzer.py
│   │       ├── lrc.py
│   │       ├── queue.py
│   │       ├── separator.py
│   │       ├── stem_separation.py
│   │       └── separator_wrapper.py
│   ├── docker-compose.yml               #    Docker Compose config
│   ├── Dockerfile                       #    Multi-platform Docker build
│   └── README.md                        #    Service documentation
│
├── services/render-worker/              # ⚡ Render Worker (AWS Lambda)
│   ├── src/sow_render_worker/           #    Worker package
│   │   ├── lambda_handler.py            #    SQS event handler
│   │   ├── config.py                    #    Env var loading
│   │   ├── pipeline.py                  #    5-phase render orchestrator
│   │   ├── audio_engine.py              #    FFmpeg audio mixing
│   │   ├── video_engine.py              #    FFmpeg video encoding
│   │   ├── frame_renderer.py            #    Pillow frame rendering
│   │   ├── db.py                        #    psycopg2 DB operations
│   │   └── r2_client.py                 #    boto3 R2 client
│   ├── tests/                           #    Test suite
│   ├── Dockerfile                       #    Lambda container image
│   └── README.md                        #    Worker documentation
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

| Directory | Package Name | Purpose | Target Users | Database | Deployment |
|-----------|-------------|---------|--------------|----------|------------|
| `src/stream_of_worship/admin/` | `stream-of-worship-admin` | Backend management CLI | Admins / DevOps | PostgreSQL (Neon) + R2 | `uv run --extra admin` |
| `src/stream_of_worship/app/` | `stream-of-worship-app` | End-user TUI (DEPRECATED) | End users (legacy) | PostgreSQL (Neon) + R2 | `uv run --extra app` |
| `services/analysis/` | `sow-analysis` | Audio analysis microservice | Internal service | SQLite (jobs only) + R2 | Docker image |
| `webapp/` | `sow-webapp` | Web application | End users | PostgreSQL (Neon) + R2 | Vercel |
| `services/render-worker/` | `sow-render-worker` | Render processing | Internal service | PostgreSQL (Neon) + R2 | Lambda container |
| `poc/` | N/A (scripts) | Experimental validation | Developers | Local files only | Local scripts |

---

## Development Roadmap

### ✅ Phase 1: Foundation (Complete)
- [x] CLI scaffold (Typer)
- [x] Database schema (PostgreSQL/Neon)
- [x] Configuration (TOML)
- [x] `db` command group (init, status, url)

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

### ✅ Phase 4: Analysis Service (Complete)
- [x] FastAPI service architecture
- [x] Job queue (in-memory + SQLite persistence)
- [x] allin1 worker (tempo, key, beats, sections, embeddings)
- [x] Demucs worker (stem separation)
- [x] Clean vocals pipeline (MelBand Roformer + UVR-De-Echo)
- [x] LRC generation (Whisper + LLM alignment + forced aligner)
- [x] R2 stems upload
- [x] Docker deployment (x86_64 + ARM64 support)
- [x] CLI integration (`audio analyze`, `audio status`)

### ✅ Phase 5: CLI ↔ Service Integration (Complete)
- [x] `audio analyze` command (submit jobs via HTTP)
- [x] `audio status` command (poll job status)
- [x] `audio lrc` command (submit LRC generation)
- [x] `audio align-lrc` command (local forced alignment)
- [x] Retry logic and error handling
- [x] Progress indicators

### ✅ Phase 6: LRC Generation (Complete)
- [x] Whisper transcription worker
- [x] LLM line alignment (OpenAI-compatible API)
- [x] Forced aligner refinement (Qwen3 Forced Aligner)
- [x] LRC file generation and R2 upload
- [x] `lyrics generate` command (via `audio lrc`)
- [x] DashScope Qwen3 ASR integration (optional)

### ✅ Phase 7: Database Migration to PostgreSQL/Neon (Complete)
- [x] Migrated from Turso/SQLite to PostgreSQL (Neon)
- [x] Admin CLI uses psycopg3 with ConnectionProvider
- [x] Web App uses Drizzle ORM with Neon serverless driver
- [x] Render Worker uses psycopg2 with connection string
- [x] User App uses psycopg3 (migration status uncertain)
- [x] Unified schema via `postgres_schema.py`
- [x] Better Auth integration with drizzleAdapter
- [x] pgvector for semantic search (song_embedding, song_line_embedding)
- [x] Full-text search via tsvector with GIN index
- [x] Removed old Turso sync infrastructure

### ✅ Phase 8: Web App (Complete)
- [x] Next.js 16 App Router setup
- [x] Drizzle ORM + Neon serverless driver
- [x] Better Auth with drizzleAdapter
- [x] Song catalog browser with full-text + semantic search
- [x] Songset CRUD with transition configuration
- [x] Render job submission and SSE progress tracking
- [x] Playback controller with synchronized lyrics
- [x] Second-screen projection (Presentation API + Google Cast)
- [x] LRC lyrics review and editing
- [x] Shareable public player links
- [x] User settings and preferences
- [x] Offline caching via Service Worker
- [x] Vercel deployment with environment configuration

### ✅ Phase 9: Render Worker (Complete)
- [x] AWS Lambda container (Docker → ECR → Lambda)
- [x] SQS event-driven job processing
- [x] 5-phase render pipeline (preparing, mixing_audio, rendering_frames, encoding_video, uploading)
- [x] PostgreSQL job status tracking
- [x] Orphan job recovery
- [x] REST mode for local development
- [x] CJK font support for lyrics rendering

### 📋 Future Enhancements
- [ ] User App deprecation and removal
- [ ] Enhanced semantic search with hybrid RRF ranking
- [ ] Template marketplace for video templates
- [ ] Multi-language support beyond Chinese
- [ ] Real-time collaborative worship set editing

**Current Focus:** Web App feature enhancements and stability

---

## Advanced Configuration

### Admin CLI Configuration

Create `~/.config/stream-of-worship-admin/config.toml`:

```toml
[service]
analysis_url = "http://localhost:8000"

[r2]
bucket = "stream-of-worship"
endpoint_url = "https://<account-id>.r2.cloudflarestorage.com"
region = "auto"

[database]
url = "postgresql://sow_admin_rw@ep-xxx-pooler.us-east-1.aws.neon.tech/sow"
```

**Note:** The old `[turso]` config section is silently ignored for backward compatibility.

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

### Web App Configuration

See [webapp/.env.production.example](webapp/.env.production.example) for full documentation of all environment variables.

**Required:**
```bash
SOW_DATABASE_URL=postgresql://...       # Neon PostgreSQL connection string
SOW_R2_BUCKET=stream-of-worship         # R2 bucket name
SOW_R2_ENDPOINT_URL=https://...         # R2 endpoint
SOW_R2_ACCESS_KEY_ID=...                # R2 access key
SOW_R2_SECRET_ACCESS_KEY=...            # R2 secret key
BETTER_AUTH_SECRET=...                  # Auth session signing secret
BETTER_AUTH_URL=https://...             # Auth base URL
NEXT_PUBLIC_BASE_URL=https://...        # Public app URL
```

### Render Worker Configuration

See [services/render-worker/.env.example](services/render-worker/.env.example).

**Required:**
```bash
SOW_DATABASE_URL=postgresql://...       # Neon PostgreSQL connection string
SOW_R2_BUCKET=stream-of-worship         # R2 bucket name
SOW_R2_ENDPOINT_URL=https://...         # R2 endpoint
SOW_R2_ACCESS_KEY_ID=...                # R2 access key
SOW_R2_SECRET_ACCESS_KEY=...            # R2 secret key
SOW_SQS_QUEUE_URL=https://...           # SQS queue URL
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

**Problem:** Forced aligner model not loading
- Verify model is downloaded: `huggingface-cli download Qwen/Qwen3-ForcedAligner-0.6B`
- Check `SOW_FORCED_ALIGNER_MODEL_PATH` environment variable
- Check memory allocation (8GB minimum)

### Database Issues

**Problem:** Admin CLI can't connect to PostgreSQL
- Verify `SOW_DATABASE_PASSWORD` environment variable is set
- Check that the Neon connection URL is correct and not expired
- Ensure Neon project is active and has available connections

**Problem:** Web App can't connect to database
- Verify `SOW_DATABASE_URL` is set correctly in `.env.local`
- Check that the `vector` extension is enabled: `psql "$SOW_DATABASE_URL" -c 'CREATE EXTENSION IF NOT EXISTS vector;'`
- Run `npx drizzle-kit push` to ensure schema is up to date

---

## Resources

- **Design Document:** [specs/worship-music-transition-system-design.md](specs/worship-music-transition-system-design.md)
- **Analysis Service:** [services/analysis/README.md](services/analysis/README.md)
- **Render Worker:** [services/render-worker/README.md](services/render-worker/README.md)
- **Web App:** [webapp/README.md](webapp/README.md)
- **Admin CLI:** [src/stream_of_worship/admin/README.md](src/stream_of_worship/admin/README.md)
- **librosa Documentation:** https://librosa.org/doc/latest/

---

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Run tests: `PYTHONPATH=src uv run --extra app --extra test pytest tests/ -v`
5. Submit a pull request

---

**Last Updated:** 2026-06-20
