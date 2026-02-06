"""Songset list screen.

Displays all user-created songsets with options to create, edit, or delete.
"""

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, DataTable, Footer, Header, Input, Label, Static

from stream_of_worship.app.db.models import Songset
from stream_of_worship.app.db.songset_client import SongsetClient
from stream_of_worship.app.state import AppScreen, AppState


class SongsetListScreen(Screen):
    """Screen for listing and managing songsets."""

    BINDINGS = [
        ("n", "new_songset", "New Songset"),
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
            yield Label("Press 'n' to create a new songset, Enter to edit", id="subtitle")

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
        self._load_songsets()

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
        # For simplicity, create with default name and let user edit
        songset = self.songset_client.create_songset(
            name="New Songset",
            description="",
        )
        self.state.select_songset(songset)
        self.app.navigate_to(AppScreen.SONGSET_EDITOR)

    def action_edit_songset(self) -> None:
        """Edit selected songset."""
        table = self.query_one("#songset_table", DataTable)
        if table.cursor_row is not None:
            row_key = table.get_row_at(table.cursor_row)[0]
            songset = self.songset_client.get_songset(row_key)
            if songset:
                self.state.select_songset(songset)
                self.app.navigate_to(AppScreen.SONGSET_EDITOR)

    def action_delete_songset(self) -> None:
        """Delete selected songset."""
        table = self.query_one("#songset_table", DataTable)
        if table.cursor_row is not None:
            # Get selected songset
            rows = list(table.rows.keys())
            if table.cursor_row < len(rows):
                songset_id = rows[table.cursor_row]
                self.songset_client.delete_songset(songset_id)
                self._load_songsets()
