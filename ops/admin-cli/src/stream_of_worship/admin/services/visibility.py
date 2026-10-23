"""Shared visibility-status badge and choice helpers.

Badges match the `audio list` legend; the audio commands and the LRC
editor header both render them, so the glyphs live here once.
"""

from typing import Optional

import typer
from rich.console import Console

from stream_of_worship.admin.db.models import Recording

# Ordered for the status menu; mirrors db.client._VALID_VISIBILITY_STATUSES
# (a status added there must be added here, or the menu can never offer it).
VISIBILITY_STATUSES: tuple[str, ...] = ("published", "review", "hold")
VISIBILITY_BADGES: dict[str, str] = {"published": "●", "review": "◐", "hold": "○"}
NO_VISIBILITY_BADGE = "·"
_VISIBILITY_COLORS = {"published": "green", "review": "yellow", "hold": "dim"}


def visibility_badge(status: Optional[str]) -> str:
    return VISIBILITY_BADGES.get(status or "", NO_VISIBILITY_BADGE)


def visibility_label(status: Optional[str]) -> str:
    """Badge plus status word, e.g. ``◐ review``; unset renders ``· none``."""
    return f"{visibility_badge(status)} {status or 'none'}"


def format_visibility(status: Optional[str]) -> str:
    """Rich-markup coloured badge (exact legacy `_colorize_visibility` output)."""
    color = _VISIBILITY_COLORS.get(status or "", "dim")
    return f"[{color}]{visibility_badge(status)}[/{color}]"


def visibility_alternatives(current: Optional[str]) -> list[str]:
    """Every valid status except ``current`` ("" / None = unset)."""
    current = current or ""
    return [status for status in VISIBILITY_STATUSES if status != current]


PROTECTED_VISIBILITY_STATUS = "published"


def is_published(recording: Optional[Recording]) -> bool:
    """True when the recording is published (admin-validated, user-visible)."""
    return recording is not None and recording.visibility_status == PROTECTED_VISIBILITY_STATUS


def reprocess_blocked_reason(song_id: str, hash_prefix: str, action: str) -> str:
    """One-line refusal plus the exact remediation command."""
    return (
        f"Refusing to {action}: recording {hash_prefix} for {song_id} is "
        f"'{PROTECTED_VISIBILITY_STATUS}'. Change visibility first: "
        f"sow-admin audio set-visibility {song_id} --status review"
    )


def ensure_reprocessable(
    recording: Optional[Recording],
    *,
    song_id: str,
    action: str,
    console: Console,
) -> None:
    """Hard-block re-processing of a published recording.

    Raises:
        typer.Exit: exit code 1 when ``recording`` is published.
    """
    if is_published(recording):
        console.print(
            f"[red]{reprocess_blocked_reason(song_id, recording.hash_prefix, action)}[/red]"
        )
        raise typer.Exit(1)
