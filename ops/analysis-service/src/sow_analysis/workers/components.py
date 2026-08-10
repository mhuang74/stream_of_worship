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
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import librosa
import numpy as np

from ..storage.cache import COMPONENT_SCHEMA_VERSION, CacheManager
from ..storage.r2 import R2Client
from .lrc_parser import parse_lrc

logger = logging.getLogger(__name__)

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
) -> list[ComponentInstance]:
    """Identify chorus/verse components from allin1 section labels.

    allin1 labels: 'intro', 'verse', 'chorus', 'bridge', 'outro', 'instrumental'.

    Rules:
    - All sections labeled 'chorus' → list with occurrence_index 1..N.
    - occurrence_index=1 → role='entry'
    - occurrence_index=N (last) → role='exit'
    - v3: If only 1 chorus → persist as TWO ComponentInstance rows
      (occurrence_index=1, role='entry' and occurrence_index=1, role='exit')
      with identical start_time/end_time.
    - The verse section immediately preceding the first chorus → role='loop_target',
      occurrence_index=1.
    - If no verse before first chorus → skip loop_target.

    Args:
        sections: List of section dicts with 'label', 'start', 'end' keys.

    Returns:
        List of ComponentInstance objects.
    """
    if not sections:
        return []

    # Collect chorus sections in order.
    chorus_sections = [s for s in sections if str(s.get("label", "")).lower() == "chorus"]
    if not chorus_sections:
        return []

    components: list[ComponentInstance] = []
    n_choruses = len(chorus_sections)

    for i, chorus in enumerate(chorus_sections):
        occurrence = i + 1
        start = float(chorus.get("start", 0.0))
        end = float(chorus.get("end", start))

        if n_choruses == 1:
            # v3: single chorus → two rows (entry + exit), same occurrence_index=1.
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
        components.append(
            ComponentInstance(
                component_type="verse",
                occurrence_index=1,
                role="loop_target",
                start_time=float(verse_before_chorus.get("start", 0.0)),
                end_time=float(verse_before_chorus.get("end", first_chorus_start)),
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
) -> list[ComponentInstance]:
    """Identify chorus via repeated-line-group clustering on LRC lines.

    v3 — Multi-cue weighting (replaces pure repeat_count scoring, which
    misidentified repeated verses as choruses).

    Algorithm:
      1. Parse LRC via the existing workers/lrc_parser.py.
      2. Normalize each line.
      3. For window sizes w = 2..min(12, N//2), slide a window of w consecutive
         lines and group window-start indices by exact-signature match.
         Also try fuzzy match (rapidfuzz.fuzz.ratio > 85) to catch minor
         lyric variations.
      4. Multi-cue scoring: repetition_score × position_weight ×
         length_weight × content_weight.
      5. Best candidate = chorus. Its occurrence positions give N chorus
         instances (entry/exit roles). Single-chorus → two rows.
      6. Verse (loop_target): lines immediately before the first chorus.
      7. Snap start_time/end_time to nearest beat if beats provided.

    Args:
        lrc_content: Raw LRC file content.
        beats: Optional list of beat timestamps for snapping.
        downbeats: Optional list of downbeat timestamps (unused for now).
        song_total_duration: Optional total song duration for position weighting.

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
        if beats:
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
            if beats:
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
    y: np.ndarray,
    sr: int,
    component: ComponentInstance,
    beats: Optional[list[float]] = None,
    downbeats: Optional[list[float]] = None,
) -> ComponentInstance:
    """Compute per-component BPM, key, groove_density, backbeat_strength, energy_level.

    Mutates and returns the component in place.

    Args:
        y: Full audio time series.
        sr: Sample rate.
        component: ComponentInstance to compute features for.
        beats: Optional global beat timestamps.
        downbeats: Optional global downbeat timestamps.

    Returns:
        The same ComponentInstance with features populated.
    """
    start_sample = int(component.start_time * sr)
    end_sample = int(component.end_time * sr)
    if end_sample <= start_sample:
        end_sample = min(start_sample + 1, len(y))
    y_slice = y[start_sample:end_sample]
    if len(y_slice) == 0:
        return component

    hop_length = 512

    # BPM: re-estimate from onset strength on the slice.
    try:
        segment_duration = component.end_time - component.start_time
        if segment_duration >= 8.0:
            onset_env = librosa.onset.onset_strength(y=y_slice, sr=sr, hop_length=hop_length)
            tempo = librosa.beat.tempo(
                onset_envelope=onset_env, sr=sr, hop_length=hop_length, start_bpm=80.0
            )
            if hasattr(tempo, "__iter__"):
                tempo = float(tempo[0])
            component.bpm = float(tempo)
        else:
            # Too short — use global beats if available.
            if beats:
                seg_beats = [b for b in beats if component.start_time <= b <= component.end_time]
                if len(seg_beats) >= 2:
                    intervals = np.diff(seg_beats)
                    if len(intervals) > 0:
                        component.bpm = float(60.0 / np.median(intervals))
    except Exception as e:
        logger.debug(f"BPM estimation failed for component: {e}")

    # Key: use detect_key_segment_vote with the single segment as window.
    try:
        from .analyzer import detect_key_segment_vote

        key_result = detect_key_segment_vote(
            y_slice, sr, segments=[{"start": 0.0, "end": segment_duration}]
        )
        component.key = key_result.key
    except Exception as e:
        logger.debug(f"Key detection failed for component: {e}")

    # groove_density: mean onset strength (onsets/sec).
    try:
        onset_env = librosa.onset.onset_strength(y=y_slice, sr=sr, hop_length=hop_length)
        segment_duration = component.end_time - component.start_time
        if segment_duration > 0:
            component.groove_density = float(np.mean(onset_env) / segment_duration)
    except Exception as e:
        logger.debug(f"Groove density failed for component: {e}")

    # backbeat_strength: mean RMS at beat positions 2&4 vs 1&3.
    try:
        if beats:
            seg_beats = [b for b in beats if component.start_time <= b <= component.end_time]
            if len(seg_beats) >= 4:
                rms = librosa.feature.rms(y=y_slice, frame_length=2048, hop_length=hop_length)[0]
                rms_times = librosa.frames_to_time(
                    np.arange(len(rms)), sr=sr, hop_length=hop_length
                )

                def _rms_at(t: float) -> float:
                    idx = int(np.argmin(np.abs(rms_times - (t - component.start_time))))
                    return float(rms[idx]) if idx < len(rms) else 0.0

                # Group beats into 4-beat groups; compare beats 2&4 vs 1&3.
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
        rms = librosa.feature.rms(y=y_slice, frame_length=2048, hop_length=hop_length)[0]
        if rms.size:
            mean_rms = float(np.mean(rms))
            component.energy_level = float(20 * np.log10(mean_rms + 1e-10))
    except Exception as e:
        logger.debug(f"Energy level failed for component: {e}")

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
) -> tuple[list[ComponentInstance], str]:
    """Extract song components using hybrid strategy.

    v3 — Cache-aware (defense in depth): the worker re-checks BOTH local cache
    and R2 for ``{hash_prefix}_components.json`` and ``{hash_prefix}/components.json``
    respectively, even when the admin CLI already short-circuited.

    Returns (components, source) where source is one of:
    'allin1_sections', 'lyrics_repetition', 'none'.

    Strategy order:
      1. UNLESS force=True: check local cache → R2 cache. If a cached
         components.json exists AND its schema_version matches
         COMPONENT_SCHEMA_VERSION, return the cached payload.
      2. If sections provided & non-empty → identify_from_allin1_sections()
      3. Elif lrc_content provided & non-empty:
         a. If beats/downbeats NOT provided → run analyze_audio_fast() inline
            to obtain beats/downbeats/tempo_bpm. Save result via
            CacheManager.save_fast_analyze_result for reuse.
         b. identify_from_lyrics_repetition(lrc_content, beats, downbeats, ...)
      4. If all fail → ([], 'none').

    After identification, ALWAYS load audio and compute per-component features
    via compute_component_features().

    Finally, persist the result to local cache AND R2 with schema_version.
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

    # 2. Identification.
    components: list[ComponentInstance] = []
    source = "none"

    if sections:
        components = identify_from_allin1_sections(sections)
        if components:
            source = "allin1_sections"

    if not components and lrc_content:
        # Tier-2: if beats missing, run analyze_audio_fast inline.
        inline_fast_ran = False
        if not beats and not downbeats:
            try:
                from .analyzer import analyze_audio_fast

                logger.info("Running inline fast_analyze for component beats...")
                fast_result = await analyze_audio_fast(
                    audio_path, cache_manager, content_hash
                )
                beats = fast_result.get("beats")
                downbeats = fast_result.get("downbeats")
                inline_fast_ran = True
            except Exception as e:
                logger.warning(f"Inline fast_analyze failed: {e}")

        # Determine song duration for position weighting.
        song_total_duration = None
        try:
            import librosa as _librosa

            y_tmp, sr_tmp = _librosa.load(str(audio_path), sr=None, mono=True)
            song_total_duration = float(_librosa.get_duration(y=y_tmp, sr=sr_tmp))
            del y_tmp
        except Exception:
            pass

        components = identify_from_lyrics_repetition(
            lrc_content,
            beats=beats,
            downbeats=downbeats,
            song_total_duration=song_total_duration,
        )
        if components:
            source = "lyrics_repetition"
            # v3: if inline fast_analyze failed and we have no beats, lower confidence.
            if not beats and not inline_fast_ran:
                for c in components:
                    c.confidence = 0.5

    if not components:
        return ([], "none")

    # 3. Load audio and compute per-component features.
    try:
        loop = asyncio.get_event_loop()

        def _load_audio():
            y, sr = librosa.load(str(audio_path), sr=None, mono=True)
            return y, sr

        y, sr = await loop.run_in_executor(None, _load_audio)

        for component in components:
            compute_component_features(y, sr, component, beats=beats, downbeats=downbeats)
    except Exception as e:
        logger.warning(f"Audio load / feature computation failed: {e}")

    # 4. Persist to local cache and R2.
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
    """Serialize components to the v3 components.json payload."""
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
                source=payload.get("component_source", ""),
            )
        )
    return components
