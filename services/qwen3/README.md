# Stream of Worship Qwen3 Alignment Service

FastAPI microservice for forced alignment of lyrics to audio timestamps using Qwen3-ForcedAligner-0.6B.

## Features

- Aligns known lyrics to audio timestamps (forced alignment, not transcription)
- Supports both LRC and JSON output formats
- Line-level timestamps (original lyric line structure preserved)
- 5-minute audio limit (model constraint)
- Health check endpoint for monitoring
- Docker isolation from other services

## API Endpoints

### POST /api/v1/align

Align lyrics to audio timestamps.

**Request:**
```json
{
  "audio_url": "s3://bucket/audio.mp3",
  "lyrics_text": "第一行歌词\n第二行歌词\n第三行歌词",
  "language": "Chinese",
  "format": "lrc"
}
```

**Parameters:**
- `audio_url` (required): Audio file URL (s3:// format for R2/S3)
- `lyrics_text` (required): Lyrics text to align, one line per newline
- `language` (optional): Language hint (default: "Chinese")
- `format` (optional): Output format - "lrc" or "json" (default: "lrc")

**Response (LRC format):**
```json
{
  "lrc_content": "[00:00.00] 第一行歌词\n[00:05.20] 第二行歌词\n[00:10.40] 第三行歌词",
  "json_data": null,
  "line_count": 3,
  "duration_seconds": 15.5
}
```

**Response (JSON format):**
```json
{
  "lrc_content": null,
  "json_data": [
    {"start_time": 0.0, "end_time": 5.2, "text": "第一行歌词"},
    {"start_time": 5.2, "end_time": 10.4, "text": "第二行歌词"}
  ],
  "line_count": 2,
  "duration_seconds": 15.5
}
```

### GET /health

Check service health (model readiness).

**Response (healthy):**
```json
{
  "status": "healthy",
  "version": "0.1.0",
  "model": "ready",
  "device": "auto",
  "max_concurrent": 1
}
```

**Response (model not loaded):**
- HTTP 503: "Model not loaded"

### GET /

Root endpoint.

**Response:**
```json
{
  "message": "Stream of Worship Qwen3 Alignment Service",
  "version": "0.1.0"
}
```

## Configuration

Environment variables (prefix: `SOW_QWEN3_`):

| Variable | Description | Default |
|----------|-------------|---------|
| `MODEL_PATH` | Path to Qwen3-ForcedAligner model | `/models/qwen3-forced-aligner` |
| `DEVICE` | Device to run on (auto/mps/cuda/cpu) | `auto` |
| `DTYPE` | Data type (bfloat16/float16/float32) | `float32` |
| `MAX_CONCURRENT` | Max concurrent alignments | `1` |
| `CACHE_DIR` | Cache directory | `/cache` |
| `API_KEY` | Optional API key for authentication | (empty) |
| `R2_BUCKET` | R2 bucket name for audio download | (empty) |
| `R2_ENDPOINT_URL` | R2/S3 endpoint URL | (empty) |
| `R2_ACCESS_KEY_ID` | R2 access key ID | (empty) |
| `R2_SECRET_ACCESS_KEY` | R2 secret access key | (empty) |

## Usage

### Docker (Production)

```bash
# Set model volume path (must contain pre-downloaded model)
export SOW_QWEN3_MODEL_VOLUME=/path/to/qwen3-forced-aligner

# Optionally set R2 credentials for audio download
export SOW_QWEN3_R2_BUCKET=your-bucket
export SOW_QWEN3_R2_ENDPOINT_URL=https://your-r2-endpoint.com
export SOW_QWEN3_R2_ACCESS_KEY_ID=your-access-key
export SOW_QWEN3_R2_SECRET_ACCESS_KEY=your-secret-key

# Start service
docker compose up qwen3

# View logs
docker compose logs -f qwen3

# Stop service
docker compose down
```

### Docker (Development)

```bash
# Start with code mount for hot-reload
# Changes to src/ directory will be reflected automatically
docker compose up qwen3-dev
```

### Direct Python

```bash
cd services/qwen3

# Install dependencies
uv sync --extra service

# Set environment variables (or create .env file)
export SOW_QWEN3_MODEL_PATH=/path/to/model
export SOW_QWEN3_DEVICE=auto

# Run service
uv run sow-qwen3
```

## Model Setup

The Qwen3-ForcedAligner-0.6B model must be pre-downloaded to the model volume path.

### Download using huggingface-cli

```bash
pip install huggingface-cli
huggingface-cli download Qwen/Qwen3-ForcedAligner-0.6B --local-dir /path/to/qwen3-forced-aligner
```

### Manual download

Visit [Qwen3-ForcedAligner-0.6B](https://huggingface.co/Qwen/Qwen3-ForcedAligner-0.6B) and download all files.

## Resource Requirements

- **Memory:** 8GB minimum (configured in docker-compose.yml)
- **CPU:** 4 cores minimum
- **Disk:** ~2.4GB for model + cache space

## Error Handling

| Error | HTTP Status | Description |
|-------|-------------|-------------|
| Audio > 5 minutes | 400 | Audio duration exceeds 5 minute limit |
| Missing lyrics | 400 | No lyrics provided in request |
| Audio download failed | 400 | Could not download audio file |
| Invalid API key | 401 | API key authentication failed |
| Model not ready | 503 | Model is not loaded yet |
| Alignment failure | 500 | Alignment process failed |

## API Authentication

If `SOW_QWEN3_API_KEY` is set, all requests must include an `Authorization` header:

```bash
curl -X POST http://localhost:8000/api/v1/align \
  -H "Authorization: Bearer your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "audio_url": "s3://bucket/audio.mp3",
    "lyrics_text": "第一行歌词\n第二行歌词"
  }'
```

If `SOW_QWEN3_API_KEY` is empty (default), authentication is disabled.

## Development

### Running tests

```bash
cd services/qwen3
PYTHONPATH=src uv run pytest tests/
```

### Code structure

```
services/qwen3/
├── src/sow_qwen3/
│   ├── __init__.py
│   ├── config.py          # Configuration
│   ├── main.py            # FastAPI app entry point
│   ├── models.py          # Pydantic models
│   ├── routes/            # API endpoints
│   │   ├── health.py      # Health check
│   │   └── align.py       # Alignment endpoint
│   ├── storage/           # Storage utilities
│   │   └── audio.py       # Audio download
│   └── workers/           # Background workers
│       └── aligner.py     # Qwen3 aligner wrapper
├── tests/                 # Tests
├── Dockerfile             # Container definition
├── docker-compose.yml     # Orchestration
└── pyproject.toml         # Package config
```
