# Implementation Plan: `poc/gen_lrc_qwen3.py`

## Overview

Create a new LRC generation script using **Qwen3ForcedAligner** (`Qwen/Qwen3-ForcedAligner-0.6B`) for aligning known lyrics to audio timestamps. This differs from the Whisper version which performs speech recognition - the Qwen version requires pre-existing lyrics and aligns them precisely to the audio.

## Key Differences from Whisper Version

| Aspect | Whisper (`gen_lrc_whisper.py`) | Qwen3ForcedAligner (`gen_lrc_qwen3.py`) |
|--------|-------------------------------|----------------------------------------|
| **Primary Function** | Speech recognition (transcribes audio → text) | Forced alignment (aligns known text → audio timestamps) |
| **Lyrics Handling** | Used as initial prompt hint | **Required input** - aligns these exact lyrics to audio |
| **Output** | Transcribed text with timestamps | Same lyrics text with precise timestamps |
| **Audio Limit** | None | **5 minutes maximum** |
| **Package** | `faster-whisper` | `qwen-asr` |
| **Dependencies** | whisper, pydub | torch, qwen-asr |

## Qwen3ForcedAligner API Reference

### Installation
```bash
uv add --optional transcription_qwen3 qwen-asr
```

### Basic Usage
```python
import torch
from qwen_asr import Qwen3ForcedAligner

model = Qwen3ForcedAligner.from_pretrained(
    "Qwen/Qwen3-ForcedAligner-0.6B",
    dtype=torch.bfloat16,
    device_map="cuda:0",
)

results = model.align(
    audio="audio.wav",  # Local path, URL, or (np.ndarray, sr) tuple
    text="这是一段中文文本。",  # The lyrics/text to align
    language="Chinese",
)

# Results structure
results[0][0].text       # Aligned text segment
results[0][0].start_time # Start timestamp in seconds
results[0][0].end_time   # End timestamp in seconds
```

### Supported Languages
Chinese, English, Cantonese, French, German, Italian, Japanese, Korean, Portuguese, Russian, Spanish

## Implementation Structure

```
poc/gen_lrc_qwen3.py
├── Imports: typer, torch, qwen_asr, pathlib
├── format_timestamp(seconds: float) -> str
│   └── Format as [mm:ss.xx] for LRC
├── align_lyrics(
│   ├── audio_path: Path
│   ├── lyrics_text: str
│   ├── language: str = "Chinese"
│   ├── device: str = "auto"
│   ├── dtype: str = "float32"
│   ) -> list[tuple[float, float, str]]
│   ├── Initialize Qwen3ForcedAligner
│   ├── Handle device selection (MPS/CUDA/CPU)
│   ├── Validate audio length (< 5 min)
│   ├── Call model.align()
│   └── Extract (start, end, text) tuples
├── phrases_to_lrc(phrases) -> str
│   └── Convert to LRC format
└── main() - Typer CLI command
    ├── Load config
    ├── Initialize database client
    ├── Lookup song + recording
    ├── **Verify lyrics exist** (required!)
    ├── Initialize R2 client + cache
    ├── Resolve audio path (vocals stem or main)
    ├── Call align_lyrics()
    └── Output LRC to file or stdout
```

## CLI Options

```python
song_id: str                    # Song ID (required, e.g., wo_yao_quan_xin_zan_mei_244)
--device: "auto"|"mps"|"cuda"|"cpu"  # Device selection (default: auto)
--dtype: "bfloat16"|"float16"|"float32"  # Data type (default: float32 for MPS)
--use-vocals: bool              # Use vocals stem if available (default: True)
--output: Optional[Path]        # Output file (default: stdout)
--offline: bool                 # Only use cached files (default: True)
--language: str                 # Language hint (default: "Chinese")
```

## Apple M2 Optimizations

Since this targets Apple M2:

1. **Auto-detect MPS** (Metal Performance Shaders)
   ```python
   if device == "auto":
       if torch.backends.mps.is_available():
           device = "mps"
       elif torch.cuda.is_available():
           device = "cuda"
       else:
           device = "cpu"
   ```

2. **Default to float32** for MPS (bfloat16 not well supported)

3. **Device mapping for forced aligner**:
   ```python
   model = Qwen3ForcedAligner.from_pretrained(
       "Qwen/Qwen3-ForcedAligner-0.6B",
       dtype=torch.float32,
       device_map="mps" if torch.backends.mps.is_available() else "cpu",
   )
   ```

## Error Handling

1. **No lyrics**: Error exit with message - forced aligner requires lyrics
2. **Audio > 5 minutes**: Warning + error (model limitation)
3. **Missing audio**: Same handling as whisper version (download if --download, else error)
4. **Device not available**: Fall back to CPU with warning

## Dependencies

Use `uv add` to add dependencies to the `transcription_qwen3` optional group:

```bash
# Add qwen-asr to the transcription_qwen3 extra
uv add --optional transcription_qwen3 qwen-asr

# torch should already be in the project, but if not:
uv add --optional transcription_qwen3 torch
```

This will create/update the `[project.optional-dependencies]` section in `pyproject.toml`:

```toml
[project.optional-dependencies]
transcription_qwen3 = [
    "qwen-asr>=x.x.x",
    "torch>=x.x.x",
]
```

## Usage Examples

### Basic usage (stdout)
```bash
uv run --extra transcription_qwen3 python poc/gen_lrc_qwen3.py wo_yao_quan_xin_zan_mei_244
```

### Save to file
```bash
uv run --extra transcription_qwen3 python poc/gen_lrc_qwen3.py wo_yao_quan_xin_zan_mei_244 -o output.lrc
```

### Force CPU usage
```bash
uv run --extra transcription_qwen3 python poc/gen_lrc_qwen3.py wo_yao_quan_xin_zan_mei_244 --device cpu
```

### Download audio from R2
```bash
uv run --extra transcription_qwen3 python poc/gen_lrc_qwen3.py wo_yao_quan_xin_zan_mei_244 --download
```

## Notes

- Unlike Whisper, this script **requires lyrics** to be in the database
- The output will be the **same lyrics** with precise timestamps added
- Maximum audio length is **5 minutes** (Qwen3ForcedAligner limitation)
- Most worship songs are under 5 minutes, so this should work for the majority of cases
