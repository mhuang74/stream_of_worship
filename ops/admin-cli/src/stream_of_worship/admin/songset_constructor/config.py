"""Runtime configuration for the songset constructor."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

VALID_SEASONS = {"advent", "christmas", "lent", "easter", "pentecost"}
DEFAULT_ALBUM_SERIES: tuple[str, ...] = ()

DEFAULT_CACHE_DIR = Path.home() / ".cache" / "sow" / "songset_constructor"
DEFAULT_REPORT_DIR = Path.cwd() / "output" / "songset_constructor"


@dataclass(slots=True)
class RunConfig:
    count: int = 3
    proposals: int = 3
    pool: int = 200
    output_dir: Path | None = None
    album_series: list[str] = field(default_factory=lambda: list(DEFAULT_ALBUM_SERIES))
    include_cpw: bool = False
    intimate: bool = False
    hymnal_mode: bool = False
    season: str | None = None
    interactive_review: bool = False
    resume_thread_id: str | None = None
    llm_enabled: bool = False
    llm_judge: bool = False
    llm_model: str | None = None
    thread_id: str | None = None
    relax_h3_bpm: int | None = None
    relax_h2_bpm: int | None = None
    relax_h1: bool = True
    auto_relax: bool = True
    relax_h4: bool = False
    relax_h5: bool = False
    relax_h4_bpm: int | None = None
    relax_h5_cfd: int | None = None
    only_evaluate_pool_enrichment: bool = False
    use_cache: bool = True
    cache_dir: Path = field(default_factory=lambda: DEFAULT_CACHE_DIR)
    cache_ttl: float = 24.0

    def __post_init__(self) -> None:
        if self.count not in {2, 3, 4, 5}:
            raise ValueError("--count supports only 2-5")
        if self.proposals < 1:
            raise ValueError("--proposals must be >= 1")
        if self.pool < self.count:
            raise ValueError("--pool must be at least --count")
        if self.season and self.season not in VALID_SEASONS:
            allowed = ", ".join(sorted(VALID_SEASONS))
            raise ValueError(f"--season must be one of: {allowed}")
        if self.relax_h3_bpm is not None and self.relax_h3_bpm < 0:
            raise ValueError("--relax-h3-bpm must be >= 0")
        if self.relax_h2_bpm is not None and self.relax_h2_bpm < 0:
            raise ValueError("--relax-h2-bpm must be >= 0")
        if self.relax_h4_bpm is not None and self.relax_h4_bpm < 0:
            raise ValueError("--relax-h4-bpm must be >= 0")
        if self.relax_h5_cfd is not None and self.relax_h5_cfd < 0:
            raise ValueError("--relax-h5-cfd must be >= 0")
        if isinstance(self.output_dir, str):
            self.output_dir = Path(self.output_dir)
        if isinstance(self.cache_dir, str):
            self.cache_dir = Path(self.cache_dir)
        self.album_series = list(dict.fromkeys(self.album_series or DEFAULT_ALBUM_SERIES))
        if self.include_cpw and "CPW" not in self.album_series:
            self.album_series.append("CPW")
        if self.hymnal_mode and "HYMN" not in self.album_series:
            self.album_series.append("HYMN")
        self.llm_model = self.llm_model or os.environ.get("SOW_LLM_MODEL")
        if not self.thread_id:
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            self.thread_id = self.resume_thread_id or f"songset-{stamp}-{self.count}s-top{self.proposals}"

    @property
    def closing_limit(self) -> int:
        if self.relax_h3_bpm is not None:
            return self.relax_h3_bpm
        return 80 if self.intimate else 90

    @property
    def opening_floor(self) -> int:
        if self.relax_h2_bpm is not None:
            return self.relax_h2_bpm
        return 90

    @property
    def h4_limit(self) -> int:
        if self.relax_h4_bpm is not None:
            return self.relax_h4_bpm
        return 40 if self.relax_h4 else 35

    @property
    def h5_limit(self) -> int:
        if self.relax_h5_cfd is not None:
            return self.relax_h5_cfd
        return 3 if self.relax_h5 else 2

    def validate_environment(self) -> None:
        if not self.llm_enabled and self.llm_judge:
            raise RuntimeError(
                "--no-llm cannot be combined with --llm-judge: the judge "
                "requires an LLM model. Either enable LLM (--llm) or disable "
                "the judge (--no-llm-judge)."
            )
        if not self.llm_enabled:
            return
        missing = []
        if not os.environ.get("SOW_LLM_API_KEY"):
            missing.append("SOW_LLM_API_KEY")
        if not self.llm_model:
            missing.append("SOW_LLM_MODEL or --llm-model")
        if missing:
            raise RuntimeError(
                "Agentic mode requires LLM configuration: "
                + ", ".join(missing)
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "count": self.count,
            "proposals": self.proposals,
            "pool": self.pool,
            "output_dir": str(self.output_dir) if self.output_dir else None,
            "album_series": self.album_series,
            "include_cpw": self.include_cpw,
            "intimate": self.intimate,
            "hymnal_mode": self.hymnal_mode,
            "season": self.season,
            "interactive_review": self.interactive_review,
            "resume_thread_id": self.resume_thread_id,
            "llm_enabled": self.llm_enabled,
            "llm_judge": self.llm_judge,
            "llm_model": self.llm_model,
            "thread_id": self.thread_id,
            "relax_h3_bpm": self.relax_h3_bpm,
            "relax_h2_bpm": self.relax_h2_bpm,
            "relax_h1": self.relax_h1,
            "auto_relax": self.auto_relax,
            "relax_h4": self.relax_h4,
            "relax_h5": self.relax_h5,
            "relax_h4_bpm": self.relax_h4_bpm,
            "relax_h5_cfd": self.relax_h5_cfd,
            "only_evaluate_pool_enrichment": self.only_evaluate_pool_enrichment,
            "use_cache": self.use_cache,
            "cache_dir": str(self.cache_dir),
            "cache_ttl": self.cache_ttl,
        }
