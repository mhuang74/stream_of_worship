"""Pydantic models for API requests and responses."""

from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class JobStatus(str, Enum):
    """Job status values."""

    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class JobType(str, Enum):
    """Job type values."""

    ANALYZE = "analyze"
    LRC = "lrc"


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
    llm_model: str = ""  # LLM model (e.g., "openai/gpt-4o-mini"), falls back to SOW_LLM_MODEL env var
    use_vocals_stem: bool = True  # Prefer vocals stem for cleaner transcription
    language: str = "zh"  # Whisper language hint
    force: bool = False  # Re-generate even if cached


class LrcJobRequest(BaseModel):
    """Request to submit an LRC generation job."""

    audio_url: str
    content_hash: str
    lyrics_text: str
    options: LrcOptions = Field(default_factory=LrcOptions)


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
    result: Optional[JobResult] = None
