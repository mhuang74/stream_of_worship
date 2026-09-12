"""Shared interactive prompt helpers for admin commands.

These are generic (not lyrics- or audio-specific) input routines used by
multiple command modules.
"""

import sys

from rich.console import Console

console = Console()


def read_song_ids_from_stdin() -> list[str]:
    """Read song IDs from stdin, one per line.

    Returns:
        List of non-empty, stripped song IDs
    """
    song_ids = []
    for line in sys.stdin:
        line = line.strip()
        if line:
            song_ids.append(line)
    return song_ids


def prompt_confirmation(message: str) -> bool:
    """Prompt for y/n confirmation, return True if accepted.

    Args:
        message: Prompt message to display

    Returns:
        True if user confirms (y), False otherwise
    """
    try:
        response = input(f"{message} [y/n]: ").strip().lower()
        return response in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        return False


def prompt_choice(prompt: str, choices: list[str]) -> int:
    """Prompt the user to choose from a list of options.

    Returns:
        Index of the chosen option
    """
    console.print(f"\n[bold]{prompt}[/bold]")
    for i, choice in enumerate(choices):
        console.print(f"  [{i + 1}] {choice}")

    while True:
        try:
            selection = int(input("Enter choice: ")) - 1
            if 0 <= selection < len(choices):
                return selection
            console.print(f"[red]Please enter a number between 1 and {len(choices)}[/red]")
        except (ValueError, EOFError):
            console.print(f"[red]Please enter a number between 1 and {len(choices)}[/red]")