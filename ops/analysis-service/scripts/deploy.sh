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
# EMBEDDING CONFIGURATION (REQUIRED for embedding generation)
# =============================================================================
SOW_EMBEDDING_API_KEY=""
SOW_EMBEDDING_BASE_URL=""
SOW_EMBEDDING_MODEL="text-embedding-3-small"

# =============================================================================
# OPTIONAL: PROCESSING CONFIGURATION
# =============================================================================
SOW_MAX_CONCURRENT_LOCAL_MODEL_JOBS=1
SOW_DEMUCS_DEVICE=cpu

# Whisper, forced alignment and stem separation all run on Modal
# (modal_apps/whisper_app.py, forced_aligner_app.py, stem_separator_app.py);
# fill the tokens in on the server, then deploy each app.
MODAL_TOKEN_ID=""
MODAL_TOKEN_SECRET=""
SOW_MODAL_WHISPER_ALLOWED_MODELS="tiny,base,small,medium,large-v3,large-v3-turbo,distil-large-v3"
SOW_WHISPER_CACHE_VERSION=2
SOW_MODAL_STEM_APP_NAME="sow-stems"
SOW_MODAL_STEM_CLASS_NAME="StemSeparator"
SOW_MODAL_STEM_MAX_ATTEMPTS=2
SOW_MODAL_STEM_RETRY_DELAY_SECONDS=5.0
SOW_MODAL_STEM_MAX_AUDIO_BYTES=100000000

# =============================================================================
# OPTIONAL: MVSEP CLOUD API
# =============================================================================
SOW_MVSEP_API_KEY=""
SOW_MVSEP_ENABLED=true

# =============================================================================
# DOCKER IMAGE CONFIGURATION
# =============================================================================
SOW_ANALYSIS_IMAGE="ghcr.io/your-org/sow-analysis:latest"
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
      NATTEN_LOG_LEVEL: error
    volumes:
      - analysis-cache:/cache
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/api/v1/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 60s

volumes:
  analysis-cache:
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
    
    if [ "$ANALYSIS_HEALTH" = "200" ]; then
        log_success "Analysis Service is healthy (http://localhost:8000)"
    else
        log_error "Analysis Service health check failed (HTTP $ANALYSIS_HEALTH)"
        log_info "Check logs: docker compose logs analysis"
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
    echo ""
    echo "Configuration:"
    echo "  - Environment File:  $DEPLOYMENT_DIR/.env"
    echo "  - Docker Compose:    $DEPLOYMENT_DIR/docker-compose.yml"
    echo ""
    echo "Models:"
    echo "  - Stem Separator:    Modal app sow-stems (no local model)"
    echo "  - Forced Aligner:    Modal app sow-forced-aligner (no local model)"
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
