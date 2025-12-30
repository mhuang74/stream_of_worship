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

# Install Poetry
RUN curl -sSL https://install.python-poetry.org | python3 -
ENV PATH="/root/.local/bin:$PATH"

# Copy dependency files
COPY pyproject.toml ./

# Configure Poetry to not create virtual env (we're in Docker)
RUN poetry config virtualenvs.create false

# Pre-install build dependencies for packages that need them
RUN pip install --no-cache-dir numpy cython

# Install Python dependencies
RUN poetry install --no-interaction --no-ansi --no-root

# Install madmom separately (has build issues with Poetry's build isolation)
RUN pip install --no-cache-dir madmom==0.16.1

# Copy project files
COPY . .

# Expose Jupyter Lab port
EXPOSE 8888

# Default command: start Jupyter Lab
CMD ["jupyter", "lab", "--ip=0.0.0.0", "--port=8888", "--no-browser", "--allow-root", "--ServerApp.token=", "--ServerApp.password=", "--ServerApp.allow_origin=*", "--ServerApp.disable_check_xsrf=True"]
