---

# Implementation Plan: Component Editor — Lyrics Panel Scrolling (v2)

> **Date:** 2026-08-16
> **Branch:** TBD
> **Spec ID:** `fix_lyrics_scrolling_glm`
> **Status:** Planning — not yet implemented
> **Supersedes:** `component-editor-lyrics-panel-scrolling-v1.md` (v1 was implemented in commit `3f41740bf` but introduced a subtle rendering bug — see Root Cause below)

---

## Goal

Fix the Lyrics Panel so that when the operator presses Up/Down to scroll, the **rendered lyrics content actually shifts** in the viewport (not just the scrollbar thumb). Today the scrollbar visibly responds to keys — even passing all v1 scroll-state tests — but the lyrics content stays pinned to the top of the song. Long songs cannot be read past their first viewport.

## Non-Goals

- No changes to LRC parsing/fetching.
- No changes to the ComponentDetailPanel (details mode unaffected).
- No changes to keyboard bindings table — `up`/`down` routes already call `panel.scroll_line_up()` / `panel.scroll_line_down()` correctly.
- No changes to the auto-scroll-to-highlight behavior; only the scroll rendering pipeline.

---

## User Decisions (from clarification Q&A)

| Question | Decision |
|---|---|
| Fix approach | **Subclass `textual.scroll_view.ScrollView`** properly, removing the v1 "fake ScrollView" overrides (`_size_updated`, `get_content_height`, `is_container`, `is_scrollable`). Implement `render_line(y)` following the canonical pattern. |
| Regression test depth | **Capture actual rendered output** via `panel.render_lines(panel.region)` and assert the visible line at viewport-y=0 matches lyric line at `scroll_offset.y`. |

---

## Root Cause

### V1's stated intent vs. what was actually shipped

Commit `3f41740bf` ("fix: LyricsPanel manual scroll not working (ScrollView pattern)") set out to adopt the ScrollView pattern used by `RichLog`/`Log`. The implementation added overrides:

- `is_scrollable = True`
- `is_container = False`
- `get_content_height = self.virtual_size.height`
- `_size_updated` that re-applies an externally-set `self.virtual_size`
- Direct `self.virtual_size = Size(width, content_h)` writes inside `update_lrc()`

The intent was to let `Static` "behave like" a ScrollView without inheriting from it. **This is not how Textual 7.5.0 works.**

### Textual 7.5.0 facts (verified from installed source)

1. **`Static` is NOT scrollable.** It directly subclasses `Widget`, not `ScrollView`. `Widget.render()` returns `self.visual` unchanged; it never consults `scroll_x`/`scroll_y`/`scroll_offset`.

2. **`Widget.render_line(y)` does NOT apply scroll offset.** It returns `self._render_cache.lines[y]` straight — i.e., the `y`-th line of the *content rendered into widget size* (`width, height = self.size`), with no `scroll_y + y` adjustment.

3. **`Widget.render_lines(crop)`** invokes `_styles_cache.render_widget(self, crop)`, which under the hood calls the widget's own `render_line(y)` for every `y` in `crop`. Because the base `render_line` ignores scroll, the widget **always paints the same top-of-content strips** regardless of `scroll_y`.

4. **Scroll offsets are applied in exactly two places:**
   - **Compositor** — for *containers* with children: it offsets each child placement by the parent's `scroll_offset`. But the v1 overrides set `is_container = False` explicitly, so the compositor path is bypassed.
   - **Line-API widgets** (`ScrollView` subclasses): the widget *itself* subtracts `scroll_offset.y` inside its `render_line(y)`, returning the line at content-index `scroll_y + y`. This is what `RichLog`, `Log`, `Tree`, `DataTable`, `TextArea`, `OptionList`, `SelectionList` all do.

5. The v1 code made `scroll_y` reactive track changes correctly (so the scrollbar thumb visibly moves and `panel.scroll_y == X` assertions pass), but **no Line-API `render_line` override exists on `LyricsPanel`**, so the actual pixels never scroll. The 5Hz playback updates also looked correct because `scroll_to` calls were updating state without ever reaching the rendering path.

6. The v1 test suite (`test_lyrics_panel.py`) only checked `panel.scroll_y`, `panel.max_scroll_y`, etc. — **scroll state, not rendered output**. This is why all 15 v1 tests passed despite the bug being visible to humans.

### The smoking gun from textual source

Base `Widget.render_line` (`widget.py:4200-4219`) returns `self._render_cache.lines[y]` verbatim. `ScrollView.render_line` is *not implemented* on `ScrollView` itself; concrete Line-API widgets implement it themselves. The canonical pattern (verbatim from `RichLog.render_line`, `_rich_log.py:301-305`):

```python
def render_line(self, y: int) -> Strip:
    scroll_x, scroll_y = self.scroll_offset
    line = self._render_line(
        scroll_y + y, scroll_x, self.scrollable_content_region.width
    )
    strip = line.apply_style(self.rich_style)
    return strip
```

The v1 LyricsPanel never implemented this method, so all scroll-induced rendering changes were silently dropped.

### Reference: scroll_offset arithmetic in every Line-API widget

| Widget | Source | Arithmetic |
|---|---|---|
| `RichLog` | `_rich_log.py:301` | `scroll_y + y` (via `_render_line(scroll_y + y, scroll_x, width)`) |
| `Log` | `_log.py:281` | `scroll_y + y` (via `_render_line(scroll_y + y, scroll_x, size.width)`) |
| `Tree` | `_tree.py:1302` | `y + scroll_y` (via `_render_line(y + scroll_y, scroll_x, scroll_x + width, style)`) |
| `DataTable` | `_data_table.py:2478` | adds `scroll_y` only for body rows: `if y >= fixed_rows_height: y += scroll_y`; then `_render_line(y, scroll_x, scroll_x + width, style)` |
| `TextArea` | `_text_area.py:1203/1268` | `scroll_y + y` (via `absolute_y = scroll_y + y`; `_render_line` uses `y_offset = y + scroll_y`) |
| `OptionList` | `_option_list.py:884` | `line_number = self.scroll_offset.y + y` |
| `SelectionList` | `_selection_list.py:506` | `selection_index = scroll_y + y` (but calls `super().render_line(y)` for the underlying prompt) |
| `Input` / `MaskedInput` | `_input.py:606` / `_masked_input.py:554` | single-line: only `scroll_offset.x` used for x-cropping; `y != 0` returns blank |

The canonical idiom is `scroll_y + y` (or `y + scroll_y`), where `y` is the viewport-relative row and `scroll_y` is the per-widget `scroll_offset.y`. All widgets also use `self.scrollable_content_region.width` or `self.size.width` for the width crop, and apply `self.rich_style` (or `base_style`) to the result.

---

## Reproduction (the regression test that should already exist)

Save this test in `ops/admin-cli/tests/admin/component_editor/test_lyrics_panel.py`. It reproduces the bug by capturing the **actual rendered output**, not the scroll state.

```python
@pytest.mark.asyncio
async def test_lyrics_rendered_content_shifts_on_scroll_down():
    """Regression v2: rendered strips must shift when scroll_y changes.

    v1 bug: `panel.scroll_y` was being incremented correctly (so the scrollbar
    thumb visibly moved and `scroll_y == X` assertions passed), but
    `Widget.render_line(y)` returned `self._render_cache.lines[y]` verbatim
    with no `scroll_offset.y` offset, so the first visible line of the panel
    stayed pinned to lyric line 0 regardless of scroll_y. Long songs could
    never be visually scrolled past the first viewport.
    """
    app, _state = _make_app()
    async with app.run_test(size=(80, 20)) as pilot:
        await pilot.pause()
        parsed = _make_parsed(num_lines=40)  # 40 timed lines + 2 metadata + 1 blank = 43 lines
        panel = await _setup_lyrics(pilot, parsed, highlighted_index=-1)
        assert panel.scroll_y == 0

        # Capture initial visible strips. The first visible strip should
        # contain metadata ("Test Song"), then "Line 0".
        pre_strips = panel.render_lines(panel.region)
        pre_text = "\n".join(strip.text for strip in pre_strips)
        assert "Line 0" in pre_text

        # Scroll down 5 lines using the keyboard path (action -> scroll_line_down).
        for _ in range(5):
            panel.scroll_line_down()
        await pilot.pause()
        assert panel.scroll_y == 5

        # Capture post-scroll visible strips. The FIRST visible strip must now
        # be lyric line 5, NOT lyric line 0.
        post_strips = panel.render_lines(panel.region)
        post_text = "\n".join(strip.text for strip in post_strips)
        assert "Line 5" in post_text
        assert "Line 0" not in post_text  # <- this assertion fails today (v1 bug)
```

Run it before the fix to verify the bug exists (the last assertion `assert "Line 0" not in post_text` will fail).

---

## Phase 1: Rebuild `LyricsPanel` as a proper `ScrollView` subclass

**File:** `ops/admin-cli/src/stream_of_worship/admin/component_editor/lyrics_panel.py`

### 1a. Change base class

Replace `class LyricsPanel(Static)` with `class LyricsPanel(ScrollView, can_focus=True)`.

Imports:
```python
from rich.text import Text
from textual.geometry import Size
from textual.scroll_view import ScrollView
from textual.strip import Strip

from stream_of_worship.admin.services.lrc_parser import (
    LRCParsedContent,
    format_centiseconds,
)
```

### 1b. Remove the broken v1 overrides

Delete the following methods entirely — they were failed attempts to mimick ScrollView from outside, and inherit cleanly from `ScrollView` once we actually inherit:

- `_size_updated` (lines 58-74) — `ScrollView._size_updated` does the equivalent correctly.
- `get_content_height` (lines 55-56) — `ScrollView.get_content_height` returns `self.virtual_size.height` already; the override was a no-op.
- `is_scrollable` property (lines 47-49) — `ScrollView.is_scrollable` returns `True` already.
- `is_container` property (lines 51-53) — `ScrollView.is_container` returns `False` already.

### 1c. Maintain a content-strips cache, like `RichLog.lines`

The widget renders Rich `Text` content into `Strip` objects once on every `update_lrc`, then serves them line-by-line from `render_line`. This avoids re-rendering on every repaint.

Add to `__init__`:
```python
self._content_strips: list[Strip] = []
self._render_width: int = 0
```

### 1d. Implement `render_line(y)` — the fix

```python
def render_line(self, y: int) -> Strip:
    """Render a single visible line, applying scroll offset.

    This is the canonical Line-API pattern (same as RichLog/Log/Tree/DataTable):
    translate the viewport y coordinate to the content y coordinate by adding
    scroll_offset.y, then return the matching content strip (or blank).
    """
    width = self.scrollable_content_region.width or self.size.width
    scroll_x, scroll_y = self.scroll_offset
    line_index = scroll_y + y

    if line_index < 0 or line_index >= len(self._content_strips):
        return Strip.blank(width, self.rich_style)

    strip = self._content_strips[line_index]
    # Apply horizontal scroll / width crop (consistent with other Line-API widgets).
    return strip.crop_extend(scroll_x, scroll_x + width, self.rich_style)
```

This is the *only* method needed to fix the rendering bug. It mirrors `RichLog._render_line` exactly: `scroll_y + y` arithmetic with width-based cropping.

### 1e. Rebuild `update_lrc` to populate `self._content_strips`

Replace the v1 `update_lrc` body with a version that builds strips directly from the rich Text (so `render_line` can serve them):

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
        text = Text(f'No LRC file found for "{song_title}"')
    else:
        self.remove_class("empty")
        text = self._build_lyrics_text(parsed, highlighted_index)

    # Render the Rich Text into Strips at the current content width.
    # Match RichLog.write's pattern: console.render + Segment.split_lines.
    width = self.scrollable_content_region.width or self.size.width or 1
    if width != self._render_width:
        self._render_width = width
    segments = self.app.console.render(
        text, self.app.console.options.update_width(width)
    )
    lines = list(Segment.split_lines(segments))
    self._content_strips = [Strip(line).adjust_cell_length(width) for line in lines]

    # Drive the scrollbar. virtual_size.height = total rendered line count,
    # virtual_size.width = current content width (no horizontal overflow desired).
    self.virtual_size = Size(width, max(1, len(self._content_strips)))

    if highlighted_index >= 0:
        self._scroll_to_highlight(highlighted_index)
    else:
        self.scroll_to(y=0, animate=False, immediate=True, force=True)

    self.refresh()
```

The `_build_lyrics_text` helper holds the existing metadata header + timed lines + highlight logic that currently lives inline in `update_lrc`. Pure refactor, no behavior change.

### 1f. Resize handling

Add `on_resize` so line-wrapping rebuilds on width changes:

```python
def on_resize(self, event: events.Resize) -> None:
    new_width = self.scrollable_content_region.width or self.size.width
    if new_width != self._render_width and self._parsed is not None:
        # Rebuild strips at new width. Use stored parsed content + current highlight.
        self.update_lrc(
            self._parsed, self._song_title, highlighted_index=self._highlighted_index
        )
```

### 1g. Keep the helper methods unchanged

The following methods work correctly with ScrollView as the base class — no changes needed:
- `_compute_content_height` — still useful for `_scroll_to_highlight` calculation.
- `_compute_highlighted_line_y` — pure calculation.
- `_scroll_to_highlight` — uses `self.scroll_to(y=center_y, ...)` which is correctly implemented on `ScrollView`. Update to use `self.size.height` (visible height) rather than relying on `virtual_size`.
- `scroll_line_up` / `scroll_line_down` — call inherited `ScrollView.scroll_up`/`scroll_down` (these correctly update `scroll_y` reactive and trigger `watch_scroll_y` -> `refresh(self.size.region)`).
- `set_highlighted_index`, `update_fetching`, `update_error`, `compute_highlighted_index` — unchanged.

For `update_fetching`/`update_error`, also clear `self._content_strips` and set `self.virtual_size = Size(width, 1)` so the panel renders one placeholder line.

### 1h. CSS — slight cleanup

The `DEFAULT_CSS` already sets `overflow-y: auto`, which `ScrollView.DEFAULT_CSS` also sets. To avoid duplication, drop the `overflow-y: auto` line from LyricsPanel's `DEFAULT_CSS` (inherited from ScrollView). Keep the panel-specific rules (background, padding, focus border, empty placeholder).

---

## Phase 2: Regression Tests

**File:** `ops/admin-cli/tests/admin/component_editor/test_lyrics_panel.py`

Add new test cases (do **not** delete existing v1 tests — they still verify scroll state for safety):

### 2a. Primary regression test (reproduces v1 bug)

`test_lyrics_rendered_content_shifts_on_scroll_down` — captured verbatim in the Reproduction section above.

### 2b. Counterpart for Up

```python
@pytest.mark.asyncio
async def test_lyrics_rendered_content_shifts_on_scroll_up():
    """Counterpart: scrolling back up returns visible strips to lyric line 0."""
    app, _state = _make_app()
    async with app.run_test(size=(80, 20)) as pilot:
        await pilot.pause()
        parsed = _make_parsed(num_lines=40)
        panel = await _setup_lyrics(pilot, parsed, highlighted_index=-1)

        # Scroll down then back up.
        for _ in range(5):
            panel.scroll_line_down()
        await pilot.pause()
        for _ in range(5):
            panel.scroll_line_up()
        await pilot.pause()
        assert panel.scroll_y == 0

        stripped = panel.render_lines(panel.region)
        text = "\n".join(strip.text for strip in stripped)
        assert "Line 0" in text
```

### 2c. Keyboard-path regression (same bug, via pilot.press)

```python
@pytest.mark.asyncio
async def test_lyrics_keyboard_scroll_shifts_rendered_content():
    """v1 regression: pressing Down arrow shifts the visible strips,
    not just the scroll_y state.
    """
    app, _state = _make_app()
    async with app.run_test(size=(80, 20)) as pilot:
        await pilot.pause()
        parsed = _make_parsed(num_lines=40)
        panel = await _setup_lyrics(pilot, parsed, highlighted_index=20)
        # Auto-scroll centres the highlight somewhere below the top.
        await pilot.pause()
        initial_strips = panel.render_lines(panel.region)
        initial_first = initial_strips[0].text

        # Press Down multiple times - visible first line MUST change.
        app.screen._active_panel = "right"
        app.screen._right_panel_mode = "lyrics"
        panel.focus()
        for _ in range(3):
            await pilot.press("down")
        await pilot.pause()

        new_strips = panel.render_lines(panel.region)
        new_first = new_strips[0].text
        assert new_first != initial_first  # <- would FAIL today (v1 bug)
```

### 2d. `render_line` returns blank beyond content bounds

```python
@pytest.mark.asyncio
async def test_lyrics_render_line_blank_beyond_content():
    """render_line(y) for y beyond content returns blank, not crashed."""
    app, _state = _make_app()
    async with app.run_test(size=(80, 30)) as pilot:
        await pilot.pause()
        parsed = _make_parsed(num_lines=5)  # short, fits in viewport
        panel = await _setup_lyrics(pilot, parsed, highlighted_index=-1)
        content_h = len(panel._content_strips)
        # Lines beyond content should be blank.
        empty = panel.render_line(content_h)
        from textual.strip import Strip
        assert isinstance(empty, Strip)
        # Blank strips have no segments or all-segments-blank
        assert empty.cell_length == panel.scrollable_content_region.width or empty.cell_length == 0
```

---

## Phase 3: Edge Cases

| Case | Behavior |
|---|---|
| No LRC for song (`parsed is None`) | `_content_strips` rebuilt from 1-line placeholder text. `virtual_size.height = 1`. No scroll possible. Empty CSS class applied for placeholder styling. |
| Short lyrics (fits in viewport) | `len(_content_strips) <= panel.size.height`, so `max_scroll_y == 0`. Auto-scroll computes `center_y = max(0, target - vh/2) = 0`. No visible scroll; `render_line` returns blanks for `y > content_h`. |
| Before playback starts (`highlighted_index == -1`) | `scroll_to(y=0, ...)` called explicitly; visible strips show metadata + `Line 0`. |
| Song switch | `_refresh_lyrics_panel()` calls `update_lrc(new_parsed, new_title, highlighted_index=idx)`, which rebuilds strips + virtual_size, then scrolls to highlight. |
| Manual scroll then auto-scroll on highlight change | `set_highlighted_index(idx)` only fires `update_lrc` when `idx != self._highlighted_index` (early-return at `lyrics_panel.py:187-188`). When it fires, the new auto-scroll overrides manual position — documented v1 behavior, unchanged in v2. |
| Resize (width change) | `on_resize` triggers `update_lrc` to rebuild strips at new width (line-wrapping re-applies). |
| 5Hz highlight updates (no change) | Early-return in `set_highlighted_index` prevents unnecessary strip rebuilds. |
| Initial mount (before `update_lrc`) | `_content_strips == []`; `render_line(y)` returns `Strip.blank(...)` for all y. Matches v1 behavior of empty Static. |
| `console` access in test environment | `app.console` is provided by Textual's `run_test()` — same console used by RichLog, no special mock needed. |

---

## Files Changed

| File | Change type | Est. LOC |
|---|---|---|
| `ops/admin-cli/src/stream_of_worship/admin/component_editor/lyrics_panel.py` | **Rewrite** — base class `Static` -> `ScrollView`; remove broken v1 overrides (`_size_updated`, `get_content_height`, `is_container`, `is_scrollable`); implement `render_line(y)`; refactor `update_lrc` to populate `_content_strips`; add `on_resize`. | ~70 net (delete ~30, add ~100) |
| `ops/admin-cli/tests/admin/component_editor/test_lyrics_panel.py` | **Add 4 tests** — `test_lyrics_rendered_content_shifts_on_scroll_down` (primary regression), `test_lyrics_rendered_content_shifts_on_scroll_up`, `test_lyrics_keyboard_scroll_shifts_rendered_content`, `test_lyrics_render_line_blank_beyond_content`. All assert on `render_lines(...)` output. | ~120 |
| `ops/admin-cli/src/stream_of_worship/admin/component_editor/screen.py` | **No changes** — `action_detail_focus_up/down` already call `panel.scroll_line_up/down()` correctly; `query_one("#lyrics-panel", LyricsPanel)` typings still work. | 0 |

**Total estimated churn:** ~190 LOC across 1 source file + 1 test file.

---

## Implementation Order

1. **Reproduction test first.** Add `test_lyrics_rendered_content_shifts_on_scroll_down` to `test_lyrics_panel.py`. Verify it fails (`AssertionError: "Line 0" found in post_text`). This confirms the bug exists and produces a clear before/after delta.
2. **Convert `LyricsPanel` to `ScrollView` subclass.** Remove the v1 overrides; add `_content_strips` cache and `render_line` implementation; refactor `update_lrc` to build strips directly.
3. **Verify reproduction test passes.** Run the test suite to confirm.
4. **Add the remaining regression tests** (2b, 2c, 2d).
5. **Run full suite + lint.**
6. **Manual smoke test** with a real song; verify you can scroll-read lyrics to the end of a long song.

---

## Verification Commands

```bash
# Reproduction + regression tests (run before and after the fix)
uv run --project ops/admin-cli --python 3.11 --extra admin --extra test \
    pytest tests/admin/component_editor/test_lyrics_panel.py -v

# Full component-editor test suite
uv run --project ops/admin-cli --python 3.11 --extra admin --extra test \
    pytest tests/admin/component_editor/ -v

# Lint + format
uv run --project ops/admin-cli ruff check src/stream_of_worship/admin/component_editor/
uv run --project ops/admin-cli ruff format --check src/stream_of_worship/admin/component_editor/

# Manual smoke test
uv run --project ops/admin-cli --extra admin sow-admin audio review-components <song_id>
#   1. Press 'v' if right panel isn't already in lyrics mode
#   2. Press Tab until lyrics panel is focused (border-left: double $accent)
#   3. Press Down repeatedly - visible lyric content must advance line-by-line
#   4. Press Up repeatedly - visible lyric content must retreat line-by-line
#   5. SPACE to play; auto-scroll to highlighted line should still work
```

---

## Backward Compatibility

- **No public API change.** `LyricsPanel.update_lrc()` signature unchanged; `compute_highlighted_index`, `set_highlighted_index`, `update_fetching`, `update_error` unchanged.
- **No CSS class changes.** `LyricsPanel`, `LyricsPanel:focus`, `LyricsPanel.empty` selectors all preserved.
- **No screen/layout changes.** `screen.py` is untouched; the only consumer of LyricsPanel (`query_one("#lyrics-panel", LyricsPanel)`) works identically.
- **Textual API surface used.** `textual.scroll_view.ScrollView`, `textual.strip.Strip`, `textual.geometry.Size`, `textual.events.Resize`. All present in textual >=0.44 (the project's minimum pin).
- **Textual version note.** Documentation referenced textual 8.x API during investigation (the project's `uv.lock` pins 8.2.7), but the *installed* version is 7.5.0. The proposed fix uses only APIs that exist in 7.5.0 (`ScrollView`, `render_line`, `Strip`, `scrollable_content_region`). If/when the lock is materialized to 8.x, the same code continues to work — `ScrollView` survives across both major versions.

---

## Why v1 missed this (postmortem)

V1's testing strategy asserted *scroll state* (numeric `panel.scroll_y`, `panel.max_scroll_y`), but never asserted on *visible content*. The Textual reactive system happily updated `scroll_y` and refreshed the scrollbar widget, satisfying all state-level assertions, while the actual `Widget.render_line` returned unchanged content strips for every `y`. The v1 fix therefore "succeeded" at the only level the tests measured, and the bug was only visible to humans interacting with the running TUI.

The lesson, encoded in the new regression tests: **scroll behavior must be asserted on `panel.render_lines(panel.region)` output** — never on `scroll_y` state alone.
