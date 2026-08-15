# Handover: Fix LyricsPanel Manual Scroll Not Working

> **Date:** 2026-08-15
> **Spec:** `specs/fix-lyrics-panel-scroll-v2.md`
> **Status:** Implementation ~90% complete — 2 of 15 tests still failing
> **Branch:** Working tree (not yet committed)

---

## Objective

Fix the LyricsPanel manual scroll (Up/Down keys) not working in the Component Metadata Editor TUI for songs with lyrics longer than the viewport.

## Problem Summary

`LyricsPanel(Static)` with `height: 1fr; overflow-y: auto;` — the Textual compositor sets `virtual_size = size` for non-container `Static` widgets, so `max_scroll_y = 0` and scrolling never works. Additionally, the old code used `set_reactive(Widget.scroll_y, value)` which bypassed `validate_scroll_y`, `watch_scroll_y`, and `refresh(repaint=True)` — the widget was never marked dirty, so the compositor never re-rendered with the new scroll offset.

## What Was Done

### 1. Rewrote `lyrics_panel.py` (COMPLETE)

**File:** `ops/admin-cli/src/stream_of_worship/admin/component_editor/lyrics_panel.py`

Adopted the `ScrollView` pattern (same approach used by Textual's `RichLog` and `Log` widgets):

- **Added `_size_updated` override** (lines 58-74) that preserves `self.virtual_size` instead of letting the compositor overwrite it with `size`. This is the core fix — it prevents `max_scroll_y` from being reset to 0.
- **Added `is_container` → `False`** (lines 52-53) — prevents the compositor from treating the panel as a container that auto-sizes to children.
- **Added `get_content_height` → `virtual_size.height`** (lines 55-56) — returns the preserved virtual size height.
- **Removed** custom `allow_vertical_scroll`, `max_scroll_y`, `validate_scroll_y` overrides — let the base `Widget` class handle these using the now-correct `virtual_size`.
- **Removed** dead `_update_virtual_size()` method.
- **Replaced** `set_reactive(Widget.scroll_y, ...)` with `self.scroll_to(y=..., animate=False, immediate=True, force=True)` in:
  - `_scroll_to_highlight()` (line 170)
  - `update_lrc()` reset-to-top path (line 143)
  - `scroll_line_up()` (line 174) — uses `self.scroll_up(animate=False, immediate=True, force=True)`
  - `scroll_line_down()` (line 178) — uses `self.scroll_down(animate=False, immediate=True, force=True)`
- **Sets `self.virtual_size`** directly in `update_lrc()` (line 139), `update_fetching()` (line 199), `update_error()` (line 207).
- **Removed** unused imports (`Size` was kept since it's used for `virtual_size` assignment, `clamp` and `Widget` removed).

### 2. Updated Tests (MOSTLY COMPLETE)

**File:** `ops/admin-cli/tests/admin/component_editor/test_lyrics_panel.py`

- **Added `_setup_lyrics()` test helper** (lines 134-158) that:
  - Populates `state.lrc_parsed[song_id]` with test data before calling `panel.update_lrc()` — prevents the async LRC prefetch worker from overwriting test data when it completes during `pilot.pause()`.
  - Sets `state.lrc_prefetch_in_progress = False` — prevents `_refresh_lyrics_panel()` from showing "Loading..." instead of lyrics.
  - Sets `pilot.app.screen.playback._position_seconds` to match the highlighted index — prevents the 5Hz position update timer from resetting the highlight (and thus scroll_y) to a different line.
- **Updated all 12 async tests** to use `_setup_lyrics()` instead of directly calling `panel.update_lrc()`.
- **Added 3 regression tests:**
  - `test_lyrics_manual_scroll_persists_after_pause` — verifies `scroll_y` persists after `pilot.pause()`
  - `test_lyrics_keyboard_scroll_down_persists` — verifies keyboard Down press scrolls and persists
  - `test_lyrics_max_scroll_y_matches_virtual_size` — verifies `max_scroll_y` uses `virtual_size.height`

### 3. Spec Document (NEEDS UPDATE)

**File:** `specs/fix-lyrics-panel-scroll-v2.md`

The spec was written before implementation and describes the original plan (remove overrides + replace `set_reactive`). The actual implementation went further by adopting the `ScrollView` pattern (`_size_updated` override, `is_container`, `get_content_height`). The spec needs to be updated to reflect the actual implementation.

## Current Test Results

**13 of 15 tests passing.** 2 failing:

```
FAILED test_lyrics_no_scroll_without_highlight
FAILED test_lyrics_keyboard_scroll_down_persists
```

### Failing Test 1: `test_lyrics_no_scroll_without_highlight`

```python
async def test_lyrics_no_scroll_without_highlight():
    app, _state = _make_app()
    async with app.run_test(size=(80, 20)) as pilot:
        await pilot.pause()
        parsed = _make_parsed(num_lines=40)
        panel = await _setup_lyrics(pilot, parsed, highlighted_index=10)
        assert panel.scroll_y > 0           # PASSES — scroll_y == 7
        # Now reset with no highlight
        panel.update_lrc(parsed, "Test Song", highlighted_index=-1)
        await pilot.pause()
        await pilot.pause()
        await pilot.pause()
        assert panel.scroll_y == 0          # FAILS — scroll_y == 7
```

**Root cause:** After `_setup_lyrics` sets playback position to `50.0` (index 10 × 5s), the test calls `panel.update_lrc(parsed, "Test Song", highlighted_index=-1)` directly (not through `_setup_lyrics`). This sets `scroll_y` to 0 via `scroll_to(y=0, ...)`. But then during `pilot.pause()`, the 5Hz timer fires and calls `_update_lyrics_highlight()`, which computes `compute_highlighted_index(parsed, 50.0)` → returns index 10 (since position is still 50.0). This calls `panel.set_highlighted_index(10)`, which calls `update_lrc(parsed, song_title, highlighted_index=10)`, which scrolls back to center line 10 → `scroll_y = 7`.

**Fix:** After calling `panel.update_lrc(parsed, "Test Song", highlighted_index=-1)`, also reset the playback position to 0.0 so the 5Hz timer computes `highlighted_index = 0` (or -1 if position is before the first line). Or better: set `pilot.app.screen.playback._position_seconds = 0.0` before the `pilot.pause()` calls. Actually, with position 0.0, `compute_highlighted_index` returns 0 (first line has `time_seconds = 0.0`, and `0.0 <= 0.0` is True). So `set_highlighted_index(0)` would be called, which calls `update_lrc(parsed, song_title, highlighted_index=0)`, which scrolls to center line 0 → `center_y = max(0, 3 - 10) = 0` → `scroll_y = 0`. That should work.

Alternatively, set position to -1.0 (before first line) so `compute_highlighted_index` returns -1, and `set_highlighted_index(-1)` is a no-op if `_highlighted_index` is already -1.

### Failing Test 2: `test_lyrics_keyboard_scroll_down_persists`

```python
async def test_lyrics_keyboard_scroll_down_persists():
    app, _state = _make_app()
    async with app.run_test(size=(80, 20)) as pilot:
        await pilot.pause()
        parsed = _make_parsed(num_lines=40)
        panel = await _setup_lyrics(pilot, parsed, highlighted_index=20)
        initial_scroll = panel.scroll_y              # 17
        assert initial_scroll > 0                     # PASSES
        app.screen._active_panel = "right"
        app.screen._right_panel_mode = "lyrics"
        await pilot.press("down")
        await pilot.pause()
        assert panel.scroll_y == initial_scroll + 1  # FAILS — scroll_y == 17 (not 18)
```

**Root cause:** After `pilot.press("down")`, `action_detail_focus_down()` calls `panel.scroll_line_down()` which calls `self.scroll_down(animate=False, immediate=True, force=True)`. This should set `scroll_y` to 18. But then during `pilot.pause()`, the 5Hz timer fires and calls `_update_lyrics_highlight()`, which computes `compute_highlighted_index(parsed, 100.0)` → returns index 20 (position is 100.0 = 20 × 5s). This calls `panel.set_highlighted_index(20)`. Since `_highlighted_index` is already 20, `set_highlighted_index` returns early (no-op). So the highlight doesn't change... but `scroll_y` should still be 18 from the `scroll_line_down()` call.

Wait — the issue might be that `scroll_down()` with `immediate=True` doesn't actually work synchronously in the test context. Or the 5Hz timer might be calling something that resets scroll. Let me re-examine...

Actually, looking more carefully: `scroll_down()` calls `scroll_to(y=self.scroll_y + 1, ...)`. With `immediate=True`, this should set `scroll_y` synchronously. But `pilot.press("down")` might not trigger `action_detail_focus_down` synchronously — it posts a message that gets processed during `pilot.pause()`. By the time `action_detail_focus_down` runs and calls `scroll_line_down()`, the 5Hz timer might have already fired and reset things.

Actually, the more likely issue: `pilot.press("down")` sends a key event. The screen has a binding `Binding("down", "action_detail_focus_down", "Down")`. But the `down` key might be intercepted by the `LyricsPanel` widget itself (since it `can_focus = True` and might have its own scroll handling). Or the binding might not fire because the focused widget handles the key first.

**Possible fix:** Instead of `pilot.press("down")`, call `app.screen.action_detail_focus_down()` directly, then check `panel.scroll_y` immediately (before `pilot.pause()`). Or investigate whether the `down` key is being consumed by the widget's default scroll behavior vs. the screen binding.

Another possibility: `scroll_down(animate=False, immediate=True, force=True)` might not actually increment by exactly 1 — it might scroll by a different amount. Check Textual's `scroll_down` implementation: it calls `self.scroll_to(y=self.scroll_y + 1, ...)` — but wait, `scroll_down` in Widget takes a `lines` parameter defaulting to 1. Actually in Textual 8.2.7, `scroll_down` might call `scroll(y=1, ...)` which scrolls relative. Need to verify.

## Next Steps

### Step 1: Fix the 2 failing tests

**For `test_lyrics_no_scroll_without_highlight`:**
- After calling `panel.update_lrc(parsed, "Test Song", highlighted_index=-1)`, also set `pilot.app.screen.playback._position_seconds = -1.0` so the 5Hz timer computes `highlighted_index = -1` and doesn't override the panel.

**For `test_lyrics_keyboard_scroll_down_persists`:**
- Debug whether `pilot.press("down")` actually triggers `action_detail_focus_down`. Try calling `app.screen.action_detail_focus_down()` directly instead.
- Or check if `scroll_down()` actually increments by 1 in this context. Add a debug assertion right after `action_detail_focus_down()` before `pilot.pause()`.

### Step 2: Update the spec document

Update `specs/fix-lyrics-panel-scroll-v2.md` to reflect the actual implementation:
- The `ScrollView` pattern (`_size_updated` override, `is_container`, `get_content_height`)
- The `_setup_lyrics()` test helper and why it's needed
- The 5Hz timer interference issue and how tests work around it

### Step 3: Run broader test suite

```bash
uv run --project ops/admin-cli --python 3.11 --extra admin --extra test pytest ops/admin-cli/tests/admin/component_editor/ -v
```

Check for regressions in `test_screen.py` and other component editor tests.

### Step 4: Commit and push

```bash
git add ops/admin-cli/src/stream_of_worship/admin/component_editor/lyrics_panel.py \
       ops/admin-cli/tests/admin/component_editor/test_lyrics_panel.py \
       specs/fix-lyrics-panel-scroll-v2.md
git commit -m "fix: LyricsPanel manual scroll not working (ScrollView pattern)"
git push
```

## Key Files

| File | Role |
|------|------|
| `ops/admin-cli/src/stream_of_worship/admin/component_editor/lyrics_panel.py` | Main file — rewritten with ScrollView pattern (COMPLETE) |
| `ops/admin-cli/tests/admin/component_editor/test_lyrics_panel.py` | Test file — 13/15 passing, 2 failing |
| `ops/admin-cli/src/stream_of_worship/admin/component_editor/screen.py` | Screen with `_update_lyrics_highlight()` (5Hz timer), `_prefetch_lrc()`, `action_detail_focus_up/down` |
| `ops/admin-cli/src/stream_of_worship/admin/component_editor/state.py` | `ComponentEditorState` with `lrc_parsed`, `lrc_fetches`, `lrc_prefetch_in_progress` |
| `ops/admin-cli/src/stream_of_worship/admin/component_editor/lrc_fetch.py` | LRC fetch logic, has cache write that fails with MagicMock in tests |
| `specs/fix-lyrics-panel-scroll-v2.md` | Spec document (needs updating to match actual implementation) |
| `ops/admin-cli/.venv/lib/python3.11/site-packages/textual/widgets/_rich_log.py` | RichLog (ScrollView subclass) — reference implementation for the pattern |
| `ops/admin-cli/.venv/lib/python3.11/site-packages/textual/widget.py` | Textual 8.2.7 Widget source — `_size_updated`, `scroll_to`, `scroll_down`, `scroll_up` |

## Test Command

```bash
uv run --project ops/admin-cli --python 3.11 --extra admin --extra test pytest ops/admin-cli/tests/admin/component_editor/test_lyrics_panel.py -v
```

## Architecture Context

- **Textual version:** 8.2.7
- `LyricsPanel(Static)` — right panel in lyrics mode, shows timestamped LRC lyrics with playback-synced highlight
- **5Hz position update timer** in `ComponentEditorScreen._start_position_updates()` calls `_update_lyrics_highlight()` which computes `highlighted_index` from playback position and calls `panel.set_highlighted_index(idx)`. If the index changed, `set_highlighted_index` calls `update_lrc()` which re-renders and auto-scrolls to center the highlighted line — this can override manual scroll.
- **Async LRC prefetch worker** (`_prefetch_lrc`) runs on mount and calls `_refresh_lyrics_panel()` in its `finally` block — this can overwrite test-set panel content during `pilot.pause()`.
- **`_PlaybackStub`** in tests has `position_seconds = 0.0` by default. The 5Hz timer reads this to compute `highlighted_index`.
- **`compute_highlighted_index(parsed, position)`** returns the index of the last timed line whose `time_seconds <= position`. For `_make_parsed(num_lines=40)`, line `i` has `time_seconds = i * 5`. So position 0.0 → index 0, position 100.0 → index 20, etc.
