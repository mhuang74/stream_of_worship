#!/usr/bin/env python3
"""Simple test to verify OGG writing works without segfault."""
import numpy as np
import soundfile as sf
from pathlib import Path
import tempfile

def test_ogg_write():
    """Test writing OGG files with different parameters."""

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)

        # Test 1: Basic stereo OGG write
        print("Test 1: Basic stereo OGG write...")
        sr = 44100
        duration = 5.0
        samples = int(sr * duration)
        audio_data = np.random.randn(samples, 2).astype(np.float32) * 0.1

        output_path = tmpdir / "test_basic.ogg"
        sf.write(str(output_path), audio_data, sr, format='OGG', subtype='VORBIS')

        if output_path.exists():
            print(f"✓ Basic OGG write successful: {output_path.stat().st_size} bytes")
        else:
            print("✗ Basic OGG write failed!")
            return False

        # Test 2: Read back the OGG file
        print("\nTest 2: Reading back OGG file...")
        audio_read, sr_read = sf.read(str(output_path))
        print(f"✓ Read back successful: {audio_read.shape}, sr={sr_read}")

        # Test 3: Write mono OGG (converted to stereo)
        print("\nTest 3: Mono to stereo OGG...")
        mono_audio = np.random.randn(samples).astype(np.float32) * 0.1
        stereo_audio = np.stack([mono_audio, mono_audio], axis=-1)

        output_path_mono = tmpdir / "test_mono_to_stereo.ogg"
        sf.write(str(output_path_mono), stereo_audio, sr, format='OGG', subtype='VORBIS')

        if output_path_mono.exists():
            print(f"✓ Mono-to-stereo OGG write successful: {output_path_mono.stat().st_size} bytes")
        else:
            print("✗ Mono-to-stereo OGG write failed!")
            return False

        # Test 4: Large audio file (simulating full song)
        print("\nTest 4: Large audio file (30 seconds)...")
        large_duration = 30.0
        large_samples = int(sr * large_duration)
        large_audio = np.random.randn(large_samples, 2).astype(np.float32) * 0.1

        output_path_large = tmpdir / "test_large.ogg"
        sf.write(str(output_path_large), large_audio, sr, format='OGG', subtype='VORBIS')

        if output_path_large.exists():
            print(f"✓ Large OGG write successful: {output_path_large.stat().st_size} bytes")
            # Check compression ratio
            flac_size_estimate = large_samples * 2 * 2  # 2 channels * 2 bytes (16-bit)
            compression_ratio = output_path_large.stat().st_size / flac_size_estimate
            print(f"  Compression ratio vs 16-bit PCM: {compression_ratio:.2%}")
        else:
            print("✗ Large OGG write failed!")
            return False

        print("\n" + "="*50)
        print("All OGG tests passed! ✓")
        print("="*50)
        return True

if __name__ == "__main__":
    try:
        success = test_ogg_write()
        exit(0 if success else 1)
    except Exception as e:
        print(f"\n✗ ERROR: {e}")
        import traceback
        traceback.print_exc()
        exit(1)
