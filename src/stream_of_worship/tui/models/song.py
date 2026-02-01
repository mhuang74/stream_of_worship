"""Song data model."""
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional


@dataclass
class Song:
    """Represents a song with metadata and sections."""

    filename: str
    filepath: Path
    duration: float
    tempo: float
    key: str
    mode: str
    key_confidence: float
    full_key: str
    loudness_db: float
    spectral_centroid: float
    sections: List["Section"]

    # Optional fields
    tempo_source: str = "allinone"
    num_beats: int = 0
    beats: List[float] = None
    num_downbeats: int = 0
    downbeats: List[float] = None
    key_source: str = "librosa"
    loudness_std: float = 0.0
    num_sections: int = 0
    section_label_source: str = "allinone_ml"
    embeddings_shape: List = None
    embeddings_mean: float = 0.0
    embeddings_std: float = 0.0
    embeddings_hop_length: int = 512
    embeddings_sr: int = 22050

    # Compatibility score (computed when Song B is selected)
    compatibility_score: float = 0.0

    def __post_init__(self):
        """Initialize default values."""
        if self.beats is None:
            self.beats = []
        if self.downbeats is None:
            self.downbeats = []
        if self.embeddings_shape is None:
            self.embeddings_shape = []
        if self.num_sections == 0:
            self.num_sections = len(self.sections)

    @property
    def id(self) -> str:
        """Return unique identifier for song."""
        return self.filename

    @property
    def display_name(self) -> str:
        """Return formatted display name with BPM and key."""
        bpm = int(self.tempo)
        return f"{self.filename} • {bpm} BPM • {self.full_key}"

    def format_duration(self) -> str:
        """Format duration as MM:SS."""
        minutes = int(self.duration // 60)
        seconds = int(self.duration % 60)
        return f"{minutes}:{seconds:02d}"

    @classmethod
    def from_dict(cls, data: dict) -> "Song":
        """Create Song instance from dictionary.

        Args:
            data: Dictionary containing song data

        Returns:
            Song instance
        """
        from stream_of_worship.tui.models.section import Section

        # Parse sections
        sections = [
            Section(
                label=s.get("label", "unknown"),
                start=s.get("start", 0.0),
                end=s.get("end", 0.0),
                duration=s.get("duration", 0.0)
            )
            for s in data.get("sections", [])
        ]

        return cls(
            filename=data.get("filename", ""),
            filepath=Path(data.get("filepath", "")),
            duration=data.get("duration", 0.0),
            tempo=data.get("tempo", 0.0),
            key=data.get("key", "C"),
            mode=data.get("mode", "major"),
            key_confidence=data.get("key_confidence", 0.0),
            full_key=data.get("full_key", "C major"),
            loudness_db=data.get("loudness_db", 0.0),
            spectral_centroid=data.get("spectral_centroid", 0.0),
            sections=sections,
            tempo_source=data.get("tempo_source", "allinone"),
            num_beats=data.get("num_beats", 0),
            beats=data.get("beats", []),
            num_downbeats=data.get("num_downbeats", 0),
            downbeats=data.get("downbeats", []),
            key_source=data.get("key_source", "librosa"),
            loudness_std=data.get("loudness_std", 0.0),
            num_sections=data.get("num_sections", len(sections)),
            section_label_source=data.get("section_label_source", "allinone_ml"),
            embeddings_shape=data.get("embeddings_shape", []),
            embeddings_mean=data.get("embeddings_mean", 0.0),
            embeddings_std=data.get("embeddings_std", 0.0),
            embeddings_hop_length=data.get("embeddings_hop_length", 512),
            embeddings_sr=data.get("embeddings_sr", 22050)
        )
