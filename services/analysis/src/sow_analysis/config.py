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
    SOW_MAX_CONCURRENT_STEM_SEPARATION_JOBS: int = 1  # Serialized (high memory usage)
    SOW_AUDIO_SEPARATOR_MODEL_DIR: Path = Path("/models/audio-separator")
    SOW_BS_ROFORMER_MODEL: str = "model_bs_roformer_ep_317_sdr_12.9755.ckpt"
    SOW_DEREVERB_MODEL: str = "UVR-De-Echo-Normal.pth"

    # Queue Configuration
    SOW_QUEUE_START_DELAY_SECONDS: int = 30  # Delay before processing starts (window to cancel/clear jobs)

    # Qwen3 Alignment Service Configuration
    SOW_QWEN3_BASE_URL: str = "http://qwen3:8000"  # Base URL for Qwen3 Alignment Service
    SOW_QWEN3_API_KEY: str = ""  # Optional API key for Qwen3 service authentication


settings = Settings()
