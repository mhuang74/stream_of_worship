"""Generation screen for creating transitions between two songs."""

from textual.containers import Horizontal, Vertical
from textual.widgets import (
    Static,
    DataTable,
    Button,
    Label,
    Input,
    Select,
    Footer,
)

from stream_of_worship.tui.state import AppState
from stream_of_worship.tui.models.song import Song
from stream_of_worship.tui.models.transition import TransitionParams
from stream_of_worship.tui.models.section import Section
from stream_of_worship.tui.services.catalog import SongCatalogLoader
from stream_of_worship.tui.services.playback import PlaybackService
from stream_of_worship.tui.services.generation import TransitionGenerationService
from stream_of_worship.tui.utils.logger import get_error_logger


class GenerationScreen(Vertical):
    """Screen for generating and previewing transitions."""

    def __init__(
        self,
        state: AppState,
        catalog: SongCatalogLoader,
        playback: PlaybackService,
        generation: TransitionGenerationService,
    ):
        """Initialize generation screen.

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

        # Build UI
        self._build_ui()

    def _build_ui(self):
        """Build the screen layout."""
        # Header
        header = Static("Generation Screen - Create Transitions", classes="header")
        self.mount(header)

        # Main layout: song A | parameters | song B
        main_container = Horizontal()

        # Song A panel
        song_a_panel = Vertical(classes="panel")
        song_a_panel.mount(Static("Song A", classes="panel-title"))
        self.song_a_list = DataTable()
        self.song_a_list.add_column("Song", key="filename")
        self.song_a_list.add_column("BPM", key="tempo")
        self.song_a_list.add_column("Key", key="full_key")
        song_a_panel.mount(self.song_a_list)

        # Parameters panel
        params_panel = Vertical(classes="panel")
        params_panel.mount(Static("Parameters", classes="panel-title"))

        # Transition type
        self.transition_type = Select(
            "Type",
            options=["gap", "crossfade", "xbeat"],
            value=self.state.transition_type,
        )
        params_panel.mount(self.transition_type)

        # Gap/Overlap input
        self.gap_input = Input(
            placeholder="Gap/Overlap (beats)",
            value=str(self.state.overlap),
        )
        params_panel.mount(self.gap_input)

        # Generate button
        self.generate_btn = Button("Generate", id="generate-btn")
        self.generate_btn.on_press = self._on_generate
        params_panel.mount(self.generate_btn)

        # Play button
        self.play_btn = Button("Play", id="play-btn")
        self.play_btn.on_press = self._on_play
        params_panel.mount(self.play_btn)

        # Song B panel
        song_b_panel = Vertical(classes="panel")
        song_b_panel.mount(Static("Song B", classes="panel-title"))
        self.song_b_list = DataTable()
        self.song_b_list.add_column("Song", key="filename")
        self.song_b_list.add_column("BPM", key="tempo")
        self.song_b_list.add_column("Key", key="full_key")
        song_b_panel.mount(self.song_b_list)

        # Add panels to main container
        main_container.mount(song_a_panel)
        main_container.mount(params_panel)
        main_container.mount(song_b_panel)

        # Mount main container
        self.mount(main_container)

        # Status bar
        self.status_label = Label("Ready")
        self.mount(self.status_label)

    def _on_generate(self) -> None:
        """Handle generate button press."""
        logger = get_error_logger()
        if not logger:
            logger = None

        # Validate selections
        if not self.state.left_song_id or not self.state.right_song_id:
            self.status_label.update("Error: Select both songs")
            return

        # Get songs from catalog
        song_a = self.catalog.get_song(self.state.left_song_id)
        song_b = self.catalog.get_song(self.state.right_song_id)

        if not song_a or not song_b:
            self.status_label.update("Error: Song not found")
            return

        # Get selected sections
        section_a = None
        section_b = None
        if self.state.left_section_index is not None:
            if 0 <= self.state.left_section_index < len(song_a.sections):
                section_a = song_a.sections[self.state.left_section_index]
        if self.state.right_section_index is not None:
            if 0 <= self.state.right_section_index < len(song_b.sections):
                section_b = song_b.sections[self.state.right_section_index]

        if not section_a or not section_b:
            self.status_label.update("Error: Invalid section selection")
            return

        # Build transition parameters
        params = TransitionParams(
            transition_type=self.state.transition_type,
            gap_beats=self.state.overlap,
            overlap=self.state.overlap,
            fade_window=self.state.fade_window,
            fade_bottom=self.state.fade_bottom,
            stems_to_fade=self.state.stems_to_fade,
        )

        # Update state with values from UI
        self.state.transition_type = self.transition_type.value
        try:
            self.state.overlap = float(self.gap_input.value)
        except ValueError:
            pass

        # Generate transition
        self.status_label.update("Generating...")
        record = self.generation.generate_transition(
            song_a.filename,
            song_b.filename,
            section_a,
            section_b,
            params,
        )

        if record:
            self.state.add_transition(record)
            self.state.last_generated_transition_path = str(record.audio_path)
            self.status_label.update("Generated: " + str(record.audio_path))
        else:
            self.status_label.update("Generation failed")
            if logger:
                logger.log_error("Generation failed for transition")

    def _on_play(self) -> None:
        """Handle play button press."""
        logger = get_error_logger()

        if not self.state.last_generated_transition_path:
            self.status_label.update("Error: No generated transition")
            return

        # Load and play the transition
        from pathlib import Path
        audio_path = Path(self.state.last_generated_transition_path)

        if not audio_path.exists():
            self.status_label.update("Error: File not found")
            if logger:
                logger.log_file_error(str(audio_path), FileNotFoundError(), operation="playback")
            return

        # Load the audio file
        if self.playback.load(audio_path):
            self.playback.play()
            self.status_label.update("Playing...")
        else:
            self.status_label.update("Error: Failed to load audio")
            if logger:
                logger.log_file_error(str(audio_path), Exception(), operation="playback")

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

        # Handle other keys
        return super().on_key(event)
