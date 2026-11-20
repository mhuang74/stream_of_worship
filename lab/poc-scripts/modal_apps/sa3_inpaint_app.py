"""Modal app hosting Stable Audio 3 `small-music` inpainting for the seam-fill POC.

Given source audio that already contains the real recordings on both sides of a
seam plus digital silence where the seam is, `inpaint` regenerates only the
silent span, conditioned on the surrounding music
(`StableAudioModel.generate(inpaint_audio=..., inpaint_mask_start_seconds=...,
inpaint_mask_end_seconds=...)`; mask 1 = preserve, 0 = regenerate).

Deploy (from the repo root, with MODAL_TOKEN_ID / MODAL_TOKEN_SECRET exported):

    uv run --no-project --with 'modal>=1.2.6,<2' \\
        modal deploy lab/poc-scripts/modal_apps/sa3_inpaint_app.py

Two classes serve the same method: `Sa3Inpaint` on whatever GPU Modal has free
(a pinned A10G request sat Pending in this workspace while other classes
allocated; 433M params fit any 16 GB card) and `Sa3InpaintCpu`, a CPU fallback
that loads fp32 — fp16 matmuls on CPU fail or crawl, which is why
`StableAudioModel.from_pretrained` also forces `model_half=False` without CUDA.

Weights: the upstream checkpoint is gated on Hugging Face, so the volume is
seeded from the unauthenticated ModelScope mirror of the same repo
(`stabilityai/stable-audio-3-small-music`). `model_config.json` there names the
text conditioner by HF `repo_id` + `subfolder`; at boot the prompt config is
rewritten to point at the local copy (`model_path` = volume dir, `subfolder`
kept — `T5GemmaConditioner` does `load_from = model_path or repo_id or
model_name` and passes `subfolder` to `from_pretrained`, which resolves a local
directory). That is the only config edit; the diffusion config is used verbatim.

Image: the upstream repo is not on PyPI, so it is cloned at a pinned commit and
installed. Its own deps pin torch/torchaudio 2.7.1 and transformers >= 5.8;
Flash Attention is optional (SDPA fallback) and only `medium` needs it, so it is
not installed.
"""

import json

import modal

APP_NAME = "sow-sa3-poc"
VOLUME_NAME = "sow-sa3-models"
MODEL_MOUNT = "/models"
MODEL_DIR = "/models/sa3-small-music"
REPO_DIR = "/opt/stable-audio-3"
REPO_COMMIT = "790793b89ddceea612be9ce42ff6830d18f6fa57"
MIRROR = "https://modelscope.cn/models/stabilityai/stable-audio-3-small-music/resolve/master"
FILES = (
    "model.safetensors",
    "model_config.json",
    "t5gemma-b-b-ul2/config.json",
    "t5gemma-b-b-ul2/model.safetensors",
    "t5gemma-b-b-ul2/tokenizer.json",
    "t5gemma-b-b-ul2/tokenizer_config.json",
    "t5gemma-b-b-ul2/special_tokens_map.json",
    "t5gemma-b-b-ul2/tokenizer.model",
    "t5gemma-b-b-ul2/generation_config.json",
)
REQUIRED_REQUEST_KEYS = (
    "prompt",
    "seed",
    "steps",
    "start_seconds",
    "end_seconds",
    "duration_seconds",
)
SAMPLE_RATE = 44100
MIN_OUTPUT_SECONDS = 0.25
MODEL_NAME = "small-music"

image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("git", "ffmpeg", "libsndfile1", "curl")
    .run_commands(
        f"git clone https://github.com/Stability-AI/stable-audio-3.git {REPO_DIR}",
        f"cd {REPO_DIR} && git checkout {REPO_COMMIT}",
        f"cd {REPO_DIR} && pip install --no-cache-dir .",
        'python -c "import torch, transformers, stable_audio_3; '
        "print('torch', torch.__version__, 'cuda', torch.version.cuda, "
        "'transformers', transformers.__version__)\"",
    )
    .env({"TOKENIZERS_PARALLELISM": "false"})
)

model_volume = modal.Volume.from_name(VOLUME_NAME, create_if_missing=True)

app = modal.App(APP_NAME)

CLASS_KWARGS = dict(
    image=image,
    cpu=8.0,
    memory=16384,
    timeout=900,
    startup_timeout=3600,  # a first boot includes the 3.5 GB weight download into the volume
    scaledown_window=300,
    max_containers=1,
    volumes={MODEL_MOUNT: model_volume},
    # NOTE: deliberately NO retries= — the client owns retries so runs cannot
    # multiply into N x M attempts.
)


def _file_is_intact(path) -> bool:
    """True when *path* parses as the file type it claims to be.

    A killed container can leave a truncated checkpoint on the volume — that is how
    `/models/sa3-small-music/model.safetensors` ended up half-written and every boot
    then failed inside `load_file` with "incomplete metadata". Existence is not
    enough; the safetensors header and the JSON files are parsed here.
    """
    from pathlib import Path

    path = Path(path)
    if not path.is_file() or path.stat().st_size == 0:
        return False
    try:
        if path.suffix == ".safetensors":
            from safetensors import safe_open

            with safe_open(str(path), framework="pt") as handle:
                return len(handle.keys()) > 0
        if path.suffix == ".json":
            json.loads(path.read_text())
            return True
    except Exception as exc:  # noqa: BLE001 — any parse failure means "not usable"
        print(f"[sow-sa3] {path.name} is unusable: {exc}", flush=True)
        return False
    return path.stat().st_size > 1024


def _bootstrap(handler, device: str, model_half: bool) -> None:
    """Seed the volume, point the text conditioner at it, load and verify the model."""
    import contextlib
    import io
    import subprocess
    import sys
    import time
    from pathlib import Path

    started = time.time()
    model_dir = Path(MODEL_DIR)
    model_dir.mkdir(parents=True, exist_ok=True)

    missing = [rel for rel in FILES if not _file_is_intact(model_dir / rel)]
    if missing:
        print(f"[sow-sa3] downloading {len(missing)} files into {MODEL_DIR}", flush=True)
        for rel in missing:
            dest = model_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            partial = dest.with_suffix(dest.suffix + ".part")
            print(f"[sow-sa3]   {rel}", flush=True)
            # Download beside the target and rename only on success: a killed
            # container must never leave a file that looks complete.
            subprocess.run(
                [
                    "curl",
                    "-L",
                    "--fail",
                    "--retry",
                    "5",
                    "--retry-delay",
                    "2",
                    "-o",
                    str(partial),
                    f"{MIRROR}/{rel}",
                ],
                check=True,
            )
            partial.replace(dest)
            if not _file_is_intact(dest):
                raise RuntimeError(f"downloaded {rel} is not intact")
        model_volume.commit()
    else:
        print(f"[sow-sa3] weights already on volume at {MODEL_DIR}", flush=True)

    # Point the text conditioner at the local copy. Always rewritten so a changed
    # constant here is picked up without a volume reset.
    cfg = json.loads((model_dir / "model_config.json").read_text())
    patched = 0
    for entry in cfg["model"]["conditioning"]["configs"]:
        if entry.get("id") == "prompt":
            entry["config"]["model_path"] = MODEL_DIR
            patched += 1
    if not patched:
        raise RuntimeError("model_config.json has no prompt conditioning entry")
    (model_dir / "model_config.local.json").write_text(
        json.dumps(cfg, indent=2, ensure_ascii=False) + "\n"
    )

    sys.path.insert(0, REPO_DIR)
    from stable_audio_3.loading_utils import load_diffusion_cond, remap_state_dict_keys
    from stable_audio_3.model import StableAudioModel

    # `copy_state_dict` prints one line per unmatched key and then calls
    # `load_state_dict(strict=False)`, so a naming mismatch loads nothing and still
    # "succeeds" — a randomly initialised model would happily return
    # plausible-looking noise that passes an RMS/peak/duration check. Capture those
    # lines and, independently, prove every model parameter is covered by a
    # same-shaped checkpoint tensor before serving anything.
    skipped = io.StringIO()
    with contextlib.redirect_stdout(skipped):
        model = load_diffusion_cond(
            cfg,
            f"{MODEL_DIR}/model.safetensors",
            device=device,
            model_half=model_half,
        )
    print(skipped.getvalue(), flush=True)

    from safetensors import safe_open

    model_sd = model.state_dict()
    with safe_open(f"{MODEL_DIR}/model.safetensors", framework="pt") as handle:
        covered: set[str] = set()
        shape_mismatch: list[str] = []
        # `remap_state_dict_keys` returns {remapped_key: value}, so it has to be
        # driven one file key at a time to keep the original name for the shape
        # lookup.
        for file_key in handle.keys():
            (target,) = remap_state_dict_keys({file_key: None}, model_sd).keys()
            if target not in model_sd:
                continue
            if tuple(handle.get_slice(file_key).get_shape()) != tuple(model_sd[target].shape):
                shape_mismatch.append(target)
                continue
            covered.add(target)
    uncovered = sorted(key for key in model_sd if key not in covered)
    if shape_mismatch or uncovered:
        raise RuntimeError(
            "checkpoint/model mismatch: "
            f"{len(shape_mismatch)} shape mismatches {shape_mismatch[:5]}, "
            f"{len(uncovered)} uncovered params {uncovered[:5]}, "
            f"{len(model_sd)} model params"
        )
    print(
        f"[sow-sa3] verified {len(covered)}/{len(model_sd)} model tensors from the checkpoint",
        flush=True,
    )

    handler.sa = StableAudioModel(model, cfg, device, model_half)
    handler.device = device
    print(
        f"[sow-sa3] ready in {time.time() - started:.1f}s "
        f"(model={MODEL_NAME}, device={device}, half={model_half}, "
        f"sr={handler.sa.model.sample_rate})",
        flush=True,
    )


def _inpaint(handler, audio_bytes: bytes, filename: str, request: dict) -> dict:
    """Regenerate `request["start_seconds"]`..`["end_seconds"]` of the given audio.

    Returns ``{"audio": <flac bytes>, "meta": {...}}``. Raises ValueError on a
    malformed request (before any model work) and RuntimeError when the model
    produces no usable audio.
    """
    import tempfile
    import time
    from pathlib import Path

    import numpy as np
    import soundfile as sf
    import torch

    if not audio_bytes:
        raise ValueError("audio_bytes is empty")
    missing = [key for key in REQUIRED_REQUEST_KEYS if key not in request]
    if missing:
        raise ValueError(f"request is missing keys: {missing}")
    start = float(request["start_seconds"])
    end = float(request["end_seconds"])
    duration = float(request["duration_seconds"])
    if start < 0.0 or end <= start or end > duration + 1e-6:
        raise ValueError(
            f"invalid mask [{start}, {end}] for duration {duration} "
            "(need 0 <= start < end <= duration)"
        )
    seed = int(request["seed"])
    steps = int(request["steps"])
    prompt = str(request["prompt"])
    started = time.time()

    with tempfile.TemporaryDirectory(prefix="sa3-inpaint-") as tmp:
        src_path = Path(tmp) / f"src{Path(filename).suffix or '.flac'}"
        src_path.write_bytes(audio_bytes)
        samples, sample_rate = sf.read(str(src_path), dtype="float32", always_2d=True)
        if sample_rate != SAMPLE_RATE:
            raise ValueError(f"source decoded at {sample_rate} Hz, expected {SAMPLE_RATE}")
        if samples.shape[1] != 2:
            raise ValueError(f"source has {samples.shape[1]} channels, expected 2")

        wave = torch.from_numpy(np.ascontiguousarray(samples.T, dtype="float32"))
        print(
            f"[sow-sa3] inpaint {start:.3f}-{end:.3f}s of {wave.shape[1] / SAMPLE_RATE:.3f}s "
            f"seed={seed} steps={steps} prompt={prompt[:60]!r}",
            flush=True,
        )
        out = handler.sa.generate(
            inpaint_audio=(SAMPLE_RATE, wave),
            inpaint_mask_start_seconds=start,
            inpaint_mask_end_seconds=end,
            prompt=prompt,
            duration=duration,
            duration_padding_sec=0.0,
            steps=steps,
            seed=seed,
            batch_size=1,
            truncate_output_to_duration=True,
        )

        if out.dim() != 3 or out.shape[0] < 1:
            raise RuntimeError(f"unexpected output shape {tuple(out.shape)}")
        audio = out[0].transpose(0, 1).float().cpu().numpy()
        if audio.shape[0] < round(MIN_OUTPUT_SECONDS * SAMPLE_RATE):
            raise RuntimeError(
                f"generated {audio.shape[0]} samples, fewer than "
                f"{round(MIN_OUTPUT_SECONDS * SAMPLE_RATE)}"
            )
        audio = np.ascontiguousarray(np.clip(audio, -1.0, 1.0), dtype=np.float32)
        dest = Path(tmp) / "out.flac"
        sf.write(str(dest), audio, SAMPLE_RATE, format="FLAC", subtype="PCM_16")

        elapsed = round(time.time() - started, 3)
        print(
            f"[sow-sa3] wrote {audio.shape[0]} samples ({audio.shape[0] / SAMPLE_RATE:.3f}s) "
            f"in {elapsed:.1f}s",
            flush=True,
        )
        return {
            "audio": dest.read_bytes(),
            "meta": {
                "model": MODEL_NAME,
                "device": handler.device,
                "seed": seed,
                "steps": steps,
                "prompt": prompt,
                "start_seconds": start,
                "end_seconds": end,
                "duration_seconds": duration,
                "sample_rate": SAMPLE_RATE,
                "output_samples": int(audio.shape[0]),
                "elapsed_seconds": elapsed,
            },
        }


@app.cls(gpu="any", **CLASS_KWARGS)
class Sa3Inpaint:
    @modal.enter()
    def bootstrap(self) -> None:
        _bootstrap(self, "cuda", True)

    @modal.method()
    def inpaint(self, audio_bytes: bytes, filename: str, request: dict) -> dict:
        """Regenerate the masked span of `audio_bytes` on the first available GPU."""
        return _inpaint(self, audio_bytes, filename, request)


@app.cls(gpu=None, **CLASS_KWARGS)
class Sa3InpaintCpu:
    @modal.enter()
    def bootstrap(self) -> None:
        _bootstrap(self, "cpu", False)

    @modal.method()
    def inpaint(self, audio_bytes: bytes, filename: str, request: dict) -> dict:
        """Regenerate the masked span of `audio_bytes` on CPU (fp32)."""
        return _inpaint(self, audio_bytes, filename, request)
