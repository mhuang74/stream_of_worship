---
 
# Implementation Plan: Component Metadata Editor TUI (v4)
 
> **Date:** 2026-08-12
> **Branch:** TBD
> **Spec ID:** `component-metadata-editor-tui-v4`
> **Supersedes:** `component-metadata-editor-tui-v3`
> **Status:** Standalone consolidation — builds on v3 (which already folded all
> v2 fixes inline) and adds four UX improvements that make transition-planning
> fields and Chorus audio metadata more prominently displayed. This document is
> self-contained; readers do NOT need to open v1, v2, or v3.
 
---
 
## Changelog (v4 vs v3)
 
v3 was a complete, self-contained plan that folded every v2 bugfix (B1–B3,
C1–C5) into the v1 section bodies. v4 inherits **all** of v3's design and
only diverges in four focused UX areas, all driven by the same product
concern: **when planning song-to-song transitions, an operator reviewing
component metadata needs the transition-critical fields — bpm, key, theme,
vocal_posture, energy_level, groove_density, plus the LLM reasoning —
visible at a glance, not buried after 16 confidence columns in a flat
27-column DataTable.** v3 had the 4 editable fields (`*Theme`, `*Posture`,
`*Groove`, `*Energy`) at columns 21–24 of a single DataTable, well past all
the per-field confidence columns. That made the four fields most relevant
to transition planning the hardest to visually reach.
 
v4 does NOT change any of v3's persistence, autosave, undo/redo, save-flow,
or R2-partial-failure machinery — those were already validated through the
v2 fix cycle. v4 changes only **rendering, layout, and the `space`
playback action semantics**.
 
### v4 deltas
 
| ID | Area | v3 behaviour | v4 behaviour |
|---|---|---|---|
| **D1** | Layout / table ordering | One 27-column `DataTable`; editable fields at columns 21–24. `theme_reasoning` / `posture_reasoning` at columns 17–18, sandwiched between confidence columns. | **Reorder `DATA_TABLE_COLUMNS`** so transition-critical + editable fields come first (role, type, occ, start, end, bpm, key, `*theme`, `*vocal_posture`, `*energy_level`, `*groove_density`, backbeat), followed by an audit-context cluster (confidences, reasoning), followed by timestamps. |
| **D2** | Layout / hero panel | No hero/summary widget; the only scannable surface is the DataTable itself. | **New `ComponentHeroPanel` widget** placed above the DataTable (below `SongBreadcrumb` + `PlaybackBar`). Always-visible summary of the currently-highlighted component's transition-critical fields — BPM, Key, Energy, Groove, Theme, Vocal Posture, Time range — plus `theme_reasoning` and `posture_reasoning` rendered as italic secondary lines (full text, no truncation). Refreshes whenever the cursor moves between the entry/exit row (or vice versa). |
| **D3** | Playback semantics | `space` → `action_toggle_playback`: a pure resume/pause toggle on the `PlaybackService`. Plays wherever the seek head happens to be. | **`space` → `action_toggle_playback_for_component`**: if currently playing, pause. If not playing, **seek to the highlighted component's `start_time` first, then play.** A separate `j` `action_jump_to_component` is kept for "seek only, no auto-play" use cases (matches v3). |
| **D4** | Reasoning access | `theme_reasoning` (col 17) and `posture_reasoning` (col 18) render as in-cell truncated text inside the DataTable, mixed between confidences. Hard to read. | Reasoning stays in the table but visually secondary (dimmed, truncated to 40 chars). **Authoritative source is now the Hero panel**, where the full LLM reasoning text renders on two italic lines that update with the cursor. |
 
### Decisions explicitly NOT taken in v4 (clarifying questions resolved)
 
These were considered and **rejected** after clarification with the operator:
 
1. **Cross-song neighbor context strip** — a thin strip showing the
   previous / next song's BPM, Key, Theme, Energy inline above the current
   song's view. **Rejected:** the operator prefers single-song-at-a-time
   review and uses `n` / `p` to cycle when comparing.
2. **Multi-song side-by-side compare view** — a toggle (`c` key) to a
   read-only compact list of all loaded songs' transition fields. **Rejected:**
   kept as a future-work item; v4 keeps v3's "no side-by-side split" decision.
3. **Standalone reasoning panel** below the DataTable — rejected in favour
   of folding the reasoning into the Hero panel itself (D2 + D4 together).
4. **Per-row `▶` play-segment button** that auto-stops at `end_time` —
   rejected. The operator wants playback to start at the highlighted
   component's `start_time` (D3) and play through the song naturally,
   mirroring the LRC editor's seek-to-start convention.
 
---
 
## Problem
 
The `sow-admin audio components` command (re)runs the Analysis Service's
Component Analysis job and persists the result into `song_components` plus
an R2 `components.json` cache. There is no interactive way for a human to:
 
- listen to the **entry** and **exit** Chorus segments,
- review the per-component **audio-derived** metadata (bpm, key, groove, energy),
- review the **LLM-derived** metadata (theme, vocal_posture + reasoning),
- compare values across multiple similar / different songs to gauge accuracy, and
- correct fields that are obviously wrong without re-running the (expensive) job.
 
**And, critically for v4:** the songset constructor (`lab/poc-scripts/poc/
songset_constructor/beam_search` and related) consumes `theme`, `bpm`,
`key`, `energy_level`, and `groove_density` to compute transition costs
between adjacent songs in a worship arc. If those fields are wrong on a
single component row, every downstream songset the constructor produces is
wrong. **v4 makes those exact fields — and the LLM reasoning behind `theme`
/ `vocal_posture` — visually prominent so an operator can spot a bad value
at a glance during review.**
 
The LRC Editor TUI (`sow-admin audio edit-lrc`) already solves the analogous
problem for lyric timestamps and is the established UX reference for hot-keys,
playback, and footer layout in the admin CLI.
 
## Goal
 
Add a new `sow-admin audio review-components <list of song_ids>` command that
launches a Textual TUI mirroring the LRC Editor's look-and-feel, focused on
viewing, comparing, and editing component-level metadata for the entry / exit
Chorus of one or more songs — **with transition-critical fields (BPM, Key,
Theme, Vocal Posture, Energy, Groove) and the LLM's reasoning surfaced in a
prominent Hero panel above a reordered DataTable, so an operator reviewing a
component for transition planning can verify the values that the songset
constructor will consume without scrolling past 16 confidence columns.**
 
## Design Decisions (from clarifying questions)
 
| Decision | Choice |
|---|---|
| Song switching | **In-TUI** via `n` / `p` hotkey cycle. All passed `song_id`s loaded up-front. Header shows current index (`Song 2 / 5`). **v4 unchanged.** |
| Compare view | **Quick-switch single view** — one song's metadata visible at a time; user cycles between songs to compare. No side-by-side split. **v4 unchanged.** |
| Editable fields | **Four human-judgement fields** only: `theme`, `vocal_posture`, `groove_density`, `energy_level`. **v4 unchanged.** |
| Persistence | **DB + R2 `components.json`**. Write through to `song_components` AND merge-field into the cached `components.json` on R2. **v4 unchanged.** |
| Playback behaviour | **v4 CHANGED.** `space` (highlighted component): seek to that component's `start_time` and start playing; if already playing, pause. Plays through the song naturally. `j` is kept as the v3 "seek-only, no auto-play" affordance. |
| Components shown | **`role = 'entry'` and `role = 'exit'` Chorus only**. Up to 2 rows per song. **v4 unchanged.** |
| Field editor UX | Enum fields cycle with `[` / `]`. Float fields use inline numeric input overlay. **v4 unchanged.** |
| Layout — primary data surface | **v4 CHANGED.** A single `DataTable` whose columns are reordered so transition-critical + editable fields are in the leftmost cluster, followed by an audit-context cluster (confidences, reasoning) and finally timestamps. |
| Layout — hero summary | **v4 NEW.** A `ComponentHeroPanel` widget placed above the DataTable always shows the highlighted component's transition-critical fields (BPM, Key, Energy, Groove, Theme, Vocal Posture, Time range) plus the LLM's `theme_reasoning` / `posture_reasoning` as italic secondary lines. Updates on every cursor move and every edit. |
| Autosave & undo | **Autosave + undo/redo** like the LRC editor. Autosave per song at `{cache_dir}/{hash_prefix}/components/components.autosave.json`. Undo stack max 100 entries; cleared on save. **v4 unchanged.** |
| Read-only context | **Show all fields, edit 4.** All `song_components` columns visible for context; only the 4 editable fields accept input. **v4 unchanged.** |
| Reasoning visibility | **v4 CHANGED.** The `theme_reasoning` / `posture_reasoning` columns remain in the table (as coarse presence indicators, dimmed, truncated to 40 chars) but the authoritative full-text rendering is in the Hero panel. |
 
---
 
## Reference: LRC Editor TUI structure (maximise reuse)
 
The new editor mirrors the package layout under
`ops/admin-cli/src/stream_of_worship/admin/editor/`:
 
| LRC Editor file | Reuse in component editor |
|---|---|
| `editor/app.py` (`LRCEditorApp`) | New `ComponentEditorApp` (Textual `App[None]`) |
| `editor/screen.py` (`LRCEditorScreen`) | New `ComponentEditorScreen` (Textual `Screen[None]`) — **v4 adds `ComponentHeroPanel` widget** |
| `editor/state.py` (`EditorState`, `UndoEntry`) | New `ComponentEditorState`, `ComponentUndoEntry` |
| `editor/autosave.py` (`AutosaveState`) | New `ComponentAutosaveState` |
| `editor/footer.py` (`GroupedFooter`) | **Reused directly** |
| `services/playback.py` (`PlaybackService`) | **Reused directly** (miniaudio-based) |
 
### LRC Editor hot-key reference (to mirror)
 
```
space        toggle_playback        Play/Pause              [v4: behaviour CHANGED — see Phase 5]
left/right   seek_backward/forward  Seek ±5s
j            jump_to_line           Seek to selected line   [v4: same — seek ONLY, no auto-play]
s            save_upload            Save
ctrl+z/y     undo/redo
escape / q   quit_editor
?            show_keymap
```
 
The component editor reuses **all** of these (with `jump_to_line` renamed
`jump_to_component`) and adds song-switch (`n` / `p`) + enum-cycling (`[` / `]`)
+ numeric edit (`e`) bindings. The `space` action is renamed
`toggle_playback_for_component` in v4 but the binding label `Play/Pause` is kept.
 
---
 
## Architecture overview
 
```
sow-admin audio review-components <song_id...> [--config PATH]
        │
        ▼
commands/audio.py::review_components()      (new Typer command)
        │  • resolves each song + recording
        │  • downloads/caches audio (one audio.mp3 per hash_prefix, like edit-lrc)
        │  • loads entry+exit SongComponent rows per song (db.get_song_components_entry_exit)
        │  • constructs ComponentEditorState per song
        │  • constructs R2Client + DatabaseClient + PlaybackService + ComponentEditorApp
        ▼
component_editor/app.py::ComponentEditorApp   (Textual App[None])
        │  on_mount → push ComponentEditorScreen
        ▼
component_editor/screen.py::ComponentEditorScreen  (Textual Screen[None])
   │  • Header()
   │  • SongBreadcrumb          — "● Song 2 / 5 — [song_id] song_title"
   │  • PlaybackBar             — ▶ [00:23 / 03:45]  (reused pattern)
   │  • ComponentHeroPanel      — [v4 NEW] always-visible summary of the highlighted component's
   │                              transition-critical fields + LLM reasoning (italic)
   │  • ComponentMetadataTable  — (DataTable) 2 rows × reordered columns (D1)
   │  • StatusIndicator         — dirty / autosave / song_index badge
   │  • GroupedFooter           — (reused from editor/footer.py)
   │
   ├── services/playback.py::PlaybackService  (REUSED — miniaudio)
   │
   ├── db/client.py::DatabaseClient
   │     • get_song_components_entry_exit(song_id) — EXISTS
   │     • update_song_component_fields(component_id, fields) — NEW (thin wrapper)
   │     • update_song_component_fields_txn(conn, component_id, fields) — NEW (txn variant)
   │
   └── services/r2.py::R2Client
         • download_component_result(hash_prefix) → dict | None — NEW
         • upload_component_result(hash_prefix, payload) → str — NEW
```
 
**Critical Separation:** Like the LRC editor, the new package must not import
PyTorch / ML libs. All ML is upstream in the Analysis Service.
 
---
 
## Phase 0: DB & R2 persistence helpers
 
**Goal:** Add a targeted single-row UPDATE that:
 
1. validates the editable-field whitelist, and
2. accepts a caller-supplied connection so multiple per-component UPDATEs
   can run inside one transaction.
 
Add R2 read/write helpers for `components.json`.
 
**v4 status:** Unchanged from v3. Restated here verbatim for self-containment.
**Complexity:** S
 
### 0.1 `ops/admin-cli/src/stream_of_worship/admin/db/client.py`
 
Add **two** new methods right after `get_song_components_entry_exit`
(currently ends at line 2118).
 
#### 0.1.1 `update_song_component_fields` (thin wrapper, no transaction)
 
Kept for non-transactional callers (tests, scripts that update a single
component row).
 
```python
def update_song_component_fields(
    self,
    component_id: int,
    fields: dict[str, float | str | None],
) -> bool:
    """Targeted UPDATE of editable metadata fields on a song_components row.

    Only the 4 user-editable fields may be passed:
        theme, vocal_posture, groove_density, energy_level
    Any other key raises ValueError. The `updated_at` column is bumped by the
    existing BEFORE UPDATE trigger (`trg_song_components_updated_at`).

    Args:
        component_id: song_components.id (NOT NULL — edits target a persisted row).
        fields: Dict of {column_name: new_value}. May be a subset.

    Returns:
        True if a row was updated; False if no row matched component_id.

    Raises:
        ValueError: If `fields` contains an unsupported column name.
    """
    with self.transaction() as conn:
        return self.update_song_component_fields_txn(conn, component_id, fields)
```
 
#### 0.1.2 `update_song_component_fields_txn` (NEW — caller-supplied connection)
 
Shared implementation used by `update_song_component_fields` and the
editor's `action_save` transaction.
 
```python
ALLOWED_COMPONENT_FIELDS: frozenset[str] = frozenset(
    {"theme", "vocal_posture", "groove_density", "energy_level"}
)
 
def update_song_component_fields_txn(
    self,
    conn: "psycopg.Connection",
    component_id: int,
    fields: dict[str, float | str | None],
) -> bool:
    """Targeted UPDATE on a song_components row using a caller-supplied
    connection. Validates the editable-field whitelist; intended for use
    inside a `DatabaseClient.transaction()` block so multiple per-component
    UPDATEs commit atomically.
    """
    invalid = set(fields) - ALLOWED_COMPONENT_FIELDS
    if invalid:
        raise ValueError(f"Cannot edit non-editable fields: {sorted(invalid)}")
    if not fields:
        return False
    set_clause = ", ".join(f"{col} = %s" for col in fields)
    params: list = list(fields.values()) + [component_id]
    cursor = conn.cursor()
    cursor.execute(
        f"UPDATE song_components SET {set_clause} WHERE id = %s",
        params,
    )
    return cursor.rowcount > 0
```
 
Rationale: the existing `upsert_song_components` is DELETE-then-INSERT and
would clobber untouched fields; it is the wrong primitive for user edits.
DB CHECK constraints (added in v5, see `schema.py:271-285`) already enforce
the `theme` (12-value) and `vocal_posture` (3-value) vocabularies at update
time, so the targeted UPDATE inherits them for free.
 
### 0.2 `ops/admin-cli/src/stream_of_worship/admin/services/r2.py`
 
Add two methods near the existing `download_analysis_json` (after line 565).
Use the same `_client` boto3 client and bucket.
 
```python
def download_component_result(self, hash_prefix: str) -> Optional[dict]:
    """Download and parse {hash_prefix}/components.json from R2.

    Returns the parsed dict (current schema_version = 2) or None if the
    object does not exist. Raises ClientError on non-404 failures and
    json.JSONDecodeError on a corrupt payload.
    """
    s3_key = f"{hash_prefix}/components.json"
    try:
        response = self._client.get_object(Bucket=self.bucket, Key=s3_key)
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "")
        if error_code in ("404", "NoSuchKey"):
            return None
        raise
    body = response["Body"].read().decode("utf-8")
    return json.loads(body)
 
def upload_component_result(
    self, hash_prefix: str, payload: dict
) -> str:
    """Upload (overwrite) {hash_prefix}/components.json with `payload`.

    `payload` must already include `schema_version`, `content_hash`,
    `hash_prefix`, `component_source`, and a `components` list. The caller
    is responsible for merging edited fields into the existing payload
    before calling this method.

    Returns the s3:// URL.
    """
    s3_key = f"{hash_prefix}/components.json"
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return self.upload_bytes(s3_key, data, content_type="application/json")
```
 
**Do NOT reuse** `AnalysisClient.get_cached_component_result`
(`services/analysis.py:678`): it has a stale check that returns `None` when
`schema_version != 1`, but the current component payload schema_version is 2.
That method is left untouched (out of scope here).
 
---
 
## Phase 1: Typer command — `audio review-components`
 
**v4 status:** Unchanged from v3. Restated compactly for self-containment.
**Complexity:** S
 
### 1.1 `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py`
 
Add a new `@app.command("review-components")` function in `commands/audio.py`
near the existing `edit-lrc` command (line 4318). Pseudocode:
 
```python
@app.command("review-components")
def review_components(
    song_ids: list[str] = typer.Argument(
        ..., help="One or more song IDs whose entry/exit Chorus metadata to review"
    ),
    config_path: Optional[Path] = typer.Option(
        None, "--config", "-c", help="Path to config file"
    ),
) -> None:
    """Launch a Textual TUI to view / review / compare / edit component metadata."""
    # 1. Load AdminConfig + DatabaseClient (mirrors edit-lrc lines 4330-4335)
    # 2. For each song_id:
    #    a. db.get_song(song_id) — error if missing (like edit-lrc line 4346)
    #    b. db.get_recording_by_song_id(song_id)
    #    c. db.get_song_components_entry_exit(song_id) — skip song with no rows
    #         (warn: 'no component analysis run for <song_id>; run `sow-admin audio components <song_id>` first')
    #    d. Ensure audio.mp3 cached under {cache_dir}/{hash_prefix}/audio/audio.mp3
    #         (reuse edit-lrc download pattern at lines 4360-4379)
    # 3. Construct R2Client (mirror edit-lrc lines 4350-4354)
    # 4. Deferred import:
    #        from stream_of_worship.admin.component_editor.app import ComponentEditorApp
    #        from stream_of_worship.admin.component_editor.state import ComponentEditorState
    #    (keep import inside the function — pattern matches edit-lrc)
    # 5. Build ComponentEditorState[] and PlaybackService()
    # 6. app = ComponentEditorApp(states, playback, cache_dir, r2_client, db_client)
    # 7. app.run()
```
 
Rejected songs (no `song_components` rows) are logged via the Rich `console`
and dropped from the list. If all songs are rejected, exit code 1.
 
Use `--config` flag and `get_cache_dir()` / `get_db_client(config)` helpers
exactly as in `edit-lrc` (lines 4330-4336).
 
---
 
## Phase 2: State, autosave, undo/redo model
 
**v4 status:** Phase 2.1, 2.3, 2.4, 2.5 unchanged from v3. **Phase 2.2
(constants) changed** to reorder columns (D1) and add new layout constants
for the Hero panel.
**Complexity:** M
 
### 2.1 New package `component_editor/`
 
```
ops/admin-cli/src/stream_of_worship/admin/component_editor/
├── __init__.py        # """Admin interactive Component Metadata editor package."""
├── app.py             # ComponentEditorApp (Textual App[None])
├── screen.py          # ComponentEditorScreen + widgets (main, ~1100 LOC target — v4 larger than v3's ~900 due to Hero panel)
├── state.py           # ComponentEditorState + ComponentUndoEntry
├── autosave.py        # ComponentAutosaveState + load/save/clear helpers
└── constants.py       # EDITABLE_FIELDS, THEME_VALUES, VOCAL_POSTURE_VALUES, SCHEMA_VERSION, DATA_TABLE_COLUMNS, HERO_PRIMARY_FIELDS, HERO_REASONING_FIELDS
```
 
### 2.2 `component_editor/constants.py` — **v4 CHANGED (D1 + D4)**
 
**v4 changes from v3:**
- `DATA_TABLE_COLUMNS` reordered: transition-critical + editable fields
  come FIRST, then audit-context (confidences + reasoning), then timestamps.
- New `HERO_PRIMARY_FIELDS`: tuple of (field_name, display_label, format_spec) tuples
  for the primary row of the Hero panel.
- New `HERO_REASONING_FIELDS`: tuple listing which reasoning fields render
  as italic secondary lines below the primary row.
- New `REASONING_TABLE_TRUNC`: max chars shown in the table's reasoning cells.
- New CSS class names `transition-cluster` / `audit-cluster` / `meta-cluster`
  to give the three column groups distinct visual styling.
 
```python
"""Constants for the Component Metadata editor TUI."""
 
# 4 user-editable columns (subset of song_components). Order matters:
# theme / vocal_posture are enums (cycle with [ / ]).
# groove_density / energy_level are floats (numeric input overlay).
EDITABLE_FIELDS: tuple[str, ...] = (
    "theme",
    "vocal_posture",
    "groove_density",
    "energy_level",
)
 
# The 12-theme vocabulary (must match db/schema.py CHECK constraint).
THEME_VALUES: tuple[str, ...] = (
    "讚美", "感恩", "敬拜", "奉獻", "認罪",
    "差遣", "信心", "祈禱", "復興", "聖靈",
    "十字架", "跟隨",
)
 
# The 3-posture vocabulary (must match db/schema.py CHECK constraint).
VOCAL_POSTURE_VALUES: tuple[str, ...] = (
    "To God", "About God", "To Congregation",
)
 
# Mirror of sow_analysis.storage.cache.COMPONENT_SCHEMA_VERSION
COMPONENT_SCHEMA_VERSION = 2
 
# =============================================================================
# v4: Column order for the DataTable (left → right). Three visual clusters:
#   (1) transition-cluster — role, identification, time, audio-derived
#       metrics, the 4 editable fields, backbeat. These are the values the
#       songset constructor consumes; they must be visible without scrolling.
#   (2) audit-cluster — confidence values + LLM reasoning (truncated in the
#       table; full text in the Hero panel).
#   (3) meta-cluster — created_at / updated_at.
# Contrast with v3 which had the 4 editable fields at positions 21–24
# AFTER every confidence column. v4 prioritises them.
# =============================================================================
DATA_TABLE_COLUMNS: tuple[tuple[str, str, bool, str], ...] = (
    # (key, header_label, editable, cluster)
 
    # ── transition-cluster (cluster 1) ──────────────────────────────────────
    ("role",                 "Role",            False, "transition-cluster"),
    ("component_type",       "Type",            False, "transition-cluster"),
    ("occurrence_index",     "Occ",             False, "transition-cluster"),
    ("start_time",           "Start",           False, "transition-cluster"),
    ("end_time",             "End",             False, "transition-cluster"),
    ("bpm",                  "BPM",             False, "transition-cluster"),
    ("key",                  "Key",             False, "transition-cluster"),
    # Editable fields, grouped together right after.bpm/key for prominence.
    ("theme",                "*Theme",          True,  "transition-cluster"),
    ("vocal_posture",        "*Posture",        True,  "transition-cluster"),
    ("energy_level",         "*Energy",         True,  "transition-cluster"),
    ("groove_density",       "*Groove",         True,  "transition-cluster"),
    ("backbeat_strength",    "Backbeat",        False, "transition-cluster"),
 
    # ── audit-cluster (cluster 2) ───────────────────────────────────────────
    ("confidence",           "Conf",            False, "audit-cluster"),
    ("bpm_confidence",       "BPMc",            False, "audit-cluster"),
    ("key_confidence",       "KEYc",            False, "audit-cluster"),
    ("groove_confidence",    "GRVc",            False, "audit-cluster"),
    ("backbeat_confidence",  "BBc",             False, "audit-cluster"),
    ("energy_confidence",    "ENGc",            False, "audit-cluster"),
    ("theme_confidence",     "THMc",            False, "audit-cluster"),
    ("vocal_posture_confidence", "PSTc",        False, "audit-cluster"),
    # Reasoning rendered DIMMED + TRUNCATED here; full text lives in Hero panel.
    ("theme_reasoning",      "ThemeReason⁂",    False, "audit-cluster"),
    ("posture_reasoning",    "PostureReason⁂",  False, "audit-cluster"),
 
    # ── meta-cluster (cluster 3) ────────────────────────────────────────────
    ("created_at",           "Created",         False, "meta-cluster"),
    ("updated_at",           "Updated",         False, "meta-cluster"),
)
 
# The ⁂ suffix signals to the operator: "the full text is in the Hero panel."
REASONING_TABLE_TRUNC = 40  # chars; v3 had no truncation policy (used cell width).
 
# =============================================================================
# v4 NEW: Hero panel layout. The Hero panel renders five rows:
#   row 1 (Header line, bold, large):
#       "▶ ENTRY CHORUS  —  Occurrence 1  —  [00:23 → 02:15]"
#   row 2 (Primary line, normal):
#       "BPM 96    Key G    Energy -12.0 dB    Groove 0.80    Backbeat 0.42"
#   row 3 (Editable / theme line, accent color):
#       "Theme: 敬拜    Vocal posture: To God"
#   row 4 (Theme reasoning, italic, dimmed):
#       "Theme reasoning: <full text>"
#   row 5 (Posture reasoning, italic, dimmed):
#       "Posture reasoning: <full text>"
# Field order in row 2:
HERO_PRIMARY_FIELDS: tuple[tuple[str, str, str], ...] = (
    # (field_name, display_label, format_spec_or_None)
    ("bpm",               "BPM",     "{:.0f}"),
    ("key",               "Key",     "{}"),
    ("energy_level",      "Energy",  "{:.1f} dB"),
    ("groove_density",    "Groove",  "{:.2f}"),
    ("backbeat_strength", "Backbeat","{:.2f}"),
)
 
HERO_REASONING_FIELDS: tuple[tuple[str, str], ...] = (
    # (field_name, display_label)
    ("theme_reasoning",   "Theme reasoning"),
    ("posture_reasoning", "Posture reasoning"),
)
 
# Float editor input attributes (unchanged from v3).
GROOVE_DENSITY_MIN = 0.0
GROOVE_DENSITY_MAX = 2.0      # no DB CHECK; admin guard only
ENERGY_LEVEL_MIN = -60.0      # dB; admin guard only
ENERGY_LEVEL_MAX = 0.0
 
# Cell-width hint for the truncated reason cells in the DataTable.
REASONING_CELL_WIDTH = REASONING_TABLE_TRUNC + 1  # +1 for the ellipsis char.
```
 
### 2.3 `component_editor/state.py` — **unchanged from v3 (adds one helper)**
 
```python
"""State model for the admin Component Metadata editor.
 
Holds the list of song sessions (one per passed song_id), the current song
index, the entry+exit SongComponent rows for the current song, the dirty /
undo / redo state, and autosave snapshot helpers.
"""
 
from dataclasses import dataclass, field
from typing import Any, Optional
 
from stream_of_worship.admin.db.models import SongComponent
 
_MAX_UNDO = 100
 
 
@dataclass
class ComponentUndoEntry:
    """One reversible field-level edit on a song_components row."""
 
    component_id: int
    component_role: str            # "entry" | "exit"
    field_name: str                # one of EDITABLE_FIELDS
    old_value: Any
    new_value: Any
 
 
@dataclass
class SongSession:
    """Per-song runtime state within the editor."""
 
    song_id: str
    song_title: str
    hash_prefix: str
    audio_path: str
    audio_duration: Optional[float]
    entry_component: Optional[SongComponent]
    exit_component: Optional[SongComponent]
    # Working copy of editable field values: keyed by (role, field) -> value
    working: dict[tuple[str, str], Any] = field(default_factory=dict)
    dirty: bool = False
    r2_save_pending: bool = False
 
    def component_for_role(self, role: str) -> Optional[SongComponent]:
        return self.entry_component if role == "entry" else self.exit_component
 
 
@dataclass
class ComponentEditorState:
    """Top-level mutable state for the Component Metadata editor."""
 
    sessions: list[SongSession]
    current_index: int = 0
    # Keyed by session.song_id (stable PK string), NOT id(session). (C4 fix.)
    _undo_stacks: dict[str, list[ComponentUndoEntry]] = field(default_factory=dict)
    _redo_stacks: dict[str, list[ComponentUndoEntry]] = field(default_factory=dict)
    selected_row: int = 0            # 0 = entry, 1 = exit
    selected_column_key: str = "role"
 
    @property
    def current(self) -> SongSession:
        return self.sessions[self.current_index]
 
    @property
    def current_undo(self) -> list[ComponentUndoEntry]:
        return self._undo_stacks.setdefault(self.current.song_id, [])
 
    @property
    def current_redo(self) -> list[ComponentUndoEntry]:
        return self._redo_stacks.setdefault(self.current.song_id, [])
 
    def push_undo(self, entry: ComponentUndoEntry) -> None:
        stack = self.current_undo
        stack.append(entry)
        if len(stack) > _MAX_UNDO:
            stack.pop(0)
        self.current_redo.clear()
        self.current.dirty = True
 
    def undo(self) -> Optional[ComponentUndoEntry]:
        stack = self.current_undo
        if not stack:
            return None
        entry = stack.pop()
        key = (entry.component_role, entry.field_name)
        self.current.working[key] = entry.old_value
        self.current_redo.append(entry)
        return entry
 
    def redo(self) -> Optional[ComponentUndoEntry]:
        stack = self.current_redo
        if not stack:
            return None
        entry = stack.pop()
        key = (entry.component_role, entry.field_name)
        self.current.working[key] = entry.new_value
        self.current_undo.append(entry)
        self.current.dirty = True
        return entry
 
    def clear_undo_stacks(self, session: SongSession) -> None:
        sid = session.song_id
        self._undo_stacks.get(sid, []).clear()
        self._redo_stacks.get(sid, []).clear()
 
    def get_value(self, role: str, field_name: str) -> Any:
        key = (role, field_name)
        if key in self.current.working:
            return self.current.working[key]
        comp = self.current.component_for_role(role)
        if comp is None:
            return None
        return getattr(comp, field_name)
 
    def set_value(self, role: str, field_name: str, value: Any) -> None:
        comp = self.current.component_for_role(role)
        if comp is None:
            return
        old = self.get_value(role, field_name)
        if old == value:
            return
        self.push_undo(ComponentUndoEntry(
            component_id=comp.id or 0,
            component_role=role,
            field_name=field_name,
            old_value=old,
            new_value=value,
        ))
        self.current.working[(role, field_name)] = value
 
    # ── v4 NEW: hero-panel helper ─────────────────────────────────────────
    def get_selected_component(self) -> Optional[SongComponent]:
        """Return the SongComponent that the currently-highlighted table row
        points at (entry if selected_row == 0 else exit)."""
        role = "entry" if self.selected_row == 0 else "exit"
        return self.current.component_for_role(role)
```
 
### 2.4 `component_editor/autosave.py` — **unchanged from v3**
 
```python
"""Autosave recovery for the Component Metadata editor.
 
One file per song at {cache_dir}/{hash_prefix}/components/components.autosave.json.
"""
 
import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional
 
logger = logging.getLogger(__name__)
 
AUTOSAVE_FILENAME = "components.autosave.json"
 
 
@dataclass
class ComponentAutosaveState:
    song_id: str
    hash_prefix: str
    working: list[dict[str, Any]] = field(default_factory=list)
    dirty: bool = False
    selected_row: int = 0
    selected_column_key: str = "role"
    r2_save_pending: bool = False
 
    def to_dict(self) -> dict:
        return {
            "song_id": self.song_id,
            "hash_prefix": self.hash_prefix,
            "working": self.working,
            "dirty": self.dirty,
            "selected_row": self.selected_row,
            "selected_column_key": self.selected_column_key,
            "r2_save_pending": self.r2_save_pending,
        }
 
    @classmethod
    def from_dict(cls, data: dict) -> "ComponentAutosaveState":
        return cls(
            song_id=data["song_id"],
            hash_prefix=data["hash_prefix"],
            working=data.get("working", []),
            dirty=data.get("dirty", False),
            selected_row=data.get("selected_row", 0),
            selected_column_key=data.get("selected_column_key", "role"),
            r2_save_pending=data.get("r2_save_pending", False),
        )
 
 
def get_autosave_path(cache_dir: Path, hash_prefix: str) -> Path:
    return cache_dir / hash_prefix / "components" / AUTOSAVE_FILENAME
 
 
def load_autosave(cache_dir: Path, hash_prefix: str) -> Optional[ComponentAutosaveState]:
    path = get_autosave_path(cache_dir, hash_prefix)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return ComponentAutosaveState.from_dict(data)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Corrupt autosave at %s: %s", path, e)
        return None
 
 
def save_autosave(cache_dir: Path, snapshot: ComponentAutosaveState) -> bool:
    path = get_autosave_path(cache_dir, snapshot.hash_prefix)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd, tmp_path = tempfile.mkstemp(
            prefix=".autosave-", suffix=".json", dir=str(path.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(snapshot.to_dict(), f, ensure_ascii=False)
            os.replace(tmp_path, path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        return True
    except OSError as e:
        logger.warning("Failed to write autosave at %s: %s", path, e)
        return False
 
 
def clear_autosave(cache_dir: Path, hash_prefix: str) -> None:
    path = get_autosave_path(cache_dir, hash_prefix)
    try:
        path.unlink(missing_ok=True)
    except OSError as e:
        logger.warning("Failed to clear autosave at %s: %s", path, e)
```
 
### 2.5 `_maybe_apply_autosave` recovery dialog — **unchanged from v3**
 
When `load_autosave(cache_dir, session.hash_prefix)` returns a snapshot whose
`dirty=True` OR `r2_save_pending=True`:
 
- If `r2_save_pending=True`: recovery banner is shown as
  *"Recovered N edits — DB committed but R2 still pending — press `s` to retry"*.
  Apply working edits to state, set `session.dirty=True` and
  `session.r2_save_pending=True`.
- Else (normal dirty recovery): standard LRC-editor-style
  `AutosaveRecoveryDialog` with "r" recover / "d" discard.
 
After applying recovered edits in either branch, call `self._refresh_hero()`
(v4 NEW) so the panel reflects the restored state.
 
---
 
## Phase 3: TUI app + screen + playback — **v4 CHANGED (D2 + D3)**
 
**Goal:** Build the Textual UI; reuse `PlaybackService` and the LRC editor's
`GroupedFooter` / `PlaybackBar` / `StatusIndicator` patterns. **v4 adds a new
`ComponentHeroPanel` widget above the DataTable and changes the `space` action
semantics to seek-to-component-start before playing.**
**Complexity:** L
 
### 3.1 `component_editor/app.py` — **unchanged from v3**
 
```python
"""Textual app for the admin Component Metadata editor."""
 
import textual.constants
textual.constants.DISABLE_KITTY_KEY = True
 
from textual.app import App
from stream_of_worship.admin.component_editor.screen import ComponentEditorScreen
from stream_of_worship.admin.component_editor.state import ComponentEditorState
from stream_of_worship.admin.services.playback import PlaybackService
 
 
class ComponentEditorApp(App[None]):
    TITLE = "Component Metadata Editor"
    CSS = """
    Screen { layout: vertical; }
    """
 
    def __init__(
        self,
        editor_state: ComponentEditorState,
        playback_service: PlaybackService,
        cache_dir,
        r2_client,
        db_client,
    ):
        super().__init__()
        self.editor_state = editor_state
        self.playback_service = playback_service
        self.cache_dir = cache_dir
        self.r2_client = r2_client
        self.db_client = db_client
 
    def on_mount(self) -> None:
        self.push_screen(ComponentEditorScreen(
            editor_state=self.editor_state,
            playback_service=self.playback_service,
            cache_dir=self.cache_dir,
            r2_client=self.r2_client,
            db_client=self.db_client,
        ))
```
 
### 3.2 `component_editor/screen.py` — **v4 CHANGED to add ComponentHeroPanel**
 
The screen composes (bottom-up, mirroring LRC editor at
`editor/screen.py:1-308`):
 
| Widget | Mirrored from | Purpose |
|---|---|---|
| `Header()` | textual.widgets.Header | Top Textual bar |
| `SongBreadcrumb` (new) | — | "● Song 2 / 5  —  [abc123] 主禱文  —  hash_prefix=abc123def4567" |
| `PlaybackBar` (new, near-verbatim copy of LRC editor's `PlaybackBar`) | `editor/screen.py:60-98` | ▶ [00:23 / 03:45] progress bar |
| **`ComponentHeroPanel` (NEW, v4 D2)** | new | Always-visible summary of the **highlighted** component's transition-critical fields + LLM reasoning (italic). Updates on cursor-row move and on every edit. |
| `ComponentMetadataTable` (new `DataTable` subclass) | `editor/screen.py`'s `LyricLineTable` pattern | 2 rows × reordered columns (D1). |
| `StatusIndicator` (new, near-verbatim copy) | `editor/screen.py:116-145` | `dirty` (`*`/`✓`), `autosave` (✓/—), current song index badge |
| `GroupedFooter` (REUSED) | `editor/footer.py` | Reads `BINDINGS` + `BINDING_GROUPS` from the screen, renders clusters |
 
For numeric input overlay, copy the LRC editor's overlay
`Input(id="value-edit-input")` + `_show_row_edit_input` machinery verbatim with
simplifications (no padding/quanter business).
 
#### 3.2.1 `ComponentHeroPanel` widget specification (v4 NEW)
 
The `ComponentHeroPanel` is a `Static`-style read-only container that renders
five rows of rich text for the **currently-highlighted component** (entry or
exit Chorus). It refreshes whenever:
 
1. The DataTable cursor moves between rows (entry ↔ exit).
2. The user switches songs (`n` / `p`).
3. The user edits a field (`[`, `]`, `e`-overlay submit).
4. The user undoes / redoes (`ctrl+z` / `ctrl+y`).
5. The user saves successfully (`s`) — refreshed to the persisted values.
 
Layout (rendered via a `Rich` `Text` assembled inside an `update(...)` call):
 
```
┌─ ▶ ENTRY CHORUS  —  Occurrence 1  —  [00:23 → 02:15] ───────────────┐
│  BPM 96    Key G    Energy -12.0 dB    Groove 0.80    Backbeat 0.42  │
│  Theme: 敬拜    Vocal posture: To God                                 │
│                                                                      │
│  Theme reasoning: 主歌中提到受造之物齐声赞美造物主，主题应为赞美类。   │
│  Posture reasoning: 第二人称开头称呼神，语气为对神的祈求。            │
└──────────────────────────────────────────────────────────────────────┘
```
 
CSS / styling notes:
- The panel header (row 1, "▶ ENTRY CHORUS …") uses **bold** styling on the
  role + a distinct color for the entry role vs exit role (e.g. entry = cyan,
  exit = magenta) so the operator can tell at-a-glance which row is focused.
- Row 2 (primary transition metrics) uses **default** styling.
- Row 3 (editable Theme + Vocal posture) uses an **accent** color (e.g.
  yellow) to draw the eye to values the operator may want to override.
- Rows 4–5 (reasoning) are **italic + dimmed** (`dim=True`). They are the
  LLM's audit rationale and are NOT editable; their purpose is to let the
  operator quickly sanity-check the LLM's classification against the lyric.
- If a reasoning field is `None` / empty: render the row as
  `Theme reasoning: — (LLM did not supply reasoning)` in italic, dimmed.
- If `entry_component is None` (partial-analysis case): the panel renders
  a single dimmed line: `"No entry Chorus component — run sow-admin audio
  components <song_id> first"` and no metric rows.
 
Pseudocode:
 
```python
class ComponentHeroPanel(Static):
    """v4 NEW. Always-visible summary of the highlighted component's
    transition-critical fields + LLM reasoning. Renders Rich Text refreshed
    on cursor move, song switch, edit, undo/redo, and save.
    """
 
    DEFAULT_CSS = """
    ComponentHeroPanel {
        border: round $accent;
        padding: 0 1;
        height: auto;
        margin: 0 0 0 0;
    }
    ComponentHeroPanel .hero-header-entry { color: $text; text-style: bold; background: $boost; }
    ComponentHeroPanel .hero-header-exit  { color: $text; text-style: bold; background: $boost; }
    ComponentHeroPanel .hero-primary      { color: $text; }
    ComponentHeroPanel .hero-editable     { color: $warning; text-style: bold; }
    ComponentHeroPanel .hero-reasoning    { color: $text-muted; text-style: italic; }
    ComponentHeroPanel .hero-empty        { color: $text-muted; text-style: italic; }
    """
 
    def render_panel(self, state: ComponentEditorState) -> None:
        session = state.current
        comp = state.get_selected_component()
        role = "entry" if state.selected_row == 0 else "exit"
 
        if comp is None:
            self.update(
                Text(f"No {role} Chorus component — run `sow-admin audio "
                     f"components {session.song_id}` first",
                     style="hero-empty")
            )
            return
 
        from rich.text import Text
        t = Text()
        # Row 1: header.
        header_cls = "hero-header-entry" if role == "entry" else "hero-header-exit"
        t.append(f"▶ {role.upper()} CHORUS", style=header_cls)
        t.append(f"  —  Occurrence {comp.occurrence_index}  —  "
                 f"[{_fmt_time(comp.start_time)} → {_fmt_time(comp.end_time)}]",
                 style=header_cls)
        t.append("\n")
 
        # Row 2: primary transition metrics.
        primary_parts = []
        for field_name, label, fmt in HERO_PRIMARY_FIELDS:
            v = state.get_value(role, field_name)
            if v is None:
                txt = "—"
            elif fmt:
                try:
                    txt = fmt.format(v)
                except (TypeError, ValueError):
                    txt = str(v)
            else:
                txt = str(v)
            primary_parts.append(f"{label} {txt}")
        t.append("    " + "    ".join(primary_parts), style="hero-primary")
        t.append("\n")
 
        # Row 3: editable theme + vocal_posture (accent).
        theme_v = state.get_value(role, "theme") or "—"
        posture_v = state.get_value(role, "vocal_posture") or "—"
        t.append(f"    Theme: {theme_v}    Vocal posture: {posture_v}",
                 style="hero-editable")
        t.append("\n\n")
 
        # Rows 4–5: reasoning (italic, dimmed, full text — no truncation).
        for field_name, label in HERO_REASONING_FIELDS:
            v = getattr(comp, field_name, None)
            if not v:
                t.append(f"    {label}: — (LLM did not supply reasoning)\n",
                         style="hero-empty")
            else:
                t.append(f"    {label}: {v}\n", style="hero-reasoning")
 
        self.update(t)
 
 
def _fmt_time(seconds: float) -> str:
    if seconds is None:
        return "--:--"
    m = int(seconds) // 60
    s = int(seconds) % 60
    return f"{m:02d}:{s:02d}"
```
 
#### 3.2.2 `ComponentMetadataTable` cursor behaviour
 
Subclass Textual's `DataTable`. Override `on_data_table_cursor_changed` to:
1. Update `state.selected_row` to the new row index (0 or 1).
2. Call `screen._refresh_hero()`.
3. Call `screen._update_status()` (existing behaviour — cheap).
 
Cursor-column changes do NOT require a hero refresh (the hero only depends
on which component row is highlighted, not which column).
 
For the reasoning columns: render the cell text via a helper that truncates
to `REASONING_TABLE_TRUNC` chars + `…` if longer. Apply the `dim=True`
style so the table stays visually quiet on these columns; their full text
lives in the Hero panel.
 
### 3.3 `on_mount` flow — **v4 CHANGED (refresh hero on mount)**
 
```python
def on_mount(self) -> None:
    self._load_audio_for_current_song()
    self.playback.set_callbacks(
        on_position_changed=self._on_playback_position,
        on_state_changed=self._on_playback_state,
        on_finished=self._on_playback_finished,
    )
    self._position_update_timer = self.set_interval(
        0.2, self._update_playback_bar
    )
    self._maybe_apply_autosave()
    self._refresh_table()
    self._refresh_hero()  # v4 NEW
 
 
def _load_audio_for_current_song(self) -> None:
    session = self.state.current
    if not session.audio_path:
        return
    self.playback.load(Path(session.audio_path))
 
 
def _refresh_hero(self) -> None:
    """v4 NEW. Re-renders the ComponentHeroPanel against the current state."""
    self.hero_panel.render_panel(self.state)
```
 
### 3.4 Song switch — **v4 CHANGED (refresh hero after table refresh)**
 
```python
def _switch_song(self, delta: int) -> None:
    if self.state.current.dirty and not self._do_autosave():
        self.app.bell()
        return
    new_idx = (self.state.current_index + delta) % len(self.state.sessions)
    if new_idx == self.state.current_index:
        return
    self.state.current_index = new_idx
    self.playback.stop()
    self._load_audio_for_current_song()
    self._refresh_table()
    self._refresh_hero()  # v4 NEW
    self._update_breadcrumb()
    self._update_status()
```
 
---
 
## Phase 4: Bindings — **v4 CHANGED (D3 renames toggle_playback)**
 
**Goal:** Mirror LRC editor's binding style. The only binding action name
change in v4: `toggle_playback` → `toggle_playback_for_component` (the
binding's user-facing label `Play/Pause` is unchanged).
**Complexity:** M
 
### 4.1 `ComponentEditorScreen.BINDINGS`
 
```python
from textual.binding import Binding
 
class ComponentEditorScreen(Screen[None]):
    BINDINGS = [
        Binding("space",  "toggle_playback_for_component", "Play/Pause"),  # v4: action renamed
        Binding("left",   "seek_backward",    "Seek -5s"),
        Binding("right",  "seek_forward",     "Seek +5s"),
        Binding("j",       "jump_to_component", "Jump"),
        Binding("n",       "next_song",        "Next Song"),
        Binding("p",       "prev_song",        "Prev Song"),
        Binding("bracketleft",  "cycle_field_prev",  "Cycle −"),  # '['
        Binding("bracketright", "cycle_field_next",  "Cycle +"),  # ']'
        Binding("e",            "edit_numeric",      "Edit Num"),
        Binding("s",            "save",              "Save"),
        Binding("ctrl+z",       "undo",              "Undo"),
        Binding("ctrl+y",       "redo",              "Redo"),
        Binding("escape",       "quit_editor",       "Quit"),
        Binding("q",            "quit_editor",       "Quit"),
        Binding("?",            "show_keymap",       "Keymap"),
    ]
 
    BINDING_GROUPS: dict[str, list[str]] = {
        "Playback": ["toggle_playback_for_component", "seek_backward", "seek_forward", "jump_to_component"],
        "Songs":     ["next_song", "prev_song"],
        "Edit":      ["cycle_field_prev", "cycle_field_next", "edit_numeric"],
        "General":   ["save", "undo", "redo", "quit_editor", "show_keymap"],
    }
```
 
### 4.2 Hot-key → action map (summary table)
 
| Key | Action | Description |
|---|---|---|
| `space` | `toggle_playback_for_component` | **v4 CHANGED.** If playing → pause. If paused → seek to highlighted component's `start_time` and start playing. Plays through the song naturally from there. |
| `left` / `right` | `seek_backward` / `seek_forward` | Seek ±5s |
| `j` | `jump_to_component` | Seek ONLY to `start_time` of selected component. No auto-play — matches v3's seek-to-start convention. |
| `n` / `p` | `next_song` / `prev_song` | Switch to next / prev song (autosave current first; hero panel refreshes after) |
| `up`/`down`/`pageup`/`pagedown` | (DataTable cursor) | Row navigation; max 2 rows. Each row move triggers a Hero panel refresh. |
| `tab`/`shift+tab` | `cursor_right`/`cursor_left` | Column navigation (does NOT need Hero refresh — only row moves do) |
| `[` / `]` | `cycle_field_prev` / `cycle_field_next` | Cycle enum value on the highlighted theme or vocal_posture cell (ignored for non-enum cells; hero refreshes after) |
| `e` | `edit_numeric` | Open numeric input overlay for the highlighted groove_density or energy_level cell (ignored for non-numeric cells; hero refreshes after submit) |
| `s` | `save` | Commit dirty edits on current song to DB + R2; on success, hero refreshes to the persisted values |
| `ctrl+z` / `ctrl+y` | `undo` / `redo` | Per-song undo / redo (max 100). Hero refreshes after. |
| `escape` / `q` | `quit_editor` | Push QuitConfirmDialog if dirty; otherwise `app.exit()`. First `escape` while value-edit overlay open cancels the overlay only. |
| `?` | `show_keymap` | Modal screen listing all bindings |
 
### 4.3 Conflict note: `left` / `right`
 
`left/right` are seek hotkeys (v3 convention preserved). Cell cursor movement
across columns is achieved via `tab` / `shift+tab`:
 
```python
Binding("tab",       "cursor_right", "Col →"),
Binding("shift+tab",  "cursor_left",  "Col ←"),
```
 
(If Textual refuses to bind `tab` due to focus traversal, fall back to `}` /
`{` keys for column nav.)
 
---
 
## Phase 5: Playback action — **v4 NEW (split out from v3's edit-UX phase)**
 
**Goal:** Specify the new `space` semantics (D3) crisply.
**Complexity:** S
 
### 5.1 `action_toggle_playback_for_component` — **v4 CHANGED (D3)**
 
```python
def action_toggle_playback_for_component(self) -> None:
    """v4. Play or pause the song, anchored to the highlighted component.
 
    Semantics (resolves operator clarification "when I hit space for
    playback, it should start playing the currently highlighted component"):
 
      • If the playback service is currently PLAYING:
            → Pause. Resume position is preserved by the playback service.
      • If the playback service is PAUSED or STOPPED:
            1. Determine the highlighted component (entry if
               state.selected_row == 0 else exit).
            2. Determine the seek target:
               a. If a component is highlighted AND the current playback
                  position is OUTSIDE the component's [start_time, end_time]
                  range: seek to comp.start_time.
                  (Rationale: the operator just moved the cursor onto this
                  row; they expect `space` to start playing THIS component.)
               b. If a component is highlighted AND the current playback
                  position is INSIDE the component's [start_time, end_time]
                  range: resume from the current position (no seek).
                  (Rationale: pause/resume within the same component should
                  not restart from the component's beginning.)
               c. If no component is highlighted (both rows None): just call
                  playback.play() — best-effort.
            3. Call playback.play().
    """
    if self._guard_active_edit():
        return
 
    if self.playback.is_playing:
        self.playback.pause()
        return
 
    comp = self.state.get_selected_component()
    if comp is not None:
        pos = self.playback.position or 0.0
        inside = (comp.start_time or 0.0) <= pos <= (comp.end_time or float("inf"))
        if not inside:
            self.playback.seek(comp.start_time or 0.0)
    self.playback.play()
```
 
### 5.2 `action_jump_to_component` — **unchanged from v3 (seek only, no auto-play)**
 
```python
def action_jump_to_component(self) -> None:
    if self._guard_active_edit():
        return
    comp = self.state.get_selected_component()
    if comp is None or comp.start_time is None:
        self.app.bell()
        return
    self.playback.seek(comp.start_time)
    self._update_playback_bar()
```
 
### 5.3 Hero panel refresh after edits — **v4 NEW**
 
Every state mutation in the edit-UX actions (`cycle_field_*`, `edit_numeric`
submit, `undo`, `redo`) MUST end with `self._refresh_hero()`. The Hero panel
reads from `state.get_value(role, field_name)` which honours
`session.working` overrides, so unsaved edits are visible in the panel
immediately.
 
```python
def action_cycle_field_next(self) -> None:
    # ... (v3 unchanged logic: validate field is enum, lookup, set_value)
    self.state.set_value(role, field, new_value)
    self._do_autosave()
    self._refresh_table()
    self._refresh_hero()  # v4 NEW
```
 
### 5.4 Guard behaviour
 
Mirroring LRC editor's `_guard_active_edit()`:
- Cycle, numeric-edit, and playback actions are blocked when a value-edit
  overlay is already open. First `escape` cancels the overlay (does not quit).
- When viewing a song whose entry or exit component is `None` (missing
  component analysis), playback actions no-op and ring the bell (or, per
  §5.1 case 2c, fall through to `playback.play()` without seeking).
 
### 5.5 Numeric edit action (v4 carries over v3 unchanged)
 
```python
def action_edit_numeric(self) -> None:
    field = self.state.selected_column_key
    if field not in ("groove_density", "energy_level"):
        return
    role = self._selected_role()
    current = self.state.get_value(role, field)
    self._show_value_edit_input(
        role=role, field=field,
        initial_text="" if current is None else f"{current:.4g}",
        validator=self._validate_numeric_field,
    )
 
def _validate_numeric_field(self, field: str, text: str) -> Optional[float]:
    try:
        val = float(text.strip())
    except ValueError:
        return None
    if field == "groove_density":
        if not (GROOVE_DENSITY_MIN <= val <= GROOVE_DENSITY_MAX):
            return None
    elif field == "energy_level":
        if not (ENERGY_LEVEL_MIN <= val <= ENERGY_LEVEL_MAX):
            return None
    return val
```
 
On submit (`on_input_submitted`): if validation passes → `state.set_value`
+ push undo + autosave + refresh cell + **`self._refresh_hero()` (v4 NEW)** +
remove overlay; else: bell + keep overlay open.
 
---
 
## Phase 6: Save flow — **v4 unchanged from v3 (adds hero refresh)**
 
**Goal:** Commit dirty edits on the current song to DB + R2, with a
reliable partial-failure path. v4 does not alter this flow's transactional
machinery; the only additions are `_refresh_hero()` calls so the panel
shows the post-save state.
**Complexity:** M
 
### 6.1 `action_save`
 
```python
def action_save(self) -> None:
    session = self.state.current
    if not session.dirty:
        self.app.bell()
        return
 
    # 1. Collect dirty edits grouped by component (entry / exit).
    updates_by_role: dict[str, dict[str, Any]] = {"entry": {}, "exit": {}}
    for (role, field), value in session.working.items():
        updates_by_role[role][field] = value
 
    # 2. Write DB (targeted UPDATE per component, single transaction).
    try:
        with self.db_client.transaction() as conn:
            for role, fields in updates_by_role.items():
                comp = session.component_for_role(role)
                if comp is None or comp.id is None or not fields:
                    continue
                self.db_client.update_song_component_fields_txn(
                    conn, comp.id, fields
                )
    except Exception as e:
        self._notify(f"[red]DB save failed: {e}[/]")
        return
 
    # 3. Write R2 components.json (merge).
    r2_ok = self._save_r2_component_result(session, updates_by_role)
 
    if not r2_ok:
        session.r2_save_pending = True
        self._do_autosave()
        self._update_status()
        self._refresh_table()
        self._notify("[yellow]Saved DB only — R2 failed — press s to retry.[/]")
        self._refresh_hero()  # v4 NEW
        return
 
    # 5. Full success → clear everything.
    session.working.clear()
    session.dirty = False
    session.r2_save_pending = False
    self._reload_components_from_db(session)
    self.state.clear_undo_stacks(session)
    clear_autosave(self.cache_dir, session.hash_prefix)
    self._update_status()
    self._refresh_table()
    self._refresh_hero()  # v4 NEW
    self._notify("[green]Saved (DB + R2).[/]")
```
 
### 6.2 `_save_r2_component_result`
 
```python
def _save_r2_component_result(
    self, session: SongSession, updates_by_role: dict[str, dict[str, Any]]
) -> bool:
    hash_prefix = session.hash_prefix
    try:
        payload = self.r2_client.download_component_result(hash_prefix)
    except Exception as e:
        self._notify(f"[yellow]R2 download failed: {e}[/]")
        return False
 
    if payload is None:
        payload = {
            "schema_version": COMPONENT_SCHEMA_VERSION,
            "content_hash": first_content_hash(session),
            "hash_prefix": hash_prefix,
            "component_source": "user_review_components",
            "components": [],
        }
        for role in ("entry", "exit"):
            comp = session.component_for_role(role)
            if comp is None:
                continue
            payload["components"].append(comp.to_dict())
 
    components = payload.get("components", [])
    for comp_dict in components:
        role = comp_dict.get("role")
        if role not in ("entry", "exit"):
            continue
        fields = updates_by_role.get(role, {})
        for field, value in fields.items():
            comp_dict[field] = value
 
    try:
        self.r2_client.upload_component_result(hash_prefix, payload)
    except Exception as e:
        self._notify(f"[yellow]R2 upload failed: {e}[/]")
        return False
 
    return True
 
 
def first_content_hash(session: SongSession) -> str:
    if session.entry_component is not None:
        return session.entry_component.content_hash or ""
    if session.exit_component is not None:
        return session.exit_component.content_hash or ""
    return ""
```
 
### 6.3 Stale-revision guard (SOFT warning)
 
```python
existing_hash = payload.get("content_hash") if isinstance(payload, dict) else None
if existing_hash and existing_hash != first_content_hash(session):
    logger.warning(
        "R2 components.json content_hash=%s mismatches recording content_hash=%s "
        "for hash_prefix=%s; saving with merged values regardless.",
        existing_hash, first_content_hash(session), hash_prefix,
    )
```
 
### 6.4 `_reload_components_from_db`
 
```python
def _reload_components_from_db(self, session: SongSession) -> None:
    entry, exit_comp = self.db_client.get_song_components_entry_exit(
        session.song_id
    )
    session.entry_component = entry
    session.exit_component = exit_comp
```
 
---
 
## Phase 7: Autosave & undo/redo loop
 
**v4 status:** Autosave unchanged from v3; undo/redo action bodies add a
`self._refresh_hero()` call (D2 wiring).
**Complexity:** S
 
### 7.1 `_do_autosave`
 
```python
def _do_autosave(self) -> bool:
    session = self.state.current
    snapshot = ComponentAutosaveState(
        song_id=session.song_id,
        hash_prefix=session.hash_prefix,
        working=[
            {"role": role, "field": field, "value": value}
            for (role, field), value in session.working.items()
        ],
        dirty=session.dirty,
        selected_row=self.state.selected_row,
        selected_column_key=self.state.selected_column_key,
        r2_save_pending=session.r2_save_pending,
    )
    ok = save_autosave(self.cache_dir, snapshot)
    self._autosave_ok = ok
    self._update_status()
    return ok
```
 
### 7.2 `_maybe_apply_autosave` (on mount)
 
See Phase 2.5 for the `r2_save_pending=True` recovery banner. After
applying recovered edits in either branch, call `self._refresh_hero()` so
the panel reflects the restored state.
 
### 7.3 Autosave triggers
 
Call `_do_autosave()` after every state mutation:
- `action_cycle_field_prev` / `action_cycle_field_next` (+ `_refresh_hero()`)
- `on_input_submitted` (numeric edit) (+ `_refresh_hero()`)
- `action_undo` / `action_redo` (+ `_refresh_hero()`)
 
Call `clear_autosave` only inside `action_save` **on full success**.
 
### 7.4 Undo / redo wiring — **v4 CHANGED to refresh hero**
 
```python
def action_undo(self) -> None:
    entry = self.state.undo()
    if entry is None:
        self.app.bell()
        return
    self._do_autosave()
    self._refresh_table()
    self._refresh_hero()  # v4 NEW
 
def action_redo(self) -> None:
    entry = self.state.redo()
    if entry is None:
        self.app.bell()
        return
    self._do_autosave()
    self._refresh_table()
    self._refresh_hero()  # v4 NEW
```
 
On `action_save` (Phase 6.1 step 5): `state.clear_undo_stacks(session)` is
invoked on **full success only**.
 
---
 
## Phase 8: Quit / dialog flow
 
**v4 status:** Unchanged from v3.
**Complexity:** S
 
- `q` / `escape`: if value-edit overlay open → cancel overlay (no quit). Else
  if current session dirty → push `QuitConfirmDialog` (autosave first). Else
  `self.app.exit()`.
- `?`: push `KeymapDialog` — modal screen reading the same `BINDINGS` list.
  Copy the LRC editor's `KeymapDialog` verbatim.
- `QuitConfirmDialog`: `y` = exit (autosave already written), `n`/`escape` =
  cancel. Identical to LRC editor's.
 
---
 
## Phase 9: Tests
 
**Goal:** Mirror the LRC editor test surface, plus regression tests for every
v2 fix AND new v4 hero-panel + playback-semantic tests.
**Complexity:** M
 
New test files:
 
| File | Coverage |
|---|---|
| `tests/admin/component_editor/test_state.py` | `ComponentEditorState.set_value/undo/redo/push_undo` (>100) / multi-session independence; `SongSession.dirty` propagation; `clear_undo_stacks`; undo/redo keyed by `song_id` (regression for C4). **v4 NEW:** `get_selected_component()` returns `entry_component` when `selected_row=0`, `exit_component` when `selected_row=1`, `None` when partial analysis. |
| `tests/admin/component_editor/test_autosave.py` | `save_autosave` ↔ `load_autosave` round-trip; corrupt file → None; `clear_autosave` no-op safety; `r2_save_pending` survives a to_dict/from_dict round-trip (regression for B1). |
| `tests/admin/component_editor/test_constants.py` | **v4 NEW.** Asserts `DATA_TABLE_COLUMNS` ordering: first 11 entries are the transition-cluster (role, type, occ, start, end, bpm, key, *theme, *posture, *energy, *groove), followed by backbeat. Asserts every editable field has cluster = `transition-cluster`. Asserts `HERO_PRIMARY_FIELDS` includes `bpm, key, energy_level, groove_density, backbeat_strength` and NOT `theme`. Asserts `HERO_REASONING_FIELDS` is `(theme_reasoning, posture_reasoning)`. |
| `tests/admin/component_editor/test_hero_panel.py` | **v4 NEW.** `ComponentHeroPanel.render_panel(state)`: (a) `selected_row=0` → header contains `ENTRY`; `selected_row=1` → header contains `EXIT`; (b) `entry_component` is None → panel renders the "missing" message; (c) primary row contains the BPM value formatted as int (e.g. `BPM 96`); (d) editable row shows theme and vocal_posture values; (e) reasoning row renders the full LLM text when present, and the `(LLM did not supply reasoning)` placeholder when None; (f) after `state.set_value(role, "theme", "敬拜")` + `_refresh_hero()`, the panel's editable row shows `Theme: 敬拜`. Use Textual Pilot. |
| `tests/admin/component_editor/test_screen.py` | Textual Pilot: launch with a fake R2Client + DatabaseClient (monkeypatch) and 3 mocked SongSessions; cycling `[` / `]` changes theme and Hero panel updates; `e` overlay accepts numeric and Hero panel updates; `s` saves → DB target UPDATE called + R2 upload called + autosave cleared + Hero panel refreshes to persisted values; `n` switches song + reloads `PlaybackService.load` + Hero panel refreshes; `q` with dirty pushes confirm dialog. Plus the v2 regression suite below. |
| `tests/admin/test_audio_commands.py` additions | `review-components` rejects unknown song_id; warns + skips song with no entry/exit components; launches app if ≥ 1 valid |
| `tests/admin/services/test_r2_component_result.py` | `R2Client.download_component_result` 404 → None; happy path → parsed dict; `upload_component_result` calls `put_object` with the right key + body + content_type; payload round-trip equality |
| `tests/admin/test_db_client.py` additions | `update_song_component_fields(component_id, {"theme": "敬拜"})` updates 1 row; rejects unknown字段; `update_song_component_fields_txn` existing-conn variant: same validation behaviour; committed in caller's transaction. |
 
### v4 playback-semantic tests (in `test_screen.py`)
 
| Test | Description |
|---|---|
| **D3 regression 1** | Playback paused, cursor on ENTRY row, current position = 0.0 (outside `[start, end]`). Press `space`. Assert `playback.seek(entry.start_time)` was called THEN `playback.play()` was called. |
| **D3 regression 2** | Playback paused, cursor on ENTRY row, current position = inside `[start, end]`. Press `space`. Assert `playback.seek` NOT called; `playback.play()` called. |
| **D3 regression 3** | Playback playing. Press `space`. Assert `playback.pause()` called; `playback.play()` NOT called. |
| **D3 regression 4** | Both components None (partial analysis). Press `space`. Assert `playback.play()` called WITHOUT a preceding `seek` (per §5.1 case 2c). |
| **D2 regression 1** | Cursor move from ENTRY row to EXIT row (via `down` key). Assert `hero_panel.render_panel` was called twice (once on mount, once on cursor move); on the second call the panel header contains `EXIT`. |
| **D2 regression 2** | After `action_cycle_field_next` changes `theme` to `感恩`, the hero panel's editable row text contains `Theme: 感恩`. |
| **D1 regression** | Inspect `DataTable` column order: first column key = `role`, then `component_type`, then in positions 7–11 the four editable fields. Assert no column key `confidence` appears before any editable column. |
 
### v2 regression test suite (in `test_screen.py` — unchanged)
 
| Test | Description |
|---|---|
| **B1 regression** | save with DB ok + R2 upload raising → asserts `session.dirty` is `True`, `session.working` is untouched, autosave file still exists with `r2_save_pending=True`, status shows retry message, undo stacks NOT cleared. **v4 add:** hero panel shows the edited value. |
| **B2 regression** | save first-time R2 payload when `session.entry_component=None` and `session.exit_component` is set → asserts no `AttributeError`, payload `content_hash` is the exit component's hash. |
| **C1 regression (download)** | `download_component_result` raising `ClientError` → save returns False, DB committed (already), state preserved for retry. |
| **C2 regression** | assert `action_save` calls `db_client.update_song_component_fields_txn` (not inline SQL). |
| **C3 regression** | after full-success save, `session.entry_component` and `session.exit_component` are freshly-fetched instances (different `id()`) whose `theme` field matches the saved value. **v4 add:** hero panel reflects the persisted value. |
| **B3 regression** | after full-success save, `ctrl+z` rings the bell (undo stack empty) and does not re-dirty the session. |
 
Use `unittest.mock` for `PlaybackService` in screen tests.
 
---
 
## Verification matrix (issues → fixes)
 
### v2 fixes (carried from v3)
 
| Issue | Severity | v4 Section | Resolution |
|---|---|---|---|
| B1 (R2 failure loses edits) | HIGH | Phase 6.1 step 4 + `r2_save_pending` flag | Keep `working` / `dirty` / autosave / undo stacks on R2 failure. |
| B2 (`content_hash` AttributeError on None) | HIGH | Phase 6.2 `first_content_hash` | None-safe picker. |
| B3 (undo/redo not cleared) | LOW | Phase 6.1 step 5 + `clear_undo_stacks` | Cleared on full success. |
| C1 (R2 download exception uncaught) | HIGH | Phase 6.2 (try/except wrap) | Caught; returns False. |
| C2 (inline UPDATE bypasses whitelist) | HIGH | Phase 0.1.2 + Phase 6.1 step 2 | `update_song_component_fields_txn` enforces ALLOWED. |
| C3 (`_reload_components_from_db` undefined) | HIGH | Phase 6.4 | Explicit spec using `db.get_song_components_entry_exit`. |
| C4 (id()-keyed undo stacks) | MED | Phase 2.3 | Keyed by `session.song_id`. |
| C5 (stale-revision guard absent) | LOW | Phase 6.3 | Soft warning on `content_hash` mismatch. |
 
### v4 fixes (the transitions-prominence deltas)
 
| Issue | Severity | v4 Section | Resolution |
|---|---|---|---|
| D1 (transition-critical columns buried at positions 21–24) | HIGH | Phase 2.2 `DATA_TABLE_COLUMNS` reorder + Phase 9 D1 regression test | Reorder columns: transition-critical + editable fields come first, in `transition-cluster` styling. |
| D2 (no prominent summary surface for at-a-glance review) | HIGH | Phase 3.2.1 `ComponentHeroPanel` spec + Phase 3.3/3.4 `_refresh_hero()` calls + Phase 9 D2 tests | Always-visible Hero panel above the DataTable; updates on cursor move, song switch, edit, undo/redo, save. |
| D3 (`space` plays wherever head happens to be, not where cursor points) | MED | Phase 5.1 `action_toggle_playback_for_component` + Phase 4.1 binding rename + Phase 9 D3 tests | `space` seeks to highlighted component's `start_time` and plays; if playing, pauses. |
| D4 (LLM reasoning text truncated inside table cells, hard to audit) | MED | Phase 2.2 `HERO_REASONING_FIELDS` constants + Phase 3.2.1 reasoning rows + Phase 2.2 dimmed/truncated table-cell policy | Authoritative reasoning rendering in Hero panel (full text, italic, dimmed). Table cells become presence-only indicators. |
 
---
 
## File inventory
 
### New files
```
ops/admin-cli/src/stream_of_worship/admin/component_editor/__init__.py
ops/admin-cli/src/stream_of_worship/admin/component_editor/app.py
ops/admin-cli/src/stream_of_worship/admin/component_editor/screen.py         # v4: ~1150 LOC (v3: ~900) — includes ComponentHeroPanel
ops/admin-cli/src/stream_of_worship/admin/component_editor/state.py
ops/admin-cli/src/stream_of_worship/admin/component_editor/autosave.py
ops/admin-cli/src/stream_of_worship/admin/component_editor/constants.py       # v4: adds HERO_PRIMARY_FIELDS, HERO_REASONING_FIELDS, REASONING_TABLE_TRUNC
 
ops/admin-cli/tests/admin/component_editor/__init__.py
ops/admin-cli/tests/admin/component_editor/test_state.py
ops/admin-cli/tests/admin/component_editor/test_autosave.py
ops/admin-cli/tests/admin/component_editor/test_constants.py                  # v4 NEW
ops/admin-cli/tests/admin/component_editor/test_hero_panel.py                 # v4 NEW
ops/admin-cli/tests/admin/component_editor/test_screen.py
ops/admin-cli/tests/admin/services/test_r2_component_result.py
```
 
### Modified files
```
ops/admin-cli/src/stream_of_worship/admin/commands/audio.py
  + review_components() Typer command (~80 LOC)
 
ops/admin-cli/src/stream_of_worship/admin/db/client.py
  + update_song_component_fields(component_id, fields) method (~15 LOC, thin wrapper)
  + update_song_component_fields_txn(conn, component_id, fields) method (~25 LOC)
  + ALLOWED_COMPONENT_FIELDS frozenset constant
 
ops/admin-cli/src/stream_of_worship/admin/services/r2.py
  + download_component_result(hash_prefix) method (~20 LOC)
  + upload_component_result(hash_prefix, payload) method (~12 LOC)
 
ops/admin-cli/tests/admin/test_db_client.py
  + test_update_song_component_fields_* (≈3 tests)
  + test_update_song_component_fields_txn_* (≈2 tests)
 
ops/admin-cli/tests/admin/test_audio_commands.py
  + test_review_components_unknown_song, test_review_components_no_components,
    test_review_components_launches_app
```
 
### Reused (unchanged)
```
ops/admin-cli/src/stream_of_worship/admin/editor/footer.py      (GroupedFooter)
ops/admin-cli/src/stream_of_worship/admin/services/playback.py  (PlaybackService)
ops/admin-cli/src/stream_of_worship/admin/db/models.py          (SongComponent, incl. to_dict)
ops/admin-cli/src/stream_of_worship/admin/db/schema.py          (no schema changes)
```
 
## LOC estimate
 
| Component | v3 LOC | v4 LOC | Δ |
|---|---|---|---|
| `constants.py` | ~70 | ~110 | +40 (HERO_PRIMARY_FIELDS, HERO_REASONING_FIELDS, REASONING_TABLE_TRUNC, cluster tagging on columns) |
| `state.py` | ~160 | ~185 | +25 (`get_selected_component()` helper) |
| `autosave.py` | ~130 | ~130 | 0 |
| `app.py` | ~50 | ~50 | 0 |
| `screen.py` | ~900 | ~1150 | +250 (ComponentHeroPanel widget + `_refresh_hero()` calls throughout + new playback action body) |
| `commands/audio.py` addition | ~80 | ~80 | 0 |
| `db/client.py` addition | ~45 | ~45 | 0 |
| `services/r2.py` addition | ~35 | ~35 | 0 |
| Tests | ~700 | ~950 | +250 (test_constants.py, test_hero_panel.py, D1–D4 regression tests) |
| **Total** | **~2170** | **~2735** | **+565** |
 
---
 
## Open questions / future work
 
1. **Bulk edit across songs** — apply the same theme correction to a selected
   set of songs in one keystroke. Not needed for v4.
2. **Theme-reasoning regeneration** — when a user changes `theme`, should
   the editor prompt to clear `theme_reasoning`? v4 leaves reasoning
   untouched. **v4 note:** the Hero panel now visibly shows the (now-stale)
   reasoning text right next to the edited theme value; this makes the
   staleness more obvious. Consider adding a `[stale]` marker on the
   reasoning row in a future version when the editable theme differs from
   the LLM-chosen one.
3. **Confidence re-bump** — should editing a field zero out its
   `*_confidence`? v4 does NOT touch confidences.
4. **Audit trail** — no audit table for who edited what / when. Out of scope
   for v4.
5. **Stale-revision guard** on R2 `components.json` (ETag/last-modified
   check). v4 performs a soft overwrite (C5); revisit if conflicts arise.
6. **Audit trail for partial-save state.** The `r2_save_pending` flag lives
   in autosave only. v4 hides this in Open questions.
7. **`--strict-stale-revision` flag.** Promotes C5's soft warning into a
   blocking error. Out of scope for v4.
8. **Concurrent editors.** Two operators editing the same song in parallel
   would race the DB UPDATEs. Consider optimistic `updated_at` CAS for a
   future version.
9. **Hero panel for partial-analysis case** — when both `entry_component`
   and `exit_component` are None, the Hero panel renders an empty-state
   message. Consider hiding the panel entirely (or collapsing to a one-line
   placeholder) in a future version.
10. **Multi-song compare view (`c` key toggle).** Rejected from v4 by the
    operator; tracked here for v5. Would swap to a compact read-only list
    of all loaded songs' transition-relevant fields (song_title, bpm, key,
    theme, energy, vocal_posture, time range) stacked one per line.
11. **Neighbor-song context strip** — the prev/next song's BPM/Key/Theme/
    Energy shown inline in the breadcrumb bar. Rejected from v4 by the
    operator; tracked here for v5.
12. **Per-row `▶` play-segment button** that auto-stops at `end_time` —
    rejected from v4 in favour of the simpler "seek-to-start + play-through"
    semantics in D3. Tracked here for a future "focused review mode" should
    the operator want to hear just the Chorus looped.
 
(End of v4 spec)
