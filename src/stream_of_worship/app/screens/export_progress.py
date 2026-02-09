"""Export progress screen.

Shows progress of audio/video export with cancel option.
"""

from datetime import timedelta

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Label, ProgressBar, Static

from stream_of_worship.app.services.export import ExportProgress, ExportService, ExportState
from stream_of_worship.app.state import AppState


class ExportProgressScreen(Screen):
    """Screen for showing export progress."""

    BINDINGS = [
        ("c", "cancel", "Cancel"),
        ("escape", "back", "Back"),
    ]

    def __init__(
        self,
        state: AppState,
        export_service: ExportService,
    ):
        """Initialize the screen.

        Args:
            state: Application state
            export_service: Export service
        """
        super().__init__()
        self.state = state
        self.export_service = export_service

    def compose(self) -> ComposeResult:
        """Compose the screen layout."""
        yield Header()

        with Vertical():
            yield Label("[bold]Export Progress[/bold]", id="title")
            yield Label(id="status_label", content="Preparing...")

            yield ProgressBar(id="progress_bar", total=100)

            yield Label(id="detail_label", content="")

            with Horizontal(id="buttons"):
                yield Button("Cancel", id="btn_cancel", variant="error")
                yield Button("Back", id="btn_back")

        yield Footer()

    def on_mount(self) -> None:
        """Handle mount event."""
        # Register for progress updates
        self.export_service.register_progress_callback(self._on_progress)
        self.export_service.register_completion_callback(self._on_complete)

        # Start export
        self._start_export()

    def _start_export(self) -> None:
        """Start the export operation."""
        songset = self.state.selected_songset
        items = self.state.current_songset_items

        if not songset or not items:
            self.notify("No songset to export", severity="error")
            return

        # Start async export
        self.export_service.export_async(
            songset=songset,
            items=items,
            include_video=True,
        )

    def _on_progress(self, progress: ExportProgress) -> None:
        """Handle progress update.

        Args:
            progress: Current progress
        """
        self.app.call_from_thread(self._update_ui, progress)

    def _update_ui(self, progress: ExportProgress) -> None:
        """Update UI with progress (called from main thread).

        Args:
            progress: Current progress
        """
        status_label = self.query_one("#status_label", Label)
        status_label.update(f"[bold]{progress.step_description}[/bold]")

        detail_label = self.query_one("#detail_label", Label)
        detail_label.update(f"Step {progress.current_step} of {progress.total_steps}")

        progress_bar = self.query_one("#progress_bar", ProgressBar)
        progress_bar.progress = progress.percent_complete

    def _on_complete(self, job, success: bool) -> None:
        """Handle export completion.

        Args:
            job: Completed export job
            success: Whether export succeeded
        """
        def update():
            status_label = self.query_one("#status_label", Label)
            detail_label = self.query_one("#detail_label", Label)

            if success:
                status_label.update("[bold green]Export complete![/bold green]")
                self.notify(f"Exported to: {job.output_audio_path}")

                # Calculate and display elapsed time
                if job.started_at and job.completed_at:
                    elapsed = job.completed_at - job.started_at
                    if elapsed.total_seconds() < 60:
                        time_str = f"{elapsed.total_seconds():.1f}s"
                    else:
                        time_str = str(timedelta(seconds=int(elapsed.total_seconds())))
                    detail_label.update(f"Total time: {time_str}")
            else:
                status_label.update("[bold red]Export failed![/bold red]")

        self.app.call_from_thread(update)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses."""
        button_id = event.button.id

        if button_id == "btn_cancel":
            self.action_cancel()
        elif button_id == "btn_back":
            self.app.navigate_back()

    def action_cancel(self) -> None:
        """Cancel the export."""
        if self.export_service.is_exporting:
            self.export_service.cancel()
            self.notify("Export cancelled")

    def action_back(self) -> None:
        """Go back."""
        self.app.navigate_back()
