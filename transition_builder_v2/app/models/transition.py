"""Transition data models."""
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass
class TransitionRecord:
    """Represents a generated transition with metadata and parameters."""
    id: int
    transition_type: str
    song_a_filename: str
    song_b_filename: str
    section_a_label: str
    section_b_label: str
    compatibility_score: float
    generated_at: datetime
    audio_path: Path
    is_saved: bool = False
    saved_path: Path | None = None
    save_note: str | None = None
    parameters: dict = field(default_factory=dict)
    output_type: str = "transition"  # "transition" or "full_song"
    full_song_path: Path | None = None  # If output_type=="full_song"

    def format_list_display(self) -> str:
        """Format for display in history list."""
        compat_pct = int(self.compatibility_score)
        type_display = self.transition_type.capitalize()
        return f"#{self.id} {type_display}: {self.song_a_filename} → {self.song_b_filename} ({compat_pct}%)"

    def format_time(self) -> str:
        """Format generated time as HH:MM:SS."""
        return self.generated_at.strftime("%H:%M:%S")

    @property
    def status_display(self) -> str:
        """Return status indicator."""
        return "● Saved" if self.is_saved else "○ Temporary"
