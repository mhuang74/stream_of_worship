import os
from dataclasses import dataclass


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

        return cls(**required_vars)


def load_config() -> RenderWorkerConfig:
    return RenderWorkerConfig.from_env()
