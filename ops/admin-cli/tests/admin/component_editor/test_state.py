"""Tests for the Component Metadata editor state model."""

from stream_of_worship.admin.component_editor.state import (
    ComponentEditorState,
    ComponentUndoEntry,
    SongSession,
)
from stream_of_worship.admin.db.models import SongComponent


def _make_component(
    role: str = "entry",
    cid: int = 1,
    theme: str = "讚美",
    vocal_posture: str = "To God",
    groove_density: float = 0.5,
    energy_level: float = -18.0,
) -> SongComponent:
    return SongComponent(
        id=cid,
        song_id="song_001",
        content_hash="abc123def4567",
        component_type="chorus",
        occurrence_index=1,
        role=role,
        start_time=10.0,
        end_time=20.0,
        bpm=80.0,
        key="G",
        groove_density=groove_density,
        backbeat_strength=1.0,
        energy_level=energy_level,
        confidence=0.9,
        theme=theme,
        vocal_posture=vocal_posture,
    )


def _make_session(
    song_id: str = "song_001",
    entry: SongComponent | None = None,
    exit_comp: SongComponent | None = None,
) -> SongSession:
    if entry is None:
        entry = _make_component("entry", cid=1)
    if exit_comp is None:
        exit_comp = _make_component("exit", cid=2)
    return SongSession(
        song_id=song_id,
        song_title="Test Song",
        hash_prefix="abc123def456",
        audio_path="/tmp/audio.mp3",
        audio_duration=180.0,
        entry_component=entry,
        exit_component=exit_comp,
    )


def _make_session_with_none(
    song_id: str = "song_001",
    entry: SongComponent | None = None,
    exit_comp: SongComponent | None = None,
) -> SongSession:
    """Like _make_session but respects explicit None for entry/exit."""
    if entry is None and exit_comp is None:
        # Both None — create entry only
        entry = _make_component("entry", cid=1)
    elif entry is None:
        entry = _make_component("entry", cid=1)
    return SongSession(
        song_id=song_id,
        song_title="Test Song",
        hash_prefix="abc123def456",
        audio_path="/tmp/audio.mp3",
        audio_duration=180.0,
        entry_component=entry,
        exit_component=exit_comp,
    )


class TestComponentEditorState:
    """Tests for ComponentEditorState set_value / undo / redo."""

    def test_set_value_pushes_undo_and_marks_dirty(self):
        session = _make_session()
        state = ComponentEditorState(sessions=[session])
        state.set_value("entry", "theme", "敬拜")
        assert session.dirty is True
        assert ("entry", "theme") in session.working
        assert session.working[("entry", "theme")] == "敬拜"
        assert len(state.current_undo) == 1

    def test_set_value_noop_when_same_value(self):
        session = _make_session()
        state = ComponentEditorState(sessions=[session])
        # entry component's theme is "讚美" by default
        state.set_value("entry", "theme", "讚美")
        assert session.dirty is False
        assert len(state.current_undo) == 0

    def test_get_value_returns_working_when_dirty(self):
        session = _make_session()
        state = ComponentEditorState(sessions=[session])
        state.set_value("entry", "theme", "感恩")
        assert state.get_value("entry", "theme") == "感恩"

    def test_get_value_returns_persisted_when_not_dirty(self):
        session = _make_session()
        state = ComponentEditorState(sessions=[session])
        assert state.get_value("entry", "theme") == "讚美"

    def test_get_value_returns_none_for_missing_component(self):
        session = _make_session_with_none(exit_comp=None)
        state = ComponentEditorState(sessions=[session])
        assert state.get_value("exit", "theme") is None

    def test_undo_reverts_working_value(self):
        session = _make_session()
        state = ComponentEditorState(sessions=[session])
        state.set_value("entry", "theme", "感恩")
        entry = state.undo()
        assert entry is not None
        assert state.get_value("entry", "theme") == "讚美"
        assert len(state.current_redo) == 1

    def test_redo_reapplies_working_value(self):
        session = _make_session()
        state = ComponentEditorState(sessions=[session])
        state.set_value("entry", "theme", "感恩")
        state.undo()
        entry = state.redo()
        assert entry is not None
        assert state.get_value("entry", "theme") == "感恩"
        assert session.dirty is True

    def test_undo_returns_none_when_empty(self):
        session = _make_session()
        state = ComponentEditorState(sessions=[session])
        assert state.undo() is None

    def test_redo_returns_none_when_empty(self):
        session = _make_session()
        state = ComponentEditorState(sessions=[session])
        assert state.redo() is None

    def test_push_undo_max_100(self):
        session = _make_session()
        state = ComponentEditorState(sessions=[session])
        for i in range(110):
            state.push_undo(
                ComponentUndoEntry(
                    component_id=1,
                    component_role="entry",
                    field_name="theme",
                    old_value=f"v{i}",
                    new_value=f"v{i + 1}",
                )
            )
        assert len(state.current_undo) == 100

    def test_push_undo_clears_redo_stack(self):
        session = _make_session()
        state = ComponentEditorState(sessions=[session])
        state.set_value("entry", "theme", "感恩")
        state.undo()
        assert len(state.current_redo) == 1
        state.set_value("entry", "vocal_posture", "About God")
        assert len(state.current_redo) == 0

    def test_multi_session_undo_independence(self):
        session1 = _make_session(song_id="song_001")
        session2 = _make_session(song_id="song_002")
        state = ComponentEditorState(sessions=[session1, session2])
        state.set_value("entry", "theme", "感恩")
        state.current_index = 1
        state.set_value("entry", "theme", "敬拜")
        assert len(state.current_undo) == 1
        state.current_index = 0
        assert len(state.current_undo) == 1

    def test_clear_undo_stacks_clears_both_for_named_session(self):
        session1 = _make_session(song_id="song_001")
        session2 = _make_session(song_id="song_002")
        state = ComponentEditorState(sessions=[session1, session2])
        state.set_value("entry", "theme", "感恩")
        state.undo()
        state.current_index = 1
        state.set_value("entry", "theme", "敬拜")
        # session1 has undo=0 (popped), redo=1; session2 has undo=1, redo=0
        assert len(state._undo_stacks["song_001"]) == 0
        assert len(state._redo_stacks["song_001"]) == 1
        assert len(state._undo_stacks["song_002"]) == 1
        # Clear session1
        state.current_index = 0
        state.clear_undo_stacks(session1)
        assert len(state._undo_stacks["song_001"]) == 0
        assert len(state._redo_stacks["song_001"]) == 0
        # session2 untouched
        assert len(state._undo_stacks["song_002"]) == 1

    def test_undo_keyed_by_song_id_survives_session_removal(self):
        """C4 regression: undo stacks keyed by song_id, not id(session)."""
        session1 = _make_session(song_id="song_001")
        state = ComponentEditorState(sessions=[session1])
        state.set_value("entry", "theme", "感恩")
        assert len(state.current_undo) == 1
        # Simulate removing and re-adding a session with the same song_id
        new_session = _make_session(song_id="song_001")
        state.sessions[0] = new_session
        # The undo stack should still be accessible via song_id
        assert len(state.current_undo) == 1


class TestSongSession:
    """Tests for SongSession helpers."""

    def test_component_for_role_entry(self):
        entry = _make_component("entry", cid=1)
        exit_comp = _make_component("exit", cid=2)
        session = _make_session(entry=entry, exit_comp=exit_comp)
        assert session.component_for_role("entry") is entry
        assert session.component_for_role("exit") is exit_comp

    def test_component_for_role_none(self):
        session = _make_session_with_none(exit_comp=None)
        assert session.component_for_role("exit") is None
