#!/usr/bin/env python3
"""Test actual transition generation with OGG output."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from app.models.song import Song, Section
from app.services.generation import TransitionGenerationService
import tempfile
import numpy as np
import soundfile as sf

def create_test_song_file(path: Path, duration: float = 10.0, sr: int = 44100):
    """Create a test audio file."""
    samples = int(sr * duration)
    audio_data = np.random.randn(samples, 2).astype(np.float32) * 0.1
    sf.write(str(path), audio_data, sr, format='OGG', subtype='VORBIS')

def test_transition_generation():
    """Test generating a transition with OGG output."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)

        # Create test directories
        audio_dir = tmpdir / "audio"
        output_dir = tmpdir / "output_transitions"
        output_songs_dir = tmpdir / "output_songs"

        audio_dir.mkdir()
        output_dir.mkdir()
        output_songs_dir.mkdir()

        # Create test audio files
        print("Creating test audio files...")
        song_a_path = audio_dir / "song_a.ogg"
        song_b_path = audio_dir / "song_b.ogg"

        create_test_song_file(song_a_path, duration=15.0)
        create_test_song_file(song_b_path, duration=15.0)
        print(f"✓ Created {song_a_path.name} ({song_a_path.stat().st_size} bytes)")
        print(f"✓ Created {song_b_path.name} ({song_b_path.stat().st_size} bytes)")

        # Create Song objects
        song_a = Song(
            filename="song_a.ogg",
            filepath=song_a_path,
            duration=15.0,
            tempo=120.0,
            key="C",
            mode="major",
            key_confidence=0.9,
            full_key="C major",
            loudness_db=-14.0,
            spectral_centroid=2000.0,
            sections=[
                Section(label="intro", start=0.0, end=5.0, duration=5.0),
                Section(label="verse", start=5.0, end=10.0, duration=5.0),
                Section(label="chorus", start=10.0, end=15.0, duration=5.0),
            ]
        )

        song_b = Song(
            filename="song_b.ogg",
            filepath=song_b_path,
            duration=15.0,
            tempo=120.0,
            key="G",
            mode="major",
            key_confidence=0.85,
            full_key="G major",
            loudness_db=-12.0,
            spectral_centroid=2500.0,
            sections=[
                Section(label="intro", start=0.0, end=4.0, duration=4.0),
                Section(label="verse", start=4.0, end=9.0, duration=5.0),
                Section(label="chorus", start=9.0, end=15.0, duration=6.0),
            ]
        )

        # Create generation service
        print("\nInitializing generation service...")
        service = TransitionGenerationService(
            output_dir=output_dir,
            output_songs_dir=output_songs_dir,
            stems_folder=None
        )

        # Test 1: Generate gap transition
        print("\nTest 1: Generating gap transition...")
        output_path, metadata = service.generate_gap_transition(
            song_a=song_a,
            song_b=song_b,
            section_a_index=1,  # verse
            section_b_index=1,  # verse
            gap_beats=1.0,
            fade_window_beats=8.0,
            fade_bottom=0.33,
            stems_to_fade=["bass", "drums", "other"]
        )

        if output_path.exists():
            print(f"✓ Transition generated: {output_path.name}")
            print(f"  Size: {output_path.stat().st_size} bytes")
            print(f"  Duration: {metadata['total_duration_seconds']:.2f}s")
            print(f"  Format: {output_path.suffix}")
            assert output_path.suffix == ".ogg", "Expected OGG format"
        else:
            print("✗ Transition generation failed!")
            return False

        # Test 2: Generate full song output
        print("\nTest 2: Generating full song output...")
        full_song_path, full_metadata = service.generate_full_song_output(
            song_a=song_a,
            song_b=song_b,
            section_a_index=1,
            section_b_index=1,
            transition_audio_path=output_path,
            sr=44100
        )

        if full_song_path.exists():
            print(f"✓ Full song generated: {full_song_path.name}")
            print(f"  Size: {full_song_path.stat().st_size} bytes")
            print(f"  Duration: {full_metadata['total_duration']:.2f}s")
            print(f"  Format: {full_song_path.suffix}")
            assert full_song_path.suffix == ".ogg", "Expected OGG format"
        else:
            print("✗ Full song generation failed!")
            return False

        # Test 3: Verify files can be read back
        print("\nTest 3: Verifying files can be read back...")
        transition_audio, sr1 = sf.read(str(output_path))
        print(f"✓ Transition readable: {transition_audio.shape}, sr={sr1}")

        full_song_audio, sr2 = sf.read(str(full_song_path))
        print(f"✓ Full song readable: {full_song_audio.shape}, sr={sr2}")

        print("\n" + "="*50)
        print("All transition generation tests passed! ✓")
        print("="*50)
        return True

if __name__ == "__main__":
    try:
        success = test_transition_generation()
        exit(0 if success else 1)
    except Exception as e:
        print(f"\n✗ ERROR: {e}")
        import traceback
        traceback.print_exc()
        exit(1)
