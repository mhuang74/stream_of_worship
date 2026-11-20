"""Modal app hosting ACE-Step 1.5 repaint for the seam-inpaint POC.

Given source audio that already contains the real recordings on both sides of a
seam plus digital silence where the seam is, `repaint` regenerates only the
silent span, conditioned on the surrounding music (ACE-Step `task_type="repaint"`,
`chunk_mask_mode="explicit"`).

Deploy (from the repo root, with MODAL_TOKEN_ID / MODAL_TOKEN_SECRET exported):

    uv run --no-project --with 'modal>=1.2.6,<2' \\
        modal deploy lab/poc-scripts/modal_apps/acestep_repaint_app.py

Image install path: the upstream repo is cloned at a pinned commit and its own
`uv.lock` is exported to a requirements file that is installed into the *system*
interpreter (`uv pip install --system`). A `uv sync` virtualenv would be
invisible to the interpreter Modal runs, so the model stack has to land in the
image's own Python; `acestep` itself is imported from the source clone through
sys.path. This is the `uv export` variant of the fallback install path the POC
plan allows.
"""

import modal

APP_NAME = "sow-acestep-poc"
VOLUME_NAME = "sow-acestep-models"
MODEL_MOUNT = "/models"
REPO_DIR = "/opt/ace-step"
REPO_COMMIT = "ca1e85fe9430179831e6bc6be790c332190a3866"
DIT_CONFIG = "acestep-v15-turbo"
LM_MODEL = "acestep-5Hz-lm-0.6B"
LM_MODEL_FALLBACK = "acestep-5Hz-lm-1.7B"
PYTORCH_INDEX = "https://download.pytorch.org/whl/cu128"
# Upstream docs/INFERENCE.md: turbo models do not auto-correct `shift`, and 3.0
# is the recommended value for them. Kept internal so the client contract (the
# `request` dict) stays fixed.
TURBO_SHIFT = 3.0
REQUIRED_REQUEST_KEYS = (
    "caption",
    "lyrics",
    "instrumental",
    "bpm",
    "keyscale",
    "duration",
    "seed",
    "repaint_start",
    "repaint_end",
)

image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("git", "ffmpeg", "libsndfile1", "build-essential")
    .run_commands(
        f"git clone https://github.com/ACE-Step/ACE-Step-1.5.git {REPO_DIR}",
        f"cd {REPO_DIR} && git checkout {REPO_COMMIT}",
        "pip install 'uv==0.9.27'",
        # Export the repo's own lockfile (hashes included) and install it into the
        # interpreter Modal runs. Named indexes are not emitted by `uv export`, so
        # the CUDA wheel index is passed explicitly; `unsafe-best-match` is required
        # because `torch==2.10.0+cu128` exists only on that index while everything
        # else lives on PyPI. Hash verification is off because triton 3.6.0 is
        # published by both indexes as different artifacts (the lock's hashes are
        # PyPI's), which would otherwise abort the build.
        f"cd {REPO_DIR} && uv export --frozen --no-dev --no-emit-project "
        "--format requirements.txt -o /tmp/ace-requirements.txt",
        # The exported file references the local nano-vllm path relative to the repo,
        # so the install has to run from there.
        f"cd {REPO_DIR} && uv pip install --system -r /tmp/ace-requirements.txt "
        f"--index {PYTORCH_INDEX} --index-strategy unsafe-best-match --no-verify-hashes",
        "python -c \"import torch, transformers; print('torch', torch.__version__, "
        "'cuda', torch.version.cuda, 'transformers', transformers.__version__)\"",
    )
    .env(
        {
            # `initialize_service` prefers this over `<project_root>/checkpoints`,
            # so the downloaded weights on the volume are the ones loaded.
            "ACESTEP_CHECKPOINTS_DIR": MODEL_MOUNT,
            "ACESTEP_PROJECT_ROOT": REPO_DIR,
            "TOKENIZERS_PARALLELISM": "false",
        }
    )
)

model_volume = modal.Volume.from_name(VOLUME_NAME, create_if_missing=True)

app = modal.App(APP_NAME)


@app.cls(
    image=image,
    # A10G, not L4: L4 requests in this workspace queue indefinitely (backlog with
    # zero runners; T4/A10G/A100 all allocate instantly). A10G is the same 24 GB
    # class the POC sized the model stack for.
    gpu="A10G",
    cpu=8.0,
    memory=32768,
    timeout=3600,  # first call includes the 10 GB weight download into the volume
    startup_timeout=7200,
    scaledown_window=600,
    max_containers=1,
    volumes={MODEL_MOUNT: model_volume},
    # NOTE: deliberately NO retries= — the client owns retries so GPU runs cannot
    # multiply into N x M attempts.
)
class AceStepRepaint:
    @modal.enter()
    def bootstrap(self) -> None:
        import subprocess
        import sys
        import time
        from pathlib import Path

        ckpt = Path(MODEL_MOUNT)
        started = time.time()
        # `acestep` is imported from the source clone (it is not pip-installed), so
        # the path has to exist before the downloader subprocess imports it.
        sys.path.insert(0, REPO_DIR)

        # The LM is never used by repaint (thinking and every use_cot_* flag are off,
        # and upstream skips the LM for repaint regardless); it is initialized only
        # because generate_music takes a handler.
        if not (ckpt / DIT_CONFIG).is_dir():
            print(f"[sow-acestep] downloading main bundle into {MODEL_MOUNT}", flush=True)
            subprocess.run(
                [sys.executable, "-m", "acestep.model_downloader", "--dir", MODEL_MOUNT],
                check=True,
                cwd=REPO_DIR,
            )
            model_volume.commit()

        if not (ckpt / LM_MODEL).is_dir():
            print(f"[sow-acestep] downloading {LM_MODEL}", flush=True)
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "acestep.model_downloader",
                    "--dir",
                    MODEL_MOUNT,
                    "--model",
                    LM_MODEL,
                    "--skip-main",
                ],
                check=True,
                cwd=REPO_DIR,
            )
            model_volume.commit()

        from acestep.handler import AceStepHandler
        from acestep.inference import GenerationConfig, GenerationParams, generate_music
        from acestep.llm_inference import LLMHandler

        self.generation_params = GenerationParams
        self.generation_config = GenerationConfig
        self.generate_music = generate_music

        dit = AceStepHandler()
        status, ok = dit.initialize_service(
            project_root=REPO_DIR, config_path=DIT_CONFIG, device="cuda"
        )
        if not ok:
            raise RuntimeError(f"ACE-Step DiT init failed: {status}")
        self.dit = dit

        llm = LLMHandler()
        self.lm_model = None
        for candidate in (LM_MODEL, LM_MODEL_FALLBACK):
            if not (ckpt / candidate).is_dir():
                continue
            status, ok = llm.initialize(
                checkpoint_dir=MODEL_MOUNT,
                lm_model_path=candidate,
                backend="pt",
                device="cuda",
            )
            if ok:
                self.lm_model = candidate
                break
            print(f"[sow-acestep] LM {candidate} init failed: {status}", flush=True)
        if self.lm_model is None:
            raise RuntimeError("no 5Hz LM could be initialized (repaint needs a handler)")

        self.llm = llm
        print(
            f"[sow-acestep] ready in {time.time() - started:.1f}s "
            f"(dit={DIT_CONFIG}, lm={self.lm_model})",
            flush=True,
        )

    @modal.method()
    def repaint(self, audio_bytes: bytes, filename: str, request: dict) -> dict:
        """Repaint `request["repaint_start"]`..`["repaint_end"]` of the given audio.

        Returns ``{"audio": <flac bytes>, "meta": {...}}``. Raises ValueError on a
        malformed request (before any GPU work) and RuntimeError when the model
        reports failure or produces no audio.
        """
        import tempfile
        import time
        from pathlib import Path

        if not audio_bytes:
            raise ValueError("audio_bytes is empty")
        missing = [key for key in REQUIRED_REQUEST_KEYS if key not in request]
        if missing:
            raise ValueError(f"request is missing keys: {missing}")
        start = float(request["repaint_start"])
        end = float(request["repaint_end"])
        if end <= start:
            raise ValueError(f"repaint_end ({end}) must be greater than repaint_start ({start})")

        seed = int(request["seed"])
        started = time.time()

        with tempfile.TemporaryDirectory(prefix="acestep-repaint-") as tmp:
            src_path = Path(tmp) / f"src{Path(filename).suffix or '.flac'}"
            src_path.write_bytes(audio_bytes)
            save_dir = Path(tmp) / "out"
            save_dir.mkdir()

            params = self.generation_params(
                task_type="repaint",
                src_audio=str(src_path),
                repainting_start=start,
                repainting_end=end,
                chunk_mask_mode="explicit",
                caption=request["caption"],
                lyrics=request["lyrics"],
                instrumental=bool(request["instrumental"]),
                vocal_language=str(request.get("vocal_language", "en")),
                bpm=int(request["bpm"]) if request["bpm"] else None,
                keyscale=str(request["keyscale"]),
                duration=float(request["duration"]),
                seed=seed,
                shift=TURBO_SHIFT,
                thinking=False,
                use_cot_metas=False,
                use_cot_caption=False,
                use_cot_language=False,
            )
            config = self.generation_config(
                batch_size=1,
                use_random_seed=False,
                seeds=[seed],
                audio_format="flac",
            )
            result = self.generate_music(self.dit, self.llm, params, config, save_dir=str(save_dir))
            if not result.success:
                raise RuntimeError(result.error or "generate_music failed without an error")
            if not result.audios:
                raise RuntimeError("generate_music returned no audio")

            first = result.audios[0]
            audio_path = Path(first["path"])
            if not audio_path.is_file():
                raise RuntimeError(f"generated audio path does not exist: {audio_path}")

            print(
                f"[sow-acestep] repaint {start:.2f}-{end:.2f}s seed={seed} "
                f"caption={request['caption'][:60]!r} in {time.time() - started:.1f}s",
                flush=True,
            )
            return {
                "audio": audio_path.read_bytes(),
                "meta": {
                    "seed": int(first.get("params", {}).get("seed", seed)),
                    "sample_rate": first.get("sample_rate"),
                    "duration_requested": float(request["duration"]),
                    "generation_info": result.status_message,
                    "dit_model": DIT_CONFIG,
                    "lm_model": self.lm_model,
                    "shift": TURBO_SHIFT,
                    "elapsed_seconds": round(time.time() - started, 3),
                },
            }
