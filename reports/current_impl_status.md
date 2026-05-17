# Stream of Worship - Current Implementation Status Report

**Generated:** 2026-05-17
**Project:** Stream of Worship - Admin CLI & Analysis Service
**Repository:** sow_cli_admin

---

## Executive Summary

The Stream of Worship project consists of an Admin CLI for backend management, an Analysis Service microservice for audio processing, and a User App (TUI) for worship leaders. The project has completed all 8 phases including foundational infrastructure, catalog management, audio download pipeline, Analysis Service implementation, CLI-Service integration, LRC generation, and the User App TUI.

**Overall Progress:** 8 of 8 phases complete (100%) 🎉

**Latest Milestone:** Phase 8 (User App TUI) completed in commit `b82fc0d`

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

### Phase 5: CLI ↔ Service Integration ✅ COMPLETE

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

**Combined Total: ~471 tests (all passing)**

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

---

## Next Steps / Pending Work

**All 8 phases are complete!** There are no pending implementation items.

The system now fully supports:
- Catalog management via Admin CLI (`sow-admin catalog` commands)
- Audio download and analysis via Analysis Service (`sow-admin audio` commands)
- Interactive songset building via User App TUI (`sow-app`)
- Export of audio + lyrics video with smooth transitions

### Future Enhancements (Optional)

Potential future improvements (not required for core functionality):

- **Turso Sync** - Bidirectional cloud synchronization for multi-device support
- **GUI Version** - Desktop GUI alternative to the TUI
- **Web Interface** - Browser-based songset builder
- **Additional Video Templates** - More visual styles for lyrics videos

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

- **Monorepo structure:** Admin CLI and Analysis Service are separate packages but co-located
- **Microservice separation:** Analysis Service is a standalone FastAPI service, not imported by CLI
- **Communication:** CLI → HTTP → Service (no direct Python imports)
- **Dependency isolation:** CLI stays lightweight (~50MB), Service is heavy (~2GB PyTorch)
- **Platform support:** Docker images support both x86_64 (CPU-only PyTorch) and ARM64 (standard PyTorch)

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

---

*This document should be updated as implementation progresses.*
