# Implementation Plan: Latent-Space Transition Variant using EnCodec (Transformers)

**Task:** Add `latent-space` transition variant to worship music transition system using EnCodec from HuggingFace Transformers library for neural codec-based latent space interpolation.

**Status:** Ready for implementation
**Date:** 2026-01-08

---

## Executive Summary

This plan implements a new `latent-space` transition variant that uses Meta's EnCodec neural audio codec (via HuggingFace Transformers) to perform smooth interpolation in continuous latent space between two worship song sections. The approach:

1. Uses EnCodec's encoder to extract continuous embeddings (bypassing quantization)
2. Performs spherical linear interpolation (slerp) in latent space
3. Decodes interpolated embeddings back to audio
4. Provides a perceptually smoother alternative to waveform crossfading

**Key Advantages:**
- Works in learned perceptual space (not raw waveforms)
- Smoother transitions with fewer artifacts
- No stem separation required
- Integrates with existing transition generation system

---

## Phase 1: Add Dependencies

### 1.1 Update requirements_allinone.txt

**File:** `/Users/mhuang/Projects/Development/stream_of_worship/requirements_allinone.txt`

**Action:** Add transformers library (if not already present)

```diff
# Core ML/Audio processing library
allin1==1.1.0

+# Transformers for EnCodec
+transformers>=4.35.0

# PyTorch ecosystem (CPU version)
```

**Why transformers?**
- EnCodec is natively integrated in transformers library
- Better maintained than standalone `encodec` package
- Already have `huggingface-hub==0.24.6` dependency
- Compatible with existing PyTorch 2.4.1+cpu

**Verification:** Check if transformers is already installed, otherwise add it.

---

## Phase 2: Implement EncodecInterpolation Module

### 2.1 Create Core Module

**File:** `/Users/mhuang/Projects/Development/stream_of_worship/poc/encodec_interpolation.py`

**Purpose:** Self-contained EnCodec interpolation class using Transformers API

**Key Implementation Details:**

```python
#!/usr/bin/env python3
"""
EnCodec Latent Space Interpolation Module

This module provides smooth audio interpolation using Meta's EnCodec neural codec
via the HuggingFace Transformers library. It bypasses quantization to enable
continuous latent space interpolation using spherical linear interpolation (slerp).

Key Features:
- Uses transformers EncodecModel (not standalone encodec package)
- Separates encoding from quantization for smooth interpolation
- Supports both 24kHz mono and 48kHz stereo models
- Implements slerp for perceptually smooth transitions
- Handles sample rate conversion automatically
"""

import torch
import numpy as np
import librosa
from typing import Tuple, Optional
from pathlib import Path

import warnings
warnings.filterwarnings('ignore')


class EncodecInterpolation:
    """
    EnCodec-based latent space audio interpolation.

    Uses HuggingFace Transformers implementation of EnCodec to perform
    smooth interpolation between audio segments in learned latent space.
    """

    def __init__(self,
                 model_name: str = "facebook/encodec_48khz",
                 device: str = "cpu",
                 bandwidth: float = 6.0):
        """
        Initialize EnCodec interpolation using Transformers.

        Args:
            model_name: HuggingFace model name
                - "facebook/encodec_24khz" (mono, 24kHz)
                - "facebook/encodec_48khz" (stereo, 48kHz) [default]
            device: Device for inference ("cpu" or "cuda")
            bandwidth: Target bandwidth in kbps (6.0 = highest quality)
        """
        from transformers import EncodecModel, AutoProcessor

        self.device = device
        self.model_name = model_name
        self.bandwidth = bandwidth

        # Load model and processor
        self.model = EncodecModel.from_pretrained(model_name)
        self.processor = AutoProcessor.from_pretrained(model_name)

        self.model.to(device)
        self.model.eval()

        # Store sampling rate
        self._sampling_rate = self.processor.sampling_rate

    @property
    def sampling_rate(self) -> int:
        """Get model's native sampling rate (24000 or 48000)."""
        return self._sampling_rate

    def interpolate(self,
                   audio_a: np.ndarray,
                   audio_b: np.ndarray,
                   num_steps: int = 32,
                   input_sr: int = 44100) -> Tuple[np.ndarray, int]:
        """
        Interpolate between two audio segments in latent space.

        Algorithm:
        1. Resample both audio segments to model's native SR
        2. Encode both to continuous latent embeddings (skip quantization)
        3. Generate interpolation coefficients (0 -> 1)
        4. For each step: slerp embeddings, decode to audio
        5. Concatenate all decoded steps
        6. Return interpolated audio at model's SR

        Args:
            audio_a: First audio segment (stereo, shape [2, samples])
            audio_b: Second audio segment (stereo, shape [2, samples])
            num_steps: Number of interpolation steps (default: 32)
            input_sr: Input sample rate (will be resampled to model SR)

        Returns:
            (interpolated_audio, output_sr) tuple
            - interpolated_audio: shape [2, total_samples]
            - output_sr: Native model sample rate (24000 or 48000)

        Raises:
            ValueError: If audio shapes incompatible
        """
        # Resample to model's sampling rate
        if input_sr != self._sampling_rate:
            audio_a = self._resample(audio_a, input_sr, self._sampling_rate)
            audio_b = self._resample(audio_b, input_sr, self._sampling_rate)

        # Ensure stereo (model expects [channels, samples])
        if audio_a.ndim == 1:
            audio_a = np.stack([audio_a, audio_a])
        if audio_b.ndim == 1:
            audio_b = np.stack([audio_b, audio_b])

        # Convert to torch tensors [batch, channels, samples]
        audio_a_tensor = torch.from_numpy(audio_a).float().unsqueeze(0)
        audio_b_tensor = torch.from_numpy(audio_b).float().unsqueeze(0)

        # Move to device
        audio_a_tensor = audio_a_tensor.to(self.device)
        audio_b_tensor = audio_b_tensor.to(self.device)

        # Encode to continuous latent embeddings (bypass quantization)
        with torch.no_grad():
            emb_a = self._encode_continuous(audio_a_tensor)
            emb_b = self._encode_continuous(audio_b_tensor)

        # Generate interpolation steps
        interpolated_chunks = []
        t_values = np.linspace(0, 1, num_steps)

        for t in t_values:
            # Spherical linear interpolation in latent space
            emb_interp = self._slerp(emb_a, emb_b, t)

            # Decode interpolated embedding back to audio
            with torch.no_grad():
                audio_interp = self._decode_continuous(emb_interp)

            interpolated_chunks.append(audio_interp)

        # Concatenate all chunks: [num_steps, batch, channels, samples]
        result = torch.cat(interpolated_chunks, dim=2)  # Concat along time

        # Convert to numpy [channels, samples]
        result_np = result.squeeze(0).cpu().numpy()

        return result_np, self._sampling_rate

    def _encode_continuous(self, audio: torch.Tensor) -> torch.Tensor:
        """
        Encode audio to continuous latent embeddings (bypass quantization).

        This method accesses the encoder directly to get continuous embeddings
        before the quantization step.

        Args:
            audio: Audio tensor [batch, channels, samples]

        Returns:
            Continuous latent embeddings from encoder
        """
        # Use encoder directly (skip quantizer)
        # EncodecModel has: model.encoder, model.quantizer, model.decoder
        encoded_frames = self.model.encoder(audio)

        # Return continuous embeddings (before quantization)
        return encoded_frames

    def _decode_continuous(self, embeddings: torch.Tensor) -> torch.Tensor:
        """
        Decode continuous latent embeddings back to audio.

        Args:
            embeddings: Continuous latent embeddings

        Returns:
            Reconstructed audio tensor [batch, channels, samples]
        """
        # Use decoder directly
        audio = self.model.decoder(embeddings)

        return audio

    def _slerp(self,
              v0: torch.Tensor,
              v1: torch.Tensor,
              t: float,
              epsilon: float = 1e-4) -> torch.Tensor:
        """
        Spherical linear interpolation between two vectors.

        Slerp provides smoother interpolation than linear (lerp) by following
        the great circle path on a hypersphere. This is more perceptually
        natural for learned embeddings.

        Args:
            v0: Start vector (any shape)
            v1: End vector (same shape as v0)
            t: Interpolation coefficient [0, 1]
            epsilon: Threshold for fallback to lerp (default: 1e-4)

        Returns:
            Interpolated vector (same shape as inputs)
        """
        # Handle t edge cases
        if t <= 0:
            return v0
        if t >= 1:
            return v1

        # Flatten to 1D for dot product calculation
        original_shape = v0.shape
        v0_flat = v0.flatten()
        v1_flat = v1.flatten()

        # Normalize vectors
        v0_norm = torch.nn.functional.normalize(v0_flat, dim=0)
        v1_norm = torch.nn.functional.normalize(v1_flat, dim=0)

        # Calculate angle between vectors
        dot = torch.dot(v0_norm, v1_norm)
        dot = torch.clamp(dot, -1.0, 1.0)

        # If vectors are nearly parallel, use linear interpolation
        if torch.abs(dot) > (1.0 - epsilon):
            result = (1 - t) * v0_flat + t * v1_flat
            return result.reshape(original_shape)

        # Slerp formula
        omega = torch.acos(dot)
        sin_omega = torch.sin(omega)

        a = torch.sin((1.0 - t) * omega) / sin_omega
        b = torch.sin(t * omega) / sin_omega

        result = a * v0_flat + b * v1_flat
        return result.reshape(original_shape)

    def _resample(self,
                 audio: np.ndarray,
                 orig_sr: int,
                 target_sr: int) -> np.ndarray:
        """
        Resample audio using librosa with high quality.

        Args:
            audio: Audio array (mono or stereo)
            orig_sr: Original sample rate
            target_sr: Target sample rate

        Returns:
            Resampled audio (same shape pattern as input)
        """
        if orig_sr == target_sr:
            return audio

        # Resample each channel if stereo
        if audio.ndim == 2:
            resampled = np.stack([
                librosa.resample(
                    audio[0],
                    orig_sr=orig_sr,
                    target_sr=target_sr,
                    res_type='soxr_hq'
                ),
                librosa.resample(
                    audio[1],
                    orig_sr=orig_sr,
                    target_sr=target_sr,
                    res_type='soxr_hq'
                )
            ])
        else:
            resampled = librosa.resample(
                audio,
                orig_sr=orig_sr,
                target_sr=target_sr,
                res_type='soxr_hq'
            )

        return resampled


# Helper function for external use
def interpolate_audio_segments(audio_a: np.ndarray,
                              audio_b: np.ndarray,
                              input_sr: int = 44100,
                              num_steps: int = 32,
                              model_name: str = "facebook/encodec_48khz",
                              device: str = "cpu") -> Tuple[np.ndarray, int]:
    """
    Convenience function to interpolate two audio segments.

    Args:
        audio_a: First audio segment [channels, samples]
        audio_b: Second audio segment [channels, samples]
        input_sr: Input sample rate
        num_steps: Number of interpolation steps
        model_name: EnCodec model to use
        device: Processing device

    Returns:
        (interpolated_audio, output_sr) tuple
    """
    interpolator = EncodecInterpolation(model_name=model_name, device=device)
    return interpolator.interpolate(audio_a, audio_b, num_steps, input_sr)
```

**Key Design Decisions:**

1. **Use Transformers API:** `EncodecModel.from_pretrained()` and `AutoProcessor`
2. **Access encoder/decoder directly:** Bypass `model.encode()` which quantizes
3. **Slerp with fallback:** Use linear interpolation for near-parallel vectors
4. **High-quality resampling:** Use `soxr_hq` for minimal artifacts
5. **Stereo support:** Default to 48kHz stereo model
6. **Memory efficient:** Use `torch.no_grad()` contexts

---

## Phase 3: Create Test Script

### 3.1 Test Script Implementation

**File:** `/Users/mhuang/Projects/Development/stream_of_worship/scripts/test_encodec_interpolation.py`

**Purpose:** Validate EnCodec interpolation implementation before integration

**Test Structure:**

```python
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
```

**Test Coverage:**
1. ✓ Transformers library availability
2. ✓ EnCodec model import
3. ✓ Custom module import
4. ✓ Model initialization (48kHz stereo)
5. ✓ Test audio loading
6. ✓ Encode/decode roundtrip
7. ✓ Two-audio interpolation
8. ✓ Slerp correctness
9. ✓ Output file generation

---

## Phase 4: Integrate into Transition Generation

### 4.1 Update Configuration

**File:** `/Users/mhuang/Projects/Development/stream_of_worship/poc/generate_section_transitions.py`

**Location:** Lines ~45-66 (CONFIG dict)

```python
CONFIG = {
    # ... existing config ...

    # Stem-based fade transition options (v2.1)
    'stem_fade_transition_beats': 8,
    'stem_fade_duration_beats': 4,

    # Latent-space transition options (v2.2)
    'latent_space_interpolation_steps': 32,      # Number of interpolation steps
    'latent_space_model': 'facebook/encodec_48khz',  # EnCodec model (24khz or 48khz)
    'latent_space_overlap_duration': 4.0,        # Seconds from each section
    'latent_space_silence_beats': 0,             # Optional silence gap (0 = none)
    'latent_space_bandwidth': 6.0,               # EnCodec bandwidth (6.0 = highest)

    # Optional features
    'generate_waveforms': False,
    'verbose': True
}
```

### 4.2 Add Generation Function

**Location:** After `generate_drum_fade_transition` (around line ~714)

```python
def generate_latent_space_transition(song_a_path, song_b_path,
                                    section_a, section_b,
                                    tempo_a=None,
                                    interpolation_steps=32,
                                    overlap_duration=4.0,
                                    silence_beats=0):
    """
    Create latent-space transition using EnCodec neural codec interpolation.

    Algorithm:
    1. Extract overlap regions from end of section A and start of section B
    2. Load EnCodec model from Transformers (48kHz stereo model)
    3. Encode both segments to continuous latent space (bypass quantization)
    4. Perform spherical linear interpolation (slerp) with N steps
    5. Decode interpolated latents back to audio
    6. Optional: Insert silence gap in middle of interpolation
    7. Concatenate: [A_pre] + [interpolation] + [B_post]
    8. Resample back to project sample rate (44.1kHz)

    This method differs from crossfade/stem-based variants by working in
    learned perceptual latent space rather than raw waveforms.

    Args:
        song_a_path, song_b_path: Paths to audio files
        section_a, section_b: Section dicts with 'start', 'end' keys
        tempo_a: Optional tempo for silence gap calculation (BPM)
        interpolation_steps: Number of interpolation steps (default: 32)
        overlap_duration: Seconds of audio from each section (default: 4.0)
        silence_beats: Optional silence beats in middle (default: 0 = none)

    Returns:
        (transition_audio, sample_rate, actual_duration, metadata_dict)

    Raises:
        ImportError: If transformers not installed
        ValueError: If sections too short for overlap
        RuntimeError: If EnCodec processing fails
    """
    try:
        from poc.encodec_interpolation import EncodecInterpolation
    except ImportError:
        raise ImportError(
            "EnCodec interpolation requires transformers. "
            "Install with: pip install transformers>=4.35.0"
        )

    sr = CONFIG['sample_rate']

    # Load full sections
    y_a, sr_a = librosa.load(str(song_a_path), sr=sr, mono=False)
    y_b, sr_b = librosa.load(str(song_b_path), sr=sr, mono=False)

    # Ensure stereo
    if y_a.ndim == 1:
        y_a = np.stack([y_a, y_a])
    if y_b.ndim == 1:
        y_b = np.stack([y_b, y_b])

    # Extract sections
    section_a_start = int(section_a['start'] * sr)
    section_a_end = int(section_a['end'] * sr)
    section_b_start = int(section_b['start'] * sr)
    section_b_end = int(section_b['end'] * sr)

    section_a_audio = y_a[:, section_a_start:section_a_end]
    section_b_audio = y_b[:, section_b_start:section_b_end]

    # Calculate overlap samples
    overlap_samples = int(overlap_duration * sr)

    # Validate section lengths
    if section_a_audio.shape[1] < overlap_samples:
        raise ValueError(
            f"Section A too short ({section_a_audio.shape[1]/sr:.1f}s) "
            f"for overlap ({overlap_duration}s). "
            f"Need at least {overlap_duration}s per section."
        )
    if section_b_audio.shape[1] < overlap_samples:
        raise ValueError(
            f"Section B too short ({section_b_audio.shape[1]/sr:.1f}s) "
            f"for overlap ({overlap_duration}s). "
            f"Need at least {overlap_duration}s per section."
        )

    # Split sections: [pre] + [overlap] for A, [overlap] + [post] for B
    section_a_pre = section_a_audio[:, :-overlap_samples]
    section_a_overlap = section_a_audio[:, -overlap_samples:]

    section_b_overlap = section_b_audio[:, :overlap_samples]
    section_b_post = section_b_audio[:, overlap_samples:]

    log(f"      Section A: {section_a_audio.shape[1]/sr:.1f}s "
        f"(pre: {section_a_pre.shape[1]/sr:.1f}s, overlap: {overlap_duration}s)")
    log(f"      Section B: {section_b_audio.shape[1]/sr:.1f}s "
        f"(overlap: {overlap_duration}s, post: {section_b_post.shape[1]/sr:.1f}s)")

    # Initialize EnCodec interpolator
    log(f"      Loading EnCodec model: {CONFIG['latent_space_model']}...")
    interpolator = EncodecInterpolation(
        model_name=CONFIG['latent_space_model'],
        device='cpu',
        bandwidth=CONFIG['latent_space_bandwidth']
    )

    # Perform interpolation in latent space
    log(f"      Interpolating in latent space ({interpolation_steps} steps)...")
    interpolated, interp_sr = interpolator.interpolate(
        section_a_overlap,
        section_b_overlap,
        num_steps=interpolation_steps,
        input_sr=sr
    )

    log(f"      Interpolation complete: {interpolated.shape[1]/interp_sr:.2f}s at {interp_sr}Hz")

    # Resample interpolation back to project sample rate if needed
    if interp_sr != sr:
        log(f"      Resampling from {interp_sr}Hz to {sr}Hz...")
        interpolated = librosa.resample(
            interpolated,
            orig_sr=interp_sr,
            target_sr=sr,
            res_type='soxr_hq'
        )

    # Optional: Insert silence gap in middle of interpolation
    silence_duration = 0.0
    if silence_beats > 0 and tempo_a:
        silence_duration = (60.0 / tempo_a) * silence_beats
        silence_samples = int(silence_duration * sr)
        silence = np.zeros((2, silence_samples), dtype=section_a_audio.dtype)

        # Split interpolation in half and insert silence
        mid_point = interpolated.shape[1] // 2
        interpolated = np.concatenate([
            interpolated[:, :mid_point],
            silence,
            interpolated[:, mid_point:]
        ], axis=1)

        log(f"      Added {silence_beats}-beat silence gap ({silence_duration:.2f}s)")

    # Concatenate final transition: [A_pre] + [interpolation] + [B_post]
    transition = np.concatenate([
        section_a_pre,
        interpolated,
        section_b_post
    ], axis=1)

    actual_duration = transition.shape[1] / sr
    interpolation_duration = interpolated.shape[1] / sr

    # Build metadata
    metadata = {
        'interpolation_steps': interpolation_steps,
        'interpolation_method': 'slerp',  # Spherical linear interpolation
        'overlap_duration': overlap_duration,
        'interpolation_duration': interpolation_duration,
        'silence_beats': silence_beats,
        'silence_duration': silence_duration,
        'model_used': CONFIG['latent_space_model'],
        'bandwidth': CONFIG['latent_space_bandwidth'],
        'native_model_sr': interpolator.sampling_rate,
        'encoding_type': 'continuous_latent',  # Not quantized
    }

    return transition, sr, actual_duration, metadata
```

### 4.3 Integrate into generate_all_variants()

**Location:** After drum-fade variant (around line ~990)

```python
    # === DRUM-FADE VARIANT ===
    # ... existing drum-fade code ...

    # === LATENT-SPACE VARIANT (EnCodec Neural Codec Interpolation) ===
    log(f"    Generating LATENT-SPACE variant ({CONFIG['latent_space_interpolation_steps']}-step neural interpolation)...")

    try:
        tempo_a = pair['tempo_a']

        transition, sr, duration, ls_metadata = generate_latent_space_transition(
            song_a_path, song_b_path, section_a, section_b,
            tempo_a=tempo_a,
            interpolation_steps=CONFIG['latent_space_interpolation_steps'],
            overlap_duration=CONFIG['latent_space_overlap_duration'],
            silence_beats=CONFIG['latent_space_silence_beats']
        )

        # Generate filename
        filename = (f"transition_latent_space_{base_a}_{section_a['label']}_"
                   f"{base_b}_{section_b['label']}_"
                   f"{CONFIG['latent_space_interpolation_steps']}steps."
                   f"{CONFIG['output_format']}")

        # Save audio
        filepath = audio_dir / 'latent-space' / filename
        filepath.parent.mkdir(parents=True, exist_ok=True)
        sf.write(filepath, transition.T, sr)

        file_size_mb = filepath.stat().st_size / (1024 * 1024)

        # Build variant metadata
        variants.append({
            'variant_type': 'latent-space',
            'interpolation_steps': ls_metadata['interpolation_steps'],
            'interpolation_method': ls_metadata['interpolation_method'],
            'interpolation_duration': ls_metadata['interpolation_duration'],
            'overlap_duration': ls_metadata['overlap_duration'],
            'silence_beats': ls_metadata['silence_beats'],
            'silence_duration': ls_metadata['silence_duration'],
            'model_used': ls_metadata['model_used'],
            'bandwidth': ls_metadata['bandwidth'],
            'native_model_sr': ls_metadata['native_model_sr'],
            'encoding_type': ls_metadata['encoding_type'],
            'total_duration': duration,
            'sections_included': {
                'song_a': [section_a['label']],
                'song_b': [section_b['label']]
            },
            'filename': str(filepath.relative_to(CONFIG['output_dir'])),
            'file_size_mb': round(file_size_mb, 2),
            'audio_specs': {
                'sample_rate': sr,
                'channels': transition.shape[0],
                'format': CONFIG['output_format'].upper()
            }
        })

        log(f"      ✓ LATENT-SPACE: {filename} ({file_size_mb:.2f} MB, {duration:.1f}s)")

    except ImportError:
        log(f"      ⚠️  Skipped LATENT-SPACE: transformers not installed")
        log(f"          Install with: pip install transformers>=4.35.0")
    except ValueError as e:
        log(f"      ⚠️  Skipped LATENT-SPACE: {e}")
    except Exception as e:
        log(f"      ✗ Failed to generate LATENT-SPACE variant: {e}")
        import traceback
        if CONFIG['verbose']:
            traceback.print_exc()

    return variants
```

### 4.4 Update Summary Report

**Location:** `print_summary_report()` function (around line 1336)

```python
    # Count variants
    total_variants = sum(len(t['variants']) for t in transitions)
    medium_crossfade_count = sum(1 for t in transitions for v in t['variants'] if v['variant_type'] == 'medium-crossfade')
    medium_silence_count = sum(1 for t in transitions for v in t['variants'] if v['variant_type'] == 'medium-silence')
    vocal_fade_count = sum(1 for t in transitions for v in t['variants'] if v['variant_type'] == 'vocal-fade')
    drum_fade_count = sum(1 for t in transitions for v in t['variants'] if v['variant_type'] == 'drum-fade')
    latent_space_count = sum(1 for t in transitions for v in t['variants'] if v['variant_type'] == 'latent-space')  # ADD THIS

    log(f"\n  Total variants generated: {total_variants}")
    log(f"    Medium-Crossfade (full sections with crossfade): {medium_crossfade_count}")
    log(f"    Medium-Silence ({CONFIG['silence_beats']}-beat silence gap): {medium_silence_count}")
    log(f"    Vocal-Fade ({CONFIG['stem_fade_transition_beats']}-beat vocal transition): {vocal_fade_count}")
    log(f"    Drum-Fade (4-beat drum transition with 1-beat gap): {drum_fade_count}")
    log(f"    Latent-Space ({CONFIG['latent_space_interpolation_steps']}-step neural interpolation): {latent_space_count}")  # ADD THIS
```

### 4.5 Add CLI Arguments

**Location:** `parse_args()` function (around line 1410)

```python
    # Silence transition options
    parser.add_argument('--silence-beats', type=int, default=4,
                        help='Number of beats for silence transition (default: 4)')

    # Latent-space transition options (ADD THIS SECTION)
    parser.add_argument('--latent-space-steps', type=int, default=32,
                        help='Number of interpolation steps for latent-space variant (default: 32)')
    parser.add_argument('--latent-space-overlap', type=float, default=4.0,
                        help='Overlap duration in seconds for latent-space variant (default: 4.0)')
    parser.add_argument('--latent-space-model', type=str,
                        default='facebook/encodec_48khz',
                        choices=['facebook/encodec_24khz', 'facebook/encodec_48khz'],
                        help='EnCodec model to use (default: 48khz)')
```

**Update CONFIG in main():** (around line 1458)

```python
    # Update CONFIG
    CONFIG['min_score'] = args.min_score
    CONFIG['silence_beats'] = args.silence_beats
    CONFIG['latent_space_interpolation_steps'] = args.latent_space_steps  # ADD THIS
    CONFIG['latent_space_overlap_duration'] = args.latent_space_overlap   # ADD THIS
    CONFIG['latent_space_model'] = args.latent_space_model                # ADD THIS
    if args.max_pairs:
        CONFIG['max_pairs'] = args.max_pairs
```

---

## Phase 5: Verification Steps

### 5.1 Unit Testing

**Steps:**
1. Run test script: `python scripts/test_encodec_interpolation.py`
2. Verify all 7 tests pass
3. Listen to output file: `/tmp/test_encodec_interpolation_output.wav`
4. Check for smoothness and lack of artifacts

**Expected Output:**
```
======================================================================
EnCodec Interpolation Module Tests
======================================================================

[Test 1] Checking transformers library...
  ✓ transformers version: 4.35.0
  ✓ EncodecModel and AutoProcessor imported successfully

[Test 2] Importing poc.encodec_interpolation module...
  ✓ EncodecInterpolation class imported

[Test 3] Initializing EnCodec model (48kHz stereo)...
  ✓ Model initialized successfully
  ✓ Sampling rate: 48000 Hz

[Test 4] Loading test audio...
  ✓ Loaded audio: praise.mp3
  ✓ Shape: (2, 220500), Sample rate: 44100 Hz, Duration: 5.00s

[Test 5] Testing encode/decode roundtrip...
  ✓ Encoding successful
    Embeddings shape: torch.Size([1, 128, 75])
  ✓ Decoding successful
    Reconstructed shape: torch.Size([1, 2, 96000])

[Test 6] Testing two-audio interpolation...
  Segment A: (2, 88200), duration: 2.00s
  Segment B: (2, 88200), duration: 2.00s
  ✓ Interpolation successful
    Output shape: (2, 768000)
    Output SR: 48000 Hz
    Total duration: 16.00s
  ✓ Saved test output to: /tmp/test_encodec_interpolation_output.wav

[Test 7] Testing slerp function...
  ✓ Slerp edge cases verified (t=0 and t=1)
  ✓ Slerp interpolation at t=0.5 computed

======================================================================
✓ ALL TESTS PASSED
======================================================================
```

### 5.2 Integration Testing

**Steps:**
1. Run transition generation on small dataset:
   ```bash
   python poc/generate_section_transitions.py --max-pairs 2 --verbose
   ```

2. Verify latent-space variant generates successfully

3. Check output structure:
   ```
   poc_output_allinone/section_transitions/
   └── audio/
       ├── medium-crossfade/
       ├── medium-silence/
       ├── vocal-fade/
       ├── drum-fade/
       └── latent-space/  ← NEW
           └── transition_latent_space_*.flac
   ```

4. Listen to generated transitions and compare quality

5. Check metadata in `transitions_index.json`

### 5.3 Error Case Testing

**Test scenarios:**
1. **Missing transformers:**
   - Uninstall transformers temporarily
   - Verify graceful skip with warning message
   - Other variants should still generate

2. **Short sections:**
   - Test with sections < 4 seconds
   - Verify ValueError with helpful message
   - Verify process continues with other pairs

3. **Memory constraints:**
   - Test with many pairs
   - Monitor memory usage
   - Verify no memory leaks

### 5.4 Quality Validation

**Listening tests:**
1. Compare latent-space transitions to crossfade baseline
2. Evaluate smoothness (no clicks or pops)
3. Check for artifacts (metallic sound, distortion)
4. Compare different interpolation steps (8, 16, 32, 64)
5. Test with different overlap durations (2s, 4s, 8s)

**Metrics to check:**
- Spectral continuity
- Energy consistency
- Phase alignment
- Perceptual smoothness

---

## Critical Files Summary

### New Files to Create
1. **`poc/encodec_interpolation.py`** - Core interpolation module (350 lines)
2. **`scripts/test_encodec_interpolation.py`** - Test script (250 lines)

### Files to Modify
1. **`requirements_allinone.txt`** - Add `transformers>=4.35.0`
2. **`poc/generate_section_transitions.py`** - Integration (6 locations):
   - Line ~66: CONFIG updates
   - Line ~716: New generation function
   - Line ~990: Variant generation block
   - Line ~1336: Summary report update
   - Line ~1410: CLI arguments
   - Line ~1458: CONFIG updates in main()

### Audio Output Structure
```
poc_output_allinone/section_transitions/
├── audio/
│   ├── latent-space/          ← NEW DIRECTORY
│   │   └── transition_latent_space_*.flac
│   ├── medium-crossfade/
│   ├── medium-silence/
│   ├── vocal-fade/
│   └── drum-fade/
└── metadata/
    ├── transitions_index.json   (updated with latent-space variants)
    └── transitions_summary.csv  (updated with latent-space count)
```

---

## Expected Performance

### Processing Time (per transition pair)
- **Encoding:** ~1-2 seconds per 4-second audio segment (CPU)
- **Interpolation:** ~0.5-1 second for 32 steps
- **Decoding:** ~1-2 seconds per interpolation step
- **Total:** ~40-50 seconds per transition (32 steps)

**Optimization opportunities:**
- GPU acceleration (if available)
- Reduce interpolation steps for faster generation
- Batch decode interpolation steps

### Audio Quality
- **Bandwidth:** 6.0 kbps (highest quality)
- **Sample rate:** 48kHz (decoded) → 44.1kHz (final output)
- **Channels:** Stereo
- **Expected quality:** Near-transparent with minor compression artifacts

---

## Potential Challenges & Solutions

### 1. Transformers Not Installed
**Issue:** User doesn't have transformers library
**Solution:** Graceful skip with clear installation instructions (like stem variants)

### 2. Sample Rate Mismatch
**Issue:** EnCodec (48kHz) vs project (44.1kHz)
**Solution:** High-quality resampling using `librosa` with `soxr_hq` resampler

### 3. Slow Processing
**Issue:** EnCodec inference is slower than waveform operations
**Solution:**
- Process only overlap regions (not full sections)
- Add progress logging
- Allow user to adjust interpolation steps

### 4. Quantization Artifacts
**Issue:** Interpolated audio may have codec artifacts
**Solution:**
- Use highest bandwidth (6.0 kbps)
- Use slerp instead of linear interpolation
- Document as experimental/research variant

### 5. Memory Usage
**Issue:** Loading multiple models (demucs + encodec)
**Solution:**
- Initialize EnCodec only when generating latent-space variant
- Use context managers for cleanup
- Process sequentially, not in parallel

### 6. Model Download on First Run
**Issue:** First run downloads ~100MB model from HuggingFace
**Solution:**
- Document expected delay on first run
- Model cached automatically by transformers
- Add informative logging during download

---

## Implementation Order

### Step 1: Dependencies ✓
1. Check if transformers installed: `pip list | grep transformers`
2. If not, add to `requirements_allinone.txt`: `transformers>=4.35.0`
3. Install: `pip install transformers>=4.35.0`

### Step 2: Core Module ✓
1. Create `poc/encodec_interpolation.py`
2. Implement `EncodecInterpolation` class
3. Add helper functions

### Step 3: Testing ✓
1. Create `scripts/test_encodec_interpolation.py`
2. Run tests: `python scripts/test_encodec_interpolation.py`
3. Listen to output: `/tmp/test_encodec_interpolation_output.wav`
4. Iterate on implementation if needed

### Step 4: Integration ✓
1. Update CONFIG in `generate_section_transitions.py`
2. Implement `generate_latent_space_transition()` function
3. Add variant generation block to `generate_all_variants()`
4. Update summary report
5. Add CLI arguments

### Step 5: Validation ✓
1. Run on small dataset: `python poc/generate_section_transitions.py --max-pairs 2`
2. Verify output files generated
3. Listen to latent-space transitions
4. Compare quality to other variants
5. Test error cases (missing deps, short sections)

### Step 6: Documentation (Optional)
1. Update README with latent-space variant info
2. Document configuration parameters
3. Add usage examples
4. Document expected performance

---

## Configuration Reference

### Default Parameters
```python
CONFIG = {
    'latent_space_interpolation_steps': 32,           # 8-64 recommended
    'latent_space_model': 'facebook/encodec_48khz',   # 24khz or 48khz
    'latent_space_overlap_duration': 4.0,             # 2.0-8.0 seconds
    'latent_space_silence_beats': 0,                  # 0-4 beats
    'latent_space_bandwidth': 6.0,                    # 6.0 = highest quality
}
```

### CLI Usage Examples
```bash
# Default (32 steps, 4s overlap, 48kHz model)
python poc/generate_section_transitions.py

# Faster generation (fewer steps)
python poc/generate_section_transitions.py --latent-space-steps 16

# Longer overlap for more context
python poc/generate_section_transitions.py --latent-space-overlap 8.0

# Use 24kHz mono model
python poc/generate_section_transitions.py --latent-space-model facebook/encodec_24khz

# Limit to 5 pairs for testing
python poc/generate_section_transitions.py --max-pairs 5
```

---

## Success Criteria

### ✅ Implementation Complete When:
1. All tests in `test_encodec_interpolation.py` pass
2. Latent-space variant generates successfully
3. Audio files saved to `audio/latent-space/` directory
4. Metadata includes latent-space entries
5. Error handling gracefully skips if transformers missing
6. No crashes or memory leaks during generation
7. Output audio quality is acceptable (minimal artifacts)
8. Integration doesn't break existing variants

### ✅ Quality Metrics:
- Smooth transitions (no clicks/pops)
- Spectral continuity between sections
- Minimal codec artifacts
- Comparable or better quality than crossfade baseline
- Processing time < 60 seconds per transition

---

## References

### Documentation
- **EnCodec Paper:** [High Fidelity Neural Audio Compression](https://arxiv.org/abs/2210.13438)
- **Transformers EnCodec:** https://huggingface.co/docs/transformers/main/en/model_doc/encodec
- **EnCodec Models:** https://huggingface.co/facebook/encodec_48khz
- **Interpolation Discussion:** https://github.com/facebookresearch/encodec/issues/68

### Inspiration
- **Music Interpolation Repo:** https://github.com/jhurliman/music-interpolation (separated quantization approach)

---

## End of Plan

This plan provides a comprehensive roadmap for implementing the latent-space transition variant using EnCodec from Transformers. The implementation follows the existing code patterns, maintains backward compatibility, and provides graceful error handling for missing dependencies.
