from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SongsetItem:
    id: str
    songset_id: str
    song_id: str
    song_title: str | None = None
    recording_hash_prefix: str | None = None
    position: int = 0
    gap_beats: float | None = None
    crossfade_enabled: int | None = None
    crossfade_duration_seconds: float | None = None
    key_shift_semitones: float | None = None
    tempo_ratio: float | None = None
    tempo_bpm: float | None = None
    duration_seconds: float | None = None


@dataclass(frozen=True)
class AudioSegmentInfo:
    item: SongsetItem
    audio_path: str
    start_time_seconds: float
    duration_seconds: float
    gap_before_seconds: float


@dataclass(frozen=True)
class ExportResult:
    output_path: str
    total_duration_seconds: float
    segments: tuple[AudioSegmentInfo, ...] = field(default_factory=tuple)
    sample_rate: int = 44100
    channels: int = 2


class AssetFetcherProtocol(Protocol):
    def download_audio(self, hash_prefix: str) -> str | None: ...


def get_crossfade_ms(item: SongsetItem) -> int:
    if not item.crossfade_enabled or not item.crossfade_duration_seconds:
        return 0
    return max(0, round(item.crossfade_duration_seconds * 1000))


def calculate_gap_ms(item: SongsetItem, tempo_bpm: float | None = None) -> int:
    if item.crossfade_enabled and item.crossfade_duration_seconds:
        return 0
    gap_beats = item.gap_beats if item.gap_beats is not None else 2.0
    if tempo_bpm and tempo_bpm > 0:
        beat_duration_ms = 60000.0 / tempo_bpm
        return round(gap_beats * beat_duration_ms)
    else:
        return round(gap_beats * 1000)


def get_audio_info(file_path: str) -> dict[str, Any] | None:
    try:
        p = Path(file_path)
        if not p.exists():
            return None
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                file_path,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return None
        metadata = json.loads(result.stdout)
        streams = metadata.get("streams", [])
        if not streams:
            return None
        audio_stream = None
        for s in streams:
            if s.get("codec_type") == "audio":
                audio_stream = s
                break
        if audio_stream is None:
            audio_stream = streams[0]
        fmt = metadata.get("format", {})
        duration_seconds = float(fmt.get("duration", 0))
        bitrate = int(fmt.get("bit_rate", "0") or "0")
        return {
            "duration_seconds": duration_seconds,
            "duration_ms": round(duration_seconds * 1000),
            "channels": int(audio_stream.get("channels", 2)),
            "sample_rate": int(audio_stream.get("sample_rate", 44100)),
            "bitrate_kbps": round(bitrate / 1000),
            "file_size_bytes": p.stat().st_size,
        }
    except Exception:
        return None


def build_ffmpeg_filter_complex(
    audio_files: list[dict[str, Any]],
    normalize: bool,
    target_lufs: float = -14.0,
) -> str:
    filter_parts: list[str] = []
    output_labels: list[str] = []

    for i, audio_file in enumerate(audio_files):
        next_crossfade_ms = audio_files[i + 1]["crossfade_ms"] if i + 1 < len(audio_files) else 0
        filters = [f"[{i}:a]asetpts=PTS-STARTPTS"]

        if audio_file["crossfade_ms"] > 0:
            fade_in_dur = audio_file["crossfade_ms"] / 1000
            filters.append(f"afade=t=in:st=0:d={fade_in_dur:.3f}")

        if next_crossfade_ms > 0:
            fade_out_start = max(0, (audio_file["duration_ms"] - next_crossfade_ms) / 1000)
            fade_out_dur = next_crossfade_ms / 1000
            filters.append(f"afade=t=out:st={fade_out_start:.3f}:d={fade_out_dur:.3f}")

        if audio_file["start_ms"] > 0:
            delay = round(audio_file["start_ms"])
            filters.append(f"adelay={delay}|{delay}")

        output_label = f"a{i}"
        filter_parts.append(f"{','.join(filters)}[{output_label}]")
        output_labels.append(f"[{output_label}]")

    amix_out_label = "[amix_out]" if normalize else "[outa]"
    filter_parts.append(
        f"{''.join(output_labels)}amix=inputs={len(output_labels)}"
        f":normalize=0:dropout_transition=0{amix_out_label}"
    )

    if normalize:
        filter_parts.append(
            f"{amix_out_label}loudnorm=I={target_lufs}:TP=-1.5:LRA=11[outa]"
        )

    return ";".join(filter_parts)


def concatenate_audio_files(
    audio_files: list[dict[str, Any]],
    output_path: str,
    normalize: bool = True,
    target_lufs: float = -14.0,
    output_bitrate: str = "320k",
    sample_rate: int = 44100,
    channels: int = 2,
    job_id: str | None = None,
) -> None:
    filter_complex = build_ffmpeg_filter_complex(audio_files, normalize, target_lufs)

    cmd: list[str] = ["ffmpeg", "-y"]
    for audio_file in audio_files:
        cmd.extend(["-i", audio_file["path"]])
    cmd.extend(["-filter_complex", filter_complex, "-map", "[outa]"])
    cmd.extend(["-c:a", "libmp3lame", "-b:a", output_bitrate])
    cmd.extend(["-ar", str(sample_rate), "-ac", str(channels)])
    cmd.append(output_path)

    logger.info("[%s] FFmpeg audio concat: starting (timeout=1800s)", job_id or "unknown")
    subprocess.run(cmd, check=True, capture_output=True, timeout=1800)
    logger.info("[%s] FFmpeg audio concat: complete", job_id or "unknown")


def generate_songset_audio(
    items: list[SongsetItem],
    output_path: str,
    asset_fetcher: AssetFetcherProtocol,
    progress_callback: Callable[[int, int], None] | None = None,
    normalize: bool = True,
    target_lufs: float = -14.0,
    output_bitrate: str = "320k",
    sample_rate: int = 44100,
    channels: int = 2,
    job_id: str | None = None,
) -> ExportResult:
    if not items:
        raise ValueError("Cannot generate audio for empty songset")

    segments: list[AudioSegmentInfo] = []
    current_time_ms = 0
    total_steps = len(items) * 2
    current_step = 0

    audio_files: list[dict[str, Any]] = []

    for i, item in enumerate(items):
        if progress_callback:
            progress_callback(current_step, total_steps)
        current_step += 1

        if not item.recording_hash_prefix:
            raise ValueError(f"Item {item.id} has no recording")

        logger.info(
            "[%s] Audio: processing song %d/%d - %s (hash=%s)",
            job_id or "unknown", i + 1, len(items),
            item.song_title or "untitled", item.recording_hash_prefix or "N/A",
        )

        audio_path = asset_fetcher.download_audio(item.recording_hash_prefix)
        if not audio_path:
            raise ValueError(f"Could not get audio for recording {item.recording_hash_prefix}")

        info = get_audio_info(audio_path)
        if not info:
            raise ValueError(f"Could not probe audio file: {audio_path}")
        duration_ms = info["duration_ms"]

        gap_ms = 0
        crossfade_ms = 0
        if i > 0:
            gap_ms = calculate_gap_ms(item, item.tempo_bpm)
            crossfade_ms = min(get_crossfade_ms(item), duration_ms)

        logger.info(
            "[%s] Audio: song %d probed - duration=%.1fs, gap_ms=%d, crossfade_ms=%d",
            job_id or "unknown", i + 1, duration_ms / 1000.0, gap_ms, crossfade_ms,
        )

        start_time_ms = 0 if i == 0 else max(0, current_time_ms + gap_ms - crossfade_ms)

        segment_info = AudioSegmentInfo(
            item=item,
            audio_path=audio_path,
            start_time_seconds=start_time_ms / 1000.0,
            duration_seconds=duration_ms / 1000.0,
            gap_before_seconds=gap_ms / 1000.0,
        )
        segments.append(segment_info)

        current_time_ms = start_time_ms + duration_ms
        audio_files.append(
            {
                "path": audio_path,
                "item": item,
                "gap_ms": gap_ms,
                "crossfade_ms": crossfade_ms,
                "duration_ms": duration_ms,
                "start_ms": start_time_ms,
            }
        )

        if progress_callback:
            progress_callback(current_step, total_steps)
        current_step += 1

    output_dir = Path(output_path).parent
    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info(
        "[%s] Audio: starting FFmpeg concatenation of %d files -> %s",
        job_id or "unknown", len(audio_files), output_path,
    )

    concatenate_audio_files(
        audio_files,
        output_path,
        normalize=normalize,
        target_lufs=target_lufs,
        output_bitrate=output_bitrate,
        sample_rate=sample_rate,
        channels=channels,
        job_id=job_id,
    )

    logger.info(
        "[%s] Audio: concatenation complete, total duration=%.1fs, %d segments",
        job_id or "unknown", current_time_ms / 1000.0, len(segments),
    )

    if progress_callback:
        progress_callback(total_steps, total_steps)

    return ExportResult(
        output_path=output_path,
        total_duration_seconds=current_time_ms / 1000.0,
        segments=tuple(segments),
        sample_rate=sample_rate,
        channels=channels,
    )


def calculate_total_duration(
    items: list[SongsetItem],
    asset_fetcher: AssetFetcherProtocol,
) -> float:
    total_ms = 0

    for i, item in enumerate(items):
        if i > 0:
            gap_ms = calculate_gap_ms(item, item.tempo_bpm)
            total_ms += gap_ms
            total_ms -= get_crossfade_ms(item)

        if item.duration_seconds is not None:
            total_ms += item.duration_seconds * 1000
        elif item.recording_hash_prefix:
            audio_path = asset_fetcher.download_audio(item.recording_hash_prefix)
            if audio_path:
                info = get_audio_info(audio_path)
                if info:
                    total_ms += info["duration_ms"]

    return total_ms / 1000.0
