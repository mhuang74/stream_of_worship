# Technology Stack

**Analysis Date:** 2026-02-13

## Languages

**Primary:**
- Python 3.11+ - Core application language for all components (admin CLI, analysis service, user app, POC scripts)

**Secondary:**
- Bash - Deployment and utility scripts in `scripts/` directory

## Runtime

**Environment:**
- Python 3.11 (minimum requirement in `pyproject.toml`)
- CPython interpreter via `uv` package manager

**Package Manager:**
- `uv` - Modern Python package manager for dependency management and environment isolation
- Lockfile: `uv.lock` (present and committed)
- Environments: Configured for both `darwin` and `linux` platforms in `[tool.uv]`

## Frameworks

**Core:**
- FastAPI 0.109.0+ - REST API framework for analysis microservice (`services/analysis/`)
- Pydantic 2.0+ - Data validation and settings management
- Pydantic-Settings 2.0+ - Environment-based configuration

**Web/UI:**
- Textual 0.44.0+ - Terminal user interface framework for interactive TUI app (`src/stream_of_worship/app/`)

**CLI:**
- Typer 0.9.0+ - CLI framework for admin commands (`sow-admin`, `sow-app`, `stream-of-worship`)
- Rich 13.0.0+ - Terminal output formatting

**Testing:**
- pytest 9.0.2+ - Unit and integration testing
- pytest-mock 3.12.0+ - Mocking support
- pytest-asyncio 1.3.0+ - Async test support
- fastapi (test dependencies) - For API testing
- httpx 0.26.0+ - Async HTTP client for testing

**Build/Dev:**
- setuptools 68.0+ - Package building
- Black - Code formatter (line length 100)
- Ruff - Linter (line length 100, target py311)

## Key Dependencies

**Critical:**
- librosa 0.10.0+ - Audio analysis and feature extraction (tempo, key detection) (`src/stream_of_worship/app/services/`, `services/analysis/workers/analyzer.py`)
- numpy 1.24.0+ / 2.0.2+ - Numerical computing for audio analysis
- torch 2.8.0 <2.9.0 - Deep learning framework for audio models (torchaudio compatibility constraint)
- torchaudio 2.8.0 <2.9.0 - Audio processing (Note: 2.9.0+ breaks pyannote.audio)
- ffmpeg-python 0.2.0+ - Audio/video processing backend
- Pillow 10.0.0+ - Image processing for video generation

**Transcription & Speech:**
- faster-whisper 1.0.0+ - Fast speech-to-text with word-level timing
- whisperx 3.7.0 - Alternative transcription with forced alignment
- funasr 1.3.0+ - Alibaba ASR model support
- modelscope 1.34.0+ - Model Zoo SDK for accessing ML models
- pyannote.audio - Speaker diarization (via torch/torchaudio)
- qwen-asr - Alibaba Qwen speech recognition

**Audio Processing:**
- pydub 0.25.0+ - Audio manipulation (trim, fade, concatenate)
- miniaudio 1.59+ - Low-latency audio playback (TUI app)
- soundfile 0.12.0+ - WAV file reading
- pyaudio 0.2.14+ - Audio I/O (TUI playback)
- demucs - Stem separation (via models in docker)
- librosa - Audio stem separation preprocessing

**Infrastructure & Storage:**
- boto3 1.34.0+ - AWS S3 API client (used for Cloudflare R2 via S3-compatible endpoint)
- libsql 0.1.0+ - Embedded SQLite with Turso sync (optional, for `turso` extra)
- requests 2.32.0+ - HTTP client for API calls and web scraping

**Web Scraping & Data:**
- beautifulsoup4 4.14.0+ - HTML parsing for sop.org scraping (`src/stream_of_worship/admin/services/scraper.py`)
- lxml 6.0.0+ - XML/HTML processing engine for BeautifulSoup
- pypinyin 0.55.0+ - Chinese character to pinyin conversion (song ID generation)
- yt-dlp 2024.1.1+ - YouTube audio downloading (`src/stream_of_worship/admin/services/youtube.py`)

**LLM & AI:**
- openai 1.0.0+ - OpenAI SDK (used with OpenRouter for LRC alignment)
- (Configured for OpenRouter via `https://openrouter.ai/api/v1` base URL, not direct OpenAI)

**Utilities:**
- tomli 2.0.0+ - TOML parsing for config files
- tomli-w 1.0.0+ - TOML writing
- youtube-transcript-api 0.6.0+ - YouTube subtitle extraction

## Configuration

**Environment:**
Environment variables are read from:
- Docker compose files set via `docker-compose.yml`
- `.env` files in working directory (not committed - contains secrets)
- OS environment (takes precedence)

**Required Environment Variables:**
- `SOW_R2_BUCKET` - Cloudflare R2 bucket name
- `SOW_R2_ENDPOINT_URL` - R2 endpoint URL
- `SOW_R2_ACCESS_KEY_ID` - R2 access key
- `SOW_R2_SECRET_ACCESS_KEY` - R2 secret key
- `SOW_ANALYSIS_API_KEY` - Bearer token for analysis service
- `SOW_TURSO_TOKEN` - Turso database auth token (optional)
- `SOW_LLM_API_KEY` - LLM API key (OpenRouter, etc.)
- `SOW_LLM_BASE_URL` - LLM API base URL
- `SOW_LLM_MODEL` - LLM model identifier
- `OPENROUTER_API_KEY` - OpenRouter API key (alternative to SOW_LLM_API_KEY)

**Build:**
- `pyproject.toml` - Python project manifest with dependencies organized into extras (`scraper`, `lrc_generation`, `video`, `tui`, `song_analysis`, `transcription`, `transcription_whisperx`, `transcription_youtube`, `admin`, `turso`, `app`, `test`)
- `tool.black` - Black formatter config (line-length 100)
- `tool.ruff` - Ruff linter config (line-length 100, target py311)
- `.nvmrc` / `.python-version` - Not present; Python version managed via `requires-python = ">=3.11"`
- Docker: `poc/docker/Dockerfile.allinone`, `services/analysis/Dockerfile` for containerized deployment

## Platform Requirements

**Development:**
- Python 3.11+
- `uv` package manager
- FFmpeg binary (system-level) - for audio/video processing
- NVIDIA CUDA toolkit (optional) - for GPU acceleration in Docker
- On Apple Silicon macOS: Use Docker (`docker-compose.allinone.yml`) for song analysis due to NATTEN native library incompatibility
- Linux/x86_64: Can run natively or via Docker

**Production:**
- Docker containers for analysis service (`services/analysis/docker-compose.yml`)
- Python 3.11+ runtime
- Optional: NVIDIA GPU with CUDA for acceleration (`deploy` section commented in compose file)
- Storage: Cloudflare R2 bucket for audio assets and LRC files
- Database: SQLite locally or Turso for remote sync
- Minimum concurrent analysis jobs: 1 (high memory/CPU)
- Configurable concurrent LRC jobs: 2 (lower memory with faster-whisper)

**Hardware Considerations:**
- Song analysis: Memory-intensive (Demucs + librosa + torch models)
- Transcription: GPU-friendly but CPU-capable (faster-whisper)
- Recommend: 8GB+ RAM for analysis, 4GB+ for transcription
- CPU-only processing supported on Linux/x86_64 and macOS Intel (via Docker on Apple Silicon)

---

*Stack analysis: 2026-02-13*
