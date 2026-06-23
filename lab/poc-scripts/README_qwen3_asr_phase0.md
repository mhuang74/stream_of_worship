# Qwen3-ASR Phase 0 POC Documentation

This directory contains Phase 0 POC scripts for evaluating Qwen3-ASR backends (ONNX vs PyTorch) for the LRC Pipeline V2.

## Overview

Phase 0 is a proof-of-concept evaluation to compare ONNX and PyTorch Qwen3-ASR backends for transcription quality before committing to a local ASR strategy.

**Decision Criteria** (per v2 spec):
- If PyTorch transcription quality is **comparable** to ONNX, PyTorch wins because it provides per-character timestamps
- ONNX is the fallback for resource-constrained environments (no PyTorch, CPU-only)
- **Expected outcome**: PyTorch Qwen3-ASR as the primary local backend, ONNX as secondary option

## Files

| File | Purpose |
|------|---------|
| `gen_lrc_qwen3_asr_pytorch.py` | PyTorch backend POC with per-character timestamps |
| `gen_lrc_qwen3_asr_onnx.py` | ONNX backend POC (text-only output) |
| `compare_asr_backends.py` | Side-by-side comparison wrapper |
| `README_qwen3_asr_phase0.md` | This documentation |

## Installation

Install the required dependencies:

```bash
uv sync --extra poc_qwen3_asr
```

This installs:
- `qwen-asr>=0.0.6` - PyTorch Qwen3-ASR package
- `onnxruntime>=1.17.0` - ONNX backend runtime
- `huggingface-hub>=0.20.0` - Model download from HuggingFace
- `transformers>=4.40.0` - Tokenizer for ONNX
- `torch>=2.8.0,<2.9.0` - PyTorch
- `torchaudio>=2.8.0,<2.9.0` - Audio loading
- Plus supporting libraries: numpy, librosa, typer, rapidfuzz, zhconv, pypinyin, dashscope

## Quick Start

### Run PyTorch Backend

```bash
uv run --extra poc_qwen3_asr python gen_lrc_qwen3_asr_pytorch.py \
  <song_id_or_audio_path> \
  --save-raw ./tmp_output \
  -o ./tmp_output/pytorch_out.txt
```

Options:
- `--model Qwen/Qwen3-ASR-0.6B` (default) or `Qwen/Qwen3-ASR-1.7B`
- `--snap-threshold 0.60` - Fuzzy matching threshold (0-1)
- `--no-lyrics-context` - Disable context biasing with catalog lyrics
- `--force-rerun` - Ignore cache and rerun transcription
- `--start 0` / `--end 60` - Transcribe a specific segment

### Run ONNX Backend

```bash
uv run --extra poc_qwen3_asr python gen_lrc_qwen3_asr_onnx.py \
  <song_id_or_audio_path> \
  --save-raw ./tmp_output \
  -o ./tmp_output/onnx_out.txt
```

Options:
- `--model-cache-dir ~/.cache/qwen3-asr-onnx` - Cache ONNX model download
- `--snap-threshold 0.60` - Fuzzy matching threshold
- `--no-lyrics-context` - Disable context biasing (ONNX doesn't support this well)
- `--force-rerun` - Ignore cache and rerun transcription

**Note**: The ONNX model is auto-downloaded from HuggingFace Hub on first run (`Daumee/Qwen3-ASR-0.6B-ONNX-CPU`).

### Run Comparison

```bash
uv run --extra poc_qwen3_asr python compare_asr_backends.py \
  <song_id_or_audio_path> \
  --output ./tmp_output/comparison.md \
  --save-raw ./tmp_output
```

This runs both backends and generates a comparison report with:
- Transcription completeness (% of canonical lyrics captured)
- Character accuracy (% correct chars)
- Inference speed (wall time)
- Memory usage
- Timestamp availability
- Decision recommendation

## Backend Comparison

| Feature | PyTorch | ONNX |
|---------|---------|------|
| **Model** | Qwen/Qwen3-ASR-0.6B or 1.7B | Daumee/Qwen3-ASR-0.6B-ONNX-CPU |
| **Timestamps** | Per-character | Estimated (text-only) |
| **Context biasing** | Yes (pass canonical lyrics) | Limited |
| **GPU support** | CUDA, MPS (Apple Silicon), CPU | CPU only |
| **Memory** | ~4GB (1.7B) | ~2.5GB |
| **Speed** | ~2.1x RT (0.6B on MPS) | ~2.8x RT |
| **Installation** | Requires PyTorch | Only ONNX Runtime |

## Caching

All scripts use Cache v2 schema per `specs/cache_raw_asr_output_qwen3_local.md`:

- Cache location: `~/.cache/qwen3_asr/`
- Cache filename: `{backend}_{song_id}_{model}_{params_hash8}.json`
- Cache includes: raw ASR output, model info, parameters, wall time

Cached results are reused by default. Use `--force-rerun` to ignore cache.

## Expected Outputs

When running with `--save-raw ./tmp_output`:

**PyTorch backend:**
- `./tmp_output/asr_raw_pytorch.json` - Raw ASR output with per-character timestamps
- `./tmp_output/diagnostic_pytorch.md` - Detailed diagnostic report
- `./tmp_output/pytorch_out.txt` - Final LRC output

**ONNX backend:**
- `./tmp_output/asr_raw_onnx.json` - Raw ASR output (text-only with estimated timestamps)
- `./tmp_output/diagnostic_onnx.md` - Detailed diagnostic report
- `./tmp_output/onnx_out.txt` - Final LRC output

**Comparison:**
- `./tmp_output/comparison.md` - Side-by-side comparison report

## Testing Strategy

### Prerequisites
- Install dependencies: `uv sync --extra poc_qwen3_asr`
- Have test audio files available or use song IDs from your catalog

### Test Commands

**Test PyTorch backend:**
```bash
uv run --extra poc_qwen3_asr python gen_lrc_qwen3_asr_pytorch.py \
  wo_yao_yi_xin_cheng_xie_mi_247 \
  --save-raw ./tmp_output \
  -o ./tmp_output/pytorch_out.txt
```

**Test ONNX backend:**
```bash
uv run --extra poc_qwen3_asr python gen_lrc_qwen3_asr_onnx.py \
  wo_yao_yi_xin_cheng_xie_mi_247 \
  --save-raw ./tmp_output \
  -o ./tmp_output/onnx_out.txt
```

**Run comparison:**
```bash
uv run --extra poc_qwen3_asr python compare_asr_backends.py \
  wo_yao_yi_xin_cheng_xie_mi_247 \
  --output ./tmp_output/comparison.md \
  --save-raw ./tmp_output
```

## Success Criteria

Phase 0 is successful when:
1. Both POC scripts run without errors on test songs
2. Comparison wrapper generates meaningful metrics
3. Decision can be made on primary local ASR backend (expected: PyTorch)
4. Cache v2 schema is validated working

## Troubleshooting

### ONNX Model Download Fails

If the ONNX model download fails:
```bash
# Set custom cache directory
export SOW_QWEN3_ASR_ONNX_MODEL_ROOT="/path/to/model"

# Or download manually
huggingface-cli download Daumee/Qwen3-ASR-0.6B-ONNX-CPU --local-dir /path/to/model
```

### PyTorch CUDA Out of Memory

For PyTorch backend on CUDA:
```bash
# Use smaller model
--model Qwen/Qwen3-ASR-0.6B

# Or use CPU
CUDA_VISIBLE_DEVICES="" python gen_lrc_qwen3_asr_pytorch.py ...
```

### Cache Issues

Clear cache if needed:
```bash
rm -rf ~/.cache/qwen3_asr/
```

## References

- Main spec: `specs/qwen3_asr_lrc_enhancement_v2.md`
- Cache schema: `specs/cache_raw_asr_output_qwen3_local.md`
- Existing POC: `poc/gen_lrc_qwen3_asr_local.py` (MLX backend reference)
