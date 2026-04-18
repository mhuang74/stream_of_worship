#!/usr/bin/env python3
"""Qwen3-ASR local MLX transcription POC script.

Uses mlx-qwen3-asr for local transcription on Apple Silicon
with context biasing and canonical-line fuzzy snap to produce LRC files.

This script mirrors the cloud variant but runs entirely locally.

Note: mlx-audio backend support is pending - current version does not include
Qwen3-ASR models. Only mlx-qwen3-asr is currently supported.
"""

import json
import os
import sys
from pathlib import Path
from typing import Optional

import typer

# Add parent directory to path for poc.utils import
sys.path.insert(0, str(Path(__file__).parent.parent))

# Import shared utilities
from poc.utils import extract_audio_segment, format_timestamp, resolve_song_audio_path

app = typer.Typer(help="Qwen3-ASR local MLX transcription POC")


def transcribe_mlx_qwen3_asr(
    audio_path: Path,
    model: str = "1.7B",
    context: Optional[str] = None,
) -> dict:
    """Run transcription using mlx-qwen3-asr backend.

    Args:
        audio_path: Path to audio file
        model: Model size (0.6B or 1.7B)
        context: Optional context string for biasing

    Returns:
        Raw transcription result as dict
    """
    from mlx_qwen3_asr import Session

    model_name = f"Qwen/Qwen3-ASR-{model}"
    typer.echo(f"Loading mlx-qwen3-asr ({model_name})...", err=True)

    session = Session(model=model_name)

    typer.echo(f"Transcribing: {audio_path}", err=True)
    if context:
        typer.echo(f"Using context biasing ({len(context)} chars)", err=True)

    result = session.transcribe(
        str(audio_path),
        context=context,
        language="Chinese",
        return_timestamps=True,
    )

    return result


# NOTE: mlx-audio backend not yet supported - current version 0.2.9 does not include
# Qwen3-ASR models. Available model types: parakeet, voxtral, wav2vec, glmasr, whisper
# when Qwen3-ASR support is added in mlx-audio, this function can be enabled:
#
# def transcribe_mlx_audio(
#     audio_path: Path,
#     model: str = "1.7B",
# ) -> any:
#     """Run transcription using mlx-audio backend.
#
#     Uses quantized 8-bit models from mlx-community.
#     Note: mlx-audio does not support context biasing.
#
#     Args:
#         audio_path: Path to audio file
#         model: Model size (0.6B or 1.7B)
#
#     Returns:
#         Raw transcription result object with .segments attribute
#     """
#     from mlx_audio.stt.generate import generate_transcription
#
#     model_name = f"mlx-community/Qwen3-ASR-{model}-8bit"
#     typer.echo(f"Loading mlx-audio ({model_name})...", err=True)
#
#     typer.echo(f"Transcribing: {audio_path}", err=True)
#
#     result = generate_transcription(
#         model=model_name,
#         audio_path=str(audio_path),
#     )
#
#     return result


def _get_field(item, field: str, default):
    """Get field from item, trying dict access first then getattr.

    Args:
        item: Dict or object to get field from
        field: Field name
        default: Default value if not found

    Returns:
        Field value or default
    """
    if isinstance(item, dict):
        return item.get(field, default)
    return getattr(item, field, default)


def extract_segments(result) -> list[dict]:
    """Extract segments from MLX output.

    Reconstructs phrase-level segments from top-level text and per-character
    timestamp segments by splitting on Chinese and ASCII punctuation.

    Args:
        result: Raw MLX output (TranscriptionResult object or dict)

    Returns:
        List of segment dicts with 'start', 'end', 'text' keys
    """
    # Get text and segments from result (handle both dict and dataclass)
    if isinstance(result, dict):
        text = result.get("text", "")
        raw_segments = result.get("segments", [])
    elif hasattr(result, "text"):
        text = result.text
        raw_segments = getattr(result, "segments", []) if result.segments else []
    else:
        typer.echo("Error: Result missing 'text' field", err=True)
        return []

    if not text or not raw_segments:
        typer.echo("Error: Missing 'text' or 'segments' in result", err=True)
        return []

    # Parse each per-char segment
    char_segments = []
    empty_count = 0
    for seg in raw_segments:
        seg_text = _get_field(seg, "text", "").strip()
        if seg_text:
            char_segments.append(
                {
                    "start": _get_field(seg, "start", 0),
                    "end": _get_field(seg, "end", 0),
                    "text": seg_text,
                }
            )
        else:
            empty_count += 1

    # Count non-punctuation characters in text
    punct_set = "。，、！？；：．.,!?;:"
    non_punct_char_count = sum(1 for c in text if c not in punct_set)

    # Check for length mismatch - fall back to per-char with warning
    if len(char_segments) != non_punct_char_count:
        typer.echo(
            f"Warning: Char count mismatch (text has {non_punct_char_count} non-punct chars, "
            f"found {len(char_segments)} segments). Falling back to per-character segments.",
            err=True,
        )
        segments = char_segments
    else:
        # Verify alignment and reconstruct phrase-level segments
        segments = []
        seg_idx = 0
        phrase_start = 0  # Position in text

        while seg_idx < len(char_segments):
            # Accumulate chars until we hit phrase boundary
            phrase_chars = []
            first_start = char_segments[seg_idx]["start"]
            last_end = char_segments[seg_idx]["end"]

            while seg_idx < len(char_segments):
                char = text[phrase_start]
                expected_char = char_segments[seg_idx]["text"]

                # Verify alignment
                if char != expected_char:
                    typer.echo(
                        f"Warning: Char mismatch at position {phrase_start}: "
                        f"text='{char}', segments['{seg_idx}']='{expected_char}'. "
                        "Falling back to per-character segments.",
                        err=True,
                    )
                    segments = char_segments
                    return segments

                phrase_chars.append(char)
                last_end = char_segments[seg_idx]["end"]
                phrase_start += 1
                seg_idx += 1

                # Stop at phrase boundary
                if seg_idx < len(text) and text[phrase_start] in punct_set:
                    # Skip the punctuation
                    phrase_start += 1
                    break

            phrase_text = "".join(phrase_chars).strip()
            if phrase_text:
                segments.append({"start": first_start, "end": last_end, "text": phrase_text})

    if not segments:
        typer.echo(
            f"Error: No valid text segments extracted (found {empty_count} empty segments)",
            err=True,
        )
    elif empty_count > 0:
        typer.echo(
            f"Warning: Filtered out {empty_count} empty segments, kept {len(segments)} valid",
            err=True,
        )

    return segments


def raw_to_dict(result) -> dict:
    """Convert TranscriptionResult to plain JSON-roundtrippable dict.

    Args:
        result: TranscriptionResult object or dict

    Returns:
        JSON-serializable dict
    """
    if hasattr(result, "__dict__"):
        orig_dict = result.__dict__
    elif isinstance(result, dict):
        orig_dict = result
    else:
        return {}

    return json.loads(json.dumps(orig_dict, ensure_ascii=False, default=str))


def compute_params_hash(params: dict) -> str:
    """Compute 8-char SHA256 prefix of canonical JSON for params.

    Args:
        params: Dict of parameters

    Returns:
        8-character hex string
    """
    import hashlib

    canonical = json.dumps(params, sort_keys=True, ensure_ascii=False)
    full_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return full_hash[:8]


def cache_file_name(
    cache_dir: Path,
    song_id: str,
    model: str,
    backend: str,
    params_hash: str,
) -> Path:
    """Generate cache file name for transcription.

    Args:
        cache_dir: Cache directory
        song_id: Song identifier
        model: Model size (0.6B or 1.7B)
        backend: MLX backend name
        params_hash: 8-char SHA256 prefix of parameters

    Returns:
        Path to cache file
    """
    safe_song_id = song_id.replace("/", "_").replace("\\", "_")
    filename = f"{safe_song_id}_{model}_{backend}_{params_hash}.json"
    return cache_dir / filename


def load_cached_transcription(cache_path: Path) -> Optional[dict]:
    """Load cached raw transcription dict.

    Args:
        cache_path: Path to cache file

    Returns:
        Raw dict from cache, or None if cache file not found/invalid
    """
    if not cache_path.exists():
        typer.echo(f"Cache not found at: {cache_path}", err=True)
        return None

    try:
        cache_data = json.loads(cache_path.read_text(encoding="utf-8"))

        cache_version = cache_data.get("cache_version")
        if cache_version != 2:
            typer.echo(f"Warning: Cache version {cache_version} incompatible, ignoring", err=True)
            return None

        raw = cache_data.get("raw")
        if not isinstance(raw, dict):
            typer.echo("Warning: Cache missing 'raw' dict, ignoring", err=True)
            return None

        if not isinstance(raw.get("text"), str) or not raw.get("text").strip():
            typer.echo("Warning: Cache 'raw.text' missing or empty, ignoring", err=True)
            return None

        segments = raw.get("segments")
        if not isinstance(segments, list) or len(segments) == 0:
            typer.echo("Warning: Cache 'raw.segments' missing or empty, ignoring", err=True)
            return None

        typer.echo(f"Loaded cached transcription from: {cache_path}", err=True)
        return cache_data
    except Exception as e:
        typer.echo(f"Warning: Cache file invalid, ignoring: {e}", err=True)
        return None


def save_cached_transcription(
    cache_path: Path,
    raw: dict,
    model: str,
    backend: str,
    params: dict,
    wall_time: float,
) -> None:
    """Save raw transcription to cache.

    Args:
        cache_path: Path to cache file
        raw: Raw dict from transcription result
        model: Model size used
        backend: Backend used
        params: Parameters that affect ASR
        wall_time: Wall-clock time taken
    """
    cache_path.parent.mkdir(parents=True, exist_ok=True)

    cache_data = {
        "cache_version": 2,
        "model": model,
        "backend": backend,
        "params": params,
        "wall_time": wall_time,
        "timestamp": __import__("time").time(),
        "raw": raw,
    }

    cache_path.write_text(json.dumps(cache_data, ensure_ascii=False, indent=2), encoding="utf-8")
    typer.echo(f"Saved transcription to cache: {cache_path}", err=True)


def _is_filler(text: str) -> bool:
    """Check if text is only filler interjections.

    Args:
        text: Text to check

    Returns:
        True if only interjection chars (嗯啊呃哦唉)
    """
    filler_chars = set("嗯啊呃哦嗯哦")
    return all(c in filler_chars for c in text.strip()) if text.strip() else False


def _normalize_text(text: str) -> str:
    """Normalize text by mapping variant characters.

    Args:
        text: Text to normalize

    Returns:
        Normalized text
    """
    # Map variant characters to standard forms
    char_map = {
        "鼵": "鼓",  # U+9F35 -> U+9F13 variant
    }

    return "".join(char_map.get(c, c) for c in text)


def _text_to_pinyin(text: str) -> str:
    """Convert Chinese characters to pinyin with tone markers.

    Args:
        text: Chinese text

    Returns:
        Pinyin representation with space-separated syllables
    """
    from pypinyin import lazy_pinyin, Style

    # Convert to pinyin without tone marks for better fuzzy matching
    pinyin_list = lazy_pinyin(text, style=Style.NORMAL)
    return " ".join(pinyin_list)


def _score(
    asr_text: str, canonical_line: str, target_script: str, use_pinyin: bool = False
) -> float:
    """Score ASR text against a canonical line.

    Uses partial_ratio for short fragments (≤3 Chinese chars),
    token_set_ratio otherwise.

    Args:
        asr_text: ASR output text
        canonical_line: Canonical lyric line
        target_script: Target script ("zh-hans" or "zh-hant")
        use_pinyin: Whether to use pinyin space for matching (handles homophones)

    Returns:
        Score between 0 and 1
    """
    from rapidfuzz import fuzz
    from zhconv import convert

    asr_normalized = _normalize_text(convert(asr_text, target_script))
    canonical_normalized = _normalize_text(convert(canonical_line, target_script))

    # Use pinyin matching if requested
    if use_pinyin:
        asr_pinyin = _text_to_pinyin(asr_normalized)
        canonical_pinyin = _text_to_pinyin(canonical_normalized)
        asr_text_comp = asr_pinyin
        canonical_text_comp = canonical_pinyin
    else:
        asr_text_comp = asr_normalized
        canonical_text_comp = canonical_normalized

    chinese_char_count = sum(1 for c in asr_normalized if "\u4e00" <= c <= "\u9fff")

    if chinese_char_count <= 3:
        return fuzz.partial_ratio(asr_text_comp, canonical_text_comp) / 100.0
    else:
        return fuzz.token_set_ratio(asr_text_comp, canonical_text_comp) / 100.0


def _combined_score(
    asr: str, canonical: str, target_script: str, asr_char_count: int
) -> float:
    """Score combining char and pinyin.

    Pinyin boost is disabled for long ASR segments (>8 Chinese chars) because
    long garbled text often contains phonetic substrings that accidentally match
    short canonical lines, producing false high-confidence snaps.

    Args:
        asr: ASR text
        canonical: Canonical line text
        target_script: Target script ("zh-hans" or "zh-hant")
        asr_char_count: Number of Chinese chars in ASR text

    Returns:
        Combined score between 0 and 1
    """
    char_s = _score(asr, canonical, target_script, use_pinyin=False)
    if asr_char_count <= 8:
        pin_s = _score(asr, canonical, target_script, use_pinyin=True) * 0.9
        return max(char_s, pin_s)
    return char_s


def detect_chinese_script(text: str) -> str:
    """Detect whether Chinese text is traditional or simplified.

    Args:
        text: Chinese text to analyze

    Returns:
        "zh-hans" if simplified, "zh-hant" if traditional
    """
    from zhconv import convert

    total_chars = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    if total_chars == 0:
        return "zh-hans"

    simplified = convert(text, "zh-hans")
    if simplified == text:
        return "zh-hans"
    else:
        return "zh-hant"


def canonical_line_snap(
    segments: list[dict],
    lyrics: list[str],
    threshold: float = 0.60,
    snap_algo: str = "dp",
    dp_skip_penalty: float = 0.15,
    dp_wrap_penalty: float = 0.05,
    dp_k_max: int = 4,
) -> tuple[list[tuple[float, str, bool, bool]], list[dict]]:
    """Snap ASR segments to canonical lyrics using fuzzy matching.

    Implements a multi-pass algorithm:
    - Merge adjacent fragmented ASR phrases
    - Force-anchor first 1-2 content segments (skip filler)
    - Either greedy walk or DP consensus for remaining segments
    - Dedup consecutive identical snaps

    Args:
        segments: List of dicts with 'start', 'end', 'text' keys
        lyrics: List of canonical lyric lines
        threshold: Minimum fuzzy score to snap (0-1)
        snap_algo: Algorithm to use ("greedy" or "dp")
        dp_skip_penalty: Penalty for skipping canonical indices within a layer
        dp_wrap_penalty: Penalty for starting a new layer mid-sequence
        dp_k_max: Maximum number of layer wraps (chorus repeats)

    Returns:
        Tuple of:
        - List of (start, final_text, replaced, merged) tuples
        - List of merged segment dicts with 'merged' flag
        - List of DP assignment info (for diagnostics) or None
    """
    from zhconv import convert

    target_script = (
        detect_chinese_script("".join([l for l in lyrics if l.strip()])) if lyrics else "zh-hans"
    )

    canonical_lines = [l for l in lyrics if l.strip()]

    if not canonical_lines:
        return [], []

    WINDOW_SIZE = 7
    CHORUS_REPEAT_THRESHOLD = 0.90
    MERGE_GAIN = 0.10
    OPENING_ANCHOR_COUNT = 2

    sample_text = "".join(canonical_lines)
    target_script = detect_chinese_script(sample_text)

    canonical_lines_normalized = [
        _normalize_text(convert(l, target_script)) for l in canonical_lines
    ]

    if not segments:
        return [], []

    n_segments = len(segments)
    n_canonical = len(canonical_lines)

    merged_segments = []
    i = 0

    while i < n_segments:
        seg_text = segments[i]["text"]

        if _is_filler(seg_text):
            i += 1
            continue

        should_merge = False
        merged_text = None

        if i < n_segments - 1:
            next_seg_text = segments[i + 1]["text"]
            if _is_filler(next_seg_text):
                # Add current segment and skip both current and filler
                merged_segments.append(
                    {
                        "start": segments[i]["start"],
                        "end": segments[i]["end"],
                        "text": seg_text,
                        "merged": False,
                    }
                )
                i += 2
                continue

            merged_text = seg_text + next_seg_text
            # Score with char + pinyin matching
            def score_text(text):
                best = 0
                for cl in canonical_lines:
                    s = max(
                        _score(text, cl, target_script, use_pinyin=False),
                        _score(text, cl, target_script, use_pinyin=True) * 0.95,
                    )
                    if s > best:
                        best = s
                return best

            best_score_i = score_text(seg_text)
            best_score_merge = score_text(merged_text)

            chinese_char_count = sum(1 for c in seg_text if "\u4e00" <= c <= "\u9fff")
            short_frag = chinese_char_count <= 3

            if short_frag and not _is_filler(segments[i + 1]["text"]):
                next_char_count = sum(
                    1 for c in segments[i + 1]["text"] if "\u4e00" <= c <= "\u9fff"
                )
                next_short_frag = next_char_count <= 3
                if next_short_frag:
                    if best_score_merge > 0.30:
                        should_merge = True
                elif best_score_merge > best_score_i:
                    should_merge = True
            elif best_score_merge > best_score_i:
                should_merge = True

            if should_merge:
                merged_segments.append(
                    {
                        "start": segments[i]["start"],
                        "end": segments[i + 1]["end"],
                        "text": merged_text,
                        "merged": True,
                    }
                )
                i += 2
                continue

        merged_segments.append(
            {
                "start": segments[i]["start"],
                "end": segments[i]["end"],
                "text": seg_text,
                "merged": False,
            }
        )
        i += 1

    first_content_idx = 0
    while first_content_idx < len(merged_segments) and _is_filler(
        merged_segments[first_content_idx]["text"]
    ):
        first_content_idx += 1

    results = []
    cursor = 0

    anchor_start = first_content_idx
    anchor_end = min(first_content_idx + OPENING_ANCHOR_COUNT, len(merged_segments), n_canonical)

    for k in range(anchor_start, anchor_end):
        if k < len(merged_segments) and k - anchor_start < n_canonical:
            canonical_text = _normalize_text(canonical_lines[k - anchor_start])
            results.append(
                (
                    merged_segments[k]["start"],
                    canonical_text,
                    True,
                    merged_segments[k].get("merged", False),
                )
            )

    dp_assignments = None
    AVG_LINE_DURATION = 8.0

    dp_segments = merged_segments[anchor_end:]
    n_dp_segments = len(dp_segments)

    if snap_algo == "dp" and n_dp_segments > 0:
        S = []
        L = []
        for seg in dp_segments:
            asr_char_count = sum(1 for c in seg["text"] if "\u4e00" <= c <= "\u9fff")
            seg_scores = [
                _combined_score(seg["text"], canonical_lines[j], target_script, asr_char_count)
                for j in range(n_canonical)
            ]
            S.append(seg_scores)
            seg_duration = seg["end"] - seg["start"]
            n_lines_est = max(1, round(seg_duration / AVG_LINE_DURATION))
            L.append(n_lines_est)

        NEG_INF = float("-inf")
        dp = {}
        back_ptr = {}

        anchor_last_j = min(anchor_end - anchor_start - 1, n_canonical - 1)
        if anchor_last_j < 0:
            anchor_last_j = 0
        dp[(0, anchor_last_j, 0)] = 0.0

        for i in range(n_dp_segments):
            seg = dp_segments[i]
            n_lines_max = min(L[i] + 1, n_canonical)

            for (prev_i, prev_j, prev_k), prev_score in list(dp.items()):
                if prev_i != i:
                    continue

                for n_lines in range(1, n_lines_max + 1):
                    for j_start in range(n_canonical):
                        j_end = j_start + n_lines - 1
                        if j_end >= n_canonical:
                            continue

                        line_score = sum(S[i][j] for j in range(j_start, j_end + 1))

                        if j_start == prev_j + 1:
                            new_k = prev_k
                            penalty = 0.0
                            new_score = prev_score + line_score - penalty
                            key = (i + 1, j_end, new_k)
                            if new_score > dp.get(key, NEG_INF):
                                dp[key] = new_score
                                back_ptr[key] = (prev_i, prev_j, prev_k, j_start, n_lines)

                        elif j_start > prev_j + 1:
                            new_k = prev_k
                            skip_penalty = dp_skip_penalty * (j_start - prev_j - 1)
                            new_score = prev_score + line_score - skip_penalty
                            key = (i + 1, j_end, new_k)
                            if new_score > dp.get(key, NEG_INF):
                                dp[key] = new_score
                                back_ptr[key] = (prev_i, prev_j, prev_k, j_start, n_lines)

                        if prev_k + 1 < dp_k_max and j_start <= prev_j:
                            new_k = prev_k + 1
                            wrap_penalty = dp_wrap_penalty if j_start > 0 else 0.0
                            new_score = prev_score + line_score - wrap_penalty
                            key = (i + 1, j_end, new_k)
                            if new_score > dp.get(key, NEG_INF):
                                dp[key] = new_score
                                back_ptr[key] = (prev_i, prev_j, prev_k, j_start, n_lines)

        best_end = None
        best_end_score = NEG_INF
        for (i, j, k), score in dp.items():
            if i == n_dp_segments and score > best_end_score:
                best_end_score = score
                best_end = (i, j, k)

        dp_assignments = []
        if best_end is not None:
            assignments = []
            current = best_end
            while current in back_ptr:
                prev_i, prev_j, prev_k, j_start, n_lines = back_ptr[current]
                assignments.append((j_start, n_lines, current[2]))
                current = (prev_i, prev_j, prev_k)
            assignments.reverse()

            for i, (j_start, n_lines, layer_k) in enumerate(assignments):
                seg = dp_segments[i]
                seg_merged = seg.get("merged", False)
                seg_duration = seg["end"] - seg["start"]

                lines_to_emit = []
                for offset in range(n_lines):
                    j = j_start + offset
                    if j < n_canonical:
                        lines_to_emit.append(j)

                n_lines_emit = len(lines_to_emit)
                for idx, line_idx in enumerate(lines_to_emit):
                    if n_lines_emit > 1:
                        timestamp = seg["start"] + seg_duration * idx / (n_lines_emit - 1)
                    else:
                        timestamp = seg["start"]
                    line_text = _normalize_text(canonical_lines[line_idx])
                    results.append((timestamp, line_text, True, seg_merged))

                dp_assignments.append({
                    "seg_idx": anchor_end + i,
                    "canonical_start": j_start,
                    "n_lines": n_lines,
                    "layer_k": layer_k,
                })

    elif snap_algo == "greedy" or n_dp_segments == 0:
        cursor = anchor_end

        for seg in merged_segments[anchor_end:]:
            asr_text = seg["text"]
            seg_merged = seg.get("merged", False)

            if cursor < n_canonical:
                window_start = cursor
                window_end = min(cursor + WINDOW_SIZE, n_canonical)
            else:
                window_start = 0
                window_end = min(WINDOW_SIZE, n_canonical)

            asr_char_count = sum(1 for c in asr_text if "\u4e00" <= c <= "\u9fff")

            scored_window = []
            if window_end > window_start:
                scored_window = [
                    _combined_score(asr_text, canonical_lines[j], target_script, asr_char_count)
                    for j in range(window_start, window_end)
                ]

            best_idx_in_window = -1
            best_score_window = 0
            if scored_window:
                best_idx_in_window = max(range(len(scored_window)), key=lambda k: scored_window[k])
                best_score_window = scored_window[best_idx_in_window]

            scored_all = [_combined_score(asr_text, cl, target_script, asr_char_count) for cl in canonical_lines]
            best_idx_all = max(range(n_canonical), key=lambda k: scored_all[k])
            best_score_all = scored_all[best_idx_all]

            selected_line = None
            selected_idx = -1
            used_window = False

            LOW_CONFIDENCE_THRESHOLD = 0.40
            seq_cursor = cursor % n_canonical

            low_confidence = best_score_all < LOW_CONFIDENCE_THRESHOLD

            if low_confidence:
                seg_duration_s = seg["end"] - seg["start"]
                n_lines_est = max(1, round(seg_duration_s / AVG_LINE_DURATION))
                selected_idx = seq_cursor
                selected_line = canonical_lines[seq_cursor]
                used_window = True
            elif best_score_window >= best_score_all * 0.9 and scored_window:
                selected_line = canonical_lines[window_start + best_idx_in_window]
                selected_idx = window_start + best_idx_in_window
                used_window = True
                n_lines_est = 1
            else:
                selected_line = canonical_lines[best_idx_all]
                selected_idx = best_idx_all
                used_window = False
                n_lines_est = 1

            lines_to_emit = []

            if low_confidence and n_lines_est > 1:
                for k in range(n_lines_est):
                    emit_idx = (seq_cursor + k) % n_canonical
                    lines_to_emit.append((emit_idx, seg["start"]))
            else:
                if used_window and selected_idx > cursor:
                    gap_size = selected_idx - cursor
                    if gap_size <= 2:
                        for skipped_idx in range(cursor, selected_idx):
                            lines_to_emit.append((skipped_idx, seg["start"]))

            if not low_confidence or n_lines_est == 1:
                lines_to_emit.append((selected_idx, seg["start"]))

            if not (low_confidence and n_lines_est > 1):
                lines_to_emit.sort(key=lambda x: x[0])

            seg_duration = seg["end"] - seg["start"]
            n_lines = len(lines_to_emit)
            for i, (line_idx, _) in enumerate(lines_to_emit):
                if n_lines > 1:
                    timestamp = seg["start"] + seg_duration * i / (n_lines - 1)
                else:
                    timestamp = seg["start"]
                line_text = _normalize_text(canonical_lines[line_idx])
                results.append((timestamp, line_text, True, seg_merged))

            if low_confidence and n_lines_est > 1:
                cursor = cursor + n_lines_est
            elif lines_to_emit:
                cursor = lines_to_emit[-1][0] + 1
            else:
                cursor = selected_idx + 1

    deduped_results = results

    return deduped_results, merged_segments, dp_assignments


def _detect_extra_lines_in_segment(
    asr_text: str,
    canonical_lines: list[str],
    target_script: str,
    matched_idx: int,
    n_canonical: int,
    matched_score: float,
) -> list[int]:
    """Detect if an ASR segment contains content for multiple canonical lines.

    Uses fuzzy matching to find lines that are clearly present in the ASR text.
    Returns additional line indices that should be emitted from this segment.

    Args:
        asr_text: The ASR text from the segment
        canonical_lines: List of canonical lines
        target_script: Target script for conversion
        matched_idx: The index of the already-matched canonical line
        n_canonical: Total number of canonical lines
        matched_score: The fuzzy score of the matched line

    Returns:
        List of additional canonical line indices to emit (sorted)
    """
    from zhconv import convert

    asr_char_count = sum(1 for c in asr_text if "\u4e00" <= c <= "\u9fff")

    # Only check for long segments (merged segments with 15+ chars)
    if asr_char_count < 15:
        return []

    # Require high confidence to detect extra lines
    # Don't try to detect extras in low-confidence segments (garbled ASR)
    if matched_score >= 0.85:
        score_threshold = 0.60  # High threshold for high-confidence segments
    elif matched_score >= 0.70:
        score_threshold = 0.65  # Medium threshold
    else:
        return []  # Don't try to detect extras in low-confidence segments

    extra_lines = []

    # Check next few canonical lines to see if they appear in the ASR text
    # Look up to 3 lines ahead
    check_end = min(matched_idx + 4, n_canonical)

    for check_idx in range(matched_idx + 1, check_end):
        # Skip if already have this line
        if check_idx in extra_lines:
            continue

        score_char = _score(asr_text, canonical_lines[check_idx], target_script, use_pinyin=False)
        score_pinyin = _score(asr_text, canonical_lines[check_idx], target_script, use_pinyin=True) * 0.95
        fuzzy_score = max(score_char, score_pinyin)

        # Add line if it meets threshold
        if fuzzy_score >= score_threshold:
            extra_lines.append(check_idx)

    return sorted(extra_lines)


def results_to_lrc(results: list[tuple[float, str, bool, bool]]) -> str:
    """Convert results to LRC format.

    Args:
        results: List of (start, text, replaced, merged) tuples

    Returns:
        LRC format string
    """
    lines = []
    for start, text, _replaced, _merged in results:
        timestamp = format_timestamp(start)
        lines.append(f"{timestamp} {text}")
    return "\n".join(lines)


def _strip_timestamp(line: str) -> str:
    """Remove timestamp from a line.

    Expected format: [HH:MM:SS]    text
    """
    import re
    line = line.lstrip()
    match = re.search(r'\[.*?\]\s*', line)
    if match:
        return line[match.end():].strip()
    return line.strip()


def generate_comparison_report(
    verified_lyrics: list[str],
    output_lyrics: list[str],
    output_path: Path,
) -> None:
    """Generate comparison report between verified and output lyrics.

    Args:
        verified_lyrics: List of verified lyric lines
        output_lyrics: List of output lyric lines
        output_path: Path to write comparison report
    """
    from difflib import SequenceMatcher

    lines = []
    lines.append("ALIGNED LYRICS COMPARISON")
    lines.append("=========================\n")
    lines.append("Key:")
    lines.append("[INPUT]  = verified.txt (reference)")
    lines.append("[OUTPUT] = out.txt (transcription)")
    lines.append("   GAP   = Line missing from output")
    lines.append("   MISMATCH = Wrong content")
    lines.append("")
    lines.append("INPUT Line    | OUTPUT Line   | Content")
    lines.append("--------------+---------------+------------------------------")

    matcher = SequenceMatcher(None, verified_lyrics, output_lyrics)

    matching_count = 0
    gap_ranges = []
    mismatch_details = []

    pending_gap_lines = []
    pending_gap_start = None
    gap_number = 0

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            # Process pending gap before showing matched lines
            if pending_gap_lines:
                gap_start = pending_gap_start
                gap_last = gap_start + len(pending_gap_lines) - 1
                gap_ranges.append((gap_start, gap_last))
                gap_number += 1
                lines.append(f">>> GAP #{gap_number}: LINES {gap_start}-{gap_last} COMPLETELY MISSING <<<")
                for gi, line in enumerate(pending_gap_lines):
                    lines.append(f"{gap_start + gi:<14} |      GAP      | {line}")
                lines.append("")
                pending_gap_lines = []
                pending_gap_start = None

            # Show aligned lines
            for idx in range(i1, i2):
                vi = idx + 1
                vj = j1 + (idx - i1) + 1
                lines.append(f"{vi:<14} | {vj:<14} | {verified_lyrics[idx]}")
                matching_count += 1

        elif tag == "delete":
            # Gap in output - defer processing
            if pending_gap_start is None:
                pending_gap_start = i1 + 1
            for idx in range(i1, i2):
                pending_gap_lines.append(verified_lyrics[idx])

        elif tag == "insert":
            # Process pending gap before showing extra lines
            if pending_gap_lines:
                gap_start = pending_gap_start
                gap_last = gap_start + len(pending_gap_lines) - 1
                gap_ranges.append((gap_start, gap_last))
                gap_number += 1
                lines.append(f">>> GAP #{gap_number}: LINES {gap_start}-{gap_last} COMPLETELY MISSING <<<")
                for gi, line in enumerate(pending_gap_lines):
                    lines.append(f"{gap_start + gi:<14} |      GAP      | {line}")
                lines.append("")
                pending_gap_lines = []
                pending_gap_start = None

            # Show extra output lines
            for idx in range(j1, j2):
                vj = idx + 1
                lines.append(f"{'EXTRA':<14} | {vj:<14} | {output_lyrics[idx]}")

        elif tag == "replace":
            # Process pending gap before showing mismatch
            if pending_gap_lines:
                gap_start = pending_gap_start
                gap_last = gap_start + len(pending_gap_lines) - 1
                gap_ranges.append((gap_start, gap_last))
                gap_number += 1
                lines.append(f">>> GAP #{gap_number}: LINES {gap_start}-{gap_last} COMPLETELY MISSING <<<")
                for gi, line in enumerate(pending_gap_lines):
                    lines.append(f"{gap_start + gi:<14} |      GAP      | {line}")
                lines.append("")
                pending_gap_lines = []
                pending_gap_start = None

            # Show mismatch
            vi = i1 + 1
            vj = j1 + 1
            expected = verified_lyrics[i1]
            actual = output_lyrics[j1]
            lines.append(f"{vi:<14} | {vj:<14} | {expected} [DIFFERS]")
            lines.append(f"{'':<14} | {'':<14} (expected: {expected})")
            lines.append(f"{'':<14} | {'':<14} (got: {actual})")
            mismatch_details.append((vi, expected, actual))

    # Handle final pending gap
    if pending_gap_lines:
        gap_start = pending_gap_start
        gap_last = gap_start + len(pending_gap_lines) - 1
        gap_ranges.append((gap_start, gap_last))
        gap_number += 1
        lines.append(f">>> GAP #{gap_number}: LINES {gap_start}-{gap_last} COMPLETELY MISSING <<<")
        for gi, line in enumerate(pending_gap_lines):
            lines.append(f"{gap_start + gi:<14} |      GAP      | {line}")
        lines.append("")

    lines.append("")
    lines.append("DETAILED GAP ANALYSIS")
    lines.append("=====================")
    if gap_ranges:
        for i, (g_start, g_last) in enumerate(gap_ranges, 1):
            lines.append(f"GAP #{i}: Lines {g_start}-{g_last}")
    else:
        lines.append("No gaps detected in transcription")

    lines.append("")
    lines.append("SUMMARY")
    lines.append("=======")
    total_verified = len(verified_lyrics)
    matching_rate = (matching_count / total_verified * 100) if total_verified > 0 else 0

    if gap_ranges:
        gap_list = ", ".join(f"Lines {s}-{l}" for s, l in gap_ranges)
        lines.append(f"Missing lines: {gap_list}")
    else:
        lines.append("Missing lines: None")

    if mismatch_details:
        lines.append(f"Content differs: {', '.join(str(x[0]) for x in mismatch_details)}")
        lines.append("")
        lines.append("Differences:")
        for line_num, expected, actual in mismatch_details:
            lines.append(f"  Line {line_num}: (expected: {expected}, got: {actual})")
    else:
        lines.append("Content differs: None")

    lines.append(f"Matching rate: {matching_count}/{total_verified} lines ({matching_rate:.1f}%)")

    output_path.write_text("\n".join(lines))


def write_diagnostic(
    segments: list[dict],
    lyrics: list[str],
    results: list[tuple[float, str, bool, bool]],
    output_path: Path,
    wall_time: float,
    merged_segments: Optional[list[dict]] = None,
    dp_assignments: Optional[list[dict]] = None,
) -> None:
    """Write diagnostic markdown file.

    Args:
        segments: List of segment dicts with 'start', 'end', 'text' keys
        lyrics: List of canonical lyric lines
        results: List of (start, final_text, replaced, merged) tuples
        output_path: Path to write diagnostic.md
        wall_time: Wall-clock elapsed time in seconds
        merged_segments: List of merged segment dicts
        dp_assignments: List of DP assignment dicts with seg_idx, canonical_start, n_lines, layer_k
    """
    from zhconv import convert

    canonical_lines = [l for l in lyrics if l.strip()]

    lines = []
    lines.append("# Qwen3-ASR Local MLX Diagnostic Report\n")
    lines.append("## Summary\n\n")
    lines.append(f"ASR segments: {len(segments)}\n")
    lines.append(f"Canonical lines: {len(canonical_lines)}\n")
    lines.append(f"Output lines: {len(results)}\n")

    replaced_count = sum(1 for _, _, replaced, _ in results if replaced)
    kept_count = len(results) - replaced_count
    lines.append(f"Replaced by snap: {replaced_count}\n")
    lines.append(f"Kept original: {kept_count}\n")
    lines.append(f"Merged segments: {sum(1 for _, _, _, merged in results if merged)}\n")

    # Detect target script for scoring
    sample_text = "".join(canonical_lines)
    target_script = detect_chinese_script(sample_text) if sample_text else "zh-hans"

    # Calculate average score using shared _score helper
    scores = []
    for seg, results_item in zip(segments, results):
        asr_text = seg["text"]
        best_score = 0
        for cl in canonical_lines:
            s = _score(asr_text, cl, target_script)
            if s > best_score:
                best_score = s
        scores.append(best_score)

    if scores:
        avg_score = sum(scores) / len(scores)
        lines.append(f"Average snap score: {avg_score:.2f}\n")

    if segments:
        duration = segments[-1]["end"] - segments[0]["start"]
        lines.append(f"Audio duration: {duration:.2f}s\n")
        if duration > 0:
            lines.append(f"Segments per second: {len(segments) / duration:.2f}\n")
            lines.append(f"Wall-clock time: {wall_time:.2f}s\n")
            lines.append(f"Real-time factor: {wall_time / duration:.2f}x\n")
        else:
            lines.append("Warning: Invalid duration (0 or negative)\n")

    # Get RAM peak if available
    try:
        import psutil

        process = psutil.Process()
        memory_mb = process.memory_info().rss / 1024 / 1024
        lines.append(f"Peak RAM usage: ~{memory_mb:.1f} MB\n")
    except Exception:
        pass

    lines.append("\n## Segment Details\n\n")

    if dp_assignments:
        lines.append("| Start | End | ASR Text | Matched Canonical | Score | Replaced | Merged | Canon Idx | Layer | N Lines |\n")
        lines.append("|-------|-----|----------|-------------------|-------|----------|--------|-----------|-------|---------|\n")
    else:
        lines.append("| Start | End | ASR Text | Matched Canonical | Score | Replaced | Merged |\n")
        lines.append("|-------|-----|----------|-------------------|-------|----------|--------|\n")

    dp_lookup = {}
    if dp_assignments:
        for assign in dp_assignments:
            dp_lookup[assign["seg_idx"]] = assign

    segments_to_diagnose = merged_segments if merged_segments is not None else segments
    for seg_idx, (seg, (start, final_text, replaced, merged)) in enumerate(
        zip(segments_to_diagnose, results)
    ):
        asr_text = seg["text"]
        best_score = 0
        best_line = ""
        for cl in canonical_lines:
            s = _score(asr_text, cl, target_script)
            if s > best_score:
                best_score = s
                best_line = cl

        merged_mark = "Yes" if merged else ""

        if dp_assignments and seg_idx in dp_lookup:
            assign = dp_lookup[seg_idx]
            canon_idx = assign["canonical_start"]
            layer_k = assign["layer_k"]
            n_lines = assign["n_lines"]
            lines.append(
                f"| {seg['start']:6.2f} | {seg['end']:4.2f} | {asr_text[:30]:30s} | {best_line[:30]:30s} | {best_score:5.2f} | {'Yes' if replaced else 'No':6s} | {merged_mark:6s} | {canon_idx:9d} | {layer_k:5d} | {n_lines:7d} |\n"
            )
        elif dp_assignments:
            lines.append(
                f"| {seg['start']:6.2f} | {seg['end']:4.2f} | {asr_text[:30]:30s} | {best_line[:30]:30s} | {best_score:5.2f} | {'Yes' if replaced else 'No':6s} | {merged_mark:6s} | {'-':>9s} | {'-':>5s} | {'-':>7s} |\n"
            )
        else:
            lines.append(
                f"| {seg['start']:6.2f} | {seg['end']:4.2f} | {asr_text[:30]:30s} | {best_line[:30]:30s} | {best_score:5.2f} | {'Yes' if replaced else 'No':6s} | {merged_mark:6s} |\n"
            )

    output_path.write_text("".join(lines))


@app.command()
def main(
    song_id: str = typer.Argument(
        ..., help="Song ID (e.g., wo_yao_quan_xin_zan_mei_244) or path to audio file"
    ),
    vocal_stem: Optional[Path] = typer.Option(
        None, "--vocal-stem", help="Path to vocal stem FLAC file to use for transcription"
    ),
    use_vocals: bool = typer.Option(
        True, "--use-vocals/--no-use-vocals", help="Use vocals stem if available"
    ),
    output: Optional[Path] = typer.Option(
        None, "--output", "-o", help="Output file (default: stdout)"
    ),
    model: str = typer.Option("1.7B", "--model", help="Model size (0.6B or 1.7B)"),
    backend: str = typer.Option("mlx-qwen3-asr", "--backend", help="MLX backend (mlx-qwen3-asr)"),
    snap: bool = typer.Option(True, "--snap/--no-snap", help="Enable canonical-line fuzzy snap"),
    snap_threshold: float = typer.Option(
        0.60, "--snap-threshold", help="Minimum fuzzy score to snap (0-1)"
    ),
    snap_algo: str = typer.Option(
        "greedy", "--snap-algo", help="Snap algorithm: 'greedy' (default) or 'dp' (dynamic programming)"
    ),
    dp_skip_penalty: float = typer.Option(
        0.15, "--dp-skip-penalty", help="DP penalty for skipping canonical indices within a layer"
    ),
    dp_wrap_penalty: float = typer.Option(
        0.05, "--dp-wrap-penalty", help="DP penalty for starting a new layer mid-sequence"
    ),
    dp_k_max: int = typer.Option(
        4, "--dp-k-max", help="DP maximum number of layer wraps (chorus repeats)"
    ),
    lyrics_context: bool = typer.Option(
        True, "--lyrics-context/--no-lyrics-context", help="Enable context biasing with lyrics"
    ),
    context_max_chars: int = typer.Option(
        2000, "--context-max-chars", help="Max chars for context (2000 default)"
    ),
    save_raw: Optional[Path] = typer.Option(
        None, "--save-raw", help="Directory to save raw ASR response + diagnostics"
    ),
    start: float = typer.Option(
        0.0, "--start", "-s", help="Start timestamp in seconds (default: 0)"
    ),
    end: Optional[float] = typer.Option(
        None, "--end", "-e", help="End timestamp in seconds (default: full song)"
    ),
    cache_dir: Optional[Path] = typer.Option(
        None,
        "--cache-dir",
        help="Directory for transcription cache (default: ~/.cache/qwen3_asr)",
    ),
    reuse_transcription: bool = typer.Option(
        True,
        "--reuse-transcription/--no-reuse-transcription",
        help="Reuse cached transcription if available",
    ),
    force_rerun: bool = typer.Option(
        False, "--force-rerun", help="Ignore cache and rerun transcription"
    ),
    verified_lyrics: Optional[Path] = typer.Option(
        None, "--verified-lyrics", "-v", help="Path to verified lyrics file for comparison"
    ),
    comparison_output: Optional[Path] = typer.Option(
        None, "--comparison-output", "-c", help="Path to write comparison report"
    ),
):
    """Run Qwen3-ASR local MLX transcription on a song and output LRC format.

    Transcription uses mlx-qwen3-asr with context biasing and canonical-line
    snap enabled by default.

    Transcription results are cached and reused by default. Use --force-rerun
    to ignore the cache.
    """
    # Validate model
    if model not in ("0.6B", "1.7B"):
        typer.echo(f"Error: Invalid model '{model}'. Use '0.6B' or '1.7B'.", err=True)
        raise typer.Exit(1)

    # Validate backend
    if backend != "mlx-qwen3-asr":
        typer.echo(
            f"Error: Backend '{backend}' not yet supported. Only 'mlx-qwen3-asr' is currently supported.",
            err=True,
        )
        raise typer.Exit(1)
        typer.echo(
            "Warning: mlx-audio backend does not support context biasing. "
            "Use --backend mlx-qwen3-asr for context support.",
            err=True,
        )

    # Resolve inputs
    audio_path, lyrics = resolve_song_audio_path(song_id, use_vocals=use_vocals)

    # Override with provided vocal stem if specified
    if vocal_stem:
        if not vocal_stem.exists():
            typer.echo(f"Error: Vocal stem file not found: {vocal_stem}", err=True)
            raise typer.Exit(1)
        audio_path = vocal_stem
        typer.echo(f"Using provided vocal stem: {audio_path}", err=True)

    if lyrics is None:
        typer.echo("Error: No lyrics from catalog; cannot run biasing/snap.", err=True)
        raise typer.Exit(1)

    lyrics_text = "\n".join(lyrics)

    # Determine time range
    effective_end: Optional[float] = end if end and end > 0 else None
    if effective_end:
        typer.echo(f"Transcribing segment: {start}s to {effective_end}s", err=True)
    elif start > 0:
        typer.echo(f"Transcribing from {start}s to end", err=True)
    else:
        typer.echo("Transcribing full song", err=True)

    # Build params dict for cache key
    # Include vocal_stem filename to ensure different stems get separate cache entries
    vocal_stem_key = vocal_stem.name if vocal_stem else None
    params = {
        "use_vocals": use_vocals,
        "lyrics_context": lyrics_context,
        "context_max_chars": context_max_chars,
        "start": start,
        "end": effective_end,
        "language": "Chinese",
        "vocal_stem": vocal_stem_key,
    }
    params_hash = compute_params_hash(params)

    # Set up cache directory
    if cache_dir is None:
        cache_dir = Path.home() / ".cache" / "qwen3_asr"
    cache_path = cache_file_name(cache_dir, song_id, model, backend, params_hash)

    raw: Optional[dict] = None
    wall_time = 0.0
    used_cache = False

    # Check for cached transcription
    if reuse_transcription and not force_rerun:
        cached_data = load_cached_transcription(cache_path)
        if cached_data is not None:
            raw = cached_data.get("raw")
            used_cache = True
            typer.echo("Using cached transcription", err=True)

    # No valid cache available — only run inference if explicitly requested
    if raw is None and not force_rerun:
        typer.echo(
            "No valid cached transcription available. "
            "Rerun with --force-rerun to perform qwen3-asr inference.",
            err=True,
        )
        raise typer.Exit(1)

    # Run transcription if not using cache
    if raw is None:
        # Extract segment if needed
        transcribe_path = audio_path
        segment_path: Optional[Path] = None
        if start > 0 or effective_end is not None:
            typer.echo(f"Extracting audio segment: {start}s to {effective_end or 'end'}s", err=True)
            segment_path = extract_audio_segment(audio_path, start, effective_end or 3600)
            transcribe_path = segment_path

        import time

        wall_time_start = time.time()

        try:
            # Build context
            # Format: space-separated phrases (per mlx-qwen3-asr convention for vocabulary biasing)
            # Newline-separated lyrics cause the model to hallucinate them as transcription output
            context = None
            if lyrics_context:
                context = " ".join(l.strip() for l in lyrics if l.strip())
                if len(context) > context_max_chars:
                    context = context[:context_max_chars]
                    typer.echo(f"Context truncated to {context_max_chars} chars", err=True)

            # Transcribe (only mlx-qwen3-asr currently supported)
            result = transcribe_mlx_qwen3_asr(
                audio_path=transcribe_path,
                model=model,
                context=context,
            )

            wall_time = time.time() - wall_time_start
            typer.echo(f"Transcription completed in {wall_time:.2f}s", err=True)

            # Convert to raw dict
            raw = raw_to_dict(result)

            # Save to cache
            save_cached_transcription(cache_path, raw, model, backend, params, wall_time)

        finally:
            if segment_path and segment_path.exists():
                segment_path.unlink()

        typer.echo(f"Saved transcription to cache for reuse", err=True)

    # Write raw output if requested (works on cache hit or miss)
    if save_raw:
        save_raw.mkdir(parents=True, exist_ok=True)
        raw_file = save_raw / "asr_raw.json"
        raw_file.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        typer.echo(f"Saved raw ASR result to: {raw_file}", err=True)

    # Extract segments from raw dict
    segments = extract_segments(raw)

    if not segments:
        typer.echo("Error: No segments extracted from ASR result", err=True)
        raise typer.Exit(1)

    typer.echo(f"Extracted {len(segments)} segments", err=True)

    if not used_cache:
        typer.echo(f"Extracted {len(segments)} segments", err=True)

    # Process segments
    if snap:
        results, merged_segments, dp_assignments = canonical_line_snap(
            segments,
            lyrics,
            threshold=snap_threshold,
            snap_algo=snap_algo,
            dp_skip_penalty=dp_skip_penalty,
            dp_wrap_penalty=dp_wrap_penalty,
            dp_k_max=dp_k_max,
        )
        replaced_count = sum(1 for _, _, replaced, _ in results if replaced)
        typer.echo(
            f"Canonical-line snap ({snap_algo}): {replaced_count}/{len(results)} segments replaced", err=True
        )

        # Write diagnostic if requested
        if save_raw:
            save_raw.mkdir(parents=True, exist_ok=True)
            diag_file = save_raw / "diagnostic.md"
            write_diagnostic(
                segments, lyrics, results, diag_file, wall_time, merged_segments, dp_assignments
            )
            typer.echo(f"Saved diagnostic report to: {diag_file}", err=True)
    else:
        results = [(seg["start"], seg["text"], False, False) for seg in segments]
        typer.echo(f"Snap disabled, using raw ASR output", err=True)

    # Convert to LRC
    lrc_content = results_to_lrc(results)

    # Output
    if output:
        output.write_text(lrc_content, encoding="utf-8")
        typer.echo(f"Wrote LRC to: {output}", err=True)
    else:
        print(lrc_content)

    # Write comparison report if verified lyrics provided
    if verified_lyrics and comparison_output:
        if not verified_lyrics.exists():
            typer.echo(f"Error: Verified lyrics file not found: {verified_lyrics}", err=True)
            raise typer.Exit(1)

        # Read verified lyrics (strip whitespace, timestamps, filter empty lines)
        verified_lines = [
            _strip_timestamp(l) for l in verified_lyrics.read_text(encoding="utf-8").splitlines() if l.strip()
        ]

        # Extract output lines from results (also strip timestamps in case they're present)
        output_lines = [_strip_timestamp(text) for _, text, _, _ in results]

        # Write comparison
        generate_comparison_report(verified_lines, output_lines, comparison_output)
        typer.echo(f"Wrote comparison report to: {comparison_output}", err=True)


if __name__ == "__main__":
    app()
