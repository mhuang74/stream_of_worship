# Enhance LRC Editor: In-Place Edit, Multi-Row Select & Paste

## Problem Statement

The current LRC editor has three UX issues:

1. **Input box obscured by footer**: The `#edit-panel` (containing the `Input` widget) sits at the bottom of `#editor-body`, directly under the `GroupedFooter` which is `dock: bottom` with `height: 3`. The docked footer overlaps the edit panel, making it invisible or partially hidden.

2. **No multi-row select/copy**: Only single-line copy (`Ctrl+C`) exists, using an internal `_clipboard` tuple. No range selection or system clipboard integration.

3. **No multi-row paste**: `Ctrl+V` pastes a single internal-clipboard line. Cannot paste multiple lines of lyrics from external sources.

## Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| In-place edit scope | Both Text & Timestamp columns | User requested; both fields benefit from inline editing |
| Multi-row selection | Shift+Up/Down range selection | Familiar spreadsheet/list pattern; natural for TUI |
| Clipboard | System clipboard via `pyperclip` | Enables cross-app copy/paste; user requested |
| Paste timestamp strategy | Draft (zero) timestamps | User requested; user stamps manually during playback |

---

## Implementation Plan

### Phase 1: Remove Bottom Input Panel & Add In-Place Editing

**Goal**: Replace the bottom `#edit-panel` Input widget with in-place cell editing inside the DataTable.

#### 1.1 Remove the edit panel from composition

**File**: `screen.py`

- Remove the `#edit-panel` Horizontal and its children from `compose()` (lines 298-300)
- Remove `#edit-panel` CSS rule (lines 197-199)
- Remove `_editing_text` and `_editing_timestamp` boolean flags from `__init__`
- Remove `action_edit_text()` and `action_edit_timestamp()` methods
- Remove `on_input_submitted()` handler

#### 1.2 Add inline editing state

**File**: `screen.py`

Add new instance variables to `LRCEditorScreen.__init__`:

```python
self._inline_editing: bool = False
self._inline_edit_row: int = -1
self._inline_edit_col: int = -1  # 1=Time, 2=Text
self._inline_edit_original: str = ""  # for cancel/undo
```

#### 1.3 Implement inline edit activation

**Key bindings** (modify `BINDINGS`):
- `e` → `edit_text` (unchanged action name, new behavior)
- `t` → `edit_timestamp` (unchanged action name, new behavior)
- `Enter` on a row → `edit_text` (new binding for Enter when not editing)

**`action_edit_text()` new behavior**:
1. Guard preview mode
2. Set `_inline_editing = True`, `_inline_edit_row = selected_index`, `_inline_edit_col = 2` (Text column)
3. Store original value in `_inline_edit_original`
4. Call `table.begin_cell_edit(row, col)` (see 1.4)

**`action_edit_timestamp()` new behavior**:
1. Guard preview mode
2. Set `_inline_editing = True`, `_inline_edit_row = selected_index`, `_inline_edit_col = 1` (Time column)
3. Store original value in `_inline_edit_original`
4. Call `table.begin_cell_edit(row, col)`

#### 1.4 Create `InlineEditDataTable` widget

**File**: `screen.py` (replace `LyricLineTable`)

Create a new subclass of `DataTable` that supports inline cell editing:

```python
class InlineEditDataTable(DataTable):
    """DataTable with inline cell editing support."""
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._editing: bool = False
        self._edit_row: int = -1
        self._edit_col: int = -1
        self._edit_buffer: str = ""
        self._edit_original: str = ""
```

**Key methods**:

- `begin_cell_edit(row: int, col: int) -> None`:
  - Store current cell value as `_edit_original`
  - Set `_editing = True`, `_edit_row`, `_edit_col`
  - Set `_edit_buffer` to current cell value
  - Render the cell with a cursor indicator (e.g., underline or reverse video)
  - Update the cell display to show editing state

- `commit_edit() -> None`:
  - Read `_edit_buffer`
  - Call `self.screen._on_inline_edit_commit(self._edit_row, self._edit_col, self._edit_buffer)`
  - Clear editing state
  - Rebuild the affected row

- `cancel_edit() -> None`:
  - Restore original value
  - Clear editing state
  - Rebuild the affected row

- `handle_key_edit(event: Key) -> bool`:
  - If not `_editing`, return `False`
  - Printable chars: append to `_edit_buffer`, update cell display
  - Backspace: remove last char from `_edit_buffer`, update cell display
  - Enter: call `commit_edit()`, return `True`
  - Escape: call `cancel_edit()`, return `True`
  - Return `True` to consume the event

**Override `key_press`** in `InlineEditDataTable`:
- If `_editing` is True, route to `handle_key_edit()` first
- If it returns True, stop event propagation
- Otherwise, fall through to normal DataTable key handling

**Visual feedback during editing**:
- Prepend `▶` or use Rich markup `[reverse]...[/reverse]` on the editing cell
- Show a cursor position indicator (pipe `│` at end of buffer)

#### 1.5 Handle edit commit on the screen side

**File**: `screen.py`

Add method `_on_inline_edit_commit(row, col, value)`:

```python
def _on_inline_edit_commit(self, row: int, col: int, value: str) -> None:
    if col == 1:  # Time column
        try:
            ts = self._parse_timestamp_input(value)
            self.state.set_timestamp(row, ts)
        except ValueError:
            self.notify("Invalid timestamp", severity="error", timeout=2)
            return
    elif col == 2:  # Text column
        self.state.set_text(row, value)
    
    self._inline_editing = False
    self._rebuild_table()
    self._update_displays()
    self._do_autosave()
```

#### 1.6 Guard other actions during inline editing

Add a guard method:

```python
def _guard_inline_edit(self) -> bool:
    return self._inline_editing
```

Prepend `if self._guard_inline_edit(): return` to all action methods that should be blocked during editing (same pattern as `_guard_preview()`).

The `action_quit_editor` should cancel inline edit first (like it cancels preview).

#### 1.7 Fix footer overlap

**File**: `screen.py` CSS

Since the `#edit-panel` is removed, the footer overlap issue is resolved for the edit input. However, the `StatusIndicator` at the bottom of `#editor-body` may still be obscured. Add bottom padding to `#editor-body`:

```css
#editor-body {
    height: 1fr;
    overflow: hidden;
    padding-bottom: 3;  /* clearance for docked footer */
}
```

This ensures the DataTable and StatusIndicator don't scroll under the 3-row docked footer.

---

### Phase 2: Multi-Row Select & Copy

**Goal**: Support Shift+Up/Down range selection with visual highlight and system clipboard copy.

#### 2.1 Add selection range state

**File**: `screen.py`

Add to `LRCEditorScreen.__init__`:

```python
self._selection_anchor: int = -1   # start of range (where Shift was first pressed)
self._selection_end: int = -1     # end of range (current cursor position)
self._range_active: bool = False   # whether a range selection is active
```

When `_range_active` is False, behavior is identical to current single-row selection.

#### 2.2 Override cursor movement in `InlineEditDataTable`

**File**: `screen.py`

Override `action_cursor_up()` and `action_cursor_down()` in `InlineEditDataTable` (currently in `LyricLineTable`):

- If Shift is held (`event.shift`), extend the range selection instead of moving the cursor
- Call `self.screen._extend_selection(new_row)` to update the range

**Challenge**: Textual's `action_cursor_up/down` don't receive the key event directly. We need to intercept at the `key_press` level.

**Approach**: Override `key_press` in `InlineEditDataTable`:

```python
def key_press(self, event: Key) -> None:
    if self._editing:
        if self.handle_key_edit(event):
            event.stop()
            event.prevent_default()
            return
    
    shift = event.shift
    if event.key in ("up", "down") and shift:
        # Range selection mode
        direction = 1 if event.key == "down" else -1
        current = self.cursor_row
        new_row = max(0, min(current + direction, self.row_count - 1))
        self.screen._extend_selection(new_row)
        self.move_cursor(row=new_row)
        event.stop()
        event.prevent_default()
        return
    
    super().key_press(event)
```

#### 2.3 Implement range selection logic

**File**: `screen.py`

Add methods to `LRCEditorScreen`:

```python
def _extend_selection(self, new_row: int) -> None:
    """Extend range selection to new_row."""
    if not self._range_active:
        self._range_active = True
        self._selection_anchor = self.state.selected_index
    self._selection_end = new_row
    self._update_range_highlight()

def _clear_range_selection(self) -> None:
    """Clear range selection (e.g., on non-shifted movement)."""
    if self._range_active:
        self._range_active = False
        self._selection_anchor = -1
        self._selection_end = -1
        self._rebuild_table()  # remove highlights
```

When a non-shifted cursor movement occurs, call `_clear_range_selection()`.

#### 2.4 Visual range highlight

**File**: `screen.py`

Modify `_rebuild_table()` and `_update_table_row()` to render range-selected rows differently:

- Range-selected rows get a visual indicator in the `#` column (e.g., `»` prefix instead of `>` for single selection)
- Range-selected rows' Text column gets `[reverse]` or `[on cyan]` background markup

Add helper:

```python
def _is_row_in_range(self, index: int) -> bool:
    if not self._range_active:
        return False
    lo = min(self._selection_anchor, self._selection_end)
    hi = max(self._selection_anchor, self._selection_end)
    return lo <= index <= hi
```

Modify `_row_label()`:

```python
def _row_label(self, index: int) -> str:
    if self._is_row_in_range(index):
        return f"»{index + 1}"
    if index == self.state.selected_index:
        return f">{index + 1}"
    return str(index + 1)
```

#### 2.5 Multi-row copy with system clipboard

**File**: `screen.py`

Replace `action_copy_line()` with `action_copy_selection()`:

```python
def action_copy_selection(self) -> None:
    if self._guard_preview() or self._guard_inline_edit():
        return
    
    if self._range_active:
        lo = min(self._selection_anchor, self._selection_end)
        hi = max(self._selection_anchor, self._selection_end)
        lines = self.state.timed_lines[lo:hi + 1]
        # Format as LRC text for clipboard
        text = "\n".join(line.text for line in lines)
        pyperclip.copy(text)
        self.notify(f"Copied {hi - lo + 1} lines", timeout=2)
    else:
        line = self.state.selected_line
        if line:
            pyperclip.copy(line.text)
            self.notify(f"Copied line {self.state.selected_index + 1}", timeout=2)
```

**Binding change**: `ctrl+c` → `copy_selection` (was `copy_line`)

#### 2.6 Add `pyperclip` dependency

**File**: `pyproject.toml`

Add `pyperclip` to the `admin` extra dependencies:

```bash
uv add --extra admin pyperclip
```

---

### Phase 3: Multi-Row Paste

**Goal**: Support pasting multiple lines of lyrics from system clipboard, inserting them as draft rows.

#### 3.1 Replace paste action

**File**: `screen.py`

Replace `action_paste_after()` with `action_paste_lines()`:

```python
def action_paste_lines(self) -> None:
    if self._guard_preview() or self._guard_inline_edit():
        return
    
    try:
        clipboard_text = pyperclip.paste()
    except pyperclip.PyperclipException:
        self.notify("Cannot access clipboard", severity="error", timeout=2)
        return
    
    if not clipboard_text or not clipboard_text.strip():
        self.notify("Clipboard is empty", timeout=2)
        return
    
    lines = [line.strip() for line in clipboard_text.split("\n") if line.strip()]
    if not lines:
        self.notify("No text to paste", timeout=2)
        return
    
    # Insert all lines after current selection with draft (0.0) timestamps
    self.state.insert_lines_after(self.state.selected_index, lines)
    self.state.select_line(self.state.selected_index + 1)
    self._rebuild_table()
    self._update_displays()
    self._do_autosave()
    self.notify(f"Pasted {len(lines)} lines (draft timestamps)", timeout=2)
```

**Binding change**: `ctrl+v` → `paste_lines` (was `paste_after`)

#### 3.2 Remove internal clipboard

**File**: `screen.py`

- Remove `self._clipboard` from `__init__`
- Remove old `action_copy_line()` and `action_paste_after()` methods

#### 3.3 Handle `pyperclip` unavailability gracefully

On systems without a display server (headless SSH), `pyperclip` may raise `PyperclipException`. Wrap all clipboard access in try/except and show a user-friendly notification.

Consider a fallback: if `pyperclip` is unavailable, fall back to the internal clipboard for single-line operations.

---

### Phase 4: Update Footer & Bindings

**Goal**: Update the footer display and key bindings to reflect new functionality.

#### 4.1 Update BINDINGS

**File**: `screen.py`

```python
BINDINGS = [
    # Playback/Nav
    Binding("space", "toggle_playback", "Play/Pause"),
    Binding("left", "seek_backward", "Seek -5s"),
    Binding("right", "seek_forward", "Seek +5s"),
    Binding("j", "jump_to_line", "Jump"),
    # Lyrics Edit
    Binding("ctrl+c", "copy_selection", "Copy"),
    Binding("ctrl+v", "paste_lines", "Paste"),
    Binding("i", "insert_after", "Insert Blank"),
    Binding("I", "insert_canonical", "Insert Canonical"),
    Binding("d", "delete_line", "Delete"),
    Binding("e", "edit_text", "Edit Text"),
    # Timecode
    Binding("tab", "stamp_and_advance", "Stamp+Advance"),
    Binding("shift+left", "show_earlier", "Earlier"),
    Binding("shift+right", "show_later", "Later"),
    Binding("t", "edit_timestamp", "Edit Time"),
    # General
    Binding("p", "preview_single", "Preview Line"),
    Binding("P", "preview_continuous", "Preview All"),
    Binding("s", "save_upload", "Save/Upload"),
    Binding("ctrl+z", "undo", "Undo"),
    Binding("ctrl+y", "redo", "Redo"),
    Binding("escape", "quit_editor", "Quit"),
    Binding("q", "quit_editor", "Quit"),
]
```

Note: Shift+Up/Down for range selection is handled at the `key_press` level, not as a Binding (since it modifies cursor movement behavior rather than triggering a discrete action).

#### 4.2 Update BINDING_GROUPS

```python
BINDING_GROUPS = {
    "Playback": [
        "toggle_playback", "seek_backward", "seek_forward", "jump_to_line",
    ],
    "Lyrics": [
        "copy_selection", "paste_lines", "insert_after",
        "insert_canonical", "delete_line", "edit_text",
    ],
    "Timecode": [
        "stamp_and_advance", "show_earlier", "show_later", "edit_timestamp",
    ],
    "General": [
        "preview_single", "preview_continuous", "save_upload",
        "undo", "redo", "quit_editor",
    ],
}
```

#### 4.3 Update footer display for Shift+Up/Down hint

**File**: `footer.py`

Add a visual hint in the Lyrics group for range selection. Either:
- Add a note in the group label: `"Lyrics (⇧↑↓ select)"`
- Or add a small extra `_BindingGroup` for selection hints

Simplest approach: update the Lyrics group label to include the hint.

---

### Phase 5: Edge Cases & Robustness

#### 5.1 Inline edit edge cases

- **Empty table**: Guard against editing when there are no rows
- **Row deleted during edit**: Cancel edit if the row being edited is deleted
- **Undo while editing**: Cancel inline edit before processing undo
- **Preview while editing**: Cancel inline edit before entering preview
- **Timestamp validation**: Show error notification on invalid timestamp input, keep editing mode active

#### 5.2 Range selection edge cases

- **Single row range**: If anchor == end, treat as single-row selection
- **Range across deleted rows**: Clear range selection on any structural change (insert, delete, paste)
- **Range during inline edit**: Block range selection while editing a cell
- **Range during preview**: Block range selection during preview

#### 5.3 Paste edge cases

- **Very large paste**: Limit to a reasonable max (e.g., 500 lines) with a warning
- **Mixed content**: Strip blank lines, trim whitespace per line
- **LRC-formatted paste**: If pasted text contains `[mm:ss.xx]` prefixes, parse them as timestamps instead of using draft timestamps (stretch goal, can be deferred)

---

## File Change Summary

| File | Changes |
|---|---|
| `screen.py` | Remove `#edit-panel`; replace `LyricLineTable` with `InlineEditDataTable`; add inline edit state & handlers; add range selection state & logic; replace copy/paste actions with system clipboard versions; update BINDINGS; add `padding-bottom: 3` to `#editor-body` CSS |
| `state.py` | No changes needed (existing `insert_lines_after()` already supports multi-line insert with draft timestamps) |
| `footer.py` | Update group label hint for range selection |
| `pyproject.toml` | Add `pyperclip` to admin extra |

## Dependency Addition

```bash
uv add --extra admin pyperclip
```

## Testing Strategy

1. **Manual testing**: Run `sow-admin edit-lrc` and verify:
   - Press `e` on a row → text cell becomes editable inline
   - Press `t` on a row → timestamp cell becomes editable inline
   - Type text, press Enter to commit, Escape to cancel
   - Shift+Up/Down creates range selection with visual highlight
   - Ctrl+C copies selected range to system clipboard
   - Ctrl+V pastes multi-line text from system clipboard as draft rows
   - Footer no longer obscures any content

2. **Unit tests** (if applicable):
   - Test `_is_row_in_range()` logic
   - Test `_parse_timestamp_input()` with various formats
   - Test `insert_lines_after()` with multiple texts
