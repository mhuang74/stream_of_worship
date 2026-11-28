"""Audio geometry shared by the Glue Lambda, Modal POC and Runpod worker."""

from math import gcd

import numpy as np

SAMPLE_RATE = 44100
CHANNELS = 2
MODEL_RATE = 48000
HOP = 1920
WINDOW_LATENTS = 375
MODEL_ID = "acestep-v15-base"
UPSTREAM_REVISION = "f5175f14b3be67e28ffc7c95874265a0390a9ada"
CHECKPOINT_REVISION = "e432212fec32b8965a14ffa57ae653438d6abd14"
STEPS = 100
# Signed R2 lifetime, and the oldest submission the GPU still honors. A Lambda
# invocation cannot outlive LAMBDA_SECONDS (900) of glue_cascade, so a 900 s
# submission age can only belong to a dead invocation; 3600 s leaves room for
# one download+sampling+PUT cycle on top of that.
PRESIGN_SECONDS = 3600
STALE_SECONDS = 900
INPUT_CAP_BYTES = 10_000_000


def resampled_frames(frames: int) -> int:
    return (frames * MODEL_RATE + SAMPLE_RATE - 1) // SAMPLE_RATE


def latent_window(a_frames: int, fill_frames: int, b_frames: int) -> tuple[int, int]:
    a, b = (
        round((frame / SAMPLE_RATE) * MODEL_RATE) for frame in (a_frames, a_frames + fill_frames)
    )
    width = WINDOW_LATENTS * HOP
    length = max(resampled_frames(a_frames + fill_frames + b_frames), width)
    start = min(max((a + b) // 2 - width // 2, 0), length - width) // HOP * HOP
    fs = (a - start) // HOP - 20
    fe = (b - start + HOP - 1) // HOP + 20
    if not 0 < fs < fe < WINDOW_LATENTS:
        raise ValueError("Gap exceeds the supported SimDPS window")
    return fs, fe


def search_candidates(guide_frames: int, window: tuple[int, int]) -> int:
    fs, fe = window
    available = resampled_frames(guide_frames) // HOP - 1
    required = fe - fs + min(5, fs) + min(5, WINDOW_LATENTS - fe)
    return max(0, available - required + 1)


def extract_fill(
    audio: np.ndarray,
    model_rate: int,
    window: tuple[int, int],
    gap: tuple[int, int],
    requested_gap: tuple[int, int],
) -> np.ndarray:
    """Crop the raw model window in its own coordinates, then resample the fill."""
    from scipy.signal import resample_poly

    window_start, window_end = window
    gap_start, gap_end = gap
    target_frames = requested_gap[1] - requested_gap[0]
    expected_gap = tuple(round(frame * model_rate / SAMPLE_RATE) for frame in requested_gap)
    if (
        audio.shape != (1, CHANNELS, window_end - window_start)
        or not 0 <= window_start <= gap_start < gap_end <= window_end
        or gap != expected_gap
        or target_frames <= 0
    ):
        raise ValueError(f"unsupported model window {window} for gap {gap} at {model_rate} Hz")

    fill = audio[0, :, gap_start - window_start : gap_end - window_start].T
    if not np.isfinite(fill).all():
        raise ValueError("model fill contains non-finite samples")
    if model_rate != SAMPLE_RATE:
        divisor = gcd(SAMPLE_RATE, model_rate)
        fill = resample_poly(fill, SAMPLE_RATE // divisor, model_rate // divisor, axis=0)
    # Rounding the two endpoints independently at the model rate can add or drop a frame.
    if len(fill) < target_frames:
        fill = np.pad(fill, ((0, target_frames - len(fill)), (0, 0)), mode="edge")
    fill = np.ascontiguousarray(fill[:target_frames], dtype=np.float32)
    if not np.isfinite(fill).all():
        raise ValueError("resampled fill contains non-finite samples")
    return fill
