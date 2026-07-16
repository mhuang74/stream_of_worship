"""Service configuration using pydantic-settings."""

from typing import Optional

from pathlib import Path
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Analysis service configuration."""

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=True, extra="ignore")

    # Logging
    SOW_LOG_LEVEL: str = "INFO"
    # Root log level for the service. Default INFO hides large content dumps
    # (LLM prompts/responses, scraped/final lyrics, Whisper phrases).
    # Set to DEBUG to surface them in console / docker logs for troubleshooting.

    # R2 Configuration
    SOW_R2_BUCKET: str = "sow-audio"
    SOW_R2_ENDPOINT_URL: str = ""
    SOW_R2_ACCESS_KEY_ID: str = ""
    SOW_R2_SECRET_ACCESS_KEY: str = ""

    # API Security
    SOW_ANALYSIS_API_KEY: str = ""
    SOW_ADMIN_API_KEY: str = ""  # Admin API key for privileged operations (cancel jobs, etc.)

    # Cache and Processing
    CACHE_DIR: Path = Path("/cache")
    KEY_ALGORITHM_VERSION: str = "ks_segment_vote_v1"
    # Tempo detection algorithm version.
    #   "v4_octave_guard" -> start_bpm=80 + double/half-time guard (current default)
    #   "v5_cps_prior"    -> CPS-derived lognormal prior (skips octave guard)
    BPM_ALGORITHM_VERSION: str = "v4_octave_guard"
    SOW_MAX_CONCURRENT_LOCAL_MODEL_JOBS: int = (
        1  # Global limit for local model execution (Whisper, Qwen3, audio-separator, allin1, demucs)
    )

    # Fast analysis (librosa-only) concurrency. CPU/memory heavy; distinct from
    # SOW_MAX_CONCURRENT_LOCAL_MODEL_JOBS. Default is cgroup-aware on Linux.
    # A value <= 0 means auto-detect (cgroup-aware on Linux, 1 elsewhere), capped at 4.
    SOW_FAST_ANALYZE_MAX_CONCURRENT: int = 0

    @field_validator("SOW_LOG_LEVEL")
    @classmethod
    def _validate_log_level(cls, v: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR"}
        upper = v.upper()
        if upper not in allowed:
            raise ValueError(f"SOW_LOG_LEVEL must be one of {allowed}, got: {v!r}")
        return upper

    @field_validator("SOW_FAST_ANALYZE_MAX_CONCURRENT")
    @classmethod
    def _validate_fast_analyze_concurrent(cls, v: int) -> int:
        """Compute cgroup-aware default when not explicitly configured (<=0)."""
        if v > 0:
            return v
        import os

        try:
            return min(4, max(1, len(os.sched_getaffinity(0)) // 2))
        except (AttributeError, OSError):  # macOS / unsupported
            return 1

    @field_validator("SOW_MAX_CONCURRENT_LOCAL_MODEL_JOBS")
    @classmethod
    def _validate_concurrent_jobs(cls, v: int) -> int:
        """Ensure concurrent jobs is at least 1 to prevent deadlock."""
        if v < 1:
            raise ValueError("SOW_MAX_CONCURRENT_LOCAL_MODEL_JOBS must be at least 1")
        return v

    @field_validator("BPM_ALGORITHM_VERSION")
    @classmethod
    def _validate_bpm_algorithm_version(cls, v: str) -> str:
        """Validate BPM algorithm version to fail fast on typos."""
        allowed = {"v4_octave_guard", "v5_cps_prior"}
        if v not in allowed:
            raise ValueError(f"BPM_ALGORITHM_VERSION must be one of {allowed}, got: {v!r}")
        return v

    # Demucs Configuration
    SOW_DEMUCS_MODEL: str = "htdemucs"
    SOW_DEMUCS_DEVICE: str = "cpu"  # "cuda" or "cpu"

    # LLM Configuration (OpenAI-compatible API for LRC alignment)
    # Supports OpenRouter, nano-gpt.com, synthetic.new, or OpenAI direct
    SOW_LLM_API_KEY: str = ""
    SOW_LLM_BASE_URL: str = ""  # e.g., "https://openrouter.ai/api/v1"
    SOW_LLM_MODEL: str = ""  # e.g., "openai/gpt-4o-mini" for OpenRouter

    # LLM Rate-Limit Retry Configuration
    SOW_LLM_MAX_CONCURRENT: int = 3
    # Module-level semaphore limiting concurrent LLM calls across all LRC jobs.
    # Matches the provider's concurrency budget (3 slots). Prevents self-inflicted
    # 429s from multiple overlapping jobs. Set to 0 to disable.

    SOW_LLM_RATE_LIMIT_MAX_RETRIES: int = 16
    # Max retry attempts on 429 / retryable errors. Applies globally to both the
    # YouTube-transcript LLM correction step (_llm_correct) and the ASR-fallback
    # LLM alignment step (_llm_align). The wall-clock SOW_LLM_RATE_LIMIT_TIMEOUT_SECONDS
    # is the primary ceiling; this count is a secondary guard.

    SOW_LLM_RATE_LIMIT_BASE_DELAY: float = 2.0
    # Base delay in seconds for exponential backoff on 429 retries.

    SOW_LLM_RATE_LIMIT_MAX_DELAY: float = 90.0
    # Cap on single backoff delay. Provider-reported retry_strategy.max_delay_s
    # from the OpenRouter error body overrides this dynamically (e.g. provider says
    # 30s), but our local cap is now 90s so we can wait longer when the provider's
    # guidance permits or is absent.

    SOW_LLM_RATE_LIMIT_TIMEOUT_SECONDS: int = 1200
    # Total wall-clock budget for the retry sequence (20 minutes). Raised from 5 min
    # because OpenRouter concurrent_budget_exceeded (3/3 slots) windows frequently
    # outlast 5 min; the YouTube transcript path is much faster than ASR fallback
    # (~50-640s vs ~900-1300s), so patience here avoids an expensive fallback.

    SOW_LLM_MIN_INTERVAL_SECONDS: float = 2.0
    # Minimum gap (seconds) between consecutive LLM HTTP calls across all jobs.
    # Compensates for provider-side in_flight accounting lag. This throttle fires
    # after acquiring the LLM semaphore slot, so it paces active requests without
    # blocking idle jobs. Set to 0 to disable.

    # Embedding Provider Configuration (OpenAI-compatible API)
    # Separate from SOW_LLM_* so chat and embedding can use different providers.
    SOW_EMBEDDING_API_KEY: str = ""
    SOW_EMBEDDING_BASE_URL: str = ""
    SOW_EMBEDDING_MODEL: str = "text-embedding-3-small"

    # Whisper Configuration
    SOW_WHISPER_DEVICE: str = "cpu"  # "cuda" or "cpu"
    SOW_WHISPER_CACHE_DIR: Path = Path("/cache/whisper")

    # DashScope Qwen3 ASR Configuration
    SOW_DASHSCOPE_API_KEY: str = ""
    SOW_DASHSCOPE_ASR_REGION: str = "intl"  # intl, cn, us
    SOW_DASHSCOPE_ASR_FLASH_MODEL: str = "qwen3-asr-flash"
    SOW_DASHSCOPE_ASR_FILETRANS_MODEL: str = "qwen3-asr-flash-filetrans"
    SOW_DASHSCOPE_ASR_CONTEXT_MAX_CHARS: int = 10000
    SOW_DASHSCOPE_ASR_SNAP_THRESHOLD: float = 0.60
    SOW_DASHSCOPE_ASR_TIMEOUT_SECONDS: int = 300
    SOW_DASHSCOPE_ASR_FILETRANS_TIMEOUT_SECONDS: int = 1800
    SOW_DASHSCOPE_ASR_MAX_CONCURRENT: int = 2
    SOW_DASHSCOPE_ASR_CACHE_VERSION: int = 1

    # Stem Separation Configuration
    SOW_AUDIO_SEPARATOR_MODEL_DIR: Path = Path("/models/audio-separator")
    SOW_VOCAL_SEPARATION_MODEL: str = "model_mel_band_roformer_ep_3005_sdr_11.4360.ckpt"
    SOW_DEREVERB_MODEL: str = "UVR-De-Echo-Normal.pth"

    # MVSEP Cloud API Configuration
    SOW_MVSEP_API_KEY: str = ""
    SOW_MVSEP_ENABLED: bool = True

    # Free-Only Patient Mode
    SOW_FREE_ONLY_MODE: bool = False
    # When enabled, LRC and stem-separation jobs wait for free-tier API quota
    # to reset (UTC daily) instead of falling back to local models (Whisper,
    # audio-separator) or failing. Scoped to LRC generation (Qwen3 ASR) and
    # stem separation (MVSEP) only. Other job types unaffected.
    SOW_QUOTA_POLL_INTERVAL_SECONDS: int = 3600
    # How often the shared QuotaWaiter's poller checks whether free-tier API
    # quota has reset. Both MVSEP and DashScope. Lower values = faster detection
    # after UTC midnight at cost of more checks. Since wait() also self-checks
    # every 1s, this primarily controls how aggressively the poller
    # re-evaluates; it is an optimization, not a hard dependency.

    SOW_QUOTA_WAIT_QUIESCENT_LOG_INTERVAL_SECONDS: int = 1800
    # When every active job is blocked on a free-tier API quota reset (MVSEP or
    # Qwen3 ASR), back off QuotaWaiter and queue-state periodic logging to this
    # interval (default 30 min) instead of the normal 30s/60s cadence. The first
    # log on entering quiescence still fires immediately; subsequent identical
    # "still waiting" lines are suppressed until the interval elapses. Tunable so
    # operators can shorten during debugging.

    # Parent job wait timeout for child stem separation
    SOW_PARENT_STEM_WAIT_TIMEOUT_SECONDS: int = 7200
    # Wall-clock budget (seconds) a parent job (LRC, forced alignment)
    # waits for an auto-triggered child stem separation job to complete
    # before giving up. In SOW_FREE_ONLY_MODE, this budget is suspended
    # (reset to zero) while the child job is in the
    # "waiting_for_mvsep_quota_reset" stage, so the parent does not time
    # out while MVSEP quota is legitimately exhausted for the day.

    # Stage 1 (Vocal Separation)
    SOW_MVSEP_STAGE1_SEP_TYPE: int = 48
    SOW_MVSEP_STAGE1_ADD_OPT1: int = 11
    SOW_MVSEP_STAGE1_ADD_OPT2: Optional[int] = None

    # Stage 2 (Reverb Removal) — None = skip Stage 2
    SOW_MVSEP_STAGE2_SEP_TYPE: Optional[int] = 22
    SOW_MVSEP_STAGE2_ADD_OPT1: Optional[int] = 0
    SOW_MVSEP_STAGE2_ADD_OPT2: Optional[int] = 1

    # Timeouts & limits
    SOW_MVSEP_HTTP_TIMEOUT: int = 60
    SOW_MVSEP_STAGE_TIMEOUT: int = 300
    SOW_MVSEP_STAGE2_TIMEOUT: int = 900  # Dedicated budget for Stage 2 + retries
    SOW_MVSEP_TOTAL_TIMEOUT: int = 1800  # Outer cap: Stage 1 + Stage 2 combined
    SOW_MVSEP_MAX_CONCURRENT: int = 1  # Max concurrent MVSEP API operations (MVSEP free-tier allows 1 pending job per token)

    @field_validator(
        "SOW_MVSEP_STAGE1_ADD_OPT2",
        "SOW_MVSEP_STAGE2_SEP_TYPE",
        "SOW_MVSEP_STAGE2_ADD_OPT1",
        "SOW_MVSEP_STAGE2_ADD_OPT2",
        mode="before",
    )
    @classmethod
    def _empty_str_to_none(cls, v):
        """Convert empty-string env vars to None for Optional[int] fields.

        pydantic-settings reads env vars as strings; an empty string (e.g.
        SOW_MVSEP_STAGE2_SEP_TYPE=) cannot be parsed as int. This validator
        converts "" / whitespace-only values to None before type coercion.
        """
        if isinstance(v, str) and not v.strip():
            return None
        return v

    # Queue Configuration
    SOW_QUEUE_START_DELAY_SECONDS: int = (
        30  # Delay before processing starts (window to cancel/clear jobs)
    )

    # Forced Aligner Configuration (Qwen3ForcedAligner-0.6B, runs in-process)
    SOW_FORCED_ALIGNER_MODEL_PATH: str = (
        "Qwen/Qwen3-ForcedAligner-0.6B"  # HF model ID or local path
    )
    SOW_FORCED_ALIGNER_DEVICE: str = "auto"  # auto/mps/cuda/cpu

    # YouTube Proxy Configuration
    SOW_YOUTUBE_PROXY: str = (
        ""  # HTTP/HTTPS/SOCKS proxy URL for YouTube transcript requests (e.g., "http://proxy:8080", "socks5://proxy:1080")
    )
    SOW_YOUTUBE_PROXY_RETRIES: int = 3  # Number of retries on HTTP 429 when using rotating proxies

    # YouTube Transcript Rate Limiting
    SOW_YOUTUBE_TRANSCRIPT_MAX_CONCURRENT: int = 1
    # Maximum concurrent YouTube transcript API calls (semaphore).
    # Default 1 (conservative — prevents IP-level rate limiting from YouTube).
    # Increase to 2-3 if using a rotating proxy with multiple IPs.
    # Set to 0 to disable the rate limiter entirely (not recommended).

    SOW_YOUTUBE_TRANSCRIPT_MIN_INTERVAL_SECONDS: float = 3.0
    # Minimum seconds between consecutive YouTube API calls (global throttle).
    # With max_concurrent=1, this caps throughput at 1/min_interval requests per second.
    # Default 3.0 = ~20 requests/minute. Lower to 2.0 for ~30 req/min if
    # using a rotating proxy with good IP diversity.

    SOW_YOUTUBE_TRANSCRIPT_MAX_RETRIES: int = 3
    # Retry attempts per YouTube API call on HTTP 429 (rate limited).
    # Each retry uses exponential backoff with jitter.

    SOW_YOUTUBE_TRANSCRIPT_RETRY_BASE_DELAY: float = 5.0
    # Base delay in seconds for exponential backoff on 429 retries.
    # Actual delay: min(base * 2^attempt, 60) + jitter(0-25%).
    # With base=5: 5s, 10s, 20s (capped at 60s).

    SOW_YOUTUBE_TRANSCRIPT_CIRCUIT_BREAKER_THRESHOLD: int = 5
    # Number of consecutive 429 failures before the circuit breaker opens.
    # When open, all YouTube transcript fetches are skipped immediately
    # (jobs fall back to Whisper/Qwen3 ASR without hitting YouTube).

    SOW_YOUTUBE_TRANSCRIPT_CIRCUIT_BREAKER_COOLDOWN: int = 120
    # Seconds before the circuit breaker auto-recovers (closes).
    # During cooldown, YouTube transcript fetches are skipped.
    # After cooldown, the next fetch attempt is allowed (and resets the breaker if successful).

    # ── Free-only-mode overrides (patient retry, no local ML fallback) ──
    SOW_YOUTUBE_TRANSCRIPT_CIRCUIT_BREAKER_THRESHOLD_FREE: int = 10
    # Consecutive 429s before breaker opens in free-only mode (vs 5 default).
    # Higher because free mode prefers waiting over falling back to local Whisper.

    SOW_YOUTUBE_TRANSCRIPT_CIRCUIT_BREAKER_COOLDOWN_FREE: int = 300
    # Breaker cooldown in seconds in free-only mode (vs 120 default).
    # Longer to let YouTube's IP-level rate-limit window pass.

    SOW_YOUTUBE_TRANSCRIPT_MAX_RETRIES_FREE: int = 5
    # Retry attempts per YouTube API call on 429 in free-only mode (vs 3 default).

    SOW_YOUTUBE_TRANSCRIPT_RETRY_BASE_DELAY_FREE: float = 10.0
    # Base backoff delay in seconds in free-only mode (vs 5.0 default).
    # With base=10: 10s, 20s, 40s, 60s, 60s (capped).

    SOW_YOUTUBE_TRANSCRIPT_MIN_INTERVAL_SECONDS_FREE: float = 30.0
    # Min seconds between YouTube API calls in free-only mode (vs 3.0 default).
    # More conservative spacing to avoid triggering IP-level 429s.
    # Actual interval includes ±25% jitter to prevent thundering herd.

    SOW_YOUTUBE_TRANSCRIPT_FREE_MODE_MAX_BREAKER_CYCLES: int = 3
    # Maximum number of breaker cooldown cycles a free-mode job will wait
    # through before giving up and falling back to local ASR.
    # Prevents infinite hangs if YouTube permanently blocks this IP.
    # 3 cycles × 300s cooldown = ~15 minutes of patience per job.

    @field_validator("SOW_YOUTUBE_TRANSCRIPT_MAX_CONCURRENT")
    @classmethod
    def _validate_youtube_transcript_concurrent(cls, v: int) -> int:
        """Ensure YouTube transcript concurrency is at least 0 (0 = disabled)."""
        if v < 0:
            raise ValueError(
                "SOW_YOUTUBE_TRANSCRIPT_MAX_CONCURRENT must be >= 0 (0 disables rate limiting)"
            )
        return v


settings = Settings()
