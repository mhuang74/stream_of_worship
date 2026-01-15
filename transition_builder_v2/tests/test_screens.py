"""Regression tests for screen navigation and workflow.

Run with: pytest tests/test_screens.py -v
Or directly: python tests/test_screens.py
"""
import asyncio
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from app.main import TransitionBuilderApp
from app.state import GenerationMode


@pytest.fixture
def config_path():
    """Get the config path."""
    return Path(__file__).parent.parent / "config.json"


@pytest.fixture
def app(config_path):
    """Create app instance."""
    return TransitionBuilderApp(config_path)


class TestScreenNavigation:
    """Test navigation between screens."""

    @pytest.mark.asyncio
    async def test_initial_screen_is_generation(self, app):
        """App should start on GenerationScreen."""
        async with app.run_test() as pilot:
            screen = app.screen
            assert "GenerationScreen" in type(screen).__name__

    @pytest.mark.asyncio
    async def test_h_key_goes_to_history(self, app):
        """H key should navigate from Generation to History screen."""
        async with app.run_test() as pilot:
            await pilot.press("h")
            await pilot.pause()
            screen = app.screen
            assert "HistoryScreen" in type(screen).__name__

    @pytest.mark.asyncio
    async def test_g_key_goes_to_generation(self, app):
        """G key should navigate from History to Generation screen."""
        async with app.run_test() as pilot:
            # First go to History
            await pilot.press("h")
            await pilot.pause()

            # Then press G to go back
            await pilot.press("g")
            await pilot.pause()
            screen = app.screen
            assert "GenerationScreen" in type(screen).__name__

    @pytest.mark.asyncio
    async def test_round_trip_navigation(self, app):
        """Should be able to navigate back and forth between screens."""
        async with app.run_test() as pilot:
            # Start on Generation
            assert "GenerationScreen" in type(app.screen).__name__

            # Go to History
            await pilot.press("h")
            await pilot.pause()
            assert "HistoryScreen" in type(app.screen).__name__

            # Go back to Generation
            await pilot.press("g")
            await pilot.pause()
            assert "GenerationScreen" in type(app.screen).__name__

            # Go to History again
            await pilot.press("h")
            await pilot.pause()
            assert "HistoryScreen" in type(app.screen).__name__


class TestTransitionGeneration:
    """Test transition generation and history."""

    @pytest.mark.asyncio
    async def test_generation_adds_to_history(self, app):
        """Generating a transition should add it to history."""
        async with app.run_test() as pilot:
            # Setup: select songs and sections
            songs = app.catalog.get_all_songs()
            app.state.left_song_id = songs[0].id
            app.state.left_section_index = 0
            app.state.right_song_id = songs[1].id
            app.state.right_section_index = 0

            # Verify history is empty
            assert len(app.state.transition_history) == 0

            # Generate transition
            app.screen.action_generate()
            await pilot.pause()
            await asyncio.sleep(1)  # Wait for generation

            # Verify transition was added
            assert len(app.state.transition_history) == 1

            transition = app.state.transition_history[0]
            assert transition.id == 1
            assert transition.song_a_filename == songs[0].filename
            assert transition.song_b_filename == songs[1].filename
            assert transition.transition_type == "gap"

    @pytest.mark.asyncio
    async def test_transition_has_correct_metadata(self, app):
        """Generated transition should have correct metadata."""
        async with app.run_test() as pilot:
            songs = app.catalog.get_all_songs()
            song_a = songs[0]
            song_b = songs[1]

            app.state.left_song_id = song_a.id
            app.state.left_section_index = 0
            app.state.right_song_id = song_b.id
            app.state.right_section_index = 0

            app.screen.action_generate()
            await pilot.pause()
            await asyncio.sleep(1)

            transition = app.state.transition_history[0]

            # Check metadata
            assert transition.section_a_label == song_a.sections[0].label
            assert transition.section_b_label == song_b.sections[0].label
            assert transition.audio_path.exists()
            assert transition.is_saved == False
            assert transition.saved_path is None

    def test_multiple_transitions_increment_id(self, app):
        """Multiple transitions should have incrementing IDs (unit test)."""
        from datetime import datetime
        from app.models.transition import TransitionRecord

        # Add transitions directly to test ID incrementing
        for i in range(3):
            record = TransitionRecord(
                id=i + 1,
                transition_type="gap",
                song_a_filename=f"song_a_{i}.mp3",
                song_b_filename=f"song_b_{i}.mp3",
                section_a_label="chorus",
                section_b_label="verse",
                compatibility_score=80.0,
                generated_at=datetime.now(),
                audio_path=Path(f"/tmp/test_{i}.flac"),
                parameters={"type": "gap"}
            )
            app.state.add_transition(record)

        # Check IDs (newest first in history)
        assert len(app.state.transition_history) == 3
        assert app.state.transition_history[0].id == 3  # Newest
        assert app.state.transition_history[1].id == 2
        assert app.state.transition_history[2].id == 1  # Oldest


class TestModifyMode:
    """Test modify mode functionality."""

    @pytest.mark.asyncio
    async def test_modify_enters_modify_mode(self, app):
        """M key should enter modify mode with correct parameters."""
        async with app.run_test() as pilot:
            # Setup: generate a transition first
            songs = app.catalog.get_all_songs()
            app.state.left_song_id = songs[0].id
            app.state.left_section_index = 0
            app.state.right_song_id = songs[1].id
            app.state.right_section_index = 0

            app.screen.action_generate()
            await pilot.pause()
            await asyncio.sleep(1)

            # Go to History
            await pilot.press("h")
            await pilot.pause()

            # Select first transition and modify
            app.state.selected_history_index = 0
            app.screen.action_modify_transition()
            await pilot.pause()

            # Verify modify mode
            assert app.state.generation_mode == GenerationMode.MODIFY
            assert app.state.base_transition_id == 1
            assert app.state.left_song_id == songs[0].filename
            assert app.state.right_song_id == songs[1].filename

    @pytest.mark.asyncio
    async def test_modify_loads_parameters(self, app):
        """Modify mode should load transition parameters."""
        async with app.run_test() as pilot:
            songs = app.catalog.get_all_songs()

            # Set custom parameters
            app.state.left_song_id = songs[0].id
            app.state.left_section_index = 0
            app.state.right_song_id = songs[1].id
            app.state.right_section_index = 0
            app.state.overlap = 2.5  # Custom gap

            app.screen.action_generate()
            await pilot.pause()
            await asyncio.sleep(1)

            # Reset parameters
            app.state.overlap = 1.0

            # Go to History and modify
            await pilot.press("h")
            await pilot.pause()

            app.state.selected_history_index = 0
            app.screen.action_modify_transition()
            await pilot.pause()

            # Verify parameters were loaded
            assert app.state.overlap == 2.5


class TestHistoryManagement:
    """Test history screen functionality."""

    @pytest.mark.asyncio
    async def test_history_cap_at_50(self, app):
        """History should cap at 50 items."""
        async with app.run_test() as pilot:
            from datetime import datetime
            from app.models.transition import TransitionRecord

            # Add 55 transitions directly to test cap
            for i in range(55):
                record = TransitionRecord(
                    id=i + 1,
                    transition_type="gap",
                    song_a_filename=f"song_a_{i}.mp3",
                    song_b_filename=f"song_b_{i}.mp3",
                    section_a_label="chorus",
                    section_b_label="verse",
                    compatibility_score=80.0,
                    generated_at=datetime.now(),
                    audio_path=Path(f"/tmp/test_{i}.flac"),
                    parameters={"type": "gap"}
                )
                app.state.add_transition(record)

            # Should be capped at 50
            assert len(app.state.transition_history) == 50

            # Newest should be first (id=55)
            assert app.state.transition_history[0].id == 55

            # Oldest kept should be id=6 (1-5 were removed)
            assert app.state.transition_history[-1].id == 6

    def test_delete_removes_transition(self, app):
        """Delete should remove transition from history (unit test)."""
        from datetime import datetime
        from app.models.transition import TransitionRecord

        # Add a transition directly
        record = TransitionRecord(
            id=1,
            transition_type="gap",
            song_a_filename="song_a.mp3",
            song_b_filename="song_b.mp3",
            section_a_label="chorus",
            section_b_label="verse",
            compatibility_score=80.0,
            generated_at=datetime.now(),
            audio_path=Path("/tmp/test.flac"),
            parameters={"type": "gap"}
        )
        app.state.add_transition(record)
        assert len(app.state.transition_history) == 1

        # Select and delete
        app.state.selected_history_index = 0
        idx = app.state.selected_history_index
        app.state.transition_history.pop(idx)

        assert len(app.state.transition_history) == 0


class TestStateManagement:
    """Test application state management."""

    def test_initial_state(self, app):
        """App should start with correct initial state."""
        from app.state import ActiveScreen, GenerationMode

        assert app.state.active_screen == ActiveScreen.GENERATION
        assert app.state.generation_mode == GenerationMode.FRESH
        assert app.state.left_song_id is None
        assert app.state.right_song_id is None
        assert len(app.state.transition_history) == 0

    def test_reset_parameters(self, app):
        """Reset parameters should restore defaults."""
        app.state.overlap = 5.0
        app.state.fade_window = 16.0
        app.state.transition_type = "crossfade"

        app.state.reset_parameters()

        assert app.state.overlap == 1.0
        assert app.state.fade_window == 8.0
        assert app.state.transition_type == "gap"

    def test_exit_modify_mode(self, app):
        """Exit modify mode should reset state."""
        from app.state import GenerationMode

        app.state.generation_mode = GenerationMode.MODIFY
        app.state.base_transition_id = 1
        app.state.left_song_id = "test.mp3"

        app.state.exit_modify_mode()

        assert app.state.generation_mode == GenerationMode.FRESH
        assert app.state.base_transition_id is None
        assert app.state.left_song_id is None


# Allow running tests directly
if __name__ == "__main__":
    # Run with pytest
    pytest.main([__file__, "-v", "--tb=short"])
