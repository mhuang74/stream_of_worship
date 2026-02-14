---
phase: 03-fallback-reliability
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - services/analysis/src/sow_analysis/workers/lrc.py
autonomous: true
must_haves:
  truths:
    - "Qwen3 refinement catches all error types (HTTP, timeout, connection, response parse)"
    - "Qwen3 failures log at WARNING level and fall back to LLM-aligned LRC"
    - "Qwen3 success logs at INFO level before falling back"
    - "Empty LRC content from Qwen3 logs WARNING and falls back to LLM-aligned LRC"
  artifacts:
    - path: services/analysis/src/sow_analysis/workers/lrc.py
      provides: Robust Qwen3 error handling and fallback logic
      contains: "class Qwen3RefinementError"
  key_links:
    - from: services/analysis/src/sow_analysis/workers/lrc.py
      to: services/analysis/src/sow_analysis/services/qwen3_client.py
      via: "Qwen3Client.align() call within try/except"
      pattern: "except.*Qwen3ClientError|except.*Exception.*Qwen3"
---

<objective>
Implement robust error handling and fallback logic for Qwen3 refinement in LRC worker.

Purpose: Ensure Qwen3 failures never break the LRC pipeline — always fall back gracefully to LLM-aligned LRC.
Output: Robust error handling that catches all Qwen3 failures and logs appropriately.
</objective>

<execution_context>
@/home/mhuang/.claude/get-shit-done/workflows/execute-plan.md
@/home/mhuang/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/PROJECT.md
@.planning/ROADMAP.md
@.planning/STATE.md
@.planning/phases/02-analysis-service-integration/02-analysis-service-integration-02-SUMMARY.md
</context>

<tasks>

<task type="auto">
  <name>Task 1: Create Qwen3RefinementError exception class</name>
  <files>services/analysis/src/sow_analysis/workers/lrc.py</files>
  <action>
  Add Qwen3RefinementError exception class after LLMAlignmentError class (line ~47):

  ```python
  class Qwen3RefinementError(LRCWorkerError):
      """Raised when Qwen3 refinement fails (non-blocking, falls back to LLM)."""
      pass
  ```

  This distinguishes Qwen3 failures (non-fatal) from other LRC worker errors (fatal).
  </action>
  <verify>grep -q "class Qwen3RefinementError" services/analysis/src/sow_analysis/workers/lrc.py</verify>
  <done>Qwen3RefinementError exception class defined</done>
</task>

<task type="auto">
  <name>Task 2: Enhance Qwen3 refinement error handling with specific exception types</name>
  <files>services/analysis/src/sow_analysis/workers/lrc.py</files>
  <action>
  Replace the generic try/except block around _qwen3_refine() call (lines 688-705) with multi-catch error handling:

  ```python
  # Step 2.5: Qwen3 refinement (improve timestamp precision)
  if options.use_qwen3 and content_hash:
      logger.info("=" * 80)
      logger.info("LRC GENERATION: Running Qwen3 timestamp refinement")
      logger.info("=" * 80)
      try:
          refined_lrc_text = await _qwen3_refine(
              hash_prefix=content_hash,
              lyrics_text=lyrics_text,
          )
          # Parse refined LRC to update lrc_lines
          refined_lines = _parse_qwen3_lrc(refined_lrc_text)
          if refined_lines:
              lrc_lines = refined_lines
              logger.info(
                  f"Qwen3 refinement successful: {len(lrc_lines)} lines "
                  f"replaced LLM-aligned timestamps"
              )
          else:
              logger.warning(
                  "Qwen3 returned empty LRC content, using LLM-aligned timestamps"
              )
      except ConnectionError as e:
          logger.warning(
              f"Qwen3 service unavailable (connection error): {e}, "
              f"using LLM-aligned timestamps"
          )
      except asyncio.TimeoutError as e:
          logger.warning(
              f"Qwen3 service request timed out: {e}, "
              f"using LLM-aligned timestamps"
          )
      except Exception as e:
          # Catch Qwen3ClientError and any other exceptions
          logger.warning(
              f"Qwen3 refinement failed: {e}, using LLM-aligned timestamps"
            )
  ```

  Key changes:
  - Catch ConnectionError for network issues
  - Catch asyncio.TimeoutError for timeouts
  - Generic Exception catches Qwen3ClientError and any other errors
  - All exceptions fall back to LLM-aligned LRC (lrc_lines unchanged)
  - No exceptions propagate to caller (pipeline continues)
  - Detailed WARNING logs explain what failed and that fallback occurred
  - INFO log confirms successful refinement before continuing
  </action>
  <verify>
  grep -A3 "except ConnectionError" services/analysis/src/sow_analysis/workers/lrc.py | grep -q "using LLM-aligned"
  grep -A3 "except asyncio.TimeoutError" services/analysis/src/sow_analysis/workers/lrc.py | grep -q "using LLM-aligned"
  </verify>
  <done>Multi-catch error handling implemented with fallback to LLM-aligned LRC</done>
</task>

</tasks>

<verification>
- Qwen3RefinementError class defined after LLMAlignmentError
- catch ConnectionError for network failures
- catch asyncio.TimeoutError for timeout failures
- catch generic Exception for Qwen3ClientError and other errors
- All exceptions fall back to LLM-aligned LRC (pipeline continues)
- Empty LRC content logs WARNING and falls back
- Successful refinement logs INFO with line count
</verification>

<success_criteria>
LRC generation completes successfully when Qwen3:
- Service is unavailable/network error (ConnectionError caught)
- Request times out (TimeoutError caught)
- Returns error response (Qwen3ClientError caught)
- Returns empty/invalid LRC (empty lines check)
</success_criteria>

<output>
After completion, create `.planning/phases/03-fallback-reliability/03-fallback-reliability-01-SUMMARY.md`
</output>
