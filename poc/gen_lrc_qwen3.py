#!/usr/bin/env python3
"""Qwen3 Forced Aligner LRC generation script.

Aligns known lyrics to audio timestamps using Qwen3-ForcedAligner-0.6B.
Unlike Whisper which transcribes audio, this requires pre-existing lyrics
and aligns them precisely to the audio timing.

Note: Maximum audio length is 5 minutes (model limitation).
"""

import sys
from pathlib import Path
from typing import Optional

import typer

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from stream_of_worship.app.config import AppConfig
from stream_of_worship.app.db.read_client import ReadOnlyClient
from stream_of_worship.app.services.catalog import CatalogService
from stream_of_worship.app.services.asset_cache import AssetCache
from stream_of_worship.admin.services.r2 import R2Client

app = typer.Typer(help="Qwen3 Forced Aligner LRC generation")


def format_timestamp(seconds: float) -> str:
    """Format seconds as [mm:ss.xx] timestamp."""
    minutes = int(seconds // 60)
    secs = seconds % 60
    return f"[{minutes:02d}:{secs:05.2f}]"


def get_audio_duration(audio_path: Path) -> float:
    """Get audio duration in seconds.

    Args:
        audio_path: Path to audio file

    Returns:
        Duration in seconds
    """
    from pydub import AudioSegment

    audio = AudioSegment.from_file(str(audio_path))
    return len(audio) / 1000.0


def get_model_cache_dir() -> Path:
    """Get the default HuggingFace cache directory.

    Returns:
        Path to the HuggingFace cache directory
    """
    import os

    # Check environment variables in order of precedence
    if "HF_HOME" in os.environ:
        return Path(os.environ["HF_HOME"])
    if "XDG_CACHE_HOME" in os.environ:
        return Path(os.environ["XDG_CACHE_HOME"]) / "huggingface"

    # Default to ~/.cache/huggingface
    return Path.home() / ".cache" / "huggingface"


def is_model_cached(model_name: str = "Qwen/Qwen3-ForcedAligner-0.6B") -> bool:
    """Check if the model is already cached locally.

    Args:
        model_name: Name of the model

    Returns:
        True if model is cached
    """
    cache_dir = get_model_cache_dir()
    # Models are stored in hub/models--{org}--{model}/snapshots/{hash}/
    model_path = cache_dir / "hub"
    if not model_path.exists():
        return False

    # Check for model directory (format: models--Qwen--Qwen3-ForcedAligner-0.6B)
    safe_model_name = model_name.replace("/", "--")
    model_dirs = list(model_path.glob(f"models--{safe_model_name}"))
    return len(model_dirs) > 0 and any(d.is_dir() for d in model_dirs)


def map_segments_to_lines(
    segments: list[tuple[float, float, str]],
    original_lines: list[str],
) -> list[tuple[float, float, str]]:
    """Map character-level alignment segments back to original lyric lines.

    The Qwen3ForcedAligner returns character/word-level timestamps. This function
    maps those fine-grained segments back to the original lyric lines by tracking
    text position and computing min/max timestamps for each line.

    Args:
        segments: List of (start_time, end_time, text) from aligner
        original_lines: Original lyric lines (preserving structure)

    Returns:
        List of (start_time, end_time, text) with one entry per original line
    """
    # Build the full aligned text and track character positions
    aligned_text = ""
    segment_positions = []  # (start_char, end_char, start_time, end_time)

    for seg_start, seg_end, seg_text in segments:
        start_char = len(aligned_text)
        aligned_text += seg_text
        end_char = len(aligned_text)
        segment_positions.append((start_char, end_char, seg_start, seg_end, seg_text))

    # Normalize for comparison (remove spaces, punctuation differences)
    def normalize(text: str) -> str:
        import re

        # Remove whitespace and common punctuation for matching
        return re.sub(r"[\s。，！？、；：\"''""''""''（）【】「」『』 ]+", "", text)

    # Build normalized aligned text
    aligned_normalized = normalize(aligned_text)

    # Map each original line to its time range
    line_alignments = []
    current_pos = 0

    for line in original_lines:
        normalized_line = normalize(line)
        if not normalized_line:
            # Empty line - use previous end time or 0
            prev_end = line_alignments[-1][1] if line_alignments else 0.0
            line_alignments.append((prev_end, prev_end, line))
            continue

        # Find this line in the normalized aligned text
        line_start = aligned_normalized.find(normalized_line, current_pos)

        if line_start == -1:
            # Line not found - might be due to alignment differences
            # Use interpolation based on position in original text
            if current_pos >= len(aligned_normalized):
                prev_end = line_alignments[-1][1] if line_alignments else 0.0
                line_alignments.append((prev_end, prev_end, line))
            else:
                # Estimate position proportionally
                ratio = current_pos / len(aligned_normalized)
                est_start = segments[0][0] if segments else 0.0
                est_end = segments[-1][1] if segments else 0.0
                duration = est_end - est_start
                line_alignments.append((
                    est_start + ratio * duration,
                    est_start + ratio * duration,
                    line
                ))
            continue

        line_end = line_start + len(normalized_line)
        current_pos = line_end

        # Find all segments that overlap with this line
        overlapping_segments = []
        for seg_start_char, seg_end_char, seg_start_time, seg_end_time, seg_text in segment_positions:
            # Check overlap
            if seg_end_char > line_start and seg_start_char < line_end:
                overlapping_segments.append((seg_start_time, seg_end_time))

        if overlapping_segments:
            # Use earliest start and latest end for this line
            start_time = min(s[0] for s in overlapping_segments)
            end_time = max(s[1] for s in overlapping_segments)
            line_alignments.append((start_time, end_time, line))
        else:
            # No segments overlap - use interpolated time
            ratio = line_start / len(aligned_normalized) if aligned_normalized else 0
            est_start = segments[0][0] if segments else 0.0
            est_end = segments[-1][1] if segments else 0.0
            duration = est_end - est_start
            line_alignments.append((
                est_start + ratio * duration,
                est_start + ratio * duration + (duration / len(original_lines)),
                line
            ))

    return line_alignments


def align_lyrics(
    audio_path: Path,
    lyrics_lines: list[str],
    language: str = "Chinese",
    device: str = "auto",
    dtype: str = "float32",
    model_cache_dir: Optional[Path] = None,
) -> list[tuple[float, float, str]]:
    """Align lyrics to audio using Qwen3ForcedAligner.

    Args:
        audio_path: Path to audio file
        lyrics_text: The lyrics/text to align to the audio
        language: Language hint (e.g., "Chinese", "English")
        device: Device to run on ("auto", "mps", "cuda", "cpu")
        dtype: Data type ("bfloat16", "float16", "float32")

    Returns:
        List of (start_time, end_time, text) tuples

    Raises:
        ValueError: If audio exceeds 5 minutes or no lyrics provided
        RuntimeError: If alignment fails
    """
    import time

    import torch
    from pydub import AudioSegment
    from qwen_asr import Qwen3ForcedAligner

    # Check audio duration (5 minute limit)
    audio_duration = get_audio_duration(audio_path)
    if audio_duration > 300:
        raise ValueError(
            f"Audio duration ({audio_duration:.1f}s) exceeds 5 minute limit "
            f"of Qwen3ForcedAligner"
        )

    if not lyrics_lines:
        raise ValueError("Lyrics are required for forced alignment")

    # Join lyrics for the aligner
    lyrics_text = "\n".join(lyrics_lines)

    # Determine device
    if device == "auto":
        if torch.backends.mps.is_available():
            device = "mps"
        elif torch.cuda.is_available():
            device = "cuda"
        else:
            device = "cpu"

    # Map dtype string to torch dtype
    dtype_map = {
        "bfloat16": torch.bfloat16,
        "float16": torch.float16,
        "float32": torch.float32,
    }
    torch_dtype = dtype_map.get(dtype, torch.float32)

    # Check if model is cached
    model_name = "Qwen/Qwen3-ForcedAligner-0.6B"
    cached = is_model_cached(model_name)
    cache_location = model_cache_dir or get_model_cache_dir()

    if cached:
        typer.echo(f"Loading Qwen3ForcedAligner from cache: {cache_location}", err=True)
    else:
        typer.echo(f"Downloading Qwen3ForcedAligner (one-time) to: {cache_location}", err=True)

    typer.echo(f"Device: {device}, dtype: {dtype}", err=True)
    load_start = time.time()

    # Set cache directory if specified
    if model_cache_dir:
        import os

        os.environ["HF_HOME"] = str(model_cache_dir)

    model = Qwen3ForcedAligner.from_pretrained(
        model_name,
        dtype=torch_dtype,
        device_map=device,
    )

    load_elapsed = time.time() - load_start
    typer.echo(f"Model loaded in {load_elapsed:.2f}s", err=True)

    # Run alignment
    typer.echo(f"Aligning lyrics to audio ({audio_duration:.1f}s)...", err=True)
    align_start = time.time()

    results = model.align(
        audio=str(audio_path),
        text=lyrics_text,
        language=language,
    )

    align_elapsed = time.time() - align_start
    typer.echo(f"Alignment completed in {align_elapsed:.2f}s", err=True)

    # Extract (start, end, text) tuples from results (character/word level)
    raw_segments = []
    for segment_list in results:
        for segment in segment_list:
            text = segment.text.strip()
            if text:
                raw_segments.append((segment.start_time, segment.end_time, text))

    typer.echo(f"Mapping {len(raw_segments)} segments to {len(lyrics_lines)} lines...", err=True)

    # Map character-level segments back to original lyric lines
    line_alignments = map_segments_to_lines(raw_segments, lyrics_lines)

    return line_alignments


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
    device: str = typer.Option("auto", "--device", "-d", help="Device (auto/mps/cuda/cpu)"),
    dtype: str = typer.Option(
        "float32", "--dtype", help="Data type (bfloat16/float16/float32)"
    ),
    use_vocals: bool = typer.Option(
        True, "--use-vocals/--no-use-vocals", help="Use vocals stem if available"
    ),
    output: Optional[Path] = typer.Option(
        None, "--output", "-o", help="Output file (default: stdout)"
    ),
    offline: bool = typer.Option(
        True,
        "--offline/--download",
        help="Only use cached files (default). Use --download to fetch from R2.",
    ),
    language: str = typer.Option("Chinese", "--language", "-l", help="Language hint"),
    model_cache_dir: Optional[Path] = typer.Option(
        None,
        "--model-cache-dir",
        help="Directory to cache the model (default: ~/.cache/huggingface)",
    ),
    lyrics_file: Optional[Path] = typer.Option(
        None,
        "--lyrics-file",
        "-L",
        help="Path to lyrics file (overrides lyrics from database)",
    ),
):
    """Generate LRC file by aligning lyrics to audio using Qwen3ForcedAligner.

    Unlike Whisper which transcribes audio, this script requires pre-existing
    lyrics and aligns them precisely to audio timestamps.

    Lyrics can come from the database (default) or a file (--lyrics-file).

    The Qwen3-ForcedAligner-0.6B model is automatically cached after first download.
    Use --model-cache-dir to specify a custom cache location.

    Maximum audio length is 5 minutes.
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

    # Get lyrics from file or database
    if lyrics_file:
        if not lyrics_file.exists():
            typer.echo(f"Error: Lyrics file not found: {lyrics_file}", err=True)
            raise typer.Exit(1)
        try:
            lyrics_text = lyrics_file.read_text(encoding="utf-8")
            lyrics = [line.rstrip() for line in lyrics_text.splitlines()]
            # Remove trailing empty lines but preserve internal ones
            while lyrics and not lyrics[-1]:
                lyrics.pop()
            typer.echo(f"Using lyrics from file: {lyrics_file}", err=True)
        except Exception as e:
            typer.echo(f"Error reading lyrics file: {e}", err=True)
            raise typer.Exit(1)
    else:
        lyrics = song.lyrics_list
        if not lyrics:
            typer.echo(
                "Error: No lyrics found for this song. Forced alignment requires lyrics.", err=True
            )
            typer.echo(
                "Please add lyrics to the song first using the scraper or admin tools.", err=True
            )
            raise typer.Exit(1)
        typer.echo("Using lyrics from database", err=True)

    typer.echo(f"Using {len(lyrics)} lines of lyrics for alignment:", err=True)
    typer.echo("-" * 40, err=True)
    for i, line in enumerate(lyrics, 1):
        typer.echo(f"{i:2d}: {line}", err=True)
    typer.echo("-" * 40, err=True)

    # Check audio duration (5 minute limit for Qwen3ForcedAligner)
    if recording.duration_seconds and recording.duration_seconds > 300:
        typer.echo(
            f"Error: Song duration ({recording.duration_seconds:.1f}s) exceeds "
            f"5 minute limit of Qwen3ForcedAligner",
            err=True,
        )
        raise typer.Exit(1)

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

    # Verify audio duration again from file
    try:
        audio_duration = get_audio_duration(audio_path)
        if audio_duration > 300:
            typer.echo(
                f"Error: Audio file duration ({audio_duration:.1f}s) exceeds "
                f"5 minute limit of Qwen3ForcedAligner",
                err=True,
            )
            raise typer.Exit(1)
    except Exception as e:
        typer.echo(f"Warning: Could not verify audio duration: {e}", err=True)

    # Run alignment
    try:
        phrases = align_lyrics(
            audio_path=audio_path,
            lyrics_lines=lyrics,
            language=language,
            device=device,
            dtype=dtype,
            model_cache_dir=model_cache_dir,
        )
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
    except Exception as e:
        typer.echo(f"Error during alignment: {e}", err=True)
        raise typer.Exit(1)

    typer.echo(f"Aligned {len(phrases)} lines", err=True)

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
