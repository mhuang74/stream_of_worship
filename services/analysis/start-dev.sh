#!/bin/bash
# Start the Analysis Service in development mode
# This script downloads required models and starts the docker compose dev environment

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
MODEL_DIR="${SOW_AUDIO_SEPARATOR_MODEL_ROOT:-$HOME/.cache/audio-separator}"

echo -e "${GREEN}=== Analysis Service Development Startup ===${NC}"
echo ""

# Check if docker compose is available
if ! command -v docker &> /dev/null; then
    echo -e "${RED}Error: Docker is not installed${NC}"
    exit 1
fi

# Download models if not present
echo -e "${YELLOW}Checking for audio-separator models...${NC}"
mkdir -p "$MODEL_DIR"

VOCAL_MODEL="model_mel_band_roformer_ep_3005_sdr_11.4360.ckpt"
UVR_MODEL="UVR-De-Echo-Normal.pth"

VOCAL_MODEL_PATH="$MODEL_DIR/$VOCAL_MODEL"
UVR_MODEL_PATH="$MODEL_DIR/$UVR_MODEL"

NEED_DOWNLOAD=false

if [[ ! -f "$VOCAL_MODEL_PATH" ]]; then
    echo -e "  ${YELLOW}Missing: $VOCAL_MODEL${NC}"
    NEED_DOWNLOAD=true
else
    echo -e "  ${GREEN}Found: $VOCAL_MODEL${NC}"
fi

if [[ ! -f "$UVR_MODEL_PATH" ]]; then
    echo -e "  ${YELLOW}Missing: $UVR_MODEL${NC}"
    NEED_DOWNLOAD=true
else
    echo -e "  ${GREEN}Found: $UVR_MODEL${NC}"
fi

if [[ "$NEED_DOWNLOAD" == true ]]; then
    echo ""
    echo -e "${YELLOW}Downloading missing models to: $MODEL_DIR${NC}"
    echo "This may take a few minutes..."
    echo ""

    cd "$PROJECT_ROOT"
    uv run --python 3.11 --extra stem_separation python << EOF
from audio_separator.separator import Separator
import os

model_dir = os.path.expanduser("$MODEL_DIR")
os.makedirs(model_dir, exist_ok=True)

try:
    print("Downloading MelBand Roformer model...")
    sep1 = Separator(output_dir=model_dir, model_file_dir=model_dir, output_format="FLAC")
    sep1.load_model(model_filename="$VOCAL_MODEL")
    print(f"  ✓ MelBand Roformer downloaded successfully")
except Exception as e:
    print(f"  ✗ Failed to download MelBand Roformer: {e}")
    exit(1)

try:
    print("Downloading UVR-De-Echo model...")
    sep2 = Separator(output_dir=model_dir, model_file_dir=model_dir, output_format="FLAC")
    sep2.load_model(model_filename="$UVR_MODEL")
    print(f"  ✓ UVR-De-Echo downloaded successfully")
except Exception as e:
    print(f"  ✗ Failed to download UVR-De-Echo: {e}")
    exit(1)

print(f"\nModels ready in: {model_dir}")
EOF

    echo ""
fi

# Export the model root for docker compose
export SOW_AUDIO_SEPARATOR_MODEL_ROOT="$MODEL_DIR"

# Check if .env file exists
if [[ ! -f "$SCRIPT_DIR/.env" ]]; then
    echo -e "${YELLOW}Warning: .env file not found at $SCRIPT_DIR/.env${NC}"
    echo -e "Copy from .env.example and configure your environment variables:"
    echo -e "  cp $SCRIPT_DIR/.env.example $SCRIPT_DIR/.env"
    echo ""
    read -p "Continue anyway? [y/N] " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# Handle --no-start flag (download only)
if [[ "$1" == "--no-start" ]]; then
    echo -e "${GREEN}Models are ready. Skipping docker compose startup.${NC}"
    exit 0
fi

echo ""
echo -e "${GREEN}Starting Analysis Service in development mode...${NC}"
echo "  Model directory: $MODEL_DIR"
echo "  API will be available at: http://localhost:8000"
echo ""

cd "$SCRIPT_DIR"
docker compose up analysis-dev "$@"
