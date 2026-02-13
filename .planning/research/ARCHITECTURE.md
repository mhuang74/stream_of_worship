# Architecture Research: Qwen3 ForcedAligner Integration

**Domain:** Audio Analysis Services
**Researched:** 2026-02-13
**Confidence:** HIGH

## Standard Architecture

### System Overview

```
Current Analysis Service Architecture:
┌─────────────────────────────────────────────────────────────────────────┐
│                        Admin CLI / User App                             │
│                     (External Clients)                                  │
└──────────────────────┬──────────────────────────────────────────────────┘
                       │ HTTP POST /api/v1/jobs/lrc
                       │
                       ↓
┌─────────────────────────────────────────────────────────────────────────┐
│                    Analysis Service (FastAPI)                           │
│                       services/analysis/                                 │
├─────────────────────────────────────────────────────────────────────────┤
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐              │
│  │ Main     │  │ Jobs     │  │ Health   │  │ Storage  │              │
│  │ (uvicorn)│  │ Route    │  │ Route    │  │ Layer    │              │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘              │
├─────────────────────────────────────────────────────────────────────────┤
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │                        Job Queue                                │    │
│  │  (in-memory, async workers with semaphore control)              │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│         │                                                               │
│         ├─── LRC Worker ──→ YouTube Transcript (primary)                │
│         │                   └─→ Whisper + LLM (fallback)                │
│         │                                                               │
│         └─── Analysis Worker ──→ allin1 + Demucs                        │
├─────────────────────────────────────────────────────────────────────────┤
│                        Cloudflare R2                                   │
│                   (Audio stems, LRC files)                              │
└─────────────────────────────────────────────────────────────────────────┘
```

```
Recommended Architecture with Qwen3 Service:
┌─────────────────────────────────────────────────────────────────────────┐
│                        Admin CLI / User App                             │
│                     (External Clients)                                  │
└──────────────────────┬──────────────────────────────────────────────────┘
                       │ HTTP POST /api/v1/jobs/lrc
                       │
                       ↓
┌─────────────────────────────────────────────────────────────────────────┐
│                    Analysis Service (FastAPI)                           │
│                 services/analysis/ (modified)                           │
├─────────────────────────────────────────────────────────────────────────┤
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐              │
│  │ Main     │  │ Jobs     │  │ Health   │  │ Storage  │              │
│  │ (uvicorn)│  │ Route    │  │ Route    │  │ (R2)     │              │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘              │
├─────────────────────────────────────────────────────────────────────────┤
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │                        Job Queue                                │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│         │                                                               │
│         ├─── LRC Worker ──→ YouTube Transcript (primary)                │
│         │                   └─→ Whisper + LLM (fallback)                │
│         │                   └─→ Qwen3 Service (NEW - via HTTP)          │
│         │                                                               │
│         └─── Analysis Worker ──→ allin1 + Demucs                        │
└────────────────────┬────────────────────────────────────────────────────┘
                     │ HTTP POST /align (NEW)
                     │
                     ↓
┌─────────────────────────────────────────────────────────────────────────┐
│              Qwen3 Alignment Service (FastAPI) NEW                      │
│                   services/qwen3_align/                                  │
├─────────────────────────────────────────────────────────────────────────┤
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐              │
│  │ Main     │  │ Align    │  │ Health   │  │ Model    │              │
│  │ (uvicorn)│  │ Route    │  │ Route    │  │ Cache    │              │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘              │
├─────────────────────────────────────────────────────────────────────────┤
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │              Qwen3ForcedAligner Worker                           │    │
│  │    - Model: Qwen/Qwen3-ForcedAligner-0.6B                       │    │
│  │    - Device: auto/mps/cuda/cpu                                   │    │
│  │    - Max duration: 5 minutes                                    │    │
│  │    - Map character segments → lyric lines                       │    │
│  └─────────────────────────────────────────────────────────────────┘    │
├─────────────────────────────────────────────────────────────────────────┤
│                    HuggingFace Cache                                   │
│                   (~1.2GB model cache)                                 │
└─────────────────────────────────────────────────────────────────────────┘
```

### Component Responsibilities

| Component | Responsibility | Typical Implementation |
|-----------|----------------|------------------------|
| **Analysis Service** | Job orchestration, R2 storage, LRC generation coordination | FastAPI, AsyncIO, boto3 |
| **LRC Worker** | LRC pipeline: YouTube → Whisper/LLM → Qwen3 (via service) | Python async functions |
| **Qwen3 Service** | Forced alignment using Qwen3ForcedAligner model | FastAPI + qwen-asr library |
| **Job Queue** | Concurrent job processing with limits | asyncio.Queue + Semaphore |

## Recommended Project Structure

```
stream_of_worship/
├── services/
│   ├── analysis/                    # Existing Analysis Service
│   │   ├── src/sow_analysis/
│   │   │   ├── workers/
│   │   │   │   ├── lrc.py           # MODIFIED - add Qwen3 client
│   │   │   │   └── qwen3_client.py  # NEW - HTTP client to Qwen3 service
│   │   │   └── ...
│   │   ├── docker-compose.yml       # UPDATED - add qwen3 service
│   │   └── pyproject.toml           # UNCHANGED - no new deps
│   │
│   └── qwen3_align/                 # NEW - Qwen3 Alignment Service
│       ├── src/qwen3_align/
│       │   ├── __init__.py
│       │   ├── main.py              # FastAPI app
│       │   ├── config.py            # Service configuration
│       │   ├── models.py            # Request/response schemas
│       │   ├── routes/
│       │   │   ├── __init__.py
│       │   │   ├── health.py        # GET /health
│       │   │   └── align.py         # POST /align
│       │   └── workers/
│       │       ├── __init__.py
│       │       └── aligner.py       # Qwen3ForcedAligner worker
│       ├── Dockerfile               # NEW - Qwen3-specific build
│       └── pyproject.toml           # NEW - qwen3-asr dependency
│
├── src/stream_of_worship/admin/
│   └── commands/audio.py            # UNCHANGED - no direct Qwen3 deps
│
└── .planning/research/
    └── ARCHITECTURE.md               # This file
```

### Structure Rationale

- **services/qwen3_align/**: Isolation from allin1/demucs dependencies prevents PyTorch version conflicts
- **separate docker-compose**: Independent scaling (Analysis Service = 1 job, Qwen3 = 2+ jobs concurrently)
- **HTTP comm between services**: Clean boundary, allows Qwen3 service to be optional/fallible
- **No changes to Admin CLI**: Qwen3 integration is internal to services layer

## Architectural Patterns

### Pattern 1: Separate Service for Isolated Dependencies

**What:** Deploy heavy/fragile dependencies in separate microservice to avoid dependency conflicts.

**When to use:**
- PyTorch version conflicts between allin1 and qwen-asr (current: both 2.8.x, but future risk)
- Different resource requirements (Analysis = serialized high-memory, Qwen3 = parallelizable)
- Optional functionality (Qwen3 can fall back to Whisper+LLM if unavailable)

**Trade-offs:**
- **Pros:** Dependency isolation, independent scaling, graceful degradation, independent deployment
- **Cons:** Additional Docker service, HTTP latency (~50-100ms), more complex deployment

**Example:**
```python
# services/analysis/src/sow_analysis/workers/qwen3_client.py
import httpx
from pydantic import BaseModel

class Qwen3AlignRequest(BaseModel):
    audio_url: str
    lyrics_text: str
    language: str = "Chinese"
    device: str = "auto"
    dtype: str = "float32"

class Qwen3AlignResponse(BaseModel):
    lines: list[dict[str, Any]]  # [{"time_seconds": 15.0, "text": "..."}]

class Qwen3Client:
    def __init__(self, base_url: str, timeout: float = 300.0):
        self.client = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout,
        )

    async def align(self, request: Qwen3AlignRequest) -> Qwen3AlignResponse:
        """Call Qwen3 service for forced alignment."""
        response = await self.client.post(
            "/api/v1/align",
            json=request.model_dump(),
        )
        response.raise_for_status()
        return Qwen3AlignResponse(**response.json())
```

### Pattern 2: Hierarchical Fallback for LRC Generation

**What:** Chain multiple alignment strategies with graceful degradation.

**When to use:**
- Multiple alignment approaches with varying accuracy/speed tradeoffs
- External dependencies that may be unavailable or fail
- Gradual rollout of new features

**Trade-offs:**
- **Pros:** High availability, A/B testing capability, progressive enhancement
- **Cons:** Complexity, multiple code paths to maintain

**Example:**
```python
# services/analysis/src/sow_analysis/workers/lrc.py
async def generate_lrc(
    audio_path: Path,
    lyrics_text: str,
    options: LrcOptions,
    output_path: Optional[Path] = None,
    youtube_url: Optional[str] = None,
) -> Path:
    """Generate timestamped LRC file with hierarchical fallback."""
    if youtube_url:
        try:
            # Path 1: YouTube transcript + LLM correction (primary)
            return await youtube_transcript_to_lrc(youtube_url, lyrics_text)
        except YouTubeTranscriptError:
            logger.warning("YouTube transcript failed, trying next path")

    # Path 2: Whisper transcription
    whisper_phrases = await _run_whisper_transcription(
        audio_path, options.whisper_model, options.language
    )

    # Path 3: Qwen3 forced alignment (if enabled and available)
    if options.use_qwen3:
        try:
            aligned_lines = await _run_qwen3_alignment(
                audio_path, lyrics_text, whisper_phrases, options
            )
            return _write_lrc(aligned_lines, output_path)
        except Qwen3UnavailableError:
            logger.warning("Qwen3 service unavailable, falling back to LLM")

    # Path 4: LLM alignment (final fallback)
    lrc_lines = await _llm_align(lyrics_text, whisper_phrases)
    return _write_lrc(lrc_lines, output_path)
```

### Pattern 3: Async Job Queue with Semaphore Control

**What:** Limit concurrent processing of specific job types while maintaining queue ordering.

**When to use:**
- Different resource requirements per job type
- Need to prevent memory/CPU overload with concurrent heavy workloads
- Maintaining job submission ordering

**Trade-offs:**
- **Pros:** Prevents resource exhaustion, fair job scheduling, control over concurrency
- **Cons:** In-memory only (service restart loses queue), no distributed processing

**Example:**
```python
# services/analysis/src/sow_analysis/workers/queue.py
class JobQueue:
    def __init__(
        self,
        max_concurrent_analysis: int = 1,  # allin1 is memory-heavy
        max_concurrent_lrc: int = 2,        # Whisper is lighter
    ):
        self._analysis_lock = asyncio.Lock()  # Serialize analysis
        self._lrc_semaphore = asyncio.Semaphore(max_concurrent_lrc)

    async def _process_job_with_semaphore(self, job: Job) -> None:
        if job.type == JobType.ANALYZE:
            async with self._analysis_lock:
                await self._process_analysis_job(job)
        elif job.type == JobType.LRC:
            async with self._lrc_semaphore:
                await self._process_lrc_job(job)
```

## Data Flow

### Request Flow (LRC Generation with Qwen3)

```
[Client: Admin CLI]
    ↓ HTTP POST /api/v1/jobs/lrc
[Analysis Service: Jobs Route]
    ↓ Enqueue LRC job
[Job Queue]
    ↓ Process job (concurrency: 2)
[LRC Worker]
    ↓ 1. Download audio from R2
    ↓ 2. Try YouTube transcript (if URL provided)
    ↓ 3. Run Whisper transcription (cached if possible)
    ↓ 4. POST /api/v1/align to Qwen3 Service (NEW)
    ↓ HTTP with JSON payload
[Qwen3 Service: Align Route]
    ↓ Validate (< 5min duration)
    ↓ Load Qwen3ForcedAligner (cached in memory)
    ↓ Run alignment (character-level)
    ↓ Map segments → lyric lines
    ↓ 200 OK with aligned lines
    ↑
[LRC Worker]
    ↓ 5. Write LRC file
    ↓ 6. Upload to R2
    ↓ 7. Update job status
    ↓
[Client: GET /api/v1/jobs/{job_id}]
```

### State Management

```
[Job Store: SQLite (async/aiosqlite)]
    ↓ persist job status/recover on restart
    |
    └──→ [In-Memory Job Cache] (active jobs only)

[Cache: /cache directory]
    ├──→ Whisper transcription cache (by content_hash)
    ├──→ LRC result cache (by content_hash + lyrics_hash)
    └──→ Qwen3 model cache (HuggingFace cache)

[Cloudflare R2]
    ├──→ Audio: {hash_prefix}/audio.mp3
    ├──→ Stems: {hash_prefix}/stems/
    └──→ LRC: {hash_prefix}/lyrics.lrc
```

### Key Data Flows

1. **LRC Job Submission:** Client → Analysis Service → Job Queue → Worker → R2
2. **Qwen3 Alignment Call:** Analysis Service → HTTP → Qwen3 Service → HuggingFace Cache
3. **Fallback Chain:** YouTube → Whisper → Qwen3 → LLM (cascade on failure)

## Scaling Considerations

| Scale | Architecture Adjustments |
|-------|--------------------------|
| 0-100 songs/week | Single Docker Compose deployment (current) |
| 100-1000 songs/week | Separate GPU server for Qwen3 service, increase SOW_MAX_CONCURRENT_LRC_JOBS |
| 1000+ songs/week | Redis for job queue (distribute across multiple Analysis Service instances), load balancer for Qwen3 service |

### Scaling Priorities

1. **First bottleneck:** Qwen3 service with single-instance model loading. Add model singleton cache and increase SOW_MAX_CONCURRENT_LRC_JOBS.
2. **Second bottleneck:** In-memory job queue on service restart. Add Redis for persistent queue.

## Anti-Patterns

### Anti-Pattern 1: Adding Qwen3 Directly to Analysis Service

**What people do:** Add qwen-asr to `services/analysis/pyproject.toml` and run Qwen3 in same process.

**Why it's wrong:**
- **PyTorch version conflict risk:** allin1 requires torch<2.9.0, qwen-asr may need different version
- **Resource contention:** allin1 is serialized (max_concurrent=1), Qwen3 can run in parallel
- **Deployment coupling:** Updating Qwen3 model requires re-analyzing all songs if cache invalid

**Do this instead:** Separate Qwen3 service with independent Dockerfile and PyTorch version control.

### Anti-Pattern 2: No Fallback Strategy

**What people do:** Make Qwen3 the only LRC generation path.

**Why it's wrong:**
- **5-minute duration limit:** Longer songs will fail entirely
- **Service downtime:** Qwen3 service failure breaks all LRC generation
- **Cold start issues:** First request waits for model download (~1.2GB)

**Do this instead:** Hierarchical fallback (YouTube → Whisper → Qwen3 → LLM) with logging for each failure path.

### Anti-Pattern 3: Synchronous Qwen3 Calls in LRC Worker

**What people do:** Run Qwen3 alignment synchronously in the LRC worker process.

**Why it's wrong:**
- **Resource contention:** Qwen3 model (~1GB) competes with allin1/Demucs for GPU memory
- **Timeouts:** 5-minute audio alignment may take 60-120s, blocking LRC queue
- **Cache thrashing:** Loading/unloading model between requests wastes time

**Do this instead:** Async HTTP call to Qwen3 service with proper timeout and model caching in service process.

## Integration Points

### External Services

| Service | Integration Pattern | Notes |
|---------|---------------------|-------|
| Cloudflare R2 | boto3 (async-ish via thread pool) | Audio download/upload |
| HuggingFace | qwen-asr library (auto-download) | Model cache in HF_HOME |
| OpenRouter/OpenAI | openai SDK (sync) | LLM alignment fallback |

### Internal Boundaries

| Boundary | Communication | Notes |
|----------|---------------|-------|
| Admin CLI ↔ Analysis Service | HTTP/JSON (async polling) | Submit job, poll status |
| Analysis Service ↔ Qwen3 Service | HTTP/JSON (async httpx) | POST /align with request/response |
| Jobs ↔ Workers | In-memory Queue + Semaphore | Concurrency control |
| Job Queue ↔ SQLite | aiosqlite (async) | Persistence on restart |

## Dependency Compatibility Matrix

| Service | PyTorch | Key Dependencies | Constraint |
|---------|---------|------------------|------------|
| Analysis Service | 2.8.x | allin1, demucs, faster-whisper | allin1 requires torch<2.9.0 |
| Qwen3 Service | 2.0.0+ | qwen-asr, pydub | qwen-asr may require newer torch |

**Recommendation:** Keep services separate to maintain independent PyTorch version control.

## Build Order

1. **Phase 1: Qwen3 Service Foundation** (Independent)
   - Create `services/qwen3_align/` with FastAPI scaffold
   - Implement `POST /api/v1/align` endpoint with Qwen3ForcedAligner
   - Add Dockerfile and pyproject.toml with qwen-asr dependency
   - Test via `docker compose -f docker-compose.qwen3.yml up`

2. **Phase 2: Qwen3 Client in Analysis Service** (Depends on Phase 1)
   - Add `workers/qwen3_client.py` with HTTP client
   - Update `workers/lrc.py` to call Qwen3 service after Whisper
   - Add `use_qwen3: bool` option to `LrcOptions` in models.py
   - Update docker-compose.yml to include qwen3 service

3. **Phase 3: Fallback Integration** (Depends on Phase 2)
   - Implement try/except around Qwen3 calls
   - Fall back to LLM alignment on Qwen3UnavailableError
   - Add logging for each fallback path

4. **Phase 4: Testing and Validation** (Depends on Phase 3)
   - Unit tests for Qwen3 client
   - Integration tests with actual Qwen3 service
   - Compare accuracy: YouTube > Qwen3 > Whisper+LLM

## Sources

- Services architecture: `/home/mhuang/Development/stream_of_worship/services/analysis/` (codebase audit, HIGH confidence)
- LRC pipeline: `/home/mhuang/Development/stream_of_worship/services/analysis/src/sow_analysis/workers/lrc.py` (codebase audit, HIGH confidence)
- Qwen3 POC: `/home/mhuang/Development/stream_of_worship/poc/gen_lrc_qwen3.py` (codebase audit, HIGH confidence)
- Project README: `/home/mhuang/Development/stream_of_worship/README.md` (codebase audit, HIGH confidence)
- Existing constraint: `/home/mhuang/Development/stream_of_worship/specs/lrc_gen_enhancements.md` (line 7, HIGH confidence)
- Qwen3 design: `/home/mhuang/Development/stream_of_worship/specs/improve_timecode_accuracy_with_qwen3_aligner.md` (HIGH confidence)

---
*Architecture research for: Stream of Worship — Qwen3 ForcedAligner Integration*
*Researched: 2026-02-13*
