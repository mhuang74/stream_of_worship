# Codebase Structure

**Analysis Date:** 2026-02-13

## Directory Layout

```
stream_of_worship/
├── src/stream_of_worship/         # Main Python package (src layout)
│   ├── admin/                     # Admin CLI and backend management
│   │   ├── commands/              # Typer command groups (db, catalog, audio)
│   │   ├── services/              # Service integrations (R2, scraper, analysis client)
│   │   ├── db/                    # Admin database schema and models
│   │   └── config.py              # Admin configuration (R2 credentials, paths)
│   ├── app/                       # User app (TUI for songset management)
│   │   ├── screens/               # Textual screen implementations
│   │   ├── services/              # Audio/video engines, export orchestration
│   │   ├── db/                    # User app database clients and models
│   │   ├── app.py                 # Main Textual App class
│   │   └── config.py              # User app configuration
│   ├── core/                      # Shared utilities
│   │   ├── config.py              # Core Config dataclass
│   │   ├── paths.py               # Path management (user data dir, config paths)
│   │   └── catalog.py             # Catalog index management
│   ├── cli/                       # Legacy unified CLI (routes to admin/app)
│   │   └── main.py                # CLI entry point with subcommands
│   ├── ingestion/                 # Data generation utilities
│   │   ├── lrc_generator.py       # LRC generation via Whisper + LLM
│   │   └── metadata_generator.py  # AI metadata generation for songs
│   ├── assets/                    # Embedded font files
│   │   └── fonts/                 # TrueType fonts for video rendering
│   └── __init__.py
├── services/analysis/             # Separate FastAPI microservice
│   ├── src/sow_analysis/
│   │   ├── routes/                # FastAPI endpoints (health, jobs)
│   │   ├── workers/               # Background job processors (analyzer, lrc, separator)
│   │   ├── storage/               # R2 and cache clients
│   │   ├── config.py              # Service configuration (env vars)
│   │   └── main.py                # FastAPI app entry point
│   ├── docker-compose.yml         # Docker Compose configuration
│   ├── Dockerfile                 # Multi-platform Docker build
│   ├── pyproject.toml             # Service dependencies
│   └── README.md                  # Service documentation
├── poc/                           # Proof-of-concept and experimental scripts
│   ├── lyrics_scraper.py          # Web scraper for sop.org
│   ├── poc_analysis_allinone.py   # Local audio analysis using allinone model
│   ├── gen_lrc_*.py               # Various LRC generation approaches
│   ├── generate_transitions.py    # Transition audio generation
│   └── [other experimental scripts]
├── tests/                         # Test suite
│   ├── admin/                     # Admin layer tests
│   ├── app/                       # User app layer tests
│   ├── services/analysis/         # Analysis service tests
│   └── conftest.py                # Pytest configuration
├── scripts/                       # Utility and migration scripts
│   ├── migrate_song_library.py    # Migrate data from legacy structure
│   └── [other utility scripts]
├── data/                          # Static data files
│   ├── lyrics/                    # Lyrics reference data
│   └── [other data]
├── pyproject.toml                 # Main project configuration and dependencies
├── README.md                       # Project documentation
├── CLAUDE.md                       # Instructions for Claude Code
├── .gitignore                     # Git ignore patterns
└── uv.lock                        # Dependency lock file (uv package manager)
```

## Directory Purposes

**`src/stream_of_worship/admin/`:**
- Purpose: Administrative backend for managing song library and catalog
- Contains: Command-line interface, database operations, service integrations
- Key files: `main.py` (Typer CLI), `db/schema.py` (database schema), `db/client.py` (database access)

**`src/stream_of_worship/admin/commands/`:**
- Purpose: Typer command group implementations
- Contains: `db.py` (init, status, reset), `catalog.py` (scrape, search, list), `audio.py` (record, download)
- Pattern: Each module is a Typer app with subcommands

**`src/stream_of_worship/admin/services/`:**
- Purpose: Integration with external services
- Key files:
  - `r2.py`: Cloudflare R2 client (upload/download audio, stems)
  - `scraper.py`: Web scraper for sop.org song metadata
  - `analysis.py`: Client for communicating with analysis service API
  - `youtube.py`: YouTube download via yt-dlp
  - `hasher.py`: Content hash calculation for deduplication
  - `sync.py`: Sync operations between local and remote storage
  - `lrc_parser.py`: Parse LRC file format

**`src/stream_of_worship/admin/db/`:**
- Purpose: Admin database schema and data models
- Key files:
  - `schema.py`: SQL schema for songs, recordings, stems, analysis metadata
  - `models.py`: Song and Recording dataclasses
  - `client.py`: SQLite database client with query methods

**`src/stream_of_worship/app/`:**
- Purpose: End-user application for creating worship songsets
- Contains: Textual TUI screens, service layer, database client, state management
- Key files: `app.py` (main Textual App), `main.py` (CLI entry), `state.py` (reactive state)

**`src/stream_of_worship/app/screens/`:**
- Purpose: Individual Textual screen implementations
- Key files:
  - `songset_list.py`: Browse and select songsets
  - `browse.py`: Search and add songs to songset
  - `songset_editor.py`: Edit songset items, configure transitions
  - `export_progress.py`: Monitor audio/video export progress
  - `settings.py`: Configure app settings
  - `transition_detail.py`: View/edit individual transition parameters

**`src/stream_of_worship/app/services/`:**
- Purpose: Core business logic for audio/video generation
- Key files:
  - `audio_engine.py`: Combine songs with gap transitions, normalize loudness
  - `video_engine.py`: Generate lyrics video using FFmpeg
  - `export.py`: Orchestrate export workflow (state machine)
  - `playback.py`: Local audio playback for preview
  - `catalog.py`: Read catalog, search songs
  - `asset_cache.py`: Local filesystem cache for downloaded assets

**`src/stream_of_worship/app/db/`:**
- Purpose: User app database for storing songsets
- Key files:
  - `models.py`: Songset and SongsetItem dataclasses
  - `read_client.py`: Read-only access to admin catalog
  - `songset_client.py`: Full CRUD for user songsets

**`src/stream_of_worship/core/`:**
- Purpose: Shared utilities across admin and app
- Key files:
  - `config.py`: Core Config dataclass (paths, audio settings, LLM settings)
  - `paths.py`: Path management (user data dir, config path, song directory structure)
  - `catalog.py`: Catalog index for browsing available songs

**`services/analysis/`:**
- Purpose: Separate microservice for CPU/GPU-intensive analysis
- Contains: FastAPI app, async job queue, ML-based workers
- Key files:
  - `main.py`: FastAPI app with lifespan management
  - `workers/analyzer.py`: Audio analysis (BPM, key, loudness, beats)
  - `workers/separator.py`: Stem separation using Demucs
  - `workers/lrc.py`: LRC generation using faster-whisper + LLM alignment
  - `storage/r2.py`: Cloudflare R2 client for this service
  - `storage/cache.py`: Local cache for intermediate results

**`poc/`:**
- Purpose: Experimental and prototype code
- Contains: Standalone scripts for testing various approaches
- Status: Not part of production deployment, used for R&D

**`tests/`:**
- Purpose: Test suite
- Organization: Mirrors source structure under `src/stream_of_worship/`
- Key files: `conftest.py` (pytest fixtures), individual test modules with `test_` prefix

## Key File Locations

**Entry Points:**
- `src/stream_of_worship/cli/main.py`: Unified legacy CLI (routes to admin/app)
- `src/stream_of_worship/admin/main.py`: Admin CLI entry (`sow-admin`)
- `src/stream_of_worship/app/main.py`: User app entry (`sow-app run`)
- `services/analysis/src/sow_analysis/main.py`: Analysis service entry point

**Configuration:**
- `src/stream_of_worship/core/config.py`: Core configuration schema
- `src/stream_of_worship/admin/config.py`: Admin-specific config (R2, paths)
- `src/stream_of_worship/app/config.py`: User app-specific config
- `services/analysis/src/sow_analysis/config.py`: Analysis service config (env vars)

**Core Logic:**
- `src/stream_of_worship/admin/services/r2.py`: Cloud storage operations
- `src/stream_of_worship/app/services/audio_engine.py`: Audio mixing and transitions
- `src/stream_of_worship/app/services/video_engine.py`: Video/lyrics generation
- `src/stream_of_worship/app/services/export.py`: Export orchestration
- `src/stream_of_worship/admin/db/client.py`: Admin database access

**Testing:**
- `tests/conftest.py`: Pytest fixtures and configuration
- `tests/admin/test_scraper.py`: Scraper functionality tests
- `tests/app/services/test_audio_engine.py`: Audio engine tests
- `tests/services/analysis/`: Analysis service integration tests

## Naming Conventions

**Files:**
- Service implementations: `{service_name}.py` (e.g., `audio_engine.py`, `export.py`)
- Test files: `test_{module_name}.py` (e.g., `test_audio_engine.py`)
- Database files: `{entity_type}.py` for models, `client.py` for access layer
- Command groups: `{command_name}.py` in `commands/` directory

**Directories:**
- Screens/UI: `screens/` for Textual screen implementations
- Services: `services/` for business logic layer
- Database: `db/` for database-related code
- Commands: `commands/` for CLI command groups
- Workers: `workers/` for background job processors
- Storage: `storage/` for persistence and cloud clients

**Modules:**
- Dataclasses (entities): Capitalized names (e.g., `class Song`, `class SongsetItem`)
- Services: Capitalized names ending in "Service" or "Engine" (e.g., `class AudioEngine`, `class CatalogService`)
- Database clients: Capitalized names (e.g., `class ReadOnlyClient`, `class SongsetClient`)
- Functions: snake_case (e.g., `get_catalog_path()`, `generate_stems()`)

## Where to Add New Code

**New Feature (e.g., key shift, crossfade transition):**
- Primary code: `src/stream_of_worship/app/services/audio_engine.py` or `export.py`
- Tests: `tests/app/services/test_audio_engine.py`
- Screen updates: `src/stream_of_worship/app/screens/songset_editor.py` (for user input)
- Models: Update `src/stream_of_worship/app/db/models.py` (if new SongsetItem fields)

**New Analysis Capability (e.g., chord detection):**
- Implementation: `services/analysis/src/sow_analysis/workers/analyzer.py`
- Storage: Update schema in `src/stream_of_worship/admin/db/schema.py`
- Tests: `tests/services/analysis/test_analyzer.py`

**New Admin Command:**
- Implementation: Create new command file in `src/stream_of_worship/admin/commands/`
- Registration: Add to app in `src/stream_of_worship/admin/main.py`
- Tests: `tests/admin/commands/test_{command_name}.py`

**New Screen/UI:**
- Implementation: Create in `src/stream_of_worship/app/screens/{screen_name}.py`
- Registration: Add to `_create_screen()` method in `src/stream_of_worship/app/app.py`
- State management: Update `src/stream_of_worship/app/state.py` if new state needed
- Styling: Add CSS to `src/stream_of_worship/app/screens/app.tcss`

**Utilities/Helpers:**
- Shared across layers: `src/stream_of_worship/core/`
- Admin-specific: `src/stream_of_worship/admin/services/`
- App-specific: `src/stream_of_worship/app/services/`

## Special Directories

**`src/stream_of_worship/assets/fonts/`:**
- Purpose: Embedded font files for video rendering
- Contents: `NotoSansTC-Bold.ttf` and other fonts
- Generated: No (static assets)
- Committed: Yes

**`data/`:**
- Purpose: Static reference data
- Contents: Lyrics samples, song metadata fixtures
- Generated: No
- Committed: Yes (for fixtures), No (for large generated data)

**`.planning/codebase/`:**
- Purpose: GSD codebase analysis documents
- Contents: ARCHITECTURE.md, STRUCTURE.md, etc.
- Generated: Yes (by GSD mapper)
- Committed: Yes (for documentation)

**`tests/`:**
- Purpose: Test suite
- Pattern: Mirrors source structure, same relative paths as `src/stream_of_worship/`
- Pytest discovery: `test_*.py` files and `*_test.py` files
- Configuration: `pyproject.toml` excludes `scripts/`, `poc/`, `build/`, `dist/`

**`.venv/`:**
- Purpose: Virtual environment for local development
- Generated: Yes
- Committed: No (.gitignore)

---

*Structure analysis: 2026-02-13*
