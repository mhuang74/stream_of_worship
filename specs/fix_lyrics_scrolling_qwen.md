# Implementation Plan: Component Editor — Lyrics Panel Scrolling Fix (v2)

> **Date:** 2026-08-16
> **Branch:** TBD
> **Spec ID:** `component-editor-lyrics-panel-scrolling-fix-v2`
> **Status:** Planning — not yet implemented
> **Replaces:** `component-editor-lyrics-panel-scrolling-v1` (which assumed Static supports scrolling)

---

## Goal

Fix the Lyrics Panel in the Component Metadata Editor TUI so that lyrics from long songs are fully viewable via scrolling.

---

## Problem Statement

In the Component Metadata Editor TUI, the Lyrics Panel (right panel, lyrics mode) shows a scrollbar that responds to up/down arrow keys — the scrollbar thumb moves and `scroll_y` changes — but the lyrics content **never visually scrolls**. The text stays in the same position. For long songs, lyrics from later parts are invisible.

Screenshots show the scrollbar thumb at different positions between the two images, but the visible lyrics text is identical.

---

## Root Cause Analysis

**`LyricsPanel` extends `Static`, which does NOT properly support scrolling.**

The `Static` widget in Textual renders its content at position `(0, 0)` regardless of the scroll offset. Even with:
- `overflow-y: auto` in CSS
- `virtual_size.height` larger than `size.height`
- `can_focus = True` and `is_scrollable = True`

The `Static` widget's `_render()` method returns the full content at `(0, 0)`. The scrollbar is drawn and `scroll_y` changes on key press, but the visual content is always rendered at the same position. This is a fundamental limitation of `Static` — it is designed for non-scrollable static content.

**Why the spec's v1 approach won't work:** The v1 spec assumed `Static` with `overflow-y: auto` + `scroll_to_y()` calls would enable scrolling. This is incorrect — `Static` does not clip or offset its rendered content based on scroll position.

---

## Fix Approach: Switch to `ScrollableContainer`

Change `LyricsPanel` base class from `Static` to `ScrollableContainer`, which is specifically designed for scrollable content. Add a `Static` child widget to hold the lyrics text content.

**Why `ScrollableContainer`:** It properly handles:
- Clipping content to the visible viewport
- Offsetting content based on scroll position
- Scrollbar rendering and keyboard scroll events
- Virtual size management

### Architecture Change

```
Before:
  LyricsPanel (extends Static)
  └── Rich Text content via self.update(text)

After:
  LyricsPanel (extends ScrollableContainer)
  └── _content (Static widget)
      └── Rich Text content via self._content.update(text)
```

### Key Behavioral Changes

| Aspect | Before (Static) | After (ScrollableContainer) |
|---|---|---|
| Base class | `Static` | `ScrollableContainer` |
| Content rendering | `self.update(text)` | `self._content.update(text)` |
| Virtual size | `self.virtual_size = Size(w, h)` | `self._content.virtual_size = Size(w, h)` |
| Scroll offset applied | No (content fixed at 0,0) | Yes (properly clipped/offset) |
| `can_focus` | `True` (on LyricsPanel) | `True` (on LyricsPanel) |

---

## Implementation Phases

### Phase 1: Change Base Class & Add Inner Widget

**File:** `ops/admin-cli/src/stream_of_worship/admin/component_editor/lyrics_panel.py`

1. Change base class: `class LyricsPanel(Static)` → `class LyricsPanel(ScrollableContainer)`
2. Add import: `from textual.widgets import ScrollableContainer, Static`
3. In `__init__`, create inner widget:
   ```python
   self._content = Static()
   self._content.can_focus = False
   ```
4. Override `compose()` to yield the inner widget:
   ```python
   def compose(self) -> ComposeResult:
       yield self._content
   ```

### Phase 2: Update Rendering Methods

1. **`update_lrc()`**: Change `self.update(text)` → `self._content.update(text)`
2. **Virtual size**: Change `self.virtual_size = Size(...)` → `self._content.virtual_size = Size(...)`
3. **Scroll methods**: `scroll_line_up()`/`scroll_line_down()` already work on `ScrollableContainer` via inherited `scroll_up()`/`scroll_down()`
4. **`_scroll_to_highlight()`**: `self.scroll_to(...)` already works on `ScrollableContainer`
5. **`_compute_content_height()`**: No change needed (pure computation)
6. **`_compute_highlighted_line_y()`**: No change needed (pure computation)
7. **`set_highlighted_index()`**: No change needed (calls `update_lrc`)

### Phase 3: Update CSS

The CSS targets `LyricsPanel` directly. With `ScrollableContainer`, the padding and background should apply to the container, and the inner `Static` should be transparent:

```css
LyricsPanel {
    height: 1fr;
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

/* Inner content widget — no extra padding/background */
LyricsPanel > Static {
    padding: 0 1;
    background: transparent;
}
```

**Note:** The existing `padding: 0 1` was on `LyricsPanel` (Static). With `ScrollableContainer`, padding moves to the inner `Static` child. The container's `padding` is removed since `ScrollableContainer` handles its own inner padding.

### Phase 4: Update `is_scrollable` / `is_container` Properties

The current overrides:
```python
@property
def is_scrollable(self) -> bool:
    return True

@property
def is_container(self) -> bool:
    return False
```

With `ScrollableContainer`:
- `is_scrollable` — `ScrollableContainer` is already scrollable, this override is redundant but harmless
- `is_container` — `ScrollableContainer` IS a container (it has children), so return `True` or remove the override

### Phase 5: Remove Custom `_size_updated` Override

The current `_size_updated` override was added to work around `Static`'s scrolling limitations. With `ScrollableContainer`, this override is no longer needed and should be removed to avoid interfering with the container's normal size management.

### Phase 6: Add Regression Test

**File:** `ops/admin-cli/tests/admin/component_editor/test_lyrics_panel.py`

Add a new test that specifically verifies scroll position changes cause content to visually scroll:

```python
@pytest.mark.asyncio
async def test_lyrics_scroll_changes_visible_content():
    """Regression: scroll_y changes must cause content to visually scroll.

    The bug was that Static widget rendered content at (0,0) regardless of
    scroll_y. With ScrollableContainer, the content should be properly
    clipped/offset based on scroll position.
    """
    app, _state = _make_app()
    async with app.run_test(size=(80, 20)) as pilot:
        await pilot.pause()
        # Create lyrics with 40 lines (more than fits in 20-row viewport)
        parsed = _make_parsed(num_lines=40)
        panel = await _setup_lyrics(pilot, parsed, highlighted_index=25)
        
        # Initial: highlight is centered, scroll_y > 0
        assert panel.scroll_y > 0
        
        # Scroll down by 5 lines
        for _ in range(5):
            panel.scroll_line_down()
        
        assert panel.scroll_y == (panel.scroll_y - 5) + 5  # incremented
        
        # Scroll to bottom
        panel.scroll_to_y(panel.max_scroll_y, animate=False, immediate=True)
        await pilot.pause()
        assert panel.scroll_y == panel.max_scroll_y
        
        # Verify content is at maximum scroll position
        # (the last few lines should now be visible)
```

### Phase 7: Adapt Existing Tests

The existing 12+ tests in `test_lyrics_panel.py` test the same behavioral surface (auto-scroll, manual scroll, edge cases) and should continue to pass with minimal changes:

- `test_compute_highlighted_line_y_*` — pure method tests, no changes needed
- `test_lyrics_auto_scroll_to_highlighted_line` — may need to access `panel._content` for some assertions
- `test_lyrics_manual_scroll_up_down` — `scroll_line_up/down` still work the same
- `test_lyrics_max_scroll_y_matches_virtual_size` — `max_scroll_y` is a `ScrollableContainer` property, should work

---

## Files Changed

| File | Change type | Est. LOC |
|---|---|---|
| `component_editor/lyrics_panel.py` | **Edit** — base class, inner widget, rendering, CSS, remove `_size_updated` | ~30 |
| `tests/admin/component_editor/test_lyrics_panel.py` | **Edit** — add regression test, adapt existing tests | ~40 |

**Total estimated additions:** ~70 LOC across 2 files.

---

## Verification Commands

```bash
# Tests
uv run --project ops/admin-cli --python 3.11 --extra admin --extra test \
    pytest tests/admin/component_editor/test_lyrics_panel.py -v

# Lint + format
uv run --project ops/admin-cli ruff check src/stream_of_worship/admin/component_editor/
uv run --project ops/admin-cli ruff format --check src/stream_of_worship/admin/component_editor/

# Manual smoke test
uv run --project ops/admin-cli --extra admin sow-admin audio review-components <song_id>
# Verify:
# 1. Lyrics panel shows lyrics
# 2. Up/Down arrows scroll content (not just scrollbar)
# 3. Playback auto-scrolls to center highlighted line
# 4. Tab to table, Up/Down navigates rows (not lyrics)
```

---

## Backward Compatibility

- **No API changes**: `LyricsPanel.update_lrc()` signature unchanged
- **No screen.py changes needed**: All interactions use public methods (`update_lrc`, `scroll_line_up`, `scroll_line_down`, `set_highlighted_index`, `scroll_to`)
- **No CSS breaking changes**: Same selector `LyricsPanel`, same visual appearance
- **No state changes**: `ComponentEditorState` and `SongSession` untouched
- **No autosave impact**: Autosave schema unchanged

---

## Risks & Mitigations

| Risk | Mitigation |
|---|---|
| `ScrollableContainer` has different layout behavior | Test with `run_test(size=(80, 20))` to verify layout |
| Inner `Static` widget affects focus behavior | Set `can_focus = False` on inner widget, keep `True` on `LyricsPanel` |
| CSS padding/margin changes visual appearance | Move padding from container to inner widget, verify visually |
| `virtual_size` propagation to inner widget | Set `virtual_size` on `_content`, not on container |
| Existing tests break due to widget hierarchy change | Adapt tests to access `_content` where needed |
