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
from stream_of_worship.app.services.asset_cache import AssetCache
from stream_of_worship.app.services.audio_engine import AudioEngine
from stream_of_worship.app.services.catalog import CatalogService
from stream_of_worship.app.services.export import ExportService
from stream_of_worship.app.services.playback import PlaybackService
from stream_of_worship.app.services.video_engine import VideoEngine
from stream_of_worship.app.state import AppScreen, AppState


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

        # Screen instances (lazy loaded)
        self._screens: dict[AppScreen, object] = {}

    def on_mount(self) -> None:
        """Handle app mount event."""
        self.push_screen(self._get_or_create_screen(AppScreen.SONGSET_LIST))

    def _get_or_create_screen(self, screen: AppScreen):
        """Get or create a screen instance with caching.

        This is our custom screen caching logic (not Textual's internal _get_screen).
        Lazily instantiates screens on first access and caches them for reuse.

        Args:
            screen: Screen enum value

        Returns:
            Screen instance
        """
        if screen not in self._screens:
            if screen == AppScreen.SONGSET_LIST:
                from stream_of_worship.app.screens.songset_list import SongsetListScreen
                self._screens[screen] = SongsetListScreen(self.state, self.songset_client)
            elif screen == AppScreen.BROWSE:
                from stream_of_worship.app.screens.browse import BrowseScreen
                self._screens[screen] = BrowseScreen(self.state, self.catalog, self.songset_client)
            elif screen == AppScreen.SONGSET_EDITOR:
                from stream_of_worship.app.screens.songset_editor import SongsetEditorScreen
                self._screens[screen] = SongsetEditorScreen(
                    self.state, self.songset_client, self.catalog, self.playback
                )
            elif screen == AppScreen.TRANSITION_DETAIL:
                from stream_of_worship.app.screens.transition_detail import TransitionDetailScreen
                self._screens[screen] = TransitionDetailScreen(
                    self.state, self.songset_client, self.playback
                )
            elif screen == AppScreen.EXPORT_PROGRESS:
                from stream_of_worship.app.screens.export_progress import ExportProgressScreen
                self._screens[screen] = ExportProgressScreen(self.state, self.export_service)
            elif screen == AppScreen.SETTINGS:
                from stream_of_worship.app.screens.settings import SettingsScreen
                self._screens[screen] = SettingsScreen(self.state, self.config)

        return self._screens[screen]

    def navigate_to(self, screen: AppScreen) -> None:
        """Navigate to a screen.

        Args:
            screen: Screen to navigate to
        """
        # Stop playback when switching screens
        if self.playback.is_playing or self.playback.is_paused:
            self.playback.stop()

        self.state.navigate_to(screen)
        self.push_screen(self._get_or_create_screen(screen))

    def navigate_back(self) -> None:
        """Navigate back to the previous screen."""
        if self.state.navigate_back():
            self.pop_screen()

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
