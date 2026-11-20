"""Seam Fill POC run2 — one 1.393 s gap, DSP baselines vs Stable Audio 3 vs ACE-Step.

Run1 asked ACE-Step to invent 8-16 s of new music per seam and produced nothing
that resembled either recording. Run2 shrinks the unit of work to the production
seam: the gap a rendered Songset actually leaves between two Songs
(`gap_ms = round(gap_beats * 60000 / incoming_bpm)`), and asks the models for a
drum fill only. Real-audio DSP baselines are the control: if a Tail Extension or
the incoming song's own pickup beats every generated fill, the honest answer is
that this seam should not be generated.

Seam (songset `hbWfCccGxN_JLQGBEqmdu`, item 0 -> item 1, read live from the DB):

    head       A[a_end-15 : a_end-8]    7 s  untouched
    a_part     A[a_end-8  : a_end]      8 s  untouched
    hole       zeros(1.393 s)                replaced by the fill
    extension  A[a_end    : a_end+1.393]     real audio past A's arrangement cut
    b_part     B[b_start  : b_start+8]  8 s  untouched
    tail       B[b_start+8: b_start+15] 7 s  untouched

Montage uploaded to the models: `a_part | hole | b_part` (17.393 s).
Listenable clip: `head | a_part | fill | b_part | tail` (31.393 s).

Outputs under `--out`:

    cache/          downloaded mp3s + decoded 44.1 kHz wavs (reused across runs)
    prompts.json    the four drum prompts; edit it or pass --refresh-prompts
    montages/       what was uploaded (generated variants only)
    fills/          only the fill audio (every variant except dsp__butt-splice)
    clips/          the listenable splice
    variants/       per-variant record (idempotency sidecar)
    index.json      every prompt, seed, parameter and measured metric
    report.html     self-contained listening page

Usage:
    uv run --project lab/poc-scripts --extra audio_inpaint \\
        python lab/poc-scripts/gen_seam_fill.py \\
        --songset hbWfCccGxN_JLQGBEqmdu --out lab/poc-scripts/output/audio-inpaint/run2

Prerequisites: `SOW_DATABASE_URL`, `SOW_R2_BUCKET`, `SOW_R2_ENDPOINT_URL`,
`SOW_R2_ACCESS_KEY_ID`, `SOW_R2_SECRET_ACCESS_KEY`, `MODAL_TOKEN_ID`,
`MODAL_TOKEN_SECRET`; `ffmpeg` on PATH.
"""

from __future__ import annotations

import argparse
import html
import json
import math
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import numpy as np
import scipy.signal

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gen_audio_inpaint import (  # noqa: E402  (same directory; reused helpers)
    SR,
    Component,
    Recording,
    SongsetItem,
    apply_join_fades,
    decode_flac_bytes,
    ensure_source_audio,
    fetch_songset,
    fit_length,
    gain_ramp,
    keyscale,
    peak_level,
    rms_db,
    seconds_to_samples,
    slice_window,
    write_flac,
)

DEFAULT_SONGSET = "hbWfCccGxN_JLQGBEqmdu"
SA3_APP = "sow-sa3-poc"
# `Sa3Inpaint` runs on the first GPU Modal has free; `Sa3InpaintCpu` is the same
# method in fp32 on CPU. A pinned/`any` GPU request sat Pending in this workspace,
# so the class is selectable and the device lands in `meta.device`.
SA3_CLASS = os.environ.get("SOW_SA3_CLASS", "Sa3Inpaint")
ACESTEP_APP = "sow-acestep-poc"
ACESTEP_TURBO_CLASS = "AceStepRepaint"
ACESTEP_BASE_CLASS = "AceStepSeamBase"

# Seam geometry. The hole is the gap the render itself leaves: 2 beats of the
# *incoming* song's tempo (audio_engine.calculate_gap_ms), never a component BPM.
SIDE_SECONDS = 8.0
HEAD_SECONDS = 7.0
GAP_BEATS = 2
GAP_MS = 1393
FILL_FADE_SECONDS = 0.01
JOIN_FADE_SECONDS = 0.005
TAIL_FADE_SECONDS = 0.05
PEAK_CEILING = 0.98
# Below this, a generated fill is treated as silent (no centroid/correlation).
SILENCE_DB_FLOOR = -60.0
# The plan derived its windows from a live arrangement cut of 71.85 s; the DB has
# since re-analysed item 0 (its last arrangement step is now 11.85-40.55 s). The
# seam is still read from the live DB, and a mismatch is logged, not fatal.
EXPECTED_A_END_SECONDS = 71.85

# Stable Audio 3
SA3_ENGINE = "sa3-small-music"
SA3_STEPS = 8
# ACE-Step
TURBO_ENGINE = "acestep-v15-turbo"
BASE_ENGINE = "acestep-v15-base"
DSP_ENGINE = "dsp"
INSTRUMENTAL_LYRICS = "[Instrumental]"
# Reference audio the ACE-Step handler crops 3 x 10 s from. Exactly 30.0 s at
# 48 kHz (1,440,000 frames) is the only length whose crop is deterministic.
REFERENCE_SECONDS = 10.0
REFERENCE_TOTAL_SECONDS = 30.0
REFERENCE_SR = 48000
REFERENCE_SAMPLES = int(REFERENCE_TOTAL_SECONDS * REFERENCE_SR)  # 1440000

CALL_ATTEMPTS = 2
CALL_BACKOFF_SECONDS = 5.0

SA3_PROMPTS: dict[str, str] = {
    "drum-a": (
        "TrackType: Music, VocalType: Instrumental, Genre: Worship, a short live acoustic drum kit "
        "fill with floor toms and snare rolling into a crash cymbal on the downbeat, no melodic "
        "instruments, no vocals, 86 BPM"
    ),
    "drum-b": (
        "TrackType: Instrument, solo drum kit, a punchy two-beat fill with floor toms and snare, "
        "recorded live in a church room, no other instruments, no vocals, 86 BPM"
    ),
}
ACESTEP_CAPTIONS: dict[str, str] = {
    "drum-a": (
        "solo live acoustic drum kit fill, floor toms and snare rolling into a crash cymbal, two "
        "beats, no melodic instruments, no piano, no guitar, no vocals, seamless lead into the "
        "next section"
    ),
    "drum-b": (
        "acoustic drum kit, punchy two-beat tom and snare fill, live church room, no other "
        "instruments, no vocals"
    ),
}
STYLES = ("drum-a", "drum-b")


# ---------------------------------------------------------------------------
# Seam
# ---------------------------------------------------------------------------


def gap_samples() -> int:
    return seconds_to_samples(GAP_MS / 1000.0)


def seam_facts(
    items: list[SongsetItem],
    recordings: dict[str, Recording],
    components: dict[str, list[Component]],
) -> dict[str, Any]:
    """Read the seam from the live DB and assert the geometry the run depends on."""
    a_item, b_item = items[0], items[1]
    if not a_item.steps or not b_item.steps:
        raise RuntimeError("arrangement_steps missing for a boundary item")
    a_end = a_item.last_step_end()
    b_start = b_item.first_step_start()
    a_rec = recordings[a_item.hash_prefix]
    b_rec = recordings[b_item.hash_prefix]
    if not b_rec.tempo_bpm:
        raise RuntimeError(
            f"no tempo for incoming recording {b_rec.hash_prefix}; cannot size the gap"
        )
    expected_ms = round(GAP_BEATS * 60000.0 / b_rec.tempo_bpm)
    if expected_ms != GAP_MS:
        raise RuntimeError(
            f"GAP_MS constant ({GAP_MS}) does not match {GAP_BEATS} beats at "
            f"{b_rec.tempo_bpm} BPM ({expected_ms}); the seam data changed"
        )
    if b_start <= 0.0:
        raise RuntimeError("incoming item starts at 0 s; the seam needs audio before it")
    if a_end < SIDE_SECONDS + HEAD_SECONDS:
        raise RuntimeError(
            f"outgoing arrangement ends at {a_end}s; need >= {SIDE_SECONDS + HEAD_SECONDS}s "
            "of audio before it"
        )
    gap = GAP_MS / 1000.0
    a_seconds = a_rec.duration_seconds
    if a_end + gap > a_seconds + 1e-6:
        raise RuntimeError(
            f"outgoing recording is {a_seconds}s; the {gap}s extension past {a_end}s "
            "does not exist (recording re-fetched?)"
        )
    if b_start + SIDE_SECONDS + HEAD_SECONDS > b_rec.duration_seconds + 1e-6:
        raise RuntimeError("incoming recording too short for the tail window")
    pickup_end = pick_entry_start(components, b_rec.content_hash)
    if pickup_end is None:
        raise RuntimeError(
            f"incoming recording {b_item.hash_prefix} has no entry component; the pickup "
            "window (its own 2 beats into the entry section) cannot be placed"
        )
    if pickup_end - gap < 0.0:
        raise RuntimeError(f"pickup window {pickup_end - gap}..{pickup_end} starts before 0 s")
    facts = {
        "a_hash": a_item.hash_prefix,
        "b_hash": b_item.hash_prefix,
        "a_song": a_item.title,
        "b_song": b_item.title,
        "a_end_seconds": round(a_end, 3),
        "b_start_seconds": round(b_start, 3),
        "gap_ms": GAP_MS,
        "gap_seconds": gap,
        "a_tempo_bpm": a_rec.tempo_bpm,
        "b_tempo_bpm": b_rec.tempo_bpm,
        "a_key": keyscale(a_rec.musical_key, a_rec.musical_mode),
        "b_key": keyscale(b_rec.musical_key, b_rec.musical_mode),
        "b_bpm_int": int(round(b_rec.tempo_bpm)),
        "pickup_start_seconds": round(pickup_end - gap, 3),
        "pickup_end_seconds": round(pickup_end, 3),
    }
    print(
        f"seam: a_end={a_end:.2f}s b_start={b_start:.2f}s gap_ms={GAP_MS} "
        f"({GAP_BEATS} beats @ {b_rec.tempo_bpm}) "
        f"{facts['a_key']} -> {facts['b_key']}",
        flush=True,
    )
    if abs(a_end - EXPECTED_A_END_SECONDS) > 1e-6:
        print(
            f"  warn: outgoing arrangement ends at {a_end:.2f}s, not the "
            f"{EXPECTED_A_END_SECONDS}s the plan recorded; windows are relative to the live value",
            flush=True,
        )
    print(
        f"  pickup: B[{facts['pickup_start_seconds']}:{facts['pickup_end_seconds']}] "
        f"(its own {GAP_BEATS} beats into the entry section)",
        flush=True,
    )
    return facts


def pick_entry_start(
    components: dict[str, list[Component]], content_hash: Optional[str]
) -> Optional[float]:
    """Start of the incoming song's entry component — its pickup ends here.

    `fetch_songset` keys components by recording content hash, and `role` is the
    same rule `boundary_seam` uses to find the incoming item's entry.
    """
    for component in components.get(content_hash or "", []):
        if component.role == "entry":
            return component.start_time
    return None


def windows(
    a_audio: np.ndarray, b_audio: np.ndarray, facts: dict[str, Any]
) -> dict[str, np.ndarray]:
    a_end = facts["a_end_seconds"]
    b_start = facts["b_start_seconds"]
    gap = facts["gap_seconds"]
    head = slice_window(a_audio, a_end - SIDE_SECONDS - HEAD_SECONDS, a_end - SIDE_SECONDS)
    a_part = slice_window(a_audio, a_end - SIDE_SECONDS, a_end)
    extension = slice_window(a_audio, a_end, a_end + gap)
    b_part = slice_window(b_audio, b_start, b_start + SIDE_SECONDS)
    tail = slice_window(b_audio, b_start + SIDE_SECONDS, b_start + SIDE_SECONDS + HEAD_SECONDS)
    pickup = slice_window(b_audio, facts["pickup_start_seconds"], facts["pickup_end_seconds"])
    hole = np.zeros((gap_samples(), 2), dtype=np.float32)
    for name, array, seconds in (
        ("head", head, HEAD_SECONDS),
        ("a_part", a_part, SIDE_SECONDS),
        ("extension", extension, gap),
        ("b_part", b_part, SIDE_SECONDS),
        ("tail", tail, HEAD_SECONDS),
        ("pickup", pickup, gap),
        ("hole", hole, gap),
    ):
        expected = seconds_to_samples(seconds)
        if array.shape[0] != expected:
            raise RuntimeError(f"{name} window has {array.shape[0]} samples, expected {expected}")
    return {
        "head": head,
        "a_part": a_part,
        "extension": extension,
        "b_part": b_part,
        "tail": tail,
        "pickup": pickup,
        "hole": hole,
    }


def montage_of(parts: dict[str, np.ndarray]) -> tuple[np.ndarray, int, int]:
    a_part = parts["a_part"]
    hole = parts["hole"]
    samples = np.ascontiguousarray(
        np.concatenate([a_part, hole, parts["b_part"]], axis=0), dtype=np.float32
    )
    hole_start = a_part.shape[0]
    return samples, hole_start, hole_start + hole.shape[0]


def reference_montage(
    a_audio: np.ndarray, b_audio: np.ndarray, facts: dict[str, Any]
) -> np.ndarray:
    """3 x 10 s at 48 kHz, exactly 1,440,000 frames (deterministic server-side crop)."""
    a_end = facts["a_end_seconds"]
    b_start = facts["b_start_seconds"]
    segments = [
        slice_window(a_audio, a_end - REFERENCE_SECONDS, a_end),
        slice_window(b_audio, b_start, b_start + REFERENCE_SECONDS),
        slice_window(
            b_audio,
            facts["pickup_end_seconds"] - REFERENCE_SECONDS,
            facts["pickup_end_seconds"],
        ),
    ]
    joined = np.concatenate(segments, axis=0).astype(np.float64)
    up, down = REFERENCE_SR, SR
    resampled = scipy.signal.resample_poly(joined, up, down, axis=0).astype(np.float32)
    resampled, corrected = fit_length(resampled, REFERENCE_SAMPLES)
    if resampled.shape[0] != REFERENCE_SAMPLES:
        raise RuntimeError(
            f"reference has {resampled.shape[0]} frames, expected {REFERENCE_SAMPLES}"
        )
    if corrected:
        print(
            f"  note: reference resampling landed {REFERENCE_SAMPLES} frames after a pad/trim",
            flush=True,
        )
    return np.ascontiguousarray(resampled, dtype=np.float32)


def write_flac_at(path: Path, samples: np.ndarray, sample_rate: int) -> None:
    import soundfile as sf

    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), samples, sample_rate, format="FLAC", subtype="PCM_16")


def reference_bytes(reference: np.ndarray, workdir: Path) -> bytes:
    path = workdir / "reference.flac"
    write_flac_at(path, reference, REFERENCE_SR)
    data = path.read_bytes()
    import soundfile as sf

    info = sf.info(str(path))
    if info.samplerate != REFERENCE_SR or info.frames != REFERENCE_SAMPLES:
        raise RuntimeError(
            f"encoded reference is {info.frames} frames at {info.samplerate} Hz; "
            f"the server's reference crop is only deterministic at {REFERENCE_SAMPLES} "
            f"frames at {REFERENCE_SR} Hz"
        )
    return data


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------


def load_prompts(out_dir: Path, refresh: bool) -> dict[str, dict[str, str]]:
    path = out_dir / "prompts.json"
    payload: dict[str, Any] = {}
    if path.exists() and not refresh:
        payload = json.loads(path.read_text())
    changed = False
    for key, defaults in (("sa3", SA3_PROMPTS), ("acestep", ACESTEP_CAPTIONS)):
        current = payload.get(key)
        if not isinstance(current, dict):
            current = {}
            changed = True
        for style, text in defaults.items():
            if not current.get(style):
                current[style] = text
                changed = True
        payload[key] = current
    payload.setdefault("version", 1)
    if changed or not path.exists():
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
        print(f"  wrote {path}", flush=True)
    return {"sa3": payload["sa3"], "acestep": payload["acestep"]}


# ---------------------------------------------------------------------------
# Variant plan
# ---------------------------------------------------------------------------


def plan_variants(args, seed_base: int) -> list[dict[str, Any]]:
    variants: list[dict[str, Any]] = []
    if not args.skip_dsp:
        for variant_id in (
            "dsp__current-silence",
            "dsp__butt-splice",
            "dsp__tail-extension",
            "dsp__b-pickup",
        ):
            variants.append({"id": variant_id, "engine": DSP_ENGINE, "style": None, "seed": None})
    if not args.skip_sa3:
        for style in STYLES:
            for offset in range(1, 4):
                seed = seed_base + offset
                variants.append(
                    {
                        "id": f"sa3__{style}__s{seed}",
                        "engine": SA3_ENGINE,
                        "style": style,
                        "seed": seed,
                    }
                )
    if not args.skip_acestep:
        for engine, prefix, steps, adg, shift, class_seed in (
            (TURBO_ENGINE, "acestep-turbo", 8, False, 3.0, 101),
            (BASE_ENGINE, "acestep-base", 32, True, 1.0, 201),
        ):
            for index, style in enumerate(STYLES):
                seed = seed_base + class_seed + index
                variants.append(
                    {
                        "id": f"{prefix}__{style}__s{seed}",
                        "engine": engine,
                        "style": style,
                        "seed": seed,
                        "inference_steps": steps,
                        "use_adg": adg,
                        "shift": shift,
                    }
                )
    return variants


# ---------------------------------------------------------------------------
# Modal clients
# ---------------------------------------------------------------------------


def modal_client(app: str, cls: str):
    import modal

    return modal.Cls.from_name(app, cls)()


def call_with_retry(fn, *args) -> dict[str, Any]:
    last_error: Optional[Exception] = None
    for attempt in range(1, CALL_ATTEMPTS + 1):
        try:
            return fn(*args)
        except Exception as exc:  # noqa: BLE001 — one retry, then fail the variant
            last_error = exc
            print(f"    attempt {attempt}/{CALL_ATTEMPTS} failed: {exc}", flush=True)
            if attempt < CALL_ATTEMPTS:
                time.sleep(CALL_BACKOFF_SECONDS)
    raise RuntimeError(f"modal call failed after {CALL_ATTEMPTS} attempts: {last_error}")


def encode_flac_bytes(samples: np.ndarray) -> bytes:
    with tempfile.TemporaryDirectory(prefix="seam-fill-encode-") as tmp:
        path = Path(tmp) / "upload.flac"
        write_flac(path, samples)
        return path.read_bytes()


# ---------------------------------------------------------------------------
# Assembly + metrics
# ---------------------------------------------------------------------------


def apply_edge_fades(clip: np.ndarray, index: int, fade: int) -> None:
    """Equal-power fade-out ending at *index* and fade-in starting there, in place."""
    if index - fade < 0 or index + fade > clip.shape[0]:
        raise RuntimeError(
            f"cannot fade at sample {index} (fade {fade}) in {clip.shape[0]} samples"
        )
    ramp = np.linspace(0.0, 1.0, fade, endpoint=False, dtype=np.float32)[:, None]
    clip[index - fade : index] *= np.cos(ramp * (np.pi / 2.0))
    clip[index : index + fade] *= np.sin(ramp * (np.pi / 2.0))


def apply_out_fade(clip: np.ndarray, end: int, fade: int) -> None:
    ramp = np.linspace(0.0, 1.0, fade, endpoint=False, dtype=np.float32)[:, None]
    clip[end - fade : end] *= np.cos(ramp * (np.pi / 2.0))


def rnd(value: Optional[float], digits: int = 2) -> Optional[float]:
    if value is None:
        return None
    value = float(value)
    if not math.isfinite(value):
        return None
    return round(value, digits)


def measure(
    fill: Optional[np.ndarray],
    clip: np.ndarray,
    a_level: float,
    b_level: float,
    peak_limited: bool,
) -> dict[str, Any]:
    import librosa

    metrics: dict[str, Any] = {
        "a_level_db": rnd(a_level),
        "b_level_db": rnd(b_level),
        "fill_rms_db": None,
        "fill_peak": None,
        "fill_spectral_centroid_hz": None,
        "fill_stereo_corr": None,
        "fill_onset_density_per_s": None,
        "fill_duration_seconds": None,
        "clip_duration_seconds": rnd(clip.shape[0] / SR, 3),
        "peak": rnd(peak_level(clip), 4),
        "peak_limited": peak_limited,
    }
    if fill is None or fill.shape[0] == 0:
        return metrics
    seconds = fill.shape[0] / SR
    metrics["fill_duration_seconds"] = rnd(seconds, 3)
    metrics["fill_peak"] = rnd(peak_level(fill), 4)
    level = rms_db(fill)
    metrics["fill_rms_db"] = rnd(level)
    if not np.isfinite(level) or level <= SILENCE_DB_FLOOR:
        return metrics
    mono = fill.mean(axis=1)
    metrics["fill_spectral_centroid_hz"] = rnd(
        float(librosa.feature.spectral_centroid(y=mono, sr=SR, n_fft=1024).mean()), 1
    )
    if float(np.std(fill[:, 0])) > 0.0 and float(np.std(fill[:, 1])) > 0.0:
        metrics["fill_stereo_corr"] = rnd(float(np.corrcoef(fill[:, 0], fill[:, 1])[0, 1]), 3)
    onsets = librosa.onset.onset_detect(y=mono, sr=SR)
    metrics["fill_onset_density_per_s"] = rnd(len(onsets) / seconds, 2)
    return metrics


def levels(parts: dict[str, np.ndarray]) -> tuple[float, float]:
    a_level = rms_db(parts["a_part"][-seconds_to_samples(1.0) :])
    b_level = rms_db(parts["b_part"][: seconds_to_samples(1.0)])
    return a_level, b_level


def build_clip(
    parts: dict[str, np.ndarray],
    fill: np.ndarray,
    fill_fade_seconds: float,
    tail_fade: bool = False,
    join_fades: bool = True,
) -> tuple[np.ndarray, np.ndarray]:
    """Splice head | a_part | fill | b_part | tail, returning the clip and the written fill.

    The returned fill is the clip's own fill region, so `fills/<id>.flac` carries
    exactly the audio that plays in the gap (including its edge fades) instead of
    a differently-faded copy.
    """
    body = np.ascontiguousarray(
        np.concatenate([parts["a_part"], fill, parts["b_part"]], axis=0), dtype=np.float32
    )
    clip = np.ascontiguousarray(
        np.concatenate([parts["head"], body, parts["tail"]], axis=0), dtype=np.float32
    )
    head_len = parts["head"].shape[0]
    fill_start = head_len + parts["a_part"].shape[0]
    fill_end = fill_start + fill.shape[0]
    if fill_fade_seconds > 0.0:
        apply_edge_fades(clip, fill_start, seconds_to_samples(fill_fade_seconds))
        apply_edge_fades(clip, fill_end, seconds_to_samples(fill_fade_seconds))
    if tail_fade:
        apply_out_fade(clip, fill_end, seconds_to_samples(TAIL_FADE_SECONDS))
    if join_fades:
        apply_join_fades(clip, (head_len, head_len + body.shape[0]))
    return clip, np.ascontiguousarray(clip[fill_start:fill_end], dtype=np.float32)


def gain_fill(fill: np.ndarray, a_level: float, b_level: float) -> tuple[np.ndarray, bool]:
    """Put the fill on the seam's average level, tilted from A's level to B's."""
    target = 0.5 * (a_level + b_level)
    level = rms_db(fill)
    if not np.isfinite(level) or not np.isfinite(target):
        return fill, False
    base_gain = 10.0 ** ((target - level) / 20.0)
    tilt = gain_ramp(fill.shape[0], a_level - target, b_level - target)
    out = np.ascontiguousarray(fill * (base_gain * tilt)[:, None], dtype=np.float32)
    peak_limited = False
    peak = peak_level(out)
    if peak > PEAK_CEILING:
        out = np.ascontiguousarray(out * (PEAK_CEILING / peak), dtype=np.float32)
        peak_limited = True
    return out, peak_limited


# ---------------------------------------------------------------------------
# Variant execution
# ---------------------------------------------------------------------------


def write_variant_outputs(
    out_dir: Path,
    variant: dict[str, Any],
    fill: Optional[np.ndarray],
    clip: np.ndarray,
) -> None:
    write_flac(out_dir / "clips" / f"{variant['id']}.flac", clip)
    if fill is not None:
        write_flac(out_dir / "fills" / f"{variant['id']}.flac", fill)


def record_base(
    variant: dict[str, Any],
    prompts: dict[str, dict[str, str]],
) -> dict[str, Any]:
    engine = variant["engine"]
    prompt = None
    if engine == SA3_ENGINE:
        prompt = prompts["sa3"][variant["style"]]
    elif engine in (TURBO_ENGINE, BASE_ENGINE):
        prompt = prompts["acestep"][variant["style"]]
    return {
        "id": variant["id"],
        "engine": engine,
        "status": "pending",
        "prompt": prompt,
        "seed": variant["seed"],
        "clip": f"clips/{variant['id']}.flac",
        "fill": None,
        "montage": None,
        "request": None,
        "meta": None,
        "elapsed_seconds": None,
        "metrics": {},
        "length_corrected": False,
        "error": None,
    }


def sidecar_is_done(out_dir: Path, variant_id: str) -> bool:
    sidecar = out_dir / "variants" / f"{variant_id}.json"
    clip = out_dir / "clips" / f"{variant_id}.flac"
    if not sidecar.exists() or not clip.exists():
        return False
    try:
        previous = json.loads(sidecar.read_text())
    except json.JSONDecodeError:
        return False
    return previous.get("status") == "ok"


def finish(out_dir: Path, record: dict[str, Any]) -> dict[str, Any]:
    sidecar = out_dir / "variants" / f"{record['id']}.json"
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n")
    return record


def run_dsp(
    variant: dict[str, Any],
    parts: dict[str, np.ndarray],
    out_dir: Path,
    prompts: dict[str, dict[str, str]],
    force: bool,
) -> dict[str, Any]:
    record = record_base(variant, prompts)
    if sidecar_is_done(out_dir, variant["id"]) and not force:
        print(f"  skip {variant['id']} (clip exists; --force to regenerate)", flush=True)
        return json.loads((out_dir / "variants" / f"{variant['id']}.json").read_text())

    a_level, b_level = levels(parts)
    variant_id = variant["id"]
    tail_fade = False
    fill_fade = FILL_FADE_SECONDS
    if variant_id == "dsp__current-silence":
        fill: Optional[np.ndarray] = parts["hole"].copy()
        fill_fade = 0.0
    elif variant_id == "dsp__butt-splice":
        # No fill at all: A's arranged material is butted against B's first downbeat.
        fill = None
        clip = np.ascontiguousarray(
            np.concatenate(
                [parts["head"], parts["a_part"], parts["b_part"], parts["tail"]], axis=0
            ),
            dtype=np.float32,
        )
        head_len = parts["head"].shape[0]
        # Three joins: head -> a_part, the A|B splice itself (the one that needs
        # de-clicking), and b_part -> tail.
        apply_join_fades(
            clip,
            (
                head_len,
                head_len + parts["a_part"].shape[0],
                head_len + parts["a_part"].shape[0] + parts["b_part"].shape[0],
            ),
        )
        metrics = measure(None, clip, a_level, b_level, False)
        write_variant_outputs(out_dir, variant, None, clip)
        record.update({"status": "ok", "metrics": metrics, "fill": None, "montage": None})
        return finish(out_dir, record)
    elif variant_id == "dsp__tail-extension":
        fill = parts["extension"].copy()
        tail_fade = True
        fill_fade = 0.0  # contiguous with a_part at the arrangement cut
    elif variant_id == "dsp__b-pickup":
        fill = parts["pickup"].copy()
    else:
        raise RuntimeError(f"unknown dsp variant {variant_id}")

    clip, written_fill = build_clip(parts, fill, fill_fade, tail_fade=tail_fade)
    metrics = measure(written_fill, clip, a_level, b_level, False)
    write_variant_outputs(out_dir, variant, written_fill, clip)
    record.update(
        {
            "status": "ok",
            "metrics": metrics,
            "fill": f"fills/{variant_id}.flac",
            "montage": None,
        }
    )
    print(
        f"  dsp {variant_id}: fill {metrics['fill_duration_seconds']}s "
        f"{metrics['fill_rms_db']} dB, clip {metrics['clip_duration_seconds']}s",
        flush=True,
    )
    return finish(out_dir, record)


def build_request(
    variant: dict[str, Any],
    facts: dict[str, Any],
    prompts: dict[str, dict[str, str]],
    montage_seconds: float,
) -> dict[str, Any]:
    engine = variant["engine"]
    start = SIDE_SECONDS
    end = round(SIDE_SECONDS + facts["gap_seconds"], 3)
    if engine == SA3_ENGINE:
        return {
            "prompt": prompts["sa3"][variant["style"]],
            "seed": variant["seed"],
            "steps": SA3_STEPS,
            "start_seconds": start,
            "end_seconds": end,
            "duration_seconds": montage_seconds,
        }
    return {
        "caption": prompts["acestep"][variant["style"]],
        "lyrics": INSTRUMENTAL_LYRICS,
        "instrumental": True,
        "bpm": facts["b_bpm_int"],
        "keyscale": facts["b_key"],
        "duration": montage_seconds,
        "seed": variant["seed"],
        "repaint_start": start,
        "repaint_end": end,
        "vocal_language": "en",
        "inference_steps": variant["inference_steps"],
        "guidance_scale": 7.0,
        "use_adg": variant["use_adg"],
        "shift": variant["shift"],
        "repaint_wav_crossfade_sec": 0.05,
        "repaint_latent_crossfade_frames": 10,
    }


def run_generated(
    variant: dict[str, Any],
    parts: dict[str, np.ndarray],
    montage: tuple[np.ndarray, int, int],
    out_dir: Path,
    facts: dict[str, Any],
    prompts: dict[str, dict[str, str]],
    clients: dict[str, Any],
    reference: bytes,
    montage_bytes: bytes,
    force: bool,
    dry_run: bool,
) -> dict[str, Any]:
    record = record_base(variant, prompts)
    variant_id = variant["id"]
    samples, hole_start, hole_end = montage
    montage_seconds = samples.shape[0] / SR
    request = build_request(variant, facts, prompts, montage_seconds)
    record["request"] = request

    if sidecar_is_done(out_dir, variant_id) and not force:
        print(f"  skip {variant_id} (clip exists; --force to regenerate)", flush=True)
        return json.loads((out_dir / "variants" / f"{variant_id}.json").read_text())

    write_flac(out_dir / "montages" / f"{variant_id}.flac", samples)
    record["montage"] = f"montages/{variant_id}.flac"

    if dry_run:
        record["status"] = "dry_run"
        record["metrics"] = {
            "montage_duration_seconds": rnd(montage_seconds, 3),
            "hole_samples": hole_end - hole_start,
        }
        return finish(out_dir, record)

    a_level, b_level = levels(parts)
    try:
        started = time.time()
        client = clients[variant["engine"]]
        if variant["engine"] == SA3_ENGINE:
            response = call_with_retry(
                client.inpaint.remote, montage_bytes, f"{variant_id}-montage.flac", request
            )
        else:
            response = call_with_retry(
                client.repaint.remote,
                montage_bytes,
                f"{variant_id}-montage.flac",
                request,
                reference,
            )
        record["elapsed_seconds"] = round(time.time() - started, 3)

        with tempfile.TemporaryDirectory(prefix=f"seam-fill-{variant_id}-") as tmp:
            returned = decode_flac_bytes(response["audio"], Path(tmp), variant_id)
        returned_samples = int(returned.shape[0])
        returned, corrected = fit_length(returned, samples.shape[0])
        record["length_corrected"] = corrected
        record["returned_samples"] = returned_samples
        record["meta"] = response.get("meta") or {}

        fill = np.ascontiguousarray(returned[hole_start:hole_end], dtype=np.float32)
        if fill.shape[0] != hole_end - hole_start:
            raise RuntimeError(
                f"fill has {fill.shape[0]} samples, expected {hole_end - hole_start}"
            )
        level = rms_db(fill)
        if not np.isfinite(level) or peak_level(fill) < 0.001:
            raise RuntimeError(
                f"model returned a silent fill (rms {level} dB, peak {peak_level(fill)})"
            )

        fill, peak_limited = gain_fill(fill, a_level, b_level)
        clip, written_fill = build_clip(parts, fill, FILL_FADE_SECONDS)
        metrics = measure(written_fill, clip, a_level, b_level, peak_limited)
        write_variant_outputs(out_dir, variant, written_fill, clip)
        record["metrics"] = metrics
        record["fill"] = f"fills/{variant_id}.flac"
        record["status"] = "ok"
        print(
            f"  {variant_id}: fill {metrics['fill_rms_db']} dB (A {metrics['a_level_db']} / "
            f"B {metrics['b_level_db']}), peak {metrics['peak']}, "
            f"{record['elapsed_seconds']:.1f}s",
            flush=True,
        )
    except Exception as exc:  # noqa: BLE001 — one bad variant must not kill the run
        record["status"] = "failed"
        record["error"] = f"{type(exc).__name__}: {exc}"
        print(f"  {variant_id} FAILED: {record['error']}", flush=True)

    return finish(out_dir, record)


# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------


def write_index(
    out_dir: Path,
    facts: dict[str, Any],
    records: list[dict[str, Any]],
    started_iso: str,
) -> None:
    payload = {
        "version": 2,
        "songset_id": facts["songset_id"],
        "created_at": started_iso,
        "seam": {
            "a_hash": facts["a_hash"],
            "b_hash": facts["b_hash"],
            "a_end_seconds": facts["a_end_seconds"],
            "b_start_seconds": facts["b_start_seconds"],
            "gap_ms": facts["gap_ms"],
            "gap_seconds": facts["gap_seconds"],
            "a_tempo_bpm": facts["a_tempo_bpm"],
            "b_tempo_bpm": facts["b_tempo_bpm"],
            "a_key": facts["a_key"],
            "b_key": facts["b_key"],
        },
        "models": {
            "sa3": SA3_ENGINE,
            "acestep_turbo": TURBO_ENGINE,
            "acestep_base": BASE_ENGINE,
        },
        "variants": records,
    }
    (out_dir / "index.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


SECTIONS = (
    ("DSP baselines (real audio)", (DSP_ENGINE,)),
    ("Stable Audio 3 Small-Music", (SA3_ENGINE,)),
    ("ACE-Step 1.5 turbo", (TURBO_ENGINE,)),
    ("ACE-Step 1.5 base", (BASE_ENGINE,)),
)


def metric_line(record: dict[str, Any]) -> str:
    metrics = record.get("metrics") or {}
    candidates = [
        ("fill duration", metrics.get("fill_duration_seconds"), "s"),
        ("fill RMS", metrics.get("fill_rms_db"), "dB"),
        ("fill peak", metrics.get("fill_peak"), ""),
        ("centroid", metrics.get("fill_spectral_centroid_hz"), "Hz"),
        ("stereo corr", metrics.get("fill_stereo_corr"), ""),
        ("onsets", metrics.get("fill_onset_density_per_s"), "/s"),
        ("A", metrics.get("a_level_db"), "dB"),
        ("B", metrics.get("b_level_db"), "dB"),
        ("clip", metrics.get("clip_duration_seconds"), "s"),
        ("peak", metrics.get("peak"), ""),
    ]
    bits = [
        f"{label} {value} {unit}".rstrip() for label, value, unit in candidates if value is not None
    ]
    if metrics.get("peak_limited"):
        bits.append("peak-limited")
    return " · ".join(bits)


def report_html(records: list[dict[str, Any]], facts: dict[str, Any]) -> str:
    title = "Seam Fill POC run2 — drum fill for one 1.393 s gap"
    subtitle = (
        f"{facts['a_song']} ({facts['a_key']}, {facts['a_tempo_bpm']} BPM) -> "
        f"{facts['b_song']} ({facts['b_key']}, {facts['b_tempo_bpm']} BPM) · "
        f"gap {GAP_BEATS} beats = {facts['gap_seconds']:.3f} s · "
        f"A extended from {facts['a_end_seconds']:.2f} s"
    )
    sections: list[str] = []
    for heading, engines in SECTIONS:
        group = [r for r in records if r["engine"] in engines]
        if not group:
            continue
        cards: list[str] = []
        for record in group:
            clip = html.escape(record["clip"])
            players = [
                f'<div class="player"><span>clip</span><audio controls preload="none" src="{clip}"></audio></div>'
            ]
            if record.get("fill"):
                players.append(
                    f'<div class="player"><span>fill</span>'
                    f'<audio controls preload="none" src="{html.escape(record["fill"])}"></audio></div>'
                )
            if record["status"] == "failed":
                players.append(
                    f'<p class="error">failed: {html.escape(str(record.get("error")))}</p>'
                )
            elif record["status"] == "dry_run":
                players.append('<p class="meta">dry run — nothing generated</p>')
            meta_bits = [html.escape(record["engine"])]
            if record["seed"] is not None:
                meta_bits.append(f"seed {record['seed']}")
            meta_bits.append(f"status {html.escape(record['status'])}")
            meta_bits.append(f"length_corrected {record['length_corrected']}")
            prompt_block = ""
            if record.get("prompt"):
                prompt_block = (
                    f"<details><summary>prompt</summary>"
                    f"<p>{html.escape(str(record['prompt']))}</p></details>"
                )
            cards.append(
                f"""
      <article class="card">
        <h3>{html.escape(record['id'])}</h3>
        <p class="meta">{' · '.join(meta_bits)}</p>
        <div class="players">{''.join(players)}</div>
        <p class="metrics">{html.escape(metric_line(record))}</p>
        {prompt_block}
      </article>"""
            )
        sections.append(
            f"<section><h2>{html.escape(heading)}</h2>"
            f'<div class="grid">{"".join(cards)}</div></section>'
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
  .player span {{ font-size: 11px; text-transform: uppercase; letter-spacing: .05em; color: #777; }}
  audio {{ width: 100%; }}
  .error {{ color: #b00; }}
  details {{ margin-top: 6px; font-size: 12px; }}
  summary {{ cursor: pointer; }}
</style>
</head>
<body>
  <h1>{html.escape(title)}</h1>
  <p class="sub">{html.escape(subtitle)}</p>
  <p class="sub">Each clip is 7 s of the outgoing song, its last 8 arranged seconds, the fill, the
  incoming song's first 8 s, then 7 s more. The fill player isolates just the gap (1.393 s).
  Metrics are recorded, not used for selection.</p>
  {''.join(sections)}
</body>
</html>
"""


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seam Fill POC run2 (drum fill for one gap)")
    parser.add_argument("--songset", default=DEFAULT_SONGSET, help="songset id")
    parser.add_argument("--out", required=True, help="output directory for this run")
    parser.add_argument("--seed", type=int, default=7000, help="seed base")
    parser.add_argument("--force", action="store_true", help="regenerate even when clips exist")
    parser.add_argument("--refresh-prompts", action="store_true", help="rewrite prompts.json")
    parser.add_argument("--skip-dsp", action="store_true", help="skip the DSP baselines")
    parser.add_argument("--skip-sa3", action="store_true", help="skip Stable Audio 3")
    parser.add_argument("--skip-acestep", action="store_true", help="skip both ACE-Step models")
    parser.add_argument("--dry-run", action="store_true", help="zero Modal calls")
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    out_dir = Path(args.out)
    for sub in ("clips", "fills", "montages", "variants", "cache"):
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
            f"{recording.musical_key}/{recording.musical_mode}",
            flush=True,
        )

    facts = seam_facts(items, recordings, components)
    facts["songset_id"] = args.songset

    audio = {
        items[0].hash_prefix: ensure_source_audio(items[0].hash_prefix, out_dir, connect_r2),
        items[1].hash_prefix: ensure_source_audio(items[1].hash_prefix, out_dir, connect_r2),
    }
    parts = windows(audio[items[0].hash_prefix], audio[items[1].hash_prefix], facts)
    montage = montage_of(parts)

    prompts = load_prompts(out_dir, args.refresh_prompts)
    variants = plan_variants(args, args.seed)
    print(f"  {len(variants)} variants, dry_run={args.dry_run}", flush=True)

    clients: dict[str, Any] = {}
    reference = b""
    montage_bytes = b""
    if not args.dry_run:
        if not args.skip_sa3:
            clients[SA3_ENGINE] = modal_client(SA3_APP, SA3_CLASS)
            print(f"  sa3 class: {SA3_CLASS}", flush=True)
        if not args.skip_acestep:
            clients[TURBO_ENGINE] = modal_client(ACESTEP_APP, ACESTEP_TURBO_CLASS)
            clients[BASE_ENGINE] = modal_client(ACESTEP_APP, ACESTEP_BASE_CLASS)
        reference_array = reference_montage(
            audio[items[0].hash_prefix], audio[items[1].hash_prefix], facts
        )
        workdir = Path(tempfile.mkdtemp(prefix="seam-fill-reference-"))
        reference = reference_bytes(reference_array, workdir)
        montage_bytes = encode_flac_bytes(montage[0])
        print(
            f"  reference: {REFERENCE_SAMPLES} frames @ {REFERENCE_SR} Hz, "
            f"montage: {montage[0].shape[0]} frames",
            flush=True,
        )

    records: list[dict[str, Any]] = []
    for variant in variants:
        if variant["engine"] == DSP_ENGINE:
            records.append(run_dsp(variant, parts, out_dir, prompts, args.force))
        else:
            records.append(
                run_generated(
                    variant,
                    parts,
                    montage,
                    out_dir,
                    facts,
                    prompts,
                    clients,
                    reference,
                    montage_bytes,
                    args.force,
                    args.dry_run,
                )
            )

    wall = time.time() - started
    write_index(out_dir, facts, records, started_iso)
    (out_dir / "report.html").write_text(report_html(records, facts))

    generated = [record for record in records if record["engine"] != DSP_ENGINE]
    ok = [record for record in records if record["status"] == "ok"]
    failed = [record for record in records if record["status"] == "failed"]
    dry = [record for record in records if record["status"] == "dry_run"]
    gpu_seconds = sum(float(record.get("elapsed_seconds") or 0.0) for record in records)
    print(
        f"done: {len(ok)} ok, {len(failed)} failed, {len(dry)} dry_run of {len(records)} "
        f"variants in {wall:.1f}s (modal {gpu_seconds:.1f}s)",
        flush=True,
    )
    for record in failed:
        print(f"  failed {record['id']}: {record['error']}", flush=True)
    if generated and all(record["status"] == "failed" for record in generated):
        print("  every generated variant failed", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Seam Fill POC run2 — one 1.393 s gap, DSP baselines vs Stable Audio 3 vs ACE-Step.

Run1 asked ACE-Step to invent 8-16 s of new music per seam and produced nothing
that resembled either recording. Run2 shrinks the unit of work to the production
seam: the gap a rendered Songset actually leaves between two Songs
(`gap_ms = round(gap_beats * 60000 / incoming_bpm)`), and asks the models for a
drum fill only. Real-audio DSP baselines are the control: if a Tail Extension or
the incoming song's own pickup beats every generated fill, the honest answer is
that this seam should not be generated.

Seam (songset `hbWfCccGxN_JLQGBEqmdu`, item 0 -> item 1, read live from the DB):

    head       A[a_end-15 : a_end-8]    7 s  untouched
    a_part     A[a_end-8  : a_end]      8 s  untouched
    hole       zeros(1.393 s)                replaced by the fill
    extension  A[a_end    : a_end+1.393]     real audio past A's arrangement cut
    b_part     B[b_start  : b_start+8]  8 s  untouched
    tail       B[b_start+8: b_start+15] 7 s  untouched

Montage uploaded to the models: `a_part | hole | b_part` (17.393 s).
Listenable clip: `head | a_part | fill | b_part | tail` (31.393 s).

Outputs under `--out`:

    cache/          downloaded mp3s + decoded 44.1 kHz wavs (reused across runs)
    prompts.json    the four drum prompts; edit it or pass --refresh-prompts
    montages/       what was uploaded (generated variants only)
    fills/          only the fill audio (every variant except dsp__butt-splice)
    clips/          the listenable splice
    variants/       per-variant record (idempotency sidecar)
    index.json      every prompt, seed, parameter and measured metric
    report.html     self-contained listening page

Usage:
    uv run --project lab/poc-scripts --extra audio_inpaint \\
        python lab/poc-scripts/gen_seam_fill.py \\
        --songset hbWfCccGxN_JLQGBEqmdu --out lab/poc-scripts/output/audio-inpaint/run2

Prerequisites: `SOW_DATABASE_URL`, `SOW_R2_BUCKET`, `SOW_R2_ENDPOINT_URL`,
`SOW_R2_ACCESS_KEY_ID`, `SOW_R2_SECRET_ACCESS_KEY`, `MODAL_TOKEN_ID`,
`MODAL_TOKEN_SECRET`; `ffmpeg` on PATH.
"""

from __future__ import annotations

import argparse
import html
import json
import math
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import numpy as np
import scipy.signal

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gen_audio_inpaint import (  # noqa: E402  (same directory; reused helpers)
    SR,
    Component,
    Recording,
    SongsetItem,
    apply_join_fades,
    decode_flac_bytes,
    ensure_source_audio,
    fetch_songset,
    fit_length,
    gain_ramp,
    keyscale,
    peak_level,
    rms_db,
    seconds_to_samples,
    slice_window,
    write_flac,
)

DEFAULT_SONGSET = "hbWfCccGxN_JLQGBEqmdu"
SA3_APP = "sow-sa3-poc"
# `Sa3Inpaint` runs on the first GPU Modal has free; `Sa3InpaintCpu` is the same
# method in fp32 on CPU. A pinned/`any` GPU request sat Pending in this workspace,
# so the class is selectable and the device lands in `meta.device`.
SA3_CLASS = os.environ.get("SOW_SA3_CLASS", "Sa3Inpaint")
ACESTEP_APP = "sow-acestep-poc"
ACESTEP_TURBO_CLASS = "AceStepRepaint"
ACESTEP_BASE_CLASS = "AceStepSeamBase"

# Seam geometry. The hole is the gap the render itself leaves: 2 beats of the
# *incoming* song's tempo (audio_engine.calculate_gap_ms), never a component BPM.
SIDE_SECONDS = 8.0
HEAD_SECONDS = 7.0
GAP_BEATS = 2
GAP_MS = 1393
FILL_FADE_SECONDS = 0.01
JOIN_FADE_SECONDS = 0.005
TAIL_FADE_SECONDS = 0.05
PEAK_CEILING = 0.98
# Below this, a generated fill is treated as silent (no centroid/correlation).
SILENCE_DB_FLOOR = -60.0
# The plan derived its windows from a live arrangement cut of 71.85 s; the DB has
# since re-analysed item 0 (its last arrangement step is now 11.85-40.55 s). The
# seam is still read from the live DB, and a mismatch is logged, not fatal.
EXPECTED_A_END_SECONDS = 71.85

# Stable Audio 3
SA3_ENGINE = "sa3-small-music"
SA3_STEPS = 8
# ACE-Step
TURBO_ENGINE = "acestep-v15-turbo"
BASE_ENGINE = "acestep-v15-base"
DSP_ENGINE = "dsp"
INSTRUMENTAL_LYRICS = "[Instrumental]"
# Reference audio the ACE-Step handler crops 3 x 10 s from. Exactly 30.0 s at
# 48 kHz (1,440,000 frames) is the only length whose crop is deterministic.
REFERENCE_SECONDS = 10.0
REFERENCE_TOTAL_SECONDS = 30.0
REFERENCE_SR = 48000
REFERENCE_SAMPLES = int(REFERENCE_TOTAL_SECONDS * REFERENCE_SR)  # 1440000

CALL_ATTEMPTS = 2
CALL_BACKOFF_SECONDS = 5.0

SA3_PROMPTS: dict[str, str] = {
    "drum-a": (
        "TrackType: Music, VocalType: Instrumental, Genre: Worship, a short live acoustic drum kit "
        "fill with floor toms and snare rolling into a crash cymbal on the downbeat, no melodic "
        "instruments, no vocals, 86 BPM"
    ),
    "drum-b": (
        "TrackType: Instrument, solo drum kit, a punchy two-beat fill with floor toms and snare, "
        "recorded live in a church room, no other instruments, no vocals, 86 BPM"
    ),
}
ACESTEP_CAPTIONS: dict[str, str] = {
    "drum-a": (
        "solo live acoustic drum kit fill, floor toms and snare rolling into a crash cymbal, two "
        "beats, no melodic instruments, no piano, no guitar, no vocals, seamless lead into the "
        "next section"
    ),
    "drum-b": (
        "acoustic drum kit, punchy two-beat tom and snare fill, live church room, no other "
        "instruments, no vocals"
    ),
}
STYLES = ("drum-a", "drum-b")


# ---------------------------------------------------------------------------
# Seam
# ---------------------------------------------------------------------------


def gap_samples() -> int:
    return seconds_to_samples(GAP_MS / 1000.0)


def seam_facts(
    items: list[SongsetItem],
    recordings: dict[str, Recording],
    components: dict[str, list[Component]],
) -> dict[str, Any]:
    """Read the seam from the live DB and assert the geometry the run depends on."""
    a_item, b_item = items[0], items[1]
    if not a_item.steps or not b_item.steps:
        raise RuntimeError("arrangement_steps missing for a boundary item")
    a_end = a_item.last_step_end()
    b_start = b_item.first_step_start()
    a_rec = recordings[a_item.hash_prefix]
    b_rec = recordings[b_item.hash_prefix]
    if not b_rec.tempo_bpm:
        raise RuntimeError(
            f"no tempo for incoming recording {b_rec.hash_prefix}; cannot size the gap"
        )
    expected_ms = round(GAP_BEATS * 60000.0 / b_rec.tempo_bpm)
    if expected_ms != GAP_MS:
        raise RuntimeError(
            f"GAP_MS constant ({GAP_MS}) does not match {GAP_BEATS} beats at "
            f"{b_rec.tempo_bpm} BPM ({expected_ms}); the seam data changed"
        )
    if b_start <= 0.0:
        raise RuntimeError("incoming item starts at 0 s; the seam needs audio before it")
    if a_end < SIDE_SECONDS + HEAD_SECONDS:
        raise RuntimeError(
            f"outgoing arrangement ends at {a_end}s; need >= {SIDE_SECONDS + HEAD_SECONDS}s "
            "of audio before it"
        )
    gap = GAP_MS / 1000.0
    a_seconds = a_rec.duration_seconds
    if a_end + gap > a_seconds + 1e-6:
        raise RuntimeError(
            f"outgoing recording is {a_seconds}s; the {gap}s extension past {a_end}s "
            "does not exist (recording re-fetched?)"
        )
    if b_start + SIDE_SECONDS + HEAD_SECONDS > b_rec.duration_seconds + 1e-6:
        raise RuntimeError("incoming recording too short for the tail window")
    pickup_end = pick_entry_start(components, b_rec.content_hash)
    if pickup_end is None:
        raise RuntimeError(
            f"incoming recording {b_item.hash_prefix} has no entry component; the pickup "
            "window (its own 2 beats into the entry section) cannot be placed"
        )
    if pickup_end - gap < 0.0:
        raise RuntimeError(f"pickup window {pickup_end - gap}..{pickup_end} starts before 0 s")
    facts = {
        "a_hash": a_item.hash_prefix,
        "b_hash": b_item.hash_prefix,
        "a_song": a_item.title,
        "b_song": b_item.title,
        "a_end_seconds": round(a_end, 3),
        "b_start_seconds": round(b_start, 3),
        "gap_ms": GAP_MS,
        "gap_seconds": gap,
        "a_tempo_bpm": a_rec.tempo_bpm,
        "b_tempo_bpm": b_rec.tempo_bpm,
        "a_key": keyscale(a_rec.musical_key, a_rec.musical_mode),
        "b_key": keyscale(b_rec.musical_key, b_rec.musical_mode),
        "b_bpm_int": int(round(b_rec.tempo_bpm)),
        "pickup_start_seconds": round(pickup_end - gap, 3),
        "pickup_end_seconds": round(pickup_end, 3),
    }
    print(
        f"seam: a_end={a_end:.2f}s b_start={b_start:.2f}s gap_ms={GAP_MS} "
        f"({GAP_BEATS} beats @ {b_rec.tempo_bpm}) "
        f"{facts['a_key']} -> {facts['b_key']}",
        flush=True,
    )
    if abs(a_end - EXPECTED_A_END_SECONDS) > 1e-6:
        print(
            f"  warn: outgoing arrangement ends at {a_end:.2f}s, not the "
            f"{EXPECTED_A_END_SECONDS}s the plan recorded; windows are relative to the live value",
            flush=True,
        )
    print(
        f"  pickup: B[{facts['pickup_start_seconds']}:{facts['pickup_end_seconds']}] "
        f"(its own {GAP_BEATS} beats into the entry section)",
        flush=True,
    )
    return facts


def pick_entry_start(
    components: dict[str, list[Component]], content_hash: Optional[str]
) -> Optional[float]:
    """Start of the incoming song's entry component — its pickup ends here.

    `fetch_songset` keys components by recording content hash, and `role` is the
    same rule `boundary_seam` uses to find the incoming item's entry.
    """
    for component in components.get(content_hash or "", []):
        if component.role == "entry":
            return component.start_time
    return None


def windows(
    a_audio: np.ndarray, b_audio: np.ndarray, facts: dict[str, Any]
) -> dict[str, np.ndarray]:
    a_end = facts["a_end_seconds"]
    b_start = facts["b_start_seconds"]
    gap = facts["gap_seconds"]
    head = slice_window(a_audio, a_end - SIDE_SECONDS - HEAD_SECONDS, a_end - SIDE_SECONDS)
    a_part = slice_window(a_audio, a_end - SIDE_SECONDS, a_end)
    extension = slice_window(a_audio, a_end, a_end + gap)
    b_part = slice_window(b_audio, b_start, b_start + SIDE_SECONDS)
    tail = slice_window(b_audio, b_start + SIDE_SECONDS, b_start + SIDE_SECONDS + HEAD_SECONDS)
    pickup = slice_window(b_audio, facts["pickup_start_seconds"], facts["pickup_end_seconds"])
    hole = np.zeros((gap_samples(), 2), dtype=np.float32)
    for name, array, seconds in (
        ("head", head, HEAD_SECONDS),
        ("a_part", a_part, SIDE_SECONDS),
        ("extension", extension, gap),
        ("b_part", b_part, SIDE_SECONDS),
        ("tail", tail, HEAD_SECONDS),
        ("pickup", pickup, gap),
        ("hole", hole, gap),
    ):
        expected = seconds_to_samples(seconds)
        if array.shape[0] != expected:
            raise RuntimeError(f"{name} window has {array.shape[0]} samples, expected {expected}")
    return {
        "head": head,
        "a_part": a_part,
        "extension": extension,
        "b_part": b_part,
        "tail": tail,
        "pickup": pickup,
        "hole": hole,
    }


def montage_of(parts: dict[str, np.ndarray]) -> tuple[np.ndarray, int, int]:
    a_part = parts["a_part"]
    hole = parts["hole"]
    samples = np.ascontiguousarray(
        np.concatenate([a_part, hole, parts["b_part"]], axis=0), dtype=np.float32
    )
    hole_start = a_part.shape[0]
    return samples, hole_start, hole_start + hole.shape[0]


def reference_montage(
    a_audio: np.ndarray, b_audio: np.ndarray, facts: dict[str, Any]
) -> np.ndarray:
    """3 x 10 s at 48 kHz, exactly 1,440,000 frames (deterministic server-side crop)."""
    a_end = facts["a_end_seconds"]
    b_start = facts["b_start_seconds"]
    segments = [
        slice_window(a_audio, a_end - REFERENCE_SECONDS, a_end),
        slice_window(b_audio, b_start, b_start + REFERENCE_SECONDS),
        slice_window(
            b_audio,
            facts["pickup_end_seconds"] - REFERENCE_SECONDS,
            facts["pickup_end_seconds"],
        ),
    ]
    joined = np.concatenate(segments, axis=0).astype(np.float64)
    up, down = REFERENCE_SR, SR
    resampled = scipy.signal.resample_poly(joined, up, down, axis=0).astype(np.float32)
    resampled, corrected = fit_length(resampled, REFERENCE_SAMPLES)
    if resampled.shape[0] != REFERENCE_SAMPLES:
        raise RuntimeError(
            f"reference has {resampled.shape[0]} frames, expected {REFERENCE_SAMPLES}"
        )
    if corrected:
        print(
            f"  note: reference resampling landed {REFERENCE_SAMPLES} frames after a pad/trim",
            flush=True,
        )
    return np.ascontiguousarray(resampled, dtype=np.float32)


def write_flac_at(path: Path, samples: np.ndarray, sample_rate: int) -> None:
    import soundfile as sf

    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), samples, sample_rate, format="FLAC", subtype="PCM_16")


def reference_bytes(reference: np.ndarray, workdir: Path) -> bytes:
    path = workdir / "reference.flac"
    write_flac_at(path, reference, REFERENCE_SR)
    data = path.read_bytes()
    import soundfile as sf

    info = sf.info(str(path))
    if info.samplerate != REFERENCE_SR or info.frames != REFERENCE_SAMPLES:
        raise RuntimeError(
            f"encoded reference is {info.frames} frames at {info.samplerate} Hz; "
            f"the server's reference crop is only deterministic at {REFERENCE_SAMPLES} "
            f"frames at {REFERENCE_SR} Hz"
        )
    return data


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------


def load_prompts(out_dir: Path, refresh: bool) -> dict[str, dict[str, str]]:
    path = out_dir / "prompts.json"
    payload: dict[str, Any] = {}
    if path.exists() and not refresh:
        payload = json.loads(path.read_text())
    changed = False
    for key, defaults in (("sa3", SA3_PROMPTS), ("acestep", ACESTEP_CAPTIONS)):
        current = payload.get(key)
        if not isinstance(current, dict):
            current = {}
            changed = True
        for style, text in defaults.items():
            if not current.get(style):
                current[style] = text
                changed = True
        payload[key] = current
    payload.setdefault("version", 1)
    if changed or not path.exists():
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
        print(f"  wrote {path}", flush=True)
    return {"sa3": payload["sa3"], "acestep": payload["acestep"]}


# ---------------------------------------------------------------------------
# Variant plan
# ---------------------------------------------------------------------------


def plan_variants(args, seed_base: int) -> list[dict[str, Any]]:
    variants: list[dict[str, Any]] = []
    if not args.skip_dsp:
        for variant_id in (
            "dsp__current-silence",
            "dsp__butt-splice",
            "dsp__tail-extension",
            "dsp__b-pickup",
        ):
            variants.append({"id": variant_id, "engine": DSP_ENGINE, "style": None, "seed": None})
    if not args.skip_sa3:
        for style in STYLES:
            for offset in range(1, 4):
                seed = seed_base + offset
                variants.append(
                    {
                        "id": f"sa3__{style}__s{seed}",
                        "engine": SA3_ENGINE,
                        "style": style,
                        "seed": seed,
                    }
                )
    if not args.skip_acestep:
        for engine, prefix, steps, adg, shift, class_seed in (
            (TURBO_ENGINE, "acestep-turbo", 8, False, 3.0, 101),
            (BASE_ENGINE, "acestep-base", 32, True, 1.0, 201),
        ):
            for index, style in enumerate(STYLES):
                seed = seed_base + class_seed + index
                variants.append(
                    {
                        "id": f"{prefix}__{style}__s{seed}",
                        "engine": engine,
                        "style": style,
                        "seed": seed,
                        "inference_steps": steps,
                        "use_adg": adg,
                        "shift": shift,
                    }
                )
    return variants


# ---------------------------------------------------------------------------
# Modal clients
# ---------------------------------------------------------------------------


def modal_client(app: str, cls: str):
    import modal

    return modal.Cls.from_name(app, cls)()


def call_with_retry(fn, *args) -> dict[str, Any]:
    last_error: Optional[Exception] = None
    for attempt in range(1, CALL_ATTEMPTS + 1):
        try:
            return fn(*args)
        except Exception as exc:  # noqa: BLE001 — one retry, then fail the variant
            last_error = exc
            print(f"    attempt {attempt}/{CALL_ATTEMPTS} failed: {exc}", flush=True)
            if attempt < CALL_ATTEMPTS:
                time.sleep(CALL_BACKOFF_SECONDS)
    raise RuntimeError(f"modal call failed after {CALL_ATTEMPTS} attempts: {last_error}")


def encode_flac_bytes(samples: np.ndarray) -> bytes:
    with tempfile.TemporaryDirectory(prefix="seam-fill-encode-") as tmp:
        path = Path(tmp) / "upload.flac"
        write_flac(path, samples)
        return path.read_bytes()


# ---------------------------------------------------------------------------
# Assembly + metrics
# ---------------------------------------------------------------------------


def apply_edge_fades(clip: np.ndarray, index: int, fade: int) -> None:
    """Equal-power fade-out ending at *index* and fade-in starting there, in place."""
    if index - fade < 0 or index + fade > clip.shape[0]:
        raise RuntimeError(
            f"cannot fade at sample {index} (fade {fade}) in {clip.shape[0]} samples"
        )
    ramp = np.linspace(0.0, 1.0, fade, endpoint=False, dtype=np.float32)[:, None]
    clip[index - fade : index] *= np.cos(ramp * (np.pi / 2.0))
    clip[index : index + fade] *= np.sin(ramp * (np.pi / 2.0))


def apply_out_fade(clip: np.ndarray, end: int, fade: int) -> None:
    ramp = np.linspace(0.0, 1.0, fade, endpoint=False, dtype=np.float32)[:, None]
    clip[end - fade : end] *= np.cos(ramp * (np.pi / 2.0))


def rnd(value: Optional[float], digits: int = 2) -> Optional[float]:
    if value is None:
        return None
    value = float(value)
    if not math.isfinite(value):
        return None
    return round(value, digits)


def measure(
    fill: Optional[np.ndarray],
    clip: np.ndarray,
    a_level: float,
    b_level: float,
    peak_limited: bool,
) -> dict[str, Any]:
    import librosa

    metrics: dict[str, Any] = {
        "a_level_db": rnd(a_level),
        "b_level_db": rnd(b_level),
        "fill_rms_db": None,
        "fill_peak": None,
        "fill_spectral_centroid_hz": None,
        "fill_stereo_corr": None,
        "fill_onset_density_per_s": None,
        "fill_duration_seconds": None,
        "clip_duration_seconds": rnd(clip.shape[0] / SR, 3),
        "peak": rnd(peak_level(clip), 4),
        "peak_limited": peak_limited,
    }
    if fill is None or fill.shape[0] == 0:
        return metrics
    seconds = fill.shape[0] / SR
    metrics["fill_duration_seconds"] = rnd(seconds, 3)
    metrics["fill_peak"] = rnd(peak_level(fill), 4)
    level = rms_db(fill)
    metrics["fill_rms_db"] = rnd(level)
    if not np.isfinite(level) or level <= SILENCE_DB_FLOOR:
        return metrics
    mono = fill.mean(axis=1)
    metrics["fill_spectral_centroid_hz"] = rnd(
        float(librosa.feature.spectral_centroid(y=mono, sr=SR, n_fft=1024).mean()), 1
    )
    if float(np.std(fill[:, 0])) > 0.0 and float(np.std(fill[:, 1])) > 0.0:
        metrics["fill_stereo_corr"] = rnd(float(np.corrcoef(fill[:, 0], fill[:, 1])[0, 1]), 3)
    onsets = librosa.onset.onset_detect(y=mono, sr=SR)
    metrics["fill_onset_density_per_s"] = rnd(len(onsets) / seconds, 2)
    return metrics


def levels(parts: dict[str, np.ndarray]) -> tuple[float, float]:
    a_level = rms_db(parts["a_part"][-seconds_to_samples(1.0) :])
    b_level = rms_db(parts["b_part"][: seconds_to_samples(1.0)])
    return a_level, b_level


def build_clip(
    parts: dict[str, np.ndarray],
    fill: np.ndarray,
    fill_fade_seconds: float,
    tail_fade: bool = False,
    join_fades: bool = True,
) -> tuple[np.ndarray, np.ndarray]:
    """Splice head | a_part | fill | b_part | tail, returning the clip and the written fill.

    The returned fill is the clip's own fill region, so `fills/<id>.flac` carries
    exactly the audio that plays in the gap (including its edge fades) instead of
    a differently-faded copy.
    """
    body = np.ascontiguousarray(
        np.concatenate([parts["a_part"], fill, parts["b_part"]], axis=0), dtype=np.float32
    )
    clip = np.ascontiguousarray(
        np.concatenate([parts["head"], body, parts["tail"]], axis=0), dtype=np.float32
    )
    head_len = parts["head"].shape[0]
    fill_start = head_len + parts["a_part"].shape[0]
    fill_end = fill_start + fill.shape[0]
    if fill_fade_seconds > 0.0:
        apply_edge_fades(clip, fill_start, seconds_to_samples(fill_fade_seconds))
        apply_edge_fades(clip, fill_end, seconds_to_samples(fill_fade_seconds))
    if tail_fade:
        apply_out_fade(clip, fill_end, seconds_to_samples(TAIL_FADE_SECONDS))
    if join_fades:
        apply_join_fades(clip, (head_len, head_len + body.shape[0]))
    return clip, np.ascontiguousarray(clip[fill_start:fill_end], dtype=np.float32)


def gain_fill(fill: np.ndarray, a_level: float, b_level: float) -> tuple[np.ndarray, bool]:
    """Put the fill on the seam's average level, tilted from A's level to B's."""
    target = 0.5 * (a_level + b_level)
    level = rms_db(fill)
    if not np.isfinite(level) or not np.isfinite(target):
        return fill, False
    base_gain = 10.0 ** ((target - level) / 20.0)
    tilt = gain_ramp(fill.shape[0], a_level - target, b_level - target)
    out = np.ascontiguousarray(fill * (base_gain * tilt)[:, None], dtype=np.float32)
    peak_limited = False
    peak = peak_level(out)
    if peak > PEAK_CEILING:
        out = np.ascontiguousarray(out * (PEAK_CEILING / peak), dtype=np.float32)
        peak_limited = True
    return out, peak_limited


# ---------------------------------------------------------------------------
# Variant execution
# ---------------------------------------------------------------------------


def write_variant_outputs(
    out_dir: Path,
    variant: dict[str, Any],
    fill: Optional[np.ndarray],
    clip: np.ndarray,
) -> None:
    write_flac(out_dir / "clips" / f"{variant['id']}.flac", clip)
    if fill is not None:
        write_flac(out_dir / "fills" / f"{variant['id']}.flac", fill)


def record_base(
    variant: dict[str, Any],
    prompts: dict[str, dict[str, str]],
) -> dict[str, Any]:
    engine = variant["engine"]
    prompt = None
    if engine == SA3_ENGINE:
        prompt = prompts["sa3"][variant["style"]]
    elif engine in (TURBO_ENGINE, BASE_ENGINE):
        prompt = prompts["acestep"][variant["style"]]
    return {
        "id": variant["id"],
        "engine": engine,
        "status": "pending",
        "prompt": prompt,
        "seed": variant["seed"],
        "clip": f"clips/{variant['id']}.flac",
        "fill": None,
        "montage": None,
        "request": None,
        "meta": None,
        "elapsed_seconds": None,
        "metrics": {},
        "length_corrected": False,
        "error": None,
    }


def sidecar_is_done(out_dir: Path, variant_id: str) -> bool:
    sidecar = out_dir / "variants" / f"{variant_id}.json"
    clip = out_dir / "clips" / f"{variant_id}.flac"
    if not sidecar.exists() or not clip.exists():
        return False
    try:
        previous = json.loads(sidecar.read_text())
    except json.JSONDecodeError:
        return False
    return previous.get("status") == "ok"


def finish(out_dir: Path, record: dict[str, Any]) -> dict[str, Any]:
    sidecar = out_dir / "variants" / f"{record['id']}.json"
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n")
    return record


def run_dsp(
    variant: dict[str, Any],
    parts: dict[str, np.ndarray],
    out_dir: Path,
    prompts: dict[str, dict[str, str]],
    force: bool,
) -> dict[str, Any]:
    record = record_base(variant, prompts)
    if sidecar_is_done(out_dir, variant["id"]) and not force:
        print(f"  skip {variant['id']} (clip exists; --force to regenerate)", flush=True)
        return json.loads((out_dir / "variants" / f"{variant['id']}.json").read_text())

    a_level, b_level = levels(parts)
    variant_id = variant["id"]
    tail_fade = False
    fill_fade = FILL_FADE_SECONDS
    if variant_id == "dsp__current-silence":
        fill: Optional[np.ndarray] = parts["hole"].copy()
        fill_fade = 0.0
    elif variant_id == "dsp__butt-splice":
        # No fill at all: A's arranged material is butted against B's first downbeat.
        fill = None
        clip = np.ascontiguousarray(
            np.concatenate(
                [parts["head"], parts["a_part"], parts["b_part"], parts["tail"]], axis=0
            ),
            dtype=np.float32,
        )
        head_len = parts["head"].shape[0]
        # Three joins: head -> a_part, the A|B splice itself (the one that needs
        # de-clicking), and b_part -> tail.
        apply_join_fades(
            clip,
            (
                head_len,
                head_len + parts["a_part"].shape[0],
                head_len + parts["a_part"].shape[0] + parts["b_part"].shape[0],
            ),
        )
        metrics = measure(None, clip, a_level, b_level, False)
        write_variant_outputs(out_dir, variant, None, clip)
        record.update({"status": "ok", "metrics": metrics, "fill": None, "montage": None})
        return finish(out_dir, record)
    elif variant_id == "dsp__tail-extension":
        fill = parts["extension"].copy()
        tail_fade = True
        fill_fade = 0.0  # contiguous with a_part at the arrangement cut
    elif variant_id == "dsp__b-pickup":
        fill = parts["pickup"].copy()
    else:
        raise RuntimeError(f"unknown dsp variant {variant_id}")

    clip, written_fill = build_clip(parts, fill, fill_fade, tail_fade=tail_fade)
    metrics = measure(written_fill, clip, a_level, b_level, False)
    write_variant_outputs(out_dir, variant, written_fill, clip)
    record.update(
        {
            "status": "ok",
            "metrics": metrics,
            "fill": f"fills/{variant_id}.flac",
            "montage": None,
        }
    )
    print(
        f"  dsp {variant_id}: fill {metrics['fill_duration_seconds']}s "
        f"{metrics['fill_rms_db']} dB, clip {metrics['clip_duration_seconds']}s",
        flush=True,
    )
    return finish(out_dir, record)


def build_request(
    variant: dict[str, Any],
    facts: dict[str, Any],
    prompts: dict[str, dict[str, str]],
    montage_seconds: float,
) -> dict[str, Any]:
    engine = variant["engine"]
    start = SIDE_SECONDS
    end = round(SIDE_SECONDS + facts["gap_seconds"], 3)
    if engine == SA3_ENGINE:
        return {
            "prompt": prompts["sa3"][variant["style"]],
            "seed": variant["seed"],
            "steps": SA3_STEPS,
            "start_seconds": start,
            "end_seconds": end,
            "duration_seconds": montage_seconds,
        }
    return {
        "caption": prompts["acestep"][variant["style"]],
        "lyrics": INSTRUMENTAL_LYRICS,
        "instrumental": True,
        "bpm": facts["b_bpm_int"],
        "keyscale": facts["b_key"],
        "duration": montage_seconds,
        "seed": variant["seed"],
        "repaint_start": start,
        "repaint_end": end,
        "vocal_language": "en",
        "inference_steps": variant["inference_steps"],
        "guidance_scale": 7.0,
        "use_adg": variant["use_adg"],
        "shift": variant["shift"],
        # The pinned handler ignores explicit crossfade values: it derives them
        # from (mode, strength) via `_resolve_repaint_config`. Send those instead.
        "repaint_mode": "balanced",
        "repaint_strength": 0.5,
    }


def run_generated(
    variant: dict[str, Any],
    parts: dict[str, np.ndarray],
    montage: tuple[np.ndarray, int, int],
    out_dir: Path,
    facts: dict[str, Any],
    prompts: dict[str, dict[str, str]],
    clients: dict[str, Any],
    reference: bytes,
    montage_bytes: bytes,
    force: bool,
    dry_run: bool,
) -> dict[str, Any]:
    record = record_base(variant, prompts)
    variant_id = variant["id"]
    samples, hole_start, hole_end = montage
    montage_seconds = samples.shape[0] / SR
    request = build_request(variant, facts, prompts, montage_seconds)
    record["request"] = request

    if sidecar_is_done(out_dir, variant_id) and not force:
        print(f"  skip {variant_id} (clip exists; --force to regenerate)", flush=True)
        return json.loads((out_dir / "variants" / f"{variant_id}.json").read_text())

    write_flac(out_dir / "montages" / f"{variant_id}.flac", samples)
    record["montage"] = f"montages/{variant_id}.flac"

    if dry_run:
        record["status"] = "dry_run"
        record["metrics"] = {
            "montage_duration_seconds": rnd(montage_seconds, 3),
            "hole_samples": hole_end - hole_start,
        }
        return finish(out_dir, record)

    a_level, b_level = levels(parts)
    try:
        started = time.time()
        client = clients[variant["engine"]]
        if variant["engine"] == SA3_ENGINE:
            response = call_with_retry(
                client.inpaint.remote, montage_bytes, f"{variant_id}-montage.flac", request
            )
        else:
            response = call_with_retry(
                client.repaint.remote,
                montage_bytes,
                f"{variant_id}-montage.flac",
                request,
                reference,
            )
        record["elapsed_seconds"] = round(time.time() - started, 3)

        with tempfile.TemporaryDirectory(prefix=f"seam-fill-{variant_id}-") as tmp:
            returned = decode_flac_bytes(response["audio"], Path(tmp), variant_id)
        returned_samples = int(returned.shape[0])
        returned, corrected = fit_length(returned, samples.shape[0])
        record["length_corrected"] = corrected
        record["returned_samples"] = returned_samples
        record["meta"] = response.get("meta") or {}

        fill = np.ascontiguousarray(returned[hole_start:hole_end], dtype=np.float32)
        if fill.shape[0] != hole_end - hole_start:
            raise RuntimeError(
                f"fill has {fill.shape[0]} samples, expected {hole_end - hole_start}"
            )
        level = rms_db(fill)
        if not np.isfinite(level) or peak_level(fill) < 0.001:
            raise RuntimeError(
                f"model returned a silent fill (rms {level} dB, peak {peak_level(fill)})"
            )

        fill, peak_limited = gain_fill(fill, a_level, b_level)
        clip, written_fill = build_clip(parts, fill, FILL_FADE_SECONDS)
        metrics = measure(written_fill, clip, a_level, b_level, peak_limited)
        write_variant_outputs(out_dir, variant, written_fill, clip)
        record["metrics"] = metrics
        record["fill"] = f"fills/{variant_id}.flac"
        record["status"] = "ok"
        print(
            f"  {variant_id}: fill {metrics['fill_rms_db']} dB (A {metrics['a_level_db']} / "
            f"B {metrics['b_level_db']}), peak {metrics['peak']}, "
            f"{record['elapsed_seconds']:.1f}s",
            flush=True,
        )
    except Exception as exc:  # noqa: BLE001 — one bad variant must not kill the run
        record["status"] = "failed"
        record["error"] = f"{type(exc).__name__}: {exc}"
        print(f"  {variant_id} FAILED: {record['error']}", flush=True)

    return finish(out_dir, record)


# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------


def write_index(
    out_dir: Path,
    facts: dict[str, Any],
    records: list[dict[str, Any]],
    started_iso: str,
) -> None:
    payload = {
        "version": 2,
        "songset_id": facts["songset_id"],
        "created_at": started_iso,
        "seam": {
            "a_hash": facts["a_hash"],
            "b_hash": facts["b_hash"],
            "a_end_seconds": facts["a_end_seconds"],
            "b_start_seconds": facts["b_start_seconds"],
            "gap_ms": facts["gap_ms"],
            "gap_seconds": facts["gap_seconds"],
            "a_tempo_bpm": facts["a_tempo_bpm"],
            "b_tempo_bpm": facts["b_tempo_bpm"],
            "a_key": facts["a_key"],
            "b_key": facts["b_key"],
        },
        "models": {
            "sa3": SA3_ENGINE,
            "acestep_turbo": TURBO_ENGINE,
            "acestep_base": BASE_ENGINE,
        },
        "variants": records,
    }
    (out_dir / "index.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


SECTIONS = (
    ("DSP baselines (real audio)", (DSP_ENGINE,)),
    ("Stable Audio 3 Small-Music", (SA3_ENGINE,)),
    ("ACE-Step 1.5 turbo", (TURBO_ENGINE,)),
    ("ACE-Step 1.5 base", (BASE_ENGINE,)),
)


def metric_line(record: dict[str, Any]) -> str:
    metrics = record.get("metrics") or {}
    candidates = [
        ("fill duration", metrics.get("fill_duration_seconds"), "s"),
        ("fill RMS", metrics.get("fill_rms_db"), "dB"),
        ("fill peak", metrics.get("fill_peak"), ""),
        ("centroid", metrics.get("fill_spectral_centroid_hz"), "Hz"),
        ("stereo corr", metrics.get("fill_stereo_corr"), ""),
        ("onsets", metrics.get("fill_onset_density_per_s"), "/s"),
        ("A", metrics.get("a_level_db"), "dB"),
        ("B", metrics.get("b_level_db"), "dB"),
        ("clip", metrics.get("clip_duration_seconds"), "s"),
        ("peak", metrics.get("peak"), ""),
    ]
    bits = [
        f"{label} {value} {unit}".rstrip() for label, value, unit in candidates if value is not None
    ]
    if metrics.get("peak_limited"):
        bits.append("peak-limited")
    return " · ".join(bits)


def report_html(records: list[dict[str, Any]], facts: dict[str, Any]) -> str:
    title = "Seam Fill POC run2 — drum fill for one 1.393 s gap"
    subtitle = (
        f"{facts['a_song']} ({facts['a_key']}, {facts['a_tempo_bpm']} BPM) -> "
        f"{facts['b_song']} ({facts['b_key']}, {facts['b_tempo_bpm']} BPM) · "
        f"gap {GAP_BEATS} beats = {facts['gap_seconds']:.3f} s · "
        f"A extended from {facts['a_end_seconds']:.2f} s"
    )
    sections: list[str] = []
    for heading, engines in SECTIONS:
        group = [r for r in records if r["engine"] in engines]
        if not group:
            continue
        cards: list[str] = []
        for record in group:
            # Only an `ok` record has a clip file on disk; a failed or dry-run
            # record keeps its planned path, which must not render a dead player.
            players = []
            if record["status"] == "ok":
                clip = html.escape(record["clip"])
                players.append(
                    '<div class="player"><span>clip</span>'
                    f'<audio controls preload="none" src="{clip}"></audio></div>'
                )
            if record.get("fill"):
                players.append(
                    f'<div class="player"><span>fill</span>'
                    f'<audio controls preload="none" src="{html.escape(record["fill"])}"></audio></div>'
                )
            if record["status"] == "failed":
                players.append(
                    f'<p class="error">failed: {html.escape(str(record.get("error")))}</p>'
                )
            elif record["status"] == "dry_run":
                players.append('<p class="meta">dry run — nothing generated</p>')
            meta_bits = [html.escape(record["engine"])]
            if record["seed"] is not None:
                meta_bits.append(f"seed {record['seed']}")
            meta_bits.append(f"status {html.escape(record['status'])}")
            meta_bits.append(f"length_corrected {record['length_corrected']}")
            prompt_block = ""
            if record.get("prompt"):
                prompt_block = (
                    f"<details><summary>prompt</summary>"
                    f"<p>{html.escape(str(record['prompt']))}</p></details>"
                )
            cards.append(
                f"""
      <article class="card">
        <h3>{html.escape(record['id'])}</h3>
        <p class="meta">{' · '.join(meta_bits)}</p>
        <div class="players">{''.join(players)}</div>
        <p class="metrics">{html.escape(metric_line(record))}</p>
        {prompt_block}
      </article>"""
            )
        sections.append(
            f"<section><h2>{html.escape(heading)}</h2>"
            f'<div class="grid">{"".join(cards)}</div></section>'
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
  .player span {{ font-size: 11px; text-transform: uppercase; letter-spacing: .05em; color: #777; }}
  audio {{ width: 100%; }}
  .error {{ color: #b00; }}
  details {{ margin-top: 6px; font-size: 12px; }}
  summary {{ cursor: pointer; }}
</style>
</head>
<body>
  <h1>{html.escape(title)}</h1>
  <p class="sub">{html.escape(subtitle)}</p>
  <p class="sub">Each clip is 7 s of the outgoing song, its last 8 arranged seconds, the fill, the
  incoming song's first 8 s, then 7 s more. The fill player isolates just the gap (1.393 s).
  Metrics are recorded, not used for selection.</p>
  {''.join(sections)}
</body>
</html>
"""


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seam Fill POC run2 (drum fill for one gap)")
    parser.add_argument("--songset", default=DEFAULT_SONGSET, help="songset id")
    parser.add_argument("--out", required=True, help="output directory for this run")
    parser.add_argument("--seed", type=int, default=7000, help="seed base")
    parser.add_argument("--force", action="store_true", help="regenerate even when clips exist")
    parser.add_argument("--refresh-prompts", action="store_true", help="rewrite prompts.json")
    parser.add_argument("--skip-dsp", action="store_true", help="skip the DSP baselines")
    parser.add_argument("--skip-sa3", action="store_true", help="skip Stable Audio 3")
    parser.add_argument("--skip-acestep", action="store_true", help="skip both ACE-Step models")
    parser.add_argument("--dry-run", action="store_true", help="zero Modal calls")
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    out_dir = Path(args.out)
    for sub in ("clips", "fills", "montages", "variants", "cache"):
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
            f"{recording.musical_key}/{recording.musical_mode}",
            flush=True,
        )

    facts = seam_facts(items, recordings, components)
    facts["songset_id"] = args.songset

    audio = {
        items[0].hash_prefix: ensure_source_audio(items[0].hash_prefix, out_dir, connect_r2),
        items[1].hash_prefix: ensure_source_audio(items[1].hash_prefix, out_dir, connect_r2),
    }
    parts = windows(audio[items[0].hash_prefix], audio[items[1].hash_prefix], facts)
    montage = montage_of(parts)

    prompts = load_prompts(out_dir, args.refresh_prompts)
    variants = plan_variants(args, args.seed)
    print(f"  {len(variants)} variants, dry_run={args.dry_run}", flush=True)

    clients: dict[str, Any] = {}
    reference = b""
    montage_bytes = b""
    if not args.dry_run:
        if not args.skip_sa3:
            clients[SA3_ENGINE] = modal_client(SA3_APP, SA3_CLASS)
            print(f"  sa3 class: {SA3_CLASS}", flush=True)
        if not args.skip_acestep:
            clients[TURBO_ENGINE] = modal_client(ACESTEP_APP, ACESTEP_TURBO_CLASS)
            clients[BASE_ENGINE] = modal_client(ACESTEP_APP, ACESTEP_BASE_CLASS)
        reference_array = reference_montage(
            audio[items[0].hash_prefix], audio[items[1].hash_prefix], facts
        )
        workdir = Path(tempfile.mkdtemp(prefix="seam-fill-reference-"))
        reference = reference_bytes(reference_array, workdir)
        montage_bytes = encode_flac_bytes(montage[0])
        print(
            f"  reference: {REFERENCE_SAMPLES} frames @ {REFERENCE_SR} Hz, "
            f"montage: {montage[0].shape[0]} frames",
            flush=True,
        )

    records: list[dict[str, Any]] = []
    for variant in variants:
        if variant["engine"] == DSP_ENGINE:
            records.append(run_dsp(variant, parts, out_dir, prompts, args.force))
        else:
            records.append(
                run_generated(
                    variant,
                    parts,
                    montage,
                    out_dir,
                    facts,
                    prompts,
                    clients,
                    reference,
                    montage_bytes,
                    args.force,
                    args.dry_run,
                )
            )

    wall = time.time() - started
    write_index(out_dir, facts, records, started_iso)
    (out_dir / "report.html").write_text(report_html(records, facts))

    generated = [record for record in records if record["engine"] != DSP_ENGINE]
    ok = [record for record in records if record["status"] == "ok"]
    failed = [record for record in records if record["status"] == "failed"]
    dry = [record for record in records if record["status"] == "dry_run"]
    gpu_seconds = sum(float(record.get("elapsed_seconds") or 0.0) for record in records)
    print(
        f"done: {len(ok)} ok, {len(failed)} failed, {len(dry)} dry_run of {len(records)} "
        f"variants in {wall:.1f}s (modal {gpu_seconds:.1f}s)",
        flush=True,
    )
    for record in failed:
        print(f"  failed {record['id']}: {record['error']}", flush=True)
    if generated and all(record["status"] == "failed" for record in generated):
        print("  every generated variant failed", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
