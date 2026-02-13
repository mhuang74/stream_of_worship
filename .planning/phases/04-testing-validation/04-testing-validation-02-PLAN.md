---
phase: 04-testing-validation
plan: 02
type: execute
wave: 1
depends_on: []
files_modified:
  - services/analysis/tests/test_qwen3_regression.py
  - services/analysis/tests/fixtures/sample_audio.mp3
  - services/analysis/tests/fixtures/sample_lyrics.txt
  - services/analysis/tests/fixtures/golden_llm_lrc.txt
autonomous: true

must_haves:
  truths:
    - "Qwen3 output has equal or more lines than Whisper+LLM baseline (no lines lost)"
    - "Qwen3 timestamps are plausible within audio duration range"
    - "Regression test compares Qwen3 and baseline LRC structure"
    - "Golden fixture files exist for reproducible testing"
  artifacts:
    - path: "services/analysis/tests/test_qwen3_regression.py"
      provides: "Regression test comparing Qwen3 vs Whisper+LLM baseline"
      min_lines: 200
    - path: "services/analysis/tests/fixtures/sample_lyrics.txt"
      provides: "Sample lyrics for regression testing"
    - path: "services/analysis/tests/fixtures/golden_llm_lrc.txt"
      provides: "Golden baseline LRC from Whisper+LLM path"
  key_links:
    - from: "test_qwen3_regression.py"
      to: "workers/lrc.py"
      via: "import generate_lrc"
      pattern: "from sow_analysis.workers.lrc import generate_lrc"
    - from: "test_qwen3_regression.py"
      to: "services/qwen3_client.py"
      via: "mock Qwen3Client"
      pattern: "patch.*Qwen3Client"
---

<objective>
Create regression tests comparing Qwen3 output vs Whisper+LLM baseline

Purpose: Verify Qwen3 refinement maintains or improves timestamp accuracy compared to Whisper+LLM baseline
Output: test_qwen3_regression.py with golden file comparison strategy
</objective>

<execution_context>
@/home/mhuang/.claude/get-shit-done/workflows/execute-plan.md
@/home/mhuang/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/PROJECT.md
@.planning/ROADMAP.md
@services/analysis/src/sow_analysis/workers/lrc.py
@services/analysis/tests/test_qwen3_fallback.py
</context>

<tasks>

<task type="auto">
  <name>Create test fixtures directory and sample data</name>
  <files>
    services/analysis/tests/fixtures/sample_lyrics.txt
    services/analysis/tests/fixtures/sample_audio.mp3
  </files>
  <action>
    Create services/analysis/tests/fixtures/ directory and sample files:

    sample_lyrics.txt - Worship song lyrics with repeated chorus:
    ```
    Verse 1
    This is the first verse
    singing praises to the Lord

    Chorus
    Praise His holy name
    Praise His holy name
   Forever we will sing

    Verse 2
    This is the second verse
    lifting up our hearts to Him

    Chorus
    Praise His holy name
    Praise His holy name
    Forever we will sing
    ```

    sample_audio.mp3 - Create minimal dummy audio file:
    Use python to generate 30-second silence WAV then convert to MP3:
    ```python
    from pathlib import Path
    import numpy as np
    from scipy.io import wavfile

    # Generate 30 seconds of silence
    sample_rate = 44100
    duration = 30
    samples = np.zeros(sample_rate * duration, dtype=np.int16)
    wav_file = Path("services/analysis/tests/fixtures/sample_audio.wav")
    wavfile.write(str(wav_file), sample_rate, samples)
    ```

    Note: For actual testing, audio transcription will be mocked. This file prevents FileNotFoundError.
  </action>
  <verify>ls services/analysis/tests/fixtures/ shows sample_lyrics.txt and sample_audio.mp3</verify>
  <done>Test fixtures with lyrics and dummy audio created for regression testing</done>
</task>

<task type="auto">
  <name>Create regression tests with golden baseline comparison</name>
  <files>
    services/analysis/tests/test_qwen3_regression.py
    services/analysis/tests/fixtures/golden_llm_lrc.txt
  </files>
  <action>
    Create services/analysis/tests/test_qwen3_regression.py with regression test framework:

    Import generate_lrc, LrcOptions from sow_analysis.workers.lrc
    Import from unittest.mock: patch, MagicMock, AsyncMock

    Define parse_lrc_file() helper to load LRC file into list of (time, text) tuples

    Fixtures:
    - sample_audio_path(tmp_path) - Path to test audio file
    - sample_lyrics() - Load from fixtures/sample_lyrics.txt
    - golden_llm_lrc_path() - Path to fixture baseline
    - mock_whisper_phrases() - Realistic Whisper phrases matching lyrics

    Test cases:
    1. test_baseline_llm_lrc_generation() - Generate baseline LRC without Qwen3, save as golden
       - Set use_qwen3=False in LrcOptions
       - Mock Whisper transcription with realistic phrases
       - Mock LLM alignment to return proper LRC
       - Write output to golden_llm_lrc.txt fixture

    2. test_qwen3_vs_baseline_comparison() - Compare Qwen3 output to baseline
       - Load golden baseline LRC
       - Mock Whisper transcription (same as baseline)
       - Mock Qwen3Client to return refined LRC slightly different timing
       - Set use_qwen3=True
       - Run generate_lrc() with mocked Qwen3
       - Parse both LRC files
       - Verify Qwen3 output has same or more lines
       - Verify Qwen3 timestamps within reasonable range (not before 0, not after audio ends)
       - Verify Qwen3 maintains all unique text content from baseline

    3. test_qwen3_precision_improvement() - Verify timing granularity
       - Compare timestamp precision between baseline and Qwen3
       - Qwen3 should produce timestamps with .00-.99 precision (coarser is okay, no precision loss)
       - Verify timestamp ordering is maintained (monotonic increase)

    For test_qwen3_vs_baseline_comparison():
    - Assert len(qwen3_lines) >= len(baseline_lines)
    - Assert all timestamps >= 0.0
    - Assert all timestamps <= audio_duration (last whisper phrase end)
    - Assert unique lyric texts in Qwen3 are subset of baseline

    Mock Qwen3 response to simulate realistic refinement (slightly different timing, same text content).
  </action>
  <verify>cd services/analysis && PYTHONPATH=src uv run --extra dev pytest tests/test_qwen3_regression.py -v passes all tests</verify>
  <done>Regression tests verify Qwen3 maintains or improves accuracy vs Whisper+LLM baseline</done>
</task>

</tasks>

<verification>
Verify all regression tests pass:
```bash
cd services/analysis && PYTHONPATH=src uv run --extra dev pytest tests/test_qwen3_regression.py -v
```

Expected: All tests pass, Qwen3 output compared to baseline successfully
</verification>

<success_criteria>
1. Regression test framework exists with golden baseline comparison
2. Qwen3 output has equal or more lines than baseline (no information loss)
3. Qwen3 timestamps are plausible (within audio duration)
4. Golden fixture files exist for reproducible testing
5. All tests pass via pytest
</success_criteria>

<output>
After completion, create `.planning/phases/04-testing-validation/04-testing-validation-02-SUMMARY.md`
</output>
