# Phase 1: Qwen3 Service Foundation - Research

**Researched:** 2026-02-13
**Domain:** FastAPI microservice for forced alignment using Qwen3-ForcedAligner
**Confidence:** HIGH

## Summary

Phase 1 requires building a standalone FastAPI microservice that loads the Qwen3-ForcedAligner-0.6B model (~1.2GB) at startup, exposes a forced alignment API endpoint, and runs in an isolated Docker environment. The service aligns known lyrics to audio timestamps using character-level alignment from qwen-asr library.

Key research findings:
1. **qwen-asr library** provides the `Qwen3ForcedAligner` class with `from_pretrained()` and `align()` methods (verified via PyPI docs)
2. **FastAPI lifespan events** with `@asynccontextmanager` is the recommended pattern for model loading at startup (verified via FastAPI official docs)
3. **Pydantic Settings** provides environment-based configuration that matches the existing Analysis Service pattern (verified via Pydantic docs)
4. **Docker resource constraints** support memory (`-m`) and CPU (`--cpus`) limits for ML services (verified via Docker docs)

The existing Analysis Service (`services/analysis/`) provides a proven template to follow for project structure, FastAPI patterns, and Docker configuration.

**Primary recommendation:** Follow the Analysis Service pattern with lifespan-based model loading, Pydantic settings, APIRouter endpoints, and Docker compose configuration.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**API Contract**
- Audio input: URL reference (R2/S3 URL) — aligns with existing storage pattern
- Response format: Both LRC and JSON (configurable via request flag)
- Timestamp granularity: Line-level only (not word/character-level karaoke style)
- Parameters: Language hint only (e.g., "zh" for Chinese)

**Model Loading Strategy**
- Load timing: At service startup (not lazy-load)
- Caching: Stay loaded as singleton (don't reload)
- Health check: `/health` returns 200 only when model is loaded and ready
- Model source: Mount from host volume (pre-downloaded, not runtime download)

**Error Handling**
- Audio >5 minutes: HTTP 400 Bad Request
- Error format: Simple string message (plain text)
- Model inference failure: HTTP 500 Internal Server Error
- Logging: Error level with full traceback for debugging

**Resource Management**
- Device selection: Environment variable `SOW_QWEN3_DEVICE` (auto-detect CUDA → MPS → CPU if not set)
- Concurrency: Limited by semaphore (configurable, not unlimited)
- Docker memory: Explicit 8GB limit
- Docker CPU: Limit to 4 cores

### Claude's Discretion

- Exact FastAPI route structure (use standard patterns)
- Pydantic model naming conventions
- Internal logging format beyond error tracebacks
- Dockerfile base image selection (use Python 3.11 slim)

### Deferred Ideas (OUT OF SCOPE)

- Word/character-level timestamp output (karaoke style) — out of scope for v1.1, revisit in v2
- Batch alignment endpoint (multiple songs) — future enhancement
- Runtime model download — not needed, using volume mount approach
- Auto-scaling based on queue depth — production optimization for later
</user_constraints>

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `fastapi` | >=0.109.0 | Web framework for microservice | Existing project pattern, mature async framework |
| `uvicorn[standard]` | >=0.27.0 | ASGI server for FastAPI | Recommended server for FastAPI |
| `pydantic` | >=2.0.0 | Data validation and settings | Existing project pattern, type-safe |
| `pydantic-settings` | >=2.0.0 | Environment variable configuration | Existing project pattern |
| `qwen-asr` | Latest | Forced aligner model | Required library for Qwen3ForcedAligner |
| `torch` | Latest (via qwen-asr) | PyTorch backend | Required dependency for qwen-asr |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `pydub` | Latest | Audio duration checking | Before alignment to enforce 5-minute limit |
| `boto3` | >=1.34.0 | S3/R2 audio download | If service needs to fetch audio from storage |
| `httpx` | >=0.26.0 | Async HTTP client | If fetching from remote URLs directly |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| `qwen-asr` | `whisperx` | qwen-asr has forced alignment capability, WhisperX requires transcription first |
| `fastapi` | `flask` | FastAPI has native async support and OpenAPI docs |
| `lifespan` function | `on_event` decorators | lifespan is recommended approach, on_event is deprecated |

**Installation:**
```bash
# Dependencies (pyproject.toml)
[project]
dependencies = [
    "fastapi>=0.109.0",
    "uvicorn[standard]>=0.27.0",
    "pydantic>=2.0.0",
    "pydantic-settings>=2.0.0",
    "qwen-asr>=0.1.0",
    "pydub>=0.25.0",
    "boto3>=1.34.0",
    "httpx>=0.26.0",
]

[project.optional-dependencies]
service = ["python-multipart>=0.0.6"]

[project.scripts]
sow-qwen3 = "sow_qwen3.main:main"
```

## Architecture Patterns

### Recommended Project Structure

```
services/qwen3/
├── docker-compose.yml          # Docker Compose configuration
├── Dockerfile                  # Multi-platform Docker build
├── pyproject.toml             # Python dependencies
├── pyproject.lock             # Lock file for reproducible builds
├── README.md                  # Service documentation
└── src/sow_qwen3/
    ├── __init__.py            # Package init with version
    ├── main.py                # FastAPI app entry point
    ├── config.py              # Pydantic settings
    ├── models.py              # Pydantic models for API
    ├── routes/
    │   ├── __init__.py
    │   ├── align.py           # Alignment endpoint
    │   └── health.py          # Health check endpoint
    ├── storage/
    │   ├── __init__.py
    │   ├── audio.py           # Audio download handling
    │   └── cache.py           # Temporary audio cache
    └── workers/
        ├── __init__.py
        └── aligner.py         # Qwen3ForcedAligner wrapper
```

### Pattern 1: FastAPI Lifespan for Model Loading

**What:** Use `@asynccontextmanager` to load the model at startup and clean up at shutdown. This ensures the model is in memory before accepting requests.

**When to use:** For long-lived ML models that should stay loaded as singleton.

**Example:** Based on FastAPI official documentation
```python
from contextlib import asynccontextmanager
from fastapi import FastAPI

from .workers.aligner import Qwen3AlignerWrapper
from .config import settings

aligner: Qwen3AlignerWrapper | None = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load model at startup, clean up at shutdown."""
    global aligner

    # Startup: Load the model
    aligner = Qwen3AlignerWrapper(
        model_path=settings.SOW_QWEN3_MODEL_PATH,
        device=settings.SOW_QWEN3_DEVICE,
        max_concurrent=settings.SOW_QWEN3_MAX_CONCURRENT,
    )
    await aligner.initialize()

    yield

    # Shutdown: Clean up
    await aligner.cleanup()

app = FastAPI(
    title="Stream of Worship Qwen3 Alignment Service",
    version=__version__,
    lifespan=lifespan,
)
```

**Source:** [FastAPI Lifespan Events Documentation](https://fastapi.tiangolo.com/advanced/events/)

### Pattern 2: Pydantic Settings for Configuration

**What:** Use `BaseSettings` with `SettingsConfigDict` for environment-based configuration.

**When to use:** For service configuration that can be overridden via environment variables.

**Example:** Based on existing Analysis Service and Pydantic documentation
```python
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    """Qwen3 alignment service configuration."""

    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=True,
        env_prefix="SOW_QWEN3_",
    )

    # Model Configuration
    MODEL_PATH: Path = Path("/models/qwen3-forced-aligner")
    DEVICE: str = "auto"  # auto/mps/cuda/cpu
    DTYPE: str = "float32"  # bfloat16/float16/float32
    MAX_CONCURRENT: int = 1  # Max concurrent alignments

    # R2/S3 Configuration (for audio download)
    R2_BUCKET: str = ""
    R2_ENDPOINT_URL: str = ""
    R2_ACCESS_KEY_ID: str = ""
    R2_SECRET_ACCESS_KEY: str = ""

    # API Security
    API_KEY: str = ""

    # Cache Configuration
    CACHE_DIR: Path = Path("/cache")

settings = Settings()
```

**Source:** [Pydantic Settings Documentation](https://docs.pydantic.dev/latest/concepts/pydantic_settings/)

### Pattern 3: APIRouter for Modular Routes

**What:** Use `APIRouter` to organize endpoints into modules, then include routers in the main app.

**When to use:** For microservices with multiple endpoints to keep code organized.

**Example:** Based on existing Analysis Service
```python
# routes/align.py
from fastapi import APIRouter, HTTPException, Depends
from ..models import AlignRequest, AlignResponse
from ..config import settings
from ..workers.aligner import get_aligner

router = APIRouter()

async def verify_api_key(authorization: str | None = Header(None)) -> str:
    """Verify Bearer token matches API_KEY."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing or invalid Authorization header")
    token = authorization[7:]
    if token != settings.API_KEY:
        raise HTTPException(401, "Invalid API key")
    return token

@router.post("/api/v1/align", response_model=AlignResponse)
async def align_lyrics(
    request: AlignRequest,
    api_key: str = Depends(verify_api_key),
) -> AlignResponse:
    """Align lyrics to audio timestamps."""
    aligner = get_aligner()
    if aligner is None:
        raise HTTPException(503, "Model not loaded")

    return await aligner.align(
        audio_url=request.audio_url,
        lyrics_text=request.lyrics_text,
        language=request.language,
        format=request.format,
    )
```

### Pattern 4: Singleton Aligner Wrapper with Semaphore

**What:** Create a wrapper class that manages the Qwen3ForcedAligner model with a semaphore to limit concurrent requests.

**When to use:** When the model is not thread-safe or has memory constraints.

**Example:** Adapted from POC and FastAPI patterns
```python
import asyncio
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

class Qwen3AlignerWrapper:
    """Wrapper for Qwen3ForcedAligner with concurrency control."""

    def __init__(
        self,
        model_path: Path,
        device: str = "auto",
        max_concurrent: int = 1,
    ):
        self.model_path = model_path
        self.device = device
        self._model = None
        self._ready = False
        self._semaphore = asyncio.Semaphore(max_concurrent)

    async def initialize(self):
        """Load the model (runs in thread pool to avoid blocking)."""
        loop = asyncio.get_event_loop()

        def _load_model():
            import torch
            from qwen_asr import Qwen3ForcedAligner

            # Determine device
            device = self.device
            if device == "auto":
                if torch.backends.mps.is_available():
                    device = "mps"
                elif torch.cuda.is_available():
                    device = "cuda"
                else:
                    device = "cpu"

            dtype_map = {
                "bfloat16": torch.bfloat16,
                "float16": torch.float16,
                "float32": torch.float32,
            }
            torch_dtype = dtype_map.get("float32", torch.float32)

            model = Qwen3ForcedAligner.from_pretrained(
                str(self.model_path),
                dtype=torch_dtype,
                device_map=device,
            )
            return model

        self._model = await loop.run_in_executor(None, _load_model)
        self._ready = True
        logger.info("Qwen3ForcedAligner loaded and ready")

    async def align(
        self,
        audio_path: Path,
        lyrics_text: str,
        language: str = "Chinese",
    ) -> dict:
        """Run alignment with concurrency control."""
        if not self._ready:
            raise RuntimeError("Model not loaded")

        async with self._semaphore:
            loop = asyncio.get_event_loop()

            def _call_align():
                results = self._model.align(
                    audio=str(audio_path),
                    text=lyrics_text,
                    language=language,
                )
                return results

            return await loop.run_in_executor(None, _call_align)

    async def cleanup(self):
        """Clean up resources."""
        self._ready = False
        self._model = None
        logger.info("Qwen3ForcedAligner cleaned up")
```

**Source:** POC code at `/home/mhuang/Development/stream_of_worship/poc/gen_lrc_qwen3.py`

### Anti-Patterns to Avoid

- **Module-level model loading**: Loading the model at module import time slows down tests and prevents proper resource management.
- **on_event decorators**: The `@app.on_event("startup")` and `@app.on_event("shutdown")` decorators are deprecated in favor of the lifespan function.
- **Unlimited concurrency**: Multiple alignment requests can exhaust memory; use semaphore or other concurrency control.
- **Lazy loading without readiness check**: Accepting requests before the model is loaded creates race conditions and poor UX.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Environment variable parsing | Custom `os.getenv()` with manual conversion | `pydantic-settings.BaseSettings` | Handles type conversion, defaults, validation, env_file |
| Request validation | Manual `request.json()` with isinstance checks | `pydantic.BaseModel` with FastAPI | Automatic validation, error responses, OpenAPI schema |
| Async concurrency control | Manual `threading.Lock` or queue | `asyncio.Semaphore` | Built for async, works with asyncio |
| Model loading in thread | Manual `ThreadPoolExecutor` creation | `loop.run_in_executor(None, func)` | FastAPI's recommended pattern |
| Audio duration checking | Manual audio parsing with librosa | `pydub.AudioSegment` | Simpler API for getting duration |

**Key insight:** Modern Python async/ML ecosystem has mature libraries for common tasks. Building custom solutions adds maintenance burden and potential bugs.

## Common Pitfalls

### Pitfall 1: Model Loading Blocks Startup

**What goes wrong:** Loading the 1.2GB Qwen3 model synchronously blocks the event loop, preventing health checks and making the service unresponsive during startup.

**Why it happens:** `Qwen3ForcedAligner.from_pretrained()` performs CPU-bound I/O and memory operations.

**How to avoid:** Always run model loading in a thread pool using `loop.run_in_executor(None, _load_model)`.

**Warning signs:** Service takes >10 seconds to start responding to health checks.

### Pitfall 2: Multiple Simultaneous Alignment Requests Exhaust Memory

**What goes wrong:** Multiple concurrent alignment requests each hold large audio buffers in memory, leading to OOM kills.

**Why it happens:** PyTorch models and audio processing are memory-intensive.

**How to avoid:** Use `asyncio.Semaphore` to limit concurrent alignment requests to a configured maximum (default: 1 for safety).

**Warning signs:** Container crashes with OOM messages, slow response times under load.

### Pitfall 3: Device Auto-Detection Fails on New Hardware

**What goes wrong:** The auto-detection logic for MPS/CUDA doesn't recognize newer hardware and defaults to CPU unnecessarily.

**Why it happens:** `torch.backends.mps.is_available()` and `torch.cuda.is_available()` may return false for some configurations.

**How to avoid:** Provide explicit device configuration via environment variable with sensible fallback behavior.

**Warning signs:** Slow inference times, high CPU usage when GPU is expected.

### Pitfall 4: Audio Duration Check Happens After Download

**What goes wrong:** Audio is downloaded first, then checked for duration, wasting bandwidth on files that exceed the 5-minute limit.

**Why it happens:** Download and validation logic are ordered incorrectly.

**How to avoid:** Check metadata (if available) or download headers first before full download. For R2/S3, use object metadata.

**Warning signs:** Slow responses for long files, unnecessary bandwidth usage.

### Pitfall 5: Health Check Returns 200 Before Model Loads

**What goes wrong:** Health endpoint returns success before the model is ready, causing client 500 errors when they try to use the service.

**Why it happens:** Simplistic health checks don't verify model state.

**How to avoid:** Check `aligner.is_ready()` or similar flag in the health endpoint.

**Warning signs:** Client requests fail immediately after service startup with 500 errors.

## Code Examples

Verified patterns from official sources:

### Qwen3ForcedAligner Basic Usage

**Source:** POC code at `/home/mhuang/Development/stream_of_worship/poc/gen_lrc_qwen3.py` and PyPI documentation
```python
import torch
from qwen_asr import Qwen3ForcedAligner

# Load model
model = Qwen3ForcedAligner.from_pretrained(
    "Qwen/Qwen3-ForcedAligner-0.6B",
    dtype=torch.float32,
    device_map="mps" if torch.backends.mps.is_available() else "cpu",
)

# Run alignment
results = model.align(
    audio="audio.wav",  # Local path, URL, or (np.ndarray, sr) tuple
    text="这是一段中文文本。",
    language="Chinese",
)

# Extract results
for segment_list in results:
    for segment in segment_list:
        print(f"{segment.start_time:.2f}s - {segment.end_time:.2f}s: {segment.text}")
```

**Source:** [PyPI qwen-asr Package](https://pypi.org/project/qwen-asr/)

### Audio Duration Check with pydub

**Source:** POC code at `/home/mhuang/Development/stream_of_worship/poc/gen_lrc_qwen3.py`
```python
from pydub import AudioSegment
from pathlib import Path

def get_audio_duration(audio_path: Path) -> float:
    """Get audio duration in seconds."""
    audio = AudioSegment.from_file(str(audio_path))
    return len(audio) / 1000.0

# Check 5-minute limit
duration = get_audio_duration(audio_path)
if duration > 300:
    raise ValueError(f"Audio duration ({duration:.1f}s) exceeds 5 minute limit")
```

### Pydantic Request/Response Models

**Source:** Existing Analysis Service at `/home/mhuang/Development/stream_of_worship/services/analysis/src/sow_analysis/models.py`
```python
from pydantic import BaseModel, Field
from enum import Enum

class OutputFormat(str, Enum):
    LRC = "lrc"
    JSON = "json"

class AlignRequest(BaseModel):
    """Request to align lyrics to audio."""

    audio_url: str = Field(..., description="Audio file URL (R2/S3)")
    lyrics_text: str = Field(..., description="Lyrics text to align")
    language: str = Field(default="Chinese", description="Language hint")
    format: OutputFormat = Field(default=OutputFormat.LRC, description="Output format")

class AlignResponse(BaseModel):
    """Response from alignment."""

    lrc_content: str | None = None
    json_data: dict | None = None
    line_count: int = Field(..., description="Number of aligned lines")
    duration_seconds: float = Field(..., description="Audio duration")
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `@app.on_event("startup")`装饰器 | `@asynccontextmanager` lifespan function | FastAPI 0.87.0+ (2023) | lifespan is recommended pattern, on_event is deprecated |
| `BaseSettings` 直接继承 | `BaseSettings` with `SettingsConfigDict` | Pydantic v2.0 (2023) | More explicit configuration, better type safety |
| Transformers 2.28 | Transformers current | Ongoing | Better performance, more model support |

**Deprecated/outdated:**
- **FastAPI Startup/Shutdown Events**: Use lifespan function instead for new code (on_event still works but is not recommended)
- **Pydantic v1 Settings API**: Project uses Pydantic v2, use v2 API only

## Open Questions

1. **Model Volume Mount Strategy**
   - What we know: CONTEXT.md specifies mount from host volume at `/models/qwen3-forced-aligner`
   - What's unclear: Whether the model should be downloaded ahead of time by a script or if the service should have fallback download logic
   - Recommendation: Implement pre-download script as separate step, service assumes model exists and fails gracefully if not found

2. **Audio Download Strategy**
   - What we know: Audio input is R2/S3 URL reference
   - What's unclear: Whether service should download audio itself or expect audio pre-downloaded to a shared location
   - Recommendation: Implement audio download in service using boto3 for R2 compatibility, cache downloaded audio locally

3. **Health Check Failure Model**
   - What we know: `/health` returns 200 only when model is loaded and ready
   - What's unclear: What health check should return during model loading phase (before ready)
   - Recommendation: Return 503 Service Unavailable with `"status": "loading"` until model is ready, then 200 with `"status": "ready"`

4. **Semaphore Value Recommendation**
   - What we know: Concurrency should be limited (not unlimited)
   - What's unclear: Appropriate default value for SOW_QWEN3_MAX_CONCURRENT
   - Recommendation: Start with 1 for safety (serialized alignment), allow configuration via env var, document tradeoffs for different values

## Sources

### Primary (HIGH confidence)

- **Analysis Service Source Code**: `/home/mhuang/Development/stream_of_worship/services/analysis/` - Existing microservice patterns verified by reading actual code
- **POC Qwen3 Script**: `/home/mhuang/Development/stream_of_worship/poc/gen_lrc_qwen3.py` - Working implementation of Qwen3ForcedAligner
- **PyPI qwen-asr Package** - Installation, API reference, usage examples: https://pypi.org/project/qwen-asr/
- **FastAPI Lifespan Docs** - Recommended startup/shutdown pattern: https://fastapi.tiangolo.com/advanced/events/
- **Pydantic Settings Docs** - Environment variable configuration: https://docs.pydantic.dev/latest/concepts/pydantic_settings/
- **Docker Resource Constraints** - Memory and CPU limits: https://docs.docker.com/engine/containers/run/#resource-constraints

### Secondary (MEDIUM confidence)

- **FastAPI Official Docs** - General best practices (verified by web search)
- **Pydantic v2 Documentation** - BaseModel, Validation, Settings (verified by web search)

### Tertiary (LOW confidence)

- **WebSearch results** - Used only for finding official docs, not for API details

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - All sources are official docs or working code
- Architecture: HIGH - Based on existing working Analysis Service and official FastAPI docs
- Pitfalls: HIGH - Based on POC testing, Docker docs, and common ML service patterns

**Research date:** 2026-02-13
**Valid until:** 2026-03-15 (30 days - stable ecosystem, unlikely to have breaking changes)
