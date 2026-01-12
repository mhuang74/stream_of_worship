"""
Audio playback with seek controls.

Adapted from review_transitions.py with sounddevice integration.
"""

import sounddevice as sd
import numpy as np
import threading
import time
import sys
import select
import termios
import tty
from typing import Optional


class AudioPlayer:
    """
    Audio player with seek controls (arrow keys for ±5s).

    Provides non-blocking playback with keyboard controls for seeking.
    """

    def __init__(self, sample_rate: int = 44100):
        """
        Initialize audio player.

        Args:
            sample_rate: Audio sample rate
        """
        self.sample_rate = sample_rate

        # Playback state
        self.playing = False
        self.stop_requested = False
        self.audio_data = None
        self.current_position = 0
        self.seek_requested = False
        self.seek_offset = 0
        self.keyboard_thread = None

    def play(self, audio_data: np.ndarray, blocking: bool = True) -> bool:
        """
        Play audio with seek controls.

        Args:
            audio_data: Stereo audio array (2, num_samples) or (num_samples,)
            blocking: If True, block until playback finishes

        Returns:
            True if playback started successfully
        """
        # Stop any current playback
        self.stop()

        try:
            # Ensure audio is in correct format
            if audio_data.ndim == 1:
                # Mono to stereo
                audio_data = np.stack([audio_data, audio_data])
            elif audio_data.shape[0] == 2:
                # Already stereo (2, num_samples)
                pass
            else:
                # Transpose if needed (num_samples, 2) -> (2, num_samples)
                if audio_data.shape[1] == 2:
                    audio_data = audio_data.T

            total_duration = audio_data.shape[1] / self.sample_rate

            print(f"\n▶ Playing audio")
            print(f"  Duration: {total_duration:.1f}s | ← → to seek ±5s | Ctrl+C to stop")

            self.playing = True
            self.stop_requested = False
            self.audio_data = audio_data
            self.current_position = 0
            self.seek_requested = False
            self.seek_offset = 0

            if blocking:
                # Start keyboard listener thread
                self.keyboard_thread = threading.Thread(
                    target=self._keyboard_listener_thread,
                    daemon=True
                )
                self.keyboard_thread.start()

                # Playback loop with seeking
                while self.playing and not self.stop_requested:
                    # Handle seek requests
                    if self.seek_requested:
                        sd.stop()
                        self.current_position += int(self.seek_offset * self.sample_rate)

                        # Clamp position
                        self.current_position = max(0, min(
                            self.current_position,
                            audio_data.shape[1] - 1
                        ))

                        self.seek_requested = False
                        current_time = self.current_position / self.sample_rate
                        print(f" → {current_time:.1f}s / {total_duration:.1f}s", flush=True)

                    # Get remaining audio
                    remaining_audio = audio_data[:, self.current_position:]

                    if remaining_audio.shape[1] == 0:
                        # Reached the end
                        break

                    # Play from current position (transpose for sounddevice)
                    sd.play(remaining_audio.T, self.sample_rate)
                    playback_start_time = time.time()
                    start_pos = self.current_position

                    # Wait for playback or seek/stop
                    while not self.seek_requested and not self.stop_requested:
                        time.sleep(0.05)

                        # Update position
                        elapsed_time = time.time() - playback_start_time
                        self.current_position = start_pos + int(elapsed_time * self.sample_rate)

                        # Check if finished
                        if self.current_position >= audio_data.shape[1]:
                            self.playing = False
                            break

                    # Stop current playback
                    sd.stop()

                self.playing = False
                print()  # New line after playback

            else:
                # Non-blocking playback
                sd.play(audio_data.T, self.sample_rate)

            return True

        except Exception as e:
            print(f"❌ ERROR: Failed to play audio: {e}")
            self.playing = False
            return False

    def stop(self):
        """Stop any currently playing audio."""
        if self.playing:
            sd.stop()
            self.playing = False
            self.stop_requested = True
            print(f"\n⏹ Stopped playback")

    def _keyboard_listener_thread(self):
        """
        Background thread to listen for arrow key presses during playback.
        Updates seek_offset when arrow keys are detected.
        """
        # Save terminal settings
        old_settings = termios.tcgetattr(sys.stdin)

        try:
            # Set terminal to raw mode
            tty.setcbreak(sys.stdin.fileno())

            while self.playing and not self.stop_requested:
                key = self._get_arrow_key()

                if key == 'right':
                    self.seek_offset = 5  # Skip forward 5s
                    self.seek_requested = True
                    print(f"\r⏩ +5s", end='', flush=True)
                elif key == 'left':
                    self.seek_offset = -5  # Skip backward 5s
                    self.seek_requested = True
                    print(f"\r⏪ -5s", end='', flush=True)

                time.sleep(0.05)  # Small delay to avoid busy-waiting

        finally:
            # Restore terminal settings
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)

    def _get_arrow_key(self) -> Optional[str]:
        """
        Non-blocking arrow key detection for Unix-like systems.

        Returns:
            'left', 'right', or None
        """
        # Check if input is available (non-blocking)
        if select.select([sys.stdin], [], [], 0)[0]:
            ch = sys.stdin.read(1)

            # Arrow keys send escape sequences: \x1b[A (up), \x1b[B (down), \x1b[C (right), \x1b[D (left)
            if ch == '\x1b':
                ch2 = sys.stdin.read(1)
                if ch2 == '[':
                    ch3 = sys.stdin.read(1)
                    if ch3 == 'C':  # Right arrow
                        return 'right'
                    elif ch3 == 'D':  # Left arrow
                        return 'left'

        return None
