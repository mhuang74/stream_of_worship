#!/usr/bin/env python3
"""Standalone test runner for the complete workflow test.

This script can be run directly without pytest to test the full workflow:
1. Pick song A
2. Pick song A section
3. Pick song B
4. Pick song B section
5. Hit 't' to preview
6. Hit 'T' to generate
7. Hit 'o' to output song set

Usage:
    python tests/run_workflow_test.py
"""
import asyncio
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.main import TransitionBuilderApp


async def test_workflow():
    """Run the complete workflow test."""
    print("=" * 70)
    print("Testing Complete Workflow: Select → Preview → Generate → Output")
    print("=" * 70)

    # Load config
    config_path = Path(__file__).parent.parent / "config.json"
    app = TransitionBuilderApp(config_path)

    async with app.run_test() as pilot:
        # Get available songs
        songs = app.catalog.get_all_songs()
        print(f"\n✓ Loaded {len(songs)} songs from catalog")

        if len(songs) < 2:
            print("✗ ERROR: Need at least 2 songs for testing")
            return False

        song_a = songs[0]
        song_b = songs[1]

        print(f"  - Song A: {song_a.filename} ({len(song_a.sections)} sections)")
        print(f"  - Song B: {song_b.filename} ({len(song_b.sections)} sections)")

        # Verify songs have sections
        if len(song_a.sections) == 0:
            print(f"✗ ERROR: Song A ({song_a.filename}) has no sections")
            return False
        if len(song_b.sections) == 0:
            print(f"✗ ERROR: Song B ({song_b.filename}) has no sections")
            return False

        # Step 1 & 2: Select Song A and section
        print("\n[Step 1-2] Selecting Song A and section...")
        app.state.left_song_id = song_a.id
        app.state.left_section_index = 0
        await pilot.pause()
        print(f"  ✓ Selected: {song_a.filename} - {song_a.sections[0].label}")

        # Step 3 & 4: Select Song B and section
        print("\n[Step 3-4] Selecting Song B and section...")
        app.state.right_song_id = song_b.id
        app.state.right_section_index = 0
        await pilot.pause()
        print(f"  ✓ Selected: {song_b.filename} - {song_b.sections[0].label}")

        # Step 5: Preview (t key)
        print("\n[Step 5] Generating focused preview (t key)...")
        try:
            await pilot.press("t")
            await pilot.pause()
            await asyncio.sleep(2)
            print("  ✓ Preview generated successfully")
        except Exception as e:
            print(f"  ✗ Preview generation failed: {e}")
            return False

        # Step 6: Generate full transition (Shift-T)
        print("\n[Step 6] Generating full transition (Shift-T key)...")
        initial_history_count = len(app.state.transition_history)
        try:
            await pilot.press("shift+t")
            await pilot.pause()
            await asyncio.sleep(2)

            if len(app.state.transition_history) == initial_history_count + 1:
                transition = app.state.transition_history[0]
                transition_path = Path(transition.audio_path) if isinstance(transition.audio_path, str) else transition.audio_path

                if transition_path.exists():
                    print(f"  ✓ Transition generated: {transition_path.name}")
                    print(f"    - Type: {transition.transition_type}")
                    print(f"    - Gap: {transition.parameters.get('gap_beats', 'N/A')} beats")
                    print(f"    - File size: {transition_path.stat().st_size / 1024 / 1024:.2f} MB")
                else:
                    print(f"  ✗ Transition file not found: {transition_path}")
                    return False
            else:
                print(f"  ✗ Transition not added to history (count: {len(app.state.transition_history)})")
                return False
        except Exception as e:
            print(f"  ✗ Transition generation failed: {e}")
            import traceback
            traceback.print_exc()
            return False

        # Verify last_generated_transition_path is set
        if not app.state.last_generated_transition_path:
            print("  ✗ last_generated_transition_path not set")
            return False

        # Step 7: Generate full song output (o key)
        print("\n[Step 7] Generating full song output (o key)...")
        try:
            await pilot.press("o")
            await pilot.pause()
            await asyncio.sleep(3)

            if len(app.state.transition_history) == initial_history_count + 2:
                full_song = app.state.transition_history[0]  # Newest first
                full_song_path = Path(full_song.audio_path) if isinstance(full_song.audio_path, str) else full_song.audio_path

                if full_song_path.exists():
                    print(f"  ✓ Full song output generated: {full_song_path.name}")
                    print(f"    - Output type: {full_song.output_type}")
                    print(f"    - Song A prefix sections: {full_song.parameters.get('num_song_a_sections_before', 0)}")
                    print(f"    - Song B suffix sections: {full_song.parameters.get('num_song_b_sections_after', 0)}")
                    print(f"    - Total duration: {full_song.parameters.get('total_duration', 0):.1f}s")
                    print(f"    - File size: {full_song_path.stat().st_size / 1024 / 1024:.2f} MB")
                    print(f"    - Location: {full_song_path.parent}")
                else:
                    print(f"  ✗ Full song file not found: {full_song_path}")
                    return False
            else:
                print(f"  ✗ Full song not added to history (count: {len(app.state.transition_history)})")
                return False
        except Exception as e:
            print(f"  ✗ Full song generation failed: {e}")
            import traceback
            traceback.print_exc()
            return False

        # Final verification
        print("\n" + "=" * 70)
        print("FINAL VERIFICATION")
        print("=" * 70)

        transition_path = Path(app.state.transition_history[1].audio_path)
        full_song_path = Path(app.state.transition_history[0].audio_path)

        checks = [
            ("Transition file exists", transition_path.exists()),
            ("Transition is FLAC", transition_path.suffix == ".flac"),
            ("Full song file exists", full_song_path.exists()),
            ("Full song is FLAC", full_song_path.suffix == ".flac"),
            ("Full song in output_songs dir", "output_songs" in str(full_song_path)),
            ("History has 2 items", len(app.state.transition_history) == 2),
        ]

        all_passed = True
        for check_name, result in checks:
            status = "✓" if result else "✗"
            print(f"  {status} {check_name}")
            if not result:
                all_passed = False

        return all_passed


def main():
    """Main entry point."""
    print("\nStarting workflow test...")

    try:
        result = asyncio.run(test_workflow())

        print("\n" + "=" * 70)
        if result:
            print("✓ ALL TESTS PASSED")
            print("=" * 70)
            return 0
        else:
            print("✗ TESTS FAILED")
            print("=" * 70)
            return 1
    except KeyboardInterrupt:
        print("\n\nTest interrupted by user")
        return 1
    except Exception as e:
        print(f"\n✗ UNEXPECTED ERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
