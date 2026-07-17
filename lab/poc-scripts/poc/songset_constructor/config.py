"""Runtime configuration for the songset constructor POC."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv


VALID_SEASONS = {"advent", "christmas", "lent", "easter", "pentecost"}
DEFAULT_ALBUM_SERIES: tuple[str, ...] = ()
DEFAULT_ENV_FILE = Path("/opt/sow/.env")


def default_output_dir() -> Path:
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return Path(__file__).resolve().parents[2] / "output" / "songset_constructor" / run_id


def load_runtime_env(env_file: Path | None = None) -> Path | None:
    configured = env_file or os.environ.get("SOW_ENV_FILE")
    candidate = Path(configured) if configured else DEFAULT_ENV_FILE
    if not candidate.exists():
        if configured:
            raise ValueError(f"Configured env file does not exist: {candidate}")
        return None
    load_dotenv(candidate, override=False)
    return candidate


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
    env_file: Path | None = None
    relax_h3_bpm: int | None = None
    relax_h2_bpm: int | None = None
    relax_h1: bool = True
    auto_relax: bool = True
    relax_h4: bool = False
    relax_h5: bool = False
    relax_h4_bpm: int | None = None
    relax_h5_cfd: int | None = None

    def __post_init__(self) -> None:
        self.env_file = load_runtime_env(self.env_file)
        if self.songs not in {2, 3, 4, 5}:
            raise ValueError("--songs supports only 2-5 for this POC")
        if self.top_k < 1:
            raise ValueError("--top-k must be >= 1")
        if self.pool_limit < self.songs:
            raise ValueError("--pool-limit must be at least --songs")
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
        self.output_dir = Path(self.output_dir)
        self.album_series = list(dict.fromkeys(self.album_series or DEFAULT_ALBUM_SERIES))
        if self.include_cpw and "CPW" not in self.album_series:
            # Only append CPW when a restrictive default list is present.
            # When album_series is empty (meaning "no filter"), appending CPW
            # would narrow the pool to only CPW rows.
            if self.album_series:
                self.album_series.append("CPW")
        if self.hymnal_mode and "HYMN" not in self.album_series:
            # Ensure HYMN candidates are in the pool when hymnal mode is set.
            if self.album_series:
                self.album_series.append("HYMN")
        self.llm_model = self.llm_model or os.environ.get("SOW_LLM_MODEL")
        if not self.thread_id:
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            self.thread_id = self.resume_thread_id or f"songset-{stamp}-{self.songs}s-top{self.top_k}"

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
        if self.no_llm and self.llm_judge:
            raise RuntimeError(
                "--no-llm cannot be combined with --llm-judge: the judge "
                "requires an LLM model. Either enable LLM (--llm) or disable "
                "the judge (--no-llm-judge)."
            )
        if self.no_llm:
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
                + ". If you sourced a .env file in the shell, export its values with "
                "`set -a; source /opt/sow/.env; set +a`, or pass --env-file."
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
            "env_file": str(self.env_file) if self.env_file else None,
            "relax_h3_bpm": self.relax_h3_bpm,
            "relax_h2_bpm": self.relax_h2_bpm,
            "relax_h1": self.relax_h1,
            "auto_relax": self.auto_relax,
            "relax_h4": self.relax_h4,
            "relax_h5": self.relax_h5,
            "relax_h4_bpm": self.relax_h4_bpm,
            "relax_h5_cfd": self.relax_h5_cfd,
        }
