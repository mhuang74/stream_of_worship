# Plan: Create gen_lrc_whisper.py Test Driver

## Context
The user wants a quick experimentation tool to debug why Whisper transcription is sometimes grossly inaccurate for Chinese songs. This will be a POC script that replicates the exact Whisper transcription logic from `services/analysis/src/sow_analysis/workers/lrc.py` but allows running it directly on a song from the cache.

## Requirements

1. **Location**: `poc/gen_lrc_whisper.py`
2. **Model & Parameters**: Exact same as `lrc.py`:
   - Uses `openai-whisper` (not faster_whisper)
   - Model: configurable, defaults to "large-v3"
   - Language: "zh"
   - Device: from settings/env (defaults to "cpu")
   - Word timestamps enabled
   - Whisper cache: from settings/env or platform default
3. **Input**: song_id as CLI argument
4. **Song Loading**: Load from User App cache:
   - Load app config from `~/.config/sow-app/config.toml` (or use default cache_dir)
   - Load admin config from `~/.config/sow-admin/config.toml` (or use default db_path)
   - Look up recording by song_id in database
   - Get hash_prefix from recording
   - Load audio file from: `~/.config/sow-app/cache/<hash_prefix>/audio/audio.mp3`
5. **Output**: LRC format to stdout (so user can redirect to file)

## Implementation

### File: `poc/gen_lrc_whisper.py`

```python
#!/usr/bin/env python3
"""Quick test driver for Whisper transcription on cached songs.

Replicates the exact Whisper transcription parameters from lrc.py
for debugging transcription accuracy issues.

Usage:
    PYTHONPATH=src uv run --extra lrc_generation poc/gen_lrc_whisper.py <song_id> > output.lrc

    # Or with explicit audio path:
    PYTHONPATH=src uv run --extra lrc_generation poc/gen_lrc_whisper.py --audio-path /path/to/audio.mp3 > output.lrc
"""

import argparse
import sys
import time
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import whisper
from stream_of_worship.app.db.read_client import ReadOnlyClient
from stream_of_worship.app.config import get_app_config_dir, AppConfig
from stream_of_worship.admin.config import AdminConfig, get_default_db_path


def get_whisper_cache_dir() -> Path:
    """Get Whisper cache directory."""
    import os
    if "SOW_WHISPER_CACHE_DIR" in os.environ:
        return Path(os.environ["SOW_WHISPER_CACHE_DIR"])
    # Default to app cache directory
    return get_app_config_dir() / "cache" / "whisper"


def get_db_path() -> Path:
    """Get database path from admin config or default."""
    config_path = Path.home() / ".config" / "sow-admin" / "config.toml"
    if config_path.exists():
        try:
            config = AdminConfig.load(config_path)
            return config.db_path
        except Exception:
            pass
    return get_default_db_path()


def get_cache_dir() -> Path:
    """Get cache directory from app config or default."""
    config_path = get_app_config_dir() / "config.toml"
    if config_path.exists():
        try:
            config = AppConfig.load(config_path)
            return config.cache_dir
        except Exception:
            pass
    return get_app_config_dir() / "cache"


def get_audio_path_from_song_id(song_id: str) -> Path:
    """Look up recording by song_id and return cached audio path."""
    db_path = get_db_path()
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")

    client = ReadOnlyClient(db_path)
    recording = client.get_recording_by_song_id(song_id)
    client.close()

    if not recording:
        raise ValueError(f"No recording found for song_id: {song_id}")

    # Path: ~/.config/sow-app/cache/<hash_prefix>/audio/audio.mp3
    cache_dir = get_cache_dir()
    audio_path = cache_dir / recording.hash_prefix / "audio" / "audio.mp3"

    if not audio_path.exists():
        raise FileNotFoundError(f"Audio not found at: {audio_path}")

    return audio_path


def transcribe_to_lrc(audio_path: Path, model_name: str = "large-v3", device: str = "cpu") -> str:
    """Transcribe audio to LRC format using exact same parameters as lrc.py."""

    cache_dir = get_whisper_cache_dir()
    cache_dir.mkdir(parents=True, exist_ok=True)

    print(f"Loading Whisper model: {model_name} on {device}", file=sys.stderr)
    load_start = time.time()
    model = whisper.load_model(model_name, device=device, download_root=str(cache_dir))
    print(f"Model loaded in {time.time() - load_start:.2f}s", file=sys.stderr)

    print(f"Transcribing: {audio_path}", file=sys.stderr)
    transcribe_start = time.time()
    result = model.transcribe(
        str(audio_path),
        language="zh",
        word_timestamps=True,
    )
    print(f"Transcription completed in {time.time() - transcribe_start:.2f}s", file=sys.stderr)

    # Build LRC output
    lines = []
    for segment in result.get("segments", []):
        start = segment.get("start", 0)
        text = segment.get("text", "").strip()
        if text:
            mm = int(start // 60)
            ss = start % 60
            lines.append(f"[{mm:02d}:{ss:05.2f}] {text}")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Test Whisper transcription on cached songs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s wo_yao_quan_xin_zan_mei_244 > output.lrc
  %(prog)s --audio-path /path/to/song.mp3 > output.lrc
  %(prog)s --model large-v3 --device cuda wo_yao_quan_xin_zan_mei_244
        """
    )
    parser.add_argument("song_id", nargs="?", help="Song ID to transcribe (from database)")
    parser.add_argument("--audio-path", type=Path, help="Direct audio file path (bypasses database lookup)")
    parser.add_argument("--model", default="large-v3", help="Whisper model name (default: large-v3)")
    parser.add_argument("--device", default="cpu", help="Device: cpu or cuda (default: cpu)")

    args = parser.parse_args()

    if args.audio_path:
        audio_path = args.audio_path
    elif args.song_id:
        audio_path = get_audio_path_from_song_id(args.song_id)
    else:
        parser.error("Either song_id or --audio-path must be provided")

    lrc_content = transcribe_to_lrc(audio_path, model_name=args.model, device=args.device)
    print(lrc_content)


if __name__ == "__main__":
    main()
```

## Verification Steps

1. Run with a known song ID:
   ```bash
   PYTHONPATH=src uv run --extra lrc_generation poc/gen_lrc_whisper.py wo_yao_quan_xin_zan_mei_244 > /tmp/test_output.lrc
   ```

2. Verify output is valid LRC format:
   ```
   [00:00.50] 歌词第一行
   [00:04.20] 歌词第二行
   ```

3. Compare with existing LRC in cache (if available):
   ```bash
   diff /tmp/test_output.lrc ~/.config/sow-app/cache/<hash_prefix>/lrc/lyrics.lrc
   ```

## Dependencies
- Uses `lrc_generation` extra from pyproject.toml (openai-whisper, openai)
- Requires database and cached audio to exist
