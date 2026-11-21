"""Seam Inpaint POC — generate replacement seam music with ACE-Step / Stable Audio 3.

For every seam of a Songset this script builds a montage from the *entire arranged
audio* of both sides — every arrangement step, in render order, not a window
around the seam — plus a hole of digital silence where the seam is, asks a
deployed Modal app to repaint only the hole (conditioned on that arranged audio,
and on a 30 s style reference for ACE-Step), gain-stages the generated music from
the outgoing song's level to the incoming song's level, and splices it back
between *samples of the original recordings*:

    [ 7 s A before its render ][ 146.5 s A arranged ][ hole ][ 177.3 s B arranged ][ 7 s B after ]

Those two numbers are this songset's A and B; both are computed from
`songset_items.arrangement_steps`, and the hole sits at the end of A's arranged
audio (the render's seam) not at a fixed offset.

Two selectable variations live in this one script:

    --mode bridge  one repaint pass over an 8 s or 16 s replacement bridge
    --mode gap     repaint only the production 2-beat gap (1.393 s at B's
                   86.13281 BPM) and apply run3's onset-based trim/grid
                   timing correction so the fill starts immediately and its
                   second hit lands on the beat

Three engines, selected with `--engine`:

    base   acestep-v15-base  64 steps, ADG, shift 1.0   (quality default)
    turbo  acestep-v15-turbo  8 steps, no ADG, shift 3.0 (run2/run3's model)
    sa3    sa3-medium         8 steps, no key/bpm/reference fields

Outputs under `--out`:

    cache/      downloaded mp3s and decoded 44.1 kHz wavs
    captions.json   LLM-authored (bridge) or fixed (gap) captions; editable input
    context/    each seam's arranged A and B audio, exactly as uploaded
    reference/  30 s / 48 kHz style reference per seam and alignment
    montages/   what was uploaded (A part + silence + B part)
    fills/      gap mode only: the raw model fill per take (1.393 s)
    clips/      the listenable splice
    bridges/    only the gained hole region
    variants/   per-take record (idempotency sidecar)
    index.json  every prompt, seed, parameter and measured metric
    report.html self-contained listening page

Usage:
    set -a && . /opt/sow/.env && set +a
    uv run --project lab/poc-scripts --extra audio_inpaint \\
        python lab/poc-scripts/gen_audio_inpaint.py \\
        --songset hbWfCccGxN_JLQGBEqmdu --out lab/poc-scripts/output/audio-inpaint/run4 --dry-run

Prerequisites: `SOW_DATABASE_URL`, `SOW_R2_BUCKET`, `SOW_R2_ENDPOINT_URL`,
`SOW_R2_ACCESS_KEY_ID`, `SOW_R2_SECRET_ACCESS_KEY`, `MODAL_TOKEN_ID`,
`MODAL_TOKEN_SECRET`, and for bridge caption authoring `SOW_LLM_API_KEY`,
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

SR = 44100
FADE_SECONDS = 0.005
# Bridge holes are butt splices into a long generated span, so their edges get a
# real crossfade; the 2-beat gap keeps 5 ms so an immediate drum attack survives.
HOLE_FADE_SECONDS = 0.04
GAP_FADE_SECONDS = 0.005
# 15 s source windows, of which 8 s go into the montage and 7 s stay untouched in the clip.
SIDE_SECONDS = 8.0
CLIP_CONTEXT_SECONDS = 15.0
# The two windows used to measure each side's level (never touched by repaint).
PROBE_SECONDS = 4.0
EDGE_PROBE_SECONDS = 0.2
# A generated region whose first or last stretch sits this far under its own level
# is reported: no 18 dB edge ramp can make such an end sit on the real audio.
DEAD_EDGE_THRESHOLD_DB = -12.0
DEAD_EDGE_WINDOW_SECONDS = 0.05
MIN_ONSET_SPACING_SECONDS = 0.06
# A generated hole that still sits this far from the real audio at a join after
# the edge ramp is not a transition: gap mode refuses it instead of scoring it.
MAX_JOIN_RESIDUAL_DB = 6.0
# 10 ms below a 16th at the slow side's tempo: a fill may breathe before the
# incoming downbeat, but run3's defect was dead air there. Measured on the base
# gap set: `raw` 0-2 ms, `trim` 46 ms, `grid` 68-90 ms (its stretch + zero padding).
MAX_GAP_TRAILING_SILENCE_MS = 50.0
# The head of a generated bridge is covered with A's own audio when the model
# fades it in; see assemble. Cap on how much real audio is borrowed, the margin
# added past the measured dead head, and the crossfade back into generation.
HEAD_PAD_MAX_SECONDS = 3.0
HEAD_PAD_LEAD_SECONDS = 0.15
HEAD_PAD_FADE_SECONDS = 0.25
# Context uploaded each side of the hole. 0 (the default) uploads the entire
# arranged audio of both songs; a positive value is the run5-era A/B cap.
DEFAULT_CONTEXT_SECONDS = 0.0
# `raw` is the model's fill untouched; `trim` drops the lead-in; `grid` puts the
# next detected onset on beat two.
GAP_CORRECTIONS = ("raw", "trim", "grid")
HOLE_OVERLAP_SECONDS = 0.5
EDGE_GAIN_LIMIT_DB = 18.0
GAIN_FRAME_SECONDS = 0.01
PEAK_CEILING = 0.98
DEFAULT_TAKES = 3

# The ACE-Step handler crops 3 x 10 s from the reference; exactly 30.0 s at 48 kHz
# (1,440,000 frames) is the only length whose crop is deterministic.
REFERENCE_SR = 48000
REFERENCE_SEGMENT_SECONDS = 10.0
REFERENCE_TOTAL_SECONDS = 30.0
REFERENCE_SAMPLES = 1_440_000

ENGINES: dict[str, dict[str, Any]] = {
    "base": {
        "app": "sow-acestep-poc",
        "class": "AceStepSeamBase",
        "model": "acestep-v15-base",
        "steps": 64,
        "guidance_scale": 8.0,
        "use_adg": True,
        "shift": 1.0,
    },
    "turbo": {
        "app": "sow-acestep-poc",
        "class": "AceStepRepaint",
        "model": "acestep-v15-turbo",
        "steps": 8,
        "guidance_scale": 7.0,
        "use_adg": False,
        "shift": 3.0,
    },
    "sa3": {
        "app": "sow-sa3-poc",
        "class": os.environ.get("SOW_SA3_CLASS", "Sa3Inpaint"),
        "model": "sa3-medium",
        "steps": 8,
        "guidance_scale": None,
        "use_adg": False,
        "shift": None,
    },
}
ALIGNMENTS = ("incoming", "outgoing")
MODES = ("bridge", "gap")

ARCS = ("keep-intensity", "settle-then-lift", "quiet-interlude")
TREATMENTS = ("inst", "texture")
LENGTHS = (8, 16)
TEXTURE_LYRICS = "[Chorus]\nOoh, ooh, ahh, ooh"
INSTRUMENTAL_LYRICS = "[Instrumental]"

REPAINT_ATTEMPTS = 2
REPAINT_BACKOFF_SECONDS = 5.0
# A generated hole below this peak is silence, not a performance.
SILENCE_PEAK_FLOOR = 0.001
# A fill can pass the peak floor on one click and still be nothing: SA3 medium
# returned -96 dB RMS music with a 0.02 peak for the 2-beat gap at every context
# tried (8 s, 15 s, and the whole 331.8 s arrangement). Real fills measure -9 to
# -16 dB RMS, so a program-level floor separates them without guessing.
SILENCE_RMS_FLOOR_DB = -50.0

# Gap timing corrections (copied from gen_seam_fill_run3.py, which cannot be
# imported: it imports this module).
# Below this a sample counts as digital silence (~-80 dBFS, under PCM16 dither).
SILENCE_FLOOR = 1e-4
# Window for the measured beat-two energy peak: the target beat ±50 ms.
BEAT_TWO_WINDOW_MS = 50.0
# Grid correction is refused outside this stretch range; anything further is a
# different performance, not a timing fix.
STRETCH_RATIO_MIN = 0.5
STRETCH_RATIO_MAX = 2.0

FALLBACK_CAPTIONS: dict[str, str] = {
    "boundary/keep-intensity/inst": (
        "worship band bridge, steady eighth-note piano and warm bass, acoustic guitar, full kit, "
        "sustained pad, no vocals, constant energy, continuous groove leading into the next chorus"
    ),
    "boundary/keep-intensity/texture": (
        "worship band bridge with a wordless ooh vocal pad, eighth-note piano, warm bass, soft kit, "
        "sustained, no words, constant energy leading into the next chorus"
    ),
    "boundary/settle-then-lift/inst": (
        "worship band bridge settling then rebuilding, piano and pad drop out, warm bass, kick and "
        "snare re-enter, no vocals, building into the next chorus"
    ),
    "boundary/settle-then-lift/texture": (
        "settling then rebuilding worship texture, wordless ooh pad, piano pulse, drums drop out "
        "and re-enter, no words, building into the next chorus"
    ),
    "boundary/quiet-interlude/inst": (
        "spacious solo piano and soft pad, slow arpeggios, no drums, no bass, no vocals, quiet "
        "interlude opening into the next chorus"
    ),
    "boundary/quiet-interlude/texture": (
        "spacious piano and pad with faint wordless ooh vocal, no drums, no words, quiet "
        "interlude opening into the next chorus"
    ),
    "intra/keep-intensity/inst": (
        "worship band verse-to-chorus bridge, sparse piano and light kit, warm bass, no vocals, "
        "steady lift into the chorus"
    ),
    "intra/settle-then-lift/inst": (
        "worship band pulling back then lifting, piano and pad, drums drop out then kick enters, "
        "no vocals, rising into the chorus"
    ),
    "intra/quiet-interlude/inst": (
        "sparse piano and pad, spacious, no drums, no vocals, quiet resolve into the chorus entry"
    ),
}

# Gap mode's fixed drum prompts, selected by engine family. The SA3 strings are
# run2's `drum-a` and run3's `immediate-pocket` verbatim; the ACE-Step strings are
# the drum-only captions for the same two styles.
GAP_PROMPTS: dict[str, dict[str, str]] = {
    "acestep": {
        "drum-fill": (
            "solo live acoustic drum kit fill, floor toms and snare rolling into a crash cymbal, "
            "two beats, no melodic instruments, no piano, no guitar, no vocals, seamless lead "
            "into the next section"
        ),
        "drum-immediate": (
            "solo live acoustic drum kit, immediate punchy snare hit at the start, two-beat "
            "eighth-note floor-tom and snare fill, no silence before the first drum, no cymbal "
            "wash, no other instruments, no vocals"
        ),
    },
    "sa3": {
        "drum-fill": (
            "TrackType: Music, VocalType: Instrumental, Genre: Worship, a short live acoustic drum "
            "kit fill with floor toms and snare rolling into a crash cymbal on the downbeat, no "
            "melodic instruments, no vocals, 86 BPM"
        ),
        "drum-immediate": (
            "TrackType: Instrument, solo live acoustic drum kit, immediate punchy snare hit at the "
            "start, natural two-beat eighth-note floor-tom and snare fill at 86 BPM, recorded live "
            "in a church room, dry close kit, no silence before the first drum, no lead-in, no "
            "cymbal wash, no other instruments, no vocals"
        ),
    },
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


def write_flac_at(path: Path, samples: np.ndarray, sample_rate: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), samples, sample_rate, format="FLAC", subtype="PCM_16")


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


def post_seam_audio(samples: np.ndarray, start: float, seconds: float) -> np.ndarray:
    """Real audio just past a seam. Empty at the end of the file, never zero-filled."""
    first = max(0, seconds_to_samples(start))
    last = min(samples.shape[0], first + seconds_to_samples(seconds))
    if last <= first:
        return np.zeros((0, 2), dtype=np.float32)
    return np.ascontiguousarray(samples[first:last], dtype=np.float32)


def rms_db(samples: np.ndarray) -> float:
    if samples.size == 0:
        raise ValueError("rms_db of an empty slice")
    mean_square = float(np.mean(np.square(samples, dtype=np.float64)))
    if mean_square <= 0.0:
        return float("-inf")
    return 20.0 * math.log10(math.sqrt(mean_square))


def peak_level(samples: np.ndarray) -> float:
    return float(np.max(np.abs(samples))) if samples.size else 0.0


def dead_edge_seconds(
    samples: np.ndarray, from_start: bool, threshold_db: float = DEAD_EDGE_THRESHOLD_DB
) -> float:
    """Seconds at one end of a generated region that sit `threshold_db` under its level.

    Reported rather than corrected: a model that fades its mask region in or out
    produces a join the gain ramp cannot fix, and the page has to show it.
    """
    window = seconds_to_samples(DEAD_EDGE_WINDOW_SECONDS)
    if samples.shape[0] < 2 * window:
        return 0.0
    reference = rms_db(samples)
    starts = list(range(0, samples.shape[0] - window + 1, window))
    if not from_start:
        starts.reverse()
    dead = 0
    for start in starts:
        if rms_db(samples[start : start + window]) > reference + threshold_db:
            break
        dead += 1
    return round(dead * window / SR, 2)


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


def apply_join_fades(
    clip: np.ndarray, joins: Iterable[int], fade_seconds: float = FADE_SECONDS
) -> None:
    """Equal-power fades at each splice, applied in place (length preserved).

    A fade-out ends at the join and a fade-in starts there, so the join is a
    butt splice with de-clicking windows rather than an overlap: the clip keeps
    head + montage + tail sample counts exactly. `gen_seam_fill*.py` rely on the
    5 ms default; this script asks for a longer fade at the hole edges only.
    """
    fade = seconds_to_samples(fade_seconds)
    ramp = np.linspace(0.0, 1.0, fade, endpoint=False, dtype=np.float32)[:, None]
    cos_ramp = np.cos(ramp * (np.pi / 2.0))
    sin_ramp = np.sin(ramp * (np.pi / 2.0))
    for join in joins:
        if join - fade < 0 or join + fade > clip.shape[0]:
            continue
        clip[join - fade : join] *= cos_ramp
        clip[join : join + fade] *= sin_ramp


def finite(value: Optional[float], digits: int = 3) -> Optional[float]:
    """JSON-safe number: non-finite means 'not measurable' and is stored as null."""
    if value is None or not math.isfinite(float(value)):
        return None
    return round(float(value), digits)


def mono(samples: np.ndarray) -> np.ndarray:
    return samples.mean(axis=1)


def pearson(a: np.ndarray, b: np.ndarray) -> Optional[float]:
    if a.size != b.size or a.size < 2:
        return None
    x = np.asarray(a, dtype=np.float64) - float(np.mean(a))
    y = np.asarray(b, dtype=np.float64) - float(np.mean(b))
    denominator = math.sqrt(float(np.sum(x * x)) * float(np.sum(y * y)))
    if denominator <= 0.0:
        return None
    return finite(float(np.sum(x * y)) / denominator)


def stereo_corr(samples: np.ndarray) -> Optional[float]:
    return pearson(samples[:, 0].astype(np.float64), samples[:, 1].astype(np.float64))


def centroid_hz(samples: np.ndarray) -> Optional[float]:
    if samples.shape[0] < 2048:
        return None
    import librosa

    values = librosa.feature.spectral_centroid(y=mono(samples), sr=SR, n_fft=1024)
    return finite(float(np.mean(values)), 1)


def chroma_mean(samples: np.ndarray) -> Optional[np.ndarray]:
    if samples.shape[0] < 4096:
        return None
    import librosa

    values = librosa.feature.chroma_cqt(y=mono(samples), sr=SR)
    return values.mean(axis=1)


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
    """One seam: the audio on both sides plus the facts used to caption/generate it.

    Tempo and key come from the *recordings* rows, not from `song_components`:
    the component rows carry analysis artifacts (A's verse reports 84.72 BPM and
    twice the incoming tempo while the recording is 68 BPM).
    """

    scope: str
    label: str
    detail: dict[str, Any]
    left_audio: np.ndarray
    right_audio: np.ndarray
    a_arranged: np.ndarray
    b_arranged: np.ndarray
    bpm_left: int
    bpm_right: int
    key_left: str
    key_right: str
    a_post: np.ndarray

    def a_part(self) -> np.ndarray:
        return self.a_arranged

    def b_part(self) -> np.ndarray:
        return self.b_arranged

    def a_clip_context(self) -> np.ndarray:
        """The last CLIP_CONTEXT_SECONDS of the arranged audio: the clip's left side."""
        return self.a_arranged[-seconds_to_samples(CLIP_CONTEXT_SECONDS) :]

    def b_clip_context(self) -> np.ndarray:
        """The first CLIP_CONTEXT_SECONDS of the arranged audio: the clip's right side."""
        return self.b_arranged[: seconds_to_samples(CLIP_CONTEXT_SECONDS)]

    def a_head_pad(self, samples: int) -> np.ndarray:
        """A's real audio for up to *samples* past the seam; shorter at the file's end."""
        return self.a_post[:samples]


@dataclass
class Montage:
    """The uploaded source: A part, N seconds of silence, B part."""

    samples: np.ndarray
    hole_start: int
    hole_end: int
    a_context_seconds: float
    b_context_seconds: float

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

    def caption_key(self) -> str:
        return f"{self.scope}/{self.arc}/{self.treatment}"


@dataclass(frozen=True)
class GapTake:
    id: str  # gap__<style>__incoming__<engine>__s<seed>
    style: str
    seed: int
    take: int
    treatment: str = "inst"

    @property
    def caption_key(self) -> str:
        return f"gap/{self.style}"


def montage_key(montage: Montage) -> str:
    """File-name tag identifying the uploaded window: `c15s`, `c146.51s`."""
    return f"c{montage.a_context_seconds:g}s"


def build_montage(
    seam: Seam, hole_seconds: float, context_seconds: Optional[float] = None
) -> Montage:
    """The uploaded source: A part, N seconds of silence, B part.

    `context_seconds` caps each side; `None`/0 (the default) keeps the entire arranged
    audio, which is what both songs are conditioned on: 146.51 s + 177.31 s for this
    songset. The cap survives only as an A/B knob. Two long-input behaviours are
    expected and measured, never hidden: SA3 fades its hole in from up to ~1.5 s of
    digital silence (assemble's head pad covers what it can and
    `hole_head_pad_seconds`/`hole_dead_edge_seconds` report the rest), and a hole below
    `SILENCE_RMS_FLOOR_DB` is still refused.
    """
    a_part = seam.a_part()
    b_part = seam.b_part()
    if context_seconds:
        cap = seconds_to_samples(context_seconds)
        a_part = a_part[-cap:]
        b_part = b_part[:cap]
    hole = np.zeros((seconds_to_samples(hole_seconds), 2), dtype=np.float32)
    samples = np.ascontiguousarray(np.concatenate([a_part, hole, b_part], axis=0))
    hole_start = a_part.shape[0]
    return Montage(
        samples=samples,
        hole_start=hole_start,
        hole_end=hole_start + hole.shape[0],
        a_context_seconds=round(a_part.shape[0] / SR, 3),
        b_context_seconds=round(b_part.shape[0] / SR, 3),
    )


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


def arranged_audio(audio: np.ndarray, steps: list[dict[str, Any]], label: str) -> np.ndarray:
    """The audio the render actually plays for one Song: every step, in array order.

    Steps are sections of the recording, so the arranged audio is their
    concatenation — for this songset A's arrangement is 156.5 s assembled from
    five non-adjacent sections and B's is 177.3 s from six. Slicing a plain time
    window around the seam (what the first draft did) made the model hear 8 s of
    a section the render may not even use at the seam.
    """
    if not steps:
        raise RuntimeError(f"{label}: no arrangement steps; cannot build arranged audio")
    segments = [
        slice_window(audio, float(step["startSeconds"]), float(step["endSeconds"]))
        for step in steps
    ]
    return np.ascontiguousarray(np.concatenate(segments, axis=0))


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


def recording_tempo(recording: Recording, label: str) -> float:
    if not recording.tempo_bpm:
        raise RuntimeError(f"no tempo for {label} recording {recording.hash_prefix}; cannot generate")
    return float(recording.tempo_bpm)


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
    a_tempo = recording_tempo(a_recording, "outgoing")
    b_tempo = recording_tempo(b_recording, "incoming")
    a_key = keyscale(a_recording.musical_key, a_recording.musical_mode)
    b_key = keyscale(b_recording.musical_key, b_recording.musical_mode)
    a_comps = components.get(a_recording.content_hash or "", [])
    b_comps = components.get(b_recording.content_hash or "", [])
    a_exit = component_at(a_comps, a_end, prefer_last=True)
    # The incoming recordings' entry component is where the render's arrangement
    # starts for that Song, not necessarily the row containing 0 s.
    b_entry = next((c for c in b_comps if c.role == "entry"), None) or component_at(
        b_comps, b_start, prefer_last=False
    )

    a_arranged = arranged_audio(audio[a_item.hash_prefix], a_item.steps, "outgoing")
    b_arranged = arranged_audio(audio[b_item.hash_prefix], b_item.steps, "incoming")

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
        "left_key": a_key,
        "right_key": b_key,
        "bpm_left": int(round(a_tempo)),
        "bpm_right": int(round(b_tempo)),
        "recording_tempo_left": a_tempo,
        "recording_tempo_right": b_tempo,
        "production_gap_seconds": round(a_item.gap_beats * 60.0 / b_tempo, 3),
        "left_arranged_seconds": round(a_arranged.shape[0] / SR, 3),
        "right_arranged_seconds": round(b_arranged.shape[0] / SR, 3),
        "left_arranged_steps": [
            [float(s["startSeconds"]), float(s["endSeconds"])] for s in a_item.steps
        ],
        "right_arranged_steps": [
            [float(s["startSeconds"]), float(s["endSeconds"])] for s in b_item.steps
        ],
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
        a_arranged=a_arranged,
        b_arranged=b_arranged,
        bpm_left=detail["bpm_left"],
        bpm_right=detail["bpm_right"],
        key_left=a_key,
        key_right=b_key,
        a_post=post_seam_audio(audio[a_item.hash_prefix], a_end, HEAD_PAD_MAX_SECONDS),
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
    recording = recordings[item.hash_prefix]
    tempo = recording_tempo(recording, "intra")
    shared_key = keyscale(recording.musical_key, recording.musical_mode)
    audio_samples = audio[item.hash_prefix]

    # An intra seam cuts between two components of one recording, so "arranged
    # audio" is just the SIDE_SECONDS either side of that cut.
    a_arranged = slice_window(audio_samples, first.end_time - SIDE_SECONDS, first.end_time)
    b_arranged = slice_window(
        audio_samples, second.start_time, second.start_time + SIDE_SECONDS
    )
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
        "bpm_left": int(round(tempo)),
        "bpm_right": int(round(tempo)),
        "recording_tempo_left": tempo,
        "recording_tempo_right": tempo,
        "production_gap_seconds": None,
        "left_arranged_seconds": round(a_arranged.shape[0] / SR, 3),
        "right_arranged_seconds": round(b_arranged.shape[0] / SR, 3),
        "theme": first.theme or second.theme,
        "vocal_posture": first.vocal_posture or second.vocal_posture,
    }
    return Seam(
        scope="intra",
        label=f"{item.song_id} {first.label()} -> {second.label()}",
        detail=detail,
        left_audio=audio_samples,
        right_audio=audio_samples,
        a_arranged=a_arranged,
        b_arranged=b_arranged,
        bpm_left=detail["bpm_left"],
        bpm_right=detail["bpm_right"],
        key_left=shared_key,
        key_right=shared_key,
        a_post=post_seam_audio(audio_samples, first.end_time, HEAD_PAD_MAX_SECONDS),
    )


# ---------------------------------------------------------------------------
# Reference audio
# ---------------------------------------------------------------------------


def _as_reference_segment(arranged: np.ndarray, start_seconds: float) -> np.ndarray:
    """One REFERENCE_SEGMENT_SECONDS slice of arranged audio, clamped to the array."""
    expected = seconds_to_samples(REFERENCE_SEGMENT_SECONDS)
    first = max(0, min(seconds_to_samples(start_seconds), max(0, arranged.shape[0] - expected)))
    return arranged[first : first + expected]


def _reference_sources(seam: Seam) -> tuple[np.ndarray, np.ndarray]:
    """Outgoing and incoming audio the 30 s reference is cut from.

    The arrangement when it is long enough (a boundary seam: 156.5 s and 177.3 s
    here), else real recording audio around the seam — an intra seam's arranged
    audio is only `SIDE_SECONDS` long and a 30 s reference built from it would be
    mostly padding.
    """
    segment = seconds_to_samples(REFERENCE_SEGMENT_SECONDS)
    sources: list[np.ndarray] = []
    for side, recording, boundary_key in (
        ("a", seam.left_audio, "boundary_left_seconds"),
        ("b", seam.right_audio, "boundary_right_seconds"),
    ):
        arranged = seam.a_arranged if side == "a" else seam.b_arranged
        if arranged.shape[0] >= segment:
            sources.append(arranged)
            continue
        boundary = float(seam.detail.get(boundary_key) or 0.0)
        if side == "a":
            start = max(0.0, boundary - 2 * REFERENCE_SEGMENT_SECONDS)
            sources.append(slice_window(recording, start, boundary) if boundary > start else arranged)
        else:
            end = min(recording.shape[0] / SR, boundary + 2 * REFERENCE_SEGMENT_SECONDS)
            sources.append(slice_window(recording, boundary, end) if end > boundary else arranged)
    return sources[0], sources[1]


def build_reference(seam: Seam, align: str) -> np.ndarray:
    """3 x 10 s at 48 kHz, exactly 1,440,000 frames (deterministic server-side crop).

    The whole arranged audio is the input we would like to give the model, but the
    handler crops three random 10 s windows from whatever it is handed, and our
    deployed `sow-acestep-poc` app rejects any other length outright (see
    `acestep_repaint_app.py`: 1,440,000 frames at 48 kHz keeps the three `randint`
    ranges collapsed, so the crop is deterministic). More audio therefore cannot be
    heard; what can be chosen is *which* 30 s. So: the last 10 s of the outgoing
    arrangement, the first 10 s of the incoming arrangement, and 10 s from the
    middle of the side whose key/tempo the bridge is generated in. That spans the
    arranged audio instead of repeating two adjacent windows.
    """
    source_a, source_b = _reference_sources(seam)
    a_tail = _as_reference_segment(source_a, (source_a.shape[0] / SR) - REFERENCE_SEGMENT_SECONDS)
    b_head = _as_reference_segment(source_b, 0.0)
    aligned = source_a if align == "outgoing" else source_b
    middle = _as_reference_segment(aligned, (aligned.shape[0] / SR) / 2.0)
    segments = [a_tail, b_head, middle]
    expected = seconds_to_samples(REFERENCE_SEGMENT_SECONDS)
    short = [segment.shape[0] for segment in segments if segment.shape[0] < expected]
    if short:
        print(
            f"  note: reference windows are short ({short} frames of {expected}); padding",
            flush=True,
        )

    import scipy.signal

    joined = np.concatenate(segments, axis=0).astype(np.float64)
    resampled = scipy.signal.resample_poly(joined, REFERENCE_SR, SR, axis=0).astype(np.float32)
    resampled, corrected = fit_length(resampled, REFERENCE_SAMPLES)
    if corrected:
        print(
            f"  note: reference resampling landed {REFERENCE_SAMPLES} frames after a pad/trim",
            flush=True,
        )
    if resampled.shape != (REFERENCE_SAMPLES, 2):
        raise RuntimeError(f"reference has shape {resampled.shape}, expected (1440000, 2)")
    return np.ascontiguousarray(resampled, dtype=np.float32)


def write_reference(path: Path, reference: np.ndarray) -> bytes:
    """Write the 48 kHz reference and assert the frames/rate the server requires."""
    write_flac_at(path, reference, REFERENCE_SR)
    info = sf.info(str(path))
    if info.frames != REFERENCE_SAMPLES or info.samplerate != REFERENCE_SR:
        raise RuntimeError(
            f"encoded reference is {info.frames} frames at {info.samplerate} Hz; "
            f"the server's reference crop is only deterministic at {REFERENCE_SAMPLES} "
            f"frames at {REFERENCE_SR} Hz"
        )
    return path.read_bytes()


def write_context_files(
    out_dir: Path, seams: dict[str, Seam], align: str, with_reference: bool
) -> dict[str, str]:
    """Write each seam's arranged A/B audio and its 30 s reference; return scope -> rel path.

    The A/B files are the exact montage parts, so the page can play what the model
    heard on each side (146.5 s and 177.3 s for this songset) next to the takes.
    """
    references: dict[str, str] = {}
    for scope, seam in seams.items():
        write_flac(out_dir / "context" / f"{scope}__a.flac", seam.a_part())
        write_flac(out_dir / "context" / f"{scope}__b.flac", seam.b_part())
        if not with_reference:
            continue
        relative = f"reference/{scope}__{align}.flac"
        write_reference(out_dir / relative, build_reference(seam, align))
        references[scope] = relative
        print(f"  wrote {relative} (30.0 s at {REFERENCE_SR} Hz)", flush=True)
    return references


def reference_bytes_for(
    out_dir: Path, seams: dict[str, Seam], align: str, engine: str, with_reference: bool
) -> dict[str, Optional[bytes]]:
    """Per-scope reference bytes for the upload; SA3 never uploads one."""
    if engine == "sa3" or not with_reference:
        return {}
    return {
        scope: (out_dir / f"reference/{scope}__{align}.flac").read_bytes() for scope in seams
    }


# ---------------------------------------------------------------------------
# Captions
# ---------------------------------------------------------------------------


def caption_keys() -> list[str]:
    keys = [f"boundary/{arc}/{treatment}" for arc in ARCS for treatment in TREATMENTS]
    keys += [f"intra/{arc}/inst" for arc in ARCS]
    return keys


def gap_captions(engine: str) -> dict[str, str]:
    family = "sa3" if engine == "sa3" else "acestep"
    return {f"gap/{style}": text for style, text in GAP_PROMPTS[family].items()}


def caption_facts(boundary: Seam, intra: Optional[Seam]) -> str:
    detail = boundary.detail
    lines = [
        "Seam A (boundary between two songs of one worship set):",
        f"- outgoing song: {detail['left_song']}, key {detail['left_key']}, "
        f"{detail['bpm_left']} BPM, exits through its {detail['left_component']} section, level "
        f"{detail['left_energy_db']} dB",
        f"- incoming song: {detail['right_song']}, key {detail['right_key']}, "
        f"{detail['bpm_right']} BPM, arrives through its {detail['right_component']} section at "
        f"{detail['boundary_right_seconds']} s, level {detail['right_energy_db']} dB",
        f"- production gap between them: {detail['production_gap_seconds']} s "
        f"({detail['gap_beats']} beats at the incoming tempo)",
        f"- theme {detail['theme']}, vocal posture {detail['vocal_posture']}",
        "- the generated bridge replaces the seam: it must begin in the outgoing recording's "
        "texture and end on an open cadence that lands exactly on the incoming recording's entry",
    ]
    if intra is not None:
        lines += [
            "",
            "Seam B (inside one song, verse into chorus):",
            f"- song: {intra.detail['song']}, key {intra.detail['left_key']}, "
            f"{intra.detail['bpm_left']} BPM, "
            f"{intra.detail['left_component']} ({intra.detail['left_energy_db']} dB) into "
            f"{intra.detail['right_component']} ({intra.detail['right_energy_db']} dB)",
            f"- energy jump {intra.detail['energy_delta_db']} dB; the bridge must land on the "
            "chorus entry",
        ]
    return "\n".join(lines)


def caption_instructions() -> str:
    keys = caption_keys()
    return (
        "Write one music-generation caption per key below. Respond with a single JSON "
        'object of the form {"<key>": {"caption": "<text>"}} containing exactly these keys: '
        + json.dumps(keys)
        + "\n\nCaption rules:\n"
        "- English, comma-separated style descriptors, at most 300 characters.\n"
        "- Name instruments, density and dynamics; describe the music, not a storyline.\n"
        "- Do NOT mention key or BPM values: they are passed to the model as parameters.\n"
        "- Each caption describes ONE WHOLE bridge, not half of one: it begins from the "
        "outgoing recording's texture and ends on an open cadence that leads into the "
        "incoming recording's entry.\n"
        "- Treatment 'inst' means no voices at all; say 'no vocals'.\n"
        "- Treatment 'texture' means a wordless ooh/ahh vocal pad: no words, only vowel sounds.\n"
        "- Arcs: keep-intensity keeps the energy flat; settle-then-lift drops then rebuilds; "
        "quiet-interlude strips down to piano and pad."
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


def load_captions(
    out_dir: Path,
    facts: str,
    refresh: bool,
    mode: str = "bridge",
    engine: str = "base",
) -> dict[str, Any]:
    path = out_dir / "captions.json"
    if mode == "gap":
        return load_gap_captions(path, refresh, engine)

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


def load_gap_captions(path: Path, refresh: bool, engine: str) -> dict[str, Any]:
    """Gap mode never calls the LLM: exactly two fixed drum prompts per engine family."""
    wanted = gap_captions(engine)
    if path.exists() and not refresh:
        data = json.loads(path.read_text())
        existing = {
            key: value
            for key, value in (data.get("captions") or {}).items()
            if normalize_caption_value(value)
        }
        if data.get("source") == "fixed" and data.get("engine") == engine and set(wanted) <= set(
            existing
        ):
            print(f"  reusing {path} ({len(existing)} fixed captions, engine={engine})")
            return data
        if data.get("source") == "fixed":
            print(
                f"  {path} was written for engine={data.get('engine')}; rewriting the fixed "
                f"prompts for engine={engine}",
                flush=True,
            )
    return write_captions(path, gap_payload(engine))


def gap_payload(engine: str) -> dict[str, Any]:
    return {
        "version": 1,
        "source": "fixed",
        "engine": engine,
        "captions": {key: {"caption": text} for key, text in gap_captions(engine).items()},
    }


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
                    "You write concise music-generation captions for newly generated seam music "
                    "that joins two live worship recordings."
                ),
            },
            {"role": "user", "content": f"{facts}\n\n{caption_instructions()}"},
        ],
        "response_format": {"type": "json_object"},
        # 9 captions plus reasoning effort tokens: small budgets truncate the JSON mid-string.
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


def modal_client(engine: str):
    import modal

    spec = ENGINES[engine]
    return modal.Cls.from_name(spec["app"], spec["class"])()


def call_engine(
    client,
    engine: str,
    audio_bytes: bytes,
    filename: str,
    request: dict[str, Any],
    reference_bytes: Optional[bytes],
) -> dict[str, Any]:
    """One uploaded montage -> one generated audio file.

    The deployed classes deliberately have no server-side retries, so transient
    Modal failures are retried here; a request error is deterministic and retried
    once anyway (the wrapper re-raises it and the take is recorded as failed).
    """
    last_error: Optional[Exception] = None
    for attempt in range(1, REPAINT_ATTEMPTS + 1):
        try:
            if engine == "sa3":
                return client.inpaint.remote(audio_bytes, filename, request)
            return client.repaint.remote(audio_bytes, filename, request, reference_bytes)
        except Exception as exc:
            last_error = exc
            print(f"    {engine} attempt {attempt}/{REPAINT_ATTEMPTS} failed: {exc}", flush=True)
            if attempt < REPAINT_ATTEMPTS:
                time.sleep(REPAINT_BACKOFF_SECONDS)
    raise RuntimeError(f"{engine} generation failed after {REPAINT_ATTEMPTS} attempts: {last_error}")


def build_request(
    seam: Seam,
    caption: str,
    treatment: str,
    seed: int,
    hole_seconds: float,
    montage_seconds: float,
    align: str,
    engine: str,
    steps_override: int,
    hole_start_seconds: Optional[float] = None,
) -> dict[str, Any]:
    """One request for one pass over the whole hole.

    Bridge mode sends a single mask spanning `[hole_start, hole_start+N)`, so the
    model hears real A on one side and real B on the other for the entire pass;
    the old two-pass split gave the first pass silence on its right and a
    different `keyscale` on each half. `hole_start` is the length of the outgoing
    arranged audio, not a fixed 8 s. SA3 has no key/bpm/reference fields at all,
    so its request is exactly the six keys its handler requires.
    """
    if hole_start_seconds is None:
        hole_start_seconds = seam.a_part().shape[0] / SR
    start = float(hole_start_seconds)
    end = start + hole_seconds
    if engine == "sa3":
        return {
            "prompt": caption,
            "seed": seed,
            "steps": steps_override or ENGINES["sa3"]["steps"],
            "start_seconds": start,
            "end_seconds": end,
            "duration_seconds": montage_seconds,
        }

    outgoing = align == "outgoing"
    spec = ENGINES[engine]
    instrumental = treatment == "inst"
    return {
        "caption": caption,
        "lyrics": INSTRUMENTAL_LYRICS if instrumental else TEXTURE_LYRICS,
        "instrumental": instrumental,
        "bpm": seam.bpm_left if outgoing else seam.bpm_right,
        "keyscale": seam.key_left if outgoing else seam.key_right,
        "duration": montage_seconds,
        "seed": seed,
        "repaint_start": round(start, 3),
        "repaint_end": round(end, 3),
        "vocal_language": "en",
        "inference_steps": steps_override or spec["steps"],
        "guidance_scale": spec["guidance_scale"],
        "use_adg": spec["use_adg"],
        "shift": spec["shift"],
        # conservative resolves to injection 1.0, 25 latent frames, 0.05 s wav
        # crossfade. The explicit repaint_*_crossfade* fields are dead (overwritten
        # downstream) and are deliberately not sent.
        "repaint_mode": "conservative",
        "repaint_strength": 1.0,
    }


# ---------------------------------------------------------------------------
# Bridge takes
# ---------------------------------------------------------------------------


def take_id_for(variant: Variant, align: str, engine: str, seed: int) -> str:
    return f"{variant.id}__{align}__{engine}__s{seed}"


def run_take(
    variant: Variant,
    take_index: int,
    seed: int,
    seam: Seam,
    montage: Montage,
    montage_bytes: bytes,
    caption: str,
    out_dir: Path,
    client,
    engine: str,
    align: str,
    steps_override: int,
    reference_bytes: Optional[bytes],
    force: bool,
    dry_run: bool,
) -> dict[str, Any]:
    take_id = take_id_for(variant, align, engine, seed)
    record: dict[str, Any] = {
        "id": take_id,
        "variant_id": variant.id,
        "scope": variant.scope,
        "arc": variant.arc,
        "length_seconds": variant.length_seconds,
        "treatment": variant.treatment,
        "engine": engine,
        "align": align,
        "take": take_index,
        "seed": seed,
        "status": "pending",
        "clip": f"clips/{take_id}.flac",
        "bridge": f"bridges/{take_id}.flac",
        "montage": f"montages/{variant.id}__{montage_key(montage)}.flac",
        "captions": {"caption": caption},
        "request": {},
        "meta": None,
        "metrics": {},
        "length_corrected": False,
        "error": None,
    }

    sidecar = out_dir / "variants" / f"{take_id}.json"
    clip_path = out_dir / "clips" / f"{take_id}.flac"
    if sidecar.exists() and not force and clip_path.exists():
        previous = json.loads(sidecar.read_text())
        if previous.get("status") == "ok":
            print(f"  skip {take_id} (clip exists; --force to regenerate)", flush=True)
            return previous

    request = build_request(
        seam,
        caption,
        variant.treatment,
        seed,
        float(variant.length_seconds),
        montage.duration_seconds,
        align,
        engine,
        steps_override,
        montage.hole_start / SR,
    )
    record["request"] = request

    if dry_run:
        record["status"] = "dry_run"
        record["metrics"] = {
            "montage_duration_seconds": round(montage.duration_seconds, 3),
            "montage_context_seconds": {
                "a": montage.a_context_seconds,
                "b": montage.b_context_seconds,
            },
            "hole_seconds": variant.length_seconds,
            "a_rms_db": round(
                rms_db(montage.samples[: montage.hole_start][-seconds_to_samples(PROBE_SECONDS) :]),
                2,
            ),
            "b_rms_db": round(
                rms_db(montage.samples[montage.hole_end :][: seconds_to_samples(PROBE_SECONDS)]), 2
            ),
        }
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        sidecar.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n")
        return record

    workdir = Path(tempfile.mkdtemp(prefix=f"inpaint-{take_id}-"))
    try:
        print(f"  {take_id}: one pass (seed {seed})", flush=True)
        generated = call_engine(
            client,
            engine,
            montage_bytes,
            f"{take_id}-montage.flac",
            request,
            reference_bytes,
        )
        samples = decode_flac_bytes(generated["audio"], workdir, "pass")
        expected = montage.samples.shape[0]
        samples, corrected = fit_length(samples, expected)
        returned = samples.shape[0]

        hole = np.ascontiguousarray(
            samples[montage.hole_start : montage.hole_end], dtype=np.float32
        )
        if (
            not np.isfinite(rms_db(hole))
            or peak_level(hole) < SILENCE_PEAK_FLOOR
            or rms_db(hole) < SILENCE_RMS_FLOOR_DB
        ):
            raise RuntimeError(
                f"returned hole is silent (peak {peak_level(hole):.6f}, "
                f"{rms_db(hole):.1f} dB RMS); refusing to splice it"
            )

        record["length_corrected"] = corrected
        record["returned_samples"] = returned
        record["meta"] = generated.get("meta") or {}
        record["variant_id"] = variant.id
        record["length_seconds"] = variant.length_seconds
        record["engine"] = engine
        record["align"] = align
        assemble(record, seam, montage, hole, out_dir, HOLE_FADE_SECONDS)
        record["status"] = "ok"
    except Exception as exc:
        record["status"] = "failed"
        record["error"] = f"{type(exc).__name__}: {exc}"
        print(f"  {take_id} FAILED: {record['error']}", flush=True)

    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n")
    return record


def bytes_from_array(samples: np.ndarray) -> bytes:
    """Encode samples as FLAC bytes for upload (kept lossless; the model resamples itself)."""
    with tempfile.TemporaryDirectory(prefix="inpaint-encode-") as tmp:
        path = Path(tmp) / "montage.flac"
        write_flac(path, samples)
        return path.read_bytes()


# ---------------------------------------------------------------------------
# Gap timing correction (copied from gen_seam_fill_run3.py)
# ---------------------------------------------------------------------------


def detect_onsets(fill: np.ndarray) -> np.ndarray:
    """Backtracked onset positions (samples) of a fill, one per attack.

    The detector splits a single sharp attack into two hits ~23 ms apart: on the
    15 s-context ACE-Step fill it reported onsets at 23.22 and 46.44 ms while the
    energy peak sat on the incoming beat at 696.53 ms. The later hit then became
    "the second onset" and reported a -650 ms beat-two error on a fill that was
    in time, so hits closer than MIN_ONSET_SPACING_SECONDS are one attack.
    """
    import librosa

    onset_env = librosa.onset.onset_strength(y=fill.mean(axis=1), sr=SR)
    onsets = librosa.onset.onset_detect(
        onset_envelope=onset_env, sr=SR, backtrack=True, delta=0.05
    )
    spacing = seconds_to_samples(MIN_ONSET_SPACING_SECONDS)
    kept: list[int] = []
    for sample in np.asarray(librosa.frames_to_samples(onsets), dtype=np.int64):
        if not kept or sample - kept[-1] >= spacing:
            kept.append(int(sample))
    return np.asarray(kept, dtype=np.int64)


def correct_gap_timing(
    fill: np.ndarray,
    onset_samples: np.ndarray,
    beat_samples: int,
    fill_samples: int,
    mode: str,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Return a *fill_samples*-frame fill whose first hit is at sample 0.

    `trim` only removes the silent lead-in and pads/trims the end, so the drum
    sound and the internal timing of the performance are untouched. `grid` then
    time-stretches so the second hit lands exactly one beat after the first;
    stretching moves transients, so its artifacts have to be judged by ear.

    Raises `ValueError` rather than fabricating a result: no onset at all, no
    second hit inside ±half a beat, or a stretch the performance cannot survive.
    """
    if mode not in ("trim", "grid"):
        raise ValueError(f"unknown correction mode {mode!r}")
    onsets = np.asarray(onset_samples, dtype=np.int64).ravel()
    if onsets.size == 0:
        raise ValueError("no drum onset detected")
    first = int(onsets[0])
    if first < 0 or first >= fill.shape[0]:
        raise ValueError(f"first onset {first} outside fill of {fill.shape[0]} frames")

    trimmed = np.ascontiguousarray(fill[first:], dtype=np.float32)
    correction: dict[str, Any] = {
        "trimmed_samples": first,
        "trimmed_seconds": round(first / SR, 6),
        "stretch_ratio": None,
    }
    if mode == "trim":
        corrected, _ = fit_length(trimmed, fill_samples)
        return np.ascontiguousarray(corrected, dtype=np.float32), correction

    target = int(beat_samples)
    wanted = first + target
    half = target // 2
    candidates = [int(s) for s in onsets[1:] if wanted - half <= s <= wanted + half]
    if not candidates:
        raise ValueError(f"no second onset within {half} samples of beat two (sample {wanted})")
    anchor = min(candidates, key=lambda sample: abs(sample - wanted))
    ratio = (anchor - first) / float(target)
    if not (STRETCH_RATIO_MIN < ratio < STRETCH_RATIO_MAX):
        raise ValueError(
            f"time-stretch ratio {ratio:.4f} outside ({STRETCH_RATIO_MIN}, {STRETCH_RATIO_MAX})"
        )
    import librosa

    channels = [
        librosa.effects.time_stretch(np.ascontiguousarray(trimmed[:, channel]), rate=ratio)
        for channel in range(trimmed.shape[1])
    ]
    stretched = np.ascontiguousarray(np.stack(channels, axis=1), dtype=np.float32)
    corrected, _ = fit_length(stretched, fill_samples)
    correction.update(
        {
            "source_beat_two_samples": anchor,
            "stretch_ratio": round(ratio, 6),
            "target_beat_two_ms": round(target / SR * 1000.0, 3),
        }
    )
    return np.ascontiguousarray(corrected, dtype=np.float32), correction


def trailing_silence_ms(fill: np.ndarray) -> float:
    """Dead air after the fill's last non-silent sample, in ms."""
    values = np.abs(fill.mean(axis=1))
    material = np.flatnonzero(values > SILENCE_FLOOR)
    if material.size == 0:
        return round(fill.shape[0] / SR * 1000.0, 2)
    return round((fill.shape[0] - 1 - int(material[-1])) / SR * 1000.0, 2)


def beat_two_peak_ms(fill: np.ndarray, beat_samples: int) -> Optional[float]:
    """Where the fill's loudest sample inside the beat-two window actually is.

    Detected onsets backtrack to the start of an attack; a slow swell therefore
    reports a later energy peak than its onset. This measures the audio, so a
    time-stretched fill's alignment can be checked without trusting `times[1]`.
    """
    radius = int(BEAT_TWO_WINDOW_MS / 1000.0 * SR)
    low = max(0, beat_samples - radius)
    high = min(fill.shape[0], beat_samples + radius)
    if high <= low:
        return None
    values = np.abs(fill.mean(axis=1))
    return round((low + int(np.argmax(values[low:high]))) / SR * 1000.0, 2)


def onset_metrics(fill: np.ndarray, beat_samples: int) -> dict[str, Any]:
    """Onset / leading-silence / beat-two metrics for one written fill."""
    onsets = detect_onsets(fill)
    times = [round(float(sample) / SR * 1000.0, 2) for sample in onsets]
    first = times[0] if times else None
    second = times[1] if len(times) > 1 else None
    target_ms = beat_samples / SR * 1000.0
    return {
        "onset_times_ms": times,
        "first_onset_ms": first,
        "second_onset_ms": second,
        "leading_silence_ms": first,
        "beat_two_error_ms": None if second is None else round(second - target_ms, 2),
        "beat_two_target_ms": round(target_ms, 3),
    }


def timing_metrics(fill: np.ndarray, beat_samples: int) -> dict[str, Any]:
    """Every timing field the report shows for one written fill."""
    metrics = onset_metrics(fill, beat_samples)
    metrics["trailing_silence_ms"] = trailing_silence_ms(fill)
    peak = beat_two_peak_ms(fill, beat_samples)
    metrics["beat_two_peak_ms"] = peak
    metrics["beat_two_peak_error_ms"] = (
        None if peak is None else round(peak - metrics["beat_two_target_ms"], 2)
    )
    return metrics


# ---------------------------------------------------------------------------
# Assembly and metrics
# ---------------------------------------------------------------------------


def assemble(
    record: dict[str, Any],
    seam: Seam,
    montage: Montage,
    hole: np.ndarray,
    out_dir: Path,
    fade_seconds: float,
    overlap_seconds: float = HOLE_OVERLAP_SECONDS,
    gap: bool = False,
) -> np.ndarray:
    """Gain-stage the hole, splice the clip from the original context, measure it.

    The clip is a listening excerpt built from *the recordings*, never from the
    model's roundtrip: `A arranged tail (15 s) + hole + B arranged head (15 s)`,
    both sides samples of the source audio. Only the hole is generated; the model
    itself heard the entire arranged audio (146.5 s and 177.3 s here) in the
    montage. Fades go only at the two hole edges.

    Returns the written hole (after gain, peak guard and fades).
    """
    a_part = seam.a_clip_context()
    b_part = seam.b_clip_context()

    probe = seconds_to_samples(PROBE_SECONDS)
    a_level = rms_db(a_part[-probe:])
    b_level = rms_db(b_part[:probe])
    target_mean = 0.5 * (a_level + b_level)

    hole = np.array(hole, dtype=np.float32, copy=True)
    hole_level = rms_db(hole)
    # Stage on the *joins*, not on 4 s means. Take 1 of the arranged-audio run
    # produced an 8 s bridge whose own last 50 ms sat 23 dB below its mean (the
    # model ended the phrase on a decay) while B enters at a steady -26.4 dB: a
    # mean-targeted tilt left a 16 dB dropout at the right splice — the run1
    # defect. Anchoring each end to the 200 ms of real audio beside it removes
    # that step, whatever envelope the model returned.
    edge = seconds_to_samples(EDGE_PROBE_SECONDS)
    fade = seconds_to_samples(fade_seconds)
    a_edge = rms_db(a_part[-edge:])
    b_edge = rms_db(b_part[:edge])
    hole_edge_start = rms_db(hole[:edge])
    hole_edge_end = rms_db(hole[hole.shape[0] - edge - fade : hole.shape[0] - fade])
    gain_start = a_edge - hole_edge_start
    gain_end = b_edge - hole_edge_end
    edge_gain: dict[str, Optional[float]] = {"start": None, "end": None}
    if np.isfinite(gain_start) and np.isfinite(gain_end):
        gain_start = float(np.clip(gain_start, -EDGE_GAIN_LIMIT_DB, EDGE_GAIN_LIMIT_DB))
        gain_end = float(np.clip(gain_end, -EDGE_GAIN_LIMIT_DB, EDGE_GAIN_LIMIT_DB))
        hole *= gain_ramp(hole.shape[0], gain_start, gain_end)[:, None]
        edge_gain = {"start": round(gain_start, 2), "end": round(gain_end, 2)}

    dead_edges = {
        "start": dead_edge_seconds(hole, from_start=True),
        "end": dead_edge_seconds(hole, from_start=False),
    }
    peak_limit = "none"
    context_peak = max(peak_level(a_part), peak_level(b_part))
    if context_peak > 1.0:
        # A source that clips would wrap in PCM16; scaling everything keeps the
        # splice consistent. Above 0.98 but below 1.0 the real audio is written
        # untouched — the recordings are not a place to spend headroom.
        scale = PEAK_CEILING / max(context_peak, peak_level(hole))
        peak_limit = "clip"
        a_part = a_part * scale
        hole = hole * scale
        b_part = b_part * scale
    elif peak_level(hole) > PEAK_CEILING:
        hole *= PEAK_CEILING / peak_level(hole)
        peak_limit = "hole"

    # Models fade their mask region in. SA3's pad captions opened every arranged-run
    # bridge 19-37 dB under the surrounding music, and no 18 dB ramp may lift that
    # without amplifying noise; A's recording continues past the seam, so the head
    # is covered with A's own audio and crossfaded into the generated region.
    head_pad = 0
    if not gap:
        cap = hole.shape[0] // 3
        available = seam.a_head_pad(cap)
        head_pad = min(seconds_to_samples(dead_edges["start"] + HEAD_PAD_LEAD_SECONDS), cap, available.shape[0])
        if head_pad >= seconds_to_samples(0.1):
            cross = min(head_pad, seconds_to_samples(HEAD_PAD_FADE_SECONDS))
            ramp = np.linspace(0.0, 1.0, cross, endpoint=False, dtype=np.float32)[:, None]
            hole = np.ascontiguousarray(
                np.concatenate(
                    [
                        available[: head_pad - cross],
                        available[head_pad - cross : head_pad] * np.cos(ramp * (np.pi / 2.0))
                        + hole[head_pad - cross : head_pad] * np.sin(ramp * (np.pi / 2.0)),
                        hole[head_pad:],
                    ],
                    axis=0,
                )
            )
            dead_edges = {
                "start": dead_edge_seconds(hole, from_start=True),
                "end": dead_edges["end"],
            }
        else:
            head_pad = 0

    # Hand the bridge over to B's real audio over the last HOLE_OVERLAP_SECONDS.
    # Edge-anchored staging fixes the *level*, but the model still ended take 1's
    # phrase on a 22 dB decay (-21 dB at -2.5 s to -43 dB at -0.1 s): an audible
    # decrescendo into the join that no gain ramp makes musical. An equal-power
    # overlap replaces that tail with the beginning of B, sample for sample, and
    # costs nothing outside the overlap.
    # Bridge takes only. Gap mode passes 0: its hole is the production 2-beat gap
    # and B must enter exactly at its end — a handoff there would pull B's downbeat
    # earlier (139 ms, 12% of the gap) and put B's audio inside the measured fill.
    overlap = 0 if gap else min(
        seconds_to_samples(overlap_seconds),
        b_part.shape[0] // 4,
    )
    if overlap > 0:
        ramp = np.linspace(0.0, 1.0, overlap, endpoint=False, dtype=np.float32)[:, None]
        spliced_hole = np.concatenate(
            [
                hole[: hole.shape[0] - overlap],
                hole[hole.shape[0] - overlap :] * np.cos(ramp * (np.pi / 2.0))
                + b_part[:overlap] * np.sin(ramp * (np.pi / 2.0)),
            ],
            axis=0,
        )
    else:
        spliced_hole = hole
    hole_level_after = rms_db(hole)
    # By construction after staging: each end of the hole sits on the 200 ms of
    # real audio beside it. Measured again so the page can show the residual.
    staged_edge_error = {
        "start": finite(a_edge - rms_db(hole[:edge]), 2),
        "end": finite(b_edge - rms_db(hole[hole.shape[0] - edge - fade : hole.shape[0] - fade]), 2),
    }
    # A `None` residual means the level was not measurable (silence on one side):
    # that is a rejection too, not a number to compare.
    if gap and any(
        residual is None or abs(residual) > MAX_JOIN_RESIDUAL_DB
        for residual in staged_edge_error.values()
    ):
        raise RuntimeError(
            f"fill cannot be staged onto the gap: residual "
            f"{staged_edge_error['start']}/{staged_edge_error['end']} dB after a "
            f"{EDGE_GAIN_LIMIT_DB} dB edge ramp; refusing to splice it"
        )
    trailing = trailing_silence_ms(hole)
    if gap and trailing > MAX_GAP_TRAILING_SILENCE_MS:
        raise RuntimeError(
            f"fill ends with {trailing} ms of dead air before the incoming downbeat "
            f"(max {MAX_GAP_TRAILING_SILENCE_MS} ms); refusing to splice it"
        )
    clip = np.ascontiguousarray(
        np.concatenate([a_part, spliced_hole, b_part[overlap:]], axis=0)
    )
    hole_start = a_part.shape[0]
    hole_end = hole_start + spliced_hole.shape[0]
    apply_join_fades(clip, (hole_start, hole_end), fade_seconds)
    written_hole = np.ascontiguousarray(clip[hole_start:hole_end])
    peak_after = peak_level(clip)

    clip_path = out_dir / "clips" / f"{record['id']}.flac"
    bridge_path = out_dir / "bridges" / f"{record['id']}.flac"
    write_flac(clip_path, clip)
    write_flac(bridge_path, written_hole)

    import librosa

    tempo, _ = librosa.beat.beat_track(y=mono(written_hole), sr=SR)
    tempo_value = float(np.atleast_1d(tempo)[0])

    probe_a = a_part[-probe:]
    probe_b = b_part[:probe]
    chroma_hole = chroma_mean(written_hole)
    chroma_a = chroma_mean(probe_a)
    chroma_b = chroma_mean(probe_b)

    edge = seconds_to_samples(0.05)
    fade_samples = seconds_to_samples(fade_seconds)
    # Fade-free, overlap-free windows on each side of each join.
    left_out = clip[hole_start - edge : hole_start]
    left_in = clip[hole_start + fade_samples : hole_start + fade_samples + edge]
    right_in = clip[hole_end - fade_samples - edge : hole_end - fade_samples]
    right_out = clip[hole_end : hole_end + edge]
    # What the splice would have measured without the handoff: the staged hole's
    # own tail against B's entry. Kept so the page can show what the overlap fixed.
    raw_right_in = hole[hole.shape[0] - fade_samples - edge : hole.shape[0] - fade_samples]
    align = str(record.get("align") or "incoming")
    outgoing = align == "outgoing"

    record["metrics"] = {
        "a_level_db": finite(a_level, 2),
        "b_level_db": finite(b_level, 2),
        "hole_target_mean_db": finite(target_mean, 2),
        "hole_level_db_before": finite(hole_level, 2),
        "hole_level_db_after": finite(hole_level_after, 2),
        "hole_edge_gain_db": edge_gain,
        "staged_edge_error_db": staged_edge_error,
        "a_edge_db": finite(a_edge, 2),
        "b_edge_db": finite(b_edge, 2),
        "hole_overlap_seconds": round(overlap / SR, 3),
        "hole_head_pad_seconds": round(head_pad / SR, 3),
        "hole_duration_seconds": round(written_hole.shape[0] / SR, 3),
        "clip_duration_seconds": round(clip.shape[0] / SR, 3),
        "montage_duration_seconds": round(montage.duration_seconds, 3),
        "montage_context_seconds": {
            "a": montage.a_context_seconds,
            "b": montage.b_context_seconds,
        },
        "hole_dead_edge_seconds": dead_edges,
        "hole_trailing_silence_ms": trailing,
        "hole_samples": int(written_hole.shape[0]),
        "length_corrected": bool(record.get("length_corrected")),
        "peak": finite(peak_after, 4),
        "peak_limit": peak_limit,
        "context_centroid_hz": {
            "a": centroid_hz(probe_a),
            "b": centroid_hz(probe_b),
        },
        "hole_centroid_hz": centroid_hz(written_hole),
        "stereo_corr_hole": stereo_corr(written_hole),
        "stereo_corr_a": stereo_corr(probe_a),
        "stereo_corr_b": stereo_corr(probe_b),
        "chroma_corr_a": None if chroma_hole is None or chroma_a is None else pearson(chroma_hole, chroma_a),
        "chroma_corr_b": None if chroma_hole is None or chroma_b is None else pearson(chroma_hole, chroma_b),
        # Windows start/stop *outside* the de-click fades: measuring across them
        # reported the fade itself (-23 dB "jump" that was our own ramp).
        "join_rms_jump_db": {
            "left": finite(rms_db(left_in) - rms_db(left_out), 2),
            "right": finite(rms_db(right_out) - rms_db(right_in), 2),
        },
        "raw_join_rms_jump_db": {
            "right": finite(rms_db(b_part[:edge]) - rms_db(raw_right_in), 2),
        },
        "join_centroid_jump_hz": {
            "left": _centroid_jump(left_out, left_in),
            "right": _centroid_jump(right_in, right_out),
        },
        "tempo_bpm_hole": finite(tempo_value, 2),
        "requested_bpm": seam.bpm_left if outgoing else seam.bpm_right,
        "requested_key": seam.key_left if outgoing else seam.key_right,
    }
    return written_hole


def _centroid_jump(before: np.ndarray, after: np.ndarray) -> Optional[float]:
    first = centroid_hz(before)
    second = centroid_hz(after)
    if first is None or second is None:
        return None
    return finite(second - first, 1)


# ---------------------------------------------------------------------------
# Gap takes
# ---------------------------------------------------------------------------


def plan_gap_takes(args) -> list[GapTake]:
    return [
        GapTake(
            id=f"gap__{style}__incoming__{args.engine}__s{args.seed + take_index}",
            style=style,
            seed=args.seed + take_index,
            take=take_index,
        )
        for style in GAP_PROMPTS["acestep"]
        for take_index in range(args.takes)
    ]


def gap_geometry(seam: Seam) -> dict[str, Any]:
    """The production gap, its fill and the incoming beat it must land on."""
    gap_seconds = float(seam.detail["production_gap_seconds"])
    tempo_right = float(seam.detail["recording_tempo_right"])
    return {
        "gap_seconds": gap_seconds,
        "fill_samples": seconds_to_samples(gap_seconds),
        "beat_samples": int(round(60.0 / tempo_right * SR)),
    }


def gap_identity(take: GapTake, engine: str, gap_seconds: float, montage_rel: str) -> dict[str, Any]:
    return {
        "raw_id": take.id,
        "variant_id": take.id,
        "mode": "gap",
        "style": take.style,
        "correction": None,
        "scope": "gap",
        "length_seconds": gap_seconds,
        "take": take.take,
        "seed": take.seed,
        "engine": engine,
        "align": "incoming",
        "montage": montage_rel,
        "model_fill": f"fills/{take.id}__model.flac",
    }


def run_gap_take(
    take: GapTake,
    seam: Seam,
    montage: Montage,
    montage_rel: str,
    montage_bytes: bytes,
    captions: dict[str, Any],
    out_dir: Path,
    client,
    engine: str,
    steps_override: int,
    reference_bytes: Optional[bytes],
    force: bool,
    dry_run: bool,
) -> list[dict[str, Any]]:
    """One raw model fill, then the raw / trim / grid splices derived from it."""
    geometry = gap_geometry(seam)
    gap_seconds = geometry["gap_seconds"]
    fill_samples = geometry["fill_samples"]
    beat_samples = geometry["beat_samples"]
    family = "sa3" if engine == "sa3" else "acestep"
    caption = GAP_PROMPTS[family][take.style]
    identity = gap_identity(take, engine, gap_seconds, montage_rel)
    model_fill_rel = identity["model_fill"]
    model_fill_path = out_dir / model_fill_rel
    request = build_request(
        seam,
        caption,
        "inst",
        take.seed,
        gap_seconds,
        montage.duration_seconds,
        "incoming",
        engine,
        steps_override,
        montage.hole_start / SR,
    )

    corrections = GAP_CORRECTIONS
    record_ids = {correction: f"{take.id}__{correction}" for correction in corrections}
    sidecars = {correction: out_dir / "variants" / f"{record_ids[correction]}.json" for correction in corrections}
    clips = {correction: out_dir / "clips" / f"{record_ids[correction]}.flac" for correction in corrections}

    if not force and all(
        sidecars[correction].exists()
        and clips[correction].exists()
        and json.loads(sidecars[correction].read_text()).get("status") == "ok"
        for correction in corrections
    ):
        print(f"  skip {take.id} (all three corrections exist; --force to regenerate)", flush=True)
        return [json.loads(sidecars[correction].read_text()) for correction in corrections]

    if dry_run:
        record = dict(identity)
        record.update(
            {
                "id": record_ids["raw"],
                "status": "dry_run",
                "correction": "raw",
                "clip": None,
                "bridge": None,
                "captions": {"caption": caption},
                "request": request,
                "meta": None,
                "metrics": {
                    "montage_duration_seconds": round(montage.duration_seconds, 3),
                    "montage_context_seconds": {
                        "a": montage.a_context_seconds,
                        "b": montage.b_context_seconds,
                    },
                    "hole_seconds": gap_seconds,
                    "hole_samples": montage.hole_samples,
                    "beat_samples": beat_samples,
                },
                "timing": None,
                "correction_meta": None,
                "error": None,
            }
        )
        _write_sidecar(sidecars["raw"], record)
        return [record]

    # 2. The model fill: generated once per raw take, reused by both corrections.
    meta: Optional[dict[str, Any]] = None
    if not force and model_fill_path.exists():
        model_fill = read_gap_fill(model_fill_path, fill_samples)
        if sidecars["raw"].exists():
            meta = json.loads(sidecars["raw"].read_text()).get("meta")
        print(f"  {take.id}: reusing {model_fill_rel}", flush=True)
    else:
        workdir = Path(tempfile.mkdtemp(prefix=f"inpaint-{take.id}-"))
        try:
            print(f"  {take.id}: one pass (seed {take.seed}, {fill_samples} frames)", flush=True)
            generated = call_engine(
                client,
                engine,
                montage_bytes,
                f"{take.id}-montage.flac",
                request,
                reference_bytes,
            )
            samples = decode_flac_bytes(generated["audio"], workdir, "pass")
            samples, _ = fit_length(samples, montage.samples.shape[0])
            model_fill = np.ascontiguousarray(
                samples[montage.hole_start : montage.hole_end], dtype=np.float32
            )
            if (
                not np.isfinite(rms_db(model_fill))
                or peak_level(model_fill) < SILENCE_PEAK_FLOOR
                or rms_db(model_fill) < SILENCE_RMS_FLOOR_DB
            ):
                raise RuntimeError(
                    f"returned fill is silent (peak {peak_level(model_fill):.6f}, "
                    f"{rms_db(model_fill):.1f} dB RMS); refusing to splice it"
                )
            write_flac(model_fill_path, model_fill)
            meta = generated.get("meta") or {}
        except Exception as exc:
            failed = dict(identity)
            failed.update(
                {
                    "id": record_ids["raw"],
                    "status": "failed",
                    "correction": "raw",
                    "clip": None,
                    "bridge": None,
                    "captions": {"caption": caption},
                    "request": request,
                    "meta": None,
                    "metrics": {},
                    "timing": None,
                    "correction_meta": None,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            print(f"  {take.id} FAILED: {failed['error']}", flush=True)
            _write_sidecar(sidecars["raw"], failed)
            return [failed]

    onsets = detect_onsets(model_fill)
    records: list[dict[str, Any]] = []
    for correction in corrections:
        if not force and sidecars[correction].exists() and clips[correction].exists():
            previous = json.loads(sidecars[correction].read_text())
            if previous.get("status") in ("ok", "unavailable"):
                print(f"  skip {record_ids[correction]} ({previous['status']}; --force)", flush=True)
                records.append(previous)
                continue

        record = dict(identity)
        record.update(
            {
                "id": record_ids[correction],
                "status": "pending",
                "correction": correction,
                "clip": f"clips/{record_ids[correction]}.flac",
                "bridge": f"bridges/{record_ids[correction]}.flac",
                "captions": {"caption": caption},
                "request": request,
                "meta": meta,
                "metrics": {},
                "timing": None,
                "correction_meta": None,
                "error": None,
            }
        )
        try:
            if correction == "raw":
                corrected = model_fill
                correction_meta: dict[str, Any] = {
                    "trimmed_samples": 0,
                    "trimmed_seconds": 0.0,
                    "stretch_ratio": None,
                }
            else:
                corrected, correction_meta = correct_gap_timing(
                    model_fill, onsets, beat_samples, fill_samples, correction
                )
            record["correction_meta"] = correction_meta
            written_hole = assemble(
                record,
                seam,
                montage,
                corrected,
                out_dir,
                GAP_FADE_SECONDS,
                gap=True,
            )
            record["timing"] = timing_metrics(written_hole, beat_samples)
            record["status"] = "ok"
        except ValueError as exc:
            # No onset, no second hit, or a destructive stretch: report it, never
            # fabricate a fill for the splice.
            record["status"] = "unavailable"
            record["error"] = str(exc)
            record["clip"] = None
            record["bridge"] = None
            print(f"  {record_ids[correction]} unavailable: {exc}", flush=True)
        except Exception as exc:
            record["status"] = "failed"
            record["error"] = f"{type(exc).__name__}: {exc}"
            record["clip"] = None
            record["bridge"] = None
            print(f"  {record_ids[correction]} FAILED: {record['error']}", flush=True)
        _write_sidecar(sidecars[correction], record)
        records.append(record)
    return records


def read_gap_fill(path: Path, fill_samples: int) -> np.ndarray:
    samples = read_wav(path)
    if samples.shape[0] != fill_samples:
        raise RuntimeError(
            f"{path} has {samples.shape[0]} frames, expected {fill_samples}"
        )
    return samples


def _write_sidecar(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------


def write_index(
    out_dir: Path,
    args,
    seams: dict[str, Seam],
    records: list[dict[str, Any]],
    references: dict[str, str],
    started: str,
    wall: float,
) -> None:
    engine = args.engine
    payload: dict[str, Any] = {
        "version": 2,
        "mode": args.mode,
        "songset_id": args.songset,
        "created_at": started,
        "engine": engine,
        "align": args.align,
        "takes": args.takes,
        "model": ENGINES[engine]["model"],
        "app": ENGINES[engine]["app"],
        "seed_base": args.seed,
        "wall_seconds": round(wall, 1),
        "seams": {scope: seam.detail for scope, seam in seams.items()},
        "reference": references,
        "variants": records,
    }
    if args.mode == "gap":
        geometry = gap_geometry(seams["boundary"])
        payload.update(
            {
                "gap_ms": int(round(geometry["gap_seconds"] * 1000.0)),
                "gap_seconds": geometry["gap_seconds"],
                "fill_samples": geometry["fill_samples"],
                "beat_samples": geometry["beat_samples"],
                "caption_source": "fixed",
            }
        )
    (out_dir / "index.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


CSS = """
  :root { color-scheme: light dark; }
  body { font: 14px/1.5 system-ui, sans-serif; margin: 0 auto; padding: 24px; max-width: 1200px; }
  h1 { font-size: 20px; margin: 0 0 4px; }
  .sub { color: #666; margin: 0 0 12px; }
  section { margin-bottom: 32px; }
  h2 { font-size: 16px; border-bottom: 1px solid #ddd; padding-bottom: 4px; }
  .grid { display: grid; gap: 16px; grid-template-columns: repeat(auto-fill, minmax(340px, 1fr)); }
  .card { border: 1px solid #ddd; border-radius: 8px; padding: 12px 14px; }
  .card h3 { font-size: 13px; margin: 0 0 6px; overflow-wrap: anywhere; }
  .take { border-top: 1px dashed #ddd; padding-top: 8px; margin-top: 8px; }
  .meta, .metrics { color: #555; font-size: 12px; margin: 4px 0; }
  .players { display: flex; flex-direction: column; gap: 4px; margin: 6px 0; }
  .players span { font-size: 11px; text-transform: uppercase; letter-spacing: .05em; color: #777; }
  audio { width: 100%; }
  .error { color: #b00; }
  code { font-size: 12px; }
  details { margin-top: 6px; font-size: 12px; }
  summary { cursor: pointer; }
  table.rubric { border-collapse: collapse; font-size: 12px; }
  table.rubric th, table.rubric td { border: 1px solid #ddd; padding: 4px 8px; text-align: left; }
  table.rubric td.w { text-align: right; font-variant-numeric: tabular-nums; }
  .score { display: flex; flex-wrap: wrap; gap: 6px 12px; align-items: center; font-size: 12px; margin: 4px 0; }
  .score label { display: flex; flex-direction: column; color: #555; font-size: 11px; }
  .score input { width: 48px; }
  .score-total, .card-total, .page-total { font-variant-numeric: tabular-nums; font-weight: 600; }
"""

# The listening decision is subjective; the evidence below it is not. Each
# criterion carries the measurements that can be checked without ears.
SCORING_CRITERIA: tuple[tuple[str, int, str], ...] = (
    (
        "Musical continuity",
        35,
        "chroma correlation against the real A/B probes; hole tempo",
    ),
    (
        "Boundary smoothness",
        25,
        "join RMS step left/right (fade-free 50 ms windows), the raw splice it "
        "replaced, and the residual error after edge staging. In gap mode the right "
        "value is expected to be positive and large: the fill decays into B's "
        "downbeat, so read the fill's trailing silence instead",
    ),
    (
        "Key and tempo transition",
        20,
        "requested key/BPM vs the recording's key/BPM; chroma A vs B",
    ),
    (
        "Audio fidelity",
        10,
        "L/R coherence, centroid, peak headroom",
    ),
    (
        "Preservation of source",
        10,
        "the clip's 15 s contexts are the original samples (peak_limit none); the "
        "handoff repeats B's first 0.5 s over the bridge's tail",
    ),
)


def _num(value: Any) -> str:
    return "—" if value is None else str(value)


def scoring_section() -> str:
    rows = "".join(
        f"<tr><td>{html.escape(name)}</td><td class=\"w\">{weight}</td>"
        f"<td>{html.escape(evidence)}</td></tr>"
        for name, weight, evidence in SCORING_CRITERIA
    )
    return (
        '<section><h2>Scoring guide</h2>'
        '<p class="meta">Score each take 0–10 per criterion; the weighted total is shown per take '
        "and averaged per card and per page. Weighted the same way for every engine, so an ACE-Step "
        "take and an SA3 take can be compared directly.</p>"
        '<table class="rubric"><thead><tr><th>Criterion</th><th>Weight</th>'
        f"<th>Objective proxy shown on each take</th></tr></thead><tbody>{rows}</tbody></table>"
        '<p class="meta">Page weighted total: <span class="page-total">—</span> / 10</p>'
        "</section>"
    )


def score_block(record: dict[str, Any]) -> str:
    if record.get("status") != "ok":
        return ""
    group = html.escape(str(record.get("id")))
    fields = "".join(
        f'<label>{html.escape(name)} ({weight})'
        f'<input type="number" min="0" max="10" step="1" class="score-input" '
        f'data-weight="{weight}" data-group="{group}"></label>'
        for name, weight, _ in SCORING_CRITERIA
    )
    return (
        f'<div class="score" data-group="{group}">{fields}'
        f"<span>weighted <span class=\"score-total\">—</span> / 10</span></div>"
    )


# Live weighted totals: no network, no dependencies. Scores are kept only in the
# page; the listener reads the totals off the screen.
SCORING_JS = """
(function () {
  function rowTotal(row) {
    var sum = 0, weight = 0;
    Array.prototype.forEach.call(row.querySelectorAll('input.score-input'), function (input) {
      var value = input.value === '' ? null : Number(input.value);
      if (value === null || Number.isNaN(value)) { return; }
      var w = Number(input.dataset.weight);
      sum += Math.min(Math.max(value, 0), 10) * w;
      weight += w;
    });
    return weight === 0 ? null : sum / weight;
  }
  function format(value) { return value === null ? '\\u2014' : value.toFixed(2); }
  function average(values) {
    if (!values.length) { return null; }
    return values.reduce(function (a, b) { return a + b; }, 0) / values.length;
  }
  function refresh() {
    var pageValues = [];
    Array.prototype.forEach.call(document.querySelectorAll('.score'), function (row) {
      var total = rowTotal(row);
      row.querySelector('.score-total').textContent = format(total);
      if (total !== null) { pageValues.push(total); }
    });
    Array.prototype.forEach.call(document.querySelectorAll('.card'), function (card) {
      var target = card.querySelector('.card-total');
      if (!target) { return; }
      var values = [];
      Array.prototype.forEach.call(card.querySelectorAll('.score'), function (row) {
        var total = rowTotal(row);
        if (total !== null) { values.push(total); }
      });
      target.textContent = format(average(values));
    });
    var page = document.querySelector('.page-total');
    if (page) { page.textContent = format(average(pageValues)); }
  }
  document.addEventListener('input', function (event) {
    if (event.target.classList && event.target.classList.contains('score-input')) { refresh(); }
  });
  refresh();
})();
"""


def metric_line(metrics: dict[str, Any]) -> str:
    context = metrics.get("context_centroid_hz") or {}
    joins = metrics.get("join_rms_jump_db") or {}
    centroid_joins = metrics.get("join_centroid_jump_hz") or {}
    staged = metrics.get("staged_edge_error_db") or {}
    gains = metrics.get("hole_edge_gain_db") or {}
    raw_join = metrics.get("raw_join_rms_jump_db") or {}
    montage_ctx = metrics.get("montage_context_seconds") or {}
    dead = metrics.get("hole_dead_edge_seconds") or {}
    return (
        f"hole {_num(metrics.get('hole_level_db_after'))} dB "
        f"(target {_num(metrics.get('hole_target_mean_db'))}) · "
        f"A {_num(metrics.get('a_level_db'))} / B {_num(metrics.get('b_level_db'))} dB · "
        f"chroma A {_num(metrics.get('chroma_corr_a'))} / B {_num(metrics.get('chroma_corr_b'))} · "
        f"stereo hole {_num(metrics.get('stereo_corr_hole'))} "
        f"(A {_num(metrics.get('stereo_corr_a'))} / B {_num(metrics.get('stereo_corr_b'))}) · "
        f"centroid hole {_num(metrics.get('hole_centroid_hz'))} Hz "
        f"(A {_num(context.get('a'))} / B {_num(context.get('b'))}) · "
        f"join RMS {_num(joins.get('left'))}/{_num(joins.get('right'))} dB "
        f"(raw right {_num(raw_join.get('right'))}) · "
        f"context {_num(montage_ctx.get('a'))}/{_num(montage_ctx.get('b'))} s · "
        f"dead edge {_num(dead.get('start'))}/{_num(dead.get('end'))} s · "
        f"join centroid {_num(centroid_joins.get('left'))}/{_num(centroid_joins.get('right'))} Hz · "
        f"edge gain {_num(gains.get('start'))}/{_num(gains.get('end'))} dB, "
        f"residual {_num(staged.get('start'))}/{_num(staged.get('end'))} dB, "
        f"head pad {_num(metrics.get('hole_head_pad_seconds'))} s · "
        f"handoff {_num(metrics.get('hole_overlap_seconds'))} s · "
        f"peak {_num(metrics.get('peak'))} ({_num(metrics.get('peak_limit'))})"
    )


def timing_line(timing: Optional[dict[str, Any]]) -> str:
    if not timing:
        return "timing not measured"
    return (
        f"first onset {_num(timing.get('first_onset_ms'))} ms · "
        f"leading silence {_num(timing.get('leading_silence_ms'))} ms · "
        f"beat-two peak error {_num(timing.get('beat_two_peak_error_ms'))} ms "
        f"(peak {_num(timing.get('beat_two_peak_ms'))} / target "
        f"{_num(timing.get('beat_two_target_ms'))}) · "
        f"second-onset error {_num(timing.get('beat_two_error_ms'))} ms · "
        f"trailing silence {_num(timing.get('trailing_silence_ms'))} ms · "
        f"beat-two peak {_num(timing.get('beat_two_peak_ms'))} ms"
    )


def players_block(record: dict[str, Any]) -> str:
    status = record.get("status")
    if status == "ok" and record.get("clip") and record.get("bridge"):
        return (
            f'<div class="players"><span>clip — 15 s real A + generated hole + 15 s real B</span>'
            f'<audio controls preload="metadata" src="{html.escape(record["clip"])}"></audio>'
            f"<span>bridge</span>"
            f'<audio controls preload="metadata" src="{html.escape(record["bridge"])}"></audio></div>'
        )
    if status == "dry_run":
        return '<p class="meta">dry run — no audio generated</p>'
    if status == "unavailable":
        return f'<p class="error">unavailable: {html.escape(str(record.get("error")))}</p>'
    return f'<p class="error">failed: {html.escape(str(record.get("error")))}</p>'


def context_section(seams: dict[str, Seam], references: dict[str, str], align: str) -> str:
    blocks: list[str] = []
    for scope, seam in seams.items():
        players = [
            (f"A arranged ({seam.a_part().shape[0] / SR:.1f} s)", f"context/{scope}__a.flac"),
            (f"B arranged ({seam.b_part().shape[0] / SR:.1f} s)", f"context/{scope}__b.flac"),
        ]
        if scope in references:
            players.append((f"reference ({align})", references[scope]))
        rows = "".join(
            f"<span>{html.escape(label)}</span>"
            f'<audio controls preload="metadata" src="{html.escape(relative)}"></audio>'
            for label, relative in players
        )
        blocks.append(
            f'<article class="card"><h3>{html.escape(scope)} — {html.escape(seam.label)}</h3>'
            f'<div class="players">{rows}</div></article>'
        )
    return (
        '<section><h2>Context</h2><div class="grid">' + "".join(blocks) + "</div></section>"
    )


def input_player_block(record: dict[str, Any]) -> str:
    """The montage that was uploaded: every arranged step of both songs + the hole."""
    montage = str(record.get("montage") or "")
    if not montage:
        return ""
    seconds = _num((record.get("metrics") or {}).get("montage_duration_seconds"))
    return (
        f'<div class="players"><span>input upload ({seconds} s)</span>'
        f'<audio controls preload="none" src="{html.escape(montage)}"></audio></div>'
    )


def bridge_card(
    variant_id: str, records: list[dict[str, Any]]
) -> str:
    first = records[0]
    caption = (first.get("captions") or {}).get("caption", "")
    rows: list[str] = []
    for record in records:
        rows.append(
            f'<div class="take"><p class="meta">{html.escape(str(record["id"]))} · '
            f'engine {html.escape(str(record.get("engine")))} · '
            f'align {html.escape(str(record.get("align")))} · '
            f'take {_num(record.get("take"))} · seed {_num(record.get("seed"))} · '
            f'length_corrected {record.get("length_corrected")}</p>'
            f"{players_block(record)}"
            f'<p class="metrics">{html.escape(metric_line(record.get("metrics") or {}))}</p>'
            f"{score_block(record)}</div>"
        )
    return (
        f'<article class="card"><h3>{html.escape(variant_id)}</h3>'
        f"<details><summary>caption</summary><p>{html.escape(caption)}</p></details>"
        f"{input_player_block(first)}"
        f'<p class="meta">card weighted: <span class="card-total">—</span> / 10</p>'
        + "".join(rows)
        + "</article>"
    )


def gap_card(raw_id: str, records: list[dict[str, Any]]) -> str:
    caption = (records[0].get("captions") or {}).get("caption", "")
    order = {"raw": 0, "trim": 1, "grid": 2}
    rows: list[str] = []
    for record in sorted(records, key=lambda r: order.get(str(r.get("correction")), 9)):
        correction_meta = record.get("correction_meta") or {}
        rows.append(
            f'<div class="take"><p class="meta">'
            f'{html.escape(str(record.get("correction")))} · seed {_num(record.get("seed"))} · '
            f'take {_num(record.get("take"))} · trimmed '
            f'{_num(correction_meta.get("trimmed_ms", correction_meta.get("trimmed_samples")))} '
            f'samples · stretch {_num(correction_meta.get("stretch_ratio"))}</p>'
            f"{players_block(record)}"
            f'<p class="metrics">{html.escape(timing_line(record.get("timing")))}</p>'
            f'<p class="metrics">{html.escape(metric_line(record.get("metrics") or {}))}</p>'
            f"{score_block(record)}</div>"
        )
    return (
        f'<article class="card"><h3>{html.escape(raw_id)}</h3>'
        f"<details><summary>caption</summary><p>{html.escape(caption)}</p></details>"
        f"{input_player_block(records[0])}"
        f'<p class="meta">card weighted: <span class="card-total">—</span> / 10</p>'
        + "".join(rows)
        + "</article>"
    )


def group_records(
    records: list[dict[str, Any]], key: str
) -> list[tuple[str, list[dict[str, Any]]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        grouped.setdefault(str(record.get(key) or record.get("id")), []).append(record)
    return list(grouped.items())


def report_html(
    seams: dict[str, Seam],
    records: list[dict[str, Any]],
    args,
    references: dict[str, str],
) -> str:
    boundary = seams.get("boundary")
    engine = args.engine
    uploaded = next(
        (
            (record.get("metrics") or {}).get("montage_context_seconds") or {}
            for record in records
            if (record.get("metrics") or {}).get("montage_context_seconds")
        ),
        {},
    )
    subtitle_bits = [
        f"mode: {args.mode}",
        f"songset {args.songset}",
        f"engine: {engine} ({ENGINES[engine]['model']})",
        f"align: {args.align}" + (" (forced)" if args.mode == "gap" else ""),
        f"takes: {args.takes}",
    ]
    if boundary:
        detail = boundary.detail
        left_label = detail.get("left_title") or detail["left_song"]
        right_label = detail.get("right_title") or detail["right_song"]
        subtitle_bits.append(
            f"{html.escape(str(left_label))} ({html.escape(str(detail['left_key']))}, "
            f"{detail['bpm_left']} BPM) → {html.escape(str(right_label))} "
            f"({html.escape(str(detail['right_key']))}, {detail['bpm_right']} BPM)"
        )
        subtitle_bits.append(f"production gap {detail['production_gap_seconds']} s")
        subtitle_bits.append(
            f"input: arranged A "
            f"{uploaded.get('a', boundary.a_part().shape[0] / SR):.1f} s + silence hole + "
            f"arranged B {uploaded.get('b', boundary.b_part().shape[0] / SR):.1f} s"
        )
        if detail.get("left_energy_db") is not None and detail.get("right_energy_db") is not None:
            subtitle_bits.append(
                f"seam level {detail['left_energy_db']} dB to {detail['right_energy_db']} dB"
            )

    sections: list[str] = []
    if args.mode == "gap":
        gap_seconds = float(boundary.detail["production_gap_seconds"]) if boundary else 0.0
        cards = [
            gap_card(raw_id, group) for raw_id, group in group_records(records, "raw_id")
        ]
        if cards:
            sections.append(
                f"<section><h2>Gap ({gap_seconds:.3f} s)</h2>"
                f'<div class="grid">{"".join(cards)}</div></section>'
            )
    else:
        groups: list[tuple[str, list[dict[str, Any]]]] = []
        for length in LENGTHS:
            groups.append(
                (
                    f"Boundary ({length} s)",
                    [
                        r
                        for r in records
                        if r.get("scope") == "boundary" and r.get("length_seconds") == length
                    ],
                )
            )
        groups.append(("Intra-song (8 s)", [r for r in records if r.get("scope") == "intra"]))
        for group_title, group_records_ in groups:
            if not group_records_:
                continue
            cards = [
                bridge_card(variant_id, group)
                for variant_id, group in group_records(group_records_, "variant_id")
            ]
            sections.append(
                f"<section><h2>{html.escape(group_title)}</h2>"
                f'<div class="grid">{"".join(cards)}</div></section>'
            )

    caption_note = (
        "Gap captions are the two fixed drum prompts; no LLM is called."
        if args.mode == "gap"
        else "Bridge captions were authored before generation and passed as parameters; keys and "
        "tempi are generation settings from the recordings, not measurements."
    )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Seam Inpaint POC</title>
<style>{CSS}</style>
</head>
<body>
  <h1>Seam Inpaint POC</h1>
  <p class="sub">{' · '.join(subtitle_bits)}</p>
  <p class="sub">model {html.escape(str(ENGINES[engine]['model']))} · app
  {html.escape(str(ENGINES[engine]['app']))} · steps
  {_num(args.steps or ENGINES[engine]['steps'])} · {html.escape(caption_note)}</p>
  {context_section(seams, references, args.align)}
  {scoring_section()}
  {''.join(sections)}
  <script>{SCORING_JS}</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate audio-inpaint seam clips with ACE-Step repaint or SA3 inpainting"
    )
    parser.add_argument("--songset", required=True, help="songset id to bridge")
    parser.add_argument("--out", required=True, help="output directory for this run")
    parser.add_argument(
        "--mode",
        choices=MODES,
        default="bridge",
        help="bridge = 8/16 s replacement holes; gap = the production 2-beat gap",
    )
    parser.add_argument(
        "--engine",
        choices=tuple(ENGINES),
        default="base",
        help="acestep-v15-base (default), acestep-v15-turbo, or sa3-medium",
    )
    parser.add_argument(
        "--align",
        choices=ALIGNMENTS,
        default="incoming",
        help="which side's recording key/tempo the bridge is generated in (ACE-Step only)",
    )
    parser.add_argument(
        "--takes", type=int, default=DEFAULT_TAKES, help="seeds per variant (default 3)"
    )
    parser.add_argument(
        "--context-seconds",
        type=float,
        default=DEFAULT_CONTEXT_SECONDS,
        help=(
            "seconds of arranged audio uploaded on each side of the hole; "
            "0 (default) uploads the entire arrangement"
        ),
    )
    parser.add_argument(
        "--steps",
        type=int,
        default=0,
        help="0 = engine default (base 64, turbo 8, SA3 8); otherwise override",
    )
    parser.add_argument(
        "--no-reference",
        action="store_true",
        help="skip building/uploading the 30 s reference (ACE-Step only)",
    )
    parser.add_argument("--arcs", default=",".join(ARCS), help="comma-separated arcs")
    parser.add_argument(
        "--lengths", default=",".join(str(n) for n in LENGTHS), help="comma-separated hole lengths"
    )
    parser.add_argument(
        "--treatments", default=",".join(TREATMENTS), help="comma-separated treatments"
    )
    parser.add_argument("--seed", type=int, default=4242, help="base seed (take i uses seed+i)")
    parser.add_argument(
        "--force", action="store_true", help="regenerate takes even when clips exist"
    )
    parser.add_argument("--refresh-captions", action="store_true", help="re-author captions.json")
    parser.add_argument("--skip-intra", action="store_true", help="only generate boundary variants")
    parser.add_argument(
        "--dry-run", action="store_true", help="build montages/captions with zero Modal calls"
    )
    args = parser.parse_args(argv)
    if args.takes < 1:
        raise SystemExit(f"--takes must be >= 1 (got {args.takes})")
    return args


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
    for sub in ("montages", "clips", "bridges", "variants", "reference", "context"):
        (out_dir / sub).mkdir(parents=True, exist_ok=True)
    if args.mode == "gap":
        (out_dir / "fills").mkdir(parents=True, exist_ok=True)
        if args.align != "incoming":
            print("note: gap mode forces align=incoming", flush=True)
            args.align = "incoming"

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

    print(f"songset {args.songset} -> {out_dir} (mode {args.mode}, engine {args.engine})", flush=True)
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
    context_seconds = args.context_seconds or None
    if args.mode == "gap":
        seams: dict[str, Seam] = {"boundary": boundary}
        references = write_context_files(out_dir, seams, args.align, not args.no_reference)
        captions = load_captions(
            out_dir,
            caption_facts(boundary, None),
            args.refresh_captions,
            mode="gap",
            engine=args.engine,
        )
        geometry = gap_geometry(boundary)
        print(
            f"  gap {geometry['gap_seconds']}s = {geometry['fill_samples']} frames, "
            f"beat {geometry['beat_samples']} frames at "
            f"{boundary.detail['recording_tempo_right']} BPM",
            flush=True,
        )
        montage = build_montage(boundary, geometry["gap_seconds"], context_seconds)
        montage_rel = f"montages/gap__incoming__{args.engine}__{montage_key(montage)}.flac"
        write_flac(out_dir / montage_rel, montage.samples)
        montage_bytes = bytes_from_array(montage.samples)
        references_bytes = reference_bytes_for(
            out_dir, seams, args.align, args.engine, not args.no_reference
        )
        takes = plan_gap_takes(args)
        print(f"  {len(takes)} gap takes x {len(GAP_CORRECTIONS)} corrections", flush=True)
        client = None if args.dry_run else modal_client(args.engine)
        records = []
        for take in takes:
            records.extend(
                run_gap_take(
                    take,
                    boundary,
                    montage,
                    montage_rel,
                    montage_bytes,
                    captions["captions"],
                    out_dir,
                    client,
                    args.engine,
                    args.steps,
                    None if args.engine == "sa3" else references_bytes.get("boundary"),
                    args.force,
                    args.dry_run,
                )
            )
    else:
        intra = None if args.skip_intra else intra_seam(items, recordings, components, audio)
        seams = {"boundary": boundary}
        if intra is not None:
            seams["intra"] = intra
        references = write_context_files(out_dir, seams, args.align, not args.no_reference)
        captions = load_captions(
            out_dir,
            caption_facts(boundary, intra),
            args.refresh_captions,
            mode="bridge",
            engine=args.engine,
        )
        references_bytes = reference_bytes_for(
            out_dir, seams, args.align, args.engine, not args.no_reference
        )
        variants = plan_variants(args)
        print(f"  {len(variants)} variants x {args.takes} takes", flush=True)
        client = None if args.dry_run else modal_client(args.engine)
        records = []
        for variant in variants:
            seam = seams[variant.scope]
            caption = captions["captions"][variant.caption_key()]["caption"]
            montage = build_montage(seam, variant.length_seconds, context_seconds)
            montage_path = out_dir / "montages" / f"{variant.id}__{montage_key(montage)}.flac"
            write_flac(montage_path, montage.samples)
            montage_bytes = montage_path.read_bytes()
            for take_index in range(args.takes):
                seed = args.seed + take_index
                records.append(
                    run_take(
                        variant,
                        take_index,
                        seed,
                        seam,
                        montage,
                        montage_bytes,
                        caption,
                        out_dir,
                        client,
                        args.engine,
                        args.align,
                        args.steps,
                        references_bytes.get(seam.scope),
                        args.force,
                        args.dry_run,
                    )
                )

    wall = time.time() - started
    write_index(out_dir, args, seams, records, references, started_iso, wall)
    (out_dir / "report.html").write_text(report_html(seams, records, args, references))

    # A gap take makes one call and copies its meta onto all three correction
    # rows, so sum elapsed time over calls, not rows (`raw` and every bridge row).
    calls = [
        record
        for record in records
        if record.get("correction") in (None, "raw") and record.get("meta")
    ]
    gpu_seconds = sum(float(record["meta"].get("elapsed_seconds") or 0.0) for record in calls)
    ok = [record for record in records if record["status"] == "ok"]
    unavailable = [record for record in records if record["status"] == "unavailable"]
    failed = [record for record in records if record["status"] == "failed"]
    print(
        f"done: {len(ok)} ok, {len(unavailable)} unavailable, {len(failed)} failed, "
        f"{len(records)} records in {wall:.1f}s (gpu {gpu_seconds:.1f}s across "
        f"{len(calls)} calls)",
        flush=True,
    )
    for record in failed:
        print(f"  failed {record['id']}: {record['error']}", flush=True)
    for record in unavailable:
        print(f"  unavailable {record['id']}: {record['error']}", flush=True)
    if failed and not ok:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
