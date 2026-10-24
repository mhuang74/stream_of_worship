"""Main editor screen for the admin LRC editor.

Provides the interactive LRC editing interface with playback controls,
line table, editing panel, and save/upload flow.
"""

import asyncio
import logging
import threading
import time
from pathlib import Path
from typing import Callable, Literal, Optional

from textual import events, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.coordinate import Coordinate
from textual.css.query import NoMatches
from textual.geometry import Offset
from textual.screen import ModalScreen, Screen
from textual.widgets import (
    DataTable,
    Header,
    Input,
    Label,
    Static,
)

from stream_of_worship.admin.audit import log_change
from stream_of_worship.admin.db.client import DatabaseClient
from stream_of_worship.admin.editor.autosave import (
    AutosaveState,
    clear_autosave,
    save_autosave,
)
from stream_of_worship.admin.editor.footer import GroupedFooter, format_key_display
from stream_of_worship.admin.editor.regenerate import RegenerateResult, regenerate_lrc_for_editor
from stream_of_worship.admin.editor.state import EditorState
from stream_of_worship.admin.editor.upload import (
    UploadResult,
    check_active_lrc_job,
    check_transcribed_changed,
    finalize_upload,
    save_local_draft,
    upload_revised_lrc,
)
from stream_of_worship.admin.editor.validation import ValidationResult, validate_lrc
from stream_of_worship.admin.services.analysis import AnalysisClient
from stream_of_worship.admin.services.lrc_parser import (
    format_centiseconds,
    format_duration,
)
from stream_of_worship.admin.services.playback import PlaybackService, PlaybackState
from stream_of_worship.admin.services.r2 import R2Client, R2ObjectIdentity
from stream_of_worship.admin.services.visibility import visibility_label

logger = logging.getLogger(__name__)


class CurrentLyricDisplay(Static):
    """Display showing the current and next lyric prominently."""

    def __init__(self):
        super().__init__("")
        self._current_text = ""
        self._next_text = ""

    def update_lyrics(self, current: str, next_line: str = "") -> None:
        self._current_text = current
        self._next_text = next_line
        self.update(
            f"[bold white on blue] {current} [/]\n[dim]{next_line}[/]"
            if next_line
            else f"[bold white on blue] {current} [/]"
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


class PreviewBanner(Static):
    """Prominent banner shown during preview mode with exit instructions."""

    def __init__(self):
        super().__init__("")
        self.visible = False

    def show_banner(self) -> None:
        self.visible = True
        self.update("[bold white on red] PREVIEW MODE — Press P or ESC to exit preview [/]")

    def hide_banner(self) -> None:
        self.visible = False
        self.update("")


class StatusIndicator(Static):
    """Recovery/draft/upload status indicator plus the feedback summary."""

    def __init__(self):
        super().__init__("")
        self._dirty = False
        self._autosave_ok = False
        self._source = ""
        self._feedback_summary = ""
        self._preview_active = False
        self._padding_offset = 0.0
        self._padding_quarters = 0
        self._activity = ""

    def set_feedback_summary(self, text: str) -> None:
        """Set the trailing feedback segment ("" = omitted) and re-render."""
        self._feedback_summary = text
        self._render_status()

    def set_activity(self, text: str) -> None:
        """Set the trailing activity segment ("" = omitted) and re-render.

        Survives ``update_status`` calls so edits made during a save/upload do
        not erase the in-progress indicator.
        """
        self._activity = text
        self._render_status()

    def update_status(
        self,
        dirty: bool,
        autosave_ok: bool,
        source: str,
        padding_offset: float = 0.0,
        padding_quarters: int = 0,
        preview_active: bool = False,
    ) -> None:
        self._dirty = dirty
        self._autosave_ok = autosave_ok
        self._source = source
        self._preview_active = preview_active
        self._padding_offset = padding_offset
        self._padding_quarters = padding_quarters
        self._render_status()

    def _render_status(self) -> None:
        changed_text = "[yellow]Changed[/yellow]" if self._dirty else "[green]No Changes[/green]"
        autosave_mark = "[green]saved[/green]" if self._autosave_ok else "[dim]—[/dim]"
        source_label = {"r2": "R2", "catalog": "Catalog"}.get(self._source, self._source)

        parts = [f" {changed_text} | Autosave: {autosave_mark} | Source: {source_label}"]
        if self._preview_active:
            parts.append(" | PREVIEW")
        if self._padding_quarters != 0:
            parts.append(f" | Pad: {self._padding_offset:+.2f}s ({self._padding_quarters:+d}q)")
        if self._feedback_summary:
            parts.append(f" ·{self._feedback_summary}")
        if self._activity:
            parts.append(f" | [yellow]{self._activity}[/yellow]")
        self.update("".join(parts))


class _FeedbackSummary:
    """Builds the one-line feedback summary rendered in ``StatusIndicator``.

    Reads rows via ``screen.db_client``; never raises — callers feed the
    returned text to ``StatusIndicator.set_feedback_summary``.
    """

    @staticmethod
    def summary_for(screen) -> str:
        if (
            not getattr(screen, "recording_content_hash", None)
            or screen.db_client is None
        ):
            return " Feedback: unavailable"
        try:
            with screen._db_lock:
                rows = screen.db_client.get_recording_feedback_rows(screen.recording_content_hash)
        except Exception as e:  # never crash the editor on feedback errors
            logger.warning(f"Feedback bar refresh failed: {e}")
            return " Feedback: unavailable"
        if not rows:
            return " Feedback: none yet (f to add yours)"
        happy = sum(1 for r in rows if r.rating == "happy")
        sad = len(rows) - happy
        open_count = sum(1 for r in rows if r.resolved_at is None)
        parts = [f" Feedback: 👍{happy} 👎{sad} · open {open_count}"]
        latest = rows[0]
        if latest.note:
            snippet = latest.note[:40] + ("…" if len(latest.note) > 40 else "")
            parts.append(f' · "{snippet}"')
        if latest.created_at:
            parts.append(f" ({latest.email}, {latest.created_at[:10]})")
        else:
            parts.append(f" ({latest.email})")
        return "".join(parts)


_SAD_REASONS: dict[str, str] = {
    "1": "missing",
    "2": "timing",
    "3": "wrong_text",
    "4": "other",
}


def _format_feedback_row(row) -> str:
    """Format one feedback row for display in the bar/dialog."""
    icon = "👍" if row.rating == "happy" else "👎"
    reason = f" · {row.reason}" if row.reason else ""
    status = " (resolved)" if row.resolved_at else ""
    note = ""
    if row.note:
        snippet = row.note[:40] + ("…" if len(row.note) > 40 else "")
        note = f' — "{snippet}"'
    date = (row.created_at or "")[:10]
    return f"{icon} {row.email}{reason}{note}{status} · {date}"


class FeedbackDialog(ModalScreen):
    """Lyrics feedback dialog: view rows, add Happy/Sad, retract, resolve.

    Result protocol (delivered to ``push_screen`` callback):
    - ``("happy", None, None)`` / ``("sad", reason, note)``: upsert.
    - ``"retract"``: delete the reviewer's own row.
    - ``"resolve_all"``: resolve all open rows on the recording.
    - ``None``: cancelled.
    """

    BINDINGS = [
        Binding("h", "happy", "Happy"),
        Binding("s", "sad", "Sad"),
        Binding("r", "retract", "Remove mine"),
        Binding("x", "resolve_all", "Resolve all open"),
        Binding("c", "cancel", "Cancel"),
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(self, rows, feedback_user_id: Optional[int]):
        super().__init__()
        self._rows = rows
        self._feedback_user_id = feedback_user_id

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog-container"):
            yield Label("Lyrics Feedback", classes="dialog-title")
            if self._rows:
                for row in self._rows:
                    yield Label(_format_feedback_row(row))
            else:
                yield Label("[dim]No feedback on this recording yet.[/dim]")
            help_parts = ["[bold]h[/bold] Happy", "[bold]s[/bold] Sad"]
            if self._feedback_user_id is not None:
                help_parts.append("[bold]r[/bold] Remove mine")
            if any(row.resolved_at is None for row in self._rows):
                help_parts.append("[bold]x[/bold] Resolve all open")
            help_parts.append("[bold]c[/bold] Cancel")
            yield Label("[d]" + " · ".join(help_parts) + "[/]")

    def action_happy(self) -> None:
        self.dismiss(("happy", None, None))

    def action_sad(self) -> None:
        def _sad_done(sad_result) -> None:
            if sad_result is not None:
                self.dismiss(sad_result)

        self.app.push_screen(SadFeedbackDialog(), _sad_done)

    def action_retract(self) -> None:
        if self._feedback_user_id is None:
            self.notify(
                "Feedback disabled: set admin.feedback_email "
                "(sow-admin config set admin.feedback_email <email>)",
                timeout=5,
            )
            return
        self.dismiss("retract")

    def action_resolve_all(self) -> None:
        if not any(row.resolved_at is None for row in self._rows):
            return
        self.dismiss("resolve_all")

    def action_cancel(self) -> None:
        self.dismiss(None)


class SadFeedbackDialog(ModalScreen):
    """Sad feedback step: pick a reason, then an optional note.

    Result: ``("sad", reason, note_or_None)`` or ``None`` (cancelled).
    """

    AUTO_FOCUS = ""  # reason keys (1-4) must fire before the note focuses

    BINDINGS = [
        Binding("1", "reason_1", "missing"),
        Binding("2", "reason_2", "timing"),
        Binding("3", "reason_3", "wrong_text"),
        Binding("4", "reason_4", "other"),
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(self):
        super().__init__()
        self._reason: Optional[str] = None

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog-container"):
            yield Label("Sad Feedback", classes="dialog-title")
            yield Label(
                "[bold]1[/bold] missing  [bold]2[/bold] timing  [bold]3[/bold] wrong_text  [bold]4[/bold] other"
            )
            yield Input(
                id="sad-note-input",
                placeholder="Optional note (Enter to submit, empty = no note)",
            )
            yield Label("[d]Pick a reason, then Enter submits · Esc cancels[/]")

    def _choose_reason(self, reason: str) -> None:
        self._reason = reason
        self.query_one("#sad-note-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "sad-note-input":
            return
        event.stop()
        if self._reason is None:
            self.notify("Pick a reason first (1-4)", severity="warning", timeout=3)
            return
        self._submit(self._reason)

    def _submit(self, reason: str) -> None:
        note = self.query_one("#sad-note-input", Input).value.strip()
        if len(note) > 2000:
            self.notify("Note must be at most 2000 characters", severity="warning", timeout=3)
            return
        self.dismiss(("sad", reason, note or None))

    def action_reason_1(self) -> None:
        self._choose_reason(_SAD_REASONS["1"])

    def action_reason_2(self) -> None:
        self._choose_reason(_SAD_REASONS["2"])

    def action_reason_3(self) -> None:
        self._choose_reason(_SAD_REASONS["3"])

    def action_reason_4(self) -> None:
        self._choose_reason(_SAD_REASONS["4"])

    def action_cancel(self) -> None:
        self.dismiss(None)


class LyricLineTable(DataTable):
    """Lyrics table with preview-aware row navigation."""

    def action_cursor_up(self) -> None:
        guard_edit = getattr(self.screen, "_guard_active_edit", None)
        if guard_edit is not None and guard_edit():
            return
        guard_preview = getattr(self.screen, "_guard_preview", None)
        if guard_preview is not None and guard_preview():
            return
        super().action_cursor_up()

    def action_cursor_down(self) -> None:
        guard_edit = getattr(self.screen, "_guard_active_edit", None)
        if guard_edit is not None and guard_edit():
            return
        guard_preview = getattr(self.screen, "_guard_preview", None)
        if guard_preview is not None and guard_preview():
            return
        super().action_cursor_down()

    def action_page_up(self) -> None:
        guard_edit = getattr(self.screen, "_guard_active_edit", None)
        if guard_edit is not None and guard_edit():
            return
        guard_preview = getattr(self.screen, "_guard_preview", None)
        if guard_preview is not None and guard_preview():
            return
        self.scroll_page_up(animate=False, force=True)

    def action_page_down(self) -> None:
        guard_edit = getattr(self.screen, "_guard_active_edit", None)
        if guard_edit is not None and guard_edit():
            return
        guard_preview = getattr(self.screen, "_guard_preview", None)
        if guard_preview is not None and guard_preview():
            return
        self.scroll_page_down(animate=False, force=True)


class LRCEditorScreen(Screen[None]):
    """Main interactive LRC editor screen.

    Provides:
    - Main lyrics preview area showing current/next lyric
    - Playback/progress display
    - Line table with timestamps, text, highlights, warnings
    - Selected-line editing panel
    - Recovery/draft/upload status indicator
    - Footer with keyboard shortcuts
    """

    DEFAULT_CSS = """
    #editor-body {
        height: 1fr;
        overflow: hidden;
    }

    #line-table {
        height: 1fr;
    }

    #row-edit-input {
        display: none;
        height: 1;
        layer: overlay;
    }
    """

    BINDINGS = [
        # Playback/Nav
        Binding("space", "toggle_playback", "Play/Pause"),
        Binding("left", "seek_backward", "Seek -5s"),
        Binding("right", "seek_forward", "Seek +5s"),
        Binding("j", "jump_to_line", "Jump"),
        # Lyrics Edit
        Binding("ctrl+c", "copy_line", "Copy"),
        Binding("ctrl+v", "paste_after", "Paste"),
        Binding("shift+up", "extend_selection_up", "Select Up"),
        Binding("shift+down", "extend_selection_down", "Select Down"),
        Binding("i", "insert_after", "Insert Blank"),
        Binding("I", "insert_canonical", "Insert Canonical"),
        Binding("d", "delete_line", "Delete"),
        Binding("e", "edit_text", "Edit Text"),
        # Timecode
        Binding("tab", "stamp_and_advance", "Stamp+Advance"),
        Binding("shift+left", "show_earlier", "Earlier"),
        Binding("shift+right", "show_later", "Later"),
        Binding("t", "edit_timestamp", "Edit Time"),
        # General
        Binding("p", "preview_single", "Preview Line"),
        Binding("P", "preview_continuous", "Preview All"),
        Binding("f", "feedback", "Feedback"),
        Binding("s", "save_upload", "Save/Upload"),
        Binding("G", "regenerate_lrc", "Regenerate LRC"),
        Binding("L", "reload_lrc", "Reload from R2"),
        Binding("ctrl+z", "undo", "Undo"),
        Binding("ctrl+y", "redo", "Redo"),
        Binding("escape", "quit_editor", "Quit"),
        Binding("q", "quit_editor", "Quit"),
        Binding("?", "show_keymap", "Keymap"),
    ]

    BINDING_GROUPS: dict[str, list[str]] = {
        "Playback": [
            "toggle_playback",
            "seek_backward",
            "seek_forward",
            "jump_to_line",
        ],
        "Lyrics": [
            "copy_line",
            "paste_after",
            "extend_selection_up",
            "extend_selection_down",
            "insert_after",
            "insert_canonical",
            "delete_line",
            "edit_text",
        ],
        "Timecode": [
            "stamp_and_advance",
            "show_earlier",
            "show_later",
            "edit_timestamp",
        ],
        "General": [
            "preview_single",
            "preview_continuous",
            "feedback",
            "save_upload",
            "regenerate_lrc",
            "reload_lrc",
            "undo",
            "redo",
            "quit_editor",
            "show_keymap",
        ],
    }

    def __init__(
        self,
        editor_state: EditorState,
        playback_service: PlaybackService,
        cache_dir: Path,
        r2_client: R2Client,
        db_client: DatabaseClient,
        hash_prefix: str,
        original_transcribed_content: Optional[str],
        upload_result_out: Optional[Callable[[UploadResult], None]] = None,
        feedback_user_id: Optional[int] = None,
        recording_content_hash: Optional[str] = None,
        visibility_status: Optional[str] = None,
        analysis_client: Optional[AnalysisClient] = None,
    ):
        super().__init__()
        self.title = (
            f"LRC Editor — {editor_state.song_title} ({editor_state.song_id})"
            if editor_state.song_id
            else f"LRC Editor — {editor_state.song_title}"
        )
        # Plain text (no Rich markup): Header.format_title renders Content.
        self.sub_title = f"Visibility: {visibility_label(visibility_status)}"
        self.state = editor_state
        self.playback = playback_service
        self.cache_dir = cache_dir
        self.r2_client = r2_client
        self.db_client = db_client
        self.hash_prefix = hash_prefix
        self.original_transcribed_content = original_transcribed_content
        self.upload_result_out = upload_result_out
        # Reviewer identity + full recording hash for the feedback hotkey;
        # None disables submitting (the bar/dialog still display rows).
        self.feedback_user_id = feedback_user_id
        self.recording_content_hash = recording_content_hash
        self._autosave_ok = False
        self._edit_mode: Literal["text", "timestamp"] | None = None
        self._edit_target_row: int | None = None
        self._edit_target_column: int | None = None
        self._position_update_timer: Optional[asyncio.Task] = None
        self._selection_anchor: int | None = None
        self._selection_end: int | None = None
        self._suppress_range_clear = False
        self._last_paste_payload: str | None = None
        self._last_paste_time: float = 0.0
        self._preview_active: bool = False
        self._preview_mode: str = "single"
        self._preview_target_index: int = -1
        self._preview_prev_index: int = -1
        self._preview_display_index: Optional[int] = None
        self._preview_end_seconds: float = 0.0
        self._save_in_flight: bool = False
        # In-editor force-regeneration (hotkey G): None disables the hotkey.
        self.analysis_client = analysis_client
        self._regen_in_flight: bool = False
        self._regen_thread: Optional[threading.Thread] = None
        self._regen_snapshot: str = ""
        # Hotkey L: reload the R2 LRC without regenerating.
        self._reload_in_flight: bool = False
        self._reload_thread: Optional[threading.Thread] = None
        self._reload_snapshot: str = ""
        # psycopg connections are not safe for concurrent use: the upload worker
        # shares db_client.connection with the main-thread feedback paths.
        self._db_lock = threading.RLock()

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="editor-body"):
            yield PreviewBanner()
            yield CurrentLyricDisplay()
            yield PlaybackBar()
            yield LyricLineTable(id="line-table")
            yield Input(
                id="row-edit-input",
                placeholder="Edit selected cell",
                select_on_focus=False,
                compact=True,
            )
            yield StatusIndicator()
        yield GroupedFooter()

    def on_mount(self) -> None:
        self._setup_table()
        self._rebuild_table()
        self.query_one("#line-table", DataTable).focus()
        self._update_displays()
        self.query_one(StatusIndicator).set_feedback_summary(
            _FeedbackSummary.summary_for(self)
        )
        self._start_position_updates()

        self.playback.set_callbacks(
            on_position_changed=self._on_playback_position,
            on_state_changed=self._on_playback_state,
            on_finished=self._on_playback_finished,
        )

        if self.state.audio_path:
            self.playback.load(Path(self.state.audio_path))

    def on_unmount(self) -> None:
        self.playback.stop()
        self.playback.set_callbacks()
        if self._position_update_timer:
            self._position_update_timer.cancel()

    def _setup_table(self) -> None:
        table = self.query_one("#line-table", DataTable)
        table.add_columns("#", "Time", "Text", "Status")
        table.cursor_type = "row"
        table.show_cursor = True

    def _rebuild_table(self) -> None:
        table = self.query_one("#line-table", DataTable)
        current_count = table.row_count
        target_count = self.state.line_count

        for i in range(min(current_count, target_count)):
            line = self.state.timed_lines[i]
            ts = format_centiseconds(line.time_seconds)
            status = self._row_status(i)
            row_label = self._row_label(i)
            for column, value in enumerate((row_label, ts, line.text, status)):
                table.update_cell_at(Coordinate(i, column), value, update_width=True)

        if target_count > current_count:
            for i in range(current_count, target_count):
                line = self.state.timed_lines[i]
                ts = format_centiseconds(line.time_seconds)
                status = self._row_status(i)
                row_label = self._row_label(i)
                table.add_row(row_label, ts, line.text, status, key=str(i))
        elif current_count > target_count:
            for i in range(current_count - 1, target_count - 1, -1):
                table.remove_row(str(i))

        if 0 <= self.state.selected_index < target_count:
            table.move_cursor(row=self.state.selected_index, scroll=True)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.data_table.id != "line-table":
            return
        old_index = self.state.selected_index
        if event.cursor_row == self.state.selected_index:
            if self._suppress_range_clear:
                self._suppress_range_clear = False
            self._update_displays()
            return
        if self._suppress_range_clear:
            self._suppress_range_clear = False
        else:
            self._clear_selection_range(refresh=True)
        self.state.select_line(event.cursor_row)
        self._update_selection_marker(old_index)
        self._update_displays()

    def _row_label(self, index: int) -> str:
        if index == self.state.selected_index:
            return f">{index + 1}"
        active_range = self._active_selection_range()
        if active_range is not None and active_range[0] <= index <= active_range[1]:
            return f"*{index + 1}"
        return str(index + 1)

    def _row_status(self, index: int) -> str:
        line = self.state.timed_lines[index]
        if line.time_seconds == 0.0 and line.text.strip():
            return "[dim]draft[/dim]"
        if index > 0 and line.time_seconds < self.state.timed_lines[index - 1].time_seconds:
            return "[red]!non-mono[/red]"
        return ""

    def _active_selection_range(self) -> tuple[int, int] | None:
        if self._selection_anchor is None or self._selection_end is None:
            return None
        if self._selection_anchor == self._selection_end:
            return None
        start = max(0, min(self._selection_anchor, self._selection_end))
        end = min(self.state.line_count - 1, max(self._selection_anchor, self._selection_end))
        if start > end:
            return None
        return start, end

    def _copy_selection_range(self) -> tuple[int, int]:
        active_range = self._active_selection_range()
        if active_range is not None:
            return active_range
        index = self.state.selected_index
        return index, index

    def _refresh_range_rows(self, old_range: tuple[int, int] | None) -> None:
        rows: set[int] = {self.state.selected_index}
        new_range = self._active_selection_range()
        for row_range in (old_range, new_range):
            if row_range is None:
                continue
            rows.update(range(row_range[0], row_range[1] + 1))
        for index in rows:
            self._update_table_row(index)

    def _clear_selection_range(self, refresh: bool = True) -> None:
        old_range = self._active_selection_range()
        self._selection_anchor = None
        self._selection_end = None
        if refresh:
            self._refresh_range_rows(old_range)

    def _parse_pasted_text(self, text: str) -> tuple[list[str], int]:
        rows: list[str] = []
        dropped_blank_count = 0
        for line in text.splitlines():
            stripped = line.strip()
            if stripped:
                rows.append(stripped)
            else:
                dropped_blank_count += 1
        return rows, dropped_blank_count

    def _format_copied_rows(self, start: int, end: int) -> str:
        return "\n".join(line.text for line in self.state.timed_lines[start : end + 1])

    def _update_table_row(self, index: int) -> None:
        if not 0 <= index < self.state.line_count:
            return
        try:
            table = self.query_one("#line-table", DataTable)
        except NoMatches:
            return
        if not 0 <= index < table.row_count:
            return

        line = self.state.timed_lines[index]
        values = (
            self._row_label(index),
            format_centiseconds(line.time_seconds),
            line.text,
            self._row_status(index),
        )
        for column, value in enumerate(values):
            table.update_cell_at(Coordinate(index, column), value, update_width=True)

    def _update_selection_marker(self, old_index: int | None = None) -> None:
        self._update_table_row(self.state.selected_index)
        if old_index is not None and old_index != self.state.selected_index:
            self._update_table_row(old_index)

    def _move_table_cursor_to_selection(self, old_index: int | None = None) -> None:
        try:
            table = self.query_one("#line-table", DataTable)
        except NoMatches:
            return
        if 0 <= self.state.selected_index < self.state.line_count:
            table.move_cursor(row=self.state.selected_index, scroll=True)
        self._update_selection_marker(old_index)

    def _sync_selection_from_table_cursor(self) -> None:
        try:
            table = self.query_one("#line-table", DataTable)
        except NoMatches:
            return

        cursor_row = table.cursor_row
        if cursor_row is None or not 0 <= cursor_row < self.state.line_count:
            return
        if cursor_row == self.state.selected_index:
            return

        old_index = self.state.selected_index
        self.state.select_line(cursor_row)
        self._update_selection_marker(old_index)
        self._update_displays()

    def _update_displays(self) -> None:
        lyric_display = self.query_one(CurrentLyricDisplay)
        if self._preview_active and self._preview_display_index == -1:
            next_line = self.state.timed_lines[0].text if self.state.line_count > 0 else ""
            lyric_display.update_lyrics("", next_line)
        else:
            current = self.state.selected_line
            if current:
                next_idx = self.state.selected_index + 1
                next_line = ""
                if next_idx < self.state.line_count:
                    next_line = self.state.timed_lines[next_idx].text
                lyric_display.update_lyrics(current.text, next_line)

        status = self.query_one(StatusIndicator)
        status.update_status(
            self.state.dirty,
            self._autosave_ok,
            self.state.source_mode,
            padding_offset=self.state.padding_offset_seconds,
            padding_quarters=self.state.padding_quarters,
            preview_active=self._preview_active,
        )

    def _set_preview_display_index(self, index: int) -> None:
        """Update the lyric banner for preview, where -1 means blank before line 1."""
        if index == self._preview_display_index:
            return

        self._preview_display_index = index
        if index >= 0:
            if self.state.selected_index != index:
                self.state.select_line(index)
                self._rebuild_table()
        else:
            self._rebuild_table()
        self._update_displays()

    def _update_playback_bar(self) -> None:
        try:
            bar = self.query_one(PlaybackBar)
        except NoMatches:
            return
        pos = self.playback.position_seconds
        dur = self.playback.duration_seconds
        bar.update_playback(pos, dur, self.playback.state)

    def _start_position_updates(self) -> None:
        async def _update_loop():
            while True:
                await asyncio.sleep(0.2)
                self._update_playback_bar()

        self._position_update_timer = asyncio.ensure_future(_update_loop())

    def _on_playback_position(self, position) -> None:
        if not self._preview_active:
            return

        current_secs = position.current_seconds

        if self._preview_mode == "single":
            target_line = self.state.timed_lines[self._preview_target_index]
            if current_secs >= self._preview_end_seconds:
                self._stop_preview()
                return
            if current_secs >= target_line.time_seconds:
                self._set_preview_display_index(self._preview_target_index)
            elif self._preview_prev_index >= 0:
                self._set_preview_display_index(self._preview_prev_index)
            else:
                self._set_preview_display_index(-1)
            return

        current_line_idx = self._find_line_at_position(current_secs)
        self._set_preview_display_index(current_line_idx)

    def _on_playback_state(self, new_state: PlaybackState) -> None:
        self._update_playback_bar()

    def _on_playback_finished(self) -> None:
        self._preview_active = False
        self._preview_mode = "single"
        self._preview_target_index = -1
        self._preview_prev_index = -1
        self._preview_display_index = None
        self._preview_end_seconds = 0.0
        try:
            self.query_one(PreviewBanner).hide_banner()
        except NoMatches:
            pass
        self._update_playback_bar()
        self._update_displays()

    def _do_autosave(self) -> None:
        try:
            autosave_state = AutosaveState(
                timed_lines=self.state.timed_lines,
                preserved_lines=self.state.preserved_lines,
                transcribed_identity=self.state.transcribed_identity,
                dirty=self.state.dirty,
                source_mode=self.state.source_mode,
                padding_quarters=self.state.padding_quarters,
                tempo_bpm=self.state.tempo_bpm,
                original_timestamps=self.state.original_timestamps,
                selected_index=self.state.selected_index,
            )
            save_autosave(self.cache_dir, self.hash_prefix, autosave_state)
            self._autosave_ok = True
            self._update_displays()
        except Exception as e:
            logger.warning(f"Autosave failed: {e}")
            self._autosave_ok = False

    def _find_line_at_position(self, position: float) -> int:
        """Find the lyric line index for the current playback position."""
        for i in range(len(self.state.timed_lines) - 1, -1, -1):
            if self.state.timed_lines[i].time_seconds <= position:
                return i
        return -1

    # --- Preview helpers ---

    def _stop_preview(self) -> None:
        self.playback.pause()
        self._preview_active = False
        self._preview_mode = "single"
        self._preview_target_index = -1
        self._preview_prev_index = -1
        self._preview_display_index = None
        self._preview_end_seconds = 0.0
        try:
            self.query_one(PreviewBanner).hide_banner()
        except NoMatches:
            pass
        self._update_displays()

    def _guard_preview(self) -> bool:
        if self._preview_active:
            self.notify("Exit preview first (P or ESC)", severity="warning", timeout=2)
            return True
        return False

    def _is_edit_active(self) -> bool:
        return self._edit_mode is not None

    def _guard_active_edit(self) -> bool:
        if self._is_edit_active():
            self.notify("Finish editing first", severity="warning", timeout=2)
            return True
        return False

    def _hide_row_edit_input(self) -> None:
        try:
            edit_input = self.query_one("#row-edit-input", Input)
        except NoMatches:
            return
        edit_input.value = ""
        edit_input.display = False
        self._edit_mode = None
        self._edit_target_row = None
        self._edit_target_column = None

    def _cancel_row_edit(self) -> None:
        self._hide_row_edit_input()
        self.query_one("#line-table", DataTable).focus()

    def _cell_screen_region(self, table: DataTable, row: int, column: int):
        cell_region = table._get_cell_region(Coordinate(row, column))
        if cell_region.width <= 0 or cell_region.height <= 0:
            return None
        x = table.region.x + cell_region.x - table.scroll_x
        y = table.region.y + cell_region.y - table.scroll_y
        if y < table.region.y or y >= table.region.y + table.region.height:
            return None
        visible_left = max(x, table.region.x)
        visible_right = min(x + cell_region.width, table.region.x + table.region.width)
        width = visible_right - visible_left
        if width <= 0:
            return None
        return visible_left, y, width

    def _show_row_edit_input(self, mode: Literal["text", "timestamp"], column: int) -> None:
        if self._guard_preview():
            return
        table = self.query_one("#line-table", DataTable)
        row = self.state.selected_index
        if not 0 <= row < self.state.line_count:
            self.notify("No row selected", severity="warning", timeout=2)
            return

        table.move_cursor(row=row, column=column, scroll=True)
        table.refresh(layout=True)

        def do_show() -> None:
            resolved = self._cell_screen_region(table, row, column)
            if resolved is None:
                self.notify("Cannot start editing this cell", severity="warning", timeout=2)
                table.focus()
                return

            x, y, width = resolved
            edit_input = self.query_one("#row-edit-input", Input)
            line = self.state.timed_lines[row]

            def reset_edit_viewport() -> None:
                edit_input.cursor_position = 0
                edit_input.set_scroll(0, None)

            edit_input.value = line.text if mode == "text" else format_centiseconds(line.time_seconds)
            reset_edit_viewport()
            edit_input.placeholder = "Edit text" if mode == "text" else "Edit timestamp"
            edit_input.styles.offset = Offset(x, y)
            edit_input.styles.width = max(1, width)
            edit_input.display = True
            self._edit_mode = mode
            self._edit_target_row = row
            self._edit_target_column = column
            edit_input.focus()
            self.app.call_later(reset_edit_viewport)

        self.call_after_refresh(do_show)

    def _refresh_row_edit_input_position(self) -> None:
        if (
            self._edit_mode is None
            or self._edit_target_row is None
            or self._edit_target_column is None
        ):
            return
        table = self.query_one("#line-table", DataTable)
        resolved = self._cell_screen_region(table, self._edit_target_row, self._edit_target_column)
        if resolved is None:
            self._cancel_row_edit()
            self.notify("Editing canceled after layout change", severity="warning", timeout=2)
            return
        x, y, width = resolved
        edit_input = self.query_one("#row-edit-input", Input)
        edit_input.styles.offset = Offset(x, y)
        edit_input.styles.width = max(1, width)

    def _insert_pasted_text(self, payload: str) -> None:
        texts, dropped_blank_count = self._parse_pasted_text(payload)
        if not texts:
            self.notify("Nothing to paste", timeout=2)
            return
        active_range = self._active_selection_range()
        insert_after = active_range[1] if active_range is not None else self.state.selected_index
        self.state.insert_lines_after(insert_after, texts)
        self.state.select_line(insert_after + 1)
        self._clear_selection_range(refresh=False)
        self._rebuild_table()
        self._update_displays()
        self._do_autosave()
        message = f"Inserted {len(texts)} lyric lines"
        if dropped_blank_count:
            message += f" ({dropped_blank_count} blank lines ignored)"
        self.notify(message, timeout=3)

    def _is_duplicate_paste(self, payload: str) -> bool:
        now = time.monotonic()
        duplicate = payload == self._last_paste_payload and now - self._last_paste_time < 0.25
        self._last_paste_payload = payload
        self._last_paste_time = now
        return duplicate

    def on_resize(self, event: events.Resize) -> None:
        self.call_after_refresh(self._refresh_row_edit_input_position)

    def on_paste(self, event: events.Paste) -> None:
        if self._guard_active_edit():
            event.stop()
            return
        if self._guard_preview():
            event.stop()
            return
        if self.app.focused is not self.query_one("#line-table", DataTable):
            return
        payload = event.text
        if not payload or self._is_duplicate_paste(payload):
            event.stop()
            return
        self._insert_pasted_text(payload)
        event.stop()

    # --- Action handlers ---

    def action_toggle_playback(self) -> None:
        if self._guard_active_edit():
            return
        if self._guard_preview():
            return
        self.playback.toggle_play_pause()

    def action_seek_forward(self) -> None:
        if self._guard_active_edit():
            return
        if self._guard_preview():
            return
        self.playback.skip_forward(5.0)

    def action_seek_backward(self) -> None:
        if self._guard_active_edit():
            return
        if self._guard_preview():
            return
        self.playback.skip_backward(5.0)

    def action_select_prev(self) -> None:
        if self._guard_active_edit():
            return
        if self._guard_preview():
            return
        self._clear_selection_range()
        old_index = self.state.selected_index
        self.state.select_prev()
        self._move_table_cursor_to_selection(old_index)
        self._update_displays()

    def action_select_next(self) -> None:
        if self._guard_active_edit():
            return
        if self._guard_preview():
            return
        self._clear_selection_range()
        old_index = self.state.selected_index
        self.state.select_next()
        self._move_table_cursor_to_selection(old_index)
        self._update_displays()

    def action_extend_selection_down(self) -> None:
        if self._guard_active_edit():
            return
        if self._guard_preview():
            return
        if self.state.selected_index >= self.state.line_count - 1:
            return
        old_range = self._active_selection_range()
        old_index = self.state.selected_index
        if self._selection_anchor is None:
            self._selection_anchor = old_index
        self.state.select_next()
        self._selection_end = self.state.selected_index
        self._suppress_range_clear = True
        self._move_table_cursor_to_selection(old_index)
        self._suppress_range_clear = False
        self._refresh_range_rows(old_range)
        self._update_displays()

    def action_extend_selection_up(self) -> None:
        if self._guard_active_edit():
            return
        if self._guard_preview():
            return
        if self.state.selected_index <= 0:
            return
        old_range = self._active_selection_range()
        old_index = self.state.selected_index
        if self._selection_anchor is None:
            self._selection_anchor = old_index
        self.state.select_prev()
        self._selection_end = self.state.selected_index
        self._suppress_range_clear = True
        self._move_table_cursor_to_selection(old_index)
        self._suppress_range_clear = False
        self._refresh_range_rows(old_range)
        self._update_displays()

    def action_jump_to_line(self) -> None:
        if self._guard_active_edit():
            return
        if self._guard_preview():
            return
        line = self.state.selected_line
        if line:
            self.playback.seek(line.time_seconds)

    def action_stamp_and_advance(self) -> None:
        if self._guard_active_edit():
            return
        if self._guard_preview():
            return
        old_index = self.state.selected_index
        pos = self.playback.position_seconds
        self.state.set_timestamp(old_index, pos)
        self.state.select_next()
        self._update_table_row(old_index)
        self._update_table_row(old_index + 1)
        self._move_table_cursor_to_selection(old_index)
        self._update_displays()
        self._do_autosave()

    def action_show_earlier(self) -> None:
        if self._guard_active_edit():
            return
        if self._guard_preview():
            return
        if not self.state.adjust_padding(-1):
            self.notify(
                f"Padding limit reached: {self.state.padding_quarters:+d}q "
                f"({self.state.padding_offset_seconds:+.2f}s)",
                severity="warning",
                timeout=2,
            )
            return
        self._rebuild_table()
        self._update_displays()
        self._do_autosave()
        offset = self.state.padding_offset_seconds
        quarters = self.state.padding_quarters
        self.notify(f"Padding: {offset:+.2f}s ({quarters:+d}q)", timeout=2)

    def action_show_later(self) -> None:
        if self._guard_active_edit():
            return
        if self._guard_preview():
            return
        if not self.state.adjust_padding(1):
            self.notify(
                f"Padding limit reached: {self.state.padding_quarters:+d}q "
                f"({self.state.padding_offset_seconds:+.2f}s)",
                severity="warning",
                timeout=2,
            )
            return
        self._rebuild_table()
        self._update_displays()
        self._do_autosave()
        offset = self.state.padding_offset_seconds
        quarters = self.state.padding_quarters
        self.notify(f"Padding: {offset:+.2f}s ({quarters:+d}q)", timeout=2)

    def action_preview_single(self) -> None:
        if self._guard_active_edit():
            return
        if self._preview_active:
            self._stop_preview()
            return

        self._sync_selection_from_table_cursor()
        target_idx = self.state.selected_index
        line = self.state.selected_line
        if not line or line.time_seconds == 0.0:
            self.notify("No timestamp on current line", severity="warning", timeout=2)
            return

        prev_idx = target_idx - 1 if target_idx > 0 else -1

        end_seconds = self.playback.duration_seconds
        for i in range(target_idx + 1, self.state.line_count):
            if self.state.timed_lines[i].time_seconds > 0.0:
                end_seconds = self.state.timed_lines[i].time_seconds
                break

        start_pos = max(0.0, line.time_seconds - 3.0)
        self._preview_mode = "single"
        self._preview_target_index = target_idx
        self._preview_prev_index = prev_idx
        self._preview_display_index = None
        self._preview_end_seconds = end_seconds
        self._preview_active = True

        self._set_preview_display_index(prev_idx if prev_idx >= 0 else -1)

        self.playback.play(start_seconds=start_pos)
        self.query_one(PreviewBanner).show_banner()
        self._update_displays()

    def action_preview_continuous(self) -> None:
        if self._guard_active_edit():
            return
        if self._preview_active:
            self._stop_preview()
            return

        self._sync_selection_from_table_cursor()
        line = self.state.selected_line
        if not line or line.time_seconds == 0.0:
            self.notify("No timestamp on current line", severity="warning", timeout=2)
            return

        start_pos = max(0.0, line.time_seconds - 3.0)
        self._preview_mode = "continuous"
        self._preview_target_index = self.state.selected_index
        self._preview_prev_index = self._preview_target_index - 1
        self._preview_display_index = None
        self._preview_active = True
        self._set_preview_display_index(self._find_line_at_position(start_pos))
        self.playback.play(start_seconds=start_pos)
        self.query_one(PreviewBanner).show_banner()
        self._update_displays()

    def action_edit_text(self) -> None:
        if self._guard_active_edit():
            return
        self._show_row_edit_input("text", 2)

    def action_edit_timestamp(self) -> None:
        if self._guard_active_edit():
            return
        self._show_row_edit_input("timestamp", 1)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "row-edit-input":
            return

        value = event.value.strip()
        if self._edit_target_row is None or self._edit_mode is None:
            self._cancel_row_edit()
            return

        target_row = self._edit_target_row
        if self._edit_mode == "text":
            self.state.set_text(target_row, value)
        elif self._edit_mode == "timestamp":
            try:
                ts = self._parse_timestamp_input(value)
            except (ValueError, TypeError):
                self.notify("Invalid timestamp", severity="warning", timeout=2)
                return
            self.state.set_timestamp(target_row, ts)

        self._hide_row_edit_input()
        self._rebuild_table()
        self._update_displays()
        self._do_autosave()
        self.query_one("#line-table", DataTable).focus()

    def _parse_timestamp_input(self, value: str) -> float:
        """Parse a timestamp input like [mm:ss.xx] or mm:ss.xx or seconds."""
        value = value.strip("[] ")
        if ":" in value:
            parts = value.split(":")
            if len(parts) == 2:
                minutes = int(parts[0])
                sec_parts = parts[1].split(".")
                seconds = int(sec_parts[0])
                cs = int(sec_parts[1].ljust(2, "0")[:2]) if len(sec_parts) > 1 else 0
                return minutes * 60 + seconds + cs / 100.0
        return max(0.0, float(value))

    def action_insert_after(self) -> None:
        if self._guard_active_edit():
            return
        if self._guard_preview():
            return
        self._clear_selection_range()
        self.state.insert_after(self.state.selected_index)
        self.state.select_next()
        self._rebuild_table()
        self._update_displays()
        self._do_autosave()

    def action_insert_canonical(self) -> None:
        if self._guard_active_edit():
            return
        if self._guard_preview():
            return
        if self._save_in_flight:
            # The upload worker shares db_client.connection; refusing beats blocking
            # the event loop on _db_lock for the length of a DB stage.
            self.notify(
                "Save/upload in progress — try again when it finishes",
                severity="warning",
                timeout=3,
            )
            return
        with self._db_lock:
            recording = self.db_client.get_recording_by_hash(self.hash_prefix)
        if not recording or not recording.song_id:
            self.notify("No song linked", severity="warning", timeout=3)
            return

        with self._db_lock:
            song = self.db_client.get_song(recording.song_id)
        if not song:
            self.notify("No canonical lyrics found", severity="warning", timeout=3)
            return

        lyrics = song.lyrics_list
        non_blank = [
            str(line).strip()
            for line in lyrics
            if str(line).strip() and str(line).strip() != "None"
        ]
        if not non_blank:
            self.notify("No canonical lyrics found", severity="warning", timeout=3)
            return

        active_range = self._active_selection_range()
        insert_after = active_range[1] if active_range is not None else self.state.selected_index
        self.state.insert_lines_after(insert_after, non_blank)
        self.state.select_line(insert_after + 1)
        self._clear_selection_range(refresh=False)
        self._rebuild_table()
        self._update_displays()
        self._do_autosave()
        self.notify(f"Inserted {len(non_blank)} canonical lyrics lines", timeout=3)

    def action_copy_line(self) -> None:
        if self._guard_active_edit():
            return
        if self._guard_preview():
            return
        if self.state.line_count <= 0:
            return
        start, end = self._copy_selection_range()
        self.app.copy_to_clipboard(self._format_copied_rows(start, end))
        count = end - start + 1
        self.notify(f"Copied {count} lyric line{'s' if count != 1 else ''}", timeout=2)

    def action_paste_after(self) -> None:
        if self._guard_active_edit():
            return
        if self._guard_preview():
            return
        payload = self.app.clipboard
        if not payload:
            self.notify("Nothing to paste", timeout=2)
            return
        if self._is_duplicate_paste(payload):
            return
        self._insert_pasted_text(payload)

    def action_delete_line(self) -> None:
        if self._guard_active_edit():
            return
        if self._guard_preview():
            return
        if self.state.line_count <= 1:
            return

        self._clear_selection_range()
        self.state.delete_line(self.state.selected_index)
        self._rebuild_table()
        self._update_displays()
        self._do_autosave()

    def action_undo(self) -> None:
        if self._guard_active_edit():
            return
        if self._guard_preview():
            return
        if self.state.undo():
            self._rebuild_table()
            self._update_displays()
            self._do_autosave()
            self.notify("Undo", timeout=2)

    def action_redo(self) -> None:
        if self._guard_active_edit():
            return
        if self._guard_preview():
            return
        if self.state.redo():
            self._rebuild_table()
            self._update_displays()
            self._do_autosave()
            self.notify("Redo", timeout=2)

    def _has_unsaved_changes(self) -> bool:
        return self.state.dirty or self.state.serialize() != self.state.original_serialized

    def action_save_upload(self) -> None:
        if self._guard_active_edit():
            return
        if self._guard_preview():
            return
        revised = self.state.serialize()
        if not self._has_unsaved_changes():
            self.notify("No changes to save", timeout=3)
            return
        result = validate_lrc(
            timed_lines=self.state.timed_lines,
            preserved_lines=self.state.preserved_lines,
            original_serialized=self.state.original_serialized,
            audio_duration_seconds=self.state.audio_duration,
            original_preserved_lines=self.state.original_preserved_lines,
        )

        if self._save_in_flight:
            self.notify("Save/upload already in progress", severity="warning", timeout=3)
            return
        if self._regen_in_flight:
            self.notify(
                "Regeneration in progress — wait for it to finish",
                severity="warning",
                timeout=3,
            )
            return

        # The R2 conflict check is blocking network I/O: run it off the event loop
        # so playback/navigation keys keep responding (see _check_r2_worker).
        identity = self.state.transcribed_identity
        self._save_in_flight = True
        self.query_one(StatusIndicator).set_activity("Checking R2…")
        self._check_r2_worker(result, revised, identity)

    def action_feedback(self) -> None:
        """Open the lyrics feedback dialog (hotkey f)."""
        if self._guard_active_edit():
            return
        if self._guard_preview():
            return
        if self._save_in_flight:
            self.notify(
                "Save/upload in progress — try again when it finishes",
                severity="warning",
                timeout=3,
            )
            return
        if self._regen_in_flight:
            self.notify(
                "Regeneration in progress — wait for it to finish",
                severity="warning",
                timeout=3,
            )
            return
        if self.feedback_user_id is None:
            self.notify(
                "Feedback disabled: set admin.feedback_email "
                "(sow-admin config set admin.feedback_email <email>)",
                timeout=5,
            )
            return
        if self.recording_content_hash is None:
            self.notify("Feedback unavailable: no recording hash", severity="warning", timeout=5)
            return

        try:
            with self._db_lock:
                rows = self.db_client.get_recording_feedback_rows(self.recording_content_hash)
        except Exception as e:
            self.query_one(StatusIndicator).update(f" [red]Feedback load failed: {e}[/red]")
            return
        self.app.push_screen(
            FeedbackDialog(rows, self.feedback_user_id),
            self._handle_feedback_submission,
        )

    def _handle_feedback_submission(self, result) -> None:
        """Apply a FeedbackDialog result; never crash the editor on errors."""
        if result is None:
            return
        status = self.query_one(StatusIndicator)

        if result == "retract":
            try:
                with self._db_lock:
                    self.db_client.delete_recording_feedback(
                        self.recording_content_hash, self.feedback_user_id
                    )
                    status.set_feedback_summary(_FeedbackSummary.summary_for(self))
                log_change(
                    "feedback_clear",
                    song_id=self.state.song_id or None,
                    hash_prefix=self.hash_prefix,
                    mode="retract",
                    rows=1,
                )
                self.notify("Your feedback removed", timeout=3)
            except Exception as e:
                status.update(f" [red]Feedback delete failed: {e}[/red]")
            return

        if result == "resolve_all":
            try:
                with self._db_lock:
                    resolved = self.db_client.resolve_recording_feedback(
                        self.recording_content_hash
                    )
                    status.set_feedback_summary(_FeedbackSummary.summary_for(self))
                log_change(
                    "feedback_clear",
                    song_id=self.state.song_id or None,
                    hash_prefix=self.hash_prefix,
                    mode="resolve_all",
                    rows=resolved,
                )
                self.notify(f"Resolved {resolved} open row(s)", timeout=3)
            except Exception as e:
                status.update(f" [red]Feedback resolve failed: {e}[/red]")
            return

        rating, reason, note = result
        try:
            with self._db_lock:
                self.db_client.upsert_recording_feedback(
                    self.recording_content_hash, self.feedback_user_id, rating, reason, note
                )
                status.set_feedback_summary(_FeedbackSummary.summary_for(self))
            log_change(
                "feedback_add",
                song_id=self.state.song_id or None,
                hash_prefix=self.hash_prefix,
                rating=rating,
                reason=reason,
                note_len=len(note or ""),
            )
            self.notify("Feedback saved", timeout=3)
        except Exception as e:
            status.update(f" [red]Feedback save failed: {e}[/red]")

    def action_regenerate_lrc(self) -> None:
        """Force-regenerate the LRC via the analysis service (hotkey G)."""
        if self._guard_active_edit():
            return
        if self._guard_preview():
            return
        if self._save_in_flight:
            self.notify(
                "Save/upload in progress — try again when it finishes",
                severity="warning",
                timeout=3,
            )
            return
        if self._regen_in_flight:
            self.notify("Regeneration already in progress", severity="warning", timeout=3)
            return
        if self.analysis_client is None:
            self.notify(
                "Regeneration disabled: analysis service not configured "
                "(SOW_ANALYSIS_API_KEY / service.analysis_url)",
                severity="warning",
                timeout=5,
            )
            return
        if not self.state.song_id:
            self.notify(
                "Regeneration unavailable: no song id for this recording",
                severity="warning",
                timeout=5,
            )
            return

        try:
            with self._db_lock:
                recording = self.db_client.get_recording_by_hash(self.hash_prefix)
        except Exception as e:
            self.notify(f"Could not read the recording: {e}", severity="warning", timeout=5)
            return
        if recording is None:
            self.notify("No recording found for this session", severity="warning", timeout=5)
            return
        has_youtube = bool((recording.youtube_url or "").strip())

        from textual.screen import ModalScreen

        class RegenerateGeneratorDialog(ModalScreen[Optional[str]]):
            BINDINGS = [
                Binding("a", "auto", "Auto"),
                Binding("y", "youtube", "YouTube"),
                Binding("q", "qwen3", "Qwen3"),
                Binding("w", "whisper", "Whisper"),
                Binding("n", "cancel", "Cancel"),
                Binding("escape", "cancel", "Cancel"),
            ]

            def __init__(self, unsaved: bool, has_youtube: bool):
                super().__init__()
                self.unsaved = unsaved
                self.has_youtube = has_youtube

            def compose(self) -> ComposeResult:
                with Vertical():
                    yield Label("[bold yellow]Force-regenerate LRC?[/bold yellow]")
                    yield Label(
                        "Submits a new LRC generation job (force) and replaces the editor "
                        "with the freshly generated LRC when it finishes."
                    )
                    yield Label("[bold]a[/bold] auto — YouTube → Qwen3 → Whisper (default)")
                    if self.has_youtube:
                        yield Label("[bold]y[/bold] youtube — transcript only")
                    else:
                        yield Label(
                            "[d][bold]y[/bold] youtube — unavailable (no YouTube URL stored)[/]"
                        )
                    yield Label("[bold]q[/bold] qwen3 — DashScope ASR only")
                    yield Label("[bold]w[/bold] whisper — Whisper ASR only")
                    if self.unsaved:
                        yield Label(
                            "[yellow]Unsaved edits will be discarded (autosave cleared).[/yellow]"
                        )
                    yield Label("[d]Pick a generator | [bold]n[/bold] or esc to cancel[/]")

            def action_auto(self) -> None:
                self.dismiss("auto")

            def action_youtube(self) -> None:
                self.dismiss("youtube")

            def action_qwen3(self) -> None:
                self.dismiss("qwen3")

            def action_whisper(self) -> None:
                self.dismiss("whisper")

            def action_cancel(self) -> None:
                self.dismiss(None)

        def _handle_regenerate_confirm(generator: Optional[str]) -> None:
            if generator is None:
                return
            if generator == "youtube" and not has_youtube:
                self.notify(
                    "--generator youtube requires a YouTube URL on the recording; "
                    "none is stored.",
                    severity="warning",
                    timeout=5,
                )
                return
            self._start_regenerate(generator)

        self.app.push_screen(
            RegenerateGeneratorDialog(self.state.dirty or self._has_unsaved_changes(), has_youtube),
            _handle_regenerate_confirm,
        )

    def _start_regenerate(self, generator: str) -> None:
        """Kick off the blocking regeneration pipeline on a daemon thread."""
        self._regen_in_flight = True
        self._regen_snapshot = self.state.serialize()
        self.query_one(StatusIndicator).set_activity("Submitting LRC job…")
        self._regen_thread = threading.Thread(
            target=self._regenerate_worker,
            args=(generator,),
            name="lrc-regenerate",
            daemon=True,
        )
        self._regen_thread.start()

    def _regenerate_worker(self, generator: str) -> None:
        """Run the submit → poll → DB → R2-download chain off the event loop.

        Deliberately a plain daemon thread, not a Textual ``@work`` worker:
        thread workers run in the default executor, which ``asyncio.run``
        joins at app exit, so a force-quit would hang until the job timeout.
        """
        try:
            result = regenerate_lrc_for_editor(
                analysis_client=self.analysis_client,
                db_client=self.db_client,
                r2_client=self.r2_client,
                db_lock=self._db_lock,
                song_id=self.state.song_id,
                hash_prefix=self.hash_prefix,
                generator=generator,
                on_progress=self._regen_progress,
            )
        except Exception as e:  # unexpected: never kill the TUI
            logger.warning(f"LRC regeneration failed: {e}")
            result = RegenerateResult(ok=False, error=f"LRC regeneration failed: {e}")
        try:
            self.app.call_from_thread(self._finish_regenerate, result)
        except Exception as e:  # app already exited (force quit)
            logger.warning(f"Could not deliver LRC regeneration result: {e}")

    def _regen_progress(self, text: str) -> None:
        """Report worker-thread progress on the main thread."""
        try:
            self.app.call_from_thread(self._set_regen_activity, text)
        except Exception:  # app exited mid-flight
            pass

    def _set_regen_activity(self, text: str) -> None:
        self.query_one(StatusIndicator).set_activity(text)

    def _finish_regenerate(self, result: RegenerateResult) -> None:
        """Apply a worker result on the main thread."""
        self._regen_in_flight = False
        status = self.query_one(StatusIndicator)
        status.set_activity("")

        if not result.ok:
            status.update(f" [red]LRC regeneration failed: {result.error}[/red]")
            self.notify(
                f"LRC regeneration failed: {result.error}", severity="error", timeout=8
            )
            return

        # Edits made while the job ran would be lost by the refresh: keep
        # them in a local draft before overwriting the session.
        current = self.state.serialize()
        if self._regen_snapshot and current != self._regen_snapshot:
            try:
                draft_path = save_local_draft(self.cache_dir, self.hash_prefix, current)
                self.notify(f"Edits made during regeneration saved as draft: {draft_path}",
                            timeout=6)
            except OSError as e:
                logger.warning(f"Could not save concurrent-edit draft: {e}")

        content = result.lrc_content
        identity = result.identity
        if not content or identity is None:
            status.update(" [red]LRC regeneration returned no LRC content[/red]")
            return
        if not self.state.replace_content(content, identity):
            status.update(" [red]LRC regeneration returned no timed lines[/red]")
            return

        self.original_transcribed_content = content
        clear_autosave(self.cache_dir, self.hash_prefix)
        self._autosave_ok = False
        self._regen_snapshot = ""

        self._clear_selection_range(refresh=False)
        self._rebuild_table()
        self.sub_title = f"Visibility: {visibility_label('review')}"
        self._update_displays()
        self.notify("LRC regenerated and refreshed from R2", timeout=5)

    def action_reload_lrc(self) -> None:
        """Reload the latest R2 LRC without regenerating (hotkey L)."""
        if self._guard_active_edit():
            return
        if self._guard_preview():
            return
        if self._save_in_flight:
            self.notify(
                "Save/upload in progress — try again when it finishes",
                severity="warning",
                timeout=3,
            )
            return
        if self._regen_in_flight:
            self.notify(
                "Regeneration in progress — wait for it to finish",
                severity="warning",
                timeout=3,
            )
            return
        if self._reload_in_flight:
            self.notify("LRC reload already in progress", severity="warning", timeout=3)
            return
        self._start_reload()

    def _start_reload(self) -> None:
        """Fetch the current R2 LRC on a daemon thread (blocking S3 I/O)."""
        self._reload_in_flight = True
        self._reload_snapshot = self.state.serialize()
        self.query_one(StatusIndicator).set_activity("Fetching LRC from R2…")
        self._reload_thread = threading.Thread(
            target=self._reload_worker, name="lrc-reload", daemon=True
        )
        self._reload_thread.start()

    def _reload_worker(self) -> None:
        """Download the R2 LRC + its identity off the event loop."""
        content: Optional[str] = None
        identity: Optional[R2ObjectIdentity] = None
        visibility: Optional[str] = None
        error: Optional[str] = None
        try:
            content = self.r2_client.download_lrc_content(self.hash_prefix)
            if content:
                identity = self.r2_client.get_lrc_identity(self.hash_prefix)
                # The reload itself writes no DB state, so the header badge
                # must come from the catalog instead of an assumption.
                try:
                    with self._db_lock:
                        recording = self.db_client.get_recording_by_hash(self.hash_prefix)
                    visibility = recording.visibility_status if recording else None
                except Exception as e:  # badge only: never fail the reload for it
                    logger.warning(f"Could not read visibility for {self.hash_prefix}: {e}")
        except Exception as e:  # network/credential failure: report, don't crash
            logger.warning(f"LRC reload failed: {e}")
            error = f"LRC reload failed: {e}"
        try:
            self.app.call_from_thread(self._finish_reload, content, identity, visibility, error)
        except Exception as e:  # app already exited (force quit)
            logger.warning(f"Could not deliver LRC reload result: {e}")

    def _finish_reload(
        self,
        content: Optional[str],
        identity: Optional[R2ObjectIdentity],
        visibility: Optional[str],
        error: Optional[str],
    ) -> None:
        """Apply a reload result on the main thread."""
        self._reload_in_flight = False
        status = self.query_one(StatusIndicator)
        status.set_activity("")

        if error:
            status.update(f" [red]{error}[/red]")
            self.notify(error, severity="error", timeout=8)
            return
        if not content:
            status.update(" [red]No LRC found on R2[/red]")
            self.notify("No LRC found on R2 for this recording", severity="warning", timeout=5)
            return
        if content == self.original_transcribed_content:
            # Nothing newer on R2: leave the session (and unsaved edits) alone.
            self.notify("LRC on R2 unchanged — nothing to reload", timeout=3)
            return

        # Unsaved edits would be lost by the swap: keep them in a local draft
        # before overwriting the session (edits made before the keypress and
        # edits made while the fetch ran both count).
        current = self.state.serialize()
        if self._has_unsaved_changes() or (
            self._reload_snapshot and current != self._reload_snapshot
        ):
            try:
                draft_path = save_local_draft(self.cache_dir, self.hash_prefix, current)
                self.notify(f"Unsaved edits saved as draft: {draft_path}", timeout=6)
            except OSError as e:
                logger.warning(f"Could not save concurrent-edit draft: {e}")

        if identity is None or not self.state.replace_content(content, identity):
            status.update(" [red]R2 LRC has no timed lines[/red]")
            self.notify("R2 LRC has no timed lines", severity="warning", timeout=5)
            return

        self.original_transcribed_content = content
        clear_autosave(self.cache_dir, self.hash_prefix)
        self._autosave_ok = False
        self._reload_snapshot = ""

        self._clear_selection_range(refresh=False)
        self._rebuild_table()
        if visibility is not None:
            self.sub_title = f"Visibility: {visibility_label(visibility)}"
        self._update_displays()
        self.notify("LRC reloaded from R2", timeout=5)

    def _show_save_upload_prompt(
        self,
        validation: ValidationResult,
        revised: str,
        etag_changed: bool,
        etag_reason: str,
    ) -> None:
        from textual.screen import ModalScreen

        class SaveUploadDialog(ModalScreen[str]):
            BINDINGS = [
                Binding("d", "save_draft", "Local Draft"),
                Binding("u", "upload", "Upload R2"),
                Binding("f", "force_upload", "Force Upload"),
                Binding("c", "cancel", "Cancel"),
                Binding("escape", "cancel", "Cancel"),
            ]

            def __init__(
                self, validation_result, revised_content, parent_screen, etag_conflict, etag_msg
            ):
                super().__init__()
                self.validation = validation_result
                self.revised = revised_content
                self.parent_screen = parent_screen
                self.etag_conflict = etag_conflict
                self.etag_msg = etag_msg

            def compose(self) -> ComposeResult:
                with Vertical(id="dialog-container"):
                    yield Label("Save / Upload", classes="dialog-title")

                    if self.etag_conflict:
                        yield Label(f"[bold red]ETag conflict: {self.etag_msg}[/bold red]")
                        yield Label(
                            "[d]Press [bold]d[/bold] for local draft | "
                            "[bold]f[/bold] to force upload (overwrite) | "
                            "[bold]c[/bold] to cancel[/]"
                        )
                    elif self.validation.can_upload:
                        yield Label(
                            "[d]Press [bold]d[/bold] for local draft | [bold]u[/bold] for upload to R2 | [bold]c[/bold] to cancel[/]"
                        )
                    else:
                        yield Label(
                            "[d]Upload blocked. Press [bold]d[/bold] for local draft | [bold]c[/bold] to cancel[/]"
                        )

                    if self.validation.errors:
                        yield Label("[bold red]BLOCKING ERRORS:[/bold red]")
                        for e in self.validation.errors:
                            yield Label(f"  [red]✗ {e.message}[/red]")

                    if self.validation.warnings:
                        yield Label("[bold yellow]WARNINGS:[/bold yellow]")
                        for w in self.validation.warnings:
                            yield Label(f"  [yellow]⚠ {w.message}[/yellow]")

                    if self.validation.diff:
                        yield Label("[bold]DIFF:[/bold]")
                        diff_display = Static(self.validation.diff[:2000])
                        yield diff_display

            def action_save_draft(self) -> None:
                self.dismiss("draft")

            def action_upload(self) -> None:
                if self.validation.can_upload and not self.etag_conflict:
                    self.dismiss("upload")
                else:
                    self.dismiss("draft")

            def action_force_upload(self) -> None:
                if self.validation.can_upload:
                    self.dismiss("force_upload")
                else:
                    self.dismiss("draft")

            def action_cancel(self) -> None:
                self.dismiss("cancel")

        def _handle_dialog_result(result_str: str) -> None:
            if result_str == "draft":
                try:
                    draft_path = save_local_draft(self.cache_dir, self.hash_prefix, revised)
                    self.query_one(StatusIndicator).update(
                        f" [green]Draft saved: {draft_path}[/green]"
                    )
                except Exception as e:
                    self.query_one(StatusIndicator).update(f" [red]Draft save failed: {e}[/red]")

            elif result_str in ("upload", "force_upload"):
                if self._save_in_flight:
                    return
                self._save_in_flight = True
                self.query_one(StatusIndicator).set_activity("Uploading to R2…")
                self._upload_worker(
                    result_str == "force_upload", revised, self.state.transcribed_identity
                )

        self.app.push_screen(
            SaveUploadDialog(validation, revised, self, etag_changed, etag_reason),
            _handle_dialog_result,
        )
        self.app.upload_attempted = True

    @work(thread=True, group="save", exit_on_error=False)
    def _check_r2_worker(self, validation: ValidationResult, revised: str, transcribed_identity) -> None:
        """Run the blocking R2 stale-session check off the event loop."""
        started = time.monotonic()
        try:
            changed, reason = check_transcribed_changed(
                self.r2_client, self.hash_prefix, transcribed_identity
            )
        except Exception as e:
            # A failed probe is reported as a conflict: draft/force-upload stay available.
            changed, reason = True, f"Could not check R2: {e}"
            logger.warning(f"R2 conflict check failed: {e}")
        elapsed = time.monotonic() - started
        logger.info(
            "save_upload stage=precheck elapsed_ms=%.1f changed=%s", elapsed * 1000.0, changed
        )
        try:
            self.app.call_from_thread(
                self._open_save_dialog, validation, revised, changed, reason, elapsed
            )
        except Exception as e:  # worker finished during shutdown
            logger.warning(f"Could not deliver save precheck result: {e}")

    def _open_save_dialog(
        self,
        validation: ValidationResult,
        revised: str,
        etag_changed: bool,
        etag_reason: str,
        elapsed: float,
    ) -> None:
        """Main-thread continuation of ``_check_r2_worker``."""
        self._save_in_flight = False
        self.query_one(StatusIndicator).set_activity("")
        if elapsed >= 1.0:
            self.notify(
                f"R2 conflict check took {elapsed:.1f}s", severity="warning", timeout=4
            )
        self._show_save_upload_prompt(validation, revised, etag_changed, etag_reason)

    @work(thread=True, group="save", exit_on_error=False)
    def _upload_worker(self, force: bool, revised: str, transcribed_identity) -> None:
        """Perform the blocking R2 upload and DB update off the event loop."""
        started = time.monotonic()
        try:
            stage_started = time.monotonic()
            with self._db_lock:
                active, job_id = check_active_lrc_job(self.db_client, self.hash_prefix)
            logger.info(
                "save_upload stage=active_job_check elapsed_ms=%.1f active=%s",
                (time.monotonic() - stage_started) * 1000.0,
                active,
            )
            if active:
                result = UploadResult(
                    success=False,
                    error=f"Upload blocked: LRC generation job {job_id} is active",
                )
            else:
                outcome = upload_revised_lrc(
                    r2_client=self.r2_client,
                    cache_dir=self.cache_dir,
                    revised_content=revised,
                    transcribed_identity=transcribed_identity,
                    original_transcribed_content=self.original_transcribed_content,
                    hash_prefix=self.hash_prefix,
                    force=force,
                )
                with self._db_lock:
                    result = finalize_upload(self.db_client, self.hash_prefix, outcome)
        except Exception as e:
            logger.warning(f"Upload worker failed: {e}")
            result = UploadResult(success=False, error=f"Upload failed: {e}")

        elapsed = time.monotonic() - started
        logger.info(
            "save_upload stage=worker_total elapsed_ms=%.1f success=%s partial=%s",
            elapsed * 1000.0,
            result.success,
            result.partial,
        )
        try:
            self.app.call_from_thread(self._finish_upload, result, force, revised, elapsed)
        except Exception as e:  # worker finished during shutdown
            logger.warning(f"Could not deliver upload result: {e}")

    def _finish_upload(
        self, result: UploadResult, force: bool, revised: str, elapsed: float
    ) -> None:
        """Main-thread continuation of ``_upload_worker``."""
        self._save_in_flight = False
        status = self.query_one(StatusIndicator)
        status.set_activity("")

        if self.upload_result_out is not None:
            try:
                self.upload_result_out(result)
            except Exception as e:  # never break the editor on observer errors
                logger.warning(f"upload_result_out callback failed: {e}")

        if result.success or result.partial:
            log_change(
                "lrc_save",
                song_id=self.state.song_id or None,
                hash_prefix=self.hash_prefix,
                via="editor",
                r2_url=result.r2_url,
                bytes=len(revised.encode("utf-8")),
                db_updated=result.success,
            )

        elapsed_text = f" ({elapsed:.1f}s)"

        if result.success:
            # Only clear the dirty flag when the session still matches what was uploaded.
            still_current = self.state.serialize() == revised
            if still_current:
                from stream_of_worship.admin.editor.autosave import clear_autosave

                clear_autosave(self.cache_dir, self.hash_prefix)
                self.state.dirty = False

            prefix = (
                " [green]Force upload successful![/green]"
                if force
                else " [green]Upload successful![/green]"
            )
            msg = prefix + elapsed_text + "\n"
            msg += f" R2 URL: {result.r2_url}\n"
            if result.local_backup_path:
                msg += f" Local backup: {result.local_backup_path}\n"
            if result.r2_backup_url:
                msg += f" R2 backup: {result.r2_backup_url}\n"
            if not still_current:
                msg += " (edits made during upload remain unsaved)\n"
            # Refresh first: _update_displays re-renders the status line and would
            # otherwise wipe this result message.
            self._update_displays()
            status.update(msg)

        elif result.partial:
            msg = (
                f" [yellow]Partial success:[/yellow]{elapsed_text}\n"
                f" R2 URL: {result.r2_url}\n"
                f" {result.error}\n"
            )
            if result.local_backup_path:
                msg += f" Local backup: {result.local_backup_path}\n"
            if result.r2_backup_url:
                msg += f" R2 backup: {result.r2_backup_url}\n"
            status.update(msg)

        else:
            status.update(f" [red]Upload failed: {result.error}[/red]{elapsed_text}")

    def action_show_keymap(self) -> None:
        if self._is_edit_active():
            return  # let `?` go into the input

        from textual.screen import ModalScreen

        class KeymapDialog(ModalScreen[None]):
            BINDINGS = [
                Binding("escape", "close", "Close"),
                Binding("?", "close", "Close"),
            ]

            def __init__(
                self, groups: dict[str, list[str]], all_bindings: list[Binding]
            ) -> None:
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
                            yield Label(
                                f"[dim]{format_key_display(b.key)}[/dim]={b.description}"
                            )
                        yield Label("")
                    yield Label(
                        "[d]Press [bold]?[/bold] or [bold]Esc[/bold] to close[/]"
                    )

            def action_close(self) -> None:
                self.dismiss(None)

        self.app.push_screen(KeymapDialog(self.BINDING_GROUPS, self.BINDINGS))

    def action_quit_editor(self) -> None:
        if self._is_edit_active():
            self._cancel_row_edit()
            return

        if self._preview_active:
            self._stop_preview()
            return

        if self._save_in_flight:
            self.notify(
                "Save/upload in progress — wait for it to finish", severity="warning", timeout=3
            )
            return

        if self._regen_in_flight:
            from textual.screen import ModalScreen

            class ForceQuitRegenDialog(ModalScreen[bool]):
                BINDINGS = [
                    Binding("y", "confirm", "Yes"),
                    Binding("n", "cancel", "No"),
                    Binding("escape", "cancel", "No"),
                ]

                def compose(self) -> ComposeResult:
                    with Vertical():
                        yield Label(
                            "[bold yellow]LRC regeneration is still running.[/bold yellow]"
                        )
                        yield Label(
                            "Force quit? The job keeps running server-side and the "
                            "recording stays 'processing' until you run "
                            "'sow-admin lyrics generate <song>' (without --force) to poll it."
                        )
                        yield Label("[d]Press [bold]y[/bold] to force quit | [bold]n[/bold] to wait[/]")

                def action_confirm(self) -> None:
                    self.dismiss(True)

                def action_cancel(self) -> None:
                    self.dismiss(False)

            def _handle_force_quit(force: bool) -> None:
                if force:
                    self.app.quit_clean = False
                    self.app.exit()

            self.app.push_screen(ForceQuitRegenDialog(), _handle_force_quit)
            return

        from textual.screen import ModalScreen

        class QuitConfirmDialog(ModalScreen[bool]):
            BINDINGS = [
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
                self.app.quit_clean = not self._has_unsaved_changes()
                self.app.exit()

        if self.state.dirty:
            self._do_autosave()

        self.app.push_screen(QuitConfirmDialog(self.state.dirty), _handle_quit_confirm)
