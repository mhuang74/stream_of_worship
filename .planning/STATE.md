# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-02-13)

**Core value:** Users can seamlessly create worship songsets with accurate lyrics videos that display at exactly the right time — no more early or late lyrics.

**Current focus:** Phase 4: Testing and Validation

## Current Position

Phase: 5 of 5 complete — Performance and Production Readiness complete
Plan: 3 of 3 — Phase 5 Plan 3: Production Configuration Documentation complete
Status: Phase 4 complete, Phase 5 complete
Last activity: 2026-02-14 — Completed Phase 5 Plan 3: Production Configuration Documentation

Progress: [████████░░] 100% Phase 2 | [████████░░] 100% Phase 3 | [████████░░] 100% Phase 4 | [████████░░] 100% Phase 5

## Performance Metrics

**Velocity:**
- Total plans completed: 18
- Average duration: 5.2 min
- Total execution time: 1.6 hours

**By Phase:**

| Phase          | Plans Complete | Total | Avg/Plan | Status |
|----------------|----------------|-------|----------|--------|
| Qwen3 Service Foundation | 4              | 4      | 7.3 min   | Complete |
| Analysis Service Integration | 3              | 3      | 5.2 min   | Complete |
| Fallback & Reliability | 3              | 3      | 2.7 min   | Complete |
| Testing and Validation | 3              | 3      | 4.4 min   | Complete |
| Performance and Production Readiness | 3              | 3      | 3.5 min   | Complete |

*Updated after each plan completion*
| Phase 02-analysis-service-integration P01 | 5min | 2 tasks | 3 files |
| Phase 02-analysis-service-integration P03 | 3min | 1 task | 1 file |
| Phase 02-analysis-service-integration P02 | 8min | 2 tasks | 3 files |
| Phase 03-fallback-reliability P01 | 2min | 2 tasks | 1 files |
| Phase 03-fallback-reliability P02 | 2min | 2 tasks | 2 files |
| Phase 03-fallback-reliability P03 | 4min | 6 tasks | 1 files |
| Phase 04-testing-validation P01 | 5min | 2 tasks | 2 files |
| Phase 04-testing-validation P02 | 5min | 2 tasks | 1 file |
| Phase 04-testing-validation P03 | 3min | 2 tasks | 3 files |
| Phase 05-performance-production-readiness P01 | 1min | 2 tasks | 3 files |
| Phase 05-performance-production-readiness P02 | 6min | 2 tasks | 2 files |
| Phase 05-performance-production-readiness P03 | 8min | 3 tasks | 3 files |

## Accumulated Context

### Decisions

Decisions are logged in PROJECT.md Key Decisions table.
Recent decisions affecting current work:
- Phase 1: Use separate Docker service for Qwen3 to isolate PyTorch dependencies
- Phase 2: Use hierarchical fallback: YouTube → Whisper → Qwen3 → LLM
- Phase 2 Plan 1: Use exact field names from Qwen3 API (format not output_format, lrc_content not response.text)
- Phase 2 Plan 1: Default use_qwen3=True to enable Qwen3 refinement when available
- Phase 2 Plan 3: Use port 8001 for qwen3 service external access to avoid conflict with analysis:8000
- Phase 2 Plan 3: Pass R2 credentials from common environment variables to both services
- Phase 2: Added --no-qwen3 flag to admin CLI for optional Qwen3 bypass
- qwen-asr version: Fixed to >=0.0.6 (latest available on PyPI)
- Share aligner getter from health route instead of duplicating
- Model path: /models/qwen3-forced-aligner (volume mount)
- Phase 3 Plan 1: Qwen3RefinementError exception to distinguish non-fatal failures
- Phase 3 Plan 1: Multi-catch error handling for ConnectionError, TimeoutError, and generic Exception
- Phase 3 Plan 2: Duration check at 300 seconds (5 min) matches Qwen3 service limit in align.py
- Phase 3 Plan 2: Skip Qwen3 entirely for long audio (not just catch error) to avoid wasted bandwidth/time
- Phase 3 Plan 2: Duration calculated from Whisper phrases (max end time) - no need to re-analyze audio
- Phase 3 Plan 3: Mock-based testing for Qwen3 fallback scenarios instead of integration tests
- Phase 3 Plan 3: Capture mock objects from patch() context for proper assertion verification
- Phase 4 Plan 1: Use pytest class-based organization for logical test grouping
- Phase 4 Plan 1: normalize_text() removes whitespace/punctuation only, not Traditional/Simplified conversion
- Phase 4 Plan 2: Use golden file comparison strategy for regression testing baseline
- Phase 4 Plan 2: Mock Whisper transcription with realistic timing instead of actual transcription
- Phase 4 Plan 2: Generate dummy audio file only for testing (transcription is mocked)
- Phase 4 Plan 3: Use .wav format for test audio fixtures (mp3 in .gitignore)
- Phase 4 Plan 3: Section headers in lyrics excluded from verification (not actual sung lines)
- Phase 5 Plan 2: Use synthetic delays for benchmark testing (fast, deterministic, no real transcription)
- Phase 5 Plan 2: Benchmark test validates 2x time requirement: Qwen3 <= 2x Whisper+LLM baseline
- Phase 5 Plan 3: Docker Compose standalone deployment with healthcheck, resource limits, logging rotation
- Phase 5 Plan 3: Health check with 180s start_period to accommodate model loading time
- Phase 5 Plan 3: Resource limits: 4 CPUs / 8GB memory for production stability
- Phase 5 Plan 3: MAX_CONCURRENT=2 default for production (balance throughput and memory)

### Phase 1 Deliverables

- FastAPI microservice foundation with pydantic-settings configuration
- Qwen3ForcedAligner wrapper with async initialization and concurrency control
- Health check endpoint (/health) for model readiness monitoring
- POST /api/v1/align endpoint with audio download, duration validation, LRC/JSON output
- Docker configuration with 8GB memory limit, 4 CPU cores, model volume mount
- Complete service documentation with API reference

### Phase 2 Deliverables

- Qwen3Client HTTP client with typed response models (services/analysis)
- use_qwen3 flag added to LrcOptions model (default True)
- use_qwen3 parameter added to admin CLI AnalysisClient.submit_lrc()
- --no-qwen3 flag added to 'sow-admin audio lrc' command
- qwen3 and qwen3-dev services added to docker-compose.yml
- Services co-deploy with shared Docker networking on qwen3:8000
- External port 8001 used for qwen3 to avoid conflict with analysis:8000
- qwen3-cache volume defined for persistent caching
- R2 credentials passed from common environment to qwen3 service
- SOW_QWEN3_BASE_URL and SOW_QWEN3_API_KEY added to settings
- Qwen3 refinement integrated into LRC worker Whisper path
- R2 URL construction in s3://{bucket}/audio/{hash}.mp3 format
- YouTube path bypasses Qwen3 (accurate from transcript)

### Phase 3 Deliverables

- Qwen3RefinementError exception class (non-fatal, falls back to LLM)
- Multi-catch error handling: ConnectionError (network), asyncio.TimeoutError (timeout), Exception (generic)
- All Qwen3 failures fall back gracefully to LLM-aligned LRC without pipeline interruption
- Empty LRC content from Qwen3 logs WARNING and falls back
- Successful refinement logs INFO with line count
- max_qwen3_duration option (default 300s) added to LrcOptions
- Duration validation before Qwen3 HTTP request - skips for audio > 300 seconds
- _get_audio_duration() helper calculates duration from Whisper phrases
- Songs exceeding 5 minutes skip Qwen3 and use LLM-aligned LRC with WARNING log
- Comprehensive mock tests for Qwen3 fallback behavior (test_qwen3_fallback.py)
- Tests for ConnectionError, TimeoutError, and Qwen3ClientError fallback scenarios
- Test for duration-based Qwen3 skip for long audio
- Test for successful Qwen3 refinement with precise timestamps

### Phase 4 Deliverables

- pytest configuration in qwen3 service (pyproject.toml)
- Comprehensive unit tests for map_segments_to_lines() (34 test cases)
- Coverage for: repeated choruses, empty lines, not found cases, Chinese worship lyrics
- normalize_text() independently tested
- Regression test framework comparing Qwen3 vs Whisper+LLM baseline (test_qwen3_regression.py)
- parse_lrc_file() helper for LRC parsing into (time, text) tuples
- Test fixtures: sample_lyrics.txt (worship song with repeated chorus)
- Golden baseline LRC fixture (golden_llm_lrc.txt) for reproducible testing
- Mock Whisper phrases with realistic timing matching lyrics structure
- Test cases: baseline generation, Qwen3 vs baseline comparison, precision improvement
- Verification: Qwen3 has >= baseline lines, plausible timestamps, maintains all text
- End-to-end integration test for full LRC pipeline with Qwen3 (test_lrc_integration_qwen3.py)
- Integration test fixtures: integration_test_lyrics.txt (complete worship song structure)
- E2E validation: Whisper -> LLM -> Qwen3 -> LRC file with repeated sections handling

### Phase 5 Deliverables

- Model loads once at startup via FastAPI lifespan event (singleton pattern)
- Graceful failure handling for model initialization (service starts even if load fails)
- Health check returns 503 when model not ready
- Production concurrency limit set to MAX_CONCURRENT=2 (configurable via SOW_QWEN3_MAX_CONCURRENT)
- Performance benchmark test validating 2x time requirement for Qwen3 vs Whisper+LLM
- Benchmark test fixtures with medium-length worship song lyrics (benchmark_lyrics.txt)
- Production Docker Compose configuration (docker/docker-compose.prod.yml) with healthcheck, resource limits, and logging rotation
- Comprehensive environment variable documentation (services/qwen3/.env.example) with 13 SOW_QWEN3_ variables
- Complete 526-line production deployment guide (docs/qwen3-production.md) covering all operational aspects

### Pending Todos

None yet.

### Blockers/Concerns

None yet.

## Session Continuity

Last session: 2026-02-14
Stopped at: Completed Phase 5 Plan 3 (Production Configuration Documentation)
Resume file: None
