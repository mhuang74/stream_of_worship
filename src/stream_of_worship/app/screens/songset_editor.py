"""Songset editor screen.

Allows editing a songset: reordering songs, adjusting transitions, previewing.
"""

from typing import Optional

from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, DataTable, Footer, Header, Input, Label, Static

from stream_of_worship.app.db.models import SongsetItem
from stream_of_worship.app.db.songset_client import SongsetClient
from stream_of_worship.app.logging_config import get_logger
from stream_of_worship.app.services.catalog import CatalogService
from stream_of_worship.app.services.playback import PlaybackService
from stream_of_worship.app.state import AppScreen, AppState

logger = get_logger(__name__)


class SongsetEditorScreen(Screen):
    """Screen for editing a songset."""

    BINDINGS = [
        ("a", "add_songs", "Add Songs"),
        ("r", "remove_song", "Remove"),
        ("e", "edit_transition", "Edit Transition"),
        ("p", "preview", "Preview"),
        ("x", "export", "Export"),
        ("i", "edit_info", "Edit Info"),
        ("escape", "back", "Back"),
    ]

    def __init__(
        self,
        state: AppState,
        songset_client: SongsetClient,
        catalog: CatalogService,
        playback: PlaybackService,
    ):
        """Initialize the screen.

        Args:
            state: Application state
            songset_client: Songset database client
            catalog: Catalog service
            playback: Playback service
        """
        super().__init__()
        self.state = state
        self.songset_client = songset_client
        self.catalog = catalog
        self.playback = playback
        self.items: list[SongsetItem] = []

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
            table.add_columns("#", "Song", "Key", "Duration", "Gap", "Transition")
            table.cursor_type = "row"
            yield table

            with Horizontal(id="buttons"):
                yield Button("Add Songs", id="btn_add", variant="primary")
                yield Button("Remove", id="btn_remove")
                yield Button("Edit Gap", id="btn_edit")
                yield Button("Preview", id="btn_preview")
                yield Button("Export", id="btn_export", variant="success")
                yield Button("Back", id="btn_back")

        yield Footer()

    def on_mount(self) -> None:
        """Handle mount event."""
        logger.info(
            f"SongsetEditorScreen mounted (songset: {self.state.selected_songset.id if self.state.selected_songset else 'None'})"
        )
        self._refresh()

        # Focus the song list, not the name input
        self.call_after_refresh(self._focus_song_list)

        # Listen for state changes
        self.state.add_listener("selected_songset", lambda _: self._refresh())

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
        """Load and display songset items."""
        if not self.state.selected_songset:
            return

        self.items = self.songset_client.get_items(self.state.selected_songset.id)
        self.state.update_songset_items(self.items)

        table = self.query_one("#items_table", DataTable)
        table.clear()

        for i, item in enumerate(self.items):
            gap_text = f"{item.gap_beats} beats" if item.gap_beats else "No gap"
            transition_text = "Crossfade" if item.crossfade_enabled else "Gap"

            table.add_row(
                str(i + 1),
                item.song_title or "Unknown",
                item.display_key or "-",
                item.formatted_duration,
                gap_text,
                transition_text,
                key=item.id,
            )

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
        if self.state.selected_item:
            return self.state.selected_item

        # If no explicit selection, use cursor row
        table = self.query_one("#items_table", DataTable)
        if table.cursor_row is not None:
            rows = list(table.rows.keys())
            if table.cursor_row < len(rows):
                item_id = rows[table.cursor_row].value
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
        self._load_items()

        # Restore cursor position (stay at same index, or last item if removed last)
        if cursor_row is not None and len(self.items) > 0:
            new_cursor = min(cursor_row, len(self.items) - 1)
            table.move_cursor(row=new_cursor)

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
        """Preview the songset."""
        if len(self.items) < 2:
            self.notify("Need at least 2 songs to preview transition", severity="warning")
            return

        # Generate a temporary preview
        self.notify("Generating preview...")

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
