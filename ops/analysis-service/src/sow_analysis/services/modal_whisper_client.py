"""Modal Whisper client — faster-whisper transcription hosted on Modal.

Mirrors services/qwen3_asr_client.py: typed error classes, bounded retries with
backoff, actionable messages, and a module-level singleton getter.

**Retry ownership:** the Modal class (modal_apps/whisper_app.py) sets no
``retries=`` — client-level retries below are the single retry layer for
function calls. Modal's internal gRPC transport still retries transient
statuses (DEADLINE_EXCEEDED, UNAVAILABLE, CANCELLED, INTERNAL, UNKNOWN, max 3)
below us; that is transport self-healing, not input re-execution. With
``retries=`` unset on the class, an input that crashes in the container is
never re-run server-side; only the client decides to re-run it. Do not add
``retries=N`` to ``@app.cls`` later without deleting the client loop (or vice
versa) — worst case is N×M GPU executions of a crashing input.
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Optional

import grpclib
import modal
import modal.exception as modal_exception

from ..config import settings

logger = logging.getLogger(__name__)


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
        grpclib.Status.DEADLINE_EXCEEDED,
        grpclib.Status.UNAVAILABLE,
        grpclib.Status.CANCELLED,
        grpclib.Status.INTERNAL,
        grpclib.Status.UNKNOWN,
    }
)


def _looks_too_large(exc: BaseException) -> bool:
    """Blob-upload 413s surface as RemoteError/InternalFailure, not RequestSizeError."""
    message = str(exc)
    return "413" in message or "too large" in message


class ModalWhisperClient:
    """Async client for the sow-whisper Modal app."""

    def __init__(
        self,
        app_name: Optional[str] = None,
        class_name: Optional[str] = None,
        max_attempts: Optional[int] = None,
        retry_delay_seconds: Optional[float] = None,
        max_audio_bytes: Optional[int] = None,
        allowed_models: Optional[frozenset[str]] = None,
    ):
        self.app_name = app_name or settings.SOW_MODAL_WHISPER_APP_NAME
        self.class_name = class_name or settings.SOW_MODAL_WHISPER_CLASS_NAME
        self.max_attempts = max_attempts or settings.SOW_MODAL_WHISPER_MAX_ATTEMPTS
        self.retry_delay_seconds = (
            retry_delay_seconds
            if retry_delay_seconds is not None
            else settings.SOW_MODAL_WHISPER_RETRY_DELAY_SECONDS
        )
        self.max_audio_bytes = (
            max_audio_bytes
            if max_audio_bytes is not None
            else settings.SOW_MODAL_WHISPER_MAX_AUDIO_BYTES
        )
        if allowed_models is None:
            allowed_models = frozenset(
                m.strip()
                for m in settings.SOW_MODAL_WHISPER_ALLOWED_MODELS.split(",")
                if m.strip()
            )
        self.allowed_models = allowed_models
        self._cls = None
        self._instances: dict[str, Any] = {}

    @property
    def is_configured(self) -> bool:
        return bool(os.environ.get("MODAL_TOKEN_ID") and os.environ.get("MODAL_TOKEN_SECRET"))

    def _get_instance(self, model_name: str) -> Any:
        if not self.is_configured:
            raise ModalWhisperConfigError(
                "MODAL_TOKEN_ID and MODAL_TOKEN_SECRET must be set for Whisper transcription"
            )
        if self._cls is None:
            # modal.Cls.from_name is lazy (no RPC, raises nothing): hydration errors
            # surface at the first remote call below, so nothing is caught here.
            self._cls = modal.Cls.from_name(self.app_name, self.class_name)
        if model_name not in self._instances:
            self._instances[model_name] = self._cls(model_name=model_name)
        return self._instances[model_name]

    async def _transcode_to_16k_mono_wav(self, audio_path: Path) -> bytes:
        """Transcode to 16 kHz mono PCM-16 WAV — Whisper's internal input rate, so
        zero ASR quality loss. Heavy libs are imported lazily and run off the event
        loop (same recipe as _slice_audio_tail in workers/lrc.py)."""

        def _transcode() -> bytes:
            import librosa
            import soundfile as sf

            y, _ = librosa.load(str(audio_path), sr=16000, mono=True)
            tmp_path = Path(tempfile.mkstemp(suffix=".wav")[1])
            try:
                sf.write(str(tmp_path), y, 16000, subtype="PCM_16")
                return tmp_path.read_bytes()
            finally:
                tmp_path.unlink(missing_ok=True)

        return await asyncio.to_thread(_transcode)

    async def transcribe(
        self,
        audio_path: Path,
        *,
        model_name: str,
        language: str,
        initial_prompt: str,
        vad_filter: bool,
        condition_on_previous_text: bool,
    ) -> dict:
        """Transcribe audio bytes on Modal; returns the container's result dict."""
        # Model gate runs before any Modal call: the model name is request-controlled
        # (LrcOptions.whisper_model) and must never reach Modal as an arbitrary HF id.
        if model_name not in self.allowed_models:
            raise ModalWhisperError(
                f"Whisper model '{model_name}' is not allowed; allowed: {sorted(self.allowed_models)}; "
                "adjust SOW_MODAL_WHISPER_ALLOWED_MODELS"
            )
        if not self.is_configured:
            raise ModalWhisperConfigError(
                "MODAL_TOKEN_ID and MODAL_TOKEN_SECRET must be set for Whisper transcription"
            )

        audio_bytes = await asyncio.to_thread(audio_path.read_bytes)
        if not audio_bytes:
            raise ModalWhisperError(f"Audio file is empty: {audio_path}")
        if len(audio_bytes) > self.max_audio_bytes:
            # The old local path had no size cap; hard-failing here would regress
            # long lossless stems. Transcode instead, then re-check.
            audio_bytes = await self._transcode_to_16k_mono_wav(audio_path)
            if len(audio_bytes) > self.max_audio_bytes:
                raise ModalWhisperError(
                    f"Audio too large even after 16kHz mono transcode "
                    f"({len(audio_bytes)} bytes > {self.max_audio_bytes})"
                )

        request_id = uuid.uuid4().hex[:12]
        instance = self._get_instance(model_name)
        started = time.time()
        last_error: Optional[Exception] = None
        failed_attempts = 0
        for attempt in range(1, self.max_attempts + 1):
            try:
                # No asyncio.wait_for here: remote.aio is not cancellation-safe (the
                # remote input keeps running and a retry would stack a second input
                # on the same pool). The class's timeout=1800 is the only kill switch.
                result = await instance.transcribe.remote.aio(
                    audio_bytes=audio_bytes,
                    request_id=request_id,
                    language=language,
                    initial_prompt=initial_prompt,
                    vad_filter=vad_filter,
                    condition_on_previous_text=condition_on_previous_text,
                )
                if failed_attempts:
                    logger.info(
                        "Modal Whisper recovered [%s] after %d failed attempt(s) in %.1fs",
                        request_id,
                        failed_attempts,
                        time.time() - started,
                    )
                return result
            except modal_exception.RequestSizeError as exc:
                raise ModalWhisperError(
                    f"Modal rejected the {len(audio_bytes)}-byte audio payload as too large: {exc}"
                ) from exc
            except modal_exception.NotFoundError as exc:
                raise ModalWhisperConfigError(
                    f"Modal app/class '{self.app_name}/{self.class_name}' not found; deploy it with "
                    "`uv run --no-project --with 'modal>=1.2.6,<2' modal deploy "
                    f"modal_apps/whisper_app.py` (request_id={request_id})"
                ) from exc
            except modal_exception.AuthError as exc:
                raise ModalWhisperConfigError(
                    f"Modal rejected MODAL_TOKEN_ID/MODAL_TOKEN_SECRET: {exc}"
                ) from exc
            except modal_exception.ExecutionError as exc:
                raise ModalWhisperError(
                    f"Modal Whisper execution failed (request_id={request_id}): {exc}"
                ) from exc
            except grpclib.GRPCError as exc:
                if exc.status not in _RETRYABLE_GRPC_STATUSES:
                    raise ModalWhisperError(
                        f"Modal transport error {exc.status} (request_id={request_id}): {exc.message}"
                    ) from exc
                last_error = exc
                failed_attempts += 1
                if attempt >= self.max_attempts:
                    break
                delay = self.retry_delay_seconds * attempt
                logger.warning(
                    "Modal Whisper attempt %d/%d failed [%s] (%s: %s); retrying in %.1fs",
                    attempt,
                    self.max_attempts,
                    request_id,
                    type(exc).__name__,
                    exc,
                    delay,
                )
                await asyncio.sleep(delay)
            except _RETRYABLE_ERRORS as exc:
                if _looks_too_large(exc):
                    raise ModalWhisperError(
                        f"Modal rejected the {len(audio_bytes)}-byte audio payload as too large: {exc}"
                    ) from exc
                last_error = exc
                failed_attempts += 1
                if attempt >= self.max_attempts:
                    logger.warning(
                        "Modal Whisper attempt %d/%d failed [%s] (%s: %s); giving up",
                        attempt,
                        self.max_attempts,
                        request_id,
                        type(exc).__name__,
                        exc,
                    )
                    break
                delay = self.retry_delay_seconds * attempt
                logger.warning(
                    "Modal Whisper attempt %d/%d failed [%s] (%s: %s); retrying in %.1fs",
                    attempt,
                    self.max_attempts,
                    request_id,
                    type(exc).__name__,
                    exc,
                    delay,
                )
                await asyncio.sleep(delay)
            except modal_exception.Error as exc:
                raise ModalWhisperError(
                    f"Modal Whisper failed (request_id={request_id}): {exc}"
                ) from exc
            except Exception as exc:
                # Container user exceptions re-raise as their original class (e.g.
                # ctranslate2 RuntimeError) and match no branch above.
                raise ModalWhisperError(
                    f"Whisper container error (request_id={request_id}): {type(exc).__name__}: {exc}; "
                    f"check `modal app logs {self.app_name}` for the "
                    f"[sow-whisper][{request_id}] traceback"
                ) from exc
        raise ModalWhisperError(
            f"Modal Whisper failed after {self.max_attempts} attempt(s) [request_id={request_id}]: "
            f"{type(last_error).__name__}: {last_error}; check `modal app logs {self.app_name}`"
        ) from last_error


_client: Optional[ModalWhisperClient] = None


def get_modal_whisper_client() -> ModalWhisperClient:
    """Return the module-level singleton, constructing it on first use."""
    global _client
    if _client is None:
        _client = ModalWhisperClient()
        logger.info(
            "Modal Whisper client: app=%s class=%s allowed_models=%s max_attempts=%d",
            _client.app_name,
            _client.class_name,
            sorted(_client.allowed_models),
            _client.max_attempts,
        )
    return _client
