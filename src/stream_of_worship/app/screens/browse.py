"""Browse screen.

Allows browsing and searching the song catalog to add songs to a songset.
"""

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
            yield table

            with Horizontal(id="buttons"):
                yield Button("Add to Songset", id="btn_add", variant="primary")
                yield Button("Preview", id="btn_preview")
                yield Button("Back", id="btn_back")

        yield Footer()

    def on_mount(self) -> None:
        """Handle mount event."""
        self._load_songs()

    def _load_songs(self, query: str = "") -> None:
        """Load and display songs.

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

    def action_add_to_songset(self) -> None:
        """Add selected song to current songset."""
        if not self.state.selected_song:
            return

        if not self.state.selected_songset:
            self.notify("No songset selected", severity="error")
            return

        song = self.state.selected_song
        recording = song.recording

        self.songset_client.add_item(
            songset_id=self.state.selected_songset.id,
            song_id=song.song.id,
            recording_hash_prefix=recording.hash_prefix if recording else None,
        )

        self.notify(f"Added '{song.song.title}' to songset")

    def action_preview(self) -> None:
        """Preview selected song."""
        if not self.state.selected_song:
            return

        recording = self.state.selected_song.recording
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
