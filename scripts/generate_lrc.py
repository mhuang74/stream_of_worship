#!/usr/bin/env python3
"""Standalone LRC generation script.

This script generates LRC files for songs using the LRCGenerator pipeline.
It can work with:
1. The new song library structure (catalog-based)
2. Legacy POC data structure (poc_audio/, poc_output/, data/lyrics/)

Usage:
    # Generate LRC for a single song in the library
    python scripts/generate_lrc.py --song-id jiang_tian_chang_kai_209

    # Generate LRC for all songs in catalog
    python scripts/generate_lrc.py --all

    # Generate LRC from legacy POC data (before migration)
    python scripts/generate_lrc.py --from-poc --song-name "將天敞開"

    # Test mode (dry run, don't save)
    python scripts/generate_lrc.py --song-id jiang_tian_chang_kai_209 --dry-run
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Optional, List

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

try:
    from stream_of_worship.ingestion.lrc_generator import LRCGenerator, LRCLine
    from stream_of_worship.core.paths import (
        get_song_dir,
        get_catalog_index_path,
        get_whisper_cache_path,
    )
    from stream_of_worship.core.catalog import CatalogIndex
except ImportError as e:
    print(f"Import error: {e}")
    print("Make sure you're running from the project root.")
    sys.exit(1)


# Legacy paths (from POC structure)
LEGACY_AUDIO_PATH = Path("poc_audio")
LEGACY_ANALYSIS_PATH = Path("poc_output_allinone/poc_full_results.json")
LEGACY_LYRICS_PATH = Path("data/lyrics/songs")


def get_api_key() -> Optional[str]:
    """Get OpenRouter API key from environment or config."""
    return os.environ.get("OPENROUTER_API_KEY")


def load_analysis_for_song(song_name: str) -> Optional[dict]:
    """Load analysis data for a song from legacy POC results.

    Args:
        song_name: Song name (filename without extension)

    Returns:
        Analysis dict or None if not found
    """
    if not LEGACY_ANALYSIS_PATH.exists():
        return None

    with LEGACY_ANALYSIS_PATH.open("r", encoding="utf-8") as f:
        results = json.load(f)

    for result in results:
        filename = result.get("filename", "")
        if filename.replace(".mp3", "") == song_name or filename == song_name:
            return result

    return None


def load_lyrics_for_song(song_name: str) -> Optional[str]:
    """Load lyrics for a song from legacy lyrics data.

    Args:
        song_name: Song name

    Returns:
        Lyrics text or None if not found
    """
    # Try various ID formats
    possible_ids = [
        song_name,
        song_name.replace(" ", "_"),
        song_name.lower().replace(" ", "_"),
    ]

    for song_id in possible_ids:
        lyrics_file = LEGACY_LYRICS_PATH / f"{song_id}.json"
        if lyrics_file.exists():
            with lyrics_file.open("r", encoding="utf-8") as f:
                data = json.load(f)
                # Try different formats
                if "lyrics_raw" in data:
                    return data["lyrics_raw"]
                elif "lyrics_lines" in data:
                    lines = data["lyrics_lines"]
                    if isinstance(lines, list):
                        return "\n".join(lines)
                    return str(lines)
                elif "lyrics" in data:
                    return data["lyrics"]

    # Try to find by partial match
    if LEGACY_LYRICS_PATH.exists():
        for lyrics_file in LEGACY_LYRICS_PATH.glob("*.json"):
            with lyrics_file.open("r", encoding="utf-8") as f:
                data = json.load(f)
                title = data.get("title", "")
                if song_name.lower() in title.lower():
                    if "lyrics_raw" in data:
                        return data["lyrics_raw"]
                    elif "lyrics_lines" in data:
                        lines = data["lyrics_lines"]
                        if isinstance(lines, list):
                            return "\n".join(lines)
                        return str(lines)

    return None


def get_audio_path(song_name: str) -> Optional[Path]:
    """Get audio file path for a song.

    Args:
        song_name: Song name

    Returns:
        Path to audio file or None if not found
    """
    # Try various formats
    possible_names = [
        f"{song_name}.mp3",
        f"{song_name}.flac",
        f"{song_name}.wav",
    ]

    for name in possible_names:
        path = LEGACY_AUDIO_PATH / name
        if path.exists():
            return path

    # Try case-insensitive match
    for ext in [".mp3", ".flac", ".wav"]:
        for file_path in LEGACY_AUDIO_PATH.glob(f"*{ext}"):
            if song_name.lower() in file_path.stem.lower():
                return file_path

    return None


def extract_lyrics_text(lyrics_data: dict) -> str:
    """Extract lyrics text from lyrics data structure.

    Args:
        lyrics_data: Lyrics data dict

    Returns:
        Lyrics text
    """
    # Try different formats
    if "lyrics_raw" in lyrics_data and lyrics_data["lyrics_raw"]:
        return lyrics_data["lyrics_raw"]
    elif "lyrics_lines" in lyrics_data:
        lines = lyrics_data["lyrics_lines"]
        if isinstance(lines, list):
            return "\n".join(lines)
        return str(lines)
    elif "lyrics" in lyrics_data:
        lyrics = lyrics_data["lyrics"]
        if isinstance(lyrics, list):
            return "\n".join(lyrics)
        return str(lyrics)

    return ""


def generate_lrc_for_song(
    song_id: str,
    generator: LRCGenerator,
    dry_run: bool = False,
    output_path: Optional[Path] = None,
) -> bool:
    """Generate LRC for a single song from the library.

    Args:
        song_id: Song ID in catalog
        generator: LRCGenerator instance
        dry_run: If True, don't save the file
        output_path: Optional custom output path

    Returns:
        True if successful, False otherwise
    """
    song_dir = get_song_dir(song_id)

    # Check required files
    audio_path = song_dir / "audio.mp3"
    lyrics_path = song_dir / "lyrics.json"
    analysis_path = song_dir / "analysis.json"

    if not audio_path.exists():
        print(f"Error: Audio file not found: {audio_path}")
        return False

    if not lyrics_path.exists():
        print(f"Error: Lyrics file not found: {lyrics_path}")
        return False

    if not analysis_path.exists():
        print(f"Error: Analysis file not found: {analysis_path}")
        return False

    # Load data
    try:
        with lyrics_path.open("r", encoding="utf-8") as f:
            lyrics_data = json.load(f)
            lyrics_text = extract_lyrics_text(lyrics_data)

        if not lyrics_text.strip():
            print(f"Error: Empty lyrics for {song_id}")
            return False

        with analysis_path.open("r", encoding="utf-8") as f:
            analysis = json.load(f)
            beats = analysis.get("beats", [])

        if not beats:
            print(f"Warning: No beats data for {song_id}, will use raw timestamps")

    except Exception as e:
        print(f"Error loading data for {song_id}: {e}")
        return False

    # Generate LRC
    if output_path is None:
        output_path = song_dir / "lyrics.lrc"

    if dry_run:
        print(f"[DRY RUN] Would generate LRC for: {song_id}")
        print(f"  Audio: {audio_path}")
        print(f"  Lyrics: {len(lyrics_text)} chars")
        print(f"  Beats: {len(beats)} timestamps")
        print(f"  Output: {output_path}")
        return True

    try:
        success = generator.generate(
            audio_path=audio_path,
            lyrics_text=lyrics_text,
            beats=beats,
            output_path=output_path,
            progress_callback=lambda msg, pct: print(f"  {msg} ({pct*100:.0f}%)"),
        )

        if success:
            print(f"Successfully generated: {output_path}")
            return True
        else:
            print(f"Failed to generate LRC for: {song_id}")
            return False

    except Exception as e:
        print(f"Error generating LRC for {song_id}: {e}")
        return False


def generate_lrc_from_poc(
    song_name: str,
    generator: LRCGenerator,
    output_path: Optional[Path] = None,
    dry_run: bool = False,
) -> bool:
    """Generate LRC for a song from legacy POC data.

    Args:
        song_name: Song name
        generator: LRCGenerator instance
        output_path: Optional custom output path
        dry_run: If True, don't save the file

    Returns:
        True if successful, False otherwise
    """
    print(f"Processing: {song_name}")

    # Find audio file
    audio_path = get_audio_path(song_name)
    if not audio_path:
        print(f"  Error: Audio file not found for '{song_name}'")
        return False
    print(f"  Audio: {audio_path}")

    # Load lyrics
    lyrics_text = load_lyrics_for_song(song_name)
    if not lyrics_text:
        print(f"  Warning: No lyrics found for '{song_name}'")
        return False
    print(f"  Lyrics: {len(lyrics_text)} chars")

    # Load analysis
    analysis = load_analysis_for_song(song_name)
    if not analysis:
        print(f"  Warning: No analysis found for '{song_name}'")
        beats = []
    else:
        beats = analysis.get("beats", [])
        print(f"  Beats: {len(beats)} timestamps")

    # Determine output path
    if output_path is None:
        output_path = Path(f"{song_name}.lrc")

    if dry_run:
        print(f"  [DRY RUN] Would save to: {output_path}")
        return True

    # Generate LRC
    try:
        success = generator.generate(
            audio_path=audio_path,
            lyrics_text=lyrics_text,
            beats=beats,
            output_path=output_path,
            progress_callback=lambda msg, pct: print(f"    {msg} ({pct*100:.0f}%)"),
        )

        if success:
            print(f"  Success: {output_path}")
            return True
        else:
            print(f"  Failed to generate LRC")
            return False

    except Exception as e:
        print(f"  Error: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Generate LRC files for worship songs"
    )
    parser.add_argument(
        "--song-id",
        type=str,
        help="Song ID from catalog (e.g., jiang_tian_chang_kai_209)",
    )
    parser.add_argument(
        "--song-name",
        type=str,
        help="Song name for POC mode (legacy)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Generate LRC for all songs in catalog",
    )
    parser.add_argument(
        "--from-poc",
        action="store_true",
        help="Use legacy POC data structure instead of catalog",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Custom output path for LRC file",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Output directory for generated LRC files",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Dry run - don't actually save files",
    )
    parser.add_argument(
        "--whisper-model",
        type=str,
        default="large-v3",
        help="Whisper model to use (default: large-v3)",
    )
    parser.add_argument(
        "--llm-model",
        type=str,
        default="openai/gpt-4o-mini",
        help="LLM model to use (default: openai/gpt-4o-mini)",
    )
    parser.add_argument(
        "--max-failures",
        type=int,
        default=5,
        help="Maximum failures before stopping batch processing",
    )

    args = parser.parse_args()

    # Validate arguments
    if not args.song_id and not args.song_name and not args.all:
        parser.error("Specify --song-id, --song-name, or --all")

    if args.from_poc and not args.song_name and not args.all:
        parser.error("--from-poc requires --song-name")

    if args.all and args.from_poc:
        parser.error("--all with --from-poc is not supported (no catalog to iterate)")

    # Check API key
    api_key = get_api_key()
    if not api_key:
        print("Error: OPENROUTER_API_KEY environment variable not set")
        print("Get your API key from: https://openrouter.ai/keys")
        sys.exit(1)

    # Initialize generator
    print(f"Initializing LRC Generator...")
    print(f"  Whisper model: {args.whisper_model}")
    print(f"  LLM model: {args.llm_model}")
    print(f"  Cache: {get_whisper_cache_path()}")

    generator = LRCGenerator(
        whisper_model=args.whisper_model,
        llm_model=args.llm_model,
        api_key=api_key,
    )

    # Process single song from library
    if args.song_id and not args.from_poc:
        success = generate_lrc_for_song(
            song_id=args.song_id,
            generator=generator,
            dry_run=args.dry_run,
            output_path=args.output,
        )
        sys.exit(0 if success else 1)

    # Process single song from POC
    if args.song_name and args.from_poc:
        success = generate_lrc_from_poc(
            song_name=args.song_name,
            generator=generator,
            output_path=args.output,
            dry_run=args.dry_run,
        )
        sys.exit(0 if success else 1)

    # Process all songs in catalog
    if args.all:
        try:
            catalog = CatalogIndex.load()
        except FileNotFoundError:
            print("Error: Catalog not found. Run migration first:")
            print("  python -m stream_of_worship migrate from-legacy")
            sys.exit(1)

        print(f"\nBatch processing {len(catalog.songs)} songs...")
        print(f"Max failures before stopping: {args.max_failures}")
        print("-" * 60)

        success_count = 0
        failure_count = 0
        skipped_count = 0

        for i, song in enumerate(catalog.songs):
            print(f"\n[{i+1}/{len(catalog.songs)}] {song.id}")

            # Check if already has LRC
            song_dir = get_song_dir(song.id)
            lrc_path = song_dir / "lyrics.lrc"

            if lrc_path.exists() and not args.dry_run:
                print(f"  Skipping (already exists): {lrc_path}")
                skipped_count += 1
                continue

            success = generate_lrc_for_song(
                song_id=song.id,
                generator=generator,
                dry_run=args.dry_run,
            )

            if success:
                success_count += 1
                # Update catalog
                if not args.dry_run:
                    song.has_lrc = True
                    catalog.save()
            else:
                failure_count += 1
                if failure_count >= args.max_failures:
                    print(f"\nStopping after {args.max_failures} failures")
                    break

        print("\n" + "=" * 60)
        print("Batch Processing Summary")
        print("=" * 60)
        print(f"Total songs: {len(catalog.songs)}")
        print(f"Successful: {success_count}")
        print(f"Failed: {failure_count}")
        print(f"Skipped (already exist): {skipped_count}")
        print("=" * 60)

        sys.exit(0 if failure_count < args.max_failures else 1)


if __name__ == "__main__":
    main()
