"""Insert SimDPS Glue between arranged songs and write an offline listening report."""

from __future__ import annotations

import argparse
import math
import os
from collections.abc import Sequence
from datetime import UTC, datetime
from functools import cache
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from typing import Any

import numpy as np
import soundfile as sf

import gen_glue_elevenlabs as glue

DEFAULT_OUT_ROOT = Path(__file__).resolve().parent / "output" / "simdps-glue"
ENV_KEYS = tuple(key for key in glue.ENV_KEYS if key.startswith("SOW_"))


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--songset", default=glue.DEFAULT_SONGSET)
    parser.add_argument("--junctions", type=glue._ints, help="1-based comma-separated junctions")
    parser.add_argument("--beats", type=glue._ints, default=list(glue.DEFAULT_BEATS))
    parser.add_argument("--takes", type=int, default=1)
    parser.add_argument("--seed-base", type=int, default=0)
    parser.add_argument("--context-seconds", type=float, default=12.0)
    parser.add_argument("--steps", type=int, default=100)
    parser.add_argument("--out-root", type=Path, default=DEFAULT_OUT_ROOT)
    parser.add_argument("--run-name", default="run1")
    parser.add_argument("--force", action="store_true", help="allow reuse of an existing run")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--dry-run-items", type=int, choices=range(2, glue.MAX_SONGS + 1), default=2
    )
    args = parser.parse_args(argv)
    args.beats = list(dict.fromkeys(args.beats))
    if min(args.beats) < 1 or min(args.steps, args.takes) < 1:
        parser.error("--beats, --steps and --takes must be positive")
    if not math.isfinite(args.context_seconds) or args.context_seconds * glue.SR < 1:
        parser.error("--context-seconds must specify at least one frame")
    if not 0 <= args.seed_base <= 2**64 - args.takes:
        parser.error("all take seeds must fit in an unsigned 64-bit integer")
    if Path(args.run_name).name != args.run_name or args.run_name in {"", "..", "cache"}:
        parser.error("--run-name must be a directory name other than cache")
    return args


def montage_geometry(
    a_frames: int, b_frames: int, context_frames: int, beats: int, bpm: float
) -> tuple[int, int, int]:
    """Return gap start, gap end, and total montage frames, all at 44.1 kHz."""
    left = min(a_frames, context_frames)
    fill = glue.seconds_to_samples(beats * 60 / bpm)
    return left, left + fill, left + fill + min(b_frames, context_frames)


def dry_run(args: argparse.Namespace) -> int:
    items = glue.DRY_RUN_ITEMS[: args.dry_run_items]
    selected = glue.junction_indices(len(items), args.junctions)
    context_frames = glue.seconds_to_samples(args.context_seconds)
    print(f"Dry run · Songset {args.songset} · {len(items)} simulated songs · {glue.SR} Hz")
    for junction in selected:
        a_ms, _ = items[junction - 1]
        b_ms, bpm = items[junction]
        for beats in args.beats:
            start, end, total = montage_geometry(
                round(a_ms * glue.SR / 1000),
                round(b_ms * glue.SR / 1000),
                context_frames,
                beats,
                bpm,
            )
            print(
                f"j{junction} · {bpm:g} BPM · {beats} beats · "
                f"gap [{start}, {end}) = {end - start:,} frames · "
                f"montage {total:,} frames · guide {b_ms / 1000:g} s · "
                f"{args.takes} take(s) · seed {args.seed_base} onward · {args.steps} steps"
            )
    return 0


def arrange_guide(item: glue.SongsetItem, out_root: Path, r2) -> np.ndarray:
    key = f"{item.hash_prefix}/stems/instrumental.flac"
    directory = out_root / "cache" / "stems" / item.hash_prefix
    source = directory / "instrumental.flac"
    decoded = directory / "instrumental.wav"
    if not source.is_file():
        if not r2.file_exists(key):
            raise FileNotFoundError(f"missing R2 instrumental stem {key}")
        r2.download_file(key, source)
    if decoded.is_file():
        samples = glue.read_wav(decoded)
    else:
        with TemporaryDirectory(dir=directory) as staging:
            pending = Path(staging) / decoded.name
            samples = glue.decode_audio(source, pending, codec="pcm_s24le")
            pending.replace(decoded)
    return glue.arranged_audio(samples, item.steps, f"instrumental guide for {item.title}")


@cache
def modal_inpainter():
    import modal
    from modal_apps.simdps_inpaint_app import APP_NAME

    return modal.Cls.from_name(APP_NAME, "SimDPSInpaint")()


def write_pair_baseline(
    run_dir: Path,
    base: Path,
    junction: int,
    a: np.ndarray,
    b: np.ndarray,
    item: glue.SongsetItem,
    bpm: float,
) -> dict[str, Any]:
    mix, (record,) = glue.mix_songset([a, b], [item], [bpm])
    full = base / "full" / "baseline.flac"
    seam = base / "seams" / "baseline.flac"
    glue.write_flac(run_dir / full, mix)
    glue.write_flac(
        run_dir / seam,
        glue.junction_excerpt(
            mix,
            len(a),
            glue.seconds_to_samples(record["gap_seconds"]),
            glue.seconds_to_samples(record["crossfade_seconds"]),
        ),
    )
    return {
        **record,
        "id": f"j{junction}-baseline",
        "label": glue.baseline_label(record),
        "summary": glue.baseline_summary(record),
        "status": "ok",
        "files": {"full": full.as_posix(), "seam": seam.as_posix()},
    }


def write_take_audio(
    run_dir: Path,
    base: Path,
    name: str,
    data: bytes,
    take: dict[str, Any],
    a: np.ndarray,
    b: np.ndarray,
) -> np.ndarray:
    with sf.SoundFile(BytesIO(data)) as wav:
        if (wav.format, wav.subtype, wav.samplerate, wav.channels, len(wav)) != (
            "WAV",
            "FLOAT",
            glue.SR,
            glue.CHANNELS,
            take["target_frames"],
        ):
            raise ValueError("Modal returned audio outside the expected float WAV geometry")
        raw = wav.read(dtype="float32", always_2d=True)
    raw_path = base / "glues" / f"{name}-raw.wav"
    (run_dir / raw_path).parent.mkdir(parents=True, exist_ok=True)
    (run_dir / raw_path).write_bytes(data)
    take["files"]["generation"] = raw_path.as_posix()
    if not np.isfinite(raw).all():
        raise ValueError("model fill contains non-finite samples")

    edge = min(glue.seconds_to_samples(glue.EDGE_SECONDS), max(1, len(raw) // 2))
    staged, stage = glue.stage_edges(raw, a[-edge:], b[:edge])
    fill = glue.apply_fades(staged)
    window = glue.seconds_to_samples(glue.EXCERPT_SECONDS)
    seam = glue.splice(a[-window:], fill, b[:window])
    left, right = min(window, len(a)), min(window, len(a)) + len(fill)
    fade = min(glue.seconds_to_samples(glue.FADE_SECONDS), len(fill) // 2)
    a_joint = glue.joint_metrics(seam, left, fade_after=fade)
    b_joint = glue.joint_metrics(seam, right, fade_before=fade)
    metrics = {
        "glue_rms_db": glue.rms_db(fill),
        "joint": {"a": a_joint, "b": b_joint},
        **stage,
    }
    take["status"], take["reasons"] = glue.evaluate_status(metrics, splice_ok=True)
    take["metrics"] = {key: _finite_values(value) for key, value in metrics.items()}
    take["glue_seconds"] = len(fill) / glue.SR
    take["metric_summary"] = (
        f"{len(fill):,} frames · peak {metrics['peak']:.3f} · "
        f"joint level steps {a_joint['step_db']:.2f} / {b_joint['step_db']:.2f} dB"
    )
    for kind, directory, samples in (
        ("glue", "glues", fill),
        ("seam", "seams", seam),
        ("full", "full", glue.splice(a, fill, b)),
    ):
        path = base / directory / f"{name}.flac"
        glue.write_flac(run_dir / path, samples)
        take["files"][kind] = path.as_posix()
    return fill


def _finite_values(value: Any) -> Any:
    if isinstance(value, list):
        return [_finite_values(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def take_names(args: argparse.Namespace):
    for beats in args.beats:
        for number in range(1, args.takes + 1):
            suffix = f"-t{number}" if args.takes > 1 else ""
            yield beats, number, f"beats-{beats}{suffix}"


def process_junction(
    args: argparse.Namespace,
    run_dir: Path,
    junction: int,
    a: np.ndarray,
    b: np.ndarray,
    item: glue.SongsetItem,
    bpm: float,
    entry: dict[str, Any],
    r2,
) -> dict[str, np.ndarray]:
    from botocore.exceptions import BotoCoreError, ClientError

    base = Path("junctions") / f"j{junction}"
    entry["baseline"] = write_pair_baseline(run_dir, base, junction, a, b, item, bpm)
    guide_error = None
    guide_data = b""
    try:
        guide = arrange_guide(item, args.out_root, r2)
        path = base / "guide.flac"
        glue.write_flac(run_dir / path, guide, subtype="PCM_24")
        guide_data = (run_dir / path).read_bytes()
        entry["guide"] = path.as_posix()
        entry["guide_frames"] = len(guide)
        entry["input_summary"] = f"instrumental search source {len(guide) / glue.SR:.2f} s"
    except (BotoCoreError, ClientError, OSError, RuntimeError, ValueError) as exc:
        guide_error = str(exc)
        entry["error"] = guide_error
        entry["input_summary"] = "instrumental guide unavailable"

    context = glue.seconds_to_samples(args.context_seconds)
    glues = {}
    for beats, number, name in take_names(args):
        start, end, total = montage_geometry(len(a), len(b), context, beats, bpm)
        take: dict[str, Any] = {
            "id": f"j{junction}-{name}",
            "label": name,
            "beats": beats,
            "take": number,
            "seed": args.seed_base + number - 1,
            "steps": args.steps,
            "target_frames": end - start,
            "target_seconds": (end - start) / glue.SR,
            "gap_frames": [start, end],
            "montage_frames": total,
            "status": "failed",
            "files": {},
            "metric_summary": f"{end - start:,} requested frames",
        }
        entry["takes"].append(take)
        try:
            path = base / "montages" / f"beats-{beats}.flac"
            if number == 1:
                silence = np.zeros((end - start, glue.CHANNELS), dtype=np.float32)
                glue.write_flac(run_dir / path, glue.splice(a[-start:], silence, b[: total - end]))
            take["files"]["montage"] = path.as_posix()
            if guide_error is not None:
                raise RuntimeError(guide_error)
            montage = (run_dir / path).read_bytes()
            started = perf_counter()
            try:
                data, meta = modal_inpainter().inpaint.remote(
                    montage,
                    guide_data,
                    (start, end),
                    take["seed"],
                    args.steps,
                )
            except Exception as exc:
                raise RuntimeError(f"SimDPS inpaint failed ({type(exc).__name__}): {exc}") from exc
            take["model"] = meta
            take["wall_seconds"] = round(perf_counter() - started, 3)
            take["summary"] = (
                f"seed {take['seed']} · {args.steps} steps · {meta['sampling_seconds']:.2f} s sampling"
            )
            fill = write_take_audio(run_dir, base, name, data, take, a, b)
            if take["status"] != "failed":
                glues[name] = fill
        except (OSError, RuntimeError, ValueError) as exc:
            take["status"] = "failed"
            take["error"] = str(exc)
        detail = take.get("error") or ", ".join(take.get("reasons", []))
        print(f"  {take['id']}: {take['status']} {detail}", flush=True)
    return glues


def build_full_set(
    args: argparse.Namespace,
    run_dir: Path,
    songs: list[np.ndarray],
    fills: dict[int, dict[str, np.ndarray]],
    full_set: dict[str, Any],
) -> None:
    if len(fills) != len(songs) - 1:
        full_set["skipped"] = "junction subset selected (--junctions)"
        return
    for beats, number, name in take_names(args):
        missing = [f"j{i}" for i, takes in fills.items() if name not in takes]
        entry: dict[str, Any] = {
            "id": f"full-set-{name}",
            "label": f"full set — {name}",
            "beats": beats,
            "take": number,
            "status": "unavailable",
            "files": {},
        }
        if missing:
            entry["error"] = f"{name} unavailable at {', '.join(missing)}"
        else:
            path = Path("full-set") / f"{name}.flac"
            assembled = glue.assemble_set(songs, [takes[name] for takes in fills.values()])
            glue.write_flac(run_dir / path, assembled)
            entry.update(
                status="ok", seconds=len(assembled) / glue.SR, files={"full": path.as_posix()}
            )
        full_set["takes"].append(entry)


def execute_run(args: argparse.Namespace) -> int:
    import psycopg
    from stream_of_worship.admin.services.r2 import R2Client
    from modal_apps.simdps_inpaint_app import MODEL_ID, UPSTREAM_REVISION

    glue.load_env_file(keys=ENV_KEYS)
    glue.require_env(ENV_KEYS)
    run_dir = args.out_root / args.run_name
    glue._prepare_run_dir(run_dir, args.force)
    r2 = R2Client(
        bucket=os.environ["SOW_R2_BUCKET"], endpoint_url=os.environ["SOW_R2_ENDPOINT_URL"]
    )
    print(f"Songset {args.songset} → {run_dir}", flush=True)
    with psycopg.connect(os.environ["SOW_DATABASE_URL"]) as conn:
        items, recordings = glue.fetch_songset(conn, args.songset)
    selected = glue.junction_indices(len(items), args.junctions)

    songs, tempos, contexts = [], [], []
    for position, item in enumerate(items, start=1):
        rec = recordings[item.hash_prefix]
        if position > 1 and (
            not rec.tempo_bpm or not math.isfinite(rec.tempo_bpm) or rec.tempo_bpm < 0
        ):
            raise ValueError(f"recording {item.hash_prefix} has no usable tempo")
        source = glue.ensure_source_audio(item.hash_prefix, args.out_root, lambda: r2)
        arranged = glue.arranged_audio(source, item.steps, f"item {position} ({item.title})")
        path = Path("context") / f"song-{position:02d}-{item.hash_prefix[:8]}.flac"
        glue.write_flac(run_dir / path, arranged)
        songs.append(arranged)
        tempos.append(rec.tempo_bpm)
        contexts.append(
            {
                "position": item.position,
                "song_id": item.song_id,
                "title": item.title,
                "hash_prefix": item.hash_prefix,
                "tempo_bpm": rec.tempo_bpm,
                "steps": len(item.steps),
                "key": " ".join(part for part in (rec.musical_key, rec.musical_mode) if part),
                "arranged_seconds": len(arranged) / glue.SR,
                "file": path.as_posix(),
            }
        )

    baseline_path = "full-set/baseline.flac"
    baseline, records = glue.mix_songset(songs, items[1:], tempos[1:])
    glue.write_flac(run_dir / baseline_path, baseline)
    full_set = {
        "baseline": {
            "id": "full-set-baseline",
            "label": "full set — baseline (production gaps/crossfades)",
            "status": "ok",
            "seconds": len(baseline) / glue.SR,
            "files": {"full": baseline_path},
        },
        "takes": [],
    }
    index = {
        "version": 1,
        "run": args.run_name,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "songset_id": args.songset,
        "review": "simdps-glue",
        "report_title": "SimDPS Glue POC",
        "report_heading": "SimDPS Glue",
        "report_summary": (
            f"SimDPS-Multi · {args.context_seconds:g} s context per side · {args.steps} steps · "
            "Listen to the raw fill and mark vocals heard if you hear singing."
        ),
        "model_id": MODEL_ID,
        "upstream_revision": UPSTREAM_REVISION,
        "sample_rate": glue.SR,
        "beats": args.beats,
        "context_seconds": args.context_seconds,
        "steps": args.steps,
        "items": contexts,
        "junctions": [],
        "full_set": full_set,
    }
    del baseline
    fills = {}
    for junction in selected:
        entry = {
            "id": f"j{junction}",
            "index": junction,
            "from": contexts[junction - 1],
            "to": contexts[junction],
            "tempo_bpm": tempos[junction],
            "beat_seconds": glue.beat_seconds(tempos[junction]),
            "takes": [],
        }
        index["junctions"].append(entry)
        print(f"  j{junction}: {entry['from']['title']} → {entry['to']['title']}", flush=True)
        fills[junction] = process_junction(
            args,
            run_dir,
            junction,
            songs[junction - 1],
            songs[junction],
            items[junction],
            tempos[junction],
            entry,
            r2,
        )
    full_set["baseline"]["junctions"] = records
    build_full_set(args, run_dir, songs, fills, full_set)
    glue._write_index(run_dir, index)
    report = run_dir / "report.html"
    report.write_text(glue.report_html(index), encoding="utf-8")
    print(f"Listening report {report}", flush=True)
    failed = any(take["status"] == "failed" for j in index["junctions"] for take in j["takes"])
    return 2 if failed else 0


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    return dry_run(args) if args.dry_run else execute_run(args)


if __name__ == "__main__":
    raise SystemExit(main())
