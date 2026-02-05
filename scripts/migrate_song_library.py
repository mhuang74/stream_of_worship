"""Data migration script for Stream of Worship.

This script migrates existing POC data to the new song library structure:
- Loads analysis data from poc_full_results.json
- Migrates scraped lyrics from data/lyrics/songs/
- Copies source audio from poc_audio/
- Moves stems from stems_output/ to per-song directories
- Generates pinyin + sequential ID naming
- Creates catalog_index.json

The migration is idempotent - it can be safely re-run and will skip
already-migrated songs.
"""

import json
import re
import shutil
from pathlib import Path
from typing import List, Dict, Any

import pypinyin

# Add parent directory to path for imports
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from stream_of_worship.core.paths import (
    get_user_data_dir,
    get_song_library_path,
    get_catalog_index_path,
    get_song_dir,
    ensure_directories,
)
from stream_of_worship.core.catalog import Song, CatalogIndex


# Configuration
POC_RESULTS_PATH = Path("poc_output_allinone/poc_full_results.json")
LYRICS_SOURCE_PATH = Path("data/lyrics/songs")
AUDIO_SOURCE_PATH = Path("poc_audio")
STEMS_SOURCE_PATH = Path("poc/output_allinone/stems")

# Song ID counter (will be incremented during migration)
# Songs that were already migrated should use their existing IDs


def clean_chinese_filename(name: str) -> str:
    """Convert Chinese name to pinyin for cross-platform compatibility.

    Args:
        name: Original name (e.g., "將天敞開")

    Returns:
        Pinyin transliteration (e.g., "jiang_tian_chang_kai")
    """
    # Remove special characters and spaces
    cleaned = re.sub(r'[^\w\u4e00-\u9fff]', '', name)

    # Convert to pinyin
    pinyin_list = pypinyin.lazy_pinyin(cleaned, style=pypinyin.Style.NORMAL)

    # Join with underscores
    result = '_'.join(pinyin_list).lower()

    # Remove empty parts
    result = re.sub(r'_+', '_', result).strip('_')

    return result if result else "unknown"


def load_poc_results(path: Path) -> List[Dict[str, Any]]:
    """Load POC analysis results.

    Args:
        path: Path to poc_full_results.json

    Returns:
        List of song analysis dictionaries
    """
    if not path.exists():
        raise FileNotFoundError(f"POC results not found: {path}")

    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_scraped_lyrics(path: Path) -> Dict[str, Dict[str, Any]]:
    """Load scraped lyrics from JSON files.

    Args:
        path: Path to lyrics directory

    Returns:
        Dictionary mapping song_id to lyrics data
    """
    lyrics_data = {}

    for file_path in path.glob("*.json"):
        song_id = file_path.stem
        with file_path.open("r", encoding="utf-8") as f:
            lyrics_data[song_id] = json.load(f)

    return lyrics_data


def get_source_audio_path(song_name: str, audio_dir: Path) -> Path:
    """Get source audio file path for a song.

    Args:
        song_name: Song name (may have variants or include extension)
        audio_dir: Path to poc_audio directory

    Returns:
        Path to audio file or None if not found
    """
    # Handle case where song_name already includes extension
    stem = Path(song_name).stem

    # Try exact match first
    audio_path = audio_dir / f"{stem}.mp3"
    if audio_path.exists():
        return audio_path

    # Try case-insensitive match
    for file_path in audio_dir.glob("*.mp3"):
        if file_path.stem.lower() == stem.lower():
            return file_path

    return None


def get_stems_path(song_name: str, stems_dir: Path) -> Path:
    """Get stems directory path for a song.

    Args:
        song_name: Song name (pinyin format)
        stems_dir: Path to stems_output directory

    Returns:
        Path to stems directory or None if not found
    """
    stems_path = stems_dir / song_name
    if stems_path.exists():
        return stems_path

    # Try case-insensitive match
    for subdir in stems_dir.iterdir():
        if subdir.is_dir() and subdir.name.lower() == song_name.lower():
            return subdir

    return None


def get_existing_catalog() -> CatalogIndex:
    """Load existing catalog if it exists.

    Returns:
        CatalogIndex (empty if doesn't exist)
    """
    catalog_path = get_catalog_index_path()
    if catalog_path.exists():
        try:
            return CatalogIndex.load(catalog_path)
        except Exception:
            pass  # Fall through to empty catalog
    return CatalogIndex()


def get_next_song_id(catalog: CatalogIndex) -> int:
    """Get the next sequential song ID.

    Args:
        catalog: Existing catalog

    Returns:
        Next ID to use
    """
    if not catalog.songs:
        return 1

    # Extract existing IDs and find max
    max_id = 0
    for song in catalog.songs:
        # ID format: {pinyin_name}_{number}
        parts = song.id.rsplit("_", 1)
        if len(parts) == 2:
            try:
                num_id = int(parts[1])
                if num_id > max_id:
                    max_id = num_id
            except ValueError:
                pass

    return max_id + 1


def migrate_song(
    analysis: Dict[str, Any],
    lyrics_data: Dict[str, Dict[str, Any]],
    next_id: int,
    catalog: CatalogIndex,
    progress_callback=None,
) -> tuple[bool, int]:
    """Migrate a single song to the library.

    Args:
        analysis: Song analysis from POC results
        lyrics_data: Dictionary of scraped lyrics
        next_id: Next available sequential ID
        catalog: Existing catalog to update
        progress_callback: Optional callback for progress updates

    Returns:
        Tuple of (success, next_id after this song)
    """
    filename = analysis.get("filename", "")
    song_name = filename.replace(".mp3", "")

    # Check if already migrated
    existing_song = None
    for song in catalog.songs:
        if filename in song.title or song_name in song.id:
            existing_song = song
            break

    if existing_song:
        if progress_callback:
            progress_callback(f"Skipping already migrated: {filename}", None)
        return True, next_id

    # Generate song ID
    pinyin_name = clean_chinese_filename(song_name)
    song_id = f"{pinyin_name}_{next_id}"

    if progress_callback:
        progress_callback(f"Migrating: {filename} -> {song_id}", None)

    # Create song directory
    song_dir = get_song_dir(song_id)
    song_dir.mkdir(parents=True, exist_ok=True)

    # Migrate analysis.json
    analysis_output = song_dir / "analysis.json"
    with analysis_output.open("w", encoding="utf-8") as f:
        json.dump(analysis, f, indent=2, ensure_ascii=False)

    # Migrate lyrics.json
    lyrics_output = song_dir / "lyrics.json"
    lyrics_entry = None

    # Try to find matching lyrics by song_id from analysis
    # The song_id in lyrics files uses a different format (song_number)
    if lyrics_data:
        # Try to match by filename
        for lyric_key, lyric_value in lyrics_data.items():
            if song_name.lower() in lyric_key.lower():
                lyrics_entry = lyric_value
                break

    if lyrics_entry:
        with lyrics_output.open("w", encoding="utf-8") as f:
            json.dump(lyrics_entry, f, indent=2, ensure_ascii=False)
    else:
        print(f"  Warning: No lyrics found for {filename}")

    # Copy source audio
    audio_source = get_source_audio_path(song_name, AUDIO_SOURCE_PATH)
    if audio_source and audio_source.exists():
        audio_dest = song_dir / "audio.mp3"
        shutil.copy2(audio_source, audio_dest)
    else:
        print(f"  Warning: No audio found for {filename}")

    # Move stems
    stems_source = get_stems_path(pinyin_name, STEMS_SOURCE_PATH)
    stems_dir = song_dir / "stems"
    if stems_source and stems_source.exists():
        stems_dir.mkdir(exist_ok=True)

        # Copy each stem file
        stem_files = ["vocals.wav", "drums.wav", "bass.wav", "other.wav"]
        for stem_file in stem_files:
            source_file = stems_source / stem_file
            if source_file.exists():
                dest_file = stems_dir / stem_file
                shutil.copy2(source_file, dest_file)

        # Also copy any .lrc files if present
        for lrc_file in stems_source.glob("*.lrc"):
            shutil.copy2(lrc_file, song_dir / lrc_file.name)
    else:
        print(f"  Warning: No stems found for {filename}")

    # Extract metadata for catalog
    catalog_song = Song(
        id=song_id,
        title=analysis.get("title", song_name),
        artist=analysis.get("artist", "Unknown"),
        bpm=float(analysis.get("tempo", 0)),
        key=analysis.get("key", "Unknown"),
        duration=float(analysis.get("duration", 0)),
    )

    # Determine tempo category
    if catalog_song.bpm < 90:
        catalog_song.tempo_category = "slow"
    elif catalog_song.bpm < 130:
        catalog_song.tempo_category = "medium"
    else:
        catalog_song.tempo_category = "fast"

    # Check for existing stems and LRC
    if stems_source and stems_source.exists():
        catalog_song.has_stems = True
        if (stems_source.glob("*.lrc") or (song_dir / "lyrics.lrc").exists()):
            catalog_song.has_lrc = True

    # Add to catalog
    catalog.add_song(catalog_song)

    return True, next_id + 1


def main():
    """Main migration function."""
    print("=" * 60)
    print("Stream of Worship - Song Library Migration")
    print("=" * 60)

    # Ensure directories exist
    ensure_directories()

    # Load existing catalog
    catalog = get_existing_catalog()
    next_id = get_next_song_id(catalog)

    # Load POC results
    print(f"\nLoading analysis results from: {POC_RESULTS_PATH}")
    poc_results = load_poc_results(POC_RESULTS_PATH)
    print(f"Found {len(poc_results)} songs in analysis results")

    # Load scraped lyrics
    print(f"\nLoading scraped lyrics from: {LYRICS_SOURCE_PATH}")
    lyrics_data = load_scraped_lyrics(LYRICS_SOURCE_PATH)
    print(f"Found {len(lyrics_data)} lyrics entries")

    # Migrate each song
    print(f"\nStarting migration to: {get_song_library_path()}")
    print(f"Starting ID sequence from: {next_id}")
    print("-" * 60)

    success_count = 0
    skip_count = 0

    for i, analysis in enumerate(poc_results):
        filename = analysis.get("filename", "")

        # Check if already migrated
        already_migrated = False
        for song in catalog.songs:
            if filename in song.title:
                already_migrated = True
                break

        if already_migrated:
            skip_count += 1
            print(f"[{i+1}/{len(poc_results)}] Skip (already migrated): {filename}")
            continue

        success, next_id = migrate_song(analysis, lyrics_data, next_id, catalog)
        if success:
            success_count += 1
            print(f"[{i+1}/{len(poc_results)}] Migrated: {filename}")
        else:
            print(f"[{i+1}/{len(poc_results)}] Failed: {filename}")

    # Save catalog
    print("-" * 60)
    catalog_path = get_catalog_index_path()
    catalog.save(catalog_path)
    print(f"\nSaved catalog to: {catalog_path}")

    # Summary
    print("\n" + "=" * 60)
    print("Migration Summary")
    print("=" * 60)
    print(f"Total songs in analysis: {len(poc_results)}")
    print(f"Already migrated: {skip_count}")
    print(f"Newly migrated: {success_count}")
    print(f"Total songs in catalog: {len(catalog.songs)}")
    print(f"Catalog location: {get_catalog_index_path()}")
    print("=" * 60)

    return catalog


if __name__ == "__main__":
    main()
