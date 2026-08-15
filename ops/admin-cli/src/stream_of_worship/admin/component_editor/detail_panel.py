"""Component Detail Panel widget for the Component Metadata editor (v5).

Bottom-right panel showing all component metadata + song info, with
editable field navigation.
"""

from rich.text import Text
from textual.widgets import Static

from stream_of_worship.admin.component_editor.constants import EDITABLE_FIELDS
from stream_of_worship.admin.component_editor.state import ComponentEditorState
from stream_of_worship.admin.services.lrc_parser import format_duration


class ComponentDetailPanel(Static):
    """Bottom-right panel showing all component metadata + song info.

    Displays:
    - Song-level info (title, artist, album, series, musical_key)
    - Component metadata for the selected role (entry/exit)
    - Confidence breakdown sub-section
    - Editable fields (theme, vocal_posture, groove_density, energy_level)
      with navigation highlight
    - Reasoning fields
    - Component lifecycle dates (created_at, updated_at) at the bottom

    Navigation: up/down arrows move focus among editable fields.
    Editing: 'e' for numeric fields, '['/']' for enum cycling.
    """

    DEFAULT_CSS = """
    ComponentDetailPanel {
        height: 1fr;
        border-left: solid $primary;
        padding: 0 1;
        overflow-y: auto;
        background: $surface;
    }
    ComponentDetailPanel:focus {
        border-left: double $accent;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._focus_idx: int = 0

    def update_detail(self, state: ComponentEditorState) -> None:
        """Render full component detail for the current song + selected role."""
        session = state.current
        role = "entry" if state.selected_row == 0 else "exit"
        comp = session.component_for_role(role)
        song = session.song

        text = Text()

        # -- Section: Song Info --
        text.append("-- Song Info --\n", style="bold cyan")
        song_fields = [
            ("Title", song.title if song else session.song_title),
            ("Artist", song.composer if song else None),
            ("Lyricist", song.lyricist if song else None),
            ("Album", song.album_name if song else None),
            ("Series", song.album_series if song else None),
            ("Song Key", song.musical_key if song else None),
        ]
        for label, value in song_fields:
            text.append(f"  {label:12s}: ", style="dim")
            text.append(f"{value or '—'}\n")

        if comp is None:
            text.append("\n[No component for this role]\n", style="red italic")
            self.update(text)
            self.scroll_home(animate=False)
            return

        text.append("\n")

        # -- Section: Component Details --
        text.append(f"-- Component ({role}) --\n", style="bold cyan")
        detail_fields = [
            ("Type", comp.component_type),
            ("Occurrence", str(comp.occurrence_index)),
            (
                "Start",
                format_duration(comp.start_time) if comp.start_time is not None else None,
            ),
            ("End", format_duration(comp.end_time) if comp.end_time is not None else None),
            ("BPM", f"{comp.bpm:.4g}" if comp.bpm is not None else None),
            ("Key", comp.key),
            ("Confidence", f"{comp.confidence:.4g}" if comp.confidence is not None else None),
            (
                "Backbeat",
                f"{comp.backbeat_strength:.4g}" if comp.backbeat_strength is not None else None,
            ),
        ]
        for label, value in detail_fields:
            text.append(f"  {label:12s}: ", style="dim")
            text.append(f"{value or '—'}\n")

        text.append("\n")

        # -- Section: Confidence Breakdown --
        text.append("-- Confidence Breakdown --\n", style="bold cyan")
        conf_fields = [
            ("BPM", comp.bpm_confidence),
            ("Key", comp.key_confidence),
            ("Groove", comp.groove_confidence),
            ("Backbeat", comp.backbeat_confidence),
            ("Energy", comp.energy_confidence),
            ("Theme", comp.theme_confidence),
            ("Posture", comp.vocal_posture_confidence),
        ]
        for label, value in conf_fields:
            text.append(f"  {label:12s}: ", style="dim")
            text.append(f"{value:.4g}" if value is not None else "—")
            text.append("\n")

        text.append("\n")

        # -- Section: Editable Fields --
        text.append("-- Editable Fields --\n", style="bold yellow")
        for i, field_name in enumerate(EDITABLE_FIELDS):
            value = state.get_value(role, field_name)
            is_focused = i == self._focus_idx
            marker = "►" if is_focused else " "

            if field_name in ("theme", "vocal_posture"):
                hint = " [◄ ►]"
                value_str = str(value) if value else "—"
            else:
                hint = " [e]"
                if isinstance(value, (int, float)):
                    value_str = f"{value:.4g}"
                elif value:
                    value_str = str(value)
                else:
                    value_str = "—"

            text.append(f" {marker} {field_name:15s}: ", style="dim")
            if is_focused:
                text.append(f"{value_str}{hint}\n", style="bold reverse")
            else:
                text.append(f"{value_str}{hint}\n")

        text.append("\n")

        # -- Section: Reasoning --
        text.append("-- Reasoning --\n", style="bold cyan")
        reasoning_fields = [
            ("Theme", comp.theme_reasoning),
            ("Posture", comp.posture_reasoning),
        ]
        for label, value in reasoning_fields:
            text.append(f"  {label:12s}: ", style="dim")
            if value:
                text.append(f"{value}\n")
            else:
                text.append("—\n")

        text.append("\n")

        # -- Section: Lifecycle (dates at bottom) --
        text.append("-- Lifecycle --\n", style="bold cyan")
        text.append(f"  {'Created':12s}: ", style="dim")
        text.append(f"{comp.created_at or '—'}\n")
        text.append(f"  {'Updated':12s}: ", style="dim")
        text.append(f"{comp.updated_at or '—'}\n")

        self.update(text)
        self.scroll_home(animate=False)

    def move_focus_up(self) -> None:
        if self._focus_idx > 0:
            self._focus_idx -= 1

    def move_focus_down(self) -> None:
        if self._focus_idx < len(EDITABLE_FIELDS) - 1:
            self._focus_idx += 1

    @property
    def focused_field(self) -> str:
        return EDITABLE_FIELDS[self._focus_idx]

    def get_editable_field_line_offset(self, field: str) -> int:
        """Return the 0-based line index of the given editable field's value
        within the rendered text. Used by the screen to position the Input overlay.

        The layout is deterministic:
        - 1 (Song header) + 6 (song fields) = 7
        - 1 (blank) = 8
        - 1 (Component header) + 8 (component fields) = 17
        - 1 (blank) = 18
        - 1 (Confidence header) + 7 (conf fields) = 26
        - 1 (blank) = 27
        - 1 (Editable header) = 28
        - + index of field in EDITABLE_FIELDS = target line
        """
        try:
            field_idx = EDITABLE_FIELDS.index(field)
        except ValueError:
            return 0
        return 28 + field_idx
