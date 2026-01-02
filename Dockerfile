FROM python:3.11-slim

# Set working directory
WORKDIR /workspace

# Install system dependencies for audio processing
RUN apt-get update && apt-get install -y \
    # FFmpeg for audio format handling
    ffmpeg \
    # libsndfile for soundfile library
    libsndfile1 \
    libsndfile1-dev \
    # librosa dependencies
    libsamplerate0 \
    libsamplerate0-dev \
    # Build tools for some Python packages
    gcc \
    g++ \
    make \
    # Useful utilities
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install uv package manager
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Set environment variables for uv
ENV UV_SYSTEM_PYTHON=1
ENV UV_COMPILE_BYTECODE=1

# Copy requirements file
COPY requirements.txt ./

# Install build dependencies first
RUN uv pip install --no-cache Cython numpy

# Install Python dependencies using uv (with build isolation for proper dependency resolution)
RUN uv pip install --no-cache -r requirements.txt

# Install madmom separately (has build issues with build isolation)
RUN uv pip install --no-cache --no-build-isolation madmom==0.16.1

# Copy project files
COPY . .

# Expose Jupyter Lab port
EXPOSE 8888

# Default command: start Jupyter Lab
CMD ["jupyter", "lab", "--ip=0.0.0.0", "--port=8888", "--no-browser", "--allow-root", "--ServerApp.token=", "--ServerApp.password=", "--ServerApp.allow_origin=*", "--ServerApp.disable_check_xsrf=True"]
