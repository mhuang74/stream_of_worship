---
phase: 03-fallback-reliability
plan: 02
type: execute
wave: 1
depends_on:
  - 03-fallback-reliability-01
files_modified:
  - services/analysis/src/sow_analysis/workers/lrc.py
  - services/analysis/src/sow_analysis/models.py
autonomous: true
must_haves:
  truths:
    - "Audio duration is calculated and available before Qwen3 refinement"
    - "Songs exceeding 5 minutes skip Qwen3 and use LLM-aligned LRC"
    - "Duration skip is logged at WARNING level with reason"
    - "Duration is computed from Whisper phrases (max end time)"
  artifacts:
    - path: services/analysis/src/sow_analysis/models.py
      provides: duration_seconds field to LrcOptions for max duration threshold
      contains: "duration_seconds.*5.*60"
    - path: services/analysis/src/sow_analysis/workers/lrc.py
      provides: Duration validation before Qwen3 refinery
      contains: "def.*_get_audio_duration|300.*seconds"
  key_links:
    - from: services/analysis/src/sow_analysis/workers/lrc.py
      to: services/qwen3/src/sow_qwen3/routes/align.py
      via: "Duration validation before HTTP request"
      pattern: "duration.*>|300"
---

<objective>
Implement duration validation to skip Qwen3 refinement for long audio files.

Purpose: Avoid wasting bandwidth/time on Qwen3 requests that will fail due to 5-minute limit.
Output: Duration check that skips Qwen3 for audio >5 min and uses LLM-aligned LRC.
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
@.planning/phases/03-fallback-reliability/03-fallback-reliability-01-SUMMARY.md
@services/qwen3/src/sow_qwen3/routes/align.py
</context>

<tasks>

<task type="auto">
  <name>Task 1: Add max_qwen3_duration option to LrcOptions</name>
  <files>services/analysis/src/sow_analysis/models.py</files>
  <action>
  Add max_qwen3_duration field to LrcOptions class with default 5 minutes (300 seconds):

  ```python
  class LrcOptions(BaseModel):
      """Options for LRC generation."""

      use_qwen3: bool = True
      max_qwen3_duration: int = 300  # 5 minutes in seconds (Qwen3 service limit)
      whisper_model: str = "large-v3"
      language: str = "zh"
      llm_model: str = ""
      force: bool = False
  ```

  Reference Qwen3 service duration validation in align.py (line 197-202) which enforces 5-minute limit.
  </action>
  <verify>grep -q "max_qwen3_duration.*300" services/analysis/src/sow_analysis/models.py</verify>
  <done>LrcOptions.max_qwen3_duration defaults to 300 seconds (5 minutes)</done>
</task>

<task type="auto">
  <name>Task 2: Create _get_audio_duration helper function</name>
  <files>services/analysis/src/sow_analysis/workers/lrc.py</files>
  <action>
  Add _get_audio_duration() helper function after _parse_qwen3_lrc() function (line ~527):

  ```python
  def _get_audio_duration(whisper_phrases: List[WhisperPhrase]) -> float:
      """Calculate audio duration from Whisper transcription phrases.

      Args:
          whisper_phrases: List of WhisperPhrase with timing information

      Returns:
          Maximum end time in seconds (audio duration)

      Raises:
          ValueError: If whisper_phrases is empty
      """
      if not whisper_phrases:
          raise ValueError("Cannot calculate duration: no Whisper phrases available")

      # Duration is the maximum end time of all phrases
      return max(p.end for p in whisper_phrases)
  ```

  This function extracts duration from the Whisper phrases we already have after transcription.
  </action>
  <verify>grep -A10 "def _get_audio_duration" services/analysis/src/sow_analysis/workers/lrc.py | grep -q "max.*end"</verify>
  <done>_get_audio_duration helper function defined</done>
</task>

<task type="auto">
  <name>Task 3: Add duration validation before Qwen3 refinement</name>
  <files>services/analysis/src/sow_analysis/workers/lrc.py</files>
  <action>
  Add duration validation check before the Qwen3 refinement try/except block (insert before line 688):

  ```python
  # Step 2.5: Qwen3 refinement (improve timestamp precision)
  if options.use_qwen3 and content_hash:
      logger.info("=" * 80)
      logger.info("LRC GENERATION: Running Qwen3 timestamp refinement")
      logger.info("=" * 80)

      # Calculate audio duration from Whisper phrases
      try:
          audio_duration = _get_audio_duration(whisper_phrases)
          logger.info(f"Audio duration: {audio_duration:.2f} seconds")

          # Skip Qwen3 if audio exceeds max duration
          if audio_duration > options.max_qwen3_duration:
              logger.warning(
                  f"Audio duration ({audio_duration:.2f}s) exceeds Qwen3 limit "
                  f"({options.max_qwen3_duration}s), skipping Qwen3 refinement. "
                  f"Using LLM-aligned timestamps."
              )
          else:
              # Proceed with Qwen3 refinement
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
                  logger.warning(
                      f"Qwen3 refinement failed: {e}, using LLM-aligned timestamps"
                  )
      except ValueError as e:
          # Fallback if duration calculation fails
          logger.warning(
              f"Cannot calculate audio duration for Qwen3 validation: {e}, "
              f"using LLM-aligned timestamps"
          )
  ```

  Key changes:
  - Calculate audio duration before calling Qwen3
  - Log duration at INFO level
  - Skip Qwen3 if duration > 300 seconds (5 min)
  - Log WARNING with explanation when skipping
  - Use LLM-aligned LRC as result (no lrc_lines modification)
  - Wrapped in try/except for duration calculation safety
  </action>
  <verify>
  grep -B5 "audio_duration > options.max_qwen3_duration" services/analysis/src/sow_analysis/workers/lrc.py | grep -q "_get_audio_duration"
  grep -A3 "exceeds Qwen3 limit" services/analysis/src/sow_analysis/workers/lrc.py | grep -q "Using LLM-aligned"
  </verify>
  <done>Duration validation implemented to skip Qwen3 for audio >5 minutes</done>
</task>

</tasks>

<verification>
- LrcOptions.max_qwen3_duration defaults to 300 seconds
- _get_audio_duration() helper calculates duration from Whisper phrases
- Duration is checked before Qwen3 HTTP request
- Songs >300s skip Qwen3 and use LLM-aligned LRC
- Skip is logged at WARNING level with duration and reason
- Duration calculation wrapped in try/except for safety
</verification>

<success_criteria>
Songs exceeding 5 minutes:
- Skip Qwen3 refinement entirely (no HTTP request made)
- Log WARNING with duration explanation
- Use LLM-aligned LRC as the result
- LRC pipeline completes successfully
</success_criteria>

<output>
After completion, create `.planning/phases/03-fallback-reliability/03-fallback-reliability-02-SUMMARY.md`
</output>
