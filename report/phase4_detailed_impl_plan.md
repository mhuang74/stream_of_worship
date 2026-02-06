# Phase 4: Analysis Service - Detailed Implementation Plan

## Overview

Phase 4 implements the Analysis Service, a standalone FastAPI-based service that runs on a powerful machine (GPU-enabled) to process audio files. The service provides REST APIs for submitting analysis jobs, polling for completion, and retrieving results.

## Goals

1. Build a containerized FastAPI service for audio analysis
2. Integrate `allin1` for deep learning-based music analysis (tempo, beats, sections, embeddings)
3. Integrate `demucs` for stem separation (vocals, drums, bass, other)
4. Implement job queue for asynchronous processing
5. Support caching by content hash to avoid re-processing
6. Store results on Cloudflare R2

## Architecture

```
services/analysis/                    # New top-level directory (separate package)
├── Dockerfile                        # Service container image
├── docker-compose.yml                # Service orchestration
├── pyproject.toml                    # Service dependencies
├── src/
│   └── sow_analysis/                 # Python package
│       ├── __init__.py               # Package version
│       ├── main.py                   # FastAPI application entry point
│       ├── config.py                 # Service configuration (pydantic-settings)
│       ├── models.py                 # Pydantic models for requests/responses
│       ├── routes/
│       │   ├── __init__.py           # Route registration
│       │   ├── health.py             # Health check endpoint
│       │   └── jobs.py               # Job submission and status endpoints
│       ├── workers/
│       │   ├── __init__.py           # Worker exports
│       │   ├── analyzer.py           # allin1 + librosa analysis worker
│       │   ├── separator.py          # Demucs stem separation worker
│       │   ├── lrc.py                # LRC worker stub (filled in Phase 6)
│       │   └── queue.py              # In-memory job queue manager
│       └── storage/
│           ├── __init__.py           # Storage exports
│           ├── r2.py                 # R2 upload/download client
│           └── cache.py              # Local result cache manager
```

## Implementation Steps

### Step 1: Create Directory Structure

**Action:** Create the `services/analysis/` directory tree.

```bash
mkdir -p services/analysis/src/sow_analysis/{routes,workers,storage}
touch services/analysis/src/sow_analysis/__init__.py
touch services/analysis/src/sow_analysis/{main,config,models}.py
touch services/analysis/src/sow_analysis/routes/{__init__,health,jobs}.py
touch services/analysis/src/sow_analysis/workers/{__init__,analyzer,separator,lrc,queue}.py
touch services/analysis/src/sow_analysis/storage/{__init__,r2,cache}.py
```

**Files to create:** 15 new files

---

### Step 2: Service Configuration (`config.py`)

**Purpose:** Pydantic-settings based configuration for the service.

**Key Configuration Values:**
- `R2_BUCKET`: R2 bucket name (default: "sow-audio")
- `R2_ENDPOINT_URL`: R2 endpoint URL
- `SOW_R2_ACCESS_KEY_ID`: R2 credentials (from env; matches CLI convention in `services/r2.py`)
- `SOW_R2_SECRET_ACCESS_KEY`: R2 credentials (from env; matches CLI convention in `services/r2.py`)
- `ANALYSIS_API_KEY`: API key for authentication (from env)
- `CACHE_DIR`: Local cache directory (default: "/cache")
- `MAX_CONCURRENT_JOBS`: Job concurrency limit (default: 2)
- `DEMUCS_MODEL`: Demucs model name (default: "htdemucs")
- `DEMUCS_DEVICE`: Device for Demucs ("cuda" or "cpu")

**Reference:** Similar pattern to `src/stream_of_worship/admin/config.py`

---

### Step 3: Pydantic Models (`models.py`)

**Purpose:** Define request/response schemas for API endpoints.

**Models to implement:**

```python
# Job submission models
class AnalyzeJobRequest(BaseModel):
    audio_url: str                    # s3://bucket/{hash}/audio.mp3
    content_hash: str                 # Full SHA-256 hash
    options: AnalyzeOptions = Field(default_factory=AnalyzeOptions)

class AnalyzeOptions(BaseModel):
    generate_stems: bool = True
    stem_model: str = "htdemucs"
    force: bool = False               # Re-process even if cached

class LrcJobRequest(BaseModel):
    audio_url: str
    content_hash: str
    lyrics_text: str
    options: LrcOptions = Field(default_factory=LrcOptions)

class LrcOptions(BaseModel):
    whisper_model: str = "large-v3"

# Job response models
class JobResponse(BaseModel):
    job_id: str
    status: JobStatus                 # queued, processing, completed, failed
    job_type: JobType                 # analyze, lrc
    created_at: datetime
    updated_at: datetime
    progress: float = 0.0             # 0.0 to 1.0
    stage: str = ""                   # Current processing stage
    error_message: Optional[str] = None

class JobResult(BaseModel):
    # Analysis results
    duration_seconds: Optional[float] = None
    tempo_bpm: Optional[float] = None
    musical_key: Optional[str] = None
    musical_mode: Optional[str] = None
    key_confidence: Optional[float] = None
    loudness_db: Optional[float] = None
    beats: Optional[List[float]] = None
    downbeats: Optional[List[float]] = None
    sections: Optional[List[Section]] = None
    embeddings_shape: Optional[List[int]] = None
    stems_url: Optional[str] = None
    # LRC results
    lrc_url: Optional[str] = None
    line_count: Optional[int] = None

class Section(BaseModel):
    label: str                        # intro, verse, chorus, bridge, outro
    start: float
    end: float
```

**Reference:** Based on API spec in `specs/sow_admin_design.md` lines 193-291

---

### Step 4: Job Queue Manager (`workers/queue.py`)

**Purpose:** In-memory job queue for v1 (can be swapped for Redis later).

**Key Classes:**

```python
class Job:
    id: str                           # job_abc123 format
    type: JobType                     # analyze or lrc
    status: JobStatus
    request: Union[AnalyzeJobRequest, LrcJobRequest]
    result: Optional[JobResult]
    error_message: Optional[str]
    created_at: datetime
    updated_at: datetime
    progress: float
    stage: str

class JobQueue:
    """In-memory job queue with concurrent execution control."""

    async def submit(self, job_type: JobType, request: BaseModel) -> Job:
        """Submit a new job to the queue."""

    async def get_job(self, job_id: str) -> Optional[Job]:
        """Get job status by ID."""

    async def process_jobs(self):
        """Background task that processes queued jobs."""

    async def _process_analysis_job(self, job: Job):
        """Process an analysis job."""

    async def _process_lrc_job(self, job: Job):
        """Process an LRC generation job.

        Phase 4 stub: immediately marks job as failed.  The real worker
        (workers/lrc.py) is implemented in Phase 6.
        """
        job.status = JobStatus.FAILED
        job.error_message = "LRC worker not yet implemented (Phase 6)"
        job.updated_at = datetime.now(tz=timezone.utc)
```

**Design Notes:**
- Use `asyncio` for async job processing
- Limit concurrent jobs with `asyncio.Semaphore`
- Store jobs in a `dict[str, Job]` for O(1) lookup
- Run `process_jobs()` as a background task in FastAPI
- `_process_lrc_job` is a deliberate stub; LRC worker added in Phase 6

---

### Step 5: Storage Layer (`storage/r2.py`)

**Purpose:** R2/S3-compatible storage client for downloading audio and uploading results.

**Key Methods:**

```python
import asyncio
import os

class R2Client:
    """Credentials read from SOW_R2_ACCESS_KEY_ID / SOW_R2_SECRET_ACCESS_KEY
    (same env-var names as the CLI R2Client in services/r2.py)."""

    def __init__(self, bucket: str, endpoint_url: str):
        access_key = os.environ["SOW_R2_ACCESS_KEY_ID"]
        secret_key = os.environ["SOW_R2_SECRET_ACCESS_KEY"]
        self.bucket = bucket
        self.s3 = boto3.client(
            's3',
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key
        )

    # All async methods wrap synchronous boto3 calls in run_in_executor
    # to avoid blocking the event loop (boto3 has no native async support).

    async def download_audio(self, s3_url: str, local_path: Path):
        """Download audio from R2 to local path.

        Args:
            s3_url: s3://bucket/{hash}/audio.mp3 format
            local_path: Where to save the file
        """
        bucket, key = parse_s3_url(s3_url)
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None, self.s3.download_file, bucket, key, str(local_path)
        )

    async def upload_stems(self, hash_prefix: str, stems_dir: Path) -> str:
        """Upload stem files to R2.

        Uploads: bass.wav, drums.wav, other.wav, vocals.wav
        Returns: s3://bucket/{hash}/stems/
        """
        loop = asyncio.get_event_loop()
        for stem in ("bass", "drums", "other", "vocals"):
            key = f"{hash_prefix}/stems/{stem}.wav"
            await loop.run_in_executor(
                None, self.s3.upload_file, str(stems_dir / f"{stem}.wav"),
                self.bucket, key
            )
        return f"s3://{self.bucket}/{hash_prefix}/stems/"

    async def upload_analysis_result(self, hash_prefix: str,
                                      result: dict) -> str:
        """Upload analysis.json to R2."""
        import json, tempfile
        key = f"{hash_prefix}/analysis.json"
        loop = asyncio.get_event_loop()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(result, f)
            tmp = f.name
        await loop.run_in_executor(
            None, self.s3.upload_file, tmp, self.bucket, key
        )
        return f"s3://{self.bucket}/{key}"

    async def upload_lrc(self, hash_prefix: str, lrc_path: Path) -> str:
        """Upload lyrics.lrc to R2."""
        key = f"{hash_prefix}/lyrics.lrc"
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None, self.s3.upload_file, str(lrc_path), self.bucket, key
        )
        return f"s3://{self.bucket}/{key}"
```

**S3 URL Parsing:**
```python
def parse_s3_url(s3_url: str) -> tuple[str, str]:
    """Parse s3://bucket/key to (bucket, key)."""
    # Handle s3://bucket/{hash}/audio.mp3 format
```

---

### Step 6: Cache Manager (`storage/cache.py`)

**Purpose:** Local disk cache for analysis results to avoid re-processing.

**Key Methods:**

```python
class CacheManager:
    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def get_analysis_result(self, content_hash: str) -> Optional[dict]:
        """Check if analysis result exists in cache."""
        cache_file = self.cache_dir / f"{content_hash[:32]}.json"
        if cache_file.exists():
            return json.loads(cache_file.read_text())
        return None

    def get_stems_dir(self, content_hash: str) -> Optional[Path]:
        """Check if stems exist in cache."""
        stems_dir = self.cache_dir / "stems" / content_hash[:32]
        if all((stems_dir / f"{stem}.wav").exists()
               for stem in ["bass", "drums", "other", "vocals"]):
            return stems_dir
        return None

    def save_analysis_result(self, content_hash: str, result: dict):
        """Save analysis result to cache."""

    def save_stems(self, content_hash: str, stems_dir: Path):
        """Move/copy stems to cache directory."""
```

**Cache Structure:**
```
/cache/
├── {hash_prefix}.json           # Analysis result cache
├── {hash_prefix}_lrc.json       # LRC result cache
└── stems/
    └── {hash_prefix}/
        ├── bass.wav
        ├── drums.wav
        ├── other.wav
        └── vocals.wav
```

---

### Step 7: Analysis Worker (`workers/analyzer.py`)

**Purpose:** Run allin1 analysis and librosa key detection on audio files.

**Key Function:**

```python
async def analyze_audio(
    audio_path: Path,
    cache_manager: CacheManager,
    force: bool = False
) -> dict:
    """Analyze audio file using allin1 + librosa.

    Steps:
    1. Check cache (if not force)
    2. Load audio with librosa
    3. Run allin1.analyze() for tempo/beats/sections/embeddings
    4. Run librosa chroma analysis for key detection
    5. Compute loudness/energy metrics
    6. Save to cache
    7. Return results dict

    Returns:
        Dictionary with all analysis fields for recordings table
    """
```

**Key Detection Algorithm (from poc_analysis_allinone.py:551-579):**
```python
# Krumhansl-Schmuckler key profile matching
def detect_key(y: np.ndarray, sr: int) -> tuple[str, str, float]:
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr, hop_length=512)
    chroma_avg = np.mean(chroma, axis=1)

    keys = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
    major_profile = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09,
                              2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
    minor_profile = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53,
                              2.54, 4.75, 3.98, 2.69, 3.34, 3.17])

    # Find best correlation
    correlations = []
    for shift in range(12):
        major_corr = np.corrcoef(chroma_avg, np.roll(major_profile, shift))[0, 1]
        minor_corr = np.corrcoef(chroma_avg, np.roll(minor_profile, shift))[0, 1]
        correlations.append(('major', keys[shift], major_corr))
        correlations.append(('minor', keys[shift], minor_corr))

    best_key = max(correlations, key=lambda x: x[2])
    return best_key[0], best_key[1], best_key[2]  # mode, key, confidence
```

**allin1 Integration (from poc_analysis_allinone.py:527-548):**
```python
import allin1

result = allin1.analyze(
    str(audio_path),
    out_dir=None,              # Don't save intermediate files
    visualize=False,           # No visualization in service
    include_embeddings=True,   # Extract embeddings
    sonify=False
)

# Extract results
bpm = result.bpm
beats = result.beats.tolist() if isinstance(result.beats, np.ndarray) else list(result.beats)
downbeats = result.downbeats.tolist() if isinstance(result.downbeats, np.ndarray) else list(result.downbeats)
sections = [
    {"label": seg.label, "start": seg.start, "end": seg.end}
    for seg in result.segments
]
embeddings_shape = list(result.embeddings.shape)  # [4, timesteps, 24]
```

**Performance Notes:**
- allin1 takes ~30-60 seconds per 3-5 minute song on GPU
- Run in thread pool: `await asyncio.get_event_loop().run_in_executor(...)`

---

### Step 8: Stem Separation Worker (`workers/separator.py`)

**Purpose:** Run Demucs to separate audio into 4 stems.

**Key Function:**

```python
async def separate_stems(
    audio_path: Path,
    output_dir: Path,
    model: str = "htdemucs",
    device: str = "cpu",
    cache_manager: Optional[CacheManager] = None,
    content_hash: Optional[str] = None,
    force: bool = False
) -> Path:
    """Separate audio into stems using Demucs.

    Steps:
    1. Check cache for existing stems (if not force)
    2. Run demucs.separate subprocess
    3. Move results to output_dir
    4. Cache results
    5. Return stems directory path

    Returns:
        Path to directory containing bass.wav, drums.wav, other.wav, vocals.wav
    """
```

**Demucs Invocation (from poc_analysis_allinone.py:414-425):**
```python
import subprocess
import sys

subprocess.run(
    [
        sys.executable, '-m', 'demucs.separate',
        '--out', temp_dir.as_posix(),
        '--name', model,           # htdemucs, demucs, etc.
        '--device', device,        # cuda or cpu
        audio_path.as_posix(),
    ],
    check=True,
    capture_output=True,           # Suppress verbose output
    text=True
)

# Stems are created in: temp_dir / model / audio_path.stem / {stem}.wav
# Move to: output_dir / {stem}.wav
```

**Performance Notes:**
- Demucs takes ~2-5 minutes per song on GPU, ~10-15 minutes on CPU
- Always run in thread pool or subprocess to not block event loop

---

### Step 8b: LRC Worker Stub (`workers/lrc.py`)

**Purpose:** Placeholder for the LRC generation worker.  The `/jobs/lrc`
endpoint and request models are defined in Phase 4 so the API surface is
complete, but the actual Whisper + LLM alignment logic is deferred to Phase 6.

```python
"""LRC generation worker — stub.

Real implementation added in Phase 6 (Whisper transcription + LLM alignment).
"""


class LRCWorkerNotImplementedError(Exception):
    """Raised when LRC generation is attempted before Phase 6."""


async def generate_lrc(audio_path, lyrics_text, options):
    """Generate timestamped LRC file.

    TODO (Phase 6): Implement Whisper transcription + LLM line alignment.
    """
    raise LRCWorkerNotImplementedError("LRC worker not yet implemented (Phase 6)")
```

**Integration:** `queue.py::_process_lrc_job` catches this exception and sets
the job status to `failed` with the error message surfaced in the API response.

---

### Step 9: FastAPI Routes

#### Health Check (`routes/health.py`)

```python
from fastapi import APIRouter

router = APIRouter()

@router.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "version": "0.1.0",
        "services": {
            "r2": check_r2_connection(),
            "cache": check_cache_access()
        }
    }
```

#### Jobs API (`routes/jobs.py`)

```python
from fastapi import APIRouter, Depends, HTTPException, Header
from typing import Optional

router = APIRouter()

async def verify_api_key(
    authorization: Optional[str] = Header(None)
) -> str:
    """Verify Bearer token matches ANALYSIS_API_KEY."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing or invalid Authorization header")
    token = authorization[7:]
    if token != settings.ANALYSIS_API_KEY:
        raise HTTPException(401, "Invalid API key")
    return token

@router.post("/jobs/analyze", response_model=JobResponse)
async def submit_analysis_job(
    request: AnalyzeJobRequest,
    api_key: str = Depends(verify_api_key)
):
    """Submit audio for analysis."""
    job = await job_queue.submit(JobType.ANALYZE, request)
    return job_to_response(job)

@router.post("/jobs/lrc", response_model=JobResponse)
async def submit_lrc_job(
    request: LrcJobRequest,
    api_key: str = Depends(verify_api_key)
):
    """Submit LRC generation job."""
    job = await job_queue.submit(JobType.LRC, request)
    return job_to_response(job)

@router.get("/jobs/{job_id}", response_model=JobResponse)
async def get_job_status(
    job_id: str,
    api_key: str = Depends(verify_api_key)
):
    """Get job status and results."""
    job = await job_queue.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job_to_response(job)
```

---

### Step 10: FastAPI Application (`main.py`)

```python
from fastapi import FastAPI
from contextlib import asynccontextmanager

from .config import settings
from .routes import health, jobs
from .workers.queue import JobQueue

# Global job queue instance
job_queue: JobQueue

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifespan."""
    # Startup
    global job_queue
    job_queue = JobQueue(
        max_concurrent=settings.MAX_CONCURRENT_JOBS,
        cache_dir=settings.CACHE_DIR
    )
    # Start background job processor
    task = asyncio.create_task(job_queue.process_jobs())

    yield

    # Shutdown
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

app = FastAPI(
    title="Stream of Worship Analysis Service",
    version="0.1.0",
    lifespan=lifespan
)

app.include_router(health.router, prefix="/api/v1")
app.include_router(jobs.router, prefix="/api/v1")

@app.get("/")
async def root():
    return {"message": "Stream of Worship Analysis Service", "version": "0.1.0"}
```

---

### Step 11: Docker Configuration

#### `services/analysis/Dockerfile`

```dockerfile
# Use Python 3.11 slim as base
FROM python:3.11-slim

# Platform arg required for conditional PyTorch/NATTEN install
# (mirrors docker/Dockerfile.allinone which is the proven working baseline)
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
# Copied from docker/Dockerfile.allinone:33-44
RUN if [ "$TARGETPLATFORM" = "linux/amd64" ]; then \
        uv pip install --no-cache \
            --index-url https://download.pytorch.org/whl/cpu \
            torch==2.4.1+cpu \
            torchaudio==2.4.1+cpu \
            torchvision==0.19.1+cpu; \
    else \
        uv pip install --no-cache torch==2.4.1; \
    fi

# Copy and install service dependencies (allin1, demucs, librosa, fastapi, etc.)
COPY pyproject.toml .
RUN uv pip install --no-cache --no-build-isolation -e ".[service]"

# Install NATTEN from source — platform-conditional flags
# Copied from docker/Dockerfile.allinone:49-58
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
```

#### `services/analysis/pyproject.toml`

```toml
[project]
name = "sow-analysis"
version = "0.1.0"
description = "Audio analysis service for Stream of Worship"
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.109.0",
    "uvicorn[standard]>=0.27.0",
    "pydantic>=2.0.0",
    "pydantic-settings>=2.0.0",
    "allin1>=1.1.0",
    "demucs>=4.0.0",
    "librosa>=0.10.0",
    "boto3>=1.34.0",
    "numpy>=1.24.0",
    "httpx>=0.26.0",
]

[project.optional-dependencies]
service = ["python-multipart>=0.0.6"]
dev = ["pytest>=7.4.0", "pytest-asyncio>=0.23.0"]

[project.scripts]
sow-analysis = "sow_analysis.main:main"

[build-system]
requires = ["setuptools>=68.0"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["src"]
```

#### `services/analysis/docker-compose.yml`

```yaml
version: '3.8'

services:
  analysis:
    build:
      context: .
      dockerfile: Dockerfile
      args:
        - TARGETPLATFORM=${TARGETPLATFORM:-linux/amd64}
    ports:
      - "8000:8000"
    environment:
      - R2_BUCKET=${R2_BUCKET:-sow-audio}
      - R2_ENDPOINT_URL=${R2_ENDPOINT_URL}
      - SOW_R2_ACCESS_KEY_ID=${SOW_R2_ACCESS_KEY_ID}
      - SOW_R2_SECRET_ACCESS_KEY=${SOW_R2_SECRET_ACCESS_KEY}
      - ANALYSIS_API_KEY=${ANALYSIS_API_KEY}
      - MAX_CONCURRENT_JOBS=${MAX_CONCURRENT_JOBS:-2}
      - DEMUCS_DEVICE=${DEMUCS_DEVICE:-cpu}
    volumes:
      - analysis-cache:/cache
    deploy:
      resources:
        reservations:
          devices:
            # GPU block requires nvidia-container-toolkit.
            # For CPU-only fallback, remove the entire deploy.resources block.
            - driver: nvidia
              count: 1
              capabilities: [gpu]

volumes:
  analysis-cache:
```

---

### Step 12: Testing Strategy

**Unit Tests:**
```python
# tests/services/analysis/test_models.py
# tests/services/analysis/test_cache.py
# tests/services/analysis/test_r2.py          (mock boto3)
# tests/services/analysis/test_queue.py       (includes LRC stub behavior)
```

**Integration Tests:**
```python
# tests/services/analysis/test_api.py
# Test job submission, status polling, results
# Test that POST /jobs/lrc returns a job that transitions to failed with
#   error_message == "LRC worker not yet implemented (Phase 6)"
```

**Manual Testing:**
```bash
# 1. Start service
cd services/analysis
docker-compose up --build

# 2. Test health endpoint
curl http://localhost:8000/api/v1/health

# 3. Test analysis job submission
curl -X POST http://localhost:8000/api/v1/jobs/analyze \
  -H "Authorization: Bearer $ANALYSIS_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "audio_url": "s3://sow-audio/c6de4449928d/audio.mp3",
    "content_hash": "c6de4449928d...",
    "options": {"generate_stems": true}
  }'

# 4. Poll for status
curl http://localhost:8000/api/v1/jobs/job_abc123 \
  -H "Authorization: Bearer $ANALYSIS_API_KEY"
```

---

## Dependencies

### New Dependencies (in `services/analysis/pyproject.toml`)

| Package | Version | Purpose |
|---------|---------|---------|
| fastapi | >=0.109.0 | Web framework |
| uvicorn | >=0.27.0 | ASGI server |
| pydantic-settings | >=2.0.0 | Configuration management |
| allin1 | >=1.1.0 | Music analysis (tempo, beats, sections) |
| demucs | >=4.0.0 | Stem separation |
| librosa | >=0.10.0 | Audio processing, key detection |
| boto3 | >=1.34.0 | R2/S3 storage |
| httpx | >=0.26.0 | HTTP client for webhooks |

### System Dependencies (Dockerfile)
- ffmpeg
- libsndfile1
- gcc/g++ (for compiling Python extensions)
- git/cmake (for building dependencies)
- natten==0.17.1 (installed from source; platform-conditional flags required — see Dockerfile)

---

## Integration with Existing Code

### Database Schema (already exists)

The `recordings` table in `src/stream_of_worship/admin/db/schema.py` already has all necessary fields:

```sql
-- Analysis metadata (populated by analysis service)
duration_seconds REAL,
tempo_bpm REAL,
musical_key TEXT,
musical_mode TEXT,
key_confidence REAL,
loudness_db REAL,
beats TEXT,                    -- JSON array
downbeats TEXT,                -- JSON array
sections TEXT,                 -- JSON array
embeddings_shape TEXT,         -- JSON array [4, timesteps, 24]

-- Processing status
analysis_status TEXT DEFAULT 'pending',
analysis_job_id TEXT,
lrc_status TEXT DEFAULT 'pending',
lrc_job_id TEXT,
```

### CLI Integration (Phase 5)

After Phase 4, Phase 5 will add:
- `src/stream_of_worship/admin/services/analysis.py` - HTTP client for service
- `src/stream_of_worship/admin/commands/audio.py` - `analyze` and `lrc` commands

---

## Risk Mitigation

| Risk | Mitigation |
|------|------------|
| allin1 requires GPU/NATTEN | Use existing working Dockerfile.allinone as base |
| Demucs is slow on CPU | Support GPU in docker-compose.yml, document CPU fallback |
| Large Docker image | Multi-stage build, don't include dev dependencies |
| Job queue memory usage | Limit concurrent jobs, add job expiration |
| R2 upload failures | Retry with exponential backoff, mark job as failed |

---

## Success Criteria

1. Service starts successfully with `docker-compose up`
2. Health endpoint returns 200
3. Can submit analysis job via API
4. Job completes with correct analysis results
5. Stems are generated and uploaded to R2
6. Cache prevents re-processing same hash
7. API key authentication works

---

## References

- Design spec: `specs/sow_admin_design.md` lines 189-307 (Analysis Service API)
- POC analysis: `poc/poc_analysis_allinone.py` (allin1 + Demucs integration)
- Docker setup: `docker/Dockerfile.allinone` (working allin1 environment)
- Database schema: `src/stream_of_worship/admin/db/schema.py`
