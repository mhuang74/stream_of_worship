"""Data models for sow-admin database entities.

Provides dataclasses for Song and Recording entities with serialization
to/from database rows.
"""

import json
from dataclasses import dataclass, field
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
    deleted_at: Optional[str] = None

    @classmethod
    def from_row(cls, row: tuple, description: tuple) -> "Song":
        """Create a Song from a database row tuple.

        Args:
            row: Database row tuple.
            description: Column descriptions from cursor.description. Required.

        Returns:
            Song instance

        Note:
            Maps columns by name rather than position, making this robust
            to schema changes and column order variations.
        """
        col_names = [desc[0] for desc in description]
        values = dict(zip(col_names, row))
        return cls(
            id=values.get("id", ""),
            title=values.get("title", ""),
            title_pinyin=values.get("title_pinyin"),
            composer=values.get("composer"),
            lyricist=values.get("lyricist"),
            album_name=values.get("album_name"),
            album_series=values.get("album_series"),
            musical_key=values.get("musical_key"),
            lyrics_raw=values.get("lyrics_raw"),
            lyrics_lines=values.get("lyrics_lines"),
            sections=values.get("sections"),
            source_url=values.get("source_url", ""),
            table_row_number=values.get("table_row_number"),
            scraped_at=values.get("scraped_at", ""),
            created_at=values.get("created_at"),
            updated_at=values.get("updated_at"),
            deleted_at=values.get("deleted_at"),
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
            "deleted_at": self.deleted_at,
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
    youtube_url: Optional[str] = None
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
    visibility_status: Optional[str] = None
    download_status: str = "pending"
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    deleted_at: Optional[str] = None

    @classmethod
    def from_row(cls, row: tuple, description: tuple) -> "Recording":
        """Create a Recording from a database row tuple.

        Args:
            row: Database row tuple.
            description: Column descriptions from cursor.description. Required.

        Returns:
            Recording instance

        Note:
            Maps columns by name rather than position, making this robust
            to schema changes and column order variations.
        """
        col_names = [desc[0] for desc in description]
        values = dict(zip(col_names, row))
        return cls(
            content_hash=values.get("content_hash", ""),
            hash_prefix=values.get("hash_prefix", ""),
            song_id=values.get("song_id"),
            original_filename=values.get("original_filename", ""),
            file_size_bytes=values.get("file_size_bytes", 0),
            imported_at=values.get("imported_at", ""),
            r2_audio_url=values.get("r2_audio_url"),
            r2_stems_url=values.get("r2_stems_url"),
            r2_lrc_url=values.get("r2_lrc_url"),
            youtube_url=values.get("youtube_url"),
            duration_seconds=values.get("duration_seconds"),
            tempo_bpm=values.get("tempo_bpm"),
            musical_key=values.get("musical_key"),
            musical_mode=values.get("musical_mode"),
            key_confidence=values.get("key_confidence"),
            loudness_db=values.get("loudness_db"),
            beats=values.get("beats"),
            downbeats=values.get("downbeats"),
            sections=values.get("sections"),
            embeddings_shape=values.get("embeddings_shape"),
            analysis_status=values.get("analysis_status", "pending"),
            analysis_job_id=values.get("analysis_job_id"),
            lrc_status=values.get("lrc_status", "pending"),
            lrc_job_id=values.get("lrc_job_id"),
            visibility_status=values.get("visibility_status"),
            deleted_at=values.get("deleted_at"),
            download_status=values.get("download_status", "pending"),
            created_at=values.get("created_at"),
            updated_at=values.get("updated_at"),
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
            "youtube_url": self.youtube_url,
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
            "visibility_status": self.visibility_status,
            "download_status": self.download_status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "deleted_at": self.deleted_at,
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
    def is_published(self) -> bool:
        """Check if the recording is published for user visibility.

        Returns:
            True if visibility_status is 'published'
        """
        return self.visibility_status == "published"

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
    active_counts: dict[str, int] = field(default_factory=dict)
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

    @property
    def active_songs(self) -> int:
        return self.active_counts.get("songs", 0)

    @property
    def active_recordings(self) -> int:
        return self.active_counts.get("recordings", 0)
