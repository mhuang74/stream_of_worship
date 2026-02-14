# Project Research Summary

**Project:** Stream of Worship — Qwen3 ForcedAligner Integration for LRC Timestamp Refinement
**Domain:** Audio Analysis Services — Forced alignment for lyric synchronization
**Researched:** 2026-02-13
**Confidence:** HIGH

## Executive Summary

This is an audio analysis enhancement project — adding Qwen3-ForcedAligner to an existing LRC generation pipeline to improve timestamp accuracy for Chinese worship lyrics videos. The current pipeline (YouTube transcript → Whisper fallback) produces approximate timing that causes lyrics to display early or late. Experts build this by integrating forced alignment models via microservice API with hierarchical fallback strategies, isolating heavy ML dependencies to avoid conflicts with existing services.

The recommended approach is to deploy Qwen3-ForcedAligner as a separate FastAPI microservice with its own Dockerfile and dependency management. This isolation prevents PyTorch version conflicts (torch<2.9.0 constraint needed for pyannote.audio) while allowing independent scaling. The integration should use a hierarchical fallback chain: YouTube transcript (primary) → Whisper transcription → Qwen3 forced alignment (when enabled and audio <5min) → LLM alignment (final fallback). This ensures availability even if Qwen3 fails or is unavailable.

The primary risks are: (1) PyTorch version conflicts if dependencies are not properly isolated, (2) silent degradation when audio exceeds the 5-minute model limit, (3) character-to-line mapping brittleness for repeated verses/choruses, and (4) model loading overhead causing performance bottlenecks. These are mitigated by separate microservices, early duration validation with clear logging, robust map_segments_to_lines() testing with regression tests, and model singleton caching for service use.

## Key Findings

### Recommended Stack

**Core technologies:**
- **Python 3.11** — Runtime language; project constraint, compatible with qwen-asr (>=3.9)
- **PyTorch 2.8.x** — Deep learning framework; torch<2.9.0 constraint required for torchaudio AudioMetaData (pyannote.audio dependency)
- **qwen-asr (latest, pinnable)** — Qwen3 audio models package; provides Qwen3ForcedAligner with transformers backend
- **transformers==4.57.6** — Hugging Face models; exact version bundled with qwen-asr

**Critical constraint:**
- **Do NOT use vLLM 0.14.0** — Requires torch==2.9.1, which conflicts with torch<2.9.0 needed for existing pyannote.audio stack. Use transformers backend instead.

**Device options:**
- **CUDA 12.6+** (recommended for production) — supports bfloat16, flash-attn for 2-3x speedup
- **MPS** (Apple Silicon) — basic support, no flash-attn
- **CPU** — supported but 10-100x slower, use only for testing

**Model specs:**
- Qwen/Qwen3-ForcedAligner-0.6B (~1.2GB), 5-minute max duration, 11 languages, non-autoregressive inference

### Expected Features

**Must have (table stakes):**
- Precise timestamp extraction — core forced alignment function
- Support for known text input — forced alignment requires pre-existing lyrics
- Character/word-level granularity — line-level timing insufficient for karaoke
- Multi-segment handling — worship songs have repeated verses/choruses
- Language detection/hinting — Chinese lyrics need explicit language specification
- Error handling/fallback — degrade gracefully when Qwen3 fails
- Device flexibility — auto-detect MPS/CUDA/CPU based on environment
- Duration validation — reject or skip songs >5 minutes

**Should have (competitive differentiators):**
- Superior accuracy (2-40x lower alignment errors vs competitors) — Qwen3 benchmarks show 27.8ms avg error
- Multi-language support (11 languages) — Chinese, English, Cantonese, French, German, Italian, Japanese, Korean, Portuguese, Russian, Spanish
- Fast NAR inference — non-autoregressive for predictable performance
- FlashAttention 2 support — 2-3x speedup on compatible GPUs
- Model caching — keep loaded model in memory between requests
- 5-minute chunking — split long songs for aligned LRC (future enhancement)

**Defer (v2+):**
- Full Qwen3-ASR integration — combine transcription + forced alignment in one model
- Multi-language lyrics — align bilingual worship songs
- ModelScope fallback — China-friendly CDN for model downloads
- Real-time alignment — pre-process offline, load pre-aligned LRC files

### Architecture Approach

**Recommended architecture:** Separate microservice for Qwen3 ForcedAligner to isolate dependencies from existing Analysis Service. Communication via HTTP/JSON between services. Hierarchical fallback chain ensures availability.

**Major components:**
1. **Analysis Service (modified)** — Job orchestration, R2 storage, LRC generation coordination. Modified lrc.py worker to call Qwen3 service via HTTP client.
2. **Qwen3 Alignment Service (NEW)** — FastAPI microservice with Qwen3ForcedAligner model. Handles POST /api/v1/align requests. Independent Dockerfile and pyproject.toml.
3. **Job Queue (existing)** — Async workers withSemaphore control for concurrency limits
4. **Storage Layer (existing)** — Cloudflare R2 for audio stems and LRC files

**Key patterns:**
- Separate Service for Isolated Dependencies — prevents PyTorch version conflicts
- Hierarchical Fallback — YouTube → Whisper → Qwen3 → LLM cascade on failure
- Async Job Queue with Semaphore Control — limits concurrent processing to prevent resource exhaustion

### Critical Pitfalls

1. **Silent PyTorch version conflicts** — qwen-asr or dependencies may conflict with torch<2.9.0 constraint. Avoid by pinning dependency versions in separate extra group, testing in clean environment.

2. **Timestamp regression worse than Whisper alone** — map_segments_to_lines() may produce drifting timing or wrong line assignments. Avoid by testing on repeated-section-heavy songs, adding regression tests with golden LRC outputs.

3. **5-minute audio limit causing silent pipeline degradation** — songs >5min fail or skip Qwen3 without clear logging. Avoid by early duration validation, WARNING-level logging when skipping, tracking metrics on what % of songs hit limit.

4. **Model loading overhead causing 10x slowdown** — per-request model loading wastes 2-5s per song. Avoid by implementing model singleton cache, lazy loading, warm-start for service use.

5. **No fallback strategy** — making Qwen3 the only LRC path breaks everything on failure. Avoid by keeping hierarchical fallback chain with clear logging for each failure path.

## Implications for Roadmap

Based on research, suggested phase structure:

### Phase 1: Qwen3 Service Foundation (Standalone)
**Rationale:** Build independent service first to validate model integration before modifying existing pipeline. Isolates dependency conflicts and allows manual testing.
**Delivers:** Working FastAPI service with POST /api/v1/align endpoint, Dockerfile/pyproject.toml, model loading with device auto-detection.
**Addresses:** Features from STACK.md (qwen-asr, torch 2.8.x, device flexibility)
**Avoids:** Pitfall #1 (PyTorch conflicts by isolating in separate service)
**Research needed:** None — well-documented qwen-asr package

### Phase 2: Analysis Service Integration
**Rationale:** Once Qwen3 service is verified, integrate it into existing LRC pipeline via HTTP client. Minimal changes to existing code.
**Delivers:** Qwen3Client HTTP client in analysis service, modified lrc.py worker, use_qwen3 option in LrcOptions, updated docker-compose.yml with qwen3 service.
**Uses:** Architecture pattern (separate service communication)
**Implements:** Table stakes features (timestamp extraction, known text input, character-level mapping)
**Avoids:** Pitfall #3 (5-minute limit) by adding duration validation early, Pitfall #4 (model loading overhead) by implementing in service layer
**Research needed:** None — standard HTTP client patterns

### Phase 3: Fallback Integration & Testing
**Rationale:** Implement hierarchical fallback chain and validate that Qwen3 actually improves accuracy. Ensure graceful degradation.
**Delivers:** Try/except around Qwen3 calls with fallback to LLM, clear logging for each fallback path, unit tests for map_segments_to_lines, regression tests with golden LRC outputs.
**Addresses:** Features from FEATURES.md (error handling, fallback, repeated section handling)
**Avoids:** Pitfall #2 (timestamp regression) by comparing vs baseline
**Research needed:** Medium — need research on testing strategy for alignment accuracy metrics

### Phase 4: Performance Optimization
**Rationale:** After functional verification, optimize for production deployment. Focus on model caching and concurrent processing.
**Delivers:** Model singleton cache in Qwen3 service, GPU memory cleanup benchmarks, semaphore limits on concurrent jobs, alignment result caching.
**Addresses:** Differentiator features (model caching, FlashAttention 2 if GPU available)
**Avoids:** Pitfall #4 (model loading overhead)
**Research needed:** Low — standard caching patterns for ML models

### Phase 5: Production Validation
**Rationale:** Full end-to-end validation with real-world usage. Monitor quality and performance.
**Delivers:** Metrics logging (Qwen3 speed vs Whisper+LLM, accuracy improvements), integration tests with actual worship songs, documentation on device selection and troubleshooting.
**Implements:** Remaining table stakes (language hinting, dtype configuration)
**Research needed:** None — standard validation practices

### Phase Ordering Rationale

- **Phase 1 → Phase 2:** Sequential dependency — need working Qwen3 service before integrating. Separates concerns and enables parallel testing.
- **Phase 2 → Phase 3:** Feature-complete before validation — integration must be complete before testing fallback behavior.
- **Phase 3 → Phase 4:** Validate before optimize — ensure correctness before investing in performance work.
- **Phase 4 → Phase 5:** Production-ready before deployment — monitoring and documentation required for production use.

**Grouping based on architecture:**
- Phases 1-2: Core service implementation (siloed work, clear boundaries)
- Phase 3: Integration and correctness (cross-boundary testing)
- Phases 4-5: Production hardening (performance, observability)

**Pitfall avoidance by phase:**
- Phase 1 addresses PyTorch conflicts via service isolation
- Phase 2 addresses 5-minute limit via early validation
- Phase 3 addresses timestamp regression via regression testing
- Phase 4 addresses model loading overhead via caching

### Research Flags

Phases likely needing deeper research during planning:
- **Phase 3:** Complex testing strategy for alignment accuracy metrics — need to define quantitative measures for "better" timing, may need research on standard evaluation methods for forced alignment quality.

Phases with standard patterns (skip research-phase):
- **Phase 1:** Well-documented qwen-asr package, standard FastAPI service patterns
- **Phase 2:** Standard HTTP client patterns, straightforward integration into existing async pipeline
- **Phase 4:** Standard ML model caching patterns, documented approaches for GPU memory management
- **Phase 5:** Standard monitoring and validation practices

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | Based on official Hugging Face docs, PyPI metadata, and PyTorch compatibility matrix. vLLM conflict verified via package metadata. |
| Features | HIGH | Based on official Qwen3-ForcedAligner documentation, existing POC code analysis, and worship music domain understanding. |
| Architecture | HIGH | Based on audit of existing codebase, standard microservice patterns, and dependency isolation requirements. |
| Pitfalls | MEDIUM | Based on code analysis of POC and existing pipeline, PyTorch ecosystem patterns. Web searches for qwen-asr docs returned limited sources. |

**Overall confidence:** HIGH

All critical decisions (separate service, torch version constraints, 5-minute limit, fallback strategy) are supported by authoritative sources or code analysis. Some implementation details (alignment accuracy metrics, chunking strategy) require validation during development.

### Gaps to Address

- **Alignment accuracy metrics:** Need to define quantitative measures for timestamp quality comparison (e.g., mean absolute error vs reference). Handle during Phase 3 planning.
- **5-minute chunking strategy:** Not researched in detail (how to handle alignment continuity across chunks). Defer to v1.2 per FEATURES.md MVP definition.
- **FlashAttention 2 availability on production hardware:** Requires verification that production GPUs support it. Handle during Phase 4.
- **ModelScope CDN reliability:** Mentioned in docs but not validated. Not critical for v1.1, defer to v2+.

## Sources

### Primary (HIGH confidence)
- **Hugging Face:** [Qwen/Qwen3-ForcedAligner-0.6B](https://huggingface.co/Qwen/Qwen3-ForcedAligner-0.6B) — Model documentation, features, constraints
- **GitHub:** [QwenLM/Qwen3-ASR](https://github.com/QwenLM/Qwen3-ASR) — qwen-asr package usage examples
- **PyPI:** [qwen-asr](https://pypi.org/pypi/qwen-asr/json) — Dependency versions, transformers==4.57.6 constraint
- **PyPI:** [vllm 0.14.0](https://pypi.org/pypi/vllm/json) — torch==2.9.1 requirement (conflict)
- **Codebase audit:** `/home/mhuang/Development/stream_of_worship/` — Existing architecture, LRC pipeline, POC implementation
- **PyTorch docs:** [CUDA compatibility](https://pytorch.org/get-started/locally/) — CUDA 12.6+ recommendations

### Secondary (MEDIUM confidence)
- **PyPI:** [flash-attn](https://pypi.org/pypi/flash-attn/json) — CUDA 12.0+ requirement, PyTorch 2.2+ support
- **Docker Hub:** [qwenllm/qwen3-asr](https://hub.docker.com/r/qwenllm/qwen3-asr/tags) — Pre-built images available
- **Codebase:** `poc/gen_lrc_qwen3.py` — POC implementation revealing edge cases
- **Codebase:** `services/analysis/src/sow_analysis/workers/lrc.py` — Existing LRC pipeline
- **Spec:** `specs/improve_timecode_accuracy_with_qwen3_aligner.md` — Implementation requirements

### Tertiary (LOW confidence)
- **Web searches** for `qwen-asr` and Qwen3 ForcedAligner docs — Returned limited authoritative sources, relied on package documentation and code analysis
- **ModelScope CDN** — Mentioned in qwen-asr docs but not directly verified
- **Alignment accuracy benchmarks** — Cited from Qwen3 docs but not independently verified

---
*Research completed: 2026-02-13*
*Ready for roadmap: yes*
