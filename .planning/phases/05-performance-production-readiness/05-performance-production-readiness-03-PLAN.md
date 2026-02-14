---
phase: 05-performance-production-readiness
plan: 03
type: execute
wave: 3
depends_on: ["05-performance-production-readiness-01", "05-performance-production-readiness-02"]
files_modified:
  - docs/qwen3-production.md
  - docker/docker-compose.prod.yml
  - services/qwen3/.env.example
autonomous: true

must_haves:
  truths:
    - "Production deployment documentation exists with environment variable reference"
    - "Docker Compose configuration for production exists"
    - "Environment variables are documented (pydantic-settings)"
    - "Deployment instructions cover model setup and health verification"
  artifacts:
    - path: "docs/qwen3-production.md"
      provides: "Production deployment documentation"
      min_lines: 150
    - path: "docker/docker-compose.prod.yml"
      provides: "Production Docker Compose configuration"
      contains: "services"
    - path: "services/qwen3/.env.example"
      provides: "Environment variable reference file"
  key_links:
    - from: "qwen3-production.md"
      to: "config.py"
      via: "Document all configuration options"
      pattern: "SOW_QWEN3_"
    - from: "docker-compose.prod.yml"
      to: "main.py"
      via: "Port and health check configuration"
      pattern: "8000|health"
---

<objective>
Create production configuration documentation

Purpose: Provide complete documentation for deploying the Qwen3 service in production via Docker Compose, including all configurable environment variables and setup instructions
Output: Production deployment documentation and Docker Compose configuration
</objective>

<execution_context>
@/home/mhuang/.claude/get-shit-done/workflows/execute-plan.md
@/home/mhuang/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/PROJECT.md
@.planning/ROADMAP.md
@services/qwen3/src/sow_qwen3/config.py
@docker/docker-compose.yml
@services/qwen3/Dockerfile
</context>

<tasks>

<task type="auto">
  <name>Create production Docker Compose configuration</name>
  <files>
    docker/docker-compose.prod.yml
  </files>
  <action>
    Create docker/docker-compose.prod.yml for production deployment:

    Based on existing docker-compose.yml, create production-specific configuration:

    ```yaml
    version: "3.8"

    services:
      qwen3:
        build:
          context: ../services/qwen3
          dockerfile: Dockerfile
        container_name: sow-qwen3
        ports:
          - "8000:8000"
        environment:
          - SOW_QWEN3_MODEL_PATH=/models/qwen3-forced-aligner
          - SOW_QWEN3_DEVICE=auto
          - SOW_QWEN3_DTYPE=float32
          - SOW_QWEN3_MAX_CONCURRENT=2
          # R2 configuration (if using)
          - SOW_QWEN3_R2_BUCKET=${SOW_QWEN3_R2_BUCKET:-}
          - SOW_QWEN3_R2_ENDPOINT_URL=${SOW_QWEN3_R2_ENDPOINT_URL:-}
          - SOW_QWEN3_R2_ACCESS_KEY_ID=${SOW_QWEN3_R2_ACCESS_KEY_ID:-}
          - SOW_QWEN3_R2_SECRET_ACCESS_KEY=${SOW_QWEN3_R2_SECRET_ACCESS_KEY:-}
          - SOW_QWEN3_API_KEY=${SOW_QWEN3_API_KEY:-}
        volumes:
          - ./models:/models:ro  # Model files mounted read-only
          - ./cache:/cache
        deploy:
          resources:
            limits:
              cpus: "4.0"
              memory: 8G
            reservations:
              cpus: "2.0"
              memory: 4G
        restart: unless-stopped
        healthcheck:
          test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
          interval: 30s
          timeout: 10s
          retries: 3
          start_period: 180s  # Allow time for model loading at startup
        logging:
          driver: "json-file"
          options:
            max-size: "10m"
            max-file: "3"
    ```

    Key production considerations from CONTEXT.md decisions:
    - Single machine Docker Compose deployment
    - Health check with start_period for model loading time
    - Resource limits to control memory usage
    - Logging rotation to prevent disk fills
    - Model files mounted read-only for security
  </action>
  <verify>cat docker/docker-compose.prod.yml shows qwen3 service with healthcheck, resource limits, and env vars</verify>
  <done>Production Docker Compose configuration created with healthchecks and resource management</done>
</task>

<task type="auto">
  <name>Create environment variable example file</name>
  <files>
    services/qwen3/.env.example
  </files>
  <action>
    Create services/qwen3/.env.example documenting all configurable environment variables:

    Reference all settings from config.py:

    ```bash
    # Model Configuration
    SOW_QWEN3_MODEL_PATH=/models/qwen3-forced-aligner
    # Path to Qwen3 forced aligner model (HuggingFace ID or local path)

    SOW_QWEN3_DEVICE=auto
    # Device to run on: auto (auto-detect), mps (Apple Silicon GPU), cuda (NVIDIA GPU), cpu

    SOW_QWEN3_DTYPE=float32
    # Model precision: float32 (default), float16 (faster, less precise), bfloat16 (recommended for GPUs)

    SOW_QWEN3_MAX_CONCURRENT=2
    # Maximum concurrent alignment requests
    # 2 = balance throughput and memory usage for production
    # 3 = higher throughput if sufficient memory available

    # R2 Storage Configuration (optional)
    SOW_QWEN3_R2_BUCKET=
    # Cloudflare R2 bucket name for audio storage

    SOW_QWEN3_R2_ENDPOINT_URL=
    # R2 API endpoint URL

    SOW_QWEN3_R2_ACCESS_KEY_ID=
    # R2 access key ID

    SOW_QWEN3_R2_SECRET_ACCESS_KEY=
    # R2 secret access key

    # API Security (optional)
    SOW_QWEN3_API_KEY=
    # If set, requests must include X-API-Key header with this value

    # Cache and Processing
    SOW_QWEN3_CACHE_DIR=/cache
    # Directory for cached audio files and temporary data
    ```

    Each variable includes:
    - Variable name
    - Default value (from config.py)
    - Description of purpose
    - Valid options where applicable
  </action>
  <verify>cat services/qwen3/.env.example shows all SOW_QWEN3_ environment variables with descriptions</verify>
  <done>Environment variable reference file created documenting all configurable options</done>
</task>

<task type="auto">
  <name>Create production deployment documentation</name>
  <files>
    docs/qwen3-production.md
  </files>
  <action>
    Create docs/qwen3-production.md with complete production deployment guide:

    Structure:

    ```markdown
    # Qwen3 Service - Production Deployment Guide

    ## Overview

    The Qwen3 Alignment Service is a FastAPI microservice that provides forced alignment for LRC timestamp refinement. This guide covers deployment via Docker Compose for single-machine production environments.

    ## Prerequisites

    - Docker Engine 20.10+
    - Docker Compose v2.0+
    - 4GB+ RAM available for GPU/MPS, 8GB+ for CPU inference
    - Qwen3-ForcedAligner-0.6B model files downloaded (approx 1.2GB)

    ## Model Setup

    1. Download Qwen3-ForcedAligner-0.6B model:
       ```bash
       mkdir -p docker/models
       cd docker/models
       git lfs install
       git clone https://huggingface.co/Qwen/Qwen2-Audio-ForcedAlignment-0.6B qwen3-forced-aligner
       ```

    2. Verify model files:
       ```bash
       ls qwen3-forced-aligner/
       # Should show: config.json, pytorch_model.bin, tokenizer.json, etc.
       ```

    ## Configuration

    Copy `.env.example` to `.env` and configure:

    ```bash
    cd docker
    cp ../services/qwen3/.env.example .env
    # Edit .env with your configuration
    ```

    Key configuration options:
    - `SOW_QWEN3_DEVICE`: Use `auto` for automatic detection, `cuda` for NVIDIA GPU, `mps` for Apple Silicon
    - `SOW_QWEN3_MAX_CONCURRENT`: Set to 2 for production (adjust based on available RAM)
    - `SOW_QWEN3_DTYPE`: Use `float32` for CPU, `float16` or `bfloat16` for GPU

    ## Deployment

    1. Start the service:
       ```bash
       cd docker
       docker-compose -f docker-compose.prod.yml up -d
       ```

    2. Monitor startup logs:
       ```bash
       docker-compose -f docker-compose.prod.yml logs -f qwen3
       ```

    Expected startup sequence:
       ```
       INFO:     Qwen3 Alignment Service starting up
       INFO:     Loading Qwen3ForcedAligner model...
       INFO:     Loading Qwen3ForcedAligner from /models/qwen3-forced-aligner on device=cuda, dtype=float32
       INFO:     Qwen3ForcedAligner loaded and ready
       INFO:     Qwen3 Alignment Service ready
       ```

    3. Verify health status:
       ```bash
       curl http://localhost:8000/health
       # Returns: {"status":"healthy","version":"...","model":"ready",...}
       ```

    4. If health check fails during startup:
       ```bash
       docker-compose -f docker-compose.prod.yml logs qwen3
       # Check for model loading errors
       ```

    ## Resource Requirements

    ### CPU-only inference:
    - CPU: 4 cores recommended
    - RAM: 8GB minimum
    - Estimated alignment time: ~2-3 seconds per minute of audio

    ### GPU (NVIDIA CUDA/MPS):
    - GPU: 4GB VRAM minimum
    - RAM: 4GB host RAM
    - Estimated alignment time: ~0.5-1 second per minute of audio

    ### Concurrency settings:
    - `MAX_CONCURRENT=2`: Recommended for 8GB RAM (2x throughput vs 1x)
    - `MAX_CONCURRENT=3`: Use only if 16GB+ RAM available
    - For CPU-only, keep at 1-2 to avoid OOM

    ## Monitoring and Logging

    ### View logs:
    ```bash
    # Real-time logs
    docker-compose -f docker-compose.prod.yml logs -f qwen3

    # Last 100 lines
    docker-compose -f docker-compose.prod.yml logs --tail=100 qwen3
    ```

    ### Resource monitoring:
    ```bash
    docker stats sow-qwen3
    # Shows CPU, memory usage
    ```

    Log files are rotated (max 10MB, 3 files retained) to prevent disk fills.

    ## Troubleshooting

    ### Service returns 503 on /health:
    - Model failed to load at startup
    - Check logs: `docker-compose logs qwen3`
    - Verify model path: `ls models/qwen3-forced-aligner`
    - Check device compatibility (CUDA available for GPU)

    ### Out of memory errors:
    - Reduce `MAX_CONCURRENT` to 1
    - Change `SOW_QWEN3_DTYPE` to `float16` (may reduce precision)
    - Close other memory-intensive services

    ### Slow alignment performance:
    - Verify GPU is being used: Check DEVICE setting, use `nvidia-smi` or Powermetrics
    - Disk I/O bottleneck: Cache directory on SSD recommended
    - Increase concurrency if memory available

    ## Security Considerations

    - Model files mounted read-only (`:ro` in volumes)
    - Configure `SOW_QWEN3_API_KEY` to require authentication for API requests
    - Run behind reverse proxy (nginx) for SSL and additional auth
    - Restrict access to `/api/v1/align` endpoint in production

    ## Integration with Analysis Service

    Include qwen3 service in main docker-compose.yml:
    ```yaml
    services:
      analysis:
        # ... analysis service config ...
        environment:
          - SOW_ANALYSIS_QWEN3_URL=http://qwen3:8000

      qwen3:
        extends:
          file: docker-compose.prod.yml
          service: qwen3
    ```

    See `docs/analysis-integration.md` for full integration guide.
    ```

    Cover all locked decisions from CONTEXT.md:
    - Docker Compose single-machine deployment
    - Environment variables only (pydantic-settings)
    - Health check endpoint behavior
    - Human-readable text logs
  </action>
  <verify>cat docs/qwen3-production.md shows complete deployment guide with prerequisites, configuration, and troubleshooting</verify>
  <done>Production deployment documentation created with environment variable reference and troubleshooting</done>
</task>

</tasks>

<verification>
Verify production artifacts:

1. Docker Compose config exists:
```bash
cat docker/docker-compose.prod.yml | grep -q "healthcheck" && echo "Healthcheck present"
```

2. Environment variables documented:
```bash
cat services/qwen3/.env.example | grep -c "SOW_QWEN3_"
```

3. Documentation complete:
```bash
wc -l docs/qwen3-production.md
```
</verification>

<success_criteria>
1. docker-compose.prod.yml exists with healthcheck, resource limits, logging
2. .env.example documents all SOW_QWEN3_ environment variables with descriptions
3. Production documentation covers: prerequisites, model setup, configuration, deployment, monitoring, troubleshooting
4. Documentation references environment variable from .env.example
5. Docker Compose configuration uses environment variables (not hardcoded values)
</success_criteria>

<output>
After completion, create `.planning/phases/05-performance-production-readiness/05-performance-production-readiness-03-SUMMARY.md`
</output>
