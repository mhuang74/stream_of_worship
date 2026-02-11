# Plan: Create `poc/gen_lrc_whisperx.py`

## Context

We want a new POC script that uses the [WhisperX](https://github.com/m-bain/whisperX) library instead of `faster-whisper` directly. WhisperX adds batched inference and optional forced alignment (via wav2vec2) for more precise timestamps. This script mirrors `poc/gen_lrc_whisper.py` but swaps the transcription backend.

## Key Differences from gen_lrc_whisper.py

| Aspect | gen_lrc_whisper.py | gen_lrc_whisperx.py |
|--------|-------------------|---------------------|
| Library | `faster-whisper` | `whisperx` |
| Default model | `large-v3` | `large-v2` |
| Alignment | None | Optional wav2vec2 forced alignment via `--align/--no-align` |
| Timestamps | Always LRC | Toggle `--timestamps/--no-timestamps` (LRC vs plain text) |
| Batch inference | No | Yes (`batch_size` param) |

## Implementation Plan

### 1. Create `poc/gen_lrc_whisperx.py`

Structure follows `gen_lrc_whisper.py` exactly (same imports from `src/`, same typer CLI pattern, same audio source resolution logic).

#### Core functions:

- **`transcribe_audio()`** — Replace `faster-whisper` calls with WhisperX:
  ```python
  import whisperx
  model = whisperx.load_model(model_name, device=device, compute_type=compute_type)
  audio = whisperx.load_audio(str(audio_path))
  result = model.transcribe(audio, batch_size=batch_size, language=language)
  ```

- **`align_segments()`** (new) — Optional forced alignment:
  ```python
  align_model, metadata = whisperx.load_align_model(language_code=language, device=device)
  result = whisperx.align(result["segments"], align_model, metadata, audio, device)
  ```

- **`phrases_to_lrc()`** — Same as existing (format `[mm:ss.xx] text`)
- **`phrases_to_plain()`** (new) — Just output text lines without timestamps
- **`format_timestamp()`** — Reused as-is

#### CLI options (typer):

Kept from gen_lrc_whisper.py:
- `song_id` (argument)
- `--device` (default: `cpu`)
- `--model` (default: `large-v2`)
- `--use-vocals/--no-use-vocals`
- `--output`
- `--offline/--download`
- `--compute-type` (default: `int8`)
- `--start`, `--end`

Changed/added:
- `--align/--no-align` (default: `--align`) — toggle wav2vec2 forced alignment
- `--timestamps/--no-timestamps` (default: `--timestamps`) — LRC vs plain text output
- `--batch-size` (default: `16`) — WhisperX batch size
- Remove `--vad-filter` and `--condition-on-previous` (not applicable to WhisperX API)

#### Main flow:
1. Load config, resolve song, find audio (identical to gen_lrc_whisper.py)
2. Build initial prompt from lyrics (identical)
3. Load audio with `whisperx.load_audio()`
4. Transcribe with `model.transcribe()`
5. If `--align`: run `whisperx.align()` for precise timestamps
6. Extract segments as `(start, end, text)` tuples
7. Output as LRC (with timestamps) or plain text (without)

### 2. Add dependencies via `uv add`

Use `uv add` to add dependencies under a new `transcription_whisperx` extra group:
```bash
uv add --optional transcription_whisperx whisperx "torch>=2.0.0" "pydub>=0.25.0" "typer>=0.12.0"
```

This will automatically update `pyproject.toml` with a new extra section.

## Files to Create/Modify

- **Create:** `poc/gen_lrc_whisperx.py`
- **Modify:** `pyproject.toml` — via `uv add` (adds `transcription_whisperx` extra)

## Verification

```bash
# Run on a cached song (offline, no alignment)
PYTHONPATH=src uv run --extra transcription_whisperx python poc/gen_lrc_whisperx.py <song_id> --no-align

# Run with alignment
PYTHONPATH=src uv run --extra transcription_whisperx python poc/gen_lrc_whisperx.py <song_id> --align

# Plain text output (no timestamps)
PYTHONPATH=src uv run --extra transcription_whisperx python poc/gen_lrc_whisperx.py <song_id> --no-timestamps
```
