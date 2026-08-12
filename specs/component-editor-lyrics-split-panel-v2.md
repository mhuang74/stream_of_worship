# Implementation Plan: Component Editor — T-Layout 3-Panel Split (v2)

> **Date:** 2026-08-12
> **Branch:** TBD
> **Spec ID:** `component-editor-lyrics-split-panel-v2`
> **Status:** Planning — not yet implemented
> **Supersedes:** `component-editor-lyrics-split-panel-v1` (v2 replaces v1 entirely; the lyrics panel concept is absorbed into the bottom-left, the LRC fetch infrastructure is retained, and the layout is restructured to a 3-panel T-shape with a new component detail panel)

---

## Goal

Restructure the Admin CLI `audio review-components` TUI editor from a single
vertical stack into a **T-shaped 3-panel layout**:

1. **Top panel** (full-width horizontal bar): compact read-only DataTable showing
   only numerical columns from the component rows. Fixed mini-height (3–4 rows).
2. **Bottom-left panel**: timestamped lyrics (LRC) display — absorbed from v1.
3. **Bottom-right panel**: component detail view with all metadata + song-level
   info, formatted for easy reading. Dates shown at the bottom. **Editing happens
   here** — the top panel is read-only selection only.

Panel focus cycles via `Tab` / `Shift+Tab`.

## Non-Goals

- No playback auto-sync / auto-seek of the lyrics panel.
- No bi-directional click-to-seek from lyrics to audio.
- No editing of LRC content from the lyrics panel (read-only display).
- No toggle keybinding to hide panels — all 3 panels always visible.
- No changes to the `audio edit-lrc` editor (sibling TUI).
- No changes to the save flow (DB + R2), autosave, or undo/redo mechanics.
- No scroll-sync between top panel and detail panel on row selection.

## User Decisions (from clarification Q&A)

| Question | Decision |
|---|---|
| Top panel height | Fixed mini-height (3–4 rows). Shows as many components as fit; scrolls for the rest. |
| Top panel columns | Numerical only: occurrence_index, bpm, key, start_time, end_time, confidence, backbeat_strength, groove_density, energy_level. No dates, no long text, no enum fields. |
| Bottom-right content | All component metadata + song-level info (title, artist, album, etc.) |
| Editing model | Top panel is read-only. Edit in bottom-right detail panel. |
| Left/right split | 50/50 (equal width between lyrics and details) |
| Panel navigation | Tab / Shift+Tab cycles: top → bottom-left → bottom-right → top |
| v1 relationship | Supersede v1 entirely |
| Dates at bottom | Component lifecycle: created_at, updated_at |

### Note: Transition Params

The user selected "Transition params" (transition_in_bpm, transition_out_bpm,
crossfade_duration_sec) as a desired column category. These fields **do not exist**
on the `SongComponent` model (`db/models.py:537-656`). They are not part of the
27-column `SONG_COMPONENT_COLUMNS_SELECT` in `db/schema.py`. This category is
therefore not applicable — no columns are added for it. If transition-related
fields are added to the model in the future, they can be added to the top panel's
`COMPACT_TABLE_COLUMNS` tuple.

---

## Architecture Overview

### Current Layout (single vertical stack — v0/v1)

```
┌─────────────────────────────────────┐
│ Header                              │
├─────────────────────────────────────┤
│ Vertical(#editor-body)              │
│   SongBreadcrumb                    │
│   PlaybackBar                       │
│   ComponentMetadataTable (1fr)     │  ← 24 columns, 2 rows (entry+exit)
│   Input(#row-edit-input, hidden)   │  ← overlay for inline numeric edit
│   StatusIndicator                   │
├─────────────────────────────────────┤
│ GroupedFooter (docked bottom)       │
└─────────────────────────────────────┘
```

### Proposed Layout (T-shape, 3 panels — v2)

```
┌─────────────────────────────────────────────────┐
│ Header                                          │
├─────────────────────────────────────────────────┤
│ Vertical(#editor-body)                          │
│   SongBreadcrumb                                │
│   PlaybackBar                                   │
│   ┌─────────────────────────────────────────┐   │
│   │ #top-panel (height: 6 — fixed)         │   │  ← T's horizontal bar
│   │   ComponentMetadataTable                │   │     compact columns only
│   │   (read-only selection: entry/exit)     │   │     3-4 visible rows
│   └─────────────────────────────────────────┘   │
│   ┌──────────────────┬──────────────────────┐   │
│   │ #lyrics-panel    │ #detail-panel        │   │  ← T's vertical stem
│   │ LyricsPanel      │ ComponentDetailPanel │   │     50/50 split
│   │ (LRC display)    │ (all metadata +     │   │
│   │              1fr │  song info + dates)  │   │
│   │                  │                  1fr │   │
│   └──────────────────┴──────────────────────┘   │
│   ┌─────────────────────────────────────────┐   │
│   │ Input(#row-edit-input, hidden)          │   │  ← overlay, repositioned
│   ├─────────────────────────────────────────┤   │
│   StatusIndicator                               │
├─────────────────────────────────────────────────┤
│ GroupedFooter (docked bottom)                  │
└─────────────────────────────────────────────────┘
```

Key structural changes:
- `#editor-body` now contains: breadcrumb, playback bar, `#top-panel` (fixed
  height), `#bottom-split` (Horizontal container with 50/50 children), the
  hidden `Input` overlay, and `StatusIndicator`.
- The old monolithic `ComponentMetadataTable` with 24 columns is replaced by a
  compact table with 9 columns (numerical only).
- A new `ComponentDetailPanel` widget renders all component + song metadata in
  a formatted, sectioned layout with editable field navigation.
- The `LyricsPanel` from v1 spec is retained in the bottom-left.
- The `Input(#row-edit-input)` overlay remains in the compose tree but is
  repositioned to overlay the detail panel's focused field instead of the table
  cell.

---

## Phase 1: Constants — Compact Column Set

**Edit:** `ops/admin-cli/src/stream_of_worship/admin/component_editor/constants.py`

### New constant: `COMPACT_TABLE_COLUMNS`

```python
COMPACT_TABLE_COLUMNS: tuple[tuple[str, str], ...] = (
    # (field_key, header_label) — numerical columns only
    ("occurrence_index", "Occ"),
    ("bpm", "BPM"),
    ("key", "Key"),
    ("start_time", "Start"),
    ("end_time", "End"),
    ("confidence", "Conf"),
    ("backbeat_strength", "Backbeat"),
    ("groove_density", "Groove"),
    ("energy_level", "Energy"),
)
```

### Column selection rationale

Selected categories from the Q&A mapped to actual `SongComponent` fields:

| Category selected | Actual fields | In compact table? |
|---|---|---|
| Component index | `occurrence_index` | Yes — "Occ" |
| Core tempo/key | `bpm`, `key` | Yes — "BPM", "Key" |
| Structural numbers | `start_time`, `end_time` | Yes — "Start", "End" (formatted as MM:SS) |
| Energy/dynamics | `confidence`, `backbeat_strength`, `groove_density`, `energy_level` | Yes — "Conf", "Backbeat", "Groove", "Energy" |
| Transition params | (do not exist in model) | N/A — no columns to add |

Excluded from compact table:
- `role`, `component_type` — text (shown in detail panel instead)
- All `*_confidence` sub-fields — numerical but verbose (shown in detail panel)
- `theme_reasoning`, `posture_reasoning` — long text
- `created_at`, `updated_at` — dates
- `theme`, `vocal_posture` — enum text (editable, shown in detail panel)

### Existing constants — unchanged

`EDITABLE_FIELDS`, `THEME_VALUES`, `VOCAL_POSTURE_VALUES`, `COMPONENT_SCHEMA_VERSION`,
`GROOVE_DENSITY_MIN/MAX`, `ENERGY_LEVEL_MIN/MAX` — all unchanged.

`DATA_TABLE_COLUMNS` is kept (used for reference / detail panel field listing)
but no longer drives the top-panel table setup.

---

## Phase 2: LRC Fetch Service

**New file:** `ops/admin-cli/src/stream_of_worship/admin/component_editor/lrc_fetch.py`

Identical to v1 spec — absorbed unchanged.

### Data class

```python
@dataclass
class LRCFetch:
    song_id: str
    content: Optional[str]       # None if no LRC exists in R2
    cached_path: Optional[Path]  # local cache path written, if any
    error: Optional[str]         # error message if fetch failed
```

### Functions

```python
async def fetch_lrc_for_song(
    song_id: str,
    r2_client: R2Client,
    cache_dir: Path,
) -> LRCFetch:
    """Download LRC for a single song from R2, cache locally.

    - Resolve LRC identity via r2.get_lrc_identity(song_id)
    - If no LRC exists in R2 → return LRCFetch(content=None)
    - Download content via r2.download_lrc_content()
    - Write to {cache_dir}/{hash_prefix}/audio/lyrics.lrc
      (same directory as audio.mp3)
    - Return LRCFetch with parsed content
    """

async def prefetch_all_lrc(
    song_sessions: list[SongSession],
    r2_client: R2Client,
    cache_dir: Path,
) -> dict[str, LRCFetch]:
    """Parallel prefetch of LRC for all songs in the songset.

    Uses asyncio.gather to fetch all in parallel.
    Returns song_id -> LRCFetch map.
    Individual fetch failures do not abort the batch — each song's
    error is captured in its own LRCFetch.error.
    """
```

### Reuse

- `services/r2.py` → `get_lrc_identity()` (line 458), `download_lrc_content()` (line 486)
- `services/asset_cache.py` → `download_lrc()` (alternative simpler path, line 134)
- Local cache path mirrors audio cache layout:
  `{cache_dir}/{hash_prefix}/audio/lyrics.lrc`

### Pre-fetch vs on-song-switch (from v1)

1. **Pre-fetch all** — After TUI launch, background worker downloads LRC for
   every song in the songset in parallel. Results populate
   `state.lrc_fetches` / `state.lrc_parsed`.
2. **On song switch** — When the user switches songs (n/p keys), all three
   panels refresh. If the pre-fetch worker hasn't populated the LRC entry yet,
   an on-demand fetch for just that song is triggered.

---

## Phase 3: State Extension

**Edit:** `ops/admin-cli/src/stream_of_worship/admin/component_editor/state.py`

### 3a. `SongSession` — add `song` field

The detail panel needs song-level info (title, artist, album, etc.). Currently
`SongSession` only stores `song_id` and `song_title`. Add the full `Song` object:

```python
from stream_of_worship.admin.db.models import Song, SongComponent

@dataclass
class SongSession:
    song_id: str
    song_title: str
    hash_prefix: str
    audio_path: str
    audio_duration: float | None
    entry_component: SongComponent | None
    exit_component: SongComponent | None
    song: Song | None = None  # NEW — full Song object for detail panel
    # ... existing fields unchanged ...
    working: dict[tuple[str, str], Any] = field(default_factory=dict)
    dirty: bool = False
    r2_save_pending: bool = False

    def component_for_role(self, role: str) -> SongComponent | None:
        return self.entry_component if role == "entry" else self.exit_component
```

### 3b. `ComponentEditorState` — add LRC fields

```python
from .lrc_fetch import LRCFetch
from ..services.lrc_parser import LRCParsedContent

@dataclass
class ComponentEditorState:
    # ... existing fields unchanged ...
    sessions: list[SongSession]
    current_index: int = 0
    _undo_stacks: dict[str, list[ComponentUndoEntry]] = field(default_factory=dict)
    _redo_stacks: dict[str, list[ComponentUndoEntry]] = field(default_factory=dict)
    selected_row: int = 0  # 0 = entry, 1 = exit
    selected_column_key: str = "role"  # retained for autosave compat

    # NEW: LRC fetch + parsed content per song
    lrc_fetches: dict[str, LRCFetch] = field(default_factory=dict)
    lrc_parsed: dict[str, Optional[LRCParsedContent]] = field(default_factory=dict)
    lrc_prefetch_in_progress: bool = False
```

### 3c. No changes to existing methods

`get_value()`, `set_value()`, `push_undo()`, `undo()`, `redo()`,
`clear_undo_stacks()`, `current`, `current_undo`, `current_redo` — all unchanged.

---

## Phase 4: Compact Table — Top Panel

**Edit:** `ops/admin-cli/src/stream_of_worship/admin/component_editor/screen.py`

### 4a. Table setup

Replace `_setup_table()` to use `COMPACT_TABLE_COLUMNS` instead of
`DATA_TABLE_COLUMNS`:

```python
from .constants import COMPACT_TABLE_COLUMNS

def _setup_table(self) -> None:
    table = self.query_one("#component-table", DataTable)
    table.add_columns(*(header for _, header in COMPACT_TABLE_COLUMNS))
    table.cursor_type = "row"
    table.show_cursor = True
    table.zebra_stripes = True  # for readability in compact form
```

### 4b. Cell formatting

The existing `_format_cell_value(field_key, value)` already handles:
- `None` → `"—"`
- `start_time` / `end_time` → `format_duration(float(value))` (MM:SS)
- `float` → `f"{value:.4g}"`
- `str` → `str(value)`

This works unchanged for the compact columns. `"key"` is a string like `"C"` or
`"G major"` — it renders fine in the compact table.

### 4c. Table refresh

`_refresh_table()` is updated to use `COMPACT_TABLE_COLUMNS`:

```python
def _refresh_table(self) -> None:
    table = self.query_one("#component-table", DataTable)
    table.clear()
    for role in ("entry", "exit"):
        row_values = [self._cell_value(role, key) for key, _ in COMPACT_TABLE_COLUMNS]
        table.add_row(*row_values, key=role)
    row = max(0, min(self.state.selected_row, 1))
    try:
        table.move_cursor(row=row, scroll=True)
    except Exception:
        pass
```

### 4d. `_refresh_table_cell` updated

```python
def _refresh_table_cell(self, role: str, field_name: str) -> None:
    try:
        table = self.query_one("#component-table", DataTable)
    except NoMatches:
        return
    try:
        col_idx = next(
            i for i, (key, _) in enumerate(COMPACT_TABLE_COLUMNS) if key == field_name
        )
    except StopIteration:
        return
    value = self._cell_value(role, field_name)
    try:
        table.update_cell_at(Coordinate(0 if role == "entry" else 1, col_idx), value)
    except Exception:
        pass
```

### 4e. Selection sync — simplified

The table is now read-only (no column-based editing). `_sync_selection_from_table_cursor()`
only tracks the row (entry/exit):

```python
def _sync_selection_from_table_cursor(self) -> None:
    try:
        table = self.query_one("#component-table", DataTable)
    except NoMatches:
        return
    cursor_row = table.cursor_row
    if cursor_row is None:
        return
    if 0 <= cursor_row <= 1:
        self.state.selected_row = cursor_row
    # Also trigger detail panel refresh
    self._refresh_detail_panel()
```

### 4f. Top panel is read-only

The `ComponentMetadataTable` action guard methods (`action_cursor_up/down/page_up/
page_down`) still call `self.screen._guard_active_edit()` — but now the guard also
checks `self._active_panel == "details"` context. The table itself never enters edit
mode. The `e`, `[`, `]` keys are intercepted at the screen level and only have effect
when the detail panel is focused.

---

## Phase 5: Component Detail Panel — Bottom-Right

**New file:** `ops/admin-cli/src/stream_of_worship/admin/component_editor/detail_panel.py`

### 5a. Class

```python
from textual.widgets import Static
from rich.text import Text
from rich.table import Table as RichTable
from typing import Optional

from ..component_editor.constants import (
    EDITABLE_FIELDS,
    THEME_VALUES,
    VOCAL_POSTURE_VALUES,
    GROOVE_DENSITY_MIN,
    GROOVE_DENSITY_MAX,
    ENERGY_LEVEL_MIN,
    ENERGY_LEVEL_MAX,
)
from ..component_editor.state import SongSession, ComponentEditorState
from ..db.models import SongComponent
from ..services.lrc_parser import format_duration


class ComponentDetailPanel(Static):
    """Bottom-right panel showing all component metadata + song info.

    Displays:
    - Song-level info (title, artist, album, series, musical_key)
    - Component metadata for the selected role (entry/exit)
    - Confidence breakdown sub-section
    - Editable fields (theme, vocal_posture, groove_density, energy_level)
      with navigation highlight
    - Reasoning fields
    - Component lifecycle dates (created_at, updated_at) at the bottom

    Navigation: up/down arrows move focus among editable fields.
    Editing: 'e' for numeric fields, '['/']' for enum cycling.
    """

    DEFAULT_CSS = """
    ComponentDetailPanel {
        height: 1fr;
        border-left: solid $primary;
        padding: 0 1;
        overflow-y: auto;
        background: $surface;
    }
    ComponentDetailPanel:focus {
        border-left: double $accent;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._focus_idx: int = 0  # index into EDITABLE_FIELDS
```

### 5b. Rendering

```python
    def update_detail(
        self,
        state: ComponentEditorState,
    ) -> None:
        """Render full component detail for the current song + selected role."""
        session = state.current
        role = "entry" if state.selected_row == 0 else "exit"
        comp = session.component_for_role(role)
        song = session.song

        text = Text()

        # ── Section: Song Info ──
        text.append("── Song Info ──\n", style="bold cyan")
        song_fields = [
            ("Title", song.title if song else session.song_title),
            ("Artist", song.composer if song else None),
            ("Lyricist", song.lyricist if song else None),
            ("Album", song.album_name if song else None),
            ("Series", song.album_series if song else None),
            ("Song Key", song.musical_key if song else None),
        ]
        for label, value in song_fields:
            text.append(f"  {label:12s}: ", style="dim")
            text.append(f"{value or '—'}\n")

        if comp is None:
            text.append("\n[No component for this role]\n", style="red italic")
            self.update(text)
            self.scroll_home(animate=False)
            return

        text.append("\n")

        # ── Section: Component Details ──
        text.append(f"── Component ({role}) ──\n", style="bold cyan")
        detail_fields = [
            ("Type", comp.component_type),
            ("Occurrence", str(comp.occurrence_index)),
            ("Start", format_duration(comp.start_time) if comp.start_time is not None else None),
            ("End", format_duration(comp.end_time) if comp.end_time is not None else None),
            ("BPM", f"{comp.bpm:.4g}" if comp.bpm is not None else None),
            ("Key", comp.key),
            ("Confidence", f"{comp.confidence:.4g}" if comp.confidence is not None else None),
            ("Backbeat", f"{comp.backbeat_strength:.4g}" if comp.backbeat_strength is not None else None),
        ]
        for label, value in detail_fields:
            text.append(f"  {label:12s}: ", style="dim")
            text.append(f"{value or '—'}\n")

        text.append("\n")

        # ── Section: Confidence Breakdown ──
        text.append("── Confidence Breakdown ──\n", style="bold cyan")
        conf_fields = [
            ("BPM", comp.bpm_confidence),
            ("Key", comp.key_confidence),
            ("Groove", comp.groove_confidence),
            ("Backbeat", comp.backbeat_confidence),
            ("Energy", comp.energy_confidence),
            ("Theme", comp.theme_confidence),
            ("Posture", comp.vocal_posture_confidence),
        ]
        for label, value in conf_fields:
            text.append(f"  {label:12s}: ", style="dim")
            text.append(f"{value:.4g}" if value is not None else "—")
            text.append("\n")

        text.append("\n")

        # ── Section: Editable Fields ──
        text.append("── Editable Fields ──\n", style="bold yellow")
        for i, field in enumerate(EDITABLE_FIELDS):
            # Get current value (working or persisted)
            value = state.get_value(role, field)
            is_focused = (i == self._focus_idx)
            marker = "►" if is_focused else " "

            if field == "theme":
                hint = " [◄ ►]"
                value_str = str(value) if value else "—"
            elif field == "vocal_posture":
                hint = " [◄ ►]"
                value_str = str(value) if value else "—"
            else:
                hint = " [e]"
                value_str = f"{value:.4g}" if isinstance(value, (int, float)) else (str(value) if value else "—")

            text.append(f" {marker} {field:15s}: ", style="dim")
            if is_focused:
                text.append(f"{value_str}{hint}\n", style="bold reverse")
            else:
                text.append(f"{value_str}{hint}\n")

        text.append("\n")

        # ── Section: Reasoning ──
        text.append("── Reasoning ──\n", style="bold cyan")
        reasoning_fields = [
            ("Theme", comp.theme_reasoning),
            ("Posture", comp.posture_reasoning),
        ]
        for label, value in reasoning_fields:
            text.append(f"  {label:12s}: ", style="dim")
            if value:
                # Wrap long text
                text.append(f"{value}\n")
            else:
                text.append("—\n")

        text.append("\n")

        # ── Section: Lifecycle (dates at bottom) ──
        text.append("── Lifecycle ──\n", style="bold cyan")
        text.append(f"  {'Created':12s}: ", style="dim")
        text.append(f"{comp.created_at or '—'}\n")
        text.append(f"  {'Updated':12s}: ", style="dim")
        text.append(f"{comp.updated_at or '—'}\n")

        self.update(text)
        self.scroll_home(animate=False)
```

### 5c. Focus navigation

```python
    def move_focus_up(self) -> None:
        if self._focus_idx > 0:
            self._focus_idx -= 1

    def move_focus_down(self) -> None:
        if self._focus_idx < len(EDITABLE_FIELDS) - 1:
            self._focus_idx += 1

    @property
    def focused_field(self) -> str:
        return EDITABLE_FIELDS[self._focus_idx]
```

### 5d. Rendered layout summary

```
── Song Info ──
  Title       : 耶穌愛你
  Artist      : 讚美之泉
  Lyricist    : —
  Album       : 敬拜讚美15
  Series      : 敬拜讚美
  Song Key     : G

── Component (entry) ──
  Type         : chorus
  Occurrence   : 1
  Start        : 0:32
  End          : 1:15
  BPM          : 128
  Key          : G
  Confidence   : 0.92
  Backbeat     : 0.75

── Confidence Breakdown ──
  BPM          : 0.95
  Key          : 0.88
  Groove       : 0.91
  Backbeat     : 0.82
  Energy       : 0.79
  Theme        : 0.85
  Posture      : 0.72

── Editable Fields ──
 ► theme          : 讚美 [◄ ►]
   vocal_posture  : To God [◄ ►]
   groove_density : 1.25 [e]
   energy_level   : -12 [e]

── Reasoning ──
  Theme        : The lyrics speak of praising God's love...
  Posture      : Directed to God as worship...

── Lifecycle ──
  Created      : 2026-08-10 14:23:01
  Updated      : 2026-08-11 09:15:33
```

---

## Phase 6: Lyrics Panel — Bottom-Left

**New file:** `ops/admin-cli/src/stream_of_worship/admin/component_editor/lyrics_panel.py`

Identical to v1 spec `LyricsPanel(Static)` — absorbed unchanged.

### Class

```python
from textual.widgets import Static
from rich.text import Text
from ..services.lrc_parser import LRCParsedContent, format_centiseconds


class LyricsPanel(Static):
    """Bottom-left panel showing timestamped LRC lyrics for the current song."""

    DEFAULT_CSS = """
    LyricsPanel {
        height: 1fr;
        padding: 0 1;
        overflow-y: auto;
        background: $surface;
    }
    LyricsPanel:focus {
        border-right: double $accent;
    }
    LyricsPanel.empty {
        color: $text-muted;
        text-align: center;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._song_title: str = ""

    def update_lrc(
        self,
        parsed: Optional[LRCParsedContent],
        song_title: str,
    ) -> None:
        self._song_title = song_title
        if parsed is None:
            self.add_class("empty")
            self.update(f'No LRC file found for "{song_title}"')
            return
        self.remove_class("empty")
        text = Text()
        if parsed.metadata:
            for key, value in parsed.metadata.items():
                text.append(f"[{key}: {value}]\n", style="dim italic")
            text.append("\n")
        for line in parsed.timed_lines:
            timestamp = (
                format_centiseconds(line.time_seconds)
                if line.time_seconds is not None
                else "--:--.--"
            )
            text.append(f"[{timestamp}]  ", style="cyan")
            text.append(line.text + "\n")
        self.update(text)
        self.scroll_home(animate=False)

    def update_fetching(self, song_title: str) -> None:
        self.add_class("empty")
        self.update(f'Loading lyrics for "{song_title}"...')

    def update_error(self, msg: str, song_title: str) -> None:
        self.add_class("empty")
        self.update(f'Error loading lyrics for "{song_title}": {msg}')
```

### Rendering details

- Timestamp column rendered in `cyan` style for visual separation
- LRC metadata header (`[ti:]`, `[ar:]`, `[al:]`) rendered in `dim italic`
- Empty lyric lines preserved (rendered as just the timestamp)
- Auto-scrolls to top on each update
- `empty` CSS class applies muted color + centered text for placeholders
- `:focus` CSS adds a right border highlight when lyrics panel is active

---

## Phase 7: Screen Layout Changes

**Edit:** `ops/admin-cli/src/stream_of_worship/admin/component_editor/screen.py`

### 7a. Imports

```python
from textual.containers import Horizontal, Vertical
from .lyrics_panel import LyricsPanel
from .detail_panel import ComponentDetailPanel
from .lrc_fetch import prefetch_all_lrc, fetch_lrc_for_song, LRCFetch
from ..services.lrc_parser import parse_lrc_full
from textual.work import work
```

### 7b. `compose()` update

```python
def compose(self) -> ComposeResult:
    yield Header()
    with Vertical(id="editor-body"):
        yield SongBreadcrumb()
        yield PlaybackBar()

        # Top panel: compact read-only table
        with Vertical(id="top-panel"):
            yield ComponentMetadataTable(id="component-table")

        # Bottom split: lyrics (left) + details (right)
        with Horizontal(id="bottom-split"):
            yield LyricsPanel(id="lyrics-panel")
            yield ComponentDetailPanel(id="detail-panel")

        # Hidden Input overlay (for numeric editing in detail panel)
        yield Input(
            id="row-edit-input",
            placeholder="Edit numeric value",
            select_on_focus=False,
            compact=True,
        )
        yield StatusIndicator()
    yield GroupedFooter()
```

### 7c. `DEFAULT_CSS` update

```css
ComponentEditorScreen {
    layout: vertical;
}

#editor-body {
    height: 1fr;
    overflow: hidden;
}

/* Top panel: fixed mini-height */
#top-panel {
    height: 6;       /* ~header(1) + 2 data rows(2) + padding/border(3) */
    overflow: hidden;
    border-bottom: solid $primary;
}

#component-table {
    height: 1fr;
}

/* Bottom split: 50/50 horizontal */
#bottom-split {
    height: 1fr;
    overflow: hidden;
}

#lyrics-panel {
    width: 1fr;       /* 50% */
    overflow-y: auto;
    background: $surface;
    border-right: solid $primary;
}

#detail-panel {
    width: 1fr;       /* 50% */
    overflow-y: auto;
    background: $surface;
}

#row-edit-input {
    display: none;
    height: 1;
    layer: overlay;
}
```

`#top-panel` = fixed `6` rows, `#bottom-split` = `1fr` (fills remaining space).
Within `#bottom-split`, `#lyrics-panel` and `#detail-panel` each get `1fr`
(equivalent to 50/50).

### 7d. Panel focus tracking

Add a screen-level attribute to track which panel is focused:

```python
class ComponentEditorScreen(Screen[None]):
    # ...
    def __init__(self, ...):
        super().__init__()
        # ... existing fields ...
        self._active_panel: str = "top"  # "top" | "lyrics" | "details"
```

### 7e. `on_mount()` — table setup, detail panel, LRC pre-fetch

```python
def on_mount(self) -> None:
    self._setup_table()
    self._refresh_table()
    self._refresh_detail_panel()
    self.query_one("#component-table", ComponentMetadataTable).focus()
    self._active_panel = "top"
    self._update_breadcrumb()
    self._update_status()
    self._start_position_updates()

    self.playback.set_callbacks(
        on_position_changed=self._on_playback_position,
        on_state_changed=self._on_playback_state,
        on_finished=self._on_playback_finished,
    )

    self._load_audio_for_current_song()
    self._maybe_apply_autosave()

    # LRC pre-fetch (absorbed from v1)
    self.state.lrc_prefetch_in_progress = True
    self._refresh_lyrics_panel()
    self._prefetch_lrc()
```

### 7f. LRC pre-fetch worker

```python
@work(exclusive=True, group="lrc-fetch")
async def _prefetch_lrc(self) -> None:
    try:
        fetches = await prefetch_all_lrc(
            self.state.sessions,
            self.r2_client,
            self.cache_dir,
        )
        for song_id, fetch in fetches.items():
            self.state.lrc_fetches[song_id] = fetch
            self.state.lrc_parsed[song_id] = (
                parse_lrc_full(fetch.content) if fetch.content else None
            )
    except Exception as exc:
        self.state.lrc_fetch_error = str(exc)
    finally:
        self.state.lrc_prefetch_in_progress = False
        self._refresh_lyrics_panel()
```

### 7g. `_refresh_lyrics_panel()` — render current song's lyrics

```python
def _refresh_lyrics_panel(self) -> None:
    panel = self.query_one("#lyrics-panel", LyricsPanel)
    session = self.state.current
    if session is None:
        panel.update_lrc(None, "")
        return

    song_id = session.song_id
    song_title = session.song_title

    if song_id in self.state.lrc_parsed:
        fetch = self.state.lrc_fetches.get(song_id)
        if fetch and fetch.error:
            panel.update_error(fetch.error, song_title)
        else:
            panel.update_lrc(self.state.lrc_parsed[song_id], song_title)
        return

    if self.state.lrc_prefetch_in_progress:
        panel.update_fetching(song_title)
        self._fetch_lrc_on_demand(song_id, song_title)
        return

    panel.update_lrc(None, song_title)
```

### 7h. `_refresh_detail_panel()` — render current component details

```python
def _refresh_detail_panel(self) -> None:
    panel = self.query_one("#detail-panel", ComponentDetailPanel)
    panel.update_detail(self.state)
```

### 7i. On-demand LRC fetch fallback

```python
@work(exclusive=False, group="lrc-fetch-on-demand")
async def _fetch_lrc_on_demand(self, song_id: str, song_title: str) -> None:
    if song_id in self.state.lrc_parsed:
        return
    try:
        fetch = await fetch_lrc_for_song(
            song_id, self.r2_client, self.cache_dir,
        )
        self.state.lrc_fetches[song_id] = fetch
        self.state.lrc_parsed[song_id] = (
            parse_lrc_full(fetch.content) if fetch.content else None
        )
    except Exception as exc:
        self.state.lrc_fetches[song_id] = LRCFetch(
            song_id=song_id, content=None, cached_path=None, error=str(exc),
        )
        self.state.lrc_parsed[song_id] = None
    current = self.state.current
    if current and current.song_id == song_id:
        self._refresh_lyrics_panel()
```

---

## Phase 8: Panel Navigation & Key Bindings

**Edit:** `ops/admin-cli/src/stream_of_worship/admin/component_editor/screen.py`

### 8a. Binding changes

The `tab` and `shift+tab` bindings are repurposed from table column navigation
to **panel focus cycling**:

```python
BINDINGS = [
    # Playback / Nav (global — work on any panel)
    Binding("space", "toggle_playback", "Play/Pause"),
    Binding("left", "seek_backward", "Seek -5s"),
    Binding("right", "seek_forward", "Seek +5s"),
    Binding("j", "jump_to_component", "Jump"),
    # Song switch (global)
    Binding("n", "next_song", "Next Song"),
    Binding("p", "prev_song", "Prev Song"),

    # Panel navigation (replaces column nav)
    Binding("tab", "cycle_panel_next", "Panel →"),
    Binding("shift+tab", "cycle_panel_prev", "Panel ←"),

    # Edit (only active when detail panel is focused)
    Binding("bracketleft", "cycle_field_prev", "Cycle −"),
    Binding("bracketright", "cycle_field_next", "Cycle +"),
    Binding("e", "edit_numeric", "Edit Num"),

    # Detail panel field navigation (only when detail panel is focused)
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

### 8b. Binding groups (footer)

```python
BINDING_GROUPS: dict[str, list[str]] = {
    "Playback": [
        "toggle_playback",
        "seek_backward",
        "seek_forward",
        "jump_to_component",
    ],
    "Songs": ["next_song", "prev_song"],
    "Panels": ["cycle_panel_next", "cycle_panel_prev", "detail_focus_up", "detail_focus_down"],
    "Edit": ["cycle_field_prev", "cycle_field_next", "edit_numeric"],
    "General": ["save", "undo", "redo", "quit_editor", "show_keymap"],
}
```

### 8c. Panel cycling actions

```python
_PANEL_ORDER = ("top", "lyrics", "details")

def action_cycle_panel_next(self) -> None:
    if self._guard_active_edit():
        return
    idx = _PANEL_ORDER.index(self._active_panel)
    self._active_panel = _PANEL_ORDER[(idx + 1) % len(_PANEL_ORDER)]
    self._focus_active_panel()

def action_cycle_panel_prev(self) -> None:
    if self._guard_active_edit():
        return
    idx = _PANEL_ORDER.index(self._active_panel)
    self._active_panel = _PANEL_ORDER[(idx - 1) % len(_PANEL_ORDER)]
    self._focus_active_panel()

def _focus_active_panel(self) -> None:
    if self._active_panel == "top":
        self.query_one("#component-table", ComponentMetadataTable).focus()
    elif self._active_panel == "lyrics":
        self.query_one("#lyrics-panel", LyricsPanel).focus()
    elif self._active_panel == "details":
        self.query_one("#detail-panel", ComponentDetailPanel).focus()
        self._refresh_detail_panel()  # re-render with focus highlight
```

### 8d. Detail panel field navigation

```python
def action_detail_focus_up(self) -> None:
    if self._active_panel != "details":
        return  # only navigates fields when detail panel is focused
    if self._guard_active_edit():
        return
    panel = self.query_one("#detail-panel", ComponentDetailPanel)
    panel.move_focus_up()
    self._refresh_detail_panel()

def action_detail_focus_down(self) -> None:
    if self._active_panel != "details":
        return
    if self._guard_active_edit():
        return
    panel = self.query_one("#detail-panel", ComponentDetailPanel)
    panel.move_focus_down()
    self._refresh_detail_panel()
```

### 8e. Edit actions — context-aware

`action_edit_numeric` and `action_cycle_field_next/prev` now operate based on
the detail panel's focused field rather than the table's selected column:

```python
def action_edit_numeric(self) -> None:
    if self._guard_active_edit():
        return
    if self._active_panel != "details":
        return  # editing only works in the detail panel
    if self._guard_no_component():
        return

    panel = self.query_one("#detail-panel", ComponentDetailPanel)
    field = panel.focused_field
    if field not in ("groove_density", "energy_level"):
        return

    role = self._selected_role()
    current = self.state.get_value(role, field)
    initial = "" if current is None else f"{current:.4g}"
    self._show_value_edit_input(role=role, field=field, initial_text=initial)

def action_cycle_field_next(self) -> None:
    if self._guard_active_edit():
        return
    if self._active_panel != "details":
        return
    if self._guard_no_component():
        return

    panel = self.query_one("#detail-panel", ComponentDetailPanel)
    field = panel.focused_field
    if field not in ("theme", "vocal_posture"):
        return

    role = self._selected_role()
    values = THEME_VALUES if field == "theme" else VOCAL_POSTURE_VALUES
    current = self.state.get_value(role, field)
    try:
        idx = values.index(current)
    except (ValueError, TypeError):
        idx = -1
    new_value = values[(idx + 1) % len(values)]
    self.state.set_value(role, field, new_value)
    self._do_autosave()
    self._refresh_table()
    self._refresh_detail_panel()
    self._update_status()

def action_cycle_field_prev(self) -> None:
    if self._guard_active_edit():
        return
    if self._active_panel != "details":
        return
    if self._guard_no_component():
        return

    panel = self.query_one("#detail-panel", ComponentDetailPanel)
    field = panel.focused_field
    if field not in ("theme", "vocal_posture"):
        return

    role = self._selected_role()
    values = THEME_VALUES if field == "theme" else VOCAL_POSTURE_VALUES
    current = self.state.get_value(role, field)
    try:
        idx = values.index(current)
    except (ValueError, TypeError):
        idx = 0
    new_value = values[(idx - 1) % len(values)]
    self.state.set_value(role, field, new_value)
    self._do_autosave()
    self._refresh_table()
    self._refresh_detail_panel()
    self._update_status()
```

### 8f. Input overlay repositioning

The `_show_value_edit_input` method is adapted to position the Input overlay
over the detail panel's focused editable field line instead of a table cell.

The detail panel renders editable fields in a known order (`EDITABLE_FIELDS`),
each on its own line. The line offset can be computed as:
- Count of lines before "Editable Fields" section header
- + 1 (section header itself)
- + `detail_panel._focus_idx` (0-based offset to the focused field)

```python
def _show_value_edit_input(self, role: str, field: str, initial_text: str) -> None:
    detail_panel = self.query_one("#detail-panel", ComponentDetailPanel)

    def do_show() -> None:
        # Compute the y-offset of the focused editable field line
        # within the detail panel's visible region.
        panel_region = detail_panel.region
        scroll_y = detail_panel.scroll_y

        # The "Editable Fields" section starts after:
        # - 6 song info lines (header + 6 fields)
        # - 1 blank line
        # - 7 component detail lines (header + 7+ fields + visible)
        # - 1 blank line
        # - 7 confidence lines (header + 7 fields)
        # - 1 blank line
        # = approximately 23 lines before the editable fields section header
        # + 1 (section header) + focus_idx
        #
        # This is fragile if the number of fields changes. For robustness,
        # the detail panel can expose its computed line offsets.

        editable_section_line = detail_panel.get_editable_field_line_offset(field)
        y = panel_region.y + editable_section_line - scroll_y
        x = panel_region.x + 2  # left padding
        width = panel_region.width - 4  # padding on both sides

        if y < panel_region.y or y >= panel_region.y + panel_region.height:
            self.notify("Scroll to the field to edit", severity="warning", timeout=2)
            return

        edit_input = self.query_one("#row-edit-input", Input)
        edit_input.value = initial_text
        edit_input.cursor_position = 0
        edit_input.set_scroll(0, None)
        edit_input.placeholder = f"Edit {field}"
        edit_input.styles.offset = Offset(x, y)
        edit_input.styles.width = max(1, width)
        edit_input.display = True
        self._edit_mode = "numeric"
        self._edit_target_role = role
        self._edit_target_field = field
        edit_input.focus()

    self.call_after_refresh(do_show)
```

### 8g. `ComponentDetailPanel` helper — line offset computation

Add a method to `ComponentDetailPanel` that returns the y-offset (line number)
of a given editable field within the rendered text:

```python
def get_editable_field_line_offset(self, field: str) -> int:
    """Return the 0-based line index of the given editable field's value
    within the rendered text. Used by the screen to position the Input overlay.

    The layout is deterministic:
    - 1 (Song header) + 6 (song fields) = 7
    - 1 (blank) = 8
    - 1 (Component header) + 8 (component fields) = 17
    - 1 (blank) = 18
    - 1 (Confidence header) + 7 (conf fields) = 26
    - 1 (blank) = 27
    - 1 (Editable header) = 28
    - + index of field in EDITABLE_FIELDS = target line
    """
    try:
        field_idx = EDITABLE_FIELDS.index(field)
    except ValueError:
        return 0
    return 28 + field_idx
```

**Note:** This offset is fragile if the rendering structure changes. An
alternative implementation would have the detail panel track line offsets
during rendering and cache them. For v2, the deterministic layout approach
is used for simplicity. The exact line count should be verified during
implementation and the constant `28` adjusted if the rendering layout differs.
A better approach for future versions would be to use Textual's `RichLog` or
individual `Label` widgets per line to avoid manual offset computation.

### 8h. `on_input_submitted` — updated

```python
def on_input_submitted(self, event: Input.Submitted) -> None:
    if event.input.id != "row-edit-input":
        return
    if (
        self._edit_mode is None
        or self._edit_target_role is None
        or self._edit_target_field is None
    ):
        self._cancel_row_edit()
        return

    role = self._edit_target_role
    field = self._edit_target_field
    val = self._validate_numeric_field(field, event.value)
    if val is None:
        self.app.bell()
        self.notify(
            f"Invalid value for {field}",
            severity="warning",
            timeout=2,
        )
        return

    self.state.set_value(role, field, val)
    self._hide_row_edit_input()
    self._refresh_table()
    self._refresh_detail_panel()  # NEW: refresh detail panel too
    self._do_autosave()
    self._update_status()
    # Refocus the detail panel (not the table)
    self.query_one("#detail-panel", ComponentDetailPanel).focus()
```

### 8i. `on_resize` — updated

```python
def on_resize(self, event: events.Resize) -> None:
    if self._edit_mode is None:
        return
    if self._edit_target_role is None or self._edit_target_field is None:
        return
    # Reposition the Input overlay over the detail panel's focused field
    detail_panel = self.query_one("#detail-panel", ComponentDetailPanel)
    panel_region = detail_panel.region
    scroll_y = detail_panel.scroll_y
    line_offset = detail_panel.get_editable_field_line_offset(self._edit_target_field)
    y = panel_region.y + line_offset - scroll_y
    x = panel_region.x + 2
    width = panel_region.width - 4
    if y < panel_region.y or y >= panel_region.y + panel_region.height:
        self._cancel_row_edit()
        return
    edit_input = self.query_one("#row-edit-input", Input)
    edit_input.styles.offset = Offset(x, y)
    edit_input.styles.width = max(1, width)
```

### 8j. `_cancel_row_edit` — updated

```python
def _cancel_row_edit(self) -> None:
    self._hide_row_edit_input()
    try:
        self.query_one("#detail-panel", ComponentDetailPanel).focus()
    except NoMatches:
        pass
```

---

## Phase 9: Song Switch Wiring — 3-Panel Refresh

When the user switches songs, all three panels must refresh:

```python
def _switch_song(self, delta: int) -> None:
    if self.state.current.dirty and not self._do_autosave():
        self.app.bell()
        return
    new_idx = (self.state.current_index + delta) % len(self.state.sessions)
    if new_idx == self.state.current_index:
        return
    self.state.current_index = new_idx
    self.playback.stop()
    self._load_audio_for_current_song()
    self._refresh_table()
    self._refresh_detail_panel()   # NEW
    self._refresh_lyrics_panel()   # NEW
    self._update_breadcrumb()
    self._update_status()
```

After undo/redo and save, both table and detail panel must refresh:

```python
def action_undo(self) -> None:
    if self._guard_active_edit():
        return
    entry = self.state.undo()
    if entry is None:
        self.app.bell()
        return
    self._do_autosave()
    self._refresh_table()
    self._refresh_detail_panel()  # NEW
    self._update_status()
    self.notify("Undo", timeout=2)

def action_redo(self) -> None:
    if self._guard_active_edit():
        return
    entry = self.state.redo()
    if entry is None:
        self.app.bell()
        return
    self._do_autosave()
    self._refresh_table()
    self._refresh_detail_panel()  # NEW
    self._update_status()
    self.notify("Redo", timeout=2)
```

After save success:

```python
def action_save(self) -> None:
    # ... existing logic through step 5 (full success) ...
    # In the success branch:
    session.working.clear()
    session.dirty = False
    session.r2_save_pending = False
    self._reload_components_from_db(session)
    self.state.clear_undo_stacks(session)
    clear_autosave(self.cache_dir, session.hash_prefix)
    self._autosave_ok = True
    self._update_status()
    self._refresh_table()
    self._refresh_detail_panel()  # NEW
    self._notify("[green]Saved (DB + R2).[/]")
```

---

## Phase 10: Song Object in `commands/audio.py`

**Edit:** `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py`

In the `review_components` command (around line 4531), the Song object is already
fetched via `song = db_client.get_song(song_id)` but only `song.title` is passed
to `SongSession`. Pass the full `song` object:

```python
sessions.append(
    SongSession(
        song_id=song_id,
        song_title=song.title,
        hash_prefix=recording.hash_prefix,
        audio_path=str(audio_path),
        audio_duration=recording.duration_seconds,
        entry_component=entry,
        exit_component=exit_comp,
        song=song,  # NEW — pass full Song object
    )
)
```

This is the only change needed in `commands/audio.py`. The `Song` data model
(`db/models.py:18-63`) has fields: `title`, `title_pinyin`, `composer`,
`lyricist`, `album_name`, `album_series`, `musical_key`, `source_url`, and more
— all available to the detail panel rendering.

---

## Phase 11: Reuse Existing Modules (no edits)

| Module | Reused for |
|---|---|
| `services/lrc_parser.py` | `parse_lrc_full(content) -> LRCParsedContent`, `format_centiseconds()`, `format_duration()` |
| `services/r2.py` | `get_lrc_identity()`, `download_lrc_content()` |
| `services/asset_cache.py` | `download_lrc()` (alternative simpler path) |
| `editor/footer.py` | `GroupedFooter`, `format_key_display` (unchanged) |
| `services/playback.py` | `PlaybackService` (unchanged) |
| `db/client.py` | `get_song()`, `get_song_components_entry_exit()`, `update_song_component_fields_txn()` (unchanged) |
| `db/models.py` | `Song`, `SongComponent` data models (unchanged) |
| `component_editor/state.py` | `ComponentEditorState`, `SongSession` (extended) |
| `component_editor/autosave.py` | `ComponentAutosaveState`, `save_autosave()`, `load_autosave()`, `clear_autosave()` (unchanged) |

---

## Phase 12: Edge Cases

| Case | Handling |
|---|---|
| No LRC in R2 for song | `LRCFetch(content=None)` → lyrics panel shows `No LRC file found for "{title}"` |
| R2 fetch error (network, auth) | `LRCFetch(error=msg)` → lyrics panel shows error message |
| User switches song mid-pre-fetch | Lyrics panel shows `Loading lyrics...`; on-demand fetch triggered for current song |
| Multi-song songset | Per-song LRC tracked in `state.lrc_fetches` / `state.lrc_parsed`; both lyrics + detail panels re-render on switch |
| Empty LRC (file exists, no timed lines) | `parse_lrc_full` returns `LRCParsedContent` with empty `timed_lines`; lyrics panel renders metadata header only |
| Pre-fetch worker crashes | `lrc_prefetch_in_progress` set `False` in `finally`; lyrics panel falls back to on-demand fetch |
| On-demand fetch for song already in progress | Early return if `song_id in state.lrc_parsed` |
| User switches away from song during on-demand fetch | Fetch completes, populates state, but panels only refresh if song is still current |
| Selected role has no component (None) | Detail panel shows `[No component for this role]`; editing keys (`e`, `[`, `]`) are no-ops (guarded by `_guard_no_component`) |
| User presses `e` on non-numeric field | No-op — `action_edit_numeric` checks `field not in ("groove_density", "energy_level")` |
| User presses `[`/`]` on non-enum field | No-op — `action_cycle_field_*` checks `field not in ("theme", "vocal_posture")` |
| User presses editing keys while top panel is focused | No-op — all edit actions check `self._active_panel != "details"` |
| Input overlay can't compute field line position | Shows warning notification `"Cannot start editing this cell"`; focus returns to detail panel |
| `song` is None on SongSession | Detail panel falls back to `session.song_title` for the Title field; other song fields show `—` |
| Terminal resized while editing | `on_resize` repositions the Input overlay relative to the detail panel's current scroll position |
| Detail panel scrolled past the focused field | Input overlay position computation detects the field is out of view; shows `"Scroll to the field to edit"` warning |
| Autosave recovery | Detail panel re-renders from restored state after `_maybe_apply_autosave()` calls `_refresh_table()` — add `_refresh_detail_panel()` call alongside |

### Autosave interplay

The existing autosave (`ComponentAutosaveState`) persists `selected_row` and
`selected_column_key`. In v2:
- `selected_row` is still used (entry/exit row selection) — persisted as-is.
- `selected_column_key` is no longer directly meaningful (no table column
  navigation), but it is retained for backward-compat. The detail panel's
  `_focus_idx` is NOT persisted in autosave — it resets to 0 on each TUI launch.
  This is an acceptable trade-off; the user can simply press down-arrow to
  reach the desired field.

Add `_refresh_detail_panel()` to the end of `_maybe_apply_autosave()`:

```python
def _maybe_apply_autosave(self) -> None:
    # ... existing logic ...
    self._refresh_table()
    self._refresh_detail_panel()  # NEW
    self._update_status()
```

---

## Files Changed

| File | Change type | Est. LOC |
|---|---|---|
| `component_editor/constants.py` | **Edit** — add `COMPACT_TABLE_COLUMNS` | ~15 |
| `component_editor/lrc_fetch.py` | **New** — LRC fetch helpers | ~80 |
| `component_editor/lyrics_panel.py` | **New** — `LyricsPanel(Static)` widget | ~70 |
| `component_editor/detail_panel.py` | **New** — `ComponentDetailPanel(Static)` widget with rendering + field navigation | ~150 |
| `component_editor/state.py` | **Edit** — add `song` field to `SongSession`, add `lrc_fetches`/`lrc_parsed`/`lrc_prefetch_in_progress` to `ComponentEditorState` | ~20 |
| `component_editor/screen.py` | **Edit** — `compose()` 3-panel layout, CSS, `on_mount` worker, panel cycling, detail panel refresh, edit action context-awareness, Input repositioning, song switch wiring, binding changes, autosave interplay | ~250 |
| `commands/audio.py` | **Edit** — pass `song=song` to `SongSession` constructor (1 line) | ~1 |

**Total estimated additions:** ~586 LOC across 3 new files + 4 edited files.

---

## Testing

### Manual verification

```bash
# Songs with LRC available
uv run --project ops/admin-cli --extra admin sow-admin audio review-components <song_id_with_lrc>

# Songs without LRC
uv run --project ops/admin-cli --extra admin sow-admin audio review-components <song_id_without_lrc>

# Multi-song songset (test pre-fetch + on-demand + panel switching)
uv run --project ops/admin-cli --extra admin sow-admin audio review-components <id1> <id2> <id3>
```

### Verification checklist

**Layout:**
- [ ] Top panel spans full width with compact numerical columns only
- [ ] Top panel is fixed mini-height (3-4 visible rows: header + entry + exit)
- [ ] Bottom-left and bottom-right panels are 50/50 width
- [ ] No dates or long text in the top panel
- [ ] Detail panel shows all component metadata + song-level info
- [ ] Dates (created_at, updated_at) appear at the bottom of the detail panel

**Lyrics panel:**
- [ ] Lyrics panel shows "Loading lyrics..." initially
- [ ] After pre-fetch completes, current song's lyrics appear with timestamps
- [ ] Switching songs (n/p) updates the lyrics panel
- [ ] Songs without LRC show placeholder text
- [ ] Lyrics panel scrolls independently (overflow-y: auto)
- [ ] LRC metadata header renders in dim italic
- [ ] Timestamps render in cyan

**Panel navigation:**
- [ ] Tab cycles: top → bottom-left (lyrics) → bottom-right (details) → top
- [ ] Shift+Tab cycles in reverse
- [ ] Focused panel has a visual highlight (border)
- [ ] Top panel shows only compact numerical columns

**Editing in detail panel:**
- [ ] Selecting entry/exit in top panel updates the detail panel
- [ ] Tab to detail panel, then up/down arrows navigate editable fields
- [ ] Focused editable field is highlighted (reverse style)
- [ ] `e` opens an Input overlay for numeric fields (groove_density, energy_level)
- [ ] `[`/`]` cycles enum values (theme, vocal_posture) immediately
- [ ] Edits reflect in both the compact table (if column is visible) and detail panel
- [ ] After editing, focus returns to the detail panel
- [ ] Undo/redo refreshes both table and detail panel
- [ ] Save (DB + R2) refreshes both table and detail panel

**Cross-panel:**
- [ ] Playback commands (space, left/right, j) work from any panel
- [ ] Song switch (n/p) refreshes all 3 panels + breadcrumb + status
- [ ] Save (s) works from any panel
- [ ] Quit dialog (q/escape) works from any panel

**Edge cases:**
- [ ] Song without `song` object shows `—` for artist/album fields
- [ ] Component is None (no entry/exit row) → detail panel shows error message
- [ ] R2 fetch errors show error message in lyrics panel
- [ ] Terminal resize repositions the Input overlay correctly
- [ ] Autosave recovery refreshes both table and detail panel

### Automated tests

If `component_editor/` has an existing test directory, add:

- `tests/test_lrc_fetch.py` — mock R2 client, test `fetch_lrc_for_song` and `prefetch_all_lrc`
- `tests/test_lyrics_panel.py` — test `LyricsPanel.update_lrc` rendering with various `LRCParsedContent` inputs
- `tests/test_detail_panel.py` — test `ComponentDetailPanel.update_detail` rendering with various component + song inputs, `move_focus_up/down`, `get_editable_field_line_offset`
- `tests/test_compact_table.py` — test that `_setup_table` uses `COMPACT_TABLE_COLUMNS`, `_refresh_table` builds correct rows

---

## Open Questions

None — all clarified via Q&A.
