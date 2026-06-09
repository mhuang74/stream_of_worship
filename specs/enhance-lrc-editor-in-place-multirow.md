# Enhance Admin LRC Editor In-Row Editing And Multi-Row Clipboard

## Summary

Replace the bottom edit input in the admin Lyrics Editor with an in-row edit overlay for
both lyric text and timestamps. Add contiguous multi-row selection for copying lyric
text, and support pasting newline-separated lyric rows into the table.

Chosen UX:

- `e` edits the selected row's Text cell in place.
- `t` edits the selected row's Time cell in place.
- `shift+up/down` expands a contiguous selected range.
- `ctrl+c` copies selected lyric text lines only.
- `ctrl+v` pastes from the app clipboard; terminal bracketed paste also inserts
  multi-row lyrics.
- Timestamped LRC paste input is accepted when detected, but normal copied output
  remains lyric text only.

## Key Changes

- In `src/stream_of_worship/admin/editor/screen.py`, remove the bottom `#edit-panel`
  from `compose()` and CSS so the footer can no longer obscure editing.
- Add one hidden overlay `Input`, for example `#row-edit-input`, inside a wrapper around
  `LyricLineTable`.
- Position the overlay over the selected Time or Text cell using the table cell region
  and current scroll offsets. Keep `DataTable` as the main table; do not replace it with
  custom row widgets.
- Replace `_editing_text` / `_editing_timestamp` with a single edit mode state
  containing:
  - mode: `"text"` or `"timestamp"`;
  - target row index;
  - target column index.
- On submit:
  - text mode calls `EditorState.set_text(...)`;
  - timestamp mode parses using the existing timestamp parser and calls
    `EditorState.set_timestamp(...)`;
  - refresh the edited row, autosave, hide the overlay, and return focus to the table.
- On escape while editing, cancel the overlay edit without changing state.

## Selection And Clipboard

- Keep range-selection UI state on `LRCEditorScreen`, not `EditorState`, because it is
  transient UI state:
  - `_selection_anchor: int | None`;
  - `_selection_end: int | None`;
  - helper returning the active contiguous range or the current row.
- Add bindings:
  - `shift+up` -> expand selection upward;
  - `shift+down` -> expand selection downward.
- Normal row navigation clears the active range.
- Render range selection through row labels:
  - current cursor row remains marked with `>`;
  - selected non-cursor rows use `*`;
  - refresh affected rows when selection changes.
- `ctrl+c` copies `"\n".join(line.text for selected rows)` to
  `self.app.copy_to_clipboard(...)`.
- `ctrl+v` reads `self.app.clipboard`; if empty, notify "Nothing to paste".
- Add `on_paste(events.Paste)` on the screen for external terminal paste when the table
  is focused.
- Paste behavior:
  - split on universal newlines;
  - trim each line and ignore blank lines;
  - if a line matches `[mm:ss.xx]text`, parse and preserve that timestamp;
  - otherwise insert it as lyric text with timestamp `0.0`;
  - insert after the selected range end, or after the current row if no range is active;
  - select the first inserted row, clear the range, rebuild the table, autosave, and
    notify inserted row count.
- Keep `delete_line` scoped to the current cursor row; clear any active selection before
  deleting.

## State And Helpers

- Add an `EditorState.insert_lrc_lines_after(index, lines)` helper accepting `LRCLine`
  objects, preserving timestamps and original timestamp tracking.
- Keep existing `insert_lines_after(index, texts)` for canonical lyric insertion and
  delegate internally to the new helper with zero timestamps.
- Extend undo/redo coverage for inserted multi-line pasted rows using the existing
  `insert_lines` undo entry path.
- Add small parsing helpers in `screen.py` or a local editor helper module:
  - parse pasted text into `list[LRCLine]`;
  - detect timestamped LRC rows;
  - format copied rows as lyric text only.

## Test Plan

- Update `tests/admin/services/test_lrc_editor_screen.py`:
  - no bottom `#edit-panel` exists;
  - pressing `e` shows an in-row input over the Text cell and submit updates only that
    row;
  - pressing `t` shows an in-row input over the Time cell and submit updates timestamp;
  - escape cancels in-row edit and restores table focus;
  - down navigation does not change selected row while the in-row input is focused;
  - footer remains below editor body in small terminal sizes.
- Add multi-selection tests:
  - `shift+down` selects a contiguous range;
  - `shift+up` shrinks or expands the range upward;
  - normal navigation clears range selection;
  - row labels show cursor and range markers.
- Add clipboard tests:
  - copying selected rows writes lyric text only, joined by newlines;
  - copying without a range copies the current row only;
  - pasting newline-separated lyrics inserts multiple draft rows after the current
    row/range;
  - pasting timestamped LRC rows preserves parsed timestamps;
  - blank pasted lines are ignored.
- Update `tests/admin/services/test_lrc_editor.py`:
  - `insert_lrc_lines_after` inserts multiple rows with timestamps;
  - undo removes all inserted paste rows;
  - redo restores all inserted paste rows and timestamps;
  - original timestamp tracking stays aligned after multi-row insert.
- Run focused tests:

```bash
PYTHONPATH=src uv run --python 3.11 --extra admin --extra test pytest \
  tests/admin/services/test_lrc_editor.py \
  tests/admin/services/test_lrc_editor_screen.py -v
```

- Then run all admin tests:

```bash
PYTHONPATH=src uv run --python 3.11 --extra admin --extra test pytest tests/admin/ -v
```

## Assumptions

- This change is limited to the admin LRC editor package.
- In-row editing means a focused overlay input aligned to the row/cell, because Textual
  `DataTable` does not natively host editable cell widgets.
- Copied plain text intentionally excludes timestamps for easier external lyric editing.
- Pasted timestamped LRC rows are accepted as a convenience but are not the default copy
  format.
- Multi-row selection is contiguous only.
- Multi-row selection is for copy/paste placement only; multi-row delete remains out of
  scope.
- No R2, DB, upload, validation, playback, or autosave file format changes are required.
