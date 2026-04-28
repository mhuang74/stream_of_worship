#!/usr/bin/env python3
"""Experiment: compute per-line signals for LRC timing evaluation.

Runs six candidate signals on two reference songs:
  - dan_dan_ai_mi_249  (known bad: audible timing drift)
  - wo_yao_yi_xin_cheng_xie_mi_247  (known good: hand-verified)

Outputs per-song CSVs and a markdown summary for signal comparison.

Usage:
    # All signals except MFA (not installed):
    PYTHONPATH=src:. uv run --extra score_lrc_base,poc_qwen3_align \
        python poc/experiment_lrc_signals.py

    # Without qwen3-forcedaligner (score_lrc_base only):
    PYTHONPATH=src:. uv run --extra score_lrc_base \
        python poc/experiment_lrc_signals.py --skip-qwen3

    # Single song:
    PYTHONPATH=src:. uv run --extra score_lrc_base,poc_qwen3_align \
        python poc/experiment_lrc_signals.py --song dan_dan_ai_mi_249

    # Skip slow signals:
    PYTHONPATH=src:. uv run --extra score_lrc_base,poc_qwen3_align \
        python poc/experiment_lrc_signals.py --skip-dtw --skip-tone

Install notes:
    silero-vad is loaded via torch hub (no extra install needed).
    qwen3-forcedaligner: included in poc_qwen3_align extra (qwen-asr package).
      Model Qwen/Qwen3-ForcedAligner-0.6B is downloaded on first run (~1.2GB).
      5-minute audio limit — both reference stems are ~4.7 min (just under limit).
    MFA: not installed. Install via conda if needed:
      conda install -c conda-forge montreal-forced-aligner
      mfa model download acoustic mandarin_mfa
      mfa model download dictionary mandarin_mfa
"""

from __future__ import annotations

import csv
import json
import os
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import typer

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

app = typer.Typer(help="LRC signal experiment: compare per-line timing signals on reference songs")

SONGS = {
    "dan_dan_ai_mi_249": {"label": "BAD", "hash_prefix": "5b445438847a"},
    "wo_yao_yi_xin_cheng_xie_mi_247": {"label": "GOOD", "hash_prefix": "c105e75972f7"},
    "zhe_shi_sheng_jie_zhi_di_259": {"label": "", "hash_prefix": "a3c1abf9de68"},
    "yuan_tian_huan_xi_245": {"label": "", "hash_prefix": "a1c7b8907693"},
    "zhu_mi_shi_wo_li_liang_321": {"label": "", "hash_prefix": "10d07a66c47e"},
    "en_dian_zhi_lu_265": {"label": "", "hash_prefix": "f69d12afd22b"},
    "shen_gao_yang_248": {"label": "", "hash_prefix": "02fa022169b7"},
    "wo_yao_quan_xin_zan_mei_244": {"label": "", "hash_prefix": "0ca4ea6a43f3"},
    "huo_zhu_wei_yao_jing_bai_mi_212": {"label": "", "hash_prefix": "de04b8ee6048"},
    "he_deng_en_dian_262": {"label": "", "hash_prefix": "aa8ee305a093"},
    "cong_zao_chen_dao_ye_wan_130": {"label": "", "hash_prefix": "e5c16c2f35f2"},
    "chai_qian_wo_566": {"label": "", "hash_prefix": "50a54ecf7488"},
    "cong_xin_he_yi_195": {"label": "", "hash_prefix": "18ade95e29dc"},
    "dan_qin_ge_chang_zan_mei_mi_401": {"label": "", "hash_prefix": "3428cfdce4f8"},
    "dao_gao_351": {"label": "", "hash_prefix": "d48247f4fb2f"},
    "bao_gui_shi_jia_314": {"label": "", "hash_prefix": "b39a98477bc5"},
    "ren_ding_mi_242": {"label": "", "hash_prefix": "59fb1a19c566"},
    "feng_sheng_de_ying_xu_250": {"label": "", "hash_prefix": "345a3688e1bc"},
    "ye_su_de_ming_246": {"label": "", "hash_prefix": "496148cbd9f9"},
    "ai_ke_yi_zai_geng_duo_yi_dian_dian_241": {"label": "", "hash_prefix": "11a027c6df54"},
    "wo_yao_kan_jian_146": {"label": "", "hash_prefix": "9d2f0d65995b"},
}

OUTPUT_BASE = Path(__file__).parent / "experiment_output"
from stream_of_worship.core.paths import get_cache_dir as _get_cache_dir
CACHE_DIR = _get_cache_dir()
DB_PATH = Path.home() / ".config" / "sow-admin" / "db" / "sow.db"
R2_ENDPOINT = "https://6c80769fe5aa4be53908b83c3d0454cd.r2.cloudflarestorage.com"
R2_BUCKET = "stream-of-worship"


# ── LRC parsing ──────────────────────────────────────────────────────────────


@dataclass
class LRCLine:
    time_seconds: float
    text: str


def parse_lrc(content: str) -> list[LRCLine]:
    pattern = r"\[(\d{2}):(\d{2})\.(\d{2,3})\](.*)"
    lines = []
    for raw in content.split("\n"):
        m = re.match(pattern, raw.strip())
        if not m:
            continue
        t = int(m.group(1)) * 60 + int(m.group(2)) + int(m.group(3).ljust(3, "0")[:3]) / 1000.0
        text = m.group(4).strip()
        if text:
            lines.append(LRCLine(time_seconds=t, text=text))
    if not lines:
        raise ValueError("No valid LRC lines found")
    return lines


# ── R2 download ───────────────────────────────────────────────────────────────


def download_r2(s3_key: str, dest: Path) -> bool:
    import boto3
    from botocore.exceptions import ClientError

    ak = os.environ.get("SOW_R2_ACCESS_KEY_ID")
    sk = os.environ.get("SOW_R2_SECRET_ACCESS_KEY")
    if not ak or not sk:
        typer.echo(
            "R2 credentials not set (SOW_R2_ACCESS_KEY_ID / SOW_R2_SECRET_ACCESS_KEY)", err=True
        )
        return False

    client = boto3.client(
        "s3",
        endpoint_url=R2_ENDPOINT,
        aws_access_key_id=ak,
        aws_secret_access_key=sk,
        region_name="auto",
    )
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        client.download_file(R2_BUCKET, s3_key, str(dest))
        return dest.exists()
    except ClientError as e:
        typer.echo(f"R2 download failed ({s3_key}): {e}", err=True)
        return False


def resolve_assets(song_id: str, hash_prefix: str) -> tuple[Path, Path]:
    """Return (stem_path, lrc_path), downloading from R2 if needed.

    Falls back to main audio file if vocal stems are not available.
    """
    # Prefer clean_vocals.flac (de-echoed) over vocals.wav
    clean_vocals = CACHE_DIR / hash_prefix / "stems" / "clean_vocals.flac"
    stem_path = clean_vocals if clean_vocals.exists() else CACHE_DIR / hash_prefix / "stems" / "vocals.wav"
    audio_path = CACHE_DIR / hash_prefix / "audio" / "audio.mp3"
    lrc_path = CACHE_DIR / hash_prefix / "lrc" / "lyrics.lrc"

    # Try vocal stem first, then fall back to main audio
    audio_source = None
    if stem_path.exists():
        audio_source = stem_path
        typer.echo(f"Using cached vocal stem: {stem_path}", err=True)
    elif audio_path.exists():
        audio_source = audio_path
        typer.echo(f"Vocal stem not available, using main audio: {audio_path}", err=True)
    else:
        # Try to download vocal stem from R2
        typer.echo(
            f"Stem not in local cache — attempting direct R2 download...",
            err=True,
        )
        if download_r2(f"{hash_prefix}/stems/vocals.wav", stem_path):
            audio_source = stem_path
        elif download_r2(f"{hash_prefix}/audio.mp3", audio_path):
            audio_source = audio_path
            typer.echo(f"Using downloaded main audio", err=True)

    if audio_source is None:
        raise FileNotFoundError(
            f"Could not obtain audio for {song_id}. Run: sow-admin audio cache {song_id}"
        )

    if not lrc_path.exists():
        typer.echo(
            f"LRC not in local cache — attempting direct R2 download...",
            err=True,
        )
        if not download_r2(f"{hash_prefix}/lyrics.lrc", lrc_path):
            raise FileNotFoundError(
                f"Could not obtain LRC for {song_id}. Run: sow-admin audio cache {song_id} --lrc"
            )

    return audio_source, lrc_path


# ── Audio utilities ────────────────────────────────────────────────────────────


def load_window(stem_path: Path, start: float, end: float, sr: int = 16000) -> np.ndarray:
    import librosa
    import soundfile as sf

    audio, orig_sr = sf.read(str(stem_path), dtype="float32")
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    s = max(0, int(start * orig_sr))
    e = min(len(audio), int(end * orig_sr))
    chunk = audio[s:e]
    if orig_sr != sr:
        chunk = librosa.resample(chunk, orig_sr=orig_sr, target_sr=sr)
    return chunk


def stem_duration(stem_path: Path) -> float:
    import soundfile as sf

    return sf.info(str(stem_path)).duration


# ── Signal 1: VAD voiced fraction ─────────────────────────────────────────────

_silero_model = None
_silero_utils = None


_silero_load_failed = False


def _load_silero():
    global _silero_model, _silero_utils, _silero_load_failed
    if _silero_load_failed:
        return None, None
    if _silero_model is None:
        import torch

        try:
            typer.echo("Loading Silero VAD...", err=True)
            _silero_model, _silero_utils = torch.hub.load(
                repo_or_dir="snakers4/silero-vad",
                model="silero_vad",
                force_reload=False,
                trust_repo=True,
            )
            typer.echo("Silero VAD loaded.", err=True)
        except Exception as e:
            typer.echo(f"Silero VAD unavailable ({e}) — skipping VAD signal.", err=True)
            _silero_load_failed = True
            return None, None
    return _silero_model, _silero_utils


def voiced_fraction(stem_path: Path, start: float, end: float) -> float:
    """Fraction of 30ms frames in [start, end] that Silero VAD marks as speech."""
    import torch

    model, utils = _load_silero()
    if model is None:
        return float("nan")
    get_speech_timestamps, _, read_audio, *_ = utils

    sr = 16000
    audio = load_window(stem_path, start, end, sr=sr)
    if len(audio) == 0:
        return 0.0

    audio_t = torch.from_numpy(audio).float()
    timestamps = get_speech_timestamps(audio_t, model, sampling_rate=sr, threshold=0.4)

    if not timestamps:
        return 0.0

    total_voiced = sum(t["end"] - t["start"] for t in timestamps)
    return float(total_voiced / len(audio))


# ── Signal 2: Framewise wav2vec2 DTW ──────────────────────────────────────────

_embedder = None


def _load_embedder():
    global _embedder
    if _embedder is None:
        import torch
        from transformers import AutoFeatureExtractor, AutoModel

        model_id = "facebook/wav2vec2-xls-r-300m"
        typer.echo(f"Loading {model_id}...", err=True)
        device = "mps" if torch.backends.mps.is_available() else "cpu"
        proc = AutoFeatureExtractor.from_pretrained(model_id)
        mdl = AutoModel.from_pretrained(model_id).to(device)
        mdl.eval()
        _embedder = (mdl, proc, device)
        typer.echo("Embedding model loaded.", err=True)
    return _embedder


def embed_framewise(audio: np.ndarray, sr: int = 16000) -> np.ndarray:
    import torch

    mdl, proc, device = _load_embedder()
    inputs = proc(audio, sampling_rate=sr, return_tensors="pt", padding=True)
    with torch.no_grad():
        out = mdl(inputs.input_values.to(device))
    return out.last_hidden_state.squeeze(0).cpu().numpy()


def dtw_path_stats(x: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    """Return (path_cosine_mean, path_slope_dev) from DTW warping path."""
    from scipy.spatial.distance import cdist

    dist = cdist(x, y, metric="cosine")
    n, m = dist.shape
    dtw = np.full((n + 1, m + 1), np.inf)
    dtw[0, 0] = 0.0
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            cost = dist[i - 1, j - 1]
            dtw[i, j] = cost + min(dtw[i - 1, j], dtw[i, j - 1], dtw[i - 1, j - 1])

    # Traceback path
    i, j = n, m
    path = []
    while i > 0 or j > 0:
        path.append((i - 1, j - 1))
        choices = [
            (dtw[i - 1, j - 1], i - 1, j - 1),
            (dtw[i - 1, j], i - 1, j),
            (dtw[i, j - 1], i, j - 1),
        ]
        _, i, j = min(choices, key=lambda t: t[0])
    path.reverse()

    cosines = [1.0 - dist[pi, pj] for pi, pj in path]
    path_cosine_mean = float(np.mean(cosines)) if cosines else 0.0

    # Slope = ratio of step in x to step in y; ideal = 1.0
    slopes = []
    for k in range(1, len(path)):
        di = path[k][0] - path[k - 1][0]
        dj = path[k][1] - path[k - 1][1]
        if dj > 0:
            slopes.append(di / dj)
    path_slope_dev = float(np.std(slopes)) if slopes else 0.0

    return path_cosine_mean, path_slope_dev


def compute_dtw_signal(
    stem_path: Path,
    t_lrc: float,
    tts_audio: np.ndarray,
    tts_sr: int,
    stem_total_dur: float,
) -> tuple[float, float]:
    tts_dur = len(tts_audio) / tts_sr
    win_start = max(0.0, t_lrc - 0.5)
    win_end = min(stem_total_dur, t_lrc + tts_dur + 1.0)

    import librosa

    tts_16k = librosa.resample(tts_audio.astype(np.float32), orig_sr=tts_sr, target_sr=16000)
    stem_chunk = load_window(stem_path, win_start, win_end, sr=16000)

    if len(tts_16k) < 100 or len(stem_chunk) < 100:
        return 0.0, 0.0

    tts_frames = embed_framewise(tts_16k)
    stem_frames = embed_framewise(stem_chunk)

    if len(tts_frames) == 0 or len(stem_frames) == 0:
        return 0.0, 0.0

    return dtw_path_stats(tts_frames, stem_frames)


# ── Signal 3: Onset match ratio ───────────────────────────────────────────────


def onset_match_ratio(
    stem_path: Path,
    t_lrc: float,
    tts_audio: np.ndarray,
    tts_sr: int,
    stem_total_dur: float,
    tolerance: float = 0.15,
) -> float:
    import librosa

    tts_dur = len(tts_audio) / tts_sr
    win_start = max(0.0, t_lrc - 0.3)
    win_end = min(stem_total_dur, t_lrc + tts_dur + 0.3)

    tts_onsets_sec = librosa.onset.onset_detect(
        y=tts_audio.astype(np.float32), sr=tts_sr, units="time"
    )

    stem_chunk = load_window(stem_path, win_start, win_end, sr=tts_sr)
    stem_onsets_rel = librosa.onset.onset_detect(y=stem_chunk, sr=tts_sr, units="time")
    # Convert relative onset times to absolute; align TTS onset to t_lrc
    stem_onsets_abs = stem_onsets_rel + win_start

    if len(tts_onsets_sec) == 0:
        return 0.0

    tts_onsets_abs = tts_onsets_sec + t_lrc
    matched = sum(
        1
        for t_on in tts_onsets_abs
        if any(abs(t_on - s_on) <= tolerance for s_on in stem_onsets_abs)
    )
    return float(matched / len(tts_onsets_sec))


# ── Signal 4: Tone / F0 correlation ───────────────────────────────────────────

# Mandarin tone → expected F0 slope sign per half of syllable:
#   1 flat  → (0, 0)
#   2 rising → (-1, +1) then (+1, +1)  → overall up → sign +1
#   3 dip   → (-1, -1) then (+1, +1)   → overall dip → sign -1 first then +1
#   4 falling → (+1, -1) then (-1, -1) → overall down → sign -1
# Simplified: map to a single expected slope sign (+1 rising, -1 falling, 0 flat)
TONE_SLOPE = {1: 0, 2: 1, 3: -1, 4: -1, 5: 0}  # tone 5 = neutral


def _get_expected_tone_slopes(text: str) -> list[int]:
    try:
        from pypinyin import pinyin, Style
    except ImportError:
        return []

    slopes = []
    for char in text:
        if not char.strip():
            continue
        result = pinyin(char, style=Style.TONE3, heteronym=False)
        if not result or not result[0]:
            continue
        syllable = result[0][0]
        # Tone number is last char if digit
        tone = int(syllable[-1]) if syllable and syllable[-1].isdigit() else 5
        slopes.append(TONE_SLOPE.get(tone, 0))
    return slopes


def tone_f0_correlation(
    stem_path: Path,
    t_lrc: float,
    text: str,
    tts_audio: np.ndarray,
    tts_sr: int,
    stem_total_dur: float,
) -> float:
    """Mean Pearson r between expected tone slope sign and observed F0 slope sign per character."""
    import librosa

    expected = _get_expected_tone_slopes(text)
    if not expected:
        return float("nan")

    tts_dur = len(tts_audio) / tts_sr
    win_start = max(0.0, t_lrc)
    win_end = min(stem_total_dur, t_lrc + tts_dur + 0.3)

    stem_chunk = load_window(stem_path, win_start, win_end, sr=22050)
    if len(stem_chunk) < 512:
        return float("nan")

    f0, _, _ = librosa.pyin(
        stem_chunk,
        fmin=librosa.note_to_hz("C2"),
        fmax=librosa.note_to_hz("C7"),
        sr=22050,
    )
    # f0 may have NaN where unvoiced; interpolate linearly
    valid = ~np.isnan(f0)
    if valid.sum() < 4:
        return float("nan")
    xp = np.where(valid)[0]
    f0_interp = np.interp(np.arange(len(f0)), xp, f0[xp])

    # Divide f0 into len(expected) equal segments and get slope sign per segment
    n = len(expected)
    seg_size = len(f0_interp) // n
    if seg_size < 2:
        return float("nan")

    observed_signs = []
    for k in range(n):
        seg = f0_interp[k * seg_size : (k + 1) * seg_size]
        slope = np.polyfit(np.arange(len(seg)), seg, 1)[0]
        observed_signs.append(np.sign(slope))

    # Pearson r between expected tone slope signs and observed
    exp_arr = np.array(expected[:n], dtype=float)
    obs_arr = np.array(observed_signs, dtype=float)

    if np.std(exp_arr) < 1e-9 or np.std(obs_arr) < 1e-9:
        return float("nan")

    r = float(np.corrcoef(exp_arr, obs_arr)[0, 1])
    return r if not np.isnan(r) else float("nan")


# ── Signal 5: qwen3-forcedaligner start-time drift ───────────────────────────

_qwen3_aligner = None
_qwen3_load_failed = False


def _load_qwen3_aligner():
    global _qwen3_aligner, _qwen3_load_failed
    if _qwen3_load_failed:
        return None
    if _qwen3_aligner is None:
        try:
            import torch
            from qwen_asr import Qwen3ForcedAligner

            device = "mps" if torch.backends.mps.is_available() else "cpu"
            typer.echo("Loading Qwen3ForcedAligner-0.6B...", err=True)
            _qwen3_aligner = Qwen3ForcedAligner.from_pretrained(
                "Qwen/Qwen3-ForcedAligner-0.6B",
                dtype=torch.float32,
                device_map=device,
            )
            typer.echo("Qwen3ForcedAligner loaded.", err=True)
        except ImportError:
            typer.echo(
                "qwen-asr not installed — skipping qwen3 signal.\n"
                "  Install with: uv sync --extra poc_qwen3_align",
                err=True,
            )
            _qwen3_load_failed = True
        except Exception as e:
            typer.echo(f"Qwen3ForcedAligner load failed ({e}) — skipping.", err=True)
            _qwen3_load_failed = True
    return _qwen3_aligner


def run_qwen3_aligner(stem_path: Path, lrc_lines: list[LRCLine]) -> list[float]:
    """Run Qwen3ForcedAligner on the full stem and return per-line drift (seconds).

    Returns a list of |qwen3_start - t_lrc| values, one per LRC line.
    Returns a list of NaN on failure.
    """
    model = _load_qwen3_aligner()
    if model is None:
        return [float("nan")] * len(lrc_lines)

    # Qwen3ForcedAligner requires audio <= 5 minutes
    try:
        import soundfile as sf

        dur = sf.info(str(stem_path)).duration
        if dur > 300:
            typer.echo(f"  Stem duration {dur:.1f}s > 300s — skipping qwen3 signal.", err=True)
            return [float("nan")] * len(lrc_lines)
    except Exception:
        pass

    lyrics_text = "\n".join(line.text for line in lrc_lines)
    try:
        results = model.align(
            audio=str(stem_path),
            text=lyrics_text,
            language="Chinese",
        )
    except Exception as e:
        typer.echo(f"  Qwen3 alignment error: {e}", err=True)
        return [float("nan")] * len(lrc_lines)

    # Flatten character-level segments
    raw_segments = []
    for segment_list in results:
        for seg in segment_list:
            text = seg.text.strip()
            if text:
                raw_segments.append((seg.start_time, seg.end_time, text))

    # Reuse map_segments_to_lines logic from gen_lrc_qwen3_force_align.py
    aligned_text = ""
    seg_positions = []
    for s_start, s_end, s_text in raw_segments:
        start_char = len(aligned_text)
        aligned_text += s_text
        seg_positions.append((start_char, len(aligned_text), s_start, s_end))

    import re as _re

    def _norm(t: str) -> str:
        return _re.sub(r"[\s。，！？、；：\"''" "''（）【】「」『』 ]+", "", t)

    aligned_norm = _norm(aligned_text)
    pos = 0
    drifts = []
    for line in lrc_lines:
        norm_line = _norm(line.text)
        if not norm_line:
            drifts.append(float("nan"))
            continue
        idx = aligned_norm.find(norm_line, pos)
        if idx == -1:
            drifts.append(float("nan"))
            continue
        line_end = idx + len(norm_line)
        pos = line_end
        overlapping = [
            s_start for (sc, ec, s_start, s_end) in seg_positions if ec > idx and sc < line_end
        ]
        if not overlapping:
            drifts.append(float("nan"))
        else:
            qwen3_start = min(overlapping)
            drifts.append(abs(qwen3_start - line.time_seconds))

    return drifts


# ── Signal 6: MFA start-time drift (stub — MFA not installed) ────────────────


def run_mfa_aligner(stem_path: Path, lrc_lines: list[LRCLine]) -> list[float]:
    """Run MFA alignment and return per-line |mfa_start - t_lrc| drift values.

    MFA is not currently installed. Install with:
        conda install -c conda-forge montreal-forced-aligner
        mfa model download acoustic mandarin_mfa
        mfa model download dictionary mandarin_mfa

    Then replace this stub with a real implementation using subprocess to call
    `mfa align` and parse the resulting TextGrid files.
    """
    try:
        import subprocess

        result = subprocess.run(["mfa", "version"], capture_output=True)
        if result.returncode != 0:
            raise FileNotFoundError
    except FileNotFoundError:
        typer.echo("MFA not installed — skipping mfa signal.", err=True)
        return [float("nan")] * len(lrc_lines)

    # TODO: implement when MFA is installed
    typer.echo("MFA found but alignment not yet implemented in this script.", err=True)
    return [float("nan")] * len(lrc_lines)


# ── TTS cache (reuse from score_lrc_quality.py) ────────────────────────────────

TTS_CACHE_DIR = Path.home() / ".cache" / "qwen3_tts"


def _tts_cache_path(text: str, voice: str = "Chelsie") -> Path:
    import hashlib

    key = f"mlx-community/Qwen3-TTS-12Hz-0.6B-Base-bf16:{voice}:{text}"
    sha1 = hashlib.sha1(key.encode()).hexdigest()
    return TTS_CACHE_DIR / f"{sha1}.wav"


def load_tts_audio(text: str, voice: str = "Chelsie") -> tuple[np.ndarray, int]:
    """Load TTS from cache (no synthesis here — assume cache populated by score_lrc_quality.py)."""
    import soundfile as sf
    from zhconv import convert

    text = convert(text, "zh-hans")
    cache_path = _tts_cache_path(text, voice)
    if not cache_path.exists():
        raise FileNotFoundError(
            f"TTS cache miss for: {text!r}\n"
            f"  Run score_lrc_quality.py on this song first to populate the TTS cache."
        )
    audio, sr = sf.read(str(cache_path), dtype="float32")
    return audio, sr


# ── Per-line signal computation ────────────────────────────────────────────────


@dataclass
class LineSignals:
    line_idx: int
    t_lrc: float
    text: str
    voiced_frac: float = float("nan")
    dtw_path_cosine: float = float("nan")
    dtw_slope_dev: float = float("nan")
    onset_match_ratio: float = float("nan")
    tone_corr: float = float("nan")
    qwen3_drift: float = float("nan")  # |qwen3_start - t_lrc| in seconds
    mfa_drift: float = float("nan")  # |mfa_start - t_lrc| in seconds
    tts_duration: float = float("nan")
    tts_cache_hit: bool = False


def compute_signals_for_song(
    song_id: str,
    stem_path: Path,
    lrc_path: Path,
    skip_vad: bool = False,
    skip_dtw: bool = False,
    skip_onset: bool = False,
    skip_tone: bool = False,
    skip_qwen3: bool = False,
    skip_mfa: bool = False,
) -> list[LineSignals]:
    lines = parse_lrc(lrc_path.read_text(encoding="utf-8"))
    total_dur = stem_duration(stem_path)
    results = []

    typer.echo(f"\nProcessing {len(lines)} lines for {song_id}...", err=True)

    # Signals 5–6: run aligner once per song, then distribute per-line drifts
    qwen3_drifts: list[float] = [float("nan")] * len(lines)
    mfa_drifts: list[float] = [float("nan")] * len(lines)

    if not skip_qwen3:
        typer.echo("  Running qwen3-forcedaligner on full stem...", err=True)
        qwen3_drifts = run_qwen3_aligner(stem_path, lines)

    if not skip_mfa:
        mfa_drifts = run_mfa_aligner(stem_path, lines)

    for i, line in enumerate(lines):
        sig = LineSignals(line_idx=i + 1, t_lrc=line.time_seconds, text=line.text)
        sig.qwen3_drift = qwen3_drifts[i]
        sig.mfa_drift = mfa_drifts[i]

        # TTS audio (best-effort from cache)
        tts_audio, tts_sr = None, None
        try:
            tts_audio, tts_sr = load_tts_audio(line.text)
            sig.tts_duration = len(tts_audio) / tts_sr
            sig.tts_cache_hit = True
        except FileNotFoundError as e:
            typer.echo(f"  Line {i + 1}: TTS cache miss — {e}", err=True)

        # Signal 1: VAD
        if not skip_vad:
            try:
                tts_dur_est = sig.tts_duration if not np.isnan(sig.tts_duration) else 3.0
                win_end = min(total_dur, line.time_seconds + tts_dur_est + 0.5)
                sig.voiced_frac = voiced_fraction(stem_path, line.time_seconds, win_end)
            except Exception as e:
                typer.echo(f"  Line {i + 1} VAD error: {e}", err=True)

        # Signals 2–4 require TTS audio
        if tts_audio is not None:
            # Signal 2: DTW
            if not skip_dtw:
                try:
                    sig.dtw_path_cosine, sig.dtw_slope_dev = compute_dtw_signal(
                        stem_path, line.time_seconds, tts_audio, tts_sr, total_dur
                    )
                except Exception as e:
                    typer.echo(f"  Line {i + 1} DTW error: {e}", err=True)

            # Signal 3: Onset
            if not skip_onset:
                try:
                    sig.onset_match_ratio = onset_match_ratio(
                        stem_path, line.time_seconds, tts_audio, tts_sr, total_dur
                    )
                except Exception as e:
                    typer.echo(f"  Line {i + 1} onset error: {e}", err=True)

            # Signal 4: Tone/F0
            if not skip_tone:
                try:
                    sig.tone_corr = tone_f0_correlation(
                        stem_path, line.time_seconds, line.text, tts_audio, tts_sr, total_dur
                    )
                except Exception as e:
                    typer.echo(f"  Line {i + 1} tone error: {e}", err=True)

        results.append(sig)
        _progress(i + 1, len(lines), sig)

    return results


def _progress(idx: int, total: int, sig: LineSignals) -> None:
    def fmt(v):
        return f"{v:.3f}" if not (isinstance(v, float) and np.isnan(v)) else "n/a"

    typer.echo(
        f"  [{idx:2d}/{total}] t={sig.t_lrc:.2f}s  "
        f"vad={fmt(sig.voiced_frac)}  dtw={fmt(sig.dtw_path_cosine)}/"
        f"{fmt(sig.dtw_slope_dev)}  onset={fmt(sig.onset_match_ratio)}  "
        f"tone={fmt(sig.tone_corr)}  qwen3={fmt(sig.qwen3_drift)}  "
        f"mfa={fmt(sig.mfa_drift)}  '{sig.text[:20]}'",
        err=True,
    )


# ── CSV output ────────────────────────────────────────────────────────────────

COLUMNS = [
    "line_idx",
    "t_lrc",
    "text",
    "voiced_frac",
    "dtw_path_cosine",
    "dtw_slope_dev",
    "onset_match_ratio",
    "tone_corr",
    "qwen3_drift",
    "mfa_drift",
    "tts_duration",
    "tts_cache_hit",
]


def write_csv(signals: list[LineSignals], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        for s in signals:
            writer.writerow(
                {
                    "line_idx": s.line_idx,
                    "t_lrc": f"{s.t_lrc:.3f}",
                    "text": s.text,
                    "voiced_frac": "" if np.isnan(s.voiced_frac) else f"{s.voiced_frac:.4f}",
                    "dtw_path_cosine": ""
                    if np.isnan(s.dtw_path_cosine)
                    else f"{s.dtw_path_cosine:.4f}",
                    "dtw_slope_dev": "" if np.isnan(s.dtw_slope_dev) else f"{s.dtw_slope_dev:.4f}",
                    "onset_match_ratio": ""
                    if np.isnan(s.onset_match_ratio)
                    else f"{s.onset_match_ratio:.4f}",
                    "tone_corr": "" if np.isnan(s.tone_corr) else f"{s.tone_corr:.4f}",
                    "qwen3_drift": "" if np.isnan(s.qwen3_drift) else f"{s.qwen3_drift:.3f}",
                    "mfa_drift": "" if np.isnan(s.mfa_drift) else f"{s.mfa_drift:.3f}",
                    "tts_duration": "" if np.isnan(s.tts_duration) else f"{s.tts_duration:.3f}",
                    "tts_cache_hit": str(s.tts_cache_hit),
                }
            )


# ── Markdown report ───────────────────────────────────────────────────────────


def _stats(vals: list[float]) -> dict:
    v = [x for x in vals if not np.isnan(x)]
    if not v:
        return {
            "n": 0,
            "mean": float("nan"),
            "std": float("nan"),
            "min": float("nan"),
            "max": float("nan"),
        }
    return {"n": len(v), "mean": np.mean(v), "std": np.std(v), "min": np.min(v), "max": np.max(v)}


def write_markdown(
    all_results: dict[str, list[LineSignals]],
    path: Path,
) -> None:
    lines = ["# LRC Signal Experiment Report\n\n"]

    # Per-song per-signal stats
    for song_id, sigs in all_results.items():
        meta = SONGS.get(song_id, {})
        lines.append(f"## {song_id} — {meta.get('label', '')}\n\n")
        lines.append(
            f"Lines processed: {len(sigs)}  |  TTS cache hits: {sum(s.tts_cache_hit for s in sigs)}\n\n"
        )

        signal_fields = [
            ("voiced_frac", "VAD voiced fraction"),
            ("dtw_path_cosine", "DTW path cosine mean"),
            ("dtw_slope_dev", "DTW slope std-dev"),
            ("onset_match_ratio", "Onset match ratio"),
            ("tone_corr", "Tone/F0 correlation"),
            ("qwen3_drift", "Qwen3 aligner drift (s)"),
            ("mfa_drift", "MFA drift (s)"),
        ]

        lines.append("### Signal summary\n\n")
        lines.append("| Signal | N | Mean | Std | Min | Max |\n")
        lines.append("|--------|---|------|-----|-----|-----|\n")
        for attr, label in signal_fields:
            vals = [getattr(s, attr) for s in sigs]
            st = _stats(vals)
            lines.append(
                f"| {label} | {st['n']} | "
                f"{st['mean']:.3f} | {st['std']:.3f} | "
                f"{st['min']:.3f} | {st['max']:.3f} |\n"
                if st["n"] > 0
                else f"| {label} | 0 | n/a | n/a | n/a | n/a |\n"
            )

        # Top-10 worst per signal (those with lowest values, except slope_dev where highest is worst)
        lines.append("\n### Top-10 lines to spot-check\n\n")
        worst_signals = [
            ("voiced_frac", "Lowest VAD voiced fraction (possible silence)", False),
            ("dtw_path_cosine", "Lowest DTW cosine (worst phonetic match)", False),
            ("dtw_slope_dev", "Highest DTW slope std-dev (timing stretch/compression)", True),
            ("onset_match_ratio", "Lowest onset match ratio (rhythm mismatch)", False),
            ("qwen3_drift", "Highest Qwen3 aligner drift (largest start-time error)", True),
            ("mfa_drift", "Highest MFA drift (largest start-time error)", True),
        ]
        for attr, label, reverse_sort in worst_signals:
            valid = [(s, getattr(s, attr)) for s in sigs if not np.isnan(getattr(s, attr))]
            if not valid:
                lines.append(f"**{label}:** no data\n\n")
                continue
            ranked = sorted(valid, key=lambda x: x[1], reverse=reverse_sort)[:10]
            lines.append(f"**{label}:**\n\n")
            lines.append("| Line | Time | Text | Value |\n")
            lines.append("|------|------|------|-------|\n")
            for s, v in ranked:
                t = f"{int(s.t_lrc // 60):02d}:{s.t_lrc % 60:05.2f}"
                lines.append(f"| {s.line_idx} | [{t}] | {s.text[:35]} | {v:.4f} |\n")
            lines.append("\n")

    # Cross-song comparison
    lines.append("## Cross-song signal comparison\n\n")
    if len(all_results) == 2:
        song_ids = list(all_results.keys())
        signal_fields_compare = [
            ("voiced_frac", "VAD voiced fraction"),
            ("dtw_path_cosine", "DTW path cosine mean"),
            ("dtw_slope_dev", "DTW slope std-dev"),
            ("onset_match_ratio", "Onset match ratio"),
            ("tone_corr", "Tone/F0 correlation"),
            ("qwen3_drift", "Qwen3 aligner drift (s)"),
            ("mfa_drift", "MFA drift (s)"),
        ]
        lines.append(
            "| Signal | "
            + " | ".join(s.split("_")[0][:12] + "…" for s in song_ids)
            + " | Separates? |\n"
        )
        lines.append("|--------|" + "|".join("---" for _ in song_ids) + "|------------|\n")
        for attr, label in signal_fields_compare:
            means = []
            for sid in song_ids:
                vals = [getattr(s, attr) for s in all_results[sid]]
                st = _stats(vals)
                means.append(st["mean"])
            if any(np.isnan(m) for m in means):
                sep = "no data"
            else:
                diff = abs(means[0] - means[1])
                sep = f"Δ={diff:.3f} {'✓ YES' if diff > 0.05 else '✗ NO'}"
            mean_strs = " | ".join(f"{m:.3f}" if not np.isnan(m) else "n/a" for m in means)
            lines.append(f"| {label} | {mean_strs} | {sep} |\n")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(lines), encoding="utf-8")


# ── CLI ───────────────────────────────────────────────────────────────────────


@app.command()
def main(
    song: Optional[str] = typer.Option(
        None,
        "--song",
        "-s",
        help="Run on single song ID instead of both reference songs",
    ),
    skip_vad: bool = typer.Option(False, "--skip-vad", help="Skip Silero VAD signal"),
    skip_dtw: bool = typer.Option(False, "--skip-dtw", help="Skip DTW signal (slow)"),
    skip_onset: bool = typer.Option(False, "--skip-onset", help="Skip onset match signal"),
    skip_tone: bool = typer.Option(False, "--skip-tone", help="Skip tone/F0 correlation signal"),
    skip_qwen3: bool = typer.Option(False, "--skip-qwen3", help="Skip qwen3-forcedaligner signal"),
    skip_mfa: bool = typer.Option(
        False, "--skip-mfa", help="Skip MFA signal (no-op if MFA not installed)"
    ),
    stem_override: Optional[Path] = typer.Option(None, "--stem", help="Override stem path"),
    lrc_override: Optional[Path] = typer.Option(None, "--lrc", help="Override LRC path"),
    out_dir: Path = typer.Option(OUTPUT_BASE, "--out-dir", help="Output directory base"),
):
    """Compute per-line timing signals on reference songs for scorer selection."""

    songs_to_run = {}
    if song:
        if song not in SONGS and stem_override is None:
            typer.echo(f"Unknown song '{song}'. Known: {list(SONGS.keys())}", err=True)
            raise typer.Exit(1)
        songs_to_run[song] = SONGS.get(song, {"label": "custom", "hash_prefix": None})
    else:
        songs_to_run = SONGS.copy()

    all_results: dict[str, list[LineSignals]] = {}

    for song_id, meta in songs_to_run.items():
        typer.echo(f"\n{'=' * 60}", err=True)
        typer.echo(f"Song: {song_id}  ({meta['label']})", err=True)
        typer.echo(f"{'=' * 60}", err=True)

        # Resolve paths
        if stem_override and lrc_override and song == song_id:
            stem_path = stem_override
            lrc_path = lrc_override
        elif meta.get("hash_prefix"):
            try:
                stem_path, lrc_path = resolve_assets(song_id, meta["hash_prefix"])
            except FileNotFoundError as e:
                typer.echo(f"ERROR: {e}", err=True)
                typer.echo(
                    "Set SOW_R2_ACCESS_KEY_ID and SOW_R2_SECRET_ACCESS_KEY to download from R2.",
                    err=True,
                )
                continue
        else:
            typer.echo(
                f"ERROR: no hash_prefix for {song_id} and no --stem/--lrc overrides.", err=True
            )
            continue

        typer.echo(f"Stem: {stem_path}", err=True)
        typer.echo(f"LRC:  {lrc_path}", err=True)

        sigs = compute_signals_for_song(
            song_id,
            stem_path,
            lrc_path,
            skip_vad=skip_vad,
            skip_dtw=skip_dtw,
            skip_onset=skip_onset,
            skip_tone=skip_tone,
            skip_qwen3=skip_qwen3,
            skip_mfa=skip_mfa,
        )
        all_results[song_id] = sigs

        csv_path = out_dir / song_id / "signals.csv"
        write_csv(sigs, csv_path)
        typer.echo(f"\nCSV written: {csv_path}", err=True)

    if all_results:
        md_path = out_dir / "signals.md"
        write_markdown(all_results, md_path)
        typer.echo(f"\nMarkdown report: {md_path}", err=True)

    typer.echo("\nDone.", err=True)


if __name__ == "__main__":
    app()
