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

    def __init__(
        self,
        editor_state,
        playback_service,
        cache_dir,
        r2_client,
        db_client,
        hash_prefix,
        original_transcribed_content,
        feedback_user_id=None,
        recording_content_hash=None,
        visibility_status=None,
        analysis_client=None,
    ):
        super().__init__()
        self.editor_state = editor_state
        self.playback_service = playback_service
        self.cache_dir = cache_dir
        self.r2_client = r2_client
        self.db_client = db_client
        self.hash_prefix = hash_prefix
        self.original_transcribed_content = original_transcribed_content
        # Reviewer identity + full recording hash for the in-editor
        # feedback hotkey; None disables submitting (display still works).
        self.feedback_user_id = feedback_user_id
        self.recording_content_hash = recording_content_hash
        # Current visibility status, rendered as a badge in the header.
        # Session-constant: the command layer only mutates visibility after
        # app.run() returns.
        self.visibility_status = visibility_status
        # Analysis service client for the in-editor force-regeneration
        # hotkey (`G`); None keeps the editor read-only w.r.t. regeneration.
        self.analysis_client = analysis_client
        # Outcome of the last save/upload dialog attempt; None until the
        # user attempts a save/upload. Read by the command layer after
        # app.run() to drive the post-upload offers (issue #260).
        self.last_upload_result: UploadResult | None = None
        # True once the save/upload dialog ran at least once, even if the
        # user cancelled it or saved only a local draft. Lets the command
        # layer distinguish "session ended via quit-without-save" (no
        # offers, review loop re-picks) from a genuine attempt (issue #260).
        self.upload_attempted = False
        # True when the session ended via the quit dialog with no unsaved
        # changes; read by the command layer to offer a visibility change.
        self.quit_clean = False

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
            feedback_user_id=self.feedback_user_id,
            recording_content_hash=self.recording_content_hash,
            visibility_status=self.visibility_status,
            analysis_client=self.analysis_client,
        ))

    def _on_upload_result(self, result: UploadResult) -> None:
        # Keep the first success: a later blocked/partial re-upload in the
        # same session must not suppress the post-upload offers that a
        # successful R2 upload earned (issue #260).
        if self.last_upload_result is None or not self.last_upload_result.success:
            self.last_upload_result = result
