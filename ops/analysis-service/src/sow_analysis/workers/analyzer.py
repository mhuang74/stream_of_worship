"""Audio analysis worker using allin1 and librosa."""

import asyncio
import logging
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import librosa
import numpy as np

from ..config import settings
from ..storage.cache import CacheManager
from . import cps as cps_module

logger = logging.getLogger(__name__)

# Key detection profiles (Krumhansl-Schmuckler)
MAJOR_PROFILE = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
MINOR_PROFILE = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
KEYS = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


@dataclass(frozen=True)
class KeyCandidate:
    key: str
    mode: str
    score: float
    window_votes: Optional[int]
    source: str


@dataclass(frozen=True)
class KeyDetectionResult:
    key: str
    mode: str
    confidence: float
    score_margin: Optional[float]
    window_agreement: Optional[float]
    candidates: list[KeyCandidate]
    algorithm_version: str
    detected_at: str

    def to_analysis_fields(self) -> dict:
        return {
            "musical_key": self.key,
            "musical_mode": self.mode,
            "key_confidence": self.confidence,
            "key_algorithm_version": self.algorithm_version,
            "key_score_margin": self.score_margin,
            "key_window_agreement": self.window_agreement,
            "key_candidates": [asdict(candidate) for candidate in self.candidates],
            "key_detected_at": self.detected_at,
        }


def _score_chroma(chroma_avg: np.ndarray) -> list[tuple[str, str, float]]:
    correlations = []
    for shift in range(12):
        major_corr = np.corrcoef(chroma_avg, np.roll(MAJOR_PROFILE, shift))[0, 1]
        minor_corr = np.corrcoef(chroma_avg, np.roll(MINOR_PROFILE, shift))[0, 1]
        correlations.append(("major", KEYS[shift], float(np.nan_to_num(major_corr))))
        correlations.append(("minor", KEYS[shift], float(np.nan_to_num(minor_corr))))
    return correlations


def detect_key_fulltrack(y: np.ndarray, sr: int) -> KeyDetectionResult:
    """Detect musical key using Krumhansl-Schmuckler key profile matching.

    Args:
        y: Audio time series
        sr: Sample rate

    Returns:
        Structured key detection result.
    """
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr, hop_length=512)
    chroma_avg = np.mean(chroma, axis=1)
    correlations = sorted(_score_chroma(chroma_avg), key=lambda x: x[2], reverse=True)
    best = correlations[0]
    second = correlations[1] if len(correlations) > 1 else None
    candidates = [
        KeyCandidate(
            key=key, mode=mode, score=score, window_votes=None, source="fulltrack_correlation"
        )
        for mode, key, score in correlations[:5]
    ]
    return KeyDetectionResult(
        key=best[1],
        mode=best[0],
        confidence=best[2],
        score_margin=best[2] - second[2] if second else None,
        window_agreement=None,
        candidates=candidates,
        algorithm_version="ks_fulltrack_v1",
        detected_at=datetime.now(timezone.utc).isoformat(),
    )


def _window_bounds(
    duration: float, segments: Optional[list[dict]] = None
) -> list[tuple[float, float]]:
    if segments:
        return [
            (float(segment["start"]), float(segment["end"]))
            for segment in segments
            if float(segment["end"]) - float(segment["start"]) >= 8.0
        ]
    if duration < 8.0:
        return [(0.0, duration)]
    window = 20.0
    step = 10.0
    starts = np.arange(0.0, max(duration - 8.0, 0.0), step)
    return [(float(start), float(min(start + window, duration))) for start in starts]


def detect_key_segment_vote(
    y: np.ndarray,
    sr: int,
    segments: Optional[list[dict]] = None,
    algorithm_version: str = "ks_segment_vote_v1",
) -> KeyDetectionResult:
    """Detect key by aggregating harmonic chroma votes across sections/windows."""
    hop_length = 512
    duration = librosa.get_duration(y=y, sr=sr)
    y_harmonic, _ = librosa.effects.hpss(y)
    chroma = librosa.feature.chroma_cqt(y=y_harmonic, sr=sr, hop_length=hop_length)
    rms = librosa.feature.rms(y=y_harmonic, frame_length=2048, hop_length=hop_length)[0]
    rms_cutoff = float(np.percentile(rms, 10)) if rms.size else 0.0
    rms_cap = float(np.percentile(rms, 90)) if rms.size else 1.0
    aggregate: dict[tuple[str, str], float] = {}
    vote_counts: dict[tuple[str, str], int] = {}
    total_weight = 0.0
    accepted = 0

    windows = _window_bounds(duration, segments)
    for index, (start, end) in enumerate(windows):
        start_frame = librosa.time_to_frames(start, sr=sr, hop_length=hop_length)
        end_frame = librosa.time_to_frames(end, sr=sr, hop_length=hop_length)
        if end - start < 8.0 or end_frame <= start_frame:
            continue
        window_chroma = chroma[:, start_frame:end_frame]
        window_rms = rms[start_frame:end_frame]
        if window_rms.size and float(np.mean(window_rms)) < rms_cutoff:
            continue
        chroma_avg = np.mean(window_chroma, axis=1)
        if float(np.max(chroma_avg) - np.min(chroma_avg)) < 0.1:
            continue
        scores = sorted(_score_chroma(chroma_avg), key=lambda x: x[2], reverse=True)
        if len(scores) > 1 and scores[0][2] - scores[1][2] < 0.03:
            continue
        mode, key, score = scores[0]
        window_duration = end - start
        rms_weight = min(float(np.mean(window_rms)) if window_rms.size else 1.0, rms_cap)
        weight = window_duration * max(rms_weight, 1e-6)
        if index == 0 and window_duration < 20.0:
            weight *= 0.5
        if index == len(windows) - 1 and window_duration < 20.0:
            weight *= 0.5
        aggregate[(mode, key)] = aggregate.get((mode, key), 0.0) + weight * score
        vote_counts[(mode, key)] = vote_counts.get((mode, key), 0) + 1
        total_weight += weight
        accepted += 1

    if not aggregate:
        return detect_key_fulltrack(y, sr)

    normalized = {
        key: score / total_weight if total_weight > 0 else score for key, score in aggregate.items()
    }
    ranked = sorted(normalized.items(), key=lambda item: item[1], reverse=True)
    (best_mode, best_key), best_score = ranked[0]
    second_score = ranked[1][1] if len(ranked) > 1 else None
    candidates = [
        KeyCandidate(
            key=key,
            mode=mode,
            score=float(score),
            window_votes=vote_counts.get((mode, key), 0),
            source="segment_vote",
        )
        for (mode, key), score in ranked[:5]
    ]
    winning_votes = sum(count for (mode, key), count in vote_counts.items() if key == best_key)
    return KeyDetectionResult(
        key=best_key,
        mode=best_mode,
        confidence=float(best_score),
        score_margin=float(best_score - second_score) if second_score is not None else None,
        window_agreement=winning_votes / accepted if accepted else None,
        candidates=candidates,
        algorithm_version=algorithm_version,
        detected_at=datetime.now(timezone.utc).isoformat(),
    )


def detect_key(y: np.ndarray, sr: int, segments: Optional[list[dict]] = None) -> KeyDetectionResult:
    algorithm = settings.KEY_ALGORITHM_VERSION
    if algorithm == "ks_fulltrack_v1":
        return detect_key_fulltrack(y, sr)
    if algorithm == "ks_segment_vote_v1":
        return detect_key_segment_vote(y, sr, segments, algorithm_version=algorithm)
    if algorithm == "ks_window_vote_v1":
        return detect_key_segment_vote(y, sr, None, algorithm_version=algorithm)
    raise ValueError(f"Unsupported KEY_ALGORITHM_VERSION: {algorithm}")


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
    content_hash: str,
    *,
    force: bool = False,
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
        content_hash: SHA-256 hash of audio content for cache key

    Returns:
        Dictionary with all analysis fields
    """
    import allin1

    # Check cache first (unless force)
    if not force:
        cached = cache_manager.get_analysis_result(content_hash)
        if cached:
            logger.info(f"Cache hit for analysis result: {content_hash[:16]}...")
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

    sections = [{"label": seg.label, "start": seg.start, "end": seg.end} for seg in result.segments]

    embeddings_shape = list(result.embeddings.shape)

    # Key detection with librosa
    logger.info("Detecting musical key...")
    key_start = time.time()
    key_result = detect_key(y, sr, sections)
    key_elapsed = time.time() - key_start
    logger.info(
        f"Key detection completed in {key_elapsed:.2f}s - "
        f"Detected: {key_result.key} {key_result.mode}"
    )

    # Loudness
    loudness_db = compute_loudness(y)

    total_elapsed = time.time() - load_start
    logger.info(f"Total analysis time: {total_elapsed:.2f}s")

    # Build result
    analysis_result = {
        "duration_seconds": duration,
        "tempo_bpm": bpm,
        **key_result.to_analysis_fields(),
        "loudness_db": loudness_db,
        "beats": beats,
        "downbeats": downbeats,
        "sections": sections,
        "embeddings_shape": embeddings_shape,
    }

    # Save to cache
    cache_manager.save_analysis_result(content_hash, analysis_result)

    return analysis_result


def _compute_tempo_v4(y: np.ndarray, sr: int, hop_length: int, start_bpm: float) -> float:
    """v4 tempo estimation: start_bpm prior + double/half-time octave guard.

    Preserved verbatim from the original ``_compute_tempo`` inner function
    for fallback use by the v5 path when LRC is missing.
    """
    onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop_length)

    # Primary estimate with worship-music prior
    tempo_primary = librosa.beat.tempo(
        onset_envelope=onset_env,
        sr=sr,
        hop_length=hop_length,
        start_bpm=start_bpm,
    )
    if hasattr(tempo_primary, "__iter__"):
        tempo_primary = float(tempo_primary[0])
    tempo_primary = float(tempo_primary)

    # Double-time guard: if primary is suspiciously fast, re-estimate with
    # a 60 BPM prior to probe the half-time peak. Handles slow worship songs
    # (~65-75 BPM true) whose onset envelope peaks at twice the beat rate
    # (eighth-/sixteenth-note patterns) and is reported at ~2x true tempo.
    if tempo_primary > 120.0:
        tempo_alt = librosa.beat.tempo(
            onset_envelope=onset_env,
            sr=sr,
            hop_length=hop_length,
            start_bpm=60.0,
        )
        if hasattr(tempo_alt, "__iter__"):
            tempo_alt = float(tempo_alt[0])
        tempo_alt = float(tempo_alt)

        # Accept the half-time if it is roughly half the primary AND lands
        # in the worship-plausible range (65-100 BPM).
        if abs(tempo_alt - tempo_primary / 2.0) < 8.0 and 65.0 <= tempo_alt <= 100.0:
            return tempo_alt

    # Half-time guard (v4): if primary is below the worship-plausible floor
    # (< 60 BPM), re-estimate with the 120 BPM prior to probe the
    # double-time peak. Handles edge-case fast songs (true tempo > 110 BPM)
    # without over-correcting the predominantly 60-100 BPM worship catalog.
    # NOTE: threshold lowered from v3's < 65 to < 60 because 60-65 BPM is a
    # legitimate slow worship tempo (e.g. "我活著要稱頌祢" at 64.6 BPM true).
    # The v3 < 65 threshold misfired on 64.6 and doubled it to 129.2.
    # Acceptance range floor raised from 100 to 110 so doublings of primaries
    # in [50, 55) (which yield alts in [100, 110)) are rejected as implausible;
    # ceiling raised from 160 to 180 to accommodate doublings of primaries up
    # to 90 (defense-in-depth; in practice only primaries < 60 trigger).
    elif tempo_primary < 60.0:
        tempo_alt = librosa.beat.tempo(
            onset_envelope=onset_env,
            sr=sr,
            hop_length=hop_length,
            start_bpm=120.0,
        )
        if hasattr(tempo_alt, "__iter__"):
            tempo_alt = float(tempo_alt[0])
        tempo_alt = float(tempo_alt)

        # If the alternative is roughly double the primary AND lands in the
        # genuinely-fast range, the primary was half-time.
        if abs(tempo_alt - 2.0 * tempo_primary) < 8.0 and 110.0 <= tempo_alt <= 180.0:
            return tempo_alt

    return tempo_primary


def _compute_tempo_v5(
    y: np.ndarray,
    sr: int,
    hop_length: int,
    lrc_content: Optional[str],
    start_bpm: float,
) -> float:
    """v5 tempo estimation: CPS-derived lognormal prior (skips octave guard).

    When ``lrc_content`` is present and parseable into a CPS value, a
    lognormal prior is built and passed to ``librosa.beat.tempo``. The prior
    resolves octave ambiguity, so the v4 octave guard is skipped.

    When CPS is None (LRC missing or unparseable), delegates to the v4 path
    (``_compute_tempo_v4``) for identical fallback behavior.
    """
    cps_value, _meta = cps_module.compute_cps(lrc_content) if lrc_content else (None, None)
    prior = cps_module.cps_to_prior(cps_value)

    onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop_length)

    if prior is not None:
        tempo_primary = librosa.beat.tempo(
            onset_envelope=onset_env,
            sr=sr,
            hop_length=hop_length,
            prior=prior,
        )
        if hasattr(tempo_primary, "__iter__"):
            tempo_primary = float(tempo_primary[0])
        tempo_primary = float(tempo_primary)
        # Prior already encodes the half/double-time belief — skip octave guard.
        return tempo_primary
    else:
        # CPS missing — fall back to v4 behavior.
        return _compute_tempo_v4(y, sr, hop_length, start_bpm)


async def analyze_audio_fast(
    audio_path: Path,
    cache_manager: CacheManager,
    content_hash: str,
    sample_rate: int = 22050,
    hop_length: int = 512,
    start_bpm: float = 80.0,
    force: bool = False,
    lrc_content: Optional[str] = None,
) -> dict:
    """Fast audio analysis using librosa only (no allin1, no stems).

    Produces only the fast-tier subset: duration_seconds, tempo_bpm,
    musical_key, musical_mode, key_confidence, loudness_db.

    Key/BPM algorithms:
      - tempo: librosa.beat.tempo over a onset strength envelope
      - key:   Krumhansl-Schmuckler via librosa.feature.chroma_cqt
      - loudness: RMS-based dB

    Args:
        audio_path: Path to audio file
        cache_manager: Cache manager instance
        content_hash: SHA-256 hash of audio content for cache key
        sample_rate: Target sample rate for librosa.load (default 22050)
        hop_length: Hop length for onset/tempo estimation (default 512)
        start_bpm: Initial tempo guess for the log-normal prior (default 80).
            Worship music typically has tempos 65-95 BPM; the librosa default
            of 120 biases toward double-time octave errors.
        force: Bypass cache
        lrc_content: Optional LRC lyrics text for CPS-based prod-v5 prior.
            When provided and BPM_ALGORITHM_VERSION=v5_cps_prior, a lognormal
            prior is derived from the CPS value. When None or empty, the v5
            path falls back to v4 behavior.

    Returns:
        Dictionary with the fast-tier analysis fields
    """
    # Check cache first (unless force)
    if not force:
        cached = cache_manager.get_fast_analyze_result(content_hash)
        if cached:
            logger.info(f"Cache hit for fast analysis result: {content_hash[:16]}...")
            return cached

    loop = asyncio.get_event_loop()

    # Load audio (blocking — run in executor)
    logger.info(f"Loading audio file for fast analysis: {audio_path}")
    load_start = time.time()

    def _load_audio():
        y, sr = librosa.load(str(audio_path), sr=sample_rate, mono=True)
        duration = librosa.get_duration(y=y, sr=sr)
        return y, sr, duration

    y, sr, duration = await loop.run_in_executor(None, _load_audio)
    load_elapsed = time.time() - load_start
    logger.info(f"Audio loaded in {load_elapsed:.2f}s - Duration: {duration:.2f}s")

    # Tempo via librosa.beat.tempo
    logger.info("Estimating tempo...")
    tempo_start = time.time()

    algorithm = settings.BPM_ALGORITHM_VERSION
    if algorithm == "v5_cps_prior":
        bpm = await loop.run_in_executor(
            None, _compute_tempo_v5, y, sr, hop_length, lrc_content, start_bpm
        )
    elif algorithm == "v4_octave_guard":
        bpm = await loop.run_in_executor(None, _compute_tempo_v4, y, sr, hop_length, start_bpm)
    else:
        raise ValueError(f"Unsupported BPM_ALGORITHM_VERSION: {algorithm}")

    tempo_elapsed = time.time() - tempo_start
    logger.info(f"Tempo estimation completed in {tempo_elapsed:.2f}s - {bpm:.1f} BPM")

    # Key detection with librosa (blocking — run in executor)
    logger.info("Detecting musical key...")
    key_start = time.time()
    key_result = await loop.run_in_executor(None, detect_key, y, sr, None)
    key_elapsed = time.time() - key_start
    logger.info(
        f"Key detection completed in {key_elapsed:.2f}s - "
        f"Detected: {key_result.key} {key_result.mode}"
    )

    # Loudness (blocking — run in executor)
    loudness_db = await loop.run_in_executor(None, compute_loudness, y)

    total_elapsed = time.time() - load_start
    logger.info(f"Total fast analysis time: {total_elapsed:.2f}s")

    # Build result (fast subset only)
    analysis_result = {
        "duration_seconds": duration,
        "tempo_bpm": bpm,
        **key_result.to_analysis_fields(),
        "loudness_db": loudness_db,
    }

    # Save to cache (distinct from full-tier cache)
    cache_manager.save_fast_analyze_result(content_hash, analysis_result)

    return analysis_result
