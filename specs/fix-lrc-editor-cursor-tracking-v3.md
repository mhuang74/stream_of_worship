# Fix: LRC Editor Cursor/Highlight Tracking Bugs (v3)

## Overview

Two remaining bugs in the LRC editor (`src/stream_of_worship/admin/editor/`) caused by the two independent selection trackers (`state.selected_index` and `DataTable.cursor_row`) falling out of sync:

1. **Bug 1**: `_refresh_table()` calls `table.clear()` which resets cursor to (0,0), firing a `RowHighlighted(0)` event that can overwrite `state.selected_index`. This race manifests as visual flicker and stale selection during preview (`_on_playback_position` ×3 call sites at 200ms intervals).
2. **Bug 2**: Resume from autosave always starts at row 0 — `selected_index` is not persisted in autosave and is forcibly reset on mount.

Bug 3 (last-row navigation/paste) was partially fixed by a prior agent (see "Already Fixed" below). The remaining scroll issue is addressed by adding `scroll=True` to `move_cursor` calls.

---

## Differences from v2 Spec

The v2 spec was written before three fix commits (`b7090b3`, `0fe15a2`, `70b2850`) were applied by another agent. Key changes:

1. **`_programmatic_highlight_rows` was added then removed** — the other agent tried this approach in `b7090b3` but reverted it in `0fe15a2` in favor of `_update_selection_marker(old_index)`.
2. **`_update_selection_marker(old_index)` now exists** — O(1), updates exactly 2 rows. Already used by `on_data_table_row_highlighted` and `_sync_selection_from_table_cursor`.
3. **`_update_table_row(index)` now exists** — updates a single row in-place via `update_cell_at`.
4. **`_move_table_cursor_to_selection(old_index)` now exists** — moves cursor + updates selection marker.
5. **`_sync_selection_from_table_cursor()` now exists** — workaround that syncs `state.selected_index` from `DataTable.cursor_row` before preview actions.
6. **`LyricLineTable` subclass now exists** — overrides `action_cursor_up/down/page_up/page_down` to guard during preview. Screen-level `up`/`down` bindings removed.
7. **4 action handlers already migrated** — `action_select_prev`, `action_select_next`, `action_stamp_and_advance`, `_sync_selection_from_table_cursor` now use O(1) updates instead of `_refresh_table()`.
8. **`_refresh_table()` still calls `clear()`** — the root cause of Bug 1 is NOT fixed. 13 call sites remain.

---

## Already Fixed (by prior agent)

These issues from the v2 spec are now resolved and are **out of scope**:

| Issue | Fix | Commit |
|-------|-----|--------|
| Screen `up`/`down` bindings never fire (Textual binding resolution) | `LyricLineTable` subclass handles cursor movement directly; Screen bindings removed | `70b2850` |
| Preview guards on cursor navigation | `LyricLineTable.action_cursor_up/down/page_up/page_down` check `_guard_preview()` | `70b2850` |
| `on_data_table_row_highlighted` called `_refresh_table()` (O(n)) | Now calls `_update_selection_marker(old_index)` (O(1)) | `0fe15a2` |
| `_sync_selection_from_table_cursor` called `_refresh_table()` | Now calls `_update_selection_marker(old_index)` | `0fe15a2` |
| `action_select_prev/next` called `_refresh_table()` | Now use `_move_table_cursor_to_selection(old_index)` | `0fe15a2` |
| `action_stamp_and_advance` called `_refresh_table()` | Now uses `_update_table_row` + `_move_table_cursor_to_selection` | `0fe15a2` |
| DataTable not focused on mount | `focus()` call added in `on_mount` | `70b2850` |
| `action_insert_after` doesn't refocus table | `focus()` call added after `_refresh_table()` | `70b2850` |

---

## Bug 1: `_refresh_table()` → `clear()` → `RowHighlighted(0)` race

### Symptom

During continuous preview, the 200ms `_on_playback_position` callback calls `_refresh_table()` which calls `table.clear()`. This resets cursor to (0,0) and queues a `RowHighlighted(0)` event via `post_message`. Although `move_cursor(row=N)` is called immediately after, both events are processed asynchronously. The `RowHighlighted(0)` handler calls `state.select_line(0)`, briefly overwriting the user's selection. This causes visual flicker and potential race conditions with user cursor movements.

### Root Cause

`DataTable.clear()` explicitly sets `self.cursor_coordinate = Coordinate(0, 0)`. This triggers `watch_cursor_coordinate()` → `_highlight_row(0)` → posts a `RowHighlighted` event with `cursor_row=0`. This event is queued via `post_message` and processed asynchronously in the next message pump cycle, **after** the calling method returns.

### Fix: Replace `_refresh_table` with `_rebuild_table` using in-place updates

Instead of `clear()` + re-add all rows, update rows in-place. This eliminates the cursor-reset → `RowHighlighted(0)` race condition entirely.

**`screen.py` — rename `_refresh_table` to `_rebuild_table` with in-place update logic**:

```python
def _rebuild_table(self) -> None:
    table = self.query_one("#line-table", DataTable)
    current_count = table.row_count
    target_count = self.state.line_count

    for i in range(min(current_count, target_count)):
        line = self.state.timed_lines[i]
        ts = format_centiseconds(line.time_seconds)
        status = self._row_status(i)
        row_label = self._row_label(i)
        for column, value in enumerate((row_label, ts, line.text, status)):
            table.update_cell_at(Coordinate(i, column), value, update_width=True)

    if target_count > current_count:
        for i in range(current_count, target_count):
            line = self.state.timed_lines[i]
            ts = format_centiseconds(line.time_seconds)
            status = self._row_status(i)
            row_label = self._row_label(i)
            table.add_row(row_label, ts, line.text, status, key=str(i))
    elif current_count > target_count:
        for i in range(current_count - 1, target_count - 1, -1):
            table.remove_row(str(i))

    if 0 <= self.state.selected_index < target_count:
        table.move_cursor(row=self.state.selected_index, scroll=True)
```

**Key properties**:
- No `table.clear()` → no cursor reset → no `RowHighlighted(0)` event → no race condition
- Existing rows updated in-place via `update_cell_at` (same as existing `_update_table_row`)
- Rows added/removed only when line count changes
- `scroll=True` on `move_cursor` ensures selected row scrolls into view
- Reuses existing `_row_label(i)` and `_row_status(i)` helpers

**Migration**: Replace all 13 remaining call sites of `_refresh_table()` with `_rebuild_table()`. Simple rename — no behavioral changes from the caller's perspective.

### Also add `scroll=True` to `_move_table_cursor_to_selection`

```python
def _move_table_cursor_to_selection(self, old_index: int | None = None) -> None:
    try:
        table = self.query_one("#line-table", DataTable)
    except NoMatches:
        return
    if 0 <= self.state.selected_index < self.state.line_count:
        table.move_cursor(row=self.state.selected_index, scroll=True)
    self._update_selection_marker(old_index)
```

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

## Bug 3 (residual): `scroll=True` on `move_cursor`

### What was already fixed

The prior agent's `LyricLineTable` subclass and removal of Screen-level `up`/`down` bindings fixed the core navigation issue — DataTable's built-in cursor movement now works correctly.

### What remains

`move_cursor` calls don't use `scroll=True`, so the selected row may not scroll into view when moved programmatically (e.g., after `_rebuild_table` or `_move_table_cursor_to_selection`).

### Fix

Add `scroll=True` to both `move_cursor` call sites (already included in `_rebuild_table` and `_move_table_cursor_to_selection` above):

| Line | Method | Change |
|------|--------|--------|
| 346 | `_rebuild_table` (was `_refresh_table`) | `table.move_cursor(row=..., scroll=True)` |
| 401 | `_move_table_cursor_to_selection` | `table.move_cursor(row=..., scroll=True)` |

---

## Implementation Order

1. **Bug 1** — Rename `_refresh_table` to `_rebuild_table` with in-place update logic; update all 13 call sites; add `scroll=True` to both `move_cursor` calls
2. **Bug 2** — Add `selected_index` to `AutosaveState` (field, `to_dict`, `from_dict`); pass it in `_do_autosave`; use it in `audio.py`; remove `select_line(0)` from `app.py`

---

## Files to Modify

| File | Changes |
|------|---------|
| `screen.py` | Rename `_refresh_table` → `_rebuild_table` with in-place update logic (no `clear()`); update 13 call sites; add `scroll=True` to `move_cursor` in `_rebuild_table` and `_move_table_cursor_to_selection`; pass `selected_index` in `_do_autosave()` |
| `autosave.py` | Add `selected_index` to `AutosaveState` (field, `to_dict()`, `from_dict()`) |
| `audio.py` | Use `autosave_state.selected_index` instead of `0` at line 3435 |
| `app.py` | Remove `self.editor_state.select_line(0)` from `on_mount` |

---

## Detailed Call Site Migration: `_refresh_table()` → `_rebuild_table()`

13 remaining call sites in `screen.py` (4 already migrated to O(1) updates by prior agent):

| Line | Method | Context |
|------|--------|---------|
| 305 | `on_mount` | Initial table population |
| 472 | `_on_playback_position` | Single preview, target hit |
| 477 | `_on_playback_position` | Single preview, prev_index hit |
| 484 | `_on_playback_position` | Continuous preview |
| 614 | `action_show_earlier` | Padding adjustment |
| 632 | `action_show_later` | Padding adjustment |
| 668 | `action_preview_single` | Select prev_idx before playback |
| 731 | `on_input_submitted` | After text/timestamp edit |
| 754 | `action_insert_after` | Insert blank line |
| 783 | `action_insert_canonical` | Insert canonical lyrics |
| 805 | `action_paste_after` | Paste clipboard |
| 816 | `action_delete_line` | Delete current line |
| 824 | `action_undo` | Undo last action |
| 833 | `action_redo` | Redo last action |

All call sites are simple method renames — no behavioral changes needed from the caller's perspective.

**Already migrated** (no changes needed, use O(1) updates instead of `_refresh_table`):

| Method | Current approach |
|--------|-----------------|
| `on_data_table_row_highlighted` | `_update_selection_marker(old_index)` |
| `_sync_selection_from_table_cursor` | `_update_selection_marker(old_index)` |
| `action_select_prev` | `_move_table_cursor_to_selection(old_index)` |
| `action_select_next` | `_move_table_cursor_to_selection(old_index)` |
| `action_stamp_and_advance` | `_update_table_row` + `_move_table_cursor_to_selection` |

---

## Testing

### Existing Tests

```bash
PYTHONPATH=src uv run --python 3.11 --extra app --extra test pytest tests/admin/services/test_lrc_editor.py -v
PYTHONPATH=src uv run --python 3.11 --extra app --extra test pytest tests/admin/services/test_lrc_editor_screen.py -v
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
2. Start continuous preview, verify no visual flicker or stale selection
3. Quit, resume from autosave, verify `>` is on last-edited row, start preview from there
4. Navigate to last row, verify it scrolls into view
5. Navigate to last row, paste — should append new row after last row

---

## Edge Cases

1. **Empty table**: `_rebuild_table` with `target_count=0` — the `min(current_count, target_count)` loop is a no-op, and the removal loop removes all rows. Safe.
2. **Table growing (insert/paste)**: New rows added via `add_row` with correct keys. Existing rows updated in-place. Safe.
3. **Table shrinking (delete)**: Rows removed from bottom up via `remove_row`. Existing rows updated in-place. Safe.
4. **`selected_index` out of range in autosave**: If corrupted autosave has `selected_index` beyond line count, `EditorState.select_line()` clamps it, and `_rebuild_table()` → `move_cursor()` handles it via the bounds check.
5. **Backward compatibility**: Old autosave files without `selected_index` default to `0` via `data.get("selected_index", 0)`, matching current behavior.
6. **`update_cell_at` vs `update_cell`**: `_rebuild_table` uses `update_cell_at(Coordinate(i, column), value)` which updates by coordinate — works correctly as long as row indices match, which they do since we never call `clear()`.
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
