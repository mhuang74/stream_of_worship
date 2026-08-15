---

# Implementation Plan: Component Editor — Lyrics Panel Scrolling (v1)

> **Date:** 2026-08-15
> **Branch:** TBD
> **Spec ID:** `component-editor-lyrics-panel-scrolling-v1`
> **Status:** Planning — not yet implemented

---

## Goal

Fix the Lyrics Panel in the Component Metadata Editor TUI so that lyrics from
long songs are fully viewable. Two enhancements:

1. **Auto-scroll to center** — When the playback-synced highlighted line
   changes, scroll the panel to keep it roughly centered in the viewport.
2. **Manual scroll via Up/Down arrows** — When the lyrics panel is focused
   (right panel in lyrics mode), `up`/`down` arrows scroll the lyrics
   line-by-line.

## Non-Goals

- No refactor of `LyricsPanel` from `Static` to `ScrollableContainer`.
- No click-to-seek from lyrics.
- No changes to LRC fetching or parsing.
- No changes to the detail panel scrolling behavior.

## User Decisions (from clarification Q&A)

| Question | Decision |
|---|---|
| Auto-scroll behavior | Auto-scroll to center — scroll so highlighted line is roughly centered. Manual scroll position is overridden on next highlight change. |
| Manual scroll keys | `Up`/`Down` arrows — in lyrics mode they scroll the lyrics panel; in details mode they continue to navigate editable fields (modes are mutually exclusive). |
| Implementation approach | Keep `Static` widget — save/restore scroll, add `scroll_to_y()` calls, add keyboard bindings. Minimal diff. |

---

## Problem Analysis

### Current Behavior

`LyricsPanel` (`lyrics_panel.py:16`) extends `Static`. It renders all lyrics
as a single `Text` object via `self.update(text)` (`lyrics_panel.py:92`). The
issues:

1. **No auto-scroll**: `set_highlighted_index()` (`lyrics_panel.py:94-105`)
   calls `update_lrc()` which calls `self.update(text)` — the scroll position
   is not preserved or adjusted to follow the highlighted line. Long songs
   have lyrics that extend below the viewport and the highlight moves out of
   view.

2. **No manual scroll keys**: The screen-level bindings
   `Binding("up", "detail_focus_up")` and `Binding("down", "detail_focus_down")`
   (`screen.py:441-442`) only operate in details mode — the actions early-return
   when `_right_panel_mode != "details"`. In lyrics mode, `up`/`down` are
   no-ops.

3. **Scroll position may reset on re-render**: `Static.update()` re-renders
   the full content, which can reset the scroll position to 0. This happens at
   each highlight change (via `set_highlighted_index` → `update_lrc` →
   `update(text)`), though only when the index actually changes (the
   `set_highlighted_index` early-return at `lyrics_panel.py:101-102` prevents
   unnecessary re-renders).

### Textual Scroll API

`Static` widgets with `overflow-y: auto` support programmatic scrolling via:

- `self.scroll_to_y(y, animate=False)` — scroll so `y` is at the top of the
  viewport
- `self.scroll_y` — current scroll position (read/write)
- `self.virtual_size.height` — total content height
- `self.size.height` — visible viewport height

Since `Static` does not mixin `ScrollableContainer`, it does not have built-in
keyboard scroll handlers. Screen-level bindings will fire when the lyrics
panel is focused.

---

## Phase 1: Auto-Scroll to Highlighted Line

**File:** `ops/admin-cli/src/stream_of_worship/admin/component_editor/lyrics_panel.py`

### 1a. Compute line Y-offset

Add a method to compute the Y-coordinate (line number) of a given timed-line
index within the rendered text. The rendering layout
(`lyrics_panel.py:67-92`) is:

```
[metadata line 0]      ← preserved_lines[0] (if tag or raw.strip())
[metadata line 1]      ← preserved_lines[1]
                       ← blank separator (1 line)
[timed line 0]         ← highlighted_index=0
[timed line 1]         ← highlighted_index=1
...
```

Add to `LyricsPanel`:

```python
def _compute_highlighted_line_y(self, highlighted_index: int) -> int:
    """Return the 0-based Y line coordinate of the highlighted timed line
    within the rendered Text content.

    Accounts for the metadata header (variable line count) + blank separator.
    Returns 0 if no parsed content.
    """
    if self._parsed is None:
        return 0
    y = 0
    if self._parsed.preserved_lines:
        for p in self._parsed.preserved_lines:
            if p.tag is not None or p.raw.strip():
                y += 1
        y += 1  # blank separator line
    y += highlighted_index
    return y
```

### 1b. Auto-scroll after update

After `self.update(text)` in `update_lrc()`, if `highlighted_index >= 0`,
scroll to center the line. Use `call_after_refresh` to ensure the virtual size
is updated before scrolling:

```python
def update_lrc(self, parsed, song_title, highlighted_index=-1) -> None:
    # ... existing rendering logic ...
    self.update(text)
    if highlighted_index >= 0:
        self._scroll_to_highlight(highlighted_index)
    else:
        self.scroll_home(animate=False)
```

### 1c. Centering scroll logic

```python
def _scroll_to_highlight(self, highlighted_index: int) -> None:
    """Scroll so the highlighted line is roughly centered in the viewport."""
    def _do_scroll() -> None:
        target_y = self._compute_highlighted_line_y(highlighted_index)
        viewport_h = self.size.height
        if viewport_h <= 0:
            return
        content_h = self.virtual_size.height
        center_y = max(0, target_y - viewport_h // 2)
        max_scroll = max(0, content_h - viewport_h)
        center_y = min(center_y, max_scroll)
        self.scroll_to_y(center_y, animate=False)

    self.call_after_refresh(_do_scroll)
```

**Why `call_after_refresh`:** `self.update(text)` schedules a re-render. The
virtual size (`virtual_size.height`) reflects the new content only after the
re-render completes. `call_after_refresh` ensures the scroll calculation uses
the correct virtual size.

### 1d. `set_highlighted_index` — no change needed

The existing `set_highlighted_index()` (`lyrics_panel.py:94-105`) calls
`update_lrc()` which now includes auto-scroll. The early-return guard
(`if index == self._highlighted_index: return`) prevents unnecessary
re-renders + scroll at 5Hz when the highlight hasn't changed.

### 1e. Full modified `update_lrc` method

```python
def update_lrc(
    self,
    parsed: LRCParsedContent | None,
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

    # LRC metadata header (unchanged)
    if parsed.preserved_lines:
        for p in parsed.preserved_lines:
            if p.tag is not None:
                text.append(f"[{p.tag}: {p.value}]\n", style="dim italic")
            elif p.raw.strip():
                text.append(f"{p.raw}\n", style="dim italic")
        text.append("\n")

    # Timed lines with current-line highlight (unchanged)
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
    # NEW: auto-scroll to keep highlighted line centered
    if highlighted_index >= 0:
        self._scroll_to_highlight(highlighted_index)
    else:
        self.scroll_home(animate=False)
```

---

## Phase 2: Manual Scroll Keys (Up/Down)

**Files:** `lyrics_panel.py` + `screen.py`

### 2a. Add scroll methods to `LyricsPanel`

```python
def scroll_line_up(self) -> None:
    """Scroll up by one line."""
    self.scroll_to_y(max(0, self.scroll_y - 1), animate=False)

def scroll_line_down(self) -> None:
    """Scroll down by one line."""
    max_scroll = max(0, self.virtual_size.height - self.size.height)
    self.scroll_to_y(min(self.scroll_y + 1, max_scroll), animate=False)
```

### 2b. Modify screen action handlers for up/down

**File:** `ops/admin-cli/src/stream_of_worship/admin/component_editor/screen.py`

The existing `action_detail_focus_up` and `action_detail_focus_down`
(`screen.py:1102-1118`) only handle details mode. Modify them to also handle
lyrics mode:

```python
def action_detail_focus_up(self) -> None:
    if self._guard_active_edit():
        return
    if self._active_panel != "right":
        return
    if self._right_panel_mode == "details":
        panel = self.query_one("#detail-panel", ComponentDetailPanel)
        panel.move_focus_up()
        self._refresh_detail_panel()
    elif self._right_panel_mode == "lyrics":
        panel = self.query_one("#lyrics-panel", LyricsPanel)
        panel.scroll_line_up()

def action_detail_focus_down(self) -> None:
    if self._guard_active_edit():
        return
    if self._active_panel != "right":
        return
    if self._right_panel_mode == "details":
        panel = self.query_one("#detail-panel", ComponentDetailPanel)
        panel.move_focus_down()
        self._refresh_detail_panel()
    elif self._right_panel_mode == "lyrics":
        panel = self.query_one("#lyrics-panel", LyricsPanel)
        panel.scroll_line_down()
```

**Key point:** The `_guard_active_edit()` check is first, preserving the
existing edit-guard behavior. When in lyrics mode, there's no active edit
(edits only happen in details mode), so the guard always passes. The
`_active_panel != "right"` check ensures that when the table (left panel) is
focused, `up`/`down` are handled by the table's own
`action_cursor_up`/`action_cursor_down` methods and the screen-level action
doesn't interfere.

### 2c. Update BINDINGS labels (cosmetic)

**File:** `screen.py:441-442`

Change the binding display labels from "Field ↑" / "Field ↓" to "Up" / "Down"
to reflect the dual-mode behavior:

```python
Binding("up", "detail_focus_up", "Up"),
Binding("down", "detail_focus_down", "Down"),
```

### 2d. BINDING_GROUPS — no change needed

The existing `"Panels"` group (`screen.py:459-464`) already includes
`"detail_focus_up"` and `"detail_focus_down"`. No structural change needed.

---

## Phase 3: Edge Cases

| Case | Handling |
|---|---|
| No LRC for song | `highlighted_index = -1` → `scroll_home()` (top). Panel shows placeholder text. |
| Short lyrics (fits in viewport) | `_scroll_to_highlight` computes `center_y = 0` (clamped). No visible scroll. |
| Before playback starts | `highlighted_index = -1` → `scroll_home()` (top). |
| Song switch | `_refresh_lyrics_panel()` calls `update_lrc()` with computed highlight from current playback position. If playback is at 0:00, `highlighted_index = -1` or 0 → scroll to top. |
| Manual scroll then highlight changes | User scrolls manually via `up`/`down`. When `set_highlighted_index` fires (playback advances to next line), `update_lrc` re-renders and auto-scrolls to center the new highlight. Manual position is overridden. |
| Empty `preserved_lines` | `_compute_highlighted_line_y` returns `highlighted_index` directly (no metadata offset). |
| `preserved_lines` with blank/empty entries | Entries where `p.tag is None and not p.raw.strip()` are skipped in rendering and in `_compute_highlighted_line_y`. |
| Very end of song | `highlighted_index` is the last line. `center_y` is clamped to `max_scroll` so the last line is visible (may not be centered if near end). |
| `virtual_size.height` not yet computed | `call_after_refresh` defers the scroll calculation until after re-render. If `viewport_h <= 0` (widget not mounted yet), the scroll is skipped. |

---

## Phase 4: Testing

### Manual verification

```bash
# Long song with many lyric lines
uv run --project ops/admin-cli --extra admin sow-admin audio review-components <song_id>

# Verify:
# 1. Lyrics panel shows first lines on load
# 2. Start playback (SPACE) — highlight follows playback
# 3. When highlight moves below viewport, panel auto-scrolls to center it
# 4. Press Tab to focus lyrics panel
# 5. Press Up/Down — panel scrolls line-by-line
# 6. When playback advances to next line, auto-scroll re-centers
```

### Automated tests

**File:** `tests/admin/component_editor/test_lyrics_panel.py` (or existing test file)

```python
async def test_lyrics_auto_scroll_to_highlighted_line():
    """When highlight changes to a line below viewport, panel scrolls to center it."""

async def test_lyrics_manual_scroll_up_down():
    """Up/Down keys scroll the lyrics panel when focused in lyrics mode."""

async def test_lyrics_scroll_preserved_for_short_songs():
    """Short lyrics that fit in viewport don't cause unwanted scrolling."""

async def test_lyrics_no_scroll_without_highlight():
    """When highlighted_index is -1, panel scrolls to top."""

async def test_up_down_noop_when_table_focused():
    """Up/Down do not scroll lyrics when left panel (table) is focused."""

async def test_up_down_noop_in_hidden_mode():
    """Up/Down are no-ops when right panel is hidden."""
```

### Verification checklist

- [ ] Long songs: lyrics from later parts are visible via auto-scroll
- [ ] Highlighted line is centered in viewport during playback
- [ ] Up/Down arrows scroll lyrics panel when focused (lyrics mode)
- [ ] Up/Down arrows still navigate editable fields (details mode)
- [ ] Up/Down still navigate table rows (left panel focused)
- [ ] Song switch resets lyrics to top (or current playback position)
- [ ] No LRC: no scroll errors
- [ ] Short songs: no spurious scrolling
- [ ] 5Hz highlight updates don't cause visible jitter (animate=False)

---

## Files Changed

| File | Change type | Est. LOC |
|---|---|---|
| `component_editor/lyrics_panel.py` | **Edit** — add `_compute_highlighted_line_y`, `_scroll_to_highlight`, `scroll_line_up`, `scroll_line_down`; modify `update_lrc` to auto-scroll | ~40 |
| `component_editor/screen.py` | **Edit** — modify `action_detail_focus_up`/`action_detail_focus_down` to handle lyrics mode; update binding labels | ~15 |
| `tests/admin/component_editor/test_lyrics_panel.py` | **New/Edit** — add scrolling tests | ~80 |

**Total estimated additions:** ~135 LOC across 1 edit + 1 new/edit test file.

---

## Implementation Order

1. **Phase 1** (auto-scroll): add `_compute_highlighted_line_y`,
   `_scroll_to_highlight`, modify `update_lrc` in `lyrics_panel.py`
2. **Phase 2** (manual scroll keys): add `scroll_line_up`/`scroll_line_down`
   to `lyrics_panel.py`; modify `action_detail_focus_up`/
   `action_detail_focus_down` in `screen.py`
3. **Phase 4** (tests): add automated tests

---

## Verification Commands

```bash
# Tests
uv run --project ops/admin-cli --python 3.11 --extra admin --extra test pytest tests/admin/component_editor/ -v

# Lint + format
uv run --project ops/admin-cli ruff check src/stream_of_worship/admin/component_editor/
uv run --project ops/admin-cli ruff format --check src/stream_of_worship/admin/component_editor/

# Manual smoke test
uv run --project ops/admin-cli --extra admin sow-admin audio review-components <song_id>
```

---

## Backward Compatibility

- **No API changes**: `LyricsPanel.update_lrc()` signature unchanged.
- **No CSS changes**: `overflow-y: auto` already in `DEFAULT_CSS`.
- **No binding changes**: Same keys (`up`/`down`), same action names
  (`detail_focus_up`/`detail_focus_down`). The actions now handle both lyrics
  and details modes.
- **No state changes**: `ComponentEditorState` and `SongSession` untouched.
- **No autosave impact**: Autosave schema unchanged.
