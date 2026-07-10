"""Textual application for the admin LRC editor.

Launches the interactive LRC editor as a Textual TUI application.
"""

import textual.constants

textual.constants.DISABLE_KITTY_KEY = True

from textual.app import App

from stream_of_worship.admin.editor.screen import LRCEditorScreen


class LRCEditorApp(App[None]):
    """Admin LRC editor Textual application."""

    TITLE = "LRC Editor"

    CSS = """
    Screen {
        layout: vertical;
    }
    """

    def __init__(self, editor_state, playback_service, cache_dir, r2_client, db_client, hash_prefix, original_transcribed_content):
        super().__init__()
        self.editor_state = editor_state
        self.playback_service = playback_service
        self.cache_dir = cache_dir
        self.r2_client = r2_client
        self.db_client = db_client
        self.hash_prefix = hash_prefix
        self.original_transcribed_content = original_transcribed_content

    def on_mount(self) -> None:
        self.push_screen(LRCEditorScreen(
            editor_state=self.editor_state,
            playback_service=self.playback_service,
            cache_dir=self.cache_dir,
            r2_client=self.r2_client,
            db_client=self.db_client,
            hash_prefix=self.hash_prefix,
            original_transcribed_content=self.original_transcribed_content,
        ))
