#!/usr/bin/env python3
"""Test multiple librosa tempo estimation strategies to find one that
produces realistic BPM diversity for Chinese worship music.

Strategies tested:
  A. Current: hop=4096, defaults (baseline)
  B. hop=512, defaults
  C. hop=512, start_bpm=80 (bias toward slower tempos)
  D. hop=512, max_tempo=160 (cap to avoid double-time)
  E. hop=512, start_bpm=80 + max_tempo=160 (combined)
  F. hop=512, uniform prior 60-180 (no bias, wider range)
  G. hop=512, log-normal prior centered at 90 (worship music bias)
  H. librosa.beat.beat_track, hop=512 (DP beat tracker)
  I. hop=512, tempogram multi-peak analysis
"""

from __future__ import annotations

import os
import tempfile
from collections import Counter
from pathlib import Path

import librosa
import numpy as np
import scipy.stats

from poc.songset_constructor.config import RunConfig
from poc.songset_constructor.db import fetch_catalog_pool
from stream_of_worship.admin.config import AdminConfig
from stream_of_worship.admin.services.r2 import R2Client

SR = 22050
HOP = 512
SAMPLE_SIZE = 20


def build_r2_client() -> R2Client:
    config = AdminConfig.load()
    return R2Client(config.r2_bucket, config.r2_endpoint_url, config.r2_region)


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


# --- Strategy functions ---

def strategy_a_current(y, sr, onset_env):
    """Current production: hop=4096, defaults."""
    # This one uses hop=4096 onset env, computed separately in the loop
    pass  # handled specially


def strategy_b_default(onset_env, sr):
    """hop=512, default params."""
    return float(librosa.beat.tempo(onset_envelope=onset_env, sr=sr, hop_length=HOP)[0])


def strategy_c_start_80(onset_env, sr):
    """hop=512, start_bpm=80."""
    return float(librosa.beat.tempo(onset_envelope=onset_env, sr=sr, hop_length=HOP, start_bpm=80)[0])


def strategy_d_max_160(onset_env, sr):
    """hop=512, max_tempo=160."""
    return float(librosa.beat.tempo(onset_envelope=onset_env, sr=sr, hop_length=HOP, max_tempo=160)[0])


def strategy_e_combined(onset_env, sr):
    """hop=512, start_bpm=80 + max_tempo=160."""
    return float(librosa.beat.tempo(onset_envelope=onset_env, sr=sr, hop_length=HOP, start_bpm=80, max_tempo=160)[0])


def strategy_f_uniform_prior(onset_env, sr):
    """hop=512, uniform prior 60-180."""
    prior = scipy.stats.uniform(60, 120)  # 60 to 180
    return float(librosa.beat.tempo(onset_envelope=onset_env, sr=sr, hop_length=HOP, prior=prior)[0])


def strategy_g_lognorm_90(onset_env, sr):
    """hop=512, log-normal prior centered at 90."""
    prior = scipy.stats.lognorm(loc=np.log(90), scale=90, s=0.8)
    return float(librosa.beat.tempo(onset_envelope=onset_env, sr=sr, hop_length=HOP, prior=prior)[0])


def strategy_h_beat_track(onset_env, sr):
    """librosa.beat.beat_track, hop=512."""
    tempo, beats = librosa.beat.beat_track(onset_envelope=onset_env, sr=sr, hop_length=HOP)
    return float(tempo)


def strategy_i_tempogram_multi(onset_env, sr):
    """hop=512, tempogram multi-peak: pick the strongest peak, but also
    check half/double tempo and pick whichever is closer to 60-140 range."""
    tg = librosa.feature.tempogram(onset_envelope=onset_env, sr=sr, hop_length=HOP)
    # Average the tempogram across time to get a global tempo profile
    avg_tg = np.mean(tg, axis=1)
    # Get the tempo values for each bin
    freqs = librosa.tempo_frequencies(tg.shape[0], hop_length=HOP, sr=sr)
    # Find the top peak
    top_idx = np.argmax(avg_tg)
    top_bpm = float(freqs[top_idx])
    if top_bpm <= 0:
        top_bpm = 120.0
    # Check half and double
    candidates = [top_bpm, top_bpm / 2, top_bpm * 2]
    # Pick the one closest to the 60-140 range center (100)
    best = min(candidates, key=lambda b: abs(b - 100) if b > 0 else 999)
    return float(best)


STRATEGIES = [
    ("A_current_h4096", None),  # special handling
    ("B_default", strategy_b_default),
    ("C_start80", strategy_c_start_80),
    ("D_max160", strategy_d_max_160),
    ("E_start80+max160", strategy_e_combined),
    ("F_uniform60-180", strategy_f_uniform_prior),
    ("G_lognorm90", strategy_g_lognorm_90),
    ("H_beat_track", strategy_h_beat_track),
    ("I_tempogram_multi", strategy_i_tempogram_multi),
]


def main() -> None:
    config = RunConfig(env_file=None)
    pool = fetch_catalog_pool(config)
    print(f"pool_size={len(pool)}")

    r2_client = build_r2_client()

    step = max(1, len(pool) // SAMPLE_SIZE)
    sample = pool[::step][:SAMPLE_SIZE]
    print(f"sample_size={len(sample)}")

    results: dict[str, list[float]] = {name: [] for name, _ in STRATEGIES}
    stored: list[float] = []

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        for i, candidate in enumerate(sample, start=1):
            audio_path = tmpdir_path / f"{candidate.recording_hash_prefix}.mp3"
            ok = download_audio(r2_client, candidate.recording_hash_prefix, audio_path)
            if not ok:
                print(f"  [{i}/{len(sample)}] {candidate.title}: DOWNLOAD FAILED")
                continue

            y, sr = librosa.load(str(audio_path), sr=SR, mono=True)
            onset_512 = librosa.onset.onset_strength(y=y, sr=sr, hop_length=HOP)
            onset_4096 = librosa.onset.onset_strength(y=y, sr=sr, hop_length=4096)

            stored_bpm = candidate.tempo_bpm
            stored.append(stored_bpm if stored_bpm is not None else -1)

            # Strategy A: current production
            bpm_a = float(librosa.beat.tempo(onset_envelope=onset_4096, sr=sr, hop_length=4096)[0])
            results["A_current_h4096"].append(bpm_a)

            # All other strategies use hop=512 onset env
            for name, func in STRATEGIES[1:]:
                bpm = func(onset_512, sr)
                results[name].append(bpm)

            row = [f"  [{i}/{len(sample)}] {candidate.title[:25]}"]
            row.append(f"stored={stored_bpm:.1f}")
            for name, _ in STRATEGIES:
                row.append(f"{name.split('_')[0]}={results[name][-1]:.1f}")
            print(" | ".join(row))

    print("\n" + "=" * 120)
    print("BPM DISTRIBUTION BY STRATEGY")
    print("=" * 120)

    # Stored
    counts = Counter(round(b, 1) for b in stored if b > 0)
    print(f"\nStored (DB):")
    print(f"  unique={len(counts)} range=[{min(counts):.1f}, {max(counts):.1f}]")
    for bpm, count in sorted(counts.items()):
        print(f"  {bpm:>7.1f} BPM: {'#' * count} ({count})")

    for name, _ in STRATEGIES:
        bpms = results[name]
        counts = Counter(round(b, 1) for b in bpms)
        unique = len(counts)
        lo = min(counts) if counts else 0
        hi = max(counts) if counts else 0
        spread = hi - lo
        # Count how many are in worship range (65-140)
        in_range = sum(c for b, c in counts.items() if 65 <= b <= 140)
        print(f"\n{name}:")
        print(f"  unique={unique} range=[{lo:.1f}, {hi:.1f}] spread={spread:.1f} in_worship_range={in_range}/{len(bpms)}")
        for bpm, count in sorted(counts.items()):
            print(f"  {bpm:>7.1f} BPM: {'#' * count} ({count})")

    # Summary table
    print("\n" + "=" * 120)
    print("SUMMARY")
    print("=" * 120)
    print(f"{'Strategy':<25} {'Unique':>7} {'Min':>7} {'Max':>7} {'Spread':>8} {'In 65-140':>10}")
    print("-" * 70)
    for name, _ in STRATEGIES:
        bpms = results[name]
        counts = Counter(round(b, 1) for b in bpms)
        unique = len(counts)
        lo = min(counts) if counts else 0
        hi = max(counts) if counts else 0
        spread = hi - lo
        in_range = sum(c for b, c in counts.items() if 65 <= b <= 140)
        print(f"{name:<25} {unique:>7} {lo:>7.1f} {hi:>7.1f} {spread:>8.1f} {in_range:>10}/{len(bpms)}")


if __name__ == "__main__":
    main()
