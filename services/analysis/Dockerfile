# Use Python 3.11 slim as base
FROM python:3.11-slim

# Platform arg required for conditional PyTorch/NATTEN install
ARG TARGETPLATFORM

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libsndfile1 \
    gcc \
    g++ \
    git \
    cmake \
    && rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
ENV UV_SYSTEM_PYTHON=1
ENV UV_COMPILE_BYTECODE=1

# Install build dependencies first (required before allin1/demucs)
RUN uv pip install --no-cache Cython numpy

# Install PyTorch — CPU-only on x86_64, standard on ARM64 (M-series)
RUN if [ "$TARGETPLATFORM" = "linux/amd64" ]; then \
        uv pip install --no-cache \
            --index-url https://download.pytorch.org/whl/cpu \
            torch==2.4.1+cpu \
            torchaudio==2.4.1+cpu \
            torchvision==0.19.1+cpu; \
    else \
        uv pip install --no-cache torch==2.4.1; \
    fi

# Copy and install service dependencies
COPY pyproject.toml .
RUN uv pip install --no-cache --no-build-isolation -e ".[service]"

# Install NATTEN from source — platform-conditional flags
RUN if [ "$TARGETPLATFORM" = "linux/amd64" ]; then \
        NATTEN_IS_FOR_PYPI=1 uv pip install --no-cache --no-build-isolation natten==0.17.1; \
    else \
        uv pip install --no-cache --no-build-isolation natten==0.17.1; \
    fi

# Copy application code
COPY src/ ./src/

# Create cache directory
RUN mkdir -p /cache

# Expose port
EXPOSE 8000

# Run the service
CMD ["uvicorn", "sow_analysis.main:app", "--host", "0.0.0.0", "--port", "8000"]
