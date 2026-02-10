# Plan: Whisper Transcription Test Driver

## Overview
Create a standalone CLI script `poc/gen_lrc_whisper.py` to run Whisper transcription directly on a song from the local cache (downloading from R2 if needed) using the exact same parameters as the analysis service. This allows quick experimentation to diagnose why Whisper transcription is sometimes grossly inaccurate for certain Chinese songs.

## Goals
- Reproduce exact Whisper behavior from the analysis service
- Enable quick iteration on transcription parameters
- Use the same cache as the User App (`~/.config/sow-app/cache`)
- Support both full audio and vocals stem

## Reference Implementation
Source: `services/analysis/src/sow_analysis/workers/lrc.py:78-177`

### Key Parameters (Exact Match)
| Parameter | Value | Source |
|-----------|-------|--------|
| whisper_model | `large-v3` | `models.py:LrcOptions` |
| language | `zh` | `models.py:LrcOptions` |
| device | `cpu` (or env) | `config.py:SOW_WHISPER_DEVICE` |
| compute_type | `int8` | `lrc.py:109` |
| beam_size | `5` | `lrc.py:133` |
| vad_filter | `False` | `lrc.py:134` |
| initial_prompt | `"这是一首中文敬拜歌的歌詞"` | `lrc.py:128` |

## Implementation

### 1. CLI Interface (Typer)

```python
# Arguments
song_id: str  # Required song ID (e.g., "wo_yao_quan_xin_zan_mei_244")

# Options
--device: str = "cpu"                    # "cpu" or "cuda"
--model: str = "large-v3"               # Whisper model name
--use-vocals: bool = True                # Prefer vocals stem if available
--output: Optional[Path] = None          # Output file (default: stdout)
--download-audio: bool = True            # Auto-download from R2 if not cached
```

### 2. Song Lookup Flow

1. Load `AppConfig` from `~/.config/sow-app/config.toml`
2. Initialize `ReadOnlyClient` with `config.db_path`
3. Initialize `CatalogService` with the db client
4. Call `catalog.get_song_with_recording(song_id)`
5. Extract `hash_prefix` from `recording.hash_prefix`

### 3. Audio File Resolution

1. Initialize `AssetCache` with `config.cache_dir` and `R2Client`
2. If `--use-vocals`:
   - Check if vocals stem exists via `asset_cache.get_stem_path(hash_prefix, "vocals")`
   - If exists, use it
   - If not and `--download-audio`, download via `asset_cache.download_stem(hash_prefix, "vocals")`
3. If vocals not available or `--use-vocals=False`:
   - Use `asset_cache.download_audio(hash_prefix)` (returns cached path or downloads)
4. Fail with clear error if audio cannot be obtained

### 4. Whisper Transcription

Copy exact logic from `lrc.py:_run_whisper_transcription()`:

```python
def _transcribe(audio_path: Path, model_name: str, device: str):
    from faster_whisper import WhisperModel

    cache_dir = Path.home() / ".cache" / "whisper"
    cache_dir.mkdir(parents=True, exist_ok=True)

    device_type = device
    compute_type = "int8"

    model = WhisperModel(
        model_name,
        device=device_type,
        compute_type=compute_type,
        download_root=str(cache_dir),
    )

    initial_prompt = "这是一首中文敬拜歌的歌詞"

    segments, info = model.transcribe(
        str(audio_path),
        language="zh",
        beam_size=5,
        vad_filter=False,  # Disabled - audio is already vocal stem from Demucs
        initial_prompt=initial_prompt,
    )

    # IMPORTANT: segments is a generator - consume it fully
    phrases = []
    for segment in segments:
        text = segment.text.strip()
        if text:
            phrases.append({
                "text": text,
                "start": segment.start,
                "end": segment.end,
            })

    return phrases, info
```

### 5. Output Format

LRC format to stdout (or file if `--output` specified):
```
[00:15.00] 我要看見
[00:18.50] 我要看見
[00:22.00] 如同摩西看見祢的榮耀
```

Log progress to stderr so it doesn't interfere with redirection:
```bash
PYTHONPATH=poc uv run --extra transcription poc/gen_lrc_whisper.py wo_yao_quan_xin_zan_mei_244 > output.lrc
```

## Dependencies

Add to `pyproject.toml` under `[project.optional-dependencies]`:

```toml
transcription = [
    "faster-whisper>=1.0.0",
    "typer>=0.12.0",
]
```

## File Structure

```
poc/
  gen_lrc_whisper.py      # Main script (~200-250 lines)
```

## Example Usage

```bash
# Basic usage - output to stdout, redirect to file
PYTHONPATH=poc uv run --extra transcription poc/gen_lrc_whisper.py wo_yao_quan_xin_zan_mei_244 > output.lrc

# Use CUDA device
PYTHONPATH=poc uv run --extra transcription poc/gen_lrc_whisper.py wo_yao_quan_xin_zan_mei_244 --device cuda > output.lrc

# Use full audio instead of vocals stem
PYTHONPATH=poc uv run --extra transcription poc/gen_lrc_whisper.py wo_yao_quan_xin_zan_mei_244 --no-use-vocals > output.lrc

# Specify output file instead of stdout
PYTHONPATH=poc uv run --extra transcription poc/gen_lrc_whisper.py wo_yao_quan_xin_zan_mei_244 --output output.lrc
```

## Error Handling

1. **Song not found**: Clear error message with suggestion to check song ID
2. **No recording**: Error if song exists but has no associated recording
3. **Audio not cached and download fails**: Error with R2 connection details
4. **Whisper model download fails**: Error with cache directory and network info
5. **Transcription fails**: Full exception traceback to stderr

## Logging

- All progress/info messages go to stderr
- Only LRC output goes to stdout
- Use `logging` module with stderr handler
- Log key events:
  - Song lookup success/failure
  - Audio file path being used
  - Model loading
  - Transcription start/complete with timing
  - Phrase count

## Future Extensions (Optional)

- Add `--compare` flag to compare two different model outputs
- Add `--word-level` for word-level timestamps
- Add `--condition-on-previous-text` toggle
- Add `--temperature` parameter for experimentation
