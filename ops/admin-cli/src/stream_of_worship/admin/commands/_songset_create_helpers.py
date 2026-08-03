"""Helper functions for ``sow-admin songset create``.

Split off from ``commands/songset.py`` to keep that file under ~700 lines.
"""

import re

import typer
from rich.console import Console
from rich.table import Table

from stream_of_worship.admin.db.models import Recording, Song
from stream_of_worship.db.app.read_client import ReadOnlyClient

_SONG_ID_RE = re.compile(r"^song_\d+$")


def resolve_song_token(
    token: str,
    read_client: ReadOnlyClient,
    console: Console,
    *,
    non_interactive: bool = False,
) -> tuple[Song, Recording]:
    """Resolve a song token (ID or title) to a ``(Song, Recording)`` pair.

    Flow:
      1. If token matches ``^song_\\d+$``, try ``get_song``. If None, warn and
         fall through to title search.
      2. Treat token as a title; search via ``search_songs(field="title")``.
      3. Zero matches → ``typer.Exit(1)``.
      4. One match → use it.
      5. Multiple matches → interactive picker (or error in non-interactive mode).
      6. Verify the song has at least one active recording.
      7. Pick the latest active recording (``imported_at DESC``).
      8. Verify ``recording.duration_seconds`` is not None.

    Args:
        token: Song ID (``song_0123``) or title string.
        read_client: Read-only DB client.
        console: Rich console for output.
        non_interactive: If True, ambiguous titles raise instead of prompting.

    Returns:
        Tuple of ``(Song, Recording)``.

    Raises:
        typer.Exit: On any resolution failure.
    """
    song: Song | None = None

    if _SONG_ID_RE.match(token):
        song = read_client.get_song(token)
        if song is None:
            console.print(
                f"[yellow]Token '{token}' looks like an ID but no song exists "
                f"with that ID — falling back to title search.[/yellow]"
            )

    if song is None:
        matches = read_client.search_songs(
            token, field="title", limit=20, include_deleted=False
        )
        if not matches:
            console.print(f"[red]No song found for token '{token}'.[/red]")
            raise typer.Exit(1)
        if len(matches) == 1:
            song = matches[0]
        else:
            if non_interactive:
                console.print(
                    f"[red]Multiple matches for '{token}' — "
                    f"supply the song_id directly.[/red]"
                )
                raise typer.Exit(1)
            song = _pick_song_interactive(token, matches, console, read_client)

    recordings = read_client.list_active_recordings_by_song_id(
        song.id, include_deleted=False
    )
    if not recordings:
        console.print(
            f"[red]No active recording for song {song.id} '{song.title}'.[/red]"
        )
        raise typer.Exit(1)

    recording = recordings[0]

    if recording.duration_seconds is None:
        console.print(
            f"[red]No duration_seconds on recording {recording.hash_prefix} "
            f"for song {song.id} '{song.title}'. "
            f"Re-run audio import or pick a different recording.[/red]"
        )
        raise typer.Exit(1)

    return song, recording


def _pick_song_interactive(
    token: str, matches: list[Song], console: Console, read_client: ReadOnlyClient
) -> Song:
    """Render a table of matches and prompt the user to pick one.

    Args:
        token: The original search token.
        matches: List of matching songs.
        console: Rich console.
        read_client: Used to fetch the latest recording's `tempo_bpm` for each matched song so the BPM column shows real tempo values.

    Returns:
        The chosen ``Song``.
    """
    console.print(f"[cyan]Multiple matches for '{token}':[/cyan]")
    table = Table()
    table.add_column("#", style="dim", justify="right")
    table.add_column("Song ID", style="dim")
    table.add_column("Title", style="cyan")
    table.add_column("Album")
    table.add_column("Key", style="magenta")
    table.add_column("BPM", style="white")
    for i, s in enumerate(matches, 1):
        recordings = read_client.list_active_recordings_by_song_id(s.id, include_deleted=False)
        bpm = recordings[0].tempo_bpm if recordings else None
        bpm_val = str(round(bpm)) if bpm is not None else "-"
        table.add_row(
            str(i),
            s.id,
            s.title,
            s.album_name or "-",
            s.musical_key or "-",
            bpm_val,
        )
    console.print(table)

    while True:
        choice = typer.prompt(f"Pick [1-{len(matches)}]", default="1")
        try:
            idx = int(choice)
            if 1 <= idx <= len(matches):
                return matches[idx - 1]
        except ValueError:
            pass
        console.print(f"[red]Invalid choice. Enter a number 1-{len(matches)}.[/red]")


def _sanitize_title_for_name(title: str) -> str:
    """Sanitize a song title for use in an auto-generated songset name.

    Strips whitespace, removes internal spaces, and drops non-printable chars.

    Args:
        title: Raw song title.

    Returns:
        Sanitized title string.
    """
    title = title.strip()
    title = title.replace(" ", "")
    title = "".join(c for c in title if c.isprintable())
    return title


def _dedupe_songset_name(name: str, existing_names: set[str]) -> str:
    """Append ``_2``, ``_3``, … to ``name`` until it is not in ``existing_names``.

    If ``name`` itself is not taken, returns it unchanged.

    Args:
        name: Desired songset name.
        existing_names: Set of names already owned by the same user.

    Returns:
        A unique name (possibly suffixed).
    """
    if name not in existing_names:
        return name
    suffix = 2
    while f"{name}_{suffix}" in existing_names:
        suffix += 1
    return f"{name}_{suffix}"


def _format_duration(seconds: float | None) -> str:
    """Format seconds as ``M:SS`` (or ``--:--`` if None).

    Args:
        seconds: Duration in seconds.

    Returns:
        Formatted duration string.
    """
    if seconds is None:
        return "--:--"
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{minutes}:{secs:02d}"
