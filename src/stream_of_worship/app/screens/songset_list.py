"""Songset list screen.

Displays all user-created songsets with options to create, edit, or delete.
"""

from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, DataTable, Footer, Header, Input, Label, Static

from stream_of_worship.app.db.models import Songset
from stream_of_worship.app.db.songset_client import SongsetClient
from stream_of_worship.app.logging_config import get_logger
from stream_of_worship.app.state import AppScreen, AppState

logger = get_logger(__name__)


class SongsetListScreen(Screen):
    """Screen for listing and managing songsets."""

    BINDINGS = [
        ("n", "new_songset", "New Songset"),
        ("e", "edit_songset", "Edit"),
        ("d", "delete_songset", "Delete"),
        ("enter", "edit_songset", "Edit"),
        ("q", "quit", "Quit"),
    ]

    def __init__(self, state: AppState, songset_client: SongsetClient):
        """Initialize the screen.

        Args:
            state: Application state
            songset_client: Songset database client
        """
        super().__init__()
        self.state = state
        self.songset_client = songset_client
        self.songsets: list[Songset] = []

    def compose(self) -> ComposeResult:
        """Compose the screen layout."""
        yield Header()

        with Vertical():
            yield Label("[bold]Your Songsets[/bold]", id="title")
            yield Label("Press 'n' for new, 'e' or Enter to edit, 'd' to delete", id="subtitle")

            table = DataTable(id="songset_table")
            table.add_columns("Name", "Description", "Songs", "Updated")
            yield table

            with Horizontal(id="buttons"):
                yield Button("New Songset", id="btn_new", variant="primary")
                yield Button("Edit", id="btn_edit")
                yield Button("Delete", id="btn_delete")
                yield Button("Quit", id="btn_quit")

        yield Footer()

    def on_mount(self) -> None:
        """Handle mount event."""
        logger.info("SongsetListScreen mounted")
        self._load_songsets()


    def on_screen_resume(self, event: events.ScreenResume) -> None:
        """Handle screen resume event (when another screen is popped)."""
        logger.info("SongsetListScreen resumed (screen popped from above)")
        self._load_songsets()
        # Use call_after_refresh to ensure focus is set after screen is fully rendered
        logger.debug("Scheduling focus restoration after refresh")
        self.call_after_refresh(self._restore_focus)

    def _load_songsets(self) -> None:
        """Load and display songsets."""
        self.songsets = self.songset_client.list_songsets()
        table = self.query_one("#songset_table", DataTable)
        table.clear()

        for songset in self.songsets:
            item_count = self.songset_client.get_item_count(songset.id)
            updated = songset.updated_at or "Never"
            if updated != "Never":
                # Format timestamp
                try:
                    from datetime import datetime
                    dt = datetime.fromisoformat(updated.replace('Z', '+00:00'))
                    updated = dt.strftime("%Y-%m-%d %H:%M")
                except Exception:
                    pass

            table.add_row(
                songset.name,
                songset.description or "",
                str(item_count),
                updated,
                key=songset.id,
            )

    def _restore_focus(self) -> None:
        """Restore focus to the table after screen resume."""
        table = self.query_one("#songset_table", DataTable)
        logger.debug(
            f"Restoring focus: rows={len(table.rows)}, "
            f"cursor_row={table.cursor_row}, "
            f"has_focus={table.has_focus}"
        )
        if len(table.rows) > 0:
            # Ensure cursor is on first row if not set
            if table.cursor_row is None:
                table.cursor_row = 0
                logger.debug("Set cursor to row 0")
            # Force focus to the table
            table.focus()
            logger.info(
                f"Focus restored to table: cursor_row={table.cursor_row}, "
                f"has_focus={table.has_focus}"
            )

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Handle row selection."""
        songset_id = event.row_key.value
        songset = self.songset_client.get_songset(songset_id)
        if songset:
            self.state.select_songset(songset)
            self.app.navigate_to(AppScreen.SONGSET_EDITOR)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses."""
        button_id = event.button.id

        if button_id == "btn_new":
            self.action_new_songset()
        elif button_id == "btn_edit":
            self.action_edit_songset()
        elif button_id == "btn_delete":
            self.action_delete_songset()
        elif button_id == "btn_quit":
            self.app.action_quit()

    def action_new_songset(self) -> None:
        """Create a new songset."""
        logger.info("Action: new_songset")
        # For simplicity, create with default name and let user edit
        songset = self.songset_client.create_songset(
            name="New Songset",
            description="",
        )
        logger.debug(f"Created songset: {songset.id}")
        self.state.select_songset(songset)
        self.app.navigate_to(AppScreen.SONGSET_EDITOR)

    def action_edit_songset(self) -> None:
        """Edit selected songset."""
        logger.info("Action: edit_songset")
        table = self.query_one("#songset_table", DataTable)
        logger.debug(f"Table state: cursor_row={table.cursor_row}, rows={len(table.rows)}, has_focus={table.has_focus}")
        if table.cursor_row is not None:
            # Get selected songset ID from row key
            rows = list(table.rows.keys())
            if table.cursor_row < len(rows):
                songset_id = rows[table.cursor_row].value
                logger.info(f"Editing songset: {songset_id}")
                songset = self.songset_client.get_songset(songset_id)
                if songset:
                    self.state.select_songset(songset)
                    self.app.navigate_to(AppScreen.SONGSET_EDITOR)
                else:
                    logger.warning(f"Songset not found: {songset_id}")
        else:
            logger.warning("Cannot edit: no cursor row")
            self.notify("No songset selected", severity="warning")

    def action_delete_songset(self) -> None:
        """Delete selected songset."""
        logger.info("Action: delete_songset")
        table = self.query_one("#songset_table", DataTable)
        if table.cursor_row is not None:
            # Get selected songset
            rows = list(table.rows.keys())
            if table.cursor_row < len(rows):
                songset_id = rows[table.cursor_row].value
                logger.info(f"Deleting songset: {songset_id}")
                songset = self.songset_client.get_songset(songset_id)
                if songset:
                    self.songset_client.delete_songset(songset_id)
                    logger.debug(f"Deleted songset: {songset.name}")
                    self.notify(f"Deleted songset '{songset.name}'")
                    self._load_songsets()
        else:
            logger.warning("Cannot delete: no cursor row")
            self.notify("No songset selected", severity="warning")
