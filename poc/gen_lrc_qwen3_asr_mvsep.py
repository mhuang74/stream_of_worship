#!/usr/bin/env python3
"""Qwen3-ASR-Flash transcription POC script with MVSEP cloud vocal extraction.

Uses Alibaba DashScope's Qwen3-ASR-Flash API for transcription with context biasing
and canonical-line fuzzy snap to produce LRC files.

Vocal extraction uses MVSEP cloud API (MelBand Roformer + Reverb Removal) instead
of local models. Falls back to cached vocals if available.

This script follows the same conventions as gen_lrc_qwen3_asr.py for easy A/B testing.
"""

import json
import os
import sys
from pathlib import Path
from typing import Optional

import typer

sys.path.insert(0, str(Path(__file__).parent.parent))

from poc.utils import extract_audio_segment, format_timestamp

app = typer.Typer(help="Qwen3-ASR-Flash transcription POC with MVSEP vocal extraction")

REGION_URL = {
    "intl": "https://dashscope-intl.aliyuncs.com/api/v1",
    "cn": "https://dashscope.aliyuncs.com/api/v1",
    "us": "https://dashscope-us.aliyuncs.com/api/v1",
}


def _upload_to_oss(audio_path: Path, model: str, region: str) -> str:
    """Upload a local audio file to DashScope OSS and return the oss:// URL.

    Args:
        audio_path: Path to local audio file
        model: Model name for upload certificate
        region: Region (intl, cn, us)

    Returns:
        oss:// URL of the uploaded file
    """
    import dashscope
    from dashscope.utils.oss_utils import OssUtils

    dashscope.base_http_api_url = REGION_URL[region]

    file_url, _ = OssUtils.upload(
        model=model,
        file_path=str(audio_path.resolve()),
        api_key=os.environ["DASHSCOPE_API_KEY"],
    )
    if file_url is None:
        typer.echo("Error: Failed to upload audio file to OSS", err=True)
        raise typer.Exit(1)

    typer.echo(f"Uploaded audio to: {file_url}", err=True)
    return file_url


def call_qwen3_asr(
    audio_path: Path,
    model: str = "qwen3-asr-flash",
    region: str = "intl",
    context: Optional[str] = None,
) -> dict:
    """Call Qwen3-ASR-Flash API.

    For qwen3-asr-flash: Uses MultiModalConversation API with file:// upload.
    For qwen3-asr-flash-filetrans: Uses QwenTranscription async API with OSS upload.

    Args:
        audio_path: Path to audio file
        model: Model name (qwen3-asr-flash or qwen3-asr-flash-filetrans)
        region: Region (intl, cn, us)
        context: Optional context string for biasing (only used with qwen3-asr-flash)

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

    resp = dashscope.MultiModalConversation.call(
        api_key=os.environ["DASHSCOPE_API_KEY"],
        model=model,
        messages=messages,
        result_format="message",
        asr_options={"enable_itn": False, "enable_words": True, "language": "zh"},
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
    """Call Qwen3-ASR-Flash-FileTrans API (async file transcription).

    This model uses the QwenTranscription API which requires an OSS URL
    instead of a local file path. It submits an async task and polls
    until completion.

    Args:
        audio_path: Path to audio file
        model: Model name (qwen3-asr-flash-filetrans)
        region: Region (intl, cn, us)
        context: Optional context string (used as vocabulary hint via vocabulary_id if available)

    Returns:
        Raw API response as dict
    """
    import dashscope
    from dashscope.audio.qwen_asr import QwenTranscription

    dashscope.base_http_api_url = REGION_URL[region]

    typer.echo(f"Uploading audio for filetrans...", err=True)
    file_url = _upload_to_oss(audio_path, model, region)

    typer.echo(f"Calling Qwen3-ASR-FileTrans ({model}) in {region} region...", err=True)

    kwargs = {}
    if context:
        typer.echo(
            "Note: filetrans model does not support system-message context biasing; "
            "context will be used for vocabulary hint only if vocabulary_id is set",
            err=True,
        )

    task_resp = QwenTranscription.async_call(
        model=model,
        file_url=file_url,
        api_key=os.environ["DASHSCOPE_API_KEY"],
        **kwargs,
    )

    if task_resp.status_code != 200:
        typer.echo(
            f"API error submitting task: {task_resp.status_code} - {task_resp.message}",
            err=True,
        )
        raise typer.Exit(1)

    task_id = task_resp.output.get("task_id", "unknown")
    typer.echo(f"Task submitted: {task_id}, waiting for completion...", err=True)

    resp = QwenTranscription.wait(
        task=task_resp,
        api_key=os.environ["DASHSCOPE_API_KEY"],
    )

    if resp.status_code != 200:
        typer.echo(f"API error: {resp.status_code} - {resp.message}", err=True)
        raise typer.Exit(1)

    task_status = resp.output.get("task_status", "")
    if task_status != "SUCCEEDED":
        typer.echo(f"Task failed with status: {task_status}", err=True)
        if resp.output.get("message"):
            typer.echo(f"Message: {resp.output['message']}", err=True)
        raise typer.Exit(1)

    return resp.output


def extract_segments(response: dict) -> list[tuple[float, float, str]]:
    """Extract segments from Qwen3-ASR response.

    Handles both qwen3-asr-flash (MultiModalConversation) and
    qwen3-asr-flash-filetrans (QwenTranscription) response formats.

    Args:
        response: Raw API response dict

    Returns:
        List of (start, end, text) tuples
    """
    segments = []

    if "results" in response:
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
        typer.echo(f"Warning: No segments extracted from response", err=True)

    return segments


def _extract_segments_filetrans(response: dict) -> list[tuple[float, float, str]]:
    """Extract segments from QwenTranscription (filetrans) response.

    The filetrans response contains a 'results' list with a
    'transcription_url' pointing to a JSON file with the actual
    transcription data containing sentences with timestamps.

    Args:
        response: Raw filetrans API response dict

    Returns:
        List of (start, end, text) tuples
    """
    import requests

    segments = []
    results = response.get("results", [])

    for result in results:
        transcription_url = result.get("transcription_url")
        if not transcription_url:
            typer.echo("Warning: No transcription_url in result", err=True)
            continue

        typer.echo(f"Fetching transcription from URL...", err=True)
        try:
            tr_resp = requests.get(transcription_url, timeout=60)
            tr_resp.raise_for_status()
            tr_data = tr_resp.json()
        except Exception as e:
            typer.echo(f"Error fetching transcription: {e}", err=True)
            continue

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
    segments: list[tuple[float, float, str]],
    lyrics: list[str],
    threshold: float = 0.60,
) -> list[tuple[float, str, bool]]:
    """Snap ASR segments to canonical lyrics using fuzzy matching.

    Automatically detects script (traditional/simplified) of canonical lyrics
    and normalizes ASR output to match for scoring, but keeps original form
    for output.

    Args:
        segments: List of (start, end, asr_text) tuples
        lyrics: List of canonical lyric lines
        threshold: Minimum fuzzy score to snap (0-1)

    Returns:
        List of (start, final_text, replaced) tuples
    """
    from rapidfuzz import fuzz
    from zhconv import convert

    canonical_lines = [l for l in lyrics if l.strip()]
    results = []

    if not canonical_lines:
        return results

    sample_text = "".join(canonical_lines)
    target_script = detect_chinese_script(sample_text)

    canonical_lines_normalized = [convert(l, target_script) for l in canonical_lines]

    for start, _end, asr_text in segments:
        asr_normalized = convert(asr_text, target_script)
        scored = [
            (
                canonical_lines[i],
                fuzz.token_set_ratio(asr_normalized, canonical_lines_normalized[i]) / 100.0,
            )
            for i in range(len(canonical_lines))
        ]
        best_line, best_score = max(scored, key=lambda x: x[1])

        if best_score >= threshold:
            results.append((start, best_line, True))
        else:
            results.append((start, asr_text, False))

    return results


def results_to_lrc(results: list[tuple[float, str, bool]]) -> str:
    """Convert results to LRC format.

    Args:
        results: List of (start, text, replaced) tuples

    Returns:
        LRC format string
    """
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
) -> None:
    """Write diagnostic markdown file.

    Args:
        segments: List of (start, end, asr_text) tuples
        lyrics: List of canonical lyric lines
        results: List of (start, final_text, replaced) tuples
        output_path: Path to write diagnostic.md
    """
    from rapidfuzz import fuzz
    from zhconv import convert

    canonical_lines = [l for l in lyrics if l.strip()]

    lines = []
    lines.append("# Qwen3-ASR + MVSEP Diagnostic Report\n")
    lines.append("## Summary\n\n")
    lines.append(f"ASR segments: {len(segments)}\n")
    lines.append(f"Canonical lines: {len(canonical_lines)}\n")
    lines.append(f"Output lines: {len(results)}\n")

    replaced_count = sum(1 for _, _, replaced in results if replaced)
    kept_count = len(results) - replaced_count
    lines.append(f"Replaced by snap: {replaced_count}\n")
    lines.append(f"Kept original: {kept_count}\n")

    sample_text = "".join(canonical_lines)
    target_script = detect_chinese_script(sample_text) if sample_text else "zh-hans"
    canonical_lines_normalized = [convert(l, target_script) for l in canonical_lines]

    scores = []
    for (_, _, asr_text), (start, _, replaced) in zip(segments, results):
        asr_normalized = convert(asr_text, target_script)
        scored = [
            fuzz.token_set_ratio(asr_normalized, canonical_lines_normalized[i]) / 100.0
            for i in range(len(canonical_lines))
        ]
        best_score = max(scored)
        scores.append(best_score)

    if scores:
        avg_score = sum(scores) / len(scores)
        lines.append(f"Average snap score: {avg_score:.2f}\n")

    if segments:
        duration = segments[-1][1] - segments[0][0]
        lines.append(f"Audio duration: {duration:.2f}s\n")
        lines.append(f"Segments per second: {len(segments) / duration:.2f}\n")

    lines.append("\n## Segment Details\n\n")
    lines.append("| Start | End | ASR Text | Matched Canonical | Score | Replaced |\n")
    lines.append("|-------|-----|----------|-------------------|-------|----------|\n")

    for (_, end, asr_text), (start, final_text, replaced) in zip(segments, results):
        asr_normalized = convert(asr_text, target_script)
        scored = [
            (
                canonical_lines[i],
                fuzz.token_set_ratio(asr_normalized, canonical_lines_normalized[i]) / 100.0,
            )
            for i in range(len(canonical_lines))
        ]
        best_line, best_score = max(scored, key=lambda x: x[1])

        lines.append(
            f"| {start:6.2f} | {end:4.2f} | {asr_text[:30]:30s} | {best_line[:30]:30s} | {best_score:5.2f} | {'Yes' if replaced else 'No'} |\n"
        )

    output_path.write_text("".join(lines))


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
    """Resolve a song ID to a local audio path using MVSEP for vocal extraction.

    This function handles both direct audio file paths and song IDs from the
    catalog. For song IDs, it initializes the database, R2 client, and asset
    cache to download/cache audio files.

    When mvsep_vocals is True and no cached vocals exist, uses MVSEP cloud API
    for vocal extraction instead of local models.

    Args:
        song_id: Song ID (e.g., "wo_yao_quan_xin_zan_mei_244") or path to audio file
        mvsep_api_token: MVSEP API token
        mvsep_vocals: Whether to use MVSEP vocal extraction
        stage1_sep_type: MVSEP Stage 1 separation type (default: 48 = MelBand Roformer)
        stage1_add_opt1: MVSEP Stage 1 model variant (default: 11 = becruily deux)
        stage2_sep_type: MVSEP Stage 2 separation type (default: 22 = Reverb Removal)
        stage2_add_opt1: MVSEP Stage 2 model variant (default: 0 = FoxJoy MDX23C)
        stage2_add_opt2: MVSEP Stage 2 additional option (default: 1 = use as is)
        output_format: MVSEP output format (default: 2 = FLAC 16-bit)
        timeout: Max seconds per MVSEP stage
        reuse_stage1: Reuse existing Stage 1 vocals if found

    Returns:
        Tuple of (audio_path, lyrics_list). Lyrics are only returned when
        resolving via song ID from database; None for direct audio paths.

    Raises:
        typer.Exit: If song not found, config missing, or audio unavailable
    """
    from stream_of_worship.app.config import AppConfig
    from stream_of_worship.db.connection import ConnectionProvider
    from stream_of_worship.app.db.read_client import ReadOnlyClient
    from stream_of_worship.app.services.catalog import CatalogService
    from stream_of_worship.app.services.asset_cache import AssetCache
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
                typer.echo(f"  Stage 1: sep_type={stage1_sep_type}, add_opt1={stage1_add_opt1}", err=True)
                typer.echo(f"  Stage 2: sep_type={stage2_sep_type}, add_opt1={stage2_add_opt1}, add_opt2={stage2_add_opt2}", err=True)
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


@app.command()
def main(
    song_id: str = typer.Argument(
        ..., help="Song ID (e.g., wo_yao_quan_xin_zan_mei_244) or path to audio file"
    ),
    mvsep_vocals: bool = typer.Option(
        True, "--mvsep-vocals/--no-mvsep-vocals", help="Use MVSEP vocal extraction"
    ),
    output: Optional[Path] = typer.Option(
        None, "--output", "-o", help="Output file (default: stdout)"
    ),
    model: str = typer.Option(
        "qwen3-asr-flash", "--model", help="Model (qwen3-asr-flash or qwen3-asr-flash-filetrans)"
    ),
    region: str = typer.Option("intl", "--region", help="Region (intl, cn, us)"),
    snap: bool = typer.Option(True, "--snap/--no-snap", help="Enable canonical-line fuzzy snap"),
    snap_threshold: float = typer.Option(
        0.60, "--snap-threshold", help="Minimum fuzzy score to snap (0-1)"
    ),
    lyrics_context: bool = typer.Option(
        True, "--lyrics-context/--no-lyrics-context", help="Enable context biasing with lyrics"
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
    mvsep_api_token: Optional[str] = typer.Option(
        None, "--mvsep-api-token", help="MVSEP API token (or set MVSEP_API_KEY env var)"
    ),
    stage1_sep_type: int = typer.Option(
        48, "--stage1-sep-type", help="MVSEP Stage 1 sep_type (default: 48 = MelBand Roformer)"
    ),
    stage1_add_opt1: int = typer.Option(
        11, "--stage1-add-opt1", help="MVSEP Stage 1 model variant (default: 11 = becruily deux)"
    ),
    stage2_sep_type: int = typer.Option(
        22, "--stage2-sep-type", help="MVSEP Stage 2 sep_type (default: 22 = Reverb Removal)"
    ),
    stage2_add_opt1: int = typer.Option(
        0, "--stage2-add-opt1", help="MVSEP Stage 2 model variant (default: 0 = FoxJoy MDX23C)"
    ),
    stage2_add_opt2: int = typer.Option(
        1, "--stage2-add-opt2", help="MVSEP Stage 2 add_opt2 (default: 1 = use as is)"
    ),
    output_format: int = typer.Option(
        2, "--output-format", help="MVSEP output format (default: 2 = FLAC 16-bit)"
    ),
    timeout: float = typer.Option(
        900.0, "--timeout", help="Max seconds per MVSEP stage (default: 900)"
    ),
    reuse_stage1: bool = typer.Option(
        False, "--reuse-stage1", help="Reuse existing Stage 1 vocals if found"
    ),
):
    """Run Qwen3-ASR-Flash transcription on a song with MVSEP vocal extraction.

    By default, the entire song is transcribed with context biasing and
    canonical-line snap enabled. Uses MVSEP cloud API for vocal extraction
    when no cached vocals are available.
    """
    if "DASHSCOPE_API_KEY" not in os.environ:
        typer.echo("Error: DASHSCOPE_API_KEY environment variable not set", err=True)
        raise typer.Exit(1)

    if mvsep_vocals:
        mvsep_token = mvsep_api_token or os.environ.get("MVSEP_API_KEY")
        if not mvsep_token:
            typer.echo(
                "Error: MVSEP API token required. Use --mvsep-api-token or set MVSEP_API_KEY env var.",
                err=True,
            )
            raise typer.Exit(1)
    else:
        mvsep_token = None

    audio_path, lyrics = resolve_song_audio_path_mvsep(
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

    if lyrics is None:
        typer.echo("Error: No lyrics from catalog; cannot run biasing/snap.", err=True)
        raise typer.Exit(1)

    lyrics_text = "\n".join(lyrics)

    effective_end: Optional[float] = end if end and end > 0 else None
    if effective_end:
        typer.echo(f"Transcribing segment: {start}s to {effective_end}s", err=True)
    elif start > 0:
        typer.echo(f"Transcribing from {start}s to end", err=True)
    else:
        typer.echo("Transcribing full song", err=True)

    transcribe_path = audio_path
    segment_path: Optional[Path] = None
    if start > 0 or effective_end is not None:
        typer.echo(f"Extracting audio segment: {start}s to {effective_end or 'end'}s", err=True)
        segment_path = extract_audio_segment(audio_path, start, effective_end or 3600)
        transcribe_path = segment_path

    try:
        context = None
        if lyrics_context:
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
            audio_path=transcribe_path,
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

        typer.echo(f"Extracted {len(segments)} segments", err=True)

        if snap:
            results = canonical_line_snap(segments, lyrics, threshold=snap_threshold)
            replaced_count = sum(1 for _, _, replaced in results if replaced)
            typer.echo(
                f"Canonical-line snap: {replaced_count}/{len(results)} segments replaced", err=True
            )

            if save_raw:
                diag_file = save_raw / "diagnostic.md"
                write_diagnostic(segments, lyrics, results, diag_file)
                typer.echo(f"Saved diagnostic report to: {diag_file}", err=True)
        else:
            results = [(start, text, False) for start, _end, text in segments]
            typer.echo(f"Snap disabled, using raw ASR output", err=True)

        lrc_content = results_to_lrc(results)

        if output:
            output.write_text(lrc_content, encoding="utf-8")
            typer.echo(f"Wrote LRC to: {output}", err=True)
        else:
            print(lrc_content)

    finally:
        if segment_path and segment_path.exists():
            segment_path.unlink()


if __name__ == "__main__":
    app()
