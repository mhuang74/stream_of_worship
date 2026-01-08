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

References:
- EnCodec Paper: https://arxiv.org/abs/2210.13438
- Transformers Docs: https://huggingface.co/docs/transformers/model_doc/encodec
- Interpolation approach inspired by: https://github.com/jhurliman/music-interpolation
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
