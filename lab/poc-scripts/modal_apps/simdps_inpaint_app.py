"""Serve the pinned SimDPS-Multi sampler for the Glue POC on Modal."""

from io import BytesIO
from math import gcd
from time import perf_counter

import modal
import numpy as np

APP_NAME = "sow-simdps-poc"
MODEL_ID = "acestep-v15-base"
UPSTREAM_REVISION = "f5175f14b3be67e28ffc7c95874265a0390a9ada"
UPSTREAM_REPO = "https://github.com/s-turland/SimDPSInpainting.git"
MODEL_CACHE = "/models"
SAMPLE_RATE = 44100
CHANNELS = 2

image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("git", "libsndfile1")
    .pip_install("torch==2.8.0", index_url="https://download.pytorch.org/whl/cu128")
    .pip_install(f"simdps[acestep] @ git+{UPSTREAM_REPO}@{UPSTREAM_REVISION}")
)
model_volume = modal.Volume.from_name("sow-simdps-models", create_if_missing=True)
app = modal.App(APP_NAME)


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
    return np.ascontiguousarray(fill[:target_frames], dtype=np.float32)


def _decode_flac(data: bytes) -> np.ndarray:
    import soundfile as sf

    with sf.SoundFile(BytesIO(data)) as source:
        if source.format != "FLAC" or source.samplerate != SAMPLE_RATE:
            raise ValueError(f"expected {SAMPLE_RATE} Hz FLAC")
        if source.channels != CHANNELS or not len(source):
            raise ValueError("expected nonempty stereo audio")
        return source.read(dtype="float32", always_2d=True)


@app.cls(
    image=image,
    gpu="L4",
    cpu=4,
    memory=16384,
    timeout=900,
    startup_timeout=1800,
    scaledown_window=120,
    max_containers=1,
    volumes={MODEL_CACHE: model_volume},
)
class SimDPSInpaint:
    @modal.enter()
    def load_model(self):
        import torch
        from simdps import Inpainter
        from simdps.priors import ACEStep

        started = perf_counter()
        prior = ACEStep(model="base", device="cuda:0", dtype=torch.bfloat16, cache_dir=MODEL_CACHE)
        self.inpainter = Inpainter(prior, cache_dir=None)
        model_volume.commit()
        self.load_seconds = perf_counter() - started

    @modal.method()
    def inpaint(
        self, montage_flac: bytes, guide_flac: bytes, gap: tuple[int, int], seed: int, steps: int
    ) -> tuple[bytes, dict]:
        import soundfile as sf
        import torch

        started = perf_counter()
        montage = _decode_flac(montage_flac)
        guide = _decode_flac(guide_flac)
        if not 0 < gap[0] < gap[1] < len(montage):
            raise ValueError(f"gap {gap} needs context on both sides of the montage")

        sampling_started = perf_counter()
        # Upstream rejects gaps whose latent mask, including its buffer, touches a window edge.
        result = self.inpainter.inpaint(
            torch.from_numpy(montage.T.copy()),
            SAMPLE_RATE,
            tuple(frame / SAMPLE_RATE for frame in gap),
            source=torch.from_numpy(guide.T.copy()),
            method="multi",
            raw=True,
            seed=seed,
            steps=steps,
        )
        sampling_seconds = perf_counter() - sampling_started
        fill = extract_fill(
            result.audio.numpy(), result.sample_rate, result.window, result.gap, gap
        )
        output = BytesIO()
        sf.write(output, fill, SAMPLE_RATE, format="WAV", subtype="FLOAT")
        return output.getvalue(), {
            "model_id": MODEL_ID,
            "upstream_revision": UPSTREAM_REVISION,
            "method": "multi",
            "gpu": torch.cuda.get_device_name(),
            "seed": seed,
            "steps": steps,
            "sample_rate": SAMPLE_RATE,
            "fill_frames": len(fill),
            "model_sample_rate": result.sample_rate,
            "model_window_frames": list(result.window),
            "model_gap_frames": list(result.gap),
            "model_load_seconds": round(self.load_seconds, 3),
            "sampling_seconds": round(sampling_seconds, 3),
            "processing_seconds": round(perf_counter() - started, 3),
        }
