# Phase 1: Qwen3 Service Foundation - Context

**Gathered:** 2026-02-13
**Status:** Ready for planning

<domain>
## Phase Boundary

Build a standalone FastAPI microservice that loads Qwen3-ForcedAligner-0.6B and exposes a forced alignment API. The service takes an audio URL and lyrics text, returns timestamp-aligned LRC data. Service runs in isolated Docker environment.

</domain>

<decisions>
## Implementation Decisions

### API Contract
- **Audio input**: URL reference (R2/S3 URL) — aligns with existing storage pattern
- **Response format**: Both LRC and JSON (configurable via request flag)
- **Timestamp granularity**: Line-level only (not word/character-level karaoke style)
- **Parameters**: Language hint only (e.g., "zh" for Chinese)

### Model Loading Strategy
- **Load timing**: At service startup (not lazy-load)
- **Caching**: Stay loaded as singleton (don't reload)
- **Health check**: `/health` returns 200 only when model is loaded and ready
- **Model source**: Mount from host volume (pre-downloaded, not runtime download)

### Error Handling
- **Audio >5 minutes**: HTTP 400 Bad Request
- **Error format**: Simple string message (plain text)
- **Model inference failure**: HTTP 500 Internal Server Error
- **Logging**: Error level with full traceback for debugging

### Resource Management
- **Device selection**: Environment variable `SOW_QWEN3_DEVICE` (auto-detect CUDA → MPS → CPU if not set)
- **Concurrency**: Limited by semaphore (configurable, not unlimited)
- **Docker memory**: Explicit 8GB limit
- **Docker CPU**: Limit to 4 cores

### Claude's Discretion
- Exact FastAPI route structure (use standard patterns)
- Pydantic model naming conventions
- Internal logging format beyond error tracebacks
- Dockerfile base image selection (use Python 3.11 slim)

</decisions>

<specifics>
## Specific Ideas

- Service should follow the same patterns as existing Analysis Service (`services/analysis/`)
- API endpoint should be `POST /api/v1/align` for consistency
- Model path volume mount: `/models/qwen3-forced-aligner` (conventional location)
- Configuration via environment variables (existing project pattern)

</specifics>

<deferred>
## Deferred Ideas

- Word/character-level timestamp output (karaoke style) — out of scope for v1.1, revisit in v2
- Batch alignment endpoint (multiple songs) — future enhancement
- Runtime model download — not needed, using volume mount approach
- Auto-scaling based on queue depth — production optimization for later

</deferred>

---

*Phase: 01-qwen3-service-foundation*
*Context gathered: 2026-02-13*
