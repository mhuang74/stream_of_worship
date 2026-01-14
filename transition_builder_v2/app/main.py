"""Main entry point for the Song Transition Preview App."""
import sys
from pathlib import Path

from textual.app import App

from app.state import AppState
from app.services.catalog import SongCatalogLoader
from app.services.playback import PlaybackService
from app.services.generation import TransitionGenerationService
from app.utils.config import Config
from app.screens.generation import GenerationScreen


class TransitionBuilderApp(App):
    """Main application for song transition preview."""

    CSS_PATH = "screens/generation.tcss"
    TITLE = "Song Transition Preview"
    SUB_TITLE = "Generation Screen"

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

        # Initialize state
        self.state = AppState()

        # Initialize services
        self.catalog = SongCatalogLoader(self.config.audio_folder)
        self.playback = PlaybackService()
        self.generation = TransitionGenerationService(self.config.output_folder)

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

    def on_mount(self) -> None:
        """Handle app mount event."""
        # Push the generation screen
        self.push_screen(GenerationScreen(self.state, self.catalog, self.playback, self.generation))


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
