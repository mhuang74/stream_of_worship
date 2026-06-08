# Fix: LRC Editor Cursor/Highlight Tracking Bugs

## Overview

Three interrelated bugs in the LRC editor (`src/stream_of_worship/admin/editor/`) caused by the two independent selection trackers (`state.selected_index` and `DataTable.cursor_row`) falling out of sync:

1. **Bug 1**: Preview starts from stale `selected_index` after cursor moved — root cause is `DataTable.clear()` resetting cursor to `(0,0)`, which fires `RowHighlighted` for row 0, overwriting the user's actual selection.
2. **Bug 2**: Resume from autosave always starts at row 0 — `selected_index` is not persisted in autosave and is forcibly reset on mount.
3. **Bug 3**: Cannot navigate to/paste at last row — consequence of Bug 1; constant `clear()`+rebuild on every cursor move destroys scroll position and can leave `selected_index` stale.

Bug 1 is the root cause of Bugs 1 and 3. Bug 2 is independent.

---

## Architecture Context

The editor has **two independent selection trackers** that must stay in sync:

1. **`state.selected_index`** — the "logical" selection in `EditorState`, used by all action handlers (preview, copy, paste, delete, etc.). Displayed as `>` prefix in the `#` column of the DataTable.
2. **`DataTable.cursor_row`** — the Textual DataTable's built-in cursor position, controlled by up/down/page keys via DataTable's own bindings.

### Critical Textual Binding Resolution

When the DataTable has focus, its built-in `up`/`down` bindings fire **before** the Screen's `up`/`down` bindings. Non-priority bindings resolve from focused widget upward. This means `action_select_prev`/`action_select_next` are **never called** via arrow keys — only `on_data_table_row_highlighted` syncs the two trackers.

### Critical Textual `clear()` Behavior

`DataTable.clear()` explicitly sets `self.cursor_coordinate = Coordinate(0, 0)`. This triggers `watch_cursor_coordinate()` → `_highlight_row(0)` → posts a `RowHighlighted` event with `cursor_row=0`. This event is processed asynchronously after `_refresh_table()` returns, and can **overwrite** `state.selected_index` with `0`.

---

## Bug 1: Preview starts from stale `selected_index` after cursor moved

### Symptom

After exiting preview (highlighted row is row 5 with `>` mark), user moves cursor back to row 1 and hits Shift-P for continuous preview — it starts from row 5 instead of row 1.

### Root Cause

`on_data_table_row_highlighted` fires and calls `_refresh_table()`, which calls `table.clear()` (resetting cursor to 0,0), then re-adds all rows, then calls `table.move_cursor(row=self.state.selected_index)`. The `clear()` resets cursor to 0, which fires a `RowHighlighted` event for row 0. This event handler then sets `state.selected_index = 0` before the `move_cursor` can restore it. The cursor briefly visits row 0 during every refresh, and the `RowHighlighted` event from that visit can overwrite the user's actual selection.

### Fix — Two Parts

#### Part A: Add a `_refreshing` guard flag

Prevent `on_data_table_row_highlighted` from updating `state.selected_index` during `_refresh_table()`:

**`screen.py` — `__init__`**: Add field:

```python
self._refreshing: bool = False
```

**`screen.py` — `_refresh_table()`**: Wrap with guard:

```python
def _refresh_table(self) -> None:
    self._refreshing = True
    try:
        table = self.query_one("#line-table", DataTable)
        table.clear()
        for i, line in enumerate(self.state.timed_lines):
            ts = format_centiseconds(line.time_seconds)
            status = ""
            if line.time_seconds == 0.0 and line.text.strip():
                status = "[dim]draft[/dim]"
            if i > 0 and line.time_seconds < self.state.timed_lines[i - 1].time_seconds:
                status = "[red]!non-mono[/red]"
            is_selected = i == self.state.selected_index
            row_label = f">{i + 1}" if is_selected else str(i + 1)
            table.add_row(row_label, ts, line.text, status, key=str(i))
        if 0 <= self.state.selected_index < self.state.line_count:
            table.move_cursor(row=self.state.selected_index)
    finally:
        self._refreshing = False
```

**`screen.py` — `on_data_table_row_highlighted()`**: Guard with early return:

```python
def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
    if event.data_table.id != "line-table":
        return
    if self._refreshing:
        return
    if event.cursor_row == self.state.selected_index:
        self._update_displays()
        return
    self.state.select_line(event.cursor_row)
    self._update_selection_marker()
    self._update_displays()
```

This replaces the existing `_programmatic_highlight_rows` workaround, which can be removed entirely.

#### Part B: Add `_update_selection_marker()` for selection-only changes

When only the selection moved (no lines added/deleted/timestamps changed), avoid `table.clear()` entirely. This eliminates the cursor-reset problem for the most common case (navigating with up/down keys):

**`screen.py` — new method**:

```python
def _update_selection_marker(self) -> None:
    """Update only the # column markers without full table rebuild.

    Use this when only the selection changed (no data mutations).
    Avoids the clear() -> cursor reset -> RowHighlighted race condition.
    """
    table = self.query_one("#line-table", DataTable)
    self._refreshing = True
    try:
        for i in range(self.state.line_count):
            marker = f">{i + 1}" if i == self.state.selected_index else str(i + 1)
            table.update_cell(str(i), "#", marker)
    finally:
        self._refreshing = False
    table.move_cursor(row=self.state.selected_index)
```

**`screen.py` — `on_data_table_row_highlighted()`**: Call `_update_selection_marker()` instead of `_refresh_table()` (as shown in Part A above).

**`screen.py` — `_sync_selection_from_table_cursor()`**: Also use `_update_selection_marker()`:

```python
def _sync_selection_from_table_cursor(self) -> None:
    try:
        table = self.query_one("#line-table", DataTable)
    except NoMatches:
        return

    cursor_row = table.cursor_row
    if cursor_row is None or not 0 <= cursor_row < self.state.line_count:
        return
    if cursor_row == self.state.selected_index:
        return

    self.state.select_line(cursor_row)
    self._update_selection_marker()
    self._update_displays()
```

Keep `_refresh_table()` for data mutations (insert, delete, timestamp changes, etc.) where the full rebuild is necessary.

---

## Bug 2: Resume from autosave starts preview at wrong row

### Symptom

After quitting and restarting with Resume, the `>` mark is on row 5, user presses Shift-P and preview starts from row 1 instead of row 5 (or vice versa — the `>` and actual preview start are inconsistent).

### Root Cause

In `audio.py:3435`, when resuming from autosave, `selected_index=0` is hardcoded:

```python
editor_state = EditorState(
    ...
    selected_index=0,  # <-- always 0, ignores where user was
    ...
)
```

Then in `app.py:33`, `on_mount` calls `self.editor_state.select_line(0)`, reinforcing the reset.

### Fix — Two Parts

#### Part A: Save `selected_index` in autosave

**`autosave.py` — `AutosaveState`**: Add field:

```python
@dataclass
class AutosaveState:
    ...
    selected_index: int = 0
```

**`autosave.py` — `to_dict()`**: Include:

```python
def to_dict(self) -> dict:
    return {
        ...
        "selected_index": self.selected_index,
    }
```

**`autosave.py` — `from_dict()`**: Restore:

```python
@classmethod
def from_dict(cls, data: dict) -> "AutosaveState":
    return cls(
        ...
        selected_index=data.get("selected_index", 0),
    )
```

**`screen.py` — `_do_autosave()`**: Pass `selected_index`:

```python
autosave_state = AutosaveState(
    ...
    selected_index=self.state.selected_index,
)
```

**`audio.py:3435`**: Use autosave value:

```python
editor_state = EditorState(
    ...
    selected_index=autosave_state.selected_index,
    ...
)
```

#### Part B: Remove the forced `select_line(0)` in `app.py`

**`app.py` — `on_mount()`**: Remove the `select_line(0)` call:

```python
def on_mount(self) -> None:
    self.push_screen(LRCEditorScreen(...))
```

The `LRCEditorScreen.on_mount` already calls `_refresh_table()` which calls `move_cursor(row=self.state.selected_index)`, so the cursor will be positioned correctly from the restored state.

---

## Bug 3: Cannot navigate to/paste at last row

### Symptom

Cannot move cursor to the last row — it's frequently off-screen. When pasting, it replaces the last row instead of appending a new row after it.

### Root Cause — Two Sub-issues

#### Sub-issue A: Last row off-screen

`_refresh_table()` calls `table.clear()` which resets scroll position. Then `table.move_cursor(row=..., scroll=True)` should scroll the cursor into view, but after `clear()` + re-adding all rows, the DataTable's virtual height may not be settled yet. Additionally, `on_data_table_row_highlighted` calling `_refresh_table()` on every cursor move means the table is constantly being rebuilt, interfering with scroll position.

**Fix:** The `_update_selection_marker()` optimization from Bug 1 Part B directly helps here — when navigating with up/down, the table is no longer cleared and rebuilt on every move, so scroll position is preserved. The DataTable's built-in cursor movement (which handles its own scrolling) will work correctly without interference.

If scroll issues persist after Bug 1 fix, add explicit `scroll=True` to `move_cursor` calls in `_refresh_table()`:

```python
if 0 <= self.state.selected_index < self.state.line_count:
    table.move_cursor(row=self.state.selected_index, scroll=True)
```

#### Sub-issue B: Paste replaces last row instead of appending

`action_paste_after()` calls `self.state.insert_after(self.state.selected_index, ...)`. If Bug 1 causes `selected_index` to be stale (e.g., still pointing to the second-to-last row due to the refresh race), the paste inserts at the wrong position. Also, if the user can't navigate to the last row at all (off-screen), they can't select it to paste after it.

**Fix:** Fixing Bug 1 (the `_refreshing` guard + `_update_selection_marker`) will fix the stale `selected_index` that causes paste-at-wrong-position. Fixing Sub-issue A (scroll) will let the user actually reach the last row to paste after it.

---

## Implementation Order

1. **Bug 1 first** (the `_refreshing` guard + `_update_selection_marker`) — this is the root cause of Bugs 1 and 3
2. **Bug 2** (autosave `selected_index`) — independent, straightforward
3. **Bug 3 verification** — test that Bug 1 fix resolves the paste/scroll issues; add explicit `scroll=True` if needed

---

## Files to Modify

| File | Changes |
|------|---------|
| `screen.py` | Add `_refreshing` flag; guard `on_data_table_row_highlighted`; add `_update_selection_marker()`; use it in selection-change paths (`on_data_table_row_highlighted`, `_sync_selection_from_table_cursor`); remove `_programmatic_highlight_rows` workaround; pass `selected_index` in `_do_autosave()` |
| `autosave.py` | Add `selected_index` to `AutosaveState` (field, `to_dict()`, `from_dict()`) |
| `audio.py` | Use `autosave_state.selected_index` instead of `0` at line 3435 |
| `app.py` | Remove `self.editor_state.select_line(0)` from `on_mount` |

---

## Testing

### Existing Tests

Run existing unit tests to verify no regressions:

```bash
PYTHONPATH=src uv run --python 3.11 --extra app --extra test pytest tests/admin/services/test_lrc_editor.py -v
```

### New Test: `selected_index` round-trip in AutosaveState

Add to `TestAutosave` class in `tests/admin/services/test_lrc_editor.py`:

```python
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
```

Also verify the existing `test_to_dict_from_dict_round_trip` still passes (it uses default `selected_index=0`).

### Manual Tests

1. Navigate with up/down, verify `>` marker follows cursor immediately
2. Exit preview, move cursor, start continuous preview — should start from cursor position
3. Quit, resume from autosave, verify `>` is on last-edited row, start preview from there
4. Navigate to last row, paste — should append new row after last row
5. Navigate to last row, verify it scrolls into view

---

## Edge Cases

1. **`_refreshing` guard during `_update_selection_marker`**: The guard is set during `update_cell` calls to prevent the `move_cursor` at the end from triggering a spurious `RowHighlighted` that would recurse. The `finally` block ensures it's always cleared.
2. **Empty table**: `_update_selection_marker` iterates `range(self.state.line_count)` which is 0 for empty table — no-op, safe.
3. **`selected_index` out of range in autosave**: If a corrupted autosave has `selected_index` beyond the line count, `EditorState.select_line()` clamps it, and `_refresh_table()` → `move_cursor()` will handle it via the bounds check.
4. **Backward compatibility**: Old autosave files without `selected_index` will default to `0` via `data.get("selected_index", 0)`, matching current behavior.
5. **`_programmatic_highlight_rows` removal**: This workaround was tracking rows that `move_cursor` would highlight programmatically, to avoid treating them as user-initiated highlights. The `_refreshing` flag is a more robust replacement that covers all programmatic cursor changes during refresh, not just `move_cursor`.
