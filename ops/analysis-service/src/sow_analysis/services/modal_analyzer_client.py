"""Modal analyzer client — full-song analysis (allin1 + Demucs + madmom) on Modal.

Mirrors services/modal_forced_aligner_client.py: typed error classes, bounded
retries with backoff, actionable messages, and a module-level singleton getter.

**Retry ownership:** the Modal class (modal_apps/analyzer_app.py) sets no
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
from .audio_transcode import transcode_to_wav_fullrate
from .modal_retry import (
    RETRYABLE_GRPC_STATUSES,
    RETRYABLE_MODAL_ERRORS,
    looks_like_oversize_payload,
)

logger = logging.getLogger(__name__)


class ModalAnalyzerError(Exception):
    """Raised when a Modal analyzer invocation fails."""


class ModalAnalyzerConfigError(ModalAnalyzerError):
    """Modal is not configured, or the deployed app/class is unreachable."""


class ModalAnalyzerClient:
    """Async client for the sow-analyzer Modal app."""

    def __init__(
        self,
        app_name: Optional[str] = None,
        class_name: Optional[str] = None,
        max_attempts: Optional[int] = None,
        retry_delay_seconds: Optional[float] = None,
        max_audio_bytes: Optional[int] = None,
    ):
        self.app_name = app_name or settings.SOW_MODAL_ANALYZER_APP_NAME
        self.class_name = class_name or settings.SOW_MODAL_ANALYZER_CLASS_NAME
        self.max_attempts = max_attempts or settings.SOW_MODAL_ANALYZER_MAX_ATTEMPTS
        self.retry_delay_seconds = (
            retry_delay_seconds
            if retry_delay_seconds is not None
            else settings.SOW_MODAL_ANALYZER_RETRY_DELAY_SECONDS
        )
        self.max_audio_bytes = (
            max_audio_bytes
            if max_audio_bytes is not None
            else settings.SOW_MODAL_ANALYZER_MAX_AUDIO_BYTES
        )
        self._cls = None
        self._instance: Any = None

    @property
    def is_configured(self) -> bool:
        return bool(os.environ.get("MODAL_TOKEN_ID") and os.environ.get("MODAL_TOKEN_SECRET"))

    def _get_instance(self) -> Any:
        if not self.is_configured:
            raise ModalAnalyzerConfigError(
                "MODAL_TOKEN_ID and MODAL_TOKEN_SECRET must be set for full analysis"
            )
        if self._cls is None:
            # modal.Cls.from_name is lazy (no RPC, raises nothing): hydration errors
            # surface at the first remote call below, so nothing is caught here.
            self._cls = modal.Cls.from_name(self.app_name, self.class_name)
        if self._instance is None:
            self._instance = self._cls()
        return self._instance

    async def analyze(
        self,
        audio_path: Path,
        output_stems_dir: Optional[Path] = None,
    ) -> dict:
        """Run full analysis on Modal.

        Args:
            audio_path: Local audio file to analyze.
            output_stems_dir: When set, the four demucs stem WAVs returned by the
                app are written here as {stem}.wav and stripped from the result.

        Returns:
            Analysis dict (duration/tempo/beats/downbeats/sections/embeddings_shape).
        """
        if not self.is_configured:
            raise ModalAnalyzerConfigError(
                "MODAL_TOKEN_ID and MODAL_TOKEN_SECRET must be set for full analysis"
            )
        if audio_path.stat().st_size == 0:
            raise ModalAnalyzerError(f"Audio file is empty: {audio_path}")

        prep_started = time.time()
        audio_bytes = await asyncio.to_thread(transcode_to_wav_fullrate, audio_path)
        logger.info(
            "Analyzer audio prepared for Modal in %.2fs (%d bytes, native-rate WAV)",
            time.time() - prep_started,
            len(audio_bytes),
        )
        if len(audio_bytes) > self.max_audio_bytes:
            raise ModalAnalyzerError(
                f"Audio too large for Modal blob upload "
                f"({len(audio_bytes)} bytes > {self.max_audio_bytes}); "
                "raise SOW_MODAL_ANALYZER_MAX_AUDIO_BYTES"
            )

        request_id = uuid.uuid4().hex[:12]
        result = await self._invoke(
            remote_method="analyze",
            audio_bytes=audio_bytes,
            filename=audio_path.name,
            request_id=request_id,
            log_prefix="[sow-analyzer]",
            payload_kwargs={
                "include_stems": output_stems_dir is not None,
                "include_beat_grid": False,
            },
        )

        stems = result.pop("stems", None)
        if output_stems_dir is not None:
            if not stems:
                raise ModalAnalyzerError(
                    f"Modal app '{self.app_name}' returned no stems for a "
                    f"generate_stems request (request_id={request_id})"
                )
            output_stems_dir.mkdir(parents=True, exist_ok=True)
            for stem_name, stem_bytes in stems.items():
                (output_stems_dir / f"{stem_name}.wav").write_bytes(stem_bytes)
        return result

    async def detect_beats(self, audio_path: Path) -> dict:
        """Run madmom downbeat detection on Modal (no stems, no allin1 pass).

        Returns the same payload shape the former in-container
        _detect_downbeats_madmom produced (source/beats/downbeats/detected_at/
        madmom_params).
        """
        if not self.is_configured:
            raise ModalAnalyzerConfigError(
                "MODAL_TOKEN_ID and MODAL_TOKEN_SECRET must be set for downbeat detection"
            )
        if audio_path.stat().st_size == 0:
            raise ModalAnalyzerError(f"Audio file is empty: {audio_path}")

        prep_started = time.time()
        audio_bytes = await asyncio.to_thread(transcode_to_wav_fullrate, audio_path)
        logger.info(
            "Beat-grid audio prepared for Modal in %.2fs (%d bytes, native-rate WAV)",
            time.time() - prep_started,
            len(audio_bytes),
        )
        if len(audio_bytes) > self.max_audio_bytes:
            raise ModalAnalyzerError(
                f"Audio too large for Modal blob upload "
                f"({len(audio_bytes)} bytes > {self.max_audio_bytes}); "
                "raise SOW_MODAL_ANALYZER_MAX_AUDIO_BYTES"
            )

        request_id = uuid.uuid4().hex[:12]
        return await self._invoke(
            remote_method="detect_beats",
            audio_bytes=audio_bytes,
            filename=audio_path.name,
            request_id=request_id,
            log_prefix="[sow-analyzer-beats]",
            payload_kwargs={},
        )

    async def _invoke(
        self,
        remote_method: str,
        audio_bytes: bytes,
        filename: str,
        request_id: str,
        log_prefix: str,
        payload_kwargs: dict,
    ) -> dict:
        """Shared retry ladder; one remote @modal.method() call, client-owned retries."""
        instance = self._get_instance()
        remote_fn = getattr(instance, remote_method).remote.aio
        started = time.time()
        last_error: Optional[Exception] = None
        failed_attempts = 0
        for attempt in range(1, self.max_attempts + 1):
            try:
                # No asyncio.wait_for here: remote.aio is not cancellation-safe (the
                # remote input keeps running and a retry would stack a second input
                # on the same pool). The class's timeout=1800 is the only kill switch.
                result = await remote_fn(
                    audio_bytes=audio_bytes,
                    filename=filename,
                    request_id=request_id,
                    **payload_kwargs,
                )
                elapsed = time.time() - started
                if failed_attempts:
                    logger.info(
                        "Modal Analyzer recovered [%s] after %d failed attempt(s) in %.1fs",
                        request_id,
                        failed_attempts,
                        elapsed,
                    )
                logger.info(
                    "Analyzer total Modal elapsed time: %.2fs "
                    "(attempts=%d/%d, remote=%s, request_id=%s; includes cold start "
                    "and audio upload)",
                    elapsed,
                    attempt,
                    self.max_attempts,
                    remote_method,
                    request_id,
                )
                return result
            except modal_exception.RequestSizeError as exc:
                raise ModalAnalyzerError(
                    f"Modal rejected the {len(audio_bytes)}-byte audio payload as too large: {exc}"
                ) from exc
            except modal_exception.NotFoundError as exc:
                raise ModalAnalyzerConfigError(
                    f"Modal app/class '{self.app_name}/{self.class_name}' not found; deploy it with "
                    "`uv run --no-project --with 'modal>=1.2.6,<2' modal deploy "
                    f"modal_apps/analyzer_app.py` (request_id={request_id})"
                ) from exc
            except modal_exception.AuthError as exc:
                raise ModalAnalyzerConfigError(
                    f"Modal rejected MODAL_TOKEN_ID/MODAL_TOKEN_SECRET: {exc}"
                ) from exc
            except modal_exception.ExecutionError as exc:
                raise ModalAnalyzerError(
                    f"Modal Analyzer execution failed (remote={remote_method}, "
                    f"request_id={request_id}): {exc}"
                ) from exc
            except grpclib.GRPCError as exc:
                if exc.status not in RETRYABLE_GRPC_STATUSES:
                    raise ModalAnalyzerError(
                        f"Modal transport error {exc.status} (request_id={request_id}): {exc.message}"
                    ) from exc
                last_error = exc
                failed_attempts += 1
                if attempt >= self.max_attempts:
                    break
                delay = self.retry_delay_seconds * attempt
                logger.warning(
                    "Modal Analyzer attempt %d/%d failed [%s] (%s: %s); retrying in %.1fs",
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
                    raise ModalAnalyzerError(
                        f"Modal rejected the {len(audio_bytes)}-byte audio payload as too large: {exc}"
                    ) from exc
                last_error = exc
                failed_attempts += 1
                if attempt >= self.max_attempts:
                    logger.warning(
                        "Modal Analyzer attempt %d/%d failed [%s] (%s: %s); giving up",
                        attempt,
                        self.max_attempts,
                        request_id,
                        type(exc).__name__,
                        exc,
                    )
                    break
                delay = self.retry_delay_seconds * attempt
                logger.warning(
                    "Modal Analyzer attempt %d/%d failed [%s] (%s: %s); retrying in %.1fs",
                    attempt,
                    self.max_attempts,
                    request_id,
                    type(exc).__name__,
                    exc,
                    delay,
                )
                await asyncio.sleep(delay)
            except modal_exception.Error as exc:
                raise ModalAnalyzerError(
                    f"Modal Analyzer failed (request_id={request_id}): {exc}"
                ) from exc
            except Exception as exc:
                # Container user exceptions re-raise as their original class and match
                # no branch above.
                raise ModalAnalyzerError(
                    f"Analyzer container error (remote={remote_method}, "
                    f"request_id={request_id}): "
                    f"{type(exc).__name__}: {exc}; "
                    f"check `modal app logs {self.app_name}` for the "
                    f"{log_prefix}[{request_id}] traceback"
                ) from exc
        logger.error(
            "Analyzer total Modal elapsed time: %.2fs (attempts=%d, remote=%s, "
            "request_id=%s, failed: %s: %s)",
            time.time() - started,
            failed_attempts,
            remote_method,
            request_id,
            type(last_error).__name__ if last_error else "None",
            last_error,
        )
        raise ModalAnalyzerError(
            f"Modal analysis failed after {self.max_attempts} attempt(s) "
            f"[request_id={request_id}]: {type(last_error).__name__}: {last_error}; "
            f"check `modal app logs {self.app_name}`"
        ) from last_error


_client: Optional[ModalAnalyzerClient] = None


def get_modal_analyzer_client() -> ModalAnalyzerClient:
    """Return the module-level singleton, constructing it on first use."""
    global _client
    if _client is None:
        _client = ModalAnalyzerClient()
        logger.info(
            "Modal Analyzer client: app=%s class=%s max_attempts=%d",
            _client.app_name,
            _client.class_name,
            _client.max_attempts,
        )
    return _client