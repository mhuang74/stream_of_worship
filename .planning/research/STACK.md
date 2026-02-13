# Stack Research

**Domain:** Qwen3-ForcedAligner-0.6B integration for LRC timestamp refinement
**Researched:** 2026-02-13
**Confidence:** HIGH

## Recommended Stack

### Core Technologies

| Technology | Version | Purpose | Why Recommended |
|------------|---------|---------|-----------------|
| **Python** | 3.11 (existing) | Runtime language | Project already uses Python 3.11; qwen-asr supports >=3.9. Python 3.12 is recommended by Qwen but 3.11 works fine. |
| **PyTorch** | 2.8.x (>=2.8.0, <2.9.0) | Deep learning framework | Existing constraint from torchaudio 2.8.x compatibility (AudioMetaData removed in 2.9.0). Qwen3-ASR requires PyTorch 2.2+, so 2.8.x satisfies both. |
| **qwen-asr** | latest (via pip) | Qwen3 audio models package | Official package providing `Qwen3ForcedAligner` with transformers/vLLM backends. |
| **transformers** | ==4.57.6 (via qwen-asr) | Hugging Face transformers | Exact version constraint from qwen-asr dependencies. |

### Qwen3 Dependencies (via qwen-asr package)

| Library | Version | Purpose | Notes |
|---------|---------|---------|-------|
| transformers | ==4.57.6 | Hugging Face models | Qwen3 code baked into this version |
| accelerate | ==1.12.0 | Mixed precision training/inference | Device acceleration utilities |
| nagisa | ==0.2.11 | Japanese morphological analyzer | For Japanese language processing |
| soynlp | ==0.0.493 | Korean NLP utilities | For Korean language processing |
| librosa | latest | Audio loading/processing | Core audio library |
| soundfile | latest | Audio I/O | Required for audio loading |
| sox | latest | Audio utilities | Audio format handling |
| gradio | latest | Optional demo UI | For web interface (optional) |
| flask | latest | Optional web server | For demo (optional) |

### Optional Performance Enhancements

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| **flash-attn** | latest | Flash Attention 2 | For Ampere/Ada/Hopper GPUs (A100, 4090, H100). Recommended for faster inference. Requires CUDA 12.0+. |
| **vllm** | 0.14.0 | High-throughput inference | See "Critical Conflicts" below - incompatible with existing torch<2.9.0 constraint |

### Supporting Libraries (project existing)

| Library | Version | Purpose |
|---------|---------|---------|
| numpy | >=2.0.2,<2.1.0 | Numerical operations (existing constraint) |
| pydub | >=0.25.0 | Audio format handling (in transcription_qwen3) |
| typer | >=0.12.0 | CLI framework |
| boto3 | >=1.34.0 | AWS S3/R2 client (for model caching if needed) |

## Docker Setup

### Recommended Base Image Options

| Option | Base Image | Use Case | Notes |
|--------|------------|----------|-------|
| **CPU-only** | `python:3.11-slim` | Development/testing | Current project base. No GPU capabilities. |
| **CUDA-ready** | `nvidia/cuda:12.6-cudnn-runtime-py3.11` | GPU inference | Recommended for production with GPU. CUDA 12.6 required for flash-attn. |
| **Pre-built** | `qwenllm/qwen3-asr:latest` | Full Qwen3-ASR stack | Pre-configured Qwen3 environment from Docker Hub. |

### GPU Dockerfile Setup

```dockerfile
# For GPU-enabled inference
FROM nvidia/cuda:12.6-cudnn-runtime-py3.11

# Install system dependencies
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libsndfile1 \
    gcc \
    g++ \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Install PyTorch 2.8.x with CUDA 12.6 support
RUN uv pip install --no-cache \
    --index-url https://download.pytorch.org/whl/cu126 \
    torch==2.8.0 \
    torchaudio==2.8.0 \
    torchvision==0.23.0

# Install dependencies
RUN uv pip install --no-cache qwen-asr

# Optional: FlashAttention 2 (requires compilation)
# RUN uv pip install --no-cache --no-build-isolation flash-attn
```

### Docker Compose for GPU

```yaml
services:
  analysis-qwen3:
    build:
      dockerfile: Dockerfile.gpu
      context: ./services/analysis
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
    environment:
      - CUDA_VISIBLE_DEVICES=0
      - HF_HOME=/cache/huggingface
    volumes:
      - analysis-cache:/cache
```

## Installation

### Local Installation (uv)

```bash
# Using existing pyproject.toml transcription_qwen3 extra
uv run --extra transcription_qwen3 python -c "from qwen_asr import Qwen3ForcedAligner; print('OK')"
```

### Full Dependencies (transcription_qwen3)

```bash
# Already defined in pyproject.toml:
# transcription_qwen3 = [
#     "qwen-asr",
#     "torch>=2.8.0,<2.9.0",
#     "numpy>=2.0.2,<2.1.0",
#     "pydub>=0.25.0",
#     "typer>=0.12.0",
# ]
```

### Flash Attention 2 (GPU acceleration)

```bash
# Standard (takes ~15-30 min to compile)
pip install -U flash-attn --no-build-isolation

# Low RAM machines (<96GB RAM)
MAX_JOBS=4 pip install -U flash-attn --no-build-isolation
```

## Critical Conflicts

### PyTorch Version Conflict: vLLM 0.14.0

| Dependency | Required PyTorch | Project Constraint | Verdict |
|------------|-----------------|-------------------|---------|
| vLLM 0.14.0 | torch==2.9.1 | torch>=2.8.0,<2.9.0 | **INCOMPATIBLE** |
| torch<2.9.0 | torchaudio AudioMetaData | torch>=2.9.0 | **BREAKS pyannote.audio** |

**Analysis:** The vLLM backend option for qwen-asr is NOT compatible with the existing project constraint `torch<2.9.0`. This constraint exists because torchaudio 2.9.0+ removed `AudioMetaData` which `pyannote.audio` (used by WhisperX) requires.

**Recommendation:** Use the **transformers backend** (default) instead of vLLM. The transformers backend with `dtype=torch.bfloat16` provides reasonable performance without the PyTorch version conflict.

**Alternatives:**
1. **Use transformers backend only** - Recommended. No vLLM, simpler dependencies.
2. **Separate vLLM container** - Run vLLM as a separate microservice with torch==2.9.1, communicate via HTTP. Useful if production throughput is critical.
3. **Drop WhisperX/pyannote.audio** - Upgrade to torchaudio 2.9+ and vLLM, but lose WhisperX capabilities.

## Python Version Compatibility

| Python | qwen-asr | Project | Verdict |
|--------|----------|---------|---------|
| 3.11 | Supported (>=3.9) | Current | OK |
| 3.12 | Recommended | Not used | Consider upgrading |

**Note:** Qwen3-ASR documentation recommends Python 3.12, but the package officially supports >=3.9. The project's Python 3.11 constraint is compatible with qwen-asr.

## Device Support

| Device | Support | Precision | Notes |
|--------|---------|-----------|-------|
| **CUDA** | Full | bfloat16, float16, float32 | Recommended. Requires NVIDIA GPU with CUDA 12.0+ for flash-attn. |
| **CPU** | Supported | float32 | Slow (~10-100x slower). Use only for testing. |
| **MPS (Apple Silicon)** | Basic | float16, float32 | Experimental. No flash-attn support. |
| **ROCm (AMD GPU)** | Via PyTorch | Dependent | Not tested with qwen-asr. |

## Model Specifications

| Attribute | Value | Notes |
|-----------|-------|-------|
| Model name | `Qwen/Qwen3-ForcedAligner-0.6B` | 0.9B parameters |
| Max audio duration | 5 minutes | Model limitation |
| Precision | bfloat16 (recommended) | Requires Ampere+ GPU for BF16, otherwise use float16 |
| Languages | 11 | Chinese, English, Cantonese, French, German, Italian, Japanese, Korean, Portuguese, Russian, Spanish |
| Model type | Non-Autoregressive (NAR) | Faster inference than AR models |

## Alternatives Considered

| Recommended | Alternative | Why Not |
|-------------|-------------|---------|
| **qwen-asr** | whisperx+pyannote.audio | Existing stack, but forced alignment needs pre-existing lyrics and provides more precise word/character level alignment |
| **qwen-asr** | gentle | Gentle is older, less accurate for Chinese, qwen3 benchmarks show 42.9ms error vs 129.8-161.1ms for alternatives |
| **bfloat16 precision** | float16 | BF16 has better dynamic range than FP16, less overflow/underflow. Requires Ampere+ GPU. |

## What NOT to Use

| Avoid | Why | Use Instead |
|-------|-----|-------------|
| **vLLM 0.14.0** | Requires torch==2.9.1, conflicts with project's torch<2.9.0 constraint needed for pyannote.audio | transformers backend (default in qwen-asr) |
| **float32 precision** | Unnecessary memory overhead for this 0.9B model | bfloat16 or float16 (if BF16 unavailable) |
| **CUDA 11.x** | flash-attn requires CUDA 12.0+, Qwen3 recommends newer CUDA | CUDA 12.6 or higher |
| **Separate vLLM container** (for now) | Adds complexity. Only needed if production throughput bottlenecks | Start with transformers backend |
| **Audio > 5 minutes** | Model limitation, will fail | Segment audio into <5 minute chunks, or use different approach |

## Stack Patterns by Variant

**If using GPU (recommended):**
- Use CUDA-enabled base image: `nvidia/cuda:12.6-cudnn-runtime-py3.11`
- Install flash-attn for 2-3x speedup
- Use bfloat16 precision
- Device: `cuda:0`

**If using Apple Silicon (MPS):**
- Use standard CPU build `from_pretrained` with `device_map="mps"`
- No flash-attn support
- Use float16 precision
- Device: `mps`

**If using CPU only:**
- Use float32 precision (no bfloat16 support in pytorch-cpu)
- Performance will be significantly slower
- Device: `cpu`

**If memory constrained (<16GB VRAM):**
- Use float16 instead of bfloat16
- Reduce batch size
- Consider CPU-only for testing

## Version Compatibility

| Package | Compatible With | Notes |
|-----------|-----------------|-------|
| qwen-asr (latest) | torch>=2.2.0 | Tested with torch 2.8.x |
| flash-attn (latest) | torch>=2.2.0, CUDA>=12.0 | Requires GPU compilation |
| transformers==4.57.6 | torch 2.8.x | Bundled with qwen-asr |
| torch 2.8.x | torchaudio 2.8.x | AudioMetaData present (removed in 2.9.0) |
| **vLLM 0.14.0** | **torch==2.9.1** | **CONFLICT: torch<2.9.0 required by project** |

## Integration Points

### Existing Stack Integration

| Existing Component | Integration Point | Notes |
|-------------------|-------------------|-------|
| `transcription_qwen3` extra | pyproject.toml | Already defined, ready to use |
| `/poc/gen_lrc_qwen3.py` | CLI script | Working POC, can be moved to src/ |
| `sow_analysis` service | Analysis Service | Add LRC refinement endpoint |
| R2/asset cache | Model/audio caching | Reuse existing cache infrastructure |

### Pipeline Integration

```
Current flow:
Whisper transcribe -> LLM aligns to lyrics -> [GAP: timestamps inaccurate]

Proposed flow:
Whisper transcribe -> LLM aligns to lyrics -> Qwen3ForcedAligner refines timestamps -> LRC output
```

### API Endpoints (suggested)

```
POST /api/v1/lrc/refine
- Input: audio_url + lyrics_text + language
- Output: LRC with refined timestamps
- Uses: Qwen3ForcedAligner directly

POST /api/v1/transcribe/qwen3
- Input: audio_url + (optional) lyrics_text
- Output: transcription + (if lyrics provided) aligned LRC
- Uses: Qwen3-ASR with ForcedAligner
```

## Constraints & Limitations

| Constraint | Limitation | Impact |
|------------|------------|--------|
| **5 minute max audio** | Model hard limit | Songs >5 min need segmentation |
| **11 languages** | Limited language support | Chinese worship songs fine, others need verification |
| **Requires pre-existing lyrics** | Forced alignment, not ASR | Doesn't solve transcription-only case |
| **GPU requirement** | CPU inference very slow | Production deployment needs GPU |
| **torch<2.9.0** | torchaudio AudioMetaData conflict | No vLLM backend available |

## Sources

- **Hugging Face**: Model page for [Qwen/Qwen3-ForcedAligner-0.6B](https://huggingface.co/Qwen/Qwen3-ForcedAligner-0.6B) — HIGH
- **GitHub**: [QwenLM/Qwen3-ASR](https://github.com/QwenLM/Qwen3-ASR) — HIGH
- **PyPI**: [qwen-asr package metadata](https://pypi.org/pypi/qwen-asr/json) — HIGH
- **PyPI**: [flash-attn package metadata](https://pypi.org/pypi/flash-attn/json) — HIGH (CUDA 12.0+, PyTorch 2.2+)
- **PyPI**: [vllm 0.14.0 package metadata](https://pypi.org/pypi/vllm/json) — HIGH (torch==2.9.1 requirement)
- **PyTorch**: [Official CUDA compatibility](https://pytorch.org/get-started/locally/) — HIGH (CUDA 12.6+ for stable builds)
- **Docker Hub**: [qwenllm/qwen3-asr](https://hub.docker.com/r/qwenllm/qwen3-asr/tags) — MEDIUM (pre-built images available)

---
*Stack research for: Qwen3-ForcedAligner-0.6B LRC timestamp refinement*
*Researched: 2026-02-13*
