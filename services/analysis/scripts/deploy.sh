#!/bin/bash
# ============================================================================
# Stream of Worship - Analysis Service Deployment Script
# One-command deployment using pre-built Docker images
# ============================================================================

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
DEPLOYMENT_DIR="${DEPLOYMENT_DIR:-$HOME/sow-deployment}"
MODEL_CACHE_DIR="${MODEL_CACHE_DIR:-$HOME/.cache}"

# ============================================================================
# Helper Functions
# ============================================================================

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# ============================================================================
# Prerequisites Check
# ============================================================================

check_prerequisites() {
    log_info "Checking prerequisites..."
    
    # Check Docker
    if ! command -v docker &> /dev/null; then
        log_error "Docker is not installed. Please install Docker first."
        log_info "Visit: https://docs.docker.com/engine/install/"
        exit 1
    fi
    
    # Check Docker Compose
    if ! docker compose version &> /dev/null; then
        log_error "Docker Compose plugin is not installed."
        log_info "Visit: https://docs.docker.com/compose/install/"
        exit 1
    fi
    
    # Check Python (needed for model downloads)
    if ! command -v python3 &> /dev/null; then
        log_error "Python 3 is required for model downloads."
        exit 1
    fi
    
    log_success "Prerequisites check passed"
}

# ============================================================================
# Setup Deployment Directory
# ============================================================================

setup_deployment_dir() {
    log_info "Setting up deployment directory: $DEPLOYMENT_DIR"
    
    mkdir -p "$DEPLOYMENT_DIR"
    cd "$DEPLOYMENT_DIR"
    
    log_success "Deployment directory ready"
}

# ============================================================================
# Download Models
# ============================================================================

download_audio_separator_models() {
    log_info "Downloading Audio-Separator models..."
    
    local MODEL_DIR="$MODEL_CACHE_DIR/audio-separator"
    mkdir -p "$MODEL_DIR"
    
    if [ -f "$MODEL_DIR/model_mel_band_roformer_ep_3005_sdr_11.4360.ckpt" ] && \
       [ -f "$MODEL_DIR/UVR-De-Echo-Normal.pth" ]; then
        log_success "Audio-Separator models already exist"
        return 0
    fi
    
    # Create temporary virtual environment
    local TEMP_VENV=$(mktemp -d)
    python3 -m venv "$TEMP_VENV"
    source "$TEMP_VENV/bin/activate"
    
    log_info "Installing audio-separator..."
    pip install -q audio-separator>=0.30.0
    
    log_info "Downloading MelBand Roformer model..."
    python3 << EOF
from audio_separator.separator import Separator
import os

model_dir = "$MODEL_DIR"
sep = Separator(output_dir=model_dir, model_file_dir=model_dir, output_format="FLAC")
sep.load_model(model_filename="model_mel_band_roformer_ep_3005_sdr_11.4360.ckpt")
print("✓ MelBand Roformer downloaded")

sep2 = Separator(output_dir=model_dir, model_file_dir=model_dir, output_format="FLAC")
sep2.load_model(model_filename="UVR-De-Echo-Normal.pth")
print("✓ UVR-De-Echo downloaded")
EOF
    
    # Cleanup
    deactivate
    rm -rf "$TEMP_VENV"
    
    log_success "Audio-Separator models downloaded to: $MODEL_DIR"
}

download_qwen3_model() {
    log_info "Downloading Qwen3 Forced Aligner model..."
    
    local MODEL_ROOT="$MODEL_CACHE_DIR/huggingface/hub/models--Qwen--Qwen3-ForcedAligner-0.6B"
    
    if [ -d "$MODEL_ROOT" ] && [ "$(ls -A $MODEL_ROOT/snapshots/ 2>/dev/null)" ]; then
        log_success "Qwen3 model already exists"
        return 0
    fi
    
    # Install huggingface-hub
    pip install -q huggingface-hub
    
    log_info "Downloading from Hugging Face (this may take several minutes)..."
    huggingface-cli download Qwen/Qwen3-ForcedAligner-0.6B
    
    log_success "Qwen3 model downloaded to: $MODEL_ROOT"
}

# ============================================================================
# Get Model Paths for Configuration
# ============================================================================

get_model_paths() {
    log_info "Determining model paths..."
    
    # Audio-Separator
    export AUDIO_SEPARATOR_ROOT="$MODEL_CACHE_DIR/audio-separator"
    
    # Qwen3
    export QWEN3_ROOT="$MODEL_CACHE_DIR/huggingface/hub/models--Qwen--Qwen3-ForcedAligner-0.6B"
    
    if [ ! -d "$QWEN3_ROOT" ]; then
        log_error "Qwen3 model not found. Please run model download first."
        exit 1
    fi
    
    # Get snapshot hash
    export QWEN3_SNAPSHOT=$(ls "$QWEN3_ROOT/snapshots/" | head -1)
    
    if [ -z "$QWEN3_SNAPSHOT" ]; then
        log_error "Could not find Qwen3 snapshot hash"
        exit 1
    fi
    
    log_info "Audio-Separator: $AUDIO_SEPARATOR_ROOT"
    log_info "Qwen3 Root: $QWEN3_ROOT"
    log_info "Qwen3 Snapshot: $QWEN3_SNAPSHOT"
}

# ============================================================================
# Create Configuration Files
# ============================================================================

create_env_file() {
    log_info "Creating .env configuration file..."
    
    local ENV_FILE="$DEPLOYMENT_DIR/.env"
    
    if [ -f "$ENV_FILE" ]; then
        log_warning ".env file already exists. Skipping creation."
        log_info "Review and update: $ENV_FILE"
        return 0
    fi
    
    # Generate random API keys
    local ANALYSIS_KEY=$(openssl rand -base64 32 2>/dev/null || head -c 48 /dev/urandom | base64)
    local ADMIN_KEY=$(openssl rand -base64 32 2>/dev/null || head -c 48 /dev/urandom | base64)
    
    cat > "$ENV_FILE" << EOF
# ============================================================================
# Stream of Worship - Analysis Service - Environment Configuration
# Generated: $(date -u +"%Y-%m-%d %H:%M:%S UTC")
# ============================================================================
# IMPORTANT: Fill in all REQUIRED fields before starting services
# ============================================================================

# =============================================================================
# R2 STORAGE CONFIGURATION (REQUIRED)
# =============================================================================
SOW_R2_ACCESS_KEY_ID=""
SOW_R2_SECRET_ACCESS_KEY=""
SOW_R2_ENDPOINT_URL=""
SOW_R2_BUCKET=""

# =============================================================================
# API SECURITY (REQUIRED)
# =============================================================================
SOW_ANALYSIS_API_KEY="$ANALYSIS_KEY"
SOW_ADMIN_API_KEY="$ADMIN_KEY"

# =============================================================================
# LLM CONFIGURATION (REQUIRED for LRC generation)
# =============================================================================
SOW_LLM_API_KEY=""
SOW_LLM_BASE_URL=""
SOW_LLM_MODEL=""

# =============================================================================
# QWEN3 MODEL CONFIGURATION (Auto-detected)
# =============================================================================
SOW_QWEN3_MODEL_ROOT="$QWEN3_ROOT"
SOW_QWEN3_MODEL_SNAPSHOT="$QWEN3_SNAPSHOT"

# =============================================================================
# AUDIO-SEPARATOR MODEL CONFIGURATION (Auto-detected)
# =============================================================================
SOW_AUDIO_SEPARATOR_MODEL_ROOT="$AUDIO_SEPARATOR_ROOT"

# =============================================================================
# OPTIONAL: PROCESSING CONFIGURATION
# =============================================================================
SOW_MAX_CONCURRENT_ANALYSIS_JOBS=1
SOW_MAX_CONCURRENT_LRC_JOBS=2
SOW_MAX_CONCURRENT_STEM_SEPARATION_JOBS=1
SOW_DEMUCS_DEVICE=cpu
SOW_WHISPER_DEVICE=cpu
SOW_WHISPER_MODEL=large-v3

# =============================================================================
# OPTIONAL: MVSEP CLOUD API
# =============================================================================
SOW_MVSEP_API_KEY=""
SOW_MVSEP_ENABLED=true

# =============================================================================
# DOCKER IMAGE CONFIGURATION
# =============================================================================
SOW_ANALYSIS_IMAGE="ghcr.io/your-org/sow-analysis:latest"
SOW_QWEN3_IMAGE="ghcr.io/your-org/sow-qwen3:latest"
EOF
    
    chmod 600 "$ENV_FILE"
    
    log_success ".env file created: $ENV_FILE"
    log_warning "IMPORTANT: Edit this file and fill in all REQUIRED fields"
}

create_docker_compose() {
    log_info "Creating docker-compose.yml..."
    
    local COMPOSE_FILE="$DEPLOYMENT_DIR/docker-compose.yml"
    
    if [ -f "$COMPOSE_FILE" ]; then
        log_warning "docker-compose.yml already exists. Skipping creation."
        return 0
    fi
    
    # Download the production docker-compose.yml
    # In production, this would be fetched from the repository or documentation
    cat > "$COMPOSE_FILE" << 'EOF'
version: "3.8"

services:
  analysis:
    image: ${SOW_ANALYSIS_IMAGE:-ghcr.io/your-org/sow-analysis:latest}
    container_name: sow-analysis
    restart: unless-stopped
    ports:
      - "8000:8000"
    env_file:
      - .env
    environment:
      CACHE_DIR: /cache
      SOW_AUDIO_SEPARATOR_MODEL_DIR: /models/audio-separator
      NATTEN_LOG_LEVEL: error
      SOW_QWEN3_BASE_URL: http://qwen3:8000
    volumes:
      - analysis-cache:/cache
      - ${SOW_AUDIO_SEPARATOR_MODEL_ROOT}:/models/audio-separator:ro
    networks:
      - sow-network
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/api/v1/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 60s

  qwen3:
    image: ${SOW_QWEN3_IMAGE:-ghcr.io/your-org/sow-qwen3:latest}
    container_name: sow-qwen3
    restart: unless-stopped
    ports:
      - "8001:8000"
    env_file:
      - .env
    environment:
      SOW_QWEN3_CACHE_DIR: /cache
      SOW_QWEN3_MODEL_PATH: /models/hf-model/snapshots/${SOW_QWEN3_MODEL_SNAPSHOT}
      SOW_QWEN3_R2_BUCKET: ${SOW_R2_BUCKET}
      SOW_QWEN3_R2_ENDPOINT_URL: ${SOW_R2_ENDPOINT_URL}
      SOW_QWEN3_R2_ACCESS_KEY_ID: ${SOW_R2_ACCESS_KEY_ID}
      SOW_QWEN3_R2_SECRET_ACCESS_KEY: ${SOW_R2_SECRET_ACCESS_KEY}
    volumes:
      - qwen3-cache:/cache
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
      start_period: 120s

volumes:
  analysis-cache:
  qwen3-cache:

networks:
  sow-network:
EOF
    
    log_success "docker-compose.yml created: $COMPOSE_FILE"
}

# ============================================================================
# Deploy Services
# ============================================================================

deploy_services() {
    log_info "Deploying services..."
    
    cd "$DEPLOYMENT_DIR"
    
    # Verify .env has required fields
    if ! grep -q "SOW_R2_ACCESS_KEY_ID=\"[^\"]\"" .env; then
        log_error "R2 credentials not configured in .env"
        log_info "Please edit $DEPLOYMENT_DIR/.env and fill in required fields"
        exit 1
    fi
    
    # Pull images
    log_info "Pulling Docker images..."
    docker compose pull
    
    # Start services
    log_info "Starting services..."
    docker compose up -d
    
    # Wait for services to be ready
    log_info "Waiting for services to start..."
    sleep 10
    
    # Health check
    log_info "Performing health checks..."
    
    local ANALYSIS_HEALTH=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/api/v1/health || echo "000")
    local QWEN3_HEALTH=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8001/health || echo "000")
    
    if [ "$ANALYSIS_HEALTH" = "200" ]; then
        log_success "Analysis Service is healthy (http://localhost:8000)"
    else
        log_error "Analysis Service health check failed (HTTP $ANALYSIS_HEALTH)"
        log_info "Check logs: docker compose logs analysis"
    fi
    
    if [ "$QWEN3_HEALTH" = "200" ]; then
        log_success "Qwen3 Service is healthy (http://localhost:8001)"
    else
        log_warning "Qwen3 Service still initializing (this is normal, can take 1-2 minutes)"
        log_info "Check logs: docker compose logs qwen3"
    fi
    
    log_success "Deployment complete!"
}

# ============================================================================
# Print Summary
# ============================================================================

print_summary() {
    echo ""
    echo "==========================================================================="
    echo "                    DEPLOYMENT SUMMARY"
    echo "==========================================================================="
    echo ""
    echo "Deployment Directory: $DEPLOYMENT_DIR"
    echo ""
    echo "Services:"
    echo "  - Analysis Service:  http://localhost:8000"
    echo "  - Qwen3 Service:     http://localhost:8001"
    echo ""
    echo "Configuration:"
    echo "  - Environment File:  $DEPLOYMENT_DIR/.env"
    echo "  - Docker Compose:    $DEPLOYMENT_DIR/docker-compose.yml"
    echo ""
    echo "Models:"
    echo "  - Audio-Separator:   $AUDIO_SEPARATOR_ROOT"
    echo "  - Qwen3:             $QWEN3_ROOT"
    echo ""
    echo "Useful Commands:"
    echo "  cd $DEPLOYMENT_DIR && docker compose logs -f"
    echo "  cd $DEPLOYMENT_DIR && docker compose ps"
    echo "  cd $DEPLOYMENT_DIR && docker compose down"
    echo ""
    echo "==========================================================================="
}

# ============================================================================
# Main
# ============================================================================

main() {
    echo "==========================================================================="
    echo "    Stream of Worship - Analysis Service Deployment"
    echo "==========================================================================="
    echo ""
    
    check_prerequisites
    setup_deployment_dir
    
    # Download models
    download_audio_separator_models
    download_qwen3_model
    
    # Get model paths
    get_model_paths
    
    # Create configuration
    create_env_file
    create_docker_compose
    
    # Ask before deploying
    echo ""
    log_warning "Before deploying, you must:"
    log_info "1. Edit $DEPLOYMENT_DIR/.env"
    log_info "2. Fill in all REQUIRED fields (R2, LLM, etc.)"
    echo ""
    read -p "Have you configured the .env file? (y/N): " -n 1 -r
    echo ""
    
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        deploy_services
        print_summary
    else
        log_info "Deployment skipped. When ready, run:"
        log_info "  cd $DEPLOYMENT_DIR && docker compose up -d"
    fi
}

# Run main function
main "$@"
