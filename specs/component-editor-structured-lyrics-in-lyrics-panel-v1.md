---

# Implementation Plan: Component Editor — Structured Lyrics in Lyrics Panel (v1)

> **Date:** 2026-08-16
> **Branch:** TBD
> **Spec ID:** `component-editor-structured-lyrics-in-lyrics-panel-v1`
> **Status:** Planning — not yet implemented

---

## Goal

Enhance the Component Metadata Editor TUI's **Lyrics Panel** (right panel,
lyrics mode) by appending the song's **Structured Lyrics** below the
existing timestamped LRC lyrics, within the **same scrollable panel**. The
user scrolls down past the LRC lines to see the structured section-tagged
lyrics (Verse, Chorus, Bridge, etc.) sourced from
`recordings.structured_lyrics` in the database.

This lets the reviewer cross-reference the structured section labels
against the detected components (entry=chorus, verse1=verse,
bridge=bridge) without switching screens.

## Non-Goals

- No editing of structured lyrics from this panel (read-only display).
- No mapping/overlay of structured sections onto component time ranges.
- No changes to the Detail Panel or Hero Panel.
- No changes to details mode — the structured lyrics appear only in lyrics mode.
- No fetching of R2 `components.json` (which has per-component `lyrics_excerpt`
  and `section_label`). The structured lyrics come from the DB
  `recordings.structured_lyrics` column.

## User Decisions (from clarification Q&A)

| Question | Decision |
|---|---|
| Data source | `Recording.structured_lyrics` (DB) — raw section-tagged lyrics from YouTube description, already parsed into JSON |
| Layout | Single scrollable panel — LRC lyrics on top, structured lyrics appended below within the same `ScrollView`. User scrolls down to see structured lyrics. No separate panel or split. |
| Visibility scope | Lyrics mode only (when `v` cycles right panel to lyrics). Details mode unaffected. |

---

## Architecture Overview

### Current Lyrics Panel (lyrics mode)

```
┌─────────────────────────────────┐
│ LyricsPanel (ScrollView)        │
│   [ti: Song Title]               │  ← metadata header (dim italic)
│   [ar: Artist]                   │
│                                  │
│   [00:12.34]  First lyric line   │  ← timed lines (cyan timestamp)
│   [00:18.56]  Second lyric line  │
│   ...                            │
│   [03:45.12]  Last lyric line    │
│                                  │  ← (end of content)
└─────────────────────────────────┘
```

### Proposed Lyrics Panel (lyrics mode)

```
┌─────────────────────────────────┐
│ LyricsPanel (ScrollView)        │
│   [ti: Song Title]               │  ← metadata header (dim italic)
│   [ar: Artist]                   │
│                                  │
│   [00:12.34]  First lyric line   │  ← timed lines (cyan timestamp)
│   [00:18.56]  Second lyric line  │
│   ...                            │
│   [03:45.12]  Last lyric line    │
│                                  │
│ ════════════════════════════════ │  ← separator line (dim)
│   -- Structured Lyrics --        │  ← section header (bold cyan)
│                                  │
│   [Verse]                        │  ← section label (bold magenta)
│   First verse line              │  ← lyric lines (normal)
│   Second verse line             │
│                                  │
│   [Chorus]                       │
│   First chorus line             │
│   ...                            │
│                                  │
│   [Bridge]                       │
│   Bridge line                    │
│                                  │
│   (or "No structured lyrics     │  ← placeholder if None
│    found for this song")        │
└─────────────────────────────────┘
```

The structured lyrics are part of the same `ScrollView` content — the
user scrolls down past the last LRC timed line to see them. Playback
auto-scroll/highlight continues to work for LRC lines only (the structured
lyrics are not timestamped and not highlightable).

---

## Phase 1: State Extension

**Edit:** `ops/admin-cli/src/stream_of_worship/admin/component_editor/state.py`

### 1a. Add `structured_lyrics` field to `SongSession`

```python
@dataclass
class SongSession:
    # ... existing fields ...

    # NEW: Parsed structured lyrics dict (from recordings.structured_lyrics JSON),
    # or None if not available. Format:
    #   {"sections": [{"label": str, "raw_label": str, "lines": [str]}],
    #    "preamble_lines": [str]}
    structured_lyrics: dict | None = None
```

### 1b. No changes to `ComponentEditorState`

The `ComponentEditorState` already has `lrc_parsed` and `lrc_fetches` dicts.
Structured lyrics are stored per-session on `SongSession` (since they come
from the DB, not R2, and are available at session construction time).

---

## Phase 2: Session Construction

**Edit:** `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py`
(around line 5472)

The `Recording` object is already fetched at line 5429
(`db_client.get_recording_by_song_id(song_id)`). Currently only
`recording.hash_prefix` and `recording.duration_seconds` are projected
into the `SongSession`. Add `structured_lyrics`:

```python
import json

# Inside the session-build loop, before SongSession construction:
structured_lyrics_dict: dict | None = None
if recording.structured_lyrics:
    try:
        structured_lyrics_dict = json.loads(recording.structured_lyrics)
    except (json.JSONDecodeError, TypeError):
        structured_lyrics_dict = None

sessions.append(
    SongSession(
        song_id=song_id,
        song_title=song.title,
        hash_prefix=recording.hash_prefix,
        audio_path=str(audio_path),
        audio_duration=recording.duration_seconds,
        components=components,
        entry_component=entry,
        exit_component=exit_comp,
        song=song,
        structured_lyrics=structured_lyrics_dict,  # NEW
    )
)
```

### Reuse

- `recording.structured_lyrics` is already populated by the DB query
  (`get_recording_by_song_id` uses `RECORDING_COLUMNS_SELECT` which includes
  `structured_lyrics`).
- `json.loads` is stdlib, no new dependency.

---

## Phase 3: Lyrics Panel Rendering

**Edit:** `ops/admin-cli/src/stream_of_worship/admin/component_editor/lyrics_panel.py`

### 3a. Update `__init__` to store `_structured_lyrics`

```python
def __init__(self, **kwargs) -> None:
    super().__init__(**kwargs)
    self._song_title: str = ""
    self._highlighted_index: int = -1
    self._parsed: LRCParsedContent | None = None
    self._structured_lyrics: dict | None = None  # NEW
    self._content_strips: list[Strip] = []
    self._render_width: int = 0
```

### 3b. Update `update_lrc()` signature

Add an optional `structured_lyrics` parameter:

```python
def update_lrc(
    self,
    parsed: LRCParsedContent | None,
    song_title: str,
    highlighted_index: int = -1,
    structured_lyrics: dict | None = None,  # NEW
) -> None:
    self._song_title = song_title
    self._parsed = parsed
    self._highlighted_index = highlighted_index
    self._structured_lyrics = structured_lyrics  # NEW: store for resize

    if parsed is None and structured_lyrics is None:
        self.add_class("empty")
        text = Text(f'No LRC file found for "{song_title}"')
    else:
        self.remove_class("empty")
        text = self._build_lyrics_text(parsed, highlighted_index, structured_lyrics)

    self._rebuild_strips(text)
    # ... (rest unchanged: scroll logic) ...
```

### 3c. Update `_build_lyrics_text()`

Add structured lyrics rendering after the LRC timed lines:

```python
def _build_lyrics_text(
    self,
    parsed: LRCParsedContent | None,
    highlighted_index: int,
    structured_lyrics: dict | None,  # NEW
) -> Text:
    text = Text()

    # --- LRC content (existing, unchanged) ---
    if parsed is not None:
        if parsed.preserved_lines:
            for p in parsed.preserved_lines:
                if p.tag is not None:
                    text.append(f"[{p.tag}: {p.value}]\n", style="dim italic")
                elif p.raw.strip():
                    text.append(f"{p.raw}\n", style="dim italic")
            text.append("\n")

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

    # --- Structured lyrics (NEW) ---
    if structured_lyrics is not None:
        sections = structured_lyrics.get("sections", [])
        if not sections:
            return text  # nothing to append

        # Separator line
        text.append("\n")
        text.append("═" * 60 + "\n", style="dim")

        # Section header
        text.append("-- Structured Lyrics --\n", style="bold cyan")
        text.append("\n")

        # Render each section
        for section in sections:
            raw_label = section.get("raw_label") or section.get("label", "")
            lines = section.get("lines", [])

            text.append(f"[{raw_label}]\n", style="bold magenta")
            for line_text in lines:
                text.append(f"{line_text}\n")
            text.append("\n")  # blank line between sections

    return text
```

### 3d. Update `on_resize()` for structured lyrics rebuild

The existing `on_resize` calls `_build_lyrics_text` — update it to pass
`self._structured_lyrics`:

```python
def on_resize(self, event: events.Resize) -> None:
    new_width = self.scrollable_content_region.width or self.size.width
    if new_width != self._render_width and (
        self._parsed is not None or self._structured_lyrics is not None
    ):
        text = self._build_lyrics_text(
            self._parsed, self._highlighted_index, self._structured_lyrics
        )
        self._rebuild_strips(text)
        self.refresh()
```

### 3e. Update `set_highlighted_index()` to preserve structured lyrics

When the highlight changes during playback (5Hz update), the panel calls
`update_lrc` internally. This must preserve the structured lyrics:

```python
def set_highlighted_index(self, index: int) -> None:
    if index == self._highlighted_index:
        return
    if self._parsed is None:
        return
    self.update_lrc(
        self._parsed,
        self._song_title,
        highlighted_index=index,
        structured_lyrics=self._structured_lyrics,  # preserve
    )
```

---

## Phase 4: Screen Wiring

**Edit:** `ops/admin-cli/src/stream_of_worship/admin/component_editor/screen.py`

### 4a. Update `_refresh_lyrics_panel()`

Pass the session's `structured_lyrics` to `panel.update_lrc()`:

```python
def _refresh_lyrics_panel(self) -> None:
    if self._right_panel_mode != "lyrics":
        return
    try:
        panel = self.query_one("#lyrics-panel", LyricsPanel)
    except NoMatches:
        return
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
            pos = self.playback.position_seconds or 0.0
            idx = LyricsPanel.compute_highlighted_index(parsed, pos)
            panel.update_lrc(
                parsed,
                song_title,
                highlighted_index=idx,
                structured_lyrics=session.structured_lyrics,  # NEW
            )
        return

    # ... (prefetch-in-progress and no-LRC cases unchanged) ...

    # If no LRC but structured lyrics exist, still show structured lyrics
    if session.structured_lyrics is not None:
        panel.update_lrc(
            None,
            song_title,
            structured_lyrics=session.structured_lyrics,
        )
    else:
        panel.update_lrc(None, song_title)
```

---

## Phase 5: Edge Cases

| Case | Handling |
|---|---|
| `recording.structured_lyrics` is None | `structured_lyrics_dict = None`; panel shows LRC only (or "No LRC" if LRC also missing) |
| `recording.structured_lyrics` is invalid JSON | `json.JSONDecodeError` caught; `structured_lyrics_dict = None`; panel shows LRC only |
| `structured_lyrics` has empty `sections` list | `_build_lyrics_text` checks `if not sections: return text` — no structured section appended |
| LRC exists but no structured lyrics | Panel shows LRC lines only (no separator or structured section) |
| No LRC but structured lyrics exist | Panel shows structured lyrics only (no LRC header); the `_build_lyrics_text` handles `parsed=None` gracefully — structured section still renders |
| Neither LRC nor structured lyrics | Panel shows "No LRC file found for ..." placeholder |
| Playback highlight changes | `set_highlighted_index` preserves `self._structured_lyrics` and re-renders full content (LRC + structured) |
| Terminal resize | `on_resize` rebuilds strips including structured lyrics |
| `structured_lyrics` sections have empty `lines` | Section label rendered, followed by blank line — no crash |

---

## Phase 6: Highlight/Scroll Computation

The existing `_compute_content_height()` and `_compute_highlighted_line_y()`
methods only account for LRC lines (metadata header + timed lines). The
structured lyrics appended below do NOT affect highlight computation
because:

1. `highlighted_index` is an index into `parsed.timed_lines` only.
2. `_scroll_to_highlight()` centers the highlighted LRC line — the
   structured lyrics below are scrolled past naturally.
3. `_compute_content_height()` is used only for initial virtual_size
   estimation, but `_rebuild_strips()` sets `virtual_size` from the actual
   strip count (which includes structured lyrics lines).

**No changes needed** to `_compute_content_height()` or
`_compute_highlighted_line_y()`.

---

## Files Changed

| File | Change type | Description |
|---|---|---|
| `component_editor/state.py` | **Edit** | Add `structured_lyrics: dict \| None = None` field to `SongSession` |
| `commands/audio.py` | **Edit** | Parse `recording.structured_lyrics` JSON, pass to `SongSession` constructor (~line 5472) |
| `component_editor/lyrics_panel.py` | **Edit** | Add `_structured_lyrics` to `__init__`; update `update_lrc()` signature; update `_build_lyrics_text()` to append structured lyrics; update `on_resize()` and `set_highlighted_index()` to preserve structured lyrics |
| `component_editor/screen.py` | **Edit** | Update `_refresh_lyrics_panel()` to pass `session.structured_lyrics` to `panel.update_lrc()` |

**Total estimated additions:** ~60 LOC across 4 edited files. No new files.

---

## Testing

### Manual verification

```bash
# Song with both LRC and structured lyrics
uv run --project ops/admin-cli --extra admin sow-admin audio review-components <song_id>

# Song with LRC but no structured lyrics
# (structured_lyrics column is NULL)

# Song with structured lyrics but no LRC
# (only structured section shows)

# Song with neither
# (placeholder shows)
```

### Verification checklist

- [ ] LRC lyrics render as before (timestamps, highlight, auto-scroll)
- [ ] Structured lyrics appear below LRC lyrics after a separator line
- [ ] Section labels ([Verse], [Chorus], etc.) render in bold magenta
- [ ] Lyric lines render in normal text
- [ ] Scrolling down from LRC lyrics reveals structured lyrics
- [ ] Playback highlight still centers on the current LRC line
- [ ] Song switch (n/p) updates both LRC and structured lyrics
- [ ] Terminal resize rebuilds both LRC and structured content
- [ ] Song with no structured lyrics shows LRC only (no separator)
- [ ] Song with no LRC but structured lyrics shows structured section only
- [ ] Song with neither shows placeholder
- [ ] Invalid JSON in structured_lyrics column does not crash

---

## Open Questions

None — all clarified via Q&A.
