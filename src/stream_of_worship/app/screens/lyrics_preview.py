"""Lyrics preview screen.

Screen for previewing lyrics synchronized with audio playback.
Accessible via Shift+P from SongsetEditorScreen when a song is selected.
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Static

from stream_of_worship.app.db.models import SongsetItem
from stream_of_worship.app.logging_config import get_logger
from stream_of_worship.app.services.asset_cache import AssetCache
from stream_of_worship.app.services.playback import PlaybackPosition, PlaybackService, PlaybackState

logger = get_logger(__name__)


@dataclass
class LRCLine:
    """A single line from an LRC file.

    Attributes:
        time_seconds: Timestamp in seconds
        text: Lyric text
    """

    time_seconds: float
    text: str


class LyricsPreviewScreen(Screen):
    """Screen for previewing lyrics synchronized with audio playback."""

    BINDINGS = [
        ("space", "toggle_playback", "Play/Pause"),
        ("left", "skip_backward", "Skip -10s"),
        ("right", "skip_forward", "Skip +10s"),
        ("escape", "back", "Back"),
    ]

    def __init__(
        self,
        item: SongsetItem,
        playback: PlaybackService,
        asset_cache: AssetCache,
    ):
        """Initialize the lyrics preview screen.

        Args:
            item: The songset item being previewed
            playback: Playback service for audio control
            asset_cache: Asset cache for downloading files
        """
        super().__init__()
        self.item = item
        self.playback = playback
        self.asset_cache = asset_cache
        self.lrc_lines: list[LRCLine] = []
        self.current_line_index: int = -1
        self._audio_path: Optional[Path] = None
        self._lrc_path: Optional[Path] = None

    def compose(self) -> ComposeResult:
        """Compose the screen layout."""
        yield Header()

        with Horizontal():
            # Left panel (wider) - Lyrics display
            with Vertical(id="lyrics_panel"):
                yield Static("", id="current_lyric", classes="lyric-current")
                yield Static("", id="next_lyric", classes="lyric-next")
                yield Static("", id="progress_bar")

            # Right panel (narrower) - Debug info
            with Vertical(id="debug_panel"):
                # Song metadata section
                with Vertical(id="metadata_section"):
                    yield Static("", id="song_title")
                    yield Static("", id="song_details")
                    yield Static("", id="song_album")

                # LRC debug table
                table = DataTable(id="lrc_table")
                table.add_columns("Time", "Lyrics")
                table.cursor_type = "row"
                table.zebra_stripes = True
                yield table

        yield Footer()

    def on_mount(self) -> None:
        """Handle mount event - initialize data and setup callbacks."""
        logger.info(f"LyricsPreviewScreen mounted for song: {self.item.song_title}")

        # Download and parse LRC file
        self._load_lrc()

        # Download audio file
        self._load_audio()

        # Populate metadata
        self._populate_metadata()

        # Populate LRC table
        self._populate_lrc_table()

        # Register playback callbacks
        self.playback.set_callbacks(
            on_position_changed=self._on_position_changed,
            on_state_changed=self._on_state_changed,
            on_finished=self._on_finished,
        )

        # Start playback if audio is available (deferred to ensure screen is ready)
        if self._audio_path:
            self.call_after_refresh(self._start_playback)

    def _start_playback(self) -> None:
        """Start audio playback."""
        if self._audio_path:
            self.playback.play(self._audio_path)

    def _load_lrc(self) -> None:
        """Download and parse the LRC file."""
        if not self.item.recording_hash_prefix:
            logger.warning("No recording hash prefix for LRC loading")
            return

        try:
            self._lrc_path = self.asset_cache.download_lrc(self.item.recording_hash_prefix)
            if self._lrc_path:
                content = self._lrc_path.read_text(encoding="utf-8")
                self.lrc_lines = self._parse_lrc(content)
                logger.info(f"Loaded {len(self.lrc_lines)} LRC lines")
            else:
                logger.warning("LRC file not found in cache")
        except Exception as e:
            logger.error(f"Failed to load LRC: {e}")
            self.lrc_lines = []

    def _load_audio(self) -> None:
        """Download the audio file."""
        if not self.item.recording_hash_prefix:
            logger.warning("No recording hash prefix for audio loading")
            return

        try:
            self._audio_path = self.asset_cache.download_audio(self.item.recording_hash_prefix)
            if self._audio_path:
                logger.info(f"Audio loaded: {self._audio_path}")
            else:
                logger.warning("Audio file not found")
        except Exception as e:
            logger.error(f"Failed to load audio: {e}")
            self._audio_path = None

    def _parse_lrc(self, lrc_content: str) -> list[LRCLine]:
        """Parse LRC file content.

        Args:
            lrc_content: Raw LRC file content

        Returns:
            List of LRC lines with timestamps
        """
        lines = []
        # Match [mm:ss.xx] or [mm:ss.xxx] format
        pattern = r"\[(\d{2}):(\d{2})\.(\d{2,3})\](.*)"

        for line in lrc_content.split("\n"):
            match = re.match(pattern, line.strip())
            if match:
                minutes = int(match.group(1))
                seconds = int(match.group(2))
                milliseconds = int(match.group(3).ljust(3, "0")[:3])
                text = match.group(4).strip()

                time_seconds = minutes * 60 + seconds + milliseconds / 1000.0
                if text:  # Only add lines with text
                    lines.append(LRCLine(time_seconds=time_seconds, text=text))

        return lines

    def _populate_metadata(self) -> None:
        """Populate the metadata section with song info."""
        title_widget = self.query_one("#song_title", Static)
        details_widget = self.query_one("#song_details", Static)
        album_widget = self.query_one("#song_album", Static)

        # Title (large, bold)
        title = self.item.song_title or "Unknown Song"
        title_widget.update(f"[bold]{title}[/bold]")

        # Key and Tempo
        key = self.item.display_key or "?"
        tempo = f"{int(self.item.tempo_bpm)} BPM" if self.item.tempo_bpm else "? BPM"
        details_widget.update(f"Key: {key}  |  Tempo: {tempo}")

        # Album
        album = self.item.song_album_name or ""
        if album:
            album_widget.update(f"Album: {album}")
        else:
            album_widget.update("")

    def _populate_lrc_table(self) -> None:
        """Populate the LRC debug table with timestamps and lyrics."""
        table = self.query_one("#lrc_table", DataTable)
        table.clear()

        if not self.lrc_lines:
            table.add_row("--", "No lyrics available")
            return

        for line in self.lrc_lines:
            time_str = self._format_timestamp(line.time_seconds)
            # Truncate long lyrics for table display
            text = line.text[:40] + "..." if len(line.text) > 40 else line.text
            table.add_row(time_str, text)

    def _format_timestamp(self, seconds: float) -> str:
        """Format seconds as mm:ss.xx timestamp.

        Args:
            seconds: Time in seconds

        Returns:
            Formatted timestamp string
        """
        minutes = int(seconds // 60)
        secs = int(seconds % 60)
        millis = int((seconds % 1) * 100)
        return f"{minutes:02d}:{secs:02d}.{millis:02d}"

    def _find_current_line(self, position_seconds: float) -> int:
        """Find the index of the lyric line for current position.

        Args:
            position_seconds: Current playback position in seconds

        Returns:
            Index of current line, or -1 if before first line
        """
        if not self.lrc_lines:
            return -1

        # Find last line where timestamp <= position
        for i in range(len(self.lrc_lines) - 1, -1, -1):
            if self.lrc_lines[i].time_seconds <= position_seconds:
                return i
        return -1

    def _on_position_changed(self, position: PlaybackPosition) -> None:
        """Handle playback position updates.

        Args:
            position: Current playback position information
        """
        # Schedule UI update on main thread (callbacks run in background thread)
        def _update():
            # Update current line index
            new_index = self._find_current_line(position.current_seconds)

            if new_index != self.current_line_index:
                self.current_line_index = new_index
                self._update_lyrics_display()
                self._highlight_lrc_row()

            # Update progress bar
            self._update_progress_bar(position)

        self.call_after_refresh(_update)

    def _update_lyrics_display(self) -> None:
        """Update the current and next lyric display."""
        current_widget = self.query_one("#current_lyric", Static)
        next_widget = self.query_one("#next_lyric", Static)

        # Current line
        if 0 <= self.current_line_index < len(self.lrc_lines):
            current_text = self.lrc_lines[self.current_line_index].text
            current_widget.update(current_text)
        else:
            current_widget.update("[dim]♪[/dim]")

        # Next line
        next_index = self.current_line_index + 1
        if 0 <= next_index < len(self.lrc_lines):
            next_text = self.lrc_lines[next_index].text
            next_widget.update(f"[dim]{next_text}[/dim]")
        else:
            next_widget.update("")

    def _update_progress_bar(self, position: PlaybackPosition) -> None:
        """Update the progress bar display.

        Args:
            position: Current playback position
        """
        progress_widget = self.query_one("#progress_bar", Static)

        current_str = self._format_timestamp(position.current_seconds)
        total_str = self._format_timestamp(position.total_seconds)

        # Build visual progress bar
        bar_width = 30
        filled = int((position.progress_percent / 100) * bar_width)
        empty = bar_width - filled

        bar = "█" * filled + "░" * empty
        icon = "⏸" if self.playback.is_paused else "▶"

        progress_widget.update(f"{icon} {current_str} / {total_str}  [{bar}]")

    def _highlight_lrc_row(self) -> None:
        """Highlight the current row in the LRC table and auto-scroll to it."""
        table = self.query_one("#lrc_table", DataTable)

        if self.current_line_index < 0 or self.current_line_index >= len(self.lrc_lines):
            return

        # Move cursor to current row (this also scrolls to it)
        table.move_cursor(row=self.current_line_index)

    def _on_state_changed(self, state: PlaybackState) -> None:
        """Handle playback state changes.

        Args:
            state: New playback state
        """
        # Schedule UI update on main thread (callbacks run in background thread)
        def _update():
            # Update progress bar to reflect new state icon
            position = self.playback.get_position()
            self._update_progress_bar(position)

        self.call_after_refresh(_update)

    def _on_finished(self) -> None:
        """Handle playback finished."""
        logger.info("Playback finished")

        # Schedule UI update on main thread (callbacks run in background thread)
        def _update():
            self.current_line_index = -1
            self._update_lyrics_display()

        self.call_after_refresh(_update)

    def action_toggle_playback(self) -> None:
        """Toggle playback with spacebar."""
        if self.playback.is_playing:
            self.playback.pause()
            self.notify("Paused")
        elif self.playback.is_paused:
            self.playback.resume()
            self.notify("Resumed")
        elif self._audio_path:
            self.playback.play(self._audio_path)
            self.notify(f"Playing: {self.item.song_title}")
        else:
            self.notify("No audio available", severity="error")

    def action_skip_forward(self) -> None:
        """Skip forward 10 seconds."""
        if not self.playback.is_playing and not self.playback.is_paused:
            self.notify("No audio playing", severity="warning")
            return

        if self.playback.skip_forward(10.0):
            position = self.playback.get_position()
            self.notify(f"⏩ {self._format_timestamp(position.current_seconds)}")

    def action_skip_backward(self) -> None:
        """Skip backward 10 seconds."""
        if not self.playback.is_playing and not self.playback.is_paused:
            self.notify("No audio playing", severity="warning")
            return

        if self.playback.skip_backward(10.0):
            position = self.playback.get_position()
            self.notify(f"⏪ {self._format_timestamp(position.current_seconds)}")

    def action_back(self) -> None:
        """Go back to songset editor."""
        logger.info("Action: back from lyrics preview")
        self.playback.stop()
        self.app.pop_screen()
