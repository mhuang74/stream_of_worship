---

# Enhance Component Metadata Editor TUI: Playback Stop, Detail Panel Scroll Fix, Editable Timing

> **Date:** 2026-08-16
> **Branch:** TBD
> **Spec ID:** `enhance-component-editor-playback-scroll-editable-timing-v1`
> **Status:** Plan — not yet implemented

---

## Overview

Three enhancements to the Component Metadata Editor TUI
(`ops/admin-cli/src/stream_of_worship/admin/component_editor/`):

1. **Component-scoped playback** — Playback starts at the selected component's
   `start_time` and **pauses when position reaches `end_time`** (instead of
   playing through the entire song).
2. **Detail Panel scroll fix** — Apply the same ScrollView Line-API pattern
   that fixed LyricsPanel scrolling (`fix-lyrics-panel-scroll-v2`) to
   `ComponentDetailPanel`, which currently cannot scroll when content exceeds
   the viewport.
3. **Editable start_time / end_time** — Allow the user to manually adjust
   `start_time` and `end_time` via the numeric edit overlay (`e` key) in the
   Detail Panel.

---

## 1. Component-Scoped Playback (Pause at end_time)

### Problem

`action_toggle_playback_for_component` in `screen.py:1178` starts playback
from the component's `start_time` but never stops — the audio plays through
the rest of the song. The original v3 spec explicitly chose "play through
the song naturally (no auto-stop at end_time)" but the user now wants playback
constrained to the component's time range so they **only listen to that
component's audio**.

### Design

When playback is active and the selected component has an `end_time`,
the 5Hz position update loop (`_update_playback_bar` / `_on_playback_position`)
checks if `position >= end_time`. If so, it calls `self.playback.pause()`.

**Behavior:**
- SPACE starts playback from the selected component's `start_time`.
- Playback continues until `position >= end_time`, at which point playback
  **pauses** (position stays at `end_time`, state becomes `PAUSED`).
- Pressing SPACE again restarts from `start_time` (the existing
  `action_toggle_playback_for_component` logic already handles this: if
  `is_playing` → pause; if paused/stopped → play from `start_time`).

**Key detail:** The check must use the *working* value of `end_time` (i.e.,
`state.get_value(role, "end_time")`), not the raw `comp.end_time` attribute,
so that edits to `end_time` in the Detail Panel take effect immediately.

### Implementation

**File:** `ops/admin-cli/src/stream_of_worship/admin/component_editor/screen.py`

#### 1.1 New method: `_check_component_playback_end`

```python
def _check_component_playback_end(self) -> None:
    """Pause playback if the position has reached the selected component's end_time.

    Called from the position update loop and the on_position_changed callback.
    Uses the working (edited) end_time value so edits take effect immediately.
    """
    if not self.playback.is_playing:
        return
    comp = self.state.get_selected_component()
    if comp is None:
        return
    role = self._selected_editor_role()
    end_time = self.state.get_value(role, "end_time")
    if end_time is None:
        return
    if self.playback.position_seconds >= end_time:
        self.playback.pause()
```

#### 1.2 Wire into the position update loop

In `_start_position_updates` (`screen.py:706`), the async loop currently
calls `_update_playback_bar()` and `_update_lyrics_highlight()` every 0.2s.
Add a call to `_check_component_playback_end()`:

```python
def _start_position_updates(self) -> None:
    async def _update_loop():
        while True:
            await asyncio.sleep(0.2)
            self._check_component_playback_end()
            self._update_playback_bar()
            self._update_lyrics_highlight()

    self._position_update_timer = asyncio.ensure_future(_update_loop())
```

#### 1.3 Wire into `_on_playback_position`

In `_on_playback_position` (`screen.py:723`), add the check before updating
the bar, so the pause happens promptly on the callback rather than waiting
for the next 0.2s tick:

```python
def _on_playback_position(self, position) -> None:
    self._check_component_playback_end()
    self._update_playback_bar()
    self._update_lyrics_highlight()
```

#### 1.4 Guard: only auto-stop when a component is selected

The check is skipped if `get_selected_component()` returns `None` or if
`end_time` is `None`. This means playback of songs without component analysis
still plays through normally.

---

## 2. Fix Detail Panel Scrolling

### Problem

`ComponentDetailPanel` (`detail_panel.py:30`) inherits from `Static` and uses
`overflow-y: auto` in its CSS. This is the exact same setup that caused the
LyricsPanel scroll bug (documented in `fix-lyrics-panel-scroll-v2.md`, Bug C):

> The Textual compositor sets `virtual_size = size` for non-container `Static`
> widgets, so `max_scroll_y = 0` and scrolling never works. The `Static`
> widget has no children/layout, so the compositor treats it as a leaf node and
> sets `virtual_size` equal to the actual `size`, ignoring any manually-set
> `virtual_size`.

When the Detail Panel's rendered content (Song Info + Component metadata +
Editable fields + Reasoning + Confidence Breakdown + Lifecycle = ~40+ lines)
exceeds the viewport height, the user cannot scroll to see the bottom
sections.

### Design

Apply the **ScrollView Line-API pattern** — the same fix used for `LyricsPanel`
in `fix-lyrics-panel-scroll-v2.md`. Convert `ComponentDetailPanel` from a
`Static` subclass to a `ScrollView` subclass using `render_line()`.

### Implementation

**File:** `ops/admin-cli/src/stream_of_worship/admin/component_editor/detail_panel.py`

#### 2.1 Change base class from `Static` to `ScrollView`

```python
from textual.scroll_view import ScrollView
from textual.geometry import Size
from textual.strip import Strip
from rich.segment import Segment

class ComponentDetailPanel(ScrollView, can_focus=True):
```

Remove `from textual.widgets import Static` import.

#### 2.2 Add instance attributes in `__init__`

```python
def __init__(self, **kwargs) -> None:
    super().__init__(**kwargs)
    self._focus_idx: int = 0
    self._content_strips: list[Strip] = []
    self._render_width: int = 0
    self._last_text: Text | None = None
```

#### 2.3 Line-API rendering

Add the same rendering infrastructure used by `LyricsPanel`:

**`render_line(self, y: int) -> Strip`:** Returns the visible line at viewport
coordinate `y`, translating to content coordinate via `scroll_offset.y`.

```python
def render_line(self, y: int) -> Strip:
    width = self.scrollable_content_region.width or self.size.width
    scroll_x, scroll_y = self.scroll_offset
    line_index = scroll_y + y

    if line_index < 0 or line_index >= len(self._content_strips):
        return Strip.blank(width, self.rich_style)

    strip = self._content_strips[line_index]
    return strip.crop_extend(scroll_x, scroll_x + width, self.rich_style)
```

**`_rebuild_strips(self, text: Text) -> None`:** Renders Rich Text into
`Strip` objects at the current content width, sets `virtual_size`.

```python
def _rebuild_strips(self, text: Text) -> None:
    width = self.scrollable_content_region.width or self.size.width or 1
    if width != self._render_width:
        self._render_width = width
    segments = self.app.console.render(text, self.app.console.options.update_width(width))
    lines = list(Segment.split_lines(segments))
    self._content_strips = [Strip(line).adjust_cell_length(width) for line in lines]
    self.virtual_size = Size(width, max(1, len(self._content_strips)))
```

**`on_resize(self, event: events.Resize) -> None`:** Re-render on width change.

```python
def on_resize(self, event: events.Resize) -> None:
    new_width = self.scrollable_content_region.width or self.size.width
    if new_width != self._render_width and self._last_text is not None:
        self._rebuild_strips(self._last_text)
        self.refresh()
```

#### 2.4 Update `update_detail` to use strip-based rendering

Replace `self.update(text)` + `self.scroll_home(animate=False)` with:

```python
self._last_text = text
self._rebuild_strips(text)
self.scroll_to(y=0, animate=False, immediate=True, force=True)
self.refresh()
```

Track `_last_text` and `_render_width` and `_content_strips` as instance
attributes initialized in `__init__`.

#### 2.5 Handle no-component case

The early-return path in `update_detail` (when `comp is None`) currently calls
`self.update(text)` + `self.scroll_home(animate=False)`. This must also be
converted to `_rebuild_strips(text)` + `scroll_to(y=0, ...)`.

#### 2.6 CSS

The existing CSS in `detail_panel.py` `DEFAULT_CSS` already sets
`height: 1fr; overflow-y: auto;`. This works with `ScrollView` — no CSS
change needed. The `ComponentDetailPanel:focus` border style is retained.

The screen-level CSS in `screen.py` also has `#right-panel ComponentDetailPanel`
rules (`height: 1fr; overflow-y: auto;`). These remain compatible.

#### 2.7 Import changes in `detail_panel.py`

Add:
```python
from textual import events
from textual.geometry import Size
from textual.scroll_view import ScrollView
from textual.strip import Strip
from rich.segment import Segment
```

Remove:
```python
from textual.widgets import Static
```

#### 2.8 `get_editable_field_line_offset` — no change needed

This method returns line indices into the rendered text content. Since
`render_line` uses the same line ordering as the old `Static.update()` text,
the offsets remain correct. The screen's `_show_value_edit_input` positions the
Input overlay using `detail_panel.region` + `get_editable_field_line_offset`
- `scroll_y`, which works correctly with ScrollView's `scroll_y`.

---

## 3. Editable start_time / end_time

### Problem

`start_time` and `end_time` are displayed in the Detail Panel's base metadata
section as read-only fields. The user may need to manually adjust these values
(e.g., the analysis job's boundary detection was off by a few seconds).

### Design

Add `start_time` and `end_time` to the `EDITABLE_FIELDS` tuple so they appear
in the Editable sub-section of the Detail Panel and are navigable via Up/Down
arrows. Editing uses the existing `e` key + numeric Input overlay, same as
`groove_density` and `energy_level`.

The DB whitelist (`ALLOWED_COMPONENT_FIELDS`) must be expanded to include
`start_time` and `end_time`.

### Implementation

#### 3.1 Constants: expand `EDITABLE_FIELDS`

**File:** `ops/admin-cli/src/stream_of_worship/admin/component_editor/constants.py`

Change `EDITABLE_FIELDS` from:
```python
EDITABLE_FIELDS: tuple[str, ...] = (
    "theme",
    "vocal_posture",
    "groove_density",
    "energy_level",
)
```
to:
```python
EDITABLE_FIELDS: tuple[str, ...] = (
    "theme",
    "vocal_posture",
    "groove_density",
    "energy_level",
    "start_time",
    "end_time",
)
```

#### 3.2 DB whitelist: expand `ALLOWED_COMPONENT_FIELDS`

**File:** `ops/admin-cli/src/stream_of_worship/admin/db/client.py`

Change:
```python
ALLOWED_COMPONENT_FIELDS: frozenset[str] = frozenset(
    {"theme", "vocal_posture", "groove_density", "energy_level"}
)
```
to:
```python
ALLOWED_COMPONENT_FIELDS: frozenset[str] = frozenset(
    {"theme", "vocal_posture", "groove_density", "energy_level",
     "start_time", "end_time"}
)
```

#### 3.3 Detail Panel: move start_time / end_time from base metadata to Editable section

**File:** `ops/admin-cli/src/stream_of_worship/admin/component_editor/detail_panel.py`

Remove `Start` and `End` from the `detail_fields` list (lines 107-111). Base
metadata becomes: Type, Occurrence, BPM, Key, Confidence, Backbeat (6 fields).

The Editable sub-section loop (`detail_panel.py:127-148`) already iterates
over `EDITABLE_FIELDS` — `start_time`/`end_time` will render automatically.
Add a formatting branch for time fields so they display as `MM:SS` while
accepting raw seconds as input:

```python
for i, field_name in enumerate(EDITABLE_FIELDS):
    value = state.get_value(role, field_name)
    is_focused = i == self._focus_idx
    marker = "►" if is_focused else " "

    if field_name in ("theme", "vocal_posture"):
        hint = " [◄ ►]"
        value_str = str(value) if value else "—"
    elif field_name in ("start_time", "end_time"):
        hint = " [e]"
        if isinstance(value, (int, float)):
            value_str = format_duration(float(value))
        elif value:
            value_str = str(value)
        else:
            value_str = "—"
    else:
        hint = " [e]"
        if isinstance(value, (int, float)):
            value_str = f"{value:.4g}"
        elif value:
            value_str = str(value)
        else:
            value_str = "—"

    text.append(f" {marker} {field_name:15s}: ", style="dim")
    if is_focused:
        text.append(f"{value_str}{hint}\n", style="bold reverse")
    else:
        text.append(f"{value_str}{hint}\n")
```

#### 3.4 Detail Panel: update `get_editable_field_line_offset`

**File:** `ops/admin-cli/src/stream_of_worship/admin/component_editor/detail_panel.py`

The line offset computation must be updated. The current layout:
- 1 (Song header) + 6 (song fields) = 7
- 1 (blank) = 8
- 1 (Component header) = 9
- 6 (base-metadata fields: Type, Occ, BPM, Key, Conf, Backbeat) = 15
  *(was 8 fields including Start/End; now 6 without them)*
- 1 (blank) = 16
- 1 (Editable sub-header) = 17
- + index of field in EDITABLE_FIELDS = target line

```python
def get_editable_field_line_offset(self, field: str) -> int:
    """Return the 0-based line index of the given editable field's value
    within the rendered text.

    Updated layout (with start_time/end_time added to EDITABLE_FIELDS and
    removed from base metadata):
    - 1 (Song header) + 6 (song fields) = 7
    - 1 (blank) = 8
    - 1 (Component header) + 6 (base-metadata fields) = 15
    - 1 (blank) = 16
    - 1 (Editable sub-header) = 17
    - + index of field in EDITABLE_FIELDS = target line
    """
    try:
        field_idx = EDITABLE_FIELDS.index(field)
    except ValueError:
        return 0
    return 17 + field_idx
```

#### 3.5 Screen: extend `action_edit_numeric` to accept `start_time` / `end_time`

**File:** `ops/admin-cli/src/stream_of_worship/admin/component_editor/screen.py`

In `action_edit_numeric` (`screen.py:1283`), the field check currently only
allows `groove_density` and `energy_level`:

```python
field = panel.focused_field
if field not in ("groove_density", "energy_level"):
    return
```

Change to:
```python
field = panel.focused_field
if field not in ("groove_density", "energy_level", "start_time", "end_time"):
    return
```

Also update the initial value formatting — `start_time` / `end_time` are
floats; the existing `f"{current:.4g}"` format works but is hard to read.
For timing fields, use raw seconds with 2 decimal places:

```python
if field in ("start_time", "end_time"):
    initial = "" if current is None else f"{current:.2f}"
else:
    initial = "" if current is None else f"{current:.4g}"
```

#### 3.6 Screen: extend `_validate_numeric_field` for timing validation

**File:** `ops/admin-cli/src/stream_of_worship/admin/component_editor/screen.py`

The current validator (`screen.py:880`) validates `groove_density` and
`energy_level` against their min/max ranges. Add validation for `start_time`
and `end_time`:

```python
def _validate_numeric_field(self, field: str, text: str) -> float | None:
    try:
        val = float(text.strip())
    except ValueError:
        return None

    if (
        field == "groove_density" and not (GROOVE_DENSITY_MIN <= val <= GROOVE_DENSITY_MAX)
    ) or (field == "energy_level" and not (ENERGY_LEVEL_MIN <= val <= ENERGY_LEVEL_MAX)):
        return None

    if field in ("start_time", "end_time"):
        session = self.state.current
        audio_duration = session.audio_duration
        if audio_duration is not None:
            if val < 0 or val > audio_duration:
                return None
        elif val < 0:
            return None
        # Cross-field validation: start_time < end_time
        role = self._selected_editor_role()
        if field == "start_time":
            end = self.state.get_value(role, "end_time")
            if end is not None and val >= end:
                return None
        elif field == "end_time":
            start = self.state.get_value(role, "start_time")
            if start is not None and val <= start:
                return None

    return val
```

Validation rules applied:
1. `start_time >= 0` — non-negative
2. `start_time < end_time` — start must be before end (cross-field check)
3. `end_time <= audio_duration` — must not exceed song duration (if known)
4. `0 <= value <= audio_duration` — both fields clamped to `[0, audio_duration]`
   (or `[0, ∞)` if `audio_duration` is `None`)

#### 3.7 Constants: no new constants needed

Validation uses `session.audio_duration` at runtime, which is the actual song
duration. The `0` lower bound is inline in the validator.

#### 3.8 Playback integration

The component-scoped playback (Section 1) uses `state.get_value(role,
"end_time")` which returns the working (edited) value if one exists, or falls
back to the persisted value. This means editing `end_time` in the Detail Panel
immediately changes the playback stop point — no additional wiring needed.

Similarly, `action_toggle_playback_for_component` currently uses
`comp.start_time` — this should be updated to use
`state.get_value(role, "start_time")` to respect edited values:

```python
def action_toggle_playback_for_component(self) -> None:
    if self._guard_active_edit():
        return
    if self.playback.is_playing:
        self.playback.pause()
        return
    comp = self.state.get_selected_component()
    role = self._selected_editor_role()
    start = self.state.get_value(role, "start_time")
    if start is None and comp is not None:
        start = comp.start_time
    if start is None:
        start = 0.0
    self.playback.play(start_seconds=start)
    self._update_lyrics_highlight()
```

And `action_jump_to_component` should similarly use the working value:

```python
def action_jump_to_component(self) -> None:
    if self._guard_active_edit():
        return
    role = self._selected_editor_role()
    start = self.state.get_value(role, "start_time")
    if start is None:
        self.app.bell()
        return
    self.playback.seek(start)
    self._update_playback_bar()
    self._update_lyrics_highlight()
```

---

## 4. Test Updates

### 4.1 `test_detail_panel.py`

- Update `get_editable_field_line_offset` test expectations:
  - `theme` → 17 (was 19)
  - `vocal_posture` → 18 (was 20)
  - `groove_density` → 19 (was 21)
  - `energy_level` → 20 (was 22)
  - Add: `start_time` → 21
  - Add: `end_time` → 22
- Add test: `start_time` / `end_time` appear in the Editable sub-section
- Add test: base metadata section no longer contains "Start:" and "End:" lines
- Add test: Detail Panel renders as ScrollView (strip-based) and content is
  scrollable when it exceeds viewport height

### 4.2 `test_screen.py`

- Add test: `_check_component_playback_end` pauses playback when position
  reaches `end_time`
- Add test: `_check_component_playback_end` does not pause when `end_time`
  is `None`
- Add test: `_check_component_playback_end` uses working value of `end_time`
  (edited value, not persisted)
- Add test: `action_edit_numeric` opens overlay for `start_time` and `end_time`
- Add test: `_validate_numeric_field` rejects `start_time >= end_time`
- Add test: `_validate_numeric_field` rejects `end_time > audio_duration`
- Add test: `action_toggle_playback_for_component` uses working `start_time`

### 4.3 `test_state.py`

- Add test: `set_value` + `get_value` works for `start_time` / `end_time`
  (undo/redo stack round-trip)

### 4.4 New test file or additions: `test_detail_panel_scroll.py`

- Add test: Detail Panel content_strips are populated after `update_detail`
- Add test: `virtual_size.height` > 0 when content exceeds viewport
- Add test: `render_line` returns correct strip for given y coordinate
- Add test: `on_resize` rebuilds strips when width changes

---

## 5. Files Changed

| File | Change |
|---|---|
| `component_editor/screen.py` | Add `_check_component_playback_end()`; wire into position loop + callback; extend `action_edit_numeric` + `_validate_numeric_field` for `start_time`/`end_time`; update `action_toggle_playback_for_component` + `action_jump_to_component` to use working start_time |
| `component_editor/detail_panel.py` | Convert from `Static` to `ScrollView` (Line-API pattern); add `render_line`, `_rebuild_strips`, `on_resize`; update `update_detail` to use strip-based rendering; remove start_time/end_time from base metadata; add them to Editable section via `EDITABLE_FIELDS` iteration; update `get_editable_field_line_offset` |
| `component_editor/constants.py` | Add `start_time`, `end_time` to `EDITABLE_FIELDS` |
| `db/client.py` | Add `start_time`, `end_time` to `ALLOWED_COMPONENT_FIELDS` |
| `tests/admin/component_editor/test_detail_panel.py` | Update offset expectations; add start_time/end_time rendering tests; add scroll tests |
| `tests/admin/component_editor/test_screen.py` | Add playback-stop tests; add timing-edit tests |
| `tests/admin/component_editor/test_state.py` | Add start_time/end_time set/get tests |

---

## 6. Verification

```bash
uv run --project ops/admin-cli --python 3.11 --extra admin --extra test pytest \
    tests/admin/component_editor/ -v
```

All existing tests must pass (with updated offset expectations). New tests
for playback stop, scroll, and timing edit must pass.
