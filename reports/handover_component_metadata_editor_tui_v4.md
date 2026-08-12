# Handover: Component Metadata Editor TUI v4 Implementation

> **Date:** 2026-08-12
> **Spec:** `specs/component-metadata-editor-tui-v4.md`
> **Status:** Implementation ~90% complete. All 88 tests pass. Linting cleanup remains.

---

## Summary

Implemented the v4 spec for the Component Metadata Editor TUI. The v4 spec
introduces four UX improvements (D1–D4) over v3, all driven by the goal of
making transition-critical fields (BPM, Key, Theme, Vocal Posture, Energy,
Groove) and LLM reasoning prominently visible during songset transition
planning review.

The existing codebase had a **v3-variant implementation** with a 3-panel layout
(compact table + lyrics panel + detail panel). The v4 spec requires a
**different layout**: a single full DataTable with reordered columns + a new
ComponentHeroPanel widget above it. This required a substantial rewrite of
`screen.py` and `constants.py`, plus new test files.

---

## What was done

### Phase 0: DB & R2 persistence helpers — ALREADY EXISTED (verified)

- `db/client.py`: `update_song_component_fields()` and
  `update_song_component_fields_txn()` already implemented at lines 2122–2183.
- `services/r2.py`: `download_component_result()` and
  `upload_component_result()` already implemented at lines 567–609.
- No changes needed.

### Phase 1: Typer command — ALREADY EXISTED (verified)

- `commands/audio.py`: `review_components()` command already implemented at
  lines 4516–4621. No changes needed.

### Phase 2: State, autosave, constants

#### `constants.py` — REWRITTEN (D1 + D4)

- `DATA_TABLE_COLUMNS` reordered: now a 4-tuple `(key, header, editable, cluster)`
  with three visual clusters:
  1. `transition-cluster` (role, type, occ, start, end, bpm, key, *theme,
     *posture, *energy, *groove, backbeat) — positions 0–11
  2. `audit-cluster` (8 confidence columns + 2 reasoning columns with `⁂`
     suffix) — positions 12–21
  3. `meta-cluster` (created_at, updated_at) — positions 22–23
- New `HERO_PRIMARY_FIELDS`: 5-tuple of (field, label, format_spec) for the
  Hero panel's primary metrics row (bpm, key, energy_level, groove_density,
  backbeat_strength).
- New `HERO_REASONING_FIELDS`: 2-tuple of (field, label) for the Hero panel's
  italic reasoning rows (theme_reasoning, posture_reasoning).
- New `REASONING_TABLE_TRUNC = 40` and `REASONING_CELL_WIDTH = 41`.
- Removed old `COMPACT_TABLE_COLUMNS` (was for the v3 3-panel layout).

#### `state.py` — ADDED `get_selected_component()` (D2)

- Added `get_selected_component()` method (lines 160–165) that returns the
  `SongComponent` for the currently-highlighted row (entry if
  `selected_row == 0`, exit if `selected_row == 1`, `None` for partial analysis).
- Removed v3's `lrc_fetches` / `lrc_parsed` / `lrc_prefetch_in_progress` /
  `lrc_fetch_error` fields from `ComponentEditorState` — these were for the v3
  lyrics panel which no longer exists in v4.

#### `autosave.py` — UNCHANGED (verified, v3 was already correct)

### Phase 3: TUI app + screen — REWRITTEN (D2 + D3)

#### `app.py` — UNCHANGED (verified)

#### `screen.py` — MAJOR REWRITE (~1200 LOC)

**New layout (top → bottom):**
```
Header
SongBreadcrumb
PlaybackBar
ComponentHeroPanel       (v4 NEW)
ComponentMetadataTable    (single DataTable, reordered columns)
StatusIndicator
GroupedFooter
```

**Removed v3 widgets:** `LyricsPanel`, `ComponentDetailPanel`, `LRCFetch`,
and all the 3-panel navigation machinery (`_PANEL_ORDER`, `cycle_panel_next`,
`cycle_panel_prev`, `detail_focus_up`, `detail_focus_down`,
`_refresh_detail_panel`, `_refresh_lyrics_panel`, `_prefetch_lrc`,
`_fetch_lrc_on_demand`).

**New `ComponentHeroPanel` widget (D2):**
- `Static` subclass with `render_panel(state)` method.
- Renders 5 rows: header (bold, cyan for entry / magenta for exit), primary
  metrics, editable theme+vocal_posture (yellow bold), theme reasoning (italic
  dimmed), posture reasoning (italic dimmed).
- Handles `None` component case with a dimmed "No {role} Chorus component"
  message.
- Handles `None` reasoning with "(LLM did not supply reasoning)" placeholder.

**New `_refresh_hero()` method:**
- Called on mount, cursor move, song switch, edit, undo/redo, save, autosave
  recovery.

**New `action_toggle_playback_for_component` (D3):**
- If playing → pause.
- If paused/stopped: seek to highlighted component's `start_time` (unless
  already inside `[start, end]` range), then play.
- If no component highlighted: best-effort `play()` without seeking.

**New `action_cursor_right` / `action_cursor_left`:**
- Screen-level handlers that forward to the DataTable's cursor actions and
  update `state.selected_column_key`.

**`_selected_field_key()` helper:**
- Reads the DataTable's cursor column and maps it to a field key via
  `DATA_TABLE_COLUMNS`. Replaces v3's `detail_panel.focused_field` approach.

**Reasoning cell rendering (D4):**
- `_format_cell_value()` now truncates reasoning fields to
  `REASONING_TABLE_TRUNC` chars + `…` ellipsis.

**Bindings (D3):**
- `space` → `toggle_playback_for_component` (renamed from `toggle_playback`).
- `tab` / `shift+tab` → `cursor_right` / `cursor_left` (column navigation).
- Removed v3's panel navigation bindings.

### Phase 4–8: Bindings, playback, save flow, autosave, quit — IMPLEMENTED

All action handlers (`action_save`, `action_undo`, `action_redo`,
`action_cycle_field_next`, `action_cycle_field_prev`, `action_edit_numeric`,
`action_quit_editor`, `action_show_keymap`) implemented with `_refresh_hero()`
calls at every state mutation point.

### Phase 9: Tests

#### New test files:
- `tests/admin/component_editor/test_constants.py` (19 tests) — D1 column
  ordering, cluster tagging, Hero field configuration, reasoning truncation.
- `tests/admin/component_editor/test_hero_panel.py` (11 tests) — D2 Hero panel
  rendering: entry/exit header, missing component, BPM/Key in primary row,
  theme/posture in editable row, full reasoning text, placeholder for None,
  update after edit, time range.

#### Updated test files:
- `tests/admin/component_editor/test_state.py` — Added `TestGetSelectedComponent`
  class (4 tests) for the new `get_selected_component()` helper.
- `tests/admin/component_editor/test_screen.py` — Rewritten for v4 layout.
  Includes all v2 regression tests (B1–B3, C1–C5) and new v4 D1–D4 regression
  tests. 37 tests total.

#### Test results: **88 passed, 0 failed**

---

## What remains to be done

### 1. Linting cleanup (REQUIRED before commit)

Run `ruff check` and `black` on the changed files. There are 34 ruff errors
and 6 files needing black reformatting. Most are minor:

- **`RUF012`** (mutable class default): `BINDINGS` and `BINDING_GROUPS` lists
  on `ComponentEditorScreen`, `KeymapDialog`, `QuitConfirmDialog`. These are
  Textual framework conventions (the LRC editor has the same pattern), so
  either add `ClassVar` annotations or add a per-file ruff ignore.
- **`BLE001`** (blind exception catch): Several `except Exception` blocks in
  save flow and table refresh. These match the v3 patterns and the LRC editor.
  Either add `# noqa: BLE001` comments or configure ruff to ignore for this
  module.
- **`S110`** (try-except-pass): Table cursor move / cell update guards. Same
  as v3.
- **`F401`** (unused imports): `PropertyMock` and `ComponentEditorScreen` in
  test_screen.py. Auto-fixable with `ruff check --fix`.
- **`RUF059`** (unused unpacked variable): Several tests unpack `app, state`
  but only use `app`. Prefix with `_` or use `app, _ = _make_app(...)`.
- **`RUF013`** (implicit Optional): `cache_dir: Path = None` in
  test_hero_panel.py. Change to `Path | None = None`.
- **`SIM102`**: Nested if in `_validate_numeric_field`. Minor style.
- **Black**: Run `black --line-length 100` on the 6 files.

**Commands to fix:**
```bash
cd ops/admin-cli
uv run ruff check --fix src/stream_of_worship/admin/component_editor/ tests/admin/component_editor/
uv run black --line-length 100 src/stream_of_worship/admin/component_editor/ tests/admin/component_editor/
```

### 2. Remove orphaned v3 files (RECOMMENDED)

The following files are no longer imported by anything in v4 but still exist:
- `src/stream_of_worship/admin/component_editor/detail_panel.py` — v3's
  bottom-right detail panel. No longer used.
- `src/stream_of_worship/admin/component_editor/lyrics_panel.py` — v3's
  bottom-left lyrics panel. No longer used.
- `src/stream_of_worship/admin/component_editor/lrc_fetch.py` — v3's LRC
  pre-fetch logic. No longer used.

**Verify with:**
```bash
rg "detail_panel|lyrics_panel|lrc_fetch|ComponentDetailPanel|LyricsPanel|LRCFetch" \
  src/stream_of_worship/admin/component_editor/screen.py
```
Should return no matches (screen.py no longer imports them). Then delete the
3 files.

### 3. Verify no other code references the removed v3 widgets

```bash
rg "ComponentDetailPanel|LyricsPanel|LRCFetch|COMPACT_TABLE_COLUMNS" \
  --type py
```
If any external code references these, update or remove the references.

### 4. Run the full test suite again after linting

```bash
cd ops/admin-cli
uv run --python 3.11 --extra admin --extra test pytest tests/admin/component_editor/ -v
```

### 5. Commit and push (per AGENTS.md session completion rules)

```bash
git add -A
git commit -m "feat: implement component-metadata-editor-tui-v4 (D1-D4)

- D1: Reorder DATA_TABLE_COLUMNS so transition-critical + editable fields
  come first (transition-cluster), followed by audit-context (confidences +
  reasoning), then timestamps (meta-cluster).
- D2: New ComponentHeroPanel widget above the DataTable, always-visible
  summary of the highlighted component's transition-critical fields + LLM
  reasoning. Refreshes on cursor move, song switch, edit, undo/redo, save.
- D3: space → action_toggle_playback_for_component: seek to highlighted
  component's start_time then play (unless already inside [start,end]).
- D4: Reasoning columns dimmed + truncated in table; full text in Hero panel.

All 88 tests pass."
git pull --rebase
git push
git status  # MUST show 'up to date with origin'
```

---

## Key files changed

| File | Change |
|---|---|
| `src/.../component_editor/constants.py` | **Rewritten** — reordered columns, new Hero constants |
| `src/.../component_editor/state.py` | **Modified** — added `get_selected_component()`, removed v3 LRC fields |
| `src/.../component_editor/screen.py` | **Rewritten** — new Hero panel + single DataTable layout, D3 playback |
| `src/.../component_editor/app.py` | Unchanged (verified) |
| `src/.../component_editor/autosave.py` | Unchanged (verified) |
| `tests/.../component_editor/test_constants.py` | **New** — 19 tests |
| `tests/.../component_editor/test_hero_panel.py` | **New** — 11 tests |
| `tests/.../component_editor/test_state.py` | **Modified** — added 4 `get_selected_component` tests |
| `tests/.../component_editor/test_screen.py` | **Rewritten** — 37 tests for v4 layout + D1–D4 regressions |

## Files to delete (orphaned v3)

| File | Reason |
|---|---|
| `src/.../component_editor/detail_panel.py` | v3 detail panel, no longer imported |
| `src/.../component_editor/lyrics_panel.py` | v3 lyrics panel, no longer imported |
| `src/.../component_editor/lrc_fetch.py` | v3 LRC fetch logic, no longer imported |

---

## Architecture notes for the next agent

### How the v4 layout works

The screen composes widgets vertically:
1. `Header()` — Textual standard
2. `SongBreadcrumb` — "● Song 2 / 5 — [song_id] song_title — hash_prefix=..."
3. `PlaybackBar` — "▶ [00:23 / 03:45] ████░░░░░░"
4. `ComponentHeroPanel` — 5-row rich-text summary of the highlighted component
5. `ComponentMetadataTable` — DataTable with 2 rows (entry/exit) × 24 columns
6. `Input(id="row-edit-input")` — hidden overlay for numeric editing
7. `StatusIndicator` — dirty/autosave/song-index/r2-pending
8. `GroupedFooter` — reused from `editor/footer.py`

### How cursor → field mapping works

The DataTable uses `cursor_type = "cell"`. When the cursor moves:
1. `on_data_table_row_highlighted` fires → `_sync_selection_from_table_cursor()`
   updates `state.selected_row` (0 or 1) and `state.selected_column_key`.
2. `_refresh_hero()` re-renders the Hero panel for the new row.
3. Column changes (via `tab`/`shift+tab`) update `selected_column_key` but do
   NOT trigger a Hero refresh (the Hero only depends on which row is highlighted).

### How the D3 playback semantics work

`action_toggle_playback_for_component()`:
- If `playback.is_playing` → `pause()`.
- Else: get `comp = state.get_selected_component()`. If comp is not None and
  current position is outside `[start_time, end_time]` → `seek(start_time)`.
  Then `play()`.
- If comp is None (partial analysis) → `play()` without seeking.

### How the D4 reasoning rendering works

- In the DataTable: `_format_cell_value()` truncates reasoning to 40 chars + `…`.
- In the Hero panel: `render_panel()` renders the full text on italic dimmed
  lines, reading directly from `getattr(comp, field_name, None)`.

---

## Pre-existing test failure (NOT related to this work)

`tests/admin/test_r2_backup.py::TestRangeGetDiagnostic::test_range_get_diagnostic_structure`
fails with `assert 10000000000.0 == 10.0`. This is a pre-existing failure
unrelated to the component editor changes.
