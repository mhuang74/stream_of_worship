"""History screen for viewing and managing generated transitions."""

from textual.containers import Horizontal, Vertical
from textual.widgets import (
    Static,
    DataTable,
    Button,
    Footer,
)

from stream_of_worship.tui.state import AppState, ActiveScreen
from stream_of_worship.tui.services.catalog import SongCatalogLoader
from stream_of_worship.tui.services.playback import PlaybackService
from stream_of_worship.tui.services.generation import TransitionGenerationService


class HistoryScreen(Vertical):
    """Screen for viewing and managing transition history."""

    def __init__(
        self,
        state: AppState,
        catalog: SongCatalogLoader,
        playback: PlaybackService,
        generation: TransitionGenerationService,
    ):
        """Initialize history screen.

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
        # Header
        header = Static("History - Generated Transitions", classes="header")
        self.mount(header)

        # History table
        self.history_table = DataTable()
        self.history_table.add_column("#", key="id")
        self.history_table.add_column("Transition", key="transition")
        self.history_table.add_column("Score", key="score")
        self.history_table.add_column("Time", key="time")
        self.history_table.add_column("Status", key="status")

        self.mount(self.history_table)

        # Buttons row
        buttons = Horizontal()

        self.back_btn = Button("Back to Generation", id="back-btn")
        self.back_btn.on_press = self._on_back
        buttons.mount(self.back_btn)

        self.play_btn = Button("Play Selected", id="play-btn")
        self.play_btn.on_press = self._on_play
        buttons.mount(self.play_btn)

        self.save_btn = Button("Save Selected", id="save-btn")
        self.save_btn.on_press = self._on_save
        buttons.mount(self.save_btn)

        self.modify_btn = Button("Modify Selected", id="modify-btn")
        self.modify_btn.on_press = self._on_modify
        buttons.mount(self.modify_btn)

        self.mount(buttons)

        # Status
        self.status_label = Static("Ready")
        self.mount(self.status_label)

        # Refresh table data
        self._refresh_table()

    def _refresh_table(self):
        """Refresh the history table with current state."""
        self.history_table.clear()

        for i, record in enumerate(self.state.transition_history):
            transition_str = f"{record.song_a_filename} → {record.song_b_filename}"
            self.history_table.add_row(
                str(record.id),
                transition_str,
                f"{int(record.compatibility_score)}%",
                record.format_time(),
                record.status_display,
            )

    def _on_back(self) -> None:
        """Handle back button press."""
        # Switch back to generation screen
        from stream_of_worship.tui.app import TransitionBuilderApp

        app = self.app
        if isinstance(app, TransitionBuilderApp):
            app.switch_screen("generation")

    def _on_play(self) -> None:
        """Handle play button press."""
        if self.state.selected_history_index is None:
            self.status_label.update("Error: No transition selected")
            return

        record = self.state.get_selected_transition()
        if not record:
            self.status_label.update("Error: Invalid selection")
            return

        if not record.audio_path.exists():
            self.status_label.update("Error: File not found")
            return

        # Load and play the transition
        if self.playback.load(record.audio_path):
            self.playback.play()
            self.status_label.update("Playing: " + str(record.audio_path.name))

    def _on_save(self) -> None:
        """Handle save button press."""
        if self.state.selected_history_index is None:
            self.status_label.update("Error: No transition selected")
            return

        record = self.state.get_selected_transition()
        if not record:
            return

        success = self.generation.save_transition(record)
        if success:
            self.status_label.update("Saved: " + (record.saved_path or "default"))
        else:
            self.status_label.update("Save failed")

    def _on_modify(self) -> None:
        """Handle modify button press."""
        if self.state.selected_history_index is None:
            self.status_label.update("Error: No transition selected")
            return

        record = self.state.get_selected_transition()
        if not record:
            return

        # Enter modify mode with this transition's parameters
        self.state.enter_modify_mode(record)

        # Switch back to generation screen
        from stream_of_worship.tui.app import TransitionBuilderApp

        app = self.app
        if isinstance(app, TransitionBuilderApp):
            app.switch_screen("generation")

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

        # Handle navigation
        if event.key == "up":
            if self.state.selected_history_index is not None:
                self.state.selected_history_index = max(
                    0, self.state.selected_history_index - 1
                )
            else:
                self.state.selected_history_index = 0
            self._refresh_table()

        elif event.key == "down":
            if self.state.selected_history_index is not None:
                self.state.selected_history_index = min(
                    len(self.state.transition_history) - 1,
                    self.state.selected_history_index + 1,
                )
            else:
                self.state.selected_history_index = 0
            self._refresh_table()

        return super().on_key(event)
