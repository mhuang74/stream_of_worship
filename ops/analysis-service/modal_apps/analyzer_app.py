"""Modal app hosting full-song analysis (allin1 + Demucs + madmom) for sow-analysis.

This is the backend behind full ANALYZE jobs: BPM/beats/downbeats/sections/
embeddings (harmonix-all via allin1), 4-stem separation (allin1's internal
htdemucs demix), and madmom RNN downbeat detection — all run on a GPU container
instead of inside the analysis service container.

Stem model note: allin1's internal demix step (allin1/demix.py) runs
``python -m demucs.separate --name htdemucs`` — the stem model is hard-named
htdemucs and the produced stems ARE the analysis byproducts, so there is no
separate Demucs invocation and no stem-model parameter.

Deploy (from ops/analysis-service, with MODAL_TOKEN_ID/MODAL_TOKEN_SECRET exported):

    uv run --no-project --with 'modal>=1.2.6,<2' modal deploy modal_apps/analyzer_app.py
"""

import modal

APP_NAME = "sow-analyzer"
VOLUME_NAME = "sow-analyzer-models"
MODEL_MOUNT = "/models"

# PyPI torch wheels ship the CUDA runtime, so no nvidia base image is needed
# (same rationale as stem_separator_app.py).
#
# Pin order & pairing (ALL explicit, none inferred from allin1's METADATA —
# allin1 1.1.0 declares natten only for Darwin and leaves demucs unpinned, so a
# plain `pip install allin1` would bump torch and break the natten ABI match):
#   1. torch/torchaudio 2.6.0 from the cu126 index (NOT plain PyPI — PyPI's
#      default wheel ABI-mismatches natten's cu126 kernel; the cu126 index URL
#      keeps the exact subminor pairing).
#   2. natten 0.17.5+torch260cu126 as a direct GitHub release wheel — the ONLY
#      wheel line compatible with both (a) torch >= 2.6 and (b) allin1's
#      learned-rpb dinat:
#        - 0.20.0+ dropped na1d_qk/na1d_av/... (fused na1d/na2d/... only);
#        - 0.18.x/0.19.x never published cp311 linux cu126 wheels (the only
#          torch260-tagged builds on whl.natten.org are all 0.17.5);
#        - 0.17.5 renamed natten1dqkrpb/natten1dav/... -> na1d_qk/na1d_av/...
#          with the same positional contract plus rpb as a kwarg, so allin1's
#          dinat.py needs the rename patch below.
#      PyPI hosts sdists only, so a plain `pip install natten` would
#      source-build against nvcc (~30 min) — never do that.
#   3. allin1 1.1.0 + demucs 4.0.0 (demucs pinned: allin1 leaves it free, and
#      an unpinned demucs can bump torch past 2.6.0, breaking the natten
#      wheel's ABI pairing) + madmom.
image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("ffmpeg", "libsndfile1", "git", "build-essential")
    .pip_install(
        "torch==2.6.0",
        "torchaudio==2.6.0",
        index_url="https://download.pytorch.org/whl/cu126",
    )
    .pip_install(
        "https://github.com/SHI-Labs/NATTEN/releases/download/v0.17.5/"
        "natten-0.17.5%2Btorch260cu126-cp311-cp311-linux_x86_64.whl"
    )
    .pip_install(
        "allin1==1.1.0",
        "demucs==4.0.0",
        "madmom @ git+https://github.com/CPJKU/madmom.git",
        # demucs.subprocess shells out to ffmpeg; allin1 needs libsndfile via
        # soundfile. madmom is a source build (CPJKU publishes no Linux wheels;
        # PyPI 0.16.1 is sdist-only too) — build-essential above supplies the
        # toolchain and pip's build isolation supplies Cython/numpy at build time.
    )
    # Freeze pins inside the same layer: fail the build, not the first request.
    .run_commands(
        "python - <<'EOF'\n"
        "import torch, natten\n"
        "assert torch.__version__.startswith('2.6.0'), torch.__version__\n"
        "assert natten.__version__.startswith('0.17.5'), natten.__version__\n"
        "print('[sow-analyzer] pins OK', torch.__version__, natten.__version__)\n"
        "EOF",
        # NATTEN 0.17.5 probes torch.cuda.get_device_capability() at import time
        # (natten/utils/testing.py) with no CPU fallback, and Modal image build
        # steps default to CPU-only builders -> "Found no NVIDIA driver".
        gpu="L4",
    )
    # allin1 1.1.0's models/dinat.py imports natten1d{av,qkrpb}/natten2d{av,qkrpb} —
    # the deprecated aliases dropped in 0.17.5. Patch to na*_qk/na*_av
    # (learned-rpb checkpoint semantics preserved: rpb stays a parameter):
    #   - import line -> na1d_qk/na1d_av/na2d_qk/na2d_av
    #   - line 99 positional qkrpb call gets rpb reordered to a keyword (the
    #     na*_qk signature is (query, key, kernel_size, dilation, ..., rpb=);
    #     reassigning the attribute alone would put rpb into kernel_size).
    #   - line 109 nattendav positional order (attn, value, kernel, dilation)
    #     already matches na1d_av/na2d_av — no change.
    .run_commands(
        "python - <<'EOF'\n"
        # Locate dinat.py WITHOUT importing allin1: `import allin1` executes
        # models/dinat.py, whose stale natten1d* import raises before this patch
        # can run (chicken-and-egg). find_spec reads the path only.
        "import pathlib, importlib.util\n"
        "spec = importlib.util.find_spec('allin1')\n"
        "assert spec is not None and spec.submodule_search_locations, "
        "'allin1 not installed'\n"
        "p = pathlib.Path(list(spec.submodule_search_locations)[0]) / 'models' / 'dinat.py'\n"
        "s = p.read_text()\n"
        "s = s.replace(\n"
        "    'from natten.functional import natten1dav, natten1dqkrpb, natten2dav, natten2dqkrpb',\n"
        "    'from natten.functional import na1d_av, na1d_qk, na2d_av, na2d_qk',\n"
        ")\n"
        "s = s.replace(\n"
        "    'attention_scores = self.nattendqkrpb(query_layer, key_layer, self.rpb, '\n"
        "    'self.kernel_size, self.dilation)',\n"
        "    'attention_scores = self.nattendqkrpb(query_layer, key_layer, '\n"
        "    'self.kernel_size, self.dilation, rpb=self.rpb)',\n"
        ")\n"
        "s = s.replace('self.nattendqkrpb = natten1dqkrpb', 'self.nattendqkrpb = na1d_qk')\n"
        "s = s.replace('self.nattendav = natten1dav', 'self.nattendav = na1d_av')\n"
        "s = s.replace('self.nattendqkrpb = natten2dqkrpb', 'self.nattendqkrpb = na2d_qk')\n"
        "s = s.replace('self.nattendav = natten2dav', 'self.nattendav = na2d_av')\n"
        "# Assert on rewritten import/assignment lines only — dinat.py:98/108\n"
        "# mention the old names in dead comments that no replace touches.\n"
        "assert 'from natten.functional import na1d_av' in s, 'dinat import patch failed'\n"
        "assert 'self.nattendqkrpb = natten' not in s and 'self.nattendav = natten' not in s, \\\n"
        "    'dinat assignments not patched'\n"
        "p.write_text(s)\n"
        "print('[sow-analyzer] patched allin1 dinat.py to NATTEN >=0.17.5 na*_qk/na*_av API')\n"
        "EOF",
        # `import allin1` pulls the natten chain transitively (same GPU-at-import
        # requirement as the pins step above).
        gpu="L4",
    )
    # Build-time import assert (also proves the patch compiles + all deps resolve).
    .run_commands(
        "python -c 'import allin1; import demucs; import madmom; import natten; "
        "print(\"[sow-analyzer] imports OK\", natten.__version__)'",
        gpu="L4",
    )
    # Silence NATTEN C++ runtime logs at inference (useless noise in a GPU container).
    # TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD=1: torch 2.6 defaults torch.load to
    # weights_only=True, and allin1's loaders.py:55 calls it unguarded, then reads
    # checkpoint['config'] (an OmegaConf object) — which the weights-only unpickler's
    # allowlist rejects. These are trusted taejunkim/allinone checkpoints; overriding
    # here keeps the failure out of the first cold-start request.
    .env({"NATTEN_LOG_LEVEL": "error", "TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD": "1"})
)
model_volume = modal.Volume.from_name(VOLUME_NAME, create_if_missing=True)

app = modal.App(APP_NAME)


@app.cls(
    image=image,
    gpu="L4",
    cpu=4.0,
    memory=16384,  # MiB; allin1 ensemble + demucs peaks well below 16 GiB
    timeout=1800,  # per-input kill switch -> FunctionTimeoutError (retryable client-side)
    startup_timeout=1800,  # first cold start downloads harmonix-all + htdemucs weights into the volume
    max_containers=2,
    scaledown_window=300,
    volumes={MODEL_MOUNT: model_volume},
    # NOTE: deliberately NO retries= — retries are owned by the client
    # (services/modal_analyzer_client.py) so the layers cannot multiply into N x M GPU runs.
)
class SongAnalyzer:
    @modal.enter()
    def load_models(self) -> None:
        """Pre-download all weights into the volume, marker-gated (sow-stems pattern).

        1. allin1's harmonix-all: 8 checkpoint files from HF repo taejunkim/allinone,
           fetched via hf_hub_download into HF_HOME during the first analyze call.
        2. demucs htdemucs: torch.hub download during allin1's internal demix
           (TORCH_HOME).
        3. madmom: RNN weights (.pkl) ship INSIDE the wheel (madmom/models/...),
           so "download" only means exercising one RNNDownBeatProcessor forward
           pass to prove the compiled loaders run on this image.
        4. model_volume.commit() when any marker is new.
        """
        import os
        import pathlib
        import tempfile
        import time

        model_dir = pathlib.Path(MODEL_MOUNT)
        hf_dir = model_dir / "hf"
        hub_dir = model_dir / "hub"
        os.environ["HF_HOME"] = str(hf_dir)
        os.environ["TORCH_HOME"] = str(hub_dir)

        # 1-second warm-up clip: enough to drive one full pass of every model.
        import numpy as np
        import soundfile as sf

        marker_harmonix = model_dir / ".downloaded-harmonix-all"
        marker_htdemucs = model_dir / ".downloaded-htdemucs"
        marker_madmom = model_dir / ".downloaded-madmom"
        fresh_download = any(
            not marker.exists()
            for marker in (marker_harmonix, marker_htdemucs, marker_madmom)
        )

        started = time.time()

        tmp = tempfile.mkdtemp(prefix="analyzer-entry-")
        # Warm-up clip: NOT silence. demucs/separate.py:162 normalizes with
        # `(wav - ref.mean()) / ref.std()`, so an all-zero clip yields std 0 -> a
        # NaN mix, and hdemucs.py:39 asserts the reflect-padded signal round-trips
        # (NaN == NaN is False) -> AssertionError inside the internal demix.
        # Different frequency per channel keeps each channel's std nonzero and the
        # signal deterministic (low amplitude so nothing clips anywhere downstream).
        warmup_path = pathlib.Path(tmp) / "warmup.wav"
        t = np.arange(44100, dtype=np.float32) / 44100.0
        warmup = np.stack(
            [
                0.10 * np.sin(2 * np.pi * 440.0 * t),
                0.10 * np.sin(2 * np.pi * 523.25 * t),
            ],
            axis=1,
        ).astype(np.float32)
        sf.write(str(warmup_path), warmup, 44100)

        if not marker_harmonix.exists() or not marker_htdemucs.exists():
            # One allin1.analyze call downloads harmonix-all (HF cache under
            # HF_HOME) AND runs the internal demix, downloading htdemucs into
            # TORCH_HOME. keep_byproducts=False deletes the stem wavs; markers
            # gate the download, nothing else.
            import allin1

            allin1.analyze(
                str(warmup_path),
                out_dir=pathlib.Path(tmp) / "out",
                visualize=False,
                sonify=False,
                include_embeddings=True,
                keep_byproducts=False,
            )
            marker_harmonix.touch()
            marker_htdemucs.touch()

        if not marker_madmom.exists():
            from madmom.features.downbeats import (
                RNNDownBeatProcessor,
                DBNDownBeatTrackingProcessor,
            )

            rnn = RNNDownBeatProcessor()
            activations = rnn(str(warmup_path))
            dbn = DBNDownBeatTrackingProcessor(beats_per_bar=[3, 4], fps=100)
            dbn(activations)
            marker_madmom.touch()

        if fresh_download:
            model_volume.commit()

        elapsed = time.time() - started
        print(
            f"[sow-analyzer] models loaded in {elapsed:.1f}s "
            f"(fresh_download={fresh_download})",
            flush=True,
        )

    @modal.method()
    def analyze(
        self,
        audio_bytes: bytes,
        filename: str,
        request_id: str,
        include_stems: bool,
        include_beat_grid: bool,
    ) -> dict:
        """Full analysis: allin1 (+ internal htdemucs stems) + optional madmom beat grid.

        Returns a JSON-safe dict mirroring the former local analyze_audio() payload:
        duration_seconds, tempo_bpm, beats, downbeats, sections, embeddings_shape
        and — when include_beat_grid — source/beats/downbeats/detected_at/
        madmom_params. Stem WAV bytes ride inline under result["stems"] when
        include_stems is set.
        """
        import os
        import pathlib
        import tempfile
        import time
        import traceback

        import numpy as np

        model_dir = pathlib.Path(MODEL_MOUNT)
        os.environ.setdefault("HF_HOME", str(model_dir / "hf"))
        os.environ.setdefault("TORCH_HOME", str(model_dir / "hub"))

        started = time.time()
        print(
            f"[sow-analyzer][{request_id}] analyze start: {len(audio_bytes)} bytes "
            f"({filename}, include_stems={include_stems}, "
            f"include_beat_grid={include_beat_grid})",
            flush=True,
        )
        try:
            import allin1

            with tempfile.TemporaryDirectory() as tmp:
                tmp_path = pathlib.Path(tmp)
                input_path = tmp_path / f"input{pathlib.Path(filename).suffix or '.wav'}"
                input_path.write_bytes(audio_bytes)

                demix_dir = tmp_path / "demix"
                spec_dir = tmp_path / "spec"
                result = allin1.analyze(
                    str(input_path),
                    out_dir=None,
                    visualize=False,
                    sonify=False,
                    include_embeddings=True,
                    demix_dir=str(demix_dir),
                    spec_dir=str(spec_dir),
                    keep_byproducts=include_stems,
                    multiprocess=False,  # avoid fork bombs inside the container
                )

                def _jsonsafe(value):
                    if isinstance(value, np.ndarray):
                        return value.tolist()
                    if isinstance(value, (list, tuple)):
                        return [_jsonsafe(v) for v in value]
                    return value

                beats = _jsonsafe(result.beats)
                downbeats = _jsonsafe(result.downbeats)
                sections = [
                    {"label": seg.label, "start": float(seg.start), "end": float(seg.end)}
                    for seg in result.segments
                ]

                payload: dict = {
                    "tempo_bpm": round(float(result.bpm), 1),
                    "beats": beats,
                    "downbeats": downbeats,
                    "sections": sections,
                    "embeddings_shape": list(result.embeddings.shape)
                    if result.embeddings is not None
                    else None,
                }

                if include_stems:
                    stems: dict[str, bytes] = {}
                    stem_dir = demix_dir / "htdemucs" / input_path.stem
                    for stem in ("bass", "drums", "other", "vocals"):
                        stems[stem] = (stem_dir / f"{stem}.wav").read_bytes()
                    payload["stems"] = stems

                if include_beat_grid:
                    from datetime import datetime, timezone

                    from madmom.features.downbeats import (
                        RNNDownBeatProcessor,
                        DBNDownBeatTrackingProcessor,
                    )

                    rnn = RNNDownBeatProcessor()
                    activations = rnn(str(input_path))
                    dbn = DBNDownBeatTrackingProcessor(
                        beats_per_bar=[3, 4],  # model 3/4 and 4/4 time
                        fps=100,  # must match RNNDownBeatProcessor's internal fps
                    )
                    grid = dbn(activations)  # shape (num_beats, 2): [time, beat_in_bar]

                    downbeat_times = grid[grid[:, 1] == 1][:, 0]
                    payload["beat_grid"] = {
                        "source": "madmom",
                        "beats": grid.tolist(),
                        "downbeats": sorted(downbeat_times.tolist()),
                        "detected_at": datetime.now(timezone.utc).isoformat(),
                        "madmom_params": {"beats_per_bar": [3, 4], "fps": 100},
                    }

            print(
                f"[sow-analyzer][{request_id}] analyze done in "
                f"{time.time() - started:.2f}s (stems={include_stems}, "
                f"beat_grid={include_beat_grid})",
                flush=True,
            )
            return payload
        except Exception:
            # Without this, an in-container OOM/CUDA failure leaves nothing in
            # `modal app logs` — the client only sees a bare INTERNAL_FAILURE string.
            print(
                f"[sow-analyzer][{request_id}] analyze failed after "
                f"{time.time() - started:.2f}s:\n{traceback.format_exc()}",
                flush=True,
            )
            raise

    @modal.method()
    def detect_beats(self, audio_bytes: bytes, filename: str, request_id: str) -> dict:
        """madmom-only downbeat detection (no allin1 ensemble, no demix).

        Backs the beat-grid cache for snap_to_downbeat. Kept as a separate method
        so a beat-grid miss does not pay for the full 8-fold harmonix-all pass +
        htdemucs demix that analyze() runs.
        """
        import tempfile
        import time
        import traceback
        from datetime import datetime, timezone

        import pathlib

        started = time.time()
        print(
            f"[sow-analyzer-beats][{request_id}] detect_beats start: {len(audio_bytes)} bytes "
            f"({filename})",
            flush=True,
        )
        try:
            with tempfile.TemporaryDirectory() as tmp:
                tmp_path = pathlib.Path(tmp)
                input_path = tmp_path / f"input{pathlib.Path(filename).suffix or '.wav'}"
                input_path.write_bytes(audio_bytes)

                from madmom.features.downbeats import (
                    RNNDownBeatProcessor,
                    DBNDownBeatTrackingProcessor,
                )

                rnn = RNNDownBeatProcessor()
                activations = rnn(str(input_path))
                dbn = DBNDownBeatTrackingProcessor(
                    beats_per_bar=[3, 4],  # model 3/4 and 4/4 time
                    fps=100,  # must match RNNDownBeatProcessor's internal fps
                )
                grid = dbn(activations)  # shape (num_beats, 2): [time, beat_in_bar]

                downbeat_times = grid[grid[:, 1] == 1][:, 0]
                payload = {
                    "source": "madmom",
                    "beats": grid.tolist(),
                    "downbeats": sorted(downbeat_times.tolist()),
                    "detected_at": datetime.now(timezone.utc).isoformat(),
                    "madmom_params": {"beats_per_bar": [3, 4], "fps": 100},
                }

            print(
                f"[sow-analyzer-beats][{request_id}] detect_beats done in "
                f"{time.time() - started:.2f}s (downbeats={len(payload['downbeats'])})",
                flush=True,
            )
            return payload
        except Exception:
            print(
                f"[sow-analyzer-beats][{request_id}] detect_beats failed after "
                f"{time.time() - started:.2f}s:\n{traceback.format_exc()}",
                flush=True,
            )
            raise