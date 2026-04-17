#!/usr/bin/env python3
"""Qwen3-ASR local MLX transcription POC script.

Uses mlx-qwen3-asr or mlx-audio for local transcription on Apple Silicon
with context biasing and canonical-line fuzzy snap to produce LRC files.

This script mirrors the cloud variant but runs entirely locally.
"""

import json
import os
import sys
from pathlib import Path
from typing import Optional

import typer

# Add parent directory to path for poc.utils import
sys.path.insert(0, str(Path(__file__).parent.parent))

# Import shared utilities
from poc.utils import extract_audio_segment, format_timestamp, resolve_song_audio_path

app = typer.Typer(help="Qwen3-ASR local MLX transcription POC")


def transcribe_mlx_qwen3_asr(
    audio_path: Path,
    model: str = "1.7B",
    context: Optional[str] = None,
) -> dict:
    """Run transcription using mlx-qwen3-asr backend.

    Args:
        audio_path: Path to audio file
        model: Model size (0.6B or 1.7B)
        context: Optional context string for biasing

    Returns:
        Raw transcription result as dict
    """
    from mlx_qwen3_asr import Session

    model_name = f"Qwen/Qwen3-ASR-{model}"
    typer.echo(f"Loading mlx-qwen3-asr ({model_name})...", err=True)

    session = Session(model=model_name)

    typer.echo(f"Transcribing: {audio_path}", err=True)
    if context:
        typer.echo(f"Using context biasing ({len(context)} chars)", err=True)

    result = session.transcribe(
        str(audio_path),
        context=context,
        language="Chinese",
        return_timestamps=True,
    )

    return result


def transcribe_mlx_audio(
    audio_path: Path,
    model: str = "1.7B",
) -> dict:
    """Run transcription using mlx-audio backend.

    Args:
        audio_path: Path to audio file
        model: Model size (0.6B or 1.7B)

    Returns:
        Raw transcription result as dict
    """
    from mlx_audio.stt import load

    model_name = f"mlx-community/Qwen3-ASR-{model}-8bit"
    typer.echo(f"Loading mlx-audio ({model_name})...", err=True)

    session = load(model_name)

    typer.echo(f"Transcribing: {audio_path}", err=True)

    result = session.generate(str(audio_path), language="Chinese")

    return result


def extract_segments(result) -> list[dict]:
    """Extract segments from MLX output.

    Args:
        result: Raw MLX output (TranscriptionResult object or dict)

    Returns:
        List of segment dicts with 'start', 'end', 'text' keys
    """
    segments = []

    try:
        if hasattr(result, "segments"):
            for seg in result.segments:
                segments.append(
                    {
                        "start": getattr(seg, "start", 0),
                        "end": getattr(seg, "end", 0),
                        "text": getattr(seg, "text", "").strip(),
                    }
                )
        elif isinstance(result, dict):
            raw_segments = result.get("segments", [])
            for seg in raw_segments:
                segments.append(
                    {
                        "start": seg.get("start", 0),
                        "end": seg.get("end", 0),
                        "text": seg.get("text", "").strip(),
                    }
                )
    except Exception as e:
        typer.echo(f"Error parsing segments: {e}", err=True)
        typer.echo(f"Result type: {type(result)}", err=True)
        if hasattr(result, "__dict__"):
            typer.echo(f"Result attributes: {list(result.__dict__.keys())}", err=True)
        if isinstance(result, dict):
            typer.echo(f"Result keys: {list(result.keys())}", err=True)
        raise

    if not segments:
        typer.echo(f"Warning: No segments extracted from result", err=True)

    return segments


def cache_file_name(
    cache_dir: Path,
    song_id: str,
    model: str,
    backend: str,
) -> Path:
    """Generate cache file name for transcription.

    Args:
        cache_dir: Cache directory
        song_id: Song identifier
        model: Model size (0.6B or 1.7B)
        backend: MLX backend name

    Returns:
        Path to cache file
    """
    safe_song_id = song_id.replace("/", "_").replace("\\", "_")
    filename = f"{safe_song_id}_{model}_{backend}_transcription.json"
    return cache_dir / filename


def load_cached_transcription(cache_path: Path) -> Optional[list[dict]]:
    """Load cached transcription segments.

    Args:
        cache_path: Path to cache file

    Returns:
        List of segment dicts, or None if cache file not found/invalid
    """
    if not cache_path.exists():
        return None

    try:
        cache_data = json.loads(cache_path.read_text(encoding="utf-8"))
        segments = cache_data.get("segments", [])

        if not segments:
            return None

        # Validate segments have required fields
        for seg in segments:
            if not all(k in seg for k in ("start", "end", "text")):
                typer.echo("Warning: Cache file has invalid segment structure, ignoring", err=True)
                return None

        typer.echo(f"Loaded {len(segments)} segments from cache: {cache_path}", err=True)
        return segments
    except Exception as e:
        typer.echo(f"Warning: Cache file invalid, ignoring: {e}", err=True)
        return None


def save_cached_transcription(
    cache_path: Path,
    segments: list[dict],
    model: str,
    backend: str,
    wall_time: float,
) -> None:
    """Save transcription segments to cache.

    Args:
        cache_path: Path to cache file
        segments: List of segment dicts
        model: Model size used
        backend: Backend used
        wall_time: Wall-clock time taken
    """
    cache_path.parent.mkdir(parents=True, exist_ok=True)

    cache_data = {
        "model": model,
        "backend": backend,
        "wall_time": wall_time,
        "timestamp": __import__("time").time(),
        "segments": segments,
    }

    cache_path.write_text(json.dumps(cache_data, ensure_ascii=False, indent=2), encoding="utf-8")
    typer.echo(f"Saved transcription to cache: {cache_path}", err=True)


def detect_chinese_script(text: str) -> str:
    """Detect whether Chinese text is traditional or simplified.

    Args:
        text: Chinese text to analyze

    Returns:
        "zh-hans" if simplified, "zh-hant" if traditional
    """
    from zhconv import convert

    total_chars = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    if total_chars == 0:
        return "zh-hans"

    simplified = convert(text, "zh-hans")
    if simplified == text:
        return "zh-hans"
    else:
        return "zh-hant"


def canonical_line_snap(
    segments: list[dict],
    lyrics: list[str],
    threshold: float = 0.60,
) -> list[tuple[float, str, bool]]:
    """Snap ASR segments to canonical lyrics using fuzzy matching.

    Automatically detects script (traditional/simplified) of canonical lyrics
    and normalizes ASR output to match for scoring, but keeps original form
    for output.

    Args:
        segments: List of dicts with 'start', 'end', 'text' keys
        lyrics: List of canonical lyric lines
        threshold: Minimum fuzzy score to snap (0-1)

    Returns:
        List of (start, final_text, replaced) tuples
    """
    from rapidfuzz import fuzz
    from zhconv import convert

    canonical_lines = [l for l in lyrics if l.strip()]
    results = []

    if not canonical_lines:
        return results

    sample_text = "".join(canonical_lines)
    target_script = detect_chinese_script(sample_text)

    canonical_lines_normalized = [convert(l, target_script) for l in canonical_lines]

    for seg in segments:
        asr_text = seg["text"]
        asr_normalized = convert(asr_text, target_script)
        scored = [
            (
                canonical_lines[i],
                fuzz.token_set_ratio(asr_normalized, canonical_lines_normalized[i]) / 100.0,
            )
            for i in range(len(canonical_lines))
        ]
        best_line, best_score = max(scored, key=lambda x: x[1])

        if best_score >= threshold:
            results.append((seg["start"], best_line, True))
        else:
            results.append((seg["start"], asr_text, False))

    return results


def results_to_lrc(results: list[tuple[float, str, bool]]) -> str:
    """Convert results to LRC format.

    Args:
        results: List of (start, text, replaced) tuples

    Returns:
        LRC format string
    """
    lines = []
    for start, text, _replaced in results:
        timestamp = format_timestamp(start)
        lines.append(f"{timestamp} {text}")
    return "\n".join(lines)


def write_diagnostic(
    segments: list[dict],
    lyrics: list[str],
    results: list[tuple[float, str, bool]],
    output_path: Path,
    wall_time: float,
) -> None:
    """Write diagnostic markdown file.

    Args:
        segments: List of segment dicts with 'start', 'end', 'text' keys
        lyrics: List of canonical lyric lines
        results: List of (start, final_text, replaced) tuples
        output_path: Path to write diagnostic.md
        wall_time: Wall-clock elapsed time in seconds
    """
    from rapidfuzz import fuzz
    from zhconv import convert

    canonical_lines = [l for l in lyrics if l.strip()]

    lines = []
    lines.append("# Qwen3-ASR Local MLX Diagnostic Report\n")
    lines.append("## Summary\n\n")
    lines.append(f"ASR segments: {len(segments)}\n")
    lines.append(f"Canonical lines: {len(canonical_lines)}\n")
    lines.append(f"Output lines: {len(results)}\n")

    replaced_count = sum(1 for _, _, replaced in results if replaced)
    kept_count = len(results) - replaced_count
    lines.append(f"Replaced by snap: {replaced_count}\n")
    lines.append(f"Kept original: {kept_count}\n")

    # Detect target script for scoring
    sample_text = "".join(canonical_lines)
    target_script = detect_chinese_script(sample_text) if sample_text else "zh-hans"
    canonical_lines_normalized = [convert(l, target_script) for l in canonical_lines]

    # Calculate average score
    scores = []
    for seg, results_item in zip(segments, results):
        asr_text = seg["text"]
        asr_normalized = convert(asr_text, target_script)
        scored = [
            fuzz.token_set_ratio(asr_normalized, canonical_lines_normalized[i]) / 100.0
            for i in range(len(canonical_lines))
        ]
        best_score = max(scored)
        scores.append(best_score)

    if scores:
        avg_score = sum(scores) / len(scores)
        lines.append(f"Average snap score: {avg_score:.2f}\n")

    if segments:
        duration = segments[-1]["end"] - segments[0]["start"]
        lines.append(f"Audio duration: {duration:.2f}s\n")
        if duration > 0:
            lines.append(f"Segments per second: {len(segments) / duration:.2f}\n")
            lines.append(f"Wall-clock time: {wall_time:.2f}s\n")
            lines.append(f"Real-time factor: {wall_time / duration:.2f}x\n")
        else:
            lines.append("Warning: Invalid duration (0 or negative)\n")

    # Get RAM peak if available
    try:
        import psutil

        process = psutil.Process()
        memory_mb = process.memory_info().rss / 1024 / 1024
        lines.append(f"Peak RAM usage: ~{memory_mb:.1f} MB\n")
    except Exception:
        pass

    lines.append("\n## Segment Details\n\n")
    lines.append("| Start | End | ASR Text | Matched Canonical | Score | Replaced |\n")
    lines.append("|-------|-----|----------|-------------------|-------|----------|\n")

    for seg, (start, final_text, replaced) in zip(segments, results):
        asr_text = seg["text"]
        asr_normalized = convert(asr_text, target_script)
        scored = [
            (
                canonical_lines[i],
                fuzz.token_set_ratio(asr_normalized, canonical_lines_normalized[i]) / 100.0,
            )
            for i in range(len(canonical_lines))
        ]
        best_line, best_score = max(scored, key=lambda x: x[1])

        lines.append(
            f"| {seg['start']:6.2f} | {seg['end']:4.2f} | {asr_text[:30]:30s} | {best_line[:30]:30s} | {best_score:5.2f} | {'Yes' if replaced else 'No'} |\n"
        )

    output_path.write_text("".join(lines))


@app.command()
def main(
    song_id: str = typer.Argument(
        ..., help="Song ID (e.g., wo_yao_quan_xin_zan_mei_244) or path to audio file"
    ),
    use_vocals: bool = typer.Option(
        True, "--use-vocals/--no-use-vocals", help="Use vocals stem if available"
    ),
    output: Optional[Path] = typer.Option(
        None, "--output", "-o", help="Output file (default: stdout)"
    ),
    model: str = typer.Option("1.7B", "--model", help="Model size (0.6B or 1.7B)"),
    backend: str = typer.Option(
        "mlx-qwen3-asr", "--backend", help="MLX backend (mlx-qwen3-asr or mlx-audio)"
    ),
    snap: bool = typer.Option(True, "--snap/--no-snap", help="Enable canonical-line fuzzy snap"),
    snap_threshold: float = typer.Option(
        0.60, "--snap-threshold", help="Minimum fuzzy score to snap (0-1)"
    ),
    lyrics_context: bool = typer.Option(
        True, "--lyrics-context/--no-lyrics-context", help="Enable context biasing with lyrics"
    ),
    context_max_chars: int = typer.Option(
        2000, "--context-max-chars", help="Max chars for context (2000 default)"
    ),
    save_raw: Optional[Path] = typer.Option(
        None, "--save-raw", help="Directory to save raw ASR response + diagnostics"
    ),
    start: float = typer.Option(
        0.0, "--start", "-s", help="Start timestamp in seconds (default: 0)"
    ),
    end: Optional[float] = typer.Option(
        None, "--end", "-e", help="End timestamp in seconds (default: full song)"
    ),
    cache_dir: Optional[Path] = typer.Option(
        None,
        "--cache-dir",
        help="Directory for transcription cache (default: ~/.cache/qwen3_asr)",
    ),
    reuse_transcription: bool = typer.Option(
        True,
        "--reuse-transcription/--no-reuse-transcription",
        help="Reuse cached transcription if available",
    ),
    force_rerun: bool = typer.Option(
        False, "--force-rerun", help="Ignore cache and rerun transcription"
    ),
):
    """Run Qwen3-ASR local MLX transcription on a song and output LRC format.

    By default, transcription uses mlx-qwen3-asr with context biasing and
    canonical-line snap enabled. Use --backend mlx-audio for 8-bit quantized
    but no context support.

    Transcription results are cached and reused by default. Use --force-rerun
    to ignore the cache.
    """
    # Validate backend
    if backend not in ("mlx-qwen3-asr", "mlx-audio"):
        typer.echo(
            f"Error: Invalid backend '{backend}'. Use 'mlx-qwen3-asr' or 'mlx-audio'.", err=True
        )
        raise typer.Exit(1)

    # Validate model
    if model not in ("0.6B", "1.7B"):
        typer.echo(f"Error: Invalid model '{model}'. Use '0.6B' or '1.7B'.", err=True)
        raise typer.Exit(1)

    # Warn if context requested but using mlx-audio backend
    if lyrics_context and backend == "mlx-audio":
        typer.echo(
            "Warning: mlx-audio backend does not support context biasing. "
            "Use --backend mlx-qwen3-asr for context support.",
            err=True,
        )

    # Resolve inputs
    audio_path, lyrics = resolve_song_audio_path(song_id, use_vocals=use_vocals)

    if lyrics is None:
        typer.echo("Error: No lyrics from catalog; cannot run biasing/snap.", err=True)
        raise typer.Exit(1)

    lyrics_text = "\n".join(lyrics)

    # Determine time range
    effective_end: Optional[float] = end if end and end > 0 else None
    if effective_end:
        typer.echo(f"Transcribing segment: {start}s to {effective_end}s", err=True)
    elif start > 0:
        typer.echo(f"Transcribing from {start}s to end", err=True)
    else:
        typer.echo("Transcribing full song", err=True)

    # Set up cache directory
    if cache_dir is None:
        cache_dir = Path.home() / ".cache" / "qwen3_asr"
    cache_path = cache_file_name(cache_dir, song_id, model, backend)

    segments: list[dict] = []
    wall_time = 0.0

    # Check for cached transcription
    used_cache = False
    if reuse_transcription and not force_rerun:
        cached_segments = load_cached_transcription(cache_path)
        if cached_segments is not None:
            segments = cached_segments
            used_cache = True
            typer.echo("Using cached transcription", err=True)

    # Run transcription if not using cache
    if not used_cache:
        # Extract segment if needed
        transcribe_path = audio_path
        segment_path: Optional[Path] = None
        if start > 0 or effective_end is not None:
            typer.echo(f"Extracting audio segment: {start}s to {effective_end or 'end'}s", err=True)
            segment_path = extract_audio_segment(audio_path, start, effective_end or 3600)
            transcribe_path = segment_path

        import time

        wall_time_start = time.time()

        try:
            # Build context
            context = None
            if lyrics_context and backend == "mlx-qwen3-asr":
                context = lyrics_text
                if len(context) > context_max_chars:
                    context = context[:context_max_chars]
                    typer.echo(f"Context truncated to {context_max_chars} chars", err=True)

            # Transcribe
            if backend == "mlx-qwen3-asr":
                result = transcribe_mlx_qwen3_asr(
                    audio_path=transcribe_path,
                    model=model,
                    context=context,
                )
            else:  # mlx-audio
                result = transcribe_mlx_audio(
                    audio_path=transcribe_path,
                    model=model,
                )

            wall_time = time.time() - wall_time_start
            typer.echo(f"Transcription completed in {wall_time:.2f}s", err=True)

            # Save raw result if requested
            if save_raw:
                save_raw.mkdir(parents=True, exist_ok=True)
                raw_file = save_raw / "asr_raw.json"
                result_dict = {}
                if hasattr(result, "__dict__"):
                    result_dict = result.__dict__
                elif isinstance(result, dict):
                    result_dict = result
                raw_file.write_text(
                    json.dumps(result_dict, ensure_ascii=False, indent=2, default=str)
                )
                typer.echo(f"Saved raw ASR result to: {raw_file}", err=True)

            segments = extract_segments(result)

            if not segments:
                typer.echo("Error: No segments extracted from ASR result", err=True)
                raise typer.Exit(1)

            typer.echo(f"Extracted {len(segments)} segments", err=True)

            # Save to cache
            save_cached_transcription(cache_path, segments, model, backend, wall_time)

        finally:
            if segment_path and segment_path.exists():
                segment_path.unlink()

        typer.echo(f"Saved transcription to cache for reuse", err=True)

    if not segments:
        typer.echo("Error: No segments available", err=True)
        raise typer.Exit(1)

    if not used_cache:
        typer.echo(f"Extracted {len(segments)} segments", err=True)

    # Process segments
    if snap:
        results = canonical_line_snap(segments, lyrics, threshold=snap_threshold)
        replaced_count = sum(1 for _, _, replaced in results if replaced)
        typer.echo(
            f"Canonical-line snap: {replaced_count}/{len(results)} segments replaced", err=True
        )

        # Write diagnostic if requested
        if save_raw:
            save_raw.mkdir(parents=True, exist_ok=True)
            diag_file = save_raw / "diagnostic.md"
            write_diagnostic(segments, lyrics, results, diag_file, wall_time)
            typer.echo(f"Saved diagnostic report to: {diag_file}", err=True)
    else:
        results = [(seg["start"], seg["text"], False) for seg in segments]
        typer.echo(f"Snap disabled, using raw ASR output", err=True)

    # Convert to LRC
    lrc_content = results_to_lrc(results)

    # Output
    if output:
        output.write_text(lrc_content, encoding="utf-8")
        typer.echo(f"Wrote LRC to: {output}", err=True)
    else:
        print(lrc_content)


if __name__ == "__main__":
    app()
