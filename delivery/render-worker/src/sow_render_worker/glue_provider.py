"""Paid ElevenLabs provider stage for durable Glue jobs.

Spec: ``specs/inpaint-transition-elevenlabs-v2.md`` §2.2/§2.5/§3.2. The spine
(``glue_jobs``) has already claimed the job, revalidated the Junction, built
both canonical excerpts, gated the upload deadline and won the
``record_provider_contact`` compare-and-set, so this module starts *after*
provider contact. It must therefore never repeat an ambiguous request: only a
definitely-not-sent connection setup may use the bounded free retry policy.

The stage ends at phase ``storing-response`` with the immutable raw response in
R2 and its checksum/byte count persisted on the job; issue #318 owns everything
after that boundary.

Request shape (spec §2.2) contains only the model, seed, song ids/ranges,
numeric timing and the fixed English prompt/style lists. Song titles, Lyrics,
worship themes, locale and any Chinese text never enter it. The payload is
built from the prepared excerpts' actual seconds and the persisted
seed/beats/incoming tempo, so a short side changes both its reference range and
``conditioning_ref.end_ms`` (``min(8000, bRefMs)``).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import math
import os
import random
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Iterator, Protocol

import urllib3
from botocore.exceptions import ClientError

from sow_render_worker.db import get_glue_job, record_glue_raw_response, set_glue_phase
from sow_render_worker.glue_detail import sanitize_detail
from sow_render_worker.glue_excerpts import record_rejection, record_uploaded

# ---------------------------------------------------------------------------
# Contract constants
# ---------------------------------------------------------------------------

MODEL_ID = "music_v2_5"
UPLOAD_URL = "https://api.elevenlabs.io/v1/music/upload"
COMPOSE_URL = "https://api.elevenlabs.io/v1/music"
COMPOSE_OUTPUT_FORMAT = "pcm_44100"

CONNECT_TIMEOUT_SECONDS = 5.0
UPLOAD_READ_TIMEOUT_SECONDS = 15.0
COMPOSE_READ_TIMEOUT_SECONDS = 90.0

MAX_RESPONSE_BYTES = 32 * 1024 * 1024
MAX_GENERATION_MS = 120_000
MAX_ERROR_BODY_BYTES = 4_096
CHUNK_SIZE = 64 * 1024

# Free infrastructure retries: initial + at most three, full jitter.
PROVIDER_NOT_SENT_RETRY_CAPS_SECONDS = (1.0, 2.0, 4.0)

GLUE_PROVIDER_UNKNOWN = "GLUE_PROVIDER_UNKNOWN"
GLUE_PROVIDER_ENTITLEMENT = "GLUE_PROVIDER_ENTITLEMENT"
GLUE_PROVIDER_PLAN = "GLUE_PROVIDER_PLAN"
GLUE_COPYRIGHT_REJECTED = "GLUE_COPYRIGHT_REJECTED"
GLUE_DURATION_TOO_LONG = "GLUE_DURATION_TOO_LONG"
GLUE_WORKER_FAILED = "GLUE_WORKER_FAILED"
GLUE_AUDIO_UNAVAILABLE = "GLUE_AUDIO_UNAVAILABLE"

GLUE_TEXT = (
    "[Glue]\n"
    "{instrumental only — no vocals, no words; seamless musical transition from the "
    "outgoing verse into the incoming chorus: warm piano, ambient pads, soft cymbal "
    "swell, natural build, no hard cut}"
)
POSITIVE_STYLES = (
    "contemporary worship",
    "warm piano",
    "ambient pads",
    "soft build",
    "seamless transition",
    "great production quality",
)
NEGATIVE_STYLES = (
    "vocals",
    "singing",
    "vocal",
    "choir",
    "spoken word",
    "lyrics",
    "abrupt ending",
    "silence",
)
CONDITION_STRENGTH = "high"
CONTEXT_ADHERENCE = "high"
CONDITIONING_REFERENCE_MS = 8_000


# ---------------------------------------------------------------------------
# Typed provider failures
# ---------------------------------------------------------------------------


class GlueProviderError(RuntimeError):
    """Base typed provider failure with a stable client code and retry class."""

    code = GLUE_PROVIDER_UNKNOWN
    retry_kind = "paid"

    def __init__(self, message: str, *, provider_status: int | None = None) -> None:
        super().__init__(message)
        self.provider_status = provider_status
        self.safe_detail = " ".join(str(message).split())[:200]


class ProviderNotSent(GlueProviderError):
    """Connection setup failed; no request bytes left the process."""

    code = GLUE_WORKER_FAILED
    retry_kind = "free"


class ProviderAmbiguous(GlueProviderError):
    """Request bytes may have left; a repeat may be charged."""

    code = GLUE_PROVIDER_UNKNOWN
    retry_kind = "paid"


class ProviderEntitlement(GlueProviderError):
    """401/402/403 or a plan/permission body: operator action, no retry."""

    code = GLUE_PROVIDER_ENTITLEMENT
    retry_kind = "none"


class ProviderPlan(GlueProviderError):
    """422, or 413/415 upload format rejection: fix the plan, no retry."""

    code = GLUE_PROVIDER_PLAN
    retry_kind = "none"


class ProviderCopyright(GlueProviderError):
    """Provider copyright rejection; cached as a negative excerpt entry."""

    code = GLUE_COPYRIGHT_REJECTED
    retry_kind = "none"


class ProviderResponseTooLarge(GlueProviderError):
    """Compose stream exceeded the 32 MiB cap; nothing partial is persisted."""

    code = GLUE_PROVIDER_UNKNOWN
    retry_kind = "paid"


# ---------------------------------------------------------------------------
# Transport protocol
# ---------------------------------------------------------------------------


class ProviderTransport(Protocol):
    def upload(self, wav_path: str | Path) -> str: ...

    def compose(self, request: dict[str, Any]) -> Iterator[bytes]: ...


def _remaining_seconds(lambda_context: Any) -> float | None:
    getter = getattr(lambda_context, "get_remaining_time_in_millis", None)
    if not callable(getter):
        return None
    try:
        return max(0.0, float(getter()) / 1000.0)
    except Exception:  # pragma: no cover - defensive around test doubles
        return None


class ElevenLabsHttpTransport:
    """Production HTTP transport: urllib3 with spec timeouts, no auto-retry."""

    def __init__(
        self,
        api_key: str,
        *,
        pool: urllib3.PoolManager | None = None,
        lambda_context: Any = None,
    ) -> None:
        if not api_key:
            raise ValueError("ElevenLabsHttpTransport requires an API key")
        self._api_key = api_key
        self._pool = pool or urllib3.PoolManager()
        self._lambda_context = lambda_context

    # -- timeouts ---------------------------------------------------------

    def _timeout(self, seconds: float) -> urllib3.Timeout:
        remaining = _remaining_seconds(self._lambda_context)
        if remaining is not None:
            seconds = min(seconds, max(0.001, remaining))
        return urllib3.Timeout(total=seconds, connect=min(CONNECT_TIMEOUT_SECONDS, seconds))

    # -- calls ------------------------------------------------------------

    def upload(self, wav_path: str | Path) -> str:
        path = Path(wav_path)
        try:
            body = path.read_bytes()
        except OSError as exc:
            raise ProviderNotSent("Excerpt audio could not be read") from exc
        encoded, content_type = urllib3.filepost.encode_multipart_formdata(
            [("file", (path.name, body, "audio/wav"))]
        )
        deadline = time.monotonic() + UPLOAD_READ_TIMEOUT_SECONDS
        response = self._send(
            "POST",
            UPLOAD_URL,
            body=encoded,
            headers={
                "xi-api-key": self._api_key,
                "Content-Type": content_type,
                "Accept": "application/json",
            },
            timeout=self._timeout(UPLOAD_READ_TIMEOUT_SECONDS),
            preload_content=False,
        )
        try:
            with contextlib.closing(self._iter_response(response, deadline)) as chunks:
                data = bytearray()
                for chunk in chunks:
                    data.extend(chunk)
                    if len(data) > MAX_ERROR_BODY_BYTES:
                        if response.status >= 400:
                            break
                        raise ProviderAmbiguous("Upload response exceeded its size limit")
            if response.status >= 400:
                raise _error_for_response(
                    response.status, bytes(data[:MAX_ERROR_BODY_BYTES]).decode("utf-8", "replace"), "upload"
                )
            try:
                payload = json.loads(data.decode("utf-8"))
            except (AttributeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ProviderAmbiguous(
                    "Upload response was not JSON", provider_status=response.status
                ) from exc
            song_id = payload.get("song_id") if isinstance(payload, dict) else None
            if not isinstance(song_id, str) or not song_id:
                raise ProviderAmbiguous(
                    "Upload response had no song id", provider_status=response.status
                )
            return song_id
        finally:
            response.release_conn()

    def compose(self, request: dict[str, Any]) -> Iterator[bytes]:
        deadline = time.monotonic() + COMPOSE_READ_TIMEOUT_SECONDS
        body = json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        response = self._send(
            "POST",
            f"{COMPOSE_URL}?output_format={COMPOSE_OUTPUT_FORMAT}",
            body=body,
            headers={
                "xi-api-key": self._api_key,
                "Content-Type": "application/json",
                "Accept": "application/octet-stream",
            },
            timeout=self._timeout(COMPOSE_READ_TIMEOUT_SECONDS),
            preload_content=False,
        )
        if response.status >= 400:
            try:
                raise _error_for_response(response.status, _response_text(response), "compose")
            finally:
                response.release_conn()
        return self._iter_response(response, deadline)

    def _iter_response(self, response: Any, deadline: float) -> Iterator[bytes]:
        try:
            while True:
                try:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise ProviderAmbiguous("Provider response exceeded its deadline")
                    sock = getattr(getattr(response, "connection", None), "sock", None)
                    if sock is not None:
                        sock.settimeout(remaining)
                    read = getattr(response, "read1", response.read)
                    chunk = read(CHUNK_SIZE)
                except urllib3.exceptions.HTTPError as exc:
                    raise ProviderAmbiguous("Provider response read failed") from exc
                except OSError as exc:
                    raise ProviderAmbiguous("Provider response connection dropped") from exc
                if not chunk:
                    return
                yield chunk
        except BaseException:
            if hasattr(response, "close"):
                response.close()
            raise
        finally:
            response.release_conn()

    def _send(
        self,
        method: str,
        url: str,
        *,
        body: bytes,
        headers: dict[str, str],
        timeout: urllib3.Timeout,
        preload_content: bool,
    ) -> Any:
        try:
            return self._pool.urlopen(
                method,
                url,
                body=body,
                headers=headers,
                timeout=timeout,
                retries=False,
                preload_content=preload_content,
                decode_content=False,
            )
        except (
            urllib3.exceptions.NewConnectionError,
            urllib3.exceptions.ConnectTimeoutError,
            urllib3.exceptions.SSLError,
            urllib3.exceptions.NameResolutionError,
        ) as exc:
            raise ProviderNotSent("Could not connect to ElevenLabs") from exc
        except urllib3.exceptions.HTTPError as exc:
            raise ProviderAmbiguous("ElevenLabs request outcome is unknown") from exc
        except OSError as exc:
            raise ProviderAmbiguous("ElevenLabs connection dropped") from exc


def _response_text(response: Any) -> str:
    try:
        data = response.data
    except Exception:  # pragma: no cover - defensive
        data = b""
    if not data:
        try:
            data = response.read(MAX_ERROR_BODY_BYTES)
        except Exception:  # pragma: no cover - defensive
            data = b""
    if isinstance(data, bytes):
        return data[:MAX_ERROR_BODY_BYTES].decode("utf-8", "replace")
    return str(data)[:MAX_ERROR_BODY_BYTES]


_MAX_PROVIDER_DETAIL_CHARS = 200


def _provider_detail(body: str) -> str | None:
    """The provider's own rejection sentence, bounded for logs and error_detail."""
    text = " ".join(body.split()) if body else ""
    if not text:
        return None
    try:
        payload = json.loads(body)
    except (TypeError, ValueError):
        return text[:_MAX_PROVIDER_DETAIL_CHARS] or None

    messages: list[str] = []

    def absorb(value: object) -> None:
        if isinstance(value, str):
            if value.strip():
                messages.append(" ".join(value.split()))
        elif isinstance(value, dict):
            for key in ("msg", "message", "code"):
                absorb(value.get(key))
            absorb(value.get("detail"))
        elif isinstance(value, list):
            for item in value:
                absorb(item)

    if isinstance(payload, dict):
        for key in ("detail", "message", "error"):
            absorb(payload.get(key))
    else:
        absorb(payload)
    joined = "; ".join(dict.fromkeys(messages))
    return joined[:_MAX_PROVIDER_DETAIL_CHARS] or text[:_MAX_PROVIDER_DETAIL_CHARS]


def _error_for_response(status: int, body: str, operation: str) -> GlueProviderError:
    """Map one HTTP response to the spec's typed failure taxonomy."""
    lowered = body.lower()
    message = f"ElevenLabs {operation} failed with HTTP {status}"
    detail = _provider_detail(body)
    if detail is not None:
        message = f"{message}: {detail}"[:400]
    if status in (401, 402, 403) or "missing_permissions" in lowered or "upgrade" in lowered:
        return ProviderEntitlement(message, provider_status=status)
    if "copyright" in lowered:
        return ProviderCopyright(message, provider_status=status)
    if status in (413, 415, 422):
        # 413/415 are upload format rejections: actionable, never an automatic
        # MP3 fallback (a later fallback needs a new spec for charged ambiguity).
        return ProviderPlan(message, provider_status=status)
    # 429, 5xx and every other response may have been charged: never repeat.
    return ProviderAmbiguous(message, provider_status=status)


# ---------------------------------------------------------------------------
# Compose request
# ---------------------------------------------------------------------------


def generation_duration_ms(beats: int, to_tempo_bpm: float) -> int:
    """Glue length in beats of the incoming tempo, floored at the API minimum."""
    if isinstance(beats, bool) or not isinstance(beats, int) or beats <= 0:
        raise ValueError(f"Glue beats must be a positive integer; received {beats!r}")
    if isinstance(to_tempo_bpm, bool) or not isinstance(to_tempo_bpm, (int, float)):
        raise TypeError("Glue incoming tempo must be numeric")
    tempo = float(to_tempo_bpm)
    if tempo <= 0:
        raise ValueError(f"Glue incoming tempo must be positive; received {to_tempo_bpm!r}")
    # Half-up milliseconds, matching the repository's canonical rounding (never
    # Python's half-even round) so the request is deterministic.
    return int(math.floor(max(3.0, beats * 60.0 / tempo) * 1000 + 0.5))


def build_compose_request(
    *,
    seed: int,
    beats: int,
    to_tempo_bpm: float,
    a_song_id: str,
    a_ref_ms: int,
    b_song_id: str,
    b_ref_ms: int,
) -> dict[str, Any]:
    """Build the exact spec §2.2 compose body for one paid attempt.

    Raises ``ValueError`` for a generation over 120 s so the caller rejects it
    before any provider spend.
    """
    if not isinstance(a_song_id, str) or not a_song_id:
        raise ValueError("Compose request requires the outgoing excerpt song id")
    if not isinstance(b_song_id, str) or not b_song_id:
        raise ValueError("Compose request requires the incoming excerpt song id")
    for label, value in (("a_ref_ms", a_ref_ms), ("b_ref_ms", b_ref_ms)):
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"Compose request requires a positive {label}")

    duration_ms = generation_duration_ms(beats, to_tempo_bpm)
    if duration_ms > MAX_GENERATION_MS:
        raise ValueError(
            f"Glue generation {duration_ms} ms exceeds the {MAX_GENERATION_MS} ms limit"
        )

    return {
        "model_id": MODEL_ID,
        "seed": int(seed),
        "composition_plan": {
            "chunks": [
                {
                    "song_id": a_song_id,
                    "range": {"start_ms": 0, "end_ms": int(a_ref_ms)},
                },
                {
                    "text": GLUE_TEXT,
                    "duration_ms": duration_ms,
                    "positive_styles": list(POSITIVE_STYLES),
                    "negative_styles": list(NEGATIVE_STYLES),
                    "context_adherence": CONTEXT_ADHERENCE,
                    "conditioning_ref": {
                        "song_id": b_song_id,
                        "range": {
                            "start_ms": 0,
                            "end_ms": min(CONDITIONING_REFERENCE_MS, int(b_ref_ms)),
                        },
                    },
                    "condition_strength": CONDITION_STRENGTH,
                },
                {
                    "song_id": b_song_id,
                    "range": {"start_ms": 0, "end_ms": int(b_ref_ms)},
                },
            ]
        },
    }


# ---------------------------------------------------------------------------
# Raw-response persistence
# ---------------------------------------------------------------------------


_PRECONDITION_CODES = frozenset({"PreconditionFailed", "ConditionalRequestConflict", "409"})


class RawResponseStore(Protocol):
    def put_if_absent(self, key: str, source: Path) -> bool:
        """Store immutable bytes, or confirm existing bytes are identical.

        Returns True when the object now holds exactly ``source``'s bytes —
        either because this call wrote them or because an earlier attempt
        already did. False means a different object is in the way, which must
        never be overwritten.
        """


class R2RawResponseStore:
    """Immutable raw-response writer over the worker's R2 client."""

    def __init__(self, r2_client: Any = None) -> None:
        if r2_client is None:
            from sow_render_worker.r2_client import create_r2_client_from_env

            r2_client = create_r2_client_from_env()
        self._client = r2_client.client
        self._bucket_name = r2_client.bucket_name

    def put_if_absent(self, key: str, source: Path) -> bool:
        try:
            with Path(source).open("rb") as handle:
                self._client.put_object(
                    Bucket=self._bucket_name,
                    Key=key,
                    Body=handle,
                    ContentType="application/octet-stream",
                    CacheControl="private, max-age=0",
                    IfNoneMatch="*",
                )
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code not in _PRECONDITION_CODES:
                raise
            return self._matches(key, Path(source))
        return True

    def _matches(self, key: str, source: Path) -> bool:
        """True only when the stored object is byte-identical to ``source``."""
        expected_size = source.stat().st_size
        head = self._client.head_object(Bucket=self._bucket_name, Key=key)
        if int(head.get("ContentLength", -1)) != expected_size:
            return False
        response = self._client.get_object(Bucket=self._bucket_name, Key=key)
        body = response["Body"]
        digest = hashlib.sha256()
        total = 0
        try:
            for chunk in iter(lambda: body.read(CHUNK_SIZE), b""):
                total += len(chunk)
                if total > expected_size:
                    return False
                digest.update(chunk)
        finally:
            close = getattr(body, "close", None)
            if callable(close):
                close()
        if total != expected_size:
            return False
        return digest.digest() == hashlib.sha256(source.read_bytes()).digest()


def raw_response_key(job: Any) -> str:
    return f"glues/{job.user_id}/jobs/{job.id}/response.pcm"


def _stream_to_file(
    chunks: Iterator[bytes],
    *,
    work_dir: Path,
    max_bytes: int,
) -> tuple[Path, int, str]:
    """Stream a compose response to a temp file under the cap.

    On overflow the partial temp file is deleted and nothing is returned, so
    no provider bytes are ever persisted for an oversized response.
    """
    handle = tempfile.NamedTemporaryFile(
        dir=work_dir, prefix="raw-response-", suffix=".pcm", delete=False
    )
    target = Path(handle.name)
    digest = hashlib.sha256()
    total = 0
    try:
        with handle:
            for chunk in chunks:
                if not isinstance(chunk, (bytes, bytearray)):
                    raise ProviderAmbiguous("Provider stream returned a non-byte chunk")
                # Refuse the chunk that would exceed the cap: nothing past the
                # limit is ever written or hashed, so no partial object exists.
                if total + len(chunk) > max_bytes:
                    # No HTTP status here: a 200 that produced unusable bytes
                    # must not be recorded as a successful provider response.
                    raise ProviderResponseTooLarge(
                        f"ElevenLabs response exceeded {max_bytes} bytes"
                    )
                total += len(chunk)
                digest.update(chunk)
                handle.write(chunk)
        if total == 0:
            raise ProviderAmbiguous("ElevenLabs returned an empty response")
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    return target, total, digest.hexdigest()


# ---------------------------------------------------------------------------
# Stage
# ---------------------------------------------------------------------------


def _glue_job_error(spine: Any, code: str, retry_kind: str, detail: str) -> Exception:
    return spine.GlueJobError(code, retry_kind, detail)


def _excerpt_sha(spine: Any, excerpt: Any) -> str:
    if excerpt.audio_sha256:
        return str(excerpt.audio_sha256)
    path = excerpt.wav_path
    if path is None:
        raise _glue_job_error(
            spine, GLUE_WORKER_FAILED, "free", f"Excerpt side {excerpt.side} has no audio"
        )
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _job_timing(spine: Any, job: Any) -> tuple[int, float]:
    """Read the persisted canonical beats/tempo from the job descriptor."""
    try:
        descriptor = json.loads(job.descriptor_json or "")
        beats = descriptor["beats"]
        tempo = descriptor["toTempoBpm"]
    except (TypeError, ValueError, KeyError) as exc:
        raise _glue_job_error(
            spine, GLUE_WORKER_FAILED, "free", "Stored descriptor is unreadable"
        ) from exc
    if not isinstance(beats, int) or isinstance(beats, bool) or beats <= 0:
        raise _glue_job_error(
            spine, GLUE_WORKER_FAILED, "free", "Stored descriptor beats are invalid"
        )
    if not isinstance(tempo, (int, float)) or isinstance(tempo, bool) or tempo <= 0:
        raise _glue_job_error(
            spine, GLUE_WORKER_FAILED, "free", "Stored descriptor tempo is invalid"
        )
    return beats, float(tempo)


def _reference_ms(spine: Any, excerpt: Any) -> int:
    seconds = getattr(excerpt, "seconds", None)
    if not isinstance(seconds, (int, float)) or isinstance(seconds, bool) or seconds <= 0:
        raise _glue_job_error(
            spine,
            GLUE_AUDIO_UNAVAILABLE,
            "free",
            f"Excerpt side {excerpt.side} has no usable seconds",
        )
    return int(math.floor(float(seconds) * 1000 + 0.5))


def _pre_request_heartbeat(
    spine: Any,
    conn: Any,
    job: Any,
    log_event: Callable[..., None],
    *,
    stage: str,
) -> None:
    """Heartbeat a stage boundary that precedes any provider request.

    A failure here is still definitely-unsent work: it must map to the free
    retry class (same-job resume), never to the paid ambiguity class.
    """
    try:
        spine._heartbeat(conn, job, log_event, stage=stage)
    except Exception as exc:
        raise _glue_job_error(
            spine, GLUE_WORKER_FAILED, "free", f"Heartbeat failed before {stage}"
        ) from exc


def _upload_with_budget(spine, context, transport, path, pending_uploads):
    spine.require_deadline(context, "upload", pending_uploads=pending_uploads)
    return transport.upload(path)


def _retry_not_sent(
    operation: Callable[[], Any],
    *,
    label: str,
    log_event: Callable[..., None],
    sleep: Callable[[float], None],
    retry_allowed: bool = True,
) -> Any:
    """Bounded retries for a request that provably never left the process."""
    attempt = 0
    while True:
        try:
            return operation()
        except ProviderNotSent as exc:
            if not retry_allowed or attempt >= len(PROVIDER_NOT_SENT_RETRY_CAPS_SECONDS):
                raise
            cap = PROVIDER_NOT_SENT_RETRY_CAPS_SECONDS[attempt]
            delay = random.uniform(0, cap)
            attempt += 1
            log_event(
                "glue.retry",
                level="warning",
                operation=label,
                retryCount=attempt,
                delayMs=round(delay * 1000, 3),
                errorType=type(exc).__name__,
            )
            sleep(delay)


def create_provider_from_env(
    *,
    lambda_context: Any = None,
    api_key: str | None = None,
) -> ProviderTransport:
    """The production transport from configuration; never a network call.

    A missing key raises ``GlueConfigurationError`` (``GLUE_NOT_CONFIGURED``)
    so paid work is never reserved without a provider. This is the single seam
    where the real client is chosen; tests and journeys inject a transport.
    """
    key = api_key or os.environ.get("SOW_ELEVENLABS_API_KEY")
    if not key:
        from sow_render_worker.glue_jobs import GlueConfigurationError

        raise GlueConfigurationError("SOW_ELEVENLABS_API_KEY is not configured")
    return ElevenLabsHttpTransport(key, lambda_context=lambda_context)


def create_provider_stage(
    *,
    lambda_context: Any = None,
    transport: ProviderTransport | None = None,
    raw_store: RawResponseStore | None = None,
    sleep: Callable[[float], None] | None = None,
    api_key: str | None = None,
    max_response_bytes: int = MAX_RESPONSE_BYTES,
) -> Callable[..., None]:
    """Return the production ``provider_stage`` callable.

    ``transport`` and ``raw_store`` are injectable so every test runs with
    fakes and no network. Without a transport the production client comes from
    ``create_provider_from_env``; a missing key is a configuration failure.
    """
    if transport is None:
        transport = create_provider_from_env(lambda_context=lambda_context, api_key=api_key)
    if raw_store is None:
        raw_store = R2RawResponseStore()
    nap = sleep or time.sleep

    def provider_stage(
        job: Any,
        conn: Any,
        *,
        a_excerpt: Any,
        b_excerpt: Any,
        work_dir: Path,
        log_event: Callable[..., None],
    ) -> None:
        from sow_render_worker import glue_jobs as spine

        _run_stage(
            spine,
            job,
            conn,
            a_excerpt=a_excerpt,
            b_excerpt=b_excerpt,
            work_dir=Path(work_dir),
            log_event=log_event,
            transport=transport,
            raw_store=raw_store,
            lambda_context=lambda_context,
            sleep=nap,
            max_response_bytes=max_response_bytes,
        )

    return provider_stage


def _run_stage(
    spine: Any,
    job: Any,
    conn: Any,
    *,
    a_excerpt: Any,
    b_excerpt: Any,
    work_dir: Path,
    log_event: Callable[..., None],
    transport: ProviderTransport,
    raw_store: RawResponseStore,
    lambda_context: Any,
    sleep: Callable[[float], None],
    max_response_bytes: int,
) -> None:
    log_event("glue.provider.stage.started", phase=job.phase, cacheHit=False)

    # A durable response — including one a resumed job persisted before an
    # interrupted phase advance — is free staging recovery; never compose twice.
    current = get_glue_job(conn, job.id, job.user_id)
    if current is not None and current.raw_response_r2_key:
        log_event("glue.response.persisted", phase="storing-response", status="already-persisted")
        set_glue_phase(conn, job.id, job.user_id, "storing-response")
        return

    beats, tempo = _job_timing(spine, job)
    try:
        duration_ms = generation_duration_ms(beats, tempo)
    except (TypeError, ValueError) as exc:
        raise _glue_job_error(spine, GLUE_WORKER_FAILED, "free", str(exc)) from exc
    if duration_ms > MAX_GENERATION_MS:
        # The job route rejects this before reservation; a persisted descriptor
        # that exceeds the limit is an invariant failure, never a paid attempt.
        raise _glue_job_error(
            spine,
            GLUE_DURATION_TOO_LONG,
            "none",
            f"Glue generation {duration_ms} ms exceeds {MAX_GENERATION_MS} ms",
        )

    song_ids: dict[str, str] = {}
    pending_uploads = sum(
        not excerpt.elevenlabs_song_id for excerpt in (a_excerpt, b_excerpt)
    )
    for excerpt in (a_excerpt, b_excerpt):
        side = excerpt.side
        if excerpt.rejection_code == GLUE_COPYRIGHT_REJECTED:
            log_event(
                "glue.provider.failed",
                level="warning",
                phase="uploading",
                errorCode=GLUE_COPYRIGHT_REJECTED,
                cacheHit=True,
            )
            raise _glue_job_error(
                spine,
                GLUE_COPYRIGHT_REJECTED,
                "none",
                f"Excerpt side {side} was rejected for copyright",
            )
        if excerpt.elevenlabs_song_id:
            song_ids[side] = excerpt.elevenlabs_song_id
            log_event(
                "glue.provider.upload.succeeded",
                phase="uploading",
                cacheHit=True,
                status="cached",
            )
            continue
        if excerpt.wav_path is None or not Path(excerpt.wav_path).exists():
            raise _glue_job_error(
                spine,
                GLUE_AUDIO_UNAVAILABLE,
                "free",
                f"Excerpt side {side} has no canonical audio",
            )

        # Deadline before the in-flight phase: a refusal here is definitely
        # unsent work and must not be mistaken for an ambiguous upload later.
        _pre_request_heartbeat(spine, conn, job, log_event, stage=f"upload-{side.lower()}")
        spine.require_deadline(lambda_context, "upload", pending_uploads=pending_uploads)
        updated = set_glue_phase(
            conn, job.id, job.user_id, "uploading", provider_phase=f"upload-{side.lower()}"
        )
        if updated is None:
            # The job is no longer active: cancellation won before any request.
            # Leave the terminal row untouched and send nothing.
            log_event(
                "glue.job.cancelled",
                phase="uploading",
                status="no-provider-call",
            )
            return

        try:
            song_id = _retry_not_sent(
                lambda: _upload_with_budget(
                    spine, lambda_context, transport, excerpt.wav_path, pending_uploads
                ),
                label=f"upload_{side}",
                log_event=log_event,
                sleep=sleep,
                retry_allowed=lambda_context is None,
            )
        except GlueProviderError as exc:
            _record_provider_status(conn, job, "uploading", exc.provider_status, log_event)
            # One sanitized sentence for every sink: the negative cache, this
            # log line, and the terminal `glue_jobs.error_detail`. A provider
            # body carrying a URL, path, or key word falls back to the code.
            detail = sanitize_detail(exc.safe_detail, fallback=exc.code)
            if isinstance(exc, ProviderCopyright):
                # A copyright verdict for this exact excerpt is a successful
                # negative cache entry: the identical audio can never incur
                # another half-fee upload.
                record_rejection(
                    conn,
                    job.user_id,
                    logical_key=excerpt.logical_key,
                    side=side,
                    seconds=excerpt.seconds,
                    content_hash=excerpt.content_hash,
                    canonical_steps_json=excerpt.canonical_steps_json,
                    audio_sha256=_excerpt_sha(spine, excerpt),
                    rejection_code=GLUE_COPYRIGHT_REJECTED,
                    safe_failure_detail=detail,
                )
            log_event(
                "glue.provider.ambiguous" if exc.retry_kind == "paid" else "glue.provider.failed",
                level="warning",
                phase="uploading",
                providerStatus=exc.provider_status,
                providerDetail=detail,
                errorCode=exc.code,
            )
            raise _glue_job_error(spine, exc.code, exc.retry_kind, detail) from exc

        # Persist the provider song id before any compose can reference it.
        record_uploaded(
            conn,
            job.user_id,
            logical_key=excerpt.logical_key,
            side=side,
            seconds=excerpt.seconds,
            content_hash=excerpt.content_hash,
            canonical_steps_json=excerpt.canonical_steps_json,
            audio_sha256=_excerpt_sha(spine, excerpt),
            elevenlabs_song_id=song_id,
        )
        song_ids[side] = song_id
        pending_uploads -= 1
        log_event(
            "glue.provider.upload.succeeded",
            phase="uploading",
            cacheHit=False,
            status="uploaded",
        )

    request = build_compose_request(
        seed=int(job.seed),
        beats=beats,
        to_tempo_bpm=tempo,
        a_song_id=song_ids["A"],
        a_ref_ms=_reference_ms(spine, a_excerpt),
        b_song_id=song_ids["B"],
        b_ref_ms=_reference_ms(spine, b_excerpt),
    )

    # Heartbeat, then the full compose reserve *before* entering the in-flight
    # phase: a refusal here is still definitely-unsent work (free resume) and
    # must not look like an ambiguous compose on redelivery.
    _pre_request_heartbeat(spine, conn, job, log_event, stage="composing")
    spine.require_deadline(lambda_context, "compose")
    updated = set_glue_phase(conn, job.id, job.user_id, "composing", provider_phase="compose")
    if updated is None:
        # Cancelled before the compose request was sent: no compose, no rewrite
        # of the already-terminal job.
        log_event("glue.job.cancelled", phase="composing", status="no-provider-call")
        return

    work_dir.mkdir(parents=True, exist_ok=True)
    raw_path: Path | None = None
    try:
        try:
            # Exactly one compose per attempt: never retried, ever.
            raw_path, byte_count, sha256 = _stream_to_file(
                transport.compose(request), work_dir=work_dir, max_bytes=max_response_bytes
            )
        except GlueProviderError as exc:
            _record_provider_status(conn, job, "composing", exc.provider_status, log_event)
            detail = sanitize_detail(exc.safe_detail, fallback=exc.code)
            log_event(
                "glue.provider.ambiguous" if exc.retry_kind == "paid" else "glue.provider.failed",
                level="warning",
                phase="composing",
                providerStatus=exc.provider_status,
                providerDetail=detail,
                errorCode=exc.code,
            )
            raise _glue_job_error(spine, exc.code, exc.retry_kind, detail) from exc

        key = raw_response_key(job)
        # The raw object is a free infrastructure write: bounded retries are
        # safe here because the bytes are already ours and the key is
        # deterministic (the store verifies identity on a collision).
        stored = spine._free_retry(
            lambda: raw_store.put_if_absent(key, raw_path),
            label="persist_raw_response",
            log=log_event,
        )
        fresh = get_glue_job(conn, job.id, job.user_id)
        if fresh is not None and fresh.raw_response_r2_key:
            # Another delivery already stored and recorded this job's one
            # response. Never overwrite the immutable object.
            log_event(
                "glue.response.persisted",
                phase="storing-response",
                status="already-persisted",
            )
            set_glue_phase(conn, job.id, job.user_id, "storing-response")
            return
        if not stored:
            # The immutable key holds different bytes and is never overwritten:
            # this job's own response cannot be proven, so the outcome stays
            # unknown (paid) and needs an explicit user retry.
            raise _glue_job_error(
                spine,
                GLUE_PROVIDER_UNKNOWN,
                "paid",
                "A different raw response is already stored for this job",
            )

        # Checksum and exact byte count are durable before any staging work.
        recorded = record_glue_raw_response(
            conn,
            job.id,
            job.user_id,
            r2_key=key,
            sha256=sha256,
            byte_count=byte_count,
        )
        if recorded is None:
            fresh = get_glue_job(conn, job.id, job.user_id)
            if fresh is None or not fresh.raw_response_r2_key:
                raise _glue_job_error(
                    spine,
                    GLUE_WORKER_FAILED,
                    "paid",
                    "Raw response metadata could not be recorded",
                )
    finally:
        if raw_path is not None:
            raw_path.unlink(missing_ok=True)

    updated = set_glue_phase(conn, job.id, job.user_id, "storing-response", provider_status=200)
    if updated is None:
        raise _glue_job_error(spine, GLUE_PROVIDER_UNKNOWN, "paid", "Glue job is no longer active")
    log_event(
        "glue.response.persisted",
        phase="storing-response",
        providerStatus=200,
        status="stored",
    )


def _record_provider_status(
    conn: Any,
    job: Any,
    phase: str,
    status: int | None,
    log_event: Callable[..., None],
) -> None:
    """Persist the provider HTTP status for diagnostics; never masks a failure."""
    if status is None:
        return
    try:
        set_glue_phase(conn, job.id, job.user_id, phase, provider_status=int(status))
    except Exception:
        log_event(
            "glue.provider.status.unrecorded",
            level="warning",
            phase=phase,
            providerStatus=status,
        )


__all__ = [
    "COMPOSE_READ_TIMEOUT_SECONDS",
    "CONNECT_TIMEOUT_SECONDS",
    "ElevenLabsHttpTransport",
    "GLUE_COPYRIGHT_REJECTED",
    "GLUE_PROVIDER_ENTITLEMENT",
    "GLUE_PROVIDER_PLAN",
    "GLUE_PROVIDER_UNKNOWN",
    "GlueProviderError",
    "MAX_GENERATION_MS",
    "MAX_RESPONSE_BYTES",
    "NEGATIVE_STYLES",
    "POSITIVE_STYLES",
    "GLUE_TEXT",
    "ProviderAmbiguous",
    "ProviderCopyright",
    "ProviderEntitlement",
    "ProviderNotSent",
    "ProviderPlan",
    "ProviderResponseTooLarge",
    "ProviderTransport",
    "R2RawResponseStore",
    "RawResponseStore",
    "UPLOAD_READ_TIMEOUT_SECONDS",
    "build_compose_request",
    "create_provider_from_env",
    "create_provider_stage",
    "generation_duration_ms",
    "raw_response_key",
]
