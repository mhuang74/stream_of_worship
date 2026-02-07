"""Logging configuration for sow-app.

Provides session logging to file without interfering with TUI display.
"""

import logging
from pathlib import Path
from datetime import datetime


def setup_logging(log_dir: Path) -> logging.Logger:
    """Set up application logging to file.

    Args:
        log_dir: Directory to store log files

    Returns:
        Configured logger instance
    """
    # Ensure log directory exists
    log_dir.mkdir(parents=True, exist_ok=True)

    # Create log file with timestamp
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = log_dir / f"sow_app_{timestamp}.log"

    # Configure root logger
    logger = logging.getLogger("sow_app")
    logger.setLevel(logging.DEBUG)

    # Remove any existing handlers
    logger.handlers.clear()

    # File handler with detailed formatting
    file_handler = logging.FileHandler(log_file, mode="w", encoding="utf-8")
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
