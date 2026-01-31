"""Playlist screen for building multi-song worship sets."""

from textual.containers import Horizontal, Vertical, Container
from textual.widgets import (
    Static,
    DataTable,
    Button,
    Input,
    Label,
    Footer,
)

from stream_of_worship.tui.state import AppState, ActiveScreen
from stream_of_worship.tui.models.playlist import Playlist
from stream_of_worship.tui.services.catalog import SongCatalogLoader
from stream_of_worship.tui.services.playback import PlaybackService
from stream_of_worship.tui.services.generation import TransitionGenerationService


class PlaylistScreen(Vertical):
    """Screen for building multi-song playlists."""

    def __init__(
        self,
        state: AppState,
        catalog: SongCatalogLoader,
        playback: PlaybackService,
        generation: TransitionGenerationService,
    ):
        """Initialize playlist screen.

        Args:
            state: Application state
            catalog: Song catalog
            playback: Audio playback service
            generation: Transition generation service
        """
        super().__init__()

        self.state = state
        self.catalog = catalog
        self.playback = playback
        self.generation = generation

        self._build_ui()

    def _build_ui(self):
        """Build the screen layout."""
        # Header with playlist name
        header_container = Horizontal()
        header_container.mount(Static("Playlist:"))
        self.name_input = Input(
            value=self.state.playlist_name,
            placeholder="Playlist name",
        )
        self.name_input.on_change = self._on_name_change
        header_container.mount(self.name_input)

        self.duration_label = Label("0:00")
        header_container.mount(self.duration_label)
        self.mount(header_container)

        # Main content: song library | playlist
        main_container = Horizontal()

        # Left panel: Song library
        library_panel = Vertical(classes="panel")
        library_panel.mount(Static("Song Library", classes="panel-title"))

        self.search_input = Input(placeholder="Search...")
        library_panel.mount(self.search_input)

        self.library_table = DataTable()
        self.library_table.add_column("Song", key="filename")
        self.library_table.add_column("BPM", key="tempo")
        self.library_table.add_column("Key", key="full_key")
        self.library_table.add_column("Duration", key="duration")
        library_panel.mount(self.library_table)

        # Library buttons
        lib_buttons = Horizontal()
        self.add_btn = Button("Add", id="add-btn")
        self.add_btn.on_press = self._on_add_song
        lib_buttons.mount(self.add_btn)

        self.preview_btn = Button("Preview", id="preview-btn")
        self.preview_btn.on_press = self._on_preview
        lib_buttons.mount(self.preview_btn)

        library_panel.mount(lib_buttons)

        # Right panel: Playlist
        playlist_panel = Vertical(classes="panel")
        playlist_panel.mount(Static("Current Playlist", classes="panel-title"))

        self.playlist_table = DataTable()
        self.playlist_table.add_column("#", key="index")
        self.playlist_table.add_column("Song", key="song")
        self.playlist_table.add_column("Transition", key="transition")
        self.playlist_table.add_column("Duration", key="duration")
        playlist_panel.mount(self.playlist_table)

        # Playlist buttons
        pl_buttons = Horizontal()

        self.remove_btn = Button("Remove", id="remove-btn")
        self.remove_btn.on_press = self._on_remove_song
        pl_buttons.mount(self.remove_btn)

        self.move_up_btn = Button("Move Up", id="moveup-btn")
        self.move_up_btn.on_press = self._on_move_up
        pl_buttons.mount(self.move_up_btn)

        self.move_down_btn = Button("Move Down", id="movedown-btn")
        self.move_down_btn.on_press = self._on_move_down
        pl_buttons.mount(self.move_down_btn)

        self.clear_btn = Button("Clear", id="clear-btn")
        self.clear_btn.on_press = self._on_clear
        pl_buttons.mount(self.clear_btn)

        playlist_panel.mount(pl_buttons)

        # Export buttons
        export_buttons = Horizontal()
        self.export_audio_btn = Button("Export Audio (e)", id="export-audio-btn")
        self.export_audio_btn.on_press = self._on_export_audio
        export_buttons.mount(self.export_audio_btn)

        self.export_video_btn = Button("Export Video (E)", id="export-video-btn")
        self.export_video_btn.on_press = self._on_export_video
        export_buttons.mount(self.export_video_btn)

        playlist_panel.mount(export_buttons)

        # Add panels to main container
        main_container.mount(library_panel)
        main_container.mount(playlist_panel)

        self.mount(main_container)

        # Status
        self.status_label = Static("Ready")
        self.mount(self.status_label)

        # Refresh tables
        self._refresh_library_table()
        self._refresh_playlist_table()

    def _refresh_library_table(self):
        """Refresh the song library table."""
        self.library_table.clear()
        songs = self.catalog.get_all_songs()

        for song in songs:
            self.library_table.add_row(
                song.filename,
                str(int(song.tempo)),
                song.full_key,
                song.format_duration(),
            )

    def _refresh_playlist_table(self):
        """Refresh the playlist table."""
        self.playlist_table.clear()

        for i, song_id in enumerate(self.state.playlist_items):
            song = self.catalog.get_song(song_id)
            if song:
                # Get transition info if available
                transition_str = "→"
                if i < len(self.state.playlist_items) - 1:
                    transition_str = "Gap/Crossfade"

                self.playlist_table.add_row(
                    str(i + 1),
                    song.filename,
                    transition_str,
                    song.format_duration(),
                )

        # Update duration display
        total_seconds = sum(
            self.catalog.get_song(sid).duration if self.catalog.get_song(sid) else 0
            for sid in self.state.playlist_items
        )
        minutes = int(total_seconds // 60)
        seconds = int(total_seconds % 60)
        self.duration_label.update(f"{minutes}:{seconds:02d}")

    def _on_name_change(self, value: str) -> None:
        """Handle playlist name change."""
        self.state.playlist_name = value

    def _on_add_song(self) -> None:
        """Handle add song button press."""
        # In a full implementation, this would add the selected song
        # from the library to the playlist
        self.status_label.update("Add song: Select from library")

    def _on_remove_song(self) -> None:
        """Handle remove song button press."""
        if self.state.selected_playlist_index is None:
            self.status_label.update("Error: No song selected in playlist")
            return

        removed = self.state.remove_song_from_playlist(
            self.state.selected_playlist_index
        )
        if removed:
            self._refresh_playlist_table()
            self.status_label.update(f"Removed song at position {self.state.selected_playlist_index + 1}")
        else:
            self.status_label.update("Error: Failed to remove song")

    def _on_move_up(self) -> None:
        """Handle move up button press."""
        if self.state.selected_playlist_index is None:
            return

        if self.state.selected_playlist_index <= 0:
            return

        self.state.move_playlist_song(
            self.state.selected_playlist_index,
            self.state.selected_playlist_index - 1
        )
        self._refresh_playlist_table()
        self.status_label.update("Moved song up")

    def _on_move_down(self) -> None:
        """Handle move down button press."""
        if self.state.selected_playlist_index is None:
            return

        if self.state.selected_playlist_index >= len(self.state.playlist_items) - 1:
            return

        self.state.move_playlist_song(
            self.state.selected_playlist_index,
            self.state.selected_playlist_index + 1
        )
        self._refresh_playlist_table()
        self.status_label.update("Moved song down")

    def _on_clear(self) -> None:
        """Handle clear button press."""
        self.state.clear_playlist()
        self._refresh_playlist_table()
        self.status_label.update("Playlist cleared")

    def _on_export_audio(self) -> None:
        """Handle export audio button press."""
        if not self.state.playlist_items:
            self.status_label.update("Error: Playlist is empty")
            return

        self.status_label.update("Exporting audio... Playlist export not yet implemented.")

    def _on_export_video(self) -> None:
        """Handle export video button press."""
        if not self.state.playlist_items:
            self.status_label.update("Error: Playlist is empty")
            return

        self.status_label.update("Exporting video... Video export not yet implemented.")

    def _on_preview(self) -> None:
        """Handle preview button press."""
        self.status_label.update("Preview: Select a song first")

    def on_key(self, event) -> None:
        """Handle keyboard shortcuts.

        Args:
            event: Key event
        """
        # Handle escape to stop playback
        if event.key == "escape":
            if self.playback.is_playing or self.playback.is_paused:
                self.playback.stop()
                self.status_label.update("Stopped")

        # Handle 'e' to export audio
        if event.key == "e":
            self._on_export_audio()

        # Handle 'E' to export video
        if event.key == "E":
            self._on_export_video()

        # Handle 'a' to add song
        if event.key == "a":
            self._on_add_song()

        # Handle 'd' to delete song
        if event.key == "d":
            self._on_remove_song()

        # Handle arrow keys for navigation
        if event.key == "up":
            if self.state.selected_playlist_index is not None:
                self.state.selected_playlist_index = max(
                    0, self.state.selected_playlist_index - 1
                )
            else:
                self.state.selected_playlist_index = 0
            self._refresh_playlist_table()

        elif event.key == "down":
            if self.state.selected_playlist_index is not None:
                self.state.selected_playlist_index = min(
                    len(self.state.playlist_items) - 1,
                    self.state.selected_playlist_index + 1,
                )
            else:
                self.state.selected_playlist_index = 0
            self._refresh_playlist_table()

        # Handle page up/down for library navigation
        # (would be implemented in full version)

        return super().on_key(event)
