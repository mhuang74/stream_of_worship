# Stream of Worship - Analysis Service Deployment Guide

This guide covers deploying the Analysis Service to a production environment using pre-built Docker images.

## Overview

The Analysis Service consists of two microservices:
- **Analysis Service** (port 8000): Audio analysis, stem separation, LRC generation
- **Qwen3 Alignment Service** (port 8001): Lyric-to-audio forced alignment

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
- Internet access (for downloading models and images)
- Optional: NVIDIA Container Toolkit (for GPU acceleration)

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

### Optional: Install NVIDIA Container Toolkit (GPU)

```bash
# Add NVIDIA package repositories
distribution=$(. /etc/os-release;echo $ID$VERSION_ID)
curl -s -L https://nvidia.github.io/nvidia-docker/gpgkey | \
    sudo apt-key add -
curl -s -L https://nvidia.github.io/nvidia-docker/$distribution/nvidia-docker.list | \
    sudo tee /etc/apt/sources.list.d/nvidia-docker.list

# Install nvidia-container-toolkit
sudo apt-get update
sudo apt-get install -y nvidia-container-toolkit

# Restart Docker
sudo systemctl restart docker

# Verify GPU access
sudo docker run --rm --gpus all nvidia/cuda:12.0-base nvidia-smi
```

---

## Step 2: Download ML Models

Models are **not included in the Docker image** and must be downloaded separately on the host. They are mounted as read-only volumes into the containers.

### 2.1 Audio-Separator Models

These models are used for vocal separation and dereverberation.

```bash
# Create model directory
mkdir -p ~/.cache/audio-separator
cd ~/.cache/audio-separator

# Create Python virtual environment for downloads
python3 -m venv /tmp/model-downloader
source /tmp/model-downloader/bin/activate

# Install audio-separator
pip install audio-separator>=0.30.0

# Download models using Python
cat > /tmp/download_models.py << 'EOF'
from audio_separator.separator import Separator
import os

model_dir = os.path.expanduser("~/.cache/audio-separator")
os.makedirs(model_dir, exist_ok=True)

# Download MelBand Roformer model (Stage 1: Vocal Separation)
print("Downloading MelBand Roformer model...")
sep1 = Separator(output_dir=model_dir, model_file_dir=model_dir, output_format="FLAC")
sep1.load_model(model_filename="model_mel_band_roformer_ep_3005_sdr_11.4360.ckpt")
print("✓ MelBand Roformer model downloaded")

# Download UVR-De-Echo model (Stage 2: Reverb Removal)
print("Downloading UVR-De-Echo model...")
sep2 = Separator(output_dir=model_dir, model_file_dir=model_dir, output_format="FLAC")
sep2.load_model(model_filename="UVR-De-Echo-Normal.pth")
print("✓ UVR-De-Echo model downloaded")

print(f"\nModels saved to: {model_dir}")
print(f"Directory size: {sum(os.path.getsize(os.path.join(dirpath,filename)) for dirpath, dirnames, filenames in os.walk(model_dir) for filename in filenames) / 1024 / 1024:.1f} MB")
EOF

python /tmp/download_models.py

# Cleanup
deactivate
rm -rf /tmp/model-downloader /tmp/download_models.py
```

**Verify models:**
```bash
ls -la ~/.cache/audio-separator/
# Should show model files (several GB total)
```

### 2.2 Qwen3 Forced Aligner Model

This model is used for precise lyric-to-audio alignment.

```bash
# Install huggingface-hub
pip install huggingface-hub

# Download Qwen3 Forced Aligner model
huggingface-cli download Qwen/Qwen3-ForcedAligner-0.6B

# Note the snapshot hash - you'll need this for configuration
ls ~/.cache/huggingface/hub/models--Qwen--Qwen3-ForcedAligner-0.6B/snapshots/
# Example output: c7cbfc2048c462b0d63a45797104fc9db3ad62b7
```

**Get model paths for configuration:**
```bash
# Get the full model root path
export QWEN3_MODEL_ROOT="$HOME/.cache/huggingface/hub/models--Qwen--Qwen3-ForcedAligner-0.6B"
echo "Model Root: $QWEN3_MODEL_ROOT"

# Get the snapshot hash
export QWEN3_SNAPSHOT=$(ls "$QWEN3_MODEL_ROOT/snapshots/" | head -1)
echo "Snapshot: $QWEN3_SNAPSHOT"

# Verify model files exist
ls "$QWEN3_MODEL_ROOT/snapshots/$QWEN3_SNAPSHOT/"
```

### 2.3 Model Storage Summary

| Model | Location | Size |
|-------|----------|------|
| Audio-Separator | `~/.cache/audio-separator/` | ~3-5 GB |
| Qwen3 Forced Aligner | `~/.cache/huggingface/hub/models--Qwen--Qwen3-ForcedAligner-0.6B` | ~2.4 GB |
| **Total** | | **~6-8 GB** |

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
SOW_LLM_MODEL=""        # e.g., openai/gpt-4o-mini

# =============================================================================
# QWEN3 MODEL CONFIGURATION (REQUIRED for LRC refinement)
# =============================================================================
# These paths must match where you downloaded the models in Step 2

SOW_QWEN3_MODEL_ROOT=""
SOW_QWEN3_MODEL_SNAPSHOT=""

# =============================================================================
# AUDIO-SEPARATOR MODEL CONFIGURATION (REQUIRED for stem separation)
# =============================================================================
# This path must match where you downloaded the models in Step 2

SOW_AUDIO_SEPARATOR_MODEL_ROOT=""

# =============================================================================
# OPTIONAL: PROCESSING CONFIGURATION
# =============================================================================

# Maximum concurrent local model executions (default: 1)
SOW_MAX_CONCURRENT_LOCAL_MODEL_JOBS=1

# Device selection (cpu or cuda - requires GPU setup)
SOW_DEMUCS_DEVICE=cpu
SOW_WHISPER_DEVICE=cpu
SOW_QWEN3_DEVICE=auto

# Whisper configuration
SOW_WHISPER_MODEL=large-v3

# =============================================================================
# OPTIONAL: MVSEP CLOUD API (for cloud-based stem separation)
# =============================================================================
# Leave empty to use local audio-separator only
# Get token from: https://mvsep.com/

SOW_MVSEP_API_KEY=""
SOW_MVSEP_ENABLED=true
SOW_MVSEP_STAGE1_SEP_TYPE=48
SOW_MVSEP_STAGE1_ADD_OPT1=11
SOW_MVSEP_STAGE2_SEP_TYPE=22
SOW_MVSEP_STAGE2_ADD_OPT1=0
SOW_MVSEP_STAGE2_ADD_OPT2=1

# =============================================================================
# OPTIONAL: QWEN3 INTERNAL API KEY
# =============================================================================
# Only needed if you set SOW_QWEN3_API_KEY in the Qwen3 service

SOW_QWEN3_API_KEY=""

# =============================================================================
# DOCKER IMAGE CONFIGURATION
# =============================================================================
# Update these when deploying new versions

SOW_ANALYSIS_IMAGE="ghcr.io/your-org/sow-analysis:latest"
SOW_QWEN3_IMAGE="ghcr.io/your-org/sow-qwen3:latest"

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
| `SOW_LLM_MODEL` | LLM model | e.g., `openai/gpt-4o-mini` |
| `SOW_QWEN3_MODEL_ROOT` | Model path | Output from Step 2.2 |
| `SOW_QWEN3_MODEL_SNAPSHOT` | Snapshot hash | Output from Step 2.2 |
| `SOW_AUDIO_SEPARATOR_MODEL_ROOT` | Model path | Output from Step 2.1 |

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
      
      # Qwen3 Service Connection
      SOW_QWEN3_BASE_URL: http://qwen3:8000
      SOW_QWEN3_API_KEY: ${SOW_QWEN3_API_KEY:-}
      
      # Whisper Configuration
      SOW_WHISPER_DEVICE: ${SOW_WHISPER_DEVICE:-cpu}
      SOW_WHISPER_MODEL: ${SOW_WHISPER_MODEL:-large-v3}
      
      # Processing Configuration
      SOW_MAX_CONCURRENT_LOCAL_MODEL_JOBS: ${SOW_MAX_CONCURRENT_LOCAL_MODEL_JOBS:-1}
      SOW_DEMUCS_DEVICE: ${SOW_DEMUCS_DEVICE:-cpu}
      
      # Stem Separation Model Configuration
      SOW_AUDIO_SEPARATOR_MODEL_DIR: /models/audio-separator
      SOW_VOCAL_SEPARATION_MODEL: ${SOW_VOCAL_SEPARATION_MODEL:-model_mel_band_roformer_ep_3005_sdr_11.4360.ckpt}
      SOW_DEREVERB_MODEL: ${SOW_DEREVERB_MODEL:-UVR-De-Echo-Normal.pth}
      
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
      SOW_MVSEP_TOTAL_TIMEOUT: ${SOW_MVSEP_TOTAL_TIMEOUT:-900}
      SOW_MVSEP_DAILY_JOB_LIMIT: ${SOW_MVSEP_DAILY_JOB_LIMIT:-50}
      
      # Cache Configuration
      CACHE_DIR: /cache
      
      # Suppress verbose NATTEN logs
      NATTEN_LOG_LEVEL: error
    volumes:
      # Persistent cache for processed audio
      - analysis-cache:/cache
      # Audio-separator models (read-only, from host)
      - ${SOW_AUDIO_SEPARATOR_MODEL_ROOT}:/models/audio-separator:ro
    networks:
      - sow-network
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/api/v1/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 60s
    # Uncomment below for GPU support
    # deploy:
    #   resources:
    #     reservations:
    #       devices:
    #         - driver: nvidia
    #           count: 1
    #           capabilities: [gpu]

  # ==========================================================================
  # Qwen3 Alignment Service - LRC Refinement (Port 8001 externally)
  # ==========================================================================
  qwen3:
    image: ${SOW_QWEN3_IMAGE:-ghcr.io/your-org/sow-qwen3:latest}
    container_name: sow-qwen3
    restart: unless-stopped
    ports:
      - "8001:8000"  # Map host 8001 to container 8000
    environment:
      SOW_QWEN3_DEVICE: ${SOW_QWEN3_DEVICE:-auto}
      SOW_QWEN3_DTYPE: ${SOW_QWEN3_DTYPE:-float32}
      SOW_QWEN3_MAX_CONCURRENT: ${SOW_QWEN3_MAX_CONCURRENT:-1}
      SOW_QWEN3_CACHE_DIR: /cache
      SOW_QWEN3_API_KEY: ${SOW_QWEN3_API_KEY:-}
      SOW_QWEN3_MODEL_PATH: /models/hf-model/snapshots/${SOW_QWEN3_MODEL_SNAPSHOT}
      # R2 credentials (for audio download)
      SOW_QWEN3_R2_BUCKET: ${SOW_R2_BUCKET}
      SOW_QWEN3_R2_ENDPOINT_URL: ${SOW_R2_ENDPOINT_URL}
      SOW_QWEN3_R2_ACCESS_KEY_ID: ${SOW_R2_ACCESS_KEY_ID}
      SOW_QWEN3_R2_SECRET_ACCESS_KEY: ${SOW_R2_SECRET_ACCESS_KEY}
    volumes:
      - qwen3-cache:/cache
      # Mount entire HuggingFace model directory to preserve symlink structure
      - ${SOW_QWEN3_MODEL_ROOT}:/models/hf-model:ro
    networks:
      - sow-network
    deploy:
      resources:
        limits:
          memory: 8g
          cpus: '4'
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 120s  # Qwen3 takes longer to load

# ============================================================================
# Persistent Volumes
# ============================================================================
volumes:
  analysis-cache:
    driver: local
  qwen3-cache:
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

**Health check - Qwen3 Service:**
```bash
curl -s http://localhost:8001/health | jq .
```

Expected response:
```json
{
  "status": "healthy",
  "version": "0.1.0",
  "model": "ready",
  "device": "cpu",
  "max_concurrent": 1
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

# Allow Qwen3 Service (internal only, usually not needed externally)
# sudo ufw allow 8001/tcp

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

# View specific service
docker compose logs -f analysis
docker compose logs -f qwen3

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
# Backup cache volumes
docker run --rm -v sow-deployment_analysis-cache:/cache -v $(pwd):/backup alpine tar czf /backup/analysis-cache.tar.gz -C /cache .
docker run --rm -v sow-deployment_qwen3-cache:/cache -v $(pwd):/backup alpine tar czf /backup/qwen3-cache.tar.gz -C /cache .

# Restore cache volumes
docker run --rm -v sow-deployment_analysis-cache:/cache -v $(pwd):/backup alpine sh -c "cd /cache && tar xzf /backup/analysis-cache.tar.gz"
docker run --rm -v sow-deployment_qwen3-cache:/cache -v $(pwd):/backup alpine sh -c "cd /cache && tar xzf /backup/qwen3-cache.tar.gz"
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
# Verify model paths are mounted correctly
docker compose exec analysis ls -la /models/audio-separator/
docker compose exec qwen3 ls -la /models/hf-model/snapshots/

# Check model files exist on host
ls -la ~/.cache/audio-separator/
ls -la ~/.cache/huggingface/hub/models--Qwen--Qwen3-ForcedAligner-0.6B/snapshots/
```

### Out of Memory

```bash
# Check memory usage
docker stats

# Reduce concurrent local model jobs in .env
SOW_MAX_CONCURRENT_LOCAL_MODEL_JOBS=1

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
3. **Model Mounts**: Models are mounted read-only (`:ro`) to prevent tampering
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
