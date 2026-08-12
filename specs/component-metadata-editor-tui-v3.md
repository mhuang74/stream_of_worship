---

# Implementation Plan: Component Metadata Editor TUI (v3)

> **Date:** 2026-08-12
> **Branch:** TBD
> **Spec ID:** `component-metadata-editor-tui-v3`
> **Supersedes:** `component-metadata-editor-tui-v2`
> **Status:** Standalone consolidation — merges v1's full scope with every v2
> fix applied inline. This document is self-contained; readers do NOT need to
> open v1 or v2.

---

## Changelog (v3 vs v2 vs v1)

v1 was the original full-scope plan. v2 was a **delta document** that only
reprinted the sections changed to fix Production-blocking issues (B1–B3,
C1–C5), pointing readers back to v1 for everything else. That split made the
spec hard to read: implementers had to mentally merge two files.

**v3 is a standalone document.** It folds every v2 fix directly into the v1
section bodies, so a single read gives the complete, current plan. No content
is changed relative to "v1 with all v2 fixes applied" — v3 is purely an
editorial consolidation, plus the minor clarifications noted below.

Minor v3-only clarifications (no behavioural change):
- Confirmed `SongComponent.to_dict()` exists (`db/models.py:627`) — the v2
  `_save_r2_component_result` synthesised-payload path relies on it.
- Confirmed `DatabaseClient.transaction()` yields a `psycopg.Connection`
  (`db/client.py:78`) — the v2 `update_song_component_fields_txn` signature
  matches.
- Confirmed DB CHECK constraints for `theme` (12-value) and `vocal_posture`
  (3-value) exist (`db/schema.py:278,280`) — the targeted UPDATE inherits them.

### Issues addressed (carried from v2)

| ID | Severity | v1 location | Issue | Fix (now inlined) |
|---|---|---|---|---|
| B1 | HIGH | `action_save` lines 925-946 | Unconditionally clears `working`/`dirty`/autosave even when R2 upload inside `_save_r2_component_result` failed. The `session.dirty = True` set in R2 failure branch is overwritten; in-memory edits + on-disk autosave are gone → retry impossible. | Phase 6.1: `_save_r2_component_result` returns `bool` success status. `action_save` only clears state when BOTH DB and R2 succeeded. On R2 failure: keeps `working` + `dirty` + autosave, surfaces a yellow "Saved DB only — R2 failed — press s to retry" status, does NOT clear undo/redo stacks. Retry re-runs the same idempotent DB UPDATE + R2 merge. |
| B2 | HIGH | `_save_r2_component_result` line 962 | `session.entry_component.content_hash or session.exit_component.content_hash or ""` raises `AttributeError` when `entry_component is None` (partial-analysis case is explicitly supported in Phase 1). | Phase 6.2: introduce None-safe helper `first_content_hash(session)` that picks whichever side is non-None (both reference recording.content_hash so either is canonical). |
| B3 | LOW | `action_save` Phase 6 body | Doesn't clear undo/redo stacks, contradicting Phase 7.4 ("On `action_save`: clear the undo + redo stacks for the saved session"). Post-save `ctrl+z` would re-apply a value whose working entry was cleared, silently re-dirtying. | Phase 6.1: on full success, call new `state.clear_undo_stacks()` which clears both stacks for the current session (Phase 7.4 already specified). |
| C1 | HIGH | `action_save` try/except (lines 916-933) | Only wraps the DB block. A `download_component_result` exception inside `_save_r2_component_result` propagates past the cleanup branch, leaving state half-committed (DB written, state indeterminate). | Phase 6.2: `_save_r2_component_result` wraps BOTH the R2 download AND upload in a single try/except. Network blip on download is treated as a retryable R2 failure (returns `False`) rather than an unhandled exception. |
| C2 | HIGH | `action_save` inline UPDATE (lines 925-930) | Builds `UPDATE song_components SET ...` inline from `session.working` keys, bypassing the Phase 0 `ALLOWED` whitelist safeguard. | Phase 0.1: add transaction-accepting sibling `update_song_component_fields_txn(conn, component_id, fields)` that performs the same ALLOWED validation. Phase 6.1: `action_save` calls the whitelisted helper inside `with self.db_client.transaction() as conn:` — no inline SQL in the screen. |
| C3 | HIGH | `action_save` line 942 | Calls `self._reload_components_from_db(session)` which is never defined in v1. | Phase 6.4: explicit spec — calls `db.get_song_components_entry_exit(session.song_id)`, replaces `session.entry_component` / `exit_component` with fresh `SongComponent` objects containing persisted (post-edit) values. |
| C4 | MED | `_undo_stacks` keyed by `id(session)` (state.py lines 427-428, 438-439) | Python's `id()` can theoretically be reused after GC. Safe today (sessions list is built once in `commands/audio.py` and never rebuilt), but brittle across refactor. | Phase 2.3: switch dict keys to `session.song_id` (stable PK). `current_undo` / `current_redo` look up `self.current.song_id`. |
| C5 | LOW | Phase 6.3 stale-revision guard | Marked "optional / out of scope for v1". For Production, a soft warning is cheap. | Phase 6.5: soft `content_hash` consistency check — if R2 `components.json` exists with a `content_hash` field that differs from the current song's recording `content_hash` (lookup via `db.get_recording_by_song_id`), log a warning and inject a banner in the save toast. Does not block the save. |

---

## Problem

The `sow-admin audio components` command (re)runs the Analysis Service's
Component Analysis job and persists the result into `song_components` plus an
R2 `components.json` cache. There is no interactive way for a human to:

- listen to the **entry** and **exit** Chorus segments,
- review the per-component **audio-derived** metadata (bpm, key, groove, energy),
- review the **LLM-derived** metadata (theme, vocal_posture + reasoning),
- compare values across multiple similar / different songs to gauge accuracy, and
- correct fields that are obviously wrong without re-running the (expensive) job.

The LRC Editor TUI (`sow-admin audio edit-lrc`) already solves the analogous
problem for lyric timestamps and is the established UX reference for hot-keys,
playback, and footer layout in the admin CLI.

## Goal

Add a new `sow-admin audio review-components <list of song_ids>` command that
launches a Textual TUI mirroring the LRC Editor's look-and-feel, focused on
viewing, comparing, and editing component-level metadata for the entry / exit
Chorus of one or more songs.

## Design Decisions (from clarifying questions)

| Decision | Choice |
|---|---|
| Song switching | **In-TUI** via `n` / `p` hotkey cycle. All passed `song_id`s loaded up-front. Header shows current index (`Song 2 / 5`). |
| Compare view | **Quick-switch single view** — one song's metadata visible at a time; user cycles between songs to compare. No side-by-side split. |
| Editable fields | **Four human-judgement fields** only: `theme`, `vocal_posture`, `groove_density`, `energy_level`. (Reasoning: bpm / key / confidences require tooling the human cannot beat; reasoning text is an audit trail of the LLM call, not editable.) |
| Persistence | **DB + R2 `components.json`**. Write through to `song_components` (truth source) AND merge-field into the cached `components.json` on R2 so a later re-run does not silently overwrite the human edit. |
| Playback behaviour | **Seek to component start, play through the song naturally** (no loop, no auto-stop at `end_time`). Mirrors the LRC editor's `j` jump-to-line. |
| Components shown | **`role = 'entry'` and `role = 'exit'` Chorus only**. Up to 2 rows per song. Uses `DatabaseClient.get_song_components_entry_exit(song_id)`. Songs missing one or both rows show a placeholder row with `—`. |
| Field editor UX | Enum fields (`theme` 12 values, `vocal_posture` 3 values) **cycle with `[` / `]` keys**. Float fields (`groove_density`, `energy_level`) use **inline numeric input overlay** (mirror LRC editor's `_show_row_edit_input`). |
| Layout | **Single DataTable**: rows = components (entry chorus, exit chorus), columns = all song_components fields. Editable columns marked with a leading `*` in the header and rendered with a distinct style. |
| Autosave & undo | **Autosave + undo/redo** like the LRC editor. Autosave per song at `{cache_dir}/{hash_prefix}/components/components.autosave.json`. Undo stack max 100 entries; cleared on save. |
| Read-only context | **Show all fields, edit 4**. All song_components columns visible for context; only the 4 editable fields accept input. |

---

## Reference: LRC Editor TUI structure (maximise reuse)

The new editor mirrors the package layout under
`ops/admin-cli/src/stream_of_worship/admin/editor/`:

| LRC Editor file | Reuse in component editor |
|---|---|
| `editor/app.py` (`LRCEditorApp`) | New `ComponentEditorApp` (Textual `App[None]`) |
| `editor/screen.py` (`LRCEditorScreen`) | New `ComponentEditorScreen` (Textual `Screen[None]`) |
| `editor/state.py` (`EditorState`, `UndoEntry`) | New `ComponentEditorState`, `ComponentUndoEntry` |
| `editor/autosave.py` (`AutosaveState`) | New `ComponentAutosaveState` |
| `editor/footer.py` (`GroupedFooter`) | **Reused directly** (reads screen's `BINDINGS` + `BINDING_GROUPS`) |
| `services/playback.py` (`PlaybackService`) | **Reused directly** (miniaudio-based) |

### LRC Editor hot-key reference (to mirror)

```
space        toggle_playback        Play/Pause
left/right   seek_backward/forward  Seek ±5s
j            jump_to_line            Seek to selected line
s            save_upload             Save
ctrl+z/y     undo/redo
escape / q   quit_editor
?            show_keymap
```

The component editor reuses **all** of these (with `jump_to_line` renamed
`jump_to_component`) and adds song-switch (`n` / `p`) + enum-cycling (`[` / `]`)
+ numeric edit (`e`) bindings.

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
   │  • SongBreadcrumb      — "Song 2 / 5 — [song_id] song_title"
   │  • PlaybackBar        — ▶ [00:23 / 03:45]  (reused pattern)
   │  • ComponentMetadataTable (DataTable)      — 2 rows × all columns
   │  • StatusIndicator    — dirty / autosave / song_index badge
   │  • GroupedFooter      — (reused from editor/footer.py)
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

    Args:
        conn: A psycopg connection with an active transaction (typically
            obtained via `with self.db_client.transaction() as conn:`).
        component_id: song_components.id (NOT NULL).
        fields: Dict of {column_name: new_value}. May be a subset.

    Returns:
        True if a row was updated; False if no row matched component_id.

    Raises:
        ValueError: If `fields` contains an unsupported column name.
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

**Goal:** Wire the CLI entry point.

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
    """Launch a Textual TUI to view / review / compare / edit component metadata.

    Loads the entry and exit Chorus component rows for each song (as produced by
    the Component Analysis job), downloads the song's audio for playback, and
    opens an interactive editor mirroring `audio edit-lrc` hotkeys.
    """
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

**Goal:** Pure-data models — no Textual imports. Pattern matches
`editor/state.py` and `editor/autosave.py`.

**Complexity:** M

### 2.1 New package `component_editor/`

Create:

```
ops/admin-cli/src/stream_of_worship/admin/component_editor/
├── __init__.py        # """Admin interactive Component Metadata editor package."""
├── app.py             # ComponentEditorApp (Textual App[None])
├── screen.py          # ComponentEditorScreen + widgets (main, ~900 LOC target)
├── state.py           # ComponentEditorState + ComponentUndoEntry
├── autosave.py        # ComponentAutosaveState + load/save/clear helpers
└── constants.py       # EDITABLE_FIELDS, THEME_VALUES, VOCAL_POSTURE_VALUES, SCHEMA_VERSION
```

### 2.2 `component_editor/constants.py`

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

# Column order for the DataTable (left → right). Read-only columns marked RO;
# editable marked RW (*). 27 columns from SONG_COMPONENT_COLUMNS_SELECT.
DATA_TABLE_COLUMNS: tuple[tuple[str, str, bool], ...] = (
    # (key, header_label, editable)
    ("role",                 "Role",            False),
    ("component_type",       "Type",            False),
    ("occurrence_index",     "Occ",             False),
    ("start_time",           "Start",           False),
    ("end_time",             "End",             False),
    ("bpm",                  "BPM",             False),
    ("key",                  "Key",             False),
    ("backbeat_strength",    "Backbeat",        False),
    ("confidence",           "Conf",            False),
    ("bpm_confidence",       "BPMc",            False),
    ("key_confidence",       "KEYc",            False),
    ("groove_confidence",    "GRVc",            False),
    ("backbeat_confidence",  "BBc",             False),
    ("energy_confidence",    "ENGc",            False),
    ("theme_confidence",     "THMc",            False),
    ("vocal_posture_confidence", "PSTc",        False),
    ("theme_reasoning",      "ThemeReason",     False),
    ("posture_reasoning",    "PostureReason",   False),
    ("created_at",           "Created",         False),
    ("updated_at",           "Updated",         False),
    # Editable (4)
    ("theme",                "*Theme",          True),
    ("vocal_posture",        "*Posture",        True),
    ("groove_density",       "*Groove",         True),
    ("energy_level",         "*Energy",         True),
)

# Float editor input attributes
GROOVE_DENSITY_MIN = 0.0
GROOVE_DENSITY_MAX = 2.0      # no DB CHECK; admin guard only
ENERGY_LEVEL_MIN = -60.0      # dB; admin guard only
ENERGY_LEVEL_MAX = 0.0
```

### 2.3 `component_editor/state.py`

Changes from the original v1 sketch:
- **C4 fix:** `_undo_stacks` / `_redo_stacks` are keyed by `song_id`
  (a stable PK string), not `id(session)`.
- **B3 fix:** Add `clear_undo_stacks(session)` helper used by `action_save`
  on full success.
- `ComponentUndoEntry` keeps `component_role: str` (derives the working key
  on undo/redo).

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
    # role in {"entry", "exit"}; field in EDITABLE_FIELDS
    working: dict[tuple[str, str], Any] = field(default_factory=dict)
    dirty: bool = False
    # NEW (C1/B1 fix): indicates the last save partially failed (DB committed,
    # R2 did not). Surfaced in the status indicator; cleared on next successful
    # full Save. Does NOT block further edits — the user may edit more fields
    # before retrying 's'.
    r2_save_pending: bool = False

    def component_for_role(self, role: str) -> Optional[SongComponent]:
        return self.entry_component if role == "entry" else self.exit_component


@dataclass
class ComponentEditorState:
    """Top-level mutable state for the Component Metadata editor."""

    sessions: list[SongSession]
    current_index: int = 0
    # C4 fix: keyed by session.song_id (stable PK string), NOT id(session).
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
        # Revert working value
        key = (entry.component_role, entry.field_name)
        self.current.working[key] = entry.old_value
        self.current_redo.append(entry)
        # dirty stays True until save
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
        """Clear both the undo and redo stacks for `session`.

        Called from action_save on full success (DB + R2 both committed).
        Rationale: session.working is empty after Reload-from-DB, so an undo
        target no longer exists. Keeping the stacks would let ctrl+z silently
        re-dirty the session by re-applying old_value into working.
        """
        sid = session.song_id
        self._undo_stacks.get(sid, []).clear()
        self._redo_stacks.get(sid, []).clear()

    def get_value(self, role: str, field_name: str) -> Any:
        """Either the working (dirty) value, or the persisted field value."""
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
```

### 2.4 `component_editor/autosave.py`

Mirrors `editor/autosave.py`. One autosave file per song. Includes the
`r2_save_pending` field so a partial-save-then-crash is recoverable on next
launch.

```python
"""Autosave recovery for the Component Metadata editor.

One file per song at {cache_dir}/{hash_prefix}/components/components.autosave.json.
Captures the working edits so a crash / disconnect / accidental exit can be
recovered on next launch for the same song.
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
    # list of {role, field, value} for any dirty working edits
    working: list[dict[str, Any]] = field(default_factory=list)
    dirty: bool = False
    selected_row: int = 0
    selected_column_key: str = "role"
    # NEW (B1/C1 fix): round-trip partial-save status so next launch warns
    # user that R2 still needs retry.
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
    # Atomic write: tmp file in same dir, then rename.
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

### 2.5 `_maybe_apply_autosave` recovery dialog

When `load_autosave(cache_dir, session.hash_prefix)` returns a snapshot whose
`dirty=True` OR `r2_save_pending=True`:

- If `r2_save_pending=True`: recovery banner is shown as
  *"Recovered N edits — DB committed but R2 still pending — press `s` to retry"*.
  Apply working edits to state, set `session.dirty=True` and
  `session.r2_save_pending=True`.
- Else (normal dirty recovery): standard LRC-editor-style
  `AutosaveRecoveryDialog` with "r" recover / "d" discard.

---

## Phase 3: TUI app + screen + playback

**Goal:** Build the Textual UI; reuse `PlaybackService` and the LRC editor's
`GroupedFooter` / `PlaybackBar` / `StatusIndicator` patterns.

**Complexity:** L

### 3.1 `component_editor/app.py`

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

### 3.2 `component_editor/screen.py` — widgets

The screen composes (bottom-up, mirroring LRC editor at
`editor/screen.py:1-308`):

| Widget | Mirrored from | Purpose |
|---|---|---|
| `Header()` | textual.widgets.Header | Top Textual bar |
| `SongBreadcrumb` (new) | — | "● Song 2 / 5  —  [abc123] 主禱文  —  hash_prefix=abc123def4567" |
| `PlaybackBar` (new, near-verbatim copy of LRC editor's `PlaybackBar`) | `editor/screen.py:60-98` | ▶ [00:23 / 03:45] progress bar |
| `ComponentMetadataTable` (new `DataTable` subclass) | `editor/screen.py`'s `LyricLineTable` pattern | 2 rows × ~24 columns; cursor on cells; column header includes `*` prefix for editable |
| `StatusIndicator` (new, near-verbatim copy) | `editor/screen.py:116-145` | `dirty` (`*`/`✓`), `autosave` (✓/—), current song index badge |
| `GroupedFooter` (REUSED) | `editor/footer.py` | Reads `BINDINGS` + `BINDING_GROUPS` from the screen, renders clusters |

For numeric input overlay, copy the LRC editor's overlay
`Input(id="value-edit-input")` + `_show_row_edit_input` machinery verbatim with
simplifications (no padding/quanter business).

### 3.3 `on_mount` flow

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
    # Apply any recovered autosave
    self._maybe_apply_autosave()
    self._refresh_table()

def _load_audio_for_current_song(self) -> None:
    session = self.state.current
    if not session.audio_path:
        return
    self.playback.load(Path(session.audio_path))
```

### 3.4 Song switch

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
    self._update_breadcrumb()
    self._update_status()
```

---

## Phase 4: Bindings

**Goal:** Mirror LRC editor's binding style (declarative `Binding` list +
`BINDING_GROUPS`). All hot-keys dispatch to `action_*` methods.

**Complexity:** M

### 4.1 `ComponentEditorScreen.BINDINGS`

```python
from textual.binding import Binding

class ComponentEditorScreen(Screen[None]):
    BINDINGS = [
        # Playback / Nav
        Binding("space",  "toggle_playback",  "Play/Pause"),
        Binding("left",   "seek_backward",    "Seek -5s"),
        Binding("right",  "seek_forward",     "Seek +5s"),
        Binding("j",       "jump_to_component", "Jump"),
        # Song switch
        Binding("n",       "next_song",        "Next Song"),
        Binding("p",       "prev_song",        "Prev Song"),
        # Component table nav (cursor_left / cursor_right / cursor_up /
        # cursor_down / page_up / page_down come from DataTable itself)
        # Edit
        Binding("bracketleft",  "cycle_field_prev",  "Cycle −"),  # '['
        Binding("bracketright", "cycle_field_next",  "Cycle +"),  # ']'
        Binding("e",            "edit_numeric",      "Edit Num"),
        # General
        Binding("s",            "save",              "Save"),
        Binding("ctrl+z",       "undo",              "Undo"),
        Binding("ctrl+y",       "redo",              "Redo"),
        Binding("escape",       "quit_editor",       "Quit"),
        Binding("q",            "quit_editor",       "Quit"),
        Binding("?",            "show_keymap",       "Keymap"),
    ]

    BINDING_GROUPS: dict[str, list[str]] = {
        "Playback": ["toggle_playback", "seek_backward", "seek_forward", "jump_to_component"],
        "Songs":     ["next_song", "prev_song"],
        "Edit":      ["cycle_field_prev", "cycle_field_next", "edit_numeric"],
        "General":   ["save", "undo", "redo", "quit_editor", "show_keymap"],
    }
```

### 4.2 Hot-key → action map (summary table)

| Key | Action | Description |
|---|---|---|
| `space` | `toggle_playback` | Play / Pause current song audio |
| `left` / `right` | `seek_backward` / `seek_forward` | Seek ±5s |
| `j` | `jump_to_component` | Seek to `start_time` of selected (entry/exit) component |
| `n` / `p` | `next_song` / `prev_song` | Switch to next / prev song in the passed list (autosave current first) |
| `up`/`down`/`pageup`/`pagedown` | (DataTable cursor) | Row navigation; max 2 rows |
| `left`/`right` (cell-level) | (DataTable cursor) | Column navigation (note: at the screen level `left/right` are bound to seek; those default cell movements are not needed because only 2 rows exist) |
| `[` / `]` | `cycle_field_prev` / `cycle_field_next` | Cycle enum value down/up on the currently highlighted theme or vocal_posture cell (ignored for non-enum cells) |
| `e` | `edit_numeric` | Open numeric input overlay for the highlighted groove_density or energy_level cell (ignored for non-numeric cells) |
| `s` | `save` | Commit dirty edits on current song to DB + R2 components.json |
| `ctrl+z` / `ctrl+y` | `undo` / `redo` | Per-song undo / redo (max 100) |
| `escape` / `q` | `quit_editor` | Push QuitConfirmDialog if dirty (matches LRC editor); otherwise `app.exit()` |
| `?` | `show_keymap` | Modal screen listing all bindings (copy from LRC editor's `KeymapDialog`) |

### 4.3 Conflict note: `left` / `right`

In the LRC editor, `left/right` are seek hotkeys; the same convention is
preserved here (mirroring the user's instruction to use LRC editor as the
reference). Cell cursor movement across columns is achieved via `tab` /
`shift+tab` (DataTable-friendly) instead. Add:

```python
Binding("tab",       "cursor_right", "Col →"),  # delegate to DataTable
Binding("shift+tab",  "cursor_left",  "Col ←"),
```

(If Textual refuses to bind `tab` due to focus traversal, fall back to `}`
/ `{` keys for column nav — same cluster as `[`/`]`.)

---

## Phase 5: Edit UX

**Goal:** Implement the 4-field edit patterns: enum cycling and numeric input.

**Complexity:** M

### 5.1 Action: `cycle_field_next` / `cycle_field_prev`

Only operates when the highlighted column key (`state.selected_column_key`)
is `theme` or `vocal_posture`. Looks up the current value in
`THEME_VALUES` / `VOCAL_POSTURE_VALUES`, picks prev / next (wrap-around),
calls `state.set_value(role, field, new_value)`, and calls
`_do_autosave()` + `_refresh_table_cell(role, field)`.

```python
def action_cycle_field_next(self) -> None:
    field = self.state.selected_column_key
    if field not in ("theme", "vocal_posture"):
        return  # ignore on non-enum cells
    role = self._selected_role()  # "entry" or "exit"
    values = THEME_VALUES if field == "theme" else VOCAL_POSTURE_VALUES
    current = self.state.get_value(role, field)
    try:
        idx = values.index(current)
    except (ValueError, TypeError):
        idx = -1  # current is None or invalid → jump to first
    new_value = values[(idx + 1) % len(values)]
    self.state.set_value(role, field, new_value)
    self._do_autosave()
    self._refresh_table()
```

### 5.2 Action: `edit_numeric`

Only operates when `state.selected_column_key` is `groove_density` or
`energy_level`. Reuses the LRC editor's `_show_row_edit_input` machinery:
position an `Input` overlay at the cell's screen region, accept a value,
validate as float + range guard (see `constants.py`), call `state.set_value`,
refresh, autosave. Reject non-numeric input by leaving the overlay open and
ringing the bell.

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
+ push undo + autosave + refresh cell + remove overlay; else: bell + keep
overlay open.

### 5.3 Guard behaviour

Mirroring LRC editor's `_guard_active_edit()`:
- Cycle and numeric-edit actions are blocked when a value-edit overlay is
  already open (`self._edit_mode is not None`). First `escape` cancels the
  overlay (does not quit).
- When viewing a song whose entry or exit component is `None` (missing
  component analysis), cycling / editing actions no-op and ring the bell.

---

## Phase 6: Save flow

**Goal:** Commit dirty edits on the current song to DB + R2, with a
reliable partial-failure path: DB and R2 cannot be made atomic (R2 has no
real transaction), but the editor must remain in a retryable state whenever
R2 fails AFTER DB succeeds.

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
    #    C2 fix: delegates to update_song_component_fields_txn to inherit
    #    the ALLOWED whitelist validation. No inline SQL in the screen.
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
        # DB failed → nothing committed. State unchanged; user can fix and
        # retry. Do NOT touch working / dirty / autosave / undo stacks.
        return

    # 3. Write R2 components.json (merge). Returns True on success.
    #    C1 fix: any exception inside _save_r2_component_result (download
    #    OR upload) is caught and reported as a retryable R2 failure.
    r2_ok = self._save_r2_component_result(session, updates_by_role)

    # 4. Branch on R2 outcome.
    if not r2_ok:
        # B1 fix: do NOT clear working / dirty / autosave / undo stacks.
        # State stays retryable. Next 's' press re-runs the same idempotent
        # DB UPDATE (CHECK constraints still respected) AND the R2 merge.
        session.r2_save_pending = True
        self._do_autosave()  # write the r2_save_pending flag to disk
        self._update_status()
        self._refresh_table()
        self._notify(
            "[yellow]Saved DB only — R2 failed — press s to retry.[/]"
        )
        return

    # 5. Full success → clear everything.
    #    After reload-from-DB (§6.4), session.working is empty; the in-memory
    #    SongComponent objects carry the persisted values. Undo/redo must be
    #    cleared so ctrl+z cannot re-dirty the session.
    session.working.clear()
    session.dirty = False
    session.r2_save_pending = False
    self._reload_components_from_db(session)  # C3 fix — spec below §6.4
    self.state.clear_undo_stacks(session)     # B3 fix
    clear_autosave(self.cache_dir, session.hash_prefix)
    self._update_status()
    self._refresh_table()
    self._notify("[green]Saved (DB + R2).[/]")
```

### 6.2 `_save_r2_component_result`

Key changes from the original v1 sketch:
- Returns `bool` (True = R2 written; False = retryable failure).
- **B2 fix:** content_hash derived via a None-safe `first_content_hash` helper.
- **C1 fix:** BOTH the R2 download AND the upload are wrapped in try/except;
  any exception is caught and reported as a retryable failure (returns False).

```python
def _save_r2_component_result(
    self, session: SongSession, updates_by_role: dict[str, dict[str, Any]]
) -> bool:
    """Merge dirty edits into R2 components.json and upload.

    Returns:
        True if the R2 upload succeeded.
        False if the R2 download OR upload failed (retryable).
    """
    hash_prefix = session.hash_prefix
    try:
        payload = self.r2_client.download_component_result(hash_prefix)
    except Exception as e:
        # C1 fix: network blip on download (transient 5xx, ReadTimeout, etc.)
        self._notify(f"[yellow]R2 download failed: {e}[/]")
        return False

    if payload is None:
        # First-time write: synthesise a minimal payload.
        # B2 fix: first_content_hash picks the non-None side (both reference
        # recording.content_hash so either is canonical).
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

    # Merge the 4 editable fields into matching component dicts.
    # NOTE: matching is by `role`. Both entry and exit rows of a single
    # recording always share the same content_hash, so there is no
    # collision risk under normal operation. If R2 ever contains duplicate
    # role entries (corruption / future schema change), the merge writes to
    # all matching dicts — harmless for v1; revisit if dedup is required.
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
        # C1 fix: upload exception is retryable, not fatal.
        self._notify(f"[yellow]R2 upload failed: {e}[/]")
        return False

    return True


def first_content_hash(session: SongSession) -> str:
    """Pick a non-None component's content_hash for the synthesised R2
    payload. Both entry and exit reference recording.content_hash, so either
    is canonical.

    B2 fix: avoids the AttributeError raised by the v1 chain
    `session.entry_component.content_hash or session.exit_component.content_hash`
    when entry_component is None (partial-analysis case explicitly supported
    by Phase 1 — songs missing one or both rows are loaded with placeholders).
    """
    if session.entry_component is not None:
        return session.entry_component.content_hash or ""
    if session.exit_component is not None:
        return session.exit_component.content_hash or ""
    return ""
```

### 6.3 Stale-revision guard (SOFT warning)

```python
# In _save_r2_component_result, after building `payload` but before upload:
existing_hash = payload.get("content_hash") if isinstance(payload, dict) else None
if existing_hash and existing_hash != first_content_hash(session):
    # C5 fix (soft): do NOT block the save; log + schedule a banner toast.
    logger.warning(
        "R2 components.json content_hash=%s mismatches recording content_hash=%s "
        "for hash_prefix=%s; saving with merged values regardless.",
        existing_hash, first_content_hash(session), hash_prefix,
    )
```

A `--strict-stale-revision` flag (out of scope for v3; tracked in Open
questions) may later promote this to a hard error.

### 6.4 `_reload_components_from_db` (C3 fix)

```python
def _reload_components_from_db(self, session: SongSession) -> None:
    """Replace session.entry_component / exit_component with refreshed
    SongComponent objects reflecting the just-persisted DB values.

    C3 fix: v1 referenced this helper without defining it. Uses the existing
    DatabaseClient.get_song_components_entry_exit (no new DB method).
    """
    entry, exit_comp = self.db_client.get_song_components_entry_exit(
        session.song_id
    )
    session.entry_component = entry
    session.exit_component = exit_comp
```

Note: `session.working` is cleared BEFORE this call in `action_save`, so on
return `state.get_value(role, field)` returns the persisted value directly
from the refreshed `SongComponent` (no working override).

---

## Phase 7: Autosave & undo/redo loop

**Goal:** Wire autosave into the editing lifecycle.

**Complexity:** S

### 7.1 `_do_autosave`

Now serialises `r2_save_pending` so a partial-save-then-crash is recoverable:

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

See Phase 2.5 for the new `r2_save_pending=True` recovery banner.

### 7.3 Autosave triggers

Call `_do_autosave()` after every state mutation:
- `action_cycle_field_prev` / `action_cycle_field_next`
- `on_input_submitted` (numeric edit)
- `action_undo` / `action_redo`

Call `clear_autosave` only inside `action_save` **on full success** (both DB
and R2). The v1 unconditional clear is removed — see Phase 6.1 step 5.

### 7.4 Undo / redo wiring (bodies aligned with Phase 6.1)

```python
def action_undo(self) -> None:
    entry = self.state.undo()
    if entry is None:
        self.app.bell()
        return
    self._do_autosave()
    self._refresh_table()

def action_redo(self) -> None:
    entry = self.state.redo()
    if entry is None:
        self.app.bell()
        return
    self._do_autosave()
    self._refresh_table()
```

On `action_save` (Phase 6.1 step 5): `state.clear_undo_stacks(session)` is
invoked on **full success only**. On partial-save (R2 failed), the stacks are
preserved so the user can keep editing and undoing within the retry window.

---

## Phase 8: Quit / dialog flow

**Goal:** Match LRC editor's quit + keymap dialog UX.

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
v2 fix.

**Complexity:** M

New test files:

| File | Coverage |
|---|---|
| `tests/admin/component_editor/test_state.py` | `ComponentEditorState.set_value` / `undo` / `redo` / `push_undo` (>100) / multi-session independence; `SongSession.dirty` propagation; `clear_undo_stacks` clears both stacks for the named session and leaves other sessions intact (multi-song fixture); undo/redo keyed by `song_id` survives removing & re-adding a `SongSession` with the same `song_id` (regression for C4). |
| `tests/admin/component_editor/test_autosave.py` | `save_autosave` ↔ `load_autosave` round-trip; corrupt file → None; `clear_autosave` no-op safety; `r2_save_pending` survives a to_dict/from_dict round-trip (regression for B1). |
| `tests/admin/component_editor/test_screen.py` | Textual Pilot: launch with a fake R2Client + DatabaseClient (monkeypatch) and 3 mocked SongSessions; cycling `[` / `]` changes theme; `e` overlay accepts numeric; `s` saves → DB target UPDATE called + R2 upload called + autosave cleared; `n` switches song + reloads PlaybackService.load; `q` with dirty pushes confirm dialog. Plus the v2 regression suite below. |
| `tests/admin/test_audio_commands.py` additions | `review-components` rejects unknown song_id; warns + skips song with no entry/exit components; launches app if ≥ 1 valid |
| `tests/admin/services/test_r2_component_result.py` | `R2Client.download_component_result` 404 → None; happy path → parsed dict; `upload_component_result` calls `put_object` with the right key + body + content_type; payload round-trip equality |
| `tests/admin/test_db_client.py` additions | `update_song_component_fields(component_id, {"theme": "敬拜"})` updates 1 row; rejects unknown字段; rejects multi-row UPDATE on wrong id (rowcount=0); `update_song_component_fields_txn` existing-conn variant: same validation behaviour as the wrapper; refuses unknown fields; committed in caller's transaction (rolled back if caller raises after the UPDATE). |

### v2 regression test suite (in `test_screen.py`)

| Test | Description |
|---|---|
| **B1 regression** | save with DB ok + R2 upload raising → asserts `session.dirty` is `True`, `session.working` is untouched, autosave file still exists with `r2_save_pending=True`, status shows retry message, undo stacks NOT cleared. |
| **B2 regression** | save first-time R2 payload when `session.entry_component=None` and `session.exit_component` is set → asserts no `AttributeError`, payload `content_hash` is the exit component's hash. Symmetric case for the inverse. |
| **C1 regression (download)** | `download_component_result` raising `ClientError` → save returns False, DB committed (already), state preserved for retry. |
| **C2 regression** | assert `action_save` calls `db_client.update_song_component_fields_txn` (not inline SQL); passing `{"bpm": 120}` into `state.set_value` is prevented because `EDITABLE_FIELDS` is enforced upstream, but a malformed `working` dict with a non-ALLOWED key raises `ValueError` from the DB helper at save time. |
| **C3 regression** | after full-success save, `session.entry_component` and `session.exit_component` are freshly-fetched instances (different `id()`) whose `theme` field matches the saved value. |
| **B3 regression** | after full-success save, `ctrl+z` rings the bell (undo stack empty) and does not re-dirty the session. |

Use `unittest.mock` for `PlaybackService` in screen tests (don't bind a real audio device).

Snapshot tests for DB call args (asserts the targeted UPDATE uses `WHERE id = %s`).

---

## Verification matrix (issues → fixes)

| Issue | Severity | v3 Section | Resolution |
|---|---|---|---|
| B1 (R2 failure loses edits) | HIGH | Phase 6.1 step 4 + `r2_save_pending` flag in §2.3 / §2.4 / §7.1 | Keep `working` / `dirty` / autosave / undo stacks on R2 failure; surface "DB only" status; retry is idempotent (DB UPDATE same values + R2 merge). |
| B2 (`content_hash` AttributeError on None) | HIGH | Phase 6.2 `first_content_hash` | None-safe picker choosing the non-None side. |
| B3 (undo/redo not cleared) | LOW | Phase 6.1 step 5 + Phase 2.3 `clear_undo_stacks` helper | Stacks cleared on **full success** (preserved on partial-save). |
| C1 (R2 download exception uncaught) | HIGH | Phase 6.2 (try/except wrap) | Both download and upload caught; returns False (retryable). |
| C2 (inline UPDATE bypasses whitelist) | HIGH | Phase 0.1.2 + Phase 6.1 step 2 | New `update_song_component_fields_txn` does ALLOWED validation; screen delegates to it. |
| C3 (`_reload_components_from_db` undefined) | HIGH | Phase 6.4 | Explicit spec using `db.get_song_components_entry_exit`. |
| C4 (id()-keyed undo stacks) | MED | Phase 2.3 | Stacks keyed by `session.song_id` (stable PK string). |
| C5 (stale-revision guard absent) | LOW | Phase 6.3 | Soft warning on `content_hash` mismatch; surfaces in toast. |

---

## File inventory

### New files
```
ops/admin-cli/src/stream_of_worship/admin/component_editor/__init__.py
ops/admin-cli/src/stream_of_worship/admin/component_editor/app.py
ops/admin-cli/src/stream_of_worship/admin/component_editor/screen.py
ops/admin-cli/src/stream_of_worship/admin/component_editor/state.py
ops/admin-cli/src/stream_of_worship/admin/component_editor/autosave.py
ops/admin-cli/src/stream_of_worship/admin/component_editor/constants.py

ops/admin-cli/tests/admin/component_editor/__init__.py
ops/admin-cli/tests/admin/component_editor/test_state.py
ops/admin-cli/tests/admin/component_editor/test_autosave.py
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

| Component | LOC |
|---|---|
| `constants.py` | ~70 |
| `state.py` | ~160 |
| `autosave.py` | ~130 |
| `app.py` | ~50 |
| `screen.py` | ~900 |
| `commands/audio.py` addition | ~80 |
| `db/client.py` addition | ~45 |
| `services/r2.py` addition | ~35 |
| Tests | ~700 |
| **Total** | **~2170** |

---

## Open questions / future work

1. **Bulk edit across songs** — apply the same theme correction to a
   selected set of songs in one keystroke. Not needed for v3.
2. **Theme-reasoning regeneration** — when a user changes `theme`, should
   the editor prompt to clear `theme_reasoning` (which now reflects the
   LLM's rationale for the wrong value)? v3 leaves reasoning untouched.
3. **Confidence re-bump** — should editing a field zero out its
   `*_confidence`? v3 does NOT touch confidences (user said those are
   LLM-derived and not editable; the editor leaves them as the LLM left
   them).
4. **Audit trail** — no audit table for who edited what / when. If requested,
   add a `song_components_edits` table keyed by `component_id` with old/new
   values and editor identity. Out of scope for v3.
5. **Stale-revision guard** on R2 `components.json` (ETag/last-modified
   check) to prevent clobbering another user's concurrent edit. v3 performs
   a soft overwrite (C5); revisit if conflicts arise.
6. **Audit trail for partial-save state.** The `r2_save_pending` flag
   lives in autosave only. If the operator runs `sow-admin audio components
   <song_id>` (the rerun path) while a `r2_save_pending=True` autosave exists
   for that song, the rerun's DELETE-then-INSERT path would silently discard
   the user's unsaved R2 overlay. v3 hides this in Open questions; revisit
   with an advisory lock based on autosave existence in the `audio components`
   command.
7. **`--strict-stale-revision` flag.** Promotes C5's soft warning into a
   blocking error if R2's `content_hash` mismatches the recording's content
   hash. Out of scope for v3.
8. **Concurrent editors.** Two operators editing the same song in parallel
   would race the DB UPDATEs (last-writer-wins, but both feel successful).
   R2 overwrite is unguarded. Consider a `song_components_edits`
   advisory-lock table or optimistic `updated_at` CAS for a future version.
