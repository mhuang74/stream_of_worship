"""Main TUI application for Stream of Worship User App.

Textual-based application for worship leaders to browse songs,
manage songsets, and export audio/video.
"""

import sys
from pathlib import Path

from textual.app import App

from stream_of_worship.admin.services.r2 import R2Client
from stream_of_worship.app.config import AppConfig
from stream_of_worship.app.db.read_client import ReadOnlyClient
from stream_of_worship.app.db.songset_client import SongsetClient
from stream_of_worship.app.logging_config import get_logger
from stream_of_worship.app.services.asset_cache import AssetCache
from stream_of_worship.app.services.audio_engine import AudioEngine
from stream_of_worship.app.services.catalog import CatalogService
from stream_of_worship.app.services.export import ExportService
from stream_of_worship.app.services.playback import PlaybackService
from stream_of_worship.app.services.video_engine import VideoEngine
from stream_of_worship.app.state import AppScreen, AppState

logger = get_logger(__name__)


class SowApp(App):
    """Main Stream of Worship User Application.

    Provides a Textual TUI for browsing songs, managing songsets,
    and exporting worship sets with gap transitions.
    """

    CSS_PATH = "screens/app.tcss"
    TITLE = "Stream of Worship"
    SUB_TITLE = "Songset Manager"

    def __init__(self, config: AppConfig, *args, **kwargs):
        """Initialize the application.

        Args:
            config: Application configuration
        """
        super().__init__(*args, **kwargs)

        self.config = config
        self.config.ensure_directories()

        # Initialize state
        self.state = AppState()

        # Initialize database clients
        self.read_client = ReadOnlyClient(config.db_path)
        self.songset_client = SongsetClient(config.db_path)
        self.songset_client.initialize_schema()

        # Initialize services
        self.catalog = CatalogService(self.read_client)
        self.r2_client = R2Client(
            bucket=config.r2_bucket,
            endpoint_url=config.r2_endpoint_url,
            region=config.r2_region,
        )
        self.asset_cache = AssetCache(
            cache_dir=config.cache_dir,
            r2_client=self.r2_client,
        )
        self.playback = PlaybackService(
            buffer_ms=config.preview_buffer_ms,
            volume=config.preview_volume,
        )
        self.audio_engine = AudioEngine(
            asset_cache=self.asset_cache,
            target_lufs=-14.0,
        )
        self.video_engine = VideoEngine(
            asset_cache=self.asset_cache,
            template=VideoEngine.get_template(config.default_video_template),
        )
        self.export_service = ExportService(
            asset_cache=self.asset_cache,
            audio_engine=self.audio_engine,
            video_engine=self.video_engine,
            output_dir=config.output_dir,
        )

    def on_mount(self) -> None:
        """Handle app mount event."""
        logger.info("App mounted, pushing initial screen: SONGSET_LIST")
        self.push_screen(self._create_screen(AppScreen.SONGSET_LIST))

    def _create_screen(self, screen: AppScreen):
        """Create a fresh screen instance.

        Creates a new screen instance on each call to avoid Textual issues
        with pushing the same screen instance multiple times.

        Args:
            screen: Screen enum value

        Returns:
            New screen instance
        """
        logger.debug(f"Creating fresh screen instance: {screen.name}")
        if screen == AppScreen.SONGSET_LIST:
            from stream_of_worship.app.screens.songset_list import SongsetListScreen
            return SongsetListScreen(self.state, self.songset_client)
        elif screen == AppScreen.BROWSE:
            from stream_of_worship.app.screens.browse import BrowseScreen
            return BrowseScreen(self.state, self.catalog, self.songset_client)
        elif screen == AppScreen.SONGSET_EDITOR:
            from stream_of_worship.app.screens.songset_editor import SongsetEditorScreen
            return SongsetEditorScreen(
                self.state, self.songset_client, self.catalog, self.playback
            )
        elif screen == AppScreen.TRANSITION_DETAIL:
            from stream_of_worship.app.screens.transition_detail import TransitionDetailScreen
            return TransitionDetailScreen(
                self.state, self.songset_client, self.playback
            )
        elif screen == AppScreen.EXPORT_PROGRESS:
            from stream_of_worship.app.screens.export_progress import ExportProgressScreen
            return ExportProgressScreen(self.state, self.export_service)
        elif screen == AppScreen.SETTINGS:
            from stream_of_worship.app.screens.settings import SettingsScreen
            return SettingsScreen(self.state, self.config)

    def navigate_to(self, screen: AppScreen) -> None:
        """Navigate to a screen.

        Args:
            screen: Screen to navigate to
        """
        logger.info(f"Navigate to: {screen.name} (from {self.state.current_screen.name})")

        # Stop playback when switching screens
        if self.playback.is_playing or self.playback.is_paused:
            logger.debug("Stopping playback before navigation")
            self.playback.stop()

        self.state.navigate_to(screen)
        self.push_screen(self._create_screen(screen))
        logger.debug(f"Screen pushed, stack depth: {len(self.screen_stack)}")

    def navigate_back(self) -> None:
        """Navigate back to the previous screen."""
        logger.info(
            f"Navigate back requested (current: {self.state.current_screen.name}, "
            f"previous: {self.state.previous_screen.name if self.state.previous_screen else 'None'})"
        )
        if self.state.navigate_back():
            logger.info(f"Popping screen, stack depth before: {len(self.screen_stack)}")
            self.pop_screen()
            logger.info(
                f"Screen popped, stack depth after: {len(self.screen_stack)}, "
                f"current screen: {self.state.current_screen.name}"
            )
        else:
            logger.warning("Cannot navigate back - no previous screen")

    def action_quit(self) -> None:
        """Quit the application with cleanup."""
        self.playback.stop()
        self.read_client.close()
        self.songset_client.close()
        self.exit()

    def action_navigate_songsets(self) -> None:
        """Navigate to songset list."""
        self.navigate_to(AppScreen.SONGSET_LIST)

    def action_navigate_browse(self) -> None:
        """Navigate to song browse."""
        self.navigate_to(AppScreen.BROWSE)

    def action_navigate_settings(self) -> None:
        """Navigate to settings."""
        self.navigate_to(AppScreen.SETTINGS)

    def action_back(self) -> None:
        """Go back to previous screen."""
        self.navigate_back()
