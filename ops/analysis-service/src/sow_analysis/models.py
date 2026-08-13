"""Pydantic models for API requests and responses."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class JobStatus(str, Enum):
    """Job status values."""

    QUEUED = "queued"
    WAITING = "waiting"
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
    FORCED_ALIGNMENT = "forced_alignment"
    FAST_ANALYZE = "fast_analyze"
    COMPONENT_ANALYSIS = "component_analysis"


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


class FastAnalyzeOptions(BaseModel):
    """Options for fast analysis jobs (librosa-only, no allin1/stems)."""

    force: bool = False
    sample_rate: int = 22050
    hop_length: int = 512
    start_bpm: float = Field(default=80.0, ge=40.0, le=200.0)
    lrc_content: Optional[str] = None  # LRC lyrics text for CPS-based prod-v5 prior


class FastAnalyzeJobRequest(BaseModel):
    """Request to submit a fast analysis job.

    Produces only the fast-tier subset: duration_seconds, tempo_bpm,
    musical_key, musical_mode, key_confidence, loudness_db.
    Full-only fields (beats, downbeats, sections, embeddings_shape, stems_url)
    are absent/None on the result.
    """

    audio_url: str
    content_hash: str
    options: FastAnalyzeOptions = Field(default_factory=FastAnalyzeOptions)


class ComponentAnalysisOptions(BaseModel):
    """Options for component analysis jobs."""

    force: bool = False
    use_stems: bool = False  # If True, prefer stems audio for feature extraction
    # v5: madmom downbeat snapping (uses existing downbeats if provided,
    # otherwise runs madmom detection on the audio file)
    snap_to_downbeat: bool = False
    # v5: energy-aware entry/exit role assignment
    energy_aware_roles: bool = False
    # v5: LLM theme classification (12 Chinese themes)
    classify_theme: bool = False
    # v5: LLM vocal posture classification (3 categories)
    classify_vocal_posture: bool = False
    # v6: bypass READING the cached beat grid (re-detect + overwrite).
    # Orthogonal to `force`: `force` invalidates components.json;
    # `skip_beat_cache` invalidates beat_grid.json reads only.
    # Fresh detection still WRITES the beat cache.
    skip_beat_cache: bool = False
    # v6: populate audio-metadata + LLM fields for ALL components.
    # Default False: only essential roles (entry/exit/loop_target/entry_exit)
    # get audio + LLM; non-essential rows are kept but with NULL fields.
    all_components: bool = False
    # v6: use LLM whole-song segmentation (Design C) for identification.
    # Operator sets this to A/B test the LLM path against the
    # lyrics-repetition fallback per song, independent of the
    # SOW_COMPONENTS_USE_LLM_SEGMENTATION env flag (which remains
    # the global default gate). The job option is one-way OR — it can
    # enable per-job but cannot disable when the env flag is on.
    use_llm_segmentation: bool = False


class LrcOptions(BaseModel):
    """Options for LRC generation jobs."""

    model_config = ConfigDict(extra="allow")

    whisper_model: str = "large-v3"
    llm_model: str = (
        ""  # LLM model (e.g., "openai/gpt-4o-mini"), falls back to SOW_LLM_MODEL env var
    )
    use_vocals_stem: bool = True  # Prefer vocals stem for cleaner transcription
    language: Literal["auto", "zh", "en"] = "auto"  # LRC language mode
    force: bool = False  # Re-generate even if cached
    force_whisper: bool = False  # Bypass Whisper transcription cache
    use_qwen3_asr: bool = True  # Use DashScope Qwen3 ASR before Whisper fallback
    force_qwen3_asr: bool = False  # Bypass Qwen3 ASR cache only
    qwen3_asr_context_max_chars: int = 10000
    qwen3_asr_snap_threshold: float = 0.60
    qwen3_asr_min_usable_segments: int = 3

    @model_validator(mode="before")
    @classmethod
    def reject_legacy_fields(cls, values):
        """Reject legacy field names that have been renamed."""
        if isinstance(values, dict):
            if "use_qwen3" in values:
                raise ValueError(
                    "use_qwen3 is a legacy ForcedAligner option and has been removed. "
                    "Use 'use_qwen3_asr' instead."
                )
        return values

    @field_validator("language", mode="before")
    @classmethod
    def validate_language(cls, value: str) -> str:
        if value is None:
            return "auto"
        if value not in {"auto", "zh", "en"}:
            raise ValueError("language must be one of: auto, zh, en")
        return value


class LrcJobRequest(BaseModel):
    """Request to submit an LRC generation job."""

    audio_url: str
    content_hash: str
    lyrics_text: str
    song_title: str = ""
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


class ForcedAlignmentOptions(BaseModel):
    """Options for forced alignment jobs."""

    model_config = ConfigDict(extra="allow")

    language: Literal["auto", "zh", "en"] = "auto"
    force: bool = False
    use_vocals_stem: bool = True


class ForcedAlignmentJobRequest(BaseModel):
    """Request to submit a forced alignment job."""

    audio_url: str
    content_hash: str
    lyrics_text: str
    song_title: str = ""
    options: ForcedAlignmentOptions = Field(default_factory=ForcedAlignmentOptions)


class Section(BaseModel):
    """Music section (verse, chorus, etc.)."""

    label: str
    start: float
    end: float


class ComponentAnalysisJobRequest(BaseModel):
    """Request to submit a component analysis job.

    The hybrid extraction strategy prefers cached allin1 sections first,
    then lyrics-repetition from LRC.

    Callers SHOULD pass cached ``sections``, ``beats``, ``downbeats``, and
    ``lrc_content`` from the DB/R2 to avoid re-computation.

    v6: If ``downbeats`` are absent and ``options.snap_to_downbeat`` is set, the
    worker resolves them via the beat-grid cache (``{hash12}/beat_grid.json``),
    running madmom detection only on cache miss. ``analyze_audio_fast`` is never
    run inline for beats — it does not produce them.
    """

    audio_url: str
    content_hash: str
    song_id: str = ""
    sections: Optional[List[Section]] = None  # Cached allin1 sections
    beats: Optional[List[float]] = None  # Cached beat timestamps
    downbeats: Optional[List[float]] = None  # Cached downbeat timestamps
    lrc_content: Optional[str] = None  # Cached LRC text
    options: ComponentAnalysisOptions = Field(default_factory=ComponentAnalysisOptions)


class ComponentResult(BaseModel):
    """A single identified song component with computed features."""

    component_type: str
    occurrence_index: int = 1
    role: str = "none"
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    bpm: Optional[float] = None
    key: Optional[str] = None
    groove_density: Optional[float] = None
    backbeat_strength: Optional[float] = None
    energy_level: Optional[float] = None
    confidence: Optional[float] = None
    # v5: per-field confidence scores
    bpm_confidence: Optional[float] = None
    key_confidence: Optional[float] = None
    groove_confidence: Optional[float] = None
    backbeat_confidence: Optional[float] = None
    energy_confidence: Optional[float] = None
    # v5: LLM-derived theme and vocal posture
    theme: Optional[str] = None
    vocal_posture: Optional[str] = None
    theme_confidence: Optional[float] = None
    vocal_posture_confidence: Optional[float] = None
    # v5: LLM reasoning fields (for debugging and audit)
    theme_reasoning: Optional[str] = None
    posture_reasoning: Optional[str] = None
    # v6: LLM segmentation fields
    section_label: Optional[str] = None
    lyrics_excerpt: Optional[str] = None
    llm_rationale: Optional[str] = None
    source: str = ""  # 'allin1_sections' | 'lyrics_repetition' | 'llm_segmentation' | 'none'


class JobResult(BaseModel):
    """Result data for a completed job."""

    # Analysis results
    duration_seconds: Optional[float] = None
    tempo_bpm: Optional[float] = None
    musical_key: Optional[str] = None
    musical_mode: Optional[str] = None
    key_confidence: Optional[float] = None
    key_algorithm_version: Optional[str] = None
    key_score_margin: Optional[float] = None
    key_window_agreement: Optional[float] = None
    key_candidates: Optional[str | list[dict]] = None
    key_detected_at: Optional[str] = None
    loudness_db: Optional[float] = None
    beats: Optional[List[float]] = None
    downbeats: Optional[List[float]] = None
    sections: Optional[List[Section]] = None
    embeddings_shape: Optional[List[int]] = None
    stems_url: Optional[str] = None

    # LRC results
    lrc_url: Optional[str] = None
    line_count: Optional[int] = None
    lrc_source: Optional[str] = (
        None  # youtube_transcript, qwen3_asr, whisper_asr, or forced_alignment
    )

    # Stem separation results
    vocals_dry_url: Optional[str] = None  # Stage 2 output (de-reverb/dry)
    vocals_url: Optional[str] = None  # Stage 1 output (raw vocals)
    instrumental_url: Optional[str] = None  # Stage 1 output (instrumental)

    # Component analysis results
    components: Optional[List[ComponentResult]] = None
    component_source: Optional[str] = None


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
    result: Optional[Union[JobResult, "EmbeddingJobResult"]] = None


class EmbeddingJobRequest(BaseModel):
    """Request to submit an embedding job."""

    song_id: str
    title: str
    composer: str = ""
    lyrics_raw: str = ""
    lyrics_lines: List[str] = []
    content_hash: str


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
        ForcedAlignmentJobRequest,
        FastAnalyzeJobRequest,
        "ComponentAnalysisJobRequest",
    ]
    result: Optional[Union[JobResult, EmbeddingJobResult]] = None
    error_message: Optional[str] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    progress: float = 0.0
    stage: str = ""
