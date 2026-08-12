"""Lyrics panel widget for the Component Metadata editor (v2).

Bottom-left panel of the T-shaped 3-panel layout. Displays timestamped
LRC (synchronized lyrics) for the current song in a read-only scrollable
``Static`` widget.

Rendering details:
- LRC metadata header (``[ti:]``, ``[ar:]``, ``[al:]``, etc.) rendered in
  ``dim italic``.
- Timestamp column rendered in ``cyan`` for visual separation.
- Empty lyric lines preserved (rendered as just the timestamp).
- Auto-scrolls to top on each update.
- ``empty`` CSS class applies muted color + centered text for placeholders.
- ``:focus`` CSS adds a right border highlight when the lyrics panel is active.
"""


from rich.text import Text
from textual.widgets import Static

from stream_of_worship.admin.services.lrc_parser import (
    LRCParsedContent,
    format_centiseconds,
)


class LyricsPanel(Static):
    """Bottom-left panel showing timestamped LRC lyrics for the current song."""

    DEFAULT_CSS = """
    LyricsPanel {
        height: 1fr;
        padding: 0 1;
        overflow-y: auto;
        background: $surface;
    }
    LyricsPanel:focus {
        border-right: double $accent;
    }
    LyricsPanel.empty {
        color: $text-muted;
        text-align: center;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._song_title: str = ""

    def update_lrc(
        self,
        parsed: LRCParsedContent | None,
        song_title: str,
    ) -> None:
        """Render parsed LRC content (or a placeholder if ``parsed`` is None)."""
        self._song_title = song_title
        if parsed is None:
            self.add_class("empty")
            self.update(f'No LRC file found for "{song_title}"')
            return
        self.remove_class("empty")
        text = Text()

        # Metadata header: recognized metadata tags preserved at the top.
        metadata = [p for p in parsed.preserved_lines if p.tag is not None]
        if metadata:
            for line in metadata:
                text.append(f"[{line.tag}: {line.value}]\n", style="dim italic")
            text.append("\n")

        for line in parsed.timed_lines:
            timestamp = (
                format_centiseconds(line.time_seconds)
                if line.time_seconds is not None
                else "--:--.--"
            )
            text.append(f"[{timestamp}]  ", style="cyan")
            text.append(line.text + "\n")

        self.update(text)
        self.scroll_home(animate=False)

    def update_fetching(self, song_title: str) -> None:
        """Show a 'loading' placeholder while LRC is being fetched."""
        self.add_class("empty")
        self.update(f'Loading lyrics for "{song_title}"...')

    def update_error(self, msg: str, song_title: str) -> None:
        """Show an error placeholder when the LRC fetch failed."""
        self.add_class("empty")
        self.update(f'Error loading lyrics for "{song_title}": {msg}')
