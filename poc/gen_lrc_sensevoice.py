#!/usr/bin/env python3
"""SenseVoice transcription driver for generating LRC files.

Runs SenseVoice transcription on a song from the local cache or a direct
file path, enabling quick experimentation to diagnose transcription accuracy.
"""

from pathlib import Path
from typing import Optional

import typer

# Import shared utilities
from poc.utils import extract_audio_segment, format_timestamp, resolve_song_audio_path

app = typer.Typer(help="SenseVoice transcription driver")


def merge_vad_segments(
    segments_ms: list[list[int]],
    max_len_s: int,
    gap_ms: int = 200,
) -> list[list[int]]:
    """Merge nearby VAD segments into longer spans."""
    if not segments_ms:
        return []
    if max_len_s <= 0:
        return segments_ms

    merged: list[list[int]] = []
    cur_start, cur_end = segments_ms[0]
    max_len_ms = max_len_s * 1000

    for start, end in segments_ms[1:]:
        gap = start - cur_end
        new_len = end - cur_start
        if gap <= gap_ms and new_len <= max_len_ms:
            cur_end = end
        else:
            merged.append([cur_start, cur_end])
            cur_start, cur_end = start, end

    merged.append([cur_start, cur_end])
    return merged


def split_segments(
    segments_ms: list[list[int]],
    chunk_seconds: int,
) -> list[list[int]]:
    """Split segments into fixed-size chunks."""
    if chunk_seconds <= 0:
        return segments_ms

    chunk_ms = chunk_seconds * 1000
    out: list[list[int]] = []
    for start, end in segments_ms:
        cur = start
        while cur < end:
            nxt = min(cur + chunk_ms, end)
            out.append([cur, nxt])
            cur = nxt
    return out


def split_segments_on_silence(
    audio_path: Path,
    segments_ms: list[list[int]],
    silence_gap_ms: int,
    silence_thresh_db: float,
) -> list[list[int]]:
    """Split segments based on detected silence gaps."""
    if silence_gap_ms <= 0 or not segments_ms:
        return segments_ms

    from pydub import AudioSegment
    from pydub.silence import detect_silence

    audio = AudioSegment.from_file(str(audio_path))
    out: list[list[int]] = []

    for start, end in segments_ms:
        segment = audio[start:end]
        silences = detect_silence(
            segment,
            min_silence_len=silence_gap_ms,
            silence_thresh=silence_thresh_db,
        )
        cur = start
        for s_start, s_end in silences:
            seg_end = start + s_start
            if seg_end > cur:
                out.append([cur, seg_end])
            cur = start + s_end
        if cur < end:
            out.append([cur, end])

    return out


def transcribe_audio(
    audio_path: Path,
    model_name: str = "iic/SenseVoiceSmall",
    language: str = "zh",
    device: str = "cpu",
    start_seconds: float = 0.0,
    end_seconds: Optional[float] = None,
    vad_model: str = "fsmn-vad",
    vad_max_single_seg_ms: int = 30000,
    merge_vad: bool = True,
    merge_length_s: int = 15,
    batch_size_s: int = 60,
    use_itn: bool = True,
    sentence_timestamp: bool = True,
    punc_model: Optional[str] = "ct-punc-c",
    chunk_seconds: int = 0,
    split_on_silence: bool = True,
    silence_gap_ms: int = 0,
    silence_thresh_db: float = -40.0,
) -> list[tuple[float, float, str]]:
    """Run SenseVoice transcription on audio file.

    Args:
        audio_path: Path to audio file
        model_name: SenseVoice model name
        language: Language hint
        device: Device to run on ("cpu" or "cuda")
        start_seconds: Start time offset (for segment extraction)
        end_seconds: End time offset (for segment extraction, None for full)
        vad_model: VAD model name
        vad_max_single_seg_ms: Max single segment length for VAD (ms)
        merge_vad: Merge VAD segments
        merge_length_s: Merge length in seconds
        batch_size_s: Batch size in seconds
        use_itn: Use inverse text normalization
        sentence_timestamp: Request sentence-level timestamps
        punc_model: Punctuation model name (required for sentence timestamps)
        chunk_seconds: Split VAD segments into fixed-size chunks (seconds)
        split_on_silence: Use VAD segments only (no fixed chunking)
        silence_gap_ms: Minimum silence gap to split (ms)
        silence_thresh_db: Silence threshold in dBFS for splitting

    Returns:
        List of (start_time, end_time, text) tuples
    """
    import time

    try:
        from funasr import AutoModel
        from funasr.utils.postprocess_utils import rich_transcription_postprocess
    except ImportError:
        typer.echo("Error: funasr is required. Install with: pip install funasr", err=True)
        raise typer.Exit(1)

    # Extract segment if needed
    segment_path: Optional[Path] = None
    if start_seconds > 0 or end_seconds is not None:
        typer.echo(
            f"Extracting audio segment: {start_seconds}s to {end_seconds or 'end'}s", err=True
        )
        segment_path = extract_audio_segment(audio_path, start_seconds, end_seconds or 3600)
        transcribe_path = segment_path
    else:
        transcribe_path = audio_path

    try:
        typer.echo(f"Loading SenseVoice model: {model_name} on {device}", err=True)
        model_load_start = time.time()

        if sentence_timestamp:
            # Use external VAD to get timestamps, then run ASR per segment.
            vad_model_runner = AutoModel(
                model=vad_model,
                trust_remote_code=True,
                remote_code="./model.py",
                device=device,
            )
            vad_res = vad_model_runner.generate(input=str(transcribe_path))
            segments_ms = vad_res[0].get("value", []) if vad_res else []
            if split_on_silence:
                if merge_vad:
                    typer.echo(
                        "Split-on-silence enabled; disabling VAD merge to preserve gaps",
                        err=True,
                    )
                merge_vad = False
                chunk_seconds = 0
            if merge_vad:
                segments_ms = merge_vad_segments(segments_ms, merge_length_s)
            if chunk_seconds > 0:
                segments_ms = split_segments(segments_ms, chunk_seconds)
            if split_on_silence and silence_gap_ms > 0:
                segments_ms = split_segments_on_silence(
                    transcribe_path, segments_ms, silence_gap_ms, silence_thresh_db
                )
            typer.echo(f"VAD segments: {len(segments_ms)}", err=True)

            asr_model = AutoModel(
                model=model_name,
                trust_remote_code=True,
                remote_code="./model.py",
                vad_model=None,
                punc_model=None,
                device=device,
            )
        else:
            asr_model = AutoModel(
                model=model_name,
                trust_remote_code=True,
                remote_code="./model.py",
                vad_model=vad_model,
                vad_kwargs={"max_single_segment_time": vad_max_single_seg_ms},
                punc_model=punc_model,
                device=device,
            )

        model_load_elapsed = time.time() - model_load_start
        typer.echo(f"Model loaded in {model_load_elapsed:.2f}s", err=True)

        typer.echo(f"Running transcription: {transcribe_path}", err=True)
        typer.echo(
            "Parameters: "
            f"language={language}, batch_size_s={batch_size_s}, use_itn={use_itn}, "
            f"merge_vad={merge_vad}, merge_length_s={merge_length_s}, "
            f"sentence_timestamp={sentence_timestamp}, punc_model={punc_model}",
            err=True,
        )
        transcribe_start = time.time()

        phrases: list[tuple[float, float, str]] = []
        if sentence_timestamp and segments_ms:
            for start_ms, end_ms in segments_ms:
                segment_path = extract_audio_segment(
                    transcribe_path, start_ms / 1000.0, end_ms / 1000.0
                )
                try:
                    result = asr_model.generate(
                        input=str(segment_path),
                        cache={},
                        language=language,
                        use_itn=use_itn,
                        batch_size_s=batch_size_s,
                        merge_vad=False,
                        merge_length_s=0,
                    )
                finally:
                    if segment_path.exists():
                        segment_path.unlink()

                if result and len(result) > 0:
                    for res in result:
                        text = res.get("text", "").strip()
                        if text:
                            try:
                                text = rich_transcription_postprocess(text)
                            except Exception:
                                pass
                            phrases.append((start_ms / 1000.0, end_ms / 1000.0, text))
        else:
            result = asr_model.generate(
                input=str(transcribe_path),
                cache={},
                language=language,
                use_itn=use_itn,
                batch_size_s=batch_size_s,
                merge_vad=merge_vad,
                merge_length_s=merge_length_s,
                sentence_timestamp=False,
            )
            if result and len(result) > 0:
                for res in result:
                    text = res.get("text", "").strip()
                    if text:
                        try:
                            text = rich_transcription_postprocess(text)
                        except Exception:
                            pass
                        phrases.append((0.0, 0.0, text))

        # Adjust timestamps by start offset
        if start_seconds > 0:
            phrases = [
                (start + start_seconds, end + start_seconds, text)
                for start, end, text in phrases
            ]

        transcribe_elapsed = time.time() - transcribe_start
        typer.echo(f"Transcription completed in {transcribe_elapsed:.2f}s", err=True)

        return phrases
    finally:
        # Clean up temp file
        if segment_path and segment_path.exists():
            segment_path.unlink()


def phrases_to_lrc(phrases: list[tuple[float, float, str]]) -> str:
    """Convert phrases to LRC format.

    Args:
        phrases: List of (start, end, text) tuples

    Returns:
        LRC format string
    """
    lines = []
    for start, _end, text in phrases:
        start_ts = format_timestamp(start)
        end_ts = format_timestamp(_end if _end > 0 else start)
        lines.append(f"{start_ts} {end_ts} {text}")
    return "\n".join(lines)


@app.command()
def main(
    song_id: str = typer.Argument(
        ..., help="Song ID (e.g., wo_yao_quan_xin_zan_mei_244) or path to audio file"
    ),
    device: str = typer.Option("cpu", "--device", "-d", help="Device to run on (cpu/cuda)"),
    model: str = typer.Option(
        "iic/SenseVoiceSmall", "--model", "-m", help="SenseVoice model name"
    ),
    use_vocals: bool = typer.Option(
        True, "--use-vocals/--no-use-vocals", help="Use vocals stem if available"
    ),
    output: Optional[Path] = typer.Option(
        None, "--output", "-o", help="Output file (default: stdout)"
    ),
    start: float = typer.Option(
        0.0, "--start", "-s", help="Start timestamp in seconds (default: 0)"
    ),
    end: Optional[float] = typer.Option(
        None, "--end", "-e", help="End timestamp in seconds (default: full song)"
    ),
    vad_model: str = typer.Option(
        "fsmn-vad", "--vad-model", help="VAD model name"
    ),
    vad_max_single_seg_ms: int = typer.Option(
        30000, "--vad-max-single-seg-ms", help="VAD max single segment time (ms)"
    ),
    merge_vad: bool = typer.Option(
        True, "--merge-vad/--no-merge-vad", help="Merge VAD segments"
    ),
    merge_length_s: int = typer.Option(
        15, "--merge-length-s", help="Merge length in seconds"
    ),
    batch_size_s: int = typer.Option(
        60, "--batch-size-s", help="Batch size in seconds"
    ),
    use_itn: bool = typer.Option(
        True, "--use-itn/--no-use-itn", help="Use inverse text normalization"
    ),
    sentence_timestamp: bool = typer.Option(
        True, "--sentence-timestamp/--no-sentence-timestamp", help="Enable sentence timestamps"
    ),
    punc_model: Optional[str] = typer.Option(
        "ct-punc-c",
        "--punc-model",
        help="Punctuation model for sentence timestamps (set empty to disable)",
    ),
    chunk_seconds: int = typer.Option(
        0, "--chunk-seconds", help="Split VAD segments into fixed-size chunks (seconds)"
    ),
    split_on_silence: bool = typer.Option(
        True,
        "--split-on-silence/--no-split-on-silence",
        help="Use VAD segments only (no fixed chunking)",
    ),
    silence_gap_ms: int = typer.Option(
        0, "--silence-gap-ms", help="Split on silence gaps of at least this length (ms)"
    ),
    silence_thresh_db: float = typer.Option(
        -40.0, "--silence-thresh-db", help="Silence threshold in dBFS for splitting"
    ),
):
    """Run SenseVoice transcription on a song and output LRC format.

    By default, the entire song is transcribed. Use --start and --end to
    transcribe a specific segment.
    """
    audio_path, _ = resolve_song_audio_path(song_id, use_vocals=use_vocals)

    # Determine time range
    effective_end: Optional[float] = end if end and end > 0 else None
    if effective_end:
        typer.echo(f"Transcribing segment: {start}s to {effective_end}s", err=True)
    elif start > 0:
        typer.echo(f"Transcribing from {start}s to end", err=True)
    else:
        typer.echo("Transcribing full song", err=True)

    # Run transcription
    effective_punc_model = punc_model if punc_model else None
    if sentence_timestamp and not effective_punc_model:
        typer.echo(
            "Warning: sentence timestamps requested without punc model; disabling sentence timestamps",
            err=True,
        )
        sentence_timestamp = False

    phrases = transcribe_audio(
        audio_path=audio_path,
        model_name=model,
        language="zh",
        device=device,
        start_seconds=start,
        end_seconds=effective_end,
        vad_model=vad_model,
        vad_max_single_seg_ms=vad_max_single_seg_ms,
        merge_vad=merge_vad,
        merge_length_s=merge_length_s,
        batch_size_s=batch_size_s,
        use_itn=use_itn,
        sentence_timestamp=sentence_timestamp,
        punc_model=effective_punc_model,
        chunk_seconds=chunk_seconds,
        split_on_silence=split_on_silence,
        silence_gap_ms=silence_gap_ms,
        silence_thresh_db=silence_thresh_db,
    )

    typer.echo(f"Transcribed {len(phrases)} phrases", err=True)

    # Convert to LRC format
    lrc_content = phrases_to_lrc(phrases)

    # Output
    if output:
        output.write_text(lrc_content, encoding="utf-8")
        typer.echo(f"Wrote LRC to: {output}", err=True)
    else:
        print(lrc_content)


if __name__ == "__main__":
    app()
