---

# Implementation Plan: Component Metadata Editor TUI (v1)

> **Date:** 2026-08-12
> **Branch:** TBD
> **Spec ID:** `component-metadata-editor-tui-v1`

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
j            jump_to_line           Seek to selected line
s            save_upload            Save
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
   │     • update_song_component_fields(component_id, fields) — NEW
   │
   └── services/r2.py::R2Client
         • download_component_result(hash_prefix) → dict | None — NEW
         • upload_component_result(hash_prefix, payload) → str — NEW
```

**Critical Separation:** Like the LRC editor, the new package must not import
PyTorch / ML libs. All ML is upstream in the Analysis Service.

---

## Phase 0: DB & R2 persistence helpers

**Goal:** Add a targeted single-row UPDATE (so user edits do not lose other
columns) and add R2 read/write helpers for `components.json`.

**Complexity:** S

### 0.1 `ops/admin-cli/src/stream_of_worship/admin/db/client.py`

Add a new method `update_song_component_fields` right after
`get_song_components_entry_exit` (currently ends at line 2118):

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
    ALLOWED = {"theme", "vocal_posture", "groove_density", "energy_level"}
    invalid = set(fields) - ALLOWED
    if invalid:
        raise ValueError(f"Cannot edit non-editable fields: {sorted(invalid)}")
    if not fields:
        return False
    set_clause = ", ".join(f"{col} = %s" for col in fields)
    params: list = list(fields.values()) + [component_id]
    cursor = self.connection.cursor()
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

Mirror the structure of `editor/state.py` (`EditorState` + `UndoEntry`).

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
    field_name: str               # one of EDITABLE_FIELDS
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
    # Working copy of editable field values: keyed by (role, field) → value
    # role ∈ {"entry", "exit"}; field ∈ EDITABLE_FIELDS
    working: dict[tuple[str, str], Any] = field(default_factory=dict)
    dirty: bool = False

    def component_for_role(self, role: str) -> Optional[SongComponent]:
        return self.entry_component if role == "entry" else self.exit_component


@dataclass
class ComponentEditorState:
    """Top-level mutable state for the Component Metadata editor."""

    sessions: list[SongSession]
    current_index: int = 0
    # Per-song undo stacks, indexed by session id() so undo/redo is per-song
    _undo_stacks: dict[int, list[ComponentUndoEntry]] = field(default_factory=dict)
    _redo_stacks: dict[int, list[ComponentUndoEntry]] = field(default_factory=dict)
    selected_row: int = 0            # 0 = entry, 1 = exit
    selected_column_key: str = "role"

    @property
    def current(self) -> SongSession:
        return self.sessions[self.current_index]

    @property
    def current_undo(self) -> list[ComponentUndoEntry]:
        sid = id(self.current)
        return self._undo_stacks.setdefault(sid, [])

    @property
    def current_redo(self) -> list[ComponentUndoEntry]:
        sid = id(self.current)
        return self._redo_stacks.setdefault(sid, [])

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
        key = (entry.component_role, entry.field_name)  # see note below
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
            # Store role too so undo can find the working key:
            component_role=role,
            field_name=field_name,
            old_value=old,
            new_value=value,
        ))
        self.current.working[(role, field_name)] = value
```

Note: `ComponentUndoEntry` above extends with `component_role: str` (compared
to the sketch at top of section). Update the dataclass accordingly — this
simplifies the `undo`/`redo` keying.

### 2.4 `component_editor/autosave.py`

Mirrors `editor/autosave.py`. One autosave file per song:

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

    def to_dict(self) -> dict:
        return {
            "song_id": self.song_id,
            "hash_prefix": self.hash_prefix,
            "working": self.working,
            "dirty": self.dirty,
            "selected_row": self.selected_row,
            "selected_column_key": self.selected_column_key,
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
        # Atomic write: tmp file in same dir, then rename.
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

**Goal:** Commit dirty edits on the current song to DB + R2, then clear
autosave and dirty flag.

**Complexity:** M

### 6.1 `action_save`

```python
def action_save(self) -> None:
    session = self.state.current
    if not session.dirty:
        self.app.bell()
        return
    # 1. Collect dirty edits: { (role, field): new_value } from session.working
    # 2. Group by component (entry / exit) → list of (component_id, {field: value})
    updates_by_role: dict[str, dict[str, Any]] = {"entry": {}, "exit": {}}
    for (role, field), value in session.working.items():
        updates_by_role[role][field] = value

    # 3. Write DB (targeted UPDATE per component, single transaction)
    try:
        with self.db_client.transaction() as conn:
            cursor = conn.cursor()
            for role, fields in updates_by_role.items():
                comp = session.component_for_role(role)
                if comp is None or comp.id is None or not fields:
                    continue
                # Inline target UPDATE (mirror of Phase 0 method; or call
                # self.db_client.update_song_component_fields(comp.id, fields))
                set_clause = ", ".join(f"{c} = %s" for c in fields)
                params = list(fields.values()) + [comp.id]
                cursor.execute(
                    f"UPDATE song_components SET {set_clause} WHERE id = %s",
                    params,
                )
    except Exception as e:
        self._notify(f"[red]DB save failed: {e}[/]")
        return

    # 4. Write R2 components.json (merge into existing)
    self._save_r2_component_result(session, updates_by_role)

    # 5. Clear dirty + autosave
    session.working.clear()
    session.dirty = False
    # Reflect persisted values into the in-memory SongComponent objects
    self._reload_components_from_db(session)
    clear_autosave(self.cache_dir, session.hash_prefix)
    self._update_status()
    self._refresh_table()
    self._notify("[green]Saved (DB + R2).[/]")
```

### 6.2 `action_save` — R2 merge

```python
def _save_r2_component_result(
    self, session: SongSession, updates_by_role: dict[str, dict[str, Any]]
) -> None:
    hash_prefix = session.hash_prefix
    payload = self.r2_client.download_component_result(hash_prefix)
    if payload is None:
        # First-time write: synthesise a minimal payload from the in-memory
        # SongComponent objects (entry + exit). Include schema_version = 2.
        payload = {
            "schema_version": COMPONENT_SCHEMA_VERSION,
            "content_hash": session.entry_component.content_hash
                or session.exit_component.content_hash
                or "",
            "hash_prefix": hash_prefix,
            "component_source": "user_review_components",
            "components": [],
        }
        for role in ("entry", "exit"):
            comp = session.component_for_role(role)
            if comp is None:
                continue
            payload["components"].append(comp.to_dict())

    # Merge the 4 editable fields into matching component dicts
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
        self._notify(
            f"[yellow]DB saved but R2 components.json upload failed: {e}[/]"
        )
        # Keep dirty so user knows to retry on next song switch
        session.dirty = True
```

### 6.3 Stale-revision guard (optional)

If R2 returns a `components.json` whose `content_hash` differs from the
session's `hash_prefix`-derived content hash, log a warning but still write.
This is a soft guard — out of scope for v1 unless trivial.

---

## Phase 7: Autosave & undo/redo loop

**Goal:** Wire autosave into the editing lifecycle.

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
    )
    ok = save_autosave(self.cache_dir, snapshot)
    self._autosave_ok = ok
    self._update_status()
    return ok
```

### 7.2 `_maybe_apply_autosave` (on mount)

If `load_autosave(cache_dir, session.hash_prefix)` returns a snapshot whose
`dirty=True`, push a `AutosaveRecoveryDialog` (modal, mirroring
`SaveUploadDialog` pattern in `editor/screen.py`):
- "r" = recover — apply working edits to state, set dirty.
- "d" = discard — clear autosave file.

### 7.3 Autosave triggers

Call `_do_autosave()` after every state mutation:
- `action_cycle_field_prev` / `action_cycle_field_next`
- `on_input_submitted` (numeric edit)
- `action_undo` / `actionredo`

Call `clear_autosave` only inside `action_save` after a successful commit.

### 7.4 Undo / redo wiring

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

On `action_save`: clear the undo + redo stacks for the saved session
(`state._undo_stacks[id(session)].clear()` and same for redo).

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

**Goal:** Mirror the LRC editor test surface.

**Complexity:** M

New test files:

| File | Coverage |
|---|---|
| `tests/admin/component_editor/test_state.py` | `ComponentEditorState.set_value` / `undo` / `redo` / `push_undo` (>100) / multi-session independence; `SongSession.dirty` propagation |
| `tests/admin/component_editor/test_autosave.py` | `save_autosave` ↔ `load_autosave` round-trip; corrupt file → None; `clear_autosave` no-op safety |
| `tests/admin/component_editor/test_screen.py` | Textual Pilot: launch with a fake R2Client + DatabaseClient (monkeypatch) and 3 mocked SongSessions; cycling `[` / `]` changes theme; `e` overlay accepts numeric; `s` saves → DB target UPDATE called + R2 upload called + autosave cleared; `n` switches song + reloads PlaybackService.load; `q` with dirty pushes confirm dialog |
| `tests/admin/test_audio_commands.py` additions | `review-components` rejects unknown song_id; warns + skips song with no entry/exit components; launches app if ≥ 1 valid |
| `tests/admin/services/test_r2_component_result.py` | `R2Client.download_component_result` 404 → None; happy path → parsed dict; `upload_component_result` calls `put_object` with the right key + body + content_type; payload round-trip equality |
| `tests/admin/test_db_client.py` additions | `update_song_component_fields(component_id, {"theme": "敬拜"})` updates 1 row; rejects unknown字段; rejects multi-row UPDATE on wrong id (rowcount=0) |

Use `unittest.mock` for `PlaybackService` in screen tests (don't bind a real audio device).

Snapshot tests for DB call args (asserts the targeted UPDATE uses `WHERE id = %s`).

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
  + update_song_component_fields(component_id, fields) method (~25 LOC)

ops/admin-cli/src/stream_of_worship/admin/services/r2.py
  + download_component_result(hash_prefix) method (~20 LOC)
  + upload_component_result(hash_prefix, payload) method (~12 LOC)

ops/admin-cli/tests/admin/test_db_client.py
  + test_update_song_component_fields_* (≈3 tests)

ops/admin-cli/tests/admin/test_audio_commands.py
  + test_review_components_unknown_song, test_review_components_no_components,
    test_review_components_launches_app
```

### Reused (unchanged)
```
ops/admin-cli/src/stream_of_worship/admin/editor/footer.py      (GroupedFooter)
ops/admin-cli/src/stream_of_worship/admin/services/playback.py  (PlaybackService)
ops/admin-cli/src/stream_of_worship/admin/db/models.py          (SongComponent)
ops/admin-cli/src/stream_of_worship/admin/db/schema.py          (no schema changes)
```

## LOC estimate

| Component | LOC |
|---|---|
| `constants.py` | ~70 |
| `state.py` | ~150 |
| `autosave.py` | ~120 |
| `app.py` | ~50 |
| `screen.py` | ~900 |
| `commands/audio.py` addition | ~80 |
| `db/client.py` addition | ~25 |
| `services/r2.py` addition | ~35 |
| Tests | ~600 |
| **Total** | **~2030** |

---

## Open questions / future work (out of scope for v1)

1. **Bulk edit across songs** — apply the same theme correction to a
   selected set of songs in one keystroke. Not needed for v1.
2. **Theme-reasoning regeneration** — when a user changes `theme`, should
   the editor prompt to clear `theme_reasoning` (which now reflects the
   LLM's rationale for the wrong value)? v1 leaves reasoning untouched.
3. **Confidence re-bump** — should editing a field zero out its
   `*_confidence`? v1 does NOT touch confidences (user said those are
   LLM-derived and not editable; the editor leaves them as the LLM left
   them).
4. **Audit trail** — no audit table for who edited what / when. If requested,
   add a `song_components_edits` table keyed by `component_id` with old/new
   values and editor identity. Out of scope for v1.
5. **Stale-revision guard** on R2 `components.json` (ETag/last-modified
   check) to prevent clobbering another user's concurrent edit. v1 performs
   a soft overwrite; revisit if conflicts arise.
