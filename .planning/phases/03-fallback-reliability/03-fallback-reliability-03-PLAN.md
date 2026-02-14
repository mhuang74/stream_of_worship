---
phase: 03-fallback-reliability
plan: 03
type: execute
wave: 2
depends_on:
  - 03-fallback-reliability-01
  - 03-fallback-reliability-02
files_modified:
  - services/analysis/tests/test_qwen3_fallback.py
autonomous: true
must_haves:
  truths:
    - "Mock Qwen3 service tests verify fallback to LLM-aligned LRC on failure"
    - "Tests cover service unavailable, timeout, and empty response scenarios"
    - "Duration skip test verifies songs >5min skip Qwen3"
    - "All tests pass with pytest"
  artifacts:
    - path: services/analysis/tests/test_qwen3_fallback.py
      provides: Mock tests for Qwen3 fallback behavior
      min_lines: 100
      contains: "test.*fallback|test.*duration.*skip"
  key_links:
    - from: services/analysis/tests/test_qwen3_fallback.py
      to: services/analysis/src/sow_analysis/workers/lrc.py
      via: "Mock Qwen3 service testing"
      pattern: "mocker.patch.*Qwen3Client|pytest.raises.*Qwen3ClientError"
---

<objective>
Create mock Qwen3 service tests to verify fallback behavior.

Purpose: Ensure Qwen3 fallback logic works correctly for all failure scenarios.
Output: Passing tests that verify robust fallback to LLM-aligned LRC.
</objective>

<execution_context>
@/home/mhuang/.claude/get-shit-done/workflows/execute-plan.md
@/home/mhuang/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/PROJECT.md
@.planning/ROADMAP.md
@.planning/STATE.md
@.planning/phases/03-fallback-reliability/03-fallback-reliability-01-SUMMARY.md
@.planning/phases/03-fallback-reliability/03-fallback-reliability-02-SUMMARY.md
@services/analysis/src/sow_analysis/workers/lrc.py
@services/analysis/tests/test_job_store.py
</context>

<tasks>

<task type="auto">
  <name>Task 1: Create test file with fallback test fixtures</name>
  <files>services/analysis/tests/test_qwen3_fallback.py</files>
  <action>
  Create test file services/analysis/tests/test_qwen3_fallback.py with fixtures and imports:

  ```python
  """Tests for Qwen3 fallback behavior when service fails or audio is too long."""

  import asyncio
  from pathlib import Path
  from unittest.mock import AsyncMock, patch

  import pytest

  from sow_analysis.models import LrcOptions, LrcJobRequest
  from sow_analysis.services.qwen3_client import Qwen3ClientError
  from sow_analysis.workers.lrc import generate_lrc


  @pytest.fixture
  def sample_audio_path(tmp_path: Path) -> Path:
      """Create a dummy audio file for testing."""
      audio_path = tmp_path / "test.mp3"
      audio_path.write_bytes(b"fake audio data")
      return audio_path


  @pytest.fixture
  def sample_lyrics() -> str:
      """Sample lyrics for testing."""
      return "Verse 1\nChorus lyrics\nVerse 2\nChorus lyrics again"


  @pytest.fixture
  def mock_whisper_phrases() -> list:
      """Mock Whisper transcription phrases (short audio, <1 min)."""
      from sow_analysis.workers.lrc import WhisperPhrase
      return [
          WhisperPhrase(text="Verse 1", start=0.0, end=5.0),
          WhisperPhrase(text="Chorus lyrics", start=5.0, end=10.0),
          WhisperPhrase(text="Verse 2", start=10.0, end=15.0),
          WhisperPhrase(text="Chorus lyrics again", start=15.0, end=20.0),
      ]


  @pytest.fixture
  def long_audio_phrases() -> list:
      """Mock Whisper transcription phrases for long audio (>5 min)."""
      from sow_analysis.workers.lrc import WhisperPhrase
      return [
          WhisperPhrase(text="First line", start=0.0, end=10.0),
          WhisperPhrase(text="...", start=10.0, end=310.0),  # 5+ minute audio
      ]


  @pytest.fixture
  def mock_llm_align_response() -> list:
      """Mock LLM-aligned LRC lines (fallback result)."""
      from sow_analysis.workers.lrc import LRCLine
      return [
          LRCLine(time_seconds=0.0, text="Verse 1"),
          LRCLine(time_seconds=5.0, text="Chorus lyrics"),
          LRCLine(time_seconds=10.0, text="Verse 2"),
          LRCLine(time_seconds=15.0, text="Chorus lyrics again"),
      ]
  ```

  Follow testing pattern from test_job_store.py (fixtures with tmp_path)
  </action>
  <verify>grep -q "def sample_audio_path" services/analysis/tests/test_qwen3_fallback.py</verify>
  <done>Test file created with fixtures for mock audio, lyrics, and phrases</done>
</task>

<task type="auto">
  <name>Task 2: Test service unavailable fallback</name>
  <files>services/analysis/tests/test_qwen3_fallback.py</files>
  <action>
  Add test for service unavailable scenario:

  ```python
  @pytest.mark.asyncio
  async def test_qwen3_service_unavailable_fallback(
      sample_audio_path: Path,
      sample_lyrics: str,
      mock_whisper_phrases: list,
      mock_llm_align_response: list,
  ) -> None:
      """Test that Qwen3 service unavailability falls back to LLM-aligned LRC."""
      from sow_analysis.workers.lrc import WhisperPhrase, _llm_align

      options = LrcOptions(use_qwen3=True)

      # Mock Whisper transcription
      with patch(
          "sow_analysis.workers.lrc._run_whisper_transcription",
          return_value=mock_whisper_phrases,
      ):
          # Mock LLM alignment
          with patch(
              "sow_analysis.workers.lrc._llm_align",
              new_callable=AsyncMock,
              return_value=mock_llm_align_response,
          ):
              # Mock Qwen3Client to raise ConnectionError (service unavailable)
              with patch(
                  "sow_analysis.workers.lrc.Qwen3Client",
                  side_effect=ConnectionError("Cannot connect to Qwen3 service"),
              ):
                  lrc_path, line_count, phrases = await generate_lrc(
                      audio_path=sample_audio_path,
                      lyrics_text=sample_lyrics,
                      options=options,
                      output_path=sample_audio_path.with_suffix(".lrc"),
                      content_hash="abc123",  # Enable Qwen3
                  )

                  # Verify LRC file was created (from LLM alignment, not Qwen3)
                  assert lrc_path.exists()
                  assert line_count == len(mock_llm_align_response)

                  # Verify LLM alignment was called (fallback worked)
                  _llm_align.assert_called_once()
  ```

  This test verifies that when Qwen3 service is unavailable, the pipeline falls back to LLM-aligned LRC.
  </action>
  <verify>grep -q "test_qwen3_service_unavailable_fallback" services/analysis/tests/test_qwen3_fallback.py</verify>
  <done>Test added for service unavailable fallback scenario</done>
</task>

<task type="auto">
  <name>Task 3: Test timeout fallback</name>
  <files>services/analysis/tests/test_qwen3_fallback.py</files>
  <action>
  Add test for Qwen3 timeout scenario:

  ```python
  @pytest.mark.asyncio
  async def test_qwen3_timeout_fallback(
      sample_audio_path: Path,
      sample_lyrics: str,
      mock_whisper_phrases: list,
      mock_llm_align_response: list,
  ) -> None:
      """Test that Qwen3 timeout falls back to LLM-aligned LRC."""
      from sow_analysis.workers.lrc import _llm_align

      options = LrcOptions(use_qwen3=True)

      with patch(
          "sow_analysis.workers.lrc._run_whisper_transcription",
          return_value=mock_whisper_phrases,
      ):
          with patch(
              "sow_analysis.workers.lrc._llm_align",
              new_callable=AsyncMock,
              return_value=mock_llm_align_response,
          ):
              # Create a mock client that raises TimeoutError
              mock_client = AsyncMock()
              mock_client.align.side_effect = asyncio.TimeoutError("Qwen3 timed out")

              with patch(
                  "sow_analysis.workers.lrc.Qwen3Client",
                  return_value=mock_client,
              ):
                  lrc_path, line_count, phrases = await generate_lrc(
                      audio_path=sample_audio_path,
                      lyrics_text=sample_lyrics,
                      options=options,
                      output_path=sample_audio_path.with_suffix(".lrc"),
                      content_hash="def456",
                  )

                  # Verify LRC file was created
                  assert lrc_path.exists()
                  assert line_count == len(mock_llm_align_response)

                  # Verify LLM alignment was called (Qwen3 failed but pipeline continued)
                  _llm_align.assert_called_once()
  ```

  This test verifies that timeout errors are caught and handled gracefully.
  </action>
  <verify>grep -q "test_qwen3_timeout_fallback" services/analysis/tests/test_qwen3_fallback.py</verify>
  <done>Test added for Qwen3 timeout fallback scenario</done>
</task>

<task type="auto">
  <name>Task 4: Test Qwen3ClientError fallback</name>
  <files>services/analysis/tests/test_qwen3_fallback.py</files>
  <action>
  Add test for Qwen3ClientError (HTTP error response):

  ```python
  @pytest.mark.asyncio
  async def test_qwen3_http_error_fallback(
      sample_audio_path: Path,
      sample_lyrics: str,
      mock_whisper_phrases: list,
      mock_llm_align_response: list,
  ) -> None:
      """Test that Qwen3 HTTP errors fall back to LLM-aligned LRC."""
      from sow_analysis.workers.lrc import _llm_align

      options = LrcOptions(use_qwen3=True)

      with patch(
          "sow_analysis.workers.lrc._run_whisper_transcription",
          return_value=mock_whisper_phrases,
      ):
          with patch(
              "sow_analysis.workers.lrc._llm_align",
              new_callable=AsyncMock,
              return_value=mock_llm_align_response,
          ):
              # Mock client that raises Qwen3ClientError
              mock_client = AsyncMock()
              mock_client.align.side_effect = Qwen3ClientError(
                  "Qwen3 service error: 500 - Internal Server Error"
              )

              with patch(
                  "sow_analysis.workers.lrc.Qwen3Client",
                  return_value=mock_client,
              ):
                  lrc_path, line_count, phrases = await generate_lrc(
                      audio_path=sample_audio_path,
                      lyrics_text=sample_lyrics,
                      options=options,
                      output_path=sample_audio_path.with_suffix(".lrc"),
                      content_hash="ghi789",
                  )

                  # Verify LRC file was created
                  assert lrc_path.exists()
                  assert line_count == len(mock_llm_align_response)

                  # Verify LLM alignment was called (Qwen3 error did not stop pipeline)
                  _llm_align.assert_called_once()
  ```

  This test verifies that Qwen3ClientError exceptions are caught properly.
  </action>
  <verify>grep -q "test_qwen3_http_error_fallback" services/analysis/tests/test_qwen3_fallback.py</verify>
  <done>Test added for Qwen3ClientError fallback scenario</done>
</task>

<task type="auto">
  <name>Task 5: Test duration skip for long audio</name>
  <files>services/analysis/tests/test_qwen3_fallback.py</files>
  <action>
  Add test for duration skip (audio >5 min):

  ```python
  @pytest.mark.asyncio
  async def test_qwen3_skip_long_audio(
      sample_audio_path: Path,
      sample_lyrics: str,
      long_audio_phrases: list,
      mock_llm_align_response: list,
  ) -> None:
      """Test that audio exceeding max duration skips Qwen3 refinement."""
      from sow_analysis.workers.lrc import _llm_align

      # Set max_qwen3_duration to 60 seconds (1 minute) for testing
      options = LrcOptions(use_qwen3=True, max_qwen3_duration=60)

      with patch(
          "sow_analysis.workers.lrc._run_whisper_transcription",
          return_value=long_audio_phrases,  # 310 seconds
      ):
          with patch(
              "sow_analysis.workers.lrc._llm_align",
              new_callable=AsyncMock,
              return_value=mock_llm_align_response,
          ):
              # Qwen3Client should NOT be called (duration check skips it)
              with patch(
                  "sow_analysis.workers.lrc.Qwen3Client"
              ) as mock_qwen3_client:
                  lrc_path, line_count, phrases = await generate_lrc(
                      audio_path=sample_audio_path,
                      lyrics_text=sample_lyrics,
                      options=options,
                      output_path=sample_audio_path.with_suffix(".lrc"),
                      content_hash="jkl012",
                  )

                  # Verify LRC file was created
                  assert lrc_path.exists()
                  assert line_count == len(mock_llm_align_response)

                  # Verify Qwen3Client was NOT instantiated (skipped due to duration)
                  mock_qwen3_client.assert_not_called()

                  # Verify LLM alignment was called (used as fallback)
                  _llm_align.assert_called_once()
  ```

  This test verifies that long audio skips Qwen3 entirely and uses LLM-aligned LRC.
  </action>
  <verify>grep -q "test_qwen3_skip_long_audio" services/analysis/tests/test_qwen3_fallback.py</verify>
  <done>Test added for duration skip scenario</done>
</task>

<task type="auto">
  <name>Task 6: Test successful Qwen3 refinement</name>
  <files>services/analysis/tests/test_qwen3_fallback.py</files>
  <action>
  Add test for successful Qwen3 refinement (happy path):

  ```python
  @pytest.mark.asyncio
  async def test_qwen3_successful_refinement(
      sample_audio_path: Path,
      sample_lyrics: str,
      mock_whisper_phrases: list,
  ) -> None:
      """Test that successful Qwen3 refinement updates LRC lines."""
      from sow_analysis.models import AlignResponse, OutputFormat
      from sow_analysis.workers.lrc import _llm_align

      options = LrcOptions(use_qwen3=True)

      # Qwen3-refined LRC content (different from LLM output)
      refined_lrc_content = "[00:00.00] Verse 1\n[00:04.50] Chorus lyrics\n[00:09.80] Verse 2\n[00:14.70] Chorus lyrics again"

      with patch(
          "sow_analysis.workers.lrc._run_whisper_transcription",
          return_value=mock_whisper_phrases,
      ):
          # Mock LLM alignment (will be replaced by Qwen3)
          llm_aligned = AsyncMock(return_value=[
              WhisperPhrase(text="Verse 1", start=0.0, end=5.0),
              WhisperPhrase(text="Chorus", start=5.0, end=10.0),
              WhisperPhrase(text="Verse 2", start=10.0, end=15.0),
              WhisperPhrase(text="Chorus", start=15.0, end=20.0),
          ])
          with patch("sow_analysis.workers.lrc._llm_align", llm_aligned):
              # Mock successful Qwen3 response
              mock_client = AsyncMock()
              mock_client.align.return_value = AlignResponse(
                  lrc_content=refined_lrc_content,
                  json_data=None,
                  line_count=4,
                  duration_seconds=20.0,
              )

              with patch(
                  "sow_analysis.workers.lrc.Qwen3Client",
                  return_value=mock_client,
              ):
                  lrc_path, line_count, phrases = await generate_lrc(
                      audio_path=sample_audio_path,
                      lyrics_text=sample_lyrics,
                      options=options,
                      output_path=sample_audio_path.with_suffix(".lrc"),
                      content_hash="mno345",
                  )

                  # Verify LRC file was created
                  assert lrc_path.exists()
                  assert line_count == 4

                  # Verify Qwen3 was called
                  mock_client.align.assert_called_once()

                  # Verify LRC contains Qwen3-timestamped content (not LLM)
                  lrc_text = lrc_path.read_text(encoding="utf-8")
                  assert "00:04.50" in lrc_text  # Qwen3's precise timestamp
                  assert "00:09.80" in lrc_text
  ```

  This test verifies the happy path where Qwen3 succeeds and replaces LLM timestamps.
  </action>
  <verify>grep -q "test_qwen3_successful_refinement" services/analysis/tests/test_qwen3_fallback.py</verify>
  <done>Test added for successful Qwen3 refinement scenario</done>
</task>

</tasks>

<verification>
- test_qwen3_service_unavailable_fallback: ConnectionError falls back to LLM
- test_qwen3_timeout_fallback: TimeoutError falls back to LLM
- test_qwen3_http_error_fallback: Qwen3ClientError falls back to LLM
- test_qwen3_skip_long_audio: Duration >5min skips Qwen3 entirely
- test_qwen3_successful_refinement: Qwen3 success updates LRC with precise timestamps
- All tests run successfully with pytest
</verification>

<success_criteria>
Mock Qwen3 service tests verify:
1. Service unavailable (ConnectionError) → LLM fallback
2. Timeout → LLM fallback
3. HTTP error (Qwen3ClientError) → LLM fallback
4. Long audio >5min → skips Qwen3, uses LLM
5. Successful refinement → Qwen3 timestamps used
</success_criteria>

<output>
After completion, create `.planning/phases/03-fallback-reliability/03-fallback-reliability-03-SUMMARY.md`
</output>
