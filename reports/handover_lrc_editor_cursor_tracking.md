# Handover: LRC Editor Cursor/Highlight Tracking Bugs

**Date:** 2026-06-07
**File:** `src/stream_of_worship/admin/editor/screen.py` (primary), `state.py`, `autosave.py`, `app.py`

## Architecture Context

The editor has **two independent selection trackers** that must stay in sync:

1. **`state.selected_index`** — the "logical" selection in `EditorState`, used by all action handlers (preview, copy, paste, delete, etc.). Displayed as `>` prefix in the `#` column of the DataTable.
2. **`DataTable.cursor_row`** — the Textual DataTable's built-in cursor position, controlled by up/down/page keys via DataTable's own bindings.

### Critical Textual Binding Resolution Fact

When the DataTable has focus, its built-in `up`/`down` bindings fire **before** the Screen's `up`/`down` bindings. Non-priority bindings resolve from focused widget upward (confirmed by reading Textual 7.5.0 source: `_binding_chain` iterates from `focused.ancestors_with_self`, and `_check_bindings` iterates this chain for non-priority bindings).

This means `action_select_prev`/`action_select_next` are **never called** via arrow keys — only `on_data_table_row_highlighted` syncs the two trackers.

### Critical Textual `clear()` Behavior

`DataTable.clear()` explicitly sets `self.cursor_coordinate = Coordinate(0, 0)` (confirmed in Textual source). This triggers `watch_cursor_coordinate()`, which calls `_highlight_row(0)`, which posts a `RowHighlighted` event with `cursor_row=0`. This event is processed asynchronously after `_refresh_table()` returns, and can **overwrite** `state.selected_index` with `0`.

---

## Bug 1: Preview starts from stale `selected_index` after cursor moved

**Symptom:** After exiting preview (highlighted row is row 5 with `>` mark), user moves cursor back to row 1 and hits Shift-P for continuous preview — it starts from row 5 instead of row 1.

**Root cause:** `action_preview_continuous()` (line 585) calls `_sync_selection_from_table_cursor()` which reads `table.cursor_row` and updates `state.selected_index`. However, `_refresh_table()` (called at line 317 of `on_data_table_row_highlighted`) calls `table.clear()` which **resets `cursor_coordinate` to `(0, 0)`**. Then `table.move_cursor(row=self.state.selected_index)` moves it back. But the `clear()` → `move_cursor()` sequence triggers `on_data_table_row_highlighted` again, which can race or produce stale values.

More critically: when the user navigates with up/down keys, `on_data_table_row_highlighted` fires and calls `_refresh_table()`, which calls `table.clear()` (resetting cursor to 0,0), then re-adds all rows, then calls `table.move_cursor(row=self.state.selected_index)`. The `clear()` resets cursor to 0, which fires a `RowHighlighted` event for row 0. This event handler then sets `state.selected_index = 0` before the `move_cursor` can restore it. **The cursor briefly visits row 0 during every refresh, and the RowHighlighted event from that visit can overwrite the user's actual selection.**

**Fix — Two parts:**

### Part A: Add a `_refreshing` guard flag

Prevent `on_data_table_row_highlighted` from updating `state.selected_index` during `_refresh_table()`:

```python
# In __init__:
self._refreshing: bool = False

# Modified _refresh_table:
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

# Modified on_data_table_row_highlighted:
def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
    if event.data_table.id != "line-table":
        return
    if self._refreshing:
        return
    if event.cursor_row == self.state.selected_index:
        self._update_displays()
        return
    self.state.select_line(event.cursor_row)
    self._refresh_table()
    self._update_displays()
```

### Part B: Optimize selection-only changes to use `update_cell` instead of full rebuild

When only the selection moved (no lines added/deleted/timestamps changed), avoid `table.clear()` entirely. This eliminates the cursor-reset problem for the most common case:

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

Then in `on_data_table_row_highlighted`, call `_update_selection_marker()` instead of `_refresh_table()`:

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

Keep `_refresh_table()` for data mutations (insert, delete, timestamp changes, etc.) where the full rebuild is necessary.

---

## Bug 2: Resume from autosave starts preview at wrong row

**Symptom:** After quitting and restarting with Resume, the `>` mark is on row 5, user presses Shift-P and preview starts from row 1 instead of row 5 (or vice versa — the `>` and actual preview start are inconsistent).

**Root cause:** In `audio.py:3435`, when resuming from autosave, `selected_index=0` is hardcoded:

```python
editor_state = EditorState(
    ...
    selected_index=0,  # <-- always 0, ignores where user was
    ...
)
```

Then in `app.py:33`, `on_mount` calls `self.editor_state.select_line(0)`, reinforcing the reset.

**Fix — Two parts:**

### Part A: Save `selected_index` in autosave

1. **`autosave.py`**: Add `selected_index: int = 0` field to `AutosaveState`, include in `to_dict()` and `from_dict()`:

```python
@dataclass
class AutosaveState:
    ...
    selected_index: int = 0

    def to_dict(self) -> dict:
        return {
            ...
            "selected_index": self.selected_index,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AutosaveState":
        return cls(
            ...
            selected_index=data.get("selected_index", 0),
        )
```

2. **`screen.py`**: In `_do_autosave()`, pass `selected_index=self.state.selected_index` to `AutosaveState`

3. **`audio.py:3435`**: Use `selected_index=autosave_state.selected_index` instead of `selected_index=0`

### Part B: Remove the forced `select_line(0)` in `app.py`

The `on_mount` should not override the restored selection:

```python
# app.py - remove the select_line(0) call
def on_mount(self) -> None:
    # Don't force select_line(0) — respect restored selected_index
    self.push_screen(LRCEditorScreen(...))
```

The `LRCEditorScreen.on_mount` already calls `_refresh_table()` which calls `move_cursor(row=self.state.selected_index)`, so the cursor will be positioned correctly.

---

## Bug 3: Cannot navigate to/paste at last row

**Symptom:** Cannot move cursor to the last row — it's frequently off-screen. When pasting, it replaces the last row instead of appending a new row after it.

**Root cause — Two sub-issues:**

### Sub-issue A: Last row off-screen

`_refresh_table()` calls `table.clear()` which resets scroll position. Then `table.move_cursor(row=..., scroll=True)` should scroll the cursor into view, but after `clear()` + re-adding all rows, the DataTable's virtual height may not be settled yet. Textual's `move_cursor` source notes: "if we tried to scroll before the virtual size has been set, then it might fail". The `move_cursor` does handle this via `call_after_refresh`, but the `clear()` → add rows → `move_cursor` sequence may still have timing issues.

Additionally, `on_data_table_row_highlighted` calling `_refresh_table()` on every cursor move means the table is constantly being rebuilt, potentially interfering with scroll position.

**Fix:** The `_update_selection_marker()` optimization from Bug 1 Part B directly helps here — when navigating with up/down, the table is no longer cleared and rebuilt on every move, so scroll position is preserved. The DataTable's built-in cursor movement (which handles its own scrolling) will work correctly without interference.

If scroll issues persist after Bug 1 fix, add explicit scroll handling after `_refresh_table()`:

```python
def _refresh_table(self) -> None:
    self._refreshing = True
    try:
        table = self.query_one("#line-table", DataTable)
        table.clear()
        for i, line in enumerate(self.state.timed_lines):
            ...
            table.add_row(...)
        if 0 <= self.state.selected_index < self.state.line_count:
            table.move_cursor(row=self.state.selected_index, scroll=True)
    finally:
        self._refreshing = False
```

### Sub-issue B: Paste replaces last row instead of appending

`action_paste_after()` (line 706) calls `self.state.insert_after(self.state.selected_index, ...)`. If the user is on the last row and `state.selected_index` is correct, this should insert after it. But if Bug 1 causes `selected_index` to be stale (e.g., still pointing to the second-to-last row due to the refresh race), the paste inserts at the wrong position. Also, if the user can't navigate to the last row at all (off-screen), they can't select it to paste after it.

**Fix:** Fixing Bug 1 (the `_refreshing` guard + `_update_selection_marker`) will fix the stale `selected_index` that causes paste-at-wrong-position. Fixing Sub-issue A (scroll) will let the user actually reach the last row to paste after it.

---

## Implementation Order

1. **Bug 1 first** (the `_refreshing` guard + `_update_selection_marker`) — this is the root cause of Bugs 1 and 3
2. **Bug 2** (autosave `selected_index`) — independent, straightforward
3. **Bug 3 verification** — test that Bug 1 fix resolves the paste/scroll issues; add explicit scroll handling if needed

## Files to Modify

| File | Changes |
|------|---------|
| `screen.py` | Add `_refreshing` flag, guard `on_data_table_row_highlighted`, add `_update_selection_marker()`, use it in selection-change paths |
| `autosave.py` | Add `selected_index` to `AutosaveState` (field, `to_dict()`, `from_dict()`) |
| `screen.py` (`_do_autosave`) | Pass `selected_index` to `AutosaveState` |
| `audio.py` | Use `autosave_state.selected_index` instead of `0` |
| `app.py` | Remove `self.editor_state.select_line(0)` from `on_mount` |

## Testing

- Manual test: navigate with up/down, verify `>` marker follows cursor immediately
- Manual test: exit preview, move cursor, start continuous preview — should start from cursor position
- Manual test: quit, resume from autosave, verify `>` is on last-edited row, start preview from there
- Manual test: navigate to last row, paste — should append new row after last row
- Existing unit tests in `tests/admin/services/test_lrc_editor.py` should still pass
