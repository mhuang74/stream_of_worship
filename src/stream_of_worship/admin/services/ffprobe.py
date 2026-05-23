from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any


def is_ffprobe_available() -> bool:
    """Return True if ffprobe binary is found on PATH."""
    return shutil.which("ffprobe") is not None


def probe_audio(file_path: Path) -> dict[str, Any] | None:
    """Probe an audio file with ffprobe and return metadata.

    Returns dict with keys: duration_seconds, duration_ms, channels,
    sample_rate, bitrate_kbps. Returns None on failure.
    """
    try:
        if not file_path.exists():
            return None
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "quiet",
                "-print_format", "json",
                "-show_format",
                "-show_streams",
                str(file_path),
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
        }
    except Exception:
        return None


def probe_duration(file_path: Path) -> float | None:
    """Probe an audio file and return duration_seconds, or None on failure."""
    info = probe_audio(file_path)
    if info and info["duration_seconds"] > 0:
        return info["duration_seconds"]
    return None
