"""Audio playback service using PyAudio backend."""
import threading
from pathlib import Path
import numpy as np
import soundfile as sf

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
                self._pyaudio = None

    def load(self, audio_path: Path, section_start: float | None = None, section_end: float | None = None) -> bool:
        """Load an audio file for playback.

        Args:
            audio_path: Path to the audio file
            section_start: Start time in seconds (None = from beginning)
            section_end: End time in seconds (None = to end)

        Returns:
            True if loaded successfully, False otherwise
        """
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
            self.is_playing = False
            self.is_paused = True
            self._stop_flag.set()

            # Stop stream first to unblock any pending write() calls
            if self._stream:
                try:
                    self._stream.stop_stream()
                except Exception:
                    pass

            # Wait for playback thread to finish
            if self._playback_thread and self._playback_thread.is_alive():
                self._playback_thread.join(timeout=0.5)

            # Clear thread reference
            self._playback_thread = None

            # Close stream (will be reopened on resume)
            if self._stream:
                try:
                    self._stream.close()
                except Exception:
                    pass
                self._stream = None

    def stop(self):
        """Stop playback."""
        self.is_playing = False
        self.is_paused = False
        self._stop_flag.set()

        # Stop stream first to unblock any pending write() calls
        if self._stream:
            try:
                self._stream.stop_stream()
            except Exception:
                pass  # Stream may already be stopped

        # Wait for playback thread to finish (should be quick now that stream is stopped)
        if self._playback_thread and self._playback_thread.is_alive():
            self._playback_thread.join(timeout=0.5)

        # Clear thread reference
        self._playback_thread = None

        # Close stream
        if self._stream:
            try:
                self._stream.close()
            except Exception:
                pass
            self._stream = None

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
            self._stream = self._pyaudio.open(
                format=pyaudio.paFloat32,
                channels=self._channels,
                rate=self._sample_rate,
                output=True
            )

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
            if "Stream not open" not in str(e):
                print(f"Playback error: {e}")
        finally:
            # Clean up stream (may already be closed by stop())
            if self._stream:
                try:
                    self._stream.stop_stream()
                except Exception:
                    pass
                try:
                    self._stream.close()
                except Exception:
                    pass
                self._stream = None

    def __del__(self):
        """Clean up resources."""
        self.stop()
        if self._pyaudio:
            self._pyaudio.terminate()
