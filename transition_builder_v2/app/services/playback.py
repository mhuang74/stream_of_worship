"""Audio playback service using PyAudio backend."""
import threading
import time
from pathlib import Path
import numpy as np
import soundfile as sf

from app.utils.logger import get_error_logger

try:
    import pyaudio
    PYAUDIO_AVAILABLE = True
except ImportError:
    PYAUDIO_AVAILABLE = False


class PlaybackService:
    """Manages audio playback using PyAudio backend.

    Implements cross-platform audio playback as per the design specification.
    """

    def __init__(self):
        """Initialize the playback service."""
        self.current_file: Path | None = None
        self.is_playing: bool = False
        self.is_paused: bool = False
        self.position: float = 0.0  # Current position in seconds
        self.duration: float = 0.0  # Total duration in seconds

        # Section boundaries (None = play entire file)
        self._section_start: float | None = None
        self._section_end: float | None = None

        # Audio data
        self._audio_data: np.ndarray | None = None
        self._sample_rate: int = 0
        self._channels: int = 0

        # PyAudio components
        self._pyaudio: pyaudio.PyAudio | None = None
        self._stream: pyaudio.Stream | None = None
        self._playback_thread: threading.Thread | None = None
        self._stop_flag: threading.Event = threading.Event()

        # Initialize PyAudio if available
        if PYAUDIO_AVAILABLE:
            try:
                self._pyaudio = pyaudio.PyAudio()
            except Exception as e:
                print(f"Warning: Failed to initialize PyAudio: {e}")
                logger = get_error_logger()
                if logger:
                    logger.log_playback_error("PyAudio", e, operation="initialization")
                self._pyaudio = None

    def load(self, audio_path: Path | str, section_start: float | None = None, section_end: float | None = None) -> bool:
        """Load an audio file for playback.

        Args:
            audio_path: Path to the audio file (Path object or string)
            section_start: Start time in seconds (None = from beginning)
            section_end: End time in seconds (None = to end)

        Returns:
            True if loaded successfully, False otherwise
        """
        # Convert string path to Path object if needed
        if not isinstance(audio_path, Path):
            audio_path = Path(audio_path)

        if not audio_path.exists():
            return False

        # Stop any current playback
        self.stop()

        try:
            # Load audio file using soundfile
            self._audio_data, self._sample_rate = sf.read(str(audio_path), dtype='float32')

            # Handle mono/stereo
            if len(self._audio_data.shape) == 1:
                self._channels = 1
            else:
                self._channels = self._audio_data.shape[1]

            # Store section boundaries
            self._section_start = section_start
            self._section_end = section_end

            # Calculate duration (section or full file)
            if section_start is not None and section_end is not None:
                self.duration = section_end - section_start
                self.position = section_start
            else:
                self.duration = len(self._audio_data) / self._sample_rate
                self.position = 0.0

            self.current_file = audio_path

            return True

        except Exception as e:
            print(f"Error loading audio file: {e}")
            logger = get_error_logger()
            if logger:
                logger.log_playback_error(str(audio_path), e, operation="load")
            return False

    def play(self):
        """Start or resume playback."""
        if self.current_file is None or self._audio_data is None:
            return

        if not PYAUDIO_AVAILABLE or self._pyaudio is None:
            # Stub mode: just update state
            self.is_playing = True
            self.is_paused = False
            return

        # Start or resume playback
        if not self.is_playing:
            self.is_playing = True
            self.is_paused = False
            self._stop_flag.clear()

            # Start playback thread
            self._playback_thread = threading.Thread(target=self._playback_loop, daemon=True)
            self._playback_thread.start()

    def pause(self):
        """Pause playback."""
        if self.is_playing:
            # Track if we had an active stream (for sleep at end)
            had_active_stream = self._stream is not None

            self.is_playing = False
            self.is_paused = True
            self._stop_flag.set()

            # Stop stream first to unblock any pending write() calls
            if self._stream:
                try:
                    self._stream.stop_stream()
                except Exception as e:
                    self._log_stream_error(e, "stop_stream")

            # Wait for playback thread to finish
            if self._playback_thread and self._playback_thread.is_alive():
                self._playback_thread.join(timeout=0.5)

            # Clear thread reference
            self._playback_thread = None

            # Close stream (will be reopened on resume)
            if self._stream:
                try:
                    self._stream.close()
                except Exception as e:
                    self._log_stream_error(e, "close_stream")
                self._stream = None

            # Give macOS CoreAudio time to release the audio device
            if had_active_stream:
                time.sleep(0.1)

    def stop(self):
        """Stop playback."""
        # Track if we had an active stream (for sleep at end)
        had_active_stream = self._stream is not None or (
            self._playback_thread is not None and self._playback_thread.is_alive()
        )

        self.is_playing = False
        self.is_paused = False
        self._stop_flag.set()

        # Stop stream first to unblock any pending write() calls
        if self._stream:
            try:
                self._stream.stop_stream()
            except Exception as e:
                self._log_stream_error(e, "stop_stream")

        # Wait for playback thread to finish (should be quick now that stream is stopped)
        if self._playback_thread and self._playback_thread.is_alive():
            self._playback_thread.join(timeout=0.5)

        # Clear thread reference
        self._playback_thread = None

        # Close stream (may have been closed by finally block already)
        if self._stream:
            try:
                self._stream.close()
            except Exception as e:
                self._log_stream_error(e, "close_stream")
            self._stream = None

        # Give macOS CoreAudio time to release the audio device
        # Without this delay, opening a new stream immediately can fail with -50
        # Must sleep even if finally block already closed the stream
        if had_active_stream:
            time.sleep(0.1)  # 100ms for more reliable device release

        self.position = 0.0
        self.current_file = None

    def seek(self, offset_seconds: float):
        """Seek by the specified offset (positive or negative).

        Args:
            offset_seconds: Number of seconds to seek (positive = forward, negative = backward)
        """
        if self.current_file is None:
            return

        new_position = self.position + offset_seconds

        # Determine valid range (section or full file)
        if self._section_start is not None and self._section_end is not None:
            min_pos = self._section_start
            max_pos = self._section_end
        else:
            min_pos = 0.0
            max_pos = len(self._audio_data) / self._sample_rate if self._audio_data is not None else 0.0

        # Clamp to valid range (wrap to beginning if past end)
        if new_position < min_pos:
            new_position = min_pos
        elif new_position > max_pos:
            new_position = min_pos  # Wrap to section start

        # If playing, restart playback from new position
        was_playing = self.is_playing
        if was_playing:
            self.pause()

        self.position = new_position

        if was_playing:
            self.play()

    def get_position(self) -> float:
        """Get current playback position in seconds."""
        return self.position

    def get_duration(self) -> float:
        """Get total duration of loaded audio in seconds."""
        return self.duration

    def toggle_play_pause(self):
        """Toggle between play and pause states."""
        if self.is_playing:
            self.pause()
        else:
            self.play()

    def _playback_loop(self):
        """Internal playback loop running in separate thread."""
        if not PYAUDIO_AVAILABLE or self._pyaudio is None or self._audio_data is None:
            return

        try:
            # Open PyAudio stream
            # Note: PortAudio may print benign error messages to stderr (e.g., "-50" errors)
            # These cannot be suppressed as Textual controls the terminal
            try:
                self._stream = self._pyaudio.open(
                    format=pyaudio.paFloat32,
                    channels=self._channels,
                    rate=self._sample_rate,
                    output=True
                )
            except Exception as e:
                logger = get_error_logger()
                if logger and self.current_file:
                    logger.log_playback_error(
                        str(self.current_file), e, operation="open_stream"
                    )
                return

            # Determine playback boundaries
            if self._section_start is not None and self._section_end is not None:
                # Playing a section
                start_frame = int(self.position * self._sample_rate)
                end_frame_limit = int(self._section_end * self._sample_rate)
            else:
                # Playing full file
                start_frame = int(self.position * self._sample_rate)
                end_frame_limit = len(self._audio_data)

            chunk_size = 1024

            # Play audio from current position
            frame = start_frame
            while frame < end_frame_limit and not self._stop_flag.is_set():
                # Get chunk of audio (don't exceed section end)
                end_frame = min(frame + chunk_size, end_frame_limit)
                chunk = self._audio_data[frame:end_frame]

                # Write to stream
                self._stream.write(chunk.tobytes())

                # Update position
                frame = end_frame
                self.position = frame / self._sample_rate

            # Reached end of section/audio
            if frame >= end_frame_limit:
                self.is_playing = False
                # Wrap to section start or beginning
                if self._section_start is not None:
                    self.position = self._section_start
                else:
                    self.position = 0.0

        except Exception as e:
            # Filter out benign errors that occur during normal stop/pause operations
            error_str = str(e)
            benign = any(x in error_str for x in ["Stream not open", "-9986", "-9988"])
            if not benign:
                print(f"Playback error: {e}")
                logger = get_error_logger()
                if logger and self.current_file:
                    logger.log_playback_error(str(self.current_file), e, operation="playback")
        finally:
            # Clean up stream (may already be closed by stop())
            # Note: Don't sleep here - stop() handles the delay for device release
            if self._stream:
                try:
                    self._stream.stop_stream()
                except Exception as e:
                    self._log_stream_error(e, "stop_stream")
                try:
                    self._stream.close()
                except Exception as e:
                    self._log_stream_error(e, "close_stream")
                self._stream = None

    def _log_stream_error(self, error: Exception, operation: str) -> None:
        """Log a stream-related error if it's not a common benign error.

        Args:
            error: The exception that occurred
            operation: The operation that failed (e.g., "stop_stream", "close_stream", "write")
        """
        error_str = str(error)
        # Skip logging for common benign errors that occur during normal cleanup
        # -9986 = paStreamIsStopped (stream already stopped, happens during race between
        #         stop()/pause() and _playback_loop's finally block - this is expected)
        # -9988 = paStreamIsNotStopped (stream not stopped when trying to close)
        benign_errors = [
            "Stream not open",
            "Stream closed",
            "not open",
            "-9986",  # paStreamIsStopped - already stopped
            "-9988",  # paStreamIsNotStopped
        ]
        if any(benign in error_str for benign in benign_errors):
            return

        # Log the error
        logger = get_error_logger()
        if logger:
            audio_path = str(self.current_file) if self.current_file else "unknown"
            logger.log_playback_error(audio_path, error, operation=operation)

    def __del__(self):
        """Clean up resources."""
        self.stop()
        if self._pyaudio:
            self._pyaudio.terminate()
