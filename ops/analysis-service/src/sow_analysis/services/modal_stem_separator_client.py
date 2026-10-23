"""Modal stem-separation client — audio-separator hosted on Modal.

Mirrors services/modal_forced_aligner_client.py: typed error classes, bounded retries
with backoff, actionable messages, and a module-level singleton getter.

**Retry ownership:** the Modal class (modal_apps/stem_separator_app.py) sets no
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
from typing import Any, Optional, Tuple

import grpclib
import modal
import modal.exception as modal_exception

from ..config import settings
from .modal_retry import (
    RETRYABLE_GRPC_STATUSES,
    RETRYABLE_MODAL_ERRORS,
    looks_like_oversize_payload,
)

logger = logging.getLogger(__name__)


class ModalStemSeparatorError(Exception):
    """Raised when a Modal stem-separation invocation fails."""


class ModalStemSeparatorConfigError(ModalStemSeparatorError):
    """Modal is not configured, or the deployed app/class is unreachable."""


class ModalStemSeparatorClient:
    """Async client for the sow-stems Modal app."""

    def __init__(
        self,
        app_name: Optional[str] = None,
        class_name: Optional[str] = None,
        vocal_model: Optional[str] = None,
        dereverb_model: Optional[str] = None,
        max_attempts: Optional[int] = None,
        retry_delay_seconds: Optional[float] = None,
        max_audio_bytes: Optional[int] = None,
    ):
        self.app_name = app_name or settings.SOW_MODAL_STEM_APP_NAME
        self.class_name = class_name or settings.SOW_MODAL_STEM_CLASS_NAME
        self.vocal_model = vocal_model or settings.SOW_VOCAL_SEPARATION_MODEL
        self.dereverb_model = dereverb_model or settings.SOW_DEREVERB_MODEL
        self.max_attempts = max_attempts or settings.SOW_MODAL_STEM_MAX_ATTEMPTS
        self.retry_delay_seconds = (
            retry_delay_seconds
            if retry_delay_seconds is not None
            else settings.SOW_MODAL_STEM_RETRY_DELAY_SECONDS
        )
        self.max_audio_bytes = (
            max_audio_bytes
            if max_audio_bytes is not None
            else settings.SOW_MODAL_STEM_MAX_AUDIO_BYTES
        )
        self._cls = None
        # Keyed by (vocal_model, dereverb_model) because both are class parameters:
        # different model pairs are different Modal class handles.
        self._instances: dict[tuple[str, str], Any] = {}

    @property
    def is_configured(self) -> bool:
        return bool(os.environ.get("MODAL_TOKEN_ID") and os.environ.get("MODAL_TOKEN_SECRET"))

    def _get_instance(self) -> Any:
        if not self.is_configured:
            raise ModalStemSeparatorConfigError(
                "MODAL_TOKEN_ID and MODAL_TOKEN_SECRET must be set for stem separation"
            )
        if self._cls is None:
            # modal.Cls.from_name is lazy (no RPC, raises nothing): hydration errors
            # surface at the first remote call below, so nothing is caught here.
            self._cls = modal.Cls.from_name(self.app_name, self.class_name)
        key = (self.vocal_model, self.dereverb_model)
        if key not in self._instances:
            self._instances[key] = self._cls(
                vocal_model=self.vocal_model, dereverb_model=self.dereverb_model
            )
        return self._instances[key]

    async def separate_stems(
        self,
        input_path: Path,
        output_dir: Path,
    ) -> Tuple[Optional[Path], Optional[Path], Optional[Path]]:
        """Run two-stage stem separation on Modal.

        Returns:
            Tuple of (vocals_dry_path, vocals_path, instrumental_path) — same order as
            the former in-container AudioSeparatorWrapper. Any element may be None when
            the corresponding stage produced nothing.
        """
        data = await self._read_audio(input_path)
        result = await self._invoke(
            "separate", {"audio_bytes": data, "filename": input_path.name}
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        vocals_dry = await self._write_stem(output_dir / "vocals_dry.flac", result.get("vocals_dry"))
        vocals = await self._write_stem(output_dir / "vocals.flac", result.get("vocals"))
        instrumental = await self._write_stem(
            output_dir / "instrumental.flac", result.get("instrumental")
        )
        return vocals_dry, vocals, instrumental

    async def remove_reverb(
        self,
        vocals_path: Path,
        output_dir: Path,
    ) -> Tuple[Optional[Path], Optional[Path]]:
        """Run Stage 2 only (de-reverb) on Modal; callers ignore the second element."""
        data = await self._read_audio(vocals_path)
        result = await self._invoke(
            "remove_reverb", {"vocals_bytes": data, "filename": vocals_path.name}
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        dry = await self._write_stem(output_dir / "vocals_dry.flac", result.get("vocals_dry"))
        return dry, None

    async def _read_audio(self, path: Path) -> bytes:
        if not path.exists():
            raise ModalStemSeparatorError(f"Audio file not found: {path}")
        data = await asyncio.to_thread(path.read_bytes)
        if not data:
            raise ModalStemSeparatorError(f"Audio file is empty: {path}")
        if len(data) > self.max_audio_bytes:
            raise ModalStemSeparatorError(
                f"Audio too large for Modal blob upload "
                f"({len(data)} bytes > {self.max_audio_bytes}); "
                "raise SOW_MODAL_STEM_MAX_AUDIO_BYTES"
            )
        return data

    @staticmethod
    async def _write_stem(path: Path, data: Optional[bytes]) -> Optional[Path]:
        """Write a returned stem atomically; falsy payload means "stage produced nothing"."""
        if not data:
            return None
        tmp = path.with_suffix(path.suffix + ".part")
        await asyncio.to_thread(tmp.write_bytes, data)
        tmp.replace(path)
        return path

    async def _invoke(self, method: str, payload: dict) -> dict:
        """Call one remote method with the shared retry ladder."""
        instance = self._get_instance()
        request_id = uuid.uuid4().hex[:12]
        audio_len = len(payload.get("audio_bytes") or payload.get("vocals_bytes") or b"")
        started = time.time()
        last_error: Optional[Exception] = None
        failed_attempts = 0

        for attempt in range(1, self.max_attempts + 1):
            try:
                # No asyncio.wait_for here: remote.aio is not cancellation-safe (the
                # remote input keeps running and a retry would stack a second input
                # on the same pool). The class's timeout=1800 is the only kill switch.
                result = await getattr(instance, method).remote.aio(
                    **payload, request_id=request_id
                )
                elapsed = time.time() - started
                if failed_attempts:
                    logger.info(
                        "Modal StemSeparator recovered [%s] after %d failed attempt(s) in %.1fs",
                        request_id,
                        failed_attempts,
                        elapsed,
                    )
                logger.info(
                    "Stem separation total Modal elapsed time: %.2fs "
                    "(attempts=%d/%d, request_id=%s; "
                    "includes cold start and stem download)",
                    elapsed,
                    attempt,
                    self.max_attempts,
                    request_id,
                )
                return result
            except modal_exception.RequestSizeError as exc:
                raise ModalStemSeparatorError(
                    f"Modal rejected the {audio_len}-byte audio payload as too large: {exc}"
                ) from exc
            except modal_exception.NotFoundError as exc:
                raise ModalStemSeparatorConfigError(
                    f"Modal app/class '{self.app_name}/{self.class_name}' not found; deploy it with "
                    "`uv run --no-project --with 'modal>=1.2.6,<2' modal deploy "
                    f"modal_apps/stem_separator_app.py` (request_id={request_id})"
                ) from exc
            except modal_exception.AuthError as exc:
                raise ModalStemSeparatorConfigError(
                    f"Modal rejected MODAL_TOKEN_ID/MODAL_TOKEN_SECRET: {exc}"
                ) from exc
            except modal_exception.ExecutionError as exc:
                raise ModalStemSeparatorError(
                    f"Modal StemSeparator execution failed (request_id={request_id}): {exc}"
                ) from exc
            except grpclib.GRPCError as exc:
                if exc.status not in RETRYABLE_GRPC_STATUSES:
                    raise ModalStemSeparatorError(
                        f"Modal transport error {exc.status} (request_id={request_id}): {exc.message}"
                    ) from exc
                last_error = exc
                failed_attempts += 1
                if attempt >= self.max_attempts:
                    break
                delay = self.retry_delay_seconds * attempt
                logger.warning(
                    "Modal StemSeparator attempt %d/%d failed [%s] (%s: %s); retrying in %.1fs",
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
                    raise ModalStemSeparatorError(
                        f"Modal rejected the {audio_len}-byte audio payload as too large: {exc}"
                    ) from exc
                last_error = exc
                failed_attempts += 1
                if attempt >= self.max_attempts:
                    logger.warning(
                        "Modal StemSeparator attempt %d/%d failed [%s] (%s: %s); giving up",
                        attempt,
                        self.max_attempts,
                        request_id,
                        type(exc).__name__,
                        exc,
                    )
                    break
                delay = self.retry_delay_seconds * attempt
                logger.warning(
                    "Modal StemSeparator attempt %d/%d failed [%s] (%s: %s); retrying in %.1fs",
                    attempt,
                    self.max_attempts,
                    request_id,
                    type(exc).__name__,
                    exc,
                    delay,
                )
                await asyncio.sleep(delay)
            except modal_exception.Error as exc:
                raise ModalStemSeparatorError(
                    f"Modal stem separation failed (request_id={request_id}): {exc}"
                ) from exc
            except Exception as exc:
                # Container user exceptions re-raise as their original class and match
                # no branch above.
                raise ModalStemSeparatorError(
                    f"Stem separator container error (request_id={request_id}): "
                    f"{type(exc).__name__}: {exc}; "
                    f"check `modal app logs {self.app_name}` for the "
                    f"[sow-stems][{request_id}] traceback"
                ) from exc

        logger.error(
            "Stem separation total Modal elapsed time: %.2fs (attempts=%d, request_id=%s, "
            "failed: %s: %s)",
            time.time() - started,
            failed_attempts,
            request_id,
            type(last_error).__name__ if last_error else "None",
            last_error,
        )
        raise ModalStemSeparatorError(
            f"Modal stem separation failed after {self.max_attempts} attempt(s) "
            f"[request_id={request_id}]: {type(last_error).__name__}: {last_error}; "
            f"check `modal app logs {self.app_name}`"
        ) from last_error


_client: Optional[ModalStemSeparatorClient] = None


def get_modal_stem_separator_client() -> ModalStemSeparatorClient:
    """Return the module-level singleton, constructing it on first use."""
    global _client
    if _client is None:
        _client = ModalStemSeparatorClient()
        logger.info(
            "Modal StemSeparator client: app=%s class=%s max_attempts=%d",
            _client.app_name,
            _client.class_name,
            _client.max_attempts,
        )
    return _client
