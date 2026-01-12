"""
Song and Section data models.

These classes represent song metadata and sections loaded from
the poc_output_allinone JSON files.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional
import math


@dataclass
class Section:
    """
    Represents a song section (verse, chorus, bridge, etc.).

    Attributes:
        song_filename: Parent song filename
        index: Section index within song
        label: Section type (verse, chorus, bridge, intro, outro, inst, etc.)
        start: Start time in seconds
        end: End time in seconds
        duration: Duration in seconds
        tempo: Section-specific BPM
        key: Musical key (e.g., "D major", "G minor")
        energy_score: Energy rating 0-100
        loudness_db: Average loudness in dB
        spectral_centroid: Brightness measure in Hz
    """

    song_filename: str
    index: int
    label: str
    start: float
    end: float
    duration: float
    tempo: float
    key: str
    energy_score: float
    loudness_db: float
    spectral_centroid: float

    def __str__(self) -> str:
        """Return human-readable string representation."""
        return f"[{self.index}] {self.label.capitalize()} ({self.start:.1f}s - {self.end:.1f}s)"

    def get_time_range_str(self) -> str:
        """Return formatted time range string."""
        return f"{self.start:.1f}s - {self.end:.1f}s"

    def get_duration_str(self) -> str:
        """Return formatted duration string."""
        return f"{self.duration:.1f}s"

    def calculate_tempo_compatibility(self, other: 'Section') -> float:
        """
        Calculate tempo compatibility score with another section.

        Args:
            other: Another Section to compare with

        Returns:
            Compatibility score 0-100
        """
        tempo_diff_pct = abs(self.tempo - other.tempo) / ((self.tempo + other.tempo) / 2) * 100

        # Score calculation (from analyze_sections.py)
        if tempo_diff_pct <= 5:
            score = 100
        elif tempo_diff_pct <= 10:
            score = 90 - (tempo_diff_pct - 5) * 2
        elif tempo_diff_pct <= 20:
            score = 80 - (tempo_diff_pct - 10) * 3
        else:
            score = max(0, 50 - (tempo_diff_pct - 20) * 2)

        return round(score, 1)

    def calculate_key_compatibility(self, other: 'Section') -> float:
        """
        Calculate key compatibility score with another section.

        Args:
            other: Another Section to compare with

        Returns:
            Compatibility score 0-100
        """
        # Simple key matching (could be enhanced with circle of fifths)
        if self.key == other.key:
            return 100.0
        elif self.key.split()[0] == other.key.split()[0]:  # Same root note
            return 80.0
        else:
            return 60.0  # Different keys

    def calculate_energy_compatibility(self, other: 'Section') -> float:
        """
        Calculate energy compatibility score with another section.

        Args:
            other: Another Section to compare with

        Returns:
            Compatibility score 0-100
        """
        energy_diff = abs(self.energy_score - other.energy_score)

        # Score calculation
        if energy_diff <= 5:
            score = 100
        elif energy_diff <= 10:
            score = 90 - (energy_diff - 5)
        elif energy_diff <= 20:
            score = 85 - (energy_diff - 10) * 1.5
        else:
            score = max(50, 70 - (energy_diff - 20))

        return round(score, 1)

    def calculate_compatibility(self, other: 'Section') -> dict:
        """
        Calculate overall compatibility with another section.

        Uses weights from section_features.json:
        - Tempo: 25%
        - Key: 25%
        - Energy: 15%
        - Embeddings: 35% (not calculated here, set to 0)

        Args:
            other: Another Section to compare with

        Returns:
            Dictionary with component scores and overall score
        """
        tempo_score = self.calculate_tempo_compatibility(other)
        key_score = self.calculate_key_compatibility(other)
        energy_score = self.calculate_energy_compatibility(other)

        # Weights from section_features.json configuration
        weights = {
            'tempo': 0.25,
            'key': 0.25,
            'energy': 0.15,
            'embeddings': 0.35
        }

        # Overall score (embeddings set to 75.0 as placeholder)
        embeddings_score = 75.0
        overall = (
            tempo_score * weights['tempo'] +
            key_score * weights['key'] +
            energy_score * weights['energy'] +
            embeddings_score * weights['embeddings']
        )

        return {
            'overall_score': round(overall, 1),
            'tempo_score': tempo_score,
            'key_score': key_score,
            'energy_score': energy_score,
            'embeddings_score': embeddings_score,
            'tempo_diff_pct': round(
                abs(self.tempo - other.tempo) / ((self.tempo + other.tempo) / 2) * 100,
                1
            ),
            'energy_diff': round(abs(self.energy_score - other.energy_score), 1)
        }


@dataclass
class Song:
    """
    Represents a worship song with metadata and sections.

    Attributes:
        filename: Song filename (e.g., "do_it_again.mp3")
        filepath: Full path to original audio file
        duration: Total duration in seconds
        tempo: Average BPM
        key: Musical key
        loudness_db: Average loudness
        spectral_centroid: Brightness measure
        sections: List of Section objects
    """

    filename: str
    filepath: Path
    duration: float
    tempo: float
    key: str
    loudness_db: float
    spectral_centroid: float
    sections: List[Section] = field(default_factory=list)

    def __str__(self) -> str:
        """Return human-readable string representation."""
        return f"{self.filename} ({self.get_duration_str()})"

    def get_duration_str(self) -> str:
        """Return formatted duration string (MM:SS)."""
        minutes = int(self.duration // 60)
        seconds = int(self.duration % 60)
        return f"{minutes}:{seconds:02d}"

    def get_metadata_summary(self) -> str:
        """Return one-line metadata summary for UI."""
        return f"Key: {self.key} | BPM: {self.tempo:.1f} | Duration: {self.get_duration_str()}"

    def get_section(self, index: int) -> Optional[Section]:
        """
        Get section by index.

        Args:
            index: Section index

        Returns:
            Section object or None if not found
        """
        for section in self.sections:
            if section.index == index:
                return section
        return None

    def get_sections_by_label(self, label: str) -> List[Section]:
        """
        Get all sections with matching label.

        Args:
            label: Section label (e.g., "chorus", "verse")

        Returns:
            List of matching Section objects
        """
        return [s for s in self.sections if s.label.lower() == label.lower()]

    def get_section_labels(self) -> List[str]:
        """Get unique section labels in this song."""
        return list(set(s.label for s in self.sections))
