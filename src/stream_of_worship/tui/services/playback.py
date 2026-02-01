"""Audio playback service for TUI preview.

Uses pygame for cross-platform audio playback with seek support.
"""

from pathlib import Path
from typing import Optional
from threading import Thread, Lock
from queue import Queue


class PlaybackService:
    """Audio playback service with seek support."""

    def __init__(self):
        """Initialize playback service."""
        self._playing = False
        self._paused = False
        self._current_file: Optional[Path] = None
        self._position = 0.0
        self._duration = 0.0
        self._lock = Lock()
        self._command_queue: Queue = Queue()

    @property
    def is_playing(self) -> bool:
        """Check if audio is currently playing."""
        with self._lock:
            return self._playing

    @property
    def is_paused(self) -> bool:
        """Check if audio is currently paused."""
        with self._lock:
            return self._paused

    @property
    def is_stopped(self) -> bool:
        """Check if audio is stopped (not playing and not paused)."""
        with self._lock:
            return not self._playing and not self._paused

    @property
    def current_file(self) -> Optional[Path]:
        """Get the currently loaded file."""
        with self._lock:
            return self._current_file

    @property
    def position(self) -> float:
        """Get current playback position in seconds."""
        with self._lock:
            return self._position

    @property
    def duration(self) -> float:
        """Get the duration of the currently loaded file."""
        with self._lock:
            return self._duration

    def load(self, file_path: Path) -> bool:
        """Load an audio file for playback.

        Args:
            file_path: Path to the audio file

        Returns:
            True if successful, False otherwise
        """
        with self._lock:
            self._current_file = file_path

        # In a full implementation, this would:
        # 1. Load the audio file
        # 2. Get duration
        # 3. Reset position to 0

        # For now, return True as placeholder
        return True

    def play(self) -> bool:
        """Start or resume playback.

        Returns:
            True if successful, False otherwise
        """
        if not self._current_file:
            return False

        with self._lock:
            if self._paused:
                self._paused = False
                self._playing = True
                return True
            if not self._playing:
                self._playing = True
                return True
        return False

    def pause(self) -> bool:
        """Pause playback.

        Returns:
            True if successful, False otherwise
        """
        with self._lock:
            if self._playing:
                self._paused = True
                self._playing = False
                return True
        return False

    def stop(self) -> bool:
        """Stop playback and reset position.

        Returns:
            True if successful
        """
        with self._lock:
            was_playing = self._playing
            self._playing = False
            self._paused = False
            self._position = 0.0
            return was_playing

    def seek(self, position_seconds: float) -> bool:
        """Seek to a specific position.

        Args:
            position_seconds: Position to seek to in seconds

        Returns:
            True if successful, False otherwise
        """
        with self._lock:
            if position_seconds < 0:
                position_seconds = 0
            elif position_seconds > self._duration:
                position_seconds = self._duration

            self._position = position_seconds
            return True

    def seek_relative(self, delta_seconds: float) -> bool:
        """Seek relative to current position.

        Args:
            delta_seconds: Seconds to seek (positive or negative)

        Returns:
            True if successful, False otherwise
        """
        return self.seek(self._position + delta_seconds)
