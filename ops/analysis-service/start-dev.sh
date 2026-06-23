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
HF_CACHE_DIR="${SOW_FORCED_ALIGNER_MODEL_ROOT:-$HOME/.cache/huggingface/hub/models--Qwen--Qwen3-ForcedAligner-0.6B}"
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

# Check for Qwen3 Forced Aligner model
echo -e "${YELLOW}Checking for Qwen3 Forced Aligner model...${NC}"

QWEN3_MODEL_FOUND=false
if [[ -d "$HF_CACHE_DIR" ]] && [[ -n "$(ls -A "$HF_CACHE_DIR/snapshots/" 2>/dev/null)" ]]; then
    QWEN3_SNAPSHOT=$(ls "$HF_CACHE_DIR/snapshots/" | head -1)
    if [[ -n "$QWEN3_SNAPSHOT" ]]; then
        echo -e "  ${GREEN}Found: Qwen3-ForcedAligner-0.6B (snapshot: $QWEN3_SNAPSHOT)${NC}"
        QWEN3_MODEL_FOUND=true
    fi
fi

if [[ "$QWEN3_MODEL_FOUND" == false ]]; then
    echo -e "  ${YELLOW}Missing: Qwen3-ForcedAligner-0.6B${NC}"
    echo ""
    echo -e "${YELLOW}Downloading Qwen3 Forced Aligner model from Hugging Face...${NC}"
    echo "This may take several minutes (~1.2GB)..."
    echo ""

    cd "$PROJECT_ROOT"
    uv run --python 3.11 --extra poc_qwen3_asr python << EOF
from huggingface_hub import snapshot_download
import os

try:
    path = snapshot_download("Qwen/Qwen3-ForcedAligner-0.6B")
    print(f"  ✓ Qwen3-ForcedAligner-0.6B downloaded to: {path}")
except Exception as e:
    print(f"  ✗ Failed to download Qwen3-ForcedAligner-0.6B: {e}")
    exit(1)
EOF

    # Re-check for snapshot after download
    if [[ -d "$HF_CACHE_DIR" ]]; then
        QWEN3_SNAPSHOT=$(ls "$HF_CACHE_DIR/snapshots/" | head -1)
    fi
    echo ""
fi

# Export the model roots for docker compose
export SOW_AUDIO_SEPARATOR_MODEL_ROOT="$MODEL_DIR"
export SOW_FORCED_ALIGNER_MODEL_ROOT="$HF_CACHE_DIR"

# If we found a snapshot, set the model path to use the local mount
if [[ -n "${QWEN3_SNAPSHOT:-}" ]]; then
    export SOW_FORCED_ALIGNER_MODEL_PATH="/models/hf-model/snapshots/$QWEN3_SNAPSHOT"
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

# Handle --no-start flag (download only)
if [[ "$NO_START" == true ]]; then
    echo -e "${GREEN}Models are ready. Skipping docker compose startup.${NC}"
    exit 0
fi

echo ""
echo -e "${GREEN}Starting Analysis Service in development mode...${NC}"
echo "  Audio-separator model directory: $MODEL_DIR"
echo "  Forced aligner model directory: $HF_CACHE_DIR"
echo "  API will be available at: http://localhost:8000"
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
