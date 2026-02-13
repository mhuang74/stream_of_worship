# Requirements: Stream of Worship v1.1

**Defined:** 2026-02-13
**Core Value:** Users can seamlessly create worship songsets with accurate lyrics videos that display at exactly the right time — no more early or late lyrics.

## v1.1 Requirements

Requirements for Qwen3 ForcedAligner integration milestone.

### Qwen3 Service

- [ ] **QWEN3-01**: Qwen3 Alignment Service runs as standalone FastAPI microservice
- [ ] **QWEN3-02**: Service exposes POST /api/v1/align endpoint accepting audio file + lyrics text
- [ ] **QWEN3-03**: Service loads Qwen3-ForcedAligner-0.6B model (~1.2GB) with device auto-detection (CUDA/MPS/CPU)
- [ ] **QWEN3-04**: Service validates audio duration and rejects files >5 minutes with clear error
- [ ] **QWEN3-05**: Service returns character-level timestamps mapped to original lyric lines
- [ ] **QWEN3-06**: Service has independent Dockerfile and pyproject.toml for dependency isolation
- [ ] **QWEN3-07**: Service uses transformers backend (not vLLM) to avoid PyTorch version conflicts

### Analysis Service Integration

- [ ] **INTG-01**: Analysis Service has Qwen3Client HTTP client for calling Qwen3 service
- [ ] **INTG-02**: LRC worker uses hierarchical fallback: YouTube → Whisper → Qwen3 → LLM
- [ ] **INTG-03**: LrcOptions dataclass has use_qwen3 flag (default: true when available)
- [ ] **INTG-04**: docker-compose.yml includes qwen3_align service with proper networking
- [ ] **INTG-05**: LRC worker logs each fallback path with clear WARNING messages

### Fallback & Reliability

- [ ] **FALLBK-01**: When Qwen3 fails (service down, 5-min limit, error), LRC falls back to LLM alignment
- [ ] **FALLBK-02**: Duration validation happens before calling Qwen3 (skip if >5min, log reason)
- [ ] **FALLBK-03**: All Qwen3 errors are caught and logged without breaking the LRC pipeline
- [ ] **FALLBK-04**: Songs exceeding 5 minutes use Whisper+LLM path (no degradation in output quality)

### Testing & Validation

- [ ] **TEST-01**: Unit tests for map_segments_to_lines() with repeated chorus scenarios
- [ ] **TEST-02**: Regression tests comparing Qwen3 output vs Whisper+LLM baseline
- [ ] **TEST-03**: Integration test for full LRC pipeline with Qwen3 enabled
- [ ] **TEST-04**: Mock Qwen3 service for testing fallback behavior

### Performance (v1.1 MVP)

- [ ] **PERF-01**: Qwen3 service loads model once at startup (not per-request)
- [ ] **PERF-02**: LRC generation with Qwen3 completes within 2x time of Whisper+LLM path

## v2 Requirements (Deferred)

### Qwen3 Service Enhancements

- **QWEN3-V2-01**: 5-minute audio chunking for long songs (align in segments, merge LRC)
- **QWEN3-V2-02**: FlashAttention 2 support for 2-3x GPU speedup
- **QWEN3-V2-03**: ModelScope CDN fallback for model downloads
- **QWEN3-V2-04**: Batch alignment endpoint for multiple songs

### Performance Optimizations

- **PERF-V2-01**: GPU memory cleanup between requests
- **PERF-V2-02**: Alignment result caching (skip re-alignment for same audio+lyrics)
- **PERF-V2-03**: Concurrent Qwen3 job limits via semaphore

## Out of Scope

| Feature | Reason |
|---------|--------|
| Full Qwen3-ASR integration (transcription + alignment) | Out of scope — we need forced alignment only, Whisper handles transcription |
| Real-time alignment for live karaoke | Out of scope — batch processing for pre-produced videos |
| Multi-language bilingual lyrics support | Out of scope — Chinese worship songs are primarily single-language |
| Singing voice adaptation | Out of scope — Qwen3 is speech-only; accuracy acceptable for worship music |

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| QWEN3-01 | Phase 1 | Pending |
| QWEN3-02 | Phase 1 | Pending |
| QWEN3-03 | Phase 1 | Pending |
| QWEN3-04 | Phase 1 | Pending |
| QWEN3-05 | Phase 1 | Pending |
| QWEN3-06 | Phase 1 | Pending |
| QWEN3-07 | Phase 1 | Pending |
| INTG-01 | Phase 2 | Pending |
| INTG-02 | Phase 2 | Pending |
| INTG-03 | Phase 2 | Pending |
| INTG-04 | Phase 2 | Pending |
| INTG-05 | Phase 2 | Pending |
| FALLBK-01 | Phase 3 | Pending |
| FALLBK-02 | Phase 3 | Pending |
| FALLBK-03 | Phase 3 | Pending |
| FALLBK-04 | Phase 3 | Pending |
| TEST-01 | Phase 3 | Pending |
| TEST-02 | Phase 3 | Pending |
| TEST-03 | Phase 3 | Pending |
| TEST-04 | Phase 3 | Pending |
| PERF-01 | Phase 4 | Pending |
| PERF-02 | Phase 4 | Pending |

**Coverage:**
- v1.1 requirements: 21 total
- Mapped to phases: 21
- Unmapped: 0 ✓

---
*Requirements defined: 2026-02-13*
*Last updated: 2026-02-13 after research synthesis*
