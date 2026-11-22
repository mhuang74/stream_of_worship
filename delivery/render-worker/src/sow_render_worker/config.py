import os
from dataclasses import dataclass, field


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class RenderWorkerConfig:
    SOW_DATABASE_URL: str
    SOW_R2_BUCKET: str
    SOW_R2_ENDPOINT_URL: str
    SOW_R2_ACCESS_KEY_ID: str
    SOW_R2_SECRET_ACCESS_KEY: str
    SOW_AWS_REGION: str
    # Optional: only Glue paid stages need it. It is never required, never
    # logged (repr=False) and never serialized into a status payload.
    SOW_ELEVENLABS_API_KEY: str | None = field(default=None, repr=False)

    @property
    def has_elevenlabs_key(self) -> bool:
        """True when a provider key is configured for paid Glue stages."""
        return bool(self.SOW_ELEVENLABS_API_KEY)

    @classmethod
    def from_env(cls) -> "RenderWorkerConfig":
        required_vars = {
            "SOW_DATABASE_URL": os.environ.get("SOW_DATABASE_URL"),
            "SOW_R2_BUCKET": os.environ.get("SOW_R2_BUCKET"),
            "SOW_R2_ENDPOINT_URL": os.environ.get("SOW_R2_ENDPOINT_URL"),
            "SOW_R2_ACCESS_KEY_ID": os.environ.get("SOW_R2_ACCESS_KEY_ID"),
            "SOW_R2_SECRET_ACCESS_KEY": os.environ.get("SOW_R2_SECRET_ACCESS_KEY"),
            "SOW_AWS_REGION": os.environ.get("SOW_AWS_REGION"),
        }

        missing = [name for name, value in required_vars.items() if not value]
        if missing:
            raise ConfigError(f"Missing required environment variables: {', '.join(missing)}")

        return cls(
            **required_vars,
            SOW_ELEVENLABS_API_KEY=os.environ.get("SOW_ELEVENLABS_API_KEY") or None,
        )


def load_config() -> RenderWorkerConfig:
    return RenderWorkerConfig.from_env()
