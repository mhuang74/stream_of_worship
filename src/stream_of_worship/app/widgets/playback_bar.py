"""Reusable playback progress bar widget."""

from textual.widgets import Static

from stream_of_worship.app.services.playback import PlaybackPosition, PlaybackService


class PlaybackBar(Static):
    """A progress bar widget that displays playback position and duration.

    Does NOT register callbacks with PlaybackService. The parent screen is
    responsible for calling update_display() and update_visibility() from
    its own callbacks.

    Attributes:
        playback: The PlaybackService instance to monitor
        bar_width: Width of the visual progress bar in characters
    """

    DEFAULT_CSS = """
    PlaybackBar {
        text-align: center;
        content-align: center middle;
        height: 1;
        margin: 1 2;
    }
    """

    def __init__(
        self,
        playback: PlaybackService,
        bar_width: int = 30,
        id: str | None = None,
        classes: str | None = None,
    ):
        super().__init__("", id=id, classes=classes or "hidden")
        self.playback = playback
        self.bar_width = bar_width

    def update_display(self, position: PlaybackPosition) -> None:
        """Update the progress bar display.

        Called by the parent screen from its _on_position_changed callback.
        """
        current_str = self._format_time(position.current_seconds)
        total_str = self._format_time(position.total_seconds)

        filled = int((position.progress_percent / 100) * self.bar_width)
        empty = self.bar_width - filled
        bar = "█" * filled + "░" * empty

        icon = "⏸" if self.playback.is_paused else "▶"

        self.update(f"{icon} {current_str} / {total_str}  [{bar}]")

    def update_visibility(self) -> None:
        """Show/hide based on playback state.

        Called by the parent screen from its _on_state_changed and
        _on_finished callbacks.
        """
        if self.playback.is_stopped:
            self.add_class("hidden")
        else:
            self.remove_class("hidden")

    @staticmethod
    def _format_time(seconds: float) -> str:
        """Format seconds as M:SS."""
        minutes = int(seconds // 60)
        secs = int(seconds % 60)
        return f"{minutes}:{secs:02d}"
