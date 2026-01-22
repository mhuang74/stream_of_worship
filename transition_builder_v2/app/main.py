"""Main entry point for the Song Transition Preview App."""
import sys
from pathlib import Path

from textual.app import App

from app.state import AppState, ActiveScreen
from app.services.catalog import SongCatalogLoader
from app.services.playback import PlaybackService
from app.services.generation import TransitionGenerationService
from app.utils.config import Config
from app.utils.logger import init_error_logger, get_error_logger, init_session_logger
from app.screens.generation import GenerationScreen
from app.screens.history import HistoryScreen


class TransitionBuilderApp(App):
    """Main application for song transition preview."""

    CSS_PATH = ["screens/generation.tcss", "screens/history.tcss"]
    TITLE = "Song Transition Preview"

    def __init__(self, config_path: Path, *args, **kwargs):
        """Initialize the application.

        Args:
            config_path: Path to config.json
        """
        super().__init__(*args, **kwargs)

        # Load configuration
        try:
            self.config = Config.load(config_path)
        except (FileNotFoundError, ValueError) as e:
            print(f"Error loading configuration: {e}", file=sys.stderr)
            sys.exit(1)

        # Initialize error logger
        # Log file is created in the same directory as config.json
        log_path = config_path.parent / "transitions_errors.log"
        init_error_logger(log_path=log_path, enabled=self.config.error_logging)

        # Initialize session logger
        session_log_path = config_path.parent / "transitions_session.log"
        init_session_logger(log_path=session_log_path, enabled=self.config.session_logging)

        # Initialize state
        self.state = AppState()

        # Initialize services
        self.catalog = SongCatalogLoader(self.config.audio_folder)
        self.playback = PlaybackService()
        self.generation = TransitionGenerationService(
            self.config.output_folder,
            stems_folder=self.config.stems_folder
        )

        # Load song catalog
        self._load_catalog()

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

    def _create_screen(self, screen_name: str):
        """Create a screen instance by name.

        Args:
            screen_name: Name of the screen ('generation' or 'history')

        Returns:
            Screen instance with all required dependencies
        """
        if screen_name == "generation":
            return GenerationScreen(self.state, self.catalog, self.playback, self.generation)
        elif screen_name == "history":
            return HistoryScreen(self.state, self.catalog, self.playback, self.generation)
        else:
            raise ValueError(f"Unknown screen: {screen_name}")

    def switch_screen(self, screen_name: str):
        """Switch to a named screen.

        Args:
            screen_name: Name of the screen to switch to
        """
        # Stop playback when switching screens
        if self.playback.is_playing or self.playback.is_paused:
            self.playback.stop()

        # Update subtitle based on screen
        if screen_name == "generation":
            self.sub_title = "Generation Screen"
        elif screen_name == "history":
            self.sub_title = "History Screen"

        # Switch to the new screen
        self.pop_screen()
        self.push_screen(self._create_screen(screen_name))

    def on_mount(self) -> None:
        """Handle app mount event."""
        # Push the initial screen based on state
        if self.state.active_screen == ActiveScreen.HISTORY:
            self.sub_title = "History Screen"
            self.push_screen(self._create_screen("history"))
        else:
            self.sub_title = "Generation Screen"
            self.push_screen(self._create_screen("generation"))

    def _cleanup_unsaved_transitions(self):
        """Remove generated transition files that weren't saved by the user."""
        output_folder = self.config.output_folder
        if not output_folder.exists():
            return

        deleted_count = 0
        logger = get_error_logger()
        for file_path in output_folder.glob("*.flac"):
            # Keep files that start with 'saved_transition_'
            if not file_path.name.startswith("saved_transition_"):
                try:
                    file_path.unlink()
                    deleted_count += 1
                except Exception as e:
                    if logger:
                        logger.log_file_error(str(file_path), e, operation="delete")

        if deleted_count > 0:
            print(f"Cleaned up {deleted_count} unsaved transition file(s)")

    def action_quit(self) -> None:
        """Quit the application with cleanup."""
        self.playback.stop()
        self._cleanup_unsaved_transitions()
        self.exit()


def main():
    """Main entry point."""
    # Determine config path
    script_dir = Path(__file__).parent.parent
    config_path = script_dir / "config.json"

    if not config_path.exists():
        print(f"Error: Config file not found: {config_path}", file=sys.stderr)
        sys.exit(1)

    # Create and run the app
    app = TransitionBuilderApp(config_path)
    app.run()


if __name__ == "__main__":
    main()
