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
cd services/analysis

# Start development server (downloads models if needed)
./start-dev.sh

# Run in detached mode
./start-dev.sh -d

# Or manually with docker compose (requires models to be pre-downloaded)
docker compose up analysis-dev
```

The development service will:
- Download BS-Roformer and UVR-De-Echo models (if not present)
- Mount your local `src/` directory into the container for hot-reload
- Automatically restart on code changes
- Expose the API on `http://localhost:8000`

### 3. Verify It's Running

```bash
# Check health endpoint
curl http://localhost:8000/api/v1/health

# View logs
docker compose logs -f analysis-dev
```

## Model Setup (One-Time)

### Audio-Separator Models (Required for Stem Separation)

Models are automatically downloaded by the startup script (see Quick Start above). The script will:
- Check for missing models (BS-Roformer and UVR-De-Echo)
- Download them to `~/.cache/audio-separator` (or `$SOW_AUDIO_SEPARATOR_MODEL_ROOT` if set)
- Start the development server

To manually download models only (without starting the server):
```bash
cd services/analysis
SOW_AUDIO_SEPARATOR_MODEL_ROOT="$HOME/.cache/audio-separator" ./start-dev.sh --no-start
```

### Qwen3 Models (Required for LRC Refinement)

See the [qwen3 service README](../qwen3/README.md) for model download instructions.

## Development Workflow

### Making Code Changes

1. Edit files in `services/analysis/src/sow_analysis/`
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
# If you need to restart (e.g., after adding new dependencies)
docker compose restart analysis-dev

# Or fully rebuild if Dockerfile changed
docker compose up -d --build analysis-dev
```

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
cd /home/mhuang/Development/stream_of_worship

# Run analysis service tests
PYTHONPATH=services/analysis/src uv run --python 3.11 \
  --extra app --extra test \
  pytest services/analysis/tests/ -v
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

### Check Model Loading

Look for these log messages on startup:
```
Loading audio-separator models...
Loading BS-Roformer model: model_bs_roformer_ep_317_sdr_12.9755.ckpt
Loading UVR-De-Echo model: UVR-De-Echo-Normal.pth
Audio-separator models loaded and ready
```

If you see:
```
AudioSeparatorWrapper not available (audio-separator not installed)
```
- The `audio-separator` package wasn't installed (check pyproject.toml)

If you see:
```
Failed to load audio-separator models: ...
```
- The models aren't at `SOW_AUDIO_SEPARATOR_MODEL_ROOT` - check the path

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

If you see "Model file not found" errors:
1. Check `SOW_AUDIO_SEPARATOR_MODEL_ROOT` is set correctly
2. Verify models were downloaded to that directory
3. Check the bind mount in docker-compose.yml:
   ```yaml
   volumes:
     - ${SOW_AUDIO_SEPARATOR_MODEL_ROOT}:/models/audio-separator:ro
   ```

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
services/analysis/src/sow_analysis/
├── main.py                 # FastAPI app, lifespan management
├── config.py               # Pydantic settings, env var parsing
├── models.py               # Pydantic models for jobs/requests/responses
├── routes/
│   ├── health.py           # Health check endpoint
│   └── jobs.py             # Job submission endpoints
├── storage/
│   ├── cache.py            # Local filesystem caching
│   ├── db.py               # SQLite job persistence
│   └── r2.py               # Cloudflare R2 client
└── workers/
    ├── analyzer.py         # Audio analysis (allin1)
    ├── lrc.py              # LRC generation (Whisper + LLM)
    ├── queue.py            # Job queue and concurrency management
    ├── separator.py        # Demucs stem separation
    ├── stem_separation.py  # BS-Roformer + UVR clean vocals
    └── separator_wrapper.py # AudioSeparator model management
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
| `SOW_QWEN3_MODEL_ROOT` | For Qwen3 | - | HuggingFace cache path |
| `SOW_QWEN3_MODEL_SNAPSHOT` | For Qwen3 | - | Model snapshot hash |
| `SOW_AUDIO_SEPARATOR_MODEL_ROOT` | For stems | - | Audio-separator models path |
| `TARGETPLATFORM` | No | linux/amd64 | Docker build platform |
