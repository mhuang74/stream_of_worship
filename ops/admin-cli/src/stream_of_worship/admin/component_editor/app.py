"""Textual app for the admin Component Metadata editor."""

import textual.constants

textual.constants.DISABLE_KITTY_KEY = True

from textual.app import App

from stream_of_worship.admin.component_editor.screen import ComponentEditorScreen
from stream_of_worship.admin.component_editor.state import ComponentEditorState
from stream_of_worship.admin.services.playback import PlaybackService


class ComponentEditorApp(App[None]):
    """Admin Component Metadata editor Textual application."""

    TITLE = "Component Metadata Editor"

    CSS = """
    Screen {
        layout: vertical;
    }
    """

    def __init__(
        self,
        editor_state: ComponentEditorState,
        playback_service: PlaybackService,
        cache_dir,
        r2_client,
        db_client,
    ):
        super().__init__()
        self.editor_state = editor_state
        self.playback_service = playback_service
        self.cache_dir = cache_dir
        self.r2_client = r2_client
        self.db_client = db_client

    def on_mount(self) -> None:
        self.push_screen(
            ComponentEditorScreen(
                editor_state=self.editor_state,
                playback_service=self.playback_service,
                cache_dir=self.cache_dir,
                r2_client=self.r2_client,
                db_client=self.db_client,
            )
        )
