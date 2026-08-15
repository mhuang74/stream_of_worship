---

# Implementation Plan: Component Editor — Detail Panel Field Navigation (v1)

> **Date:** 2026-08-16
> **Branch:** TBD
> **Spec ID:** `component-editor-detail-panel-field-nav-v1`
> **Status:** Planning — not yet implemented

---

## Goal

Restore field-navigation behavior in `ComponentDetailPanel` (right panel of the Component Metadata editor TUI, details mode) that was lost when the panel was migrated to extend `textual.scroll_view.ScrollView`.

Specifically, after migration:
- ✅ Panel content long enough to overflow the viewport can now be scrolled.
- ❌ Pressing Up/Down no longer moves the "►" highlight marker among the editable fields (theme, vocal_posture, groove_density, energy_level, start_time, end_time), so the user cannot navigate to the field they want to edit.

This plan restores the focus-navigation behavior while preserving the new scrolling capability by **remapping scrolling to PageUp/PageDown** (and Home/End/mouse wheel) and **reclaiming Up/Down for editable-field focus navigation** only.

## Non-Goals

- No refactor of `ComponentDetailPanel` away from `ScrollView` (keep the Line-API render pattern).
- No changes to `LyricsPanel` semantics (it already supports up/down line-scroll and pageup/pagedown natively through its parent `ScrollableContainer`).
- No changes to LRC fetching, parsing, or the panel's playback highlight logic.
- No changes to autosave, undo/redo, save-to-DB/R2 flows.
- No changes to the editable-field set or column layout.

## User Decisions (from clarification Q&A)

| Question | Decision |
|---|---|
| Scroll interaction model | **Up/Down = field focus navigation** (details mode) with minimum bring-into-view auto-scroll. **PgUp/PgDown** = page-scroll in both panels (already inherited from `ScrollableContainer`). Mouse wheel + Home/End remain free-scroll. |
| Scroll-to-field position when focus moves | **Bring into view minimum** — only scroll if the focused field is outside the viewport; otherwise leave scroll position unchanged. Avoids jarring re-centers when scrolling is not needed. |

---

## Problem Analysis

### Root Cause

`ComponentDetailPanel` was migrated to subclass `textual.scroll_view.ScrollView` (detail_panel.py:34), with content rendered using the Line-API pattern (`render_line` returning a `Strip`, scroll offset applied internally — detail_panel.py:78–94).

`ScrollView` inherits from `textual.containers.ScrollableContainer`, which declares the following **non-priority** key bindings (verified via Textual 8.2.7 source at `textual/containers.py:32–74`):

```python
BINDINGS = [
    Binding("up", "scroll_up", "Scroll Up", show=False),
    Binding("down", "scroll_down", "Scroll Down", show=False),
    Binding("left", "scroll_left", "Scroll Left", show=False),
    Binding("right", "scroll_right", "Scroll Right", show=False),
    Binding("home", "scroll_home", "Scroll Home", show=False),
    Binding("end", "scroll_end", "Scroll End", show=False),
    Binding("pageup", "page_up", "Page Up", show=False),
    Binding("pagedown", "page_down", "Page Down", show=False),
    Binding("ctrl+pageup", "page_left", ...),
    Binding("ctrl+pagedown", "page_right", ...),
]
```

`ComponentDetailPanel` does **not** declare its own `BINDINGS`, so it inherits every one of these. When the panel has focus:

1. **Key dispatch in Textual:** the non-priority pass walks the binding chain **focused widget → Screen → App** (bottom-up). The widget's inherited `up`/`down` bindings (`scroll_up`/`scroll_down`) match first and handle the event.
2. The screen-level `Binding("up", "detail_focus_up", "Up")` (`screen.py:441`) and `Binding("down", "detail_focus_down", "Down")` (`screen.py:442`) never fire when the DetailPanel is focused.
3. Therefore `action_detail_focus_up` / `action_detail_focus_down` (`screen.py:1142–1166`) — which call `panel.move_focus_up()` / `panel.move_focus_down()` — are silently bypassed, and the `_focus_idx` cursor stays pinned at 0.

The net effect: up/down scrolls the panel (good), but the "►" highlight marker never moves and the field the user wants to edit cannot be selected.

### Secondary Issue: `update_detail` Resets Scroll to Top

Even if the binding conflict were solved, `update_detail` ends with:

```python
self.scroll_to(y=0, animate=False, immediate=True, force=True)  # detail_panel.py:246
```

Because `move_focus_up`/`move_focus_down` trigger a screen-driven refresh via `_refresh_detail_panel` → `panel.update_detail(state)`, every focus change would snap the panel back to line 0 — defeating any auto-scroll-to-focused-field logic. This call must be conditional.

### Textual Binding Resolution (verified against 8.2.7)

- Bindings are merged at class-definition time via `DOMNode.__init_subclass__` → `_merge_bindings()` (`textual/dom.py:565–595, 671–695`). The merge walks the MRO base-first, subclass-last, and for each key the **last assignment wins**.
- A subclass that declares `BINDINGS = [Binding("up", "my_action", ...)]` **replaces** (does not stack with) the inherited `up` binding from `ScrollableContainer`. Other inherited keys (pageup, pagedown, home, end, ctrl+pageup, ctrl+pagedown) survive untouched.
- `priority=True` causes a binding to fire in a separate first pass (App → Screen → focused widget, top-down). Not needed for our case — declaring the binding on the panel subclass is sufficient to override the inherited `up`/`down` and take precedence over the screen-level bindings (both are non-priority, so the binding chain walks focused widget first).

---

## Solution Design

### Phase 1: Override Up/Down in `ComponentDetailPanel`

**File:** `ops/admin-cli/src/stream_of_worship/admin/component_editor/detail_panel.py`

#### 1a. Declare panel-level `BINDINGS` to reclaim Up/Down

Add a `BINDINGS` class variable on `ComponentDetailPanel` that re-binds `up`/`down` to panel-local actions. Other inherited keys (`pageup`, `pagedown`, `home`, `end`, `ctrl+pageup`, `ctrl+pagedown`, `left`, `right`) remain inherited from `ScrollableContainer` and continue to provide free-scroll navigation — no further work needed.

```python
from typing import ClassVar
from textual.binding import Binding

class ComponentDetailPanel(ScrollView, can_focus=True):
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("up", "focus_up", "Field ↑", show=False),
        Binding("down", "focus_down", "Field ↓", show=False),
    ]
```

Why no `priority=True`: the binding only needs to take precedence over `ScrollableContainer`'s inherited `up`/`down` when the panel has focus. Per the merge rule (key-collision resolved in subclass-first MRO order), the panel's binding replaces the inherited one. Other key handlers on the screen (`Binding("up", "detail_focus_up")`) become dead code when the panel has focus — handled in Phase 3.

#### 1b. Cache state for re-render after focus-only change

Add a private attribute to cache the most recent `ComponentEditorState` passed to `update_detail`. This avoids a screen round-trip on focus moves (the panel already owns the rendering logic; it just needs the state snapshot).

```python
def __init__(self, **kwargs) -> None:
    super().__init__(**kwargs)
    self._focus_idx: int = 0
    self._content_strips: list[Strip] = []
    self._render_width: int = 0
    self._last_text: Text | None = None
    self._last_state: ComponentEditorState | None = None  # NEW
```

#### 1c. Parameterize `update_detail` scroll behavior

Add a `reset_scroll: bool = True` keyword parameter. When `True` (default — preserved across all existing callers via screen's `_refresh_detail_panel`), the panel resets scroll to the top. When `False`, the existing `scroll_y` is preserved (used for the new focus-move path).

- Cache `state` in `self._last_state` at the top of `update_detail`.
- Replace the unconditional `self.scroll_to(y=0, ...)` at `detail_panel.py:246` (and the early-return path at `detail_panel.py:145`) with a conditional that only fires when `reset_scroll=True`.

```python
def update_detail(self, state: ComponentEditorState, reset_scroll: bool = True) -> None:
    self._last_state = state
    # ... existing rendering logic that builds `text` ...
    self._last_text = text
    self._rebuild_strips(text)
    if reset_scroll:
        self.scroll_to(y=0, animate=False, immediate=True, force=True)
    self.refresh()
```

Apply the same `reset_scroll`-conditional to the early-return path where `comp is None` (line 145 area).

#### 1d. Add `_scroll_focused_into_view()` (minimum bring-into-view)

New method that scrolls the panel so the focused editable field line is within the viewport. Uses the existing `get_editable_field_line_offset` helper (`detail_panel.py:261–278`), which returns the 0-based content-line offset of a given editable field (currently hardcoded as `17 + field_idx`).

Algorithm: if the field's target line is below the current viewport, scroll down just enough to show the field at the bottom of the viewport; if above, scroll up so it sits at the top; if already visible, do nothing.

```python
def _scroll_focused_into_view(self) -> None:
    """Bring the focused editable field line into view (minimum scroll).

    Mirrors Textual's scroll-into-view semantics for focused widgets: only
    scroll if the target is outside the current viewport; never re-center.
    """
    field = self.focused_field
    target_y = self.get_editable_field_line_offset(field)
    viewport_h = self.size.height
    if viewport_h <= 0:
        return
    scroll_y = self.scroll_y
    if target_y < scroll_y:
        self.scroll_to(y=target_y, animate=False, immediate=True, force=True)
    elif target_y >= scroll_y + viewport_h:
        new_y = target_y - viewport_h + 1
        max_y = max(0, self.virtual_size.height - viewport_h)
        self.scroll_to(
            y=min(new_y, max_y), animate=False, immediate=True, force=True
        )
```

#### 1e. Add `action_focus_up` / `action_focus_down` action handlers

These are invoked by the panel's own `BINDINGS` from Phase 1a. The flow:

1. Move the focus index via the existing `move_focus_up` / `move_focus_down` (which already guard bounds at 0..`len(EDITABLE_FIELDS) - 1`).
2. Re-render strips WITHOUT resetting scroll — invoke `update_detail(self._last_state, reset_scroll=False)`. This rebuilds the Text with the new `►` marker + reverse highlight on the newly focused field and updates `_content_strips` so `render_line` shows the new highlight. The text layout itself does not change (field order is fixed), so `virtual_size` stays stable and `scroll_y` is preserved.
3. Call `_scroll_focused_into_view()` to bring the focused field into view if it's off-screen.

```python
def action_focus_up(self) -> None:
    """Handle Up arrow: move focus to previous editable field, re-render
    with new highlight, and bring focused field into view."""
    self.move_focus_up()
    self._rebuild_after_focus_change()

def action_focus_down(self) -> None:
    """Handle Down arrow: move focus to next editable field, re-render
    with new highlight, and bring focused field into view."""
    self.move_focus_down()
    self._rebuild_after_focus_change()

def _rebuild_after_focus_change(self) -> None:
    """Rebuild strips with current _focus_idx (new highlight marker) without
    changing scroll position; then bring the focused field into view."""
    if self._last_state is None:
        return
    self.update_detail(self._last_state, reset_scroll=False)
    self._scroll_focused_into_view()
```

### Phase 2: Verify LyricsPanel parity (no code changes)

**File:** `ops/admin-cli/src/stream_of_worship/admin/component_editor/lyrics_panel.py`

Verify only — no edits required. `LyricsPanel(ScrollView, can_focus=True)` already:

- Declares `BINDINGS` that re-bind `home`/`end`/`pageup`/`pagedown` to the same actions they inherited from `ScrollableContainer`. This is redundant but harmless (and serves as documentation that these are the intended scroll keys).
- Inherits the `up`/`down` bindings from `ScrollableContainer` → `scroll_up`/`scroll_down` (line-by-line scroll). Matches the user's intent: up/down still scrolls the lyrics panel.
- Inherits `scroll_up`/`scroll_down`/`page_up`/`page_down`/`scroll_home`/`scroll_end` (and more) for free.

Acceptable verification: launch the TUI, enter lyrics mode (press `v` to cycle the right panel), focus the lyrics panel (Tab), and confirm Up/Down scrolls line-by-line while PageUp/PageDown scrolls page-by-page.

### Phase 3: Clean up now-dead screen action branches

**File:** `ops/admin-cli/src/stream_of_worship/admin/component_editor/screen.py`

The screen-level `Binding("up", "detail_focus_up", "Up")` and `Binding("down", "detail_focus_down", "Down")` (`screen.py:441–442`) no longer fire when `ComponentDetailPanel` has focus — the panel's own `up`/`down` bindings take precedence. They still fire when:

- `LyricsPanel` is focused (lyrics mode) — but again, `LyricsPanel` inherits `up`/`down` → `scroll_up`/`scroll_down` from `ScrollableContainer`, so the widget binding fires first and the screen-level action is bypassed here too.
- No widget with an `up`/`down` binding has focus — but in practice the table is always focused in left-panel-active state, and `DataTable` has its own `up`/`down` bindings.

Net: `action_detail_focus_up` / `action_detail_focus_down` are effectively dead code paths in both modes. They are currently still useful for the footer display labels (`BINDING_GROUPS["Panels"]` references `detail_focus_up`/`detail_focus_down` at `screen.py:464–465` for the keymap legend).

#### 3a. Simplify the action handlers (RECOMMENDED — chosen)

Keep the bindings for the footer legend, but reduce the handlers to a no-op gesture (or `pass`) so they cannot cause confusing behavior if invoked via `app.action>detail_focus_up`-style routing:

```python
def action_detail_focus_up(self) -> None:
    if self._guard_active_edit():
        return
    # Field navigation in details mode is handled by ComponentDetailPanel's
    # own bindings (overrides inherited ScrollView up/down). LyricsPanel
    # uses inherited ScrollableContainer up/down for line scroll.
    # Kept for the footer keymap display only.

def action_detail_focus_down(self) -> None:
    if self._guard_active_edit():
        return
    # See action_detail_focus_up above.
```

#### 3b. (Alternative — NOT chosen) Remove the bindings entirely

Delete the two `Binding("up", "detail_focus_up")` / `Binding("down", "detail_focus_down")` lines from `BINDINGS` and the corresponding entries in `BINDING_GROUPS["Panels"]`. The footer will no longer show "Up" / "Down" hints — instead add two new lines (or extend an existing group) describing the panel-local bindings:

```python
# Under ComponentDetailPanel's own BINDINGS, displayed via footer.
# Move the "Field ↑/↓" labels under a "Detail Panel" footer group that
# reads ComponentDetailPanel.BINDINGS.
```

Option 3a is recommended because it keeps the footer's existing Panels-group layout unchanged. The footer reads `BINDINGS` + `BINDING_GROUPS` from the screen (`GroupedFooter` in `editor/footer.py`), so the labels remain visible.

### Phase 4: Verify edit-overlay positioning works with auto-scroll

**File:** `ops/admin-cli/src/stream_of_worship/admin/component_editor/screen.py`

The `_show_value_edit_input` method (`screen.py:867–898`) computes the Input overlay Y position from `detail_panel.region.y + editable_section_line - scroll_y` and aborts with "Scroll to the field to edit" if the field is outside the viewport (`screen.py:881–883`).

With Phase 1d's `_scroll_focused_into_view`, the focused field is always in view by the time the user presses `e` (because Up/Down navigation auto-scrolls). But there's an edge case: the user can still free-scroll with PageUp/PageDown such that the focused field scrolls out of view, then press `e`. In that case the existing "Scroll to the field to edit" notify + early-return remains the correct UX.

No changes required. Verify that:

- After Up/Down focus moves to a field that was previously below the viewport, pressing `e` shows the Input overlay on the correct line.
- The Input overlay's position is recalculated on resize (`on_resize` at `screen.py:964–985`) — already implemented.

---

## Files Changed

| File | Change type | Est. LOC |
|---|---|---|
| `ops/admin-cli/src/stream_of_worship/admin/component_editor/detail_panel.py` | **Edit** — add `BINDINGS` for up/down, add `_last_state` cache, parameterize `update_detail(reset_scroll=True)`, add `_scroll_focused_into_view` and `action_focus_up`/`action_focus_down` + helper `_rebuild_after_focus_change` | ~45 |
| `ops/admin-cli/src/stream_of_worship/admin/component_editor/screen.py` | **Edit** — simplify now-dead `action_detail_focus_up` / `action_detail_focus_down` to guarded no-ops (keep bindings for footer display) | ~12 |
| `tests/admin/component_editor/test_detail_panel.py` (existing if any, else new) | **New/Edit** — add automated tests (see Phase 5) | ~120 |

**Total estimated additions:** ~175 LOC across 2 edits + 1 new/edit test file.

---

## Edge Cases

| Case | Handling |
|---|---|
| First field, press Up | `move_focus_up` already no-ops at `_focus_idx == 0` (no scroll, no re-render net-effect). `_scroll_focused_into_view` finds target inside viewport and is a no-op. |
| Last field, press Down | `move_focus_down` no-ops at `_focus_idx == len(EDITABLE_FIELDS) - 1`. Same as above. |
| Field already in viewport | `_scroll_focused_into_view` detects `scroll_y <= target_y < scroll_y + viewport_h` and does nothing. Material new "►" highlight + reverse style still applied via re-render. |
| Field above viewport (user PageUp-scrolls past it) | PageUp doesn't change `_focus_idx`. Field's target_y < scroll_y → auto-scroll up so the field sits at the top of viewport. |
| Field below viewport (user PageDown-scrolls past it) | Field's target_y >= scroll_y + viewport_h → auto-scroll down by `target_y - viewport_h + 1` lines (clamped to `virtual_size.height - viewport_h`). |
| `update_detail` called with `reset_scroll=False` before first `reset_scroll=True` call (i.e., `_last_state is None`) | `_rebuild_after_focus_change` early-returns when `_last_state is None` — no re-render, no scroll. |
| Song switch invokes `_switch_song` → `_refresh_detail_panel` (re-runs `update_detail(state)` with default `reset_scroll=True`) | Scroll resets to top + new song's editable fields render with `_focus_idx=0` (carries over from previous song — acceptable, matches existing behavior). |
| Table cursor change (left-panel active) invokes `_sync_selection_from_table_cursor` → `_refresh_detail_panel` | `update_detail(state)` with default `reset_scroll=True` → scroll to top. Matches existing behavior on role change. |
| User edits a value (`action_edit_numeric` → `on_input_submitted`) → `_refresh_detail_panel` called with `reset_scroll=True` | Scroll resets to top. This is the **current** behavior and still acceptable — but worth flagging as a follow-up if the user finds it jarring. (Out of scope for this spec: could be changed to `reset_scroll=False` + re-scroll-to-focused.) |
| Resize while focused field is in-view | `on_resize` triggers `_rebuild_strips(self._last_text)` (existing). Scroll position preserved by ScrollView. `_scroll_focused_into_view` is **not** auto-called on resize — acceptable. |
| `update_detail` rendered Text layout shifts (e.g., reasoning text grows on undo) | `virtual_size.height` may change. `_scroll_focused_into_view` reads the updated `virtual_size.height` (after `_rebuild_strips`) and clamps appropriately. |
| Right panel transitions hidden → details (press `v` twice) | `_apply_right_panel_mode` calls `_refresh_detail_panel` with default `reset_scroll=True` → scroll to top + `_focus_idx` carries over. Acceptable: returning to details mode shows top-of-content. |
| Right panel in lyrics mode, press Up/Down | `LyricsPanel` inherits `up`/`down` from `ScrollableContainer` → `scroll_up`/`scroll_down` line scroll. The screen-level `detail_focus_up` / `detail_focus_down` no longer fire (was already dead code before this spec — see Phase 3). |

---

## Phase 5: Testing

### Manual verification

```bash
uv run --project ops/admin-cli --extra admin sow-admin audio review-components <song_id_with_long_detail_panel>
```

Verify:

1. Press `v` to cycle right panel to details mode (or `Tab` to focus the right panel).
2. Press Up/Down repeatedly — "►" marker + reverse highlight moves among the six editable fields (theme, vocal_posture, groove_density, energy_level, start_time, end_time).
3. Press PageDown several times — panel content scrolls down a page at a time; "►" marker stays where it was on the focused field.
4. Continue pressing Down — when the next focused field is below the viewport, the panel auto-scrolls (minimum) to bring it into view.
5. Continue pressing Up — when the next focused field is above the viewport, the panel auto-scrolls up.
6. With focused field visible, press PageUp then Down — the panel scrolls up only enough to show the field; no re-center jank.
7. Press `e` on a focused numeric field — Input overlay appears on the focused line. Press Esc to cancel; focus returns to the table? (Actually, `_cancel_row_edit` refocuses the detail panel — verify.)
8. Lyric-panel parity: Press `v` to switch to lyrics mode, Tab to focus lyrics panel. Up/Down scrolls one line; PageUp/PageDown scrolls a page; Home/End jump to top/bottom.
9. Table is still usable: Tab to focus the table (left panel). Up/Down moves the highlighted row (entry → exit etc.). PageUp/PageDown still range-over within the table (existing DataTable behavior).

### Automated tests

**File:** `tests/admin/component_editor/test_detail_panel.py` (new — none exist currently for the detail panel)

```python
async def test_detail_panel_up_arrow_moves_focus(...):
    """Pressing Up on focused ComponentDetailPanel decrements _focus_idx
    and re-renders with the marker on the previous field."""

async def test_detail_panel_down_arrow_moves_focus(...):
    """Pressing Down on focused ComponentDetailPanel increments _focus_idx
    and re-renders with the marker on the next field."""

async def test_detail_panel_focus_clamps_at_bounds(...):
    """Down past the last field stays at the last field; Up before the first
    does nothing."""

async def test_detail_panel_update_detail_default_resets_scroll(...):
    """Calling update_detail(state) with defaults scrolls to top."""

async def test_detail_panel_update_detail_preserve_scroll(...):
    """Calling update_detail(state, reset_scroll=False) preserves current
    scroll_y value."""

async def test_detail_panel_scroll_into_view_when_field_below(...):
    """When _focus_idx moves to a field whose content-line offset is below
    the viewport, scroll_y advances so the field is visible."""

async def test_detail_panel_scroll_into_view_when_field_above(...):
    """When _focus_idx moves to a field whose content-line offset is above
    the viewport, scroll_y retreats so the field is visible."""

async def test_detail_panel_scroll_into_view_noop_when_visible(...):
    """When _focus_idx moves to a field already within the viewport,
    scroll_y is unchanged."""

async def test_detail_panel_pageup_pagedown_scroll_without_focus_change(...):
    """PgUp/PgDown change scroll_y without mutating _focus_idx."""

async def test_detail_panel_inherited_home_end_scroll(...):
    """Home scrolls to top; End scrolls to bottom. _focus_idx unchanged."""

async def test_action_detail_focus_up_is_noop_now(...):
    """Screen-level action_detail_focus_up does not raise when invoked;
    field navigation is handled by the panel's own bindings."""
```

### Verification checklist

- [ ] Up/Down moves "►" marker among the six editable fields while the DetailPanel is focused
- [ ] Focus navigation auto-scrolls to bring the focused field into view (minimum, not center)
- [ ] Focus already in viewport → no scroll change when navigating (no jitter)
- [ ] PageUp / PageDown still scroll the panel content (free-scroll without focus change)
- [ ] Home / End (inherited from ScrollableContainer) jump to top / bottom
- [ ] Mouse wheel scroll still works (inherited)
- [ ] Escaping edit overlay returns focus to DetailPanel and `e` re-opens it correctly
- [ ] Table (left-panel) Up/Down still navigates table rows unaffected (DataTable's own bindings handle it)
- [ ] LyricsPanel Up/Down still scrolls one line; PgUp/PgDown scroll a page (no regression)
- [ ] Song switch, undo/redo, save, autosave recovery all still work and reset scroll to top (acceptable)
- [ ] Resize keeps focused field in view if it was visible before resize

---

## Implementation Order

1. **Phase 1** (`detail_panel.py`): add `BINDINGS`, add `_last_state` cache, parameterize `update_detail(reset_scroll=True)`, implement `action_focus_up`/`action_focus_down`/`_scroll_focused_into_view`/`_rebuild_after_focus_change`.
2. **Phase 3** (`screen.py`): simplify now-dead `action_detail_focus_up`/`action_detail_focus_down` to guarded no-ops while keeping the `Binding` declarations for the footer legend.
3. **Phase 5** (tests): add the automated tests under `tests/admin/component_editor/test_detail_panel.py` (or extend existing test file if present).
4. Manual smoke test.

---

## Verification Commands

```bash
# Automated tests for the component editor suite
uv run --project ops/admin-cli --python 3.11 --extra admin --extra test \
    pytest tests/admin/component_editor/ -v

# Lint + format check (Black line-length 100; Ruff py311)
uv run --project ops/admin-cli ruff check \
    src/stream_of_worship/admin/component_editor/
uv run --project ops/admin-cli ruff format --check \
    src/stream_of_worship/admin/component_editor/

# Manual smoke test
uv run --project ops/admin-cli --extra admin sow-admin audio review-components <song_id>
```

---

## Backward Compatibility

- **No API changes that affect callers** — `update_detail(state)` signature is backward-compatible (the new `reset_scroll: bool = True` parameter has a default).
- **No CSS changes.**
- **No state-schema changes** — `ComponentEditorState`, `SongSession`, `ComponentAutosaveState` untouched.
- **No autosave format changes** — `_focus_idx` was never persisted to autosave (it's a transient UI cursor) and the spec does not add persistence.
- **No key conflicts** — Up/Down were already assigned to `detail_focus_up`/`detail_focus_down` in the screen's `BINDINGS`; those screen-level bindings become dead code when DetailPanel has focus. The footer keymap legend continues to show "Up"/"Down" under the Panels group (cleaner if Phase 3a is chosen over 3b).

---

## Open Questions (deferred, out of scope)

1. **Reset-scroll on save / undo / redo:** after `action_save`, `action_undo`, `action_redo`, the panel currently resets scroll to top via the default `_refresh_detail_panel` path. Should this be changed to `reset_scroll=False` + scroll-to-focused? (Out of scope; flag for a follow-up spec.)
2. **Persist `_focus_idx` across song switches:** currently `_focus_idx` carries over from one song to the next, which may surprise users who expected to start at the first field. (Out of scope.)
3. **`get_editable_field_line_offset` hardcoded to `17 + field_idx`:** if anyone rearranges the rendered-text layout in `update_detail` (e.g., by inserting a new section above the editable block), this constant will silently drift. Consider replacing with a runtime scan that finds the field-line in `_content_strips` by marker. (Out of scope; noted as fragility.)

---

**End of spec.**
