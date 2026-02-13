---
phase: 02-analysis-service-integration
plan: 02
subsystem: analysis-service
tags: [qwen3,integration,lrc,refinement]
dependency_graph:
  requires: ["02-analysis-service-integration-01"]
  provides: ["02-analysis-service-integration-03"]
  affects: []
tech_stack:
  added: []
  patterns: ["dual-path-lrc-generation"]
key_files:
  created: []
  modified:
    - "services/analysis/src/sow_analysis/config.py"
    - "services/analysis/src/sow_analysis/workers/lrc.py"
    - "services/analysis/src/sow_analysis/workers/queue.py"
decisions: []
metrics:
  duration: "537 seconds (8 minutes)"
  completed_date: "2026-02-13"
  tasks_completed: 2
  files_modified: 3
  commits: 2
---

# Phase 2 Plan 02: Integrate Qwen3 Timestamp Refinement into LRC Worker Summary

Implement Qwen3 forced alignment service integration into the LRC worker's Whisper transcription fallback path while preserving the existing YouTube transcript path for sources with accurate timestamps.

## One-Liner

Integrated Qwen3 forced alignment service into LRC worker for improved timestamp accuracy, with dual-path logic: YouTube transcript path skips Qwen3 (accurate from transcript), Whisper path uses Qwen3 refinement when enabled.

## Implementation

### Task 1: Add Qwen3 Configuration to Settings

Added Qwen3 service configuration to `services/analysis/src/sow_analysis/config.py`:

- `SOW_QWEN3_BASE_URL: str = "http://qwen3:8000"` - Docker network URL for Qwen3 service
- `SOW_QWEN3_API_KEY: str = ""` - Optional API key for authentication

These settings follow the SOW_* prefix pattern and allow the Analysis Service to discover and authenticate with the Qwen3 service via environment variables.

### Task 2: Integrate Qwen3 Refinement into LRC Worker Whisper Path

Modified the LRC worker to add Qwen3 timestamp refinement:

1. Added imports for `Qwen3Client` and `OutputFormat` from the services module
2. Created `_qwen3_refine(hash_prefix, lyrics_text)` helper function:
   - Constructs R2 URL in `s3://{bucket}/audio/{hash}.mp3` format
   - Instantiates Qwen3Client with configured base URL and API key
   - Calls `client.align()` with LRC output format
   - Returns the `lrc_content` field from AlignResponse

3. Created `_parse_qwen3_lrc(lrc_content)` helper function:
   - Parses standard LRC format `[mm:ss.xx] text`
   - Returns List[LRCLine] objects

4. Modified `generate_lrc()` function:
   - Added `content_hash: Optional[str]` parameter for R2 URL construction
   - Added Qwen3 refinement after LLM alignment in Whisper path
   - Qwen3 refinement only runs when `options.use_qwen3=True` AND `content_hash` is provided
   - YouTube path naturally bypasses Qwen3 (returns early at line 577)

5. Updated queue to pass `request.content_hash` to `generate_lrc`

## Verification

All implementation requirements verified:

- Settings has SOW_QWEN3_BASE_URL and SOW_QWEN3_API_KEY environment variables
- LRC worker imports Qwen3Client
- Qwen3 refinement happens in Whisper path only
- YouTube path bypasses Qwen3 (returns early)
- R2 URL constructed in s3://{bucket}/audio/{hash}.mp3 format for audio_url
- use_qwen3 flag controls Qwen3 refinement behavior
- Qwen3 refinement extracts lrc_content from AlignResponse

## Deviations from Plan

None - plan executed exactly as written.

## Key Links

```
services/analysis/src/sow_analysis/workers/lrc.py
  └─> http://qwen3:8000/api/v1/align (via Qwen3Client.align())
  └─> s3://{SOW_R2_BUCKET}/audio/{hash_prefix}.mp3 (R2 audio URL)

services/analysis/src/sow_analysis/config.py
  └─> SOW_QWEN3_BASE_URL (environment variable)
  └─> SOW_QWEN3_API_KEY (environment variable)
```

## Self-Check

```bash
[ -f "services/analysis/src/sow_analysis/config.py" ] && echo "FOUND: config.py"
[ -f "services/analysis/src/sow_analysis/workers/lrc.py" ] && echo "FOUND: lrc.py"
[ -f "services/analysis/src/sow_analysis/workers/queue.py" ] && echo "FOUND: queue.py"
git log --oneline --all | grep -q "415d396" && echo "FOUND: 415d396 (Task 1 commit)"
git log --oneline --all | grep -q "4f78681" && echo "FOUND: 4f78681 (Task 2 commit)"
```
