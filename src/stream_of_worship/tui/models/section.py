"""Section data model."""
from dataclasses import dataclass


@dataclass
class Section:
    """Represents a section within a song."""

    label: str
    start: float
    end: float
    duration: float

    def format_time(self, seconds: float) -> str:
        """Format seconds as MM:SS."""
        minutes = int(seconds // 60)
        secs = int(seconds % 60)
        return f"{minutes}:{secs:02d}"

    def format_display(self) -> str:
        """Format section for display: 'Chorus (1:23-2:10, 47s)'."""
        start_str = self.format_time(self.start)
        end_str = self.format_time(self.end)
        duration_str = f"{int(self.duration)}s"
        return f"{self.label.capitalize()} ({start_str}-{end_str}, {duration_str})"
