"""
Transition type definitions based on PDF concepts.

Three primary methods from the Worship Transitions handbook:
1. Overlap (Intro Overlap): Last note of Song A overlaps with intro of Song B
2. Short Gap: Brief pause between songs to "clear the air"
3. No Break: Continuous beat, seamless flow
"""

from enum import Enum


class TransitionType(Enum):
    """
    Enumeration of transition types based on PDF handbook.

    Attributes:
        OVERLAP: Intro Overlap - Last note of first song overlaps with
                 introduction to second song
        SHORT_GAP: Short Gap - Brief moment of silence between songs
        NO_BREAK: No Break - Beat continues constantly without break
    """

    OVERLAP = "overlap"
    SHORT_GAP = "short_gap"
    NO_BREAK = "no_break"

    def __str__(self) -> str:
        """Return human-readable name."""
        return self.value.replace('_', ' ').title()

    @classmethod
    def from_string(cls, value: str) -> 'TransitionType':
        """
        Create TransitionType from string value.

        Args:
            value: String representation (e.g., "overlap", "short_gap")

        Returns:
            TransitionType enum value

        Raises:
            ValueError: If value doesn't match any transition type
        """
        value = value.lower().strip()
        for transition_type in cls:
            if transition_type.value == value:
                return transition_type
        raise ValueError(f"Unknown transition type: {value}")

    @property
    def display_name(self) -> str:
        """Return formatted display name for UI."""
        names = {
            TransitionType.OVERLAP: "Overlap (Intro Overlap)",
            TransitionType.SHORT_GAP: "Short Gap",
            TransitionType.NO_BREAK: "No Break"
        }
        return names[self]

    @property
    def description(self) -> str:
        """Return description of transition type."""
        descriptions = {
            TransitionType.OVERLAP:
                "Last note of Song A overlaps with intro of Song B",
            TransitionType.SHORT_GAP:
                "Brief silence gap between songs to 'clear the air'",
            TransitionType.NO_BREAK:
                "Continuous beat - songs flow together seamlessly"
        }
        return descriptions[self]
