#!/bin/bash
# Start the Analysis Service in development mode
# This script starts the docker compose dev environment. ML models (Whisper, forced
# alignment, stem separation) run on Modal, so nothing is downloaded locally.

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
REBUILD=false
LOG_LEVEL=""
COMPOSE_UP_ARGS=()

args=("$@")
i=0
while [[ $i -lt ${#args[@]} ]]; do
    case "${args[$i]}" in
        --build|--rebuild)
            REBUILD=true
            ;;
        --log-level)
            LOG_LEVEL="${args[$((i + 1))]}"
            i=$((i + 1))  # skip the value
            ;;
        *)
            COMPOSE_UP_ARGS+=("${args[$i]}")
            ;;
    esac
    i=$((i + 1))
done

echo -e "${GREEN}=== Analysis Service Development Startup ===${NC}"
echo ""

# Check if docker compose is available
if ! command -v docker &> /dev/null; then
    echo -e "${RED}Error: Docker is not installed${NC}"
    exit 1
fi

# Validate and export the log level (mirrors config.py SOW_LOG_LEVEL validation)
if [[ -n "$LOG_LEVEL" ]]; then
    case "${LOG_LEVEL^^}" in
        DEBUG|INFO|WARNING|ERROR)
            export SOW_LOG_LEVEL="${LOG_LEVEL^^}"
            ;;
        *)
            echo -e "${RED}Error: invalid --log-level '${LOG_LEVEL}' (allowed: DEBUG, INFO, WARNING, ERROR)${NC}"
            exit 1
            ;;
    esac
fi

# Check if .env file exists
if [[ ! -f "/opt/sow/.env" ]]; then
    echo -e "${YELLOW}Warning: .env file not found at /opt/sow/.env${NC}"
    echo -e "Copy from .env.example and configure your environment variables:"
    echo -e "  cp $SCRIPT_DIR/.env.example /opt/sow/.env"
    echo ""
    read -p "Continue anyway? [y/N] " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

echo ""
echo -e "${GREEN}Starting Analysis Service in development mode...${NC}"
echo "  Stem separation: Modal app ${SOW_MODAL_STEM_APP_NAME:-sow-stems}"
echo "  Forced alignment: Modal app ${SOW_MODAL_FORCED_ALIGNER_APP_NAME:-sow-forced-aligner}"
echo "  Log level: ${SOW_LOG_LEVEL:-INFO}"

# Determine the bind IP for display (matches docker-compose SOW_BIND_IP logic)
BIND_IP="0.0.0.0"
if [[ -f "/opt/sow/.env" ]]; then
    BIND_IP=$(grep -E '^SOW_BIND_IP=' /opt/sow/.env | cut -d= -f2- | tr -d '[:space:]' || echo "0.0.0.0")
    BIND_IP="${BIND_IP:-0.0.0.0}"
fi
if [[ "$BIND_IP" == "0.0.0.0" ]]; then
    DISPLAY_IP="localhost"
else
    DISPLAY_IP="$BIND_IP"
fi
echo "  API will be available at: http://${DISPLAY_IP}:8000  (bound to ${BIND_IP})"
if [[ "$REBUILD" == true ]]; then
    echo "  Rebuilding Docker image before start"
    COMPOSE_UP_ARGS=(--build "${COMPOSE_UP_ARGS[@]}")
fi
echo ""

cd "$SCRIPT_DIR"
if [[ -f "/opt/sow/.env" ]]; then
    docker compose --env-file /opt/sow/.env up "${COMPOSE_UP_ARGS[@]}" analysis-dev
else
    docker compose up "${COMPOSE_UP_ARGS[@]}" analysis-dev
fi
