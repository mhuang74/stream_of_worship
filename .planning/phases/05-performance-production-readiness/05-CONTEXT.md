# Phase 5: Performance & Production Readiness - Context

**Gathered:** 2026-02-14
**Status:** Ready for planning

<domain>
## Phase Boundary

Optimize processing time and ensure production deployment readiness for the Qwen3 forced alignment service. This phase delivers a production-ready service that loads the model once at startup and completes LRC generation within 2x the time of the Whisper+LLM path.

</domain>

<decisions>
## Implementation Decisions

### Model caching and lifecycle
- Load model at startup using FastAPI lifespan events — blocks startup until ready
- Service starts without model if loading fails, health check returns unhealthy
- Limited concurrency: 2-3 concurrent alignment requests
- Double validation: Both Qwen3 service and analysis service enforce 5-minute duration limit

### Production deployment
- Target: Docker Compose (local/self-hosted) — single machine deployment
- Configuration: Environment variables only (pydantic-settings)
- Health check: Deep health — /health returns 200 only if model is loaded and ready
- Logging: Text with timestamps format — human-readable in Docker logs

### Claude's Discretion
- Exact concurrency limit (2 or 3) based on memory profiling
- Memory usage thresholds and monitoring approach
- Retry logic for transient failures
- Graceful shutdown behavior

</decisions>

<specifics>
## Specific Ideas

- Service should be ready for production deployment after this phase
- Focus on single-machine Docker Compose setup, not Kubernetes
- Human-readable logs preferred over structured JSON for this use case

</specifics>

<deferred>
## Deferred Ideas

- Kubernetes deployment with GPU support — future phase if needed
- Horizontal pod autoscaling — out of scope for Docker Compose target
- Structured JSON logging with log aggregation — add to backlog if needed
- Metrics endpoint (Prometheus) — future observability phase

</deferred>

---

*Phase: 05-performance-production-readiness*
*Context gathered: 2026-02-14*
