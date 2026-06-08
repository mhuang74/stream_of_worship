"""Unit tests for LRC editor upload safety and autosave."""

import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from stream_of_worship.admin.services.lrc_parser import (
    LRCLine,
    LRCPreservedLine,
    serialize_lrc,
)
from stream_of_worship.admin.services.r2 import R2ObjectIdentity
from stream_of_worship.admin.editor.autosave import (
    AutosaveState,
    autosave_exists,
    clear_autosave,
    get_autosave_path,
    load_autosave,
    save_autosave,
)
from stream_of_worship.admin.editor.upload import (
    check_active_lrc_job,
    save_local_draft,
    save_local_backup,
    upload_r2_backup,
    upload_revised_lrc,
)
from stream_of_worship.admin.editor.state import EditorState
from stream_of_worship.admin.db.models import Recording


def _make_lines(timestamps_texts):
    return [
        LRCLine(time_seconds=ts, text=txt, raw_timestamp="[00:00.00]")
        for ts, txt in timestamps_texts
    ]


class TestAutosave:
    def test_save_and_load_round_trip(self, tmp_path):
        state = AutosaveState(
            timed_lines=_make_lines([(10.0, "A"), (20.0, "B")]),
            preserved_lines=[LRCPreservedLine(raw="[ti:Title]", tag="ti", value="Title")],
            transcribed_identity=R2ObjectIdentity(exists=True, etag="abc123"),
            dirty=True,
            source_mode="r2",
        )
        path = save_autosave(tmp_path, "abc123def456", state)
        assert path.exists()

        loaded = load_autosave(tmp_path, "abc123def456")
        assert loaded is not None
        assert len(loaded.timed_lines) == 2
        assert loaded.timed_lines[0].text == "A"
        assert loaded.dirty is True
        assert loaded.source_mode == "r2"
        assert loaded.transcribed_identity.etag == "abc123"

    def test_autosave_exists(self, tmp_path):
        assert not autosave_exists(tmp_path, "abc123")
        state = AutosaveState(
            timed_lines=[],
            preserved_lines=[],
            transcribed_identity=R2ObjectIdentity(exists=False),
            dirty=False,
            source_mode="catalog",
        )
        save_autosave(tmp_path, "abc123", state)
        assert autosave_exists(tmp_path, "abc123")

    def test_clear_autosave(self, tmp_path):
        state = AutosaveState(
            timed_lines=[],
            preserved_lines=[],
            transcribed_identity=R2ObjectIdentity(exists=False),
            dirty=False,
            source_mode="catalog",
        )
        save_autosave(tmp_path, "abc123", state)
        assert autosave_exists(tmp_path, "abc123")
        clear_autosave(tmp_path, "abc123")
        assert not autosave_exists(tmp_path, "abc123")

    def test_clear_nonexistent_autosave(self, tmp_path):
        clear_autosave(tmp_path, "nonexistent")

    def test_load_corrupted_autosave(self, tmp_path):
        path = get_autosave_path(tmp_path, "abc123")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("not valid json{", encoding="utf-8")
        result = load_autosave(tmp_path, "abc123")
        assert result is None

    def test_to_dict_from_dict_round_trip(self):
        state = AutosaveState(
            timed_lines=_make_lines([(5.0, "X")]),
            preserved_lines=[],
            transcribed_identity=R2ObjectIdentity(exists=False),
            dirty=True,
            source_mode="catalog",
        )
        d = state.to_dict()
        restored = AutosaveState.from_dict(d)
        assert len(restored.timed_lines) == 1
        assert restored.timed_lines[0].text == "X"
        assert restored.dirty is True

    def test_selected_index_round_trip(self, tmp_path):
        state = AutosaveState(
            timed_lines=_make_lines([(10.0, "A"), (20.0, "B"), (30.0, "C")]),
            preserved_lines=[],
            transcribed_identity=R2ObjectIdentity(exists=False),
            dirty=True,
            source_mode="catalog",
            selected_index=2,
        )
        path = save_autosave(tmp_path, "abc123", state)
        loaded = load_autosave(tmp_path, "abc123")
        assert loaded is not None
        assert loaded.selected_index == 2


class TestSaveLocalDraft:
    def test_creates_timestamped_file(self, tmp_path):
        content = "[00:10.00]Hello\n"
        path = save_local_draft(tmp_path, "abc123", content)
        assert path.exists()
        assert path.read_text(encoding="utf-8") == content
        assert "lyrics.edited." in path.name
        assert path.name.endswith(".lrc")

    def test_never_overwrites(self, tmp_path):
        content1 = "[00:10.00]First\n"
        content2 = "[00:10.00]Second\n"
        path1 = save_local_draft(tmp_path, "abc123", content1)
        import time
        time.sleep(1.1)
        path2 = save_local_draft(tmp_path, "abc123", content2)
        assert path1 != path2
        assert path1.read_text(encoding="utf-8") == content1
        assert path2.read_text(encoding="utf-8") == content2


class TestSaveLocalBackup:
    def test_creates_backup_when_content_exists(self, tmp_path):
        path = save_local_backup(tmp_path, "abc123", "original content")
        assert path is not None
        assert path.exists()
        assert "lyrics.backup." in path.name

    def test_returns_none_when_no_content(self, tmp_path):
        result = save_local_backup(tmp_path, "abc123", "")
        assert result is None

        result = save_local_backup(tmp_path, "abc123", None)
        assert result is None


class TestUploadR2Backup:
    def test_uploads_when_content_exists(self):
        r2_client = MagicMock()
        r2_client.upload_bytes.return_value = "s3://bucket/abc123/backups/lyrics.20260102-120000.lrc"
        result = upload_r2_backup(r2_client, "abc123", "original content")
        assert result is not None
        assert "backups/" in result
        r2_client.upload_bytes.assert_called_once()

    def test_returns_none_when_no_content(self):
        r2_client = MagicMock()
        result = upload_r2_backup(r2_client, "abc123", "")
        assert result is None
        r2_client.upload_bytes.assert_not_called()


class TestCheckActiveLrcJob:
    def test_active_job_detected(self):
        db_client = MagicMock()
        recording = Recording(
            content_hash="a" * 64,
            hash_prefix="abc123def456",
            original_filename="test.mp3",
            file_size_bytes=1000,
            imported_at="2024-01-01",
            lrc_status="processing",
            lrc_job_id="job-123",
        )
        db_client.get_recording_by_hash.return_value = recording
        active, job_id = check_active_lrc_job(db_client, "abc123def456")
        assert active is True
        assert job_id == "job-123"

    def test_no_active_job(self):
        db_client = MagicMock()
        recording = Recording(
            content_hash="a" * 64,
            hash_prefix="abc123def456",
            original_filename="test.mp3",
            file_size_bytes=1000,
            imported_at="2024-01-01",
            lrc_status="completed",
            lrc_job_id=None,
        )
        db_client.get_recording_by_hash.return_value = recording
        active, job_id = check_active_lrc_job(db_client, "abc123def456")
        assert active is False
        assert job_id == ""

    def test_no_recording(self):
        db_client = MagicMock()
        db_client.get_recording_by_hash.return_value = None
        active, job_id = check_active_lrc_job(db_client, "abc123def456")
        assert active is False


class TestUploadRevisedLrc:
    def test_manual_editor_upload_does_not_force_review_visibility(self, tmp_path):
        r2_client = MagicMock()
        r2_client.upload_lrc.return_value = "s3://bucket/abc123def456/lyrics.lrc"

        db_client = MagicMock()
        db_client.get_recording_by_hash.return_value = Recording(
            content_hash="a" * 64,
            hash_prefix="abc123def456",
            original_filename="test.mp3",
            file_size_bytes=1000,
            imported_at="2024-01-01",
            lrc_status="completed",
            lrc_job_id=None,
        )

        state = EditorState(
            timed_lines=_make_lines([(10.0, "Manual edit")]),
            preserved_lines=[],
            original_serialized="",
            original_preserved_lines=[],
            transcribed_identity=R2ObjectIdentity(exists=True, etag="etag-1"),
        )

        result = upload_revised_lrc(
            r2_client=r2_client,
            db_client=db_client,
            cache_dir=tmp_path,
            state=state,
            original_transcribed_content=None,
            hash_prefix="abc123def456",
            force=True,
        )

        assert result.success is True
        db_client.update_recording_lrc.assert_called_once_with(
            hash_prefix="abc123def456",
            r2_lrc_url="s3://bucket/abc123def456/lyrics.lrc",
        )


class TestEditorState:
    def test_set_timestamp(self):
        state = EditorState(
            timed_lines=_make_lines([(0.0, "A"), (0.0, "B")]),
            preserved_lines=[],
            original_serialized="",
            original_preserved_lines=[],
            transcribed_identity=R2ObjectIdentity(exists=False),
        )
        state.set_timestamp(0, 10.5)
        assert state.timed_lines[0].time_seconds == 10.5
        assert state.dirty is True

    def test_set_timestamp_clamps_negative(self):
        state = EditorState(
            timed_lines=_make_lines([(10.0, "A")]),
            preserved_lines=[],
            original_serialized="",
            original_preserved_lines=[],
            transcribed_identity=R2ObjectIdentity(exists=False),
        )
        state.set_timestamp(0, -5.0)
        assert state.timed_lines[0].time_seconds == 0.0

    def test_set_text(self):
        state = EditorState(
            timed_lines=_make_lines([(0.0, "Old")]),
            preserved_lines=[],
            original_serialized="",
            original_preserved_lines=[],
            transcribed_identity=R2ObjectIdentity(exists=False),
        )
        state.set_text(0, "New")
        assert state.timed_lines[0].text == "New"
        assert state.dirty is True

    def test_insert_after(self):
        state = EditorState(
            timed_lines=_make_lines([(0.0, "A"), (0.0, "C")]),
            preserved_lines=[],
            original_serialized="",
            original_preserved_lines=[],
            transcribed_identity=R2ObjectIdentity(exists=False),
            selected_index=0,
        )
        state.insert_after(0, text="B", time_seconds=5.0)
        assert len(state.timed_lines) == 3
        assert state.timed_lines[1].text == "B"
        assert state.timed_lines[1].time_seconds == 5.0

    def test_insert_before(self):
        state = EditorState(
            timed_lines=_make_lines([(0.0, "A"), (0.0, "C")]),
            preserved_lines=[],
            original_serialized="",
            original_preserved_lines=[],
            transcribed_identity=R2ObjectIdentity(exists=False),
            selected_index=1,
        )
        state.insert_before(1, text="B", time_seconds=5.0)
        assert len(state.timed_lines) == 3
        assert state.timed_lines[1].text == "B"

    def test_delete_line(self):
        state = EditorState(
            timed_lines=_make_lines([(0.0, "A"), (0.0, "B"), (0.0, "C")]),
            preserved_lines=[],
            original_serialized="",
            original_preserved_lines=[],
            transcribed_identity=R2ObjectIdentity(exists=False),
            selected_index=1,
        )
        deleted = state.delete_line(1)
        assert deleted.text == "B"
        assert len(state.timed_lines) == 2
        assert state.dirty is True

    def test_delete_last_line_adjusts_selection(self):
        state = EditorState(
            timed_lines=_make_lines([(0.0, "A"), (0.0, "B")]),
            preserved_lines=[],
            original_serialized="",
            original_preserved_lines=[],
            transcribed_identity=R2ObjectIdentity(exists=False),
            selected_index=1,
        )
        state.delete_line(1)
        assert state.selected_index == 0

    def test_select_line_clamping(self):
        state = EditorState(
            timed_lines=_make_lines([(0.0, "A"), (0.0, "B")]),
            preserved_lines=[],
            original_serialized="",
            original_preserved_lines=[],
            transcribed_identity=R2ObjectIdentity(exists=False),
        )
        state.select_line(5)
        assert state.selected_index == 1
        state.select_line(-1)
        assert state.selected_index == 0

    def test_serialize(self):
        state = EditorState(
            timed_lines=_make_lines([(10.5, "Hello"), (20.75, "World")]),
            preserved_lines=[],
            original_serialized="",
            original_preserved_lines=[],
            transcribed_identity=R2ObjectIdentity(exists=False),
        )
        result = state.serialize()
        assert "[00:10.50]Hello" in result
        assert "[00:20.75]World" in result


class TestUndoRedo:
    def _make_state(self, lines=None):
        if lines is None:
            lines = _make_lines([(0.0, "A"), (5.0, "B"), (10.0, "C")])
        return EditorState(
            timed_lines=lines,
            preserved_lines=[],
            original_serialized="",
            original_preserved_lines=[],
            transcribed_identity=R2ObjectIdentity(exists=False),
        )

    def test_undo_empty_stack_returns_false(self):
        state = self._make_state()
        assert state.undo() is False

    def test_redo_empty_stack_returns_false(self):
        state = self._make_state()
        assert state.redo() is False

    def test_undo_set_text(self):
        state = self._make_state()
        state.set_text(1, "B-edited")
        assert state.timed_lines[1].text == "B-edited"
        assert state.undo() is True
        assert state.timed_lines[1].text == "B"

    def test_undo_set_timestamp(self):
        state = self._make_state()
        state.set_timestamp(1, 99.0)
        assert state.timed_lines[1].time_seconds == 99.0
        assert state.undo() is True
        assert state.timed_lines[1].time_seconds == 5.0

    def test_undo_delete_line(self):
        state = self._make_state()
        state.delete_line(1)
        assert len(state.timed_lines) == 2
        assert state.undo() is True
        assert len(state.timed_lines) == 3
        assert state.timed_lines[1].text == "B"
        assert state.timed_lines[1].time_seconds == 5.0

    def test_undo_insert_after(self):
        state = self._make_state()
        state.insert_after(0, text="X", time_seconds=2.5)
        assert len(state.timed_lines) == 4
        assert state.undo() is True
        assert len(state.timed_lines) == 3
        assert state.timed_lines[0].text == "A"

    def test_undo_insert_before(self):
        state = self._make_state()
        state.insert_before(1, text="X", time_seconds=2.5)
        assert len(state.timed_lines) == 4
        assert state.undo() is True
        assert len(state.timed_lines) == 3
        assert state.timed_lines[1].text == "B"

    def test_redo_after_undo_set_text(self):
        state = self._make_state()
        state.set_text(1, "B-edited")
        state.undo()
        assert state.redo() is True
        assert state.timed_lines[1].text == "B-edited"

    def test_redo_after_undo_delete(self):
        state = self._make_state()
        state.delete_line(1)
        state.undo()
        assert state.redo() is True
        assert len(state.timed_lines) == 2

    def test_redo_after_undo_insert(self):
        state = self._make_state()
        state.insert_after(0, text="X", time_seconds=2.5)
        state.undo()
        assert state.redo() is True
        assert len(state.timed_lines) == 4
        assert state.timed_lines[1].text == "X"

    def test_new_mutation_clears_redo_stack(self):
        state = self._make_state()
        state.set_text(1, "B-edited")
        state.undo()
        assert len(state._redo_stack) == 1
        state.set_text(2, "C-edited")
        assert len(state._redo_stack) == 0

    def test_undo_restores_selected_index(self):
        state = self._make_state()
        state.set_text(2, "C-edited")
        state.undo()
        assert state.selected_index == 2

    def test_undo_delete_restores_selected_index(self):
        state = self._make_state()
        state.delete_line(1)
        state.undo()
        assert state.selected_index == 1

    def test_multiple_undo_redo(self):
        state = self._make_state()
        state.set_text(0, "A1")
        state.set_text(1, "B1")
        state.set_text(2, "C1")
        assert state.undo() is True
        assert state.timed_lines[2].text == "C"
        assert state.undo() is True
        assert state.timed_lines[1].text == "B"
        assert state.redo() is True
        assert state.timed_lines[1].text == "B1"
        assert state.redo() is True
        assert state.timed_lines[2].text == "C1"

    def test_undo_stack_capped_at_max(self):
        state = self._make_state()
        for i in range(150):
            state.set_text(0, f"v{i}")
        assert len(state._undo_stack) == 100


class TestInsertLinesAfter:
    def test_insert_multiple_lines(self):
        state = EditorState(
            timed_lines=_make_lines([(0.0, "A"), (0.0, "B")]),
            preserved_lines=[],
            original_serialized="",
            original_preserved_lines=[],
            transcribed_identity=R2ObjectIdentity(exists=False),
            selected_index=0,
        )
        state.insert_lines_after(0, ["X", "Y", "Z"])
        assert len(state.timed_lines) == 5
        assert state.timed_lines[1].text == "X"
        assert state.timed_lines[2].text == "Y"
        assert state.timed_lines[3].text == "Z"
        assert state.dirty is True

    def test_insert_at_end(self):
        state = EditorState(
            timed_lines=_make_lines([(0.0, "A")]),
            preserved_lines=[],
            original_serialized="",
            original_preserved_lines=[],
            transcribed_identity=R2ObjectIdentity(exists=False),
            selected_index=0,
        )
        state.insert_lines_after(0, ["X"])
        assert state.timed_lines[1].text == "X"

    def test_insert_empty_list_noop(self):
        state = EditorState(
            timed_lines=_make_lines([(0.0, "A")]),
            preserved_lines=[],
            original_serialized="",
            original_preserved_lines=[],
            transcribed_identity=R2ObjectIdentity(exists=False),
        )
        state.insert_lines_after(0, [])
        assert len(state.timed_lines) == 1

    def test_strips_and_filters_blank_lines(self):
        state = EditorState(
            timed_lines=_make_lines([(0.0, "A")]),
            preserved_lines=[],
            original_serialized="",
            original_preserved_lines=[],
            transcribed_identity=R2ObjectIdentity(exists=False),
            selected_index=0,
        )
        raw = ["  hello  ", "", "   ", "world"]
        filtered = [str(line).strip() for line in raw if str(line).strip()]
        state.insert_lines_after(0, filtered)
        assert len(state.timed_lines) == 3
        assert state.timed_lines[1].text == "hello"
        assert state.timed_lines[2].text == "world"

    def test_defends_against_non_string_json_items(self):
        state = EditorState(
            timed_lines=_make_lines([(0.0, "A")]),
            preserved_lines=[],
            original_serialized="",
            original_preserved_lines=[],
            transcribed_identity=R2ObjectIdentity(exists=False),
            selected_index=0,
        )
        raw = ["hello", None, "world"]
        filtered = [
            str(line).strip()
            for line in raw
            if str(line).strip() and str(line).strip() != "None"
        ]
        state.insert_lines_after(0, filtered)
        assert len(state.timed_lines) == 3
        assert state.timed_lines[1].text == "hello"
        assert state.timed_lines[2].text == "world"


class TestUndoRedoInsertLines:
    def _make_state(self):
        return EditorState(
            timed_lines=_make_lines([(0.0, "A"), (0.0, "B")]),
            preserved_lines=[],
            original_serialized="",
            original_preserved_lines=[],
            transcribed_identity=R2ObjectIdentity(exists=False),
            selected_index=0,
        )

    def test_undo_insert_lines_removes_all(self):
        state = self._make_state()
        state.insert_lines_after(0, ["X", "Y"])
        assert len(state.timed_lines) == 4
        assert state.undo() is True
        assert len(state.timed_lines) == 2
        assert state.timed_lines[0].text == "A"
        assert state.timed_lines[1].text == "B"

    def test_redo_insert_lines_restores_all(self):
        state = self._make_state()
        state.insert_lines_after(0, ["X", "Y"])
        state.undo()
        assert state.redo() is True
        assert len(state.timed_lines) == 4
        assert state.timed_lines[1].text == "X"
        assert state.timed_lines[2].text == "Y"

    def test_new_mutation_clears_redo_stack_after_insert_lines(self):
        state = self._make_state()
        state.insert_lines_after(0, ["X"])
        state.undo()
        assert len(state._redo_stack) == 1
        state.set_text(0, "A-edited")
        assert len(state._redo_stack) == 0
