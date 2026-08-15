---

# Fix: LyricsPanel Manual Scroll Not Working

> **Date:** 2026-08-15
> **Branch:** TBD
> **Spec ID:** `fix-lyrics-panel-scroll-v2`
> **Status:** Implemented — all 15 tests passing

---

## Problem

In the Component Metadata Editor TUI (`ops/admin-cli/src/stream_of_worship/admin/component_editor/lyrics_panel.py`), the LyricsPanel **does not scroll** when the user presses Up/Down keys for songs with lyrics longer than the viewport. Short songs (content fits in viewport) are unaffected since no scrolling is needed.

## Root Cause

**Three bugs work together to prevent manual scrolling:**

### Bug A: `set_reactive` bypasses `watch_scroll_y` → no `_set_dirty()` → no repaint

`LyricsPanel.scroll_line_down()` used `set_reactive(Widget.scroll_y, new_y)` to set the scroll position. `set_reactive` directly writes the reactive value, **bypassing `validate_scroll_y`, `watch_scroll_y`, and the reactive's `refresh()` call**.

The regular reactive setter flow is:
1. `validate_scroll_y(value)` — clamps to `[0, max_scroll_y]`
2. Store value
3. `watch_scroll_y(old, new)` — if rounded value changed:
   - Updates scrollbar position
   - Calls `_refresh_scroll()` → `_scroll_required = True`
4. If `self._repaint` (True for `scroll_y`): `obj.refresh(repaint=True)` → `_set_dirty()` → widget marked dirty for repaint

`set_reactive` skips ALL four steps. No `_set_dirty()`, so the widget is **not marked dirty** and the compositor does not re-render it with the new scroll offset.

### Bug B: Custom `max_scroll_y` override doesn't match `virtual_size.height`

`LyricsPanel` overrode `max_scroll_y`:
```python
@property
def max_scroll_y(self) -> int:
    return max(0, self._compute_content_height() - self.size.height)
```

The base `Widget.max_scroll_y` uses:
```python
return max(0, self.virtual_size.height - (self.container_size.height - self.scrollbar_size_horizontal))
```

`_compute_content_height()` is a **manual line count** that doesn't account for line wrapping. If any lyric line wraps (content wider than panel width minus padding), `virtual_size.height` (computed by the compositor from the rendered `Visual`) exceeds `_compute_content_height()`. The custom `max_scroll_y` is then **smaller than the correct value**, and `validate_scroll_y` clamps the scroll position prematurely — the user cannot scroll to see wrapped-line content at the bottom.

### Bug C: Compositor overwrites `virtual_size` for non-container `Static` widgets

`LyricsPanel(Static)` with `height: 1fr; overflow-y: auto;` — the Textual compositor sets `virtual_size = size` for non-container `Static` widgets, so `max_scroll_y = 0` and scrolling never works. The `Static` widget has no children/layout, so the compositor treats it as a leaf node and sets `virtual_size` equal to the actual `size`, ignoring any manually-set `virtual_size`.

## Fix: ScrollView Pattern

The actual implementation went further than the original plan (remove overrides + replace `set_reactive`). It adopts the **`ScrollView` pattern** — the same approach used by Textual's `RichLog` and `Log` widgets.

### Fix 1: `_size_updated` override (preserves `virtual_size`)

**File:** `ops/admin-cli/src/stream_of_worship/admin/component_editor/lyrics_panel.py`

Override `_size_updated()` to preserve `self.virtual_size` instead of letting the compositor overwrite it with `size`. This is the core fix — it prevents `max_scroll_y` from being reset to 0.

```python
def _size_updated(
    self, size: Size, virtual_size: Size, container_size: Size, layout: bool = True
) -> bool:
    size_changed = self._size != size
    if size_changed:
        self._set_dirty()
    if (
        size_changed
        or virtual_size != self.virtual_size
        or container_size != self.container_size
    ):
        self._scrollbar_changes.clear()
        self._size = size
        virtual_size = self.virtual_size  # ← KEY: preserve our virtual_size
        self._container_size = size - self.styles.gutter.totals
        self._scroll_update(virtual_size)
    return size_changed or self._container_size != container_size
```

This mirrors `ScrollView._size_updated()` exactly.

### Fix 2: `is_container` → `False`

```python
@property
def is_container(self) -> bool:
    return False
```

Prevents the compositor from treating the panel as a container that auto-sizes to children.

### Fix 3: `get_content_height` → `virtual_size.height`

```python
def get_content_height(self, container: Size, viewport: Size, width: int) -> int:
    return self.virtual_size.height
```

Returns the preserved virtual size height.

### Fix 4: Removed custom scroll property overrides

Removed:
- `allow_vertical_scroll` property — let base class use `is_scrollable and show_vertical_scrollbar`
- `max_scroll_y` property — let base class use `virtual_size.height - (container_size.height - scrollbar_size_horizontal)`
- `validate_scroll_y` method — let base class use `clamp(value, 0, max_scroll_y)`
- `_update_virtual_size()` method — dead code

Kept `is_scrollable = True` — still needed because `Static` has no layout/children, so the base property returns `False`.

### Fix 5: Replace `set_reactive` with Textual's built-in scroll methods

Replaced `scroll_line_down()`:
```python
def scroll_line_down(self) -> None:
    self.scroll_down(animate=False, immediate=True, force=True)
```

Replaced `scroll_line_up()`:
```python
def scroll_line_up(self) -> None:
    self.scroll_up(animate=False, immediate=True, force=True)
```

Replaced `_scroll_to_highlight()`:
```python
def _scroll_to_highlight(self, highlighted_index: int) -> None:
    target_y = self._compute_highlighted_line_y(highlighted_index)
    viewport_h = self.size.height
    if viewport_h <= 0:
        return
    center_y = max(0, target_y - viewport_h // 2)
    self.scroll_to(y=center_y, animate=False, immediate=True, force=True)
```

Replaced `update_lrc()` reset-to-top path:
```python
else:
    self.scroll_to(y=0, animate=False, immediate=True, force=True)
```

Textual's `scroll_down()` / `scroll_up()` / `scroll_to()` use the regular reactive setter → `validate_scroll_y` + `watch_scroll_y` + `refresh(repaint=True)` → widget is marked dirty. `immediate=True` runs synchronously. `force=True` forces scrolling even when overflow styling might prohibit it.

### Fix 6: Set `self.virtual_size` directly in state-update methods

In `update_lrc()`, `update_fetching()`, and `update_error()`, set `self.virtual_size` directly:
```python
self.virtual_size = Size(self.size.width, content_h)
```

This ensures `virtual_size` is correct before the next `_size_updated()` call preserves it.

## Test Infrastructure

### `_setup_lyrics()` test helper

**File:** `ops/admin-cli/tests/admin/component_editor/test_lyrics_panel.py`

A test helper that populates `state.lrc_parsed[song_id]` with test data before calling `panel.update_lrc()`. This is needed because:

1. **Async LRC prefetch worker** — `_prefetch_lrc` runs on mount and calls `_refresh_lyrics_panel()` in its `finally` block. During `pilot.pause()`, this worker can complete and overwrite test-set panel content. Setting `state.lrc_parsed[song_id]` and `state.lrc_prefetch_in_progress = False` prevents this.

2. **5Hz position update timer** — `_start_position_updates()` runs a loop that calls `_update_lyrics_highlight()` every 0.2s. This computes `highlighted_index` from playback position and calls `panel.set_highlighted_index(idx)`. If the index changed, `set_highlighted_index` calls `update_lrc()` which re-renders and auto-scrolls — **this can override manual scroll**. The helper sets `pilot.app.screen.playback._position_seconds` to match the highlighted index so the timer doesn't reset it.

3. **`highlighted_index = -1` case** — When testing with no highlight, the helper sets `_position_seconds = -1.0` so `compute_highlighted_index` returns -1 (position before first line). This prevents the 5Hz timer from computing index 0 (position 0.0 → first line at time 0.0) and calling `set_highlighted_index(0)`, which would re-render and scroll to center line 0.

### Keyboard scroll test fix

The `test_lyrics_keyboard_scroll_down_persists` test needed `panel.focus()` after setting `_active_panel = "right"`. Without focus, the `down` key is consumed by the `ComponentMetadataTable` (which has its own `action_cursor_down`) before reaching the screen binding `action_detail_focus_down`.

## Summary of Changes

| Change | Location | What |
|--------|----------|------|
| Add `_size_updated` override | `lyrics_panel.py:58-74` | Preserve `virtual_size` (ScrollView pattern) |
| Add `is_container` → `False` | `lyrics_panel.py:52-53` | Prevent auto-size-to-children |
| Add `get_content_height` | `lyrics_panel.py:55-56` | Return `virtual_size.height` |
| Remove `allow_vertical_scroll` override | — | Use base class |
| Remove `max_scroll_y` override | — | Use base class (uses `virtual_size.height`) |
| Remove `validate_scroll_y` override | — | Use base class |
| Remove `_update_virtual_size()` | — | Dead code |
| Replace `scroll_line_down()` | `lyrics_panel.py:176-178` | Use `self.scroll_down(animate=False, immediate=True, force=True)` |
| Replace `scroll_line_up()` | `lyrics_panel.py:172-174` | Use `self.scroll_up(animate=False, immediate=True, force=True)` |
| Replace `_scroll_to_highlight()` | `lyrics_panel.py:163-170` | Use `self.scroll_to(y=..., animate=False, immediate=True, force=True)` |
| Update `update_lrc()` reset path | `lyrics_panel.py:143` | Use `self.scroll_to(y=0, ...)` |
| Set `virtual_size` in state updates | `lyrics_panel.py:139,199,207` | Direct assignment in `update_lrc`, `update_fetching`, `update_error` |
| Keep `is_scrollable = True` | `lyrics_panel.py:48-49` | Still needed for `Static` |
| Keep `_compute_content_height()` | `lyrics_panel.py:76-87` | Still used by `_compute_highlighted_line_y()` |
| Add `_setup_lyrics()` helper | `test_lyrics_panel.py:134-158` | Prevents prefetch/timer interference |
| Update all async tests | `test_lyrics_panel.py` | Use `_setup_lyrics()` instead of direct `update_lrc()` |
| Add `panel.focus()` in keyboard test | `test_lyrics_panel.py:399` | Ensure `down` key reaches screen binding |

## Regression Tests

All 15 tests in `ops/admin-cli/tests/admin/component_editor/test_lyrics_panel.py`:

1. `test_compute_highlighted_line_y_with_metadata` — pure method, header offset
2. `test_compute_highlighted_line_y_no_metadata` — pure method, no header
3. `test_compute_highlighted_line_y_no_parsed` — pure method, no content
4. `test_compute_highlighted_line_y_zero_index` — pure method, index 0
5. `test_lyrics_auto_scroll_to_highlighted_line` — auto-scroll centers highlighted line
6. `test_lyrics_no_scroll_without_highlight` — `highlighted_index=-1` scrolls to top
7. `test_lyrics_scroll_preserved_for_short_songs` — short content, no scroll
8. `test_lyrics_manual_scroll_up_down` — direct `scroll_line_down/up()` calls
9. `test_lyrics_manual_scroll_up_clamped_at_zero` — clamp at top
10. `test_up_down_noop_when_table_focused` — no scroll when left panel focused
11. `test_up_down_noop_in_hidden_mode` — no scroll when right panel hidden
12. `test_up_down_scrolls_lyrics_in_lyrics_mode` — action-based scroll in lyrics mode
13. `test_lyrics_manual_scroll_persists_after_pause` — scroll persists after layout cycle
14. `test_lyrics_keyboard_scroll_down_persists` — keyboard Down scrolls and persists
15. `test_lyrics_max_scroll_y_matches_virtual_size` — `max_scroll_y` uses `virtual_size.height`

## Verification

```bash
uv run --project ops/admin-cli --python 3.11 --extra admin --extra test pytest tests/admin/component_editor/test_lyrics_panel.py -v
```

All 15 tests pass. Broader component editor suite (147 tests) also passes with no regressions.
