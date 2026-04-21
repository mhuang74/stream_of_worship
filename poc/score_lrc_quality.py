#!/usr/bin/env python3
"""LRC quality scorer via Qwen3-TTS round-trip comparison.

Synthesizes speech from each LRC line via Qwen3-TTS on MLX, then compares
against the original vocal stem in phonetic embedding space to detect
content errors.

Usage:
    PYTHONPATH=src:. uv run --extra score_lrc_base python poc/score_lrc_quality.py \
        wo_yao_quan_xin_zan_mei_244

    PYTHONPATH=src:. uv run --extra score_lrc_base python poc/score_lrc_quality.py \
        wo_yao_quan_xin_zan_mei_244 \
        --stem /custom/path/vocals.flac \
        --lrc /custom/path/lyrics.lrc

Installation Notes:
    This script requires mlx-audio>=0.3.0 for Qwen3-TTS support. Due to
    dependency conflicts with poc_qwen3_align (which requires qwen-asr
    that pins transformers==4.57.6), mlx-audio must be installed separately:

        uv pip install mlx-audio>=0.3.0 --prerelease=allow

    This will upgrade transformers to 5.0.0rc3 which is required for
    Qwen3-TTS support.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import typer

# Add src directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def format_timestamp(seconds: float) -> str:
    """Format seconds as [mm:ss.xx] timestamp."""
    minutes = int(seconds // 60)
    secs = seconds % 60
    return f"[{minutes:02d}:{secs:05.2f}]"


# LRC Parser
from dataclasses import dataclass
from typing import List


@dataclass
class LRCLine:
    """A single line of synchronized lyrics."""
    time_seconds: float
    text: str
    raw_timestamp: str


@dataclass
class LRCFile:
    """Parsed LRC file with metadata."""
    lines: List[LRCLine]
    line_count: int
    duration_seconds: float
    raw_content: str


def parse_lrc(content: str) -> LRCFile:
    """Parse LRC file content."""
    import re

    lines = []
    pattern = r"\[(\d{2}):(\d{2})\.(\d{2,3})\](.*)"

    for line in content.split("\n"):
        match = re.match(pattern, line.strip())
        if match:
            minutes = int(match.group(1))
            seconds = int(match.group(2))
            milliseconds = int(match.group(3).ljust(3, "0")[:3])
            text = match.group(4).strip()

            time_seconds = minutes * 60 + seconds + milliseconds / 1000.0
            raw_timestamp = f"[{match.group(1)}:{match.group(2)}.{match.group(3)}]"

            lines.append(LRCLine(time_seconds=time_seconds, text=text, raw_timestamp=raw_timestamp))

    if not lines:
        raise ValueError("No valid LRC lines found in file")

    duration_seconds = lines[-1].time_seconds if lines else 0.0

    return LRCFile(
        lines=lines, line_count=len(lines), duration_seconds=duration_seconds, raw_content=content
    )


app = typer.Typer(help="LRC quality scorer via TTS round-trip comparison")


# =============================================================================
# Config and path resolution (inlined to avoid heavy dependencies)
# =============================================================================

def load_admin_config(config_path: Optional[Path] = None):
    """Load admin config from file or default location."""
    import tomllib

    if config_path is None:
        config_path = Path.home() / ".config" / "sow-admin" / "config.toml"

    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    content = config_path.read_text()
    config = tomllib.loads(content)

    return config


def get_recording_from_db(db_path: Path, song_id: str) -> Optional[dict]:
    """Get recording hash_prefix for a song from the database."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Find song by ID (songs.id is the primary key, not song_id)
    cursor.execute(
        "SELECT id FROM songs WHERE id = ?",
        (song_id,)
    )
    song_row = cursor.fetchone()

    if not song_row:
        conn.close()
        return None

    song_db_id = song_row["id"]

    # Find recording for this song
    cursor.execute(
        "SELECT hash_prefix FROM recordings WHERE song_id = ?",
        (song_db_id,)
    )
    recording_row = cursor.fetchone()

    conn.close()

    if recording_row:
        return {"hash_prefix": recording_row["hash_prefix"]}
    return None


def download_from_r2(
    s3_key: str,
    dest_path: Path,
    bucket: str,
    endpoint_url: str,
    region: str = "auto",
) -> bool:
    """Download a file from R2."""
    import boto3
    from botocore.exceptions import ClientError

    access_key = os.environ.get("SOW_R2_ACCESS_KEY_ID")
    secret_key = os.environ.get("SOW_R2_SECRET_ACCESS_KEY")

    if not access_key or not secret_key:
        raise ValueError(
            "R2 credentials not set. Set SOW_R2_ACCESS_KEY_ID and SOW_R2_SECRET_ACCESS_KEY."
        )

    client = boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=region,
    )

    try:
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        client.download_file(bucket, s3_key, str(dest_path))
        return dest_path.exists()
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "")
        if error_code in ("404", "NoSuchKey"):
            typer.echo(f"File not found in R2: {s3_key}", err=True)
        else:
            typer.echo(f"Error downloading from R2: {e}", err=True)
        return False
    except Exception as e:
        typer.echo(f"Error downloading from R2: {e}", err=True)
        return False


def resolve_lrc_path(
    song_id: str,
    cache_dir: Path,
    db_path: Path,
    r2_bucket: str,
    r2_endpoint: str,
) -> Path:
    """Resolve LRC path for a song, downloading from R2 if not cached."""
    input_path = Path(song_id).expanduser()

    # Direct file path
    if input_path.exists() and input_path.suffix == ".lrc":
        return input_path

    # Look up song in database
    recording = get_recording_from_db(db_path, song_id)
    if not recording:
        typer.echo(f"Error: Song not found: {song_id}", err=True)
        raise typer.Exit(1)

    hash_prefix = recording["hash_prefix"]
    lrc_path = cache_dir / hash_prefix / "lrc" / "lyrics.lrc"

    # Check local cache first
    if lrc_path.exists():
        typer.echo(f"Using cached LRC: {lrc_path}", err=True)
        return lrc_path

    # Download from R2
    typer.echo("Downloading LRC from R2...", err=True)
    s3_key = f"{hash_prefix}/lyrics.lrc"

    if download_from_r2(s3_key, lrc_path, r2_bucket, r2_endpoint):
        typer.echo(f"Downloaded LRC to: {lrc_path}", err=True)
        return lrc_path

    # Failed to download
    typer.echo(f"Error: LRC not found in local cache or R2: {song_id}", err=True)
    typer.echo(f"Run 'sow-admin audio lrc {song_id}' to generate LRC first.", err=True)
    raise typer.Exit(1)


def resolve_stem_path(
    song_id: str,
    cache_dir: Path,
    db_path: Path,
    r2_bucket: str,
    r2_endpoint: str,
) -> Path:
    """Resolve vocal stem path for a song, downloading from R2 if not cached."""
    input_path = Path(song_id).expanduser()

    # Direct file path
    if input_path.exists() and input_path.suffix in (".flac", ".wav", ".mp3"):
        return input_path

    # Look up song in database
    recording = get_recording_from_db(db_path, song_id)
    if not recording:
        typer.echo(f"Error: Song not found: {song_id}", err=True)
        raise typer.Exit(1)

    hash_prefix = recording["hash_prefix"]

    # Try vocals stem first
    vocals_path = cache_dir / hash_prefix / "stems" / "vocals.wav"
    if vocals_path.exists():
        typer.echo(f"Using cached vocals stem: {vocals_path}", err=True)
        return vocals_path

    # Download vocals stem from R2
    typer.echo("Downloading vocals stem from R2...", err=True)
    s3_key = f"{hash_prefix}/stems/vocals.wav"

    if download_from_r2(s3_key, vocals_path, r2_bucket, r2_endpoint):
        typer.echo(f"Downloaded vocals stem to: {vocals_path}", err=True)
        return vocals_path

    # Try main audio as fallback
    main_audio_path = cache_dir / hash_prefix / "audio.mp3"
    if main_audio_path.exists():
        typer.echo(f"Using cached main audio: {main_audio_path}", err=True)
        return main_audio_path

    # Download main audio from R2
    typer.echo("Downloading main audio from R2...", err=True)
    s3_key = f"{hash_prefix}/audio.mp3"

    if download_from_r2(s3_key, main_audio_path, r2_bucket, r2_endpoint):
        typer.echo(f"Downloaded main audio to: {main_audio_path}", err=True)
        return main_audio_path

    # Failed to find any audio
    typer.echo(f"Error: Could not find or download audio for: {song_id}", err=True)
    raise typer.Exit(1)


# =============================================================================
# Dependency checking
# =============================================================================

def check_mlx_audio() -> bool:
    """Check if mlx-audio with Qwen3-TTS support is available."""
    try:
        from mlx_audio.tts.utils import load_model
        return True
    except ImportError:
        return False


def check_qwen_tts_support() -> bool:
    """Check if Qwen3-TTS is supported in mlx-audio."""
    try:
        from mlx_audio.tts.models.qwen3 import Model
        return True
    except ImportError:
        return False


# =============================================================================
# Singletons for model loading
# =============================================================================

class MLXQwen3TTS:
    """Singleton wrapper for MLX Qwen3-TTS model."""

    _instance: Optional[MLXQwen3TTS] = None
    _model = None
    _model_id: Optional[str] = None

    def __new__(cls, model_id: str = "mlx-community/Qwen3-TTS-12Hz-0.6B-Base-bf16"):
        if cls._instance is None or cls._model_id != model_id:
            cls._instance = super().__new__(cls)
            cls._instance._load(model_id)
            cls._model_id = model_id
        return cls._instance

    def _load(self, model_id: str) -> None:
        """Load the TTS model."""
        from mlx_audio.tts.utils import load_model

        typer.echo(f"Loading TTS model: {model_id}...", err=True)
        self._model = load_model(model_id)
        typer.echo("TTS model loaded.", err=True)

    @property
    def sample_rate(self) -> int:
        return self._model.sample_rate

    def synthesize(self, text: str, voice: str = "Chelsie", language: str = "Mandarin") -> np.ndarray:
        """Synthesize audio from text."""
        results = list(self._model.generate(text=text, voice=voice, language=language))
        audio_mx = results[0].audio
        return np.array(audio_mx, dtype=np.float32)


class Wav2Vec2Embedder:
    """Singleton wrapper for wav2vec2-xls-r embedding model."""

    _instance: Optional[Wav2Vec2Embedder] = None
    _model = None
    _processor = None
    _device: Optional[str] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._load()
        return cls._instance

    def _load(self) -> None:
        """Load the wav2vec2 model and processor."""
        import torch
        from transformers import AutoFeatureExtractor, AutoModel

        model_id = "facebook/wav2vec2-xls-r-300m"
        typer.echo(f"Loading embedding model: {model_id}...", err=True)

        self._device = "cpu"
        if torch.backends.mps.is_available():
            self._device = "mps"
            typer.echo("Using MPS device for embeddings", err=True)

        self._processor = AutoFeatureExtractor.from_pretrained(model_id)
        self._model = AutoModel.from_pretrained(model_id).to(self._device)
        self._model.eval()
        typer.echo("Embedding model loaded.", err=True)

    def embed(self, audio: np.ndarray, sample_rate: int = 16000) -> np.ndarray:
        """Extract phonetic embedding from audio."""
        import torch

        inputs = self._processor(
            audio,
            sampling_rate=sample_rate,
            return_tensors="pt",
            padding=True,
        )
        input_values = inputs.input_values.to(self._device)

        with torch.no_grad():
            outputs = self._model(input_values)

        embeddings = outputs.last_hidden_state.mean(dim=1)
        return embeddings.cpu().numpy().squeeze()

    def embed_framewise(self, audio: np.ndarray, sample_rate: int = 16000) -> np.ndarray:
        """Extract framewise phonetic embeddings from audio."""
        import torch

        inputs = self._processor(
            audio,
            sampling_rate=sample_rate,
            return_tensors="pt",
            padding=True,
        )
        input_values = inputs.input_values.to(self._device)

        with torch.no_grad():
            outputs = self._model(input_values)

        return outputs.last_hidden_state.squeeze(0).cpu().numpy()


# =============================================================================
# TTS Cache
# =============================================================================

def get_cache_path(text: str, voice: str, model_id: str, cache_dir: Path) -> Path:
    """Get cache path for TTS audio."""
    key = f"{model_id}:{voice}:{text}"
    sha1 = hashlib.sha1(key.encode("utf-8")).hexdigest()
    return cache_dir / f"{sha1}.wav"


def synthesize_cached(
    text: str,
    tts: MLXQwen3TTS,
    voice: str = "Chelsie",
    cache_dir: Path = Path.home() / ".cache" / "qwen3_tts",
    normalize_zh: bool = True,
) -> tuple[np.ndarray, int]:
    """Synthesize audio with caching."""
    import soundfile as sf
    from zhconv import convert

    if normalize_zh:
        text = convert(text, "zh-hans")

    cache_path = get_cache_path(text, voice, tts._model_id, cache_dir)

    if cache_path.exists():
        audio, sr = sf.read(str(cache_path), dtype="float32")
        return audio, sr

    audio = tts.synthesize(text, voice=voice, language="Mandarin")
    sr = tts.sample_rate

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(cache_path), audio, sr)

    return audio, sr


# =============================================================================
# Audio utilities
# =============================================================================

def load_stem_window(
    stem_path: Path,
    start: float,
    end: float,
    target_sr: int = 16000,
) -> np.ndarray:
    """Load a time window from the stem audio."""
    import librosa
    import soundfile as sf

    audio, sr = sf.read(str(stem_path), dtype="float32")

    if audio.ndim > 1:
        audio = audio.mean(axis=1)

    start_sample = int(start * sr)
    end_sample = int(end * sr)
    audio = audio[start_sample:end_sample]

    if sr != target_sr:
        audio = librosa.resample(audio, orig_sr=sr, target_sr=target_sr)

    return audio


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Compute cosine similarity between two vectors."""
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))


def dtw_distance(x: np.ndarray, y: np.ndarray) -> float:
    """Compute normalized DTW distance between two sequences."""
    from scipy.spatial.distance import cdist

    dist_matrix = cdist(x, y, metric="cosine")

    n, m = dist_matrix.shape
    dtw = np.zeros((n + 1, m + 1))
    dtw[0, 1:] = np.inf
    dtw[1:, 0] = np.inf
    dtw[0, 0] = 0

    for i in range(1, n + 1):
        for j in range(1, m + 1):
            cost = dist_matrix[i - 1, j - 1]
            dtw[i, j] = cost + min(dtw[i - 1, j], dtw[i, j - 1], dtw[i - 1, j - 1])

    max_dist = 2.0
    normalized_dist = dtw[n, m] / (n + m)
    similarity = 1.0 - (normalized_dist / max_dist)

    return float(np.clip(similarity, 0.0, 1.0))


def find_peak_offset(
    tts_framewise: np.ndarray,
    stem_framewise: np.ndarray,
    hop_seconds: float = 0.02,
) -> float:
    """Find the time offset where TTS best matches stem via sliding window."""
    if len(stem_framewise) < len(tts_framewise):
        return 0.0

    num_positions = len(stem_framewise) - len(tts_framewise) + 1
    similarities = []

    for i in range(num_positions):
        stem_slice = stem_framewise[i : i + len(tts_framewise)]
        sim = cosine_similarity(tts_framewise.mean(axis=0), stem_slice.mean(axis=0))
        similarities.append(sim)

    if not similarities:
        return 0.0

    peak_idx = int(np.argmax(similarities))
    return float(peak_idx * hop_seconds)


# =============================================================================
# Scoring
# =============================================================================

@dataclass
class LineScore:
    """Score result for a single LRC line."""
    line_idx: int
    timestamp: float
    text: str
    score: float
    dtw_score: Optional[float] = None
    peak_offset: float = 0.0
    tts_duration: float = 0.0
    stem_duration: float = 0.0


@dataclass
class ScoreReport:
    """Overall scoring report for an LRC file."""
    stem_path: Path
    lrc_path: Path
    line_scores: list[LineScore] = field(default_factory=list)
    overall: float = 0.0
    min_score: float = 0.0
    p10_score: float = 0.0
    num_below_threshold: int = 0
    threshold: float = 0.60
    status: str = "REVIEW"
    model_ids: dict = field(default_factory=dict)


def score_line(
    stem_audio: np.ndarray,
    tts_audio: np.ndarray,
    embedder: Wav2Vec2Embedder,
    use_dtw: bool = False,
) -> dict:
    """Score a single line by comparing stem and TTS audio."""
    tts_embed = embedder.embed(tts_audio)
    stem_embed = embedder.embed(stem_audio)

    score = cosine_similarity(tts_embed, stem_embed)

    result = {"score": score, "dtw_score": None, "peak_offset": 0.0}

    if use_dtw or (0.4 <= score <= 0.7):
        tts_frames = embedder.embed_framewise(tts_audio)
        stem_frames = embedder.embed_framewise(stem_audio)
        result["dtw_score"] = dtw_distance(tts_frames, stem_frames)

    tts_frames = embedder.embed_framewise(tts_audio)
    stem_frames = embedder.embed_framewise(stem_audio)
    result["peak_offset"] = find_peak_offset(tts_frames, stem_frames)

    return result


def score_lrc(
    stem_path: Path,
    lrc_path: Path,
    threshold: float = 0.60,
    max_window: float = 15.0,
    tts_model: str = "mlx-community/Qwen3-TTS-12Hz-0.6B-Base-bf16",
    tts_voice: str = "Chelsie",
    tts_cache_dir: Path = Path.home() / ".cache" / "qwen3_tts",
) -> ScoreReport:
    """Score an LRC file against its vocal stem."""
    tts = MLXQwen3TTS(model_id=tts_model)
    embedder = Wav2Vec2Embedder()

    lrc_content = lrc_path.read_text(encoding="utf-8")
    lrc_file = parse_lrc(lrc_content)

    report = ScoreReport(
        stem_path=stem_path,
        lrc_path=lrc_path,
        threshold=threshold,
        model_ids={
            "tts": tts_model,
            "embedder": "facebook/wav2vec2-xls-r-300m",
        },
    )

    import soundfile as sf
    stem_info = sf.info(str(stem_path))
    stem_duration = stem_info.duration

    typer.echo(f"Scoring {len(lrc_file.lines)} lines...", err=True)

    for i, line in enumerate(lrc_file.lines):
        if i < len(lrc_file.lines) - 1:
            next_time = lrc_file.lines[i + 1].time_seconds
        else:
            next_time = line.time_seconds + max_window

        window_end = min(line.time_seconds + max_window, next_time, stem_duration)
        window_start = line.time_seconds

        if window_end <= window_start:
            typer.echo(f"Warning: Invalid window for line {i+1}, skipping", err=True)
            continue

        stem_duration_actual = window_end - window_start

        try:
            tts_audio, tts_sr = synthesize_cached(
                line.text,
                tts,
                voice=tts_voice,
                cache_dir=tts_cache_dir,
            )
        except Exception as e:
            typer.echo(f"Error synthesizing line {i+1}: {e}", err=True)
            continue

        tts_duration = len(tts_audio) / tts_sr

        try:
            stem_audio = load_stem_window(stem_path, window_start, window_end)
        except Exception as e:
            typer.echo(f"Error loading stem for line {i+1}: {e}", err=True)
            continue

        try:
            result = score_line(stem_audio, tts_audio, embedder)
        except Exception as e:
            typer.echo(f"Error scoring line {i+1}: {e}", err=True)
            continue

        line_score = LineScore(
            line_idx=i + 1,
            timestamp=line.time_seconds,
            text=line.text,
            score=result["score"],
            dtw_score=result.get("dtw_score"),
            peak_offset=result["peak_offset"],
            tts_duration=tts_duration,
            stem_duration=stem_duration_actual,
        )
        report.line_scores.append(line_score)

        typer.echo(
            f"Line {i+1}/{len(lrc_file.lines)}: score={result['score']:.3f}, "
            f"offset={result['peak_offset']:.2f}s",
            err=True,
        )

    if report.line_scores:
        scores = [ls.score for ls in report.line_scores]
        report.overall = float(np.mean(scores))
        report.min_score = float(np.min(scores))
        report.p10_score = float(np.percentile(scores, 10))
        report.num_below_threshold = sum(1 for s in scores if s < threshold)

        below_ratio = report.num_below_threshold / len(scores)
        if report.overall >= threshold and below_ratio <= 0.15:
            report.status = "PASS"
        else:
            report.status = "REVIEW"

    return report


# =============================================================================
# Report generation
# =============================================================================

def write_report_json(report: ScoreReport, json_path: Path) -> None:
    """Write JSON score report."""
    data = {
        "stem_path": str(report.stem_path),
        "lrc_path": str(report.lrc_path),
        "overall": report.overall,
        "min_score": report.min_score,
        "p10_score": report.p10_score,
        "threshold": report.threshold,
        "num_below_threshold": report.num_below_threshold,
        "total_lines": len(report.line_scores),
        "status": report.status,
        "models": report.model_ids,
        "lines": [
            {
                "line_idx": ls.line_idx,
                "timestamp": ls.timestamp,
                "timestamp_formatted": format_timestamp(ls.timestamp),
                "text": ls.text,
                "score": ls.score,
                "dtw_score": ls.dtw_score,
                "peak_offset": ls.peak_offset,
                "tts_duration": ls.tts_duration,
                "stem_duration": ls.stem_duration,
            }
            for ls in report.line_scores
        ],
    }
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2))


def write_report_markdown(report: ScoreReport, md_path: Path) -> None:
    """Write Markdown report."""
    lines = []
    lines.append("# LRC Quality Report\n")
    lines.append(f"**Stem:** `{report.stem_path}`\n")
    lines.append(f"**LRC:** `{report.lrc_path}`\n")
    lines.append(f"**Status:** {'✅ PASS' if report.status == 'PASS' else '⚠️ REVIEW'}\n")
    lines.append("\n## Summary\n")
    lines.append(f"- **Overall Score:** {report.overall:.3f}\n")
    lines.append(f"- **Min Score:** {report.min_score:.3f}\n")
    lines.append(f"- **P10 Score:** {report.p10_score:.3f}\n")
    lines.append(f"- **Threshold:** {report.threshold:.3f}\n")
    lines.append(f"- **Lines Below Threshold:** {report.num_below_threshold}/{len(report.line_scores)}\n")

    lines.append("\n## Models\n")
    for key, val in report.model_ids.items():
        lines.append(f"- **{key}:** `{val}`\n")

    problem_lines = [ls for ls in report.line_scores if ls.score < report.threshold]
    if problem_lines:
        lines.append("\n## Problem Lines (Score < Threshold)\n")
        lines.append("| Line | Time | Text | Score | Peak Offset |\n")
        lines.append("|------|------|------|-------|-------------|\n")
        for ls in problem_lines:
            time_str = format_timestamp(ls.timestamp)
            text_short = ls.text[:30] + "..." if len(ls.text) > 30 else ls.text
            lines.append(f"| {ls.line_idx} | {time_str} | {text_short} | {ls.score:.3f} | {ls.peak_offset:.2f}s |\n")

    # All lines sorted by score (ascending)
    lines.append("\n## All Lines (Sorted by Score)\n")
    lines.append("| Line | Time | Text | Score | DTW Score | Peak Offset |\n")
    lines.append("|------|------|------|-------|-----------|-------------|\n")
    sorted_by_score = sorted(report.line_scores, key=lambda x: x.score)
    for ls in sorted_by_score:
        time_str = format_timestamp(ls.timestamp)
        text_short = ls.text[:30] + "..." if len(ls.text) > 30 else ls.text
        dtw_str = f"{ls.dtw_score:.3f}" if ls.dtw_score is not None else "-"
        lines.append(f"| {ls.line_idx} | {time_str} | {text_short} | {ls.score:.3f} | {dtw_str} | {ls.peak_offset:.2f}s |\n")

    # All lines sorted by line index (chronological)
    lines.append("\n## All Lines (Sorted by Line Index)\n")
    lines.append("| Line | Time | Text | Score | DTW Score | Peak Offset |\n")
    lines.append("|------|------|------|-------|-----------|-------------|\n")
    sorted_by_index = sorted(report.line_scores, key=lambda x: x.line_idx)
    for ls in sorted_by_index:
        time_str = format_timestamp(ls.timestamp)
        text_short = ls.text[:30] + "..." if len(ls.text) > 30 else ls.text
        dtw_str = f"{ls.dtw_score:.3f}" if ls.dtw_score is not None else "-"
        lines.append(f"| {ls.line_idx} | {time_str} | {text_short} | {ls.score:.3f} | {dtw_str} | {ls.peak_offset:.2f}s |\n")

    md_path.write_text("".join(lines), encoding="utf-8")


# =============================================================================
# CLI
# =============================================================================

@app.command()
def main(
    song_id: str = typer.Argument(
        ..., help="Song ID (e.g., wo_yao_quan_xin_zan_mei_244) or path to files"
    ),
    stem: Optional[Path] = typer.Option(
        None, "--stem", help="Override vocal stem path"
    ),
    lrc: Optional[Path] = typer.Option(
        None, "--lrc", help="Override LRC file path"
    ),
    threshold: float = typer.Option(
        0.60, "--threshold", help="PASS/REVIEW cutoff threshold (0-1)"
    ),
    max_window: float = typer.Option(
        15.0, "--max-window", help="Maximum window duration in seconds"
    ),
    tts_cache_dir: Path = typer.Option(
        Path.home() / ".cache" / "qwen3_tts",
        "--tts-cache-dir",
        help="Directory for TTS audio cache",
    ),
    report: Optional[Path] = typer.Option(
        None, "--report", help="Output Markdown report (default: tmp_output/{song_id}.quality.md)"
    ),
    score_json: Optional[Path] = typer.Option(
        None, "--score-json", help="Output JSON scores (default: tmp_output/{song_id}.quality.json)"
    ),
    tts_model: str = typer.Option(
        "mlx-community/Qwen3-TTS-12Hz-0.6B-Base-bf16",
        "--tts-model",
        help="TTS model ID",
    ),
    tts_voice: str = typer.Option(
        "Chelsie", "--tts-voice", help="TTS voice name"
    ),
    config_path: Optional[Path] = typer.Option(
        None, "--config", "-c", help="Path to admin config file"
    ),
):
    """Score LRC quality by TTS round-trip comparison.

    Synthesizes each LRC line via Qwen3-TTS and compares against the vocal stem
    in phonetic embedding space. Outputs PASS/REVIEW status, per-line scores,
    and detailed reports.

    If --stem or --lrc are not provided, resolves them from the local cache
    (downloading from R2 if necessary).

    Installation Note:
        This script requires mlx-audio>=0.3.0 which conflicts with
        poc_qwen3_align (qwen-asr pins transformers==4.57.6).
        Install separately:
            uv pip install mlx-audio>=0.3.0 --prerelease=allow

    Exit codes: 0 = PASS, 1 = REVIEW
    """
    if not check_mlx_audio():
        typer.echo("ERROR: mlx-audio is not installed.", err=True)
        typer.echo("", err=True)
        typer.echo("To install, run:", err=True)
        typer.echo("    uv pip install mlx-audio>=0.3.0 --prerelease=allow", err=True)
        raise typer.Exit(1)

    if not check_qwen_tts_support():
        typer.echo("ERROR: mlx-audio is installed but Qwen3-TTS support is missing.", err=True)
        typer.echo("You may need to upgrade mlx-audio:", err=True)
        typer.echo("    uv pip install mlx-audio>=0.3.0 --prerelease=allow", err=True)
        raise typer.Exit(1)

    # Load config
    try:
        config = load_admin_config(config_path)
    except FileNotFoundError as e:
        typer.echo(f"Error: {e}", err=True)
        typer.echo("Run 'sow-admin config init' first.", err=True)
        raise typer.Exit(1)
    except Exception as e:
        typer.echo(f"Error loading config: {e}", err=True)
        raise typer.Exit(1)

    # Parse TOML sections
    database_config = config.get("database", {})
    r2_config = config.get("r2", {})

    db_path = Path(database_config.get("path", Path.home() / ".config" / "sow-admin" / "db" / "sow.db"))
    r2_bucket = r2_config.get("bucket")
    r2_endpoint = r2_config.get("endpoint_url")

    # Cache dir is not in config, use default location
    cache_dir = Path.home() / ".cache" / "stream-of-worship"

    if not db_path.exists():
        typer.echo(f"Error: Database not found at {db_path}", err=True)
        typer.echo("Run 'sow-admin catalog init' first.", err=True)
        raise typer.Exit(1)

    # Resolve stem path
    if stem:
        if not stem.exists():
            typer.echo(f"Error: Stem file not found: {stem}", err=True)
            raise typer.Exit(1)
        stem_path = stem
        typer.echo(f"Using provided stem: {stem_path}", err=True)
    else:
        stem_path = resolve_stem_path(song_id, cache_dir, db_path, r2_bucket, r2_endpoint)

    # Resolve LRC path
    if lrc:
        if not lrc.exists():
            typer.echo(f"Error: LRC file not found: {lrc}", err=True)
            raise typer.Exit(1)
        lrc_path = lrc
        typer.echo(f"Using provided LRC: {lrc_path}", err=True)
    else:
        lrc_path = resolve_lrc_path(song_id, cache_dir, db_path, r2_bucket, r2_endpoint)

    # Determine output paths
    safe_song_id = Path(song_id).stem if Path(song_id).exists() else song_id.replace("/", "_")
    if report is None:
        report = Path("tmp_output") / f"{safe_song_id}.quality.md"
    if score_json is None:
        score_json = Path("tmp_output") / f"{safe_song_id}.quality.json"

    report.parent.mkdir(parents=True, exist_ok=True)
    score_json.parent.mkdir(parents=True, exist_ok=True)

    typer.echo("", err=True)
    typer.echo("Note: First run will download ~2GB of models from HuggingFace:", err=True)
    typer.echo(f"  - TTS: {tts_model}", err=True)
    typer.echo("  - Embedder: facebook/wav2vec2-xls-r-300m", err=True)
    typer.echo("", err=True)

    # Run scoring
    try:
        report_data = score_lrc(
            stem_path=stem_path,
            lrc_path=lrc_path,
            threshold=threshold,
            max_window=max_window,
            tts_model=tts_model,
            tts_voice=tts_voice,
            tts_cache_dir=tts_cache_dir,
        )
    except Exception as e:
        typer.echo(f"Error during scoring: {e}", err=True)
        import traceback
        traceback.print_exc()
        raise typer.Exit(1)

    write_report_markdown(report_data, report)
    typer.echo(f"Wrote Markdown report to: {report}", err=True)

    write_report_json(report_data, score_json)
    typer.echo(f"Wrote JSON scores to: {score_json}", err=True)

    typer.echo("\n" + "=" * 50, err=True)
    typer.echo(f"Status: {report_data.status}", err=True)
    typer.echo(f"Overall Score: {report_data.overall:.3f}", err=True)
    typer.echo(f"Lines Below Threshold: {report_data.num_below_threshold}/{len(report_data.line_scores)}", err=True)
    typer.echo("=" * 50, err=True)

    if report_data.status == "PASS":
        raise typer.Exit(0)
    else:
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
