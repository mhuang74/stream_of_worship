#!/usr/bin/env python3
"""
Test Script: EnCodec Interpolation Module

Validates the EnCodec interpolation implementation following the pattern
of test_allinone_analyze.py with manual checkpoint validation.
"""

import sys
from pathlib import Path

# Add poc directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

print("=" * 70)
print("EnCodec Interpolation Module Tests")
print("=" * 70)

# Test 1: Import transformers and check EnCodec availability
print("\n[Test 1] Checking transformers library...")
try:
    import transformers
    print(f"  ✓ transformers version: {transformers.__version__}")
except ImportError as e:
    print(f"  ✗ transformers not installed: {e}")
    print(f"  Install with: pip install transformers>=4.35.0")
    sys.exit(1)

try:
    from transformers import EncodecModel, AutoProcessor
    print(f"  ✓ EncodecModel and AutoProcessor imported successfully")
except ImportError as e:
    print(f"  ✗ EnCodec not available in transformers: {e}")
    sys.exit(1)

# Test 2: Import custom module
print("\n[Test 2] Importing poc.encodec_interpolation module...")
try:
    from poc.encodec_interpolation import EncodecInterpolation
    print(f"  ✓ EncodecInterpolation class imported")
except ImportError as e:
    print(f"  ✗ Failed to import: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Test 3: Initialize model
print("\n[Test 3] Initializing EnCodec model (48kHz stereo)...")
try:
    interpolator = EncodecInterpolation(
        model_name="facebook/encodec_48khz",
        device="cpu",
        bandwidth=6.0
    )
    print(f"  ✓ Model initialized successfully")
    print(f"  ✓ Sampling rate: {interpolator.sampling_rate} Hz")
except Exception as e:
    print(f"  ✗ Model initialization failed: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Test 4: Load test audio
print("\n[Test 4] Loading test audio...")
try:
    import librosa
    import numpy as np

    # Use first 5 seconds of praise.mp3 for testing
    audio_path = Path(__file__).parent.parent / "poc_audio" / "praise.mp3"

    if not audio_path.exists():
        # Try alternative audio files
        audio_dir = Path(__file__).parent.parent / "poc_audio"
        audio_files = list(audio_dir.glob("*.mp3")) + list(audio_dir.glob("*.flac"))
        if audio_files:
            audio_path = audio_files[0]
        else:
            raise FileNotFoundError("No audio files found in poc_audio/")

    y, sr = librosa.load(str(audio_path), sr=44100, mono=False, duration=5.0)

    # Ensure stereo
    if y.ndim == 1:
        y = np.stack([y, y])

    print(f"  ✓ Loaded audio: {audio_path.name}")
    print(f"  ✓ Shape: {y.shape}, Sample rate: {sr} Hz, Duration: {y.shape[1]/sr:.2f}s")
except Exception as e:
    print(f"  ✗ Failed to load audio: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Test 5: Encode/decode roundtrip
print("\n[Test 5] Testing encode/decode roundtrip...")
try:
    import torch

    # Take first 2 seconds for quick test
    test_audio = y[:, :int(2 * sr)]

    # Convert to tensor
    audio_tensor = torch.from_numpy(test_audio).float().unsqueeze(0)

    # Resample to model SR
    if sr != interpolator.sampling_rate:
        test_audio_resampled = interpolator._resample(
            test_audio, sr, interpolator.sampling_rate
        )
    else:
        test_audio_resampled = test_audio

    audio_tensor = torch.from_numpy(test_audio_resampled).float().unsqueeze(0)

    # Encode
    with torch.no_grad():
        embeddings = interpolator._encode_continuous(audio_tensor)

    print(f"  ✓ Encoding successful")
    print(f"    Embeddings shape: {embeddings.shape}")

    # Decode
    with torch.no_grad():
        reconstructed = interpolator._decode_continuous(embeddings)

    print(f"  ✓ Decoding successful")
    print(f"    Reconstructed shape: {reconstructed.shape}")

except Exception as e:
    print(f"  ✗ Encode/decode failed: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Test 6: Two-audio interpolation
print("\n[Test 6] Testing two-audio interpolation...")
try:
    # Split the 5-second audio into two 2-second segments with 1s gap
    segment_a = y[:, :int(2 * sr)]
    segment_b = y[:, int(3 * sr):int(5 * sr)]

    print(f"  Segment A: {segment_a.shape}, duration: {segment_a.shape[1]/sr:.2f}s")
    print(f"  Segment B: {segment_b.shape}, duration: {segment_b.shape[1]/sr:.2f}s")

    # Run interpolation with 8 steps for quick test
    interpolated, output_sr = interpolator.interpolate(
        segment_a,
        segment_b,
        num_steps=8,
        input_sr=sr
    )

    print(f"  ✓ Interpolation successful")
    print(f"    Output shape: {interpolated.shape}")
    print(f"    Output SR: {output_sr} Hz")
    print(f"    Total duration: {interpolated.shape[1]/output_sr:.2f}s")

    # Save test output
    import soundfile as sf
    output_path = Path("/tmp/test_encodec_interpolation_output.wav")
    sf.write(output_path, interpolated.T, output_sr)
    print(f"  ✓ Saved test output to: {output_path}")

except Exception as e:
    print(f"  ✗ Interpolation failed: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Test 7: Slerp function
print("\n[Test 7] Testing slerp function...")
try:
    # Create two random vectors
    v0 = torch.randn(1, 128, 100)
    v1 = torch.randn(1, 128, 100)

    # Test interpolation at t=0, 0.5, 1.0
    result_0 = interpolator._slerp(v0, v1, 0.0)
    result_half = interpolator._slerp(v0, v1, 0.5)
    result_1 = interpolator._slerp(v0, v1, 1.0)

    # Verify edge cases
    assert torch.allclose(result_0, v0, atol=1e-5), "t=0 should return v0"
    assert torch.allclose(result_1, v1, atol=1e-5), "t=1 should return v1"

    print(f"  ✓ Slerp edge cases verified (t=0 and t=1)")
    print(f"  ✓ Slerp interpolation at t=0.5 computed")

except Exception as e:
    print(f"  ✗ Slerp test failed: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Summary
print("\n" + "=" * 70)
print("✓ ALL TESTS PASSED")
print("=" * 70)
print("\nEnCodec interpolation module is ready for integration.")
print(f"Test output saved to: /tmp/test_encodec_interpolation_output.wav")
print("\nNext steps:")
print("  1. Listen to test output to verify quality")
print("  2. Integrate into generate_section_transitions.py")
print("  3. Run full transition generation workflow")
sys.exit(0)
