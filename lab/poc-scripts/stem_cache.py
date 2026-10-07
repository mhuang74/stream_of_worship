#!/usr/bin/env python3
"""Phase 0c: serial stem-separation cache for the LRC review triage cascade.

Implements the stem cache of ``specs/lrc-review-triage-cascade-design-v2.md``
(issue #244). Every checker signal in later phases reads clean vocals from
this cache: ``clean_vocals.flac`` per song, falling back to an explicit
``vocals.wav``/``vocals.flac`` record — never the full mix.

Layout (inside the admin/sow-app cache dir, per consumer convention used by
``experiment_lrc_signals.py`` and ``docs/agent_instructions-fix-lrc.md``):

    <cache_dir>/<hash_prefix>/stems/clean_vocals.flac   # preferred
    <cache_dir>/<hash_prefix>/stems/vocals_dry.flac     # R2 fallback copy
    <cache_dir>/<hash_prefix>/audio/audio.mp3           # separation input

The manifest (``eval/lrc_truth/stem_cache/manifest.json`` in the repo) records
per-song status so re-invocation skips completed work and failed entries are
retried. Separation runs strictly serially (BS-Roformer memory starvation
otherwise); a flock guard makes concurrent invocations fail loudly instead of
queueing.

Read-only with respect to the catalog: zero writes to canonical Lyrics,
catalog status, provenance, or visibility. Writes are limited to the local
cache directory and the repo manifest.
"""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

MANIFEST_SCHEMA_VERSION = 1

# R2 stems priority for clean-vocal resolution. vocals_dry.flac is the
# canonical two-stage (BS-Roformer + UVR-De-Echo) product; vocals_clean.flac
# is the legacy name for the same thing; wet vocals are an explicit fallback
# record (never presented as clean vocals).
R2_STEM_CANDIDATES: tuple[tuple[str, str], ...] = (
    ("vocals_dry", "stems/vocals_dry.flac"),
    ("vocals_clean", "stems/vocals_clean.flac"),
    ("vocals", "stems/vocals.flac"),
    ("vocals_wav", "stems/vocals.wav"),
)


class CacheStatus(str, Enum):
    PENDING = "pending"
    CACHED = "cached"
    FALLBACK = "fallback"
    FAILED = "failed"


class StemCacheError(Exception):
    """Stem cache invariant violation."""


@dataclass(frozen=True)
class SongRef:
    song_id: str
    hash_prefix: str


def _utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class StemCacheManifest:
    """Manifest of per-song stem-cache status (JSON-backed)."""

    def __init__(self, data: dict[str, Any]):
        self.data = data

    @property
    def schema_version(self) -> int:
        return self.data["schema_version"]

    @property
    def songs(self) -> dict[str, dict[str, Any]]:
        return self.data["songs"]

    def to_json(self) -> str:
        return json.dumps(self.data, indent=2, ensure_ascii=False) + "\n"


def load_or_init_manifest(path: Path) -> StemCacheManifest:
    """Load the manifest at *path*, or initialize a fresh one."""
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        version = data.get("schema_version")
        if version != MANIFEST_SCHEMA_VERSION:
            raise StemCacheError(
                f"manifest {path} has schema_version={version!r}, "
                f"expected {MANIFEST_SCHEMA_VERSION}"
            )
        data.setdefault("songs", {})
        return StemCacheManifest(data)
    now = _utcnow()
    manifest = StemCacheManifest(
        {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "created_at": now,
            "updated_at": now,
            "songs": {},
        }
    )
    save_manifest(manifest, path)
    return manifest


def save_manifest(manifest: StemCacheManifest, path: Path) -> None:
    """Atomically persist the manifest (temp file + rename, no partial reads)."""
    manifest.data["updated_at"] = _utcnow()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(manifest.to_json())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


def record_result(
    manifest: StemCacheManifest,
    path: Path,
    song: SongRef,
    *,
    status: CacheStatus,
    source: str | None = None,
    audio: str | None = None,
    error: str | None = None,
    producer: str | None = None,
) -> None:
    """Record one song's outcome and persist the manifest atomically.

    Terminal entries (cached/fallback) must not be overwritten — a re-invoked
    completed run must never corrupt or invalidate existing cache entries.
    """
    existing = manifest.songs.get(song.song_id)
    if (
        existing
        and existing.get("status") in (
            CacheStatus.CACHED.value,
            CacheStatus.FALLBACK.value,
        )
        and existing.get("status") != status.value
    ):
        raise StemCacheError(
            f"{song.song_id}: refusing to overwrite terminal status "
            f"{existing.get('status')!r} with {status.value!r}"
        )
    entry: dict[str, Any] = {
        "status": status.value,
        "hash_prefix": song.hash_prefix,
        "resolved_at": _utcnow(),
    }
    if source is not None:
        entry["source"] = source
    if audio is not None:
        entry["audio"] = audio
    if producer is not None:
        # separation model / producer provenance (e.g. which Roformer variant
        # made the stem) — the cache can mix producers across songs
        entry["producer"] = producer
    if error is not None:
        entry["error"] = error
    manifest.songs[song.song_id] = entry
    save_manifest(manifest, path)


def next_pending(
    manifest: StemCacheManifest,
    songs: list[SongRef],
    cache_root: Path | None = None,
) -> list[SongRef]:
    """Songs not yet resolved: pending and failed entries, in input order.

    Cached and fallback entries are skipped (resume) — unless *cache_root* is
    given and the recorded audio file is missing or empty on this machine.
    The manifest is committed to the repo but the audio is machine-local, so
    a fresh clone must re-resolve rather than silently "resume" with nothing
    on disk. A song whose manifest entry carries a different hash_prefix than
    the DB says is an invariant violation — cache entries are keyed by song
    but rooted at a recording.
    """
    pending: list[SongRef] = []
    for song in songs:
        entry = manifest.songs.get(song.song_id)
        if entry is None:
            pending.append(song)
            continue
        recorded_prefix = entry.get("hash_prefix")
        if recorded_prefix is not None and recorded_prefix != song.hash_prefix:
            raise StemCacheError(
                f"{song.song_id}: manifest hash_prefix {recorded_prefix!r} "
                f"!= DB hash_prefix {song.hash_prefix!r}"
            )
        if entry.get("status") not in (
            CacheStatus.CACHED.value,
            CacheStatus.FALLBACK.value,
        ):
            pending.append(song)
            continue
        if cache_root is not None:
            audio_rel = entry.get("audio")
            audio_path = (
                cache_root / song.hash_prefix / audio_rel if audio_rel else None
            )
            if audio_path is None or not audio_path.exists() or audio_path.stat().st_size == 0:
                # recorded but absent/empty locally: re-resolve
                pending.append(song)
    return pending


@contextmanager
def try_lock_serial(lock_path: Path) -> Iterator[None]:
    """Acquire the serial-execution lock; fail loudly if another run holds it.

    Separation must never run concurrently (BS-Roformer memory starvation):
    rather than queueing, a second invocation errors out.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(lock_path, "w")  # noqa: SIM115 — lock fd must outlive the yield
    try:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as e:
            raise StemCacheError(
                f"another stem-cache run is already running (lock {lock_path} held): {e}"
            ) from e
        yield
    finally:
        try:
            fcntl.flock(handle, fcntl.LOCK_UN)
        finally:
            handle.close()


def lookup_r2_clean_vocals(r2_client: Any, hash_prefix: str) -> tuple[str, str] | None:
    """Best available vocal stem in R2 for *hash_prefix*.

    Returns (source_name, relative_audio_path) or None when the recording has
    no vocal stems at all. Priority: vocals_dry.flac > vocals_clean.flac >
    vocals.flac > vocals.wav.
    """
    for source_name, rel_path in R2_STEM_CANDIDATES:
        s3_key = f"{hash_prefix}/{rel_path}"
        if r2_client.file_exists(s3_key):
            return source_name, rel_path
    return None


def pick_dry_vocals(downloaded: list[Path]) -> Path | None:
    """Pick the dry-vocals file from downloaded MVSEP outputs.

    MVSEP de-reverb outputs are named like "vocals_(No Reverb).flac"; fall
    back to the first file when naming doesn't match.
    """
    if not downloaded:
        return None
    for path in downloaded:
        name_lower = path.name.lower()
        if "no reverb" in name_lower or "noreverb" in name_lower:
            return path
        if "no echo" in name_lower or "no_echo" in name_lower:
            return path
        if "dry" in name_lower:
            return path
    return downloaded[0]
