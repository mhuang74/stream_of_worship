"""Main CLI entry point for Stream of Worship.

Provides a unified interface for:
- Running the TUI application
- Admin/ingestion tasks
- Playlist operations
- Configuration management
"""

import argparse
import sys
from pathlib import Path

from stream_of_worship.core.paths import (
    ensure_directories,
    get_config_path,
    get_user_data_dir,
)
from stream_of_worship.core.config import Config, create_default_config


def main():
    """Main entry point for the CLI.

    When run without arguments, launches the TUI application.
    With subcommands, performs specific admin tasks.
    """
    # Ensure directories exist
    ensure_directories()

    # Ensure config exists
    config = ensure_config_exists()

    parser = argparse.ArgumentParser(
        prog="stream-of-worship",
        description="Stream of Worship - Seamless worship music transitions and lyrics videos",
    )
    parser.add_argument(
        "--version",
        action="version",
        version="%(prog)s 0.2.0"
    )

    # Subcommands
    subparsers = parser.add_subparsers(
        dest="command",
        help="Available commands",
        metavar="COMMAND"
    )

    # TUI command (default)
    tui_parser = subparsers.add_parser(
        "tui",
        help="Launch the Text User Interface (default)"
    )
    tui_parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to config file (default: user data directory)"
    )

    # Ingest subcommands
    ingest_parser = subparsers.add_parser(
        "ingest",
        help="Admin tools for song ingestion"
    )
    ingest_subparsers = ingest_parser.add_subparsers(dest="ingest_command")

    # Ingest: analyze
    analyze_parser = ingest_subparsers.add_parser(
        "analyze",
        help="Analyze audio files"
    )
    analyze_parser.add_argument(
        "--song",
        type=Path,
        required=True,
        help="Path to audio file to analyze"
    )
    analyze_parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output path for analysis JSON"
    )

    # Ingest: scrape-lyrics
    scrape_parser = ingest_subparsers.add_parser(
        "scrape-lyrics",
        help="Scrape lyrics from sop.org"
    )
    scrape_parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit number of songs to scrape"
    )
    scrape_parser.add_argument(
        "--test",
        action="store_true",
        help="Run with test song only"
    )

    # Ingest: generate-lrc
    lrc_parser = ingest_subparsers.add_parser(
        "generate-lrc",
        help="Generate LRC files using Whisper + LLM"
    )
    lrc_parser.add_argument(
        "--song-id",
        type=str,
        default=None,
        help="Song ID to generate LRC for"
    )
    lrc_parser.add_argument(
        "--all",
        action="store_true",
        help="Generate LRC for all songs in catalog"
    )

    # Ingest: generate-metadata
    metadata_parser = ingest_subparsers.add_parser(
        "generate-metadata",
        help="Generate AI metadata for songs"
    )
    metadata_parser.add_argument(
        "--song-id",
        type=str,
        default=None,
        help="Song ID to generate metadata for"
    )
    metadata_parser.add_argument(
        "--all",
        action="store_true",
        help="Generate metadata for all songs in catalog"
    )

    # Playlist subcommands
    playlist_parser = subparsers.add_parser(
        "playlist",
        help="Playlist operations"
    )
    playlist_subparsers = playlist_parser.add_subparsers(dest="playlist_command")

    # Playlist: build
    build_parser = playlist_subparsers.add_parser(
        "build",
        help="Build audio from playlist JSON"
    )
    build_parser.add_argument(
        "--from-json",
        type=Path,
        required=True,
        help="Path to playlist JSON file"
    )
    build_parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output audio path (default: auto-generated)"
    )

    # Playlist: export-video
    video_parser = playlist_subparsers.add_parser(
        "export-video",
        help="Export video from playlist"
    )
    video_parser.add_argument(
        "--from-json",
        type=Path,
        required=True,
        help="Path to playlist JSON file"
    )
    video_parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output video path (default: auto-generated)"
    )
    video_parser.add_argument(
        "--background",
        type=Path,
        default=None,
        help="Background video/image path"
    )

    # Playlist: validate
    validate_parser = playlist_subparsers.add_parser(
        "validate",
        help="Validate playlist JSON file"
    )
    validate_parser.add_argument(
        "--from-json",
        type=Path,
        required=True,
        help="Path to playlist JSON file"
    )

    # Utility subcommands
    config_parser = subparsers.add_parser(
        "config",
        help="Configuration management"
    )
    config_subparsers = config_parser.add_subparsers(dest="config_command")

    # Config: show
    show_parser = config_subparsers.add_parser(
        "show",
        help="Show current configuration"
    )

    # Config: set
    set_parser = config_subparsers.add_parser(
        "set",
        help="Set a configuration value"
    )
    set_parser.add_argument(
        "key",
        type=str,
        help="Configuration key"
    )
    set_parser.add_argument(
        "value",
        type=str,
        help="Configuration value"
    )

    # Migration subcommands
    migrate_parser = subparsers.add_parser(
        "migrate",
        help="Data migration tools"
    )
    migrate_subparsers = migrate_parser.add_subparsers(dest="migrate_command")

    # Migrate: from-legacy
    legacy_parser = migrate_subparsers.add_parser(
        "from-legacy",
        help="Migrate data from legacy POC structure"
    )

    # Parse arguments
    args = parser.parse_args()

    # Execute command
    if args.command is None:
        # Default: launch TUI
        launch_tui(config)
    elif args.command == "tui":
        launch_tui(config, args.config)
    elif args.command == "ingest":
        handle_ingest(args, config)
    elif args.command == "playlist":
        handle_playlist(args, config)
    elif args.command == "config":
        handle_config(args, config)
    elif args.command == "migrate":
        handle_migration(args)


def launch_tui(config: Config, config_path: Path = None):
    """Launch the TUI application.

    Args:
        config: Configuration object
        config_path: Optional path to config file
    """
    try:
        from stream_of_worship.tui.app import TransitionBuilderApp

        if config_path is None:
            config_path = get_config_path()

        app = TransitionBuilderApp(config_path)
        app.run()

    except ImportError as e:
        print(f"TUI dependencies not installed: {e}")
        print("Install with: uv add --extra tui textual pydub")
        sys.exit(1)


def handle_ingest(args, config: Config):
    """Handle ingestion subcommands.

    Args:
        args: Parsed arguments
        config: Configuration object
    """
    if args.ingest_command == "analyze":
        print(f"Analyzing: {args.song}")
        # TODO: Implement analysis command
        print("Analysis command not yet implemented. Use poc_analysis_allinone.py directly.")

    elif args.ingest_command == "scrape-lyrics":
        from poc.lyrics_scraper import main as scrape_main

        sys.argv = ["lyrics_scraper.py"]
        if args.test:
            sys.argv.append("--test")
        if args.limit:
            sys.argv.extend(["--limit", str(args.limit)])

        scrape_main()

    elif args.ingest_command == "generate-lrc":
        if not args.song_id and not args.all:
            print("Error: Specify --song-id or --all")
            sys.exit(1)

        from stream_of_worship.ingestion.lrc_generator import LRCGenerator

        generator = LRCGenerator(
            llm_model=config.llm_model,
            api_key=config.openrouter_api_key,
        )

        if args.song_id:
            # Generate LRC for single song
            from stream_of_worship.core.paths import get_song_dir, get_catalog_index_path
            from stream_of_worship.core.catalog import CatalogIndex

            catalog = CatalogIndex.load()
            song = catalog.get_song(args.song_id)

            if not song:
                print(f"Error: Song not found in catalog: {args.song_id}")
                sys.exit(1)

            song_dir = get_song_dir(song.id)
            audio_path = song_dir / "audio.mp3"
            lyrics_path = song_dir / "lyrics.json"
            analysis_path = song_dir / "analysis.json"
            output_path = song_dir / "lyrics.lrc"

            # Load data
            import json
            with lyrics_path.open("r", encoding="utf-8") as f:
                lyrics_data = json.load(f)
                lyrics_text = lyrics_data.get("lyrics_raw", lyrics_data.get("lyrics_lines", ""))

            with analysis_path.open("r", encoding="utf-8") as f:
                analysis = json.load(f)
                beats = analysis.get("beats", [])

            # Generate LRC
            success = generator.generate(audio_path, lyrics_text, beats, output_path)

            if success:
                print(f"Successfully generated LRC: {output_path}")
                # Update catalog
                song.has_lrc = True
                catalog.save()
            else:
                print(f"Failed to generate LRC for: {args.song_id}")

        elif args.all:
            # Generate LRC for all songs
            from stream_of_worship.core.paths import get_catalog_index_path
            from stream_of_worship.core.catalog import CatalogIndex

            catalog = CatalogIndex.load()

            # Prepare batch
            songs_to_process = []
            for song in catalog.songs:
                if song.has_lrc:
                    continue

                song_dir = get_song_dir(song.id)
                audio_path = song_dir / "audio.mp3"
                lyrics_path = song_dir / "lyrics.json"
                analysis_path = song_dir / "analysis.json"
                output_path = song_dir / "lyrics.lrc"

                if not (audio_path.exists() and lyrics_path.exists() and analysis_path.exists()):
                    print(f"Skipping {song.id}: missing files")
                    continue

                import json
                with lyrics_path.open("r", encoding="utf-8") as f:
                    lyrics_data = json.load(f)
                    lyrics_text = lyrics_data.get("lyrics_raw", lyrics_data.get("lyrics_lines", ""))

                with analysis_path.open("r", encoding="utf-8") as f:
                    analysis = json.load(f)
                    beats = analysis.get("beats", [])

                songs_to_process.append((audio_path, lyrics_text, beats, output_path))

            if not songs_to_process:
                print("No songs to process")
                return

            print(f"Generating LRC for {len(songs_to_process)} songs...")
            success, failures = generator.batch_generate(
                songs_to_process,
                max_failures=10,
                progress_callback=lambda msg, pct: print(f"{msg} ({pct*100:.0f}%)")
            )

            print(f"\nResults: {success} succeeded, {failures} failed")

            # Update catalog
            for _, _, _, output_path in songs_to_process:
                song_id = output_path.parent.name
                song = catalog.get_song(song_id)
                if song:
                    song.has_lrc = True
            catalog.save()

    elif args.ingest_command == "generate-metadata":
        if not args.song_id and not args.all:
            print("Error: Specify --song-id or --all")
            sys.exit(1)

        from stream_of_worship.ingestion.metadata_generator import MetadataGenerator

        generator = MetadataGenerator(
            model=config.llm_model,
            api_key=config.openrouter_api_key,
        )

        if args.song_id:
            # Generate metadata for single song
            from stream_of_worship.core.paths import get_song_dir, get_catalog_index_path
            from stream_of_worship.core.catalog import CatalogIndex

            catalog = CatalogIndex.load()
            song = catalog.get_song(args.song_id)

            if not song:
                print(f"Error: Song not found in catalog: {args.song_id}")
                sys.exit(1)

            song_dir = get_song_dir(song.id)
            lyrics_path = song_dir / "lyrics.json"

            import json
            with lyrics_path.open("r", encoding="utf-8") as f:
                lyrics_data = json.load(f)
                lyrics_text = lyrics_data.get("lyrics_raw", "")

            metadata = generator.generate(
                song.title,
                song.artist,
                lyrics_text,
                song.key,
                song.bpm,
            )

            # Save metadata
            metadata_path = song_dir / "metadata.json"
            with metadata_path.open("w", encoding="utf-8") as f:
                json.dump(metadata.to_dict(), f, indent=2, ensure_ascii=False)

            # Update catalog
            song.themes = metadata.themes
            song.ai_summary = metadata.ai_summary
            song.bible_verses = metadata.bible_verses
            song.vocalist = metadata.vocalist
            catalog.save()

            print(f"Generated metadata for: {song.title}")
            print(f"  Summary: {metadata.ai_summary}")
            print(f"  Themes: {', '.join(metadata.themes)}")
            print(f"  Verses: {', '.join(metadata.bible_verses)}")
            print(f"  Vocalist: {metadata.vocalist}")

        elif args.all:
            # Generate metadata for all songs
            from stream_of_worship.core.paths import get_catalog_index_path
            from stream_of_worship.core.catalog import CatalogIndex

            catalog = CatalogIndex.load()

            # Prepare batch
            songs_to_process = []
            for song in catalog.songs:
                if song.themes:  # Skip if already has themes
                    continue

                song_dir = get_song_dir(song.id)
                lyrics_path = song_dir / "lyrics.json"

                if not lyrics_path.exists():
                    print(f"Skipping {song.id}: no lyrics file")
                    continue

                import json
                with lyrics_path.open("r", encoding="utf-8") as f:
                    lyrics_data = json.load(f)
                    lyrics_text = lyrics_data.get("lyrics_raw", "")

                songs_to_process.append((song.title, song.artist, lyrics_text, song.key, song.bpm))

            if not songs_to_process:
                print("No songs to process")
                return

            print(f"Generating metadata for {len(songs_to_process)} songs...")

            results = generator.batch_generate(
                songs_to_process,
                progress_callback=lambda msg, pct: print(f"{msg} ({pct*100:.0f}%)")
            )

            # Update catalog
            for i, (title, artist, _, _, _) in enumerate(songs_to_process):
                song_id = f"{title}_{artist}".replace(" ", "_").lower()
                song = None
                for s in catalog.songs:
                    if title.lower() in s.title.lower():
                        song = s
                        break

                if song and song_id in results:
                    metadata = results[song_id]
                    song.themes = metadata.themes
                    song.ai_summary = metadata.ai_summary
                    song.bible_verses = metadata.bible_verses
                    song.vocalist = metadata.vocalist

                    # Save metadata file
                    song_dir = get_song_dir(song.id)
                    metadata_path = song_dir / "metadata.json"
                    with metadata_path.open("w", encoding="utf-8") as f:
                        json.dump(metadata.to_dict(), f, indent=2, ensure_ascii=False)

            catalog.save()
            print(f"Updated metadata for {len(results)} songs")


def handle_playlist(args, config: Config):
    """Handle playlist subcommands.

    Args:
        args: Parsed arguments
        config: Configuration object
    """
    if args.playlist_command == "validate":
        from stream_of_worship.tui.models.playlist import Playlist

        try:
            playlist = Playlist.load(args.from_json)
            print(f"Valid playlist: {playlist.name}")
            print(f"  Songs: {playlist.song_count}")
            print(f"  Duration: {playlist.metadata.formatted_duration}")
        except Exception as e:
            print(f"Invalid playlist: {e}")
            sys.exit(1)

    elif args.playlist_command == "build":
        print("Playlist build command not yet implemented.")
        print("Use the TUI for audio generation.")

    elif args.playlist_command == "export-video":
        print("Video export command not yet implemented.")
        print("Use the TUI for video generation.")


def handle_config(args, config: Config):
    """Handle configuration subcommands.

    Args:
        args: Parsed arguments
        config: Configuration object
    """
    if args.config_command == "show":
        print("Configuration:")
        print(f"  Audio folder: {config.audio_folder}")
        print(f"  Output folder: {config.output_folder}")
        print(f"  Stems folder: {config.stems_folder}")
        print(f"  Analysis JSON: {config.analysis_json}")
        print(f"  Lyrics folder: {config.lyrics_folder}")
        print(f"  Error logging: {config.error_logging}")
        print(f"  Session logging: {config.session_logging}")
        print(f"  Audio format: {config.audio_format}")
        print(f"  Audio bitrate: {config.audio_bitrate}")
        print(f"  Video resolution: {config.video_resolution}")
        print(f"  LLM model: {config.llm_model}")
        if config.openrouter_api_key:
            print(f"  OpenRouter API key: [SET]")
        else:
            print(f"  OpenRouter API key: [NOT SET]")

    elif args.config_command == "set":
        if hasattr(config, args.key):
            # Try to convert to appropriate type
            current_value = getattr(config, args.key)
            if isinstance(current_value, bool):
                value = args.value.lower() in ("true", "1", "yes")
            elif isinstance(current_value, int):
                value = int(args.value)
            elif isinstance(current_value, float):
                value = float(args.value)
            elif isinstance(current_value, list):
                value = [x.strip() for x in args.value.split(",")]
            elif isinstance(current_value, Path):
                value = Path(args.value)
            else:
                value = args.value

            setattr(config, args.key, value)
            config.save()
            print(f"Set {args.key} = {value}")
        else:
            print(f"Unknown config key: {args.key}")


def handle_migration(args):
    """Handle migration subcommands.

    Args:
        args: Parsed arguments
    """
    if args.migrate_command == "from-legacy":
        # Import the migration script
        from scripts.migrate_song_library import main as migrate_main
        migrate_main()
    else:
        print("Unknown migration command")


if __name__ == "__main__":
    main()
