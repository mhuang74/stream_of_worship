# Stream of Worship - Current Implementation Status Report

**Generated:** 2026-05-17 (updated 2026-06-08)
**Project:** Stream of Worship - Admin CLI, Analysis Service, Web App & Render Worker
**Repository:** sow_deployment_preps

---

## Executive Summary

The Stream of Worship project consists of an Admin CLI for backend management, an Analysis Service microservice for audio processing, a User App (TUI) for worship leaders, a Next.js Web App for browser-based worship preparation and playback, and an AWS Lambda Render Worker for serverless video/audio rendering. The project has completed all core phases including foundational infrastructure, catalog management, audio download pipeline, Analysis Service implementation, CLI-Service integration, LRC generation, the User App TUI, the Web App, and the Lambda Render Worker migration.

**Overall Progress:** All phases complete (100%)

**Latest Milestone:** Web App Drizzle migration path fixed — fresh Postgres/pgvector migrations now apply through the PR #97 migration chain

**Latest Maintenance:** PR #97 CI migration failure resolved by removing duplicate `songs.search_vector` DDL from migration `0007`, aligning `idx_songs_search_vector` metadata with its GIN index, making the manual active render-job index transaction-safe, and fixing the render page smoke-test Postgres grouping query; `pnpm lint`, `pnpm typecheck`, `pnpm test`, `drizzle-kit migrate`, and `pnpm test:postgres-smoke` pass locally.

---

## Phase-by-Phase Implementation Status

### Phase 1: Foundation ✅ COMPLETE

**Status:** Fully implemented and tested (commit `8675fff`)

**Components:**
- Database client (`src/stream_of_worship/admin/db/client.py`)
  - Turso/libSQL integration
  - Connection pooling and session management
  - CRUD operations for all entities
- Configuration system (`src/stream_of_worship/admin/config.py`)
  - Environment-based configuration
  - Turso credential management
- Data models (`src/stream_of_worship/admin/db/models.py`)
  - SQLModel-based ORM models
  - Catalog, Audio, Analysis, LRC, SyncState tables
- CLI entry point (`src/stream_of_worship/admin/main.py`)
  - Typer-based CLI framework
  - Database command group

**Tests:** `tests/admin/test_db.py`

---

### Phase 2: Catalog Management ✅ COMPLETE

**Status:** Fully implemented and tested (commit `1685d69`)

**Components:**
- Scraper service (`src/stream_of_worship/admin/services/scraper.py`)
  - sop.org Chinese lyrics scraper
  - Retry logic with exponential backoff
  - Artist and song listing extraction
- Catalog commands (`src/stream_of_worship/admin/commands/catalog.py`)
  - `catalog scrape` - Scrape song catalog from sop.org
  - `catalog list` - List catalog entries with filters
  - `catalog show <song_id>` - Show song details with recording panel (if audio exists)
  - `catalog search` - Search catalog by keyword

**Tests:**
- `tests/admin/test_scraper.py`
- `tests/admin/test_catalog_commands.py`

---

### Phase 3: Audio Download ✅ COMPLETE

**Status:** Fully implemented and tested (commit `a2690b2`)

**Components:**
- YouTube downloader service (`src/stream_of_worship/admin/services/youtube.py`)
  - yt-dlp integration for audio extraction
  - Search query builder from song metadata
  - Progress tracking and error handling
- R2 cloud storage client (`src/stream_of_worship/admin/services/r2.py`)
  - boto3 S3-compatible client for Cloudflare R2
  - Upload/download with hash-based organization
  - Environment-based credentials (`SOW_R2_ACCESS_KEY_ID`, `SOW_R2_SECRET_ACCESS_KEY`)
- Audio hasher (`src/stream_of_worship/admin/services/hasher.py`)
  - SHA-256 content hashing
  - 12-character hash prefix generation
  - Deduplication support
- Audio commands (`src/stream_of_worship/admin/commands/audio.py`)
  - `audio download <song_id>` - Download from YouTube and upload to R2
  - `audio list` - List downloaded recordings (shows Song ID as primary column)
  - `audio show <song_id>` - Show recording details by song ID
  - `audio analyze <song_id>` - Submit recording for analysis by song ID
  - `audio status` - Check analysis status (shows Song ID as primary column)

**Download Pipeline:**
```
Song lookup → YouTube search → Download → SHA-256 hash → R2 upload → Recording insert → Cleanup
```

**Tests:**
- `tests/admin/services/test_hasher.py` - 10 tests
- `tests/admin/services/test_youtube.py` - 13 tests
- `tests/admin/services/test_r2.py` - 10 tests
- `tests/admin/commands/test_audio_commands.py` - 24 tests

---

### Phase 4: Analysis Service ✅ COMPLETE

**Status:** Fully implemented and tested (commit `bdd01d3`)

**Architecture:** Separate microservice package (`sow_analysis`) at `services/analysis/`

**Components:**
- FastAPI application (`services/analysis/src/sow_analysis/main.py`)
  - CORS configuration
  - Job queue initialization
  - Health and jobs route registration
- Configuration (`services/analysis/src/sow_analysis/config.py`)
  - R2 credentials from `SOW_R2_*` environment variables
  - Service settings (cache directory, concurrent workers)
- Data models (`services/analysis/src/sow_analysis/models.py`)
  - Pydantic models for jobs, requests, responses
  - Job status tracking (pending, processing, completed, failed)
- API routes (`services/analysis/src/sow_analysis/routes/`)
  - `GET /api/v1/health` - Service health check
  - `POST /api/v1/jobs/analyze` - Submit analysis job
  - `GET /api/v1/jobs/{job_id}` - Get job status
  - `POST /api/v1/jobs/lrc` - Submit LRC generation job (stub for Phase 6)
- Background workers (`services/analysis/src/sow_analysis/workers/`)
  - `analyzer.py` - allin1 wrapper (tempo, key, beats, sections, embeddings)
  - `separator.py` - Demucs wrapper (vocal/drum/bass/other stem separation)
  - `lrc.py` - LRC generation stub (raises NotImplementedError)
  - `queue.py` - In-memory job queue with asyncio concurrency
- Storage layer (`services/analysis/src/sow_analysis/storage/`)
  - `r2.py` - Async R2 client with `run_in_executor` for boto3 calls
  - `cache.py` - Content-hash based result caching

**Docker Infrastructure:**
- `Dockerfile` - Multi-stage build with platform-conditional PyTorch/NATTEN install
  - x86_64: CPU-only PyTorch (`torch==2.4.1+cpu`, `NATTEN_IS_FOR_PYPI=1`)
  - ARM64: Standard PyTorch (`torch==2.4.1`)
- `docker-compose.yml` - Service orchestration with optional GPU support

**Analysis Pipeline:**
```
HTTP Request → Job Queue → Worker Pool → allin1/Demucs → R2 Upload → Database Update
```

**Tests:**
- `tests/services/analysis/test_models.py` - Pydantic model validation
- `tests/services/analysis/test_config.py` - Configuration loading
- `tests/services/analysis/test_queue.py` - Job queue operations
- `tests/services/analysis/test_r2.py` - R2 client (mocked boto3)
- `tests/services/analysis/test_cache.py` - Cache operations
- `tests/services/analysis/test_api.py` - FastAPI endpoint integration
- **Total: 54 tests**

**Key Dependencies:**
- fastapi, uvicorn - Web framework
- torch==2.4.1, torchaudio, torchvision - PyTorch (platform-conditional)
- natten==0.17.1 - Neighborhood attention (compiled from source)
- allin1 - Deep learning music analysis
- demucs - Source separation
- boto3 - R2 storage client

---

---

## Analysis Service Deployment

### Docker Setup

```bash
# Navigate to service directory
cd services/analysis

# Set environment variables
export SOW_R2_ACCESS_KEY_ID="your-key"
export SOW_R2_SECRET_ACCESS_KEY="your-secret"

# Build the image (takes 10-20 minutes first time)
docker compose build

# Start the service
docker compose up -d

# Check health
curl http://localhost:8000/api/v1/health
```

### API Endpoints

- `GET /api/v1/health` - Service health check
- `POST /api/v1/jobs/analyze` - Submit audio analysis job
- `GET /api/v1/jobs/{job_id}` - Get job status and results
- `POST /api/v1/jobs/lrc` - Submit LRC generation job (stub, Phase 6)

### Platform-Specific Builds

The Dockerfile automatically detects the target platform:

**x86_64 (linux/amd64):**
- PyTorch CPU-only from `https://download.pytorch.org/whl/cpu`
- NATTEN compiled with `NATTEN_IS_FOR_PYPI=1` flag
- Suitable for cloud servers without GPU

**ARM64 (linux/arm64):**
- Standard PyTorch from PyPI
- NATTEN compiled without special flags
- Suitable for M-series Macs

### GPU Support

For GPU acceleration, ensure:
1. `nvidia-container-toolkit` is installed
2. Docker Compose `deploy.resources` block is uncommented
3. CUDA-compatible PyTorch is installed (modify Dockerfile)

---

## Lambda Render Worker

### Architecture

The render pipeline has been migrated from in-process Vercel execution to an AWS Lambda-based worker architecture. The Next.js web app enqueues render jobs to SQS instead of running `executeRenderPipeline()` via `after()`. A Python Lambda container processes jobs, achieving feature parity with the previous Node.js pipeline while eliminating heavy native dependencies (canvas, ffmpeg-static, fastembed) from the Vercel deployment.

**Render Job Flow:**
```
Next.js POST /api/render-jobs → Create DB job (queued) → SQS sendMessage →
Lambda handler picks up message → execute_render_pipeline() →
Audio mixing + Video encoding + R2 upload → Update DB job (completed/failed)
```

### Components (services/render-worker/)

| Module | File | Description |
|--------|------|-------------|
| Config | `config.py` | Environment variable loading with validation |
| Lambda Handler | `lambda_handler.py` | SQS event parsing, job dispatch, error handling |
| Pipeline | `pipeline.py` | Main orchestrator: 5-phase render with cancellation, progress |
| Audio Engine | `audio_engine.py` | FFmpeg audio mixing, gap/crossfade, loudnorm |
| Video Engine | `video_engine.py` | FFmpeg video encoding from Pillow-rendered frames |
| Frame Renderer | `frame_renderer.py` | Pillow-based lyrics frame rendering with CJK fonts |
| Chapters | `chapters.py` | Chapter manifest generation, FFmpeg metadata injection |
| LRC Parser | `lrc_parser.py` | LRC lyric timestamp parsing and global timeline |
| R2 Client | `r2_client.py` | boto3 S3-compatible client for Cloudflare R2 |
| Asset Fetcher | `asset_fetcher.py` | R2 download with local filesystem cache |
| Uploader | `uploader.py` | R2 upload of MP3/MP4/chapters artifacts |
| DB | `db.py` | psycopg2 job status CRUD (start, progress, complete, fail) |

### Key Design Decisions

- **Python 3.11** on Lambda container image (`public.ecr.aws/lambda/python:3.11`)
- **boto3** for R2 (S3-compatible) and SQS access
- **psycopg2** for Neon PostgreSQL job status updates
- **Pillow** for frame rendering (replaces node-canvas)
- **subprocess** for FFmpeg commands (replaces fluent-ffmpeg)
- **CJK fonts** via `google-noto-sans-cjk-fonts` system package
- **SQS DLQ** with 3-retry maxReceiveCount and 900s visibility timeout
- **Batch size 1** — one render per Lambda invocation

### Web App Changes

The Next.js web app was updated to support the Lambda worker architecture:

- **SQS Integration**: `webapp/src/lib/sqs/client.ts` — SQSClient wrapper sending `{ jobId, songsetId, userId }` as JSON body
- **Render Jobs Route**: `POST /api/render-jobs` now enqueues to SQS instead of calling `after(() => executeRenderPipeline(...))`
- **Removed Dependencies**: canvas, ffmpeg-static, fluent-ffmpeg, fastembed removed from `webapp/package.json`
- **Hybrid Search**: Replaced runtime fastembed with Postgres tsvector full-text search + pre-computed embedding lookup
- **vercel.json**: `maxDuration` reduced from 800 to 60; `fluid: true` removed from render routes
- **next.config.ts**: Heavy packages removed from `serverExternalPackages`

### CI/CD

- **CI Workflow** (`.github/workflows/ci.yml`): Runs on PR to main — pnpm lint + test for webapp, pytest for render-worker
- **Deploy Workflow** (`.github/workflows/deploy.yml`): Runs on push to main — Vercel deploy for webapp, private ECR build+push + Lambda update for render-worker

### Tests

| Component | Test File | Tests |
|-----------|-----------|-------|
| Config | `test_config.py` | Environment variable validation |
| Lambda Handler | `test_lambda_handler.py` | SQS event parsing, success/failure paths |
| Pipeline | `test_pipeline.py` | Pipeline flow, cancellation, error propagation |
| Audio Engine | `test_audio_engine.py` | Gap calculation, FFmpeg filter complex |
| Video Engine | `test_video_engine.py` | Codec args, chapter injection |
| Frame Renderer | `test_frame_renderer.py` | Template definitions, frame rendering |
| Chapters | `test_chapters.py` | Manifest generation, FFmpeg metadata |
| LRC Parser | `test_lrc_parser.py` | Parse, global timeline, duration estimation |
| R2 Client | `test_r2_client.py` | Signed URL generation, file existence |
| Asset Fetcher | `test_asset_fetcher.py` | Caching logic, download |
| Uploader | `test_uploader.py` | Upload artifacts, content type mapping |
| DB | `test_db.py` | Job status transitions, orphan recovery |
| Docker | `test_docker.py` | Image build and handler import smoke test |

**Render Worker Total: ~100+ tests**

---

**Status:** Fully implemented and tested (commit `cb96e17`)

**Components:**
- AnalysisClient service (`src/stream_of_worship/admin/services/analysis.py`)
  - HTTP client for FastAPI analysis service
  - `AnalysisServiceError` exception with status_code support
  - `AnalysisResult` dataclass with all analysis fields
  - `JobInfo` dataclass for job status tracking
  - Methods: `health_check()`, `submit_analysis()`, `get_job()`, `wait_for_completion()`
- Audio commands integration (`src/stream_of_worship/admin/commands/audio.py`)
  - `audio analyze <song_id>` - Submit recording for analysis by song_id
    - `--force` flag for re-analysis
    - `--no-stems` to skip stem separation
    - `--wait` with Rich progress display (spinner, bar, stage)
    - Handles already-completed and already-processing states
  - `audio status` - Check analysis status
    - Query specific job by ID
    - List pending recordings when no ID provided (shows Song ID as primary column)
    - Color-coded status display
  - `audio show <song_id>` - Show recording details by song_id
  - `audio list` - List recordings with Song ID as primary column
- Database updates (`src/stream_of_worship/admin/db/client.py`)
  - `update_recording_analysis()` with `r2_stems_url` parameter
  - Status tracking for analysis and LRC jobs

**Tests:**
- `tests/admin/test_analysis_client.py` - 28 tests
- `tests/admin/test_audio_commands.py` - 27 tests (analyze/status commands)
- **Total: 55 new tests**

---

### Phase 6: LRC Generation ✅ COMPLETE

**Status:** Fully implemented and tested (commit `f858da4`)

**Components:**
- LRC worker (`services/analysis/src/sow_analysis/workers/lrc.py`)
  - Whisper transcription with word-level timestamps
  - OpenAI-compatible LLM alignment (OpenRouter/nano-gpt/synthetic.new/OpenAI)
  - Retry logic with configurable max attempts
  - Error handling: `LRCWorkerError`, `LLMConfigError`, `WhisperTranscriptionError`, `LLMAlignmentError`
  - Pipeline: Whisper → LLM alignment → LRC file generation
- Extended models (`services/analysis/src/sow_analysis/models.py`)
  - `LrcOptions` with `llm_model`, `use_vocals_stem`, `language`, `force` fields
  - Support for custom LLM providers via `SOW_LLM_BASE_URL`
- Configuration updates (`services/analysis/src/sow_analysis/config.py`)
  - `SOW_LLM_API_KEY` - API key for OpenAI-compatible services
  - `SOW_LLM_BASE_URL` - Base URL (default: OpenRouter)
  - `WHISPER_DEVICE` - CPU or CUDA device selection
  - `WHISPER_CACHE_DIR` - Model cache directory
- Queue integration (`services/analysis/src/sow_analysis/workers/queue.py`)
  - Full `_process_lrc_job()` implementation
  - Downloads audio from R2
  - Optional vocals stem usage for cleaner transcription
  - Content-hash based caching (skip if cached unless `force=True`)
  - Uploads generated LRC files to R2
- Docker updates (`services/analysis/docker-compose.yml`)
  - Added `SOW_LLM_API_KEY`, `SOW_LLM_BASE_URL`, `WHISPER_DEVICE` environment variables

**LRC Generation Pipeline:**
```
HTTP Request → Download Audio → (Optional) Use Vocals Stem →
Whisper Transcription → LLM Alignment → Generate LRC →
Upload to R2 → Cache Result
```

**Dependencies Added:**
- `openai-whisper>=20231117` - Audio transcription with word timestamps
- `openai>=1.10.0` - OpenAI client (works with OpenRouter and other providers)

**Tests:**
- `tests/services/analysis/test_lrc_worker.py` - 31 comprehensive tests
  - LRC line formatting (5 tests)
  - Whisper word handling (1 test)
  - Prompt building (2 tests)
  - LLM response parsing (7 tests)
  - LRC file writing (3 tests)
  - Whisper transcription (2 tests)
  - LLM alignment (3 tests)
  - Full pipeline integration (1 test)
  - Queue processing (5 tests)
  - LrcOptions model (2 tests)
- Updated `tests/services/analysis/test_queue.py` - 2 tests updated for new behavior

**Key Features:**
- Multi-provider LLM support (OpenRouter, nano-gpt.com, synthetic.new, OpenAI)
- Automatic vocals stem detection and usage
- Word-level timestamp precision from Whisper
- Intelligent lyrics alignment preserving original text
- Markdown code block stripping from LLM responses
- Configurable retry logic for API failures
- Content-hash based result caching

---

### Phase 7: Turso Sync ✅ COMPLETE

**Status:** Fully implemented and tested (commit `ce5bbc4`)

**Components:**
- SyncService (`src/stream_of_worship/admin/services/sync.py`)
  - High-level sync orchestration with status checking
  - Configuration validation (libsql, database, URL, token)
  - Error handling: `SyncConfigError`, `SyncNetworkError`
  - URL masking for security
- DatabaseClient enhancements (`src/stream_of_worship/admin/db/client.py`)
  - Conditional libsql backend for Turso support
  - Automatic fallback to sqlite3 when not configured
  - `is_turso_enabled` property
  - `sync()` method for embedded replica sync
  - `update_sync_metadata()` for sync state tracking
- Sync metadata tracking
  - `last_sync_at` - ISO timestamp of last sync
  - `sync_version` - Schema version for sync protocol
  - `local_device_id` - Unique device identifier
- CLI commands (`src/stream_of_worship/admin/commands/db.py`)
  - `db sync` - Execute cloud sync with `--force` flag
  - Enhanced `db status` - Displays sync configuration state

**Configuration:**
```toml
[database]
path = "/path/to/sow.db"

[turso]
database_url = "libsql://your-db.turso.io"
```
```bash
export SOW_TURSO_TOKEN="your-auth-token"
```

**Security:**
- Auth token from `SOW_TURSO_TOKEN` environment variable (not config file)
- URL masking in status display prevents token leakage

**Tests:**
- `tests/admin/services/test_sync.py` - 28 tests
- `tests/admin/commands/test_db_commands.py` - 24 tests
- **Total: 52 new tests**

**Dependencies:**
- Optional `libsql>=0.1.0` via `uv add --extra turso libsql`

**Backward Compatibility:**
- Zero breaking changes - operates in local-only mode if libsql not installed
- All existing functionality preserved

---

### Phase 8: User App (TUI) ✅ COMPLETE

**Status:** Fully implemented (commit `b82fc0d`)

**Architecture:** Textual-based TUI application in `src/stream_of_worship/app/`

**Components:**
- **Configuration** (`src/stream_of_worship/app/config.py`)
  - `AppConfig` extends `AdminConfig` with app-specific settings
  - Cache directory, output directory, default gap beats, video template
  - TOML-based configuration with `[app]` section

- **Database Layer** (`src/stream_of_worship/app/db/`)
  - `schema.py` - SQL DDL for `songsets` and `songset_items` tables
  - `models.py` - `Songset` and `SongsetItem` dataclasses with `from_row()` pattern
  - `read_client.py` - `ReadOnlyClient` for read-only access to admin tables (songs, recordings)
  - `songset_client.py` - `SongsetClient` with full CRUD and transaction support

- **Services** (`src/stream_of_worship/app/services/`)
  - `catalog.py` - `CatalogService` with `SongWithRecording` dataclass for browsing
  - `asset_cache.py` - `AssetCache` for R2 downloads with local caching
  - `playback.py` - `PlaybackService` using miniaudio for audio playback
  - `audio_engine.py` - `AudioEngine` for gap transition generation (ported from POC)
  - `video_engine.py` - `VideoEngine` with 3 templates (dark, gradient_warm, gradient_blue)
  - `export.py` - `ExportService` with progress tracking and cancellation support

- **State Management** (`src/stream_of_worship/app/state.py`)
  - `AppState` with reactive properties and listener pattern
  - `AppScreen` enum for navigation
  - Observable pattern for UI updates

- **TUI Screens** (`src/stream_of_worship/app/screens/`)
  - `songset_list.py` - List and manage songsets
  - `browse.py` - Browse catalog and add songs to songsets
  - `songset_editor.py` - Edit songset (reorder, remove, edit transitions)
  - `transition_detail.py` - Fine-tune transition parameters (gap, crossfade, key shift)
  - `export_progress.py` - Show export progress with cancel option
  - `settings.py` - Edit application settings
  - `app.tcss` - Textual CSS stylesheet

- **Main Application** (`src/stream_of_worship/app/app.py`, `main.py`)
  - `SowApp` - Main Textual App class with service wiring
  - Navigation stack with `navigate_to()` and `navigate_back()`
  - CLI entry point with `sow-app` command

**User App Workflow:**
```
sow-app → Songset List → Browse Songs → Songset Editor → Export Progress
                    ↓           ↓              ↓
               Settings    Add Songs   Transition Detail
```

**Export Pipeline:**
```
Songset + Items → Asset Cache (R2 download) → Audio Engine (gap transitions) →
Video Engine (lyrics video) → Export audio + video files
```

**Key Dependencies:**
- `textual>=0.47.0` - TUI framework
- `pydub>=0.25.0` - Audio manipulation
- `miniaudio>=1.59.0` - Audio playback
- `pillow>=10.0.0` - Image/frame generation
- `requests>=2.31.0` - HTTP client for R2

---

## File Structure Summary

```
sow_cli_admin/
├── src/stream_of_worship/
│   ├── admin/                        # 🖥️ Admin CLI (COMPLETE)
│   │   ├── main.py                  # CLI entry point
│   │   ├── config.py                # Configuration loader
│   │   ├── db/
│   │   │   ├── client.py            # Database client
│   │   │   ├── models.py            # Pydantic models
│   │   │   └── schema.py            # SQL schema
│   │   ├── commands/
│   │   │   ├── db.py                # db init/status/reset
│   │   │   ├── catalog.py           # catalog scrape/list/search
│   │   │   └── audio.py             # audio download/list/show
│   │   └── services/
│   │       ├── scraper.py           # sop.org scraper
│   │       ├── youtube.py           # yt-dlp wrapper
│   │       ├── hasher.py            # SHA-256 hashing
│   │       ├── r2.py                # R2 storage client
│   │       └── analysis.py          # Analysis Service HTTP client (Phase 5)
│   └── app/                          # 🎵 User App (COMPLETE - Phase 8)
│       ├── main.py                  # TUI entry point
│       ├── config.py                # AppConfig
│       ├── state.py                 # Reactive app state
│       ├── app.py                   # Main Textual App
│       ├── db/
│       │   ├── schema.py            # songsets/songset_items DDL
│       │   ├── models.py            # Songset/SongsetItem models
│       │   ├── read_client.py       # Read-only admin tables
│       │   └── songset_client.py    # Songset CRUD
│       ├── services/
│       │   ├── catalog.py           # Catalog browsing
│       │   ├── asset_cache.py       # R2 download cache
│       │   ├── playback.py          # Audio playback
│       │   ├── audio_engine.py      # Gap transitions
│       │   ├── video_engine.py      # Lyrics video generation
│       │   └── export.py            # Export orchestrator
│       └── screens/
│           ├── songset_list.py      # List songsets
│           ├── browse.py            # Browse catalog
│           ├── songset_editor.py    # Edit songset
│           ├── transition_detail.py # Transition tuning
│           ├── export_progress.py   # Export progress
│           ├── settings.py          # App settings
│           └── app.tcss             # Textual CSS
│
├── services/analysis/                # 🚀 Analysis Service (COMPLETE)
│   ├── src/sow_analysis/
│   │   ├── main.py                  # FastAPI app
│   │   ├── config.py                # Service config
│   │   ├── models.py                # Request/response schemas
│   │   ├── routes/
│   │   │   ├── health.py            # GET /health
│   │   │   └── jobs.py              # POST/GET /jobs/*
│   │   ├── workers/
│   │   │   ├── analyzer.py          # allin1 worker
│   │   │   ├── separator.py         # Demucs worker
│   │   │   ├── lrc.py               # LRC worker (Whisper + LLM)
│   │   │   └── queue.py             # Job queue
│   │   └── storage/
│   │       ├── r2.py                # Async R2 client
│   │       └── cache.py             # Result cache
│   ├── Dockerfile                    # Multi-stage build
│   ├── docker-compose.yml            # Service orchestration
│   └── pyproject.toml                # Service dependencies
│
├── services/render-worker/            # 🎬 Lambda Render Worker (COMPLETE)
│   ├── src/sow_render_worker/
│   │   ├── lambda_handler.py        # SQS event handler
│   │   ├── config.py                # Env var config
│   │   ├── pipeline.py              # Render orchestrator
│   │   ├── audio_engine.py          # FFmpeg audio mixing
│   │   ├── video_engine.py          # FFmpeg video encoding
│   │   ├── frame_renderer.py        # Pillow frame rendering
│   │   ├── chapters.py              # Chapter manifest
│   │   ├── lrc_parser.py            # LRC timestamp parser
│   │   ├── r2_client.py             # R2 S3 client
│   │   ├── asset_fetcher.py         # R2 download + cache
│   │   ├── uploader.py              # R2 upload
│   │   └── db.py                    # Job status CRUD
│   ├── tests/                        # Worker tests
│   ├── Dockerfile                    # Lambda container image
│   ├── docker-compose.yml            # Local testing
│   ├── requirements.txt              # Python dependencies
│   └── pyproject.toml                # Project metadata
│
├── tests/
│   ├── admin/                        # CLI tests
│   │   ├── commands/
│   │   │   ├── test_catalog_commands.py
│   │   │   └── test_audio_commands.py
│   │   ├── services/
│   │   │   ├── test_scraper.py
│   │   │   ├── test_youtube.py
│   │   │   ├── test_hasher.py
│   │   │   └── test_r2.py
│   │   └── db/
│   │       └── test_client.py
│   ├── services/analysis/            # Service tests
│   │   ├── test_models.py
│   │   ├── test_config.py
│   │   ├── test_queue.py
│   │   ├── test_r2.py
│   │   ├── test_cache.py
│   │   ├── test_api.py
│   │   └── test_lrc_worker.py
│   └── app/                          # User App tests
│       ├── test_config.py
│       ├── test_integration.py
│       ├── db/
│       │   ├── test_schema.py
│       │   ├── test_models.py
│       │   ├── test_read_client.py
│       │   └── test_songset_client.py
│       └── services/
│           ├── test_catalog.py
│           ├── test_asset_cache.py
│           ├── test_audio_engine.py
│           ├── test_video_engine.py
│           ├── test_playback.py
│           └── test_export.py
│
├── poc/                              # 🧪 POC Scripts (ARCHIVED)
│   ├── docker/
│   ├── poc_analysis_allinone.py
│   └── transition_builder_v2/
│
├── webapp/                            # 🌐 Next.js Web App (COMPLETE)
│   ├── src/
│   │   ├── app/                      # Next.js App Router pages
│   │   ├── lib/                      # Shared libraries
│   │   │   ├── db/                   # Drizzle ORM + search
│   │   │   ├── render/              # (deprecated, moved to render-worker)
│   │   │   ├── sqs/                  # SQS client
│   │   │   └── r2/                   # R2 client
│   │   └── test/                     # Test files
│   ├── drizzle/                      # DB migrations
│   ├── package.json
│   ├── next.config.ts
│   └── vercel.json
│
├── .github/workflows/                 # CI/CD
│   ├── ci.yml                        # PR checks
│   └── deploy.yml                    # Vercel + Lambda deploy
│
├── report/
│   ├── current_impl_status.md        # This file
│   └── phase4_detailed_impl_plan.md
│
└── specs/
    └── sow_admin_design.md           # System design spec
```

---

## Test Coverage Status

### Admin CLI Tests

| Component | Test File | Tests | Status |
|-----------|-----------|-------|--------|
| Database Client | `tests/admin/db/test_client.py` | ~40 | ✅ Complete |
| Scraper Service | `tests/admin/services/test_scraper.py` | 22 | ✅ Complete |
| YouTube Service | `tests/admin/services/test_youtube.py` | 13 | ✅ Complete |
| Hasher Service | `tests/admin/services/test_hasher.py` | 10 | ✅ Complete |
| R2 Client | `tests/admin/services/test_r2.py` | 10 | ✅ Complete |
| Catalog Commands | `tests/admin/commands/test_catalog_commands.py` | 22 | ✅ Complete |
| Audio Commands | `tests/admin/commands/test_audio_commands.py` | 51 | ✅ Complete |
| Analysis Client | `tests/admin/test_analysis_client.py` | 28 | ✅ Complete |
| Sync Service | `tests/admin/services/test_sync.py` | 28 | ✅ Complete |
| DB Commands | `tests/admin/commands/test_db_commands.py` | 24 | ✅ Complete |

**Admin CLI Total: 262 tests**

### Analysis Service Tests

| Component | Test File | Tests | Status |
|-----------|-----------|-------|--------|
| Models | `tests/services/analysis/test_models.py` | ~12 | ✅ Complete |
| Config | `tests/services/analysis/test_config.py` | ~8 | ✅ Complete |
| Job Queue | `tests/services/analysis/test_queue.py` | ~15 | ✅ Complete |
| R2 Client | `tests/services/analysis/test_r2.py` | ~8 | ✅ Complete |
| Cache | `tests/services/analysis/test_cache.py` | ~6 | ✅ Complete |
| API Routes | `tests/services/analysis/test_api.py` | ~5 | ✅ Complete |
| LRC Worker | `tests/services/analysis/test_lrc_worker.py` | 31 | ✅ Complete |

**Analysis Service Total: 85 tests**

### User App (TUI) Tests

| Component | Test File | Tests | Status |
|-----------|-----------|-------|--------|
| Config | `tests/app/test_config.py` | ~8 | ✅ Complete |
| DB Schema | `tests/app/db/test_schema.py` | ~6 | ✅ Complete |
| DB Models | `tests/app/db/test_models.py` | ~10 | ✅ Complete |
| Read Client | `tests/app/db/test_read_client.py` | ~12 | ✅ Complete |
| Songset Client | `tests/app/db/test_songset_client.py` | ~18 | ✅ Complete |
| Catalog Service | `tests/app/services/test_catalog.py` | ~10 | ✅ Complete |
| Asset Cache | `tests/app/services/test_asset_cache.py` | ~12 | ✅ Complete |
| Audio Engine | `tests/app/services/test_audio_engine.py` | ~15 | ✅ Complete |
| Video Engine | `tests/app/services/test_video_engine.py` | ~10 | ✅ Complete |
| Playback Service | `tests/app/services/test_playback.py` | ~8 | ✅ Complete |
| Export Service | `tests/app/services/test_export.py` | ~10 | ✅ Complete |
| Integration | `tests/app/test_integration.py` | ~5 | ✅ Complete |

**User App Total: ~124 tests**

### Render Worker Tests

| Component | Test File | Status |
|-----------|-----------|--------|
| Config | `test_config.py` | ✅ Complete |
| Lambda Handler | `test_lambda_handler.py` | ✅ Complete |
| Pipeline | `test_pipeline.py` | ✅ Complete |
| Audio Engine | `test_audio_engine.py` | ✅ Complete |
| Video Engine | `test_video_engine.py` | ✅ Complete |
| Frame Renderer | `test_frame_renderer.py` | ✅ Complete |
| Chapters | `test_chapters.py` | ✅ Complete |
| LRC Parser | `test_lrc_parser.py` | ✅ Complete |
| R2 Client | `test_r2_client.py` | ✅ Complete |
| Asset Fetcher | `test_asset_fetcher.py` | ✅ Complete |
| Uploader | `test_uploader.py` | ✅ Complete |
| DB | `test_db.py` | ✅ Complete |
| Docker | `test_docker.py` | ✅ Complete |

**Render Worker Total: ~100+ tests**

### Web App Tests

| Component | Test Directory | Status |
|-----------|---------------|--------|
| API Routes | `src/test/api/` | ✅ Complete |
| DB Search | `src/test/lib/db/` | ✅ Complete |
| SQS Client | `src/test/lib/sqs/` | ✅ Complete |
| Deployment Config | `src/test/deployment/` | ✅ Complete |
| CI/CD Workflows | `src/test/deployment/` | ✅ Complete |

**Web App Total: ~200+ tests**

**Combined Total: ~900+ tests (all passing)**

---

**Test Execution:**
```bash
# Run all admin CLI tests
PYTHONPATH=src uv run --extra admin --extra test pytest tests/admin/ -v

# Run analysis service tests
pytest tests/services/analysis/ -v

# Run all tests with coverage
pytest tests/ --cov=src --cov=services/analysis/src --cov-report=html
```

---

## Key Technologies Used

### Admin CLI

| Layer | Technology |
|-------|------------|
| CLI Framework | Typer |
| Database | SQLite (local) with Turso sync support |
| ORM | Pydantic models |
| HTTP Client | requests |
| Scraping | BeautifulSoup4 |
| YouTube Download | yt-dlp |
| Cloud Storage | boto3 (S3-compatible for R2) |
| Hashing | hashlib (SHA-256) |
| Testing | pytest |
| Packaging | uv |

### Analysis Service

| Layer | Technology |
|-------|------------|
| Web Framework | FastAPI + uvicorn |
| Audio Analysis | allin1 (deep learning) |
| Stem Separation | Demucs |
| LRC Generation | Whisper + OpenAI-compatible LLM |
| Deep Learning | PyTorch 2.4.1 (platform-conditional) |
| Attention | NATTEN 0.17.1 (compiled from source) |
| Cloud Storage | boto3 (async with run_in_executor) |
| Job Queue | In-memory asyncio (Redis planned) |
| Caching | Content-hash based |
| Testing | pytest, pytest-asyncio |
| Containerization | Docker (multi-stage builds) |

### Render Worker (Lambda)

| Layer | Technology |
|-------|------------|
| Runtime | AWS Lambda (Python 3.11 container) |
| Job Queue | AWS SQS with DLQ |
| Audio Processing | FFmpeg (subprocess) |
| Video Encoding | FFmpeg + Pillow (frame rendering) |
| Cloud Storage | boto3 (R2 S3-compatible) |
| Database | psycopg2 (Neon PostgreSQL) |
| CJK Fonts | google-noto-sans-cjk-fonts |
| Containerization | Docker (Lambda container image) |
| Testing | pytest, pytest-mock |

### Web App (Next.js)

| Layer | Technology |
|-------|------------|
| Framework | Next.js 16 (App Router) |
| ORM | Drizzle ORM + PostgreSQL (Neon serverless) |
| Auth | Better Auth |
| Storage | Cloudflare R2 |
| Job Queue | AWS SQS (@aws-sdk/client-sqs) |
| Search | Postgres tsvector full-text + pre-computed embeddings |
| Deployment | Vercel (web) + AWS Lambda (render worker) |
| Testing | Vitest |

#### Database Migrations (CI/CD)

**Problem:** `drizzle-kit push --force` and `drizzle-kit migrate` both fail in CI:
- `push --force` prompts for TTY when encountering destructive schema changes (e.g., adding unique constraint to populated table)
- `migrate` hangs indefinitely with `@neondatabase/serverless` driver (websocket connection issues)

**Solution:**
1. Custom migration script `webapp/scripts/migrate.ts` using `drizzle-orm/neon-http/migrator` (works with Neon serverless)
2. CI runs `npx tsx scripts/migrate.ts` instead of `drizzle-kit migrate`
3. One-time setup script `webapp/scripts/mark-migrations-applied.ts` to populate `drizzle.__drizzle_migrations` table when migrating from `push`-based to `migrate`-based workflow

**Developer Workflow:**
- Local dev: `npx drizzle-kit push` for rapid prototyping
- Before committing schema changes: `npx drizzle-kit generate` → commit migration files in `webapp/drizzle/`
- CI: `npx tsx scripts/migrate.ts` applies pending migrations non-interactively

---

## Next Steps / Pending Work

**All phases are complete!** There are no pending implementation items.

The system now fully supports:
- Catalog management via Admin CLI (`sow-admin catalog` commands)
- Audio download and analysis via Analysis Service (`sow-admin audio` commands)
- Interactive songset building via User App TUI (`sow-app`)
- Browser-based songset builder, render pipeline, worship playback, and sharing via Web App (`webapp/`)
- Serverless render processing via AWS Lambda Render Worker (`services/render-worker/`)

### Future Enhancements (Optional)

Potential future improvements (not required for core functionality):

- **Turso Sync** - Bidirectional cloud synchronization for multi-device support
- **Additional Video Templates** - More visual styles for lyrics videos
- **GPU Acceleration** - GPU-enabled Lambda for faster video encoding

### Phase 9: Web App (Completed)

A Next.js 16 (App Router) web application providing phone-first worship preparation and playback:

- **Authentication**: Email/password login and registration via Better Auth
- **Songset Management**: Create, edit, reorder songs with drag-and-drop
- **Render Pipeline**: Jobs enqueued to SQS, processed by AWS Lambda Python container
- **Worship Playback**: Controller player with Presentation API for second-screen lyrics projection
- **Offline Caching**: Service Worker with Cache Storage API for artifact persistence
- **Hybrid Search**: Postgres tsvector full-text search + pre-computed embedding lookup (no runtime ML)
- **Sharing**: Public share links with token-based access and revocation
- **Settings**: Per-user defaults for gap, crossfade, template, resolution, and offline caching
- **Deployment**: Vercel (web app) + AWS Lambda container (render worker) + SQS (job queue)

### Phase 10: Lambda Render Worker Migration (Completed)

Migrated the render pipeline from in-process Vercel execution to an AWS Lambda-based worker architecture:

- **Python Render Worker** (`services/render-worker/`): Feature-parity port of Node.js render pipeline
- **SQS Integration**: Next.js enqueues jobs instead of running `after()` callbacks
- **Dependency Removal**: canvas, ffmpeg-static, fluent-ffmpeg, fastembed removed from webapp
- **Hybrid Search**: Replaced runtime fastembed with tsvector full-text + pre-computed embeddings
- **CI/CD**: GitHub Actions workflows for CI (PR) and deploy (push to main)
- **Docker**: Lambda container image with FFmpeg, CJK fonts, Python 3.11

### Phase 11: Simplify Render Progress Notification v2 (Completed)

Simplified the render progress UX by removing real-time SSE polling and percentage-based progress in favor of a static "submitted" card and text-only status badges:

- **Songset Size Limit**: Max 5 songs / 25 min per songset, enforced at API (`route.ts`), DB layer (`songsets.ts`), UI (`SongsetEditor`, `BrowseSheet`), and worker (`pipeline.py`)
- **Static RenderSubmitted Card**: Replaced `RenderProgress` (SSE/polling) with a static card showing estimated time and "You can leave this page" message
- **RenderStatusBadge**: Replaced `RenderStateButton` (with percentage) with a text-only badge (unrendered/rendering/fresh/stale/failed)
- **Removed Deprecated Fields**: `percentComplete` and `estimatedSecondsLeft` removed from `RenderJob` interface and `job-manager.ts` (DB columns kept for backward compat)
- **Updated Render Ratios**: 720p/1080p video default ratios changed from 0.8/0.65 to 0.5
- **Deleted SSE Endpoint**: `api/render-jobs/[id]/events/route.ts` and related test removed
- **New Files**: `constants.ts`, `RenderSubmitted.tsx`, `RenderStatusBadge.tsx` + tests
- **Commit**: `029b7f5`

---

## Dependencies

### Admin CLI (`pyproject.toml` - `[admin]` extra)

```toml
admin = [
    "typer>=0.9.0",
    "pydantic>=2.0.0",
    "beautifulsoup4>=4.12.0",
    "requests>=2.31.0",
    "rich>=13.0.0",
    "yt-dlp>=2024.1.1",      # Phase 3
    "boto3>=1.34.0",         # Phase 3
]
```

### Analysis Service (`services/analysis/pyproject.toml`)

```toml
dependencies = [
    "fastapi>=0.109.0",
    "uvicorn[standard]>=0.27.0",
    "pydantic>=2.5.0",
    "torch==2.4.1",          # Platform-conditional in Dockerfile
    "torchaudio==2.4.1",
    "torchvision==0.19.1",
    "allin1>=0.4.0",
    "demucs>=4.0.0",
    "natten==0.17.1",        # Compiled from source in Dockerfile
    "boto3>=1.34.0",
    "openai-whisper>=20231117",  # Phase 6: LRC generation
    "openai>=1.10.0",            # Phase 6: LLM alignment
]
```

### User App (`pyproject.toml` - `[app]` extra)

```toml
app = [
    "textual>=0.47.0",       # Phase 8: TUI framework
    "pydub>=0.25.0",         # Phase 8: Audio manipulation
    "miniaudio>=1.59.0",     # Phase 8: Audio playback
    "pillow>=10.0.0",        # Phase 8: Video frame generation
    "requests>=2.31.0",      # Phase 8: HTTP client
]
```

### Render Worker (`services/render-worker/requirements.txt`)

```
boto3>=1.34.0               # R2 S3-compatible storage + SQS
psycopg2-binary>=2.9.0       # Neon PostgreSQL job status
Pillow>=10.0.0               # Frame rendering
python-dotenv>=1.0.0         # Local development env loading
```

### Development Dependencies

```toml
test = [
    "pytest>=7.4.0",
    "pytest-asyncio>=0.21.0",
    "pytest-cov>=4.1.0",
]
```

---

## Notes

### Architecture Decisions

- **Monorepo structure:** Admin CLI, Analysis Service, Render Worker, and Web App are separate packages but co-located
- **Microservice separation:** Analysis Service and Render Worker are standalone services, not imported by CLI or web app
- **Communication:** CLI → HTTP → Analysis Service; Web App → SQS → Lambda Render Worker
- **Dependency isolation:** CLI stays lightweight (~50MB), Analysis Service is heavy (~2GB PyTorch), Render Worker is medium (~500MB with FFmpeg+fonts)
- **Platform support:** Docker images support both x86_64 (CPU-only PyTorch) and ARM64 (standard PyTorch)
- **Render offloading:** Heavy render pipeline moved from Vercel to Lambda to avoid Vercel Pro + Fluid Compute costs and timeout limits
- **Search optimization:** Replaced runtime fastembed with Postgres tsvector full-text search (no ML at query time)

### Implementation Patterns

- Phase 1-2: Catalog management foundation with retry logic and rate limiting
- Phase 3: Hash-based deduplication prevents duplicate R2 uploads
- Phase 4: Content-hash caching avoids re-analyzing identical audio files
- Phase 6: LLM-agnostic design supports any OpenAI-compatible API provider
- All database operations use Pydantic models for type safety
- Tests use temporary databases and mocked HTTP/R2 clients for isolation
- CLI follows command-group pattern (db, catalog, audio)
- Analysis Service uses async job queue with concurrent worker processing

### Security

- R2 credentials read from environment variables (`SOW_R2_ACCESS_KEY_ID`, `SOW_R2_SECRET_ACCESS_KEY`)
- No credentials stored in config files or code
- Database paths configurable via TOML config

### Git Commit References

- Phase 1: `8675fff` - Foundation
- Phase 2: `1685d69` - Catalog Management
- Phase 3: `a2690b2` - Audio Download
- Phase 4: `bdd01d3` - Analysis Service
- Phase 5: `cb96e17` - CLI ↔ Service Integration
- Phase 6: `f858da4` - LRC Generation
- Phase 7: `ce5bbc4` - Turso Sync
- Phase 8: `b82fc0d` - User App (TUI)
- Phase 9: Web App (Next.js)
- Phase 10: Lambda Render Worker Migration
- Phase 11: `029b7f5` - Simplify Render Progress Notification v2

---

*This document should be updated as implementation progresses.*
