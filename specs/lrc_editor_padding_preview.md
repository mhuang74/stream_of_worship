# LRC Editor: Timecode Padding & Preview Mode

## Overview

Two enhancements to the Interactive TUI LRC editor (`src/stream_of_worship/admin/editor/`):

1. **Timecode Padding** — Globally shift all timecodes by 1/4-beat increments to compensate for human reaction delay when stamping with `a`.
2. **Preview Mode** — Play audio from 3s before the cursor line, auto-advancing the cursor as each line passes.

---

## Feature 1: Timecode Padding

### Problem

When pressing `a` to stamp a lyric line's start timecode, human reaction time causes timestamps to be consistently late (too close to the actual start). Users need a way to shift all timecodes earlier or later in musically-meaningful increments.

### Design

- **Increment unit**: 1/4 beat (one sixteenth note at the song's BPM)
- **Quarter-beat duration**: `60 / (bpm * 4)` seconds when BPM is available; fallback to `0.2s` when BPM is unknown
- **Scope**: ALL timecodes shift together (global offset), not per-line
- **Direction convention**:
  - "Increase padding" = SUBTRACT 1/4 beat from all timecodes (timestamps move earlier, compensating for late stamping)
  - "Decrease padding" = ADD 1/4 beat to all timecodes (timestamps move later, reducing the offset)
- **Cumulative tracking**: A `padding_quarters` counter tracks the net number of 1/4-beat shifts applied. The actual offset in seconds is derived from this counter.
- **Original timestamps**: Store the raw stamped values separately so padding adjustments are always relative to the original, avoiding compounding rounding errors.

### Key Bindings

| Key | Action | Description |
|-----|--------|-------------|
| `[` | `increase_padding` | Shift all timecodes earlier by 1/4 beat (increase padding) |
| `]` | `decrease_padding` | Shift all timecodes later by 1/4 beat (decrease padding) |

### UI Display

The `StatusIndicator` widget shows the current padding offset, e.g.:

```
 * Dirty | Autosave: saved | Source: R2 | Pad: -0.50s (-2q)
```

Where `-2q` means 2 quarter-beats of padding applied (negative = earlier).

### Detailed Changes

#### `state.py` — EditorState

New fields:

```python
tempo_bpm: Optional[float] = None
padding_quarters: int = 0
original_timestamps: List[float] = field(default_factory=list)
```

New computed property:

```python
@property
def padding_offset_seconds(self) -> float:
    if self.tempo_bpm and self.tempo_bpm > 0:
        quarter_beat = 60.0 / (self.tempo_bpm * 4)
    else:
        quarter_beat = 0.2
    return self.padding_quarters * quarter_beat
```

New method:

```python
def adjust_padding(self, delta_quarters: int) -> None:
    """Adjust global padding by delta_quarters (negative = earlier).
    
    Recalculates all timecodes from original_timestamps + new offset.
    Pushes an undo entry for the padding change.
    """
    old_quarters = self.padding_quarters
    self.padding_quarters += delta_quarters
    new_offset = self.padding_offset_seconds
    
    for i, line in enumerate(self.timed_lines):
        if i < len(self.original_timestamps):
            line.time_seconds = max(0.0, self.original_timestamps[i] + new_offset)
    
    self._push_undo(UndoEntry(
        action="adjust_padding",
        index=0,
        old_time=float(old_quarters),
        new_time=float(self.padding_quarters),
    ))
    self.dirty = True
```

Modify `set_timestamp()`:

```python
def set_timestamp(self, index: int, time_seconds: float) -> None:
    if 0 <= index < len(self.timed_lines):
        # Store raw stamped value (without padding) in original_timestamps
        raw_time = max(0.0, time_seconds - self.padding_offset_seconds)
        if index < len(self.original_timestamps):
            self.original_timestamps[index] = raw_time
        else:
            # Extend original_timestamps if needed
            while len(self.original_timestamps) <= index:
                self.original_timestamps.append(0.0)
            self.original_timestamps[index] = raw_time
        
        # Apply current padding to get displayed time
        adjusted_time = max(0.0, raw_time + self.padding_offset_seconds)
        
        old_time = self.timed_lines[index].time_seconds
        self._push_undo(UndoEntry(
            action="set_timestamp", index=index,
            old_time=old_time, new_time=adjusted_time,
        ))
        self.timed_lines[index].time_seconds = adjusted_time
        self.dirty = True
```

Undo support for `adjust_padding` in `undo()`/`redo()`:

```python
elif entry.action == "adjust_padding":
    self.padding_quarters = int(entry.old_time)
    new_offset = self.padding_offset_seconds
    for i, line in enumerate(self.timed_lines):
        if i < len(self.original_timestamps):
            line.time_seconds = max(0.0, self.original_timestamps[i] + new_offset)
```

Handle `insert_after`/`insert_lines_after`/`delete_line` — keep `original_timestamps` in sync:

- `insert_after`: insert corresponding entry in `original_timestamps` at same index (value = `time_seconds - padding_offset_seconds`)
- `insert_lines_after`: insert multiple entries
- `delete_line`: pop from `original_timestamps` at same index

Initialize `original_timestamps` in `__init__` or post-init:

```python
def __post_init__(self):
    if not self.original_timestamps:
        self.original_timestamps = [line.time_seconds for line in self.timed_lines]
```

#### `screen.py` — LRCEditorScreen

New bindings:

```python
Binding("[", "increase_padding", "Pad Earlier"),
Binding("]", "decrease_padding", "Pad Later"),
```

New actions:

```python
def action_increase_padding(self) -> None:
    """Shift all timecodes earlier by 1/4 beat."""
    self.state.adjust_padding(-1)
    self._refresh_table()
    self._update_displays()
    self._do_autosave()
    offset = self.state.padding_offset_seconds
    quarters = self.state.padding_quarters
    self.notify(f"Padding: {offset:+.2f}s ({quarters:+d}q)", timeout=2)

def action_decrease_padding(self) -> None:
    """Shift all timecodes later by 1/4 beat."""
    self.state.adjust_padding(1)
    self._refresh_table()
    self._update_displays()
    self._do_autosave()
    offset = self.state.padding_offset_seconds
    quarters = self.state.padding_quarters
    self.notify(f"Padding: {offset:+.2f}s ({quarters:+d}q)", timeout=2)
```

Update `StatusIndicator` to display padding:

```python
def update_status(self, dirty: bool, autosave_ok: bool, source: str, padding_offset: float = 0.0, padding_quarters: int = 0) -> None:
    # ... existing logic ...
    if padding_quarters != 0:
        pad_str = f" | Pad: {padding_offset:+.2f}s ({padding_quarters:+d}q)"
    else:
        pad_str = ""
    self.update(f" {dirty_mark} Dirty | Autosave: {autosave_mark} | Source: {source_label}{pad_str}")
```

Update `_update_displays()` to pass padding info:

```python
status.update_status(
    self.state.dirty, self._autosave_ok, self.state.source_mode,
    padding_offset=self.state.padding_offset_seconds,
    padding_quarters=self.state.padding_quarters,
)
```

#### `autosave.py` — AutosaveState

Add fields:

```python
@dataclass
class AutosaveState:
    timed_lines: List[LRCLine]
    preserved_lines: List[LRCPreservedLine]
    transcribed_identity: R2ObjectIdentity
    dirty: bool
    source_mode: str
    padding_quarters: int = 0          # NEW
    tempo_bpm: Optional[float] = None  # NEW
```

Update `save_autosave()` and `load_autosave()` to serialize/deserialize these fields.

#### `commands/audio.py` — Editor state construction

Pass `tempo_bpm` when constructing `EditorState`:

In `_build_fresh_editor_state()`:

```python
return EditorState(
    ...,
    tempo_bpm=recording.tempo_bpm,
)
```

In autosave recovery path (line ~3427):

```python
editor_state = EditorState(
    ...,
    tempo_bpm=autosave_state.tempo_bpm,
    padding_quarters=autosave_state.padding_quarters,
)
```

After constructing from autosave, re-apply padding offset to all timecodes:

```python
if editor_state.padding_quarters != 0:
    offset = editor_state.padding_offset_seconds
    for i, line in enumerate(editor_state.timed_lines):
        if i < len(editor_state.original_timestamps):
            line.time_seconds = max(0.0, editor_state.original_timestamps[i] + offset)
```

---

## Feature 2: Preview Mode

### Problem

Users need to hear how the stamped lyrics align with the audio. Currently they must manually play, watch the position, and mentally track which line should be current. A preview mode automates this walkthrough.

### Design

- Press `p` to start preview from the currently selected line
- Audio starts playing from **3 seconds before** the selected line's timestamp (lead-in)
- As playback passes each subsequent line's timestamp, the cursor auto-advances to that line
- Preview continues walking through lines until:
  - Playback reaches the end of audio
  - User presses `space` (pause) or `q`/`escape`
  - User presses `p` again (toggles preview off)
- The `CurrentLyricDisplay` updates in real-time as the cursor advances

### Key Bindings

| Key | Action | Description |
|-----|--------|-------------|
| `p` | `preview` | Start/stop preview from cursor position |

### Detailed Changes

#### `screen.py` — LRCEditorScreen

New field:

```python
_preview_active: bool = False
```

New binding:

```python
Binding("p", "preview", "Preview"),
```

New action:

```python
def action_preview(self) -> None:
    """Start preview from 3s before selected line, or stop active preview."""
    if self._preview_active:
        self.playback.pause()
        self._preview_active = False
        return
    
    line = self.state.selected_line
    if not line or line.time_seconds == 0.0:
        self.notify("No timestamp on current line", severity="warning", timeout=2)
        return
    
    start_pos = max(0.0, line.time_seconds - 3.0)
    self.playback.play(start_seconds=start_pos)
    self._preview_active = True
```

Modify `_on_playback_position()` (currently a no-op at line 265):

```python
def _on_playback_position(self, position) -> None:
    if not self._preview_active:
        return
    
    # Find the last line whose timestamp <= current position
    current_line_idx = self._find_line_at_position(position.current_seconds)
    
    if current_line_idx != self.state.selected_index:
        self.state.select_line(current_line_idx)
        self._refresh_table()
        self._update_displays()
```

Modify `_on_playback_finished()`:

```python
def _on_playback_finished(self) -> None:
    self._preview_active = False
    self._update_playback_bar()
```

Modify `action_toggle_playback()`:

```python
def action_toggle_playback(self) -> None:
    if self._preview_active and self.playback.is_playing:
        self._preview_active = False
    self.playback.toggle_play_pause()
```

Modify `action_quit_editor()`:

```python
def action_quit_editor(self) -> None:
    self._preview_active = False
    # ... existing quit logic ...
```

---

## Edge Cases

1. **Padding with no BPM**: Use fixed 0.2s per quarter-beat increment. Display still shows `Pad: -0.40s (-2q)`.
2. **Padding clamping**: Timecodes are clamped to `>= 0.0`. If padding pushes a timecode below zero, it becomes 0.0 but `original_timestamps` retains the raw value so un-padding restores it correctly.
3. **Padding + stamp interaction**: When stamping a new timecode while padding is active, the raw value (without padding) is stored in `original_timestamps`, and the displayed/applied value includes the current padding offset.
4. **Preview on line with timestamp 0.0**: Show warning notification, don't start preview.
5. **Preview + seek**: If user seeks during preview (left/right arrows), preview continues from new position, advancing cursor based on the new playback position.
6. **Preview + padding**: Preview uses the padded (displayed) timestamps, since those represent the intended sync points.
7. **Undo padding**: Undoing a padding change restores all timecodes to their previous offset. The `original_timestamps` are never modified by undo/redo of padding — only `padding_quarters` changes, and timecodes are recalculated.
8. **Autosave recovery**: When recovering from autosave with non-zero `padding_quarters`, re-apply the offset to all timecodes after loading.

---

## Files Modified (Summary)

| File | Changes |
|------|---------|
| `state.py` | Add `tempo_bpm`, `padding_quarters`, `original_timestamps`; add `padding_offset_seconds` property, `adjust_padding()` method; modify `set_timestamp()`, `insert_after()`, `insert_lines_after()`, `delete_line()`, `undo()`, `redo()`; add `__post_init__` |
| `screen.py` | Add `[`, `]`, `p` bindings + actions; add `_preview_active` field; implement preview logic in `_on_playback_position()`; update `StatusIndicator` display with padding info; update `_on_playback_finished()`, `action_toggle_playback()`, `action_quit_editor()` |
| `autosave.py` | Add `padding_quarters` and `tempo_bpm` to `AutosaveState`; update save/load |
| `commands/audio.py` | Pass `tempo_bpm` to `EditorState` in `_build_fresh_editor_state()` and autosave recovery; re-apply padding on autosave recovery |
