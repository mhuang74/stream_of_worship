# Component Editor Vertical Layout v2 — Fixes

## Summary

Four issues found during review of the v2 implementation (after v1 fixes from `component-editor-vertical-layout-v1-fixes.md` were applied):

| # | Issue | Root Cause |
|---|-------|------------|
| A | Cannot focus Detail panel to edit fields | `ComponentDetailPanel` extends `Static`, whose `can_focus = False` (inherited from `Widget`); calls to `details.focus()` in `_apply_right_panel_mode()` and `_focus_active_panel()` are no-ops. When the user presses `v` to enter details mode, `_active_panel` is set to `"right"` in code, but actual keyboard focus still sits on the `DataTable`. Up/down arrow keys then navigate the table instead of cycling editable fields, leaving the `►` focus marker stuck on `theme` (index 0). |
| B | SPACE doesn't jump to component `start_time` when playback is paused inside the component range | `action_toggle_playback_for_component` (`screen.py:1185-1194`) only seeks to `comp.start_time` when the current position is **outside** `[start, end]`. If the user has manually seeked into the component range (or finished another playback), pressing SPACE resumes from wherever playback was last positioned — not from the component's start, which is what the user expects. |
| C | Detail Panel section ordering: Confidence should sit just above Lifecycle | Current order in `detail_panel.py:59-177`: Song Info → Component Details → Confidence Breakdown → Editable Fields → Reasoning → Lifecycle. The Confidence section is buried in the middle of the panel, pushing the editable fields and reasoning too far down. The user wants Energy / Theme fields and their reasoning grouped together as part of a single "Component" section, with Confidence moved lower (just above Lifecycle). |
| D | Lifecycle timestamps display raw ISO 8601 with microseconds + tz offset | `detail_panel.py:171-174` does `text.append(f"{comp.created_at or '—'}\n")` — `created_at`/`updated_at` are stored as ISO strings (via `to_str()` in `db/helpers.py:10-21`, which converts `datetime.isoformat()`). Output looks like `2026-08-15T14:23:45.123456+00:00`. User wants them formatted to the nearest second (microseconds stripped). |

## Clarifying Questions

1. **Issue A — confirming root cause:** Inline review confirms `Static` in Textual 8.2.7 (project's pinned version, see `.venv/lib/python*/site-packages/textual/widgets/_static.py`) inherits `can_focus = False` from `Widget` and has no `BINDINGS` and no `on_key` handler. So the proposed fix (set `can_focus = True` on `ComponentDetailPanel` and `LyricsPanel`) should be sufficient — no need to also add priority bindings for `up`/`down` since `Static` does not bind those keys.
   - **Assumption:** Yes — same fix should be applied symmetrically to `LyricsPanel` so both right-panel views accept focus consistently (their `:focus { border-left: double $accent; }` CSS rules anticipate this).

2. **Issue B — always seek, or only seek on transition from stopped to playing?**
   The user said "jump to start_time of highlighted component". The simplest behavior matching that intent is: when not currently playing, always seek to `comp.start_time` regardless of whether playback is currently inside `[start, end]`.
   - **Assumption:** Always seek to `comp.start_time` before `play()` when playback is paused or stopped. This makes SPACE behave identically to `j` (jump) followed by play, while still pausing when currently playing. If a user wants to resume from somewhere other than the highlighted component start, they should use the seek keys (left/right, ±5s).

3. **Issue C — what exactly to merge into the "Component" section?**
   The user said: *"Component (merge Energy, Theme and Reasoning fields into this section)"*. The current panel has a "Component Details" base-metadata block, a separate "Editable Fields" block (containing `theme`, `vocal_posture`, `groove_density`, `energy_level`), and a separate "Reasoning" block (`theme_reasoning`, `posture_reasoning`).
   - **Assumption:** The "Component" section = the existing base metadata (Type/Occurrence/Start/End/BPM/Key/Backbeat) **followed by** the editable fields (theme, vocal_posture, groove_density, energy_level with their `►` focus markers and hints) **followed by** the reasoning text (theme_reasoning, posture_reasoning). The `confidence` scalar (overall confidence) currently in the "Component Details" block stays where it is. The "Confidence Breakdown" section (per-field confidences) moves down to just above Lifecycle. The Editable Fields and Reasoning sections lose their separate headers and become unlabeled sub-groups within "Component".

4. **Issue D — exact format for timestamps?**
   The user said "formatted to nearest seconds". Options:
   - `2026-08-15 14:23:45 UTC` (human-friendly, space-separated, UTC label)
   - `2026-08-15 14:23:45` (drop timezone display)
   - `2026-08-15T14:23:45+00:00` (ISO, truncated to seconds)
   - **Assumption:** `2026-08-15 14:23:45 UTC` — consistent with `recover_visibility.py:511` (`db_updated_at.strftime("%Y-%m-%d %H:%M:%S UTC")`). If the string fails to parse as ISO (legacy or non-UTC formats), fall back to displaying the raw string verbatim.

## Issue A: Cannot Focus Detail Panel to Edit Fields

### Investigation

`ComponentDetailPanel` (`detail_panel.py:15-178`) extends `Static`. Verified against installed Textual 8.2.7 source (`.venv/lib/python3.11/site-packages/textual/widgets/_static.py`, 95 lines):

- `Static(Widget, inherit_bindings=False)` — line 12. **`inherit_bindings=False`** means even base-class bindings do not apply.
- `Static` does **not** override `can_focus`, `BINDINGS`, or `on_key` anywhere.
- `Widget.can_focus = False` (`.venv/lib/python3.11/site-packages/textual/widget.py:337-340`). Inherited by `Static`.

So calling `details.focus()` from `_apply_right_panel_mode` (`screen.py:1061-1063`) and `_focus_active_panel` (`screen.py:1099`) silently no-ops. Focus remains on whatever widget had it last (the `DataTable` in the left panel).

#### Symptom Flow

1. Screen boots. `_active_panel = "left"`, `_right_panel_mode = "lyrics"`. `DataTable` is focused (`screen.py:528`).
2. User presses `v` once → mode cycles to `"details"`.
3. `_apply_right_panel_mode()` (`screen.py:1057-1064`) sets `_active_panel = "right"` in Python state, but `details.focus()` is a no-op because `Static.can_focus = False`. **Focus stays on `DataTable`.**
4. User presses `▲`/`▼` to navigate editable fields. Because `DataTable` still has focus, two things contend for the key:
   - `ComponentMetadataTable.action_cursor_up/action_cursor_down` (`screen.py:317-327`) — these fire because the `DataTable` is focused and its priority bindings consume the keys.
   - Screen-level non-priority `detail_focus_up`/`detail_focus_down` bindings (`screen.py:437-438`) never fire because priority bindings consume the event first.
5. Result: cursor moves in the table (changes `selected_row`, which changes _which_ component is highlighted). The detail panel's `_focus_idx` stays at `0` (theme). The user perceives this as "cannot rotate focus to Detail panel".

#### Fix

Make `ComponentDetailPanel` (and `LyricsPanel` for symmetry) focusable by setting `can_focus = True`:

```python
# detail_panel.py
class ComponentDetailPanel(Static):
    can_focus = True  # NEW — required so .focus() actually moves keyboard focus
    DEFAULT_CSS = """
    ComponentDetailPanel {
        height: 1fr;
        padding: 0 1;
        overflow-y: auto;
        background: $surface;
    }
    ComponentDetailPanel:focus {
        border-left: double $accent;  # now actually activates
    }
    """
    ...
```

```python
# lyrics_panel.py
class LyricsPanel(Static):
    can_focus = True  # NEW — symmetric with ComponentDetailPanel for v-cycle focus
    ...
```

#### Why this is sufficient

Once `can_focus = True`:
- `details.focus()` (called from `_apply_right_panel_mode` and `_focus_active_panel`) actually moves keyboard focus to the panel.
- When `ComponentDetailPanel` is focused, the binding resolution for `▲`/`▼`:
  1. Priority bindings — widget-level: `Static` has none (and `inherit_bindings=False`). Screen-level: only `tab`/`shift+tab` are priority. App-level: none. **No match.**
  2. `on_key` — widget-level: `Static` has none. Screen-level: Textual's `Screen.on_key` handles `tab`/`shift+tab` (calls `focus_next`/`focus_previous`) but not arrow keys. **Does not consume arrow keys.**
  3. Non-priority bindings — widget-level: none. **Screen-level: `up → detail_focus_up` and `down → detail_focus_down` match and fire.**

The screen's `action_detail_focus_up`/`action_detail_focus_down` (`screen.py:1102-1118`) already guard with `_active_panel != "right" or _right_panel_mode != "details"` and call `panel.move_focus_up()`/`move_focus_down()` then `_refresh_detail_panel()`. After the focus fix, these will actually run when the user presses `▲`/`▼`. No other changes needed.

#### Edge Case: Numeric Edit Overlay Repositioning

`_show_value_edit_input` (`screen.py:847-878`) uses `detail_panel.get_editable_field_line_offset(field)` (`detail_panel.py:191-209`) to position the `Input` overlay over the focused field. The current layout arithmetic is:

```
1 (Song header) + 6 (song fields) = 7
1 (blank) = 8
1 (Component header) + 8 (component fields) = 17
1 (blank) = 18
1 (Confidence header) + 7 (conf fields) = 26
1 (blank) = 27
1 (Editable header) = 28
+ field_idx = target line
```

**This math is coupled to the existing section layout.** Issue C below will restructure the section ordering; `get_editable_field_line_offset` MUST be updated in lock-step with that refactor, or the `Input` overlay will appear at the wrong y coordinate.

## Issue B: SPACE Doesn't Jump to Component Start

### Current Behavior

```python
# screen.py:1170-1194
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
            self._update_lyrics_highlight()
    self.playback.play()
```

The seek only fires when `pos` is **outside** `[start, end]`. If the user has previously played this component (and stopped inside its range), or has seeked manually into the range, SPACE resumes from `pos` — not from `comp.start_time`.

### Desired Behavior

When starting playback (i.e., when not currently playing), always:
1. Seek to `comp.start_time` of the highlighted component (if a component is selected and has a `start_time`).
2. Refresh lyrics highlight.
3. Call `play()`.

When currently playing: pause (existing behavior, no change).

### Fix

```python
# screen.py — action_toggle_playback_for_component
def action_toggle_playback_for_component(self) -> None:
    """Play or pause the song, anchored to the highlighted component.

    - If playing: pause.
    - If paused/stopped: always seek to the highlighted component's
      start_time (so SPACE reliably restarts the component), then play.
    - If no component is highlighted: best-effort play() without seeking.
    """
    if self._guard_active_edit():
        return

    if self.playback.is_playing:
        self.playback.pause()
        return

    comp = self.state.get_selected_component()
    if comp is not None and comp.start_time is not None:
        self.playback.seek(comp.start_time)
        self._update_lyrics_highlight()
    self.playback.play()
```

This removes the `inside = start <= pos <= end` check entirely. SPACE now always seeks to the highlighted component's start before playing.

### Behavior Comparison

| Scenario | Before | After |
|----------|--------|-------|
| Playing | Pause | Pause (no change) |
| Paused inside `[start, end]` | Play from current pos | Seek to `start` then play |
| Paused outside `[start, end]` | Seek + play | Seek + play (same) |
| No component highlighted | Play from current pos | Play from current pos (same) |

### Tests

```python
async def test_space_always_seeks_to_component_start_when_paused_inside():
    """SPACE seeks to start_time even when playback is paused inside the
    component range (e.g. after a previous playthrough)."""
    app, playback = _make_app()
    playback.is_playing = False
    playback.position_seconds = 30.0  # inside [10, 45]
    async with app.run_test(size=(160, 30)) as pilot:
        await pilot.pause()
        await pilot.press("space")
        await pilot.pause()
        assert playback._seeked_to == 10.0  # start_time of entry
        assert playback._played

async def space_paused_outside_range_still_seek_and_play():
    # Existing behavior — keep working.
    ...

async def space_playing_pauses_without_seek():
    playback.is_playing = True
    async with app.run_test(size=(160, 30)) as pilot:
        await pilot.pause()
        await pilot.press("space")
        await pilot.pause()
        assert playback._paused
        assert not playback._seeked
```

## Issue C: Detail Panel Section Reordering

### Current Layout (detail_panel.py:59-177)

In order:
1. **Song Info** header + 6 fields (Title, Artist, Lyricist, Album, Series, Song Key)
2. *(early return if comp is None)*
3. **Component Details** header + 8 fields (Type, Occurrence, Start, End, BPM, Key, Confidence, Backbeat)
4. **Confidence Breakdown** header + 7 fields (BPM, Key, Groove, Backbeat, Energy, Theme, Posture per-field confidences)
5. **Editable Fields** header + 4 fields with `►` marker (theme, vocal_posture, groove_density, energy_level)
6. **Reasoning** header + 2 fields (Theme, Posture reasoning)
7. **Lifecycle** header + 2 fields (Created, Updated)

### Desired Layout

1. **Song Info** — no change
2. **Component** — merge the existing Component Details + Editable Fields + Reasoning sub-sections. Sub-layout proposal:
   - 2a. Base metadata: Type, Occurrence, Start, End, BPM, Key, **Confidence** (overall), Backbeat
   - 2b. *"Editable"* sub-header (or just a blank line + label): theme, vocal_posture, groove_density, energy_level (with `►` focus markers and `[◄ ►]` / `[e]` hints — same edit affordances)
   - 2c. Reasoning: theme_reasoning, posture_reasoning (long-form text, same as today)
3. **Confidence Breakdown** — moved lower, sits just above Lifecycle; content unchanged (7 per-field confidence values)
4. **Lifecycle** — Created, Updated timestamps formatted to nearest seconds (see Issue D)

### Why this ordering?

The user's mental model is: "show me the component, what its editable attributes are, and why" all in one place — because theme/energy fields and their LLM reasoning are tightly coupled. Confidence breakdown is a deeper audit view; it moves lower to be near the lifecycle/audit data.

### Implementation

Rewrite `update_detail()` in `detail_panel.py` to render in the new order. Treat Editable Fields and Reasoning as labeled sub-sections inside the "Component" section (use a slightly different style to visually separate them — e.g., a dim `"  -- Editable --" / "  -- Reasoning --"` line, or just blank lines with consistent indentation).

#### Proposed Code Structure

```python
def update_detail(self, state: ComponentEditorState) -> None:
    """Render full component detail for the current song + selected role."""
    session = state.current
    roles = session.ordered_component_roles
    if roles:
        idx = max(0, min(state.selected_row, len(roles) - 1))
        role = roles[idx]
    else:
        role = "entry"
    comp = session.component_for_role(role)
    song = session.song

    text = Text()

    # -- Section: Song Info --
    text.append("-- Song Info --\n", style="bold cyan")
    # ... 6 song fields as before ...

    if comp is None:
        # ... early return as before ...
        return

    text.append("\n")

    # -- Section: Component (merged) --
    display_label = ROLE_LABELS.get(role, role.upper())
    text.append(f"-- Component ({display_label}) --\n", style="bold cyan")

    # 2a. Base metadata
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

    # 2b. Editable fields (sub-section within Component)
    text.append("\n")
    text.append("  -- Editable --\n", style="dim italic")
    for i, field_name in enumerate(EDITABLE_FIELDS):
        # ... same editable-field rendering as today ...
        ...

    # 2c. Reasoning (sub-section within Component)
    text.append("\n")
    text.append("  -- Reasoning --\n", style="dim italic")
    reasoning_fields = [
        ("Theme", comp.theme_reasoning),
        ("Posture", comp.posture_reasoning),
    ]
    for label, value in reasoning_fields:
        # ... same rendering as today ...

    text.append("\n")

    # -- Section: Confidence Breakdown (moved lower) --
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
        # ... same rendering as today ...
        ...

    text.append("\n")

    # -- Section: Lifecycle (timestamps formatted to nearest seconds) --
    text.append("-- Lifecycle --\n", style="bold cyan")
    text.append(f"  {'Created':12s}: ", style="dim")
    text.append(f"{_format_timestamp(comp.created_at)}\n")
    text.append(f"  {'Updated':12s}: ", style="dim")
    text.append(f"{_format_timestamp(comp.updated_at)}\n")

    self.update(text)
    self.scroll_home(animate=False)
```

### Update `get_editable_field_line_offset`

The Input-overlay positioning math (`detail_panel.py:191-209`) is tightly coupled to the line layout. New layout:

```
1 (Song header) + 6 (song fields) = 7
1 (blank) = 8
1 (Component header) + 8 (base-metadata fields) = 17
1 (blank) = 18
1 (Editable sub-header) = 19
+ field_idx = target line  (theme=19, vocal_posture=20, groove_density=21, energy_level=22)
```

```python
def get_editable_field_line_offset(self, field: str) -> int:
    """Return the 0-based line index of the given editable field's value
    within the rendered text. Used by the screen to position the Input overlay.

    New layout (v2):
    - 1 (Song header) + 6 (song fields) = 7
    - 1 (blank) = 8
    - 1 (Component header) + 8 (base-metadata fields) = 17
    - 1 (blank) = 18
    - 1 (Editable sub-header) = 19
    - + index of field in EDITABLE_FIELDS = target line
    """
    try:
        field_idx = EDITABLE_FIELDS.index(field)
    except ValueError:
        return 0
    return 19 + field_idx
```

**Verify** this arithmetic after implementation by adding a debug print of both the rendered text and the `_focus_idx` offset.

## Issue D: Lifecycle Timestamp Formatting

### Current

`detail_panel.py:171-174`:
```python
text.append(f"  {'Created':12s}: ", style="dim")
text.append(f"{comp.created_at or '—'}\n")
text.append(f"  {'Updated':12s}: ", style="dim")
text.append(f"{comp.updated_at or '—'}\n")
```

`comp.created_at` and `comp.updated_at` are `Optional[str]` (`db/models.py:617-618`). The string value is whatever `to_str()` in `db/helpers.py:10-21` returns:

```python
def to_str(val) -> Optional[str]:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.isoformat()  # e.g. "2026-08-15T14:23:45.123456+00:00"
    return str(val)
```

So output reads e.g. `2026-08-15T14:23:45.123456+00:00` — six digits of microseconds plus tz offset, which is verbose.

### Desired

Format as `2026-08-15 14:23:45 UTC` — space-separated, no microseconds, explicit `UTC` label. Consistent with `ops/admin-cli/src/stream_of_worship/admin/commands/recover_visibility.py:511`.

### Implementation

Add a helper to `detail_panel.py` (or import from a shared utilities location — there is no existing shared formatter for component timestamps in this code path):

```python
from datetime import datetime, timezone

def _format_timestamp(value: str | None) -> str:
    """Format an ISO-8601 timestamp to nearest second with UTC label."""
    if value is None:
        return "—"
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    except (ValueError, TypeError):
        # Unparseable legacy format — show raw string as-is.
        return value
```

Then in `update_detail()` (`detail_panel.py:169-174`), replace the `f"{comp.created_at or '—'}\n"` strings with `_format_timestamp(comp.created_at)` / `_format_timestamp(comp.updated_at)` calls.

### Tests

```python
def test_format_timestamp_strips_microseconds():
    assert _format_timestamp("2026-08-15T14:23:45.123456+00:00") == "2026-08-15 14:23:45 UTC"

def test_format_timestamp_handles_none():
    assert _format_timestamp(None) == "—"

def test_format_timestamp_handles_naive_datetime():
    # Legacy strings without timezone get UTC label.
    assert _format_timestamp("2026-08-15T14:23:45") == "2026-08-15 14:23:45 UTC"

def test_format_timestamp_passes_through_garbage():
    assert _format_timestamp("not a date") == "not a date"
```

## Affected Files

| File | Issues | Changes |
|------|--------|---------|
| `detail_panel.py` | A, C, D | Set `can_focus = True`; rewrite `update_detail()` for new section ordering; add `_format_timestamp()` helper; update `get_editable_field_line_offset()` math (lines 191-209, new offset is `19 + field_idx` instead of `28 + field_idx`) |
| `lyrics_panel.py` | A | Set `can_focus = True` (symmetric with detail panel) |
| `screen.py` | B | Simplify `action_toggle_playback_for_component` to always seek to `comp.start_time` when starting playback (lines 1170-1194) |
| `test_detail_panel.py` (or wherever detail-panel unit tests live) | A, C, D | New tests for `_format_timestamp`; updated layout/section-order tests; new test confirming that focus marker cycles through fields after `v` to details mode |

**No changes needed to:**
- `screen.py` BINDINGS — `tab`/`shift+tab` already have `priority=True` (v1 fix), and `up`/`down` for `detail_focus_up`/`detail_focus_down` do not need `priority=True` once `ComponentDetailPanel.can_focus = True`
- `state.py` — `SongSession` already has the `components` dict from v1 Issue C
- `constants.py` — `EDITABLE_FIELDS`, `ROLE_LABELS`, `ESSENTIAL_COMPONENT_SLOTS`, `identify_editor_role()` all unchanged
- `autosave.py` — schema unaffected
- DB / R2 schema or any persistence layer — no changes

## Implementation Order

1. **Issue A first** (smallest, unblocks the rest) — set `can_focus = True` on `ComponentDetailPanel` and `LyricsPanel`. Verify focus works by pressing `v` then `▲`/`▼` — the `►` marker should move between editable fields and the table cursor should NOT move.
2. **Issue B second** — small, localized change to `action_toggle_playback_for_component`. Verify with a single playback test.
3. **Issue D third** — add `_format_timestamp()` + 4 unit tests. Independent of Issue C.
4. **Issue C last** — largest refactor of `update_detail()`. Must include updating `get_editable_field_line_offset()` in lockstep. After this change, manually verify that the `Input` overlay for numeric edits appears over the correct row of the new layout.

Issues A and B should be unblocked first; they're trivial and give immediate UX improvement. Issue D is independent and can be done in parallel with C if desired. Issue C is the visually-largest change and benefits from careful manual review.

## Verification

```bash
# Unit tests
uv run --project ops/admin-cli --python 3.11 --extra admin --extra test pytest \
    tests/admin/component_editor/ -v

# Lint + format
uv run --project ops/admin-cli ruff check src/stream_of_worship/admin/component_editor/
uv run --project ops/admin-cli ruff format --check src/stream_of_worship/admin/component_editor/

# Manual smoke test
uv run --project ops/admin-cli --extra admin sow-admin audio components review <song_id>

# In the editor, verify:
#   1. Press 'v' to switch to details mode — detail panel should be visibly focused (accent border on left)
#   2. Press ▲/▼ — the ► focus marker should cycle between theme / vocal_posture / groove_density / energy_level
#                  and the table cursor on the left should NOT move
#   3. Press SPACE while paused inside the highlighted component range — playback should seek to component start_time
#   4. Inspect the Detail panel layout: should read in order Song Info → Component (with Editable + Reasoning
#      sub-headers) → Confidence Breakdown → Lifecycle
#   5. Lifecycle timestamps should be like "2026-08-15 14:23:45 UTC" — no microseconds
```

## Backward Compatibility

- **Autosave**: unchanged — `can_focus` and detail-panel rendering changes don't touch `ComponentAutosaveState` or `working` dict schema.
- **DB save**: unchanged — `update_song_component_fields_txn` is component-id-based, not layout-based.
- **R2 merge**: unaffected — `components.json` serialization is independent of detail-panel layout.
- **Keyboard bindings**: no new or modified bindings — only widget `can_focus` and an action's internal seek logic change.
- **User-visible format change for Lifecycle timestamps**: This changes the rendered detail-panel output only. The underlying `SongComponent.created_at` / `updated_at` strings remain ISO 8601 with microseconds — formatting is applied at render time in the detail panel only. No downstream code that reads `comp.created_at` is affected.
