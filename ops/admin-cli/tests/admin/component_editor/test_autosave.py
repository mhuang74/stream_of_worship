"""Tests for the Component Metadata editor autosave."""

import json

from stream_of_worship.admin.component_editor.autosave import (
    ComponentAutosaveState,
    clear_autosave,
    get_autosave_path,
    load_autosave,
    save_autosave,
)


def _make_snapshot(
    song_id: str = "song_001",
    hash_prefix: str = "abc123def456",
    dirty: bool = True,
    r2_save_pending: bool = False,
) -> ComponentAutosaveState:
    return ComponentAutosaveState(
        song_id=song_id,
        hash_prefix=hash_prefix,
        working=[{"role": "entry", "field": "theme", "value": "敬拜"}],
        dirty=dirty,
        selected_row=0,
        selected_column_key="theme",
        r2_save_pending=r2_save_pending,
    )


class TestComponentAutosave:
    """Tests for ComponentAutosaveState round-trip and disk helpers."""

    def test_to_dict_from_dict_roundtrip(self, tmp_path):
        snapshot = _make_snapshot()
        d = snapshot.to_dict()
        restored = ComponentAutosaveState.from_dict(d)
        assert restored.song_id == snapshot.song_id
        assert restored.hash_prefix == snapshot.hash_prefix
        assert restored.working == snapshot.working
        assert restored.dirty == snapshot.dirty
        assert restored.selected_row == snapshot.selected_row
        assert restored.selected_column_key == snapshot.selected_column_key
        assert restored.r2_save_pending == snapshot.r2_save_pending

    def test_r2_save_pending_survives_roundtrip(self):
        """B1 regression: r2_save_pending must survive to_dict/from_dict."""
        snapshot = _make_snapshot(r2_save_pending=True)
        d = snapshot.to_dict()
        restored = ComponentAutosaveState.from_dict(d)
        assert restored.r2_save_pending is True

    def test_save_and_load_autosave_roundtrip(self, tmp_path):
        snapshot = _make_snapshot()
        ok = save_autosave(tmp_path, snapshot)
        assert ok is True
        loaded = load_autosave(tmp_path, snapshot.hash_prefix)
        assert loaded is not None
        assert loaded.song_id == snapshot.song_id
        assert loaded.working == snapshot.working
        assert loaded.dirty is True

    def test_load_autosave_returns_none_when_missing(self, tmp_path):
        assert load_autosave(tmp_path, "nonexistent") is None

    def test_load_autosave_returns_none_on_corrupt_file(self, tmp_path):
        path = get_autosave_path(tmp_path, "abc123def456")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("not valid json", encoding="utf-8")
        assert load_autosave(tmp_path, "abc123def456") is None

    def test_clear_autosave_noop_when_missing(self, tmp_path):
        # Should not raise
        clear_autosave(tmp_path, "nonexistent")

    def test_clear_autosave_removes_existing(self, tmp_path):
        snapshot = _make_snapshot()
        save_autosave(tmp_path, snapshot)
        path = get_autosave_path(tmp_path, snapshot.hash_prefix)
        assert path.exists()
        clear_autosave(tmp_path, snapshot.hash_prefix)
        assert not path.exists()

    def test_save_autosave_writes_valid_json(self, tmp_path):
        snapshot = _make_snapshot()
        save_autosave(tmp_path, snapshot)
        path = get_autosave_path(tmp_path, snapshot.hash_prefix)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["song_id"] == snapshot.song_id
        assert data["r2_save_pending"] == snapshot.r2_save_pending

    def test_from_dict_defaults(self):
        """from_dict handles missing optional keys gracefully."""
        minimal = {"song_id": "song_001", "hash_prefix": "abc123def456"}
        restored = ComponentAutosaveState.from_dict(minimal)
        assert restored.song_id == "song_001"
        assert restored.working == []
        assert restored.dirty is False
        assert restored.r2_save_pending is False
        assert restored.selected_row == 0
        assert restored.selected_column_key == "role"
