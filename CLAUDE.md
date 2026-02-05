# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Stream of Worship is a seamless Chinese worship music transition system designed to analyze songs (tempo, key, structure) and generate smooth transitions between them. The project is currently in the Proof of Concept (POC) phase, focusing on validation of audio analysis pipelines and interactive transition generation.

The end goal is to:
- generate audio file containing multiple songs with smooth transiton between songs
- generate video file containing lyrics video of multiple songs with smooth transition between songs
- interactive tool to select multiple songs from song library, experiment with transition parameters, and generate output audio/video file with multiple songs with smooth transition between songs

## Architecture & Structure

- **`poc/`**: Core analysis scripts and utilities (Phase 1).
  - `poc_analysis.py`: Main analysis script using Librosa (Signal Processing).
  - `poc_analysis_allinone.py`: Advanced analysis using `allin1` (Deep Learning).
  - `lyrics_scraper.py`: Utility to scrape Chinese lyrics from sop.org.
- **`poc/transition_builder_v2/`**: Text-based User Interface (TUI) for interactive transition generation (archived).
  - Built with the [Textual](https://textual.textualize.io/) framework.
  - `app/`: Application source code (`main.py`, `services/`, `screens/`, `models/`).
  - `config.json`: Configuration for paths and settings.
- **`data/`**: Data storage (e.g., `data/lyrics/`).
- **`poc/audio/`**: Input directory for source audio files (MP3/FLAC).
- **`poc/output/`**: Output directory for analysis results (JSON, CSV, PNG) and generated transitions.
- **`specs/`**: Design specifications and documentation.
- **Docker** (`docker/`): Containerized environments for reproducible analysis.
  - `docker/docker-compose.yml`: Standard environment (Librosa).
  - `docker/docker-compose.allinone.yml`: Deep learning environment (PyTorch, All-In-One).


### Running Analysis (POC)
```bash
# Standard analysis (Librosa) - Fast, lightweight
docker compose -f docker/docker-compose.yml run --rm librosa python poc/poc_analysis.py

# Advanced analysis (All-In-One) - Slower, more accurate
docker compose -f docker/docker-compose.allinone.yml run --rm allinone python poc/poc_analysis_allinone.py
```

### Running the Transition Builder (TUI)
```bash
# Navigate to directory first
cd poc/transition_builder_v2

# Run via Python module
uv run --extra tui python -m app.main

# OR via script
./run.sh
```

### Running the Lyrics Scraper
```bash
# Run full scrape
uv run --extra scraper python poc/lyrics_scraper.py

# Validate with test song
uv run --extra scraper python poc/lyrics_scraper.py --test

# Scrape with limit
uv run --extra scraper python poc/lyrics_scraper.py --limit 10
```

### Testing
```bash
# Run tests for Transition Builder
cd poc/transition_builder_v2
pytest
```

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
