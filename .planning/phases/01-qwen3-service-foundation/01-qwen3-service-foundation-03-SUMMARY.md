---
phase: 01-qwen3-service-foundation
plan: 03
subsystem: api
tags: [fastapi, alignment, pydantic, pydub, boto3]

# Dependency graph
requires:
  - phase: 01-qwen3-service-foundation-02
    provides: aligner wrapper with Qwen3AlignerWrapper class
provides:
  - POST /api/v1/align endpoint for lyrics-to-audio alignment
  - Audio download from R2/S3 with caching
  - Duration validation (5-minute limit)
  - LRC and JSON output formats
  - Character-to-line timestamp mapping
affects: [01-qwen3-service-foundation-04]

# Tech tracking
tech-stack:
  added: []
  patterns: [API-key-auth, s3-download-with-cache, segment-to-line-mapping]

key-files:
  created: [services/qwen3/src/sow_qwen3/models.py, services/qwen3/src/sow_qwen3/storage/audio.py, services/qwen3/src/sow_qwen3/routes/align.py]
  modified: [services/qwen3/src/sow_qwen3/main.py]

key-decisions:
  - "Share aligner getter from health route instead of duplicating"
  - "Map char-level segments to line-level timestamps for LRC output"
  - "Support both LRC and JSON output formats"

patterns-established:
  - "Pattern: API key verification via Authorization header (optional when API_KEY not set)"
  - "Pattern: Audio download with MD5 hash-based caching"
  - "Pattern: Share aligner getter across routes via health.get_aligner()"

# Metrics
duration: 8min
completed: 2026-02-13
---

# Phase 1 Plan 3: Align API Endpoint Summary

**POST /api/v1/align endpoint with audio download, duration validation, Qwen3 alignment, and LRC/JSON output formats**

## Performance

- **Duration:** 8 min
- **Started:** 2026-02-13T14:54:14Z
- **Completed:** 2026-02-13T15:02:22Z
- **Tasks:** 3
- **Files modified:** 3 created, 1 modified

## Accomplishments

- Created Pydantic models for align API (AlignRequest, AlignResponse, LyricLine, OutputFormat)
- Implemented audio download from R2/S3 with MD5 hash-based caching and duration validation
- Built POST /api/v1/align endpoint with API key verification, full alignment workflow, and LRC/JSON output
- Implemented character-to-line timestamp mapping algorithm for accurate lyric timing

## Task Commits

Each task was committed atomically:

1. **Task 1: Create Pydantic models in models.py** - `309a49b` (feat)
2. **Task 2: Create audio.py for download and duration validation** - `4e60b2a` (feat)
3. **Task 3: Create align.py route with alignment endpoint** - `6bae0e3` (feat)

## Files Created/Modified

- `services/qwen3/src/sow_qwen3/models.py` - Pydantic models for align API (AlignRequest, AlignResponse, LyricLine, OutputFormat)
- `services/qwen3/src/sow_qwen3/storage/audio.py` - Audio download from R2/S3, duration validation, MD5 hash caching
- `services/qwen3/src/sow_qwen3/storage/__init__.py` - Storage package initialization
- `services/qwen3/src/sow_qwen3/routes/align.py` - POST /api/v1/align endpoint with alignment workflow
- `services/qwen3/src/sow_qwen3/main.py` - Added align router import and include_router

## Decisions Made

- Shared aligner getter from health route instead of duplicating the pattern
  - Imported `get_aligner` from `.health` to reuse the aligner initialization from main.py
  - Plan specified importing from workers.aligner which doesn't have this function

- Implemented character-to-line timestamp mapping algorithm
  - Followed POC `gen_lrc_qwen3.py` map_segments_to_lines logic
  - Handles empty lines, missing matches via interpolation

- Added API key verification as optional feature
  - Only enforces when SOW_QWEN3_API_KEY environment variable is set
  - Allows deployment without authentication for internal use

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Correctness] Updated aligner import to use health.get_aligner**

- **Found during:** Task 3 (align.py route implementation)
- **Issue:** Plan specified `from ..workers.aligner import get_aligner` but this function doesn't exist; the aligner is exposed via health route pattern
- **Fix:** Imported `get_aligner` from `.health` to reuse the global aligner getter set by main.py
- **Files modified:** `services/qwen3/src/sow_qwen3/routes/align.py`
- **Verification:** Import succeeds, align route can access ready aligner instance
- **Committed in:** `6bae0e3` (Task 3 commit)

---

**Total deviations:** 1 auto-fixed (1 correctness fix)
**Impact on plan:** Essential fix for implementation correctness. The plan referenced non-existent function. Code follows established pattern from health route.

## Issues Encountered

- None - all tasks executed successfully with auto-fix

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- Align API endpoint complete, ready for Docker deployment (Plan 04)
- Audio download with R2/S3 support configured and functional
- LRC generation with Qwen3 alignment ready for integration with Analysis Service

---
*Phase: 01-qwen3-service-foundation*
*Completed: 2026-02-13*
