"""Data models for the agentic songset constructor POC."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


Phase = Literal["Praise", "Thanksgiving", "Worship", "Response", "Sending"]


class ConstructorConfig(BaseModel):
    songs: int = 5
    top_k: int = 3
    pool_limit: int = 200
    output_dir: str = "lab/poc-scripts/output/songset_constructor"
    album_series: str | None = None
    include_dev: bool = True
    include_cpw: bool = False
    intimate: bool = False
    hymnal_mode: bool = False
    season: str | None = None
    interactive_review: bool = False
    resume_thread_id: str | None = None
    llm_model: str | None = None
    no_llm: bool = False


class SongCandidate(BaseModel):
    song_id: str
    title: str
    recording_hash_prefix: str
    bpm: float
    musical_key: str
    musical_mode: str = "major"
    key_confidence: float | None = None
    album_name: str | None = None
    album_series: str | None = None
    composer: str | None = None
    lyricist: str | None = None
    lyrics_raw: str | None = None
    inferred_themes: list[str] = Field(default_factory=list)
    phase: Phase = "Worship"
    source_warnings: list[str] = Field(default_factory=list)


class TransitionCandidate(BaseModel):
    from_hash: str
    to_hash: str
    bpm_delta: float
    cfd: int
    compatibility_score: float
    key_shift_semitones: int = 0
    tempo_ratio: float = 1.0
    gap_beats: float = 2.0
    crossfade_enabled: bool = False
    crossfade_duration_seconds: float | None = None
    warnings: list[str] = Field(default_factory=list)


class ProposalItem(BaseModel):
    song_id: str
    recording_hash_prefix: str
    position: int
    title: str | None = None
    phase: Phase | None = None
    key_shift_semitones: int = 0
    tempo_ratio: float = 1.0
    gap_beats: float = 2.0
    crossfade_enabled: bool = False
    crossfade_duration_seconds: float | None = None


class ScoreBreakdown(BaseModel):
    theme: float = 0.0
    tempo: float = 0.0
    harmony: float = 0.0
    diversity: float = 0.0
    total: float = 0.0


class SongsetProposal(BaseModel):
    items: list[ProposalItem]
    score_breakdown: ScoreBreakdown = Field(default_factory=ScoreBreakdown)
    llm_rationale: str = ""
    validation_warnings: list[str] = Field(default_factory=list)
    validation_errors: list[str] = Field(default_factory=list)


class LlmDraft(BaseModel):
    recording_hashes: list[str]
    rationale: str = ""


class ConstructorState(BaseModel):
    config: ConstructorConfig
    pool: list[SongCandidate] = Field(default_factory=list)
    transition_matrix: dict[str, dict[str, TransitionCandidate]] = Field(default_factory=dict)
    candidate_beams: list[SongsetProposal] = Field(default_factory=list)
    llm_drafts: list[LlmDraft] = Field(default_factory=list)
    validation_feedback: list[str] = Field(default_factory=list)
    final_proposals: list[SongsetProposal] = Field(default_factory=list)
    trace: list[dict[str, Any]] = Field(default_factory=list)
    iteration: int = 0
