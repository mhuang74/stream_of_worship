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

### 1. POC Scripts (Experimental)
- **Location:** `poc/` directory

### 2. Admin CLI (Backend Management)
- **Location:** `src/stream_of_worship/admin/` (Python package)

### 3. Analysis Service (Microservice)
- **Location:** `services/analysis/` (separate package: `sow_analysis`)

### 4. User App (End-User Application)
- **Location:** `src/stream_of_worship/app/` (Python package)



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
- run tests only using `uv`, remembering to set PYTHONPATH first. For example: `PYTHONPATH=src uv run --extra app pytest tests/app/services/test_video_engine.py -v`
