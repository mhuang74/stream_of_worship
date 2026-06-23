#!/usr/bin/env python3
"""Qwen3-ASR ONNX backend POC script.

This script provides a standalone POC for the ONNX Qwen3-ASR backend,
allowing side-by-side comparison with the PyTorch backend.

Features:
- Auto-download ONNX model from HuggingFace Hub
- Text-only output (no per-character timestamps)
- Simple phrase splitting by punctuation for rough timestamps
- Full canonical_line_snap() algorithm
- Caching v2 schema support

Note: The ONNX model provides text-only output. Timestamps are estimated
by splitting transcription text and distributing evenly across audio duration.
"""

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Optional

import numpy as np
import typer

# Add parent directory to path for poc.utils import
sys.path.insert(0, str(Path(__file__).parent.parent))

# Import shared utilities
from poc.utils import extract_audio_segment, format_timestamp, resolve_song_audio_path

app = typer.Typer(help="Qwen3-ASR ONNX backend POC (text-only output)")


def download_onnx_model(cache_dir: Optional[Path] = None) -> Path:
    """Download ONNX model from HuggingFace Hub.

    Args:
        cache_dir: Optional directory to cache model

    Returns:
        Path to downloaded model directory
    """
    from huggingface_hub import snapshot_download

    if cache_dir is None:
        cache_dir = Path.home() / ".cache" / "qwen3-asr-onnx"

    model_path = os.environ.get("SOW_QWEN3_ASR_ONNX_MODEL_ROOT")
    if model_path:
        return Path(model_path).expanduser()

    typer.echo("Downloading ONNX model from HuggingFace Hub...", err=True)
    model_dir = snapshot_download(
        repo_id="Daumee/Qwen3-ASR-0.6B-ONNX-CPU",
        local_dir=str(cache_dir),
        local_dir_use_symlinks=False,
    )
    return Path(model_dir)


class OnnxAsrPipeline:
    """ONNX ASR pipeline for Qwen3-ASR.

    Adapted from Daumee's onnx_inference.py for Qwen3-ASR-ONNX-CPU.
    """

    def __init__(self, model_dir: Path):
        """Initialize ONNX ASR pipeline.

        Args:
            model_dir: Directory containing encoder/decoder ONNX models
        """
        import onnxruntime as ort
        from transformers import AutoTokenizer

        self.model_dir = model_dir

        # Load tokenizer
        typer.echo("Loading tokenizer...", err=True)
        self.tokenizer = AutoTokenizer.from_pretrained(str(model_dir))

        # Load ONNX models
        typer.echo("Loading ONNX models...", err=True)
        encoder_path = model_dir / "encoder_model.onnx"
        decoder_path = model_dir / "decoder_model.onnx"

        if not encoder_path.exists():
            raise FileNotFoundError(f"Encoder model not found: {encoder_path}")
        if not decoder_path.exists():
            raise FileNotFoundError(f"Decoder model not found: {decoder_path}")

        # Create ONNX Runtime sessions
        # Use CPU provider (the model is optimized for CPU)
        providers = ["CPUExecutionProvider"]

        self.encoder_session = ort.InferenceSession(
            str(encoder_path), providers=providers
        )
        self.decoder_session = ort.InferenceSession(
            str(decoder_path), providers=providers
        )

        typer.echo(f"ONNX models loaded. Providers: {providers}", err=True)

    def _load_audio(self, audio_path: Path) -> np.ndarray:
        """Load and preprocess audio file.

        Args:
            audio_path: Path to audio file

        Returns:
            Audio waveform as numpy array
        """
        import librosa

        # Load audio at 16kHz (expected by the model)
        audio, sr = librosa.load(str(audio_path), sr=16000, mono=True)
        return audio.astype(np.float32)

    def _encode_audio(self, audio: np.ndarray) -> np.ndarray:
        """Run encoder on audio.

        Args:
            audio: Audio waveform

        Returns:
            Encoder hidden states
        """
        # Prepare encoder inputs
        # The encoder expects mel-spectrogram features
        # For Qwen3-ASR, we need to compute mel features
        encoder_inputs = self._prepare_encoder_inputs(audio)

        # Run encoder
        outputs = self.encoder_session.run(None, encoder_inputs)
        return outputs[0]  # Hidden states

    def _prepare_encoder_inputs(self, audio: np.ndarray) -> dict:
        """Prepare inputs for encoder.

        Args:
            audio: Audio waveform

        Returns:
            Dictionary of encoder inputs
        """
        import librosa

        # Compute mel spectrogram
        # Use standard parameters for ASR
        mel_spec = librosa.feature.melspectrogram(
            y=audio,
            sr=16000,
            n_fft=400,
            hop_length=160,
            n_mels=80,
            fmin=0,
            fmax=8000,
        )

        # Convert to log scale
        log_mel = librosa.power_to_db(mel_spec, ref=np.max)

        # Normalize (assuming model expects specific normalization)
        # This is a simplified version - the actual model may expect
        # different preprocessing
        log_mel = (log_mel - log_mel.mean()) / (log_mel.std() + 1e-5)

        # Transpose to (time, mel)
        log_mel = log_mel.T.astype(np.float32)

        # Add batch dimension
        log_mel = np.expand_dims(log_mel, axis=0)

        return {"input_features": log_mel}

    def _generate_tokens(
        self, encoder_hidden_states: np.ndarray, language: str = "Chinese"
    ) -> list[int]:
        """Generate tokens using decoder.

        Args:
            encoder_hidden_states: Encoder outputs
            language: Target language

        Returns:
            List of token IDs
        """
        # Add language token to the beginning
        lang_token = f"<{language}>"
        lang_token_id = self.tokenizer.convert_tokens_to_ids(lang_token)
        if lang_token_id is None:
            # Fallback: try without brackets
            lang_token_id = self.tokenizer.convert_tokens_to_ids(language)

        # Initialize with language token
        if lang_token_id is not None:
            input_ids = [lang_token_id]
        else:
            input_ids = [self.tokenizer.pad_token_id or 0]

        max_length = 256
        eos_token_id = self.tokenizer.eos_token_id

        for _ in range(max_length):
            # Prepare decoder inputs
            input_ids_np = np.array([input_ids], dtype=np.int64)
            attention_mask = np.ones_like(input_ids_np, dtype=np.int64)

            decoder_inputs = {
                "input_ids": input_ids_np,
                "attention_mask": attention_mask,
                "encoder_hidden_states": encoder_hidden_states,
            }

            # Run decoder
            outputs = self.decoder_session.run(None, decoder_inputs)
            logits = outputs[0]  # (batch, seq_len, vocab_size)

            # Get next token
            next_token_logits = logits[0, -1, :]
            next_token_id = int(np.argmax(next_token_logits))

            # Check for EOS
            if next_token_id == eos_token_id:
                break

            input_ids.append(next_token_id)

        return input_ids

    def transcribe(
        self,
        audio_path: Path,
        language: str = "Chinese",
    ) -> dict:
        """Transcribe audio file.

        Args:
            audio_path: Path to audio file
            language: Target language

        Returns:
            Dict with 'text' and 'language' keys
        """
        # Load audio
        audio = self._load_audio(audio_path)
        audio_duration = len(audio) / 16000  # seconds

        # Encode audio
        encoder_hidden_states = self._encode_audio(audio)

        # Generate tokens
        token_ids = self._generate_tokens(encoder_hidden_states, language)

        # Decode tokens
        text = self.tokenizer.decode(token_ids, skip_special_tokens=True)

        # Create segments by splitting text and distributing timestamps
        segments = self._create_segments_from_text(text, audio_duration)

        return {
            "text": text,
            "language": language,
            "segments": segments,
            "audio_duration": audio_duration,
        }

    def _create_segments_from_text(
        self, text: str, audio_duration: float
    ) -> list[dict]:
        """Create rough segments by splitting text on punctuation.

        Since ONNX model doesn't provide timestamps, we estimate them by
        distributing segments evenly across audio duration.

        Args:
            text: Transcribed text
            audio_duration: Total audio duration in seconds

        Returns:
            List of segment dicts with estimated timestamps
        """
        # Split on Chinese and ASCII punctuation
        parts = re.split(r"([。！？\.,!?])", text)

        # Reassemble parts with their delimiters
        phrases = []
        current = ""
        for i, part in enumerate(parts):
            if not part:
                continue
            current += part
            # If this part is a delimiter or it's the last part
            if part in "。！？\.,!?" or i == len(parts) - 1:
                stripped = current.strip()
                if stripped:
                    phrases.append(stripped)
                current = ""

        if current.strip():
            phrases.append(current.strip())

        if not phrases:
            return []

        # Distribute phrases across audio duration
        # Add small padding at start and end
        start_offset = 0.5
        end_padding = 0.5
        usable_duration = max(0, audio_duration - start_offset - end_padding)

        segments = []
        n_phrases = len(phrases)

        # Calculate average duration per phrase, weighted by character count
        total_chars = sum(len(p) for p in phrases)
        if total_chars == 0:
            total_chars = n_phrases

        current_time = start_offset
        for phrase in phrases:
            # Duration proportional to character count
            char_ratio = len(phrase) / total_chars
            duration = usable_duration * char_ratio

            segments.append({
                "text": phrase,
                "start": current_time,
                "end": current_time + duration,
            })
            current_time += duration

        return segments


def transcribe_onnx(
    audio_path: Path,
    language: str = "Chinese",
    model_cache_dir: Optional[Path] = None,
) -> dict:
    """Run transcription using ONNX backend.

    Args:
        audio_path: Path to audio file
        language: Target language
        model_cache_dir: Optional directory to cache ONNX model

    Returns:
        Raw transcription result as dict
    """
    # Download and load model
    model_dir = download_onnx_model(model_cache_dir)

    # Create pipeline
    pipeline = OnnxAsrPipeline(model_dir)

    # Transcribe
    typer.echo(f"Transcribing with ONNX backend: {audio_path}", err=True)
    result = pipeline.transcribe(audio_path, language)

    return result


def compute_params_hash(params: dict) -> str:
    """Compute 8-char SHA256 prefix of canonical JSON for params."""
    import hashlib

    canonical = json.dumps(params, sort_keys=True, ensure_ascii=False)
    full_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return full_hash[:8]


def cache_file_name(
    cache_dir: Path,
    song_id: str,
    params_hash: str,
) -> Path:
    """Generate cache file name for transcription.

    Args:
        cache_dir: Cache directory
        song_id: Song identifier
        params_hash: 8-char SHA256 prefix of parameters

    Returns:
        Path to cache file
    """
    safe_song_id = song_id.replace("/", "_").replace("\\", "_")
    filename = f"onnx_{safe_song_id}_{params_hash}.json"
    return cache_dir / filename


def load_cached_transcription(cache_path: Path) -> Optional[dict]:
    """Load cached raw transcription dict.

    Args:
        cache_path: Path to cache file

    Returns:
        Raw dict from cache, or None if cache file not found/invalid
    """
    if not cache_path.exists():
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

        typer.echo(f"Loaded cached transcription from: {cache_path}", err=True)
        return cache_data
    except Exception as e:
        typer.echo(f"Warning: Cache file invalid, ignoring: {e}", err=True)
        return None


def save_cached_transcription(
    cache_path: Path,
    raw: dict,
    params: dict,
    wall_time: float,
) -> None:
    """Save raw transcription to cache.

    Args:
        cache_path: Path to cache file
        raw: Raw dict from transcription result
        params: Parameters that affect ASR
        wall_time: Wall-clock time taken
    """
    cache_path.parent.mkdir(parents=True, exist_ok=True)

    cache_data = {
        "cache_version": 2,
        "model": "Qwen3-ASR-0.6B-ONNX-CPU",
        "backend": "onnx",
        "params": params,
        "wall_time": wall_time,
        "timestamp": time.time(),
        "raw": raw,
    }

    cache_path.write_text(json.dumps(cache_data, ensure_ascii=False, indent=2), encoding="utf-8")
    typer.echo(f"Saved transcription to cache: {cache_path}", err=True)


def _is_filler(text: str) -> bool:
    """Check if text is only filler interjections."""
    filler_chars = set("嗯啊呃哦嗯哦")
    return all(c in filler_chars for c in text.strip()) if text.strip() else False


def _normalize_text(text: str) -> str:
    """Normalize text by mapping variant characters."""
    char_map = {
        "鼵": "鼓",  # U+9F35 -> U+9F13 variant
    }
    return "".join(char_map.get(c, c) for c in text)


def _text_to_pinyin(text: str) -> str:
    """Convert Chinese characters to pinyin with tone markers."""
    from pypinyin import lazy_pinyin, Style

    pinyin_list = lazy_pinyin(text, style=Style.NORMAL)
    return " ".join(pinyin_list)


def _score(
    asr_text: str, canonical_line: str, target_script: str, use_pinyin: bool = False
) -> float:
    """Score ASR text against a canonical line."""
    from rapidfuzz import fuzz
    from zhconv import convert

    asr_normalized = _normalize_text(convert(asr_text, target_script))
    canonical_normalized = _normalize_text(convert(canonical_line, target_script))

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


def _combined_score(asr: str, canonical: str, target_script: str, asr_char_count: int) -> float:
    """Score combining char and pinyin."""
    char_s = _score(asr, canonical, target_script, use_pinyin=False)
    if asr_char_count <= 8:
        pin_s = _score(asr, canonical, target_script, use_pinyin=True) * 0.9
        return max(char_s, pin_s)
    return char_s


def detect_chinese_script(text: str) -> str:
    """Detect whether Chinese text is traditional or simplified."""
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
) -> tuple[list[tuple[float, str, bool, bool]], list[dict]]:
    """Snap ASR segments to canonical lyrics using fuzzy matching.

    Args:
        segments: List of dicts with 'start', 'end', 'text' keys
        lyrics: List of canonical lyric lines
        threshold: Minimum fuzzy score to snap (0-1)

    Returns:
        Tuple of:
        - List of (start, final_text, replaced, merged) tuples
        - List of merged segment dicts
    """
    from zhconv import convert

    target_script = (
        detect_chinese_script("".join([l for l in lyrics if l.strip()])) if lyrics else "zh-hans"
    )

    canonical_lines = [l for l in lyrics if l.strip()]

    if not canonical_lines:
        return [], []

    WINDOW_SIZE = 7

    sample_text = "".join(canonical_lines)
    target_script = detect_chinese_script(sample_text)

    canonical_lines_normalized = [
        _normalize_text(convert(l, target_script)) for l in canonical_lines
    ]

    if not segments:
        return [], []

    n_segments = len(segments)
    n_canonical = len(canonical_lines)

    # Merge adjacent fragmented ASR phrases
    merged_segments = []
    i = 0

    while i < n_segments:
        seg_text = segments[i]["text"]

        if _is_filler(seg_text):
            i += 1
            continue

        should_merge = False

        if i < n_segments - 1:
            next_seg_text = segments[i + 1]["text"]
            if _is_filler(next_seg_text):
                merged_segments.append({
                    "start": segments[i]["start"],
                    "end": segments[i]["end"],
                    "text": seg_text,
                    "merged": False,
                })
                i += 2
                continue

            merged_text = seg_text + next_seg_text

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
                merged_segments.append({
                    "start": segments[i]["start"],
                    "end": segments[i + 1]["end"],
                    "text": merged_text,
                    "merged": True,
                })
                i += 2
                continue

        merged_segments.append({
            "start": segments[i]["start"],
            "end": segments[i]["end"],
            "text": seg_text,
            "merged": False,
        })
        i += 1

    # Find first content segment
    first_content_idx = 0
    while first_content_idx < len(merged_segments) and _is_filler(
        merged_segments[first_content_idx]["text"]
    ):
        first_content_idx += 1

    results = []
    cursor = 0

    # Force-anchor first 1-2 content segments
    OPENING_ANCHOR_COUNT = 2
    anchor_start = first_content_idx
    anchor_end = min(first_content_idx + OPENING_ANCHOR_COUNT, len(merged_segments), n_canonical)

    for k in range(anchor_start, anchor_end):
        if k < len(merged_segments) and k - anchor_start < n_canonical:
            canonical_text = _normalize_text(canonical_lines[k - anchor_start])
            results.append((
                merged_segments[k]["start"],
                canonical_text,
                True,
                merged_segments[k].get("merged", False),
            ))

    # Greedy walk for remaining segments
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

        scored_all = [
            _combined_score(asr_text, cl, target_script, asr_char_count)
            for cl in canonical_lines
        ]
        best_idx_all = max(range(n_canonical), key=lambda k: scored_all[k])
        best_score_all = scored_all[best_idx_all]

        selected_line = None
        selected_idx = -1
        used_window = False

        LOW_CONFIDENCE_THRESHOLD = 0.40
        seq_cursor = cursor % n_canonical

        low_confidence = best_score_all < LOW_CONFIDENCE_THRESHOLD
        AVG_LINE_DURATION = 8.0

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

    return results, merged_segments


def results_to_lrc(results: list[tuple[float, str, bool, bool]]) -> str:
    """Convert results to LRC format."""
    lines = []
    for start, text, _replaced, _merged in results:
        timestamp = format_timestamp(start)
        lines.append(f"{timestamp} {text}")
    return "\n".join(lines)


def write_diagnostic(
    segments: list[dict],
    lyrics: list[str],
    results: list[tuple[float, str, bool, bool]],
    output_path: Path,
    wall_time: float,
    merged_segments: list[dict],
) -> None:
    """Write diagnostic markdown file."""
    canonical_lines = [l for l in lyrics if l.strip()]

    lines = []
    lines.append("# Qwen3-ASR ONNX Backend Diagnostic Report\n")
    lines.append("## Summary\n\n")
    lines.append(f"ASR segments: {len(segments)}\n")
    lines.append(f"Canonical lines: {len(canonical_lines)}\n")
    lines.append(f"Output lines: {len(results)}\n")

    replaced_count = sum(1 for _, _, replaced, _ in results if replaced)
    lines.append(f"Replaced by snap: {replaced_count}\n")
    lines.append(f"Kept original: {len(results) - replaced_count}\n")
    lines.append(f"Merged segments: {sum(1 for _, _, _, merged in results if merged)}\n")

    sample_text = "".join(canonical_lines)
    target_script = detect_chinese_script(sample_text) if sample_text else "zh-hans"

    scores = []
    for seg in segments:
        asr_text = seg["text"]
        best_score = 0
        for cl in canonical_lines:
            s = _score(asr_text, cl, target_script)
            if s > best_score:
                best_score = s
        scores.append(best_score)

    if scores:
        lines.append(f"Average snap score: {sum(scores) / len(scores):.2f}\n")

    if segments:
        duration = segments[-1]["end"] - segments[0]["start"]
        lines.append(f"Audio duration: {duration:.2f}s\n")
        if duration > 0:
            lines.append(f"Segments per second: {len(segments) / duration:.2f}\n")
            lines.append(f"Wall-clock time: {wall_time:.2f}s\n")
            lines.append(f"Real-time factor: {wall_time / duration:.2f}x\n")

    try:
        import psutil
        process = psutil.Process()
        memory_mb = process.memory_info().rss / 1024 / 1024
        lines.append(f"Peak RAM usage: ~{memory_mb:.1f} MB\n")
    except Exception:
        pass

    lines.append("\n## Segment Details\n\n")
    lines.append("| Start | End | ASR Text | Matched Canonical | Score | Replaced | Merged |\n")
    lines.append("|-------|-----|----------|-------------------|-------|----------|--------|\n")

    for seg, (start, final_text, replaced, merged) in zip(merged_segments, results):
        asr_text = seg["text"]
        best_score = 0
        best_line = ""
        for cl in canonical_lines:
            s = _score(asr_text, cl, target_script)
            if s > best_score:
                best_score = s
                best_line = cl

        merged_mark = "Yes" if merged else ""
        lines.append(
            f"| {seg['start']:6.2f} | {seg['end']:4.2f} | {asr_text[:30]:30s} | "
            f"{best_line[:30]:30s} | {best_score:5.2f} | "
            f"{'Yes' if replaced else 'No':6s} | {merged_mark:6s} |\n"
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
    snap: bool = typer.Option(
        True, "--snap/--no-snap", help="Enable canonical-line fuzzy snap"
    ),
    snap_threshold: float = typer.Option(
        0.60, "--snap-threshold", help="Minimum fuzzy score to snap (0-1)"
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
    force_rerun: bool = typer.Option(
        False, "--force-rerun", help="Ignore cache and rerun transcription"
    ),
    model_cache_dir: Optional[Path] = typer.Option(
        None,
        "--model-cache-dir",
        help="Directory to cache ONNX model (default: ~/.cache/qwen3-asr-onnx)",
    ),
):
    """Run Qwen3-ASR ONNX transcription on a song and output LRC format.

    This script uses the ONNX Qwen3-ASR backend which provides text-only
    output (no per-character timestamps). Timestamps are estimated by
    distributing transcription phrases across audio duration.

    Transcription results are cached and reused by default. Use --force-rerun
to ignore the cache.
    """
    # Resolve inputs
    audio_path, lyrics = resolve_song_audio_path(song_id, use_vocals=use_vocals)

    # Override with provided vocal stem if specified
    if vocal_stem:
        if not vocal_stem.exists():
            typer.echo(f"Error: Vocal stem file not found: {vocal_stem}", err=True)
            raise typer.Exit(1)
        audio_path = vocal_stem
        typer.echo(f"Using provided vocal stem: {audio_path}", err=True)

    # Lyrics are optional
    if lyrics is None:
        if lyrics_context:
            typer.echo("Warning: No lyrics from catalog; disabling context biasing.", err=True)
            lyrics_context = False
        if snap:
            typer.echo("Warning: No lyrics from catalog; disabling canonical-line snap.", err=True)
            snap = False
        lyrics_text = ""
    else:
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
    cache_path = cache_file_name(cache_dir, song_id, params_hash)

    raw: Optional[dict] = None
    wall_time = 0.0
    used_cache = False

    # Check for cached transcription
    if not force_rerun:
        cached_data = load_cached_transcription(cache_path)
        if cached_data is not None:
            raw = cached_data.get("raw")
            used_cache = True
            typer.echo("Using cached transcription", err=True)

    # Run transcription if not using cache
    if raw is None:
        # Extract segment if needed
        transcribe_path = audio_path
        segment_path: Optional[Path] = None
        if start > 0 or effective_end is not None:
            typer.echo(f"Extracting audio segment: {start}s to {effective_end or 'end'}s", err=True)
            segment_path = extract_audio_segment(audio_path, start, effective_end or 3600)
            transcribe_path = segment_path

        wall_time_start = time.time()

        try:
            # Note: ONNX model doesn't support context biasing in the same way
            # as PyTorch, but we can log if it was requested
            if lyrics_context:
                typer.echo(
                    "Note: ONNX backend doesn't support context biasing; continuing without it.",
                    err=True,
                )

            # Transcribe with ONNX backend
            raw = transcribe_onnx(
                audio_path=transcribe_path,
                language="Chinese",
                model_cache_dir=model_cache_dir,
            )

            wall_time = time.time() - wall_time_start
            typer.echo(f"Transcription completed in {wall_time:.2f}s", err=True)

            # Save to cache
            save_cached_transcription(cache_path, raw, params, wall_time)

        finally:
            if segment_path and segment_path.exists():
                segment_path.unlink()

    # Write raw output if requested
    if save_raw:
        save_raw.mkdir(parents=True, exist_ok=True)
        raw_file = save_raw / "asr_raw_onnx.json"
        raw_file.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        typer.echo(f"Saved raw ASR result to: {raw_file}", err=True)

    # Extract segments from raw dict
    segments = raw.get("segments", [])

    if not segments:
        typer.echo("Error: No segments extracted from ASR result", err=True)
        raise typer.Exit(1)

    typer.echo(f"Extracted {len(segments)} segments", err=True)

    # Process segments
    if snap:
        results, merged_segments = canonical_line_snap(
            segments,
            lyrics,
            threshold=snap_threshold,
        )
        replaced_count = sum(1 for _, _, replaced, _ in results if replaced)
        typer.echo(
            f"Canonical-line snap: {replaced_count}/{len(results)} segments replaced",
            err=True,
        )

        # Write diagnostic if requested
        if save_raw:
            save_raw.mkdir(parents=True, exist_ok=True)
            diag_file = save_raw / "diagnostic_onnx.md"
            write_diagnostic(
                segments, lyrics, results, diag_file, wall_time, merged_segments
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


if __name__ == "__main__":
    app()
