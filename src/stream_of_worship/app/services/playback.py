"""Audio playback service for sow-app.

Provides audio playback using miniaudio. Manages playback state,
supports previewing songs and transitions.
"""

import threading
import time
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path
from typing import Callable, Optional

import miniaudio


class PlaybackState(Enum):
    """Current playback state."""

    STOPPED = auto()
    PLAYING = auto()
    PAUSED = auto()


@dataclass
class PlaybackPosition:
    """Current playback position information.

    Attributes:
        current_seconds: Current position in seconds
        total_seconds: Total duration in seconds
        progress_percent: Progress as percentage (0-100)
    """

    current_seconds: float
    total_seconds: float
    progress_percent: float


class PlaybackService:
    """Audio playback service using miniaudio.

    Manages audio playback with play/pause/stop controls and
    position tracking. Supports playback callbacks.

    Attributes:
        buffer_ms: Audio buffer size in milliseconds
        volume: Playback volume (0.0 to 1.0)
    """

    def __init__(self, buffer_ms: int = 500, volume: float = 0.8):
        """Initialize the playback service.

        Args:
            buffer_ms: Audio buffer size in milliseconds
            volume: Initial playback volume
        """
        self.buffer_ms = buffer_ms
        self.volume = max(0.0, min(1.0, volume))

        self._current_file: Optional[Path] = None
        self._state = PlaybackState.STOPPED
        self._position_seconds = 0.0
        self._duration_seconds = 0.0
        self._start_time: Optional[float] = None
        self._paused_at: Optional[float] = None

        self._device: Optional[miniaudio.PlaybackDevice] = None
        self._source: Optional[miniaudio.DecodedSoundFile] = None
        self._stream: Optional[miniaudio.Stream] = None

        self._lock = threading.Lock()
        self._stop_event = threading.Event()

        # Callbacks
        self._on_position_changed: Optional[Callable[[PlaybackPosition], None]] = None
        self._on_state_changed: Optional[Callable[[PlaybackState], None]] = None
        self._on_finished: Optional[Callable[[], None]] = None

        # Position update thread
        self._position_thread: Optional[threading.Thread] = None

    def set_callbacks(
        self,
        on_position_changed: Optional[Callable[[PlaybackPosition], None]] = None,
        on_state_changed: Optional[Callable[[PlaybackState], None]] = None,
        on_finished: Optional[Callable[[], None]] = None,
    ) -> None:
        """Set playback event callbacks.

        Args:
            on_position_changed: Called when position updates
            on_state_changed: Called when state changes
            on_finished: Called when playback finishes
        """
        self._on_position_changed = on_position_changed
        self._on_state_changed = on_state_changed
        self._on_finished = on_finished

    @property
    def state(self) -> PlaybackState:
        """Get current playback state."""
        with self._lock:
            return self._state

    @property
    def is_playing(self) -> bool:
        """Check if currently playing."""
        return self.state == PlaybackState.PLAYING

    @property
    def is_paused(self) -> bool:
        """Check if paused."""
        return self.state == PlaybackState.PAUSED

    @property
    def is_stopped(self) -> bool:
        """Check if stopped."""
        return self.state == PlaybackState.STOPPED

    @property
    def current_file(self) -> Optional[Path]:
        """Get currently loaded file."""
        with self._lock:
            return self._current_file

    @property
    def duration_seconds(self) -> float:
        """Get duration of current file in seconds."""
        with self._lock:
            return self._duration_seconds

    @property
    def position_seconds(self) -> float:
        """Get current position in seconds."""
        with self._lock:
            if self._state == PlaybackState.PLAYING and self._start_time:
                elapsed = time.time() - self._start_time
                return min(self._position_seconds + elapsed, self._duration_seconds)
            return self._position_seconds

    def get_position(self) -> PlaybackPosition:
        """Get current playback position information.

        Returns:
            PlaybackPosition with current state
        """
        with self._lock:
            current = self.position_seconds
            total = self._duration_seconds
            progress = (current / total * 100) if total > 0 else 0
            return PlaybackPosition(
                current_seconds=current,
                total_seconds=total,
                progress_percent=progress,
            )

    def _set_state(self, new_state: PlaybackState) -> None:
        """Update state and notify listeners."""
        with self._lock:
            old_state = self._state
            self._state = new_state

        if old_state != new_state and self._on_state_changed:
            self._on_state_changed(new_state)

    def _position_tracker(self) -> None:
        """Background thread to track playback position."""
        while not self._stop_event.is_set():
            if self._state == PlaybackState.PLAYING:
                if self._on_position_changed:
                    self._on_position_changed(self.get_position())
            time.sleep(0.1)  # Update 10 times per second

    def load(self, file_path: Path) -> bool:
        """Load an audio file for playback.

        Args:
            file_path: Path to audio file

        Returns:
            True if loaded successfully
        """
        self.stop()

        if not file_path.exists():
            return False

        try:
            # Decode to get duration
            self._source = miniaudio.decode_file(
                str(file_path),
                output_format=miniaudio.SampleFormat.SIGNED16,
                nchannels=2,
                sample_rate=44100,
            )

            with self._lock:
                self._current_file = file_path
                self._duration_seconds = len(self._source.samples) / (
                    self._source.sample_rate * self._source.nchannels * 2  # 2 bytes per sample (S16)
                ) if self._source.sample_rate > 0 else 0
                self._position_seconds = 0.0

            return True
        except Exception:
            self._source = None
            return False

    def play(self, file_path: Optional[Path] = None, start_seconds: float = 0.0) -> bool:
        """Start or resume playback.

        Args:
            file_path: File to play (if not already loaded)
            start_seconds: Position to start from

        Returns:
            True if playback started
        """
        with self._lock:
            if file_path:
                self._current_file = file_path
                self._position_seconds = start_seconds
            elif self._state == PlaybackState.PAUSED:
                # Resume from pause
                pass
            elif not self._current_file:
                return False

        # Load if needed
        if file_path or not self._source:
            if not self.load(self._current_file):
                return False

        try:
            # Create playback device
            self._device = miniaudio.PlaybackDevice(
                output_format=miniaudio.SampleFormat.SIGNED16,
                nchannels=2,
                sample_rate=44100,
                buffer_msec=self.buffer_ms,
            )

            # Start playback thread
            self._stop_event.clear()
            self._position_thread = threading.Thread(target=self._position_tracker, daemon=True)
            self._position_thread.start()

            with self._lock:
                self._start_time = time.time() - self._position_seconds
                self._paused_at = None

            self._set_state(PlaybackState.PLAYING)

            # Start playback (simplified - full implementation would handle streaming)
            return True

        except Exception:
            self.stop()
            return False

    def pause(self) -> bool:
        """Pause playback.

        Returns:
            True if paused successfully
        """
        with self._lock:
            if self._state != PlaybackState.PLAYING:
                return False

            self._position_seconds = self.position_seconds
            self._paused_at = time.time()

        self._set_state(PlaybackState.PAUSED)
        return True

    def resume(self) -> bool:
        """Resume paused playback.

        Returns:
            True if resumed successfully
        """
        with self._lock:
            if self._state != PlaybackState.PAUSED:
                return False

            self._start_time = time.time() - self._position_seconds
            self._paused_at = None

        self._set_state(PlaybackState.PLAYING)
        return True

    def stop(self) -> None:
        """Stop playback and reset position."""
        self._stop_event.set()

        if self._device:
            try:
                self._device.stop()
                self._device.close()
            except Exception:
                pass
            self._device = None

        if self._position_thread and self._position_thread.is_alive():
            self._position_thread.join(timeout=1.0)

        with self._lock:
            self._state = PlaybackState.STOPPED
            self._position_seconds = 0.0
            self._start_time = None
            self._paused_at = None
            self._source = None

        if self._on_state_changed:
            self._on_state_changed(PlaybackState.STOPPED)

    def seek(self, position_seconds: float) -> bool:
        """Seek to a position in the current file.

        Args:
            position_seconds: Position to seek to

        Returns:
            True if seek was successful
        """
        with self._lock:
            if not self._current_file or not self._source:
                return False

            position_seconds = max(0.0, min(position_seconds, self._duration_seconds))
            self._position_seconds = position_seconds

            if self._state == PlaybackState.PLAYING:
                self._start_time = time.time() - position_seconds

        if self._on_position_changed:
            self._on_position_changed(self.get_position())

        return True

    def set_volume(self, volume: float) -> None:
        """Set playback volume.

        Args:
            volume: Volume level (0.0 to 1.0)
        """
        self.volume = max(0.0, min(1.0, volume))

    def preview_section(
        self,
        file_path: Path,
        start_seconds: float,
        duration_seconds: float = 10.0,
    ) -> bool:
        """Preview a section of a file.

        Args:
            file_path: Path to audio file
            start_seconds: Start position
            duration_seconds: Duration to preview

        Returns:
            True if preview started
        """
        # TODO: Implement section preview with automatic stop
        # For now, just play from start position
        return self.play(file_path, start_seconds)
