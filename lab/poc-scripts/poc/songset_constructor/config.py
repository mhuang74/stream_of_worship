"""Runtime configuration for the songset constructor POC."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path


VALID_SEASONS = {"advent", "christmas", "lent", "easter", "pentecost"}
DEFAULT_ALBUM_SERIES = ("PW", "DEV")


def default_output_dir() -> Path:
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return Path(__file__).resolve().parents[2] / "output" / "songset_constructor" / run_id


@dataclass(slots=True)
class RunConfig:
    songs: int = 5
    top_k: int = 3
    pool_limit: int = 200
    output_dir: Path = field(default_factory=default_output_dir)
    album_series: list[str] = field(default_factory=lambda: list(DEFAULT_ALBUM_SERIES))
    include_cpw: bool = False
    intimate: bool = False
    hymnal_mode: bool = False
    season: str | None = None
    interactive_review: bool = False
    resume_thread_id: str | None = None
    no_llm: bool = False
    llm_judge: bool = False
    llm_model: str | None = None
    thread_id: str | None = None

    def __post_init__(self) -> None:
        if self.songs not in {4, 5}:
            raise ValueError("--songs supports only 4 or 5 for this POC")
        if self.top_k < 1:
            raise ValueError("--top-k must be >= 1")
        if self.pool_limit < self.songs:
            raise ValueError("--pool-limit must be at least --songs")
        if self.season and self.season not in VALID_SEASONS:
            allowed = ", ".join(sorted(VALID_SEASONS))
            raise ValueError(f"--season must be one of: {allowed}")
        self.output_dir = Path(self.output_dir)
        self.album_series = list(dict.fromkeys(self.album_series or DEFAULT_ALBUM_SERIES))
        if self.include_cpw and "CPW" not in self.album_series:
            self.album_series.append("CPW")
        self.llm_model = self.llm_model or os.environ.get("SOW_LLM_MODEL")
        if not self.thread_id:
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            self.thread_id = self.resume_thread_id or f"songset-{stamp}-{self.songs}s-top{self.top_k}"

    def validate_environment(self) -> None:
        if self.no_llm:
            return
        missing = []
        if not os.environ.get("SOW_LLM_API_KEY"):
            missing.append("SOW_LLM_API_KEY")
        if not self.llm_model:
            missing.append("SOW_LLM_MODEL or --llm-model")
        if missing:
            raise RuntimeError(
                "Agentic mode requires LLM configuration: " + ", ".join(missing)
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "songs": self.songs,
            "top_k": self.top_k,
            "pool_limit": self.pool_limit,
            "output_dir": str(self.output_dir),
            "album_series": self.album_series,
            "include_cpw": self.include_cpw,
            "intimate": self.intimate,
            "hymnal_mode": self.hymnal_mode,
            "season": self.season,
            "interactive_review": self.interactive_review,
            "resume_thread_id": self.resume_thread_id,
            "no_llm": self.no_llm,
            "llm_judge": self.llm_judge,
            "llm_model": self.llm_model,
            "thread_id": self.thread_id,
        }
