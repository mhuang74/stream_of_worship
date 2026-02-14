"""Service configuration using pydantic-settings."""

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Qwen3 alignment service configuration."""

    model_config = SettingsConfigDict(
        env_file=".env", case_sensitive=True, env_prefix="SOW_QWEN3_"
    )

    # Model configuration
    MODEL_PATH: Path = Path("/models/qwen3-forced-aligner")
    DEVICE: str = "auto"  # auto/mps/cuda/cpu
    DTYPE: str = "float32"  # bfloat16/float16/float32

    # Concurrency
    MAX_CONCURRENT: int = 2  # Max concurrent alignments (2=balance throughput/memory, 3=higher throughput if memory permits)

    # R2 Configuration
    R2_BUCKET: str = ""
    R2_ENDPOINT_URL: str = ""
    R2_ACCESS_KEY_ID: str = ""
    R2_SECRET_ACCESS_KEY: str = ""

    # API Security
    API_KEY: str = ""

    # Cache and Processing
    CACHE_DIR: Path = Path("/cache")


settings = Settings()
