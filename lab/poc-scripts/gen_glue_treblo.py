"""Treblo instrumental Glue POC — one generated Glue at every adjacent junction of a 2–5 song
Songset, via the v2 inpainting endpoint.

For each adjacent junction the script builds both *arranged* sides from Postgres + R2 (every
Arrangement Step in playback order, exactly what the render plays), splices them into one uploaded
montage with a silent hole where the Glue belongs

    upload = [ A arranged last 12 s ][ silent hole = 4 beats ][ B arranged first 12 s ]

and asks Treblo to replace that one section of that one file:

    POST /v1/generations/v2/inpaint
    sections = [[12.0, 14.786493]]   selection_crop = true

The response is the new section only, so the seam is placed by construction rather than located
in the response afterwards: no needle search, no envelope alignment, no 3 s generation floor.
The returned section is not sample-exact (measured Δ -94.7 ms on a 3.438 s request), so its tail
is zero-padded or trimmed to the requested length and the delta is recorded per take. The outgoing
Song's Song Arrangement and the incoming Song's Song Arrangement stay sample-identical; only the
Glue is new:

    final = A arranged + staged Glue + B arranged

Glue lengths are whole beats of the *incoming* Song's tempo — 4 beats by default, and `--beats`
takes several lengths. Every Glue take is also assembled into one full-set file per take, next to
a production-parity baseline (real gaps / crossfades, raw PCM without loudnorm).

Outputs under <out-root>/<run-name>:

    context/        song-01-<hash8>.flac ... — every arranged side (splice sources)
    uploads/        canonical 44.1 kHz stereo WAVs that were uploaded (content-addressed)
    uploads.json    full SHA-256 -> {audio_upload_id, seconds, format, uploaded_at} (shared by runs)
    cache/          decoded source wavs (shared by runs) and cache/treblo-responses/ paid responses
    full-set/       <take>.flac (all junctions at that length) and baseline.flac
    junctions/jN/   per junction: montages/ plans/ responses/ glues/ full/ seams/
    index.json      every request field, seed and measured metric, per junction and for the full set
    report.html     self-contained listening + scoring page, one section per junction

A request costs 100 credits (the plan's balance was 3000), uploads are free, and a FAILURE task is
not charged. One junction, one 4-beat take, is therefore 100 credits. Every paid response is cached
by request body + take id under cache/treblo-responses/, so a rerun of the same take never buys the
same audio twice: an interrupted run resumes from its saved task id instead of resubmitting.

Usage:
    uv run --project lab/poc-scripts --extra glue_treblo \\
        python lab/poc-scripts/gen_glue_treblo.py --run-name run1
    uv run --project lab/poc-scripts --extra glue_treblo \\
        python lab/poc-scripts/gen_glue_treblo.py --run-name run1 --junctions 1 --beats 4,8
    uv run --project lab/poc-scripts --extra glue_treblo \\
        python lab/poc-scripts/gen_glue_treblo.py --dry-run --dry-run-items 5

`--dry-run` prints the montage geometry, the credits and the exact request body of every take
without touching Postgres, R2 or the API, so a tag or field error shows up before credits are spent.

Tags come from the closed v2 vocabulary in data/treblo-v2-tags.txt and are validated before any
request: the API rejects anything off-list with a 422 (a rejected request is not charged).

`POST /uploads` is free and fronts Cloudflare, so a transient edge/origin failure on it (524, the
other 520–523, 5xx, or no response at all) is retried three times at 30/60/120 s; the bytes are
cached, so a rerun re-sends exactly them for 0 credits. It also refuses audio Treblo already holds
("This upload matches existing audio"): that 422 carries no `audio_upload_id` and `/v1/uploads` has
no way to recover one, so the affected *junction* is recorded as `blocked` and skipped while the
rest of the run continues — before any credits are spent. Use recordings that
pass the upload; never transform audio to get past that filter.

Prerequisites: `TREBLO_API_KEY`, `SOW_DATABASE_URL`, `SOW_R2_BUCKET`, `SOW_R2_ENDPOINT_URL`,
`SOW_R2_ACCESS_KEY_ID`, `SOW_R2_SECRET_ACCESS_KEY` (read from /opt/sow/.env when unset) and
`ffmpeg`/`ffprobe` on PATH.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import os
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf
from stream_of_worship.admin.services.proxy import (
    ProxyConfigError,
    choose_proxy,
    get_proxy_pool,
    redact_proxy_url,
)

API = "https://api.treblo.com/v1"
ENDPOINT = "POST /generations/v2/inpaint"
ENV_FILE = Path("/opt/sow/.env")
TAG_FILE = Path(__file__).resolve().parent / "data" / "treblo-v2-tags.txt"
DEFAULT_SONGSET = "hbWfCccGxN_JLQGBEqmdu"
DEFAULT_TAGS = ("worship", "praise & worship")
DEFAULT_BEATS = (4,)
DEFAULT_UPLOAD_SECONDS = 12.0
MIN_UPLOAD_SECONDS, MAX_UPLOAD_SECONDS = 3.0, 30.0
SR = 44100
CHANNELS = 2
SAMPLE_WIDTH = 2  # PCM16
FADE_SECONDS = 0.02
EDGE_SECONDS = 0.2
EXCERPT_SECONDS = 15.0
MIN_SECTION_SECONDS = 0.1  # API floor for `sections`
MAX_UPLOAD_BYTES = 40 * 1024 * 1024
SECTION_TOLERANCE_SECONDS = 0.1  # crop length accepted before it counts as geometry error
MAX_TAGS = 64
CREDITS_PER_REQUEST = 100
UPLOAD_TTL_SECONDS = 20 * 3600.0  # ids expire "about 24 hours" after upload; re-upload with margin
UPLOAD_RETRY_SLEEPS = (30.0, 60.0, 120.0)  # 4 attempts; transient edge/origin blips only
# 524 is Cloudflare's origin timeout (observed 2026-11-23); 520-523 are the rest of its 5xx family;
# 500/502/503/504 are origin/gateway failures; 408/429 are retryable with no side effect.
RETRYABLE_UPLOAD_STATUSES = frozenset({408, 429, 500, 502, 503, 504, 520, 521, 522, 523, 524})
CONTENT_GUARD_MESSAGE = (
    "upload refused by the content guard (HTTP 422): {body}\n"
    "Treblo will not accept this audio as an inpainting source, and the response carries no "
    "audio_upload_id, so this montage cannot be submitted — /v1/uploads has no list or GET-by-id "
    "operation to recover one. This junction is skipped and nothing is charged. Do not transform "
    "the audio to get past the filter."
)
POLL_INTERVAL_SECONDS = 5.0
POLL_TIMEOUT_SECONDS = 900.0
DEFAULT_PROMPT_STRENGTH = 2.0
DEFAULT_BALANCE_STRENGTH = 0.7
OUTPUT_FORMAT = "flac"
MAX_SONGS = 5  # mirrors delivery/webapp/src/lib/constants.ts SONGSET_MAX_SONGS
DEFAULT_GAP_BEATS = 2.0  # mirrors calculate_gap_ms's NULL fallback in the render worker
# Used only by --dry-run, which touches neither Postgres nor R2.
# (arranged_ms, tempo_bpm) per simulated song, in playback order.
DRY_RUN_TEMPO_BPM = 86.13281
DRY_RUN_ITEMS = (
    (146510, 68.0),
    (177310, DRY_RUN_TEMPO_BPM),
    (150000, 74.0),
    (162000, 90.0),
    (140000, 80.0),
)
SUB_DIRS = ("context", "montages", "glues", "full-set", "junctions")
ENV_KEYS = (
    "TREBLO_API_KEY",
    "SOW_DATABASE_URL",
    "SOW_R2_BUCKET",
    "SOW_R2_ENDPOINT_URL",
    "SOW_R2_ACCESS_KEY_ID",
    "SOW_R2_SECRET_ACCESS_KEY",
)


class ApiFatalError(RuntimeError):
    """A response that no retry can fix: key, credits, 413, 422, poll timeout."""


class UploadBlocked(ApiFatalError):
    """The content guard refused this montage, so no audio_upload_id exists for it.

    Uploads are free, so this ends the junction, not the run: other junctions of the songset may
    still be accepted, and only an accepted montage can ever be charged.
    """


class TrebloTaskFailed(RuntimeError):
    """The task ended FAILURE: this take failed, the next one may still run."""


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------


def load_env_file(path: Path = ENV_FILE) -> None:
    """Fill missing environment variables from `path`; never print a value."""
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key not in ENV_KEYS:
            continue
        os.environ.setdefault(key, value.strip().strip("'\""))


def require_env() -> None:
    missing = [name for name in ENV_KEYS if not os.environ.get(name)]
    if missing:
        raise SystemExit(
            "missing environment variables: "
            + ", ".join(missing)
            + f" (set them in the environment or in {ENV_FILE})"
        )


# ---------------------------------------------------------------------------
# Audio helpers
# ---------------------------------------------------------------------------


def seconds_to_samples(seconds: float) -> int:
    return round(seconds * SR)


def _run(cmd: list[str]) -> None:
    proc = subprocess.run(
        cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, check=False
    )
    if proc.returncode != 0:
        raise RuntimeError(f"command failed: {' '.join(cmd)}\n{proc.stderr[-2000:]}")


def decode_audio(src: Path, dest_wav: Path) -> np.ndarray:
    """Decode any ffmpeg-readable file to 44.1 kHz stereo PCM16, return float32 samples."""
    dest_wav.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-i",
            str(src),
            "-ar",
            str(SR),
            "-ac",
            str(CHANNELS),
            "-c:a",
            "pcm_s16le",
            str(dest_wav),
        ]
    )
    return read_wav(dest_wav)


def read_wav(path: Path) -> np.ndarray:
    samples, sample_rate = sf.read(str(path), dtype="float32", always_2d=True)
    if sample_rate != SR:
        raise RuntimeError(f"{path} decoded at {sample_rate} Hz, expected {SR}")
    if samples.shape[1] != CHANNELS:
        raise RuntimeError(f"{path} decoded with {samples.shape[1]} channels, expected 2")
    return np.ascontiguousarray(samples, dtype=np.float32)


def write_flac(path: Path, samples: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), samples, SR, format="FLAC", subtype="PCM_16")


def write_wav_pcm16(path: Path, samples: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), samples, SR, format="WAV", subtype="PCM_16")


def slice_window(samples: np.ndarray, start: float, end: float) -> np.ndarray:
    first = max(0, seconds_to_samples(start))
    last = min(samples.shape[0], seconds_to_samples(end))
    if last <= first:
        raise RuntimeError(f"empty audio window [{start}, {end}]")
    return samples[first:last]


def rms_db(samples: np.ndarray) -> float:
    if samples.size == 0:
        raise ValueError("rms_db of an empty slice")
    mean_square = float(np.mean(np.square(samples, dtype=np.float64)))
    if mean_square <= 0.0:
        return float("-inf")
    return 20.0 * math.log10(math.sqrt(mean_square))


def peak_level(samples: np.ndarray) -> float:
    return float(np.max(np.abs(samples))) if samples.size else 0.0


def audio_duration_seconds(path: Path) -> float:
    out = subprocess.check_output(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=nw=1:nk=1",
            str(path),
        ],
        text=True,
    )
    return float(out.strip())


def pcm16_and_sha(samples: np.ndarray) -> tuple[np.ndarray, str]:
    """The exact PCM16 content that gets uploaded, plus its full SHA-256 cache key."""
    clipped = np.clip(samples.astype(np.float32, copy=False), -1.0, 1.0)
    ints = np.round(clipped * 32767.0).astype("<i2")
    return ints, hashlib.sha256(ints.tobytes()).hexdigest()


# ---------------------------------------------------------------------------
# Glue geometry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GlueSpec:
    """One Glue length: whole beats of the incoming tempo, and nothing else.

    Treblo replaces exactly the section that is asked for, so there is no generation floor to pad
    to and no tail to trim — the requested section *is* the Glue.
    """

    beats: int
    target_seconds: float

    @property
    def target_samples(self) -> int:
        return seconds_to_samples(self.target_seconds)


def glue_specs(beats: Sequence[int], tempo_bpm: float) -> list[GlueSpec]:
    """Glue lengths in beats of the incoming tempo, refused below the API's section floor."""
    if tempo_bpm <= 0:
        raise ValueError(f"tempo must be positive, got {tempo_bpm}")
    specs: list[GlueSpec] = []
    for count in beats:
        if count <= 0:
            raise ValueError(f"beat count must be positive, got {count}")
        target_seconds = count * 60.0 / tempo_bpm
        if target_seconds < MIN_SECTION_SECONDS:
            raise ValueError(
                f"{count} beats at {tempo_bpm:g} BPM is {target_seconds:.4f} s, under the API's "
                f"{MIN_SECTION_SECONDS:g} s section floor"
            )
        specs.append(GlueSpec(beats=int(count), target_seconds=target_seconds))
    return specs


def beat_seconds(tempo_bpm: float) -> float:
    return 60.0 / tempo_bpm


def junction_indices(item_count: int, requested: Sequence[int] | None) -> list[int]:
    """1-based junction indices to process; None means all adjacent junctions.

    Junction `j` joins item `j - 1` (outgoing) to item `j` (incoming), so a set of `n` songs has
    `n - 1` junctions. An out-of-range request is an error, never silently clamped or widened.
    """
    if item_count < 2:
        raise ValueError(f"a junction needs at least 2 songs, got {item_count}")
    if requested is None:
        return list(range(1, item_count))
    selected = sorted(set(requested))
    valid = range(1, item_count)
    bad = [value for value in selected if value not in valid]
    if bad:
        raise SystemExit(
            f"--junctions {','.join(str(value) for value in bad)} is outside 1..{item_count - 1} "
            f"(a {item_count}-song set has {item_count - 1} junction(s))"
        )
    return selected


# ---------------------------------------------------------------------------
# Tag vocabulary
# ---------------------------------------------------------------------------


def load_tag_vocabulary(path: Path = TAG_FILE) -> set[str]:
    """The closed tag list the v2 endpoint accepts; anything else is a 422."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise SystemExit(f"tag vocabulary not found at {path}: {exc}") from exc
    vocabulary = {line.strip() for line in text.splitlines() if line.strip()}
    if not vocabulary:
        raise SystemExit(f"tag vocabulary at {path} is empty")
    return vocabulary


def validate_tags(tags: Sequence[str], vocabulary: set[str]) -> None:
    """Refuse the request locally instead of spending a round trip on a 422."""
    if not tags:
        raise SystemExit(f"no --tags given; pick at least one tag from {TAG_FILE}")
    if len(tags) > MAX_TAGS:
        raise SystemExit(f"{len(tags)} tags given; the API accepts at most {MAX_TAGS}")
    unknown = [tag for tag in tags if tag not in vocabulary]
    if unknown:
        raise SystemExit(f"unknown Treblo tags: {json.dumps(unknown)} (not in {TAG_FILE})")


# ---------------------------------------------------------------------------
# Montage
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Montage:
    """The uploaded source: A's last N s, the silent hole, B's first N s."""

    samples: np.ndarray
    hole_start: int
    hole_end: int
    side_seconds: float

    @property
    def seconds(self) -> float:
        return self.samples.shape[0] / SR

    @property
    def wav_bytes(self) -> int:
        return int(self.samples.size) * SAMPLE_WIDTH + 44


def build_montage(
    a_song: np.ndarray, b_song: np.ndarray, spec: GlueSpec, upload_seconds: float
) -> Montage:
    """A's last / B's first `upload_seconds`, with `spec` beats of digital silence between them.

    The hole is the section Treblo replaces, so its geometry *is* the Glue geometry: the seam is
    placed by construction rather than located in the response afterwards.
    """
    frames = seconds_to_samples(upload_seconds)
    if frames > min(a_song.shape[0], b_song.shape[0]):
        raise ValueError(
            f"--upload-seconds {upload_seconds:g} exceeds an arranged side "
            f"({a_song.shape[0] / SR:.2f} s / {b_song.shape[0] / SR:.2f} s)"
        )
    a_part = a_song[-frames:]
    b_part = b_song[:frames]
    hole = np.zeros((spec.target_samples, CHANNELS), dtype=np.float32)
    samples = np.ascontiguousarray(np.concatenate([a_part, hole, b_part], axis=0))
    montage = Montage(
        samples=samples,
        hole_start=frames,
        hole_end=frames + spec.target_samples,
        side_seconds=upload_seconds,
    )
    if montage.wav_bytes > MAX_UPLOAD_BYTES:
        raise ValueError(
            f"montage {montage.wav_bytes / 1024 / 1024:.1f} MB exceeds the API's 40 MB upload limit"
        )
    if not np.array_equal(samples[: montage.hole_start], a_part):
        raise RuntimeError("montage invariant failed: A's side was altered")
    if not np.array_equal(samples[montage.hole_end :], b_part):
        raise RuntimeError("montage invariant failed: B's side was altered")
    if np.any(samples[montage.hole_start : montage.hole_end]):
        raise RuntimeError("montage invariant failed: the hole is not silent")
    return montage


def inpaint_body(
    upload_id: str,
    montage: Montage,
    *,
    tags: Sequence[str],
    instrumental: bool,
    lyrics: str | None,
    seed: int,
    prompt_strength: float,
    balance_strength: float,
    output_format: str = OUTPUT_FORMAT,
) -> dict[str, Any]:
    """The exact request body: one section of one uploaded file, nothing else.

    `audio_url`/`audio_base64` are never sent — the upload id names this run's montage, so the API
    cannot silently regenerate a different window.
    """
    body: dict[str, Any] = {
        "audio_upload_id": upload_id,
        "tags": list(tags),
        "sections": [[round(montage.hole_start / SR, 6), round(montage.hole_end / SR, 6)]],
        "selection_crop": True,
        "num_songs": 1,
        "seed": int(seed),
        "prompt_strength": float(prompt_strength),
        "balance_strength": float(balance_strength),
        "output_format": output_format,
    }
    if instrumental:
        body["instrumental"] = True
    else:
        text = (lyrics or "").strip()
        if not text:
            raise ValueError("--no-instrumental needs --lyrics: the API rejects a body without one")
        body["lyrics"] = text
    return body


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SongsetItem:
    """One Songset item plus the transition parameters the render applies *before* it."""

    position: int
    song_id: str
    title: str
    hash_prefix: str
    gap_beats: float | None
    crossfade_enabled: bool
    crossfade_duration_seconds: float | None
    steps: list[dict[str, Any]]


@dataclass(frozen=True)
class Recording:
    hash_prefix: str
    content_hash: str | None
    duration_seconds: float
    tempo_bpm: float | None
    musical_key: str | None
    musical_mode: str | None
    loudness_db: float | None


def load_arrangement_steps(raw: Any) -> list[dict[str, Any]]:
    """Steps in stored (playback) order — the render concatenates them in array order.

    Sorting by `startSeconds` would relocate the seam: item 0's Song Arrangement
    ends with its last *array* element, not with the chronologically last section.
    """
    if raw is None:
        return []
    data = raw if isinstance(raw, (list, dict)) else json.loads(raw)
    if isinstance(data, dict):
        data = data.get("steps") or data.get("arrangementSteps") or []
    return [
        step
        for step in data
        if isinstance(step, dict) and "startSeconds" in step and "endSeconds" in step
    ]


def arranged_audio(audio: np.ndarray, steps: list[dict[str, Any]], label: str) -> np.ndarray:
    """The audio the render actually plays for one Song: every step, in array order.

    Steps are sections of the recording, so the arranged audio is their concatenation — for this
    songset A's arrangement is 146.51 s assembled from five non-adjacent sections and B's is
    177.31 s from six. Slicing a plain time window around the seam would make the model hear
    audio the render may not even use at the seam.
    """
    if not steps:
        raise RuntimeError(f"{label}: no arrangement steps; cannot build arranged audio")
    segments = [
        slice_window(audio, float(step["startSeconds"]), float(step["endSeconds"]))
        for step in steps
    ]
    return np.ascontiguousarray(np.concatenate(segments, axis=0))


def fetch_songset(conn, songset_id: str) -> tuple[list[SongsetItem], dict[str, Recording]]:
    """Items (playback order) and their recordings; tempo comes from `recordings.tempo_bpm`."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT i.position, i.song_id, s.title, i.recording_hash_prefix, i.gap_beats,
                   i.crossfade_enabled, i.crossfade_duration_seconds, i.arrangement_steps
              FROM songset_items i
              JOIN songs s ON s.id = i.song_id
             WHERE i.songset_id = %s
             ORDER BY i.position
            """,
            (songset_id,),
        )
        item_rows = cur.fetchall()
        if len(item_rows) < 2:
            raise RuntimeError(f"songset {songset_id} has {len(item_rows)} items; need >= 2")
        if len(item_rows) > MAX_SONGS:
            raise SystemExit(
                f"songset {songset_id} has {len(item_rows)} items; the delivery limit is "
                f"{MAX_SONGS} songs — trim it first"
            )

        items = [
            SongsetItem(
                position=int(row[0]),
                song_id=str(row[1]),
                title=str(row[2]),
                hash_prefix=str(row[3]),
                gap_beats=float(row[4]) if row[4] is not None else None,
                crossfade_enabled=bool(row[5]),
                crossfade_duration_seconds=float(row[6]) if row[6] is not None else None,
                steps=load_arrangement_steps(row[7]),
            )
            for row in item_rows
        ]

        prefixes = [item.hash_prefix for item in items]
        cur.execute(
            """
            SELECT hash_prefix, content_hash, duration_seconds, tempo_bpm, musical_key,
                   musical_mode, loudness_db
              FROM recordings
             WHERE hash_prefix = ANY(%s)
            """,
            (prefixes,),
        )
        recordings: dict[str, Recording] = {}
        for row in cur.fetchall():
            recording = Recording(
                hash_prefix=str(row[0]),
                content_hash=str(row[1]) if row[1] is not None else None,
                duration_seconds=float(row[2]),
                tempo_bpm=float(row[3]) if row[3] is not None else None,
                musical_key=str(row[4]) if row[4] is not None else None,
                musical_mode=str(row[5]) if row[5] is not None else None,
                loudness_db=float(row[6]) if row[6] is not None else None,
            )
            recordings[recording.hash_prefix] = recording

    missing = [prefix for prefix in prefixes if prefix not in recordings]
    if missing:
        raise RuntimeError(f"no recordings row for hash prefixes {missing}")
    return items, recordings


def ensure_source_audio(hash_prefix: str, out_root: Path, connect) -> np.ndarray:
    """Download the R2 mp3 and decode it once; both files are cached under <out-root>/cache."""
    cache = out_root / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    mp3_path = cache / f"{hash_prefix}.mp3"
    wav_path = cache / f"{hash_prefix}.wav"

    if not mp3_path.exists():
        print(f"  downloading {hash_prefix} from R2", flush=True)
        connect().download_audio(hash_prefix, mp3_path)
    if wav_path.exists():
        return read_wav(wav_path)
    print(f"  decoding {mp3_path.name} -> {wav_path.name}", flush=True)
    return decode_audio(mp3_path, wav_path)


# ---------------------------------------------------------------------------
# Treblo API
# ---------------------------------------------------------------------------


def _api_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {os.environ['TREBLO_API_KEY']}"}


def load_proxy_pool() -> list[str] | None:
    """The `SOW_YOUTUBE_PROXY` port-range pool, or None when unset/blank.

    A malformed range is configuration, not an API answer: it aborts before any request, like the
    script's other CLI validation errors.
    """
    try:
        return get_proxy_pool()
    except ProxyConfigError as exc:
        raise SystemExit(f"SOW_YOUTUBE_PROXY is invalid: {exc}") from exc


def request_proxies() -> dict[str, str] | None:
    """One pool URL for this request, or None (then requests' own HTTPS_PROXY applies).

    Random per request, mirroring `RotatingProxyConfig.to_requests_dict` in the admin CLI: a retry
    exits from a different port, which is the point of the range notation. Deliberately uncached,
    like `get_proxy_pool`.
    """
    pool = load_proxy_pool()
    if not pool:
        return None
    chosen = choose_proxy(pool)
    # both keys carry the URL because requests routes an https:// target through CONNECT with the
    # `https` key; leaving it unset would silently bypass the proxy
    return {"http": chosen, "https": chosen}


def _fatal_from_response(status: int, body: str, what: str) -> ApiFatalError:
    if status in (401, 403):
        return ApiFatalError(
            f"{what} returned HTTP {status}: {body[:800]}\n"
            "The key is wrong, missing, or lacks inpainting access: set TREBLO_API_KEY in the "
            f"environment or in {ENV_FILE}. This is a key problem, not a code bug — nothing is "
            "retried."
        )
    if status == 402:
        return ApiFatalError(
            f"{what} returned HTTP 402 (payment required): {body[:800]}\n"
            "Out of credits — GET /v1/credits/balance reports what is left (the balance was 3000 "
            f"at plan time and one inpaint request costs {CREDITS_PER_REQUEST}). Nothing is "
            "retried."
        )
    if status == 413:
        return ApiFatalError(
            f"{what} rejected the file as larger than 40 MB (HTTP 413): {body[:800]}\n"
            "Nothing is retried; shrink --upload-seconds."
        )
    if status == 422:
        duplicate = "matches existing audio" in body
        hint = (
            "The upload contains audio Treblo already has, so it cannot be used as source: this "
            "recording is blocked for this account, and the fix is a different song — never "
            "transformed audio, which would be evading the filter. "
            if duplicate
            else "`detail` names each offending field or tag; a tag here means "
            f"{TAG_FILE} is stale. "
        )
        return ApiFatalError(
            f"{what} returned HTTP 422 (request rejected): {body[:1200]}\n"
            f"{hint}Nothing is retried, and a rejected request is not charged."
        )
    return ApiFatalError(f"{what} failed HTTP {status}: {body[:800]}")


def _post_upload(path: Path, mime: str, proxies: dict[str, str] | None = None) -> Any:
    import requests

    with path.open("rb") as handle:
        return requests.post(
            f"{API}/uploads",
            headers=_api_headers(),
            files={"file": (path.name, handle, mime)},
            timeout=900,
            proxies=proxies,
        )


def treblo_post_upload(
    wav_path: Path,
    flac_path: Path,
    post: Any = _post_upload,
    sleep=time.sleep,
) -> tuple[str, str]:
    """Upload one montage; returns (audio_upload_id, format).

    A single attempt is not reliable: Cloudflare fronts api.treblo.com and answers 524 when its
    origin is slower than ~100 s, so the bytes leave but no id comes back (recorded in
    test_arrangement/failure.json, 2026-11-23). Uploads cost no credits, so exactly the transient
    edge/origin family is retried, each attempt through the next proxy port; 401/402/413/415/422
    are answers, never retried.
    """
    import requests

    path, mime, used_format = wav_path, "audio/wav", "wav"
    retries = 0
    while True:
        proxies = request_proxies()
        if proxies:
            print(
                f"  upload attempt {retries + 1}/{len(UPLOAD_RETRY_SLEEPS) + 1} via "
                f"{redact_proxy_url(proxies['https'])}",
                flush=True,
            )
        try:
            resp = post(path, mime, proxies)
        except requests.exceptions.RequestException as exc:
            resp = None
            problem = f"{type(exc).__name__}: {exc}"
        else:
            problem = f"HTTP {resp.status_code}: {resp.text[:400]}"
        if resp is not None and resp.status_code < 400:
            return str(resp.json()["audio_upload_id"]), used_format
        if resp is not None and resp.status_code in (413, 415) and used_format == "wav":
            print(f"  upload rejected ({resp.status_code}); retrying as flac", flush=True)
            flac_path.parent.mkdir(parents=True, exist_ok=True)
            _run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-y",
                    "-i",
                    str(wav_path),
                    "-c:a",
                    "flac",
                    str(flac_path),
                ]
            )
            path, mime, used_format = flac_path, "audio/flac", "flac"
            continue
        if resp is not None and resp.status_code not in RETRYABLE_UPLOAD_STATUSES:
            if resp.status_code == 422 and "matches existing audio" in resp.text:
                raise UploadBlocked(CONTENT_GUARD_MESSAGE.format(body=resp.text[:1200]))
            raise _fatal_from_response(resp.status_code, resp.text, "upload")
        if retries >= len(UPLOAD_RETRY_SLEEPS):
            raise ApiFatalError(
                f"upload failed after {len(UPLOAD_RETRY_SLEEPS) + 1} attempts; last attempt: "
                f"{problem}\n"
                "Every attempt hit the same transient edge/origin failure (Cloudflare 524 family, "
                "5xx, or no response at all), so this is the vendor's upload path, not the audio. "
                "Nothing was charged — uploads are free — and the bytes are cached at "
                f"{path}, so a rerun re-sends exactly these bytes for 0 credits."
            )
        delay = UPLOAD_RETRY_SLEEPS[retries]
        retries += 1
        print(
            f"  upload attempt failed ({problem}); retrying in {delay:.0f} s "
            f"(retry {retries}/{len(UPLOAD_RETRY_SLEEPS)})",
            flush=True,
        )
        sleep(delay)


def load_uploads(out_root: Path) -> dict[str, dict[str, Any]]:
    path = out_root / "uploads.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def save_uploads(out_root: Path, uploads: dict[str, dict[str, Any]]) -> None:
    out_root.mkdir(parents=True, exist_ok=True)
    (out_root / "uploads.json").write_text(json.dumps(uploads, indent=2, sort_keys=True) + "\n")


def _upload_is_fresh(entry: dict[str, Any], now: datetime | None = None) -> bool:
    """Whether a cached `audio_upload_id` is still within the API's ~24 h lifetime."""
    stamp = entry.get("uploaded_at")
    if not isinstance(stamp, str):
        return False
    try:
        uploaded = datetime.fromisoformat(stamp)
    except ValueError:
        return False
    if uploaded.tzinfo is None:
        uploaded = uploaded.replace(tzinfo=UTC)
    return ((now or datetime.now(UTC)) - uploaded).total_seconds() < UPLOAD_TTL_SECONDS


def treblo_upload(
    samples: np.ndarray,
    out_root: Path,
    refresh: bool,
    label: str,
) -> dict[str, Any]:
    """Upload one montage once; the audio hash is the cache key, so a rerun never re-uploads.

    Uploads cost no credits, but the id is what every request body references, so a stale id (after
    an `--refresh-uploads`, or past the API's ~24 h upload lifetime) is the only way to ask about
    audio that is no longer this run's montage.
    """
    _, sha = pcm16_and_sha(samples)
    cache = load_uploads(out_root)
    if not refresh and sha in cache:
        entry = dict(cache[sha])
        if _upload_is_fresh(entry):
            print(
                f"  reusing upload for {label}: audio_upload_id {entry['audio_upload_id']} "
                f"({entry.get('format', 'wav')}, {entry['seconds']:.2f} s)",
                flush=True,
            )
            return {**entry, "sha256": sha}
        print(
            f"  cached upload for {label} is past the upload lifetime; uploading again",
            flush=True,
        )

    uploads_dir = out_root / "uploads"
    wav_path = uploads_dir / f"{sha[:16]}.wav"
    flac_path = uploads_dir / f"{sha[:16]}.flac"
    if not wav_path.is_file():
        write_wav_pcm16(wav_path, samples)
    seconds = samples.shape[0] / SR
    print(f"  uploading {label} {seconds:.2f} s (uploads cost no credits)", flush=True)
    audio_upload_id, used_format = treblo_post_upload(wav_path, flac_path)
    entry = {
        "audio_upload_id": audio_upload_id,
        "seconds": round(seconds, 3),
        "format": used_format,
        "uploaded_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    cache[sha] = entry
    save_uploads(out_root, cache)
    print(
        f"  uploaded {label}: audio_upload_id {audio_upload_id} ({used_format}, {seconds:.2f} s)",
        flush=True,
    )
    return {**entry, "sha256": sha}


def treblo_submit(body: dict[str, Any]) -> str:
    """POST the plan and return its task id.

    A charged submit whose payload names the id differently (`id`, `job_id`) must not raise a bare
    KeyError - the id is the only handle on already-spent credits - so every shape ends in either
    the id or an `ApiFatalError` that quotes the body.
    """
    import requests

    try:
        resp = requests.post(
            f"{API}/generations/v2/inpaint",
            headers={**_api_headers(), "Content-Type": "application/json"},
            json=body,
            timeout=120,
            proxies=request_proxies(),
        )
    except requests.exceptions.RequestException as exc:
        raise ApiFatalError(
            "inpaint submit did not complete at the transport layer: "
            f"{type(exc).__name__}: {exc}\n"
            "The request may still have been charged — check GET /v1/credits/balance before "
            "submitting again, and never resubmit a submit whose task id you did not receive."
        ) from exc
    if resp.status_code >= 400:
        raise _fatal_from_response(resp.status_code, resp.text, "inpaint")
    try:
        payload = resp.json()
    except ValueError as exc:
        raise ApiFatalError(
            f"inpaint returned a non-JSON body, so its task id is unknown: {resp.text[:1200]}"
        ) from exc
    if isinstance(payload, dict):
        for key in ("task_id", "id", "job_id"):
            value = payload.get(key)
            if value:
                return str(value)
    raise ApiFatalError(
        f"inpaint response carries no task id, so the request may be charged and unpollable: "
        f"{resp.text[:1200]}"
    )


def treblo_status(task_id: str) -> str:
    """One poll. The status body is a JSON *string*, or an object with `include_alignment`.

    While a task is queued the endpoint can answer with an empty or non-JSON body; that is "not
    finished yet", never a failed take, so it polls on instead of raising.
    """
    import requests

    try:
        resp = requests.get(
            f"{API}/generations/status/{task_id}",
            headers=_api_headers(),
            timeout=120,
            proxies=request_proxies(),
        )
    except requests.exceptions.RequestException as exc:
        raise ApiFatalError(
            f"status poll for task {task_id} failed at the transport layer: "
            f"{type(exc).__name__}: {exc}\n"
            "The task id is already recorded in the paid-response cache "
            f"(`{task_id}.request.json`), so rerunning polls it instead of buying another one."
        ) from exc

    if resp.status_code >= 400:
        raise _fatal_from_response(resp.status_code, resp.text, "status")
    text = resp.text.strip()
    if not text:
        return "PENDING"
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        # a bare status word, or an unparseable body: report it and keep polling
        return text[:40]
    if isinstance(payload, str):
        return payload
    if isinstance(payload, dict):
        return str(payload.get("status") or "PENDING")
    return "PENDING"


def treblo_task(task_id: str) -> dict[str, Any]:
    """The finished task: `song_paths`, `error_message`, `model_version`."""
    import requests

    try:
        resp = requests.get(
            f"{API}/generations/{task_id}",
            headers=_api_headers(),
            timeout=120,
            proxies=request_proxies(),
        )
    except requests.exceptions.RequestException as exc:
        raise ApiFatalError(
            f"task fetch for task {task_id} failed at the transport layer: "
            f"{type(exc).__name__}: {exc}\n"
            "The task was already charged and its id is recorded in the paid-response cache, so "
            "rerunning resumes it."
        ) from exc

    if resp.status_code >= 400:
        raise _fatal_from_response(resp.status_code, resp.text, "task")
    try:
        payload = resp.json()
    except ValueError as exc:
        raise ApiFatalError(f"task {task_id} returned a non-JSON body: {resp.text[:400]}") from exc
    if not isinstance(payload, dict):
        raise ApiFatalError(
            f"task {task_id} returned a {type(payload).__name__}, expected an object"
        )
    return payload


def treblo_wait(
    task_id: str,
    sleep=time.sleep,
    clock=time.monotonic,
) -> dict[str, Any]:
    """Poll until the task ends; FAILURE ends this take, a timeout ends the run."""
    deadline = clock() + POLL_TIMEOUT_SECONDS
    while True:
        status = treblo_status(task_id)
        if status == "SUCCESS":
            return treblo_task(task_id)
        if status == "FAILURE":
            task = treblo_task(task_id)
            raise TrebloTaskFailed(f"task {task_id} ended FAILURE: {task.get('error_message')}")
        if clock() >= deadline:
            raise ApiFatalError(
                f"task {task_id} still {status} after {POLL_TIMEOUT_SECONDS:.0f} s; nothing is "
                "retried"
            )
        sleep(POLL_INTERVAL_SECONDS)


def section_url(task: dict[str, Any]) -> str:
    """The CDN URL of the one returned section."""
    paths = task.get("song_paths") or []
    if isinstance(paths, str):
        paths = [paths]
    if not paths:
        raise ApiFatalError(f"task {task.get('task_id')} succeeded with no song_paths")
    return str(paths[0])


def treblo_download(url: str, dest: Path) -> bytes:
    """Fetch the finished audio. The URL is a CDN link, so it carries no Authorization header."""
    import requests

    resp = requests.get(url, timeout=600)
    if resp.status_code >= 400:
        raise ApiFatalError(f"download failed HTTP {resp.status_code}: {resp.text[:400]}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(resp.content)
    return resp.content


# ---------------------------------------------------------------------------
# Paid-request cache
# ---------------------------------------------------------------------------


def response_key(body: dict[str, Any], take_id: str) -> str:
    """Cache key for one paid inpaint: the request *and* the take it belongs to.

    The take id is part of the key because a fixed seed is not a reproducibility promise: two takes
    must never share cached audio.
    """
    payload = json.dumps({"endpoint": ENDPOINT, "body": body, "take": take_id}, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def treblo_generate_cached(
    body: dict[str, Any],
    take_id: str,
    cache_dir: Path,
    refresh: bool,
    sleep=time.sleep,
    clock=time.monotonic,
) -> tuple[bytes, dict[str, Any], bool]:
    """Generate once per (body, take); a paid response is never bought twice.

    Three files per key: `.request.json` (the task id, written right after a successful submit),
    `.bin` (the downloaded section) and `.meta.json` (the task dict). A key with a request but no
    `.bin` is an interrupted run: the saved task is polled, never resubmitted.
    """
    key = response_key(body, take_id)
    cache_dir.mkdir(parents=True, exist_ok=True)
    request_path = cache_dir / f"{key}.request.json"
    bin_path = cache_dir / f"{key}.bin"
    meta_path = cache_dir / f"{key}.meta.json"
    if refresh:
        for path in (request_path, bin_path, meta_path):
            if path.is_file():
                path.unlink()

    if bin_path.is_file():
        meta = json.loads(meta_path.read_text()) if meta_path.is_file() else {}
        return bin_path.read_bytes(), meta, True

    if request_path.is_file():
        task_id = str(json.loads(request_path.read_text())["task_id"])
        print(f"  resuming saved task {task_id}; not submitting again", flush=True)
    else:
        task_id = treblo_submit(body)
        request_path.write_text(
            json.dumps(
                {
                    "task_id": task_id,
                    "submitted_at": datetime.now(UTC).isoformat(timespec="seconds"),
                },
                indent=2,
            )
            + "\n"
        )
        print(f"  submitted task {task_id}", flush=True)

    task = {**treblo_wait(task_id, sleep=sleep, clock=clock), "task_id": task_id}
    raw = treblo_download(section_url(task), bin_path)
    meta_path.write_text(json.dumps(task, indent=2, ensure_ascii=False) + "\n")
    return raw, task, False


# ---------------------------------------------------------------------------
# Section geometry
# ---------------------------------------------------------------------------


def resolve_section(
    decoded: np.ndarray,
    montage: Montage,
    spec: GlueSpec,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Cut the Glue out of the response: the requested section, or the hole if crop was ignored.

    `selection_crop` should return exactly the requested section. Two responses are still usable:
    a section within `SECTION_TOLERANCE_SECONDS` of the request (padded or trimmed at the tail), and
    the whole montage (the API ignored `selection_crop`, so the hole is sliced out). Anything else
    is a geometry error and fails the take instead of splicing the wrong audio.

    `section_delta_ms` is the returned length minus the requested length. In the montage fallback
    the sliced section is exact by construction, so the delta is 0 and `returned_seconds` records
    the full montage instead.
    """
    returned = int(decoded.shape[0])
    target = spec.target_samples
    tolerance = seconds_to_samples(SECTION_TOLERANCE_SECONDS)
    detail: dict[str, Any] = {
        "returned_seconds": round(returned / SR, 4),
        "padded_ms": 0.0,
        "trimmed_ms": 0.0,
    }
    if abs(returned - target) <= tolerance:
        geometry = "crop"
        padded = max(0, target - returned)
        trimmed = max(0, returned - target)
        glue = np.zeros((target, CHANNELS), dtype=np.float32)
        copy = min(returned, target)
        glue[:copy] = decoded[:copy]
        detail["section_delta_ms"] = round((returned - target) / SR * 1000.0, 3)
        detail["padded_ms"] = round(padded / SR * 1000.0, 3)
        detail["trimmed_ms"] = round(trimmed / SR * 1000.0, 3)
    elif abs(returned - montage.samples.shape[0]) <= seconds_to_samples(0.5):
        geometry = "montage"
        glue = np.array(decoded[montage.hole_start : montage.hole_end], dtype=np.float32, copy=True)
        if glue.shape[0] != target:
            raise RuntimeError(
                f"montage fallback sliced {glue.shape[0]} samples, expected {target}"
            )
        detail["section_delta_ms"] = 0.0
    else:
        raise RuntimeError(
            f"returned {returned / SR:.3f} s is neither the requested section "
            f"({spec.target_seconds:.3f} s) nor the montage ({montage.seconds:.3f} s)"
        )
    return np.ascontiguousarray(glue), {"geometry": geometry, **detail}


# ---------------------------------------------------------------------------
# Staging, splicing, metrics
# ---------------------------------------------------------------------------


def _mono(samples: np.ndarray) -> np.ndarray:
    mono = samples.mean(axis=1) if samples.ndim == 2 else samples
    return np.asarray(mono, dtype=np.float64)


def stage_edges(
    glue: np.ndarray,
    a_edge: np.ndarray,
    b_edge: np.ndarray,
    limit_db: float = 18.0,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Gain-stage the glue from A's exit level to B's entry level, ramped in dB."""
    edge_n = max(1, min(seconds_to_samples(EDGE_SECONDS), glue.shape[0] // 2))
    head, tail = glue[:edge_n], glue[-edge_n:]
    requested = [rms_db(a_edge) - rms_db(head), rms_db(b_edge) - rms_db(tail)]
    applied = [max(-limit_db, min(limit_db, value)) for value in requested]
    gains_db = np.linspace(applied[0], applied[1], glue.shape[0], dtype=np.float64)
    staged = np.ascontiguousarray(
        (glue.astype(np.float64) * np.power(10.0, gains_db / 20.0)[:, None]).astype(np.float32)
    )
    peak = peak_level(staged)
    scaled = False
    if peak > 0.99:
        staged = np.ascontiguousarray(staged * np.float32(0.99 / peak))
        scaled = True
    staged_head, staged_tail = staged[:edge_n], staged[-edge_n:]
    info: dict[str, Any] = {
        "edge_gain_db": [round(applied[0], 3), round(applied[1], 3)],
        "edge_gain_requested_db": [round(requested[0], 3), round(requested[1], 3)],
        "edge_gain_clamped": [abs(requested[0]) > limit_db, abs(requested[1]) > limit_db],
        "edge_limit_db": limit_db,
        "a_residual_db": round(rms_db(a_edge) - rms_db(staged_head), 3),
        "b_residual_db": round(rms_db(b_edge) - rms_db(staged_tail), 3),
        "peak": round(peak_level(staged), 6),
        "peak_scaled": scaled,
    }
    return staged, info


def apply_fades(glue: np.ndarray, fade_seconds: float = FADE_SECONDS) -> np.ndarray:
    """20 ms linear fade in and out, applied to the glue only."""
    fade_n = min(seconds_to_samples(fade_seconds), glue.shape[0] // 2)
    faded = np.array(glue, dtype=np.float32, copy=True)
    if fade_n > 0:
        ramp = np.linspace(0.0, 1.0, fade_n, dtype=np.float32)
        faded[:fade_n] *= ramp[:, None]
        faded[-fade_n:] *= ramp[::-1][:, None]
    return np.ascontiguousarray(faded)


def splice(a: np.ndarray, glue: np.ndarray, b: np.ndarray) -> np.ndarray:
    """A + glue + B with A and B sample-identical, asserted before returning."""
    stitched = np.ascontiguousarray(np.concatenate([a, glue, b], axis=0))
    if not np.array_equal(stitched[: a.shape[0]], a):
        raise RuntimeError("splice invariant failed: A was altered")
    if not np.array_equal(stitched[-b.shape[0] :], b):
        raise RuntimeError("splice invariant failed: B was altered")
    if stitched.shape[0] != a.shape[0] + glue.shape[0] + b.shape[0]:
        raise RuntimeError("splice invariant failed: unexpected length")
    return stitched


def mix_songset(
    songs: Sequence[np.ndarray],
    incoming: Sequence[SongsetItem],
    tempos: Sequence[float],
) -> tuple[np.ndarray, list[dict[str, Any]]]:
    """Song timeline exactly as the render worker mixes it (minus loudnorm).

    `incoming[i]`/`tempos[i]` describe the junction before `songs[i + 1]`. A crossfade is
    `min(crossfade_duration_seconds, incoming arranged seconds)` and replaces the gap entirely.
    Mirrors `delivery/render-worker/src/sow_render_worker/audio_engine.py`: `calculate_gap_ms`,
    `get_crossfade_ms`, `start = prev_end + gap - crossfade`, linear `afade` in/out, `amix
    normalize=0`. Peak scaling above 0.99 keeps the written FLAC undistorted; production's
    loudnorm is not emulated.
    """
    if not songs:
        raise ValueError("mix_songset needs at least one song")
    if len(incoming) != len(songs) - 1:
        raise ValueError(
            f"mix_songset needs {len(songs) - 1} junction parameter sets for {len(songs)} songs, "
            f"got {len(incoming)}"
        )
    if len(tempos) != len(songs) - 1:
        raise ValueError(
            f"mix_songset needs {len(songs) - 1} tempos for {len(songs)} songs, got {len(tempos)}"
        )
    for index, tempo in enumerate(tempos):
        if not tempo > 0:
            raise ValueError(f"junction {index + 1}: tempo must be positive, got {tempo}")

    starts = [0]
    crosses = [0]
    gap_frames: list[int] = []
    gap_beats_of: list[float] = []
    for index in range(1, len(songs)):
        item = incoming[index - 1]
        cross = 0
        if item.crossfade_enabled and item.crossfade_duration_seconds:
            cross = min(round(item.crossfade_duration_seconds * SR), songs[index].shape[0])
        beats = item.gap_beats if item.gap_beats is not None else DEFAULT_GAP_BEATS
        gap = 0 if cross else round(beats * 60.0 / tempos[index - 1] * SR)
        starts.append(max(0, starts[index - 1] + songs[index - 1].shape[0] + gap - cross))
        crosses.append(cross)
        gap_frames.append(gap)
        gap_beats_of.append(float(beats))

    total = max(starts[index] + songs[index].shape[0] for index in range(len(songs)))
    buffer = np.zeros((total, CHANNELS), dtype=np.float32)
    for index, song in enumerate(songs):
        segment = np.array(song, dtype=np.float32, copy=True)
        fade_in = min(crosses[index], segment.shape[0])
        if fade_in:
            ramp_in = np.arange(fade_in, dtype=np.float32) / np.float32(crosses[index])
            segment[:fade_in] *= ramp_in[:, None]
        fade_out = min(crosses[index + 1], segment.shape[0]) if index + 1 < len(crosses) else 0
        if fade_out:
            ramp_out = 1.0 - np.arange(fade_out, dtype=np.float32) / np.float32(crosses[index + 1])
            segment[-fade_out:] *= ramp_out[:, None]
        start = starts[index]
        buffer[start : start + segment.shape[0]] += segment

    peak = peak_level(buffer)
    scaled = peak > 0.99
    if scaled:
        buffer *= np.float32(0.99 / peak)

    records = [
        {
            "mode": "crossfade" if crosses[index + 1] else "gap",
            "gap_beats": gap_beats_of[index],
            "gap_seconds": round(gap_frames[index] / SR, 6),
            "crossfade_seconds": round(crosses[index + 1] / SR, 6),
            "outgoing_seconds": round(songs[index].shape[0] / SR, 6),
            "incoming_seconds": round(songs[index + 1].shape[0] / SR, 6),
            "start_seconds": round(starts[index + 1] / SR, 6),
            "peak_scaled": scaled,
        }
        for index in range(len(incoming))
    ]
    return np.ascontiguousarray(buffer), records


def full_set_skip_reason(
    junctions: Sequence[dict[str, Any]], selected: Sequence[int], item_count: int
) -> str | None:
    """Why the full set cannot be assembled, or None when every junction has a Glue.

    `assemble_set` needs exactly one Glue per junction, so a junction with no Glue — a content-guard
    refusal, which has no `audio_upload_id` at all — must skip the build instead of shortening the
    list and failing inside the assembly.
    """
    blocked = [entry["index"] for entry in junctions if entry.get("status") == "blocked"]
    if blocked:
        return (
            "junction "
            + ", ".join(f"j{number}" for number in blocked)
            + " blocked by the content guard"
        )
    if len(selected) != item_count - 1:
        return "junction subset selected (--junctions)"
    return None


def assemble_set(songs: Sequence[np.ndarray], glues: Sequence[np.ndarray]) -> np.ndarray:
    """songs[0] + glues[0] + songs[1] + ... with every Song sample-identical."""
    if len(glues) != len(songs) - 1:
        raise ValueError(
            f"assemble_set needs {len(songs) - 1} glues for {len(songs)} songs, got {len(glues)}"
        )
    parts: list[np.ndarray] = []
    for index, song in enumerate(songs):
        parts.append(song)
        if index < len(glues):
            parts.append(glues[index])
    assembled = np.ascontiguousarray(np.concatenate(parts, axis=0))
    offset = 0
    for index, song in enumerate(songs):
        if not np.array_equal(assembled[offset : offset + song.shape[0]], song):
            raise RuntimeError(f"assemble invariant failed: song {index} was altered")
        offset += song.shape[0]
        if index < len(glues):
            offset += glues[index].shape[0]
    return assembled


def junction_excerpt(
    baseline: np.ndarray,
    a_frames: int,
    gap_frames: int,
    crossfade_frames: int,
    seconds: float = EXCERPT_SECONDS,
) -> np.ndarray:
    """The 2×EXCERPT_SECONDS window a listener needs to judge one junction baseline.

    For a crossfade junction `gap_frames` is 0, so the window is `seconds` of A before the overlap,
    the overlap, then `seconds` of B after it.
    """
    window = seconds_to_samples(seconds)
    start = max(0, a_frames - window - crossfade_frames)
    end = min(baseline.shape[0], a_frames + gap_frames + window)
    if end <= start:
        raise RuntimeError(f"empty junction excerpt window [{start}, {end}]")
    return np.ascontiguousarray(baseline[start:end])


def baseline_text(record: dict[str, Any]) -> str:
    """What the production transition at this junction actually does, in plain numbers."""
    if record.get("mode") == "crossfade":
        return f"{_num(record.get('crossfade_seconds'))} s crossfade"
    beats = record.get("gap_beats")
    beats_value = DEFAULT_GAP_BEATS if beats is None else float(beats)
    plural = "" if abs(beats_value - 1.0) < 1e-9 else "s"
    gap = _num(record.get("gap_seconds"))
    return f"{beats_value:g} beat{plural} ({gap} s) of silence"


def baseline_label(record: dict[str, Any]) -> str:
    return f"baseline — {baseline_text(record)}"


def baseline_summary(record: dict[str, Any]) -> str:
    kind = "crossfade" if record.get("mode") == "crossfade" else "gap"
    return f"current render — production {kind} at this junction"


def joint_metrics(
    samples: np.ndarray,
    index: int,
    fade_before: int = 0,
    fade_after: int = 0,
) -> dict[str, Any]:
    """Largest sample jump within ±5 ms vs the local 99.99th percentile, plus a 50 ms RMS step.

    The step windows skip the deliberate 20 ms fade (`fade_before` at a glue→original junction,
    `fade_after` at an original→glue junction); otherwise a perfect level match still reads -1.35 dB
    because the fade itself removes energy.
    """
    mono = _mono(samples)
    diffs = np.abs(np.diff(mono))
    lo = max(1, index - SR)
    hi = min(len(diffs), index + SR)
    jump_window = diffs[max(0, index - 220) : min(len(diffs), index + 220)]
    jump = float(jump_window.max()) if jump_window.size else 0.0
    local = float(np.percentile(diffs[lo:hi], 99.99)) if hi > lo else 0.0
    window = int(0.05 * SR)
    before = mono[max(0, index - fade_before - window) : max(0, index - fade_before)]
    after = mono[index + fade_after : index + fade_after + window]
    if before.size == 0 or after.size == 0:
        step_db = 0.0
    else:
        rms_before = math.sqrt(float(np.mean(np.square(before)))) + 1e-12
        rms_after = math.sqrt(float(np.mean(np.square(after)))) + 1e-12
        step_db = 20.0 * math.log10(rms_after / rms_before)
    ratio = jump / local if local > 0 else float("inf")
    return {
        "jump": round(jump, 6),
        "local_p9999": round(local, 6),
        "jump_ratio": round(ratio, 3) if math.isfinite(ratio) else None,
        "step_db": round(step_db, 3),
    }


def evaluate_status(metrics: dict[str, Any], splice_ok: bool) -> tuple[str, list[str]]:
    """ok / check / failed from the measured numbers; never a silent drop.

    There is no reference chunk to correlate against in a section replace, so the verdict rests on
    the Glue's own level, the returned section length, and the two spliced joints.
    """
    if not splice_ok:
        return "failed", ["splice invariant failed"]
    glue_rms = metrics.get("glue_rms_db")
    if glue_rms is None or not math.isfinite(glue_rms) or glue_rms < -50.0:
        return "failed", [f"glue level {glue_rms} dBFS is below -50 dBFS"]
    reasons: list[str] = []
    delta = metrics.get("section_delta_ms")
    if delta is not None and abs(delta) > 100.0:
        reasons.append(f"section length off by {delta} ms")
    padded = metrics.get("padded_ms")
    if padded:
        reasons.append(f"{padded} ms of padding at the glue tail")
    for key in ("a_residual_db", "b_residual_db"):
        value = metrics.get(key)
        if value is not None and abs(value) > 6.0:
            reasons.append(f"|{key}| {value} dB > 6 dB")
    for name, label in (("a", "A joint"), ("b", "B joint")):
        joint = metrics.get("joint", {}).get(name) or {}
        step = joint.get("step_db")
        if step is not None and abs(step) > 3.0:
            reasons.append(f"{label} level step {step} dB > 3 dB")
        ratio = joint.get("jump_ratio")
        if ratio is not None and ratio > 2.0:
            reasons.append(f"{label} click {ratio}x local 99.99th percentile")
    return ("check" if reasons else "ok"), reasons


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def _num(value: Any, digits: int = 3) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (int, float)):
        if not math.isfinite(float(value)):
            return "—"
        return f"{float(value):.{digits}f}"
    return str(value)


CSS = """
:root { color-scheme: light dark; }
body { font: 15px/1.5 system-ui, sans-serif; margin: 0 auto; max-width: 1100px; padding: 24px; }
h1 { font-size: 22px; margin: 0 0 4px; }
h2 { font-size: 17px; margin: 28px 0 8px; }
h3 { font-size: 15px; margin: 0 0 6px; display: flex; gap: 8px; align-items: center; }
.sub { color: #666; margin: 0 0 6px; }
.meta, .metrics { color: #666; font-size: 13px; margin: 6px 0; }
.metrics { font-variant-numeric: tabular-nums; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(420px, 1fr)); gap: 14px; }
.card { border: 1px solid #d0d0d0; border-radius: 10px; padding: 12px; }
.card.last-played { border-color: #2f6feb; box-shadow: 0 0 0 2px #2f6feb33; }
.badge { display: none; font-size: 11px; font-weight: 600; color: #2f6feb;
         border: 1px solid #2f6feb; border-radius: 999px; padding: 1px 8px; }
.card.last-played .badge { display: inline-block; }
.players { display: grid; grid-template-columns: 150px 1fr; gap: 6px 10px; align-items: center; }
.players span { font-size: 13px; color: #666; }
audio { width: 100%; }
.error { color: #b3261e; font-size: 13px; }
.score-ui { display: grid; grid-template-columns: 90px 1fr; gap: 6px 10px; align-items: center;
            margin-top: 10px; }
input[type=number] { width: 80px; }
textarea { width: 100%; min-height: 44px; font: inherit; }
table { border-collapse: collapse; width: 100%; margin-top: 8px; font-variant-numeric: tabular-nums; }
th, td { text-align: left; padding: 4px 8px; border-bottom: 1px solid #e0e0e0; font-size: 13px; }
th { color: #666; font-weight: 600; }
#last-played { position: sticky; top: 0; z-index: 2; background: Canvas; padding: 8px 0;
               border-bottom: 1px solid #d0d0d0; font-size: 13px; }
.toolbar { display: flex; gap: 8px; flex-wrap: wrap; margin: 10px 0; }
button { font: inherit; padding: 4px 10px; }
"""

JS = """
(function () {
  var RUN = document.body.dataset.run || 'run';
  var SCORE_KEY = 'sow.treblo-glue:' + RUN + ':scores';
  var LAST_KEY = 'sow.treblo-glue:' + RUN + ':lastPlayed';
  var scores = {};
  var lastPlayed = null;

  function readScores() {
    try { scores = JSON.parse(localStorage.getItem(SCORE_KEY) || '{}') || {}; }
    catch (e) { scores = {}; }
  }
  function writeScores() { localStorage.setItem(SCORE_KEY, JSON.stringify(scores)); }
  function readLast() {
    try { lastPlayed = JSON.parse(localStorage.getItem(LAST_KEY) || 'null'); }
    catch (e) { lastPlayed = null; }
  }
  function pad(n) { return n < 10 ? '0' + n : '' + n; }
  function clock(iso) {
    var d = iso ? new Date(iso) : new Date();
    if (isNaN(d.getTime())) { d = new Date(); }
    return pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds());
  }
  function cards() {
    return Array.prototype.slice.call(document.querySelectorAll('.card[data-take-id]'));
  }
  function cardFor(id) {
    return document.querySelector('.card[data-take-id="' + id + '"]');
  }
  function showLast(entry) {
    var bar = document.getElementById('last-played');
    if (!bar) { return; }
    bar.textContent = entry
      ? 'Last played: ' + entry.label + ' — ' + entry.kind + ' — ' + clock(entry.at)
      : 'Last played: —';
    cards().forEach(function (card) {
      var on = entry && card.dataset.takeId === entry.takeId;
      card.classList.toggle('last-played', !!on);
      var badge = card.querySelector('.badge');
      if (badge) { badge.hidden = !on; }
    });
  }
  function scoreOf(id) {
    var entry = scores[id];
    if (!entry || entry.score === null || entry.score === undefined) { return null; }
    var value = Number(entry.score);
    return Number.isNaN(value) ? null : value;
  }
  function refreshRanking() {
    Array.prototype.forEach.call(document.querySelectorAll('.rank-row'), function (row) {
      var id = row.dataset.takeId;
      var score = scoreOf(id);
      var entry = scores[id] || {};
      row.dataset.score = score === null ? '' : String(score);
      var cell = row.querySelector('.rank-score');
      if (cell) { cell.textContent = score === null ? '—' : score.toFixed(1); }
      var vocals = row.querySelector('.rank-vocals');
      if (vocals) { vocals.textContent = entry.vocals ? 'yes' : '—'; }
    });
    Array.prototype.forEach.call(document.querySelectorAll('tbody[data-sortable]'), function (body) {
      var rows = Array.prototype.slice.call(body.querySelectorAll('.rank-row'));
      rows.sort(function (a, b) {
        var av = a.dataset.score === '' ? -1 : Number(a.dataset.score);
        var bv = b.dataset.score === '' ? -1 : Number(b.dataset.score);
        if (av !== bv) { return bv - av; }
        return a.dataset.order - b.dataset.order;
      });
      rows.forEach(function (row) { body.appendChild(row); });
    });
  }
  function applyScores() {
    cards().forEach(function (card) {
      var id = card.dataset.takeId;
      var entry = scores[id] || {};
      var score = card.querySelector('.score-input');
      var note = card.querySelector('.note-input');
      var vocals = card.querySelector('.vocals-input');
      if (score) { score.value = entry.score === null || entry.score === undefined ? '' : entry.score; }
      if (note) { note.value = entry.note || ''; }
      if (vocals) { vocals.checked = !!entry.vocals; }
    });
    refreshRanking();
  }
  function record(card) {
    var id = card.dataset.takeId;
    var scoreInput = card.querySelector('.score-input');
    var noteInput = card.querySelector('.note-input');
    var vocalsInput = card.querySelector('.vocals-input');
    var raw = scoreInput ? scoreInput.value.trim() : '';
    var score = raw === '' ? null : Number(raw);
    if (score !== null && Number.isNaN(score)) { score = null; }
    if (score !== null) { score = Math.min(10, Math.max(0, score)); }
    scores[id] = {
      score: score,
      note: noteInput ? noteInput.value : '',
      vocals: !!(vocalsInput && vocalsInput.checked),
      updatedAt: new Date().toISOString()
    };
    writeScores();
    refreshRanking();
  }
  function exportScores() {
    var payload = {
      version: 1,
      run: RUN,
      exportedAt: new Date().toISOString(),
      scores: scores,
      lastPlayed: lastPlayed
    };
    var blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
    var url = URL.createObjectURL(blob);
    var link = document.createElement('a');
    link.href = url;
    link.download = 'scores-' + RUN + '.json';
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  }
  function importScores(file) {
    var reader = new FileReader();
    reader.onload = function () {
      var payload;
      try { payload = JSON.parse(String(reader.result)); }
      catch (e) { window.alert('not valid JSON: ' + e.message); return; }
      var incoming = payload && payload.scores ? payload.scores : {};
      Object.keys(incoming).forEach(function (key) { scores[key] = incoming[key]; });
      if (payload && payload.lastPlayed) { lastPlayed = payload.lastPlayed; }
      writeScores();
      if (lastPlayed) { localStorage.setItem(LAST_KEY, JSON.stringify(lastPlayed)); showLast(lastPlayed); }
      applyScores();
    };
    reader.readAsText(file);
  }
  function resetScores() {
    if (!window.confirm('Clear all scores for ' + RUN + '?')) { return; }
    localStorage.removeItem(SCORE_KEY);
    scores = {};
    applyScores();
  }
  document.addEventListener('DOMContentLoaded', function () {
    readScores();
    readLast();
    applyScores();
    showLast(lastPlayed);
    Array.prototype.forEach.call(document.querySelectorAll('audio'), function (audio) {
      audio.addEventListener('play', function () {
        var card = audio.closest('.card[data-take-id]');
        if (!card) { return; }
        lastPlayed = {
          takeId: card.dataset.takeId,
          label: card.dataset.label || card.dataset.takeId,
          kind: audio.dataset.kind || 'audio',
          at: new Date().toISOString()
        };
        localStorage.setItem(LAST_KEY, JSON.stringify(lastPlayed));
        showLast(lastPlayed);
      });
    });
    cards().forEach(function (card) {
      ['input', 'change'].forEach(function (name) {
        card.addEventListener(name, function (event) {
          var target = event.target;
          if (target.classList && (target.classList.contains('score-input') ||
              target.classList.contains('note-input') ||
              target.classList.contains('vocals-input'))) {
            record(card);
          }
        });
      });
    });
    var exportButton = document.getElementById('export-scores');
    if (exportButton) { exportButton.addEventListener('click', exportScores); }
    var resetButton = document.getElementById('reset-scores');
    if (resetButton) { resetButton.addEventListener('click', resetScores); }
    var importButton = document.getElementById('import-scores');
    var importInput = document.getElementById('import-file');
    if (importButton && importInput) {
      importButton.addEventListener('click', function () { importInput.click(); });
      importInput.addEventListener('change', function () {
        if (importInput.files && importInput.files[0]) { importScores(importInput.files[0]); }
      });
    }
  });
})();
"""


def _audio(src: str, kind: str, preload: str = "metadata") -> str:
    return (
        f'<audio controls preload="{preload}" data-kind="{html.escape(kind)}"'
        f' src="{html.escape(src)}"></audio>'
    )


def metric_text(take: dict[str, Any]) -> str:
    metrics = take.get("metrics") or {}
    joint = metrics.get("joint") or {}
    left = joint.get("a") or {}
    right = joint.get("b") or {}
    gains = metrics.get("edge_gain_db") or [None, None]
    task_id = str(metrics.get("task_id") or "")
    return (
        f"section requested {_num(take.get('target_seconds'))} s · returned "
        f"{_num(metrics.get('returned_seconds'))} s · Δ "
        f"{_num(metrics.get('section_delta_ms'), 1)} ms · geometry "
        f"{metrics.get('geometry') or '—'} · glue {_num(take.get('glue_seconds'))} s · "
        f"edge gain {_num(gains[0], 2)}/{_num(gains[1], 2)} dB · "
        f"residual {_num(metrics.get('a_residual_db'), 2)}/"
        f"{_num(metrics.get('b_residual_db'), 2)} dB · "
        f"A joint step {_num(left.get('step_db'), 2)} dB click "
        f"{_num(left.get('jump_ratio'), 2)}x · "
        f"B joint step {_num(right.get('step_db'), 2)} dB click "
        f"{_num(right.get('jump_ratio'), 2)}x · peak {_num(metrics.get('peak'))} · "
        f"task {task_id[:8] or '—'}"
    )


def card_html(take: dict[str, Any], baseline: bool = False) -> str:
    take_id = str(take.get("id"))
    files = take.get("files") or {}
    label = str(take.get("label") or take_id)
    is_baseline = baseline or take_id.endswith("baseline")
    players: list[tuple[str, str]] = []
    if not is_baseline and files.get("glue"):
        players.append(("<span>Glue (spliced)</span>", _audio(str(files["glue"]), "glue")))
    if not is_baseline and files.get("generation"):
        players.append(
            ("<span>Section (returned)</span>", _audio(str(files["generation"]), "generation"))
        )
    if files.get("seam"):
        players.append(("<span>Seam excerpt</span>", _audio(str(files["seam"]), "seam")))
    if files.get("full"):
        players.append(
            ("<span>Full pair</span>", _audio(str(files["full"]), "full", preload="none"))
        )
    body = (
        "".join(f"{tag}{audio}" for tag, audio in players)
        if players
        else ('<p class="meta">no audio written</p>')
    )
    status = take.get("status")
    error = take.get("error")
    reasons = take.get("reasons") or []
    metrics_line = baseline_text(take) if is_baseline else metric_text(take)
    detail = f'<p class="error">failed: {html.escape(str(error))}</p>' if error else ""
    if reasons and not error:
        # the prefix is the take's own status: a refused take must not read "check"
        detail += (
            f'<p class="error">{html.escape(str(status))}: '
            f"{html.escape('; '.join(str(r) for r in reasons))}</p>"
        )
    return f"""<article class="card" data-take-id="{html.escape(take_id)}"
  data-label="{html.escape(label)}">
  <h3>{html.escape(label)} <span class="badge" hidden>last played</span></h3>
  <p class="meta">status {html.escape(str(status))} · {html.escape(str(take.get("summary") or ""))}</p>
  <div class="players">{body}</div>
  <p class="metrics">{html.escape(metrics_line)}</p>
  {detail}
  <div class="score-ui">
    <label>score 0–10</label>
    <input class="score-input" type="number" min="0" max="10" step="0.5">
    <label>vocals heard</label>
    <input class="vocals-input" type="checkbox">
    <label>note</label>
    <textarea class="note-input" rows="2"></textarea>
  </div>
</article>"""


def ranking_table(takes: list[dict[str, Any]], failed: bool, heading: str) -> str:
    rows: list[str] = []
    for order, take in enumerate(takes):
        take_id = html.escape(str(take.get("id")))
        label = html.escape(str(take.get("label") or take.get("id")))
        beats = _num(take.get("beats"), 0)
        glue = take.get("glue_seconds")
        if glue is None:
            glue = take.get("gap_seconds") or take.get("crossfade_seconds")
        rows.append(
            f'<tr class="rank-row" data-take-id="{take_id}" data-order="{order}" data-score="">'
            f"<td>{label}</td><td>{beats}</td><td>{_num(glue)}</td>"
            f'<td class="rank-score">—</td><td class="rank-vocals">—</td></tr>'
        )
    if not rows:
        return f'<h2>{html.escape(heading)}</h2><p class="meta">none</p>'
    return (
        f"<h2>{html.escape(heading)}</h2><table><thead><tr><th>take</th><th>beats</th>"
        f"<th>glue s</th><th>score</th><th>vocals</th></tr></thead>"
        f'<tbody data-sortable="{"0" if failed else "1"}">{"".join(rows)}</tbody></table>'
    )


def context_card_html(item: dict[str, Any]) -> str:
    """One arranged side as played — the reference every take at its junctions is judged on."""
    title = str(item.get("title") or item.get("song_id"))
    source = item.get("file")
    player = (
        _audio(str(source), "context", preload="none")
        if source
        else '<p class="meta">no audio written</p>'
    )
    return f"""<article class="card">
  <h3>{html.escape(title)}</h3>
  <p class="meta">arranged {_num(item.get("arranged_seconds"), 2)} s ·
  {_num(item.get("tempo_bpm"), 5)} BPM · {html.escape(str(item.get("key")))} ·
  {_num(item.get("steps"), 0)} steps</p>
  <div class="players"><span>Arranged</span>{player}</div>
</article>"""


def full_set_card_html(entry: dict[str, Any]) -> str:
    """A whole-set player card — no score UI, because a full-set listen is not a take vote."""
    take_id = str(entry.get("id"))
    label = str(entry.get("label") or take_id)
    files = entry.get("files") or {}
    status = str(entry.get("status"))
    summary = str(entry.get("summary") or "")
    seconds = entry.get("seconds")
    players = (
        f"<span>Full set</span>{_audio(str(files['full']), 'full-set', preload='none')}"
        if files.get("full")
        else '<p class="meta">no audio written</p>'
    )
    error = entry.get("error")
    detail = f'<p class="error">unavailable: {html.escape(str(error))}</p>' if error else ""
    meta = f"status {html.escape(status)}"
    if seconds is not None:
        meta += f" · {_num(seconds, 2)} s"
    if summary:
        meta += f" · {html.escape(summary)}"
    return f"""<article class="card" data-take-id="{html.escape(take_id)}"
  data-label="{html.escape(label)}">
  <h3>{html.escape(label)} <span class="badge" hidden>last played</span></h3>
  <p class="meta">{meta}</p>
  <div class="players">{players}</div>
  {detail}
</article>"""


def report_html(index: dict[str, Any]) -> str:
    run = str(index.get("run"))
    items = index.get("items") or []
    junctions = index.get("junctions") or []
    full_set = index.get("full_set") or {}
    beats_text = ", ".join(str(beats) for beats in index.get("beats") or [])
    tags_text = ", ".join(str(tag) for tag in index.get("tags") or [])
    items_text = " · ".join(
        f"{item.get('title')} (arranged {_num(item.get('arranged_seconds'), 2)} s, "
        f"{_num(item.get('tempo_bpm'), 5)} BPM, {item.get('key')})"
        for item in items
    )
    context_cards = "".join(context_card_html(item) for item in items)

    full_baseline = full_set.get("baseline") or {}
    full_takes = full_set.get("takes") or []
    full_entries = ([full_baseline] if full_baseline else []) + full_takes
    full_cards = "".join(full_set_card_html(entry) for entry in full_entries)
    skipped = full_set.get("skipped")
    full_note = (
        f'<p class="meta">full set not built: {html.escape(str(skipped))}</p>' if skipped else ""
    )
    full_section = (
        f'<section><h2>Full set</h2>{full_note}<div class="grid">{full_cards}</div></section>'
    )

    sections: list[str] = []
    for junction in junctions:
        baseline = junction.get("baseline") or {}
        if not baseline:
            continue
        takes = junction.get("takes") or []
        ok_takes = [take for take in takes if take.get("status") != "failed"]
        failed_takes = [take for take in takes if take.get("status") == "failed"]
        number = junction.get("index")
        montage = junction.get("montage") or {}
        cards = card_html(baseline, baseline=True) + "".join(card_html(take) for take in ok_takes)
        failed_cards = "".join(card_html(take) for take in failed_takes)
        failed_block = (
            f'<h3>Failed takes</h3><div class="grid">{failed_cards}</div>' if failed_cards else ""
        )
        rankable = ok_takes + [baseline]
        blocked_note = (
            f'<p class="error">blocked: {html.escape(str(junction.get("blocked_reason")))}</p>'
            if junction.get("status") == "blocked"
            else ""
        )
        sections.append(f"""<section id="junction-j{html.escape(str(number))}">
  <h2>Junction {html.escape(str(number))} —
  {html.escape(str((junction.get("from") or {}).get("title")))} →
  {html.escape(str((junction.get("to") or {}).get("title")))}</h2>
  <p class="meta">incoming tempo {_num(junction.get("tempo_bpm"), 5)} BPM · beat
  {_num(junction.get("beat_seconds"), 5)} s · baseline {html.escape(baseline_text(baseline))} ·
  montage {_num(montage.get("seconds"), 2)} s ({_num(montage.get("side_seconds"), 2)} s sides,
  hole {_num(montage.get("hole_seconds"))} s)</p>
  {blocked_note}
  <div class="grid">{cards}</div>
  {failed_block}
  {ranking_table(rankable, False, f"Junction {number} ranking (score desc, unscored last)")}
  {ranking_table(failed_takes, True, f"Junction {number} failed takes")}
</section>""")

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Treblo Glue POC — {html.escape(run)}</title>
<style>{CSS}</style>
</head>
<body data-run="{html.escape(run)}">
  <h1>Treblo instrumental Glue — {html.escape(run)}</h1>
  <p class="sub">songset {html.escape(str(index.get("songset_id")))} ·
  {html.escape(items_text)}</p>
  <p class="sub">{len(items)} songs · {len(junctions)} junction(s) · beats
  {html.escape(beats_text)} · engine treblo · {html.escape(str(index.get("endpoint")))} · model
  {html.escape(str(index.get("model_version")))} · tags {html.escape(tags_text)} ·
  instrumental {_num(bool(index.get("instrumental")))} · prompt_strength
  {_num(index.get("prompt_strength"), 2)} · balance_strength
  {_num(index.get("balance_strength"), 2)} · selection_crop
  {_num(bool(index.get("selection_crop")))} · {_num(index.get("upload_seconds"), 2)} s sides ·
  {_num(index.get("credits_spent"), 0)}/{_num(index.get("credits_estimate"), 0)} credits ·
  baseline mix is raw PCM (no loudnorm)</p>
  <div id="last-played">Last played: —</div>
  <div class="toolbar">
    <button id="export-scores" type="button">Export scores</button>
    <button id="import-scores" type="button">Import scores</button>
    <button id="reset-scores" type="button">Reset scores</button>
    <input id="import-file" type="file" accept="application/json" hidden>
  </div>
  <section><h2>Context</h2><div class="grid">{context_cards}</div></section>
  {full_section}
  {"".join(sections)}
  <script>{JS}</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _ints(text: str) -> list[int]:
    values = [chunk.strip() for chunk in text.split(",") if chunk.strip()]
    if not values:
        raise argparse.ArgumentTypeError("expected a comma-separated list of beat counts")
    try:
        return [int(value) for value in values]
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _tag_list(text: str) -> list[str]:
    return [chunk.strip() for chunk in text.split(",") if chunk.strip()]


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate instrumental Glues at every adjacent junction of a 2–5 song Songset "
            "(Treblo v2 inpaint: one montage, one replaced section)."
        )
    )
    parser.add_argument("--songset", default=DEFAULT_SONGSET, help="songset id (2..5 items)")
    parser.add_argument(
        "--beats",
        type=_ints,
        default=list(DEFAULT_BEATS),
        help="glue lengths in beats of the incoming tempo (default 4; try 2,4,8 to sweep)",
    )
    parser.add_argument(
        "--junctions",
        type=_ints,
        default=None,
        help="1-based junction indices (default: every adjacent junction)",
    )
    parser.add_argument("--takes", type=int, default=1, help="takes per glue length")
    parser.add_argument("--seed-base", type=int, default=0, help="seed for take 0")
    parser.add_argument("--run-name", default="run1", help="run directory name")
    parser.add_argument(
        "--out-root",
        type=Path,
        default=Path("lab/poc-scripts/output/treblo-glue"),
        help="output root (cache and uploads are shared by runs)",
    )
    parser.add_argument(
        "--tags",
        type=_tag_list,
        default=list(DEFAULT_TAGS),
        help="comma-separated tags, each from data/treblo-v2-tags.txt",
    )
    parser.add_argument(
        "--instrumental",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="ask for an instrumental section (default); --no-instrumental needs --lyrics",
    )
    parser.add_argument(
        "--lyrics", default=None, help="lyrics for a vocal Glue (with --no-instrumental)"
    )
    parser.add_argument(
        "--prompt-strength",
        type=float,
        default=DEFAULT_PROMPT_STRENGTH,
        help="prompt adherence, 1.4..3.1",
    )
    parser.add_argument(
        "--balance-strength",
        type=float,
        default=DEFAULT_BALANCE_STRENGTH,
        help="v2 vocal/instrument balance: higher is more natural vocals, lower sharper "
        "instrumentals, 0.0..1.0",
    )
    parser.add_argument(
        "--upload-seconds",
        type=float,
        default=DEFAULT_UPLOAD_SECONDS,
        help="A's last / B's first N seconds inside the montage, 3..30",
    )
    parser.add_argument("--output-format", default=OUTPUT_FORMAT)
    parser.add_argument("--dry-run", action="store_true", help="print plans; no network, no writes")
    parser.add_argument(
        "--dry-run-items",
        type=int,
        choices=(2, 3, 4, 5),
        default=2,
        help="simulated songset size for --dry-run",
    )
    parser.add_argument("--force", action="store_true", help="reuse a non-empty run directory")
    parser.add_argument("--refresh-uploads", action="store_true", help="ignore the upload cache")
    parser.add_argument(
        "--refresh-responses",
        action="store_true",
        help="submit again instead of reusing a cached response (costs credits)",
    )
    args = parser.parse_args(argv)
    if not 1.4 <= args.prompt_strength <= 3.1:
        raise SystemExit("--prompt-strength must be within 1.4..3.1")
    if not 0.0 <= args.balance_strength <= 1.0:
        raise SystemExit("--balance-strength must be within 0.0..1.0")
    if not MIN_UPLOAD_SECONDS <= args.upload_seconds <= MAX_UPLOAD_SECONDS:
        raise SystemExit(
            f"--upload-seconds must be within {MIN_UPLOAD_SECONDS:g}..{MAX_UPLOAD_SECONDS:g}"
        )
    if args.instrumental and args.lyrics is not None:
        raise SystemExit("--lyrics needs --no-instrumental")
    return args


def dry_run(args: argparse.Namespace) -> int:
    """Print montage geometry, credits and request bodies without touching Postgres, R2 or the API."""
    validate_tags(args.tags, load_tag_vocabulary())
    items = [
        {
            "title": f"Song {index + 1:02d}",
            "arranged_ms": int(arranged_ms),
            "tempo_bpm": float(tempo),
        }
        for index, (arranged_ms, tempo) in enumerate(DRY_RUN_ITEMS[: args.dry_run_items])
    ]
    try:
        selected = junction_indices(len(items), args.junctions)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    tempos_text = ", ".join(f"{item['tempo_bpm']:g}" for item in items)
    print(
        f"dry run: songset {args.songset}, {len(items)} simulated songs "
        f"(tempo stand-ins {tempos_text} BPM), junctions "
        f"{','.join(str(junction) for junction in selected)}, beats "
        f"{','.join(str(beats) for beats in args.beats)}, tags {', '.join(args.tags)}",
        flush=True,
    )

    requests = 0
    plan_items: list[tuple[int, GlueSpec, dict[str, Any], float]] = []
    for junction in selected:
        a_item, b_item = items[junction - 1], items[junction]
        tempo = b_item["tempo_bpm"]
        print(
            f"junction j{junction}: {a_item['title']} ({a_item['arranged_ms'] / 1000.0:.2f} s) -> "
            f"{b_item['title']} ({b_item['arranged_ms'] / 1000.0:.2f} s, {tempo} BPM, "
            f"beat {beat_seconds(tempo):.5f} s)",
            flush=True,
        )
        print(
            f"baseline j{junction}: gap {DEFAULT_GAP_BEATS * beat_seconds(tempo):.3f} s "
            f"(gap_beats {DEFAULT_GAP_BEATS:g})",
            flush=True,
        )
        try:
            specs = glue_specs(args.beats, tempo)
        except ValueError as exc:
            raise SystemExit(f"junction j{junction}: {exc}") from exc
        print(
            f"{'beats':>5} {'target s':>9} {'section frames':>15} {'montage s':>10} "
            f"{'upload MB':>10} {'credits':>8}",
            flush=True,
        )
        sides = np.zeros((seconds_to_samples(args.upload_seconds), CHANNELS), dtype=np.float32)
        for spec in specs:
            try:
                montage = build_montage(sides, sides, spec, args.upload_seconds)
            except ValueError as exc:
                raise SystemExit(f"junction j{junction}: {exc}") from exc
            upload_id = "<UPLOAD_ID>"
            body = inpaint_body(
                upload_id,
                montage,
                tags=args.tags,
                instrumental=args.instrumental,
                lyrics=args.lyrics,
                seed=args.seed_base,
                prompt_strength=args.prompt_strength,
                balance_strength=args.balance_strength,
                output_format=args.output_format,
            )
            print(
                f"{spec.beats:>5} {spec.target_seconds:>9.4f} {spec.target_samples:>15} "
                f"{montage.seconds:>10.3f} {montage.wav_bytes / 1e6:>10.2f} "
                f"{CREDITS_PER_REQUEST * args.takes:>8}",
                flush=True,
            )
            plan_items.append((junction, spec, body, montage.seconds))
            requests += args.takes

    print(
        f"est. credits: {requests} × {CREDITS_PER_REQUEST} = {requests * CREDITS_PER_REQUEST}",
        flush=True,
    )
    for junction, spec, body, montage_seconds in plan_items:
        print(
            f"request j{junction}/beats-{spec.beats} (montage {montage_seconds:.3f} s):", flush=True
        )
        print(json.dumps(body, indent=2, ensure_ascii=False), flush=True)
    return 0


def _take_index(
    spec: GlueSpec, take_number: int, takes: int, junction: int
) -> tuple[str, str, dict[str, str], str]:
    """(display id, response-cache take id, files, label).

    The cache take id is the vendor-independent `beats-4` / `beats-4-t2` string; the display id
    carries the junction and is what the index, the HTML cards and `localStorage` use.
    """
    cache_take = f"beats-{spec.beats}" if takes == 1 else f"beats-{spec.beats}-t{take_number + 1}"
    take_id = f"j{junction}-{cache_take}"
    base = f"junctions/j{junction}"
    files = {
        "glue": f"{base}/glues/{cache_take}.flac",
        "generation": f"{base}/glues/{cache_take}-section.flac",
        "seam": f"{base}/seams/{cache_take}.flac",
        "full": f"{base}/full/{cache_take}.flac",
    }
    label = f"{cache_take} — {spec.beats} beat{'' if spec.beats == 1 else 's'}"
    return take_id, cache_take, files, label


def _prepare_run_dir(run_dir: Path, force: bool) -> None:
    if run_dir.exists() and any(run_dir.iterdir()) and not force:
        raise SystemExit(
            f"{run_dir} already has content; pass --force to reuse it or pick another --run-name"
        )
    for sub in SUB_DIRS:
        (run_dir / sub).mkdir(parents=True, exist_ok=True)
    stale_failure = run_dir / "failure.json"
    if force and stale_failure.is_file():
        # a reused run directory must not keep reporting the previous attempt's fatal error
        stale_failure.unlink()


def _write_index(run_dir: Path, index: dict[str, Any]) -> None:
    (run_dir / "index.json").write_text(
        json.dumps(index, indent=2, sort_keys=False, ensure_ascii=False) + "\n"
    )


def _write_failure(run_dir: Path, run_name: str, songset: str, message: str) -> None:
    """Record a fatal error next to the artifacts, whoever hit it."""
    if not run_dir.is_dir():
        return
    (run_dir / "failure.json").write_text(
        json.dumps(
            {
                "run": run_name,
                "songset_id": songset,
                "failed_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "error": message,
            },
            indent=2,
        )
        + "\n"
    )
    print(f"  failure recorded in {run_dir / 'failure.json'}", flush=True)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.dry_run:
        return dry_run(args)
    try:
        return _execute_run(args)
    except ApiFatalError as exc:
        print(f"error: {exc}", flush=True)
        _write_failure(Path(args.out_root) / args.run_name, args.run_name, args.songset, str(exc))
        return 2


def _execute_run(args: argparse.Namespace) -> int:
    load_env_file()
    require_env()
    pool = load_proxy_pool()
    if pool:
        print(
            f"  proxy pool: {len(pool)} port(s) via "
            f"{redact_proxy_url(os.environ['SOW_YOUTUBE_PROXY'])}",
            flush=True,
        )

    tags = list(args.tags)
    validate_tags(tags, load_tag_vocabulary())
    out_root = Path(args.out_root)
    run_dir = out_root / args.run_name
    _prepare_run_dir(run_dir, args.force)

    started = datetime.now(UTC).isoformat(timespec="seconds")

    import psycopg
    from stream_of_worship.admin.services.r2 import R2Client

    r2_client: list[R2Client] = []

    def connect_r2() -> R2Client:
        if not r2_client:
            r2_client.append(
                R2Client(
                    bucket=os.environ["SOW_R2_BUCKET"],
                    endpoint_url=os.environ["SOW_R2_ENDPOINT_URL"],
                    region="auto",
                )
            )
        return r2_client[0]

    print(f"songset {args.songset} -> {run_dir}", flush=True)
    with psycopg.connect(os.environ["SOW_DATABASE_URL"]) as conn:
        items, recordings = fetch_songset(conn, args.songset)

    songs: list[np.ndarray] = []
    tempos: list[float] = []
    items_index: list[dict[str, Any]] = []
    for position, item in enumerate(items, start=1):
        recording = recordings[item.hash_prefix]
        print(
            f"  item {item.position}: {item.song_id} [{item.hash_prefix}] "
            f"{recording.duration_seconds:.2f}s {recording.tempo_bpm} BPM "
            f"{recording.musical_key}/{recording.musical_mode} steps {len(item.steps)}",
            flush=True,
        )
        if not recording.tempo_bpm:
            raise SystemExit(
                f"recording {item.hash_prefix} (item {item.position}) has no tempo_bpm; "
                "cannot size the glue"
            )
        source = ensure_source_audio(item.hash_prefix, out_root, connect_r2)
        song = arranged_audio(source, item.steps, f"item {position} ({item.title})")
        relative = f"context/song-{position:02d}-{item.hash_prefix[:8]}.flac"
        write_flac(run_dir / relative, song)
        songs.append(song)
        tempos.append(float(recording.tempo_bpm))
        items_index.append(
            {
                "position": item.position,
                "song_id": item.song_id,
                "title": item.title,
                "hash_prefix": item.hash_prefix,
                "arranged_seconds": round(song.shape[0] / SR, 2),
                "tempo_bpm": recording.tempo_bpm,
                "key": f"{recording.musical_key} {recording.musical_mode}".strip(),
                "steps": len(item.steps),
                "file": relative,
            }
        )
    try:
        selected = junction_indices(len(items), args.junctions)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    arranged_text = ", ".join(f"{entry['arranged_seconds']:.2f}" for entry in items_index)
    print(
        f"  {len(items)} songs ({arranged_text} s arranged) -> junctions "
        f"{','.join(str(junction) for junction in selected)}, beats {list(args.beats)}",
        flush=True,
    )

    baseline_mix, baseline_records = mix_songset(songs, items[1:], tempos[1:])
    write_flac(run_dir / "full-set" / "baseline.flac", baseline_mix)
    baseline_modes = ", ".join(
        f"j{position + 1} {record['mode']}" for position, record in enumerate(baseline_records)
    )
    print(
        f"  full-set baseline {baseline_mix.shape[0] / SR:.2f} s ({baseline_modes})",
        flush=True,
    )

    index: dict[str, Any] = {
        "version": 1,
        "run": args.run_name,
        "created_at": started,
        "engine": "treblo",
        "endpoint": ENDPOINT,
        "songset_id": args.songset,
        "model_version": None,
        "tags": tags,
        "instrumental": bool(args.instrumental),
        "prompt_strength": args.prompt_strength,
        "balance_strength": args.balance_strength,
        "selection_crop": True,
        "output_format": args.output_format,
        "upload_seconds": args.upload_seconds,
        "beats": list(args.beats),
        "credits_estimate": 0,
        "credits_spent": 0,
        "items": items_index,
        "junctions": [],
        "full_set": {
            "baseline": {
                "id": "full-set-baseline",
                "kind": "full-set",
                "label": "full set — baseline (production gaps/crossfades)",
                "status": "ok",
                "seconds": round(baseline_mix.shape[0] / SR, 4),
                "files": {"full": "full-set/baseline.flac"},
            },
            "takes": [],
            "skipped": None,
        },
    }

    prepared: list[dict[str, Any]] = []
    for junction in selected:
        a_song, b_song = songs[junction - 1], songs[junction]
        a_item, b_item = items[junction - 1], items[junction]
        b_tempo = tempos[junction]
        try:
            specs = glue_specs(args.beats, b_tempo)
        except ValueError as exc:
            raise SystemExit(f"junction j{junction}: {exc}") from exc
        boundary = run_dir / "junctions" / f"j{junction}"
        for sub in ("plans", "responses", "montages", "glues", "full", "seams"):
            (boundary / sub).mkdir(parents=True, exist_ok=True)
        junction_mix, junction_records = mix_songset([a_song, b_song], [b_item], [b_tempo])
        record = junction_records[0]
        write_flac(boundary / "full" / "baseline.flac", junction_mix)
        write_flac(
            boundary / "seams" / "baseline.flac",
            junction_excerpt(
                junction_mix,
                a_song.shape[0],
                seconds_to_samples(record["gap_seconds"]),
                seconds_to_samples(record["crossfade_seconds"]),
            ),
        )

        montages: dict[int, Montage] = {}
        montage_info: dict[int, dict[str, Any]] = {}
        blocked: str | None = None
        for spec in specs:
            try:
                montage = build_montage(a_song, b_song, spec, args.upload_seconds)
            except ValueError as exc:
                raise SystemExit(f"junction j{junction}: {exc}") from exc
            relative = f"junctions/j{junction}/montages/beats-{spec.beats}.flac"
            write_flac(run_dir / relative, montage.samples)
            montages[spec.beats] = montage
            montage_info[spec.beats] = {
                "file": relative,
                "seconds": round(montage.seconds, 6),
                "side_seconds": round(montage.side_seconds, 6),
                "hole_start_seconds": round(montage.hole_start / SR, 6),
                "hole_seconds": round((montage.hole_end - montage.hole_start) / SR, 6),
                "wav_bytes": montage.wav_bytes,
                "sha256": None,
                "audio_upload_id": None,
            }
            try:
                upload = treblo_upload(
                    montage.samples,
                    out_root,
                    args.refresh_uploads,
                    f"j{junction} montage beats-{spec.beats} ({montage.seconds:.2f} s)",
                )
            except UploadBlocked as exc:
                blocked = str(exc)
                break
            montage_info[spec.beats]["sha256"] = upload["sha256"]
            montage_info[spec.beats]["audio_upload_id"] = upload["audio_upload_id"]

        entry: dict[str, Any] = {
            "id": f"j{junction}",
            "index": junction,
            "from": {
                "position": a_item.position,
                "song_id": a_item.song_id,
                "title": a_item.title,
                "hash_prefix": a_item.hash_prefix,
            },
            "to": {
                "position": b_item.position,
                "song_id": b_item.song_id,
                "title": b_item.title,
                "hash_prefix": b_item.hash_prefix,
            },
            "tempo_bpm": b_tempo,
            "beat_seconds": round(beat_seconds(b_tempo), 6),
            "status": "blocked" if blocked else "ok",
            "blocked_reason": blocked,
            "montage": montage_info[specs[0].beats],
            "baseline": {
                "id": f"j{junction}-baseline",
                "label": baseline_label(record),
                "summary": baseline_summary(record),
                "status": "ok",
                **record,
                "seconds": round(junction_mix.shape[0] / SR, 6),
                "files": {
                    "seam": f"junctions/j{junction}/seams/baseline.flac",
                    "full": f"junctions/j{junction}/full/baseline.flac",
                },
            },
            "takes": [],
        }
        index["junctions"].append(entry)
        if blocked:
            print(f"  junction j{junction}: blocked — {blocked.splitlines()[0]}", flush=True)
            continue
        prepared.append(
            {
                "index": junction,
                "entry": entry,
                "boundary": boundary,
                "specs": specs,
                "a_song": a_song,
                "b_song": b_song,
                "a_item": a_item,
                "b_item": b_item,
                "montages": montages,
                "montage_info": montage_info,
                "context_a": a_song[-round(EDGE_SECONDS * SR) :],
                "context_b": b_song[: round(EDGE_SECONDS * SR)],
                "work": [
                    (spec, take_number) for spec in specs for take_number in range(args.takes)
                ],
                "glues": {},
            }
        )
        print(
            f"  junction j{junction}: {a_item.title} -> {b_item.title} "
            f"({b_tempo:g} BPM, beat {beat_seconds(b_tempo):.5f} s, {baseline_label(record)})",
            flush=True,
        )

    requests = sum(len(prep["work"]) for prep in prepared)
    credits_estimate = requests * CREDITS_PER_REQUEST
    index["credits_estimate"] = credits_estimate
    print(
        f"  est. total credits: {credits_estimate} ({requests} request(s) × "
        f"{CREDITS_PER_REQUEST} credits over {len(prepared)} junction(s); uploads are free)",
        flush=True,
    )

    def fatal(message: str) -> int:
        print(f"error: {message}", flush=True)
        _write_index(run_dir, index)
        (run_dir / "report.html").write_text(report_html(index))
        _write_failure(run_dir, args.run_name, args.songset, message)
        print(f"  partial artifacts in {run_dir}", flush=True)
        return 2

    cache_dir = out_root / "cache" / "treblo-responses"
    for prep in prepared:
        junction = prep["index"]
        entry = prep["entry"]
        boundary = prep["boundary"]
        a_song = prep["a_song"]
        b_song = prep["b_song"]
        for spec, take_number in prep["work"]:
            take_id, cache_take, files, label = _take_index(spec, take_number, args.takes, junction)
            seed = args.seed_base + take_number
            montage = prep["montages"][spec.beats]
            take: dict[str, Any] = {
                "id": take_id,
                "take": cache_take,
                "label": label,
                "beats": spec.beats,
                "seed": seed,
                "status": "pending",
                "target_seconds": round(spec.target_seconds, 4),
                "glue_seconds": None,
                "summary": f"seed {seed}",
                "montage": prep["montage_info"][spec.beats],
                "metrics": {},
                "files": files,
                "reasons": [],
                "error": None,
            }
            entry["takes"].append(take)
            body = inpaint_body(
                prep["montage_info"][spec.beats]["audio_upload_id"],
                montage,
                tags=tags,
                instrumental=args.instrumental,
                lyrics=args.lyrics,
                seed=seed,
                prompt_strength=args.prompt_strength,
                balance_strength=args.balance_strength,
                output_format=args.output_format,
            )
            (boundary / "plans" / f"{cache_take}.json").write_text(
                json.dumps(body, indent=2, ensure_ascii=False) + "\n"
            )
            print(
                f"take {take_id}: section {spec.target_seconds:.3f} s ({spec.beats} beats), "
                f"montage {montage.seconds:.3f} s",
                flush=True,
            )
            try:
                raw, meta, reused = treblo_generate_cached(
                    body, cache_take, cache_dir, args.refresh_responses
                )
                index["credits_spent"] += 0 if reused else CREDITS_PER_REQUEST
                index["model_version"] = index["model_version"] or meta.get("model_version")
                print(f"  {'reusing' if reused else 'bought'} response", flush=True)
                raw_path = boundary / "responses" / f"{cache_take}.{args.output_format}"
                raw_path.parent.mkdir(parents=True, exist_ok=True)
                raw_path.write_bytes(raw)
                decoded = decode_audio(raw_path, boundary / "responses" / f"{cache_take}.wav")
                glue, info = resolve_section(decoded, montage, spec)
                write_flac(run_dir / files["generation"], glue)
                glue_rms = rms_db(glue)
                nonfinite = not bool(np.all(np.isfinite(glue)))
                staged, stage_info = stage_edges(glue, prep["context_a"], prep["context_b"])
                faded = apply_fades(staged)
                write_flac(run_dir / files["glue"], faded)
                prep["glues"][(spec.beats, seed)] = faded
                full = splice(a_song, faded, b_song)
                write_flac(run_dir / files["full"], full)
                seam_excerpt = np.ascontiguousarray(
                    np.concatenate(
                        [
                            a_song[-seconds_to_samples(EXCERPT_SECONDS) :],
                            faded,
                            b_song[: seconds_to_samples(EXCERPT_SECONDS)],
                        ],
                        axis=0,
                    )
                )
                write_flac(run_dir / files["seam"], seam_excerpt)
                fade_n = min(seconds_to_samples(FADE_SECONDS), faded.shape[0] // 2)
                left = joint_metrics(full, a_song.shape[0], fade_after=fade_n)
                right = joint_metrics(full, a_song.shape[0] + faded.shape[0], fade_before=fade_n)
                metrics = {
                    **info,
                    "glue_seconds": round(faded.shape[0] / SR, 4),
                    "glue_rms_db": round(glue_rms, 3),
                    "nonfinite": nonfinite,
                    "joint": {"a": left, "b": right},
                    **stage_info,
                    "task_id": meta.get("task_id"),
                    "model_version": meta.get("model_version"),
                    "credits": 0 if reused else CREDITS_PER_REQUEST,
                }
                take["metrics"] = metrics
                take["summary"] = f"seed {seed} · section {info['geometry']}"
                take["glue_seconds"] = metrics["glue_seconds"]
                status, reasons = evaluate_status(metrics, splice_ok=True)
                if nonfinite:
                    status, reasons = "failed", ["glue contains non-finite samples"]
                take["status"] = status
                take["reasons"] = reasons
                print(
                    f"  {take_id}: {status} section {info['returned_seconds']:.3f} s "
                    f"({info['geometry']}), Δ {info['section_delta_ms']:.1f} ms, "
                    f"glue {metrics['glue_seconds']:.3f} s, "
                    f"joint steps {left['step_db']}/{right['step_db']} dB"
                    + (f" ({'; '.join(reasons)})" if reasons else ""),
                    flush=True,
                )
            except ApiFatalError as exc:
                take["status"] = "failed"
                take["error"] = str(exc)
                index["full_set"]["skipped"] = "aborted by a fatal API response"
                return fatal(str(exc))
            except (TrebloTaskFailed, RuntimeError, ValueError, OSError) as exc:
                take["status"] = "failed"
                take["error"] = str(exc)
                print(f"  {take_id}: failed — {exc}", flush=True)

    index["full_set"]["skipped"] = full_set_skip_reason(index["junctions"], selected, len(items))
    if not index["full_set"]["skipped"]:
        for beats in dict.fromkeys(args.beats):
            for take_number in range(args.takes):
                cache_take = (
                    f"beats-{beats}" if args.takes == 1 else f"beats-{beats}-t{take_number + 1}"
                )
                seed = args.seed_base + take_number
                missing = next(
                    (prep["index"] for prep in prepared if (beats, seed) not in prep["glues"]),
                    None,
                )
                relative = f"full-set/{cache_take}.flac"
                full_entry: dict[str, Any] = {
                    "id": f"full-set-{cache_take}",
                    "kind": "full-set",
                    "label": f"full set — {cache_take}",
                    "beats": beats,
                    "take": take_number + 1,
                    "songs": len(items),
                    "junctions": len(selected),
                    "seconds": None,
                    "status": "unavailable",
                    "files": {},
                    "error": None,
                }
                if missing is None:
                    assembled = assemble_set(
                        songs, [prep["glues"][(beats, seed)] for prep in prepared]
                    )
                    write_flac(run_dir / relative, assembled)
                    full_entry["seconds"] = round(assembled.shape[0] / SR, 4)
                    full_entry["status"] = "ok"
                    full_entry["files"] = {"full": relative}
                    print(
                        f"full set {cache_take}: {full_entry['seconds']:.2f} s "
                        f"({len(items)} songs, {len(selected)} junction(s))",
                        flush=True,
                    )
                else:
                    full_entry["error"] = (
                        f"take {cache_take} not available at junction j{missing} (status failed)"
                    )
                    print(f"full set {cache_take}: unavailable — {full_entry['error']}", flush=True)
                index["full_set"]["takes"].append(full_entry)

    _write_index(run_dir, index)
    (run_dir / "report.html").write_text(report_html(index))
    counts: dict[str, int] = {}
    for prep in prepared:
        for take in prep["entry"]["takes"]:
            counts[take["status"]] = counts.get(take["status"], 0) + 1
    total_takes = sum(counts.values())
    print(
        f"takes: {total_takes} "
        f"({', '.join(f'{status} {count}' for status, count in sorted(counts.items())) or 'none'})",
        flush=True,
    )
    blocked_ids = [entry["id"] for entry in index["junctions"] if entry.get("status") == "blocked"]
    if blocked_ids:
        print(
            f"blocked junctions: {', '.join(blocked_ids)} "
            "(content guard refused the montage; no credits spent)",
            flush=True,
        )
    if index["full_set"]["skipped"]:
        print(f"full set: not built ({index['full_set']['skipped']})", flush=True)
    else:
        built = [entry for entry in index["full_set"]["takes"] if entry["status"] == "ok"]
        print(
            f"full set: {len(built)}/{len(index['full_set']['takes'])} take(s) built",
            flush=True,
        )
    print(
        f"credits: {index['credits_spent']} spent of {index['credits_estimate']} estimated",
        flush=True,
    )
    print(f"artifacts: {run_dir}", flush=True)
    print(
        f"review: cd {run_dir} && python -m http.server 8094 -> http://127.0.0.1:8094/report.html",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
