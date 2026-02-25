# Lyrics Preview Feature Implementation Plan

## Context

**Problem:** Users need to preview lyrics timing accuracy before generating video files. Currently, the only way to verify LRC timecode accuracy is to generate the full video MP4, which is time-consuming.

**Solution:** Add a new "Lyrics Preview" screen accessible via Shift+P from the SongsetEditorScreen. This screen will display synchronized lyrics alongside audio playback, with a debug panel showing all LRC timestamps for quick verification.

## Requirements Summary

- **Trigger:** Shift+P from SongsetEditorScreen (when a song is highlighted)
- **Layout:** Left/Right split screen
  - **Left (wider):** Large lyrics preview with current/next line + progress bar
  - **Right:** Song metadata (title, key, tempo, album) + full LRC with timestamps, auto-scrolling to current line
- **Controls:** Space (play/pause), Left/Right arrows (seek ±10s), Escape (exit)
- **Audio:** Plays synchronized with lyrics display

## Implementation Plan

### Phase 1: Create LyricsPreviewScreen

**File:** `src/stream_of_worship/app/screens/lyrics_preview.py` (new)

Create a new screen following the same pattern as `songset_editor.py`:

```python
class LyricsPreviewScreen(Screen):
    """Screen for previewing lyrics synchronized with audio playback."""

    BINDINGS = [
        ("space", "toggle_playback", "Play/Pause"),
        ("left", "skip_backward", "Skip -10s"),
        ("right", "skip_forward", "Skip +10s"),
        ("escape", "back", "Back"),
    ]

    def __init__(
        self,
        item: SongsetItem,
        playback: PlaybackService,
        asset_cache: AssetCache,
    ):
        super().__init__()
        self.item = item
        self.playback = playback
        self.asset_cache = asset_cache
        self.lrc_lines: list[LRCLine] = []
        self.current_line_index: int = -1
```

**Screen State:**
- `item: SongsetItem` - the song being previewed (contains `recording_hash_prefix`, `song_title`, `song_key`, `tempo_bpm`, `song_album_name`)
- `lrc_lines: list[LRCLine]` - parsed lyrics with timestamps
- `current_line_index: int` - tracks which line is currently playing

### Phase 2: Implement Screen Layout

**Layout structure using Textual containers:**

```
┌────────────────────────────────┬─────────────────────────┐
│                                │ ┌─────────────────────┐ │
│  [Current Lyric - Large Bold]  │ │  SONG METADATA      │ │
│                                │ │  Title: 奇異恩典     │ │
│  [Next Lyric - Dimmed]         │ │  Key: C   Tempo: 72 │ │
│                                │ │  Album: 讚美之泉     │ │
│                                │ └─────────────────────┘ │
│                                │ ┌─────────────────────┐ │
│  ───────────────────────────── │ │  LRC DEBUG          │ │
│  ▶ 01:23 / 04:56  [░░░▓▓▓]    │ │  00:00.00  Verse 1  │ │
│                                │ │  00:05.50  Line 2   │ │
│                                │ │▶ 00:10.20  Line 3   │ │
│                                │ │  00:15.80  Line 4   │ │
│                                │ └─────────────────────┘ │
└────────────────────────────────┴─────────────────────────┘
```

**compose() method:**
```python
def compose(self) -> ComposeResult:
    yield Header()

    with Horizontal():
        # Left panel (2/3 width) - Lyrics display
        with Vertical(id="lyrics_panel"):
            yield Static("", id="current_lyric", classes="lyric-current")
            yield Static("", id="next_lyric", classes="lyric-next")
            yield Static("", id="progress_bar")

        # Right panel (1/3 width) - Debug info
        with Vertical(id="debug_panel"):
            # Song metadata section
            with Vertical(id="metadata_section"):
                yield Static("", id="song_title")
                yield Static("", id="song_details")  # Key, Tempo
                yield Static("", id="song_album")

            # LRC debug table
            table = DataTable(id="lrc_table")
            table.add_columns("Time", "Lyrics")
            table.cursor_type = "row"
            yield table

    yield Footer()
```

### Phase 3: Parse LRC and Initialize

**on_mount() method:**
1. Download LRC file using `asset_cache.download_lrc(hash_prefix)`
2. Parse LRC content into `list[LRCLine]`
3. Populate metadata labels
4. Populate LRC debug table
5. Download audio file
6. Register playback callbacks

**LRC parsing (reuse existing logic):**
```python
@dataclass
class LRCLine:
    timestamp_seconds: float
    text: str

def _parse_lrc(self, content: str) -> list[LRCLine]:
    """Parse LRC content. Reuses pattern from video_engine.py."""
    lines = []
    pattern = re.compile(r'\[(\d{2}):(\d{2})\.(\d{2,3})\](.*)')
    for line in content.split('\n'):
        match = pattern.match(line.strip())
        if match:
            minutes = int(match.group(1))
            seconds = int(match.group(2))
            ms_str = match.group(3)
            # Handle both 2-digit (centiseconds) and 3-digit (milliseconds)
            ms = int(ms_str) * (10 if len(ms_str) == 2 else 1)
            timestamp = minutes * 60 + seconds + ms / 1000
            text = match.group(4).strip()
            if text:  # Skip empty lines
                lines.append(LRCLine(timestamp, text))
    return lines
```

### Phase 4: Implement Playback Sync

**Register callbacks in on_mount():**
```python
self.playback.set_callbacks(
    on_position_changed=self._on_position_changed,
    on_state_changed=self._on_state_changed,
    on_finished=self._on_finished,
)
```

**_on_position_changed(position: PlaybackPosition):**
1. Find current line index using binary search or linear scan
2. Update `#current_lyric` with current line text (large, bold)
3. Update `#next_lyric` with next line text (dimmed)
4. Update `#progress_bar` with position/duration
5. Highlight current row in `#lrc_table`
6. Auto-scroll `#lrc_table` to keep current line visible

**Find current line algorithm:**
```python
def _find_current_line(self, position_seconds: float) -> int:
    """Find the index of the lyric line for current position."""
    if not self.lrc_lines:
        return -1

    # Find last line where timestamp <= position
    for i in range(len(self.lrc_lines) - 1, -1, -1):
        if self.lrc_lines[i].timestamp_seconds <= position_seconds:
            return i
    return -1
```

### Phase 5: Implement Actions

**action_toggle_playback():**
```python
def action_toggle_playback(self) -> None:
    if self.playback.is_playing:
        self.playback.pause()
    elif self.playback.is_paused:
        self.playback.resume()
    else:
        audio_path = self.asset_cache.download_audio(self.item.recording_hash_prefix)
        if audio_path:
            self.playback.play(audio_path)
```

**action_skip_forward() / action_skip_backward():**
```python
def action_skip_forward(self) -> None:
    self.playback.skip_forward(10.0)

def action_skip_backward(self) -> None:
    self.playback.skip_backward(10.0)
```

**action_back():**
```python
def action_back(self) -> None:
    self.playback.stop()
    self.app.pop_screen()
```

### Phase 6: Wire Up from SongsetEditorScreen

**File:** `src/stream_of_worship/app/screens/songset_editor.py` (modify)

1. Add import:
```python
from stream_of_worship.app.screens.lyrics_preview import LyricsPreviewScreen
```

2. Add keybinding to BINDINGS list:
```python
("shift+p", "lyrics_preview", "Lyrics Preview"),
```

3. Implement action:
```python
def action_lyrics_preview(self) -> None:
    """Open lyrics preview for the selected song."""
    item = self._get_selected_item()
    if not item:
        self.notify("No song selected", severity="warning")
        return

    if not item.recording_hash_prefix:
        self.notify("Song has no recording", severity="warning")
        return

    # Check if LRC exists by attempting download
    lrc_path = self.asset_cache.download_lrc(item.recording_hash_prefix)
    if not lrc_path:
        self.notify("No lyrics available for this song", severity="warning")
        return

    self.app.push_screen(
        LyricsPreviewScreen(
            item=item,
            playback=self.playback,
            asset_cache=self.asset_cache,
        )
    )
```

### Phase 7: Add CSS Styling

**File:** `src/stream_of_worship/app/app.tcss` (modify or create)

```css
#lyrics_panel {
    width: 2fr;
    align: center middle;
    padding: 1;
}

#debug_panel {
    width: 1fr;
    border-left: solid $primary;
    padding: 1;
}

.lyric-current {
    text-align: center;
    text-style: bold;
    content-align: center middle;
}

.lyric-next {
    text-align: center;
    color: $text-muted;
    content-align: center middle;
}

#lrc_table {
    height: 1fr;
}

#metadata_section {
    height: auto;
    border-bottom: solid $primary;
    padding-bottom: 1;
    margin-bottom: 1;
}
```

## Files to Modify/Create

| File | Action | Purpose |
|------|--------|---------|
| `src/stream_of_worship/app/screens/lyrics_preview.py` | Create | New preview screen with all layout and logic |
| `src/stream_of_worship/app/screens/songset_editor.py` | Modify | Add Shift+P binding and action_lyrics_preview() |
| `src/stream_of_worship/app/screens/__init__.py` | Modify | Export LyricsPreviewScreen |
| `src/stream_of_worship/app/app.tcss` | Modify | Add CSS styles for lyrics preview |

## Key Code to Reuse

| Code | Location | Purpose |
|------|----------|---------|
| `_parse_lrc()` | `app/services/video_engine.py:380-400` | LRC parsing logic |
| `download_lrc()` | `app/services/asset_cache.py:195` | Download LRC from R2 |
| `download_audio()` | `app/services/asset_cache.py:160` | Download audio from R2 |
| `PlaybackService` | `app/services/playback.py` | Audio playback with callbacks |
| Screen pattern | `app/screens/songset_editor.py` | Constructor, compose, bindings pattern |

## Verification

### Manual Testing
1. Run the TUI app: `uv run python -m stream_of_worship.app.main`
2. Create or open a songset with songs that have LRC files
3. Select a song in the editor and press Shift+P
4. Verify:
   - [ ] Split screen layout appears (lyrics left, debug right)
   - [ ] Song metadata shows correctly (title, key, tempo, album)
   - [ ] Full LRC with timestamps visible in debug table
   - [ ] Press Space - audio starts playing
   - [ ] Current lyric updates in sync with audio (large text)
   - [ ] Next lyric shows dimmed below current
   - [ ] Progress bar updates with current position
   - [ ] Current line highlighted in debug table
   - [ ] Debug table auto-scrolls to keep current line visible
   - [ ] Left/Right arrows seek ±10 seconds
   - [ ] Lyrics update correctly after seeking
   - [ ] Escape returns to songset editor
   - [ ] Playback stops on exit

### Edge Cases to Test
- [ ] Song with no LRC - should show warning, not open preview
- [ ] Song with no recording - should show warning
- [ ] Empty LRC file - should show "No lyrics" message
- [ ] Very long lyrics lines - should wrap or truncate gracefully
- [ ] Rapid seeking - lyrics should stay in sync

### Automated Tests
Create `tests/app/screens/test_lyrics_preview.py`:
- Test `_find_current_line()` with various positions
- Test `_parse_lrc()` with valid/invalid LRC content
- Test screen initialization with mock services
