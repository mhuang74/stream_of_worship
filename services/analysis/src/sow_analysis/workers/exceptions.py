class WorkerError(Exception):
    """Base exception for worker errors."""

    pass


class LLMConfigError(WorkerError):
    """Raised when LLM configuration is missing or invalid."""

    pass
