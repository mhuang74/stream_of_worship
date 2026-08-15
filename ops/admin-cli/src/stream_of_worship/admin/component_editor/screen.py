"""Main editor screen for the admin Component Metadata editor (v5).

v5 layout (T-shape, 3 panels):
    Header
    SongBreadcrumb
    PlaybackBar
    #top-panel (full-width):
        ComponentHeroPanel       (v4, retained)
        ComponentMetadataTable   (compact, read-only, 9 numerical columns)
    #bottom-split (50/50 horizontal):
        LyricsPanel              (bottom-left, LRC + playback highlight)
        ComponentDetailPanel     (bottom-right, all metadata + editable fields)
    Input(#row-edit-input, hidden overlay)
    StatusIndicator
    GroupedFooter

v5 changes from v4:
- T-shaped 3-panel layout: Hero Panel + compact table on top; bottom split =
  lyrics (left) + detail panel (right).
- Compact table with 9 numerical columns (COMPACT_TABLE_COLUMNS), read-only.
- New LyricsPanel with playback-synced current-line highlight.
- New ComponentDetailPanel with all component metadata + editable field nav.
- Tab/shift+tab cycles panel focus (top → lyrics → details).
- Edit actions (e, [, ]) operate on the detail panel's focused field.
"""

import asyncio
import logging
from pathlib import Path
from typing import Any, ClassVar, Literal

from rich.text import Text
from textual import events, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.coordinate import Coordinate
from textual.css.query import NoMatches
from textual.geometry import Offset
from textual.screen import Screen
from textual.widgets import (
    DataTable,
    Header,
    Input,
    Label,
    Static,
)

from stream_of_worship.admin.component_editor.autosave import (
    ComponentAutosaveState,
    clear_autosave,
    load_autosave,
    save_autosave,
)
from stream_of_worship.admin.component_editor.constants import (
    COMPACT_TABLE_COLUMNS,
    COMPONENT_SCHEMA_VERSION,
    ENERGY_LEVEL_MAX,
    ENERGY_LEVEL_MIN,
    GROOVE_DENSITY_MAX,
    GROOVE_DENSITY_MIN,
    HERO_PRIMARY_FIELDS,
    HERO_REASONING_FIELDS,
    THEME_VALUES,
    VOCAL_POSTURE_VALUES,
)
from stream_of_worship.admin.component_editor.detail_panel import ComponentDetailPanel
from stream_of_worship.admin.component_editor.lrc_fetch import (
    LRCFetch,
    fetch_lrc_for_song,
    prefetch_all_lrc,
)
from stream_of_worship.admin.component_editor.lyrics_panel import LyricsPanel
from stream_of_worship.admin.component_editor.state import (
    ComponentEditorState,
    SongSession,
)
from stream_of_worship.admin.db.client import DatabaseClient
from stream_of_worship.admin.editor.footer import GroupedFooter, format_key_display
from stream_of_worship.admin.services.lrc_parser import format_duration, parse_lrc_full
from stream_of_worship.admin.services.playback import PlaybackService, PlaybackState
from stream_of_worship.admin.services.r2 import R2Client

logger = logging.getLogger(__name__)


def first_content_hash(session: SongSession) -> str:
    """Pick a non-None component's content_hash for the synthesised R2 payload.

    B2 fix: avoids the AttributeError raised by the v1 chain when
    entry_component is None (partial-analysis case).
    """
    if session.entry_component is not None:
        return session.entry_component.content_hash or ""
    if session.exit_component is not None:
        return session.exit_component.content_hash or ""
    return ""


def _fmt_time(seconds: float | None) -> str:
    if seconds is None:
        return "--:--"
    m = int(seconds) // 60
    s = int(seconds) % 60
    return f"{m:02d}:{s:02d}"


class SongBreadcrumb(Static):
    """Breadcrumb showing current song index, id, title, and hash_prefix."""

    def __init__(self):
        super().__init__("")
        self._index = 1
        self._total = 1
        self._song_id = ""
        self._song_title = ""
        self._hash_prefix = ""

    def update_breadcrumb(
        self,
        index: int,
        total: int,
        song_id: str,
        song_title: str,
        hash_prefix: str,
    ) -> None:
        self._index = index
        self._total = total
        self._song_id = song_id
        self._song_title = song_title
        self._hash_prefix = hash_prefix
        self.update(
            f"[bold cyan]● Song {index} / {total}[/]  —  "
            f"[{song_id}] {song_title}  —  hash_prefix={hash_prefix}"
        )


class PlaybackBar(Static):
    """Playback progress display."""

    def __init__(self):
        super().__init__("")
        self._position = 0.0
        self._duration = 0.0
        self._state = PlaybackState.STOPPED

    def update_playback(self, position: float, duration: float, state: PlaybackState) -> None:
        self._position = position
        self._duration = duration
        self._state = state

        state_icon = {
            PlaybackState.PLAYING: "▶",
            PlaybackState.PAUSED: "⏸",
            PlaybackState.STOPPED: "⏹",
        }.get(state, "?")

        pos_str = format_duration(position)
        dur_str = format_duration(duration)

        if duration > 0:
            progress = position / duration
            bar_width = 30
            filled = int(progress * bar_width)
            bar = "█" * filled + "░" * (bar_width - filled)
            self.update(f" {state_icon} [{pos_str}/{dur_str}] {bar}")
        else:
            self.update(f" {state_icon} [{pos_str}/{dur_str}]")


class ComponentHeroPanel(Static):
    """v4 NEW. Always-visible summary of the highlighted component's
    transition-critical fields + LLM reasoning. Renders Rich Text refreshed
    on cursor move, song switch, edit, undo/redo, and save.
    """

    DEFAULT_CSS = """
    ComponentHeroPanel {
        border: round $accent;
        padding: 0 1;
        height: auto;
        margin: 0 0 0 0;
    }
    """

    def render_panel(self, state: ComponentEditorState) -> None:
        session = state.current
        comp = state.get_selected_component()
        role = "entry" if state.selected_row == 0 else "exit"

        if comp is None:
            self.update(
                Text(
                    f"No {role} Chorus component — run `sow-admin audio "
                    f"components {session.song_id}` first",
                    style="dim italic",
                )
            )
            return

        t = Text()
        # Row 1: header.
        header_style = "bold cyan" if role == "entry" else "bold magenta"
        t.append(f"▶ {role.upper()} CHORUS", style=header_style)
        t.append(
            f"  —  Occurrence {comp.occurrence_index}  —  "
            f"[{_fmt_time(comp.start_time)} → {_fmt_time(comp.end_time)}]",
            style=header_style,
        )
        t.append("\n")

        # Row 2: primary transition metrics.
        primary_parts = []
        for field_name, label, fmt in HERO_PRIMARY_FIELDS:
            v = state.get_value(role, field_name)
            if v is None:
                txt = "—"
            elif fmt:
                try:
                    txt = fmt.format(v)
                except (TypeError, ValueError):
                    txt = str(v)
            else:
                txt = str(v)
            primary_parts.append(f"{label} {txt}")
        t.append("    " + "    ".join(primary_parts), style="")
        t.append("\n")

        # Row 3: editable theme + vocal_posture (accent).
        theme_v = state.get_value(role, "theme") or "—"
        posture_v = state.get_value(role, "vocal_posture") or "—"
        t.append(
            f"    Theme: {theme_v}    Vocal posture: {posture_v}",
            style="bold yellow",
        )
        t.append("\n\n")

        # Rows 4-5: reasoning (italic, dimmed, full text — no truncation).
        for field_name, label in HERO_REASONING_FIELDS:
            v = getattr(comp, field_name, None)
            if not v:
                t.append(
                    f"    {label}: — (LLM did not supply reasoning)\n",
                    style="dim italic",
                )
            else:
                t.append(f"    {label}: {v}\n", style="dim italic")

        self.update(t)


class StatusIndicator(Static):
    """Dirty / autosave / song-index / r2-pending status indicator."""

    def __init__(self):
        super().__init__("")
        self._dirty = False
        self._autosave_ok = False
        self._song_index = 1
        self._song_total = 1
        self._r2_pending = False

    def update_status(
        self,
        dirty: bool,
        autosave_ok: bool,
        song_index: int,
        song_total: int,
        r2_pending: bool = False,
    ) -> None:
        self._dirty = dirty
        self._autosave_ok = autosave_ok
        self._song_index = song_index
        self._song_total = song_total
        self._r2_pending = r2_pending

        dirty_mark = "[red]*[/red]" if dirty else "[green]✓[/green]"
        autosave_mark = "[green]saved[/green]" if autosave_ok else "[dim]—[/dim]"
        parts = [
            f" {dirty_mark} Dirty | Autosave: {autosave_mark} | Song {song_index}/{song_total}"
        ]
        if r2_pending:
            parts.append(" | [yellow]R2 pending — press s to retry[/yellow]")
        self.update("".join(parts))


class ComponentMetadataTable(DataTable):
    """Component metadata table with edit-guard-aware row navigation."""

    def action_cursor_up(self) -> None:
        guard_edit = getattr(self.screen, "_guard_active_edit", None)
        if guard_edit is not None and guard_edit():
            return
        super().action_cursor_up()

    def action_cursor_down(self) -> None:
        guard_edit = getattr(self.screen, "_guard_active_edit", None)
        if guard_edit is not None and guard_edit():
            return
        super().action_cursor_down()

    def action_page_up(self) -> None:
        guard_edit = getattr(self.screen, "_guard_active_edit", None)
        if guard_edit is not None and guard_edit():
            return
        self.scroll_page_up(animate=False, force=True)

    def action_page_down(self) -> None:
        guard_edit = getattr(self.screen, "_guard_active_edit", None)
        if guard_edit is not None and guard_edit():
            return
        self.scroll_page_down(animate=False, force=True)


class ComponentEditorScreen(Screen[None]):
    """Main interactive Component Metadata editor screen (v4).

    Provides:
    - Song breadcrumb showing current song index / id / title
    - Playback progress display
    - ComponentHeroPanel (v4 NEW) — always-visible summary of the highlighted
      component's transition-critical fields + LLM reasoning
    - Component metadata table (entry + exit rows × reordered columns)
    - Dirty / autosave / r2-pending status indicator
    - Footer with keyboard shortcuts
    """

    DEFAULT_CSS = """
    ComponentEditorScreen {
        layout: vertical;
    }

    #editor-body {
        height: 1fr;
        overflow: hidden;
    }

    /* Top panel: Hero Panel (auto height) + compact table (fixed mini-height) */
    #top-panel {
        height: auto;
        max-height: 20;
        overflow: hidden;
        border-bottom: solid $primary;
    }

    #component-table {
        height: 6;
    }

    /* Bottom split: 50/50 horizontal */
    #bottom-split {
        height: 1fr;
        overflow: hidden;
    }

    #lyrics-panel {
        width: 1fr;
        overflow-y: auto;
        background: $surface;
        border-right: solid $primary;
    }

    #detail-panel {
        width: 1fr;
        overflow-y: auto;
        background: $surface;
    }

    #row-edit-input {
        display: none;
        height: 1;
        layer: overlay;
    }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        # Playback / Nav (global — work on any panel)
        Binding("space", "toggle_playback_for_component", "Play/Pause"),
        Binding("left", "seek_backward", "Seek -5s"),
        Binding("right", "seek_forward", "Seek +5s"),
        Binding("j", "jump_to_component", "Jump"),
        # Song switch (global)
        Binding("n", "next_song", "Next Song"),
        Binding("p", "prev_song", "Prev Song"),
        # Panel navigation (replaces column nav)
        Binding("tab", "cycle_panel_next", "Panel →"),
        Binding("shift+tab", "cycle_panel_prev", "Panel ←"),
        # Edit (only active when detail panel is focused)
        Binding("bracketleft", "cycle_field_prev", "Cycle −"),
        Binding("bracketright", "cycle_field_next", "Cycle +"),
        Binding("e", "edit_numeric", "Edit Num"),
        # Detail panel field navigation (only when detail panel is focused)
        Binding("up", "detail_focus_up", "Field ↑"),
        Binding("down", "detail_focus_down", "Field ↓"),
        # General (global)
        Binding("s", "save", "Save"),
        Binding("ctrl+z", "undo", "Undo"),
        Binding("ctrl+y", "redo", "Redo"),
        Binding("escape", "quit_editor", "Quit"),
        Binding("q", "quit_editor", "Quit"),
        Binding("?", "show_keymap", "Keymap"),
    ]

    BINDING_GROUPS: ClassVar[dict[str, list[str]]] = {
        "Playback": [
            "toggle_playback_for_component",
            "seek_backward",
            "seek_forward",
            "jump_to_component",
        ],
        "Songs": ["next_song", "prev_song"],
        "Panels": [
            "cycle_panel_next",
            "cycle_panel_prev",
            "detail_focus_up",
            "detail_focus_down",
        ],
        "Edit": ["cycle_field_prev", "cycle_field_next", "edit_numeric"],
        "General": ["save", "undo", "redo", "quit_editor", "show_keymap"],
    }

    def __init__(
        self,
        editor_state: ComponentEditorState,
        playback_service: PlaybackService,
        cache_dir: Path,
        r2_client: R2Client,
        db_client: DatabaseClient,
    ):
        super().__init__()
        self.state = editor_state
        self.playback = playback_service
        self.cache_dir = cache_dir
        self.r2_client = r2_client
        self.db_client = db_client
        self._autosave_ok = False
        self._edit_mode: Literal["numeric"] | None = None
        self._edit_target_role: str | None = None
        self._edit_target_field: str | None = None
        self._position_update_timer: asyncio.Task | None = None
        self._active_panel: str = "top"  # "top" | "lyrics" | "details"

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="editor-body"):
            yield SongBreadcrumb()
            yield PlaybackBar()

            # Top panel: Hero Panel + compact read-only table
            with Vertical(id="top-panel"):
                yield ComponentHeroPanel()
                yield ComponentMetadataTable(id="component-table")

            # Bottom split: lyrics (left) + details (right)
            with Horizontal(id="bottom-split"):
                yield LyricsPanel(id="lyrics-panel")
                yield ComponentDetailPanel(id="detail-panel")

            # Hidden Input overlay (for numeric editing in detail panel)
            yield Input(
                id="row-edit-input",
                placeholder="Edit numeric value",
                select_on_focus=False,
                compact=True,
            )
            yield StatusIndicator()
        yield GroupedFooter()

    def on_mount(self) -> None:
        self._setup_table()
        self._refresh_table()
        self._refresh_detail_panel()
        self._refresh_hero()
        self.query_one("#component-table", ComponentMetadataTable).focus()
        self._active_panel = "top"
        self._update_breadcrumb()
        self._update_status()
        self._start_position_updates()

        self.playback.set_callbacks(
            on_position_changed=self._on_playback_position,
            on_state_changed=self._on_playback_state,
            on_finished=self._on_playback_finished,
        )

        self._load_audio_for_current_song()
        self._maybe_apply_autosave()

        # LRC pre-fetch (v5)
        self.state.lrc_prefetch_in_progress = True
        self._refresh_lyrics_panel()
        self._prefetch_lrc()

    def on_unmount(self) -> None:
        self.playback.stop()
        self.playback.set_callbacks()
        if self._position_update_timer:
            self._position_update_timer.cancel()

    # --- Table setup / refresh ---

    def _setup_table(self) -> None:
        table = self.query_one("#component-table", DataTable)
        table.add_columns(*(header for _, header in COMPACT_TABLE_COLUMNS))
        table.cursor_type = "row"
        table.show_cursor = True
        table.zebra_stripes = True

    def _cell_value(self, role: str, field_key: str) -> str:
        session = self.state.current
        comp = session.component_for_role(role)
        if comp is None:
            return "—"
        value = self.state.get_value(role, field_key)
        return self._format_cell_value(field_key, value)

    def _format_cell_value(self, field_key: str, value: Any) -> str:
        if value is None:
            return "—"
        if field_key in ("start_time", "end_time") and isinstance(value, (int, float)):
            return format_duration(float(value))
        if isinstance(value, float):
            return f"{value:.4g}"
        return str(value)

    def _refresh_table(self) -> None:
        table = self.query_one("#component-table", DataTable)
        table.clear()
        for role in ("entry", "exit"):
            row_values = [self._cell_value(role, key) for key, _ in COMPACT_TABLE_COLUMNS]
            table.add_row(*row_values, key=role)
        row = max(0, min(self.state.selected_row, 1))
        try:
            table.move_cursor(row=row, scroll=True)
        except Exception:  # noqa: BLE001 S110
            pass

    def _refresh_table_cell(self, role: str, field_name: str) -> None:
        try:
            table = self.query_one("#component-table", DataTable)
        except NoMatches:
            return
        try:
            col_idx = next(
                i for i, (key, _) in enumerate(COMPACT_TABLE_COLUMNS) if key == field_name
            )
        except StopIteration:
            return
        value = self._cell_value(role, field_name)
        try:
            table.update_cell_at(Coordinate(0 if role == "entry" else 1, col_idx), value)
        except Exception:  # noqa: BLE001 S110
            pass

    # --- Breadcrumb / status ---

    def _update_breadcrumb(self) -> None:
        session = self.state.current
        try:
            breadcrumb = self.query_one(SongBreadcrumb)
        except NoMatches:
            return
        breadcrumb.update_breadcrumb(
            index=self.state.current_index + 1,
            total=len(self.state.sessions),
            song_id=session.song_id,
            song_title=session.song_title,
            hash_prefix=session.hash_prefix,
        )

    def _update_status(self) -> None:
        session = self.state.current
        try:
            status = self.query_one(StatusIndicator)
        except NoMatches:
            return
        status.update_status(
            dirty=session.dirty,
            autosave_ok=self._autosave_ok,
            song_index=self.state.current_index + 1,
            song_total=len(self.state.sessions),
            r2_pending=session.r2_save_pending,
        )

    # --- Hero panel (v4 NEW) ---

    def _refresh_hero(self) -> None:
        """v4 NEW. Re-renders the ComponentHeroPanel against the current state."""
        try:
            panel = self.query_one(ComponentHeroPanel)
        except NoMatches:
            return
        panel.render_panel(self.state)

    def _refresh_detail_panel(self) -> None:
        """v5 NEW. Re-renders the ComponentDetailPanel against the current state."""
        try:
            panel = self.query_one("#detail-panel", ComponentDetailPanel)
        except NoMatches:
            return
        panel.update_detail(self.state)

    def _refresh_lyrics_panel(self) -> None:
        """v5 NEW. Re-renders the LyricsPanel for the current song."""
        try:
            panel = self.query_one("#lyrics-panel", LyricsPanel)
        except NoMatches:
            return
        session = self.state.current
        if session is None:
            panel.update_lrc(None, "")
            return

        song_id = session.song_id
        song_title = session.song_title

        if song_id in self.state.lrc_parsed:
            fetch = self.state.lrc_fetches.get(song_id)
            if fetch and fetch.error:
                panel.update_error(fetch.error, song_title)
            else:
                parsed = self.state.lrc_parsed[song_id]
                pos = self.playback.position_seconds or 0.0
                idx = LyricsPanel.compute_highlighted_index(parsed, pos)
                panel.update_lrc(parsed, song_title, highlighted_index=idx)
            return

        if self.state.lrc_prefetch_in_progress:
            panel.update_fetching(song_title)
            self._fetch_lrc_on_demand(song_id, session.hash_prefix, song_title)
            return

        panel.update_lrc(None, song_title)

    # --- Playback ---

    def _load_audio_for_current_song(self) -> None:
        session = self.state.current
        if not session.audio_path:
            return
        self.playback.load(Path(session.audio_path))

    def _start_position_updates(self) -> None:
        async def _update_loop():
            while True:
                await asyncio.sleep(0.2)
                self._update_playback_bar()
                self._update_lyrics_highlight()

        self._position_update_timer = asyncio.ensure_future(_update_loop())

    def _update_playback_bar(self) -> None:
        try:
            bar = self.query_one(PlaybackBar)
        except NoMatches:
            return
        pos = self.playback.position_seconds
        dur = self.playback.duration_seconds
        bar.update_playback(pos, dur, self.playback.state)

    def _on_playback_position(self, position) -> None:
        self._update_playback_bar()
        self._update_lyrics_highlight()

    def _update_lyrics_highlight(self) -> None:
        """v5 NEW. Update the lyrics panel's current-line highlight based on
        playback position. Called at 5Hz from the position update timer.
        """
        try:
            panel = self.query_one("#lyrics-panel", LyricsPanel)
        except NoMatches:
            return
        session = self.state.current
        if session is None:
            return
        parsed = self.state.lrc_parsed.get(session.song_id)
        if parsed is None:
            return
        pos = self.playback.position_seconds or 0.0
        idx = LyricsPanel.compute_highlighted_index(parsed, pos)
        panel.set_highlighted_index(idx)

    def _on_playback_state(self, new_state: PlaybackState) -> None:
        self._update_playback_bar()

    def _on_playback_finished(self) -> None:
        self._update_playback_bar()

    # --- Selection helpers ---

    def _selected_role(self) -> str:
        return "entry" if self.state.selected_row == 0 else "exit"

    def _selected_field_key(self) -> str:
        """Return the field key for the current cursor column (retained for
        autosave compat). In v5 the table is read-only with row-only cursor,
        so this always returns the stored selected_column_key.
        """
        return self.state.selected_column_key

    def _sync_selection_from_table_cursor(self) -> None:
        try:
            table = self.query_one("#component-table", DataTable)
        except NoMatches:
            return
        cursor_row = table.cursor_row
        if cursor_row is None:
            return
        if 0 <= cursor_row <= 1:
            self.state.selected_row = cursor_row
        # Also trigger detail panel + hero refresh
        self._refresh_detail_panel()
        self._refresh_hero()

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.data_table.id != "component-table":
            return
        self._sync_selection_from_table_cursor()
        self._refresh_hero()  # v4 NEW

    def on_data_table_cell_highlighted(self, event: DataTable.CellHighlighted) -> None:
        if event.data_table.id != "component-table":
            return
        prev_row = self.state.selected_row
        self._sync_selection_from_table_cursor()
        if self.state.selected_row != prev_row:
            self._refresh_hero()  # v4 NEW

    def on_data_table_cell_selected(self, event: DataTable.CellSelected) -> None:
        if event.data_table.id != "component-table":
            return
        self._sync_selection_from_table_cursor()
        self._refresh_hero()  # v4 NEW

    # --- Guards ---

    def _is_edit_active(self) -> bool:
        return self._edit_mode is not None

    def _guard_active_edit(self) -> bool:
        if self._is_edit_active():
            self.notify("Finish editing first", severity="warning", timeout=2)
            return True
        return False

    def _guard_no_component(self) -> bool:
        """Return True (and bell) if the selected role has no component row."""
        role = self._selected_role()
        comp = self.state.current.component_for_role(role)
        if comp is None:
            self.app.bell()
            self.notify(f"No {role} component for this song", severity="warning", timeout=2)
            return True
        return False

    # --- Numeric edit overlay ---

    def _hide_row_edit_input(self) -> None:
        try:
            edit_input = self.query_one("#row-edit-input", Input)
        except NoMatches:
            return
        edit_input.value = ""
        edit_input.display = False
        self._edit_mode = None
        self._edit_target_role = None
        self._edit_target_field = None

    def _cancel_row_edit(self) -> None:
        self._hide_row_edit_input()
        try:
            self.query_one("#detail-panel", ComponentDetailPanel).focus()
        except NoMatches:
            pass

    def _show_value_edit_input(self, role: str, field: str, initial_text: str) -> None:
        detail_panel = self.query_one("#detail-panel", ComponentDetailPanel)

        def do_show() -> None:
            panel_region = detail_panel.region
            scroll_y = detail_panel.scroll_y

            editable_section_line = detail_panel.get_editable_field_line_offset(field)
            y = panel_region.y + editable_section_line - scroll_y
            x = panel_region.x + 2  # left padding
            width = panel_region.width - 4  # padding on both sides

            if y < panel_region.y or y >= panel_region.y + panel_region.height:
                self.notify("Scroll to the field to edit", severity="warning", timeout=2)
                return

            edit_input = self.query_one("#row-edit-input", Input)
            edit_input.value = initial_text
            edit_input.cursor_position = 0
            edit_input.set_scroll(0, None)
            edit_input.placeholder = f"Edit {field}"
            edit_input.styles.offset = Offset(x, y)
            edit_input.styles.width = max(1, width)
            edit_input.display = True
            self._edit_mode = "numeric"
            self._edit_target_role = role
            self._edit_target_field = field
            edit_input.focus()

        self.call_after_refresh(do_show)

    def _validate_numeric_field(self, field: str, text: str) -> float | None:
        try:
            val = float(text.strip())
        except ValueError:
            return None
        if (
            field == "groove_density" and not (GROOVE_DENSITY_MIN <= val <= GROOVE_DENSITY_MAX)
        ) or (field == "energy_level" and not (ENERGY_LEVEL_MIN <= val <= ENERGY_LEVEL_MAX)):
            return None
        return val

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "row-edit-input":
            return
        if (
            self._edit_mode is None
            or self._edit_target_role is None
            or self._edit_target_field is None
        ):
            self._cancel_row_edit()
            return

        role = self._edit_target_role
        field = self._edit_target_field
        val = self._validate_numeric_field(field, event.value)
        if val is None:
            self.app.bell()
            self.notify(
                f"Invalid value for {field}",
                severity="warning",
                timeout=2,
            )
            return

        self.state.set_value(role, field, val)
        self._hide_row_edit_input()
        self._refresh_table()
        self._refresh_detail_panel()
        self._refresh_hero()
        self._do_autosave()
        self._update_status()
        # Refocus the detail panel (not the table)
        self.query_one("#detail-panel", ComponentDetailPanel).focus()

    def on_resize(self, event: events.Resize) -> None:
        if self._edit_mode is None:
            return
        if self._edit_target_role is None or self._edit_target_field is None:
            return
        # Reposition the Input overlay over the detail panel's focused field
        try:
            detail_panel = self.query_one("#detail-panel", ComponentDetailPanel)
        except NoMatches:
            return
        panel_region = detail_panel.region
        scroll_y = detail_panel.scroll_y
        line_offset = detail_panel.get_editable_field_line_offset(self._edit_target_field)
        y = panel_region.y + line_offset - scroll_y
        x = panel_region.x + 2
        width = panel_region.width - 4
        if y < panel_region.y or y >= panel_region.y + panel_region.height:
            self._cancel_row_edit()
            return
        edit_input = self.query_one("#row-edit-input", Input)
        edit_input.styles.offset = Offset(x, y)
        edit_input.styles.width = max(1, width)

    # --- Autosave ---

    def _do_autosave(self) -> bool:
        session = self.state.current
        snapshot = ComponentAutosaveState(
            song_id=session.song_id,
            hash_prefix=session.hash_prefix,
            working=[
                {"role": role, "field": field, "value": value}
                for (role, field), value in session.working.items()
            ],
            dirty=session.dirty,
            selected_row=self.state.selected_row,
            selected_column_key=self.state.selected_column_key,
            r2_save_pending=session.r2_save_pending,
        )
        ok = save_autosave(self.cache_dir, snapshot)
        self._autosave_ok = ok
        self._update_status()
        return ok

    def _maybe_apply_autosave(self) -> None:
        session = self.state.current
        snapshot = load_autosave(self.cache_dir, session.hash_prefix)
        if snapshot is None:
            return
        if not snapshot.dirty and not snapshot.r2_save_pending:
            return

        for item in snapshot.working:
            role = item.get("role")
            field = item.get("field")
            value = item.get("value")
            if role and field:
                session.working[(role, field)] = value
        session.dirty = snapshot.dirty
        session.r2_save_pending = snapshot.r2_save_pending
        self.state.selected_row = snapshot.selected_row
        self.state.selected_column_key = snapshot.selected_column_key

        if snapshot.r2_save_pending:
            self._notify(
                "[yellow]Recovered edits — DB committed but R2 still pending — press s to retry.[/]"
            )
        else:
            self._notify("[yellow]Recovered unsaved edits from autosave.[/]")
        self._refresh_table()
        self._refresh_detail_panel()
        self._refresh_lyrics_panel()
        self._refresh_hero()
        self._update_status()

    # --- Song switch ---

    def _switch_song(self, delta: int) -> None:
        if self.state.current.dirty and not self._do_autosave():
            self.app.bell()
            return
        new_idx = (self.state.current_index + delta) % len(self.state.sessions)
        if new_idx == self.state.current_index:
            return
        self.state.current_index = new_idx
        self.playback.stop()
        self._load_audio_for_current_song()
        self._refresh_table()
        self._refresh_detail_panel()
        self._refresh_lyrics_panel()
        self._refresh_hero()
        self._update_breadcrumb()
        self._update_status()

    # --- Panel navigation (v5) ---

    _PANEL_ORDER = ("top", "lyrics", "details")

    def action_cycle_panel_next(self) -> None:
        if self._guard_active_edit():
            return
        idx = self._PANEL_ORDER.index(self._active_panel)
        self._active_panel = self._PANEL_ORDER[(idx + 1) % len(self._PANEL_ORDER)]
        self._focus_active_panel()

    def action_cycle_panel_prev(self) -> None:
        if self._guard_active_edit():
            return
        idx = self._PANEL_ORDER.index(self._active_panel)
        self._active_panel = self._PANEL_ORDER[(idx - 1) % len(self._PANEL_ORDER)]
        self._focus_active_panel()

    def _focus_active_panel(self) -> None:
        if self._active_panel == "top":
            self.query_one("#component-table", ComponentMetadataTable).focus()
        elif self._active_panel == "lyrics":
            self.query_one("#lyrics-panel", LyricsPanel).focus()
        elif self._active_panel == "details":
            self.query_one("#detail-panel", ComponentDetailPanel).focus()
            self._refresh_detail_panel()

    def action_detail_focus_up(self) -> None:
        if self._active_panel != "details":
            return
        if self._guard_active_edit():
            return
        panel = self.query_one("#detail-panel", ComponentDetailPanel)
        panel.move_focus_up()
        self._refresh_detail_panel()

    def action_detail_focus_down(self) -> None:
        if self._active_panel != "details":
            return
        if self._guard_active_edit():
            return
        panel = self.query_one("#detail-panel", ComponentDetailPanel)
        panel.move_focus_down()
        self._refresh_detail_panel()

    # --- LRC pre-fetch + on-demand (v5) ---

    @work(exclusive=True, group="lrc-fetch")
    async def _prefetch_lrc(self) -> None:
        try:
            fetches = await prefetch_all_lrc(
                self.state.sessions,
                self.r2_client,
                self.cache_dir,
            )
            for song_id, fetch in fetches.items():
                self.state.lrc_fetches[song_id] = fetch
                self.state.lrc_parsed[song_id] = (
                    parse_lrc_full(fetch.content) if fetch.content else None
                )
        except Exception as exc:  # noqa: BLE001
            self.state.lrc_fetch_error = str(exc)
        finally:
            self.state.lrc_prefetch_in_progress = False
            self._refresh_lyrics_panel()

    @work(exclusive=False, group="lrc-fetch-on-demand")
    async def _fetch_lrc_on_demand(
        self, song_id: str, hash_prefix: str, song_title: str
    ) -> None:
        if song_id in self.state.lrc_parsed:
            return
        try:
            fetch = await fetch_lrc_for_song(
                song_id, hash_prefix, self.r2_client, self.cache_dir,
            )
            self.state.lrc_fetches[song_id] = fetch
            self.state.lrc_parsed[song_id] = (
                parse_lrc_full(fetch.content) if fetch.content else None
            )
        except Exception as exc:  # noqa: BLE001
            self.state.lrc_fetches[song_id] = LRCFetch(
                song_id=song_id, content=None, cached_path=None, error=str(exc),
            )
            self.state.lrc_parsed[song_id] = None
        current = self.state.current
        if current and current.song_id == song_id:
            self._refresh_lyrics_panel()

    # --- Action handlers ---

    def action_toggle_playback_for_component(self) -> None:
        """v4 (D3). Play or pause the song, anchored to the highlighted component.

        - If playing: pause.
        - If paused/stopped: seek to the highlighted component's start_time
          (unless already inside its [start, end] range), then play.
        - If no component is highlighted: best-effort play() without seeking.
        """
        if self._guard_active_edit():
            return

        if self.playback.is_playing:
            self.playback.pause()
            return

        comp = self.state.get_selected_component()
        if comp is not None:
            pos = self.playback.position_seconds or 0.0
            start = comp.start_time or 0.0
            end = comp.end_time if comp.end_time is not None else float("inf")
            inside = start <= pos <= end
            if not inside:
                self.playback.seek(comp.start_time or 0.0)
                self._update_lyrics_highlight()
        self.playback.play()

    def action_seek_forward(self) -> None:
        if self._guard_active_edit():
            return
        self.playback.skip_forward(5.0)

    def action_seek_backward(self) -> None:
        if self._guard_active_edit():
            return
        self.playback.skip_backward(5.0)

    def action_jump_to_component(self) -> None:
        if self._guard_active_edit():
            return
        comp = self.state.get_selected_component()
        if comp is None or comp.start_time is None:
            self.app.bell()
            return
        self.playback.seek(comp.start_time)
        self._update_playback_bar()
        self._update_lyrics_highlight()

    def action_next_song(self) -> None:
        if self._guard_active_edit():
            return
        self._switch_song(1)

    def action_prev_song(self) -> None:
        if self._guard_active_edit():
            return
        self._switch_song(-1)

    def action_cycle_field_next(self) -> None:
        if self._guard_active_edit():
            return
        if self._active_panel != "details":
            return
        if self._guard_no_component():
            return

        panel = self.query_one("#detail-panel", ComponentDetailPanel)
        field = panel.focused_field
        if field not in ("theme", "vocal_posture"):
            return

        role = self._selected_role()
        values = THEME_VALUES if field == "theme" else VOCAL_POSTURE_VALUES
        current = self.state.get_value(role, field)
        try:
            idx = values.index(current)
        except (ValueError, TypeError):
            idx = -1
        new_value = values[(idx + 1) % len(values)]
        self.state.set_value(role, field, new_value)
        self._do_autosave()
        self._refresh_table()
        self._refresh_detail_panel()
        self._refresh_hero()
        self._update_status()

    def action_cycle_field_prev(self) -> None:
        if self._guard_active_edit():
            return
        if self._active_panel != "details":
            return
        if self._guard_no_component():
            return

        panel = self.query_one("#detail-panel", ComponentDetailPanel)
        field = panel.focused_field
        if field not in ("theme", "vocal_posture"):
            return

        role = self._selected_role()
        values = THEME_VALUES if field == "theme" else VOCAL_POSTURE_VALUES
        current = self.state.get_value(role, field)
        try:
            idx = values.index(current)
        except (ValueError, TypeError):
            idx = 0
        new_value = values[(idx - 1) % len(values)]
        self.state.set_value(role, field, new_value)
        self._do_autosave()
        self._refresh_table()
        self._refresh_detail_panel()
        self._refresh_hero()
        self._update_status()

    def action_edit_numeric(self) -> None:
        if self._guard_active_edit():
            return
        if self._active_panel != "details":
            return
        if self._guard_no_component():
            return

        panel = self.query_one("#detail-panel", ComponentDetailPanel)
        field = panel.focused_field
        if field not in ("groove_density", "energy_level"):
            return

        role = self._selected_role()
        current = self.state.get_value(role, field)
        initial = "" if current is None else f"{current:.4g}"
        self._show_value_edit_input(role=role, field=field, initial_text=initial)

    def action_undo(self) -> None:
        if self._guard_active_edit():
            return
        entry = self.state.undo()
        if entry is None:
            self.app.bell()
            return
        self._do_autosave()
        self._refresh_table()
        self._refresh_detail_panel()
        self._refresh_hero()
        self._update_status()
        self.notify("Undo", timeout=2)

    def action_redo(self) -> None:
        if self._guard_active_edit():
            return
        entry = self.state.redo()
        if entry is None:
            self.app.bell()
            return
        self._do_autosave()
        self._refresh_table()
        self._refresh_detail_panel()
        self._refresh_hero()
        self._update_status()
        self.notify("Redo", timeout=2)

    def action_save(self) -> None:
        if self._guard_active_edit():
            return
        session = self.state.current
        if not session.dirty:
            self.app.bell()
            return

        # 1. Collect dirty edits grouped by component (entry / exit).
        updates_by_role: dict[str, dict[str, Any]] = {"entry": {}, "exit": {}}
        for (role, field), value in session.working.items():
            updates_by_role[role][field] = value

        # 2. Write DB (targeted UPDATE per component, single transaction).
        try:
            with self.db_client.transaction() as conn:
                for role, fields in updates_by_role.items():
                    comp = session.component_for_role(role)
                    if comp is None or comp.id is None or not fields:
                        continue
                    self.db_client.update_song_component_fields_txn(conn, comp.id, fields)
        except Exception as e:  # noqa: BLE001
            self._notify(f"[red]DB save failed: {e}[/]")
            return

        # 3. Write R2 components.json (merge).
        r2_ok = self._save_r2_component_result(session, updates_by_role)

        if not r2_ok:
            session.r2_save_pending = True
            self._do_autosave()
            self._update_status()
            self._refresh_table()
            self._refresh_detail_panel()
            self._refresh_hero()
            self._notify("[yellow]Saved DB only — R2 failed — press s to retry.[/]")
            return

        # 5. Full success -> clear everything.
        session.working.clear()
        session.dirty = False
        session.r2_save_pending = False
        self._reload_components_from_db(session)
        self.state.clear_undo_stacks(session)
        clear_autosave(self.cache_dir, session.hash_prefix)
        self._autosave_ok = True
        self._update_status()
        self._refresh_table()
        self._refresh_detail_panel()
        self._refresh_hero()
        self._notify("[green]Saved (DB + R2).[/]")

    def _save_r2_component_result(
        self, session: SongSession, updates_by_role: dict[str, dict[str, Any]]
    ) -> bool:
        """Merge dirty edits into R2 components.json and upload."""
        hash_prefix = session.hash_prefix
        try:
            payload = self.r2_client.download_component_result(hash_prefix)
        except Exception as e:  # noqa: BLE001
            self._notify(f"[yellow]R2 download failed: {e}[/]")
            return False

        if payload is None:
            payload = {
                "schema_version": COMPONENT_SCHEMA_VERSION,
                "content_hash": first_content_hash(session),
                "hash_prefix": hash_prefix,
                "component_source": "user_review_components",
                "components": [],
            }
            for role in ("entry", "exit"):
                comp = session.component_for_role(role)
                if comp is None:
                    continue
                payload["components"].append(comp.to_dict())

        # C5 fix (soft): stale-revision guard.
        existing_hash = payload.get("content_hash") if isinstance(payload, dict) else None
        current_hash = first_content_hash(session)
        if existing_hash and current_hash and existing_hash != current_hash:
            logger.warning(
                "R2 components.json content_hash=%s mismatches recording content_hash=%s "
                "for hash_prefix=%s; saving with merged values regardless.",
                existing_hash,
                current_hash,
                hash_prefix,
            )

        components = payload.get("components", [])
        for comp_dict in components:
            role = comp_dict.get("role")
            if role not in ("entry", "exit"):
                continue
            fields = updates_by_role.get(role, {})
            for field, value in fields.items():
                comp_dict[field] = value

        try:
            self.r2_client.upload_component_result(hash_prefix, payload)
        except Exception as e:  # noqa: BLE001
            self._notify(f"[yellow]R2 upload failed: {e}[/]")
            return False

        return True

    def _reload_components_from_db(self, session: SongSession) -> None:
        """Replace session.entry_component / exit_component with refreshed
        SongComponent objects reflecting the just-persisted DB values.
        """
        entry, exit_comp = self.db_client.get_song_components_entry_exit(session.song_id)
        session.entry_component = entry
        session.exit_component = exit_comp

    def action_show_keymap(self) -> None:
        if self._is_edit_active():
            return

        from textual.screen import ModalScreen

        class KeymapDialog(ModalScreen[None]):
            BINDINGS: ClassVar[list[Binding]] = [
                Binding("escape", "close", "Close"),
                Binding("?", "close", "Close"),
            ]

            def __init__(self, groups: dict[str, list[str]], all_bindings: list[Binding]) -> None:
                super().__init__()
                self._groups = groups
                self._all_bindings = all_bindings

            def compose(self) -> ComposeResult:
                binding_map: dict[str, Binding] = {b.action: b for b in self._all_bindings}
                with Vertical(id="keymap-container"):
                    yield Label("Keymap", classes="dialog-title")
                    for group_label, action_names in self._groups.items():
                        yield Label(f"[bold]{group_label}[/bold]")
                        for action_name in action_names:
                            b = binding_map.get(action_name)
                            if b is None:
                                continue
                            yield Label(f"[dim]{format_key_display(b.key)}[/dim]={b.description}")
                        yield Label("")
                    yield Label("[d]Press [bold]?[/bold] or [bold]Esc[/bold] to close[/]")

            def action_close(self) -> None:
                self.dismiss(None)

        self.app.push_screen(KeymapDialog(self.BINDING_GROUPS, self.BINDINGS))

    def action_quit_editor(self) -> None:
        if self._is_edit_active():
            self._cancel_row_edit()
            return

        from textual.screen import ModalScreen

        class QuitConfirmDialog(ModalScreen[bool]):
            BINDINGS: ClassVar[list[Binding]] = [
                Binding("y", "confirm", "Yes"),
                Binding("n", "cancel", "No"),
                Binding("escape", "cancel", "No"),
            ]

            def __init__(self, is_dirty: bool):
                super().__init__()
                self.is_dirty = is_dirty

            def compose(self) -> ComposeResult:
                with Vertical():
                    if self.is_dirty:
                        yield Label("[bold yellow]Unsaved changes exist![/bold yellow]")
                        yield Label("Autosave has been updated. Quit anyway?")
                    else:
                        yield Label("Quit the editor?")
                    yield Label("[d]Press [bold]y[/bold] to quit | [bold]n[/bold] to return[/]")

            def action_confirm(self) -> None:
                self.dismiss(True)

            def action_cancel(self) -> None:
                self.dismiss(False)

        def _handle_quit_confirm(should_quit: bool) -> None:
            if should_quit:
                self.app.exit()

        if self.state.current.dirty:
            self._do_autosave()

        self.app.push_screen(QuitConfirmDialog(self.state.current.dirty), _handle_quit_confirm)

    # --- Notify helper ---

    def _notify(self, message: str) -> None:
        try:
            self.notify(message, timeout=3)
        except Exception:  # noqa: BLE001 S110
            pass
