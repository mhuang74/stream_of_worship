"""Shared interactive prompt helpers for admin commands.

These are generic (not lyrics- or audio-specific) input routines used by
multiple command modules.
"""

import sys
from typing import Optional

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


def prompt_typed_yes(message: str) -> bool:
    """Prompt requiring the full word 'yes' to confirm.

    Unlike :func:`prompt_confirmation`, a bare Enter, "y", or any other
    input declines — intended for hard-to-reverse actions such as
    publishing a recording. Case-insensitive; EOF/Ctrl+C declines.
    """
    try:
        response = input(f"{message} [type 'yes' to confirm]: ").strip().lower()
        return response == "yes"
    except (EOFError, KeyboardInterrupt):
        return False


def prompt_enter_or_no(message: str) -> bool:
    """Prompt where a bare Enter proceeds and 'n'/'no' (or any other input) stops.

    Inverse of :func:`prompt_typed_yes`: for flow-control questions such as
    "continue to the next song", ENTER is the safe, expected advance key.
    EOF/Ctrl+C stops.
    """
    try:
        response = input(f"{message} [Enter=yes, n=no]: ").strip().lower()
        return response == ""
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


def prompt_choice_or_none(prompt: str, choices: list[str]) -> Optional[int]:
    """Numbered menu where any non-answer means "leave unchanged".

    Unlike :func:`prompt_choice`, this never loops: empty input, EOF,
    Ctrl+C, non-integer text, or an out-of-range number all return None
    so finite stdin in tests and non-interactive runs cannot hang.

    Returns:
        0-based index of the chosen option, or None for no change.
    """
    console.print(f"\n[bold]{prompt}[/bold]")
    for i, choice in enumerate(choices):
        console.print(f"  [{i + 1}] {choice}")

    try:
        raw = input(f"Enter choice [1-{len(choices)}, Enter=leave unchanged]: ").strip()
    except (EOFError, KeyboardInterrupt):
        return None
    if not raw:
        return None
    try:
        selection = int(raw) - 1
    except ValueError:
        return None
    if 0 <= selection < len(choices):
        return selection
    return None