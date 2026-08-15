# Implementation Plan: Component Editor — Lyrics + Detail Split Panel (v5)

> **Date:** 2026-08-15
> **Branch:** TBD
> **Spec ID:** `component-editor-lyrics-panel-v5`
> **Status:** Planning — not yet implemented
> **Builds on:** `component-metadata-editor-tui-v4` (implemented) + `component-editor-lyrics-split-panel-v2` (planned, superseded by this spec)

---

## Goal

Add the **Lyrics Panel** (timestamped LRC display) and **Component Detail Panel**
that were originally planned in `component-editor-lyrics-split-panel-v2.md` but
dropped when v4 was implemented with a Hero Panel + single DataTable layout.

The v4 implementation is **already live** in
`ops/admin-cli/src/stream_of_worship/admin/component_editor/screen.py`. This
spec restructures that screen from a vertical stack into a **T-shaped 3-panel
layout** that merges v4's Hero Panel with v2's lyrics + detail split:

1. **Top panel** (full-width): `SongBreadcrumb` → `PlaybackBar` →
   `ComponentHeroPanel` (v4, retained) → compact read-only `DataTable`
   (numerical columns only, from v2's `COMPACT_TABLE_COLUMNS`).
2. **Bottom-left panel**: `LyricsPanel` — timestamped LRC lyrics with
   playback-synced current-line highlight.
3. **Bottom-right panel**: `ComponentDetailPanel` — all component metadata +
   song-level info, formatted for easy reading, with editable field navigation.

The operator can now **view the timestamped lyrics while listening/reviewing
each of the components**, with the current lyric line highlighted as audio
plays.

---

## User Decisions (from clarification Q&A)

| Question | Decision |
|---|---|
| Layout integration with v4's Hero Panel | **T-shape**: Hero Panel + compact table on top; bottom split = lyrics (left) + detail panel (right). |
| Playback-synced lyric highlighting | **Current-line highlight** that tracks playback position (visual highlight only, no auto-scroll-to-center). |
| Lyric highlight on component seek | When `space` or `j` seeks to a component's `start_time`, the lyric highlight **jumps** to the first LRC line whose timestamp is ≥ `start_time`, then continues following via the playback-tracking mechanism. |
| Toggle visibility | **Always visible** — no toggle keybinding. |
| No LRC exists | Show placeholder message: `No LRC file found for "{title}"`. |
| Pre-fetch scope | **Pre-fetch all** LRC in parallel at TUI launch (v2 design), with on-demand fallback. |

---

## Non-Goals

- No auto-scroll-to-center of the lyrics panel (only visual highlight of the
  current line; the operator scrolls manually if the highlighted line is out
  of view).
- No bi-directional click-to-seek from lyrics to audio.
- No editing of LRC content from the lyrics panel (read-only display).
- No changes to the `audio edit-lrc` editor (sibling TUI).
- No changes to the save flow (DB + R2), autosave, or undo/redo mechanics.
- No changes to the Hero Panel's rendering logic (retained as-is from v4).
- No changes to `commands/audio.py` beyond what v4 already did (passing
  `song=song` to `SongSession` is already implemented at line 5465).

---

## Architecture Overview

### Current Layout (v4 — vertical stack)

```
┌─────────────────────────────────────┐
│ Header                              │
├─────────────────────────────────────┤
│ Vertical(#editor-body)              │
│   SongBreadcrumb                    │
│   PlaybackBar                       │
│   ComponentHeroPanel                │  ← v4, retained
│   ComponentMetadataTable (1fr)      │  ← 24 columns, 2 rows (entry+exit)
│   Input(#row-edit-input, hidden)    │  ← overlay for inline numeric edit
│   StatusIndicator                   │
├─────────────────────────────────────┤
│ GroupedFooter (docked bottom)       │
└─────────────────────────────────────┘
```

### Proposed Layout (v5 — T-shape, 3 panels)

```
┌─────────────────────────────────────────────────┐
│ Header                                          │
├─────────────────────────────────────────────────┤
│ Vertical(#editor-body)                          │
│   SongBreadcrumb                                │
│   PlaybackBar                                   │
│   ┌─────────────────────────────────────────┐   │
│   │ #top-panel (height: auto)               │   │  ← T's horizontal bar
│   │   ComponentHeroPanel                    │   │     (v4, retained)
│   │   ComponentMetadataTable (compact)      │   │     compact columns only
│   │   (read-only selection: entry/exit)      │   │     3-4 visible rows
│   └─────────────────────────────────────────┘   │
│   ┌──────────────────┬──────────────────────┐   │
│   │ #lyrics-panel    │ #detail-panel        │   │  ← T's vertical stem
│   │ LyricsPanel      │ ComponentDetailPanel │   │     50/50 split
│   │ (LRC display     │ (all metadata +      │   │
│   │  + current-line  │  song info +         │   │
│   │  highlight)       │  editable fields)    │   │
│   │              1fr │                  1fr │   │
│   └──────────────────┴──────────────────────┘   │
│   ┌─────────────────────────────────────────┐   │
│   │ Input(#row-edit-input, hidden)          │   │  ← overlay, repositioned
│   ├─────────────────────────────────────────┤   │
│   StatusIndicator                               │
├─────────────────────────────────────────────────┤
│ GroupedFooter (docked bottom)                  │
└─────────────────────────────────────────────────┘
```

Key structural changes from v4:
- `#editor-body` now contains: breadcrumb, playback bar, `#top-panel`
  (Hero Panel + compact table), `#bottom-split` (Horizontal container with
  50/50 children: `LyricsPanel` + `ComponentDetailPanel`), the hidden `Input`
  overlay, and `StatusIndicator`.
- The v4 `ComponentMetadataTable` with 24 columns is replaced by a **compact
  table** with 9 numerical columns (from v2's `COMPACT_TABLE_COLUMNS`).
  The full 24-column view is no longer needed — all metadata is in the detail
  panel, and transition-critical fields are in the Hero Panel.
- The table is now **read-only** (row selection only). Editing happens in the
  detail panel.
- A new `ComponentDetailPanel` widget renders all component + song metadata in
  a formatted, sectioned layout with editable field navigation (from v2 Phase 5).
- A new `LyricsPanel` widget renders timestamped LRC lyrics with playback-synced
  current-line highlight (from v2 Phase 6, extended with highlight tracking).
- The `Input(#row-edit-input)` overlay remains in the compose tree but is
  repositioned to overlay the detail panel's focused field instead of the
  table cell (from v2 Phase 8f).

---

## Phase 1: Constants — Compact Column Set

**Edit:** `ops/admin-cli/src/stream_of_worship/admin/component_editor/constants.py`

### 1a. New constant: `COMPACT_TABLE_COLUMNS`

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

### 1b. Column selection rationale

| Category | Actual fields | In compact table? |
|---|---|---|
| Component index | `occurrence_index` | Yes — "Occ" |
| Core tempo/key | `bpm`, `key` | Yes — "BPM", "Key" |
| Structural numbers | `start_time`, `end_time` | Yes — "Start", "End" (formatted as MM:SS) |
| Energy/dynamics | `confidence`, `backbeat_strength`, `groove_density`, `energy_level` | Yes — "Conf", "Backbeat", "Groove", "Energy" |

Excluded from compact table (shown in detail panel instead):
- `role`, `component_type` — text
- All `*_confidence` sub-fields — numerical but verbose
- `theme_reasoning`, `posture_reasoning` — long text
- `created_at`, `updated_at` — dates
- `theme`, `vocal_posture` — enum text (editable, shown in detail panel)

### 1c. Existing constants — unchanged

`EDITABLE_FIELDS`, `THEME_VALUES`, `VOCAL_POSTURE_VALUES`, `COMPONENT_SCHEMA_VERSION`,
`GROOVE_DENSITY_MIN/MAX`, `ENERGY_LEVEL_MIN/MAX`, `HERO_PRIMARY_FIELDS`,
`HERO_REASONING_FIELDS`, `REASONING_TABLE_TRUNC` — all unchanged.

`DATA_TABLE_COLUMNS` is kept (used for reference / detail panel field listing)
but no longer drives the top-panel table setup.

---

## Phase 2: LRC Fetch Service

**New file:** `ops/admin-cli/src/stream_of_worship/admin/component_editor/lrc_fetch.py`

### 2a. Data class

```python
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class LRCFetch:
    song_id: str
    content: Optional[str]       # None if no LRC exists in R2
    cached_path: Optional[Path]  # local cache path written, if any
    error: Optional[str]         # error message if fetch failed
```

### 2b. Functions

```python
import asyncio
from stream_of_worship.admin.services.r2 import R2Client


async def fetch_lrc_for_song(
    song_id: str,
    hash_prefix: str,
    r2_client: R2Client,
    cache_dir: Path,
) -> LRCFetch:
    """Download LRC for a single song from R2, cache locally.

    - Download content via r2.download_lrc_content(hash_prefix)
    - If no LRC exists in R2 -> return LRCFetch(content=None)
    - Write to {cache_dir}/{hash_prefix}/audio/lyrics.lrc
      (same directory as audio.mp3)
    - Return LRCFetch with parsed content
    """

async def prefetch_all_lrc(
    sessions: list[SongSession],
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

### 2c. Reuse

- `services/r2.py` → `get_lrc_identity()` (line 458), `download_lrc_content()` (line 486)
- `services/asset_cache.py` → `download_lrc()` (alternative simpler path, line 194)
- Local cache path mirrors audio cache layout:
  `{cache_dir}/{hash_prefix}/audio/lyrics.lrc`

### 2d. Pre-fetch vs on-song-switch

1. **Pre-fetch all** — After TUI launch, background worker downloads LRC for
   every song in the songset in parallel. Results populate
   `state.lrc_fetches` / `state.lrc_parsed`.
2. **On song switch** — When the user switches songs (n/p keys), all three
   panels refresh. If the pre-fetch worker hasn't populated the LRC entry yet,
   an on-demand fetch for just that song is triggered.

---

## Phase 3: State Extension

**Edit:** `ops/admin-cli/src/stream_of_worship/admin/component_editor/state.py`

### 3a. `SongSession` — `song` field already present

The `song` field was already added in the v4 implementation (line 40 of
`state.py`). No change needed here.

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
    lrc_fetch_error: Optional[str] = None  # global pre-fetch error, if any
```

### 3c. No changes to existing methods

`get_value()`, `set_value()`, `push_undo()`, `undo()`, `redo()`,
`clear_undo_stacks()`, `current`, `current_undo`, `current_redo`,
`get_selected_component()` — all unchanged.

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
    # Also trigger detail panel + hero refresh
    self._refresh_detail_panel()
    self._refresh_hero()
```

### 4f. Top panel is read-only

The `ComponentMetadataTable` action guard methods (`action_cursor_up/down/page_up/
page_down`) still call `self.screen._guard_active_edit()`. The table itself never
enters edit mode. The `e`, `[`, `]` keys are intercepted at the screen level and
only have effect when the detail panel is focused.

The `tab` / `shift+tab` bindings are repurposed from column navigation to
**panel focus cycling** (see Phase 8).

---

## Phase 5: Component Detail Panel — Bottom-Right

**New file:** `ops/admin-cli/src/stream_of_worship/admin/component_editor/detail_panel.py`

### 5a. Class

```python
from textual.widgets import Static
from rich.text import Text
from typing import Optional

from .constants import (
    EDITABLE_FIELDS,
    THEME_VALUES,
    VOCAL_POSTURE_VALUES,
    GROOVE_DENSITY_MIN,
    GROOVE_DENSITY_MAX,
    ENERGY_LEVEL_MIN,
    ENERGY_LEVEL_MAX,
)
from .state import SongSession, ComponentEditorState
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

        # -- Section: Song Info --
        text.append("-- Song Info --\n", style="bold cyan")
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

        # -- Section: Component Details --
        text.append(f"-- Component ({role}) --\n", style="bold cyan")
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

        # -- Section: Confidence Breakdown --
        text.append("-- Confidence Breakdown --\n", style="bold cyan")
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

        # -- Section: Editable Fields --
        text.append("-- Editable Fields --\n", style="bold yellow")
        for i, field in enumerate(EDITABLE_FIELDS):
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

        # -- Section: Reasoning --
        text.append("-- Reasoning --\n", style="bold cyan")
        reasoning_fields = [
            ("Theme", comp.theme_reasoning),
            ("Posture", comp.posture_reasoning),
        ]
        for label, value in reasoning_fields:
            text.append(f"  {label:12s}: ", style="dim")
            if value:
                text.append(f"{value}\n")
            else:
                text.append("—\n")

        text.append("\n")

        # -- Section: Lifecycle (dates at bottom) --
        text.append("-- Lifecycle --\n", style="bold cyan")
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

### 5d. Line offset computation (for Input overlay positioning)

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

**Note:** This offset is fragile if the rendering structure changes. The exact
line count should be verified during implementation and the constant `28`
adjusted if the rendering layout differs. A better approach for future versions
would be to use Textual's `RichLog` or individual `Label` widgets per line to
avoid manual offset computation.

### 5e. Rendered layout summary

```
-- Song Info --
  Title       : 耶穌愛你
  Artist      : 讚美之泉
  Lyricist    : —
  Album       : 敬拜讚美15
  Series      : 敬拜讚美
  Song Key    : G

-- Component (entry) --
  Type         : chorus
  Occurrence   : 1
  Start        : 0:32
  End          : 1:15
  BPM          : 128
  Key          : G
  Confidence   : 0.92
  Backbeat     : 0.75

-- Confidence Breakdown --
  BPM          : 0.95
  Key          : 0.88
  Groove       : 0.91
  Backbeat     : 0.82
  Energy       : 0.79
  Theme        : 0.85
  Posture      : 0.72

-- Editable Fields --
 ► theme          : 讚美 [◄ ►]
   vocal_posture  : To God [◄ ►]
   groove_density : 1.25 [e]
   energy_level   : -12 [e]

-- Reasoning --
  Theme        : The lyrics speak of praising God's love...
  Posture      : Directed to God as worship...

-- Lifecycle --
  Created      : 2026-08-10 14:23:01
  Updated      : 2026-08-11 09:15:33
```

---

## Phase 6: Lyrics Panel — Bottom-Left

**New file:** `ops/admin-cli/src/stream_of_worship/admin/component_editor/lyrics_panel.py`

### 6a. Class

```python
from textual.widgets import Static
from rich.text import Text
from typing import Optional

from ..services.lrc_parser import LRCParsedContent, format_centiseconds


class LyricsPanel(Static):
    """Bottom-left panel showing timestamped LRC lyrics for the current song.

    Features:
    - Renders LRC metadata header (ti, ar, al, etc.) in dim italic
    - Renders each timed line with a cyan timestamp column
    - Highlights the "current line" based on playback position (visual only,
      no auto-scroll-to-center)
    - Shows placeholder messages for loading / no-LRC / error states
    """

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
        self._highlighted_index: int = -1  # index into parsed.timed_lines
        self._parsed: Optional[LRCParsedContent] = None
```

### 6b. Rendering

```python
    def update_lrc(
        self,
        parsed: Optional[LRCParsedContent],
        song_title: str,
        highlighted_index: int = -1,
    ) -> None:
        self._song_title = song_title
        self._parsed = parsed
        self._highlighted_index = highlighted_index

        if parsed is None:
            self.add_class("empty")
            self.update(f'No LRC file found for "{song_title}"')
            return

        self.remove_class("empty")
        text = Text()

        # LRC metadata header
        if parsed.preserved_lines:
            for p in parsed.preserved_lines:
                if p.tag is not None:
                    text.append(f"[{p.tag}: {p.value}]\n", style="dim italic")
                elif p.raw.strip():
                    text.append(f"{p.raw}\n", style="dim italic")
            text.append("\n")

        # Timed lines with current-line highlight
        for i, line in enumerate(parsed.timed_lines):
            timestamp = (
                format_centiseconds(line.time_seconds)
                if line.time_seconds is not None
                else "--:--.--"
            )
            if i == highlighted_index:
                text.append(f"[{timestamp}]  ", style="bold cyan reverse")
                text.append(line.text + "\n", style="bold reverse")
            else:
                text.append(f"[{timestamp}]  ", style="cyan")
                text.append(line.text + "\n")

        self.update(text)
        # Do NOT auto-scroll — operator scrolls manually (Q2 decision: B)
```

### 6c. Current-line highlight update (without full re-render)

```python
    def set_highlighted_index(self, index: int) -> None:
        """Update the highlighted line index and re-render.

        Called by the screen's playback position callback. If the index
        hasn't changed, this is a no-op (avoids unnecessary re-renders
        at 5Hz playback update frequency).
        """
        if index == self._highlighted_index:
            return
        if self._parsed is None:
            return
        self.update_lrc(self._parsed, self._song_title, highlighted_index=index)
```

### 6d. Placeholder states

```python
    def update_fetching(self, song_title: str) -> None:
        self._parsed = None
        self._highlighted_index = -1
        self._song_title = song_title
        self.add_class("empty")
        self.update(f'Loading lyrics for "{song_title}"...')

    def update_error(self, msg: str, song_title: str) -> None:
        self._parsed = None
        self._highlighted_index = -1
        self._song_title = song_title
        self.add_class("empty")
        self.update(f'Error loading lyrics for "{song_title}": {msg}')
```

### 6e. Current-line computation

```python
    @staticmethod
    def compute_highlighted_index(
        parsed: Optional[LRCParsedContent],
        position_seconds: float,
    ) -> int:
        """Compute the index of the LRC line that corresponds to the given
        playback position.

        Returns the index of the last timed line whose time_seconds <= position.
        Returns -1 if no line matches (position before first line) or if
        parsed is None / empty.
        """
        if parsed is None or not parsed.timed_lines:
            return -1
        result = -1
        for i, line in enumerate(parsed.timed_lines):
            if line.time_seconds is None:
                continue
            if line.time_seconds <= position_seconds:
                result = i
            else:
                break
        return result
```

### 6f. Rendering details

- Timestamp column rendered in `cyan` style for visual separation
- LRC metadata header (`[ti:]`, `[ar:]`, `[al:]`) rendered in `dim italic`
- Empty lyric lines preserved (rendered as just the timestamp)
- Current line rendered with `bold reverse` (highlight) on both timestamp and text
- `empty` CSS class applies muted color + centered text for placeholders
- `:focus` CSS adds a right border highlight when lyrics panel is active
- No auto-scroll on highlight change (Q2 decision: B — visual highlight only)

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

        # Top panel: Hero Panel + compact read-only table
        with Vertical(id="top-panel"):
            yield ComponentHeroPanel()
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

/* Top panel: Hero Panel (auto height) + compact table (fixed mini-height) */
#top-panel {
    height: auto;
    max-height: 20;  /* Hero (~6 rows) + table header + 2 data rows + padding */
    overflow: hidden;
    border-bottom: solid $primary;
}

#component-table {
    height: 6;  /* ~header(1) + 2 data rows(2) + padding/border(3) */
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

`#top-panel` = auto height (Hero Panel renders ~6 rows, table renders ~4 rows),
`#bottom-split` = `1fr` (fills remaining space).
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

### 7e. `on_mount()` — table setup, detail panel, lyrics panel, LRC pre-fetch

```python
def on_mount(self) -> None:
    self._setup_table()
    self._refresh_table()
    self._refresh_detail_panel()
    self._refresh_hero()
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

    # LRC pre-fetch (from v2)
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
            parsed = self.state.lrc_parsed[song_id]
            # Compute current highlight from playback position
            pos = self.playback.position_seconds or 0.0
            idx = LyricsPanel.compute_highlighted_index(parsed, pos)
            panel.update_lrc(parsed, song_title, highlighted_index=idx)
        return

    if self.state.lrc_prefetch_in_progress:
        panel.update_fetching(song_title)
        self._fetch_lrc_on_demand(song_id, session.hash_prefix, song_title)
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
async def _fetch_lrc_on_demand(
    self, song_id: str, hash_prefix: str, song_title: str
) -> None:
    if song_id in self.state.lrc_parsed:
        return
    try:
        fetch = await fetch_lrc_for_song(
            song_id, hash_prefix, self.r2_client, self.cache_dir,
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

## Phase 8: Playback-Synced Lyric Highlight

**Edit:** `ops/admin-cli/src/stream_of_worship/admin/component_editor/screen.py`

### 8a. Playback position callback — update lyric highlight

The existing `_on_playback_position` callback is extended to update the
lyrics panel's highlighted line:

```python
def _on_playback_position(self, position) -> None:
    self._update_playback_bar()
    self._update_lyrics_highlight()
```

### 8b. `_update_lyrics_highlight()`

```python
def _update_lyrics_highlight(self) -> None:
    """Update the lyrics panel's current-line highlight based on playback
    position. Called at 5Hz (every 0.2s) from the position update timer.

    Uses LyricsPanel.set_highlighted_index() which is a no-op if the index
    hasn't changed, avoiding unnecessary re-renders.
    """
    try:
        panel = self.query_one("#lyrics-panel", LyricsPanel)
    except NoMatches:
        return
    session = self.state.current
    if session is None:
        return
    parsed = self.state.lrc_parsed.get(session.song_id)
    if parsed is None:
        return
    pos = self.playback.position_seconds or 0.0
    idx = LyricsPanel.compute_highlighted_index(parsed, pos)
    panel.set_highlighted_index(idx)
```

### 8c. Seek-to-component → jump lyric highlight

When `space` (D3) or `j` seeks to a component's `start_time`, the lyric
highlight should immediately jump to the first LRC line whose timestamp is
≥ `start_time`. This is achieved by calling `_update_lyrics_highlight()`
after the seek, which computes the new index from the (now-updated) playback
position.

Update `action_toggle_playback_for_component` and `action_jump_to_component`
to call `_update_lyrics_highlight()` after seeking:

```python
def action_toggle_playback_for_component(self) -> None:
    if self._guard_active_edit():
        return
    if self.playback.is_playing:
        self.playback.pause()
        return
    comp = self.state.get_selected_component()
    if comp is not None:
        pos = self.playback.position_seconds or 0.0
        start = comp.start_time or 0.0
        end = comp.end_time if comp.end_time is not None else float("inf")
        inside = start <= pos <= end
        if not inside:
            self.playback.seek(comp.start_time or 0.0)
            self._update_lyrics_highlight()  # NEW: jump highlight to component start
    self.playback.play()

def action_jump_to_component(self) -> None:
    if self._guard_active_edit():
        return
    comp = self.state.get_selected_component()
    if comp is None or comp.start_time is None:
        self.app.bell()
        return
    self.playback.seek(comp.start_time)
    self._update_playback_bar()
    self._update_lyrics_highlight()  # NEW: jump highlight to component start
```

### 8d. Position update timer — also updates lyrics

The existing `_start_position_updates()` timer loop (every 0.2s) already
calls `_update_playback_bar()`. Extend it to also update the lyrics highlight:

```python
def _start_position_updates(self) -> None:
    async def _update_loop():
        while True:
            await asyncio.sleep(0.2)
            self._update_playback_bar()
            self._update_lyrics_highlight()  # NEW

    self._position_update_timer = asyncio.ensure_future(_update_loop())
```

---

## Phase 9: Panel Navigation & Key Bindings

**Edit:** `ops/admin-cli/src/stream_of_worship/admin/component_editor/screen.py`

### 9a. Binding changes

The `tab` and `shift+tab` bindings are repurposed from table column navigation
(v4) to **panel focus cycling** (v5):

```python
BINDINGS = [
    # Playback / Nav (global — work on any panel)
    Binding("space", "toggle_playback_for_component", "Play/Pause"),
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

### 9b. Binding groups (footer)

```python
BINDING_GROUPS: dict[str, list[str]] = {
    "Playback": [
        "toggle_playback_for_component",
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

### 9c. Panel cycling actions

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

### 9d. Detail panel field navigation

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

### 9e. Edit actions — context-aware

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
    self._refresh_hero()
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
    self._refresh_hero()
    self._update_status()
```

### 9f. Input overlay repositioning

The `_show_value_edit_input` method is adapted to position the Input overlay
over the detail panel's focused editable field line instead of a table cell.

```python
def _show_value_edit_input(self, role: str, field: str, initial_text: str) -> None:
    detail_panel = self.query_one("#detail-panel", ComponentDetailPanel)

    def do_show() -> None:
        panel_region = detail_panel.region
        scroll_y = detail_panel.scroll_y

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

### 9g. `on_input_submitted` — updated

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
    self._refresh_hero()
    self._do_autosave()
    self._update_status()
    # Refocus the detail panel (not the table)
    self.query_one("#detail-panel", ComponentDetailPanel).focus()
```

### 9h. `on_resize` — updated

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

### 9i. `_cancel_row_edit` — updated

```python
def _cancel_row_edit(self) -> None:
    self._hide_row_edit_input()
    try:
        self.query_one("#detail-panel", ComponentDetailPanel).focus()
    except NoMatches:
        pass
```

### 9j. Remove v4 column navigation actions

The v4 `action_cursor_right` and `action_cursor_left` methods (which forwarded
to the DataTable's column cursor) are **removed** — `tab` / `shift+tab` now
cycle panels, not columns.

---

## Phase 10: Song Switch Wiring — 3-Panel Refresh

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
    self._refresh_hero()
    self._update_breadcrumb()
    self._update_status()
```

After undo/redo and save, both table, detail panel, and hero must refresh:

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
    self._refresh_hero()
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
    self._refresh_hero()
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
    self._refresh_hero()
    self._notify("[green]Saved (DB + R2).[/]")
```

---

## Phase 11: Autosave Interplay

The existing autosave (`ComponentAutosaveState`) persists `selected_row` and
`selected_column_key`. In v5:
- `selected_row` is still used (entry/exit row selection) — persisted as-is.
- `selected_column_key` is no longer directly meaningful (no table column
  navigation), but it is retained for backward-compat. The detail panel's
  `_focus_idx` is NOT persisted in autosave — it resets to 0 on each TUI launch.
  This is an acceptable trade-off; the user can simply press down-arrow to
  reach the desired field.

Add `_refresh_detail_panel()` and `_refresh_lyrics_panel()` to the end of
`_maybe_apply_autosave()`:

```python
def _maybe_apply_autosave(self) -> None:
    # ... existing logic ...
    self._refresh_table()
    self._refresh_detail_panel()  # NEW
    self._refresh_lyrics_panel()  # NEW
    self._refresh_hero()
    self._update_status()
```

---

## Phase 12: Reuse Existing Modules (no edits)

| Module | Reused for |
|---|---|
| `services/lrc_parser.py` | `parse_lrc_full(content) -> LRCParsedContent`, `format_centiseconds()`, `format_duration()` |
| `services/r2.py` | `get_lrc_identity()`, `download_lrc_content()` |
| `services/asset_cache.py` | `download_lrc()` (alternative simpler path) |
| `editor/footer.py` | `GroupedFooter`, `format_key_display` (unchanged) |
| `services/playback.py` | `PlaybackService` (unchanged) |
| `db/client.py` | `get_song()`, `get_song_components_entry_exit()`, `update_song_component_fields_txn()` (unchanged) |
| `db/models.py` | `Song`, `SongComponent` data models (unchanged) |
| `component_editor/state.py` | `ComponentEditorState`, `SongSession` (extended with LRC fields) |
| `component_editor/autosave.py` | `ComponentAutosaveState`, `save_autosave()`, `load_autosave()`, `clear_autosave()` (unchanged) |
| `component_editor/constants.py` | `HERO_PRIMARY_FIELDS`, `HERO_REASONING_FIELDS` (unchanged, used by Hero Panel) |

---

## Phase 13: Edge Cases

| Case | Handling |
|---|---|
| No LRC in R2 for song | `LRCFetch(content=None)` → lyrics panel shows `No LRC file found for "{title}"` |
| R2 fetch error (network, auth) | `LRCFetch(error=msg)` → lyrics panel shows error message |
| User switches song mid-pre-fetch | Lyrics panel shows `Loading lyrics...`; on-demand fetch triggered for current song |
| Multi-song songset | Per-song LRC tracked in `state.lrc_fetches` / `state.lrc_parsed`; all panels re-render on switch |
| Empty LRC (file exists, no timed lines) | `parse_lrc_full` returns `LRCParsedContent` with empty `timed_lines`; lyrics panel renders metadata header only; highlight index = -1 |
| Pre-fetch worker crashes | `lrc_prefetch_in_progress` set `False` in `finally`; lyrics panel falls back to on-demand fetch |
| On-demand fetch for song already in progress | Early return if `song_id in state.lrc_parsed` |
| User switches away from song during on-demand fetch | Fetch completes, populates state, but panels only refresh if song is still current |
| Playback position before first LRC line | `compute_highlighted_index` returns -1; no line highlighted |
| Playback position after last LRC line | Last line stays highlighted |
| Selected role has no component (None) | Detail panel shows `[No component for this role]`; editing keys (`e`, `[`, `]`) are no-ops (guarded by `_guard_no_component`) |
| User presses `e` on non-numeric field | No-op — `action_edit_numeric` checks `field not in ("groove_density", "energy_level")` |
| User presses `[`/`]` on non-enum field | No-op — `action_cycle_field_*` checks `field not in ("theme", "vocal_posture")` |
| User presses editing keys while top panel is focused | No-op — all edit actions check `self._active_panel != "details"` |
| Input overlay can't compute field line position | Shows warning notification `"Scroll to the field to edit"`; focus returns to detail panel |
| `song` is None on SongSession | Detail panel falls back to `session.song_title` for the Title field; other song fields show `—` |
| Terminal resized while editing | `on_resize` repositions the Input overlay relative to the detail panel's current scroll position |
| Detail panel scrolled past the focused field | Input overlay position computation detects the field is out of view; shows `"Scroll to the field to edit"` warning |
| Autosave recovery | Detail + lyrics panels re-render from restored state after `_maybe_apply_autosave()` |

---

## Files Changed

| File | Change type | Est. LOC |
|---|---|---|
| `component_editor/constants.py` | **Edit** — add `COMPACT_TABLE_COLUMNS` | ~15 |
| `component_editor/lrc_fetch.py` | **New** — LRC fetch helpers | ~80 |
| `component_editor/lyrics_panel.py` | **New** — `LyricsPanel(Static)` widget with playback-synced highlight | ~120 |
| `component_editor/detail_panel.py` | **New** — `ComponentDetailPanel(Static)` widget with rendering + field navigation | ~150 |
| `component_editor/state.py` | **Edit** — add `lrc_fetches`/`lrc_parsed`/`lrc_prefetch_in_progress`/`lrc_fetch_error` to `ComponentEditorState` | ~15 |
| `component_editor/screen.py` | **Edit** — `compose()` 3-panel layout, CSS, `on_mount` worker, panel cycling, detail panel refresh, lyrics panel refresh + highlight, edit action context-awareness, Input repositioning, song switch wiring, binding changes, autosave interplay, remove v4 column nav | ~300 |
| `commands/audio.py` | **No change** — `song=song` already passed to `SongSession` (line 5465) | 0 |

**Total estimated additions:** ~680 LOC across 3 new files + 3 edited files.

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
- [ ] Top panel spans full width with Hero Panel + compact numerical columns
- [ ] Top panel table has only 9 compact columns (no dates, no long text)
- [ ] Bottom-left and bottom-right panels are 50/50 width
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
- [ ] Current line is highlighted (bold reverse) during playback
- [ ] Highlight advances as playback progresses
- [ ] No auto-scroll (highlight may go off-screen; operator scrolls manually)
- [ ] Pressing `space` (seek to component start) jumps the highlight to the
      first LRC line ≥ component start_time
- [ ] Pressing `j` (seek only) also jumps the highlight

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
- [ ] Undo/redo refreshes table, detail panel, and hero panel
- [ ] Save (DB + R2) refreshes table, detail panel, and hero panel

**Cross-panel:**
- [ ] Playback commands (space, left/right, j) work from any panel
- [ ] Song switch (n/p) refreshes all 3 panels + hero + breadcrumb + status
- [ ] Save (s) works from any panel
- [ ] Quit dialog (q/escape) works from any panel

**Edge cases:**
- [ ] Song without `song` object shows `—` for artist/album fields
- [ ] Component is None (no entry/exit row) → detail panel shows error message
- [ ] R2 fetch errors show error message in lyrics panel
- [ ] Terminal resize repositions the Input overlay correctly
- [ ] Autosave recovery refreshes table, detail panel, lyrics panel, and hero

### Automated tests

- `tests/test_lrc_fetch.py` — mock R2 client, test `fetch_lrc_for_song` and `prefetch_all_lrc`
- `tests/test_lyrics_panel.py` — test `LyricsPanel.update_lrc` rendering with various `LRCParsedContent` inputs; test `compute_highlighted_index` with various positions; test `set_highlighted_index` no-op when index unchanged
- `tests/test_detail_panel.py` — test `ComponentDetailPanel.update_detail` rendering with various component + song inputs, `move_focus_up/down`, `get_editable_field_line_offset`
- `tests/test_compact_table.py` — test that `_setup_table` uses `COMPACT_TABLE_COLUMNS`, `_refresh_table` builds correct rows

---

## Open Questions

None — all clarified via Q&A.
