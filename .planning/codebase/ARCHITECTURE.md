# Architecture

**Analysis Date:** 2026-02-13

## Pattern Overview

**Overall:** Modular microservices with a centralized admin backend and user-facing frontend TUI.

The codebase follows a **multi-service architecture** where:
- **Admin CLI** (`src/stream_of_worship/admin/`) manages song catalog ingestion, analysis, and database
- **User App** (`src/stream_of_worship/app/`) provides a Textual TUI for creating songsets and exporting audio/video
- **Analysis Service** (`services/analysis/`) runs as a separate FastAPI microservice for CPU/GPU-intensive operations
- **Core Utilities** (`src/stream_of_worship/core/`) provide shared configuration and paths
- **POC Scripts** (`poc/`) contain experimental implementations and development prototypes

**Key Characteristics:**
- Separation of concerns: Admin/ingestion separate from user app
- Async processing: Long-running analysis jobs run in background workers
- Pluggable services: Audio/video engines, storage backends (R2), database clients
- SQLite-based persistence: Admin uses Turso/SQLite for song catalog, user app stores songsets locally
- Cloud-native design: Audio files stored in Cloudflare R2, analysis results cached locally

## Layers

**Admin Layer (Backend Management):**
- Purpose: Manage song catalog, download audio, perform analysis, generate metadata/LRC
- Location: `src/stream_of_worship/admin/`
- Contains: CLI commands (scraper, DB operations), service integrations (R2, YouTube, analysis API), database schema and models
- Depends on: Core utilities, external APIs (sop.org scraper, OpenAI, Cloudflare R2, analysis service)
- Used by: User app (read-only), CLI commands, analysis service

**User App Layer (End-User Interface):**
- Purpose: Browse song catalog, create/edit songsets, preview audio, export final audio/video
- Location: `src/stream_of_worship/app/`
- Contains: Textual screens (TUI), state management, export service, audio/video engines, playback service
- Depends on: Admin layer (read catalog), core utilities, external services (R2 for asset download, analysis API for LRC)
- Used by: End users via CLI (`sow-app run`)

**Analysis Service (Microservice):**
- Purpose: Perform CPU/GPU-intensive operations (audio analysis, stem separation, LRC generation)
- Location: `services/analysis/src/sow_analysis/`
- Contains: FastAPI routes, async job queue, workers (analyzer, separator, LRC generator), storage clients
- Depends on: Heavy ML libraries (librosa, demucs, faster-whisper), Cloudflare R2, LLM APIs
- Used by: Admin CLI (job submission), optionally user app

**Core Layer (Shared Utilities):**
- Purpose: Configuration management, path handling, shared models
- Location: `src/stream_of_worship/core/`
- Contains: Config dataclass, path utilities, catalog index management
- Depends on: Standard library only (pathlib, dataclasses, json)
- Used by: All layers

**POC/Experimental Layer:**
- Purpose: Prototypes and development scripts
- Location: `poc/`
- Contains: Standalone scripts for testing analysis, scraping, transition generation
- Depends on: Ad-hoc implementations, sometimes mirrors production code
- Used by: Development and testing only

## Data Flow

**Song Ingestion Pipeline:**

1. Admin scrapes sop.org → Song metadata stored in admin database
2. Admin downloads audio from YouTube via yt-dlp → Audio stored in R2
3. Admin submits analysis job to Analysis Service → Audio analyzed (BPM, key, stems, etc.)
4. Analysis Service uploads results to R2 → Admin downloads and stores metadata
5. Admin generates LRC using Whisper + LLM → LRC stored locally in song directory
6. Admin updates catalog index with metadata

**User Export Pipeline:**

1. User selects songs and creates/edits songset in sow-app
2. User previews song with playback service (audio cached locally)
3. User configures transitions (gaps, key shifts, tempo adjustments) per song
4. User initiates export → ExportService begins:
   - Downloads audio files from R2 to local cache (AssetCache)
   - AudioEngine combines songs with transition gaps using pydub
   - VideoEngine creates lyrics video using FFmpeg (if enabled)
   - Returns paths to exported audio/video files in output directory

**State Management:**

- **Songset Data:** Stored in user app database (`~/.config/stream-of-worship/app.db`)
- **Catalog Data:** Stored in admin database (shared location or synced)
- **User Preferences:** Stored in config.json (R2 bucket, paths, LLM settings)
- **Export State:** In-memory in ExportService, synchronized with UI screens

## Key Abstractions

**SongsetClient:**
- Purpose: Manage songset CRUD operations and persistence
- Examples: `src/stream_of_worship/app/db/songset_client.py`
- Pattern: Read/write SQLite via SQL statements, manage songset items with song/recording joins

**ReadOnlyClient:**
- Purpose: Read-only access to song catalog for browsing and search
- Examples: `src/stream_of_worship/app/db/read_client.py`
- Pattern: Query admin database, join songs with their latest recordings and analysis metadata

**AssetCache:**
- Purpose: Local filesystem cache for audio files downloaded from R2
- Examples: `src/stream_of_worship/app/services/asset_cache.py`
- Pattern: Lazy download on first request, LRU-style cache management, integrated with R2Client

**AudioEngine:**
- Purpose: Combine multiple songs with transition gaps
- Examples: `src/stream_of_worship/app/services/audio_engine.py`
- Pattern: Load audio with pydub, calculate gaps based on tempo/beats, concatenate segments, normalize loudness

**VideoEngine:**
- Purpose: Generate lyrics video with background and synchronized subtitle rendering
- Examples: `src/stream_of_worship/app/services/video_engine.py`
- Pattern: Template-based, uses FFmpeg for final composition, supports multiple templates

**ExportService:**
- Purpose: Orchestrate audio and video export in a background thread
- Examples: `src/stream_of_worship/app/services/export.py`
- Pattern: State machine (PREPARING → DOWNLOADING → GENERATING_AUDIO → GENERATING_VIDEO → COMPLETED), progress callback for UI

**JobQueue (Analysis Service):**
- Purpose: In-memory queue with configurable concurrency for analysis and LRC jobs
- Examples: `services/analysis/src/sow_analysis/workers/queue.py`
- Pattern: Async queue, workers poll for jobs, separate concurrency limits for analysis (1) vs LRC (2)

**R2Client:**
- Purpose: S3-compatible storage access to Cloudflare R2
- Examples: `src/stream_of_worship/admin/services/r2.py`
- Pattern: Boto3 wrapper, supports upload/download/list with content hash tracking

## Entry Points

**Admin CLI (`stream-of-worship` / `sow-admin`):**
- Location: `src/stream_of_worship/cli/main.py` and `src/stream_of_worship/admin/main.py`
- Triggers: User runs command-line tool
- Responsibilities: Route to subcommands (ingest, playlist, config, migrate), parse arguments, execute operations

**User App (`sow-app`):**
- Location: `src/stream_of_worship/app/main.py`
- Triggers: User runs `sow-app run`
- Responsibilities: Check config/database, initialize services, launch TUI via Textual App

**Analysis Service API:**
- Location: `services/analysis/src/sow_analysis/main.py`
- Triggers: HTTP requests to `/api/v1/jobs/analyze` or `/api/v1/jobs/lrc`
- Responsibilities: Validate API key, submit jobs to queue, return job status

## Error Handling

**Strategy:** Layered error propagation with user-facing messaging.

**Patterns:**

- **Admin Layer:** Exceptions propagate to CLI with descriptive messages via Rich console formatting
- **User App:** Exceptions caught in screens, displayed in error modal via AppState.set_error()
- **Analysis Service:** Job failures stored in JobQueue with error messages, accessible via status endpoint
- **Export Service:** Failures captured in ExportProgress, UI displays in export_progress screen

**File Verification:**
- Before processing, check file existence with `Path.exists()`
- Audio engine loads files with exception handling, graceful degradation
- Missing LRC files cause export to fail early with clear messaging

## Cross-Cutting Concerns

**Logging:**
- Admin: Uses Rich console for formatted output
- User App: Structured logging to `~/.config/stream-of-worship/logs/sow_app.log`
- Analysis Service: FastAPI logging configured at module level
- Pattern: Module-level logger via `get_logger(__name__)` in app layer

**Validation:**
- Admin database: SQL schema enforces column types, NOT NULL constraints
- User app forms: Input validation at screen level (Textual widgets)
- Export config: SongsetItem validates transition parameters (gap_beats, crossfade_duration, key_shift, tempo_ratio)
- API: FastAPI Pydantic models validate request/response JSON

**Authentication:**
- Admin → Analysis Service: Shared API key via `SOW_ANALYSIS_API_KEY` env var (Bearer token)
- User App → R2: Credentials from admin config (inherited)
- No user-level auth in user app (assumes single user on local machine)

---

*Architecture analysis: 2026-02-13*
