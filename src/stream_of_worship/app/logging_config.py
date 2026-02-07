"""Logging configuration for sow-app.

Provides session logging to file without interfering with TUI display.
"""

import logging
from pathlib import Path
from datetime import datetime


def _rotate_log_if_needed(log_file: Path, max_bytes: int = 10 * 1024 * 1024, backup_count: int = 5) -> None:
    """Rotate log file on startup if it exceeds max size.

    Args:
        log_file: Path to the log file
        max_bytes: Maximum file size before rotation (default: 10MB)
        backup_count: Number of backup files to keep (default: 5)
    """
    if not log_file.exists():
        return

    # Check if rotation is needed
    if log_file.stat().st_size < max_bytes:
        return

    # Rotate existing backups (5 -> 6 [delete], 4 -> 5, 3 -> 4, etc.)
    for i in range(backup_count - 1, 0, -1):
        source = log_file.parent / f"{log_file.name}.{i}"
        dest = log_file.parent / f"{log_file.name}.{i + 1}"

        if i == backup_count - 1:
            # Delete oldest backup
            if dest.exists():
                dest.unlink()

        if source.exists():
            source.rename(dest)

    # Move current log to .1
    backup = log_file.parent / f"{log_file.name}.1"
    log_file.rename(backup)


def setup_logging(log_dir: Path) -> logging.Logger:
    """Set up application logging to file with startup rotation.

    Args:
        log_dir: Directory to store log files

    Returns:
        Configured logger instance
    """
    # Ensure log directory exists
    log_dir.mkdir(parents=True, exist_ok=True)

    # Use fixed log filename
    log_file = log_dir / "sow_app.log"

    # Rotate log if it's too large
    _rotate_log_if_needed(log_file)

    # Configure root logger
    logger = logging.getLogger("sow_app")
    logger.setLevel(logging.DEBUG)

    # Remove any existing handlers
    logger.handlers.clear()

    # File handler with append mode (since we rotated if needed)
    file_handler = logging.FileHandler(log_file, mode="a", encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)

    # Detailed format with timestamp, level, module, and message
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)-20s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S.%f",
    )
    file_handler.setFormatter(formatter)

    logger.addHandler(file_handler)

    # Log startup
    logger.info("=" * 80)
    logger.info("SOW-APP SESSION STARTED")
    logger.info(f"Log file: {log_file}")
    logger.info("=" * 80)

    return logger


def get_logger(name: str) -> logging.Logger:
    """Get a logger for a specific module.

    Args:
        name: Module name (usually __name__)

    Returns:
        Logger instance
    """
    return logging.getLogger(f"sow_app.{name}")
