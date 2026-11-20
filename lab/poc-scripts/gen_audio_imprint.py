"""Audio imprinting POC — generate replacement seam music with ACE-Step 1.5 repaint.

For every seam of a Songset this script builds a montage from the real recordings
on both sides plus a hole of digital silence where the seam is, asks the deployed
`sow-acestep-poc` Modal app to repaint only the hole (conditioned on the music
around it), gain-stages the generated music from the outgoing song's level to the
incoming song's level, and splices it into a listenable clip:

    [ 7 s of song A's real audio ][ generated macontage ][ 7 s of song B's real audio ]

Outputs under `--out`:

    cache/      downloaded mp3s and decoded 44.1 kHz wavs
    captions.json   LLM-authored (or fallback) captions; editable input on re-runs
    montages/   what was uploaded (A part + silence + B part)
    clips/      the listenable splice
    bridges/    only the gained hole region
    variants/   per-variant record (idempotency sidecar)
    index.json  every prompt, seed, parameter and measured metric
    report.html self-contained listening page

Usage:
    uv run --project lab/poc-scripts --extra audio_imprint \\
        python lab/poc-scripts/gen_audio_imprint.py \\
        --songset hbWfCccGxN_JLQGBEqmdu --out lab/poc-scripts/output/audio-imprint/run1 --dry-run

Prerequisites: `SOW_DATABASE_URL`, `SOW_R2_BUCKET`, `SOW_R2_ENDPOINT_URL`,
`SOW_R2_ACCESS_KEY_ID`, `SOW_R2_SECRET_ACCESS_KEY`, `MODAL_TOKEN_ID`,
`MODAL_TOKEN_SECRET`, and for caption authoring `SOW_LLM_API_KEY`,
`SOW_LLM_BASE_URL`, `SOW_LLM_MODEL`; `ffmpeg` on PATH.
"""

from __future__ import annotations

import argparse
import html
import json
import math
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

import numpy as np
import soundfile as sf

APP_NAME = "sow-acestep-poc"
MODEL_NAME = "acestep-v15-turbo"

SR = 44100
FADE_SECONDS = 0.005
# 15 s source windows, of which 8 s go into the montage and 7 s stay untouched in the clip.
WINDOW_SECONDS = 15.0
SIDE_SECONDS = 8.0
HEAD_SECONDS = 7.0
# The two windows used to measure each side's level (never touched by repaint).
PROBE_SECONDS = 4.0
GAIN_FRAME_SECONDS = 0.01
PEAK_CEILING = 0.98

ARCS = ("keep-intensity", "settle-then-lift", "quiet-interlude")
TREATMENTS = ("inst", "texture")
LENGTHS = (8, 16)
SIDES = ("left", "right")
TEXTURE_LYRICS = "[Chorus]\nOoh, ooh, ahh, ooh"
INSTRUMENTAL_LYRICS = "[Instrumental]"

REPAINT_ATTEMPTS = 2
REPAINT_BACKOFF_SECONDS = 5.0
LENGTH_TOLERANCE_SECONDS = 0.05  # trim/pad the tail past this

FALLBACK_CAPTIONS: dict[str, str] = {
    "boundary/keep-intensity/inst/left": (
        "worship band, steady 4/4 eighth-note piano, warm bass, acoustic guitar, full kit, "
        "sustained pad, no vocals, continuous drive, no build"
    ),
    "boundary/keep-intensity/inst/right": (
        "same worship band texture, steady eighth-note piano and warm bass, full kit, sustained "
        "pad, no vocals, sustained intensity leading forward"
    ),
    "boundary/keep-intensity/texture/left": (
        "worship band with wordless ooh vocal pad, eighth-note piano, warm bass, soft kit, "
        "sustained, no words"
    ),
    "boundary/keep-intensity/texture/right": (
        "wordless ahh vocal pad over eighth-note piano and warm bass, soft kit, sustained, no words"
    ),
    "boundary/settle-then-lift/inst/left": (
        "worship band settling, piano and pad, drums dropping out, warm bass, no vocals, "
        "gentle release"
    ),
    "boundary/settle-then-lift/inst/right": (
        "worship band rebuilding, piano pulse returning, kick entering, warm bass, no vocals, "
        "lifting"
    ),
    "boundary/settle-then-lift/texture/left": (
        "settling worship texture, wordless ooh pad, piano, drums dropping out, no words"
    ),
    "boundary/settle-then-lift/texture/right": (
        "rebuilding worship texture, wordless ahh pad, piano pulse, light kick, no words"
    ),
    "boundary/quiet-interlude/inst/left": (
        "solo piano and soft pad, spacious, slow arpeggios, no drums, no bass, no vocals"
    ),
    "boundary/quiet-interlude/inst/right": (
        "solo piano and soft pad, spacious, light arpeggios building gently, no drums, no vocals"
    ),
    "boundary/quiet-interlude/texture/left": (
        "spacious piano and pad with faint wordless ooh vocal, no drums, no words"
    ),
    "boundary/quiet-interlude/texture/right": (
        "spacious piano and pad with faint wordless ahh vocal, gently building, no words"
    ),
    "intra/keep-intensity/inst/left": (
        "worship band verse texture, sparse piano, light kit, warm bass, no vocals, steady build"
    ),
    "intra/keep-intensity/inst/right": (
        "worship band chorus texture, fuller piano and kit, warm bass, no vocals, "
        "continuous energy"
    ),
    "intra/settle-then-lift/inst/left": (
        "worship band pulling back before the chorus, piano and pad, drums dropping out, no vocals"
    ),
    "intra/settle-then-lift/inst/right": (
        "worship band lifting into the chorus, piano chords, kick entering, no vocals, rising"
    ),
    "intra/quiet-interlude/inst/left": (
        "sparse piano and pad, verse ending, no drums, no vocals, spacious"
    ),
    "intra/quiet-interlude/inst/right": (
        "sparse piano and pad resolving into a fuller chorus entry, no drums, no vocals"
    ),
}


# ---------------------------------------------------------------------------
# Small audio helpers
# ---------------------------------------------------------------------------


def seconds_to_samples(seconds: float) -> int:
    return int(round(seconds * SR))


def decode_audio(src: Path, dest_wav: Path) -> np.ndarray:
    """Decode any ffmpeg-readable file to 44.1 kHz stereo PCM16, return float32 samples."""
    subprocess.run(
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
            "2",
            "-c:a",
            "pcm_s16le",
            str(dest_wav),
        ],
        check=True,
    )
    return read_wav(dest_wav)


def read_wav(path: Path) -> np.ndarray:
    samples, sample_rate = sf.read(str(path), dtype="float32", always_2d=True)
    if sample_rate != SR:
        raise RuntimeError(f"{path} decoded at {sample_rate} Hz, expected {SR}")
    if samples.shape[1] != 2:
        raise RuntimeError(f"{path} decoded with {samples.shape[1]} channels, expected 2")
    return np.ascontiguousarray(samples, dtype=np.float32)


def write_flac(path: Path, samples: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), samples, SR, format="FLAC", subtype="PCM_16")


def decode_flac_bytes(data: bytes, workdir: Path, stem: str) -> np.ndarray:
    """Decode returned FLAC bytes (any rate, as produced by the model) to 44.1 kHz stereo."""
    flac_path = workdir / f"{stem}.flac"
    flac_path.write_bytes(data)
    wav_path = workdir / f"{stem}.wav"
    return decode_audio(flac_path, wav_path)


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


def fit_length(samples: np.ndarray, expected: int) -> tuple[np.ndarray, bool]:
    """Trim or zero-pad the tail so the array is exactly *expected* samples long."""
    if samples.shape[0] == expected:
        return samples, False
    if samples.shape[0] > expected:
        return np.ascontiguousarray(samples[:expected]), True
    pad = np.zeros((expected - samples.shape[0], samples.shape[1]), dtype=samples.dtype)
    return np.ascontiguousarray(np.concatenate([samples, pad], axis=0)), True


def gain_ramp(num_samples: int, start_db: float, end_db: float) -> np.ndarray:
    """Linear-in-dB gain from *start_db* to *end_db*, computed per 10 ms frame, interpolated."""
    if num_samples <= 0:
        return np.ones(0, dtype=np.float32)
    frame = max(1, seconds_to_samples(GAIN_FRAME_SECONDS))
    starts = np.arange(0, num_samples, frame, dtype=np.float64)
    centers = np.clip(starts + frame / 2.0, 0.0, float(num_samples))
    fraction = centers / float(num_samples)
    frame_db = start_db + (end_db - start_db) * fraction
    frame_gain = np.power(10.0, frame_db / 20.0)
    return np.interp(
        np.arange(num_samples, dtype=np.float64),
        centers,
        frame_gain,
    ).astype(np.float32)


def apply_join_fades(clip: np.ndarray, joins: Iterable[int]) -> None:
    """Equal-power 5 ms fades at each splice, applied in place (length preserved).

    A fade-out ends at the join and a fade-in starts there, so the join is a
    butt splice with de-clicking windows rather than an overlap: the clip keeps
    head + montage + tail sample counts exactly.
    """
    fade = seconds_to_samples(FADE_SECONDS)
    ramp = np.linspace(0.0, 1.0, fade, endpoint=False, dtype=np.float32)[:, None]
    cos_ramp = np.cos(ramp * (np.pi / 2.0))
    sin_ramp = np.sin(ramp * (np.pi / 2.0))
    for join in joins:
        if join - fade < 0 or join + fade > clip.shape[0]:
            continue
        clip[join - fade : join] *= cos_ramp
        clip[join : join + fade] *= sin_ramp


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class SongsetItem:
    position: int
    song_id: str
    title: str
    hash_prefix: str
    gap_beats: int
    crossfade_enabled: bool
    steps: list[dict[str, Any]]

    def first_step_start(self) -> float:
        return float(self.steps[0]["startSeconds"])

    def last_step_end(self) -> float:
        return float(self.steps[-1]["endSeconds"])


@dataclass
class Recording:
    hash_prefix: str
    content_hash: Optional[str]
    duration_seconds: float
    tempo_bpm: Optional[float]
    musical_key: Optional[str]
    musical_mode: Optional[str]
    loudness_db: Optional[float]


@dataclass
class Component:
    content_hash: str
    component_type: str
    occurrence_index: int
    role: Optional[str]
    start_time: float
    end_time: float
    energy_level: Optional[float]
    bpm: Optional[float]
    key: Optional[str]
    theme: Optional[str]
    vocal_posture: Optional[str]

    def label(self) -> str:
        return f"{self.component_type}{self.occurrence_index}"


@dataclass
class Seam:
    """One seam: the audio on both sides plus the facts used to caption/generate it."""

    scope: str
    label: str
    detail: dict[str, Any]
    left_audio: np.ndarray
    right_audio: np.ndarray
    a_window: tuple[float, float]
    b_window: tuple[float, float]
    bpm: int
    key_left: str
    key_right: str

    def head(self) -> np.ndarray:
        return slice_window(self.left_audio, *self.a_window)[: seconds_to_samples(HEAD_SECONDS)]

    def tail(self) -> np.ndarray:
        window = slice_window(self.right_audio, *self.b_window)
        return window[-seconds_to_samples(HEAD_SECONDS) :]

    def a_part(self) -> np.ndarray:
        window = slice_window(self.left_audio, *self.a_window)
        return window[-seconds_to_samples(SIDE_SECONDS) :]

    def b_part(self) -> np.ndarray:
        window = slice_window(self.right_audio, *self.b_window)
        return window[: seconds_to_samples(SIDE_SECONDS)]


@dataclass
class Montage:
    """The uploaded source: A part, N seconds of silence, B part."""

    samples: np.ndarray
    hole_start: int
    hole_end: int

    @property
    def duration_seconds(self) -> float:
        return self.samples.shape[0] / SR

    @property
    def hole_samples(self) -> int:
        return self.hole_end - self.hole_start


@dataclass
class Variant:
    id: str
    scope: str
    arc: str
    length_seconds: int
    treatment: str

    def caption_key(self, side: str) -> str:
        return f"{self.scope}/{self.arc}/{self.treatment}/{side}"


def build_montage(seam: Seam, hole_seconds: int) -> Montage:
    a_part = seam.a_part()
    b_part = seam.b_part()
    hole = np.zeros((seconds_to_samples(hole_seconds), 2), dtype=np.float32)
    samples = np.ascontiguousarray(np.concatenate([a_part, hole, b_part], axis=0))
    hole_start = a_part.shape[0]
    return Montage(samples=samples, hole_start=hole_start, hole_end=hole_start + hole.shape[0])


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------


def load_arrangement_steps(raw: Any) -> list[dict[str, Any]]:
    """Steps in stored (playback) order — the render concatenates them in array order.

    Sorting by `startSeconds` would relocate the seam: item 0's Song Arrangement
    ends with its last *array* element (chorus 40.55-71.85), not with the
    chronologically last section (chorus 217.5-246.7).
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


def fetch_songset(
    conn, songset_id: str
) -> tuple[list[SongsetItem], dict[str, Recording], dict[str, list[Component]]]:
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
        content_hashes: list[str] = []
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
            if recording.content_hash:
                content_hashes.append(recording.content_hash)

        missing = [prefix for prefix in prefixes if prefix not in recordings]
        if missing:
            raise RuntimeError(f"no recordings row for hash prefixes {missing}")

        cur.execute(
            """
            SELECT content_hash, component_type, occurrence_index, role, start_time, end_time,
                   energy_level, bpm, key, theme, vocal_posture
              FROM song_components
             WHERE content_hash = ANY(%s) AND start_time IS NOT NULL AND end_time IS NOT NULL
             ORDER BY content_hash, start_time
            """,
            (content_hashes,),
        )
        components: dict[str, list[Component]] = {}
        for row in cur.fetchall():
            component = Component(
                content_hash=str(row[0]),
                component_type=str(row[1]),
                occurrence_index=int(row[2]),
                role=str(row[3]) if row[3] is not None else None,
                start_time=float(row[4]),
                end_time=float(row[5]),
                energy_level=float(row[6]) if row[6] is not None else None,
                bpm=float(row[7]) if row[7] is not None else None,
                key=str(row[8]) if row[8] is not None else None,
                theme=str(row[9]) if row[9] is not None else None,
                vocal_posture=str(row[10]) if row[10] is not None else None,
            )
            components.setdefault(component.content_hash, []).append(component)

    return items, recordings, components


# ---------------------------------------------------------------------------
# Audio acquisition
# ---------------------------------------------------------------------------


def ensure_source_audio(hash_prefix: str, out_dir: Path, connect) -> np.ndarray:
    """Download the R2 mp3 and decode it once; both files are cached under <out>/cache."""
    cache = out_dir / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    mp3_path = cache / f"{hash_prefix}.mp3"
    wav_path = cache / f"{hash_prefix}.wav"

    if not mp3_path.exists():
        print(f"  downloading {hash_prefix} from R2", flush=True)
        client = connect()
        client.download_audio(hash_prefix, mp3_path)
    if wav_path.exists():
        return read_wav(wav_path)
    print(f"  decoding {mp3_path.name} -> {wav_path.name}", flush=True)
    return decode_audio(mp3_path, wav_path)


# ---------------------------------------------------------------------------
# Seams
# ---------------------------------------------------------------------------


def keyscale(key: Optional[str], mode: Optional[str]) -> str:
    if not key:
        raise RuntimeError("component/recording key is missing; cannot set keyscale")
    normalized_mode = (mode or "major").strip().capitalize()
    return f"{key.strip()} {normalized_mode}"


def component_at(comps: list[Component], seconds: float, prefer_last: bool) -> Optional[Component]:
    """The component containing *seconds*, else the closest one on the requested side."""
    if not comps:
        return None
    for component in comps:
        if component.start_time <= seconds <= component.end_time:
            return component
    if prefer_last:
        candidates = [c for c in comps if c.end_time <= seconds]
        return max(candidates, key=lambda c: c.end_time) if candidates else comps[-1]
    candidates = [c for c in comps if c.start_time >= seconds]
    return min(candidates, key=lambda c: c.start_time) if candidates else comps[0]


def boundary_seam(
    items: list[SongsetItem],
    recordings: dict[str, Recording],
    components: dict[str, list[Component]],
    audio: dict[str, np.ndarray],
) -> Seam:
    a_item, b_item = items[0], items[1]
    if not a_item.steps or not b_item.steps:
        raise RuntimeError("arrangement_steps missing for a boundary item; cannot place the seam")
    a_end = a_item.last_step_end()
    b_start = b_item.first_step_start()
    if b_start <= 0.0:
        raise RuntimeError("incoming item starts at 0 s; the seam needs audio before it")

    a_recording = recordings[a_item.hash_prefix]
    b_recording = recordings[b_item.hash_prefix]
    a_comps = components.get(a_recording.content_hash or "", [])
    b_comps = components.get(b_recording.content_hash or "", [])
    a_exit = component_at(a_comps, a_end, prefer_last=True)
    # The incoming recordings' entry component is where the render's arrangement
    # starts for that Song, not necessarily the row containing 0 s.
    b_entry = next((c for c in b_comps if c.role == "entry"), None) or component_at(
        b_comps, b_start, prefer_last=False
    )

    bpm_source = (a_exit.bpm if a_exit and a_exit.bpm else None) or (
        b_entry.bpm if b_entry and b_entry.bpm else None
    )
    if bpm_source is None:
        bpm_source = a_recording.tempo_bpm or b_recording.tempo_bpm
    if bpm_source is None:
        raise RuntimeError("no boundary or recording tempo available")

    a_window = (max(0.0, a_end - WINDOW_SECONDS), a_end)
    b_window = (b_start, b_start + WINDOW_SECONDS)

    detail = {
        "kind": "boundary",
        "left_song": a_item.song_id,
        "left_title": a_item.title,
        "left_hash": a_item.hash_prefix,
        "right_song": b_item.song_id,
        "right_title": b_item.title,
        "right_hash": b_item.hash_prefix,
        "boundary_left_seconds": a_end,
        "boundary_right_seconds": b_start,
        "gap_beats": a_item.gap_beats,
        "crossfade_enabled": a_item.crossfade_enabled,
        "left_key": keyscale(a_recording.musical_key, a_recording.musical_mode),
        "right_key": keyscale(b_recording.musical_key, b_recording.musical_mode),
        "boundary_bpm": round(float(bpm_source), 2),
        "left_energy_db": a_exit.energy_level if a_exit else None,
        "right_energy_db": b_entry.energy_level if b_entry else None,
        "left_component": a_exit.label() if a_exit else None,
        "right_component": b_entry.label() if b_entry else None,
        "theme": (a_exit.theme if a_exit and a_exit.theme else None)
        or (b_entry.theme if b_entry else None),
        "vocal_posture": (a_exit.vocal_posture if a_exit and a_exit.vocal_posture else None)
        or (b_entry.vocal_posture if b_entry else None),
    }
    return Seam(
        scope="boundary",
        label=f"{a_item.song_id} -> {b_item.song_id}",
        detail=detail,
        left_audio=audio[a_item.hash_prefix],
        right_audio=audio[b_item.hash_prefix],
        a_window=a_window,
        b_window=b_window,
        bpm=int(round(float(bpm_source))),
        key_left=detail["left_key"],
        key_right=detail["right_key"],
    )


def pick_intra_pair(
    items: list[SongsetItem],
    recordings: dict[str, Recording],
    components: dict[str, list[Component]],
) -> Optional[tuple[SongsetItem, Component, Component, float]]:
    """Adjacent-in-start_time component pair with the largest |Δenergy_level|."""
    best: Optional[tuple[SongsetItem, Component, Component, float]] = None
    for item in items:
        recording = recordings[item.hash_prefix]
        comps = sorted(
            (
                c
                for c in components.get(recording.content_hash or "", [])
                if c.energy_level is not None
            ),
            key=lambda c: c.start_time,
        )
        for first, second in zip(comps, comps[1:]):
            if second.start_time <= first.end_time - 1e-6:
                continue  # overlapping/same-section rows, not a real seam
            delta = abs(second.energy_level - first.energy_level)  # type: ignore[operator]
            if best is None or delta > best[3]:
                best = (item, first, second, delta)
    return best


def intra_seam(
    items: list[SongsetItem],
    recordings: dict[str, Recording],
    components: dict[str, list[Component]],
    audio: dict[str, np.ndarray],
) -> Optional[Seam]:
    pair = pick_intra_pair(items, recordings, components)
    if pair is None:
        print(
            "  WARNING: no adjacent component pair with energy levels; skipping intra", flush=True
        )
        return None
    item, first, second, delta = pair
    print(
        f"  intra pair: {first.label()} -> {second.label()}, delta={delta:.2f} dB",
        flush=True,
    )
    key = first.key or second.key or recordings[item.hash_prefix].musical_key
    shared_key = keyscale(key, None)
    # The section being entered carries the tempo the seam must land on; the
    # verse often reports a half/double artifact of the same beat.
    bpm_source = second.bpm or first.bpm or recordings[item.hash_prefix].tempo_bpm
    if bpm_source is None:
        raise RuntimeError("no tempo available for the intra seam")

    a_window = (max(0.0, first.end_time - WINDOW_SECONDS), first.end_time)
    b_window = (second.start_time, second.start_time + WINDOW_SECONDS)
    detail = {
        "kind": "intra",
        "song": item.song_id,
        "hash": item.hash_prefix,
        "left_component": first.label(),
        "right_component": second.label(),
        "boundary_left_seconds": first.end_time,
        "boundary_right_seconds": second.start_time,
        "left_energy_db": first.energy_level,
        "right_energy_db": second.energy_level,
        "energy_delta_db": round(delta, 2),
        "left_key": shared_key,
        "right_key": shared_key,
        "boundary_bpm": round(float(bpm_source), 2),
        "theme": first.theme or second.theme,
        "vocal_posture": first.vocal_posture or second.vocal_posture,
    }
    return Seam(
        scope="intra",
        label=f"{item.song_id} {first.label()} -> {second.label()}",
        detail=detail,
        left_audio=audio[item.hash_prefix],
        right_audio=audio[item.hash_prefix],
        a_window=a_window,
        b_window=b_window,
        bpm=int(round(float(bpm_source))),
        key_left=shared_key,
        key_right=shared_key,
    )


# ---------------------------------------------------------------------------
# Captions
# ---------------------------------------------------------------------------


def caption_keys() -> list[str]:
    keys = [
        f"boundary/{arc}/{treatment}/{side}"
        for arc in ARCS
        for treatment in TREATMENTS
        for side in SIDES
    ]
    keys += [f"intra/{arc}/inst/{side}" for arc in ARCS for side in SIDES]
    return keys


def caption_facts(boundary: Seam, intra: Optional[Seam]) -> str:
    lines = [
        "Seam A (boundary between two songs of one worship set):",
        f"- outgoing song: {boundary.detail['left_song']}, key {boundary.detail['left_key']}, "
        f"exits through its {boundary.detail['left_component']} section, level "
        f"{boundary.detail['left_energy_db']} dB",
        f"- incoming song: {boundary.detail['right_song']}, key {boundary.detail['right_key']}, "
        f"arrives through its {boundary.detail['right_component']} section at 0.7 s, level "
        f"{boundary.detail['right_energy_db']} dB",
        f"- shared tempo at the seam: {boundary.detail['boundary_bpm']} BPM",
        f"- theme {boundary.detail['theme']}, vocal posture {boundary.detail['vocal_posture']}",
        "- the seam is currently 1.393 s of silence; the generated music replaces it",
    ]
    if intra is not None:
        lines += [
            "",
            "Seam B (inside one song, verse into chorus):",
            f"- song: {intra.detail['song']}, key {intra.detail['left_key']}, "
            f"{intra.detail['left_component']} ({intra.detail['left_energy_db']} dB) into "
            f"{intra.detail['right_component']} ({intra.detail['right_energy_db']} dB)",
            f"- tempo {intra.detail['boundary_bpm']} BPM, energy jump "
            f"{intra.detail['energy_delta_db']} dB",
        ]
    return "\n".join(lines)


def caption_instructions() -> str:
    keys = caption_keys()
    return (
        "Write one ACE-Step music-generation caption per key below. Respond with a single JSON "
        'object of the form {"<key>": {"caption": "<text>"}} containing exactly these keys: '
        + json.dumps(keys)
        + "\n\nCaption rules:\n"
        "- English, comma-separated style descriptors, at most 300 characters.\n"
        "- Name instruments, density and dynamics; describe the music, not a storyline.\n"
        "- Do NOT mention key or BPM values: they are passed to the model as parameters.\n"
        "- Captions ending in /left must follow the outgoing recording seamlessly; captions "
        "ending in /right must lead into the incoming recording.\n"
        "- Treatment 'inst' means no voices at all; say 'no vocals'.\n"
        "- Treatment 'texture' means a wordless ooh/ahh vocal pad: no words, only vowel sounds.\n"
        "- Arcs: keep-intensity keeps the energy flat; settle-then-lift drops then rebuilds; "
        "quiet-interlude strips down to piano and pad.\n"
        "- Each /left caption and its /right counterpart are chained: the right pass hears the "
        "left pass's music and must continue it rather than restart."
    )


def normalize_caption_value(value: Any) -> Optional[str]:
    if isinstance(value, str):
        text = value.strip()
    elif isinstance(value, dict) and isinstance(value.get("caption"), str):
        text = str(value["caption"]).strip()
    else:
        return None
    if not text or len(text) > 300:
        return None
    return text


def load_captions(out_dir: Path, facts: str, refresh: bool) -> dict[str, Any]:
    path = out_dir / "captions.json"
    if path.exists() and not refresh:
        data = json.loads(path.read_text())
        captions = {
            key: value
            for key, value in (data.get("captions") or {}).items()
            if normalize_caption_value(value)
        }
        if set(caption_keys()) <= set(captions):
            print(f"  reusing {path} ({len(captions)} captions, source={data.get('source')})")
            return data
        print(f"  {path} is incomplete; re-authoring captions", flush=True)

    return author_captions(out_dir, path, facts)


def author_captions(out_dir: Path, path: Path, facts: str) -> dict[str, Any]:
    model = os.environ.get("SOW_LLM_MODEL", "")
    api_key = os.environ.get("SOW_LLM_API_KEY")
    base_url = os.environ.get("SOW_LLM_BASE_URL")
    if not (model and api_key and base_url):
        print("  SOW_LLM_* not set; writing fallback captions", flush=True)
        return write_captions(path, fallback_payload())

    from openai import OpenAI
    from stream_of_worship.admin.services.llm_models import split_llm_model_effort

    model_id, effort = split_llm_model_effort(model)
    kwargs: dict[str, Any] = {
        "model": model_id,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You write concise ACE-Step music-generation captions for newly generated "
                    "seam music that joins two live worship recordings."
                ),
            },
            {"role": "user", "content": f"{facts}\n\n{caption_instructions()}"},
        ],
        "response_format": {"type": "json_object"},
        # 18 captions plus reasoning effort tokens: 2000 truncates the JSON mid-string.
        "max_tokens": 8000,
    }
    if effort:
        kwargs["extra_body"] = {"reasoning_effort": effort}

    try:
        client = OpenAI(api_key=api_key, base_url=base_url)
        response = client.chat.completions.create(**kwargs)
        raw = response.choices[0].message.content or ""
        data = json.loads(raw)
    except Exception as exc:  # network, HTTP, JSON, provider quirks — never fatal
        print(f"  caption authoring failed ({exc}); writing fallback captions", flush=True)
        return write_captions(path, fallback_payload())

    raw_captions = data.get("captions") if isinstance(data.get("captions"), dict) else data
    captions: dict[str, Any] = {}
    for key in caption_keys():
        text = normalize_caption_value((raw_captions or {}).get(key))
        if text:
            captions[key] = {"caption": text}
    if len(captions) != len(caption_keys()):
        missing = sorted(set(caption_keys()) - set(captions))
        print(f"  caption response incomplete ({missing}); writing fallback captions", flush=True)
        return write_captions(path, fallback_payload())

    payload = {"version": 1, "source": "llm", "model": model, "captions": captions}
    return write_captions(path, payload)


def fallback_payload() -> dict[str, Any]:
    return {
        "version": 1,
        "source": "fallback",
        "model": os.environ.get("SOW_LLM_MODEL", ""),
        "captions": {key: {"caption": text} for key, text in FALLBACK_CAPTIONS.items()},
    }


def write_captions(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    print(f"  wrote {path} ({len(payload['captions'])} captions, source={payload['source']})")
    return payload


# ---------------------------------------------------------------------------
# Modal client
# ---------------------------------------------------------------------------


def modal_client():
    import modal

    return modal.Cls.from_name(APP_NAME, "AceStepRepaint")()


def call_repaint(
    client, audio_bytes: bytes, filename: str, request: dict[str, Any]
) -> dict[str, Any]:
    last_error: Optional[Exception] = None
    for attempt in range(1, REPAINT_ATTEMPTS + 1):
        try:
            return client.repaint.remote(audio_bytes, filename, request)
        except Exception as exc:
            last_error = exc
            print(f"    repaint attempt {attempt}/{REPAINT_ATTEMPTS} failed: {exc}", flush=True)
            if attempt < REPAINT_ATTEMPTS:
                time.sleep(REPAINT_BACKOFF_SECONDS)
    raise RuntimeError(f"repaint failed after {REPAINT_ATTEMPTS} attempts: {last_error}")


def build_request(
    variant: Variant,
    seam: Seam,
    caption: str,
    side: str,
    hole_seconds: float,
    seed: int,
    montage_seconds: float,
) -> dict[str, Any]:
    half = hole_seconds / 2.0
    start = float(seconds_to_samples(SIDE_SECONDS)) / SR
    if side == "left":
        repaint_start, repaint_end = start, start + half
        keyscale_value = seam.key_left
    else:
        repaint_start, repaint_end = start + half, start + hole_seconds
        keyscale_value = seam.key_right
    instrumental = variant.treatment == "inst"
    return {
        "caption": caption,
        "lyrics": INSTRUMENTAL_LYRICS if instrumental else TEXTURE_LYRICS,
        "instrumental": instrumental,
        "bpm": seam.bpm,
        "keyscale": keyscale_value,
        "duration": montage_seconds,
        "seed": seed,
        "repaint_start": round(repaint_start, 3),
        "repaint_end": round(repaint_end, 3),
        "vocal_language": "en",
    }


# ---------------------------------------------------------------------------
# Variant execution
# ---------------------------------------------------------------------------


def run_variant(
    variant: Variant,
    seam: Seam,
    captions: dict[str, Any],
    out_dir: Path,
    client,
    seed_base: int,
    force: bool,
    dry_run: bool,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "id": variant.id,
        "scope": variant.scope,
        "arc": variant.arc,
        "length_seconds": variant.length_seconds,
        "treatment": variant.treatment,
        "status": "pending",
        "clip": f"clips/{variant.id}.flac",
        "bridge": f"bridges/{variant.id}.flac",
        "montage": f"montages/{variant.id}.flac",
        "captions": {
            "left": captions[variant.caption_key("left")]["caption"],
            "right": captions[variant.caption_key("right")]["caption"],
        },
        "requests": [],
        "metrics": {},
        "length_corrected": False,
        "error": None,
    }

    sidecar = out_dir / "variants" / f"{variant.id}.json"
    if sidecar.exists() and not force and (out_dir / "clips" / f"{variant.id}.flac").exists():
        previous = json.loads(sidecar.read_text())
        if previous.get("status") == "ok":
            print(f"  skip {variant.id} (clip exists; --force to regenerate)", flush=True)
            previous["captions"] = record["captions"]
            return previous

    montage = build_montage(seam, variant.length_seconds)
    write_flac(out_dir / "montages" / f"{variant.id}.flac", montage.samples)

    if dry_run:
        record["status"] = "dry_run"
        record["metrics"] = {
            "montage_duration_seconds": round(montage.duration_seconds, 3),
            "hole_seconds": variant.length_seconds,
            "a_rms_db": round(
                rms_db(montage.samples[: montage.hole_start][-seconds_to_samples(PROBE_SECONDS) :]),
                2,
            ),
            "b_rms_db": round(
                rms_db(montage.samples[montage.hole_end :][: seconds_to_samples(PROBE_SECONDS)]), 2
            ),
        }
        return record

    workdir = Path(tempfile.mkdtemp(prefix=f"imprint-{variant.id}-"))
    try:
        left_request = build_request(
            variant,
            seam,
            record["captions"]["left"],
            "left",
            variant.length_seconds,
            seed_base,
            montage.duration_seconds,
        )
        right_request = build_request(
            variant,
            seam,
            record["captions"]["right"],
            "right",
            variant.length_seconds,
            seed_base + 1,
            montage.duration_seconds,
        )
        record["requests"] = [left_request, right_request]
        record["seeds"] = [left_request["seed"], right_request["seed"]]

        expected = montage.samples.shape[0]
        corrected = False
        metas: list[dict[str, Any]] = []

        print(f"  {variant.id}: pass 1 (left, seed {left_request['seed']})", flush=True)
        call_started = time.time()
        first = call_repaint(
            client, bytes_from_array(montage.samples), f"{variant.id}-montage.flac", left_request
        )
        call_seconds = [round(time.time() - call_started, 1)]
        first_samples = decode_flac_bytes(first["audio"], workdir, "pass1")
        first_samples, first_corrected = fit_length(first_samples, expected)
        corrected = corrected or first_corrected
        metas.append(first.get("meta") or {})

        print(f"  {variant.id}: pass 2 (right, seed {right_request['seed']})", flush=True)
        call_started = time.time()
        second = call_repaint(
            client, bytes_from_array(first_samples), f"{variant.id}-pass1.flac", right_request
        )
        call_seconds.append(round(time.time() - call_started, 1))
        second_samples = decode_flac_bytes(second["audio"], workdir, "pass2")
        second_samples, second_corrected = fit_length(second_samples, expected)
        corrected = corrected or second_corrected
        metas.append(second.get("meta") or {})

        record["length_corrected"] = corrected
        record["meta"] = metas
        record["call_seconds"] = call_seconds
        assemble(record, variant, seam, montage, second_samples, out_dir)
        record["status"] = "ok"
    except Exception as exc:
        record["status"] = "failed"
        record["error"] = f"{type(exc).__name__}: {exc}"
        print(f"  {variant.id} FAILED: {record['error']}", flush=True)

    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n")
    return record


def bytes_from_array(samples: np.ndarray) -> bytes:
    """Encode samples as FLAC bytes for upload (kept lossless; the model resamples itself)."""
    with tempfile.TemporaryDirectory(prefix="imprint-encode-") as tmp:
        path = Path(tmp) / "montage.flac"
        write_flac(path, samples)
        return path.read_bytes()


def assemble(
    record: dict[str, Any],
    variant: Variant,
    seam: Seam,
    montage: Montage,
    repainted: np.ndarray,
    out_dir: Path,
) -> None:
    """Gain-stage the hole, splice the clip, write clips/bridges, record metrics."""
    a_level = rms_db(
        montage.samples[montage.hole_start - seconds_to_samples(PROBE_SECONDS) : montage.hole_start]
    )
    b_level = rms_db(
        montage.samples[montage.hole_end : montage.hole_end + seconds_to_samples(PROBE_SECONDS)]
    )

    body = np.array(repainted, dtype=np.float32, copy=True)
    hole = body[montage.hole_start : montage.hole_end]
    target_mean = 0.5 * (a_level + b_level)
    hole_level = rms_db(hole)
    if np.isfinite(hole_level) and np.isfinite(target_mean):
        # One constant gain puts the generated music on the seam's average level
        # (keeping its internal dynamics intact) and the ramp tilts it from A's
        # level to B's across the hole.
        base_gain = 10.0 ** ((target_mean - hole_level) / 20.0)
        tilt = gain_ramp(hole.shape[0], a_level - target_mean, b_level - target_mean)
        hole *= (base_gain * tilt)[:, None]

    head = seam.head()
    tail = seam.tail()

    # Prefer trimming the generated hole over scaling the real recordings: the
    # untouched song material must stay sample-identical to the source. Only a
    # recording that is itself over the ceiling forces a whole-clip scale, which
    # is applied to every part so the written bridge matches the written clip.
    untouched_peak = max(
        peak_level(head),
        peak_level(tail),
        peak_level(body[: montage.hole_start]),
        peak_level(body[montage.hole_end :]),
    )
    peak_limit = "none"
    if untouched_peak > PEAK_CEILING:
        clip_scale = PEAK_CEILING / max(untouched_peak, peak_level(hole))
        peak_limit = "clip"
        head = head * clip_scale
        tail = tail * clip_scale
        body = body * clip_scale
    elif peak_level(hole) > PEAK_CEILING:
        hole *= PEAK_CEILING / peak_level(hole)
        peak_limit = "hole"

    # Measure the level of the audio that is actually written, i.e. after both
    # the seam gain and the peak guard.
    hole_level_after = rms_db(body[montage.hole_start : montage.hole_end])

    clip = np.ascontiguousarray(np.concatenate([head, body, tail], axis=0))
    apply_join_fades(clip, (head.shape[0], head.shape[0] + body.shape[0]))
    peak_after = peak_level(clip)

    write_flac(out_dir / "clips" / f"{variant.id}.flac", clip)
    write_flac(
        out_dir / "bridges" / f"{variant.id}.flac", body[montage.hole_start : montage.hole_end]
    )

    hole_mono = body[montage.hole_start : montage.hole_end].mean(axis=1)
    third = max(1, hole_mono.shape[0] // 3)
    import librosa

    tempo, _ = librosa.beat.beat_track(y=hole_mono, sr=SR)
    tempo_value = float(np.atleast_1d(tempo)[0])
    probe = seconds_to_samples(PROBE_SECONDS)

    record["metrics"] = {
        "bpm": round(tempo_value, 2),
        "a_rms_db": round(a_level, 2),
        "b_rms_db": round(b_level, 2),
        "hole_level_db_before": round(hole_level, 2) if np.isfinite(hole_level) else None,
        "hole_level_db_after": (
            round(hole_level_after, 2) if np.isfinite(hole_level_after) else None
        ),
        "hole_target_mean_db": round(target_mean, 2) if np.isfinite(target_mean) else None,
        "probe_rms_db": {
            "a_tail": round(rms_db(body[montage.hole_start - probe : montage.hole_start]), 2),
            "hole_first_third": round(
                rms_db(body[montage.hole_start : montage.hole_start + third]), 2
            ),
            "hole_last_third": round(rms_db(body[montage.hole_end - third : montage.hole_end]), 2),
            "b_head": round(rms_db(body[montage.hole_end : montage.hole_end + probe]), 2),
        },
        "bridge_duration_seconds": round((montage.hole_end - montage.hole_start) / SR, 3),
        "clip_duration_seconds": round(clip.shape[0] / SR, 3),
        "montage_duration_seconds": round(montage.duration_seconds, 3),
        "peak": round(peak_after, 4),
        "peak_limit": peak_limit,
    }


# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------


def write_index(
    out_dir: Path,
    args,
    seams: dict[str, Seam],
    records: list[dict[str, Any]],
    started: str,
    wall: float,
) -> None:
    payload = {
        "version": 1,
        "songset_id": args.songset,
        "created_at": started,
        "model": MODEL_NAME,
        "app": APP_NAME,
        "seed_base": args.seed,
        "wall_seconds": round(wall, 1),
        "seams": {scope: seam.detail for scope, seam in seams.items()},
        "variants": records,
    }
    (out_dir / "index.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


def report_html(seams: dict[str, Seam], records: list[dict[str, Any]], songset_id: str) -> str:
    boundary = seams.get("boundary")
    intra = seams.get("intra")
    title = "Audio Imprint POC"
    subtitle_bits = [f"songset {songset_id}"]
    if boundary:
        detail = boundary.detail
        left_label = detail.get("left_title") or detail["left_song"]
        right_label = detail.get("right_title") or detail["right_song"]
        subtitle_bits.append(
            f"{html.escape(str(left_label))} ({html.escape(str(detail['left_key']))}) into "
            f"{html.escape(str(right_label))} ({html.escape(str(detail['right_key']))})"
        )
        if detail.get("left_energy_db") is not None and detail.get("right_energy_db") is not None:
            subtitle_bits.append(
                f"seam level {detail['left_energy_db']} dB to {detail['right_energy_db']} dB"
            )
        subtitle_bits.append(f"boundary tempo {detail['boundary_bpm']} BPM")
    if intra:
        subtitle_bits.append(
            f"intra: {html.escape(intra.detail['left_component'])} into "
            f"{html.escape(intra.detail['right_component'])} "
            f"(+{intra.detail['energy_delta_db']} dB)"
        )

    groups: list[tuple[str, list[dict[str, Any]]]] = []
    for length in LENGTHS:
        groups.append(
            (
                f"Boundary ({length} s)",
                [r for r in records if r["scope"] == "boundary" and r["length_seconds"] == length],
            )
        )
    groups.append(("Intra-song (8 s)", [r for r in records if r["scope"] == "intra"]))

    sections: list[str] = []
    for group_title, group_records in groups:
        if not group_records:
            continue
        cards: list[str] = []
        for record in group_records:
            clip = html.escape(record["clip"])
            bridge = html.escape(record["bridge"])
            ok = record["status"] == "ok"
            metrics = record.get("metrics") or {}
            probe = metrics.get("probe_rms_db") or {}
            metric_line = (
                f"bpm {metrics.get('bpm', '—')} · clip {metrics.get('clip_duration_seconds', '—')} s · "
                f"bridge {metrics.get('bridge_duration_seconds', '—')} s · "
                f"A {metrics.get('a_rms_db', '—')} dB → B {metrics.get('b_rms_db', '—')} dB · "
                f"probes: A tail {probe.get('a_tail', '—')}, hole ⅓ {probe.get('hole_first_third', '—')}, "
                f"hole ⅔ {probe.get('hole_last_third', '—')}, B head {probe.get('b_head', '—')} dB"
            )
            if ok:
                players = (
                    f'<div class="players"><span>clip</span>'
                    f'<audio controls preload="metadata" src="{clip}"></audio>'
                    f"<span>bridge</span>"
                    f'<audio controls preload="metadata" src="{bridge}"></audio></div>'
                )
            elif record["status"] == "dry_run":
                players = '<p class="meta">dry run — no audio generated</p>'
            else:
                players = f'<p class="error">failed: {html.escape(str(record.get("error")))}</p>'
            seeds = record.get("seeds") or []
            cards.append(f"""
      <article class="card">
        <h3>{html.escape(record['id'])}</h3>
        <p class="meta">arc {html.escape(record['arc'])} · treatment
          {html.escape(record['treatment'])} · seeds {html.escape(str(seeds))} · length_corrected
          {record.get('length_corrected')}</p>
        {players}
        <p class="metrics">{html.escape(metric_line)}</p>
        <details><summary>captions</summary>
          <p><strong>left</strong> {html.escape(record['captions']['left'])}</p>
          <p><strong>right</strong> {html.escape(record['captions']['right'])}</p>
        </details>
      </article>""")
        sections.append(
            f"<section><h2>{html.escape(group_title)}</h2><div class=\"grid\">{''.join(cards)}</div></section>"
        )

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font: 14px/1.5 system-ui, sans-serif; margin: 0 auto; padding: 24px; max-width: 1100px; }}
  h1 {{ font-size: 20px; margin: 0 0 4px; }}
  .sub {{ color: #666; margin: 0 0 24px; }}
  section {{ margin-bottom: 32px; }}
  h2 {{ font-size: 16px; border-bottom: 1px solid #ddd; padding-bottom: 4px; }}
  .grid {{ display: grid; gap: 16px; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); }}
  .card {{ border: 1px solid #ddd; border-radius: 8px; padding: 12px 14px; }}
  .card h3 {{ font-size: 13px; margin: 0 0 6px; overflow-wrap: anywhere; }}
  .meta, .metrics {{ color: #555; font-size: 12px; margin: 4px 0; }}
  .players {{ display: flex; flex-direction: column; gap: 4px; margin: 6px 0; }}
  .players span {{ font-size: 11px; text-transform: uppercase; letter-spacing: .05em; color: #777; }}
  audio {{ width: 100%; }}
  .error {{ color: #b00; }}
  details {{ margin-top: 6px; font-size: 12px; }}
  summary {{ cursor: pointer; }}
</style>
</head>
<body>
  <h1>{html.escape(title)}</h1>
  <p class="sub">{' · '.join(subtitle_bits)}</p>
  <p class="sub">Generated with {MODEL_NAME} repaint (self-hosted on Modal). Captions were authored
  before generation and passed as parameters; no key detection was attempted, so keys are the
  generation settings, not measurements.</p>
  {''.join(sections)}
</body>
</html>
"""


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate audio-imprint seam clips with ACE-Step repaint"
    )
    parser.add_argument("--songset", required=True, help="songset id to bridge")
    parser.add_argument("--out", required=True, help="output directory for this run")
    parser.add_argument("--arcs", default=",".join(ARCS), help="comma-separated arcs")
    parser.add_argument(
        "--lengths", default=",".join(str(n) for n in LENGTHS), help="comma-separated hole lengths"
    )
    parser.add_argument(
        "--treatments", default=",".join(TREATMENTS), help="comma-separated treatments"
    )
    parser.add_argument("--seed", type=int, default=4242, help="base seed (right pass uses seed+1)")
    parser.add_argument(
        "--force", action="store_true", help="regenerate variants even when clips exist"
    )
    parser.add_argument("--refresh-captions", action="store_true", help="re-author captions.json")
    parser.add_argument("--skip-intra", action="store_true", help="only generate boundary variants")
    parser.add_argument(
        "--dry-run", action="store_true", help="build montages/captions with zero Modal calls"
    )
    return parser.parse_args(argv)


def parse_list(raw: str, allowed: Iterable[str], name: str) -> list[str]:
    values = [value.strip() for value in raw.split(",") if value.strip()]
    unknown = [value for value in values if value not in allowed]
    if unknown:
        raise SystemExit(f"unknown {name}: {unknown} (allowed: {list(allowed)})")
    return values


def plan_variants(args) -> list[Variant]:
    arcs = parse_list(args.arcs, ARCS, "arcs")
    lengths = [
        int(value) for value in parse_list(args.lengths, [str(n) for n in LENGTHS], "lengths")
    ]
    treatments = parse_list(args.treatments, TREATMENTS, "treatments")

    variants: list[Variant] = []
    for arc in arcs:
        for length in lengths:
            for treatment in treatments:
                variants.append(
                    Variant(
                        id=f"boundary__{arc}__{length}s__{treatment}",
                        scope="boundary",
                        arc=arc,
                        length_seconds=length,
                        treatment=treatment,
                    )
                )
    if not args.skip_intra:
        for arc in arcs:
            variants.append(
                Variant(
                    id=f"intra__{arc}__8s__inst",
                    scope="intra",
                    arc=arc,
                    length_seconds=8,
                    treatment="inst",
                )
            )
    return variants


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    out_dir = Path(args.out)
    for sub in ("montages", "clips", "bridges", "variants"):
        (out_dir / sub).mkdir(parents=True, exist_ok=True)

    started_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    started = time.time()

    import psycopg
    from stream_of_worship.admin.services.r2 import R2Client

    r2_bucket = os.environ["SOW_R2_BUCKET"]
    r2_endpoint = os.environ["SOW_R2_ENDPOINT_URL"]
    r2_client: Optional[R2Client] = None

    def connect_r2() -> R2Client:
        nonlocal r2_client
        if r2_client is None:
            r2_client = R2Client(bucket=r2_bucket, endpoint_url=r2_endpoint, region="auto")
        return r2_client

    print(f"songset {args.songset} -> {out_dir}", flush=True)
    with psycopg.connect(os.environ["SOW_DATABASE_URL"]) as conn:
        items, recordings, components = fetch_songset(conn, args.songset)

    for item in items:
        recording = recordings[item.hash_prefix]
        print(
            f"  item {item.position}: {item.song_id} [{item.hash_prefix}] "
            f"{recording.duration_seconds:.2f}s {recording.tempo_bpm} BPM "
            f"{recording.musical_key}/{recording.musical_mode} {recording.loudness_db} dB "
            f"steps {len(item.steps)}",
            flush=True,
        )

    audio = {
        item.hash_prefix: ensure_source_audio(item.hash_prefix, out_dir, connect_r2)
        for item in items
    }

    boundary = boundary_seam(items, recordings, components, audio)
    intra = None if args.skip_intra else intra_seam(items, recordings, components, audio)
    seams: dict[str, Seam] = {"boundary": boundary}
    if intra is not None:
        seams["intra"] = intra

    captions = load_captions(
        out_dir,
        caption_facts(boundary, intra),
        args.refresh_captions,
    )

    variants = plan_variants(args)
    print(f"  {len(variants)} variants", flush=True)

    client = None if args.dry_run else modal_client()
    records: list[dict[str, Any]] = []
    for variant in variants:
        seam = seams[variant.scope]
        records.append(
            run_variant(
                variant,
                seam,
                captions["captions"],
                out_dir,
                client,
                args.seed,
                args.force,
                args.dry_run,
            )
        )

    wall = time.time() - started
    write_index(out_dir, args, seams, records, started_iso, wall)
    (out_dir / "report.html").write_text(report_html(seams, records, args.songset))

    gpu_seconds = sum(
        float(meta.get("elapsed_seconds") or 0.0)
        for record in records
        for meta in (record.get("meta") or [])
    )
    ok = [record for record in records if record["status"] == "ok"]
    failed = [record for record in records if record["status"] == "failed"]
    print(
        f"done: {len(ok)} ok, {len(failed)} failed, {len(records)} variants in {wall:.1f}s "
        f"(gpu {gpu_seconds:.1f}s across {sum(len(r.get('meta') or []) for r in records)} calls)",
        flush=True,
    )
    call_seconds = [seconds for record in records for seconds in (record.get("call_seconds") or [])]
    if call_seconds:
        rest = call_seconds[1:]
        slowest_rest = max(rest) if rest else 0.0
        print(
            f"  repaint wall times: first call {call_seconds[0]:.1f}s (includes cold start), "
            f"other calls max {slowest_rest:.1f}s",
            flush=True,
        )
    if failed:
        for record in failed:
            print(f"  failed {record['id']}: {record['error']}", flush=True)
        if not ok:
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
