# Plan: Improve LRC Generation with Whisper + Qwen3 Forced Alignment

## Overview

Replace the current two-stage pipeline (Whisper → LLM alignment) with a more accurate three-stage pipeline:

**Current:** `Whisper transcription → LLM alignment → LRC output`

**Improved:** `Whisper transcription (with VAD + lyrics prompt) → Qwen3 forced alignment → LRC output`

The Qwen3 forced aligner provides character-level alignment accuracy that surpasses LLM-based alignment, which often struggles with repeated sections and timing precision.

## Key Improvements from POC Scripts

### From `gen_lrc_whisper.py`
1. **Enable VAD filter** - Better segmentation of vocal passages
2. **Initial prompt with scraped lyrics** - Guide Whisper with actual lyrics as context for more accurate transcription

### From `gen_lrc_qwen3.py`
1. **Qwen3 ForcedAligner refinement** - Replace LLM alignment with precise forced alignment
2. **Character-level timestamp mapping** - Map aligner output back to original lyric lines

## Architecture Changes

### New Pipeline Flow

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         LRC Generation Pipeline                         │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  ┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐     │
│  │  Stage 1:       │    │  Stage 2:       │    │  Stage 3:       │     │
│  │  Whisper        │───→│  Qwen3 Forced   │───→│  LRC Output     │     │
│  │  Transcription  │    │  Alignment      │    │  Generation     │     │
│  │  (with VAD)     │    │  (refinement)   │    │                 │     │
│  └─────────────────┘    └─────────────────┘    └─────────────────┘     │
│           ↑                      ↑                                      │
│           │                      │                                      │
│     ┌─────┴─────┐          ┌─────┴─────┐                                │
│     │ Lyrics    │          │ Original  │                                │
│     │ as prompt │          │ lyrics    │                                │
│     └───────────┘          └───────────┘                                │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

### Modified Components

```
services/analysis/src/sow_analysis/workers/lrc.py
├── Configuration updates (LrcOptions)
├── Stage 1: Enhanced Whisper transcription
│   ├── Add VAD filter (currently disabled)
│   ├── Build initial prompt from lyrics
│   └── Return phrases with timestamps
├── Stage 2: Qwen3 Forced Alignment (NEW)
│   ├── Load Qwen3ForcedAligner model
│   ├── Validate audio length (< 5 min)
│   ├── Run alignment with Whisper output as text
│   └── Map character segments to lyric lines
└── Stage 3: LRC output (unchanged)
```

## Detailed Implementation Plan

### Phase 1: Update Configuration (`models.py`)

Add new configuration options to `LrcOptions`:

```python
@dataclass
class LrcOptions:
    # Existing options
    whisper_model: str = "large-v3"
    language: str = "zh"
    llm_model: Optional[str] = None  # Deprecated but keep for backward compat

    # NEW: Whisper enhancement options
    vad_filter: bool = True  # Enable VAD for better segmentation
    condition_on_previous: bool = True  # Maintain transcription consistency

    # NEW: Qwen3 alignment options
    use_qwen3_alignment: bool = True  # Enable Qwen3 refinement stage
    qwen3_model: str = "Qwen/Qwen3-ForcedAligner-0.6B"
    qwen3_device: str = "auto"  # auto/mps/cuda/cpu
    qwen3_dtype: str = "float32"  # bfloat16/float16/float32
    qwen3_max_duration: int = 300  # 5 minute limit in seconds
```

### Phase 2: Enhanced Whisper Transcription

Update `_run_whisper_transcription()` to:

1. **Build initial prompt from lyrics** (similar to `gen_lrc_whisper.py:267-277`):
   ```python
   # Join first 50 lines of lyrics, limit to ~2000 chars
   lyrics_preview = "\n".join(lyrics_text.split("\n")[:50])
   if len(lyrics_preview) > 2000:
       lyrics_preview = lyrics_preview[:2000]
   initial_prompt = f"这是一首中文敬拜诗歌。歌词如下：\n{lyrics_preview}"
   ```

2. **Enable VAD filter** (currently hardcoded to `False` in line 134):
   ```python
   vad_filter=options.vad_filter,  # Use config instead of hardcoded False
   ```

3. **Add condition_on_previous_text**:
   ```python
   condition_on_previous_text=options.condition_on_previous,
   ```

### Phase 3: Add Qwen3 Forced Alignment Stage (NEW)

Create new function `_run_qwen3_alignment()`:

```python
async def _run_qwen3_alignment(
    audio_path: Path,
    lyrics_text: str,
    whisper_phrases: List[WhisperPhrase],
    options: LrcOptions,
) -> List[WhisperPhrase]:
    """Refine Whisper timestamps using Qwen3 ForcedAligner.

    Uses Whisper transcription as the text input to Qwen3, which performs
    precise character-level alignment against the audio.
    """
```

**Implementation steps:**

1. **Validate audio duration** (< 5 minutes):
   ```python
   from pydub import AudioSegment
   audio_duration = len(AudioSegment.from_file(str(audio_path))) / 1000.0
   if audio_duration > options.qwen3_max_duration:
       logger.warning(f"Audio {audio_duration}s exceeds Qwen3 limit, skipping alignment")
       return whisper_phrases  # Fall back to Whisper timestamps
   ```

2. **Load Qwen3ForcedAligner** (adapted from `gen_lrc_qwen3.py:271-275`):
   ```python
   from qwen_asr import Qwen3ForcedAligner
   import torch

   # Determine device
   device = options.qwen3_device
   if device == "auto":
       device = "mps" if torch.backends.mps.is_available() else \
                "cuda" if torch.cuda.is_available() else "cpu"

   dtype_map = {
       "bfloat16": torch.bfloat16,
       "float16": torch.float16,
       "float32": torch.float32,
   }
   torch_dtype = dtype_map.get(options.qwen3_dtype, torch.float32)

   model = Qwen3ForcedAligner.from_pretrained(
       options.qwen3_model,
       dtype=torch_dtype,
       device_map=device,
   )
   ```

3. **Run alignment** with Whisper output as text:
   ```python
   # Use Whisper transcription as the text to align
   # This gives us the precise timing for the actual sung content
   whisper_text = " ".join(p.text for p in whisper_phrases)

   results = model.align(
       audio=str(audio_path),
       text=whisper_text,
       language="Chinese",
   )
   ```

4. **Map character segments to original lyrics**:
   - Adapt the `map_segments_to_lines()` function from `gen_lrc_qwen3.py:90-188`
   - Map Qwen3's character-level output back to original lyric lines
   - Use original lyrics as the gold standard text (not Whisper transcription)

### Phase 4: Update Main `generate_lrc()` Function

Modify the pipeline in `generate_lrc()`:

```python
async def generate_lrc(...):
    # Step 1: Whisper transcription (enhanced with VAD + lyrics prompt)
    whisper_phrases = await _run_whisper_transcription(...)

    # Step 2: Qwen3 refinement (NEW - replaces LLM alignment)
    if options.use_qwen3_alignment:
        try:
            aligned_phrases = await _run_qwen3_alignment(
                audio_path, lyrics_text, whisper_phrases, options
            )
        except Exception as e:
            logger.warning(f"Qwen3 alignment failed: {e}, using Whisper timestamps")
            aligned_phrases = whisper_phrases
    else:
        aligned_phrases = whisper_phrases

    # Step 3: Convert to LRC lines (adapt from WhisperPhrase to LRCLine)
    lrc_lines = _phrases_to_lrc_lines(aligned_phrases)

    # Step 4: Write LRC file
    _write_lrc(lrc_lines, output_path)
```

### Phase 5: Remove/Deprecate LLM Alignment

The LLM-based alignment (`_llm_align()`, `_build_alignment_prompt()`, etc.) should be:
- **Option A**: Remove entirely (cleaner, but breaking change)
- **Option B**: Keep as fallback when Qwen3 fails or for long audio (> 5 min)

**Recommendation**: Option B - keep LLM alignment as a fallback mechanism with a feature flag.

## Dependencies

Add to `services/analysis/pyproject.toml`:

```toml
[project.optional-dependencies]
qwen3 = [
    "qwen-asr>=0.1.0",
    "torch>=2.0.0",
]
```

## Configuration Updates

Update environment/config handling in `services/analysis/src/sow_analysis/config.py`:

```python
# Add new settings
SOW_QWEN3_DEVICE: str = "auto"
SOW_QWEN3_DTYPE: str = "float32"
SOW_QWEN3_CACHE_DIR: Optional[Path] = None
```

## Error Handling Strategy

| Scenario | Handling |
|----------|----------|
| Audio > 5 minutes | Skip Qwen3, use Whisper timestamps + warning |
| Qwen3 model load fails | Fall back to Whisper timestamps + warning |
| Qwen3 alignment fails | Fall back to Whisper timestamps + warning |
| Whisper returns no phrases | Raise WhisperTranscriptionError |
| Lyrics missing | Use default prompt only (no lyrics injection) |

## Testing Strategy

1. **Unit tests** for `map_segments_to_lines()` with various lyric structures
2. **Integration tests** comparing:
   - Old pipeline (Whisper + LLM)
   - New pipeline (Whisper + Qwen3)
   - Evaluation metric: timestamp accuracy vs manual alignment
3. **Edge case tests**:
   - Audio exactly at 5-minute boundary
   - Songs with many repeated sections
   - Songs with instrumental sections

## Migration Path

1. **Phase 1**: Implement Qwen3 alignment alongside existing LLM alignment
2. **Phase 2**: A/B test on sample songs to validate accuracy improvement
3. **Phase 3**: Make Qwen3 the default, keep LLM as fallback
4. **Phase 4**: Remove LLM alignment code once Qwen3 proves stable

## Open Questions for User

1. **Qwen3 5-minute limit**: Should we split longer songs into segments for alignment, or fall back to LLM/Whisper for long songs?

2. **Fallback strategy**: When Qwen3 fails, should we:
   - A) Use raw Whisper timestamps (no alignment)
   - B) Fall back to LLM alignment (existing behavior)

3. **Model caching**: Qwen3 model is ~0.6B parameters (~1.2GB). Should we cache loaded models between requests for the analysis service?

4. **Device preference**: The POC defaults to auto-detect MPS on Mac. Should the analysis service follow this or have a configurable default?
