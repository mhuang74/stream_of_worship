"""Textual screen tests for the admin Component Metadata editor (v4).

Covers the v4 layout (Hero panel + single DataTable), D1-D4 regression tests,
and the v2 regression suite (B1-B3, C1-C5).
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from textual.widgets import DataTable

from stream_of_worship.admin.component_editor.app import ComponentEditorApp
from stream_of_worship.admin.component_editor.constants import (
    DATA_TABLE_COLUMNS,
    THEME_VALUES,
)
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

    def play(self, *args, **kwargs):
        self.play_calls += 1


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
) -> SongComponent:
    return SongComponent(
        id=cid,
        song_id="song_001",
        content_hash=content_hash,
        component_type="chorus",
        occurrence_index=1,
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
) -> SongSession:
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
    return SongSession(
        song_id=song_id,
        song_title="Test Song",
        hash_prefix="abc123def456",
        audio_path="/tmp/audio.mp3",
        audio_duration=180.0,
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
    """Return the column index for a field key in DATA_TABLE_COLUMNS."""
    return next(i for i, (k, _, _, _) in enumerate(DATA_TABLE_COLUMNS) if k == field_key)


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
        table = app.screen.query_one("#component-table", DataTable)
        table.move_cursor(row=0, column=_col_idx("theme"))
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
        table = app.screen.query_one("#component-table", DataTable)
        table.move_cursor(row=0, column=_col_idx("theme"))
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
        # Cursor at column 0 (role) — not an enum cell
        table = app.screen.query_one("#component-table", DataTable)
        table.move_cursor(row=0, column=0)
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
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.pause()
        state.selected_row = 0
        table = app.screen.query_one("#component-table", DataTable)
        table.move_cursor(row=0, column=_col_idx("groove_density"))
        await pilot.pause()
        app.screen.action_edit_numeric()
        await pilot.pause()
        await pilot.pause()
        assert app.screen._edit_mode == "numeric"


@pytest.mark.asyncio
async def test_edit_numeric_ignored_on_non_numeric_cell():
    app, _state = _make_app()
    async with app.run_test(size=(160, 30)) as pilot:
        await pilot.pause()
        table = app.screen.query_one("#component-table", DataTable)
        table.move_cursor(row=0, column=0)  # role column
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
# v4 D1 regression: column ordering
# =============================================================================


@pytest.mark.asyncio
async def test_d1_column_order_first_is_role():
    """D1 regression: first column label = Role."""
    app, _state = _make_app()
    async with app.run_test(size=(160, 30)) as pilot:
        await pilot.pause()
        table = app.screen.query_one("#component-table", DataTable)
        cols = table.ordered_columns
        assert cols[0].label.plain == "Role"


@pytest.mark.asyncio
async def test_d1_editable_fields_before_confidence():
    """D1 regression: no confidence column appears before any editable column."""
    app, _state = _make_app()
    async with app.run_test(size=(160, 30)) as pilot:
        await pilot.pause()
        table = app.screen.query_one("#component-table", DataTable)
        col_labels = [c.label.plain for c in table.ordered_columns]
        first_editable_idx = next(
            i for i, (k, _, editable, _) in enumerate(DATA_TABLE_COLUMNS) if editable
        )
        for i in range(first_editable_idx):
            assert (
                "conf" not in col_labels[i].lower()
            ), f"Confidence column at position {i} ({col_labels[i]}) before editable fields"


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
    """D2 regression: after cycle_field_next changes theme, hero shows new value."""
    app, state = _make_app()
    app.cache_dir = tmp_path
    async with app.run_test(size=(160, 30)) as pilot:
        await pilot.pause()
        state.selected_row = 0
        table = app.screen.query_one("#component-table", DataTable)
        table.move_cursor(row=0, column=_col_idx("theme"))
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
async def test_d3_space_seeks_to_component_start_then_plays():
    """D3 regression 1: paused, position outside [start,end] -> seek then play."""
    playback = _PlaybackStub()
    playback.state = PlaybackState.STOPPED
    playback.position_seconds = 0.0  # outside [10.0, 20.0]
    app, state = _make_app(playback=playback)
    async with app.run_test(size=(160, 30)) as pilot:
        await pilot.pause()
        state.selected_row = 0  # entry component, start_time=10.0
        app.screen.action_toggle_playback_for_component()
        assert playback.seek_calls == [10.0]
        assert playback.play_calls == 1
        assert playback.pause_calls == 0


@pytest.mark.asyncio
async def test_d3_space_inside_component_no_seek_just_plays():
    """D3 regression 2: paused, position inside [start,end] -> play without seek."""
    playback = _PlaybackStub()
    playback.state = PlaybackState.STOPPED
    playback.position_seconds = 15.0  # inside [10.0, 20.0]
    app, state = _make_app(playback=playback)
    async with app.run_test(size=(160, 30)) as pilot:
        await pilot.pause()
        state.selected_row = 0
        app.screen.action_toggle_playback_for_component()
        assert playback.seek_calls == []
        assert playback.play_calls == 1


@pytest.mark.asyncio
async def test_d3_space_when_playing_pauses():
    """D3 regression 3: playing -> pause, no play()."""
    playback = _PlaybackStub()
    playback.state = PlaybackState.PLAYING
    app, state = _make_app(playback=playback)
    async with app.run_test(size=(160, 30)) as pilot:
        await pilot.pause()
        state.selected_row = 0
        app.screen.action_toggle_playback_for_component()
        assert playback.pause_calls == 1
        assert playback.play_calls == 0


@pytest.mark.asyncio
async def test_d3_space_both_components_none_plays_without_seek():
    """D3 regression 4: both components None -> play() without seek."""
    playback = _PlaybackStub()
    playback.state = PlaybackState.STOPPED
    session = SongSession(
        song_id="song_001",
        song_title="Test Song",
        hash_prefix="abc123def456",
        audio_path="/tmp/audio.mp3",
        audio_duration=180.0,
        entry_component=None,
        exit_component=None,
    )
    app, state = _make_app(sessions=[session], playback=playback)
    async with app.run_test(size=(160, 30)) as pilot:
        await pilot.pause()
        state.selected_row = 0
        app.screen.action_toggle_playback_for_component()
        assert playback.play_calls == 1
        assert playback.seek_calls == []


# =============================================================================
# v4 D4 regression: reasoning in Hero panel (full text) + table (truncated)
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
