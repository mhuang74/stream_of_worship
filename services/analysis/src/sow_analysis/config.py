"""Service configuration using pydantic-settings."""

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Analysis service configuration."""

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=True, extra="ignore")

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
    SOW_MAX_CONCURRENT_ANALYSIS_JOBS: int = 1  # Serialized (high memory/CPU usage)
    SOW_MAX_CONCURRENT_LRC_JOBS: int = 2  # Configurable (lower memory with faster-whisper)

    # Demucs Configuration
    SOW_DEMUCS_MODEL: str = "htdemucs"
    SOW_DEMUCS_DEVICE: str = "cpu"  # "cuda" or "cpu"

    # LLM Configuration (OpenAI-compatible API for LRC alignment)
    # Supports OpenRouter, nano-gpt.com, synthetic.new, or OpenAI direct
    SOW_LLM_API_KEY: str = ""
    SOW_LLM_BASE_URL: str = ""  # e.g., "https://openrouter.ai/api/v1"
    SOW_LLM_MODEL: str = ""  # e.g., "openai/gpt-4o-mini" for OpenRouter

    # Whisper Configuration
    SOW_WHISPER_DEVICE: str = "cpu"  # "cuda" or "cpu"
    SOW_WHISPER_CACHE_DIR: Path = Path("/cache/whisper")

    # Stem Separation Configuration
    SOW_MAX_CONCURRENT_STEM_SEPARATION_JOBS: int = 1  # Serialized (high memory/CPU usage)
    SOW_AUDIO_SEPARATOR_MODEL_DIR: Path = Path("/models/audio-separator")
    SOW_VOCAL_SEPARATION_MODEL: str = "model_mel_band_roformer_ep_3005_sdr_11.4360.ckpt"
    SOW_DEREVERB_MODEL: str = "UVR-De-Echo-Normal.pth"

    # MVSEP Cloud API Configuration
    SOW_MVSEP_API_KEY: str = ""
    SOW_MVSEP_ENABLED: bool = True
    SOW_MVSEP_VOCAL_MODEL: int = 81       # sep_type=40 add_opt1 (BS Roformer 2025.07)
    SOW_MVSEP_DEREVERB_MODEL: int = 0     # sep_type=22 add_opt1 (FoxJoy MDX23C)
    SOW_MVSEP_HTTP_TIMEOUT: int = 60      # seconds per HTTP request
    SOW_MVSEP_STAGE_TIMEOUT: int = 300    # max seconds per stage (submit+poll)
    SOW_MVSEP_TOTAL_TIMEOUT: int = 900     # max seconds for entire MVSEP attempt per song
    SOW_MVSEP_DAILY_JOB_LIMIT: int = 50   # max MVSEP jobs per UTC day (cost cap)

    # Queue Configuration
    SOW_QUEUE_START_DELAY_SECONDS: int = 30  # Delay before processing starts (window to cancel/clear jobs)

    # Qwen3 Alignment Service Configuration
    SOW_QWEN3_BASE_URL: str = "http://qwen3:8000"  # Base URL for Qwen3 Alignment Service
    SOW_QWEN3_API_KEY: str = ""  # Optional API key for Qwen3 service authentication


settings = Settings()
