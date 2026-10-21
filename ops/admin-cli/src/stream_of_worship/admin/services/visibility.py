"""Shared visibility-status badge and choice helpers.

Badges match the `audio list` legend; the audio commands and the LRC
editor header both render them, so the glyphs live here once.
"""

from typing import Optional

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