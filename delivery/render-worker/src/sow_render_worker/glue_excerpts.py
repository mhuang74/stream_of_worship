"""Worker-built canonical Glue excerpts.

The browser never supplies Junction audio. This module downloads the selected
Recording through the worker's atomic asset path, decodes it to 44.1 kHz
stereo through FFmpeg, slices the canonical arranged tail (side A) or head
(side B), and writes a deterministic 44-byte RIFF PCM16 WAV.

Excerpt cache identity is logical, never the decoded byte hash:
``sha256(canonical_json({v, side, seconds, contentHash, steps}))``. The same
serializer as the descriptor (``glue_descriptor``) is reused so TypeScript and
Python produce byte-identical keys.
"""

from __future__ import annotations

import hashlib
import io
import math
import os
import subprocess
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import psycopg2.extras
import soundfile as sf

from sow_render_worker.arrangements import validate_arrangement
from sow_render_worker.glue_descriptor import canonical_json, canonical_six_dp

EXCERPT_VERSION = 2
SAMPLE_RATE = 44_100
CHANNELS = 2
PCM16_MAX = 32767
TARGET_EXCERPT_SECONDS = 12.0
MIN_EXCERPT_SECONDS = 1.0

EXCERPT_PHASES = ("constructed", "uploaded", "rejected", "failed")
_CACHE_HIT_PHASES = frozenset({"uploaded", "rejected"})

GLUE_EXCERPT_TOO_SHORT = "GLUE_EXCERPT_TOO_SHORT"
GLUE_AUDIO_UNAVAILABLE = "GLUE_AUDIO_UNAVAILABLE"


class GlueExcerptError(RuntimeError):
    """Excerpt construction failure carrying a stable client error code."""

    code = "GLUE_EXCERPT_ERROR"

    def __init__(self, message: str, *, side: str | None = None) -> None:
        super().__init__(message)
        self.side = side


class GlueExcerptTooShort(GlueExcerptError):
    """Arranged side has under one second and must be rejected before spend."""

    code = GLUE_EXCERPT_TOO_SHORT


class GlueAudioUnavailable(GlueExcerptError):
    """Recording download or decode failed; infrastructure, not a cache entry."""

    code = GLUE_AUDIO_UNAVAILABLE


@dataclass(frozen=True)
class GlueExcerptRow:
    user_id: int
    logical_key: str
    side: str
    seconds: float
    content_hash: str
    phase: str
    version: int = EXCERPT_VERSION
    canonical_steps_json: str | None = None
    audio_sha256: str | None = None
    elevenlabs_song_id: str | None = None
    rejection_code: str | None = None
    safe_failure_detail: str | None = None


@dataclass(frozen=True)
class PreparedExcerpt:
    side: str
    seconds: float
    logical_key: str
    content_hash: str
    canonical_steps_json: str | None
    wav_path: Path | None
    audio_sha256: str | None
    elevenlabs_song_id: str | None
    rejection_code: str | None = None
    cached: bool = False


# ---------------------------------------------------------------------------
# Logical identity
# ---------------------------------------------------------------------------


def build_excerpt_key(
    *,
    side: str,
    seconds: float,
    content_hash: str,
    steps: Sequence[Sequence[float]] | None,
) -> tuple[str, str]:
    """Return the canonical excerpt JSON and its sha256 logical key."""
    if side not in ("A", "B"):
        raise ValueError(f"Glue excerpt side must be 'A' or 'B'; received {side!r}")
    if not isinstance(content_hash, str) or not content_hash:
        raise ValueError("Glue excerpt requires a non-empty contentHash")
    payload: dict[str, Any] = {
        "v": EXCERPT_VERSION,
        "side": side,
        "seconds": canonical_six_dp(seconds),
        "contentHash": content_hash,
        "steps": None if steps is None else _canonical_steps(steps),
    }
    canonical = canonical_json(payload)
    return canonical, hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def canonical_steps_from_arrangement(
    raw_arrangement: str | None,
    sections_json: str | None,
    beats_json: str | None,
    recording_duration: float | None,
) -> list[list[float]] | None:
    """Validate a saved arrangement exactly as render does and canonicalize it.

    ``None`` means the untouched full Recording; it is never replaced with a
    synthetic full-range step. Stale or malformed arrangements raise the
    repair-or-reset ``ValueError`` from ``arrangements.validate_arrangement``.
    """
    steps = validate_arrangement(
        raw_arrangement, sections_json, beats_json, recording_duration
    )
    if steps is None:
        return None
    return [
        [canonical_six_dp(step.start_seconds), canonical_six_dp(step.end_seconds)]
        for step in steps
    ]


# ---------------------------------------------------------------------------
# Audio construction
# ---------------------------------------------------------------------------


def build_excerpt(
    *,
    source_path: str | Path,
    steps: Sequence[Sequence[float]] | None,
    side: str,
    work_dir: str | Path,
    timeout: float | None = None,
) -> tuple[Path, float, str]:
    """Build one canonical WAV and return (path, actual seconds, sha256).

    FFmpeg decodes into a file-backed float32 WAV, so the full Recording is
    never held in memory. Only the up-to-12 s excerpt window is read, clamped,
    rounded and written.
    """
    if side not in ("A", "B"):
        raise ValueError(f"Glue excerpt side must be 'A' or 'B'; received {side!r}")
    canonical_steps = None if steps is None else _validated_steps(steps)
    work_path = Path(work_dir)
    work_path.mkdir(parents=True, exist_ok=True)

    decoded = _decode_to_float32(Path(source_path), work_path, timeout=timeout)
    try:
        with sf.SoundFile(str(decoded)) as source:
            windows = _plan_windows(
                canonical_steps,
                frames=len(source),
                side=side,
            )
            samples = _read_windows(source, windows)
    finally:
        decoded.unlink(missing_ok=True)

    if samples.shape[0] < _seconds_to_samples(MIN_EXCERPT_SECONDS):
        raise GlueExcerptTooShort(
            f"Glue excerpt side {side} has under {MIN_EXCERPT_SECONDS:g} s of arranged audio",
            side=side,
        )

    seconds = canonical_six_dp(samples.shape[0] / SAMPLE_RATE)
    wav_bytes = _wav_bytes(samples)

    handle = tempfile.NamedTemporaryFile(
        dir=work_path, prefix=f"excerpt-{side}-", suffix=".wav", delete=False
    )
    target = Path(handle.name)
    handle.close()
    partial = target.with_suffix(".part")
    partial.write_bytes(wav_bytes)
    os.replace(partial, target)
    return target, seconds, hashlib.sha256(wav_bytes).hexdigest()


def build_guide(source: Path, steps, work_dir: Path, *, timeout: float) -> np.ndarray:
    decoded = _decode_to_float32(source, work_dir, timeout=timeout)
    try:
        with sf.SoundFile(decoded) as audio:
            windows = _plan_windows(
                _validated_steps(steps) if steps is not None else None,
                frames=len(audio), side="B", target_seconds=18,
            )
            return _read_windows(audio, windows)
    finally:
        decoded.unlink(missing_ok=True)


def _decode_to_float32(
    source: Path, work_dir: Path, *, timeout: float | None = None
) -> Path:
    handle = tempfile.NamedTemporaryFile(
        dir=work_dir, prefix="decode-", suffix=".f32.wav", delete=False
    )
    decoded = Path(handle.name)
    handle.close()
    command = [
        "ffmpeg",
        "-v",
        "error",
        "-nostdin",
        "-y",
        "-i",
        str(source),
        "-map",
        "0:a:0",
        "-ac",
        str(CHANNELS),
        "-ar",
        str(SAMPLE_RATE),
        "-c:a",
        "pcm_f32le",
        "-f",
        "wav",
        str(decoded),
    ]
    try:
        subprocess.run(command, check=True, capture_output=True, timeout=timeout)
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        decoded.unlink(missing_ok=True)
        raise GlueAudioUnavailable(f"Failed to decode Recording {source.name}") from exc
    return decoded


def _plan_windows(
    steps: Sequence[Sequence[float]] | None,
    *,
    frames: int,
    side: str,
    target_seconds: float = TARGET_EXCERPT_SECONDS,
) -> list[tuple[int, int]]:
    """Plan (start frame, frame count) windows in playback order.

    A walks the arranged steps backwards taking each step's tail until the
    budget fills, then restores forward order. B walks forward taking each
    step's head. A short side keeps every available frame.
    """
    if frames <= 0:
        raise GlueExcerptTooShort("Recording decoded to no audio", side=side)

    if steps is None:
        spans = [(0, frames)]
    else:
        spans = []
        for start_seconds, end_seconds in steps:
            start = _clamp_slice(_seconds_to_samples(start_seconds), frames)
            end = _clamp_slice(_seconds_to_samples(end_seconds), frames)
            if end <= start:
                continue
            spans.append((start, end - start))
        if not spans:
            raise GlueExcerptTooShort("Arrangement selects no audio", side=side)

    budget = _seconds_to_samples(target_seconds)
    remaining = budget
    windows: list[tuple[int, int]] = []
    if side == "A":
        for start, count in reversed(spans):
            if remaining <= 0:
                break
            take = min(count, remaining)
            windows.append((start + count - take, take))
            remaining -= take
        windows.reverse()
    else:
        for start, count in spans:
            if remaining <= 0:
                break
            take = min(count, remaining)
            windows.append((start, take))
            remaining -= take
    return windows


def _read_windows(source: sf.SoundFile, windows: Sequence[tuple[int, int]]) -> np.ndarray:
    total = sum(count for _, count in windows)
    samples = np.empty((total, CHANNELS), dtype=np.int16)
    offset = 0
    for start, count in windows:
        source.seek(start)
        block = source.read(count, dtype="float32", always_2d=True)
        if block.shape[0] != count:
            block = block[:count]
        samples[offset : offset + block.shape[0]] = _to_pcm16(block)
        offset += block.shape[0]
    return np.ascontiguousarray(samples[:offset])


def _to_pcm16(samples: np.ndarray) -> np.ndarray:
    clipped = np.clip(samples.astype(np.float32, copy=False), -1.0, 1.0)
    return np.rint(clipped * PCM16_MAX).astype("<i2")


def _wav_bytes(samples: np.ndarray) -> bytes:
    buffer = io.BytesIO()
    sf.write(buffer, samples, SAMPLE_RATE, format="WAV", subtype="PCM_16")
    return buffer.getvalue()


def _seconds_to_samples(seconds: float) -> int:
    return int(math.floor(float(seconds) * SAMPLE_RATE + 0.5))


def _clamp_slice(value: int, frames: int) -> int:
    return max(0, min(int(value), frames))


def _canonical_steps(steps: Sequence[Sequence[float]]) -> list[list[float]]:
    canonical: list[list[float]] = []
    for step in steps:
        if len(step) != 2:
            raise ValueError("Glue excerpt steps must be [start, end] pairs")
        canonical.append([canonical_six_dp(step[0]), canonical_six_dp(step[1])])
    return canonical


def _validated_steps(steps: Sequence[Sequence[float]]) -> list[list[float]]:
    """Canonicalize, then require playable [start, end) bounds for extraction."""
    canonical = _canonical_steps(steps)
    for start, end in canonical:
        if start < 0 or end <= start:
            raise ValueError(
                "Glue excerpt steps must be non-negative [start, end] "
                f"with start < end; received {[start, end]!r}"
            )
    return canonical


# ---------------------------------------------------------------------------
# Excerpt cache
# ---------------------------------------------------------------------------

_UPSERT_SQL = (
    "INSERT INTO glue_excerpts ("
    "user_id, logical_key, version, side, seconds, content_hash, "
    "canonical_steps_json, audio_sha256, elevenlabs_song_id, phase, "
    "rejection_code, safe_failure_detail, created_at, updated_at"
    ") VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now(), now()) "
    "ON CONFLICT (user_id, logical_key) DO UPDATE SET "
    "version = EXCLUDED.version, "
    "side = EXCLUDED.side, "
    "seconds = EXCLUDED.seconds, "
    "content_hash = EXCLUDED.content_hash, "
    "canonical_steps_json = COALESCE("
    "EXCLUDED.canonical_steps_json, glue_excerpts.canonical_steps_json), "
    "audio_sha256 = COALESCE(EXCLUDED.audio_sha256, glue_excerpts.audio_sha256), "
    "elevenlabs_song_id = COALESCE("
    "EXCLUDED.elevenlabs_song_id, glue_excerpts.elevenlabs_song_id), "
    "phase = EXCLUDED.phase, "
    "rejection_code = EXCLUDED.rejection_code, "
    "safe_failure_detail = EXCLUDED.safe_failure_detail, "
    "updated_at = now() "
    "RETURNING *"
)


def get_excerpt(
    conn: Any,
    user_id: int,
    logical_key: str,
) -> GlueExcerptRow | None:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT * FROM glue_excerpts WHERE user_id = %s AND logical_key = %s",
            (user_id, logical_key),
        )
        row = cur.fetchone()
    if not row:
        return None
    return _row_to_excerpt(row)


def record_constructed(
    conn: Any,
    user_id: int,
    *,
    logical_key: str,
    side: str,
    seconds: float,
    content_hash: str,
    canonical_steps_json: str | None,
    audio_sha256: str,
) -> GlueExcerptRow:
    return _upsert(
        conn,
        user_id,
        logical_key=logical_key,
        side=side,
        seconds=seconds,
        content_hash=content_hash,
        canonical_steps_json=canonical_steps_json,
        audio_sha256=audio_sha256,
        elevenlabs_song_id=None,
        phase="constructed",
        rejection_code=None,
        safe_failure_detail=None,
    )


def record_uploaded(
    conn: Any,
    user_id: int,
    *,
    logical_key: str,
    side: str,
    seconds: float,
    content_hash: str,
    canonical_steps_json: str | None,
    audio_sha256: str,
    elevenlabs_song_id: str,
) -> GlueExcerptRow:
    if not elevenlabs_song_id:
        raise ValueError("record_uploaded requires an elevenlabs_song_id")
    return _upsert(
        conn,
        user_id,
        logical_key=logical_key,
        side=side,
        seconds=seconds,
        content_hash=content_hash,
        canonical_steps_json=canonical_steps_json,
        audio_sha256=audio_sha256,
        elevenlabs_song_id=elevenlabs_song_id,
        phase="uploaded",
        rejection_code=None,
        safe_failure_detail=None,
    )


def record_rejection(
    conn: Any,
    user_id: int,
    *,
    logical_key: str,
    side: str,
    seconds: float,
    content_hash: str,
    canonical_steps_json: str | None,
    audio_sha256: str,
    rejection_code: str,
    safe_failure_detail: str | None = None,
) -> GlueExcerptRow:
    """Cache a copyright rejection as a successful negative entry."""
    if rejection_code != "GLUE_COPYRIGHT_REJECTED":
        raise ValueError("record_rejection only caches GLUE_COPYRIGHT_REJECTED")
    return _upsert(
        conn,
        user_id,
        logical_key=logical_key,
        side=side,
        seconds=seconds,
        content_hash=content_hash,
        canonical_steps_json=canonical_steps_json,
        audio_sha256=audio_sha256,
        elevenlabs_song_id=None,
        phase="rejected",
        rejection_code=rejection_code,
        safe_failure_detail=safe_failure_detail,
    )


def record_failed(
    conn: Any,
    user_id: int,
    *,
    logical_key: str,
    side: str,
    seconds: float,
    content_hash: str,
    canonical_steps_json: str | None,
    audio_sha256: str,
    safe_failure_detail: str | None = None,
) -> GlueExcerptRow:
    """Record an infrastructure failure; never a negative cache hit."""
    return _upsert(
        conn,
        user_id,
        logical_key=logical_key,
        side=side,
        seconds=seconds,
        content_hash=content_hash,
        canonical_steps_json=canonical_steps_json,
        audio_sha256=audio_sha256,
        elevenlabs_song_id=None,
        phase="failed",
        rejection_code=None,
        safe_failure_detail=safe_failure_detail,
    )


def _upsert(
    conn: Any,
    user_id: int,
    *,
    logical_key: str,
    side: str,
    seconds: float,
    content_hash: str,
    canonical_steps_json: str | None,
    audio_sha256: str | None,
    elevenlabs_song_id: str | None,
    phase: str,
    rejection_code: str | None,
    safe_failure_detail: str | None,
) -> GlueExcerptRow:
    if side not in ("A", "B"):
        raise ValueError(f"Glue excerpt side must be 'A' or 'B'; received {side!r}")
    if phase not in EXCERPT_PHASES:
        raise ValueError(f"Unknown glue_excerpts phase: {phase!r}")
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            _UPSERT_SQL,
            (
                user_id,
                logical_key,
                EXCERPT_VERSION,
                side,
                canonical_six_dp(seconds),
                content_hash,
                canonical_steps_json,
                audio_sha256,
                elevenlabs_song_id,
                phase,
                rejection_code,
                safe_failure_detail,
            ),
        )
        row = cur.fetchone()
    return _row_to_excerpt(row)


def _row_to_excerpt(row: dict[str, Any]) -> GlueExcerptRow:
    return GlueExcerptRow(
        user_id=row["user_id"],
        logical_key=row["logical_key"],
        side=row["side"],
        seconds=row["seconds"],
        content_hash=row["content_hash"],
        phase=row["phase"],
        version=row.get("version") or EXCERPT_VERSION,
        canonical_steps_json=row.get("canonical_steps_json"),
        audio_sha256=row.get("audio_sha256"),
        elevenlabs_song_id=row.get("elevenlabs_song_id"),
        rejection_code=row.get("rejection_code"),
        safe_failure_detail=row.get("safe_failure_detail"),
    )


# ---------------------------------------------------------------------------
# Job-stage entry point
# ---------------------------------------------------------------------------


def prepare_excerpt(
    *,
    conn: Any,
    user_id: int,
    side: str,
    recording_hash_prefix: str,
    content_hash: str,
    steps: Sequence[Sequence[float]] | None,
    asset_fetcher: Any,
    work_dir: str | Path,
    local_only: bool = False,
    preparation_time=None,
    include_rejected_audio: bool = False,
) -> PreparedExcerpt:
    """Build (or reuse) one side's canonical WAV and persist its cache state.

    Consulted before construction when the arranged length is known: an
    ``uploaded`` (reusable provider song id) or ``rejected`` (copyright
    negative entry) row short-circuits download and decode entirely.
    """
    canonical_steps = None if steps is None else _validated_steps(steps)
    steps_json = None if canonical_steps is None else canonical_json(canonical_steps)

    predicted_seconds: float | None = None
    predicted_key: str | None = None
    if canonical_steps is not None:
        arranged = sum(end - start for start, end in canonical_steps)
        if arranged < MIN_EXCERPT_SECONDS:
            raise GlueExcerptTooShort(
                f"Glue excerpt side {side} has under {MIN_EXCERPT_SECONDS:g} s "
                "of arranged audio",
                side=side,
            )
        predicted_seconds = canonical_six_dp(min(TARGET_EXCERPT_SECONDS, arranged))
        predicted_key = build_excerpt_key(
            side=side,
            seconds=predicted_seconds,
            content_hash=content_hash,
            steps=canonical_steps,
        )[1]
        cached = _cached_excerpt(conn, user_id, predicted_key)
        if cached is not None and (
            not local_only or (cached.rejection_code and not include_rejected_audio)
        ):
            return cached

    source = asset_fetcher.download_audio(recording_hash_prefix)
    if not source:
        raise GlueAudioUnavailable(
            f"Recording {recording_hash_prefix} is unavailable", side=side
        )

    wav_path, seconds, audio_sha256 = build_excerpt(
        source_path=source,
        steps=canonical_steps,
        side=side,
        work_dir=work_dir,
        **({"timeout": preparation_time()} if preparation_time else {}),
    )
    # Step bounds are authoritative (canonical 6-dp); frame rounding at the
    # window edge must not fork the logical identity from the webapp's planned
    # seconds. A materially shorter decode (truncated source) still wins.
    if predicted_seconds is not None and abs(seconds - predicted_seconds) <= (
        1.0 / SAMPLE_RATE + 1e-9
    ):
        seconds = predicted_seconds
    logical_key = build_excerpt_key(
        side=side,
        seconds=seconds,
        content_hash=content_hash,
        steps=canonical_steps,
    )[1]

    if logical_key != predicted_key or local_only:
        cached = _cached_excerpt(conn, user_id, logical_key)
        if cached is not None:
            if not local_only or (cached.rejection_code and not include_rejected_audio):
                wav_path.unlink(missing_ok=True)
                return cached
            return replace(cached, wav_path=wav_path, audio_sha256=audio_sha256)

    if not local_only:
        record_constructed(
            conn,
            user_id,
            logical_key=logical_key,
            side=side,
            seconds=seconds,
            content_hash=content_hash,
            canonical_steps_json=steps_json,
            audio_sha256=audio_sha256,
        )
    return PreparedExcerpt(
        side=side,
        seconds=seconds,
        logical_key=logical_key,
        content_hash=content_hash,
        canonical_steps_json=steps_json,
        wav_path=wav_path,
        audio_sha256=audio_sha256,
        elevenlabs_song_id=None,
    )


def _cached_excerpt(
    conn: Any,
    user_id: int,
    logical_key: str,
) -> PreparedExcerpt | None:
    row = get_excerpt(conn, user_id, logical_key)
    if row is None or row.phase not in _CACHE_HIT_PHASES:
        return None
    return PreparedExcerpt(
        side=row.side,
        seconds=row.seconds,
        logical_key=row.logical_key,
        content_hash=row.content_hash,
        canonical_steps_json=row.canonical_steps_json,
        wav_path=None,
        audio_sha256=row.audio_sha256,
        elevenlabs_song_id=row.elevenlabs_song_id,
        rejection_code=row.rejection_code,
        cached=True,
    )
