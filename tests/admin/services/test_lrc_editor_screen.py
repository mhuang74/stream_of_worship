"""Textual screen tests for the admin LRC editor."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from textual.widgets import DataTable, Input

from stream_of_worship.admin.editor.app import LRCEditorApp
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
        assert str(table.get_cell_at((48, 0))) == "49"
        assert str(table.get_cell_at((49, 0))) == ">50"

        for _ in range(3):
            await pilot.press("down")
            await pilot.pause()

        assert state.selected_index == 49
        assert table.cursor_row == 49
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
