#!/usr/bin/env python3
"""LRC quality scorer via Qwen3-TTS round-trip comparison.

Synthesizes speech from each LRC line via Qwen3-TTS on MLX, then compares
against the original vocal stem in phonetic embedding space to detect
content errors.

Usage:
    PYTHONPATH=. uv run --extra score_lrc python poc/score_lrc_quality.py \
        --stem tmp_input/wo_yao_clean_vocals.flac \
        --lrc tmp_output/wo_yao.lrc \
        --report tmp_output/wo_yao.quality.md \
        --score-json tmp_output/wo_yao.quality.json

Installation Notes:
    This script requires mlx-audio>=0.3.0 for Qwen3-TTS support. Due to
    dependency conflicts with transcription_qwen3 (which requires qwen-asr
    that pins transformers==4.57.6), mlx-audio must be installed separately:

        uv pip install mlx-audio>=0.3.0 --prerelease=allow

    This will upgrade transformers to 5.0.0rc3 which is required for
    Qwen3-TTS support.
"""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import typer


def format_timestamp(seconds: float) -> str:
    """Format seconds as [mm:ss.xx] timestamp."""
    minutes = int(seconds // 60)
    secs = seconds % 60
    return f"[{minutes:02d}:{secs:05.2f}]"


# LRC Parser (copied from src/stream_of_worship/admin/services/lrc_parser.py)
# We inline this to avoid importing the full package which has many dependencies

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
    # Match [mm:ss.xx] or [mm:ss.xxx] format
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
        """Synthesize audio from text.

        Args:
            text: Text to synthesize
            voice: Voice name (e.g., "Chelsie")
            language: Language code

        Returns:
            Audio as float32 numpy array
        """
        import mlx.core as mx

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
        """Extract phonetic embedding from audio.

        Args:
            audio: Audio samples as float32 array
            sample_rate: Sample rate of audio

        Returns:
            Mean-pooled embedding vector
        """
        import torch

        # Process through feature extractor
        inputs = self._processor(
            audio,
            sampling_rate=sample_rate,
            return_tensors="pt",
            padding=True,
        )

        # Move to device
        input_values = inputs.input_values.to(self._device)

        # Forward pass
        with torch.no_grad():
            outputs = self._model(input_values)

        # Mean pool the hidden states across time dimension
        # outputs.last_hidden_state shape: (batch, time, hidden_dim)
        embeddings = outputs.last_hidden_state.mean(dim=1)  # (batch, hidden_dim)

        return embeddings.cpu().numpy().squeeze()

    def embed_framewise(self, audio: np.ndarray, sample_rate: int = 16000) -> np.ndarray:
        """Extract framewise phonetic embeddings from audio.

        Args:
            audio: Audio samples as float32 array
            sample_rate: Sample rate of audio

        Returns:
            Framewise embeddings array of shape (time, hidden_dim)
        """
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

        # Return framewise embeddings: (time, hidden_dim)
        return outputs.last_hidden_state.squeeze(0).cpu().numpy()


# =============================================================================
# TTS Cache
# =============================================================================

def get_cache_path(text: str, voice: str, model_id: str, cache_dir: Path) -> Path:
    """Get cache path for TTS audio.

    Args:
        text: Text to synthesize
        voice: Voice name
        model_id: TTS model ID
        cache_dir: Base cache directory

    Returns:
        Path to cached WAV file
    """
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
    """Synthesize audio with caching.

    Args:
        text: Text to synthesize
        tts: TTS model instance
        voice: Voice name
        cache_dir: Cache directory
        normalize_zh: Whether to normalize Traditional to Simplified Chinese

    Returns:
        Tuple of (audio_array, sample_rate)
    """
    import soundfile as sf
    from zhconv import convert

    # Normalize Chinese characters
    if normalize_zh:
        text = convert(text, "zh-hans")

    cache_path = get_cache_path(text, voice, tts._model_id, cache_dir)

    if cache_path.exists():
        audio, sr = sf.read(str(cache_path), dtype="float32")
        return audio, sr

    # Synthesize
    audio = tts.synthesize(text, voice=voice, language="Mandarin")
    sr = tts.sample_rate

    # Save to cache
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
    """Load a time window from the stem audio.

    Args:
        stem_path: Path to stem audio file
        start: Start time in seconds
        end: End time in seconds
        target_sr: Target sample rate

    Returns:
        Audio samples as float32 mono array at target_sr
    """
    import librosa
    import soundfile as sf

    # Load with soundfile first to get native sample rate
    audio, sr = sf.read(str(stem_path), dtype="float32")

    # Convert to mono if stereo
    if audio.ndim > 1:
        audio = audio.mean(axis=1)

    # Extract window
    start_sample = int(start * sr)
    end_sample = int(end * sr)
    audio = audio[start_sample:end_sample]

    # Resample to target if needed
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
    """Compute normalized DTW distance between two sequences.

    Args:
        x: First sequence of shape (time_x, dim)
        y: Second sequence of shape (time_y, dim)

    Returns:
        Normalized DTW similarity (higher = more similar)
    """
    from scipy.spatial.distance import cdist

    # Compute pairwise distances
    dist_matrix = cdist(x, y, metric="cosine")

    # DTW algorithm
    n, m = dist_matrix.shape
    dtw = np.zeros((n + 1, m + 1))
    dtw[0, 1:] = np.inf
    dtw[1:, 0] = np.inf
    dtw[0, 0] = 0

    for i in range(1, n + 1):
        for j in range(1, m + 1):
            cost = dist_matrix[i - 1, j - 1]
            dtw[i, j] = cost + min(dtw[i - 1, j], dtw[i, j - 1], dtw[i - 1, j - 1])

    # Normalize by path length and convert to similarity
    max_dist = 2.0  # Maximum cosine distance is 2
    normalized_dist = dtw[n, m] / (n + m)
    similarity = 1.0 - (normalized_dist / max_dist)

    return float(np.clip(similarity, 0.0, 1.0))


def find_peak_offset(
    tts_framewise: np.ndarray,
    stem_framewise: np.ndarray,
    hop_seconds: float = 0.02,
) -> float:
    """Find the time offset where TTS best matches stem via sliding window.

    Args:
        tts_framewise: TTS framewise embeddings (time_tts, dim)
        stem_framewise: Stem framewise embeddings (time_stem, dim)
        hop_seconds: Seconds per frame (default 20ms for wav2vec2)

    Returns:
        Peak offset in seconds from start of stem window
    """
    if len(stem_framewise) < len(tts_framewise):
        return 0.0

    # Slide TTS over stem and compute similarity at each position
    num_positions = len(stem_framewise) - len(tts_framewise) + 1
    similarities = []

    for i in range(num_positions):
        stem_slice = stem_framewise[i : i + len(tts_framewise)]
        # Use mean cosine similarity across aligned frames
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
    """Score a single line by comparing stem and TTS audio.

    Args:
        stem_audio: Stem audio window
        tts_audio: TTS synthesized audio
        embedder: Embedding model
        use_dtw: Whether to compute DTW alignment score

    Returns:
        Dict with score, dtw_score (optional), and peak_offset
    """
    # Get embeddings
    tts_embed = embedder.embed(tts_audio)
    stem_embed = embedder.embed(stem_audio)

    # Cosine similarity
    score = cosine_similarity(tts_embed, stem_embed)

    result = {"score": score, "dtw_score": None, "peak_offset": 0.0}

    # DTW for borderline cases
    if use_dtw or (0.4 <= score <= 0.7):
        tts_frames = embedder.embed_framewise(tts_audio)
        stem_frames = embedder.embed_framewise(stem_audio)
        result["dtw_score"] = dtw_distance(tts_frames, stem_frames)

    # Find peak offset
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
    """Score an LRC file against its vocal stem.

    Args:
        stem_path: Path to vocal stem audio file
        lrc_path: Path to LRC file
        threshold: PASS/REVIEW threshold
        max_window: Maximum window duration in seconds
        tts_model: TTS model ID
        tts_voice: TTS voice name
        tts_cache_dir: TTS cache directory

    Returns:
        ScoreReport with per-line and overall scores
    """
    # Initialize models
    tts = MLXQwen3TTS(model_id=tts_model)
    embedder = Wav2Vec2Embedder()

    # Parse LRC
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

    # Get stem duration
    import soundfile as sf

    stem_info = sf.info(str(stem_path))
    stem_duration = stem_info.duration

    typer.echo(f"Scoring {len(lrc_file.lines)} lines...", err=True)

    for i, line in enumerate(lrc_file.lines):
        # Determine window end
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

        # Synthesize TTS
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

        # Load stem window
        try:
            stem_audio = load_stem_window(stem_path, window_start, window_end)
        except Exception as e:
            typer.echo(f"Error loading stem for line {i+1}: {e}", err=True)
            continue

        # Score
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

    # Compute overall statistics
    if report.line_scores:
        scores = [ls.score for ls in report.line_scores]
        report.overall = float(np.mean(scores))
        report.min_score = float(np.min(scores))
        report.p10_score = float(np.percentile(scores, 10))
        report.num_below_threshold = sum(1 for s in scores if s < threshold)

        # Determine status
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

    # Problem lines
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
    sorted_lines = sorted(report.line_scores, key=lambda x: x.score)
    for ls in sorted_lines:
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
    stem: Path = typer.Option(..., "--stem", help="Path to vocal stem audio file (FLAC, WAV, etc.)"),
    lrc: Path = typer.Option(..., "--lrc", help="Path to LRC file to score"),
    threshold: float = typer.Option(0.60, "--threshold", help="PASS/REVIEW cutoff threshold (0-1)"),
    max_window: float = typer.Option(15.0, "--max-window", help="Maximum window duration in seconds"),
    tts_cache_dir: Path = typer.Option(
        Path.home() / ".cache" / "qwen3_tts",
        "--tts-cache-dir",
        help="Directory for TTS audio cache",
    ),
    report: Optional[Path] = typer.Option(None, "--report", help="Path to write Markdown report"),
    score_json: Optional[Path] = typer.Option(None, "--score-json", help="Path to write JSON scores"),
    tts_model: str = typer.Option(
        "mlx-community/Qwen3-TTS-12Hz-0.6B-Base-bf16",
        "--tts-model",
        help="TTS model ID",
    ),
    tts_voice: str = typer.Option("Chelsie", "--tts-voice", help="TTS voice name"),
):
    """Score LRC quality by TTS round-trip comparison.

    Synthesizes each LRC line via Qwen3-TTS and compares against the vocal stem
    in phonetic embedding space. Outputs PASS/REVIEW status, per-line scores,
    and detailed reports.

    Installation Note:
        This script requires mlx-audio>=0.3.0 which conflicts with
        transcription_qwen3 (qwen-asr pins transformers==4.57.6).
        Install separately:
            uv pip install mlx-audio>=0.3.0 --prerelease=allow

    Exit codes: 0 = PASS, 1 = REVIEW
    """
    # Check dependencies
    if not check_mlx_audio():
        typer.echo("ERROR: mlx-audio is not installed.", err=True)
        typer.echo("", err=True)
        typer.echo("To install, run:", err=True)
        typer.echo("    uv pip install mlx-audio>=0.3.0 --prerelease=allow", err=True)
        typer.echo("", err=True)
        typer.echo("Note: This will upgrade transformers to 5.0.0rc3 which conflicts", err=True)
        typer.echo("with transcription_qwen3. Use a separate venv for LRC scoring.", err=True)
        raise typer.Exit(1)

    if not check_qwen_tts_support():
        typer.echo("ERROR: mlx-audio is installed but Qwen3-TTS support is missing.", err=True)
        typer.echo("You may need to upgrade mlx-audio:", err=True)
        typer.echo("    uv pip install mlx-audio>=0.3.0 --prerelease=allow", err=True)
        raise typer.Exit(1)

    # Validate inputs
    if not stem.exists():
        typer.echo(f"Error: Stem file not found: {stem}", err=True)
        raise typer.Exit(1)

    if not lrc.exists():
        typer.echo(f"Error: LRC file not found: {lrc}", err=True)
        raise typer.Exit(1)

    # Warn about model downloads
    typer.echo("Note: First run will download ~2GB of models from HuggingFace:", err=True)
    typer.echo(f"  - TTS: {tts_model}", err=True)
    typer.echo("  - Embedder: facebook/wav2vec2-xls-r-300m", err=True)
    typer.echo("", err=True)

    # Run scoring
    try:
        report_data = score_lrc(
            stem_path=stem,
            lrc_path=lrc,
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

    # Write reports
    if report:
        write_report_markdown(report_data, report)
        typer.echo(f"Wrote Markdown report to: {report}", err=True)

    if score_json:
        write_report_json(report_data, score_json)
        typer.echo(f"Wrote JSON scores to: {score_json}", err=True)

    # Summary
    typer.echo("\n" + "=" * 50, err=True)
    typer.echo(f"Status: {report_data.status}", err=True)
    typer.echo(f"Overall Score: {report_data.overall:.3f}", err=True)
    typer.echo(f"Lines Below Threshold: {report_data.num_below_threshold}/{len(report_data.line_scores)}", err=True)
    typer.echo("=" * 50, err=True)

    # Exit code
    if report_data.status == "PASS":
        raise typer.Exit(0)
    else:
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
