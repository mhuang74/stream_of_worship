# External Integrations

**Analysis Date:** 2026-02-13

## APIs & External Services

**Song Metadata & Lyrics:**
- sop.org/songs (Web scraping) - Song catalog with lyrics, composer, key metadata
  - Client: `src/stream_of_worship/admin/services/scraper.py` (`CatalogScraper` class)
  - Auth: None (public web scraping with User-Agent headers)
  - Protocol: HTTP GET with BeautifulSoup4 HTML parsing
  - Table scraped: TablePress table ID `tablepress-3`

**Audio Source:**
- YouTube (Video/Audio source) - Song recordings for download
  - Client: `src/stream_of_worship/admin/services/youtube.py` (`YouTubeDownloader` class)
  - SDK: yt-dlp 2024.1.1+
  - Auth: None (public video access)
  - Output: MP3 conversion at 192 kbps
  - Search: Title + composer + album metadata to find official lyrics videos

**Analysis Service API:**
- Internal FastAPI microservice (`services/analysis/`)
  - Protocol: REST HTTP/JSON
  - Base URL: Configured per environment
  - Auth: Bearer token via `SOW_ANALYSIS_API_KEY` header
  - Endpoints:
    - `POST /jobs/analyze` - Submit song for tempo/key/beat analysis
    - `POST /jobs/lrc` - Submit audio + lyrics for LRC generation
    - `GET /jobs/{job_id}` - Poll job status
  - Client: `src/stream_of_worship/admin/services/analysis.py` (`AnalysisClient` class)
  - Job queue: Custom queue system with async processing
  - Max concurrent analysis jobs: `SOW_MAX_CONCURRENT_ANALYSIS_JOBS` (default 1, CPU-bound)
  - Max concurrent LRC jobs: `SOW_MAX_CONCURRENT_LRC_JOBS` (default 2, GPU-friendly)

## Data Storage

**Databases:**

*Local/Primary:*
- SQLite 3 - Local song catalog and recording metadata
  - Location: Configured in `src/stream_of_worship/admin/config.py` (default in user config dir)
  - Client: `src/stream_of_worship/admin/db/client.py` (`DatabaseClient` class)
  - Tables: Songs, Recordings, Analysis metadata
  - Connection: sqlite3 (standard library) or libsql if Turso enabled

*Remote/Optional:*
- Turso (LibSQL) - Embedded SQLite replica for remote sync
  - URL: `SOW_TURSO_DATABASE_URL` environment variable or `config.json`
  - Auth: `SOW_TURSO_TOKEN` environment variable
  - SDK: libsql 0.1.0+ (optional, in `turso` extra)
  - Sync: Bidirectional with local SQLite via embedded replica
  - Client: Same `DatabaseClient` auto-detects and enables when libsql available

**File Storage:**

*Cloud:*
- Cloudflare R2 - S3-compatible object storage for audio and LRC files
  - Bucket: `SOW_R2_BUCKET` environment variable
  - Endpoint: `SOW_R2_ENDPOINT_URL` environment variable
  - Auth:
    - Access Key: `SOW_R2_ACCESS_KEY_ID`
    - Secret Key: `SOW_R2_SECRET_ACCESS_KEY`
  - SDK: boto3 1.34.0+ (AWS S3 SDK, adapted for R2)
  - Client: `src/stream_of_worship/admin/services/r2.py` (`R2Client` class)
  - Paths:
    - Audio stems: `{hash_prefix}/stems/` (uploaded by analysis service)
    - Audio master: `{hash_prefix}/audio.mp3`
    - Lyrics: `{hash_prefix}/lyrics.lrc`
  - Usage: Songs analyzed via analysis service upload results directly to R2

*Local:*
- Filesystem directories (configurable)
  - `output_transitions/` - Generated transition clips
  - `output_songs/` - Full song outputs
  - `stems/` - Separated audio stems (local cache)
  - Cache paths: `~/.cache/stream_of_worship/` for Whisper models

**Caching:**
- No external cache service (Redis, Memcached)
- Analysis service maintains disk cache at `/cache` (Docker volume mount)
- Whisper models cached at `~/.cache/whisper/` or `/cache/whisper` (Docker)

## Authentication & Identity

**Auth Provider:**
- None (no user authentication system)
- API Authentication:
  - Analysis service: Bearer token (`SOW_ANALYSIS_API_KEY`)
  - R2/S3: AWS-style credentials (access key + secret key)
  - Turso: Database auth token
  - LLM: API key for OpenRouter or compatible service

**No User System:**
- Single-user or team scenarios
- All credentials managed via environment variables
- Admin CLI and TUI app (no multi-tenant, no user accounts)

## Monitoring & Observability

**Error Tracking:**
- None detected (no Sentry, Rollbar, etc.)
- Local error logging to stdout/files

**Logs:**
- Python logging to console and optional file outputs
- Docker: PYTHONUNBUFFERED=1 for real-time log streaming
- Demucs/NATTEN: `NATTEN_LOG_LEVEL=error` (suppresses verbose model logs)
- Dev logging: Print statements and logger calls throughout

**Health Checks:**
- Analysis service: `GET /health` endpoint (`services/analysis/routes/health.py`)
- Service startup health validation

## CI/CD & Deployment

**Hosting:**
- Docker containers for production analysis service
- Local Python environments for admin CLI and user app
- Optional: Kubernetes/Docker Compose for orchestration

**CI Pipeline:**
- Not detected in codebase (no GitHub Actions, GitLab CI, Jenkins configs)
- Manual deployment via Docker Compose
- Commands in `scripts/` directory for common tasks

**Docker:**
- `services/analysis/docker-compose.yml` - Analysis microservice
  - Multi-service: `analysis` (production) + `analysis-dev` (dev with hot-reload)
  - GPU support: Optional NVIDIA runtime (commented, requires NVIDIA Container Toolkit)
  - Cache volume: `analysis-cache` for model downloads
  - Environment: All SOW_* variables passed from host
- `poc/docker/docker-compose.allinone.yml` - All-in-one for POC/testing
  - Container: `allinone_container`
  - Mounts: Workspace, audio dirs, model cache
  - Usage: `docker compose -f docker-compose.allinone.yml run --rm allinone python script.py`

## Environment Configuration

**Required env vars for full functionality:**
```
SOW_R2_BUCKET=sow-audio
SOW_R2_ENDPOINT_URL=https://xxxxx.r2.cloudflarestorage.com
SOW_R2_ACCESS_KEY_ID=your-access-key
SOW_R2_SECRET_ACCESS_KEY=your-secret-key
SOW_ANALYSIS_API_KEY=your-analysis-api-key
SOW_LLM_API_KEY=your-openrouter-key
SOW_LLM_BASE_URL=https://openrouter.ai/api/v1
SOW_LLM_MODEL=openai/gpt-4o-mini
SOW_TURSO_TOKEN=your-turso-token (optional)
```

**Optional performance tuning:**
```
SOW_MAX_CONCURRENT_ANALYSIS_JOBS=1  # Increase carefully (high memory)
SOW_MAX_CONCURRENT_LRC_JOBS=2       # Can increase with GPU
SOW_DEMUCS_DEVICE=cpu               # Switch to "cuda" for GPU
SOW_WHISPER_DEVICE=cpu              # Switch to "cuda" for GPU
```

**Secrets location:**
- Environment variables (preferred, never committed)
- `.env` file in working directory (auto-loaded by pydantic-settings, `.gitignore`d)
- Docker: Passed via `docker-compose.yml` or `.env.docker` file
- Config file: `config.json` in user config dir (admin CLI stores R2 bucket/endpoint)

## Webhooks & Callbacks

**Incoming:**
- None detected (no webhook listeners)
- Analysis service: Only HTTP POST endpoints for job submission

**Outgoing:**
- None detected
- Analysis service uploads results directly to R2 (push model, not webhooks)

**Job Status Polling:**
- Client-initiated polling via `AnalysisClient.poll_job()` in `src/stream_of_worship/admin/services/analysis.py`
- Configurable polling interval and timeout

## API Clients Used

**OpenAI-Compatible:**
- Base client for LLM communication via openai SDK
- Endpoint: OpenRouter (`https://openrouter.ai/api/v1`) or configured alternative
- Authentication: API key in header
- Models: `openai/gpt-4o-mini` (default for LRC alignment)
- Used in:
  - `src/stream_of_worship/ingestion/lrc_generator.py` - LRC alignment
  - `src/stream_of_worship/ingestion/metadata_generator.py` - Metadata generation

**YouTube API:**
- SDK: yt-dlp (not official YouTube Data API)
- No API key required (public video access)
- Supports search, preview, and download

**Speech Recognition:**
- Whisper (OpenAI) - Embedded model, no API calls
- faster-whisper - Local model with word-level timing
- WhisperX - Forced alignment variant (optional)
- Qwen ASR - Alternative via modelscope
- All run locally in Docker/Python, no remote API

---

*Integration audit: 2026-02-13*
