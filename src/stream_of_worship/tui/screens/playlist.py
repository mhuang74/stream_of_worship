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
from typing import Optional

from stream_of_worship.tui.state import AppState, ActiveScreen
from stream_of_worship.tui.models.playlist import Playlist, PlaylistItem
from stream_of_worship.tui.models.transition import TransitionParams
from stream_of_worship.tui.models.section import Section
from stream_of_worship.tui.services.catalog import SongCatalogLoader
from stream_of_worship.tui.services.playback import PlaybackService
from stream_of_worship.tui.services.generation import TransitionGenerationService


class PlaylistScreen(Vertical):
    """Screen for building multi-song playlists.

    Layout:
    - Header: Playlist name and duration
    - Left Panel: Song Library (with search)
    - Right Panel: Current Playlist (with move/remove controls)
    - Bottom Panel: Transition Editor (context-aware)
    - Footer: Keyboard shortcuts
    """

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

        # Working playlist (transient, not persisted)
        self._playlist: Optional[Playlist] = None

        # UI component references
        self.name_input: Optional[Input] = None
        self.duration_label: Optional[Label] = None
        self.library_table: Optional[DataTable] = None
        self.playlist_table: Optional[DataTable] = None
        self.status_label: Optional[Static] = None

        # Transition editor components
        self.transition_type_input: Optional[Input] = None
        self.overlap_input: Optional[Input] = None
        self.section_start_select: Optional[Button] = None
        self.section_end_select: Optional[Button] = None

        # Library state
        self._library_page = 0
        self._library_page_size = 50
        self._search_query = ""

        # Playlist state
        self._previewing_index: Optional[int] = None

        self._build_ui()

    def _build_ui(self) -> None:
        """Build screen layout."""
        # Header: Playlist name and duration
        header_container = Horizontal()
        header_container.mount(Static("Playlist: "))

        self.name_input = Input(
            value=self.state.playlist_name,
            placeholder="Playlist name",
            id="playlist-name-input",
        )
        self.name_input.on_change = self._on_name_change
        header_container.mount(self.name_input)

        self.duration_label = Label("0:00", id="duration-label")
        header_container.mount(self.duration_label)

        # Save playlist button
        save_btn = Button("Save", id="save-btn")
        save_btn.on_press = self._on_save_playlist
        header_container.mount(save_btn)

        # Load playlist button
        load_btn = Button("Load", id="load-btn")
        load_btn.on_press = self._on_load_playlist
        header_container.mount(load_btn)

        self.mount(header_container)

        # Main content: song library | playlist
        main_container = Horizontal()

        # Left panel: Song library
        library_panel = Vertical(classes="panel")
        library_panel.mount(Static("Song Library", classes="panel-title"))

        self.search_input = Input(
            placeholder="Search...",
            id="search-input",
        )
        self.search_input.on_change = self._on_search_change
        library_panel.mount(self.search_input)

        self.library_table = DataTable()
        self.library_table.add_column("Song", key="filename")
        self.library_table.add_column("BPM", key="tempo")
        self.library_table.add_column("Key", key="full_key")
        self.library_table.add_column("Duration", key="duration")
        self.library_table.on_select = self._on_library_select
        library_panel.mount(self.library_table)

        # Library navigation buttons
        lib_nav = Horizontal()
        prev_page_btn = Button("Previous Page", id="prev-page-btn")
        prev_page_btn.on_press = self._on_prev_page
        lib_nav.mount(prev_page_btn)

        page_label = Label(f"Page {self._library_page + 1}", id="page-label")
        lib_nav.mount(page_label)

        next_page_btn = Button("Next Page", id="next-page-btn")
        next_page_btn.on_press = self._on_next_page
        lib_nav.mount(next_page_btn)

        library_panel.mount(lib_nav)

        # Library action buttons
        lib_actions = Horizontal()
        self.add_btn = Button("Add (A)", id="add-btn")
        self.add_btn.on_press = self._on_add_song
        lib_actions.mount(self.add_btn)

        self.lib_preview_btn = Button("Preview (Space)", id="lib-preview-btn")
        self.lib_preview_btn.on_press = self._on_library_preview
        lib_actions.mount(self.lib_preview_btn)

        library_panel.mount(lib_actions)

        # Right panel: Playlist
        playlist_panel = Vertical(classes="panel")
        playlist_panel.mount(Static("Current Playlist", classes="panel-title"))

        self.playlist_table = DataTable()
        self.playlist_table.add_column("#", key="index")
        self.playlist_table.add_column("Song", key="song")
        self.playlist_table.add_column("Sections", key="sections")
        self.playlist_table.add_column("Transition", key="transition")
        self.playlist_table.add_column("Duration", key="duration")
        self.playlist_table.on_select = self._on_playlist_select
        playlist_panel.mount(self.playlist_table)

        # Playlist action buttons
        pl_actions = Horizontal()
        self.remove_btn = Button("Remove (D)", id="remove-btn")
        self.remove_btn.on_press = self._on_remove_song
        pl_actions.mount(self.remove_btn)

        self.move_up_btn = Button("Move Up (↑)", id="moveup-btn")
        self.move_up_btn.on_press = self._on_move_up
        pl_actions.mount(self.move_up_btn)

        self.move_down_btn = Button("Move Down (↓)", id="movedown-btn")
        self.move_down_btn.on_press = self._on_move_down
        pl_actions.mount(self.move_down_btn)

        pl_actions.mount(Static("|"))

        self.clear_btn = Button("Clear All", id="clear-btn")
        self.clear_btn.on_press = self._on_clear
        pl_actions.mount(self.clear_btn)

        playlist_panel.mount(pl_actions)

        # Playlist export buttons
        export_actions = Horizontal()
        self.export_audio_btn = Button("Export Audio (e)", id="export-audio-btn")
        self.export_audio_btn.on_press = self._on_export_audio
        export_actions.mount(self.export_audio_btn)

        self.export_video_btn = Button("Export Video (E)", id="export-video-btn")
        self.export_video_btn.on_press = self._on_export_video
        export_actions.mount(self.export_video_btn)

        playlist_panel.mount(export_actions)

        # Add panels to main container
        main_container.mount(library_panel)
        main_container.mount(playlist_panel)
        self.mount(main_container)

        # Bottom panel: Transition Editor
        transition_panel = Vertical(classes="transition-panel")
        transition_panel.mount(Static("Transition Editor", classes="panel-title"))

        self.editing_label = Label(
            "Select a song to edit its transition",
            classes="transition-status",
        )
        transition_panel.mount(self.editing_label)

        # Transition parameters row
        params_row = Horizontal()
        params_row.mount(Static("Type:"))

        self.transition_type_input = Input(
            value="gap",
            placeholder="gap or crossfade",
            id="transition-type-input",
        )
        self.transition_type_input.on_change = self._on_transition_param_change
        params_row.mount(self.transition_type_input)

        params_row.mount(Static("Overlap/Fade:"))

        self.overlap_input = Input(
            value="4.0",
            placeholder="beats",
            id="overlap-input",
        )
        self.overlap_input.on_change = self._on_transition_param_change
        params_row.mount(self.overlap_input)

        apply_btn = Button("Apply", id="apply-transition-btn")
        apply_btn.on_press = self._on_apply_transition
        params_row.mount(apply_btn)

        transition_panel.mount(params_row)

        # Section selection row
        section_row = Horizontal()
        section_row.mount(Static("Sections:"))

        self.section_start_select = Button(
            "Select Start",
            id="section-start-btn",
        )
        self.section_start_select.on_press = self._on_section_start
        section_row.mount(self.section_start_select)

        section_row.mount(Static("to"))

        self.section_end_select = Button(
            "Select End",
            id="section-end-btn",
        )
        self.section_end_select.on_press = self._on_section_end
        section_row.mount(self.section_end_select)

        section_row.mount(Static("(default: full song)"))

        transition_panel.mount(section_row)

        self.mount(transition_panel)

        # Status
        self.status_label = Static("Ready")
        self.mount(self.status_label)

        # Initial refresh
        self._refresh_library_table()
        self._refresh_playlist_table()
        self._update_duration_display()

    def _refresh_library_table(self) -> None:
        """Refresh song library table with current search and page."""
        self.library_table.clear()

        songs = self.catalog.get_all_songs()

        # Apply search filter
        if self._search_query:
            query = self._search_query.lower()
            songs = [
                s for s in songs
                if query in s.filename.lower() or
                   query in str(s.tempo) or
                   query in s.full_key.lower()
            ]

        # Apply pagination
        start = self._library_page * self._library_page_size
        end = start + self._library_page_size
        page_songs = songs[start:end]

        for song in page_songs:
            self.library_table.add_row(
                song.filename,
                str(int(song.tempo)),
                song.full_key,
                song.format_duration(),
            )

    def _refresh_playlist_table(self) -> None:
        """Refresh playlist table from state."""
        self.playlist_table.clear()

        for i, song_id in enumerate(self.state.playlist_items):
            song = self.catalog.get_song(song_id)
            if song:
                # Get transition info
                transition_str = "→"

                # Determine section display
                sections_str = "Full"
                # (In full implementation, show start_section to end_section)

                if i < len(self.state.playlist_items) - 1:
                    # This song has a transition to next
                    transition_str = "Gap (4 beats)"

                self.playlist_table.add_row(
                    str(i + 1),
                    song.filename,
                    sections_str,
                    transition_str,
                    song.format_duration(),
                )

    def _update_duration_display(self) -> None:
        """Update total duration display."""
        total_seconds = 0

        for song_id in self.state.playlist_items:
            song = self.catalog.get_song(song_id)
            if song:
                total_seconds += song.duration

        minutes = int(total_seconds // 60)
        seconds = int(total_seconds % 60)
        hours = minutes // 60
        minutes = minutes % 60

        if hours > 0:
            self.duration_label.update(f"{hours}:{minutes:02d}:{seconds:02d}")
        else:
            self.duration_label.update(f"{minutes}:{seconds:02d}")

    def _on_name_change(self, value: str) -> None:
        """Handle playlist name change."""
        self.state.playlist_name = value

    def _on_search_change(self, value: str) -> None:
        """Handle search input change."""
        self._search_query = value
        self._library_page = 0  # Reset to first page
        self._refresh_library_table()

    def _on_library_select(self, row_key: str) -> None:
        """Handle song library selection."""
        # Just highlight, don't auto-add
        pass

    def _on_playlist_select(self, row_key: str) -> None:
        """Handle playlist selection."""
        # Parse index from row_key
        try:
            index = int(row_key) - 1
            self.state.selected_playlist_index = index

            # Update transition editor for this song
            self._update_transition_editor(index)

            self.status_label.update(
                f"Selected song {index + 1} of {len(self.state.playlist_items)}"
            )
        except (ValueError, IndexError):
            pass

    def _update_transition_editor(self, index: int) -> None:
        """Update transition editor for selected song."""
        if index >= len(self.state.playlist_items) - 1:
            # Last song has no transition to next
            self.editing_label.update("Last song - no transition to edit")
            return

        song_id = self.state.playlist_items[index]
        song = self.catalog.get_song(song_id)

        if song:
            self.editing_label.update(
                f"Editing transition for: {song.filename}"
            )

            # Default values
            self.transition_type_input.value = "gap"
            self.overlap_input.value = "4.0"

    def _on_add_song(self) -> None:
        """Handle add song button press - adds first selected from library."""
        # In full implementation, would add selected library song
        # For now, add first song in library as placeholder
        songs = self.catalog.get_all_songs()
        if songs:
            first_song = songs[0]
            self.state.add_song_to_playlist(first_song.id)
            self._refresh_playlist_table()
            self._update_duration_display()
            self.status_label.update(f"Added: {first_song.filename}")

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
            self._update_duration_display()

            if self.state.selected_playlist_index >= len(self.state.playlist_items):
                self.state.selected_playlist_index = len(self.state.playlist_items) - 1

            self.status_label.update(
                f"Removed song at position {self.state.selected_playlist_index + 1}"
            )
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
        self._update_duration_display()
        self.status_label.update("Playlist cleared")

    def _on_prev_page(self) -> None:
        """Handle previous page button."""
        if self._library_page > 0:
            self._library_page -= 1
            self._refresh_library_table()

    def _on_next_page(self) -> None:
        """Handle next page button."""
        self._library_page += 1
        self._refresh_library_table()

    def _on_library_preview(self) -> None:
        """Handle library preview button press."""
        # Preview the first visible song in library
        if self.library_table.row_count > 0:
            first_key = self.library_table.rows[0].key
            if first_key:
                song = self.catalog.get_song(first_key)
                if song:
                    if self.playback.load(song.filepath):
                        self.playback.play()
                        self.status_label.update(f"Previewing: {song.filename}")

    def _on_save_playlist(self) -> None:
        """Handle save playlist button press."""
        if not self.state.playlist_items:
            self.status_label.update("Error: Cannot save empty playlist")
            return

        # Create Playlist from state
        from stream_of_worship.core.paths import get_playlists_path

        playlist = Playlist(id="saved-playlist")
        playlist.name = self.state.playlist_name

        # Add songs with default transitions
        for song_id in self.state.playlist_items:
            song = self.catalog.get_song(song_id)
            if song:
                transition = TransitionParams(transition_type="gap", gap_beats=1.0)
                playlist.add_song(
                    song_id=song_id,
                    song_filename=song.filename,
                    start_section=0,
                    end_section=None,
                    transition=transition,
                )

        # Save to playlists directory
        save_path = get_playlists_path() / f"{playlist.name}.json"
        playlist.save(save_path)

        self.status_label.update(f"Saved playlist to: {save_path}")

    def _on_load_playlist(self) -> None:
        """Handle load playlist button press."""
        # Placeholder - would show file picker in full implementation
        self.status_label.update("Load playlist: Select a file to load")

    def _on_export_audio(self) -> None:
        """Handle export audio button press."""
        if not self.state.playlist_items:
            self.status_label.update("Error: Playlist is empty")
            return

        self.status_label.update("Exporting audio... Not yet implemented in Phase 3")

    def _on_export_video(self) -> None:
        """Handle export video button press."""
        if not self.state.playlist_items:
            self.status_label.update("Error: Playlist is empty")
            return

        self.status_label.update("Exporting video... Not yet implemented in Phase 3")

    def _on_transition_param_change(self, value: str) -> None:
        """Handle transition parameter change."""
        if self.state.selected_playlist_index is None:
            return

        # Store the parameter changes
        # (In full implementation, would update transition in playlist item)

    def _on_apply_transition(self) -> None:
        """Handle apply transition button press."""
        if self.state.selected_playlist_index is None:
            self.status_label.update("Error: No song selected")
            return

        if self.state.selected_playlist_index >= len(self.state.playlist_items) - 1:
            self.status_label.update("Error: Last song has no transition")
            return

        try:
            overlap = float(self.overlap_input.value)
            trans_type = self.transition_type_input.value

            # Create transition params
            params = TransitionParams(transition_type=trans_type)
            if trans_type == "gap":
                params.gap_beats = overlap
            else:
                params.overlap = overlap

            # Update playlist item transition
            # (In full implementation, would update the specific playlist item)

            self.status_label.update(
                f"Applied transition: {trans_type}, overlap/fade: {overlap}"
            )
        except ValueError:
            self.status_label.update("Error: Invalid overlap value")

    def _on_section_start(self) -> None:
        """Handle section start selection."""
        self.status_label.update("Section start selection: Not yet implemented")

    def _on_section_end(self) -> None:
        """Handle section end selection."""
        self.status_label.update("Section end selection: Not yet implemented")

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

        # Handle arrow keys for playlist navigation
        if event.key == "up":
            self._on_move_up()
        elif event.key == "down":
            self._on_move_down()

        # Handle 'a' to add song
        if event.key == "a":
            self._on_add_song()

        # Handle 'd' to delete song
        if event.key == "d":
            self._on_remove_song()

        # Handle 'e' to export audio
        if event.key == "e":
            self._on_export_audio()

        # Handle 'E' to export video
        if event.key == "E":
            self._on_export_video()

        # Handle space to preview
        if event.key == "space":
            self._on_library_preview()

        # Handle 'q' to go back to main menu
        if event.key == "q":
            from stream_of_worship.tui.app import TransitionBuilderApp

            app = self.app
            if isinstance(app, TransitionBuilderApp):
                app.switch_screen("generation")

        return super().on_key(event)

    def on_mount(self) -> None:
        """Handle screen mount event."""
        # Stop any existing playback
        if self.playback.is_playing or self.playback.is_paused:
            self.playback.stop()

        # Set active screen in state
        from stream_of_worship.tui.app import TransitionBuilderApp
        app = self.app
        if isinstance(app, TransitionBuilderApp):
            app.state.active_screen = ActiveScreen.PLAYLIST
