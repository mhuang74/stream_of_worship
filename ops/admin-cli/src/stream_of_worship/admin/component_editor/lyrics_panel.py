"""Lyrics Panel widget for the Component Metadata editor (v6).

Right panel (lyrics mode) showing timestamped LRC lyrics for the current song,
with playback-synced current-line highlight.
"""

from rich.text import Text
from textual.widgets import Static

from stream_of_worship.admin.services.lrc_parser import (
    LRCParsedContent,
    format_centiseconds,
)


class LyricsPanel(Static):
    """Right panel (lyrics mode) showing timestamped LRC lyrics for the current song.

    Features:
    - Renders LRC metadata header (ti, ar, al, etc.) in dim italic
    - Renders each timed line with a cyan timestamp column
    - Highlights the "current line" based on playback position (visual only,
      no auto-scroll-to-center)
    - Shows placeholder messages for loading / no-LRC / error states
    """

    DEFAULT_CSS = """
    LyricsPanel {
        height: 1fr;
        padding: 0 1;
        overflow-y: auto;
        background: $surface;
    }
    LyricsPanel:focus {
        border-left: double $accent;
    }
    LyricsPanel.empty {
        color: $text-muted;
        text-align: center;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._song_title: str = ""
        self._highlighted_index: int = -1
        self._parsed: LRCParsedContent | None = None

    def update_lrc(
        self,
        parsed: LRCParsedContent | None,
        song_title: str,
        highlighted_index: int = -1,
    ) -> None:
        self._song_title = song_title
        self._parsed = parsed
        self._highlighted_index = highlighted_index

        if parsed is None:
            self.add_class("empty")
            self.update(f'No LRC file found for "{song_title}"')
            return

        self.remove_class("empty")
        text = Text()

        # LRC metadata header
        if parsed.preserved_lines:
            for p in parsed.preserved_lines:
                if p.tag is not None:
                    text.append(f"[{p.tag}: {p.value}]\n", style="dim italic")
                elif p.raw.strip():
                    text.append(f"{p.raw}\n", style="dim italic")
            text.append("\n")

        # Timed lines with current-line highlight
        for i, line in enumerate(parsed.timed_lines):
            timestamp = (
                format_centiseconds(line.time_seconds)
                if line.time_seconds is not None
                else "--:--.--"
            )
            if i == highlighted_index:
                text.append(f"[{timestamp}]  ", style="bold cyan reverse")
                text.append(line.text + "\n", style="bold reverse")
            else:
                text.append(f"[{timestamp}]  ", style="cyan")
                text.append(line.text + "\n")

        self.update(text)

    def set_highlighted_index(self, index: int) -> None:
        """Update the highlighted line index and re-render.

        Called by the screen's playback position callback. If the index
        hasn't changed, this is a no-op (avoids unnecessary re-renders
        at 5Hz playback update frequency).
        """
        if index == self._highlighted_index:
            return
        if self._parsed is None:
            return
        self.update_lrc(self._parsed, self._song_title, highlighted_index=index)

    def update_fetching(self, song_title: str) -> None:
        self._parsed = None
        self._highlighted_index = -1
        self._song_title = song_title
        self.add_class("empty")
        self.update(f'Loading lyrics for "{song_title}"...')

    def update_error(self, msg: str, song_title: str) -> None:
        self._parsed = None
        self._highlighted_index = -1
        self._song_title = song_title
        self.add_class("empty")
        self.update(f'Error loading lyrics for "{song_title}": {msg}')

    @staticmethod
    def compute_highlighted_index(
        parsed: LRCParsedContent | None,
        position_seconds: float,
    ) -> int:
        """Compute the index of the LRC line that corresponds to the given
        playback position.

        Returns the index of the last timed line whose time_seconds <= position.
        Returns -1 if no line matches (position before first line) or if
        parsed is None / empty.
        """
        if parsed is None or not parsed.timed_lines:
            return -1
        result = -1
        for i, line in enumerate(parsed.timed_lines):
            if line.time_seconds is None:
                continue
            if line.time_seconds <= position_seconds:
                result = i
            else:
                break
        return result
