#!/usr/bin/env python3
"""Compare BPM detection across librosa, madmom, BeatNet, prod-v4, and prod-v5.

Runs five BPM detection libraries on a set of Chinese worship songs and reports
per-song BPM + timing, aggregate runtime, BPM distribution, CPS distribution,
and CSV output.

prod-v5 replaces librosa's flat ``start_bpm=80`` scalar prior with an
intelligent lognormal prior whose center is derived from the song's own
Characters-Per-Second (CPS) computed from its timestamped lyrics (LRC).

Timing methodology:
  - librosa / prod-v4 / prod-v5: receive pre-loaded (y, sr) arrays. Timing
    captures only the algorithm (onset_strength + tempo estimation), not audio
    loading.
  - madmom / BeatNet: receive a file path. Timing includes internal audio
    loading + processing, reflecting real end-to-end cost.
  Model weight loading (one-time) is excluded from per-song timing via lazy
  singletons, so the mean-per-song projection is not inflated by startup cost.
"""

from __future__ import annotations

import csv
import math
import time
import tempfile
import unicodedata
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

import librosa
from librosa.feature.rhythm import tempo as librosa_tempo
import numpy as np
import typer
from scipy import stats

from poc.songset_constructor.config import RunConfig
from poc.songset_constructor.db import fetch_catalog_pool
from stream_of_worship.admin.config import AdminConfig
from stream_of_worship.admin.db.client import DatabaseClient
from stream_of_worship.admin.services.lrc_parser import parse_lrc
from stream_of_worship.admin.services.r2 import R2Client
from stream_of_worship.db.connection import ConnectionProvider

app = typer.Typer(
    help="Compare BPM detection across librosa, madmom, BeatNet, prod-v4, and prod-v5"
)

DEFAULT_SONG_IDS = [
    "yu_mi_man_bu_e46c5fe7",
    "mei_hao_de_chuang_zao_3d42d76e",
    "song_zan_gui_yu_mi_d0e41287",
]

SR = 22050
HOP = 512
CATALOG_SIZE = 99

LIBRARIES = ["librosa_raw", "madmom", "beatnet", "prod_v4", "prod_v5"]
LIBRARY_LABELS = {
    "librosa_raw": "librosa (raw)",
    "madmom": "madmom",
    "beatnet": "BeatNet",
    "prod_v4": "prod-v4",
    "prod_v5": "prod-v5",
}


@dataclass
class SongInfo:
    song_id: str
    hash_prefix: str
    stored_bpm: Optional[float]
    title: str


@dataclass
class LibraryResult:
    bpm: Optional[float]
    elapsed: float


def build_r2_client() -> R2Client:
    config = AdminConfig.load()
    return R2Client(config.r2_bucket, config.r2_endpoint_url, config.r2_region)


def build_db_client() -> DatabaseClient:
    config = AdminConfig.load()
    conn = ConnectionProvider(config.get_connection_url())
    return DatabaseClient(conn)


def download_audio(r2_client: R2Client, hash_prefix: str, dest: Path) -> bool:
    s3_key = f"{hash_prefix}/audio.mp3"
    try:
        if not r2_client.file_exists(s3_key):
            return False
        r2_client.download_file(s3_key, dest)
        return dest.exists()
    except Exception:
        if dest.exists():
            dest.unlink()
        return False


def resolve_song(db_client: DatabaseClient, song_id: str) -> Optional[SongInfo]:
    recording = db_client.get_recording_by_song_id(song_id)
    if recording is None:
        return None
    song = db_client.get_song(song_id)
    title = song.title if song else song_id
    return SongInfo(
        song_id=song_id,
        hash_prefix=recording.hash_prefix,
        stored_bpm=recording.tempo_bpm,
        title=title,
    )


def resolve_songs(
    song_ids: list[str],
    all_catalog: bool,
    limit: Optional[int],
) -> list[SongInfo]:
    if all_catalog:
        config = RunConfig(env_file=None, songs=2, pool_limit=9999)
        pool = fetch_catalog_pool(config)
        candidates = pool[:limit] if limit else pool
        return [
            SongInfo(
                song_id=c.song_id,
                hash_prefix=c.recording_hash_prefix,
                stored_bpm=c.tempo_bpm,
                title=c.title,
            )
            for c in candidates
        ]

    ids = song_ids if song_ids else DEFAULT_SONG_IDS
    db_client = build_db_client()
    try:
        songs: list[SongInfo] = []
        for sid in ids:
            info = resolve_song(db_client, sid)
            if info is None:
                typer.echo(f"Warning: song not found in DB: {sid}", err=True)
                continue
            songs.append(info)
        return songs
    finally:
        db_client.close()


# --- CPS (Characters-Per-Second) pipeline ---


def _is_ws_or_punct(ch: str) -> bool:
    return ch.isspace() or unicodedata.category(ch).startswith(("P", "S"))


def count_lyric_chars(text: str) -> int:
    """Count lyric units: CJK characters individually, ASCII alphanumeric runs as 1 token each.

    Whitespace and punctuation/symbol characters are excluded.
    """
    count = 0
    ascii_run = 0
    for ch in text:
        if _is_ws_or_punct(ch):
            if ascii_run:
                count += 1
                ascii_run = 0
            continue
        if ord(ch) > 0x2E7F:  # CJK and surrounding ranges
            if ascii_run:
                count += 1
                ascii_run = 0
            count += 1
        else:  # ASCII letter/digit
            ascii_run += 1
    if ascii_run:
        count += 1
    return count


def compute_cps(lrc_content: str) -> tuple[Optional[float], Optional[dict]]:
    """Compute Characters-Per-Second from LRC content.

    Vocal span = first → last timed LRC line timestamp.
    Returns (cps, meta_dict) or (None, {"reason": ...}) on failure.
    """
    try:
        parsed = parse_lrc(lrc_content)
    except ValueError:
        return None, {"reason": "no valid LRC lines"}
    if len(parsed.lines) < 2:
        return None, {"reason": "fewer than 2 timed lines"}
    total_chars = sum(count_lyric_chars(line.text) for line in parsed.lines)
    span = parsed.lines[-1].time_seconds - parsed.lines[0].time_seconds
    if span <= 0:
        return None, {"reason": "non-positive span"}
    cps = total_chars / span
    return cps, {
        "lines": len(parsed.lines),
        "chars": total_chars,
        "span_s": span,
        "first_ts": parsed.lines[0].time_seconds,
        "last_ts": parsed.lines[-1].time_seconds,
    }


def cps_bucket_label(cps: Optional[float]) -> Optional[str]:
    """Return nominal CPS bucket: 'slow', 'moderate', 'fast', or None."""
    if cps is None:
        return None
    if cps < 1.5:
        return "slow"
    elif cps <= 2.8:
        return "moderate"
    else:
        return "fast"


def cps_to_prior(cps: Optional[float]) -> Optional[stats.rv_continuous]:
    """Build a lognormal prior distribution from CPS, or None for fallback.

    When ``cps is None`` (LRC missing), returns None so the caller falls back
    to ``start_bpm=80``.
    """
    if cps is None:
        return None
    if cps < 1.5:
        mean, std = 70.0, 12.0
    elif cps <= 2.8:
        mean, std = 105.0, 15.0
    else:
        mean, std = 135.0, 15.0
    var = std**2
    mu = math.log(mean**2 / math.sqrt(var + mean**2))
    sigma = math.sqrt(math.log(1 + var / mean**2))
    return stats.lognorm(scale=math.exp(mu), s=sigma)


def v5_prior_label(cps: Optional[float]) -> str:
    """Human-readable prior label for the per-song table Prior column."""
    if cps is None:
        return "fallback_start_bpm_80"
    if cps < 1.5:
        return "cps(Slow,70,12)"
    elif cps <= 2.8:
        return "cps(Moderate,105,15)"
    else:
        return "cps(Fast,135,15)"


def v5_prior_source(cps: Optional[float]) -> str:
    """Machine-readable prior source tag for CSV."""
    if cps is None:
        return "fallback_start_bpm_80"
    if cps < 1.5:
        return "cps_slow"
    elif cps <= 2.8:
        return "cps_moderate"
    else:
        return "cps_fast"


def _prior_label_for_lib(lib: str, cps: Optional[float]) -> str:
    """Return the Prior column value for a given library and CPS."""
    if lib == "prod_v4":
        return "start_bpm=80"
    if lib == "prod_v5":
        return v5_prior_label(cps)
    return "—"


# --- Lazy model singletons (loaded once, excluded from per-song timing) ---

_madmom_rnn = None
_madmom_dbn = None
_beatnet_estimator = None


def _get_madmom_processors():
    global _madmom_rnn, _madmom_dbn
    if _madmom_rnn is None:
        import madmom

        _madmom_rnn = madmom.features.RNNBeatProcessor()
        _madmom_dbn = madmom.features.beats.DBNBeatTrackingProcessor(fps=100)
    return _madmom_rnn, _madmom_dbn


def _get_beatnet_estimator():
    global _beatnet_estimator
    if _beatnet_estimator is None:
        import sys
        import types

        # BeatNet imports pyaudio at module level, but it's only used in 'stream'
        # mode. Stub it out so offline mode works without portaudio/pyaudio.
        if "pyaudio" not in sys.modules:
            pyaudio_stub = types.ModuleType("pyaudio")
            pyaudio_stub.PyAudio = type("PyAudio", (), {})
            pyaudio_stub.paFloat32 = 0
            sys.modules["pyaudio"] = pyaudio_stub

        from BeatNet.BeatNet import BeatNet

        _beatnet_estimator = BeatNet(
            1, mode="offline", inference_model="DBN", plot=[], thread=False
        )
    return _beatnet_estimator


# --- Timed library wrappers ---


def timed_librosa_raw(y: np.ndarray, sr: int) -> LibraryResult:
    t0 = time.perf_counter()
    try:
        onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=HOP)
        tempo = librosa_tempo(onset_envelope=onset_env, sr=sr, hop_length=HOP, start_bpm=80)
        if hasattr(tempo, "__iter__"):
            tempo = float(tempo[0])
        bpm = float(tempo)
        elapsed = time.perf_counter() - t0
        return LibraryResult(bpm=bpm, elapsed=elapsed)
    except Exception as exc:
        elapsed = time.perf_counter() - t0
        typer.echo(f"  librosa (raw) ERROR: {exc}", err=True)
        return LibraryResult(bpm=None, elapsed=elapsed)


def timed_madmom(audio_path: Path) -> LibraryResult:
    rnn, dbn = _get_madmom_processors()
    t0 = time.perf_counter()
    try:
        act = rnn(str(audio_path))
        beats = dbn(act)
        if len(beats) > 1:
            bpm = float(60.0 / np.median(np.diff(beats)))
        else:
            bpm = 0.0
        elapsed = time.perf_counter() - t0
        return LibraryResult(bpm=bpm, elapsed=elapsed)
    except Exception as exc:
        elapsed = time.perf_counter() - t0
        typer.echo(f"  madmom ERROR: {exc}", err=True)
        return LibraryResult(bpm=None, elapsed=elapsed)


def timed_beatnet(audio_path: Path) -> LibraryResult:
    estimator = _get_beatnet_estimator()
    t0 = time.perf_counter()
    try:
        output = estimator.process(str(audio_path))
        beats = output[:, 0]
        if len(beats) > 1:
            bpm = float(60.0 / np.median(np.diff(beats)))
        else:
            bpm = 0.0
        elapsed = time.perf_counter() - t0
        return LibraryResult(bpm=bpm, elapsed=elapsed)
    except Exception as exc:
        elapsed = time.perf_counter() - t0
        typer.echo(f"  BeatNet ERROR: {exc}", err=True)
        return LibraryResult(bpm=None, elapsed=elapsed)


def timed_prod_v4(y: np.ndarray, sr: int) -> LibraryResult:
    t0 = time.perf_counter()
    try:
        onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=HOP)
        tempo_primary = librosa_tempo(onset_envelope=onset_env, sr=sr, hop_length=HOP, start_bpm=80)
        if hasattr(tempo_primary, "__iter__"):
            tempo_primary = float(tempo_primary[0])
        tempo_primary = float(tempo_primary)

        if tempo_primary > 120.0:
            tempo_alt = librosa_tempo(
                onset_envelope=onset_env, sr=sr, hop_length=HOP, start_bpm=60.0
            )
            if hasattr(tempo_alt, "__iter__"):
                tempo_alt = float(tempo_alt[0])
            tempo_alt = float(tempo_alt)
            if abs(tempo_alt - tempo_primary / 2.0) < 8.0 and 65.0 <= tempo_alt <= 100.0:
                bpm = tempo_alt
            else:
                bpm = tempo_primary
        elif tempo_primary < 60.0:
            tempo_alt = librosa_tempo(
                onset_envelope=onset_env, sr=sr, hop_length=HOP, start_bpm=120.0
            )
            if hasattr(tempo_alt, "__iter__"):
                tempo_alt = float(tempo_alt[0])
            tempo_alt = float(tempo_alt)
            if abs(tempo_alt - 2.0 * tempo_primary) < 8.0 and 110.0 <= tempo_alt <= 180.0:
                bpm = tempo_alt
            else:
                bpm = tempo_primary
        else:
            bpm = tempo_primary

        elapsed = time.perf_counter() - t0
        return LibraryResult(bpm=bpm, elapsed=elapsed)
    except Exception as exc:
        elapsed = time.perf_counter() - t0
        typer.echo(f"  prod-v4 ERROR: {exc}", err=True)
        return LibraryResult(bpm=None, elapsed=elapsed)


def timed_prod_v5(y: np.ndarray, sr: int, cps: Optional[float]) -> LibraryResult:
    """prod-v5: same as v4 but with a CPS-derived lognormal prior (or start_bpm=80 fallback).

    When a CPS-derived prior is active, the v4 octave guard is skipped — the
    prior is the v5 signal that resolves octave ambiguity. When ``cps is None``
    (LRC missing), v5 falls back to the exact v4 behavior.
    """
    t0 = time.perf_counter()
    try:
        onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=HOP)
        prior = cps_to_prior(cps)
        if prior is not None:
            tempo_primary = librosa_tempo(
                onset_envelope=onset_env, sr=sr, hop_length=HOP, prior=prior
            )
        else:
            tempo_primary = librosa_tempo(
                onset_envelope=onset_env, sr=sr, hop_length=HOP, start_bpm=80
            )
        if hasattr(tempo_primary, "__iter__"):
            tempo_primary = float(tempo_primary[0])
        tempo_primary = float(tempo_primary)

        if prior is not None:
            # Prior already encodes the half/double-time belief — skip octave guard.
            bpm = tempo_primary
        elif tempo_primary > 120.0:
            tempo_alt = librosa_tempo(
                onset_envelope=onset_env, sr=sr, hop_length=HOP, start_bpm=60.0
            )
            if hasattr(tempo_alt, "__iter__"):
                tempo_alt = float(tempo_alt[0])
            tempo_alt = float(tempo_alt)
            if abs(tempo_alt - tempo_primary / 2.0) < 8.0 and 65.0 <= tempo_alt <= 100.0:
                bpm = tempo_alt
            else:
                bpm = tempo_primary
        elif tempo_primary < 60.0:
            tempo_alt = librosa_tempo(
                onset_envelope=onset_env, sr=sr, hop_length=HOP, start_bpm=120.0
            )
            if hasattr(tempo_alt, "__iter__"):
                tempo_alt = float(tempo_alt[0])
            tempo_alt = float(tempo_alt)
            if abs(tempo_alt - 2.0 * tempo_primary) < 8.0 and 110.0 <= tempo_alt <= 180.0:
                bpm = tempo_alt
            else:
                bpm = tempo_primary
        else:
            bpm = tempo_primary

        elapsed = time.perf_counter() - t0
        return LibraryResult(bpm=bpm, elapsed=elapsed)
    except Exception as exc:
        elapsed = time.perf_counter() - t0
        typer.echo(f"  prod-v5 ERROR: {exc}", err=True)
        return LibraryResult(bpm=None, elapsed=elapsed)


# --- Output formatting helpers ---


def octave_flag(library_bpm: Optional[float], stored_bpm: Optional[float]) -> str:
    if library_bpm is None or stored_bpm is None or stored_bpm == 0:
        return "—"
    ratio = library_bpm / stored_bpm
    if abs(ratio - 1.0) <= 0.1:
        return "≈1"
    if abs(ratio - 2.0) <= 0.2:
        return "×2"
    if abs(ratio - 0.5) <= 0.1:
        return "×0.5"
    return f"{ratio:.1f}×"


def format_duration(seconds: float) -> str:
    if seconds < 60:
        return f"~{seconds:.0f}s"
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    return f"~{seconds:.0f}s ({minutes}m {secs}s)"


def format_bpm(bpm: Optional[float]) -> str:
    if bpm is None:
        return "ERROR"
    return f"{bpm:.1f}"


def format_time(elapsed: float) -> str:
    return f"{elapsed:.1f}s"


def print_per_song_table(
    idx: int,
    total: int,
    song: SongInfo,
    results: dict[str, LibraryResult],
    cps_info: Optional[dict],
) -> None:
    print(f"\nSong {idx}/{total}: {song.title} ({song.song_id})")
    print(f"  Hash: {song.hash_prefix} | Stored BPM: {song.stored_bpm or '—'}")

    cps = cps_info["cps"] if cps_info else None
    meta = cps_info["cps_meta"] if cps_info else None
    lrc_available = cps_info["lrc_available"] if cps_info else False

    if cps is not None and meta:
        bucket = cps_bucket_label(cps)
        bucket_title = bucket.title() if bucket else "—"
        print(
            f"  CPS: {cps:.2f}  (chars={meta['chars']}, lines={meta['lines']}, "
            f"span={meta['span_s']:.1f}s, first={meta['first_ts']:.2f}s, "
            f"last={meta['last_ts']:.2f}s)"
        )
        print(f"  LRC: lyrics.lrc  |  CPS bucket: {bucket_title} (nominal)")
    elif lrc_available:
        reason = meta.get("reason", "unknown") if meta else "unknown"
        print(f"  CPS: —  (LRC present but no valid CPS: {reason})")
        print("  LRC: lyrics.lrc  |  CPS bucket: —")
    else:
        print("  CPS: —  (no LRC)")
        print("  LRC: —  |  CPS bucket: —")

    print(f"\n  {'Library':<16} {'BPM':>7} {'Octave*':>8} {'Time':>7} {'Prior':>24}")
    print(f"  {'─' * 16} {'─' * 7} {'─' * 8} {'─' * 7} {'─' * 24}")
    for lib in LIBRARIES:
        r = results[lib]
        flag = octave_flag(r.bpm, song.stored_bpm)
        prior = _prior_label_for_lib(lib, cps)
        print(
            f"  {LIBRARY_LABELS[lib]:<16} {format_bpm(r.bpm):>7} {flag:>8} "
            f"{format_time(r.elapsed):>7} {prior:>24}"
        )
    print("\n  * Octave = ratio of library BPM to stored DB BPM (≈1, ×2, ×0.5 flags doubling)")


def print_aggregate_runtime(
    all_results: list[dict[str, LibraryResult]],
    all_catalog: bool,
    cps_results: Optional[list[dict]] = None,
) -> None:
    n_songs = len(all_results)
    print(f"\n{'=' * 60}")
    print("=== Aggregate Runtime ===")
    print(f"{'':>30} ({n_songs} song{'s' if n_songs != 1 else ''})")

    means: dict[str, float] = {}
    totals: dict[str, float] = {}

    for lib in LIBRARIES:
        times = [r[lib].elapsed for r in all_results if r[lib].bpm is not None]
        total = sum(times)
        mean = total / len(times) if times else 0.0
        totals[lib] = total
        means[lib] = mean

    fastest = min(means.values()) if means else 1.0
    if fastest <= 0:
        fastest = 1.0

    print(f"\n  {'Library':<16} {'Total':>10} {'Mean/song':>11} {'Rel×':>6}")
    print(f"  {'─' * 16} {'─' * 10} {'─' * 11} {'─' * 6}")
    for lib in LIBRARIES:
        rel = means[lib] / fastest if fastest > 0 else 0.0
        print(
            f"  {LIBRARY_LABELS[lib]:<16} {format_time(totals[lib]):>10} "
            f"{format_time(means[lib]):>11} {rel:>5.1f}×"
        )

    if cps_results is not None:
        n_with_cps = sum(1 for cr in cps_results if cr["cps"] is not None)
        n_fallback = len(cps_results) - n_with_cps
        print(f"\n  Prior source: CPS ({n_with_cps} songs) / start_bpm=80 ({n_fallback} songs)")

    if not all_catalog and n_songs < CATALOG_SIZE:
        print(f"\n  Projected {CATALOG_SIZE}-song catalog sweep:")
        for lib in LIBRARIES:
            projected = means[lib] * CATALOG_SIZE
            print(f"    {LIBRARY_LABELS[lib]:<16} {format_duration(projected)}")


def print_bpm_distribution(
    all_results: list[dict[str, LibraryResult]],
    stored_bpms: list[Optional[float]],
) -> None:
    print(f"\n{'=' * 60}")
    print("=== BPM Distribution ===")

    def print_dist(label: str, bpms: list[float]) -> None:
        valid = [b for b in bpms if b is not None and b > 0]
        if not valid:
            print(f"\n  {label:<16} (no valid BPMs)")
            return
        counts = Counter(round(b, 1) for b in valid)
        unique = len(counts)
        lo = min(counts)
        hi = max(counts)
        spread = hi - lo
        print(f"\n  {label:<16} unique={unique}  range=[{lo:.1f}, {hi:.1f}]  spread={spread:.1f}")
        for bpm, count in sorted(counts.items()):
            print(f"    {bpm:>7.1f}   {'#' * count} ({count})")

    for lib in LIBRARIES:
        bpms = [r[lib].bpm for r in all_results]
        print_dist(LIBRARY_LABELS[lib], bpms)

    stored_valid = [b for b in stored_bpms if b is not None and b > 0]
    print_dist("stored (DB)", stored_valid)


def _bpm_bucket(bpm: Optional[float]) -> Optional[str]:
    """Bucket a BPM into 'slow' (<90), 'moderate' (90–120), or 'fast' (>120)."""
    if bpm is None or bpm <= 0:
        return None
    if bpm < 90:
        return "slow"
    elif bpm <= 120:
        return "moderate"
    else:
        return "fast"


def print_cps_distribution(
    cps_results: list[dict],
    all_results: list[dict[str, LibraryResult]],
    stored_bpms: list[Optional[float]],
) -> None:
    print(f"\n{'=' * 60}")
    print("=== CPS Distribution ===")

    valid_entries = [
        (i, cps_results[i]["cps"])
        for i in range(len(cps_results))
        if cps_results[i]["cps"] is not None
    ]
    n_with_lrc = len(valid_entries)
    n_missing = len(cps_results) - n_with_lrc
    print(f"  N={n_with_lrc} songs with LRC  ({n_missing} missing)")

    if n_with_lrc == 0:
        print("  No CPS data available.")
        return

    cps_values = [c for _, c in valid_entries]
    lo = min(cps_values)
    hi = max(cps_values)
    sorted_cps = sorted(cps_values)
    median = sorted_cps[n_with_lrc // 2]
    mean = sum(cps_values) / n_with_lrc
    print(f"  CPS range = [{lo:.2f}, {hi:.2f}]  median = {median:.2f}  mean = {mean:.2f}")

    # k-means 3-cluster on per-song CPS
    kmeans_labels: dict[int, str] = {}  # song_idx -> bucket label
    if n_with_lrc >= 3:
        from sklearn.cluster import KMeans

        arr = np.array(cps_values).reshape(-1, 1)
        km = KMeans(n_clusters=3, n_init=10, random_state=42)
        raw_labels = km.fit_predict(arr)
        centers = km.cluster_centers_.flatten()
        sorted_idx = np.argsort(centers)
        label_map = {
            int(sorted_idx[0]): "slow",
            int(sorted_idx[1]): "moderate",
            int(sorted_idx[2]): "fast",
        }
        sorted_centers = centers[sorted_idx]
        cut_lower = (sorted_centers[0] + sorted_centers[1]) / 2.0
        cut_upper = (sorted_centers[1] + sorted_centers[2]) / 2.0

        bucket_counts = {"slow": 0, "moderate": 0, "fast": 0}
        for j, (song_idx, _) in enumerate(valid_entries):
            bucket = label_map[int(raw_labels[j])]
            kmeans_labels[song_idx] = bucket
            bucket_counts[bucket] += 1

        print("\n  k-means 3-cluster cutoffs (sorted ascending):")
        print(
            f"    Slow      CPS < {cut_lower:.2f}   (n={bucket_counts['slow']})   " f"nominal < 1.5"
        )
        print(
            f"    Moderate  {cut_lower:.2f} ≤ CPS < {cut_upper:.2f}  "
            f"(n={bucket_counts['moderate']})   nominal 1.5–2.8"
        )
        print(
            f"    Fast      CPS ≥ {cut_upper:.2f}   (n={bucket_counts['fast']})   " f"nominal > 2.8"
        )
        delta_lower = cut_lower - 1.5
        delta_upper = cut_upper - 2.8
        print(
            f"\n  Nominal-vs-empirical deltas: lower {delta_lower:+.2f}, "
            f"upper {delta_upper:+.2f}"
        )
    else:
        print("\n  (fewer than 3 CPS values — k-means skipped, using nominal buckets)")

    def print_crosstab(label: str, bpms: list[Optional[float]]) -> None:
        rows = ["slow", "moderate", "fast"]
        table = {r: {c: 0 for c in rows} for r in rows}
        total = 0
        diagonal = 0
        for song_idx, _ in valid_entries:
            cps_bucket = kmeans_labels.get(song_idx)
            if cps_bucket is None:
                cps_bucket = cps_bucket_label(cps_results[song_idx]["cps"])
            if cps_bucket is None:
                continue
            bpm = bpms[song_idx] if song_idx < len(bpms) else None
            b_bucket = _bpm_bucket(bpm)
            if b_bucket is None:
                continue
            table[cps_bucket][b_bucket] += 1
            total += 1
            if cps_bucket == b_bucket:
                diagonal += 1

        print(f"\n=== CPS bucket × BPM bucket ({label}) ===")
        print(f"                 {'BPM<90':>7} {'90–120':>7} {'>120':>7}")
        for r in rows:
            vals = [table[r][c] for c in rows]
            print(f"  {r.capitalize():<12} {vals[0]:>7} {vals[1]:>7} {vals[2]:>7}")
        if total > 0:
            pct = diagonal / total * 100
            print(f"  Diagonal mass: {diagonal}/{total} = {pct:.0f}%  " f"(CPS-BPM agreement)")

    print_crosstab("stored DB", stored_bpms)
    for lib in LIBRARIES:
        bpms = [r[lib].bpm for r in all_results]
        print_crosstab(LIBRARY_LABELS[lib], bpms)


def write_csv(
    all_results: list[dict[str, LibraryResult]],
    songs: list[SongInfo],
    cps_results: list[dict],
    output_dir: Path,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = output_dir / f"bpm_comparison_{timestamp}.csv"

    fieldnames = [
        "song_id",
        "hash_prefix",
        "title",
        "stored_bpm",
        "librosa_bpm",
        "librosa_sec",
        "madmom_bpm",
        "madmom_sec",
        "beatnet_bpm",
        "beatnet_sec",
        "prod_v4_bpm",
        "prod_v4_sec",
        "lrc_available",
        "cps",
        "cps_chars",
        "cps_lines",
        "cps_span_s",
        "cps_first_ts",
        "cps_last_ts",
        "cps_bucket",
        "prod_v5_bpm",
        "prod_v5_sec",
        "prod_v5_prior",
    ]

    col_map = {
        "librosa_bpm": "librosa_raw",
        "librosa_sec": "librosa_raw",
        "madmom_bpm": "madmom",
        "madmom_sec": "madmom",
        "beatnet_bpm": "beatnet",
        "beatnet_sec": "beatnet",
        "prod_v4_bpm": "prod_v4",
        "prod_v4_sec": "prod_v4",
        "prod_v5_bpm": "prod_v5",
        "prod_v5_sec": "prod_v5",
    }

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for song, results, cps_info in zip(songs, all_results, cps_results):
            row: dict[str, object] = {
                "song_id": song.song_id,
                "hash_prefix": song.hash_prefix,
                "title": song.title,
                "stored_bpm": song.stored_bpm if song.stored_bpm is not None else "",
            }
            for csv_col, lib_key in col_map.items():
                r = results[lib_key]
                if csv_col.endswith("_bpm"):
                    row[csv_col] = f"{r.bpm:.1f}" if r.bpm is not None else "ERROR"
                else:
                    row[csv_col] = f"{r.elapsed:.3f}"

            cps = cps_info["cps"]
            meta = cps_info["cps_meta"]
            row["lrc_available"] = "true" if cps_info["lrc_available"] else "false"
            if cps is not None and meta:
                row["cps"] = f"{cps:.4f}"
                row["cps_chars"] = meta.get("chars", "")
                row["cps_lines"] = meta.get("lines", "")
                row["cps_span_s"] = f"{meta.get('span_s', 0):.3f}"
                row["cps_first_ts"] = f"{meta.get('first_ts', 0):.3f}"
                row["cps_last_ts"] = f"{meta.get('last_ts', 0):.3f}"
                row["cps_bucket"] = cps_bucket_label(cps) or ""
            else:
                row["cps"] = ""
                row["cps_chars"] = ""
                row["cps_lines"] = ""
                row["cps_span_s"] = ""
                row["cps_first_ts"] = ""
                row["cps_last_ts"] = ""
                row["cps_bucket"] = ""
            row["prod_v5_prior"] = v5_prior_source(cps)

            writer.writerow(row)

    return csv_path


@app.command()
def main(
    song_ids: list[str] = typer.Option(
        None, "--song-id", help="Song ID (repeatable); defaults to 3 POC songs"
    ),
    all_catalog: bool = typer.Option(
        False, "--all-catalog", help="Process entire catalog (Phase 2)"
    ),
    limit: Optional[int] = typer.Option(
        None, "--limit", help="Cap number of songs (with --all-catalog)"
    ),
    csv_output: bool = typer.Option(True, "--csv/--no-csv", help="Write results CSV (default on)"),
) -> None:
    """Compare BPM detection across librosa, madmom, BeatNet, prod-v4, and prod-v5."""
    songs = resolve_songs(song_ids, all_catalog, limit)
    if not songs:
        typer.echo("No songs to process.", err=True)
        raise typer.Exit(1)

    typer.echo(f"Resolving {len(songs)} song(s)...", err=True)

    r2_client = build_r2_client()
    output_dir = Path(__file__).resolve().parent / "output"

    all_results: list[dict[str, LibraryResult]] = []
    processed_songs: list[SongInfo] = []
    stored_bpms: list[Optional[float]] = []
    cps_results: list[dict] = []

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        for i, song in enumerate(songs, start=1):
            audio_path = tmpdir_path / f"{song.hash_prefix}.mp3"
            ok = download_audio(r2_client, song.hash_prefix, audio_path)
            if not ok:
                typer.echo(
                    f"\nSong {i}/{len(songs)}: {song.title} ({song.song_id})\n"
                    f"  Hash: {song.hash_prefix}\n  DOWNLOAD FAILED",
                    err=True,
                )
                continue

            size_mb = audio_path.stat().st_size / (1024 * 1024)
            typer.echo(
                f"\nSong {i}/{len(songs)}: {song.title} ({song.song_id})\n"
                f"  Hash: {song.hash_prefix} | Stored BPM: {song.stored_bpm or '—'}\n"
                f"  Downloaded audio.mp3 ({size_mb:.2f} MB)",
                err=True,
            )

            y, sr = librosa.load(str(audio_path), sr=SR, mono=True)

            lrc_text = r2_client.download_lrc_content(song.hash_prefix)
            if lrc_text:
                cps, cps_meta = compute_cps(lrc_text)
            else:
                cps, cps_meta = None, None
            cps_info = {
                "lrc_available": lrc_text is not None,
                "cps": cps,
                "cps_meta": cps_meta,
            }
            cps_results.append(cps_info)

            results: dict[str, LibraryResult] = {
                "librosa_raw": timed_librosa_raw(y, sr),
                "madmom": timed_madmom(audio_path),
                "beatnet": timed_beatnet(audio_path),
                "prod_v4": timed_prod_v4(y, sr),
                "prod_v5": timed_prod_v5(y, sr, cps),
            }

            all_results.append(results)
            processed_songs.append(song)
            stored_bpms.append(song.stored_bpm)

            print_per_song_table(i, len(songs), song, results, cps_info)

    if not all_results:
        typer.echo("No songs processed successfully.", err=True)
        raise typer.Exit(1)

    print_aggregate_runtime(all_results, all_catalog, cps_results)
    print_bpm_distribution(all_results, stored_bpms)
    print_cps_distribution(cps_results, all_results, stored_bpms)

    if csv_output:
        csv_path = write_csv(all_results, processed_songs, cps_results, output_dir)
        typer.echo(f"\nCSV written to: {csv_path}", err=True)


if __name__ == "__main__":
    app()
