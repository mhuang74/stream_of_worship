"""Logging configuration with job_id context support.

Uses contextvars to automatically inject job_id into all log records.
"""

import logging
from contextvars import ContextVar

# Context variable for storing current job_id
job_id_ctx: ContextVar[str | None] = ContextVar("job_id", default=None)


class JobIdFormatter(logging.Formatter):
    """Custom formatter that adds job_id prefix from contextvar.

    Format: %(asctime)s - %(name)s - %(levelname)s - [%(job_id)s] %(message)s

    If job_id is not set in context, logs without the job_id prefix.
    """

    def format(self, record: logging.LogRecord) -> str:
        """Format log record with job_id from context."""
        # Get job_id from context
        job_id = job_id_ctx.get()

        # Store original message
        original_msg = record.msg
        original_args = record.args

        # Add job_id prefix if available
        if job_id:
            record.msg = f"[{job_id}] {record.msg}"

        # Format the record
        result = super().format(record)

        # Restore original values (important for other formatters/handlers)
        record.msg = original_msg
        record.args = original_args

        return result


def configure_logging(
    level: int = logging.INFO,
    format_string: str | None = None,
    suppress_external: bool = True,
) -> None:
    """Configure logging with job_id support.

    Args:
        level: Logging level (default: INFO)
        format_string: Custom format string. If None, uses default with job_id support.
        suppress_external: If True, sets external library loggers to WARNING.
    """
    if format_string is None:
        format_string = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

    # Create formatter with job_id support
    formatter = JobIdFormatter(format_string)

    # Configure root handler
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Remove existing handlers to avoid duplicates
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Add new handler with our formatter
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    root_logger.addHandler(handler)

    # Suppress external library logs if requested
    if suppress_external:
        # audio-separator and related libraries
        logging.getLogger("audio_separator").setLevel(logging.WARNING)
        logging.getLogger("audio_separator.separator").setLevel(logging.WARNING)
        logging.getLogger("audio_separator.separator.separator").setLevel(logging.WARNING)
        # Other potentially noisy libraries
        logging.getLogger("urllib3").setLevel(logging.WARNING)
        logging.getLogger("botocore").setLevel(logging.WARNING)


def set_job_id(job_id: str | None) -> None:
    """Set the current job_id in the context.

    Args:
        job_id: Job ID to set, or None to clear.
    """
    job_id_ctx.set(job_id)
