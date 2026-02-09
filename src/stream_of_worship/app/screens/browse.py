"""Browse screen.

Allows browsing and searching the song catalog to add songs to a songset.
"""

from typing import Optional

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, DataTable, Footer, Header, Input, Label, Static

from stream_of_worship.app.db.songset_client import SongsetClient
from stream_of_worship.app.services.catalog import CatalogService, SongWithRecording
from stream_of_worship.app.state import AppScreen, AppState


class BrowseScreen(Screen):
    """Screen for browsing and searching songs."""

    BINDINGS = [
        ("s", "add_to_songset", "Add to Songset"),
        ("space", "toggle_playback", "Play/Stop"),
        ("f", "focus_search", "Search"),
        ("escape", "back", "Back"),
        ("q", "quit", "Quit"),
    ]

    def __init__(
        self,
        state: AppState,
        catalog: CatalogService,
        songset_client: SongsetClient,
    ):
        """Initialize the screen.

        Args:
            state: Application state
            catalog: Catalog service
            songset_client: Songset database client
        """
        super().__init__()
        self.state = state
        self.catalog = catalog
        self.songset_client = songset_client
        self.songs: list[SongWithRecording] = []

    def compose(self) -> ComposeResult:
        """Compose the screen layout."""
        yield Header()

        with Vertical():
            yield Label("[bold]Browse Songs[/bold]", id="title")

            with Horizontal(id="search_row"):
                yield Input(placeholder="Search songs...", id="search_input")
                yield Button("Search", id="btn_search")
                yield Button("Clear", id="btn_clear")

            table = DataTable(id="song_table")
            table.add_columns("Title", "Key", "Tempo", "Duration", "Album")
            table.cursor_type = "row"
            yield table

            with Vertical(id="empty_state", classes="hidden"):
                yield Static("📭", id="empty_icon")
                yield Label("[bold]Catalog Empty[/bold]", id="empty_title")
                yield Static("Loading...", id="empty_message")
                yield Button("Refresh", id="btn_refresh", variant="default")

            with Horizontal(id="buttons"):
                yield Button("Add to Songset", id="btn_add", variant="primary")
                yield Button("Preview", id="btn_preview")
                yield Button("Back", id="btn_back")

        yield Footer()

    def on_mount(self) -> None:
        """Handle mount event."""
        self._load_songs()

    def _show_empty_state(self, message: str) -> None:
        """Show empty state with custom message."""
        empty_container = self.query_one("#empty_state")
        empty_container.remove_class("hidden")

        message_widget = self.query_one("#empty_message", Static)
        message_widget.update(message)

        table = self.query_one("#song_table", DataTable)
        table.add_class("hidden")

    def _hide_empty_state(self) -> None:
        """Hide empty state and show table."""
        empty_container = self.query_one("#empty_state")
        empty_container.add_class("hidden")

        table = self.query_one("#song_table", DataTable)
        table.remove_class("hidden")

    def _load_songs(self, query: str = "") -> None:
        """Load and display songs with empty state handling.

        Args:
            query: Optional search query
        """
        if query:
            self.songs = self.catalog.search_songs_with_recordings(query, limit=50)
        else:
            self.songs = self.catalog.list_songs_with_recordings(
                only_analyzed=True, limit=50
            )

        table = self.query_one("#song_table", DataTable)
        table.clear()

        # Check if catalog is empty
        if not self.songs:
            health = self.catalog.get_catalog_health()
            self._show_empty_state(health["guidance"])
            return

        # Hide empty state and show results
        self._hide_empty_state()

        for song in self.songs:
            table.add_row(
                song.song.title,
                song.display_key,
                f"{int(song.tempo_bpm)}" if song.tempo_bpm else "-",
                song.formatted_duration,
                song.song.album_name or "-",
                key=song.song.id,
            )

    def on_input_changed(self, event: Input.Changed) -> None:
        """Handle search input changes."""
        if event.input.id == "search_input":
            self.state.set_search_query(event.value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses."""
        button_id = event.button.id

        if button_id == "btn_search":
            search_input = self.query_one("#search_input", Input)
            self._load_songs(search_input.value)
        elif button_id == "btn_clear":
            search_input = self.query_one("#search_input", Input)
            search_input.value = ""
            self._load_songs()
        elif button_id == "btn_refresh":
            self._load_songs()
            self.notify("Catalog refreshed")
        elif button_id == "btn_add":
            self.action_add_to_songset()
        elif button_id == "btn_preview":
            self.action_preview()
        elif button_id == "btn_back":
            self.app.navigate_back()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Handle row selection."""
        song_id = event.row_key.value
        song = self.catalog.get_song_with_recording(song_id)
        if song:
            self.state.select_song(song)

    def _get_selected_song(self) -> Optional[SongWithRecording]:
        """Get the currently highlighted song based on cursor position."""
        table = self.query_one("#song_table", DataTable)

        # Always use cursor row - that's what the user sees highlighted
        if table.cursor_row is not None:
            rows = list(table.rows.keys())
            if table.cursor_row < len(rows):
                song_id = rows[table.cursor_row].value
                return self.catalog.get_song_with_recording(song_id)

        # Fallback to explicit selection if cursor not available
        return self.state.selected_song

    def action_add_to_songset(self) -> None:
        """Add selected song to current songset."""
        song = self._get_selected_song()

        if not song:
            self.notify("No song selected", severity="warning")
            return

        # Update state to reflect selection
        self.state.select_song(song)

        # If no songset selected, create one automatically
        if not self.state.selected_songset:
            songset = self.songset_client.create_songset(
                name="New Songset",
                description="",
            )
            self.state.select_songset(songset)
            self.notify(f"Created new songset: {songset.name}")

        recording = song.recording

        if not recording:
            self.notify("No recording available for this song", severity="error")
            return

        self.songset_client.add_item(
            songset_id=self.state.selected_songset.id,
            song_id=song.song.id,
            recording_hash_prefix=recording.hash_prefix,
        )

        self.notify(f"Added '{song.song.title}' to songset")

    def action_preview(self) -> None:
        """Preview selected song."""
        song = self._get_selected_song()

        if not song:
            self.notify("No song selected", severity="warning")
            return

        # Update state to reflect selection
        self.state.select_song(song)

        recording = song.recording
        if not recording:
            self.notify("No recording available for preview", severity="error")
            return

        # Download and play
        audio_path = self.app.asset_cache.download_audio(recording.hash_prefix)
        if audio_path:
            self.app.playback.play(audio_path)

    def action_toggle_playback(self) -> None:
        """Toggle playback of the currently selected song with spacebar."""
        if self.app.playback.is_playing:
            self.app.playback.stop()
            self.notify("Playback stopped")
            return

        song = self._get_selected_song()
        if not song:
            self.notify("No song selected", severity="warning")
            return

        # Update state to reflect selection
        self.state.select_song(song)

        recording = song.recording
        if not recording:
            self.notify("No recording available for preview", severity="error")
            return

        # Download and play
        audio_path = self.app.asset_cache.download_audio(recording.hash_prefix)
        if audio_path:
            self.app.playback.play(audio_path)

    def action_focus_search(self) -> None:
        """Focus the search input."""
        self.query_one("#search_input", Input).focus()

    def action_back(self) -> None:
        """Go back."""
        self.app.navigate_back()
