"""Lyrics Panel widget for the Component Metadata editor (v6).

Right panel (lyrics mode) showing timestamped LRC lyrics for the current song,
with playback-synced current-line highlight.
"""

from typing import ClassVar

from rich.segment import Segment
from rich.text import Text
from textual import events
from textual.binding import Binding
from textual.geometry import Size
from textual.scroll_view import ScrollView
from textual.strip import Strip

from stream_of_worship.admin.services.lrc_parser import (
    LRCParsedContent,
    format_centiseconds,
)


class LyricsPanel(ScrollView, can_focus=True):
    """Right panel (lyrics mode) showing timestamped LRC lyrics for the current song.

    Features:
    - Renders LRC metadata header (ti, ar, al, etc.) in dim italic
    - Renders each timed line with a cyan timestamp column
    - Highlights the "current line" based on playback position and
      auto-scrolls to keep it centered in the viewport
    - Supports manual line-by-line scrolling via Up/Down arrows
    - Shows placeholder messages for loading / no-LRC / error states

    Implemented as a proper ``ScrollView`` subclass using the Line-API
    pattern (same as ``RichLog``/``Log``/``Tree``): content is rendered
    into ``Strip`` objects once on every ``update_lrc``, then served
    line-by-line from ``render_line`` with ``scroll_offset.y`` applied.
    """

    DEFAULT_CSS = """
    LyricsPanel {
        height: 1fr;
        padding: 0 1;
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

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("home", "scroll_home", show=False),
        Binding("end", "scroll_end", show=False),
        Binding("pageup", "page_up", show=False),
        Binding("pagedown", "page_down", show=False),
    ]

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._song_title: str = ""
        self._highlighted_index: int = -1
        self._parsed: LRCParsedContent | None = None
        self._content_strips: list[Strip] = []
        self._render_width: int = 0

    # ------------------------------------------------------------------
    # Line-API rendering (the fix)
    # ------------------------------------------------------------------

    def render_line(self, y: int) -> Strip:
        """Render a single visible line, applying scroll offset.

        This is the canonical Line-API pattern (same as RichLog/Log/Tree/DataTable):
        translate the viewport y coordinate to the content y coordinate by adding
        scroll_offset.y, then return the matching content strip (or blank).
        """
        width = self.scrollable_content_region.width or self.size.width
        scroll_x, scroll_y = self.scroll_offset
        line_index = scroll_y + y

        if line_index < 0 or line_index >= len(self._content_strips):
            return Strip.blank(width, self.rich_style)

        strip = self._content_strips[line_index]
        return strip.crop_extend(scroll_x, scroll_x + width, self.rich_style)

    def on_resize(self, event: events.Resize) -> None:
        new_width = self.scrollable_content_region.width or self.size.width
        if new_width != self._render_width and self._parsed is not None:
            text = self._build_lyrics_text(self._parsed, self._highlighted_index)
            self._rebuild_strips(text)
            self.refresh()

    # ------------------------------------------------------------------
    # Content building
    # ------------------------------------------------------------------

    def _build_lyrics_text(self, parsed: LRCParsedContent, highlighted_index: int) -> Text:
        """Build the Rich Text representation of the lyrics content."""
        text = Text()

        if parsed.preserved_lines:
            for p in parsed.preserved_lines:
                if p.tag is not None:
                    text.append(f"[{p.tag}: {p.value}]\n", style="dim italic")
                elif p.raw.strip():
                    text.append(f"{p.raw}\n", style="dim italic")
            text.append("\n")

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

        return text

    def _rebuild_strips(self, text: Text) -> None:
        """Render Rich Text into Strip objects at the current content width."""
        width = self.scrollable_content_region.width or self.size.width or 1
        if width != self._render_width:
            self._render_width = width
        segments = self.app.console.render(text, self.app.console.options.update_width(width))
        lines = list(Segment.split_lines(segments))
        self._content_strips = [Strip(line).adjust_cell_length(width) for line in lines]
        self.virtual_size = Size(width, max(1, len(self._content_strips)))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

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
            text = Text(f'No LRC file found for "{song_title}"')
        else:
            self.remove_class("empty")
            text = self._build_lyrics_text(parsed, highlighted_index)

        self._rebuild_strips(text)

        if highlighted_index >= 0:
            self._scroll_to_highlight(highlighted_index)
        else:
            self.scroll_to(y=0, animate=False, immediate=True, force=True)

        self.refresh()

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
        self._content_strips = []
        width = self.scrollable_content_region.width or self.size.width or 1
        self.virtual_size = Size(width, 1)
        self.refresh()

    def update_error(self, msg: str, song_title: str) -> None:
        self._parsed = None
        self._highlighted_index = -1
        self._song_title = song_title
        self.add_class("empty")
        self._content_strips = []
        width = self.scrollable_content_region.width or self.size.width or 1
        self.virtual_size = Size(width, 1)
        self.refresh()

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
