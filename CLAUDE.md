# Agent Instructions

## Project Overview

Stream of Worship is a seamless Chinese worship music transition system designed to analyze songs (tempo, key, structure) and generate smooth transitions between them.

The end goal is to:
- generate audio file containing multiple songs with smooth transition between songs
- generate video file containing lyrics video of multiple songs with smooth transition between songs
- interactive tool to select multiple songs from song library, experiment with transition parameters, and generate output audio/video file with multiple songs with smooth transition between songs
- admin tool to manage song library (via scraping sop.org), and perform song analysis and lyrics LRC generation

## Issue Tracking (beads)

This project uses **bd** (beads) for issue tracking:

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --status in_progress  # Claim work
bd close <id>         # Complete work
bd sync               # Sync with git
```

## Development Commands

**Package Manager:** `uv` (always use `uv add` to add dependencies)

**Run Components:**
```bash
# Admin CLI (lightweight, no ML)
uv run --extra admin sow-admin --help

# User App TUI
uv run --extra app sow-app run

# Analysis Service (heavy ML, requires Docker + R2 credentials)
cd services/analysis && docker compose up -d
```

**Run Tests:**
```bash
# Always set PYTHONPATH=src prefix and use Python 3.11
# Use --extra app --extra test to include all test dependencies (fastapi, pydantic, aiosqlite, etc.)
PYTHONPATH=src uv run --python 3.11 --extra app --extra test pytest tests/app/services/test_video_engine.py -v

# Run all tests (excludes poc/, scripts/ directories per pyproject.toml config)
PYTHONPATH=src uv run --python 3.11 --extra app --extra test pytest tests/ -v

# Run specific test categories
PYTHONPATH=src uv run --python 3.11 --extra app --extra test pytest tests/app/ -v
PYTHONPATH=src uv run --python 3.11 --extra app --extra test pytest tests/admin/ -v

# Note: tests/services/analysis/ require the analysis service dependencies
# which are installed separately in services/analysis/ and run via Docker

# Run tests excluding backend services (analysis/qwen3) - FAST, no Docker needed
PYTHONPATH=src uv run --python 3.11 --extra app --extra test pytest tests/ \
  --ignore=tests/services/analysis \
  --ignore=services/qwen3/tests \
  --ignore=services/analysis/tests -v
```

## Architecture & Structure

The project consists of **four architecturally separate components**:

### 1. POC Scripts (Experimental)
- **Location:** `poc/` directory

### 2. Admin CLI (Backend Management)
- **Location:** `src/stream_of_worship/admin/` (Python package)

### 3. Analysis Service (Microservice)
- **Location:** `services/analysis/` (separate package: `sow_analysis`)

### 4. User App (End-User Application)
- **Location:** `src/stream_of_worship/app/` (Python package)

**Critical Separation:** Admin CLI (`sow-admin`) never imports PyTorch/ML libraries. It submits jobs to Analysis Service via HTTP. The Analysis Service is the only component with heavy ML dependencies.

- **Admin CLI**: Lightweight catalog/audio management
- **User App**: TUI for transitions, read-only from Turso/R2
- **Analysis Service**: Heavy ML (PyTorch, Demucs, allin1) in Docker

## Development Guidelines

- **Python Version**: 3.11
- **Python Env**:
  - ALWAYS use `uv add` to add dependencies so it's documented in pyproject.toml
  - for running song analysis using allinone library, use Docker via docker/docker-compose.allinone.yml (due to dependencies on NATTEN native libraries, doesn't work on Apple Silicon MacOS)
  - when not doing song analysis via allinone, use `uv run` to execute in isolated environment
  - put dependencies for different usecases into separate extra sections in pyproject.toml, eg song_analysis, scraper, tui, etc
- **Path Handling**: ALWAYS use `pathlib.Path` for file system operations. Do not use string concatenation for paths.
- **Configuration**: Use `config.json` (in TUI) or centralized constants. Do not hardcode paths (e.g., avoid `Path("output")`, use configured paths).
- **Formatting**:
  - **Black**: Line length 100.
  - **Ruff**: Line length 100, target version py311.
- **Output Directories**:
  - `output_transitions/`: For transition clips.
  - `output_songs/`: For full song outputs.
  - `stems/`: For separated audio stems.
- **Safety**:
  - Use `run_in_background=True` for long-running analysis tasks.
  - Verify file existence before reading/processing.
- Update `report/current_impl_status.md` and MEMORY after completion of each phase, typically triggered by git commit

## Session Completion (MANDATORY)

Work is NOT complete until `git push` succeeds:

```bash
git pull --rebase
bd sync
git push
git status  # MUST show "up to date with origin"
```

**CRITICAL:** Never stop before pushing. Never say "ready to push when you are" — YOU must push. If push fails, resolve and retry until it succeeds.
