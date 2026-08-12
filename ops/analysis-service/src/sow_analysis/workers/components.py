"""Song component extraction (chorus/verse identification + per-component features).

Hybrid strategy:
  1. Cached allin1 section labels (free — already computed during full analysis).
  2. Lyrics-repetition clustering from LRC lines (multi-cue v3 disambiguation).

Per-component audio features (BPM, key, groove_density, backbeat_strength,
energy_level) are computed via librosa on the audio slice regardless of the
identification source.
"""

import asyncio
import logging
import re
import string
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import librosa
import numpy as np

from ..config import settings
from ..storage.cache import BEAT_GRID_SCHEMA_VERSION, COMPONENT_SCHEMA_VERSION, CacheManager
from ..storage.r2 import R2Client
from .lrc_parser import parse_lrc

logger = logging.getLogger(__name__)

# Lyrical content cues that nudge chorus identification.


def _detect_downbeats_madmom(
    audio_path: Path,
) -> Optional[dict]:
    """Detect beats + downbeats via madmom's two-stage pipeline.

    madmom API (correct usage):
      1. RNNDownBeatProcessor() takes a FILE PATH (not numpy array),
         returns activations array at 100 fps.
      2. DBNDownBeatTrackingProcessor(beats_per_bar=[3,4], fps=100) takes
         activations, returns [[time, beat_in_bar], ...].
      3. Downbeats = rows where beat_in_bar == 1.

    Note: madmom resamples to 44100 Hz internally. fps=100 must match between
    RNNDownBeatProcessor and DBNDownBeatTrackingProcessor.

    Args:
        audio_path: Path to audio file.

    Returns:
        A partial beat-grid payload (source/beats/downbeats/detected_at/
        madmom_params). Identity fields (schema_version/content_hash/
        hash_prefix) are stamped by get_or_detect_beat_grid, which knows the
        content hash. None if detection fails.
    """
    try:
        from madmom.features.downbeats import (
            RNNDownBeatProcessor,
            DBNDownBeatTrackingProcessor,
        )

        rnn = RNNDownBeatProcessor()
        activations = rnn(str(audio_path))

        dbn = DBNDownBeatTrackingProcessor(
            beats_per_bar=[3, 4],  # model 3/4 and 4/4 time
            fps=100,               # must match RNNDownBeatProcessor's internal fps
        )
        grid = dbn(activations)  # shape (num_beats, 2): [time, beat_in_bar]

        # Downbeats are where beat_in_bar == 1
        downbeat_times = grid[grid[:, 1] == 1][:, 0]
        return {
            "source": "madmom",
            "beats": grid.tolist(),
            "downbeats": sorted(downbeat_times.tolist()),
            "detected_at": datetime.now(timezone.utc).isoformat(),
            "madmom_params": {"beats_per_bar": [3, 4], "fps": 100},
        }
    except Exception as e:
        logger.warning(f"madmom downbeat detection failed: {e}")
        return None


async def get_or_detect_beat_grid(
    audio_path: Path,
    content_hash: str,
    cache_manager: CacheManager,
    r2_client: Optional[R2Client],
    skip_beat_cache: bool = False,
) -> Optional[dict]:
    """Return the cached beat grid, detecting + caching on miss.

    Read order (skipped entirely when skip_beat_cache=True):
      1. Local cache ({hash32}_beat_grid.json)
      2. R2 ({hash12}/beat_grid.json) — on hit, backfill local cache

    On miss: run _detect_downbeats_madmom in an executor, stamp identity
    fields, persist local (atomic) + R2 (best-effort; failure logs a warning).
    Returns the payload dict or None if detection fails.

    Args:
        audio_path: Path to the audio file.
        content_hash: Full SHA-256 content hash.
        cache_manager: Local cache manager.
        r2_client: Optional R2 client for remote cache.
        skip_beat_cache: If True, bypass cache reads (detection still writes).

    Returns:
        Beat-grid payload dict or None if detection fails.
    """
    hash_prefix = content_hash[:12]

    if not skip_beat_cache:
        cached = cache_manager.get_beat_grid(content_hash)
        if cached is not None:
            logger.info(f"Beat grid cache hit (local): {content_hash[:16]}...")
            return cached
        if r2_client is not None:
            r2_cached = await r2_client.download_beat_grid(hash_prefix)
            if r2_cached is not None:
                logger.info(f"Beat grid cache hit (R2): {content_hash[:16]}...")
                cache_manager.save_beat_grid(content_hash, r2_cached)
                return r2_cached

    loop = asyncio.get_event_loop()
    detected = await loop.run_in_executor(None, _detect_downbeats_madmom, audio_path)
    if detected is None:
        return None

    detected["content_hash"] = content_hash
    detected["hash_prefix"] = hash_prefix
    detected["schema_version"] = BEAT_GRID_SCHEMA_VERSION

    cache_manager.save_beat_grid(content_hash, detected)

    if r2_client is not None:
        try:
            await r2_client.upload_beat_grid(hash_prefix, detected)
        except Exception as e:
            logger.warning(f"Failed to upload beat_grid.json to R2: {e}")

    return detected


# Lyrical content cues that nudge chorus identification.
_CHORUS_KEYWORDS = (
    "chorus",
    "赞美",
    "敬拜",
    "主",
    "宝座",
    "圣",
    "sing",
    "hallelujah",
    "哈利路亚",
    "羔羊",
    "王",
    "恩典",
)


@dataclass
class ComponentInstance:
    """An identified song component with computed features.

    Attributes:
        component_type: 'chorus' | 'verse' | 'prechorus' | 'bridge' | ...
        occurrence_index: 1-based occurrence index.
        role: 'entry' | 'exit' | 'loop_target' | 'entry_exit' | 'none'.
        start_time: Start time in seconds.
        end_time: End time in seconds.
        bpm: Per-component tempo (optional).
        key: Per-component detected key (optional).
        groove_density: Onset/note density metric.
        backbeat_strength: Backbeat (beats 2&4) accent strength.
        energy_level: RMS/energy for the segment.
        confidence: Detection confidence (0.0–1.0).
        source: 'allin1_sections' | 'lyrics_repetition' | 'none'.
    """

    component_type: str
    occurrence_index: int
    role: str
    start_time: float
    end_time: float
    bpm: Optional[float] = None
    key: Optional[str] = None
    groove_density: Optional[float] = None
    backbeat_strength: Optional[float] = None
    energy_level: Optional[float] = None
    confidence: Optional[float] = None
    source: str = ""
    # v5: per-field confidence scores
    bpm_confidence: Optional[float] = None
    key_confidence: Optional[float] = None
    groove_confidence: Optional[float] = None
    backbeat_confidence: Optional[float] = None
    energy_confidence: Optional[float] = None
    # v5: LLM-derived theme and vocal posture
    theme: Optional[str] = None
    vocal_posture: Optional[str] = None
    theme_confidence: Optional[float] = None
    vocal_posture_confidence: Optional[float] = None
    # v5: LLM reasoning fields
    theme_reasoning: Optional[str] = None
    posture_reasoning: Optional[str] = None


@dataclass
class GlobalFeatures:
    """Pre-computed global audio features for per-component slicing."""

    y: np.ndarray
    sr: int
    duration: float
    onset_env: np.ndarray
    onset_frames: np.ndarray
    onset_times: np.ndarray
    rms: np.ndarray
    rms_times: np.ndarray
    y_harmonic: np.ndarray
    chroma: np.ndarray
    drums_y: Optional[np.ndarray]
    drums_onset_env: Optional[np.ndarray]
    drums_rms: Optional[np.ndarray]
    drums_rms_times: Optional[np.ndarray]
    vocals_y: Optional[np.ndarray]


def _precompute_global_features(
    audio_path: Path,
    hop_length: int = 512,
    stems_dir: Optional[Path] = None,
) -> GlobalFeatures:
    """Load audio once and compute all expensive global features.

    This replaces the per-component librosa.load + hpss + chroma_cqt + onset
    + rms calls with a single pass over the full audio.

    Audio is loaded with sr=None to preserve the native sample rate,
    matching the existing extract_components behavior.
    """
    y, sr = librosa.load(str(audio_path), sr=None, mono=True)
    duration = float(librosa.get_duration(y=y, sr=sr))

    onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop_length)
    onset_times = librosa.frames_to_time(
        np.arange(len(onset_env)), sr=sr, hop_length=hop_length
    )

    rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=hop_length)[0]
    rms_times = librosa.frames_to_time(
        np.arange(len(rms)), sr=sr, hop_length=hop_length
    )

    y_harmonic, _ = librosa.effects.hpss(y)

    chroma = librosa.feature.chroma_cqt(
        y=y_harmonic, sr=sr, hop_length=hop_length
    )

    drums_y = None
    drums_onset_env = None
    drums_rms = None
    drums_rms_times = None
    vocals_y = None
    if stems_dir is not None:
        drums_path = stems_dir / "drums.wav"
        if drums_path.exists():
            try:
                drums_y, _ = librosa.load(str(drums_path), sr=sr, mono=True)
                drums_onset_env = librosa.onset.onset_strength(
                    y=drums_y, sr=sr, hop_length=hop_length
                )
                drums_rms = librosa.feature.rms(
                    y=drums_y, frame_length=2048, hop_length=hop_length
                )[0]
                drums_rms_times = librosa.frames_to_time(
                    np.arange(len(drums_rms)), sr=sr, hop_length=hop_length
                )
            except Exception as e:
                logger.debug(f"Could not load drums stem: {e}")
        vocals_path = stems_dir / "vocals.wav"
        if vocals_path.exists():
            try:
                vocals_y, _ = librosa.load(str(vocals_path), sr=sr, mono=True)
            except Exception as e:
                logger.debug(f"Could not load vocals stem: {e}")

    return GlobalFeatures(
        y=y,
        sr=sr,
        duration=duration,
        onset_env=onset_env,
        onset_frames=np.arange(len(onset_env)),
        onset_times=onset_times,
        rms=rms,
        rms_times=rms_times,
        y_harmonic=y_harmonic,
        chroma=chroma,
        drums_y=drums_y,
        drums_onset_env=drums_onset_env,
        drums_rms=drums_rms,
        drums_rms_times=drums_rms_times,
        vocals_y=vocals_y,
    )


def _detect_key_from_precomputed_chroma(
    chroma: np.ndarray,
    rms: np.ndarray,
    sr: int,
    hop_length: int,
    start_time: float,
    end_time: float,
    rms_times: np.ndarray,
) -> tuple[Optional[str], Optional[float]]:
    """Detect key from pre-computed full-track chroma, sliced to [start, end].

    Returns (key, score_margin) — the two fields needed by
    compute_component_features, avoiding a second detect_key_segment_vote call.

    Note: This function replicates the window filtering logic from
    detect_key_segment_vote (analyzer.py:120) but omits the weighted voting
    logic. This is valid because compute_component_features always calls key
    detection with a single segment
    (segments=[{"start": 0.0, "end": segment_duration}]), so there is only
    one window and voting is a no-op.

    CQT is a sliding window transform, so slicing the full-track chroma to
    the component frame range is near-identical to computing chroma_cqt on
    the audio slice directly.
    """
    duration = end_time - start_time
    if duration < 8.0:
        return None, None

    start_frame = librosa.time_to_frames(start_time, sr=sr, hop_length=hop_length)
    end_frame = librosa.time_to_frames(end_time, sr=sr, hop_length=hop_length)
    if end_frame <= start_frame:
        return None, None

    window_chroma = chroma[:, start_frame:end_frame]
    window_rms = rms[start_frame:end_frame]

    if window_rms.size and float(np.mean(window_rms)) < float(np.percentile(rms, 10)):
        return None, None

    chroma_avg = np.mean(window_chroma, axis=1)
    if float(np.max(chroma_avg) - np.min(chroma_avg)) < 0.1:
        return None, None

    from .analyzer import _score_chroma

    scores = sorted(_score_chroma(chroma_avg), key=lambda x: x[2], reverse=True)
    if len(scores) > 1 and scores[0][2] - scores[1][2] < 0.03:
        return None, None

    mode, key, score = scores[0]
    margin = float(scores[0][2] - scores[1][2]) if len(scores) > 1 else None
    return key, margin


def _snap_to_beat(time_seconds: float, beats: list[float]) -> float:
    """Snap a timestamp to the nearest beat.

    Args:
        time_seconds: Timestamp in seconds.
        beats: Sorted list of beat timestamps.

    Returns:
        Nearest beat timestamp (or the input if beats is empty).
    """
    if not beats:
        return time_seconds
    beats_arr = np.asarray(beats, dtype=float)
    idx = int(np.argmin(np.abs(beats_arr - time_seconds)))
    return float(beats_arr[idx])


def _snap_to_downbeat(time_seconds: float, downbeats: list[float]) -> float:
    """Snap a timestamp to the nearest downbeat.

    Args:
        time_seconds: Timestamp in seconds.
        downbeats: Sorted list of downbeat timestamps.

    Returns:
        Nearest downbeat timestamp, or the input if downbeats is empty.
    """
    if not downbeats:
        return time_seconds
    downbeats_arr = np.asarray(downbeats, dtype=float)
    idx = int(np.argmin(np.abs(downbeats_arr - time_seconds)))
    return float(downbeats_arr[idx])


def _detect_phrases_via_onset(
    y: np.ndarray,
    sr: int,
    segment_start: float,
    segment_end: float,
    hop_length: int = 512,
) -> list[float]:
    """Detect phrase boundaries within a segment using onset strength zero-crossings.

    Computes the onset strength envelope, then finds zero-crossings of the
    derivative (peaks = phrase starts, valleys = phrase ends). Returns a list
    of absolute timestamp offsets within [segment_start, segment_end].

    Args:
        y: Full audio time series.
        sr: Sample rate.
        segment_start: Start time of the segment.
        segment_end: End time of the segment.
        hop_length: Hop length for onset strength computation.

    Returns:
        List of phrase boundary timestamps (absolute, not relative).
    """
    start_sample = int(segment_start * sr)
    end_sample = int(segment_end * sr)
    if end_sample <= start_sample:
        return []
    y_slice = y[start_sample:end_sample]
    if len(y_slice) == 0:
        return []

    try:
        onset_env = librosa.onset.onset_strength(y=y_slice, sr=sr, hop_length=hop_length)
        if len(onset_env) < 3:
            return []
        # Derivative — zero crossings indicate peaks/valleys.
        diff = np.diff(onset_env)
        # Find where derivative crosses zero (positive to negative = peak).
        crossings = np.where((diff[:-1] > 0) & (diff[1:] <= 0))[0]
        # Convert frame indices to absolute timestamps.
        times = librosa.frames_to_time(
            crossings, sr=sr, hop_length=hop_length
        )
        return [float(segment_start + t) for t in times]
    except Exception as e:
        logger.debug(f"Phrase detection failed: {e}")
        return []


def _snap_to_edit_point(
    time_seconds: float,
    beats: Optional[list[float]] = None,
    downbeats: Optional[list[float]] = None,
    y: Optional[np.ndarray] = None,
    sr: Optional[int] = None,
    segment_start: Optional[float] = None,
    segment_end: Optional[float] = None,
    hop_length: int = 512,
) -> float:
    """Snap a timestamp to the best available edit point.

    Priority order:
      1. Nearest downbeat (from madmom) — most musically meaningful
      2. Nearest phrase boundary (from onset zero-crossings)
      3. Nearest beat (from librosa) — fallback

    Args:
        time_seconds: Timestamp to snap.
        beats: Optional beat timestamps (librosa).
        downbeats: Optional downbeat timestamps (madmom).
        y: Optional audio time series (for phrase detection).
        sr: Optional sample rate (for phrase detection).
        segment_start: Optional segment start (for phrase detection context).
        segment_end: Optional segment end (for phrase detection context).
        hop_length: Hop length for onset strength computation.

    Returns:
        Snapped timestamp.
    """
    # Priority 1: Nearest downbeat.
    if downbeats:
        return _snap_to_downbeat(time_seconds, downbeats)

    # Priority 2: Nearest phrase boundary.
    if y is not None and sr is not None and segment_start is not None and segment_end is not None:
        phrase_boundaries = _detect_phrases_via_onset(
            y, sr, segment_start, segment_end, hop_length=hop_length
        )
        if phrase_boundaries:
            arr = np.asarray(phrase_boundaries, dtype=float)
            idx = int(np.argmin(np.abs(arr - time_seconds)))
            return float(arr[idx])

    # Priority 3: Nearest beat.
    if beats:
        return _snap_to_beat(time_seconds, beats)

    return time_seconds


def _assign_roles_by_energy(
    components: list[ComponentInstance],
    y: np.ndarray,
    sr: int,
    stems_dir: Optional[Path] = None,
) -> list[ComponentInstance]:
    """Reassign entry/exit roles based on energy/instrumentation cues.

    Only operates on chorus components (component_type='chorus'). Verse and
    other component roles (e.g., loop_target) are preserved unchanged.

    For each unique chorus occurrence (identified by unique start_time/end_time
    pairs — handles the v3 single-chorus two-row pattern), compute an energy
    score from:
      - RMS energy of the audio slice (full mix or vocals stem)
      - Drum stem onset density (if stems available)
      - Backbeat strength (if stems available)

    The unique chorus with the LOWEST energy score -> role='entry'
    The unique chorus with the HIGHEST energy score -> role='exit'
    Others -> role='none'

    If only 1 unique chorus, keep both 'entry' and 'exit' roles (v3 behavior).
    If energy scores are identical, fall back to positional: first=entry, last=exit.

    Args:
        components: List of ALL ComponentInstance objects (function filters
            to chorus-only internally).
        y: Full audio time series.
        sr: Sample rate.
        stems_dir: Optional path to cached Demucs stems directory.

    Returns:
        The same list with chorus roles reassigned.
    """
    chorus_components = [c for c in components if c.component_type == "chorus"]
    if len(chorus_components) < 2:
        return components

    # Deduplicate by unique (start_time, end_time) pairs.
    unique_pairs: dict[tuple[float, float], list[ComponentInstance]] = {}
    for c in chorus_components:
        key = (c.start_time, c.end_time)
        unique_pairs.setdefault(key, []).append(c)

    if len(unique_pairs) < 2:
        # Single unique chorus — keep existing entry/exit roles (v3 behavior).
        return components

    # Load stems if available.
    drums_y: Optional[np.ndarray] = None
    if stems_dir is not None:
        drums_path = stems_dir / "drums.wav"
        if drums_path.exists():
            try:
                drums_y, _ = librosa.load(str(drums_path), sr=sr, mono=True)
            except Exception as e:
                logger.debug(f"Could not load drums stem: {e}")

    # Compute energy score for each unique chorus.
    pair_scores: list[tuple[tuple[float, float], float]] = []
    for pair, comp_list in unique_pairs.items():
        start_t, end_t = pair
        start_sample = int(start_t * sr)
        end_sample = int(end_t * sr)
        if end_sample <= start_sample:
            pair_scores.append((pair, 0.0))
            continue

        # RMS energy from full mix.
        y_slice = y[start_sample:end_sample]
        if len(y_slice) == 0:
            pair_scores.append((pair, 0.0))
            continue
        try:
            rms = librosa.feature.rms(y=y_slice, frame_length=2048, hop_length=512)[0]
            rms_mean = float(np.mean(rms)) if rms.size else 0.0
        except Exception:
            rms_mean = 0.0

        if drums_y is not None:
            # Drums onset density.
            drums_slice = drums_y[start_sample:end_sample] if end_sample <= len(drums_y) else np.array([])
            if len(drums_slice) > 0:
                try:
                    drums_onset = librosa.onset.onset_strength(
                        y=drums_slice, sr=sr, hop_length=512
                    )
                    drums_density = float(np.mean(drums_onset)) if drums_onset.size else 0.0
                except Exception:
                    drums_density = 0.0
                # Backbeat strength from drums stem.
                try:
                    drums_rms = librosa.feature.rms(
                        y=drums_slice, frame_length=2048, hop_length=512
                    )[0]
                    backbeat_strength = float(np.mean(drums_rms)) if drums_rms.size else 0.0
                except Exception:
                    backbeat_strength = 0.0
            else:
                drums_density = 0.0
                backbeat_strength = 0.0

            # Normalize components (simple min-max across all pairs is done later).
            pair_scores.append((pair, (rms_mean, drums_density, backbeat_strength)))
        else:
            pair_scores.append((pair, (rms_mean,)))

    # Normalize and compute final energy scores.
    if drums_y is not None and len(pair_scores[0][1]) == 3:
        rms_vals = [s[0] for _, s in pair_scores]
        drum_vals = [s[1] for _, s in pair_scores]
        back_vals = [s[2] for _, s in pair_scores]

        def _norm(vals: list[float]) -> list[float]:
            if not vals or max(vals) == min(vals):
                return [0.5] * len(vals)
            mn, mx = min(vals), max(vals)
            return [(v - mn) / (mx - mn) for v in vals]

        rms_norm = _norm(rms_vals)
        drum_norm = _norm(drum_vals)
        back_norm = _norm(back_vals)

        final_scores = [
            0.4 * rms_norm[i] + 0.3 * drum_norm[i] + 0.3 * back_norm[i]
            for i in range(len(pair_scores))
        ]
    else:
        rms_vals = [s[0] if isinstance(s, tuple) else s for _, s in pair_scores]
        if max(rms_vals) == min(rms_vals):
            final_scores = [0.5] * len(rms_vals)
        else:
            mn, mx = min(rms_vals), max(rms_vals)
            final_scores = [(v - mn) / (mx - mn) for v in rms_vals]

    # Find lowest and highest energy pairs.
    scored = list(zip(unique_pairs.keys(), final_scores))
    # Check for identical scores — fall back to positional.
    if len(set(final_scores)) == 1:
        # All identical — positional: first=entry, last=exit.
        sorted_pairs = list(unique_pairs.keys())
    else:
        sorted_pairs = [p for p, _ in sorted(scored, key=lambda x: x[1])]

    entry_pair = sorted_pairs[0]
    exit_pair = sorted_pairs[-1]

    # Reassign roles.
    for c in chorus_components:
        key = (c.start_time, c.end_time)
        if key == entry_pair:
            c.role = "entry"
        elif key == exit_pair:
            c.role = "exit"
        else:
            c.role = "none"

    return components


def _normalize_line(text: str) -> str:
    """Normalize a lyric line for repetition comparison.

    Strips, lowercases, removes punctuation and whitespace.
    """
    text = text.strip().lower()
    # Remove punctuation (both ASCII and common CJK punctuation).
    translator = str.maketrans("", "", string.punctuation)
    text = text.translate(translator)
    # Remove common CJK punctuation.
    text = re.sub(r"[，。！？、；：""''（）【】《》…—]", "", text)
    text = re.sub(r"\s+", "", text)
    return text


def identify_from_allin1_sections(
    sections: list[dict],
    snap_to_downbeat: bool = False,
    downbeats: Optional[list[float]] = None,
) -> list[ComponentInstance]:
    """Identify chorus/verse components from allin1 section labels.

    allin1 labels: 'intro', 'verse', 'chorus', 'bridge', 'outro', 'instrumental'.

    Rules:
    - All sections labeled 'chorus' -> list with occurrence_index 1..N.
    - occurrence_index=1 -> role='entry'
    - occurrence_index=N (last) -> role='exit'
    - v3: If only 1 chorus -> persist as TWO ComponentInstance rows
      (occurrence_index=1, role='entry' and occurrence_index=1, role='exit')
      with identical start_time/end_time.
    - The verse section immediately preceding the first chorus -> role='loop_target',
      occurrence_index=1.
    - If no verse before first chorus -> skip loop_target.

    v5: When snap_to_downbeat=True and downbeats are provided, start_time/end_time
    are snapped to nearest downbeat instead of nearest beat.

    Args:
        sections: List of section dicts with 'label', 'start', 'end' keys.
        snap_to_downbeat: If True, snap to downbeats (requires downbeats param).
        downbeats: Optional downbeat timestamps for snapping.

    Returns:
        List of ComponentInstance objects.
    """
    if not sections:
        return []

    # Collect chorus sections in order.
    chorus_sections = [s for s in sections if str(s.get("label", "")).lower() == "chorus"]
    if not chorus_sections:
        return []

    def _snap(t: float) -> float:
        if snap_to_downbeat and downbeats:
            return _snap_to_downbeat(t, downbeats)
        return t

    components: list[ComponentInstance] = []
    n_choruses = len(chorus_sections)

    for i, chorus in enumerate(chorus_sections):
        occurrence = i + 1
        start = _snap(float(chorus.get("start", 0.0)))
        end = _snap(float(chorus.get("end", start)))

        if n_choruses == 1:
            # v3: single chorus -> two rows (entry + exit), same occurrence_index=1.
            components.append(
                ComponentInstance(
                    component_type="chorus",
                    occurrence_index=1,
                    role="entry",
                    start_time=start,
                    end_time=end,
                    confidence=0.9,
                    source="allin1_sections",
                )
            )
            components.append(
                ComponentInstance(
                    component_type="chorus",
                    occurrence_index=1,
                    role="exit",
                    start_time=start,
                    end_time=end,
                    confidence=0.9,
                    source="allin1_sections",
                )
            )
        else:
            role = "entry" if i == 0 else ("exit" if i == n_choruses - 1 else "none")
            components.append(
                ComponentInstance(
                    component_type="chorus",
                    occurrence_index=occurrence,
                    role=role,
                    start_time=start,
                    end_time=end,
                    confidence=0.9,
                    source="allin1_sections",
                )
            )

    # loop_target: the verse section immediately preceding the first chorus.
    first_chorus_start = float(chorus_sections[0].get("start", 0.0))
    verse_before_chorus: Optional[dict] = None
    for section in sections:
        label = str(section.get("label", "")).lower()
        section_start = float(section.get("start", 0.0))
        if section_start >= first_chorus_start:
            break
        if label == "verse":
            verse_before_chorus = section  # keep the last verse before chorus

    if verse_before_chorus is not None:
        verse_start = _snap(float(verse_before_chorus.get("start", 0.0)))
        verse_end = _snap(float(verse_before_chorus.get("end", first_chorus_start)))
        components.append(
            ComponentInstance(
                component_type="verse",
                occurrence_index=1,
                role="loop_target",
                start_time=verse_start,
                end_time=verse_end,
                confidence=0.9,
                source="allin1_sections",
            )
        )

    return components


def identify_from_lyrics_repetition(
    lrc_content: str,
    beats: Optional[list[float]] = None,
    downbeats: Optional[list[float]] = None,
    song_total_duration: Optional[float] = None,
    snap_to_downbeat: bool = False,
) -> list[ComponentInstance]:
    """Identify chorus via repeated-line-group clustering on LRC lines.

    v3 — Multi-cue weighting (replaces pure repeat_count scoring, which
    misidentified repeated verses as choruses).

    v5: When snap_to_downbeat=True and downbeats are provided, start_time/end_time
    are snapped to nearest downbeat instead of nearest beat.

    Algorithm:
      1. Parse LRC via the existing workers/lrc_parser.py.
      2. Normalize each line.
      3. For window sizes w = 2..min(12, N//2), slide a window of w consecutive
         lines and group window-start indices by exact-signature match.
         Also try fuzzy match (rapidfuzz.fuzz.ratio > 85) to catch minor
         lyric variations.
      4. Multi-cue scoring: repetition_score x position_weight x
         length_weight x content_weight.
      5. Best candidate = chorus. Its occurrence positions give N chorus
         instances (entry/exit roles). Single-chorus -> two rows.
      6. Verse (loop_target): lines immediately before the first chorus.
      7. Snap start_time/end_time to nearest beat if beats provided.

    Args:
        lrc_content: Raw LRC file content.
        beats: Optional list of beat timestamps for snapping.
        downbeats: Optional list of downbeat timestamps for snapping (preferred).
        song_total_duration: Optional total song duration for position weighting.
        snap_to_downbeat: If True, snap to downbeats (requires downbeats param).

    Returns:
        List of ComponentInstance objects.
    """
    try:
        lrc_file = parse_lrc(lrc_content)
    except (ValueError, Exception):
        return []

    lines = [ln for ln in lrc_file.lines if ln.text and ln.text.strip()]
    if len(lines) < 4:
        return []

    n = len(lines)
    normalized = [_normalize_line(ln.text) for ln in lines]
    max_window = min(12, n // 2)

    # Try rapidfuzz for fuzzy matching; fall back to exact only.
    try:
        from rapidfuzz import fuzz as rf_fuzz
    except ImportError:
        rf_fuzz = None

    # Build candidates: dict[signature] -> list of window start indices.
    # signature = tuple(normalized[i:i+w])
    candidates: dict[tuple[str, ...], list[int]] = {}
    for w in range(2, max_window + 1):
        for i in range(n - w + 1):
            sig = tuple(normalized[i : i + w])
            if not all(sig):
                continue
            candidates.setdefault(sig, []).append(i)

    # Merge near-duplicate signatures via fuzzy matching.
    # Group signatures that are fuzzy-similar (ratio > 85 on joined text).
    sig_list = list(candidates.keys())
    merged: dict[int, set[int]] = {i: {i} for i in range(len(sig_list))}
    if rf_fuzz is not None and len(sig_list) > 1:
        for i in range(len(sig_list)):
            for j in range(i + 1, len(sig_list)):
                if len(sig_list[i]) != len(sig_list[j]):
                    continue
                # Only merge if they don't overlap in window starts.
                starts_i = set(candidates[sig_list[i]])
                starts_j = set(candidates[sig_list[j]])
                if starts_i & starts_j:
                    continue
                joined_i = " ".join(sig_list[i])
                joined_j = " ".join(sig_list[j])
                if rf_fuzz.ratio(joined_i, joined_j) > 85:
                    # Union the groups.
                    root_i = next(k for k, v in merged.items() if i in v)
                    root_j = next(k for k, v in merged.items() if j in v)
                    if root_i != root_j:
                        merged[root_i] = merged[root_i] | merged[root_j]
                        merged[root_j] = set()

    # Collect merged candidate groups with >= 2 occurrences.
    scored_candidates: list[dict] = []
    for root, members in merged.items():
        if not members:
            continue
        # Combine all window starts from member signatures.
        all_starts: list[int] = []
        for member_idx in members:
            all_starts.extend(candidates[sig_list[member_idx]])
        all_starts = sorted(set(all_starts))
        if len(all_starts) < 2:
            continue

        w = len(sig_list[root])
        repeat_count = len(all_starts)
        occurrence_times = [lines[idx].time_seconds for idx in all_starts]
        joined_text = " ".join(sig_list[root])

        # v3 multi-cue scoring.
        repetition_score = min(repeat_count, 4) * w
        if song_total_duration and song_total_duration > 0:
            position_weight = 1.0 if occurrence_times[0] > 0.1 * song_total_duration else 0.4
        else:
            position_weight = 1.0 if occurrence_times[0] > 10.0 else 0.4
        length_weight = 1.0 if 4 <= w <= 8 else 0.6
        content_weight = (
            1.4
            if any(kw in joined_text.lower() for kw in _CHORUS_KEYWORDS)
            else 1.0
        )
        final_score = (
            repetition_score * position_weight * length_weight * content_weight
        )

        scored_candidates.append(
            {
                "window_starts": all_starts,
                "window_size": w,
                "repeat_count": repeat_count,
                "occurrence_times": occurrence_times,
                "joined_text": joined_text,
                "repetition_score": repetition_score,
                "final_score": final_score,
            }
        )

    if not scored_candidates:
        return []

    # Best candidate = highest final_score; tiebreak: highest repetition_score,
    # then earliest occurrence.
    scored_candidates.sort(
        key=lambda c: (-c["final_score"], -c["repetition_score"], c["occurrence_times"][0])
    )
    best = scored_candidates[0]

    # Build chorus components from the best candidate.
    window_starts = best["window_starts"]
    w = best["window_size"]
    occurrence_times = best["occurrence_times"]
    n_occurrences = len(window_starts)

    components: list[ComponentInstance] = []

    for i, start_idx in enumerate(window_starts):
        occurrence = i + 1
        start_time = lines[start_idx].time_seconds
        # end_time: next line after the block, or last line + estimated duration.
        end_idx = start_idx + w
        if end_idx < n:
            end_time = lines[end_idx].time_seconds
        else:
            # Estimate: average line duration in the block.
            block_durations = []
            for k in range(start_idx, min(start_idx + w, n - 1)):
                block_durations.append(lines[k + 1].time_seconds - lines[k].time_seconds)
            avg_dur = sum(block_durations) / len(block_durations) if block_durations else 4.0
            end_time = lines[min(start_idx + w - 1, n - 1)].time_seconds + avg_dur

        # Snap to beats if provided.
        if snap_to_downbeat and downbeats:
            start_time = _snap_to_downbeat(start_time, downbeats)
            end_time = _snap_to_downbeat(end_time, downbeats)
        elif beats:
            start_time = _snap_to_beat(start_time, beats)
            end_time = _snap_to_beat(end_time, beats)

        if n_occurrences == 1:
            # v3: single chorus → two rows (entry + exit).
            components.append(
                ComponentInstance(
                    component_type="chorus",
                    occurrence_index=1,
                    role="entry",
                    start_time=start_time,
                    end_time=end_time,
                    confidence=0.7,
                    source="lyrics_repetition",
                )
            )
            components.append(
                ComponentInstance(
                    component_type="chorus",
                    occurrence_index=1,
                    role="exit",
                    start_time=start_time,
                    end_time=end_time,
                    confidence=0.7,
                    source="lyrics_repetition",
                )
            )
        else:
            role = "entry" if i == 0 else ("exit" if i == n_occurrences - 1 else "none")
            components.append(
                ComponentInstance(
                    component_type="chorus",
                    occurrence_index=occurrence,
                    role=role,
                    start_time=start_time,
                    end_time=end_time,
                    confidence=0.7,
                    source="lyrics_repetition",
                )
            )

    # Verse (loop_target): lines immediately before the first chorus occurrence.
    first_chorus_start_idx = window_starts[0]
    if first_chorus_start_idx > 0:
        # Walk backward until a time-gap > 3s or song start.
        verse_end_idx = first_chorus_start_idx
        verse_start_idx = first_chorus_start_idx
        for k in range(first_chorus_start_idx - 1, -1, -1):
            gap = lines[k + 1].time_seconds - lines[k].time_seconds
            if gap > 3.0 and k != first_chorus_start_idx - 1:
                break
            verse_start_idx = k
        if verse_start_idx < verse_end_idx:
            verse_start_time = lines[verse_start_idx].time_seconds
            verse_end_time = lines[verse_end_idx].time_seconds
            if snap_to_downbeat and downbeats:
                verse_start_time = _snap_to_downbeat(verse_start_time, downbeats)
                verse_end_time = _snap_to_downbeat(verse_end_time, downbeats)
            elif beats:
                verse_start_time = _snap_to_beat(verse_start_time, beats)
                verse_end_time = _snap_to_beat(verse_end_time, beats)
            components.append(
                ComponentInstance(
                    component_type="verse",
                    occurrence_index=1,
                    role="loop_target",
                    start_time=verse_start_time,
                    end_time=verse_end_time,
                    confidence=0.7,
                    source="lyrics_repetition",
                )
            )

    return components


def compute_component_features(
    gf: GlobalFeatures,
    component: ComponentInstance,
    beats: Optional[list[float]] = None,
    downbeats: Optional[list[float]] = None,
    hop_length: int = 512,
) -> ComponentInstance:
    """Compute per-component BPM, key, groove_density, backbeat_strength, energy_level.

    Uses pre-computed global features (GlobalFeatures) and slices them per
    component, eliminating redundant librosa.load + hpss + chroma_cqt +
    onset_strength + rms calls.

    v5 changes:
      - Uses cached Demucs stems (drums, vocals, bass, other) when available.
      - Computes per-field confidence scores.
      - Computes composite `confidence` as weighted mean of per-field scores.

    Mutates and returns the component in place.

    Args:
        gf: Pre-computed global features (full-track audio + librosa arrays).
        component: ComponentInstance to compute features for.
        beats: Optional global beat timestamps.
        downbeats: Optional global downbeat timestamps.
        hop_length: Hop length for onset strength computation.

    Returns:
        The same ComponentInstance with features and per-field confidences populated.
    """
    sr = gf.sr
    start_sample = int(component.start_time * sr)
    end_sample = int(component.end_time * sr)
    if end_sample <= start_sample:
        end_sample = min(start_sample + 1, len(gf.y))
    y_slice = gf.y[start_sample:end_sample]
    if len(y_slice) == 0:
        return component

    segment_duration = component.end_time - component.start_time
    has_stems = gf.drums_y is not None

    # Frame range for slicing global features.
    start_frame = librosa.time_to_frames(
        component.start_time, sr=sr, hop_length=hop_length
    )
    end_frame = librosa.time_to_frames(
        component.end_time, sr=sr, hop_length=hop_length
    )
    if end_frame <= start_frame:
        end_frame = start_frame + 1

    # Slice global onset_env and rms for this component (cheap numpy views).
    onset_env_slice = gf.onset_env[start_frame:end_frame]
    rms_slice = gf.rms[start_frame:end_frame]

    # BPM: re-estimate from onset strength slice.
    try:
        if segment_duration >= 8.0:
            tempo = librosa.beat.tempo(
                onset_envelope=onset_env_slice,
                sr=sr,
                hop_length=hop_length,
                start_bpm=80.0,
            )
            if hasattr(tempo, "__iter__"):
                tempo = float(tempo[0])
            component.bpm = float(tempo)
        else:
            if beats:
                seg_beats = [
                    b for b in beats if component.start_time <= b <= component.end_time
                ]
                if len(seg_beats) >= 2:
                    intervals = np.diff(seg_beats)
                    if len(intervals) > 0:
                        component.bpm = float(60.0 / np.median(intervals))
    except Exception as e:
        logger.debug(f"BPM estimation failed for component: {e}")

    # Key: detect from pre-computed chroma (single call for key + margin).
    # Eliminates the duplicate detect_key_segment_vote call.
    margin: Optional[float] = None
    try:
        key, margin = _detect_key_from_precomputed_chroma(
            gf.chroma,
            gf.rms,
            gf.sr,
            hop_length,
            component.start_time,
            component.end_time,
            gf.rms_times,
        )
        if key is None:
            from .analyzer import detect_key_fulltrack

            ft_result = detect_key_fulltrack(gf.y_harmonic, gf.sr)
            key = ft_result.key
            margin = ft_result.score_margin
        component.key = key
    except Exception as e:
        logger.debug(f"Key detection failed for component: {e}")

    # groove_density: from drums stem onset (if stems), else full mix onset.
    try:
        if gf.drums_onset_env is not None:
            drums_onset_slice = gf.drums_onset_env[start_frame:end_frame]
            onset_for_groove = (
                drums_onset_slice if drums_onset_slice.size else onset_env_slice
            )
        else:
            onset_for_groove = onset_env_slice
        if segment_duration > 0 and onset_for_groove.size:
            component.groove_density = float(np.mean(onset_for_groove) / segment_duration)
    except Exception as e:
        logger.debug(f"Groove density failed for component: {e}")

    # backbeat_strength: mean RMS at beat positions 2&4 vs 1&3.
    try:
        if beats:
            seg_beats = [
                b for b in beats if component.start_time <= b <= component.end_time
            ]
            if len(seg_beats) >= 4:
                if gf.drums_rms is not None and gf.drums_rms_times is not None:
                    source_rms = gf.drums_rms
                    source_rms_times = gf.drums_rms_times
                else:
                    source_rms = gf.rms
                    source_rms_times = gf.rms_times

                def _rms_at(t: float) -> float:
                    idx = int(np.argmin(np.abs(source_rms_times - t)))
                    return float(source_rms[idx]) if idx < len(source_rms) else 0.0

                backbeat_vals = []
                frontbeat_vals = []
                for group_start in range(0, len(seg_beats) - 3, 4):
                    group = seg_beats[group_start : group_start + 4]
                    if len(group) >= 4:
                        frontbeat_vals.extend([_rms_at(group[0]), _rms_at(group[2])])
                        backbeat_vals.extend([_rms_at(group[1]), _rms_at(group[3])])
                if backbeat_vals and frontbeat_vals:
                    front_mean = np.mean(frontbeat_vals)
                    if front_mean > 0:
                        component.backbeat_strength = float(
                            np.mean(backbeat_vals) / front_mean
                        )
    except Exception as e:
        logger.debug(f"Backbeat strength failed for component: {e}")

    # energy_level: mean RMS in dB.
    try:
        if gf.vocals_y is not None:
            vocals_slice = (
                gf.vocals_y[start_sample:end_sample]
                if end_sample <= len(gf.vocals_y)
                else np.array([])
            )
            if len(vocals_slice) > 0:
                rms_vocals = librosa.feature.rms(
                    y=vocals_slice, frame_length=2048, hop_length=hop_length
                )[0]
            else:
                rms_vocals = np.array([])
            if rms_slice.size and rms_vocals.size:
                mean_rms = float(0.7 * np.mean(rms_slice) + 0.3 * np.mean(rms_vocals))
            elif rms_slice.size:
                mean_rms = float(np.mean(rms_slice))
            else:
                mean_rms = 0.0
        else:
            mean_rms = float(np.mean(rms_slice)) if rms_slice.size else 0.0
        component.energy_level = float(20 * np.log10(mean_rms + 1e-10))
    except Exception as e:
        logger.debug(f"Energy level failed for component: {e}")

    # v5: Per-field confidence scores.
    # bpm_confidence: based on segment duration.
    if segment_duration >= 16.0:
        component.bpm_confidence = 0.9
    elif segment_duration >= 8.0:
        component.bpm_confidence = 0.7
    else:
        component.bpm_confidence = 0.4

    # key_confidence: from margin, sigmoid-mapped.
    if margin is not None:
        component.key_confidence = float(1.0 / (1.0 + np.exp(-2.0 * margin)))
    else:
        component.key_confidence = 0.7

    # groove/backbeat/energy confidence: higher if stems available.
    if has_stems:
        component.groove_confidence = 0.9
        component.backbeat_confidence = 0.9
        component.energy_confidence = 0.9
    else:
        component.groove_confidence = 0.7
        component.backbeat_confidence = 0.7
        component.energy_confidence = 0.7

    # Composite confidence: weighted mean of per-field scores.
    per_field = [
        component.bpm_confidence or 0.7,
        component.key_confidence or 0.7,
        component.groove_confidence or 0.7,
        component.backbeat_confidence or 0.7,
        component.energy_confidence or 0.7,
    ]
    component.confidence = float(np.mean(per_field))

    return component


async def extract_components(
    audio_path: Path,
    content_hash: str,
    cache_manager: CacheManager,
    r2_client: Optional[R2Client],
    sections: Optional[list[dict]] = None,
    lrc_content: Optional[str] = None,
    beats: Optional[list[float]] = None,
    downbeats: Optional[list[float]] = None,
    force: bool = False,
    use_stems: bool = False,
    snap_to_downbeat: bool = False,
    energy_aware_roles: bool = False,
) -> tuple[list[ComponentInstance], str]:
    """Extract song components using hybrid strategy (v5 enhancements).

    v5 additions:
      - `use_stems`: If True, load cached Demucs stems for per-component
        feature extraction.
      - `snap_to_downbeat`: If True, use downbeats for edit-point snapping.
      - `energy_aware_roles`: If True, run _assign_roles_by_energy after
        identification to reassign entry/exit roles based on energy cues.

    Note: `analyze_audio_fast()` does NOT return beats/downbeats and is never
    called from this function. Downbeats are expected from the caller (queue.py
    populates them via the beat-grid cache when snap_to_downbeat is set); the
    tier-2 lyrics path additionally reads the beat-grid cache directly as
    defense-in-depth.

    Returns (components, source) where source is one of:
    'allin1_sections', 'lyrics_repetition', 'none'.
    """
    hash_prefix = content_hash[:12]

    # 1. Cache check (defense in depth).
    if not force:
        cached = cache_manager.get_component_result(content_hash)
        if cached is not None:
            logger.info(f"Component cache hit (local): {content_hash[:16]}...")
            return _deserialize_components(cached), cached.get("component_source", "none")

        if r2_client is not None:
            r2_cached = await r2_client.download_component_result(hash_prefix)
            if r2_cached is not None:
                logger.info(f"Component cache hit (R2): {content_hash[:16]}...")
                # Save to local cache for next time.
                cache_manager.save_component_result(content_hash, r2_cached)
                return _deserialize_components(r2_cached), r2_cached.get(
                    "component_source", "none"
                )

    # 2. Pre-compute global features (single load + all expensive features).
    stems_dir: Optional[Path] = None
    if use_stems:
        stems_dir = cache_manager.get_stems_dir(content_hash)
        if stems_dir is None:
            logger.info("Stems not cached; using full-mix features")

    gf: Optional[GlobalFeatures] = None
    try:
        loop = asyncio.get_event_loop()
        precompute_start = time.time()
        gf = await loop.run_in_executor(
            None, _precompute_global_features, audio_path, 512, stems_dir
        )
        logger.info(
            f"Global feature precomputation completed in "
            f"{time.time() - precompute_start:.2f}s"
        )
    except Exception as e:
        logger.warning(f"Global feature precomputation failed: {e}")

    # 3. Identification.
    components: list[ComponentInstance] = []
    source = "none"

    if sections:
        components = identify_from_allin1_sections(
            sections, snap_to_downbeat=snap_to_downbeat, downbeats=downbeats
        )
        if components:
            source = "allin1_sections"

    if not components and lrc_content:
        # v6: prefer the beat-grid cache. The old inline analyze_audio_fast call
        # never returned beats/downbeats (analyzer.py:545–553); dropping it only
        # forfeits its fast-cache warm-up side effect (accepted — see Risks).
        if not beats and not downbeats:
            cached_grid = cache_manager.get_beat_grid(content_hash)
            if cached_grid is not None:
                downbeats = cached_grid.get("downbeats")
                # The full grid (cached_grid["beats"]) is not consumed by
                # identify_from_lyrics_repetition, which takes flat timestamps.

        # Use pre-computed duration (eliminates redundant librosa.load).
        song_total_duration = gf.duration if gf is not None else None

        identify_start = time.time()
        components = identify_from_lyrics_repetition(
            lrc_content,
            beats=beats,
            downbeats=downbeats,
            song_total_duration=song_total_duration,
            snap_to_downbeat=snap_to_downbeat,
        )
        logger.info(
            f"Component identification completed in "
            f"{time.time() - identify_start:.2f}s ({len(components)} components)"
        )
        if components:
            source = "lyrics_repetition"
            # v6: lower confidence when no downbeats are available (madmom
            # failure or cache miss). The old inline_fast_ran flag is gone.
            if not downbeats:
                for c in components:
                    c.confidence = 0.5

    if not components:
        return ([], "none")

    # 4. Energy-aware role assignment + per-component feature computation.
    if gf is not None:
        try:
            if energy_aware_roles:
                components = _assign_roles_by_energy(
                    components, gf.y, gf.sr, stems_dir=stems_dir
                )

            features_start = time.time()
            last_heartbeat = time.time()
            for i, component in enumerate(components, 1):
                compute_component_features(
                    gf, component, beats=beats, downbeats=downbeats
                )
                now = time.time()
                if (
                    now - last_heartbeat
                    >= settings.SOW_STEP_HEARTBEAT_INTERVAL_SECONDS
                ):
                    elapsed = now - features_start
                    logger.info(
                        f"Feature computation heartbeat: {i}/{len(components)} "
                        f"components done ({elapsed:.1f}s elapsed)"
                    )
                    last_heartbeat = now
            logger.info(
                f"Per-component feature computation completed in "
                f"{time.time() - features_start:.2f}s ({len(components)} components)"
            )
        except Exception as e:
            logger.warning(f"Feature computation failed: {e}")

    # 5. Persist to local cache and R2.
    payload = _serialize_components(components, content_hash, hash_prefix, source)
    cache_manager.save_component_result(content_hash, payload)

    if r2_client is not None:
        try:
            await r2_client.upload_component_result(hash_prefix, payload)
        except Exception as e:
            logger.warning(f"Failed to upload components.json to R2: {e}")

    return (components, source)


def _serialize_components(
    components: list[ComponentInstance],
    content_hash: str,
    hash_prefix: str,
    source: str,
) -> dict:
    """Serialize components to the v5 components.json payload."""
    return {
        "schema_version": COMPONENT_SCHEMA_VERSION,
        "content_hash": content_hash,
        "hash_prefix": hash_prefix,
        "component_source": source,
        "components": [
            {
                "component_type": c.component_type,
                "occurrence_index": c.occurrence_index,
                "role": c.role,
                "start_time": c.start_time,
                "end_time": c.end_time,
                "bpm": c.bpm,
                "key": c.key,
                "groove_density": c.groove_density,
                "backbeat_strength": c.backbeat_strength,
                "energy_level": c.energy_level,
                "confidence": c.confidence,
                # v5: per-field confidence
                "bpm_confidence": c.bpm_confidence,
                "key_confidence": c.key_confidence,
                "groove_confidence": c.groove_confidence,
                "backbeat_confidence": c.backbeat_confidence,
                "energy_confidence": c.energy_confidence,
                # v5: LLM theme/posture
                "theme": c.theme,
                "vocal_posture": c.vocal_posture,
                "theme_confidence": c.theme_confidence,
                "vocal_posture_confidence": c.vocal_posture_confidence,
                # v5: reasoning
                "theme_reasoning": c.theme_reasoning,
                "posture_reasoning": c.posture_reasoning,
            }
            for c in components
        ],
    }


def _deserialize_components(payload: dict) -> list[ComponentInstance]:
    """Deserialize components from a cached components.json payload."""
    components = []
    for c in payload.get("components", []):
        components.append(
            ComponentInstance(
                component_type=c.get("component_type", ""),
                occurrence_index=c.get("occurrence_index", 1),
                role=c.get("role", "none"),
                start_time=c.get("start_time", 0.0),
                end_time=c.get("end_time", 0.0),
                bpm=c.get("bpm"),
                key=c.get("key"),
                groove_density=c.get("groove_density"),
                backbeat_strength=c.get("backbeat_strength"),
                energy_level=c.get("energy_level"),
                confidence=c.get("confidence"),
                # v5: per-field confidence
                bpm_confidence=c.get("bpm_confidence"),
                key_confidence=c.get("key_confidence"),
                groove_confidence=c.get("groove_confidence"),
                backbeat_confidence=c.get("backbeat_confidence"),
                energy_confidence=c.get("energy_confidence"),
                # v5: LLM theme/posture
                theme=c.get("theme"),
                vocal_posture=c.get("vocal_posture"),
                theme_confidence=c.get("theme_confidence"),
                vocal_posture_confidence=c.get("vocal_posture_confidence"),
                # v5: reasoning
                theme_reasoning=c.get("theme_reasoning"),
                posture_reasoning=c.get("posture_reasoning"),
                source=payload.get("component_source", ""),
            )
        )
    return components
