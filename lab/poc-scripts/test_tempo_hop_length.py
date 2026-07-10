#!/usr/bin/env python3
"""Test tempo estimation with different hop_length values to confirm
the hypothesis that hop_length=4096 produces only two discrete BPM values.

Downloads a sample of songs from R2, estimates tempo with various
hop_length settings, and prints the BPM distribution for each.
"""

from __future__ import annotations

import os
import tempfile
from collections import Counter
from pathlib import Path

import librosa
import numpy as np

from poc.songset_constructor.config import RunConfig
from poc.songset_constructor.db import fetch_catalog_pool
from stream_of_worship.admin.config import AdminConfig
from stream_of_worship.admin.services.r2 import R2Client

HOP_LENGTHS = [512, 1024, 2048, 4096]
SAMPLE_SIZE = 20
SR = 22050


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


def estimate_tempo(audio_path: Path, hop_length: int) -> float:
    y, sr = librosa.load(str(audio_path), sr=SR, mono=True)
    onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop_length)
    tempo = librosa.beat.tempo(onset_envelope=onset_env, sr=sr, hop_length=hop_length)
    if hasattr(tempo, "__iter__"):
        tempo = float(tempo[0])
    return float(tempo)


def main() -> None:
    config = RunConfig(env_file=None)
    pool = fetch_catalog_pool(config)
    print(f"pool_size={len(pool)}")

    r2_client = build_r2_client()

    # Sample across the pool: take every Nth song to get diversity
    step = max(1, len(pool) // SAMPLE_SIZE)
    sample = pool[::step][:SAMPLE_SIZE]
    print(f"sample_size={len(sample)}")

    results: dict[int, list[float]] = {hop: [] for hop in HOP_LENGTHS}
    stored: list[float] = []

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        for i, candidate in enumerate(sample, start=1):
            audio_path = tmpdir_path / f"{candidate.recording_hash_prefix}.mp3"
            ok = download_audio(r2_client, candidate.recording_hash_prefix, audio_path)
            if not ok:
                print(f"  [{i}/{len(sample)}] {candidate.title}: DOWNLOAD FAILED")
                continue
            stored_bpm = candidate.tempo_bpm
            stored.append(stored_bpm if stored_bpm is not None else -1)
            row = [f"  [{i}/{len(sample)}] {candidate.title[:30]} stored={stored_bpm:.1f}"]
            for hop in HOP_LENGTHS:
                bpm = estimate_tempo(audio_path, hop)
                results[hop].append(bpm)
                row.append(f"hop{hop}={bpm:.1f}")
            print(" | ".join(row))

    print("\n=== BPM distribution by hop_length ===")
    for hop in HOP_LENGTHS:
        bpms = results[hop]
        counts = Counter(round(b, 1) for b in bpms)
        print(f"\nhop_length={hop} (frame_rate={SR/hop:.2f} Hz, min_lag_bpm={60*SR/hop/2:.1f}, max_lag_bpm={60*SR/hop/10:.1f}):")
        print(f"  unique_values={len(counts)}")
        for bpm, count in sorted(counts.items()):
            print(f"  {bpm:>7.1f} BPM: {count}")

    print(f"\nstored (from DB):")
    counts = Counter(round(b, 1) for b in stored if b > 0)
    for bpm, count in sorted(counts.items()):
        print(f"  {bpm:>7.1f} BPM: {count}")


if __name__ == "__main__":
    main()
