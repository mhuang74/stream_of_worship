---
phase: 02-analysis-service-integration
plan: 02
type: execute
wave: 2
depends_on: ["02-analysis-service-integration-01"]
files_modified:
  - services/analysis/src/sow_analysis/workers/lrc.py
  - services/analysis/src/sow_analysis/config.py
autonomous: true

must_haves:
  truths:
    - "LRC worker produces accurate Chinese LRC files from Whisper path with Qwen3 refinement"
    - "LRC worker produces accurate LRC files from YouTube path (skip Qwen3)"
    - "LRC worker skips Qwen3 refinement when youtube_url is provided or use_qwen3=False"
  artifacts:
    - path: "services/analysis/src/sow_analysis/workers/lrc.py"
      provides: "LRC generation with dual-path logic"
      contains: "Qwen3Client"
  key_links:
    - from: "services/analysis/src/sow_analysis/workers/lrc.py"
      to: "http://qwen3:8000/api/v1/align"
      via: "Qwen3Client.align() call in Whisper fallback path"
      pattern: "qwen3_client.*align"
    - from: "services/analysis/src/sow_analysis/workers/lrc.py"
      to: "R2 audio URL (s3:// format)"
      via: "R2 URL construction from hash_prefix"
      pattern: "s3://.*R2"
    - from: "services/analysis/src/sow_analysis/config.py"
      to: "http://qwen3:8000"
      via: "SOW_QWEN3_BASE_URL environment variable"
      pattern: "SOW_QWEN3_BASE_URL"
---

# Objective

Integrate Qwen3 timestamp refinement into the LRC worker's Whisper transcription fallback path while preserving YouTube transcript path (skipping Qwen3).

Purpose: Improve LRC timestamp accuracy by using Qwen3 forced alignment when available, while maintaining existing YouTube transcript path for sources with accurate timestamps.

Output:
- Modified LRC worker with dual-path logic: YouTube path (skip Qwen3) vs Whisper path (use Qwen3 when enabled)
- Qwen3 configuration in settings for service base URL and optional API key
- R2 URL construction for Qwen3 audio_url input (s3:// format)

<execution_context>
@/home/mhuang/.claude/get-shit-done/workflows/execute-plan.md
@/home/mhuang/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/PROJECT.md
@.planning/ROADMAP.md
@.planning/STATE.md

# Reference: Qwen3Client module (created in Plan 01)
@services/analysis/src/sow_analysis/services/qwen3_client.py

# Reference: Existing LRC worker (modify to add Qwen3 integration)
@services/analysis/src/sow_analysis/workers/lrc.py

# Reference: Configuration pattern (add Qwen3 settings)
@services/analysis/src/sow_analysis/config.py

# Reference: Phase 1 implementation details
@.planning/phases/01-qwen3-service-foundation/01-qwen3-service-foundation-03-SUMMARY.md
</context>

<tasks>

<task type="auto">
  <name>Add Qwen3 configuration to settings</name>
  <files>services/analysis/src/sow_analysis/config.py</files>
  <action>
    Modify `services/analysis/src/sow_analysis/config.py` to add Qwen3 client configuration:

    Add new environment-configured fields to settings (following existing SOW_* prefix pattern):

    1. `SOW_QWEN3_BASE_URL: str = "http://qwen3:8000"` - Default Docker network URL for Qwen3 service
    2. `SOW_QWEN3_API_KEY: Optional[str] = None` - Optional API key for Qwen3 service authentication

    Use pydantic-settings Field for these with proper descriptions:
    - SOW_QWEN3_BASE_URL: Base URL for Qwen3 Alignment Service (default: http://qwen3:8000)
    - SOW_QWEN3_API_KEY: Optional API key for Qwen3 service authentication

    Place these settings after the LLM configuration section, maintaining existing order.

    This allows the Analysis Service to discover and authenticate with the Qwen3 service via environment variables.
  </action>
  <verify>
    Verify settings exist:
    - `grep "SOW_QWEN3_BASE_URL" services/analysis/src/sow_analysis/config.py`
    - `grep "SOW_QWEN3_API_KEY" services/analysis/src/sow_analysis/config.py`
  </verify>
  <done>
    Qwen3 client configuration available via SOW_QWEN3_BASE_URL and SOW_QWEN3_API_KEY environment variables in settings.
  </done>
</task>

<task type="auto">
  <name>Integrate Qwen3 refinement into LRC worker Whisper path</name>
  <files>services/analysis/src/sow_analysis/workers/lrc.py</files>
  <action>
    Modify `generate_lrc()` function in `services/analysis/src/sow_analysis/workers/lrc.py`:

    1. Add import: `from ..services import Qwen3Client`

    2. After the LLM alignment call in the Whisper fallback path (around line 605), add Qwen3 refinement:

    Create new async helper function `_qwen3_refine()`:
    - Takes hash_prefix (str) and lyrics_text (str), returns refined LRC text
    - Constructs R2 URL from hash_prefix using R2 settings
    - R2 URL format: `s3://{SOW_R2_BUCKET}/audio/{hash_prefix}.mp3`
    - Instantiates Qwen3Client with settings.SOW_QWEN3_BASE_URL and settings.SOW_QWEN3_API_KEY
    - Calls client.align(audio_url=constructed_r2_url, lyrics_text=lyrics_text, language="Chinese", format=OutputFormat.LRC)
    - Returns response.lrc_content field (from AlignResponse)
    - Handles Qwen3ClientError gracefully and logs warning

    3. Modify generate_lrc() to add Qwen3 refinement path after LLM alignment:

    In the Whisper fallback path section (after `_llm_align()` call, around line 609):

    ```python
    # Qwen3 refinement: improve timestamp precision
    if options.use_qwen3 and job.hash_prefix:
        logger.info("=" * 80)
        logger.info("LRC GENERATION: Running Qwen3 timestamp refinement")
        logger.info("=" * 80)
        try:
            refined_lrc_text = await _qwen3_refine(
                hash_prefix=job.hash_prefix,
                lyrics_text=lyrics_text
            )
            # Parse refined LRC to update lrc_lines
            lrc_lines = _parse_qwen3_lrc(refined_lrc_text)
            logger.info(f"Qwen3 refinement completed: {len(lrc_lines)} lines")
        except Exception as e:
            logger.warning(f"Qwen3 refinement failed: {e}, using LLM timestamps")
    ```

    4. IMPORTANT: The YouTube transcript path should NOT call Qwen3 (already has accurate timestamps from transcript).

    5. Add `_parse_qwen3_lrc()` helper to parse Qwen3 LRC output into List[LRCLine]:
    - Use existing LRC line parsing logic from format() method
    - Parse `[mm:ss.xx] text` format

    6. Add R2 URL construction helper (optional or inline):
    - R2 URL pattern: `s3://{settings.SOW_R2_BUCKET}/audio/{hash_prefix}.mp3`
    - This matches the expected s3:// format for Qwen3 service audio loading

    BEHAVIOR CLARIFICATION: When youtube_url is provided, the function returns early (line 575) before reaching the Whisper path, so Qwen3 is naturally skipped. When youtube_url is NOT provided, Whisper path runs and Qwen3 refinement applies if use_qwen3=True and job.hash_prefix is available.

  </action>
  <verify>
    Verify integration:
    - `grep "Qwen3Client" services/analysis/src/sow_analysis/workers/lrc.py`
    - `grep "_qwen3_refine" services/analysis/src/sow_analysis/workers/lrc.py`
    - `grep "use_qwen3" services/analysis/src/sow_analysis/workers/lrc.py`
    - `grep "s3://.*SOW_R2_BUCKET" services/analysis/src/sow_analysis/workers/lrc.py`
  </verify>
  <done>
    LRC worker has Qwen3 refinement path in Whisper fallback, YouTube path skips Qwen3 (accurate from transcript), use_qwen3 flag controls behavior, R2 URL construction specified (s3://{bucket}/audio/{hash_prefix}.mp3).
  </done>
</task>

</tasks>

<verification>

After plan completion, verify:
1. Settings has SOW_QWEN3_BASE_URL and SOW_QWEN3_API_KEY
2. LRC worker imports Qwen3Client
3. Qwen3 refinement happens in Whisper path only
4. YouTube path bypasses Qwen3 (returns early)
5. R2 URL construction uses s3:// format with SOW_R2_BUCKET

Run: `PYTHONPATH=services/analysis/src uv run --extra test python -c "from sow_analysis.workers.lrc import generate_lrc; from sow_analysis.config import settings; assert hasattr(settings, 'SOW_QWEN3_BASE_URL'); print('Verification successful')"`

</verification>

<success_criteria>

Plan is successful when:
- SOW_QWEN3_BASE_URL and SOW_QWEN3_API_KEY environment variables available in settings
- LRC worker calls Qwen3Client.align() in Whisper path when use_qwen3=True and job.hash_prefix exists
- R2 URL constructed in s3://{bucket}/audio/{hash_prefix}.mp3 format for audio_url
- YouTube transcript path returns early, bypassing Qwen3 refinement
- use_qwen3 flag controls Qwen3 refinement behavior
- Qwen3 refinement extracts lrc_content from AlignResponse (not response.text)
- All imports verified without errors

</success_criteria>

<output>

After completion, create `.planning/phases/02-analysis-service-integration/02-analysis-service-integration-02-SUMMARY.md`

</output>
