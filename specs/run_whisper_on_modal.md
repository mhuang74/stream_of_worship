# Move Whisper transcription from the analysis container to Modal

## Context

The dev Analysis Service (`analysis-service-analysis-dev-1`) exited/restarted repeatedly in tmux pane `sow_dev_2`.
Evidence:
- `journalctl -u earlyoom`: `sending SIGTERM to process 2184510 ... VmRSS 11567 MiB` (06:38:45) and `sending SIGTERM to process 2525728 ... VmRSS 12395 MiB` (06:52:37), with `mem avail: 1452 of 15872 MiB (9.15%), swap free: 0`. earlyoom kills the biggest process — the uvicorn server worker.
- The worker's peak RSS comes from running `faster-whisper large-v3` in-process: `_run_whisper_transcription` constructs a fresh `WhisperModel` on every transcription (`ops/analysis-service/src/sow_analysis/workers/lrc.py:202-220`), with `SOW_LRC_MAX_CONCURRENT=2` (two concurrent loads) plus the resident audio-separator / forced-aligner singletons.
- Other shutdowns in the pane were intentional dev reloads (`WatchFiles detected changes ... Reloading...`). The residual risk after this change is the `uvicorn --reload` parent surviving a dead child; that behavior is unchanged by this plan.

Ask: run Whisper on Modal; do not run Whisper locally. `MODAL_TOKEN_ID` / `MODAL_TOKEN_SECRET` will be provided in `/opt/sow/.env` (start-dev.sh already passes `--env-file /opt/sow/.env` to compose).

End state: LRC jobs call a deployed Modal class for transcription; the analysis image no longer contains `faster-whisper`; the service holds no Whisper weights in RAM.

## Approach

### 1. Add the Modal app (new file: `ops/analysis-service/modal_apps/whisper_app.py`)

Use a parametrized `@app.cls` so each requested `LrcOptions.whisper_model` value gets its own container pool and model load. Directory name is `modal_apps/`, not `modal/`, so `import modal` inside the app file cannot resolve to a sibling directory.

```python
"""Modal app hosting faster-whisper transcription for sow-analysis.

Deploy (from ops/analysis-service, with MODAL_TOKEN_ID/MODAL_TOKEN_SECRET exported):

    uv run --no-project --with 'modal>=1.3' modal deploy modal_apps/whisper_app.py
"""

import modal

APP_NAME = "sow-whisper"
VOLUME_NAME = "sow-whisper-models"
MODEL_MOUNT = "/models"

# CTranslate2 4.x (faster-whisper 1.2.x) loads cuBLAS + cuDNN 9 from the system,
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
    timeout=1800,
    max_containers=4,
    scaledown_window=600,
    volumes={MODEL_MOUNT: model_volume},
)
class WhisperTranscriber:
    model_name: str = modal.parameter(default="large-v3")

    @modal.enter()
    def load_model(self) -> None:
        from faster_whisper import WhisperModel

        self.model = WhisperModel(
            self.model_name,
            device="cuda",
            compute_type="float16",
            download_root=MODEL_MOUNT,
        )
        model_volume.commit()

    @modal.method()
    def transcribe(
        self,
        audio_bytes: bytes,
        language: str,
        initial_prompt: str,
        vad_filter: bool,
        condition_on_previous_text: bool,
        beam_size: int = 5,
    ) -> dict:
        import io
        import time

        started = time.time()
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
        print(
            f"Modal Whisper: {len(audio_bytes)} bytes -> {len(phrases)} phrases "
            f"in {time.time() - started:.2f}s (model={self.model_name})"
        )
        return {
            "phrases": phrases,
            "language": info.language,
            "language_probability": float(info.language_probability),
        }
```

`faster_whisper` is imported inside `load_model` (not at module top): `modal deploy` imports this module in the local deploy environment, which must not need faster-whisper.

### 2. Add the client (new file: `ops/analysis-service/src/sow_analysis/services/modal_whisper_client.py`)

Mirror the existing remote-service pattern (`services/qwen3_asr_client.py`): typed error classes, retries with backoff, actionable messages, module-level singleton getter.

```python
class ModalWhisperError(Exception):
    """Raised when a Modal Whisper invocation fails."""


class ModalWhisperConfigError(ModalWhisperError):
    """Modal is not configured, or the deployed app/class is unreachable."""


_RETRYABLE_ERRORS = (
    modal_exception.ServiceError,
    modal_exception.ConnectionError,
    modal_exception.InternalFailure,
    modal_exception.ResourceExhaustedError,
    modal_exception.TimeoutError,  # covers FunctionTimeoutError/ExecTimeoutError
    asyncio.TimeoutError,
)
```

`ModalWhisperClient.__init__(app_name=None, class_name=None, timeout_seconds=None, max_attempts=None, retry_delay_seconds=None, max_audio_bytes=None)` reads defaults from settings (step 3), keeps `self._cls = None` and `self._instances: dict[str, Any]`.

Exact behavior:
- `is_configured` property → `bool(os.environ.get("MODAL_TOKEN_ID") and os.environ.get("MODAL_TOKEN_SECRET"))`.
- `_get_instance(model_name)`:
  - not configured → `ModalWhisperConfigError("MODAL_TOKEN_ID and MODAL_TOKEN_SECRET must be set for Whisper transcription")`.
  - `self._cls = await asyncio.to_thread(modal.Cls.from_name, self.app_name, self.class_name)` (lookup is a blocking RPC; never call it on the event loop).
  - `modal_exception.NotFoundError` → `ModalWhisperConfigError(f"Modal app/class '{app}/{cls}' not found; deploy it with \`uv run --no-project --with 'modal>=1.3' modal deploy modal_apps/whisper_app.py\`")`.
  - `modal_exception.AuthError` / `PermissionDeniedError` → `ModalWhisperConfigError(f"Modal rejected MODAL_TOKEN_ID/MODAL_TOKEN_SECRET: {exc}")`.
  - instance = `self._cls(model_name=model_name)`, cached per model name.
- `async transcribe(audio_path: Path, *, model_name, language, initial_prompt, vad_filter, condition_on_previous_text) -> dict`:
  - `audio_bytes = await asyncio.to_thread(audio_path.read_bytes)`; empty → `ModalWhisperError(f"Audio file is empty: {audio_path}")`.
  - `len(audio_bytes) > max_audio_bytes` → `ModalWhisperError(f"Audio file too large for the Modal gRPC payload ({n} bytes > {cap}); raise SOW_MODAL_WHISPER_MAX_AUDIO_BYTES or transcode first")`. Rationale: Modal's gRPC payload cap is 100 MB.
  - Attempts loop `for attempt in range(1, max_attempts + 1)`, calling
    `await asyncio.wait_for(instance.transcribe.remote.aio(audio_bytes=..., language=..., initial_prompt=..., vad_filter=..., condition_on_previous_text=...), timeout=self.timeout_seconds)`.
    - `modal_exception.RequestSizeError` → `ModalWhisperError("Modal rejected the {n}-byte audio payload as too large: {exc}")`, no retry.
    - `_RETRYABLE_ERRORS` → log `Modal Whisper attempt %d/%d failed (%s); retrying|giving up`, sleep `retry_delay_seconds * attempt`, continue.
    - `modal_exception.Error` (e.g. `RemoteError` from faster-whisper) → `ModalWhisperError(f"Modal Whisper failed: {exc}")`, no retry.
  - Exhausted attempts → `ModalWhisperError(f"Modal Whisper failed after {max_attempts} attempt(s): {last_error}")`.
- Module-level `get_modal_whisper_client() -> ModalWhisperClient` lazy singleton (`_client`).

### 3. Settings (`ops/analysis-service/src/sow_analysis/config.py`)

Delete `SOW_WHISPER_DEVICE` and `SOW_WHISPER_CACHE_DIR` (lines 262-263) and add in the same block:

```python
    # Whisper runs on Modal (modal_apps/whisper_app.py), not in this container.
    SOW_MODAL_WHISPER_APP_NAME: str = "sow-whisper"
    SOW_MODAL_WHISPER_CLASS_NAME: str = "WhisperTranscriber"
    SOW_MODAL_WHISPER_TIMEOUT_SECONDS: int = 900
    SOW_MODAL_WHISPER_MAX_ATTEMPTS: int = 2
    SOW_MODAL_WHISPER_RETRY_DELAY_SECONDS: float = 5.0
    SOW_MODAL_WHISPER_MAX_AUDIO_BYTES: int = 90_000_000
```

### 4. Replace the local transcription body (`ops/analysis-service/src/sow_analysis/workers/lrc.py`)

- Import `from ..services.modal_whisper_client import ModalWhisperError, get_modal_whisper_client` at module top.
- `_run_whisper_transcription` (lines 164-303): drop the `device` parameter; delete the `faster_whisper` import / `WhisperModel` construction / `run_in_executor`. New signature:

```python
async def _run_whisper_transcription(
    audio_path: Path,
    model_name: str,
    language: ResolvedLrcLanguage,
    lyrics_text: Optional[str] = None,
    vad_filter: bool = True,
    condition_on_previous_text: bool = True,
    max_no_speech_prob: Optional[float] = None,
) -> List[WhisperPhrase]:
```

  Keep the existing initial-prompt construction verbatim (lyrics truncation to 50 lines / 2000 chars, the English and Chinese prompt strings) so `WHISPER_INITIAL_PROMPT_VERSION = "lang-v2"` cache keys stay valid. Then:

```python
    logger.info("Running Modal Whisper transcription: %s", audio_path)
    transcribe_start = time.time()
    try:
        result = await get_modal_whisper_client().transcribe(
            audio_path,
            model_name=model_name,
            language=language,
            initial_prompt=initial_prompt,
            vad_filter=vad_filter,
            condition_on_previous_text=condition_on_previous_text,
        )
    except Exception as e:
        raise WhisperTranscriptionError(f"Whisper transcription failed: {e}") from e

    phrases: List[WhisperPhrase] = []
    for segment in result.get("phrases", []):
        text = (segment.get("text") or "").strip()
        if not text:
            continue
        no_speech_prob = float(segment.get("no_speech_prob", 0.0))
        if max_no_speech_prob is not None and no_speech_prob >= max_no_speech_prob:
            logger.info(
                "Dropping Whisper segment %.2f-%.2fs (no_speech_prob %.2f >= %.2f): %s",
                segment["start"], segment["end"], no_speech_prob, max_no_speech_prob, text,
            )
            continue
        phrases.append(
            WhisperPhrase(
                text=text,
                start=float(segment["start"]),
                end=float(segment["end"]),
                no_speech_prob=no_speech_prob,
            )
        )
    logger.info(
        "Modal Whisper transcription completed in %.2fs (%d phrases)",
        time.time() - transcribe_start, len(phrases),
    )
    logger.info(
        "Detected language: %s, probability: %.2f",
        result.get("language"), float(result.get("language_probability") or 0.0),
    )
    if not phrases:
        raise WhisperTranscriptionError("Whisper returned no phrases")
    return phrases
```

- Remove the `local_model_semaphore` parameter from `fill_uncovered_tail` (line 910) and `generate_lrc` (line 1225), the two `async with optional_semaphore(local_model_semaphore):` wrappers (lines 1024, 1288) and both `from .queue import optional_semaphore` local imports (same two functions). Whisper is no longer a local model, so it must not consume `SOW_MAX_CONCURRENT_LOCAL_MODEL_JOBS` slots.
- Update both call sites to drop `device=settings.SOW_WHISPER_DEVICE` (lines 1029, 1293) and the removed `local_model_semaphore` docs.

### 5. Queue call sites and dependency gate (`ops/analysis-service/src/sow_analysis/workers/queue.py`)

- `fill_uncovered_tail(...)` call (~line 1630): delete `local_model_semaphore=self._local_model_semaphore,`.
- `generate_lrc(...)` call (~line 1807): delete `local_model_semaphore=self._local_model_semaphore,`.
- LRC import gate: change the comment `# Optional LRC imports - require whisper and openai` to `# Optional LRC imports - require openai`, and both `"LRC dependencies not available (whisper, openai)"` literals (lines 1355, 1363) to `"LRC dependencies not available (openai)"`. No test asserts the old string (verified by grep).

### 6. Startup capability log (`ops/analysis-service/src/sow_analysis/main.py`)

Add `import os`. Replace the two `Whisper` rows (~lines 214-215) with:

```python
        ("Whisper", "backend", "modal"),
        (
            "Whisper",
            "app",
            f"{settings.SOW_MODAL_WHISPER_APP_NAME}/{settings.SOW_MODAL_WHISPER_CLASS_NAME}",
        ),
        (
            "Whisper",
            "configured",
            str(
                bool(
                    os.environ.get("MODAL_TOKEN_ID")
                    and os.environ.get("MODAL_TOKEN_SECRET")
                )
            ),
        ),
```

### 7. Env plumbing (compose + deploy template)

`ops/analysis-service/docker-compose.yml` lines 33-35: replace the `# Whisper Configuration` / `SOW_WHISPER_DEVICE` lines inside `x-common-env` with:

```yaml
  # Whisper runs on Modal (modal_apps/whisper_app.py); tokens come from /opt/sow/.env
  MODAL_TOKEN_ID: ${MODAL_TOKEN_ID}
  MODAL_TOKEN_SECRET: ${MODAL_TOKEN_SECRET}
  SOW_MODAL_WHISPER_APP_NAME: ${SOW_MODAL_WHISPER_APP_NAME:-sow-whisper}
  SOW_MODAL_WHISPER_CLASS_NAME: ${SOW_MODAL_WHISPER_CLASS_NAME:-WhisperTranscriber}
  SOW_MODAL_WHISPER_TIMEOUT_SECONDS: ${SOW_MODAL_WHISPER_TIMEOUT_SECONDS:-900}
  SOW_MODAL_WHISPER_MAX_ATTEMPTS: ${SOW_MODAL_WHISPER_MAX_ATTEMPTS:-2}
  SOW_MODAL_WHISPER_RETRY_DELAY_SECONDS: ${SOW_MODAL_WHISPER_RETRY_DELAY_SECONDS:-5.0}
  SOW_MODAL_WHISPER_MAX_AUDIO_BYTES: ${SOW_MODAL_WHISPER_MAX_AUDIO_BYTES:-90000000}
```

`ops/analysis-service/scripts/deploy.sh`: the generated `docker-compose.yml` uses `env_file: .env`, so only the `.env` heredoc needs editing. In the `cat > "$ENV_FILE" << EOF` block (lines ~198-264) replace

```
SOW_DEMUCS_DEVICE=cpu
SOW_WHISPER_DEVICE=cpu
SOW_WHISPER_MODEL=large-v3
```

with

```
SOW_DEMUCS_DEVICE=cpu

# Whisper runs on Modal (modal_apps/whisper_app.py); fill these in on the server
MODAL_TOKEN_ID=""
MODAL_TOKEN_SECRET=""
```

(`SOW_WHISPER_MODEL` is dead config — nothing reads it.)

### 8. Dependencies (`ops/analysis-service/pyproject.toml`)

- Remove `"faster-whisper>=1.0.0",`.
- Add `"modal>=1.3.0",` (runtime client; `modal.exception` classes used above exist since 1.3).
- Refuse the temptation to keep a local fallback: there is no local Whisper path after this cutover.
- Regenerate `ops/analysis-service/uv.lock` with `uv lock`.

### 9. Tests

New `ops/analysis-service/tests/test_modal_whisper_client.py` (no network):
- Fake Modal handle (duck-typed): `_Cls(**params)` → object whose `.transcribe.remote.aio` is an `AsyncMock`; patch `modal.Cls.from_name` via `monkeypatch.setattr(modal.Cls, "from_name", staticmethod(lambda app, name: fake_cls))`; `monkeypatch.setenv("MODAL_TOKEN_ID"/"MODAL_TOKEN_SECRET", "test")`.
- Cases: success payload passthrough; missing credentials → `ModalWhisperConfigError`; `NotFoundError` → config error containing `modal deploy modal_apps/whisper_app.py`; transient `modal.exception.ServiceError` then success → 2 calls, returns payload; `RequestSizeError` → `ModalWhisperError`, no retry; `RemoteError` → wrapped, no retry; oversize file → `ModalWhisperError` before any Modal call.

Update `ops/analysis-service/tests/integration/test_lrc_worker.py`:
- Delete the module-level `sys.modules["faster_whisper"]` shim and `mock_faster_whisper`/`mock_whisper_model_class` objects (lines 14-18).
- `TestRunWhisperTranscription.test_transcription_returns_phrases`: monkeypatch `sow_analysis.workers.lrc.get_modal_whisper_client` to return a stub with `async transcribe(audio_path, **kwargs)` → `{"phrases": [{"text": "hello", "start": 0.0, "end": 0.5, "no_speech_prob": 0.01}, {"text": "world", "start": 0.6, "end": 1.0, "no_speech_prob": 0.01}], "language": "zh", "language_probability": 0.98}`; call `await _run_whisper_transcription(Path(tmp.name), "large-v3", "zh")`; assert 2 phrases. Add one case asserting a segment with `no_speech_prob >= max_no_speech_prob` is dropped.
- `test_transcription_no_phrases_raises`: stub returns `{"phrases": []}` → `WhisperTranscriptionError`.
- `TestGenerateLRC.test_generate_lrc_success` and `TestLRCJobQueueProcessing.test_lrc_job_missing_llm_key_fails`: replace faster-whisper mocks with `monkeypatch.setattr("sow_analysis.workers.lrc._run_whisper_transcription", AsyncMock(return_value=[...WhisperPhrase...]))` and delete all `mock_settings.SOW_WHISPER_CACHE_DIR` / `SOW_WHISPER_DEVICE` assignments (lines 303, 326, 625-626, 801-802).

Tests that patch `_run_whisper_transcription` (`tests/test_lrc_tail_fill.py`, `tests/test_youtube_transcript.py`) need no change.

### 10. Operator docs tied to the removed/added config

- `ops/analysis-service/README.md`: remove the `SOW_WHISPER_DEVICE` line (~73) and the `export SOW_WHISPER_DEVICE=cuda` guidance (~393); add a short "Whisper on Modal" block: deploy command, `MODAL_TOKEN_ID`/`MODAL_TOKEN_SECRET` in `/opt/sow/.env`, and the `SOW_MODAL_WHISPER_*` knobs.
- `ops/analysis-service/DEPLOYMENT.md`: replace `SOW_WHISPER_DEVICE=cpu` / `SOW_WHISPER_MODEL=large-v3` (~276-279) and the compose snippet `SOW_WHISPER_DEVICE: ${SOW_WHISPER_DEVICE:-cpu}` / `SOW_WHISPER_MODEL: ...` (~399-400) with the Modal token + `SOW_MODAL_WHISPER_*` variables.

## Critical files & anchors

- `ops/analysis-service/src/sow_analysis/workers/lrc.py:164-303` — `_run_whisper_transcription`; prompt strings and no-speech filtering must be preserved verbatim.
- `ops/analysis-service/src/sow_analysis/workers/lrc.py:900-1035,1220-1300` — tail-fill and `generate_lrc` call sites + semaphore wrappers.
- `ops/analysis-service/src/sow_analysis/workers/queue.py:1347-1365,1628-1642,1805-1820` — dependency gate and the two LRC call sites.
- `ops/analysis-service/tests/integration/test_lrc_worker.py:14-18,275-330,600-640,780-810` — faster-whisper mocks to remove.
- `ops/analysis-service/src/sow_analysis/services/qwen3_asr_client.py:28-45,147-193,254-281` — retry/error/logging pattern to copy for the new client.

## Verification

Prerequisites: run from `ops/analysis-service`; Modal credentials are supplied via `/opt/sow/.env` (load with `set -a; . /opt/sow/.env; set +a` — never echo values). A real Chinese clip exists inside the dev container at `/cache/cc8f-lrc-tail.wav` (2.6 MB).

1. Lock + unit tests:
   ```bash
   cd ops/analysis-service
   uv lock
   uv run --extra dev pytest tests/test_modal_whisper_client.py tests/integration/test_lrc_worker.py tests/test_lrc_tail_fill.py tests/test_youtube_transcript.py tests/test_lrc.py -q
   ```
   Expected: all pass.

2. Deploy the app (requires `MODAL_TOKEN_ID` non-empty after sourcing):
   ```bash
   uv run --no-project --with 'modal>=1.3' modal deploy modal_apps/whisper_app.py
   ```
   Expected: `App deployed` output listing `WhisperTranscriber`; the `sow-whisper-models` volume is created.

3. Real remote transcription through the production client (host side):
   ```bash
   docker cp analysis-service-analysis-dev-1:/cache/cc8f-lrc-tail.wav /tmp/cc8f-lrc-tail.wav
   uv run --project . --extra dev python - <<'PY'
   import asyncio
   from pathlib import Path
   from sow_analysis.services.modal_whisper_client import get_modal_whisper_client

   async def main():
       r = await get_modal_whisper_client().transcribe(
           Path("/tmp/cc8f-lrc-tail.wav"),
           model_name="large-v3",
           language="zh",
           initial_prompt="這是一首中文敬拜歌的歌詞",
           vad_filter=False,
           condition_on_previous_text=False,
       )
       print(r["language"], round(r["language_probability"], 2), len(r["phrases"]))
       print(r["phrases"][:3])

   asyncio.run(main())
   PY
   ```
   Expected: `zh`, probability ≈ 1.0, ≥1 phrase, first text is Traditional Chinese (matches the song's lyrics). This proves credentials, lookup, GPU transcription, and the return contract.

4. Local Whisper is gone + in-container path works. Rebuild the dev image and restart:
   ```bash
   ./start-dev.sh --rebuild     # in tmux pane sow_dev_2 (or a fresh pane); wait for "Application startup complete."
   docker exec analysis-service-analysis-dev-1 python -c "import faster_whisper"
   ```
   Expected: `ModuleNotFoundError: No module named 'faster_whisper'`. Then run the real LRC function inside the container:
   ```bash
   docker exec analysis-service-analysis-dev-1 python - <<'PY'
   import asyncio
   from pathlib import Path
   from sow_analysis.workers.lrc import _run_whisper_transcription

   async def main():
       phrases = await _run_whisper_transcription(
           Path("/cache/cc8f-lrc-tail.wav"), "large-v3", "zh",
           vad_filter=False, condition_on_previous_text=False, max_no_speech_prob=0.6,
       )
       print(len(phrases), phrases[0])

   asyncio.run(main())
   PY
   ```
   Expected: ≥1 `WhisperPhrase`. `docker logs analysis-service-analysis-dev-1 | tail -50` must show `Running Modal Whisper transcription` and must NOT show `Loading Whisper model` / `Whisper model loaded`. Startup log must show `Whisper | backend | modal` and `Whisper | configured | True`.

5. Memory proof: `docker stats --no-stream --format '{{.Name}} {{.MemUsage}}'` after a transcription — the analysis container should stay in the low hundreds of MB (previously the process reached 12.4 GiB and triggered earlyoom). Also confirm no stale references:
   ```bash
   grep -rn "SOW_WHISPER_DEVICE\|SOW_WHISPER_CACHE_DIR\|faster_whisper" src tests docker-compose.yml start-dev.sh
   ```
   Expected: only `modal_apps/whisper_app.py` (its `from faster_whisper import WhisperModel`) matches `faster_whisper`.

## Assumptions & contingencies

- Credentials: the user adds `MODAL_TOKEN_ID` / `MODAL_TOKEN_SECRET` to `/opt/sow/.env`. If they are absent at implementation time, run steps 1 and the grep check, then stop and report the exact blocked commands (steps 2-4). Never reintroduce a local Whisper fallback.
- If the `nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04` image fails to build on Modal, switch the registry tag to `nvidia/cuda:12.6.3-cudnn-runtime-ubuntu24.04` (same `add_python="3.11"` and pip line).
- If `gpu="L4"` is unavailable for the workspace, change it to `gpu="T4"` in `modal_apps/whisper_app.py`; no other change.
- If the workspace uses a non-default Modal environment, set `MODAL_ENVIRONMENT` in `/opt/sow/.env` (the SDK reads it); no code change.
- If `faster-whisper==1.2.1` fails inside the Modal image, bump to the latest 1.2.x pin in `modal_apps/whisper_app.py` only.
