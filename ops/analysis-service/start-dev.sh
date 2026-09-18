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
LOG_LEVEL=""
COMPOSE_UP_ARGS=()

args=("$@")
i=0
while [[ $i -lt ${#args[@]} ]]; do
    case "${args[$i]}" in
        --no-start)
            NO_START=true
            ;;
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

# Download models if not present
echo -e "${YELLOW}Checking for audio-separator models...${NC}"
mkdir -p "$MODEL_DIR"

VOCAL_MODEL="model_mel_band_roformer_ep_3005_sdr_11.4360.ckpt"
UVR_MODEL="UVR-De-Echo-Normal.pth"

VOCAL_MODEL_PATH="$MODEL_DIR/$VOCAL_MODEL"
UVR_MODEL_PATH="$MODEL_DIR/$UVR_MODEL"

# Minimum plausible model sizes (bytes). Guards against truncated/interrupted
# downloads: audio_separator's downloader writes directly to the final path, so
# a killed run leaves a corrupt file that torch fails to load at service start.
VOCAL_MODEL_MIN_BYTES=$((900 * 1024 * 1024))   # full model ~1.0 GB
UVR_MODEL_MIN_BYTES=$((100 * 1024 * 1024))     # full model ~127 MB

model_ok() {
    local path="$1" min_bytes="$2"
    [[ -f "$path" ]] || return 1
    local size
    size=$(stat -c%s "$path" 2>/dev/null) || return 1
    [[ "$size" -ge "$min_bytes" ]]
}

NEED_DOWNLOAD=false

if model_ok "$VOCAL_MODEL_PATH" "$VOCAL_MODEL_MIN_BYTES"; then
    echo -e "  ${GREEN}Found: $VOCAL_MODEL${NC}"
else
    if [[ -f "$VOCAL_MODEL_PATH" ]]; then
        echo -e "  ${RED}Corrupt/truncated (too small), will re-download: $VOCAL_MODEL ($(stat -c%s "$VOCAL_MODEL_PATH") bytes)${NC}"
        rm -f "$VOCAL_MODEL_PATH"
    else
        echo -e "  ${YELLOW}Missing: $VOCAL_MODEL${NC}"
    fi
    NEED_DOWNLOAD=true
fi

if model_ok "$UVR_MODEL_PATH" "$UVR_MODEL_MIN_BYTES"; then
    echo -e "  ${GREEN}Found: $UVR_MODEL${NC}"
else
    if [[ -f "$UVR_MODEL_PATH" ]]; then
        echo -e "  ${RED}Corrupt/truncated (too small), will re-download: $UVR_MODEL ($(stat -c%s "$UVR_MODEL_PATH") bytes)${NC}"
        rm -f "$UVR_MODEL_PATH"
    else
        echo -e "  ${YELLOW}Missing: $UVR_MODEL${NC}"
    fi
    NEED_DOWNLOAD=true
fi

if [[ "$NEED_DOWNLOAD" == true ]]; then
    echo ""
    echo -e "${YELLOW}Downloading missing models to: $MODEL_DIR${NC}"
    echo "This may take a few minutes..."
    echo ""

    # audio_separator's Python downloader streams a single connection and crawls
    # on some networks (~7 KB/s observed; GitHub release assets are Fastly-CDN'd
    # and some ISP peerings throttle them). Release assets support ranged
    # requests; parallel ranges recover ~100x aggregate throughput.
    download_parallel() {
        local url="$1" out="$2" size="$3" tmp="$4"
        local chunk=$(( (size + 15) / 16 ))
        local pids=()

        rm -f "$tmp" "$tmp".part*
        for i in $(seq 0 15); do
            local start=$((i * chunk))
            [[ "$start" -ge "$size" ]] && break
            local end=$((start + chunk - 1))
            [[ "$end" -ge "$size" ]] && end=$((size - 1))
            curl -fsSL --retry 3 --retry-delay 2 -o "$(printf '%s.part%02d' "$tmp" "$i")" -r "$start-$end" "$url" &
            pids+=($!)
        done

        local fail=0
        for pid in "${pids[@]}"; do
            wait "$pid" || fail=1
        done
        if [[ "$fail" -ne 0 ]]; then
            rm -f "$tmp" "$tmp".part??
            return 1
        fi

        cat "$tmp".part?? > "$tmp"
        rm -f "$tmp".part??

        local downloaded
        downloaded=$(stat -c%s "$tmp")
        if [[ "$downloaded" -ne "$size" ]]; then
            echo "  Size mismatch: expected $size bytes, got $downloaded"
            rm -f "$tmp"
            return 1
        fi
        mv "$tmp" "$out"
    }

    fetch_model() {
        local filename="$1" size="$2" url="$3"
        echo "Downloading $filename ($(numfmt --to=iec "$size"))..."
        download_parallel "$url" "$MODEL_DIR/$filename" "$size" "$MODEL_DIR/.$filename.tmp" \
            && echo "  ✓ $filename downloaded successfully" \
            || { echo "  ✗ Failed to download $filename"; rm -f "$MODEL_DIR/.$filename.tmp" "$MODEL_DIR/$filename"; exit 1; }
    }

    VOCAL_MODEL_URL="https://github.com/TRvlvr/model_repo/releases/download/all_public_uvr_models/$VOCAL_MODEL"
    UVR_MODEL_URL="https://github.com/TRvlvr/model_repo/releases/download/all_public_uvr_models/$UVR_MODEL"
    # Exact upstream sizes (GitHub release assets); used for both range planning
    # and post-download verification.
    VOCAL_MODEL_SIZE=1007816988   # ~1.0 GB
    UVR_MODEL_SIZE=127139365      # ~127 MB

    [[ -f "$VOCAL_MODEL_PATH" ]] || fetch_model "$VOCAL_MODEL" "$VOCAL_MODEL_SIZE" "$VOCAL_MODEL_URL"
    [[ -f "$UVR_MODEL_PATH" ]] || fetch_model "$UVR_MODEL" "$UVR_MODEL_SIZE" "$UVR_MODEL_URL"

    echo ""
    echo "Models ready in: $MODEL_DIR"
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

    uv run --project "$SCRIPT_DIR/../../lab/poc-scripts" --python 3.11 --extra poc_qwen3_asr python << EOF
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
