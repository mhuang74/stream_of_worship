# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Stream of Worship is a seamless Chinese worship music transition system designed to analyze songs (tempo, key, structure) and generate smooth transitions between them.

The end goal is to:
- generate audio file containing multiple songs with smooth transiton between songs
- generate video file containing lyrics video of multiple songs with smooth transition between songs
- interactive tool to select multiple songs from song library, experiment with transition parameters, and generate output audio/video file with multiple songs with smooth transition between songs
- admin tool to manage song library (via scraping sop.org), and perform song analysis and lyrics LRC generation

## Architecture & Structure

The project consists of **four architecturally separate components**:

### 1. 🧪 POC Scripts (Experimental)
- **Location:** `poc/` directory
- **Purpose:** Validate analysis algorithms during development
- **Runtime:** One-off script execution in Docker
- **Technologies:** Librosa (signal processing) or All-In-One (deep learning)
- **Status:** Archived experimental code (including `poc/transition_builder_v2/` TUI)

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


## Development Guidelines

- **Python Version**: 3.11+
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
- run tests only using `uv`, remembering to set PYTHONPATH first. For example: `PYTHONPATH=src uv run --extra app pytest tests/app/services/test_video_engine.py -v`
