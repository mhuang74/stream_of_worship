"""Modal app hosting Qwen3-ForcedAligner for sow-analysis.

Deploy (from ops/analysis-service, with MODAL_TOKEN_ID/MODAL_TOKEN_SECRET exported):

    uv run --no-project --with 'modal>=1.2.6,<2' modal deploy modal_apps/forced_aligner_app.py
"""

import modal

APP_NAME = "sow-forced-aligner"
VOLUME_NAME = "sow-forced-aligner-models"
MODEL_MOUNT = "/models"
MODEL_ID = "Qwen/Qwen3-ForcedAligner-0.6B"

# PyPI torch wheels ship the CUDA 12 runtime (cublas/cudnn), so no nvidia base image is needed.
image = (
    modal.Image.debian_slim(python_version="3.11")
    .uv_pip_install("qwen-asr==0.0.6", "torch==2.10.0")
    .env({"HF_HOME": MODEL_MOUNT})
)
model_volume = modal.Volume.from_name(VOLUME_NAME, create_if_missing=True)

app = modal.App(APP_NAME)


@app.cls(
    image=image,
    gpu="L4",
    timeout=1800,  # per-input kill switch; surfaces as FunctionTimeoutError (retryable client-side)
    startup_timeout=900,  # weights download (~1.2 GB) + load into the volume on first cold start
    max_containers=2,
    scaledown_window=300,
    volumes={MODEL_MOUNT: model_volume},
    # NOTE: deliberately NO retries= — retries are owned by the client
    # (services/modal_forced_aligner_client.py) so the layers cannot multiply into N x M GPU runs.
)
class ForcedAligner:
    @modal.enter()
    def load_model(self) -> None:
        import time
        from pathlib import Path

        import torch
        from qwen_asr import Qwen3ForcedAligner

        marker = Path(MODEL_MOUNT) / ".downloaded-qwen3-forced-aligner-0.6b"
        needs_download = not marker.exists()
        started = time.time()
        self.aligner = Qwen3ForcedAligner.from_pretrained(
            MODEL_ID, dtype=torch.bfloat16, device_map="cuda:0"
        )
        load_s = time.time() - started
        # HF download happens lazily inside from_pretrained; the marker is written after the
        # load succeeds, so it proves the volume holds usable weights.
        print(
            f"[sow-forced-aligner] model={MODEL_ID} loaded in {load_s:.1f}s "
            f"(fresh_download={needs_download})",
            flush=True,
        )
        if not marker.exists():
            marker.touch()
            model_volume.commit()

    @modal.method()
    def align(
        self, audio_wav: bytes, lyrics_text: str, language: str, request_id: str
    ) -> list[dict]:
        import tempfile
        import time
        import traceback
        from pathlib import Path

        started = time.time()
        print(
            f"[sow-forced-aligner][{request_id}] start: {len(audio_wav)} bytes "
            f"(lang={language}, lyrics_chars={len(lyrics_text)})",
            flush=True,
        )
        try:
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "audio.wav"
                path.write_bytes(audio_wav)
                results = self.aligner.align(
                    audio=str(path), text=lyrics_text, language=language
                )
            segments = [
                {
                    "start": float(seg.start_time),
                    "end": float(seg.end_time),
                    "text": seg.text.strip(),
                }
                for segment_list in results
                for seg in segment_list
                if seg.text.strip()
            ]
        except Exception:
            # Without this, an in-container OOM/CUDA failure leaves nothing in
            # `modal app logs` — the client only sees a bare INTERNAL_FAILURE string.
            print(
                f"[sow-forced-aligner][{request_id}] alignment failed after "
                f"{time.time() - started:.2f}s:\n{traceback.format_exc()}",
                flush=True,
            )
            raise
        print(
            f"[sow-forced-aligner][{request_id}] aligned {len(segments)} segments in "
            f"{time.time() - started:.2f}s",
            flush=True,
        )
        return segments
