"""Component Detail Panel widget for the Component Metadata editor (v6).

Right panel (details mode) showing all component metadata + song info, with
editable field navigation.
"""

from datetime import UTC, datetime

from rich.text import Text
from textual.widgets import Static

from stream_of_worship.admin.component_editor.constants import EDITABLE_FIELDS, ROLE_LABELS
from stream_of_worship.admin.component_editor.state import ComponentEditorState
from stream_of_worship.admin.services.lrc_parser import format_duration


def _format_timestamp(value: str | None) -> str:
    """Format an ISO-8601 timestamp to nearest second with UTC label."""
    if value is None:
        return "—"
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    except (ValueError, TypeError):
        return value


class ComponentDetailPanel(Static):
    """Right panel (details mode) showing all component metadata + song info.

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

    can_focus = True

    DEFAULT_CSS = """
    ComponentDetailPanel {
        height: 1fr;
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
        roles = session.ordered_component_roles
        if roles:
            idx = max(0, min(state.selected_row, len(roles) - 1))
            role = roles[idx]
        else:
            role = "entry"
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
            label = ROLE_LABELS.get(role, role.upper())
            text.append(f"\n[No {label} component]\n", style="red italic")
            self.update(text)
            self.scroll_home(animate=False)
            return

        text.append("\n")

        # -- Section: Component (merged: base metadata + editable + reasoning) --
        display_label = ROLE_LABELS.get(role, role.upper())
        text.append(f"-- Component ({display_label}) --\n", style="bold cyan")
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

        # 2b. Editable fields (sub-section within Component)
        text.append("\n")
        text.append("  -- Editable --\n", style="dim italic")
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

        # 2c. Reasoning (sub-section within Component)
        text.append("\n")
        text.append("  -- Reasoning --\n", style="dim italic")
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

        # -- Section: Confidence Breakdown (moved lower, just above Lifecycle) --
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

        # -- Section: Lifecycle (timestamps formatted to nearest seconds) --
        text.append("-- Lifecycle --\n", style="bold cyan")
        text.append(f"  {'Created':12s}: ", style="dim")
        text.append(f"{_format_timestamp(comp.created_at)}\n")
        text.append(f"  {'Updated':12s}: ", style="dim")
        text.append(f"{_format_timestamp(comp.updated_at)}\n")

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

        New layout (v2):
        - 1 (Song header) + 6 (song fields) = 7
        - 1 (blank) = 8
        - 1 (Component header) + 8 (base-metadata fields) = 17
        - 1 (blank) = 18
        - 1 (Editable sub-header) = 19
        - + index of field in EDITABLE_FIELDS = target line
        """
        try:
            field_idx = EDITABLE_FIELDS.index(field)
        except ValueError:
            return 0
        return 19 + field_idx
