"""Lyrics Panel widget for the Component Metadata editor (v6).

Right panel (lyrics mode) showing timestamped LRC lyrics for the current song,
with playback-synced current-line highlight.
"""

from rich.text import Text
from textual.geometry import Size
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
    - Highlights the "current line" based on playback position and
      auto-scrolls to keep it centered in the viewport
    - Supports manual line-by-line scrolling via Up/Down arrows
    - Shows placeholder messages for loading / no-LRC / error states
    """

    can_focus = True

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

    @property
    def is_scrollable(self) -> bool:
        return True

    @property
    def is_container(self) -> bool:
        return False

    def get_content_height(self, container: Size, viewport: Size, width: int) -> int:
        return self.virtual_size.height

    def _size_updated(
        self, size: Size, virtual_size: Size, container_size: Size, layout: bool = True
    ) -> bool:
        size_changed = self._size != size
        if size_changed:
            self._set_dirty()
        if (
            size_changed
            or virtual_size != self.virtual_size
            or container_size != self.container_size
        ):
            self._scrollbar_changes.clear()
            self._size = size
            virtual_size = self.virtual_size
            self._container_size = size - self.styles.gutter.totals
            self._scroll_update(virtual_size)
        return size_changed or self._container_size != container_size

    def _compute_content_height(self) -> int:
        """Compute the total number of rendered lines from parsed content."""
        if self._parsed is None:
            return 1
        h = 0
        if self._parsed.preserved_lines:
            for p in self._parsed.preserved_lines:
                if p.tag is not None or p.raw.strip():
                    h += 1
            h += 1  # blank separator line
        h += len(self._parsed.timed_lines)
        return max(h, 1)

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
            self.virtual_size = Size(self.size.width, 1)
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
        content_h = self._compute_content_height()
        self.virtual_size = Size(self.size.width, content_h)
        if highlighted_index >= 0:
            self._scroll_to_highlight(highlighted_index)
        else:
            self.scroll_to(y=0, animate=False, immediate=True, force=True)

    def _compute_highlighted_line_y(self, highlighted_index: int) -> int:
        """Return the 0-based Y line coordinate of the highlighted timed line
        within the rendered Text content.

        Accounts for the metadata header (variable line count) + blank separator.
        Returns 0 if no parsed content.
        """
        if self._parsed is None:
            return 0
        y = 0
        if self._parsed.preserved_lines:
            for p in self._parsed.preserved_lines:
                if p.tag is not None or p.raw.strip():
                    y += 1
            y += 1  # blank separator line
        y += highlighted_index
        return y

    def _scroll_to_highlight(self, highlighted_index: int) -> None:
        """Scroll so the highlighted line is roughly centered in the viewport."""
        target_y = self._compute_highlighted_line_y(highlighted_index)
        viewport_h = self.size.height
        if viewport_h <= 0:
            return
        center_y = max(0, target_y - viewport_h // 2)
        self.scroll_to(y=center_y, animate=False, immediate=True, force=True)

    def scroll_line_up(self) -> None:
        """Scroll up by one line."""
        self.scroll_up(animate=False, immediate=True, force=True)

    def scroll_line_down(self) -> None:
        """Scroll down by one line."""
        self.scroll_down(animate=False, immediate=True, force=True)

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
        self.virtual_size = Size(self.size.width, 1)

    def update_error(self, msg: str, song_title: str) -> None:
        self._parsed = None
        self._highlighted_index = -1
        self._song_title = song_title
        self.add_class("empty")
        self.update(f'Error loading lyrics for "{song_title}": {msg}')
        self.virtual_size = Size(self.size.width, 1)

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
