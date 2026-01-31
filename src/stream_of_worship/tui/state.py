"""Application state model with playlist support."""
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

from stream_of_worship.tui.models.transition import TransitionRecord


class ActiveScreen(Enum):
    """Available screens in application."""
    GENERATION = "generation"
    HISTORY = "history"
    PLAYLIST = "playlist"
    DISCOVERY = "discovery"
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
    """Global application state with playlist support."""

    # Screen management
    active_screen: ActiveScreen = ActiveScreen.GENERATION
    previous_screen: Optional[ActiveScreen] = None

    # Generation state (legacy 2-song mode)
    generation_mode: GenerationMode = GenerationMode.FRESH
    base_transition_id: Optional[int] = None

    # Song/section selection (legacy 2-song mode)
    left_song_id: Optional[str] = None
    left_section_index: Optional[int] = None
    right_song_id: Optional[str] = None
    right_section_index: Optional[int] = None

    # Parameters (base + extension)
    transition_type: str = "gap"
    overlap: float = 1.0  # in beats, can be negative (for Gap, this is gap duration)
    fade_window: float = 8.0  # in beats (total: half for fade-out, half for fade-in)
    fade_speed: float = 2.0  # in beats (unused for gap, kept for crossfade compatibility)
    fade_bottom: float = 0.33  # min volume during fade (0.0 to 1.0)
    # Default: fade all stems except vocals (bass, drums, other)
    stems_to_fade: List[str] = field(default_factory=lambda: ["bass", "drums", "other"])
    extension_parameters: dict = field(default_factory=dict)

    # Section adjustments (in beats, range: -4 to +4)
    from_section_start_adjust: int = 0  # negative = start earlier, positive = start later
    from_section_end_adjust: int = 0    # negative = end earlier, positive = end later
    to_section_start_adjust: int = 0
    to_section_end_adjust: int = 0

    # History
    transition_history: List[TransitionRecord] = field(default_factory=list)
    selected_history_index: Optional[int] = None

    # Playback
    playback_target: Optional[str] = None
    playback_position: float = 0.0  # seconds
    playback_state: PlaybackState = PlaybackState.STOPPED
    last_generated_transition_path: Optional[str] = None  # Path to last generated transition

    # UI State
    active_validation_warnings: List[str] = field(default_factory=list)
    generation_in_progress: bool = False
    generation_start_time: Optional[float] = None

    # Panel focus for Generation screen
    focused_panel: str = "song_a"  # "song_a", "song_b", "parameters"

    # ===== NEW: Playlist Support =====
    playlist_name: str = "Untitled Playlist"
    playlist_items: List[str] = field(default_factory=list)  # List of song IDs
    selected_playlist_index: Optional[int] = None  # Currently selected song in playlist
    editing_transition_index: Optional[int] = None  # Which transition is being edited

    def add_song_to_playlist(self, song_id: str, index: Optional[int] = None) -> None:
        """Add a song to the playlist.

        Args:
            song_id: ID of song to add
            index: Position to insert (None = append to end)
        """
        if index is None:
            self.playlist_items.append(song_id)
        else:
            self.playlist_items.insert(index, song_id)

    def remove_song_from_playlist(self, index: int) -> Optional[str]:
        """Remove a song from the playlist.

        Args:
            index: Index of song to remove

        Returns:
            Removed song ID, or None if index invalid
        """
        if 0 <= index < len(self.playlist_items):
            return self.playlist_items.pop(index)
        return None

    def move_playlist_song(self, from_index: int, to_index: int) -> bool:
        """Move a song to a new position.

        Args:
            from_index: Current position
            to_index: New position

        Returns:
            True if successful, False otherwise
        """
        if not (0 <= from_index < len(self.playlist_items) and
                0 <= to_index < len(self.playlist_items)):
            return False

        if from_index == to_index:
            return True

        song_id = self.playlist_items.pop(from_index)
        self.playlist_items.insert(to_index, song_id)
        return True

    def clear_playlist(self) -> None:
        """Clear all items from playlist."""
        self.playlist_items.clear()
        self.playlist_name = "Untitled Playlist"

    def get_playlist_song_id(self, index: int) -> Optional[str]:
        """Get song ID at playlist index.

        Args:
            index: Index to get

        Returns:
            Song ID, or None if index invalid
        """
        if 0 <= index < len(self.playlist_items):
            return self.playlist_items[index]
        return None

    def reset_parameters(self):
        """Reset all parameters to defaults."""
        self.transition_type = "gap"
        self.overlap = 1.0
        self.fade_window = 8.0
        self.fade_speed = 2.0
        self.fade_bottom = 0.33
        self.stems_to_fade = ["bass", "drums", "other"]
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
        # Section indices are looked up from catalog in History screen

        params = transition.parameters
        self.transition_type = params.get("type", "gap")

        # Handle gap vs overlap based on transition type
        if self.transition_type == "gap":
            # For gap transitions, use gap_beats as overlap
            self.overlap = params.get("gap_beats", 1.0)
        else:
            self.overlap = params.get("overlap", 4.0)

        self.fade_window = params.get("fade_window", 8.0)
        self.fade_speed = params.get("fade_speed", 2.0)
        self.fade_bottom = params.get("fade_bottom", 0.0)
        self.stems_to_fade = params.get("stems_to_fade", ["all"])
        self.extension_parameters = params.get("extension", {})

    def add_transition(self, transition: TransitionRecord):
        """Add a transition to history, enforcing 50-item cap."""
        self.transition_history.insert(0, transition)  # Newest first
        if len(self.transition_history) > 50:
            self.transition_history.pop()  # Remove oldest

    def get_selected_transition(self) -> Optional[TransitionRecord]:
        """Get currently selected transition from history."""
        if self.selected_history_index is not None and \
           0 <= self.selected_history_index < len(self.transition_history):
            return self.transition_history[self.selected_history_index]
        return None
