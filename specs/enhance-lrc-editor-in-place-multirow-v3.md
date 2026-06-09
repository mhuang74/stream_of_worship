# Enhance Admin LRC Editor In-Row Editing And Text-Only Multi-Row Paste V3

## Summary

Replace the bottom edit input in the admin Lyrics Editor with a single hidden `Input`
overlay that appears over the selected Time or Text cell. Add contiguous multi-row
selection for lyric-text copy, and support text-only multi-row paste from Textual's
application clipboard and terminal paste events.

This v3 plan resolves the conflicts between the prior v2 plans:

- Keep the existing `LyricLineTable`/`DataTable` architecture instead of replacing it
  with a manually edited table widget.
- Use Textual's built-in app clipboard APIs and paste events instead of adding
  `pyperclip`.
- Keep paste intentionally text-only: pasted lines always become draft rows with
  timestamp `0.0`.
- Keep Escape simple: cancel the overlay edit without committing changes.
- Do not add a large-paste confirmation modal unless a later product requirement asks
  for it.

Chosen UX:

- `e` edits the selected row's Text cell in place.
- `t` edits the selected row's Time cell in place.
- `shift+up/down` expands a contiguous selected range.
- `ctrl+c` copies selected lyric text lines only.
- `ctrl+v` pastes lyric text from the app clipboard.
- Terminal bracketed paste inserts lyric text rows when the table is focused.
- Pasted rows always receive timestamp `0.0`.
- Timestamp-looking pasted text, such as `[00:12.34]Text`, is inserted literally as
  lyric text and is not parsed.

## Key Changes

### In-Row Editing

- In `src/stream_of_worship/admin/editor/screen.py`, remove the bottom `#edit-panel`
  from `compose()` and remove its CSS rule so the footer can no longer obscure editing.
- Keep the existing `LyricLineTable` subclass and extend it only where needed for
  range-selection key handling and active-edit guards.
- Add one hidden overlay `Input`, for example `#row-edit-input`, in the editor body near
  `LyricLineTable`.
- Position the overlay over the selected Time or Text cell using the table cell region
  and current scroll offsets. Keep `DataTable` as the main table; do not replace rows
  with custom widgets and do not simulate text editing by rewriting cell strings on every
  keypress.
- Scroll the target row into view before showing the overlay. If the target cell cannot
  be resolved after scroll or resize, keep focus on the table and notify that editing
  cannot start.
- Replace `_editing_text` / `_editing_timestamp` with one screen-owned edit mode state:
  - mode: `"text"` or `"timestamp"`;
  - target row index captured when editing starts;
  - target column index captured when editing starts.
- On text submit:
  - update only the captured target row with `EditorState.set_text(...)`;
  - refresh the edited row or rebuild the table as needed;
  - autosave;
  - hide the overlay and return focus to the table.
- On timestamp submit:
  - parse using the existing timestamp parser and call `EditorState.set_timestamp(...)`;
  - on success, refresh the row, autosave, hide the overlay, and return focus to the
    table;
  - on parse failure, keep the overlay visible, preserve the typed value, do not autosave,
    and notify the user.
- On Escape while editing, cancel the overlay edit without changing state and return
  focus to the table.
- While the overlay input is focused, table navigation keys must not change the selected
  row.
- Guard conflicting actions while an overlay edit is active. Actions that change table
  structure, playback preview, undo/redo, copy/paste, save/upload, or quit should either
  cancel the edit explicitly or notify the user to finish editing first. Prefer the
  existing preview-guard style for consistency.

### Selection And Clipboard

- Keep range-selection UI state on `LRCEditorScreen`, not `EditorState`, because it is
  transient UI state:
  - `_selection_anchor: int | None`;
  - `_selection_end: int | None`;
  - helper returning the active contiguous range or the current row.
- Add bindings/actions for:
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
- Change copy behavior to lyric text only:
  - `ctrl+c` copies `"\n".join(line.text for selected rows)` to
    `self.app.copy_to_clipboard(...)`;
  - copying without a range copies the current row only;
  - timestamps are never copied.
- Change paste behavior to use Textual facilities, not `pyperclip`:
  - `ctrl+v` reads `self.app.clipboard`;
  - if the app clipboard is empty, notify "Nothing to paste";
  - add `on_paste(events.Paste)` on the screen for external terminal paste when the table
    is focused.
- Avoid duplicate insertion if a terminal paste path emits both a paste event and a
  `ctrl+v` action for the same payload.

### Text-Only Paste

- Parse pasted text with a small local helper:
  - split on universal newlines;
  - trim each line;
  - ignore blank lines;
  - return inserted line count and dropped blank count.
- Do not parse timestamp prefixes. A pasted string like `[00:12.34]Text` remains the
  literal lyric text.
- Insert each non-empty line as lyric text with timestamp `0.0`.
- Insert after the selected range end, or after the current row if no range is active.
- After paste:
  - select the first inserted row;
  - clear the active range;
  - rebuild or refresh the table;
  - autosave;
  - notify inserted row count;
  - if blank lines were dropped, include the dropped blank count in the notification.
- Keep `delete_line` scoped to the current cursor row; clear any active selection before
  deleting.

### State And Helpers

- Keep `EditorState.insert_lines_after(index, texts)` as the shared multi-row insertion
  path for canonical lyric insertion and text-only paste.
- Do not add timestamp-preserving paste helpers; `insert_lrc_lines_after(index, lines)` is
  out of scope.
- Keep undo/redo coverage for pasted multi-line rows through the existing `insert_lines`
  undo entry path.
- Ensure `original_timestamps` stays aligned when inserting zero-timestamp rows, including
  when padding is active.
- Add small helpers in `screen.py` or a local editor helper module:
  - parse pasted text into non-empty lyric rows plus dropped blank count;
  - format copied rows as lyric text only;
  - resolve current active selection range;
  - clear and refresh range-selection labels.
- Do not add `pyperclip` or any new dependency.
- Do not change R2, DB, upload, validation, playback, or autosave file formats.

## Test Plan

Update `tests/admin/services/test_lrc_editor_screen.py`:

- Assert no bottom `#edit-panel` exists.
- Pressing `e` shows an overlay input over the Text cell and submit updates only the
  captured target row.
- Pressing `t` shows an overlay input over the Time cell and submit updates the timestamp.
- Invalid timestamp submit keeps the overlay open, preserves the typed value, warns, and
  does not autosave.
- Escape cancels in-row edit and restores table focus.
- Down navigation does not change selected row while the overlay input is focused.
- Overlay remains aligned after vertical scroll.
- Overlay remains aligned in narrow terminal layouts and after terminal resize.
- Offscreen target rows are scrolled into view before overlay placement.
- Footer remains below editor body in small terminal sizes after removing `#edit-panel`.

Add multi-selection tests:

- `shift+down` selects a contiguous range from anchor to endpoint.
- `shift+up` shrinks or expands the range correctly.
- Normal navigation clears range selection.
- Row labels show cursor and selected non-cursor markers.
- Delete affects only the cursor row and clears selection.

Add clipboard and paste tests:

- Copying selected rows writes lyric text only, joined by newlines, via the Textual app
  clipboard.
- Copying without a range copies the current row only.
- Pasting newline-separated lyrics inserts multiple draft rows after the current row or
  range.
- Pasted rows always have timestamp `0.0`.
- Timestamp-looking pasted text, such as `[00:12.34]Text`, is inserted literally as lyric
  text with timestamp `0.0`.
- Blank pasted lines are ignored and reported.
- Terminal paste and `ctrl+v` do not double-insert the same paste payload.

Update `tests/admin/services/test_lrc_editor.py` only if current coverage is incomplete:

- `insert_lines_after` inserts multiple zero-timestamp rows.
- Undo removes all inserted paste rows.
- Redo restores all inserted paste rows.
- Original timestamp tracking stays aligned after multi-row insert.
- Original timestamp tracking stays aligned when inserting rows while padding is active
  and then adjusting padding.

Run focused tests:

```bash
PYTHONPATH=src uv run --python 3.11 --extra admin --extra test pytest \
  tests/admin/services/test_lrc_editor.py \
  tests/admin/services/test_lrc_editor_screen.py -v
```

Then run all admin tests:

```bash
PYTHONPATH=src uv run --python 3.11 --extra admin --extra test pytest tests/admin/ -v
```

## Assumptions

- This change is limited to the admin LRC editor package.
- In-row editing means a focused overlay input aligned to the selected DataTable cell,
  because Textual `DataTable` does not natively host editable cell widgets.
- Textual's installed `App` clipboard APIs are available and should be preferred over
  adding `pyperclip`.
- Copied plain text intentionally excludes timestamps for easier external lyric editing.
- Paste is intentionally text-only to avoid accidental timestamp corruption.
- Blank line preservation is out of scope for this version; dropped blanks are reported.
- Multi-row selection is contiguous only.
- Multi-row selection is for copy/paste placement only; multi-row delete remains out of
  scope.
- Large-paste confirmation is out of scope for v3 unless a later product decision asks
  for it.
