---

# Implementation Plan: Component Metadata Editor TUI (v2)

> **Date:** 2026-08-12
> **Branch:** TBD
> **Spec ID:** `component-metadata-editor-tui-v2`
> **Supersedes:** `component-metadata-editor-tui-v1`
> **Status:** Review Fix Pass — corrects Production-blocking issues found
> in the v1 save flow, state model, and DB helper.

---

## Changelog (v2 vs v1)

This is a **delta document**. Unchanged sections (Problem, Goal, Design
Decisions, LRC Editor Reference, Phase 1, Phase 3, Phase 4, Phase 5,
Phase 8, Phase 9, File inventory, LOC estimate, Open questions) are
unchanged from v1 — see `specs/component-metadata-editor-tui-v1.md`.

The sections below **replace** the corresponding v1 sections. Read the rest
from v1.

### Issues addressed

| ID | Severity | v1 location | Issue | v2 fix |
|---|---|---|---|---|
| B1 | HIGH | `action_save` lines 925-946 | Unconditionally clears `working`/`dirty`/autosave even when R2 upload inside `_save_r2_component_result` failed. The `session.dirty = True` set in R2 failure branch is overwritten; in-memory edits + on-disk autosave are gone → retry impossible. | Phase 6.1 rewritten: `_save_r2_component_result` returns `bool` success status. `action_save` only clears state when BOTH DB and R2 succeeded. On R2 failure: keeps `working` + `dirty` + autosave, surfaces a yellow "Saved DB only — R2 failed — press s to retry" status, does NOT clear undo/redo stacks. Retry re-runs the same idempotent DB UPDATE + R2 merge. |
| B2 | HIGH | `_save_r2_component_result` line 962 | `session.entry_component.content_hash or session.exit_component.content_hash or ""` raises `AttributeError` when `entry_component is None` (partial-analysis case is explicitly supported in Phase 1). | Phase 6.2: introduce None-safe helper `first_content_hash(session)` that picks whichever side is non-None (both reference recording.content_hash so either is canonical). |
| B3 | LOW | `action_save` Phase 6 body | Doesn't clear undo/redo stacks, contradicting Phase 7.4 ("On `action_save`: clear the undo + redo stacks for the saved session"). Post-save `ctrl+z` would re-apply a value whose working entry was cleared, silently re-dirtying. | Phase 6.1: on full success, call new `state.clear_undo_stacks()` which clears both stacks for the current session (Phase 7.4 already specified). |
| C1 | HIGH | `action_save` try/except (lines 916-933) | Only wraps the DB block. A `download_component_result` exception inside `_save_r2_component_result` propagates past the cleanup branch, leaving state half-committed (DB written, state indeterminate). | Phase 6.2: `_save_r2_component_result` wraps BOTH the R2 download AND upload in a single try/except. Network blip on download is treated as a retryable R2 failure (returns `False`) rather than an unhandled exception. |
| C2 | HIGH | `action_save` inline UPDATE (lines 925-930) | Builds `UPDATE song_components SET ...` inline from `session.working` keys, bypassing the Phase 0 `ALLOWED` whitelist safeguard. | Phase 0.1: add transaction-accepting sibling `update_song_component_fields_txn(conn, component_id, fields)` that performs the same ALLOWED validation. Phase 6.1: `action_save` calls the whitelisted helper inside `with self.db_client.transaction() as conn:` — no inline SQL in the screen. |
| C3 | HIGH | `action_save` line 942 | Calls `self._reload_components_from_db(session)` which is never defined in v1. | Phase 6.4: explicit spec — calls `db.get_song_components_entry_exit(session.song_id)`, replaces `session.entry_component` / `exit_component` with fresh `SongComponent` objects containing persisted (post-edit) values. |
| C4 | MED | `_undo_stacks` keyed by `id(session)` (state.py lines 427-428, 438-439) | Python's `id()` can theoretically be reused after GC. Safe today (sessions list is built once in `commands/audio.py` and never rebuilt), but brittle across refactor. | Phase 2.3: switch dict keys to `session.song_id` (stable PK). `current_undo` / `current_redo` look up `self.current.song_id`. |
| C5 | LOW | Phase 6.3 stale-revision guard | Marked "optional / out of scope for v1". For Production, a soft warning is cheap. | Phase 6.5: soft `content_hash` consistency check — if R2 `components.json` exists with a `content_hash` field that differs from the current song's recording `content_hash` (lookup via `db.get_recording_by_song_id`), log a warning and inject a banner in the save toast. Does not block the save. |

---

## Phase 0: DB & R2 persistence helpers (REPLACES v1 Phase 0)

**Goal:** Add a targeted single-row UPDATE that:

1. validates the editable-field whitelist, and
2. accepts a caller-supplied connection so multiple per-component UPDATEs
   can run inside one transaction.

Add R2 read/write helpers for `components.json` (unchanged from v1 §0.2).

**Complexity:** S

### 0.1 `ops/admin-cli/src/stream_of_worship/admin/db/client.py`

Add **two** new methods right after `get_song_components_entry_exit`
(currently ends at line 2118).

#### 0.1.1 `update_song_component_fields` (thin wrapper, no transaction)

Identical to v1 §0.1. Kept for non-transactional callers (tests, scripts
that update a single component row).

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

Unchanged from v1 §0.2 — see `specs/component-metadata-editor-tui-v1.md`
lines 180-219. Both `download_component_result` and
`upload_component_result` signatures/behaviours preserved.

---

## Phase 2: State, autosave, undo/redo model (PARTIAL REPLACEMENT)

**Goal:** Pure-data models — no Textual imports. Pattern matches
`editor/state.py` and `editor/autosave.py`.

**Complexity:** M

### 2.1 New package `component_editor/`

Unchanged from v1 §2.1.

### 2.2 `component_editor/constants.py`

Unchanged from v1 §2.2.

### 2.3 `component_editor/state.py` (REPLACES v1 §2.3)

Changes from v1:
- **C4 fix:** `_undo_stacks` / `_redo_stacks` are now keyed by `song_id`
  (a stable PK string), not `id(session)`.
- **B3 fix:** Add `clear_undo_stacks(session)` helper used by `action_save`
  on full success.
- `ComponentUndoEntry` keeps `component_role: str` (derives the working key
  on undo/redo — carried forward from v1's inline note at lines 505-507).

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

### 2.4 `component_editor/autosave.py` (PARTIAL REPLACEMENT)

Same structure as v1 §2.4, with **one new field on `ComponentAutosaveState`**
used to round-trip the `r2_save_pending` flag (added in §2.3). Without this
field, a partial-save-then-crash would be silently forgotten on next launch.

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
    # (unchanged from v1 — see v1 §2.4 lines 582-603)
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

### 2.5 `_maybe_apply_autosave` recovery dialog (CLARIFICATION)

When `load_autosave(cache_dir, session.hash_prefix)` returns a snapshot whose
`dirty=True` OR `r2_save_pending=True`:

- If `r2_save_pending=True`: recovery banner is shown as
  *"Recovered N edits — DB committed but R2 still pending — press `s` to retry"*.
  Apply working edits to state, set `session.dirty=True` and
  `session.r2_save_pending=True`.
- Else (normal dirty recovery): standard LRC-editor-style
  `AutosaveRecoveryDialog` with "r" recover / "d" discard.

---

## Phase 6: Save flow (REPLACES v1 Phase 6)

**Goal:** Commit dirty edits on the current song to DB + R2, with a
reliable partial-failure path: DB and R2 cannot be made atomic (R2 has no
real transaction), but the editor must remain in a retryable state whenever
R2 fails AFTER DB succeeds.

**Complexity:** M

### 6.1 `action_save` (REWRITTEN)

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

### 6.2 `_save_r2_component_result` (REWRITTEN)

Key changes from v1:
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

### 6.3 Stale-revision guard (REPLACES v1 §6.3 — now SOFT warning)

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

A `--strict-stale-revision` flag (out of scope for v2; tracked in Open
questions) may later promote this to a hard error.

### 6.4 `_reload_components_from_db` (NEW — C3 fix)

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

## Phase 7: Autosave & undo/redo loop (PARTIAL REPLACEMENT)

### 7.1 `_do_autosave` (UPDATED)

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

### 7.3 Autosave triggers (unchanged from v1 §7.3)

Call `_do_autosave()` after every state mutation:
- `action_cycle_field_prev` / `action_cycle_field_next`
- `on_input_submitted` (numeric edit)
- `action_undo` / `action_redo`

Call `clear_autosave` only inside `action_save` **on full success** (both DB
and R2). The v1 unconditional clear is removed — see Phase 6.1 step 5.

### 7.4 Undo / redo wiring (UNCHANGED on bodies; aligned with Phase 6.1)

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

## Test additions for v2 (Phase 9 additions)

In addition to v1's test surface, add:

| File | New test |
|---|---|
| `tests/admin/component_editor/test_state.py` | `clear_undo_stacks` clears both stacks for the named session and leaves other sessions intact (multi-song fixture). |
| `tests/admin/component_editor/test_state.py` | undo/redo keyed by `song_id` survives removing & re-adding a `SongSession` with the same `song_id` (regression for C4). |
| `tests/admin/component_editor/test_autosave.py` | `r2_save_pending` survives a to_dict/from_dict round-trip (regression for B1). |
| `tests/admin/component_editor/test_screen.py` | **B1 regression:** save with DB ok + R2 upload raising → asserts `session.dirty` is `True`, `session.working` is untouched, autosave file still exists with `r2_save_pending=True`, status shows retry message, undo stacks NOT cleared. |
| `tests/admin/component_editor/test_screen.py` | **B2 regression:** save first-time R2 payload when `session.entry_component=None` and `session.exit_component` is set → asserts no `AttributeError`, payload `content_hash` is the exit component's hash. Symmetric case for the inverse. |
| `tests/admin/component_editor/test_screen.py` | **C1 regression (download):** `download_component_result` raising `ClientError` → save returns False, DB committed (already), state preserved for retry. |
| `tests/admin/component_editor/test_screen.py` | **C2 regression:** assert `action_save` calls `db_client.update_song_component_fields_txn` (not inline SQL); passing `{"bpm": 120}` into `state.set_value` is prevented because `EDITABLE_FIELDS` is enforced upstream, but a malformed `working` dict with a non-ALLOWED key raises `ValueError` from the DB helper at save time. |
| `tests/admin/component_editor/test_screen.py` | **C3 regression:** after full-success save, `session.entry_component` and `session.exit_component` are freshly-fetched instances (different `id()`) whose `theme` field matches the saved value. |
| `tests/admin/component_editor/test_screen.py` | **B3 regression:** after full-success save, `ctrl+z` rings the bell (undo stack empty) and does not re-dirty the session. |
| `tests/admin/test_db_client.py` | `update_song_component_fields_txn` existing-conn variant: same validation behaviour as the wrapper; refuses unknown fields; committed in caller's transaction (rolled back if caller raises after the UPDATE). |

---

## Verification matrix (issues → fixes)

| v1 Issue | Severity | v2 Section | Resolution |
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

## Open questions / future work (extends v1)

Same as v1 §"Open questions", plus:

6. **Audit trail for partial-save state.** The new `r2_save_pending` flag
   lives in autosave only. If the operator runs `sow-admin audio components
   <song_id>` (the rerun path) while a `r2_save_pending=True` autosave exists
   for that song, the rerun's DELETE-then-INSERT path would silently discard
   the user's unsaved R2 overlay. v2 hides this in Open questions; revisit
   with an advisory lock based on autosave existence in the `audio components`
   command.

7. **`--strict-stale-revision` flag.** Promotes C5's soft warning into a
   blocking error if R2's `content_hash` mismatches the recording's content
   hash. Out of scope for v2.

8. **Concurrent editors.** Two operators editing the same song in parallel
   would race the DB UPDATEs (last-writer-wins, but both feel successful).
   R2 overwrite is unguarded v1/v2. Consider a `song_components_edits`
   advisory-lock table or optimistic `updated_at` CAS for v3.
