# Component Editor Vertical Layout v1 — Fixes

## Summary

Three issues found during review of the v1 vertical layout implementation:

| # | Issue | Root Cause |
|---|-------|------------|
| A | Unable to pull up Detail panel | `_apply_right_panel_mode()` doesn't auto-focus right panel when switching to lyrics/details mode; combined with Tab not working (Issue B), the detail panel displays but is non-interactive |
| B | TAB does not cycle through left/right panels | `tab`/`shift+tab` bindings lack `priority=True`; Textual's built-in `Screen.on_key` focus management intercepts these keys before non-priority bindings fire |
| C | Data Table should include all Essential Components | `SongSession` only holds `entry_component`/`exit_component`; table iterates over `("entry", "exit")` only — missing verse 1 and bridge |

## Clarifying Questions

1. **Essential Components scope**: Should the table show exactly 4 essential component types (entry chorus, exit chorus, verse 1, bridge occurrence_index=1), or all components returned by `get_song_components()`?
   - **Assumption**: 4 essential types only, using the `is_essential_bridge`-style logic from `services/analysis.py:166`

2. **Editing scope**: Should verse 1 and bridge components be fully editable (theme, vocal_posture, groove_density, energy_level) with the same save/undo/autosave flow?
   - **Assumption**: Yes, all 4 component types are editable

3. **Detail panel behavior for non-entry/exit components**: Should the detail panel show full metadata for verse 1 / bridge when their row is selected?
   - **Assumption**: Yes

## Issue A: Unable to Pull Up Detail Panel

### Root Cause

`screen.py:1014-1045` (`_apply_right_panel_mode`):

- When `v` switches to "details" mode, the method sets `details.display = True` but does **not** set `self._active_panel = "right"` or focus the detail panel widget.
- All edit actions guard with `self._active_panel != "right" or self._right_panel_mode != "details"` (`screen.py:1205`, `screen.py:1233`, `screen.py:1261`).
- Since `_active_panel` stays "left" after pressing `v`, no edit actions work.
- Tab (Issue B) is the only way to set `_active_panel = "right"`, but it doesn't work either.
- Result: detail panel is visible but dead — user perceives this as "unable to pull up Detail panel".

### Fix

**Location:** `screen.py:1034-1045` (`_apply_right_panel_mode`)

When `v` switches to "lyrics" or "details" mode, auto-focus the right panel:

```python
elif self._right_panel_mode == "lyrics":
    left_panel.remove_class("dismissed-right")
    right_panel.display = True
    lyrics.display = True
    details.display = False
    self._active_panel = "right"          # NEW
    self._refresh_lyrics_panel()
    lyrics.focus()                          # NEW

elif self._right_panel_mode == "details":
    left_panel.remove_class("dismissed-right")
    right_panel.display = True
    lyrics.display = False
    details.display = True
    self._active_panel = "right"          # NEW
    self._refresh_detail_panel()
    details.focus()                         # NEW
```

**Rationale:** When the user presses `v` to cycle to a visible right-panel mode, they expect the right panel to be interactive. Auto-focusing eliminates the need to Tab after cycling, which is especially important since Tab itself has issues (Issue B).

**Edge case:** If the user is in "left" mode and presses `v` to cycle to "details", then presses `v` again to "hidden", the "hidden" branch already sets `_active_panel = "left"` and focuses the table — this is correct.

## Issue B: TAB Does Not Cycle Through Left/Right Panels

### Root Cause

`screen.py:425-426` (BINDINGS):

```python
Binding("tab", "cycle_panel_next", "Panel →"),
Binding("shift+tab", "cycle_panel_prev", "Panel ←"),
```

In Textual, the `Screen` base class has built-in `tab`/`shift+tab` handling in its `on_key` method that calls `focus_next()`/`focus_previous()`. The binding resolution order in Textual is:

1. **Priority bindings** (checked first, at all levels: widget → screen → app)
2. **`on_key` handlers** (widget → screen → app) — `Screen.on_key` intercepts `tab`/`shift+tab` here
3. **Non-priority bindings** (widget → screen → app)

Since the `tab` binding lacks `priority=True`, Textual's built-in `Screen.on_key` focus management fires first, consuming the key event before `action_cycle_panel_next` can execute.

### Fix

**Location:** `screen.py:425-426` (BINDINGS)

Add `priority=True` to both bindings:

```python
Binding("tab", "cycle_panel_next", "Panel →", priority=True),
Binding("shift+tab", "cycle_panel_prev", "Panel ←", priority=True),
```

With `priority=True`, these bindings are checked before any `on_key` handler, so `action_cycle_panel_next`/`action_cycle_panel_prev` fire instead of Textual's built-in focus cycling.

**No other changes needed** — the `action_cycle_panel_next`/`action_cycle_panel_prev` methods (`screen.py:1049-1065`) and `_focus_active_panel` (`screen.py:1067-1075`) are already correctly implemented.

## Issue C: Data Table Should Include All Essential Components

### Current State

| Component | DB `role` | DB `component_type` | In Table? |
|-----------|-----------|---------------------|-----------|
| Entry chorus | `"entry"` | `"chorus"` | Yes |
| Exit chorus | `"exit"` | `"chorus"` | Yes |
| Verse 1 | `"none"` | `"verse"` (occurrence_index=1) | No |
| Bridge | `"none"` | `"bridge"` (occurrence_index=1) | No |

**Blockers:**
- `SongSession` (`state.py:29-54`) only holds `entry_component: SongComponent | None` and `exit_component: SongComponent | None`
- `component_for_role()` (`state.py:53-54`) only handles "entry" and "exit"
- `_refresh_table()` (`screen.py:565-575`) iterates over `("entry", "exit")` only
- `_selected_role()` (`screen.py:741-742`) assumes binary: `"entry" if selected_row == 0 else "exit"`
- `_sync_selection_from_table_cursor()` (`screen.py:759`) clamps to `0 <= cursor_row <= 1`
- `action_save()` (`screen.py:1313`) hardcodes `updates_by_role = {"entry": {}, "exit": {}}`
- `_save_r2_component_result()` (`screen.py:1393-1397`) skips components with `role not in ("entry", "exit")`
- `_reload_components_from_db()` (`screen.py:1410-1416`) calls `get_song_components_entry_exit()` — only loads 2 components
- `first_content_hash()` (`screen.py:88-98`) references `session.entry_component`/`session.exit_component` directly
- `ComponentHeroPanel.render_panel()` (`screen.py:194`) determines role via `state.selected_row == 0`
- `ComponentDetailPanel.update_detail()` (`detail_panel.py:50`) determines role via `state.selected_row == 0`
- `ComponentAutosaveState` (`autosave.py:22-34`) stores `selected_row` as int — works for any row count, but `working` list uses arbitrary `role` strings which need to include "verse1"/"bridge"
- Command in `audio.py:5435` calls `get_song_components_entry_exit()` — only loads 2 components

### Design: Editor-Level Role Keys

Introduce editor-level "role keys" that map to specific DB components. These are **editor concepts**, not DB `role` values:

```python
# constants.py
ESSENTIAL_COMPONENT_SLOTS: tuple[tuple[str, str, int], ...] = (
    # (editor_role, db_role_or_component_type, occurrence_index)
    ("entry", "entry", 1),      # role == "entry"
    ("exit", "exit", 1),        # role == "exit"
    ("verse1", "verse", 1),     # component_type == "verse", occurrence_index == 1
    ("bridge", "bridge", 1),    # component_type == "bridge", occurrence_index == 1
)
```

The working dict (`session.working`) keys are `(editor_role, field)` tuples. The autosave schema already stores these as `{"role": str, "field": str, "value": Any}` — `"verse1"` and `"bridge"` are valid string values, so **autosave is backward compatible**.

### Implementation Plan

#### Phase C1: Extend `SongSession` (`state.py`)

Replace fixed `entry_component`/`exit_component` fields with a list-based model while preserving backward compatibility:

```python
@dataclass
class SongSession:
    song_id: str
    song_title: str
    hash_prefix: str
    audio_path: str
    audio_duration: float | None
    # v7: all essential components (entry, exit, verse1, bridge)
    components: dict[str, SongComponent | None] = field(default_factory=dict)
    # Legacy accessors for backward compat (first_content_hash, R2 payload synthesis)
    entry_component: SongComponent | None = None   # = components.get("entry")
    exit_component: SongComponent | None = None    # = components.get("exit")
    song: Song | None = None
    working: dict[tuple[str, str], Any] = field(default_factory=dict)
    dirty: bool = False
    r2_save_pending: bool = False

    def component_for_role(self, editor_role: str) -> SongComponent | None:
        """Return the component for an editor-level role key."""
        return self.components.get(editor_role)

    @property
    def ordered_component_roles(self) -> list[str]:
        """Roles that have a non-None component, in canonical display order."""
        from ...constants import ESSENTIAL_COMPONENT_SLOTS
        return [
            role for role, _, _ in ESSENTIAL_COMPONENT_SLOTS
            if self.components.get(role) is not None
        ]
```

**Backward compat:** `entry_component`/`exit_component` remain as fields, set alongside `components`. `first_content_hash()` continues to work unchanged.

#### Phase C2: Add `ESSENTIAL_COMPONENT_SLOTS` Constant (`constants.py`)

```python
ESSENTIAL_COMPONENT_SLOTS: tuple[tuple[str, str, int], ...] = (
    ("entry", "entry", 1),       # role == "entry"
    ("exit", "exit", 1),         # role == "exit"
    ("verse1", "verse", 1),      # component_type == "verse", occ == 1
    ("bridge", "bridge", 1),     # component_type == "bridge", occ == 1
)

def identify_editor_role(comp: SongComponent) -> str | None:
    """Map a DB SongComponent to its editor-level role key, or None if not essential."""
    if comp.role == "entry":
        return "entry"
    if comp.role == "exit":
        return "exit"
    if comp.component_type == "verse" and comp.occurrence_index == 1:
        return "verse1"
    if comp.component_type == "bridge" and comp.occurrence_index == 1:
        return "bridge"
    return None
```

#### Phase C3: Load All Essential Components (`audio.py:5419-5467`)

Replace `get_song_components_entry_exit()` with `get_song_components()` (returns all, ordered by `start_time`):

```python
for song_id in song_ids:
    ...
    all_components = db_client.get_song_components(song_id)
    if not all_components:
        console.print(f"[yellow]No component analysis for {song_id}; skipping.[/yellow]")
        continue

    # Filter to essential components
    from stream_of_worship.admin.component_editor.constants import identify_editor_role
    components: dict[str, SongComponent | None] = {}
    for comp in all_components:
        editor_role = identify_editor_role(comp)
        if editor_role and editor_role not in components:
            components[editor_role] = comp

    if not components:
        console.print(f"[yellow]No essential components for {song_id}; skipping.[/yellow]")
        continue

    # Ensure entry/exit are present even if None (for first_content_hash backward compat)
    entry_comp = components.get("entry")
    exit_comp = components.get("exit")

    sessions.append(
        SongSession(
            song_id=song_id,
            song_title=song.title,
            hash_prefix=recording.hash_prefix,
            audio_path=str(audio_path),
            audio_duration=recording.duration_seconds,
            components=components,
            entry_component=entry_comp,   # backward compat
            exit_component=exit_comp,    # backward compat
            song=song,
        )
    )
```

#### Phase C4: Update `_refresh_table()` (`screen.py:565-575`)

Iterate over the session's available components (not just entry/exit):

```python
def _refresh_table(self) -> None:
    table = self.query_one("#component-table", DataTable)
    table.clear()
    session = self.state.current
    for editor_role in session.ordered_component_roles:
        row_values = [self._cell_value(editor_role, key) for key, _ in COMPACT_TABLE_COLUMNS]
        table.add_row(*row_values, key=editor_role)
    row = max(0, min(self.state.selected_row, max(0, len(session.ordered_component_roles) - 1)))
    try:
        table.move_cursor(row=row, scroll=True)
    except Exception:
        pass
```

#### Phase C5: Update `_cell_value()` (`screen.py:548-554`)

Already takes a `role: str` parameter — just ensure it's called with editor_role keys. No change needed beyond the caller (Phase C4).

#### Phase C6: Update `_selected_role()` to `_selected_editor_role()` (`screen.py:741-742`)

Replace binary mapping with dynamic lookup based on the current session's component list:

```python
def _selected_editor_role(self) -> str:
    """Return the editor-level role key for the currently highlighted table row."""
    roles = self.state.current.ordered_component_roles
    idx = max(0, min(self.state.selected_row, len(roles) - 1))
    return roles[idx]
```

#### Phase C7: Update All Role References in `screen.py`

Replace all `self._selected_role()` calls with `self._selected_editor_role()`. Key locations:
- `screen.py:194` — `ComponentHeroPanel.render_panel()` (role determination)
- `screen.py:1215, 1227, 1244, 1252, 1271` — edit action handlers
- `screen.py:796-798` — `_guard_no_component()`
- `screen.py:1170, 1185` — playback actions

**Hero panel** (`screen.py:194`): Replace `role = "entry" if state.selected_row == 0 else "exit"` with:
```python
roles = state.current.ordered_component_roles
idx = max(0, min(state.selected_row, len(roles) - 1))
editor_role = roles[idx]
# Display label: "ENTRY CHORUS", "EXIT CHORUS", "VERSE 1", "BRIDGE"
role_labels = {"entry": "ENTRY CHORUS", "exit": "EXIT CHORUS", "verse1": "VERSE 1", "bridge": "BRIDGE"}
label = role_labels.get(editor_role, editor_role.upper())
```

**Detail panel** (`detail_panel.py:50`): Same dynamic role lookup:
```python
roles = state.current.ordered_component_roles
idx = max(0, min(state.selected_row, len(roles) - 1))
editor_role = roles[idx]
comp = session.component_for_role(editor_role)
```

#### Phase C8: Update `_sync_selection_from_table_cursor()` (`screen.py:751-763`)

Remove the `0 <= cursor_row <= 1` clamp; use dynamic max:

```python
def _sync_selection_from_table_cursor(self) -> None:
    try:
        table = self.query_one("#component-table", DataTable)
    except NoMatches:
        return
    cursor_row = table.cursor_row
    if cursor_row is None:
        return
    max_row = max(0, len(self.state.current.ordered_component_roles) - 1)
    if 0 <= cursor_row <= max_row:
        self.state.selected_row = cursor_row
    self._refresh_detail_panel()
    self._refresh_hero()
```

#### Phase C9: Update `action_save()` (`screen.py:1304-1354`)

Replace hardcoded `{"entry": {}, "exit": {}}` with dynamic grouping:

```python
def action_save(self) -> None:
    ...
    session = self.state.current
    # Group dirty edits by editor_role
    updates_by_role: dict[str, dict[str, Any]] = {}
    for (editor_role, field), value in session.working.items():
        updates_by_role.setdefault(editor_role, {})[field] = value

    # Write DB (targeted UPDATE per component, single transaction)
    try:
        with self.db_client.transaction() as conn:
            for editor_role, fields in updates_by_role.items():
                comp = session.component_for_role(editor_role)
                if comp is None or comp.id is None or not fields:
                    continue
                self.db_client.update_song_component_fields_txn(conn, comp.id, fields)
    except Exception as e:
        ...
```

#### Phase C10: Update `_save_r2_component_result()` (`screen.py:1356-1408`)

Replace `role in ("entry", "exit")` matching with editor-role matching:

```python
from stream_of_worship.admin.component_editor.constants import identify_editor_role

components = payload.get("components", [])
for comp_dict in components:
    # Reconstruct a minimal SongComponent to identify its editor role
    temp_comp = SongComponent(
        component_type=comp_dict.get("component_type", ""),
        occurrence_index=comp_dict.get("occurrence_index", 1),
        role=comp_dict.get("role", "none"),
    )
    editor_role = identify_editor_role(temp_comp)
    if editor_role and editor_role in updates_by_role:
        fields = updates_by_role[editor_role]
        for field, value in fields.items():
            comp_dict[field] = value
```

Also update the payload synthesis fallback (`screen.py:1375-1379`) to include all components:

```python
if payload is None:
    payload = {...}
    for editor_role in session.ordered_component_roles:
        comp = session.component_for_role(editor_role)
        if comp is not None:
            payload["components"].append(comp.to_dict())
```

#### Phase C11: Update `_reload_components_from_db()` (`screen.py:1410-1416`)

Reload all essential components, not just entry/exit:

```python
def _reload_components_from_db(self, session: SongSession) -> None:
    all_components = self.db_client.get_song_components(session.song_id)
    from stream_of_worship.admin.component_editor.constants import identify_editor_role
    for comp in all_components:
        editor_role = identify_editor_role(comp)
        if editor_role:
            session.components[editor_role] = comp
    # Update legacy fields
    session.entry_component = session.components.get("entry")
    session.exit_component = session.components.get("exit")
```

#### Phase C12: Autosave Compatibility (`autosave.py`)

**No schema change needed.** `ComponentAutosaveState.working` already stores `{"role": str, "field": str, "value": Any}` — "verse1" and "bridge" are valid string role values. `selected_row` is an int that works for any row count.

**One consideration:** If an autosave from a v6 session (with "entry"/"exit" roles only) is recovered in v7, the working dict keys are still valid because `component_for_role("entry")` and `component_for_role("exit")` still work.

#### Phase C13: Tests (`test_screen.py`, `test_hero_panel.py`)

**Update existing tests:**
- All tests that set `_active_panel = "right"` + `_right_panel_mode = "details"` will still work (Issue A fix doesn't break the manual setup approach)
- `_make_session()` helper needs to accept `components` dict or auto-populate from entry/exit
- `test_d2_hero_refreshes_on_cursor_move` — now uses dynamic role lookup, not hardcoded "entry"/"exit"

**New tests for Issue A:**
```python
async def test_v_cycle_to_details_auto_focuses_right_panel():
    """Pressing v to switch to details mode auto-sets _active_panel='right'."""
    app, _ = _make_app()
    async with app.run_test(size=(160, 30)) as pilot:
        await pilot.pause()
        # Default is lyrics
        assert app.screen._right_panel_mode == "lyrics"
        assert app.screen._active_panel == "right"  # auto-focused (Issue A fix)
        # Press v -> details
        await pilot.press("v")
        await pilot.pause()
        assert app.screen._right_panel_mode == "details"
        assert app.screen._active_panel == "right"  # auto-focused (Issue A fix)

async def test_v_cycle_to_lyrics_auto_focuses_right_panel():
    """Pressing v from hidden to lyrics auto-focuses lyrics panel."""
    app, _ = _make_app()
    async with app.run_test(size=(160, 30)) as pilot:
        await pilot.pause()
        # Cycle to hidden
        app.screen._right_panel_mode = "hidden"
        app.screen._apply_right_panel_mode()
        await pilot.pause()
        assert app.screen._active_panel == "left"
        # Press v -> lyrics
        await pilot.press("v")
        await pilot.pause()
        assert app.screen._right_panel_mode == "lyrics"
        assert app.screen._active_panel == "right"  # auto-focused (Issue A fix)
```

**New tests for Issue B:**
```python
async def test_tab_cycles_left_to_right():
    """Tab moves focus from left panel to right panel."""
    app, _ = _make_app()
    async with app.run_test(size=(160, 30)) as pilot:
        await pilot.pause()
        # At launch: lyrics mode, focus should be on table (left)
        await pilot.press("tab")
        await pilot.pause()
        assert app.screen._active_panel == "right"

async def test_tab_cycles_right_to_left():
    """Tab moves focus from right panel back to left panel."""
    app, _ = _make_app()
    async with app.run_test(size=(160, 30)) as pilot:
        await pilot.pause()
        # Cycle to right
        await pilot.press("tab")
        await pilot.pause()
        assert app.screen._active_panel == "right"
        # Tab again -> back to left
        await pilot.press("tab")
        await pilot.pause()
        assert app.screen._active_panel == "left"
```

**New tests for Issue C:**
```python
async def test_table_shows_4_essential_components():
    """Data table includes entry, exit, verse1, bridge rows when available."""
    entry = _make_component("entry", cid=1)
    exit_c = _make_component("exit", cid=2)
    verse1 = _make_component("verse1", cid=3, component_type="verse")
    bridge = _make_component("bridge", cid=4, component_type="bridge")
    session = _make_session(components={"entry": entry, "exit": exit_c, "verse1": verse1, "bridge": bridge})
    app, _ = _make_app(sessions=[session])
    async with app.run_test(size=(160, 30)) as pilot:
        await pilot.pause()
        table = app.screen.query_one("#component-table", DataTable)
        assert table.row_count == 4

async def test_table_shows_only_available_components():
    """Table only shows rows for components that exist (omits None)."""
    entry = _make_component("entry", cid=1)
    exit_c = _make_component("exit", cid=2)
    # No verse1 or bridge
    session = _make_session(components={"entry": entry, "exit": exit_c})
    app, _ = _make_app(sessions=[session])
    async with app.run_test(size=(160, 30)) as pilot:
        await pilot.pause()
        table = app.screen.query_one("#component-table", DataTable)
        assert table.row_count == 2

async def test_hero_shows_verse1_label():
    """Hero panel shows 'VERSE 1' when verse1 row is selected."""
    ...
    assert "VERSE 1" in hero_text

async def test_hero_shows_bridge_label():
    """Hero panel shows 'BRIDGE' when bridge row is selected."""
    ...
    assert "BRIDGE" in hero_text

async def test_save_writes_all_4_components():
    """Save updates DB for entry, exit, verse1, bridge."""
    ...

async def test_r2_merge_updates_verse1_and_bridge():
    """R2 component.json merge updates verse1/bridge components."""
    ...
```

**Update `_make_session()` helper:**
```python
def _make_session(
    song_id="song_001",
    song_title="Test Song",
    hash_prefix="abc123def456",
    components: dict[str, SongComponent | None] | None = None,
    ...
) -> SongSession:
    if components is None:
        components = {
            "entry": _make_component("entry", cid=1),
            "exit": _make_component("exit", cid=2),
        }
    return SongSession(
        ...
        components=components,
        entry_component=components.get("entry"),
        exit_component=components.get("exit"),
    )
```

## Affected Files

| File | Changes |
|------|---------|
| `screen.py` | Issue A: add `_active_panel`/`focus()` to `_apply_right_panel_mode`; Issue B: add `priority=True` to tab bindings; Issue C: update table refresh, role lookup, save, R2 merge, reload |
| `state.py` | Issue C: add `components` dict field, `ordered_component_roles` property, update `component_for_role` |
| `constants.py` | Issue C: add `ESSENTIAL_COMPONENT_SLOTS`, `identify_editor_role()` |
| `detail_panel.py` | Issue C: use dynamic role lookup instead of `selected_row == 0` |
| `audio.py` (`commands/`) | Issue C: load all essential components via `get_song_components()` |
| `autosave.py` | No change (backward compatible) |
| `test_screen.py` | Issue A/B/C: update helpers, update existing tests, add new tests |
| `test_hero_panel.py` | Issue C: update hero role label tests for verse1/bridge |

## Implementation Order

1. **Issue B first** (smallest change — 2 lines) — add `priority=True` to tab/shift+tab bindings
2. **Issue A second** (small change — 4 lines) — add `_active_panel = "right"` + `focus()` calls to `_apply_right_panel_mode`
3. **Issue C last** (largest change) — extend data model, update all role references, add tests

Issues A and B should be done first because they're trivial fixes and immediately unblock the right panel. Issue C is a larger refactor that can be verified independently.

## Verification

```bash
# Run component editor tests
uv run --project ops/admin-cli --python 3.11 --extra admin --extra test pytest tests/admin/component_editor/ -v

# Lint + format
uv run --project ops/admin-cli ruff check src/stream_of_worship/admin/component_editor/
uv run --project ops/admin-cli ruff format --check src/stream_of_worship/admin/component_editor/

# Manual smoke test: verify v, tab, and 4-component table
uv run --project ops/admin-cli --extra admin sow-admin audio components review <song_id>
```

## Backward Compatibility

- **Autosave**: `ComponentAutosaveState` schema unchanged — `"verse1"` and `"bridge"` are valid string role values in the `working` list
- **DB save**: `update_song_component_fields_txn` is component-id-based, not role-based — works for any component
- **R2**: `components.json` array already contains all component types; the merge logic now matches by `(component_type, occurrence_index)` instead of `role in ("entry", "exit")`
- **Keyboard shortcuts**: No existing binding changes (only `priority=True` added to tab/shift+tab)
- **Legacy `entry_component`/`exit_component` fields**: Retained on `SongSession` for backward compat (used by `first_content_hash()`)
