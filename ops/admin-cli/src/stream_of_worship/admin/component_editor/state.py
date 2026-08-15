"""State model for the admin Component Metadata editor.

Holds the list of song sessions (one per passed song_id), the current song
index, the entry+exit SongComponent rows for the current song, the dirty /
undo / redo state, and autosave snapshot helpers.
"""

from dataclasses import dataclass, field
from typing import Any

from stream_of_worship.admin.component_editor.lrc_fetch import LRCFetch
from stream_of_worship.admin.db.models import Song, SongComponent
from stream_of_worship.admin.services.lrc_parser import LRCParsedContent

_MAX_UNDO = 100


@dataclass
class ComponentUndoEntry:
    """One reversible field-level edit on a song_components row."""

    component_id: int
    component_role: str  # "entry" | "exit"
    field_name: str  # one of EDITABLE_FIELDS
    old_value: Any
    new_value: Any


@dataclass
class SongSession:
    """Per-song runtime state within the editor."""

    song_id: str
    song_title: str
    hash_prefix: str
    audio_path: str
    audio_duration: float | None
    # v7: all essential components (entry, exit, verse1, bridge)
    components: dict[str, SongComponent | None] = field(default_factory=dict)
    # Legacy accessors for backward compat (first_content_hash, R2 payload synthesis)
    entry_component: SongComponent | None = None
    exit_component: SongComponent | None = None
    # Full Song object for the detail panel (title, artist, album, etc.).
    # None when the song was not loaded (e.g. legacy callers).
    song: Song | None = None
    # Working copy of editable field values: keyed by (role, field) -> value
    # role in {"entry", "exit", "verse1", "bridge"}; field in EDITABLE_FIELDS
    working: dict[tuple[str, str], Any] = field(default_factory=dict)
    dirty: bool = False
    # Indicates the last save partially failed (DB committed, R2 did not).
    # Surfaced in the status indicator; cleared on next successful full Save.
    # Does NOT block further edits — the user may edit more fields before
    # retrying 's'.
    r2_save_pending: bool = False

    def __post_init__(self) -> None:
        """Sync legacy fields from components dict if not explicitly set."""
        if self.components:
            if self.entry_component is None and "entry" in self.components:
                self.entry_component = self.components["entry"]
            if self.exit_component is None and "exit" in self.components:
                self.exit_component = self.components["exit"]
        else:
            # Backward compat: if components is empty but legacy fields are set,
            # populate components from them.
            if self.entry_component is not None:
                self.components["entry"] = self.entry_component
            if self.exit_component is not None:
                self.components["exit"] = self.exit_component

    def component_for_role(self, editor_role: str) -> SongComponent | None:
        """Return the component for an editor-level role key."""
        return self.components.get(editor_role)

    @property
    def ordered_component_roles(self) -> list[str]:
        """Roles that have a non-None component, in canonical display order."""
        from stream_of_worship.admin.component_editor.constants import (
            ESSENTIAL_COMPONENT_SLOTS,
        )

        return [
            role
            for role, _, _ in ESSENTIAL_COMPONENT_SLOTS
            if self.components.get(role) is not None
        ]


@dataclass
class ComponentEditorState:
    """Top-level mutable state for the Component Metadata editor."""

    sessions: list[SongSession]
    current_index: int = 0
    # C4 fix: keyed by session.song_id (stable PK string), NOT id(session).
    _undo_stacks: dict[str, list[ComponentUndoEntry]] = field(default_factory=dict)
    _redo_stacks: dict[str, list[ComponentUndoEntry]] = field(default_factory=dict)
    selected_row: int = 0  # 0 = entry, 1 = exit
    selected_column_key: str = "role"

    # v5: LRC fetch + parsed content per song
    lrc_fetches: dict[str, LRCFetch] = field(default_factory=dict)
    lrc_parsed: dict[str, LRCParsedContent | None] = field(default_factory=dict)
    lrc_prefetch_in_progress: bool = False
    lrc_fetch_error: str | None = None  # global pre-fetch error, if any

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

    def undo(self) -> ComponentUndoEntry | None:
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

    def redo(self) -> ComponentUndoEntry | None:
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
        """Clear both the undo and redo stacks for ``session``.

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
        self.push_undo(
            ComponentUndoEntry(
                component_id=comp.id or 0,
                component_role=role,
                field_name=field_name,
                old_value=old,
                new_value=value,
            )
        )
        self.current.working[(role, field_name)] = value

    def get_selected_component(self) -> SongComponent | None:
        """Return the SongComponent that the currently-highlighted table row
        points at, using dynamic role lookup based on the session's component
        list.
        """
        roles = self.current.ordered_component_roles
        if not roles:
            return None
        idx = max(0, min(self.selected_row, len(roles) - 1))
        return self.current.component_for_role(roles[idx])
