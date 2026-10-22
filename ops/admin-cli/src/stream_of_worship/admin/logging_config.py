"""File logging for sow-admin.

Writes session logs to a rotating file so that INFO records survive even when
Textual captures and discards stderr. Mirrors ``lab/sow-app`` logging setup but
configures the *root* logger: shared DB helpers log under ``sow_admin.db``,
outside the ``stream_of_worship`` namespace.
"""

import logging
import os
from pathlib import Path
from typing import Optional

from stream_of_worship.admin.config import get_cache_dir

DEFAULT_LOG_FILENAME = "sow-admin.log"
DEFAULT_MAX_BYTES = 5 * 1024 * 1024
DEFAULT_BACKUP_COUNT = 3

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"

# Third-party loggers that are extremely chatty at DEBUG/INFO.
_NOISY_LOGGERS = ("botocore", "boto3", "urllib3")


def get_log_path() -> Path:
    """Get the path of the sow-admin log file.

    Returns:
        ``<cache_dir>/logs/sow-admin.log``
    """
    return get_cache_dir() / "logs" / DEFAULT_LOG_FILENAME


def _rotate_log_if_needed(
    log_file: Path,
    max_bytes: int = DEFAULT_MAX_BYTES,
    backup_count: int = DEFAULT_BACKUP_COUNT,
) -> None:
    """Rotate the log file on startup if it exceeds ``max_bytes``.

    Renames ``X`` -> ``X.1``, shifting existing backups down and dropping the
    oldest one. Failures are ignored: logging must never break the CLI.
    """
    if not log_file.exists():
        return

    try:
        if log_file.stat().st_size < max_bytes:
            return
    except OSError:
        return

    try:
        for index in range(backup_count - 1, 0, -1):
            source = log_file.parent / f"{log_file.name}.{index}"
            destination = log_file.parent / f"{log_file.name}.{index + 1}"
            if source.exists():
                source.replace(destination)

        oldest = log_file.parent / f"{log_file.name}.{backup_count + 1}"
        if oldest.exists():
            oldest.unlink()

        log_file.replace(log_file.parent / f"{log_file.name}.1")
    except OSError:
        pass


def setup_logging(
    log_dir: Optional[Path] = None,
    level_name: Optional[str] = None,
) -> logging.Logger:
    """Configure root logging to a rotating file under the cache directory.

    Args:
        log_dir: Override the log directory (defaults to the ``logs`` dir next to
            the cached config).
        level_name: Override the log level name (defaults to the
            ``SOW_ADMIN_LOG_LEVEL`` environment variable, then ``INFO``).

    Returns:
        The configured root logger.
    """
    root = logging.getLogger()

    target_dir = log_dir if log_dir is not None else get_log_path().parent

    # Resolve level, falling back to INFO for unknown names.
    name = level_name if level_name is not None else os.environ.get("SOW_ADMIN_LOG_LEVEL", "INFO")
    level = getattr(logging, str(name).upper(), None)
    if not isinstance(level, int):
        level = logging.INFO

    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:  # pragma: no cover - depends on filesystem permissions
        root.warning(f"Could not create log directory {target_dir}: {e}")
        return root

    log_file = target_dir / DEFAULT_LOG_FILENAME
    _rotate_log_if_needed(log_file)

    try:
        handler = logging.FileHandler(log_file, mode="a", encoding="utf-8")
    except OSError as e:  # pragma: no cover - depends on filesystem permissions
        root.warning(f"Could not open log file {log_file}: {e}")
        return root

    handler.setFormatter(logging.Formatter(LOG_FORMAT))

    root.handlers.clear()
    root.setLevel(level)
    root.addHandler(handler)

    if level > logging.DEBUG:
        for noisy in _NOISY_LOGGERS:
            logging.getLogger(noisy).setLevel(logging.WARNING)

    root.info("Log file: %s (level=%s)", log_file, logging.getLevelName(level))
    return root
