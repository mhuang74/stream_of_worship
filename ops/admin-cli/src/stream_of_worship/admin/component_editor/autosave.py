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
from typing import Any

logger = logging.getLogger(__name__)

AUTOSAVE_FILENAME = "components.autosave.json"


@dataclass
class ComponentAutosaveState:
    """State captured in the autosave recovery file."""

    song_id: str
    hash_prefix: str
    # list of {role, field, value} for any dirty working edits
    working: list[dict[str, Any]] = field(default_factory=list)
    dirty: bool = False
    selected_row: int = 0
    selected_column_key: str = "role"
    # Round-trip partial-save status so next launch warns user that R2 still
    # needs retry.
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


def load_autosave(cache_dir: Path, hash_prefix: str) -> ComponentAutosaveState | None:
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
        fd, tmp_path = tempfile.mkstemp(prefix=".autosave-", suffix=".json", dir=str(path.parent))
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
