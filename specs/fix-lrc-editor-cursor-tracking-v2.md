# Fix: LRC Editor Cursor/Highlight Tracking Bugs (v2)

## Overview

Three interrelated bugs in the LRC editor (`src/stream_of_worship/admin/editor/`) caused by the two independent selection trackers (`state.selected_index` and `DataTable.cursor_row`) falling out of sync:

1. **Bug 1**: Preview starts from stale `selected_index` after cursor moved — root cause is `DataTable.clear()` resetting cursor to `(0,0)`, which fires `RowHighlighted` for row 0, overwriting the user's actual selection.
2. **Bug 2**: Resume from autosave always starts at row 0 — `selected_index` is not persisted in autosave and is forcibly reset on mount.
3. **Bug 3**: Cannot navigate to/paste at last row — consequence of Bug 1; `_refresh_table()` calling `clear()` on every data mutation destroys scroll position and can leave `selected_index` stale.

Bug 1 is the root cause of Bugs 1 and 3. Bug 2 is independent.

---

## Differences from v1 Spec

The v1 spec (`fix-lrc-editor-cursor-tracking.md`) had several issues discovered during code review:

1. **Referenced `_programmatic_highlight_rows` which does not exist** in the current codebase. The current code uses `_update_selection_marker(old_index)` (O(1), updates 2 rows) instead.
2. **Claimed `on_data_table_row_highlighted` calls `_refresh_table()`** — incorrect. The current code at `screen.py:308-317` already calls `_update_selection_marker(old_index)`, the efficient O(1) path.
3. **`_refreshing` boolean guard is ineffective** — Textual's `post_message` queues `RowHighlighted` events for the next message pump cycle. By the time the event is processed, `_refreshing` is already `False` (cleared in `finally`). The guard is a no-op for the exact scenario it's meant to prevent.
4. **Proposed `_update_selection_marker()` (no params) is an O(n) regression** — iterates all rows instead of the current O(1) approach that updates only old+new rows.

This v2 plan addresses these issues with a revised approach.

---

## Architecture Context

The editor has **two independent selection trackers** that must stay in sync:

1. **`state.selected_index`** — the "logical" selection in `EditorState`, used by all action handlers (preview, copy, paste, delete, etc.). Displayed as `>` prefix in the `#` column of the DataTable.
2. **`DataTable.cursor_row`** — the Textual DataTable's built-in cursor position, controlled by up/down/page keys via DataTable's own bindings.

### Critical Textual Binding Resolution

When the DataTable has focus, its built-in `up`/`down` bindings fire **before** the Screen's `up`/`down` bindings. Non-priority bindings resolve from focused widget upward. This means `action_select_prev`/`action_select_next` are **never called** via arrow keys — only `on_data_table_row_highlighted` syncs the two trackers.

### Critical Textual `clear()` Behavior

`DataTable.clear()` explicitly sets `self.cursor_coordinate = Coordinate(0, 0)`. This triggers `watch_cursor_coordinate()` → `_highlight_row(0)` → posts a `RowHighlighted` event with `cursor_row=0`. This event is queued via `post_message` and processed asynchronously in the next message pump cycle, **after** the calling method returns. It can overwrite `state.selected_index` with `0`.

### Current Code State (verified)

- `on_data_table_row_highlighted` (`screen.py:308-317`): Already calls `_update_selection_marker(old_index)` — does NOT call `_refresh_table()`. This is the efficient O(1) path.
- `_update_selection_marker(old_index)` (`screen.py:350-353`): Updates exactly 2 rows (old and new). O(1).
- `_refresh_table()` (`screen.py:291-306`): Calls `table.clear()` then re-adds all rows. Called from 14 locations (on_mount, _on_playback_position ×3, action handlers ×9).
- `_programmatic_highlight_rows`: Does NOT exist in the codebase.

---

## Bug 1: Preview starts from stale `selected_index` after cursor moved

### Symptom

After exiting preview (highlighted row is row 5 with `>` mark), user moves cursor back to row 1 and hits Shift-P for continuous preview — it starts from row 5 instead of row 1.

### Root Cause

`_refresh_table()` calls `table.clear()`, which resets cursor to (0,0) and queues a `RowHighlighted(0)` event. Then `move_cursor(row=N)` queues `RowHighlighted(N)`. Both events are processed asynchronously after `_refresh_table()` returns. The `RowHighlighted(0)` handler calls `state.select_line(0)`, briefly overwriting the user's selection. Although `RowHighlighted(N)` restores it, the intermediate `select_line(0)` call triggers `_update_selection_marker` and `_update_displays` with wrong state, causing visual flicker and potential race conditions.

This primarily manifests when `_refresh_table()` is called from `_on_playback_position` (lines 432, 437, 444) during preview — the 200ms callback interval means stale `RowHighlighted(0)` events can interleave with user cursor movements.

### Fix — Two Parts

#### Part A: Replace `_refresh_table()` with `_rebuild_table()` that avoids `clear()`

Instead of `clear()` + re-add all rows, update rows in-place. This eliminates the cursor-reset → `RowHighlighted(0)` race condition entirely.

**`screen.py` — rename `_refresh_table` to `_rebuild_table` with in-place updates**:

```python
def _rebuild_table(self) -> None:
    table = self.query_one("#line-table", DataTable)
    current_count = table.row_count
    target_count = self.state.line_count

    for i in range(min(current_count, target_count)):
        line = self.state.timed_lines[i]
        ts = format_centiseconds(line.time_seconds)
        status = ""
        if line.time_seconds == 0.0 and line.text.strip():
            status = "[dim]draft[/dim]"
        if i > 0 and line.time_seconds < self.state.timed_lines[i - 1].time_seconds:
            status = "[red]!non-mono[/red]"
        row_label = f">{i + 1}" if i == self.state.selected_index else str(i + 1)
        for column, value in enumerate((row_label, ts, line.text, status)):
            table.update_cell_at(Coordinate(i, column), value, update_width=True)

    if target_count > current_count:
        for i in range(current_count, target_count):
            line = self.state.timed_lines[i]
            ts = format_centiseconds(line.time_seconds)
            status = ""
            if line.time_seconds == 0.0 and line.text.strip():
                status = "[dim]draft[/dim]"
            if i > 0 and line.time_seconds < self.state.timed_lines[i - 1].time_seconds:
                status = "[red]!non-mono[/red]"
            row_label = f">{i + 1}" if i == self.state.selected_index else str(i + 1)
            table.add_row(row_label, ts, line.text, status, key=str(i))
    elif current_count > target_count:
        for i in range(current_count - 1, target_count - 1, -1):
            table.remove_row(str(i))

    if 0 <= self.state.selected_index < target_count:
        table.move_cursor(row=self.state.selected_index, scroll=True)
```

**Key differences from v1 approach**:
- No `table.clear()` → no cursor reset → no `RowHighlighted(0)` event → no race condition
- Existing rows are updated in-place via `update_cell_at` (already used by `_update_table_row`)
- Rows are added/removed only when line count changes
- `scroll=True` added to `move_cursor` (fixes Bug 3 scroll issue)
- No `_refreshing` guard flag needed — the root cause is eliminated

**Migration**: Replace all 14 call sites of `_refresh_table()` with `_rebuild_table()`. The method signature and behavior are identical from the caller's perspective.

#### Part B: Keep existing `_update_selection_marker(old_index)` for selection-only changes

The current `_update_selection_marker(old_index)` at `screen.py:350-353` is already optimal for selection-only changes — O(1), updates exactly 2 rows. No changes needed.

The current `on_data_table_row_highlighted` already calls `_update_selection_marker(old_index)` instead of `_refresh_table()`. This is correct and should be preserved.

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

The `LRCEditorScreen.on_mount` already calls `_rebuild_table()` which calls `move_cursor(row=self.state.selected_index, scroll=True)`, so the cursor will be positioned correctly from the restored state.

---

## Bug 3: Cannot navigate to/paste at last row

### Symptom

Cannot move cursor to the last row — it's frequently off-screen. When pasting, it replaces the last row instead of appending a new row after it.

### Root Cause — Two Sub-issues

#### Sub-issue A: Last row off-screen

`_refresh_table()` calls `table.clear()` which resets scroll position. Then `table.move_cursor(row=...)` should scroll the cursor into view, but after `clear()` + re-adding all rows, the DataTable's virtual height may not be settled yet.

**Fix:** The `_rebuild_table()` approach from Bug 1 Part A directly fixes this — no `clear()` means no scroll reset. The DataTable's built-in cursor movement (which handles its own scrolling) works correctly without interference. The `scroll=True` parameter on `move_cursor` provides an additional guarantee.

#### Sub-issue B: Paste replaces last row instead of appending

`action_paste_after()` calls `self.state.insert_after(self.state.selected_index, ...)`. If Bug 1 causes `selected_index` to be stale (e.g., still pointing to the second-to-last row due to the refresh race), the paste inserts at the wrong position. Also, if the user can't navigate to the last row at all (off-screen), they can't select it to paste after it.

**Fix:** Fixing Bug 1 (the `_rebuild_table` approach that eliminates `clear()`) will fix the stale `selected_index` that causes paste-at-wrong-position. Fixing Sub-issue A (scroll) will let the user actually reach the last row to paste after it.

---

## Implementation Order

1. **Bug 1 Part A** (`_rebuild_table` replacing `_refresh_table`) — this is the root cause fix for Bugs 1 and 3
2. **Bug 2** (autosave `selected_index`) — independent, straightforward
3. **Bug 3 verification** — test that Bug 1 fix resolves the paste/scroll issues; add explicit `scroll=True` if needed (already included in `_rebuild_table`)

---

## Files to Modify

| File | Changes |
|------|---------|
| `screen.py` | Rename `_refresh_table` to `_rebuild_table` with in-place update logic (no `clear()`); update all 14 call sites; add `scroll=True` to `move_cursor`; pass `selected_index` in `_do_autosave()` |
| `autosave.py` | Add `selected_index` to `AutosaveState` (field, `to_dict()`, `from_dict()`) |
| `audio.py` | Use `autosave_state.selected_index` instead of `0` at line 3435 |
| `app.py` | Remove `self.editor_state.select_line(0)` from `on_mount` |

---

## Detailed Call Site Migration: `_refresh_table()` → `_rebuild_table()`

All 14 call sites in `screen.py`:

| Line | Method | Context |
|------|--------|---------|
| 266 | `on_mount` | Initial table population |
| 432 | `_on_playback_position` | Single preview, target hit |
| 437 | `_on_playback_position` | Single preview, prev_index hit |
| 444 | `_on_playback_position` | Continuous preview |
| 574 | `action_show_earlier` | Padding adjustment |
| 592 | `action_show_later` | Padding adjustment |
| 628 | `action_preview_single` | Select prev_idx before playback |
| 691 | `on_input_submitted` | After text/timestamp edit |
| 714 | `action_insert_after` | Insert blank line |
| 743 | `action_insert_canonical` | Insert canonical lyrics |
| 765 | `action_paste_after` | Paste clipboard |
| 776 | `action_delete_line` | Delete current line |
| 784 | `action_undo` | Undo last action |
| 793 | `action_redo` | Redo last action |

All call sites are simple method renames — no behavioral changes needed from the caller's perspective.

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
6. Start continuous preview, verify no visual flicker or stale selection

---

## Edge Cases

1. **Empty table**: `_rebuild_table` with `target_count=0` — the `min(current_count, target_count)` loop is a no-op, and the removal loop removes all rows. Safe.
2. **Table growing (insert/paste)**: New rows are added via `add_row` with correct keys. Existing rows are updated in-place. Safe.
3. **Table shrinking (delete)**: Rows are removed from bottom up via `remove_row`. Existing rows are updated in-place. Safe.
4. **`selected_index` out of range in autosave**: If a corrupted autosave has `selected_index` beyond the line count, `EditorState.select_line()` clamps it, and `_rebuild_table()` → `move_cursor()` will handle it via the bounds check.
5. **Backward compatibility**: Old autosave files without `selected_index` will default to `0` via `data.get("selected_index", 0)`, matching current behavior.
6. **`update_cell_at` vs `update_cell`**: `_rebuild_table` uses `update_cell_at(Coordinate(i, column), value)` which updates by coordinate — this works correctly as long as the row indices match, which they do since we never call `clear()`.
7. **Row keys**: `_rebuild_table` uses `key=str(i)` for new rows (same as current `_refresh_table`). Since we never `clear()`, existing rows retain their keys. New rows get the correct key. Removed rows are removed by key. No key collision risk.
8. **`_on_playback_position` during `_rebuild_table`**: The 200ms callback may fire during table updates. Since `_rebuild_table` doesn't call `clear()`, the table is always in a consistent state — partial updates are safe because each `update_cell_at` is atomic.

---

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| `update_cell_at` on non-existent coordinate | Low | Crash | Guard with `0 <= i < table.row_count` before update |
| Row key mismatch after insert/delete | Low | Wrong row updated | Use `str(i)` keys consistently; verify with manual test |
| Performance regression from in-place updates vs clear+re-add | Low | Slower updates | In-place is actually faster (no DOM rebuild); benchmark if concerned |
| `remove_row` by key not found | Low | Exception | Guard removal loop with row existence check |
| Autosave `selected_index` beyond line count | Low | Wrong row selected | `select_line()` clamps; safe |
