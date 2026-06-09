# Enhance LRC Editor: In-Place Edit, Multi-Row Select & Paste (v2)

## Problem Statement

The current LRC editor has three UX issues:

1. **Input box obscured by footer**: The `#edit-panel` (containing the `Input` widget) sits at the bottom of `#editor-body`, directly under the `GroupedFooter` which is `dock: bottom` with `height: 3`. The docked footer overlaps the edit panel, making it invisible or partially hidden.

2. **No multi-row select/copy**: Only single-line copy (`Ctrl+C`) exists, using an internal `_clipboard` tuple. No range selection or system clipboard integration.

3. **No multi-row paste**: `Ctrl+V` pastes a single internal-clipboard line. Cannot paste multiple lines of lyrics from external sources.

## Scope

- **Copy/paste is lyrics-text only.** Timestamps are never copied or pasted. Pasted lines always receive draft (0.0) timestamps; the user stamps them manually during playback.
- No LRC-format copy/paste (no `[mm:ss.xx]` prefix parsing).

## Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| In-place edit scope | Both Text & Timestamp columns | User requested; both fields benefit from inline editing |
| Multi-row selection | Shift+Up/Down range selection | Familiar spreadsheet/list pattern; natural for TUI |
| Clipboard | System clipboard via `pyperclip` (lazy-imported) | Enables cross-app copy/paste; user requested |
| Clipboard fallback | Keep internal `_clipboard` for single-line ops when `pyperclip` unavailable | Graceful degradation on headless SSH |
| Paste timestamp strategy | Draft (zero) timestamps | User requested; user stamps manually during playback |
| Cell rendering | Row replacement via `update_cell_at()` | Simplest approach; may flicker on very large tables |
| Edit state ownership | Screen owns all edit state; table is rendering delegate | Eliminates dual-state desync risk |
| Escape-discard | Double-Escape confirmation within 2s | Prevents accidental data loss |
| Large paste | Confirmation prompt for >10 lines | Prevents accidental bulk insertion |

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

#### 1.2 Add inline editing state (screen-owned)

**File**: `screen.py`

Add new instance variables to `LRCEditorScreen.__init__`:

```python
self._inline_editing: bool = False
self._inline_edit_row: int = -1
self._inline_edit_col: int = -1  # 1=Time, 2=Text
self._inline_edit_original: str = ""  # for cancel/undo
self._inline_edit_buffer: str = ""   # current edit content
self._escape_confirm_pending: bool = False
```

**Invariant**: The `InlineEditDataTable` never stores original values or edit state. It receives the buffer from the screen and renders it. All state lives on the screen.

#### 1.3 Implement inline edit activation

**Key bindings** (modify `BINDINGS`):
- `e` → `edit_text` (unchanged action name, new behavior)
- `t` → `edit_timestamp` (unchanged action name, new behavior)

**`action_edit_text()` new behavior**:
1. Guard: if `_inline_editing`, commit current edit first
2. Guard preview mode
3. Guard empty table
4. Set `_inline_editing = True`, `_inline_edit_row = selected_index`, `_inline_edit_col = 2` (Text column)
5. Store original value in `_inline_edit_original`
6. Set `_inline_edit_buffer` to current cell value
7. Call `table.begin_cell_edit(row, col, buffer)` (see 1.4)
8. Reset `_escape_confirm_pending = False`

**`action_edit_timestamp()` new behavior**:
1. Guard: if `_inline_editing`, commit current edit first
2. Guard preview mode
3. Guard empty table
4. Set `_inline_editing = True`, `_inline_edit_row = selected_index`, `_inline_edit_col = 1` (Time column)
5. Store original value in `_inline_edit_original`
6. Set `_inline_edit_buffer` to current cell value
7. Call `table.begin_cell_edit(row, col, buffer)`
8. Reset `_escape_confirm_pending = False`

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
```

**Key methods**:

- `begin_cell_edit(row: int, col: int, buffer: str) -> None`:
  - Set `_editing = True`, `_edit_row`, `_edit_col`
  - Render the cell with a column-specific prefix and cursor indicator:
    - Text column (col=2): prefix `✏`, append cursor `│`
    - Timestamp column (col=1): prefix `⏱`, append cursor `│`
  - Call `self.update_cell_at(Coordinate(row, col), f"✏{buffer}│")` (or `⏱` for timestamp)
  - This is the **row replacement** approach: each keystroke replaces the cell content.

- `update_edit_display(buffer: str) -> None`:
  - Re-render the editing cell with the current buffer + cursor indicator.
  - Prefix based on `_edit_col` (✏ or ⏱).
  - Call `self.update_cell_at(Coordinate(self._edit_row, self._edit_col), f"{prefix}{buffer}│")`

- `end_cell_edit() -> None`:
  - Clear `_editing`, `_edit_row`, `_edit_col`
  - Does NOT restore the cell value — the screen's commit/cancel handler does that via `_update_table_row()`

- `handle_key_edit(event: Key) -> bool`:
  - If not `_editing`, return `False`
  - Printable chars: append to screen's `_inline_edit_buffer`, call `self.update_edit_display(buffer)`
  - Backspace: remove last char from screen's `_inline_edit_buffer`, call `self.update_edit_display(buffer)`
  - Enter: call `self.screen._on_inline_edit_commit()`, return `True`
  - Escape: call `self.screen._on_inline_edit_escape()`, return `True`
  - Return `True` to consume the event

**Override `key_press`** in `InlineEditDataTable`:

```python
def key_press(self, event: Key) -> None:
    # Branch 1: Inline editing — consume all keys
    if self._editing:
        if self.handle_key_edit(event):
            event.stop()
            event.prevent_default()
            return

    # Branch 2: Range selection — intercept Shift+Up/Down
    shift = event.shift
    if event.key in ("up", "down") and shift:
        direction = 1 if event.key == "down" else -1
        current = self.cursor_row
        new_row = max(0, min(current + direction, self.row_count - 1))
        self.screen._extend_selection(new_row)
        self.move_cursor(row=new_row)
        event.stop()
        event.prevent_default()
        return

    # Branch 3: Normal cursor movement — clear range selection
    if event.key in ("up", "down") and not shift:
        self.screen._clear_range_selection()

    # Branch 4: Fall through to DataTable default
    super().key_press(event)
```

**Visual feedback during editing**:
- Text column: `✏hello world│` (prefix ✏, cursor │ at end of buffer)
- Timestamp column: `⏱01:23.45│` (prefix ⏱, cursor │ at end of buffer)
- The `#` column label changes to `✏` or `⏱` for the editing row (via `_row_label()`)

#### 1.5 Handle edit commit on the screen side

**File**: `screen.py`

Add method `_on_inline_edit_commit()`:

```python
def _on_inline_edit_commit(self) -> None:
    value = self._inline_edit_buffer
    row = self._inline_edit_row
    col = self._inline_edit_col

    table = self.query_one("#line-table", InlineEditDataTable)
    table.end_cell_edit()

    if col == 1:  # Time column
        try:
            ts = self._parse_timestamp_input(value)
            self.state.set_timestamp(row, ts)
        except ValueError:
            self.notify("Invalid timestamp", severity="error", timeout=2)
            # Re-enter edit mode with the invalid value still in buffer
            table.begin_cell_edit(row, col, value)
            return
    elif col == 2:  # Text column
        self.state.set_text(row, value)

    self._inline_editing = False
    self._escape_confirm_pending = False
    self._rebuild_table()
    self._update_displays()
    self._do_autosave()
```

Add method `_on_inline_edit_escape()`:

```python
def _on_inline_edit_escape(self) -> None:
    if self._inline_edit_buffer != self._inline_edit_original:
        # Buffer has changes — require double-Escape confirmation
        if not self._escape_confirm_pending:
            self._escape_confirm_pending = True
            self.notify("Discard changes? Press Escape again to confirm", severity="warning", timeout=2)
            # Auto-reset after 2 seconds
            self.set_timer(2, self._reset_escape_confirm)
            return
        # Second Escape within window — discard
        self._escape_confirm_pending = False

    # No changes, or confirmed discard — cancel edit
    table = self.query_one("#line-table", InlineEditDataTable)
    table.end_cell_edit()
    self._inline_editing = False
    self._escape_confirm_pending = False
    self._update_table_row(self._inline_edit_row)
    self.query_one("#line-table", InlineEditDataTable).focus()
```

```python
def _reset_escape_confirm(self) -> None:
    self._escape_confirm_pending = False
```

#### 1.6 Guard other actions during inline editing

Add a guard method:

```python
def _guard_inline_edit(self) -> bool:
    if self._inline_editing:
        self.notify("Finish editing first (Enter/Esc)", severity="warning", timeout=2)
        return True
    return False
```

Prepend `if self._guard_inline_edit(): return` to all action methods that should be blocked during editing (same pattern as `_guard_preview()`).

The `action_quit_editor` should cancel inline edit first (like it cancels preview):

```python
def action_quit_editor(self) -> None:
    if self._inline_editing:
        # Discard edit silently on quit (no double-confirm needed — quit is already confirmed)
        table = self.query_one("#line-table", InlineEditDataTable)
        table.end_cell_edit()
        self._inline_editing = False
        self._escape_confirm_pending = False
        self._update_table_row(self._inline_edit_row)
        return

    if self._preview_active:
        self._stop_preview()
        return
    # ... existing quit logic
```

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

**Goal**: Support Shift+Up/Down range selection with visual highlight and system clipboard copy (text only).

#### 2.1 Add selection range state

**File**: `screen.py`

Add to `LRCEditorScreen.__init__`:

```python
self._selection_anchor: int = -1   # start of range (where Shift was first pressed)
self._selection_end: int = -1     # end of range (current cursor position)
self._range_active: bool = False   # whether a range selection is active
```

When `_range_active` is False, behavior is identical to current single-row selection.

#### 2.2 Range selection via key_press override

**File**: `screen.py`

The Shift+Up/Down interception is handled in `InlineEditDataTable.key_press()` (see 1.4, Branch 2). It calls `self.screen._extend_selection(new_row)` and `self.screen._clear_range_selection()`.

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
        self._update_range_highlight()
```

#### 2.4 Visual range highlight (incremental)

**File**: `screen.py`

**`_update_range_highlight()`** — incrementally updates only the rows whose range-membership changed:

```python
def _update_range_highlight(self) -> None:
    """Incrementally update row labels for range selection changes."""
    try:
        table = self.query_one("#line-table", InlineEditDataTable)
    except NoMatches:
        return

    lo = min(self._selection_anchor, self._selection_end) if self._range_active else -1
    hi = max(self._selection_anchor, self._selection_end) if self._range_active else -1

    for i in range(table.row_count):
        in_range = lo <= i <= hi if self._range_active else False
        new_label = self._row_label(i)
        table.update_cell_at(Coordinate(i, 0), new_label, update_width=True)

        # Update text column background for range rows
        if i < self.state.line_count:
            text = self.state.timed_lines[i].text
            if in_range:
                text = f"[on cyan]{text}[/on cyan]"
            table.update_cell_at(Coordinate(i, 2), text, update_width=True)
```

**Note**: This is an incremental approach — it updates every row's label and text, but does NOT call `_rebuild_table()` (which reconstructs the entire table). For very large tables (>200 rows), this could be further optimized to only update the symmetric difference of the old and new ranges, but the full-scan approach is simpler and sufficient for typical LRC files (20-80 lines).

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
    if self._inline_editing and index == self._inline_edit_row:
        prefix = "✏" if self._inline_edit_col == 2 else "⏱"
        return f"{prefix}{index + 1}"
    if self._is_row_in_range(index):
        return f"»{index + 1}"
    if index == self.state.selected_index:
        return f">{index + 1}"
    return str(index + 1)
```

#### 2.5 Multi-row copy with system clipboard (text only)

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
        text = "\n".join(line.text for line in lines)
    else:
        line = self.state.selected_line
        if not line:
            return
        text = line.text

    # Try system clipboard first; fall back to internal clipboard
    try:
        import pyperclip
        pyperclip.copy(text)
    except Exception:
        self._clipboard = text
        self.notify("Copied (internal clipboard — pyperclip unavailable)", timeout=2)
        if self._range_active:
            self.notify(f"Copied {hi - lo + 1} lines", timeout=2)
        else:
            self.notify(f"Copied line {self.state.selected_index + 1}", timeout=2)
        return

    if self._range_active:
        self.notify(f"Copied {hi - lo + 1} lines", timeout=2)
    else:
        self.notify(f"Copied line {self.state.selected_index + 1}", timeout=2)
```

**Binding change**: `ctrl+c` → `copy_selection` (was `copy_line`)

**`pyperclip` import strategy**: Lazy-import inside the action method. Do NOT import at module level. This prevents crashes on systems without `pyperclip` installed or without a display server.

#### 2.6 Add `pyperclip` dependency

**File**: `pyproject.toml`

Add `pyperclip` to the `admin` extra dependencies:

```bash
uv add --extra admin pyperclip
```

---

### Phase 3: Multi-Row Paste

**Goal**: Support pasting multiple lines of lyrics from system clipboard, inserting them as draft rows. Text only — no timestamp paste.

#### 3.1 Replace paste action

**File**: `screen.py`

Replace `action_paste_after()` with `action_paste_lines()`:

```python
def action_paste_lines(self) -> None:
    if self._guard_preview() or self._guard_inline_edit():
        return

    # Try system clipboard first; fall back to internal clipboard
    clipboard_text = None
    try:
        import pyperclip
        clipboard_text = pyperclip.paste()
    except Exception:
        # Fall back to internal clipboard (single-line only)
        if self._clipboard is not None:
            clipboard_text = self._clipboard
        else:
            self.notify("Cannot access clipboard", severity="error", timeout=2)
            return

    if not clipboard_text or not clipboard_text.strip():
        self.notify("Clipboard is empty", timeout=2)
        return

    lines = [line.strip() for line in clipboard_text.split("\n") if line.strip()]
    if not lines:
        self.notify("No text to paste", timeout=2)
        return

    # Confirmation prompt for large paste (>10 lines)
    if len(lines) > 10:
        self._pending_paste_lines = lines
        self._show_paste_confirm(len(lines))
        return

    self._do_paste_lines(lines)
```

```python
def _do_paste_lines(self, lines: list[str]) -> None:
    """Execute the paste after optional confirmation."""
    self.state.insert_lines_after(self.state.selected_index, lines)
    self.state.select_line(self.state.selected_index + 1)
    self._rebuild_table()
    self._update_displays()
    self._do_autosave()
    self.notify(f"Pasted {len(lines)} lines (draft timestamps)", timeout=2)
```

```python
def _show_paste_confirm(self, count: int) -> None:
    """Show confirmation dialog for large paste."""
    from textual.screen import ModalScreen

    class PasteConfirmDialog(ModalScreen[bool]):
        BINDINGS = [
            Binding("y", "confirm", "Yes"),
            Binding("n", "cancel", "No"),
            Binding("escape", "cancel", "No"),
        ]

        def __init__(self, line_count: int):
            super().__init__()
            self.line_count = line_count

        def compose(self) -> ComposeResult:
            with Vertical():
                yield Label(f"Paste {self.line_count} lines with draft timestamps?")
                yield Label("[d]Press [bold]y[/bold] to paste | [bold]n[/bold] to cancel[/]")

        def action_confirm(self) -> None:
            self.dismiss(True)

        def action_cancel(self) -> None:
            self.dismiss(False)

    def _handle_paste_confirm(confirmed: bool) -> None:
        if confirmed and self._pending_paste_lines:
            self._do_paste_lines(self._pending_paste_lines)
        self._pending_paste_lines = None

    self.app.push_screen(PasteConfirmDialog(count), _handle_paste_confirm)
```

Add to `__init__`:

```python
self._pending_paste_lines: Optional[list[str]] = None
```

**Binding change**: `ctrl+v` → `paste_lines` (was `paste_after`)

#### 3.2 Update internal clipboard type

**File**: `screen.py`

Change `self._clipboard` from `Optional[tuple]` (text, time_seconds) to `Optional[str]` (text only). The internal clipboard now stores only lyrics text, consistent with the text-only copy/paste scope.

Update `__init__`:

```python
self._clipboard: Optional[str] = None  # internal fallback clipboard (text only)
```

#### 3.3 Handle `pyperclip` unavailability gracefully

On systems without a display server (headless SSH), `pyperclip` may raise `PyperclipException`. The lazy-import + try/except pattern in `action_copy_selection()` and `action_paste_lines()` handles this:

- **Copy**: Falls back to internal `_clipboard` (single-line text only)
- **Paste**: Falls back to internal `_clipboard` (single-line text only)
- **Multi-line operations on headless**: Show "Cannot access clipboard" error — the internal clipboard is single-line only

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
BINDING_GROUPS: dict[str, list[str]] = {
    "Playback": [
        "toggle_playback", "seek_backward", "seek_forward", "jump_to_line",
    ],
    "Lyrics (⇧↑↓ select)": [
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

The group label change in `BINDING_GROUPS` (above) is sufficient. The `GroupedFooter` reads group labels from `BINDING_GROUPS` keys, so renaming `"Lyrics"` to `"Lyrics (⇧↑↓ select)"` automatically updates the footer.

---

### Phase 5: Edge Cases & Robustness

#### 5.1 Inline edit edge cases

- **Empty table**: Guard against editing when there are no rows (`if self.state.line_count == 0: return`)
- **Row deleted during edit**: Cancel edit if the row being edited is deleted (check `_inline_edit_row < self.state.line_count` in the guard)
- **Undo while editing**: Cancel inline edit before processing undo (add `_guard_inline_edit` check to `action_undo`)
- **Preview while editing**: Cancel inline edit before entering preview (add `_guard_inline_edit` check to preview actions)
- **Timestamp validation failure**: Show error notification, keep editing mode active with the invalid value still in the buffer (see 1.5)
- **Double-Escape auto-reset**: The `_reset_escape_confirm` timer fires after 2s, resetting `_escape_confirm_pending` so a later Escape doesn't accidentally discard

#### 5.2 Range selection edge cases

- **Single row range**: If anchor == end, treat as single-row selection (copy one line)
- **Range across deleted rows**: Clear range selection on any structural change (insert, delete, paste) — call `_clear_range_selection()` in those action handlers
- **Range during inline edit**: Block range selection while editing a cell (the `key_press` override checks `_editing` first)
- **Range during preview**: Block range selection during preview (the `key_press` override checks `_guard_preview` via the existing `action_cursor_up/down` guards)

#### 5.3 Paste edge cases

- **Very large paste**: Confirmation prompt for >10 lines (see 3.1)
- **Mixed content**: Strip blank lines, trim whitespace per line
- **Empty clipboard**: Show "Clipboard is empty" notification
- **Headless SSH**: Fall back to internal single-line clipboard; show error for multi-line

---

## File Change Summary

| File | Changes |
|---|---|
| `screen.py` | Remove `#edit-panel`; replace `LyricLineTable` with `InlineEditDataTable`; add inline edit state & handlers (screen-owned); add range selection state & logic; replace copy/paste actions with system clipboard versions (text only); update BINDINGS; add `padding-bottom: 3` to `#editor-body` CSS; add double-Escape confirmation; add paste confirmation dialog; change `_clipboard` type from `Optional[tuple]` to `Optional[str]` |
| `state.py` | No changes needed (existing `insert_lines_after()` already supports multi-line insert with draft timestamps) |
| `footer.py` | No code changes needed (group label updated via `BINDING_GROUPS` key rename) |
| `pyproject.toml` | Add `pyperclip` to admin extra |

## Dependency Addition

```bash
uv add --extra admin pyperclip
```

## Testing Strategy

1. **Manual testing**: Run `sow-admin edit-lrc` and verify:
   - Press `e` on a row → text cell becomes editable inline with ✏ prefix and │ cursor
   - Press `t` on a row → timestamp cell becomes editable inline with ⏱ prefix and │ cursor
   - Type text, press Enter to commit, Escape to cancel
   - Escape with changes → "Discard changes?" notification; second Escape within 2s confirms
   - Shift+Up/Down creates range selection with `»` labels and cyan background
   - Ctrl+C copies selected range text to system clipboard
   - Ctrl+V pastes multi-line text from system clipboard as draft rows
   - Paste >10 lines → confirmation dialog
   - Footer no longer obscures any content
   - On headless SSH: copy/paste falls back to internal clipboard

2. **Unit tests** (if applicable):
   - Test `_is_row_in_range()` logic
   - Test `_parse_timestamp_input()` with various formats
   - Test `insert_lines_after()` with multiple texts
