#!/usr/bin/env python3
"""
Interactive Worship Transition Builder

A text-based interactive tool for creating worship song transitions based on
PDF concepts (Overlap, Short Gap, No Break) with real-time parameter adjustment.

Version: 1.0.0
"""

import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.layout import Layout
from rich.text import Text

from .models import TransitionConfig, TransitionType, Song, Section
from .utils import MetadataLoader, export_transition, generate_default_filename
from .audio import StemLoader, TransitionGenerator, AudioPlayer


console = Console()


def display_song_list(songs: list[Song], compatibility_scores: dict[str, float] = None) -> None:
    """Display list of available songs with optional compatibility scores."""
    table = Table(title="Available Songs")
    table.add_column("#", style="cyan", width=4)
    table.add_column("Filename", style="green")
    table.add_column("Key", style="yellow", width=12)
    table.add_column("BPM", style="magenta", width=8)
    table.add_column("Duration", style="blue", width=10)

    if compatibility_scores:
        table.add_column("Best Match", style="bright_cyan", width=12)

    for idx, song in enumerate(songs, 1):
        row = [
            str(idx),
            song.filename,
            song.key,
            f"{song.tempo:.1f}",
            song.get_duration_str()
        ]

        if compatibility_scores and song.filename in compatibility_scores:
            score = compatibility_scores[song.filename]
            row.append(f"{score:.1f}/100")

        table.add_row(*row)

    console.print(table)


def display_section_list(song: Song) -> None:
    """Display sections for a song."""
    table = Table(title=f"Sections - {song.filename}")
    table.add_column("#", style="cyan", width=4)
    table.add_column("Label", style="green", width=12)
    table.add_column("Time Range", style="yellow")
    table.add_column("Duration", style="magenta", width=10)
    table.add_column("Energy", style="blue", width=10)

    for section in song.sections:
        table.add_row(
            str(section.index + 1),
            section.label.capitalize(),
            section.get_time_range_str(),
            section.get_duration_str(),
            f"{section.energy_score:.1f}/100"
        )

    console.print(table)


def filter_compatible_songs(
    section_a: Section,
    all_songs: list[Song],
    threshold: float = 80.0
) -> tuple[list[Song], dict[str, float]]:
    """
    Filter songs based on compatibility with the selected section.

    Args:
        section_a: The selected section from Song A
        all_songs: List of all available songs
        threshold: Minimum compatibility score (default: 80.0)

    Returns:
        Tuple of (filtered_songs, compatibility_scores_dict)
        where compatibility_scores_dict maps filename to best compatibility score
    """
    compatibility_scores = {}

    # Calculate best compatibility score for each song
    for song in all_songs:
        max_score = 0.0
        for section in song.sections:
            # Skip if same song and same section as section_a
            if (song.filename == section_a.song_filename and
                section.index == section_a.index):
                continue

            scores = section_a.calculate_compatibility(section)
            overall_score = scores['overall_score']
            max_score = max(max_score, overall_score)

        if max_score > 0:
            compatibility_scores[song.filename] = max_score

    # Filter songs that meet the threshold
    filtered_songs = [
        song for song in all_songs
        if compatibility_scores.get(song.filename, 0) >= threshold
    ]

    return filtered_songs, compatibility_scores


def display_transition_config(config: TransitionConfig) -> None:
    """Display current transition configuration."""
    console.print("\n" + "=" * 70)
    console.print(Panel.fit(
        "[bold cyan]Transition Configuration[/bold cyan]",
        border_style="cyan"
    ))

    # Transition type
    console.print(f"  Type: [bold]{config.transition_type.display_name}[/bold]")
    console.print(f"  Description: {config.transition_type.description}")

    # Parameters
    console.print("\n[bold]Parameters:[/bold]")
    console.print(f"  transition_window:  {config.transition_window} beats")

    # Show seconds conversion if sections are selected
    if config.section_a:
        tw_seconds = config.get_transition_window_seconds()
        console.print(f"                      ({tw_seconds:.1f}s @ {config.section_a.tempo:.0f} BPM)")

    if config.transition_type == TransitionType.OVERLAP:
        console.print(f"  overlap_window:     {config.overlap_window} beats")
        if config.section_a:
            ow_seconds = config.get_overlap_window_seconds()
            console.print(f"                      ({ow_seconds:.1f}s @ {config.section_a.tempo:.0f} BPM)")
    elif config.transition_type == TransitionType.SHORT_GAP:
        console.print(f"  gap_window:         {config.gap_window} beats")
        if config.section_a:
            gw_seconds = config.get_gap_window_seconds()
            console.print(f"                      ({gw_seconds:.1f}s @ {config.section_a.tempo:.0f} BPM)")

    console.print(f"  stems_to_fade:      {', '.join(config.stems_to_fade)}")
    console.print(f"  fade_window_pct:    {config.fade_window_pct}%")

    # Compatibility
    if config.song_a and config.song_b and config.section_a and config.section_b:
        config.update_compatibility_scores()
        console.print(f"\n[bold]Compatibility Score:[/bold] {config.compatibility_score:.1f}/100")
        console.print(f"  Tempo:      {config.tempo_score:.1f}/100")
        console.print(f"  Key:        {config.key_score:.1f}/100")
        console.print(f"  Energy:     {config.energy_score:.1f}/100")
        console.print(f"  Embeddings: {config.embeddings_score:.1f}/100")

    console.print("=" * 70 + "\n")


def main():
    """Main application loop."""
    console.print("\n[bold cyan]Interactive Worship Transition Builder v1.0[/bold cyan]\n")

    try:
        # Initialize components
        console.print("[yellow]Loading metadata...[/yellow]")
        metadata_loader = MetadataLoader()
        songs = metadata_loader.get_song_list()
        console.print(f"[green]✓ Loaded {len(songs)} songs[/green]\n")

        stem_loader = StemLoader()
        generator = TransitionGenerator(stem_loader)
        player = AudioPlayer()

        # Main loop - allows generating multiple transitions
        while True:
            # Initialize config
            config = TransitionConfig()

            # Step 1: Select Song A
            console.print("[bold]Step 1: Select Song A[/bold]")
            display_song_list(songs)
            song_a_idx = int(console.input("\nEnter song number (1-{}): ".format(len(songs)))) - 1
            config.song_a = songs[song_a_idx]
            console.print(f"[green]✓ Selected: {config.song_a.filename}[/green]\n")

            # Step 2: Select Section A
            console.print("[bold]Step 2: Select Section from Song A[/bold]")
            if len(config.song_a.sections) == 1:
                # Auto-select if only one section available
                config.section_a = config.song_a.sections[0]
                console.print(f"[green]✓ Auto-selected (only section): {config.section_a.label}[/green]\n")
            else:
                display_section_list(config.song_a)
                section_a_idx = int(console.input(f"\nEnter section number (1-{len(config.song_a.sections)}): ")) - 1
                config.section_a = config.song_a.sections[section_a_idx]
                console.print(f"[green]✓ Selected: {config.section_a.label}[/green]\n")

            # Step 3: Select Song B (filtered by compatibility)
            console.print("[bold]Step 3: Select Song B[/bold]")
            console.print("[yellow]Calculating compatibility scores...[/yellow]")

            # Filter songs with compatibility >= 80
            compatible_songs, compatibility_scores = filter_compatible_songs(
                config.section_a,
                songs,
                threshold=80.0
            )

            if not compatible_songs:
                console.print("[yellow]⚠ No songs found with compatibility ≥ 80[/yellow]")
                console.print("[yellow]Showing all songs instead...[/yellow]\n")
                compatible_songs = songs
                # Recalculate without threshold for display
                _, compatibility_scores = filter_compatible_songs(
                    config.section_a,
                    songs,
                    threshold=0.0
                )
            else:
                console.print(f"[green]✓ Found {len(compatible_songs)} compatible songs (≥80 score)[/green]\n")

            display_song_list(compatible_songs, compatibility_scores)
            song_b_idx = int(console.input("\nEnter song number (1-{}): ".format(len(compatible_songs)))) - 1
            config.song_b = compatible_songs[song_b_idx]
            console.print(f"[green]✓ Selected: {config.song_b.filename}[/green]\n")

            # Step 4: Select Section B
            console.print("[bold]Step 4: Select Section from Song B[/bold]")
            if len(config.song_b.sections) == 1:
                # Auto-select if only one section available
                config.section_b = config.song_b.sections[0]
                console.print(f"[green]✓ Auto-selected (only section): {config.section_b.label}[/green]\n")
            else:
                display_section_list(config.song_b)
                section_b_idx = int(console.input(f"\nEnter section number (1-{len(config.song_b.sections)}): ")) - 1
                config.section_b = config.song_b.sections[section_b_idx]
                console.print(f"[green]✓ Selected: {config.section_b.label}[/green]\n")

            # Step 5: Choose Transition Type
            console.print("[bold]Step 5: Choose Transition Type[/bold]")
            console.print("  [1] Overlap (Intro Overlap)")
            console.print("  [2] Short Gap")
            console.print("  [3] No Break")
            type_choice = int(console.input("\nEnter choice (1-3): "))

            if type_choice == 1:
                config.transition_type = TransitionType.OVERLAP
            elif type_choice == 2:
                config.transition_type = TransitionType.SHORT_GAP
            elif type_choice == 3:
                config.transition_type = TransitionType.NO_BREAK

            # Reset to defaults for chosen type
            config.reset_to_defaults()
            console.print(f"[green]✓ Selected: {config.transition_type.display_name}[/green]\n")

            # Display configuration
            display_transition_config(config)

            # Step 6: Adjust Parameters (simplified - just ask if they want to change)
            adjust = console.input("Adjust parameters? (y/n) [n]: ").lower()
            if adjust == 'y':
                tw = console.input(f"transition_window [{config.transition_window} beats]: ")
                if tw:
                    config.transition_window = float(tw)

                if config.transition_type == TransitionType.OVERLAP:
                    ow = console.input(f"overlap_window [{config.overlap_window} beats]: ")
                    if ow:
                        config.overlap_window = float(ow)
                elif config.transition_type == TransitionType.SHORT_GAP:
                    gw = console.input(f"gap_window [{config.gap_window} beats]: ")
                    if gw:
                        config.gap_window = float(gw)

                fade_pct = console.input(f"fade_window_pct [{config.fade_window_pct}%]: ")
                if fade_pct:
                    config.fade_window_pct = int(fade_pct)

                # Display updated config
                display_transition_config(config)

            # Step 7: Generate and Preview
            generate = console.input("\nGenerate transition? (y/n) [y]: ").lower()
            if generate != 'n':
                console.print("\n[yellow]Generating transition...[/yellow]")
                audio_data, metadata = generator.generate(config)
                console.print(f"[green]✓ Generated transition ({metadata['duration']:.1f}s)[/green]")

                # Preview loop - allow multiple replays
                continue_loop = True
                while continue_loop:
                    console.print("\n[bold]Options:[/bold]")
                    console.print("  [p] Play/Replay transition", markup=False)
                    console.print("  [s] Save transition", markup=False)
                    console.print("  [e] Edit parameters and regenerate", markup=False)
                    console.print("  [n] Generate new transition", markup=False)
                    console.print("  [q] Quit", markup=False)

                    choice = console.input("\nChoice (p/s/e/n/q) [p]: ").lower() or 'p'

                    if choice == 'p':
                        player.play(audio_data, blocking=True)

                    elif choice == 's':
                        default_name = generate_default_filename(config)
                        output_dir = Path("section_transitions")
                        output_path = output_dir / default_name

                        custom_name = console.input(f"\nFilename [{default_name}]: ")
                        if custom_name:
                            output_path = output_dir / custom_name

                        audio_path, metadata_path = export_transition(
                            audio_data,
                            config,
                            output_path,
                            metadata=metadata
                        )
                        console.print("[green]✓ Transition saved successfully![/green]")
                        console.print(f"  Audio: {audio_path}")
                        console.print(f"  Metadata: {metadata_path}")

                    elif choice == 'e':
                        # Edit parameters and regenerate
                        console.print("\n[bold cyan]Edit Parameters[/bold cyan]")
                        display_transition_config(config)

                        console.print("[yellow]Enter new values (press Enter to keep current)[/yellow]")

                        tw = console.input(f"transition_window [{config.transition_window} beats]: ")
                        if tw:
                            config.transition_window = float(tw)

                        if config.transition_type == TransitionType.OVERLAP:
                            ow = console.input(f"overlap_window [{config.overlap_window} beats]: ")
                            if ow:
                                config.overlap_window = float(ow)
                        elif config.transition_type == TransitionType.SHORT_GAP:
                            gw = console.input(f"gap_window [{config.gap_window} beats]: ")
                            if gw:
                                config.gap_window = float(gw)

                        fade_pct = console.input(f"fade_window_pct [{config.fade_window_pct}%]: ")
                        if fade_pct:
                            config.fade_window_pct = int(fade_pct)

                        # Regenerate with new parameters
                        console.print("\n[yellow]Regenerating transition...[/yellow]")
                        try:
                            audio_data, metadata = generator.generate(config)
                            console.print(f"[green]✓ Regenerated transition ({metadata['duration']:.1f}s)[/green]")
                        except Exception as e:
                            console.print(f"[bold red]ERROR: {e}[/bold red]")
                            console.print("[yellow]Keeping previous transition[/yellow]")

                    elif choice == 'n':
                        # Break inner loop to generate new transition
                        console.print("\n" + "="*70 + "\n")
                        continue_loop = False

                    elif choice == 'q':
                        # Break both loops to quit
                        console.print("\n[bold cyan]Thank you for using Interactive Transition Builder![/bold cyan]\n")
                        return

                    else:
                        console.print("[yellow]Invalid choice, please try again[/yellow]")
            else:
                # User chose not to generate, ask if they want to quit
                quit_choice = console.input("\nQuit? (y/n) [n]: ").lower()
                if quit_choice == 'y':
                    console.print("\n[bold cyan]Thank you for using Interactive Transition Builder![/bold cyan]\n")
                    return

    except KeyboardInterrupt:
        console.print("\n\n[yellow]Interrupted by user[/yellow]")
        sys.exit(0)
    except Exception as e:
        console.print(f"\n[bold red]ERROR: {e}[/bold red]")
        sys.exit(1)


if __name__ == "__main__":
    main()
