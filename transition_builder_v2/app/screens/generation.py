"""Generation screen for song transition preview app."""
from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Static, Label, ListView, ListItem, Header, Footer, Input, Select
from textual.binding import Binding
from textual.events import MouseMove

from app.state import AppState, GenerationMode
from app.models.song import Song
from app.services.catalog import SongCatalogLoader
from app.utils.logger import get_error_logger


class SongListPanel(ListView):
    """Panel displaying a list of songs."""

    def __init__(self, title: str, is_left: bool = True, *args, **kwargs):
        """Initialize the song list panel.

        Args:
            title: Panel title ("SONG A" or "SONG B")
            is_left: Whether this is the left (Song A) panel
        """
        super().__init__(*args, **kwargs)
        self.border_title = title
        self.is_left = is_left
        self.songs: list[Song] = []

    def set_songs(self, songs: list[Song], selected_song_id: str | None = None):
        """Set the list of songs to display.

        Args:
            songs: List of songs to display
            selected_song_id: ID of currently selected song (for highlighting)
        """
        self.songs = songs
        self.clear()
        for song in songs:
            # Add checkbox if selected
            if selected_song_id and song.id == selected_song_id:
                text = f"✓ {song.display_name}"
            else:
                text = f"  {song.display_name}"

            item = ListItem(Label(text))

            # Apply selected class if this is the selected song
            if selected_song_id and song.id == selected_song_id:
                item.add_class("--selected")

            self.append(item)

    def on_mouse_move(self, event: MouseMove) -> None:
        """Update highlighted index when mouse moves over items."""
        # Get the widget under the mouse
        widget, _ = self.screen.get_widget_at(*event.screen_offset)

        # If it's a ListItem in this ListView, update the index
        if isinstance(widget, ListItem) and widget in self.children:
            new_index = list(self.children).index(widget)
            if new_index != self.index:
                self.index = new_index


class SectionListPanel(ListView):
    """Panel displaying sections for a selected song."""

    def __init__(self, title: str = "Sections", *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._default_title = title
        self.border_title = title
        self.sections: list = []

    def set_sections(self, song: Song, selected_section_index: int | None = None):
        """Set the sections for a song.

        Args:
            song: Song whose sections to display
            selected_section_index: Index of currently selected section (for highlighting)
        """
        # Clear the list first
        super().clear()

        # Store sections and update title
        self.sections = song.sections
        self.border_title = f"Sections for: {song.filename}"

        # Add section items
        for idx, section in enumerate(self.sections):
            # Add checkbox if selected
            if selected_section_index is not None and idx == selected_section_index:
                text = f"✓ {section.format_display()}"
            else:
                text = f"  {section.format_display()}"

            item = ListItem(Label(text))

            # Apply selected class if this is the selected section
            if selected_section_index is not None and idx == selected_section_index:
                item.add_class("--selected")

            self.append(item)

    def clear(self):
        """Clear the list and reset title."""
        super().clear()
        self.border_title = self._default_title
        self.sections = []

    def on_mouse_move(self, event: MouseMove) -> None:
        """Update highlighted index when mouse moves over items."""
        # Get the widget under the mouse
        widget, _ = self.screen.get_widget_at(*event.screen_offset)

        # If it's a ListItem in this ListView, update the index
        if isinstance(widget, ListItem) and widget in self.children:
            new_index = list(self.children).index(widget)
            if new_index != self.index:
                self.index = new_index


class MetadataPanel(Static):
    """Panel displaying song and section metadata."""

    def __init__(self, title: str = "Metadata", *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.border_title = title

    def set_song(self, song: Song, section_index: int | None = None):
        """Display metadata for a song and optionally a section.

        Args:
            song: The song to display metadata for
            section_index: Index of the selected section (if any)
        """
        metadata_text = f"""Song: {song.filename}
Duration: {song.format_duration()}
Key: {song.full_key}
BPM: {int(song.tempo)}
"""

        # Add section info if a section is selected
        if section_index is not None and 0 <= section_index < len(song.sections):
            section = song.sections[section_index]
            section_start = section.format_time(section.start)
            section_end = section.format_time(section.end)
            section_duration = f"{int(section.duration)}s"
            metadata_text += f"""
Section: {section.label.capitalize()}
Section Time: {section_start} - {section_end}
Section Duration: {section_duration}
"""

        if song.compatibility_score > 0:
            metadata_text += f"Compatibility: {int(song.compatibility_score)}%"

        self.update(metadata_text.strip())

    def clear_metadata(self):
        """Clear the metadata display."""
        self.update("No song selected")


class ParametersPanel(Container):
    """Panel for configuring transition parameters."""

    def __init__(self, state: AppState, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.state = state
        self.border_title = "TRANSITION PARAMETERS"

    def compose(self) -> ComposeResult:
        """Create child widgets."""
        # Two-column layout
        with Horizontal():
            # Left column: transition parameters
            with Vertical(id="params_left_column"):
                # Type selector
                with Horizontal():
                    yield Label("Type:", classes="param-label")
                    self.type_select = Select(
                        [("Gap", "gap"), ("Crossfade", "crossfade")],
                        value=self.state.transition_type,
                        id="type_select"
                    )
                    yield self.type_select

                # Gap/Overlap input
                with Horizontal():
                    self.overlap_label = Label("Gap (beats):", classes="param-label")
                    yield self.overlap_label
                    self.overlap_input = Input(
                        value=str(abs(self.state.overlap)),
                        placeholder="1.0",
                        id="overlap_input",
                        max_length=6
                    )
                    yield self.overlap_input

                # Fade Window (for crossfade)
                with Horizontal():
                    yield Label("Fade Window:", classes="param-label")
                    self.fade_window_input = Input(
                        value=str(self.state.fade_window),
                        placeholder="8.0",
                        id="fade_window_input",
                        max_length=6
                    )
                    yield self.fade_window_input

                # Fade Speed (for crossfade)
                with Horizontal():
                    yield Label("Fade Speed:", classes="param-label")
                    self.fade_speed_input = Input(
                        value=str(self.state.fade_speed),
                        placeholder="2.0",
                        id="fade_speed_input",
                        max_length=6
                    )
                    yield self.fade_speed_input
                
                # Fade Bottom
                with Horizontal():
                    yield Label("Fade Bottom (%):", classes="param-label")
                    self.fade_bottom_input = Input(
                        value=str(int(self.state.fade_bottom * 100)),
                        placeholder="0",
                        id="fade_bottom_input",
                        max_length=3
                    )
                    yield self.fade_bottom_input

            # Right column: section adjustments
            with Vertical(id="params_right_column"):
                # Section adjustments
                with Horizontal():
                    yield Label("From Sec Start:", classes="param-label")
                    self.from_start_input = Input(
                        value=str(self.state.from_section_start_adjust),
                        placeholder="0",
                        id="from_start_input",
                        max_length=3
                    )
                    yield self.from_start_input

                with Horizontal():
                    yield Label("From Sec End:", classes="param-label")
                    self.from_end_input = Input(
                        value=str(self.state.from_section_end_adjust),
                        placeholder="0",
                        id="from_end_input",
                        max_length=3
                    )
                    yield self.from_end_input

                with Horizontal():
                    yield Label("To Sec Start:", classes="param-label")
                    self.to_start_input = Input(
                        value=str(self.state.to_section_start_adjust),
                        placeholder="0",
                        id="to_start_input",
                        max_length=3
                    )
                    yield self.to_start_input

                with Horizontal():
                    yield Label("To Sec End:", classes="param-label")
                    self.to_end_input = Input(
                        value=str(self.state.to_section_end_adjust),
                        placeholder="0",
                        id="to_end_input",
                        max_length=3
                    )
                    yield self.to_end_input

    def on_select_changed(self, event: Select.Changed) -> None:
        """Handle transition type selection."""
        if event.select.id == "type_select":
            self.state.transition_type = event.value
            # Update overlap label based on type
            if event.value == "gap":
                self.overlap_label.update("Gap (beats):")
            else:
                self.overlap_label.update("Overlap (beats):")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle input submission (Enter key)."""
        self._update_parameter_from_input(event.input)
        # Move focus to the parent screen to allow keyboard shortcuts to work
        # Access the GenerationScreen through the screen property
        generation_screen = self.screen
        if generation_screen:
            # Focus on section_a_list by default (neutral choice)
            section_a_list = generation_screen.query_one("#section_a_list")
            if section_a_list:
                section_a_list.focus()

    def on_input_blurred(self, event: Input.Blurred) -> None:
        """Handle input losing focus."""
        self._update_parameter_from_input(event.input)

    def _update_parameter_from_input(self, input_widget: Input) -> None:
        """Update state from input widget value."""
        # Section adjustments are integers
        if input_widget.id in ["from_start_input", "from_end_input", "to_start_input", "to_end_input"]:
            try:
                value = int(input_widget.value) if input_widget.value else 0
                # Clamp to range -4 to +4
                value = max(-4, min(4, value))
            except ValueError:
                # Invalid input, reset to current state value
                if input_widget.id == "from_start_input":
                    input_widget.value = str(self.state.from_section_start_adjust)
                elif input_widget.id == "from_end_input":
                    input_widget.value = str(self.state.from_section_end_adjust)
                elif input_widget.id == "to_start_input":
                    input_widget.value = str(self.state.to_section_start_adjust)
                elif input_widget.id == "to_end_input":
                    input_widget.value = str(self.state.to_section_end_adjust)
                return

            # Update state
            if input_widget.id == "from_start_input":
                self.state.from_section_start_adjust = value
                input_widget.value = str(value)  # Update to clamped value
            elif input_widget.id == "from_end_input":
                self.state.from_section_end_adjust = value
                input_widget.value = str(value)
            elif input_widget.id == "to_start_input":
                self.state.to_section_start_adjust = value
                input_widget.value = str(value)
            elif input_widget.id == "to_end_input":
                self.state.to_section_end_adjust = value
                input_widget.value = str(value)
        else:
            # Float parameters
            try:
                value = float(input_widget.value) if input_widget.value else 0.0
            except ValueError:
                # Invalid input, reset to current state value
                if input_widget.id == "overlap_input":
                    input_widget.value = str(abs(self.state.overlap))
                elif input_widget.id == "fade_window_input":
                    input_widget.value = str(self.state.fade_window)
                elif input_widget.id == "fade_speed_input":
                    input_widget.value = str(self.state.fade_speed)
                elif input_widget.id == "fade_bottom_input":
                    input_widget.value = str(int(self.state.fade_bottom * 100))
                return

            # Update state
            if input_widget.id == "overlap_input":
                self.state.overlap = abs(value)  # Always positive for gap
            elif input_widget.id == "fade_window_input":
                self.state.fade_window = value
            elif input_widget.id == "fade_speed_input":
                self.state.fade_speed = value
            elif input_widget.id == "fade_bottom_input":
                # Ensure 0-100 range
                value = max(0.0, min(100.0, value))
                self.state.fade_bottom = value / 100.0
                input_widget.value = str(int(value))

    def update_from_state(self):
        """Update UI from current state values."""
        self.type_select.value = self.state.transition_type
        self.overlap_input.value = str(abs(self.state.overlap))
        self.fade_window_input.value = str(self.state.fade_window)
        self.fade_speed_input.value = str(self.state.fade_speed)
        self.fade_bottom_input.value = str(int(self.state.fade_bottom * 100))
        self.from_start_input.value = str(self.state.from_section_start_adjust)
        self.from_end_input.value = str(self.state.from_section_end_adjust)
        self.to_start_input.value = str(self.state.to_section_start_adjust)
        self.to_end_input.value = str(self.state.to_section_end_adjust)

        # Update label
        if self.state.transition_type == "gap":
            self.overlap_label.update("Gap (beats):")
        else:
            self.overlap_label.update("Overlap (beats):")


class WarningPanel(Static):
    """Panel for displaying validation warnings."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.warnings: list[str] = []

    def set_warnings(self, warnings: list[str]):
        """Display warnings."""
        self.warnings = warnings
        if warnings:
            warning_text = "\n".join(f"⚠ {w}" for w in warnings)
            self.update(warning_text)
            self.display = True
        else:
            self.update("")
            self.display = False


class GenerationScreen(Screen):
    """Main generation screen for creating song transitions."""

    BINDINGS = [
        Binding("tab", "cycle_panel", "Next Panel", show=True),
        Binding("shift+tab", "cycle_panel_reverse", "Prev Panel", show=True),
        Binding("g", "generate", "Generate", show=True),
        Binding("G", "quick_test", "Quick Test", show=False),
        Binding("t", "play_transition", "Play Transition", show=True),
        Binding("T", "play_focused_preview", "Focused Preview", show=True),
        Binding("h", "show_history", "History", show=True),
        Binding("/", "search_songs", "Search", show=True),
        Binding("s", "swap_songs", "Swap A⇄B", show=True),
        Binding("escape", "stop_playback_or_exit_modify", "Stop/Exit", show=True),
        Binding("a", "play_song_a", "Play A", show=False),
        Binding("b", "play_song_b", "Play B", show=False),
        Binding("space", "play_highlighted", "Play", show=True),
        Binding("left", "seek_backward", "Seek -3s", show=True),
        Binding("right", "seek_forward", "Seek +4s", show=True),
        Binding("?", "show_help", "Help", show=True),
        Binding("f1", "show_help", "Help", show=False),
    ]

    def __init__(self, state: AppState, catalog: SongCatalogLoader, playback, generation, *args, **kwargs):
        """Initialize the generation screen.

        Args:
            state: Application state
            catalog: Song catalog loader
            playback: Playback service
            generation: Transition generation service
        """
        super().__init__(*args, **kwargs)
        self.state = state
        self.catalog = catalog
        self.playback = playback
        self.generation = generation
        self.focused_panel_name = "song_a"  # "song_a", "song_b", "parameters"

    def compose(self) -> ComposeResult:
        """Create child widgets."""
        yield Header()

        # Mode banner (shown only in modify mode)
        self.mode_banner = Static("", id="mode_banner")
        self.mode_banner.display = False
        yield self.mode_banner

        # Main content area
        with Horizontal(id="main_content"):
            # Left column: Song A
            with Vertical(id="song_a_panel", classes="song-panel"):
                self.song_a_list = SongListPanel("SONG A", is_left=True, id="song_a_list")
                yield self.song_a_list

                self.section_a_list = SectionListPanel("Sections A", id="section_a_list")
                yield self.section_a_list

                self.metadata_a = MetadataPanel("TRANSITION FROM", id="metadata_a")
                yield self.metadata_a

            # Right column: Song B
            with Vertical(id="song_b_panel", classes="song-panel"):
                self.song_b_list = SongListPanel("SONG B", is_left=False, id="song_b_list")
                yield self.song_b_list

                self.section_b_list = SectionListPanel("Sections B", id="section_b_list")
                yield self.section_b_list

                self.metadata_b = MetadataPanel("TRANSITION TO", id="metadata_b")
                yield self.metadata_b

        # Parameters panel
        self.parameters_panel = ParametersPanel(self.state, id="parameters_panel")
        yield self.parameters_panel

        # Warning panel
        self.warning_panel = WarningPanel(id="warning_panel")
        yield self.warning_panel

        yield Footer()

    def on_mount(self) -> None:
        """Handle screen mount event."""
        self.update_screen()

    def update_screen(self):
        """Update the screen based on current state."""
        # Update mode banner
        if self.state.generation_mode == GenerationMode.MODIFY:
            self.mode_banner.update(
                f"[bold yellow]MODIFY MODE: Based on Transition #{self.state.base_transition_id}[/]"
            )
            self.mode_banner.display = True
        else:
            self.mode_banner.display = False

        # Update Song A list (with selection highlighting)
        songs = self.catalog.get_all_songs()
        self.song_a_list.set_songs(songs, self.state.left_song_id)
        # Restore cursor to selected song
        if self.state.left_song_id:
            for i, song in enumerate(self.song_a_list.songs):
                if song.id == self.state.left_song_id:
                    self.song_a_list.index = i
                    break

        # Update Song B list (sorted by compatibility if Song A is selected, with highlighting)
        if self.state.left_song_id:
            songs_b = self.catalog.get_songs_sorted_by_compatibility(self.state.left_song_id)
            self.song_b_list.set_songs(songs_b, self.state.right_song_id)

            # Update sections for Song A (with selection highlighting)
            song_a = self.catalog.get_song(self.state.left_song_id)
            if song_a:
                self.section_a_list.set_sections(song_a, self.state.left_section_index)
                # Restore cursor to selected section
                if self.state.left_section_index is not None:
                    self.section_a_list.index = self.state.left_section_index
                self.metadata_a.set_song(song_a, self.state.left_section_index)
        else:
            self.song_b_list.set_songs(songs, self.state.right_song_id)
            self.section_a_list.clear()
            self.metadata_a.clear_metadata()

        # Restore cursor to selected song B
        if self.state.right_song_id:
            for i, song in enumerate(self.song_b_list.songs):
                if song.id == self.state.right_song_id:
                    self.song_b_list.index = i
                    break

        # Update sections for Song B (with selection highlighting)
        if self.state.right_song_id:
            song_b = self.catalog.get_song(self.state.right_song_id)
            if song_b:
                self.section_b_list.set_sections(song_b, self.state.right_section_index)
                # Restore cursor to selected section
                if self.state.right_section_index is not None:
                    self.section_b_list.index = self.state.right_section_index
                self.metadata_b.set_song(song_b, self.state.right_section_index)
        else:
            self.section_b_list.clear()
            self.metadata_b.clear_metadata()

        # Update warnings
        self.warning_panel.set_warnings(self.state.active_validation_warnings)

        # Update parameters panel
        self.parameters_panel.update_from_state()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Handle list item selection."""
        list_view = event.list_view

        if list_view.id == "song_a_list":
            # Song A selected
            index = event.list_view.index
            if 0 <= index < len(self.song_a_list.songs):
                selected_song = self.song_a_list.songs[index]
                self.state.left_song_id = selected_song.id
                self.state.left_section_index = None
                self.update_screen()

        elif list_view.id == "song_b_list":
            # Song B selected
            index = event.list_view.index
            if 0 <= index < len(self.song_b_list.songs):
                selected_song = self.song_b_list.songs[index]
                # Cannot select same song for both A and B
                if selected_song.id != self.state.left_song_id:
                    self.state.right_song_id = selected_song.id
                    self.state.right_section_index = None
                    self.update_screen()

        elif list_view.id == "section_a_list":
            # Section A selected
            index = event.list_view.index
            if 0 <= index < len(self.section_a_list.sections):
                self.state.left_section_index = index
                self.update_screen()

        elif list_view.id == "section_b_list":
            # Section B selected
            index = event.list_view.index
            if 0 <= index < len(self.section_b_list.sections):
                self.state.right_section_index = index
                self.update_screen()

    def action_cycle_panel(self):
        """Cycle focus through panels forward (Tab key)."""
        self._cycle_panel_direction(forward=True)

    def action_cycle_panel_reverse(self):
        """Cycle focus through panels backward (Shift+Tab key)."""
        self._cycle_panel_direction(forward=False)

    def _cycle_panel_direction(self, forward: bool = True):
        """Cycle focus through all panels in the specified direction.

        Args:
            forward: If True, cycle forward; if False, cycle backward
        """
        # Get current focused widget
        focused = self.app.focused

        # Define the cycle order: song_a_list -> section_a_list -> song_b_list -> section_b_list -> parameters
        panels = [
            self.song_a_list,
            self.section_a_list,
            self.song_b_list,
            self.section_b_list,
            self.parameters_panel
        ]

        # Find current position
        try:
            current_index = panels.index(focused)
        except ValueError:
            # Not in our panels list, default to first panel
            current_index = 0 if forward else len(panels) - 1

        # Calculate next index
        if forward:
            next_index = (current_index + 1) % len(panels)
        else:
            next_index = (current_index - 1) % len(panels)

        # Focus the next panel
        panels[next_index].focus()

    def action_swap_songs(self):
        """Swap Song A with Song B, including section selections (S key)."""
        # Swap song selections
        self.state.left_song_id, self.state.right_song_id = self.state.right_song_id, self.state.left_song_id

        # Swap section selections
        self.state.left_section_index, self.state.right_section_index = self.state.right_section_index, self.state.left_section_index

        # Update the screen to reflect the swap
        self.update_screen()

        # Notify user
        if self.state.left_song_id and self.state.right_song_id:
            self.notify("Swapped Song A ⇄ Song B")
        elif self.state.left_song_id:
            self.notify("Swapped: Song B is now Song A")
        elif self.state.right_song_id:
            self.notify("Swapped: Song A is now Song B")
        else:
            self.notify("No songs to swap")

    def action_generate(self):
        """Generate transition (G key)."""
        # Validate selection
        if not self.state.left_song_id:
            self.notify("Please select Song A first", severity="warning")
            return

        if not self.state.right_song_id:
            self.notify("Please select Song B first", severity="warning")
            return

        if self.state.left_section_index is None:
            self.notify("Please select a section for Song A", severity="warning")
            return

        if self.state.right_section_index is None:
            self.notify("Please select a section for Song B", severity="warning")
            return

        # Get songs
        song_a = self.catalog.get_song(self.state.left_song_id)
        song_b = self.catalog.get_song(self.state.right_song_id)

        if not song_a or not song_b:
            self.notify("Error: Could not load songs", severity="error")
            return

        # Generate transition
        try:
            self.notify("Generating transition...")

            # Get transition parameters
            transition_type = self.state.transition_type.lower()

            if transition_type == "gap":
                # Gap transition: use overlap parameter as gap_beats (but positive)
                gap_beats = abs(self.state.overlap)  # Convert negative overlap to positive gap
                if gap_beats == 0:
                    gap_beats = 1.0  # Default to 1 beat

                output_path, metadata = self.generation.generate_transition(
                    song_a=song_a,
                    song_b=song_b,
                    section_a_index=self.state.left_section_index,
                    section_b_index=self.state.right_section_index,
                    transition_type="gap",
                    gap_beats=gap_beats,
                    section_a_start_adjust=self.state.from_section_start_adjust,
                    section_a_end_adjust=self.state.from_section_end_adjust,
                    section_b_start_adjust=self.state.to_section_start_adjust,
                    section_b_end_adjust=self.state.to_section_end_adjust,
                    fade_window_beats=self.state.fade_window,  # Pass valid fade args
                    fade_bottom=self.state.fade_bottom
                )
            else:
                self.notify(f"Transition type '{transition_type}' not yet implemented", severity="warning")
                return

            # Success! Store path and auto-play if configured
            self.state.last_generated_transition_path = str(output_path)
            self.notify(f"Transition saved: {output_path.name}", timeout=5)

            # Auto-play if configured
            if self.app.config.auto_play_on_generate:
                if self.playback.load(output_path):
                    self.playback.play()
                    self.notify(f"Playing transition... Press [T] to replay", timeout=3)

            # Add to history
            self._add_transition_to_history(
                transition_type=transition_type,
                song_a=song_a,
                song_b=song_b,
                output_path=output_path,
                metadata=metadata
            )

        except Exception as e:
            self.notify(f"Error generating transition: {str(e)}", severity="error")
            logger = get_error_logger()
            if logger:
                logger.log_generation_error(
                    song_a=song_a.filename if song_a else "unknown",
                    song_b=song_b.filename if song_b else "unknown",
                    transition_type=self.state.transition_type,
                    error=e,
                    parameters={
                        "overlap": self.state.overlap,
                        "fade_window": self.state.fade_window,
                        "fade_speed": self.state.fade_speed,
                        "fade_bottom": self.state.fade_bottom,
                        "from_section_start_adjust": self.state.from_section_start_adjust,
                        "from_section_end_adjust": self.state.from_section_end_adjust,
                        "to_section_start_adjust": self.state.to_section_start_adjust,
                        "to_section_end_adjust": self.state.to_section_end_adjust,
                    }
                )

    def action_play_transition(self):
        """Play last generated transition (T key)."""
        if not self.state.last_generated_transition_path:
            self.notify("No transition generated yet. Press [G] to generate one.", severity="warning")
            return

        from pathlib import Path
        transition_path = Path(self.state.last_generated_transition_path)

        if not transition_path.exists():
            self.notify(f"Transition file not found: {transition_path.name}", severity="error")
            return

        # Stop current playback and play transition
        if self.playback.is_playing or self.playback.is_paused:
            self.playback.stop()

        if self.playback.load(transition_path):
            self.playback.play()
            self.notify(f"Playing transition: {transition_path.name}")
        else:
            self.notify(f"Error loading transition file", severity="error")

    def action_play_focused_preview(self):
        """Play focused preview of transition point (Shift+T key).

        Plays last 4 beats of Song A section + gap + first 4 beats of Song B section.
        """
        # Validate selection
        if not self.state.left_song_id:
            self.notify("Please select Song A first", severity="warning")
            return

        if not self.state.right_song_id:
            self.notify("Please select Song B first", severity="warning")
            return

        if self.state.left_section_index is None:
            self.notify("Please select a section for Song A", severity="warning")
            return

        if self.state.right_section_index is None:
            self.notify("Please select a section for Song B", severity="warning")
            return

        # Get songs
        song_a = self.catalog.get_song(self.state.left_song_id)
        song_b = self.catalog.get_song(self.state.right_song_id)

        if not song_a or not song_b:
            self.notify("Error: Could not load songs", severity="error")
            return

        # Generate focused preview
        try:
            self.notify("Generating focused preview...")

            # Include gap if using gap transition
            gap_beats = 0.0
            if self.state.transition_type.lower() == "gap":
                gap_beats = abs(self.state.overlap)

            from pathlib import Path
            output_path, metadata = self.generation.generate_focused_preview(
                song_a=song_a,
                song_b=song_b,
                section_a_index=self.state.left_section_index,
                section_b_index=self.state.right_section_index,
                preview_beats=4.0,
                gap_beats=gap_beats,
                section_a_start_adjust=self.state.from_section_start_adjust,
                section_a_end_adjust=self.state.from_section_end_adjust,
                section_b_start_adjust=self.state.to_section_start_adjust,
                section_b_end_adjust=self.state.to_section_end_adjust
            )

            # Stop current playback and play preview
            if self.playback.is_playing or self.playback.is_paused:
                self.playback.stop()

            if self.playback.load(output_path):
                self.playback.play()
                self.notify(f"Playing preview: last 4 beats A → first 4 beats B")
            else:
                self.notify(f"Error loading preview file", severity="error")

        except Exception as e:
            self.notify(f"Error generating preview: {str(e)}", severity="error")
            logger = get_error_logger()
            if logger:
                logger.log_generation_error(
                    song_a=song_a.filename if song_a else "unknown",
                    song_b=song_b.filename if song_b else "unknown",
                    transition_type="focused_preview",
                    error=e,
                    parameters={
                        "preview_beats": 4.0,
                        "gap_beats": gap_beats,
                    }
                )

    def action_quick_test(self):
        """Quick test generation (Shift+G)."""
        # TODO: Implement ephemeral generation
        self.notify("Quick test (not yet implemented)")

    def action_show_history(self):
        """Switch to history screen (H key)."""
        from app.state import ActiveScreen
        self.state.active_screen = ActiveScreen.HISTORY
        # Select most recent transition if history exists and none selected
        if self.state.transition_history and self.state.selected_history_index is None:
            self.state.selected_history_index = 0
        self.app.switch_screen("history")

    def action_search_songs(self):
        """Open song search screen (/ key)."""
        # TODO: Implement song search
        self.notify("Search songs (not yet implemented)")

    def action_stop_playback_or_exit_modify(self):
        """Stop playback or exit modify mode (Esc key)."""
        # If playing, stop playback
        if self.playback.is_playing or self.playback.is_paused:
            self.playback.stop()
            self.notify("Playback stopped")
        # Otherwise, if in modify mode, exit it
        elif self.state.generation_mode == GenerationMode.MODIFY:
            self.state.exit_modify_mode()
            self.update_screen()
            self.notify("Exited modify mode")

    def action_play_song_a(self):
        """Play Song A section (P key)."""
        if not self.state.left_song_id:
            self.notify("Please select Song A first")
            return

        song = self.catalog.get_song(self.state.left_song_id)
        if not song or not song.filepath:
            self.notify("Audio file not found")
            return

        # Get selected section if any
        section = None
        section_start = None
        section_end = None
        if self.state.left_section_index is not None and 0 <= self.state.left_section_index < len(song.sections):
            section = song.sections[self.state.left_section_index]
            section_start = section.start
            section_end = section.end

        # Load and play
        if self.playback.load(song.filepath, section_start, section_end):
            self.playback.play()
            if section:
                self.notify(f"Playing: {song.filename} - {section.label.capitalize()} ({section.format_time(section.start)}-{section.format_time(section.end)})")
            else:
                self.notify(f"Playing: {song.filename} (full song)")
        else:
            self.notify("Failed to load audio file")

    def action_play_song_b(self):
        """Play Song B section (L key)."""
        if not self.state.right_song_id:
            self.notify("Please select Song B first")
            return

        song = self.catalog.get_song(self.state.right_song_id)
        if not song or not song.filepath:
            self.notify("Audio file not found")
            return

        # Get selected section if any
        section = None
        section_start = None
        section_end = None
        if self.state.right_section_index is not None and 0 <= self.state.right_section_index < len(song.sections):
            section = song.sections[self.state.right_section_index]
            section_start = section.start
            section_end = section.end

        # Load and play
        if self.playback.load(song.filepath, section_start, section_end):
            self.playback.play()
            if section:
                self.notify(f"Playing: {song.filename} - {section.label.capitalize()} ({section.format_time(section.start)}-{section.format_time(section.end)})")
            else:
                self.notify(f"Playing: {song.filename} (full song)")
        else:
            self.notify("Failed to load audio file")

    def action_play_highlighted(self):
        """Play currently highlighted item from beginning (Space key)."""
        # Always play from beginning - stop any current playback first
        if self.playback.is_playing or self.playback.is_paused:
            self.playback.stop()

        # Play whatever is currently highlighted
        focused = self.app.focused

        if focused == self.song_a_list:
            # Play highlighted song from Song A list
            index = self.song_a_list.index
            if index is not None and 0 <= index < len(self.song_a_list.songs):
                song = self.song_a_list.songs[index]
                if song and song.filepath:
                    if self.playback.load(song.filepath):
                        self.playback.play()
                        self.notify(f"Playing: {song.filename} (full song)")
                    else:
                        self.notify("Failed to load audio file")
                else:
                    self.notify("Audio file not found")
            else:
                self.notify("No song highlighted")

        elif focused == self.song_b_list:
            # Play highlighted song from Song B list
            index = self.song_b_list.index
            if index is not None and 0 <= index < len(self.song_b_list.songs):
                song = self.song_b_list.songs[index]
                if song and song.filepath:
                    if self.playback.load(song.filepath):
                        self.playback.play()
                        self.notify(f"Playing: {song.filename} (full song)")
                    else:
                        self.notify("Failed to load audio file")
                else:
                    self.notify("Audio file not found")
            else:
                self.notify("No song highlighted")

        elif focused == self.section_a_list:
            # Play highlighted section from Song A
            if not self.state.left_song_id:
                self.notify("Please select Song A first")
                return

            index = self.section_a_list.index
            song = self.catalog.get_song(self.state.left_song_id)
            if song and index is not None and 0 <= index < len(song.sections):
                section = song.sections[index]
                if self.playback.load(song.filepath, section.start, section.end):
                    self.playback.play()
                    self.notify(f"Playing: {song.filename} - {section.label.capitalize()} ({section.format_time(section.start)}-{section.format_time(section.end)})")
                else:
                    self.notify("Failed to load audio file")
            else:
                # No section highlighted, play full song instead
                if song and song.filepath:
                    if self.playback.load(song.filepath):
                        self.playback.play()
                        self.notify(f"Playing: {song.filename} (full song)")
                    else:
                        self.notify("Failed to load audio file")
                else:
                    self.notify("Audio file not found")

        elif focused == self.section_b_list:
            # Play highlighted section from Song B
            if not self.state.right_song_id:
                self.notify("Please select Song B first")
                return

            index = self.section_b_list.index
            song = self.catalog.get_song(self.state.right_song_id)
            if song and index is not None and 0 <= index < len(song.sections):
                section = song.sections[index]
                if self.playback.load(song.filepath, section.start, section.end):
                    self.playback.play()
                    self.notify(f"Playing: {song.filename} - {section.label.capitalize()} ({section.format_time(section.start)}-{section.format_time(section.end)})")
                else:
                    self.notify("Failed to load audio file")
            else:
                # No section highlighted, play full song instead
                if song and song.filepath:
                    if self.playback.load(song.filepath):
                        self.playback.play()
                        self.notify(f"Playing: {song.filename} (full song)")
                    else:
                        self.notify("Failed to load audio file")
                else:
                    self.notify("Audio file not found")

        else:
            # No list focused, fall back to playing selected items
            if self.state.left_song_id:
                self.action_play_song_a()
            elif self.state.right_song_id:
                self.action_play_song_b()
            else:
                self.notify("Please select a song first")

    def action_seek_backward(self):
        """Seek backward 3 seconds (← key)."""
        if self.playback.current_file:
            self.playback.seek(-3.0)
            self.notify(f"Seek to {self.playback.position:.1f}s")
        else:
            self.notify("No audio loaded")

    def action_seek_forward(self):
        """Seek forward 4 seconds (→ key)."""
        if self.playback.current_file:
            self.playback.seek(4.0)
            self.notify(f"Seek to {self.playback.position:.1f}s")
        else:
            self.notify("No audio loaded")

    def action_show_help(self):
        """Show help overlay (? or F1 key)."""
        # TODO: Implement help overlay
        self.notify("Help (not yet implemented)")

    def _add_transition_to_history(
        self,
        transition_type: str,
        song_a,
        song_b,
        output_path,
        metadata: dict
    ):
        """Add a generated transition to history.

        Args:
            transition_type: Type of transition (gap, crossfade, etc.)
            song_a: Song A object
            song_b: Song B object
            output_path: Path to the generated audio file
            metadata: Generation metadata dictionary
        """
        from datetime import datetime
        from pathlib import Path
        from app.models.transition import TransitionRecord

        # Get section labels
        section_a_label = "unknown"
        section_b_label = "unknown"

        if self.state.left_section_index is not None and 0 <= self.state.left_section_index < len(song_a.sections):
            section_a_label = song_a.sections[self.state.left_section_index].label

        if self.state.right_section_index is not None and 0 <= self.state.right_section_index < len(song_b.sections):
            section_b_label = song_b.sections[self.state.right_section_index].label

        # Calculate compatibility score
        compat_score = song_b.compatibility_score if song_b.compatibility_score > 0 else 50.0

        # Generate sequential ID
        next_id = 1
        if self.state.transition_history:
            next_id = max(t.id for t in self.state.transition_history) + 1

        # Build parameters dictionary
        parameters = {
            "type": transition_type,
            "gap_beats": abs(self.state.overlap) if transition_type == "gap" else 0,
            "overlap": self.state.overlap if transition_type != "gap" else 0,
            "fade_window": self.state.fade_window,
            "fade_speed": self.state.fade_speed,
            "stems_to_fade": self.state.stems_to_fade.copy(),
            "section_a_start_adjust": self.state.from_section_start_adjust,
            "section_a_end_adjust": self.state.from_section_end_adjust,
            "section_b_start_adjust": self.state.to_section_start_adjust,
            "section_b_end_adjust": self.state.to_section_end_adjust,
        }

        # Add any extension parameters
        if self.state.extension_parameters:
            parameters["extension"] = self.state.extension_parameters.copy()

        # Create transition record
        record = TransitionRecord(
            id=next_id,
            transition_type=transition_type,
            song_a_filename=song_a.filename,
            song_b_filename=song_b.filename,
            section_a_label=section_a_label,
            section_b_label=section_b_label,
            compatibility_score=compat_score,
            generated_at=datetime.now(),
            audio_path=Path(output_path),
            is_saved=False,
            saved_path=None,
            save_note=None,
            parameters=parameters
        )

        # Add to history (newest first, capped at 50)
        self.state.add_transition(record)

        # Exit modify mode if active
        if self.state.generation_mode == GenerationMode.MODIFY:
            self.state.generation_mode = GenerationMode.FRESH
            self.state.base_transition_id = None
            self.update_screen()  # Update the mode banner
