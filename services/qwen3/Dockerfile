# Use Python 3.11 slim as base
FROM python:3.11-slim

# Platform arg for future multi-platform support
ARG TARGETPLATFORM

# Development mode flag - set to "true" for dev mode with volume mount
ARG DEV_MODE=false

WORKDIR /workspace

# Install system dependencies (ffmpeg for audio processing)
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
ENV UV_SYSTEM_PYTHON=1
ENV UV_COMPILE_BYTECODE=1

# Copy and install service dependencies
# qwen-asr includes PyTorch as a dependency
COPY pyproject.toml .
RUN uv pip install --no-cache --no-build-isolation -e ".[service]"

# Copy application code (only used in production mode, overridden by volume in dev)
COPY src/ ./src/

# Create directories for cache and model
RUN mkdir -p /cache /models

# Expose port
EXPOSE 8000

# Run the service with optional reload for development
# In dev mode, mount your local code to /workspace and use DEV_MODE=true
CMD if [ "$DEV_MODE" = "true" ]; then \
        uvicorn sow_qwen3.main:app --host 0.0.0.0 --port 8000 --reload --reload-dir /workspace/src; \
    else \
        uvicorn sow_qwen3.main:app --host 0.0.0.0 --port 8000; \
    fi
