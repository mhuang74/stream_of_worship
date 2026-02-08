"""Audio analysis worker using allin1 and librosa."""

import asyncio
import logging
import tempfile
import time
from pathlib import Path
from typing import Optional

import librosa
import numpy as np

from ..storage.cache import CacheManager

logger = logging.getLogger(__name__)

# Key detection profiles (Krumhansl-Schmuckler)
MAJOR_PROFILE = np.array(
    [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
)
MINOR_PROFILE = np.array(
    [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]
)
KEYS = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def detect_key(y: np.ndarray, sr: int) -> tuple[str, str, float]:
    """Detect musical key using Krumhansl-Schmuckler key profile matching.

    Args:
        y: Audio time series
        sr: Sample rate

    Returns:
        Tuple of (mode, key, confidence)
    """
    # Compute chroma features
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr, hop_length=512)
    chroma_avg = np.mean(chroma, axis=1)

    # Find best correlation with major and minor profiles
    correlations = []
    for shift in range(12):
        major_corr = np.corrcoef(chroma_avg, np.roll(MAJOR_PROFILE, shift))[0, 1]
        minor_corr = np.corrcoef(chroma_avg, np.roll(MINOR_PROFILE, shift))[0, 1]
        correlations.append(("major", KEYS[shift], major_corr))
        correlations.append(("minor", KEYS[shift], minor_corr))

    best_key = max(correlations, key=lambda x: x[2])
    return best_key[0], best_key[1], best_key[2]


def compute_loudness(y: np.ndarray) -> float:
    """Compute integrated loudness in dB.

    Args:
        y: Audio time series

    Returns:
        Loudness in dB
    """
    # Simple RMS-based loudness estimate
    rms = np.sqrt(np.mean(y**2))
    db = 20 * np.log10(rms + 1e-10)
    return float(db)


async def analyze_audio(
    audio_path: Path,
    cache_manager: CacheManager,
) -> dict:
    """Analyze audio file using allin1 + librosa.

    Steps:
    1. Check cache (if not force)
    2. Load audio with librosa
    3. Run allin1.analyze() for tempo/beats/sections/embeddings
    4. Run librosa chroma analysis for key detection
    5. Compute loudness/energy metrics
    6. Save to cache
    7. Return results dict

    Args:
        audio_path: Path to audio file
        cache_manager: Cache manager instance

    Returns:
        Dictionary with all analysis fields
    """
    import allin1

    # Check cache first
    cached = cache_manager.get_analysis_result(audio_path.stem)
    if cached:
        logger.info(f"Cache hit for analysis result: {audio_path.stem}")
        return cached

    # Load audio
    logger.info(f"Loading audio file: {audio_path}")
    load_start = time.time()
    y, sr = librosa.load(str(audio_path), sr=None, mono=True)
    duration = librosa.get_duration(y=y, sr=sr)
    load_elapsed = time.time() - load_start
    logger.info(f"Audio loaded in {load_elapsed:.2f}s - Duration: {duration:.2f}s")

    # Run allin1 analysis in thread pool (it's blocking)
    # Use isolated temp directory to prevent concurrent jobs from mixing outputs
    logger.info("Starting allin1 analysis (tempo, beats, sections, embeddings)")
    allin1_start = time.time()
    loop = asyncio.get_event_loop()

    with tempfile.TemporaryDirectory() as temp_dir:

        def run_allin1():
            return allin1.analyze(
                str(audio_path),
                out_dir=temp_dir,
                visualize=False,
                include_embeddings=True,
                sonify=False,
            )

        result = await loop.run_in_executor(None, run_allin1)

    allin1_elapsed = time.time() - allin1_start
    logger.info(f"allin1 analysis completed in {allin1_elapsed:.2f}s")

    # Extract allin1 results
    bpm = result.bpm

    beats = result.beats
    if isinstance(beats, np.ndarray):
        beats = beats.tolist()
    else:
        beats = list(beats)

    downbeats = result.downbeats
    if isinstance(downbeats, np.ndarray):
        downbeats = downbeats.tolist()
    else:
        downbeats = list(downbeats)

    sections = [
        {"label": seg.label, "start": seg.start, "end": seg.end}
        for seg in result.segments
    ]

    embeddings_shape = list(result.embeddings.shape)

    # Key detection with librosa
    logger.info("Detecting musical key...")
    key_start = time.time()
    mode, key, key_confidence = detect_key(y, sr)
    key_elapsed = time.time() - key_start
    logger.info(f"Key detection completed in {key_elapsed:.2f}s - Detected: {key} {mode}")

    # Loudness
    loudness_db = compute_loudness(y)

    total_elapsed = time.time() - load_start
    logger.info(f"Total analysis time: {total_elapsed:.2f}s")

    # Build result
    analysis_result = {
        "duration_seconds": duration,
        "tempo_bpm": bpm,
        "musical_key": key,
        "musical_mode": mode,
        "key_confidence": key_confidence,
        "loudness_db": loudness_db,
        "beats": beats,
        "downbeats": downbeats,
        "sections": sections,
        "embeddings_shape": embeddings_shape,
    }

    # Save to cache
    cache_manager.save_analysis_result(audio_path.stem, analysis_result)

    return analysis_result
