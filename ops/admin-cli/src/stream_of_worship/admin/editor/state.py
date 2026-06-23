"""Editor state model for the admin LRC editor.

Holds all mutable editing session data: lyric rows, preserved content,
transcribed session token, dirty tracking, and source mode.
"""

from dataclasses import dataclass, field
from typing import List, Optional

from stream_of_worship.admin.services.lrc_parser import (
    LRCLine,
    LRCPreservedLine,
    serialize_lrc,
)
from stream_of_worship.admin.services.r2 import R2ObjectIdentity

_MAX_UNDO = 100
PADDING_QUARTERS_MIN = -8
PADDING_QUARTERS_MAX = 8


@dataclass
class UndoEntry:
    action: str
    index: int
    old_text: str = ""
    new_text: str = ""
    old_time: float = 0.0
    new_time: float = 0.0
    line: Optional[LRCLine] = None
    lines: Optional[List[LRCLine]] = None
    old_padding_quarters: Optional[int] = None
    new_padding_quarters: Optional[int] = None


@dataclass
class EditorState:
    """Mutable editing session state for the LRC editor.

    Attributes:
        timed_lines: Editable timed lyric rows
        preserved_lines: Non-editable preserved content
        original_serialized: Force-refreshed original LRC for diff base
        original_preserved_lines: Preserved lines from original for drop detection
        transcribed_identity: Session token for stale-session detection of the transcribed LRC on R2
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
    transcribed_identity: R2ObjectIdentity
    dirty: bool = False
    source_mode: str = "catalog"
    selected_index: int = 0
    song_title: str = ""
    hash_prefix: str = ""
    audio_path: Optional[str] = None
    audio_duration: Optional[float] = None
    tempo_bpm: Optional[float] = None
    padding_quarters: int = 0
    original_timestamps: List[float] = field(default_factory=list)
    _undo_stack: List[UndoEntry] = field(default_factory=list)
    _redo_stack: List[UndoEntry] = field(default_factory=list)

    def __post_init__(self):
        if not self.original_timestamps:
            self.original_timestamps = [line.time_seconds for line in self.timed_lines]

    @property
    def line_count(self) -> int:
        return len(self.timed_lines)

    @property
    def selected_line(self) -> Optional[LRCLine]:
        if 0 <= self.selected_index < len(self.timed_lines):
            return self.timed_lines[self.selected_index]
        return None

    @property
    def padding_offset_seconds(self) -> float:
        if self.tempo_bpm and self.tempo_bpm > 0:
            quarter_beat = 60.0 / (self.tempo_bpm * 4)
        else:
            quarter_beat = 0.2
        return self.padding_quarters * quarter_beat

    def _push_undo(self, entry: UndoEntry) -> None:
        self._undo_stack.append(entry)
        if len(self._undo_stack) > _MAX_UNDO:
            self._undo_stack.pop(0)
        self._redo_stack.clear()

    def adjust_padding(self, delta_quarters: int) -> bool:
        new_quarters = self.padding_quarters + delta_quarters
        if new_quarters < PADDING_QUARTERS_MIN or new_quarters > PADDING_QUARTERS_MAX:
            return False

        old_quarters = self.padding_quarters
        self.padding_quarters = new_quarters
        new_offset = self.padding_offset_seconds

        for i, line in enumerate(self.timed_lines):
            if i < len(self.original_timestamps):
                line.time_seconds = max(0.0, self.original_timestamps[i] + new_offset)

        self._push_undo(UndoEntry(
            action="adjust_padding",
            index=0,
            old_padding_quarters=old_quarters,
            new_padding_quarters=self.padding_quarters,
        ))
        self.dirty = True
        return True

    def set_timestamp(self, index: int, time_seconds: float) -> None:
        if 0 <= index < len(self.timed_lines):
            raw_time = max(0.0, time_seconds - self.padding_offset_seconds)
            if index < len(self.original_timestamps):
                self.original_timestamps[index] = raw_time
            else:
                while len(self.original_timestamps) <= index:
                    self.original_timestamps.append(0.0)
                self.original_timestamps[index] = raw_time

            adjusted_time = max(0.0, raw_time + self.padding_offset_seconds)

            old_time = self.timed_lines[index].time_seconds
            self._push_undo(UndoEntry(
                action="set_timestamp", index=index,
                old_time=old_time, new_time=adjusted_time,
            ))
            self.timed_lines[index].time_seconds = adjusted_time
            self.dirty = True

    def set_text(self, index: int, text: str) -> None:
        """Set the text for a lyric line."""
        if 0 <= index < len(self.timed_lines):
            old_text = self.timed_lines[index].text
            self._push_undo(UndoEntry(
                action="set_text", index=index,
                old_text=old_text, new_text=text,
            ))
            self.timed_lines[index].text = text
            self.dirty = True

    def insert_after(self, index: int, text: str = "", time_seconds: float = 0.0) -> None:
        new_line = LRCLine(time_seconds=time_seconds, text=text, raw_timestamp="[00:00.00]")
        insert_at = index + 1
        self._push_undo(UndoEntry(action="insert", index=insert_at, line=new_line))
        self.timed_lines.insert(insert_at, new_line)
        raw_ts = time_seconds - self.padding_offset_seconds
        if insert_at < len(self.original_timestamps):
            self.original_timestamps.insert(insert_at, raw_ts)
        else:
            while len(self.original_timestamps) <= insert_at:
                self.original_timestamps.append(0.0)
            self.original_timestamps[insert_at] = raw_ts
        self.dirty = True

    def insert_before(self, index: int, text: str = "", time_seconds: float = 0.0) -> None:
        new_line = LRCLine(time_seconds=time_seconds, text=text, raw_timestamp="[00:00.00]")
        self._push_undo(UndoEntry(action="insert", index=index, line=new_line))
        self.timed_lines.insert(index, new_line)
        raw_ts = time_seconds - self.padding_offset_seconds
        if index < len(self.original_timestamps):
            self.original_timestamps.insert(index, raw_ts)
        else:
            while len(self.original_timestamps) <= index:
                self.original_timestamps.append(0.0)
            self.original_timestamps[index] = raw_ts
        self.dirty = True

    def insert_lines_after(self, index: int, texts: List[str]) -> None:
        new_lines = [
            LRCLine(time_seconds=0.0, text=text, raw_timestamp="[00:00.00]")
            for text in texts
        ]
        insert_at = index + 1
        self._push_undo(UndoEntry(action="insert_lines", index=insert_at, lines=new_lines))
        for i, line in enumerate(new_lines):
            self.timed_lines.insert(insert_at + i, line)
        raw_ts = 0.0 - self.padding_offset_seconds
        for i in range(len(new_lines)):
            if insert_at + i < len(self.original_timestamps):
                self.original_timestamps.insert(insert_at + i, raw_ts)
            else:
                self.original_timestamps.append(raw_ts)
        self.dirty = True

    def delete_line(self, index: int) -> Optional[LRCLine]:
        if 0 <= index < len(self.timed_lines):
            deleted = self.timed_lines.pop(index)
            self._push_undo(UndoEntry(action="delete", index=index, line=deleted))
            if index < len(self.original_timestamps):
                self.original_timestamps.pop(index)
            self.dirty = True
            if self.selected_index >= len(self.timed_lines) and self.selected_index > 0:
                self.selected_index = len(self.timed_lines) - 1
            return deleted
        return None

    def undo(self) -> bool:
        if not self._undo_stack:
            return False
        entry = self._undo_stack.pop()
        if entry.action == "set_text":
            self.timed_lines[entry.index].text = entry.old_text
        elif entry.action == "set_timestamp":
            self.timed_lines[entry.index].time_seconds = entry.old_time
        elif entry.action == "insert":
            if 0 <= entry.index < len(self.timed_lines):
                self.timed_lines.pop(entry.index)
                if entry.index < len(self.original_timestamps):
                    self.original_timestamps.pop(entry.index)
        elif entry.action == "insert_lines":
            if entry.lines is not None:
                for _ in range(len(entry.lines)):
                    if 0 <= entry.index < len(self.timed_lines):
                        self.timed_lines.pop(entry.index)
                    if entry.index < len(self.original_timestamps):
                        self.original_timestamps.pop(entry.index)
        elif entry.action == "delete":
            if entry.line is not None:
                self.timed_lines.insert(entry.index, entry.line)
                raw_ts = entry.line.time_seconds - self.padding_offset_seconds
                if entry.index < len(self.original_timestamps):
                    self.original_timestamps.insert(entry.index, raw_ts)
                else:
                    while len(self.original_timestamps) <= entry.index:
                        self.original_timestamps.append(0.0)
                    self.original_timestamps[entry.index] = raw_ts
        elif entry.action == "adjust_padding":
            self.padding_quarters = entry.old_padding_quarters if entry.old_padding_quarters is not None else 0
            new_offset = self.padding_offset_seconds
            for i, line in enumerate(self.timed_lines):
                if i < len(self.original_timestamps):
                    line.time_seconds = max(0.0, self.original_timestamps[i] + new_offset)
        self.selected_index = min(entry.index, len(self.timed_lines) - 1)
        self.dirty = True
        self._redo_stack.append(entry)
        return True

    def redo(self) -> bool:
        if not self._redo_stack:
            return False
        entry = self._redo_stack.pop()
        if entry.action == "set_text":
            self.timed_lines[entry.index].text = entry.new_text
        elif entry.action == "set_timestamp":
            self.timed_lines[entry.index].time_seconds = entry.new_time
        elif entry.action == "insert":
            if entry.line is not None:
                self.timed_lines.insert(entry.index, entry.line)
                raw_ts = entry.line.time_seconds - self.padding_offset_seconds
                if entry.index < len(self.original_timestamps):
                    self.original_timestamps.insert(entry.index, raw_ts)
                else:
                    while len(self.original_timestamps) <= entry.index:
                        self.original_timestamps.append(0.0)
                    self.original_timestamps[entry.index] = raw_ts
        elif entry.action == "insert_lines":
            if entry.lines is not None:
                for i, line in enumerate(entry.lines):
                    self.timed_lines.insert(entry.index + i, line)
                    raw_ts = line.time_seconds - self.padding_offset_seconds
                    if entry.index + i < len(self.original_timestamps):
                        self.original_timestamps.insert(entry.index + i, raw_ts)
                    else:
                        self.original_timestamps.append(raw_ts)
        elif entry.action == "delete":
            if 0 <= entry.index < len(self.timed_lines):
                self.timed_lines.pop(entry.index)
                if entry.index < len(self.original_timestamps):
                    self.original_timestamps.pop(entry.index)
        elif entry.action == "adjust_padding":
            self.padding_quarters = entry.new_padding_quarters if entry.new_padding_quarters is not None else 0
            new_offset = self.padding_offset_seconds
            for i, line in enumerate(self.timed_lines):
                if i < len(self.original_timestamps):
                    line.time_seconds = max(0.0, self.original_timestamps[i] + new_offset)
        self.selected_index = min(entry.index, len(self.timed_lines) - 1)
        self.dirty = True
        self._undo_stack.append(entry)
        return True

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
