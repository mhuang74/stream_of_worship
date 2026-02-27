#!/usr/bin/env python3
"""Whisper transcription test driver for quick experimentation.

Runs Whisper transcription directly on a song from the local cache,
enabling quick experimentation to diagnose transcription accuracy issues.
"""

from pathlib import Path
from typing import Optional

import typer
from faster_whisper import WhisperModel

# Import shared utilities
from poc.utils import extract_audio_segment, format_timestamp, resolve_song_audio_path

app = typer.Typer(help="Whisper transcription test driver")


def transcribe_audio(
    audio_path: Path,
    model_name: str = "large-v3",
    language: str = "zh",
    device: str = "cpu",
    compute_type: str = "int8",
    start_seconds: float = 0.0,
    end_seconds: Optional[float] = None,
    vad_filter: bool = True,
    condition_on_previous: bool = True,
    initial_prompt: str = "这是一首中文敬拜诗歌",
) -> list[tuple[float, float, str]]:
    """Run Whisper transcription on audio file.

    Args:
        audio_path: Path to audio file
        model_name: Whisper model name
        language: Language hint
        device: Device to run on ("cpu" or "cuda")
        compute_type: Compute type ("int8", "float16", "int8_float16")
        start_seconds: Start time offset (for segment extraction)
        end_seconds: End time offset (for segment extraction, None for full)
        vad_filter: Enable Voice Activity Detection filter
        condition_on_previous: Condition on previous text for consistency
        initial_prompt: Initial prompt to guide transcription (includes lyrics)

    Returns:
        List of (start_time, end_time, text) tuples
    """
    import time

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
            f"Loading Whisper model: {model_name} on {device} with {compute_type}", err=True
        )
        model_load_start = time.time()
        model = WhisperModel(
            model_name,
            device=device,
            compute_type=compute_type,
        )
        model_load_elapsed = time.time() - model_load_start
        typer.echo(f"Model loaded in {model_load_elapsed:.2f}s", err=True)

        # Transcribe with Chinese worship song optimizations
        typer.echo(f"Running transcription: {transcribe_path}", err=True)
        typer.echo(
            f"Parameters: model={model_name}, language={language}, beam_size=5, "
            f"vad_filter={vad_filter}, condition_on_previous_text={condition_on_previous}",
            err=True,
        )
        transcribe_start = time.time()

        segments, info = model.transcribe(
            str(transcribe_path),
            language=language,
            beam_size=5,
            initial_prompt=initial_prompt,
            vad_filter=vad_filter,
            condition_on_previous_text=condition_on_previous,
        )

        # Collect segments (note: segments is a generator)
        phrases = []
        for segment in segments:
            text = segment.text.strip()
            if text:
                # Adjust timestamps by start offset
                adjusted_start = segment.start + start_seconds
                adjusted_end = segment.end + start_seconds
                phrases.append((adjusted_start, adjusted_end, text))

        transcribe_elapsed = time.time() - transcribe_start
        typer.echo(f"Transcription completed in {transcribe_elapsed:.2f}s", err=True)
        typer.echo(
            f"Detected language: {info.language}, probability: {info.language_probability:.2f}",
            err=True,
        )

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


@app.command()
def main(
    song_id: str = typer.Argument(
        ..., help="Song ID (e.g., wo_yao_quan_xin_zan_mei_244) or path to audio file"
    ),
    device: str = typer.Option("cpu", "--device", "-d", help="Device to run on (cpu/cuda)"),
    model: str = typer.Option("large-v3", "--model", "-m", help="Whisper model name"),
    use_vocals: bool = typer.Option(
        True, "--use-vocals/--no-use-vocals", help="Use vocals stem if available"
    ),
    output: Optional[Path] = typer.Option(
        None, "--output", "-o", help="Output file (default: stdout)"
    ),
    compute_type: str = typer.Option(
        "int8", "--compute-type", "-c", help="Compute type (int8/float16/int8_float16)"
    ),
    vad_filter: bool = typer.Option(
        True, "--vad-filter/--no-vad-filter", help="Enable Voice Activity Detection filter"
    ),
    condition_on_previous: bool = typer.Option(
        True, "--condition-on-previous/--no-condition-on-previous", help="Condition on previous text for consistency"
    ),
    start: float = typer.Option(
        0.0, "--start", "-s", help="Start timestamp in seconds (default: 0)"
    ),
    end: Optional[float] = typer.Option(
        None, "--end", "-e", help="End timestamp in seconds (default: full song)"
    ),
):
    """Run Whisper transcription on a song and output LRC format.

    By default, the entire song is transcribed. Use --start and --end to
    transcribe a specific segment.
    """
    # Build initial prompt
    initial_prompt = "这是一首中文敬拜诗歌"

    audio_path, lyrics = resolve_song_audio_path(song_id, use_vocals=use_vocals)

    # Build initial prompt from song lyrics (if available)
    if lyrics:
        # Join lyrics with newlines, limit to ~2000 chars to stay within Whisper's context
        lyrics_text = "\n".join(lyrics[:50])  # First 50 lines
        if len(lyrics_text) > 2000:
            lyrics_text = lyrics_text[:2000]
        initial_prompt = f"这是一首中文敬拜诗歌。歌词如下：\n{lyrics_text}"
        typer.echo(f"Using lyrics as initial prompt ({len(lyrics)} lines)", err=True)
    elif not Path(song_id).expanduser().exists():
        typer.echo("No lyrics found, using default prompt", err=True)

    # Determine time range
    # end=None means transcribe full song
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
        start_seconds=start,
        end_seconds=effective_end,
        vad_filter=vad_filter,
        condition_on_previous=condition_on_previous,
        initial_prompt=initial_prompt,
    )

    typer.echo(f"Transcribed {len(phrases)} phrases", err=True)

    # Convert to LRC format
    lrc_content = phrases_to_lrc(phrases)

    # Output
    if output:
        output.write_text(lrc_content, encoding="utf-8")
        typer.echo(f"Wrote LRC to: {output}", err=True)
    else:
        print(lrc_content)


if __name__ == "__main__":
    app()
