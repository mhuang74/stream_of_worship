"""Modal app hosting audio-separator stem separation for sow-analysis.

This is the failover backend behind MVSEP: Stage 1 (Mel-Band Roformer vocal
separation) and Stage 2 (UVR-De-Echo de-reverb) run on a GPU container instead
of inside the analysis service container.

Deploy (from ops/analysis-service, with MODAL_TOKEN_ID/MODAL_TOKEN_SECRET exported):

    uv run --no-project --with 'modal>=1.2.6,<2' modal deploy modal_apps/stem_separator_app.py
"""

from pathlib import Path
from typing import Optional

import modal

APP_NAME = "sow-stems"
VOLUME_NAME = "sow-stems-models"
MODEL_MOUNT = "/models"
DEFAULT_VOCAL_MODEL = "model_mel_band_roformer_ep_3005_sdr_11.4360.ckpt"
DEFAULT_DEREVERB_MODEL = "UVR-De-Echo-Normal.pth"

# audio-separator shells out to ffmpeg (it raises if missing) and needs libsndfile for
# soundfile/resampy. PyPI torch wheels ship the CUDA runtime, so no nvidia base image is
# needed. The [cpu] extra only supplies onnxruntime, which audio_separator imports at
# module scope; both models used here (Mel-Band Roformer = MDXC, UVR-De-Echo = VR) run on
# torch/CUDA (verified with Separator(...).list_supported_model_files()).
image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("ffmpeg", "libsndfile1")
    .uv_pip_install("audio-separator[cpu]==0.44.1")
)
model_volume = modal.Volume.from_name(VOLUME_NAME, create_if_missing=True)

app = modal.App(APP_NAME)


@app.cls(
    image=image,
    gpu="L4",
    cpu=4.0,
    memory=16384,  # MiB; roformer separation peaks well below 16 GiB
    timeout=1800,  # per-input kill switch -> FunctionTimeoutError (retryable client-side)
    startup_timeout=1800,  # first cold start downloads ~1.1 GB of weights into the volume
    max_containers=2,
    scaledown_window=300,
    volumes={MODEL_MOUNT: model_volume},
    # NOTE: deliberately NO retries= — retries are owned by the client
    # (services/modal_stem_separator_client.py) so the layers cannot multiply into N x M GPU runs.
)
class StemSeparator:
    # Model filenames are class parameters so the client can select them from settings
    # (SOW_VOCAL_SEPARATION_MODEL / SOW_DEREVERB_MODEL) without a redeploy.
    vocal_model: str = modal.parameter(default=DEFAULT_VOCAL_MODEL)
    dereverb_model: str = modal.parameter(default=DEFAULT_DEREVERB_MODEL)
    output_format: str = modal.parameter(default="FLAC")

    @modal.enter()
    def load_models(self) -> None:
        import tempfile
        import time
        from pathlib import Path

        from audio_separator.separator import Separator

        model_dir = Path(MODEL_MOUNT)
        markers = {
            name: model_dir / f".downloaded-{name}"
            for name in (self.vocal_model, self.dereverb_model)
        }
        fresh_download = any(not marker.exists() for marker in markers.values())

        started = time.time()
        self.vocal_separator = Separator(
            output_dir=tempfile.mkdtemp(prefix="stem-entry-"),
            model_file_dir=MODEL_MOUNT,
            output_format=self.output_format,
        )
        self.vocal_separator.load_model(model_filename=self.vocal_model)
        self.dereverb_separator = Separator(
            output_dir=tempfile.mkdtemp(prefix="stem-entry-"),
            model_file_dir=MODEL_MOUNT,
            output_format=self.output_format,
        )
        self.dereverb_separator.load_model(model_filename=self.dereverb_model)
        elapsed = time.time() - started

        # Model downloads happen lazily inside load_model(); the markers are written
        # after both loads succeed, so they prove the volume holds usable weights.
        for marker in markers.values():
            marker.touch()
        if fresh_download:
            model_volume.commit()
        print(
            f"[sow-stems] models loaded in {elapsed:.1f}s "
            f"(fresh_download={fresh_download}, vocal={self.vocal_model}, "
            f"dereverb={self.dereverb_model})",
            flush=True,
        )

    @modal.method()
    def separate(self, audio_bytes: bytes, filename: str, request_id: str) -> dict:
        """Stage 1 + Stage 2: vocals/instrumental split, then de-reverb the vocals."""
        import tempfile
        import time
        import traceback

        started = time.time()
        print(
            f"[sow-stems][{request_id}] separate start: {len(audio_bytes)} bytes "
            f"({filename}, vocal={self.vocal_model}, dereverb={self.dereverb_model})",
            flush=True,
        )
        try:
            with tempfile.TemporaryDirectory() as tmp:
                tmp_path = Path(tmp)
                input_path = tmp_path / f"input{Path(filename).suffix or '.wav'}"
                input_path.write_bytes(audio_bytes)

                stage1_dir = tmp_path / "stage1"
                stage1_dir.mkdir()
                _redirect_output(self.vocal_separator, str(stage1_dir))
                stage1_outputs = self.vocal_separator.separate(str(input_path))

                vocals_path = None
                instrumental_path = None
                for output_file in stage1_outputs:
                    output_path = Path(output_file)
                    if not output_path.is_absolute():
                        output_path = stage1_dir / output_path

                    name_lower = output_path.name.lower()
                    if "vocals" in name_lower:
                        vocals_path = output_path
                    elif "instrumental" in name_lower or "other" in name_lower:
                        instrumental_path = output_path

                if not vocals_path or not vocals_path.exists():
                    print(
                        f"[sow-stems][{request_id}] Stage 1 produced no vocals file "
                        f"({len(stage1_outputs)} output(s)) after "
                        f"{time.time() - started:.2f}s",
                        flush=True,
                    )
                    return {
                        "vocals": None,
                        "instrumental": _read(instrumental_path),
                        "vocals_dry": None,
                    }
                if not instrumental_path or not instrumental_path.exists():
                    print(
                        f"[sow-stems][{request_id}] Stage 1 produced no instrumental file",
                        flush=True,
                    )

                stage2_dir = tmp_path / "stage2"
                stage2_dir.mkdir()
                _redirect_output(self.dereverb_separator, str(stage2_dir))
                stage2_outputs = self.dereverb_separator.separate(str(vocals_path))

                dry_vocals_path = _classify_dry(stage2_outputs, stage2_dir)
                result = {
                    "vocals": _read(vocals_path),
                    "instrumental": _read(instrumental_path),
                    "vocals_dry": _read(dry_vocals_path),
                }
            print(
                f"[sow-stems][{request_id}] separate done in {time.time() - started:.2f}s "
                f"(vocals={_size(result['vocals'])}, "
                f"instrumental={_size(result['instrumental'])}, "
                f"vocals_dry={_size(result['vocals_dry'])})",
                flush=True,
            )
            return result
        except Exception:
            # Without this, an in-container OOM/CUDA failure leaves nothing in
            # `modal app logs` — the client only sees a bare INTERNAL_FAILURE string.
            print(
                f"[sow-stems][{request_id}] separate failed after "
                f"{time.time() - started:.2f}s:\n{traceback.format_exc()}",
                flush=True,
            )
            raise

    @modal.method()
    def remove_reverb(self, vocals_bytes: bytes, filename: str, request_id: str) -> dict:
        """Stage 2 only: remove echo/reverb from already-separated vocals."""
        import tempfile
        import time
        import traceback

        started = time.time()
        print(
            f"[sow-stems][{request_id}] remove_reverb start: {len(vocals_bytes)} bytes "
            f"({filename}, dereverb={self.dereverb_model})",
            flush=True,
        )
        try:
            with tempfile.TemporaryDirectory() as tmp:
                tmp_path = Path(tmp)
                input_path = tmp_path / f"vocals{Path(filename).suffix or '.wav'}"
                input_path.write_bytes(vocals_bytes)

                stage2_dir = tmp_path / "stage2"
                stage2_dir.mkdir()
                _redirect_output(self.dereverb_separator, str(stage2_dir))
                stage2_outputs = self.dereverb_separator.separate(str(input_path))

                dry_vocals_path = _classify_dry(stage2_outputs, stage2_dir)
                result = {"vocals_dry": _read(dry_vocals_path)}
            print(
                f"[sow-stems][{request_id}] remove_reverb done in "
                f"{time.time() - started:.2f}s "
                f"(vocals_dry={_size(result['vocals_dry'])})",
                flush=True,
            )
            return result
        except Exception:
            print(
                f"[sow-stems][{request_id}] remove_reverb failed after "
                f"{time.time() - started:.2f}s:\n{traceback.format_exc()}",
                flush=True,
            )
            raise


def _redirect_output(separator, output_dir: str) -> None:
    """Point the next separator.separate() call at output_dir.

    Separator.load_model() snapshots self.output_dir into the model instance's
    common params (separator.py:879), and the write paths (common_separator.py:317
    and :405) join that instance value — not Separator.output_dir — onto the stem
    name. Setting only Separator.output_dir would leave the stems in the directory
    the instance was constructed with, so both must be updated.
    """
    separator.output_dir = output_dir
    separator.model_instance.output_dir = output_dir


def _classify_dry(outputs: list[str], output_dir: Path) -> Optional[Path]:
    """Pick the de-reverb output.

    Same classification as the former in-container wrapper: UVR-De-Echo names its
    outputs "No Echo"/"dry" vs "Reverb"/"echo"; if nothing matches (and any output
    was produced), the first output is the dry stem.
    """
    dry_vocals_path: Optional[Path] = None
    for output_file in outputs:
        output_path = Path(output_file)
        if not output_path.is_absolute():
            output_path = output_dir / output_path

        name_lower = output_path.name.lower()
        if "no echo" in name_lower or "dry" in name_lower or "no_echo" in name_lower:
            dry_vocals_path = output_path

    if dry_vocals_path is None and outputs:
        dry_vocals_path = Path(outputs[0])
        if not dry_vocals_path.is_absolute():
            dry_vocals_path = output_dir / dry_vocals_path

    return dry_vocals_path


def _read(path: Optional[Path]) -> Optional[bytes]:
    """Return the file bytes, or None when the stage produced nothing."""
    if path is None or not path.exists():
        return None
    return path.read_bytes()


def _size(data: Optional[bytes]) -> int:
    return len(data) if data else 0
