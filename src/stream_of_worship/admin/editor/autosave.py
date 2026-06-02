"""Autosave recovery for the admin LRC editor.

Maintains an autosave recovery file so dirty edits survive process
crashes, terminal disconnects, and accidental exits. The autosave
includes enough state to restore lyric rows, preserved content,
transcribed session token, dirty status, and source mode.
"""

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from stream_of_worship.admin.services.lrc_parser import LRCLine, LRCPreservedLine
from stream_of_worship.admin.services.r2 import R2ObjectIdentity

logger = logging.getLogger(__name__)

AUTOSAVE_FILENAME = "lyrics.autosave.json"


@dataclass
class AutosaveState:
    """State captured in the autosave recovery file.

    Attributes:
        timed_lines: Editable timed lyric rows
        preserved_lines: Non-editable preserved content
        transcribed_identity: Session token for stale-session detection of the transcribed LRC on R2
        dirty: Whether there are unsaved changes
        source_mode: How the editor was initialized ("r2" or "catalog")
    """

    timed_lines: List[LRCLine]
    preserved_lines: List[LRCPreservedLine]
    transcribed_identity: R2ObjectIdentity
    dirty: bool
    source_mode: str

    def to_dict(self) -> dict:
        return {
            "timed_lines": [
                {"time_seconds": line.time_seconds, "text": line.text}
                for line in self.timed_lines
            ],
            "preserved_lines": [
                {"raw": p.raw, "tag": p.tag, "value": p.value}
                for p in self.preserved_lines
            ],
            "transcribed_identity": {
                "exists": self.transcribed_identity.exists,
                "etag": self.transcribed_identity.etag,
                "last_modified": self.transcribed_identity.last_modified,
            },
            "dirty": self.dirty,
            "source_mode": self.source_mode,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AutosaveState":
        timed_lines = [
            LRCLine(time_seconds=item["time_seconds"], text=item["text"], raw_timestamp="[00:00.00]")
            for item in data.get("timed_lines", [])
        ]
        preserved_lines = [
            LRCPreservedLine(raw=p["raw"], tag=p.get("tag"), value=p.get("value"))
            for p in data.get("preserved_lines", [])
        ]
        ti_data = data.get("transcribed_identity") or data.get("canonical_identity", {})
        transcribed_identity = R2ObjectIdentity(
            exists=ti_data.get("exists", False),
            etag=ti_data.get("etag"),
            last_modified=ti_data.get("last_modified"),
        )
        return cls(
            timed_lines=timed_lines,
            preserved_lines=preserved_lines,
            transcribed_identity=transcribed_identity,
            dirty=data.get("dirty", True),
            source_mode=data.get("source_mode", "catalog"),
        )


def get_autosave_path(cache_dir: Path, hash_prefix: str) -> Path:
    """Get the autosave recovery file path.

    Args:
        cache_dir: Admin cache directory
        hash_prefix: 12-character hash prefix

    Returns:
        Path to the autosave JSON file
    """
    return cache_dir / hash_prefix / "lrc" / AUTOSAVE_FILENAME


def autosave_exists(cache_dir: Path, hash_prefix: str) -> bool:
    """Check whether an autosave recovery file exists."""
    return get_autosave_path(cache_dir, hash_prefix).exists()


def load_autosave(cache_dir: Path, hash_prefix: str) -> Optional[AutosaveState]:
    """Load autosave recovery state from disk.

    Returns:
        AutosaveState if a valid autosave exists, None otherwise
    """
    path = get_autosave_path(cache_dir, hash_prefix)
    if not path.exists():
        return None

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return AutosaveState.from_dict(data)
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        logger.warning(f"Failed to load autosave from {path}: {e}")
        return None


def save_autosave(cache_dir: Path, hash_prefix: str, state: AutosaveState) -> Path:
    """Write autosave recovery state to disk.

    Args:
        cache_dir: Admin cache directory
        hash_prefix: 12-character hash prefix
        state: Current editor state to save

    Returns:
        Path to the autosave file
    """
    path = get_autosave_path(cache_dir, hash_prefix)
    path.parent.mkdir(parents=True, exist_ok=True)

    tmp_path = path.with_suffix(".tmp")
    try:
        tmp_path.write_text(json.dumps(state.to_dict(), indent=2), encoding="utf-8")
        tmp_path.replace(path)
    except BaseException:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise

    return path


def clear_autosave(cache_dir: Path, hash_prefix: str) -> None:
    """Remove the autosave recovery file after clean upload or explicit discard."""
    path = get_autosave_path(cache_dir, hash_prefix)
    if path.exists():
        path.unlink()
