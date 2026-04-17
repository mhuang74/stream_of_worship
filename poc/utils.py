#!/usr/bin/env python3
"""Shared utilities for POC lyric generation scripts.

This module provides common functionality used across multiple transcription
drivers to reduce code duplication.
"""

import sys
import tempfile
from pathlib import Path
from typing import Optional

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from stream_of_worship.app.config import AppConfig
from stream_of_worship.app.db.read_client import ReadOnlyClient
from stream_of_worship.app.services.catalog import CatalogService
from stream_of_worship.app.services.asset_cache import AssetCache
from stream_of_worship.admin.services.r2 import R2Client

# Supported audio formats for export
SUPPORTED_AUDIO_FORMATS = (".wav", ".mp3", ".flac", ".m4a")


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
    source_suffix = audio_path.suffix.lower()
    if source_suffix not in SUPPORTED_AUDIO_FORMATS:
        source_suffix = ".wav"  # Default to wav for other formats

    temp_file = tempfile.NamedTemporaryFile(suffix=source_suffix, delete=False)
    temp_path = Path(temp_file.name)
    temp_file.close()

    export_format = source_suffix.lstrip(".")
    segment.export(str(temp_path), format=export_format)
    return temp_path


def resolve_song_audio_path(
    song_id: str,
    use_vocals: bool = True,
    require_config: bool = True,
) -> tuple[Path, Optional[list[str]]]:
    """Resolve a song ID to a local audio path.

    This function handles both direct audio file paths and song IDs from the
    catalog. For song IDs, it initializes the database, R2 client, and asset
    cache to download/cache audio files.

    Args:
        song_id: Song ID (e.g., "wo_yao_quan_xin_zan_mei_244") or path to audio file
        use_vocals: Whether to prefer vocals stem over main audio
        require_config: Whether to require config for song ID lookup (vs direct path)

    Returns:
        Tuple of (audio_path, lyrics_list). Lyrics are only returned when
        resolving via song ID from database; None for direct audio paths.

    Raises:
        typer.Exit: If song not found, config missing, or audio unavailable
    """
    import typer

    input_path = Path(song_id).expanduser()
    lyrics: Optional[list[str]] = None

    # Direct audio file path
    if input_path.exists():
        return input_path, lyrics

    if require_config:
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
        lyrics = song.lyrics_list

        typer.echo(f"Song: {song.title}", err=True)
        typer.echo(f"Recording: {hash_prefix}", err=True)

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
        audio_path: Optional[Path] = None

        # Try vocals stem first if requested
        # Preference: vocals_clean > vocals
        if use_vocals:
            for stem_name in ["vocals_clean", "vocals"]:
                stem_path = cache.get_stem_path(hash_prefix, stem_name)
                if stem_path.exists():
                    audio_path = stem_path
                    typer.echo(f"Using cached {stem_name} stem: {audio_path}", err=True)
                    break
                downloaded = cache.download_stem(hash_prefix, stem_name)
                if downloaded:
                    audio_path = downloaded
                    typer.echo(f"Downloaded {stem_name} stem: {audio_path}", err=True)
                    break

        # Fall back to main audio
        if audio_path is None:
            main_audio_path = cache.get_audio_path(hash_prefix)
            if main_audio_path.exists():
                audio_path = main_audio_path
                typer.echo(f"Using cached main audio: {audio_path}", err=True)
            else:
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

        return audio_path, lyrics

    raise typer.Exit(1)
