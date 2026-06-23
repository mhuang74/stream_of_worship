"""Song and Section data models."""
from dataclasses import dataclass
from pathlib import Path


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
    sections: list[Section]

    # Optional fields
    tempo_source: str = "allinone"
    num_beats: int = 0
    beats: list[float] = None
    num_downbeats: int = 0
    downbeats: list[float] = None
    key_source: str = "librosa"
    loudness_std: float = 0.0
    num_sections: int = 0
    section_label_source: str = "allinone_ml"
    embeddings_shape: list = None
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
        """Return unique identifier for the song."""
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
        """Create Song instance from dictionary."""
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
