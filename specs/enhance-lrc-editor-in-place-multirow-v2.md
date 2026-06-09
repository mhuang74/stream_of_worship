# Enhance Admin LRC Editor In-Row Editing And Text-Only Multi-Row Paste V2

## Summary

Replace the bottom edit input in the admin Lyrics Editor with an in-row edit overlay for
lyric text and timestamps. Add contiguous multi-row selection for copying lyric text, and
support pasting newline-separated lyric text rows into the table.

Paste is intentionally text-only. Timestamp changes remain supported only through explicit
in-row timestamp editing with `t`.

Chosen UX:

- `e` edits the selected row's Text cell in place.
- `t` edits the selected row's Time cell in place.
- `shift+up/down` expands a contiguous selected range.
- `ctrl+c` copies selected lyric text lines only.
- `ctrl+v` pastes lyric text from the app clipboard.
- Terminal bracketed paste also inserts lyric text rows when the table is focused.
- Pasted rows always receive timestamp `0.0`.
- Timestamp-looking pasted text, such as `[00:12.34]Text`, is inserted literally as lyric
  text and is not parsed.

## Key Changes

- In `src/stream_of_worship/admin/editor/screen.py`, remove the bottom `#edit-panel`
  from `compose()` and CSS so the footer can no longer obscure editing.
- Add one hidden overlay `Input`, for example `#row-edit-input`, inside a wrapper around
  `LyricLineTable`.
- Position the overlay over the selected Time or Text cell using the table cell region
  and current scroll offsets. Keep `DataTable` as the main table; do not replace it with
  custom row widgets.
- Scroll the target row into view before showing the overlay. If a target cell cannot be
  resolved after scroll or resize, keep focus on the table and notify that editing cannot
  start.
- Replace `_editing_text` / `_editing_timestamp` with a single edit mode state containing:
  - mode: `"text"` or `"timestamp"`;
  - target row index;
  - target column index.
- On text submit:
  - update only the captured target row with `EditorState.set_text(...)`;
  - refresh the edited row, autosave, hide the overlay, and return focus to the table.
- On timestamp submit:
  - parse using the existing timestamp parser and call `EditorState.set_timestamp(...)`;
  - on success, refresh the edited row, autosave, hide the overlay, and return focus to
    the table;
  - on parse failure, keep the overlay visible, preserve the typed value, do not autosave,
    and notify the user.
- On escape while editing, cancel the overlay edit without changing state and return focus
  to the table.
- While the overlay input is focused, table navigation keys must not change the selected
  row.

## Selection And Clipboard

- Keep range-selection UI state on `LRCEditorScreen`, not `EditorState`, because it is
  transient UI state:
  - `_selection_anchor: int | None`;
  - `_selection_end: int | None`;
  - helper returning the active contiguous range or the current row.
- Add bindings:
  - `shift+up` -> expand selection upward;
  - `shift+down` -> expand selection downward.
- For shift-range actions:
  - the anchor remains the row where range selection started;
  - the endpoint follows the shifted cursor movement;
  - `state.selected_index` and the table cursor move to the endpoint;
  - range clearing is suppressed for that action.
- Normal unmodified row navigation clears the active range.
- Render range selection through row labels:
  - current cursor row remains marked with `>`;
  - selected non-cursor rows use `*`;
  - refresh affected rows when selection changes.
- `ctrl+c` copies `"\n".join(line.text for selected rows)` to
  `self.app.copy_to_clipboard(...)`.
- `ctrl+v` reads `self.app.clipboard`; if empty, notify "Nothing to paste".
- Add `on_paste(events.Paste)` on the screen for external terminal paste when the table
  is focused.
- Avoid duplicate insertion if a terminal paste path emits both a paste event and a
  `ctrl+v` action for the same payload.
- Paste behavior:
  - split on universal newlines;
  - trim each line and ignore blank lines;
  - do not parse timestamp prefixes;
  - insert each non-empty line as lyric text with timestamp `0.0`;
  - insert after the selected range end, or after the current row if no range is active;
  - select the first inserted row, clear the range, rebuild the table, autosave, and
    notify inserted row count;
  - if blank lines were dropped, include the dropped blank count in the notification.
- Keep `delete_line` scoped to the current cursor row; clear any active selection before
  deleting.

## State And Helpers

- Keep `EditorState.insert_lines_after(index, texts)` as the shared multi-row insertion
  path for canonical lyric insertion and text-only paste.
- Do not add timestamp-preserving paste helpers; `insert_lrc_lines_after(index, lines)` is
  out of scope.
- Extend undo/redo coverage for pasted multi-line rows using the existing `insert_lines`
  undo entry path.
- Ensure `original_timestamps` stays aligned when inserting zero-timestamp rows, including
  when padding is active.
- Add small parsing/formatting helpers in `screen.py` or a local editor helper module:
  - parse pasted text into `list[str]`;
  - report inserted and dropped blank line counts;
  - format copied rows as lyric text only.

## Test Plan

- Update `tests/admin/services/test_lrc_editor_screen.py`:
  - no bottom `#edit-panel` exists;
  - pressing `e` shows an in-row input over the Text cell and submit updates only the
    captured target row;
  - pressing `t` shows an in-row input over the Time cell and submit updates timestamp;
  - invalid timestamp submit keeps the overlay open, preserves the typed value, warns,
    and does not autosave;
  - escape cancels in-row edit and restores table focus;
  - down navigation does not change selected row while the in-row input is focused;
  - overlay remains aligned after vertical scroll;
  - overlay remains aligned in narrow terminal layouts and after terminal resize;
  - offscreen target rows are scrolled into view before overlay placement;
  - footer remains below editor body in small terminal sizes.
- Add multi-selection tests:
  - `shift+down` selects a contiguous range from anchor to endpoint;
  - `shift+up` shrinks or expands the range correctly;
  - normal navigation clears range selection;
  - row labels show cursor and selected non-cursor markers;
  - delete affects only the cursor row and clears selection.
- Add clipboard tests:
  - copying selected rows writes lyric text only, joined by newlines;
  - copying without a range copies the current row only;
  - pasting newline-separated lyrics inserts multiple draft rows after the current
    row/range;
  - pasted rows always have timestamp `0.0`;
  - timestamp-looking pasted text, such as `[00:12.34]Text`, is inserted literally as
    lyric text with timestamp `0.0`;
  - blank pasted lines are ignored and reported;
  - terminal paste and `ctrl+v` do not double-insert the same paste payload.
- Update `tests/admin/services/test_lrc_editor.py`:
  - `insert_lines_after` inserts multiple zero-timestamp rows;
  - undo removes all inserted paste rows;
  - redo restores all inserted paste rows;
  - original timestamp tracking stays aligned after multi-row insert;
  - original timestamp tracking stays aligned when inserting rows while padding is active
    and then adjusting padding.
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
- Overlay positioning may use the installed Textual cell-region helper, but scroll,
  resize, and narrow-layout behavior must be covered by tests.
- Copied plain text intentionally excludes timestamps for easier external lyric editing.
- Paste is intentionally text-only to avoid accidental timestamp corruption.
- Blank line preservation is out of scope for this version; dropped blanks are reported.
- Multi-row selection is contiguous only.
- Multi-row selection is for copy/paste placement only; multi-row delete remains out of
  scope.
- No R2, DB, upload, validation, playback, or autosave file format changes are required.
