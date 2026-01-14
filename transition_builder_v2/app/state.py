"""Application state model."""
from dataclasses import dataclass, field
from enum import Enum

from app.models.transition import TransitionRecord


class ActiveScreen(Enum):
    """Available screens in the application."""
    GENERATION = "generation"
    HISTORY = "history"
    SONG_SEARCH = "song_search"
    HELP_OVERLAY = "help_overlay"


class GenerationMode(Enum):
    """Generation screen modes."""
    FRESH = "fresh"
    MODIFY = "modify"


class PlaybackState(Enum):
    """Playback states."""
    PLAYING = "playing"
    PAUSED = "paused"
    STOPPED = "stopped"


@dataclass
class AppState:
    """Global application state."""
    # Screen management
    active_screen: ActiveScreen = ActiveScreen.GENERATION
    previous_screen: ActiveScreen | None = None

    # Generation state
    generation_mode: GenerationMode = GenerationMode.FRESH
    base_transition_id: int | None = None

    # Song/section selection
    left_song_id: str | None = None
    left_section_index: int | None = None
    right_song_id: str | None = None
    right_section_index: int | None = None

    # Parameters (base + extension)
    transition_type: str = "gap"
    overlap: float = 1.0  # in beats, can be negative (for Gap, this is gap duration)
    fade_window: float = 8.0  # in beats
    fade_speed: float = 2.0  # in beats
    stems_to_fade: list[str] = field(default_factory=lambda: ["all"])
    extension_parameters: dict = field(default_factory=dict)

    # Section adjustments (in beats, range: -4 to +4)
    from_section_start_adjust: int = 0  # negative = start earlier, positive = start later
    from_section_end_adjust: int = 0    # negative = end earlier, positive = end later
    to_section_start_adjust: int = 0
    to_section_end_adjust: int = 0

    # History
    transition_history: list[TransitionRecord] = field(default_factory=list)
    selected_history_index: int | None = None

    # Playback
    playback_target: str | None = None
    playback_position: float = 0.0  # seconds
    playback_state: PlaybackState = PlaybackState.STOPPED
    last_generated_transition_path: str | None = None  # Path to last generated transition

    # UI State
    active_validation_warnings: list[str] = field(default_factory=list)
    generation_in_progress: bool = False
    generation_start_time: float | None = None

    # Panel focus for Generation screen
    focused_panel: str = "song_a"  # "song_a", "song_b", "parameters"

    def reset_parameters(self):
        """Reset all parameters to defaults."""
        self.transition_type = "gap"
        self.overlap = 1.0
        self.fade_window = 8.0
        self.fade_speed = 2.0
        self.stems_to_fade = ["all"]
        self.extension_parameters = {}
        self.from_section_start_adjust = 0
        self.from_section_end_adjust = 0
        self.to_section_start_adjust = 0
        self.to_section_end_adjust = 0
        self.active_validation_warnings = []

    def exit_modify_mode(self):
        """Exit modify mode and return to fresh mode."""
        self.generation_mode = GenerationMode.FRESH
        self.base_transition_id = None
        self.reset_parameters()
        # Clear selections
        self.left_song_id = None
        self.left_section_index = None
        self.right_song_id = None
        self.right_section_index = None

    def enter_modify_mode(self, transition: TransitionRecord):
        """Enter modify mode with a transition's parameters."""
        self.generation_mode = GenerationMode.MODIFY
        self.base_transition_id = transition.id

        # Load parameters from transition
        self.left_song_id = transition.song_a_filename
        self.right_song_id = transition.song_b_filename
        # Section indices would need to be looked up from the catalog

        params = transition.parameters
        self.transition_type = params.get("type", "crossfade")
        self.overlap = params.get("overlap", 4.0)
        self.fade_window = params.get("fade_window", 8.0)
        self.fade_speed = params.get("fade_speed", 2.0)
        self.stems_to_fade = params.get("stems_to_fade", ["all"])
        self.extension_parameters = params.get("extension", {})

    def add_transition(self, transition: TransitionRecord):
        """Add a transition to history, enforcing the 50-item cap."""
        self.transition_history.insert(0, transition)  # Newest first
        if len(self.transition_history) > 50:
            self.transition_history.pop()  # Remove oldest

    def get_selected_transition(self) -> TransitionRecord | None:
        """Get the currently selected transition from history."""
        if self.selected_history_index is not None and 0 <= self.selected_history_index < len(self.transition_history):
            return self.transition_history[self.selected_history_index]
        return None
