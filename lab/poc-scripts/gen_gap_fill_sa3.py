"""Seam Fill run3 — immediate drum onset and beat-grid correction for one 1.393 s gap.

Run2's best-sounding take (`sa3__drum-a__s7003`) still opens with ~0.42 s of
near-digital silence: measured with backtracked onset detection its first hit is
at 406 ms and its next clear hit at 940 ms, while the incoming song's beat is
~696.6 ms. The defect is inside the generated fill, not the clip assembly, so
run3 attacks it from both sides:

* two new Stable Audio 3 prompts that ask for drums *on beat one* with no
  lead-in and no count-in, three seeds each;
* two deterministic corrections of every raw take (prompt improvement alone is
  not trusted): `trim` drops everything before the first detected onset and
  pads/trims the end, `grid` additionally time-stretches so the second hit lands
  exactly one incoming-tempo beat after the first.

Reused without change: `run2/montages/sa3__drum-a__s7003.flac` (the exact bytes
run2 uploaded), the reconstructed parts of `run2/clips/sa3__drum-a__s7003.flac`,
the already gain-staged `run2/fills/sa3__drum-a__s7003.flac` as the reference
take, and run2's variant sidecar as its request/metadata record. No database or
R2 access: everything is read from `--run2` on disk.

Outputs under `--out`:

    clips/          listenable 31.393 s splice per variant
    fills/          the fill region of that splice (1.393 s)
    montages/       the montage per raw take (what was (or would be) uploaded)
    variants/       per-variant record (idempotency sidecar for Modal takes)
    prompts.json    the two run3 prompts
    index.json      version 3: seam, tempo/beat geometry, and every variant
    report.html     self-contained comparison page

Usage:
    set -a && . /opt/sow/.env && set +a
    uv run --project lab/poc-scripts --extra audio_inpaint \\
        python lab/poc-scripts/gen_gap_fill_sa3.py \\
        --out lab/poc-scripts/output/audio-inpaint/run3

`--dry-run` makes zero Modal calls and still writes the run2 reference raw/trim/
grid audio, so the reported silence can be inspected before paying for a GPU.
"""

from __future__ import annotations

import argparse
import html
import json
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gen_seam_inpaint_acestep import SR, fit_length  # noqa: E402
from gen_gap_fill_dsp_sa3 import (  # noqa: E402  (run2 module, renamed; reused verbatim)
    SA3_APP,
    SA3_CLASS,
    SA3_ENGINE,
    SA3_STEPS,
    SIDE_SECONDS,
    build_clip,
    call_with_retry,
    decode_flac_bytes,
    gain_fill,
    levels,
    measure,
    modal_client,
    peak_level,
    rms_db,
    write_flac,
)

# Seam geometry, identical to run2 (asserted against the files on disk, never assumed).
GAP_MS = 1393
RUN2_ID = "sa3__drum-a__s7003"
A_HASH = "6a410a5c222a"
B_HASH = "20379ea26256"
HEAD_SAMPLES = 308_700  # 7.0 s
SIDE_SAMPLES = 352_800  # 8.0 s
FILL_SAMPLES = 61_431  # 1.393 s
# The mask end the models were asked for; the sample slice truncates to 61,431 frames.
SEAM_END_SECONDS = 9.393
TAIL_SAMPLES = 308_700  # 7.0 s
CLIP_SAMPLES = HEAD_SAMPLES + SIDE_SAMPLES + FILL_SAMPLES + SIDE_SAMPLES + TAIL_SAMPLES
MONTAGE_SAMPLES = SIDE_SAMPLES + FILL_SAMPLES + SIDE_SAMPLES
# Run3 fades the fill edges by 5 ms instead of run2's 10 ms so an immediate first
# attack keeps as much of its transient as the splice can afford.
FILL_FADE_SECONDS = 0.005
# A raw take with nothing to trim is not a usable drum performance.
SILENCE_PEAK_FLOOR = 0.001
FIRST_ONSET_TARGET_MS = 50.0
# Below this a sample counts as digital silence (~-80 dBFS, under PCM16 dither).
SILENCE_FLOOR = 1e-4
# Window for the measured beat-two energy peak: the target beat ±50 ms.
BEAT_TWO_WINDOW_MS = 50.0
# Grid correction is refused outside this stretch range; anything further is a
# different performance, not a timing fix.
STRETCH_RATIO_MIN = 0.5
STRETCH_RATIO_MAX = 2.0
CALL_ATTEMPTS = 2

REFERENCE_RAW_ID = "run2-s7003__raw"

SA3_PROMPTS: dict[str, str] = {
    "immediate-roll": (
        "TrackType: Instrument, VocalType: Instrumental, Genre: Worship, solo live acoustic "
        "drum kit, two beats at 86 BPM, tight natural drummer, drums start immediately on beat "
        "one with a snare and floor-tom hit, continuous short fill, clear second hit exactly one "
        "beat later, end cleanly on beat two, no silence, no lead-in, no count-in, no crash, no "
        "melodic instruments, no vocals"
    ),
    "immediate-pocket": (
        "TrackType: Instrument, solo live acoustic drum kit, immediate punchy snare hit at the "
        "start, natural two-beat eighth-note floor-tom and snare fill at 86 BPM, recorded live "
        "in a church room, dry close kit, no silence before the first drum, no lead-in, no "
        "cymbal wash, no other instruments, no vocals"
    ),
}
STYLES = tuple(SA3_PROMPTS)


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


def read_flac(path: Path) -> np.ndarray:
    import soundfile as sf

    if not path.is_file():
        raise SystemExit(f"missing audio input: {path}")
    samples, sample_rate = sf.read(str(path), dtype="float32", always_2d=True)
    if sample_rate != SR:
        raise SystemExit(f"{path} is {sample_rate} Hz, expected {SR}")
    if samples.shape[1] != 2:
        raise SystemExit(f"{path} has {samples.shape[1]} channels, expected 2")
    return np.ascontiguousarray(samples, dtype=np.float32)


def require_frames(samples: np.ndarray, expected: int, label: str) -> np.ndarray:
    if samples.shape[0] != expected:
        raise SystemExit(f"{label} has {samples.shape[0]} frames, expected {expected}")
    return samples


def load_run2(run2: Path) -> dict[str, Any]:
    """Read and assert every run2 fact run3 depends on. Fails loudly, never guesses."""
    index_path = run2 / "index.json"
    if not index_path.is_file():
        raise SystemExit(f"missing run2 index: {index_path}")
    index = json.loads(index_path.read_text())
    seam = index.get("seam") or {}
    if seam.get("gap_ms") != GAP_MS:
        raise SystemExit(f"run2 gap_ms {seam.get('gap_ms')} != {GAP_MS}")
    if seam.get("a_hash") != A_HASH or seam.get("b_hash") != B_HASH:
        raise SystemExit(
            f"run2 seam hashes {seam.get('a_hash')}/{seam.get('b_hash')} "
            f"!= {A_HASH}/{B_HASH}"
        )
    tempo = float(seam["b_tempo_bpm"])
    # The gap is the incoming song's two beats; the grid anchors on one of them.
    beat_samples = round(60.0 / tempo * SR)
    if beat_samples != 30_720:
        raise SystemExit(f"beat_samples {beat_samples} != 30720 at {tempo} BPM")

    variant_path = run2 / "variants" / f"{RUN2_ID}.json"
    if not variant_path.is_file():
        raise SystemExit(f"missing run2 variant record: {variant_path}")
    reference_variant = json.loads(variant_path.read_text())
    if reference_variant.get("status") != "ok":
        raise SystemExit(f"run2 variant {RUN2_ID} is {reference_variant.get('status')!r}")

    clip = require_frames(read_flac(run2 / "clips" / f"{RUN2_ID}.flac"), CLIP_SAMPLES, "run2 clip")
    montage = require_frames(
        read_flac(run2 / "montages" / f"{RUN2_ID}.flac"), MONTAGE_SAMPLES, "run2 montage"
    )
    reference_fill = require_frames(
        read_flac(run2 / "fills" / f"{RUN2_ID}.flac"), FILL_SAMPLES, "run2 fill"
    )

    head_end = HEAD_SAMPLES
    a_end = head_end + SIDE_SAMPLES
    b_start = a_end + FILL_SAMPLES
    b_end = b_start + SIDE_SAMPLES
    parts = {
        "head": np.ascontiguousarray(clip[:head_end]),
        "a_part": np.ascontiguousarray(clip[head_end:a_end]),
        "b_part": np.ascontiguousarray(clip[b_start:b_end]),
        "tail": np.ascontiguousarray(clip[b_end:]),
    }
    if sum(part.shape[0] for part in parts.values()) + FILL_SAMPLES != CLIP_SAMPLES:
        raise SystemExit("reconstructed run2 parts do not sum back to the clip length")

    return {
        "path": run2,
        "index": index,
        "seam": seam,
        "tempo": tempo,
        "beat_samples": beat_samples,
        "variant": reference_variant,
        "parts": parts,
        "clip": clip,
        "montage": montage,
        "reference_fill": reference_fill,
    }


# ---------------------------------------------------------------------------
# Onset detection and timing correction
# ---------------------------------------------------------------------------


def detect_onsets(fill: np.ndarray) -> np.ndarray:
    """Backtracked onset positions (samples) of a fill, as run3 measures them."""
    import librosa

    onset_env = librosa.onset.onset_strength(y=fill.mean(axis=1), sr=SR)
    onsets = librosa.onset.onset_detect(
        onset_envelope=onset_env, sr=SR, backtrack=True, delta=0.05
    )
    return np.asarray(librosa.frames_to_samples(onsets), dtype=np.int64)


def correct_timing(
    fill: np.ndarray,
    onset_samples: np.ndarray,
    beat_samples: int,
    mode: str,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Return a 61,431-frame fill whose first hit is at sample 0.

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
        corrected, _ = fit_length(trimmed, FILL_SAMPLES)
        return np.ascontiguousarray(corrected, dtype=np.float32), correction

    target = int(beat_samples)
    wanted = first + target
    half = target // 2
    candidates = [int(s) for s in onsets[1:] if wanted - half <= s <= wanted + half]
    if not candidates:
        raise ValueError(
            f"no second onset within {half} samples of beat two (sample {wanted})"
        )
    anchor = min(candidates, key=lambda sample: abs(sample - wanted))
    ratio = (anchor - first) / float(target)
    if not (STRETCH_RATIO_MIN < ratio < STRETCH_RATIO_MAX):
        raise ValueError(
            f"time-stretch ratio {ratio:.4f} outside "
            f"({STRETCH_RATIO_MIN}, {STRETCH_RATIO_MAX})"
        )
    import librosa

    channels = [
        librosa.effects.time_stretch(np.ascontiguousarray(trimmed[:, channel]), rate=ratio)
        for channel in range(trimmed.shape[1])
    ]
    stretched = np.ascontiguousarray(np.stack(channels, axis=1), dtype=np.float32)
    corrected, _ = fit_length(stretched, FILL_SAMPLES)
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
    mono = np.abs(fill.mean(axis=1))
    material = np.flatnonzero(mono > SILENCE_FLOOR)
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
    mono = np.abs(fill.mean(axis=1))
    return round((low + int(np.argmax(mono[low:high]))) / SR * 1000.0, 2)


def timing_metrics(fill: np.ndarray, beat_samples: int) -> dict[str, Any]:
    """Every timing field the report shows for one written fill."""
    metrics = onset_metrics(fill, beat_samples)
    metrics["trailing_silence_ms"] = trailing_silence_ms(fill)
    metrics["beat_two_peak_ms"] = beat_two_peak_ms(fill, beat_samples)
    return metrics


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


# ---------------------------------------------------------------------------
# Variant planning and records
# ---------------------------------------------------------------------------


def plan_raw_takes(seed_base: int) -> list[dict[str, Any]]:
    takes: list[dict[str, Any]] = []
    for style in STYLES:
        for offset in (1, 2, 3):
            seed = seed_base + offset
            takes.append(
                {
                    "id": f"sa3__{style}__s{seed}__raw",
                    "style": style,
                    "seed": seed,
                    "prompt": SA3_PROMPTS[style],
                    "version": "raw",
                }
            )
    return takes


def raw_request(prompt: str, seed: int) -> dict[str, Any]:
    return {
        "prompt": prompt,
        "seed": seed,
        "steps": SA3_STEPS,
        "start_seconds": SIDE_SECONDS,
        "end_seconds": SEAM_END_SECONDS,
        "duration_seconds": MONTAGE_SAMPLES / SR,
    }


def base_record(
    variant_id: str,
    version: str,
    parent_id: Optional[str],
    prompt: Optional[str],
    seed: Optional[int],
    request: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    return {
        "id": variant_id,
        "engine": SA3_ENGINE,
        "version": version,
        "parent_id": parent_id,
        "status": "pending",
        "prompt": prompt,
        "seed": seed,
        "request": request,
        "meta": None,
        "elapsed_seconds": None,
        "returned_samples": None,
        "length_corrected": None,
        "metrics": {},
        "correction": None,
        "clip": None,
        "fill": None,
        "montage": None,
        "error": None,
    }


def finish(out_dir: Path, record: dict[str, Any]) -> dict[str, Any]:
    sidecar = out_dir / "variants" / f"{record['id']}.json"
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n")
    return record


def sidecar_is_done(out_dir: Path, variant_id: str) -> bool:
    sidecar = out_dir / "variants" / f"{variant_id}.json"
    clip = out_dir / "clips" / f"{variant_id}.flac"
    fill = out_dir / "fills" / f"{variant_id}.flac"
    if not sidecar.is_file() or not clip.is_file() or not fill.is_file():
        return False
    try:
        previous = json.loads(sidecar.read_text())
    except json.JSONDecodeError:
        return False
    return previous.get("status") == "ok"


def drop_variant(out_dir: Path, variant_id: str) -> None:
    """Remove a variant's artifacts so a failed/stale take cannot be played."""
    for sub, suffix in (("clips", ".flac"), ("fills", ".flac"), ("variants", ".json")):
        path = out_dir / sub / f"{variant_id}{suffix}"
        if path.is_file():
            path.unlink()


def write_variant_audio(
    out_dir: Path, record: dict[str, Any], fill: np.ndarray, clip: np.ndarray
) -> None:
    if fill.shape[0] != FILL_SAMPLES:
        raise RuntimeError(
            f"{record['id']} fill has {fill.shape[0]} frames, expected {FILL_SAMPLES}"
        )
    if clip.shape[0] != CLIP_SAMPLES:
        raise RuntimeError(
            f"{record['id']} clip has {clip.shape[0]} frames, expected {CLIP_SAMPLES}"
        )
    write_flac(out_dir / "fills" / f"{record['id']}.flac", fill)
    write_flac(out_dir / "clips" / f"{record['id']}.flac", clip)
    record["fill"] = f"fills/{record['id']}.flac"
    record["clip"] = f"clips/{record['id']}.flac"


def strip_private(record: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in record.items() if not key.startswith("_")}


# ---------------------------------------------------------------------------
# Variants
# ---------------------------------------------------------------------------


def run_reference(
    run2: dict[str, Any],
    out_dir: Path,
    levels_db: tuple[float, float],
    beat_samples: int,
) -> tuple[dict[str, Any], np.ndarray, np.ndarray]:
    """Sample-preserving copy of the run2 take used as the run3 reference."""
    reference = run2["variant"]
    record = base_record(
        REFERENCE_RAW_ID,
        "raw",
        None,
        reference.get("prompt"),
        reference.get("seed"),
        reference.get("request"),
    )
    record["meta"] = reference.get("meta")
    record["copied_from"] = {
        "clip": f"run2/clips/{RUN2_ID}.flac",
        "fill": f"run2/fills/{RUN2_ID}.flac",
        "montage": f"run2/montages/{RUN2_ID}.flac",
        "variant": f"run2/variants/{RUN2_ID}.json",
    }
    reference_fill = run2["reference_fill"]
    fill = np.ascontiguousarray(reference_fill, dtype=np.float32)
    clip = run2["clip"]
    metrics = measure(fill, clip, levels_db[0], levels_db[1], False)
    metrics.update(timing_metrics(fill, beat_samples))
    record["metrics"] = metrics
    write_variant_audio(out_dir, record, fill, clip)
    montage_path = out_dir / "montages" / f"{REFERENCE_RAW_ID}.flac"
    montage_path.parent.mkdir(parents=True, exist_ok=True)
    montage_path.write_bytes((run2["path"] / "montages" / f"{RUN2_ID}.flac").read_bytes())
    record["montage"] = f"montages/{REFERENCE_RAW_ID}.flac"
    record["status"] = "ok"
    print(
        f"  {REFERENCE_RAW_ID}: first onset {metrics['first_onset_ms']} ms, "
        f"second {metrics['second_onset_ms']} ms",
        flush=True,
    )
    return finish(out_dir, record), fill, detect_onsets(fill)


def run_new_raw(
    take: dict[str, Any],
    run2: dict[str, Any],
    out_dir: Path,
    levels_db: tuple[float, float],
    beat_samples: int,
    clients: dict[str, Any],
    force: bool,
    dry_run: bool,
) -> tuple[dict[str, Any], Optional[np.ndarray], Optional[np.ndarray]]:
    """Generate one raw take; fill/onsets are None unless the take is playable."""
    variant_id = take["id"]
    request = raw_request(take["prompt"], take["seed"])
    record = base_record(variant_id, "raw", None, take["prompt"], take["seed"], request)
    montage_samples = run2["montage"]
    montage_path = out_dir / "montages" / f"{variant_id}.flac"
    montage_path.parent.mkdir(parents=True, exist_ok=True)
    if not montage_path.is_file():
        write_flac(montage_path, montage_samples)
    record["montage"] = f"montages/{variant_id}.flac"

    if sidecar_is_done(out_dir, variant_id) and not force:
        print(f"  skip {variant_id} (clip exists; --force to regenerate)", flush=True)
        previous = json.loads((out_dir / "variants" / f"{variant_id}.json").read_text())
        fill = read_flac(out_dir / "fills" / f"{variant_id}.flac")
        clip = read_flac(out_dir / "clips" / f"{variant_id}.flac")
        peak_limited = bool((previous.get("metrics") or {}).get("peak_limited"))
        metrics = measure(fill, clip, levels_db[0], levels_db[1], peak_limited)
        metrics.update(timing_metrics(fill, beat_samples))
        previous["metrics"] = metrics
        return finish(out_dir, previous), fill, detect_onsets(fill)

    if dry_run:
        record["status"] = "dry_run"
        record["metrics"] = {
            "montage_duration_seconds": round(MONTAGE_SAMPLES / SR, 3),
            "fill_samples": FILL_SAMPLES,
        }
        return finish(out_dir, record), None, None

    montage_bytes = montage_path.read_bytes()
    try:
        started = time.time()
        response = call_with_retry(
            clients["sa3"].inpaint.remote, montage_bytes, f"{variant_id}-montage.flac", request
        )
        record["elapsed_seconds"] = round(time.time() - started, 3)
        with tempfile.TemporaryDirectory(prefix=f"seam-run3-{variant_id}-") as tmp:
            returned = decode_flac_bytes(response["audio"], Path(tmp), variant_id)
        returned_samples = int(returned.shape[0])
        returned, corrected = fit_length(returned, MONTAGE_SAMPLES)
        record["returned_samples"] = returned_samples
        record["length_corrected"] = corrected
        record["meta"] = response.get("meta") or {}

        fill = np.ascontiguousarray(
            returned[HEAD_SAMPLES : HEAD_SAMPLES + FILL_SAMPLES], dtype=np.float32
        )
        if fill.shape[0] != FILL_SAMPLES:
            raise RuntimeError(
                f"model returned {returned.shape[0]} frames; hole slice has {fill.shape[0]}, "
                f"expected {FILL_SAMPLES}"
            )
        level = rms_db(fill)
        if not np.isfinite(level) or peak_level(fill) < SILENCE_PEAK_FLOOR:
            raise RuntimeError(
                f"model returned a silent fill (rms {level} dB, peak {peak_level(fill)})"
            )
        fill, peak_limited = gain_fill(fill, levels_db[0], levels_db[1])
        clip, written_fill = build_clip(run2["parts"], fill, FILL_FADE_SECONDS)
        onsets = detect_onsets(written_fill)
        if onsets.size == 0:
            raise RuntimeError("no drum onset detected")
        metrics = measure(written_fill, clip, levels_db[0], levels_db[1], peak_limited)
        metrics.update(timing_metrics(written_fill, beat_samples))
        record["metrics"] = metrics
        write_variant_audio(out_dir, record, written_fill, clip)
        record["status"] = "ok"
        print(
            f"  {variant_id}: first onset {metrics['first_onset_ms']} ms, "
            f"second {metrics['second_onset_ms']} ms, fill {metrics['fill_rms_db']} dB, "
            f"{record['elapsed_seconds']:.1f}s",
            flush=True,
        )
        return finish(out_dir, record), written_fill, onsets
    except Exception as exc:  # noqa: BLE001 — one bad take must not kill the run
        record["status"] = "failed"
        record["error"] = f"{type(exc).__name__}: {exc}"
        drop_variant(out_dir, variant_id)
        print(f"  {variant_id} FAILED: {record['error']}", flush=True)
        return finish(out_dir, record), None, None


def run_derived(
    parent: dict[str, Any],
    mode: str,
    fill: np.ndarray,
    onsets: np.ndarray,
    parts: dict[str, np.ndarray],
    out_dir: Path,
    levels_db: tuple[float, float],
    beat_samples: int,
) -> dict[str, Any]:
    """Write one trim/grid correction of *parent*'s raw fill."""
    variant_id = parent["id"].replace("__raw", f"__{mode}")
    record = base_record(
        variant_id,
        mode,
        parent["id"],
        parent.get("prompt"),
        parent.get("seed"),
        parent.get("request"),
    )
    record["meta"] = parent.get("meta")
    try:
        corrected, correction = correct_timing(fill, onsets, beat_samples, mode)
    except ValueError as exc:
        record["status"] = "unavailable"
        record["error"] = f"{mode} unavailable: {exc}"
        drop_variant(out_dir, variant_id)
        print(f"  {variant_id} UNAVAILABLE: {exc}", flush=True)
        return finish(out_dir, record)
    record["correction"] = correction
    peak_limited = bool((parent.get("metrics") or {}).get("peak_limited"))
    clip, written_fill = build_clip(parts, corrected, FILL_FADE_SECONDS)
    metrics = measure(written_fill, clip, levels_db[0], levels_db[1], peak_limited)
    metrics.update(timing_metrics(written_fill, beat_samples))
    record["metrics"] = metrics
    write_variant_audio(out_dir, record, written_fill, clip)
    record["status"] = "ok"
    print(
        f"  {variant_id}: first onset {metrics['first_onset_ms']} ms, "
        f"second {metrics['second_onset_ms']} ms"
        + (f", ratio {correction['stretch_ratio']}" if correction.get("stretch_ratio") else ""),
        flush=True,
    )
    return finish(out_dir, record)


def derive_all(
    parent: dict[str, Any],
    fill: np.ndarray,
    onsets: np.ndarray,
    parts: dict[str, np.ndarray],
    out_dir: Path,
    levels_db: tuple[float, float],
    beat_samples: int,
) -> list[dict[str, Any]]:
    return [
        run_derived(parent, mode, fill, onsets, parts, out_dir, levels_db, beat_samples)
        for mode in ("trim", "grid")
    ]


# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------


def write_prompts(out_dir: Path) -> None:
    payload = {"version": 3, "sa3": SA3_PROMPTS, "styles": list(STYLES)}
    (out_dir / "prompts.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    )


def write_index(
    run2: dict[str, Any],
    out_dir: Path,
    records: list[dict[str, Any]],
    started_iso: str,
    seed_base: int,
) -> None:
    seam = run2["seam"]
    beat_samples = run2["beat_samples"]
    payload = {
        "version": 3,
        "created_at": started_iso,
        "run2": {
            "path": str(run2["path"]),
            "reference_id": RUN2_ID,
            "gap_ms": seam["gap_ms"],
            "a_hash": seam["a_hash"],
            "b_hash": seam["b_hash"],
            "a_end_seconds": seam.get("a_end_seconds"),
            "b_start_seconds": seam.get("b_start_seconds"),
            "first_onset_ms": next(
                (
                    (record.get("metrics") or {}).get("first_onset_ms")
                    for record in records
                    if record["id"] == REFERENCE_RAW_ID
                ),
                None,
            ),
        },
        "seam": {
            "gap_ms": seam["gap_ms"],
            "gap_seconds": seam["gap_ms"] / 1000.0,
            "clip_samples": CLIP_SAMPLES,
            "fill_samples": FILL_SAMPLES,
            "montage_samples": MONTAGE_SAMPLES,
            "fill_fade_seconds": FILL_FADE_SECONDS,
        },
        "tempo": {
            "incoming_bpm": run2["tempo"],
            "beat_samples": beat_samples,
            "beat_ms": round(beat_samples / SR * 1000.0, 3),
        },
        "targets": {
            "first_onset_ms_max": FIRST_ONSET_TARGET_MS,
            "beat_two_ms": round(beat_samples / SR * 1000.0, 3),
        },
        "models": {"sa3": SA3_ENGINE, "sa3_class": SA3_CLASS},
        "seed_base": seed_base,
        "prompts": SA3_PROMPTS,
        "variants": [strip_private(record) for record in records],
    }
    (out_dir / "index.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    )


SECTIONS = (
    ("Run2 reference — sa3__drum-a__s7003", REFERENCE_RAW_ID),
    ("New SA3 — immediate-roll", "immediate-roll"),
    ("New SA3 — immediate-pocket", "immediate-pocket"),
)
VERSIONS = ("raw", "trim", "grid")


def fmt(value: Optional[float], unit: str = "ms", digits: int = 1) -> str:
    if value is None:
        return "—"
    text = f"{value:.{digits}f}" if isinstance(value, float) else str(value)
    return f"{text} {unit}".strip()


def signed(value: Optional[float], digits: int = 1) -> str:
    if value is None:
        return "—"
    return f"{value:+.{digits}f} ms"


def players(record: dict[str, Any]) -> str:
    if record["status"] != "ok":
        return ""
    clip = html.escape(record["clip"])
    fill = html.escape(record["fill"])
    return (
        '<div class="players">'
        f'<div class="player"><span>clip · full song</span>'
        f'<audio controls preload="none" src="{clip}"></audio></div>'
        f'<div class="player"><span>fill · isolation</span>'
        f'<audio controls preload="none" src="{fill}"></audio></div>'
        "</div>"
    )


def correction_line(record: dict[str, Any]) -> str:
    correction = record.get("correction")
    if record["version"] == "raw":
        return "no correction — as generated" if record["status"] == "ok" else ""
    if not correction:
        return ""
    bits = [
        (
            f"trimmed {correction['trimmed_samples']} samples "
            f"({correction['trimmed_seconds'] * 1000.0:.1f} ms)"
        )
    ]
    if correction.get("stretch_ratio") is not None:
        bits.append(f"stretch ratio {correction['stretch_ratio']:.6f}")
        bits.append(f"anchor {correction['source_beat_two_samples']} samples")
    else:
        bits.append("no stretch")
    return " · ".join(bits)


def metrics_line(record: dict[str, Any]) -> str:
    metrics = record.get("metrics") or {}
    bits = [
        f"first onset {fmt(metrics.get('first_onset_ms'))}",
        f"second onset {fmt(metrics.get('second_onset_ms'))}",
        f"leading silence {fmt(metrics.get('leading_silence_ms'))}",
        f"beat-two error {signed(metrics.get('beat_two_error_ms'))}",
        f"beat-two peak {fmt(metrics.get('beat_two_peak_ms'))}",
        f"trailing silence {fmt(metrics.get('trailing_silence_ms'))}",
        f"fill RMS {fmt(metrics.get('fill_rms_db'), 'dB')}",
        f"peak {fmt(metrics.get('fill_peak'), '', 4)}",
        f"clip {fmt(metrics.get('clip_duration_seconds'), 's', 3)}",
    ]
    if metrics.get("peak_limited"):
        bits.append("peak-limited")
    return " · ".join(bits)


def variant_row(record: dict[str, Any]) -> str:
    status = html.escape(record["status"])
    body = [players(record), f'<p class="metrics">{html.escape(metrics_line(record))}</p>']
    correction = correction_line(record)
    if correction:
        body.append(f'<p class="correction">{html.escape(correction)}</p>')
    if record["status"] in ("failed", "unavailable"):
        body.append(f'<p class="error">{html.escape(str(record.get("error")))}</p>')
    elif record["status"] == "dry_run":
        body.append('<p class="note">dry run — nothing generated</p>')
    if record.get("length_corrected"):
        body.append(
            '<p class="note">model length corrected: returned '
            f'{record.get("returned_samples")} frames</p>'
        )
    return (
        f'<div class="row"><h4>{html.escape(record["version"])}'
        f'<span class="badge {status}">{status}</span></h4>{"".join(body)}</div>'
    )


def report_html(records: list[dict[str, Any]], run2: dict[str, Any], seed_base: int) -> str:
    beat_ms = run2["beat_samples"] / SR * 1000.0
    reference = next((r for r in records if r["id"] == REFERENCE_RAW_ID), None)
    reference_onset = ((reference or {}).get("metrics") or {}).get("first_onset_ms")
    title = "Seam Fill run3 — immediate drum onset and timing correction"
    diagnosis = (
        f"Run2's closest take ({RUN2_ID}) starts with "
        f"{fmt(reference_onset)} of near-silence before the first drum. Run3 targets a first "
        f"onset ≤{FIRST_ONSET_TARGET_MS:.0f} ms and the second hit exactly one incoming-tempo "
        f"beat later ({beat_ms:.1f} ms)."
    )
    caveat = (
        "trim removes the silent lead-in and leaves the performance's own timing and timbre "
        "intact; grid time-stretches so beat two lands on the beat and may smear transients. "
        "Both are rendered for every take so the artifacts can be rejected by ear. Metrics are "
        "recorded, not used for selection."
    )
    # Dead air at the fill's end is the model's decay, carried through by trim
    # (which pads to 61,431 frames) — never the splice. Say it with numbers.
    reference_raw = next((r for r in records if r["id"] == REFERENCE_RAW_ID), None)
    reference_trim = next((r for r in records if r["id"] == "run2-s7003__trim"), None)
    raw_trail = ((reference_raw or {}).get("metrics") or {}).get("trailing_silence_ms")
    trim_trail = ((reference_trim or {}).get("metrics") or {}).get("trailing_silence_ms")
    new_trails = [
        (r.get("metrics") or {}).get("trailing_silence_ms")
        for r in records
        if r["version"] == "raw" and r["id"].startswith("sa3__")
    ]
    new_trails = [value for value in new_trails if value is not None]
    ends = (
        f"Measured end-of-gap dead air: run2 raw {fmt(raw_trail)}, run2 trimmed {fmt(trim_trail)}"
        + (
            f", new raw takes {fmt(min(new_trails))}–{fmt(max(new_trails))}"
            if new_trails
            else ""
        )
        + ". trim zero-pads the end to fill 1.393 s, so that silence sits immediately before "
        "the incoming downbeat and is reported per row as trailing silence — it is the fill's "
        "own decay, not the splice."
    )

    sections: list[str] = []
    for heading, key in SECTIONS:
        if key == REFERENCE_RAW_ID:
            reference_take = next((r for r in records if r["id"] == REFERENCE_RAW_ID), None)
            cards = (
                [
                    _card(
                        reference_take,
                        [r for r in records if r["parent_id"] == REFERENCE_RAW_ID],
                        "selected run2 take, sample-preserving copy",
                    )
                ]
                if reference_take
                else []
            )
        else:
            cards = [
                _card(parent, [r for r in records if r["parent_id"] == parent["id"]], "")
                for parent in records
                if parent["version"] == "raw" and parent.get("style") == key
            ]
        if not cards:
            continue
        sections.append(
            f"<section><h2>{html.escape(heading)}</h2>"
            f'<div class="cards">{"".join(cards)}</div></section>'
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
  h1 {{ font-size: 20px; margin: 0 0 6px; }}
  .sub {{ color: #666; margin: 0 0 8px; }}
  section {{ margin-bottom: 32px; }}
  h2 {{ font-size: 16px; border-bottom: 1px solid #ddd; padding-bottom: 4px; }}
  .cards {{ display: grid; gap: 16px;
           grid-template-columns: repeat(auto-fill, minmax(340px, 1fr)); }}
  .card {{ border: 1px solid #ddd; border-radius: 8px; padding: 12px 14px; }}
  .card h3 {{ font-size: 13px; margin: 0 0 4px; overflow-wrap: anywhere; }}
  .row {{ border-top: 1px solid #eee; padding-top: 8px; margin-top: 10px; }}
  .row h4 {{ font-size: 12px; margin: 0 0 6px; text-transform: uppercase; letter-spacing: .05em; }}
  .badge {{ font-size: 10px; text-transform: none; letter-spacing: 0; margin-left: 6px;
           padding: 1px 6px; border-radius: 8px; border: 1px solid #bbb; color: #555; }}
  .badge.ok {{ color: #17692b; border-color: #17692b; }}
  .badge.failed, .badge.unavailable {{ color: #b00; border-color: #b00; }}
  .meta, .metrics, .correction, .note {{ color: #555; font-size: 12px; margin: 4px 0; }}
  .players {{ display: flex; flex-direction: column; gap: 4px; margin: 6px 0; }}
  .player span {{ font-size: 11px; text-transform: uppercase; letter-spacing: .05em; color: #777; }}
  audio {{ width: 100%; }}
  .error {{ color: #b00; font-size: 12px; }}
  details {{ margin-top: 8px; font-size: 12px; }}
  summary {{ cursor: pointer; }}
</style>
</head>
<body>
  <h1>{html.escape(title)}</h1>
  <p class="sub">{html.escape(diagnosis)}</p>
  <p class="sub">{html.escape(caveat)}</p>
  <p class="sub">{html.escape(ends)}</p>
  <p class="sub">Each clip is 7 s of the outgoing song, its last 8 arranged seconds, the fill,
  the incoming song's first 8 s, then 7 s more (31.393 s). The fill player isolates the
  1.393 s gap. Seeds are {seed_base + 1}–{seed_base + 3} per prompt.</p>
  {''.join(sections)}
</body>
</html>
"""


def _card(parent: dict[str, Any], derived: list[dict[str, Any]], tag: str) -> str:
    rows: list[str] = []
    for version in VERSIONS:
        record = (
            parent
            if version == "raw"
            else next((r for r in derived if r["version"] == version), None)
        )
        if record is None:
            rows.append(
                f'<div class="row"><h4>{version}<span class="badge unavailable">not run</span>'
                '</h4><p class="note">no parent audio — correction not attempted</p></div>'
            )
            continue
        rows.append(variant_row(record))
    prompt = parent.get("prompt") or ""
    prompt_block = (
        f"<details><summary>prompt</summary><p>{html.escape(prompt)}</p></details>"
        if prompt
        else ""
    )
    meta_bits = []
    if parent.get("seed") is not None:
        meta_bits.append(f"seed {parent['seed']}")
    meta_bits.append(f"status {parent['status']}")
    if tag:
        meta_bits.append(tag)
    return f"""
      <article class="card">
        <h3>{html.escape(parent['id'].replace('__raw', ''))}</h3>
        <p class="meta">{html.escape(' · '.join(meta_bits))}</p>
        {''.join(rows)}
        {prompt_block}
      </article>"""


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Seam Fill run3 (immediate drum onset + timing correction)"
    )
    parser.add_argument(
        "--run2",
        type=Path,
        default=Path("lab/poc-scripts/output/audio-inpaint/run2"),
        help="run2 output directory whose artifacts run3 reuses",
    )
    parser.add_argument("--out", type=Path, required=True, help="output directory for run3")
    parser.add_argument("--seed", type=int, default=8000, help="seed base")
    parser.add_argument("--force", action="store_true", help="regenerate existing ok takes")
    parser.add_argument("--dry-run", action="store_true", help="zero Modal calls")
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    started_iso = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    run2 = load_run2(args.run2)
    out_dir = args.out
    for sub in ("clips", "fills", "montages", "variants"):
        (out_dir / sub).mkdir(parents=True, exist_ok=True)

    beat_samples = run2["beat_samples"]
    levels_db = levels(run2["parts"])
    print(
        f"run2 seam gap {run2['seam']['gap_ms']} ms, incoming {run2['tempo']} BPM -> "
        f"beat {beat_samples} samples ({beat_samples / SR * 1000.0:.1f} ms), "
        f"A {levels_db[0]:.2f} dB / B {levels_db[1]:.2f} dB",
        flush=True,
    )
    write_prompts(out_dir)

    records: list[dict[str, Any]] = []
    reference_record, reference_fill, reference_onsets = run_reference(
        run2, out_dir, levels_db, beat_samples
    )
    records.append(reference_record)
    records.extend(
        derive_all(
            reference_record,
            reference_fill,
            reference_onsets,
            run2["parts"],
            out_dir,
            levels_db,
            beat_samples,
        )
    )

    clients: dict[str, Any] = {}
    takes = plan_raw_takes(args.seed)
    if not args.dry_run:
        clients["sa3"] = modal_client(SA3_APP, SA3_CLASS)

    generated = 0
    failed = 0
    for take in takes:
        outcome = run_new_raw(
            take,
            run2,
            out_dir,
            levels_db,
            beat_samples,
            clients,
            args.force,
            args.dry_run,
        )
        record, fill, onsets = outcome
        record["style"] = take["style"]
        records.append(record)
        if record["status"] == "ok":
            generated += 1
            records.extend(
                derive_all(
                    record, fill, onsets, run2["parts"], out_dir, levels_db, beat_samples
                )
            )
        elif record["status"] == "failed":
            failed += 1

    write_index(run2, out_dir, records, started_iso, args.seed)
    (out_dir / "report.html").write_text(
        report_html(records, run2, args.seed), encoding="utf-8"
    )

    total_new = len(takes)
    if failed == total_new:
        print(
            f"all {total_new} new SA3 generations failed; run2 reference corrections and "
            f"report are still written",
            flush=True,
        )
        return 1
    if args.dry_run:
        planned = sum(1 for record in records if record["status"] == "dry_run")
        print(
            f"dry run: {out_dir} — {planned} new take(s) planned, zero Modal calls; "
            f"run2 reference corrections written",
            flush=True,
        )
        return 0
    print(f"wrote {out_dir} — {generated}/{total_new} new raw takes ok", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
