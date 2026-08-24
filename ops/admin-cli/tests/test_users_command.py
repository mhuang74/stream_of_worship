"""Tests for the 'users' CLI command cascade preview."""

from rich.console import Console

import stream_of_worship.admin.commands.users as users_mod
from stream_of_worship.admin.commands.users import _print_cascade_preview

_ALL_TABLES = [
    "songsets",
    "songset_items",
    "user_settings",
    "user_lrc_override",
    "lyric_mark",
    "songset_share",
    "account",
    "session",
]


def _render(preview):
    console = Console(record=True, width=200)
    original = users_mod.console
    users_mod.console = console
    try:
        _print_cascade_preview(preview)
    finally:
        users_mod.console = original
    return console.export_text()


def test_cascade_preview_skips_empty_tables_and_totals():
    preview = {k: [] for k in _ALL_TABLES}
    preview["songsets"] = [
        {"id": 1, "name": "Set A", "description": None, "created_at": "2026-01-01"},
    ]
    preview["songset_items"] = [
        {"id": 1, "songset_id": 1, "song_id": "s1", "position": 0, "created_at": "2026-01-01"},
    ]
    text = _render(preview)
    assert "Songsets" in text
    assert "Songset Items" in text
    assert "(no rows)" not in text
    assert "User Settings" not in text
    assert "Total: 2 row(s) will be cascade-deleted." in text


def test_cascade_preview_all_empty_prints_no_data_line():
    preview = {k: [] for k in _ALL_TABLES}
    text = _render(preview)
    assert "No cascade data to delete." in text
    assert "(no rows)" not in text
    assert "Total:" not in text
