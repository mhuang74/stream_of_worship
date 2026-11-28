"""Free staging after a paid provider response is already stored.

This module is the post-provider half of a Glue job: it never calls
ElevenLabs. It verifies the persisted raw response byte-for-byte, removes the
requested A/Glue/B geometry, measures the seam against the reference excerpts,
applies the bounded edge ramps, writes one immutable staged WAV, and then
records the ready take and (when the junction is still current) the selection
in a single transaction.

Failures are free: a decode/storage fault finishes the job ``failed`` with
``retry_kind='free'`` so ``retry-processing`` can replay staging from the
verified raw bytes. An unverifiable raw response is terminal and is never
turned into a second compose request.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np
import soundfile as sf

from sow_render_worker import glue_jobs as spine
from sow_render_worker.db import (
    GLUE_STAGING_PHASES,
    insert_glue_take_if_absent,
    list_glue_takes_for_descriptor,
    read_songset_items_for_glue,
    select_glue_take_for_junction,
    select_glue_take_for_boundary,
    finish_glue_job_ready,
    get_glue_job,
)
from sow_render_worker.glue_descriptor import canonical_six_dp, milliseconds_to_frames
from sow_render_worker.glue_provider import generation_duration_ms

SAMPLE_RATE = 44_100
CHANNELS = 2
BYTES_PER_SAMPLE = 2
PCM16_MAX = 32767

# The API may return one sample more/less than the requested geometry; this is
# the spec's 0.1 s tolerance, expressed in frames plus one rounding frame.
GEOMETRY_TOLERANCE_SECONDS = 0.1
MIN_RESPONSE_SHORTFALL_SECONDS = 0.1
ENVELOPE_BLOCK_SECONDS = 0.05
ENVELOPE_REFERENCE_SECONDS = 3.0
ENVELOPE_OK_FLOOR = 0.90
EDGE_SECONDS = 0.2
EDGE_GAIN_LIMIT_DB = 18.0
PEAK_LIMIT = 0.99
FADE_SECONDS = 0.02
MIN_GENERATED_RMS_DBFS = -50.0
TARGET_EXCERPT_SECONDS = 12.0

GLUE_WORKER_FAILED = "GLUE_WORKER_FAILED"

_PRECONDITION_CODES = frozenset(
    {"PreconditionFailed", "ConditionalRequestConflict", "409"}
)

logger = __import__("logging").getLogger(__name__)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


def _worker_error(detail: str, retry_kind: str = "free") -> spine.GlueJobError:
    """A typed staging failure in the shared worker vocabulary."""
    return spine.GlueJobError(GLUE_WORKER_FAILED, retry_kind, detail)


class RawResponseUnverified(Exception):
    """The persisted provider response cannot be trusted; never retry compose."""


class StagingGeometryError(Exception):
    """The provider response does not match the requested A/Glue/B geometry."""


# ---------------------------------------------------------------------------
# Persistence seam
# ---------------------------------------------------------------------------


def _socket_attached(body: Any) -> bool:
    """True while the body's urllib3 stream still holds its socket chain."""
    raw = getattr(body, "_raw_stream", None)
    return getattr(getattr(raw, "_fp", None), "fp", None) is not None


def set_read_timeout(body: Any, seconds: float) -> None:
    """Bound the next read of an R2 body, if that body still owns a socket.

    urllib3 drops ``HTTPResponse._fp.fp`` once the body reaches its end, and
    ``botocore.response.StreamingBody.set_socket_timeout`` logs a full ERROR
    record with a stack trace before re-raising the resulting AttributeError.
    Check that same chain first so a finished body never reaches botocore.
    """
    setter = getattr(body, "set_socket_timeout", None)
    if setter is None or not _socket_attached(body):
        return
    try:
        setter(seconds)
    except AttributeError:
        return


class R2StagingStore:
    """Small R2 adapter used by staging (production and tests).

    ``put_take`` is conditional (``If-None-Match: *``) so a replay can never
    overwrite a different object at the immutable take key. A lost race is
    accepted only after the stored bytes verify against this run's WAV.
    """

    def __init__(self, r2_client: Any = None, *, deadline: float | None = None) -> None:
        if r2_client is None:
            from sow_render_worker.r2_client import create_r2_client_from_env

            r2_client = create_r2_client_from_env(bounded=deadline is not None)
        self._client = r2_client.client
        self._bucket_name = r2_client.bucket_name
        self._deadline = deadline

    def _time_left(self) -> float:
        remaining = float("inf") if self._deadline is None else self._deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Glue staging deadline reached")
        return remaining

    def read_raw(self, job: Any) -> bytes:
        """Read the raw response and verify length + sha256 before decoding.

        A metadata mismatch is terminal (``RawResponseUnverified``). A
        transient R2 read error propagates as a generic exception so the
        caller's bounded infrastructure retry can retry it.
        """
        key = job.raw_response_r2_key
        expected_sha = job.raw_response_sha256
        expected_bytes = job.raw_response_bytes
        if not key or not expected_sha or not expected_bytes:
            raise RawResponseUnverified(
                "The stored provider response is missing its key, checksum, or byte count"
            )
        expected_bytes = int(expected_bytes)
        self._time_left()
        response = self._client.get_object(Bucket=self._bucket_name, Key=str(key))
        body = response["Body"]
        digest = hashlib.sha256()
        chunks: list[bytes] = []
        total = 0
        try:
            while True:
                if self._deadline is not None:
                    set_read_timeout(body, min(5, self._time_left()))
                chunk = body.read(64 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > expected_bytes:
                    raise RawResponseUnverified(
                        "The stored provider response is larger than recorded"
                    )
                digest.update(chunk)
                chunks.append(chunk)
        finally:
            close = getattr(body, "close", None)
            if callable(close):
                close()
        if total != expected_bytes:
            raise RawResponseUnverified(
                "The stored provider response size does not match its recorded byte count"
            )
        if digest.hexdigest() != str(expected_sha):
            raise RawResponseUnverified(
                "The stored provider response checksum does not match its record"
            )
        return b"".join(chunks)

    def put_take(self, key: str, wav_bytes: bytes) -> None:
        """Write the immutable staged WAV, then verify the stored bytes."""
        import botocore.exceptions

        self._time_left()
        try:
            self._client.put_object(
                Bucket=self._bucket_name,
                Key=key,
                Body=wav_bytes,
                ContentType="audio/wav",
                CacheControl="private, max-age=31536000, immutable",
                IfNoneMatch="*",
            )
        except botocore.exceptions.ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            if code not in _PRECONDITION_CODES and status not in (409, 412):
                raise
        existing = self._read_object(key)
        if hashlib.sha256(existing).hexdigest() != hashlib.sha256(wav_bytes).hexdigest():
            raise OSError("Staged WAV verification failed after upload")

    def _read_object(self, key: str) -> bytes:
        self._time_left()
        response = self._client.get_object(Bucket=self._bucket_name, Key=key)
        body = response["Body"]
        try:
            if self._deadline is not None:
                chunks = []
                while True:
                    set_read_timeout(body, min(5, self._time_left()))
                    chunk = body.read(64 * 1024)
                    if not chunk:
                        return b"".join(chunks)
                    chunks.append(chunk)
            return body.read()
        finally:
            close = getattr(body, "close", None)
            if callable(close):
                close()


# ---------------------------------------------------------------------------
# Decoding and geometry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReferenceAudio:
    """Measured reference excerpts, when the source recordings were fetched."""

    a: Optional[np.ndarray] = None
    b: Optional[np.ndarray] = None
    a_seconds: Optional[float] = None
    b_seconds: Optional[float] = None


@dataclass(frozen=True)
class PreparedTake:
    wav_bytes: bytes
    seconds: float
    status: str
    reasons: tuple[str, ...]
    metrics: dict[str, Any]


def decode_stereo_pcm16(raw: bytes, *, headerless: bool = False) -> np.ndarray:
    """Decode RIFF WAV or raw ``pcm_44100`` into an (N, 2) int16 array."""
    if len(raw) == 0:
        raise StagingGeometryError("The stored provider response is empty")
    if not headerless and raw[:4] == b"RIFF":
        samples, sample_rate = sf.read(io.BytesIO(raw), dtype="int16", always_2d=True)
        if int(sample_rate) != SAMPLE_RATE:
            raise StagingGeometryError(
                f"Provider WAV is {sample_rate} Hz; expected {SAMPLE_RATE} Hz"
            )
    else:
        if len(raw) % (CHANNELS * BYTES_PER_SAMPLE) != 0:
            raise StagingGeometryError(
                "Raw provider PCM is not aligned to stereo 16-bit frames"
            )
        samples = np.frombuffer(raw, dtype="<i2").reshape(-1, CHANNELS)
    if samples.shape[1] == 1:
        samples = np.repeat(samples, 2, axis=1)
    elif samples.shape[1] != CHANNELS:
        raise StagingGeometryError(
            f"Provider audio has {samples.shape[1]} channels; expected {CHANNELS}"
        )
    return np.ascontiguousarray(samples.astype("<i2", copy=False))


def _seconds_to_samples(seconds: float) -> int:
    return int(math.floor(seconds * SAMPLE_RATE + 0.5))


def _mono(samples: np.ndarray) -> np.ndarray:
    return np.asarray(samples, dtype=np.float64).mean(axis=1) / 32768.0


def _rms(samples: np.ndarray) -> float:
    if samples.shape[0] == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(samples))))


def _db(value: float) -> Optional[float]:
    if value <= 0.0 or not math.isfinite(value):
        return None
    return 20.0 * math.log10(value)


def _block_rms(samples: np.ndarray, block: int) -> np.ndarray:
    usable = samples.shape[0] - (samples.shape[0] % block)
    if usable <= 0:
        return np.empty(0, dtype=np.float64)
    return np.sqrt(np.mean(np.square(samples[:usable].reshape(-1, block)), axis=1))


def envelope_correlation(
    reference: np.ndarray,
    candidate: np.ndarray,
    *,
    align: str = "tail",
) -> Optional[float]:
    """50 ms RMS-block Pearson correlation (the POC's reference-envelope QA)."""
    if reference.shape[0] == 0 or candidate.shape[0] == 0:
        return None
    length = min(reference.shape[0], candidate.shape[0])
    block = _seconds_to_samples(ENVELOPE_BLOCK_SECONDS)
    if length < block * 3:
        return None
    left = reference[-length:] if align == "tail" else reference[:length]
    right = candidate[-length:] if align == "tail" else candidate[:length]
    left_blocks = _block_rms(left, block)
    right_blocks = _block_rms(right, block)
    if left_blocks.shape[0] < 3 or right_blocks.shape[0] < 3:
        return None
    if float(np.std(left_blocks)) == 0.0 or float(np.std(right_blocks)) == 0.0:
        return 0.0
    value = float(np.corrcoef(left_blocks, right_blocks)[0, 1])
    return value if math.isfinite(value) else 0.0


def _predicted_side_seconds(steps: Any, duration: Any) -> Optional[float]:
    if isinstance(steps, list) and steps:
        total = 0.0
        for entry in steps:
            if not isinstance(entry, (list, tuple)) or len(entry) != 2:
                return None
            start, end = entry
            if not all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in (start, end)):
                return None
            total += max(0.0, float(end) - float(start))
        if total <= 0.0:
            return None
        return canonical_six_dp(min(TARGET_EXCERPT_SECONDS, total))
    if isinstance(duration, (int, float)) and not isinstance(duration, bool) and duration > 0:
        return canonical_six_dp(min(TARGET_EXCERPT_SECONDS, float(duration)))
    return None


def _ramp_edge(generated: np.ndarray, edge: np.ndarray) -> tuple[np.ndarray, dict[str, Any]]:
    """Blend the generated edge into the adjacent reference edge (POC ramp)."""
    edge_len = min(edge.shape[0], generated.shape[0])
    if edge_len <= 0:
        return generated, {"gainDb": None, "clamped": False, "residualDb": None}
    target = generated[:edge_len] + edge[-edge_len:]
    generated_rms = _rms(generated[:edge_len])
    target_rms = _rms(target)
    request_db = _db(target_rms / generated_rms) if generated_rms > 0 and target_rms > 0 else 0.0
    request_db = float(request_db or 0.0)
    applied_db = max(-EDGE_GAIN_LIMIT_DB, min(EDGE_GAIN_LIMIT_DB, request_db))
    gain = 10.0 ** (applied_db / 20.0)

    smooth = np.linspace(0.0, 1.0, edge_len + 2)[1:-1]
    blended = generated[:edge_len] * smooth + target * (1.0 - smooth)
    ramp = np.linspace(1.0, gain, edge_len + 2)[1:-1]
    ramped = np.concatenate(
        [generated[:edge_len].copy(), generated[edge_len:].copy()]
    )
    ramped[:edge_len] *= ramp

    residual_db: Optional[float] = None
    if edge_len > 0:
        residual = np.asarray(ramped[-edge_len:], dtype=np.float64) - edge[-edge_len:]
        edge_rms = _rms(edge[-edge_len:])
        residual_rms = _rms(residual)
        if edge_rms > 0:
            residual_db = _db(max(residual_rms, 1e-12) / edge_rms)
    return ramped, {
        "gainDb": round(applied_db, 3),
        "requestedGainDb": round(request_db, 3),
        "clamped": abs(request_db) > EDGE_GAIN_LIMIT_DB,
        "residualDb": None if residual_db is None else round(residual_db, 3),
    }


def _apply_fades(samples: np.ndarray) -> np.ndarray:
    fade = min(_seconds_to_samples(FADE_SECONDS), samples.shape[0] // 2)
    if fade <= 0:
        return samples
    ramp = np.linspace(0.0, 1.0, fade + 2)[1:-1]
    result = samples.copy()
    result[:fade] *= ramp
    result[-fade:] *= ramp[::-1]
    return result


def _limit_peak(samples: np.ndarray) -> tuple[np.ndarray, float, bool]:
    peak = float(np.max(np.abs(samples))) if samples.shape[0] else 0.0
    if peak > PEAK_LIMIT:
        return samples * (PEAK_LIMIT / peak), PEAK_LIMIT, True
    return samples, peak, False


def _to_pcm16(samples: np.ndarray) -> np.ndarray:
    clipped = np.clip(samples, -1.0, 1.0)
    return np.rint(clipped * PCM16_MAX).astype("<i2")


def _wav_bytes(samples: np.ndarray) -> bytes:
    buffer = io.BytesIO()
    sf.write(buffer, samples, SAMPLE_RATE, format="WAV", subtype="PCM_16")
    return buffer.getvalue()


def validate_generated_level(generated: np.ndarray) -> float:
    level = _db(_rms(_mono(generated)))
    if level is None or not np.isfinite(level) or level < MIN_GENERATED_RMS_DBFS:
        raise _worker_error("The generated AI transition is below the minimum audio level")
    return level


def _take_provider(job):
    route = getattr(job, "provider_context_json", None) or {}
    winner = route.get("winner")
    if route.get("provider") == "cascade" and winner is not None:
        backend = winner["backend"]
        return backend, route["attempts"][backend]
    return route.get("provider", "elevenlabs"), route


def _prepare_boundary_take(seam, descriptor, route, simdps, reference):
    target = descriptor["target"]
    milliseconds = generation_duration_ms(
        descriptor["beats"], descriptor["toTempoBpm"], boundary=True
    )
    frames = milliseconds_to_frames(milliseconds)
    if simdps:
        if route["fill_frames"] != frames:
            raise _worker_error("The SimDPS boundary fill length changed")
        start, expected = route["a_frames"], route["a_frames"] + frames + route["b_frames"]
    else:
        side = "b" if target == "intro" else "a"
        seconds = route.get(f"{side}_seconds") or getattr(reference, f"{side}_seconds")
        seconds = seconds or _predicted_side_seconds(descriptor["recording"]["steps"], None)
        if not seconds:
            raise _worker_error("The stored boundary reference geometry is incomplete")
        reference_frames = _seconds_to_samples(seconds)
        chunk_frames = milliseconds_to_frames(max(3000, milliseconds))
        start = chunk_frames - frames if target == "intro" else reference_frames
        expected = reference_frames + chunk_frames
    if abs(len(seam) - expected) > _seconds_to_samples(GEOMETRY_TOLERANCE_SECONDS) + 1:
        raise _worker_error("The provider response duration does not match the boundary")
    generated = seam[start : start + frames]
    if len(generated) != frames:
        raise _worker_error("The provider response ended before the requested boundary interval")
    rms = validate_generated_level(generated)
    faded = generated.astype(np.float32) / 32768.0
    for channel in range(CHANNELS):
        faded[:, channel] = _apply_fades(faded[:, channel])
    limited, _, _ = _limit_peak(faded)
    seconds = canonical_six_dp(frames / SAMPLE_RATE)
    return PreparedTake(
        wav_bytes=_wav_bytes(_to_pcm16(limited)),
        seconds=seconds,
        status="ok",
        reasons=(),
        metrics={"status": "ok", "seconds": seconds, "generatedRmsDb": round(rms, 2)},
    )


def prepare_staged_take(
    raw: bytes,
    *,
    job: Any,
    descriptor: dict[str, Any],
    items: dict[str, dict[str, Any]],
    reference: ReferenceAudio = ReferenceAudio(),
    generation_shortfall_tolerance_seconds: float = MIN_RESPONSE_SHORTFALL_SECONDS,
) -> PreparedTake:
    """Decode, measure, trim and render the staged WAV (no DB or R2 writes)."""
    backend, route = _take_provider(job)
    cascade = (getattr(job, "provider_context_json", None) or {}).get("provider") == "cascade"
    simdps = backend in {"simdps", "modal", "runpod"}
    seam = decode_stereo_pcm16(raw, headerless=simdps)
    if descriptor.get("target") in ("intro", "outro"):
        return _prepare_boundary_take(seam, descriptor, route, simdps, reference)
    from_payload = descriptor.get("from") if isinstance(descriptor, dict) else None
    to_payload = descriptor.get("to") if isinstance(descriptor, dict) else None
    if not isinstance(from_payload, dict) or not isinstance(to_payload, dict):
        raise _worker_error("The stored descriptor has no from/to excerpt geometry")

    from_item = items.get(job.from_item_id) or {}
    to_item = items.get(job.to_item_id) or {}
    if simdps:
        a_seconds = route["a_frames"] / SAMPLE_RATE
        b_seconds = route["b_frames"] / SAMPLE_RATE
    else:
        a_seconds = route.get("a_seconds") or reference.a_seconds or _predicted_side_seconds(
            from_payload.get("steps"), from_item.get("duration_seconds")
        )
        b_seconds = route.get("b_seconds") or reference.b_seconds or _predicted_side_seconds(
            to_payload.get("steps"), to_item.get("duration_seconds")
        )
    if a_seconds is None or b_seconds is None:
        raise _worker_error(
            "The stored descriptor excerpt geometry is incomplete"
        )

    beats = descriptor.get("beats")
    tempo = descriptor.get("toTempoBpm")
    try:
        generation_ms = generation_duration_ms(int(beats), float(tempo))
    except (TypeError, ValueError) as exc:
        raise _worker_error("The stored descriptor has invalid beats or tempo") from exc
    requested_generation_samples = milliseconds_to_frames(generation_ms)
    a_samples = _seconds_to_samples(float(a_seconds))
    b_samples = _seconds_to_samples(float(b_seconds))

    if simdps:
        a_samples, b_samples = route["a_frames"], route["b_frames"]

    total = seam.shape[0]
    expected = a_samples + requested_generation_samples + b_samples
    if abs(total - expected) > _seconds_to_samples(GEOMETRY_TOLERANCE_SECONDS) + 1:
        raise _worker_error(
            "The provider response duration does not match the requested "
            f"geometry ({total / SAMPLE_RATE:.3f} s vs {expected / SAMPLE_RATE:.3f} s)"
        )

    reasons: list[str] = []
    glue_start = a_samples
    available = max(0, total - glue_start)
    generated = seam[glue_start : glue_start + min(requested_generation_samples, available)]
    b_start = glue_start + generated.shape[0]
    speech_b = seam[b_start:]
    shortfall = requested_generation_samples - generated.shape[0]
    if shortfall > _seconds_to_samples(generation_shortfall_tolerance_seconds):
        raise _worker_error(
            "The provider response ended before the requested AI transition "
            f"({shortfall / SAMPLE_RATE:.3f} s short)"
        )
    if shortfall > 0:
        reasons.append(
            f"provider response ended {shortfall / SAMPLE_RATE * 1000:.1f} ms before "
            f"the requested {generation_ms / 1000:.3f} s"
        )
    if generated.shape[0] == 0:
        raise _worker_error("The provider response contains no generated AI transition")
    if generated.shape[0] > requested_generation_samples:
        generated = generated[-requested_generation_samples:]
        b_start = glue_start + requested_generation_samples
        speech_b = seam[b_start:]

    generated_rms_db = validate_generated_level(generated)

    context = _seconds_to_samples(ENVELOPE_REFERENCE_SECONDS)
    left_width = min(context, glue_start)
    right_width = min(context, total - b_start)
    speech_a = seam[glue_start - left_width : glue_start] if left_width else seam[:0]
    speech_b_context = speech_b[:right_width]

    correlation_a = envelope_correlation(
        reference.a if reference.a is not None else _mono(speech_a),
        _mono(speech_a),
        align="tail",
    )
    correlation_b = envelope_correlation(
        reference.b if reference.b is not None else _mono(speech_b_context),
        _mono(speech_b_context),
        align="head",
    )
    source_available = reference.a is not None and reference.b is not None
    for side, value in (("A", correlation_a), ("B", correlation_b)):
        if source_available and value is not None and value < ENVELOPE_OK_FLOOR:
            reasons.append(
                f"reference-envelope correlation on side {side} was {value:.2f} "
                f"(threshold {ENVELOPE_OK_FLOOR:.2f})"
            )
    edge_samples = min(_seconds_to_samples(EDGE_SECONDS), generated.shape[0])
    # On redelivery the catalog fetch may be unavailable. Derive cascade WAV
    # edges from the immutable seam so the same take always has the same bytes.
    edge_a = (
        reference.a[-edge_samples:]
        if reference.a is not None and not cascade
        else _mono(speech_a)[-edge_samples:]
    )
    edge_b = (
        reference.b[:edge_samples]
        if reference.b is not None and not cascade
        else _mono(speech_b_context)[:edge_samples]
    )
    generated_float = _mono(generated) if generated.ndim == 1 else _mono(generated)
    head_ramped, head_info = _ramp_edge(generated_float, edge_a)
    tail_ramped, tail_info = _ramp_edge(head_ramped, edge_b[::-1] if edge_b.shape[0] else edge_b)

    staged_mono = _apply_fades(tail_ramped)
    staged_mono, peak, peak_scaled = _limit_peak(staged_mono)
    staged = np.repeat(_to_pcm16(staged_mono)[:, None], CHANNELS, axis=1)
    wav_bytes = _wav_bytes(staged)
    seconds = canonical_six_dp(staged.shape[0] / SAMPLE_RATE)

    status = "check" if reasons else "ok"
    metrics: dict[str, Any] = {
        "status": status,
        "reasons": reasons,
        "seconds": seconds,
        "requestedGlueSeconds": canonical_six_dp(generation_ms / 1000.0),
        "generatedSeconds": canonical_six_dp(generated.shape[0] / SAMPLE_RATE),
        "aRefSeconds": canonical_six_dp(float(a_seconds)),
        "bRefSeconds": canonical_six_dp(float(b_seconds)),
        "responseBytes": total * BYTES_PER_SAMPLE,
        "responseFrames": total,
        "responseGeometryDeltaMs": round((total - expected) / SAMPLE_RATE * 1000.0, 3),
        "generationShortfallMs": round(shortfall / SAMPLE_RATE * 1000.0, 3),
        "refEnvelopeMeasured": source_available,
        "refEnvelopeRA": None if correlation_a is None else round(correlation_a, 4),
        "refEnvelopeRB": None if correlation_b is None else round(correlation_b, 4),
        "refEnvelopeFloor": ENVELOPE_OK_FLOOR,
        "generatedRmsDb": None if generated_rms_db is None else round(generated_rms_db, 2),
        "headRamp": head_info,
        "tailRamp": tail_info,
        "peak": round(peak, 6),
        "peakLimit": PEAK_LIMIT,
        "peakScaled": peak_scaled,
        "fadeSeconds": FADE_SECONDS,
    }
    return PreparedTake(
        wav_bytes=wav_bytes,
        seconds=seconds,
        status=status,
        reasons=tuple(reasons),
        metrics=metrics,
    )


# ---------------------------------------------------------------------------
# Reference excerpts
# ---------------------------------------------------------------------------


def load_reference_audio(
    *,
    job: Any,
    descriptor: dict[str, Any],
    items: dict[str, dict[str, Any]],
    asset_fetcher: Any,
    work_dir: Path,
    log: Callable[..., None],
) -> ReferenceAudio:
    """Fetch and build both reference excerpts for the envelope measurement.

    A fetch failure degrades QA to a recorded "measurement unavailable" reason;
    it never fails the take and never contacts the provider.
    """
    if asset_fetcher is None:
        return ReferenceAudio()
    try:
        initialize = getattr(asset_fetcher, "initialize", None)
        if callable(initialize):
            initialize()
        from sow_render_worker.glue_excerpts import build_excerpt

        audio: dict[str, tuple[np.ndarray, float]] = {}
        for side in spine.reference_sides(job):
            item = items.get(job.from_item_id if side == "A" else job.to_item_id)
            payload = descriptor.get("recording" if "target" in descriptor else
                                     ("from" if side == "A" else "to"))
            if not item or not isinstance(payload, dict):
                return ReferenceAudio()
            source_path = spine._free_retry(
                lambda item=item: asset_fetcher.download_audio(
                    item["recording_hash_prefix"]
                ),
                label=f"download_reference_{side.lower()}",
                log=log,
            )
            time_left = getattr(asset_fetcher, "time_left", None)
            wav_path, seconds, _ = build_excerpt(
                source_path=source_path,
                steps=payload.get("steps"),
                side=side,
                work_dir=work_dir,
                **({"timeout": time_left()} if callable(time_left) else {}),
            )
            samples, sample_rate = sf.read(str(wav_path), dtype="int16", always_2d=True)
            if int(sample_rate) != SAMPLE_RATE:
                return ReferenceAudio()
            audio[side] = (_mono(np.asarray(samples)), float(seconds))
        return ReferenceAudio(
            a=audio.get("A", (None, None))[0],
            b=audio.get("B", (None, None))[0],
            a_seconds=audio.get("A", (None, None))[1],
            b_seconds=audio.get("B", (None, None))[1],
        )
    except Exception as exc:
        log(
            "glue.staging.reference.unavailable",
            level="warning",
            errorType=type(exc).__name__,
        )
        return ReferenceAudio()


# ---------------------------------------------------------------------------
# Persistence transaction
# ---------------------------------------------------------------------------


def _status(value: Any) -> Optional[str]:
    if value is None:
        return None
    return getattr(value, "value", value)


def _take_object_key(job: Any) -> str:
    return f"glues/{job.user_id}/{job.descriptor_key}/take-{job.pending_take_id}.wav"


def _junction_is_current(
    items: dict[str, dict[str, Any]],
    job: Any,
) -> bool:
    """Whether both items still carry the timestamps the job snapshotted.

    A changed timestamp means a descriptor input (recording, arrangement,
    tempo, beats) or the junction itself changed after the paid request was
    snapshotted. The take is still retained; it is simply not selected.
    """
    from_item = items.get(job.from_item_id)
    to_item = items.get(job.to_item_id)
    if from_item is None or to_item is None:
        return False
    return spine._timestamps_match(
        job.expected_from_updated_at, from_item.get("updated_at")
    ) and spine._timestamps_match(
        job.expected_to_updated_at, to_item.get("updated_at")
    )


def record_staged_take(
    conn: Any,
    job: Any,
    *,
    prepared: PreparedTake,
    wav_sha256: str,
    log: Callable[..., None],
) -> bool:
    """Insert the immutable take and select it only for a still-current junction.

    Runs in one transaction: the take insert is idempotent on the pre-allocated
    id, the selection is guarded on owner/descriptor/ready/audio, and the job
    moves to ``ready`` only from a staging phase. Returns whether this delivery
    recorded/finished anything new.
    """
    key = _take_object_key(job)
    metrics = dict(prepared.metrics)
    backend, route = _take_provider(job)
    metrics.update(provider=backend, providerModel=route.get("model", "music_v2_5"))
    if backend in {"simdps", "modal", "runpod"}:
        metrics.update(upstreamRevision=route["upstream_revision"],
                       checkpointRevision=route["checkpoint_revision"],
                       searchSource=route["search_source"])
    metrics["audioSha256"] = wav_sha256
    metrics_json = json.dumps(metrics, ensure_ascii=False, sort_keys=True)
    reasons_json = json.dumps(list(prepared.reasons), ensure_ascii=False)

    recorded = False
    with spine._atomic(conn):
        current = get_glue_job(conn, job.id, job.user_id)
        if current is None:
            log("glue.staging.missing", level="warning", phase="unknown-job")
            return False

        take, created = insert_glue_take_if_absent(
            conn,
            take_id=str(job.pending_take_id),
            user_id=job.user_id,
            descriptor_key=job.descriptor_key,
            descriptor_json=job.descriptor_json,
            ordinal=job.take_ordinal,
            seed=job.seed,
            status="ready",
            r2_key=key,
            seconds=prepared.seconds,
            raw_response_r2_key=job.raw_response_r2_key,
            raw_response_sha256=job.raw_response_sha256,
            raw_response_bytes=job.raw_response_bytes,
            metrics_json=metrics_json,
            reasons_json=reasons_json,
        )
        if not created:
            if _status(current.phase) == "ready":
                log(
                    "glue.staging.replay",
                    phase="ready",
                    takeId=take.id,
                    status="noop",
                )
                return False
            log(
                "glue.staging.replay",
                phase=_status(current.phase),
                takeId=take.id,
                status="existing-take",
            )
        else:
            recorded = True

        if _status(current.phase) not in GLUE_STAGING_PHASES:
            log(
                "glue.staging.ignored",
                level="warning",
                phase=_status(current.phase),
                takeId=take.id,
                status="terminal",
            )
            return recorded

        active_take_id: Optional[str] = None
        if current.active:
            fresh_items = read_songset_items_for_glue(conn, job.songset_id)
            if getattr(job, "target", "junction") != "junction":
                try:
                    inputs = spine._rebuild_junction(fresh_items, job)
                    if (inputs.canonical_json == job.descriptor_json
                            and select_glue_take_for_boundary(conn, job, take.id)):
                        active_take_id = take.id
                except spine._StaleJunction:
                    pass
                if active_take_id is None:
                    log("glue.staging.stale", takeId=take.id, status="take-retained-unselected")
            elif _junction_is_current({row["id"]: row for row in fresh_items}, job):
                if select_glue_take_for_junction(
                    conn,
                    item_id=job.from_item_id,
                    songset_id=job.songset_id,
                    user_id=job.user_id,
                    take_id=take.id,
                    descriptor_key=job.descriptor_key,
                    manage_transaction=False,
                ):
                    active_take_id = take.id
                else:
                    log(
                        "glue.staging.selection.refused",
                        level="warning",
                        phase=_status(current.phase),
                        takeId=take.id,
                    )
            else:
                log(
                    "glue.staging.stale",
                    level="warning",
                    phase=_status(current.phase),
                    takeId=take.id,
                    status="take-retained-unselected",
                )

        finished = finish_glue_job_ready(
            conn,
            job.id,
            job.user_id,
            resulting_take_id=take.id,
            active_take_id=active_take_id,
        )
        if finished is None:
            log(
                "glue.staging.finish.skipped",
                level="warning",
                phase=_status(current.phase),
                takeId=take.id,
            )
            return recorded
        log(
            "glue.job.ready",
            phase="ready",
            takeId=take.id,
            seconds=prepared.seconds,
            qaStatus=prepared.status,
            selected=active_take_id is not None,
        )
    return recorded


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _descriptor_payload(job: Any) -> dict[str, Any]:
    try:
        payload = json.loads(job.descriptor_json)
    except (TypeError, ValueError) as exc:
        raise _worker_error("The stored descriptor is not valid JSON", "none") from exc
    if not isinstance(payload, dict):
        raise _worker_error("The stored descriptor is not an object", "none")
    return payload


def finalize_staging(
    conn: Any,
    job: Any,
    log: Callable[..., None],
    *,
    work_dir: Optional[Path] = None,
    asset_fetcher: Any = None,
    r2_store: Any = None,
    deadline: float | None = None,
) -> None:
    """Turn a verified raw provider response into one ready take, for free."""
    log("glue.staging.started", phase=_status(job.phase), takeOrdinal=job.take_ordinal)
    spine._free_retry(
        lambda: spine.heartbeat_glue_job(conn, job.id, job.user_id),
        label="heartbeat_staging_start",
        log=log,
    )

    if not (job.raw_response_r2_key and job.raw_response_sha256 and job.raw_response_bytes):
        raise spine.GlueJobError(
            GLUE_WORKER_FAILED,
            "none",
            "No verified provider response is persisted; refusing to stage",
        )

    store = r2_store or R2StagingStore(deadline=deadline)

    def _read_verified_once() -> bytes:
        try:
            return store.read_raw(job)
        except RawResponseUnverified as exc:
            # Terminal: a metadata/checksum mismatch is never a retryable R2
            # blip and never authorizes another compose request. Raising the
            # typed job error also keeps _free_retry from retrying it.
            raise spine.GlueJobError(GLUE_WORKER_FAILED, "none", str(exc)) from exc

    raw = spine._free_retry(
        _read_verified_once,
        label="read_raw_response",
        log=log,
    )

    descriptor = _descriptor_payload(job)
    items = {
        row["id"]: row for row in read_songset_items_for_glue(conn, job.songset_id)
    }
    own_work_dir = work_dir is None
    staging_dir = Path(work_dir) if work_dir is not None else Path(
        tempfile.mkdtemp(prefix=f"sow-glue-staging-{job.id[:8]}-")
    )
    try:
        og_dir = staging_dir / "references"
        og_dir.mkdir(parents=True, exist_ok=True)
        reference = load_reference_audio(
            job=job,
            descriptor=descriptor,
            items=items,
            asset_fetcher=asset_fetcher,
            work_dir=og_dir,
            log=log,
        )
        prepared = prepare_staged_take(
            raw,
            job=job,
            descriptor=descriptor,
            items=items,
            reference=reference,
        )
        wav_sha256 = hashlib.sha256(prepared.wav_bytes).hexdigest()
        key = _take_object_key(job)
        spine._free_retry(
            lambda: store.put_take(key, prepared.wav_bytes),
            label="store_staged_take",
            log=log,
        )
        spine._free_retry(
            lambda: spine.heartbeat_glue_job(conn, job.id, job.user_id),
            label="heartbeat_staging_ready",
            log=log,
        )
        recorded = record_staged_take(
            conn,
            job,
            prepared=prepared,
            wav_sha256=wav_sha256,
            log=log,
        )
        if recorded:
            log(
                "glue.staging.recovered",
                phase="staging",
                takeId=str(job.pending_take_id),
                seconds=prepared.seconds,
                qaStatus=prepared.status,
            )
        else:
            log("glue.staging.replay", phase="staging", status="noop")
    finally:
        if own_work_dir:
            import shutil

            shutil.rmtree(staging_dir, ignore_errors=True)


__all__ = [
    "PreparedTake",
    "R2StagingStore",
    "RawResponseUnverified",
    "ReferenceAudio",
    "StagingGeometryError",
    "decode_stereo_pcm16",
    "envelope_correlation",
    "finalize_staging",
    "load_reference_audio",
    "prepare_staged_take",
    "record_staged_take",
    "list_glue_takes_for_descriptor",
    "set_read_timeout",
]
