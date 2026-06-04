# LRC Editor: Timecode Padding & Preview Mode (v2)

## Overview

Two enhancements to the Interactive TUI LRC editor (`src/stream_of_worship/admin/editor/`):

1. **Timecode Padding** — Globally shift all timecodes by 1/4-beat increments to compensate for human reaction delay when stamping with `a`.
2. **Preview Mode** — Play audio around the cursor line with auto-advancing cursor, in single-line or continuous mode.

### Changes from v1

- Renamed actions from `increase_padding`/`decrease_padding` to `show_earlier`/`show_later`
- Hard limit on padding at ±8 quarter-beats
- 0.0 is never a valid timestamp; preview blocks on 0.0 without ambiguity
- Added persistent "PREVIEW" badge in status bar during active preview
- Stamping (`a`) is blocked during preview with a warning notification
- Preview stop simplified to `p`/`P` toggle only; `space` stays audio-only pause
- Two preview modes: `p` = single-line, `P` (shift+p) = continuous walk-through
- Key bindings changed from `[`/`]` to `Shift+Left`/`Shift+Right`

---

## Feature 1: Timecode Padding

### Problem

When pressing `a` to stamp a lyric line's start timecode, human reaction time causes timestamps to be consistently late (too close to the actual start). Users need a way to shift all timecodes earlier or later in musically-meaningful increments.

### Design

- **Increment unit**: 1/4 beat (one sixteenth note at the song's BPM)
- **Quarter-beat duration**: `60 / (bpm * 4)` seconds when BPM is available; fallback to `0.2s` when BPM is unknown
- **Scope**: ALL timecodes shift together (global offset), not per-line
- **Direction convention**:
  - "show_earlier" = SUBTRACT 1/4 beat from all timecodes (timestamps move earlier, compensating for late stamping)
  - "show_later" = ADD 1/4 beat to all timecodes (timestamps move later, reducing the offset)
- **Hard limit**: `padding_quarters` is clamped to the range `[-8, +8]`. Attempting to exceed this limit shows a warning notification and does nothing.
- **Cumulative tracking**: A `padding_quarters` counter tracks the net number of 1/4-beat shifts applied. The actual offset in seconds is derived from this counter.
- **Original timestamps**: Store the raw stamped values separately so padding adjustments are always relative to the original, avoiding compounding rounding errors.

### Key Bindings

| Key | Action | Description |
|-----|--------|-------------|
| `Shift+Left` | `show_earlier` | Shift all timecodes earlier by 1/4 beat |
| `Shift+Right` | `show_later` | Shift all timecodes later by 1/4 beat |

### UI Display

The `StatusIndicator` widget shows the current padding offset, e.g.:

```
 * Dirty | Autosave: saved | Source: R2 | Pad: -0.50s (-2q)
```

Where `-2q` means 2 quarter-beats of padding applied (negative = earlier).

When padding is at the hard limit, the notification includes a hint:

```
Padding limit reached: +8q (+1.60s)
```

### Detailed Changes

#### `state.py` — EditorState

New constants:

```python
PADDING_QUARTERS_MIN = -8
PADDING_QUARTERS_MAX = 8
```

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
def adjust_padding(self, delta_quarters: int) -> bool:
    """Adjust global padding by delta_quarters (negative = earlier).

    Returns False if the adjustment would exceed the hard limit.
    Recalculates all timecodes from original_timestamps + new offset.
    Pushes an undo entry for the padding change.
    """
    new_quarters = self.padding_quarters + delta_quarters
    if new_quarters < PADDING_QUARTERS_MIN or new_quarters > PADDING_QUARTERS_MAX:
        return False

    old_quarters = self.padding_quarters
    self.padding_quarters = new_quarters
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
    return True
```

Modify `set_timestamp()`:

```python
def set_timestamp(self, index: int, time_seconds: float) -> None:
    if 0 <= index < len(self.timed_lines):
        raw_time = max(0.0, time_seconds - self.padding_offset_seconds)
        if index < len(self.original_timestamps):
            self.original_timestamps[index] = raw_time
        else:
            while len(self.original_timestamps) <= index:
                self.original_timestamps.append(0.0)
            self.original_timestamps[index] = raw_time

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
Binding("shift+left", "show_earlier", "Pad Earlier"),
Binding("shift+right", "show_later", "Pad Later"),
```

New actions:

```python
def action_show_earlier(self) -> None:
    """Shift all timecodes earlier by 1/4 beat."""
    if not self.state.adjust_padding(-1):
        self.notify(
            f"Padding limit reached: {self.state.padding_quarters:+d}q "
            f"({self.state.padding_offset_seconds:+.2f}s)",
            severity="warning", timeout=2,
        )
        return
    self._refresh_table()
    self._update_displays()
    self._do_autosave()
    offset = self.state.padding_offset_seconds
    quarters = self.state.padding_quarters
    self.notify(f"Padding: {offset:+.2f}s ({quarters:+d}q)", timeout=2)

def action_show_later(self) -> None:
    """Shift all timecodes later by 1/4 beat."""
    if not self.state.adjust_padding(1):
        self.notify(
            f"Padding limit reached: {self.state.padding_quarters:+d}q "
            f"({self.state.padding_offset_seconds:+.2f}s)",
            severity="warning", timeout=2,
        )
        return
    self._refresh_table()
    self._update_displays()
    self._do_autosave()
    offset = self.state.padding_offset_seconds
    quarters = self.state.padding_quarters
    self.notify(f"Padding: {offset:+.2f}s ({quarters:+d}q)", timeout=2)
```

Update `StatusIndicator` to display padding:

```python
def update_status(self, dirty: bool, autosave_ok: bool, source: str, padding_offset: float = 0.0, padding_quarters: int = 0, preview_active: bool = False) -> None:
    # ... existing logic ...
    parts = [f" {dirty_mark} Dirty | Autosave: {autosave_mark} | Source: {source_label}"]
    if preview_active:
        parts.append(" | PREVIEW")
    if padding_quarters != 0:
        parts.append(f" | Pad: {padding_offset:+.2f}s ({padding_quarters:+d}q)")
    self.update("".join(parts))
```

Update `_update_displays()` to pass padding and preview info:

```python
status.update_status(
    self.state.dirty, self._autosave_ok, self.state.source_mode,
    padding_offset=self.state.padding_offset_seconds,
    padding_quarters=self.state.padding_quarters,
    preview_active=self._preview_active,
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
    padding_quarters: int = 0
    tempo_bpm: Optional[float] = None
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

In autosave recovery path:

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

Two preview modes:

- **Single-line preview** (`p`): Play audio from 3s before the selected line's timestamp through 2s after it, then auto-stop. Cursor highlights the line during playback.
- **Continuous preview** (`P` / shift+p): Play audio from 3s before the selected line's timestamp. As playback passes each subsequent line's timestamp, the cursor auto-advances. Continues until playback reaches the end of audio or the user presses `P` again.

Common behavior:

- Pressing the same key again toggles preview off (pauses audio, stops auto-advance)
- The `CurrentLyricDisplay` updates in real-time as the cursor advances
- A persistent "PREVIEW" badge appears in the status bar while preview is active
- Stamping (`a`) is **blocked** during preview — shows a warning notification
- `space` remains audio-only play/pause and does NOT stop preview mode

### Key Bindings

| Key | Action | Description |
|-----|--------|-------------|
| `p` | `preview_single` | Preview current line only (3s before → line → 2s after) |
| `P` (shift+p) | `preview_continuous` | Start/stop continuous preview from cursor position |

### Detailed Changes

#### `screen.py` — LRCEditorScreen

New fields:

```python
_preview_active: bool = False
_preview_mode: Literal["single", "continuous"] = "single"
_preview_target_index: int = -1
```

New bindings:

```python
Binding("p", "preview_single", "Preview Line"),
Binding("P", "preview_continuous", "Preview All"),
```

New actions:

```python
def action_preview_single(self) -> None:
    """Preview current line: play from 3s before to 2s after, then stop."""
    if self._preview_active:
        self._stop_preview()
        return

    line = self.state.selected_line
    if not line or line.time_seconds == 0.0:
        self.notify("No timestamp on current line", severity="warning", timeout=2)
        return

    start_pos = max(0.0, line.time_seconds - 3.0)
    end_pos = line.time_seconds + 2.0
    self._preview_mode = "single"
    self._preview_target_index = self.state.selected_index
    self._preview_active = True
    self.playback.play(start_seconds=start_pos)
    self._update_displays()

def action_preview_continuous(self) -> None:
    """Start/stop continuous preview from cursor position."""
    if self._preview_active:
        self._stop_preview()
        return

    line = self.state.selected_line
    if not line or line.time_seconds == 0.0:
        self.notify("No timestamp on current line", severity="warning", timeout=2)
        return

    start_pos = max(0.0, line.time_seconds - 3.0)
    self._preview_mode = "continuous"
    self._preview_target_index = self.state.selected_index
    self._preview_active = True
    self.playback.play(start_seconds=start_pos)
    self._update_displays()

def _stop_preview(self) -> None:
    """Stop active preview and clean up state."""
    self.playback.pause()
    self._preview_active = False
    self._preview_mode = "single"
    self._preview_target_index = -1
    self._update_displays()
```

Modify `_on_playback_position()`:

```python
def _on_playback_position(self, position) -> None:
    if not self._preview_active:
        return

    current_secs = position.current_seconds

    if self._preview_mode == "single":
        target_line = self.state.timed_lines[self._preview_target_index]
        if current_secs >= target_line.time_seconds + 2.0:
            self._stop_preview()
            return
        if current_secs >= target_line.time_seconds:
            if self.state.selected_index != self._preview_target_index:
                self.state.select_line(self._preview_target_index)
                self._refresh_table()
                self._update_displays()
        return

    # continuous mode
    current_line_idx = self._find_line_at_position(current_secs)
    if current_line_idx != self.state.selected_index:
        self.state.select_line(current_line_idx)
        self._refresh_table()
        self._update_displays()
```

Modify `_on_playback_finished()`:

```python
def _on_playback_finished(self) -> None:
    self._preview_active = False
    self._preview_mode = "single"
    self._preview_target_index = -1
    self._update_playback_bar()
    self._update_displays()
```

Block stamping during preview — modify `action_stamp_line()`:

```python
def action_stamp_line(self) -> None:
    if self._preview_active:
        self.notify("Stamping disabled during preview", severity="warning", timeout=2)
        return
    # ... existing stamp logic ...
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
3. **Padding hard limit**: `padding_quarters` is clamped to `[-8, +8]`. Attempting to exceed shows a warning notification with the current limit value.
4. **Padding + stamp interaction**: When stamping a new timecode while padding is active, the raw value (without padding) is stored in `original_timestamps`, and the displayed/applied value includes the current padding offset.
5. **Preview on line with timestamp 0.0**: Show warning notification, don't start preview. 0.0 is never a valid timestamp.
6. **Preview + seek**: If user seeks during preview (left/right arrows), preview continues from new position. In single-line mode, seeking beyond the target line + 2s stops preview. In continuous mode, cursor advances based on new playback position.
7. **Preview + padding**: Preview uses the padded (displayed) timestamps, since those represent the intended sync points.
8. **Preview + stamp blocked**: Pressing `a` during preview shows "Stamping disabled during preview" warning. User must stop preview first.
9. **Undo padding**: Undoing a padding change restores all timecodes to their previous offset. The `original_timestamps` are never modified by undo/redo of padding — only `padding_quarters` changes, and timecodes are recalculated.
10. **Autosave recovery**: When recovering from autosave with non-zero `padding_quarters`, re-apply the offset to all timecodes after loading.
11. **Single-line preview on last line**: If the target line is the last timed line, playback continues for 2s after its timestamp then stops (even if audio continues beyond).
12. **Continuous preview wraps**: Continuous preview stops when playback reaches end of audio (handled by `_on_playback_finished`).

---

## Files Modified (Summary)

| File | Changes |
|------|---------|
| `state.py` | Add `PADDING_QUARTERS_MIN/MAX` constants; add `tempo_bpm`, `padding_quarters`, `original_timestamps`; add `padding_offset_seconds` property, `adjust_padding()` method (returns bool for limit); modify `set_timestamp()`, `insert_after()`, `insert_lines_after()`, `delete_line()`, `undo()`, `redo()`; add `__post_init__` |
| `screen.py` | Add `Shift+Left`, `Shift+Right`, `p`, `P` bindings + actions; add `_preview_active`, `_preview_mode`, `_preview_target_index` fields; add `_stop_preview()` helper; implement preview logic in `_on_playback_position()`; block stamping during preview; update `StatusIndicator` with padding info and PREVIEW badge; update `_on_playback_finished()`, `action_quit_editor()` |
| `autosave.py` | Add `padding_quarters` and `tempo_bpm` to `AutosaveState`; update save/load |
| `commands/audio.py` | Pass `tempo_bpm` to `EditorState` in `_build_fresh_editor_state()` and autosave recovery; re-apply padding on autosave recovery |
