"""Pydantic schemas used by the songset constructor."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SongCandidate(BaseModel):
    song_id: str
    title: str
    title_pinyin: str | None = None
    composer: str | None = None
    lyricist: str | None = None
    album_name: str | None = None
    album_series: str | None = None
    recording_hash_prefix: str
    tempo_bpm: float | None = None
    musical_key: str | None = None
    musical_mode: str | None = None
    key_confidence: float | None = None
    loudness_db: float | None = None
    duration_seconds: float | None = None
    lyrics_raw: str | None = None
    song_theme_scores_raw: dict[str, float] = Field(default_factory=dict)
    line_theme_scores_raw: dict[str, float] = Field(default_factory=dict)
    themes: dict[str, float] = Field(default_factory=dict)
    phase: int = 0
    secondary_phases: list[int] = Field(default_factory=list)
    fan_out: int = 0
    is_dead_end: bool = False
    is_hymn: bool = False
    in_leader_range: bool = True
    leader_range_distance: int = 0
    recommended_key_shift_for_range: int = 0
    leader_range_pcs: list[int] = Field(default_factory=list)
    leader_range_label: str | None = None


class TransitionCandidate(BaseModel):
    from_hash_prefix: str
    to_hash_prefix: str
    cfd: int
    bpm_delta: float
    key_compat: float
    suggested_key_shift: int
    transition_technique: str
    crossfade_enabled: bool
    crossfade_duration_seconds: float
    gap_beats: float
    warnings: list[str] = Field(default_factory=list)


class DraftItem(BaseModel):
    position: int
    recording_hash_prefix: str
    key_shift_semitones: int = 0
    crossfade_enabled: bool = False
    crossfade_duration_seconds: float = 0.0
    gap_beats: float = 2.0
    tempo_ratio: float = 1.0


class SongsetDraft(BaseModel):
    items: list[DraftItem]
    rationale: str = ""


class ProposalItem(DraftItem):
    song_id: str
    title: str
    album_name: str | None = None
    phase: int
    secondary_phases: list[int] = Field(default_factory=list)
    themes: list[str] = Field(default_factory=list)
    bpm: float | None = None
    key: str | None = None
    mode: str | None = None
    key_confidence: float | None = None
    duration_seconds: float | None = None
    tonic_pc: int = 0
    in_leader_range: bool = True
    leader_range_distance: int = 0
    recommended_key_shift_for_range: int = 0


class ScoreBreakdown(BaseModel):
    f_theme: float
    f_tempo: float
    f_harmony: float
    f_diversity: float
    total: float
    range_penalty: float = 0.0


class SongsetProposal(BaseModel):
    rank: int = 0
    items: list[ProposalItem]
    score: ScoreBreakdown
    rationale: str = ""
    hard_constraint_warnings: list[str] = Field(default_factory=list)
    llm_origin: bool = False
    judge_reason: str | None = None
    judge_score: float | None = None


class ValidationFeedback(BaseModel):
    passed: bool
    violated: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    repair_hints: list[str] = Field(default_factory=list)


class JudgeItem(BaseModel):
    rank: int
    recording_hash_prefixes: list[str]
    reason: str
    score: float = 0.0


class JudgeRanking(BaseModel):
    rankings: list[JudgeItem] = Field(default_factory=list)
