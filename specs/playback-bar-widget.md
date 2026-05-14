# Playback Bar Widget Implementation Plan

## Context

**Problem:** Users browsing songs in `BrowseScreen` and editing songsets in `SongsetEditorScreen` have no visual feedback for playback position. The only feedback is toast notifications (`self.notify()`) which:
1. Stack up and clutter the screen when skipping forward/backward repeatedly
2. Disappear after a few seconds, losing context
3. Don't show real-time progress during playback

**Solution:** Create a reusable `PlaybackBar` widget that displays current position, duration, and a visual progress bar. This widget will be shared across `BrowseScreen`, `SongsetEditorScreen`, and `LyricsPreviewScreen`, replacing the inline progress bar logic currently in `LyricsPreviewScreen`.

## Requirements Summary

- **Visual progress bar**: Shows current position / total duration with Unicode block characters (`█░`)
- **Real-time updates**: Updates ~10x/second during playback via `PlaybackService` callbacks
- **State indicator**: Shows `▶` (playing) or `⏸` (paused) icon
- **Auto-hide**: Hidden when playback is stopped, visible when playing/paused
- **Reusable**: Single widget class used across multiple screens
- **No notification spam**: Skip forward/backward actions update the bar instead of showing toasts

## Implementation Plan

### Phase 1: Create `widgets/` Package Structure

**Files:**
- `src/stream_of_worship/app/widgets/__init__.py` (new)
- `src/stream_of_worship/app/widgets/playback_bar.py` (new)

**`__init__.py`:**
```python
"""Custom Textual widgets for the Stream of Worship app."""

from stream_of_worship.app.widgets.playback_bar import PlaybackBar

__all__ = ["PlaybackBar"]
```

### Phase 2: Implement `PlaybackBar` Widget

**File:** `src/stream_of_worship/app/widgets/playback_bar.py`

```python
"""Reusable playback progress bar widget."""

from textual.widgets import Static
from textual.message import Message
from stream_of_worship.app.services.playback import PlaybackService, PlaybackState, PlaybackPosition


class PlaybackBar(Static):
    """A progress bar widget that displays playback position and duration.
    
    Automatically registers callbacks with the PlaybackService and updates
    in real-time during playback. Hidden when playback is stopped.
    
    Attributes:
        playback: The PlaybackService instance to monitor
        bar_width: Width of the visual progress bar in characters
    """
    
    DEFAULT_CSS = """
    PlaybackBar {
        text-align: center;
        content-align: center middle;
        height: 1;
        margin: 1 2;
    }
    """
    
    def __init__(
        self,
        playback: PlaybackService,
        bar_width: int = 30,
        id: str | None = None,
        classes: str | None = None,
    ):
        super().__init__("", id=id, classes=classes)
        self.playback = playback
        self.bar_width = bar_width
    
    def on_mount(self) -> None:
        """Register callbacks with the playback service."""
        self.playback.set_callbacks(
            on_position_changed=self._on_position_changed,
            on_state_changed=self._on_state_changed,
            on_finished=self._on_finished,
        )
        # Initial state
        self._update_visibility()
    
    def on_unmount(self) -> None:
        """Unregister callbacks to prevent memory leaks."""
        self.playback.set_callbacks()
    
    def _on_position_changed(self, position: PlaybackPosition) -> None:
        """Handle position updates from playback service (runs in background thread)."""
        def _update():
            self._update_display(position)
        self.call_after_refresh(_update)
    
    def _on_state_changed(self, state: PlaybackState) -> None:
        """Handle state changes from playback service (runs in background thread)."""
        def _update():
            self._update_visibility()
            if state != PlaybackState.STOPPED:
                self._update_display(self.playback.get_position())
        self.call_after_refresh(_update)
    
    def _on_finished(self) -> None:
        """Handle playback finished."""
        def _update():
            self._update_visibility()
        self.call_after_refresh(_update)
    
    def _update_visibility(self) -> None:
        """Show/hide based on playback state."""
        if self.playback.is_stopped:
            self.add_class("hidden")
        else:
            self.remove_class("hidden")
    
    def _update_display(self, position: PlaybackPosition) -> None:
        """Update the progress bar display."""
        current_str = self._format_time(position.current_seconds)
        total_str = self._format_time(position.total_seconds)
        
        # Build visual progress bar
        filled = int((position.progress_percent / 100) * self.bar_width)
        empty = self.bar_width - filled
        bar = "█" * filled + "░" * empty
        
        # State icon
        icon = "⏸" if self.playback.is_paused else "▶"
        
        self.update(f"{icon} {current_str} / {total_str}  [{bar}]")
    
    @staticmethod
    def _format_time(seconds: float) -> str:
        """Format seconds as MM:SS."""
        minutes = int(seconds // 60)
        secs = int(seconds % 60)
        return f"{minutes}:{secs:02d}"
```

**Key Design Decisions:**
1. **Widget owns callback registration** — screens just pass the `PlaybackService` instance
2. **`call_after_refresh()` for thread safety** — callbacks run in background thread, must schedule UI updates on main thread
3. **Unregister on unmount** — prevents callbacks to destroyed widgets
4. **Inline CSS** — widget is self-contained, no external CSS required (but can be overridden)
5. **Hidden when stopped** — bar only appears during playback, avoiding wasted space

### Phase 3: Add Skip Bindings to BrowseScreen

**File:** `src/stream_of_worship/app/screens/browse.py`

**Changes:**

1. Add imports:
```python
from stream_of_worship.app.widgets import PlaybackBar
```

2. Add bindings (line 24):
```python
BINDINGS = [
    ("s", "add_to_songset", "Add to Songset"),
    ("space", "toggle_playback", "Play/Stop"),
    ("left", "skip_backward", "Skip -10s"),
    ("right", "skip_forward", "Skip +10s"),
    ("f", "focus_search", "Search"),
    ("escape", "back", "Back"),
    ("q", "quit", "Quit"),
]
```

3. Add widget to `compose()` (after button row, before Footer):
```python
yield PlaybackBar(self.app.playback, id="playback_bar")
```

4. Add skip actions (after `action_toggle_playback`):
```python
def action_skip_forward(self) -> None:
    """Skip forward 10 seconds in current playback."""
    if not self.app.playback.is_playing and not self.app.playback.is_paused:
        return
    self.app.playback.skip_forward(10.0)

def action_skip_backward(self) -> None:
    """Skip backward 10 seconds in current playback."""
    if not self.app.playback.is_playing and not self.app.playback.is_paused:
        return
    self.app.playback.skip_backward(10.0)
```

5. Remove `self.notify()` calls from `action_toggle_playback` (optional — keep for play/stop feedback, remove for skip actions).

### Phase 4: Add PlaybackBar to SongsetEditorScreen

**File:** `src/stream_of_worship/app/screens/songset_editor.py`

**Changes:**

1. Add import:
```python
from stream_of_worship.app.widgets import PlaybackBar
```

2. Add widget to `compose()` (after button row, before Footer):
```python
yield PlaybackBar(self.playback, id="playback_bar")
```

3. Remove `self.notify()` calls from `action_skip_forward()` and `action_skip_backward()`:
```python
def action_skip_forward(self) -> None:
    """Skip forward 10 seconds in current playback."""
    if not self.playback.is_playing:
        return
    self.playback.skip_forward(10.0)

def action_skip_backward(self) -> None:
    """Skip backward 10 seconds in current playback."""
    if not self.playback.is_playing:
        return
    self.playback.skip_backward(10.0)
```

4. Keep `self.notify()` for play/stop in `action_toggle_playback()` (optional — provides feedback when starting/stopping).

### Phase 5: Refactor LyricsPreviewScreen to Use PlaybackBar

**File:** `src/stream_of_worship/app/screens/lyrics_preview.py`

**Changes:**

1. Add import:
```python
from stream_of_worship.app.widgets import PlaybackBar
```

2. Replace `Static("", id="progress_bar")` in `compose()` with:
```python
yield PlaybackBar(self.playback, id="playback_bar")
```

3. Remove `_update_progress_bar()` method (lines 306-325) — no longer needed.

4. Remove progress bar update from `_on_position_changed()` — keep only lyrics sync logic:
```python
def _on_position_changed(self, position: PlaybackPosition) -> None:
    def _update():
        new_index = self._find_current_line(position.current_seconds)
        if new_index != self.current_line_index:
            self.current_line_index = new_index
            self._update_lyrics_display()
            self._highlight_lrc_row()
    self.call_after_refresh(_update)
```

5. Remove `_on_state_changed()` method — PlaybackBar handles state changes.

6. Keep `_on_finished()` for any cleanup needed.

### Phase 6: Update CSS

**File:** `src/stream_of_worship/app/screens/app.tcss`

**Changes:**

1. Remove duplicate `#progress_bar` rules (lines 96-98 and 203-208) — PlaybackBar has inline CSS.

2. Add optional override for PlaybackBar if needed:
```css
/* PlaybackBar - optional overrides */
PlaybackBar {
    /* Override bar styling here if needed */
}
```

3. Ensure `.hidden` class exists (line 238-240 already has it).

## Files to Modify/Create

| File | Action | Purpose |
|------|--------|---------|
| `src/stream_of_worship/app/widgets/__init__.py` | Create | Export PlaybackBar |
| `src/stream_of_worship/app/widgets/playback_bar.py` | Create | PlaybackBar widget implementation |
| `src/stream_of_worship/app/screens/browse.py` | Modify | Add skip bindings, embed PlaybackBar |
| `src/stream_of_worship/app/screens/songset_editor.py` | Modify | Embed PlaybackBar, remove skip notifications |
| `src/stream_of_worship/app/screens/lyrics_preview.py` | Modify | Use PlaybackBar, remove inline progress logic |
| `src/stream_of_worship/app/screens/app.tcss` | Modify | Clean up duplicate #progress_bar rules |

## Key Code to Reuse

| Code | Location | Purpose |
|------|----------|---------|
| `PlaybackService.set_callbacks()` | `app/services/playback.py:88-103` | Register position/state callbacks |
| `PlaybackPosition` dataclass | `app/services/playback.py:30-42` | Position info passed to callbacks |
| `_format_time()` logic | `app/screens/lyrics_preview.py:327-330` | Time formatting (MM:SS) |
| Unicode bar rendering | `app/screens/lyrics_preview.py:318-322` | `█░` progress bar |
| `call_after_refresh()` pattern | `app/screens/lyrics_preview.py:272-284` | Thread-safe UI updates |

## Verification

### Manual Testing

1. Run the TUI app: `uv run --extra app sow-app run`
2. Navigate to BrowseScreen
3. Verify:
   - [ ] Select a song and press Space — audio starts playing
   - [ ] PlaybackBar appears showing position and progress
   - [ ] Progress bar updates in real-time during playback
   - [ ] Press Left arrow — skips backward 10s, bar updates, NO notification
   - [ ] Press Right arrow — skips forward 10s, bar updates, NO notification
   - [ ] Rapidly press Left/Right — bar updates smoothly, no notification spam
   - [ ] Press Space to stop — PlaybackBar hides
   - [ ] Press Space to start again — PlaybackBar reappears

4. Navigate to SongsetEditorScreen
5. Verify:
   - [ ] Same playback bar behavior as BrowseScreen
   - [ ] Skip actions work without notification spam
   - [ ] PlaybackBar persists when navigating between songs in the list

6. Navigate to LyricsPreviewScreen (Shift+P from SongsetEditor)
7. Verify:
   - [ ] PlaybackBar appears at bottom
   - [ ] Lyrics sync still works correctly
   - [ ] Skip actions work
   - [ ] No duplicate progress bars

### Edge Cases to Test

- [ ] Song with 0:00 duration — bar shows 0:00 / 0:00
- [ ] Very long songs (>60 minutes) — time format handles correctly
- [ ] Rapid screen switching — callbacks don't cause errors
- [ ] Playback finishes naturally — bar hides correctly
- [ ] Pause/resume — icon changes between ▶ and ⏸

### Automated Tests

Create `tests/app/widgets/test_playback_bar.py`:
- Test `_format_time()` with various inputs
- Test visibility toggling based on playback state
- Test callback registration/unregistration
- Test progress bar calculation

## Future Enhancements (Out of Scope)

- Clickable progress bar for seeking
- Display current song title in the bar
- Volume indicator
- Waveform visualization
- Configurable bar width via settings
