"""Shared Modal error classification for the sow-analysis Modal clients.

Used by services/modal_whisper_client.py and services/modal_forced_aligner_client.py
so both clients agree on which failures are worth retrying.
"""

from __future__ import annotations

import grpclib
import modal.exception as modal_exception

# Only classes that exist in modal.exception (verified in modal 1.2.x).
# NOTE: modal.exception has NO ServiceError/ResourceExhaustedError/PermissionDeniedError.
# Two subtleties verified against the modal client source:
# 1. A crashed/failed input re-raises the ORIGINAL user exception from the container
#    (container_io_manager pickles it; _process_result deserializes and re-raises it).
#    RemoteError is only the fallback when deserialization fails. So a ctranslate2
#    RuntimeError arrives as RuntimeError — the final `except Exception` in each client
#    is the contract backstop, not RemoteError.
# 2. Raw grpclib.GRPCError escapes Modal's conversion once its transport retries are
#    exhausted (grpc_utils re-raises for any status except UNAUTHENTICATED→AuthError).
#    It must be classified by gRPC status here.
RETRYABLE_MODAL_ERRORS = (
    modal_exception.ConnectionError,   # socket/transport failures
    modal_exception.TimeoutError,      # base of FunctionTimeoutError/ExecTimeoutError
    modal_exception.InternalFailure,   # crash-looped container / infra failure
    modal_exception.RemoteError,       # container exception that could not be deserialized;
                                       # bounded retries absorb transient cases. Non-transient
                                       # model errors fail after the configured MAX_ATTEMPTS.
)
RETRYABLE_GRPC_STATUSES = frozenset(
    {
        grpclib.Status.DEADLINE_EXCEEDED,
        grpclib.Status.UNAVAILABLE,
        grpclib.Status.CANCELLED,
        grpclib.Status.INTERNAL,
        grpclib.Status.UNKNOWN,
    }
)


def looks_like_oversize_payload(exc: BaseException) -> bool:
    """Blob-upload 413s surface as RemoteError/InternalFailure, not RequestSizeError."""
    message = str(exc)
    return "413" in message or "too large" in message
