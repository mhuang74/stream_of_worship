---

# Implementation Plan: Component Editor — Bottom Split Lyrics Panel (v1)

> **Date:** 2026-08-12
> **Branch:** TBD
> **Spec ID:** `component-editor-lyrics-split-panel-v1`
> **Status:** Planning — not yet implemented

---

## Goal

Enhance the Admin CLI `audio review-components` TUI editor with a bottom
split panel that displays the timestamped lyrics (LRC file) for the
currently-selected song. The panel is always visible (when an LRC exists),
uses a 40/60 vertical split (editor top=40%, lyrics bottom=60%), and shows
a placeholder when no LRC is available for the song.

## Non-Goals

- No playback auto-sync / auto-seek of the lyrics panel.
- No bi-directional click-to-seek from lyrics to audio.
- No editing of LRC content from this panel (read-only display).
- No toggle keybinding to hide the panel — always visible.
- No changes to the `audio edit-lrc` editor (sibling TUI).

## User Decisions (from clarification Q&A)

| Question | Decision |
|---|---|
| Lyrics source | R2 + local cache (download via `r2.download_lrc_content`, cached under same `cache_dir` as audio) |
| Playback sync | Static display only (no auto-advance) |
| Split proportion | 40/60 — editor top=40%, lyrics bottom=60% |
| Missing LRC | Show placeholder text in lyrics panel |
| LRC loading trigger | On song switch (async/non-blocking) |
| Panel visibility | Always visible |
| Pre-fetch strategy | Pre-fetch all songs' LRC upfront in parallel after launch |

### Reconciliation: pre-fetch vs on-song-switch

These two decisions are complementary, not contradictory:

1. **Pre-fetch all** — After TUI launch, kick off a background worker that
   downloads LRC files for every song in the songset in parallel. Results
   populate `state.lrc_fetches` / `state.lrc_parsed`.
2. **On song switch** — When the user switches songs (n/p keys), the lyrics
   panel is refreshed from `state.lrc_parsed[song_id]`. If the pre-fetch
   worker has not yet populated that entry (race condition), trigger an
   immediate on-demand fetch for just that one song, then refresh.

This gives fast startup (TUI renders immediately) plus guaranteed LRC
availability for the current song even if pre-fetch is still in flight.

---

## Architecture Overview

### Current Layout (single vertical stack)

```
┌─────────────────────────────────────┐
│ Header                              │
├─────────────────────────────────────┤
│ Vertical(#editor-body)              │
│   SongBreadcrumb                    │
│   PlaybackBar                       │
│   ComponentMetadataTable (1fr)      │
│   Input(#row-edit-input, hidden)    │
│   StatusIndicator                   │
├─────────────────────────────────────┤
│ GroupedFooter (docked bottom)       │
└─────────────────────────────────────┘
```

### Proposed Layout (40/60 vertical split)

```
┌─────────────────────────────────────┐
│ Header                              │
├─────────────────────────────────────┤
│ Vertical(#editor-body)              │
│   SongBreadcrumb                    │
│   PlaybackBar                       │
│   ┌─────────────────────────────┐   │
│   │ Vertical(#editor-top) 2fr   │   │  ← 40%
│   │   ComponentMetadataTable    │   │
│   │   Input(#row-edit-input)    │   │
│   │   StatusIndicator           │   │
│   └─────────────────────────────┘   │
│   ┌─────────────────────────────┐   │
│   │ LyricsPanel(#lyrics-panel)  │   │
│   │                        3fr  │   │  ← 60%
│   │   [mm:ss.cc]  lyric_text     │   │
│   │   [mm:ss.cc]  lyric_text     │   │
│   │   ...                        │   │
│   └─────────────────────────────┘   │
├─────────────────────────────────────┤
│ GroupedFooter (docked bottom)       │
└─────────────────────────────────────┘
```

---

## Phase 1: LRC Fetch Service

**New file:** `ops/admin-cli/src/stream_of_worship/admin/component_editor/lrc_fetch.py`

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

- `services/r2.py` → `get_lrc_identity()`, `download_lrc_content()`
- `services/asset_cache.py` → `download_lrc()` (alternative simpler path)
- Local cache path mirrors audio cache layout:
  `{cache_dir}/{hash_prefix}/audio/lyrics.lrc`

---

## Phase 2: State Extension

**Edit:** `ops/admin-cli/src/stream_of_worship/admin/component_editor/state.py`

Add fields to `ComponentEditorState`:

```python
from .lrc_fetch import LRCFetch
from ..services.lrc_parser import LRCParsedContent

@dataclass
class ComponentEditorState:
    # ... existing fields ...

    # New: LRC fetch + parsed content per song
    lrc_fetches: dict[str, LRCFetch] = field(default_factory=dict)
    lrc_parsed: dict[str, Optional[LRCParsedContent]] = field(default_factory=dict)
    lrc_prefetch_in_progress: bool = False
```

### Population

- `lrc_fetches` populated by `prefetch_all_lrc` worker
- `lrc_parsed` populated by parsing each `LRCFetch.content` via
  `parse_lrc_full(content)` from `services/lrc_parser.py`
- `lrc_prefetch_in_progress` set `True` at worker start, `False` at completion

### No changes to existing fields

`SongSession`, `ComponentUndoEntry`, `selected_row`, `selected_column_key`,
etc. are unchanged.

---

## Phase 3: LRC Display Widget

**New file:** `ops/admin-cli/src/stream_of_worship/admin/component_editor/lyrics_panel.py`

### Class

```python
from textual.widgets import Static
from textual.reactive import reactive
from rich.text import Text
from ..services.lrc_parser import LRCParsedContent, format_centiseconds


class LyricsPanel(Static):
    """Bottom split panel showing timestamped LRC lyrics for the current song."""

    DEFAULT_CSS = """
    LyricsPanel {
        height: 3fr;
        border-top: solid $primary;
        padding: 0 1;
        overflow-y: auto;
        background: $surface;
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
        """Render timestamped lyrics or placeholder."""
        self._song_title = song_title
        if parsed is None:
            self.add_class("empty")
            self.update(f'No LRC file found for "{song_title}"')
            return
        self.remove_class("empty")
        text = Text()
        # Optional: render LRC metadata header (ti:, ar:, al:) if present
        if parsed.metadata:
            for key, value in parsed.metadata.items():
                text.append(f"[{key}: {value}]\n", style="dim italic")
            text.append("\n")
        # Render each timed line: [mm:ss.cc]  lyric_text
        for line in parsed.timed_lines:
            timestamp = format_centiseconds(line.time_cs) if line.time_cs is not None else "--:--.--"
            text.append(f"[{timestamp}]  ", style="cyan")
            text.append(line.text + "\n")
        self.update(text)
        self.scroll_home(animate=False)  # scroll to top

    def update_fetching(self, song_title: str) -> None:
        """Show loading state during pre-fetch."""
        self.add_class("empty")
        self.update(f'Loading lyrics for "{song_title}"...')

    def update_error(self, msg: str, song_title: str) -> None:
        """Show error state."""
        self.add_class("empty")
        self.update(f'Error loading lyrics for "{song_title}": {msg}')
```

### Rendering details

- Timestamp column rendered in `cyan` style for visual separation
- LRC metadata header (`[ti:]`, `[ar:]`, `[al:]`) rendered in `dim italic`
  at the top, separated by a blank line
- Empty lyric lines preserved (rendered as just the timestamp)
- Auto-scrolls to top on each update
- `empty` CSS class applies muted color + centered text for placeholders

---

## Phase 4: Screen Layout Changes

**Edit:** `ops/admin-cli/src/stream_of_worship/admin/component_editor/screen.py`

### 4a. Imports

```python
from .lyrics_panel import LyricsPanel
from .lrc_fetch import prefetch_all_lrc, fetch_lrc_for_song
from ..services.lrc_parser import parse_lrc_full
from textual.work import work
```

### 4b. `compose()` update

```python
def compose(self) -> ComposeResult:
    yield Header()
    with Vertical(id="editor-body"):
        yield SongBreadcrumb()
        yield PlaybackBar()
        with Vertical(id="editor-top"):
            yield ComponentMetadataTable(id="component-table")
            yield Input(id="row-edit-input", placeholder="Edit numeric value", ...)
            yield StatusIndicator()
        yield LyricsPanel(id="lyrics-panel")
    yield GroupedFooter()
```

### 4c. `DEFAULT_CSS` update

```css
ComponentEditorScreen {
    layout: vertical;
}

#editor-body {
    height: 1fr;
    overflow: hidden;
}

#editor-top {
    height: 2fr;       /* 40% of body */
    overflow: hidden;
}

#component-table {
    height: 1fr;
}

#row-edit-input {
    display: none;
    height: 1;
    layer: overlay;
}

#lyrics-panel {
    height: 3fr;       /* 60% of body */
    border-top: solid $primary;
    padding: 0 1;
    overflow-y: auto;
    background: $surface;
}

#lyrics-panel.empty {
    color: $text-muted;
    text-align: center;
}
```

`#editor-top` = `2fr`, `#lyrics-panel` = `3fr` → 40/60 split.

### 4d. `on_mount` — kick off pre-fetch worker

```python
def on_mount(self) -> None:
    # ... existing setup ...
    self.state.lrc_prefetch_in_progress = True
    self._refresh_lyrics_panel()  # shows "Loading lyrics..." initially
    self._prefetch_lrc()

@work(exclusive=True, group="lrc-fetch")
async def _prefetch_lrc(self) -> None:
    """Background pre-fetch of all songs' LRC files in parallel."""
    try:
        fetches = await prefetch_all_lrc(
            self.state.song_sessions,
            self.state.r2_client,
            self.state.cache_dir,
        )
        for song_id, fetch in fetches.items():
            self.state.lrc_fetches[song_id] = fetch
            self.state.lrc_parsed[song_id] = (
                parse_lrc_full(fetch.content) if fetch.content else None
            )
    except Exception as exc:
        # Log error; individual song errors are in LRCFetch.error
        self.state.lrc_fetch_error = str(exc)
    finally:
        self.state.lrc_prefetch_in_progress = False
        self._refresh_lyrics_panel()
```

### 4e. `_refresh_lyrics_panel()` — render current song's lyrics

```python
def _refresh_lyrics_panel(self) -> None:
    """Update the lyrics panel for the currently-selected song."""
    panel = self.query_one("#lyrics-panel", LyricsPanel)
    session = self.state.current_song_session()
    if session is None:
        panel.update_lrc(None, "")
        return

    song_id = session.song.song_id
    song_title = session.song.title

    # Case 1: LRC already parsed (pre-fetch completed for this song)
    if song_id in self.state.lrc_parsed:
        fetch = self.state.lrc_fetches.get(song_id)
        if fetch and fetch.error:
            panel.update_error(fetch.error, song_title)
        else:
            panel.update_lrc(self.state.lrc_parsed[song_id], song_title)
        return

    # Case 2: Pre-fetch still in progress
    if self.state.lrc_prefetch_in_progress:
        panel.update_fetching(song_title)
        # Trigger on-demand fetch for this song as fallback
        self._fetch_lrc_on_demand(song_id, song_title)
        return

    # Case 3: Pre-fetch done but this song has no entry (shouldn't happen
    # if prefetch_all_lrc covers all song_sessions, but handle gracefully)
    panel.update_lrc(None, song_title)
```

### 4f. On-demand fetch fallback

```python
@work(exclusive=False, group="lrc-fetch-on-demand")
async def _fetch_lrc_on_demand(self, song_id: str, song_title: str) -> None:
    """Fetch a single song's LRC if pre-fetch hasn't reached it yet."""
    if song_id in self.state.lrc_parsed:
        return  # already available
    try:
        fetch = await fetch_lrc_for_song(
            song_id, self.state.r2_client, self.state.cache_dir,
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
    # Only refresh if this song is still the current one
    current = self.state.current_song_session()
    if current and current.song.song_id == song_id:
        self._refresh_lyrics_panel()
```

### 4g. Song switch wiring

Update `action_next_song` / `action_prev_song` (or the shared
`_switch_song` helper if one exists):

```python
def _switch_song(self, direction: int) -> None:
    # ... existing song switch logic ...
    self._refresh_breadcrumb()
    self._refresh_table()
    self._refresh_lyrics_panel()  # NEW: update lyrics panel
    # ... playback reload ...
```

---

## Phase 5: No Changes to `commands/audio.py`

The `review_components` command (`commands/audio.py:4516`) constructs
`ComponentEditorState`, `PlaybackService`, and `ComponentEditorApp` and
calls `app.run()`. No changes are needed here because:

- `ComponentEditorState` is created with default empty `lrc_fetches` /
  `lrcrc_parsed` dicts (populated lazily by the screen's `on_mount` worker)
- `r2_client` and `cache_dir` are already passed to the state (or screen)
  for audio download — the same references are reused for LRC download
- The TUI renders immediately while LRC pre-fetch runs in the background

---

## Phase 6: Reuse Existing Modules (no edits)

| Module | Reused for |
|---|---|
| `services/lrc_parser.py` | `parse_lrc_full(content) -> LRCParsedContent`, `format_centiseconds()` |
| `services/r2.py` | `get_lrc_identity()`, `download_lrc_content()` |
| `services/asset_cache.py` | `download_lrc()` (alternative simpler path) |
| `editor/footer.py` | `GroupedFooter` (already shared, unchanged) |

---

## Phase 7: Edge Cases

| Case | Handling |
|---|---|
| No LRC in R2 for song | `LRCFetch(content=None)` → panel shows `No LRC file found for "{title}"` |
| R2 fetch error (network, auth) | `LRCFetch(error=msg)` → panel shows `Error loading lyrics for "{title}": {msg}` |
| User switches song mid-pre-fetch | Panel shows `Loading lyrics for "{title}"...`; on-demand fetch triggered for current song |
| Multi-song songset | Per-song LRC tracked in `state.lrc_fetches` / `state.lrc_parsed`; panel re-renders on switch |
| Empty LRC content (file exists but no timed lines) | `parse_lrc_full` returns `LRCParsedContent` with empty `timed_lines`; panel renders metadata header only (if any) |
| LRC with only metadata tags (`[ti:]`, `[ar:]`, etc.) | Metadata header rendered in `dim italic` at top; no timed lines below |
| Pre-fetch worker crashes | `lrc_prefetch_in_progress` set `False` in `finally`; panel falls back to on-demand fetch |
| On-demand fetch for song already in progress | Early return if `song_id in state.lrc_parsed` |
| User switches away from song during on-demand fetch | Fetch completes and populates state, but panel only refreshes if song is still current |

---

## Files Changed

| File | Change type | Est. LOC |
|---|---|---|
| `component_editor/lrc_fetch.py` | **New** — LRC fetch helpers | ~80 |
| `component_editor/lyrics_panel.py` | **New** — `LyricsPanel(Static)` widget | ~70 |
| `component_editor/state.py` | **Edit** — add `lrc_fetches`, `lrc_parsed`, `lrc_prefetch_in_progress` fields | ~15 |
| `component_editor/screen.py` | **Edit** — `compose()` split, CSS, `on_mount` worker, `_refresh_lyrics_panel`, song switch wiring, on-demand fetch | ~120 |
| `commands/audio.py` | **No change** | 0 |

**Total estimated additions:** ~285 LOC across 2 new files + 2 edited files.

---

## Testing

### Manual verification

```bash
# Songs with LRC available
uv run --project ops/admin-cli --extra admin sow-admin audio review-components <song_id_with_lrc>

# Songs without LRC
uv run --project ops/admin-cli --extra admin sow-admin audio review-components <song_id_without_lrc>

# Multi-song songset (test pre-fetch + on-demand)
uv run --project ops/admin-cli --extra admin sow-admin audio review-components <id1> <id2> <id3>
```

### Verification checklist

- [ ] TUI launches immediately (no blocking on LRC fetch)
- [ ] Lyrics panel shows "Loading lyrics..." initially
- [ ] After pre-fetch completes, current song's lyrics appear with timestamps
- [ ] Switching songs (n/p) updates the lyrics panel
- [ ] Songs without LRC show placeholder text
- [ ] 40/60 split is visually correct (lyrics panel larger than editor)
- [ ] Lyrics panel scrolls independently (overflow-y: auto)
- [ ] LRC metadata header (`[ti:]`, `[ar:]`) renders in dim italic
- [ ] Timestamps render in cyan
- [ ] R2 fetch errors show error message in panel
- [ ] On-demand fetch works when switching to a song not yet pre-fetched

### Automated tests

If `component_editor/` has an existing test directory, add:

- `tests/test_lrc_fetch.py` — mock R2 client, test `fetch_lrc_for_song` and `prefetch_all_lrc`
- `tests/test_lyrics_panel.py` — test `LyricsPanel.update_lrc` rendering with various `LRCParsedContent` inputs

---

## Open Questions

None — all clarified via Q&A.
