# Phase 0 Implementation Plan: Qwen3-ASR Backend POC

## Overview

This document provides the detailed implementation plan for Phase 0 of the LRC Pipeline V2 spec (`qwen3_asr_lrc_enhancement_v2.md`). Phase 0 is a POC to evaluate ONNX vs PyTorch Qwen3-ASR backends for transcription quality before committing to a local ASR strategy.

## Goals

1. Create runnable POC scripts for both ONNX and PyTorch Qwen3-ASR backends
2. Enable side-by-side comparison of transcription quality
3. Provide caching to avoid redundant model inference during testing
4. Generate metrics to inform the Phase 0 decision: PyTorch vs ONNX for primary local backend

## Decision Criteria

Per the v2 spec:
- If PyTorch transcription quality is **comparable** to ONNX, PyTorch wins because it provides per-character timestamps
- ONNX is the fallback for resource-constrained environments (no PyTorch, CPU-only)
- **Expected outcome**: PyTorch Qwen3-ASR as the primary local backend, ONNX as secondary option

## Files to Create

### 1. `poc/gen_lrc_qwen3_asr_onnx.py`

**Purpose**: ONNX ASR backend POC using Daumee/Qwen3-ASR-0.6B-ONNX-CPU

**Key Features**:
- Auto-download ONNX model from HuggingFace Hub on first run
- Returns text-only (no per-character timestamps)
- Simple phrase splitting by punctuation for rough timestamps
- Full `canonical_line_snap()` algorithm for fuzzy matching
- Caching v2 schema per `cache_raw_asr_output_qwen3_local.md`

**Dependencies**:
- `onnxruntime>=1.17.0` (already installed via stem_separation)
- `huggingface-hub>=0.20.0` (for model download)
- `numpy`, `librosa` (for audio processing)
- `transformers` (for tokenizer)
- Reused from existing POCs: `typer`, `rapidfuzz`, `zhconv`, `pypinyin`

**Model Download**:
```python
from huggingface_hub import snapshot_download
model_path = snapshot_download(
    repo_id="Daumee/Qwen3-ASR-0.6B-ONNX-CPU",
    local_dir=os.environ.get("SOW_QWEN3_ASR_ONNX_MODEL_ROOT", "~/.cache/qwen3-asr-onnx")
)
```

**ONNX Pipeline Structure** (adapted from Daumee's `onnx_inference.py`):
```python
class OnnxAsrPipeline:
    def __init__(self, model_dir: Path):
        # Load encoder/decoder ONNX models
        # Initialize tokenizer
        pass
    
    def transcribe(self, audio_path: Path, language: str = "Chinese") -> dict:
        # Process audio
        # Run encoder + decoder
        # Return {"text": str, "language": str}
        pass
```

**CLI Interface**:
```bash
uv run --extra poc_qwen3_asr python poc/gen_lrc_qwen3_asr_onnx.py \
  <song_id_or_audio_path> \
  [--output ./tmp_output/out.txt] \
  [--save-raw ./tmp_output] \
  [--no-lyrics-context] \
  [--force-rerun] \
  [--snap-threshold 0.60]
```

---

### 2. `poc/gen_lrc_qwen3_asr_pytorch.py`

**Purpose**: PyTorch ASR backend POC using `qwen-asr` package

**Key Features**:
- Uses `qwen_asr.Qwen3ASRModel` (same as existing qwen3 forced aligner service)
- Returns per-character timestamps (same as MLX backend)
- Context biasing support (pass canonical lyrics for better transcription)
- Full `canonical_line_snap()` algorithm
- Caching v2 schema

**Dependencies**:
- `qwen-asr>=0.0.6` (PyTorch Qwen3-ASR)
- `torch>=2.8.0,<2.9.0` (already in transcription extra)
- `huggingface-hub>=0.20.0` (for model download)
- Reused: `typer`, `rapidfuzz`, `zhconv`, `pypinyin`

**PyTorch Pipeline Structure**:
```python
class PytorchAsrPipeline:
    def __init__(self, model_name: str = "Qwen/Qwen3-ASR-0.6B", device: str = "auto"):
        # Load Qwen3ASRModel
        pass
    
    def transcribe(
        self, 
        audio_path: Path, 
        language: str = "Chinese",
        context: Optional[str] = None
    ) -> dict:
        # Run transcription with optional context biasing
        # Return {"text": str, "language": str, "segments": [...]}
        pass
```

**CLI Interface**:
```bash
uv run --extra poc_qwen3_asr python poc/gen_lrc_qwen3_asr_pytorch.py \
  <song_id_or_audio_path> \
  [--output ./tmp_output/out.txt] \
  [--save-raw ./tmp_output] \
  [--no-lyrics-context] \
  [--force-rerun] \
  [--snap-threshold 0.60] \
  [--model Qwen/Qwen3-ASR-0.6B]  # or 1.7B
```

---

### 3. `poc/compare_asr_backends.py`

**Purpose**: Side-by-side comparison wrapper for evaluating both backends

**Features**:
- Run both ONNX and PyTorch on same audio file
- Generate comparison report with metrics:
  - Transcription completeness (% of canonical lyrics captured)
  - Character accuracy (% correct chars)
  - Inference speed (wall time)
  - Memory usage (peak RSS)
  - Timestamp availability
- Output: Markdown table + detailed JSON

**CLI Interface**:
```bash
uv run --extra poc_qwen3_asr python poc/compare_asr_backends.py \
  <song_id_or_audio_path> \
  --output ./tmp_output/comparison.md \
  [--save-raw ./tmp_output] \
  [--no-lyrics-context] \
  [--force-rerun]
```

**Output Format** (Markdown):
```markdown
# ASR Backend Comparison Report

## Summary

| Metric | ONNX | PyTorch | MLX (existing) |
|--------|------|---------|-----------------|
| Transcription completeness | 87% | 91% | 92% |
| Character accuracy | 84% | 89% | 90% |
| Timestamp availability | None | Per-character | Per-character |
| Inference speed | 2.8x RT | 2.1x RT | 1.5x RT |
| Memory usage | 2.5GB | 4.2GB | 3.1GB |
| GPU required | No | No | No |

## Detailed Results

### Song: wo_yao_yi_xin_cheng_xie_mi_247

#### ONNX Output
```
[00:12.50] 我要一心稱謝你
[00:15.20] 耶和華啊我要...
```

#### PyTorch Output
```
[00:12.45] 我要一心稱謝你
[00:15.15] 耶和華啊我要...
```

### Coverage Analysis
...
```

---

### 4. `pyproject.toml` Update

Add new optional dependency group `poc_qwen3_asr`:

```toml
# Qwen3-ASR POC dependencies (Phase 0 evaluation)
# Includes both ONNX and PyTorch backends for comparison
poc_qwen3_asr = [
    "qwen-asr>=0.0.6",           # PyTorch Qwen3-ASR
    "huggingface-hub>=0.20.0",    # Model download
    "onnxruntime>=1.17.0",        # ONNX backend
    "transformers>=4.40.0",       # Tokenizer for ONNX
    "torch>=2.8.0,<2.9.0",        # PyTorch
    "torchaudio>=2.8.0,<2.9.0",   # Audio loading
    "numpy>=2.0.2,<2.1.0",
    "librosa>=0.10.0",
    "typer>=0.12.0",
    "rapidfuzz>=3.0.0",
    "zhconv>=1.4.0",
    "pypinyin>=0.52.0",
    "dashscope>=1.14.0",          # For comparison baseline
]
```

---

### 5. `poc/README_qwen3_asr_phase0.md`

Documentation covering:
- Installation instructions
- Running individual POC scripts
- Using the comparison wrapper
- Expected metrics and decision criteria
- Troubleshooting common issues

---

## Implementation Order

1. Update `pyproject.toml` with new dependency group
2. Create `gen_lrc_qwen3_asr_pytorch.py` (easier - similar to existing MLX POC)
3. Create `gen_lrc_qwen3_asr_onnx.py` (requires ONNX adaptation)
4. Create `compare_asr_backends.py`
5. Create `README_qwen3_asr_phase0.md`

## Testing Strategy

### Prerequisites
- Install dependencies: `uv sync --extra poc_qwen3_asr`
- Have test audio files available (or use song IDs from catalog)

### Test Commands

**Test PyTorch backend:**
```bash
uv run --extra poc_qwen3_asr python poc/gen_lrc_qwen3_asr_pytorch.py \
  wo_yao_yi_xin_cheng_xie_mi_247 \
  --save-raw ./tmp_output \
  -o ./tmp_output/pytorch_out.txt
```

**Test ONNX backend:**
```bash
uv run --extra poc_qwen3_asr python poc/gen_lrc_qwen3_asr_onnx.py \
  wo_yao_yi_xin_cheng_xie_mi_247 \
  --save-raw ./tmp_output \
  -o ./tmp_output/onnx_out.txt
```

**Run comparison:**
```bash
uv run --extra poc_qwen3_asr python poc/compare_asr_backends.py \
  wo_yao_yi_xin_cheng_xie_mi_247 \
  --output ./tmp_output/comparison.md \
  --save-raw ./tmp_output
```

## Expected Outputs

After running comparison on 10-20 songs, expect:

1. **Cache files** in `~/.cache/qwen3_asr/`:
   - `onnx_{song_id}_{params_hash}.json`
   - `pytorch_{song_id}_{params_hash}.json`

2. **Raw ASR outputs** in `--save-raw` directory:
   - `asr_raw_onnx.json`
   - `asr_raw_pytorch.json`

3. **Comparison report** with decision recommendation

## Success Criteria

Phase 0 is successful when:
- Both POC scripts run without errors on test songs
- Comparison wrapper generates meaningful metrics
- Decision can be made on primary local ASR backend (expected: PyTorch)
- Cache v2 schema is validated working

## Notes

- ONNX model is text-only per spec - no timestamp estimation needed
- PyTorch model provides per-character timestamps (same as existing MLX POC)
- Both backends support the same `canonical_line_snap()` algorithm
- Cache schema follows v2 pattern from `cache_raw_asr_output_qwen3_local.md`

---

## References

- Main spec: `specs/qwen3_asr_lrc_enhancement_v2.md` (Phase 0 section)
- Cache schema: `specs/cache_raw_asr_output_qwen3_local.md`
- Existing POC: `poc/gen_lrc_qwen3_asr_local.py` (MLX backend reference)
- v1 spec (deprecated): `specs/qwen3_asr_onnx_lrc_enhancement.md`
