"""ElevenLabs instrumental Glue POC — generate transition glue between two arranged songs.

For a Songset's first Transition this script builds both *arranged* sides from Postgres + R2
(every Arrangement Step in playback order, exactly what the render plays), uploads an excerpt of
each arranged side to the ElevenLabs music API, composes one inpainting plan per Glue length

    [ A arranged ][ generated Glue ][ B arranged ]

and then keeps only the generated middle chunk. The outgoing Song's Song Arrangement and the
incoming Song's Song Arrangement stay sample-identical; only the Glue is new audio:

    final = A arranged + staged Glue + B arranged

Glue lengths are whole beats of the *incoming* Song's tempo (2, 4, 8, 16 beats by default). The
API floor is a 3 s generation chunk, so 2- and 4-beat takes ask for 3 s and keep the tail (the
arrival into B); the untrimmed generation is kept for review.

Outputs under <out-root>/<run-name>:

    cache/          downloaded mp3s and decoded 44.1 kHz wavs (shared by runs)
    uploads/        canonical 44.1 kHz stereo WAVs that were uploaded (content-addressed)
    uploads.json    full SHA-256 -> {song_id, seconds, format, uploaded_at} (shared by runs)
    context/        a-arranged.flac, b-arranged.flac — the full arranged sides (splice sources)
    plans/          the composition plan request body per take
    responses/      raw API response bytes per take
    glues/          <take>.flac (staged + faded) and <take>-generation.flac (untrimmed)
    full/           full pair per take (and baseline.flac, the production silence gap)
    seams/          15 s A + glue + 15 s B excerpt per take
    index.json      every plan field, seed and measured metric
    report.html     self-contained listening + scoring page

The API re-renders the reference chunks instead of inserting them (measured 2026-11-21: waveform
correlation 0.25-0.47, 50 ms energy-envelope correlation 0.96-0.99), so the Glue is located by plan
geometry and the returned references are verified by envelope, never by wave correlation.

Usage:
    uv run --project lab/poc-scripts --extra elevenlabs_glue \\
        python lab/poc-scripts/gen_elevenlabs_glue.py --run-name run1 --upload-seconds 12
    uv run --project lab/poc-scripts --extra elevenlabs_glue \\
        python lab/poc-scripts/gen_elevenlabs_glue.py --dry-run

Costs ElevenLabs credits: 15 credits per second uploaded plus 15 per second generated. Full
arranged sides are 4,857 credits of upload and the copyright screen rejects them for commercial
recordings (half the fee is still charged), so `--upload-seconds 12` — the vendor guide's recipe —
uploads A's last and B's first 12 s instead: 360 + 341 credits for the same four takes.

Prerequisites: `ELEVENLABS_API_KEY`, `SOW_DATABASE_URL`, `SOW_R2_BUCKET`, `SOW_R2_ENDPOINT_URL`,
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
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

API = "https://api.elevenlabs.io"
ENV_FILE = Path("/opt/sow/.env")
MODEL_ID = "music_v2_5"
DEFAULT_SONGSET = "hbWfCccGxN_JLQGBEqmdu"
SR = 44100
CHANNELS = 2
SAMPLE_WIDTH = 2
CREDITS_PER_SECOND = 15.0
FADE_SECONDS = 0.02
EDGE_SECONDS = 0.2
EXCERPT_SECONDS = 15.0
NEEDLE_SECONDS = 3.0
MAX_LAG = 8820  # 200 ms at 44.1 kHz
WIDEN_LAG = SR  # 1 s, only when the default search misses
WIDEN_THRESHOLD = 0.90
CORRELATION_FLOOR = 0.95
ENVELOPE_FLOOR = 0.90
ENVELOPE_SHIFT_SECONDS = 1.0
MAX_SHORTFALL_SECONDS = 0.1
BEATS = (2, 4, 8, 16)
# Used only by --dry-run, which touches neither Postgres nor R2.
DRY_RUN_TEMPO_BPM = 86.13281
DRY_RUN_A_MS = 146510
DRY_RUN_B_MS = 177310
ENV_KEYS = (
    "ELEVENLABS_API_KEY",
    "SOW_DATABASE_URL",
    "SOW_R2_BUCKET",
    "SOW_R2_ENDPOINT_URL",
    "SOW_R2_ACCESS_KEY_ID",
    "SOW_R2_SECRET_ACCESS_KEY",
)
DEFAULT_GLUE_TEXT = (
    "[Glue]\n{instrumental only — no vocals, no words; seamless musical transition from the "
    "outgoing verse into the incoming chorus: warm piano, ambient pads, soft cymbal swell, "
    "natural build, no hard cut}"
)
DEFAULT_POSITIVE_STYLES = (
    "contemporary worship",
    "warm piano",
    "ambient pads",
    "soft build",
    "seamless transition",
    "great production quality",
)
DEFAULT_NEGATIVE_STYLES = (
    "vocals",
    "singing",
    "vocal",
    "choir",
    "spoken word",
    "lyrics",
    "abrupt ending",
    "silence",
)
SUB_DIRS = ("context", "uploads", "plans", "responses", "glues", "full", "seams")


class ApiFatalError(RuntimeError):
    """An ElevenLabs response that no retry can fix (entitlement, copyright, bad plan, limit)."""


class SeamDecodeError(RuntimeError):
    """The raw response layout could not be inferred from the byte count."""


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
# Seam geometry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TakeSpec:
    """One Glue length: what B's beat grid asks for and what the API can actually generate."""

    beats: int
    target_seconds: float
    generation_seconds: float
    duration_ms: int
    trim: str

    @property
    def target_samples(self) -> int:
        return seconds_to_samples(self.target_seconds)

    @property
    def generation_samples(self) -> int:
        return self.duration_ms * SR // 1000


def take_specs(beats: Sequence[int], tempo_bpm: float) -> list[TakeSpec]:
    """Glue lengths in beats of the incoming tempo, floored at the API's 3 s minimum."""
    if tempo_bpm <= 0:
        raise ValueError(f"tempo must be positive, got {tempo_bpm}")
    spec_list: list[TakeSpec] = []
    for count in beats:
        if count <= 0:
            raise ValueError(f"beat count must be positive, got {count}")
        target_seconds = count * 60.0 / tempo_bpm
        generation_seconds = max(3.0, target_seconds)
        duration_ms = round(generation_seconds * 1000)
        trim = "tail" if generation_seconds > target_seconds + 0.001 else "none"
        spec_list.append(
            TakeSpec(
                beats=int(count),
                target_seconds=target_seconds,
                generation_seconds=duration_ms / 1000.0,
                duration_ms=duration_ms,
                trim=trim,
            )
        )
    return spec_list


def beat_seconds(tempo_bpm: float) -> float:
    return 60.0 / tempo_bpm


@dataclass(frozen=True)
class PlanOptions:
    """Everything that can vary in the generation chunk."""

    glue_text: str = DEFAULT_GLUE_TEXT
    positive_styles: Sequence[str] = DEFAULT_POSITIVE_STYLES
    negative_styles: Sequence[str] = DEFAULT_NEGATIVE_STYLES
    condition: str = "b"
    condition_strength: str = "high"
    condition_seconds: float = 8.0


def glue_chunk(b_id: str, spec: TakeSpec, opts: PlanOptions) -> dict[str, Any]:
    chunk: dict[str, Any] = {
        "text": opts.glue_text,
        "duration_ms": spec.duration_ms,
        "positive_styles": list(opts.positive_styles),
        "negative_styles": list(opts.negative_styles),
        "context_adherence": "high",
    }
    if opts.condition == "b":
        ref_ms = round(opts.condition_seconds * 1000)
        if not 50 <= ref_ms <= 30000:
            raise ValueError(f"conditioning reference must be 50..30000 ms, got {ref_ms}")
        chunk["conditioning_ref"] = {"song_id": b_id, "range": {"start_ms": 0, "end_ms": ref_ms}}
        chunk["condition_strength"] = opts.condition_strength
    elif opts.condition != "none":
        raise ValueError(f"unknown condition mode {opts.condition!r}")
    return chunk


def build_plan(
    a_id: str,
    b_id: str,
    a_start_ms: int,
    a_ref_ms: int,
    b_ref_ms: int,
    spec: TakeSpec,
    opts: PlanOptions,
) -> dict[str, Any]:
    """The three-chunk inpainting plan: A reference, the Glue, B reference."""
    return {
        "chunks": [
            {"song_id": a_id, "range": {"start_ms": int(a_start_ms), "end_ms": int(a_ref_ms)}},
            glue_chunk(b_id, spec, opts),
            {"song_id": b_id, "range": {"start_ms": 0, "end_ms": int(b_ref_ms)}},
        ]
    }


def arrangement_windows(
    a: np.ndarray,
    b: np.ndarray,
    context_seconds: float,
    upload_seconds: float,
) -> tuple[np.ndarray, np.ndarray, int, int, int]:
    """What to upload per side and the plan ranges that reference it.

    `upload_seconds > 0` uploads only A's last / B's first N seconds — the ElevenLabs guide's
    recipe, and the only path the copyright screen accepts for commercial recordings. The plan
    then references the whole uploaded window. `context_seconds > 0` keeps the full sides and
    narrows the referenced ranges instead; 0 uploads and references everything.
    """
    a_ref_ms = round(a.shape[0] / SR * 1000)
    b_ref_ms = round(b.shape[0] / SR * 1000)
    if upload_seconds:
        frames = seconds_to_samples(upload_seconds)
        if frames > min(a.shape[0], b.shape[0]):
            raise ValueError(
                f"--upload-seconds {upload_seconds} exceeds an arranged side "
                f"({a.shape[0] / SR:.2f} s / {b.shape[0] / SR:.2f} s)"
            )
        window_ms = round(upload_seconds * 1000)
        return a[-frames:], b[:frames], 0, window_ms, window_ms
    if context_seconds:
        return (
            a,
            b,
            max(0, a_ref_ms - round(context_seconds * 1000)),
            a_ref_ms,
            round(context_seconds * 1000),
        )
    return a, b, 0, a_ref_ms, b_ref_ms


def plan_total_seconds(spec: TakeSpec, a_start_ms: int, a_ref_ms: int, b_ref_ms: int) -> float:
    return (a_ref_ms - a_start_ms) / 1000.0 + spec.duration_ms / 1000.0 + b_ref_ms / 1000.0


def check_plan_total(total_seconds: float) -> None:
    if not 3.0 <= total_seconds <= 600.0:
        raise ValueError(f"plan total {total_seconds:.2f} s outside the API's 3 s..600 s range")


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SongsetItem:
    position: int
    song_id: str
    title: str
    hash_prefix: str
    gap_beats: int
    crossfade_enabled: bool
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
                   i.crossfade_enabled, i.arrangement_steps
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

        items = [
            SongsetItem(
                position=int(row[0]),
                song_id=str(row[1]),
                title=str(row[2]),
                hash_prefix=str(row[3]),
                gap_beats=int(row[4] or 0),
                crossfade_enabled=bool(row[5]),
                steps=load_arrangement_steps(row[6]),
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
# ElevenLabs API
# ---------------------------------------------------------------------------


def _api_headers(key: str) -> dict[str, str]:
    return {"xi-api-key": key}


def _entitlement_message(status: int, body: str) -> str:
    return (
        f"ElevenLabs returned HTTP {status}: {body[:800]}\n"
        "POST /v1/music/upload is gated to enterprise workspaces with inpainting access; the "
        "guide's concurrency table lists Free as 0. This is a plan/key problem, not a code bug — "
        "fix the key or the workspace plan and rerun. Nothing is retried."
    )


def _fatal_from_response(status: int, body: str, what: str) -> ApiFatalError:
    lowered = body.lower()
    if status in (401, 403) or "missing_permissions" in lowered or "upgrade" in lowered:
        return ApiFatalError(_entitlement_message(status, body))
    if status == 402:
        return ApiFatalError(
            f"{what} returned HTTP 402 (payment required): {body[:800]}\n"
            "The ElevenLabs Music API needs a paid workspace plan with music access — upload and "
            "compose are both gated, so no glue can be generated with this key. Check the plan and "
            "the remaining music credits, then rerun. Nothing is retried."
        )
    if "copyright" in lowered:
        return ApiFatalError(
            f"{what} rejected for copyright (HTTP {status}): {body[:800]}\n"
            "Half of that request's cost is still charged."
        )
    if status == 422:
        return ApiFatalError(f"{what} returned HTTP 422 (bad plan): {body[:1200]}")
    if status == 429:
        return ApiFatalError(
            f"{what} returned HTTP 429 (rate or concurrency limit): {body[:800]}\n"
            "Music concurrency: Free 0, Starter/Creator/Pro 2, Scale/Business 5. Serialize requests."
        )
    return ApiFatalError(f"{what} failed HTTP {status}: {body[:800]}")


def _post_upload(key: str, path: Path, mime: str) -> Any:
    import requests

    with path.open("rb") as handle:
        return requests.post(
            f"{API}/v1/music/upload",
            headers=_api_headers(key),
            files={"file": (path.name, handle, mime)},
            timeout=900,
        )


def upload_song(key: str, wav_path: Path, mp3_path: Path) -> tuple[str, str]:
    """Upload one arranged side; returns (song_id, format). Re-encodes on 413/415 once."""
    resp = _post_upload(key, wav_path, "audio/wav")
    used_format = "wav"
    if resp.status_code in (413, 415):
        print(f"  upload rejected ({resp.status_code}); retrying as mp3 320 kbps", flush=True)
        mp3_path.parent.mkdir(parents=True, exist_ok=True)
        _run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-i",
                str(wav_path),
                "-b:a",
                "320k",
                str(mp3_path),
            ]
        )
        resp = _post_upload(key, mp3_path, "audio/mpeg")
        used_format = "mp3"
    if resp.status_code >= 400:
        raise _fatal_from_response(resp.status_code, resp.text, "upload")
    return str(resp.json()["song_id"]), used_format


def compose(key: str, plan: dict[str, Any], output_format: str, seed: int) -> bytes:
    import requests

    resp = requests.post(
        f"{API}/v1/music",
        params={"output_format": output_format},
        headers={**_api_headers(key), "Content-Type": "application/json"},
        json={"model_id": MODEL_ID, "composition_plan": plan, "seed": seed},
        timeout=1800,
    )
    if resp.status_code >= 400:
        raise _fatal_from_response(resp.status_code, resp.text, "compose")
    return resp.content


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


def ensure_upload(
    key: str,
    samples: np.ndarray,
    out_root: Path,
    refresh: bool,
    label: str,
) -> dict[str, Any]:
    """Upload one arranged side once; reuse the cached song_id afterwards.

    A copyright screening rejection is cached too, keyed by the same audio hash: a rerun must not
    silently pay the half fee again. `--refresh-uploads` clears it deliberately.
    """
    _, sha = pcm16_and_sha(samples)
    cache = load_uploads(out_root)
    if not refresh and sha in cache:
        entry = dict(cache[sha])
        if entry.get("rejected"):
            raise ApiFatalError(
                f"{label} was rejected earlier ({entry['rejected']} at {entry.get('at')}); "
                "pass --refresh-uploads to retry (a retry costs credits)"
            )
        print(
            f"  reusing upload for {label}: song_id {entry['song_id']} "
            f"({entry['seconds']:.2f} s, {entry.get('format', 'wav')})",
            flush=True,
        )
        return {**entry, "sha256": sha}

    uploads_dir = out_root / "uploads"
    wav_path = uploads_dir / f"{sha[:16]}.wav"
    mp3_path = uploads_dir / f"{sha[:16]}.mp3"
    if not wav_path.is_file():
        write_wav_pcm16(wav_path, samples)
    seconds = samples.shape[0] / SR
    print(
        f"  uploading {label} {seconds:.2f} s -> est. {seconds * CREDITS_PER_SECOND:.0f} credits",
        flush=True,
    )
    try:
        song_id, used_format = upload_song(key, wav_path, mp3_path)
    except ApiFatalError as exc:
        if "copyright" in str(exc).lower():
            cache[sha] = {
                "rejected": str(exc),
                "at": datetime.now(UTC).isoformat(timespec="seconds"),
            }
            save_uploads(out_root, cache)
            print("  rejection cached; a rerun will not retry this audio", flush=True)
        raise
    entry = {
        "song_id": song_id,
        "seconds": round(seconds, 3),
        "format": used_format,
        "uploaded_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    cache[sha] = entry
    save_uploads(out_root, cache)
    print(f"  uploaded {label}: song_id {song_id} ({used_format})", flush=True)
    return {**entry, "sha256": sha}


# ---------------------------------------------------------------------------
# Response decoding
# ---------------------------------------------------------------------------


def response_key(plan: dict[str, Any], seed: int, output_format: str, take_id: str) -> str:
    """Cache key for one paid compose: the request *and* the take it belongs to.

    The take id is part of the key because the same body at 2 and 4 beats returned different audio
    on 2026-11-21 even with a fixed seed — the API is not reproducible, so two takes must never share
    a cached response.
    """
    body = json.dumps(
        {
            "model_id": MODEL_ID,
            "plan": plan,
            "seed": seed,
            "output_format": output_format,
            "take": take_id,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def compose_cached(
    key: str,
    plan: dict[str, Any],
    seed: int,
    output_format: str,
    cache_dir: Path,
    take_id: str,
    refresh: bool = False,
) -> tuple[bytes, bool]:
    """Compose once per (plan, seed, format, take); a paid response is never bought twice."""
    path = cache_dir / f"{response_key(plan, seed, output_format, take_id)}.bin"
    if refresh and path.is_file():
        path.unlink()
    if path.is_file():
        return path.read_bytes(), True
    raw = compose(key, plan, output_format, seed)
    cache_dir.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return raw, False


def decode_seam(
    raw: bytes,
    raw_path: Path,
    wav_path: Path,
    plan_seconds: float,
    output_format: str,
) -> np.ndarray:
    """Decode a compose response to 44.1 kHz stereo PCM16 whatever the wire format was."""
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_bytes(raw)
    if raw[:4] == b"RIFF":
        return decode_audio(raw_path, wav_path)
    if output_format.startswith("pcm_"):
        rate = int(output_format.split("_")[1])
        frames = round(plan_seconds * rate)
        for channels in (CHANNELS, 1):
            expected = frames * SAMPLE_WIDTH * channels
            tolerance = rate * SAMPLE_WIDTH * channels // 10
            if abs(len(raw) - expected) <= tolerance:
                _run(
                    [
                        "ffmpeg",
                        "-v",
                        "error",
                        "-y",
                        "-f",
                        "s16le",
                        "-ar",
                        str(rate),
                        "-ac",
                        str(channels),
                        "-i",
                        str(raw_path),
                        "-ar",
                        str(SR),
                        "-ac",
                        str(CHANNELS),
                        "-c:a",
                        "pcm_s16le",
                        str(wav_path),
                    ]
                )
                return read_wav(wav_path)
        raise SeamDecodeError(
            f"cannot infer raw PCM layout: {len(raw)} bytes for {plan_seconds:.3f} s at {rate} Hz; "
            "retrying with mp3_48000_320"
        )
    return decode_audio(raw_path, wav_path)


# ---------------------------------------------------------------------------
# Alignment against the returned references
# ---------------------------------------------------------------------------


def _mono(samples: np.ndarray) -> np.ndarray:
    mono = samples.mean(axis=1) if samples.ndim == 2 else samples
    return np.asarray(mono, dtype=np.float64)


def best_offset(
    needle: np.ndarray,
    haystack: np.ndarray,
    center: int,
    max_lag: int = MAX_LAG,
) -> tuple[float, int]:
    """Exact Pearson correlation of `needle` against haystack[s : s+len(needle)].

    Every offset in `center ± max_lag` is scored at sample resolution — the numerator is one FFT
    cross-correlation over just the searched segment and the per-window mean/energy come from
    cumulative sums, so there is no lag quantisation to miss the true peak. `center` is the
    *expected start* of the needle inside the haystack. Returns (r, s); r is -1 when no candidate
    window fits inside the haystack.
    """
    n = _mono(needle)
    width = int(n.shape[0])
    h = _mono(haystack)
    if width == 0 or h.shape[0] < width:
        return -1.0, int(center)
    n = n - n.mean()
    n_norm = float(np.linalg.norm(n))
    if n_norm == 0.0:
        return -1.0, int(center)

    lo = max(0, int(center) - max_lag)
    hi = min(h.shape[0] - width, int(center) + max_lag)
    if hi < lo:
        return -1.0, int(center)

    segment = h[lo : hi + width]
    offsets = hi - lo + 1
    size = 1 << math.ceil(math.log2(segment.shape[0] + width))
    # irfft(rfft(segment) * conj(rfft(needle)))[k] = sum_i segment[k+i] * needle[i]
    spectrum = np.fft.rfft(segment, size) * np.conj(np.fft.rfft(n, size))
    numerator = np.fft.irfft(spectrum, size)[:offsets]
    cumsum = np.concatenate(([0.0], np.cumsum(segment)))
    cumsum_sq = np.concatenate(([0.0], np.cumsum(np.square(segment))))
    window_sum = cumsum[width:] - cumsum[:-width]
    window_sq = cumsum_sq[width:] - cumsum_sq[:-width]
    variance = np.maximum(window_sq - np.square(window_sum) / width, 0.0)
    denominator = n_norm * np.sqrt(variance)
    with np.errstate(divide="ignore", invalid="ignore"):
        correlations = np.where(denominator > 0.0, numerator / denominator, 0.0)
    best = int(np.argmax(correlations))
    return float(correlations[best]), lo + best


@dataclass(frozen=True)
class Alignment:
    glue_start: int
    glue_end: int
    r_a: float
    r_b: float
    lag_a: int
    lag_b: int
    widened: bool
    mode: str = "correlation"
    ref_envelope_r_a: float | None = None
    ref_envelope_r_b: float | None = None
    measured_offset_a_ms: float = 0.0
    measured_offset_b_ms: float = 0.0


def block_envelope(samples: np.ndarray, size: int, blocks: int) -> np.ndarray:
    """RMS of each `size`-frame block of the mono mix."""
    shaped = _mono(samples)[: blocks * size].reshape(blocks, size)
    return np.sqrt(np.mean(np.square(shaped), axis=1))


def envelope_correlation(
    reference: np.ndarray, candidate: np.ndarray, window_seconds: float = 0.05
) -> float:
    """Correlation of 50 ms energy envelopes — the identity test for a re-rendered reference.

    ElevenLabs re-renders the audio-reference chunks instead of inserting them verbatim (measured
    2026-11-21: waveform r≈0 but envelope r=0.99), so wave correlation alone cannot tell a
    faithful re-render from unrelated audio. The envelope can.
    """
    size = max(1, round(window_seconds * SR))
    blocks = min(reference.shape[0], candidate.shape[0]) // size
    if blocks < 2:
        return 0.0
    left = block_envelope(reference, size, blocks)
    right = block_envelope(candidate, size, blocks)
    if left.std() == 0.0 or right.std() == 0.0:
        return 0.0
    return float(np.corrcoef(left, right)[0, 1])


def envelope_offset(
    reference: np.ndarray,
    seam: np.ndarray,
    expected_start: int,
    max_shift_seconds: float = ENVELOPE_SHIFT_SECONDS,
    window_seconds: float = 0.05,
) -> tuple[int, float]:
    """Where `reference`'s energy envelope actually starts, searched ±max_shift_seconds.

    Plan geometry places the glue, but a returned seam can be a few ms off; this measures that
    offset at 50 ms resolution instead of assuming it away. Returns (offset_samples, r).
    """
    size = max(1, round(window_seconds * SR))
    blocks = reference.shape[0] // size
    if blocks < 2:
        return 0, 0.0
    ref_env = block_envelope(reference, size, blocks)
    if ref_env.std() == 0.0:
        return 0, 0.0
    span = blocks * size
    max_step = max(1, round(max_shift_seconds / window_seconds))
    best_r, best_offset = -2.0, 0
    for step in range(-max_step, max_step + 1):
        start = expected_start + step * size
        if start < 0 or start + span > seam.shape[0]:
            continue
        env = block_envelope(seam[start : start + span], size, blocks)
        if env.std() == 0.0:
            continue
        r = float(np.corrcoef(ref_env, env)[0, 1])
        if r > best_r:
            best_r, best_offset = r, step * size
    return best_offset, round(best_r, 6)


def locate_glue(
    seam: np.ndarray,
    a_tail_needle: np.ndarray,
    b_head_needle: np.ndarray,
    a_ref_samples: int,
    generation_samples: int,
) -> Alignment:
    """Locate the generated glue between the two returned reference regions.

    A's reference occupies seam[0 : a_ref_samples] and B's follows the generated chunk, so the
    expected start of A's last 3 s is `a_ref_samples - len(needle)` and the expected start of B's
    first 3 s is `a_ref_samples + generation_samples`.
    """
    width = int(a_tail_needle.shape[0])
    center_a = a_ref_samples - width
    r_a, start_a = best_offset(a_tail_needle, seam, center_a)
    center_b = a_ref_samples + generation_samples
    r_b, start_b = best_offset(b_head_needle, seam, center_b)
    widened = False
    if min(r_a, r_b) < WIDEN_THRESHOLD:
        widened = True
        r_a_w, start_a_w = best_offset(a_tail_needle, seam, center_a, max_lag=WIDEN_LAG)
        r_b_w, start_b_w = best_offset(b_head_needle, seam, center_b, max_lag=WIDEN_LAG)
        if r_a_w > r_a:
            r_a, start_a = r_a_w, start_a_w
        if r_b_w > r_b:
            r_b, start_b = r_b_w, start_b_w

    if min(r_a, r_b) >= CORRELATION_FLOOR:
        glue_start = int(start_a) + width
        glue_end = int(start_b)
        mode = "correlation"
    else:
        # The references came back re-rendered, so there is no sample-identical window to find:
        # plan geometry places the glue, and the energy envelope then measures how far the returned
        # regions actually sit (a response can be a few ms short of the plan).
        mode = "plan"
        glue_start = int(a_ref_samples)
        glue_end = min(seam.shape[0], int(a_ref_samples) + int(generation_samples))
        offset_a, _ = envelope_offset(a_tail_needle, seam, glue_start - width)
        offset_b, _ = envelope_offset(b_head_needle, seam, glue_end)
        glue_start = max(0, glue_start + offset_a)
        glue_end = min(seam.shape[0], glue_end + offset_b)
    if glue_end <= glue_start:
        raise RuntimeError(f"glue window [{glue_start}, {glue_end}] is empty")
    if glue_start < 0 or glue_end > seam.shape[0]:
        raise RuntimeError(
            f"glue window [{glue_start}, {glue_end}] outside seam of {seam.shape[0]}"
        )
    speech_a = seam[max(0, glue_start - width) : glue_start]
    speech_b = seam[glue_end : min(seam.shape[0], glue_end + width)]
    return Alignment(
        glue_start=glue_start,
        glue_end=glue_end,
        r_a=round(float(r_a), 6),
        r_b=round(float(r_b), 6),
        lag_a=glue_start - a_ref_samples,
        lag_b=glue_end - (a_ref_samples + generation_samples),
        widened=widened,
        mode=mode,
        ref_envelope_r_a=round(envelope_correlation(a_tail_needle, speech_a), 4),
        ref_envelope_r_b=round(envelope_correlation(b_head_needle, speech_b), 4),
        measured_offset_a_ms=round((glue_start - int(a_ref_samples)) / SR * 1000.0, 3),
        measured_offset_b_ms=round(
            (glue_end - min(seam.shape[0], int(a_ref_samples) + int(generation_samples)))
            / SR
            * 1000.0,
            3,
        ),
    )


def take_tail(generated: np.ndarray, target_samples: int) -> np.ndarray:
    """The kept slice of a generation: the last target_samples (arrival into B).

    Responses can come back a few ms short of the plan. Discarding the take would throw away a paid
    generation, so a shortfall up to MAX_SHORTFALL_SECONDS keeps everything returned (the Transition
    loses those milliseconds from its start) and the caller records the shortfall in the metrics.
    """
    if generated.shape[0] < target_samples:
        shortfall = target_samples - generated.shape[0]
        if shortfall > round(MAX_SHORTFALL_SECONDS * SR):
            raise RuntimeError(
                f"generation of {generated.shape[0]} samples is shorter than {target_samples} "
                f"by {shortfall / SR * 1000.0:.1f} ms (limit {MAX_SHORTFALL_SECONDS * 1000.0:.0f} ms)"
            )
        return np.ascontiguousarray(generated)
    return np.ascontiguousarray(generated[-target_samples:])


# ---------------------------------------------------------------------------
# Staging, splicing, metrics
# ---------------------------------------------------------------------------


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
    """ok / check / failed from the measured numbers; never a silent drop."""
    if not splice_ok:
        return "failed", ["splice invariant failed"]
    glue_rms = metrics.get("glue_rms_db")
    if glue_rms is None or not math.isfinite(glue_rms) or glue_rms < -50.0:
        return "failed", [f"glue level {glue_rms} dBFS is below -50 dBFS"]
    reasons: list[str] = []
    if metrics.get("geometry") == "plan":
        for key in ("ref_envelope_r_a", "ref_envelope_r_b"):
            value = metrics.get(key)
            if value is None or value < ENVELOPE_FLOOR:
                reasons.append(
                    f"{key} {value} < {ENVELOPE_FLOOR} (returned reference is not this performance)"
                )
    else:
        for key in ("r_a", "r_b"):
            value = metrics.get(key)
            if value is not None and value < CORRELATION_FLOOR:
                reasons.append(f"{key} {value} < {CORRELATION_FLOOR} (reference not located)")
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
  var SCORE_KEY = 'sow.elevenlabs-glue:' + RUN + ':scores';
  var LAST_KEY = 'sow.elevenlabs-glue:' + RUN + ':lastPlayed';
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
    return (
        f"target {_num(take.get('target_seconds'))} s · requested "
        f"{_num(take.get('generation_seconds'))} s · generated "
        f"{_num(metrics.get('generated_seconds'))} s · trim {take.get('trim')} · "
        f"glue {_num(take.get('glue_seconds'))} s · "
        f"geometry {metrics.get('geometry') or '—'} · "
        f"r_a {_num(metrics.get('r_a'))} / r_b {_num(metrics.get('r_b'))} · "
        f"envelope {_num(metrics.get('ref_envelope_r_a'))} / "
        f"{_num(metrics.get('ref_envelope_r_b'))} · "
        f"offset {_num(metrics.get('measured_offset_a_ms'), 1)} / "
        f"{_num(metrics.get('measured_offset_b_ms'), 1)} ms · "
        f"lag_a {_num(metrics.get('lag_a'), 0)} / lag_b {_num(metrics.get('lag_b'), 0)} samples · "
        f"Δ {_num(metrics.get('duration_delta_ms'), 1)} ms · "
        f"edge gain {_num(gains[0], 2)}/{_num(gains[1], 2)} dB · "
        f"residual {_num(metrics.get('a_residual_db'), 2)}/"
        f"{_num(metrics.get('b_residual_db'), 2)} dB · "
        f"A joint step {_num(left.get('step_db'), 2)} dB click "
        f"{_num(left.get('jump_ratio'), 2)}x · "
        f"B joint step {_num(right.get('step_db'), 2)} dB click "
        f"{_num(right.get('jump_ratio'), 2)}x · peak {_num(metrics.get('peak'))}"
    )


def card_html(take: dict[str, Any], baseline: bool = False) -> str:
    take_id = str(take.get("id"))
    files = take.get("files") or {}
    label = str(take.get("label") or take_id)
    is_baseline = baseline or take_id == "baseline"
    players: list[tuple[str, str]] = []
    if not is_baseline and files.get("glue"):
        players.append(("<span>Glue (spliced)</span>", _audio(str(files["glue"]), "glue")))
    if not is_baseline and files.get("generation"):
        players.append(
            ("<span>Generation (raw)</span>", _audio(str(files["generation"]), "generation"))
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
    detail = f'<p class="error">failed: {html.escape(str(error))}</p>' if error else ""
    if reasons and not error:
        detail += f'<p class="error">check: {html.escape("; ".join(str(r) for r in reasons))}</p>'
    return f"""<article class="card" data-take-id="{html.escape(take_id)}"
  data-label="{html.escape(label)}">
  <h3>{html.escape(label)} <span class="badge" hidden>last played</span></h3>
  <p class="meta">status {html.escape(str(status))} · {html.escape(str(take.get('summary') or ''))}</p>
  <div class="players">{body}</div>
  <p class="metrics">{html.escape(metric_text(take))}</p>
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


def ranking_table(takes: list[dict[str, Any]], failed: bool) -> str:
    rows: list[str] = []
    for order, take in enumerate(takes):
        take_id = html.escape(str(take.get("id")))
        label = html.escape(str(take.get("label") or take.get("id")))
        beats = _num(take.get("beats"), 0)
        glue = _num(take.get("glue_seconds"))
        rows.append(
            f'<tr class="rank-row" data-take-id="{take_id}" data-order="{order}" data-score="">'
            f"<td>{label}</td><td>{beats}</td><td>{glue}</td>"
            f'<td class="rank-score">—</td><td class="rank-vocals">—</td></tr>'
        )
    title = "Failed takes" if failed else "Ranking (score desc, unscored last)"
    if not rows:
        return f'<h2>{title}</h2><p class="meta">none</p>'
    return (
        f"<h2>{title}</h2><table><thead><tr><th>take</th><th>beats</th><th>glue s</th>"
        f"<th>score</th><th>vocals</th></tr></thead>"
        f'<tbody data-sortable="{"0" if failed else "1"}">{"".join(rows)}</tbody></table>'
    )


def report_html(index: dict[str, Any]) -> str:
    run = str(index.get("run"))
    seam = index.get("seam") or {}
    items = index.get("items") or []
    takes = index.get("takes") or []
    baseline = index.get("baseline") or {}
    condition = index.get("condition") or {}
    items_text = " · ".join(
        f"{item.get('title')} (arranged {_num(item.get('arranged_seconds'), 2)} s, "
        f"{_num(item.get('tempo_bpm'), 2)} BPM, {item.get('key')})"
        for item in items[:2]
    )
    ok_takes = [take for take in takes if take.get("status") != "failed"]
    failed_takes = [take for take in takes if take.get("status") == "failed"]
    rankable = ok_takes + ([baseline] if baseline else [])
    cards = "".join(card_html(take) for take in ok_takes)
    failed_cards = "".join(card_html(take) for take in failed_takes)
    baseline_card = card_html(baseline, baseline=True) if baseline else ""
    failed_section = (
        f'<section><h2>Failed</h2><div class="grid">{failed_cards}</div></section>'
        if failed_cards
        else ""
    )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ElevenLabs Glue POC — {html.escape(run)}</title>
<style>{CSS}</style>
</head>
<body data-run="{html.escape(run)}">
  <h1>ElevenLabs instrumental Glue — {html.escape(run)}</h1>
  <p class="sub">songset {html.escape(str(index.get('songset_id')))} ·
  {html.escape(items_text)}</p>
  <p class="sub">beat {_num(seam.get('beat_seconds'), 5)} s at
  {_num(seam.get('tempo_bpm'), 5)} BPM · model {html.escape(str(index.get('model_id')))} ·
  output {html.escape(str(index.get('output_format')))} · input
  {html.escape(str(index.get('input')))} · conditioning {html.escape(str(condition.get('mode')))}
  / {html.escape(str(condition.get('strength')))} / {_num(condition.get('seconds'), 1)} s ·
  est. {_num(index.get('credits_estimate'), 0)} credits</p>
  <div id="last-played">Last played: —</div>
  <div class="toolbar">
    <button id="export-scores" type="button">Export scores</button>
    <button id="import-scores" type="button">Import scores</button>
    <button id="reset-scores" type="button">Reset scores</button>
    <input id="import-file" type="file" accept="application/json" hidden>
  </div>
  <section><h2>Context</h2><div class="grid">
    <article class="card"><h3>A arranged</h3><div class="players">
      <span>A arranged</span>{_audio("context/a-arranged.flac", "context")}</div></article>
    <article class="card"><h3>B arranged</h3><div class="players">
      <span>B arranged</span>{_audio("context/b-arranged.flac", "context")}</div></article>
  </div></section>
  {ranking_table(rankable, failed=False)}
  {ranking_table(failed_takes, failed=True)}
  <section><h2>Takes</h2><div class="grid">{cards}</div></section>
  {failed_section}
  <section><h2>Baseline</h2><div class="grid">{baseline_card}</div></section>
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


def _styles(text: str) -> list[str]:
    return [chunk.strip() for chunk in text.split(",") if chunk.strip()]


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate instrumental Glues between two arranged songs (ElevenLabs)."
    )
    parser.add_argument("--songset", default=DEFAULT_SONGSET, help="songset id")
    parser.add_argument("--beats", type=_ints, default=list(BEATS), help="glue lengths in beats")
    parser.add_argument("--takes", type=int, default=1, help="takes per glue length")
    parser.add_argument("--seed-base", type=int, default=0, help="seed for take 0")
    parser.add_argument("--run-name", default="run1", help="run directory name")
    parser.add_argument(
        "--out-root",
        type=Path,
        default=Path("lab/poc-scripts/output/elevenlabs-glue"),
        help="output root (cache and uploads are shared by runs)",
    )
    parser.add_argument("--glue-text", default=DEFAULT_GLUE_TEXT, help="generation chunk text")
    parser.add_argument("--positive-styles", type=_styles, default=list(DEFAULT_POSITIVE_STYLES))
    parser.add_argument("--negative-styles", type=_styles, default=list(DEFAULT_NEGATIVE_STYLES))
    parser.add_argument("--condition", choices=["b", "none"], default="b")
    parser.add_argument(
        "--condition-strength",
        choices=["low", "medium", "high", "xhigh"],
        default="high",
    )
    parser.add_argument("--condition-seconds", type=float, default=8.0)
    parser.add_argument(
        "--upload-seconds",
        type=float,
        default=0.0,
        help="upload only A's last / B's first N seconds (0 = upload both full arranged sides)",
    )
    parser.add_argument(
        "--context-seconds",
        type=float,
        default=0.0,
        help="reference window into the full uploaded sides (0 = whole side)",
    )
    parser.add_argument("--output-format", default="pcm_44100")
    parser.add_argument("--dry-run", action="store_true", help="print plans; no network, no writes")
    parser.add_argument("--force", action="store_true", help="reuse a non-empty run directory")
    parser.add_argument("--refresh-uploads", action="store_true", help="ignore the upload cache")
    parser.add_argument(
        "--refresh-responses",
        action="store_true",
        help="compose again instead of reusing a cached response (costs credits)",
    )
    return parser.parse_args(argv)


def dry_run(args: argparse.Namespace) -> int:
    """Print the take table and the plan bodies without touching Postgres, R2 or the API."""
    tempo = DRY_RUN_TEMPO_BPM
    specs = take_specs(args.beats, tempo)
    if args.upload_seconds:
        a_start_ms = 0
        a_ref_ms = b_ref_ms = round(args.upload_seconds * 1000)
    else:
        a_ref_ms = DRY_RUN_A_MS
        a_start_ms = (
            max(0, a_ref_ms - round(args.context_seconds * 1000)) if args.context_seconds else 0
        )
        b_ref_ms = round(args.context_seconds * 1000) if args.context_seconds else DRY_RUN_B_MS
    opts = PlanOptions(
        glue_text=args.glue_text,
        positive_styles=tuple(args.positive_styles),
        negative_styles=tuple(args.negative_styles),
        condition=args.condition,
        condition_strength=args.condition_strength,
        condition_seconds=args.condition_seconds,
    )
    totals: list[float] = []
    print(
        f"dry run: songset {args.songset}, tempo stand-in {tempo} BPM "
        f"(beat {beat_seconds(tempo):.5f} s), A ref {a_start_ms}..{a_ref_ms} ms, "
        f"B ref 0..{b_ref_ms} ms",
        flush=True,
    )
    print(
        f"{'beats':>5} {'target s':>9} {'gen s':>8} {'request ms':>11} {'trim':>5} "
        f"{'plan total s':>13}",
        flush=True,
    )
    for spec in specs:
        total = plan_total_seconds(spec, a_start_ms, a_ref_ms, b_ref_ms)
        check_plan_total(total)
        totals.append(total)
        print(
            f"{spec.beats:>5} {spec.target_seconds:>9.4f} {spec.generation_seconds:>8.4f} "
            f"{spec.duration_ms:>11} {spec.trim:>5} {total:>13.2f}",
            flush=True,
        )
    upload_seconds = (a_ref_ms - a_start_ms + b_ref_ms) / 1000.0
    generation_seconds = sum(spec.generation_seconds for spec in specs) * args.takes
    credits = (upload_seconds + generation_seconds) * CREDITS_PER_SECOND
    print(
        f"est. credits: upload {upload_seconds * CREDITS_PER_SECOND:.0f} + generation "
        f"{generation_seconds * CREDITS_PER_SECOND:.0f} = {credits:.0f}",
        flush=True,
    )
    for spec in specs:
        plan = build_plan("<A_ID>", "<B_ID>", a_start_ms, a_ref_ms, b_ref_ms, spec, opts)
        print(f"plan beats-{spec.beats}:", flush=True)
        print(json.dumps(plan, indent=2, ensure_ascii=False), flush=True)
    return 0


def _take_index(spec: TakeSpec, take_number: int, takes: int) -> tuple[str, dict[str, str], str]:
    take_id = f"beats-{spec.beats}" if takes == 1 else f"beats-{spec.beats}-t{take_number + 1}"
    files = {
        "glue": f"glues/{take_id}.flac",
        "generation": f"glues/{take_id}-generation.flac",
        "seam": f"seams/{take_id}.flac",
        "full": f"full/{take_id}.flac",
    }
    label = f"{take_id} — {spec.beats} beat{'' if spec.beats == 1 else 's'}"
    return take_id, files, label


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


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.dry_run:
        return dry_run(args)
    try:
        return _execute_run(args)
    except ApiFatalError as exc:
        print(f"error: {exc}", flush=True)
        run_dir = Path(args.out_root) / args.run_name
        if run_dir.is_dir():
            (run_dir / "failure.json").write_text(
                json.dumps(
                    {
                        "run": args.run_name,
                        "songset_id": args.songset,
                        "failed_at": datetime.now(UTC).isoformat(timespec="seconds"),
                        "error": str(exc),
                    },
                    indent=2,
                )
                + "\n"
            )
            print(f"  failure recorded in {run_dir / 'failure.json'}", flush=True)
        return 2


def _execute_run(args: argparse.Namespace) -> int:
    load_env_file()
    require_env()
    key = os.environ["ELEVENLABS_API_KEY"]
    out_root = Path(args.out_root)
    run_dir = out_root / args.run_name
    _prepare_run_dir(run_dir, args.force)

    started = datetime.now(UTC).isoformat(timespec="seconds")
    opts = PlanOptions(
        glue_text=args.glue_text,
        positive_styles=tuple(args.positive_styles),
        negative_styles=tuple(args.negative_styles),
        condition=args.condition,
        condition_strength=args.condition_strength,
        condition_seconds=args.condition_seconds,
    )

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
    if len(items) > 2:
        print(f"  warning: songset has {len(items)} items; using positions 0 and 1 only")
    item_a, item_b = items[0], items[1]
    rec_a, rec_b = recordings[item_a.hash_prefix], recordings[item_b.hash_prefix]
    for item, recording in ((item_a, rec_a), (item_b, rec_b)):
        print(
            f"  item {item.position}: {item.song_id} [{item.hash_prefix}] "
            f"{recording.duration_seconds:.2f}s {recording.tempo_bpm} BPM "
            f"{recording.musical_key}/{recording.musical_mode} steps {len(item.steps)}",
            flush=True,
        )
    if not rec_b.tempo_bpm:
        raise SystemExit(f"recording {rec_b.hash_prefix} has no tempo_bpm; cannot size the glue")

    source = {
        item.hash_prefix: ensure_source_audio(item.hash_prefix, out_root, connect_r2)
        for item in (item_a, item_b)
    }
    a = arranged_audio(source[item_a.hash_prefix], item_a.steps, "A")
    b = arranged_audio(source[item_b.hash_prefix], item_b.steps, "B")
    write_flac(run_dir / "context/a-arranged.flac", a)
    write_flac(run_dir / "context/b-arranged.flac", b)
    a_seconds = a.shape[0] / SR
    b_seconds = b.shape[0] / SR
    print(
        f"  arranged A {a_seconds:.2f} s, arranged B {b_seconds:.2f} s "
        f"(tempo {rec_b.tempo_bpm} BPM, beat {beat_seconds(rec_b.tempo_bpm):.5f} s)",
        flush=True,
    )
    if args.context_seconds and not 3.0 <= args.context_seconds <= 30.0:
        raise SystemExit("--context-seconds must be within 3..30 (or 0 for full arranged sides)")
    if args.upload_seconds and not 3.0 <= args.upload_seconds <= 30.0:
        raise SystemExit("--upload-seconds must be within 3..30")
    if args.upload_seconds and args.context_seconds:
        print("  note: --upload-seconds overrides --context-seconds", flush=True)

    try:
        a_upload, b_upload, a_start_ms, a_ref_ms, b_ref_ms = arrangement_windows(
            a, b, args.context_seconds, args.upload_seconds
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    specs = take_specs(args.beats, float(rec_b.tempo_bpm))
    for spec in specs:
        check_plan_total(plan_total_seconds(spec, a_start_ms, a_ref_ms, b_ref_ms))

    upload_label_a = f"A arranged last {a_upload.shape[0] / SR:.2f} s"
    upload_label_b = f"B arranged first {b_upload.shape[0] / SR:.2f} s"
    upload_a = ensure_upload(key, a_upload, out_root, args.refresh_uploads, upload_label_a)
    upload_b = ensure_upload(key, b_upload, out_root, args.refresh_uploads, upload_label_b)

    generation_seconds = sum(spec.generation_seconds for spec in specs) * args.takes
    credits_estimate = round(
        (upload_a["seconds"] + upload_b["seconds"] + generation_seconds) * CREDITS_PER_SECOND
    )
    print(f"  est. total credits: {credits_estimate}", flush=True)

    beat = beat_seconds(float(rec_b.tempo_bpm))
    baseline_gap_seconds = 2.0 * beat
    baseline_gap_samples = seconds_to_samples(baseline_gap_seconds)
    silence = np.zeros((baseline_gap_samples, CHANNELS), dtype=np.float32)

    items_index = [
        {
            "position": item.position,
            "title": item.title,
            "hash_prefix": item.hash_prefix,
            "arranged_seconds": round(seconds, 2),
            "tempo_bpm": recording.tempo_bpm,
            "key": f"{recording.musical_key} {recording.musical_mode}".strip(),
            "steps": len(item.steps),
        }
        for item, recording, seconds in (
            (item_a, rec_a, a_seconds),
            (item_b, rec_b, b_seconds),
        )
    ]
    index: dict[str, Any] = {
        "version": 1,
        "run": args.run_name,
        "created_at": started,
        "songset_id": args.songset,
        "model_id": MODEL_ID,
        "output_format": args.output_format,
        "input": (
            f"excerpt:{args.upload_seconds:g}s"
            if args.upload_seconds
            else ("windowed" if args.context_seconds else "full")
        ),
        "items": items_index,
        "seam": {
            "tempo_bpm": float(rec_b.tempo_bpm),
            "beat_seconds": round(beat, 5),
            "baseline_gap_seconds": round(baseline_gap_seconds, 4),
        },
        "uploads": {
            "a": {
                "song_id": upload_a["song_id"],
                "sha256": upload_a["sha256"],
                "seconds": upload_a["seconds"],
            },
            "b": {
                "song_id": upload_b["song_id"],
                "sha256": upload_b["sha256"],
                "seconds": upload_b["seconds"],
            },
        },
        "condition": {
            "mode": opts.condition,
            "strength": opts.condition_strength,
            "seconds": opts.condition_seconds if opts.condition == "b" else None,
        },
        "credits_estimate": credits_estimate,
        "takes": [],
        "baseline": {
            "id": "baseline",
            "label": f"baseline — {round(baseline_gap_seconds / beat)} beats "
            f"({baseline_gap_seconds:.3f} s) of silence",
            "summary": "current render — 2-beat silence gap",
            "status": "ok",
            "beats": round(baseline_gap_seconds / beat),
            "glue_seconds": round(baseline_gap_seconds, 4),
            "files": {"seam": "seams/baseline.flac", "full": "full/baseline.flac"},
        },
    }

    a_needle = a[-seconds_to_samples(NEEDLE_SECONDS) :]
    b_needle = b[: seconds_to_samples(NEEDLE_SECONDS)]
    context_a = a[-round(EDGE_SECONDS * SR) :]
    context_b = b[: round(EDGE_SECONDS * SR)]

    def fatal(message: str) -> int:
        print(f"error: {message}", flush=True)
        _write_index(run_dir, index)
        (run_dir / "report.html").write_text(report_html(index))
        print(f"  partial artifacts in {run_dir}", flush=True)
        return 2

    for spec in specs:
        for take_number in range(args.takes):
            take_id, files, label = _take_index(spec, take_number, args.takes)
            seed = args.seed_base + take_number
            take: dict[str, Any] = {
                "id": take_id,
                "label": label,
                "beats": spec.beats,
                "seed": seed,
                "status": "pending",
                "target_seconds": round(spec.target_seconds, 4),
                "generation_seconds": round(spec.generation_seconds, 4),
                "duration_ms": spec.duration_ms,
                "trim": spec.trim,
                "generated_seconds": None,
                "glue_seconds": None,
                "summary": f"seed {seed}",
                "metrics": {},
                "files": files,
                "reasons": [],
                "error": None,
            }
            index["takes"].append(take)
            plan = build_plan(
                upload_a["song_id"],
                upload_b["song_id"],
                a_start_ms,
                a_ref_ms,
                b_ref_ms,
                spec,
                opts,
            )
            plan_seconds = plan_total_seconds(spec, a_start_ms, a_ref_ms, b_ref_ms)
            body = {"model_id": MODEL_ID, "composition_plan": plan, "seed": seed}
            (run_dir / "plans" / f"{take_id}.json").write_text(
                json.dumps(body, indent=2, ensure_ascii=False) + "\n"
            )
            print(
                f"take {take_id}: generation {spec.duration_ms} ms, plan {plan_seconds:.2f} s",
                flush=True,
            )
            try:
                cache_dir = out_root / "cache" / "responses"
                raw, reused = compose_cached(
                    key,
                    plan,
                    seed,
                    args.output_format,
                    cache_dir,
                    take_id,
                    args.refresh_responses,
                )
                suffix = args.output_format.split("_")[0] if "_" in args.output_format else "bin"
                raw_path = run_dir / "responses" / f"{take_id}.{suffix}"
                wav_path = run_dir / "responses" / f"{take_id}.wav"
                print(f"  {'reusing' if reused else 'composed'} response", flush=True)
                try:
                    seam = decode_seam(raw, raw_path, wav_path, plan_seconds, args.output_format)
                except SeamDecodeError as exc:
                    print(f"  {exc}", flush=True)
                    fallback = "mp3_48000_320"
                    raw, reused = compose_cached(
                        key, plan, seed, fallback, cache_dir, take_id, args.refresh_responses
                    )
                    raw_path = run_dir / "responses" / f"{take_id}.mp3"
                    seam = decode_seam(raw, raw_path, wav_path, plan_seconds, fallback)
                    index["output_format"] = fallback
                print(f"  seam {seam.shape[0] / SR:.3f} s (plan {plan_seconds:.3f} s)", flush=True)
                alignment = locate_glue(
                    seam,
                    a_needle,
                    b_needle,
                    (a_ref_ms - a_start_ms) * SR // 1000,
                    spec.generation_samples,
                )
                generated = np.ascontiguousarray(seam[alignment.glue_start : alignment.glue_end])
                write_flac(run_dir / files["generation"], generated)
                glue = take_tail(generated, spec.target_samples)
                glue_rms = rms_db(glue)
                nonfinite = not bool(np.all(np.isfinite(glue)))
                staged, stage_info = stage_edges(glue, context_a, context_b)
                faded = apply_fades(staged)
                write_flac(run_dir / files["glue"], faded)
                full = splice(a, faded, b)
                write_flac(run_dir / files["full"], full)
                seam_excerpt = np.ascontiguousarray(
                    np.concatenate(
                        [
                            a[-seconds_to_samples(EXCERPT_SECONDS) :],
                            faded,
                            b[: seconds_to_samples(EXCERPT_SECONDS)],
                        ],
                        axis=0,
                    )
                )
                write_flac(run_dir / files["seam"], seam_excerpt)
                fade_n = min(seconds_to_samples(FADE_SECONDS), faded.shape[0] // 2)
                left = joint_metrics(full, a.shape[0], fade_after=fade_n)
                right = joint_metrics(full, a.shape[0] + faded.shape[0], fade_before=fade_n)
                metrics = {
                    "generated_seconds": round(generated.shape[0] / SR, 4),
                    "glue_seconds": round(faded.shape[0] / SR, 4),
                    "glue_rms_db": round(glue_rms, 3),
                    "nonfinite": nonfinite,
                    "r_a": alignment.r_a,
                    "r_b": alignment.r_b,
                    "geometry": alignment.mode,
                    "ref_envelope_r_a": alignment.ref_envelope_r_a,
                    "ref_envelope_r_b": alignment.ref_envelope_r_b,
                    "lag_a": alignment.lag_a,
                    "lag_b": alignment.lag_b,
                    "search_widened": alignment.widened,
                    "duration_delta_ms": round(
                        (generated.shape[0] - spec.generation_samples) / SR * 1000.0, 3
                    ),
                    "generation_short_ms": round(
                        max(0, spec.target_samples - glue.shape[0]) / SR * 1000.0, 3
                    ),
                    "measured_offset_a_ms": alignment.measured_offset_a_ms,
                    "measured_offset_b_ms": alignment.measured_offset_b_ms,
                    "joint": {"a": left, "b": right},
                    **stage_info,
                }
                take["metrics"] = metrics
                take["summary"] = f"seed {seed} · glue located by {alignment.mode}"
                take["generated_seconds"] = metrics["generated_seconds"]
                take["glue_seconds"] = metrics["glue_seconds"]
                status, reasons = evaluate_status(metrics, splice_ok=True)
                if nonfinite:
                    status, reasons = "failed", ["glue contains non-finite samples"]
                take["status"] = status
                take["reasons"] = reasons
                print(
                    f"  {take_id}: {status} glue {metrics['glue_seconds']:.3f} s, "
                    f"r_a {alignment.r_a:.3f} r_b {alignment.r_b:.3f}, "
                    f"envelope {alignment.ref_envelope_r_a:.3f}/{alignment.ref_envelope_r_b:.3f} "
                    f"({alignment.mode}), lags {alignment.lag_a}/{alignment.lag_b}, "
                    f"joint steps {left['step_db']}/{right['step_db']} dB"
                    + (f" ({'; '.join(reasons)})" if reasons else ""),
                    flush=True,
                )
            except ApiFatalError as exc:
                take["status"] = "failed"
                take["error"] = str(exc)
                return fatal(str(exc))
            except (RuntimeError, ValueError, OSError) as exc:
                take["status"] = "failed"
                take["error"] = str(exc)
                print(f"  {take_id}: failed — {exc}", flush=True)

    write_flac(run_dir / "full/baseline.flac", splice(a, silence, b))
    write_flac(
        run_dir / "seams/baseline.flac",
        np.ascontiguousarray(
            np.concatenate(
                [
                    a[-seconds_to_samples(EXCERPT_SECONDS) :],
                    silence,
                    b[: seconds_to_samples(EXCERPT_SECONDS)],
                ],
                axis=0,
            )
        ),
    )
    _write_index(run_dir, index)
    (run_dir / "report.html").write_text(report_html(index))
    statuses = {take["status"] for take in index["takes"]}
    print(f"takes: {len(index['takes'])} ({', '.join(sorted(statuses)) or 'none'})", flush=True)
    print(f"artifacts: {run_dir}", flush=True)
    print(
        f"review: cd {run_dir} && python -m http.server 8092 "
        "-> http://127.0.0.1:8092/report.html",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
