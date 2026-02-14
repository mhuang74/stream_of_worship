---
phase: 02-analysis-service-integration
verified: 2026-02-13T00:00:00Z
status: gaps_found
score: 4/5 must-haves verified
gaps:
  - truth: "LrcOptions has use_qwen3 flag accessible in admin CLI"
    status: failed
    reason: "use_qwen3 flag exists in LrcOptions model on backend, but admin CLI's submit_lrc() method does NOT include use_qwen3 parameter or send it in payload. The flag is only accessible via direct API calls with custom payload."
    artifacts:
      - path: "src/stream_of_worship/admin/services/analysis.py"
        issue: "submit_lrc() method signature and payload missing use_qwen3 parameter"
    missing:
      - "Add use_qwen3 parameter to submit_lrc() method signature in admin/services/analysis.py"
      - "Include use_qwen3 in the options payload sent to /api/v1/jobs/lrc endpoint"
human_verification:
  - test: "End-to-end LRC generation with Qwen3 enabled"
    expected: "Analysis Service calls Qwen3 align endpoint with R2 audio URL and returns refined LRC with accurate timestamps"
    why_human: "Cannot verify Qwen3 service is running or produces accurate timestamps without running actual audio files through the pipeline"
  - test: "End-to-end LRC generation with YouTube source"
    expected: "LRC generated from YouTube transcript without calling Qwen3 service (youtube_url path should skip Qwen3)"
    why_human: "Cannot verify YouTube transcript path bypasses Qwen3 without running actual YouTube URL through the pipeline"
---

# Phase 2: Analysis Service Integration Verification Report

**Phase Goal:** Connect Qwen3 service to existing LRC pipeline
**Verified:** 2026-02-13
**Status:** gaps_found
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths

| #   | Truth   | Status     | Evidence |
| --- | ------- | ---------- | -------- |
| 1   | Analysis Service has Qwen3Client HTTP client for alignment calls | VERIFIED | qwen3_client.py exists with Qwen3Client class, async align() method calling http://qwen3:8000/api/v1/align |
| 2   | LRC pipeline with Qwen3 enabled produces accurate LRC files from Whisper path | VERIFIED (impl) | _qwen3_refine() function implemented in lrc.py with R2 URL construction s3://{bucket}/audio/{hash}.mp3 |
| 3   | LRC pipeline with YouTube source produces accurate LRC files (skip Qwen3) | VERIFIED (impl) | generate_lrc() returns early at line 651 when youtube_url provided, bypassing Whisper path and Qwen3 |
| 4   | LrcOptions has use_qwen3 flag accessible in admin CLI | FAILED | use_qwen3: bool = True exists in LrcOptions model (models.py:52), but admin CLI's submit_lrc() does NOT include parameter |
| 5   | Qwen3 service is included in docker-compose.yml with proper networking | VERIFIED | qwen3 and qwen3-dev services defined with build context ../qwen3, port 8001:8000, qwen3-cache volume |

**Score:** 4/5 truths verified

### Required Artifacts

| Artifact | Expected | Status | Details |
| -------- | -------- | ------ | ------- |
| services/analysis/src/sow_analysis/services/qwen3_client.py | HTTP client for Qwen3 align endpoint | VERIFIED | 136 lines, exports Qwen3Client, AlignRequest, AlignResponse, OutputFormat, Qwen3ClientError |
| services/analysis/src/sow_analysis/services/__init__.py | Exports Qwen3Client classes | VERIFIED | All required exports present |
| services/analysis/src/sow_analysis/models.py | LrcOptions with use_qwen3 flag | VERIFIED | Line 52: use_qwen3: bool = True |
| services/analysis/src/sow_analysis/config.py | Qwen3 service configuration | VERIFIED | SOW_QWEN3_BASE_URL = "http://qwen3:8000", SOW_QWEN3_API_KEY = "" |
| services/analysis/src/sow_analysis/workers/lrc.py | LRC generation with Qwen3 integration | VERIFIED | 722 lines, imports Qwen3Client, implements _qwen3_refine() and _parse_qwen3_lrc() |
| services/analysis/docker-compose.yml | Docker compose with qwen3 service | VERIFIED | qwen3 and qwen3-dev services with proper networking |

### Key Link Verification

| From | To | Via | Status | Details |
| ---- | --- | --- | ------ | ------- |
| qwen3_client.py | http://qwen3:8000/api/v1/align | httpx.AsyncClient POST | WIRED | Line 103-108: client.post() with request_body |
| lrc.py | Qwen3Client.align() | _qwen3_refine() helper | WIRED | Line 553-558: client.align(audio_url=..., lyrics_text=..., language="Chinese", format=OutputFormat.LRC) |
| lrc.py | s3://{SOW_R2_BUCKET}/audio/{hash}.mp3 | R2 URL construction | WIRED | Line 543: audio_url = f"s3://{settings.SOW_R2_BUCKET}/audio/{hash_prefix}.mp3" |
| analysis service container | qwen3 service container | Docker bridge network | WIRED | docker-compose.yml services on same network, internal communication on qwen3:8000 |
| queue.py | generate_lrc() with content_hash | _process_lrc_job() | WIRED | Line 596: content_hash=request.content_hash passed to generate_lrc() |
| **admin/services/analysis.py submit_lrc()** | **/api/v1/jobs/lrc with use_qwen3** | **payload options** | **NOT_WIRED** | Payload at line 242-248 does NOT include use_qwen3, only whisper_model, language, use_vocals_stem, force, force_whisper |

### Requirements Coverage

| Requirement | Status | Blocking Issue |
| ----------- | ------ | -------------- |
| INTG-01: Analysis Service has Qwen3Client HTTP client for alignment calls | SATISFIED | None |
| INTG-02: LRC pipeline with Qwen3 enabled produces accurate LRC files from Whisper path | SATISFIED | None |

### Anti-Patterns Found

No anti-patterns found in modified files:
- No TODO/FIXME/XXX/HACK/PLACEHOLDER markers
- No stub implementations (return null, [], {}, pass)
- No console.log only implementations

### Human Verification Required

#### 1. End-to-end LRC generation with Qwen3 enabled

**Test:** Run LRC generation via admin CLI or API with Whisper path (no youtube_url)
**Expected:** Analysis Service transcribes audio with Whisper, then calls Qwen3 align endpoint with R2 audio URL (s3:// format), receives refined LRC with accurate timestamps, and uploads to R2
**Why human:** Cannot verify Qwen3 service is running or produces accurate timestamps without running actual audio files through the pipeline

#### 2. End-to-end LRC generation with YouTube source

**Test:** Run LRC generation via admin CLI or API with youtube_url provided
**Expected:** LRC generated from YouTube transcript, skipping Whisper transcription AND Qwen3 refinement (since YouTube transcript already has accurate timestamps)
**Why human:** Cannot verify YouTube transcript path bypasses Qwen3 without running actual YouTube URL through the pipeline

### Gaps Summary

One gap blocking goal achievement:

**Gap: use_qwen3 flag not accessible from admin CLI**

The `use_qwen3` flag exists in the backend `LrcOptions` model with default value `True`, and the Analysis Service API can accept this field via `LrcJobRequest.options`. However, the admin CLI's `submit_lrc()` method does NOT include `use_qwen3` as a parameter or send it in the request payload.

**Impact:** Users of the admin CLI cannot control whether Qwen3 refinement is enabled or disabled - it will always use the default value (`True`).

**Evidence:**
- `services/analysis/src/sow_analysis/models.py:52`: `use_qwen3: bool = True` exists in LrcOptions
- `src/stream_of_worship/admin/services/analysis.py:206-249`: `submit_lrc()` method signature and payload do NOT include use_qwen3
- The admin CLI can still generate LRC files, but use_qwen3 always defaults to True on the backend

**Recommendation:** Add `use_qwen3: bool = True` parameter to `submit_lrc()` method signature and include it in the options payload at `src/stream_of_worship/admin/services/analysis.py`.

---

_Verified: 2026-02-13_
_Verifier: Claude (gsd-verifier)_
