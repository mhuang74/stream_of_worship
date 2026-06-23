"""Transition data models."""
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional


@dataclass
class TransitionParams:
    """Parameters for generating a transition between two songs."""

    # Transition type: "gap", "crossfade", "xbeat", etc.
    transition_type: str = "gap"

    # Gap/Crossfade parameters (in beats)
    gap_beats: float = 1.0  # Silence duration for gap transitions
    overlap: float = 4.0  # Overlap duration for crossfade transitions

    # Fade parameters
    fade_window: float = 8.0  # Total fade window in beats
    fade_bottom: float = 0.33  # Minimum volume during fade (0.0 to 1.0)

    # Stem configuration: which stems to fade (default: all except vocals)
    stems_to_fade: list[str] = field(default_factory=lambda: ["bass", "drums", "other"])

    # Section boundary adjustments (in beats, range: -4 to +4)
    from_section_start_adjust: int = 0
    from_section_end_adjust: int = 0
    to_section_start_adjust: int = 0
    to_section_end_adjust: int = 0

    @property
    def is_gap(self) -> bool:
        """Check if this is a gap transition."""
        return self.transition_type == "gap"

    @property
    def is_crossfade(self) -> bool:
        """Check if this is a crossfade transition."""
        return self.transition_type == "crossfade"

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "type": self.transition_type,
            "gap_beats": self.gap_beats,
            "overlap": self.overlap,
            "fade_window": self.fade_window,
            "fade_bottom": self.fade_bottom,
            "stems_to_fade": self.stems_to_fade,
            "from_section_start_adjust": self.from_section_start_adjust,
            "from_section_end_adjust": self.from_section_end_adjust,
            "to_section_start_adjust": self.to_section_start_adjust,
            "to_section_end_adjust": self.to_section_end_adjust,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TransitionParams":
        """Create from dictionary."""
        return cls(
            transition_type=data.get("type", "gap"),
            gap_beats=data.get("gap_beats", 1.0),
            overlap=data.get("overlap", 4.0),
            fade_window=data.get("fade_window", 8.0),
            fade_bottom=data.get("fade_bottom", 0.33),
            stems_to_fade=data.get("stems_to_fade", ["bass", "drums", "other"]),
            from_section_start_adjust=data.get("from_section_start_adjust", 0),
            from_section_end_adjust=data.get("from_section_end_adjust", 0),
            to_section_start_adjust=data.get("to_section_start_adjust", 0),
            to_section_end_adjust=data.get("to_section_end_adjust", 0),
        )


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
    saved_path: Optional[Path] = None
    save_note: Optional[str] = None
    parameters: dict = field(default_factory=dict)
    output_type: str = "transition"  # "transition" or "full_song"
    full_song_path: Optional[Path] = None  # If output_type=="full_song"

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
