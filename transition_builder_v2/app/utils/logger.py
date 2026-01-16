"""Error logging utility for the Transition Builder app.

Provides centralized error logging with timestamps and stack traces.
Logs are appended to ./transitions_errors.log when error_logging is enabled.
"""

import traceback
from datetime import datetime
from pathlib import Path
from typing import Optional


class ErrorLogger:
    """Centralized error logging service.

    Appends error events with timestamps and stack traces to a log file.
    Respects the error_logging configuration setting.
    """

    DEFAULT_LOG_FILE = "transitions_errors.log"

    def __init__(self, log_path: Optional[Path] = None, enabled: bool = True):
        """Initialize the error logger.

        Args:
            log_path: Path to the log file. Defaults to ./transitions_errors.log
            enabled: Whether error logging is enabled (from config.error_logging)
        """
        self._enabled = enabled
        self._log_path = log_path or Path(self.DEFAULT_LOG_FILE)

    @property
    def enabled(self) -> bool:
        """Whether error logging is enabled."""
        return self._enabled

    @property
    def log_path(self) -> Path:
        """Path to the log file."""
        return self._log_path

    def log_error(
        self,
        message: str,
        error: Optional[Exception] = None,
        context: Optional[dict] = None
    ) -> None:
        """Log an error event with optional exception and context.

        Args:
            message: Human-readable error description
            error: Optional exception object (stack trace will be included)
            context: Optional dictionary with additional context (e.g., parameters, file paths)
        """
        if not self._enabled:
            return

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        log_entry = self._format_log_entry(timestamp, message, error, context)

        try:
            with open(self._log_path, 'a') as f:
                f.write(log_entry)
        except Exception:
            # If we can't write to the log file, fail silently
            # (we don't want logging failures to crash the app)
            pass

    def log_generation_error(
        self,
        song_a: str,
        song_b: str,
        transition_type: str,
        error: Exception,
        parameters: Optional[dict] = None
    ) -> None:
        """Log a transition generation failure.

        Args:
            song_a: Song A filename
            song_b: Song B filename
            transition_type: Type of transition attempted
            error: The exception that occurred
            parameters: Optional transition parameters
        """
        context = {
            "song_a": song_a,
            "song_b": song_b,
            "transition_type": transition_type,
        }
        if parameters:
            context["parameters"] = parameters

        self.log_error(
            f"Generation failed for transition {song_a} -> {song_b}",
            error=error,
            context=context
        )

    def log_playback_error(
        self,
        audio_path: str,
        error: Exception,
        operation: str = "playback"
    ) -> None:
        """Log an audio playback error.

        Args:
            audio_path: Path to the audio file
            error: The exception that occurred
            operation: The operation that failed (e.g., "load", "playback", "seek")
        """
        self.log_error(
            f"Playback error during {operation}: {audio_path}",
            error=error,
            context={"audio_path": audio_path, "operation": operation}
        )

    def log_file_error(
        self,
        file_path: str,
        error: Exception,
        operation: str = "read"
    ) -> None:
        """Log a file I/O error.

        Args:
            file_path: Path to the file
            error: The exception that occurred
            operation: The operation that failed (e.g., "read", "write", "delete")
        """
        self.log_error(
            f"File {operation} error: {file_path}",
            error=error,
            context={"file_path": file_path, "operation": operation}
        )

    def log_catalog_error(
        self,
        json_path: str,
        error: Exception,
        song_filename: Optional[str] = None
    ) -> None:
        """Log a catalog loading error.

        Args:
            json_path: Path to the catalog JSON file
            error: The exception that occurred
            song_filename: Optional specific song that failed to load
        """
        context = {"json_path": json_path}
        if song_filename:
            context["song_filename"] = song_filename
            message = f"Catalog error loading song {song_filename}"
        else:
            message = f"Catalog error loading {json_path}"

        self.log_error(message, error=error, context=context)

    def _format_log_entry(
        self,
        timestamp: str,
        message: str,
        error: Optional[Exception],
        context: Optional[dict]
    ) -> str:
        """Format a log entry with all components.

        Args:
            timestamp: Formatted timestamp string
            message: Error message
            error: Optional exception
            context: Optional context dictionary

        Returns:
            Formatted log entry string
        """
        lines = [
            f"{timestamp} [ERROR] {message}",
        ]

        if error:
            lines.append(f"  Error: {type(error).__name__}: {error}")

        if context:
            lines.append("  Context:")
            for key, value in context.items():
                if isinstance(value, dict):
                    lines.append(f"    {key}:")
                    for k, v in value.items():
                        lines.append(f"      {k}: {v}")
                else:
                    lines.append(f"    {key}: {value}")

        if error:
            lines.append("  Stack trace:")
            # Get the full traceback as a string
            tb_lines = traceback.format_exception(type(error), error, error.__traceback__)
            for tb_line in tb_lines:
                # Indent each line of the traceback
                for sub_line in tb_line.rstrip().split('\n'):
                    lines.append(f"    {sub_line}")

        # Add separator between entries
        lines.append("-" * 80)
        lines.append("")

        return '\n'.join(lines)


# Global logger instance (initialized by main.py)
_error_logger: Optional[ErrorLogger] = None


def get_error_logger() -> Optional[ErrorLogger]:
    """Get the global error logger instance.

    Returns:
        The global ErrorLogger instance, or None if not initialized
    """
    return _error_logger


def init_error_logger(log_path: Optional[Path] = None, enabled: bool = True) -> ErrorLogger:
    """Initialize the global error logger.

    Args:
        log_path: Path to the log file
        enabled: Whether error logging is enabled

    Returns:
        The initialized ErrorLogger instance
    """
    global _error_logger
    _error_logger = ErrorLogger(log_path=log_path, enabled=enabled)
    return _error_logger
