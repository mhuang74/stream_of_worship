"""Component Detail Panel widget for the Component Metadata editor (v6).

Right panel (details mode) showing all component metadata + song info, with
editable field navigation.
"""

from datetime import UTC, datetime
from typing import ClassVar

from rich.segment import Segment
from rich.text import Text
from textual import events
from textual.binding import Binding
from textual.geometry import Size
from textual.scroll_view import ScrollView
from textual.strip import Strip

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


class ComponentDetailPanel(ScrollView, can_focus=True):
    """Right panel (details mode) showing all component metadata + song info.

    Displays:
    - Song-level info (title, artist, album, series, musical_key)
    - Component metadata for the selected role (entry/exit)
    - Confidence breakdown sub-section
    - Editable fields (theme, vocal_posture, groove_density, energy_level,
      start_time, end_time) with navigation highlight
    - Reasoning fields
    - Component lifecycle dates (created_at, updated_at) at the bottom

    Navigation: up/down arrows move focus among editable fields.
    Editing: 'e' for numeric fields, '['/']' for enum cycling.

    Implemented as a proper ``ScrollView`` subclass using the Line-API
    pattern (same as ``LyricsPanel``/``RichLog``/``Log``/``Tree``): content
    is rendered into ``Strip`` objects once on every ``update_detail``, then
    served line-by-line from ``render_line`` with ``scroll_offset.y`` applied.
    """

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

    # Reclaim Up/Down for editable-field focus navigation. This overrides the
    # inherited ScrollableContainer up/down (scroll_up/scroll_down) bindings.
    # Other inherited keys (pageup, pagedown, home, end, ctrl+pageup,
    # ctrl+pagedown, left, right) remain and provide free-scroll navigation.
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("up", "focus_up", "Field ↑", show=False),
        Binding("down", "focus_down", "Field ↓", show=False),
    ]

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._focus_idx: int = 0
        self._content_strips: list[Strip] = []
        self._render_width: int = 0
        self._last_text: Text | None = None
        self._last_state: ComponentEditorState | None = None

    # ------------------------------------------------------------------
    # Line-API rendering (the scroll fix)
    # ------------------------------------------------------------------

    def render_line(self, y: int) -> Strip:
        """Render a single visible line, applying scroll offset.

        Translates the viewport y coordinate to the content y coordinate by
        adding scroll_offset.y, then returns the matching content strip (or
        blank). This is the canonical Line-API pattern that makes scrolling
        actually work for a non-container widget.
        """
        width = self.scrollable_content_region.width or self.size.width
        scroll_x, scroll_y = self.scroll_offset
        line_index = scroll_y + y

        if line_index < 0 or line_index >= len(self._content_strips):
            return Strip.blank(width, self.rich_style)

        strip = self._content_strips[line_index]
        return strip.crop_extend(scroll_x, scroll_x + width, self.rich_style)

    def _rebuild_strips(self, text: Text) -> None:
        """Render Rich Text into Strip objects at the current content width."""
        width = self.scrollable_content_region.width or self.size.width or 1
        if width != self._render_width:
            self._render_width = width
        segments = self.app.console.render(text, self.app.console.options.update_width(width))
        lines = list(Segment.split_lines(segments))
        self._content_strips = [Strip(line).adjust_cell_length(width) for line in lines]
        self.virtual_size = Size(width, max(1, len(self._content_strips)))

    def on_resize(self, event: events.Resize) -> None:
        new_width = self.scrollable_content_region.width or self.size.width
        if new_width != self._render_width and self._last_text is not None:
            self._rebuild_strips(self._last_text)
            self.refresh()

    def update_detail(self, state: ComponentEditorState, reset_scroll: bool = True) -> None:
        """Render full component detail for the current song + selected role.

        Args:
            state: The current editor state snapshot.
            reset_scroll: When True (default), reset scroll to the top after
                rendering. When False, preserve the current scroll position
                (used by focus-move navigation so the viewport doesn't jump).
        """
        self._last_state = state
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
            self._last_text = text
            self._rebuild_strips(text)
            if reset_scroll:
                self.scroll_to(y=0, animate=False, immediate=True, force=True)
            self.refresh()
            return

        text.append("\n")

        # -- Section: Component (merged: base metadata + editable + reasoning) --
        display_label = ROLE_LABELS.get(role, role.upper())
        text.append(f"-- Component ({display_label}) --\n", style="bold cyan")
        detail_fields = [
            ("Type", comp.component_type),
            ("Occurrence", str(comp.occurrence_index)),
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
            elif field_name in ("start_time", "end_time"):
                hint = " [e]"
                if isinstance(value, (int, float)):
                    value_str = format_duration(float(value))
                elif value:
                    value_str = str(value)
                else:
                    value_str = "—"
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

        self._last_text = text
        self._rebuild_strips(text)
        if reset_scroll:
            self.scroll_to(y=0, animate=False, immediate=True, force=True)
        self.refresh()

    def move_focus_up(self) -> None:
        if self._focus_idx > 0:
            self._focus_idx -= 1

    def move_focus_down(self) -> None:
        if self._focus_idx < len(EDITABLE_FIELDS) - 1:
            self._focus_idx += 1

    @property
    def focused_field(self) -> str:
        return EDITABLE_FIELDS[self._focus_idx]

    def action_focus_up(self) -> None:
        """Handle Up arrow: move focus to previous editable field, re-render
        with new highlight, and bring focused field into view."""
        self.move_focus_up()
        self._rebuild_after_focus_change()

    def action_focus_down(self) -> None:
        """Handle Down arrow: move focus to next editable field, re-render
        with new highlight, and bring focused field into view."""
        self.move_focus_down()
        self._rebuild_after_focus_change()

    def _rebuild_after_focus_change(self) -> None:
        """Rebuild strips with current _focus_idx (new highlight marker) without
        changing scroll position; then bring the focused field into view."""
        if self._last_state is None:
            return
        self.update_detail(self._last_state, reset_scroll=False)
        self._scroll_focused_into_view()

    def _scroll_focused_into_view(self) -> None:
        """Bring the focused editable field line into view (minimum scroll).

        Mirrors Textual's scroll-into-view semantics for focused widgets: only
        scroll if the target is outside the current viewport; never re-center.
        """
        field = self.focused_field
        target_y = self.get_editable_field_line_offset(field)
        viewport_h = self.size.height
        if viewport_h <= 0:
            return
        scroll_y = self.scroll_y
        if target_y < scroll_y:
            self.scroll_to(y=target_y, animate=False, immediate=True, force=True)
        elif target_y >= scroll_y + viewport_h:
            new_y = target_y - viewport_h + 1
            max_y = max(0, self.virtual_size.height - viewport_h)
            self.scroll_to(y=min(new_y, max_y), animate=False, immediate=True, force=True)

    def get_editable_field_line_offset(self, field: str) -> int:
        """Return the 0-based line index of the given editable field's value
        within the rendered text. Used by the screen to position the Input overlay.

        Layout (with start_time/end_time added to EDITABLE_FIELDS and removed
        from base metadata):
        - 1 (Song header) + 6 (song fields) = 7
        - 1 (blank) = 8
        - 1 (Component header) + 6 (base-metadata fields) = 15
        - 1 (blank) = 16
        - 1 (Editable sub-header) = 17
        - + index of field in EDITABLE_FIELDS = target line
        """
        try:
            field_idx = EDITABLE_FIELDS.index(field)
        except ValueError:
            return 0
        return 17 + field_idx
