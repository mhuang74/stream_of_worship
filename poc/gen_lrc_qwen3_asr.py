#!/usr/bin/env python3
"""Qwen3-ASR-Flash transcription POC script.

Uses Alibaba DashScope's Qwen3-ASR-Flash API for transcription with context biasing
and canonical-line fuzzy snap to produce LRC files.

This script follows the same conventions as gen_lrc_whisper.py for easy A/B testing.
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

app = typer.Typer(help="Qwen3-ASR-Flash transcription POC")

REGION_URL = {
    "intl": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
    "cn": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "us": "https://dashscope-us.aliyuncs.com/compatible-mode/v1",
}


def call_qwen3_asr(
    audio_path: Path,
    model: str = "qwen3-asr-flash",
    region: str = "intl",
    context: Optional[str] = None,
) -> dict:
    """Call Qwen3-ASR-Flash API.

    Args:
        audio_path: Path to audio file
        model: Model name (qwen3-asr-flash or qwen3-asr-flash-filetrans)
        region: Region (intl, cn, us)
        context: Optional context string for biasing

    Returns:
        Raw API response as dict
    """
    import dashscope

    dashscope.base_http_api_url = REGION_URL[region]

    messages = [
        {"role": "user", "content": [{"audio": f"file://{audio_path.resolve()}"}]},
    ]

    if context:
        messages.insert(0, {"role": "system", "content": [{"text": context}]})

    typer.echo(f"Calling Qwen3-ASR ({model}) in {region} region...", err=True)
    if context:
        typer.echo(f"Using context biasing ({len(context)} chars)", err=True)

    resp = dashscope.MultiModalConversation.call(
        api_key=os.environ["DASHSCOPE_API_KEY"],
        model=model,
        messages=messages,
        result_format="message",
        asr_options={"enable_itn": False, "enable_words": True, "language": "zh"},
    )

    if resp.status_code != 200:
        typer.echo(f"API error: {resp.status_code} - {resp.message}", err=True)
        raise typer.Exit(1)

    return resp.output


def extract_segments(response: dict) -> list[tuple[float, float, str]]:
    """Extract segments from Qwen3-ASR response.

    Args:
        response: Raw API response

    Returns:
        List of (start, end, text) tuples
    """
    segments = []

    try:
        content = response.get("choices", [{}])[0].get("message", {}).get("content", [])

        for item in content:
            if item.get("type") == "audio_transcription":
                sentences = item.get("audio_transcription_results", {}).get("sentences", [])
                for sentence in sentences:
                    start = sentence.get("begin_time", 0) / 1000.0  # Convert ms to seconds
                    end = sentence.get("end_time", 0) / 1000.0
                    text = sentence.get("text", "").strip()
                    if text:
                        segments.append((start, end, text))
    except Exception as e:
        typer.echo(f"Error parsing segments: {e}", err=True)
        typer.echo(f"Response keys: {list(response.keys())}", err=True)
        raise

    if not segments:
        typer.echo(f"Warning: No segments extracted from response", err=True)

    return segments


def canonical_line_snap(
    segments: list[tuple[float, float, str]],
    lyrics: list[str],
    threshold: float = 0.60,
) -> list[tuple[float, str, bool]]:
    """Snap ASR segments to canonical lyrics using fuzzy matching.

    Args:
        segments: List of (start, end, asr_text) tuples
        lyrics: List of canonical lyric lines
        threshold: Minimum fuzzy score to snap (0-1)

    Returns:
        List of (start, final_text, replaced) tuples
    """
    from rapidfuzz import fuzz

    canonical_lines = [l for l in lyrics if l.strip()]
    results = []

    for start, _end, asr_text in segments:
        scored = [(line, fuzz.token_set_ratio(asr_text, line) / 100.0) for line in canonical_lines]
        best_line, best_score = max(scored, key=lambda x: x[1])

        if best_score >= threshold:
            results.append((start, best_line, True))
        else:
            results.append((start, asr_text, False))

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
    segments: list[tuple[float, float, str]],
    lyrics: list[str],
    results: list[tuple[float, str, bool]],
    output_path: Path,
) -> None:
    """Write diagnostic markdown file.

    Args:
        segments: List of (start, end, asr_text) tuples
        lyrics: List of canonical lyric lines
        results: List of (start, final_text, replaced) tuples
        output_path: Path to write diagnostic.md
    """
    from rapidfuzz import fuzz

    canonical_lines = [l for l in lyrics if l.strip()]

    lines = []
    lines.append("# Qwen3-ASR Diagnostic Report\n")
    lines.append("## Summary\n\n")
    lines.append(f"ASR segments: {len(segments)}\n")
    lines.append(f"Canonical lines: {len(canonical_lines)}\n")
    lines.append(f"Output lines: {len(results)}\n")

    replaced_count = sum(1 for _, _, replaced in results if replaced)
    kept_count = len(results) - replaced_count
    lines.append(f"Replaced by snap: {replaced_count}\n")
    lines.append(f"Kept original: {kept_count}\n")

    # Calculate average score
    scores = []
    for (_, _, asr_text), (start, _, replaced) in zip(segments, results):
        scored = [(line, fuzz.token_set_ratio(asr_text, line) / 100.0) for line in canonical_lines]
        best_score = max(s[1] for s in scored)
        scores.append(best_score)

    if scores:
        avg_score = sum(scores) / len(scores)
        lines.append(f"Average snap score: {avg_score:.2f}\n")

    if segments:
        duration = segments[-1][1] - segments[0][0]
        lines.append(f"Audio duration: {duration:.2f}s\n")
        lines.append(f"Segments per second: {len(segments) / duration:.2f}\n")

    lines.append("\n## Segment Details\n\n")
    lines.append("| Start | End | ASR Text | Matched Canonical | Score | Replaced |\n")
    lines.append("|-------|-----|----------|-------------------|-------|----------|\n")

    from rapidfuzz import fuzz

    for (_, end, asr_text), (start, final_text, replaced) in zip(segments, results):
        scored = [(line, fuzz.token_set_ratio(asr_text, line) / 100.0) for line in canonical_lines]
        best_line, best_score = max(scored, key=lambda x: x[1])

        lines.append(
            f"| {start:6.2f} | {end:4.2f} | {asr_text[:30]:30s} | {best_line[:30]:30s} | {best_score:5.2f} | {'Yes' if replaced else 'No'} |\n"
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
    model: str = typer.Option(
        "qwen3-asr-flash", "--model", help="Model (qwen3-asr-flash or qwen3-asr-flash-filetrans)"
    ),
    region: str = typer.Option("intl", "--region", help="Region (intl, cn, us)"),
    snap: bool = typer.Option(True, "--snap/--no-snap", help="Enable canonical-line fuzzy snap"),
    snap_threshold: float = typer.Option(
        0.60, "--snap-threshold", help="Minimum fuzzy score to snap (0-1)"
    ),
    lyrics_context: bool = typer.Option(
        True, "--lyrics-context/--no-lyrics-context", help="Enable context biasing with lyrics"
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
):
    """Run Qwen3-ASR-Flash transcription on a song and output LRC format.

    By default, the entire song is transcribed with context biasing and
    canonical-line snap enabled. Use --no-snap for raw ASR output.
    """
    # Check API key
    if "DASHSCOPE_API_KEY" not in os.environ:
        typer.echo("Error: DASHSCOPE_API_KEY environment variable not set", err=True)
        raise typer.Exit(1)

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

    # Extract segment if needed
    transcribe_path = audio_path
    segment_path: Optional[Path] = None
    if start > 0 or effective_end is not None:
        typer.echo(f"Extracting audio segment: {start}s to {effective_end or 'end'}s", err=True)
        segment_path = extract_audio_segment(audio_path, start, effective_end or 3600)
        transcribe_path = segment_path

    try:
        # Build context
        context = None
        if lyrics_context:
            context = (
                "This is a Chinese Christian worship song. "
                "Use the following canonical lyrics as term/phrase reference for recognition. "
                "The performance may repeat verses and choruses; transcribe what is actually sung.\n\n"
                + lyrics_text
            )
            if len(context) > 10000:
                context = context[:10000]
                typer.echo("Context truncated to 10k chars", err=True)

        # Call ASR
        response = call_qwen3_asr(
            audio_path=transcribe_path,
            model=model,
            region=region,
            context=context,
        )

        # Save raw response if requested
        if save_raw:
            save_raw.mkdir(parents=True, exist_ok=True)
            raw_file = save_raw / "asr_raw.json"
            raw_file.write_text(json.dumps(response, ensure_ascii=False, indent=2))
            typer.echo(f"Saved raw ASR response to: {raw_file}", err=True)

        # Extract segments
        segments = extract_segments(response)

        if not segments:
            typer.echo("Error: No segments extracted from ASR response", err=True)
            raise typer.Exit(1)

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
                diag_file = save_raw / "diagnostic.md"
                write_diagnostic(segments, lyrics, results, diag_file)
                typer.echo(f"Saved diagnostic report to: {diag_file}", err=True)
        else:
            results = [(start, text, False) for start, _end, text in segments]
            typer.echo(f"Snap disabled, using raw ASR output", err=True)

        # Convert to LRC
        lrc_content = results_to_lrc(results)

        # Output
        if output:
            output.write_text(lrc_content, encoding="utf-8")
            typer.echo(f"Wrote LRC to: {output}", err=True)
        else:
            print(lrc_content)

    finally:
        # Clean up temp file
        if segment_path and segment_path.exists():
            segment_path.unlink()


if __name__ == "__main__":
    app()
