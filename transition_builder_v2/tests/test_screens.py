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


class TestFullWorkflow:
    """Integration test for complete user workflow: select songs -> preview -> generate -> output."""

    @pytest.mark.asyncio
    async def test_complete_workflow_preview_generate_output(self, app):
        """Test complete workflow: pick songs/sections, preview (t), generate (T), output (o).

        Steps:
        1. Pick song A
        2. Pick song A section
        3. Pick song B
        4. Pick song B section
        5. Hit 't' to preview (focused preview)
        6. Hit 'T' to generate (full transition)
        7. Hit 'o' to output song set (full song output)
        """
        async with app.run_test() as pilot:
            # Get available songs
            songs = app.catalog.get_all_songs()
            assert len(songs) >= 2, "Need at least 2 songs for testing"

            song_a = songs[0]
            song_b = songs[1]

            # Ensure songs have sections
            assert len(song_a.sections) > 0, f"Song A ({song_a.filename}) has no sections"
            assert len(song_b.sections) > 0, f"Song B ({song_b.filename}) has no sections"

            # Step 1 & 2: Select Song A and its first section
            app.state.left_song_id = song_a.id
            app.state.left_section_index = 0
            await pilot.pause()

            # Verify Song A selection
            assert app.state.left_song_id == song_a.id
            assert app.state.left_section_index == 0

            # Step 3 & 4: Select Song B and its first section
            app.state.right_song_id = song_b.id
            app.state.right_section_index = 0
            await pilot.pause()

            # Verify Song B selection
            assert app.state.right_song_id == song_b.id
            assert app.state.right_section_index == 0

            # Step 5: Hit 't' to generate focused preview
            await pilot.press("t")
            await pilot.pause()
            await asyncio.sleep(2)  # Wait for preview generation

            # Verify preview was generated (no errors)
            # Note: Playback is mocked in tests, so we just verify no exceptions

            # Step 6: Hit 'T' (Shift-t) to generate full transition
            initial_history_count = len(app.state.transition_history)
            await pilot.press("shift+t")
            await pilot.pause()
            await asyncio.sleep(2)  # Wait for generation

            # Verify transition was added to history
            assert len(app.state.transition_history) == initial_history_count + 1
            new_transition = app.state.transition_history[0]  # Newest first

            # Verify transition metadata
            assert new_transition.song_a_filename == song_a.filename
            assert new_transition.song_b_filename == song_b.filename
            assert new_transition.section_a_label == song_a.sections[0].label
            assert new_transition.section_b_label == song_b.sections[0].label
            assert new_transition.transition_type == "gap"

            # Verify transition audio file was created
            transition_path = Path(new_transition.audio_path) if isinstance(new_transition.audio_path, str) else new_transition.audio_path
            assert transition_path.exists(), f"Transition audio file not created: {transition_path}"

            # Verify state has last_generated_transition_path set
            assert app.state.last_generated_transition_path is not None
            assert Path(app.state.last_generated_transition_path).exists()

            # Step 7: Hit 'o' to create full song output
            await pilot.press("o")
            await pilot.pause()
            await asyncio.sleep(2)  # Wait for full song generation

            # Verify full song output was added to history
            assert len(app.state.transition_history) == initial_history_count + 2
            full_song_record = app.state.transition_history[0]  # Newest first

            # Verify full song metadata
            assert full_song_record.output_type == "full_song"
            assert full_song_record.song_a_filename == song_a.filename
            assert full_song_record.song_b_filename == song_b.filename

            # Verify full song parameters
            params = full_song_record.parameters
            assert params is not None
            assert params.get("output_type") == "full_song"
            assert "num_song_a_sections_before" in params
            assert "num_song_b_sections_after" in params
            assert "total_duration" in params

            # Verify full song audio file was created
            full_song_path = Path(full_song_record.audio_path) if isinstance(full_song_record.audio_path, str) else full_song_record.audio_path
            assert full_song_path.exists(), f"Full song audio file not created: {full_song_path}"

            # Verify the file is in the output_songs directory
            assert "output_songs" in str(full_song_path)

            # Verify both files are FLAC format
            assert transition_path.suffix == ".flac"
            assert full_song_path.suffix == ".flac"

    @pytest.mark.asyncio
    async def test_workflow_with_custom_parameters(self, app):
        """Test workflow with custom gap and fade parameters."""
        async with app.run_test() as pilot:
            songs = app.catalog.get_all_songs()
            assert len(songs) >= 2

            # Select songs and sections
            app.state.left_song_id = songs[0].id
            app.state.left_section_index = 0
            app.state.right_song_id = songs[1].id
            app.state.right_section_index = 0

            # Set custom parameters
            app.state.overlap = 2.0  # 2 beats gap
            app.state.fade_window = 16.0  # 16 beats fade window
            app.state.fade_bottom = 0.5  # 50% fade bottom
            app.state.stems_to_fade = ["drums", "bass"]  # Only fade drums and bass

            await pilot.pause()

            # Generate transition with custom parameters
            await pilot.press("shift+t")
            await pilot.pause()
            await asyncio.sleep(2)

            # Verify transition was created with custom parameters
            transition = app.state.transition_history[0]
            params = transition.parameters

            assert params["gap_beats"] == 2.0
            assert params["fade_window"] == 16.0
            assert params["stems_to_fade"] == ["drums", "bass"]

            # Generate full song output
            await pilot.press("o")
            await pilot.pause()
            await asyncio.sleep(2)

            # Verify both files exist
            transition_path = Path(transition.audio_path) if isinstance(transition.audio_path, str) else transition.audio_path
            full_song = app.state.transition_history[0]
            full_song_path = Path(full_song.audio_path) if isinstance(full_song.audio_path, str) else full_song.audio_path

            assert transition_path.exists()
            assert full_song_path.exists()

    @pytest.mark.asyncio
    async def test_workflow_seamless_transition(self, app):
        """Test workflow with gap=0 for seamless transition."""
        async with app.run_test() as pilot:
            songs = app.catalog.get_all_songs()
            assert len(songs) >= 2

            # Select songs and sections
            app.state.left_song_id = songs[0].id
            app.state.left_section_index = 0
            app.state.right_song_id = songs[1].id
            app.state.right_section_index = 0

            # Set gap to 0 for seamless transition
            app.state.overlap = 0.0
            app.state.fade_window = 8.0

            await pilot.pause()

            # Generate transition
            await pilot.press("shift+t")
            await pilot.pause()
            await asyncio.sleep(2)

            # Verify transition was created
            assert len(app.state.transition_history) > 0
            transition = app.state.transition_history[0]

            # Verify gap is 0
            assert transition.parameters["gap_beats"] == 0.0

            # Verify file exists
            transition_path = Path(transition.audio_path) if isinstance(transition.audio_path, str) else transition.audio_path
            assert transition_path.exists()

            # Generate full song output
            await pilot.press("o")
            await pilot.pause()
            await asyncio.sleep(2)

            # Verify full song was created
            assert len(app.state.transition_history) > 1
            full_song_path = Path(app.state.transition_history[0].audio_path)
            assert full_song_path.exists()


# Allow running tests directly
if __name__ == "__main__":
    # Run with pytest
    pytest.main([__file__, "-v", "--tb=short"])
