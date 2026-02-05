"""Service configuration using pydantic-settings."""

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Analysis service configuration."""

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=True)

    # R2 Configuration
    R2_BUCKET: str = "sow-audio"
    R2_ENDPOINT_URL: str = ""
    SOW_R2_ACCESS_KEY_ID: str = ""
    SOW_R2_SECRET_ACCESS_KEY: str = ""

    # API Security
    ANALYSIS_API_KEY: str = ""

    # Cache and Processing
    CACHE_DIR: Path = Path("/cache")
    MAX_CONCURRENT_JOBS: int = 2

    # Demucs Configuration
    DEMUCS_MODEL: str = "htdemucs"
    DEMUCS_DEVICE: str = "cpu"  # "cuda" or "cpu"


settings = Settings()
