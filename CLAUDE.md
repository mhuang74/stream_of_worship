# Agent Instructions

## Project Overview

Stream of Worship is a seamless Chinese worship music transition system designed to analyze songs (tempo, key, structure) and generate smooth transitions between them.

The end goal is to:
- generate audio file containing multiple songs with smooth transition between songs
- generate video file containing lyrics video of multiple songs with smooth transition between songs
- interactive tool to select multiple songs from song library, experiment with transition parameters, and generate output audio/video file with multiple songs with smooth transition between songs
- admin tool to manage song library (via scraping sop.org), and perform song analysis and lyrics LRC generation

## Development Commands

**Package Manager:** `uv` (always use `uv add` to add dependencies)

**Run Components:**
```bash
# Admin CLI (lightweight, no ML)
uv run --project ops/admin-cli --extra admin sow-admin --help

# Lab User App TUI
uv run --project lab/sow-app sow-app --help

# Web App (Next.js, from delivery/webapp/ directory)
cd delivery/webapp && pnpm dev
# Or from project root:
pnpm --filter sow-webapp dev

# Android App (native mobile client)
cd delivery/android && ./gradlew assembleDebug

# Analysis Service (heavy ML, requires Docker + R2 credentials)
cd ops/analysis-service && docker compose up -d
```

**Run Tests:**
```bash
# Admin CLI + shared DB helpers
# (integration tests requiring Docker/testcontainers are excluded by default)
uv run --project ops/admin-cli --python 3.11 --extra admin --extra test pytest -v

# Lab app
uv run --project lab/sow-app --extra test pytest -v

# Analysis service
cd ops/analysis-service && uv run --extra dev pytest tests/ -v

# Render worker
cd delivery/render-worker && uv run --extra dev pytest tests/ -v

# Legacy CLI/TUI
uv run --project lab/legacy-cli-tui --extra test pytest -v

# Android app
cd delivery/android && ./gradlew testDebugUnitTest koverXmlReport
```

**Web App Commands (delivery/webapp/ directory):**
```bash
# Development
pnpm dev          # Start dev server on http://localhost:8080
pnpm test         # Run tests (Vitest)
pnpm test:watch   # Run tests in watch mode
pnpm lint         # Lint with ESLint
pnpm build        # Production build

# Database migrations (Drizzle ORM)
npx drizzle-kit push       # Push schema changes to DB (dev)
npx drizzle-kit generate   # Generate migration files
npx drizzle-kit migrate    # Run pending migrations
```

**Render Worker Commands (delivery/render-worker/ directory):**
```bash
# Run tests
cd delivery/render-worker && uv run --extra dev pytest tests/ -v

# Run specific test files
cd delivery/render-worker && uv run --extra dev pytest tests/test_pipeline.py -v
cd delivery/render-worker && uv run --extra dev pytest tests/test_video_engine.py -v

# Local development with Docker
docker compose up --build
```

**Android App Commands (delivery/android/ directory):**
```bash
./gradlew testDebugUnitTest
./gradlew koverXmlReport
./gradlew lintDebug
./gradlew assembleDebug
```

## Architecture & Structure

The project consists of **eight architecturally separate components**:

### 1. POC Scripts (Experimental)
- **Location:** `lab/poc-scripts/` directory

### 2. Admin CLI (Backend Management)
- **Location:** `ops/admin-cli/src/stream_of_worship/admin/` (Python package)

### 3. Analysis Service (Microservice)
- **Location:** `ops/analysis-service/` (separate package: `sow_analysis`)

### 4. Lab User App (Deprecated TUI)
- **Location:** `lab/sow-app/` (Python package: `sow_lab_app`)

### 5. Legacy CLI/TUI (Deprecated)
- **Location:** `lab/legacy-cli-tui/` (Python package: `sow_legacy_cli_tui`)
- **Stack:** Textual TUI, pydub, miniaudio, ffmpeg-python, Pillow
- **Commands:** `uv run --project lab/legacy-cli-tui stream-of-worship --help`

### 6. Web App (Next.js Browser Application)
- **Location:** `delivery/webapp/` (Node.js/TypeScript, Next.js 16 App Router)
- **Stack:** Drizzle ORM + Neon Postgres, Better Auth, Cloudflare R2, AWS SQS (render jobs enqueued to SQS, processed by Lambda worker)
- **Commands:** `pnpm dev`, `pnpm test`, `pnpm lint`, `pnpm build` (run from `delivery/webapp/` or via `pnpm --filter sow-webapp`)

### 7. Android App (Native Mobile Client)
- **Location:** `delivery/android/` (Kotlin/Jetpack Compose Gradle project)
- **Stack:** Jetpack Compose, Navigation, Retrofit/OkHttp, Better Auth cookies, Media3, Android DownloadManager, Kover, Robolectric
- **Boundary:** Uses only the webapp JSON APIs. It does not connect directly to PostgreSQL, Cloudflare R2, or AWS SQS.
- **Commands:** `./gradlew testDebugUnitTest`, `./gradlew koverXmlReport`, `./gradlew lintDebug`, `./gradlew assembleDebug`

### 8. Render Worker (AWS Lambda)
- **Location:** `delivery/render-worker/` (Python, deployed as Lambda container via private ECR)
- **Stack:** psycopg2, boto3, Pillow, FFmpeg, urllib3
- **Commands:** See `delivery/render-worker/README.md`

**Critical Separation:** Admin CLI (`sow-admin`) never imports PyTorch/ML libraries. It submits jobs to Analysis Service via HTTP. The Analysis Service is the only component with heavy ML dependencies. The Web App is a separate Node.js stack with its own package.json and dependencies, distinct from the Python components. The Android app is a separate Kotlin mobile client and must use the webapp JSON APIs instead of PostgreSQL, R2, or SQS directly.

- **Admin CLI**: Lightweight catalog/audio management
- **Lab User App**: Deprecated TUI for transitions, read-only from PostgreSQL/R2
- **Legacy CLI/TUI**: Deprecated CLI/TUI, predecessor to sow-app
- **Analysis Service**: Heavy ML (PyTorch, Demucs, allin1) in Docker
- **Android App**: Native delivery client for auth, songsets, render submission/status, playback, sharing, settings, and offline downloads

## Development Guidelines

- **Python Version**: 3.11
- **Python Env**:
  - ALWAYS use `uv add` to add dependencies so it's documented in pyproject.toml
  - for running song analysis using allinone library, use Docker via lab/poc-scripts/docker/docker-compose.allinone.yml (due to dependencies on NATTEN native libraries, doesn't work on Apple Silicon MacOS)
  - when not doing song analysis via allinone, use `uv run` to execute in isolated environment
  - put dependencies for different usecases into separate extra sections in pyproject.toml, eg song_analysis, scraper, tui, etc
- **Path Handling**: ALWAYS use `pathlib.Path` for file system operations. Do not use string concatenation for paths.
- **Configuration**: Use `config.json` (in TUI) or centralized constants. Do not hardcode paths (e.g., avoid `Path("output")`, use configured paths).
- **Formatting**:
  - **Black**: Line length 100.
  - **Ruff**: Line length 100, target version py311.
- **Output Directories**:
  - `output/transitions/`: For transition clips.
  - `output/songs/`: For full song outputs.
  - `stems/`: For separated audio stems.
- **Safety**:
  - Use `run_in_background=True` for long-running analysis tasks.
  - Verify file existence before reading/processing.
- Update MEMORY after completion of each phase, typically triggered by git commit

## Session Completion (MANDATORY)

Work is NOT complete until `git push` succeeds:

```bash
git pull --rebase
git push
git status  # MUST show "up to date with origin"
```

**CRITICAL:** Never stop before pushing. Never say "ready to push when you are" — YOU must push. If push fails, resolve and retry until it succeeds.

## graphify

This project has a graphify knowledge graph at graphify-out/.

Rules:
- Before answering architecture or codebase questions, read graphify-out/GRAPH_REPORT.md for god nodes and community structure
- If graphify-out/wiki/index.md exists, navigate it instead of reading raw files
- For cross-module "how does X relate to Y" questions, prefer `graphify query "<question>"`, `graphify path "<A>" "<B>"`, or `graphify explain "<concept>"` over grep — these traverse the graph's EXTRACTED + INFERRED edges instead of scanning files
- After modifying code files in this session, run `graphify update .` to keep the graph current (AST-only, no API cost)
