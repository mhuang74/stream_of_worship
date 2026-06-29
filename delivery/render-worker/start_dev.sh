#!/bin/bash
# Start the Render Worker in development mode
# This script runs pre-checks and starts the docker compose dev environment

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
NO_START=false
REBUILD=false
COMPOSE_UP_ARGS=()

for arg in "$@"; do
    case "$arg" in
        --no-start)
            NO_START=true
            ;;
        --build|--rebuild)
            REBUILD=true
            ;;
        *)
            COMPOSE_UP_ARGS+=("$arg")
            ;;
    esac
done

echo -e "${GREEN}=== Render Worker Development Startup ===${NC}"
echo ""

# Check if docker compose is available
if ! command -v docker &> /dev/null; then
    echo -e "${RED}Error: Docker is not installed${NC}"
    exit 1
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

# Handle --no-start flag (pre-checks only)
if [[ "$NO_START" == true ]]; then
    echo -e "${GREEN}Pre-checks passed. Skipping docker compose startup.${NC}"
    exit 0
fi

echo ""
echo -e "${GREEN}Starting Render Worker in development mode...${NC}"
echo "  Env file: /opt/sow/.env"
echo "  Lambda RIE endpoint: http://localhost:9000"
if [[ "$REBUILD" == true ]]; then
    echo "  Rebuilding Docker image before start"
    COMPOSE_UP_ARGS=(--build "${COMPOSE_UP_ARGS[@]}")
fi
echo ""

cd "$SCRIPT_DIR"
if [[ -f "/opt/sow/.env" ]]; then
    docker compose --env-file /opt/sow/.env -f docker-compose.dev.yml up "${COMPOSE_UP_ARGS[@]}" render-worker
else
    docker compose -f docker-compose.dev.yml up "${COMPOSE_UP_ARGS[@]}" render-worker
fi
