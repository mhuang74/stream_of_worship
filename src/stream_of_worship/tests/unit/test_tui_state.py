"""Tests for TUI state management."""

from datetime import datetime
from pathlib import Path
from uuid import uuid4
import pytest

from stream_of_worship.tui.state import (
    ActiveScreen,
    GenerationMode,
    PlaybackState,
    AppState,
)
from stream_of_worship.tui.models.transition import TransitionParams, TransitionRecord


class TestActiveScreen:
    """Tests for ActiveScreen enum."""

    def test_all_values_defined(self):
        """Test that all screen enum values are defined."""
        assert ActiveScreen.GENERATION.value == "generation"
        assert ActiveScreen.HISTORY.value == "history"
        assert ActiveScreen.PLAYLIST.value == "playlist"
        assert ActiveScreen.DISCOVERY.value == "discovery"
        assert ActiveScreen.SONG_SEARCH.value == "song_search"
        assert ActiveScreen.HELP_OVERLAY.value == "help_overlay"


class TestGenerationMode:
    """Tests for GenerationMode enum."""

    def test_values_defined(self):
        """Test that generation mode values are defined."""
        assert GenerationMode.FRESH.value == "fresh"
        assert GenerationMode.MODIFY.value == "modify"


class TestPlaybackState:
    """Tests for PlaybackState enum."""

    def test_values_defined(self):
        """Test that playback state values are defined."""
        assert PlaybackState.PLAYING.value == "playing"
        assert PlaybackState.PAUSED.value == "paused"
        assert PlaybackState.STOPPED.value == "stopped"


class TestAppState:
    """Tests for AppState dataclass."""

    def test_default_values(self):
        """Test default application state values."""
        state = AppState()

        # Screen management
        assert state.active_screen == ActiveScreen.GENERATION
        assert state.previous_screen is None

        # Generation mode
        assert state.generation_mode == GenerationMode.FRESH
        assert state.base_transition_id is None

        # Song selection
        assert state.left_song_id is None
        assert state.left_section_index is None
        assert state.right_song_id is None
        assert state.right_section_index is None

        # Parameters
        assert state.transition_type == "gap"
        assert state.overlap == 1.0
        assert state.fade_window == 8.0
        assert state.fade_speed == 2.0
        assert state.fade_bottom == 0.33
        assert state.stems_to_fade == ["bass", "drums", "other"]
        assert state.extension_parameters == {}

        # Section adjustments
        assert state.from_section_start_adjust == 0
        assert state.from_section_end_adjust == 0
        assert state.to_section_start_adjust == 0
        assert state.to_section_end_adjust == 0

        # History
        assert state.transition_history == []
        assert state.selected_history_index is None

        # Playback
        assert state.playback_target is None
        assert state.playback_position == 0.0
        assert state.playback_state == PlaybackState.STOPPED
        assert state.last_generated_transition_path is None

        # UI state
        assert state.active_validation_warnings == []
        assert state.generation_in_progress is False
        assert state.generation_start_time is None
        assert state.focused_panel == "song_a"

        # Playlist support
        assert state.playlist_name == "Untitled Playlist"
        assert state.playlist_items == []
        assert state.selected_playlist_index is None
        assert state.editing_transition_index is None

    def test_add_song_to_playlist_append(self):
        """Test adding song to end of playlist."""
        state = AppState()

        state.add_song_to_playlist("song_1")
        state.add_song_to_playlist("song_2")

        assert len(state.playlist_items) == 2
        assert state.playlist_items[0] == "song_1"
        assert state.playlist_items[1] == "song_2"

    def test_add_song_to_playlist_at_index(self):
        """Test adding song at specific index."""
        state = AppState()

        state.add_song_to_playlist("song_1")
        state.add_song_to_playlist("song_2")

        # Insert at position 1
        state.add_song_to_playlist("song_3", index=1)

        assert len(state.playlist_items) == 3
        assert state.playlist_items[0] == "song_1"
        assert state.playlist_items[1] == "song_3"
        assert state.playlist_items[2] == "song_2"

    def test_remove_song_from_playlist_success(self):
        """Test removing song from playlist."""
        state = AppState()

        state.add_song_to_playlist("song_1")
        state.add_song_to_playlist("song_2")
        state.add_song_to_playlist("song_3")

        removed = state.remove_song_from_playlist(1)

        assert removed == "song_2"
        assert len(state.playlist_items) == 2
        assert state.playlist_items[0] == "song_1"
        assert state.playlist_items[1] == "song_3"

    def test_remove_song_from_playlist_invalid_index(self):
        """Test removing with invalid index."""
        state = AppState()

        state.add_song_to_playlist("song_1")
        removed = state.remove_song_from_playlist(10)

        assert removed is None
        assert len(state.playlist_items) == 1

    def test_remove_song_from_playlist_negative_index(self):
        """Test removing with negative index."""
        state = AppState()

        state.add_song_to_playlist("song_1")
        removed = state.remove_song_from_playlist(-1)

        assert removed is None

    def test_move_playlist_song_forward(self):
        """Test moving song forward in playlist."""
        state = AppState()

        state.add_song_to_playlist("song_1")
        state.add_song_to_playlist("song_2")
        state.add_song_to_playlist("song_3")

        result = state.move_playlist_song(0, 2)

        assert result is True
        assert state.playlist_items[0] == "song_2"
        assert state.playlist_items[1] == "song_1"
        assert state.playlist_items[2] == "song_3"

    def test_move_playlist_song_backward(self):
        """Test moving song backward in playlist."""
        state = AppState()

        state.add_song_to_playlist("song_1")
        state.add_song_to_playlist("song_2")
        state.add_song_to_playlist("song_3")

        result = state.move_playlist_song(2, 0)

        assert result is True
        assert state.playlist_items[0] == "song_3"
        assert state.playlist_items[1] == "song_1"
        assert state.playlist_items[2] == "song_2"

    def test_move_playlist_song_invalid_indices(self):
        """Test moving with invalid indices."""
        state = AppState()

        state.add_song_to_playlist("song_1")
        state.add_song_to_playlist("song_2")

        result = state.move_playlist_song(0, 10)

        assert result is False

    def test_move_playlist_song_same_position(self):
        """Test moving song to same position."""
        state = AppState()

        state.add_song_to_playlist("song_1")
        original_items = state.playlist_items.copy()

        result = state.move_playlist_song(0, 0)

        assert result is True
        assert state.playlist_items == original_items

    def test_clear_playlist(self):
        """Test clearing playlist."""
        state = AppState()

        state.add_song_to_playlist("song_1")
        state.add_song_to_playlist("song_2")
        state.playlist_name = "Test Playlist"

        state.clear_playlist()

        assert len(state.playlist_items) == 0
        assert state.playlist_name == "Untitled Playlist"

    def test_get_playlist_song_id_found(self):
        """Test getting song ID from playlist."""
        state = AppState()

        state.add_song_to_playlist("song_1")
        state.add_song_to_playlist("song_2")

        result = state.get_playlist_song_id(1)

        assert result == "song_2"

    def test_get_playlist_song_id_not_found(self):
        """Test getting song ID with invalid index."""
        state = AppState()

        state.add_song_to_playlist("song_1")

        result = state.get_playlist_song_id(10)

        assert result is None

    def test_reset_parameters(self):
        """Test resetting parameters to defaults."""
        state = AppState()

        # Modify some parameters
        state.transition_type = "crossfade"
        state.overlap = 4.0
        state.fade_window = 16.0
        state.fade_bottom = 0.5
        state.from_section_start_adjust = 2
        state.to_section_end_adjust = -1

        state.reset_parameters()

        # Should be back to defaults
        assert state.transition_type == "gap"
        assert state.overlap == 1.0
        assert state.fade_window == 8.0
        assert state.fade_speed == 2.0
        assert state.fade_bottom == 0.33
        assert state.stems_to_fade == ["bass", "drums", "other"]
        assert state.extension_parameters == {}
        assert state.from_section_start_adjust == 0
        assert state.from_section_end_adjust == 0
        assert state.to_section_start_adjust == 0
        assert state.to_section_end_adjust == 0
        assert state.active_validation_warnings == []

    def test_exit_modify_mode(self):
        """Test exiting modify mode."""
        state = AppState()

        # Set up modify mode
        state.generation_mode = GenerationMode.MODIFY
        state.base_transition_id = 123
        state.left_song_id = "song_a"
        state.right_song_id = "song_b"
        state.left_section_index = 0
        state.right_section_index = 1

        state.exit_modify_mode()

        # Should be in fresh mode
        assert state.generation_mode == GenerationMode.FRESH
        assert state.base_transition_id is None
        assert state.left_song_id is None
        assert state.right_song_id is None
        assert state.left_section_index is None
        assert state.right_section_index is None

    def test_enter_modify_mode(self):
        """Test entering modify mode with transition."""
        state = AppState()

        # Create a transition record
        transition = TransitionRecord(
            id=1,
            transition_type="crossfade",
            song_a_filename="song_a.mp3",
            song_b_filename="song_b.mp3",
            section_a_label="Chorus",
            section_b_label="Verse",
            compatibility_score=85.0,
            generated_at=datetime.now(),
            audio_path=Path("/output/test.flac"),
            parameters={
                "type": "crossfade",
                "overlap": 4.0,
                "fade_window": 12.0,
                "fade_bottom": 0.25,
                "stems_to_fade": ["bass", "drums"],
            },
        )

        state.enter_modify_mode(transition)

        assert state.generation_mode == GenerationMode.MODIFY
        assert state.base_transition_id == 1
        assert state.left_song_id == "song_a.mp3"
        assert state.right_song_id == "song_b.mp3"
        assert state.transition_type == "crossfade"
        assert state.overlap == 4.0
        assert state.fade_window == 12.0
        assert state.fade_bottom == 0.25

    def test_add_transition_enforces_cap(self):
        """Test that add_transition enforces 50-item cap."""
        state = AppState()

        # Add 50 transitions
        for i in range(50):
            state.add_transition(TransitionRecord(
                id=i,
                transition_type="gap",
                song_a_filename=f"song_a_{i}",
                song_b_filename=f"song_b_{i}",
                section_a_label="Chorus",
                section_b_label="Verse",
                compatibility_score=85.0,
                generated_at=datetime.now(),
                audio_path=Path(f"/output/{i}.flac"),
            ))

        assert len(state.transition_history) == 50

        # Add one more - oldest should be removed
        state.add_transition(TransitionRecord(
            id=50,
            transition_type="gap",
            song_a_filename="song_a_50",
            song_b_filename="song_b_50",
            section_a_label="Chorus",
            section_b_label="Verse",
            compatibility_score=85.0,
            generated_at=datetime.now(),
            audio_path=Path("/output/50.flac"),
        ))

        assert len(state.transition_history) == 50
        # The first one (id=0) should have been removed
        assert state.transition_history[0].id == 50
        assert state.transition_history[-1].id == 1  # Next oldest after id=0 was removed

    def test_add_transition_newest_first(self):
        """Test that transitions are added newest first."""
        state = AppState()

        state.add_transition(TransitionRecord(
            id=1,
            transition_type="gap",
            song_a_filename="song_a",
            song_b_filename="song_b",
            section_a_label="Chorus",
            section_b_label="Verse",
            compatibility_score=85.0,
            generated_at=datetime.now(),
            audio_path=Path("/output/1.flac"),
        ))

        state.add_transition(TransitionRecord(
            id=2,
            transition_type="crossfade",
            song_a_filename="song_a",
            song_b_filename="song_b",
            section_a_label="Chorus",
            section_b_label="Verse",
            compatibility_score=85.0,
            generated_at=datetime.now(),
            audio_path=Path("/output/2.flac"),
        ))

        # Newest should be first
        assert state.transition_history[0].id == 2
        assert state.transition_history[1].id == 1

    def test_get_selected_transition_found(self):
        """Test getting selected transition."""
        state = AppState()

        state.add_transition(TransitionRecord(
            id=1,
            transition_type="gap",
            song_a_filename="song_a",
            song_b_filename="song_b",
            section_a_label="Chorus",
            section_b_label="Verse",
            compatibility_score=85.0,
            generated_at=datetime.now(),
            audio_path=Path("/output/1.flac"),
        ))

        state.selected_history_index = 0
        result = state.get_selected_transition()

        assert result is not None
        assert result.id == 1

    def test_get_selected_transition_not_found(self):
        """Test getting selected transition when none selected."""
        state = AppState()

        state.selected_history_index = None
        result = state.get_selected_transition()

        assert result is None

    def test_get_selected_transition_invalid_index(self):
        """Test getting selected transition with invalid index."""
        state = AppState()

        state.add_transition(TransitionRecord(
            id=1,
            transition_type="gap",
            song_a_filename="song_a",
            song_b_filename="song_b",
            section_a_label="Chorus",
            section_b_label="Verse",
            compatibility_score=85.0,
            generated_at=datetime.now(),
            audio_path=Path("/output/1.flac"),
        ))

        state.selected_history_index = 10
        result = state.get_selected_transition()

        assert result is None
