"""Editor state model for the admin LRC editor.

Holds all mutable editing session data: lyric rows, preserved content,
canonical session token, dirty tracking, and source mode.
"""

from dataclasses import dataclass
from typing import List, Optional

from stream_of_worship.admin.services.lrc_parser import (
    LRCLine,
    LRCPreservedLine,
    serialize_lrc,
)
from stream_of_worship.admin.services.r2 import R2ObjectIdentity


@dataclass
class EditorState:
    """Mutable editing session state for the LRC editor.

    Attributes:
        timed_lines: Editable timed lyric rows
        preserved_lines: Non-editable preserved content
        original_serialized: Force-refreshed original LRC for diff base
        original_preserved_lines: Preserved lines from original for drop detection
        canonical_identity: Session token for stale-session detection
        dirty: Whether there are unsaved changes since last save/upload
        source_mode: How the editor was initialized ("r2" or "catalog")
        selected_index: Currently selected lyric line index
        song_title: Song title for display
        hash_prefix: Recording hash prefix
        audio_path: Path to cached audio file
        audio_duration: Audio duration in seconds
    """

    timed_lines: List[LRCLine]
    preserved_lines: List[LRCPreservedLine]
    original_serialized: str
    original_preserved_lines: List[LRCPreservedLine]
    canonical_identity: R2ObjectIdentity
    dirty: bool = False
    source_mode: str = "catalog"
    selected_index: int = 0
    song_title: str = ""
    hash_prefix: str = ""
    audio_path: Optional[str] = None
    audio_duration: Optional[float] = None

    @property
    def line_count(self) -> int:
        return len(self.timed_lines)

    @property
    def selected_line(self) -> Optional[LRCLine]:
        if 0 <= self.selected_index < len(self.timed_lines):
            return self.timed_lines[self.selected_index]
        return None

    def set_timestamp(self, index: int, time_seconds: float) -> None:
        """Set the timestamp for a lyric line."""
        if 0 <= index < len(self.timed_lines):
            self.timed_lines[index].time_seconds = max(0.0, time_seconds)
            self.dirty = True

    def set_text(self, index: int, text: str) -> None:
        """Set the text for a lyric line."""
        if 0 <= index < len(self.timed_lines):
            self.timed_lines[index].text = text
            self.dirty = True

    def insert_after(self, index: int, text: str = "", time_seconds: float = 0.0) -> None:
        """Insert a new line after the given index."""
        new_line = LRCLine(time_seconds=time_seconds, text=text, raw_timestamp="[00:00.00]")
        self.timed_lines.insert(index + 1, new_line)
        self.dirty = True

    def insert_before(self, index: int, text: str = "", time_seconds: float = 0.0) -> None:
        """Insert a new line before the given index."""
        new_line = LRCLine(time_seconds=time_seconds, text=text, raw_timestamp="[00:00.00]")
        self.timed_lines.insert(index, new_line)
        self.dirty = True

    def delete_line(self, index: int) -> Optional[LRCLine]:
        """Delete the line at the given index. Returns the deleted line."""
        if 0 <= index < len(self.timed_lines):
            deleted = self.timed_lines.pop(index)
            self.dirty = True
            if self.selected_index >= len(self.timed_lines) and self.selected_index > 0:
                self.selected_index = len(self.timed_lines) - 1
            return deleted
        return None

    def select_line(self, index: int) -> None:
        """Select a lyric line by index, clamping to valid range."""
        self.selected_index = max(0, min(index, len(self.timed_lines) - 1))

    def select_next(self) -> None:
        """Select the next line."""
        self.select_line(self.selected_index + 1)

    def select_prev(self) -> None:
        """Select the previous line."""
        self.select_line(self.selected_index - 1)

    def serialize(self) -> str:
        """Serialize current state to LRC format."""
        return serialize_lrc(self.timed_lines, self.preserved_lines)
