"""Modal app hosting faster-whisper transcription for sow-analysis.

Deploy (from ops/analysis-service, with MODAL_TOKEN_ID/MODAL_TOKEN_SECRET exported):

    uv run --no-project --with 'modal>=1.2.6,<2' modal deploy modal_apps/whisper_app.py
"""

import modal

APP_NAME = "sow-whisper"
VOLUME_NAME = "sow-whisper-models"
MODEL_MOUNT = "/models"

# CTranslate2 >=4.5 (faster-whisper 1.2.1) loads cuBLAS + cuDNN 9 from the system,
# so the container must start from a CUDA 12 image that ships cuDNN.
image = (
    modal.Image.from_registry(
        "nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04", add_python="3.11"
    )
    .entrypoint([])
    .uv_pip_install("faster-whisper==1.2.1")
)
model_volume = modal.Volume.from_name(VOLUME_NAME, create_if_missing=True)

app = modal.App(APP_NAME)


@app.cls(
    image=image,
    gpu="L4",
    timeout=1800,  # per-input kill switch; surfaces as FunctionTimeoutError (retryable client-side)
    startup_timeout=900,  # model download + load into GPU on first cold start
    max_containers=2,  # SOW_LRC_MAX_CONCURRENT=2 caps concurrent inputs; more is idle-billed GPU
    scaledown_window=300,
    volumes={MODEL_MOUNT: model_volume},
    # NOTE: deliberately NO retries= here. Retries are owned by the client
    # (modal_whisper_client.py) so the two layers cannot multiply into N x M GPU runs.
    # See "Retry ownership" in services/modal_whisper_client.py.
)
class WhisperTranscriber:
    model_name: str = modal.parameter(default="large-v3")

    @modal.enter()
    def load_model(self) -> None:
        import time
        from pathlib import Path

        from faster_whisper import WhisperModel

        marker = Path(MODEL_MOUNT) / f".downloaded-{self.model_name}"
        needs_download = not marker.exists()
        started = time.time()
        self.model = WhisperModel(
            self.model_name,
            device="cuda",
            compute_type="float16",
            download_root=MODEL_MOUNT,
        )
        load_s = time.time() - started
        # hf download happens lazily inside WhisperModel; marker written after load proves
        # the weights are usable. Print goes to `modal app logs` (observable cold-start health).
        print(
            f"[sow-whisper] model={self.model_name} loaded in {load_s:.1f}s "
            f"(fresh_download={needs_download})",
            flush=True,
        )
        if not marker.exists():
            marker.touch()
            model_volume.commit()

    @modal.method()
    def transcribe(
        self,
        audio_bytes: bytes,
        request_id: str,
        language: str,
        initial_prompt: str,
        vad_filter: bool,
        condition_on_previous_text: bool,
        beam_size: int = 5,
    ) -> dict:
        import io
        import time
        import traceback

        started = time.time()
        print(
            f"[sow-whisper][{request_id}] start: {len(audio_bytes)} bytes "
            f"(model={self.model_name}, lang={language})",
            flush=True,
        )
        try:
            segments, info = self.model.transcribe(
                io.BytesIO(audio_bytes),
                language=language,
                beam_size=beam_size,
                vad_filter=vad_filter,
                condition_on_previous_text=condition_on_previous_text,
                initial_prompt=initial_prompt,
            )
            phrases = [
                {
                    "text": segment.text.strip(),
                    "start": float(segment.start),
                    "end": float(segment.end),
                    "no_speech_prob": float(segment.no_speech_prob),
                }
                for segment in segments  # generator: transcription runs during iteration
            ]
        except Exception:
            # Without this, an in-container OOM/cuDNN/CUDA failure leaves nothing in
            # `modal app logs` — the client only sees INTERNAL_FAILURE with a bare string.
            print(
                f"[sow-whisper][{request_id}] transcription failed after "
                f"{time.time() - started:.2f}s:\n{traceback.format_exc()}",
                flush=True,
            )
            raise
        print(
            f"[sow-whisper][{request_id}] transcribed {len(audio_bytes)} bytes -> "
            f"{len(phrases)} phrases in {time.time() - started:.2f}s",
            flush=True,
        )
        return {
            "phrases": phrases,
            "language": info.language,
            "language_probability": float(info.language_probability),
        }
