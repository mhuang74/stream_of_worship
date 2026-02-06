"""Data models for sow-admin database entities.

Provides dataclasses for Song and Recording entities with serialization
to/from database rows.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional


@dataclass
class Song:
    """Song catalog entry scraped from sop.org.

    Attributes:
        id: Unique song ID (e.g., "song_0001")
        title: Song title in Chinese
        title_pinyin: Pinyin representation of title
        composer: Composer name
        lyricist: Lyricist name
        album_name: Album name
        album_series: Album series (e.g., "敬拜讚美15")
        musical_key: Musical key (e.g., "G", "D")
        lyrics_raw: Raw lyrics text
        lyrics_lines: JSON array of lyric lines
        sections: JSON array of song sections
        source_url: URL where song was scraped
        table_row_number: Row number in source table
        scraped_at: ISO timestamp when scraped
        created_at: ISO timestamp when created
        updated_at: ISO timestamp when last updated
    """

    id: str
    title: str
    source_url: str
    scraped_at: str
    title_pinyin: Optional[str] = None
    composer: Optional[str] = None
    lyricist: Optional[str] = None
    album_name: Optional[str] = None
    album_series: Optional[str] = None
    musical_key: Optional[str] = None
    lyrics_raw: Optional[str] = None
    lyrics_lines: Optional[str] = None
    sections: Optional[str] = None
    table_row_number: Optional[int] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    @classmethod
    def from_row(cls, row: tuple) -> "Song":
        """Create a Song from a database row tuple.

        Args:
            row: Database row tuple with columns in schema order

        Returns:
            Song instance
        """
        return cls(
            id=row[0],
            title=row[1],
            title_pinyin=row[2],
            composer=row[3],
            lyricist=row[4],
            album_name=row[5],
            album_series=row[6],
            musical_key=row[7],
            lyrics_raw=row[8],
            lyrics_lines=row[9],
            sections=row[10],
            source_url=row[11],
            table_row_number=row[12],
            scraped_at=row[13],
            created_at=row[14],
            updated_at=row[15],
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert Song to dictionary.

        Returns:
            Dictionary representation of the song
        """
        return {
            "id": self.id,
            "title": self.title,
            "title_pinyin": self.title_pinyin,
            "composer": self.composer,
            "lyricist": self.lyricist,
            "album_name": self.album_name,
            "album_series": self.album_series,
            "musical_key": self.musical_key,
            "lyrics_raw": self.lyrics_raw,
            "lyrics_lines": self.lyrics_lines,
            "sections": self.sections,
            "source_url": self.source_url,
            "table_row_number": self.table_row_number,
            "scraped_at": self.scraped_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @property
    def lyrics_list(self) -> list[str]:
        """Get lyrics as a list of lines.

        Returns:
            List of lyric lines
        """
        if self.lyrics_lines:
            try:
                return json.loads(self.lyrics_lines)
            except json.JSONDecodeError:
                pass
        if self.lyrics_raw:
            return self.lyrics_raw.strip().split("\n")
        return []


@dataclass
class Recording:
    """Audio recording with hash-based identification.

    Attributes:
        content_hash: Full SHA-256 hash (64 chars)
        hash_prefix: First 12 chars of hash (R2 directory ID)
        song_id: Reference to songs.id (optional)
        original_filename: Original filename when imported
        file_size_bytes: File size in bytes
        imported_at: ISO timestamp when imported
        r2_audio_url: R2 URL for audio file
        r2_stems_url: R2 URL prefix for stems directory
        r2_lrc_url: R2 URL for LRC file
        duration_seconds: Audio duration
        tempo_bpm: Detected tempo
        musical_key: Detected key
        musical_mode: Detected mode (major/minor)
        key_confidence: Key detection confidence
        loudness_db: Loudness in dB
        beats: JSON array of beat timestamps
        downbeats: JSON array of downbeat timestamps
        sections: JSON array of section objects
        embeddings_shape: JSON array of embedding dimensions
        analysis_status: pending/processing/completed/failed
        analysis_job_id: Analysis service job ID
        lrc_status: pending/processing/completed/failed
        lrc_job_id: LRC service job ID
        created_at: ISO timestamp when created
        updated_at: ISO timestamp when last updated
    """

    content_hash: str
    hash_prefix: str
    original_filename: str
    file_size_bytes: int
    imported_at: str
    song_id: Optional[str] = None
    r2_audio_url: Optional[str] = None
    r2_stems_url: Optional[str] = None
    r2_lrc_url: Optional[str] = None
    duration_seconds: Optional[float] = None
    tempo_bpm: Optional[float] = None
    musical_key: Optional[str] = None
    musical_mode: Optional[str] = None
    key_confidence: Optional[float] = None
    loudness_db: Optional[float] = None
    beats: Optional[str] = None
    downbeats: Optional[str] = None
    sections: Optional[str] = None
    embeddings_shape: Optional[str] = None
    analysis_status: str = "pending"
    analysis_job_id: Optional[str] = None
    lrc_status: str = "pending"
    lrc_job_id: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    @classmethod
    def from_row(cls, row: tuple) -> "Recording":
        """Create a Recording from a database row tuple.

        Args:
            row: Database row tuple with columns in schema order

        Returns:
            Recording instance
        """
        return cls(
            content_hash=row[0],
            hash_prefix=row[1],
            song_id=row[2],
            original_filename=row[3],
            file_size_bytes=row[4],
            imported_at=row[5],
            r2_audio_url=row[6],
            r2_stems_url=row[7],
            r2_lrc_url=row[8],
            duration_seconds=row[9],
            tempo_bpm=row[10],
            musical_key=row[11],
            musical_mode=row[12],
            key_confidence=row[13],
            loudness_db=row[14],
            beats=row[15],
            downbeats=row[16],
            sections=row[17],
            embeddings_shape=row[18],
            analysis_status=row[19],
            analysis_job_id=row[20],
            lrc_status=row[21],
            lrc_job_id=row[22],
            created_at=row[23],
            updated_at=row[24],
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert Recording to dictionary.

        Returns:
            Dictionary representation of the recording
        """
        return {
            "content_hash": self.content_hash,
            "hash_prefix": self.hash_prefix,
            "song_id": self.song_id,
            "original_filename": self.original_filename,
            "file_size_bytes": self.file_size_bytes,
            "imported_at": self.imported_at,
            "r2_audio_url": self.r2_audio_url,
            "r2_stems_url": self.r2_stems_url,
            "r2_lrc_url": self.r2_lrc_url,
            "duration_seconds": self.duration_seconds,
            "tempo_bpm": self.tempo_bpm,
            "musical_key": self.musical_key,
            "musical_mode": self.musical_mode,
            "key_confidence": self.key_confidence,
            "loudness_db": self.loudness_db,
            "beats": self.beats,
            "downbeats": self.downbeats,
            "sections": self.sections,
            "embeddings_shape": self.embeddings_shape,
            "analysis_status": self.analysis_status,
            "analysis_job_id": self.analysis_job_id,
            "lrc_status": self.lrc_status,
            "lrc_job_id": self.lrc_job_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @property
    def has_analysis(self) -> bool:
        """Check if analysis is complete.

        Returns:
            True if analysis_status is 'completed'
        """
        return self.analysis_status == "completed"

    @property
    def has_lrc(self) -> bool:
        """Check if LRC generation is complete.

        Returns:
            True if lrc_status is 'completed'
        """
        return self.lrc_status == "completed"

    @property
    def beats_list(self) -> list[float]:
        """Get beats as a list of floats.

        Returns:
            List of beat timestamps
        """
        if self.beats:
            try:
                return json.loads(self.beats)
            except json.JSONDecodeError:
                pass
        return []

    @property
    def formatted_duration(self) -> str:
        """Get duration formatted as MM:SS.

        Returns:
            Formatted duration string
        """
        if self.duration_seconds is None:
            return "--:--"
        minutes = int(self.duration_seconds // 60)
        seconds = int(self.duration_seconds % 60)
        return f"{minutes}:{seconds:02d}"


@dataclass
class DatabaseStats:
    """Statistics about the database state.

    Attributes:
        table_counts: Dictionary of table names to row counts
        integrity_ok: Whether integrity check passed
        foreign_keys_enabled: Whether foreign keys are enabled
        last_sync_at: Last sync timestamp (if any)
        sync_version: Schema version for sync compatibility
        local_device_id: Unique identifier for this device
        turso_configured: Whether Turso sync is configured
    """

    table_counts: dict[str, int] = field(default_factory=dict)
    integrity_ok: bool = True
    foreign_keys_enabled: bool = False
    last_sync_at: Optional[str] = None
    sync_version: str = "1"
    local_device_id: str = ""
    turso_configured: bool = False

    @property
    def total_songs(self) -> int:
        """Get total number of songs.

        Returns:
            Number of songs in the database
        """
        return self.table_counts.get("songs", 0)

    @property
    def total_recordings(self) -> int:
        """Get total number of recordings.

        Returns:
            Number of recordings in the database
        """
        return self.table_counts.get("recordings", 0)
