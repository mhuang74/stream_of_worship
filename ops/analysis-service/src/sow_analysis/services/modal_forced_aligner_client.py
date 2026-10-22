"""Modal forced-alignment client — Qwen3-ForcedAligner hosted on Modal.

Mirrors services/modal_whisper_client.py: typed error classes, bounded retries with
backoff, actionable messages, and a module-level singleton getter.

**Retry ownership:** the Modal class (modal_apps/forced_aligner_app.py) sets no
``retries=`` — client-level retries below are the single retry layer for function
calls. Modal's internal gRPC transport still retries transient statuses below us;
that is transport self-healing, not input re-execution. Do not add ``retries=N`` to
``@app.cls`` later without deleting the client loop (or vice versa) — worst case is
N×M GPU executions of a crashing input.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any, Optional

import grpclib
import modal
import modal.exception as modal_exception

from ..config import settings
from .audio_transcode import transcode_to_16k_mono_wav
from .modal_retry import (
    RETRYABLE_GRPC_STATUSES,
    RETRYABLE_MODAL_ERRORS,
    looks_like_oversize_payload,
)

logger = logging.getLogger(__name__)


class ModalForcedAlignerError(Exception):
    """Raised when a Modal forced-alignment invocation fails."""


class ModalForcedAlignerConfigError(ModalForcedAlignerError):
    """Modal is not configured, or the deployed app/class is unreachable."""


class ModalForcedAlignerClient:
    """Async client for the sow-forced-aligner Modal app."""

    def __init__(
        self,
        app_name: Optional[str] = None,
        class_name: Optional[str] = None,
        max_attempts: Optional[int] = None,
        retry_delay_seconds: Optional[float] = None,
        max_audio_bytes: Optional[int] = None,
    ):
        self.app_name = app_name or settings.SOW_MODAL_FORCED_ALIGNER_APP_NAME
        self.class_name = class_name or settings.SOW_MODAL_FORCED_ALIGNER_CLASS_NAME
        self.max_attempts = max_attempts or settings.SOW_MODAL_FORCED_ALIGNER_MAX_ATTEMPTS
        self.retry_delay_seconds = (
            retry_delay_seconds
            if retry_delay_seconds is not None
            else settings.SOW_MODAL_FORCED_ALIGNER_RETRY_DELAY_SECONDS
        )
        self.max_audio_bytes = (
            max_audio_bytes
            if max_audio_bytes is not None
            else settings.SOW_MODAL_FORCED_ALIGNER_MAX_AUDIO_BYTES
        )
        self._cls = None
        self._instance: Any = None

    @property
    def is_configured(self) -> bool:
        return bool(os.environ.get("MODAL_TOKEN_ID") and os.environ.get("MODAL_TOKEN_SECRET"))

    def _get_instance(self) -> Any:
        if not self.is_configured:
            raise ModalForcedAlignerConfigError(
                "MODAL_TOKEN_ID and MODAL_TOKEN_SECRET must be set for forced alignment"
            )
        if self._cls is None:
            # modal.Cls.from_name is lazy (no RPC, raises nothing): hydration errors
            # surface at the first remote call below, so nothing is caught here.
            self._cls = modal.Cls.from_name(self.app_name, self.class_name)
        if self._instance is None:
            self._instance = self._cls()
        return self._instance

    async def align(
        self,
        audio_path: Path,
        lyrics_text: str,
        language: str = "Chinese",
    ) -> list[tuple[float, float, str]]:
        """Align lyrics to audio on Modal; returns (start, end, text) tuples."""
        if not self.is_configured:
            raise ModalForcedAlignerConfigError(
                "MODAL_TOKEN_ID and MODAL_TOKEN_SECRET must be set for forced alignment"
            )
        if audio_path.stat().st_size == 0:
            raise ModalForcedAlignerError(f"Audio file is empty: {audio_path}")

        prep_started = time.time()
        audio_bytes = await asyncio.to_thread(transcode_to_16k_mono_wav, audio_path)
        logger.info(
            "Forced alignment audio prepared for Modal in %.2fs (%d bytes, 16kHz mono)",
            time.time() - prep_started,
            len(audio_bytes),
        )
        if len(audio_bytes) > self.max_audio_bytes:
            raise ModalForcedAlignerError(
                f"Audio too large for Modal blob upload "
                f"({len(audio_bytes)} bytes > {self.max_audio_bytes}); "
                "raise SOW_MODAL_FORCED_ALIGNER_MAX_AUDIO_BYTES"
            )

        request_id = uuid.uuid4().hex[:12]
        instance = self._get_instance()
        started = time.time()
        last_error: Optional[Exception] = None
        failed_attempts = 0
        for attempt in range(1, self.max_attempts + 1):
            try:
                # No asyncio.wait_for here: remote.aio is not cancellation-safe (the
                # remote input keeps running and a retry would stack a second input
                # on the same pool). The class's timeout=1800 is the only kill switch.
                result = await instance.align.remote.aio(
                    audio_wav=audio_bytes,
                    lyrics_text=lyrics_text,
                    language=language,
                    request_id=request_id,
                )
                segments = [
                    (float(s["start"]), float(s["end"]), str(s["text"])) for s in result
                ]
                elapsed = time.time() - started
                if failed_attempts:
                    logger.info(
                        "Modal ForcedAligner recovered [%s] after %d failed attempt(s) in %.1fs",
                        request_id,
                        failed_attempts,
                        elapsed,
                    )
                logger.info(
                    "Forced alignment total Modal elapsed time: %.2fs "
                    "(attempts=%d/%d, segments=%d, request_id=%s; "
                    "includes cold start and audio upload)",
                    elapsed,
                    attempt,
                    self.max_attempts,
                    len(segments),
                    request_id,
                )
                return segments
            except modal_exception.RequestSizeError as exc:
                raise ModalForcedAlignerError(
                    f"Modal rejected the {len(audio_bytes)}-byte audio payload as too large: {exc}"
                ) from exc
            except modal_exception.NotFoundError as exc:
                raise ModalForcedAlignerConfigError(
                    f"Modal app/class '{self.app_name}/{self.class_name}' not found; deploy it with "
                    "`uv run --no-project --with 'modal>=1.2.6,<2' modal deploy "
                    f"modal_apps/forced_aligner_app.py` (request_id={request_id})"
                ) from exc
            except modal_exception.AuthError as exc:
                raise ModalForcedAlignerConfigError(
                    f"Modal rejected MODAL_TOKEN_ID/MODAL_TOKEN_SECRET: {exc}"
                ) from exc
            except modal_exception.ExecutionError as exc:
                raise ModalForcedAlignerError(
                    f"Modal ForcedAligner execution failed (request_id={request_id}): {exc}"
                ) from exc
            except grpclib.GRPCError as exc:
                if exc.status not in RETRYABLE_GRPC_STATUSES:
                    raise ModalForcedAlignerError(
                        f"Modal transport error {exc.status} (request_id={request_id}): {exc.message}"
                    ) from exc
                last_error = exc
                failed_attempts += 1
                if attempt >= self.max_attempts:
                    break
                delay = self.retry_delay_seconds * attempt
                logger.warning(
                    "Modal ForcedAligner attempt %d/%d failed [%s] (%s: %s); retrying in %.1fs",
                    attempt,
                    self.max_attempts,
                    request_id,
                    type(exc).__name__,
                    exc,
                    delay,
                )
                await asyncio.sleep(delay)
            except RETRYABLE_MODAL_ERRORS as exc:
                if looks_like_oversize_payload(exc):
                    raise ModalForcedAlignerError(
                        f"Modal rejected the {len(audio_bytes)}-byte audio payload as too large: {exc}"
                    ) from exc
                last_error = exc
                failed_attempts += 1
                if attempt >= self.max_attempts:
                    logger.warning(
                        "Modal ForcedAligner attempt %d/%d failed [%s] (%s: %s); giving up",
                        attempt,
                        self.max_attempts,
                        request_id,
                        type(exc).__name__,
                        exc,
                    )
                    break
                delay = self.retry_delay_seconds * attempt
                logger.warning(
                    "Modal ForcedAligner attempt %d/%d failed [%s] (%s: %s); retrying in %.1fs",
                    attempt,
                    self.max_attempts,
                    request_id,
                    type(exc).__name__,
                    exc,
                    delay,
                )
                await asyncio.sleep(delay)
            except modal_exception.Error as exc:
                raise ModalForcedAlignerError(
                    f"Modal ForcedAligner failed (request_id={request_id}): {exc}"
                ) from exc
            except Exception as exc:
                # Container user exceptions re-raise as their original class and match
                # no branch above.
                raise ModalForcedAlignerError(
                    f"Forced aligner container error (request_id={request_id}): "
                    f"{type(exc).__name__}: {exc}; "
                    f"check `modal app logs {self.app_name}` for the "
                    f"[sow-forced-aligner][{request_id}] traceback"
                ) from exc
        logger.error(
            "Forced alignment total Modal elapsed time: %.2fs (attempts=%d, request_id=%s, "
            "failed: %s: %s)",
            time.time() - started,
            failed_attempts,
            request_id,
            type(last_error).__name__ if last_error else "None",
            last_error,
        )
        raise ModalForcedAlignerError(
            f"Modal forced alignment failed after {self.max_attempts} attempt(s) "
            f"[request_id={request_id}]: {type(last_error).__name__}: {last_error}; "
            f"check `modal app logs {self.app_name}`"
        ) from last_error


_client: Optional[ModalForcedAlignerClient] = None


def get_modal_forced_aligner_client() -> ModalForcedAlignerClient:
    """Return the module-level singleton, constructing it on first use."""
    global _client
    if _client is None:
        _client = ModalForcedAlignerClient()
        logger.info(
            "Modal ForcedAligner client: app=%s class=%s max_attempts=%d",
            _client.app_name,
            _client.class_name,
            _client.max_attempts,
        )
    return _client
