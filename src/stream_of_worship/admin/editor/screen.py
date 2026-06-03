"""Main editor screen for the admin LRC editor.

Provides the interactive LRC editing interface with playback controls,
line table, editing panel, and save/upload flow.
"""

import asyncio
import logging
from pathlib import Path
from typing import Optional

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import (
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    Static,
)

from stream_of_worship.admin.db.client import DatabaseClient
from stream_of_worship.admin.editor.autosave import AutosaveState, save_autosave
from stream_of_worship.admin.editor.state import EditorState
from stream_of_worship.admin.editor.upload import (
    check_transcribed_changed,
    save_local_draft,
    upload_revised_lrc,
)
from stream_of_worship.admin.editor.validation import ValidationResult, validate_lrc
from stream_of_worship.admin.services.lrc_parser import (
    format_centiseconds,
    format_duration,
)
from stream_of_worship.admin.services.playback import PlaybackService, PlaybackState
from stream_of_worship.admin.services.r2 import R2Client

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


class StatusIndicator(Static):
    """Recovery/draft/upload status indicator."""

    def __init__(self):
        super().__init__("")
        self._dirty = False
        self._autosave_ok = False
        self._source = ""

    def update_status(self, dirty: bool, autosave_ok: bool, source: str, padding_offset: float = 0.0, padding_quarters: int = 0, preview_active: bool = False) -> None:
        self._dirty = dirty
        self._autosave_ok = autosave_ok
        self._source = source

        dirty_mark = "[red]*[/red]" if dirty else "[green]✓[/green]"
        autosave_mark = "[green]saved[/green]" if autosave_ok else "[dim]—[/dim]"
        source_label = {"r2": "R2", "catalog": "Catalog"}.get(source, source)

        parts = [f" {dirty_mark} Dirty | Autosave: {autosave_mark} | Source: {source_label}"]
        if preview_active:
            parts.append(" | PREVIEW")
        if padding_quarters != 0:
            parts.append(f" | Pad: {padding_offset:+.2f}s ({padding_quarters:+d}q)")
        self.update("".join(parts))


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

    BINDINGS = [
        Binding("space", "toggle_playback", "Play/Pause"),
        Binding("left", "seek_backward", "Seek -5s"),
        Binding("right", "seek_forward", "Seek +5s"),
        Binding("shift+left", "show_earlier", "Pad Earlier"),
        Binding("shift+right", "show_later", "Pad Later"),
        Binding("up", "select_prev", "Prev Line"),
        Binding("down", "select_next", "Next Line"),
        Binding("j", "jump_to_line", "Jump"),
        Binding("enter", "stamp_line", "Stamp Time"),
        Binding("a", "stamp_and_advance", "Stamp+Advance"),
        Binding("e", "edit_text", "Edit Text"),
        Binding("t", "edit_timestamp", "Edit Time"),
        Binding("i", "insert_after", "Insert After"),
        Binding("I", "insert_canonical", "Insert Canonical"),
        Binding("ctrl+c", "copy_line", "Copy"),
        Binding("ctrl+v", "paste_after", "Paste"),
        Binding("d", "delete_line", "Delete Line"),
        Binding("ctrl+z", "undo", "Undo"),
        Binding("ctrl+y", "redo", "Redo"),
        Binding("s", "save_upload", "Save/Upload"),
        Binding("p", "preview_single", "Preview Line"),
        Binding("P", "preview_continuous", "Preview All"),
        Binding("escape", "quit_editor", "Quit"),
        Binding("q", "quit_editor", "Quit"),
    ]

    def __init__(
        self,
        editor_state: EditorState,
        playback_service: PlaybackService,
        cache_dir: Path,
        r2_client: R2Client,
        db_client: DatabaseClient,
        hash_prefix: str,
        original_transcribed_content: Optional[str],
    ):
        super().__init__()
        self.state = editor_state
        self.playback = playback_service
        self.cache_dir = cache_dir
        self.r2_client = r2_client
        self.db_client = db_client
        self.hash_prefix = hash_prefix
        self.original_transcribed_content = original_transcribed_content
        self._autosave_ok = False
        self._editing_text = False
        self._editing_timestamp = False
        self._position_update_timer: Optional[asyncio.Task] = None
        self._clipboard: Optional[tuple] = None
        self._preview_active: bool = False
        self._preview_mode: str = "single"
        self._preview_target_index: int = -1

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical():
            yield CurrentLyricDisplay()
            yield PlaybackBar()
            yield DataTable(id="line-table")
            with Horizontal(id="edit-panel"):
                yield Label("Selected:", id="edit-label")
                yield Input(id="edit-input", placeholder="Edit text or timestamp here")
            yield StatusIndicator()
        yield Footer()

    def on_mount(self) -> None:
        self._setup_table()
        self._refresh_table()
        self._update_displays()
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

    def _refresh_table(self) -> None:
        table = self.query_one("#line-table", DataTable)
        table.clear()

        for i, line in enumerate(self.state.timed_lines):
            ts = format_centiseconds(line.time_seconds)
            status = ""
            if line.time_seconds == 0.0 and line.text.strip():
                status = "[dim]draft[/dim]"
            if i > 0 and line.time_seconds < self.state.timed_lines[i - 1].time_seconds:
                status = "[red]!non-mono[/red]"

            is_selected = i == self.state.selected_index
            row_label = f">{i + 1}" if is_selected else str(i + 1)

            table.add_row(row_label, ts, line.text, status, key=str(i))

        if 0 <= self.state.selected_index < self.state.line_count:
            table.move_cursor(row=self.state.selected_index)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self.state.selected_index = event.cursor_row
        self._update_displays()

    def _update_displays(self) -> None:
        lyric_display = self.query_one(CurrentLyricDisplay)
        current = self.state.selected_line
        if current:
            next_idx = self.state.selected_index + 1
            next_line = ""
            if next_idx < self.state.line_count:
                next_line = self.state.timed_lines[next_idx].text
            lyric_display.update_lyrics(current.text, next_line)

        status = self.query_one(StatusIndicator)
        status.update_status(
            self.state.dirty, self._autosave_ok, self.state.source_mode,
            padding_offset=self.state.padding_offset_seconds,
            padding_quarters=self.state.padding_quarters,
            preview_active=self._preview_active,
        )

    def _update_playback_bar(self) -> None:
        bar = self.query_one(PlaybackBar)
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
            if current_secs >= target_line.time_seconds + 2.0:
                self._stop_preview()
                return
            if current_secs >= target_line.time_seconds:
                if self.state.selected_index != self._preview_target_index:
                    self.state.select_line(self._preview_target_index)
                    self._refresh_table()
                    self._update_displays()
            return

        current_line_idx = self._find_line_at_position(current_secs)
        if current_line_idx != self.state.selected_index:
            self.state.select_line(current_line_idx)
            self._refresh_table()
            self._update_displays()

    def _on_playback_state(self, new_state: PlaybackState) -> None:
        self._update_playback_bar()

    def _on_playback_finished(self) -> None:
        self._preview_active = False
        self._preview_mode = "single"
        self._preview_target_index = -1
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
        return 0

    # --- Preview helpers ---

    def _stop_preview(self) -> None:
        self.playback.pause()
        self._preview_active = False
        self._preview_mode = "single"
        self._preview_target_index = -1
        self._update_displays()

    # --- Action handlers ---

    def action_toggle_playback(self) -> None:
        self.playback.toggle_play_pause()

    def action_seek_forward(self) -> None:
        self.playback.skip_forward(5.0)

    def action_seek_backward(self) -> None:
        self.playback.skip_backward(5.0)

    def action_select_prev(self) -> None:
        self.state.select_prev()
        self._refresh_table()
        self._update_displays()

    def action_select_next(self) -> None:
        self.state.select_next()
        self._refresh_table()
        self._update_displays()

    def action_jump_to_line(self) -> None:
        line = self.state.selected_line
        if line:
            self.playback.seek(line.time_seconds)

    def action_stamp_line(self) -> None:
        if self._preview_active:
            self.notify("Stamping disabled during preview", severity="warning", timeout=2)
            return
        pos = self.playback.position_seconds
        self.state.set_timestamp(self.state.selected_index, pos)
        line = self.state.selected_line
        if line:
            self.playback.seek(line.time_seconds)
        self._refresh_table()
        self._update_displays()
        self._do_autosave()

    def action_stamp_and_advance(self) -> None:
        if self._preview_active:
            self.notify("Stamping disabled during preview", severity="warning", timeout=2)
            return
        pos = self.playback.position_seconds
        self.state.set_timestamp(self.state.selected_index, pos)
        self.state.select_next()
        self._refresh_table()
        self._update_displays()
        self._do_autosave()

    def action_show_earlier(self) -> None:
        if not self.state.adjust_padding(-1):
            self.notify(
                f"Padding limit reached: {self.state.padding_quarters:+d}q "
                f"({self.state.padding_offset_seconds:+.2f}s)",
                severity="warning", timeout=2,
            )
            return
        self._refresh_table()
        self._update_displays()
        self._do_autosave()
        offset = self.state.padding_offset_seconds
        quarters = self.state.padding_quarters
        self.notify(f"Padding: {offset:+.2f}s ({quarters:+d}q)", timeout=2)

    def action_show_later(self) -> None:
        if not self.state.adjust_padding(1):
            self.notify(
                f"Padding limit reached: {self.state.padding_quarters:+d}q "
                f"({self.state.padding_offset_seconds:+.2f}s)",
                severity="warning", timeout=2,
            )
            return
        self._refresh_table()
        self._update_displays()
        self._do_autosave()
        offset = self.state.padding_offset_seconds
        quarters = self.state.padding_quarters
        self.notify(f"Padding: {offset:+.2f}s ({quarters:+d}q)", timeout=2)

    def action_preview_single(self) -> None:
        if self._preview_active:
            self._stop_preview()
            return

        line = self.state.selected_line
        if not line or line.time_seconds == 0.0:
            self.notify("No timestamp on current line", severity="warning", timeout=2)
            return

        start_pos = max(0.0, line.time_seconds - 3.0)
        self._preview_mode = "single"
        self._preview_target_index = self.state.selected_index
        self._preview_active = True
        self.playback.play(start_seconds=start_pos)
        self._update_displays()

    def action_preview_continuous(self) -> None:
        if self._preview_active:
            self._stop_preview()
            return

        line = self.state.selected_line
        if not line or line.time_seconds == 0.0:
            self.notify("No timestamp on current line", severity="warning", timeout=2)
            return

        start_pos = max(0.0, line.time_seconds - 3.0)
        self._preview_mode = "continuous"
        self._preview_target_index = self.state.selected_index
        self._preview_active = True
        self.playback.play(start_seconds=start_pos)
        self._update_displays()

    def action_edit_text(self) -> None:
        self._editing_text = True
        self._editing_timestamp = False
        edit_input = self.query_one("#edit-input", Input)
        line = self.state.selected_line
        if line:
            edit_input.value = line.text
        edit_input.focus()

    def action_edit_timestamp(self) -> None:
        self._editing_text = False
        self._editing_timestamp = True
        edit_input = self.query_one("#edit-input", Input)
        line = self.state.selected_line
        if line:
            edit_input.value = format_centiseconds(line.time_seconds)
        edit_input.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "edit-input":
            value = event.value.strip()
            if self._editing_text:
                self.state.set_text(self.state.selected_index, value)
            elif self._editing_timestamp:
                try:
                    ts = self._parse_timestamp_input(value)
                    self.state.set_timestamp(self.state.selected_index, ts)
                except ValueError:
                    pass

            self._editing_text = False
            self._editing_timestamp = False
            event.input.value = ""
            self._refresh_table()
            self._update_displays()
            self._do_autosave()

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
        self.state.insert_after(self.state.selected_index)
        self.state.select_next()
        self._refresh_table()
        self._update_displays()
        self._do_autosave()

    def action_insert_canonical(self) -> None:
        recording = self.db_client.get_recording_by_hash(self.hash_prefix)
        if not recording or not recording.song_id:
            self.notify("No song linked", severity="warning", timeout=3)
            return

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

        self.state.insert_lines_after(self.state.selected_index, non_blank)
        self.state.select_line(self.state.selected_index + 1)
        self._refresh_table()
        self._update_displays()
        self._do_autosave()
        self.notify(f"Inserted {len(non_blank)} canonical lyrics lines", timeout=3)

    def action_copy_line(self) -> None:
        line = self.state.selected_line
        if line:
            self._clipboard = (line.text, line.time_seconds)
            self.notify(f"Copied line {self.state.selected_index + 1}", timeout=2)

    def action_paste_after(self) -> None:
        if self._clipboard is None:
            self.notify("Nothing to paste", timeout=2)
            return
        text, time_seconds = self._clipboard
        self.state.insert_after(self.state.selected_index, text=text, time_seconds=time_seconds)
        self.state.select_next()
        self._refresh_table()
        self._update_displays()
        self._do_autosave()

    def action_delete_line(self) -> None:
        if self.state.line_count <= 1:
            return

        self.state.delete_line(self.state.selected_index)
        self._refresh_table()
        self._update_displays()
        self._do_autosave()

    def action_undo(self) -> None:
        if self.state.undo():
            self._refresh_table()
            self._update_displays()
            self._do_autosave()
            self.notify("Undo", timeout=2)

    def action_redo(self) -> None:
        if self.state.redo():
            self._refresh_table()
            self._update_displays()
            self._do_autosave()
            self.notify("Redo", timeout=2)

    def action_save_upload(self) -> None:
        revised = self.state.serialize()
        result = validate_lrc(
            timed_lines=self.state.timed_lines,
            preserved_lines=self.state.preserved_lines,
            original_serialized=self.state.original_serialized,
            audio_duration_seconds=self.state.audio_duration,
            original_preserved_lines=self.state.original_preserved_lines,
        )

        etag_changed, etag_reason = check_transcribed_changed(
            self.r2_client, self.hash_prefix, self.state.transcribed_identity,
        )

        self._show_save_upload_prompt(result, revised, etag_changed, etag_reason)

    def _show_save_upload_prompt(
        self, validation: ValidationResult, revised: str, etag_changed: bool, etag_reason: str,
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

            def __init__(self, validation_result, revised_content, parent_screen, etag_conflict, etag_msg):
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
                        yield Label(
                            f"[bold red]ETag conflict: {self.etag_msg}[/bold red]"
                        )
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

        async def _handle_dialog_result(result_str: str) -> None:
            if result_str == "draft":
                try:
                    draft_path = save_local_draft(self.cache_dir, self.hash_prefix, revised)
                    self.query_one(StatusIndicator).update(
                        f" [green]Draft saved: {draft_path}[/green]"
                    )
                except Exception as e:
                    self.query_one(StatusIndicator).update(f" [red]Draft save failed: {e}[/red]")

            elif result_str in ("upload", "force_upload"):
                force = result_str == "force_upload"
                upload_result = upload_revised_lrc(
                    r2_client=self.r2_client,
                    db_client=self.db_client,
                    cache_dir=self.cache_dir,
                    state=self.state,
                    original_transcribed_content=self.original_transcribed_content,
                    hash_prefix=self.hash_prefix,
                    force=force,
                )

                if upload_result.success:
                    from stream_of_worship.admin.editor.autosave import clear_autosave

                    clear_autosave(self.cache_dir, self.hash_prefix)
                    self.state.dirty = False

                    prefix = " [green]Force upload successful![/green]\n" if force else " [green]Upload successful![/green]\n"
                    msg = prefix + f" R2 URL: {upload_result.r2_url}\n"
                    if upload_result.local_backup_path:
                        msg += f" Local backup: {upload_result.local_backup_path}\n"
                    if upload_result.r2_backup_url:
                        msg += f" R2 backup: {upload_result.r2_backup_url}\n"
                    self.query_one(StatusIndicator).update(msg)
                    self._update_displays()

                elif upload_result.partial:
                    msg = (
                        f" [yellow]Partial success:[/yellow]\n"
                        f" R2 URL: {upload_result.r2_url}\n"
                        f" {upload_result.error}\n"
                    )
                    if upload_result.local_backup_path:
                        msg += f" Local backup: {upload_result.local_backup_path}\n"
                    if upload_result.r2_backup_url:
                        msg += f" R2 backup: {upload_result.r2_backup_url}\n"
                    self.query_one(StatusIndicator).update(msg)

                else:
                    self.query_one(StatusIndicator).update(
                        f" [red]Upload failed: {upload_result.error}[/red]"
                    )

        self.app.push_screen(
            SaveUploadDialog(validation, revised, self, etag_changed, etag_reason),
            _handle_dialog_result,
        )

    def action_quit_editor(self) -> None:
        if self._editing_text or self._editing_timestamp:
            self._editing_text = False
            self._editing_timestamp = False
            edit_input = self.query_one("#edit-input", Input)
            edit_input.value = ""
            self.query_one("#line-table", DataTable).focus()
            return

        self._preview_active = False

        if self.state.dirty:
            self._do_autosave()
            from textual.screen import ModalScreen

            class QuitConfirmDialog(ModalScreen[bool]):
                BINDINGS = [
                    Binding("y", "confirm", "Yes"),
                    Binding("n", "cancel", "No"),
                    Binding("escape", "cancel", "No"),
                ]

                def compose(self) -> ComposeResult:
                    with Vertical():
                        yield Label("[bold yellow]Unsaved changes exist![/bold yellow]")
                        yield Label("Autosave has been updated. Quit anyway?")
                        yield Label("[d]Press [bold]y[/bold] to quit | [bold]n[/bold] to return[/]")

                def action_confirm(self) -> None:
                    self.dismiss(True)

                def action_cancel(self) -> None:
                    self.dismiss(False)

            def _handle_quit_confirm(should_quit: bool) -> None:
                if should_quit:
                    self.app.exit()

            self.app.push_screen(QuitConfirmDialog(), _handle_quit_confirm)
        else:
            self.app.exit()
