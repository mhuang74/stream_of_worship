# Stream of Worship - Analysis Service Deployment Guide

This guide covers deploying the Analysis Service to a production environment using pre-built Docker images.

## Overview

The Analysis Service is a single microservice (port 8000) that handles audio analysis, stem separation, LRC generation, and forced alignment in-process.

## Prerequisites

### Host Requirements

| Resource | Minimum | Recommended |
|----------|---------|-------------|
| RAM | 8 GB | 16 GB |
| CPU Cores | 4 | 8+ |
| Disk Space | 20 GB | 50 GB |
| OS | Linux (Ubuntu 20.04+) | Ubuntu 22.04 LTS |

### Required Software

- Docker Engine 24.0+ with Docker Compose plugin
- Internet access (for pulling images; ML weights live in Modal volumes)

### External Services

- **Cloudflare R2**: For audio file storage (S3-compatible)
- **LLM API**: OpenAI-compatible endpoint (OpenRouter, OpenAI, etc.)
- **Container Registry**: To store/pull pre-built images

---

## Step 1: Prepare the Host

### Install Docker

```bash
# Update package index
sudo apt-get update

# Install prerequisites
sudo apt-get install -y ca-certificates curl gnupg lsb-release

# Add Docker's official GPG key
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | \
    sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg

# Add Docker repository
echo \
    "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
    https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | \
    sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

# Install Docker
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# Verify installation
sudo docker --version
sudo docker compose version

# Add user to docker group (logout/login required)
sudo usermod -aG docker $USER
```

---

## Step 2: Deploy the Modal Apps

No ML weights are ever downloaded on the host or into the analysis container — all
models live in Modal volumes after a one-time `modal deploy` per app.

### 2.1 Stem Separator (no host download)

Stem separation runs on Modal (the `sow-stems` app), so there are no
`~/.cache/audio-separator` models to download and no `/models/audio-separator` volume
to mount. Deploy the app once with the Modal token; the `sow-stems-models` volume then
caches both model files on Modal's side — see the Modal section in `README.md`
("Stem separation on Modal").

```bash
# From ops/analysis-service/, with MODAL_TOKEN_ID/MODAL_TOKEN_SECRET exported
uv run --no-project --with 'modal>=1.2.6,<2' modal deploy modal_apps/stem_separator_app.py
```

### 2.2 Forced Aligner (no download)

Forced alignment runs on Modal (the `sow-forced-aligner` app), so there is no local
Qwen3-ForcedAligner checkpoint to download in this step. Deploy the app once with the
Modal token and the volume handles the weights — see the Modal section in
`README.md` ("Forced alignment on Modal").

### 2.3 Full Analyzer (no download)

Full analysis (allin1 harmonix-all, Demucs `htdemucs`) runs on Modal (the
`sow-analyzer` app, `modal_apps/analyzer_app.py`). Deploy it once; the
`sow-analyzer-models` volume caches all weights on Modal's side:

```bash
# From ops/analysis-service/, with MODAL_TOKEN_ID/MODAL_TOKEN_SECRET exported
uv run --no-project --with 'modal>=1.2.6,<2' modal deploy modal_apps/analyzer_app.py
```

First cold start downloads harmonix-all (8 checkpoints) + htdemucs into the volume;
allow up to 30 minutes of startup time (`startup_timeout=1800`).

### 2.3.1 Downbeat Detection (no download)

Beat/downbeat grid detection for component analysis runs on its own small app
(`sow-downbeats`, `modal_apps/downbeats_app.py`: torch cu126 + madmom-infer's
torch BLSTM backend, no allin1/NATTEN/Demucs). It keeps the beat-grid cache miss
off the multi-GB `sow-analyzer` image. Deploy it alongside the analyzer:

```bash
# From ops/analysis-service/, with MODAL_TOKEN_ID/MODAL_TOKEN_SECRET exported
uv run --no-project --with 'modal>=1.2.6,<2' modal deploy modal_apps/downbeats_app.py
```

Component analysis silently degrades to beat snapping (no `snap_to_downbeat`) when
this app is undeployed, so deploy it before relying on downbeat snapping. First
cold start downloads the 8 BLSTM weight files (~30 MB, sha256-verified) from
GitHub; no volume is involved.

### 2.4 Model Storage Summary

| Model | Location | Size |
|-------|----------|------|
| Stem separator (MelBand Roformer + UVR-De-Echo) | Modal volume `sow-stems-models` | ~2-3 GB (Modal-side) |
| Qwen3 Forced Aligner | Modal volume `sow-forced-aligner-models` | ~2.4 GB (Modal-side) |
| Whisper | Modal volume `sow-whisper-models` | ~1-3 GB per model (Modal-side) |
| Full analysis (harmonix-all + htdemucs) | Modal volume `sow-analyzer-models` | ~1.5 GB (Modal-side) |
| Beat-grid detection (madmom BLSTM x8) | `sow-downbeats` container cache (per cold start, sha256-verified download) | ~30 MB (Modal-side) |
| **Total (host)** | | **~0 GB** (all model weights live in Modal volumes) |

---

## Step 3: Create Environment Configuration

Create a `.env` file on the host. This file contains all sensitive configuration and is **never committed to git**.

### 3.1 Create the .env File

```bash
# Create deployment directory
mkdir -p ~/sow-deployment
cd ~/sow-deployment

# Create .env file
cat > .env << 'ENVFILE'
# ============================================================================
# Stream of Worship - Analysis Service - Production Environment Configuration
# ============================================================================
# Copy this file to your deployment host and fill in all required values.
# NEVER commit this file to version control.
# ============================================================================

# =============================================================================
# R2 STORAGE CONFIGURATION (REQUIRED)
# =============================================================================
# Cloudflare R2 credentials for audio file storage
# Get from Cloudflare R2 dashboard: Manage R2 API Tokens

SOW_R2_ACCESS_KEY_ID=""
SOW_R2_SECRET_ACCESS_KEY=""
SOW_R2_ENDPOINT_URL=""  # e.g., https://<account-id>.r2.cloudflarestorage.com
SOW_R2_BUCKET=""

# =============================================================================
# API SECURITY (REQUIRED)
# =============================================================================
# Generate secure random keys:
#   openssl rand -base64 32
# These must match the values used by the Admin CLI

SOW_ANALYSIS_API_KEY=""
SOW_ADMIN_API_KEY=""    # Different from SOW_ANALYSIS_API_KEY

# =============================================================================
# LLM CONFIGURATION (REQUIRED for LRC generation)
# =============================================================================
# OpenAI-compatible API for lyric alignment
# Supports: OpenRouter, OpenAI, NeuralWatt, etc.

SOW_LLM_API_KEY=""
SOW_LLM_BASE_URL=""     # e.g., https://openrouter.ai/api/v1
SOW_LLM_MODEL=""        # e.g., openai/gpt-4o-mini, or "deepseek-v4.1-flash:low" (model:effort suffix)

# =============================================================================
# YOUTUBE PROXY (OPTIONAL — rotating residential proxy for transcript fetches)
# =============================================================================
# A port range expands to one proxy URL per port (inclusive, max 64 ports);
# each request picks one at random. A URL without a range is used as-is.

SOW_YOUTUBE_PROXY=""            # e.g., http://user:pass@host:10001-10010
SOW_YOUTUBE_PROXY_RETRIES=10    # Retries on HTTP 429 (default 10); each retry rotates the port

# =============================================================================
# EMBEDDING CONFIGURATION (REQUIRED for embedding generation)
# =============================================================================
# OpenAI-compatible API for song/line embeddings.
# Can use a different provider than SOW_LLM_*.

SOW_EMBEDDING_API_KEY=""
SOW_EMBEDDING_BASE_URL=""     # e.g., https://api.openai.com/v1
SOW_EMBEDDING_MODEL="text-embedding-3-small"

# =============================================================================
# STEM SEPARATOR CONFIGURATION (Modal failover)
# =============================================================================
# Stem separation runs on the sow-stems Modal app and reuses
# MODAL_TOKEN_ID / MODAL_TOKEN_SECRET from the Whisper section above.
# Model filenames are Modal class parameters.

SOW_VOCAL_SEPARATION_MODEL="model_mel_band_roformer_ep_3005_sdr_11.4360.ckpt"
SOW_DEREVERB_MODEL="UVR-De-Echo-Normal.pth"
SOW_MODAL_STEM_MAX_ATTEMPTS=2
SOW_MODAL_STEM_MAX_AUDIO_BYTES=100000000

# =============================================================================
# OPTIONAL: PROCESSING CONFIGURATION
# =============================================================================

# Fast Analysis (librosa-only) concurrency (default: 0 = auto-detect, capped at 4)
SOW_FAST_ANALYZE_MAX_CONCURRENT=0

# Full analysis concurrency (default: 2; Modal sow-analyzer runs max_containers=2)
SOW_ANALYZE_MAX_CONCURRENT=2

# Tempo detection algorithm for Fast Analysis jobs
# Allowed: v4_octave_guard (default) or v5_cps_prior
BPM_ALGORITHM_VERSION=v4_octave_guard

# Whisper, forced alignment, stem separation and full analysis run on Modal;
# fill these in on the server. Deploy each app once (see README.md "Whisper on
# Modal", "Forced alignment on Modal", "Stem separation on Modal"):
#   modal deploy modal_apps/whisper_app.py
#   modal deploy modal_apps/forced_aligner_app.py
#   modal deploy modal_apps/stem_separator_app.py
#   modal deploy modal_apps/analyzer_app.py
#   modal deploy modal_apps/downbeats_app.py
MODAL_TOKEN_ID=""
MODAL_TOKEN_SECRET=""
SOW_MODAL_WHISPER_ALLOWED_MODELS="tiny,base,small,medium,large-v3,large-v3-turbo,distil-large-v3"
SOW_WHISPER_CACHE_VERSION=2
SOW_MODAL_ANALYZER_APP_NAME="sow-analyzer"
SOW_MODAL_ANALYZER_CLASS_NAME="SongAnalyzer"
SOW_MODAL_ANALYZER_MAX_ATTEMPTS=2
SOW_MODAL_ANALYZER_RETRY_DELAY_SECONDS=5.0
SOW_MODAL_ANALYZER_MAX_AUDIO_BYTES=150000000
SOW_MODAL_DOWNBEATS_APP_NAME="sow-downbeats"
SOW_MODAL_DOWNBEATS_CLASS_NAME="DownbeatDetector"
SOW_MODAL_DOWNBEATS_MAX_ATTEMPTS=2
SOW_MODAL_DOWNBEATS_RETRY_DELAY_SECONDS=5.0
SOW_MODAL_DOWNBEATS_MAX_AUDIO_BYTES=150000000
SOW_ANALYSIS_CACHE_VERSION=2

# =============================================================================
# OPTIONAL: MVSEP CLOUD API (for cloud-based stem separation)
# =============================================================================
# Leave empty to run stem separation on the sow-stems Modal app only
# Get token from: https://mvsep.com/

SOW_MVSEP_API_KEY=""
SOW_MVSEP_ENABLED=true
SOW_MVSEP_STAGE1_SEP_TYPE=48
SOW_MVSEP_STAGE1_ADD_OPT1=11
SOW_MVSEP_STAGE2_SEP_TYPE=22
SOW_MVSEP_STAGE2_ADD_OPT1=0
SOW_MVSEP_STAGE2_ADD_OPT2=1

# =============================================================================
# DOCKER IMAGE CONFIGURATION
# =============================================================================
# Update these when deploying new versions

SOW_ANALYSIS_IMAGE="ghcr.io/your-org/sow-analysis:latest"

ENVFILE
```

### 3.2 Fill in Required Values

Edit the `.env` file and fill in all required values:

```bash
# Use your preferred editor
nano ~/sow-deployment/.env
# or
vim ~/sow-deployment/.env
```

**Required fields to fill:**

| Variable | Description | How to Obtain |
|----------|-------------|---------------|
| `SOW_R2_ACCESS_KEY_ID` | R2 access key | Cloudflare R2 Dashboard → Manage R2 API Tokens |
| `SOW_R2_SECRET_ACCESS_KEY` | R2 secret key | Cloudflare R2 Dashboard → Manage R2 API Tokens |
| `SOW_R2_ENDPOINT_URL` | R2 endpoint | `https://<account-id>.r2.cloudflarestorage.com` |
| `SOW_R2_BUCKET` | R2 bucket name | Your R2 bucket name |
| `SOW_ANALYSIS_API_KEY` | Service API key | `openssl rand -base64 32` |
| `SOW_ADMIN_API_KEY` | Admin API key | `openssl rand -base64 32` (different from above) |
| `SOW_LLM_API_KEY` | LLM API key | OpenRouter, OpenAI, etc. |
| `SOW_LLM_BASE_URL` | LLM endpoint | e.g., `https://openrouter.ai/api/v1` |
| `SOW_LLM_MODEL` | LLM model | e.g., `openai/gpt-4o-mini`; a `model:effort` suffix (`deepseek-v4.1-flash:low`) is sent as `reasoning_effort` instead of the model id |
| `SOW_EMBEDDING_API_KEY` | Embedding API key | OpenAI-compatible embedding provider |
| `SOW_EMBEDDING_BASE_URL` | Embedding endpoint | e.g., `https://api.openai.com/v1` |
| `SOW_EMBEDDING_MODEL` | Embedding model | default `text-embedding-3-small` |
| `MODAL_TOKEN_ID` / `MODAL_TOKEN_SECRET` | Modal tokens | Whisper, forced alignment, stem separation on Modal |
| `SOW_VOCAL_SEPARATION_MODEL` / `SOW_DEREVERB_MODEL` | Stem model filenames | Defaults are fine; change only if you want different Modal models |

### 3.3 Secure the .env File

```bash
# Restrict permissions
chmod 600 ~/sow-deployment/.env

# Verify
ls -la ~/sow-deployment/.env
# Should show: -rw------- (readable/writable by owner only)
```

---

## Step 4: Create Docker Compose Configuration

Create a `docker-compose.yml` file optimized for production deployment with pre-built images.

```bash
cat > ~/sow-deployment/docker-compose.yml << 'YAML'
# ============================================================================
# Stream of Worship - Analysis Service - Production Deployment
# Using pre-built Docker images
# ============================================================================

version: "3.8"

services:
  # ==========================================================================
  # Analysis Service - Main API (Port 8000)
  # ==========================================================================
  analysis:
    image: ${SOW_ANALYSIS_IMAGE:-ghcr.io/your-org/sow-analysis:latest}
    container_name: sow-analysis
    restart: unless-stopped
    ports:
      - "8000:8000"
    environment:
      # R2 Storage
      SOW_R2_BUCKET: ${SOW_R2_BUCKET}
      SOW_R2_ENDPOINT_URL: ${SOW_R2_ENDPOINT_URL}
      SOW_R2_ACCESS_KEY_ID: ${SOW_R2_ACCESS_KEY_ID}
      SOW_R2_SECRET_ACCESS_KEY: ${SOW_R2_SECRET_ACCESS_KEY}
      
      # API Security
      SOW_ANALYSIS_API_KEY: ${SOW_ANALYSIS_API_KEY}
      SOW_ADMIN_API_KEY: ${SOW_ADMIN_API_KEY}
      
      # LLM Configuration
      SOW_LLM_API_KEY: ${SOW_LLM_API_KEY}
      SOW_LLM_BASE_URL: ${SOW_LLM_BASE_URL}
      SOW_LLM_MODEL: ${SOW_LLM_MODEL}

      # Embedding Configuration
      SOW_EMBEDDING_API_KEY: ${SOW_EMBEDDING_API_KEY}
      SOW_EMBEDDING_BASE_URL: ${SOW_EMBEDDING_BASE_URL}
      SOW_EMBEDDING_MODEL: ${SOW_EMBEDDING_MODEL:-text-embedding-3-small}
      
      # Whisper runs on Modal (modal_apps/whisper_app.py); tokens come from /opt/sow/.env
      MODAL_TOKEN_ID: ${MODAL_TOKEN_ID}
      MODAL_TOKEN_SECRET: ${MODAL_TOKEN_SECRET}
      SOW_MODAL_WHISPER_ALLOWED_MODELS: ${SOW_MODAL_WHISPER_ALLOWED_MODELS:-tiny,base,small,medium,large-v3,large-v3-turbo,distil-large-v3}
      SOW_WHISPER_CACHE_VERSION: ${SOW_WHISPER_CACHE_VERSION:-2}
      
      # Processing Configuration
      SOW_ANALYZE_MAX_CONCURRENT: ${SOW_ANALYZE_MAX_CONCURRENT:-2}
      
      # Fast Analysis (librosa-only) Concurrency
      SOW_FAST_ANALYZE_MAX_CONCURRENT: ${SOW_FAST_ANALYZE_MAX_CONCURRENT:-0}
      # BPM Algorithm (Fast Analysis tempo detection)
      BPM_ALGORITHM_VERSION: ${BPM_ALGORITHM_VERSION:-v4_octave_guard}
      
      # Stem separator runs on Modal (modal_apps/stem_separator_app.py); it reuses the
      # MODAL_TOKEN_* values above. Model filenames are Modal class parameters.
      SOW_VOCAL_SEPARATION_MODEL: ${SOW_VOCAL_SEPARATION_MODEL:-model_mel_band_roformer_ep_3005_sdr_11.4360.ckpt}
      SOW_DEREVERB_MODEL: ${SOW_DEREVERB_MODEL:-UVR-De-Echo-Normal.pth}
      SOW_MODAL_STEM_MAX_ATTEMPTS: ${SOW_MODAL_STEM_MAX_ATTEMPTS:-2}
      SOW_MODAL_STEM_MAX_AUDIO_BYTES: ${SOW_MODAL_STEM_MAX_AUDIO_BYTES:-100000000}
      
      # MVSEP Configuration
      SOW_MVSEP_API_KEY: ${SOW_MVSEP_API_KEY:-}
      SOW_MVSEP_ENABLED: ${SOW_MVSEP_ENABLED:-true}
      SOW_MVSEP_STAGE1_SEP_TYPE: ${SOW_MVSEP_STAGE1_SEP_TYPE:-48}
      SOW_MVSEP_STAGE1_ADD_OPT1: ${SOW_MVSEP_STAGE1_ADD_OPT1:-11}
      SOW_MVSEP_STAGE1_ADD_OPT2: ${SOW_MVSEP_STAGE1_ADD_OPT2:-}
      SOW_MVSEP_STAGE2_SEP_TYPE: ${SOW_MVSEP_STAGE2_SEP_TYPE:-22}
      SOW_MVSEP_STAGE2_ADD_OPT1: ${SOW_MVSEP_STAGE2_ADD_OPT1:-0}
      SOW_MVSEP_STAGE2_ADD_OPT2: ${SOW_MVSEP_STAGE2_ADD_OPT2:-1}
      SOW_MVSEP_HTTP_TIMEOUT: ${SOW_MVSEP_HTTP_TIMEOUT:-60}
      SOW_MVSEP_STAGE_TIMEOUT: ${SOW_MVSEP_STAGE_TIMEOUT:-300}
      SOW_MVSEP_STAGE2_TIMEOUT: ${SOW_MVSEP_STAGE2_TIMEOUT:-900}
      SOW_MVSEP_TOTAL_TIMEOUT: ${SOW_MVSEP_TOTAL_TIMEOUT:-1800}
      SOW_MVSEP_MAX_CONCURRENT: ${SOW_MVSEP_MAX_CONCURRENT:-1}
      
      # Cache Configuration
      CACHE_DIR: /cache
    volumes:
      # Persistent cache for processed audio
      - analysis-cache:/cache
    networks:
      - sow-network
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/api/v1/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 60s

# ============================================================================
# Persistent Volumes
# ============================================================================
volumes:
  analysis-cache:
    driver: local

# ============================================================================
# Networks
# ============================================================================
networks:
  sow-network:
    driver: bridge
YAML
```

---

## Step 5: Deploy the Services

### 5.1 Pull the Images

```bash
cd ~/sow-deployment

# Pull the latest images
docker compose pull

# Verify images are available
docker images | grep sow-
```

### 5.2 Start the Services

```bash
# Start in detached mode
docker compose up -d

# View startup logs
docker compose logs -f
```

### 5.3 Verify Deployment

**Check container status:**
```bash
docker compose ps
# Should show both services as "running"
```

**Health check - Analysis Service:**
```bash
curl -s http://localhost:8000/api/v1/health | jq .
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

### 5.4 Test Job Submission

```bash
# Set API key from .env
export API_KEY=$(grep SOW_ANALYSIS_API_KEY ~/sow-deployment/.env | cut -d'"' -f2)

# Submit a test analysis job
curl -X POST http://localhost:8000/api/v1/jobs/analyze \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "audio_url": "s3://your-bucket/test-hash/audio.mp3",
    "content_hash": "test123",
    "options": {
      "generate_stems": false,
      "force": false
    }
  }'

# Check job status
curl -s http://localhost:8000/api/v1/jobs/<job-id> \
  -H "Authorization: Bearer $API_KEY" | jq .
```

---

## Step 6: Configure Firewall (Optional but Recommended)

```bash
# Check if UFW is installed
sudo ufw status

# Allow SSH (if not already allowed)
sudo ufw allow 22/tcp

# Allow Analysis Service (if needed externally)
sudo ufw allow 8000/tcp

# Allow Docker bridge traffic
sudo ufw allow in on docker0
sudo ufw default allow FORWARD

# Enable UFW
sudo ufw enable

# Verify
sudo ufw status
```

---

## Step 7: Setup Log Rotation

Prevent Docker logs from filling up disk:

```bash
# Create/update Docker daemon config
sudo mkdir -p /etc/docker
sudo tee /etc/docker/daemon.json << 'EOF'
{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "100m",
    "max-file": "3"
  }
}
EOF

# Restart Docker
sudo systemctl restart docker

# Restart services
cd ~/sow-deployment
docker compose up -d
```

---

## Maintenance & Operations

### Viewing Logs

```bash
cd ~/sow-deployment

# View all service logs
docker compose logs -f

# View service logs
docker compose logs -f analysis

# View last N lines
docker compose logs --tail=100 analysis

# Follow with timestamps
docker compose logs -f --timestamps
```

### Updating to New Versions

```bash
cd ~/sow-deployment

# Pull new images
docker compose pull

# Restart with new images
docker compose up -d

# Verify
docker compose ps
curl http://localhost:8000/api/v1/health
```

### Backup and Restore Cache

```bash
# Backup cache volume
docker run --rm -v sow-deployment_analysis-cache:/cache -v $(pwd):/backup alpine tar czf /backup/analysis-cache.tar.gz -C /cache .

# Restore cache volume
docker run --rm -v sow-deployment_analysis-cache:/cache -v $(pwd):/backup alpine sh -c "cd /cache && tar xzf /backup/analysis-cache.tar.gz"
```

### Clearing Cache

```bash
cd ~/sow-deployment

# Stop services
docker compose down

# Remove volumes (clears cache)
docker compose down -v

# Start fresh
docker compose up -d
```

### Stopping Services

```bash
cd ~/sow-deployment

# Stop services (preserve data)
docker compose stop

# Stop and remove containers
docker compose down

# Stop, remove containers, and clear cache
docker compose down -v
```

---

## Troubleshooting

### Container Fails to Start

```bash
# Check logs
docker compose logs analysis

# Check for missing environment variables
docker compose config

# Verify .env file is loaded
docker compose exec analysis env | grep SOW_
```

### Health Check Failing

```bash
# Test manually
docker compose exec analysis curl -s http://localhost:8000/api/v1/health

# Check if R2 credentials are valid
docker compose exec analysis python -c "
import boto3
from sow_analysis.config import settings

s3 = boto3.client(
    's3',
    endpoint_url=settings.SOW_R2_ENDPOINT_URL,
    aws_access_key_id=settings.SOW_R2_ACCESS_KEY_ID,
    aws_secret_access_key=settings.SOW_R2_SECRET_ACCESS_KEY
)
print(s3.list_buckets())
"
```

### Model Loading Errors

```bash
# Modal-hosted models: check the container-side logs (no host mounts any more)
uv run --no-project --with 'modal>=1.2.6,<2' modal app logs sow-stems
uv run --no-project --with 'modal>=1.2.6,<2' modal app logs sow-forced-aligner
uv run --no-project --with 'modal>=1.2.6,<2' modal app logs sow-whisper
uv run --no-project --with 'modal>=1.2.6,<2' modal app logs sow-analyzer

# Analysis/beat-grid/stem cache (no model weights — those live in Modal volumes)
docker compose exec analysis ls -la /cache
```

### Out of Memory

```bash
# Check memory usage
docker stats

# The container is librosa-only (fast-analyze, component features); lower the
# local concurrency knobs if needed:
SOW_FAST_ANALYZE_MAX_CONCURRENT=0   # auto-detect, capped at 4
SOW_COMPONENT_MAX_CONCURRENT=0      # auto-detect, capped at 4
SOW_ANALYZE_MAX_CONCURRENT=2        # full analysis is one Modal call per job

# Restart
docker compose up -d
```

### Network Connectivity Issues

If containers can resolve DNS but cannot connect to external services:

```bash
# Test from inside container
docker compose exec analysis python -c "
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.settimeout(5)
try:
    s.connect(('1.1.1.1', 443))
    print('OK')
except Exception as e:
    print('Blocked:', e)
"

# Fix UFW rules
sudo ufw allow in on docker0
sudo ufw default allow FORWARD
sudo ufw reload
```

---

## Security Considerations

1. **Environment File**: Keep `.env` file permissions restricted (`chmod 600`)
2. **API Keys**: Use strong, randomly generated keys
3. **Modal tokens**: `MODAL_TOKEN_ID` / `MODAL_TOKEN_SECRET` grant access to your Modal
   workspace (Whisper, forced alignment, stem separation); treat them like API keys
4. **Network**: Only expose necessary ports (8000 for Analysis Service)
5. **Updates**: Regularly update Docker images for security patches
6. **Monitoring**: Set up log aggregation and alerting for failures

---

## Next Steps

- [ ] Configure reverse proxy (nginx/traefik) with SSL/TLS
- [ ] Set up monitoring (Prometheus/Grafana)
- [ ] Configure log aggregation (ELK stack or similar)
- [ ] Set up automated backups of cache volumes
- [ ] Configure CI/CD pipeline for image updates

---

## Quick Reference

| Command | Description |
|---------|-------------|
| `docker compose up -d` | Start services |
| `docker compose down` | Stop and remove containers |
| `docker compose logs -f` | Follow logs |
| `docker compose pull` | Update images |
| `docker compose ps` | Check status |
| `docker compose exec <service> <cmd>` | Run command in container |
| `docker stats` | View resource usage |
