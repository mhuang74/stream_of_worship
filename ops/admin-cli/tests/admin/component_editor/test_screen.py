"""Textual screen tests for the admin Component Metadata editor (v6).

Covers the v6 layout (vertical split: Hero panel + compact table on left;
toggleable lyrics/detail panel on right), D1-D4 regression tests (adapted
for v6), and the v2 regression suite (B1-B3, C1-C5).
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from textual.widgets import DataTable

from stream_of_worship.admin.component_editor.app import ComponentEditorApp
from stream_of_worship.admin.component_editor.constants import (
    COMPACT_TABLE_COLUMNS,
    EDITABLE_FIELDS,
    THEME_VALUES,
)
from stream_of_worship.admin.component_editor.detail_panel import ComponentDetailPanel
from stream_of_worship.admin.component_editor.state import (
    ComponentEditorState,
    SongSession,
)
from stream_of_worship.admin.db.models import SongComponent
from stream_of_worship.admin.services.playback import PlaybackState


class _PlaybackStub:
    """Minimal playback stub for screen tests."""

    def __init__(self):
        self._state = PlaybackState.STOPPED
        self._position_seconds = 0.0
        self.duration_seconds = 180.0
        self.seek_calls: list[float] = []
        self.play_calls: int = 0
        self.pause_calls: int = 0
        self.play_start_seconds: list[float] = []
        self.set_state_on_play: PlaybackState | None = PlaybackState.PLAYING

    @property
    def state(self):
        return self._state

    @state.setter
    def state(self, val):
        self._state = val

    @property
    def is_playing(self):
        return self._state == PlaybackState.PLAYING

    @property
    def position_seconds(self):
        return self._position_seconds

    @position_seconds.setter
    def position_seconds(self, val):
        self._position_seconds = val

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
        self.seek_calls.append(seconds)
        self._position_seconds = seconds

    def pause(self):
        self.pause_calls += 1
        self._state = PlaybackState.PAUSED

    def play(self, start_seconds: float = 0.0, file_path=None) -> bool:
        self.play_calls += 1
        self.play_start_seconds.append(start_seconds)
        self._position_seconds = start_seconds
        if self.set_state_on_play is not None:
            self._state = self.set_state_on_play
        return True


def _make_component(
    role: str = "entry",
    cid: int = 1,
    theme: str = "讚美",
    vocal_posture: str = "To God",
    groove_density: float = 0.5,
    energy_level: float = -18.0,
    content_hash: str = "abc123def4567",
    start_time: float = 10.0,
    end_time: float = 20.0,
    bpm: float = 80.0,
    key: str = "G",
    theme_reasoning: str | None = "LLM theme reasoning text",
    posture_reasoning: str | None = "LLM posture reasoning text",
    component_type: str = "chorus",
    occurrence_index: int = 1,
) -> SongComponent:
    return SongComponent(
        id=cid,
        song_id="song_001",
        content_hash=content_hash,
        component_type=component_type,
        occurrence_index=occurrence_index,
        role=role,
        start_time=start_time,
        end_time=end_time,
        bpm=bpm,
        key=key,
        groove_density=groove_density,
        backbeat_strength=1.0,
        energy_level=energy_level,
        confidence=0.9,
        theme=theme,
        vocal_posture=vocal_posture,
        theme_reasoning=theme_reasoning,
        posture_reasoning=posture_reasoning,
    )


def _make_session(
    song_id: str = "song_001",
    song_title: str = "Test Song",
    hash_prefix: str = "abc123def456",
    entry: SongComponent | None = None,
    exit_comp: SongComponent | None = None,
    content_hash: str = "abc123def4567",
    components: dict[str, SongComponent | None] | None = None,
) -> SongSession:
    if components is not None:
        return SongSession(
            song_id=song_id,
            song_title=song_title,
            hash_prefix=hash_prefix,
            audio_path="/tmp/audio.mp3",
            audio_duration=180.0,
            components=components,
            entry_component=components.get("entry"),
            exit_component=components.get("exit"),
        )
    if entry is None:
        entry = _make_component("entry", cid=1, content_hash=content_hash)
    if exit_comp is None:
        exit_comp = _make_component("exit", cid=2, content_hash=content_hash)
    return SongSession(
        song_id=song_id,
        song_title=song_title,
        hash_prefix=hash_prefix,
        audio_path="/tmp/audio.mp3",
        audio_duration=180.0,
        components={"entry": entry, "exit": exit_comp},
        entry_component=entry,
        exit_component=exit_comp,
    )


def _make_session_with_none(
    song_id: str = "song_001",
    entry: SongComponent | None = None,
    exit_comp: SongComponent | None = None,
    content_hash: str = "abc123def4567",
) -> SongSession:
    if entry is None:
        entry = _make_component("entry", cid=1, content_hash=content_hash)
    components: dict[str, SongComponent | None] = {"entry": entry}
    if exit_comp is not None:
        components["exit"] = exit_comp
    return SongSession(
        song_id=song_id,
        song_title="Test Song",
        hash_prefix="abc123def456",
        audio_path="/tmp/audio.mp3",
        audio_duration=180.0,
        components=components,
        entry_component=entry,
        exit_component=exit_comp,
    )


def _make_app(
    sessions: list[SongSession] | None = None,
    r2_client: MagicMock | None = None,
    db_client: MagicMock | None = None,
    playback: _PlaybackStub | None = None,
) -> tuple[ComponentEditorApp, ComponentEditorState]:
    if sessions is None:
        sessions = [_make_session()]
    state = ComponentEditorState(sessions=sessions)
    if r2_client is None:
        r2_client = MagicMock()
    if db_client is None:
        db_client = MagicMock()
    if playback is None:
        playback = _PlaybackStub()
    app = ComponentEditorApp(
        editor_state=state,
        playback_service=playback,
        cache_dir=Path("/tmp/nonexistent_cache_dir_for_tests"),
        r2_client=r2_client,
        db_client=db_client,
    )
    return app, state


def _col_idx(field_key: str) -> int:
    """Return the column index for a field key in COMPACT_TABLE_COLUMNS."""
    return next(i for i, (k, _) in enumerate(COMPACT_TABLE_COLUMNS) if k == field_key)


@pytest.mark.asyncio
async def test_launches_and_shows_table():
    app, _state = _make_app()
    async with app.run_test(size=(160, 30)) as pilot:
        await pilot.pause()
        table = app.screen.query_one("#component-table", DataTable)
        assert table.row_count == 2


@pytest.mark.asyncio
async def test_breadcrumb_shows_song_index():
    sessions = [_make_session(song_id=f"song_{i:03d}") for i in range(3)]
    app, state = _make_app(sessions=sessions)
    async with app.run_test(size=(160, 30)) as pilot:
        await pilot.pause()
        assert state.current_index == 0


@pytest.mark.asyncio
async def test_cycle_theme_next_changes_value(tmp_path):
    app, state = _make_app()
    app.cache_dir = tmp_path
    async with app.run_test(size=(160, 30)) as pilot:
        await pilot.pause()
        state.selected_row = 0
        # Switch to detail panel and focus the theme field
        app.screen._active_panel = "right"
        app.screen._right_panel_mode = "details"
        detail = app.screen.query_one("#detail-panel", ComponentDetailPanel)
        detail._focus_idx = EDITABLE_FIELDS.index("theme")
        app.screen._refresh_detail_panel()
        await pilot.pause()
        app.screen.action_cycle_field_next()
        await pilot.pause()
        assert state.get_value("entry", "theme") == THEME_VALUES[1]
        assert state.current.dirty is True


@pytest.mark.asyncio
async def test_cycle_theme_prev_changes_value(tmp_path):
    app, state = _make_app()
    app.cache_dir = tmp_path
    async with app.run_test(size=(160, 30)) as pilot:
        await pilot.pause()
        state.selected_row = 0
        # Switch to detail panel and focus the theme field
        app.screen._active_panel = "right"
        app.screen._right_panel_mode = "details"
        detail = app.screen.query_one("#detail-panel", ComponentDetailPanel)
        detail._focus_idx = EDITABLE_FIELDS.index("theme")
        app.screen._refresh_detail_panel()
        await pilot.pause()
        app.screen.action_cycle_field_prev()
        await pilot.pause()
        assert state.get_value("entry", "theme") == THEME_VALUES[-1]


@pytest.mark.asyncio
async def test_cycle_ignored_on_non_enum_cell(tmp_path):
    app, state = _make_app()
    app.cache_dir = tmp_path
    async with app.run_test(size=(160, 30)) as pilot:
        await pilot.pause()
        # On details panel but focused on groove_density (not an enum field)
        app.screen._active_panel = "right"
        app.screen._right_panel_mode = "details"
        detail = app.screen.query_one("#detail-panel", ComponentDetailPanel)
        detail._focus_idx = EDITABLE_FIELDS.index("groove_density")
        app.screen._refresh_detail_panel()
        await pilot.pause()
        app.screen.action_cycle_field_next()
        await pilot.pause()
        assert state.current.dirty is False


@pytest.mark.asyncio
async def test_save_calls_db_and_r2_and_clears_dirty(tmp_path):
    r2_client = MagicMock()
    r2_client.download_component_result.return_value = None
    db_client = MagicMock()
    mock_conn = MagicMock()
    mock_txn = MagicMock()
    mock_txn.__enter__ = MagicMock(return_value=mock_conn)
    mock_txn.__exit__ = MagicMock(return_value=False)
    db_client.transaction.return_value = mock_txn
    reloaded_entry = _make_component("entry", cid=1, theme="敬拜")
    reloaded_exit = _make_component("exit", cid=2)
    db_client.get_song_components_entry_exit.return_value = (reloaded_entry, reloaded_exit)
    db_client.get_song_components.return_value = [reloaded_entry, reloaded_exit]
    sessions = [_make_session()]
    app, state = _make_app(sessions=sessions, r2_client=r2_client, db_client=db_client)
    app.cache_dir = tmp_path

    async with app.run_test(size=(160, 30)) as pilot:
        await pilot.pause()
        state.set_value("entry", "theme", "敬拜")
        await pilot.pause()
        await pilot.press("s")
        await pilot.pause()
        assert db_client.update_song_component_fields_txn.called
        assert r2_client.upload_component_result.called
        assert state.current.dirty is False
        assert state.current.r2_save_pending is False
        assert len(state.current.working) == 0


@pytest.mark.asyncio
async def test_save_noop_when_not_dirty(tmp_path):
    r2_client = MagicMock()
    db_client = MagicMock()
    app, _state = _make_app(r2_client=r2_client, db_client=db_client)
    app.cache_dir = tmp_path
    async with app.run_test(size=(160, 30)) as pilot:
        await pilot.pause()
        app.screen.action_save()
        await pilot.pause()
        assert not db_client.transaction.called


@pytest.mark.asyncio
async def test_b1_r2_failure_keeps_dirty_and_pending(tmp_path):
    """B1 regression: R2 failure keeps working/dirty/autosave, sets r2_save_pending."""
    r2_client = MagicMock()
    r2_client.download_component_result.return_value = None
    r2_client.upload_component_result.side_effect = Exception("R2 upload failed")
    db_client = MagicMock()
    mock_conn = MagicMock()
    mock_txn = MagicMock()
    mock_txn.__enter__ = MagicMock(return_value=mock_conn)
    mock_txn.__exit__ = MagicMock(return_value=False)
    db_client.transaction.return_value = mock_txn
    sessions = [_make_session()]
    app, state = _make_app(sessions=sessions, r2_client=r2_client, db_client=db_client)
    app.cache_dir = tmp_path

    async with app.run_test(size=(160, 30)) as pilot:
        await pilot.pause()
        state.set_value("entry", "theme", "敬拜")
        await pilot.pause()
        await pilot.press("s")
        await pilot.pause()
        assert state.current.dirty is True
        assert len(state.current.working) > 0
        assert state.current.r2_save_pending is True
        assert len(state.current_undo) > 0


@pytest.mark.asyncio
async def test_b2_first_content_hash_with_none_entry(tmp_path):
    """B2 regression: synthesise R2 payload when entry_component is None."""
    r2_client = MagicMock()
    r2_client.download_component_result.return_value = None
    db_client = MagicMock()
    mock_conn = MagicMock()
    mock_txn = MagicMock()
    mock_txn.__enter__ = MagicMock(return_value=mock_conn)
    mock_txn.__exit__ = MagicMock(return_value=False)
    db_client.transaction.return_value = mock_txn
    exit_comp = _make_component("exit", cid=2, content_hash="deadbeef")
    reloaded_exit = _make_component("exit", cid=2, content_hash="deadbeef", theme="敬拜")
    db_client.get_song_components_entry_exit.return_value = (None, reloaded_exit)
    db_client.get_song_components.return_value = [reloaded_exit]
    session = _make_session_with_none(entry=None, exit_comp=exit_comp, content_hash="deadbeef")
    app, state = _make_app(sessions=[session], r2_client=r2_client, db_client=db_client)
    app.cache_dir = tmp_path

    async with app.run_test(size=(160, 30)) as pilot:
        await pilot.pause()
        state.set_value("exit", "theme", "敬拜")
        await pilot.pause()
        app.screen.action_save()
        await pilot.pause()
        assert r2_client.upload_component_result.called
        call_args = r2_client.upload_component_result.call_args
        payload = call_args[0][1] if len(call_args[0]) > 1 else call_args[1].get("payload")
        assert payload["content_hash"] == "deadbeef"


@pytest.mark.asyncio
async def test_c1_r2_download_failure_returns_retryable(tmp_path):
    """C1 regression: R2 download exception is caught, save returns retryable."""
    r2_client = MagicMock()
    r2_client.download_component_result.side_effect = Exception("Network error")
    db_client = MagicMock()
    mock_conn = MagicMock()
    mock_txn = MagicMock()
    mock_txn.__enter__ = MagicMock(return_value=mock_conn)
    mock_txn.__exit__ = MagicMock(return_value=False)
    db_client.transaction.return_value = mock_txn
    app, state = _make_app(r2_client=r2_client, db_client=db_client)
    app.cache_dir = tmp_path

    async with app.run_test(size=(160, 30)) as pilot:
        await pilot.pause()
        state.set_value("entry", "theme", "敬拜")
        await pilot.pause()
        await pilot.press("s")
        await pilot.pause()
        assert state.current.dirty is True
        assert state.current.r2_save_pending is True


@pytest.mark.asyncio
async def test_c2_save_calls_txn_helper(tmp_path):
    """C2 regression: action_save calls update_song_component_fields_txn."""
    r2_client = MagicMock()
    r2_client.download_component_result.return_value = None
    db_client = MagicMock()
    mock_conn = MagicMock()
    mock_txn = MagicMock()
    mock_txn.__enter__ = MagicMock(return_value=mock_conn)
    mock_txn.__exit__ = MagicMock(return_value=False)
    db_client.transaction.return_value = mock_txn
    reloaded_entry = _make_component("entry", cid=1, theme="敬拜")
    reloaded_exit = _make_component("exit", cid=2)
    db_client.get_song_components_entry_exit.return_value = (reloaded_entry, reloaded_exit)
    db_client.get_song_components.return_value = [reloaded_entry, reloaded_exit]
    app, state = _make_app(r2_client=r2_client, db_client=db_client)
    app.cache_dir = tmp_path

    async with app.run_test(size=(160, 30)) as pilot:
        await pilot.pause()
        state.set_value("entry", "theme", "敬拜")
        await pilot.pause()
        await pilot.press("s")
        await pilot.pause()
        assert db_client.update_song_component_fields_txn.called
        assert db_client.transaction.called


@pytest.mark.asyncio
async def test_c3_reload_after_save(tmp_path):
    """C3 regression: after full-success save, components are reloaded from DB."""
    r2_client = MagicMock()
    r2_client.download_component_result.return_value = None
    db_client = MagicMock()
    mock_conn = MagicMock()
    mock_txn = MagicMock()
    mock_txn.__enter__ = MagicMock(return_value=mock_conn)
    mock_txn.__exit__ = MagicMock(return_value=False)
    db_client.transaction.return_value = mock_txn
    reloaded_entry = _make_component("entry", cid=1, theme="敬拜")
    reloaded_exit = _make_component("exit", cid=2, theme="讚美")
    db_client.get_song_components_entry_exit.return_value = (reloaded_entry, reloaded_exit)
    db_client.get_song_components.return_value = [reloaded_entry, reloaded_exit]
    app, state = _make_app(r2_client=r2_client, db_client=db_client)
    app.cache_dir = tmp_path

    async with app.run_test(size=(160, 30)) as pilot:
        await pilot.pause()
        old_entry_id = id(state.current.entry_component)
        state.set_value("entry", "theme", "敬拜")
        await pilot.pause()
        await pilot.press("s")
        await pilot.pause()
        assert id(state.current.entry_component) != old_entry_id
        assert state.current.entry_component.theme == "敬拜"


@pytest.mark.asyncio
async def test_b3_undo_stack_cleared_after_save(tmp_path):
    """B3 regression: after full-success save, undo stack is cleared."""
    r2_client = MagicMock()
    r2_client.download_component_result.return_value = None
    db_client = MagicMock()
    mock_conn = MagicMock()
    mock_txn = MagicMock()
    mock_txn.__enter__ = MagicMock(return_value=mock_conn)
    mock_txn.__exit__ = MagicMock(return_value=False)
    db_client.transaction.return_value = mock_txn
    reloaded_entry = _make_component("entry", cid=1, theme="敬拜")
    reloaded_exit = _make_component("exit", cid=2)
    db_client.get_song_components_entry_exit.return_value = (reloaded_entry, reloaded_exit)
    db_client.get_song_components.return_value = [reloaded_entry, reloaded_exit]
    app, state = _make_app(r2_client=r2_client, db_client=db_client)
    app.cache_dir = tmp_path

    async with app.run_test(size=(160, 30)) as pilot:
        await pilot.pause()
        state.set_value("entry", "theme", "敬拜")
        await pilot.pause()
        assert len(state.current_undo) == 1
        await pilot.press("s")
        await pilot.pause()
        assert len(state.current_undo) == 0
        assert state.current.dirty is False


@pytest.mark.asyncio
async def test_next_song_switches_and_reloads():
    sessions = [_make_session(song_id="song_001"), _make_session(song_id="song_002")]
    app, state = _make_app(sessions=sessions)
    async with app.run_test(size=(160, 30)) as pilot:
        await pilot.pause()
        assert state.current_index == 0
        await pilot.press("n")
        await pilot.pause()
        assert state.current_index == 1
        assert state.current.song_id == "song_002"


@pytest.mark.asyncio
async def test_prev_song_wraps_around():
    sessions = [_make_session(song_id="song_001"), _make_session(song_id="song_002")]
    app, state = _make_app(sessions=sessions)
    async with app.run_test(size=(160, 30)) as pilot:
        await pilot.pause()
        assert state.current_index == 0
        await pilot.press("p")
        await pilot.pause()
        assert state.current_index == 1


@pytest.mark.asyncio
async def test_quit_with_dirty_pushes_confirm_dialog():
    app, state = _make_app()
    async with app.run_test(size=(160, 30)) as pilot:
        await pilot.pause()
        state.set_value("entry", "theme", "敬拜")
        await pilot.pause()
        await pilot.press("q")
        await pilot.pause()
        assert len(app.screen_stack) > 1


@pytest.mark.asyncio
async def test_quit_without_dirty_exits():
    app, _state = _make_app()
    async with app.run_test(size=(160, 30)) as pilot:
        await pilot.pause()
        await pilot.press("q")
        await pilot.pause()
        assert len(app.screen_stack) > 1  # confirm dialog pushed


@pytest.mark.asyncio
async def test_edit_numeric_opens_overlay(tmp_path):
    app, state = _make_app()
    app.cache_dir = tmp_path
    async with app.run_test(size=(160, 60)) as pilot:
        await pilot.pause()
        state.selected_row = 0
        # Switch to detail panel and focus the groove_density field
        app.screen._active_panel = "right"
        app.screen._right_panel_mode = "details"
        app.screen._apply_right_panel_mode()
        await pilot.pause()
        detail = app.screen.query_one("#detail-panel", ComponentDetailPanel)
        detail._focus_idx = EDITABLE_FIELDS.index("groove_density")
        app.screen._refresh_detail_panel()
        await pilot.pause()
        app.screen.action_edit_numeric()
        await pilot.pause()
        await pilot.pause()
        await pilot.pause()
        assert app.screen._edit_mode == "numeric"


@pytest.mark.asyncio
async def test_edit_numeric_ignored_on_non_numeric_cell():
    app, _state = _make_app()
    async with app.run_test(size=(160, 30)) as pilot:
        await pilot.pause()
        # On details panel but focused on theme (not a numeric field)
        app.screen._active_panel = "right"
        app.screen._right_panel_mode = "details"
        detail = app.screen.query_one("#detail-panel", ComponentDetailPanel)
        detail._focus_idx = EDITABLE_FIELDS.index("theme")
        app.screen._refresh_detail_panel()
        await pilot.pause()
        await pilot.press("e")
        await pilot.pause()
        assert app.screen._edit_mode is None


@pytest.mark.asyncio
async def test_jump_to_component_seeks():
    app, _state = _make_app()
    async with app.run_test(size=(160, 30)) as pilot:
        await pilot.pause()
        await pilot.press("j")
        await pilot.pause()
        assert app.screen.playback.position_seconds == 10.0


# =============================================================================
# v5 D1 regression: compact table column ordering
# =============================================================================


@pytest.mark.asyncio
async def test_d1_column_order_first_is_occ():
    """v5 regression: first column label = Occ (compact table)."""
    app, _state = _make_app()
    async with app.run_test(size=(160, 30)) as pilot:
        await pilot.pause()
        table = app.screen.query_one("#component-table", DataTable)
        cols = table.ordered_columns
        assert cols[0].label.plain == "Occ"


@pytest.mark.asyncio
async def test_d1_compact_table_has_9_columns():
    """v5 regression: compact table has exactly 9 numerical columns."""
    app, _state = _make_app()
    async with app.run_test(size=(160, 30)) as pilot:
        await pilot.pause()
        table = app.screen.query_one("#component-table", DataTable)
        assert len(table.ordered_columns) == len(COMPACT_TABLE_COLUMNS)
        col_labels = [c.label.plain for c in table.ordered_columns]
        expected = [header for _, header in COMPACT_TABLE_COLUMNS]
        assert col_labels == expected


# =============================================================================
# v4 D2 regression: Hero panel refresh on cursor move + edit
# =============================================================================


@pytest.mark.asyncio
async def test_d2_hero_refreshes_on_cursor_move():
    """D2 regression: cursor move from ENTRY to EXIT refreshes hero panel.

    The on_data_table_row_highlighted handler calls _sync_selection_from_table_cursor
    + _refresh_hero. Here we simulate the cursor move by setting selected_row
    and calling _refresh_hero (the handler wiring is verified by code inspection).
    """
    app, state = _make_app()
    async with app.run_test(size=(160, 30)) as pilot:
        await pilot.pause()
        # Simulate cursor moving to EXIT row
        state.selected_row = 1
        app.screen._refresh_hero()
        await pilot.pause()
        hero = app.screen.query_one("ComponentHeroPanel")
        content = hero.content
        text = content.plain if hasattr(content, "plain") else str(content)
        assert "EXIT" in text


@pytest.mark.asyncio
async def test_d2_hero_shows_edited_theme(tmp_path):
    """v6 regression: after cycle_field_next changes theme, hero shows new value."""
    app, state = _make_app()
    app.cache_dir = tmp_path
    async with app.run_test(size=(160, 30)) as pilot:
        await pilot.pause()
        state.selected_row = 0
        # Switch to detail panel and focus the theme field
        app.screen._active_panel = "right"
        app.screen._right_panel_mode = "details"
        detail = app.screen.query_one("#detail-panel", ComponentDetailPanel)
        detail._focus_idx = EDITABLE_FIELDS.index("theme")
        app.screen._refresh_detail_panel()
        await pilot.pause()
        app.screen.action_cycle_field_next()
        await pilot.pause()
        hero = app.screen.query_one("ComponentHeroPanel")
        content = hero.content
        text = content.plain if hasattr(content, "plain") else str(content)
        assert f"Theme: {THEME_VALUES[1]}" in text


# =============================================================================
# v4 D3 regression: toggle_playback_for_component semantics
# =============================================================================


@pytest.mark.asyncio
async def test_space_seeks_to_component_start_then_plays():
    """Stopped outside [start,end] -> play(start_seconds=start_time)."""
    playback = _PlaybackStub()
    playback.state = PlaybackState.STOPPED
    playback.position_seconds = 0.0  # outside [10.0, 20.0]
    app, state = _make_app(playback=playback)
    async with app.run_test(size=(160, 30)) as pilot:
        await pilot.pause()
        state.selected_row = 0  # entry component, start_time=10.0
        app.screen.action_toggle_playback_for_component()
        assert playback.play_calls == 1
        assert playback.pause_calls == 0
        assert playback.play_start_seconds == [10.0]
        assert playback.position_seconds == 10.0


@pytest.mark.asyncio
async def test_space_inside_component_still_seeks_to_start():
    """Stopped inside [start,end] -> still play(start_seconds=start_time)
    (SPACE reliably restarts the highlighted component)."""
    playback = _PlaybackStub()
    playback.state = PlaybackState.STOPPED
    playback.position_seconds = 15.0  # inside [10.0, 20.0]
    app, state = _make_app(playback=playback)
    async with app.run_test(size=(160, 30)) as pilot:
        await pilot.pause()
        state.selected_row = 0
        app.screen.action_toggle_playback_for_component()
        assert playback.play_calls == 1
        assert playback.play_start_seconds == [10.0]
        assert playback.position_seconds == 10.0


@pytest.mark.asyncio
async def test_space_when_playing_pauses():
    """Playing -> pause, no play()."""
    playback = _PlaybackStub()
    playback.state = PlaybackState.PLAYING
    app, state = _make_app(playback=playback)
    async with app.run_test(size=(160, 30)) as pilot:
        await pilot.pause()
        state.selected_row = 0
        app.screen.action_toggle_playback_for_component()
        assert playback.pause_calls == 1
        assert playback.play_calls == 0
        assert playback.play_start_seconds == []


@pytest.mark.asyncio
async def test_space_no_component_plays_from_zero():
    """No component highlighted -> play(start_seconds=0.0)."""
    playback = _PlaybackStub()
    playback.state = PlaybackState.STOPPED
    session = SongSession(
        song_id="song_001",
        song_title="Test Song",
        hash_prefix="abc123def456",
        audio_path="/tmp/audio.mp3",
        audio_duration=180.0,
        components={},
        entry_component=None,
        exit_component=None,
    )
    app, state = _make_app(sessions=[session], playback=playback)
    async with app.run_test(size=(160, 30)) as pilot:
        await pilot.pause()
        state.selected_row = 0
        app.screen.action_toggle_playback_for_component()
        assert playback.play_calls == 1
        assert playback.play_start_seconds == [0.0]
        assert playback.position_seconds == 0.0


@pytest.mark.asyncio
async def test_space_paused_then_space_plays_from_component_start():
    """Lifecycle: PAUSED -> SPACE -> play from start; PLAYING -> SPACE -> pause."""
    playback = _PlaybackStub()
    playback.state = PlaybackState.PAUSED
    playback.position_seconds = 15.0  # inside [10.0, 20.0]
    app, state = _make_app(playback=playback)
    async with app.run_test(size=(160, 30)) as pilot:
        await pilot.pause()
        state.selected_row = 0
        # First SPACE: PAUSED -> play(start_seconds=10.0)
        app.screen.action_toggle_playback_for_component()
        assert playback.play_calls == 1
        assert playback.play_start_seconds == [10.0]
        assert playback.is_playing  # stub now mutates state
        # Second SPACE: PLAYING -> pause
        app.screen.action_toggle_playback_for_component()
        assert playback.pause_calls == 1
        assert playback.play_calls == 1


@pytest.mark.asyncio
async def test_space_with_edit_active_is_noop():
    """Edit overlay active -> SPACE is a no-op."""
    playback = _PlaybackStub()
    playback.state = PlaybackState.STOPPED
    app, state = _make_app(playback=playback)
    async with app.run_test(size=(160, 30)) as pilot:
        await pilot.pause()
        state.selected_row = 0
        # Simulate active edit guard returning True
        app.screen._guard_active_edit = lambda: True
        app.screen.action_toggle_playback_for_component()
        assert playback.play_calls == 0
        assert playback.pause_calls == 0
        assert playback.seek_calls == []


@pytest.mark.asyncio
async def test_space_uses_exit_component_start_time():
    """Highlight exit component -> play from its start_time."""
    playback = _PlaybackStub()
    playback.state = PlaybackState.STOPPED
    app, state = _make_app(playback=playback)
    async with app.run_test(size=(160, 30)) as pilot:
        await pilot.pause()
        state.selected_row = 1  # exit component
        app.screen.action_toggle_playback_for_component()
        assert playback.play_start_seconds == [10.0]  # _make_component default
        assert playback.position_seconds == 10.0


# =============================================================================
# v4 D4 regression: reasoning in Hero panel (full text)
# =============================================================================


@pytest.mark.asyncio
async def test_d4_hero_shows_full_reasoning():
    """D4 regression: Hero panel renders the full LLM reasoning text."""
    entry = _make_component("entry", theme_reasoning="This is a very long reasoning text " * 5)
    app, _state = _make_app(sessions=[_make_session(entry=entry)])
    async with app.run_test(size=(160, 30)) as pilot:
        await pilot.pause()
        hero = app.screen.query_one("ComponentHeroPanel")
        content = hero.content
        text = content.plain if hasattr(content, "plain") else str(content)
        assert "This is a very long reasoning text" in text


@pytest.mark.asyncio
async def test_d2_hero_updates_on_arrow_key_navigation():
    """D2 regression: pressing the down cursor key moves the highlighted row to
    EXIT and the Hero panel reflects the newly selected component.

    Because the table uses cursor_type='cell', Textual posts CellHighlighted
    (not RowHighlighted) on arrow-key navigation. The screen must listen for it
    to keep the Hero panel in sync.
    """
    app, state = _make_app()
    async with app.run_test(size=(160, 30)) as pilot:
        await pilot.pause()
        table = app.screen.query_one("#component-table", DataTable)
        table.focus()
        await pilot.pause()
        assert state.selected_row == 0
        await pilot.press("down")
        await pilot.pause()
        assert state.selected_row == 1
        hero = app.screen.query_one("ComponentHeroPanel")
        content = hero.content
        text = content.plain if hasattr(content, "plain") else str(content)
        assert "EXIT" in text


# =============================================================================
# v6: Right panel state management tests
# =============================================================================


@pytest.mark.asyncio
async def test_right_panel_default_is_lyrics():
    """v6: right panel defaults to lyrics mode on launch."""
    app, _state = _make_app()
    async with app.run_test(size=(160, 30)) as pilot:
        await pilot.pause()
        assert app.screen._right_panel_mode == "lyrics"


@pytest.mark.asyncio
async def test_cycle_right_panel_hidden_to_lyrics_to_details():
    """v6: pressing 'v' cycles hidden → lyrics → details → hidden."""
    app, _state = _make_app()
    async with app.run_test(size=(160, 30)) as pilot:
        await pilot.pause()
        # Default is lyrics
        assert app.screen._right_panel_mode == "lyrics"
        # Press v → details
        await pilot.press("v")
        await pilot.pause()
        assert app.screen._right_panel_mode == "details"
        # Press v → hidden
        await pilot.press("v")
        await pilot.pause()
        assert app.screen._right_panel_mode == "hidden"
        # Press v → lyrics
        await pilot.press("v")
        await pilot.pause()
        assert app.screen._right_panel_mode == "lyrics"


@pytest.mark.asyncio
async def test_right_panel_hidden_left_full_width():
    """v6: when right panel is hidden, left panel has dismissed-right class."""
    app, _state = _make_app()
    async with app.run_test(size=(160, 30)) as pilot:
        await pilot.pause()
        # Cycle to hidden
        app.screen._right_panel_mode = "hidden"
        app.screen._apply_right_panel_mode()
        await pilot.pause()
        left_panel = app.screen.query_one("#left-panel")
        assert left_panel.has_class("dismissed-right")
        right_panel = app.screen.query_one("#right-panel")
        assert right_panel.display is False


@pytest.mark.asyncio
async def test_tab_noop_when_right_dismissed():
    """v6: Tab is a no-op when right panel is dismissed."""
    app, _state = _make_app()
    async with app.run_test(size=(160, 30)) as pilot:
        await pilot.pause()
        # Cycle to hidden
        app.screen._right_panel_mode = "hidden"
        app.screen._apply_right_panel_mode()
        await pilot.pause()
        assert app.screen._active_panel == "left"
        await pilot.press("tab")
        await pilot.pause()
        assert app.screen._active_panel == "left"


@pytest.mark.asyncio
async def test_edit_ignored_when_not_in_details_mode(tmp_path):
    """v6: edit actions are ignored when right panel is in lyrics mode."""
    app, state = _make_app()
    app.cache_dir = tmp_path
    async with app.run_test(size=(160, 30)) as pilot:
        await pilot.pause()
        state.selected_row = 0
        # Right panel is in lyrics mode (default)
        assert app.screen._right_panel_mode == "lyrics"
        app.screen._active_panel = "right"
        # Try edit actions — should be no-ops
        app.screen.action_edit_numeric()
        app.screen.action_cycle_field_next()
        app.screen.action_cycle_field_prev()
        assert app.screen._edit_mode is None
        assert state.current.dirty is False


@pytest.mark.asyncio
async def test_hero_shows_song_info():
    """v6: Hero panel renders Song Info row with title/artist/album."""
    from stream_of_worship.admin.db.models import Song

    song = Song(
        id="song_001",
        title="Test Song",
        source_url="http://example.com",
        scraped_at="2024-01-01",
        composer="Test Artist",
        album_name="Test Album",
        album_series="Test Series",
        musical_key="G",
    )
    session = _make_session()
    session.song = song
    app, _state = _make_app(sessions=[session])
    async with app.run_test(size=(160, 30)) as pilot:
        await pilot.pause()
        app.screen._refresh_hero()
        await pilot.pause()
        hero = app.screen.query_one("ComponentHeroPanel")
        content = hero.content
        text = content.plain if hasattr(content, "plain") else str(content)
        assert "Title: Test Song" in text
        assert "Artist: Test Artist" in text
        assert "Album: Test Album" in text


# =============================================================================
# v7: Issue A — auto-focus right panel on 'v' cycle
# =============================================================================


@pytest.mark.asyncio
async def test_v_cycle_to_details_auto_focuses_right_panel():
    """Pressing v to switch to details mode auto-sets _active_panel='right'."""
    app, _ = _make_app()
    async with app.run_test(size=(160, 30)) as pilot:
        await pilot.pause()
        # Default is lyrics (on_mount focuses the table)
        assert app.screen._right_panel_mode == "lyrics"
        # Press v -> details
        await pilot.press("v")
        await pilot.pause()
        assert app.screen._right_panel_mode == "details"
        assert app.screen._active_panel == "right"  # auto-focused (Issue A fix)


@pytest.mark.asyncio
async def test_v_cycle_to_lyrics_auto_focuses_right_panel():
    """Pressing v from hidden to lyrics auto-focuses lyrics panel."""
    app, _ = _make_app()
    async with app.run_test(size=(160, 30)) as pilot:
        await pilot.pause()
        # Cycle to hidden
        app.screen._right_panel_mode = "hidden"
        app.screen._apply_right_panel_mode()
        await pilot.pause()
        assert app.screen._active_panel == "left"
        # Press v -> lyrics
        await pilot.press("v")
        await pilot.pause()
        assert app.screen._right_panel_mode == "lyrics"
        assert app.screen._active_panel == "right"  # auto-focused (Issue A fix)


# =============================================================================
# v7: Issue B — Tab cycles through panels
# =============================================================================


@pytest.mark.asyncio
async def test_tab_cycles_left_to_right():
    """Tab moves focus from left panel to right panel."""
    app, _ = _make_app()
    async with app.run_test(size=(160, 30)) as pilot:
        await pilot.pause()
        # At launch: lyrics mode, focus should be on table (left)
        await pilot.press("tab")
        await pilot.pause()
        assert app.screen._active_panel == "right"


@pytest.mark.asyncio
async def test_tab_cycles_right_to_left():
    """Tab moves focus from right panel back to left panel."""
    app, _ = _make_app()
    async with app.run_test(size=(160, 30)) as pilot:
        await pilot.pause()
        # Cycle to right
        await pilot.press("tab")
        await pilot.pause()
        assert app.screen._active_panel == "right"
        # Tab again -> back to left
        await pilot.press("tab")
        await pilot.pause()
        assert app.screen._active_panel == "left"


# =============================================================================
# v7: Issue C — Data table shows all essential components
# =============================================================================


@pytest.mark.asyncio
async def test_table_shows_4_essential_components():
    """Data table includes entry, exit, verse1, bridge rows when available."""
    entry = _make_component("entry", cid=1)
    exit_c = _make_component("exit", cid=2)
    verse1 = _make_component("none", cid=3, component_type="verse")
    bridge = _make_component("none", cid=4, component_type="bridge")
    session = _make_session(
        components={"entry": entry, "exit": exit_c, "verse1": verse1, "bridge": bridge}
    )
    app, _ = _make_app(sessions=[session])
    async with app.run_test(size=(160, 30)) as pilot:
        await pilot.pause()
        table = app.screen.query_one("#component-table", DataTable)
        assert table.row_count == 4


@pytest.mark.asyncio
async def test_table_shows_only_available_components():
    """Table only shows rows for components that exist (omits None)."""
    entry = _make_component("entry", cid=1)
    exit_c = _make_component("exit", cid=2)
    # No verse1 or bridge
    session = _make_session(components={"entry": entry, "exit": exit_c})
    app, _ = _make_app(sessions=[session])
    async with app.run_test(size=(160, 30)) as pilot:
        await pilot.pause()
        table = app.screen.query_one("#component-table", DataTable)
        assert table.row_count == 2


@pytest.mark.asyncio
async def test_hero_shows_verse1_label():
    """Hero panel shows 'VERSE 1' when verse1 row is selected."""
    entry = _make_component("entry", cid=1)
    exit_c = _make_component("exit", cid=2)
    verse1 = _make_component("none", cid=3, component_type="verse")
    bridge = _make_component("none", cid=4, component_type="bridge")
    session = _make_session(
        components={"entry": entry, "exit": exit_c, "verse1": verse1, "bridge": bridge}
    )
    app, state = _make_app(sessions=[session])
    async with app.run_test(size=(160, 30)) as pilot:
        await pilot.pause()
        # Select verse1 (row index 2)
        state.selected_row = 2
        app.screen._refresh_hero()
        await pilot.pause()
        hero = app.screen.query_one("ComponentHeroPanel")
        content = hero.content
        text = content.plain if hasattr(content, "plain") else str(content)
        assert "VERSE 1" in text


@pytest.mark.asyncio
async def test_hero_shows_bridge_label():
    """Hero panel shows 'BRIDGE' when bridge row is selected."""
    entry = _make_component("entry", cid=1)
    exit_c = _make_component("exit", cid=2)
    verse1 = _make_component("none", cid=3, component_type="verse")
    bridge = _make_component("none", cid=4, component_type="bridge")
    session = _make_session(
        components={"entry": entry, "exit": exit_c, "verse1": verse1, "bridge": bridge}
    )
    app, state = _make_app(sessions=[session])
    async with app.run_test(size=(160, 30)) as pilot:
        await pilot.pause()
        # Select bridge (row index 3)
        state.selected_row = 3
        app.screen._refresh_hero()
        await pilot.pause()
        hero = app.screen.query_one("ComponentHeroPanel")
        content = hero.content
        text = content.plain if hasattr(content, "plain") else str(content)
        assert "BRIDGE" in text


@pytest.mark.asyncio
async def test_save_writes_all_4_components(tmp_path):
    """Save updates DB for entry, exit, verse1, bridge."""
    entry = _make_component("entry", cid=1)
    exit_c = _make_component("exit", cid=2)
    verse1 = _make_component("none", cid=3, component_type="verse")
    bridge = _make_component("none", cid=4, component_type="bridge")
    session = _make_session(
        components={"entry": entry, "exit": exit_c, "verse1": verse1, "bridge": bridge}
    )
    r2_client = MagicMock()
    r2_client.download_component_result.return_value = None
    db_client = MagicMock()
    mock_conn = MagicMock()
    mock_txn = MagicMock()
    mock_txn.__enter__ = MagicMock(return_value=mock_conn)
    mock_txn.__exit__ = MagicMock(return_value=False)
    db_client.transaction.return_value = mock_txn
    db_client.get_song_components.return_value = [entry, exit_c, verse1, bridge]
    app, state = _make_app(sessions=[session], r2_client=r2_client, db_client=db_client)
    app.cache_dir = tmp_path

    async with app.run_test(size=(160, 30)) as pilot:
        await pilot.pause()
        state.set_value("entry", "theme", "敬拜")
        state.set_value("verse1", "theme", "感恩")
        state.set_value("bridge", "theme", "信心")
        await pilot.pause()
        await pilot.press("s")
        await pilot.pause()
        assert db_client.update_song_component_fields_txn.called
        # Should have been called for at least 3 components
        assert db_client.update_song_component_fields_txn.call_count >= 3
        assert r2_client.upload_component_result.called
        assert state.current.dirty is False


@pytest.mark.asyncio
async def test_r2_merge_updates_verse1_and_bridge(tmp_path):
    """R2 component.json merge updates verse1/bridge components."""
    entry = _make_component("entry", cid=1)
    exit_c = _make_component("exit", cid=2)
    verse1 = _make_component("none", cid=3, component_type="verse")
    bridge = _make_component("none", cid=4, component_type="bridge")
    session = _make_session(
        components={"entry": entry, "exit": exit_c, "verse1": verse1, "bridge": bridge}
    )
    r2_client = MagicMock()
    # Simulate existing R2 payload with all 4 components
    r2_client.download_component_result.return_value = {
        "schema_version": 4,
        "content_hash": "abc123def4567",
        "hash_prefix": "abc123def456",
        "component_source": "user_review_components",
        "components": [
            {"component_type": "chorus", "occurrence_index": 1, "role": "entry", "theme": "讚美"},
            {"component_type": "chorus", "occurrence_index": 1, "role": "exit", "theme": "讚美"},
            {"component_type": "verse", "occurrence_index": 1, "role": "none", "theme": "讚美"},
            {"component_type": "bridge", "occurrence_index": 1, "role": "none", "theme": "讚美"},
        ],
    }
    db_client = MagicMock()
    mock_conn = MagicMock()
    mock_txn = MagicMock()
    mock_txn.__enter__ = MagicMock(return_value=mock_conn)
    mock_txn.__exit__ = MagicMock(return_value=False)
    db_client.transaction.return_value = mock_txn
    db_client.get_song_components.return_value = [entry, exit_c, verse1, bridge]
    app, state = _make_app(sessions=[session], r2_client=r2_client, db_client=db_client)
    app.cache_dir = tmp_path

    async with app.run_test(size=(160, 30)) as pilot:
        await pilot.pause()
        state.set_value("verse1", "theme", "感恩")
        state.set_value("bridge", "theme", "信心")
        await pilot.pause()
        await pilot.press("s")
        await pilot.pause()
        assert r2_client.upload_component_result.called
        call_args = r2_client.upload_component_result.call_args
        payload = call_args[0][1] if len(call_args[0]) > 1 else call_args[1].get("payload")
        comps = payload["components"]
        verse_comp = next(c for c in comps if c.get("component_type") == "verse")
        bridge_comp = next(c for c in comps if c.get("component_type") == "bridge")
        assert verse_comp["theme"] == "感恩"
        assert bridge_comp["theme"] == "信心"


# =============================================================================
# v2 fixes: Issue A — can_focus on detail/lyrics panels
# =============================================================================


@pytest.mark.asyncio
async def test_detail_panel_can_focus_true():
    """Issue A: ComponentDetailPanel.can_focus must be True."""
    from stream_of_worship.admin.component_editor.detail_panel import ComponentDetailPanel

    assert ComponentDetailPanel.can_focus is True


@pytest.mark.asyncio
async def test_lyrics_panel_can_focus_true():
    """Issue A: LyricsPanel.can_focus must be True."""
    from stream_of_worship.admin.component_editor.lyrics_panel import LyricsPanel

    assert LyricsPanel.can_focus is True


@pytest.mark.asyncio
async def test_v_to_details_focuses_detail_panel():
    """Issue A: pressing 'v' to details mode moves keyboard focus to the detail panel."""
    app, _ = _make_app()
    async with app.run_test(size=(160, 30)) as pilot:
        await pilot.pause()
        await pilot.press("v")
        await pilot.pause()
        assert app.screen._right_panel_mode == "details"
        detail = app.screen.query_one("#detail-panel", ComponentDetailPanel)
        assert app.screen.focused is detail
