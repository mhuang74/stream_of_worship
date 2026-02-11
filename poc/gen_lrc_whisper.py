#!/usr/bin/env python3
"""Whisper transcription test driver for quick experimentation.

Runs Whisper transcription directly on a song from the local cache,
enabling quick experimentation to diagnose transcription accuracy issues.
"""

import sys
from pathlib import Path
from typing import Optional

import typer
from faster_whisper import WhisperModel

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from stream_of_worship.app.config import AppConfig
from stream_of_worship.app.db.read_client import ReadOnlyClient
from stream_of_worship.app.services.catalog import CatalogService
from stream_of_worship.app.services.asset_cache import AssetCache
from stream_of_worship.admin.services.r2 import R2Client

app = typer.Typer(help="Whisper transcription test driver")


def format_timestamp(seconds: float) -> str:
    """Format seconds as [mm:ss.xx] timestamp."""
    minutes = int(seconds // 60)
    secs = seconds % 60
    return f"[{minutes:02d}:{secs:05.2f}]"


def extract_audio_segment(
    audio_path: Path,
    start_seconds: float,
    end_seconds: float,
) -> Path:
    """Extract a segment of audio to a temporary file.

    Args:
        audio_path: Path to source audio file
        start_seconds: Start time in seconds
        end_seconds: End time in seconds

    Returns:
        Path to temporary file containing the segment
    """
    from pydub import AudioSegment

    # Load audio
    audio = AudioSegment.from_file(str(audio_path))

    # Calculate duration
    duration_ms = len(audio)
    start_ms = int(start_seconds * 1000)
    end_ms = int(end_seconds * 1000)

    # Clamp to valid range
    start_ms = max(0, start_ms)
    end_ms = min(duration_ms, end_ms)

    if start_ms >= end_ms:
        raise ValueError(f"Invalid segment: start ({start_seconds}s) >= end ({end_seconds}s)")

    # Extract segment
    segment = audio[start_ms:end_ms]

    # Write to temp file (preserve original format)
    import tempfile

    # Match source file format (wav for stems, mp3 for main audio)
    source_suffix = audio_path.suffix.lower()
    if source_suffix not in (".wav", ".mp3"):
        source_suffix = ".wav"  # Default to wav for other formats

    temp_file = tempfile.NamedTemporaryFile(suffix=source_suffix, delete=False)
    temp_path = Path(temp_file.name)
    temp_file.close()

    export_format = source_suffix.lstrip(".")
    segment.export(str(temp_path), format=export_format)
    return temp_path


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
    song_id: str = typer.Argument(..., help="Song ID (e.g., wo_yao_quan_xin_zan_mei_244)"),
    device: str = typer.Option("cpu", "--device", "-d", help="Device to run on (cpu/cuda)"),
    model: str = typer.Option("large-v3", "--model", "-m", help="Whisper model name"),
    use_vocals: bool = typer.Option(
        True, "--use-vocals/--no-use-vocals", help="Use vocals stem if available"
    ),
    output: Optional[Path] = typer.Option(
        None, "--output", "-o", help="Output file (default: stdout)"
    ),
    offline: bool = typer.Option(
        True, "--offline/--download", help="Only use cached files (default). Use --download to fetch from R2."
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
    # Load config
    try:
        config = AppConfig.load()
    except FileNotFoundError:
        typer.echo(
            "Error: Config file not found. Please run 'sow-app' first to create config.",
            err=True,
        )
        raise typer.Exit(1)

    # Initialize database client
    db_client = ReadOnlyClient(config.db_path)
    catalog = CatalogService(db_client)

    # Look up song and recording
    song_with_recording = catalog.get_song_with_recording(song_id)
    if not song_with_recording:
        typer.echo(f"Error: Song not found: {song_id}", err=True)
        raise typer.Exit(1)

    if not song_with_recording.recording:
        typer.echo(f"Error: No recording found for song: {song_id}", err=True)
        raise typer.Exit(1)

    song = song_with_recording.song
    recording = song_with_recording.recording
    hash_prefix = recording.hash_prefix

    typer.echo(f"Song: {song.title}", err=True)
    typer.echo(f"Recording: {hash_prefix}", err=True)

    # Build initial prompt from song lyrics
    lyrics = song.lyrics_list
    if lyrics:
        # Join lyrics with newlines, limit to ~2000 chars to stay within Whisper's context
        lyrics_text = "\n".join(lyrics[:50])  # First 50 lines
        if len(lyrics_text) > 2000:
            lyrics_text = lyrics_text[:2000]
        initial_prompt = f"这是一首中文敬拜诗歌。歌词如下：\n{lyrics_text}"
        typer.echo(f"Using lyrics as initial prompt ({len(lyrics)} lines)", err=True)
    else:
        initial_prompt = "这是一首中文敬拜诗歌"
        typer.echo("No lyrics found, using default prompt", err=True)

    # Initialize R2 client and asset cache
    try:
        r2_client = R2Client(
            bucket=config.r2_bucket,
            endpoint_url=config.r2_endpoint_url,
            region=config.r2_region,
        )
    except ValueError as e:
        typer.echo(f"Error: R2 credentials not configured: {e}", err=True)
        raise typer.Exit(1)

    cache = AssetCache(cache_dir=config.cache_dir, r2_client=r2_client)

    # Determine audio path
    audio_path: Optional[Path] = None

    # Try vocals stem first if requested
    if use_vocals:
        vocals_path = cache.get_stem_path(hash_prefix, "vocals")
        if vocals_path.exists():
            audio_path = vocals_path
            typer.echo(f"Using cached vocals stem: {audio_path}", err=True)
        elif not offline:
            typer.echo("Downloading vocals stem...", err=True)
            audio_path = cache.download_stem(hash_prefix, "vocals")
            if audio_path:
                typer.echo(f"Downloaded vocals stem: {audio_path}", err=True)

    # Fall back to main audio
    if audio_path is None:
        main_audio_path = cache.get_audio_path(hash_prefix)
        if main_audio_path.exists():
            audio_path = main_audio_path
            typer.echo(f"Using cached main audio: {audio_path}", err=True)
        elif not offline:
            typer.echo("Downloading main audio...", err=True)
            audio_path = cache.download_audio(hash_prefix)
            if audio_path:
                typer.echo(f"Downloaded main audio: {audio_path}", err=True)

    if audio_path is None:
        typer.echo("Error: Could not find or download audio", err=True)
        raise typer.Exit(1)

    if not audio_path.exists():
        typer.echo(f"Error: Audio file not found: {audio_path}", err=True)
        raise typer.Exit(1)

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
