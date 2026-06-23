#!/usr/bin/env python3
"""WhisperX transcription test driver for quick experimentation.

Runs WhisperX transcription with optional forced alignment on a song from the local cache,
enabling quick experimentation to diagnose transcription accuracy issues.
"""

import tempfile
import time
from pathlib import Path
from typing import Optional

import typer
from pydub import AudioSegment

# Import shared utilities
from poc.utils import extract_audio_segment, format_timestamp, resolve_song_audio_path

app = typer.Typer(help="WhisperX transcription test driver with optional alignment")


def transcribe_audio(
    audio_path: Path,
    model_name: str = "large-v2",
    language: str = "zh",
    device: str = "cpu",
    compute_type: str = "int8",
    batch_size: int = 16,
    start_seconds: float = 0.0,
    end_seconds: Optional[float] = None,
    align: bool = True,
    vad: str = "silero",
) -> list[tuple[float, float, str]]:
    """Run WhisperX transcription on audio file with optional alignment.

    Args:
        audio_path: Path to audio file
        model_name: Whisper model name
        language: Language code
        device: Device to run on ("cpu" or "cuda")
        compute_type: Compute type ("int8", "float16", "float32")
        batch_size: Batch size for inference
        start_seconds: Start time offset (for segment extraction)
        end_seconds: End time offset (for segment extraction, None for full)
        align: Whether to run wav2vec2 forced alignment
        vad: VAD model to use ("silero", "pyannote", or "none")

    Returns:
        List of (start_time, end_time, text) tuples
    """
    import whisperx

    # Extract segment if needed
    segment_path: Optional[Path] = None
    if start_seconds > 0 or end_seconds is not None:
        typer.echo(
            f"Extracting audio segment: {start_seconds}s to {end_seconds or 'end'}s", err=True
        )
        segment_path = extract_audio_segment(audio_path, start_seconds, end_seconds or 3600)
        transcribe_path = segment_path
    else:
        transcribe_path = audio_path

    try:
        # Load model
        typer.echo(
            f"Loading WhisperX model: {model_name} on {device} with {compute_type}", err=True
        )
        model_load_start = time.time()

        # Configure VAD
        # WhisperX supports vad_method="pyannote" or "silero"
        # Pyannote requires HuggingFace auth, Silero works without auth
        vad_method = vad if vad in ("pyannote", "silero") else "silero"

        model = whisperx.load_model(
            model_name,
            device=device,
            compute_type=compute_type,
            language=language,
            vad_method=vad_method,
        )
        model_load_elapsed = time.time() - model_load_start
        typer.echo(f"Model loaded in {model_load_elapsed:.2f}s", err=True)

        # Load audio
        typer.echo(f"Loading audio: {transcribe_path}", err=True)
        audio = whisperx.load_audio(str(transcribe_path))

        # Transcribe with batched inference
        typer.echo(f"Running transcription (batch_size={batch_size}, vad={vad})...", err=True)
        transcribe_start = time.time()

        result = model.transcribe(audio, batch_size=batch_size, language=language)
        transcribe_elapsed = time.time() - transcribe_start
        typer.echo(f"Transcription completed in {transcribe_elapsed:.2f}s", err=True)

        # Optional: Run forced alignment for more precise timestamps
        if align:
            typer.echo("Loading alignment model...", err=True)
            align_model, metadata = whisperx.load_align_model(
                language_code=language, device=device
            )
            typer.echo("Running forced alignment...", err=True)
            align_start = time.time()
            result = whisperx.align(
                result["segments"], align_model, metadata, audio, device
            )
            align_elapsed = time.time() - align_start
            typer.echo(f"Alignment completed in {align_elapsed:.2f}s", err=True)

        # Extract segments
        phrases = []
        for segment in result["segments"]:
            text = segment["text"].strip()
            if text:
                # Adjust timestamps by start offset
                adjusted_start = segment["start"] + start_seconds
                adjusted_end = segment["end"] + start_seconds
                phrases.append((adjusted_start, adjusted_end, text))

        return phrases
    finally:
        # Clean up temp file
        if segment_path and segment_path.exists():
            segment_path.unlink()


def phrases_to_lrc(phrases: list[tuple[float, float, str]]) -> str:
    """Convert phrases to LRC format.

    Args:
        phrases: List of (start, end, text) tuples

    Returns:
        LRC format string
    """
    lines = []
    for start, _end, text in phrases:
        timestamp = format_timestamp(start)
        lines.append(f"{timestamp} {text}")
    return "\n".join(lines)


def phrases_to_plain(phrases: list[tuple[float, float, str]]) -> str:
    """Convert phrases to plain text (no timestamps).

    Args:
        phrases: List of (start, end, text) tuples

    Returns:
        Plain text string
    """
    lines = []
    for _start, _end, text in phrases:
        lines.append(text)
    return "\n".join(lines)


@app.command()
def main(
    song_id: str = typer.Argument(
        ..., help="Song ID (e.g., wo_yao_quan_xin_zan_mei_244) or path to audio file"
    ),
    device: str = typer.Option("cpu", "--device", "-d", help="Device to run on (cpu/cuda)"),
    model: str = typer.Option("large-v2", "--model", "-m", help="Whisper model name"),
    use_vocals: bool = typer.Option(
        True, "--use-vocals/--no-use-vocals", help="Use vocals stem if available"
    ),
    output: Optional[Path] = typer.Option(
        None, "--output", "-o", help="Output file (default: stdout)"
    ),
    compute_type: str = typer.Option(
        "int8", "--compute-type", "-c", help="Compute type (int8/float16/float32)"
    ),
    align: bool = typer.Option(
        True, "--align/--no-align", help="Run wav2vec2 forced alignment for precise timestamps"
    ),
    timestamps: bool = typer.Option(
        True, "--timestamps/--no-timestamps", help="Output LRC format with timestamps (default). Use --no-timestamps for plain text."
    ),
    batch_size: int = typer.Option(
        16, "--batch-size", "-b", help="Batch size for WhisperX inference"
    ),
    vad: str = typer.Option(
        "silero", "--vad", help="VAD model (silero/pyannote/none). Silero works without auth."
    ),
    start: float = typer.Option(
        0.0, "--start", "-s", help="Start timestamp in seconds (default: 0)"
    ),
    end: Optional[float] = typer.Option(
        None, "--end", "-e", help="End timestamp in seconds (default: full song)"
    ),
):
    """Run WhisperX transcription on a song and output LRC or plain text format.

    By default, the entire song is transcribed with forced alignment enabled.
    Use --start and --end to transcribe a specific segment.
    Use --no-align to skip forced alignment (faster but less precise timestamps).
    Use --no-timestamps to output plain text without timestamps.
    Use --vad to choose the VAD model (silero works without HuggingFace auth).
    """
    audio_path, _ = resolve_song_audio_path(song_id, use_vocals=use_vocals)

    # Determine time range
    effective_end: Optional[float] = end if end and end > 0 else None
    if effective_end:
        typer.echo(f"Transcribing segment: {start}s to {effective_end}s", err=True)
    elif start > 0:
        typer.echo(f"Transcribing from {start}s to end", err=True)
    else:
        typer.echo("Transcribing full song", err=True)

    # Run transcription
    phrases = transcribe_audio(
        audio_path=audio_path,
        model_name=model,
        language="zh",
        device=device,
        compute_type=compute_type,
        batch_size=batch_size,
        start_seconds=start,
        end_seconds=effective_end,
        align=align,
        vad=vad,
    )

    typer.echo(f"Transcribed {len(phrases)} phrases", err=True)

    # Convert to output format
    if timestamps:
        output_content = phrases_to_lrc(phrases)
        format_name = "LRC"
    else:
        output_content = phrases_to_plain(phrases)
        format_name = "plain text"

    # Output
    if output:
        output.write_text(output_content, encoding="utf-8")
        typer.echo(f"Wrote {format_name} to: {output}", err=True)
    else:
        print(output_content)


if __name__ == "__main__":
    app()
