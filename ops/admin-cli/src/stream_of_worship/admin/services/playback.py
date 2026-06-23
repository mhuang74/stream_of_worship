"""Audio playback service for the admin LRC editor.

Provides audio playback using miniaudio with threading. Designed for
the admin interactive editor: supports play/pause/seek/skip and
position callbacks for lyric line tracking.
"""

import logging
import threading
import time
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path
from typing import Callable, Generator, Optional

import miniaudio
import numpy as np

logger = logging.getLogger(__name__)


class PlaybackState(Enum):
    STOPPED = auto()
    PLAYING = auto()
    PAUSED = auto()


@dataclass
class PlaybackPosition:
    current_seconds: float
    total_seconds: float
    progress_percent: float


class PlaybackService:
    """Audio playback service using miniaudio for the admin LRC editor.

    Manages audio playback with play/pause/stop/seek controls and
    position tracking. Supports playback callbacks for UI updates.
    """

    def __init__(self, volume: float = 0.8):
        self.volume = max(0.0, min(1.0, volume))

        self._current_file: Optional[Path] = None
        self._state = PlaybackState.STOPPED
        self._position_seconds = 0.0
        self._duration_seconds = 0.0
        self._start_time: Optional[float] = None

        self._device: Optional[miniaudio.PlaybackDevice] = None
        self._source: Optional[miniaudio.DecodedSoundFile] = None
        self._generator: Optional[Generator] = None

        self._lock = threading.Lock()
        self._stop_event = threading.Event()

        self._on_position_changed: Optional[Callable[[PlaybackPosition], None]] = None
        self._on_state_changed: Optional[Callable[[PlaybackState], None]] = None
        self._on_finished: Optional[Callable[[], None]] = None

        self._position_thread: Optional[threading.Thread] = None

    def set_callbacks(
        self,
        on_position_changed: Optional[Callable[[PlaybackPosition], None]] = None,
        on_state_changed: Optional[Callable[[PlaybackState], None]] = None,
        on_finished: Optional[Callable[[], None]] = None,
    ) -> None:
        self._on_position_changed = on_position_changed
        self._on_state_changed = on_state_changed
        self._on_finished = on_finished

    @property
    def state(self) -> PlaybackState:
        with self._lock:
            return self._state

    @property
    def is_playing(self) -> bool:
        return self.state == PlaybackState.PLAYING

    @property
    def is_paused(self) -> bool:
        return self.state == PlaybackState.PAUSED

    @property
    def duration_seconds(self) -> float:
        with self._lock:
            return self._duration_seconds

    @property
    def position_seconds(self) -> float:
        with self._lock:
            if self._state == PlaybackState.PLAYING and self._start_time:
                elapsed = time.time() - self._start_time
                return min(self._position_seconds + elapsed, self._duration_seconds)
            return self._position_seconds

    def get_position(self) -> PlaybackPosition:
        with self._lock:
            if self._state == PlaybackState.PLAYING and self._start_time:
                elapsed = time.time() - self._start_time
                current = min(self._position_seconds + elapsed, self._duration_seconds)
            else:
                current = self._position_seconds
            total = self._duration_seconds
            progress = (current / total * 100) if total > 0 else 0
            return PlaybackPosition(
                current_seconds=current,
                total_seconds=total,
                progress_percent=progress,
            )

    def _set_state(self, new_state: PlaybackState) -> None:
        with self._lock:
            old_state = self._state
            self._state = new_state

        if old_state != new_state and self._on_state_changed:
            self._on_state_changed(new_state)

    def _position_tracker(self) -> None:
        while not self._stop_event.is_set():
            if self._state == PlaybackState.PLAYING:
                if self._on_position_changed:
                    self._on_position_changed(self.get_position())
            time.sleep(0.1)

    def load(self, file_path: Path) -> bool:
        if self._state != PlaybackState.STOPPED:
            self.stop(clear_source=True)

        if not file_path.exists():
            logger.error(f"Audio file not found: {file_path}")
            return False

        try:
            self._source = miniaudio.decode_file(
                str(file_path),
                output_format=miniaudio.SampleFormat.SIGNED16,
                nchannels=2,
                sample_rate=44100,
            )

            total_frames = len(self._source.samples) // self._source.nchannels
            duration = total_frames / self._source.sample_rate if self._source.sample_rate > 0 else 0

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
        sample_pos = start_sample
        nchannels = 2

        try:
            num_frames = yield np.zeros((0, nchannels), dtype=np.int16)

            while not self._stop_event.is_set() and sample_pos < len(source_samples):
                if num_frames is None or num_frames <= 0:
                    break

                samples_needed = num_frames * nchannels
                end_pos = min(sample_pos + samples_needed, len(source_samples))
                chunk = source_samples[sample_pos:end_pos]

                samples = np.array(chunk, dtype=np.int16)

                if len(samples) < samples_needed:
                    padding = samples_needed - len(samples)
                    samples = np.concatenate([samples, np.zeros(padding, dtype=np.int16)])

                if self.volume != 1.0:
                    samples = (samples * self.volume).astype(np.int16)

                samples = samples.reshape((num_frames, nchannels))
                sample_pos = end_pos

                num_frames = yield samples

                if sample_pos >= len(source_samples):
                    break

        except GeneratorExit:
            return

        if not self._stop_event.is_set():
            self._set_state(PlaybackState.STOPPED)
            if self._on_finished:
                self._on_finished()

    def play(self, file_path: Optional[Path] = None, start_seconds: float = 0.0) -> bool:
        with self._lock:
            if file_path:
                self._current_file = file_path
            elif self._state == PlaybackState.PAUSED:
                pass
            elif not self._current_file:
                return False

            target_position = start_seconds
            target_file = self._current_file

        self.stop(clear_source=False)

        with self._lock:
            self._current_file = target_file

        if file_path or not self._source:
            if not self.load(self._current_file):
                return False

        with self._lock:
            self._position_seconds = target_position

        try:
            sample_rate = self._source.sample_rate
            nchannels = self._source.nchannels
            start_sample_index = int(self._position_seconds * sample_rate * nchannels)

            if start_sample_index >= len(self._source.samples):
                return False

            self._generator = self._stream_generator(self._source.samples, start_sample_index)
            next(self._generator)

            self._device = miniaudio.PlaybackDevice(
                output_format=miniaudio.SampleFormat.SIGNED16,
                nchannels=nchannels,
                sample_rate=sample_rate,
            )

            self._stop_event.clear()
            self._position_thread = threading.Thread(target=self._position_tracker, daemon=True)
            self._position_thread.start()

            with self._lock:
                self._start_time = time.time() - target_position
                self._position_seconds = 0.0

            self._set_state(PlaybackState.PLAYING)

            self._device.start(self._generator)
            return True

        except Exception as e:
            logger.error(f"Playback error: {e}", exc_info=True)
            self.stop()
            return False

    def pause(self) -> bool:
        with self._lock:
            if self._state != PlaybackState.PLAYING:
                return False

            if self._start_time:
                elapsed = time.time() - self._start_time
                current = min(self._position_seconds + elapsed, self._duration_seconds)
            else:
                current = self._position_seconds

            self._position_seconds = current

        self._stop_event.set()

        if self._generator:
            try:
                self._generator.close()
            except Exception:
                pass
            self._generator = None

        if self._device:
            try:
                self._device.stop()
                self._device.close()
            except Exception:
                pass
            self._device = None

        self._set_state(PlaybackState.PAUSED)
        return True

    def resume(self) -> bool:
        with self._lock:
            if self._state != PlaybackState.PAUSED:
                return False
            saved_position = self._position_seconds
            current_file = self._current_file

        if not current_file:
            return False

        return self.play(start_seconds=saved_position)

    def stop(self, clear_source: bool = True) -> None:
        self._stop_event.set()

        if self._generator:
            try:
                self._generator.close()
            except Exception:
                pass
            self._generator = None

        if self._device:
            try:
                self._device.stop()
                self._device.close()
            except Exception:
                pass
            self._device = None

        if self._position_thread and self._position_thread.is_alive():
            self._position_thread.join(timeout=0.1)

        with self._lock:
            self._position_seconds = 0.0
            self._start_time = None
            if clear_source:
                self._source = None

        self._set_state(PlaybackState.STOPPED)

    def seek(self, position_seconds: float) -> bool:
        with self._lock:
            if not self._current_file or not self._source:
                return False
            position_seconds = max(0.0, min(position_seconds, self._duration_seconds))
            was_playing = self._state == PlaybackState.PLAYING

        if was_playing:
            return self.play(start_seconds=position_seconds)
        else:
            with self._lock:
                self._position_seconds = position_seconds

            if self._on_position_changed:
                self._on_position_changed(self.get_position())

            return True

    def skip_forward(self, seconds: float = 5.0) -> bool:
        current = self.position_seconds
        duration = self.duration_seconds

        if current + seconds >= duration - 1.0:
            return False

        new_position = min(current + seconds, duration)
        return self.seek(new_position)

    def skip_backward(self, seconds: float = 5.0) -> bool:
        current = self.position_seconds

        if current <= seconds:
            return self.seek(0.0)

        new_position = max(current - seconds, 0.0)
        return self.seek(new_position)

    def set_volume(self, volume: float) -> None:
        self.volume = max(0.0, min(1.0, volume))

    def toggle_play_pause(self) -> bool:
        if self._state == PlaybackState.PLAYING:
            return self.pause()
        elif self._state == PlaybackState.PAUSED:
            return self.resume()
        else:
            return self.play()
