# Analysis Service - Developer Guide

This guide covers how to set up and run the Analysis Service in development mode with hot-reload for rapid iteration.

## Quick Start (Development Mode)

### 1. Prerequisites

- Docker with Docker Compose
- `uv` (Python package manager) for model downloads
- All required environment variables in `.env` file (copy from `.env.example`)

### 2. Start the Development Server

Use the provided startup script that handles model downloads and starts the dev environment:

```bash
cd ops/analysis-service

# Start development server (downloads models if needed)
./start-dev.sh

# Run in detached mode
./start-dev.sh -d

# Rebuild the Docker image after dependency changes
./start-dev.sh --build

# Or manually with docker compose (requires models to be pre-downloaded)
docker compose up analysis-dev
```

The development service will:
- Download BS-Roformer and UVR-De-Echo models (if not present)
- Mount your local `src/` directory into the container for hot-reload
- Automatically restart on code changes
- Expose the API on `http://localhost:8000`

Source changes in `src/` hot-reload because the directory is bind-mounted into the container. Python dependency changes do not hot-reload: packages are installed when the Docker image is built. After changing `pyproject.toml` or `uv.lock`, run `./start-dev.sh --build` (or `./start-dev.sh --rebuild`) once so the `analysis-dev` image includes the new packages.

### 3. Verify It's Running

```bash
# Check health endpoint
curl http://localhost:8000/api/v1/health

# View logs
docker compose logs -f analysis-dev
```

## Model Setup (One-Time)

### Stem Separator (Runs on Modal)

Nothing to download here: stem separation runs in the `sow-stems` Modal app
(`modal_apps/stem_separator_app.py`), which caches the MelBand Roformer and
UVR-De-Echo models in its own volume. There is no in-container `audio-separator`
dependency and no `/models/audio-separator` bind mount any more. Deploy once with
your Modal token — see [README](README.md#stem-separation-on-modal).

```bash
uv run --no-project --with 'modal>=1.2.6,<2' modal deploy modal_apps/stem_separator_app.py
```

### Forced Aligner (Runs on Modal)

Nothing to download here: forced alignment runs in the `sow-forced-aligner` Modal
app, which caches the Qwen3-ForcedAligner checkpoint in its own volume. Deploy it
once with your Modal token — see [README](README.md#forced-alignment-on-modal).

## Development Workflow

### Making Code Changes

1. Edit files in `ops/analysis-service/src/sow_analysis/`
2. The server automatically reloads (no restart needed)
3. Watch logs for any syntax errors or import issues

### Common Development Tasks

#### View Logs
```bash
# Follow logs in real-time
docker compose logs -f analysis-dev

# Show last 100 lines
docker compose logs --tail=100 analysis-dev
```

#### Restart the Service
```bash
# Restart the existing container image
docker compose restart analysis-dev

# Rebuild after changing pyproject.toml, uv.lock, or Dockerfile
./start-dev.sh --build -d
```

`docker compose restart analysis-dev` is not enough after dependency changes. It restarts the existing container from the existing image, so newly added packages such as `dashscope` will still be missing until the image is rebuilt.

#### Run a Shell in the Container
```bash
# Access the running container
docker compose exec analysis-dev bash

# Inside the container, you can:
# - Run Python interactively
# - Check installed packages
# - Debug issues
```

#### Test API Endpoints

```bash
# Set your API key
export SOW_ANALYSIS_API_KEY="your-api-key"

# Health check
curl http://localhost:8000/api/v1/health

# Submit a test job
curl -X POST http://localhost:8000/api/v1/jobs/analyze \
  -H "Authorization: Bearer $SOW_ANALYSIS_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "audio_url": "s3://your-bucket/hash/audio.mp3",
    "content_hash": "abc123...",
    "options": {"generate_stems": true}
  }'
```

## Running Tests

### Unit Tests (Outside Docker)

```bash
# From project root
cd ops/analysis-service

# Run analysis service tests
uv run --extra dev pytest tests/ -v
```

### Integration Tests (Inside Docker)

```bash
# Start services
docker compose up -d analysis-dev

# Run tests inside container
docker compose exec analysis-dev python -m pytest tests/ -v
```

## Debugging

### Enable Debug Logging

Set the environment variable in your `.env`:
```bash
SOW_LOG_LEVEL=debug
```

Or modify `main.py` temporarily:
```python
logging.basicConfig(
    level=logging.DEBUG,  # Change from INFO to DEBUG
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
```

### Check Stem Separation Backend

Look for the client log line on the first stem separation job:

```
Modal StemSeparator client: app=sow-stems class=StemSeparator max_attempts=2
```

If the job fails instead:
- `ModalStemSeparatorConfigError: MODAL_TOKEN_ID and MODAL_TOKEN_SECRET must be set` —
  the Modal token is missing from the dev container's environment.
- `Modal app/class 'sow-stems/StemSeparator' not found` — the app is not deployed (the
  message contains the `modal deploy` command to run).

### Check Full Analysis Backend

Look for the client log line on the first ANALYZE job:

```
Modal Analyzer client: app=sow-analyzer class=SongAnalyzer max_attempts=2
```

`ModalAnalyzerConfigError` (missing token / app not deployed) fails the job — there is
no in-container fallback.

### Database Inspection

The SQLite job database is stored in the cache volume:
```bash
# Access the database
docker compose exec analysis-dev sqlite3 /cache/jobs.db

# Example queries
.tables
SELECT * FROM jobs LIMIT 10;
SELECT job_id, type, status, stage FROM jobs WHERE status = 'processing';
```

## Troubleshooting

### Hot-Reload Not Working

1. Check that you're using `analysis-dev` service (not `analysis`)
2. Verify volume mount in docker-compose.yml:
   ```yaml
   volumes:
     - ./src:/workspace/src:ro
   ```
3. Check file permissions - the container needs read access

### Import Errors After Adding New Files

The dev server auto-reloads on changes, but new Python files might not be picked up:
```bash
# Restart to pick up new files
docker compose restart analysis-dev
```

### Model Not Found Errors

If the `sow-stems` container reports a model filename it cannot load:
1. Check `SOW_VOCAL_SEPARATION_MODEL` / `SOW_DEREVERB_MODEL` — both are passed to the
   Modal class as parameters and must be filenames `audio-separator` can resolve.
2. Check the container logs: `modal app logs sow-stems`.
3. The download markers (`.downloaded-<model>`) are written only after both
   `load_model()` calls succeed, so a failed download retries on the next cold start
   instead of being skipped. An `HTTPSConnectionPool`/`Network is unreachable` error
   there points at Modal egress or a wrong filename, not at the container.

### Port Already in Use

If port 8000 is taken:
```bash
# Modify docker-compose.yml to use a different port
ports:
  - "8001:8000"  # Map host 8001 to container 8000
```

## Architecture Overview

### Key Files for Development

```
ops/analysis-service/src/sow_analysis/
├── main.py                 # FastAPI app, lifespan management
├── config.py               # Pydantic settings, env var parsing
├── models.py               # Pydantic models for jobs/requests/responses
├── routes/
│   ├── health.py           # Health check endpoint
│   └── jobs.py             # Job submission endpoints
├── services/               # External backends
│   ├── modal_stem_separator_client.py  # sow-stems client (MVSEP failover)
│   ├── modal_whisper_client.py         # sow-whisper client
│   ├── modal_forced_aligner_client.py  # sow-forced-aligner client
│   └── ...
├── storage/
│   ├── cache.py            # Local filesystem caching
│   ├── db.py               # SQLite job persistence
│   └── r2.py               # Cloudflare R2 client
└── workers/
    ├── analyzer.py         # Modal orchestration + cache for full analysis
    ├── lrc.py              # LRC generation (Whisper + LLM)
    ├── queue.py            # Job queue and concurrency management
    └── stem_separation.py  # MVSEP pipeline + sow-stems Modal failover
```

### Adding a New Job Type

1. Add job type to `models.py`:
   ```python
   class JobType(str, Enum):
       ANALYZE = "analyze"
       LRC = "lrc"
       STEM_SEPARATION = "stem_separation"
       YOUR_NEW_TYPE = "your_new_type"
   ```

2. Add request model:
   ```python
   class YourNewJobRequest(BaseModel):
       audio_url: str
       content_hash: str
       options: YourNewOptions
   ```

3. Update `config.py` if new settings needed

4. Update `storage/db.py` schema and `_row_to_job()`

5. Add worker function in `workers/your_worker.py`

6. Add processing method in `workers/queue.py`

7. Add endpoint in `routes/jobs.py`

8. Update `main.py` lifespan if needed

## Development vs Production

| Feature | Development (`analysis-dev`) | Production (`analysis`) |
|---------|------------------------------|-------------------------|
| Code mounting | `./src:/workspace/src:ro` | Copied in build |
| Hot-reload | Yes | No |
| Port | 8000 | 8000 |
| Volume mounts | All dev volumes | Only cache |
| Auto-restart on crash | No | Yes (Docker) |

## Useful Commands Reference

```bash
# Start dev server
docker compose up analysis-dev

# Start in background
docker compose up -d analysis-dev

# Rebuild after dependency or Dockerfile changes
./start-dev.sh --build -d

# Stop
docker compose down

# Rebuild after Dockerfile changes
docker compose up -d --build analysis-dev

# View logs
docker compose logs -f analysis-dev

# Shell into container
docker compose exec analysis-dev bash

# Check running processes
docker compose exec analysis-dev ps aux

# Restart just the analysis service
docker compose restart analysis-dev

# Remove volumes (clear cache and database)
docker compose down -v
```

## Environment Variable Quick Reference

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `SOW_R2_ACCESS_KEY_ID` | Yes | - | R2 access key |
| `SOW_R2_SECRET_ACCESS_KEY` | Yes | - | R2 secret key |
| `SOW_R2_ENDPOINT_URL` | Yes | - | R2 endpoint |
| `SOW_R2_BUCKET` | Yes | - | R2 bucket name |
| `SOW_ANALYSIS_API_KEY` | Yes | - | API auth key |
| `SOW_LLM_API_KEY` | For LRC | - | LLM API key |
| `SOW_LLM_BASE_URL` | For LRC | - | LLM base URL |
| `SOW_LLM_MODEL` | For LRC | - | LLM model name |
| `SOW_EMBEDDING_API_KEY` | For embedding jobs | - | Embedding API key |
| `SOW_EMBEDDING_BASE_URL` | For embedding jobs | - | Embedding base URL |
| `SOW_EMBEDDING_MODEL` | For embedding jobs | text-embedding-3-small | Embedding model name |
| `BPM_ALGORITHM_VERSION` | Optional | v4_octave_guard | Tempo algorithm for Fast Analysis jobs (v4_octave_guard / v5_cps_prior) |
| `MODAL_TOKEN_ID` / `MODAL_TOKEN_SECRET` | For LRC + stems | - | Modal tokens (Whisper, forced alignment, stem separation) |
| `SOW_MODAL_FORCED_ALIGNER_APP_NAME` | Optional | sow-forced-aligner | Deployed forced-aligner app |
| `SOW_MODAL_FORCED_ALIGNER_CLASS_NAME` | Optional | ForcedAligner | Deployed Modal class name |
| `SOW_MODAL_FORCED_ALIGNER_MAX_ATTEMPTS` | Optional | 2 | Retry budget per alignment call |
| `SOW_MODAL_FORCED_ALIGNER_RETRY_DELAY_SECONDS` | Optional | 5.0 | Linear backoff between attempts |
| `SOW_MODAL_FORCED_ALIGNER_MAX_AUDIO_BYTES` | Optional | 90000000 | Cap on the 16 kHz mono WAV upload |
| `SOW_DASHSCOPE_API_KEY` | Optional | - | DashScope API key for Qwen3 ASR |
| `SOW_DASHSCOPE_ASR_REGION` | Optional | intl | DashScope region (intl, cn, us) |
| `SOW_DASHSCOPE_ASR_TIMEOUT_SECONDS` | Optional | 300 | Direct ASR call timeout |
| `SOW_DASHSCOPE_ASR_FILETRANS_TIMEOUT_SECONDS` | Optional | 1800 | Filetrans ASR call timeout |
| `SOW_DASHSCOPE_ASR_MAX_CONCURRENT` | Optional | 2 | Max concurrent ASR requests |
| `SOW_DASHSCOPE_ASR_CONTEXT_MAX_CHARS` | Optional | 10000 | Max context chars for LLM alignment |
| `SOW_MODAL_STEM_APP_NAME` | For stems | sow-stems | Deployed stem-separator app |
| `SOW_MODAL_STEM_CLASS_NAME` | Optional | StemSeparator | Deployed Modal class name |
| `SOW_MODAL_STEM_MAX_ATTEMPTS` | Optional | 2 | Retry budget per stem-separation call |
| `SOW_MODAL_STEM_RETRY_DELAY_SECONDS` | Optional | 5.0 | Linear backoff between attempts |
| `SOW_MODAL_STEM_MAX_AUDIO_BYTES` | Optional | 100000000 | Cap on the uploaded audio |
| `SOW_MODAL_ANALYZER_APP_NAME` | For analyze | sow-analyzer | Deployed full-analysis app |
| `SOW_MODAL_ANALYZER_CLASS_NAME` | Optional | SongAnalyzer | Deployed Modal class name |
| `SOW_MODAL_ANALYZER_MAX_ATTEMPTS` | Optional | 2 | Retry budget per analyzer call |
| `SOW_MODAL_ANALYZER_RETRY_DELAY_SECONDS` | Optional | 5.0 | Linear backoff between attempts |
| `SOW_MODAL_ANALYZER_MAX_AUDIO_BYTES` | Optional | 150000000 | Cap on the uploaded WAV payload |
| `SOW_ANALYSIS_CACHE_VERSION` | Optional | 2 | Version prefix for full-analysis cache entries |
| `SOW_ANALYZE_MAX_CONCURRENT` | Optional | 2 | Max concurrent full-analyze jobs (client side) |
| `SOW_VOCAL_SEPARATION_MODEL` | Optional | model_mel_band_roformer_ep_3005_sdr_11.4360.ckpt | Stage 1 model filename (Modal class parameter) |
| `SOW_DEREVERB_MODEL` | Optional | UVR-De-Echo-Normal.pth | Stage 2 model filename (Modal class parameter) |
