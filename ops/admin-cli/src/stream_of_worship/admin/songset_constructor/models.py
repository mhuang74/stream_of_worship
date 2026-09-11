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
    has_components: bool = False
    # Boundary (role='entry') values, from song_components
    entry_bpm: float | None = None
    entry_key: str | None = None  # e.g. "G" (bare note name, never mode-suffixed)
    entry_mode: str | None = None  # copied from the song's recording musical_mode at aggregation
    entry_key_confidence: float | None = None
    entry_energy_level_db: float | None = None
    # Boundary (role='exit') values
    exit_bpm: float | None = None
    exit_key: str | None = None
    exit_mode: str | None = None  # same rule: copied from recording musical_mode
    exit_key_confidence: float | None = None
    exit_energy_level_db: float | None = None
    # Aggregates (chorus-preference, confidence-weighted)
    component_theme_scores: dict[str, float] | None = None  # dense 12-key; None = no votes at all
    component_posture: str | None = None  # To God | About God | To Congregation
    component_posture_confidence: float | None = None
    recording_posture: str | None = None  # recordings.vocal_posture (fallback rung)
    recording_theme: str | None = None  # recordings.theme (unused in scoring; diagnostic only)
    theme_source: str | None = None  # "component" | "fusion" | None; set during enrichment
    # Energy percentiles (set by the enrichment pass, pool-wide)
    entry_energy_pct: float | None = None
    exit_energy_pct: float | None = None


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
    boundary_source: str = "song_level"  # "component" | "song_level"


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
    has_components: bool = False
    theme_source: str | None = None
    component_posture: str | None = None  # effective posture: component → recording → None
    entry_energy_pct: float | None = None
    exit_energy_pct: float | None = None
    entry_bpm: float | None = None
    exit_bpm: float | None = None
    entry_key: str | None = None
    exit_key: str | None = None
    entry_key_confidence: float | None = None
    incoming_boundary_source: str | None = None  # boundary_source of the transition INTO this item (opener: None)


class ScoreBreakdown(BaseModel):
    f_theme: float
    f_tempo: float
    f_harmony: float
    f_diversity: float
    f_energy: float | None = None  # None = term absent from the weighted sum
    f_posture: float | None = None  # None = term absent from the weighted sum
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
