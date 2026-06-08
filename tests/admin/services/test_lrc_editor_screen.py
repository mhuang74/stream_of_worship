"""Textual screen tests for the admin LRC editor."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from textual.widgets import DataTable, Input

from stream_of_worship.admin.editor.app import LRCEditorApp
from stream_of_worship.admin.editor.footer import GroupedFooter
from stream_of_worship.admin.editor.screen import CurrentLyricDisplay, StatusIndicator
from stream_of_worship.admin.editor.state import EditorState
from stream_of_worship.admin.services.lrc_parser import LRCLine
from stream_of_worship.admin.services.playback import PlaybackState
from stream_of_worship.admin.services.r2 import R2ObjectIdentity


class _PlaybackStub:
    state = PlaybackState.STOPPED
    position_seconds = 0.0
    duration_seconds = 120.0

    def set_callbacks(self, *args, **kwargs):
        pass

    def load(self, path: Path):
        pass

    def stop(self, *args, **kwargs):
        pass

    def toggle_play_pause(self):
        pass

    def skip_forward(self, seconds: float):
        pass

    def skip_backward(self, seconds: float):
        pass

    def seek(self, seconds: float):
        self.position_seconds = seconds

    def pause(self):
        pass

    def play(self, *args, **kwargs):
        pass


def _make_app(line_count: int) -> tuple[LRCEditorApp, EditorState]:
    lines = [
        LRCLine(time_seconds=float(index), text=f"Line {index + 1}", raw_timestamp="[00:00.00]")
        for index in range(line_count)
    ]
    return _make_app_with_lines(lines)


def _make_app_with_lines(lines: list[LRCLine]) -> tuple[LRCEditorApp, EditorState]:
    state = EditorState(
        timed_lines=lines,
        preserved_lines=[],
        original_serialized="",
        original_preserved_lines=[],
        transcribed_identity=R2ObjectIdentity(exists=False),
    )
    app = LRCEditorApp(
        editor_state=state,
        playback_service=_PlaybackStub(),
        cache_dir=Path("/tmp"),
        r2_client=MagicMock(),
        db_client=MagicMock(),
        hash_prefix="abc123",
        original_transcribed_content=None,
    )
    return app, state


def _bottom(widget) -> int:
    return widget.region.y + widget.region.height


@pytest.mark.asyncio
async def test_down_navigation_keeps_fiftieth_line_selected():
    app, state = _make_app(line_count=50)

    async with app.run_test(size=(80, 12)) as pilot:
        await pilot.pause()
        table = app.screen.query_one("#line-table", DataTable)

        assert app.focused is table

        for _ in range(49):
            await pilot.press("down")
            await pilot.pause()

        assert state.selected_index == 49
        assert table.cursor_row == 49
        assert table.scroll_y <= 50 < table.scroll_y + table.region.height
        assert str(table.get_cell_at((48, 0))) == "49"
        assert str(table.get_cell_at((49, 0))) == ">50"

        for _ in range(3):
            await pilot.press("down")
            await pilot.pause()

        assert state.selected_index == 49
        assert table.cursor_row == 49
        assert table.scroll_y <= 50 < table.scroll_y + table.region.height
        assert str(table.get_cell_at((48, 0))) == "49"
        assert str(table.get_cell_at((49, 0))) == ">50"


@pytest.mark.asyncio
async def test_down_navigation_does_not_change_line_while_editing_text():
    app, state = _make_app(line_count=3)

    async with app.run_test(size=(80, 12)) as pilot:
        await pilot.pause()

        await pilot.press("e")
        await pilot.pause()

        assert isinstance(app.focused, Input)
        await pilot.press("down")
        await pilot.pause()

        assert state.selected_index == 0


@pytest.mark.asyncio
async def test_small_terminal_layout_keeps_footer_out_of_lyrics_viewport():
    app, _ = _make_app(line_count=50)

    async with app.run_test(size=(80, 12)) as pilot:
        await pilot.pause()

        editor_body = app.screen.query_one("#editor-body")
        table = app.screen.query_one("#line-table", DataTable)
        edit_panel = app.screen.query_one("#edit-panel")
        status = app.screen.query_one(StatusIndicator)
        footer = app.screen.query_one(GroupedFooter)

        assert _bottom(editor_body) <= footer.region.y
        assert _bottom(table) <= edit_panel.region.y
        assert _bottom(edit_panel) <= status.region.y
        assert _bottom(status) <= footer.region.y

        footer_bottom = _bottom(footer)
        for child in footer.query("*"):
            assert child.region.y >= footer.region.y
            assert _bottom(child) <= footer_bottom


@pytest.mark.asyncio
async def test_page_keys_scroll_without_changing_selected_line():
    app, state = _make_app(line_count=50)

    async with app.run_test(size=(80, 12)) as pilot:
        await pilot.pause()
        table = app.screen.query_one("#line-table", DataTable)

        assert state.selected_index == 0
        assert table.cursor_row == 0
        assert table.scroll_y == 0

        await pilot.press("pagedown")
        await pilot.pause()

        assert table.scroll_y > 0
        assert state.selected_index == 0
        assert table.cursor_row == 0
        assert str(table.get_cell_at((0, 0))) == ">1"

        await pilot.press("pageup")
        await pilot.pause()

        assert table.scroll_y == 0
        assert state.selected_index == 0
        assert table.cursor_row == 0
        assert str(table.get_cell_at((0, 0))) == ">1"


@pytest.mark.asyncio
async def test_continuous_preview_shows_blank_before_first_line():
    app, state = _make_app_with_lines(
        [
            LRCLine(time_seconds=10.0, text="Line 1", raw_timestamp="[00:10.00]"),
            LRCLine(time_seconds=20.0, text="Line 2", raw_timestamp="[00:20.00]"),
        ]
    )

    async with app.run_test(size=(80, 12)) as pilot:
        await pilot.pause()

        app.screen.action_preview_continuous()
        await pilot.pause()

        lyric_display = app.screen.query_one(CurrentLyricDisplay)
        assert lyric_display._current_text == ""
        assert lyric_display._next_text == "Line 1"
        assert state.selected_index == 0

        app.screen._on_playback_position(SimpleNamespace(current_seconds=10.0))
        await pilot.pause()

        assert lyric_display._current_text == "Line 1"
        assert lyric_display._next_text == "Line 2"


@pytest.mark.asyncio
async def test_single_preview_shows_blank_before_first_line():
    app, _ = _make_app_with_lines(
        [
            LRCLine(time_seconds=10.0, text="Line 1", raw_timestamp="[00:10.00]"),
            LRCLine(time_seconds=20.0, text="Line 2", raw_timestamp="[00:20.00]"),
        ]
    )

    async with app.run_test(size=(80, 12)) as pilot:
        await pilot.pause()

        app.screen.action_preview_single()
        await pilot.pause()

        lyric_display = app.screen.query_one(CurrentLyricDisplay)
        assert lyric_display._current_text == ""
        assert lyric_display._next_text == "Line 1"

        app.screen._on_playback_position(SimpleNamespace(current_seconds=10.0))
        await pilot.pause()

        assert lyric_display._current_text == "Line 1"
        assert lyric_display._next_text == "Line 2"
