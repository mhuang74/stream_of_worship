# Roadmap: Stream of Worship

## Milestones

- ✅ **v1.0 Initial** - Shipped pre-2026-02-13
- 🚧 **v1.1 Qwen3 LRC Refinement** - Phases 1-5 (in progress)
- 📋 **v1.2** - Not planned

## Phases

### 🚧 v1.1 Qwen3 LRC Refinement (In Progress)

**Milestone Goal:** Integrate Qwen3-ForcedAligner-0.6B to eliminate early/late lyrics display issues.

#### Phase 1: Qwen3 Service Foundation
**Goal**: Build standalone FastAPI microservice for forced alignment
**Depends on**: Nothing
**Requirements**: QWEN3-01, QWEN3-02, QWEN3-03, QWEN3-04, QWEN3-05, QWEN3-06, QWEN3-07
**Success Criteria** (what must be TRUE):
  1. Qwen3 Alignment Service starts and loads Qwen3-ForcedAligner-0.6B model
  2. POST /api/v1/align endpoint accepts audio file and lyrics text
  3. Service returns character-level timestamps mapped to lyric lines
  4. Service rejects audio files >5 minutes with clear error message
  5. Service runs in isolated Docker environment without PyTorch conflicts
**Plans**: TBD

Plans:
- [ ] 01-01: FastAPI service structure and Dockerfile
- [ ] 01-02: Qwen3 model loading and device detection
- [ ] 01-03: Alignment endpoint with duration validation

#### Phase 2: Analysis Service Integration
**Goal**: Connect Qwen3 service to existing LRC pipeline
**Depends on**: Phase 1
**Requirements**: INTG-01, INTG-02, INTG-03, INTG-04, INTG-05, INTG-06, INTG-07
**Success Criteria** (what must be TRUE):
  1. Analysis Service has Qwen3Client HTTP client for alignment calls
  2. LRC pipeline with Qwen3 enabled produces accurate LRC files from Whisper path
  3. LRC pipeline with YouTube source produces accurate LRC files (skip Qwen3)
  4. LrcOptions has use_qwen3 flag accessible in admin CLI
  5. Qwen3 service is included in docker-compose.yml with proper networking
**Plans**: TBD

Plans:
- [ ] 02-01: Qwen3Client HTTP client implementation
- [ ] 02-02: LRC worker integration with dual-path logic
- [ ] 02-03: Docker compose configuration

#### Phase 3: Fallback & Reliability
**Goal**: Implement graceful degradation when Qwen3 fails
**Depends on**: Phase 2
**Requirements**: FALLBK-01, FALLBK-02, FALLBK-03, FALLBK-04, FALLBK-05, TEST-04
**Success Criteria** (what must be TRUE):
  1. LRC generation completes successfully when Qwen3 service is unavailable
  2. Songs exceeding 5 minutes skip Qwen3 and use LLM-aligned LRC
  3. Qwen3 failures are logged as WARNING without breaking LRC pipeline
  4. Successful Qwen3 refinement is logged at INFO level
  5. Mock Qwen3 service tests verify fallback to LLM-aligned LRC
**Plans**: TBD

Plans:
- [ ] 03-01: Error handling and fallback logic in LRC worker
- [ ] 03-02: Duration validation with skip logic
- [ ] 03-03: Logging strategy for success/skip/failure cases

#### Phase 4: Testing & Validation
**Goal**: Verify through testing that Qwen3 improves timestamp accuracy
**Depends on**: Phase 3
**Requirements**: TEST-01, TEST-02, TEST-03
**Success Criteria** (what must be TRUE):
  1. map_segments_to_lines() passes unit tests for repeated chorus scenarios
  2. Regression tests show Qwen3 output maintains or improves timing vs Whisper-only
  3. Integration test validates full LRC pipeline with Qwen3 enabled from end to end
**Plans**: TBD

Plans:
- [ ] 04-01: Unit tests for map_segments_to_lines() edge cases
- [ ] 04-02: Regression tests with golden LRC comparison
- [ ] 04-03: End-to-end integration test with real audio/lyrics

#### Phase 5: Performance & Production Readiness
**Goal**: Optimize processing time and ensure production deployment readiness
**Depends on**: Phase 4
**Requirements**: PERF-01, PERF-02
**Success Criteria** (what must be TRUE):
  1. Qwen3 service loads model once at startup (not per-request)
  2. LRC generation with Qwen3 completes within 2x time of Whisper+LLM path
**Plans**: TBD

Plans:
- [ ] 05-01: Model singleton cache implementation
- [ ] 05-02: Performance benchmarking and validation
- [ ] 05-03: Production configuration documentation

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2 → 3 → 4 → 5

| Phase | Milestone | Plans Complete | Status | Completed |
|-------|-----------|----------------|--------|-----------|
| 1. Qwen3 Service Foundation | v1.1 | 0/3 | Not started | - |
| 2. Analysis Service Integration | v1.1 | 0/3 | Not started | - |
| 3. Fallback & Reliability | v1.1 | 0/3 | Not started | - |
| 4. Testing & Validation | v1.1 | 0/3 | Not started | - |
| 5. Performance & Production Readiness | v1.1 | 0/3 | Not started | - |
