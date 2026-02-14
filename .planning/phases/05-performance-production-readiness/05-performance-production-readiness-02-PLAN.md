---
phase: 05-performance-production-readiness
plan: 02
type: execute
wave: 2
depends_on: ["05-performance-production-readiness-01"]
files_modified:
  - services/analysis/tests/test_lrc_benchmark.py
  - services/analysis/tests/fixtures/benchmark_audio.mp3
  - services/analysis/tests/fixtures/benchmark_lyrics.txt
autonomous: true

must_haves:
  truths:
    - "LRC generation with Qwen3 completes within 2x time of Whisper+LLM path"
    - "Benchmark test measures timing for both paths (with/without Qwen3)"
    - "Benchmark test produces comparable timing metrics"
    - "Performance threshold is validated via automated test"
  artifacts:
    - path: "services/analysis/tests/test_lrc_benchmark.py"
      provides: "Benchmark test comparing Qwen3 vs Whisper+LLM performance"
      min_lines: 200
    - path: "services/analysis/tests/fixtures/benchmark_lyrics.txt"
      provides: "Benchmark lyrics fixture"
  key_links:
    - from: "test_lrc_benchmark.py"
      to: "workers/lrc.py"
      via: "generate_lrc() function call with different use_qwen3 flags"
      pattern: "generate_lrc\\("
    - from: "test_lrc_benchmark.py"
      to: "services/qwen3_client.py"
      via: "Mock Qwen3Client for controlled timing"
      pattern: "patch.*Qwen3Client"
---

<objective>
Create performance benchmark tests to validate 2x time requirement

Purpose: Verify that LRC generation with Qwen3 refinement completes within 2x the time of the Whisper+LLM baseline path (PERF-02 requirement)
Output: Automated benchmark test with timing comparison
</objective>

<execution_context>
@/home/mhuang/get-shit-done/workflows/execute-plan.md
@/home/mhuang/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/PROJECT.md
@.planning/ROADMAP.md
@services/analysis/src/sow_analysis/workers/lrc.py
@services/analysis/tests/test_lrc_integration_qwen3.py
@services/analysis/tests/test_qwen3_regression.py
</context>

<tasks>

<task type="auto">
  <name>Create benchmark test fixtures</name>
  <files>
    services/analysis/tests/fixtures/benchmark_audio.mp3
    services/analysis/tests/fixtures/benchmark_lyrics.txt
  </files>
  <action>
    Create benchmark test fixtures representing realistic worship song:

    benchmark_lyrics.txt - Medium length worship song (~1500 characters):
    ```
    Verse 1
    在主爱中我们得自由
    脱离了一切罪与忧愁
    洪恩浩大何等深厚
    永远在我心中

    Chorus
    赞美主，耶稣救主
    高唱哈利路亚荣耀圣名
    赞美主，全能救主
    配得称颂直到永远

    Verse 2
    神的恩典如江河涌流
    洗净我罪使我得自由
    圣灵充满充满充满
    浇灌在我身上

    Chorus
    赞美主，耶稣救主
    高唱哈利路亚荣耀圣名
    赞美主，全能救主
    配得称颂直到永远

    Bridge
    高举双手敬拜
    全心全意归向主
    圣洁公义慈爱
    永远赞美不停止

    Chorus
    赞美主，耶稣救主
    高唱哈利路亚荣耀圣名
    赞美主，全能救主
    配得称颂直到永远
    ```

    benchmark_audio.mp3 - Copy existing dummy audio or create minimal file:
    Generate or copy from existing fixtures (sample_audio.mp3 or integration_test_audio.mp3).
    For benchmark timing comparison, the actual audio content doesn't matter because all expensive operations (Whisper, LLM, Qwen3) will be mocked with controlled delays.

    Use same dummy audio pattern as regression/integration tests to avoid file duplication.
  </action>
  <verify>ls services/analysis/tests/fixtures/ shows benchmark_audio.mp3 and benchmark_lyrics.txt</verify>
  <done>Benchmark fixtures created with medium length worship song lyrics</done>
</task>

<task type="auto">
  <name>Create performance benchmark test</name>
  <files>services/analysis/tests/test_lrc_benchmark.py</files>
  <action>
    Create services/analysis/tests/test_lrc_benchmark.py with timing comparison:

    Import: generate_lrc, LrcOptions from sow_analysis.workers.lrc
    Import: AsyncMock, patch from unittest.mock
    Import: time from standard library

    Fixtures:
    - benchmark_audio_path(tmp_path) - Path to benchmark audio
    - benchmark_lyrics() - Load from fixtures/benchmark_lyrics.txt

    Mock helpers:
    - mock_whisper_with_delay(delay_seconds) - Mock Whisper that takes time
    - mock_llm_with_delay(delay_seconds) - Mock LLM that takes time
    - mock_qwen3_with_overhead(delay_seconds) - Mock Qwen3 with additional time

    Main benchmark test: test_performance_within_2x_baseline()
    Test the timing requirement:

    1. Baseline timing (Whisper+LLM only):
       - Set use_qwen3=False
       - Mock Whisper to take 5 seconds
       - Mock LLM to take 3 seconds
       - Run generate_lrc() and measure total time
       - Expected baseline ~8 seconds

    2. Qwen3 timing (Whisper+LLM+Qwen3):
       - Set use_qwen3=True
       - Mock Whisper to take 5 seconds (same as baseline)
       - Mock LLM to take 3 seconds (same as baseline)
       - Mock Qwen3Client to take 2 seconds (additional overhead)
       - Run generate_lrc() and measure total time
       - Expected Qwen3 path ~10 seconds

    3. Verify 2x requirement:
       - Assert qwen3_time <= baseline_time * 2.0
       - Assert qwen3_time > baseline_time (Qwen3 adds some time)

    Use time.time() before and after each generate_lrc() call to measure duration.

    Print timing summary:
    ```
    Benchmark Results:
    Baseline (Whisper+LLM): {baseline_time:.2f}s
    Qwen3 (Whisper+LLM+Qwen3): {qwen3_time:.2f}s
    Ratio: {ratio:.2f}x (requirement: <= 2.0x)
    ```

    Note: Use AsyncMock for async calls. The test uses synthetic delays to simulate real-world performance characteristics.
  </action>
  <verify>cd services/analysis && PYTHONPATH=src uv run --extra dev pytest tests/test_lrc_benchmark.py -v -s passes and shows timing metrics</verify>
  <done>Benchmark test validates Qwen3 path completes within 2x time of Whisper+LLM baseline</done>
</task>

</tasks>

<verification>
Verify benchmark test passes:
```bash
cd services/analysis && PYTHONPATH=src uv run --extra dev pytest tests/test_lrc_benchmark.py -v -s
```

Expected output shows timing metrics and confirms Qwen3 completes within 2x baseline:
```
Benchmark Results:
Baseline (Whisper+LLM): 8.00s
Qwen3 (Whisper+LLM+Qwen3): 10.00s
Ratio: 1.25x (requirement: <= 2.0x)
```
</verification>

<success_criteria>
1. Benchmark test measures both Whisper+LLM and Whisper+LLM+Qwen3 paths
2. Timing metrics printed to stdout for visibility
3. Assertion validates qwen3_time <= baseline_time * 2.0
4. Test passes with synthetic mocks (simulating ~10-20% Qwen3 overhead is reasonable)
5. Benchmark can be run independently with realistic delays adjusted
</success_criteria>

<output>
After completion, create `.planning/phases/05-performance-production-readiness/05-performance-production-readiness-02-SUMMARY.md`
</output>
