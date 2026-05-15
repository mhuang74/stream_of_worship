"""Songset editor screen.

Allows editing a songset: reordering songs, adjusting transitions, previewing.
"""

from typing import Optional

from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.screen import Screen
from textual.widgets import Button, DataTable, Footer, Header, Input, Label

from stream_of_worship.app.db.models import SongsetItem
from stream_of_worship.app.db.songset_client import SongsetClient
from stream_of_worship.app.logging_config import get_logger
from stream_of_worship.app.services.asset_cache import AssetCache
from stream_of_worship.app.services.audio_engine import AudioEngine
from stream_of_worship.app.services.catalog import CatalogService
from stream_of_worship.app.services.playback import PlaybackPosition, PlaybackService, PlaybackState
from stream_of_worship.app.state import AppScreen, AppState
from stream_of_worship.app.widgets import PlaybackBar

logger = get_logger(__name__)


class SongsetEditorScreen(Screen):
    """Screen for editing a songset."""

    BINDINGS = [
        ("c", "add_songs", "Song Catalog"),
        ("r", "remove_song", "Remove"),
        ("comma", "move_up", "Move Up"),
        ("full_stop", "move_down", "Move Down"),
        ("e", "edit_transition", "Edit Transition"),
        ("t", "preview", "Transition Preview"),
        ("l", "lyrics_preview", "Lyrics"),
        ("space", "toggle_playback", "Play/Stop"),
        ("left", "skip_backward", "Skip -10s"),
        ("right", "skip_forward", "Skip +10s"),
        ("x", "export", "Export"),
        ("i", "edit_info", "Edit Info"),
        ("escape", "back", "Back"),
        # Override app-level bindings to disable them on this screen
        Binding("q", "noop", "Quit", show=False),
        Binding("s", "noop", "Settings", show=False),
    ]

    def __init__(
        self,
        state: AppState,
        songset_client: SongsetClient,
        catalog: CatalogService,
        playback: PlaybackService,
        audio_engine: AudioEngine,
        asset_cache: AssetCache,
    ):
        """Initialize the screen.

        Args:
            state: Application state
            songset_client: Songset database client
            catalog: Catalog service
            playback: Playback service
            audio_engine: Audio engine for generating previews
            asset_cache: Asset cache for downloading audio
        """
        super().__init__()
        self.state = state
        self.songset_client = songset_client
        self.catalog = catalog
        self.playback = playback
        self.audio_engine = audio_engine
        self.asset_cache = asset_cache
        self.items: list[SongsetItem] = []
        self._initial_load = True
        self._songset_listener = None
        self._pending_cursor_row: Optional[int] = None

    def compose(self) -> ComposeResult:
        """Compose the screen layout."""
        yield Header()

        with Vertical():
            yield Label("[bold]Songset Editor[/bold]", id="title")

            with Horizontal(id="info_container"):
                yield Label("Name:", id="name_label")
                yield Input(placeholder="Songset name", id="input_name")
                yield Label("Description:", id="desc_label")
                yield Input(placeholder="Description (optional)", id="input_description")

            table = DataTable(id="items_table")
            table.add_columns("#", "Song", "Key", "Tempo", "Duration", "Gap", "Transition")
            table.cursor_type = "row"
            yield table

            with Horizontal(id="buttons"):
                yield Button("Song Catalog", id="btn_add", variant="primary")
                yield Button("Remove", id="btn_remove")
                yield Button("Edit Transition", id="btn_edit")
                yield Button("Preview", id="btn_preview")
                yield Button("Lyrics", id="btn_lyrics")
                yield Button("Export", id="btn_export", variant="success")
                yield Button("Back", id="btn_back")

            yield PlaybackBar(self.playback, id="playback_bar")

        yield Footer()

    def on_mount(self) -> None:
        """Handle mount event."""
        logger.info(
            f"SongsetEditorScreen mounted (songset: {self.state.selected_songset.id if self.state.selected_songset else 'None'})"
        )
        # Defer refresh until DOM is ready (Fix 2)
        self.call_after_refresh(self._refresh)

        # Focus the song list, not the name input
        self.call_after_refresh(self._focus_song_list)

        # Store named method before registration so remove_listener can match by identity (Fix 3)
        self._songset_listener = self._on_selected_songset_changed
        self.state.add_listener("selected_songset", self._songset_listener)

        # Register playback callbacks
        self.playback.set_callbacks(
            on_position_changed=self._on_position_changed,
            on_state_changed=self._on_state_changed,
            on_finished=self._on_finished,
        )

    def _on_selected_songset_changed(self, _) -> None:
        """Handle selected_songset state change."""
        self._refresh()

    def on_unmount(self) -> None:
        """Unregister callbacks to prevent memory leaks."""
        self.playback.set_callbacks()
        if self._songset_listener:
            self.state.remove_listener("selected_songset", self._songset_listener)
            self._songset_listener = None

    def _on_position_changed(self, position: PlaybackPosition) -> None:
        """Handle position updates from playback service."""

        def _update():
            try:
                self.query_one(PlaybackBar).update_display(position)
            except NoMatches:
                pass

        self.call_after_refresh(_update)

    def _on_state_changed(self, state: PlaybackState) -> None:
        """Handle state changes from playback service."""

        def _update():
            try:
                self.query_one(PlaybackBar).update_visibility()
                if state != PlaybackState.STOPPED:
                    self.query_one(PlaybackBar).update_display(self.playback.get_position())
            except NoMatches:
                pass

        self.call_after_refresh(_update)

    def _on_finished(self) -> None:
        """Handle playback finished."""

        def _update():
            try:
                self.query_one(PlaybackBar).update_visibility()
            except NoMatches:
                pass

        self.call_after_refresh(_update)

    def _focus_song_list(self) -> None:
        """Focus the items table."""
        table = self.query_one("#items_table", DataTable)
        table.focus()

    def on_screen_resume(self, event: events.ScreenResume) -> None:
        """Handle screen resume (when returning from browse/add songs)."""
        logger.info("SongsetEditorScreen resumed, refreshing items")
        self._refresh()


    def _refresh(self) -> None:
        """Refresh the display."""
        if not self.is_current:
            return
        songset = self.state.selected_songset
        if not songset:
            return

        # Update input fields with current values
        try:
            name_input = self.query_one("#input_name", Input)
            name_input.value = songset.name

            desc_input = self.query_one("#input_description", Input)
            desc_input.value = songset.description or ""
        except Exception as e:
            logger.error(f"Failed to update input fields: {e}")

        self._load_items()

    def _load_items(self) -> None:
        """Load songset items on a worker thread (Fix 9)."""
        self.run_worker(self._load_items_worker, exclusive=True, group="load_items", thread=True)

    def _load_items_worker(self) -> None:
        """Worker: fetch items from DB then update UI on main thread."""
        if not self.state.selected_songset:
            return
        try:
            details, orphan_count = self.catalog.get_songset_with_items(
                self.state.selected_songset.id, self.songset_client
            )
            self.app.call_from_thread(self._update_items_table, details, orphan_count)
        except Exception as e:
            logger.error(f"Error loading songset items: {e}")
            self.app.call_from_thread(self.notify, "Failed to load items", severity="error")

    def _update_items_table(self, details, orphan_count) -> None:
        """Update the items table on the main thread."""
        self.items = [d.item for d in details]
        for detail in details:
            detail.item.song_title = detail.display_title
            if detail.recording:
                detail.item.tempo_bpm = detail.recording.tempo_bpm
                detail.item.duration_seconds = detail.recording.duration_seconds
                detail.item.recording_key = detail.recording.musical_key
            if detail.song:
                detail.item.song_key = detail.song.musical_key

        self.state.update_songset_items(self.items)

        table = self.query_one("#items_table", DataTable)
        table.clear()

        for i, item in enumerate(self.items):
            gap_text = f"{item.gap_beats} beats" if item.gap_beats else "No gap"
            transition_text = "Crossfade" if item.crossfade_enabled else "Gap"
            tempo_text = f"{int(item.tempo_bpm)}" if item.tempo_bpm else "-"

            table.add_row(
                str(i + 1),
                item.song_title or "Unknown",
                item.display_key or "-",
                tempo_text,
                item.formatted_duration,
                gap_text,
                transition_text,
                key=item.id,
            )

        if self._pending_cursor_row is not None:
            table.move_cursor(row=self._pending_cursor_row)
            self._pending_cursor_row = None

        if len(self.items) == 0 and self._initial_load:
            self._initial_load = False
            self.call_after_refresh(self._open_browse_for_new_songset)

    def _open_browse_for_new_songset(self) -> None:
        """Navigate to Browse screen for new empty songsets."""
        self.app.navigate_to(AppScreen.BROWSE)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses."""
        button_id = event.button.id

        if button_id == "btn_add":
            self.action_add_songs()
        elif button_id == "btn_remove":
            self.action_remove_song()
        elif button_id == "btn_edit":
            self.action_edit_transition()
        elif button_id == "btn_preview":
            self.action_preview()
        elif button_id == "btn_lyrics":
            self.action_lyrics_preview()
        elif button_id == "btn_export":
            self.action_export()
        elif button_id == "btn_back":
            self.action_back()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Handle row selection."""
        item_id = event.row_key.value
        for item in self.items:
            if item.id == item_id:
                self.state.select_item(item)
                break

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle input submission (Enter key)."""
        if event.input.id in ("input_name", "input_description"):
            self._save_songset_info()
            # Move focus to table
            table = self.query_one("#items_table", DataTable)
            table.focus()

    def on_input_blurred(self, event: Input.Blurred) -> None:
        """Handle input losing focus."""
        if event.input.id in ("input_name", "input_description"):
            self._save_songset_info()

    def _save_songset_info(self) -> None:
        """Save songset name and description from input fields."""
        if not self.state.selected_songset:
            return

        name_input = self.query_one("#input_name", Input)
        desc_input = self.query_one("#input_description", Input)

        name = name_input.value.strip()
        description = desc_input.value.strip()

        # Validate name is not empty
        if not name:
            self.notify("Songset name cannot be empty", severity="error")
            name_input.value = self.state.selected_songset.name
            return

        # Only update if changed
        if (name != self.state.selected_songset.name or
            description != (self.state.selected_songset.description or "")):

            success = self.songset_client.update_songset(
                self.state.selected_songset.id,
                name=name,
                description=description if description else None,
            )

            if success:
                logger.info(f"Updated songset info: name='{name}', description='{description}'")
                # Update state
                self.state.selected_songset.name = name
                self.state.selected_songset.description = description if description else None
                self.notify("Songset info updated")
            else:
                logger.error("Failed to update songset info")
                self.notify("Failed to update songset", severity="error")

    def _get_selected_item(self) -> Optional[SongsetItem]:
        """Get the currently selected item, or the cursor row if none selected."""
        item_id = None

        if self.state.selected_item:
            item_id = self.state.selected_item.id
        else:
            # If no explicit selection, use cursor row
            table = self.query_one("#items_table", DataTable)
            if table.cursor_row is not None:
                rows = list(table.rows.keys())
                if table.cursor_row < len(rows):
                    item_id = rows[table.cursor_row].value

        # Always look up from current items list to avoid stale object references
        if item_id is not None:
            for item in self.items:
                if item.id == item_id:
                    return item

        return None

    def action_add_songs(self) -> None:
        """Navigate to browse screen to add songs."""
        self.app.navigate_to(AppScreen.BROWSE)

    def action_remove_song(self) -> None:
        """Remove selected song from songset."""
        item = self._get_selected_item()

        if not item:
            self.notify("No song selected", severity="error")
            return

        # Save cursor position before removal
        table = self.query_one("#items_table", DataTable)
        cursor_row = table.cursor_row

        self.state.select_item(item)
        self.songset_client.remove_item(item.id)
        self.state.select_item(None)  # Clear selection after removal

        # Defer cursor positioning until after _load_items completes
        if cursor_row is not None and len(self.items) > 0:
            self._pending_cursor_row = min(cursor_row, len(self.items) - 1)
        self._load_items()

        self.notify("Song removed")

    def action_edit_transition(self) -> None:
        """Edit transition for selected item."""
        item = self._get_selected_item()

        if not item:
            self.notify("No song selected", severity="error")
            return

        self.state.select_item(item)
        self.app.navigate_to(AppScreen.TRANSITION_DETAIL)

    def action_preview(self) -> None:
        """Preview the transition from previous song to selected song."""
        if len(self.items) < 2:
            self.notify("Need at least 2 songs to preview transition", severity="warning")
            return

        to_item = self._get_selected_item()
        if not to_item:
            self.notify("No song selected", severity="error")
            return

        # Find the index of the selected item
        try:
            to_index = self.items.index(to_item)
        except ValueError:
            self.notify("Selected song not found in songset", severity="error")
            return

        # Can't preview transition for the first song (no previous song)
        if to_index == 0:
            self.notify("First song has no transition to preview", severity="warning")
            return

        from_item = self.items[to_index - 1]
        self.notify("Generating transition preview...")
        self.run_worker(
            lambda: self._preview_worker(from_item, to_item),
            exclusive=True,
            group="preview",
            thread=True,
        )

    def _preview_worker(self, from_item: SongsetItem, to_item: SongsetItem) -> None:
        """Worker: generate transition preview audio (Fix 9)."""
        try:
            preview_path = self.audio_engine.preview_transition(from_item, to_item)
            if preview_path:
                self.app.call_from_thread(self.playback.play, preview_path)
                self.app.call_from_thread(
                    self.notify,
                    f"Playing transition: {from_item.song_title} → {to_item.song_title}",
                )
            else:
                self.app.call_from_thread(
                    self.notify, "Failed to generate preview", severity="error"
                )
        except Exception as e:
            logger.error(f"Error generating preview: {e}")
            self.app.call_from_thread(
                self.notify, f"Error generating preview: {e}", severity="error"
            )

    def action_lyrics_preview(self) -> None:
        """Open lyrics preview for the selected song (Fix 7: route through navigate_to)."""
        item = self._get_selected_item()
        if not item:
            self.notify("No song selected", severity="warning")
            return

        if not item.recording_hash_prefix:
            self.notify("Song has no recording", severity="warning")
            return

        self.notify("Loading lyrics...")
        self.run_worker(
            lambda: self._lyrics_preview_worker(item),
            exclusive=True,
            group="lyrics_preview",
            thread=True,
        )

    def _lyrics_preview_worker(self, item: SongsetItem) -> None:
        """Worker: check LRC availability then navigate on main thread."""
        lrc_path = self.asset_cache.download_lrc(item.recording_hash_prefix)
        if not lrc_path:
            self.app.call_from_thread(
                self.notify, "No lyrics available for this song", severity="warning"
            )
            return
        self.state.selected_preview_item = item
        self.app.call_from_thread(self.app.navigate_to, AppScreen.LYRICS_PREVIEW)

    def action_toggle_playback(self) -> None:
        """Toggle playback of the currently selected song with spacebar."""
        if self.playback.is_playing:
            self.playback.stop()
            self.notify("Playback stopped")
            return

        item = self._get_selected_item()
        if not item:
            self.notify("No song selected", severity="error")
            return

        if not item.recording_hash_prefix:
            self.notify("Selected song has no audio recording", severity="error")
            return

        self.run_worker(
            lambda: self._play_item_worker(item),
            exclusive=True,
            group="playback",
            thread=True,
        )

    def _play_item_worker(self, item: SongsetItem) -> None:
        """Worker: download audio then play on main thread (Fix 9)."""
        try:
            audio_path = self.asset_cache.download_audio(item.recording_hash_prefix)
            if audio_path:
                self.app.call_from_thread(self.playback.play, audio_path)
                self.app.call_from_thread(self.notify, f"Playing: {item.song_title}")
            else:
                self.app.call_from_thread(
                    self.notify, "Failed to download audio file", severity="error"
                )
        except Exception as e:
            logger.error(f"Error playing audio: {e}")
            self.app.call_from_thread(self.notify, f"Error playing audio: {e}", severity="error")

    def action_skip_forward(self) -> None:
        """Skip forward 10 seconds in current playback."""
        if not self.playback.is_playing and not self.playback.is_paused:
            return
        self.playback.skip_forward(10.0)

    def action_skip_backward(self) -> None:
        """Skip backward 10 seconds in current playback."""
        if not self.playback.is_playing and not self.playback.is_paused:
            return
        self.playback.skip_backward(10.0)

    def action_export(self) -> None:
        """Export the songset."""
        if len(self.items) < 1:
            self.notify("Need at least 1 song to export", severity="error")
            return

        self.app.navigate_to(AppScreen.EXPORT_PROGRESS)

    def action_edit_info(self) -> None:
        """Focus the name input to edit songset info."""
        name_input = self.query_one("#input_name", Input)
        name_input.focus()

    def action_back(self) -> None:
        """Go back to songset list."""
        logger.info("Action: back (from songset editor)")
        self.app.navigate_back()

    def action_move_up(self) -> None:
        """Move selected song up in the list."""
        item = self._get_selected_item()
        if not item:
            self.notify("No song selected", severity="error")
            return

        # Find current index
        try:
            current_index = self.items.index(item)
        except ValueError:
            self.notify("Selected song not found in list", severity="error")
            return

        if current_index == 0:
            self.notify("Already at the top", severity="info")
            return

        # Save reference to table
        table = self.query_one("#items_table", DataTable)

        # Reorder in database (new_position is current_index - 1)
        success = self.songset_client.reorder_item(item.id, current_index - 1)
        if success:
            self._pending_cursor_row = current_index - 1
            self._load_items()
            self.notify(f"Moved '{item.song_title}' up")
        else:
            self.notify("Failed to move song", severity="error")

    def action_move_down(self) -> None:
        """Move selected song down in the list."""
        item = self._get_selected_item()
        if not item:
            self.notify("No song selected", severity="error")
            return

        try:
            current_index = self.items.index(item)
        except ValueError:
            self.notify("Selected song not found in list", severity="error")
            return

        if current_index >= len(self.items) - 1:
            self.notify("Already at the bottom", severity="info")
            return

        table = self.query_one("#items_table", DataTable)

        success = self.songset_client.reorder_item(item.id, current_index + 1)
        if success:
            self._pending_cursor_row = current_index + 1
            self._load_items()
            self.notify(f"Moved '{item.song_title}' down")
        else:
            self.notify("Failed to move song", severity="error")

    def action_noop(self) -> None:
        """No-op action to disable inherited bindings."""
        pass
