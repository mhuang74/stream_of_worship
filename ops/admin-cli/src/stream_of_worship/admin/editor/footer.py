"""Grouped footer widget for the LRC editor.

Displays key bindings organized into labeled clusters
(Playback, Lyrics Edit, Timecode, General) instead of a flat list.
"""

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Footer, Static


def format_key_display(key: str) -> str:
    if key.startswith("ctrl+"):
        return "^" + key[5:].upper()
    if key == "shift+left":
        return "⇧←"
    if key == "shift+right":
        return "⇧→"
    if key == "left":
        return "←"
    if key == "right":
        return "→"
    if key == "up":
        return "↑"
    if key == "down":
        return "↓"
    if key == "space":
        return "⎵"
    if key == "escape":
        return "Esc"
    return key


class _BindingGroup(Static):
    def __init__(self, label: str, bindings: list[Binding]) -> None:
        self._group_label = label
        self._bindings = bindings
        super().__init__(self._format_content(), shrink=True)

    def _format_content(self) -> Text:
        parts: list[str] = [f"[bold]{self._group_label}[/bold] "]
        for i, b in enumerate(self._bindings):
            key_display = format_key_display(b.key)
            parts.append(f"[dim]{key_display}[/dim]={b.description}")
            if i < len(self._bindings) - 1:
                parts.append("[dim] │ [/dim]")

        content = Text.from_markup("".join(parts), overflow="ellipsis", end="")
        content.no_wrap = True
        return content


class GroupedFooter(Horizontal):
    DEFAULT_CSS = """
    GroupedFooter {
        height: 3;
        dock: bottom;
        background: $surface;
        color: $text;
        padding: 0 1;
        border-top: solid $primary;
        overflow: hidden;
    }
    GroupedFooter > _BindingGroup {
        width: 1fr;
        height: 1;
        padding: 0 1;
        overflow: hidden;
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
