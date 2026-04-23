#!/usr/bin/env python3
"""OmniSenseVoice transcription driver for generating LRC files.

Runs OmniSenseVoice transcription on a song from the local cache or a direct
file path, enabling quick experimentation to diagnose transcription accuracy.
"""

from pathlib import Path
from typing import Optional

import typer

# Import shared utilities
from poc.utils import SUPPORTED_AUDIO_FORMATS, extract_audio_segment, format_timestamp

app = typer.Typer(help="OmniSenseVoice transcription driver")

# Max time gap (seconds) between identical adjacent phrases to consider them duplicates
DEDUPE_TIME_THRESHOLD_S = 1.0


def transcribe_audio(
    audio_path: Path,
    model_name: str = "iic/SenseVoiceSmall",
    language: str = "zh",
    device: str = "cpu",
    start_seconds: float = 0.0,
    end_seconds: Optional[float] = None,
    textnorm: str = "withitn",
    timestamps: bool = True,
    quantize: bool = False,
    chunk_seconds: float = 0.0,
    chunk_overlap: float = 0.0,
) -> list[tuple[float, float, str]]:
    """Run OmniSenseVoice transcription on audio file."""
    import time

    try:
        from omnisense.models import OmniSenseVoiceSmall
    except ImportError:
        typer.echo(
            "Error: OmniSenseVoice is required. Install with: pip install OmniSenseVoice",
            err=True,
        )
        raise typer.Exit(1)

    if chunk_seconds > 0 and chunk_overlap >= chunk_seconds:
        typer.echo("Error: --chunk-overlap must be smaller than --chunk-seconds", err=True)
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
        device_id = -1
        device_override = None
        if device.startswith("cuda"):
            device_override = device
            if ":" in device:
                try:
                    device_id = int(device.split(":", 1)[1])
                except ValueError:
                    device_id = 0
            else:
                device_id = 0
        elif device != "cpu":
            device_override = device

        typer.echo(f"Loading OmniSenseVoice model: {model_name} on {device}", err=True)
        model_load_start = time.time()
        model = OmniSenseVoiceSmall(
            model_dir=model_name,
            device_id=device_id,
            device=device_override,
            quantize=quantize,
        )
        model_load_elapsed = time.time() - model_load_start
        typer.echo(f"Model loaded in {model_load_elapsed:.2f}s", err=True)

        typer.echo(f"Running transcription: {transcribe_path}", err=True)
        typer.echo(
            f"Parameters: language={language}, textnorm={textnorm}, timestamps={timestamps}, "
            f"quantize={quantize}, chunk_seconds={chunk_seconds}, chunk_overlap={chunk_overlap}",
            err=True,
        )
        transcribe_start = time.time()
        phrases: list[tuple[float, float, str]] = []
        if chunk_seconds > 0:
            from pydub import AudioSegment

            import tempfile

            source_suffix = transcribe_path.suffix.lower()
            if source_suffix not in SUPPORTED_AUDIO_FORMATS:
                source_suffix = ".wav"

            audio = AudioSegment.from_file(str(transcribe_path))
            duration_s = len(audio) / 1000.0
            step_s = chunk_seconds - chunk_overlap

            chunk_starts: list[float] = []
            cur = 0.0
            while cur < duration_s:
                chunk_starts.append(cur)
                cur += step_s

            typer.echo(
                f"Chunking enabled: duration={duration_s:.2f}s, chunks={len(chunk_starts)}, "
                f"window={chunk_seconds:.2f}s, overlap={chunk_overlap:.2f}s",
                err=True,
            )

            for idx, chunk_start in enumerate(chunk_starts, start=1):
                chunk_end = min(duration_s, chunk_start + chunk_seconds)
                start_ms = int(chunk_start * 1000)
                end_ms = int(chunk_end * 1000)

                chunk = audio[start_ms:end_ms]
                temp_file = tempfile.NamedTemporaryFile(suffix=source_suffix, delete=False)
                chunk_path = Path(temp_file.name)
                temp_file.close()
                try:
                    chunk.export(str(chunk_path), format=source_suffix.lstrip("."))
                    typer.echo(
                        f"Transcribing chunk {idx}/{len(chunk_starts)}: "
                        f"{chunk_start:.2f}s-{chunk_end:.2f}s",
                        err=True,
                    )
                    results = model.transcribe(
                        str(chunk_path),
                        language=language,
                        textnorm=textnorm,
                        batch_size=1,
                        timestamps=timestamps,
                        progressbar=False,
                    )
                finally:
                    if chunk_path.exists():
                        chunk_path.unlink()

                if not results:
                    continue

                for result in results:
                    text = (result.text or "").strip()
                    if not text:
                        continue

                    start = chunk_start
                    end = chunk_start
                    if timestamps and result.words:
                        first_word = result.words[0]
                        last_word = result.words[-1]
                        start = chunk_start + float(first_word.start)
                        end = chunk_start + float(last_word.start + last_word.duration)

                    phrases.append((start, end, text))
        else:
            results = model.transcribe(
                str(transcribe_path),
                language=language,
                textnorm=textnorm,
                batch_size=1,
                timestamps=timestamps,
                progressbar=False,
            )

            if results:
                for result in results:
                    text = (result.text or "").strip()
                    if not text:
                        continue

                    start = 0.0
                    end = 0.0
                    if timestamps and result.words:
                        first_word = result.words[0]
                        last_word = result.words[-1]
                        start = float(first_word.start)
                        end = float(last_word.start + last_word.duration)

                    phrases.append((start, end, text))

        # Adjust timestamps by start offset
        if start_seconds > 0:
            phrases = [
                (start + start_seconds, end + start_seconds, text)
                for start, end, text in phrases
            ]

        # Overlap chunking can produce repeated adjacent lines; remove exact duplicates.
        deduped: list[tuple[float, float, str]] = []
        for start, end, text in phrases:
            if deduped and deduped[-1][2] == text and abs(start - deduped[-1][0]) < DEDUPE_TIME_THRESHOLD_S:
                continue
            deduped.append((start, end, text))
        phrases = deduped

        transcribe_elapsed = time.time() - transcribe_start
        typer.echo(f"Transcription completed in {transcribe_elapsed:.2f}s", err=True)

        return phrases
    finally:
        if segment_path and segment_path.exists():
            segment_path.unlink()


def phrases_to_lrc(phrases: list[tuple[float, float, str]]) -> str:
    """Convert phrases to LRC format."""
    lines = []
    for start, end, text in phrases:
        start_ts = format_timestamp(start)
        end_ts = format_timestamp(end if end > 0 else start)
        lines.append(f"{start_ts} {end_ts} {text}")
    return "\n".join(lines)


@app.command()
def main(
    song_id: str = typer.Argument(
        ..., help="Song ID (e.g., wo_yao_quan_xin_zan_mei_244) or path to audio file"
    ),
    device: str = typer.Option("cpu", "--device", "-d", help="Device to run on (cpu/cuda[:id])"),
    model: str = typer.Option(
        "iic/SenseVoiceSmall", "--model", "-m", help="OmniSenseVoice model name/path"
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
    language: str = typer.Option(
        "zh", "--language", "-l", help="Language hint (auto, zh, en, yue, ja, ko)"
    ),
    textnorm: str = typer.Option(
        "withitn", "--textnorm", help="Text normalization mode (withitn/woitn)"
    ),
    timestamps: bool = typer.Option(
        True, "--timestamps/--no-timestamps", help="Enable word-level timestamps"
    ),
    quantize: bool = typer.Option(
        False, "--quantize", help="Use quantized OmniSenseVoice model"
    ),
    chunk_seconds: float = typer.Option(
        0.0, "--chunk-seconds", help="Chunk window size in seconds (0 disables chunking)"
    ),
    chunk_overlap: float = typer.Option(
        0.0, "--chunk-overlap", help="Chunk overlap in seconds (requires chunking)"
    ),
):
    """Run OmniSenseVoice transcription on a song and output LRC format."""
    from poc.utils import resolve_song_audio_path

    if textnorm not in ("withitn", "woitn"):
        typer.echo("Error: --textnorm must be one of: withitn, woitn", err=True)
        raise typer.Exit(1)

    audio_path, _ = resolve_song_audio_path(song_id, use_vocals=use_vocals)

    effective_end: Optional[float] = end if end and end > 0 else None
    if effective_end:
        typer.echo(f"Transcribing segment: {start}s to {effective_end}s", err=True)
    elif start > 0:
        typer.echo(f"Transcribing from {start}s to end", err=True)
    else:
        typer.echo("Transcribing full song", err=True)

    phrases = transcribe_audio(
        audio_path=audio_path,
        model_name=model,
        language=language,
        device=device,
        start_seconds=start,
        end_seconds=effective_end,
        textnorm=textnorm,
        timestamps=timestamps,
        quantize=quantize,
        chunk_seconds=chunk_seconds,
        chunk_overlap=chunk_overlap,
    )

    typer.echo(f"Transcribed {len(phrases)} phrases", err=True)

    lrc_content = phrases_to_lrc(phrases)

    if output:
        output.write_text(lrc_content, encoding="utf-8")
        typer.echo(f"Wrote LRC to: {output}", err=True)
    else:
        print(lrc_content)


if __name__ == "__main__":
    app()
