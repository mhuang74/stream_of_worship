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
from stream_of_worship.admin.editor.screen import CurrentLyricDisplay, LRCEditorScreen, StatusIndicator
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


def _make_app_with_lines(
    lines: list[LRCLine],
    visibility_status: str | None = None,
    analysis_client=None,
    cache_dir: Path | None = None,
    original_transcribed_content: str | None = None,
) -> tuple[LRCEditorApp, EditorState]:
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
        cache_dir=cache_dir or Path("/tmp"),
        r2_client=MagicMock(),
        db_client=MagicMock(),
        hash_prefix="abc123",
        original_transcribed_content=original_transcribed_content,
        visibility_status=visibility_status,
        analysis_client=analysis_client,
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
async def test_edit_text_overlay_starts_at_beginning_of_long_existing_lyric():
    long_text = "This is a long existing lyric line that should be visible from the beginning"
    app, _ = _make_app_with_lines(
        [LRCLine(time_seconds=0.0, text=long_text, raw_timestamp="[00:00.00]")]
    )

    async with app.run_test(size=(80, 12)) as pilot:
        await pilot.pause()

        await pilot.press("e")
        await pilot.pause()

        edit_input = app.screen.query_one("#row-edit-input", Input)
        assert edit_input.value == long_text
        assert edit_input.cursor_position == 0
        assert edit_input.scroll_x == 0
        assert edit_input.display is True
        assert app.focused is edit_input


@pytest.mark.asyncio
async def test_edit_text_defers_overlay_until_after_refresh(monkeypatch):
    app, _ = _make_app(line_count=3)

    async with app.run_test(size=(80, 12)) as pilot:
        await pilot.pause()
        callbacks = []

        def fake_call_after_refresh(callback, *args, **kwargs):
            callbacks.append((callback, args, kwargs))

        monkeypatch.setattr(app.screen, "call_after_refresh", fake_call_after_refresh)

        app.screen.action_edit_text()

        edit_input = app.screen.query_one("#row-edit-input", Input)
        assert edit_input.display is False
        assert len(callbacks) == 1

        callback, args, kwargs = callbacks[0]
        callback(*args, **kwargs)
        await pilot.pause()

        assert app.focused is edit_input
        assert edit_input.display is True
        assert edit_input.value == "Line 1"


@pytest.mark.asyncio
async def test_resize_defers_overlay_reposition_until_after_refresh(monkeypatch):
    app, _ = _make_app(line_count=3)

    async with app.run_test(size=(80, 12)) as pilot:
        await pilot.pause()
        callbacks = []
        reposition_count = 0

        def fake_call_after_refresh(callback, *args, **kwargs):
            callbacks.append((callback, args, kwargs))

        def fake_refresh_position():
            nonlocal reposition_count
            reposition_count += 1

        monkeypatch.setattr(app.screen, "call_after_refresh", fake_call_after_refresh)
        monkeypatch.setattr(app.screen, "_refresh_row_edit_input_position", fake_refresh_position)

        app.screen.on_resize(events.Resize(size=(100, 20), virtual_size=(100, 20)))

        assert reposition_count == 0
        assert len(callbacks) == 1

        callback, args, kwargs = callbacks[0]
        callback(*args, **kwargs)

        assert reposition_count == 1


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
async def test_show_keymap_dialog_lists_all_bindings_grouped():
    app, _ = _make_app(line_count=3)

    async with app.run_test(size=(80, 12)) as pilot:
        await pilot.pause()

        app.screen.action_show_keymap()
        await pilot.pause()

        from textual.screen import ModalScreen

        assert isinstance(app.screen, ModalScreen)
        container = app.screen.query_one("#keymap-container")

        labels = [label.render().plain for label in container.query("Label")]
        rendered = "\n".join(labels)

        for group_label in ("Playback", "Lyrics", "Timecode", "General"):
            assert group_label in rendered

        for b in LRCEditorScreen.BINDINGS:
            assert b.description in rendered

        # Dismiss by pressing `?` so the full App._check_bindings dispatch path
        # is exercised (regression for the KeymapDialog._bindings clobber bug,
        # where overwriting Textual's BindingsMap with a plain list caused
        # AttributeError on key dispatch).
        await pilot.press("?")
        await pilot.pause()

        assert not isinstance(app.screen, ModalScreen)


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


@pytest.mark.asyncio
async def test_save_with_no_changes_shows_toast_not_dialog():
    from textual.screen import ModalScreen

    # Simulate an R2-source session: serialize() equals the original so a
    # clean session counts as unchanged (catalog-source sessions with
    # original_serialized="" always count as changed).
    app, state = _make_app(line_count=3)
    state.original_serialized = state.serialize()

    async with app.run_test(size=(80, 12)) as pilot:
        await pilot.pause()

        await pilot.press("s")
        await pilot.pause()

        assert app.upload_attempted is False
        assert not isinstance(app.screen, ModalScreen)


@pytest.mark.asyncio
async def test_save_after_real_edit_opens_dialog():
    from textual.screen import ModalScreen

    app, state = _make_app(line_count=3)

    async with app.run_test(size=(80, 12)) as pilot:
        await pilot.pause()

        state.set_text(0, "Changed")
        await pilot.press("s")
        await app.workers.wait_for_complete()
        await pilot.pause()

        assert isinstance(app.screen, ModalScreen)
        assert app.upload_attempted is True


@pytest.mark.asyncio
async def test_save_upload_does_not_block_event_loop(monkeypatch):
    """Regression: the R2 conflict check must run off the Textual event loop."""
    import time as time_module
    from textual.screen import ModalScreen

    import stream_of_worship.admin.editor.screen as screen_module

    def _slow_check(*args, **kwargs):
        time_module.sleep(0.5)
        return False, ""

    monkeypatch.setattr(screen_module, "check_transcribed_changed", _slow_check)

    app, state = _make_app(line_count=3)

    async with app.run_test(size=(80, 12)) as pilot:
        await pilot.pause()
        editor = app.screen
        state.set_text(0, "Changed")

        started = time_module.monotonic()
        editor.action_save_upload()
        elapsed = time_module.monotonic() - started

        assert elapsed < 0.1
        assert editor._save_in_flight is True
        assert "Checking R2" in str(editor.query_one(StatusIndicator).visual)

        await app.workers.wait_for_complete()
        await pilot.pause()

        assert isinstance(app.screen, ModalScreen)
        assert editor._save_in_flight is False


@pytest.mark.asyncio
async def test_upload_dialog_does_not_block_event_loop(monkeypatch, caplog):
    """Regression: the R2 upload + DB write must run off the Textual event loop."""
    import logging
    import time as time_module

    import stream_of_worship.admin.editor.screen as screen_module
    from stream_of_worship.admin.editor.upload import R2UploadOutcome

    caplog.set_level(logging.INFO, logger="stream_of_worship.admin.audit")

    def _slow_upload(**kwargs):
        time_module.sleep(0.5)
        return R2UploadOutcome(r2_url="s3://bucket/abc123def456/lyrics.lrc")

    monkeypatch.setattr(screen_module, "upload_revised_lrc", _slow_upload)
    monkeypatch.setattr(screen_module, "check_transcribed_changed", lambda *a, **k: (False, ""))

    app, state = _make_app(line_count=3)

    async with app.run_test(size=(80, 12)) as pilot:
        await pilot.pause()
        editor = app.screen
        state.set_text(0, "Changed")
        await pilot.press("s")
        await app.workers.wait_for_complete()
        await pilot.pause()

        started = time_module.monotonic()
        await pilot.press("u")
        elapsed = time_module.monotonic() - started

        assert elapsed < 0.2
        assert "Uploading to R2" in str(editor.query_one(StatusIndicator).visual)

        await app.workers.wait_for_complete()
        await pilot.pause()

        status_visual = str(editor.query_one(StatusIndicator).visual)
        assert "Upload successful" in status_visual
        assert "audit event=lrc_save" in caplog.text
        assert "via=editor" in caplog.text
        assert "db_updated=True" in caplog.text


@pytest.mark.asyncio
async def test_status_indicator_shows_changed_or_no_changes():
    app, _ = _make_app(line_count=3)

    async with app.run_test(size=(80, 12)) as pilot:
        await pilot.pause()
        status = app.screen.query_one(StatusIndicator)

        status.update_status(dirty=True, autosave_ok=True, source="r2")
        await pilot.pause()
        visual = str(status.visual)
        assert "Changed" in visual
        assert "No Changes" not in visual

        status.update_status(dirty=False, autosave_ok=True, source="r2")
        await pilot.pause()
        visual = str(status.visual)
        assert "No Changes" in visual
        assert "Changed" not in visual


@pytest.mark.asyncio
async def test_quit_clean_sets_quit_clean_flag():
    # Clean R2-source session (see test_save_with_no_changes...).
    app, state = _make_app(line_count=3)
    state.original_serialized = state.serialize()

    async with app.run_test() as pilot:
        await pilot.pause()

        await pilot.press("escape")
        await pilot.pause()
        await pilot.press("y")
        await pilot.pause()

        assert app.quit_clean is True


@pytest.mark.asyncio
async def test_quit_dirty_sets_quit_clean_false():
    app, state = _make_app(line_count=3)

    async with app.run_test() as pilot:
        await pilot.pause()

        state.set_text(0, "X")
        await pilot.press("escape")
        await pilot.pause()
        await pilot.press("y")
        await pilot.pause()

        assert app.quit_clean is False


@pytest.mark.parametrize(
    ("visibility_status", "expected_sub_title"),
    [
        ("published", "Visibility: ● published"),
        ("review", "Visibility: ◐ review"),
        ("hold", "Visibility: ○ hold"),
        (None, "Visibility: · none"),
    ],
)
@pytest.mark.asyncio
async def test_header_shows_visibility_badge(visibility_status, expected_sub_title):
    from textual.widgets import Header

    app, _ = _make_app_with_lines(
        [LRCLine(time_seconds=0.0, text="Line 1", raw_timestamp="[00:00.00]")],
        visibility_status=visibility_status,
    )

    async with app.run_test(size=(80, 12)) as pilot:
        await pilot.pause()

        assert app.screen.sub_title == expected_sub_title
        # Header renders "title — sub_title" as plain Content.
        assert expected_sub_title in str(app.screen.query_one(Header).format_title())


@pytest.mark.asyncio
async def test_regenerate_hotkey_asks_and_refreshes_from_r2(monkeypatch):
    import asyncio
    from textual.screen import ModalScreen

    import stream_of_worship.admin.editor.screen as screen_module
    from stream_of_worship.admin.editor.regenerate import RegenerateResult

    calls: list[dict] = []

    def _fake_regenerate(**kwargs) -> RegenerateResult:
        calls.append(kwargs)
        return RegenerateResult(
            ok=True,
            lrc_content="[00:01.00]New A\n[00:02.00]New B",
            identity=R2ObjectIdentity(exists=True, etag="new"),
            job_id="job-1",
        )

    monkeypatch.setattr(screen_module, "regenerate_lrc_for_editor", _fake_regenerate)

    app, state = _make_app_with_lines(
        [
            LRCLine(time_seconds=1.0, text="Old A", raw_timestamp="[00:01.00]"),
            LRCLine(time_seconds=2.0, text="Old B", raw_timestamp="[00:02.00]"),
        ],
        analysis_client=MagicMock(),
    )
    state.song_id = "song_0001"

    async with app.run_test(size=(80, 12)) as pilot:
        await pilot.pause()
        editor = app.screen

        await pilot.press("G")
        await pilot.pause()
        assert isinstance(app.screen, ModalScreen)

        await pilot.press("a")
        await asyncio.to_thread(editor._regen_thread.join, 5.0)
        await pilot.pause()

        assert calls[0]["generator"] == "auto"
        assert [line.text for line in state.timed_lines] == ["New A", "New B"]
        assert state.dirty is False
        assert state.source_mode == "r2"
        assert state.transcribed_identity.etag == "new"
        assert editor._regen_in_flight is False

        table = editor.query_one("#line-table", DataTable)
        assert str(table.get_cell_at((0, 2))) == "New A"
        assert "Source: R2" in str(editor.query_one(StatusIndicator).visual)


@pytest.mark.asyncio
async def test_regenerate_generator_picker_passes_whisper(monkeypatch):
    import asyncio
    from textual.screen import ModalScreen

    import stream_of_worship.admin.editor.screen as screen_module
    from stream_of_worship.admin.editor.regenerate import RegenerateResult

    calls: list[dict] = []

    def _fake_regenerate(**kwargs) -> RegenerateResult:
        calls.append(kwargs)
        return RegenerateResult(
            ok=True,
            lrc_content="[00:01.00]New A",
            identity=R2ObjectIdentity(exists=True, etag="new"),
            job_id="job-1",
        )

    monkeypatch.setattr(screen_module, "regenerate_lrc_for_editor", _fake_regenerate)

    app, state = _make_app_with_lines(
        [LRCLine(time_seconds=1.0, text="Old A", raw_timestamp="[00:01.00]")],
        analysis_client=MagicMock(),
    )
    state.song_id = "song_0001"

    async with app.run_test(size=(80, 12)) as pilot:
        await pilot.pause()
        editor = app.screen

        await pilot.press("G")
        await pilot.pause()
        assert isinstance(app.screen, ModalScreen)

        await pilot.press("w")
        await asyncio.to_thread(editor._regen_thread.join, 5.0)
        await pilot.pause()

        assert calls[0]["generator"] == "whisper"
        assert [line.text for line in state.timed_lines] == ["New A"]


@pytest.mark.asyncio
async def test_regenerate_youtube_without_url_warns_and_does_not_start(monkeypatch):
    import stream_of_worship.admin.editor.screen as screen_module

    calls: list[dict] = []
    monkeypatch.setattr(
        screen_module,
        "regenerate_lrc_for_editor",
        lambda **kwargs: calls.append(kwargs),
    )

    app, state = _make_app_with_lines(
        [LRCLine(time_seconds=1.0, text="Old A", raw_timestamp="[00:01.00]")],
        analysis_client=MagicMock(),
    )
    state.song_id = "song_0001"

    async with app.run_test(size=(80, 12)) as pilot:
        await pilot.pause()
        editor = app.screen
        editor.db_client.get_recording_by_hash.return_value = SimpleNamespace(youtube_url=None)
        messages: list[str] = []
        editor.notify = lambda message, **kwargs: messages.append(message)

        await pilot.press("G")
        await pilot.pause()
        await pilot.press("y")
        await pilot.pause()

        assert editor._regen_thread is None
        assert editor._regen_in_flight is False
        assert calls == []
        assert any("requires a YouTube URL" in message for message in messages)
        assert [line.text for line in state.timed_lines] == ["Old A"]


@pytest.mark.asyncio
async def test_regenerate_cancel_keeps_editor_untouched():
    from textual.screen import ModalScreen

    app, state = _make_app_with_lines(
        [LRCLine(time_seconds=1.0, text="Old A", raw_timestamp="[00:01.00]")],
        analysis_client=MagicMock(),
    )
    state.song_id = "song_0001"

    async with app.run_test(size=(80, 12)) as pilot:
        await pilot.pause()
        editor = app.screen

        await pilot.press("G")
        await pilot.pause()
        assert isinstance(app.screen, ModalScreen)

        await pilot.press("n")
        await pilot.pause()

        assert editor._regen_thread is None
        assert editor._regen_in_flight is False
        assert [line.text for line in state.timed_lines] == ["Old A"]


@pytest.mark.asyncio
async def test_regenerate_hotkey_disabled_without_analysis_client():
    from textual.screen import ModalScreen

    app, state = _make_app_with_lines(
        [LRCLine(time_seconds=1.0, text="Old A", raw_timestamp="[00:01.00]")]
    )
    state.song_id = "song_0001"

    async with app.run_test(size=(80, 12)) as pilot:
        await pilot.pause()
        editor = app.screen

        await pilot.press("G")
        await pilot.pause()

        assert not isinstance(app.screen, ModalScreen)
        assert editor._regen_thread is None


@pytest.mark.asyncio
async def test_quit_during_regeneration_requires_force_confirm():
    from textual.screen import ModalScreen

    app, state = _make_app_with_lines(
        [LRCLine(time_seconds=1.0, text="Old A", raw_timestamp="[00:01.00]")],
        analysis_client=MagicMock(),
    )
    state.song_id = "song_0001"

    async with app.run_test(size=(80, 12)) as pilot:
        await pilot.pause()
        editor = app.screen
        editor._regen_in_flight = True

        await pilot.press("escape")
        await pilot.pause()

        assert isinstance(app.screen, ModalScreen)
        assert app.is_running is True

        await pilot.press("y")
        await pilot.pause()

        assert app.quit_clean is False


@pytest.mark.asyncio
async def test_reload_hotkey_applies_changed_r2_content(tmp_path):
    import asyncio

    app, state = _make_app_with_lines(
        [LRCLine(time_seconds=1.0, text="Old A", raw_timestamp="[00:01.00]")],
        cache_dir=tmp_path,
    )

    async with app.run_test(size=(80, 12)) as pilot:
        await pilot.pause()
        editor = app.screen
        editor.r2_client.download_lrc_content.return_value = "[00:01.00]From R2"
        editor.r2_client.get_lrc_identity.return_value = R2ObjectIdentity(exists=True, etag="new")
        editor.db_client.get_recording_by_hash.return_value = SimpleNamespace(
            visibility_status="hold"
        )

        await pilot.press("L")
        await asyncio.to_thread(editor._reload_thread.join, 5.0)
        await pilot.pause()

        assert [line.text for line in state.timed_lines] == ["From R2"]
        assert state.source_mode == "r2"
        assert state.dirty is False
        assert state.transcribed_identity.etag == "new"
        assert editor._reload_in_flight is False
        assert editor.original_transcribed_content == "[00:01.00]From R2"
        # The badge is the catalog's status, not an assumption about the reload.
        assert editor.sub_title == "Visibility: ○ hold"

        table = editor.query_one("#line-table", DataTable)
        assert str(table.get_cell_at((0, 2))) == "From R2"
        assert "Source: R2" in str(editor.query_one(StatusIndicator).visual)


@pytest.mark.asyncio
async def test_reload_hotkey_is_a_noop_when_r2_content_is_unchanged(tmp_path, monkeypatch):
    import asyncio

    import stream_of_worship.admin.editor.screen as screen_module

    cleared: list[tuple] = []
    monkeypatch.setattr(
        screen_module, "clear_autosave", lambda *args, **kwargs: cleared.append(args)
    )

    unchanged = "[00:01.00]Old A"
    app, state = _make_app_with_lines(
        [LRCLine(time_seconds=1.0, text="Old A", raw_timestamp="[00:01.00]")],
        cache_dir=tmp_path,
        original_transcribed_content=unchanged,
    )

    async with app.run_test(size=(80, 12)) as pilot:
        await pilot.pause()
        editor = app.screen
        editor.r2_client.download_lrc_content.return_value = unchanged
        editor.r2_client.get_lrc_identity.return_value = R2ObjectIdentity(exists=True, etag="same")

        await pilot.press("L")
        await asyncio.to_thread(editor._reload_thread.join, 5.0)
        await pilot.pause()

        assert [line.text for line in state.timed_lines] == ["Old A"]
        assert editor._reload_in_flight is False
        assert cleared == []
        assert not list(tmp_path.rglob("*.lrc"))


@pytest.mark.asyncio
async def test_reload_preserves_edits_made_during_fetch_as_draft(tmp_path):
    import asyncio
    import threading

    gate = threading.Event()
    app, state = _make_app_with_lines(
        [LRCLine(time_seconds=1.0, text="Old A", raw_timestamp="[00:01.00]")],
        cache_dir=tmp_path,
    )

    async with app.run_test(size=(80, 12)) as pilot:
        await pilot.pause()
        editor = app.screen

        def _download(_hash_prefix: str) -> str:
            gate.wait(5.0)
            return "[00:01.00]From R2"

        editor.r2_client.download_lrc_content.side_effect = _download
        editor.r2_client.get_lrc_identity.return_value = R2ObjectIdentity(exists=True, etag="new")

        await pilot.press("L")
        await pilot.pause()

        state.set_text(0, "Edited During Fetch")
        gate.set()
        await asyncio.to_thread(editor._reload_thread.join, 5.0)
        await pilot.pause()

        drafts = list((tmp_path / "abc123" / "lrc").glob("lyrics.edited.*.lrc"))
        assert len(drafts) == 1
        assert "Edited During Fetch" in drafts[0].read_text(encoding="utf-8")
        assert [line.text for line in state.timed_lines] == ["From R2"]
        assert editor._reload_in_flight is False


@pytest.mark.asyncio
async def test_reload_hotkey_reports_fetch_failure(tmp_path):
    import asyncio

    app, state = _make_app_with_lines(
        [LRCLine(time_seconds=1.0, text="Old A", raw_timestamp="[00:01.00]")],
        cache_dir=tmp_path,
    )

    async with app.run_test(size=(80, 12)) as pilot:
        await pilot.pause()
        editor = app.screen
        editor.r2_client.download_lrc_content.side_effect = RuntimeError("boom")

        await pilot.press("L")
        await asyncio.to_thread(editor._reload_thread.join, 5.0)
        await pilot.pause()

        assert editor._reload_in_flight is False
        assert [line.text for line in state.timed_lines] == ["Old A"]
        assert "LRC reload failed" in str(editor.query_one(StatusIndicator).visual)


@pytest.mark.asyncio
async def test_align_hotkey_asks_and_refreshes_from_r2(monkeypatch):
    import asyncio
    from textual.screen import ModalScreen

    import stream_of_worship.admin.editor.screen as screen_module
    from stream_of_worship.admin.editor.align import AlignResult

    calls: list[dict] = []

    def _fake_align(**kwargs) -> AlignResult:
        calls.append(kwargs)
        return AlignResult(
            ok=True,
            lrc_content="[00:01.10]Old A\n[00:02.20]Old B",
            identity=R2ObjectIdentity(exists=True, etag="aligned"),
            job_id="job-1",
        )

    monkeypatch.setattr(screen_module, "align_lrc_for_editor", _fake_align)

    app, state = _make_app_with_lines(
        [
            LRCLine(time_seconds=1.0, text="Old A", raw_timestamp="[00:01.00]"),
            LRCLine(time_seconds=2.0, text="Old B", raw_timestamp="[00:02.00]"),
        ],
        analysis_client=MagicMock(),
    )
    state.song_id = "song_0001"
    state.audio_duration = 200.0

    async with app.run_test(size=(80, 12)) as pilot:
        await pilot.pause()
        editor = app.screen

        await pilot.press("A")
        await pilot.pause()
        assert isinstance(app.screen, ModalScreen)

        await pilot.press("a")
        await asyncio.to_thread(editor._align_thread.join, 5.0)
        await pilot.pause()

        assert calls[0]["language"] == "auto"
        assert calls[0]["hash_prefix"] == "abc123"
        assert [line.text for line in state.timed_lines] == ["Old A", "Old B"]
        assert [line.time_seconds for line in state.timed_lines] == [1.1, 2.2]
        assert state.dirty is False
        assert state.source_mode == "r2"
        assert state.transcribed_identity.etag == "aligned"
        assert editor._align_in_flight is False

        table = editor.query_one("#line-table", DataTable)
        assert str(table.get_cell_at((0, 2))) == "Old A"
        assert "Source: R2" in str(editor.query_one(StatusIndicator).visual)


@pytest.mark.asyncio
async def test_align_language_picker_passes_zh(monkeypatch):
    import asyncio
    from textual.screen import ModalScreen

    import stream_of_worship.admin.editor.screen as screen_module
    from stream_of_worship.admin.editor.align import AlignResult

    calls: list[dict] = []

    monkeypatch.setattr(
        screen_module,
        "align_lrc_for_editor",
        lambda **kwargs: (
            calls.append(kwargs)
            or AlignResult(
                ok=True,
                lrc_content="[00:01.10]Old A",
                identity=R2ObjectIdentity(exists=True, etag="aligned"),
                job_id="job-1",
            )
        ),
    )

    app, state = _make_app_with_lines(
        [LRCLine(time_seconds=1.0, text="Old A", raw_timestamp="[00:01.00]")],
        analysis_client=MagicMock(),
    )
    state.song_id = "song_0001"

    async with app.run_test(size=(80, 12)) as pilot:
        await pilot.pause()
        editor = app.screen

        await pilot.press("A")
        await pilot.pause()
        assert isinstance(app.screen, ModalScreen)

        await pilot.press("z")
        await asyncio.to_thread(editor._align_thread.join, 5.0)
        await pilot.pause()

        assert calls[0]["language"] == "zh"
        assert [line.text for line in state.timed_lines] == ["Old A"]


@pytest.mark.asyncio
async def test_align_cancel_keeps_editor_untouched(monkeypatch):
    from textual.screen import ModalScreen

    import stream_of_worship.admin.editor.screen as screen_module

    calls: list[dict] = []
    monkeypatch.setattr(
        screen_module, "align_lrc_for_editor", lambda **kwargs: calls.append(kwargs)
    )

    app, state = _make_app_with_lines(
        [LRCLine(time_seconds=1.0, text="Old A", raw_timestamp="[00:01.00]")],
        analysis_client=MagicMock(),
    )
    state.song_id = "song_0001"

    async with app.run_test(size=(80, 12)) as pilot:
        await pilot.pause()
        editor = app.screen

        await pilot.press("A")
        await pilot.pause()
        assert isinstance(app.screen, ModalScreen)

        await pilot.press("escape")
        await pilot.pause()

        assert editor._align_thread is None
        assert editor._align_in_flight is False
        assert calls == []
        assert [line.text for line in state.timed_lines] == ["Old A"]


@pytest.mark.asyncio
async def test_align_blocked_over_300s(monkeypatch):
    from textual.screen import ModalScreen

    import stream_of_worship.admin.editor.screen as screen_module

    calls: list[dict] = []
    monkeypatch.setattr(
        screen_module, "align_lrc_for_editor", lambda **kwargs: calls.append(kwargs)
    )

    app, state = _make_app_with_lines(
        [LRCLine(time_seconds=1.0, text="Old A", raw_timestamp="[00:01.00]")],
        analysis_client=MagicMock(),
    )
    state.song_id = "song_0001"
    state.audio_duration = 301.0

    async with app.run_test(size=(80, 12)) as pilot:
        await pilot.pause()
        editor = app.screen
        messages: list[str] = []
        editor.notify = lambda message, **kwargs: messages.append(message)

        await pilot.press("A")
        await pilot.pause()

        assert not isinstance(app.screen, ModalScreen)
        assert editor._align_thread is None
        assert editor._align_in_flight is False
        assert calls == []
        assert any("too long" in message for message in messages)
        assert "too long" in str(editor.query_one(StatusIndicator).visual)


@pytest.mark.asyncio
async def test_align_hotkey_disabled_without_analysis_client():
    from textual.screen import ModalScreen

    app, state = _make_app_with_lines(
        [LRCLine(time_seconds=1.0, text="Old A", raw_timestamp="[00:01.00]")]
    )
    state.song_id = "song_0001"

    async with app.run_test(size=(80, 12)) as pilot:
        await pilot.pause()
        editor = app.screen

        await pilot.press("A")
        await pilot.pause()

        assert not isinstance(app.screen, ModalScreen)
        assert editor._align_thread is None


@pytest.mark.asyncio
async def test_actions_blocked_while_align_in_flight():
    app, state = _make_app_with_lines(
        [LRCLine(time_seconds=1.0, text="Old A", raw_timestamp="[00:01.00]")],
        analysis_client=MagicMock(),
    )
    state.song_id = "song_0001"

    async with app.run_test(size=(80, 12)) as pilot:
        await pilot.pause()
        editor = app.screen
        editor._align_in_flight = True
        messages: list[str] = []
        editor.notify = lambda message, **kwargs: messages.append(message)
        editor._has_unsaved_changes = lambda: True

        await pilot.press("G")
        await pilot.pause()
        await pilot.press("L")
        await pilot.pause()
        await pilot.press("s")
        await pilot.pause()

        assert editor._regen_thread is None
        assert editor._reload_thread is None
        assert editor._save_in_flight is False
        assert any("Alignment already in progress" in message for message in messages)
        assert any("Alignment in progress" in message for message in messages)

        from stream_of_worship.admin.editor.align import AlignResult

        editor._finish_align(AlignResult(ok=False, error="boom"))
        await pilot.pause()

        assert editor._align_in_flight is False


@pytest.mark.asyncio
async def test_quit_during_alignment_requires_force_confirm():
    from textual.screen import ModalScreen

    app, state = _make_app_with_lines(
        [LRCLine(time_seconds=1.0, text="Old A", raw_timestamp="[00:01.00]")],
        analysis_client=MagicMock(),
    )
    state.song_id = "song_0001"

    async with app.run_test(size=(80, 12)) as pilot:
        await pilot.pause()
        editor = app.screen
        editor._align_in_flight = True

        await pilot.press("escape")
        await pilot.pause()

        assert isinstance(app.screen, ModalScreen)
        assert app.is_running is True

        await pilot.press("y")
        await pilot.pause()

        assert app.quit_clean is False
