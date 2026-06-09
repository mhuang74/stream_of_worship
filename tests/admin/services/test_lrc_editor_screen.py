"""Textual screen tests for the admin LRC editor."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from textual import events
from textual.css.query import NoMatches
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
        with pytest.raises(NoMatches):
            app.screen.query_one("#edit-panel")
        row_edit_input = app.screen.query_one("#row-edit-input", Input)
        status = app.screen.query_one(StatusIndicator)
        footer = app.screen.query_one(GroupedFooter)

        assert _bottom(editor_body) <= footer.region.y
        assert row_edit_input.display is False
        assert _bottom(table) <= status.region.y
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
async def test_edit_text_uses_overlay_and_updates_captured_row():
    app, state = _make_app(line_count=3)

    async with app.run_test(size=(80, 12)) as pilot:
        await pilot.pause()
        table = app.screen.query_one("#line-table", DataTable)

        await pilot.press("e")
        await pilot.pause()

        edit_input = app.screen.query_one("#row-edit-input", Input)
        assert app.focused is edit_input
        assert edit_input.display is True
        assert table.region.contains(edit_input.region.x, edit_input.region.y)
        assert edit_input.value == "Line 1"

        state.select_line(1)
        edit_input.value = "Edited first line"
        await pilot.press("enter")
        await pilot.pause()

        assert state.timed_lines[0].text == "Edited first line"
        assert state.timed_lines[1].text == "Line 2"
        assert edit_input.display is False
        assert app.focused is table


@pytest.mark.asyncio
async def test_edit_timestamp_invalid_keeps_overlay_open_without_autosave():
    app, state = _make_app(line_count=2)

    async with app.run_test(size=(80, 12)) as pilot:
        await pilot.pause()

        await pilot.press("t")
        await pilot.pause()
        edit_input = app.screen.query_one("#row-edit-input", Input)
        edit_input.value = "not-time"
        app.screen._autosave_ok = False

        await pilot.press("enter")
        await pilot.pause()

        assert edit_input.display is True
        assert edit_input.value == "not-time"
        assert state.timed_lines[0].time_seconds == 0.0
        assert app.screen._autosave_ok is False


@pytest.mark.asyncio
async def test_escape_cancels_overlay_edit_and_restores_table_focus():
    app, state = _make_app(line_count=2)

    async with app.run_test(size=(80, 12)) as pilot:
        await pilot.pause()

        await pilot.press("e")
        await pilot.pause()
        edit_input = app.screen.query_one("#row-edit-input", Input)
        edit_input.value = "Discard me"

        await pilot.press("escape")
        await pilot.pause()

        assert state.timed_lines[0].text == "Line 1"
        assert edit_input.display is False
        assert app.focused is app.screen.query_one("#line-table", DataTable)


@pytest.mark.asyncio
async def test_shift_selection_labels_copy_and_normal_navigation_clears_range():
    app, state = _make_app(line_count=5)

    async with app.run_test(size=(80, 14)) as pilot:
        await pilot.pause()
        table = app.screen.query_one("#line-table", DataTable)

        await pilot.press("shift+down")
        await pilot.pause()
        await pilot.press("shift+down")
        await pilot.pause()

        assert state.selected_index == 2
        assert str(table.get_cell_at((0, 0))) == "*1"
        assert str(table.get_cell_at((1, 0))) == "*2"
        assert str(table.get_cell_at((2, 0))) == ">3"

        app.screen.action_copy_line()
        await pilot.pause()
        assert app.clipboard == "Line 1\nLine 2\nLine 3"

        await pilot.press("down")
        await pilot.pause()
        assert state.selected_index == 3
        assert str(table.get_cell_at((0, 0))) == "1"
        assert str(table.get_cell_at((1, 0))) == "2"
        assert str(table.get_cell_at((2, 0))) == "3"
        assert str(table.get_cell_at((3, 0))) == ">4"


@pytest.mark.asyncio
async def test_shift_up_shrinks_selection_range():
    app, state = _make_app(line_count=5)

    async with app.run_test(size=(80, 14)) as pilot:
        await pilot.pause()
        table = app.screen.query_one("#line-table", DataTable)

        await pilot.press("shift+down")
        await pilot.pause()
        await pilot.press("shift+down")
        await pilot.pause()
        await pilot.press("shift+up")
        await pilot.pause()

        assert state.selected_index == 1
        assert str(table.get_cell_at((0, 0))) == "*1"
        assert str(table.get_cell_at((1, 0))) == ">2"
        assert str(table.get_cell_at((2, 0))) == "3"


@pytest.mark.asyncio
async def test_paste_text_only_inserts_draft_rows_after_current_row():
    app, state = _make_app(line_count=2)

    async with app.run_test(size=(80, 12)) as pilot:
        await pilot.pause()

        app.copy_to_clipboard("[00:12.34]Literal\n\nNew line")
        app.screen.action_paste_after()
        await pilot.pause()

        assert [line.text for line in state.timed_lines] == [
            "Line 1",
            "[00:12.34]Literal",
            "New line",
            "Line 2",
        ]
        assert [line.time_seconds for line in state.timed_lines[1:3]] == [0.0, 0.0]
        assert state.selected_index == 1


@pytest.mark.asyncio
async def test_paste_after_selection_and_terminal_duplicate_suppression():
    app, state = _make_app(line_count=4)

    async with app.run_test(size=(80, 14)) as pilot:
        await pilot.pause()

        await pilot.press("shift+down")
        await pilot.pause()
        app.screen.on_paste(events.Paste("A\nB"))
        app.copy_to_clipboard("A\nB")
        app.screen.action_paste_after()
        await pilot.pause()

        assert [line.text for line in state.timed_lines] == [
            "Line 1",
            "Line 2",
            "A",
            "B",
            "Line 3",
            "Line 4",
        ]
        assert state.selected_index == 2


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
