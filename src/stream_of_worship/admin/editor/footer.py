"""Grouped footer widget for the LRC editor.

Displays key bindings organized into labeled clusters
(Playback, Lyrics Edit, Timecode, General) instead of a flat list.
"""

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Footer, Static


class _BindingGroup(Static):
    def __init__(self, label: str, bindings: list[Binding]) -> None:
        self._group_label = label
        self._bindings = bindings
        super().__init__(self._format_content())

    def _format_content(self) -> str:
        parts: list[str] = [f"[bold]{self._group_label}[/bold] "]
        for i, b in enumerate(self._bindings):
            key_display = b.key
            if key_display.startswith("ctrl+"):
                key_display = "^" + key_display[5:].upper()
            elif key_display == "shift+left":
                key_display = "⇧←"
            elif key_display == "shift+right":
                key_display = "⇧→"
            elif key_display == "left":
                key_display = "←"
            elif key_display == "right":
                key_display = "→"
            elif key_display == "up":
                key_display = "↑"
            elif key_display == "down":
                key_display = "↓"
            elif key_display == "space":
                key_display = "⎵"
            elif key_display == "escape":
                key_display = "Esc"
            parts.append(f"[dim]{key_display}[/dim]={b.description}")
            if i < len(self._bindings) - 1:
                parts.append("[dim] │ [/dim]")
        return "".join(parts)


class GroupedFooter(Horizontal):
    DEFAULT_CSS = """
    GroupedFooter {
        height: 3;
        dock: bottom;
        background: $surface;
        color: $text;
        padding: 0 1;
        border-top: solid $primary;
    }
    GroupedFooter > _BindingGroup {
        width: 1fr;
        height: auto;
        padding: 0 1;
    }
    """

    def compose(self) -> ComposeResult:
        screen = self.screen
        groups = getattr(screen, "BINDING_GROUPS", None)
        if not groups:
            yield Footer()
            return

        binding_map: dict[str, Binding] = {}
        for b in screen.BINDINGS:
            binding_map[b.action] = b

        for group_label, action_names in groups.items():
            group_bindings = [binding_map[a] for a in action_names if a in binding_map]
            yield _BindingGroup(group_label, group_bindings)
