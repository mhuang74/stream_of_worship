"""Textual application for the admin LRC editor.

Launches the interactive LRC editor as a Textual TUI application.
"""

import textual.constants

textual.constants.DISABLE_KITTY_KEY = True

from textual.app import App

from stream_of_worship.admin.editor.screen import LRCEditorScreen
from stream_of_worship.admin.editor.upload import UploadResult


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
        # Outcome of the last save/upload dialog attempt; None until the
        # user attempts a save/upload. Read by the command layer after
        # app.run() to drive the post-upload offers (issue #260).
        self.last_upload_result: UploadResult | None = None
        # True once the save/upload dialog ran at least once, even if the
        # user cancelled it or saved only a local draft. Lets the command
        # layer distinguish "session ended via quit-without-save" (no
        # offers, review loop re-picks) from a genuine attempt (issue #260).
        self.upload_attempted = False

    def on_mount(self) -> None:
        self.push_screen(LRCEditorScreen(
            editor_state=self.editor_state,
            playback_service=self.playback_service,
            cache_dir=self.cache_dir,
            r2_client=self.r2_client,
            db_client=self.db_client,
            hash_prefix=self.hash_prefix,
            original_transcribed_content=self.original_transcribed_content,
            upload_result_out=self._on_upload_result,
        ))

    def _on_upload_result(self, result: UploadResult) -> None:
        # Keep the first success: a later blocked/partial re-upload in the
        # same session must not suppress the post-upload offers that a
        # successful R2 upload earned (issue #260).
        if self.last_upload_result is None or not self.last_upload_result.success:
            self.last_upload_result = result
