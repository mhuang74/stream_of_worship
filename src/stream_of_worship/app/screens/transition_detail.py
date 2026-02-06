"""Transition detail screen.

Allows fine-tuning of transition parameters between songs.
"""

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Input, Label, Select, Static, Switch

from stream_of_worship.app.db.songset_client import SongsetClient
from stream_of_worship.app.services.playback import PlaybackService
from stream_of_worship.app.state import AppState


class TransitionDetailScreen(Screen):
    """Screen for editing transition details."""

    BINDINGS = [
        ("s", "save", "Save"),
        ("p", "preview", "Preview"),
        ("escape", "back", "Back"),
    ]

    def __init__(
        self,
        state: AppState,
        songset_client: SongsetClient,
        playback: PlaybackService,
    ):
        """Initialize the screen.

        Args:
            state: Application state
            songset_client: Songset database client
            playback: Playback service
        """
        super().__init__()
        self.state = state
        self.songset_client = songset_client
        self.playback = playback

    def compose(self) -> ComposeResult:
        """Compose the screen layout."""
        yield Header()

        with Vertical():
            yield Label("[bold]Transition Settings[/bold]", id="title")
            yield Label(id="song_info")

            with Horizontal(id="gap_row"):
                yield Label("Gap (beats):")
                yield Input(id="gap_input", value="2.0")

            with Horizontal(id="crossfade_row"):
                yield Label("Use Crossfade:")
                yield Switch(id="crossfade_switch")

            with Horizontal(id="duration_row"):
                yield Label("Crossfade Duration (s):")
                yield Input(id="duration_input", value="4.0")

            with Horizontal(id="key_row"):
                yield Label("Key Shift (semitones):")
                yield Input(id="key_input", value="0")

            with Horizontal(id="buttons"):
                yield Button("Save", id="btn_save", variant="primary")
                yield Button("Preview", id="btn_preview")
                yield Button("Back", id="btn_back")

        yield Footer()

    def on_mount(self) -> None:
        """Handle mount event."""
        self._load_values()

    def _load_values(self) -> None:
        """Load current values into inputs."""
        item = self.state.selected_item
        if not item:
            return

        info_label = self.query_one("#song_info", Label)
        info_label.update(f"Editing: {item.song_title or 'Unknown'}")

        gap_input = self.query_one("#gap_input", Input)
        gap_input.value = str(item.gap_beats or 2.0)

        crossfade_switch = self.query_one("#crossfade_switch", Switch)
        crossfade_switch.value = item.crossfade_enabled

        duration_input = self.query_one("#duration_input", Input)
        duration_input.value = str(item.crossfade_duration_seconds or 4.0)

        key_input = self.query_one("#key_input", Input)
        key_input.value = str(item.key_shift_semitones or 0)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses."""
        button_id = event.button.id

        if button_id == "btn_save":
            self.action_save()
        elif button_id == "btn_preview":
            self.action_preview()
        elif button_id == "btn_back":
            self.app.navigate_back()

    def action_save(self) -> None:
        """Save transition settings."""
        item = self.state.selected_item
        if not item:
            return

        try:
            gap_beats = float(self.query_one("#gap_input", Input).value)
            crossfade_enabled = self.query_one("#crossfade_switch", Switch).value
            crossfade_duration = float(self.query_one("#duration_input", Input).value)
            key_shift = int(self.query_one("#key_input", Input).value)

            self.songset_client.update_item(
                item_id=item.id,
                gap_beats=gap_beats,
                crossfade_enabled=crossfade_enabled,
                crossfade_duration_seconds=crossfade_duration,
                key_shift_semitones=key_shift,
            )

            self.notify("Settings saved")
        except ValueError as e:
            self.notify(f"Invalid value: {e}", severity="error")

    def action_preview(self) -> None:
        """Preview the transition."""
        self.notify("Preview not yet implemented")

    def action_back(self) -> None:
        """Go back."""
        self.app.navigate_back()
