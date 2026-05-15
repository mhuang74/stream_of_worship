# Fix: Songset Editor Move Up/Down (',' and '.') Broken

## Context

In the Songset Editor TUI, pressing `,` to move a song up always reports "Already at top", and pressing `.` to move down appears to do nothing. This is caused by an async race condition between the move actions and `_load_items()`.

## Root Cause

After a successful reorder, both `action_move_up` (line 581) and `action_move_down` (line 608) call `self._load_items()` which spawns an **async worker thread** (line 213). The `table.move_cursor()` calls at lines 582 and 609 execute immediately — before the worker finishes. When the worker completes, `_update_items_table` calls `table.clear()` (line 243), which **resets the cursor to row 0**, discarding the cursor position.

**Result:**
- After any move, cursor snaps to row 0 (first song)
- Next "move up" (`comma`): cursor is at row 0 → `current_index == 0` → "Already at the top"
- Next "move down" (`period`): cursor is at row 0 → moves first item down, but cursor resets to 0 again → appears as if nothing happened

## Fix

**File:** `src/stream_of_worship/app/screens/songset_editor.py`

### 1. Add a `_pending_cursor_row` attribute to defer cursor positioning

Add `self._pending_cursor_row: Optional[int] = None` in the `__init__` or `compose`/`on_mount` setup (near line 77 where `self.items` is initialized).

### 2. In `action_move_up` and `action_move_down`, store the desired cursor position instead of calling `move_cursor` immediately

Replace the `_load_items()` + `move_cursor()` pattern:
```python
# Before (broken):
self._load_items()
table.move_cursor(row=current_index - 1)

# After (fixed):
self._pending_cursor_row = current_index - 1
self._load_items()
```

Same for move_down but with `current_index + 1`.

### 3. In `_update_items_table`, apply the pending cursor position after repopulating the table

At the end of `_update_items_table` (after the `table.add_row` loop), add:
```python
if self._pending_cursor_row is not None:
    table.move_cursor(row=self._pending_cursor_row)
    self._pending_cursor_row = None
```

## Files to Modify

- `src/stream_of_worship/app/screens/songset_editor.py` — 3 small changes (add field, update move_up, update move_down, update _update_items_table)

## Verification

```bash
# Run the TUI
uv run --extra app sow-app run

# Test steps:
# 1. Open a songset with 3+ songs
# 2. Arrow-key to song #2, press ',' → should move to position #1
# 3. Press '.' → should move back to position #2
# 4. Arrow to last song, press '.' → should say "Already at the bottom"
# 5. Arrow to first song, press ',' → should say "Already at the top"
```
