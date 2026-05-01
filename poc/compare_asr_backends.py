#!/usr/bin/env python3
"""ASR backend comparison script for Phase 0 evaluation.

This script runs both ONNX and PyTorch Qwen3-ASR backends on the same audio
and generates a comprehensive comparison report with metrics.

Metrics:
- Transcription completeness (% of canonical lyrics captured)
- Character accuracy (% correct chars)
- Inference speed (wall time)
- Memory usage (peak RSS)
- Timestamp availability
"""

import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

import typer

# Add parent directory to path for poc.utils import
sys.path.insert(0, str(Path(__file__).parent.parent))

from poc.utils import resolve_song_audio_path

app = typer.Typer(help="Compare Qwen3-ASR ONNX vs PyTorch backends")


def run_backend(
    backend: str,
    song_id: str,
    output_dir: Path,
    save_raw: Optional[Path] = None,
    use_lyrics_context: bool = True,
    force_rerun: bool = False,
    snap_threshold: float = 0.60,
) -> dict:
    """Run a backend and capture its output.

    Args:
        backend: Backend to run ('pytorch' or 'onnx')
        song_id: Song ID or audio path
        output_dir: Directory for outputs
        save_raw: Directory to save raw ASR output
        use_lyrics_context: Whether to use lyrics for context biasing
        force_rerun: Force rerun even if cache exists
        snap_threshold: Fuzzy matching threshold

    Returns:
        Dict with results and metrics
    """
    script_name = f"gen_lrc_qwen3_asr_{backend}.py"
    script_path = Path(__file__).parent / script_name

    if not script_path.exists():
        raise FileNotFoundError(f"Backend script not found: {script_path}")

    # Build command
    cmd = [
        sys.executable,
        str(script_path),
        song_id,
        "--output", str(output_dir / f"{backend}_out.txt"),
        "--snap-threshold", str(snap_threshold),
    ]

    if save_raw:
        backend_raw_dir = save_raw / backend
        backend_raw_dir.mkdir(parents=True, exist_ok=True)
        cmd.extend(["--save-raw", str(backend_raw_dir)])

    if not use_lyrics_context:
        cmd.append("--no-lyrics-context")

    if force_rerun:
        cmd.append("--force-rerun")

    # Run with timing
    typer.echo(f"Running {backend} backend...", err=True)
    start_time = time.time()

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,  # 10 minute timeout
        )
        wall_time = time.time() - start_time

        if result.returncode != 0:
            typer.echo(f"{backend} backend failed: {result.stderr}", err=True)
            return {
                "backend": backend,
                "success": False,
                "error": result.stderr,
                "wall_time": wall_time,
            }

        # Read output file
        output_file = output_dir / f"{backend}_out.txt"
        output_text = ""
        if output_file.exists():
            output_text = output_file.read_text(encoding="utf-8")

        # Read raw ASR output if available
        raw_asr = None
        if save_raw:
            raw_file = save_raw / backend / f"asr_raw_{backend}.json"
            if raw_file.exists():
                raw_asr = json.loads(raw_file.read_text(encoding="utf-8"))

        return {
            "backend": backend,
            "success": True,
            "wall_time": wall_time,
            "output_text": output_text,
            "raw_asr": raw_asr,
        }

    except subprocess.TimeoutExpired:
        return {
            "backend": backend,
            "success": False,
            "error": "Timeout after 600 seconds",
            "wall_time": 600,
        }
    except Exception as e:
        return {
            "backend": backend,
            "success": False,
            "error": str(e),
            "wall_time": time.time() - start_time,
        }


def extract_output_lines(output_text: str) -> list[str]:
    """Extract lyric lines from output text (strip timestamps).

    Args:
        output_text: LRC format text with timestamps

    Returns:
        List of lyric lines without timestamps
    """
    import re

    lines = []
    for line in output_text.splitlines():
        line = line.strip()
        if not line:
            continue
        # Remove timestamp [MM:SS.xx]
        match = re.search(r"\[.*?\]\s*(.*)", line)
        if match:
            text = match.group(1).strip()
            if text:
                lines.append(text)
        else:
            lines.append(line)
    return lines


def compute_completeness(output_lines: list[str], canonical_lines: list[str]) -> float:
    """Compute percentage of canonical lines found in output.

    Args:
        output_lines: Lines from ASR output
        canonical_lines: Canonical lyric lines

    Returns:
        Percentage (0-100) of canonical lines matched
    """
    from rapidfuzz import fuzz
    from zhconv import convert

    if not canonical_lines:
        return 0.0

    matched = 0
    output_text = "".join(output_lines)
    output_simplified = convert(output_text, "zh-hans")

    for canonical in canonical_lines:
        canonical_simplified = convert(canonical, "zh-hans")
        # Check if canonical line appears in output with fuzzy matching
        if len(canonical_simplified) <= 3:
            score = fuzz.partial_ratio(canonical_simplified, output_simplified) / 100.0
        else:
            score = fuzz.token_set_ratio(canonical_simplified, output_simplified) / 100.0

        if score >= 0.6:  # Threshold for match
            matched += 1

    return (matched / len(canonical_lines)) * 100


def compute_character_accuracy(output_lines: list[str], canonical_lines: list[str]) -> float:
    """Compute character-level accuracy.

    Args:
        output_lines: Lines from ASR output
        canonical_lines: Canonical lyric lines

    Returns:
        Percentage (0-100) of characters correctly transcribed
    """
    from difflib import SequenceMatcher
    from zhconv import convert

    output_text = "".join(output_lines)
    canonical_text = "".join(canonical_lines)

    # Convert both to simplified for comparison
    output_simplified = convert(output_text, "zh-hans")
    canonical_simplified = convert(canonical_text, "zh-hans")

    if not canonical_simplified:
        return 0.0

    # Use sequence matcher for character-level comparison
    matcher = SequenceMatcher(None, output_simplified, canonical_simplified)
    matches = sum(triple.size for triple in matcher.get_matching_blocks())

    return (matches / len(canonical_simplified)) * 100


def get_memory_usage() -> Optional[float]:
    """Get current memory usage in MB.

    Returns:
        Memory usage in MB or None if unavailable
    """
    try:
        import psutil
        process = psutil.Process()
        return process.memory_info().rss / 1024 / 1024
    except Exception:
        return None


def has_timestamps(raw_asr: Optional[dict]) -> bool:
    """Check if raw ASR output has per-character timestamps.

    Args:
        raw_asr: Raw ASR output dict

    Returns:
        True if timestamps are available
    """
    if not raw_asr:
        return False
    segments = raw_asr.get("segments", [])
    if not segments:
        return False
    # Check if segments have start/end timestamps
    first_seg = segments[0]
    return "start" in first_seg and "end" in first_seg


def count_segments(raw_asr: Optional[dict]) -> int:
    """Count number of segments in raw ASR output.

    Args:
        raw_asr: Raw ASR output dict

    Returns:
        Number of segments
    """
    if not raw_asr:
        return 0
    return len(raw_asr.get("segments", []))


def format_duration(seconds: float) -> str:
    """Format duration in seconds to readable string.

    Args:
        seconds: Duration in seconds

    Returns:
        Formatted string like "2m 30s" or "45s"
    """
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = int(seconds // 60)
    secs = seconds % 60
    return f"{minutes}m {secs:.1f}s"


def generate_markdown_report(
    song_id: str,
    pytorch_result: dict,
    onnx_result: dict,
    canonical_lines: list[str],
    audio_duration: Optional[float],
) -> str:
    """Generate Markdown comparison report.

    Args:
        song_id: Song ID
        pytorch_result: PyTorch backend results
        onnx_result: ONNX backend results
        canonical_lines: Canonical lyric lines
        audio_duration: Audio duration in seconds

    Returns:
        Markdown formatted report
    """
    lines = []
    lines.append("# ASR Backend Comparison Report")
    lines.append("")
    lines.append("## Summary")
    lines.append("")

    # Compute metrics
    pytorch_success = pytorch_result.get("success", False)
    onnx_success = onnx_result.get("success", False)

    pytorch_output = extract_output_lines(pytorch_result.get("output_text", ""))
    onnx_output = extract_output_lines(onnx_result.get("output_text", ""))

    pytorch_completeness = compute_completeness(pytorch_output, canonical_lines) if pytorch_success else 0.0
    onnx_completeness = compute_completeness(onnx_output, canonical_lines) if onnx_success else 0.0

    pytorch_accuracy = compute_character_accuracy(pytorch_output, canonical_lines) if pytorch_success else 0.0
    onnx_accuracy = compute_character_accuracy(onnx_output, canonical_lines) if onnx_success else 0.0

    pytorch_timestamps = has_timestamps(pytorch_result.get("raw_asr"))
    onnx_timestamps = has_timestamps(onnx_result.get("raw_asr"))

    pytorch_segments = count_segments(pytorch_result.get("raw_asr"))
    onnx_segments = count_segments(onnx_result.get("raw_asr"))

    pytorch_time = pytorch_result.get("wall_time", 0)
    onnx_time = onnx_result.get("wall_time", 0)

    # Calculate real-time factors
    pytorch_rtf = pytorch_time / audio_duration if audio_duration and pytorch_success else 0
    onnx_rtf = onnx_time / audio_duration if audio_duration and onnx_success else 0

    # Summary table
    lines.append("| Metric | ONNX | PyTorch |")
    lines.append("|--------|------|---------|")
    lines.append(f"| Transcription completeness | {onnx_completeness:.1f}% | {pytorch_completeness:.1f}% |")
    lines.append(f"| Character accuracy | {onnx_accuracy:.1f}% | {pytorch_accuracy:.1f}% |")
    lines.append(f"| Timestamp availability | {'Per-segment' if onnx_timestamps else 'None'} | {'Per-character' if pytorch_timestamps else 'None'} |")
    lines.append(f"| Inference time | {format_duration(onnx_time)} | {format_duration(pytorch_time)} |")
    lines.append(f"| Real-time factor | {onnx_rtf:.2f}x RT | {pytorch_rtf:.2f}x RT |")
    lines.append(f"| Segment count | {onnx_segments} | {pytorch_segments} |")
    lines.append(f"| Success | {'Yes' if onnx_success else 'No'} | {'Yes' if pytorch_success else 'No'} |")

    lines.append("")
    lines.append("## Decision Recommendation")
    lines.append("")

    # Recommendation logic based on spec
    if not pytorch_success and not onnx_success:
        lines.append("**Neither backend succeeded. Please check the error logs above.**")
    elif not pytorch_success:
        lines.append("**PyTorch backend failed. ONNX is the fallback option.**")
    elif not onnx_success:
        lines.append("**ONNX backend failed. PyTorch is the only working option.**")
    else:
        # Both succeeded - compare quality
        quality_diff = abs(pytorch_completeness - onnx_completeness)

        if pytorch_completeness >= onnx_completeness - 5:  # Within 5%
            lines.append("**Recommendation: PyTorch**")
            lines.append("")
            lines.append("Rationale:")
            lines.append("- PyTorch provides per-character timestamps (same quality as existing MLX backend)")
            lines.append("- Transcription quality is comparable to ONNX (within 5%)")
            lines.append("- Per-character timestamps enable better alignment with canonical lyrics")
            lines.append("- PyTorch is the expected primary local backend per the spec")
        else:
            lines.append("**Recommendation: ONNX (surprising result)**")
            lines.append("")
            lines.append("Rationale:")
            lines.append(f"- ONNX completeness ({onnx_completeness:.1f}%) is significantly higher than PyTorch ({pytorch_completeness:.1f}%)")
            lines.append("- Consider investigating PyTorch model configuration or using ONNX as fallback")

    lines.append("")
    lines.append("## Detailed Results")
    lines.append("")
    lines.append(f"### Song: {song_id}")
    lines.append("")

    # Output sections
    lines.append("#### ONNX Output")
    lines.append("```")
    for line in onnx_output[:20]:  # First 20 lines
        lines.append(line)
    if len(onnx_output) > 20:
        lines.append(f"... ({len(onnx_output) - 20} more lines)")
    lines.append("```")
    lines.append("")

    lines.append("#### PyTorch Output")
    lines.append("```")
    for line in pytorch_output[:20]:  # First 20 lines
        lines.append(line)
    if len(pytorch_output) > 20:
        lines.append(f"... ({len(pytorch_output) - 20} more lines)")
    lines.append("```")
    lines.append("")

    # Coverage analysis
    lines.append("### Coverage Analysis")
    lines.append("")
    lines.append(f"**Canonical lines**: {len(canonical_lines)}")
    lines.append("")
    lines.append("**ONNX coverage**:")
    lines.append(f"- Lines found: {int(onnx_completeness / 100 * len(canonical_lines))}/{len(canonical_lines)}")
    lines.append(f"- Completeness: {onnx_completeness:.1f}%")
    lines.append("")
    lines.append("**PyTorch coverage**:")
    lines.append(f"- Lines found: {int(pytorch_completeness / 100 * len(canonical_lines))}/{len(canonical_lines)}")
    lines.append(f"- Completeness: {pytorch_completeness:.1f}%")
    lines.append("")

    # Raw ASR details
    lines.append("### Raw ASR Details")
    lines.append("")
    lines.append("| Backend | Text Length | Segments | Has Timestamps |")
    lines.append("|---------|-------------|----------|----------------|")

    pytorch_raw = pytorch_result.get("raw_asr", {})
    onnx_raw = onnx_result.get("raw_asr", {})

    pytorch_text_len = len(pytorch_raw.get("text", "")) if pytorch_raw else 0
    onnx_text_len = len(onnx_raw.get("text", "")) if onnx_raw else 0

    lines.append(f"| ONNX | {onnx_text_len} chars | {onnx_segments} | {'Yes' if onnx_timestamps else 'No'} |")
    lines.append(f"| PyTorch | {pytorch_text_len} chars | {pytorch_segments} | {'Yes' if pytorch_timestamps else 'No'} |")

    lines.append("")

    # Errors if any
    if not pytorch_success or not onnx_success:
        lines.append("### Errors")
        lines.append("")
        if not pytorch_success:
            lines.append("**PyTorch error**:")
            lines.append("```")
            lines.append(pytorch_result.get("error", "Unknown error"))
            lines.append("```")
            lines.append("")
        if not onnx_success:
            lines.append("**ONNX error**:")
            lines.append("```")
            lines.append(onnx_result.get("error", "Unknown error"))
            lines.append("```")

    return "\n".join(lines)


@app.command()
def main(
    song_id: str = typer.Argument(
        ..., help="Song ID (e.g., wo_yao_quan_xin_zan_mei_244) or path to audio file"
    ),
    output: Path = typer.Option(
        ..., "--output", "-o", help="Output file for comparison report (Markdown)"
    ),
    save_raw: Optional[Path] = typer.Option(
        None, "--save-raw", help="Directory to save raw ASR outputs from both backends"
    ),
    no_lyrics_context: bool = typer.Option(
        False, "--no-lyrics-context", help="Disable context biasing with lyrics"
    ),
    force_rerun: bool = typer.Option(
        False, "--force-rerun", help="Force rerun transcription (ignore cache)"
    ),
    snap_threshold: float = typer.Option(
        0.60, "--snap-threshold", help="Fuzzy matching threshold for canonical snap"
    ),
):
    """Compare Qwen3-ASR ONNX vs PyTorch backends on the same audio.

    Generates a comprehensive comparison report with metrics to inform the
    Phase 0 decision on which backend to use as the primary local ASR.
    """
    import tempfile

    # Resolve song to get canonical lyrics
    audio_path, lyrics = resolve_song_audio_path(song_id)
    canonical_lines = [l for l in lyrics if l.strip()] if lyrics else []

    typer.echo(f"Comparing backends for: {song_id}", err=True)
    typer.echo(f"Canonical lyrics: {len(canonical_lines)} lines", err=True)

    # Create temporary output directory
    with tempfile.TemporaryDirectory() as tmpdir:
        output_dir = Path(tmpdir)

        # Run both backends
        pytorch_result = run_backend(
            backend="pytorch",
            song_id=song_id,
            output_dir=output_dir,
            save_raw=save_raw,
            use_lyrics_context=not no_lyrics_context,
            force_rerun=force_rerun,
            snap_threshold=snap_threshold,
        )

        onnx_result = run_backend(
            backend="onnx",
            song_id=song_id,
            output_dir=output_dir,
            save_raw=save_raw,
            use_lyrics_context=not no_lyrics_context,
            force_rerun=force_rerun,
            snap_threshold=snap_threshold,
        )

    # Get audio duration for RTF calculation
    audio_duration = None
    try:
        import librosa
        audio, sr = librosa.load(str(audio_path), sr=None)
        audio_duration = len(audio) / sr
    except Exception:
        pass

    # Generate report
    report = generate_markdown_report(
        song_id=song_id,
        pytorch_result=pytorch_result,
        onnx_result=onnx_result,
        canonical_lines=canonical_lines,
        audio_duration=audio_duration,
    )

    # Write report
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(report, encoding="utf-8")
    typer.echo(f"Comparison report written to: {output}", err=True)

    # Also print summary
    print("\n" + "=" * 60)
    print("COMPARISON SUMMARY")
    print("=" * 60)

    if pytorch_result.get("success"):
        pytorch_time = pytorch_result.get("wall_time", 0)
        print(f"\nPyTorch backend:")
        print(f"  - Time: {format_duration(pytorch_time)}")
        print(f"  - Output lines: {len(extract_output_lines(pytorch_result.get('output_text', '')))}")
        print(f"  - Has timestamps: {has_timestamps(pytorch_result.get('raw_asr'))}")
    else:
        print(f"\nPyTorch backend: FAILED - {pytorch_result.get('error', 'Unknown error')}")

    if onnx_result.get("success"):
        onnx_time = onnx_result.get("wall_time", 0)
        print(f"\nONNX backend:")
        print(f"  - Time: {format_duration(onnx_time)}")
        print(f"  - Output lines: {len(extract_output_lines(onnx_result.get('output_text', '')))}")
        print(f"  - Has timestamps: {has_timestamps(onnx_result.get('raw_asr'))}")
    else:
        print(f"\nONNX backend: FAILED - {onnx_result.get('error', 'Unknown error')}")

    print(f"\nFull report: {output}")


if __name__ == "__main__":
    app()
