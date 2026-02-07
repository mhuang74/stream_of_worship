# Stream of Worship - Analysis Service

The Analysis Service is a FastAPI-based microservice that performs CPU/GPU-intensive audio analysis and processing for the Stream of Worship platform.

## Features

- **Audio Analysis**: Detects tempo (BPM), musical key, mode, loudness, beats, and song sections
- **Stem Separation**: Separates audio into vocals, drums, bass, and other stems using Demucs
- **LRC Generation**: Generates timestamped lyric files using Whisper + LLM alignment
- **R2 Storage**: Uploads/download results to/from Cloudflare R2 (S3-compatible)

## Architecture

This service is designed to run as a long-lived container with async job processing:

- **API Layer**: FastAPI endpoints for job submission and status checking
- **Job Queue**: In-memory queue with configurable concurrency
- **Workers**: Background tasks for analysis, stem separation, and LRC generation
- **Cache**: Local filesystem cache for expensive operations

## Prerequisites

- Docker with Docker Compose
- For GPU support: NVIDIA Container Toolkit (`nvidia-container-toolkit`)
- Cloudflare R2 account (for audio storage)
- OpenAI-compatible API key (for LRC generation)

## Environment Variables

Create a `.env` file in this directory with the following variables:

### Required

```bash
# R2 Storage (Cloudflare R2 credentials)
SOW_R2_BUCKET="your-bucket-name"
SOW_R2_ENDPOINT_URL="https://<account-id>.r2.cloudflarestorage.com"
SOW_R2_ACCESS_KEY_ID="your-access-key"
SOW_R2_SECRET_ACCESS_KEY="your-secret-key"

# API Security (shared secret with Admin CLI)
SOW_ANALYSIS_API_KEY="your-random-api-key"

# LLM Configuration (for LRC generation)
SOW_LLM_API_KEY="sk-or-v1-..."  # OpenRouter, OpenAI, etc.
SOW_LLM_BASE_URL="https://openrouter.ai/api/v1"
SOW_LLM_MODEL="openai/gpt-4o-mini"
```

### Optional

```bash
# Processing Configuration
SOW_MAX_CONCURRENT_JOBS=2      # Number of parallel jobs (default: 2)
SOW_DEMUCS_DEVICE=cpu          # "cpu" or "cuda" (default: cpu)
SOW_WHISPER_DEVICE=cpu         # "cpu" or "cuda" (default: cpu)
```

## Quick Start

### 1. Build and Start the Service

```bash
cd services/analysis

# Build the image
docker compose build

# Start the service
docker compose up -d

# View logs
docker compose logs -f
```

The service will be available at `http://localhost:8000`.

### 2. Verify Health

```bash
curl http://localhost:8000/api/v1/health
```

Expected response:
```json
{
  "status": "healthy",
  "version": "0.1.0",
  "services": {
    "r2": {"status": "configured", "bucket": "your-bucket"},
    "cache": {"status": "healthy", "path": "/cache"}
  }
}
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Service info |
| `/api/v1/health` | GET | Health check |
| `/api/v1/jobs/analyze` | POST | Submit audio analysis job |
| `/api/v1/jobs/lrc` | POST | Submit LRC generation job |
| `/api/v1/jobs/{job_id}` | GET | Get job status and results |

### Submit Analysis Job

```bash
curl -X POST http://localhost:8000/api/v1/jobs/analyze \
  -H "Authorization: Bearer $SOW_ANALYSIS_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "audio_url": "s3://your-bucket/hash/audio.mp3",
    "content_hash": "abc123...",
    "options": {
      "generate_stems": true,
      "stem_model": "htdemucs",
      "force": false
    }
  }'
```

### Submit LRC Generation Job

```bash
curl -X POST http://localhost:8000/api/v1/jobs/lrc \
  -H "Authorization: Bearer $SOW_ANALYSIS_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "audio_url": "s3://your-bucket/hash/audio.mp3",
    "content_hash": "abc123...",
    "lyrics_text": "Line 1\nLine 2\nLine 3",
    "options": {
      "whisper_model": "large-v3",
      "llm_model": "openai/gpt-4o-mini",
      "use_vocals_stem": true,
      "language": "zh"
    }
  }'
```

### Check Job Status

```bash
curl http://localhost:8000/api/v1/jobs/job_abc123 \
  -H "Authorization: Bearer $SOW_ANALYSIS_API_KEY"
```

## Platform-Specific Builds

The Dockerfile supports platform-specific builds for PyTorch and NATTEN:

### x86_64 (Linux/AMD64) - CPU-only PyTorch

```bash
export TARGETPLATFORM=linux/amd64
docker compose build
```

### ARM64 (Apple Silicon) - Standard PyTorch

```bash
export TARGETPLATFORM=linux/arm64
docker compose build
```

## GPU Support

To enable GPU acceleration for Demucs and Whisper:

1. Install NVIDIA Container Toolkit:
   ```bash
   # Ubuntu/Debian
   sudo apt-get install -y nvidia-container-toolkit
   sudo systemctl restart docker
   ```

2. Set environment variables:
   ```bash
   export SOW_DEMUCS_DEVICE=cuda
   export SOW_WHISPER_DEVICE=cuda
   ```

3. Uncomment the GPU section in `docker-compose.yml`:
   ```yaml
   deploy:
     resources:
       reservations:
         devices:
           - driver: nvidia
             count: 1
             capabilities: [ gpu ]
   ```

4. Start the service:
   ```bash
   docker compose up -d
   ```

If you don't have a GPU, the service will run fine on CPU with the default configuration.

## Stopping the Service

```bash
# Stop and remove containers
docker compose down

# Stop and remove containers + volumes (clears cache)
docker compose down -v
```

## Troubleshooting

### Service fails to start with "missing_credentials"

Check that all required R2 environment variables are set:
```bash
echo $SOW_R2_ACCESS_KEY_ID
echo $SOW_R2_SECRET_ACCESS_KEY
```

### LRC jobs fail with "SOW_LLM_API_KEY not set"

You must set all three LLM variables:
```bash
export SOW_LLM_API_KEY="your-key"
export SOW_LLM_BASE_URL="https://openrouter.ai/api/v1"
export SOW_LLM_MODEL="openai/gpt-4o-mini"
```

### GPU not detected

Verify NVIDIA Container Toolkit is installed:
```bash
docker run --rm --gpus all nvidia/cuda:12.0-base nvidia-smi
```

If this fails, GPU support is not properly configured.

## Development

### Project Structure

```
services/analysis/
├── docker-compose.yml          # Docker Compose configuration
├── Dockerfile                  # Multi-platform Docker build
├── pyproject.toml             # Python dependencies
├── README.md                  # This file
└── src/sow_analysis/
    ├── __init__.py
    ├── main.py                # FastAPI app entry point
    ├── config.py              # Pydantic settings
    ├── models.py              # Pydantic models
    ├── routes/                # API endpoints
    │   ├── health.py
    │   └── jobs.py
    ├── storage/               # R2 and cache clients
    │   ├── cache.py
    │   └── r2.py
    └── workers/               # Background job processors
        ├── analyzer.py
        ├── lrc.py
        ├── queue.py
        └── separator.py
```

### Running Tests

```bash
# From project root
pytest tests/services/analysis/ -v
```

## License

Part of the Stream of Worship project.
