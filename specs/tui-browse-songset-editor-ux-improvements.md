# TUI Browse & Songset Editor UX Improvements

## Overview

This plan addresses UX improvements for the Browse Song screen and Songset Editor screen, plus a crash bug fix.

## Changes

### 1. Browse Screen: Change 's' → 'a' for "Add to Songset"

**File:** `src/stream_of_worship/app/screens/browse.py`

**Location:** Line 27

**Change:**
```python
# Before
("s", "add_to_songset", "Add to Songset"),

# After
("a", "add_to_songset", "Add to Songset"),
```

**Rationale:** 'a' for "add" is more intuitive. Frees up 's' for future use.

---

### 2. Browse Screen: Auto-focus table on mount

**File:** `src/stream_of_worship/app/screens/browse.py`

**Location:** `on_mount()` method (lines 131-138)

**Change:** After `_load_songs()`, add a `call_after_refresh` call to focus the song table and set cursor to first row.

```python
def on_mount(self) -> None:
    """Handle mount event."""
    self._load_songs()
    self.app.playback.set_callbacks(
        on_position_changed=self._on_position_changed,
        on_state_changed=self._on_state_changed,
        on_finished=self._on_finished,
    )
    # Auto-focus table so user can immediately use arrow keys and 'a' to add
    self.call_after_refresh(self._focus_song_table)

def _focus_song_table(self) -> None:
    """Focus the song table and set cursor to first row."""
    table = self.query_one("#song_table", DataTable)
    table.focus()
    if len(table.rows) > 0:
        table.cursor_row = 0
```

**Rationale:** User can immediately navigate with arrow keys and press 'a' to add songs without needing to tab to the table first.

---

### 3. Songset Editor: Rename "Add Songs" → "Song Catalog" with 'c' shortcut

**File:** `src/stream_of_worship/app/screens/songset_editor.py`

**Locations:** Lines 33 and 98

**Changes:**
```python
# Line 33 - Before
("a", "add_songs", "Add Songs"),

# Line 33 - After
("c", "add_songs", "Song Catalog"),

# Line 98 - Before
yield Button("Add Songs", id="btn_add", variant="primary")

# Line 98 - After
yield Button("Song Catalog", id="btn_add", variant="primary")
```

**Rationale:** "Song Catalog" better describes the action of browsing the catalog. 'c' for "catalog" is intuitive. Frees up 'a' for Browse screen's "add" action.

---

### 4. Songset Editor: Auto-open Browse for new (empty) songsets

**File:** `src/stream_of_worship/app/screens/songset_editor.py`

**Changes:**

1. Add `self._initial_load = True` flag in `__init__` (around line 77)

2. Modify `_load_items()` to auto-navigate to Browse for empty songsets on first load:

```python
def __init__(self, ...):
    ...
    self.items: list[SongsetItem] = []
    self._initial_load = True  # NEW

def _load_items(self) -> None:
    """Load and display songset items."""
    if not self.state.selected_songset:
        return

    self.items = self.songset_client.get_items(self.state.selected_songset.id)
    self.state.update_songset_items(self.items)

    table = self.query_one("#items_table", DataTable)
    table.clear()

    for i, item in enumerate(self.items):
        # ... existing row rendering ...

    # NEW: Auto-open Browse for empty songsets on first load
    if len(self.items) == 0 and self._initial_load:
        self._initial_load = False
        self.call_after_refresh(self._open_browse_for_new_songset)

def _open_browse_for_new_songset(self) -> None:
    """Navigate to Browse screen for new empty songsets."""
    self.app.navigate_to(AppScreen.BROWSE)
```

**Rationale:** When user creates a new songset, they immediately want to add songs. Auto-opening Browse saves a step.

**Guard:** `_initial_load` flag prevents re-triggering when returning from Browse with still-empty songset (user might have browsed but not added anything).

---

### 5. Fix "Unknown" bug in Song column

**File:** `src/stream_of_worship/app/screens/songset_editor.py`

**Root Cause:** `_load_items()` calls `self.songset_client.get_items()` which uses a query that only selects from `songset_items` table without joining `songs`/`recordings`. The `SongsetItem.song_title` field is always `None`, so `item.song_title or "Unknown"` always shows "Unknown".

**Solution:** Use `CatalogService.get_songset_with_items()` which performs a two-step cross-DB lookup to resolve song and recording details. Then populate the raw `SongsetItem` objects with resolved data so existing code continues to work.

**Changes:**

1. Modify `_load_items()` to use `catalog.get_songset_with_items()`:

```python
def _load_items(self) -> None:
    """Load and display songset items."""
    if not self.state.selected_songset:
        return

    # Use catalog service to resolve song/recording details
    details, orphan_count = self.catalog.get_songset_with_items(
        self.state.selected_songset.id, self.songset_client
    )

    # Extract raw items and populate with resolved data
    self.items = [d.item for d in details]
    for detail in details:
        # Populate joined fields on raw item so existing code works
        detail.item.song_title = detail.display_title
        if detail.recording:
            detail.item.tempo_bpm = detail.recording.tempo_bpm
            detail.item.duration_seconds = detail.recording.duration_seconds
            detail.item.recording_key = detail.recording.musical_key
        if detail.song:
            detail.item.song_key = detail.song.musical_key

    self.state.update_songset_items(self.items)

    table = self.query_one("#items_table", DataTable)
    table.clear()

    for i, item in enumerate(self.items):
        gap_text = f"{item.gap_beats} beats" if item.gap_beats else "No gap"
        transition_text = "Crossfade" if item.crossfade_enabled else "Gap"
        tempo_text = f"{int(item.tempo_bpm)}" if item.tempo_bpm else "-"

        table.add_row(
            str(i + 1),
            item.song_title or "Unknown",  # Now populated!
            item.display_key or "-",
            tempo_text,
            item.formatted_duration,
            gap_text,
            transition_text,
            key=item.id,
        )

    # Auto-open Browse for empty songsets on first load
    if len(self.items) == 0 and self._initial_load:
        self._initial_load = False
        self.call_after_refresh(self._open_browse_for_new_songset)
```

**Rationale:** By populating `song_title`, `tempo_bpm`, `duration_seconds`, `recording_key`, and `song_key` on the raw `SongsetItem` objects, all existing code that references `self.items` (notification messages, `_get_selected_item()`, etc.) continues to work without modification.

---

### 6. Fix crash on returning from Browse Screen (PlaybackBar race condition)

**File:** `src/stream_of_worship/app/screens/browse.py` and `src/stream_of_worship/app/screens/songset_editor.py`

**Root Cause:** When a screen is popped (navigated back), the playback service may fire a callback that tries to `query_one(PlaybackBar)` on a screen whose DOM widgets are already torn down. The `call_after_refresh` defers the update, but by then the widget is gone, causing `NoMatches` exception.

**Solution:** Wrap each `_update()` inner function with a try/except for `NoMatches`, so the callback silently no-ops if the widget is gone.

**Changes in `browse.py` (lines 144-170):**

```python
from textual.widgets import NoMatches  # Add to imports

def _on_position_changed(self, position: PlaybackPosition) -> None:
    """Handle position updates from playback service."""

    def _update():
        try:
            self.query_one(PlaybackBar).update_display(position)
        except NoMatches:
            pass  # Widget already removed from DOM

    self.call_after_refresh(_update)

def _on_state_changed(self, state: PlaybackState) -> None:
    """Handle state changes from playback service."""

    def _update():
        try:
            self.query_one(PlaybackBar).update_visibility()
            if state != PlaybackState.STOPPED:
                self.query_one(PlaybackBar).update_display(
                    self.app.playback.get_position()
                )
        except NoMatches:
            pass  # Widget already removed from DOM

    self.call_after_refresh(_update)

def _on_finished(self) -> None:
    """Handle playback finished."""

    def _update():
        try:
            self.query_one(PlaybackBar).update_visibility()
        except NoMatches:
            pass  # Widget already removed from DOM

    self.call_after_refresh(_update)
```

**Same changes in `songset_editor.py` (lines 134-158):**

Apply identical try/except pattern to all three callback methods.

**Rationale:** Gracefully handles the race condition where playback callbacks fire after screen DOM is torn down. The callback simply no-ops if the widget is gone.

---

## Summary of Files Changed

| File | Changes |
|------|---------|
| `src/stream_of_worship/app/screens/browse.py` | 1. 's' → 'a' binding<br>2. Auto-focus table on mount<br>3. PlaybackBar crash fix |
| `src/stream_of_worship/app/screens/songset_editor.py` | 1. "Add Songs" → "Song Catalog" with 'c'<br>2. Auto-open Browse for empty songsets<br>3. Fix "Unknown" bug via catalog service<br>4. PlaybackBar crash fix |

---

## Testing Checklist

- [ ] Browse screen: Press 'a' adds selected song to songset
- [ ] Browse screen: On entering, cursor is on first row in table
- [ ] Browse screen: Arrow keys work immediately without tabbing
- [ ] Songset Editor: Press 'c' opens Browse screen
- [ ] Songset Editor: "Song Catalog" button opens Browse screen
- [ ] Songset Editor: New songset auto-opens Browse screen
- [ ] Songset Editor: Song column shows actual song titles (not "Unknown")
- [ ] Songset Editor: Tempo column shows actual BPM (not "-")
- [ ] Songset Editor: Key column shows actual key (not "?")
- [ ] No crash when returning from Browse screen during playback
- [ ] No crash when returning from Songset Editor during playback
