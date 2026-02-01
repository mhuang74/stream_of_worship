"""Main TUI application for Stream of Worship.

This is the entry point for the Textual-based user interface
for managing songs, transitions, and playlists.
"""

import sys
from pathlib import Path

from textual.app import App

from stream_of_worship.core.config import Config
from stream_of_worship.core.paths import ensure_directories
from stream_of_worship.tui.state import AppState, ActiveScreen
from stream_of_worship.tui.services.catalog import SongCatalogLoader
from stream_of_worship.tui.services.playback import PlaybackService
from stream_of_worship.tui.services.generation import TransitionGenerationService
from stream_of_worship.tui.utils.logger import (
    init_error_logger,
    get_error_logger,
    init_session_logger,
    get_session_logger,
)


class TransitionBuilderApp(App):
    """Main application for song transition preview."""

    CSS_PATH = ["screens/generation.tcss", "screens/history.tcss"]
    TITLE = "Stream of Worship - Transition Builder"

    def __init__(self, config_path: Path, *args, **kwargs):
        """Initialize the application.

        Args:
            config_path: Path to config.json
        """
        super().__init__(*args, **kwargs)

        # Ensure directories exist
        ensure_directories()

        # Load configuration
        try:
            self.config = Config.load(config_path)
        except (FileNotFoundError, ValueError) as e:
            print(f"Error loading configuration: {e}", file=sys.stderr)
            sys.exit(1)

        # Initialize loggers
        log_path = config_path.parent / "transitions_errors.log"
        session_log_path = config_path.parent / "transitions_session.log"
        init_error_logger(log_path=log_path, enabled=self.config.error_logging)
        init_session_logger(log_path=session_log_path, enabled=self.config.session_logging)

        # Initialize state
        self.state = AppState()

        # Initialize services
        self.catalog = SongCatalogLoader(self.config.audio_folder)
        self.playback = PlaybackService()
        self.generation = TransitionGenerationService(
            output_dir=self.config.output_folder,
            output_songs_dir=self.config.output_songs_folder,
            stems_folder=self.config.stems_folder
        )

        # Load song catalog
        self._load_catalog()

        # Screens will be created on demand
        self._generation_screen = None
        self._history_screen = None
        self._playlist_screen = None

    def _load_catalog(self):
        """Load the song catalog from JSON."""
        if not self.config.analysis_json.exists():
            print(f"Error: Analysis JSON not found: {self.config.analysis_json}", file=sys.stderr)
            sys.exit(1)

        songs = self.catalog.load_from_json(self.config.analysis_json)

        # Display warnings if any
        if self.catalog.warnings:
            print("\nWarnings during catalog loading:", file=sys.stderr)
            for warning in self.catalog.warnings:
                print(f"  - {warning}", file=sys.stderr)
            print()

        if len(songs) == 0:
            print("Error: No songs loaded from catalog", file=sys.stderr)
            sys.exit(1)

        print(f"Loaded {len(songs)} songs from catalog")

    def _get_generation_screen(self):
        """Get or create the generation screen."""
        if self._generation_screen is None:
            from stream_of_worship.tui.screens.generation import GenerationScreen
            self._generation_screen = GenerationScreen(
                self.state,
                self.catalog,
                self.playback,
                self.generation,
            )
        return self._generation_screen

    def _get_history_screen(self):
        """Get or create the history screen."""
        if self._history_screen is None:
            from stream_of_worship.tui.screens.history import HistoryScreen
            self._history_screen = HistoryScreen(
                self.state,
                self.catalog,
                self.playback,
                self.generation,
            )
        return self._history_screen

    def _get_playlist_screen(self):
        """Get or create the playlist screen."""
        if self._playlist_screen is None:
            from stream_of_worship.tui.screens.playlist import PlaylistScreen
            self._playlist_screen = PlaylistScreen(
                self.state,
                self.catalog,
                self.playback,
                self.generation,
            )
        return self._playlist_screen

    def switch_screen(self, screen_name: str):
        """Switch to a named screen.

        Args:
            screen_name: Name of the screen ('generation', 'history', or 'playlist')
        """
        # Stop playback when switching screens
        if self.playback.is_playing or self.playback.is_paused:
            self.playback.stop()

        # Store previous screen
        self.state.previous_screen = self.state.active_screen

        # Update subtitle based on screen
        if screen_name == "generation":
            self.state.active_screen = ActiveScreen.GENERATION
            self.sub_title = "Generation Screen"
            self.pop_screen()
            self.push_screen(self._get_generation_screen())
        elif screen_name == "history":
            self.state.active_screen = ActiveScreen.HISTORY
            self.sub_title = "History Screen"
            self.pop_screen()
            self.push_screen(self._get_history_screen())
        elif screen_name == "playlist":
            self.state.active_screen = ActiveScreen.PLAYLIST
            self.sub_title = "Playlist Screen"
            self.pop_screen()
            self.push_screen(self._get_playlist_screen())
        else:
            raise ValueError(f"Unknown screen: {screen_name}")

    def on_mount(self) -> None:
        """Handle app mount event."""
        # Push the initial screen based on state
        if self.state.active_screen == ActiveScreen.HISTORY:
            self.sub_title = "History Screen"
            self.push_screen(self._get_history_screen())
        elif self.state.active_screen == ActiveScreen.PLAYLIST:
            self.sub_title = "Playlist Screen"
            self.push_screen(self._get_playlist_screen())
        else:
            self.sub_title = "Generation Screen"
            self.push_screen(self._get_generation_screen())

    def action_quit(self) -> None:
        """Quit the application with cleanup."""
        self.playback.stop()
        self.exit()

    def action_switch_to_generation(self) -> None:
        """Switch to generation screen."""
        self.switch_screen("generation")

    def action_switch_to_history(self) -> None:
        """Switch to history screen."""
        self.switch_screen("history")

    def action_switch_to_playlist(self) -> None:
        """Switch to playlist screen."""
        self.switch_screen("playlist")


# For direct running of the app (from old transition_builder_v2)
def run_transition_builder():
    """Run the transition builder app with old-style config loading."""
    script_dir = Path(__file__).parent.parent.parent
    config_path = script_dir / "config.json"

    if not config_path.exists():
        print(f"Error: Config file not found: {config_path}", file=sys.stderr)
        sys.exit(1)

    app = TransitionBuilderApp(config_path)
    app.run()


if __name__ == "__main__":
    run_transition_builder()
