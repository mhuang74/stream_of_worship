"""Pydantic models for API requests and responses."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional, Union

from pydantic import BaseModel, Field


class JobStatus(str, Enum):
    """Job status values."""

    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobType(str, Enum):
    """Job type values."""

    ANALYZE = "analyze"
    LRC = "lrc"
    STEM_SEPARATION = "stem_separation"
    EMBEDDING = "embedding"


class AnalyzeOptions(BaseModel):
    """Options for analysis jobs."""

    generate_stems: bool = True
    stem_model: str = "htdemucs"
    force: bool = False


class AnalyzeJobRequest(BaseModel):
    """Request to submit an analysis job."""

    audio_url: str
    content_hash: str
    options: AnalyzeOptions = Field(default_factory=AnalyzeOptions)


class LrcOptions(BaseModel):
    """Options for LRC generation jobs."""

    whisper_model: str = "large-v3"
    llm_model: str = (
        ""  # LLM model (e.g., "openai/gpt-4o-mini"), falls back to SOW_LLM_MODEL env var
    )
    use_vocals_stem: bool = True  # Prefer vocals stem for cleaner transcription
    language: str = "zh"  # Whisper language hint
    force: bool = False  # Re-generate even if cached
    force_whisper: bool = False  # Bypass Whisper transcription cache
    use_qwen3: bool = True  # Use Qwen3 service for timestamp refinement
    max_qwen3_duration: int = 300  # 5 minutes in seconds (Qwen3 service limit)


class LrcJobRequest(BaseModel):
    """Request to submit an LRC generation job."""

    audio_url: str
    content_hash: str
    lyrics_text: str
    youtube_url: str = ""  # YouTube URL for transcript-based LRC (primary path)
    options: LrcOptions = Field(default_factory=LrcOptions)


class StemSeparationOptions(BaseModel):
    """Options for stem separation jobs."""

    force: bool = False  # Re-generate even if cached
    dereverb_model: str = "UVR-De-Echo-Normal.pth"  # Model for echo/reverb removal


class StemSeparationJobRequest(BaseModel):
    """Request to submit a stem separation job."""

    audio_url: str
    content_hash: str
    options: StemSeparationOptions = Field(default_factory=StemSeparationOptions)


class Section(BaseModel):
    """Music section (verse, chorus, etc.)."""

    label: str
    start: float
    end: float


class JobResult(BaseModel):
    """Result data for a completed job."""

    # Analysis results
    duration_seconds: Optional[float] = None
    tempo_bpm: Optional[float] = None
    musical_key: Optional[str] = None
    musical_mode: Optional[str] = None
    key_confidence: Optional[float] = None
    loudness_db: Optional[float] = None
    beats: Optional[List[float]] = None
    downbeats: Optional[List[float]] = None
    sections: Optional[List[Section]] = None
    embeddings_shape: Optional[List[int]] = None
    stems_url: Optional[str] = None

    # LRC results
    lrc_url: Optional[str] = None
    line_count: Optional[int] = None
    lrc_source: Optional[str] = None  # "youtube_transcript" or "whisper_asr"

    # Stem separation results
    vocals_dry_url: Optional[str] = None  # Stage 2 output (de-reverb/dry)
    vocals_url: Optional[str] = None  # Stage 1 output (raw vocals)
    instrumental_url: Optional[str] = None  # Stage 1 output (instrumental)


class JobResponse(BaseModel):
    """Response containing job status and results."""

    job_id: str
    status: JobStatus
    job_type: JobType
    created_at: datetime
    updated_at: datetime
    progress: float = 0.0
    stage: str = ""
    error_message: Optional[str] = None
    warning: Optional[str] = None
    result: Optional[JobResult] = None


class EmbeddingJobRequest(BaseModel):
    """Request to submit an embedding job."""

    song_id: str
    title: str
    composer: str = ""
    lyrics_raw: str = ""
    lyrics_lines: List[str] = []


class LineEmbedding(BaseModel):
    """Embedding for a single lyric line."""

    line_index: int
    line_text: str
    embedding: List[float]


class EmbeddingJobResult(BaseModel):
    """Result data for a completed embedding job."""

    song_id: str
    embedding: List[float]
    line_embeddings: List[LineEmbedding]
    model_version: str = "text-embedding-3-small"
    content_hash: str


@dataclass
class Job:
    """Represents a job in the queue."""

    id: str
    type: JobType
    status: JobStatus
    request: Union[
        AnalyzeJobRequest,
        LrcJobRequest,
        StemSeparationJobRequest,
        EmbeddingJobRequest,
    ]
    result: Optional[Union[JobResult, EmbeddingJobResult]] = None
    error_message: Optional[str] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    progress: float = 0.0
    stage: str = ""
