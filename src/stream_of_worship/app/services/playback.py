"""Audio playback service for sow-app.

Provides audio playback using miniaudio. Manages playback state,
supports previewing songs and transitions.
"""

import threading
import time
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path
from typing import Callable, Generator, Optional

import miniaudio
import numpy as np

from stream_of_worship.app.logging_config import get_logger

logger = get_logger(__name__)


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
        self._generator: Optional[Generator] = None

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
            logger.error(f"Audio file not found: {file_path}")
            return False

        try:
            logger.debug(f"Loading audio file: {file_path} ({file_path.stat().st_size} bytes)")

            # Decode to get duration
            self._source = miniaudio.decode_file(
                str(file_path),
                output_format=miniaudio.SampleFormat.SIGNED16,
                nchannels=2,
                sample_rate=44100,
            )

            # Calculate duration (samples is array.array of int16 values, interleaved)
            # Total frames = total_samples / nchannels
            total_frames = len(self._source.samples) // self._source.nchannels
            duration = total_frames / self._source.sample_rate if self._source.sample_rate > 0 else 0

            logger.debug(f"Audio loaded: {self._source.sample_rate}Hz, {self._source.nchannels}ch, "
                        f"{len(self._source.samples)} samples, {duration:.2f}s duration")

            with self._lock:
                self._current_file = file_path
                self._duration_seconds = duration
                self._position_seconds = 0.0

            return True
        except Exception as e:
            logger.error(f"Failed to load audio: {e}")
            self._source = None
            return False

    def _stream_generator(self, source_samples, start_sample):
        """Coroutine generator that yields audio chunks as requested by miniaudio.

        This generator receives the number of frames needed via .send() and yields
        that exact amount of audio data as a numpy array with shape (num_frames, nchannels).

        Args:
            source_samples: array.array of int16 audio samples (interleaved stereo)
            start_sample: Starting sample position (not bytes)

        Yields:
            Numpy arrays with shape (num_frames, nchannels) and dtype int16
        """
        sample_pos = start_sample  # Position in samples, not bytes
        chunks_yielded = 0
        nchannels = 2  # Stereo

        logger.debug(f"Stream generator starting: start_sample={start_sample}, total_samples={len(source_samples)}, volume={self.volume}")

        try:
            # Initialize: yield empty array, receive first frame request
            num_frames = yield np.zeros((0, nchannels), dtype=np.int16)
            logger.debug(f"Generator primed, first request: {num_frames} frames")

            while not self._stop_event.is_set() and sample_pos < len(source_samples):
                # Handle None or invalid frame requests
                if num_frames is None or num_frames <= 0:
                    logger.warning(f"Invalid frame request: {num_frames}")
                    break

                # Calculate how many samples we need (frames * channels)
                samples_needed = num_frames * nchannels
                end_pos = min(sample_pos + samples_needed, len(source_samples))
                chunk = source_samples[sample_pos:end_pos]

                # Convert array.array to numpy array
                samples = np.array(chunk, dtype=np.int16)

                # Pad with zeros if we don't have enough samples
                if len(samples) < samples_needed:
                    padding = samples_needed - len(samples)
                    samples = np.concatenate([samples, np.zeros(padding, dtype=np.int16)])
                    logger.debug(f"Padded with {padding} samples of silence")

                # Apply volume scaling
                if self.volume != 1.0:
                    samples = (samples * self.volume).astype(np.int16)

                # Reshape to (num_frames, nchannels)
                samples = samples.reshape((num_frames, nchannels))

                chunks_yielded += 1
                if chunks_yielded <= 5:
                    logger.debug(f"Yielding chunk {chunks_yielded}: shape {samples.shape} at sample pos {sample_pos}")

                sample_pos = end_pos

                # Yield the samples array and receive next frame request
                num_frames = yield samples

                if chunks_yielded <= 5:
                    logger.debug(f"Received next frame request: {num_frames}")

                # If we've reached the end, break
                if sample_pos >= len(source_samples):
                    logger.debug("Reached end of audio samples")
                    break

        except GeneratorExit:
            logger.debug(f"Stream generator stopped externally: yielded {chunks_yielded} chunks, final pos={current_pos}")
            return

        logger.debug(f"Stream generator ending naturally: yielded {chunks_yielded} chunks, final pos={current_pos}")

        # Playback finished naturally
        if not self._stop_event.is_set():
            self._set_state(PlaybackState.STOPPED)
            if self._on_finished:
                self._on_finished()

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

        # Stop any existing playback first
        self.stop()

        # Load if needed
        if file_path or not self._source:
            if not self.load(self._current_file):
                return False

        try:
            # Calculate start position in samples (source.samples is array.array of int16 values)
            sample_rate = self._source.sample_rate
            nchannels = self._source.nchannels
            # Calculate sample index (frames * channels since samples are interleaved)
            start_sample_index = int(self._position_seconds * sample_rate * nchannels)

            if start_sample_index >= len(self._source.samples):
                return False

            logger.debug(f"Starting playback: start_sample={start_sample_index}, file={self._current_file}, volume={self.volume}")

            # Create and prime the generator (must be started before passing to device)
            self._generator = self._stream_generator(self._source.samples, start_sample_index)
            next(self._generator)  # Prime the generator

            # Create playback device
            self._device = miniaudio.PlaybackDevice(
                output_format=miniaudio.SampleFormat.SIGNED16,
                nchannels=nchannels,
                sample_rate=sample_rate,
            )

            # Start playback thread for position tracking
            self._stop_event.clear()
            self._position_thread = threading.Thread(target=self._position_tracker, daemon=True)
            self._position_thread.start()

            with self._lock:
                self._start_time = time.time() - self._position_seconds
                self._paused_at = None

            self._set_state(PlaybackState.PLAYING)

            # Start the device with the primed generator
            # miniaudio will send() the required number of frames to the generator
            self._device.start(self._generator)

            logger.debug("Playback device started successfully")
            return True

        except Exception as e:
            logger.error(f"Playback error: {e}", exc_info=True)
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

        # Close generator first
        if self._generator:
            try:
                self._generator.close()
            except Exception:
                pass
            self._generator = None

        # Stop and close device
        if self._device:
            try:
                self._device.stop()
                self._device.close()
            except Exception:
                pass
            self._device = None

        # Wait for position thread to finish
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
