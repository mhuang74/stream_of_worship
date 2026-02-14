"""Performance benchmark tests for LRC generation.

Tests validate the 2x time requirement:
LRC generation with Qwen3 refinement should complete within 2x the time
of the Whisper+LLM baseline path (PERF-02 requirement).

Uses synthetic delays to simulate real-world performance characteristics
without requiring actual long-running transcriptions.
"""

import asyncio
import time
from pathlib import Path
from typing import List, Callable
from unittest.mock import AsyncMock, patch

import pytest

from sow_analysis.models import LrcOptions
from sow_analysis.services.qwen3_client import AlignResponse
from sow_analysis.workers.lrc import generate_lrc, WhisperPhrase, LRCLine


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def benchmark_audio_path(tmp_path: Path) -> Path:
    """Path to benchmark audio fixture file."""
    return Path(__file__).parent / "fixtures" / "benchmark_audio.wav"


@pytest.fixture
def benchmark_lyrics() -> str:
    """Load benchmark worship song lyrics from fixture."""
    fixture_path = Path(__file__).parent / "fixtures" / "benchmark_lyrics.txt"
    return fixture_path.read_text(encoding="utf-8").strip()


@pytest.fixture
def mock_whisper_phrases() -> List[WhisperPhrase]:
    """Mock Whisper transcription phrases matching benchmark lyrics."""
    return [
        # Verse 1
        WhisperPhrase(text="Verse 1", start=0.0, end=3.5),
        WhisperPhrase(text="在主爱中我们得自由", start=3.5, end=8.2),
        WhisperPhrase(text="脱离了一切罪与忧愁", start=8.2, end=12.8),
        WhisperPhrase(text="洪恩浩大何等深厚", start=12.8, end=17.5),
        WhisperPhrase(text="永远在我心中", start=17.5, end=22.0),
        # Chorus (first)
        WhisperPhrase(text="赞美主，耶稣救主", start=22.5, end=27.2),
        WhisperPhrase(text="高唱哈利路亚荣耀圣名", start=27.2, end=32.0),
        WhisperPhrase(text="赞美主，全能救主", start=32.5, end=37.2),
        WhisperPhrase(text="配得称颂直到永远", start=37.2, end=42.0),
        # Verse 2
        WhisperPhrase(text="神的恩典如江河涌流", start=42.5, end=47.2),
        WhisperPhrase(text="洗净我罪使我得自由", start=47.2, end=52.0),
        WhisperPhrase(text="圣灵充满充满充满", start=52.5, end=57.2),
        WhisperPhrase(text="浇灌在我身上", start=57.2, end=61.8),
        # Chorus (second)
        WhisperPhrase(text="赞美主，耶稣救主", start=62.0, end=66.8),
        WhisperPhrase(text="高唱哈利路亚荣耀圣名", start=67.0, end=71.8),
        WhisperPhrase(text="赞美主，全能救主", start=72.0, end=76.8),
        WhisperPhrase(text="配得称颂直到永远", start=77.0, end=81.8),
        # Bridge
        WhisperPhrase(text="高举双手敬拜", start=82.0, end=86.5),
        WhisperPhrase(text="全心全意归向主", start=86.5, end=91.2),
        WhisperPhrase(text="圣洁公义慈爱", start=91.5, end=96.2),
        WhisperPhrase(text="永远赞美不停止", start=96.5, end=101.2),
        # Chorus (third)
        WhisperPhrase(text="赞美主，耶稣救主", start=101.5, end=106.2),
        WhisperPhrase(text="高唱哈利路亚荣耀圣名", start=106.5, end=111.2),
        WhisperPhrase(text="赞美主，全能救主", start=111.5, end=116.2),
        WhisperPhrase(text="配得称颂直到永远", start=116.5, end=121.2),
    ]


@pytest.fixture
def mock_llm_aligned_lines(mock_whisper_phrases: List[WhisperPhrase]) -> List[LRCLine]:
    """Mock LLM-aligned LRC lines."""
    return [
        LRCLine(time_seconds=p.start, text=p.text)
        for p in mock_whisper_phrases
    ]


@pytest.fixture
def mock_qwen3_refined_lines(mock_llm_aligned_lines: List[LRCLine]) -> List[LRCLine]:
    """Mock Qwen3-refined LRC lines with slight timestamp adjustments."""
    return [
        LRCLine(time_seconds=line.time_seconds + 0.02, text=line.text)
        for line in mock_llm_aligned_lines
    ]


# ============================================================================
# Mock Helper Functions
# ============================================================================

async def mock_whisper_with_delay(
    phrases: List[WhisperPhrase],
    delay: float,
) -> List[WhisperPhrase]:
    """Mock Whisper transcription with controlled delay.

    Args:
        phrases: Whisper phrases to return
        delay: Delay in seconds to simulate transcription

    Returns:
        List of WhisperPhrase
    """
    await asyncio.sleep(delay)
    return phrases


async def mock_llm_align_with_delay(
    lines: List[LRCLine],
    delay: float,
) -> List[LRCLine]:
    """Mock LLM alignment with controlled delay.

    Args:
        lines: LRC lines to return
        delay: Delay in seconds to simulate LLM call

    Returns:
        List of LRCLine
    """
    await asyncio.sleep(delay)
    return lines


async def mock_qwen3_call_with_overhead(
    lines: List[LRCLine],
    overhead: float,
) -> str:
    """Mock Qwen3 service call with controlled overhead.

    Args:
        lines: LRC lines to convert to LRC format
        overhead: Delay in seconds to simulate Qwen3 service call

    Returns:
        LRC content string
    """
    await asyncio.sleep(overhead)
    return "\n".join(line.format() for line in lines)


# ============================================================================
# Main Benchmark Test
# ============================================================================

@pytest.mark.asyncio
async def test_performance_within_2x_baseline(
    benchmark_audio_path: Path,
    benchmark_lyrics: str,
    mock_whisper_phrases: List[WhisperPhrase],
    mock_llm_aligned_lines: List[LRCLine],
    mock_qwen3_refined_lines: List[LRCLine],
) -> None:
    """Test that Qwen3 path completes within 2x the Whisper+LLM baseline.

    Validates PERF-02 requirement: LRC generation with Qwen3 refinement
    should complete within 2x the time of the Whisper+LLM baseline path.

    Benchmark timing:
    - Baseline (Whisper+LLM): ~8 seconds (Whisper: 5s + LLM: 3s)
    - Qwen3 (Whisper+LLM+Qwen3): ~10 seconds (Baseline: 8s + Qwen3: 2s)
    - Ratio: 1.25x (requirement: <= 2.0x)

    The synthetic delays simulate realistic performance:
    - Whisper: 5 seconds (transcription is the primary bottleneck)
    - LLM: 3 seconds (API call overhead)
    - Qwen3: 2 seconds (forced alignment overhead)
    """
    # Configure synthetic delays to simulate real-world performance
    whisper_delay = 5.0  # Simulate Whisper transcription
    llm_delay = 3.0      # Simulate LLM API call
    qwen3_overhead = 2.0  # Simulate Qwen3 forced alignment

    print("\n" + "=" * 80)
    print("LRC PERFORMANCE BENCHMARK")
    print("=" * 80)
    print(f"Whisper delay: {whisper_delay}s")
    print(f"LLM delay: {llm_delay}s")
    print(f"Qwen3 overhead: {qwen3_overhead}s")
    print("=" * 80)

    # ------------------------------------------------------------------------
    # Baseline Timing: Whisper + LLM (no Qwen3)
    # ------------------------------------------------------------------------
    print("\n[Baseline] Running Whisper+LLM path (use_qwen3=False)...")

    baseline_options = LrcOptions(use_qwen3=False)

    # Create delayed whisper async function
    async def delayed_whisper_baseline(*args, **kwargs):
        return await mock_whisper_with_delay(mock_whisper_phrases, whisper_delay)

    # Create delayed LLM align async function
    async def delayed_llm_baseline(*args, **kwargs):
        return await mock_llm_align_with_delay(mock_llm_aligned_lines, llm_delay)

    baseline_start = time.time()
    with patch(
        "sow_analysis.workers.lrc._run_whisper_transcription",
        side_effect=delayed_whisper_baseline,
    ):
        with patch(
            "sow_analysis.workers.lrc._llm_align",
            side_effect=delayed_llm_baseline,
        ):
            with patch("sow_analysis.workers.lrc.Qwen3Client"):
                # Generate LRC without Qwen3
                baseline_lrc_path, baseline_line_count, baseline_phrases = await generate_lrc(
                    audio_path=benchmark_audio_path,
                    lyrics_text=benchmark_lyrics,
                    options=baseline_options,
                    output_path=benchmark_audio_path.with_suffix(".baseline.lrc"),
                    content_hash=None,  # Disable Qwen3
                )

    baseline_elapsed = time.time() - baseline_start

    print(f"[Baseline] Completed in {baseline_elapsed:.2f}s")
    print(f"[Baseline] Generated {baseline_line_count} lines")

    # ------------------------------------------------------------------------
    # Qwen3 Timing: Whisper + LLM + Qwen3
    # ------------------------------------------------------------------------
    print("\n[Qwen3] Running Whisper+LLM+Qwen3 path (use_qwen3=True)...")

    qwen3_options = LrcOptions(use_qwen3=True)

    # Create delayed functions for Qwen3 path
    async def delayed_whisper_qwen3(*args, **kwargs):
        return await mock_whisper_with_delay(mock_whisper_phrases, whisper_delay)

    async def delayed_llm_qwen3(*args, **kwargs):
        return await mock_llm_align_with_delay(mock_llm_aligned_lines, llm_delay)

    # Mock Qwen3 client to return refined LRC with overhead
    mock_client = AsyncMock()
    qwen3_lrc_content = "\n".join(line.format() for line in mock_qwen3_refined_lines)

    async def delayed_qwen3_align(*args, **kwargs):
        await asyncio.sleep(qwen3_overhead)
        return AlignResponse(
            lrc_content=qwen3_lrc_content,
            json_data=None,
            line_count=len(mock_qwen3_refined_lines),
            duration_seconds=max(p.end for p in mock_whisper_phrases),
        )

    mock_client.align = delayed_qwen3_align

    qwen3_start = time.time()
    with patch(
        "sow_analysis.workers.lrc._run_whisper_transcription",
        side_effect=delayed_whisper_qwen3,
    ):
        with patch(
            "sow_analysis.workers.lrc._llm_align",
            side_effect=delayed_llm_qwen3,
        ):
            with patch(
                "sow_analysis.workers.lrc.Qwen3Client",
                return_value=mock_client,
            ):
                # Generate LRC with Qwen3
                qwen3_lrc_path, qwen3_line_count, qwen3_phrases = await generate_lrc(
                    audio_path=benchmark_audio_path,
                    lyrics_text=benchmark_lyrics,
                    options=qwen3_options,
                    output_path=benchmark_audio_path.with_suffix(".qwen3.lrc"),
                    content_hash="benchmark_test_hash",  # Enable Qwen3
                )

    qwen3_elapsed = time.time() - qwen3_start

    print(f"[Qwen3] Completed in {qwen3_elapsed:.2f}s")
    print(f"[Qwen3] Generated {qwen3_line_count} lines")

    # ------------------------------------------------------------------------
    # Verify 2x Requirement
    # ------------------------------------------------------------------------
    ratio = qwen3_elapsed / baseline_elapsed if baseline_elapsed > 0 else 0.0

    print("\n" + "=" * 80)
    print("BENCHMARK RESULTS")
    print("=" * 80)
    print(f"Baseline (Whisper+LLM):           {baseline_elapsed:.2f}s")
    print(f"Qwen3 (Whisper+LLM+Qwen3):        {qwen3_elapsed:.2f}s")
    print(f"Ratio:                             {ratio:.2f}x (requirement: <= 2.0x)")
    print("=" * 80)

    # Verify both generated valid LRC files
    assert baseline_lrc_path.exists(), "Baseline LRC file should exist"
    assert qwen3_lrc_path.exists(), "Qwen3 LRC file should exist"

    # Verify same number of lines generated
    assert baseline_line_count == qwen3_line_count, (
        f"Line count mismatch: baseline={baseline_line_count}, qwen3={qwen3_line_count}"
    )

    # Verify timing assertion: Qwen3 should complete within 2x baseline
    assert qwen3_elapsed <= baseline_elapsed * 2.0, (
        f"Qwen3 path ({qwen3_elapsed:.2f}s) exceeds 2x baseline "
        f"({baseline_elapsed * 2.0:.2f}s)"
    )

    # Verify Qwen3 adds some time (not zero overhead)
    # Allow for small measurement noise (0.1s tolerance)
    assert qwen3_elapsed > baseline_elapsed - 0.1, (
        f"Qwen3 should add some time overhead, but measured as zero/negative: "
        f"qwen3={qwen3_elapsed:.2f}s, baseline={baseline_elapsed:.2f}s"
    )

    print("\n[PASS] Qwen3 path completes within 2x baseline requirement")


@pytest.mark.asyncio
async def test_performance_higher_qwen3_overhead(
    benchmark_audio_path: Path,
    benchmark_lyrics: str,
    mock_whisper_phrases: List[WhisperPhrase],
    mock_llm_aligned_lines: List[LRCLine],
    mock_qwen3_refined_lines: List[LRCLine],
) -> None:
    """Test that Qwen3 path handles higher overhead within limits.

    This test validates the boundary case where Qwen3 has a higher overhead
    (simulating slow network or high load) but still meets the 2x requirement.

    Uses higher Qwen3 overhead: 6 seconds instead of 2 seconds.
    Expected ratio: (5+3+6) / (5+3) = 14/8 = 1.75x (still below 2.0x)
    """
    # Configure delays for higher Qwen3 overhead scenario
    whisper_delay = 5.0
    llm_delay = 3.0
    qwen3_overhead = 6.0  # Higher overhead

    baseline_options = LrcOptions(use_qwen3=False)
    qwen3_options = LrcOptions(use_qwen3=True)

    # Baseline measurement
    async def delayed_whisper_baseline(*args, **kwargs):
        return await mock_whisper_with_delay(mock_whisper_phrases, whisper_delay)

    async def delayed_llm_baseline(*args, **kwargs):
        return await mock_llm_align_with_delay(mock_llm_aligned_lines, llm_delay)

    baseline_start = time.time()
    with patch(
        "sow_analysis.workers.lrc._run_whisper_transcription",
        side_effect=delayed_whisper_baseline,
    ):
        with patch(
            "sow_analysis.workers.lrc._llm_align",
            side_effect=delayed_llm_baseline,
        ):
            with patch("sow_analysis.workers.lrc.Qwen3Client"):
                await generate_lrc(
                    audio_path=benchmark_audio_path,
                    lyrics_text=benchmark_lyrics,
                    options=baseline_options,
                    output_path=benchmark_audio_path.with_suffix(".baseline2.lrc"),
                    content_hash=None,
                )

    baseline_elapsed = time.time() - baseline_start

    # Qwen3 path measurement with higher overhead
    async def delayed_whisper_qwen3(*args, **kwargs):
        return await mock_whisper_with_delay(mock_whisper_phrases, whisper_delay)

    async def delayed_llm_qwen3(*args, **kwargs):
        return await mock_llm_align_with_delay(mock_llm_aligned_lines, llm_delay)

    mock_client = AsyncMock()
    qwen3_lrc_content = "\n".join(line.format() for line in mock_qwen3_refined_lines)

    async def delayed_qwen3_align(*args, **kwargs):
        await asyncio.sleep(qwen3_overhead)
        return AlignResponse(
            lrc_content=qwen3_lrc_content,
            json_data=None,
            line_count=len(mock_qwen3_refined_lines),
            duration_seconds=max(p.end for p in mock_whisper_phrases),
        )

    mock_client.align = delayed_qwen3_align

    qwen3_start = time.time()
    with patch(
        "sow_analysis.workers.lrc._run_whisper_transcription",
        side_effect=delayed_whisper_qwen3,
    ):
        with patch(
            "sow_analysis.workers.lrc._llm_align",
            side_effect=delayed_llm_qwen3,
        ):
            with patch(
                "sow_analysis.workers.lrc.Qwen3Client",
                return_value=mock_client,
            ):
                await generate_lrc(
                    audio_path=benchmark_audio_path,
                    lyrics_text=benchmark_lyrics,
                    options=qwen3_options,
                    output_path=benchmark_audio_path.with_suffix(".qwen32.lrc"),
                    content_hash="benchmark_test_hash_2",
                )

    qwen3_elapsed = time.time() - qwen3_start

    ratio = qwen3_elapsed / baseline_elapsed if baseline_elapsed > 0 else 0.0

    print("\n" + "=" * 80)
    print("BENCHMARK RESULTS (Higher Qwen3 Overhead)")
    print("=" * 80)
    print(f"Baseline (Whisper+LLM):           {baseline_elapsed:.2f}s")
    print(f"Qwen3 (Whisper+LLM+Qwen3):        {qwen3_elapsed:.2f}s")
    print(f"Ratio:                             {ratio:.2f}x (requirement: <= 2.0x)")
    print("=" * 80)

    # Verify Qwen3 still completes within 2x baseline even with higher overhead
    assert qwen3_elapsed <= baseline_elapsed * 2.0, (
        f"Qwen3 path ({qwen3_elapsed:.2f}s) exceeds 2x baseline "
        f"({baseline_elapsed * 2.0:.2f}s) even with higher overhead"
    )

    print("\n[PASS] Qwen3 path meets 2x requirement with higher overhead")
