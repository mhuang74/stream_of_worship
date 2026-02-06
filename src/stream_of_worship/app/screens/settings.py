"""Settings screen.

Allows viewing and editing application settings.
"""

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Input, Label, Select, Static

from stream_of_worship.app.config import AppConfig
from stream_of_worship.app.services.video_engine import VideoEngine
from stream_of_worship.app.state import AppState


class SettingsScreen(Screen):
    """Screen for application settings."""

    BINDINGS = [
        ("s", "save", "Save"),
        ("escape", "back", "Back"),
    ]

    def __init__(
        self,
        state: AppState,
        config: AppConfig,
    ):
        """Initialize the screen.

        Args:
            state: Application state
            config: Application configuration
        """
        super().__init__()
        self.state = state
        self.config = config

    def compose(self) -> ComposeResult:
        """Compose the screen layout."""
        yield Header()

        with Vertical():
            yield Label("[bold]Settings[/bold]", id="title")

            with Horizontal(id="cache_row"):
                yield Label("Cache Directory:")
                yield Input(id="cache_input", value=str(self.config.cache_dir))

            with Horizontal(id="output_row"):
                yield Label("Output Directory:")
                yield Input(id="output_input", value=str(self.config.output_dir))

            with Horizontal(id="gap_row"):
                yield Label("Default Gap (beats):")
                yield Input(id="gap_input", value=str(self.config.default_gap_beats))

            with Horizontal(id="template_row"):
                yield Label("Video Template:")
                templates = VideoEngine.get_available_templates()
                yield Select(
                    [(t, t) for t in templates],
                    id="template_select",
                    value=self.config.default_video_template,
                )

            with Horizontal(id="buttons"):
                yield Button("Save", id="btn_save", variant="primary")
                yield Button("Back", id="btn_back")

        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses."""
        button_id = event.button.id

        if button_id == "btn_save":
            self.action_save()
        elif button_id == "btn_back":
            self.app.navigate_back()

    def action_save(self) -> None:
        """Save settings."""
        try:
            self.config.cache_dir = __import__("pathlib").Path(
                self.query_one("#cache_input", Input).value
            )
            self.config.output_dir = __import__("pathlib").Path(
                self.query_one("#output_input", Input).value
            )
            self.config.default_gap_beats = float(
                self.query_one("#gap_input", Input).value
            )
            self.config.default_video_template = self.query_one(
                "#template_select", Select
            ).value

            self.config.save()
            self.notify("Settings saved")
        except Exception as e:
            self.notify(f"Error saving: {e}", severity="error")

    def action_back(self) -> None:
        """Go back."""
        self.app.navigate_back()
