# Move Whisper transcription from the analysis container to Modal (v2)

> Supersedes `specs/run_whisper_on_modal.md` (kept for context; do not edit).
> v2 fixes issues found in review of the original plan. Every fix below is backed by
> code inspection of `modal` 1.2.7.dev0 (latest line; 1.3 does not exist on PyPI as of
> 2026-10-20) and the analysis-service source at HEAD.

## Review findings driving v2 (vs v1)

| # | Severity | v1 issue | Fact verified | v2 resolution |
|---|----------|----------|---------------|---------------|
| 1 | **Critical** | `modal>=1.3.0` / `--with 'modal>=1.3'` unresolvable | `pip index versions modal`: latest stable **1.2.6**, newest pre 1.2.7.dev0 | Pin `modal>=1.2.6,<2` everywhere |
| 2 | **Critical** | `_RETRYABLE_ERRORS` references `modal_exception.ServiceError`, `ResourceExhaustedError`; error mapping references `PermissionDeniedError` | None exist in `modal.exception` (full class list grepped from wheel). Import-time `AttributeError` would crash the worker | Tuple built only from real classes: `ConnectionError`, `TimeoutError` (+ subclasses incl. `FunctionTimeoutError`), `InternalFailure`, `RemoteError`; raw `grpclib.GRPCError` classified by status (rows 13-14) |
| 3 | **High** | `Cls.from_name` treated as blocking RPC with `NotFoundError`/`AuthError` caught at lookup | `cls.py:623-642`: `from_name` is **lazy** — no server round-trip; hydration errors surface at first use (the `remote.aio` call) | Drop `to_thread` lookup; keep class handle cached; map lookup/auth errors at the call site where they actually raise |
| 4 | **High** | `modal_exception.Error` → wrap, no retry: treats `RemoteError` as permanent | `RemoteError` wraps *user* exceptions from the container (e.g. transient CUDA OOM, blob hiccups). `ExecutionError` covers deserialization/infra. Neither is reliably permanent | `RemoteError` **retryable** (bounded); `ExecutionError` non-retryable; retry budget small (2 attempts) so a genuinely broken model doesn't loop |
| 5 | **High** | No observability for Modal-side failures (crash-looping container, model-load failure, cold starts) | `GENERIC_STATUS_INTERNAL_FAILURE` → `InternalFailure` includes **container startup crash loops** (e.g. cuDNN missing, model load OOM in `@modal.enter()`). From the client these are indistinguishable without logging | Structured error strings + `_load_model` logs model load duration/VRAM; every retryable failure logged with attempt, elapsed, error class; startup capability log shows `configured` state; verification step inspects `modal app logs` |
| 6 | **High** | `asyncio.wait_for` timeout cancels `remote.aio` mid-flight | `_functions.py:1974`: the coroutine is documented **not cancellation-safe** — the remote input keeps running (billed GPU time) and the retry stacks a second input on the same pool | No client-side `wait_for`. Modal's own `timeout=1800` on the class is the single kill switch (returns `FunctionTimeoutError` — retryable). `SOW_MODAL_WHISPER_TIMEOUT_SECONDS` removed |
| 7 | **High** | `whisper_model` from API hits Modal as `modal.parameter` → arbitrary HF download on Modal | `LrcOptions.whisper_model: str` is request-controlled (`routes/jobs.py:256`), admin CLI exposes `--model` | **Env-tunable allowlist** `SOW_MODAL_WHISPER_ALLOWED_MODELS` (user decision). Reject outside list before any Modal call |
| 8 | Medium | `RequestSizeError` branch described as the gRPC 100MB guard; error message blames "gRPC payload" | Function inputs >2 MiB are transparently **blob-uploaded** (`should_upload`, `MAX_OBJECT_SIZE_BYTES = 2 MiB`), so the 100MB gRPC cap is not what binds. `RequestSizeError` is raised by queue/dict, not function input; a >100MB *blob* upload fails as HTTP 413 surfaced as `RemoteError`/`InternalFailure` | Keep a client-side cap as cheap sanity (still 90 MB default) but reword the error to "too large for Modal blob upload"; treat blob-upload 413 as non-retryable size failure (match "413"/"too large" in message) |
| 9 | Medium | v1 silently changed Whisper `compute_type` from `int8` (local) to `float16` (Modal) | `lrc.py:209` uses `int8`; fp16 vs int8 can shift timestamps/segments slightly | Accept the change (GPU fp16 is the point of the migration) but **bump whisper cache-version suffix** so stale local-int8 entries don't mix with Modal-fp16 entries |
| 10 | Medium | `model_volume.commit()` after every `@modal.enter()` | Harmless but only needed once per new model; adds enter latency each cold start | Keep (correctness: ensures the volume write is durable before container dies) — but only call when a download actually happened (check marker file) |
| 11 | Low | `ModalWhisperConfigError` singleton caching: `_cls` cached forever, wrong-env changes need restart | Correct behavior; document it | Documented |
| 12 | Low | v1 verified startup log shows `Whisper \| configured \| True` but nothing logs *which* model/allowlist | — | Capability rows include allowed models |
| 13 | **High** | Raw `grpclib.GRPCError` escapes every typed branch | `grpc_utils.py:410-415`: statuses outside Modal's retryable set are re-raised raw (only UNAUTHENTICATED → `AuthError`); retryable statuses that exhaust Modal's 3 internal retries also surface raw (`process_exception_before_retry` re-raises). Persistent UNAVAILABLE would get zero client retries | Dedicated `grpclib.GRPCError` branch: retry iff status ∈ {DEADLINE_EXCEEDED, UNAVAILABLE, CANCELLED, INTERNAL, UNKNOWN} |
| 14 | **High** | Container user exceptions are NOT `RemoteError` | `container_io_manager.py:329` pickles the exception; `_process_result` deserializes and re-raises the **original class** (`raise exc_with_hints(exc)`); `RemoteError` is only the deserialization fallback. A ctranslate2 `RuntimeError` would bypass all branches untagged | Final `except Exception` catch-all in the attempts loop → non-retryable `ModalWhisperError` naming `type(exc).__name__` |
| 15 | Medium | `max_containers=4` undrivable / over-billed | `SOW_LRC_MAX_CONCURRENT=2` caps concurrent LRC jobs; each job runs at most one Whisper input at a time (main path and tail-fill are sequential within a job) → max 2 concurrent inputs, ever | `max_containers=2`; `scaledown_window=300` (halve idle L4 billing; warm restarts load from the volume) |
| 16 | Medium | v2 test list claimed `test_lrc_tail_fill.py` needs no change — wrong | `tests/test_lrc_tail_fill.py:189` passes `local_model_semaphore=None`; step 4 deletes that parameter → TypeError | Remove the kwarg there (verified: no hits in `test_youtube_transcript.py`) |
| 17 | Medium | Verification grep couldn't fail on leftovers in files v2 edits | Step 7 grep covered only `src tests docker-compose.yml start-dev.sh`; v2 also edits `.env.example`, `scripts/deploy.sh`, `README.md`, `DEPLOYMENT.md` | Widen grep to the whole `ops/analysis-service` (excl. `.venv`/`__pycache__`) |
| 18 | Medium | No client↔container log correlation | `SOW_LRC_MAX_CONCURRENT=2` + tail-fill + retries overlap in `modal app logs`; client logs already carry `[job_id]` via `logging_config.py`, container logs carry nothing | Client generates a `request_id` (uuid4 hex, 12 chars) per call, passes it into the Modal method, logs it on every attempt and embeds it in errors; container prints it in every line |
| 19 | Medium | `SOW_MODAL_WHISPER_MAX_AUDIO_BYTES` can hard-fail inputs that worked locally | Main transcription path transcribes `vocals_dry.flac`/`.wav` or `audio.mp3` (`queue.py:1197-1219`); long lossless stems can exceed 90 MB where the local path had no cap | Oversize → transcode to 16 kHz mono PCM-16 WAV (Whisper's internal rate — zero ASR quality loss; mirrors `_slice_audio_tail`) before upload; cap re-checked after transcode |
| 20 | Low | Test-plan: singleton/class-handle caching leaks fakes across cases | `get_modal_whisper_client()` caches `_cls`/`_instances` module-wide | Tests instantiate a fresh `ModalWhisperClient` per case (or reset the singleton); never go through the singleton in tests |

Non-issues confirmed (v1 was right): `modal_apps/` dir naming (avoids shadowing `import modal`);
`faster_whisper` import inside `load_model` (deploy env must not need it);
`modal.enter` / `modal.parameter` / `uv_pip_install` / `Volume.from_name(commit)` all exist in 1.2.x;
`from_registry(..., add_python="3.11").entrypoint([])` is the documented CUDA pattern;
cuDNN-9 image requirement is real for CTranslate2 ≥4.5 (`faster-whisper` 1.2.1 pins `ctranslate2>=4.0,<5`);
Modal's gRPC client already retries transient statuses (DEADLINE_EXCEEDED, UNAVAILABLE, CANCELLED, INTERNAL, UNKNOWN) — our client layer adds *function-call-level* retries on top, which is the right place for crash-loop/cold-start failures.

## Approach

### 1. Add the Modal app (new file: `ops/analysis-service/modal_apps/whisper_app.py`)

```python
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
    # See "Retry ownership" in step 2.
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
```

Notes vs v1:
- `startup_timeout=900` — first cold start must download ~3 GB (large-v3) into a Volume before
  the first input can run; default startup timeout is too tight and a cold-start failure here
  surfaces to callers as repeated `InternalFailure` (finding #5).
- `faster_whisper` imported inside `load_model`, not module top (unchanged from v1): `modal deploy`
  imports this module in the local deploy environment, which must not need faster-whisper. The
  `from pathlib import Path` used by the marker file also lives inside `load_model` for symmetry.
- `compute_type="float16"` differs from the old local `int8` — whisper transcription cache keys get
  a version suffix bump (step 5) so old int8-derived entries are not mixed with fp16 results.

### 2. Add the client (new file: `ops/analysis-service/src/sow_analysis/services/modal_whisper_client.py`)

Mirror the existing remote-service pattern (`services/qwen3_asr_client.py`): typed error classes,
retries with backoff, actionable messages, module-level singleton getter.

**Retry ownership:** the Modal class sets **no `retries=`** — client-level retries are the single
retry layer for function calls. Modal's internal gRPC transport still retries transient statuses
(DEADLINE_EXCEEDED, UNAVAILABLE, CANCELLED, INTERNAL, UNKNOWN, max 3) below us; that is transport
self-healing, not input re-execution. With `retries=` unset on the class, an input that crashes in
the container is never re-run server-side; only the client decides to re-run it. Do not add
`retries=N` to `@app.cls` later without deleting the client loop (or vice versa) — worst case is
N×M GPU executions of a crashing input.

```python
class ModalWhisperError(Exception):
    """Raised when a Modal Whisper invocation fails."""


class ModalWhisperConfigError(ModalWhisperError):
    """Modal is not configured, or the deployed app/class is unreachable."""


# Only classes that exist in modal.exception (verified in modal 1.2.x).
# NOTE: modal.exception has NO ServiceError/ResourceExhaustedError/PermissionDeniedError.
# Two subtleties verified against the modal client source:
# 1. A crashed/failed input re-raises the ORIGINAL user exception from the container
#    (container_io_manager pickles it; _process_result deserializes and re-raises it).
#    RemoteError is only the fallback when deserialization fails. So a ctranslate2
#    RuntimeError arrives as RuntimeError — the final `except Exception` below is the
#    contract backstop, not RemoteError.
# 2. Raw grpclib.GRPCError escapes Modal's conversion once its transport retries are
#    exhausted (grpc_utils re-raises for any status except UNAUTHENTICATED→AuthError).
#    It must be classified by gRPC status here.
_RETRYABLE_ERRORS = (
    modal_exception.ConnectionError,   # socket/transport failures
    modal_exception.TimeoutError,      # base of FunctionTimeoutError/ExecTimeoutError
    modal_exception.InternalFailure,   # crash-looped container / infra failure
    modal_exception.RemoteError,       # container exception that could not be deserialized;
                                       # bounded retries absorb transient cases. Non-transient
                                       # model errors fail after SOW_MODAL_WHISPER_MAX_ATTEMPTS.
)
_RETRYABLE_GRPC_STATUSES = frozenset(
    {
        grpclib.GRPCStatus.DEADLINE_EXCEEDED,
        grpclib.GRPCStatus.UNAVAILABLE,
        grpclib.GRPCStatus.CANCELLED,
        grpclib.GRPCStatus.INTERNAL,
        grpclib.GRPCStatus.UNKNOWN,
    }
)
```

`ModalWhisperClient.__init__(app_name=None, class_name=None, max_attempts=None,
retry_delay_seconds=None, max_audio_bytes=None, allowed_models=None)` reads defaults from
settings (step 3), keeps `self._cls = None`, `self._instances: dict[str, Any] = {}`.

Exact behavior:

- `is_configured` property → `bool(os.environ.get("MODAL_TOKEN_ID") and os.environ.get("MODAL_TOKEN_SECRET"))`.
- Model gate: `model_name not in self.allowed_models` →
  `ModalWhisperError(f"Whisper model '{model_name}' is not allowed; allowed: {sorted(self.allowed_models)}; adjust SOW_MODAL_WHISPER_ALLOWED_MODELS")` —
  checked **before** any Modal call (finding #7).
- `_get_instance(model_name)`:
  - not configured → `ModalWhisperConfigError("MODAL_TOKEN_ID and MODAL_TOKEN_SECRET must be set for Whisper transcription")`.
  - `modal.Cls.from_name` is **lazy** (no RPC, raises nothing): call it directly,
    `self._cls = modal.Cls.from_name(self.app_name, self.class_name)`, and cache.
  - instance = `self._cls(model_name=model_name)`, cached per model name.
  - `NotFoundError`/`AuthError` are NOT caught here (dead code — they surface at the call below).
- `async transcribe(audio_path: Path, *, model_name, language, initial_prompt, vad_filter,
  condition_on_previous_text) -> dict`:
  - Model gate + credentials checked first (above); empty file →
    `ModalWhisperError(f"Audio file is empty: {audio_path}")`.
  - `audio_bytes = await asyncio.to_thread(audio_path.read_bytes)`.
  - `len(audio_bytes) > max_audio_bytes` → transcode in a temp file via
    `librosa.load(sr=16000, mono=True)` + `soundfile.write(subtype="PCM_16")` (both already
    runtime deps of the service; import lazily inside the client function, off the event loop via
    `asyncio.to_thread` — same recipe as `_slice_audio_tail`, `lrc.py:882-897`; 16 kHz mono is
    Whisper's internal input rate, so zero ASR quality loss). Re-read; still oversize →
    `ModalWhisperError(f"Audio too large even after 16kHz mono transcode ({n} bytes > {cap})")`.
    (Finding #19: the main path transcribes lossless FLAC stems that can exceed 90 MB; the local
    path had no cap, so a hard fail here would be a regression.)
  - `request_id = uuid.uuid4().hex[:12]` per call — logged client-side on every attempt and
    embedded in every raised error, and printed by the container (step 1) so `modal app logs`
    lines correlate with analysis-service logs, which already carry `[job_id]`
    (`logging_config.py`).
  - Attempts loop `for attempt in range(1, max_attempts + 1)` calling
    `await instance.transcribe.remote.aio(audio_bytes=..., request_id=request_id,
    language=..., initial_prompt=..., vad_filter=..., condition_on_previous_text=...)` —
    **no `asyncio.wait_for` wrapper** (finding #6: the coroutine is not cancellation-safe;
    Modal's `timeout=1800` is the only kill switch). Error handling inside the loop, in order:
    - `modal_exception.RequestSizeError` →
      `ModalWhisperError(f"Modal rejected the {n}-byte audio payload as too large: {exc}")`, no retry.
      Also treat any error whose message contains "413" or "too large" as this class (blob-upload
      413 surfaces as RemoteError/InternalFailure, finding #8).
    - `modal_exception.NotFoundError` →
      `ModalWhisperConfigError(f"Modal app/class '{app}/{cls}' not found; deploy it with \`uv run --no-project --with 'modal>=1.2.6,<2' modal deploy modal_apps/whisper_app.py\` (request_id={request_id})")`, no retry.
    - `modal_exception.AuthError` →
      `ModalWhisperConfigError(f"Modal rejected MODAL_TOKEN_ID/MODAL_TOKEN_SECRET: {exc}")`, no retry.
    - `modal_exception.ExecutionError` (deserialization/infra misuse) →
      `ModalWhisperError(f"Modal Whisper execution failed (request_id={request_id}): {exc}")`, no retry.
    - `grpclib.GRPCError` → retryable iff `exc.status in _RETRYABLE_GRPC_STATUSES`
      (finding #13: Modal re-raises raw GRPCError once its transport retries exhaust); otherwise
      `ModalWhisperError(f"Modal transport error {exc.status} (request_id={request_id}): {exc.message}")`, no retry.
    - `_RETRYABLE_ERRORS` →
      log `logger.warning("Modal Whisper attempt %d/%d failed [%s] (%s: %s); retrying in %.1fs", attempt, max_attempts, request_id, type(exc).__name__, exc, delay)`;
      sleep `retry_delay_seconds * attempt`; continue. On the final attempt the log says `giving up`.
    - `modal_exception.Error` (any other modal exception) →
      `ModalWhisperError(f"Modal Whisper failed (request_id={request_id}): {exc}")`, no retry.
    - `except Exception` catch-all (finding #14: container user exceptions re-raise as their
      original class — e.g. ctranslate2 `RuntimeError` — and match nothing above) →
      `ModalWhisperError(f"Whisper container error (request_id={request_id}): {type(exc).__name__}: {exc}; check \`modal app logs sow-whisper\` for the [sow-whisper][{request_id}] traceback")`, no retry.
  - Exhausted attempts →
    `ModalWhisperError(f"Modal Whisper failed after {max_attempts} attempt(s) [request_id={request_id}]: {type(last_error).__name__}: {last_error}; check \`modal app logs sow-whisper\`")`.
- Module-level `get_modal_whisper_client() -> ModalWhisperClient` lazy singleton (`_client`).
- Observability (finding #5): every retryable failure logs error **class name** + message + attempt +
  next delay + request_id (above); the first successful call after ≥1 failure logs recovery;
  the singleton logs once at first construction: `Modal Whisper client: app=%s class=%s
  allowed_models=%s max_attempts=%d`.

### 3. Settings (`ops/analysis-service/src/sow_analysis/config.py`)

Delete `SOW_WHISPER_DEVICE` and `SOW_WHISPER_CACHE_DIR` (lines 262-263) and add in the same block:

```python
    # Whisper runs on Modal (modal_apps/whisper_app.py), not in this container.
    SOW_MODAL_WHISPER_APP_NAME: str = "sow-whisper"
    SOW_MODAL_WHISPER_CLASS_NAME: str = "WhisperTranscriber"
    SOW_MODAL_WHISPER_MAX_ATTEMPTS: int = 2
    SOW_MODAL_WHISPER_RETRY_DELAY_SECONDS: float = 5.0
    SOW_MODAL_WHISPER_MAX_AUDIO_BYTES: int = 90_000_000
    SOW_MODAL_WHISPER_ALLOWED_MODELS: str = "tiny,base,small,medium,large-v3,large-v3-turbo,distil-large-v3"
    SOW_WHISPER_CACHE_VERSION: int = 2  # bump: local int8 -> Modal float16 changes outputs
```

(`SOW_MODAL_WHISPER_TIMEOUT_SECONDS` from v1 is dropped — no client-side timeout, finding #6.
`SOW_WHISPER_CACHE_VERSION` is consumed in step 5.)

### 4. Replace the local transcription body (`ops/analysis-service/src/sow_analysis/workers/lrc.py`)

- Import `from ..services.modal_whisper_client import ModalWhisperError, get_modal_whisper_client` at module top.
- `_run_whisper_transcription` (lines 164-303): drop the `device` parameter; delete the
  `faster_whisper` import / `WhisperModel` construction / `run_in_executor` / cache-dir mkdir.
  New signature:

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

  Keep the existing initial-prompt construction verbatim (lyrics truncation to 50 lines / 2000 chars,
  the English and Chinese prompt strings) so `WHISPER_INITIAL_PROMPT_VERSION = "lang-v2"` cache keys
  stay valid. Then:

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
        "Modal Whisper transcription completed in %.2fs (%d phrases, %d raw)",
        time.time() - transcribe_start, len(phrases), len(result.get("phrases", [])),
    )
    # language is always passed explicitly, so faster-whisper reports probability 1.0;
    # log it for continuity with the old local path but do not present it as a detection.
    logger.info(
        "Whisper language: %s (requested %s)",
        result.get("language"), language,
    )
    if not phrases:
        raise WhisperTranscriptionError("Whisper returned no phrases")
    return phrases
```

  Keep the tail of the old function that logs transcribed phrases with timecodes at DEBUG
  (`_format_timestamp` loop) — observability parity with the local path.

- Remove the `local_model_semaphore` parameter from `fill_uncovered_tail` (line 910) and
  `generate_lrc` (line 1225), the two `async with optional_semaphore(local_model_semaphore):`
  wrappers (lines 1024, 1288) and both `from .queue import optional_semaphore` local imports
  (same two functions). Whisper is no longer a local model, so it must not consume
  `SOW_MAX_CONCURRENT_LOCAL_MODEL_JOBS` slots (those remain for allin1/demucs/aligner —
  `queue.py:503,2103` and stem-separation keep their own slots).
- Update both call sites to drop `device=settings.SOW_WHISPER_DEVICE` (lines 1029, 1293) and the
  removed `local_model_semaphore` doc lines.

### 5. Whisper transcription cache key version bump

`build_whisper_transcription_cache_key` (in `workers/lrc.py`) must incorporate
`settings.SOW_WHISPER_CACHE_VERSION` in `range_suffix`-style composition (append
`wv{settings.SOW_WHISPER_CACHE_VERSION}` to the model/language portion). Rationale (finding #9):
existing cached phrases were produced with local `int8`; Modal produces `float16`. Without a
version bump, old and new entries mix under one key. Bumping means one-time cache invalidation —
acceptable, and `SOW_WHISPER_CACHE_VERSION` lets ops force re-transcription later.

### 6. Queue call sites and dependency gate (`ops/analysis-service/src/sow_analysis/workers/queue.py`)

- `fill_uncovered_tail(...)` call (~line 1638): delete `local_model_semaphore=self._local_model_semaphore,`.
- `generate_lrc(...)` call (~line 1816): delete `local_model_semaphore=self._local_model_semaphore,`.
- LRC import gate: change the comment `# Optional LRC imports - require whisper and openai` (line 114)
  to `# Optional LRC imports - require openai`, and both
  `"LRC dependencies not available (whisper, openai)"` literals (lines 1355, 1363) to
  `"LRC dependencies not available (openai)"`. No test asserts the old string (verified by grep).

### 7. Startup capability log (`ops/analysis-service/src/sow_analysis/main.py`)

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
        ("Whisper", "allowed_models", settings.SOW_MODAL_WHISPER_ALLOWED_MODELS),
        ("Whisper", "max_attempts", str(settings.SOW_MODAL_WHISPER_MAX_ATTEMPTS)),
```

### 8. Env plumbing (compose + deploy template + env example)

`ops/analysis-service/docker-compose.yml` lines 33-35: replace the `# Whisper Configuration` /
`SOW_WHISPER_DEVICE` lines inside `x-common-env` with:

```yaml
  # Whisper runs on Modal (modal_apps/whisper_app.py); tokens come from /opt/sow/.env
  MODAL_TOKEN_ID: ${MODAL_TOKEN_ID}
  MODAL_TOKEN_SECRET: ${MODAL_TOKEN_SECRET}
  SOW_MODAL_WHISPER_APP_NAME: ${SOW_MODAL_WHISPER_APP_NAME:-sow-whisper}
  SOW_MODAL_WHISPER_CLASS_NAME: ${SOW_MODAL_WHISPER_CLASS_NAME:-WhisperTranscriber}
  SOW_MODAL_WHISPER_MAX_ATTEMPTS: ${SOW_MODAL_WHISPER_MAX_ATTEMPTS:-2}
  SOW_MODAL_WHISPER_RETRY_DELAY_SECONDS: ${SOW_MODAL_WHISPER_RETRY_DELAY_SECONDS:-5.0}
  SOW_MODAL_WHISPER_MAX_AUDIO_BYTES: ${SOW_MODAL_WHISPER_MAX_AUDIO_BYTES:-90000000}
  SOW_MODAL_WHISPER_ALLOWED_MODELS: ${SOW_MODAL_WHISPER_ALLOWED_MODELS:-tiny,base,small,medium,large-v3,large-v3-turbo,distil-large-v3}
  SOW_WHISPER_CACHE_VERSION: ${SOW_WHISPER_CACHE_VERSION:-2}
```

`ops/analysis-service/scripts/deploy.sh`: the generated `docker-compose.yml` uses
`env_file: .env`, so only the `.env` heredoc needs editing. In the `cat > "$ENV_FILE" << EOF`
block (lines ~198-264) replace

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
SOW_MODAL_WHISPER_ALLOWED_MODELS="tiny,base,small,medium,large-v3,large-v3-turbo,distil-large-v3"
SOW_WHISPER_CACHE_VERSION=2
```

(`SOW_WHISPER_MODEL` is dead config — nothing reads it.)

`ops/analysis-service/.env.example` lines ~242-246: replace the `SOW_WHISPER_DEVICE` block with
the same Modal block as deploy.sh (v1 missed this file).

### 9. Dependencies (`ops/analysis-service/pyproject.toml`)

- Remove `"faster-whisper>=1.0.0",`.
- Add `"modal>=1.2.6,<2",` — **not** `>=1.3`; that version does not exist (latest stable 1.2.6).
- Refuse the temptation to keep a local fallback: there is no local Whisper path after this cutover.
- Regenerate `ops/analysis-service/uv.lock` with `uv lock`.

### 10. Model allowlist enforcement point (finding #7)

`LrcOptions.whisper_model` is request-controlled (`routes/jobs.py:256` accepts it from the API;
admin CLI exposes `--model`). Enforcement lives in `ModalWhisperClient.transcribe` (step 2):
reject before any Modal call with an actionable error naming the env var. No change to
`LrcOptions` itself — validation at the Modal boundary, operator-tunable via
`SOW_MODAL_WHISPER_ALLOWED_MODELS`. A disallowed model fails the LRC job with
`WhisperTranscriptionError` wrapping the allowlist message — visible in `job.error_message`.

### 11. Tests

New `ops/analysis-service/tests/test_modal_whisper_client.py` (no network; `modal` must be
importable — it is a runtime dep after step 9):

- Fake Modal handle (duck-typed): `_Cls(**params)` → object whose `.transcribe.remote.aio` is an
  `AsyncMock`; patch `modal.Cls.from_name` via
  `monkeypatch.setattr(modal.Cls, "from_name", staticmethod(lambda app, name: fake_cls))`;
  `monkeypatch.setenv("MODAL_TOKEN_ID"/"MODAL_TOKEN_SECRET", "test")`.
- **Fresh instance per case** (finding #20): construct `ModalWhisperClient(...)` directly —
  never via `get_modal_whisper_client()` — so cached `_cls`/`_instances` from an earlier case
  can't leak a stale fake handle into a later one. Add one test that calls the singleton getter
  and asserts it returns the same object twice (wiring only).
- Cases:
  - success payload passthrough; `request_id` kwarg is passed to `remote.aio`;
  - missing credentials → `ModalWhisperConfigError`;
  - model outside allowlist → `ModalWhisperError` naming `SOW_MODAL_WHISPER_ALLOWED_MODELS`,
    zero Modal calls (assert AsyncMock not awaited);
  - `NotFoundError` → config error containing `modal deploy modal_apps/whisper_app.py`;
  - transient `modal.exception.ConnectionError` then success → 2 calls, returns payload, warning
    logged with attempt count + request_id;
  - `FunctionTimeoutError` then success → retry works (timeout class is retryable);
  - `grpclib.GRPCError(status=UNAVAILABLE)` then success → retry works;
    `grpclib.GRPCError(status=INVALID_ARGUMENT)` → non-retryable `ModalWhisperError`
    (finding #13);
  - container user exception (plain `RuntimeError("CUDA out of memory")` — not a modal type,
    finding #14) → single call, non-retryable `ModalWhisperError` containing
    `RuntimeError` and the `modal app logs` hint;
  - `RequestSizeError` → `ModalWhisperError`, no retry;
  - `RemoteError` → wrapped retryable: succeeds on 2nd call; if it fails all attempts →
    `ModalWhisperError` mentioning attempt count and last error class;
  - `ExecutionError` → `ModalWhisperError`, no retry;
  - oversize file → first call to `transcribe` sees 16 kHz mono WAV bytes smaller than the cap
    (transcode path, finding #19): use a generated sine-wave FLAC over the cap; assert success and
    that the bytes sent differ from the file bytes; an incompressible file still over the cap after
    transcode → `ModalWhisperError` before any Modal call;
  - no client-side timeout: a slow-but-succeeding call (stub awaits `asyncio.sleep`) returns the
    payload — proves no `wait_for` wrapper.

Update `ops/analysis-service/tests/integration/test_lrc_worker.py`:

- Delete the module-level `sys.modules["faster_whisper"]` shim and
  `mock_faster_whisper`/`mock_whisper_model_class` objects (lines 14-18).
- `TestRunWhisperTranscription.test_transcription_returns_phrases`: monkeypatch
  `sow_analysis.workers.lrc.get_modal_whisper_client` to return a stub with
  `async transcribe(audio_path, **kwargs)` →
  `{"phrases": [{"text": "hello", "start": 0.0, "end": 0.5, "no_speech_prob": 0.01},
  {"text": "world", "start": 0.6, "end": 1.0, "no_speech_prob": 0.01}], "language": "zh",
  "language_probability": 1.0}`; call `await _run_whisper_transcription(Path(tmp.name), "large-v3", "zh")`;
  assert 2 phrases. Add one case asserting a segment with `no_speech_prob >= max_no_speech_prob`
  is dropped.
- `test_transcription_no_phrases_raises`: stub returns `{"phrases": []}` →
  `WhisperTranscriptionError`.
- `TestGenerateLRC.test_generate_lrc_success` and
  `TestLRCJobQueueProcessing.test_lrc_job_missing_llm_key_fails`: replace faster-whisper mocks with
  `monkeypatch.setattr("sow_analysis.workers.lrc._run_whisper_transcription",
  AsyncMock(return_value=[...WhisperPhrase...]))` and delete all
  `mock_settings.SOW_WHISPER_CACHE_DIR` / `SOW_WHISPER_DEVICE` assignments (lines 303, 326,
  625-626, 801-802).

Tests that patch `_run_whisper_transcription` (`tests/test_lrc_tail_fill.py`,
`tests/test_youtube_transcript.py`) need no change **except**: `tests/test_lrc_tail_fill.py:189`
passes `local_model_semaphore=None` to `fill_uncovered_tail` — delete that kwarg when step 4
removes the parameter (verified: `test_youtube_transcript.py` has no such kwarg).

If a cache-key version test exists for `build_whisper_transcription_cache_key`, update the expected
suffix for the `wv{SOW_WHISPER_CACHE_VERSION}` addition (step 5).

### 12. Operator docs tied to the removed/added config

- `ops/analysis-service/README.md`: remove the `SOW_WHISPER_DEVICE` line (~73) and the
  `export SOW_WHISPER_DEVICE=cuda` guidance (~393); add a short "Whisper on Modal" block: deploy
  command (`--with 'modal>=1.2.6,<2'`), `MODAL_TOKEN_ID`/`MODAL_TOKEN_SECRET` in `/opt/sow/.env`,
  the `SOW_MODAL_WHISPER_*` knobs, and troubleshooting: `uv run --no-project --with 'modal>=1.2.6,<2'
  modal app logs sow-whisper` for container-side errors (cold-start/model-load failures only
  visible there).
- `ops/analysis-service/DEPLOYMENT.md`: replace `SOW_WHISPER_DEVICE=cpu` / `SOW_WHISPER_MODEL=large-v3`
  (~276-279) and the compose snippet `SOW_WHISPER_DEVICE: ${SOW_WHISPER_DEVICE:-cpu}` /
  `SOW_WHISPER_MODEL: ...` (~399-400) with the Modal token + `SOW_MODAL_WHISPER_*` variables.

## Critical files & anchors

- `ops/analysis-service/src/sow_analysis/workers/lrc.py:164-303` — `_run_whisper_transcription`;
  prompt strings and no-speech filtering must be preserved verbatim.
- `ops/analysis-service/src/sow_analysis/workers/lrc.py:900-1035,1220-1300` — tail-fill and
  `generate_lrc` call sites + semaphore wrappers; also `build_whisper_transcription_cache_key`
  for the version bump.
- `ops/analysis-service/src/sow_analysis/workers/queue.py:114-135,1347-1365,1628-1642,1805-1820`
  — import gate and the two LRC call sites.
- `ops/analysis-service/tests/integration/test_lrc_worker.py:14-18,275-330,600-640,780-810` —
  faster-whisper mocks to remove.
- `ops/analysis-service/src/sow_analysis/services/qwen3_asr_client.py:28-45,254-281` —
  retry/error/logging pattern to copy for the new client.

## Observability summary (finding #5)

Failure surfaces and where each is visible:

| Failure | Client log | Modal-side | Job result |
|---|---|---|---|
| Missing token | startup capability row `configured=False`; call raises `ModalWhisperConfigError` | — | `job.error_message` names env vars |
| App not deployed / wrong env | `NotFoundError` → `ModalWhisperConfigError` with deploy command | — | same |
| Bad token | `AuthError` → `ModalWhisperConfigError` | — | same |
| Model load failure on Modal (cuDNN/OOM) | retries (bounded) then `ModalWhisperError` naming `InternalFailure` | `[sow-whisper] model=... loaded` absent in `modal app logs` | `job.error_message` includes last error |
| Transcription crash in container (CUDA OOM, cuDNN) | catch-all → `ModalWhisperError` with exception class + request_id + logs hint | `[sow-whisper][<rid>] transcription failed:` + full traceback | `job.error_message` |
| Transcription timeout (>1800s) | `FunctionTimeoutError` retry, then fail | timeout visible in `modal app logs` | `job.error_message` |
| Transient infra (connection reset, UNAVAILABLE after Modal's own retries) | `ConnectionError`/`GRPCError` warning per attempt w/ class name + delay + request_id; recovery log after success | — | succeeds silently if retried |
| Disallowed model | `ModalWhisperError` before any Modal call | — | allowlist message |
| Oversize audio incompressible even at 16 kHz mono | `ModalWhisperError` before any Modal call | — | cap + transcode message |

Correlation: client attempts log `[<rid>]`; the container prints `[sow-whisper][<rid>]` start /
success / traceback lines; analysis-service logs carry `[job_id]` (`logging_config.py`). Chain:
`job_id` → `request_id` → container traceback.

Runbook: on repeated `InternalFailure` or `FunctionTimeoutError`, inspect
`uv run --no-project --with 'modal>=1.2.6,<2' modal app logs sow-whisper` — client logs cannot
distinguish model-load failure from transient infra (documented in README block). For
`request_id`-tagged failures, grep the logs for the rid to get the container-side traceback.

## Verification

Prerequisites: run from `ops/analysis-service`; Modal credentials are supplied via `/opt/sow/.env`
(load with `set -a; . /opt/sow/.env; set +a` — never echo values). A real Chinese clip exists inside
the dev container at `/cache/cc8f-lrc-tail.wav` (2.6 MB).

1. Lock + unit tests:
   ```bash
   cd ops/analysis-service
   uv lock
   uv run --extra dev pytest tests/test_modal_whisper_client.py tests/integration/test_lrc_worker.py tests/test_lrc_tail_fill.py tests/test_youtube_transcript.py tests/test_lrc.py -q
   ```
   Expected: all pass.

2. Deploy the app (requires `MODAL_TOKEN_ID` non-empty after sourcing):
   ```bash
   uv run --no-project --with 'modal>=1.2.6,<2' modal deploy modal_apps/whisper_app.py
   ```
   Expected: `App deployed` output listing `WhisperTranscriber`; the `sow-whisper-models` volume is
   created.

3. Cold-start + real remote transcription through the production client (host side):
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
   Expected: `zh`, probability 1.0 (language was passed explicitly, not detected), ≥1 phrase, first
   text is Traditional Chinese (matches the song's lyrics). This proves credentials, lookup, GPU
   transcription, and the return contract. The first call includes cold start (model download into
   the volume) — expect minutes, and `startup_timeout=900` must cover it.

4. Container-side observability proof:
   ```bash
   uv run --no-project --with 'modal>=1.2.6,<2' modal app logs sow-whisper | tail -20
   ```
   Expected: `[sow-whisper] model=large-v3 loaded in ...s (fresh_download=True)` and the
   `[sow-whisper] transcribed ...` line from step 3. Absence of the loaded line after a failed call
   is the model-load-failure signature (runbook).

5. Local Whisper is gone + in-container path works. Rebuild the dev image and restart:
   ```bash
   ./start-dev.sh --rebuild     # in tmux pane sow_dev_2 (or a fresh pane); wait for "Application startup complete."
   docker exec analysis-service-analysis-dev-1 python -c "import faster_whisper"
   ```
   Expected: `ModuleNotFoundError: No module named 'faster_whisper'`. Then run the real LRC function
   inside the container:
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
   Expected: ≥1 `WhisperPhrase`. `docker logs analysis-service-analysis-dev-1 | tail -50` must show
   `Running Modal Whisper transcription` and must NOT show `Loading Whisper model` /
   `Whisper model loaded`. Startup log must show `Whisper | backend | modal`,
   `Whisper | configured | True`, and the allowed-models row.

6. Failure-path observability (deliberate, then revert): temporarily rename
   `SOW_MODAL_WHISPER_CLASS_NAME` to `NoSuchClass` in the container env, submit a real LRC job, and
   confirm the job fails with `NotFoundError`-derived config error containing the deploy command;
   restore the value. Also submit with `options.whisper_model="bogus-model"` and confirm
   `job.error_message` names `SOW_MODAL_WHISPER_ALLOWED_MODELS` with zero Modal-side log noise.
   For a failing transcription, capture the client log's `[<rid>]` and confirm
   `modal app logs sow-whisper` contains the matching `[sow-whisper][<rid>]` lines — this proves
   the correlation chain end to end.

7. Memory proof: `docker stats --no-stream --format '{{.Name}} {{.MemUsage}}'` after a
   transcription — the analysis container should stay in the low hundreds of MB (previously the
   process reached 12.4 GiB and triggered earlyoom). Also confirm no stale references anywhere in
   the component (v2 edits docs/env files too, so the grep must cover them):
   ```bash
   grep -rn "SOW_WHISPER_DEVICE\|SOW_WHISPER_CACHE_DIR\|SOW_WHISPER_MODEL\|faster_whisper" \
     . --exclude-dir=.venv --exclude-dir=__pycache__ --exclude-dir=.git
   ```
   Expected: only `modal_apps/whisper_app.py` (its `from faster_whisper import WhisperModel`)
   matches `faster_whisper`, and zero matches for the three old env names.

## Assumptions & contingencies

- Credentials: the user adds `MODAL_TOKEN_ID` / `MODAL_TOKEN_SECRET` to `/opt/sow/.env`. If they are
  absent at implementation time, run steps 1 and the grep check, then stop and report the exact
  blocked commands (steps 2-6). Never reintroduce a local Whisper fallback.
- If `modal>=1.2.6,<2` gains a 1.3+ line later, the pin may be widened deliberately — not silently.
- If the `nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04` image fails to build on Modal, switch the
  registry tag to `nvidia/cuda:12.6.3-cudnn-runtime-ubuntu24.04` (same `add_python="3.11"` and pip
  line).
- If `gpu="L4"` is unavailable for the workspace, change it to `gpu="T4"` in
  `modal_apps/whisper_app.py`; no other change (T4 lacks bf16 tensor cores but fp16 works).
- If the workspace uses a non-default Modal environment, set `MODAL_ENVIRONMENT` in `/opt/sow/.env`
  (the SDK reads env-var overrides for config keys); no code change. The client's
  `Cls.from_name(app, cls)` resolves in that environment.
- If `faster-whisper==1.2.1` fails inside the Modal image, bump to the latest 1.2.x pin in
  `modal_apps/whisper_app.py` only.
- If cold starts are too slow in practice (large-v3 download ~3 GB into a fresh volume), the
  operator may pre-bake the model into the image instead of the volume — code change confined to
  `whisper_app.py` (`download_root` swap); not part of this plan.
