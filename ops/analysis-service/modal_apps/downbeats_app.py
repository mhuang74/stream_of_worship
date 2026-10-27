"""Modal app hosting beat/downbeat grid detection for sow-analysis.

This is the backend behind the component-analysis beat-grid cache miss: madmom's
8-network BLSTM downbeat ensemble plus the DBN decoder run on a small GPU
container instead of the multi-GB `sow-analyzer` image (allin1 + Demucs + NATTEN
+ a C toolchain). Those extra layers are cold-start time the beat grid never
needs.

Weights and decoder are the SAME as upstream madmom (madmom-infer 0.3.0 ships
madmom's own ``downbeats_blstm_[1-8].pkl``, sha256-verified at fetch, and ports
``DBNDownBeatTrackingProcessor`` verbatim), so the persisted beat-grid payload is
unchanged: the only difference is the torch BLSTM backend (``[torch]`` extra)
instead of madmom's compiled one.

Audio convention: ``madmom_infer.audio.signal`` does NOT resample — its
``_load_wave_file`` raises when the requested 44.1 kHz sampling rate differs from
the file's native rate. The analysis service uploads native-rate stereo PCM-16
WAV, so ``_mono_44k_wav`` normalizes here before handing the path to the
pipeline.

Deploy (from ops/analysis-service, with MODAL_TOKEN_ID/MODAL_TOKEN_SECRET exported):

    uv run --no-project --with 'modal>=1.2.6,<2' modal deploy modal_apps/downbeats_app.py
"""

import math
import pathlib

import modal

APP_NAME = "sow-downbeats"
CLASS_NAME = "DownbeatDetector"

# Same values the sow-analyzer implementation used. Changing either changes the
# persisted beat grid (madmom_params is written into the cache artifact).
BEATS_PER_BAR = [3, 4]
FPS = 100

# Torch cu126 wheels ship the CUDA runtime, so no nvidia base image is needed
# (same rationale as analyzer_app.py / stem_separator_app.py).
#
# Deliberately NOT here, each one cold-start time this app never pays for:
# allin1, natten, demucs, ffmpeg, libsndfile1, build-essential. Audio in/out is
# WAV via scipy.io.wavfile (both `_mono_44k_wav` and madmom_infer's own
# `_load_wave_file` use it), and the 8 BLSTM `.pkl` files (~30 MB) are
# sha256-verified downloads from raw.githubusercontent.com into
# `~/.cache/madmom_infer/models/` at container start.
#
# Commit-pinned: PyPI only carries stale historical snapshots of madmom-infer
# (0.3.0 is git-only). `[torch]` is already satisfied by the torch pin above, so
# it adds no second torch.
image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("git")  # required for the git+https pip install below
    .pip_install(
        "torch==2.6.0",
        index_url="https://download.pytorch.org/whl/cu126",
    )
    .pip_install(
        "madmom-infer[torch] @ git+https://github.com/openmirlab/madmom-infer.git"
        "@bffead9be61857fd44b2e0d3a2510d9d94c8f2d2",
    )
    # Fail the build, not the first request.
    .run_commands(
        "python -c 'import torch, madmom_infer; "
        "from madmom_infer.torch.features import build_pipeline; "
        "print(\"[sow-downbeats] imports OK\", torch.__version__, madmom_infer.__version__)'",
    )
)

app = modal.App(APP_NAME)


def _mono_44k_wav(audio_path: pathlib.Path, out_dir: pathlib.Path) -> pathlib.Path:
    """Downmix to mono and resample to 44.1 kHz PCM-16 WAV.

    madmom_infer.audio.signal does not resample: Signal/_load_wave_file raises on a
    sample-rate mismatch, so the pipeline must be handed 44.1 kHz mono audio.

    Returns the path of the normalized WAV inside ``out_dir``.
    """
    import numpy as np
    import scipy.io.wavfile
    import scipy.signal

    sr, data = scipy.io.wavfile.read(str(audio_path), mmap=False)
    samples = data.astype(np.float32)
    if np.issubdtype(data.dtype, np.integer):
        samples /= float(np.iinfo(data.dtype).max)
    mono = samples.mean(axis=1) if samples.ndim == 2 else samples
    if sr != 44100:
        g = math.gcd(44100, sr)
        mono = scipy.signal.resample_poly(mono, 44100 // g, sr // g).astype(np.float32)
    out = out_dir / "beat_grid_44k.wav"
    scipy.io.wavfile.write(str(out), 44100, (mono * 32767).astype(np.int16))
    return out


@app.cls(
    image=image,
    gpu="L4",
    cpu=4.0,
    memory=8192,  # MiB; madmom-infer's numpy DBN decoder peaks around 2.8 GiB on a 270 s file
    timeout=600,  # per-input kill switch -> FunctionTimeoutError (retryable client-side)
    startup_timeout=600,  # weights fetch + BLSTM load into VRAM
    max_containers=2,
    scaledown_window=300,
    # NOTE: deliberately NO retries= — retries are owned by the client
    # (services/modal_downbeats_client.py) so the layers cannot multiply into N x M GPU runs.
)
class DownbeatDetector:
    @modal.enter()
    def load_models(self) -> None:
        """Fetch the BLSTM weights and build the processor pair once per container."""
        import time

        from madmom_infer.features.downbeats import (
            DBNDownBeatTrackingProcessor,
            RNNDownBeatProcessor,
        )

        started = time.time()
        self.rnn = RNNDownBeatProcessor(backend="torch", device="cuda")
        # num_threads=2 decodes the two bar-length hypotheses in parallel; the
        # decoder is CPU-only in madmom-infer by design.
        self.dbn = DBNDownBeatTrackingProcessor(
            beats_per_bar=BEATS_PER_BAR,
            fps=FPS,
            num_threads=2,
        )
        elapsed = time.time() - started
        print(f"[sow-downbeats] models loaded in {elapsed:.1f}s", flush=True)

    @modal.method()
    def detect_beats(self, audio_bytes: bytes, filename: str, request_id: str) -> dict:
        """madmom downbeat detection on the torch backend (no allin1, no demix).

        Returns the detection payload the beat-grid cache persists:
        source/beats/downbeats/detected_at/madmom_params.
        """
        import pathlib
        import tempfile
        import time
        import traceback
        from datetime import datetime, timezone

        started = time.time()
        print(
            f"[sow-downbeats][{request_id}] detect_beats start: {len(audio_bytes)} bytes "
            f"({filename})",
            flush=True,
        )
        try:
            with tempfile.TemporaryDirectory() as tmp:
                tmp_path = pathlib.Path(tmp)
                input_path = tmp_path / f"input{pathlib.Path(filename).suffix or '.wav'}"
                input_path.write_bytes(audio_bytes)

                grid = self.dbn(self.rnn(str(_mono_44k_wav(input_path, tmp_path))))

                downbeat_times = grid[grid[:, 1] == 1][:, 0]
                payload = {
                    "source": "madmom",
                    "beats": grid.tolist(),
                    "downbeats": sorted(downbeat_times.tolist()),
                    "detected_at": datetime.now(timezone.utc).isoformat(),
                    "madmom_params": {"beats_per_bar": BEATS_PER_BAR, "fps": FPS},
                }

            print(
                f"[sow-downbeats][{request_id}] detect_beats done in "
                f"{time.time() - started:.2f}s (downbeats={len(payload['downbeats'])})",
                flush=True,
            )
            return payload
        except Exception:
            print(
                f"[sow-downbeats][{request_id}] detect_beats failed after "
                f"{time.time() - started:.2f}s:\n{traceback.format_exc()}",
                flush=True,
            )
            raise
