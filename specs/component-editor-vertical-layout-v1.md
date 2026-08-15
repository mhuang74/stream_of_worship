# Component Editor Vertical Layout v1

## Summary

- Change the Admin CLI Component Metadata Editor TUI from a T-shaped 3-panel layout to a vertically split (|) 2-panel layout
- **Left panel (40%)**: expanded Hero Panel (with Song Info row) + compact read-only Data Table
- **Right panel (60%)**: toggleable between Lyrics (LRC) view and Details (editable) view; can be dismissed entirely to give left panel full width
- Remove the current `#bottom-split` horizontal split and the always-visible Detail Panel
- Editing interactions stay in the Details panel (now toggleable in the right panel), preserving `get_editable_field_line_offset()` positioning logic
- Single cycle key (`v`) toggles right panel state: hidden → lyrics → details → hidden
- Tab cycles focus between left and right panels (no-op when right is dismissed)

## Current vs Proposed Layout

### Current (v5) — T-Shaped 3-Panel

```
┌─────────────────────────────────────────────────┐
│ Header                                           │
│ SongBreadcrumb                                    │
│ PlaybackBar                                       │
├─────────────────────────────────────────────────┤
│ #top-panel (full-width):                          │
│   ComponentHeroPanel (summary + reasoning)        │
│   ComponentMetadataTable (9-col, read-only)       │
├──────────────────────┬──────────────────────────┤
│ #bottom-split (50/50):                            │
│   LyricsPanel        │  ComponentDetailPanel    │
│   (LRC lyrics)        │  (all metadata +          │
│                       │   editable fields)         │
├──────────────────────┴──────────────────────────┤
│ Input(#row-edit-input, hidden overlay)             │
│ StatusIndicator                                    │
│ GroupedFooter                                      │
└─────────────────────────────────────────────────┘
```

### Proposed (v6) — Vertically Split 2-Panel

```
┌─────────────────────────────────────────────────┐
│ Header (full-width)                               │
│ SongBreadcrumb (full-width)                       │
│ PlaybackBar (full-width)                          │
├──────────────────────┬──────────────────────────┤
│ Left Panel (40%)     │ Right Panel (60%)        │
│                      │ (toggleable/dismissable)  │
│ ComponentHeroPanel   │  ┌─ Lyrics view ──────┐  │
│   Song Info row (NEW) │  │ LRC timestamped     │  │
│   Primary metrics     │  │ lyrics with playback│  │
│   Theme/Posture       │  │ synced highlight   │  │
│   Reasoning          │  └─────────────────────┘  │
│                      │  ┌─ Details view ─────┐  │
│ ComponentMetadata    │  │ Song Info           │  │
│   Table (9-col       │  │ Component details   │  │
│   read-only)          │  │ Confidence breakdown│  │
│                      │  │ Editable fields ◄──┤  │
│                      │  │ Reasoning           │  │
│                      │  │ Lifecycle            │  │
│                      │  └─────────────────────┘  │
│                      │  ┌─ Dismissed ────────┐  │
│                      │  │ (empty, left panel  │  │
│                      │  │  expands full width)│  │
│                      │  └─────────────────────┘  │
├──────────────────────┴──────────────────────────┤
│ Input(#row-edit-input, hidden overlay)             │
│ StatusIndicator (full-width)                      │
│ GroupedFooter (full-width)                        │
└─────────────────────────────────────────────────┘
```

**Right panel states (cycle: `v` key):**

```
hidden ──(v)──▶ lyrics ──(v)──▶ details ──(v)──▶ hidden
```

- **hidden**: Right panel not displayed; left panel takes 100% width
- **lyrics**: Right panel shows `LyricsPanel` (LRC with playback highlight)
- **details**: Right panel shows `ComponentDetailPanel` (full metadata + editable fields)

## Key Decisions (from Interview)

| Question | Decision |
|----------|----------|
| Horizontal bars placement | Keep Breadcrumb/PlaybackBar above split, StatusIndicator/Footer below — all full-width |
| Editing target | Stays in Details panel, which is now toggleable in the right panel |
| Split ratio | 40/60 (left 40%, right 60% — wider right for lyrics/details) |
| Detail Panel data | Right panel shows either Lyrics or Details; can be dismissed; scrollable |
| Toggle mechanism | Single cycle key (`v`): hidden → lyrics → details → hidden |
| Default state on launch | Lyrics view |
| Hero Panel | Expand with Song Info row (title/artist/album/series/musical_key) |
| Focus cycling | Tab toggles left↔right (2 panels); no-op when right panel is dismissed |
| Visual separation | Divider line (border) + focus highlight (subtle style on focused panel) |

## Affected Files

| File | Lines | Changes |
|------|-------|---------|
| `ops/admin-cli/src/stream_of_worship/admin/component_editor/screen.py` | 1393 | Major: rewrite `DEFAULT_CSS`, `compose()`, panel cycling logic, bindings |
| `ops/admin-cli/src/stream_of_worship/admin/component_editor/detail_panel.py` | 203 | Minor: CSS border adjustments for right-panel placement |
| `ops/admin-cli/src/stream_of_worship/admin/component_editor/lyrics_panel.py` | 142 | Minor: CSS border adjustments for right-panel placement |
| `ops/admin-cli/src/stream_of_worship/admin/component_editor/constants.py` | 153 | Minor: add `HERO_SONG_INFO_FIELDS` constant for expanded hero |
| `ops/admin-cli/tests/admin/component_editor/test_screen.py` | 761 | Major: update layout assertions, panel cycling tests, add right-panel toggle tests |
| `ops/admin-cli/tests/admin/component_editor/test_hero_panel.py` | 237 | Minor: add Song Info row assertions |

## Implementation Plan

### Phase 1: Expand Hero Panel with Song Info Row

**Location:** `screen.py:171-249` (`ComponentHeroPanel.render_panel`), `constants.py:100-126`

Add a new row to the HeroPanel between the header line (row 1) and the primary metrics line (row 2):

```
Row 1: ▶ ENTRY CHORUS  —  Occurrence 1  —  [00:23 → 02:15]
Row 2 (NEW): Title: <title>  |  Artist: <artist>  |  Album: <album>  |  Series: <series>  |  Key: <musical_key>
Row 3: BPM 96    Key G    Energy -12.0 dB    Groove 0.80    Backbeat 0.42
Row 4: Theme: 敬拜    Vocal posture: To God
Row 5: Theme reasoning: <full text>
Row 6: Posture reasoning: <full text>
```

**Changes:**
1. Add `HERO_SONG_INFO_FIELDS` constant to `constants.py`:
   ```python
   HERO_SONG_INFO_FIELDS: tuple[tuple[str, str], ...] = (
       ("song_title", "Title"),
       ("composer", "Artist"),
       ("album_name", "Album"),
       ("album_series", "Series"),
       ("musical_key", "Key"),
   )
   ```
2. In `ComponentHeroPanel.render_panel()`, insert a new section after the header row that reads song-level info from `session.song` (the `Song` model object already available via `state.current.song`).
3. Update tests in `test_hero_panel.py` to assert Song Info rendering.

### Phase 2: Restructure CSS and Compose Method

**Location:** `screen.py:328-374` (`DEFAULT_CSS`), `screen.py:443-467` (`compose()`)

Replace the T-layout CSS and compose with a vertical split:

**New CSS:**
```css
ComponentEditorScreen {
    layout: vertical;
}

#editor-body {
    height: 1fr;
    overflow: hidden;
}

/* Left panel: Hero Panel (auto height) + compact table (fill remainder) */
#left-panel {
    width: 40%;
    overflow: hidden;
    border-right: solid $primary;
}
#left-panel:focus-within {
    border-right: double $accent;
}

#left-panel ComponentHeroPanel {
    height: auto;
    max-height: 24;
    border: round $accent;
    padding: 0 1;
    margin: 0;
}

#left-panel #component-table {
    height: 1fr;
}

#right-panel {
    width: 60%;
    overflow: hidden;
}
#right-panel:focus-within {
    border-left: double $accent;
}

/* Right panel internal views */
#right-panel LyricsPanel {
    height: 1fr;
    overflow-y: auto;
    background: $surface;
    padding: 0 1;
}
#right-panel ComponentDetailPanel {
    height: 1fr;
    overflow-y: auto;
    background: $surface;
    padding: 0 1;
}

/* When right panel is dismissed, left panel takes full width */
#left-panel.dismissed-right {
    width: 100%;
    border-right: none;
}

#row-edit-input {
    display: none;
    height: 1;
    layer: overlay;
}
```

**New `compose()` method:**
```python
def compose(self) -> ComposeResult:
    yield Header()
    with Vertical(id="editor-body"):
        yield SongBreadcrumb()
        yield PlaybackBar()

        with Horizontal(id="main-split"):
            with Vertical(id="left-panel"):
                yield ComponentHeroPanel()
                yield ComponentMetadataTable(id="component-table")
            with Vertical(id="right-panel"):
                yield LyricsPanel(id="lyrics-panel")
                yield ComponentDetailPanel(id="detail-panel")

        yield Input(
            id="row-edit-input",
            placeholder="Edit numeric value",
            select_on_focus=False,
            compact=True,
        )
        yield StatusIndicator()
    yield GroupedFooter()
```

**Key CSS logic:**
- Left and right panels wrapped in `Horizontal(id="main-split")`
- Both LyricsPanel and ComponentDetailPanel are yielded inside `#right-panel` but only one is `display: block` at a time based on `_right_panel_mode`
- When right panel is dismissed: `#left-panel` gets `.dismissed-right` class (width 100%), `#right-panel` gets `display: none`

### Phase 3: Right Panel State Management

**Location:** `screen.py` — new instance variables and methods

Add state tracking:

```python
# Right panel modes
_RIGHT_PANEL_MODES = ("hidden", "lyrics", "details")

def __init__(self, ...):
    ...
    self._right_panel_mode: str = "lyrics"  # default on launch
    self._active_panel: str = "left"  # "left" | "right"
```

**Toggle method:**
```python
def _cycle_right_panel(self) -> None:
    """Cycle: hidden → lyrics → details → hidden."""
    idx = self._RIGHT_PANEL_MODES.index(self._right_panel_mode)
    self._right_panel_mode = self._RIGHT_PANEL_MODES[(idx + 1) % len(self._RIGHT_PANEL_MODES)]
    self._apply_right_panel_mode()

def _apply_right_panel_mode(self) -> None:
    """Update CSS classes and widget visibility based on _right_panel_mode."""
    left_panel = self.query_one("#left-panel")
    right_panel = self.query_one("#right-panel")
    lyrics = self.query_one("#lyrics-panel", LyricsPanel)
    details = self.query_one("#detail-panel", ComponentDetailPanel)

    if self._right_panel_mode == "hidden":
        left_panel.add_class("dismissed-right")
        right_panel.display = False
        lyrics.display = False
        details.display = False
        self._active_panel = "left"
        self.query_one("#component-table", ComponentMetadataTable).focus()
    elif self._right_panel_mode == "lyrics":
        left_panel.remove_class("dismissed-right")
        right_panel.display = True
        lyrics.display = True
        details.display = False
        self._refresh_lyrics_panel()
    elif self._right_panel_mode == "details":
        left_panel.remove_class("dismissed-right")
        right_panel.display = True
        lyrics.display = False
        details.display = True
        self._refresh_detail_panel()
```

### Phase 4: Update Bindings and Focus Cycling

**Location:** `screen.py:376-420` (`BINDINGS`, `BINDING_GROUPS`)

**Binding changes:**

| Action | Key | v5 (current) | v6 (proposed) |
|--------|-----|-------------|---------------|
| Cycle right panel | `v` | — | NEW: cycles hidden → lyrics → details → hidden |
| Panel focus next | `tab` | cycle: top → lyrics → details | toggle: left ↔ right (no-op if right dismissed) |
| Panel focus prev | `shift+tab` | cycle: top → lyrics → details | toggle: left ↔ right (no-op if right dismissed) |
| Field focus up | `up` | only when details focused | only when right panel in details mode AND focused |
| Field focus down | `down` | only when details focused | only when right panel in details mode AND focused |
| Edit numeric | `e` | only when details focused | only when right panel in details mode AND focused |
| Cycle field prev | `bracketleft` | only when details focused | only when right panel in details mode AND focused |
| Cycle field next | `bracketright` | only when details focused | only when right panel in details mode AND focused |

**New `BINDINGS`:**
```python
BINDINGS: ClassVar[list[Binding]] = [
    # Playback / Nav (global)
    Binding("space", "toggle_playback_for_component", "Play/Pause"),
    Binding("left", "seek_backward", "Seek -5s"),
    Binding("right", "seek_forward", "Seek +5s"),
    Binding("j", "jump_to_component", "Jump"),
    # Song switch (global)
    Binding("n", "next_song", "Next Song"),
    Binding("p", "prev_song", "Prev Song"),
    # Right panel cycle (NEW)
    Binding("v", "cycle_right_panel", "View"),
    # Panel navigation
    Binding("tab", "cycle_panel_next", "Panel →"),
    Binding("shift+tab", "cycle_panel_prev", "Panel ←"),
    # Edit (only when right panel in details mode AND focused)
    Binding("bracketleft", "cycle_field_prev", "Cycle −"),
    Binding("bracketright", "cycle_field_next", "Cycle +"),
    Binding("e", "edit_numeric", "Edit Num"),
    # Detail panel field navigation (only details mode + focused)
    Binding("up", "detail_focus_up", "Field ↑"),
    Binding("down", "detail_focus_down", "Field ↓"),
    # General (global)
    Binding("s", "save", "Save"),
    Binding("ctrl+z", "undo", "Undo"),
    Binding("ctrl+y", "redo", "Redo"),
    Binding("escape", "quit_editor", "Quit"),
    Binding("q", "quit_editor", "Quit"),
    Binding("?", "show_keymap", "Keymap"),
]
```

**Updated `BINDING_GROUPS`:**
```python
BINDING_GROUPS: ClassVar[dict[str, list[str]]] = {
    "Playback": [
        "toggle_playback_for_component",
        "seek_backward",
        "seek_forward",
        "jump_to_component",
    ],
    "Songs": ["next_song", "prev_song"],
    "Panels": [
        "cycle_right_panel",
        "cycle_panel_next",
        "cycle_panel_prev",
        "detail_focus_up",
        "detail_focus_down",
    ],
    "Edit": ["cycle_field_prev", "cycle_field_next", "edit_numeric"],
    "General": ["save", "undo", "redo", "quit_editor", "show_keymap"],
}
```

**Focus cycling rewrite:**
```python
_PANELS = ("left", "right")

def action_cycle_panel_next(self) -> None:
    if self._guard_active_edit():
        return
    if self._right_panel_mode == "hidden":
        return  # no-op when right panel dismissed
    idx = self._PANELS.index(self._active_panel)
    self._active_panel = self._PANELS[(idx + 1) % len(self._PANELS)]
    self._focus_active_panel()

def action_cycle_panel_prev(self) -> None:
    if self._guard_active_edit():
        return
    if self._right_panel_mode == "hidden":
        return  # no-op when right panel dismissed
    idx = self._PANELS.index(self._active_panel)
    self._active_panel = self._PANELS[(idx - 1) % len(self._PANELS)]
    self._focus_active_panel()

def _focus_active_panel(self) -> None:
    if self._active_panel == "left":
        self.query_one("#component-table", ComponentMetadataTable).focus()
    elif self._active_panel == "right":
        if self._right_panel_mode == "lyrics":
            self.query_one("#lyrics-panel", LyricsPanel).focus()
        elif self._right_panel_mode == "details":
            self.query_one("#detail-panel", ComponentDetailPanel).focus()
            self._refresh_detail_panel()
```

**Edit action guards:**

All edit actions (`action_cycle_field_prev`, `action_cycle_field_next`, `action_edit_numeric`, `action_detail_focus_up`, `action_detail_focus_down`) need their guard updated from:
```python
if self._active_panel != "details":
    return
```
to:
```python
if self._active_panel != "right" or self._right_panel_mode != "details":
    return
```

### Phase 5: Panel CSS Adjustments for Detail and Lyrics Panels

**Location:** `detail_panel.py:31-42`, `lyrics_panel.py:28-42`

**`ComponentDetailPanel.DEFAULT_CSS` (updated):**
```css
ComponentDetailPanel {
    height: 1fr;
    padding: 0 1;
    overflow-y: auto;
    background: $surface;
}
ComponentDetailPanel:focus {
    border-left: double $accent;
}
```
Remove the `border-left: solid $primary` (now handled by parent `#right-panel` container).

**`LyricsPanel.DEFAULT_CSS` (updated):**
```css
LyricsPanel {
    height: 1fr;
    padding: 0 1;
    overflow-y: auto;
    background: $surface;
}
LyricsPanel:focus {
    border-left: double $accent;
}
LyricsPanel.empty {
    color: $text-muted;
    text-align: center;
}
```
Change `border-right` to `border-left` since the panel is now on the right side of the screen.

### Phase 6: Update Refresh Methods and State Sync

**Location:** `screen.py:595-633` (`_refresh_detail_panel`, `_refresh_lyrics_panel`)

Update all callers to check right panel mode:

```python
def _refresh_detail_panel(self) -> None:
    if self._right_panel_mode != "details":
        return  # skip if not in details mode
    try:
        panel = self.query_one("#detail-panel", ComponentDetailPanel)
    except NoMatches:
        return
    panel.update_detail(self.state)

def _refresh_lyrics_panel(self) -> None:
    if self._right_panel_mode != "lyrics":
        return  # skip if not in lyrics mode
    try:
        panel = self.query_one("#lyrics-panel", LyricsPanel)
    except NoMatches:
        return
    ...  # existing logic unchanged
```

**Song switch (`_switch_song`):** Update to refresh based on right panel mode:
```python
def _switch_song(self, delta: int) -> None:
    ...
    if self._right_panel_mode == "lyrics":
        self._refresh_lyrics_panel()
    elif self._right_panel_mode == "details":
        self._refresh_detail_panel()
    self._refresh_hero()
    ...
```

**Autosave recovery (`_maybe_apply_autosave`):** Update similarly.

**`_update_lyrics_highlight`:** Skip if not in lyrics mode:
```python
def _update_lyrics_highlight(self) -> None:
    if self._right_panel_mode != "lyrics":
        return
    ...
```

### Phase 7: Input Overlay Positioning Update

**Location:** `screen.py:776-805` (`_show_value_edit_input`), `screen.py:851-872` (`on_resize`)

The Input overlay positioning logic uses `detail_panel.region` and `detail_panel.get_editable_field_line_offset()`. Since the Details panel moves from bottom-right to right-panel (wider area), the `get_editable_field_line_offset()` method in `detail_panel.py:185-203` stays unchanged — it computes line offsets based on the rendered text, which doesn't change.

The only change is that the panel region will be wider (60% instead of 50%), but the x/width calculations already use `panel_region.x` and `panel_region.width` dynamically, so they adapt automatically.

**Guard update:** Add mode check:
```python
def _show_value_edit_input(self, role: str, field: str, initial_text: str) -> None:
    if self._right_panel_mode != "details":
        return
    ...
```

### Phase 8: Remove Obsolete Code

**Location:** `screen.py`

Remove/clean up:
1. `#top-panel` container — no longer exists
2. `#bottom-split` container — no longer exists
3. `_PANEL_ORDER = ("top", "lyrics", "details")` — replaced by `_PANELS = ("left", "right")`
4. `_active_panel` initialization: `"top"` → `"left"`
5. All references to `_active_panel == "details"` → `_active_panel == "right" and _right_panel_mode == "details"`
6. All references to `_active_panel == "lyrics"` → `_active_panel == "right" and _right_panel_mode == "lyrics"`
7. All references to `_active_panel == "top"` → `_active_panel == "left"`

### Phase 9: Test Updates

**Location:** `ops/admin-cli/tests/admin/component_editor/test_screen.py`

**Tests to update:**

1. **`test_launches_and_shows_table`**: Update to check table exists in `#left-panel` instead of `#top-panel`

2. **`test_cycle_theme_next_changes_value`**: Update setup:
   ```python
   app.screen._active_panel = "right"
   app.screen._right_panel_mode = "details"
   ```

3. **`test_cycle_theme_prev_changes_value`**: Same as above

4. **`test_cycle_ignored_on_non_enum_cell`**: Same mode setup

5. **`test_edit_numeric_opens_overlay`**: Same mode setup

6. **`test_edit_numeric_ignored_on_non_numeric_cell`**: Same mode setup

7. **`test_d2_hero_refreshes_on_cursor_move`**: No change needed (hero in left panel)

8. **`test_d2_hero_shows_edited_theme`**: Update panel mode setup

9. **`test_d2_hero_updates_on_arrow_key_navigation`**: No change needed

**New tests to add:**

1. **`test_right_panel_default_is_lyrics`**: Assert `_right_panel_mode == "lyrics"` on mount

2. **`test_cycle_right_panel_hidden_to_lyrics_to_details`**: Press `v` three times, verify mode transitions

3. **`test_right_panel_hidden_left_full_width`**: Cycle to hidden, verify `#left-panel` has `.dismissed-right` class and `#right-panel` display is False

4. **`test_tab_noop_when_right_dismissed`**: Cycle to hidden, press Tab, verify `_active_panel` stays "left"

5. **`test_edit_ignored_when_not_in_details_mode`**: Try `e` / `[` / `]` when right panel is in lyrics mode, verify no state changes

6. **`test_hero_shows_song_info`**: Verify Song Info row (title/artist/album) appears in hero panel content

**Location:** `ops/admin-cli/tests/admin/component_editor/test_hero_panel.py`

Add tests:
1. **`test_hero_contains_song_title`**: Assert song title appears in hero panel text
2. **`test_hero_contains_artist`**: Assert artist/composer appears
3. **`test_hero_contains_album`**: Assert album name appears

## Verification

After implementation, run:

```bash
# Admin CLI tests
uv run --project ops/admin-cli --python 3.11 --extra admin --extra test pytest tests/admin/component_editor/ -v

# Lint + format
uv run --project ops/admin-cli ruff check src/stream_of_worship/admin/component_editor/
uv run --project ops/admin-cli ruff format --check src/stream_of_worship/admin/component_editor/

# Manual smoke test
uv run --project ops/admin-cli --extra admin sow-admin audio components review <song_id> --entry-hash <hash> --exit-hash <hash>
```

## Backward Compatibility

- **Autosave**: `ComponentAutosaveState` schema unchanged — no migration needed
- **DB/R2 save logic**: Unchanged — all save/undo/redo paths work identically
- **Keyboard shortcuts**: Existing playback, song switch, save, undo/redo, quit bindings unchanged
- **New binding**: Only `v` (cycle right panel) is new — does not conflict with any existing binding
