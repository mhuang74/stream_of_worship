#!/usr/bin/env python3
"""Qwen3-ASR + MVSEP + ForcedAligner V2 LRC generation script.

Combines Qwen3-ASR transcription with Qwen3-ForcedAligner to produce
high-quality LRC files. Uses ASR for the text (which reflects the actual
performance structure with repeated sections), then uses the ForcedAligner
to produce accurate timestamps by aligning that text against the audio.
Chunk-based alignment eliminates the 5-minute limit.

Pipeline:
  Audio -> MVSEP vocal extraction -> Qwen3-ASR (full song) -> Chunk planning
  -> ForcedAligner per chunk -> Merge chunks -> Optional canonical snap -> LRC

Note: Many functions are copied from gen_lrc_qwen3_asr_mvsep.py and
gen_lrc_qwen3_force_align.py. They should eventually be refactored into
poc/utils.py or a shared module. This is deferred per POC convention.
"""

import json
import os
import re
import sys
from pathlib import Path
from typing import Optional

import typer

sys.path.insert(0, str(Path(__file__).parent.parent))
from poc.utils import extract_audio_segment, format_timestamp

app = typer.Typer(help="Qwen3-ASR + MVSEP + ForcedAligner V2 LRC generation")

CHUNK_DURATION = 300.0

MAX_LOOKAHEAD_WORDS = 30
MIN_LINE_SCORE = 0.40
GAP_THRESHOLD = 1.0
SHORT_FRAG_CHARS = 3
BACKTRACK_WINDOW = 3
MAX_BACKTRACK_ALT = 3
BACKTRACK_GAIN_THRESHOLD = 0.10
WRAP_MIN_SCORE = 0.50
RECONSTRUCTION_FALLBACK_THRESHOLD = 0.40

REGION_URL = {
    "intl": "https://dashscope-intl.aliyuncs.com/api/v1",
    "cn": "https://dashscope.aliyuncs.com/api/v1",
    "us": "https://dashscope-us.aliyuncs.com/api/v1",
}


# ---------------------------------------------------------------------------
# Copied from gen_lrc_qwen3_asr_mvsep.py (TODO: refactor to shared module)
# ---------------------------------------------------------------------------


def _upload_to_oss(audio_path: Path, model: str, region: str) -> str:
    """Upload a local audio file to DashScope OSS and return the oss:// URL."""
    import dashscope
    from dashscope.utils.oss_utils import OssUtils

    dashscope.base_http_api_url = REGION_URL[region]

    result = OssUtils.upload(
        api_key=os.environ["DASHSCOPE_API_KEY"],
        file_path=str(audio_path.resolve()),
        model=model,
    )
    if not result:
        typer.echo("Error: Failed to upload audio to OSS", err=True)
        raise typer.Exit(1)

    oss_url = result[0] if isinstance(result, tuple) else result
    typer.echo(f"Uploaded to OSS: {oss_url}", err=True)
    return oss_url


def call_qwen3_asr(
    audio_path: Path,
    model: str = "qwen3-asr-flash",
    region: str = "intl",
    context: Optional[str] = None,
) -> dict:
    """Call Qwen3-ASR API on an audio file.

    Args:
        audio_path: Path to audio file
        model: Model name (qwen3-asr-flash or qwen3-asr-flash-filetrans)
        region: Region (intl, cn, us)
        context: Optional context string for biasing

    Returns:
        Raw API response as dict
    """
    import dashscope

    dashscope.base_http_api_url = REGION_URL[region]

    if "filetrans" in model:
        return _call_qwen3_asr_filetrans(audio_path, model, region, context)

    messages = [
        {"role": "user", "content": [{"audio": f"file://{audio_path.resolve()}"}]},
    ]

    if context:
        messages.insert(0, {"role": "system", "content": [{"text": context}]})

    typer.echo(f"Calling Qwen3-ASR ({model}) in {region} region...", err=True)
    if context:
        typer.echo(f"Using context biasing ({len(context)} chars)", err=True)

    asr_options = {"enable_itn": False, "enable_words": True, "language": "zh"}
    debug_payload = {
        "model": model,
        "messages": messages,
        "result_format": "message",
        "asr_options": asr_options,
    }
    typer.echo(
        f"ASR request payload:\n{json.dumps(debug_payload, ensure_ascii=False, indent=2)}",
        err=True,
    )

    resp = dashscope.MultiModalConversation.call(
        api_key=os.environ["DASHSCOPE_API_KEY"],
        model=model,
        messages=messages,
        result_format="message",
        asr_options=asr_options,
    )

    if resp.status_code != 200:
        typer.echo(f"API error: {resp.status_code} - {resp.message}", err=True)
        raise typer.Exit(1)

    return resp.output


def _call_qwen3_asr_filetrans(
    audio_path: Path,
    model: str,
    region: str,
    context: Optional[str] = None,
) -> dict:
    """Call Qwen3-ASR-Flash-FileTrans API (async file transcription)."""
    import dashscope
    from dashscope.audio.qwen_asr import QwenTranscription

    dashscope.base_http_api_url = REGION_URL[region]

    typer.echo("Uploading audio for filetrans...", err=True)
    file_url = _upload_to_oss(audio_path, model, region)

    typer.echo(f"Calling Qwen3-ASR-FileTrans ({model}) in {region} region...", err=True)

    if context:
        typer.echo(
            "Note: filetrans model does not support system-message context biasing; "
            "context will be used for vocabulary hint only if vocabulary_id is set",
            err=True,
        )

    filetrans_headers = {"X-DashScope-OssResourceResolve": "enable"}
    debug_payload = {
        "model": model,
        "file_url": file_url,
        "headers": filetrans_headers,
        "enable_words": True,
    }
    typer.echo(
        f"FileTrans request payload:\n{json.dumps(debug_payload, ensure_ascii=False, indent=2)}",
        err=True,
    )

    task_resp = QwenTranscription.async_call(
        model=model,
        file_url=file_url,
        api_key=os.environ["DASHSCOPE_API_KEY"],
        headers=filetrans_headers,
        enable_words=True,
    )

    if task_resp.status_code != 200:
        typer.echo(
            f"FileTrans submit error: {task_resp.status_code} - {task_resp.message}", err=True
        )
        raise typer.Exit(1)

    task_id = task_resp.output.get("task_id")
    if not task_id:
        typer.echo("Error: No task_id in filetrans response", err=True)
        raise typer.Exit(1)

    typer.echo(f"FileTrans task submitted: {task_id}", err=True)

    import time

    while True:
        result_resp = QwenTranscription.wait(
            task=task_id,
            api_key=os.environ["DASHSCOPE_API_KEY"],
        )
        status = result_resp.output.get("task_status", "")
        if status == "SUCCEEDED":
            return result_resp.output
        elif status == "FAILED":
            typer.echo(f"FileTrans task failed: {result_resp.output}", err=True)
            raise typer.Exit(1)
        typer.echo(f"FileTrans task status: {status}, waiting...", err=True)
        time.sleep(5)


def extract_segments(response: dict) -> list[tuple[float, float, str]]:
    """Extract sentence-level segments from Qwen3-ASR response.

    Handles both qwen3-asr-flash (MultiModalConversation) and
    qwen3-asr-flash-filetrans (QwenTranscription) response formats.

    Args:
        response: Raw API response dict

    Returns:
        List of (start, end, text) tuples
    """
    segments = []

    if "result" in response and "transcription_url" in response.get("result", {}):
        return _extract_segments_filetrans(response)

    try:
        content = response.get("choices", [{}])[0].get("message", {}).get("content", [])

        for item in content:
            if item.get("type") == "audio_transcription":
                sentences = item.get("audio_transcription_results", {}).get("sentences", [])
                for sentence in sentences:
                    start = sentence.get("begin_time", 0) / 1000.0
                    end = sentence.get("end_time", 0) / 1000.0
                    text = sentence.get("text", "").strip()
                    if text:
                        segments.append((start, end, text))
    except Exception as e:
        typer.echo(f"Error parsing segments: {e}", err=True)
        typer.echo(f"Response keys: {list(response.keys())}", err=True)
        raise

    if not segments:
        typer.echo("Warning: No segments extracted from response", err=True)

    return segments


def _extract_segments_filetrans(response: dict) -> list[tuple[float, float, str]]:
    """Extract segments from QwenTranscription (filetrans) response."""
    import requests

    segments = []
    result = response.get("result", {})
    transcription_url = result.get("transcription_url")
    if not transcription_url:
        typer.echo("Warning: No transcription_url in result", err=True)
        return segments

    typer.echo("Fetching transcription from URL...", err=True)
    try:
        tr_resp = requests.get(transcription_url, timeout=60)
        tr_resp.raise_for_status()
        tr_data = tr_resp.json()
    except Exception as e:
        typer.echo(f"Error fetching transcription: {e}", err=True)
        return segments

    transcripts = tr_data.get("transcripts", [])
    for transcript in transcripts:
        sentences = transcript.get("sentences", [])
        for sentence in sentences:
            start = sentence.get("begin_time", 0) / 1000.0
            end = sentence.get("end_time", 0) / 1000.0
            text = sentence.get("text", "").strip()
            if text:
                segments.append((start, end, text))

    if not segments:
        typer.echo("Warning: No segments extracted from filetrans response", err=True)

    return segments


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


def results_to_lrc(results: list[tuple[float, str, bool]]) -> str:
    """Convert results to LRC format."""
    lines = []
    for start, text, _replaced in results:
        timestamp = format_timestamp(start)
        lines.append(f"{timestamp} {text}")
    return "\n".join(lines)


def write_diagnostic(
    segments: list[tuple[float, float, str]],
    lyrics: list[str],
    results: list[tuple[float, str, bool]],
    output_path: Path,
    asr_score: Optional[tuple[float, str]] = None,
    chunk_stats: Optional[list[dict]] = None,
) -> None:
    """Write diagnostic markdown file (extended for V2).

    Args:
        segments: List of (start, end, asr_text) tuples
        lyrics: List of canonical lyric lines
        results: List of (start, final_text, replaced) tuples
        output_path: Path to write diagnostic.md
        asr_score: Optional (score, label) from verify_asr_quality
        chunk_stats: Optional list of per-chunk alignment stats dicts
    """
    from rapidfuzz import fuzz
    from zhconv import convert

    canonical_lines = [line for line in lyrics if line.strip()]

    if not canonical_lines:
        return

    sample_text = "".join(canonical_lines)
    target_script = detect_chinese_script(sample_text) if sample_text else "zh-hans"
    canonical_lines_normalized = [convert(line, target_script) for line in canonical_lines]

    out = []
    out.append("# Qwen3-ASR + MVSEP + ForcedAligner V2 Diagnostic Report\n")
    out.append("## Summary\n\n")
    out.append(f"ASR segments: {len(segments)}\n")
    out.append(f"Canonical lines: {len(canonical_lines)}\n")
    out.append(f"Output lines: {len(results)}\n")

    if asr_score is not None:
        score, label = asr_score
        out.append(f"ASR verification score: {score:.2f} ({label})\n")

    replaced_count = sum(1 for _, _, replaced in results if replaced)
    kept_count = len(results) - replaced_count
    out.append(f"Replaced by snap: {replaced_count}\n")
    out.append(f"Kept original: {kept_count}\n")

    scores = []
    for start, final_text, replaced in results:
        final_normalized = convert(final_text, target_script)
        scored = [
            fuzz.token_set_ratio(final_normalized, canonical_lines_normalized[i]) / 100.0
            for i in range(len(canonical_lines))
        ]
        best_score = max(scored)
        scores.append(best_score)

    if scores:
        avg_score = sum(scores) / len(scores)
        out.append(f"Average snap score: {avg_score:.2f}\n")

    if segments:
        duration = segments[-1][1] - segments[0][0]
        out.append(f"Audio duration: {duration:.2f}s\n")
        out.append(f"Segments per second: {len(segments) / duration:.2f}\n")

    if chunk_stats:
        out.append("\n## Chunk Alignment Stats\n\n")
        out.append("| Chunk | Start | End | Segments | Status |\n")
        out.append("|-------|-------|-----|----------|--------|\n")
        for stat in chunk_stats:
            out.append(
                f"| {stat.get('index', '?')} | {stat.get('start', 0):.1f} | "
                f"{stat.get('end', 0):.1f} | {stat.get('segments', 0)} | "
                f"{stat.get('status', 'unknown')} |\n"
            )

    out.append("\n## Segment Details\n\n")
    out.append("| Start | Final Text | Matched Canonical | Score | Replaced |\n")
    out.append("|-------|------------|-------------------|-------|----------|\n")

    for start, final_text, replaced in results:
        final_normalized = convert(final_text, target_script)
        scored = [
            (
                canonical_lines[i],
                fuzz.token_set_ratio(final_normalized, canonical_lines_normalized[i]) / 100.0,
            )
            for i in range(len(canonical_lines))
        ]
        best_line, best_score = max(scored, key=lambda x: x[1])

        out.append(
            f"| {start:6.2f} | {final_text[:30]:30s} | "
            f"{best_line[:30]:30s} | {best_score:5.2f} | "
            f"{'Yes' if replaced else 'No'} |\n"
        )

    output_path.write_text("".join(out))


def _find_local_dry_vocals(song_cache_dir: Path) -> Optional[Path]:
    """Find locally cached dry vocals from MVSEP output directories."""
    stage2_dir = song_cache_dir / "stage2_dereverb"
    if stage2_dir.is_dir():
        for f in sorted(stage2_dir.iterdir()):
            if f.is_file() and f.suffix.lower() in (".flac", ".wav", ".mp3"):
                name_lower = f.name.lower()
                if "noreverb" in name_lower or "no_reverb" in name_lower:
                    return f

        flac_files = [
            f
            for f in stage2_dir.iterdir()
            if f.is_file() and f.suffix.lower() in (".flac", ".wav", ".mp3")
        ]
        if flac_files:
            return sorted(flac_files)[0]

    stage1_dir = song_cache_dir / "stage1_vocal_separation"
    if stage1_dir.is_dir():
        for f in sorted(stage1_dir.iterdir()):
            if f.is_file() and f.suffix.lower() in (".flac", ".wav", ".mp3"):
                name_lower = f.name.lower()
                if "vocal" in name_lower:
                    return f

    return None


def resolve_song_audio_path_mvsep(
    song_id: str,
    mvsep_api_token: str,
    mvsep_vocals: bool = True,
    stage1_sep_type: int = 48,
    stage1_add_opt1: int = 11,
    stage2_sep_type: int = 22,
    stage2_add_opt1: int = 0,
    stage2_add_opt2: int = 1,
    output_format: int = 2,
    timeout: float = 900.0,
    reuse_stage1: bool = False,
) -> tuple[Path, Optional[list[str]]]:
    """Resolve a song ID to a local audio path using MVSEP for vocal extraction."""
    from sow_lab_app.config import AppConfig
    from stream_of_worship.db.connection import ConnectionProvider
    from stream_of_worship.db.app.read_client import ReadOnlyClient
    from sow_lab_app.services.catalog import CatalogService
    from sow_lab_app.services.asset_cache import AssetCache
    from stream_of_worship.admin.services.r2 import R2Client
    from poc.gen_clean_vocal_stem_mvsep import extract_vocals_two_stage_mvsep

    input_path = Path(song_id).expanduser()
    lyrics: Optional[list[str]] = None

    if input_path.exists():
        return input_path, lyrics

    try:
        config = AppConfig.load()
    except FileNotFoundError:
        typer.echo(
            "Error: Config file not found. Please run 'sow-app' first to create config.",
            err=True,
        )
        raise typer.Exit(1)

    provider = ConnectionProvider(config.get_connection_url())
    db_client = ReadOnlyClient(provider)
    catalog = CatalogService(db_client)

    try:
        song_with_recording = catalog.get_song_with_recording(song_id)
        if not song_with_recording:
            typer.echo(f"Error: Song not found: {song_id}", err=True)
            raise typer.Exit(1)

        if not song_with_recording.recording:
            typer.echo(f"Error: No recording found for song: {song_id}", err=True)
            raise typer.Exit(1)

        song = song_with_recording.song
        recording = song_with_recording.recording
        hash_prefix = recording.hash_prefix
        lyrics = song.lyrics_list

        typer.echo(f"Song: {song.title}", err=True)
        typer.echo(f"Recording: {hash_prefix}", err=True)

        try:
            r2_client = R2Client(
                bucket=config.r2_bucket,
                endpoint_url=config.r2_endpoint_url,
                region=config.r2_region,
            )
        except ValueError as e:
            typer.echo(f"Error: R2 credentials not configured: {e}", err=True)
            raise typer.Exit(1)

        cache = AssetCache(cache_dir=config.cache_dir, r2_client=r2_client)
        audio_path: Optional[Path] = None

        if mvsep_vocals:
            clean_vocals_path = cache.cache_dir / hash_prefix / "clean_vocals.flac"
            if clean_vocals_path.exists():
                audio_path = clean_vocals_path
                typer.echo(f"Using cached clean vocal stem: {audio_path}", err=True)

            if audio_path is None:
                for stem_name in ["vocals_dry", "vocals_clean", "vocals"]:
                    stem_path = cache.get_stem_path(hash_prefix, stem_name)
                    if stem_path.exists():
                        audio_path = stem_path
                        typer.echo(f"Using cached {stem_name} stem: {audio_path}", err=True)
                        break
                    downloaded = cache.download_stem(hash_prefix, stem_name)
                    if downloaded:
                        audio_path = downloaded
                        typer.echo(f"Downloaded {stem_name} stem: {audio_path}", err=True)
                        break

            if audio_path is None:
                song_cache_dir = cache.cache_dir / hash_prefix
                audio_path = _find_local_dry_vocals(song_cache_dir)
                if audio_path:
                    typer.echo(f"Using locally cached dry vocals: {audio_path}", err=True)

        if audio_path is None and mvsep_vocals:
            main_audio_path = cache.get_audio_path(hash_prefix)
            if not main_audio_path.exists():
                typer.echo("Downloading main audio...", err=True)
                main_audio_path = cache.download_audio(hash_prefix)
                if main_audio_path:
                    typer.echo(f"Downloaded main audio: {main_audio_path}", err=True)

            if main_audio_path and main_audio_path.exists():
                output_dir = cache.cache_dir / hash_prefix
                typer.echo("Generating clean vocal stem via MVSEP cloud API...", err=True)
                typer.echo(
                    f"  Stage 1: sep_type={stage1_sep_type}, add_opt1={stage1_add_opt1}",
                    err=True,
                )
                typer.echo(
                    f"  Stage 2: sep_type={stage2_sep_type}, add_opt1={stage2_add_opt1}, "
                    f"add_opt2={stage2_add_opt2}",
                    err=True,
                )
                results = extract_vocals_two_stage_mvsep(
                    input_path=main_audio_path,
                    output_dir=output_dir,
                    api_token=mvsep_api_token,
                    vocal_model=stage1_add_opt1,
                    dereverb_model=stage2_add_opt1,
                    output_format=output_format,
                    reuse_stage1=reuse_stage1,
                    timeout=timeout,
                )
                dry_vocal_path = results["stages"]["stage2"].get("dry_vocals_file")
                if dry_vocal_path:
                    audio_path = Path(dry_vocal_path)
                    typer.echo(f"Generated clean vocal stem: {audio_path}", err=True)

        if audio_path is None:
            typer.echo("Error: Could not find or download audio", err=True)
            raise typer.Exit(1)

        if not audio_path.exists():
            typer.echo(f"Error: Audio file not found: {audio_path}", err=True)
            raise typer.Exit(1)

        return audio_path, lyrics
    finally:
        db_client.close()


# ---------------------------------------------------------------------------
# Copied from gen_lrc_qwen3_force_align.py (TODO: refactor to shared module)
# ---------------------------------------------------------------------------


def get_audio_duration(audio_path: Path) -> float:
    """Get audio duration in seconds."""
    from pydub import AudioSegment

    audio = AudioSegment.from_file(str(audio_path))
    return len(audio) / 1000.0


def get_model_cache_dir() -> Path:
    """Get the default HuggingFace cache directory."""
    import os

    if "HF_HOME" in os.environ:
        return Path(os.environ["HF_HOME"])
    if "XDG_CACHE_HOME" in os.environ:
        return Path(os.environ["XDG_CACHE_HOME"]) / "huggingface"

    return Path.home() / ".cache" / "huggingface"


def is_model_cached(model_name: str = "Qwen/Qwen3-ForcedAligner-0.6B") -> bool:
    """Check if the model is already cached locally."""
    cache_dir = get_model_cache_dir()
    model_path = cache_dir / "hub"
    if not model_path.exists():
        return False

    safe_model_name = model_name.replace("/", "--")
    model_dirs = list(model_path.glob(f"models--{safe_model_name}"))
    return len(model_dirs) > 0 and any(d.is_dir() for d in model_dirs)


def map_segments_to_lines(
    segments: list[tuple[float, float, str]],
    original_lines: list[str],
) -> list[tuple[float, float, str]]:
    """Map character-level alignment segments back to original lyric lines."""
    import re

    aligned_text = ""
    segment_positions = []

    for seg_start, seg_end, seg_text in segments:
        start_char = len(aligned_text)
        aligned_text += seg_text
        end_char = len(aligned_text)
        segment_positions.append((start_char, end_char, seg_start, seg_end, seg_text))

    def normalize(text: str) -> str:
        return re.sub(r"[\s。，！？、；：\"''" "''" "''（）【】「」『』 ]+", "", text)

    aligned_normalized = normalize(aligned_text)

    line_alignments = []
    current_pos = 0

    for line in original_lines:
        normalized_line = normalize(line)
        if not normalized_line:
            prev_end = line_alignments[-1][1] if line_alignments else 0.0
            line_alignments.append((prev_end, prev_end, line))
            continue

        line_start = aligned_normalized.find(normalized_line, current_pos)

        if line_start == -1:
            if current_pos >= len(aligned_normalized):
                prev_end = line_alignments[-1][1] if line_alignments else 0.0
                line_alignments.append((prev_end, prev_end, line))
            else:
                ratio = current_pos / len(aligned_normalized)
                est_start = segments[0][0] if segments else 0.0
                est_end = segments[-1][1] if segments else 0.0
                duration = est_end - est_start
                line_alignments.append(
                    (est_start + ratio * duration, est_start + ratio * duration, line)
                )
            continue

        line_end = line_start + len(normalized_line)
        current_pos = line_end

        overlapping_segments = []
        for (
            seg_start_char,
            seg_end_char,
            seg_start_time,
            seg_end_time,
            seg_text,
        ) in segment_positions:
            if seg_end_char > line_start and seg_start_char < line_end:
                overlapping_segments.append((seg_start_time, seg_end_time))

        if overlapping_segments:
            start_time = min(s[0] for s in overlapping_segments)
            end_time = max(s[1] for s in overlapping_segments)
            line_alignments.append((start_time, end_time, line))
        else:
            ratio = line_start / len(aligned_normalized) if aligned_normalized else 0
            est_start = segments[0][0] if segments else 0.0
            est_end = segments[-1][1] if segments else 0.0
            duration = est_end - est_start
            line_alignments.append(
                (
                    est_start + ratio * duration,
                    est_start + ratio * duration + (duration / len(original_lines)),
                    line,
                )
            )

    return line_alignments


def align_lyrics(
    audio_path: Path,
    lyrics_lines: list[str],
    language: str = "Chinese",
    device: str = "auto",
    dtype: str = "float32",
    model_cache_dir: Optional[Path] = None,
) -> list[tuple[float, float, str]]:
    """Align lyrics to audio using Qwen3ForcedAligner.

    Note: This function enforces a 5-minute audio limit. For longer audio,
    use the chunk-based approach in this script's main flow.

    Args:
        audio_path: Path to audio file (must be ≤ 300 seconds)
        lyrics_lines: Lines of text to align
        language: Language hint
        device: Device (auto/mps/cuda/cpu)
        dtype: Data type (bfloat16/float16/float32)
        model_cache_dir: Custom HuggingFace cache directory

    Returns:
        List of (start_time, end_time, text) tuples

    Raises:
        ValueError: If audio exceeds 5 minutes or no lyrics provided
        RuntimeError: If alignment fails
    """
    import time

    import torch
    from qwen_asr import Qwen3ForcedAligner

    audio_duration = get_audio_duration(audio_path)
    if audio_duration > 300:
        raise ValueError(
            f"Audio duration ({audio_duration:.1f}s) exceeds 5 minute limit of Qwen3ForcedAligner"
        )

    if not lyrics_lines:
        raise ValueError("Lyrics are required for forced alignment")

    lyrics_text = "\n".join(lyrics_lines)

    if device == "auto":
        if torch.backends.mps.is_available():
            device = "mps"
        elif torch.cuda.is_available():
            device = "cuda"
        else:
            device = "cpu"

    dtype_map = {
        "bfloat16": torch.bfloat16,
        "float16": torch.float16,
        "float32": torch.float32,
    }
    torch_dtype = dtype_map.get(dtype, torch.float32)

    model_name = "Qwen/Qwen3-ForcedAligner-0.6B"
    cached = is_model_cached(model_name)
    cache_location = model_cache_dir or get_model_cache_dir()

    if cached:
        typer.echo(f"Loading Qwen3ForcedAligner from cache: {cache_location}", err=True)
    else:
        typer.echo(f"Downloading Qwen3ForcedAligner (one-time) to: {cache_location}", err=True)

    typer.echo(f"Device: {device}, dtype: {dtype}", err=True)
    load_start = time.time()

    if model_cache_dir:
        os.environ["HF_HOME"] = str(model_cache_dir)

    model = Qwen3ForcedAligner.from_pretrained(
        model_name,
        dtype=torch_dtype,
        device_map=device,
    )

    load_elapsed = time.time() - load_start
    typer.echo(f"Model loaded in {load_elapsed:.2f}s", err=True)

    typer.echo(f"Aligning lyrics to audio ({audio_duration:.1f}s)...", err=True)
    align_start = time.time()

    results = model.align(
        audio=str(audio_path),
        text=lyrics_text,
        language=language,
    )

    align_elapsed = time.time() - align_start
    typer.echo(f"Alignment completed in {align_elapsed:.2f}s", err=True)

    raw_segments = []
    for segment_list in results:
        for segment in segment_list:
            text = segment.text.strip()
            if text:
                raw_segments.append((segment.start_time, segment.end_time, text))

    typer.echo(f"Mapping {len(raw_segments)} segments to {len(lyrics_lines)} lines...", err=True)

    line_alignments = map_segments_to_lines(raw_segments, lyrics_lines)

    return line_alignments


# ---------------------------------------------------------------------------
# New V2 functions
# ---------------------------------------------------------------------------


def extract_word_timestamps(response: dict) -> list[tuple[float, float, str]]:
    """Extract word-level timestamps from Qwen3-ASR response.

    Strictly validates that the response contains word-level data.
    The ASR API is called with enable_words: True, which should produce
    a 'words' field within each sentence object.

    Expected schema (per sentence):
        words: list of dicts with begin_time, end_time, text

    Args:
        response: Raw API response dict

    Returns:
        List of (start, end, text) tuples at word granularity

    Raises:
        ValueError: If word-level data is not present in the response
    """
    words = []

    if "result" in response and "transcription_url" in response.get("result", {}):
        return _extract_word_timestamps_filetrans(response)

    try:
        content = response.get("choices", [{}])[0].get("message", {}).get("content", [])

        for item in content:
            if item.get("type") == "audio_transcription":
                sentences = item.get("audio_transcription_results", {}).get("sentences", [])
                for sentence in sentences:
                    sentence_words = sentence.get("words")
                    if sentence_words is None:
                        raise ValueError(
                            "Word-level data not found in ASR response. "
                            "The 'words' field is missing from sentence objects. "
                            "Ensure enable_words: True is set in the ASR request."
                        )
                    for w in sentence_words:
                        begin = w.get("begin_time")
                        end = w.get("end_time")
                        text = w.get("text", "").strip() + w.get("punctuation", "")
                        if begin is None or end is None:
                            raise ValueError(f"Word entry missing begin_time/end_time: {w}")
                        if text:
                            words.append((begin / 1000.0, end / 1000.0, text))
    except ValueError:
        raise
    except Exception as e:
        raise ValueError(f"Error parsing word-level timestamps: {e}") from e

    if not words:
        raise ValueError(
            "No word-level timestamps extracted from ASR response. "
            "The response may not contain word-level data."
        )

    return words


def _extract_word_timestamps_filetrans(response: dict) -> list[tuple[float, float, str]]:
    """Extract word-level timestamps from filetrans response."""
    import requests

    words = []
    result = response.get("result", {})
    transcription_url = result.get("transcription_url")
    if not transcription_url:
        raise ValueError("No transcription_url in filetrans result")

    typer.echo("Fetching transcription for word timestamps...", err=True)
    try:
        tr_resp = requests.get(transcription_url, timeout=60)
        tr_resp.raise_for_status()
        tr_data = tr_resp.json()
    except Exception as e:
        raise ValueError(f"Error fetching transcription: {e}") from e

    transcripts = tr_data.get("transcripts", [])
    for transcript in transcripts:
        sentences = transcript.get("sentences", [])
        for sentence in sentences:
            sentence_words = sentence.get("words")
            if sentence_words is None:
                raise ValueError(
                    "Word-level data not found in filetrans response. "
                    "The 'words' field is missing from sentence objects."
                )
            for w in sentence_words:
                begin = w.get("begin_time")
                end = w.get("end_time")
                text = w.get("text", "").strip() + w.get("punctuation", "")
                if begin is None or end is None:
                    raise ValueError(f"Word entry missing begin_time/end_time: {w}")
                if text:
                    words.append((begin / 1000.0, end / 1000.0, text))

    if not words:
        raise ValueError("No word-level timestamps extracted from filetrans response")

    return words


def extract_asr_text(response: dict) -> str:
    """Extract concatenated text from ASR response (all sentence.text joined by newline).

    Args:
        response: Raw API response dict

    Returns:
        Single string with all sentence texts joined by newline
    """
    texts = []

    if "result" in response and "transcription_url" in response.get("result", {}):
        import requests

        result = response.get("result", {})
        transcription_url = result.get("transcription_url")
        if transcription_url:
            try:
                tr_resp = requests.get(transcription_url, timeout=60)
                tr_resp.raise_for_status()
                tr_data = tr_resp.json()
                for transcript in tr_data.get("transcripts", []):
                    for sentence in transcript.get("sentences", []):
                        text = sentence.get("text", "").strip()
                        if text:
                            texts.append(text)
            except Exception as e:
                typer.echo(f"Error fetching transcription for text: {e}", err=True)
        return "\n".join(texts)

    try:
        content = response.get("choices", [{}])[0].get("message", {}).get("content", [])
        for item in content:
            if item.get("type") == "audio_transcription":
                sentences = item.get("audio_transcription_results", {}).get("sentences", [])
                for sentence in sentences:
                    text = sentence.get("text", "").strip()
                    if text:
                        texts.append(text)
    except Exception as e:
        typer.echo(f"Error extracting ASR text: {e}", err=True)

    return "\n".join(texts)


def plan_chunks(audio_duration: float, overlap: float = 60.0) -> list[tuple[float, float]]:
    """Compute chunk boundaries for chunk-based forced alignment.

    Args:
        audio_duration: Total audio duration in seconds
        overlap: Overlap between chunks in seconds (default: 60)

    Returns:
        List of (chunk_start, chunk_end) tuples.
        Single chunk if audio ≤ 300s.
    """
    if audio_duration <= CHUNK_DURATION:
        return [(0.0, audio_duration)]

    step = CHUNK_DURATION - overlap
    chunks = []
    pos = 0.0
    while pos < audio_duration:
        chunk_end = min(pos + CHUNK_DURATION, audio_duration)
        chunks.append((pos, chunk_end))
        if chunk_end >= audio_duration:
            break
        pos += step

    return chunks


def _strip_cjk_spaces(text: str) -> str:
    return re.sub(r"([\u4e00-\u9fff\u3400-\u4dbf])\s+(?=[\u4e00-\u9fff\u3400-\u4dbf])", r"\1", text)


def _normalize_for_matching(text: str) -> str:
    import re as _re

    from zhconv import convert

    text = convert(text, "zh-hans")
    text = _re.sub(r"[^\u4e00-\u9fff\u3400-\u4dbf a-zA-Z]", "", text)
    text = _re.sub(r"\s+", " ", text).strip()
    return text


def _count_cjk_chars(text: str) -> int:
    return sum(1 for c in text if "\u4e00" <= c <= "\u9fff" or "\u3400" <= c <= "\u4dbf")


def _partition_words_to_chunks(
    asr_words: list[tuple[float, float, str]],
    chunks: list[tuple[float, float]],
) -> dict[int, list[tuple[float, float, str]]]:
    chunk_words: dict[int, list[tuple[float, float, str]]] = {i: [] for i in range(len(chunks))}

    for word in asr_words:
        word_start = word[0]
        for i, (chunk_start, chunk_end) in enumerate(chunks):
            is_last = i == len(chunks) - 1
            in_chunk = (
                (chunk_start <= word_start <= chunk_end)
                if is_last
                else (chunk_start <= word_start < chunk_end)
            )
            if in_chunk:
                chunk_words[i].append(word)
                break

    return chunk_words


def reconstruct_lines_from_words(
    asr_words: list[tuple[float, float, str]],
    canonical_lines: list[str],
    start_canonical_idx: int = 0,
) -> tuple[list[tuple[int, list[int]]], int]:
    from rapidfuzz import fuzz

    if not asr_words or not canonical_lines:
        return [], start_canonical_idx

    asr_word_norms = [_normalize_for_matching(w[2]) for w in asr_words]
    canonical_norm_lines = [_normalize_for_matching(line) for line in canonical_lines]

    line_assignments: list[tuple[int, list[int]]] = []
    word_cursor = 0
    canonical_cursor = start_canonical_idx

    while word_cursor < len(asr_words) and canonical_cursor < len(canonical_norm_lines):
        canonical_idx = canonical_cursor
        canonical_norm = canonical_norm_lines[canonical_idx]
        use_partial = _count_cjk_chars(canonical_norm) <= SHORT_FRAG_CHARS

        best_j = -1
        best_score = -1.0
        candidates: list[tuple[float, int]] = []

        max_j = min(word_cursor + MAX_LOOKAHEAD_WORDS, len(asr_words))
        for j in range(word_cursor, max_j):
            candidate_text = "".join(asr_word_norms[word_cursor : j + 1])
            if not candidate_text:
                continue

            if use_partial:
                score = fuzz.partial_ratio(candidate_text, canonical_norm) / 100.0
            else:
                score = fuzz.token_set_ratio(candidate_text, canonical_norm) / 100.0

            if score >= MIN_LINE_SCORE:
                candidates.append((score, j))
            if score > best_score:
                best_score = score
                best_j = j

        if best_score < MIN_LINE_SCORE:
            canonical_cursor += 1
            typer.echo(
                f"Skipping canonical line {canonical_idx}: "
                f"'{canonical_lines[canonical_idx][:30]}' (best score={best_score:.2f})",
                err=True,
            )
            continue

        chosen_j = best_j

        if canonical_cursor + 1 < len(canonical_norm_lines):
            next_scores: list[float] = []
            for check_ci in range(
                canonical_cursor + 1,
                min(canonical_cursor + 1 + BACKTRACK_WINDOW, len(canonical_norm_lines)),
            ):
                remaining_text = "".join(asr_word_norms[chosen_j + 1 :])
                if not remaining_text:
                    next_scores.append(0.0)
                    continue
                next_norm = canonical_norm_lines[check_ci]
                next_use_partial = _count_cjk_chars(next_norm) <= SHORT_FRAG_CHARS
                if next_use_partial:
                    ns = fuzz.partial_ratio(remaining_text, next_norm) / 100.0
                else:
                    ns = fuzz.token_set_ratio(remaining_text, next_norm) / 100.0
                next_scores.append(ns)

            original_next_score = max(next_scores) if next_scores else 0.0

            candidates.sort(key=lambda x: x[0], reverse=True)
            for alt_score, alt_j in candidates[:MAX_BACKTRACK_ALT]:
                if alt_j == chosen_j:
                    continue
                alt_remaining = "".join(asr_word_norms[alt_j + 1 :])
                if not alt_remaining:
                    continue
                alt_next_scores: list[float] = []
                for check_ci in range(
                    canonical_cursor + 1,
                    min(
                        canonical_cursor + 1 + BACKTRACK_WINDOW,
                        len(canonical_norm_lines),
                    ),
                ):
                    next_norm = canonical_norm_lines[check_ci]
                    next_use_partial = _count_cjk_chars(next_norm) <= SHORT_FRAG_CHARS
                    if next_use_partial:
                        ans = fuzz.partial_ratio(alt_remaining, next_norm) / 100.0
                    else:
                        ans = fuzz.token_set_ratio(alt_remaining, next_norm) / 100.0
                    alt_next_scores.append(ans)

                alt_best_next = max(alt_next_scores) if alt_next_scores else 0.0
                if alt_best_next >= original_next_score + BACKTRACK_GAIN_THRESHOLD:
                    typer.echo(
                        f"Backtracking: line {canonical_idx} end moved from "
                        f"word {chosen_j} to {alt_j} "
                        f"(next score {alt_best_next:.2f} vs {original_next_score:.2f})",
                        err=True,
                    )
                    chosen_j = alt_j
                    break

        line_assignments.append((canonical_idx, list(range(word_cursor, chosen_j + 1))))
        word_cursor = chosen_j + 1
        canonical_cursor += 1

    if word_cursor < len(asr_words):
        remaining_norm = "".join(asr_word_norms[word_cursor:])
        if remaining_norm.strip():
            best_wrap_idx = -1
            best_wrap_score = 0.0

            for ci in range(len(canonical_norm_lines)):
                cn = canonical_norm_lines[ci]
                if not cn:
                    continue
                ws = fuzz.partial_ratio(remaining_norm, cn) / 100.0
                if ws > best_wrap_score:
                    best_wrap_score = ws
                    best_wrap_idx = ci

            if best_wrap_score >= WRAP_MIN_SCORE and best_wrap_idx >= 0:
                typer.echo(
                    f"Smart wrap-around: restarting from canonical line {best_wrap_idx} "
                    f"(score={best_wrap_score:.2f})",
                    err=True,
                )
                canonical_cursor = best_wrap_idx

                while word_cursor < len(asr_words) and canonical_cursor < len(canonical_norm_lines):
                    canonical_idx = canonical_cursor
                    canonical_norm = canonical_norm_lines[canonical_idx]
                    use_partial = _count_cjk_chars(canonical_norm) <= SHORT_FRAG_CHARS

                    best_j = -1
                    best_score = -1.0

                    max_j = min(word_cursor + MAX_LOOKAHEAD_WORDS, len(asr_words))
                    for j in range(word_cursor, max_j):
                        candidate_text = "".join(asr_word_norms[word_cursor : j + 1])
                        if not candidate_text:
                            continue
                        if use_partial:
                            score = fuzz.partial_ratio(candidate_text, canonical_norm) / 100.0
                        else:
                            score = fuzz.token_set_ratio(candidate_text, canonical_norm) / 100.0
                        if score > best_score:
                            best_score = score
                            best_j = j

                    if best_score < MIN_LINE_SCORE:
                        canonical_cursor += 1
                        continue

                    line_assignments.append((canonical_idx, list(range(word_cursor, best_j + 1))))
                    word_cursor = best_j + 1
                    canonical_cursor += 1

    if word_cursor < len(asr_words):
        current_group = [word_cursor]
        for wi in range(word_cursor + 1, len(asr_words)):
            prev_end = asr_words[wi - 1][1]
            curr_start = asr_words[wi][0]
            if curr_start - prev_end > GAP_THRESHOLD:
                line_assignments.append((-1, list(current_group)))
                current_group = [wi]
            else:
                current_group.append(wi)
        if current_group:
            line_assignments.append((-1, list(current_group)))

    return line_assignments, canonical_cursor


def build_aligned_text(
    asr_words: list[tuple[float, float, str]],
    line_assignments: list[tuple[int, list[int]]],
    canonical_lines: list[str],
) -> list[str]:
    lines = []
    for canonical_idx, word_indices in line_assignments:
        if canonical_idx >= 0:
            lines.append(canonical_lines[canonical_idx])
        else:
            raw = " ".join(asr_words[i][2] for i in word_indices)
            lines.append(_strip_cjk_spaces(raw))
    return lines


def _reconstruction_quality(
    line_assignments: list[tuple[int, list[int]]],
    total_words: int,
    total_canonical_lines: int,
) -> float:
    matched_words = sum(len(indices) for idx, indices in line_assignments if idx >= 0)
    matched_canonical = len({idx for idx, _ in line_assignments if idx >= 0})
    word_fraction = matched_words / total_words if total_words > 0 else 0
    canonical_fraction = (
        matched_canonical / total_canonical_lines if total_canonical_lines > 0 else 0
    )
    return (word_fraction + canonical_fraction) / 2


def _sentence_fallback_for_chunk(
    segments: list[tuple[float, float, str]],
    chunk_start: float,
    chunk_end: float,
) -> str:
    chunk_sents = [text for start, end, text in segments if chunk_start <= start <= chunk_end]
    return "\n".join(chunk_sents)


def assign_text_to_chunks(
    asr_words: list[tuple[float, float, str]],
    chunks: list[tuple[float, float]],
) -> dict[int, str]:
    """Use word-level ASR timestamps to determine which text falls within each chunk.

    A word is assigned to a chunk if its start time falls within the chunk's
    time range (inclusive of start, exclusive of end, except the last chunk
    which is inclusive of both).

    Args:
        asr_words: List of (start, end, text) tuples at word granularity
        chunks: List of (chunk_start, chunk_end) tuples

    Returns:
        Dict mapping chunk index to concatenated ASR text for that chunk
    """
    chunk_texts: dict[int, list[str]] = {i: [] for i in range(len(chunks))}

    for word_start, word_end, word_text in asr_words:
        for i, (chunk_start, chunk_end) in enumerate(chunks):
            is_last_chunk = i == len(chunks) - 1
            if is_last_chunk:
                in_chunk = chunk_start <= word_start <= chunk_end
            else:
                in_chunk = chunk_start <= word_start < chunk_end
            if in_chunk:
                chunk_texts[i].append(word_text)

    return {i: " ".join(texts) for i, texts in chunk_texts.items()}


def align_chunk(
    audio_path: Path,
    chunk_start: float,
    chunk_end: float,
    chunk_text: str,
    asr_words: list[tuple[float, float, str]],
    language: str = "Chinese",
    device: str = "auto",
    dtype: str = "float32",
    model_cache_dir: Optional[Path] = None,
) -> list[tuple[float, float, str]]:
    """Force-align a single chunk of audio against its ASR text.

    Extracts the audio segment for the chunk, runs forced alignment,
    and offsets all timestamps by chunk_start. Falls back to word-level
    ASR timestamps if alignment fails.

    Args:
        audio_path: Path to full audio file
        chunk_start: Start time of chunk in seconds
        chunk_end: End time of chunk in seconds
        chunk_text: ASR text for this chunk
        asr_words: Word-level ASR timestamps (for fallback)
        language: Language hint
        device: Device for forced aligner
        dtype: Data type for forced aligner
        model_cache_dir: Custom HuggingFace cache directory

    Returns:
        List of (start, end, text) tuples with global timestamps
    """
    chunk_lines = [line for line in chunk_text.split("\n") if line.strip()]
    if not chunk_lines:
        chunk_lines = [chunk_text] if chunk_text.strip() else []

    if not chunk_lines:
        typer.echo(
            f"Warning: No text for chunk {chunk_start:.1f}-{chunk_end:.1f}s, "
            f"using word-level ASR fallback",
            err=True,
        )
        return _word_fallback_for_chunk(asr_words, chunk_start, chunk_end)

    segment_path: Optional[Path] = None
    try:
        typer.echo(f"Extracting audio segment: {chunk_start:.1f}-{chunk_end:.1f}s", err=True)
        segment_path = extract_audio_segment(audio_path, chunk_start, chunk_end)

        try:
            aligned = align_lyrics(
                audio_path=segment_path,
                lyrics_lines=chunk_lines,
                language=language,
                device=device,
                dtype=dtype,
                model_cache_dir=model_cache_dir,
            )

            offset_aligned = [
                (start + chunk_start, end + chunk_start, text) for start, end, text in aligned
            ]
            return offset_aligned

        except Exception as e:
            typer.echo(
                f"Warning: Forced alignment failed for chunk "
                f"{chunk_start:.1f}-{chunk_end:.1f}s: {e}. "
                f"Falling back to word-level ASR timestamps.",
                err=True,
            )
            return _word_fallback_for_chunk(asr_words, chunk_start, chunk_end)

    finally:
        if segment_path and segment_path.exists():
            segment_path.unlink()


def _word_fallback_for_chunk(
    asr_words: list[tuple[float, float, str]],
    chunk_start: float,
    chunk_end: float,
) -> list[tuple[float, float, str]]:
    """Fall back to word-level ASR timestamps for a chunk.

    Groups words into sentence-like segments based on time gaps.

    Args:
        asr_words: Word-level ASR timestamps
        chunk_start: Start time of chunk
        chunk_end: End time of chunk

    Returns:
        List of (start, end, text) tuples
    """
    chunk_words = [(s, e, t) for s, e, t in asr_words if chunk_start <= s <= chunk_end]

    if not chunk_words:
        return []

    segments = []
    current_words = [chunk_words[0]]
    gap_threshold = 1.0

    for i in range(1, len(chunk_words)):
        prev_end = chunk_words[i - 1][1]
        curr_start = chunk_words[i][0]
        if curr_start - prev_end > gap_threshold:
            seg_text = _strip_cjk_spaces(" ".join(w[2] for w in current_words))
            segments.append((current_words[0][0], current_words[-1][1], seg_text))
            current_words = [chunk_words[i]]
        else:
            current_words.append(chunk_words[i])

    if current_words:
        seg_text = _strip_cjk_spaces(" ".join(w[2] for w in current_words))
        segments.append((current_words[0][0], current_words[-1][1], seg_text))

    return segments


def merge_chunks(
    chunk_results: list[list[tuple[float, float, str]]],
    chunks: list[tuple[float, float]],
) -> list[tuple[float, float, str]]:
    """Merge force-aligned results from all chunks.

    For single-chunk songs, returns results directly.
    For multi-chunk songs, deduplicates at overlap boundaries by preferring
    alignment from the chunk whose timestamps are farther from its edges
    (since alignment quality may degrade near chunk boundaries).

    Args:
        chunk_results: List of aligned segments per chunk (global timestamps)
        chunks: List of (chunk_start, chunk_end) tuples

    Returns:
        Full-song list of (start, end, text) tuples, sorted by start time
    """
    if len(chunks) == 1:
        return chunk_results[0] if chunk_results else []

    tagged: list[tuple[float, float, str, int]] = []
    for chunk_idx, segments in enumerate(chunk_results):
        chunk_start, chunk_end = chunks[chunk_idx]
        for start, end, text in segments:
            tagged.append((start, end, text, chunk_idx))

    tagged.sort(key=lambda x: x[0])

    merged = []
    overlap_regions = _compute_overlap_regions(chunks)

    for start, end, text, chunk_idx in tagged:
        in_overlap = any(ov_start <= start <= ov_end for ov_start, ov_end in overlap_regions)

        if not in_overlap:
            merged.append((start, end, text))
            continue

        chunk_start, chunk_end = chunks[chunk_idx]
        dist_from_edge = min(start - chunk_start, chunk_end - end)

        conflict_idx = None
        for mi, (ms, me, mt) in enumerate(merged):
            if abs(ms - start) < 0.5 and abs(me - end) < 0.5:
                conflict_idx = mi
                break

        if conflict_idx is None:
            merged.append((start, end, text))
        else:
            existing_start, existing_end, existing_text = merged[conflict_idx]
            existing_chunk_idx = None
            for ci, (cs, ce) in enumerate(chunks):
                if cs <= existing_start <= ce:
                    existing_chunk_idx = ci
                    break

            if existing_chunk_idx is not None:
                ecs, ece = chunks[existing_chunk_idx]
                existing_dist = min(existing_start - ecs, ece - existing_end)
                if dist_from_edge > existing_dist:
                    merged[conflict_idx] = (start, end, text)
            else:
                merged.append((start, end, text))

    merged.sort(key=lambda x: x[0])
    return merged


def _compute_overlap_regions(
    chunks: list[tuple[float, float]],
) -> list[tuple[float, float]]:
    """Compute overlap regions between consecutive chunks.

    Args:
        chunks: List of (chunk_start, chunk_end) tuples

    Returns:
        List of (overlap_start, overlap_end) tuples
    """
    regions = []
    for i in range(len(chunks) - 1):
        current_end = chunks[i][1]
        next_start = chunks[i + 1][0]
        if next_start < current_end:
            regions.append((next_start, current_end))
    return regions


def sequential_canonical_snap(
    segments: list[tuple[float, float, str]],
    lyrics: list[str],
    threshold: float = 0.60,
) -> list[tuple[float, str, bool]]:
    """Snap ASR segments to canonical lyrics using sequential fuzzy matching.

    Maintains a cursor through canonical lyrics, advancing forward.
    For each force-aligned line, searches forward from the current cursor.
    If no good match is found forward, wraps around to the beginning.
    If still no match, keeps original ASR text without advancing cursor.

    Args:
        segments: List of (start, end, text) tuples from force alignment
        lyrics: List of canonical lyric lines
        threshold: Minimum fuzzy score to snap (0-1)

    Returns:
        List of (start, final_text, replaced) tuples
    """
    from rapidfuzz import fuzz
    from zhconv import convert

    canonical_lines = [line for line in lyrics if line.strip()]
    results = []

    if not canonical_lines:
        for start, _end, text in segments:
            results.append((start, text, False))
        return results

    sample_text = "".join(canonical_lines)
    target_script = detect_chinese_script(sample_text)
    canonical_normalized = [convert(line, target_script) for line in canonical_lines]

    cursor = 0

    for start, _end, asr_text in segments:
        asr_normalized = convert(asr_text, target_script)

        best_line = None
        best_score = 0.0
        best_idx = -1

        for i in range(cursor, len(canonical_lines)):
            score = fuzz.token_set_ratio(asr_normalized, canonical_normalized[i]) / 100.0
            if score > best_score:
                best_score = score
                best_line = canonical_lines[i]
                best_idx = i

        if best_score >= threshold and best_line is not None:
            results.append((start, best_line, True))
            cursor = best_idx + 1
            continue

        if cursor > 0:
            best_wrap_line = None
            best_wrap_score = 0.0
            best_wrap_idx = -1

            for i in range(len(canonical_lines)):
                score = fuzz.token_set_ratio(asr_normalized, canonical_normalized[i]) / 100.0
                if score > best_wrap_score:
                    best_wrap_score = score
                    best_wrap_line = canonical_lines[i]
                    best_wrap_idx = i

            if best_wrap_score >= threshold and best_wrap_line is not None:
                results.append((start, best_wrap_line, True))
                cursor = best_wrap_idx + 1
                continue

        results.append((start, asr_text, False))

    return results


def verify_asr_quality(
    asr_text: str,
    canonical_lyrics: list[str],
) -> tuple[float, str]:
    """Compute overall fuzzy match score between ASR text and canonical lyrics.

    This is a diagnostic-only function that does not affect pipeline behavior.

    Args:
        asr_text: Concatenated ASR text
        canonical_lyrics: List of canonical lyric lines

    Returns:
        Tuple of (score, label) where label is "high"/"moderate"/"low"
    """
    from rapidfuzz import fuzz
    from zhconv import convert

    canonical_text = "\n".join(line for line in canonical_lyrics if line.strip())
    if not canonical_text:
        return (0.0, "low")

    sample_text = "".join(line for line in canonical_lyrics if line.strip())
    target_script = detect_chinese_script(sample_text)

    asr_normalized = convert(asr_text, target_script)
    canonical_normalized = convert(canonical_text, target_script)

    score = fuzz.token_set_ratio(asr_normalized, canonical_normalized) / 100.0

    if score >= 0.8:
        label = "high"
    elif score >= 0.5:
        label = "moderate"
    else:
        label = "low"

    return (score, label)


# ---------------------------------------------------------------------------
# Main CLI
# ---------------------------------------------------------------------------


@app.command()
def main(
    song_id: str = typer.Argument(
        ...,
        help="Song ID (e.g., wo_yao_quan_xin_zan_mei_244) or path to audio file",
    ),
    mvsep_vocals: bool = typer.Option(
        True, "--mvsep-vocals/--no-mvsep-vocals", help="Use MVSEP vocal extraction"
    ),
    mvsep_api_token: Optional[str] = typer.Option(
        None, "--mvsep-api-token", help="MVSEP API token (or set MVSEP_API_KEY env var)"
    ),
    stage1_sep_type: int = typer.Option(
        48, "--stage1-sep-type", help="MVSEP Stage 1 sep_type (default: 48)"
    ),
    stage1_add_opt1: int = typer.Option(
        11, "--stage1-add-opt1", help="MVSEP Stage 1 model variant (default: 11)"
    ),
    stage2_sep_type: int = typer.Option(
        22, "--stage2-sep-type", help="MVSEP Stage 2 sep_type (default: 22)"
    ),
    stage2_add_opt1: int = typer.Option(
        0, "--stage2-add-opt1", help="MVSEP Stage 2 model variant (default: 0)"
    ),
    stage2_add_opt2: int = typer.Option(
        1, "--stage2-add-opt2", help="MVSEP Stage 2 add_opt2 (default: 1)"
    ),
    output_format: int = typer.Option(
        2, "--output-format", help="MVSEP output format (default: 2)"
    ),
    timeout: float = typer.Option(
        900.0, "--timeout", help="Max seconds per MVSEP stage (default: 900)"
    ),
    reuse_stage1: bool = typer.Option(
        False, "--reuse-stage1", help="Reuse existing Stage 1 vocals"
    ),
    model: str = typer.Option(
        "qwen3-asr-flash",
        "--model",
        help="ASR model (qwen3-asr-flash or qwen3-asr-flash-filetrans)",
    ),
    region: str = typer.Option("intl", "--region", help="Region (intl, cn, us)"),
    lyrics_context: bool = typer.Option(
        True,
        "--lyrics-context/--no-lyrics-context",
        help="Enable context biasing with lyrics",
    ),
    device: str = typer.Option(
        "auto", "--device", help="Device for forced aligner (auto/mps/cuda/cpu)"
    ),
    dtype: str = typer.Option("float32", "--dtype", help="Data type (bfloat16/float16/float32)"),
    model_cache_dir: Optional[Path] = typer.Option(
        None, "--model-cache-dir", help="Custom HuggingFace cache directory"
    ),
    language: str = typer.Option("Chinese", "--language", help="Language hint"),
    chunk_overlap: float = typer.Option(
        60.0, "--chunk-overlap", help="Overlap between chunks in seconds (default: 60)"
    ),
    snap: bool = typer.Option(
        True, "--snap/--no-snap", help="Enable canonical-line sequential snap"
    ),
    snap_threshold: float = typer.Option(
        0.60, "--snap-threshold", help="Minimum fuzzy score to snap (0-1)"
    ),
    output: Optional[Path] = typer.Option(
        None, "--output", "-o", help="Output file (default: stdout)"
    ),
    save_raw: Optional[Path] = typer.Option(
        None, "--save-raw", help="Directory to save raw ASR response + diagnostics"
    ),
    lyrics_file: Optional[Path] = typer.Option(
        None, "--lyrics-file", help="Path to lyrics file (overrides DB lyrics)"
    ),
):
    """Generate LRC using Qwen3-ASR + MVSEP + ForcedAligner V2.

    Combines ASR transcription with forced alignment for high-quality LRC
    output. Chunk-based alignment handles songs of any duration.
    """
    if "DASHSCOPE_API_KEY" not in os.environ:
        typer.echo("Error: DASHSCOPE_API_KEY environment variable not set", err=True)
        raise typer.Exit(1)

    if mvsep_vocals:
        mvsep_token = mvsep_api_token or os.environ.get("MVSEP_API_KEY")
        if not mvsep_token:
            typer.echo(
                "Error: MVSEP API token required. Use --mvsep-api-token or "
                "set MVSEP_API_KEY env var.",
                err=True,
            )
            raise typer.Exit(1)
    else:
        mvsep_token = None

    # Step 1: Resolve audio + lyrics
    audio_path, db_lyrics = resolve_song_audio_path_mvsep(
        song_id,
        mvsep_api_token=mvsep_token,
        mvsep_vocals=mvsep_vocals,
        stage1_sep_type=stage1_sep_type,
        stage1_add_opt1=stage1_add_opt1,
        stage2_sep_type=stage2_sep_type,
        stage2_add_opt1=stage2_add_opt1,
        stage2_add_opt2=stage2_add_opt2,
        output_format=output_format,
        timeout=timeout,
        reuse_stage1=reuse_stage1,
    )

    lyrics: Optional[list[str]] = db_lyrics

    if lyrics_file:
        if not lyrics_file.exists():
            typer.echo(f"Error: Lyrics file not found: {lyrics_file}", err=True)
            raise typer.Exit(1)
        try:
            lyrics_text = lyrics_file.read_text(encoding="utf-8")
            lyrics = [line.rstrip() for line in lyrics_text.splitlines()]
            while lyrics and not lyrics[-1]:
                lyrics.pop()
            typer.echo(f"Using lyrics from file: {lyrics_file}", err=True)
        except Exception as e:
            typer.echo(f"Error reading lyrics file: {e}", err=True)
            raise typer.Exit(1)

    if snap and not lyrics:
        typer.echo(
            "Error: Lyrics are required for canonical snap. "
            "Provide --lyrics-file or use a song ID with DB lyrics.",
            err=True,
        )
        raise typer.Exit(1)

    if lyrics_context and not lyrics:
        typer.echo(
            "Warning: No lyrics available for context biasing; proceeding without context.",
            err=True,
        )

    # Step 2: Run ASR on full song
    context = None
    if lyrics_context and lyrics:
        lyrics_text = "\n".join(lyrics)
        context = (
            "This is a Chinese Christian worship song. "
            "Use the following canonical lyrics as term/phrase reference for recognition. "
            "The performance may repeat verses and choruses; transcribe what is actually sung.\n\n"
            + lyrics_text
        )
        if len(context) > 10000:
            context = context[:10000]
            typer.echo("Context truncated to 10k chars", err=True)

    response = call_qwen3_asr(
        audio_path=audio_path,
        model=model,
        region=region,
        context=context,
    )

    if save_raw:
        save_raw.mkdir(parents=True, exist_ok=True)
        raw_file = save_raw / "asr_raw.json"
        raw_file.write_text(json.dumps(response, ensure_ascii=False, indent=2))
        typer.echo(f"Saved raw ASR response to: {raw_file}", err=True)

    segments = extract_segments(response)

    if not segments:
        typer.echo("Error: No segments extracted from ASR response", err=True)
        raise typer.Exit(1)

    typer.echo(f"Extracted {len(segments)} ASR segments", err=True)

    asr_words = None
    try:
        asr_words = extract_word_timestamps(response)
        typer.echo(f"Extracted {len(asr_words)} word-level timestamps", err=True)
    except ValueError as e:
        typer.echo(f"Warning: Could not extract word-level timestamps: {e}", err=True)
        typer.echo("Falling back to sentence-level timestamps for chunk assignment", err=True)

    asr_text = extract_asr_text(response)

    # Step 3: ASR verification (diagnostic only)
    asr_score: Optional[tuple[float, str]] = None
    if lyrics:
        asr_score = verify_asr_quality(asr_text, lyrics)
        score, label = asr_score
        typer.echo(f"ASR verification: {label} confidence (score={score:.2f})", err=True)

    # Step 4: Plan chunks
    audio_duration = get_audio_duration(audio_path)
    chunks = plan_chunks(audio_duration, overlap=chunk_overlap)

    typer.echo(f"Audio duration: {audio_duration:.1f}s", err=True)
    typer.echo(f"Planned {len(chunks)} chunk(s)", err=True)
    for i, (cs, ce) in enumerate(chunks):
        typer.echo(f"  Chunk {i}: {cs:.1f}s - {ce:.1f}s", err=True)

    # Step 5: Force-align each chunk
    chunk_results: list[list[tuple[float, float, str]]] = []
    chunk_stats: list[dict] = []

    used_canonical_reconstruction = False

    if len(chunks) == 1:
        if lyrics and asr_words:
            line_assignments, _ = reconstruct_lines_from_words(asr_words, lyrics)
            quality = _reconstruction_quality(line_assignments, len(asr_words), len(lyrics))

            if quality >= RECONSTRUCTION_FALLBACK_THRESHOLD:
                chunk_lines = build_aligned_text(asr_words, line_assignments, lyrics)
                used_canonical_reconstruction = True
                typer.echo(
                    f"Reconstructed {len(chunk_lines)} lines from {len(asr_words)} words "
                    f"using {len(lyrics)} canonical lines (quality={quality:.2f})",
                    err=True,
                )
            else:
                typer.echo(
                    f"Reconstruction quality {quality:.2f} below threshold; "
                    f"falling back to ASR sentence text",
                    err=True,
                )
                chunk_lines = [line for line in asr_text.split("\n") if line.strip()]
                if not chunk_lines:
                    chunk_lines = [asr_text] if asr_text.strip() else []
        else:
            chunk_text = asr_text
            chunk_lines = [line for line in chunk_text.split("\n") if line.strip()]
            if not chunk_lines:
                chunk_lines = [chunk_text] if chunk_text.strip() else []

        if chunk_lines:
            try:
                aligned = align_lyrics(
                    audio_path=audio_path,
                    lyrics_lines=chunk_lines,
                    language=language,
                    device=device,
                    dtype=dtype,
                    model_cache_dir=model_cache_dir,
                )
                chunk_results.append(aligned)
                chunk_stats.append(
                    {
                        "index": 0,
                        "start": chunks[0][0],
                        "end": chunks[0][1],
                        "segments": len(aligned),
                        "status": "aligned",
                    }
                )
            except Exception as e:
                typer.echo(
                    f"Warning: Forced alignment failed for single chunk: {e}. "
                    f"Falling back to ASR sentence-level output.",
                    err=True,
                )
                chunk_results.append(segments)
                chunk_stats.append(
                    {
                        "index": 0,
                        "start": chunks[0][0],
                        "end": chunks[0][1],
                        "segments": len(segments),
                        "status": "asr_fallback",
                    }
                )
        else:
            chunk_results.append(segments)
            chunk_stats.append(
                {
                    "index": 0,
                    "start": chunks[0][0],
                    "end": chunks[0][1],
                    "segments": len(segments),
                    "status": "asr_fallback_no_text",
                }
            )
    else:
        if asr_words is None:
            typer.echo(
                "Warning: No word-level timestamps for multi-chunk assignment; "
                "using sentence-level timestamps (less precise)",
                err=True,
            )
            asr_words_fallback = [(s, e, t) for s, e, t in segments]
        else:
            asr_words_fallback = asr_words

        chunk_word_map = _partition_words_to_chunks(asr_words_fallback, chunks)
        next_canonical_idx = 0

        for i, (chunk_start, chunk_end) in enumerate(chunks):
            chunk_words = chunk_word_map.get(i, [])

            if chunk_words and lyrics:
                line_assignments, next_canonical_idx = reconstruct_lines_from_words(
                    chunk_words,
                    lyrics,
                    start_canonical_idx=next_canonical_idx,
                )
                quality = _reconstruction_quality(
                    line_assignments,
                    len(chunk_words),
                    len(lyrics),
                )

                if quality >= RECONSTRUCTION_FALLBACK_THRESHOLD:
                    chunk_lines = build_aligned_text(chunk_words, line_assignments, lyrics)
                    chunk_text = "\n".join(chunk_lines)
                    used_canonical_reconstruction = True
                    n_lines = len(chunk_lines)
                else:
                    typer.echo(
                        f"Chunk {i}: reconstruction quality {quality:.2f} below threshold; "
                        f"falling back to sentence-based assignment",
                        err=True,
                    )
                    chunk_text = _sentence_fallback_for_chunk(segments, chunk_start, chunk_end)
                    n_lines = chunk_text.count("\n") + 1 if chunk_text else 0
            elif chunk_words:
                chunk_text = " ".join(w[2] for w in chunk_words)
                n_lines = 0
            else:
                chunk_text = ""
                n_lines = 0

            typer.echo(
                f"Processing chunk {i}: {chunk_start:.1f}-{chunk_end:.1f}s "
                f"({len(chunk_words)} words -> {n_lines} lines, "
                f"canonical_start={next_canonical_idx})",
                err=True,
            )

            try:
                result = align_chunk(
                    audio_path=audio_path,
                    chunk_start=chunk_start,
                    chunk_end=chunk_end,
                    chunk_text=chunk_text,
                    asr_words=asr_words_fallback,
                    language=language,
                    device=device,
                    dtype=dtype,
                    model_cache_dir=model_cache_dir,
                )
                chunk_results.append(result)
                chunk_stats.append(
                    {
                        "index": i,
                        "start": chunk_start,
                        "end": chunk_end,
                        "segments": len(result),
                        "status": "aligned",
                    }
                )
            except Exception as e:
                typer.echo(
                    f"Warning: Chunk {i} alignment failed: {e}. " f"Using word-level ASR fallback.",
                    err=True,
                )
                fallback = _word_fallback_for_chunk(asr_words_fallback, chunk_start, chunk_end)
                if not fallback:
                    fallback = [(s, e, t) for s, e, t in segments if chunk_start <= s <= chunk_end]
                chunk_results.append(fallback)
                chunk_stats.append(
                    {
                        "index": i,
                        "start": chunk_start,
                        "end": chunk_end,
                        "segments": len(fallback),
                        "status": "word_fallback",
                    }
                )

    # Step 6: Merge chunks
    merged = merge_chunks(chunk_results, chunks)
    typer.echo(f"Merged into {len(merged)} segments", err=True)

    # Check if all chunks fell back to ASR
    all_fallback = all(stat.get("status", "").endswith("fallback") for stat in chunk_stats)
    if all_fallback and len(chunks) > 1:
        typer.echo(
            "Warning: All chunks fell back to ASR timestamps. "
            "Using full ASR sentence-level output.",
            err=True,
        )
        merged = segments

    # Step 7: Optional sequential canonical snap
    if snap and lyrics:
        if used_canonical_reconstruction:
            typer.echo(
                "Canonical text used for alignment; snap step skipped (already canonical)",
                err=True,
            )
            results = [(start, text, True) for start, _end, text in merged]
        else:
            results = sequential_canonical_snap(merged, lyrics, threshold=snap_threshold)
            replaced_count = sum(1 for _, _, replaced in results if replaced)
            typer.echo(
                f"Sequential canonical snap: {replaced_count}/{len(results)} segments replaced",
                err=True,
            )
    else:
        results = [(start, text, False) for start, _end, text in merged]
        if not snap:
            typer.echo("Snap disabled, using force-aligned text", err=True)

    # Step 8: Output LRC
    lrc_content = results_to_lrc(results)

    if output:
        if output.is_dir():
            output = output / f"{song_id}.lrc"
        output.write_text(lrc_content, encoding="utf-8")
        typer.echo(f"Wrote LRC to: {output}", err=True)
    else:
        print(lrc_content)

    # Write diagnostic
    if save_raw:
        diag_file = save_raw / "diagnostic.md"
        write_diagnostic(
            segments=segments,
            lyrics=lyrics or [],
            results=results,
            output_path=diag_file,
            asr_score=asr_score,
            chunk_stats=chunk_stats,
        )
        typer.echo(f"Saved diagnostic report to: {diag_file}", err=True)


if __name__ == "__main__":
    app()
