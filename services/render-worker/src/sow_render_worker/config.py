import os
from dataclasses import dataclass


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class RenderWorkerConfig:
    DATABASE_URL: str
    R2_BUCKET: str
    R2_ENDPOINT_URL: str
    R2_ACCESS_KEY_ID: str
    R2_SECRET_ACCESS_KEY: str
    AWS_REGION: str
    SQS_QUEUE_URL: str

    @classmethod
    def from_env(cls) -> "RenderWorkerConfig":
        required_vars = {
            "DATABASE_URL": os.environ.get("DATABASE_URL"),
            "R2_BUCKET": os.environ.get("R2_BUCKET"),
            "R2_ENDPOINT_URL": os.environ.get("R2_ENDPOINT_URL"),
            "R2_ACCESS_KEY_ID": os.environ.get("R2_ACCESS_KEY_ID"),
            "R2_SECRET_ACCESS_KEY": os.environ.get("R2_SECRET_ACCESS_KEY"),
            "AWS_REGION": os.environ.get("AWS_REGION"),
            "SQS_QUEUE_URL": os.environ.get("SQS_QUEUE_URL"),
        }

        missing = [name for name, value in required_vars.items() if not value]
        if missing:
            raise ConfigError(f"Missing required environment variables: {', '.join(missing)}")

        return cls(**required_vars)


def load_config() -> RenderWorkerConfig:
    return RenderWorkerConfig.from_env()
