---
phase: 04-testing-validation
plan: 03
type: execute
wave: 2
depends_on: ["04-testing-validation-01", "04-testing-validation-02"]
files_modified:
  - services/analysis/tests/test_lrc_integration_qwen3.py
  - services/analysis/tests/fixtures/integration_test_audio.mp3
  - services/analysis/tests/fixtures/integration_test_lyrics.txt
autonomous: true

must_haves:
  truths:
    - "Full LRC pipeline with Qwen3 enabled runs from start to finish without errors"
    - "Integration test validates Whisper transcription → LLM alignment → Qwen3 refinement flow"
    - "Output LRC file is valid format and contains all lyric lines"
    - "Integration test can run independently without external services (all mocked)"
  artifacts:
    - path: "services/analysis/tests/test_lrc_integration_qwen3.py"
      provides: "End-to-end integration test for LRC pipeline with Qwen3"
      min_lines: 200
    - path: "services/analysis/tests/fixtures/integration_test_lyrics.txt"
      provides: "Full song lyrics for integration testing"
      min_lines: 20
  key_links:
    - from: "test_lrc_integration_qwen3.py"
      to: "workers/lrc.py"
      via: "generate_lrc() function call"
      pattern: "generate_lrc\\("
    - from: "test_lrc_integration_qwen3.py"
      to: "services/qwen3_client.py"
      via: "Full Qwen3Client mock in integration test"
      pattern: "Mock.*Qwen3Client"
---

<objective>
Create end-to-end integration test for full LRC pipeline with Qwen3 enabled

Purpose: Validate the complete LRC generation flow works correctly with Qwen3 refinement, verifying all components integrate properly
Output: test_lrc_integration_qwen3.py with comprehensive integration test
</object>

<execution_context>
@/home/mhuang/.claude/get-shit-done/workflows/execute-plan.md
@/home/mhuang/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/PROJECT.md
@.planning/ROADMAP.md
@services/analysis/src/sow_analysis/workers/lrc.py
@services/analysis/tests/test_qwen3_fallback.py
@services/analysis/tests/test_qwen3_regression.py
@services/qwen3/tests/test_map_segments_to_lines.py
</context>

<tasks>

<task type="auto">
  <name>Create integration test fixtures</name>
  <files>
    services/analysis/tests/fixtures/integration_test_audio.mp3
    services/analysis/tests/fixtures/integration_test_lyrics.txt
  </files>
  <action>
    Create integration test fixtures:

    integration_test_lyrics.txt - Complete worship song with structure (verse, chorus repeated):
    ```
    Verse 1
    You are my strength when I am weak
    You are the treasure that I seek
    You are my all in all

    Chorus
    Seeking You as a precious jewel
    Lord, to give up I'd be a fool
    You are my all in all

    Verse 2
    Jesus, Lamb of God, worthy is Your name
    Jesus, Lamb of God, worthy is Your name

    Chorus
    Seeking You as a precious jewel
    Lord, to give up I'd be a fool
    You are my all in all

    Bridge
    Taking my sin, my cross, my shame
    Rising again I bless Your name

    Chorus
    Seeking You as a precious jewel
    Lord, to give up I'd be a fool
    You are my all in all
    ```

    integration_test_audio.mp3 - Dummy audio file (same as regression test):
    Generate minimal dummy audio or copy from regression fixtures.
    For integration testing this file prevents FileNotFoundError; all real processing is mocked.
  </action>
  <verify>ls services/analysis/tests/fixtures/ shows integration_test_lyrics.txt and integration_test_audio.mp3</verify>
  <done>Integration test fixtures created with complete song structure</done>
</task>

<task type="auto">
  <name>Create end-to-end integration test</name>
  <files>services/analysis/tests/test_lrc_integration_qwen3.py</files>
  <action>
    Create services/analysis/tests/test_lrc_integration_qwen3.py with E2E integration test:

    Import: generate_lrc, LrcOptions from sow_analysis.workers.lrc
    Import: AlignResponse from sow_analysis.services.qwen3_client
    Import: patch, AsyncMock from unittest.mock

    Helper functions:
    - parse_lrc_file(lrc_path) -> List[Tuple[float, str]]
    - count_unique_lines(lines) -> Dict[str, int]
    - verify_lrc_format(lines) -> bool

    Fixtures:
    - integration_audio_path(tmp_path) - Path to test audio
    - integration_lyrics() - Load from fixtures/integration_test_lyrics.txt
    - mock_whisper_phrases_full() - Comprehensive phrases matching song structure
    - mock_qwen3_response() - Realistic Qwen3 refined LRC

    Main integration test: test_full_pipeline_with_qwen3_enabled()
    Test the complete flow:
    1. Setup: Create audio file, load lyrics
    2. Mock Whisper transcription to return realistic phrases
    3. Mock LLM alignment to return base LRC
    4. Mock Qwen3Client to return refined LRC with character-level precision
    5. Create LrcOptions with use_qwen3=True and content_hash="test_hash_abc123"
    6. Call generate_lrc() with all mocks in place
    7. Verify:
       - Function returns successfully without exceptions
       - LRC file exists at output_path
       - LRC file contains all lyric lines from input
       - Timestamps are in ascending order (monotonic)
       - First timestamp is >= 0.0
       - Last timestamp is <= audio_duration (from mock Whisper)
       - Unique lyric count matches input (no lines lost)
       - Repeated sections have multiple entries (chorus appears 3 times)
    8. Verify Qwen3 client was actually called (mock.assert_called_once())
    9. Verify LLM alignment was called first (pipeline order)

    Verification test: test_qwen3_refinement_applied()
    Separate test to verify Qwen3 refinement actually replaces LLM timestamps:
    - LLM returns timestamps at 0, 5, 10, 15 seconds
    - Qwen3 returns refined timestamps at 0.12, 4.87, 10.05, 14.92 seconds
    - Assert final LRC uses Qwen3 timestamps (not LLM)

    Edge case test: test_qwen3_disabled_uses_llm()
    Run with use_qwen3=False:
    - Verify Qwen3Client is NOT called
    - Verify LLM alignment produces final output
    - Ensure pipeline still works correctly

    Use patterns from test_qwen3_fallback.py for mock structure, but test success path instead of fallback.
  </action>
  <verify>cd services/analysis && PYTHONPATH=src uv run --extra dev pytest tests/test_lrc_integration_qwen3.py -v passes all tests</verify>
  <done>End-to-end integration test validates full LRC pipeline with Qwen3 enabled</done>
</task>

</tasks>

<verification>
Verify all integration tests pass:
```bash
cd services/analysis && PYTHONPATH=src uv run --extra dev pytest tests/test_lrc_integration_qwen3.py -v
```

Expected: All tests pass, pipeline runs end-to-end without errors
</verification>

<success_criteria>
1. End-to-end integration test exists for full LRC pipeline with Qwen3
2. Integration test validates complete flow: Whisper → LLM → Qwen3 → LRC file
3. Output LRC is valid format with all lyric lines
4. Timestamps are monotonically increasing
5. Repeated sections (chorus) correctly appear multiple times
6. Qwen3 refinement is applied (replaces LLM timestamps)
7. Pipeline works with qwen3 disabled as fallback
8. All tests pass via pytest
</success_criteria>

<output>
After completion, create `.planning/phases/04-testing-validation/04-testing-validation-03-SUMMARY.md`
</output>
