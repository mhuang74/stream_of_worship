---
phase: 01-qwen3-service-foundation
plan: 04
type: execute
wave: 4
depends_on: ["01-qwen3-service-foundation-01", "01-qwen3-service-foundation-02", "01-qwen3-service-foundation-03"]
files_modified: [services/qwen3/Dockerfile, services/qwen3/docker-compose.yml, services/qwen3/README.md]
autonomous: true

must_haves:
  truths:
    - "Dockerfile uses python:3.11-slim base image"
    - "Docker service has 8GB memory limit and 4 CPU cores limit"
    - "Model mounted from host volume at /models/qwen3-forced-aligner"
    - "Service runs isolated from other services (no PyTorch conflicts)"
    - "Service exposes port 8000 for API access"
  artifacts:
    - path: "services/qwen3/Dockerfile"
      provides: "Container image for Qwen3 service"
      contains: "FROM python:3.11-slim", "qwen-asr", "uvicorn"
    - path: "services/qwen3/docker-compose.yml"
      provides: "Orchestration with resource constraints"
      contains: "mem_limit: 8g", "cpus: '4'", "volumes"
    - path: "services/qwen3/README.md"
      provides: "Service documentation"
      contains: "Qwen3 Alignment Service", "/api/v1/align"
  key_links:
    - from: "services/qwen3/Dockerfile"
      to: "services/qwen3/src/sow_qwen3/main.py"
      via: "uvicorn entry point"
      pattern: "CMD.*uvicorn sow_qwen3.main:app"
    - from: "services/qwen3/docker-compose.yml"
      to: "services/qwen3/Dockerfile"
      via: "build context"
      pattern: "build:.*context: \."
---

<objective>
Create Docker configuration for isolated deployment of Qwen3 Alignment Service.

Purpose: Containerize the service with explicit resource constraints (8GB memory, 4 CPUs), model volume mount, and isolated PyTorch environment. This ensures the service runs independently without conflicting with other services and has adequate resources for ML inference.

Output: Dockerfile, docker-compose.yml, and README.md documentation.
</objective>

<execution_context>
@/home/mhuang/.claude/get-shit-done/workflows/execute-plan.md
@/home/mhuang/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/PROJECT.md
@.planning/ROADMAP.md
@.planning/STATE.md
@.planning/phases/01-qwen3-service-foundation/01-qwen3-service-foundation-RESEARCH.md
@.planning/phases/01-qwen3-service-foundation/01-qwen3-service-foundation-CONTEXT.md

# Reference Docker configuration from Analysis Service
@/home/mhuang/Development/stream_of_worship/services/analysis/Dockerfile
@/home/mhuang/Development/stream_of_worship/services/analysis/docker-compose.yml

# Service code from previous plans
@services/qwen3/src/sow_qwen3/main.py
@services/qwen3/pyproject.toml
</context>

<tasks>

<task type="auto">
  <name>Task 1: Create Dockerfile with isolated Python 3.11 environment</name>
  <files>services/qwen3/Dockerfile</files>
  <action>
Create services/qwen3/Dockerfile following Analysis Service pattern:

1. Base image: FROM python:3.11-slim

2. Platform arg: ARG TARGETPLATFORM

3. Development mode flag: ARG DEV_MODE=false

4. WORKDIR /workspace

5. Install system dependencies:
   - RUN apt-get update && apt-get install -y ffmpeg libsndfile1 && rm -rf /var/lib/apt/lists/*

6. Install uv:
   - COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
   - ENV UV_SYSTEM_PYTHON=1
   - ENV UV_COMPILE_BYTECODE=1

7. Install dependencies:
   - COPY pyproject.toml .
   - RUN uv pip install --no-cache --no-build-isolation -e ".[service]"

   Note: qwen-asr includes torch as dependency, no need for separate torch install.

8. Copy code (only in production mode, overridden by volume in dev):
   - COPY src/ ./src/

9. Create directories:
   - RUN mkdir -p /cache /models

10. Expose port: EXPOSE 8000

11. CMD with reload support:
   - CMD if [ "$DEV_MODE" = "true" ]; then uvicorn sow_qwen3.main:app --host 0.0.0.0 --port 8000 --reload --reload-dir /workspace/src; else uvicorn sow_qwen3.main:app --host 0.0.0.0 --port 8000; fi

Simplified from Analysis Service (no NATTEN, no platform-specific PyTorch install since qwen-asr handles it).

DO NOT include: NATTEN install, allin1/demucs/madmom dependencies, platform-specific torch install.
  </action>
  <verify>
grep -q "FROM python:3.11-slim" services/qwen3/Dockerfile && grep -q "qwen-asr" services/qwen3/pyproject.toml
  </verify>
  <done>
Dockerfile created with python:3.11-slim base, uv installed, service entry point via uvicorn
  </done>
</task>

<task type="auto">
  <name>Task 2: Create docker-compose.yml with resource constraints</name>
  <files>services/qwen3/docker-compose.yml, services/qwen3/.env.example</files>
  <action>
Create services/qwen3/docker-compose.yml:

version: '3.8'

# Common environment variables
x-common-env: &common-env
  SOW_QWEN3_MODEL_PATH: /models/qwen3-forced-aligner
  SOW_QWEN3_DEVICE: ${SOW_QWEN3_DEVICE:-auto}
  SOW_QWEN3_DTYPE: ${SOW_QWEN3_DTYPE:-float32}
  SOW_QWEN3_MAX_CONCURRENT: ${SOW_QWEN3_MAX_CONCURRENT:-1}
  SOW_QWEN3_CACHE_DIR: /cache
  SOW_QWEN3_API_KEY: ${SOW_QWEN3_API_KEY:-}
  SOW_R2_BUCKET: ${SOW_R2_BUCKET:-}
  SOW_R2_ENDPOINT_URL: ${SOW_R2_ENDPOINT_URL:-}
  SOW_R2_ACCESS_KEY_ID: ${SOW_R2_ACCESS_KEY_ID:-}
  SOW_R2_SECRET_ACCESS_KEY: ${SOW_R2_SECRET_ACCESS_KEY:-}

services:
  qwen3:
    build:
      context: .
      dockerfile: Dockerfile
      args:
        - TARGETPLATFORM=${TARGETPLATFORM:-linux/amd64}
    ports:
      - "8000:8000"
    environment:
      <<: *common-env
    volumes:
      - qwen3-cache:/cache
      - ${SOW_QWEN3_MODEL_VOLUME}:/models/qwen3-forced-aligner:ro
    deploy:
      resources:
        limits:
          memory: 8g
          cpus: '4'

  # Development mode with code mount
  qwen3-dev:
    build:
      context: .
      dockerfile: Dockerfile
      args:
        - TARGETPLATFORM=${TARGETPLATFORM:-linux/amd64}
        - DEV_MODE=true
    ports:
      - "8000:8000"
    environment:
      <<: *common-env
      DEV_MODE: "true"
    volumes:
      - qwen3-cache:/cache
      - ${SOW_QWEN3_MODEL_VOLUME}:/models/qwen3-forced-aligner:ro
      - ./src:/workspace/src:ro
    deploy:
      resources:
        limits:
          memory: 8g
          cpus: '4'

volumes:
  qwen3-cache:

Create .env.example with all environment variables documented.

Following Analysis Service docker-compose.yml pattern but with Qwen3-specific env vars and explicit resource limits.

DO NOT include: GPU deployment section (out of scope for initial implementationAnalysis Service specific).
  </action>
  <verify>
grep -q "mem_limit: 8g" services/qwen3/docker-compose.yml && grep -q "cpus: '4'" services/qwen3/docker-compose.yml
  </verify>
  <done>
docker-compose.yml created with 8GB memory limit and 4 CPU limit, model volume mount
  </done>
</task>

<task type="auto">
  <name>Task 3: Create README.md with service documentation</name>
  <files>services/qwen3/README.md</files>
  <action>
Create services/qwen3/README.md with:

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
  "audio_url": "https://r2.example.com/audio.mp3",
  "lyrics_text": "第一行歌词\n第二行歌词\n第三行歌词",
  "language": "Chinese",
  "format": "lrc"
}
```

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

### GET /api/v1/health

Check service health (model readiness).

**Response (healthy):**
```json
{
  "status": "healthy",
  "version": "0.1.0",
  "model": "ready"
}
```

**Response (model not loaded):**
- HTTP 503: "Model not loaded"

## Configuration

Environment variables (prefix: SOW_QWEN3_):

- `MODEL_PATH`: Path to Qwen3-ForcedAligner model (default: /models/qwen3-forced-aligner)
- `DEVICE`: Device to run on (auto/mps/cuda/cpu, default: auto)
- `DTYPE`: Data type (bfloat16/float16/float32, default: float32)
- `MAX_CONCURRENT`: Max concurrent alignments (default: 1)
- `CACHE_DIR`: Cache directory (default: /cache)
- `API_KEY`: Optional API key for authentication (default: empty)
- `R2_BUCKET`: R2 bucket name for audio download
- `R2_ENDPOINT_URL`: R2/S3 endpoint URL
- `R2_ACCESS_KEY_ID`: R2 access key ID
- `R2_SECRET_ACCESS_KEY`: R2 secret access key

## Usage

### Docker (Production)

```bash
# Set model volume path
export SOW_QWEN3_MODEL_VOLUME=/path/to/qwen3-forced-aligner

# Start service
docker compose up qwen3
```

### Docker (Development)

```bash
# Start with code mount for hot-reload
docker compose up qwen3-dev
```

### Direct Python

```bash
cd services/qwen3
poetry install
poetry run sow-qwen3
```

## Model Setup

The Qwen3-ForcedAligner-0.6B model must be pre-downloaded to the model volume path.

Download using huggingface-cli:
```bash
pip install huggingface-cli
huggingface-cli download Qwen/Qwen3-ForcedAligner-0.6B --local-dir /path/to/qwen3-forced-aligner
```

## Resource Requirements

- Memory: 8GB minimum (configured in docker-compose.yml)
- CPU: 4 cores minimum
- Disk: ~2.4GB for model + cache

## Error Handling

- **Audio > 5 minutes**: HTTP 400 Bad Request
- **Missing lyrics**: HTTP 400 Bad Request
- **Model not ready**: HTTP 503 Service Unavailable
- **Alignment failure**: HTTP 500 Internal Server Error
- **Invalid API key**: HTTP 401 Unauthorized

Following Analysis Service README pattern.

DO NOT include: GPU deployment instructions, advanced configuration (keep focused on v1 basic usage).
  </action>
  <verify>
grep -q "POST /api/v1/align" services/qwen3/README.md && grep -q "Health check endpoint" services/qwen3/README.md
  </verify>
  <done>
README.md created with API documentation, configuration, usage instructions
  </done>
</task>

</tasks>

<verification>
- Verify Dockerfile builds: docker-compose build qwen3
- Verify docker-compose syntax: docker-compose config
- Verify resource constraints: check docker-compose.yml for mem_limit and cpus
- Verify README completeness: API docs, configuration, usage sections all present
</verification>

<success_criteria>
- Dockerfile uses python:3.11-slim base and installs qwen-asr
- docker-compose.yml has 8GB memory limit and 4 CPU limit
- Model volume mount configured at /models/qwen3-forced-aligner
- Service exposes port 8000
- README.md documents POST /api/v1/align and GET /api/v1/health endpoints
- Service runs in isolated container (no shared dependencies with Analysis Service)
  </success_criteria>

<output>
After completion, create `.planning/phases/01-qwen3-service-foundation/01-qwen3-service-foundation-04-SUMMARY.md`
</output>
