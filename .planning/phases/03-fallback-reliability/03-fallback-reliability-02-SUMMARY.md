---
phase: 03-fallback-reliability
plan: 02
subsystem: LRC generation
tags: [whisper, qwen3, duration-validation, fallback, analytics]

# Dependency graph
requires:
  - phase: 02-analysis-service-integration
    provides: Qwen3 refinement integration, LrcOptions.use_qwen3 flag
provides:
  - max_qwen3_duration option to skip Qwen3 for long audio files
  - Duration validation before Qwen3 HTTP request
  - Automatic fallback to LLM-aligned LRC for songs > 300 seconds
affects: [03-03, lrc-worker, qwen3-client]

# Tech tracking
tech-stack:
  added: []
  patterns: [duration-based feature gating, early validation to avoid wasted requests]

key-files:
  created: []
  modified:
    - services/analysis/src/sow_analysis/models.py - Added max_qwen3_duration field
    - services/analysis/src/sow_analysis/workers/lrc.py - Added duration validation logic

key-decisions:
  - "Duration check at 300 seconds (5 min) matches Qwen3 service limit enforced in align.py"
  - "Skip Qwen3 refinement entirely for long audio, not just catch error - avoids wasted bandwidth/time"
  - "Duration calculated from Whisper phrases (max end time) - no need to re-analyze audio"

patterns-established:
  - "Duration-based feature gating: Validate before expensive operations"
  - "Graceful degradation: Skip Qwen3 and use LLM-aligned LRC when duration exceeds limit"

# Metrics
duration: 2min
completed: 2026-02-13
---

# Phase 03: Fallback & Reliability Summary

**Duration-based Qwen3 skip for long audio files with automatic fallback to LLM-aligned timestamps**

## Performance

- **Duration:** 2 min
- **Started:** 2026-02-13T12:22:59Z
- **Completed:** 2026-02-13T12:26:20Z
- **Tasks:** 3
- **Files modified:** 2

## Accomplishments

- Added `max_qwen3_duration` option (default 300s) to LrcOptions model
- Created `_get_audio_duration()` helper to calculate duration from Whisper phrases
- Implemented duration validation before Qwen3 refinement with skipping logic
- Audio exceeding 300 seconds skips Qwen3 entirely and uses LLM-aligned LRC
- Logs WARNING with duration and reason when skipping

## Task Commits

Each task was committed atomically:

1. **Task 1: Add max_qwen3_duration option to LrcOptions** - `3cbf080` (feat)
2. **Task 2: Create _get_audio_duration helper function** - `9174ebe` (feat, completed in prior plan)
3. **Task 3: Add duration validation before Qwen3 refinement** - `f268cfe` (feat)

**Plan metadata:** (final commit TBD)

## Files Created/Modified

- `services/analysis/src/sow_analysis/models.py` - Added `max_qwen3_duration: int = 300` field to LrcOptions class
- `services/analysis/src/sow_analysis/workers/lrc.py` - Added `_get_audio_duration()` helper and duration validation before Qwen3 call

## Deviations from Plan

### Auto-fixed Issues

**1. [No rules triggered] _get_audio_duration function already existed**
- **Found during:** Task 2 verification
- **Issue:** `_get_audio_duration()` function was already implemented in plan 03-01 (commit 9174ebe)
- **Fix:** Acknowledged existence, skipped duplicate implementation
- **Verification:** Function exists at line 577 with correct implementation
- **Committed in:** Already committed in plan 03-01

---

**Total deviations:** 1 pre-existing implementation
**Impact on plan:** Task 2 was already completed in prior plan; no additional work needed.

## Issues Encountered

None - all tasks completed as planned.

## User Setup Required

None - no external service configuration required. The `max_qwen3_duration` option can be configured via the LrcOptions model in API requests if a different threshold is needed.

## Next Phase Readiness

- Duration validation complete, ready for additional fallback mechanisms
- No blockers - all tasks verified and committed
- Plan 03-03 (likely additional fallback/reliability features) can proceed

---
*Phase: 03-fallback-reliability*
*Completed: 2026-02-13*

## Self-Check: PASSED

All verification checks passed:
- SUMMARY.md file created at correct location
- Commit 3cbf080: max_qwen3_duration option added
- Commit 9174ebe: _get_audio_duration helper function (completed in prior plan)
- Commit f268cfe: Duration validation before Qwen3 refinement
- max_qwen3_duration field exists in models.py
- _get_audio_duration function exists in lrc.py
- Duration validation logic exists in lrc.py
